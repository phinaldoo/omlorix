function captureSelectionWithin(element) {
    if (typeof window === 'undefined' || !element) {
        return null;
    }
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) {
        return null;
    }
    const range = selection.getRangeAt(0);
    if (!element.contains(range.startContainer) || !element.contains(range.endContainer)) {
        return null;
    }
    try {
        const preSelectionRange = range.cloneRange();
        preSelectionRange.selectNodeContents(element);
        preSelectionRange.setEnd(range.startContainer, range.startOffset);
        const start = preSelectionRange.toString().length;
        const selectionRange = range.cloneRange();
        const selectionLength = selectionRange.toString().length;
        const end = start + selectionLength;
        
        // Detect selection direction: if anchor comes after focus, selection is backward
        let isBackward = false;
        if (selection.anchorNode && selection.focusNode) {
            if (selection.anchorNode === selection.focusNode) {
                isBackward = selection.anchorOffset > selection.focusOffset;
            } else if (element.contains(selection.anchorNode) && element.contains(selection.focusNode)) {
                const position = selection.anchorNode.compareDocumentPosition(selection.focusNode);
                // If focus is before anchor, selection is backward
                isBackward = (position & Node.DOCUMENT_POSITION_PRECEDING) !== 0;
            }
        }
        
        return { start, end, isBackward };
    } catch (_) {
        return null;
    }
}

function findNodeForCharacterOffset(root, offset) {
    if (typeof document === 'undefined' || !root) {
        return null;
    }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
    let currentOffset = 0;
    let node = walker.nextNode();
    let lastNode = null;
    while (node) {
        const nodeText = node.textContent || '';
        const nextOffset = currentOffset + nodeText.length;
        if (offset <= nextOffset) {
            return { node, offset: Math.max(0, offset - currentOffset) };
        }
        currentOffset = nextOffset;
        lastNode = node;
        node = walker.nextNode();
    }
    if (lastNode) {
        return { node: lastNode, offset: lastNode.textContent.length };
    }
    return { node: root, offset: root.childNodes ? root.childNodes.length : 0 };
}

function restoreSelectionWithin(element, savedSelection) {
    if (!savedSelection || typeof window === 'undefined' || !element) {
        return false;
    }
    const selection = window.getSelection();
    if (!selection) {
        return false;
    }
    const startInfo = findNodeForCharacterOffset(element, savedSelection.start);
    const endInfo = findNodeForCharacterOffset(element, savedSelection.end);
    if (!startInfo || !startInfo.node || !endInfo || !endInfo.node) {
        return false;
    }
    try {
        const range = document.createRange();
        range.setStart(startInfo.node, startInfo.offset);
        range.setEnd(endInfo.node, endInfo.offset);
        selection.removeAllRanges();
        
        // Restore selection direction using setBaseAndExtent if backward
        if (savedSelection.isBackward && typeof selection.setBaseAndExtent === 'function') {
            // For backward selection: anchor (base) is at end, focus (extent) is at start
            selection.setBaseAndExtent(
                endInfo.node, endInfo.offset,
                startInfo.node, startInfo.offset
            );
        } else {
            selection.addRange(range);
        }
        return true;
    } catch (_) {
        // Ignore selection restoration failures
    }
    return false;
}

function scheduleSelectionRestore(element, savedSelection, token, attempts = 2) {
    if (!savedSelection || !element || !token) {
        return;
    }
    const enqueue = typeof requestAnimationFrame === 'function'
        ? requestAnimationFrame
        : (cb) => setTimeout(cb, 16);

    let remaining = attempts;
    const attemptRestore = () => {
        if (!element || element._selectionRestoreToken !== token) {
            return;
        }
        restoreSelectionWithin(element, savedSelection);
        remaining -= 1;
        if (remaining > 0) {
            enqueue(attemptRestore);
        }
    };

    enqueue(attemptRestore);
}

function renderMarkdownContent(element, content) {
    if (!element) {
        return;
    }
    const nextMarkdownSource = String(content ?? '');
    const previousRenderedRawContent = String(element.getAttribute('data-rendered-raw-content') || '');
    if (tryUpdateStreamingCodeBlockContent({ element, previousRaw: previousRenderedRawContent, nextRaw: nextMarkdownSource })) {
        element.setAttribute('data-rendered-raw-content', nextMarkdownSource);
        return;
    }

    const savedSelection = captureSelectionWithin(element);
    const selectionRestoreToken = savedSelection
        ? ((element._selectionRestoreToken || 0) + 1)
        : null;
    if (selectionRestoreToken !== null) {
        element._selectionRestoreToken = selectionRestoreToken;
    }
    const markdownSource = nextMarkdownSource;
    const md = getMarkdownRenderer();

    if (!md) {
        element.textContent = markdownSource;
        element.classList.remove('markdown-body');
        element.setAttribute('data-rendered-raw-content', markdownSource);
        if (savedSelection) {
            restoreSelectionWithin(element, savedSelection);
        }
        return;
    }

    let renderFailed = false;
    preserveChatScrollViewportDuringMutation(element, () => {
        try {
            const renderedHtml = md.render(markdownSource);
            const preparedHtml = window.ChatMarkdownFileRefs && typeof window.ChatMarkdownFileRefs.prepareRenderedHtml === 'function'
                ? window.ChatMarkdownFileRefs.prepareRenderedHtml(renderedHtml)
                : renderedHtml;
            if (window.ChatSanitizer && typeof window.ChatSanitizer.sanitizeHtml === 'function') {
                element.innerHTML = window.ChatSanitizer.sanitizeHtml(preparedHtml);
                window.ChatMarkdownAlerts?.enhanceIcons?.(element);
            } else {
                element.textContent = markdownSource;
            }
            element.classList.add('markdown-body');
        } catch (error) {
            renderFailed = true;
            console.error('Markdown rendering error:', error);
            element.textContent = markdownSource;
            element.classList.remove('markdown-body');
        }

        if (!renderFailed) {
            const isStreamingRender = isCodeBlockInStreamingContext(element);
            if (isStreamingRender) {
                // Markdown parsing is required for readable live output, but
                // math, highlighting, diagrams, embeds, and code controls scan
                // the complete accumulated response. Run that expensive pass
                // once when the stream becomes stable.
                element.dataset.streamingMarkdownNeedsFinalize = 'true';
                return;
            }
            delete element.dataset.streamingMarkdownNeedsFinalize;
            const runEnhancer = (fn, label) => {
                if (typeof fn !== 'function') {
                    return;
                }
                try {
                    fn();
                } catch (error) {
                    if (label) {
                        console.error(`Markdown enhancement error in ${label}:`, error);
                    } else {
                        console.error('Markdown enhancement error:', error);
                    }
                }
            };

            runEnhancer(() => wrapImplicitMathSegments(element), 'wrapImplicitMathSegments');
            runEnhancer(() => renderMathWithRetry(element, 0), 'renderMathWithRetry');
            runEnhancer(() => ensureMarkdownEvents(), 'ensureMarkdownEvents');
            runEnhancer(() => ensureMarkdownObserver(), 'ensureMarkdownObserver');
            runEnhancer(() => enhanceCodeBlocks(element), 'enhanceCodeBlocks');
            runEnhancer(() => applySyntaxHighlighting(element), 'applySyntaxHighlighting');
            runEnhancer(() => renderMermaidBlocks(element), 'renderMermaidBlocks');
            runEnhancer(() => enhanceMarkdownTaskLists(element), 'enhanceMarkdownTaskLists');
            runEnhancer(() => {
                if (typeof requestAnimationFrame === 'function' && typeof updateVisibleCodeBlockHeights === 'function') {
                    requestAnimationFrame(() => {
                        try {
                            updateVisibleCodeBlockHeights(element);
                        } catch (error) {
                            console.error('Markdown enhancement error in updateVisibleCodeBlockHeights:', error);
                        }
                    });
                }
            }, 'updateVisibleCodeBlockHeights');
            runEnhancer(() => {
                if (typeof YouTubeEmbed !== 'undefined' && typeof YouTubeEmbed.processRenderedContent === 'function') {
                    YouTubeEmbed.processRenderedContent(element);
                }
            }, 'YouTubeEmbed');
        }
    });

    if (savedSelection) {
        restoreSelectionWithin(element, savedSelection);
        scheduleSelectionRestore(element, savedSelection, selectionRestoreToken, 2);
    }

    element.setAttribute('data-rendered-raw-content', markdownSource);
}

function enhanceCodeBlocks(root) {
    if (!root) {
        return;
    }
    if (root.querySelectorAll) {
        root.querySelectorAll('.code-block-wrapper').forEach(wrapper => {
            if (!wrapper.dataset.editing) {
                wrapper.dataset.editing = 'false';
            }
            const contentWrapper = wrapper.querySelector('.code-block-content');
            if (!contentWrapper) {
                return;
            }
            const editor = getCodeBlockEditor(wrapper);
            if (editor instanceof HTMLTextAreaElement && !editor.dataset.initialized) {
                editor.dataset.initialized = 'true';
                editor.value = getCodeBlockSource(wrapper);
                editor.hidden = true;
                editor.disabled = true;
                editor.spellcheck = false;
                editor.autocapitalize = 'off';
                editor.autocomplete = 'off';
                editor.autocorrect = 'off';
            }
            const language = (wrapper.getAttribute('data-language') || '').toLowerCase();
            if (!wrapper.dataset.previewKind && (language === 'svg' || language === 'xml')) {
                appendSvgPreview(wrapper);
            }
        });
        syncCodeBlockCollapseState(root);
        syncCodeBlockViewState(root);
    }
}

function populateCodeBlockContents(root) {
    if (!root || typeof root.querySelectorAll !== 'function') {
        return;
    }
    root.querySelectorAll('code[data-code-id]').forEach((codeElement) => {
        const codeId = codeElement.getAttribute('data-code-id');
        if (!codeId) {
            return;
        }
        const snippet = codeSnippetRegistry.get(codeId);
        if (snippet === undefined) {
            return;
        }
        if (codeElement.textContent !== snippet) {
            codeElement.textContent = snippet;
        }
    });
}

function applySyntaxHighlighting(root) {
    if (!root || typeof Prism === 'undefined') {
        return;
    }
    const selectionSnapshot = captureSelectionWithin(root);
    try {
        populateCodeBlockContents(root);
        const codeElements = root.querySelectorAll('code[class*="language-"]');
        codeElements.forEach((code) => {
            const expectedSnippet = codeSnippetRegistry.get(code.getAttribute('data-code-id') || '');
            if (code.querySelector('.token') && (expectedSnippet === undefined || code.textContent === expectedSnippet)) {
                return;
            }
            if (typeof Prism.highlightElement === 'function') {
                try {
                    Prism.highlightElement(code);
                } catch (_) {}
            }
        });
    } catch (error) {
        console.error('Syntax highlighting failed:', error);
    } finally {
        if (selectionSnapshot) {
            restoreSelectionWithin(root, selectionSnapshot);
        }
    }
}

function enhanceMarkdownTaskLists(root) {
    if (!root || typeof root.querySelectorAll !== 'function') {
        return;
    }

    const checkboxes = root.querySelectorAll('.task-list-item input[type="checkbox"]');
    checkboxes.forEach((checkbox) => {
        if (!(checkbox instanceof HTMLInputElement)) {
            return;
        }
        if (checkbox.dataset.markdownTaskEnhanced === 'true') {
            return;
        }

        const listItem = checkbox.closest('.task-list-item');
        if (!listItem) {
            return;
        }

        listItem.classList.add('markdown-task-item-enhanced');
        checkbox.dataset.markdownTaskEnhanced = 'true';
        checkbox.classList.add('markdown-task-checkbox-input');
        checkbox.tabIndex = -1;
        checkbox.setAttribute('aria-hidden', 'true');
        checkbox.removeAttribute('disabled');

        if (!checkbox.id) {
            checkbox.id = `markdown-task-${Math.random().toString(36).slice(2, 9)}`;
        }

        const customButton = document.createElement('button');
        customButton.type = 'button';
        customButton.className = 'markdown-task-checkbox-button form-checkbox';
        customButton.dataset.checkboxTargetId = checkbox.id;
        customButton.setAttribute('role', 'checkbox');
        customButton.setAttribute('aria-label', getChatPreviewTranslation('markdown_task_toggle_state', 'Toggle task state'));
        const insertionPoint = checkbox.nextSibling;
        if (insertionPoint && insertionPoint.parentNode === listItem) {
            listItem.insertBefore(customButton, insertionPoint);
        } else {
            listItem.appendChild(customButton);
        }

        updateMarkdownTaskCheckboxVisual(customButton, checkbox);

        checkbox.addEventListener('change', () => {
            updateMarkdownTaskCheckboxVisual(customButton, checkbox);
        });

        customButton.addEventListener('keydown', (event) => {
            if (event.key === ' ' || event.key === 'Enter') {
                event.preventDefault();
                toggleMarkdownTaskCheckbox(customButton);
            }
        });
    });
}

function toggleMarkdownTaskCheckbox(button) {
    if (!(button instanceof Element)) {
        return;
    }
    const checkboxId = button.dataset.checkboxTargetId;
    if (!checkboxId) {
        return;
    }
    const checkbox = document.getElementById(checkboxId);
    if (!(checkbox instanceof HTMLInputElement)) {
        return;
    }

    const nextChecked = !checkbox.checked;
    checkbox.checked = nextChecked;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    updateMarkdownTaskCheckboxVisual(button, checkbox);
}

function updateMarkdownTaskCheckboxVisual(button, checkbox) {
    if (!(button instanceof Element) || !(checkbox instanceof HTMLInputElement)) {
        return;
    }
    const isChecked = checkbox.checked;
    button.classList.toggle('is-checked', isChecked);
    button.setAttribute('aria-checked', isChecked ? 'true' : 'false');
    if (button.innerHTML) {
        button.innerHTML = '';
    }
}

function appendSvgPreview(wrapper) {
    if (!wrapper) {
        return;
    }
    const codePanel = wrapper.querySelector('.code-block-panel-code');
    const codeElement = codePanel ? codePanel.querySelector('pre code') : null;
    if (!codeElement) {
        return;
    }
    const svgSource = (codeElement.textContent || '').trim();
    if (!svgSource || !/<svg\b[^>]*>/i.test(svgSource)) {
        return;
    }

    let sanitized = '';
    try {
        if (window.ChatSanitizer && typeof window.ChatSanitizer.sanitizeSvg === 'function') {
            sanitized = window.ChatSanitizer.sanitizeSvg(svgSource);
        }
    } catch (error) {
        console.error('SVG sanitization failed:', error);
    }

    if (!sanitized) {
        return;
    }

    const existingPreview = wrapper.querySelector('.code-block-svg-preview');
    if (existingPreview) {
        existingPreview.remove();
    }

    const previewWrapper = document.createElement('div');
    previewWrapper.className = 'code-block-svg-preview';
    previewWrapper.innerHTML = sanitized;
    if (codePanel) {
        codePanel.appendChild(previewWrapper);
    }
}

function ensureMarkdownEvents() {
    if (markdownEventsInitialized) {
        return;
    }
    document.addEventListener('pointerdown', handleMarkdownPointerDown, false);
    document.addEventListener('click', handleMarkdownClick, false);
    document.addEventListener('keydown', handleMarkdownKeydown, false);
    document.addEventListener('input', handleMarkdownInput, false);
    document.addEventListener('change', handleMarkdownChange, false);
    markdownEventsInitialized = true;
}

function cancelPendingCollapseTransition(content) {
    if (!(content instanceof Element)) {
        return;
    }
    if (content._collapseTransitionAbort) {
        content._collapseTransitionAbort.abort();
        content._collapseTransitionAbort = null;
    }
    delete content.dataset.isExpanding;
    delete content.dataset.isCollapsing;
}

function toggleCodeBlockCollapseButton(button) {
    if (!(button instanceof Element)) {
        return;
    }
    const wrapper = button.closest('.code-block-wrapper');
    if (!wrapper) {
        return;
    }
    const content = wrapper.querySelector('.code-block-content');
    if (!content) {
        return;
    }
    const host = getCodeBlockStateHost(wrapper);
    if (host instanceof Element) {
        host.dataset.codeBlockCollapseAnimatingUntil = String(Date.now() + CODE_BLOCK_COLLAPSE_ANIMATION_DURATION_MS);
    }

    cancelPendingCollapseTransition(content);
    const ac = new AbortController();
    content._collapseTransitionAbort = ac;

    const isCollapsed = content.classList.contains('collapsed');
    if (isCollapsed) {
        content.dataset.isExpanding = 'true';
        content.style.maxHeight = '0px';
        content.style.overflow = 'hidden';
        void content.offsetHeight;
        content.classList.remove('collapsed');
        wrapper.classList.remove('is-collapsed');

        const finalizeExpand = () => {
            if (ac.signal.aborted) {
                return;
            }
            cancelPendingCollapseTransition(content);
            content.style.maxHeight = 'none';
            content.style.overflow = '';
        };

        content.addEventListener('transitionend', (event) => {
            if (event.target !== content || event.propertyName !== 'max-height') {
                return;
            }
            finalizeExpand();
        }, { signal: ac.signal });

        setTimeout(() => {
            if (content.dataset.isExpanding === 'true') {
                finalizeExpand();
            }
        }, CODE_BLOCK_COLLAPSE_ANIMATION_DURATION_MS + 64);

        // Force a reflow so the browser commits the start value before we expand.
        content.style.maxHeight = content.scrollHeight + 'px';
        setCodeBlockCollapseButtonState(button, false);
        persistCodeBlockCollapsePreference(wrapper, false);
        return;
    }

    content.dataset.isCollapsing = 'true';
    const finalizeCollapse = () => {
        if (ac.signal.aborted) {
            return;
        }
        wrapper.classList.add('is-collapsed');
        cancelPendingCollapseTransition(content);
    };
    content.style.overflow = 'hidden';
    content.style.maxHeight = content.scrollHeight + 'px';
    void content.offsetHeight;
    requestAnimationFrame(() => {
        if (ac.signal.aborted) {
            return;
        }
        content.classList.add('collapsed');
        content.style.maxHeight = '0';
    });
    content.addEventListener('transitionend', (event) => {
        if (event.target !== content || event.propertyName !== 'max-height') {
            return;
        }
        finalizeCollapse();
    }, { signal: ac.signal });
    setTimeout(() => {
        if (content.dataset.isCollapsing === 'true') {
            finalizeCollapse();
        }
    }, CODE_BLOCK_COLLAPSE_ANIMATION_DURATION_MS + 64);
    setCodeBlockCollapseButtonState(button, true);
    persistCodeBlockCollapsePreference(wrapper, true);
}

function handleMarkdownPointerDown(event) {
    if (typeof event.button === 'number' && event.button !== 0) {
        return;
    }
    const target = event.target;
    if (!(target instanceof Element)) {
        return;
    }

    // Close any settings popup when the next primary interaction starts
    // outside it. Delegation keeps this working for newly streamed messages.
    document.querySelectorAll('.code-block-html-settings.is-open').forEach((settings) => {
        if (settings.contains(target)) {
            return;
        }
        const settingsWrapper = settings.closest('.code-block-wrapper');
        if (settingsWrapper instanceof Element) {
            setHtmlPreviewSettingsMenuOpen(settingsWrapper, false);
        }
    });

    const button = target.closest('button.collapse-code-btn');
    if (!button) {
        return;
    }
    const markdownRoot = button.closest('.markdown-body');
    if (!markdownRoot) {
        return;
    }
    const wrapper = button.closest('.code-block-wrapper');
    if (!wrapper || !isCodeBlockInStreamingContext(wrapper)) {
        return;
    }

    event.preventDefault();
    wrapper._collapsePointerClickSuppressionUntil = Date.now() + COLLAPSE_CLICK_SUPPRESSION_WINDOW_MS;
    toggleCodeBlockCollapseButton(button);
}

function handleMarkdownKeydown(event) {
    const target = event.target;
    if (target instanceof HTMLTextAreaElement && target.classList.contains('code-block-inline-editor')) {
        const wrapper = target.closest('.code-block-wrapper');
        if (event.key === 'Escape') {
            event.preventDefault();
            if (wrapper) {
                setCodeBlockEditMode(wrapper, false);
            }
            return;
        }
        if ((event.metaKey || event.ctrlKey) && !event.shiftKey && event.key === 'Enter') {
            event.preventDefault();
            if (wrapper) {
                setCodeBlockEditMode(wrapper, false);
            }
            return;
        }
        if (event.key === 'Tab') {
            event.preventDefault();
            const start = target.selectionStart;
            const end = target.selectionEnd;
            const value = target.value;
            target.value = `${value.slice(0, start)}\t${value.slice(end)}`;
            target.selectionStart = start + 1;
            target.selectionEnd = start + 1;
            handleCodeBlockEditorInput(target);
            return;
        }
    }

    if (target instanceof Element && target.classList.contains('code-block-html-settings-trigger')) {
        const wrapper = target.closest('.code-block-wrapper');
        if (!(wrapper instanceof Element)) {
            return;
        }
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            setHtmlPreviewSettingsMenuOpen(wrapper, true, { focus: 'first' });
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setHtmlPreviewSettingsMenuOpen(wrapper, true, { focus: 'last' });
        } else if (event.key === 'Escape') {
            event.preventDefault();
            setHtmlPreviewSettingsMenuOpen(wrapper, false);
        }
        return;
    }

    if (target instanceof HTMLInputElement && target.classList.contains('html-preview-capability-toggle')) {
        const wrapper = target.closest('.code-block-wrapper');
        if (!(wrapper instanceof Element)) {
            return;
        }
        const items = getHtmlPreviewSettingsMenuItems(wrapper);
        const itemIndex = items.indexOf(target);
        if (event.key === 'Escape') {
            event.preventDefault();
            setHtmlPreviewSettingsMenuOpen(wrapper, false, { restoreFocus: true });
            return;
        }
        if (event.key === 'Tab') {
            setHtmlPreviewSettingsMenuOpen(wrapper, false);
            return;
        }
        if (itemIndex < 0) {
            return;
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            const delta = event.key === 'ArrowDown' ? 1 : -1;
            items[(itemIndex + delta + items.length) % items.length]?.focus({ preventScroll: true });
        } else if (event.key === 'Home') {
            event.preventDefault();
            items[0]?.focus({ preventScroll: true });
        } else if (event.key === 'End') {
            event.preventDefault();
            items.at(-1)?.focus({ preventScroll: true });
        }
        return;
    }

    if (!(target instanceof Element) || !target.classList.contains('code-view-toggle-btn')) {
        return;
    }
    const tablist = target.closest('.code-block-mode-toggle[role="tablist"]');
    if (!(tablist instanceof Element)) {
        return;
    }
    const tabs = Array.from(tablist.querySelectorAll('.code-view-toggle-btn[role="tab"]'))
        .filter((tab) => !(tab instanceof HTMLButtonElement) || !tab.disabled);
    if (!tabs.length) {
        return;
    }
    const currentIndex = tabs.indexOf(target);
    if (currentIndex === -1) {
        return;
    }

    let nextIndex = -1;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
        nextIndex = (currentIndex + 1) % tabs.length;
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
        nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    } else if (event.key === 'Home') {
        nextIndex = 0;
    } else if (event.key === 'End') {
        nextIndex = tabs.length - 1;
    }

    if (nextIndex === -1) {
        return;
    }

    event.preventDefault();
    const nextTab = tabs[nextIndex];
    const wrapper = nextTab.closest('.code-block-wrapper');
    if (wrapper) {
        setCodeBlockView(wrapper, nextTab.getAttribute('data-view') || 'code');
    }
    nextTab.focus();
}

function handleMarkdownInput(event) {
    const target = event.target;
    if (!(target instanceof HTMLTextAreaElement) || !target.classList.contains('code-block-inline-editor')) {
        return;
    }
    const markdownRoot = target.closest('.markdown-body');
    if (!markdownRoot) {
        return;
    }
    handleCodeBlockEditorInput(target);
}

/** Apply a native HTML permission switch to its owning sandboxed preview. */
function handleMarkdownChange(event) {
    const toggle = event.target;
    if (!(toggle instanceof HTMLInputElement)
        || !toggle.classList.contains('html-preview-capability-toggle')) {
        return;
    }
    const wrapper = toggle.closest('.code-block-wrapper');
    const previewPane = wrapper?.querySelector('.code-block-preview-pane');
    if (!(wrapper instanceof Element)
        || !(previewPane instanceof Element)
        || isCodeBlockPreviewToggleDisabled(wrapper)) {
        return;
    }

    const source = getCodeBlockSource(wrapper);
    const capabilities = analyzeHtmlPreviewCapabilities(source);
    const permissions = getHtmlPreviewPermissions(wrapper);
    const permission = toggle.dataset.htmlPreviewPermission;
    if (permission === 'scripts' && capabilities.scripts) {
        // The UI disables this switch until external content is granted, but
        // retain an explicit guard for synthetic or stale change events.
        permissions.allowScripts = toggle.checked && permissions.allowExternalContent;
    } else if (permission === 'external-content' && capabilities.externalContent) {
        permissions.allowExternalContent = toggle.checked;
        if (!toggle.checked) {
            permissions.allowScripts = false;
        }
    } else {
        // Source edits can make a previously available setting inapplicable.
        // Restore the authoritative state instead of accepting a stale event.
        syncHtmlPreviewCapabilityControls(wrapper, capabilities);
        return;
    }
    mountHtmlCodePreview(previewPane, source, wrapper, permissions);
}

async function handleMarkdownClick(event) {
    const target = event.target;
    if (!(target instanceof Element)) {
        return;
    }
    const button = target.closest('button');
    if (!button) {
        return;
    }
    const markdownRoot = button.closest('.markdown-body');
    if (!markdownRoot) {
        return;
    }

    if (button.classList.contains('markdown-task-checkbox-button')) {
        event.preventDefault();
        toggleMarkdownTaskCheckbox(button);
        return;
    }

    if (button.classList.contains('code-block-html-settings-trigger')) {
        event.preventDefault();
        const wrapper = button.closest('.code-block-wrapper');
        if (!wrapper || button.disabled || isCodeBlockPreviewToggleDisabled(wrapper)) {
            return;
        }
        const isOpen = button.getAttribute('aria-expanded') === 'true';
        setHtmlPreviewSettingsMenuOpen(wrapper, !isOpen);
        return;
    }

    if (button.classList.contains('code-view-toggle-btn')) {
        if (button instanceof HTMLButtonElement && button.disabled) {
            return;
        }
        const wrapper = button.closest('.code-block-wrapper');
        if (!wrapper) {
            return;
        }
        setCodeBlockView(wrapper, button.dataset.view || 'code');
        return;
    }

    if (button.classList.contains('run-code-btn')) {
        event.preventDefault();
        const wrapper = button.closest('.code-block-wrapper');
        if (!wrapper) {
            return;
        }
        await runMarkdownPythonCodeBlock(wrapper, button);
        return;
    }

    if (button.classList.contains('vega-preview-external-resources-btn')) {
        event.preventDefault();
        const wrapper = button.closest('.code-block-wrapper');
        if (!wrapper || isCodeBlockPreviewToggleDisabled(wrapper)) {
            return;
        }
        const source = getCodeBlockSource(wrapper);
        const sources = getVegaExternalSourcesForWrapper(wrapper, source);
        const signature = getVegaExternalResourceSignature(sources);
        if (hasVegaExternalConsent(wrapper, signature)) {
            revokeVegaExternalConsent(wrapper, signature);
        }
        syncVegaExternalResourceControl(wrapper, source, sources);
        await setCodeBlockView(wrapper, 'preview', { forcePreviewRefresh: true });
        return;
    }

    if (button.classList.contains('vega-preview-expand-btn')) {
        event.preventDefault();
        const wrapper = button.closest('.code-block-wrapper');
        if (!wrapper || isCodeBlockPreviewToggleDisabled(wrapper)) {
            return;
        }
        openVegaPreviewModal(wrapper);
        return;
    }

    if (button.classList.contains('reload-preview-btn')) {
        event.preventDefault();
        if (button.dataset.loading === 'true') {
            return;
        }
        const wrapper = button.closest('.code-block-wrapper');
        if (!wrapper || isCodeBlockPreviewToggleDisabled(wrapper)) {
            return;
        }

        const defaultIcon = button.dataset.defaultIcon || button.innerHTML || MARKDOWN_RELOAD_SVG;
        button.dataset.defaultIcon = defaultIcon;
        button.dataset.loading = 'true';
        button.classList.add('is-loading');
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        button.setAttribute('aria-disabled', 'true');
        setCodeBlockActionButtonLabel(button, 'code_block_reloading_preview', 'Reloading preview');
        button.innerHTML = '<span class="code-action-btn-spinner" aria-hidden="true"></span>';

        let renderResult = null;
        try {
            renderResult = setCodeBlockView(wrapper, 'preview', { forcePreviewRefresh: true });
            if (renderResult && typeof renderResult.then === 'function') {
                await renderResult;
            }
        } catch (_) {
            // Preview errors are already handled in renderer surfaces.
        } finally {
            if (button.isConnected) {
                button.dataset.loading = 'false';
                button.classList.remove('is-loading');
                button.removeAttribute('aria-busy');
                setCodeBlockActionButtonLabel(button, 'code_block_reload_preview', 'Reload preview');
                button.innerHTML = defaultIcon;
                const shouldDisable = isCodeBlockPreviewToggleDisabled(wrapper);
                button.disabled = shouldDisable;
                button.setAttribute('aria-disabled', shouldDisable ? 'true' : 'false');
            }
        }
        return;
    }

    if (button.classList.contains('copy-code-btn')) {
        const codeId = button.getAttribute('data-code-id');
        const code = (codeId && codeSnippetRegistry.get(codeId)) || '';
        const unescaped = unescapeHtml(code);
        const originalHTML = button.innerHTML;
        const successIcon = (typeof Icons !== 'undefined' && typeof Icons.check !== 'undefined')
            ? Icons.check
            : MARKDOWN_DONE_SVG;

        button.disabled = true;
        try {
            const exportText = typeof window !== 'undefined'
                && typeof window.appendComplianceWatermarkIfNeeded === 'function'
                ? window.appendComplianceWatermarkIfNeeded(unescaped)
                : unescaped;
            await copyToClipboard(exportText);
            button.innerHTML = successIcon;
            setCodeBlockActionButtonLabel(button, 'code_block_copy_code_success', 'Copied code');
            reportChatCopyFeedback({
                success: true,
                key: 'chat_copy_code_success',
                fallback: 'Code copied to clipboard.',
            });
            if (!shouldReduceMotionForSendMessage()) {
                try {
                    button.animate(
                        [
                            { transform: 'scale(1)' },
                            { transform: 'scale(1.05)' },
                            { transform: 'scale(1)' }
                        ],
                        { duration: 200, easing: 'ease-out' }
                    );
                } catch (_) {
                    // Ignore animation errors
                }
            }
            setTimeout(() => {
                button.innerHTML = originalHTML;
                setCodeBlockActionButtonLabel(button, 'code_block_copy_code', 'Copy code');
                button.disabled = false;
            }, 2000);
        } catch (error) {
            console.error('Copy code failed:', error);
            button.innerHTML = originalHTML;
            button.disabled = false;
            reportChatCopyFeedback({
                success: false,
                key: 'chat_copy_code_error',
                fallback: 'Failed to copy code.',
            });
        }
        return;
    }

    if (button.classList.contains('download-code-btn')) {
        const codeId = button.getAttribute('data-code-id');
        const code = (codeId && codeSnippetRegistry.get(codeId)) || '';
        const lang = button.getAttribute('data-lang') || '';
        const unescaped = unescapeHtml(code);
        const extension = getFileExtension(lang);
        const filename = `code.${extension}`;
        downloadCodeSnippet(unescaped, filename, button);
        return;
    }

    if (button.classList.contains('collapse-code-btn')) {
        const wrapper = button.closest('.code-block-wrapper');
        const suppressionUntil = Number(wrapper?._collapsePointerClickSuppressionUntil || '0');
        if (Number.isFinite(suppressionUntil) && Date.now() <= suppressionUntil) {
            if (wrapper) {
                wrapper._collapsePointerClickSuppressionUntil = 0;
            }
            return;
        }
        if (wrapper) {
            wrapper._collapsePointerClickSuppressionUntil = 0;
        }
        toggleCodeBlockCollapseButton(button);
        return;
    }

    const tableBtn = button.classList.contains('table-copy-btn') ? button : button.closest('.table-copy-btn');
    if (tableBtn) {
        const tableWrapper = tableBtn.closest('.table-wrapper');
        if (!tableWrapper) {
            return;
        }
        const tableMarkdown = extractTableMarkdown(tableWrapper);
        if (tableMarkdown) {
            const originalHTML = tableBtn.innerHTML;
            tableBtn.disabled = true;
            try {
                const exportText = typeof window !== 'undefined'
                    && typeof window.appendComplianceWatermarkIfNeeded === 'function'
                    ? window.appendComplianceWatermarkIfNeeded(tableMarkdown)
                    : tableMarkdown;
                await copyToClipboard(exportText);
                const successIcon = (typeof Icons !== 'undefined' && typeof Icons.check !== 'undefined') ? Icons.check : MARKDOWN_DONE_SVG;
                tableBtn.innerHTML = successIcon;
                tableBtn.disabled = true;
                reportChatCopyFeedback({
                    success: true,
                    key: 'chat_copy_table_success',
                    fallback: 'Table markdown copied to clipboard.',
                });
                if (!shouldReduceMotionForSendMessage()) {
                    try {
                        tableBtn.animate(
                            [
                                { transform: 'scale(1)' },
                                { transform: 'scale(1.1)' },
                                { transform: 'scale(1)' }
                            ],
                            { duration: 250, easing: 'ease-out' }
                        );
                    } catch (_) {
                        // ignore animation errors
                    }
                }
                setTimeout(() => {
                    tableBtn.innerHTML = originalHTML;
                    tableBtn.disabled = false;
                }, 3000);
            } catch (error) {
                console.error('Copy table markdown failed:', error);
                tableBtn.innerHTML = originalHTML;
                tableBtn.disabled = false;
                reportChatCopyFeedback({
                    success: false,
                    key: 'chat_copy_table_error',
                    fallback: 'Failed to copy table markdown.',
                });
            }
        }
    }
}

async function copyToClipboard(text) {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        await navigator.clipboard.writeText(text);
        return true;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);

    let copied = false;
    try {
        textarea.focus();
        textarea.select();
        copied = document.execCommand('copy');
    } finally {
        document.body.removeChild(textarea);
    }

    if (!copied) {
        const errorMessage = typeof getChatPreviewTranslation === 'function'
            ? getChatPreviewTranslation('chat_clipboard_copy_fallback_failed', 'Clipboard copy fallback failed')
            : 'Clipboard copy fallback failed';
        throw new Error(errorMessage);
    }

    return true;
}

function downloadCodeSnippet(content, filename, button) {
    try {
        const blob = new Blob([content], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        setTimeout(() => URL.revokeObjectURL(url), 1000);

        if (button) {
            const rect = button.getBoundingClientRect();
            const x = rect.left + window.scrollX + rect.width / 2;
            const y = rect.top + window.scrollY - 12;
            showTooltip(getCodeBlockActionLabel('code_block_downloaded_code', 'Code downloaded'), x, y);
        }
    } catch (error) {
        console.error('Download failed:', error);
    }
}

function getFileExtension(lang) {
    const map = {
        javascript: 'js',
        typescript: 'ts',
        python: 'py',
        java: 'java',
        cpp: 'cpp',
        c: 'c',
        csharp: 'cs',
        php: 'php',
        ruby: 'rb',
        go: 'go',
        rust: 'rs',
        swift: 'swift',
        kotlin: 'kt',
        html: 'html',
        css: 'css',
        scss: 'scss',
        json: 'json',
        vega: 'json',
        'vega-lite': 'json',
        vegalite: 'json',
        vg: 'json',
        vl: 'json',
        xml: 'xml',
        yaml: 'yaml',
        yml: 'yml',
        markdown: 'md',
        mermaid: 'mmd',
        mmd: 'mmd',
        sql: 'sql',
        bash: 'sh',
        shell: 'sh',
        powershell: 'ps1',
        r: 'r',
        dart: 'dart',
        lua: 'lua',
        perl: 'pl'
    };
    const normalized = String(lang || '').toLowerCase();
    return map[normalized] || 'txt';
}

function normalizeHighlightLanguage(lang) {
    const normalized = String(lang || '').trim().toLowerCase();
    if (!normalized) {
        return 'plaintext';
    }
    const map = {
        text: 'plaintext',
        plain: 'plaintext',
        plaintext: 'plaintext',
        shell: 'bash',
        sh: 'bash',
        zsh: 'bash',
        bash: 'bash',
        powershell: 'powershell',
        ps: 'powershell',
        ps1: 'powershell',
        c: 'c',
        'c++': 'cpp',
        cpp: 'cpp',
        'c#': 'csharp',
        cs: 'csharp',
        fsharp: 'fsharp',
        'f#': 'fsharp',
        js: 'javascript',
        jsx: 'jsx',
        ts: 'typescript',
        tsx: 'tsx',
        py: 'python',
        rb: 'ruby',
        yml: 'yaml',
        md: 'markdown',
        mmd: 'mermaid',
        vega: 'json',
        vg: 'json',
        'vega-lite': 'json',
        vegalite: 'json',
        vl: 'json'
    };
    return map[normalized] || normalized;
}

function normalizeAlignmentValue(value) {
    if (!value) {
        return '';
    }
    const normalized = value.toLowerCase();
    if (normalized === 'start') {
        return 'left';
    }
    if (normalized === 'end') {
        return 'right';
    }
    if (['left', 'center', 'right'].includes(normalized)) {
        return normalized;
    }
    return '';
}

function detectCellAlignment(cell) {
    if (!(cell instanceof Element)) {
        return '';
    }

    const attrAlign = normalizeAlignmentValue(cell.getAttribute('align'));
    if (attrAlign) {
        return attrAlign;
    }

    const dataAlign = normalizeAlignmentValue(cell.dataset?.align);
    if (dataAlign) {
        return dataAlign;
    }

    const inlineAlign = normalizeAlignmentValue(cell.style?.textAlign);
    if (inlineAlign) {
        return inlineAlign;
    }

    if (typeof window !== 'undefined' && typeof window.getComputedStyle === 'function') {
        try {
            const computed = window.getComputedStyle(cell);
            const computedAlign = normalizeAlignmentValue(computed?.textAlign);
            if (computedAlign) {
                return computedAlign;
            }
        } catch (_) {
            // Ignore computed style errors
        }
    }

    return '';
}

function alignmentToMarkdown(align) {
    switch (align) {
        case 'center':
            return ':---:';
        case 'right':
            return '---:';
        default:
            return '---';
    }
}

function extractTableMarkdown(tableWrapper) {
    const table = tableWrapper.querySelector('table');
    if (!table) {
        return '';
    }

    const rows = table.querySelectorAll('tr');
    const markdownLines = [];
    const columnAlignments = [];

    rows.forEach((row, index) => {
        const cells = row.querySelectorAll('th, td');
        if (!cells.length) {
            return;
        }

        cells.forEach((cell, cellIndex) => {
            if (columnAlignments[cellIndex]) {
                return;
            }
            const detected = detectCellAlignment(cell);
            if (detected) {
                columnAlignments[cellIndex] = detected;
            }
        });

        const cellContents = Array.from(cells).map(cell => cell.textContent.trim());
        markdownLines.push(`| ${cellContents.join(' | ')} |`);

        if (index === 0 && row.querySelector('th')) {
            const alignmentLine = cellContents.map((_, cellIndex) => alignmentToMarkdown(columnAlignments[cellIndex]));
            markdownLines.push(`| ${alignmentLine.join(' | ')} |`);
        }
    });

    return markdownLines.join('\n');
}

function showTooltip(text, x, y) {
    const tooltip = document.createElement('div');
    tooltip.className = 'copied-tooltip';
    tooltip.textContent = text;
    tooltip.style.left = `${x}px`;
    tooltip.style.top = `${y}px`;
    document.body.appendChild(tooltip);

    setTimeout(() => {
        tooltip.style.opacity = '0';
        setTimeout(() => {
            if (tooltip.parentNode) {
                tooltip.parentNode.removeChild(tooltip);
            }
        }, 200);
    }, 1500);
}

function ensureMarkdownObserver() {
    if (markdownMutationObserver || typeof MutationObserver === 'undefined') {
        return;
    }
    const container = document.getElementById('chatAreaContainer');
    if (!container) {
        return;
    }
    markdownMutationObserver = new MutationObserver(mutations => {
        mutations.forEach(mutation => {
            mutation.addedNodes.forEach(node => {
                if (!(node instanceof Element)) {
                    return;
                }
                if (node.classList.contains('code-block-content')) {
                    applySyntaxHighlighting(node);
                    updateVisibleCodeBlockHeights(node);
                    return;
                }
                if (node.querySelectorAll) {
                    applySyntaxHighlighting(node);
                    updateVisibleCodeBlockHeights(node);
                }
            });
        });
    });

    markdownMutationObserver.observe(container, { childList: true, subtree: true });
}

function updateVisibleCodeBlockHeights(root) {
    if (!root) {
        return;
    }
    const elements = [];
    const seen = new Set();

    if (root instanceof Element && root.classList.contains('code-block-content')) {
        elements.push(root);
    }

    if (root.querySelectorAll) {
        root.querySelectorAll('.code-block-content').forEach(el => {
            elements.push(el);
        });
    }

    elements.forEach(el => {
        if (seen.has(el)) {
            return;
        }
        seen.add(el);
        if (el.classList.contains('collapsed')) {
            return;
        }
        el.style.maxHeight = 'none';
    });
}






