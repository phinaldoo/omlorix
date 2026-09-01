(function (root) {
    'use strict';

    const modules = root.__omlorixCanvasWidgetModules ||= {};

    function initializeLifecycle(deps, state) {
        const {
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
        } = deps;
        function initResultWidget(node) {
            if (!node || node.dataset.canvasWidgetInit === 'true') return;
            node.dataset.canvasWidgetInit = 'true';
    
            const fileId = String(node.dataset.canvasFileId || '').trim();
            const contentType = normalizeContentType(node.dataset.canvasContentType);
            const fileName = resolveDisplayCanvasFileName(node.dataset.canvasFileName, contentType);
            const button = node.querySelector('[data-canvas-open="true"]');
    
            if (button && fileId) {
                button.addEventListener('click', () => {
                    if (state.previewVisible && state.activeDraftKey === fileId) {
                        hidePreviewPanel();
                    } else {
                        openPreviewForFile(fileId, fileName, contentType);
                    }
                });
            }
    
            if (fileId) {
                canvasFileIds.add(fileId);
                registerCanvasFile(fileId, fileName, contentType);
            }
            refreshWidgetOpenButtonStates();
        }
    
        function scanForWidgets(root) {
            if (!root) return;
            const widgets = root.querySelectorAll('.canvas-markdown-result-widget:not([data-canvas-widget-init="true"])');
            widgets.forEach((widget) => initResultWidget(widget));
        }
    
        function reset() {
            clearHtmlRenderTimer();
            clearMarkdownStreamingRenderSchedule();
            // A chat transition must not erase an edit made inside the autosave
            // debounce window. Start every spreadsheet persistence request while
            // its editor and draft are still reachable; generation invalidation
            // below prevents those completions from rebuilding the cleared UI.
            draftMap.forEach((draft, draftKey) => {
                if (!SPREADSHEET_CONTENT_TYPES.has(normalizeContentType(draft?.contentType))) return;
                // The focused input may not have emitted blur/onChange yet, so
                // commit it before deciding whether this draft needs persistence.
                draft?.spreadsheetEditor?.commitPendingEdit?.();
                const state = getDraftEditState(draftKey, `revision:${draft?.canvasRevision || 0}`);
                if (draft?.spreadsheetEditor && state?.dirty) {
                    void saveActiveDraftEdits(draftKey);
                }
            });
            state.draftLifecycleGeneration += 1;
            draftMap.clear();
            draftScrollStates.clear();
            draftEditStateMap.clear();
            htmlPreviewPermissionMap.clear();
            htmlExternalResourcePromptTimers.forEach((timer) => clearTimeout(timer));
            htmlExternalResourcePromptTimers.clear();
            previewRenderTimers.forEach((timer) => clearTimeout(timer));
            previewRenderTimers.clear();
            // Clearing invalidates every in-flight response because its captured
            // token can no longer equal the current map entry.
            latexRenderRequestTokens.clear();
            // Keep already-started saves discoverable across chat navigation. If
            // the same file is reopened immediately, its loader must await those
            // bytes before requesting the next validated server snapshot. Each
            // promise removes itself from this map when it settles.
            filePreviewLoadTokens.clear();
            autoSaveTimers.forEach((timer) => clearTimeout(timer));
            autoSaveTimers.clear();
            destroyActiveMarkdownEditor();
            destroyActiveSpreadsheetEditor({ persistPending: false });
            resetSelectablePdfPreviewRendering();
            state.activeStreamingMarkdownPreview = null;
            state.pendingCanvasToolScrollSnapshot = null;
            state.suppressUserScrollEvents = false;
            state.activeDraftKey = '';
            state.activeCanvasToolCallKey = '';
            canvasToolCallKeysByMessage.clear();
            terminalCanvasToolCallKeys.clear();
            canvasFileIds.clear();
            state.lastActiveMessageId = '';
            state.activeFileContext = null;
            state.activeReferenceSelection = null;
            state.currentHtmlViewMode = 'preview';
            state.htmlPreviewAvailable = true;
            if (state.shareModalOpen) {
                closeShareModal();
            }
            closeHtmlExternalResourceModal({ restoreFocus: false });
            if (previewTrack) previewTrack.innerHTML = '';
            if (previewTitle) previewTitle.textContent = t('canvas_preview_title', 'Canvas Preview');
            if (previewStatus) {
                previewStatus.textContent = t('canvas_preview_waiting', 'Waiting for canvas tool…');
                previewStatus.classList.remove('generating', 'complete', 'unsaved', 'error');
            }
            if (previewDownload) {
                setPreviewDownloadEnabled(false);
                delete previewDownload.dataset.fileId;
                delete previewDownload.dataset.contentType;
                delete previewDownload.dataset.fileName;
                delete previewDownload.dataset.pdfFileId;
                delete previewDownload.dataset.sourceFileId;
                delete previewDownload.dataset.pdfFileName;
                delete previewDownload.dataset.sourceFileName;
            }
            if (previewDownloadFormat) {
                previewDownloadFormat.hidden = true;
                previewDownloadFormat.disabled = true;
                previewDownloadFormat.value = 'md';
                delete previewDownloadFormat.dataset.contentType;
            }
            if (previewPanel) {
                previewPanel.classList.remove('is-streaming-markdown');
                previewPanel.removeAttribute('data-content-type');
            }
            setPanelVisible(false);
            // Reset toggle button states
            const codeBtn = document.getElementById('canvas-html-ViewCodeBtn');
            const previewBtn = document.getElementById('canvas-html-ViewPreviewBtn');
            if (codeBtn) codeBtn.classList.remove('active');
            if (previewBtn) previewBtn.classList.add('active');
            updateHtmlToggleButtons();
            refreshWidgetOpenButtonStates();
            updateCopyButtonState('');
            hideReferenceToolbar();
            updateEditorActionButtons(null, null);
        }
    
        /* ── Fullscreen HTML Preview ── */
        function openHtmlFullscreen() {
            const draft = draftMap.get(state.activeDraftKey);
            if (!draft || normalizeContentType(draft.contentType) !== 'html' || !state.htmlPreviewAvailable) return;
            const htmlContent = getRenderableContentForDraft(state.activeDraftKey, draft.content || '');
            if (!htmlContent) return;
    
            const existing = document.querySelector('.canvas-html-fullscreen-overlay');
            if (existing) existing.remove();
    
            const overlay = document.createElement('div');
            overlay.className = 'canvas-html-fullscreen-overlay';
    
            const content = document.createElement('div');
            content.className = 'canvas-html-fullscreen-content';
    
            const closeBtn = document.createElement('button');
            closeBtn.className = 'canvas-html-fullscreen-close';
            closeBtn.setAttribute('aria-label', t('canvas_close_fullscreen_aria', 'Close fullscreen'));
            closeBtn.innerHTML = Icons.close;
            closeBtn.addEventListener('click', closeHtmlFullscreen);
    
            const iframe = document.createElement('iframe');
            iframe.className = 'canvas-html-fullscreen-iframe';
            iframe.setAttribute('title', t('canvas_html_preview_title', 'HTML Preview'));
    
            content.appendChild(closeBtn);
            content.appendChild(iframe);
            overlay.appendChild(content);
            document.body.appendChild(overlay);
            renderHTMLPreviewInto(iframe, htmlContent, state.activeDraftKey);
    
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) closeHtmlFullscreen();
            });
    
            document.addEventListener('keydown', handleFullscreenEscape);
        }
    
        function closeHtmlFullscreen() {
            const overlay = document.querySelector('.canvas-html-fullscreen-overlay');
            if (overlay) overlay.remove();
            hideReferenceToolbar();
            document.removeEventListener('keydown', handleFullscreenEscape);
        }
    
        function handleFullscreenEscape(e) {
            if (e.key === 'Escape') closeHtmlFullscreen();
        }
    
        if (previewClose) {
            previewClose.addEventListener('click', hidePreviewPanel);
        }

        if (typeof window.registerEscapeHandler === 'function') {
            window.registerEscapeHandler({
                id: 'canvas-preview',
                priority: 70,
                isActive: () => state.previewVisible,
                close: () => {
                    if (state.shareModalOpen) {
                        closeShareModal();
                        return;
                    }
                    if (state.pendingHtmlExternalResourceConsent) {
                        resolveHtmlExternalResourceConsent(false);
                        return;
                    }
                    if (document.querySelector('.canvas-html-fullscreen-overlay')) {
                        closeHtmlFullscreen();
                        return;
                    }
                    if (htmlSettingsBtn?.getAttribute('aria-expanded') === 'true') {
                        setHtmlSettingsMenuOpen(false);
                        htmlSettingsBtn.focus({ preventScroll: true });
                        return;
                    }
                    hidePreviewPanel();
                },
            });
        }
    
        if (previewResizer) {
            previewResizer.addEventListener('pointerdown', beginPreviewResize);
            previewResizer.addEventListener('pointermove', updatePreviewResize);
            previewResizer.addEventListener('pointerup', endPreviewResize);
            previewResizer.addEventListener('pointercancel', endPreviewResize);
            previewResizer.addEventListener('dblclick', () => resetPreviewWidth());
            previewResizer.addEventListener('keydown', handlePreviewResizerKeydown);
        }
    
        // HTML view toggle buttons
        const htmlViewCodeBtn = document.getElementById('canvas-html-ViewCodeBtn');
        const htmlViewPreviewBtn = document.getElementById('canvas-html-ViewPreviewBtn');
        
        if (htmlViewCodeBtn) {
            htmlViewCodeBtn.addEventListener('click', () => setHtmlViewMode('code'));
        }
        if (htmlViewPreviewBtn) {
            htmlViewPreviewBtn.addEventListener('click', () => {
                void requestActivePreview();
            });
        }
    
        function setActiveHtmlPermission(permissionName, enabled) {
            const draft = draftMap.get(state.activeDraftKey);
            if (!draft || normalizeContentType(draft.contentType) !== 'html' || !state.htmlPreviewAvailable) return;
            const permissions = getHtmlPreviewPermissions(state.activeDraftKey);
            if (permissionName === 'allowScripts' && enabled && !permissions.allowExternalContent) {
                updateHtmlCapabilityControls();
                return;
            }
            permissions[permissionName] = enabled;
            if (permissionName === 'allowExternalContent') {
                if (!enabled) permissions.allowScripts = false;
                const content = getRenderableContentForDraft(state.activeDraftKey, draft.content || '');
                getHtmlExternalResources(content).forEach((url) => permissions.reviewedExternalResources.add(url));
                clearHtmlExternalResourcePromptTimer(state.activeDraftKey);
                if (state.pendingHtmlExternalResourceConsent?.draftKey === state.activeDraftKey) {
                    closeHtmlExternalResourceModal({ restoreFocus: false });
                }
            }
            updateHtmlCapabilityControls();
            reloadHtmlPreview();
            // The fullscreen view owns a separate iframe, so refresh it through
            // the existing renderer when the permission changes.
            if (document.querySelector('.canvas-html-fullscreen-overlay')) {
                openHtmlFullscreen();
            }
        }
    
        htmlSettingsBtn?.addEventListener('click', () => {
            const isOpen = htmlSettingsBtn.getAttribute('aria-expanded') === 'true';
            setHtmlSettingsMenuOpen(!isOpen);
        });
    
        htmlSettingsBtn?.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                setHtmlSettingsMenuOpen(true, { focus: 'first' });
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                setHtmlSettingsMenuOpen(true, { focus: 'last' });
            } else if (event.key === 'Escape') {
                setHtmlSettingsMenuOpen(false);
            }
        });
    
        // Native switches update their checked state before `change` fires. Read
        // that value rather than inverting stored state so clicks, keyboard input,
        // and assistive-technology activation all follow the same path.
        htmlScriptsBtn?.addEventListener('change', (event) => {
            setActiveHtmlPermission('allowScripts', event.currentTarget.checked);
        });
        htmlExternalContentBtn?.addEventListener('change', (event) => {
            setActiveHtmlPermission('allowExternalContent', event.currentTarget.checked);
        });
    
        htmlExternalResourceAllowBtn?.addEventListener('click', () => {
            resolveHtmlExternalResourceConsent(true);
        });
    
        htmlExternalResourceDenyBtn?.addEventListener('click', () => {
            resolveHtmlExternalResourceConsent(false);
        });
    
        htmlExternalResourceOverlay?.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                resolveHtmlExternalResourceConsent(false);
                return;
            }
            trapFocus(event, htmlExternalResourceOverlay);
        });
    
        htmlSettingsMenu?.addEventListener('keydown', (event) => {
            const items = getHtmlSettingsMenuItems();
            const itemIndex = items.indexOf(document.activeElement);
            if (event.key === 'Escape') {
                event.preventDefault();
                setHtmlSettingsMenuOpen(false);
                htmlSettingsBtn?.focus({ preventScroll: true });
                return;
            }
            if (event.key === 'Tab') {
                setHtmlSettingsMenuOpen(false);
                return;
            }
            if (itemIndex < 0) return;
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
        });
    
        document.addEventListener('pointerdown', (event) => {
            if (!htmlSettings || htmlSettingsMenu?.hidden || htmlSettings.contains(event.target)) return;
            setHtmlSettingsMenuOpen(false);
        });
    
        if (htmlReloadBtn) {
            htmlReloadBtn.addEventListener('click', reloadHtmlPreview);
        }
    
        if (markdownEditorMarkdownTab) {
            markdownEditorMarkdownTab.addEventListener('click', () => state.activeMarkdownEditorInstance?.switchView?.('source'));
        }
        if (markdownEditorEditorTab) {
            markdownEditorEditorTab.addEventListener('click', () => state.activeMarkdownEditorInstance?.switchView?.('editor'));
        }
        // Fullscreen button
        const fullscreenBtn = document.getElementById('canvas-html-FullscreenBtn');
        if (fullscreenBtn) {
            fullscreenBtn.addEventListener('click', openHtmlFullscreen);
        }
    
        if (previewShareBtn) {
            previewShareBtn.addEventListener('click', () => {
                if (!state.sharingAllowedByGroup && (!Array.isArray(state.currentShareLinks) || state.currentShareLinks.length === 0)) {
                    notifyShareError(t('canvas_share_button_disabled_admin', 'Sharing disabled by admin'));
                    return;
                }
                openShareModal();
            });
        }
    
        if (previewCopyBtn) {
            previewCopyBtn.addEventListener('click', copyRawCanvasContent);
        }
    
        if (previewSaveBtn) {
            previewSaveBtn.addEventListener('click', () => {
                saveActiveDraftEdits();
            });
        }
    
        if (previewRevertBtn) {
            previewRevertBtn.addEventListener('click', () => {
                revertActiveDraftEdits();
            });
        }
    
        if (shareOverlay) {
            shareOverlay.addEventListener('click', (event) => {
                if (event.target === shareOverlay) {
                    closeShareModal();
                }
            });
        }
    
        if (shareCloseBtn) {
            shareCloseBtn.addEventListener('click', closeShareModal);
        }
    
        if (shareModal) {
            shareModal.addEventListener('keydown', (event) => {
                if (!state.shareModalOpen) return;
                trapFocus(event, shareModal);
            });
        }
    
        if (shareLinksList) {
            shareLinksList.addEventListener('click', async (event) => {
                const button = event.target?.closest('button[data-share-action]');
                if (!button) return;
    
                const item = button.closest('.cs-link-card');
                const shareId = item?.dataset?.shareId || '';
                const action = button.dataset.shareAction || '';
    
                if (action === 'copy') {
                    const urlInput = item?.querySelector('.cs-link-url');
                    await copyShareUrl(urlInput?.value || '', button);
                    return;
                }
                if (action === 'open') {
                    const urlInput = item?.querySelector('.cs-link-url');
                    const url = urlInput?.value || '';
                    if (url) window.open(url, '_blank', 'noopener,noreferrer');
                    return;
                }
                if (action === 'edit') {
                    enterShareEditMode(getShareLinkById(shareId));
                    return;
                }
                if (action === 'delete') {
                    await runShareWithBusy(() => deleteShareLink(shareId));
                }
            });
        }
    
        if (sharePasswordToggle) {
            sharePasswordToggle.addEventListener('change', () => {
                if (!sharePasswordContent) return;
                sharePasswordContent.hidden = !sharePasswordToggle.checked;
                if (!sharePasswordToggle.checked) {
                    hideSharePasswordError();
                } else {
                    setTimeout(() => sharePasswordInput?.focus(), 50);
                }
            });
        }
    
        if (sharePasswordInput) {
            sharePasswordInput.addEventListener('input', () => {
                if (!sharePasswordToggle?.checked) return;
                const value = String(sharePasswordInput.value || '').trim();
                if (value && value.length < 8) {
                    showSharePasswordError(t('chat_share_password_min_error', 'Password must be at least 8 characters long.'));
                } else {
                    hideSharePasswordError();
                }
            });
        }
    
        if (shareExpiryToggle) {
            shareExpiryToggle.addEventListener('change', () => {
                shareExpiryToggle.checked = true;
                if (shareExpiryContent) shareExpiryContent.hidden = false;
                hideShareExpiryError();
                if (shareExpiryInput && !shareExpiryInput.value) {
                    shareExpiryInput.value = toLocalDateTimeValue(getDefaultShareExpiryIso());
                }
                setTimeout(() => shareExpiryInput?.focus(), 50);
            });
        }
    
        shareExpiryInput?.addEventListener('input', hideShareExpiryError);
        shareExpiryInput?.addEventListener('change', hideShareExpiryError);
    
        if (sharePrimaryBtn) {
            sharePrimaryBtn.addEventListener('click', () => {
                if (state.shareMode === 'list') {
                    if (!state.sharingAllowedByGroup) {
                        notifyShareError(t('canvas_share_disabled_tooltip', 'Canvas sharing is disabled for your group'));
                        return;
                    }
                    enterShareCreateMode();
                    return;
                }
                runShareWithBusy(state.shareMode === 'edit' ? updateShareLink : createShareLink);
            });
        }
    
        if (shareSecondaryBtn) {
            shareSecondaryBtn.addEventListener('click', () => {
                if (state.shareMode === 'list' || !state.currentShareLinks.length) {
                    closeShareModal();
                } else {
                    enterShareListMode();
                }
            });
        }
    
        document.addEventListener('i18n:updated', () => {
            // The menu item text changes with its checked state, so refresh it
            // explicitly after the locale switch rather than relying only on the
            // static trigger attributes.
            updateHtmlCapabilityControls();
            updateShareButtonState();
            setReferenceToolbarState(state.activeReferenceSelection);
            if (state.shareModalOpen || state.currentShareLinks.length) {
                applyShareMode();
            }
            if (shareFileName && !state.activeFileContext?.fileName && !state.shareModalOpen) {
                shareFileName.textContent = t('canvas_share_selected_file', 'Selected file');
            }
        });
    
        document.addEventListener('keydown', (event) => {
            if ((event.metaKey || event.ctrlKey) && !event.shiftKey && event.key.toLowerCase() === 's' && state.previewVisible) {
                event.preventDefault();
                saveActiveDraftEdits();
                return;
            }
            if (event.key === 'Escape' && state.shareModalOpen) {
                event.preventDefault();
                closeShareModal();
            }
        });
    
        document.addEventListener('selectionchange', () => {
            if (!state.previewVisible) return;
            refreshReferenceSelectionState();
        });
    
        if (previewTrack) {
            ['mouseup', 'keyup', 'pointerup', 'touchend'].forEach((eventName) => {
                previewTrack.addEventListener(eventName, () => {
                    refreshReferenceSelectionState();
                }, { passive: true });
            });
            previewTrack.addEventListener('scroll', () => {
                if (!state.suppressUserScrollEvents) hideReferenceToolbar();
            }, { passive: true });
        }
    
        window.addEventListener('resize', () => {
            applyPreviewWidthRatio();
            if (!state.previewVisible) return;
            refreshReferenceSelectionState();
        }, { passive: true });
        window.addEventListener('blur', () => endPreviewResize());
    
        if (previewDownload) {
            previewDownload.addEventListener('click', async (event) => {
                event.preventDefault();
                if (previewDownload.classList.contains('disabled')) return;
                const downloadContentType = String(previewDownload.dataset.contentType || '').trim();
                const defaultFormat = downloadContentType === 'markdown'
                    ? 'md'
                    : (downloadContentType === 'html'
                        ? 'html'
                        : (SPREADSHEET_CONTENT_TYPES.has(downloadContentType) ? downloadContentType : 'pdf'));
                const selectedFormat = String(previewDownloadFormat?.value || defaultFormat);
                const sourceFileId = String(previewDownload.dataset.sourceFileId || '').trim();
                const pdfFileId = String(previewDownload.dataset.pdfFileId || '').trim();
                const defaultFileId = String(previewDownload.dataset.fileId || '').trim();
                const fileName = String(previewDownload.dataset.fileName || (previewTitle ? previewTitle.textContent : '') || 'canvas.md').trim();
                const fileId = downloadContentType === 'latex'
                    ? (selectedFormat === 'tex' ? sourceFileId : (selectedFormat === 'pdf' ? pdfFileId : ''))
                    : (pdfFileId || defaultFileId);
                const saveBlob = (blob, filename) => {
                    if (window.chatDownloadControls && typeof window.chatDownloadControls.saveBlobAsFile === 'function') {
                        window.chatDownloadControls.saveBlobAsFile(blob, filename);
                        return;
                    }
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = filename;
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    URL.revokeObjectURL(url);
                };
                if (!fileId && downloadContentType !== 'html') return;
                const wasEnabled = !previewDownload.classList.contains('disabled');
                try {
                    setPreviewDownloadBusy(true, true);
                    if (SPREADSHEET_CONTENT_TYPES.has(downloadContentType)) {
                        const draft = draftMap.get(state.activeDraftKey);
                        const editor = draft?.spreadsheetEditor || state.activeSpreadsheetEditorInstance;
                        if (!editor || typeof editor.serialize !== 'function') {
                            throw new Error(t('spreadsheet_export_unavailable', 'The spreadsheet is not ready to export.'));
                        }
                        const serialized = await editor.serialize(selectedFormat);
                        saveBlob(serialized.blob, serialized.fileName);
                        return;
                    }
                    if (downloadContentType === 'markdown') {
                        const draft = draftMap.get(state.activeDraftKey);
                        const markdown = draft
                            ? getRenderableContentForDraft(state.activeDraftKey, draft.content || '')
                            : '';
                        const mdFilename = fileName && /\.(md|markdown)$/i.test(fileName) ? fileName : `${fileName || 'canvas'}.md`;
    
                        if (selectedFormat === 'pdf') {
                            if (typeof window.authedFetch !== 'function') {
                                throw new Error('Authenticated download is unavailable.');
                            }
                            const pdfFilename = mdFilename.replace(/\.(md|markdown)$/i, '.pdf');
                            const response = await window.authedFetch('/api/v1/files/canvas/markdown/pdf', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                credentials: 'include',
                                body: JSON.stringify({
                                    source_file_id: defaultFileId,
                                    markdown,
                                    filename: pdfFilename,
                                }),
                            });
                            if (!response.ok) {
                                throw new Error(`PDF download failed: ${response.status}`);
                            }
                            saveBlob(await response.blob(), pdfFilename);
                            return;
                        }
    
                        saveBlob(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }), mdFilename);
                        return;
                    }
    
                    if (downloadContentType === 'html') {
                        // Use the live editor value for both formats so unsaved
                        // changes shown in the Canvas preview are also downloaded.
                        const draft = draftMap.get(state.activeDraftKey);
                        const html = draft
                            ? getRenderableContentForDraft(state.activeDraftKey, draft.content || '')
                            : '';
                        // Generated HTML sometimes arrives wrapped in a Markdown
                        // fence or as escaped whole-document code.  Preserve the
                        // normalized authored document so downloaded interactive
                        // websites retain their scripts, handlers, and resources.
                        // The files route already forces HTML source downloads to
                        // use Content-Disposition: attachment.
                        const normalizedHtml = normalizeCanvasHtmlSource(html);
                        const htmlFilename = fileName && hasHtmlFileExtension(fileName)
                            ? fileName
                            : `${fileName || 'website'}.html`;
    
                        if (selectedFormat === 'png') {
                            // htmlFilename is guaranteed to use a recognized HTML
                            // extension above, so replace only its final suffix.
                            const pngFilename = htmlFilename.replace(/\.[^.]+$/, '.png');
                            saveBlob(await renderHtmlCanvasPngBlob(normalizedHtml), pngFilename);
                            return;
                        }
    
                        saveBlob(new Blob([normalizedHtml], { type: 'text/html;charset=utf-8' }), htmlFilename);
                        return;
                    }
    
                    if (downloadContentType === 'latex') {
                        const draft = draftMap.get(state.activeDraftKey);
                        const editState = draft
                            ? getDraftEditState(state.activeDraftKey, draft.content || '')
                            : null;
    
                        if (selectedFormat === 'tex' && draft) {
                            // Match HTML Canvas behavior: download the live editor
                            // value, including an edit whose autosave has not yet
                            // completed, instead of returning an older file blob.
                            const texSource = getRenderableContentForDraft(state.activeDraftKey, draft.content || '');
                            const configuredName = String(previewDownload.dataset.sourceFileName || 'document.tex');
                            const texFilename = /\.tex$/i.test(configuredName)
                                ? configuredName
                                : `${configuredName || 'document'}.tex`;
                            saveBlob(new Blob([texSource], { type: 'text/x-tex;charset=utf-8' }), texFilename);
                            return;
                        }
    
                        if (selectedFormat === 'pdf' && !hasCurrentLatexPdf(draft, editState)) {
                            notifyShareError(t('canvas_latex_preview_stale', 'Preview is out of date'));
                            return;
                        }
                    }
    
                    if (typeof window.authedFetch !== 'function') {
                        throw new Error('Authenticated download is unavailable.');
                    }
                    const response = await window.authedFetch(buildFileDownloadUrl(fileId));
                    if (!response.ok) {
                        throw new Error(`Download failed: ${response.status}`);
                    }
                    const blob = await response.blob();
                    const filename = selectedFormat === 'tex'
                        ? String(previewDownload.dataset.sourceFileName || 'document.tex')
                        : String(previewDownload.dataset.pdfFileName || (previewTitle ? previewTitle.textContent : 'canvas.md'));
                    saveBlob(blob, filename);
                } catch (error) {
                    console.error(error);
                    notifyShareError(t('canvas_download_failed', 'Failed to prepare download.'));
                } finally {
                    setPreviewDownloadBusy(false, wasEnabled);
                }
            });
        }
    
        if (chatArea && typeof MutationObserver !== 'undefined') {
            const observer = new MutationObserver((mutations) => {
                for (const mutation of mutations) {
                    for (const node of mutation.addedNodes) {
                        if (!node || node.nodeType !== Node.ELEMENT_NODE) continue;
                        if (node.classList && node.classList.contains('canvas-markdown-result-widget')) {
                            initResultWidget(node);
                        } else {
                            scanForWidgets(node);
                        }
                    }
                }
            });
            observer.observe(chatArea, { childList: true, subtree: true });
        }
    
        if (chatArea) {
            scanForWidgets(chatArea);
        }
    
        if (typeof window !== 'undefined' && window.chatSetup) {
            setSharingFlagFromSetup(window.chatSetup);
        }
        document.addEventListener('chatSetupReady', (event) => {
            setSharingFlagFromSetup(event.detail || {});
        });
    
        updateShareButtonState();
        refreshWidgetOpenButtonStates();
        updateCopyButtonState('');
        hideReferenceToolbar();
        updateEditorActionButtons(null, null);
        applyPreviewWidthRatio();
    
        window.canvasMarkdownWidget = {
            handleToolCallEvent,
            handleToolCallDeltaEvent,
            handleCanvasEvent,
            handleStreamEnd,
            openPreviewForFile,
            openLatexPdfPreview,
            showLatexPdfStatus,
            renderSavedWidgetFromFile,
            hidePreviewPanel,
            setHtmlViewMode,
            reset,
            // Workspace file rows use this public query to provide the same
            // open/close toggle behavior as Canvas result cards.
            isPreviewOpenForFile: (fileId) => Boolean(
                state.previewVisible && state.activeDraftKey === String(fileId || '')
            ),
            isCanvasFile: (fileId) => canvasFileIds.has(String(fileId || '')),
            isLikelyCanvasFile,
            // Deep Research deliberately uses Canvas' proven sizing behavior and
            // persisted width so every artifact preview feels like one surface.
            applyPreviewWidthRatio,
            getPreviewWidthBounds,
            getPreviewWidthRatio: () => state.canvasPreviewWidthRatio,
            resetPreviewWidth,
            setPreviewWidthFromPixels,
            setPreviewWidthFromPointerX,
        };
        window.artifactShareDialog = {
            open: openShareDialogForFile,
            setSharingAllowed: (enabled) => {
                state.sharingAllowedByGroup = enabled !== false;
                updateShareButtonState();
                void refreshExistingShareLinksForButton();
            },
            isSharingAllowed: () => state.sharingAllowedByGroup,
        };
    }

    modules.lifecycle = Object.freeze({ initialize: initializeLifecycle });
})(globalThis);
