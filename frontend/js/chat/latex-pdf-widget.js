(function () {
    'use strict';

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const panel = document.getElementById('latex-pdf-PreviewPanel');
    const closeBtn = document.getElementById('latex-pdf-PreviewClose');
    const titleEl = document.getElementById('latex-pdf-PreviewTitle');
    const statusEl = document.getElementById('latex-pdf-PreviewStatus');
    const downloadBtn = document.getElementById('latex-pdf-PreviewDownload');
    const frame = document.getElementById('latex-pdf-PreviewFrame');
    const loading = document.getElementById('latex-pdf-PreviewLoading');
    const loadingText = document.getElementById('latex-pdf-PreviewLoadingText');
    const logDetails = document.getElementById('latex-pdf-PreviewLog');
    const logText = document.getElementById('latex-pdf-PreviewLogText');

    const PREVIEW_LOAD_TIMEOUT_MS = 1800;
    const PREVIEW_PDF_FRAGMENT = 'toolbar=1&navpanes=0&view=FitH&zoom=page-width';

    let active = null;
    let activeObjectUrl = '';
    let activeFrameUrl = '';
    let activePreviewToken = 0;
    let activePreviewTimer = null;
    let generatingCard = null;
    const latexPdfFileIds = new Set();
    let downloadControlsEnabled = false;
    // Tracks whether the preview has finished loading so the share/download
    // actions can be interacted with. The download button is only usable when
    // this is true *and* a downloadable file exists (downloadControlsEnabled).
    let previewActionsEnabled = false;
    const downloadBtnDefaultHtml = downloadBtn ? downloadBtn.innerHTML : '';

    function esc(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function showPanel() {
        if (!panel) return;
        window.closeOtherArtifactPreviews?.('latex-pdf-preview');
        panel.classList.add('visible');
        panel.setAttribute('aria-hidden', 'false');
        document.body.classList.add('latex-pdf-preview-open');
    }

    function clearPreviewTimer() {
        if (activePreviewTimer) {
            clearTimeout(activePreviewTimer);
            activePreviewTimer = null;
        }
    }

    function clearFrame() {
        clearPreviewTimer();
        activePreviewToken += 1;
        activeFrameUrl = '';
        if (frame) {
            frame.onload = null;
            frame.classList.remove('visible');
            frame.removeAttribute('src');
        }
        if (activeObjectUrl) {
            URL.revokeObjectURL(activeObjectUrl);
            activeObjectUrl = '';
        }
    }

    function hidePanel() {
        if (!panel) return;
        clearFrame();
        active = null;
        setDownloadControls(false);
        setActionsEnabled(false);
        panel.classList.remove('visible');
        panel.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('latex-pdf-preview-open');
    }

    function setStatus(text, state) {
        if (statusEl) {
            statusEl.textContent = text || '';
            statusEl.className = 'latex-pdf-preview-status ' + (state || '');
        }
    }

    function setLoading(message, visible) {
        if (loadingText && message) {
            loadingText.textContent = message;
        }
        if (loading) {
            loading.classList.toggle('hidden', !visible);
        }
        if (frame) {
            frame.classList.toggle('visible', !visible && Boolean(activeFrameUrl || activeObjectUrl));
        }
    }

    // Applies the combined enabled/disabled state to the direct PDF download
    // button using the shared chatDownloadControls helper.
    // Keep the native button state synchronized with the shared disabled class,
    // aria-disabled value, and tabindex management used by the preview controls.
    function applyDownloadButtonState() {
        const enabled = previewActionsEnabled && downloadControlsEnabled;
        window.chatDownloadControls?.setDownloadControlsEnabled?.({
            button: downloadBtn,
            enabled,
            disabledClass: 'disabled',
            manageTabIndex: true,
            defaultHtml: downloadBtnDefaultHtml,
            label: t('files_preview_download', 'Download'),
        });
    }

    function setActionsEnabled(enabled) {
        previewActionsEnabled = Boolean(enabled);
        applyDownloadButtonState();
    }

    function setDownloadControls(enabled) {
        downloadControlsEnabled = Boolean(enabled);
        applyDownloadButtonState();
    }

    function setDownloadBusy(isBusy) {
        window.chatDownloadControls?.setDownloadBusy?.({
            button: downloadBtn,
            busy: isBusy,
            enabled: previewActionsEnabled && downloadControlsEnabled,
            disabledClass: 'disabled',
            manageTabIndex: true,
            defaultHtml: downloadBtnDefaultHtml,
            busyLabel: t('slide_presentation_downloading', 'Downloading...'),
            idleLabel: t('files_preview_download', 'Download'),
        });
    }

    function normalizePayload(data) {
        const payload = data && typeof data === 'object' ? data : {};
        const meta = payload.meta && typeof payload.meta === 'object' ? payload.meta : {};
        const isSourceRecord = meta.latex_source === true;
        const recordFileId = String(payload.file_id || payload.fileId || '').trim();
        const recordFileName = String(payload.file_name || payload.fileName || meta.original_filename || '').trim();
        const fileId = String(
            payload.pdf_file_id
            || payload.pdfFileId
            || (isSourceRecord ? meta.latex_pdf_file_id : recordFileId)
            || ''
        ).trim();
        const fileName = String(
            payload.pdf_file_name
            || payload.pdfFileName
            || meta.latex_pdf_file_name
            || (!isSourceRecord ? recordFileName : '')
            || 'document.pdf'
        ).trim() || 'document.pdf';
        const title = String(
            payload.title
            || payload.display_title
            || payload.displayTitle
            || meta.latex_display_title
            || meta.title
            || fileName
        ).trim() || fileName;
        const normalized = {
            fileId,
            sourceFileId: String(
                payload.source_file_id
                || payload.sourceFileId
                || meta.latex_source_file_id
                || (isSourceRecord ? recordFileId : '')
                || ''
            ).trim(),
            fileName,
            sourceFileName: String(
                payload.source_file_name
                || payload.sourceFileName
                || (isSourceRecord ? recordFileName : '')
                || fileName.replace(/\.pdf$/i, '.tex')
                || 'document.tex'
            ).trim() || 'document.tex',
            title,
            mimeType: String(payload.mime_type || payload.mimeType || 'application/pdf'),
            size: payload.size,
            compiler: payload.compiler || 'pdflatex',
            logExcerpt: String(payload.log_excerpt || payload.logExcerpt || meta.latex_log_excerpt || ''),
            executionTime: payload.execution_time || payload.executionTime,
            assetFileIds: Array.isArray(payload.asset_file_ids) ? payload.asset_file_ids : (Array.isArray(payload.assetFileIds) ? payload.assetFileIds : (Array.isArray(meta.latex_asset_file_ids) ? meta.latex_asset_file_ids : [])),
            inputFileNames: Array.isArray(payload.input_file_names) ? payload.input_file_names : (Array.isArray(payload.inputFileNames) ? payload.inputFileNames : (Array.isArray(meta.latex_input_file_names) ? meta.latex_input_file_names : [])),
        };
        return normalized;
    }

    function registerFile(payload) {
        if (!payload.fileId || !window.canvasFilesDropdown) return;
        window.canvasFilesDropdown.registerFile(payload.fileId, payload.title || payload.fileName, 'latex-pdf', function () {
            openPdfPreview(payload).catch((error) => {
                console.error('[latex-pdf] Failed to open registered PDF preview', error);
                if (typeof window.notifyError === 'function') {
                    window.notifyError(error?.message || t('latex_pdf_preview_open_failed', 'Failed to open PDF preview.'));
                }
            });
        });
    }

    function parentForMessage(messageId) {
        let parent = null;
        if (messageId) parent = document.getElementById('a-' + messageId);
        if (!parent) parent = document.getElementById('chatAreaContainer');
        return parent;
    }

    function suppressGenericAttachmentForFile(messageId, fileId) {
        const normalizedFileId = String(fileId || '').trim();
        if (!normalizedFileId) return;
        const parent = parentForMessage(messageId);
        if (!parent) return;
        parent.querySelectorAll('.assistant-file[data-file-id]').forEach((element) => {
            if (String(element.dataset.fileId || '').trim() === normalizedFileId) {
                element.remove();
            }
        });
    }

    function isLatexPdfFile(fileId) {
        return latexPdfFileIds.has(String(fileId || '').trim());
    }

    function registerRepresentedFileIds(messageId, payload) {
        [payload.fileId, payload.sourceFileId].forEach((fileId) => {
            const normalizedFileId = String(fileId || '').trim();
            if (!normalizedFileId) return;
            latexPdfFileIds.add(normalizedFileId);
            suppressGenericAttachmentForFile(messageId, normalizedFileId);
        });
    }

    function finalizePriorThinkingBlocks(parent) {
        if (!parent || typeof finalizeThinkingBlocks !== 'function') {
            return;
        }
        finalizeThinkingBlocks(parent);
    }

    function insertCard(parent, card) {
        if (!parent || !card) return;
        finalizePriorThinkingBlocks(parent);
        const listDiv = parent.querySelector('.assistant-message-list');
        if (listDiv && listDiv.parentNode === parent) {
            parent.insertBefore(card, listDiv);
        } else {
            parent.appendChild(card);
        }
    }

    function bindCard(card, payload) {
        const btn = card.querySelector('.latex-pdf-result-btn');
        if (!btn) return;
        btn.addEventListener('click', () => {
            openPdfPreview(payload).catch((error) => {
                console.error('[latex-pdf] Failed to open PDF preview from card', error);
                if (typeof window.notifyError === 'function') {
                    window.notifyError(error?.message || t('latex_pdf_preview_open_failed', 'Failed to open PDF preview.'));
                }
            });
        });
    }

    function cardHtml(payload, state) {
        const ready = state === 'ready';
        const error = state === 'error';
        const title = error
            ? t('latex_pdf_compile_failed', 'LaTeX compile failed')
            : (ready ? payload.title || payload.fileName : t('latex_pdf_compiling_title', 'Compiling LaTeX PDF'));
        const sub = error
            ? payload.message || t('latex_pdf_compile_failed_desc', 'Review the compile error and try again.')
            : (ready ? t('latex_pdf_ready_desc', 'PDF ready to preview, download, or share.') : t('latex_pdf_compiling_desc', 'This may take a moment.'));
        const iconRegistry = typeof Icons !== 'undefined' ? Icons : null;
        const latexIcons = typeof latexPdfStatusIcons !== 'undefined' ? latexPdfStatusIcons : iconRegistry?.latexPdfStatusIcons;
        const icon = error
            ? latexIcons?.error
            : (ready ? latexIcons?.ready : latexIcons?.compiling);
        const renderedIcon = iconRegistry?.withSvgAttributes(icon, {
            width: '18',
            height: '18',
            'aria-hidden': 'false',
        }) || '';
        return '' +
            '<div class="latex-pdf-result-icon">' +
                renderedIcon +
            '</div>' +
            '<div class="latex-pdf-result-info">' +
                '<div class="latex-pdf-result-title">' + esc(title) + '</div>' +
                '<div class="latex-pdf-result-sub">' + esc(sub) + '</div>' +
            '</div>' +
            (ready ? '<button class="latex-pdf-result-btn" type="button">' + esc(t('latex_pdf_view_pdf', 'View PDF')) + '</button>' : '');
    }

    function addGeneratingCard(messageId, data) {
        const parent = parentForMessage(messageId);
        if (!parent) return;
        const card = document.createElement('div');
        card.className = 'latex-pdf-result-card compiling';
        card.innerHTML = cardHtml({ title: data?.title || 'LaTeX PDF' }, 'compiling');
        insertCard(parent, card);
        generatingCard = card;
    }

    function addCompletionCard(messageId, data) {
        const payload = normalizePayload(data);
        registerRepresentedFileIds(messageId, payload);
        registerFile(payload);
        const card = generatingCard || document.createElement('div');
        generatingCard = null;
        card.className = 'latex-pdf-result-card ready';
        card.innerHTML = cardHtml(payload, 'ready');
        bindCard(card, payload);
        if (!card.parentNode) {
            insertCard(parentForMessage(messageId), card);
        }
    }

    function addErrorCard(messageId, data) {
        const parent = parentForMessage(messageId);
        if (!parent && !generatingCard) return;
        const card = generatingCard || document.createElement('div');
        generatingCard = null;
        card.className = 'latex-pdf-result-card error';
        card.innerHTML = cardHtml({ message: data?.message }, 'error');
        if (!card.parentNode) {
            insertCard(parent, card);
        }
    }

    async function downloadFileBlob(fileId, fileName) {
        if (!fileId) {
            throw new Error(t('latex_pdf_missing_file_id', 'PDF file id is missing.'));
        }
        return window.chatDownloadControls.downloadBlobFromUrl(
            buildDownloadUrl(fileId),
            fileName || 'document.pdf',
            { errorMessage: t('latex_pdf_download_failed', 'Failed to download PDF.') }
        );
    }

    function buildDownloadUrl(fileId, options = {}) {
        const normalizedId = String(fileId || '').trim();
        const inline = options?.inline === true;
        if (typeof window.resolveChatFileDownloadUrl === 'function') {
            return window.resolveChatFileDownloadUrl(normalizedId, { inline });
        }
        const params = new URLSearchParams({ file_id: normalizedId });
        if (inline) params.set('inline', 'true');
        return `/api/v1/files/download?${params.toString()}`;
    }

    function buildPreviewUrl(fileId) {
        const baseUrl = buildDownloadUrl(fileId, { inline: true });
        return `${baseUrl.split('#')[0]}#${PREVIEW_PDF_FRAGMENT}`;
    }

    function completePreviewLoad(token) {
        if (token !== activePreviewToken) return;
        clearPreviewTimer();
        setStatus(t('latex_pdf_ready', 'Ready'), 'ready');
        setActionsEnabled(true);
        setLoading('', false);
    }

    async function openPdfPreview(data) {
        const payload = normalizePayload(data);
        if (!payload.fileId) {
            throw new Error(t('latex_pdf_missing_file_id', 'PDF file id is missing.'));
        }
        if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.openLatexPdfPreview === 'function') {
            await window.canvasMarkdownWidget.openLatexPdfPreview({
                file_id: payload.fileId,
                source_file_id: payload.sourceFileId,
                file_name: payload.fileName,
                source_file_name: payload.sourceFileName,
                title: payload.title,
                log_excerpt: payload.logExcerpt,
                asset_file_ids: payload.assetFileIds,
                input_file_names: payload.inputFileNames,
            });
            return;
        }
        active = payload;
        if (titleEl) titleEl.textContent = payload.title || payload.fileName;
        setStatus(t('latex_pdf_loading', 'Loading PDF'), 'compiling');
        setDownloadControls(Boolean(payload.fileId));
        setActionsEnabled(false);
        if (logDetails && logText) {
            logText.textContent = payload.logExcerpt || '';
            logDetails.hidden = !payload.logExcerpt;
        }
        setLoading(t('latex_pdf_loading_pdf', 'Loading PDF...'), true);
        showPanel();

        if (frame) {
            const token = activePreviewToken + 1;
            activePreviewToken = token;
            clearPreviewTimer();
            if (activeObjectUrl) {
                URL.revokeObjectURL(activeObjectUrl);
                activeObjectUrl = '';
            }
            activeFrameUrl = buildPreviewUrl(payload.fileId);
            frame.onload = () => completePreviewLoad(token);
            frame.classList.remove('visible');
            frame.removeAttribute('src');
            requestAnimationFrame(() => {
                if (token !== activePreviewToken) return;
                frame.src = activeFrameUrl;
                activePreviewTimer = setTimeout(() => completePreviewLoad(token), PREVIEW_LOAD_TIMEOUT_MS);
            });
        } else {
            setStatus(t('latex_pdf_ready', 'Ready'), 'ready');
            setDownloadControls(Boolean(payload.fileId));
            setActionsEnabled(true);
            setLoading('', false);
        }
    }

    async function downloadActivePdf() {
        if (!active) return;
        const fileId = active.fileId;
        const fileName = active.fileName;
        if (!fileId) {
            throw new Error(t('latex_pdf_missing_file_id', 'PDF file id is missing.'));
        }

        setDownloadBusy(true);
        try {
            await downloadFileBlob(fileId, fileName);
        } finally {
            setDownloadBusy(false);
        }
    }

    function handleLatexPdfEvent(obj, messageId) {
        const event = String(obj?.event || '').toLowerCase();
        const data = obj?.data && typeof obj.data === 'object' ? obj.data : {};
        if (event === 'status') {
            if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.showLatexPdfStatus === 'function') {
                window.canvasMarkdownWidget.showLatexPdfStatus(data);
                addGeneratingCard(messageId, data);
                return;
            }
            clearFrame();
            active = null;
            addGeneratingCard(messageId, data);
            if (titleEl) titleEl.textContent = data.title || t('latex_pdf_default_title', 'LaTeX PDF');
            setStatus(data.message || t('latex_pdf_compiling', 'Compiling'), 'compiling');
            setDownloadControls(false);
            setActionsEnabled(false);
            setLoading(data.message || t('latex_pdf_compiling_pdf', 'Compiling LaTeX PDF...'), true);
            showPanel();
        } else if (event === 'complete') {
            addCompletionCard(messageId, data);
            openPdfPreview(data).catch((error) => {
                console.error('[latex-pdf] Failed to open completed PDF preview', error);
            });
        } else if (event === 'error') {
            clearFrame();
            active = null;
            addErrorCard(messageId, data);
            setStatus(t('latex_pdf_error', 'Error'), 'error');
            setDownloadControls(false);
            setActionsEnabled(false);
            setLoading(data.message || t('latex_pdf_compile_failed', 'LaTeX compile failed'), true);
        }
    }

    function renderLatexPdfResultBlock(messageId, meta) {
        if (!meta) return;
        addCompletionCard(messageId, meta);
    }

    if (closeBtn) closeBtn.addEventListener('click', hidePanel);
    if (downloadBtn) {
        // Ignore clicks while the download action is in the disabled state.
        downloadBtn.addEventListener('click', (e) => {
            e.preventDefault();
            if (downloadBtn.classList.contains('disabled')) {
                return;
            }
            downloadActivePdf().catch((error) => {
                console.error('[latex-pdf] Download failed', error);
                if (typeof window.notifyError === 'function') {
                    window.notifyError(error?.message || t('latex_pdf_download_failed', 'Failed to download PDF.'));
                }
            });
        });
    }
    window.latexPdfWidget = {
        handleLatexPdfEvent,
        renderLatexPdfResultBlock,
        openPdfPreview,
        isLatexPdfFile,
        hidePanel,
        hidePreviewPanel: hidePanel,
        reset: hidePanel,
    };
})();
