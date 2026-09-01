(function (root) {
    'use strict';

    const modules = root.__omlorixCanvasWidgetModules ||= {};

    function createEditorPersistenceModule(deps, state) {
        const {
            AUTO_SAVE_DELAY_MS, SPREADSHEET_CONTENT_TYPES, autoSaveTimers, canvasFileIds,
            clearHtmlExternalResourcePromptTimer, draftEditStateMap, draftMap, draftSavePromises,
            draftScrollStates, formatT, getTypeLabel, htmlPreviewPermissionMap,
            latexRenderRequestTokens, normalizeContentType, openPreviewForFile, previewRenderTimers,
            previewRevertBtn, previewSaveBtn, previewStatus, previewTitle,
            previewTrack, refreshActiveHtmlDraftAfterSave, refreshActiveMarkdownDraftAfterSave, registerCanvasFile,
            renderDraft, setHtmlViewMode, t, updateDraft,
            updateMarkdownEditorHeaderControls, updateStatusClass,
        } = deps;
        function clearPreviewRenderTimer(key) {
            const timer = previewRenderTimers.get(key);
            if (timer) {
                clearTimeout(timer);
                previewRenderTimers.delete(key);
            }
        }
    
        function clearAutoSaveTimer(key) {
            const timer = autoSaveTimers.get(key);
            if (timer) {
                clearTimeout(timer);
                autoSaveTimers.delete(key);
            }
        }
    
        function destroyActiveMarkdownEditor() {
            if (state.activeMarkdownEditorInstance && typeof state.activeMarkdownEditorInstance.destroy === 'function') {
                state.activeMarkdownEditorInstance.destroy();
            }
            state.activeMarkdownEditorInstance = null;
            updateMarkdownEditorHeaderControls(null);
        }
    
        function destroyActiveSpreadsheetEditor({
            persistPending = true,
            commitPending = true,
        } = {}) {
            state.spreadsheetRenderToken += 1;
            const editor = state.activeSpreadsheetEditorInstance;
            const draftKey = state.activeSpreadsheetEditorDraftKey;
            if (editor) {
                const draft = draftKey ? draftMap.get(draftKey) : null;
                // A focused cell editor has not necessarily emitted blur yet.
                // Flush it before reading dirty state or starting serialization.
                if (commitPending) editor.commitPendingEdit?.();
                const editState = draftKey
                    ? getDraftEditState(draftKey, `revision:${draft?.canvasRevision || 0}`)
                    : null;
                if (
                    persistPending
                    && draft?.spreadsheetEditor === editor
                    && (editState?.dirty || editState?.saving)
                ) {
                    // Start serialization before detaching the editor. XLSX
                    // package patching can finish asynchronously using the
                    // captured editor even after another Canvas replaces its DOM.
                    void saveActiveDraftEdits(draftKey);
                }
                draftMap.forEach((draft) => {
                    if (draft?.spreadsheetEditor === editor) delete draft.spreadsheetEditor;
                });
                if (typeof editor.destroy === 'function') editor.destroy();
            }
            state.activeSpreadsheetEditorInstance = null;
            state.activeSpreadsheetEditorDraftKey = '';
        }
    
        function schedulePreviewRender(key, callback, delay = 140) {
            if (!key || typeof callback !== 'function') return;
            clearPreviewRenderTimer(key);
            const timer = setTimeout(() => {
                previewRenderTimers.delete(key);
                callback();
            }, delay);
            previewRenderTimers.set(key, timer);
        }
    
        function getDraftEditState(key, initialContent = '') {
            if (!key) return null;
            const normalized = String(initialContent ?? '');
            if (!draftEditStateMap.has(key)) {
                draftEditStateMap.set(key, {
                    baselineContent: normalized,
                    draftContent: normalized,
                    dirty: false,
                    saving: false,
                    autoSavePending: false,
                    error: '',
                    updatedAt: Date.now(),
                    editSeq: 0,
                });
            }
            return draftEditStateMap.get(key);
        }
    
        function syncDraftEditStateFromServer(key, serverContent, { force = false } = {}) {
            const state = getDraftEditState(key, serverContent);
            if (!state) return null;
            const normalized = String(serverContent ?? '');
            if (force || !state.dirty) {
                state.baselineContent = normalized;
                state.draftContent = normalized;
                state.dirty = false;
                state.error = '';
                state.autoSavePending = false;
                state.updatedAt = Date.now();
            }
            return state;
        }
    
        function getRenderableContentForDraft(draftKey, fallbackContent = '') {
            const state = draftKey ? draftEditStateMap.get(draftKey) : null;
            if (!state) return String(fallbackContent ?? '');
            return String(state.draftContent ?? '');
        }
    
        function isDraftPersistable(draft) {
            return Boolean(draft?.fileId);
        }
    
        function isDraftEditorInteractive(draft) {
            if (!draft) return false;
            if (normalizeContentType(draft.contentType) === 'pdf') return false;
            if (isDraftPersistable(draft)) return true;
            return normalizeContentType(draft.contentType) === 'markdown';
        }
    
        function setButtonDisabledState(button, disabled) {
            if (!button) return;
            button.disabled = Boolean(disabled);
            button.classList.toggle('is-disabled', Boolean(disabled));
            button.setAttribute('aria-disabled', disabled ? 'true' : 'false');
        }
    
        function updateEditorActionButtons(draft, editState) {
            const persistable = isDraftPersistable(draft);
            const saving = Boolean(editState?.saving);
            const dirty = Boolean(editState?.dirty);
    
            if (previewSaveBtn) {
                setButtonDisabledState(previewSaveBtn, !persistable || saving || !dirty);
            }
    
            if (previewRevertBtn) {
                setButtonDisabledState(previewRevertBtn, !persistable || saving || !dirty);
            }
        }
    
        function updateDraftEditStateFromInput(draftKey, nextContent) {
            const state = getDraftEditState(draftKey, nextContent);
            if (!state) return null;
            state.draftContent = String(nextContent ?? '');
            state.dirty = state.draftContent !== state.baselineContent;
            state.autoSavePending = state.dirty;
            state.error = '';
            state.updatedAt = Date.now();
            return state;
        }
    
        function getPreviewStatusText(draft, editState) {
            if (editState?.saving) return t('canvas_status_saving_changes', 'Saving changes...');
            if (editState?.autoSavePending) {
                return isDraftPersistable(draft)
                    ? t('canvas_status_saving_changes', 'Saving changes...')
                    : t('canvas_status_waiting_file_creation', 'Waiting for file creation...');
            }
            if (editState?.error) return editState.error;
            const fallbackType = getTypeLabel(draft?.contentType);
            return draft?.status || formatT('canvas_status_preparing_type', 'Preparing {type}', { type: fallbackType });
        }
    
        /** Return a locale-independent visual state for the translated status. */
        function getPreviewStatusKind(draft, editState) {
            if (editState?.saving || editState?.autoSavePending) return 'generating';
            if (editState?.error) return 'error';
            return String(draft?.statusKind || '').trim().toLowerCase();
        }
    
        function buildFileDownloadUrl(fileId, options = {}) {
            const normalizedId = String(fileId || '').trim();
            const inline = options?.inline === true;
            if (typeof window.resolveChatFileDownloadUrl === 'function') {
                return window.resolveChatFileDownloadUrl(normalizedId, { inline });
            }
            const params = new URLSearchParams({ file_id: normalizedId });
            if (inline) params.set('inline', 'true');
            return `/api/v1/files/download?${params.toString()}`;
        }
    
        /** Build a dependency URL whose permission is limited to one Canvas. */
        function buildCanvasAssetUrl(canvasFileId, fileId) {
            const normalizedCanvasId = String(canvasFileId || '').trim();
            const normalizedAssetId = String(fileId || '').trim();
            if (!normalizedCanvasId) {
                return buildFileDownloadUrl(fileId, { inline: true });
            }
            const params = new URLSearchParams({
                canvas_file_id: normalizedCanvasId,
                asset_file_id: normalizedAssetId,
            });
            return `/api/v1/files/canvas/assets/content?${params.toString()}`;
        }
    
        function getApiErrorMessage(payload, fallback) {
            const detail = payload?.detail;
            if (detail === 'canvas_asset_access_denied') {
                return t(
                    'canvas_asset_access_denied',
                    'A referenced file is unavailable or still waiting for its owner’s approval.',
                );
            }
            if (detail && typeof detail === 'object') {
                return String(detail.message || detail.detail || fallback);
            }
            return String(detail || payload?.message || fallback);
        }
    
        async function renderLatexPdfSource({ draft }) {
            if (!draft?.fileId || typeof window.authedFetch !== 'function') {
                throw new Error(t('latex_pdf_rerender_unavailable', 'LaTeX PDF cannot be re-rendered right now.'));
            }
    
            const response = await window.authedFetch('/api/v1/files/canvas/latex/render', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    file_id: String(draft.fileId),
                    expected_revision: Number(draft.canvasRevision) || undefined,
                }),
            });
    
            let payload = null;
            try {
                payload = await response.json();
            } catch (_) {
                payload = null;
            }
    
            if (!response.ok) {
                const fallback = formatT('latex_pdf_rerender_failed_status', 'LaTeX render failed ({status})', { status: response.status });
                const error = new Error(getApiErrorMessage(payload, fallback));
                error.status = response.status;
                error.payload = payload;
                throw error;
            }
            return payload || {};
        }
    
        async function saveCanvasFileContent({ fileId, content, contentType, fileName, fileIds }) {
            if (!fileId || typeof window.authedFetch !== 'function') {
                throw new Error(t('canvas_file_save_unavailable', 'Canvas file cannot be saved right now'));
            }
    
            const response = await window.authedFetch('/api/v1/files/canvas/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_id: String(fileId),
                    content: String(content ?? ''),
                    content_type: normalizeContentType(contentType),
                    filename: String(fileName || ''),
                    ...(Array.isArray(fileIds) ? { file_ids: fileIds } : {}),
                }),
                credentials: 'include',
            });
    
            let payload = null;
            try {
                payload = await response.json();
            } catch (_) {
                payload = null;
            }
    
            if (!response.ok) {
                const fallback = formatT('canvas_file_save_failed_status', 'Save failed ({status})', { status: response.status });
                throw new Error(getApiErrorMessage(payload, fallback));
            }
            return payload || {};
        }
    
        async function renderSavedLatexDraft(targetDraftKey, { switchToPreview = true } = {}) {
            const draftKey = String(targetDraftKey || '');
            const initialDraft = draftMap.get(draftKey);
            if (!initialDraft?.fileId || normalizeContentType(initialDraft.contentType) !== 'latex') return null;
    
            const expectedRevision = Number(initialDraft.canvasRevision) || 0;
            if (
                initialDraft.renderStatus === 'ready'
                && Number(initialDraft.renderRevision) === expectedRevision
                && initialDraft.pdfFileId
            ) {
                if (initialDraft.previewRequested) {
                    renderDraft(updateDraft(draftKey, { previewRequested: false }));
                }
                if (switchToPreview) setHtmlViewMode('preview');
                return initialDraft;
            }
    
            const token = Number(latexRenderRequestTokens.get(draftKey) || 0) + 1;
            latexRenderRequestTokens.set(draftKey, token);
            const renderingDraft = updateDraft(draftKey, {
                previewRequested: false,
                renderStatus: 'rendering',
                status: t('latex_pdf_compiling_pdf', 'Compiling LaTeX PDF...'),
                statusKind: 'generating',
                allowHtmlPreview: Boolean(initialDraft.pdfFileId),
            });
            renderDraft(renderingDraft);
    
            try {
                const result = await renderLatexPdfSource({ draft: renderingDraft });
                const currentDraft = draftMap.get(draftKey);
                if (
                    latexRenderRequestTokens.get(draftKey) !== token
                    || (expectedRevision && Number(currentDraft?.canvasRevision) !== expectedRevision)
                ) {
                    return currentDraft || null;
                }
    
                const pdfFileId = String(result.file_id || '');
                const renderedRevision = Number(result.render_revision ?? result.source_revision ?? expectedRevision) || expectedRevision;
                const readyDraft = updateDraft(draftKey, {
                    previewRequested: false,
                    sourceFileId: String(result.source_file_id || renderingDraft.fileId),
                    pdfFileId,
                    pdfFileName: String(result.file_name || renderingDraft.pdfFileName || 'document.pdf'),
                    title: String(result.title || renderingDraft.title || renderingDraft.fileName || 'LaTeX PDF'),
                    canvasRevision: Number(result.source_revision) || renderedRevision,
                    renderRevision: renderedRevision,
                    renderStatus: 'ready',
                    status: t('latex_pdf_status_saved_rendered', 'Saved and rendered'),
                    statusKind: 'saved',
                    allowHtmlPreview: Boolean(pdfFileId),
                    logExcerpt: String(result.log_excerpt || ''),
                    inputFileNames: Array.isArray(result.input_file_names) ? result.input_file_names : [],
                    assetFileIds: Array.isArray(result.asset_file_ids) ? result.asset_file_ids : (renderingDraft.assetFileIds || []),
                });
                renderDraft(readyDraft);
                if (switchToPreview && pdfFileId) setHtmlViewMode('preview');
                return readyDraft;
            } catch (error) {
                const currentDraft = draftMap.get(draftKey);
                if (latexRenderRequestTokens.get(draftKey) !== token) return currentDraft || null;
                const detail = error?.payload?.detail;
                const logExcerpt = String(
                    (detail && typeof detail === 'object' ? detail.log_excerpt : '')
                    || currentDraft?.logExcerpt
                    || ''
                );
                const failedDraft = updateDraft(draftKey, {
                    previewRequested: false,
                    renderStatus: error?.status === 409 ? 'stale' : 'failed',
                    status: error?.status === 409
                        ? t('canvas_latex_preview_stale', 'Preview is out of date')
                        : t('latex_pdf_status_render_failed', 'LaTeX render failed'),
                    statusKind: 'error',
                    allowHtmlPreview: Boolean(currentDraft?.pdfFileId),
                    logExcerpt,
                });
                renderDraft(failedDraft);
                if (typeof window.notifyError === 'function') {
                    window.notifyError(error?.message || t('latex_pdf_rerender_failed', 'Failed to render LaTeX PDF.'));
                }
                if (error?.status === 409 && failedDraft.fileId) {
                    // A collaborator saved a newer source revision. Reload it
                    // before retrying so the editor never keeps submitting the
                    // revision that the backend correctly rejected.
                    void openPreviewForFile(
                        failedDraft.fileId,
                        failedDraft.fileName || 'document.tex',
                        'latex',
                    );
                }
                return failedDraft;
            }
        }
    
        function migrateDraftClientState(fromKey, toKey) {
            if (!fromKey || !toKey || fromKey === toKey) return;
    
            const previousState = draftEditStateMap.get(fromKey);
            if (previousState) {
                draftEditStateMap.set(toKey, previousState);
                draftEditStateMap.delete(fromKey);
            }
    
            clearPreviewRenderTimer(fromKey);
            clearAutoSaveTimer(fromKey);
            clearHtmlExternalResourcePromptTimer(fromKey);
    
            const scrollState = draftScrollStates.get(fromKey);
            if (scrollState) {
                draftScrollStates.set(toKey, scrollState);
                draftScrollStates.delete(fromKey);
            }
    
            const htmlPermissions = htmlPreviewPermissionMap.get(fromKey);
            if (htmlPermissions) {
                htmlPreviewPermissionMap.set(toKey, htmlPermissions);
                htmlPreviewPermissionMap.delete(fromKey);
            }
            if (latexRenderRequestTokens.has(fromKey)) {
                latexRenderRequestTokens.set(toKey, latexRenderRequestTokens.get(fromKey));
                latexRenderRequestTokens.delete(fromKey);
            }
            if (state.pendingHtmlExternalResourceConsent?.draftKey === fromKey) {
                state.pendingHtmlExternalResourceConsent.draftKey = toKey;
            }
    
            const existingDraft = draftMap.get(fromKey);
            if (existingDraft) {
                draftMap.set(toKey, { ...existingDraft, key: toKey });
                draftMap.delete(fromKey);
            }
    
            if (state.activeDraftKey === fromKey) {
                state.activeDraftKey = toKey;
            }
        }
    
        function queueAutoSaveForDraft(draftKey, { immediate = false } = {}) {
            if (!draftKey) return;
            const draft = draftMap.get(draftKey);
            const editState = getDraftEditState(draftKey, draft?.content || '');
            if (!draft || !editState) return;
    
            clearAutoSaveTimer(draftKey);
    
            if (!editState.dirty) {
                editState.autoSavePending = false;
                return;
            }
    
            editState.autoSavePending = true;
    
            if (editState.saving) {
                return;
            }
    
            if (!isDraftPersistable(draft)) {
                if (previewStatus && state.activeDraftKey === draftKey) {
                    previewStatus.textContent = getPreviewStatusText(draft, editState);
                    updateStatusClass(previewStatus.textContent, getPreviewStatusKind(draft, editState));
                }
                return;
            }
    
            const timer = setTimeout(() => {
                autoSaveTimers.delete(draftKey);
                saveActiveDraftEdits(draftKey);
            }, immediate ? 0 : AUTO_SAVE_DELAY_MS);
            autoSaveTimers.set(draftKey, timer);
        }
    
        async function saveSpreadsheetFileContent({ draft, serialized }) {
            if (!draft?.fileId || !serialized?.blob || typeof window.authedFetch !== 'function') {
                throw new Error(t('canvas_file_save_unavailable', 'The canvas file cannot be saved right now'));
            }
            const formData = new FormData();
            formData.append('file', serialized.blob, serialized.fileName);
            formData.append('file_id', String(draft.fileId));
            formData.append('file_format', String(draft.contentType));
            // Optimistic concurrency prevents collaborators from silently
            // replacing bytes saved from a newer server snapshot.
            formData.append('expected_revision', String(Number(draft.canvasRevision) || 0));
            formData.append('filename', String(draft.fileName || serialized.fileName));
            formData.append(
                'requires_recalculation',
                serialized.requiresRecalculation === true ? 'true' : 'false',
            );
            const response = await window.authedFetch('/api/v1/files/canvas/spreadsheet/save', {
                method: 'POST',
                headers: { 'Content-Type': null },
                credentials: 'include',
                body: formData,
            });
            const payload = await response.json().catch(() => null);
            if (!response.ok) {
                const responseDetail = payload?.detail;
                const detail = typeof responseDetail === 'object' && responseDetail
                    ? String(responseDetail.code || '').trim()
                    : String(responseDetail || '').trim();
                const knownErrors = {
                    spreadsheet_archive_too_complex: t(
                        'spreadsheet_archive_too_complex',
                        'This workbook is too large or complex to edit safely in the browser.',
                    ),
                    spreadsheet_preview_too_large: t(
                        'spreadsheet_preview_too_large',
                        'This spreadsheet is too large to edit in the browser. Download it to continue.',
                    ),
                    spreadsheet_revision_conflict: t(
                        'spreadsheet_revision_conflict',
                        'This spreadsheet changed elsewhere. Reload it before saving again.',
                    ),
                };
                const message = knownErrors[detail]
                    || (typeof responseDetail === 'object' ? String(responseDetail?.message || '') : detail);
                throw new Error(message || formatT('canvas_file_save_failed_status', 'Save failed ({status})', {
                    status: response.status,
                }));
            }
            return payload || {};
        }
    
        async function performSpreadsheetDraftSave(draftKey, draft, editState) {
            const editor = draft?.spreadsheetEditor;
            if (!editor || !editState?.dirty || editState.saving) return !editState?.dirty;
            clearAutoSaveTimer(draftKey);
            const saveLifecycleGeneration = state.draftLifecycleGeneration;
            const saveStartedEditSeq = Number(editState.editSeq) || 0;
            editState.saving = true;
            editState.autoSavePending = false;
            editState.error = '';
            if (previewStatus && state.activeDraftKey === draftKey) {
                previewStatus.textContent = getPreviewStatusText(draft, editState);
                updateStatusClass(previewStatus.textContent, getPreviewStatusKind(draft, editState));
            }
            try {
                const serialized = await editor.serialize(draft.contentType);
                const saveResult = await saveSpreadsheetFileContent({ draft, serialized });
                if (saveLifecycleGeneration !== state.draftLifecycleGeneration) {
                    // A chat reset intentionally discarded this draft's UI state,
                    // but the already-started persistence request still protected
                    // the user's last edit. Do not recreate stale draft maps after
                    // navigation merely to publish its completed status.
                    editor.destroy?.();
                    return true;
                }
                const savedName = String(saveResult.file_name || draft.fileName || serialized.fileName);
                const savedBinaryContent = serialized.bytes instanceof ArrayBuffer
                    ? serialized.bytes.slice(0)
                    : serialized.bytes;
                const updated = updateDraft(draftKey, {
                    fileName: savedName,
                    binaryContent: savedBinaryContent,
                    canvasRevision: Number(saveResult.canvas_revision) || draft.canvasRevision || 0,
                    spreadsheetRequiresRecalculation: Boolean(
                        saveResult.spreadsheet_requires_recalculation
                        ?? serialized.requiresRecalculation
                    ),
                    status: t('canvas_status_saved', 'Saved'),
                    statusKind: 'saved',
                }, { activate: state.activeDraftKey === draftKey });
                editState.saving = false;
                editState.error = '';
                if ((Number(editState.editSeq) || 0) === saveStartedEditSeq) {
                    editor.markSaved(serialized);
                    editState.baselineContent = `revision:${updated.canvasRevision}`;
                    editState.draftContent = editState.baselineContent;
                    editState.dirty = false;
                    editState.autoSavePending = false;
                } else {
                    editState.dirty = true;
                    editState.autoSavePending = true;
                    queueAutoSaveForDraft(draftKey, { immediate: true });
                }
                updateEditorActionButtons(updated, editState);
                if (previewTitle && state.activeDraftKey === draftKey) previewTitle.textContent = savedName;
                if (previewStatus && state.activeDraftKey === draftKey) {
                    previewStatus.textContent = getPreviewStatusText(updated, editState);
                    updateStatusClass(previewStatus.textContent, getPreviewStatusKind(updated, editState));
                }
                return true;
            } catch (error) {
                editState.saving = false;
                editState.autoSavePending = false;
                editState.error = error?.message || t('spreadsheet_save_failed', 'Spreadsheet save failed');
                if (previewStatus && state.activeDraftKey === draftKey) {
                    previewStatus.textContent = getPreviewStatusText(draft, editState);
                    updateStatusClass(previewStatus.textContent, getPreviewStatusKind(draft, editState));
                }
                const message = error?.message || editState.error;
                if (typeof window.notifyError === 'function') window.notifyError(message);
                else if (typeof showNotification === 'function') showNotification(message, 'error');
                return false;
            }
        }
    
        async function performActiveDraftSave(targetDraftKey = state.activeDraftKey) {
            const draftKey = String(targetDraftKey || '');
            if (!draftKey) return false;
            const draft = draftMap.get(draftKey);
            if (!draft || !isDraftPersistable(draft)) return false;
    
            const editState = getDraftEditState(draftKey, draft.content || '');
            if (SPREADSHEET_CONTENT_TYPES.has(normalizeContentType(draft.contentType))) {
                return performSpreadsheetDraftSave(draftKey, draft, editState);
            }
            if (!editState || !editState.dirty) return true;
            if (editState.saving) return false;
    
            clearAutoSaveTimer(draftKey);
            const contentToSave = String(editState.draftContent ?? '');
            editState.saving = true;
            editState.autoSavePending = false;
            editState.error = '';
            updateEditorActionButtons(draft, editState);
            if (previewStatus) {
                previewStatus.textContent = getPreviewStatusText(draft, editState);
                updateStatusClass(previewStatus.textContent, getPreviewStatusKind(draft, editState));
            }
    
            try {
                const saveResult = await saveCanvasFileContent({
                    fileId: draft.fileId,
                    content: contentToSave,
                    contentType: draft.contentType,
                    fileName: draft.fileName,
                    fileIds: normalizeContentType(draft.contentType) === 'latex'
                        ? (Array.isArray(draft.assetFileIds) ? draft.assetFileIds : [])
                        : undefined,
                });
                if (Number(saveResult.pending_asset_approval_count || 0) > 0) {
                    const pendingMessage = t(
                        'canvas_asset_approval_pending',
                        'Saved. The preview will become available after the file owner approves the referenced file.',
                    );
                    if (typeof showNotification === 'function') showNotification(pendingMessage, 'info');
                }
                const savedType = normalizeContentType(saveResult.content_type || draft.contentType);
                const savedName = String(saveResult.file_name || draft.fileName || 'canvas.md');
                const savedContent = String(saveResult.content ?? contentToSave);
                const savedFileId = String(saveResult.file_id || draft.fileId || draftKey);
                const migratedKey = savedFileId !== draftKey ? savedFileId : draftKey;
    
                if (savedFileId !== draftKey) {
                    migrateDraftClientState(draftKey, savedFileId);
                }
    
                const updated = updateDraft(migratedKey, {
                    key: migratedKey,
                    fileId: savedFileId,
                    fileName: savedName,
                    contentType: savedType,
                    content: savedContent,
                    status: savedType === 'latex'
                        ? t('canvas_latex_preview_stale', 'Preview is out of date')
                        : t('canvas_status_saved', 'Saved'),
                    statusKind: 'saved',
                    allowHtmlPreview: savedType === 'latex'
                        ? Boolean(saveResult.pdf_file_id || draft.pdfFileId)
                        : true,
                    canvasRevision: Number(saveResult.canvas_revision) || draft.canvasRevision || 0,
                    pdfFileId: String(saveResult.pdf_file_id || draft.pdfFileId || ''),
                    pdfFileName: String(saveResult.pdf_file_name || draft.pdfFileName || ''),
                    assetFileIds: Array.isArray(saveResult.asset_file_ids)
                        ? saveResult.asset_file_ids
                        : (draft.assetFileIds || []),
                    renderRevision: Number(saveResult.render_revision) || draft.renderRevision || 0,
                    renderStatus: String(saveResult.render_status || (savedType === 'latex' ? 'stale' : '')),
                });
    
                const nextState = getDraftEditState(migratedKey, savedContent);
                if (nextState) {
                    nextState.saving = false;
                    nextState.error = '';
                    if (String(nextState.draftContent ?? '') === contentToSave) {
                        syncDraftEditStateFromServer(migratedKey, savedContent, { force: true });
                    } else {
                        nextState.baselineContent = savedContent;
                        nextState.dirty = String(nextState.draftContent ?? '') !== nextState.baselineContent;
                        nextState.autoSavePending = nextState.dirty;
                        nextState.updatedAt = Date.now();
                    }
                }
                if (savedType === 'latex') {
                    canvasFileIds.add(savedFileId);
                    registerCanvasFile(savedFileId, savedName, 'latex');
                    renderDraft(updated);
                    if (nextState?.dirty) {
                        queueAutoSaveForDraft(migratedKey, { immediate: true });
                    }
                } else if (
                    !refreshActiveMarkdownDraftAfterSave(updated, nextState)
                    && !refreshActiveHtmlDraftAfterSave(updated, nextState)
                ) {
                    renderDraft(updated);
                }
                if (savedType !== 'latex' && nextState?.dirty) {
                    queueAutoSaveForDraft(migratedKey, { immediate: true });
                }
                return true;
            } catch (error) {
                editState.saving = false;
                editState.autoSavePending = false;
                editState.error = 'Save failed';
                updateEditorActionButtons(draft, editState);
                if (previewStatus) {
                    previewStatus.textContent = getPreviewStatusText(draft, editState);
                    updateStatusClass(previewStatus.textContent, getPreviewStatusKind(draft, editState));
                }
                const message = error?.message || 'Failed to save canvas';
                if (typeof window.notifyError === 'function') {
                    window.notifyError(message);
                } else if (typeof showNotification === 'function') {
                    showNotification(message, 'error');
                }
                return false;
            }
        }
    
        /**
         * Save a draft once and share that request with callers such as autosave,
         * Cmd/Ctrl+S, and the LaTeX Preview tab. This ordering guarantees that a
         * preview always compiles the latest persisted source revision.
         */
        async function saveActiveDraftEdits(targetDraftKey = state.activeDraftKey) {
            const draftKey = String(targetDraftKey || '');
            if (!draftKey) return false;
    
            const existingPromise = draftSavePromises.get(draftKey);
            if (existingPromise) return existingPromise;
    
            const savePromise = performActiveDraftSave(draftKey);
            draftSavePromises.set(draftKey, savePromise);
            try {
                return await savePromise;
            } finally {
                if (draftSavePromises.get(draftKey) === savePromise) {
                    draftSavePromises.delete(draftKey);
                }
            }
        }
    
        function revertActiveDraftEdits() {
            const draftKey = state.activeDraftKey;
            if (!draftKey) return;
            const draft = draftMap.get(draftKey);
            if (!draft || !isDraftPersistable(draft)) return;
    
            const state = getDraftEditState(draftKey, draft.content || '');
            if (!state || !state.dirty || state.saving) return;
            clearAutoSaveTimer(draftKey);
            if (SPREADSHEET_CONTENT_TYPES.has(normalizeContentType(draft.contentType))) {
                // Destroy the cell input without committing it. Revert must throw
                // away the focused value along with the already-recorded edits.
                destroyActiveSpreadsheetEditor({
                    persistPending: false,
                    commitPending: false,
                });
            }
            state.draftContent = state.baselineContent;
            state.dirty = false;
            state.autoSavePending = false;
            state.error = '';
            renderDraft(draft, true);
        }
    
        function getScrollState(key) {
            if (!key) return null;
            if (!draftScrollStates.has(key)) {
                draftScrollStates.set(key, {
                    autoFollow: true,
                    userInterrupted: false,
                    trackScrollTop: 0,
                    trackScrollLeft: 0,
                    codeScrollTop: 0,
                    codeScrollLeft: 0,
                    markdownEditorScrollTop: 0,
                    markdownEditorScrollLeft: 0,
                    markdownSourceScrollTop: 0,
                    markdownSourceScrollLeft: 0,
                    markdownActiveView: 'editor',
                    restoreOnNextRender: false,
                });
            }
            return draftScrollStates.get(key);
        }
    
        function resetScrollState(key, { autoFollow = true } = {}) {
            if (!key) return;
            draftScrollStates.set(key, {
                autoFollow,
                userInterrupted: !autoFollow,
                trackScrollTop: 0,
                trackScrollLeft: 0,
                codeScrollTop: 0,
                codeScrollLeft: 0,
                markdownEditorScrollTop: 0,
                markdownEditorScrollLeft: 0,
                markdownSourceScrollTop: 0,
                markdownSourceScrollLeft: 0,
                markdownActiveView: 'editor',
                restoreOnNextRender: false,
            });
        }
    
        function getMarkdownEditorScrollElement() {
            return previewTrack ? previewTrack.querySelector('.canvas-md-editor-view') : null;
        }
    
        function getMarkdownSourceScrollElement() {
            return previewTrack ? previewTrack.querySelector('.canvas-md-source-editor') : null;
        }
    
        function captureScrollState(key) {
            if (!key) return;
            const state = getScrollState(key);
            if (!state) return;
            if (previewTrack) {
                state.trackScrollTop = previewTrack.scrollTop;
                state.trackScrollLeft = previewTrack.scrollLeft;
            }
            const existingCodeView = previewTrack ? previewTrack.querySelector('.canvas-html-code-view') : null;
            if (existingCodeView) {
                state.codeScrollTop = existingCodeView.scrollTop;
                state.codeScrollLeft = existingCodeView.scrollLeft;
            }
            const editorViewport = state.activeMarkdownEditorInstance?.getScrollState?.() || null;
            if (editorViewport) {
                state.markdownEditorScrollTop = editorViewport.editorScrollTop;
                state.markdownEditorScrollLeft = editorViewport.editorScrollLeft;
                state.markdownSourceScrollTop = editorViewport.sourceScrollTop;
                state.markdownSourceScrollLeft = editorViewport.sourceScrollLeft;
                state.markdownActiveView = editorViewport.view === 'source' ? 'source' : 'editor';
            } else {
                const markdownEditorView = getMarkdownEditorScrollElement();
                if (markdownEditorView) {
                    state.markdownEditorScrollTop = markdownEditorView.scrollTop;
                    state.markdownEditorScrollLeft = markdownEditorView.scrollLeft;
                }
                const markdownSourceEditor = getMarkdownSourceScrollElement();
                if (markdownSourceEditor) {
                    state.markdownSourceScrollTop = markdownSourceEditor.scrollTop;
                    state.markdownSourceScrollLeft = markdownSourceEditor.scrollLeft;
                }
                const markdownSourceView = previewTrack?.querySelector('.canvas-md-editor-source-view');
                state.markdownActiveView = markdownSourceView && !markdownSourceView.hidden ? 'source' : 'editor';
            }
        }
    
        function getStoredMarkdownScrollTop(state) {
            if (!state) return 0;
            return state.markdownActiveView === 'source'
                ? Math.max(Number(state.markdownSourceScrollTop) || 0, 0)
                : Math.max(Number(state.markdownEditorScrollTop) || 0, 0);
        }
    
        /** Capture the currently visible saved canvas before a new tool-call key takes over. */
        function rememberCanvasScrollForToolCall(draftKey) {
            const normalizedDraftKey = String(draftKey || '').trim();
            const sourceKey = String(state.activeDraftKey || '').trim();
            if (!normalizedDraftKey || !sourceKey || normalizedDraftKey === sourceKey) return;
            if (state.pendingCanvasToolScrollSnapshot?.draftKey === normalizedDraftKey) return;
    
            const sourceDraft = draftMap.get(sourceKey);
            if (!sourceDraft?.fileId && !canvasFileIds.has(sourceKey)) return;
            captureScrollState(sourceKey);
            const sourceState = draftScrollStates.get(sourceKey);
            if (!sourceState) return;
            state.pendingCanvasToolScrollSnapshot = {
                draftKey: normalizedDraftKey,
                sourceKey,
                fileId: String(sourceDraft?.fileId || sourceKey),
                state: { ...sourceState },
                applied: false,
            };
        }
    
        /** Seed a transient edit draft once its streamed file_id identifies the source canvas. */
        function restoreCanvasScrollForToolEdit(draftKey, fileId) {
            const normalizedDraftKey = String(draftKey || '').trim();
            const normalizedFileId = String(fileId || '').trim();
            const snapshot = state.pendingCanvasToolScrollSnapshot;
            if (!normalizedDraftKey || !normalizedFileId || !snapshot) return false;
            if (snapshot.applied) return false;
            if (snapshot.draftKey !== normalizedDraftKey) return false;
            if (snapshot.fileId !== normalizedFileId && snapshot.sourceKey !== normalizedFileId) return false;
    
            draftScrollStates.set(normalizedDraftKey, {
                ...snapshot.state,
                autoFollow: false,
                userInterrupted: true,
                restoreOnNextRender: true,
            });
            snapshot.applied = true;
            return true;
        }
    
        function isDraftStreaming(draft) {
            if (String(draft?.statusKind || '').trim().toLowerCase() === 'generating') return true;
            if (!draft?.status) return false;
            const status = String(draft.status).toLowerCase();
            return status.includes('stream') || status.includes('writing');
        }
    
        function shouldAutoScrollDraft(draft, state) {
            if (!draft || !state) return false;
            if (!state.autoFollow) return false;
            return normalizeContentType(draft.contentType) === 'html' && isDraftStreaming(draft);
        }
    
        function isElementVisible(el) {
            if (!el) return false;
            if (typeof el.offsetParent !== 'undefined') {
                return el.offsetParent !== null;
            }
            return true;
        }
    
        function runWithProgrammaticScroll(fn) {
            state.suppressUserScrollEvents = true;
            try {
                fn();
            } finally {
                requestAnimationFrame(() => {
                    state.suppressUserScrollEvents = false;
                });
            }
        }
    
        function attachScrollListeners(codeView) {
            if (previewTrack && previewTrack.dataset.canvasScrollBind !== 'true') {
                previewTrack.dataset.canvasScrollBind = 'true';
                previewTrack.addEventListener('scroll', handleUserScrollEvent, { passive: true });
                previewTrack.addEventListener('wheel', handleUserGestureEvent, { passive: true });
                previewTrack.addEventListener('touchstart', handleUserGestureEvent, { passive: true });
                previewTrack.addEventListener('pointerdown', handlePreviewTrackPointerDown, { passive: true });
            }
            if (codeView && codeView.dataset.canvasScrollBind !== 'true') {
                codeView.dataset.canvasScrollBind = 'true';
                codeView.addEventListener('scroll', handleUserScrollEvent, { passive: true });
                codeView.addEventListener('wheel', handleUserGestureEvent, { passive: true });
                codeView.addEventListener('touchstart', handleUserGestureEvent, { passive: true });
            }
            const markdownEditorView = getMarkdownEditorScrollElement();
            if (markdownEditorView && markdownEditorView.dataset.canvasScrollBind !== 'true') {
                markdownEditorView.dataset.canvasScrollBind = 'true';
                markdownEditorView.addEventListener('scroll', handleUserScrollEvent, { passive: true });
                markdownEditorView.addEventListener('wheel', handleUserGestureEvent, { passive: true });
                markdownEditorView.addEventListener('touchstart', handleUserGestureEvent, { passive: true });
            }
            const markdownSourceEditor = getMarkdownSourceScrollElement();
            if (markdownSourceEditor && markdownSourceEditor.dataset.canvasScrollBind !== 'true') {
                markdownSourceEditor.dataset.canvasScrollBind = 'true';
                markdownSourceEditor.addEventListener('scroll', handleUserScrollEvent, { passive: true });
                markdownSourceEditor.addEventListener('wheel', handleUserGestureEvent, { passive: true });
                markdownSourceEditor.addEventListener('touchstart', handleUserGestureEvent, { passive: true });
            }
        }
    
        /** Stop bottom-follow as soon as the user grabs the vertical scrollbar. */
        function handlePreviewTrackPointerDown(event) {
            if (!previewTrack || event.currentTarget !== previewTrack) return;
            const rect = previewTrack.getBoundingClientRect();
            if (event.clientX >= rect.right - 18) {
                handleUserGestureEvent();
            }
        }
    
        function handleUserGestureEvent() {
            const key = state.activeDraftKey;
            if (!key) return;
            const state = getScrollState(key);
            if (!state) return;
            state.autoFollow = false;
            state.userInterrupted = true;
            state.restoreOnNextRender = false;
        }
    
        function handleUserScrollEvent(event) {
            if (state.suppressUserScrollEvents) return;
            hideReferenceToolbar();
            const key = state.activeDraftKey;
            if (!key) return;
            const state = getScrollState(key);
            if (!state) return;
            state.restoreOnNextRender = false;
            if (!state.userInterrupted) {
                state.userInterrupted = true;
                state.autoFollow = false;
            }
            if (event.currentTarget === previewTrack && previewTrack) {
                state.trackScrollTop = previewTrack.scrollTop;
                state.trackScrollLeft = previewTrack.scrollLeft;
                return;
            }
            const target = event.currentTarget;
            if (target && target.classList && target.classList.contains('canvas-html-code-view')) {
                state.codeScrollTop = target.scrollTop;
                state.codeScrollLeft = target.scrollLeft;
                return;
            }
            if (target && target.classList && target.classList.contains('canvas-md-editor-view')) {
                state.markdownEditorScrollTop = target.scrollTop;
                state.markdownEditorScrollLeft = target.scrollLeft;
                return;
            }
            if (target && target.classList && target.classList.contains('canvas-md-source-editor')) {
                state.markdownSourceScrollTop = target.scrollTop;
                state.markdownSourceScrollLeft = target.scrollLeft;
            }
        }
    
        function applyScrollState(key, draft) {
            if (!key || !previewTrack) return;
            const state = getScrollState(key);
            if (!state) return;
            const codeView = previewTrack.querySelector('.canvas-html-code-view');
            attachScrollListeners(codeView);
    
            const applyMarkdownEditorScroll = () => {
                state.activeMarkdownEditorInstance?.restoreScrollState?.({
                    view: state.markdownActiveView,
                    editorScrollTop: state.markdownEditorScrollTop,
                    editorScrollLeft: state.markdownEditorScrollLeft,
                    sourceScrollTop: state.markdownSourceScrollTop,
                    sourceScrollLeft: state.markdownSourceScrollLeft,
                });
                const markdownEditorView = getMarkdownEditorScrollElement();
                if (markdownEditorView) {
                    markdownEditorView.scrollTop = state.markdownEditorScrollTop || 0;
                    markdownEditorView.scrollLeft = state.markdownEditorScrollLeft || 0;
                }
                const markdownSourceEditor = getMarkdownSourceScrollElement();
                if (markdownSourceEditor) {
                    markdownSourceEditor.scrollTop = state.markdownSourceScrollTop || 0;
                    markdownSourceEditor.scrollLeft = state.markdownSourceScrollLeft || 0;
                }
            };
    
            const shouldAuto = shouldAutoScrollDraft(draft, state);
            if (shouldAuto) {
                runWithProgrammaticScroll(() => {
                    const maxTrackTop = previewTrack.scrollHeight - previewTrack.clientHeight;
                    previewTrack.scrollTop = Math.max(maxTrackTop, 0);
                    if (codeView && isElementVisible(codeView)) {
                        // Streaming follow is intentionally vertical-only. The
                        // horizontal position must stay at the beginning instead
                        // of chasing the end of the newest (possibly long) line.
                        const maxCodeTop = codeView.scrollHeight - codeView.clientHeight;
                        codeView.scrollTop = Math.max(maxCodeTop, 0);
                    }
                });
                state.trackScrollTop = previewTrack.scrollTop;
                state.trackScrollLeft = previewTrack.scrollLeft;
                if (codeView) {
                    state.codeScrollTop = codeView.scrollTop;
                    state.codeScrollLeft = codeView.scrollLeft;
                }
                return;
            }
    
            if (state.userInterrupted) {
                runWithProgrammaticScroll(() => {
                    previewTrack.scrollTop = state.trackScrollTop;
                    previewTrack.scrollLeft = state.trackScrollLeft;
                    if (codeView) {
                        codeView.scrollTop = state.codeScrollTop;
                        codeView.scrollLeft = state.codeScrollLeft;
                    }
                    applyMarkdownEditorScroll();
                });
                // Rich editor layout can settle one frame after the new DOM is inserted.
                requestAnimationFrame(() => runWithProgrammaticScroll(applyMarkdownEditorScroll));
            }
        }
    
        /**
         * Restore the streaming viewport after replacing the temporary article
         * with the final rich editor. Both elements can own vertical overflow at
         * different breakpoints, so the position is applied to each of them.
         */
        function restoreScrollAfterMarkdownStream(key, scrollTop, autoFollow) {
            if (!key || !previewTrack) return;
            const scrollState = getScrollState(key);
            const restore = () => {
                if (!previewTrack || state.activeDraftKey !== key) return;
                const editorView = getMarkdownEditorScrollElement();
                runWithProgrammaticScroll(() => {
                    previewTrack.scrollLeft = 0;
                    previewTrack.scrollTop = autoFollow
                        ? Math.max(previewTrack.scrollHeight - previewTrack.clientHeight, 0)
                        : Math.max(Number(scrollTop) || 0, 0);
                    if (editorView) {
                        editorView.scrollLeft = 0;
                        editorView.scrollTop = autoFollow
                            ? Math.max(editorView.scrollHeight - editorView.clientHeight, 0)
                            : Math.max(Number(scrollTop) || 0, 0);
                    }
                });
                if (scrollState) {
                    scrollState.trackScrollTop = previewTrack.scrollTop;
                    scrollState.trackScrollLeft = 0;
                    scrollState.markdownEditorScrollTop = editorView?.scrollTop || 0;
                    scrollState.markdownEditorScrollLeft = 0;
                    scrollState.restoreOnNextRender = false;
                }
            };
            restore();
            requestAnimationFrame(restore);
        }
    

        return Object.freeze({
            clearPreviewRenderTimer, clearAutoSaveTimer, destroyActiveMarkdownEditor, destroyActiveSpreadsheetEditor,
            schedulePreviewRender, getDraftEditState, syncDraftEditStateFromServer, getRenderableContentForDraft,
            isDraftPersistable, isDraftEditorInteractive, setButtonDisabledState, updateEditorActionButtons,
            updateDraftEditStateFromInput, getPreviewStatusText, getPreviewStatusKind, buildFileDownloadUrl,
            buildCanvasAssetUrl, getApiErrorMessage, renderLatexPdfSource, saveCanvasFileContent,
            renderSavedLatexDraft, migrateDraftClientState, queueAutoSaveForDraft, saveSpreadsheetFileContent,
            performSpreadsheetDraftSave, performActiveDraftSave, saveActiveDraftEdits, revertActiveDraftEdits,
            getScrollState, resetScrollState, getMarkdownEditorScrollElement, getMarkdownSourceScrollElement,
            captureScrollState, getStoredMarkdownScrollTop, rememberCanvasScrollForToolCall, restoreCanvasScrollForToolEdit,
            isDraftStreaming, shouldAutoScrollDraft, isElementVisible, runWithProgrammaticScroll,
            attachScrollListeners, handlePreviewTrackPointerDown, handleUserGestureEvent, handleUserScrollEvent,
            applyScrollState, restoreScrollAfterMarkdownStream,
        });
    }

    modules.editorPersistence = Object.freeze({ create: createEditorPersistenceModule });
})(globalThis);
