const attachmentState = {
    requestImageIds: [], // Array of strings of file ids of images
    requestDocumentIds: [], // Array of strings of file ids of documents
    requestAudioIds: [], // Array of strings of file ids of audio files
    requestVideoIds: [], // Array of strings of file ids of video files
    chatAttachmentRegistry: new Map(), // fileId -> category
    chatAttachmentMetadata: new Map(), // fileId -> metadata returned by upload
    pendingChatAttachmentUploadedFiles: [], // Metadata for attachments pending render
    pendingChatAttachmentUploadsQueue: new Map(), // tempId -> { abort }
};

const codeSnippetRegistry = new Map(); // codeId -> code content
const pendingChatAttachmentUploads = new Map(); // tempId -> { abort }
// Fingerprints are reserved before an upload starts. This closes the small
// window where a second picker/drop event could queue the same local file while
// the first request is still in flight.
const reservedChatAttachmentFingerprints = new Set();
let visibilityReconnectState = null;
// One normal-chat/regeneration transport is active at a time. Split-screen
// panels keep equivalent state independently in splitScreen.js.
let activeChatGenerationTransport = null;
const TEMP_ATTACHMENT_PREFIX = 'temp-upload-';
const MAX_CHAT_ATTACHMENT_BYTES = 100 * 1024 * 1024; // 100MB limit

function beginChatGenerationTransport(generationId) {
    const normalizedId = String(generationId || '').trim();
    activeChatGenerationTransport = {
        generationId: normalizedId,
        abortController: new AbortController(),
        reader: null,
        cancelled: false,
        cancellationPromise: null,
        messageId: '',
        transcriptRoot: null,
    };
    return activeChatGenerationTransport;
}

function releaseChatGenerationTransport(generationId) {
    if (activeChatGenerationTransport?.generationId !== String(generationId || '').trim()) {
        return;
    }
    activeChatGenerationTransport = null;
}

async function requestGenerationCancellation(generationId, { attempts = 10 } = {}) {
    const normalizedId = String(generationId || '').trim();
    if (!normalizedId || typeof window.authedFetch !== 'function') {
        return false;
    }

    // The first attempt can race the send endpoint before it reserves the
    // client-created ID. Retry briefly so an immediate Stop remains reliable
    // without allowing unauthenticated pre-emptive cancellation IDs.
    for (let attempt = 0; attempt < attempts; attempt += 1) {
        try {
            const params = new URLSearchParams({ generation_id: normalizedId });
            const response = await window.authedFetch(`/api/v1/chats/cancel?${params.toString()}`, {
                method: 'POST',
                headers: { Accept: 'application/json' },
                body: '',
            });
            const result = await response?.clone?.().json().catch(() => null);
            if (response?.ok && result?.status !== 'error') {
                return true;
            }
        } catch (_) {
            // A later retry may succeed after the generation reservation lands.
        }
        if (attempt + 1 < attempts) {
            await new Promise((resolve) => setTimeout(resolve, 75));
        }
    }
    return false;
}

function cancelChatGenerationTransport() {
    const generationId = String(
        activeChatGenerationTransport?.generationId || window.currentGenerationId || ''
    ).trim();
    if (!generationId) {
        window.pendingCancelGeneration = true;
        return true;
    }

    if (activeChatGenerationTransport) {
        activeChatGenerationTransport.cancelled = true;
    }
    window.pendingCancelGeneration = false;
    const cancelledMessageId = String(activeChatGenerationTransport?.messageId || '').trim();
    const transcriptRoot = activeChatGenerationTransport?.transcriptRoot || null;
    if (cancelledMessageId) {
        if (typeof clearMediaGenPlaceholderForNonFileEvent === 'function') {
            clearMediaGenPlaceholderForNonFileEvent(cancelledMessageId);
        }
        window.finalizeCancelledAssistantStream?.(cancelledMessageId, transcriptRoot);
    }
    // Start the independent control request before tearing down the response
    // transport. The backend continues generation on its background worker and
    // receives this cancellation even though the browser stream closes now.
    const cancellationPromise = requestGenerationCancellation(generationId);
    if (activeChatGenerationTransport?.generationId === generationId) {
        activeChatGenerationTransport.cancellationPromise = cancellationPromise;
    }
    void cancellationPromise.then((acknowledged) => {
        if (!acknowledged) return;
        window.messageQueue?.handleGenerationTerminal?.({
            generationId,
            surface: 'chat',
            status: 'cancelled',
        });
    });
    Promise.resolve(activeChatGenerationTransport?.reader?.cancel?.()).catch(() => {});
    try {
        activeChatGenerationTransport?.abortController?.abort?.();
    } catch (_) {}
    window.endGenerationUI?.();
    return true;
}

if (typeof window !== 'undefined') {
    window.cancelGeneration = cancelChatGenerationTransport;
    window.requestGenerationCancellation = requestGenerationCancellation;
}

const unsupportedFileWarningState = {
    ids: new Set(),
};

function shouldReduceMotionForSendMessage() {
    try {
        return typeof window.matchMedia === 'function'
            && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (_error) {
        return false;
    }
}

function normalizeUnsupportedFileIds(rawFileIds) {
    if (!Array.isArray(rawFileIds)) {
        return [];
    }
    const seen = new Set();
    const normalized = [];
    rawFileIds.forEach((rawId) => {
        const sid = String(rawId || '').trim();
        if (!sid || seen.has(sid)) {
            return;
        }
        seen.add(sid);
        normalized.push(sid);
    });
    return normalized;
}

function updateUnsupportedFileWarnings(rawFileIds = [], { replace = false } = {}) {
    const nextIds = normalizeUnsupportedFileIds(rawFileIds);
    if (replace) {
        unsupportedFileWarningState.ids = new Set(nextIds);
    } else {
        nextIds.forEach((sid) => unsupportedFileWarningState.ids.add(sid));
    }
    if (typeof window.applyUnsupportedFileWarnings === 'function') {
        window.applyUnsupportedFileWarnings(Array.from(unsupportedFileWarningState.ids));
    }
}

function clearUnsupportedFileWarningState() {
    unsupportedFileWarningState.ids.clear();
    if (typeof window.clearUnsupportedFileWarnings === 'function') {
        window.clearUnsupportedFileWarnings();
    }
}

if (typeof window !== 'undefined') {
    window.getUnsupportedFileWarningIds = () => Array.from(unsupportedFileWarningState.ids);
    window.addEventListener('modelSelect:changed', () => {
        clearUnsupportedFileWarningState();
    });
}

function isChatReferenceApiError(errorData) {
    const code = errorData?.detail?.code;
    return code === 'chat_reference_context_too_large' || code === 'chat_reference_invalid';
}

function generateTempAttachmentId() {
    return `${TEMP_ATTACHMENT_PREFIX}${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function resolveAttachmentDisplayData(file) {
    return {
        name: file?.name || 'File',
        icon: getFileIconName(file?.type),
        extension: getFileExtensionLabel(file?.name),
    };
}

/**
 * Build a stable identity for one browser File without reading its contents.
 *
 * File pickers create a new File object for every selection, so object identity
 * cannot detect repeat selections. The browser-provided name, size, MIME type,
 * and modification time identify the same local file while still allowing a
 * changed version of that file to be attached.
 */
function createChatAttachmentFileFingerprint(file) {
    if (!file || typeof file !== 'object') {
        return '';
    }

    const name = String(file.name || '');
    const size = Number.isFinite(file.size) ? String(file.size) : '';
    const type = String(file.type || '').trim().toLowerCase();
    const lastModified = Number.isFinite(file.lastModified) ? String(file.lastModified) : '';
    if (!name && !size && !type && !lastModified) {
        return '';
    }
    return JSON.stringify([name, size, type, lastModified]);
}

/** Reserve a local file for this composer, returning false for a duplicate. */
function reserveChatAttachmentFingerprint(fingerprint) {
    if (!fingerprint) {
        return true;
    }
    if (reservedChatAttachmentFingerprints.has(fingerprint)) {
        return false;
    }
    reservedChatAttachmentFingerprints.add(fingerprint);
    return true;
}

/** Release every local-file identity owned by an attachment metadata record. */
function releaseChatAttachmentFingerprints(metadata) {
    const fingerprints = Array.isArray(metadata?.file_fingerprints)
        ? metadata.file_fingerprints
        : [metadata?.file_fingerprint];
    fingerprints.filter(Boolean).forEach((fingerprint) => {
        reservedChatAttachmentFingerprints.delete(fingerprint);
    });
}

function isTemporaryAttachmentId(id) {
    return typeof id === 'string' && id.startsWith(TEMP_ATTACHMENT_PREFIX);
}

function cancelPendingUpload(fileId, { removeFromUI = true, deleteEntry = false } = {}) {
    if (!fileId) {
        return false;
    }

    const pending = pendingChatAttachmentUploads.get(fileId);
    if (pending) {
        pending.cancelled = true;
        try {
            pending.abort?.();
        } catch (_) {}
        if (deleteEntry) {
            pendingChatAttachmentUploads.delete(fileId);
        }
    } else if (deleteEntry) {
        pendingChatAttachmentUploads.delete(fileId);
    }

    if (deleteEntry) {
        releaseChatAttachmentFingerprints(attachmentState.chatAttachmentMetadata.get(fileId));
    }

    if (removeFromUI && window.ChatBoxAttachmentsUI) {
        window.ChatBoxAttachmentsUI.removeAttachment(fileId);
    }

    return Boolean(pending);
}

function createTemporaryChatAttachment(file, { progress = 0, fingerprint = '' } = {}) {
    const tempId = generateTempAttachmentId();
    const display = resolveAttachmentDisplayData(file);
    attachmentState.chatAttachmentMetadata.set(tempId, {
        id: tempId,
        file_id: tempId,
        original_name: display.name,
        mime_type: file?.type || '',
        file_type: file?.type || '',
        file_size: typeof file?.size === 'number' ? file.size : 0,
        file_fingerprints: fingerprint ? [fingerprint] : [],
        temporary: true,
    });
    if (window.ChatBoxAttachmentsUI) {
        window.ChatBoxAttachmentsUI.upsertAttachment({
            id: tempId,
            ...display,
            mimeType: file?.type || '',
            fileType: file?.type || '',
            fileSize: typeof file?.size === 'number' ? file.size : 0,
            isUploading: true,
            progress,
        });
    }
    return tempId;
}

function cancelAllPendingUploads() {
    const ids = Array.from(pendingChatAttachmentUploads.keys());
    ids.forEach((tempId) => {
        cancelPendingUpload(tempId, { deleteEntry: true });
        attachmentState.chatAttachmentMetadata.delete(tempId);
    });
}

function syncChatSelectionAfterRemoval(normalizedId) {
    if (typeof window.setSelectedUploadedFileIdsForChat !== 'function' || typeof window.getSelectedUploadedFileIdsForChat !== 'function') {
        return;
    }
    try {
        if (normalizedId === null) {
            window.setSelectedUploadedFileIdsForChat([], { notify: false });
            return;
        }
        const currentSelection = window.getSelectedUploadedFileIdsForChat() || [];
        const nextSelection = currentSelection.filter((id) => String(id) !== normalizedId);
        window.setSelectedUploadedFileIdsForChat(nextSelection, { notify: false });
    } catch (error) {
        console.error('Failed to synchronize chat selection after removal', error);
    }
}

function removeExistingChatAttachment(fileId, options = {}) {
    const { syncSelection = true, clearAll = false } = options;
    if (clearAll) {
        reservedChatAttachmentFingerprints.clear();
        attachmentState.chatAttachmentRegistry.clear();
        attachmentState.chatAttachmentMetadata.clear();
        attachmentState.requestImageIds = [];
        attachmentState.requestAudioIds = [];
        attachmentState.requestVideoIds = [];
        attachmentState.requestDocumentIds = [];
        pendingChatAttachmentUploads.clear();
        if (window.ChatBoxAttachmentsUI && typeof window.ChatBoxAttachmentsUI.clear === 'function') {
            window.ChatBoxAttachmentsUI.clear();
        } else if (window.ChatBoxAttachmentsUI && typeof window.ChatBoxAttachmentsUI.removeAttachment === 'function') {
            Array.from(attachmentState.chatAttachmentMetadata.keys()).forEach((id) => {
                window.ChatBoxAttachmentsUI.removeAttachment(id);
            });
        }
        if (syncSelection) {
            syncChatSelectionAfterRemoval(null);
        }
        return;
    }

    const normalizedId = String(fileId || '').trim();
    if (!normalizedId || isTemporaryAttachmentId(normalizedId)) {
        return;
    }
    releaseChatAttachmentFingerprints(attachmentState.chatAttachmentMetadata.get(normalizedId));
    attachmentState.chatAttachmentRegistry.delete(normalizedId);
    removeRequestFileId(normalizedId);
    attachmentState.chatAttachmentMetadata.delete(normalizedId);
    if (window.ChatBoxAttachmentsUI) {
        window.ChatBoxAttachmentsUI.removeAttachment(normalizedId);
    }
    if (syncSelection) {
        syncChatSelectionAfterRemoval(normalizedId);
    }
}

function isWithinChatAttachmentLimit(file) {
    if (!file || typeof file.size !== 'number') return true;
    return file.size <= MAX_CHAT_ATTACHMENT_BYTES;
}

function formatBytes(bytes) {
    if (!Number.isFinite(bytes)) return '';
    if (bytes >= 1024 * 1024) {
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }
    if (bytes >= 1024) {
        return `${(bytes / 1024).toFixed(1)} KB`;
    }
    return `${bytes} B`;
}

function clearVisibilityReconnectState() {
    if (!visibilityReconnectState) {
        return;
    }
    const { visibilityHandler, focusHandler, pageShowHandler } = visibilityReconnectState;
    if (visibilityHandler && typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', visibilityHandler);
    }
    if (focusHandler && typeof window !== 'undefined') {
        window.removeEventListener('focus', focusHandler);
    }
    if (pageShowHandler && typeof window !== 'undefined') {
        window.removeEventListener('pageshow', pageShowHandler);
    }
    visibilityReconnectState = null;
}

function scheduleVisibilityReconnect(chatId) {
    if (!chatId || typeof document === 'undefined' || typeof window === 'undefined') {
        return false;
    }

    clearVisibilityReconnectState();

    const attemptReconnect = async () => {
        if (typeof document !== 'undefined' && document.hidden) {
            return;
        }
        clearVisibilityReconnectState();
        if (typeof checkAndAttachOngoingStream === 'function') {
            try {
                await checkAndAttachOngoingStream(chatId);
            } catch (error) {
                console.error('Failed to resume chat stream after backgrounding', error);
                if (typeof notifyError === 'function') {
                    notifyError(getChatPreviewTranslation('chat_connection_interrupted_retry', 'Connection interrupted. Please try again.'));
                }
            }
        } else if (typeof notifyError === 'function') {
            notifyError(getChatPreviewTranslation('chat_connection_interrupted_retry', 'Connection interrupted. Please try again.'));
        }
    };

    const visibilityHandler = () => {
        if (!document.hidden) {
            attemptReconnect();
        }
    };
    const focusHandler = () => {
        attemptReconnect();
    };
    const pageShowHandler = () => {
        attemptReconnect();
    };

    document.addEventListener('visibilitychange', visibilityHandler);
    window.addEventListener('focus', focusHandler);
    window.addEventListener('pageshow', pageShowHandler);

    visibilityReconnectState = {
        chatId,
        visibilityHandler,
        focusHandler,
        pageShowHandler
    };

    return true;
}

