function categorizeFileForRequest(fileType) {
    const normalized = String(fileType || '').toLowerCase();
    if (normalized === 'image/svg+xml') {
        return 'document';
    }
    if (normalized.startsWith('image/')) {
        return 'image';
    }
    if (normalized.startsWith('audio/')) {
        return 'audio';
    }
    if (normalized.startsWith('video/')) {
        return 'video';
    }
    return 'document';
}

function normalizeCategoryValue(category) {
    const normalized = String(category || '').trim().toLowerCase();
    if (normalized === 'documents' || normalized === 'doc') {
        return 'document';
    }
    if (normalized === 'unknown' || normalized.length === 0) {
        return 'document';
    }
    if (normalized === 'image' || normalized === 'audio' || normalized === 'video' || normalized === 'document') {
        return normalized;
    }
    return 'document';
}

function normalizeAttachmentCategory(category, fileType) {
    const normalized = normalizeCategoryValue(category);
    if (normalized === 'document' && category && String(category).trim().length > 0) {
        return normalized;
    }
    if (normalized === 'document' && (!category || normalizeCategoryValue(category) === 'document')) {
        return normalized;
    }
    if (normalized === 'document' || normalized === 'image' || normalized === 'audio' || normalized === 'video') {
        return normalized;
    }
    return categorizeFileForRequest(fileType);
}

function addRequestFileId(category, fileId) {
    if (!fileId) return;
    const normalizedCategory = normalizeCategoryValue(category);
    switch (normalizedCategory) {
        case 'image':
            if (!attachmentState.requestImageIds.includes(fileId)) {
                attachmentState.requestImageIds.push(fileId);
            }
            break;
        case 'audio':
            if (!attachmentState.requestAudioIds.includes(fileId)) {
                attachmentState.requestAudioIds.push(fileId);
            }
            break;
        case 'video':
            if (!attachmentState.requestVideoIds.includes(fileId)) {
                attachmentState.requestVideoIds.push(fileId);
            }
            break;
        case 'document':
        default:
            if (!attachmentState.requestDocumentIds.includes(fileId)) {
                attachmentState.requestDocumentIds.push(fileId);
            }
            break;
    };
}

function removeRequestFileId(fileId) {
    if (!fileId) return;
    attachmentState.requestImageIds = attachmentState.requestImageIds.filter(id => id !== fileId);
    attachmentState.requestAudioIds = attachmentState.requestAudioIds.filter(id => id !== fileId);
    attachmentState.requestVideoIds = attachmentState.requestVideoIds.filter(id => id !== fileId);
    attachmentState.requestDocumentIds = attachmentState.requestDocumentIds.filter(id => id !== fileId);
}

async function uploadChatAttachment(file, { onProgress, signal } = {}) {
    if (typeof authedFetch !== 'function') {
        throw new Error(getChatPreviewTranslation('chat_attachment_authentication_required', 'Authentication required'));
    }

    const formData = new FormData();
    formData.append('file', file);

    try {
        const modelId = typeof window !== 'undefined' && typeof window.getSelectedModelId === 'function'
            ? window.getSelectedModelId()
            : null;
        const isByokModel = Boolean(
            modelId
            && typeof window !== 'undefined'
            && typeof window.BYOK?.isByokModelId === 'function'
            && window.BYOK.isByokModelId(modelId)
        );
        if (modelId && !isByokModel) {
            formData.append('model_id', String(modelId));
        }
    } catch (_) {
        // no-op
    }

    const adapter = (input, init) => {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            const method = init?.method || 'GET';
            const url = typeof input === 'string'
                ? input
                : (input && typeof input === 'object' && 'url' in input
                    ? input.url
                    : String(input || ''));

            const createAbortError = () => {
                const error = new Error(getChatPreviewTranslation('chat_attachment_upload_cancelled', 'Upload cancelled'));
                error.name = 'AbortError';
                return error;
            };

            const handleAbort = () => {
                try { xhr.abort(); } catch (_) {}
            };

            const cleanup = () => {
                if (signal) {
                    signal.removeEventListener('abort', handleAbort);
                }
            };

            if (signal) {
                if (signal.aborted) {
                    cleanup();
                    reject(createAbortError());
                    return;
                }
                signal.addEventListener('abort', handleAbort);
            }

            xhr.upload.addEventListener('progress', (event) => {
                if (event.lengthComputable && typeof onProgress === 'function') {
                    const percent = (event.loaded / event.total) * 100;
                    onProgress(percent);
                }
            });

            xhr.addEventListener('load', () => {
                cleanup();
                const rawHeaders = xhr.getAllResponseHeaders() || '';
                let responseHeaders;
                if (typeof Headers !== 'undefined') {
                    const headers = new Headers();
                    rawHeaders.split(/[\r\n]+/).forEach((line) => {
                        if (!line) return;
                        const parts = line.split(': ');
                        const header = parts.shift();
                        const value = parts.join(': ');
                        if (header) {
                            headers.append(header, value);
                        }
                    });
                    responseHeaders = headers;
                } else {
                    const headerMap = {};
                    rawHeaders.split(/[\r\n]+/).forEach((line) => {
                        if (!line) return;
                        const parts = line.split(': ');
                        const header = parts.shift();
                        const value = parts.join(': ');
                        if (header) {
                            headerMap[header] = value;
                        }
                    });
                    responseHeaders = headerMap;
                }
                const response = new Response(xhr.responseText ?? '', {
                    status: xhr.status,
                    statusText: xhr.statusText,
                    headers: responseHeaders,
                });
                resolve(response);
            });

            xhr.addEventListener('error', () => {
                cleanup();
                reject(new Error(getChatPreviewTranslation('chat_attachment_upload_failed', 'Upload failed')));
            });

            xhr.addEventListener('abort', () => {
                cleanup();
                reject(createAbortError());
            });

            xhr.open(method, url);
            if (init?.headers instanceof Headers) {
                init.headers.forEach((value, key) => {
                    xhr.setRequestHeader(key, value);
                });
            } else if (init?.headers && typeof init.headers === 'object') {
                Object.entries(init.headers).forEach(([key, value]) => {
                    if (value !== undefined && value !== null) {
                        xhr.setRequestHeader(key, value);
                    }
                });
            }
            xhr.send(init?.body ?? null);
        });
    };

    const response = await authedFetch('/api/v1/files/upload', {
        method: 'POST',
        body: formData,
        adapter,
    });

    let payload = null;
    try {
        payload = await response.json();
    } catch (_) {
        payload = null;
    }

    if (response.ok && payload?.file_id) {
        return {
            fileId: payload.file_id,
            fileCategory: payload.file_category,
            alreadyAdded: Boolean(payload?.already_uploaded),
        };
    }

    const detail = payload?.detail || payload?.message || response.statusText || getChatPreviewTranslation('chat_attachment_upload_failed', 'Upload failed');
    throw new Error(detail);
}

function getFileIconName(fileType) {
    if (typeof window.getFileIconForType === 'function') {
        return window.getFileIconForType(fileType);
    }
    return 'txt.svg';
}

function getFileExtensionLabel(filename) {
    const externalHandler = (typeof window !== 'undefined' && window.getFileExtensionLabel);
    if (typeof externalHandler === 'function' && externalHandler !== getFileExtensionLabel) {
        return externalHandler(filename);
    }
    const parts = String(filename || '').split('.');
    return parts.length > 1 ? parts.pop().toUpperCase() : 'FILE';
}

function gatherPendingAttachments() {
    if (typeof window.getSelectedUploadedFileIdsForChat === 'function') {
        try {
            const selectedIds = window.getSelectedUploadedFileIdsForChat();
            if (Array.isArray(selectedIds) && selectedIds.length > 0) {
                const seenSelections = new Set(selectedIds.map((id) => String(id)));
                seenSelections.forEach((fileId) => {
                    const category = attachmentState.chatAttachmentRegistry.get(fileId);
                    if (!category) {
                        return;
                    }
                    switch (category) {
                        case 'image':
                            if (!attachmentState.requestImageIds.includes(fileId)) {
                                attachmentState.requestImageIds.push(fileId);
                            }
                            break;
                        case 'video':
                            if (!attachmentState.requestVideoIds.includes(fileId)) {
                                attachmentState.requestVideoIds.push(fileId);
                            }
                            break;
                        case 'audio':
                            if (!attachmentState.requestAudioIds.includes(fileId)) {
                                attachmentState.requestAudioIds.push(fileId);
                            }
                            break;
                        default:
                            if (!attachmentState.requestDocumentIds.includes(fileId)) {
                                attachmentState.requestDocumentIds.push(fileId);
                            }
                            break;
                    }
                });
            }
        } catch (error) {
            console.error('Failed to merge selected uploaded files into request arrays', error);
        }
    }

    const orderedIds = [
        ...attachmentState.requestImageIds,
        ...attachmentState.requestVideoIds,
        ...attachmentState.requestAudioIds,
        ...attachmentState.requestDocumentIds
    ];
    const seen = new Set();
    const attachments = [];
    orderedIds.forEach((id) => {
        if (!id || seen.has(id)) {
            return;
        }
        seen.add(id);
        const meta = attachmentState.chatAttachmentMetadata.get(id);
        if (meta) {
            attachments.push({
                id,
                file_id: id,
                original_name: meta.original_name || meta.name || '',
                mime_type: meta.mime_type || meta.file_type || '',
                file_type: meta.file_type || meta.mime_type || '',
                file_size: typeof meta.file_size === 'number' ? meta.file_size : meta.size || 0
            });
        }
    });
    return attachments;
}

/**
 * Capture every composer-owned value used by a normal chat request.
 *
 * Queue dispatch temporarily restores an older composer snapshot. This helper
 * must therefore run before sendMessage's first await and return private arrays
 * that remain valid after the user's current draft is restored immediately.
 */
function captureChatSendComposerContext() {
    const cloneObjects = (values) => (Array.isArray(values)
        ? values.map((value) => (
            value && typeof value === 'object' ? { ...value } : value
        ))
        : []);
    const attachments = gatherPendingAttachments();

    return {
        imageIds: [...attachmentState.requestImageIds],
        videoIds: [...attachmentState.requestVideoIds],
        audioIds: [...attachmentState.requestAudioIds],
        documentIds: [...attachmentState.requestDocumentIds],
        skillIds: typeof window.getSelectedSkillIds === 'function'
            ? [...(window.getSelectedSkillIds() || [])]
            : [],
        noteIds: typeof window.getSelectedNoteIds === 'function'
            ? [...(window.getSelectedNoteIds() || [])]
            : [],
        promptIds: typeof window.getSelectedPromptIds === 'function'
            ? [...(window.getSelectedPromptIds() || [])]
            : [],
        referenceParts: typeof window.getSelectedReferenceParts === 'function'
            ? [...(window.getSelectedReferenceParts() || [])]
            : [],
        chatReferenceIds: typeof window.getSelectedChatReferenceIds === 'function'
            ? [...(window.getSelectedChatReferenceIds() || [])]
            : [],
        chatReferencePayload: typeof window.getSelectedChatReferencePayload === 'function'
            ? cloneObjects(window.getSelectedChatReferencePayload() || [])
            : [],
        subagentTargets: Array.isArray(window.SubagentTargets?.getSelection?.())
            ? cloneObjects(window.SubagentTargets.getSelection())
            : null,
        attachments: cloneObjects(attachments),
    };
}

function hasUnsupportedRealtimeRequestContext(composerContext = {}) {
    return ['skillIds', 'noteIds', 'promptIds', 'referenceParts'].some((key) => (
        Array.isArray(composerContext?.[key]) && composerContext[key].length > 0
    )) || Array.isArray(composerContext?.subagentTargets);
}

function clearAcceptedRealtimeFileAttachments(fileIds = []) {
    const acceptedFileIds = Array.from(new Set(
        (Array.isArray(fileIds) ? fileIds : [])
            .map((fileId) => String(fileId || '').trim())
            .filter(Boolean)
    ));

    acceptedFileIds.forEach((fileId) => {
        if (isTemporaryAttachmentId(fileId)) {
            cancelPendingUpload(fileId, { removeFromUI: true, deleteEntry: true });
            removeRequestFileId(fileId);
            attachmentState.chatAttachmentMetadata.delete(fileId);
            return;
        }
        removeExistingChatAttachment(fileId);
    });
}

function clearChatRequestFiles(options = {}) {
    const { preserveSkills = false } = options || {};
    attachmentState.requestImageIds = [];
    attachmentState.requestAudioIds = [];
    attachmentState.requestVideoIds = [];
    attachmentState.requestDocumentIds = [];
    cancelAllPendingUploads();
    // A sent/cleared composer starts a new attachment scope. The same file may
    // legitimately be attached to a later message.
    reservedChatAttachmentFingerprints.clear();
    if (window.ChatBoxAttachmentsUI) {
        window.ChatBoxAttachmentsUI.clear();
    }
    if (typeof window.clearChatUploadedFilesSelection === 'function') {
        try {
            window.clearChatUploadedFilesSelection({ notify: false });
        } catch (_) {}
    }
    if (!preserveSkills && typeof window.clearAllSkillAttachments === 'function') {
        try {
            window.clearAllSkillAttachments();
        } catch (_) {}
    }
    if (typeof window.clearAllNoteAttachments === 'function') {
        try {
            window.clearAllNoteAttachments();
        } catch (_) {}
    }
    if (typeof window.clearAllPromptAttachments === 'function') {
        try {
            window.clearAllPromptAttachments();
        } catch (_) {}
    }
    // Connector mentions are deliberately one-request-only. Clear both their
    // composer chips and the mirrored model-settings selection after a request
    // is accepted so the next request starts with no MCP access.
    if (typeof window.clearAllMcpConnectorAttachments === 'function') {
        try {
            window.clearAllMcpConnectorAttachments();
        } catch (_) {}
    } else if (typeof window.clearMcpServersForNextRequest === 'function') {
        try {
            window.clearMcpServersForNextRequest();
        } catch (_) {}
    }
    if (typeof window.clearAllChatReferenceAttachments === 'function') {
        try {
            window.clearAllChatReferenceAttachments();
        } catch (_) {}
    }
}

if (typeof window !== 'undefined') {
    window.resetChatAttachmentsState = clearChatRequestFiles;

    /**
     * Return the exact attachment buckets used by the normal chat send path.
     *
     * Split-screen has its own request coordinator, but the composer still owns
     * attachment classification and uploaded-file selection. Exposing a cloned
     * snapshot keeps both send surfaces in parity without letting split-screen
     * mutate the private attachment registry.
     */
    window.getCurrentChatAttachmentPayload = () => {
        // Merge files selected from the existing-files picker into the request
        // buckets before copying them, just as the normal send path does.
        gatherPendingAttachments();
        return {
            imageIds: [...attachmentState.requestImageIds],
            videoIds: [...attachmentState.requestVideoIds],
            audioIds: [...attachmentState.requestAudioIds],
            documentIds: [...attachmentState.requestDocumentIds],
        };
    };

    window.getCurrentChatAttachmentFiles = () => {
        const selectedIds = (typeof window.getSelectedUploadedFileIdsForChat === 'function')
            ? (window.getSelectedUploadedFileIdsForChat() || [])
            : [];
        const ids = [
            ...attachmentState.requestImageIds,
            ...attachmentState.requestVideoIds,
            ...attachmentState.requestAudioIds,
            ...attachmentState.requestDocumentIds,
            ...selectedIds,
        ]
            .map((id) => String(id))
            .filter(Boolean);

        const seen = new Set();
        const result = [];
        ids.forEach((id) => {
            if (seen.has(id)) return;
            seen.add(id);
            const meta = attachmentState.chatAttachmentMetadata.get(id);
            const name = String(meta?.original_name || meta?.name || id);
            const mime = String(meta?.mime_type || meta?.file_type || '').toLowerCase();
            result.push({ id, name, mime_type: mime });
        });
        return result;
    };

    window.removeChatAttachmentsByIds = (ids) => {
        const list = Array.isArray(ids) ? ids : [];
        list.forEach((id) => {
            try {
                if (typeof window.handleChatAttachmentRemoval === 'function') {
                    window.handleChatAttachmentRemoval(id);
                }
            } catch (_) {
                // no-op
            }
        });
    };

    window.handleChatFileSelection = async (files) => {
        const fileList = Array.isArray(files) ? files : Array.from(files || []);
        if (!fileList.length) {
            return;
        }

        if (!window.ChatBoxAttachmentsUI) {
            return;
        }

        const allowedFiles = [];
        for (const file of fileList) {
            if (!isWithinChatAttachmentLimit(file)) {
                const friendlyName = (file && file.name) ? ` "${file.name}"` : '';
                const sizeLabel = file && typeof file.size === 'number' ? ` (${formatBytes(file.size)})` : '';
                if (typeof notifyError === 'function') {
                    notifyError(formatChatPreviewTranslation('chat_attachment_file_size_limit_error', 'File{name}{size} exceeds the 100MB limit.', {
                        name: friendlyName,
                        size: sizeLabel,
                    }));
                }
                continue;
            }

            const fingerprint = createChatAttachmentFileFingerprint(file);
            if (!reserveChatAttachmentFingerprint(fingerprint)) {
                // Duplicate selections are intentionally a no-op. The original
                // attachment remains visible, so no extra toast is needed.
                continue;
            }
            allowedFiles.push({ file, fingerprint });
        }
        if (!allowedFiles.length) {
            return;
        }

        window.ChatBoxAttachmentsUI.setUploading(true);
        try {
            for (const { file, fingerprint } of allowedFiles) {
                let tempId = null;
                const abortController = new AbortController();
                const displayData = resolveAttachmentDisplayData(file);

                try {
                    tempId = createTemporaryChatAttachment(file, { fingerprint });
                    if (tempId) {
                        pendingChatAttachmentUploads.set(tempId, { abort: () => abortController.abort() });
                    }

                    const uploadResult = await uploadChatAttachment(file, {
                        signal: abortController.signal,
                        onProgress: (percent) => {
                            if (!tempId || !window.ChatBoxAttachmentsUI) {
                                return;
                            }
                            const safePercent = Math.max(0, Math.min(100, percent || 0));
                            window.ChatBoxAttachmentsUI.updateAttachment(tempId, {
                                isUploading: true,
                                progress: safePercent,
                            });
                        },
                    });

                    pendingChatAttachmentUploads.delete(tempId);

                    if (!uploadResult?.fileId) {
                        throw new Error(getChatPreviewTranslation('chat_attachment_upload_failed', 'Upload failed'));
                    }

                    const fileId = uploadResult.fileId;
                    const category = normalizeAttachmentCategory(uploadResult.fileCategory, file.type);
                    if (uploadResult.alreadyAdded && typeof notifySuccess === 'function') {
                        notifySuccess(getChatPreviewTranslation('chat_attachment_reusing_uploaded_file', 'File already uploaded, reusing that'));
                    }
                    attachmentState.chatAttachmentRegistry.set(fileId, category);
                    addRequestFileId(category, fileId);

                    const existingMeta = attachmentState.chatAttachmentMetadata.get(fileId);
                    const fileFingerprints = new Set(
                        Array.isArray(existingMeta?.file_fingerprints)
                            ? existingMeta.file_fingerprints
                            : []
                    );
                    if (fingerprint) {
                        fileFingerprints.add(fingerprint);
                    }
                    const meta = {
                        id: fileId,
                        file_id: fileId,
                        original_name: file.name,
                        mime_type: file.type,
                        file_size: file.size,
                        file_type: file.type,
                        file_fingerprints: Array.from(fileFingerprints),
                    };
                    attachmentState.chatAttachmentMetadata.delete(tempId);
                    attachmentState.chatAttachmentMetadata.set(fileId, meta);

                    if (window.ChatBoxAttachmentsUI) {
                        const attachmentPayload = {
                            id: fileId,
                            name: displayData.name,
                            icon: displayData.icon,
                            extension: displayData.extension,
                            mimeType: file.type,
                            fileType: file.type,
                            fileSize: file.size,
                            isUploading: false,
                        };
                        if (tempId) {
                            window.ChatBoxAttachmentsUI.replaceAttachmentId(tempId, attachmentPayload);
                        } else {
                            window.ChatBoxAttachmentsUI.upsertAttachment(attachmentPayload);
                        }
                    }
                } catch (error) {
                    pendingChatAttachmentUploads.delete(tempId);
                    if (tempId) {
                        cancelPendingUpload(tempId, { deleteEntry: true });
                        attachmentState.chatAttachmentMetadata.delete(tempId);
                    } else if (fingerprint) {
                        // Creation can fail before a temporary metadata record
                        // owns the reservation, so release it directly.
                        reservedChatAttachmentFingerprints.delete(fingerprint);
                    }
                    if (error?.name === 'AbortError') {
                        continue;
                    }
                    console.error('Chat file upload failed', error);
                    const message = error?.message || `Failed to upload ${file.name}`;
                    if (typeof notifyError === 'function') {
                        notifyError(message);
                    }
                }
            }
        } finally {
            window.ChatBoxAttachmentsUI.setUploading(false);
        }
    };

    window.handleChatAttachmentRemoval = (fileId) => {
        if (!fileId) return;
        const normalizedId = String(fileId);
        if (isTemporaryAttachmentId(normalizedId)) {
            cancelPendingUpload(normalizedId, { deleteEntry: true });
            attachmentState.chatAttachmentMetadata.delete(normalizedId);
            return;
        }
        removeExistingChatAttachment(normalizedId);
    };

    window.notifyChatUploadedFileToggled = (file, selected) => {
        try {
            const fileIdRaw = file && (file.file_id ?? file.id);
            const fileId = String(fileIdRaw ?? '').trim();
            if (!fileId) {
                return;
            }

            const inferredType = String(
                file?.file_type ||
                file?.meta?.file_type ||
                file?.mime_type ||
                file?.meta?.mime_type ||
                ''
            );
            const category = normalizeAttachmentCategory(file?.file_category, inferredType);

            if (selected) {
                attachmentState.chatAttachmentRegistry.set(fileId, category);
                addRequestFileId(category, fileId);

                const originalName = file?.meta?.original_filename || file?.original_name || file?.name || '';
                const fileSize = typeof file?.file_size === 'number'
                    ? file.file_size
                    : (typeof file?.meta?.file_size === 'number'
                        ? file.meta.file_size
                        : (typeof file?.meta?.size === 'number' ? file.meta.size : 0));
                const fileType = inferredType;

                const meta = {
                    id: fileId,
                    file_id: fileId,
                    original_name: originalName,
                    mime_type: fileType,
                    file_type: fileType,
                    file_size: fileSize
                };
                attachmentState.chatAttachmentMetadata.set(fileId, meta);

                if (window.ChatBoxAttachmentsUI) {
                    const displayName = originalName || fileId;
                    const iconName = getFileIconName(fileType);
                    const extension = getFileExtensionLabel(displayName);
                    window.ChatBoxAttachmentsUI.addAttachment({
                        id: fileId,
                        name: displayName,
                        icon: iconName,
                        extension,
                        mimeType: fileType,
                        fileType,
                        fileSize,
                    });
                }
            } else {
                attachmentState.chatAttachmentRegistry.delete(fileId);
                removeRequestFileId(fileId);
                attachmentState.chatAttachmentMetadata.delete(fileId);
                if (window.ChatBoxAttachmentsUI) {
                    window.ChatBoxAttachmentsUI.removeAttachment(fileId);
                }
                syncChatSelectionAfterRemoval(fileId);
            }
        } catch (error) {
            console.error('Failed to synchronize uploaded file selection', error);
        }
    };

    window.handleFilesDeletedForChat = ({ fileIds = [], clearAll = false } = {}) => {
        if (clearAll) {
            removeExistingChatAttachment(null, { clearAll: true });
            return;
        }
        if (!Array.isArray(fileIds) || !fileIds.length) {
            return;
        }
        fileIds.forEach((fileId) => removeExistingChatAttachment(fileId));
    };
}

if (typeof window !== 'undefined') {
    window.ChatAttachmentHelpers = {
        uploadChatAttachment,
        isWithinChatAttachmentLimit,
        formatBytes,
        normalizeAttachmentCategory,
        getFileIconName,
        getFileExtensionLabel,
        createChatAttachmentFileFingerprint,
        reserveChatAttachmentFingerprint,
        releaseChatAttachmentFingerprints,
    };
}

