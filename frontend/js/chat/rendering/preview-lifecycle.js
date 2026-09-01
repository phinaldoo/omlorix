function cleanupMarkdownCodeBlockPreviews(root) {
    if (!(root instanceof Element) || typeof root.querySelectorAll !== 'function') {
        return;
    }
    root.querySelectorAll('.code-block-preview-pane').forEach((previewPane) => {
        if (typeof previewPane._previewCleanup === 'function') {
            try {
                previewPane._previewCleanup();
            } catch (error) {
                console.error('Code block preview cleanup failed:', error);
            }
            delete previewPane._previewCleanup;
        }
    });
    root.querySelectorAll('.code-block-wrapper').forEach((wrapper) => {
        if (wrapper._codeBlockLivePreviewTimer) {
            clearTimeout(wrapper._codeBlockLivePreviewTimer);
            wrapper._codeBlockLivePreviewTimer = null;
        }
        const content = wrapper.querySelector('.code-block-content');
        if (content) cancelPendingCollapseTransition(content);
    });
}

/**
 * Make rendered code blocks safe to serialize and insert into another DOM.
 * Async preview state and element expandos cannot survive innerHTML transfer;
 * leaving either behind would strand the receiving block in "loading".
 */
function prepareMarkdownCodeBlocksForTransfer(root) {
    if (!(root instanceof Element) || typeof root.querySelectorAll !== 'function') {
        return;
    }
    cleanupMarkdownCodeBlockPreviews(root);
    root.querySelectorAll('.code-block-wrapper').forEach((wrapper) => {
        setCodeBlockView(wrapper, 'code', { skipStatePersist: true });
        setCodeBlockPreviewToggleDisabled(wrapper, false);
        setCodeBlockRunButtonDisabled(wrapper, false);
        const previewPane = wrapper.querySelector('.code-block-preview-pane');
        if (previewPane) {
            previewPane.replaceChildren();
            previewPane.dataset.previewState = 'idle';
            delete previewPane.dataset.previewHash;
        }
    });
}

function persistCodeBlockViewPreference(wrapper, view) {
    if (!(wrapper instanceof Element)) {
        return;
    }
    if (isCodeBlockInStreamingContext(wrapper)) {
        return;
    }
    const signature = wrapper.dataset.previewSignature;
    if (!signature) {
        return;
    }
    const host = getCodeBlockStateHost(wrapper);
    if (!(host instanceof Element)) {
        return;
    }
    const nextState = readCodeBlockViewState(host);
    nextState[signature] = view;
    writeCodeBlockViewState(host, nextState);
}

function getCodeBlockCollapseSignature(index) {
    return `block:${index}`;
}

function persistCodeBlockCollapsePreference(wrapper, collapsed) {
    if (!(wrapper instanceof Element)) {
        return;
    }
    const host = getCodeBlockStateHost(wrapper);
    if (!(host instanceof Element)) {
        return;
    }
    let signature = wrapper.dataset.collapseSignature;
    if (!signature) {
        const wrappers = getCodeBlockWrappersForHost(host, host);
        const index = wrappers.indexOf(wrapper);
        if (index < 0) {
            return;
        }
        signature = getCodeBlockCollapseSignature(index);
        wrapper.dataset.collapseSignature = signature;
    }
    const nextState = readCodeBlockCollapseState(host);
    const shouldCollapse = Boolean(collapsed);
    const hasEntry = Object.prototype.hasOwnProperty.call(nextState, signature);
    if (shouldCollapse) {
        nextState[signature] = true;
        writeCodeBlockCollapseState(host, nextState);
        return;
    }
    if (!hasEntry) {
        return;
    }
    delete nextState[signature];
    writeCodeBlockCollapseState(host, nextState);
}

function applyCodeBlockCollapsedState(wrapper, collapsed) {
    if (!(wrapper instanceof Element)) {
        return;
    }
    const content = wrapper.querySelector('.code-block-content');
    if (!(content instanceof Element)) {
        return;
    }
    if (content.dataset.isCollapsing === 'true' || content.dataset.isExpanding === 'true') {
        return;
    }
    const shouldCollapse = Boolean(collapsed);
    const button = wrapper.querySelector('.collapse-code-btn');

    content.classList.toggle('collapsed', shouldCollapse);
    wrapper.classList.toggle('is-collapsed', shouldCollapse);
    if (shouldCollapse) {
        content.style.maxHeight = '0';
        content.style.overflow = 'hidden';
    } else {
        content.style.maxHeight = 'none';
        content.style.overflow = '';
    }

    if (button instanceof Element) {
        setCodeBlockCollapseButtonState(button, shouldCollapse);
    }
}

function setCodeBlockView(wrapper, nextView, options = {}) {
    if (!(wrapper instanceof Element)) {
        return null;
    }
    const previewPanel = wrapper.querySelector('.code-block-panel-preview');
    const codePanel = wrapper.querySelector('.code-block-panel-code');
    const resolvedView = nextView === 'preview'
        && previewPanel
        && !isCodeBlockInStreamingContext(wrapper)
        && !isCodeBlockPreviewToggleDisabled(wrapper)
        ? 'preview'
        : 'code';
    wrapper.dataset.activeView = resolvedView;

    const content = wrapper.querySelector('.code-block-content');
    if (content) {
        content.dataset.activeView = resolvedView;
    }

    wrapper.querySelectorAll('.code-view-toggle-btn').forEach((button) => {
        const isActive = button.getAttribute('data-view') === resolvedView;
        button.classList.toggle('is-active', isActive);
        button.setAttribute('aria-selected', isActive ? 'true' : 'false');
        button.setAttribute('tabindex', isActive ? '0' : '-1');
    });

    if (codePanel) {
        codePanel.hidden = resolvedView !== 'code';
        codePanel.classList.toggle('is-active', resolvedView === 'code');
    }
    if (previewPanel) {
        previewPanel.hidden = resolvedView !== 'preview';
        previewPanel.classList.toggle('is-active', resolvedView === 'preview');
    }

    if (!options.skipStatePersist) {
        persistCodeBlockViewPreference(wrapper, resolvedView);
    }

    if (resolvedView === 'preview') {
        return ensureCodeBlockPreview(wrapper, { force: options.forcePreviewRefresh === true });
    }

    // A menu anchored in the preview toolbar must never remain logically open
    // after the user returns to source code or streaming forces Code view.
    setHtmlPreviewSettingsMenuOpen(wrapper, false);

    return null;
}

function syncCodeBlockViewState(root) {
    if (!(root instanceof Element) || typeof root.querySelectorAll !== 'function') {
        return;
    }
    const allWrappers = Array.from(root.querySelectorAll('.code-block-wrapper'));
    if (!allWrappers.length) {
        return;
    }
    const isStreaming = isCodeBlockInStreamingContext(root);
    allWrappers.forEach((wrapper) => {
        setCodeBlockRunButtonDisabled(wrapper, isStreaming);
    });
    const previewableWrappers = allWrappers.filter((wrapper) => wrapper.hasAttribute('data-preview-kind'));
    if (!previewableWrappers.length) {
        return;
    }
    const host = getCodeBlockStateHost(root);
    const state = readCodeBlockViewState(host);
    let shouldWriteState = false;
    previewableWrappers.forEach((wrapper, index) => {
        const previewEnabled = !isStreaming;
        setCodeBlockPreviewToggleDisabled(wrapper, !previewEnabled);
        const sourceHash = hashCodeBlockSource(getCodeBlockSource(wrapper));
        const previewKind = wrapper.dataset.previewKind || '';
        const signature = `${previewKind}:${sourceHash}:${index}`;
        wrapper.dataset.previewSignature = signature;
        const hasPersistedView = typeof state[signature] === 'string';
        const desiredView = previewEnabled
            ? (hasPersistedView ? state[signature] : CODE_BLOCK_DEFAULT_VIEW)
            : 'code';
        if (!hasPersistedView && previewEnabled) {
            state[signature] = desiredView;
            shouldWriteState = true;
        }
        setCodeBlockView(wrapper, desiredView, { skipStatePersist: true });
    });
    if (shouldWriteState) {
        writeCodeBlockViewState(host, state);
    }
}

function syncCodeBlockCollapseState(root) {
    if (!(root instanceof Element) || typeof root.querySelectorAll !== 'function') {
        return;
    }
    const host = getCodeBlockStateHost(root);
    if (!(host instanceof Element)) {
        return;
    }
    const wrappers = getCodeBlockWrappersForHost(root, host);
    const state = readCodeBlockCollapseState(host);
    const activeSignatures = new Set();
    let shouldWriteState = false;
    const collapseAnimationUntil = Number(host.dataset.codeBlockCollapseAnimatingUntil || '0');
    const hasRunningWrapperAnimation = wrappers.some((wrapper) => {
        if (!(wrapper instanceof Element) || typeof wrapper.getAnimations !== 'function') {
            return false;
        }
        try {
            return wrapper.getAnimations({ subtree: true }).some((animation) => animation?.playState === 'running');
        } catch (_) {
            return wrapper.getAnimations().some((animation) => animation?.playState === 'running');
        }
    });
    const shouldSkipAnimationSync = Number.isFinite(collapseAnimationUntil)
        && Date.now() < collapseAnimationUntil
        && hasRunningWrapperAnimation;

    if (shouldSkipAnimationSync) {
        wrappers.forEach((wrapper, index) => {
            wrapper.dataset.collapseSignature = getCodeBlockCollapseSignature(index);
        });
        return;
    }

    if (host.dataset.codeBlockCollapseAnimatingUntil) {
        delete host.dataset.codeBlockCollapseAnimatingUntil;
    }

    wrappers.forEach((wrapper, index) => {
        const signature = getCodeBlockCollapseSignature(index);
        wrapper.dataset.collapseSignature = signature;
        activeSignatures.add(signature);
        applyCodeBlockCollapsedState(wrapper, state[signature] === true);
    });

    const isHostRoot = root === host;
    if (!isHostRoot) {
        return;
    }

    Object.keys(state).forEach((signature) => {
        if (activeSignatures.has(signature)) {
            return;
        }
        delete state[signature];
        shouldWriteState = true;
    });

    if (shouldWriteState) {
        writeCodeBlockCollapseState(host, state);
    }
}

function finalizeCodeBlockPreviewState(root) {
    if (!(root instanceof Element) || typeof root.querySelectorAll !== 'function') {
        return;
    }
    const interactiveWrappers = Array.from(root.querySelectorAll('.code-block-wrapper'));
    interactiveWrappers.forEach((wrapper) => {
        setCodeBlockRunButtonDisabled(wrapper, false);
    });
    const previewableWrappers = Array.from(root.querySelectorAll('.code-block-wrapper[data-preview-kind]'));
    if (!previewableWrappers.length) {
        return;
    }
    const host = getCodeBlockStateHost(root);
    const state = readCodeBlockViewState(host);
    let shouldWriteState = false;
    previewableWrappers.forEach((wrapper, index) => {
        setCodeBlockPreviewToggleDisabled(wrapper, false);
        const sourceHash = hashCodeBlockSource(getCodeBlockSource(wrapper));
        const previewKind = wrapper.dataset.previewKind || '';
        const signature = wrapper.dataset.previewSignature || `${previewKind}:${sourceHash}:${index}`;
        wrapper.dataset.previewSignature = signature;
        if (typeof state[signature] !== 'string') {
            state[signature] = CODE_BLOCK_DEFAULT_VIEW;
            shouldWriteState = true;
        }
        setCodeBlockView(wrapper, state[signature], { skipStatePersist: true });
    });
    if (shouldWriteState) {
        writeCodeBlockViewState(host, state);
    }
}

function setCodeBlockPreviewToggleDisabled(wrapper, disabled) {
    if (!(wrapper instanceof Element)) {
        return;
    }
    const previewToggle = wrapper.querySelector('.code-view-toggle-btn[data-view="preview"]');
    const shouldDisable = Boolean(disabled);
    if (previewToggle instanceof HTMLButtonElement) {
        previewToggle.disabled = shouldDisable;
        previewToggle.setAttribute('aria-disabled', shouldDisable ? 'true' : 'false');
    }
    const reloadButton = wrapper.querySelector('.reload-preview-btn');
    if (reloadButton instanceof HTMLButtonElement && reloadButton.dataset.loading !== 'true') {
        reloadButton.disabled = shouldDisable;
        reloadButton.setAttribute('aria-disabled', shouldDisable ? 'true' : 'false');
    }
    const expandButton = wrapper.querySelector('.vega-preview-expand-btn');
    if (expandButton instanceof HTMLButtonElement) {
        expandButton.disabled = shouldDisable;
        expandButton.setAttribute('aria-disabled', shouldDisable ? 'true' : 'false');
    }
    const vegaExternalButton = wrapper.querySelector('.vega-preview-external-resources-btn');
    if (vegaExternalButton instanceof HTMLButtonElement) {
        vegaExternalButton.disabled = shouldDisable;
        vegaExternalButton.setAttribute('aria-disabled', shouldDisable ? 'true' : 'false');
    }
    const htmlSettingsTrigger = wrapper.querySelector('.code-block-html-settings-trigger');
    if (htmlSettingsTrigger instanceof HTMLButtonElement) {
        htmlSettingsTrigger.disabled = shouldDisable;
        htmlSettingsTrigger.setAttribute('aria-disabled', shouldDisable ? 'true' : 'false');
    }
    if (shouldDisable) {
        setHtmlPreviewSettingsMenuOpen(wrapper, false);
    }
    if (wrapper.dataset.previewKind === 'html') {
        syncHtmlPreviewCapabilityControls(wrapper);
    }
}

function setCodeBlockRunButtonDisabled(wrapper, disabled) {
    if (!(wrapper instanceof Element)) {
        return;
    }
    const runButton = wrapper.querySelector('.run-code-btn');
    if (!(runButton instanceof HTMLButtonElement)) {
        return;
    }
    runButton.dataset.streamLocked = disabled ? 'true' : 'false';
    setRunCodeButtonState(runButton, runButton.dataset.running === 'true');
}

function isCodeBlockPreviewToggleDisabled(wrapper) {
    if (!(wrapper instanceof Element)) {
        return false;
    }
    const previewToggle = wrapper.querySelector('.code-view-toggle-btn[data-view="preview"]');
    return previewToggle instanceof HTMLButtonElement ? previewToggle.disabled : false;
}

function renderMermaidBlocks(root) {
    if (!root || typeof root.querySelectorAll !== 'function') {
        return;
    }
    const blocks = root.querySelectorAll('.mermaid-block');
    blocks.forEach((block) => {
        const sourceEl = block.querySelector('.mermaid-block-source');
        const previewEl = block.querySelector('.mermaid-diagram');
        if (!sourceEl || !previewEl) {
            return;
        }
        const source = String(sourceEl.textContent || '');
        if (previewEl.dataset.mermaidSource === source) {
            return;
        }
        previewEl.dataset.mermaidSource = source;
        previewEl.textContent = getChatPreviewTranslation('code_block_mermaid_rendering', 'Rendering Mermaid diagram...');
        renderMermaidDiagram(previewEl, source).catch(() => {});
    });
}

