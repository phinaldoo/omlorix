// Chat-Side Notes Tool Sidebar
// ============================================================================

const NotesToolSidebar = (() => {
    const state = {
        panel: null,
        track: null,
        title: null,
        status: null,
        closeBtn: null,
        markdownTab: null,
        editorTab: null,
        copyBtn: null,
        downloadFormat: null,
        downloadBtn: null,
        editor: null,
        activeNoteId: '',
        activeMessageId: '',
        canEdit: false,
        content: '',
        lastSavedContent: '',
        lastSavedUpdatedAt: '',
        referencedFiles: [],
        saveTimer: null,
        isSaving: false,
        activeSaveNoteId: '',
        activeSaveContent: '',
        activeSavePromise: null,
        isVisible: false,
        isLoading: false,
        resizeActive: false,
        widthRatio: 0.5,
        statusKey: 'notes_tool_preview_waiting',
        statusFallback: 'Waiting for notes tool...',
        statusClassName: 'complete',
        isDownloading: false,
        downloadDefaultHtml: '',
        copyFeedbackTimer: null,
        copyDefaultLabel: '',
        // Navigation can close and immediately reset the sidebar before the
        // normal debounce fires. Keep the exact note/content snapshot outside
        // the editor lifecycle so that transition saves survive teardown.
        backgroundSaveSignature: '',
        backgroundSavePromise: null,
        // Tool arguments arrive as JSON fragments before the notes endpoint has
        // persisted anything. Keep that transient state separate from a saved
        // note so the sidebar can render the Markdown without enabling edits or
        // downloads prematurely.
        streamingCallId: '',
        streamingMessageId: '',
        streamingArgsBuffer: '',
        streamingOperation: '',
        streamingBaseContent: '',
        streamingBaseNoteId: '',
        streamingRevision: 0,
        streamingPreviewEl: null,
        streamingRenderTimer: null,
        streamingScrollFrame: null,
        streamingPendingMessageId: '',
        streamingLastRenderAt: 0,
        streamingAutoFollow: true,
        streamingUserControlledScroll: false,
        streamingPreservedScrollTop: null,
        streamingIgnoreScrollUntil: 0,
        streamingTouchY: null,
        streamingOriginNoteId: '',
        streamingOriginMessageId: '',
        streamingOriginContent: '',
        streamingOriginLastSavedContent: '',
        streamingOriginUpdatedAt: '',
        streamingOriginCanEdit: false,
        streamingOriginWasVisible: false,
        streamingOriginReferencedFiles: [],
        streamingOriginScrollState: null,
        streamingOriginSavePromise: null,
        // Closing a live or freshly saved note is a user decision for the
        // remainder of that assistant response. Late deltas and the terminal
        // saved event must update the result widget without reopening it.
        dismissedPreviewMessageId: '',
    };

    const WIDTH_STORAGE_KEY = 'omlorix.notesToolPreviewWidthRatio';
    const MIN_PANEL_WIDTH = 420;
    const MIN_MAIN_WIDTH = 360;
    const RESIZE_STEP = 32;
    const SAVE_DELAY_MS = 500;
    // Full Markdown parsing and DOM replacement is intentionally capped. Tool
    // deltas can arrive dozens of times per second, while 10 visual updates per
    // second remains fluid and dramatically reduces layout and garbage churn.
    const STREAM_RENDER_INTERVAL_MS = 100;
    const STREAM_SCROLL_BOTTOM_THRESHOLD = 24;
    // The preview panel is shared, but split-screen generations are independent.
    // Keep non-visible calls queued by tool-call id so deltas from one panel can
    // never reset the argument buffer or cleanup state owned by another panel.
    const streamingCallIdsByMessage = new Map();
    const queuedStreamingCalls = new Map();
    // Provider adapters can surface the same stream envelope more than once.
    // Never open two approval dialogs for the same destructive invocation.

    function readStoredWidthRatio() {
        try {
            const value = Number(localStorage.getItem(WIDTH_STORAGE_KEY));
            if (Number.isFinite(value)) {
                return Math.max(0.32, Math.min(0.72, value));
            }
        } catch (_) {}
        return 0.5;
    }

    function writeStoredWidthRatio(ratio) {
        try {
            localStorage.setItem(WIDTH_STORAGE_KEY, String(ratio));
        } catch (_) {}
    }

    function applyWidthRatio() {
        const ratio = Number.isFinite(state.widthRatio) ? state.widthRatio : readStoredWidthRatio();
        document.documentElement.style.setProperty('--notes-tool-preview-width', `${Math.round(window.innerWidth * ratio)}px`);
    }

    function setWidthFromPixels(width, { persist = false } = {}) {
        const viewportWidth = Math.max(window.innerWidth || 0, MIN_PANEL_WIDTH + MIN_MAIN_WIDTH);
        const maxWidth = Math.max(MIN_PANEL_WIDTH, viewportWidth - MIN_MAIN_WIDTH);
        const nextWidth = Math.max(MIN_PANEL_WIDTH, Math.min(maxWidth, Number(width) || MIN_PANEL_WIDTH));
        state.widthRatio = Math.max(0.32, Math.min(0.72, nextWidth / viewportWidth));
        applyWidthRatio();
        if (persist) writeStoredWidthRatio(state.widthRatio);
    }

    function noteTitle(content, fallback = notesT('notes_accept_untitled', 'Untitled note')) {
        return NotesRender.getNoteTitle(String(content || ''), 60) || fallback;
    }

    function isNotesToolName(name) {
        return String(name || '').trim().toLowerCase() === 'notes';
    }

    function isMessageMounted(messageId) {
        const normalizedMessageId = String(messageId || '').trim();
        return !normalizedMessageId || Boolean(document.getElementById(`a-${normalizedMessageId}`));
    }

    function parseNotesJson(rawValue) {
        if (rawValue && typeof rawValue === 'object') return rawValue;
        if (typeof rawValue !== 'string' || !rawValue.trim()) return null;
        try {
            return JSON.parse(rawValue);
        } catch (_) {
            return null;
        }
    }

    /**
     * Read a JSON string property even while the closing quote or object is not
     * available yet. This mirrors the canvas preview parser and decodes escapes
     * as they arrive, which is what makes streamed Markdown readable live.
     */
    function readStreamingJsonStringField(buffer, fieldName) {
        if (typeof buffer !== 'string' || !buffer || !fieldName) return null;
        const escapedName = String(fieldName).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const match = new RegExp(`"${escapedName}"\\s*:\\s*"`, 'i').exec(buffer);
        if (!match) return null;

        let index = match.index + match[0].length;
        let value = '';
        let escaped = false;
        while (index < buffer.length) {
            const char = buffer[index];
            index += 1;
            if (escaped) {
                switch (char) {
                    case 'n': value += '\n'; break;
                    case 'r': value += '\r'; break;
                    case 't': value += '\t'; break;
                    case 'b': value += '\b'; break;
                    case 'f': value += '\f'; break;
                    case '\\': value += '\\'; break;
                    case '/': value += '/'; break;
                    case '"': value += '"'; break;
                    case 'u': {
                        const hex = buffer.slice(index, index + 4);
                        if (/^[0-9a-fA-F]{4}$/.test(hex)) {
                            value += String.fromCharCode(parseInt(hex, 16));
                            index += 4;
                        } else {
                            value += 'u';
                        }
                        break;
                    }
                    default: value += char; break;
                }
                escaped = false;
                continue;
            }
            if (char === '\\') {
                escaped = true;
            } else if (char === '"') {
                return { value, complete: true };
            } else {
                value += char;
            }
        }
        if (escaped) value += '\\';
        return { value, complete: false };
    }

    function extractStreamingNotesArgs(rawArgs) {
        const parsed = parseNotesJson(rawArgs);
        if (parsed) {
            return {
                operation: String(parsed.type || '').trim().toLowerCase(),
                noteId: String(parsed.note_id || '').trim(),
                content: typeof parsed.content === 'string' ? parsed.content : '',
                startSnippet: typeof parsed.start_snippet === 'string' ? parsed.start_snippet : '',
                endSnippet: typeof parsed.end_snippet === 'string' ? parsed.end_snippet : '',
                hasContent: Object.prototype.hasOwnProperty.call(parsed, 'content'),
            };
        }

        const buffer = typeof rawArgs === 'string' ? rawArgs : '';
        const operation = readStreamingJsonStringField(buffer, 'type');
        const noteId = readStreamingJsonStringField(buffer, 'note_id');
        const content = readStreamingJsonStringField(buffer, 'content');
        const startSnippet = readStreamingJsonStringField(buffer, 'start_snippet');
        const endSnippet = readStreamingJsonStringField(buffer, 'end_snippet');
        return {
            operation: String(operation?.value || '').trim().toLowerCase(),
            noteId: String(noteId?.value || '').trim(),
            content: content?.value || '',
            startSnippet: startSnippet?.value || '',
            endSnippet: endSnippet?.value || '',
            hasContent: Boolean(content),
        };
    }

    function applyStreamingNoteEdit(baseContent, args) {
        const replacement = String(args?.content || '');
        const startSnippet = String(args?.startSnippet || '');
        const endSnippet = String(args?.endSnippet || '');
        if (!startSnippet || !endSnippet) return replacement;

        const source = String(baseContent || '');
        const startIndex = source.indexOf(startSnippet);
        if (startIndex < 0) return replacement;
        if (startSnippet === endSnippet) {
            return source.slice(0, startIndex) + replacement + source.slice(startIndex + startSnippet.length);
        }
        const endIndex = source.indexOf(endSnippet, startIndex + startSnippet.length);
        if (endIndex < 0) return replacement;
        return source.slice(0, startIndex) + replacement + source.slice(endIndex + endSnippet.length);
    }

    function setStatus(key, fallback, className = 'complete') {
        state.statusKey = key;
        state.statusFallback = fallback;
        state.statusClassName = className;
        if (!state.status) return;
        state.status.textContent = notesT(key, fallback);
        state.status.className = `canvas-markdown-preview-status ${className || ''}`.trim();
    }

    function refreshPanelTranslations() {
        if (!state.panel) return;

        const resizeLabel = notesT('notes_tool_resize_preview_aria', 'Resize note preview');
        const closeLabel = notesT('notes_tool_close_preview_aria', 'Close note preview');
        const viewLabel = notesT('markdown_editor_document_view', 'Document view');
        const markdownLabel = notesT('markdown_editor_tab_markdown', 'Markdown');
        const editorLabel = notesT('markdown_editor_tab_editor', 'Editor');
        const copyLabel = notesT('notes_share_copy_action', 'Copy');
        const downloadFormatLabel = notesT('notes_download_format_aria', 'Download format');
        const downloadLabel = notesT('notes_download_aria', 'Download note');

        const resizer = state.panel.querySelector('#notes-tool-PreviewResizer');
        if (resizer) {
            resizer.setAttribute('aria-label', resizeLabel);
            resizer.setAttribute('title', resizeLabel);
        }
        if (state.closeBtn) {
            state.closeBtn.setAttribute('aria-label', closeLabel);
            state.closeBtn.setAttribute('title', closeLabel);
        }
        state.panel.querySelectorAll('.canvas-markdown-editor-header-controls, .canvas-markdown-editor-view-toggle')
            .forEach((el) => el.setAttribute('aria-label', viewLabel));

        if (state.markdownTab) {
            state.markdownTab.setAttribute('aria-label', markdownLabel);
            state.markdownTab.setAttribute('title', markdownLabel);
            const label = state.markdownTab.querySelector('.canvas-markdown-editor-view-btn-label');
            if (label) label.textContent = markdownLabel;
        }
        if (state.editorTab) {
            state.editorTab.setAttribute('aria-label', editorLabel);
            state.editorTab.setAttribute('title', editorLabel);
            const label = state.editorTab.querySelector('.canvas-markdown-editor-view-btn-label');
            if (label) label.textContent = editorLabel;
        }
        if (state.copyBtn && !state.copyBtn.dataset.copyState) {
            setCopyButtonLabel(copyLabel);
        }
        if (state.downloadFormat) {
            state.downloadFormat.setAttribute('aria-label', downloadFormatLabel);
            const mdOption = state.downloadFormat.querySelector('option[value="md"]');
            const pdfOption = state.downloadFormat.querySelector('option[value="pdf"]');
            if (mdOption) mdOption.textContent = notesT('notes_download_md', 'MD');
            if (pdfOption) pdfOption.textContent = notesT('notes_download_pdf', 'PDF');
        }
        if (state.downloadBtn) {
            state.downloadBtn.setAttribute('aria-label', downloadLabel);
            state.downloadBtn.setAttribute('title', downloadLabel);
        }

        setStatus(state.statusKey, state.statusFallback, state.statusClassName);
    }

    function setHeaderButtonDisabled(button, disabled) {
        if (!button) return;
        button.disabled = Boolean(disabled);
        button.setAttribute('aria-disabled', disabled ? 'true' : 'false');
        button.classList.toggle('is-disabled', Boolean(disabled));
    }

    function setCopyButtonLabel(label) {
        if (!state.copyBtn) return;
        const nextLabel = label || notesT('notes_share_copy_action', 'Copy');
        state.copyBtn.setAttribute('aria-label', nextLabel);
        state.copyBtn.setAttribute('title', nextLabel);
    }

    function updateCopyButtonState(content = state.content) {
        const hasContent = !state.isLoading && String(content || '').length > 0;
        setHeaderButtonDisabled(state.copyBtn, !hasContent);
        if (!hasContent && state.copyBtn) {
            clearCopyFeedback();
        }
        if (state.copyBtn && !state.copyBtn.dataset.copyState) {
            setCopyButtonLabel(hasContent ? notesT('notes_share_copy_action', 'Copy') : notesT('canvas_copy_unavailable', 'Copy unavailable'));
        }
    }

    function updateHeaderControls() {
        updateCopyButtonState(getEditorValue());
    }

    function updateEditorTabs(editorState = null) {
        const view = editorState?.view === 'source' ? 'source' : 'editor';
        if (state.markdownTab) {
            state.markdownTab.classList.toggle('active', view === 'source');
            state.markdownTab.setAttribute('aria-selected', view === 'source' ? 'true' : 'false');
        }
        if (state.editorTab) {
            state.editorTab.classList.toggle('active', view === 'editor');
            state.editorTab.setAttribute('aria-selected', view === 'editor' ? 'true' : 'false');
        }
        updateHeaderControls();
    }

    function destroyEditor() {
        if (state.editor && typeof state.editor.destroy === 'function') {
            state.editor.destroy();
        }
        state.editor = null;
    }

    function nowForStreaming() {
        return typeof performance !== 'undefined' && typeof performance.now === 'function'
            ? performance.now()
            : Date.now();
    }

    function clearStreamingRenderSchedule() {
        if (state.streamingRenderTimer) {
            window.clearTimeout(state.streamingRenderTimer);
            state.streamingRenderTimer = null;
        }
        if (state.streamingScrollFrame) {
            window.cancelAnimationFrame(state.streamingScrollFrame);
            state.streamingScrollFrame = null;
        }
        state.streamingPendingMessageId = '';
    }

    /**
     * Forget an artifact tool stream without touching the currently rendered
     * note. This is used by read-only `view` calls: observing a note must never
     * replace the user's editor, discard dirty content, or move its viewport.
     */
    function clearStreamingToolCallState() {
        clearStreamingRenderSchedule();
        state.streamingRevision += 1;
        state.streamingCallId = '';
        state.streamingMessageId = '';
        state.streamingArgsBuffer = '';
        state.streamingOperation = '';
        state.streamingBaseContent = '';
        state.streamingBaseNoteId = '';
        state.streamingOriginNoteId = '';
        state.streamingOriginMessageId = '';
        state.streamingOriginContent = '';
        state.streamingOriginLastSavedContent = '';
        state.streamingOriginUpdatedAt = '';
        state.streamingOriginCanEdit = false;
        state.streamingOriginWasVisible = false;
        state.streamingOriginReferencedFiles = [];
        state.streamingOriginScrollState = null;
        state.streamingOriginSavePromise = null;
        state.streamingPreviewEl = null;
        state.streamingLastRenderAt = 0;
        state.streamingAutoFollow = true;
        state.streamingUserControlledScroll = false;
        state.streamingPreservedScrollTop = null;
        state.streamingIgnoreScrollUntil = 0;
        state.streamingTouchY = null;
    }

    function isStreamingPreviewActive() {
        return Boolean(state.streamingCallId && state.streamingOperation);
    }

    function pinStreamingScrollToVerticalAxis() {
        if (state.track && state.track.scrollLeft !== 0) {
            state.track.scrollLeft = 0;
        }
    }

    function stopStreamingAutoFollow() {
        if (!isStreamingPreviewActive()) return;
        state.streamingAutoFollow = false;
        state.streamingUserControlledScroll = true;
        state.streamingPreservedScrollTop = Math.max(Number(state.track?.scrollTop) || 0, 0);
        if (state.streamingScrollFrame) {
            window.cancelAnimationFrame(state.streamingScrollFrame);
            state.streamingScrollFrame = null;
        }
    }

    function scheduleStreamingAutoScroll() {
        if (!state.track || !state.streamingAutoFollow) return;
        if (state.streamingScrollFrame) {
            window.cancelAnimationFrame(state.streamingScrollFrame);
        }
        state.streamingScrollFrame = window.requestAnimationFrame(() => {
            state.streamingScrollFrame = null;
            if (!state.track || !state.streamingAutoFollow) return;
            state.streamingIgnoreScrollUntil = nowForStreaming() + 120;
            state.track.scrollLeft = 0;
            state.track.scrollTop = Math.max(state.track.scrollHeight - state.track.clientHeight, 0);
        });
    }

    function capturePreviewScrollState() {
        const editorScrollState = state.editor?.getScrollState?.() || null;
        const preferredScrollTop = editorScrollState
            ? (editorScrollState.view === 'source'
                ? Math.max(Number(editorScrollState.sourceScrollTop) || 0, 0)
                : Math.max(Number(editorScrollState.editorScrollTop) || 0, 0))
            : Math.max(Number(state.track?.scrollTop) || 0, 0);
        return {
            trackScrollTop: Math.max(Number(state.track?.scrollTop) || 0, 0),
            preferredScrollTop,
            editorScrollState,
        };
    }

    function restorePreviewTrackScroll(scrollTop, autoFollow, editorScrollState = null) {
        if (!state.track) return;
        const restore = () => {
            if (!state.track) return;
            state.track.scrollLeft = 0;
            state.track.scrollTop = autoFollow
                ? Math.max(state.track.scrollHeight - state.track.clientHeight, 0)
                : Math.max(Number(scrollTop) || 0, 0);
            const editorView = state.track.querySelector('.canvas-md-editor-view');
            if (editorView) {
                editorView.scrollLeft = autoFollow
                    ? 0
                    : Math.max(Number(editorScrollState?.editorScrollLeft) || 0, 0);
                editorView.scrollTop = autoFollow
                    ? Math.max(editorView.scrollHeight - editorView.clientHeight, 0)
                    : Math.max(Number(editorScrollState?.editorScrollTop ?? scrollTop) || 0, 0);
            }
            if (!autoFollow && editorScrollState && typeof state.editor?.restoreScrollState === 'function') {
                state.editor.restoreScrollState(editorScrollState);
            }
        };
        restore();
        window.requestAnimationFrame(restore);
    }

    function ensureStreamingPreviewElement() {
        ensurePanel();
        if (state.streamingPreviewEl?.isConnected) return state.streamingPreviewEl;

        destroyEditor();
        state.track.innerHTML = '';
        const preview = document.createElement('article');
        preview.className = 'notes-tool-streaming-preview canvas-markdown-render markdown-body';
        preview.setAttribute('aria-label', notesT('notes_tool_preview_title', 'Note Preview'));
        state.track.appendChild(preview);
        state.streamingPreviewEl = preview;
        return preview;
    }

    /** Render Markdown off-DOM so the visible preview can retain stable nodes. */
    function renderStreamingNotesHtml(content) {
        const markdown = String(content || '');
        if (window.ChatMarkdownBlockEditor && typeof window.ChatMarkdownBlockEditor.renderMarkdownToHtml === 'function') {
            return window.ChatMarkdownBlockEditor.renderMarkdownToHtml(markdown);
        }

        const staging = document.createElement('div');
        NotesPreview.render(staging, markdown, state.activeNoteId || 'streaming-note', []);
        return staging.innerHTML;
    }

    /**
     * Preserve the unchanged top-level Markdown prefix and replace only its
     * unstable tail. This prevents already-rendered content from flickering or
     * losing layout state while new tokens arrive below it.
     */
    function reconcileStreamingNotesPreview(target, renderedHtml) {
        if (!target) return;
        const template = document.createElement('template');
        template.innerHTML = String(renderedHtml || '');
        const currentNodes = Array.from(target.childNodes);
        const nextNodes = Array.from(template.content.childNodes);
        let stablePrefixLength = 0;

        while (
            stablePrefixLength < currentNodes.length
            && stablePrefixLength < nextNodes.length
            && currentNodes[stablePrefixLength].isEqualNode(nextNodes[stablePrefixLength])
        ) {
            stablePrefixLength += 1;
        }

        while (target.childNodes.length > stablePrefixLength) {
            target.lastChild.remove();
        }
        for (let index = 0; index < stablePrefixLength; index += 1) {
            nextNodes[index].remove();
        }
        target.appendChild(template.content);
    }

    function renderEditor(content, { editable = false, focus = false } = {}) {
        if (!state.track) return;
        const value = String(content || '');
        destroyEditor();
        state.track.innerHTML = '';

        if (window.ChatMarkdownBlockEditor && typeof window.ChatMarkdownBlockEditor.create === 'function') {
            state.editor = window.ChatMarkdownBlockEditor.create({
                value,
                editable,
                onChange: (nextValue) => handleEditorChange(nextValue),
                onSave: () => saveNow(),
                onReferenceSelection: (selectionData) => addNoteSelectionToChatReferences({
                    selectionData,
                    noteId: state.activeNoteId,
                    title: noteTitle(getEditorValue()),
                    source: 'notes tool preview',
                }),
                onStateChange: (editorState) => updateEditorTabs(editorState),
            });
            state.editor?.element?.classList.add('notes-tool-editor', 'notes-markdown-editor', 'canvas-markdown-editor-host');
            if (state.editor?.element) {
                state.track.appendChild(state.editor.element);
            }
            updateEditorTabs(state.editor?.getState?.());
            if (focus && editable) requestAnimationFrame(() => state.editor?.focus?.());
            return;
        }

        const preview = document.createElement('div');
        preview.className = 'notes-tool-preview-fallback canvas-markdown-render';
        NotesPreview.render(preview, value, state.activeNoteId, state.referencedFiles);
        state.track.appendChild(preview);
        updateHeaderControls();
    }

    function getEditorValue() {
        if (state.editor && typeof state.editor.getValue === 'function') {
            return state.editor.getValue();
        }
        return state.content;
    }

    function updateResultWidgets(noteId, { title = '', statusKey = '', statusFallback = '' } = {}) {
        const selector = `.notes-tool-result-widget[data-note-id="${CSS.escape(String(noteId || ''))}"]`;
        document.querySelectorAll(selector).forEach((widget) => {
            widget.classList.add('canvas-markdown-result-widget');
            if (title) {
                widget.dataset.noteTitle = title;
                const titleEl = widget.querySelector('.canvas-markdown-result-title');
                if (titleEl) titleEl.textContent = title;
            }
            if (statusKey || statusFallback) {
                const subEl = widget.querySelector('.canvas-markdown-result-sub');
                if (subEl) {
                    if (statusKey) subEl.setAttribute('data-i18n', statusKey);
                    subEl.textContent = notesT(statusKey || 'notes_tool_widget_status_updated', statusFallback || 'Updated note');
                }
            }
        });
    }

    /** Finalize thinking above a saved note without changing its expanded state. */
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

    function getPersistedWidgetStatus(operation) {
        const normalizedOperation = String(operation || '').trim().toLowerCase();
        if (normalizedOperation === 'edit') {
            return {
                key: 'notes_tool_widget_status_updated',
                fallback: 'Updated note',
            };
        }
        if (normalizedOperation === 'view') {
            return {
                key: 'notes_tool_widget_status_loaded',
                fallback: 'Loaded note',
            };
        }
        return {
            key: 'notes_tool_widget_status_created',
            fallback: 'Created note',
        };
    }

    /** Remove a live Notes card while preserving the assistant tool activity row. */
    function removeStreamingResultWidget(messageId, callId = state.streamingCallId) {
        const normalizedMessageId = String(messageId || state.streamingMessageId || '').trim();
        const normalizedCallId = String(callId || '').trim();
        if (!normalizedMessageId) return;
        const container = document.getElementById('a-' + normalizedMessageId);
        if (!container) return;

        const widget = normalizedCallId
            ? container.querySelector(
                `.notes-tool-result-widget[data-notes-call-id="${CSS.escape(normalizedCallId)}"]`
            )
            : null;
        widget?.closest('.assistant-widget')?.remove();
    }

    /**
     * Add the same generated-file card used by Canvas once a create stream is
     * identifiable. Edit/view calls keep only their normal tool activity row.
     */
    function injectStreamingResultWidget(messageId, { content = '', operation = '' } = {}) {
        const normalizedMessageId = String(messageId || state.streamingMessageId || '').trim();
        const callId = String(state.streamingCallId || '').trim();
        const normalizedOperation = String(operation || state.streamingOperation || '').trim().toLowerCase();
        if (!normalizedMessageId || !callId) return null;
        if (normalizedOperation !== 'create') {
            removeStreamingResultWidget(normalizedMessageId, callId);
            return null;
        }

        const container = document.getElementById('a-' + normalizedMessageId);
        if (!container) return null;

        let widget = container.querySelector(
            `.notes-tool-result-widget[data-notes-call-id="${CSS.escape(callId)}"]`
        );
        const title = noteTitle(content);
        const liveStatusKey = 'notes_tool_status_streaming';
        const liveStatus = notesT(liveStatusKey, 'Streaming note...');

        if (!widget) {
            widget = document.createElement('div');
            widget.className = 'canvas-markdown-result-widget notes-tool-result-widget';
            widget.dataset.notesCallId = callId;
            widget.dataset.noteStatus = 'generating';
            widget.dataset.noteOperation = normalizedOperation;
            widget.dataset.noteTitle = title;
            widget.innerHTML =
                '<div class="canvas-markdown-result-header">' +
                '  <div class="canvas-markdown-result-icon canvas-type-markdown" aria-hidden="true">' + (Icons.file || '') + '</div>' +
                '  <div class="canvas-markdown-result-meta">' +
                '    <div class="canvas-markdown-result-title">' + NotesUtils.escapeHtml(title) + '</div>' +
                '    <div class="canvas-markdown-result-sub" data-i18n="' + liveStatusKey + '">' + NotesUtils.escapeHtml(liveStatus) + '</div>' +
                '  </div>' +
                '</div>' +
                '<button class="canvas-markdown-result-open-btn notes-tool-result-open-btn" type="button" data-note-open="true">' +
                '  <span aria-hidden="true">' + (Icons.eye || '') + '</span>' +
                '  <span class="canvas-markdown-result-open-label">' + NotesUtils.escapeHtml(notesT('notes_tool_open_note', 'Open Note')) + '</span>' +
                '</button>';

            const wrapper = document.createElement('div');
            wrapper.className = 'assistant-widget';
            wrapper.dataset.widgetType = 'notes_result';
            wrapper.appendChild(widget);
            if (typeof appendBeforeAssistantList === 'function') {
                appendBeforeAssistantList(container, wrapper);
            } else {
                container.appendChild(wrapper);
            }
            initWidget(widget);
        }

        widget.dataset.noteTitle = title;
        widget.dataset.noteOperation = normalizedOperation;
        const titleEl = widget.querySelector('.canvas-markdown-result-title');
        if (titleEl) titleEl.textContent = title;
        const statusEl = widget.querySelector('.canvas-markdown-result-sub');
        if (statusEl) {
            statusEl.setAttribute('data-i18n', liveStatusKey);
            statusEl.textContent = liveStatus;
        }
        refreshWidgetButtons();
        return widget;
    }

    /** Upgrade the transient result card only after the persisted event arrives. */
    function finalizeStreamingResultWidget(messageId, data) {
        const normalizedMessageId = String(messageId || state.streamingMessageId || '').trim();
        if (!normalizedMessageId) return null;
        const operation = String(data?.operation || state.streamingOperation || '').trim().toLowerCase();
        if (operation !== 'create') {
            removeStreamingResultWidget(normalizedMessageId);
            return null;
        }

        // This changes "Writing note" to "Saved note" and is intentionally
        // absent from the live-widget path above.
        finalizeThinkingForMessage(normalizedMessageId);

        const container = document.getElementById('a-' + normalizedMessageId);
        if (!container) return null;
        const callId = String(state.streamingCallId || '').trim();
        let widget = callId
            ? container.querySelector(`.notes-tool-result-widget[data-notes-call-id="${CSS.escape(callId)}"]`)
            : null;
        if (!widget) {
            widget = container.querySelector('.notes-tool-result-widget[data-note-status="generating"]');
        }
        if (!widget) return null;

        const noteId = String(data?.note_id || data?.id || '').trim();
        const content = typeof data?.content === 'string' ? data.content : state.content;
        const title = noteTitle(content);
        const persistedStatus = getPersistedWidgetStatus(operation);

        widget.dataset.noteId = noteId;
        widget.dataset.noteTitle = title;
        widget.dataset.noteOperation = operation;
        widget.dataset.noteStatus = 'saved';
        delete widget.dataset.notesCallId;
        const titleEl = widget.querySelector('.canvas-markdown-result-title');
        if (titleEl) titleEl.textContent = title;
        const statusEl = widget.querySelector('.canvas-markdown-result-sub');
        if (statusEl) {
            statusEl.setAttribute('data-i18n', persistedStatus.key);
            statusEl.textContent = notesT(persistedStatus.key, persistedStatus.fallback);
        }
        refreshWidgetButtons();
        return widget;
    }

    /**
     * Register an active note with the shared generated-files dropdown in the
     * main chat header. Canvas markdown, LaTeX PDF, presentations, and notes all
     * use the same registry, which keeps the header button behavior consistent.
     */
    function registerHeaderNote(noteId, title = '') {
        const id = String(noteId || '').trim();
        const dropdown = window.canvasFilesDropdown;
        if (!id || !dropdown || typeof dropdown.registerFile !== 'function') return;

        const displayTitle = title || noteTitle(state.activeNoteId === id ? state.content : '');
        dropdown.registerFile(`note:${id}`, displayTitle, 'note', () => {
            openNote(id, { focus: false });
        });
    }

    function clearSaveTimer() {
        if (state.saveTimer) {
            window.clearTimeout(state.saveTimer);
            state.saveTimer = null;
        }
    }

    /** Update the shared Notes workspace cache after a chat-sidebar save. */
    function updateCachedNoteAfterSave(noteId, content, updated = null) {
        const existingIndex = NotesState.notes.findIndex((note) => note.id === noteId);
        if (existingIndex < 0) return;
        NotesState.notes[existingIndex] = {
            ...NotesState.notes[existingIndex],
            ...(updated && typeof updated === 'object' ? updated : {}),
            content,
        };
        NotesManager.sortNotesState();
        NotesManager.renderCurrentNotesList();
    }

    async function openSidebarConflict(noteId, baseContent, localContent, baseRevision, serverSnapshot = null) {
        if (!window.NotesConflictManager) return false;
        setStatus('notes_status_conflict', 'Conflict needs review', 'error');
        try {
            return await window.NotesConflictManager.open({
                noteId,
                baseContent,
                localContent,
                baseRevision,
                serverSnapshot,
                fetchLatest: (id) => NotesAPI.fetchNoteContent(id),
                save: (id, content, revision) => NotesAPI.updateNote(id, content, revision),
                onResolved: ({ content, updated }) => {
                    updateCachedNoteAfterSave(noteId, content, updated);
                    if (state.activeNoteId !== noteId) return;
                    state.content = content;
                    state.lastSavedContent = content;
                    state.lastSavedUpdatedAt = normalizeNoteRevisionToken(updated?.updated_at);
                    state.referencedFiles = Array.isArray(updated?.referenced_files) ? updated.referenced_files : state.referencedFiles;
                    renderEditor(content, { editable: state.canEdit });
                    setStatus('notes_status_saved', 'Saved', 'complete');
                },
                onDeferred: () => {
                    if (state.activeNoteId === noteId) setStatus('notes_status_conflict', 'Conflict needs review', 'error');
                },
            });
        } catch (error) {
            console.error('Failed to open sidebar note conflict recovery', error);
            showNotification?.(notesT('notes_conflict_load_failed', 'Could not load the latest note. Your draft remains available in the editor.'), 'error');
            return false;
        }
    }

    /**
     * Persist the latest editor snapshot before navigation tears down the DOM.
     *
     * The transition itself stays synchronous and responsive. The captured
     * note id and content no longer depend on `state` after this function
     * returns, and duplicate hide/reset calls reuse the same request.
     */
    function persistPendingEditsBeforeTeardown() {
        clearSaveTimer();
        const noteId = String(state.activeNoteId || '').trim();
        const content = String(getEditorValue() || '');
        const baseContent = String(state.lastSavedContent || '');
        const expectedUpdatedAt = state.lastSavedUpdatedAt;
        if (!state.canEdit || !noteId || content === state.lastSavedContent) {
            return state.backgroundSavePromise || Promise.resolve(true);
        }

        const signature = `${noteId}\u0000${content}`;
        if (state.backgroundSaveSignature === signature && state.backgroundSavePromise) {
            return state.backgroundSavePromise;
        }

        // The debounce may already be persisting this exact snapshot. Reuse
        // that request instead of creating an unnecessary note-history version.
        if (
            state.isSaving
            && state.activeSaveNoteId === noteId
            && state.activeSaveContent === content
            && state.activeSavePromise
        ) {
            state.backgroundSaveSignature = signature;
            const inFlightSave = state.activeSavePromise
                .then(() => true)
                .catch(async (error) => {
                    state.backgroundSaveSignature = '';
                    if (isNoteRevisionConflict(error)) {
                        await openSidebarConflict(noteId, baseContent, content, expectedUpdatedAt);
                        return false;
                    }
                    console.error('Failed to finish note save while closing chat sidebar', error);
                    showNotification?.(notesT('notes_error_save_note', 'Failed to save note'), 'error');
                    return false;
                })
                .finally(() => {
                    if (state.backgroundSavePromise === inFlightSave) {
                        state.backgroundSavePromise = null;
                    }
                });
            state.backgroundSavePromise = inFlightSave;
            return inFlightSave;
        }

        const saveSnapshot = async () => {
            let revisionForSave = expectedUpdatedAt;
            // If the regular debounce save is already writing an older
            // snapshot, serialize this newer transition snapshot after it.
            if (state.isSaving) {
                if (state.activeSaveNoteId === noteId && state.activeSavePromise) {
                    try {
                        const priorUpdatedNote = await state.activeSavePromise;
                        revisionForSave = normalizeNoteRevisionToken(priorUpdatedNote?.updated_at) || revisionForSave;
                    } catch (_) {
                        // Retry with the captured revision. A genuine conflict
                        // remains a conflict, while a transient network failure
                        // can be retried without discarding the snapshot.
                    }
                } else {
                    const settled = await waitForNoteSaveToSettle(() => state.isSaving);
                    if (!settled) {
                        throw new Error(notesT('notes_error_save_note', 'Failed to save note'));
                    }
                }
            }
            const updated = await NotesAPI.updateNote(noteId, content, revisionForSave);
            updateCachedNoteAfterSave(noteId, content, updated);

            // Settings and manual-close flows keep the same editor instance.
            // Reflect the successful background save if it is still showing
            // the captured note and has not received newer input meanwhile.
            if (state.activeNoteId === noteId && String(getEditorValue() || '') === content) {
                state.content = content;
                state.lastSavedContent = content;
                state.lastSavedUpdatedAt = normalizeNoteRevisionToken(updated?.updated_at);
                const title = noteTitle(content);
                if (state.title) state.title.textContent = title;
                setStatus('notes_status_saved', 'Saved', 'complete');
                updateResultWidgets(noteId, { title });
                registerHeaderNote(noteId, title);
            }
            return true;
        };

        state.backgroundSaveSignature = signature;
        const backgroundSave = saveSnapshot().catch(async (error) => {
            // Allow a later transition or edit to retry the same snapshot.
            if (state.backgroundSaveSignature === signature) {
                state.backgroundSaveSignature = '';
            }
            if (isNoteRevisionConflict(error)) {
                await openSidebarConflict(noteId, baseContent, content, expectedUpdatedAt);
                return false;
            }
            console.error('Failed to save note before closing chat sidebar', error);
            showNotification?.(notesT('notes_error_save_note', 'Failed to save note'), 'error');
            return false;
        }).finally(() => {
            if (state.backgroundSavePromise === backgroundSave) {
                state.backgroundSavePromise = null;
            }
        });
        state.backgroundSavePromise = backgroundSave;
        return backgroundSave;
    }

    function clearCopyFeedback() {
        if (!state.copyBtn) return;
        if (state.copyFeedbackTimer) {
            window.clearTimeout(state.copyFeedbackTimer);
            state.copyFeedbackTimer = null;
        }
        state.copyBtn.removeAttribute('data-copy-state');
    }

    async function writeTextToClipboard(text) {
        const value = String(text || '');
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(value);
            return;
        }

        const textarea = document.createElement('textarea');
        textarea.value = value;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        let copied = false;
        try {
            copied = document.execCommand('copy');
        } finally {
            textarea.remove();
        }
        if (!copied) {
            throw new Error('Clipboard copy fallback failed');
        }
    }

    function handleCopySuccess() {
        if (!state.copyBtn) return;
        clearCopyFeedback();
        state.copyBtn.dataset.copyState = 'copied';
        setCopyButtonLabel(notesT('canvas_copy_success', 'Copied!'));
        state.copyFeedbackTimer = window.setTimeout(() => {
            state.copyBtn?.removeAttribute('data-copy-state');
            setCopyButtonLabel(notesT('notes_share_copy_action', 'Copy'));
            state.copyFeedbackTimer = null;
        }, 1500);
        showNotification?.(notesT('chat_selection_copy_success', 'Copied to clipboard'), 'success');
    }

    function handleCopyFailure(error) {
        if (!state.copyBtn) return;
        clearCopyFeedback();
        state.copyBtn.dataset.copyState = 'error';
        setCopyButtonLabel(notesT('canvas_copy_failed', 'Copy failed'));
        state.copyFeedbackTimer = window.setTimeout(() => {
            state.copyBtn?.removeAttribute('data-copy-state');
            setCopyButtonLabel(notesT('notes_share_copy_action', 'Copy'));
            state.copyFeedbackTimer = null;
        }, 2000);
        showNotification?.(notesT('canvas_copy_failed', 'Copy failed'), 'error');
        console.error('Failed to copy note from chat sidebar', error);
    }

    async function copyActiveNote() {
        if (!state.copyBtn || state.copyBtn.disabled) return;
        try {
            await writeTextToClipboard(getEditorValue());
            handleCopySuccess();
        } catch (error) {
            handleCopyFailure(error);
        }
    }

    function setDownloadEnabled(enabled) {
        const canDownload = Boolean(enabled) && Boolean(state.activeNoteId) && !state.isDownloading && !state.isLoading;
        if (window.chatDownloadControls && typeof window.chatDownloadControls.setDownloadControlsEnabled === 'function') {
            window.chatDownloadControls.setDownloadControlsEnabled({
                button: state.downloadBtn,
                select: state.downloadFormat,
                enabled: canDownload,
                disabledClass: 'disabled',
                manageTabIndex: false,
            });
            return;
        }

        if (state.downloadBtn) {
            state.downloadBtn.disabled = !canDownload;
            state.downloadBtn.classList.toggle('disabled', !canDownload);
            state.downloadBtn.setAttribute('aria-disabled', canDownload ? 'false' : 'true');
        }
        if (state.downloadFormat) {
            state.downloadFormat.disabled = !canDownload;
        }
    }

    function setDownloadBusy(isBusy) {
        state.isDownloading = Boolean(isBusy);
        if (state.downloadBtn && !state.downloadDefaultHtml) {
            state.downloadDefaultHtml = state.downloadBtn.innerHTML;
        }

        if (window.chatDownloadControls && typeof window.chatDownloadControls.setDownloadBusy === 'function') {
            window.chatDownloadControls.setDownloadBusy({
                button: state.downloadBtn,
                select: state.downloadFormat,
                busy: state.isDownloading,
                enabled: Boolean(state.activeNoteId),
                defaultHtml: state.downloadDefaultHtml,
                disabledClass: 'disabled',
                manageTabIndex: false,
                busyLabel: notesT('notes_download_preparing', 'Preparing download...'),
                idleLabel: notesT('notes_download_aria', 'Download note'),
            });
            return;
        }

        setDownloadEnabled(!state.isDownloading);
    }

    async function downloadActiveNote(event) {
        event?.preventDefault?.();
        if (!state.activeNoteId || state.isDownloading) return;

        const selectedNoteId = state.activeNoteId;
        const selectedFormat = typeof window.chatDownloadControls?.getSelectedDownloadFormat === 'function'
            ? window.chatDownloadControls.getSelectedDownloadFormat(state.downloadFormat, 'md')
            : String(state.downloadFormat?.value || 'md');
        const format = String(selectedFormat || 'md').trim().toLowerCase() === 'pdf' ? 'pdf' : 'md';
        const content = String(getEditorValue() || '');
        const title = noteTitle(content);

        try {
            setDownloadBusy(true);

            if (format === 'md') {
                const filename = NotesUtils.noteDownloadFilename(title, 'md');
                NotesUtils.saveBlob(new Blob([content], { type: 'text/markdown;charset=utf-8' }), filename);
                showNotification?.(notesT('notes_download_success', 'Note downloaded.'), 'success');
                return;
            }

            if (state.canEdit && content !== state.lastSavedContent) {
                let saved = await saveNow();
                if (!saved && state.isSaving) {
                    const settled = await waitForNoteSaveToSettle(() => state.isSaving);
                    if (!settled) {
                        throw new Error(notesT('notes_error_save_note', 'Failed to save note'));
                    }
                    saved = content !== state.lastSavedContent ? await saveNow() : true;
                }
                if (!saved) throw new Error(notesT('notes_error_save_note', 'Failed to save note'));
            }

            const filename = NotesUtils.noteDownloadFilename(title, 'pdf');
            const blob = await NotesAPI.downloadNote(selectedNoteId, 'pdf');
            NotesUtils.saveBlob(blob, filename);
            showNotification?.(notesT('notes_download_success', 'Note downloaded.'), 'success');
        } catch (error) {
            console.error('Failed to download note from chat sidebar', error);
            showNotification?.(error?.message || notesT('notes_download_failed', 'Failed to prepare note download.'), 'error');
        } finally {
            setDownloadBusy(false);
            setDownloadEnabled(true);
        }
    }

    function handleEditorChange(nextValue) {
        state.content = String(nextValue || '');
        // A new edit is a new persistence snapshot even if the user returns to
        // text that was previously queued during a navigation transition.
        state.backgroundSaveSignature = '';
        if (!state.canEdit || !state.activeNoteId) return;
        if (window.NotesConflictManager?.isActiveFor?.(state.activeNoteId)) {
            window.NotesConflictManager.updateLocalDraft(state.activeNoteId, state.content);
            updateCopyButtonState(state.content);
            clearSaveTimer();
            setStatus('notes_status_conflict', 'Conflict needs review', 'error');
            return;
        }
        const dirty = state.content !== state.lastSavedContent;
        updateCopyButtonState(state.content);
        if (!dirty) {
            clearSaveTimer();
            setStatus('notes_status_saved', 'Saved', 'complete');
            return;
        }
        setStatus('notes_tool_status_unsaved', 'Unsaved changes', 'unsaved');
        clearSaveTimer();
        state.saveTimer = window.setTimeout(() => {
            saveNow().catch((error) => {
                console.error('Failed to save note from chat sidebar', error);
            });
        }, SAVE_DELAY_MS);
    }

    async function saveNow() {
        if (!state.canEdit || !state.activeNoteId || state.isSaving) return false;
        const savedNoteId = state.activeNoteId;
        const nextContent = String(getEditorValue() || '');
        const expectedUpdatedAt = state.lastSavedUpdatedAt;
        if (nextContent === state.lastSavedContent) {
            setStatus('notes_status_saved', 'Saved', 'complete');
            return true;
        }

        clearSaveTimer();
        state.isSaving = true;
        setStatus('notes_status_saving', 'Saving changes...', 'generating');
        let saveRequest = null;
        try {
            saveRequest = NotesAPI.updateNote(savedNoteId, nextContent, expectedUpdatedAt);
            state.activeSaveNoteId = savedNoteId;
            state.activeSaveContent = nextContent;
            state.activeSavePromise = saveRequest;
            const updated = await saveRequest;
            updateCachedNoteAfterSave(savedNoteId, nextContent, updated);
            if (state.activeNoteId !== savedNoteId) return true;
            state.content = nextContent;
            state.lastSavedContent = nextContent;
            state.lastSavedUpdatedAt = normalizeNoteRevisionToken(updated?.updated_at);
            const title = noteTitle(nextContent);
            if (state.title) state.title.textContent = title;
            setStatus('notes_status_saved', 'Saved', 'complete');
            updateResultWidgets(savedNoteId, {
                title,
                statusKey: 'notes_tool_widget_status_updated',
                statusFallback: 'Updated note',
            });
            registerHeaderNote(savedNoteId, title);

            return true;
        } catch (error) {
            if (isNoteRevisionConflict(error)) {
                await openSidebarConflict(savedNoteId, state.lastSavedContent, nextContent, expectedUpdatedAt);
                return false;
            }
            setStatus('notes_error_save_note', 'Failed to save note', 'error');
            throw error;
        } finally {
            if (state.activeSavePromise === saveRequest) {
                state.activeSaveNoteId = '';
                state.activeSaveContent = '';
                state.activeSavePromise = null;
            }
            state.isSaving = false;
        }
    }

    function setVisible(visible) {
        state.isVisible = Boolean(visible);
        document.body.classList.toggle('notes-tool-preview-open', state.isVisible);

        // Commit the Notes surface first. Sidebar restoration and preview
        // handoff are ancillary operations; an error in either must never
        // strand a visually open panel or leave it in the accessibility tree.
        state.panel?.classList.toggle('visible', state.isVisible);
        state.panel?.setAttribute('aria-hidden', state.isVisible ? 'false' : 'true');
        state.panel?.toggleAttribute('inert', !state.isVisible);

        if (state.isVisible) {
            applyWidthRatio();
        } else {
            stopResize();
        }

        try {
            if (typeof window.setMainSidebarAutoCollapsed === 'function') {
                // Register before hiding Canvas so a direct Canvas/Notes handoff
                // cannot restore and recollapse the main sidebar between panels.
                window.setMainSidebarAutoCollapsed('notes-preview', state.isVisible);
            } else if (state.isVisible && typeof closeSidebar === 'function') {
                closeSidebar({ persist: false });
            }
        } catch (error) {
            console.warn('Unable to update the main sidebar for the Notes preview:', error);
        }

        if (state.isVisible) {
            try {
                window.closeOtherArtifactPreviews?.('notes-preview');
            } catch (error) {
                console.warn('Unable to complete the Notes preview handoff:', error);
            }
        }
        setDownloadEnabled(state.isVisible && Boolean(state.activeNoteId));
        refreshWidgetButtons();
    }

    function hidePreviewPanel() {
        // Do not await: navigation must remain immediate, while the captured
        // snapshot continues saving independently from the sidebar DOM.
        void persistPendingEditsBeforeTeardown();
        state.dismissedPreviewMessageId = String(
            state.streamingMessageId || state.activeMessageId || ''
        ).trim();
        setVisible(false);
    }

    function refreshWidgetButtons() {
        document.querySelectorAll('.notes-tool-result-widget').forEach((widget) => {
            const noteId = String(widget.dataset.noteId || '');
            const callId = String(widget.dataset.notesCallId || '');
            const isOpen = state.isVisible && (
                (noteId && noteId === state.activeNoteId)
                || (callId && callId === state.streamingCallId)
            );
            const label = isOpen
                ? notesT('notes_tool_close_note', 'Close Note')
                : notesT('notes_tool_open_note', 'Open Note');
            widget.classList.toggle('active', isOpen);
            const labelEl = widget.querySelector('.canvas-markdown-result-open-label');
            if (labelEl) labelEl.textContent = label;
        });
    }

    async function openNote(noteId, options = {}) {
        const normalizedNoteId = String(noteId || '').trim();
        if (!normalizedNoteId) return;
        const requestedMessageId = String(options.messageId || '').trim();
        if (
            options.automatic === true
            && requestedMessageId
            && requestedMessageId === state.dismissedPreviewMessageId
        ) {
            return;
        }
        if (options.automatic !== true) {
            state.dismissedPreviewMessageId = '';
        }

        const wasStreamingPreview = Boolean(state.streamingCallId && state.track);
        const originScrollState = state.streamingOriginScrollState;
        const streamingUserControlledScroll = state.streamingUserControlledScroll;
        const streamingScrollTop = wasStreamingPreview
            ? (streamingUserControlledScroll
                ? Math.max(Number(state.track.scrollTop) || 0, 0)
                : Math.max(
                    Number(state.streamingPreservedScrollTop) || 0,
                    Number(originScrollState?.preferredScrollTop) || 0,
                    Number(state.track.scrollTop) || 0
                ))
            : 0;
        const streamingWasFollowing = wasStreamingPreview ? state.streamingAutoFollow : false;

        // Invalidate any pending base-note request from the live tool preview.
        // The persisted event below is authoritative from this point onward.
        clearStreamingRenderSchedule();
        state.streamingRevision += 1;
        state.streamingCallId = '';
        state.streamingMessageId = '';
        state.streamingArgsBuffer = '';
        state.streamingOperation = '';
        state.streamingBaseContent = '';
        state.streamingBaseNoteId = '';
        state.streamingOriginNoteId = '';
        state.streamingOriginMessageId = '';
        state.streamingOriginContent = '';
        state.streamingOriginLastSavedContent = '';
        state.streamingOriginUpdatedAt = '';
        state.streamingOriginCanEdit = false;
        state.streamingOriginWasVisible = false;
        state.streamingOriginReferencedFiles = [];
        state.streamingOriginScrollState = null;
        state.streamingOriginSavePromise = null;
        state.streamingUserControlledScroll = false;
        state.streamingPreservedScrollTop = null;
        state.streamingPreviewEl = null;
        ensurePanel();
        state.activeNoteId = normalizedNoteId;
        state.activeMessageId = requestedMessageId || (
            options.automatic === true ? state.activeMessageId : ''
        );
        refreshWidgetButtons();
        state.content = String(options.content ?? state.content ?? '');
        state.lastSavedContent = state.content;
        state.lastSavedUpdatedAt = normalizeNoteRevisionToken(options.updatedAt);
        state.canEdit = false;
        state.isLoading = true;
        state.referencedFiles = [];
        if (state.title) state.title.textContent = noteTitle(state.content);
        setStatus('notes_tool_status_loading', 'Loading note...', 'generating');
        renderEditor(state.content, { editable: false });
        if (wasStreamingPreview) {
            restorePreviewTrackScroll(
                streamingScrollTop,
                streamingWasFollowing,
                originScrollState?.editorScrollState
            );
        }
        if (!state.isVisible) setVisible(true);
        setDownloadEnabled(false);
        updateHeaderControls();

        try {
            const contentData = await NotesAPI.fetchNoteContent(normalizedNoteId);
            if (state.activeNoteId !== normalizedNoteId) return;
            state.isLoading = false;
            state.content = String(contentData?.content || '');
            state.lastSavedContent = state.content;
            state.lastSavedUpdatedAt = normalizeNoteRevisionToken(contentData?.updated_at);
            state.canEdit = contentData?.share_type !== 'live';
            state.referencedFiles = Array.isArray(contentData?.referenced_files) ? contentData.referenced_files : [];
            const title = noteTitle(state.content);
            if (state.title) state.title.textContent = title;
            const scrollTopBeforeFinalRender = state.track?.scrollTop || streamingScrollTop;
            renderEditor(state.content, { editable: state.canEdit, focus: state.canEdit && options.focus !== false });
            if (wasStreamingPreview) {
                restorePreviewTrackScroll(
                    scrollTopBeforeFinalRender,
                    streamingWasFollowing,
                    originScrollState?.editorScrollState
                );
            }
            setStatus(state.canEdit ? 'notes_status_saved' : 'notes_status_readonly', state.canEdit ? 'Saved' : 'Read-only', state.canEdit ? 'complete' : 'complete');
            updateResultWidgets(normalizedNoteId, { title });
            registerHeaderNote(normalizedNoteId, title);
            setDownloadEnabled(true);
            updateHeaderControls();
            const recovery = await window.NotesConflictManager?.getRecovery?.(normalizedNoteId);
            if (state.activeNoteId !== normalizedNoteId) return;
            if (recovery && String(recovery.localContent ?? '') !== state.content) {
                state.content = String(recovery.localContent ?? '');
                state.lastSavedContent = String(recovery.baseContent ?? '');
                state.lastSavedUpdatedAt = normalizeNoteRevisionToken(recovery.baseRevision);
                renderEditor(state.content, { editable: state.canEdit, focus: false });
                await openSidebarConflict(
                    normalizedNoteId,
                    recovery.baseContent,
                    recovery.localContent,
                    recovery.baseRevision,
                    contentData,
                );
            }
        } catch (error) {
            console.error('Failed to load notes tool preview', error);
            state.isLoading = false;
            setStatus('notes_error_fetch_note_content', 'Failed to fetch note content', 'error');
            setDownloadEnabled(false);
            updateHeaderControls();
        }
    }

    function renderStreamingPreviewNow(args, messageId = '') {
        const operation = String(args?.operation || state.streamingOperation || '').trim().toLowerCase();
        if (!['create', 'edit'].includes(operation)) return;
        if (String(messageId || '').trim() === state.dismissedPreviewMessageId) return;

        const streamedNoteId = String(args?.noteId || '').trim();
        const isEditingOriginNote = operation === 'edit'
            && Boolean(state.streamingOriginNoteId)
            && (!streamedNoteId || streamedNoteId === state.streamingOriginNoteId);
        state.streamingOperation = operation;
        state.activeMessageId = String(messageId || state.activeMessageId || '');
        state.activeNoteId = operation === 'edit'
            ? (streamedNoteId || state.streamingOriginNoteId)
            : '';
        state.canEdit = false;
        state.isLoading = true;
        state.referencedFiles = [];

        const previewContent = operation === 'edit'
            ? applyStreamingNoteEdit(state.streamingBaseContent, args)
            : String(args.content || '');
        state.content = previewContent;
        state.lastSavedContent = '';
        state.lastSavedUpdatedAt = '';

        // The chat-side file card appears before the preview opens and remains
        // the same DOM node through the saved event.
        injectStreamingResultWidget(state.activeMessageId, {
            content: previewContent,
            operation,
        });

        const hadStreamingPreview = Boolean(state.streamingPreviewEl?.isConnected);
        if (isEditingOriginNote && !state.streamingUserControlledScroll) {
            state.streamingAutoFollow = false;
            state.streamingPreservedScrollTop = Math.max(
                Number(state.streamingPreservedScrollTop) || 0,
                Number(state.streamingOriginScrollState?.preferredScrollTop) || 0
            );
        }
        const previousScrollTop = hadStreamingPreview
            ? (state.streamingUserControlledScroll
                ? state.track.scrollTop
                : Math.max(
                    Number(state.streamingPreservedScrollTop) || 0,
                    Number(state.track.scrollTop) || 0
                ))
            : (isEditingOriginNote ? state.streamingPreservedScrollTop || 0 : 0);
        const preview = ensureStreamingPreviewElement();
        state.streamingIgnoreScrollUntil = nowForStreaming() + 120;

        // The live phase deliberately uses only the stateless renderer. Creating
        // the full editor here would also rebuild its hidden source textarea,
        // line gutter, undo state, selection handlers, and Mermaid jobs for every
        // update. The persisted event replaces this surface with the real editor.
        const renderedHtml = previewContent
            ? renderStreamingNotesHtml(previewContent)
            : `<p class="notes-preview-placeholder">${NotesUtils.escapeHtml(notesT('notes_preview_empty_content', 'Nothing here yet. Start writing or insert media.'))}</p>`;
        reconcileStreamingNotesPreview(preview, renderedHtml);
        preview.setAttribute('data-rendered-raw-content', previewContent);

        pinStreamingScrollToVerticalAxis();
        if (state.streamingAutoFollow) {
            scheduleStreamingAutoScroll();
        } else {
            // Disabling scroll anchoring in CSS plus restoring the exact vertical
            // offset keeps the document stationary after the user takes control.
            state.track.scrollTop = previousScrollTop;
            window.requestAnimationFrame(() => {
                if (state.streamingPreviewEl === preview && !state.streamingAutoFollow) {
                    state.track.scrollTop = previousScrollTop;
                }
            });
        }
        if (state.title) state.title.textContent = noteTitle(previewContent);
        setStatus('notes_tool_status_streaming', 'Streaming note...', 'generating');
        if (!state.isVisible) setVisible(true);
        setDownloadEnabled(false);
        updateCopyButtonState(previewContent);
        updateHeaderControls();
        state.streamingLastRenderAt = nowForStreaming();
    }

    function flushStreamingPreview(messageId = state.streamingPendingMessageId) {
        state.streamingRenderTimer = null;
        state.streamingPendingMessageId = '';
        const args = extractStreamingNotesArgs(state.streamingArgsBuffer);
        if (args.operation) state.streamingOperation = args.operation;
        if ((args.operation || state.streamingOperation) === 'edit' && args.noteId) {
            void loadStreamingBaseNote(args.noteId, state.streamingRevision, messageId);
        }
        renderStreamingPreviewNow(args, messageId);
    }

    function scheduleStreamingPreview(messageId, { immediate = false } = {}) {
        state.streamingPendingMessageId = String(messageId || state.streamingPendingMessageId || '');
        if (immediate) {
            if (state.streamingRenderTimer) {
                window.clearTimeout(state.streamingRenderTimer);
                state.streamingRenderTimer = null;
            }
            flushStreamingPreview(state.streamingPendingMessageId);
            return;
        }
        if (state.streamingRenderTimer) return;

        const elapsed = nowForStreaming() - state.streamingLastRenderAt;
        const delay = Math.max(STREAM_RENDER_INTERVAL_MS - elapsed, 0);
        state.streamingRenderTimer = window.setTimeout(() => {
            flushStreamingPreview(state.streamingPendingMessageId);
        }, delay);
    }

    async function loadStreamingBaseNote(noteId, revision, messageId) {
        const normalizedNoteId = String(noteId || '').trim();
        if (!normalizedNoteId || state.streamingBaseNoteId === normalizedNoteId) return;

        state.streamingBaseNoteId = normalizedNoteId;
        try {
            const contentData = await NotesAPI.fetchNoteContent(normalizedNoteId);
            if (revision !== state.streamingRevision || state.streamingBaseNoteId !== normalizedNoteId) return;
            state.streamingBaseContent = String(contentData?.content || '');
            scheduleStreamingPreview(messageId, { immediate: true });
        } catch (error) {
            // A partial-edit preview can still show the streamed replacement if
            // the base note cannot be loaded. The actual tool result remains the
            // source of truth and will surface its own error if access is denied.
            console.warn('Failed to load note for live tool preview', error);
        }
    }

    function serializeStreamingArgs(rawArgs) {
        if (rawArgs === undefined || rawArgs === null) return '';
        return typeof rawArgs === 'string' ? rawArgs : JSON.stringify(rawArgs);
    }

    function resolveStreamingCallId(descriptor, messageId) {
        const normalizedMessageId = String(messageId || '');
        return String(
            descriptor?.id
            || streamingCallIdsByMessage.get(normalizedMessageId)
            || `notes:${normalizedMessageId || 'current'}`
        );
    }

    /** Retain a background panel's complete argument stream until it can own the sidebar. */
    function queueStreamingToolCall(descriptor, rawArgs, messageId, { append = false } = {}) {
        const normalizedMessageId = String(messageId || '');
        const callId = resolveStreamingCallId(descriptor, normalizedMessageId);
        streamingCallIdsByMessage.set(normalizedMessageId, callId);
        const existing = queuedStreamingCalls.get(callId) || {
            callId,
            messageId: normalizedMessageId,
            descriptor: {},
            argsBuffer: '',
        };
        const nextArgs = serializeStreamingArgs(rawArgs);
        queuedStreamingCalls.set(callId, {
            ...existing,
            messageId: normalizedMessageId,
            descriptor: {
                ...(existing.descriptor || {}),
                ...(descriptor && typeof descriptor === 'object' ? descriptor : {}),
                id: callId,
                name: 'notes',
            },
            argsBuffer: append ? `${existing.argsBuffer || ''}${nextArgs}` : nextArgs,
        });
        return callId;
    }

    /** Give the shared sidebar to the oldest still-running queued Notes call. */
    function promoteQueuedStreamingToolCall() {
        if (state.streamingCallId || queuedStreamingCalls.size === 0) return false;
        const nextCall = queuedStreamingCalls.values().next().value;
        if (!nextCall) return false;
        queuedStreamingCalls.delete(nextCall.callId);
        beginStreamingToolCall(
            nextCall.descriptor,
            nextCall.argsBuffer,
            nextCall.messageId,
            { callId: nextCall.callId }
        );
        return true;
    }

    /**
     * List and delete calls deliberately have no persisted Notes artifact event.
     * When the same assistant response starts its next Notes call, that new call
     * is the completion boundary for the prior non-artifact operation. Release
     * the shared preview slot immediately instead of leaving the useful call
     * queued until the whole response ends.
     */
    function retireSupersededNonArtifactCall(nextCallId, messageId) {
        const normalizedMessageId = String(messageId || '').trim();
        const activeOperation = String(
            state.streamingOperation
            || extractStreamingNotesArgs(state.streamingArgsBuffer).operation
            || ''
        ).trim().toLowerCase();
        const isSameResponse = Boolean(
            normalizedMessageId
            && state.streamingMessageId === normalizedMessageId
        );
        if (
            !state.streamingCallId
            || state.streamingCallId === nextCallId
            || !isSameResponse
            || !['list', 'delete'].includes(activeOperation)
        ) {
            return false;
        }

        const retiredCallId = state.streamingCallId;
        queuedStreamingCalls.delete(retiredCallId);
        if (streamingCallIdsByMessage.get(normalizedMessageId) === retiredCallId) {
            streamingCallIdsByMessage.delete(normalizedMessageId);
        }
        clearStreamingToolCallState();
        refreshWidgetButtons();
        return true;
    }

    function beginStreamingToolCall(descriptor, rawArgs, messageId, options = {}) {
        const normalizedMessageId = String(messageId || '');
        const callId = String(options.callId || resolveStreamingCallId(descriptor, normalizedMessageId));
        streamingCallIdsByMessage.set(normalizedMessageId, callId);
        queuedStreamingCalls.delete(callId);
        if (state.streamingCallId !== callId) {
            // Capture the rich editor before a create/edit stream replaces it.
            // Do not tear it down yet: the first delta can be only "{", and a
            // later field may identify this as a non-mutating view operation.
            const originNoteId = String(state.activeNoteId || '').trim();
            const originScrollState = originNoteId ? capturePreviewScrollState() : null;
            const originContent = originNoteId ? String(getEditorValue() || '') : '';
            const originLastSavedContent = originNoteId ? String(state.lastSavedContent || '') : '';
            const originUpdatedAt = originNoteId ? String(state.lastSavedUpdatedAt || '') : '';
            const originCanEdit = Boolean(originNoteId && state.canEdit);
            const originWasVisible = Boolean(state.isVisible);
            const originMessageId = String(state.activeMessageId || '');
            const originReferencedFiles = Array.isArray(state.referencedFiles)
                ? [...state.referencedFiles]
                : [];
            // Capture and start persisting the dirty editor while it is still
            // intact. The asynchronous request owns its note/content snapshot,
            // so the streaming preview can safely replace the editor afterward.
            const originSavePromise = originNoteId
                ? persistPendingEditsBeforeTeardown()
                : Promise.resolve(true);
            clearStreamingRenderSchedule();
            state.streamingRevision += 1;
            state.streamingCallId = callId;
            state.streamingMessageId = normalizedMessageId;
            state.streamingArgsBuffer = '';
            state.streamingOperation = '';
            state.streamingBaseContent = '';
            state.streamingBaseNoteId = '';
            state.streamingOriginNoteId = originNoteId;
            state.streamingOriginMessageId = originMessageId;
            state.streamingOriginContent = originContent;
            state.streamingOriginLastSavedContent = originLastSavedContent;
            state.streamingOriginUpdatedAt = originUpdatedAt;
            state.streamingOriginCanEdit = originCanEdit;
            state.streamingOriginWasVisible = originWasVisible;
            state.streamingOriginReferencedFiles = originReferencedFiles;
            state.streamingOriginScrollState = originScrollState;
            state.streamingOriginSavePromise = originSavePromise;
            state.streamingPreviewEl = null;
            state.streamingLastRenderAt = 0;
            state.streamingAutoFollow = true;
            state.streamingUserControlledScroll = false;
            state.streamingPreservedScrollTop = originScrollState?.preferredScrollTop ?? null;
            state.streamingIgnoreScrollUntil = 0;
            state.streamingTouchY = null;
        }

        if (rawArgs !== undefined && rawArgs !== null) {
            state.streamingArgsBuffer = serializeStreamingArgs(rawArgs);
        }
        scheduleStreamingPreview(messageId, { immediate: true });
    }

    function handleToolCallEvent(obj, messageId) {
        const descriptor = obj?.d || {};
        const toolName = typeof descriptor === 'string' ? descriptor : (descriptor.name || obj?.name || '');
        if (!isNotesToolName(toolName)) return;
        if (!isMessageMounted(messageId)) return;
        const normalizedDescriptor = typeof descriptor === 'object' ? descriptor : {};
        const rawArgs = typeof descriptor === 'object' ? (descriptor.args ?? obj?.c ?? {}) : (obj?.c ?? {});
        const callId = resolveStreamingCallId(normalizedDescriptor, messageId);
        retireSupersededNonArtifactCall(callId, messageId);
        if (state.streamingCallId && state.streamingCallId !== callId) {
            queueStreamingToolCall(normalizedDescriptor, rawArgs, messageId);
            return;
        }
        beginStreamingToolCall(normalizedDescriptor, rawArgs, messageId, { callId });
    }

    function handleToolCallDeltaEvent(obj, messageId) {
        const descriptor = obj?.d || {};
        const toolName = typeof descriptor === 'object' ? descriptor.name : '';
        // Some providers send the tool name and call id only on the first delta.
        // Continue the active notes call when later chunks contain only text.
        const normalizedMessageId = String(messageId || '');
        if (!isMessageMounted(normalizedMessageId)) return;
        const callId = resolveStreamingCallId(descriptor, normalizedMessageId);
        const belongsToActiveCall = state.streamingCallId === callId;
        const belongsToQueuedCall = queuedStreamingCalls.has(callId);
        if (!isNotesToolName(toolName) && !belongsToActiveCall && !belongsToQueuedCall) return;

        // Some providers begin the next call with deltas and do not send its
        // completed t_c envelope until later. Hand off here as well so list ->
        // create/edit sequences can stream immediately on every provider.
        retireSupersededNonArtifactCall(callId, normalizedMessageId);
        const isNowActiveCall = state.streamingCallId === callId;

        streamingCallIdsByMessage.set(normalizedMessageId, callId);
        const delta = typeof descriptor.delta === 'string' ? descriptor.delta : '';

        // A different split panel currently owns the shared sidebar. Preserve
        // this call's deltas without switching global preview/origin state.
        if (state.streamingCallId && !isNowActiveCall) {
            queueStreamingToolCall(descriptor, delta, normalizedMessageId, { append: true });
            return;
        }

        if (!isNowActiveCall) {
            beginStreamingToolCall(descriptor, '', normalizedMessageId, { callId });
        }
        if (!delta) return;
        state.streamingArgsBuffer += delta;
        scheduleStreamingPreview(messageId);
    }

    function handleNotesEvent(obj, messageId) {
        if (!obj || obj.t !== 'notes_evt') return;
        const data = obj.data || {};
        if (String(obj.event || '') !== 'saved') return;
        const noteId = String(data.note_id || data.id || '').trim();
        if (!noteId) return;
        const initialContent = typeof data.content === 'string' ? data.content : '';
        const operation = String(data.operation || '').trim().toLowerCase();
        const normalizedMessageId = String(messageId || '').trim();
        if (!isMessageMounted(normalizedMessageId)) {
            streamingCallIdsByMessage.delete(normalizedMessageId);
            return;
        }
        const eventCallId = String(
            streamingCallIdsByMessage.get(normalizedMessageId)
            || (state.streamingMessageId === normalizedMessageId ? state.streamingCallId : '')
        );

        // A background split panel can finish while another call owns the
        // sidebar. Its canonical widget event will render the result card; do
        // not clear or replace the active panel's preview state here.
        if (eventCallId && state.streamingCallId && eventCallId !== state.streamingCallId) {
            queuedStreamingCalls.delete(eventCallId);
            streamingCallIdsByMessage.delete(normalizedMessageId);
            registerHeaderNote(noteId, noteTitle(initialContent));
            return;
        }

        if (eventCallId) queuedStreamingCalls.delete(eventCallId);
        streamingCallIdsByMessage.delete(normalizedMessageId);

        // A model view is observational. In particular, do not call openNote:
        // that path rebuilds the editor from the server and could overwrite a
        // user's newer local edit or reset the preview's scroll position.
        if (operation === 'view') {
            clearStreamingToolCallState();
            registerHeaderNote(noteId, noteTitle(initialContent));
            refreshWidgetButtons();
            promoteQueuedStreamingToolCall();
            return;
        }

        finalizeStreamingResultWidget(messageId, data);
        registerHeaderNote(noteId, noteTitle(initialContent));
        if (normalizedMessageId === state.dismissedPreviewMessageId) {
            clearStreamingToolCallState();
            refreshWidgetButtons();
            promoteQueuedStreamingToolCall();
            return;
        }
        openNote(noteId, {
            messageId,
            content: initialContent,
            updatedAt: data.updated_at,
            focus: false,
            automatic: true,
        });
        promoteQueuedStreamingToolCall();
    }

    /**
     * Remove an unresolved live preview when its generation ends without a
     * persisted Notes event (tool validation error, cancellation, disconnect,
     * or a list/delete call that intentionally has no artifact event).
     */
    function handleStreamEnd(messageId) {
        const normalizedMessageId = String(messageId || '').trim();
        const callId = String(streamingCallIdsByMessage.get(normalizedMessageId) || '');
        if (callId && callId !== state.streamingCallId) {
            queuedStreamingCalls.delete(callId);
            streamingCallIdsByMessage.delete(normalizedMessageId);
            return true;
        }
        if (
            !state.streamingCallId
            || !normalizedMessageId
            || state.streamingMessageId !== normalizedMessageId
        ) {
            return false;
        }

        const operation = String(state.streamingOperation || '').trim().toLowerCase();
        const preview = state.streamingPreviewEl;
        const origin = {
            noteId: String(state.streamingOriginNoteId || '').trim(),
            messageId: String(state.streamingOriginMessageId || ''),
            content: String(state.streamingOriginContent || ''),
            lastSavedContent: String(state.streamingOriginLastSavedContent || ''),
            updatedAt: String(state.streamingOriginUpdatedAt || ''),
            canEdit: Boolean(state.streamingOriginCanEdit),
            wasVisible: Boolean(state.streamingOriginWasVisible),
            referencedFiles: Array.isArray(state.streamingOriginReferencedFiles)
                ? [...state.streamingOriginReferencedFiles]
                : [],
            scrollState: state.streamingOriginScrollState,
            savePromise: state.streamingOriginSavePromise,
        };
        removeStreamingResultWidget(normalizedMessageId);
        queuedStreamingCalls.delete(state.streamingCallId);
        streamingCallIdsByMessage.delete(normalizedMessageId);
        clearStreamingToolCallState();

        // List, view, and delete never replace the editor, so clearing their
        // transient classifier state is sufficient.
        if (!['create', 'edit'].includes(operation) || !preview?.isConnected) {
            promoteQueuedStreamingToolCall();
            return true;
        }

        // The streamed Markdown was never persisted. Remove it rather than
        // leaving a sidebar and file card that incorrectly claim generation is
        // still running.
        destroyEditor();
        if (state.track?.contains(preview)) state.track.innerHTML = '';

        // If streaming displaced an existing editor, restore the exact local
        // snapshot immediately. This keeps unsaved text and the user's viewport
        // available even when the background save fails or is still in flight.
        if (origin.noteId) {
            state.activeNoteId = origin.noteId;
            state.activeMessageId = origin.messageId;
            state.content = origin.content;
            state.lastSavedContent = origin.lastSavedContent;
            state.lastSavedUpdatedAt = origin.updatedAt;
            state.canEdit = origin.canEdit;
            state.isLoading = false;
            state.referencedFiles = origin.referencedFiles;
            if (state.title) state.title.textContent = noteTitle(origin.content);
            renderEditor(origin.content, { editable: origin.canEdit, focus: false });
            restorePreviewTrackScroll(
                origin.scrollState?.preferredScrollTop || origin.scrollState?.trackScrollTop || 0,
                false,
                origin.scrollState?.editorScrollState || null
            );
            const isDirty = origin.canEdit && origin.content !== origin.lastSavedContent;
            setStatus(
                isDirty ? 'notes_tool_status_unsaved' : (origin.canEdit ? 'notes_status_saved' : 'notes_status_readonly'),
                isDirty ? 'Unsaved changes' : (origin.canEdit ? 'Saved' : 'Read-only'),
                isDirty ? 'unsaved' : 'complete'
            );
            setVisible(origin.wasVisible);
            setDownloadEnabled(origin.wasVisible);
            updateHeaderControls();
            refreshWidgetButtons();
            // Keep the captured promise observed; its own completion handler
            // updates this restored editor when the same snapshot is still open.
            void Promise.resolve(origin.savePromise).catch(() => false);
            promoteQueuedStreamingToolCall();
            return true;
        }

        state.activeNoteId = '';
        state.content = '';
        state.lastSavedContent = '';
        state.lastSavedUpdatedAt = '';
        state.canEdit = false;
        state.isLoading = false;
        if (state.title) state.title.textContent = notesT('notes_tool_preview_title', 'Note Preview');
        setStatus('notes_tool_preview_waiting', 'Waiting for notes tool...', 'complete');
        setDownloadEnabled(false);
        updateHeaderControls();
        setVisible(false);
        promoteQueuedStreamingToolCall();
        return true;
    }

    function initWidget(widget) {
        if (!widget || widget.dataset.notesToolWidgetInit === 'true') return;
        widget.dataset.notesToolWidgetInit = 'true';
        widget.classList.add('canvas-markdown-result-widget');
        const noteId = String(widget.dataset.noteId || '').trim();
        if (noteId) {
            registerHeaderNote(noteId, widget.dataset.noteTitle || '');
        }
        const iconEl = widget.querySelector('.canvas-markdown-result-icon');
        if (iconEl && !iconEl.innerHTML.trim()) {
            iconEl.innerHTML = Icons.file || '';
        }
        const statusEl = widget.querySelector('.canvas-markdown-result-sub[data-i18n]');
        const statusKey = statusEl?.getAttribute('data-i18n');
        if (statusEl && statusKey) {
            statusEl.textContent = notesT(statusKey, statusEl.textContent || '');
        }
        const openIcon = widget.querySelector('.notes-tool-result-open-btn > span[aria-hidden="true"]');
        if (openIcon && !openIcon.innerHTML.trim()) {
            openIcon.innerHTML = Icons.eye || '';
        }
        const button = widget.querySelector('[data-note-open="true"]');
        button?.addEventListener('click', () => {
            const selectedNoteId = String(widget.dataset.noteId || '').trim();
            if (selectedNoteId) {
                if (state.isVisible && state.activeNoteId === selectedNoteId) {
                    hidePreviewPanel();
                } else {
                    openNote(selectedNoteId, {
                        messageId: widget.closest('.assistant-message-container')?.id?.replace(/^a-/, '') || '',
                        focus: false,
                    });
                }
                return;
            }

            const selectedCallId = String(widget.dataset.notesCallId || '').trim();
            if (!selectedCallId || selectedCallId !== state.streamingCallId) return;
            if (state.isVisible) {
                hidePreviewPanel();
                return;
            }
            state.dismissedPreviewMessageId = '';
            scheduleStreamingPreview(state.streamingMessageId, { immediate: true });
        });
        refreshWidgetButtons();
    }

    function scanForWidgets(root = document) {
        root.querySelectorAll?.('.notes-tool-result-widget:not([data-notes-tool-widget-init="true"])')
            ?.forEach((widget) => initWidget(widget));
    }

    function startResize(event) {
        if (!state.panel) return;
        event.preventDefault();
        state.resizeActive = true;
        document.body.classList.add('notes-tool-preview-resizing');
        document.addEventListener('pointermove', handleResizeMove);
        document.addEventListener('pointerup', stopResize, { once: true });
    }

    function handleResizeMove(event) {
        if (!state.resizeActive) return;
        setWidthFromPixels(window.innerWidth - event.clientX, { persist: true });
    }

    function stopResize() {
        if (!state.resizeActive) return;
        state.resizeActive = false;
        document.body.classList.remove('notes-tool-preview-resizing');
        document.removeEventListener('pointermove', handleResizeMove);
    }

    function handleResizeKey(event) {
        const keyMap = {
            ArrowLeft: RESIZE_STEP,
            ArrowRight: -RESIZE_STEP,
            Home: window.innerWidth * 0.72,
            End: window.innerWidth * 0.32,
        };
        if (!(event.key in keyMap)) return;
        event.preventDefault();
        const currentWidth = state.panel?.getBoundingClientRect?.().width || window.innerWidth * state.widthRatio;
        const nextWidth = ['Home', 'End'].includes(event.key) ? keyMap[event.key] : currentWidth + keyMap[event.key];
        setWidthFromPixels(nextWidth, { persist: true });
    }

    function ensurePanel() {
        if (state.panel) return state.panel;
        state.widthRatio = readStoredWidthRatio();

        const panel = document.createElement('div');
        panel.className = 'canvas-markdown-preview-panel notes-tool-preview-panel';
        panel.id = 'notes-tool-PreviewPanel';
        panel.setAttribute('aria-hidden', 'true');
        panel.setAttribute('inert', '');
        panel.setAttribute('data-content-type', 'markdown');
        panel.innerHTML = `
            <div class="canvas-markdown-preview-resizer notes-tool-preview-resizer" id="notes-tool-PreviewResizer" role="separator" tabindex="0" aria-orientation="vertical" aria-label="${NotesRender.escapeHtml(notesT('notes_tool_resize_preview_aria', 'Resize note preview'))}" title="${NotesRender.escapeHtml(notesT('notes_tool_resize_preview_aria', 'Resize note preview'))}"></div>
            <div class="canvas-markdown-preview-header notes-tool-preview-header">
                <div class="canvas-markdown-preview-header-left">
                    <button class="om-button" id="notes-tool-PreviewClose" type="button" aria-label="${NotesRender.escapeHtml(notesT('notes_tool_close_preview_aria', 'Close note preview'))}" title="${NotesRender.escapeHtml(notesT('notes_tool_close_preview_aria', 'Close note preview'))}">
                        ${Icons.close || ''}
                    </button>
                    <div class="canvas-markdown-preview-title-wrap">
                        <span class="canvas-markdown-preview-title" id="notes-tool-PreviewTitle">${NotesRender.escapeHtml(notesT('notes_tool_preview_title', 'Note Preview'))}</span>
                        <span class="canvas-markdown-preview-status" id="notes-tool-PreviewStatus">${NotesRender.escapeHtml(notesT('notes_tool_preview_waiting', 'Waiting for notes tool...'))}</span>
                    </div>
                </div>
                <div class="canvas-markdown-preview-header-right">
                    <div class="canvas-markdown-editor-header-controls" role="toolbar" aria-label="${NotesRender.escapeHtml(notesT('markdown_editor_document_view', 'Document view'))}">
                        <div class="canvas-markdown-editor-view-toggle" role="tablist" aria-label="${NotesRender.escapeHtml(notesT('markdown_editor_document_view', 'Document view'))}">
                            <button class="canvas-markdown-editor-view-btn" id="notes-tool-MarkdownTab" type="button" role="tab" aria-selected="false" aria-label="${NotesRender.escapeHtml(notesT('markdown_editor_tab_markdown', 'Markdown'))}" title="${NotesRender.escapeHtml(notesT('markdown_editor_tab_markdown', 'Markdown'))}">
                                <span class="canvas-markdown-editor-view-btn-icon" aria-hidden="true">${Icons.list || ''}</span>
                                <span class="canvas-markdown-editor-view-btn-label">${NotesRender.escapeHtml(notesT('markdown_editor_tab_markdown', 'Markdown'))}</span>
                            </button>
                            <button class="canvas-markdown-editor-view-btn active" id="notes-tool-EditorTab" type="button" role="tab" aria-selected="true" aria-label="${NotesRender.escapeHtml(notesT('markdown_editor_tab_editor', 'Editor'))}" title="${NotesRender.escapeHtml(notesT('markdown_editor_tab_editor', 'Editor'))}">
                                <span class="canvas-markdown-editor-view-btn-icon" aria-hidden="true">${Icons.edit || ''}</span>
                                <span class="canvas-markdown-editor-view-btn-label">${NotesRender.escapeHtml(notesT('markdown_editor_tab_editor', 'Editor'))}</span>
                            </button>
                        </div>
                    </div>
                    <button class="om-button notes-tool-preview-copy-btn is-disabled" id="notes-tool-CopyBtn" type="button" aria-label="${NotesRender.escapeHtml(notesT('notes_share_copy_action', 'Copy'))}" title="${NotesRender.escapeHtml(notesT('notes_share_copy_action', 'Copy'))}" aria-disabled="true" disabled>
                        ${Icons.copy || ''}
                    </button>
                    <div class="notes-tool-preview-download-controls slide-presentation-preview-download-controls">
                        <select class="notes-tool-preview-download-select slide-presentation-preview-download-select" id="notes-tool-DownloadFormat" aria-label="${NotesRender.escapeHtml(notesT('notes_download_format_aria', 'Download format'))}" disabled>
                            <option value="md" data-i18n="notes_download_md">${NotesRender.escapeHtml(notesT('notes_download_md', 'MD'))}</option>
                            <option value="pdf" data-i18n="notes_download_pdf">${NotesRender.escapeHtml(notesT('notes_download_pdf', 'PDF'))}</option>
                        </select>
                        <button type="button" class="om-button notes-tool-preview-download-btn disabled" id="notes-tool-PreviewDownload" aria-label="${NotesRender.escapeHtml(notesT('notes_download_aria', 'Download note'))}" title="${NotesRender.escapeHtml(notesT('notes_download_aria', 'Download note'))}" aria-disabled="true" disabled>
                            ${Icons.download || ''}
                        </button>
                    </div>
                </div>
            </div>
            <div class="canvas-markdown-preview-workspace canvas-markdown-preview-track notes-tool-preview-track" id="notes-tool-PreviewTrack"></div>
        `;
        document.body.appendChild(panel);
        state.panel = panel;
        state.track = panel.querySelector('#notes-tool-PreviewTrack');
        state.title = panel.querySelector('#notes-tool-PreviewTitle');
        state.status = panel.querySelector('#notes-tool-PreviewStatus');
        state.closeBtn = panel.querySelector('#notes-tool-PreviewClose');
        state.markdownTab = panel.querySelector('#notes-tool-MarkdownTab');
        state.editorTab = panel.querySelector('#notes-tool-EditorTab');
        state.copyBtn = panel.querySelector('#notes-tool-CopyBtn');
        state.downloadFormat = panel.querySelector('#notes-tool-DownloadFormat');
        state.downloadBtn = panel.querySelector('#notes-tool-PreviewDownload');

        // Bind the primary dismissal action before any optional enhancement or
        // editor setup. If a later integration fails, the lazily appended panel
        // must still remain closable.
        state.closeBtn?.addEventListener('click', hidePreviewPanel);

        // Tool-created note previews are appended lazily, so enhance their
        // download control only after the sidebar markup exists in the DOM.
        window.chatDownloadControls?.enhanceDownloadFormatSelect?.(state.downloadFormat, {
            downloadButton: state.downloadBtn,
        });

        state.track?.addEventListener('scroll', () => {
            // The preview track never owns horizontal scrolling. Wide code,
            // tables, and formulas may scroll inside their own elements only.
            pinStreamingScrollToVerticalAxis();
            if (!isStreamingPreviewActive() || nowForStreaming() <= state.streamingIgnoreScrollUntil) return;
            const remaining = Math.max(state.track.scrollHeight - state.track.clientHeight - state.track.scrollTop, 0);
            if (remaining > STREAM_SCROLL_BOTTOM_THRESHOLD) stopStreamingAutoFollow();
        }, { passive: true });
        state.track?.addEventListener('wheel', (event) => {
            if (event.deltaY < 0) stopStreamingAutoFollow();
            if (event.deltaX) window.requestAnimationFrame(pinStreamingScrollToVerticalAxis);
        }, { passive: true });
        state.track?.addEventListener('pointerdown', (event) => {
            const rect = state.track.getBoundingClientRect();
            if (event.clientX >= rect.right - 18) stopStreamingAutoFollow();
        }, { passive: true });
        state.track?.addEventListener('touchstart', (event) => {
            state.streamingTouchY = event.touches?.[0]?.clientY ?? null;
        }, { passive: true });
        state.track?.addEventListener('touchmove', (event) => {
            const nextY = event.touches?.[0]?.clientY ?? null;
            if (nextY !== null && state.streamingTouchY !== null && nextY > state.streamingTouchY) {
                stopStreamingAutoFollow();
            }
            state.streamingTouchY = nextY;
        }, { passive: true });
        panel.addEventListener('keydown', (event) => {
            if (['ArrowUp', 'PageUp', 'Home'].includes(event.key)) stopStreamingAutoFollow();
        });

        state.markdownTab?.addEventListener('click', () => {
            state.editor?.switchView?.('source');
            updateEditorTabs(state.editor?.getState?.());
        });
        state.editorTab?.addEventListener('click', () => {
            state.editor?.switchView?.('editor');
            updateEditorTabs(state.editor?.getState?.());
        });
        state.copyBtn?.addEventListener('click', copyActiveNote);
        const resizer = panel.querySelector('#notes-tool-PreviewResizer');
        resizer?.addEventListener('pointerdown', startResize);
        resizer?.addEventListener('keydown', handleResizeKey);
        state.downloadBtn?.addEventListener('click', downloadActiveNote);
        applyWidthRatio();
        setDownloadEnabled(false);
        return panel;
    }

    function reset() {
        // `hidePreviewPanel()` and `reset()` often run back-to-back. The
        // snapshot signature deduplicates those calls while preserving edits.
        void persistPendingEditsBeforeTeardown();
        clearCopyFeedback();
        clearStreamingRenderSchedule();
        destroyEditor();
        state.activeNoteId = '';
        state.activeMessageId = '';
        state.content = '';
        state.lastSavedContent = '';
        state.lastSavedUpdatedAt = '';
        state.canEdit = false;
        state.isLoading = false;
        state.isDownloading = false;
        state.referencedFiles = [];
        state.streamingRevision += 1;
        state.streamingCallId = '';
        state.streamingMessageId = '';
        state.streamingArgsBuffer = '';
        state.streamingOperation = '';
        state.streamingBaseContent = '';
        state.streamingBaseNoteId = '';
        state.streamingOriginNoteId = '';
        state.streamingOriginMessageId = '';
        state.streamingOriginContent = '';
        state.streamingOriginLastSavedContent = '';
        state.streamingOriginUpdatedAt = '';
        state.streamingOriginCanEdit = false;
        state.streamingOriginWasVisible = false;
        state.streamingOriginReferencedFiles = [];
        state.streamingOriginScrollState = null;
        state.streamingOriginSavePromise = null;
        state.streamingPreviewEl = null;
        state.streamingLastRenderAt = 0;
        state.streamingAutoFollow = true;
        state.streamingUserControlledScroll = false;
        state.streamingPreservedScrollTop = null;
        state.streamingIgnoreScrollUntil = 0;
        state.streamingTouchY = null;
        state.dismissedPreviewMessageId = '';
        streamingCallIdsByMessage.clear();
        queuedStreamingCalls.clear();
        setVisible(false);
    }

    window.addEventListener('resize', () => {
        if (state.isVisible) applyWidthRatio();
    }, { passive: true });

    document.addEventListener('i18n:updated', () => {
        refreshPanelTranslations();
        refreshWidgetButtons();
        document.querySelectorAll('.notes-tool-result-widget .canvas-markdown-result-sub[data-i18n]').forEach((statusEl) => {
            const key = statusEl.getAttribute('data-i18n');
            if (key) statusEl.textContent = notesT(key, statusEl.textContent || '');
        });
    });

    if (typeof MutationObserver !== 'undefined') {
        const observer = new MutationObserver((mutations) => {
            for (const mutation of mutations) {
                mutation.addedNodes.forEach((node) => {
                    if (node?.nodeType !== Node.ELEMENT_NODE) return;
                    if (node.classList?.contains('notes-tool-result-widget')) {
                        initWidget(node);
                    } else {
                        scanForWidgets(node);
                    }
                });
            }
        });
        const startWidgetObserver = () => {
            const chatArea = document.getElementById('chatAreaContainer');
            if (chatArea) observer.observe(chatArea, { childList: true, subtree: true });
            scanForWidgets(chatArea || document);
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', startWidgetObserver, { once: true });
        } else {
            startWidgetObserver();
        }
    }

    return {
        handleToolCallEvent,
        handleToolCallDeltaEvent,
        handleNotesEvent,
        handleStreamEnd,
        // Chat sends await this snapshot before starting model/tool execution,
        // ensuring an assistant edit observes the user's latest durable note.
        flushPendingEdits: persistPendingEditsBeforeTeardown,
        openNote,
        hidePreviewPanel,
        scanForWidgets,
        reset,
    };
})();
