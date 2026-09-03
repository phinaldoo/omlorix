(function (root) {
    'use strict';

    const modules = root.__omlorixCanvasWidgetModules ||= {};

    function createRenderingModule(deps, state) {
        const {
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
            openHtmlFullscreen, prepareInteractiveHtmlPreviewSource, previewPanel, previewStatus, previewTitle, previewTrack,
            queueAutoSaveForDraft, renderCSVInto, renderSavedLatexDraft, resolveDisplayCanvasFileName,
            replaceOmlorixFileUrls, refreshReferenceSelectionState,
            restoreScrollAfterMarkdownStream, runWithProgrammaticScroll, saveActiveDraftEdits, schedulePreviewRender,
            setActiveFileContext, setHtmlPreviewAvailability, setHtmlViewMode, setPreviewDownloadEnabled,
            setPreviewDownloadFormatOptions, syncDraftEditStateFromServer, syncMarkdownCompactMainLayout, t,
            updateCopyButtonState, updateDraftEditStateFromInput, updateEditorActionButtons, updateHtmlViewMode,
            updateMarkdownEditorHeaderControls, updateShareButtonState, updateStatusClass, withIframeSecurityGuard,
        } = deps;
        /* ── Markdown Rendering ── */
        function renderMarkdownInto(target, content) {
            const canvasFileId = String(arguments[2] || target?.dataset?.canvasFileId || '');
            if (!target) return;
            target.setAttribute('data-raw-content', String(content ?? ''));
            target.classList.remove('canvas-csv-render');
            if (typeof window.renderMarkdownContent === 'function') {
                window.renderMarkdownContent(
                    target,
                    replaceOmlorixFileUrls(String(content ?? ''), canvasFileId),
                );
            } else {
                target.innerHTML = '';
                target.textContent = String(content ?? '');
            }
            target.classList.add('canvas-markdown-render');
        }
    
        function renderMermaidPreviewInto(previewPane, mermaidContent) {
            if (!previewPane) return;
            const content = String(mermaidContent ?? '');
            previewPane.innerHTML = '';
            previewPane.classList.add('canvas-mermaid-preview-pane', 'canvas-preview-pane');
            const diagram = document.createElement('div');
            diagram.className = 'canvas-mermaid-diagram';
            diagram.textContent = t('canvas_mermaid_rendering', 'Rendering Mermaid diagram...');
            previewPane.appendChild(diagram);
    
            if (typeof window.renderMermaidDiagram === 'function') {
                window.renderMermaidDiagram(diagram, content).catch(() => {
                    diagram.classList.add('mermaid-diagram-error');
                    diagram.textContent = t('canvas_mermaid_render_failed', 'Failed to render Mermaid diagram.');
                });
            } else {
                diagram.classList.add('mermaid-diagram-error');
                diagram.textContent = t('canvas_mermaid_renderer_unavailable', 'Mermaid renderer is unavailable.');
            }
        }
    
        function getHtmlPreviewPermissions(draftKey = state.activeDraftKey) {
            const key = String(draftKey || '');
            let permissions = htmlPreviewPermissionMap.get(key);
            if (!permissions) {
                // Arbitrary authored scripts can navigate their own sandboxed
                // frame, so interactions and network access both require explicit
                // viewer grants. The proxy repeats this invariant defensively.
                permissions = {
                    allowScripts: false,
                    allowExternalContent: false,
                    // A denied URL should remain blocked without prompting again
                    // on every autosave render. Newly introduced URLs are still
                    // reviewed because they are not present in this session set.
                    reviewedExternalResources: new Set(),
                };
                htmlPreviewPermissionMap.set(key, permissions);
            }
            if (!(permissions.reviewedExternalResources instanceof Set)) {
                permissions.reviewedExternalResources = new Set();
            }
            return permissions;
        }
    
        /** Return normalized remote URLs discovered by the shared preview runtime. */
        function getHtmlExternalResources(htmlContent) {
            const runtime = window.OmlorixCanvasHtmlPreview;
            if (!runtime || typeof runtime.collectExternalResources !== 'function') return [];
            return runtime.collectExternalResources(String(htmlContent || ''));
        }
    
        /** Cancel a delayed consent prompt for one Canvas draft. */
        function clearHtmlExternalResourcePromptTimer(draftKey) {
            const key = String(draftKey || '');
            const timer = htmlExternalResourcePromptTimers.get(key);
            if (timer) clearTimeout(timer);
            htmlExternalResourcePromptTimers.delete(key);
        }
    
        /** Close the consent modal without changing the stored permission. */
        function closeHtmlExternalResourceModal({ restoreFocus = true } = {}) {
            if (htmlExternalResourceOverlay) {
                htmlExternalResourceOverlay.setAttribute('hidden', '');
                htmlExternalResourceOverlay.setAttribute('aria-hidden', 'true');
            }
            state.pendingHtmlExternalResourceConsent = null;
            if (restoreFocus) state.htmlExternalResourceModalReturnFocus?.focus?.({ preventScroll: true });
            state.htmlExternalResourceModalReturnFocus = null;
        }
    
        /** Display the concrete URLs that need the external-network permission. */
        function openHtmlExternalResourceModal(draftKey, resources) {
            if (!htmlExternalResourceOverlay || !htmlExternalResourceList || !resources.length) return;
            const signature = JSON.stringify(resources);
            const modalWasOpen = !htmlExternalResourceOverlay.hidden;
            if (!modalWasOpen) {
                state.htmlExternalResourceModalReturnFocus = document.activeElement instanceof HTMLElement
                    ? document.activeElement
                    : htmlSettingsBtn;
            }
    
            state.pendingHtmlExternalResourceConsent = {
                draftKey: String(draftKey || ''),
                resources: [...resources],
                signature,
            };
            htmlExternalResourceList.replaceChildren();
            resources.forEach((url) => {
                const row = document.createElement('li');
                const value = document.createElement('code');
                value.dir = 'ltr';
                value.textContent = url;
                value.title = url;
                row.appendChild(value);
                htmlExternalResourceList.appendChild(row);
            });
            setHtmlSettingsMenuOpen(false);
            htmlExternalResourceOverlay.removeAttribute('hidden');
            htmlExternalResourceOverlay.setAttribute('aria-hidden', 'false');
            if (!modalWasOpen) htmlExternalResourceDenyBtn?.focus({ preventScroll: true });
        }
    
        /**
         * Debounce automatic consent until a saved Canvas render is stable.
         * Streaming HTML may contain a temporarily incomplete URL, so prompting
         * only after generation completes prevents partial domains from appearing
         * and avoids repeated dialogs while tool arguments arrive.
         */
        function scheduleHtmlExternalResourcePrompt(draftKey, htmlContent) {
            const key = String(draftKey || '');
            const draft = draftMap.get(key);
            const permissions = getHtmlPreviewPermissions(key);
            const resources = getHtmlExternalResources(htmlContent);
            clearHtmlExternalResourcePromptTimer(key);
            if (state.pendingHtmlExternalResourceConsent
                && state.pendingHtmlExternalResourceConsent.draftKey !== key) {
                closeHtmlExternalResourceModal({ restoreFocus: false });
            }
    
            if (resources.length === 0) {
                if (state.pendingHtmlExternalResourceConsent?.draftKey === key) {
                    closeHtmlExternalResourceModal({ restoreFocus: false });
                }
                return;
            }
            if (resources.every((url) => permissions.reviewedExternalResources.has(url))) return;
            if (state.pendingHtmlExternalResourceConsent?.draftKey === key
                && state.pendingHtmlExternalResourceConsent.signature === JSON.stringify(resources)) {
                return;
            }
            if (draft?.statusKind === 'generating') return;
    
            const timer = setTimeout(() => {
                htmlExternalResourcePromptTimers.delete(key);
                const currentDraft = draftMap.get(key);
                if (!state.previewVisible || state.activeDraftKey !== key || currentDraft?.statusKind === 'generating') return;
                const currentContent = getRenderableContentForDraft(key, currentDraft?.content || '');
                const currentResources = getHtmlExternalResources(currentContent);
                const currentPermissions = getHtmlPreviewPermissions(key);
                if (currentResources.length === 0) return;
                if (currentResources.every((url) => currentPermissions.reviewedExternalResources.has(url))) return;
                openHtmlExternalResourceModal(key, currentResources);
            }, 500);
            htmlExternalResourcePromptTimers.set(key, timer);
        }
    
        /** Record a consent choice and synchronize it with the dropdown switch. */
        function resolveHtmlExternalResourceConsent(allow) {
            const request = state.pendingHtmlExternalResourceConsent;
            if (!request) {
                closeHtmlExternalResourceModal();
                return;
            }
            const permissions = getHtmlPreviewPermissions(request.draftKey);
            request.resources.forEach((url) => permissions.reviewedExternalResources.add(url));
            permissions.allowExternalContent = Boolean(allow);
            if (!permissions.allowExternalContent) permissions.allowScripts = false;
            closeHtmlExternalResourceModal();
    
            if (state.activeDraftKey === request.draftKey) {
                updateHtmlCapabilityControls();
                // No authored HTML was mounted while the decision was pending.
                // Rebuild after either choice: approval permits connections, while
                // denial renders the same document under the blocking CSP.
                reloadHtmlPreview();
                if (document.querySelector('.canvas-html-fullscreen-overlay')) openHtmlFullscreen();
            }
        }
    
        /**
         * Keep a permission switch and its checked state in sync.
         *
         * The row label intentionally remains stable (for example, "Interactions")
         * so assistive technology announces a named switch and its state, rather
         * than an action button whose name changes after each activation.
         */
        function setHtmlCapabilityToggleState(toggle, enabled) {
            if (!toggle) return;
            toggle.checked = enabled;
        }
    
        /** Return the two switches in the settings menu's visual order. */
        function getHtmlSettingsMenuItems() {
            return [htmlExternalContentBtn, htmlScriptsBtn].filter(Boolean);
        }
    
        /** Open or close the HTML settings menu without changing either permission. */
        function setHtmlSettingsMenuOpen(isOpen, { focus = '' } = {}) {
            if (!htmlSettingsBtn || !htmlSettingsMenu) return;
            const shouldOpen = Boolean(isOpen) && !htmlSettingsBtn.disabled && !htmlSettingsBtn.hidden;
            htmlSettingsMenu.hidden = !shouldOpen;
            htmlSettings?.classList.toggle('is-open', shouldOpen);
            htmlSettingsBtn.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
    
            if (!shouldOpen || !focus) return;
            window.requestAnimationFrame(() => {
                const items = getHtmlSettingsMenuItems();
                const target = focus === 'last' ? items.at(-1) : items[0];
                target?.focus({ preventScroll: true });
            });
        }
    
        function updateHtmlCapabilityControls() {
            const draft = draftMap.get(state.activeDraftKey);
            const isHtml = normalizeContentType(draft?.contentType) === 'html';
            const permissions = getHtmlPreviewPermissions(state.activeDraftKey);
            const isUnavailable = !isHtml || !state.htmlPreviewAvailable;
            if (htmlSettings) htmlSettings.hidden = !isHtml;
            if (htmlSettingsBtn) {
                htmlSettingsBtn.hidden = !isHtml;
                htmlSettingsBtn.disabled = isUnavailable;
                htmlSettingsBtn.setAttribute('aria-disabled', isUnavailable ? 'true' : 'false');
            }
            if (htmlScriptsBtn) {
                const scriptsUnavailable = isUnavailable || !permissions.allowExternalContent;
                htmlScriptsBtn.disabled = scriptsUnavailable;
                htmlScriptsBtn.closest('.canvas-html-settings-menu-item')
                    ?.classList.toggle('is-disabled', scriptsUnavailable);
            }
            if (htmlExternalContentBtn) {
                htmlExternalContentBtn.disabled = isUnavailable;
                htmlExternalContentBtn.closest('.canvas-html-settings-menu-item')
                    ?.classList.toggle('is-disabled', isUnavailable);
            }
            if (isUnavailable) setHtmlSettingsMenuOpen(false);
            setHtmlCapabilityToggleState(
                htmlScriptsBtn,
                permissions.allowScripts,
            );
            setHtmlCapabilityToggleState(
                htmlExternalContentBtn,
                permissions.allowExternalContent,
            );
        }
    
        function renderHTMLPreviewInto(iframe, htmlContent, draftKey = '') {
            if (!iframe) return;
            const runtime = window.OmlorixCanvasHtmlPreview;
            if (!runtime || typeof runtime.render !== 'function') {
                // Fail closed when the trusted proxy runtime did not load.  The
                // source editor remains available, but no authored HTML is shown
                // because its external-resource requirements cannot be reviewed.
                iframe.removeAttribute('src');
                iframe.setAttribute('sandbox', '');
                iframe.srcdoc = withIframeSecurityGuard('');
                return;
            }
            const resolvedDraftKey = String(
                draftKey
                || iframe.closest('.canvas-html-preview-wrapper')?.dataset?.draftKey
                || state.activeDraftKey
                || ''
            );
            const resolvedDraft = draftMap.get(resolvedDraftKey);
            const resolvedCanvasFileId = String(resolvedDraft?.fileId || resolvedDraftKey || '');
            const permissions = getHtmlPreviewPermissions(resolvedDraftKey);
            const externalResources = getHtmlExternalResources(htmlContent);
            const needsExternalDecision = externalResources
                .some((url) => !permissions.reviewedExternalResources.has(url));
    
            if (needsExternalDecision) {
                // Replace any previously rendered authored document with a blank,
                // inert document before opening consent. The iframe is also hidden
                // immediately so a stale frame cannot flash while the proxy
                // processes the blank render message.
                iframe.dataset.canvasHtmlExternalConsent = 'pending';
                iframe.setAttribute('aria-busy', 'true');
                runtime.render(iframe, '', {
                    title: t('canvas_html_preview_title', 'HTML Preview'),
                    allowScripts: false,
                    allowExternalContent: false,
                });
                scheduleHtmlExternalResourcePrompt(resolvedDraftKey, htmlContent);
                return;
            }
    
            clearHtmlExternalResourcePromptTimer(resolvedDraftKey);
            if (state.pendingHtmlExternalResourceConsent?.draftKey === resolvedDraftKey) {
                closeHtmlExternalResourceModal({ restoreFocus: false });
            }
            delete iframe.dataset.canvasHtmlExternalConsent;
            iframe.setAttribute('aria-busy', 'false');
            const allowExternalContent = permissions.allowExternalContent
                && externalResources.every((url) => permissions.reviewedExternalResources.has(url));
            runtime.render(iframe, prepareInteractiveHtmlPreviewSource(htmlContent || '', resolvedCanvasFileId), {
                title: t('canvas_html_preview_title', 'HTML Preview'),
                allowScripts: permissions.allowScripts && allowExternalContent,
                // The proxy CSP is enabled only after every discovered URL in the
                // current document has been explicitly reviewed for this Canvas.
                allowExternalContent,
            });
        }
    
        /**
         * Rebuild the active HTML iframe from the latest editor state.
         *
         * Canvas keeps unsaved edits in `draftEditStateMap`, so reading through
         * `getRenderableContentForDraft()` is important: reloading from the last
         * server payload would silently discard the user's visible edits. Like the
         * code-block reload action, this also makes the preview tab active so the
         * refreshed result is immediately visible.
         */
        function reloadHtmlPreview() {
            const draft = draftMap.get(state.activeDraftKey);
            if (!draft || normalizeContentType(draft.contentType) !== 'html' || !state.htmlPreviewAvailable) {
                return false;
            }
    
            const wrapper = previewTrack?.querySelector('.canvas-html-preview-wrapper[data-content-type="html"]');
            const iframe = wrapper?.querySelector('.canvas-html-preview-iframe');
            if (!iframe || String(wrapper.dataset.draftKey || '') !== String(state.activeDraftKey || '')) {
                return false;
            }
    
            // Cancel a pending typing debounce before forcing the authoritative
            // refresh, otherwise that timer would render the same draft a second
            // time shortly after the user clicks Reload.
            clearPreviewRenderTimer(state.activeDraftKey);
            const htmlContent = getRenderableContentForDraft(state.activeDraftKey, draft.content || '');
            setHtmlViewMode('preview');
            renderHTMLPreviewInto(iframe, htmlContent, state.activeDraftKey);
            return true;
        }
    
        /**
         * Keep same-document links inside a `srcdoc` Canvas preview.
         *
         * A srcdoc document inherits the embedding Omlorix page as its base URL.
         * Without this boundary, a normal `href="#section"` therefore navigates
         * the iframe to Omlorix's own URL plus that fragment. Handle only bare
         * fragments here; every other link retains the sandbox's normal behavior.
         */
        const HTML_PREVIEW_SRCDOC_URL = 'about:srcdoc';
    
        /** Return a bare fragment from either authored or srcdoc-safe link form. */
        function getIframeFragmentHref(href) {
            const normalizedHref = String(href || '').trim();
            if (normalizedHref.startsWith('#')) return normalizedHref;
            if (normalizedHref.toLowerCase().startsWith(`${HTML_PREVIEW_SRCDOC_URL}#`)) {
                return normalizedHref.slice(HTML_PREVIEW_SRCDOC_URL.length);
            }
            return '';
        }
    
        function handleIframeFragmentNavigation(event, frameDocument, frameWindow) {
            if (event.defaultPrevented || event.button !== 0) return;
            const anchor = event.target?.closest?.('a[href]');
            const href = String(anchor?.getAttribute('href') || '').trim();
            const fragmentHref = getIframeFragmentHref(href);
            if (!fragmentHref) return;
    
            event.preventDefault();
            const encodedFragment = fragmentHref.slice(1);
            if (!encodedFragment) {
                frameWindow.scrollTo({ top: 0, left: 0, behavior: 'auto' });
                return;
            }
    
            let fragment = encodedFragment;
            try {
                fragment = decodeURIComponent(encodedFragment);
            } catch (_) {
                // Invalid percent escapes are still valid literal HTML IDs.
            }
            const target = frameDocument.getElementById(fragment)
                || frameDocument.getElementsByName(fragment)[0];
            target?.scrollIntoView({ block: 'start' });
        }
    
        /**
         * Keep fragment navigation inside the rendered HTML preview.
         *
         * Selection events deliberately stay inside the iframe. Rendered HTML
         * remains natively selectable and copyable, but only the HTML source/code
         * editor is allowed to show Canvas' Copy / Add reference tooltip.
         */
        function bindIframePreviewNavigation(iframe) {
            if (!iframe || iframe.dataset.previewNavigationEvents === 'true') return;
            iframe.dataset.previewNavigationEvents = 'true';
    
            iframe.addEventListener('load', () => {
                let frameDocument;
                let frameWindow;
                try {
                    frameDocument = iframe.contentDocument;
                    frameWindow = iframe.contentWindow;
                } catch (_) {
                    return;
                }
                if (!frameDocument || !frameWindow) return;
    
                frameDocument.addEventListener('click', (event) => {
                    handleIframeFragmentNavigation(event, frameDocument, frameWindow);
                });
            });
        }
    
        const {
            resetSelectablePdfPreviewRendering,
            renderSelectablePdfPreviewInto,
        } = canvasWidgetModules.pdfPreview.create({ t, formatT });
    
        function renderContentPreview(previewPane, contentType, content) {
            const canvasFileId = String(arguments[3] || '');
            if (!previewPane) return;
            const text = String(content ?? '');
            if (contentType === 'html') {
                renderHTMLPreviewInto(previewPane, text, canvasFileId);
                return;
            }
            if (contentType === 'mermaid') {
                renderMermaidPreviewInto(previewPane, text);
                return;
            }
            if (contentType === 'csv') {
                renderCSVInto(previewPane, text);
                return;
            }
            if (contentType === 'latex') {
                previewPane.textContent = text;
                return;
            }
            renderMarkdownInto(previewPane, text, canvasFileId);
        }
    
        /** Return the number of visual source lines represented by a text value. */
        function getCanvasCodeLineCount(value) {
            return Math.max(1, String(value ?? '').split('\n').length);
        }
    
        /**
         * Keep the lightweight raw-editor gutter aligned with its textarea.
         * Rebuild the number nodes only when the line count changes; ordinary
         * typing and horizontal scrolling therefore remain inexpensive.
         */
        function syncCanvasCodeGutter(editor, gutter, { refreshLines = false } = {}) {
            if (!editor || !gutter) return;
    
            if (refreshLines) {
                const lineCount = getCanvasCodeLineCount(editor.value);
                if (Number(gutter.dataset.lineCount || 0) !== lineCount) {
                    const fragment = document.createDocumentFragment();
                    for (let line = 1; line <= lineCount; line += 1) {
                        const number = document.createElement('span');
                        number.textContent = String(line);
                        fragment.appendChild(number);
                    }
                    gutter.replaceChildren(fragment);
                    gutter.dataset.lineCount = String(lineCount);
                }
            }
    
            gutter.scrollTop = editor.scrollTop;
        }
    
        /** Insert code-editor text without replacing the textarea or losing focus. */
        function insertCanvasCodeText(editor, text) {
            if (!editor || editor.disabled || editor.readOnly) return;
            const start = Number(editor.selectionStart) || 0;
            const end = Number(editor.selectionEnd) || start;
            editor.setRangeText(String(text ?? ''), start, end, 'end');
            editor.dispatchEvent(new Event('input', { bubbles: true }));
        }
    
        function createLatexPreviewNotice(draft, draftKey) {
            const notice = document.createElement('section');
            notice.className = 'canvas-latex-preview-notice';
            const failed = draft?.renderStatus === 'failed';
            const rendering = draft?.renderStatus === 'rendering' || draft?.previewRequested === true;
            const stale = draft?.renderStatus === 'stale';
            notice.classList.toggle('is-error', failed);
            notice.setAttribute('role', failed ? 'alert' : 'status');
            notice.setAttribute('aria-live', failed ? 'assertive' : 'polite');
    
            const messageRow = document.createElement('div');
            messageRow.className = 'canvas-latex-preview-message';
            if (rendering) {
                const spinner = document.createElement('span');
                spinner.className = 'canvas-latex-preview-spinner';
                spinner.setAttribute('aria-hidden', 'true');
                messageRow.appendChild(spinner);
            }
            const message = document.createElement('p');
            if (failed) {
                message.textContent = t(
                    'canvas_latex_render_failed_help',
                    'The source was saved. Fix the compile error and render again.',
                );
            } else if (rendering) {
                message.textContent = t('latex_pdf_compiling_pdf', 'Compiling LaTeX PDF...');
            } else if (stale) {
                message.textContent = t('canvas_latex_preview_stale', 'Preview is out of date');
            } else {
                message.textContent = t(
                    'canvas_latex_preview_pending',
                    'Save the LaTeX source to generate its PDF preview.',
                );
            }
            messageRow.appendChild(message);
            notice.appendChild(messageRow);
    
            if (failed && String(draft?.logExcerpt || '').trim()) {
                const details = document.createElement('details');
                const summary = document.createElement('summary');
                summary.textContent = t('latex_pdf_compile_log', 'Compile log');
                const log = document.createElement('pre');
                log.textContent = String(draft.logExcerpt).slice(-4000);
                details.append(summary, log);
                notice.appendChild(details);
            }
    
            if (draft?.fileId && draft?.renderStatus !== 'rendering') {
                const retry = document.createElement('button');
                retry.type = 'button';
                retry.className = 'secondary-button canvas-latex-render-btn';
                retry.textContent = t('canvas_latex_render_preview', 'Render preview');
                retry.addEventListener('click', () => {
                    void renderSavedLatexDraft(draftKey, { switchToPreview: true });
                });
                notice.appendChild(retry);
            }
            return notice;
        }
    
        /**
         * Build an accessible Canvas-body error for a file that could not be read.
         * Detailed failures do not belong in the narrow header status, where long
         * translated messages wrap between the title and controls.
         */
        function createCanvasFileLoadErrorView(draft) {
            const errorView = document.createElement('section');
            errorView.className = 'canvas-file-load-error';
            errorView.setAttribute('role', 'alert');
            errorView.setAttribute('aria-live', 'assertive');
    
            const card = document.createElement('div');
            card.className = 'canvas-file-load-error-card';
    
            const icon = document.createElement('span');
            icon.className = 'canvas-file-load-error-icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.innerHTML = getPreviewHeaderIcon('warning');
    
            const message = document.createElement('h2');
            message.className = 'canvas-file-load-error-message';
            message.textContent = String(
                draft?.loadError?.message
                || t('files_preview_load_error', 'Failed to load preview'),
            );
    
            card.append(icon, message);
    
            if (draft?.fileId) {
                const download = document.createElement('a');
                download.className = 'secondary-button canvas-file-load-error-download';
                download.href = buildFileDownloadUrl(draft.fileId);
                download.download = String(draft.fileName || '');
                download.setAttribute('aria-label', t('files_preview_download_aria', 'Download file'));
    
                const downloadIcon = document.createElement('span');
                downloadIcon.setAttribute('aria-hidden', 'true');
                downloadIcon.innerHTML = getPreviewHeaderIcon('download');
                const downloadLabel = document.createElement('span');
                downloadLabel.textContent = t('files_preview_download_file', 'Download File');
                download.append(downloadIcon, downloadLabel);
                card.appendChild(download);
            }
    
            errorView.appendChild(card);
            return errorView;
        }
    
        function renderLatexPreviewInto(previewPane, draft, draftKey) {
            const waitingForPreview = draft?.renderStatus === 'rendering' || draft?.previewRequested === true;
            previewPane.setAttribute('aria-busy', waitingForPreview ? 'true' : 'false');
            if (!draft?.pdfFileId || waitingForPreview) {
                previewPane.replaceChildren(createLatexPreviewNotice(draft, draftKey));
                return;
            }
            void renderSelectablePdfPreviewInto(previewPane, draft.pdfFileId).then(() => {
                const shouldShowNotice = draft?.renderStatus === 'failed'
                    || draft?.renderStatus === 'rendering'
                    || draft?.previewRequested === true;
                if (!previewPane.isConnected || !shouldShowNotice) return;
                previewPane.prepend(createLatexPreviewNotice(draft, draftKey));
            });
        }
    
        function createEditableCanvasView({
            draft,
            draftKey,
            contentType,
            content,
            editable,
            allowPreview,
        }) {
            const handleDraftContentChange = (nextValue) => {
                const nextContent = String(nextValue ?? '');
                const nextState = updateDraftEditStateFromInput(draftKey, nextContent);
                updateEditorActionButtons(draft, nextState);
                queueAutoSaveForDraft(draftKey);
    
                if (previewStatus) {
                    previewStatus.textContent = getPreviewStatusText(draft, nextState);
                    updateStatusClass(previewStatus.textContent, getPreviewStatusKind(draft, nextState));
                }
    
                const copyContextLabel = buildCopyContextLabel(draft.fileName, contentType);
                updateCopyButtonState(nextContent, copyContextLabel);
                if (contentType === 'latex') {
                    const currentDraft = draftMap.get(draftKey) || draft;
                    setPreviewDownloadFormatOptions('latex', Boolean(currentDraft.fileId), {
                        sourceAvailable: Boolean(currentDraft.fileId),
                        pdfAvailable: hasCurrentLatexPdf(currentDraft, nextState),
                    });
                }
                return nextState;
            };
    
            if (contentType === 'markdown' && window.ChatMarkdownBlockEditor && typeof window.ChatMarkdownBlockEditor.create === 'function') {
                state.activeMarkdownEditorInstance = window.ChatMarkdownBlockEditor.create({
                    value: content,
                    editable,
                    onChange: handleDraftContentChange,
                    onSave: () => saveActiveDraftEdits(draftKey),
                    onReferenceSelection: (data) => addMarkedSelectionAsReference(data),
                    canReferenceSelection: hasAdjacentChatComposer,
                    onStateChange: (state) => updateMarkdownEditorHeaderControls(state),
                });
    
                if (state.activeMarkdownEditorInstance?.element) {
                    state.activeMarkdownEditorInstance.element.classList.add('canvas-markdown-editor-host');
                    state.activeMarkdownEditorInstance.element.dataset.contentType = contentType;
                    state.activeMarkdownEditorInstance.element.dataset.draftKey = draftKey;
                    updateMarkdownEditorHeaderControls(state.activeMarkdownEditorInstance.getState?.());
                    return state.activeMarkdownEditorInstance.element;
                }
    
                const fallback = document.createElement('div');
                fallback.className = 'canvas-markdown-editor-host';
                fallback.dataset.contentType = contentType;
                fallback.dataset.draftKey = draftKey;
                return fallback;
            }
    
            const wrapper = document.createElement('div');
            wrapper.className = 'canvas-html-preview-wrapper preview-view';
            wrapper.dataset.allowPreview = allowPreview ? 'true' : 'false';
            wrapper.dataset.contentType = contentType;
            wrapper.dataset.draftKey = draftKey;
            if (contentType === 'mermaid') {
                wrapper.classList.add('canvas-mermaid-preview-wrapper');
            }
    
            let previewPane;
            if (contentType === 'html') {
                previewPane = document.createElement('iframe');
                previewPane.className = 'canvas-html-preview-iframe canvas-preview-pane';
                previewPane.setAttribute('loading', 'lazy');
                previewPane.setAttribute('title', t('canvas_html_preview_title', 'HTML Preview'));
            } else if (contentType === 'latex' || contentType === 'pdf') {
                previewPane = document.createElement('div');
                previewPane.className = 'canvas-pdf-document-viewer canvas-preview-pane';
                previewPane.setAttribute('role', 'document');
                previewPane.setAttribute('aria-label', contentType === 'latex'
                    ? t('latex_pdf_preview_title', 'LaTeX PDF preview')
                    : (draft.fileName || t('files_preview_pdf_title', 'Preview PDF')));
                previewPane.addEventListener('scroll', () => hideReferenceToolbar(), { passive: true });
            } else {
                previewPane = document.createElement('div');
                previewPane.className = 'canvas-preview-pane';
                if (contentType === 'markdown') previewPane.classList.add('canvas-markdown-preview-pane');
                if (contentType === 'csv') previewPane.classList.add('canvas-csv-preview-pane');
                if (contentType === 'mermaid') previewPane.classList.add('canvas-mermaid-preview-pane');
            }
    
            // Ordinary PDF files are read-only binary documents. Their app-owned
            // page renderer supplies selectable text, but they intentionally omit
            // an empty source editor and a meaningless code/preview switch.
            if (contentType === 'pdf') {
                wrapper.appendChild(previewPane);
                void renderSelectablePdfPreviewInto(previewPane, draft.fileId || draft.pdfFileId);
                updateHtmlViewMode(wrapper, true);
                return wrapper;
            }
    
            // Raw Canvas formats use the same fixed gutter concept as the
            // Markdown source editor. Keeping the gutter outside the scrolling
            // textarea leaves horizontal code scrolling independent while the
            // vertical line numbers remain synchronized.
            const editorShell = document.createElement('div');
            editorShell.className = 'canvas-html-code-shell';
            editorShell.classList.toggle('is-readonly', !editable);
    
            const editorGutter = document.createElement('div');
            editorGutter.className = 'canvas-html-code-gutter';
            editorGutter.setAttribute('aria-hidden', 'true');
    
            const editor = document.createElement('textarea');
            editor.className = 'canvas-html-code-view canvas-raw-editor';
            editor.value = content;
            editor.readOnly = !editable;
            // Read-only source must remain focusable and selectable so streamed or
            // shared HTML can still be copied or added as an exact reference.
            editor.disabled = false;
            editor.setAttribute('aria-readonly', editable ? 'false' : 'true');
            editor.spellcheck = false;
            editor.autocapitalize = 'off';
            editor.autocomplete = 'off';
            editor.autocorrect = 'off';
            editor.dataset.draftKey = draftKey;
            editor.setAttribute('aria-label', formatT('canvas_edit_source_type_aria', 'Edit {type} source', { type: getTypeLabel(contentType, 'canvas') }));
            editorShell.append(editorGutter, editor);
            syncCanvasCodeGutter(editor, editorGutter, { refreshLines: true });
    
            wrapper.appendChild(previewPane);
    
            wrapper.appendChild(editorShell);
            if (contentType === 'latex') {
                renderLatexPreviewInto(previewPane, draft, draftKey);
            } else {
                renderContentPreview(previewPane, contentType, content, draft.fileId || draftKey);
            }
            updateHtmlViewMode(wrapper, allowPreview && state.htmlPreviewAvailable);
    
            if (editable) {
                editor.addEventListener('input', () => {
                    syncCanvasCodeGutter(editor, editorGutter, { refreshLines: true });
                    const nextState = handleDraftContentChange(editor.value);
                    const nextContent = String(nextState?.draftContent ?? editor.value ?? '');
                    if (contentType !== 'latex') {
                        schedulePreviewRender(draftKey, () => {
                            if (!previewTrack || !previewTrack.contains(wrapper)) return;
                            renderContentPreview(previewPane, contentType, nextContent, draft.fileId || draftKey);
                        }, contentType === 'html' ? 160 : 110);
                    }
                });
    
                editor.addEventListener('keydown', (event) => {
                    if ((event.metaKey || event.ctrlKey) && !event.shiftKey && event.key.toLowerCase() === 's') {
                        event.preventDefault();
                        saveActiveDraftEdits(draftKey);
                        return;
                    }
                    if (event.key === 'Tab' && !event.metaKey && !event.ctrlKey && !event.altKey) {
                        event.preventDefault();
                        insertCanvasCodeText(editor, '  ');
                    }
                });
    
            }
    
            // Selection actions are useful in both editable and read-only source.
            ['select', 'keyup', 'mouseup', 'pointerup', 'touchend'].forEach((eventName) => {
                editor.addEventListener(eventName, () => {
                    refreshReferenceSelectionState();
                });
            });
    
            // Scroll synchronization is useful in read-only streaming views too,
            // and remains separate from the listener that records Canvas scroll
            // restoration state.
            editor.addEventListener('scroll', () => {
                syncCanvasCodeGutter(editor, editorGutter);
            }, { passive: true });
    
            return wrapper;
        }
    
        function refreshActiveMarkdownDraftAfterSave(draft, editState) {
            const draftKey = String(draft?.key || '');
            if (!draftKey || state.activeDraftKey !== draftKey) return false;
            if (normalizeContentType(draft.contentType) !== 'markdown') return false;
            if (!state.activeMarkdownEditorInstance?.element || !previewTrack?.contains(state.activeMarkdownEditorInstance.element)) return false;
    
            const fileName = resolveDisplayCanvasFileName(draft.fileName, 'markdown');
            const content = getRenderableContentForDraft(draftKey, draft.content || '');
            const copyContextLabel = buildCopyContextLabel(fileName, 'markdown');
    
            state.activeMarkdownEditorInstance.element.dataset.draftKey = draftKey;
            state.activeMarkdownEditorInstance.element.dataset.contentType = 'markdown';
            previewPanel?.setAttribute('data-content-type', 'markdown');
            syncMarkdownCompactMainLayout();
            setActiveFileContext(draft.fileId || '', fileName, 'markdown');
            setHtmlPreviewAvailability(true);
    
            if (previewTitle) previewTitle.textContent = fileName;
            if (previewStatus) {
                previewStatus.textContent = getPreviewStatusText(draft, editState);
            }
            updateStatusClass(
                previewStatus ? previewStatus.textContent : draft.status,
                getPreviewStatusKind(draft, editState),
            );
            updateEditorActionButtons(draft, editState);
            updateCopyButtonState(content, copyContextLabel);
            updateMarkdownEditorHeaderControls(state.activeMarkdownEditorInstance.getState?.());
    
            if (previewDownload) {
                if (draft.fileId) {
                    setPreviewDownloadEnabled(true);
                    previewDownload.dataset.fileId = draft.fileId;
                    previewDownload.dataset.contentType = 'markdown';
                    previewDownload.dataset.fileName = fileName || 'canvas.md';
                    delete previewDownload.dataset.pdfFileId;
                    delete previewDownload.dataset.sourceFileId;
                    delete previewDownload.dataset.pdfFileName;
                    delete previewDownload.dataset.sourceFileName;
                } else {
                    setPreviewDownloadEnabled(false);
                    delete previewDownload.dataset.fileId;
                    delete previewDownload.dataset.contentType;
                    delete previewDownload.dataset.fileName;
                    delete previewDownload.dataset.pdfFileId;
                    delete previewDownload.dataset.sourceFileId;
                    delete previewDownload.dataset.pdfFileName;
                    delete previewDownload.dataset.sourceFileName;
                }
            }
            setPreviewDownloadFormatOptions('markdown', Boolean(draft.fileId));
    
            return true;
        }
    
        /**
         * Refresh HTML Canvas chrome after autosave without reconstructing the
         * textarea. Replacing the editor here would drop focus, selection, and the
         * browser's undo stack every time the autosave timer completes.
         */
        function refreshActiveHtmlDraftAfterSave(draft, editState) {
            const draftKey = String(draft?.key || '');
            if (!draftKey || state.activeDraftKey !== draftKey) return false;
            if (normalizeContentType(draft.contentType) !== 'html') return false;
    
            const wrapper = previewTrack?.querySelector('.canvas-html-preview-wrapper[data-content-type="html"]');
            const editor = wrapper?.querySelector('.canvas-html-code-view');
            if (!wrapper || !editor || String(wrapper.dataset.draftKey || '') !== draftKey) return false;
    
            const fileName = resolveDisplayCanvasFileName(draft.fileName, 'html');
            const content = getRenderableContentForDraft(draftKey, draft.content || '');
            const copyContextLabel = buildCopyContextLabel(fileName, 'html');
    
            previewPanel?.setAttribute('data-content-type', 'html');
            syncMarkdownCompactMainLayout();
            setActiveFileContext(draft.fileId || '', fileName, 'html');
            setHtmlPreviewAvailability(Boolean(draft.allowHtmlPreview));
    
            if (previewTitle) previewTitle.textContent = fileName;
            if (previewStatus) previewStatus.textContent = getPreviewStatusText(draft, editState);
            updateStatusClass(
                previewStatus ? previewStatus.textContent : draft.status,
                getPreviewStatusKind(draft, editState),
            );
            updateEditorActionButtons(draft, editState);
            updateCopyButtonState(content, copyContextLabel);
    
            if (previewDownload && draft.fileId) {
                setPreviewDownloadEnabled(true);
                previewDownload.dataset.fileId = draft.fileId;
                previewDownload.dataset.contentType = 'html';
                previewDownload.dataset.fileName = fileName || 'website.html';
                delete previewDownload.dataset.pdfFileId;
                delete previewDownload.dataset.sourceFileId;
                delete previewDownload.dataset.pdfFileName;
                delete previewDownload.dataset.sourceFileName;
            }
            setPreviewDownloadFormatOptions('html', Boolean(draft.fileId));
    
            return true;
        }
    
        /**
         * Produce the same safe Markdown markup used by the canvas editor without
         * constructing an editor instance. The fallback still goes through the
         * shared chat renderer, which owns sanitizing and file-reference handling.
         */
        function renderStreamingMarkdownHtml(content) {
            const canvasFileId = String(arguments[1] || '');
            const markdown = String(content ?? '');
            const staging = document.createElement('div');
            staging.dataset.canvasFileId = canvasFileId;
            // Live Canvas output uses the same renderer as a chat message. In
            // particular, fenced code must already have the canonical wrapper and
            // preview affordances before the completed rich editor takes over.
            renderMarkdownInto(staging, markdown);
            // Streaming reconciliation transfers the staged markup via innerHTML.
            // Strip non-serializable async preview state first; users can still
            // open any preview on demand, and the completed editor hydrates its
            // normal default view after it is connected.
            window.prepareMarkdownCodeBlocksForTransfer?.(staging);
            return staging.innerHTML;
        }
    
        /**
         * Patch only the changed Markdown suffix. Streaming normally changes the
         * final paragraph, list, table, or code block; retaining the equal prefix
         * keeps images, selections, layout boxes, and previously rendered content
         * stable while the tail continues to grow.
         */
        function reconcileStreamingMarkdown(target, renderedHtml) {
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
    
            // Remove only the old unstable tail, then move the new tail out of the
            // inert template. No unchanged prefix node is detached from the page.
            while (target.childNodes.length > stablePrefixLength) {
                target.lastChild.remove();
            }
            for (let index = 0; index < stablePrefixLength; index += 1) {
                nextNodes[index].remove();
            }
            target.appendChild(template.content);
        }
    
        /**
         * Synchronize header actions for the transient Markdown preview. Editing,
         * saving, sharing, and downloading stay unavailable until the canvas file
         * exists, while copy remains useful during generation.
         */
        function syncStreamingMarkdownChrome(draft, content) {
            const fileName = resolveDisplayCanvasFileName(draft?.fileName, 'markdown');
            const copyContextLabel = buildCopyContextLabel(fileName, 'markdown');
            const nextFileId = String(draft?.fileId || '');
    
            // Avoid restarting share-link discovery for every streamed token when
            // an existing canvas file is being edited.
            if (nextFileId && (
                String(state.activeFileContext?.fileId || '') !== nextFileId
                || String(state.activeFileContext?.fileName || '') !== fileName
                || String(state.activeFileContext?.contentType || '') !== 'markdown'
            )) {
                setActiveFileContext(nextFileId, fileName, 'markdown');
            } else if (!nextFileId && state.activeFileContext) {
                setActiveFileContext('', '', '');
            }
            previewPanel?.setAttribute('data-content-type', 'markdown');
            syncMarkdownCompactMainLayout();
            previewPanel?.classList.add('is-streaming-markdown');
            if (previewTitle) previewTitle.textContent = fileName;
            if (previewStatus) previewStatus.textContent = getPreviewStatusText(draft, null);
            updateStatusClass(
                previewStatus ? previewStatus.textContent : draft?.status,
                getPreviewStatusKind(draft, null),
            );
            updateEditorActionButtons(draft, null);
            updateMarkdownEditorHeaderControls(null);
            setHtmlPreviewAvailability(true);
            setPreviewDownloadEnabled(false);
            setPreviewDownloadFormatOptions('markdown', false);
            updateCopyButtonState(content, copyContextLabel);
            hideReferenceToolbar();
        }
    
        /**
         * Render one throttled Markdown snapshot into a persistent article and
         * explicitly control scroll anchoring. User scroll always wins; automatic
         * following resumes only for a fresh canvas stream.
         */
        function renderStreamingMarkdownDraft(draft) {
            if (!draft || !previewPanel || !previewTrack) return;
            const draftKey = String(draft.key || state.activeDraftKey || '');
            const content = String(draft.content || '');
            const scrollState = getScrollState(draftKey);
            const previousScrollTop = scrollState?.restoreOnNextRender
                ? getStoredMarkdownScrollTop(scrollState)
                : previewTrack.scrollTop;
            const shouldFollow = Boolean(scrollState?.autoFollow && !scrollState?.userInterrupted);
    
            syncStreamingMarkdownChrome(draft, content);
    
            let preview = state.activeStreamingMarkdownPreview?.element || null;
            if (
                !preview
                || !preview.isConnected
                || state.activeStreamingMarkdownPreview?.draftKey !== draftKey
            ) {
                destroyActiveMarkdownEditor();
                resetSelectablePdfPreviewRendering();
                previewTrack.innerHTML = '';
                preview = document.createElement('article');
                preview.className = 'canvas-markdown-streaming-preview canvas-markdown-render markdown-body';
                preview.setAttribute('aria-label', t('canvas_preview_title', 'Canvas Preview'));
                previewTrack.appendChild(preview);
                state.activeStreamingMarkdownPreview = { draftKey, element: preview };
                attachScrollListeners(null);
            }
    
            const renderedHtml = renderStreamingMarkdownHtml(content, draft.fileId || draftKey);
            const restoreScroll = () => {
                if (
                    !previewTrack
                    || state.activeDraftKey !== draftKey
                    || !preview.isConnected
                    || state.activeStreamingMarkdownPreview?.element !== preview
                ) return;
                runWithProgrammaticScroll(() => {
                    previewTrack.scrollLeft = 0;
                    previewTrack.scrollTop = shouldFollow
                        ? Math.max(previewTrack.scrollHeight - previewTrack.clientHeight, 0)
                        : Math.max(Number(previousScrollTop) || 0, 0);
                });
                if (scrollState) {
                    scrollState.trackScrollTop = previewTrack.scrollTop;
                    scrollState.trackScrollLeft = 0;
                }
            };
    
            // Scroll events caused by the DOM patch must not be mistaken for a
            // user's wheel, touch, or scrollbar gesture.
            runWithProgrammaticScroll(() => reconcileStreamingMarkdown(preview, renderedHtml));
            preview.setAttribute('data-rendered-raw-content', content);
            restoreScroll();
            requestAnimationFrame(restoreScroll);
            state.markdownStreamLastRenderAt = Date.now();
        }
    
        /** Cancel a queued Markdown snapshot so a final/saved render cannot race it. */
        function clearMarkdownStreamingRenderSchedule() {
            if (state.markdownStreamRenderTimer) {
                clearTimeout(state.markdownStreamRenderTimer);
                state.markdownStreamRenderTimer = null;
            }
            state.pendingMarkdownStreamDraft = null;
            state.markdownStreamLastRenderAt = 0;
        }
    
        /** Render the newest queued snapshot and discard superseded deltas. */
        function flushMarkdownStreamingRender() {
            state.markdownStreamRenderTimer = null;
            const draft = state.pendingMarkdownStreamDraft;
            state.pendingMarkdownStreamDraft = null;
            if (draft) renderStreamingMarkdownDraft(draft);
        }
    
        /**
         * Coalesce high-frequency deltas into at most ten visual updates per
         * second. The first snapshot is immediate so the canvas feels responsive.
         */
        function scheduleMarkdownStreamingRender(draft, { immediate = false } = {}) {
            if (!draft) return;
            state.pendingMarkdownStreamDraft = draft;
            const elapsed = Date.now() - state.markdownStreamLastRenderAt;
            if (immediate || !state.markdownStreamLastRenderAt || elapsed >= MARKDOWN_STREAM_RENDER_INTERVAL_MS) {
                if (state.markdownStreamRenderTimer) {
                    clearTimeout(state.markdownStreamRenderTimer);
                    state.markdownStreamRenderTimer = null;
                }
                flushMarkdownStreamingRender();
                return;
            }
            if (state.markdownStreamRenderTimer) return;
            state.markdownStreamRenderTimer = setTimeout(
                flushMarkdownStreamingRender,
                Math.max(MARKDOWN_STREAM_RENDER_INTERVAL_MS - elapsed, 0)
            );
        }
    
        async function renderSpreadsheetDraft(draft, draftKey, contentType) {
            const fileName = resolveDisplayCanvasFileName(draft.fileName, contentType);
            const spreadsheetData = draft.binaryContent || (
                ['csv', 'tsv'].includes(contentType) && typeof draft.content === 'string'
                    ? new TextEncoder().encode(draft.content).buffer
                    : null
            );
            const editState = getDraftEditState(draftKey, `revision:${draft.canvasRevision || 0}`);
            if (!editState.dirty && !editState.saving) {
                editState.baselineContent = `revision:${draft.canvasRevision || 0}`;
                editState.draftContent = editState.baselineContent;
            }
    
            setActiveFileContext(draft.fileId || '', fileName, contentType);
            if (previewTitle) previewTitle.textContent = fileName;
            if (previewStatus) previewStatus.textContent = getPreviewStatusText(draft, editState);
            updateStatusClass(previewStatus?.textContent || draft.status, getPreviewStatusKind(draft, editState));
            updateEditorActionButtons(draft, editState);
            previewPanel.setAttribute('data-content-type', contentType);
            updateShareButtonState();
            syncMarkdownCompactMainLayout();
            previewPanel.classList.remove('is-streaming-markdown');
            destroyActiveMarkdownEditor();
            if (state.activeSpreadsheetEditorDraftKey !== draftKey || !spreadsheetData) {
                // A same-file reload deliberately clears binaryContent while it
                // fetches a fresh server snapshot. Detach the old editor too, or
                // the next render would reattach its stale workbook instance.
                destroyActiveSpreadsheetEditor();
            }
            const reusableEditor = draft.spreadsheetEditor
                && draft.spreadsheetEditor === state.activeSpreadsheetEditorInstance
                ? draft.spreadsheetEditor
                : null;
            const token = ++state.spreadsheetRenderToken;
            resetSelectablePdfPreviewRendering();
            previewTrack.replaceChildren();
            setHtmlPreviewAvailability(false);
            updateHtmlCapabilityControls();
            updateMarkdownEditorHeaderControls(null);
    
            if (!spreadsheetData) {
                const loading = document.createElement('div');
                loading.className = 'canvas-markdown-loading';
                loading.textContent = draft.status || t('canvas_status_loading', 'Loading canvas…');
                previewTrack.appendChild(loading);
            } else if (!window.ChatSpreadsheetEditor?.create) {
                const error = document.createElement('div');
                error.className = 'canvas-markdown-error';
                error.textContent = t('spreadsheet_library_unavailable', 'Spreadsheet support is unavailable. Reload the page and try again.');
                previewTrack.appendChild(error);
            } else {
                const host = document.createElement('div');
                host.className = 'canvas-spreadsheet-host';
                host.setAttribute('aria-busy', reusableEditor ? 'false' : 'true');
                previewTrack.appendChild(host);
                if (reusableEditor) {
                    host.replaceChildren(reusableEditor.element);
                }
                try {
                    const editor = reusableEditor || await window.ChatSpreadsheetEditor.create({
                            data: spreadsheetData,
                            fileName,
                            format: contentType,
                            editable: Boolean(draft.fileId),
                            requiresRecalculation: draft.spreadsheetRequiresRecalculation === true,
                            onChange: ({ dirty }) => {
                                const currentDraft = draftMap.get(draftKey);
                                const currentState = getDraftEditState(draftKey, `revision:${currentDraft?.canvasRevision || 0}`);
                                if (!currentState) return;
                                currentState.dirty = Boolean(dirty);
                                currentState.autoSavePending = Boolean(dirty);
                                currentState.error = '';
                                currentState.updatedAt = Date.now();
                                currentState.editSeq = (Number(currentState.editSeq) || 0) + 1;
                                if (previewStatus && state.activeDraftKey === draftKey) {
                                    previewStatus.textContent = getPreviewStatusText(currentDraft, currentState);
                                    updateStatusClass(previewStatus.textContent, getPreviewStatusKind(currentDraft, currentState));
                                }
                                updateEditorActionButtons(currentDraft, currentState);
                                if (dirty) queueAutoSaveForDraft(draftKey);
                            },
                        });
                    if (token !== state.spreadsheetRenderToken || state.activeDraftKey !== draftKey || !host.isConnected) {
                        if (!reusableEditor) editor.destroy?.();
                        return;
                    }
                    state.activeSpreadsheetEditorInstance = editor;
                    state.activeSpreadsheetEditorDraftKey = draftKey;
                    draft.spreadsheetEditor = editor;
                    if (!reusableEditor) host.replaceChildren(editor.element);
                    host.setAttribute('aria-busy', 'false');
                } catch (error) {
                    if (token !== state.spreadsheetRenderToken || !host.isConnected) return;
                    host.setAttribute('aria-busy', 'false');
                    host.classList.add('canvas-markdown-error');
                    host.textContent = error?.message || t('files_preview_load_error', 'Failed to load preview');
                    console.error(error);
                }
            }
    
            if (previewDownload && draft.fileId) {
                setPreviewDownloadEnabled(true);
                previewDownload.dataset.fileId = draft.fileId;
                previewDownload.dataset.contentType = contentType;
                previewDownload.dataset.fileName = fileName;
            } else {
                setPreviewDownloadEnabled(false);
            }
            setPreviewDownloadFormatOptions(contentType, Boolean(draft.fileId));
            updateCopyButtonState('', '');
            hideReferenceToolbar();
        }
    
        /* ── Main Render Function ── */
        function renderDraft(draft) {
            if (!draft || !previewPanel || !previewTrack) return;
    
            const draftKey = draft.key || state.activeDraftKey || '';
            const wasStreamingMarkdown = Boolean(
                state.activeStreamingMarkdownPreview?.draftKey === draftKey
                && state.activeStreamingMarkdownPreview?.element?.isConnected
            );
            const streamingScrollState = wasStreamingMarkdown ? getScrollState(draftKey) : null;
            const streamingScrollTop = wasStreamingMarkdown
                ? (streamingScrollState?.restoreOnNextRender
                    ? getStoredMarkdownScrollTop(streamingScrollState)
                    : previewTrack.scrollTop)
                : 0;
            const streamingWasFollowing = Boolean(
                streamingScrollState?.autoFollow && !streamingScrollState?.userInterrupted
            );
            const contentType = normalizeContentType(draft.contentType);
            const hasLoadError = Boolean(draft.loadError?.message);
            if (!hasLoadError
                && SPREADSHEET_CONTENT_TYPES.has(contentType)
                && (draft.binaryContent || draft.fileId)) {
                void renderSpreadsheetDraft(draft, draftKey, contentType);
                return;
            }
            if (state.pendingHtmlExternalResourceConsent
                && (contentType !== 'html' || state.pendingHtmlExternalResourceConsent.draftKey !== draftKey)) {
                closeHtmlExternalResourceModal({ restoreFocus: false });
            }
            const serverContent = String(draft.content || '');
            const editable = isDraftEditorInteractive(draft);
            const allowPreview = contentType === 'html'
                ? Boolean(draft.allowHtmlPreview)
                // A LaTeX preview without a PDF is still meaningful: it contains
                // compilation progress, an actionable error log, and the retry
                // control. Keeping the tab enabled is especially important after
                // the very first render fails, when no older derivative exists.
                : (contentType === 'latex'
                    ? true
                    : (contentType === 'pdf' ? Boolean(draft.fileId || draft.pdfFileId) : true));
            const editState = syncDraftEditStateFromServer(draftKey, serverContent, { force: !editable });
            const content = getRenderableContentForDraft(draftKey, serverContent);
    
            captureScrollState(draftKey);
    
            const fileName = resolveDisplayCanvasFileName(draft.fileName, contentType);
            const copyContextLabel = buildCopyContextLabel(fileName, contentType);
            if (contentType === 'latex') {
                setActiveFileContext(draft.pdfFileId || '', draft.pdfFileName || draft.title || 'document.pdf', 'pdf');
            } else {
                setActiveFileContext(draft.fileId || '', fileName, contentType);
            }
    
            const sidebarTitle = contentType === 'latex'
                ? String(draft.title || draft.pdfFileName || fileName || t('latex_pdf_default_title', 'LaTeX PDF')).trim()
                : fileName;
            if (previewTitle) previewTitle.textContent = sidebarTitle;
            if (previewStatus) {
                previewStatus.textContent = getPreviewStatusText(draft, editState);
            }
            updateStatusClass(
                previewStatus ? previewStatus.textContent : draft.status,
                getPreviewStatusKind(draft, editState),
            );
            updateEditorActionButtons(draft, editState);
    
            // Update panel class for type-specific styling
            previewPanel.setAttribute('data-content-type', contentType);
            previewPanel.setAttribute('data-load-error', hasLoadError ? 'true' : 'false');
            // setActiveFileContext runs before the panel type changes. Recompute
            // here so opening or leaving a PDF updates Share in the same render.
            updateShareButtonState();
            syncMarkdownCompactMainLayout();
            previewPanel.classList.remove('is-streaming-markdown');
            state.activeStreamingMarkdownPreview = null;
    
            destroyActiveMarkdownEditor();
            destroyActiveSpreadsheetEditor();
            resetSelectablePdfPreviewRendering();
            previewTrack.innerHTML = '';
    
            const view = hasLoadError
                ? createCanvasFileLoadErrorView(draft)
                : createEditableCanvasView({
                    draft,
                    draftKey,
                    contentType,
                    content,
                    editable,
                    allowPreview,
                });
            setHtmlPreviewAvailability(!hasLoadError && allowPreview);
            updateHtmlCapabilityControls();
    
            if (hasLoadError || contentType === 'markdown') {
                previewTrack.appendChild(view);
            } else {
                const pageEl = document.createElement('section');
                pageEl.className = 'canvas-markdown-preview-page';
                pageEl.setAttribute('data-content-type', contentType);
    
                const contentEl = document.createElement('div');
                contentEl.className = 'canvas-markdown-preview-page-content';
                if (contentType === 'html') {
                    contentEl.classList.remove('canvas-markdown-preview-page-content');
                    contentEl.classList.add('canvas-html-preview-body');
                }
                if (contentType === 'latex' || contentType === 'pdf') {
                    contentEl.classList.remove('canvas-markdown-preview-page-content');
                    contentEl.classList.add(
                        'canvas-html-preview-body',
                        contentType === 'pdf' ? 'canvas-pdf-preview-body' : 'canvas-latex-preview-body',
                    );
                }
    
                contentEl.appendChild(view);
                pageEl.appendChild(contentEl);
                previewTrack.appendChild(pageEl);
            }
            applyScrollState(draftKey, draft);
            if (wasStreamingMarkdown) {
                restoreScrollAfterMarkdownStream(draftKey, streamingScrollTop, streamingWasFollowing);
            }
    
            if (previewDownload) {
                if (contentType === 'latex' && (draft.pdfFileId || draft.fileId)) {
                    setPreviewDownloadEnabled(true);
                    previewDownload.dataset.fileId = draft.pdfFileId || draft.fileId;
                    previewDownload.dataset.contentType = 'latex';
                    previewDownload.dataset.fileName = draft.pdfFileName || fileName || 'document.pdf';
                    previewDownload.dataset.pdfFileId = draft.pdfFileId || '';
                    previewDownload.dataset.sourceFileId = draft.fileId || '';
                    previewDownload.dataset.pdfFileName = draft.pdfFileName || 'document.pdf';
                    previewDownload.dataset.sourceFileName = fileName || 'document.tex';
                } else if (draft.fileId) {
                    setPreviewDownloadEnabled(true);
                    previewDownload.dataset.fileId = draft.fileId;
                    previewDownload.dataset.contentType = contentType;
                    previewDownload.dataset.fileName = fileName;
                    delete previewDownload.dataset.pdfFileId;
                    delete previewDownload.dataset.sourceFileId;
                    delete previewDownload.dataset.pdfFileName;
                    delete previewDownload.dataset.sourceFileName;
                } else {
                    setPreviewDownloadEnabled(false);
                    delete previewDownload.dataset.fileId;
                    delete previewDownload.dataset.contentType;
                    delete previewDownload.dataset.fileName;
                    delete previewDownload.dataset.pdfFileId;
                    delete previewDownload.dataset.sourceFileId;
                    delete previewDownload.dataset.pdfFileName;
                    delete previewDownload.dataset.sourceFileName;
                }
            }
            if (contentType === 'latex') {
                const sourceAvailable = Boolean(draft.fileId);
                const pdfAvailable = hasCurrentLatexPdf(draft, editState);
                setPreviewDownloadFormatOptions('latex', sourceAvailable || pdfAvailable, {
                    sourceAvailable,
                    pdfAvailable,
                });
            } else {
                setPreviewDownloadFormatOptions(contentType, Boolean(draft.fileId || draft.pdfFileId));
            }
    
            updateCopyButtonState(hasLoadError ? '' : content, copyContextLabel);
            refreshReferenceSelectionState();
        }
    
        function clearHtmlRenderTimer() {
            clearTimeout(state.renderDebounceTimer);
            state.renderDebounceTimer = null;
            state.pendingHtmlRenderDraft = null;
        }
    
        function scheduleHtmlStreamingRender(draft) {
            if (!draft) return;
            state.pendingHtmlRenderDraft = draft;
    
            if (state.renderDebounceTimer) {
                return;
            }
    
            renderDraft(state.pendingHtmlRenderDraft);
            state.pendingHtmlRenderDraft = null;
            state.renderDebounceTimer = setTimeout(() => {
                const nextDraft = state.pendingHtmlRenderDraft;
                state.renderDebounceTimer = null;
                state.pendingHtmlRenderDraft = null;
                if (nextDraft) {
                    scheduleHtmlStreamingRender(nextDraft);
                }
            }, RENDER_DEBOUNCE_MS);
        }
    

        return Object.freeze({
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
        });
    }

    modules.rendering = Object.freeze({ create: createRenderingModule });
})(globalThis);
