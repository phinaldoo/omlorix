function hashCodeBlockSource(value) {
    const source = String(value || '');
    let hash = 0;
    for (let i = 0; i < source.length; i += 1) {
        hash = ((hash << 5) - hash + source.charCodeAt(i)) | 0;
    }
    return Math.abs(hash).toString(36);
}

function getCodeBlockSource(wrapper) {
    if (!wrapper) {
        return '';
    }
    const codeElement = wrapper.querySelector('code[data-code-id]');
    if (!codeElement) {
        return '';
    }
    const codeId = codeElement.getAttribute('data-code-id') || '';
    if (codeId && codeSnippetRegistry.has(codeId)) {
        return String(codeSnippetRegistry.get(codeId) || '');
    }
    return String(codeElement.textContent || '');
}

function getCodeBlockEditor(wrapper) {
    if (!(wrapper instanceof Element)) {
        return null;
    }
    return wrapper.querySelector('.code-block-inline-editor');
}

function syncCodeBlockSource(wrapper, source) {
    if (!(wrapper instanceof Element)) {
        return;
    }
    const normalizedSource = String(source || '');
    const sourceChanged = getCodeBlockSource(wrapper) !== normalizedSource;
    if (sourceChanged) {
        // Permission grants describe one exact source revision. Reset them
        // before storing edits so changed HTML returns to the safe baseline.
        resetHtmlPreviewPermissions(wrapper);
    }
    const codeElement = wrapper.querySelector('code[data-code-id]');
    if (codeElement) {
        const codeId = String(codeElement.getAttribute('data-code-id') || '');
        if (codeId) {
            codeSnippetRegistry.set(codeId, normalizedSource);
        }
        if (codeElement.textContent !== normalizedSource) {
            codeElement.textContent = normalizedSource;
        }
    }
    const editor = getCodeBlockEditor(wrapper);
    if (editor && editor.value !== normalizedSource) {
        editor.value = normalizedSource;
    }
}

function scheduleCodeBlockLivePreview(wrapper) {
    if (!(wrapper instanceof Element) || !wrapper.dataset.previewKind) {
        return;
    }
    if (wrapper._codeBlockLivePreviewTimer) {
        clearTimeout(wrapper._codeBlockLivePreviewTimer);
    }
    wrapper._codeBlockLivePreviewTimer = setTimeout(() => {
        wrapper._codeBlockLivePreviewTimer = null;
        const previewPane = wrapper.querySelector('.code-block-preview-pane');
        if (previewPane instanceof Element) {
            previewPane.dataset.previewHash = '';
            previewPane.dataset.previewState = 'idle';
        }
        ensureCodeBlockPreview(wrapper);
    }, 140);
}

function setCodeBlockEditMode(wrapper, isEditing, options = {}) {
    if (!(wrapper instanceof Element)) {
        return;
    }
    const editing = Boolean(isEditing);
    const shouldFocus = options.focus !== false;
    const codePanel = wrapper.querySelector('.code-block-panel-code');
    const codePre = codePanel?.querySelector('pre');
    const editor = getCodeBlockEditor(wrapper);
    const editButton = wrapper.querySelector('.edit-code-btn');

    wrapper.dataset.editing = editing ? 'true' : 'false';
    wrapper.classList.toggle('is-editing', editing);

    if (codePre instanceof Element) {
        codePre.hidden = editing;
    }
    if (editor instanceof HTMLTextAreaElement) {
        editor.hidden = !editing;
        editor.disabled = !editing;
        if (editing) {
            editor.readOnly = false;
            if (shouldFocus) {
                try {
                    editor.focus({ preventScroll: true });
                } catch (_) {
                    editor.focus();
                }
                editor.selectionStart = editor.value.length;
                editor.selectionEnd = editor.value.length;
            }
        }
    }

    if (editButton instanceof HTMLButtonElement) {
        editButton.classList.toggle('is-active', editing);
        editButton.innerHTML = editing ? MARKDOWN_DONE_SVG : (Icons?.edit || MARKDOWN_EDIT_SVG);
        editButton.title = editing ? 'Apply edits' : 'Edit code';
        editButton.setAttribute('aria-label', editing ? 'Apply edits' : 'Edit code');
    }

    if (editing) {
        setCodeBlockView(wrapper, 'code');
    } else {
        const highlightRoot = codePanel || wrapper;
        applySyntaxHighlighting(highlightRoot);
        if (wrapper.dataset.previewKind) {
            scheduleCodeBlockLivePreview(wrapper);
        }
    }
}

function handleCodeBlockEditorInput(editor) {
    if (!(editor instanceof HTMLTextAreaElement)) {
        return;
    }
    const wrapper = editor.closest('.code-block-wrapper');
    if (!(wrapper instanceof Element)) {
        return;
    }
    syncCodeBlockSource(wrapper, editor.value);
    scheduleCodeBlockLivePreview(wrapper);
}

function isPythonExecutionLanguage(language) {
    const normalized = String(language || '').trim().toLowerCase();
    return normalized === 'python' || normalized === 'python3' || normalized === 'py' || normalized === 'py3';
}

function canRunPythonCodeBlocks() {
    return typeof window !== 'undefined'
        && typeof window.authedFetch === 'function'
        && !isChatViewReadOnly();
}

function getCurrentChatIdForCodeExecution() {
    const chatContainer = document.getElementById('chatContainer');
    return String(chatContainer?.getAttribute('data-chat-id') || '').trim();
}

function getRunCodeButtonMarkup(isRunning = false) {
    const iconHtml = isRunning
        ? '<span class="code-action-btn-spinner" aria-hidden="true"></span>'
        : MARKDOWN_RUN_SVG;
    const { key, fallback } = getRunCodeButtonTextConfig(isRunning);
    const label = escapeHtml(getCodeBlockActionLabel(key, fallback));
    return `${iconHtml}<span class="code-action-btn-label" data-i18n="${key}">${label}</span>`;
}

function setRunCodeButtonState(button, isRunning) {
    if (!(button instanceof HTMLButtonElement)) {
        return;
    }
    const running = Boolean(isRunning);
    const streamLocked = button.dataset.streamLocked === 'true';
    const disabled = running || streamLocked;
    button.dataset.running = running ? 'true' : 'false';
    button.innerHTML = getRunCodeButtonMarkup(running);
    button.disabled = disabled;
    button.classList.toggle('is-running', running);
    button.classList.toggle('is-stream-locked', streamLocked && !running);
    button.setAttribute('aria-busy', running ? 'true' : 'false');
    button.setAttribute('aria-disabled', disabled ? 'true' : 'false');
    const { key, fallback } = getRunCodeButtonA11yConfig(running, streamLocked);
    const title = getCodeBlockActionLabel(key, fallback);
    button.setAttribute('title', title);
    button.setAttribute('aria-label', title);
    button.setAttribute('data-i18n-attr', `aria-label:${key};title:${key}`);
}

function ensureCodeExecutionResultsContainer(wrapper) {
    if (!(wrapper instanceof Element)) {
        return null;
    }
    let container = wrapper.querySelector('.code-execution-results');
    if (!container) {
        container = document.createElement('div');
        container.className = 'code-execution-results';
        container.hidden = true;
        wrapper.appendChild(container);
    }
    return container;
}

function formatCodeExecutionDuration(seconds) {
    const numeric = Number(seconds);
    if (!Number.isFinite(numeric) || numeric < 0) {
        return '';
    }
    if (numeric < 1) {
        return `${Math.round(numeric * 1000)} ms`;
    }
    if (numeric < 10) {
        return `${numeric.toFixed(2)} s`;
    }
    return `${numeric.toFixed(1)} s`;
}

function createCodeExecutionFileDescriptor(file) {
    const normalized = file && typeof file === 'object' ? file : {};
    const fileId = String(normalized.file_id || normalized.id || '').trim();
    const fileName = String(normalized.name || normalized.file_name || normalized.original_name || normalized.original_filename || 'output.bin').trim() || 'output.bin';
    // Keep code-execution SVGs on the same rendering path as streamed and
    // historical assistant files, even if execution storage reports a generic
    // MIME type.
    const mimeType = resolveAssistantFileType(
        normalized.mime_type || normalized.file_type || 'application/octet-stream',
        fileName,
    ) || 'application/octet-stream';
    const fileSize = Number(normalized.size);
    return {
        file_id: fileId,
        id: fileId,
        file_type: mimeType,
        file_size: Number.isFinite(fileSize) ? fileSize : 0,
        original_name: fileName,
        original_filename: fileName,
        meta: {
            original_filename: fileName,
            mime_type: mimeType,
            file_size: Number.isFinite(fileSize) ? fileSize : 0,
            origin: 'assistant',
            code_execution: true,
        },
    };
}

function createCodeExecutionImagePreview(file) {
    const normalizedFile = createCodeExecutionFileDescriptor(file);
    if (!normalizedFile.file_id) {
        return null;
    }

    const wrapper = document.createElement('div');
    wrapper.className = 'assistant-inline-image code-execution-image-preview';
    wrapper.dataset.fileId = normalizedFile.file_id;
    wrapper.dataset.fileType = String(normalizedFile.file_type || '').toLowerCase();

    const image = document.createElement('img');
    image.className = 'assistant-inline-image-img';
    image.alt = normalizedFile.original_filename || normalizedFile.original_name || getChatPreviewTranslation('chat_generated_image_alt', 'Generated image');
    image.loading = 'lazy';

    const downloadBtn = document.createElement('button');
    downloadBtn.type = 'button';
    downloadBtn.className = 'assistant-inline-image-download';
    const downloadImageLabel = getChatPreviewTranslation('chat_download_image', 'Download image');
    downloadBtn.title = downloadImageLabel;
    downloadBtn.setAttribute('aria-label', downloadImageLabel);
    downloadBtn.innerHTML = MARKDOWN_DOWNLOAD_SVG;

    downloadBtn.addEventListener('click', async (event) => {
        event.preventDefault();
        event.stopPropagation();
        try {
            await downloadChatFileById(normalizedFile.file_id, normalizedFile.original_filename || 'image');
        } catch (error) {
            console.error('Failed to download code execution image', error);
            notifyError?.(getChatPreviewTranslation('chat_failed_download_image', 'Failed to download image'));
        }
    });

    image.addEventListener('load', () => {
        wrapper.classList.add('loaded');
    });

    image.addEventListener('error', () => {
        const fallback = createAssistantFileFallback?.(normalizedFile.file_id, normalizedFile);
        if (fallback && wrapper.parentElement) {
            wrapper.parentElement.replaceChild(fallback, wrapper);
        }
    });

    wrapper.appendChild(image);
    wrapper.appendChild(downloadBtn);

    try {
        attachPreviewToInlineImage?.(wrapper, normalizedFile);
    } catch (_) {}

    if (typeof loadAssistantImageWithAuth === 'function') {
        loadAssistantImageWithAuth(image, normalizedFile.file_id);
    }

    return wrapper;
}

function createCodeExecutionOutputNode(file) {
    const normalizedFile = createCodeExecutionFileDescriptor(file);
    if (!normalizedFile.file_id) {
        return null;
    }
    if (isDisplayableImageType(normalizedFile.file_type)) {
        return createCodeExecutionImagePreview(normalizedFile);
    }
    return createAssistantFileFallback?.(normalizedFile.file_id, normalizedFile) || null;
}

function appendCodeExecutionTextSection(parent, title, text, modifier = '') {
    const normalizedText = String(text || '');
    if (!normalizedText) {
        return;
    }
    const section = document.createElement('section');
    section.className = `code-execution-text-section${modifier ? ` ${modifier}` : ''}`;

    const heading = document.createElement('div');
    heading.className = 'code-execution-text-heading';
    heading.textContent = title;

    const pre = document.createElement('pre');
    pre.className = 'code-execution-text-pre';
    pre.textContent = normalizedText;

    section.appendChild(heading);
    section.appendChild(pre);
    parent.appendChild(section);
}

function renderCodeExecutionLoadingState(wrapper) {
    const container = ensureCodeExecutionResultsContainer(wrapper);
    if (!(container instanceof Element)) {
        return;
    }
    container.hidden = false;
    container.dataset.state = 'running';
    container.innerHTML = `
        <div class="code-execution-results-header">
            <span class="code-execution-status-pill is-running">
                <span class="code-action-btn-spinner" aria-hidden="true"></span>
                <span>${escapeHtml(getChatPreviewTranslation('code_block_running_python_short', 'Running Python'))}</span>
            </span>
        </div>
        <div class="code-execution-results-empty">${escapeHtml(getChatPreviewTranslation('code_block_executing_backend_sandbox', 'Executing this code block in the backend sandbox...'))}</div>
    `;
}

function renderCodeExecutionResult(wrapper, payload) {
    const container = ensureCodeExecutionResultsContainer(wrapper);
    if (!(container instanceof Element)) {
        return;
    }

    const safePayload = payload && typeof payload === 'object' ? payload : {};
    const files = Array.isArray(safePayload.files) ? safePayload.files : [];
    const available = safePayload.available !== false;
    const timedOut = Boolean(safePayload.timed_out);
    const hasError = Boolean(safePayload.error);
    const state = !available
        ? 'unavailable'
        : (timedOut || hasError ? 'error' : 'success');

    container.hidden = false;
    container.dataset.state = state;
    container.innerHTML = '';

    const header = document.createElement('div');
    header.className = 'code-execution-results-header';

    const statusPill = document.createElement('span');
    statusPill.className = `code-execution-status-pill is-${state}`;
    statusPill.textContent = !available
        ? 'Code execution unavailable'
        : (timedOut ? 'Execution timed out' : (hasError ? 'Execution finished with errors' : 'Execution completed'));
    header.appendChild(statusPill);

    const meta = document.createElement('div');
    meta.className = 'code-execution-results-meta';
    const metaParts = [];
    const durationLabel = formatCodeExecutionDuration(safePayload.execution_time);
    if (durationLabel) {
        metaParts.push(durationLabel);
    }
    if (files.length) {
        metaParts.push(`${files.length} file${files.length === 1 ? '' : 's'}`);
    }
    if (safePayload.execution_id) {
        metaParts.push(`run ${String(safePayload.execution_id).slice(0, 8)}`);
    }
    meta.textContent = metaParts.join(' • ');
    if (meta.textContent) {
        header.appendChild(meta);
    }
    container.appendChild(header);

    const body = document.createElement('div');
    body.className = 'code-execution-results-body';
    container.appendChild(body);

    if (safePayload.error) {
        appendCodeExecutionTextSection(body, 'Error', safePayload.error, 'is-error');
    }
    if (safePayload.stdout) {
        appendCodeExecutionTextSection(body, 'Output', safePayload.stdout, 'is-stdout');
    }
    if (safePayload.stderr) {
        appendCodeExecutionTextSection(body, 'Standard error', safePayload.stderr, 'is-stderr');
    }

    if (files.length) {
        const filesSection = document.createElement('section');
        filesSection.className = 'code-execution-files-section';

        const filesHeading = document.createElement('div');
        filesHeading.className = 'code-execution-text-heading';
        filesHeading.textContent = files.length === 1 ? 'Generated file' : 'Generated files';
        filesSection.appendChild(filesHeading);

        const filesGrid = document.createElement('div');
        filesGrid.className = 'code-execution-files-grid';

        files.forEach((file) => {
            const node = createCodeExecutionOutputNode(file);
            if (node) {
                filesGrid.appendChild(node);
            }
        });

        if (filesGrid.childElementCount > 0) {
            filesSection.appendChild(filesGrid);
            body.appendChild(filesSection);
        }
    }

    if (!body.childElementCount) {
        const empty = document.createElement('div');
        empty.className = 'code-execution-results-empty';
        empty.textContent = available
            ? 'Execution finished with no text output or generated files.'
            : 'The code execution service is unavailable right now.';
        body.appendChild(empty);
    }
}

async function extractExecutionErrorMessage(response) {
    if (!(response instanceof Response)) {
        return 'Failed to execute Python code.';
    }
    const contentType = String(response.headers.get('content-type') || '').toLowerCase();
    try {
        if (contentType.includes('application/json')) {
            const payload = await response.json();
            if (typeof payload?.detail === 'string' && payload.detail.trim()) {
                return payload.detail.trim();
            }
            if (typeof payload?.error === 'string' && payload.error.trim()) {
                return payload.error.trim();
            }
        } else {
            const text = await response.text();
            if (text && text.trim()) {
                return text.trim();
            }
        }
    } catch (_) {}
    return `Failed to execute Python code (${response.status}).`;
}

async function runMarkdownPythonCodeBlock(wrapper, button) {
    if (
        !(wrapper instanceof Element)
        || !(button instanceof HTMLButtonElement)
        || button.disabled
        || button.dataset.streamLocked === 'true'
        || isCodeBlockInStreamingContext(wrapper)
    ) {
        return;
    }

    const code = getCodeBlockSource(wrapper);
    if (!String(code || '').trim()) {
        renderCodeExecutionResult(wrapper, {
            available: true,
            error: 'This code block is empty.',
            error_type: 'validation_error',
        });
        return;
    }

    setRunCodeButtonState(button, true);
    renderCodeExecutionLoadingState(wrapper);

    try {
        const response = await window.authedFetch(CODE_EXECUTION_MARKDOWN_ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                code,
                chat_id: getCurrentChatIdForCodeExecution() || null,
            }),
        });

        if (!response.ok) {
            throw new Error(await extractExecutionErrorMessage(response));
        }

        const payload = await response.json();
        renderCodeExecutionResult(wrapper, payload);
    } catch (error) {
        console.error('Failed to execute markdown Python block', error);
        renderCodeExecutionResult(wrapper, {
            available: true,
            error: String(error?.message || error || 'Failed to execute Python code.'),
            error_type: 'request_error',
        });
    } finally {
        setRunCodeButtonState(button, false);
    }
}

function readCodeBlockViewState(host) {
    if (!(host instanceof Element)) {
        return {};
    }
    const raw = host.dataset.codeBlockViewState;
    if (!raw) {
        return {};
    }
    try {
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_) {
        return {};
    }
}

function writeCodeBlockViewState(host, state) {
    if (!(host instanceof Element)) {
        return;
    }
    try {
        host.dataset.codeBlockViewState = JSON.stringify(state || {});
    } catch (_) {
        host.dataset.codeBlockViewState = '{}';
    }
}

function readCodeBlockCollapseState(host) {
    if (!(host instanceof Element)) {
        return {};
    }
    const raw = host.dataset.codeBlockCollapseState;
    if (!raw) {
        return {};
    }
    try {
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_) {
        return {};
    }
}

function writeCodeBlockCollapseState(host, state) {
    if (!(host instanceof Element)) {
        return;
    }
    try {
        host.dataset.codeBlockCollapseState = JSON.stringify(state || {});
    } catch (_) {
        host.dataset.codeBlockCollapseState = '{}';
    }
}

function getCodeBlockStateHost(element) {
    if (!(element instanceof Element)) {
        return null;
    }
    const directHost = element.closest('.assistant-message-content, .user-message-content, .markdown-body');
    if (directHost) {
        return directHost;
    }
    if (typeof element.querySelector === 'function') {
        const nestedWrapper = element.querySelector('.code-block-wrapper');
        if (nestedWrapper instanceof Element) {
            return nestedWrapper.closest('.assistant-message-content, .user-message-content, .markdown-body') || element;
        }
    }
    return element;
}

function getCodeBlockWrappersForHost(root, host) {
    if (!(root instanceof Element) || typeof root.querySelectorAll !== 'function') {
        return [];
    }
    const resolvedHost = host instanceof Element ? host : getCodeBlockStateHost(root);
    if (!(resolvedHost instanceof Element)) {
        return [];
    }
    return Array.from(root.querySelectorAll('.code-block-wrapper')).filter(
        (wrapper) => getCodeBlockStateHost(wrapper) === resolvedHost
    );
}

function getChatScrollViewportForElement(element) {
    if (!(element instanceof Element)) {
        return null;
    }
    const viewport = element.closest('.chat-area, .split-chat-area');
    return viewport instanceof HTMLElement ? viewport : null;
}

function getChatScrollContainerForElement(element) {
    if (!(element instanceof Element)) {
        return null;
    }
    const container = element.closest('.chat-area-container, .split-chat-area-container');
    return container instanceof HTMLElement ? container : null;
}

function isViewportNearBottom(viewport, threshold = 80) {
    if (!(viewport instanceof HTMLElement)) {
        return true;
    }
    const remaining = viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop;
    return remaining <= threshold;
}

function findVisibleChatViewportAnchor(container, viewportRect) {
    if (!(container instanceof HTMLElement)) {
        return null;
    }
    const topLevelChildren = Array.from(container.children).filter((child) => child instanceof HTMLElement);
    for (const child of topLevelChildren) {
        const rect = child.getBoundingClientRect();
        if (rect.bottom > viewportRect.top + 1 && rect.top < viewportRect.bottom - 1) {
            return child;
        }
    }
    return topLevelChildren[0] || null;
}

function captureChatScrollViewportSnapshot(target) {
    if (window.ChatScrollManager && typeof window.ChatScrollManager.capture === 'function') {
        return window.ChatScrollManager.capture(target);
    }
    if (!(target instanceof Element)) {
        return null;
    }
    const viewport = getChatScrollViewportForElement(target);
    const container = getChatScrollContainerForElement(target);
    if (!(viewport instanceof HTMLElement) || !(container instanceof HTMLElement)) {
        return null;
    }
    if (viewport.scrollHeight <= viewport.clientHeight + 1 || isViewportNearBottom(viewport)) {
        return null;
    }

    const viewportRect = viewport.getBoundingClientRect();
    const anchor = findVisibleChatViewportAnchor(container, viewportRect);
    return {
        viewport,
        anchor,
        anchorTop: anchor ? anchor.getBoundingClientRect().top : 0,
        scrollTop: viewport.scrollTop,
    };
}

function restoreChatScrollViewportSnapshot(snapshot) {
    if (!snapshot) {
        return;
    }
    if (window.ChatScrollManager && typeof window.ChatScrollManager.restore === 'function') {
        window.ChatScrollManager.restore(snapshot);
        return;
    }
    const { viewport, anchor, anchorTop, scrollTop } = snapshot;
    if (!(viewport instanceof HTMLElement) || !viewport.isConnected) {
        return;
    }

    let nextScrollTop = scrollTop;
    if (anchor instanceof HTMLElement && anchor.isConnected) {
        nextScrollTop += anchor.getBoundingClientRect().top - anchorTop;
    }

    const maxScrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
    viewport.scrollTop = Math.min(Math.max(nextScrollTop, 0), maxScrollTop);
}

function preserveChatScrollViewportDuringMutation(target, mutate) {
    if (window.ChatScrollManager && typeof window.ChatScrollManager.preserveDuringMutation === 'function') {
        return window.ChatScrollManager.preserveDuringMutation(target, mutate);
    }
    const snapshot = captureChatScrollViewportSnapshot(target);
    try {
        return mutate();
    } finally {
        if (snapshot) {
            // The shared controller normally handles guarded post-layout
            // corrections. Its fallback is intentionally synchronous so an
            // old snapshot can never overwrite a later user scroll.
            restoreChatScrollViewportSnapshot(snapshot);
        }
    }
}

function isCodeBlockInStreamingContext(element) {
    if (!(element instanceof Element)) {
        return false;
    }
    const container = element.closest('.assistant-message-container');
    return container?.dataset?.isStreaming === 'true';
}

function getTrailingOpenCodeFence(markdownSource) {
    const text = String(markdownSource ?? '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    if (!text.includes('```') && !text.includes('~~~')) {
        return null;
    }

    const lines = text.split('\n');
    let offset = 0;
    let openFence = null;

    for (let i = 0; i < lines.length; i += 1) {
        const line = lines[i];
        if (!openFence) {
            const openingMatch = line.match(/^ {0,3}([`~]{3,})(.*)$/);
            if (openingMatch) {
                openFence = {
                    markerChar: openingMatch[1][0],
                    markerLength: openingMatch[1].length,
                    info: (openingMatch[2] || '').trim(),
                    startOffset: offset,
                    contentStartOffset: offset + line.length + (i < lines.length - 1 ? 1 : 0),
                };
            }
        } else {
            const closingMatch = line.match(/^ {0,3}([`~]{3,})[ \t]*$/);
            if (
                closingMatch
                && closingMatch[1][0] === openFence.markerChar
                && closingMatch[1].length >= openFence.markerLength
            ) {
                openFence = null;
            }
        }
        offset += line.length;
        if (i < lines.length - 1) {
            offset += 1;
        }
    }

    if (!openFence) {
        return null;
    }

    return {
        startOffset: openFence.startOffset,
        info: openFence.info,
        content: text.slice(openFence.contentStartOffset),
    };
}

function tryUpdateStreamingCodeBlockContent({ element, previousRaw, nextRaw }) {
    if (!(element instanceof Element) || !isCodeBlockInStreamingContext(element)) {
        return false;
    }

    const host = getCodeBlockStateHost(element);
    if (!(host instanceof Element)) {
        return false;
    }

    const prevText = String(previousRaw ?? '');
    const nextText = String(nextRaw ?? '');
    if (!nextText || nextText === prevText) {
        return false;
    }
    if (prevText && !nextText.startsWith(prevText)) {
        return false;
    }

    const previousOpenFence = getTrailingOpenCodeFence(prevText);
    const nextOpenFence = getTrailingOpenCodeFence(nextText);
    if (!previousOpenFence || !nextOpenFence) {
        return false;
    }
    if (
        previousOpenFence.startOffset !== nextOpenFence.startOffset
        || previousOpenFence.info !== nextOpenFence.info
    ) {
        return false;
    }

    const wrappers = getCodeBlockWrappersForHost(element, host);
    if (!wrappers.length) {
        return false;
    }
    const wrapper = wrappers[wrappers.length - 1];
    if (!(wrapper instanceof Element)) {
        return false;
    }

    setCodeBlockPreviewToggleDisabled(wrapper, true);
    setCodeBlockRunButtonDisabled(wrapper, true);
    if (wrapper.dataset.activeView === 'preview') {
        setCodeBlockView(wrapper, 'code', { skipStatePersist: true });
    }

    const codeElement = wrapper.querySelector('.code-block-panel-code code[data-code-id]');
    if (!(codeElement instanceof Element)) {
        return false;
    }
    const codeId = String(codeElement.getAttribute('data-code-id') || '');
    if (!codeId) {
        return false;
    }

    const nextCode = String(nextOpenFence.content || '');
    if (codeElement.textContent === nextCode && codeSnippetRegistry.get(codeId) === nextCode) {
        return true;
    }

    preserveChatScrollViewportDuringMutation(element, () => {
        codeSnippetRegistry.set(codeId, nextCode);
        codeElement.textContent = nextCode;

        applySyntaxHighlighting(wrapper);
        if (wrapper.dataset.activeView === 'preview') {
            ensureCodeBlockPreview(wrapper);
        }
        updateVisibleCodeBlockHeights(wrapper);
    });
    return true;
}

