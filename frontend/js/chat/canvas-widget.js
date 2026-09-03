/* ==========================================================================
   Canvas Widget + Preview Panel
   Supports: markdown, mermaid, csv, html, LaTeX, PDF
   ========================================================================== */

(function () {
    'use strict';

    const canvasWidgetModules = globalThis.__omlorixCanvasWidgetModules;
    if (!canvasWidgetModules) throw new Error('Canvas widget modules must load before canvas-widget.js');
    const { ensureCanvasPreviewHeader, getPreviewHeaderIcon } = canvasWidgetModules.header;

    const previewPanel = document.getElementById('canvas-markdown-PreviewPanel');
    ensureCanvasPreviewHeader(previewPanel);
    const previewResizer = document.getElementById('canvas-markdown-PreviewResizer');
    const previewClose = document.getElementById('canvas-markdown-PreviewClose');
    const previewTitle = document.getElementById('canvas-markdown-PreviewTitle');
    const previewStatus = document.getElementById('canvas-markdown-PreviewStatus');
    const previewDownload = document.getElementById('canvas-markdown-PreviewDownload');
    const previewTrack = document.getElementById('canvas-markdown-PreviewTrack');
    const previewShareBtn = document.getElementById('canvas-markdown-ShareBtn');
    const previewCopyBtn = document.getElementById('canvas-markdown-CopyBtn');
    const htmlSettings = document.getElementById('canvas-html-Settings');
    const htmlSettingsBtn = document.getElementById('canvas-html-SettingsBtn');
    const htmlSettingsMenu = document.getElementById('canvas-html-SettingsMenu');
    const htmlScriptsBtn = document.getElementById('canvas-html-ScriptsBtn');
    const htmlExternalContentBtn = document.getElementById('canvas-html-ExternalContentBtn');
    const htmlReloadBtn = document.getElementById('canvas-html-ReloadBtn');
    const previewSaveBtn = document.getElementById('canvas-markdown-SaveBtn');
    const previewRevertBtn = document.getElementById('canvas-markdown-RevertBtn');
    const previewDownloadFormat = document.getElementById('canvas-markdown-DownloadFormat');
    const previewDownloadControls = previewDownloadFormat?.closest('.slide-presentation-preview-download-controls');
    let previewDownloadDefaultHtml = '';
    const markdownEditorControls = document.getElementById('canvas-markdown-EditorControls');
    const markdownEditorMarkdownTab = document.getElementById('canvas-markdown-MarkdownTab');
    const markdownEditorEditorTab = document.getElementById('canvas-markdown-EditorTab');
    const { escapeHtml, updateStatusClass } = canvasWidgetModules.status.create({ previewStatus });

    // Replace the browser-native format popup with the shared split-button
    // menu while retaining the select as the download handler's source of
    // truth. This also keeps dynamic Markdown/LaTeX option changes simple.
    window.chatDownloadControls?.enhanceDownloadFormatSelect?.(previewDownloadFormat, {
        downloadButton: previewDownload,
    });

    // Enable/disable the preview download button using the shared
    // chatDownloadControls helper. The button is an <a> styled with the shared
    // slide-presentation download classes, so it is toggled via the `disabled`
    // class while keeping aria-disabled and tabindex in sync for accessibility.
    function setPreviewDownloadEnabled(enabled) {
        if (!previewDownload) return;
        if (window.chatDownloadControls?.setDownloadControlsEnabled) {
            window.chatDownloadControls.setDownloadControlsEnabled({
                button: previewDownload,
                enabled: Boolean(enabled),
                disabledClass: 'disabled',
                manageTabIndex: true,
            });
        } else {
            // Fallback if the shared helper is unavailable.
            previewDownload.classList.toggle('disabled', !enabled);
            previewDownload.setAttribute('aria-disabled', enabled ? 'false' : 'true');
            previewDownload.tabIndex = enabled ? 0 : -1;
        }
    }

    function setPreviewDownloadBusy(isBusy, enabled = true) {
        if (!previewDownload) return;
        if (!previewDownloadDefaultHtml) {
            previewDownloadDefaultHtml = previewDownload.innerHTML;
        }
        if (window.chatDownloadControls?.setDownloadBusy) {
            window.chatDownloadControls.setDownloadBusy({
                button: previewDownload,
                select: previewDownloadFormat,
                busy: Boolean(isBusy),
                enabled: Boolean(enabled),
                defaultHtml: previewDownloadDefaultHtml,
                disabledClass: 'disabled',
                manageTabIndex: true,
                busyLabel: t('canvas_download_preparing', 'Preparing download...'),
                idleLabel: t('files_preview_download', 'Download'),
            });
            return;
        }
        setPreviewDownloadEnabled(!isBusy && enabled);
    }

    function setPreviewDownloadFormatOptions(
        contentType,
        enabled,
        { sourceAvailable = true, pdfAvailable = true } = {},
    ) {
        if (!previewDownloadFormat) return;
        const normalizedType = normalizeContentType(contentType);
        // Mermaid source files, like rendered PDFs, have exactly one download
        // representation. Use the direct action so the header does not show an
        // empty format selector beside the download button.
        const usesDirectDownload = normalizedType === 'mermaid'
            || normalizedType === 'pdf';

        // Mark single-format controls as direct actions so the shared
        // split-button enhancer cannot leave a trigger or divider in the
        // header.
        previewDownloadControls?.classList.toggle('is-direct-download', usesDirectDownload);
        if (usesDirectDownload) {
            window.chatDownloadControls?.closeOpenFormatMenu?.();
        }
        const optionSets = {
            markdown: [
                { value: 'md', key: 'canvas_markdown_download_md', fallback: 'MD' },
                { value: 'pdf', key: 'canvas_markdown_download_pdf', fallback: 'PDF' },
            ],
            html: [
                { value: 'html', key: 'canvas_html_download_html', fallback: 'HTML' },
                { value: 'png', key: 'canvas_html_download_png', fallback: 'PNG image' },
            ],
            latex: [
                { value: 'tex', key: 'latex_pdf_download_tex', fallback: 'TeX source', enabled: sourceAvailable },
                { value: 'pdf', key: 'latex_pdf_download_pdf', fallback: 'PDF', enabled: pdfAvailable },
            ],
            csv: [
                { value: 'csv', key: 'canvas_spreadsheet_download_csv', fallback: 'CSV' },
                { value: 'tsv', key: 'canvas_spreadsheet_download_tsv', fallback: 'TSV' },
                { value: 'xlsx', key: 'canvas_spreadsheet_download_xlsx', fallback: 'Excel' },
            ],
            tsv: [
                { value: 'tsv', key: 'canvas_spreadsheet_download_tsv', fallback: 'TSV' },
                { value: 'csv', key: 'canvas_spreadsheet_download_csv', fallback: 'CSV' },
                { value: 'xlsx', key: 'canvas_spreadsheet_download_xlsx', fallback: 'Excel' },
            ],
            xlsx: [
                { value: 'xlsx', key: 'canvas_spreadsheet_download_xlsx', fallback: 'Excel' },
                { value: 'csv', key: 'canvas_spreadsheet_download_csv_current_sheet', fallback: 'CSV (current sheet)' },
                { value: 'tsv', key: 'canvas_spreadsheet_download_tsv_current_sheet', fallback: 'TSV (current sheet)' },
            ],
            xls: [
                { value: 'xlsx', key: 'canvas_spreadsheet_download_xlsx', fallback: 'Excel' },
                { value: 'csv', key: 'canvas_spreadsheet_download_csv_current_sheet', fallback: 'CSV (current sheet)' },
                { value: 'tsv', key: 'canvas_spreadsheet_download_tsv_current_sheet', fallback: 'TSV (current sheet)' },
            ],
        };
        const options = optionSets[normalizedType] || [];
        const hasOptions = options.length > 0;
        const previousValue = String(previewDownloadFormat.value || '');

        if (previewDownloadFormat.dataset.contentType !== normalizedType) {
            previewDownloadFormat.innerHTML = '';
            options.forEach((option) => {
                const optionEl = document.createElement('option');
                optionEl.value = option.value;
                optionEl.textContent = t(option.key, option.fallback);
                optionEl.setAttribute('data-i18n', option.key);
                optionEl.disabled = option.enabled === false;
                previewDownloadFormat.appendChild(optionEl);
            });
            previewDownloadFormat.dataset.contentType = normalizedType;
        } else {
            // LaTeX availability changes without changing artifact type: the
            // source remains downloadable while PDF becomes available only
            // after the current revision has rendered successfully.
            options.forEach((option) => {
                const optionEl = Array.from(previewDownloadFormat.options)
                    .find((candidate) => candidate.value === option.value);
                if (optionEl) optionEl.disabled = option.enabled === false;
            });
        }

        const allowedValues = new Set(options.map((option) => option.value));
        const selectedOption = Array.from(previewDownloadFormat.options)
            .find((option) => option.value === previousValue);
        if (!allowedValues.has(previousValue) || selectedOption?.disabled) {
            previewDownloadFormat.value = Array.from(previewDownloadFormat.options)
                .find((option) => !option.disabled)?.value || '';
        }

        previewDownloadFormat.hidden = !hasOptions;
        const hasEnabledOption = Array.from(previewDownloadFormat.options)
            .some((option) => !option.disabled);
        previewDownloadFormat.disabled = !hasOptions || !hasEnabledOption || !enabled;
        window.chatDownloadControls?.syncDownloadFormatSelect?.(previewDownloadFormat);
    }

    /** Return true only when the stored PDF represents the visible source. */
    function hasCurrentLatexPdf(draft, editState = null) {
        if (!draft?.pdfFileId || draft.renderStatus !== 'ready' || editState?.dirty) return false;
        return Number(draft.renderRevision) === Number(draft.canvasRevision);
    }

    const shareOverlay = document.getElementById('canvas-artifact-ShareOverlay');
    const shareCloseBtn = document.getElementById('canvas-artifact-ShareCloseBtn');
    const shareFileName = document.getElementById('canvas-artifact-ShareFileName');
    const shareLinksSection = document.getElementById('canvas-artifact-ShareLinksSection');
    const shareEmptySection = document.getElementById('canvas-artifact-ShareEmptySection');
    const shareForm = document.getElementById('canvas-artifact-ShareForm');
    const shareFormTitle = document.getElementById('canvas-artifact-ShareFormTitle');
    const sharePasswordToggle = document.getElementById('canvas-artifact-SharePasswordToggle');
    const sharePasswordContent = document.getElementById('canvas-artifact-SharePasswordContent');
    const sharePasswordInput = document.getElementById('canvas-artifact-SharePasswordInput');
    const sharePasswordHelper = document.getElementById('canvas-artifact-SharePasswordHelper');
    const sharePasswordError = document.getElementById('canvas-artifact-SharePasswordError');
    const shareExpiryToggle = document.getElementById('canvas-artifact-ShareExpiryToggle');
    const shareExpiryContent = document.getElementById('canvas-artifact-ShareExpiryContent');
    const shareExpiryInput = document.getElementById('canvas-artifact-ShareExpiryInput');
    const shareExpiryError = document.getElementById('canvas-artifact-ShareExpiryError');
    const shareNotice = document.getElementById('canvas-artifact-ShareNotice');
    const sharePrimaryBtn = document.getElementById('canvas-artifact-SharePrimaryBtn');
    const shareSecondaryBtn = document.getElementById('canvas-artifact-ShareSecondaryBtn');
    const shareLinksList = document.getElementById('canvas-artifact-ShareLinksList');
    const shareModal = shareOverlay?.querySelector('.cs-modal');
    const htmlExternalResourceOverlay = document.getElementById('canvas-html-ExternalResourcesOverlay');
    const htmlExternalResourceList = document.getElementById('canvas-html-ExternalResourcesList');
    const htmlExternalResourceAllowBtn = document.getElementById('canvas-html-ExternalResourcesAllowBtn');
    const htmlExternalResourceDenyBtn = document.getElementById('canvas-html-ExternalResourcesDenyBtn');

    const chatArea = document.getElementById('chatAreaContainer');

    let previewVisible = false;
    let activeDraftKey = '';
    // Tracks argument deltas independently from the draft displayed in the
    // sidebar. A read-only view call must not become the active preview draft.
    let activeCanvasToolCallKey = '';
    // Split-screen can stream two generations concurrently. Keep each
    // message's in-flight Canvas draft separate so a saved event from one
    // panel cannot migrate or finalize the other panel's draft.
    const canvasToolCallKeysByMessage = new Map();
    // Keep a bounded record of terminal tool calls. Provider adapters may
    // deliver a finalized t_c/t_cd after the Canvas saved event; without this
    // guard that late packet recreates the draft with a "Writing" status.
    const terminalCanvasToolCallKeys = new Set();
    const MAX_TERMINAL_CANVAS_TOOL_CALL_KEYS = 256;
    const draftMap = new Map();
    const draftScrollStates = new Map();
    let suppressUserScrollEvents = false;
    const canvasFileIds = new Set();
    let lastActiveMessageId = '';
    let sharingAllowedByGroup = true;
    let activeFileContext = null;
    let shareModalOpen = false;
    let shareModalReturnFocus = null;
    let currentShareLinks = [];
    let shareLinksRefreshToken = 0;
    let shareMode = 'list';
    let activeShareLink = null;
    let shareBusy = false;
    let copyFeedbackTimer = null;
    let lastCopyContextLabel = '';
    const draftEditStateMap = new Map();
    // HTML capability grants are intentionally session-scoped. They never
    // become part of a saved artifact or share payload, and can always be
    // revoked from the visible preview controls.
    const htmlPreviewPermissionMap = new Map();
    const htmlExternalResourcePromptTimers = new Map();
    let pendingHtmlExternalResourceConsent = null;
    let htmlExternalResourceModalReturnFocus = null;
    const previewRenderTimers = new Map();
    // A source can be saved again while a slow compiler request is in flight.
    // Monotonic client tokens ensure only the newest response may update the
    // visible PDF, complementing the backend's persisted revision check.
    const latexRenderRequestTokens = new Map();
    const autoSaveTimers = new Map();
    // Preview can be requested while an autosave is already in flight. Keep
    // the promise per draft so the tab switch can await that exact save
    // instead of starting a duplicate request or compiling an older revision.
    const draftSavePromises = new Map();
    // Every file open receives a unique token. Together with activeDraftKey,
    // this prevents a slow response from an older file (or an older reopen of
    // the same file) from replacing the preview the user selected afterward.
    const filePreviewLoadTokens = new Map();
    let activeMarkdownEditorInstance = null;
    let activeSpreadsheetEditorInstance = null;
    let activeSpreadsheetEditorDraftKey = '';
    let spreadsheetRenderToken = 0;
    let draftLifecycleGeneration = 0;
    // Markdown tool arguments can arrive in very small chunks. Keep one
    // lightweight, read-only preview alive for the whole stream so completed
    // blocks are not destroyed and recreated every time another token lands.
    let activeStreamingMarkdownPreview = null;
    let markdownStreamRenderTimer = null;
    let pendingMarkdownStreamDraft = null;
    let markdownStreamLastRenderAt = 0;
    // Model edits use a transient tool-call key before the saved file id is
    // emitted. Hold the visible file's viewport here so that key transition
    // cannot reset the replacement preview to the top.
    let pendingCanvasToolScrollSnapshot = null;
    let activeReferenceSelection = null;
    let referenceToolbarEl = null;
    let referenceToolbarController = null;

    const CONTENT_TYPES = ['markdown', 'mermaid', 'csv', 'tsv', 'xlsx', 'xls', 'html', 'latex', 'pdf'];
    const SPREADSHEET_CONTENT_TYPES = new Set(['csv', 'tsv', 'xlsx', 'xls']);
    const TYPE_LABELS = { markdown: 'Markdown', mermaid: 'Mermaid Diagram', csv: 'CSV Table', tsv: 'TSV Table', xlsx: 'Excel Workbook', xls: 'Excel Workbook', html: 'HTML Website', latex: 'LaTeX Document', pdf: 'PDF' };
    const DEFAULT_NAMES_BY_TYPE = { markdown: 'canvas.md', mermaid: 'diagram.mmd', csv: 'data.csv', tsv: 'data.tsv', xlsx: 'workbook.xlsx', xls: 'workbook.xls', html: 'website.html', latex: 'document.tex', pdf: 'document.pdf' };
    const TYPE_LABEL_KEYS = {
        markdown: 'canvas_type_markdown',
        mermaid: 'canvas_type_mermaid_diagram',
        csv: 'canvas_type_csv_table',
        tsv: 'canvas_type_tsv_table',
        xlsx: 'canvas_type_excel_workbook',
        xls: 'canvas_type_excel_workbook',
        html: 'canvas_type_html_website',
        latex: 'canvas_type_latex_document',
        pdf: 'latex_pdf_download_pdf',
    };
    const HTML_FILE_MIME_TYPES = new Set([
        'text/html',
        'application/html',
        'application/xhtml+xml',
        'application/x-html',
        'text/xhtml',
    ]);
    const HTML_FILE_SUFFIXES = ['.html', '.htm', '.xhtml', '.xht', '.xhtm', '.shtml', '.shtm'];
    const CANVAS_FILE_MIME_TYPES = new Set([
        'text/markdown',
        'text/x-markdown',
        'text/plain',
        'text/csv',
        'text/tab-separated-values',
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'text/x-mermaid',
        'text/x-tex',
        'text/x-latex',
        'application/x-latex',
        ...HTML_FILE_MIME_TYPES,
    ]);
    const DEFAULT_CANVAS_FILENAMES = ['canvas.md', 'diagram.mmd', 'data.csv', 'data.tsv', 'workbook.xlsx', 'workbook.xls', 'website.html', 'document.tex'];
    const SHAREABLE_FILE_SUFFIXES = ['.md', '.markdown', '.mmd', '.mermaid', ...HTML_FILE_SUFFIXES, '.css', '.pdf'];

    const COPY_LABEL_DEFAULT = 'Copy raw code';
    const COPY_LABEL_UNAVAILABLE = 'Copy unavailable';
    const COPY_LABEL_SUCCESS = 'Copied!';
    const COPY_LABEL_FAILURE = 'Copy failed';

    let currentHtmlViewMode = 'preview';
    let htmlPreviewAvailable = true;

    let renderDebounceTimer = null;
    let pendingHtmlRenderDraft = null;
    const RENDER_DEBOUNCE_MS = 50;
    const MARKDOWN_STREAM_RENDER_INTERVAL_MS = 100;
    const AUTO_SAVE_DELAY_MS = 450;
    const PREVIEW_WIDTH_STORAGE_KEY = 'omlorix.canvasMarkdownPreviewWidthRatio';
    const PREVIEW_DEFAULT_WIDTH_RATIO = 0.5;
    const PREVIEW_MIN_PANEL_WIDTH = 420;
    const PREVIEW_MIN_MAIN_WIDTH = 360;
    const PREVIEW_RESIZE_KEYBOARD_STEP = 32;
    const PREVIEW_RESIZE_KEYBOARD_LARGE_STEP = 96;

    const BUTTON_LABEL_OPEN = 'Open Canvas';
    const BUTTON_LABEL_CLOSE = 'Close Canvas';

    let canvasPreviewWidthRatio = readStoredPreviewWidthRatio();
    let previewResizeActive = false;

    function t(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function formatT(key, fallback, vars) {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars?.[token];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    function getTypeLabel(contentType, fallback = 'Canvas') {
        const normalized = normalizeContentType(contentType);
        const label = TYPE_LABELS[normalized] || fallback;
        const key = TYPE_LABEL_KEYS[normalized] || 'canvas_type_canvas';
        return t(key, label);
    }

    function getContentLabel(contentType) {
        const normalized = normalizeContentType(contentType);
        if (TYPE_LABELS[normalized]) return getTypeLabel(normalized);
        return t('canvas_type_content', 'content');
    }

    function getButtonLabel(isOpen) {
        return isOpen ? t('canvas_close_canvas', BUTTON_LABEL_CLOSE) : t('canvas_open_canvas', BUTTON_LABEL_OPEN);
    }

    function getRawCodeLabel() {
        return t('canvas_copy_raw_code_aria', COPY_LABEL_DEFAULT);
    }

    function setMarkdownEditorHeaderButtonDisabled(button, disabled) {
        if (!button) return;
        button.disabled = Boolean(disabled);
        button.setAttribute('aria-disabled', disabled ? 'true' : 'false');
        button.classList.toggle('is-disabled', Boolean(disabled));
    }

    function updateMarkdownEditorHeaderControls(state = null) {
        const isMarkdownDraft = previewPanel?.dataset.contentType === 'markdown';
        const hasEditor = Boolean(activeMarkdownEditorInstance);
        const normalizedView = state?.view === 'source' ? 'source' : 'editor';
        const controlsDisabled = !isMarkdownDraft || !hasEditor;

        if (markdownEditorControls) {
            markdownEditorControls.setAttribute('aria-disabled', controlsDisabled ? 'true' : 'false');
        }

        if (markdownEditorMarkdownTab) {
            markdownEditorMarkdownTab.classList.toggle('active', normalizedView === 'source');
            markdownEditorMarkdownTab.setAttribute('aria-selected', normalizedView === 'source' ? 'true' : 'false');
            setMarkdownEditorHeaderButtonDisabled(markdownEditorMarkdownTab, controlsDisabled);
        }
        if (markdownEditorEditorTab) {
            markdownEditorEditorTab.classList.toggle('active', normalizedView === 'editor');
            markdownEditorEditorTab.setAttribute('aria-selected', normalizedView === 'editor' ? 'true' : 'false');
            setMarkdownEditorHeaderButtonDisabled(markdownEditorEditorTab, controlsDisabled);
        }
    }

    function isLikelyCanvasFile(meta = {}, fileType = '', fileName = '') {
        if (!meta && !fileType && !fileName) return false;
        if (meta && meta.canvas === true) return true;
        const origin = String(meta?.origin || '').toLowerCase();
        const canvasType = String(meta?.canvas_type || '').toLowerCase();
        if (canvasType && (CONTENT_TYPES.includes(canvasType) || canvasType === 'spreadsheet')) return true;
        const effectiveType = String(fileType || meta?.mime_type || meta?.file_type || '').toLowerCase();
        const typeIsCanvas = CANVAS_FILE_MIME_TYPES.has(effectiveType);
        const originalName = String(fileName || meta?.original_filename || '').toLowerCase();
        const looksLikeDefaultName = originalName && DEFAULT_CANVAS_FILENAMES.some((name) => originalName.endsWith(name));
        if (typeIsCanvas && origin === 'assistant') return true;
        if (typeIsCanvas && looksLikeDefaultName) return true;
        if (origin === 'assistant' && looksLikeDefaultName) return true;
        return false;
    }

    function getWidgetKeyFromElement(widget) {
        if (!widget || !widget.dataset) return '';
        return widget.dataset.canvasFileId || widget.dataset.canvasDraftKey || '';
    }

    function updateOpenButtonLabel(button, isOpen) {
        if (!button) return;
        const labelEl = button.querySelector('.canvas-markdown-result-open-label');
        const targetText = getButtonLabel(isOpen);
        if (labelEl) labelEl.textContent = targetText;
        button.setAttribute('aria-pressed', isOpen ? 'true' : 'false');
    }

    function refreshWidgetOpenButtonStates() {
        const widgets = document.querySelectorAll('.canvas-markdown-result-widget');
        widgets.forEach((widget) => {
            // Several artifact cards intentionally reuse Canvas result-card
            // styling. Only cards carrying a Canvas file/draft identity belong
            // to this controller; touching skill or note buttons would replace
            // or append their labels with the generic "Open Canvas" text.
            const widgetKey = getWidgetKeyFromElement(widget);
            if (!widgetKey) return;
            const button = widget.querySelector('.canvas-markdown-result-open-btn');
            if (!button) return;
            const isOpen = Boolean(previewVisible && widgetKey && widgetKey === activeDraftKey);
            button.classList.toggle('is-open', isOpen);
            updateOpenButtonLabel(button, isOpen);
        });
    }

    function normalizeName(name) {
        return String(name || '').trim().toLowerCase();
    }

    function isCanvasToolName(name) {
        return normalizeName(name) === 'canvas';
    }

    function markCanvasToolCallTerminal(rawKey) {
        const key = String(rawKey || '').trim();
        if (!key) return;
        terminalCanvasToolCallKeys.delete(key);
        terminalCanvasToolCallKeys.add(key);
        while (terminalCanvasToolCallKeys.size > MAX_TERMINAL_CANVAS_TOOL_CALL_KEYS) {
            terminalCanvasToolCallKeys.delete(terminalCanvasToolCallKeys.values().next().value);
        }
    }

    function isCanvasToolCallTerminal(rawKey) {
        const key = String(rawKey || '').trim();
        return Boolean(key && terminalCanvasToolCallKeys.has(key));
    }

    /** Track every Canvas call because providers may execute calls in parallel. */
    function trackCanvasToolCallForMessage(rawMessageId, rawDraftKey) {
        const messageId = String(rawMessageId || '').trim();
        const draftKey = String(rawDraftKey || '').trim();
        if (!messageId || !draftKey) return;
        const keys = canvasToolCallKeysByMessage.get(messageId) || new Set();
        keys.delete(draftKey);
        keys.add(draftKey);
        canvasToolCallKeysByMessage.set(messageId, keys);
    }

    function getLatestCanvasToolCallForMessage(rawMessageId) {
        const messageId = String(rawMessageId || '').trim();
        const keys = canvasToolCallKeysByMessage.get(messageId);
        if (!keys?.size) return '';
        const orderedKeys = Array.from(keys);
        return String(orderedKeys[orderedKeys.length - 1] || '');
    }

    function forgetCanvasToolCallForMessage(rawMessageId, rawDraftKey) {
        const messageId = String(rawMessageId || '').trim();
        const draftKey = String(rawDraftKey || '').trim();
        const keys = canvasToolCallKeysByMessage.get(messageId);
        if (!keys) return;
        if (draftKey) keys.delete(draftKey);
        else keys.clear();
        if (!keys.size) canvasToolCallKeysByMessage.delete(messageId);
    }

    function normalizeContentType(type) {
        const t = String(type || 'markdown').toLowerCase().trim();
        return CONTENT_TYPES.includes(t) ? t : 'markdown';
    }

    function getDefaultCanvasFileName(contentType) {
        return DEFAULT_NAMES_BY_TYPE[normalizeContentType(contentType)] || DEFAULT_NAMES_BY_TYPE.markdown;
    }

    function resolveDisplayCanvasFileName(fileName, contentType) {
        const trimmed = String(fileName || '').trim();
        return trimmed || getDefaultCanvasFileName(contentType);
    }

    function hasExplicitCanvasContentType(rawArgs) {
        if (!rawArgs) return false;
        if (typeof rawArgs === 'object') {
            return Object.prototype.hasOwnProperty.call(rawArgs, 'type')
                || Object.prototype.hasOwnProperty.call(rawArgs, 'content_type');
        }
        return /"(?:type|content_type)"\s*:/.test(String(rawArgs));
    }

    function parseBooleanValue(value, fallback = true) {
        if (value === null || typeof value === 'undefined') return fallback;
        if (typeof value === 'boolean') return value;
        const normalized = String(value).trim().toLowerCase();
        if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
        if (['false', '0', 'no', 'off'].includes(normalized)) return false;
        return fallback;
    }

    /** Return whether a filename uses a static HTML-family extension. */
    function hasHtmlFileExtension(fileName) {
        const normalizedName = String(fileName || '').trim().toLowerCase();
        return HTML_FILE_SUFFIXES.some((suffix) => normalizedName.endsWith(suffix));
    }

    function setSharingFlagFromSetup(setup) {
        const enabled = parseBooleanValue(setup?.enable_artifact_sharing, true);
        sharingAllowedByGroup = enabled !== false;
        updateShareButtonState();
        void refreshExistingShareLinksForButton();
    }

    function getCanonicalContentType(fileName, contentType) {
        const normalizedType = String(contentType || '').trim().toLowerCase();
        if (normalizedType === 'html' || HTML_FILE_MIME_TYPES.has(normalizedType)) return 'html';
        if (normalizedType === 'markdown') return 'markdown';
        if (normalizedType === 'mermaid') return 'mermaid';
        if (normalizedType === 'css') return 'css';
        if (normalizedType === 'pdf' || normalizedType === 'application/pdf') return 'pdf';
        if (normalizedType === 'latex' || normalizedType === 'text/x-tex' || normalizedType === 'text/x-latex' || normalizedType === 'application/x-latex') return 'latex';
        const name = String(fileName || '').toLowerCase();
        if (hasHtmlFileExtension(name)) return 'html';
        if (name.endsWith('.css')) return 'css';
        if (name.endsWith('.mmd') || name.endsWith('.mermaid')) return 'mermaid';
        if (name.endsWith('.pdf')) return 'pdf';
        if (name.endsWith('.tex')) return 'latex';
        if (name.endsWith('.md') || name.endsWith('.markdown')) return 'markdown';
        return normalizedType || '';
    }

    function isShareableFileContext(context) {
        if (!context || !context.fileId) return false;
        const canonicalType = getCanonicalContentType(context.fileName, context.contentType);
        if (canonicalType === 'markdown' || canonicalType === 'mermaid' || canonicalType === 'html' || canonicalType === 'css' || canonicalType === 'pdf') return true;
        const fileName = String(context.fileName || '').toLowerCase();
        return SHAREABLE_FILE_SUFFIXES.some((suffix) => fileName.endsWith(suffix));
    }

    function setActiveFileContext(fileId, fileName, contentType) {
        if (!fileId) {
            activeFileContext = null;
            currentShareLinks = [];
            shareLinksRefreshToken += 1;
            updateShareButtonState();
            return;
        }
        activeFileContext = {
            fileId: String(fileId),
            fileName: String(fileName || ''),
            contentType: String(contentType || ''),
        };
        currentShareLinks = [];
        shareLinksRefreshToken += 1;
        updateShareButtonState();
        void refreshExistingShareLinksForButton();
    }

    function notifyShareSuccess(message) {
        if (typeof window.notifySuccess === 'function') {
            window.notifySuccess(message);
            return;
        }
        if (typeof showNotification === 'function') {
            showNotification(message, 'success');
        }
    }

    function notifyShareError(message) {
        if (typeof window.notifyError === 'function') {
            window.notifyError(message);
            return;
        }
        if (typeof showNotification === 'function') {
            showNotification(message, 'error');
        }
    }

    function setCopyButtonLabel(label) {
        if (!previewCopyBtn) return;
        const nextLabel = label || getRawCodeLabel();
        previewCopyBtn.setAttribute('aria-label', nextLabel);
        previewCopyBtn.title = nextLabel;
    }

    function clearCopyButtonFeedback() {
        if (!previewCopyBtn) return;
        if (copyFeedbackTimer) {
            clearTimeout(copyFeedbackTimer);
            copyFeedbackTimer = null;
        }
        previewCopyBtn.removeAttribute('data-copy-state');
    }

    function buildCopyContextLabel(fileName, contentType) {
        const trimmedName = String(fileName || '').trim();
        if (trimmedName) return trimmedName;
        const normalizedType = normalizeContentType(contentType);
        const typeLabel = TYPE_LABELS[normalizedType] ? getTypeLabel(normalizedType) : '';
        return typeLabel ? formatT('canvas_source_label', '{type} source', { type: typeLabel }) : t('canvas_source_raw_code', 'raw code');
    }

    function updateCopyButtonState(rawContent = '', contextLabel = '') {
        if (!previewCopyBtn) return;
        const hasContent = typeof rawContent === 'string' && rawContent.length > 0;
        previewCopyBtn.disabled = !hasContent;
        previewCopyBtn.classList.toggle('is-disabled', !hasContent);
        previewCopyBtn.setAttribute('aria-disabled', hasContent ? 'false' : 'true');
        previewCopyBtn._rawContent = hasContent ? rawContent : '';
        if (!hasContent) {
            lastCopyContextLabel = '';
            clearCopyButtonFeedback();
            setCopyButtonLabel(t('canvas_copy_unavailable', COPY_LABEL_UNAVAILABLE));
            return;
        }
        const label = contextLabel ? formatT('canvas_copy_context_aria', 'Copy {context}', { context: contextLabel }) : getRawCodeLabel();
        lastCopyContextLabel = label;
        if (!previewCopyBtn.dataset.copyState) {
            setCopyButtonLabel(label);
        }
    }

    const { hasAdjacentChatComposer, hideReferenceToolbar, setReferenceToolbarState, refreshReferenceSelectionState, addMarkedSelectionAsReference } = canvasWidgetModules.referenceSelection.create({
        SPREADSHEET_CONTENT_TYPES, draftMap, getTypeLabel, normalizeContentType,
        previewPanel, previewTrack, resolveDisplayCanvasFileName, t,
    }, {
        get activeReferenceSelection() { return activeReferenceSelection; },
        set activeReferenceSelection(value) { activeReferenceSelection = value; },
        get referenceToolbarEl() { return referenceToolbarEl; },
        set referenceToolbarEl(value) { referenceToolbarEl = value; },
        get referenceToolbarController() { return referenceToolbarController; },
        set referenceToolbarController(value) { referenceToolbarController = value; },
        get activeDraftKey() { return activeDraftKey; },
        set activeDraftKey(value) { activeDraftKey = value; },
        get previewVisible() { return previewVisible; },
        set previewVisible(value) { previewVisible = value; },
    });
    async function copyRawCanvasContent() {
        if (!previewCopyBtn || previewCopyBtn.disabled) return;
        const rawContent = typeof previewCopyBtn._rawContent === 'string' ? previewCopyBtn._rawContent : '';
        if (!rawContent) return;
        try {
            const exportText = typeof window !== 'undefined'
                && typeof window.appendComplianceWatermarkIfNeeded === 'function'
                ? window.appendComplianceWatermarkIfNeeded(rawContent)
                : rawContent;
            await navigator.clipboard.writeText(exportText);
            handleCopySuccess();
        } catch (error) {
            handleCopyFailure(error);
        }
    }

    function handleCopySuccess() {
        if (!previewCopyBtn) return;
        if (copyFeedbackTimer) {
            clearTimeout(copyFeedbackTimer);
        }
        previewCopyBtn.dataset.copyState = 'copied';
        setCopyButtonLabel(t('canvas_copy_success', COPY_LABEL_SUCCESS));
        copyFeedbackTimer = setTimeout(() => {
            previewCopyBtn.removeAttribute('data-copy-state');
            setCopyButtonLabel(lastCopyContextLabel || getRawCodeLabel());
            copyFeedbackTimer = null;
        }, 1500);
    }

    function handleCopyFailure(error) {
        if (!previewCopyBtn) return;
        const copyErrorMessage = t('canvas_copy_output_failed', 'Failed to copy canvas output');
        if (copyFeedbackTimer) {
            clearTimeout(copyFeedbackTimer);
        }
        previewCopyBtn.dataset.copyState = 'error';
        setCopyButtonLabel(t('canvas_copy_failed', COPY_LABEL_FAILURE));
        copyFeedbackTimer = setTimeout(() => {
            previewCopyBtn.removeAttribute('data-copy-state');
            setCopyButtonLabel(lastCopyContextLabel || getRawCodeLabel());
            copyFeedbackTimer = null;
        }, 2000);
        if (typeof window.notifyError === 'function') {
            window.notifyError(copyErrorMessage);
        } else if (typeof showNotification === 'function') {
            showNotification(copyErrorMessage, 'error');
        }
        if (error) {
            console.error(copyErrorMessage, error);
        }
    }

    const {
        updateShareButtonState, requestShareApi, setTranslatedText, toIso,
        toLocalDateTimeValue, formatShareTimestamp, isShareExpired, getDefaultShareExpiryIso,
        getRequiredShareExpiryIso, getShareLinkById, isVisibleElement, getFocusableElements,
        trapFocus, setShareBusy, showShareNotice, showShareControlError,
        hideShareControlError, showSharePasswordError, hideSharePasswordError, showShareExpiryError,
        hideShareExpiryError, runShareWithBusy, renderShareLinkCard, renderShareLinks,
        loadShareLinks, refreshExistingShareLinksForButton, applyShareMode, resetShareFormForCreate,
        populateShareFormFromLink, enterShareCreateMode, enterShareEditMode, enterShareListMode,
        validateShareForm, createShareLink, copyShareUrl, updateShareLink,
        deleteShareLink, openShareModal, openShareDialogForFile, closeShareModal,
    } = canvasWidgetModules.sharing.create({
        escapeHtml, formatT, isShareableFileContext, notifyShareError,
        notifyShareSuccess, previewShareBtn, setActiveFileContext, shareCloseBtn,
        shareEmptySection, shareExpiryContent, shareExpiryError, shareExpiryInput,
        shareExpiryToggle, shareFileName, shareForm, shareFormTitle,
        shareLinksList, shareLinksSection, shareModal, shareNotice,
        shareOverlay, sharePasswordContent, sharePasswordError, sharePasswordHelper,
        sharePasswordInput, sharePasswordToggle, sharePrimaryBtn, shareSecondaryBtn,
        t,
    }, {
        get activeFileContext() { return activeFileContext; },
        set activeFileContext(value) { activeFileContext = value; },
        get sharingAllowedByGroup() { return sharingAllowedByGroup; },
        set sharingAllowedByGroup(value) { sharingAllowedByGroup = value; },
        get shareModalOpen() { return shareModalOpen; },
        set shareModalOpen(value) { shareModalOpen = value; },
        get currentShareLinks() { return currentShareLinks; },
        set currentShareLinks(value) { currentShareLinks = value; },
        get shareLinksRefreshToken() { return shareLinksRefreshToken; },
        set shareLinksRefreshToken(value) { shareLinksRefreshToken = value; },
        get shareMode() { return shareMode; },
        set shareMode(value) { shareMode = value; },
        get activeShareLink() { return activeShareLink; },
        set activeShareLink(value) { activeShareLink = value; },
        get shareBusy() { return shareBusy; },
        set shareBusy(value) { shareBusy = value; },
        get shareModalReturnFocus() { return shareModalReturnFocus; },
        set shareModalReturnFocus(value) { shareModalReturnFocus = value; },
        get previewVisible() { return previewVisible; },
        set previewVisible(value) { previewVisible = value; },
    });
    const {
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
    } = canvasWidgetModules.editorPersistence.create({
        AUTO_SAVE_DELAY_MS, SPREADSHEET_CONTENT_TYPES, autoSaveTimers, canvasFileIds,
        clearHtmlExternalResourcePromptTimer: (...args) => clearHtmlExternalResourcePromptTimer(...args),
        draftEditStateMap, draftMap, draftSavePromises,
        draftScrollStates, formatT, getTypeLabel, hideReferenceToolbar, htmlPreviewPermissionMap,
        latexRenderRequestTokens, normalizeContentType, openPreviewForFile, previewRenderTimers,
        previewRevertBtn, previewSaveBtn, previewStatus, previewTitle,
        previewTrack,
        refreshActiveHtmlDraftAfterSave: (...args) => refreshActiveHtmlDraftAfterSave(...args),
        refreshActiveMarkdownDraftAfterSave: (...args) => refreshActiveMarkdownDraftAfterSave(...args),
        registerCanvasFile,
        renderDraft: (...args) => renderDraft(...args),
        setHtmlViewMode, t, updateDraft,
        updateMarkdownEditorHeaderControls, updateStatusClass,
    }, {
        get activeDraftKey() { return activeDraftKey; },
        set activeDraftKey(value) { activeDraftKey = value; },
        get suppressUserScrollEvents() { return suppressUserScrollEvents; },
        set suppressUserScrollEvents(value) { suppressUserScrollEvents = value; },
        get pendingHtmlExternalResourceConsent() { return pendingHtmlExternalResourceConsent; },
        set pendingHtmlExternalResourceConsent(value) { pendingHtmlExternalResourceConsent = value; },
        get activeMarkdownEditorInstance() { return activeMarkdownEditorInstance; },
        set activeMarkdownEditorInstance(value) { activeMarkdownEditorInstance = value; },
        get activeSpreadsheetEditorInstance() { return activeSpreadsheetEditorInstance; },
        set activeSpreadsheetEditorInstance(value) { activeSpreadsheetEditorInstance = value; },
        get activeSpreadsheetEditorDraftKey() { return activeSpreadsheetEditorDraftKey; },
        set activeSpreadsheetEditorDraftKey(value) { activeSpreadsheetEditorDraftKey = value; },
        get spreadsheetRenderToken() { return spreadsheetRenderToken; },
        set spreadsheetRenderToken(value) { spreadsheetRenderToken = value; },
        get draftLifecycleGeneration() { return draftLifecycleGeneration; },
        set draftLifecycleGeneration(value) { draftLifecycleGeneration = value; },
        get pendingCanvasToolScrollSnapshot() { return pendingCanvasToolScrollSnapshot; },
        set pendingCanvasToolScrollSnapshot(value) { pendingCanvasToolScrollSnapshot = value; },
    });
    const {
        parseJsonSafe,
        extractCanvasArgs,
        readJsonStringField,
        extractCanvasArgsFromBuffer,
        hasCanvasContentArgument,
        classifyCanvasResultKind,
    } = canvasWidgetModules.arguments.create({
        normalizeContentType,
        hasExplicitCanvasContentType,
        contentTypes: CONTENT_TYPES,
    });

    function readStoredPreviewWidthRatio() {
        try {
            const stored = Number(window.localStorage?.getItem(PREVIEW_WIDTH_STORAGE_KEY));
            return Number.isFinite(stored) && stored > 0 ? stored : PREVIEW_DEFAULT_WIDTH_RATIO;
        } catch (_) {
            return PREVIEW_DEFAULT_WIDTH_RATIO;
        }
    }

    function writeStoredPreviewWidthRatio(ratio) {
        try {
            window.localStorage?.setItem(PREVIEW_WIDTH_STORAGE_KEY, String(ratio));
        } catch (_) {}
    }

    // Store preview width as a viewport ratio so the split survives window resizes
    // while still respecting the minimum sizes required by both panes.
    function getViewportWidth() {
        return window.innerWidth || document.documentElement.clientWidth || 0;
    }

    function isDesktopPreviewLayout() {
        return getViewportWidth() > 900;
    }

    function getPreviewWidthBounds() {
        const viewportWidth = Math.max(getViewportWidth(), 1);
        const minWidth = Math.min(PREVIEW_MIN_PANEL_WIDTH, Math.max(1, viewportWidth - PREVIEW_MIN_MAIN_WIDTH));
        const maxWidth = Math.max(minWidth, viewportWidth - PREVIEW_MIN_MAIN_WIDTH);
        return { viewportWidth, minWidth, maxWidth };
    }

    function clampPreviewWidth(width) {
        const { minWidth, maxWidth } = getPreviewWidthBounds();
        return Math.min(Math.max(Number(width) || 0, minWidth), maxWidth);
    }

    function updatePreviewResizerA11y(widthRatio) {
        if (!previewResizer) return;
        const { viewportWidth, minWidth, maxWidth } = getPreviewWidthBounds();
        const minPercent = Math.round((minWidth / viewportWidth) * 100);
        const maxPercent = Math.round((maxWidth / viewportWidth) * 100);
        const currentPercent = Math.round(widthRatio * 100);
        previewResizer.setAttribute('aria-valuemin', String(minPercent));
        previewResizer.setAttribute('aria-valuemax', String(maxPercent));
        previewResizer.setAttribute('aria-valuenow', String(currentPercent));
    }

    function setPreviewWidthFromPixels(width, { persist = false } = {}) {
        const { viewportWidth } = getPreviewWidthBounds();
        const clampedWidth = clampPreviewWidth(width);
        const nextRatio = clampedWidth / viewportWidth;
        const widthValue = `${(nextRatio * 100).toFixed(3)}vw`;
        canvasPreviewWidthRatio = nextRatio;
        document.documentElement.style.setProperty('--canvas-markdown-preview-width', widthValue);
        previewPanel?.style.setProperty('--canvas-markdown-preview-width', widthValue);
        updatePreviewResizerA11y(nextRatio);
        if (persist) writeStoredPreviewWidthRatio(nextRatio);
        return clampedWidth;
    }

    function applyPreviewWidthRatio(ratio = canvasPreviewWidthRatio) {
        if (!isDesktopPreviewLayout()) {
            updatePreviewResizerA11y(canvasPreviewWidthRatio);
            return;
        }
        const { viewportWidth } = getPreviewWidthBounds();
        setPreviewWidthFromPixels(viewportWidth * (Number(ratio) || PREVIEW_DEFAULT_WIDTH_RATIO));
    }

    function resetPreviewWidth({ persist = true } = {}) {
        const { viewportWidth } = getPreviewWidthBounds();
        setPreviewWidthFromPixels(viewportWidth * PREVIEW_DEFAULT_WIDTH_RATIO, { persist });
    }

    function setPreviewWidthFromPointerX(clientX, options = {}) {
        const { viewportWidth } = getPreviewWidthBounds();
        return setPreviewWidthFromPixels(viewportWidth - Number(clientX || 0), options);
    }

    function beginPreviewResize(event) {
        if (!previewResizer || !isDesktopPreviewLayout()) return;
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        event.preventDefault();
        previewResizeActive = true;
        document.body.classList.add('canvas-markdown-preview-resizing');
        previewResizer.setPointerCapture?.(event.pointerId);
        setPreviewWidthFromPointerX(event.clientX);
    }

    function updatePreviewResize(event) {
        if (!previewResizeActive) return;
        event.preventDefault();
        setPreviewWidthFromPointerX(event.clientX);
    }

    function endPreviewResize(event) {
        if (!previewResizeActive) return;
        previewResizeActive = false;
        document.body.classList.remove('canvas-markdown-preview-resizing');
        if (event?.pointerId !== undefined) {
            previewResizer?.releasePointerCapture?.(event.pointerId);
        }
        writeStoredPreviewWidthRatio(canvasPreviewWidthRatio);
    }

    function handlePreviewResizerKeydown(event) {
        if (!isDesktopPreviewLayout()) return;
        const { viewportWidth, minWidth, maxWidth } = getPreviewWidthBounds();
        const currentWidth = clampPreviewWidth(viewportWidth * canvasPreviewWidthRatio);
        const step = event.shiftKey ? PREVIEW_RESIZE_KEYBOARD_LARGE_STEP : PREVIEW_RESIZE_KEYBOARD_STEP;
        let nextWidth = null;

        if (event.key === 'ArrowLeft') {
            nextWidth = currentWidth + step;
        } else if (event.key === 'ArrowRight') {
            nextWidth = currentWidth - step;
        } else if (event.key === 'Home') {
            nextWidth = minWidth;
        } else if (event.key === 'End') {
            nextWidth = maxWidth;
        } else if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            resetPreviewWidth();
            return;
        }

        if (nextWidth === null) return;
        event.preventDefault();
        setPreviewWidthFromPixels(nextWidth, { persist: true });
    }

    let markdownCompactMainLayoutActive = false;

    /**
     * Give the content beside a visible split document preview the same navigation
     * and drawer affordances it would receive on a narrow viewport. The event
     * lets feature-specific drawers reset transient open state when the split
     * layout is entered or left.
     */
    function syncMarkdownCompactMainLayout() {
        const shouldUseCompactLayout = Boolean(
            previewVisible && ['markdown', 'html', 'pdf'].includes(previewPanel?.dataset.contentType)
        );
        if (shouldUseCompactLayout === markdownCompactMainLayoutActive) return;

        markdownCompactMainLayoutActive = shouldUseCompactLayout;
        document.body.classList.toggle(
            'canvas-markdown-compact-main-layout',
            shouldUseCompactLayout
        );
        if (typeof window.setMainSidebarCompactLayout === 'function') {
            window.setMainSidebarCompactLayout('canvas-markdown-preview', shouldUseCompactLayout);
        }
        document.dispatchEvent(new CustomEvent('canvasMarkdownCompactLayoutChange', {
            detail: { active: shouldUseCompactLayout },
        }));
    }
    function setPanelVisible(visible) {
        previewVisible = Boolean(visible);
        if (previewVisible) {
            applyPreviewWidthRatio();
        }
        if (previewPanel) {
            previewPanel.classList.toggle('visible', previewVisible);
            previewPanel.setAttribute('aria-hidden', previewVisible ? 'false' : 'true');
            previewPanel.toggleAttribute('inert', !previewVisible);
        }
        document.body.classList.toggle('canvas-markdown-preview-open', previewVisible);
        if (!previewVisible) {
            // Make the panel non-interactive before cancelling format-specific
            // work. A cleanup failure must never strand the visible sidebar.
            endPreviewResize();
            resetSelectablePdfPreviewRendering();
        }
        if (typeof window.setMainSidebarAutoCollapsed === 'function') {
            // Canvas only borrows the sidebar's horizontal space. The shared
            // controller restores the user's persisted state when Canvas closes.
            window.setMainSidebarAutoCollapsed('canvas-preview', previewVisible);
        } else if (previewVisible && typeof closeSidebar === 'function') {
            // Keep older embedded frontends functional without overwriting the
            // user's preference when the shared controller is unavailable.
            closeSidebar({ persist: false });
        }
        syncMarkdownCompactMainLayout();
        if (previewVisible && typeof window.closeOtherArtifactPreviews === 'function') {
            window.closeOtherArtifactPreviews('canvas-preview');
        }
        updateShareButtonState();
        refreshWidgetOpenButtonStates();
        if (!previewVisible) {
            updateEditorActionButtons(null, null);
        }
        if (!previewVisible) {
            updateCopyButtonState('');
            hideReferenceToolbar();
        } else {
            refreshReferenceSelectionState();
        }
    }

    function resolveCanvasContentType(rawType, fileName) {
        const type = String(rawType || '').toLowerCase().trim();
        const mimeMap = {
            'text/csv': 'csv',
            'application/csv': 'csv',
            'text/tab-separated-values': 'tsv',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
            'application/vnd.ms-excel': 'xls',
            'text/html': 'html',
            'application/html': 'html',
            'application/xhtml+xml': 'html',
            'application/x-html': 'html',
            'text/xhtml': 'html',
            'text/x-mermaid': 'mermaid',
            'text/markdown': 'markdown',
            'text/x-markdown': 'markdown',
            'text/x-tex': 'latex',
            'text/x-latex': 'latex',
            'application/x-latex': 'latex',
            'application/pdf': 'pdf',
            'text/plain': detectContentTypeFromFileName(fileName),
        };
        return normalizeContentType(mimeMap[type] || type || detectContentTypeFromFileName(fileName));
    }

    function inferCanvasContentType({ explicitType = '', currentType = '', fileName = '', content = '' } = {}) {
        const normalizedExplicitType = String(explicitType || '').toLowerCase().trim();
        if (CONTENT_TYPES.includes(normalizedExplicitType)) return normalizedExplicitType;

        const typeFromFileName = detectContentTypeFromFileName(fileName);
        if (fileName && typeFromFileName !== 'markdown') return typeFromFileName;

        const sample = String(content || '').trimStart();
        const lowerSample = sample.toLowerCase();
        if (/^(?:<!doctype\s+html\b|<html\b|<head\b|<body\b|<main\b|<section\b|<article\b|<div\b|<style\b)/i.test(sample)) {
            return 'html';
        }
        if (/^(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|mindmap|timeline|quadrantChart|gitGraph)\b/.test(sample)) {
            return 'mermaid';
        }
        if (/^[^\n,]+,[^\n,]+(?:,|[\r\n])/.test(sample)) {
            return 'csv';
        }
        if (lowerSample.startsWith('<!doctype') || lowerSample.startsWith('<svg')) {
            return 'html';
        }
        return normalizeContentType(currentType || 'markdown');
    }

    /**
     * Finalize thinking above a persisted canvas result without changing the
     * user's expanded state.
     */
    function finalizeThinkingForMessage(messageId) {
        if (!messageId) return;
        const container = document.getElementById('a-' + messageId);
        if (!container) return;

        if (typeof finalizeThinkingBlocks === 'function') {
            finalizeThinkingBlocks(container);
            return;
        }

        container.querySelectorAll('.assistant-thinking').forEach((block) => {
            if (typeof finalizeThinkingBlockHeader === 'function') {
                finalizeThinkingBlockHeader(block);
            }
        });
    }

    function renderSavedWidgetFromFile({ messageId, fileId, fileName, contentType, pageCount }) {
        if (!messageId || !fileId) return false;
        const container = document.getElementById('a-' + messageId);
        if (!container) return false;

        finalizeThinkingForMessage(messageId);

        const initialName = String(fileName || '');
        const resolvedType = resolveCanvasContentType(contentType, initialName);
        const resolvedName = resolveDisplayCanvasFileName(initialName, resolvedType);
        const typeLabel = getTypeLabel(resolvedType);
        const iconSvg = TYPE_ICONS[resolvedType] || TYPE_ICONS.markdown;

        let widget = container.querySelector(`.canvas-markdown-result-widget[data-canvas-file-id="${CSS.escape(String(fileId))}"]`);
        if (!widget) {
            widget = container.querySelector('.canvas-markdown-result-widget[data-canvas-status="generating"]');
        }

        if (!widget) {
            widget = document.createElement('div');
            widget.className = 'canvas-markdown-result-widget';
            widget.innerHTML =
                '<div class="canvas-markdown-result-header">' +
                '  <div class="canvas-markdown-result-icon" aria-hidden="true"></div>' +
                '  <div class="canvas-markdown-result-meta">' +
                '    <div class="canvas-markdown-result-title"></div>' +
                '    <div class="canvas-markdown-result-sub"></div>' +
                '  </div>' +
                '</div>' +
                '<button class="canvas-markdown-result-open-btn" type="button" data-canvas-open="true">' +
                Icons.eye +
                '  <span class="canvas-markdown-result-open-label">' + escapeHtml(t('canvas_open_canvas', BUTTON_LABEL_OPEN)) + '</span>' +
                '</button>';

            const widgetWrapper = document.createElement('div');
            widgetWrapper.className = 'assistant-widget';
            widgetWrapper.dataset.widgetType = 'canvas_result';
            widgetWrapper.appendChild(widget);
            appendBeforeAssistantList(container, widgetWrapper);
        }

        widget.setAttribute('data-canvas-file-id', String(fileId));
        widget.setAttribute('data-canvas-file-name', resolvedName);
        widget.setAttribute('data-canvas-content-type', resolvedType);
        widget.setAttribute('data-canvas-status', 'saved');
        widget.removeAttribute('data-canvas-draft-key');

        const iconEl = widget.querySelector('.canvas-markdown-result-icon');
        if (iconEl) {
            iconEl.className = 'canvas-markdown-result-icon canvas-type-' + resolvedType;
            iconEl.innerHTML = iconSvg;
        }
        const titleEl = widget.querySelector('.canvas-markdown-result-title');
        if (titleEl) titleEl.textContent = resolvedName || 'canvas';
        const subEl = widget.querySelector('.canvas-markdown-result-sub');
        if (subEl) subEl.textContent = typeLabel;

        const oldBtn = widget.querySelector('.canvas-markdown-result-open-btn');
        if (oldBtn) {
            const newBtn = oldBtn.cloneNode(true);
            oldBtn.parentNode.replaceChild(newBtn, oldBtn);
            newBtn.addEventListener('click', () => {
                if (previewVisible && activeDraftKey === String(fileId)) {
                    hidePreviewPanel();
                } else {
                    openPreviewForFile(String(fileId), resolvedName, resolvedType);
                }
            });
            updateOpenButtonLabel(newBtn, previewVisible && activeDraftKey === String(fileId));
        }

        widget.dataset.canvasFileId = String(fileId);
        widget.dataset.canvasFileName = resolvedName;
        widget.dataset.canvasContentType = resolvedType;
        widget.dataset.canvasWidgetInit = 'true';

        canvasFileIds.add(String(fileId));
        registerCanvasFile(String(fileId), resolvedName, resolvedType);
        refreshWidgetOpenButtonStates();
        return true;
    }

    function hidePreviewPanel() {
        // Closing while a file is loading invalidates its response. Otherwise a
        // late completion can rebuild the hidden editor after the user dismissed
        // it and interfere with the next open.
        filePreviewLoadTokens.delete(activeDraftKey);
        setPanelVisible(false);
        closeHtmlExternalResourceModal({ restoreFocus: false });
    }

    /* ── CSV Parsing and Rendering ── */
    const { parseCSV, renderCSVInto } = canvasWidgetModules.csv;
    delete globalThis.__omlorixCanvasWidgetModules;

    const {
        renderMarkdownInto, renderMermaidPreviewInto, getHtmlPreviewPermissions, getHtmlExternalResources,
        clearHtmlExternalResourcePromptTimer, closeHtmlExternalResourceModal, openHtmlExternalResourceModal, scheduleHtmlExternalResourcePrompt,
        resolveHtmlExternalResourceConsent, setHtmlCapabilityToggleState, getHtmlSettingsMenuItems, setHtmlSettingsMenuOpen,
        updateHtmlCapabilityControls, renderHTMLPreviewInto, reloadHtmlPreview, getIframeFragmentHref,
        handleIframeFragmentNavigation, bindIframePreviewNavigation, renderContentPreview, getCanvasCodeLineCount,
        syncCanvasCodeGutter, insertCanvasCodeText, createLatexPreviewNotice, createCanvasFileLoadErrorView,
        renderLatexPreviewInto, createEditableCanvasView, refreshActiveMarkdownDraftAfterSave, refreshActiveHtmlDraftAfterSave,
        renderStreamingMarkdownHtml, reconcileStreamingMarkdown, syncStreamingMarkdownChrome, renderStreamingMarkdownDraft,
        clearMarkdownStreamingRenderSchedule, flushMarkdownStreamingRender, scheduleMarkdownStreamingRender, renderSpreadsheetDraft,
        renderDraft, clearHtmlRenderTimer, scheduleHtmlStreamingRender, resetSelectablePdfPreviewRendering,
        renderSelectablePdfPreviewInto, HTML_PREVIEW_SRCDOC_URL,
    } = canvasWidgetModules.rendering.create({
        MARKDOWN_STREAM_RENDER_INTERVAL_MS, RENDER_DEBOUNCE_MS, SPREADSHEET_CONTENT_TYPES,
        addMarkedSelectionAsReference, applyScrollState,
        attachScrollListeners, buildCopyContextLabel, buildFileDownloadUrl, canvasWidgetModules,
        captureScrollState, clearPreviewRenderTimer, destroyActiveMarkdownEditor, destroyActiveSpreadsheetEditor,
        draftEditStateMap, draftMap, formatT, getDraftEditState, getPreviewHeaderIcon,
        getPreviewStatusKind, getPreviewStatusText, getRenderableContentForDraft, getScrollState,
        getStoredMarkdownScrollTop, getTypeLabel, hasAdjacentChatComposer, hasCurrentLatexPdf,
        hideReferenceToolbar, htmlExternalContentBtn,
        htmlExternalResourceDenyBtn, htmlExternalResourceList, htmlExternalResourceOverlay, htmlExternalResourcePromptTimers,
        htmlPreviewPermissionMap, htmlScriptsBtn, htmlSettings, htmlSettingsBtn,
        htmlSettingsMenu, isDraftEditorInteractive, normalizeContentType, previewDownload,
        openHtmlFullscreen: () => document.getElementById('canvas-html-FullscreenBtn')?.click(),
        prepareInteractiveHtmlPreviewSource: (...args) => prepareInteractiveHtmlPreviewSource(...args),
        previewPanel, previewStatus, previewTitle, previewTrack,
        queueAutoSaveForDraft, renderCSVInto, renderSavedLatexDraft, resolveDisplayCanvasFileName,
        replaceOmlorixFileUrls: (...args) => replaceOmlorixFileUrls(...args),
        refreshReferenceSelectionState,
        restoreScrollAfterMarkdownStream, runWithProgrammaticScroll, saveActiveDraftEdits, schedulePreviewRender,
        setActiveFileContext, setHtmlPreviewAvailability, setHtmlViewMode, setPreviewDownloadEnabled,
        setPreviewDownloadFormatOptions, syncDraftEditStateFromServer, syncMarkdownCompactMainLayout, t,
        updateCopyButtonState, updateDraftEditStateFromInput, updateEditorActionButtons, updateHtmlViewMode,
        updateMarkdownEditorHeaderControls, updateShareButtonState, updateStatusClass,
        withIframeSecurityGuard: (...args) => withIframeSecurityGuard(...args),
    }, {
        get previewVisible() { return previewVisible; },
        set previewVisible(value) { previewVisible = value; },
        get activeDraftKey() { return activeDraftKey; },
        set activeDraftKey(value) { activeDraftKey = value; },
        get activeFileContext() { return activeFileContext; },
        set activeFileContext(value) { activeFileContext = value; },
        get pendingHtmlExternalResourceConsent() { return pendingHtmlExternalResourceConsent; },
        set pendingHtmlExternalResourceConsent(value) { pendingHtmlExternalResourceConsent = value; },
        get htmlExternalResourceModalReturnFocus() { return htmlExternalResourceModalReturnFocus; },
        set htmlExternalResourceModalReturnFocus(value) { htmlExternalResourceModalReturnFocus = value; },
        get activeMarkdownEditorInstance() { return activeMarkdownEditorInstance; },
        set activeMarkdownEditorInstance(value) { activeMarkdownEditorInstance = value; },
        get activeSpreadsheetEditorInstance() { return activeSpreadsheetEditorInstance; },
        set activeSpreadsheetEditorInstance(value) { activeSpreadsheetEditorInstance = value; },
        get activeSpreadsheetEditorDraftKey() { return activeSpreadsheetEditorDraftKey; },
        set activeSpreadsheetEditorDraftKey(value) { activeSpreadsheetEditorDraftKey = value; },
        get spreadsheetRenderToken() { return spreadsheetRenderToken; },
        set spreadsheetRenderToken(value) { spreadsheetRenderToken = value; },
        get activeStreamingMarkdownPreview() { return activeStreamingMarkdownPreview; },
        set activeStreamingMarkdownPreview(value) { activeStreamingMarkdownPreview = value; },
        get markdownStreamRenderTimer() { return markdownStreamRenderTimer; },
        set markdownStreamRenderTimer(value) { markdownStreamRenderTimer = value; },
        get pendingMarkdownStreamDraft() { return pendingMarkdownStreamDraft; },
        set pendingMarkdownStreamDraft(value) { pendingMarkdownStreamDraft = value; },
        get markdownStreamLastRenderAt() { return markdownStreamLastRenderAt; },
        set markdownStreamLastRenderAt(value) { markdownStreamLastRenderAt = value; },
        get htmlPreviewAvailable() { return htmlPreviewAvailable; },
        set htmlPreviewAvailable(value) { htmlPreviewAvailable = value; },
        get renderDebounceTimer() { return renderDebounceTimer; },
        set renderDebounceTimer(value) { renderDebounceTimer = value; },
        get pendingHtmlRenderDraft() { return pendingHtmlRenderDraft; },
        set pendingHtmlRenderDraft(value) { pendingHtmlRenderDraft = value; },
    });
    const TYPE_ICONS = {
        markdown: Icons.file,
        mermaid: Icons.mermaid,
        csv: Icons.csv,
        tsv: Icons.csv,
        xlsx: Icons.csv,
        xls: Icons.csv,
        html: Icons.code,
        latex: Icons.file,
        pdf: Icons.file,
    };

    function injectInlineWidget(messageId, draft) {
        if (!messageId) return;
        const container = document.getElementById('a-' + messageId);
        if (!container) return;

        const draftKey = draft?.key || activeDraftKey || '';
        const contentType = normalizeContentType(draft?.contentType);
        const fileName = draft?.fileName || '';
        const typeLabel = getTypeLabel(contentType);
        const iconSvg = TYPE_ICONS[contentType] || TYPE_ICONS.markdown;
        const statusText = draft?.status || t('canvas_status_generating', 'Generating...');

        // The first tool delta creates the file box. Later deltas update that
        // same node so the chat does not flicker or reorder while the preview
        // is streaming beside it.
        const existing = container.querySelector(`.canvas-markdown-result-widget[data-canvas-draft-key="${CSS.escape(draftKey)}"]`);
        if (existing) {
            updateInlineWidget(messageId, draft);
            return existing;
        }

        const widget = document.createElement('div');
        widget.className = 'canvas-markdown-result-widget';
        widget.setAttribute('data-canvas-draft-key', draftKey);
        widget.setAttribute('data-canvas-file-name', fileName);
        widget.setAttribute('data-canvas-content-type', contentType);
        widget.setAttribute('data-canvas-status', 'generating');

        widget.innerHTML =
            '<div class="canvas-markdown-result-header">' +
            '  <div class="canvas-markdown-result-icon canvas-type-' + escapeHtml(contentType) + '" aria-hidden="true">' + iconSvg + '</div>' +
            '  <div class="canvas-markdown-result-meta">' +
            '    <div class="canvas-markdown-result-title">' + escapeHtml(fileName || 'canvas') + '</div>' +
            '    <div class="canvas-markdown-result-sub">' + escapeHtml(typeLabel) + ' • ' + escapeHtml(statusText) + '</div>' +
            '  </div>' +
            '</div>' +
            '<button class="canvas-markdown-result-open-btn" type="button">' +
            Icons.eye + 
            '  <span class="canvas-markdown-result-open-label">' + escapeHtml(t('canvas_open_canvas', BUTTON_LABEL_OPEN)) + '</span>' +
            '</button>';

        const openBtn = widget.querySelector('.canvas-markdown-result-open-btn');
        if (openBtn) {
            openBtn.addEventListener('click', () => {
                if (previewVisible && activeDraftKey === draftKey) {
                    hidePreviewPanel();
                    return;
                }
                activeDraftKey = draftKey;
                setPanelVisible(true);
                const currentDraft = draftMap.get(draftKey);
                if (!currentDraft) return;
                if (normalizeContentType(currentDraft.contentType) === 'markdown' && isDraftStreaming(currentDraft)) {
                    scheduleMarkdownStreamingRender(currentDraft, { immediate: true });
                } else {
                    renderDraft(currentDraft);
                }
            });
        }

        const widgetWrapper = document.createElement('div');
        widgetWrapper.className = 'assistant-widget';
        widgetWrapper.dataset.widgetType = 'canvas_result';
        widgetWrapper.appendChild(widget);

        if (typeof appendBeforeAssistantList === 'function') {
            appendBeforeAssistantList(container, widgetWrapper);
        } else {
            container.appendChild(widgetWrapper);
        }
        refreshWidgetOpenButtonStates();
        return widget;
    }

    /** Remove a speculative result card without altering the tool activity row. */
    function removeInlineWidget(messageId, draftKey = '') {
        if (!messageId) return;
        const container = document.getElementById('a-' + messageId);
        if (!container) return;

        const normalizedDraftKey = String(draftKey || '').trim();
        let widget = normalizedDraftKey
            ? container.querySelector(
                `.canvas-markdown-result-widget[data-canvas-draft-key="${CSS.escape(normalizedDraftKey)}"]`
            )
            : null;
        if (!widget) {
            const generatingWidgets = container.querySelectorAll(
                '.canvas-markdown-result-widget[data-canvas-status="generating"]'
            );
            if (generatingWidgets.length === 1) widget = generatingWidgets[0];
        }
        widget?.closest('.assistant-widget')?.remove();
    }

    /** Show cards for creations only; edits retain the normal tool activity UI. */
    function syncInlineWidgetForResultKind(messageId, draft) {
        const resultKind = String(draft?.resultKind || 'unknown');
        if (resultKind === 'create') {
            return injectInlineWidget(messageId, draft);
        }
        if (resultKind === 'edit' || resultKind === 'view') {
            removeInlineWidget(messageId, draft?.key || '');
        }
        return null;
    }

    function updateInlineWidget(messageId, draft) {
        if (!messageId) return;
        const container = document.getElementById('a-' + messageId);
        if (!container) return;
        const draftKey = draft?.key || activeDraftKey || '';
        const widget = container.querySelector(`.canvas-markdown-result-widget[data-canvas-draft-key="${CSS.escape(draftKey)}"]`);
        if (!widget) return;

        const contentType = normalizeContentType(draft?.contentType);
        const fileName = draft?.fileName || '';
        const typeLabel = getTypeLabel(contentType);
        const statusText = draft?.status || t('canvas_status_generating', 'Generating...');
        const iconSvg = TYPE_ICONS[contentType] || TYPE_ICONS.markdown;

        widget.setAttribute('data-canvas-file-name', fileName);
        widget.setAttribute('data-canvas-content-type', contentType);

        const iconEl = widget.querySelector('.canvas-markdown-result-icon');
        if (iconEl) {
            iconEl.className = 'canvas-markdown-result-icon canvas-type-' + contentType;
            iconEl.innerHTML = iconSvg;
        }
        const titleEl = widget.querySelector('.canvas-markdown-result-title');
        if (titleEl) titleEl.textContent = fileName || 'canvas';
        const subEl = widget.querySelector('.canvas-markdown-result-sub');
        if (subEl) subEl.textContent = typeLabel + ' • ' + statusText;
        refreshWidgetOpenButtonStates();
    }

    function updateInlineWidgetFinal(messageId, draft, fileId) {
        if (!messageId) return;
        const container = document.getElementById('a-' + messageId);
        if (!container) return;

        finalizeThinkingForMessage(messageId);

        const draftKey = draft?.key || activeDraftKey || '';

        // Find widget by old draft key
        let widget = container.querySelector(`.canvas-markdown-result-widget[data-canvas-draft-key="${CSS.escape(draftKey)}"]`);
        // Also try by any generating widget
        if (!widget) {
            widget = container.querySelector('.canvas-markdown-result-widget[data-canvas-status="generating"]');
        }
        if (!widget) return;

        const contentType = normalizeContentType(draft?.contentType);
        const fileName = draft?.fileName || '';
        const typeLabel = getTypeLabel(contentType);
        const iconSvg = TYPE_ICONS[contentType] || TYPE_ICONS.markdown;

        widget.setAttribute('data-canvas-file-id', fileId || '');
        widget.setAttribute('data-canvas-file-name', fileName);
        widget.setAttribute('data-canvas-content-type', contentType);
        widget.setAttribute('data-canvas-status', 'saved');
        widget.removeAttribute('data-canvas-draft-key');

        const iconEl = widget.querySelector('.canvas-markdown-result-icon');
        if (iconEl) {
            iconEl.className = 'canvas-markdown-result-icon canvas-type-' + contentType;
            iconEl.innerHTML = iconSvg;
        }
        const titleEl = widget.querySelector('.canvas-markdown-result-title');
        if (titleEl) titleEl.textContent = fileName || 'canvas';
        const subEl = widget.querySelector('.canvas-markdown-result-sub');
        if (subEl) subEl.textContent = typeLabel;

        // Replace open button handler with file-based toggle
        const oldBtn = widget.querySelector('.canvas-markdown-result-open-btn');
        if (oldBtn && fileId) {
            const newBtn = oldBtn.cloneNode(true);
            oldBtn.parentNode.replaceChild(newBtn, oldBtn);
            newBtn.addEventListener('click', () => {
                if (previewVisible && activeDraftKey === fileId) {
                    hidePreviewPanel();
                } else {
                    openPreviewForFile(fileId, fileName, contentType);
                }
            });
            updateOpenButtonLabel(newBtn, previewVisible && activeDraftKey === fileId);
        }

        // Mark for initResultWidget compatibility
        widget.dataset.canvasFileId = fileId || '';
        widget.dataset.canvasFileName = fileName;
        widget.dataset.canvasContentType = contentType;
        widget.dataset.canvasWidgetInit = 'true';
        refreshWidgetOpenButtonStates();
    }

    function registerCanvasFile(fileId, fileName, contentType) {
        if (!fileId || !window.canvasFilesDropdown) return;
        const type = normalizeContentType(contentType);
        const name = resolveDisplayCanvasFileName(fileName, type);
        window.canvasFilesDropdown.registerFile(fileId, name, 'canvas-markdown', () => {
            openPreviewForFile(fileId, name, type);
        });
    }

    function normalizeLatexPdfPayload(data) {
        const payload = data && typeof data === 'object' ? data : {};
        const meta = payload.meta && typeof payload.meta === 'object' ? payload.meta : {};
        const isSourceRecord = meta.latex_source === true;
        const recordFileId = String(payload.file_id || payload.fileId || '').trim();
        const recordFileName = String(payload.file_name || payload.fileName || meta.original_filename || '').trim();
        const pdfFileId = String(
            payload.pdf_file_id
            || payload.pdfFileId
            || (isSourceRecord ? meta.latex_pdf_file_id : recordFileId)
            || ''
        ).trim();
        const sourceFileId = String(
            payload.source_file_id
            || payload.sourceFileId
            || meta.latex_source_file_id
            || (isSourceRecord ? recordFileId : '')
            || ''
        ).trim();
        const pdfFileName = String(
            payload.pdf_file_name
            || payload.pdfFileName
            || meta.latex_pdf_file_name
            || (!isSourceRecord ? recordFileName : '')
            || 'document.pdf'
        ).trim() || 'document.pdf';
        const sourceFileName = String(
            payload.source_file_name
            || payload.sourceFileName
            || (isSourceRecord ? recordFileName : '')
            || pdfFileName.replace(/\.pdf$/i, '.tex')
            || 'document.tex'
        ).trim() || 'document.tex';
        const assetFileIds = Array.isArray(payload.asset_file_ids)
            ? payload.asset_file_ids
            : (Array.isArray(payload.assetFileIds) ? payload.assetFileIds : (Array.isArray(meta.latex_asset_file_ids) ? meta.latex_asset_file_ids : []));
        const inputFileNames = Array.isArray(payload.input_file_names)
            ? payload.input_file_names
            : (Array.isArray(payload.inputFileNames) ? payload.inputFileNames : (Array.isArray(meta.latex_input_file_names) ? meta.latex_input_file_names : []));
        const title = String(
            payload.title
            || payload.display_title
            || payload.displayTitle
            || meta.latex_display_title
            || meta.title
            || pdfFileName
        ).trim() || pdfFileName;
        // New Canvas records carry explicit source/render revisions. Results
        // created by the retired dedicated tool predate those fields, so a
        // successful PDF result with two zero revisions is still current.
        const canvasRevision = Number(
            payload.canvas_revision
            ?? payload.canvasRevision
            ?? payload.source_revision
            ?? payload.sourceRevision
            ?? meta.canvas_revision
            ?? meta.latex_source_revision
            ?? 0
        ) || 0;
        const renderRevision = Number(
            payload.render_revision
            ?? payload.renderRevision
            ?? payload.source_revision
            ?? payload.sourceRevision
            ?? meta.latex_render_revision
            ?? meta.latex_source_revision
            ?? 0
        ) || 0;
        const renderStatus = String(
            payload.render_status
            || payload.renderStatus
            || meta.latex_render_status
            || (pdfFileId ? 'ready' : 'not_rendered')
        ).trim() || (pdfFileId ? 'ready' : 'not_rendered');
        const normalized = {
            pdfFileId,
            sourceFileId,
            pdfFileName,
            sourceFileName,
            title,
            logExcerpt: String(payload.log_excerpt || payload.logExcerpt || meta.latex_log_excerpt || ''),
            assetFileIds: assetFileIds.map((id) => String(id || '').trim()).filter(Boolean),
            inputFileNames: inputFileNames.map((name) => String(name || '').trim()).filter(Boolean),
            canvasRevision,
            renderRevision,
            renderStatus,
        };
        return normalized;
    }

    function registerLatexPdfFile(draftOrPayload) {
        const payload = normalizeLatexPdfPayload({
            file_id: draftOrPayload?.pdfFileId || draftOrPayload?.file_id,
            source_file_id: draftOrPayload?.sourceFileId || draftOrPayload?.fileId || draftOrPayload?.source_file_id,
            file_name: draftOrPayload?.pdfFileName || draftOrPayload?.file_name,
            source_file_name: draftOrPayload?.fileName || draftOrPayload?.source_file_name,
            title: draftOrPayload?.title,
            log_excerpt: draftOrPayload?.logExcerpt || draftOrPayload?.log_excerpt,
            asset_file_ids: draftOrPayload?.assetFileIds || draftOrPayload?.asset_file_ids,
            input_file_names: draftOrPayload?.inputFileNames || draftOrPayload?.input_file_names,
            canvas_revision: draftOrPayload?.canvasRevision ?? draftOrPayload?.canvas_revision,
            render_revision: draftOrPayload?.renderRevision ?? draftOrPayload?.render_revision,
            render_status: draftOrPayload?.renderStatus || draftOrPayload?.render_status,
            source_revision: draftOrPayload?.sourceRevision ?? draftOrPayload?.source_revision,
        });
        if (!payload.pdfFileId || !window.canvasFilesDropdown) return;
        window.canvasFilesDropdown.registerFile(payload.pdfFileId, payload.title || payload.pdfFileName, 'latex-pdf', () => {
            openLatexPdfPreview(payload).catch((error) => {
                console.error('[canvas] Failed to open LaTeX PDF preview', error);
                if (typeof window.notifyError === 'function') {
                    window.notifyError(error?.message || t('latex_pdf_preview_open_failed', 'Failed to open PDF preview.'));
                }
            });
        });
    }

    async function openLatexPdfPreview(data) {
        const payload = normalizeLatexPdfPayload(data);
        if (!payload.pdfFileId) {
            throw new Error(t('latex_pdf_missing_file_id', 'PDF file id is missing.'));
        }
        const draftKey = payload.sourceFileId || payload.pdfFileId;
        resetScrollState(draftKey, { autoFollow: false });
        const draft = updateDraft(draftKey, {
            key: draftKey,
            toolName: 'latex_pdf',
            fileId: payload.sourceFileId,
            sourceFileId: payload.sourceFileId,
            pdfFileId: payload.pdfFileId,
            fileName: payload.sourceFileName,
            pdfFileName: payload.pdfFileName,
            title: payload.title,
            contentType: 'latex',
            content: '',
            status: payload.sourceFileId
                ? t('latex_pdf_status_loading_source', 'Loading LaTeX source...')
                : t('latex_pdf_ready', 'Ready'),
            allowHtmlPreview: Boolean(payload.pdfFileId),
            logExcerpt: payload.logExcerpt,
            assetFileIds: payload.assetFileIds,
            inputFileNames: payload.inputFileNames,
            canvasRevision: payload.canvasRevision,
            renderRevision: payload.renderRevision,
            renderStatus: payload.renderStatus,
        });
        setPanelVisible(true);
        renderDraft(draft);
        registerLatexPdfFile(payload);

        if (!payload.sourceFileId) {
            return;
        }

        try {
            // A stored transcript describes the PDF that existed when the
            // message was written. Load the source record as well so reopening
            // it uses the current derivative, revision, asset bundle, and
            // compile state rather than treating every historical result as
            // stale or recompiling it unnecessarily.
            const [content, sourceRecord] = await Promise.all([
                loadContentFromFile(payload.sourceFileId),
                loadCanvasFileRecord(payload.sourceFileId),
            ]);
            const sourceMeta = sourceRecord?.meta && typeof sourceRecord.meta === 'object'
                ? sourceRecord.meta
                : {};
            const hydratedPayload = normalizeLatexPdfPayload({
                ...payload,
                file_id: payload.sourceFileId,
                file_name: payload.sourceFileName,
                meta: sourceMeta,
                // Authoritative source metadata wins over transcript values
                // when a newer PDF has since been generated.
                pdf_file_id: sourceMeta.latex_pdf_file_id || payload.pdfFileId,
                pdf_file_name: sourceMeta.latex_pdf_file_name || payload.pdfFileName,
                canvas_revision: sourceMeta.canvas_revision ?? payload.canvasRevision,
                render_revision: sourceMeta.latex_render_revision ?? payload.renderRevision,
                render_status: sourceMeta.latex_render_status || payload.renderStatus,
                asset_file_ids: Array.isArray(sourceMeta.latex_asset_file_ids)
                    ? sourceMeta.latex_asset_file_ids
                    : payload.assetFileIds,
                input_file_names: Array.isArray(sourceMeta.latex_input_file_names)
                    ? sourceMeta.latex_input_file_names
                    : payload.inputFileNames,
            });
            const updated = updateDraft(draftKey, {
                content,
                pdfFileId: hydratedPayload.pdfFileId,
                pdfFileName: hydratedPayload.pdfFileName,
                title: hydratedPayload.title,
                canvasRevision: hydratedPayload.canvasRevision,
                renderRevision: hydratedPayload.renderRevision,
                renderStatus: hydratedPayload.renderStatus,
                logExcerpt: hydratedPayload.logExcerpt,
                assetFileIds: hydratedPayload.assetFileIds,
                inputFileNames: hydratedPayload.inputFileNames,
                status: hydratedPayload.renderStatus === 'ready'
                    && Number(hydratedPayload.renderRevision) === Number(hydratedPayload.canvasRevision)
                    ? t('latex_pdf_ready', 'Ready')
                    : t('canvas_latex_preview_stale', 'Preview is out of date'),
            });
            syncDraftEditStateFromServer(draftKey, content, { force: true });
            renderDraft(updated);
            registerLatexPdfFile(updated);
            const hasCurrentPreview = hydratedPayload.renderStatus === 'ready'
                && Number(hydratedPayload.renderRevision) === Number(hydratedPayload.canvasRevision)
                && Boolean(hydratedPayload.pdfFileId);
            setHtmlViewMode(hasCurrentPreview ? 'preview' : 'code');
        } catch (error) {
            const failed = updateDraft(draftKey, {
                status: getCanvasFileLoadFailureStatus(
                    error,
                    'latex_pdf_source_load_failed',
                    'Failed to load LaTeX source',
                ),
            });
            renderDraft(failed);
            throw error;
        }
    }

    function showLatexPdfStatus(data = {}) {
        const title = String(data.title || data.file_name || 'LaTeX PDF').trim() || 'LaTeX PDF';
        const draftKey = `latex:status:${title}`;
        const draft = updateDraft(draftKey, {
            key: draftKey,
            toolName: 'latex_pdf',
            fileId: '',
            sourceFileId: '',
            pdfFileId: '',
            fileName: title.replace(/\.pdf$/i, '.tex') || 'document.tex',
            pdfFileName: title.endsWith('.pdf') ? title : `${title}.pdf`,
            title,
            contentType: 'latex',
            content: '',
            status: String(data.message || t('latex_pdf_compiling', 'Compiling')),
            allowHtmlPreview: false,
        });
        setPanelVisible(true);
        renderDraft(draft);
        setHtmlViewMode('code');
    }

    function updateDraft(draftKey, updates, { activate = true } = {}) {
        const current = draftMap.get(draftKey) || {
            key: draftKey,
            toolName: 'canvas',
            content: '',
            contentType: 'markdown',
            argsBuffer: '',
            fileId: '',
            fileName: 'canvas.md',
            status: formatT('canvas_status_preparing_type', 'Preparing {type}', {
                type: t('canvas_type_canvas', 'Canvas'),
            }),
            statusKind: 'generating',
            allowHtmlPreview: false,
            hasExplicitContentType: false,
        };
        const next = { ...current, ...updates };
        draftMap.set(draftKey, next);
        if (activate) activeDraftKey = draftKey;
        return next;
    }

    const {
        CANVAS_FILE_PREVIEW_MAX_BYTES,
        CanvasPreviewTooLargeError,
        getCanvasFilePreviewMaxBytes,
        loadContentFromFile,
        loadSpreadsheetFromFile,
        loadCanvasFileRecord,
        getCanvasFileLoadFailureStatus,
        detectContentTypeFromFileName,
    } = canvasWidgetModules.fileLoading.create({ t, formatT, hasHtmlFileExtension });
    const {
        replaceOmlorixFileUrls,
        normalizeCanvasHtmlSource,
        prepareInteractiveHtmlPreviewSource,
        rewriteCanvasHtmlPreviewHtml,
        withIframeSecurityGuard,
        renderHtmlCanvasPngBlob,
    } = canvasWidgetModules.htmlDocuments.create({
        buildCanvasAssetUrl,
        getActiveDraft: () => ({
            key: String(activeDraftKey || ''),
            draft: draftMap.get(String(activeDraftKey || '')),
        }),
        srcdocUrl: HTML_PREVIEW_SRCDOC_URL,
    });
    function updateHtmlViewMode(wrapper, allowPreview = true) {
        if (!wrapper) return;
        // Binary PDFs have no source editor. They share the preview wrapper
        // only for layout, so applying a previously selected HTML/LaTeX code
        // mode would hide their sole content pane and leave Canvas blank.
        const previewOnly = wrapper.dataset.contentType === 'pdf';
        const effectiveMode = previewOnly
            ? 'preview'
            : (allowPreview ? currentHtmlViewMode : 'code');
        wrapper.classList.remove('code-view', 'preview-view');
        wrapper.classList.add(effectiveMode === 'code' ? 'code-view' : 'preview-view');
    }

    function setHtmlViewMode(mode) {
        // A source selection can remain active when its textarea is hidden.
        // Always dismiss its floating actions during a Code/Preview switch so
        // the rendered HTML surface never inherits a stale source tooltip.
        hideReferenceToolbar();
        if (mode === 'preview' && !htmlPreviewAvailable) {
            currentHtmlViewMode = 'code';
        } else {
            currentHtmlViewMode = mode === 'code' ? 'code' : 'preview';
        }
        updateHtmlToggleButtons();

        // Update all wrappers in the preview
        const wrappers = document.querySelectorAll('.canvas-html-preview-wrapper');
        wrappers.forEach((wrapper) => {
            const allowPreview = wrapper.dataset.allowPreview !== 'false';
            updateHtmlViewMode(wrapper, allowPreview && htmlPreviewAvailable);
        });
    }

    /**
     * Open the active preview. HTML and Mermaid remain immediate, while
     * LaTeX treats the Code -> Preview transition as the explicit compile
     * action. Any pending source edit is persisted first so the backend never
     * renders a superseded revision.
     */
    async function requestActivePreview() {
        let draftKey = String(activeDraftKey || '');
        let draft = draftMap.get(draftKey);
        if (!draft || normalizeContentType(draft.contentType) !== 'latex') {
            setHtmlViewMode('preview');
            return;
        }
        // Only the actual Code -> Preview transition is a compile action.
        // Re-clicking the already selected tab must not enqueue duplicate
        // compiler work while a render is still running.
        if (currentHtmlViewMode !== 'code') return;

        const editState = getDraftEditState(draftKey, draft.content || '');
        const revision = Number(draft.canvasRevision) || 0;
        const hasCurrentPreview = !editState?.dirty
            && draft.renderStatus === 'ready'
            && Number(draft.renderRevision) === revision
            && Boolean(draft.pdfFileId);
        if (hasCurrentPreview) {
            setHtmlViewMode('preview');
            return;
        }

        // Switch tabs immediately so the user gets visible feedback while a
        // pending autosave finishes and the compiler request starts.
        draft = updateDraft(draftKey, { previewRequested: true });
        setHtmlViewMode('preview');
        renderDraft(draft);

        // Normally this loop runs once. It also covers the narrow race where
        // an input event lands just before the code editor is hidden.
        while (true) {
            const currentDraft = draftMap.get(draftKey);
            if (!currentDraft) return;
            if (activeDraftKey !== draftKey) {
                updateDraft(draftKey, { previewRequested: false }, { activate: false });
                return;
            }
            const currentState = getDraftEditState(draftKey, currentDraft.content || '');
            if (!currentState?.dirty && !currentState?.saving) break;
            const saved = await saveActiveDraftEdits(draftKey);
            if (!saved) {
                const failedSaveDraft = updateDraft(draftKey, { previewRequested: false });
                renderDraft(failedSaveDraft);
                return;
            }
        }

        if (activeDraftKey !== draftKey) {
            updateDraft(draftKey, { previewRequested: false }, { activate: false });
            return;
        }
        await renderSavedLatexDraft(draftKey, { switchToPreview: false });
    }

    function setHtmlPreviewAvailability(isEnabled) {
        htmlPreviewAvailable = Boolean(isEnabled);
        updateHtmlToggleButtons();
        updateHtmlCapabilityControls();
        const wrappers = document.querySelectorAll('.canvas-html-preview-wrapper');
        wrappers.forEach((wrapper) => {
            const allowPreview = wrapper.dataset.allowPreview !== 'false';
            updateHtmlViewMode(wrapper, allowPreview && htmlPreviewAvailable);
        });
    }

    function updateHtmlToggleButtons() {
        const codeBtn = document.getElementById('canvas-html-ViewCodeBtn');
        const previewBtn = document.getElementById('canvas-html-ViewPreviewBtn');
        const fullscreenBtn = document.getElementById('canvas-html-FullscreenBtn');
        if (!codeBtn || !previewBtn) return;

        const effectiveMode = htmlPreviewAvailable ? currentHtmlViewMode : 'code';
        previewBtn.disabled = !htmlPreviewAvailable;
        previewBtn.classList.toggle('disabled', !htmlPreviewAvailable);
        previewBtn.setAttribute('aria-disabled', !htmlPreviewAvailable ? 'true' : 'false');

        if (fullscreenBtn) {
            fullscreenBtn.disabled = !htmlPreviewAvailable;
            fullscreenBtn.classList.toggle('is-disabled', !htmlPreviewAvailable);
            fullscreenBtn.setAttribute('aria-disabled', !htmlPreviewAvailable ? 'true' : 'false');
        }

        if (htmlReloadBtn) {
            htmlReloadBtn.disabled = !htmlPreviewAvailable;
            htmlReloadBtn.classList.toggle('is-disabled', !htmlPreviewAvailable);
            htmlReloadBtn.setAttribute('aria-disabled', !htmlPreviewAvailable ? 'true' : 'false');
        }

        codeBtn.classList.toggle('active', effectiveMode === 'code');
        previewBtn.classList.toggle('active', effectiveMode === 'preview');
        codeBtn.setAttribute('aria-selected', effectiveMode === 'code' ? 'true' : 'false');
        previewBtn.setAttribute('aria-selected', effectiveMode === 'preview' ? 'true' : 'false');
        updateHtmlCapabilityControls();
    }

    async function openPreviewForFile(fileId, fileName, contentType) {
        const detectedType = normalizeContentType(contentType || detectContentTypeFromFileName(fileName));
        const name = resolveDisplayCanvasFileName(fileName, detectedType);
        const isSpreadsheet = SPREADSHEET_CONTENT_TYPES.has(detectedType);
        const loadToken = Symbol(fileId);
        filePreviewLoadTokens.set(fileId, loadToken);
        const isCurrentLoad = () => (
            filePreviewLoadTokens.get(fileId) === loadToken
            && activeDraftKey === fileId
            && previewVisible
        );
        
        resetScrollState(fileId, { autoFollow: false });
        const draft = updateDraft(fileId, {
            key: fileId,
            fileId,
            fileName: name,
            contentType: detectedType,
            status: detectedType === 'pdf'
                ? t('latex_pdf_ready', 'Ready')
                : t('canvas_status_loading', 'Loading canvas…'),
            statusKind: detectedType === 'pdf' ? 'saved' : 'generating',
            allowHtmlPreview: detectedType !== 'html' && detectedType !== 'latex',
            loadError: null,
            // Never remount a previously cached workbook while the current
            // server snapshot is loading. That stale grid would be editable
            // and its first change could either be discarded by this request
            // or overwrite a newer collaborative revision.
            ...(isSpreadsheet ? { binaryContent: null } : {}),
        });
        setPanelVisible(true);
        try {
            renderDraft(draft);
        } catch (error) {
            // The loading shell is best-effort. Continue to fetch the actual
            // file because a renderer that rejects an empty draft may still be
            // able to render the populated document.
            console.error(error);
        }

        // The selectable PDF renderer loads bounded page metadata, text, and
        // inert images from dedicated authenticated endpoints. Never read the
        // binary file through the Canvas text loader.
        if (detectedType === 'pdf') {
            return;
        }

        try {
            if (isSpreadsheet) {
                // A fast switch away and back can overlap the prior Canvas'
                // autosave. Wait for that captured editor request so the
                // validated content endpoint returns the newly saved bytes.
                const pendingSave = draftSavePromises.get(fileId);
                if (pendingSave) await pendingSave;
                if (!isCurrentLoad()) return;
                const spreadsheetSnapshot = await loadSpreadsheetFromFile(fileId);
                if (!isCurrentLoad()) return;
                const canvasRevision = Number(spreadsheetSnapshot.canvasRevision) || 0;
                const updated = updateDraft(fileId, {
                    binaryContent: spreadsheetSnapshot.bytes,
                    content: '',
                    status: t('canvas_status_saved', 'Saved'),
                    statusKind: 'saved',
                    allowHtmlPreview: false,
                    canvasRevision,
                    spreadsheetRequiresRecalculation: spreadsheetSnapshot.requiresRecalculation === true,
                });
                const spreadsheetState = getDraftEditState(fileId, `revision:${canvasRevision}`);
                if (spreadsheetState) {
                    spreadsheetState.baselineContent = `revision:${canvasRevision}`;
                    spreadsheetState.draftContent = spreadsheetState.baselineContent;
                    spreadsheetState.dirty = false;
                    spreadsheetState.saving = false;
                    spreadsheetState.autoSavePending = false;
                    spreadsheetState.error = '';
                }
                renderDraft(updated);
                return;
            }
            let content;
            let fileRecord;
            if (detectedType === 'html') {
                // Read authenticated metadata before selecting the larger HTML
                // budget. The client never grants 8 MiB from an extension or
                // untrusted event payload alone.
                fileRecord = await loadCanvasFileRecord(fileId);
                if (!isCurrentLoad()) return;
                const maxBytes = getCanvasFilePreviewMaxBytes(detectedType, fileRecord);
                content = await loadContentFromFile(fileId, maxBytes);
                if (!isCurrentLoad()) return;
            } else {
                [content, fileRecord] = await Promise.all([
                    loadContentFromFile(fileId),
                    detectedType === 'latex' ? loadCanvasFileRecord(fileId) : Promise.resolve(null),
                ]);
                if (!isCurrentLoad()) return;
            }
            const fileMeta = fileRecord?.meta && typeof fileRecord.meta === 'object'
                ? fileRecord.meta
                : {};
            const canvasRevision = Number(fileMeta.canvas_revision) || 0;
            const renderRevision = Number(fileMeta.latex_render_revision) || 0;
            const pdfFileId = detectedType === 'latex'
                ? String(fileMeta.latex_pdf_file_id || '')
                : '';
            const updated = updateDraft(fileId, {
                content,
                status: detectedType === 'latex' && renderRevision !== canvasRevision
                    ? t('canvas_latex_preview_stale', 'Preview is out of date')
                    : t('canvas_status_saved', 'Saved'),
                statusKind: 'saved',
                allowHtmlPreview: detectedType === 'latex'
                    ? Boolean(pdfFileId)
                    : (detectedType === 'html' ? true : draft.allowHtmlPreview),
                sourceFileId: detectedType === 'latex' ? String(fileId) : '',
                pdfFileId,
                pdfFileName: detectedType === 'latex'
                    ? String(fileMeta.latex_pdf_file_name || '')
                    : '',
                title: detectedType === 'latex'
                    ? String(fileMeta.latex_display_title || fileMeta.title || name)
                    : '',
                canvasRevision,
                renderRevision,
                renderStatus: detectedType === 'latex'
                    ? String(fileMeta.latex_render_status || 'not_rendered')
                    : '',
                logExcerpt: detectedType === 'latex'
                    ? String(fileMeta.latex_log_excerpt || '')
                    : '',
                assetFileIds: detectedType === 'latex' && Array.isArray(fileMeta.latex_asset_file_ids)
                    ? fileMeta.latex_asset_file_ids
                    : [],
                loadError: null,
            });
            syncDraftEditStateFromServer(fileId, content, { force: true });
            renderDraft(updated);
            if (detectedType === 'latex') {
                if (pdfFileId && renderRevision === canvasRevision && updated.renderStatus === 'ready') {
                    setHtmlViewMode('preview');
                } else {
                    // Opening an unrendered or stale source is read-only with
                    // respect to compilation. The user starts the render by
                    // moving from Code to Preview.
                    setHtmlViewMode('code');
                }
            } else if (detectedType === 'html' || detectedType === 'mermaid') {
                setHtmlViewMode('preview');
            }
        } catch (error) {
            if (!isCurrentLoad()) return;
            const failureMessage = getCanvasFileLoadFailureStatus(
                error,
                'files_preview_load_error',
                'Failed to load preview',
            );
            const failed = updateDraft(fileId, {
                // Keep the narrow header status concise. The detailed and
                // actionable error is rendered in the Canvas body instead.
                status: t('files_preview_load_error', 'Failed to load preview'),
                loadError: {
                    kind: error instanceof CanvasPreviewTooLargeError ? 'too-large' : 'load-failed',
                    message: failureMessage,
                    maxBytes: error instanceof CanvasPreviewTooLargeError ? error.maxBytes : null,
                },
                // updateDraft merges with the loading draft, so explicitly
                // replace its generating state when the request fails.
                statusKind: 'error',
            });
            try {
                renderDraft(failed);
            } catch (renderError) {
                // Keep the close control usable even if the rich renderer is
                // what failed. This minimal error state has no editor/runtime
                // dependencies and therefore cannot trap the sidebar loading.
                if (previewPanel) {
                    previewPanel.setAttribute('data-content-type', detectedType);
                    previewPanel.setAttribute('data-load-error', 'true');
                }
                if (previewStatus) {
                    previewStatus.textContent = failed.status;
                    updateStatusClass(failed.status, 'error');
                }
                if (previewTrack) {
                    const fallback = document.createElement('div');
                    fallback.className = 'canvas-markdown-error';
                    fallback.textContent = failureMessage;
                    previewTrack.replaceChildren(fallback);
                }
                console.error(renderError);
            }
            console.error(error);
        }
    }

    function handleToolCallEvent(obj, messageId) {
        const descriptor = obj?.d || {};
        const toolName = typeof descriptor === 'string'
            ? descriptor
            : (descriptor.name || obj?.name || '');
        if (!isCanvasToolName(toolName)) return;

        const normalizedMessageId = String(messageId || '').trim();
        const draftKey = String(descriptor.id || `canvas:${normalizedMessageId || 'current'}`);
        // A saved event is terminal for this exact call. Ignore a provider's
        // late finalized tool packet instead of reviving the progress state.
        if (isCanvasToolCallTerminal(draftKey)) return;

        lastActiveMessageId = normalizedMessageId;
        clearHtmlRenderTimer();
        clearMarkdownStreamingRenderSchedule();
        if (normalizedMessageId) {
            trackCanvasToolCallForMessage(normalizedMessageId, draftKey);
        }
        activeCanvasToolCallKey = draftKey;
        rememberCanvasScrollForToolCall(draftKey);
        const argsFromDescriptor = descriptor.args ?? obj?.c ?? {};
        const decodedArgs = parseJsonSafe(argsFromDescriptor)
            || (typeof argsFromDescriptor === 'object' ? argsFromDescriptor : null)
            || {};
        const extracted = extractCanvasArgs(argsFromDescriptor);
        const resultKind = classifyCanvasResultKind(decodedArgs, extracted);
        const contentType = inferCanvasContentType({
            explicitType: extracted.hasContentType ? extracted.contentType : '',
            fileName: extracted.fileName,
            content: extracted.content,
        });
        const typeLabel = getTypeLabel(contentType);
        const hasRenderableInitialArgs = Boolean(
            extracted.content
            || extracted.fileId
            || extracted.fileName
            || hasExplicitCanvasContentType(argsFromDescriptor)
        );

        // A completed t_c event follows the t_cd stream. Preserve the draft's
        // user-interrupted scroll state instead of treating completion as a new
        // canvas and resetting it.
        if (extracted.fileId) restoreCanvasScrollForToolEdit(draftKey, extracted.fileId);
        if (!draftScrollStates.has(draftKey)) resetScrollState(draftKey);
        const nextDraft = updateDraft(draftKey, {
            toolName: 'canvas',
            content: extracted.content || '',
            contentType: contentType,
            fileId: extracted.fileId || '',
            fileName: resolveDisplayCanvasFileName(extracted.fileName, contentType),
            status: formatT('canvas_status_writing_type', 'Writing {type}…', { type: typeLabel }),
            statusKind: 'generating',
            allowHtmlPreview: contentType === 'html' ? false : true,
            hasExplicitContentType: hasExplicitCanvasContentType(argsFromDescriptor),
            resultKind,
        }, { activate: resultKind !== 'view' && hasRenderableInitialArgs });
        // Only a definitive create call owns a new chat file box. Empty and
        // partial starts remain card-free until their arguments are known.
        syncInlineWidgetForResultKind(normalizedMessageId, nextDraft);
        if (resultKind === 'view' || !hasRenderableInitialArgs) {
            if (resultKind === 'view' && normalizedMessageId) {
                forgetCanvasToolCallForMessage(normalizedMessageId, draftKey);
            }
            activeCanvasToolCallKey = '';
            return;
        }
        setPanelVisible(true);
        renderDraft(nextDraft);
        activeCanvasToolCallKey = '';
    }

    function handleToolCallDeltaEvent(obj, messageId) {
        const descriptor = obj?.d || {};
        const maybeName = normalizeName(descriptor.name || '');
        if (maybeName && !isCanvasToolName(maybeName)) return;

        const normalizedMessageId = String(messageId || '').trim();
        const toolMessageId = normalizedMessageId || lastActiveMessageId || '';

        const isNamedCanvasDelta = isCanvasToolName(maybeName);
        const draftKey = String(
            descriptor.id
            || getLatestCanvasToolCallForMessage(toolMessageId)
            || (isNamedCanvasDelta ? `canvas:${toolMessageId || 'current'}` : activeCanvasToolCallKey)
            || activeDraftKey
            || `canvas:${toolMessageId || 'current'}`
        );
        if (isCanvasToolCallTerminal(draftKey)) return;
        lastActiveMessageId = toolMessageId;
        if (descriptor.id || isNamedCanvasDelta) {
            activeCanvasToolCallKey = draftKey;
            if (toolMessageId) {
                trackCanvasToolCallForMessage(toolMessageId, draftKey);
            }
        }
        rememberCanvasScrollForToolCall(draftKey);
        const existingDraft = draftMap.get(draftKey);
        getScrollState(draftKey);
        const hasCanvasContext = Boolean(
            isCanvasToolName(maybeName)
            || (existingDraft && isCanvasToolName(existingDraft.toolName))
            || (activeDraftKey && draftMap.get(activeDraftKey) && isCanvasToolName(draftMap.get(activeDraftKey).toolName))
        );
        if (!hasCanvasContext) return;

        const existingContentType = existingDraft?.contentType || 'markdown';
        const current = updateDraft(draftKey, {
            toolName: maybeName || 'canvas',
            status: formatT('canvas_streaming_type', 'Streaming {type}...', { type: getContentLabel(existingContentType) }),
            statusKind: 'generating',
            resultKind: existingDraft?.resultKind || 'unknown',
        }, { activate: false });
        const delta = typeof descriptor.delta === 'string' ? descriptor.delta : '';
        if (!delta) {
            // A name/id-only first delta does not reveal whether this is a
            // create, edit, or view. Keep the existing preview intact until an
            // argument establishes that the call will render content.
            return;
        }

        const buffer = String(current.argsBuffer || '') + delta;
        const parsedArgs = parseJsonSafe(buffer);
        let updated;
        if (parsedArgs && typeof parsedArgs === 'object') {
            const extracted = extractCanvasArgs(parsedArgs);
            const resultKind = classifyCanvasResultKind(parsedArgs, extracted);
            const hasExplicitContentType = Boolean(current.hasExplicitContentType || extracted.hasContentType);
            const contentType = inferCanvasContentType({
                explicitType: extracted.hasContentType ? extracted.contentType : '',
                currentType: current.contentType,
                fileName: extracted.fileName || current.fileName,
                content: extracted.content || current.content || '',
            });
            updated = updateDraft(draftKey, {
                argsBuffer: buffer,
                content: extracted.content || current.content || '',
                contentType: contentType,
                fileId: extracted.fileId || current.fileId || '',
                fileName: resolveDisplayCanvasFileName(extracted.fileName || current.fileName, contentType),
                status: formatT('canvas_streaming_type', 'Streaming {type}...', { type: getContentLabel(contentType) }),
                statusKind: 'generating',
                allowHtmlPreview: contentType === 'html' ? false : true,
                hasExplicitContentType,
                resultKind,
            }, { activate: false });
        } else {
            const streamingExtracted = extractCanvasArgsFromBuffer(buffer);
            const resultKind = streamingExtracted.rawType === 'view'
                ? 'view'
                : (streamingExtracted.fileId ? 'edit' : (current.resultKind || 'unknown'));
            const hasExplicitContentType = Boolean(current.hasExplicitContentType || streamingExtracted.hasContentType);
            const contentType = inferCanvasContentType({
                explicitType: streamingExtracted.hasContentType ? streamingExtracted.contentType : '',
                currentType: current.contentType,
                fileName: streamingExtracted.fileName || current.fileName,
                content: streamingExtracted.content || current.content || '',
            });
            updated = updateDraft(draftKey, {
                argsBuffer: buffer,
                content: streamingExtracted.content || current.content || '',
                contentType: contentType,
                fileId: streamingExtracted.fileId || current.fileId || '',
                fileName: resolveDisplayCanvasFileName(streamingExtracted.fileName || current.fileName, contentType),
                status: formatT('canvas_streaming_type', 'Streaming {type}...', { type: getContentLabel(contentType) }),
                statusKind: 'generating',
                allowHtmlPreview: contentType === 'html' ? false : true,
                hasExplicitContentType,
                resultKind,
            }, { activate: false });
        }

        if (updated.fileId) restoreCanvasScrollForToolEdit(draftKey, updated.fileId);

        // Viewing an artifact is observational. Never replace or empty an open
        // preview merely because the model read a Canvas file.
        if (updated.resultKind === 'view') {
            syncInlineWidgetForResultKind(toolMessageId, updated);
            return;
        }

        if (!updated.content && !updated.fileId && !updated.hasExplicitContentType) {
            // A partial object is not enough to distinguish view from a
            // mutating call. Waiting for a type, file id, or content avoids a
            // transient empty preview while still opening create/edit streams
            // as soon as they become identifiable.
            syncInlineWidgetForResultKind(toolMessageId, updated);
            return;
        }

        activeDraftKey = draftKey;
        syncInlineWidgetForResultKind(toolMessageId, updated);
        setPanelVisible(true);
        
        const contentType = normalizeContentType(updated.contentType);
        if (contentType === 'html') {
            clearMarkdownStreamingRenderSchedule();
            scheduleHtmlStreamingRender(updated);
        } else if (contentType === 'markdown') {
            clearHtmlRenderTimer();
            scheduleMarkdownStreamingRender(updated);
        } else {
            clearHtmlRenderTimer();
            clearMarkdownStreamingRenderSchedule();
            renderDraft(updated);
        }
    }

    function handleCanvasEvent(obj, messageId) {
        if (!obj || obj.t !== 'canvas_evt') return;
        const eventName = String(obj.event || '');
        const data = obj.data || {};
        if (eventName !== 'saved') return;

        // Slide HTML remains editable through Canvas, but its preview belongs
        // to the presentation sidebar. The backend marks this explicitly so
        // ordinary HTML keeps the normal isolated Canvas preview flow.
        if (data.artifact_kind === 'slide_presentation') {
            const presentation = data.presentation || {};
            if (window.slidePresentationWidget?.handleSlidePresentationEvent) {
                window.slidePresentationWidget.handleSlidePresentationEvent(
                    { t: 'slide_presentation_evt', event: 'complete', data: presentation },
                    messageId,
                );
            }
            return;
        }

        clearHtmlRenderTimer();
        clearMarkdownStreamingRenderSchedule();

        const contentType = normalizeContentType(data.content_type || data.contentType);
        const normalizedMessageId = String(messageId || '').trim();
        const eventMessageId = normalizedMessageId || lastActiveMessageId || '';
        const eventToolCallId = String(data.tool_call_id || data.toolCallId || '').trim();
        const sourceKey = String(
            eventToolCallId
            || getLatestCanvasToolCallForMessage(eventMessageId)
            || activeDraftKey
            || ''
        );
        const key = String(data.file_id || sourceKey || `canvas:saved:${Date.now()}`);
        const resultKind = data.created === true ? 'create' : 'edit';
        markCanvasToolCallTerminal(eventToolCallId || sourceKey);
        if (sourceKey && sourceKey !== key) {
            migrateDraftClientState(sourceKey, key);
        }
        const updated = updateDraft(key, {
            key,
            fileId: data.file_id ? String(data.file_id) : '',
            fileName: resolveDisplayCanvasFileName(data.file_name || data.title, contentType),
            content: typeof data.content === 'string' ? data.content : (typeof data.markdown === 'string' ? data.markdown : (draftMap.get(key)?.content || '')),
            contentType: contentType,
            status: contentType === 'latex'
                ? t('canvas_latex_preview_stale', 'Preview is out of date')
                : t('canvas_status_saved', 'Saved'),
            statusKind: 'saved',
            allowHtmlPreview: contentType === 'latex'
                ? Boolean(data.pdf_file_id || data.pdfFileId)
                : (contentType === 'html' || contentType === 'mermaid'),
            sourceFileId: contentType === 'latex' ? String(data.file_id || '') : '',
            pdfFileId: contentType === 'latex' ? String(data.pdf_file_id || data.pdfFileId || '') : '',
            pdfFileName: contentType === 'latex' ? String(data.pdf_file_name || data.pdfFileName || '') : '',
            canvasRevision: Number(data.canvas_revision ?? data.canvasRevision) || 0,
            renderRevision: Number(data.render_revision ?? data.renderRevision) || 0,
            renderStatus: contentType === 'latex'
                ? String(data.render_status || data.renderStatus || 'not_rendered')
                : '',
            assetFileIds: Array.isArray(data.asset_file_ids)
                ? data.asset_file_ids.map((id) => String(id || '').trim()).filter(Boolean)
                : [],
            resultKind,
        });
        const savedState = getScrollState(key);
        if (savedState) {
            savedState.autoFollow = false;
        }
        if (updated.fileId) {
            canvasFileIds.add(String(updated.fileId));
            registerCanvasFile(updated.fileId, updated.fileName, contentType);
        }
        const editState = syncDraftEditStateFromServer(key, updated.content || '', { force: false });
        setPanelVisible(true);
        renderDraft(updated, true);
        if (editState?.dirty) {
            queueAutoSaveForDraft(key, { immediate: true });
        }
        if (resultKind === 'create') {
            updateInlineWidgetFinal(eventMessageId, updated, updated.fileId);
        } else {
            // Do not collapse or replace the tool activity row for edits; only
            // remove any defensive/speculative card left by an older stream.
            removeInlineWidget(eventMessageId, sourceKey || key);
        }
        if (eventMessageId) {
            forgetCanvasToolCallForMessage(eventMessageId, eventToolCallId || sourceKey);
        }
        if (contentType === 'latex') {
            // Model-authored LaTeX opens in Code. Compilation is deliberately
            // deferred until the user selects Preview.
            setHtmlViewMode('code');
        } else if (contentType === 'html' || contentType === 'mermaid') {
            setHtmlViewMode('preview');
        }
    }

    /**
     * Resolve a draft when its transport ends without a Canvas saved event.
     * Successful saves remove the message mapping in handleCanvasEvent, so
     * this is a no-op for the normal path and an explicit error for truncated,
     * cancelled, or failed streams.
     */
    function handleStreamEnd(messageId) {
        const normalizedMessageId = String(messageId || '').trim();
        if (!normalizedMessageId) return false;
        const draftKeys = Array.from(canvasToolCallKeysByMessage.get(normalizedMessageId) || []);
        if (!draftKeys.length) return false;

        canvasToolCallKeysByMessage.delete(normalizedMessageId);
        let resolvedAny = false;
        for (const draftKey of draftKeys) {
            markCanvasToolCallTerminal(draftKey);
            const draft = draftMap.get(draftKey);
            if (!draft || !isDraftStreaming(draft)) continue;

            clearHtmlRenderTimer();
            clearMarkdownStreamingRenderSchedule();
            const failed = updateDraft(draftKey, {
                status: t('canvas_status_not_saved', 'Canvas generation ended before it was saved.'),
                statusKind: 'error',
                allowHtmlPreview: normalizeContentType(draft.contentType) !== 'html',
            }, { activate: false });
            removeInlineWidget(normalizedMessageId, draftKey);
            if (activeDraftKey === draftKey) renderDraft(failed);
            resolvedAny = true;
        }
        return resolvedAny;
    }

    canvasWidgetModules.lifecycle.initialize({
        SPREADSHEET_CONTENT_TYPES, applyPreviewWidthRatio, applyShareMode, autoSaveTimers,
        beginPreviewResize, buildFileDownloadUrl, canvasFileIds, canvasToolCallKeysByMessage,
        chatArea, clearHtmlExternalResourcePromptTimer, clearHtmlRenderTimer, clearMarkdownStreamingRenderSchedule,
        closeHtmlExternalResourceModal, closeShareModal, copyRawCanvasContent, copyShareUrl,
        createShareLink, deleteShareLink, destroyActiveMarkdownEditor, destroyActiveSpreadsheetEditor,
        draftEditStateMap, draftMap, draftScrollStates, endPreviewResize,
        enterShareCreateMode, enterShareEditMode, enterShareListMode, filePreviewLoadTokens,
        getDefaultShareExpiryIso, getDraftEditState, getHtmlExternalResources, getHtmlPreviewPermissions,
        getHtmlSettingsMenuItems, getPreviewWidthBounds, getRenderableContentForDraft, getShareLinkById,
        handleCanvasEvent, handlePreviewResizerKeydown, handleStreamEnd, handleToolCallDeltaEvent,
        handleToolCallEvent, hasCurrentLatexPdf, hasHtmlFileExtension, hidePreviewPanel,
        hideReferenceToolbar, hideShareExpiryError, hideSharePasswordError, htmlExternalContentBtn,
        htmlExternalResourceAllowBtn, htmlExternalResourceDenyBtn, htmlExternalResourceOverlay, htmlExternalResourcePromptTimers,
        htmlPreviewPermissionMap, htmlReloadBtn, htmlScriptsBtn, htmlSettings,
        htmlSettingsBtn, htmlSettingsMenu, isLikelyCanvasFile, latexRenderRequestTokens,
        markdownEditorEditorTab, markdownEditorMarkdownTab, normalizeCanvasHtmlSource, normalizeContentType,
        notifyShareError, openLatexPdfPreview, openPreviewForFile, openShareDialogForFile,
        openShareModal, previewClose, previewCopyBtn,
        previewDownload, previewDownloadFormat, previewPanel, previewRenderTimers,
        previewResizer, previewRevertBtn, previewSaveBtn, previewShareBtn,
        previewStatus, previewTitle, previewTrack, refreshExistingShareLinksForButton,
        refreshReferenceSelectionState, refreshWidgetOpenButtonStates, registerCanvasFile, reloadHtmlPreview,
        renderHTMLPreviewInto, renderHtmlCanvasPngBlob, renderSavedWidgetFromFile, requestActivePreview,
        resetPreviewWidth, resetSelectablePdfPreviewRendering, resolveDisplayCanvasFileName, resolveHtmlExternalResourceConsent,
        revertActiveDraftEdits, runShareWithBusy, saveActiveDraftEdits, setHtmlSettingsMenuOpen,
        setHtmlViewMode, setPanelVisible, setPreviewDownloadBusy, setPreviewDownloadEnabled,
        setPreviewWidthFromPixels, setPreviewWidthFromPointerX, setReferenceToolbarState, setSharingFlagFromSetup,
        shareCloseBtn, shareExpiryContent, shareExpiryInput, shareExpiryToggle,
        shareFileName, shareLinksList, shareModal, shareOverlay,
        sharePasswordContent, sharePasswordInput, sharePasswordToggle, sharePrimaryBtn,
        shareSecondaryBtn, showLatexPdfStatus, showSharePasswordError, t,
        terminalCanvasToolCallKeys, toLocalDateTimeValue, trapFocus, updateCopyButtonState,
        updateEditorActionButtons, updateHtmlCapabilityControls, updateHtmlToggleButtons, updatePreviewResize,
        updateShareButtonState, updateShareLink,
    }, {
        get previewVisible() { return previewVisible; },
        set previewVisible(value) { previewVisible = value; },
        get activeDraftKey() { return activeDraftKey; },
        set activeDraftKey(value) { activeDraftKey = value; },
        get activeCanvasToolCallKey() { return activeCanvasToolCallKey; },
        set activeCanvasToolCallKey(value) { activeCanvasToolCallKey = value; },
        get draftLifecycleGeneration() { return draftLifecycleGeneration; },
        set draftLifecycleGeneration(value) { draftLifecycleGeneration = value; },
        get activeStreamingMarkdownPreview() { return activeStreamingMarkdownPreview; },
        set activeStreamingMarkdownPreview(value) { activeStreamingMarkdownPreview = value; },
        get pendingCanvasToolScrollSnapshot() { return pendingCanvasToolScrollSnapshot; },
        set pendingCanvasToolScrollSnapshot(value) { pendingCanvasToolScrollSnapshot = value; },
        get suppressUserScrollEvents() { return suppressUserScrollEvents; },
        set suppressUserScrollEvents(value) { suppressUserScrollEvents = value; },
        get lastActiveMessageId() { return lastActiveMessageId; },
        set lastActiveMessageId(value) { lastActiveMessageId = value; },
        get activeFileContext() { return activeFileContext; },
        set activeFileContext(value) { activeFileContext = value; },
        get activeReferenceSelection() { return activeReferenceSelection; },
        set activeReferenceSelection(value) { activeReferenceSelection = value; },
        get pendingHtmlExternalResourceConsent() { return pendingHtmlExternalResourceConsent; },
        set pendingHtmlExternalResourceConsent(value) { pendingHtmlExternalResourceConsent = value; },
        get currentHtmlViewMode() { return currentHtmlViewMode; },
        set currentHtmlViewMode(value) { currentHtmlViewMode = value; },
        get htmlPreviewAvailable() { return htmlPreviewAvailable; },
        set htmlPreviewAvailable(value) { htmlPreviewAvailable = value; },
        get shareModalOpen() { return shareModalOpen; },
        set shareModalOpen(value) { shareModalOpen = value; },
        get sharingAllowedByGroup() { return sharingAllowedByGroup; },
        set sharingAllowedByGroup(value) { sharingAllowedByGroup = value; },
        get currentShareLinks() { return currentShareLinks; },
        set currentShareLinks(value) { currentShareLinks = value; },
        get activeMarkdownEditorInstance() { return activeMarkdownEditorInstance; },
        set activeMarkdownEditorInstance(value) { activeMarkdownEditorInstance = value; },
        get activeSpreadsheetEditorInstance() { return activeSpreadsheetEditorInstance; },
        set activeSpreadsheetEditorInstance(value) { activeSpreadsheetEditorInstance = value; },
        get canvasPreviewWidthRatio() { return canvasPreviewWidthRatio; },
        set canvasPreviewWidthRatio(value) { canvasPreviewWidthRatio = value; },
        get shareMode() { return shareMode; },
        set shareMode(value) { shareMode = value; },
        get activeShareLink() { return activeShareLink; },
        set activeShareLink(value) { activeShareLink = value; },
    });
})();
