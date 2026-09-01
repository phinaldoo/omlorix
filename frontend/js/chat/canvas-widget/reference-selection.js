(function (root) {
    'use strict';

    const modules = root.__omlorixCanvasWidgetModules ||= {};

    function createReferenceSelectionModule(deps, state) {
        const {
            SPREADSHEET_CONTENT_TYPES, draftMap, getTypeLabel, normalizeContentType,
            previewPanel, previewTrack, resolveDisplayCanvasFileName, t,
        } = deps;
        function getReferenceToolbar() {
            if (state.referenceToolbarEl) return state.referenceToolbarEl;
            state.referenceToolbarController = window.createSelectionActionTooltip({
                className: 'canvas-reference-floating-toolbar',
                getSelectionText: () => (
                    state.activeReferenceSelection?.text
                    || readCurrentArtifactSelection()?.text
                    || ''
                ),
                onAddReference: () => addMarkedSelectionAsReference(),
                onEmptySelection: () => notifyReferenceError(),
                clearSelection: clearCurrentArtifactSelection,
                getLabels: () => ({
                    copyLabel: t('chat_selection_copy_label', 'Copy'),
                    copyTitle: t('chat_selection_copy_title', 'Copy'),
                    addReferenceLabel: t('canvas_add_selection_reference_label', 'Add reference'),
                    addReferenceTitle: t('canvas_add_selection_reference_aria', 'Add marked selection as reference'),
                }),
            });
            state.referenceToolbarEl = state.referenceToolbarController.element;
            document.body.appendChild(state.referenceToolbarEl);
            return state.referenceToolbarEl;
        }
    
        /**
         * Return whether Canvas currently has a visible chat composer to receive a
         * reference chip.
         *
         * Canvas is also the document viewer for Workspace files. In that layout
         * the chat DOM still exists, and `window.addReferencePart` is still
         * registered, but both are hidden behind the Workspace route. Checking the
         * callback alone would therefore expose an action whose destination is not
         * on screen. Keep this decision based on the live layout so every Canvas
         * entry point (tool result, chat attachment, Workspace file, and split
         * screen) follows the same rule.
         */
        function hasAdjacentChatComposer() {
            const chatContainer = document.getElementById('chatContainer');
            const chatComposer = document.getElementById('chatBoxArea');
            if (!chatContainer || !chatComposer) return false;
    
            // Workspace is an explicit non-chat surface. This also closes the
            // brief navigation window before inline/computed styles have settled.
            if (document.body?.classList?.contains('workspace-view-active')) return false;
    
            const isHidden = (element) => {
                if (element.hidden || element.getAttribute('aria-hidden') === 'true') return true;
                if (element.style?.display === 'none' || element.style?.visibility === 'hidden') return true;
                try {
                    const style = window.getComputedStyle?.(element);
                    return style?.display === 'none' || style?.visibility === 'hidden';
                } catch (_) {
                    return false;
                }
            };
    
            return !isHidden(chatContainer) && !isHidden(chatComposer);
        }
    
        function hideReferenceToolbar({ clearSelection = true } = {}) {
            if (clearSelection) {
                state.activeReferenceSelection = null;
            }
            if (!state.referenceToolbarEl) return;
            state.referenceToolbarController?.hide();
            state.referenceToolbarEl.classList.remove('is-below');
            state.referenceToolbarEl.style.left = '';
            state.referenceToolbarEl.style.top = '';
        }
    
        /**
         * Collapse the marked range after either selection action, matching the
         * assistant-message tooltip. Textareas and same-origin preview iframes do
         * not participate in window.getSelection(), so clear those explicitly.
         */
        function clearCurrentArtifactSelection() {
            const selection = state.activeReferenceSelection;
            const sourceElement = selection?.element;
            if (
                sourceElement
                && typeof sourceElement.setSelectionRange === 'function'
                && typeof selection?.end === 'number'
            ) {
                sourceElement.setSelectionRange(selection.end, selection.end);
            }
    
            try {
                selection?.frameElement?.contentWindow?.getSelection?.()?.removeAllRanges?.();
            } catch (_) {
                // Cross-origin PDF viewers do not expose their Selection object.
            }
            window.getSelection?.()?.removeAllRanges?.();
            state.activeReferenceSelection = null;
        }
    
        function getVisibleTextareaRangeRect(element, start, end) {
            if (!element || typeof start !== 'number' || typeof end !== 'number' || start === end) return null;
    
            const hostRect = element.getBoundingClientRect();
            if (!hostRect.width || !hostRect.height) return null;
    
            const styles = window.getComputedStyle(element);
            const mirror = document.createElement('div');
            const selectedText = String(element.value || '').slice(start, end);
            const selectedSpan = document.createElement('span');
    
            // Mirror the textarea's text layout so the toolbar can use the selected
            // text range instead of falling back to the selection start/caret.
            [
                'boxSizing',
                'borderTopWidth',
                'borderRightWidth',
                'borderBottomWidth',
                'borderLeftWidth',
                'paddingTop',
                'paddingRight',
                'paddingBottom',
                'paddingLeft',
                'fontFamily',
                'fontSize',
                'fontStyle',
                'fontWeight',
                'fontStretch',
                'letterSpacing',
                'lineHeight',
                'textAlign',
                'textTransform',
                'textIndent',
                'tabSize',
                'direction',
                'wordBreak',
                'overflowWrap',
            ].forEach((property) => {
                mirror.style[property] = styles[property];
            });
    
            mirror.style.position = 'fixed';
            mirror.style.left = `${hostRect.left - element.scrollLeft}px`;
            mirror.style.top = `${hostRect.top - element.scrollTop}px`;
            mirror.style.width = `${hostRect.width}px`;
            mirror.style.minHeight = `${hostRect.height}px`;
            // Match both wrapped textareas and the raw Canvas code editor, whose
            // `white-space: pre` lines may extend beyond the visible viewport.
            mirror.style.whiteSpace = styles.whiteSpace || 'pre-wrap';
            mirror.style.visibility = 'hidden';
            mirror.style.pointerEvents = 'none';
            mirror.style.zIndex = '-1';
    
            mirror.appendChild(document.createTextNode(String(element.value || '').slice(0, start)));
            selectedSpan.textContent = selectedText || '\u200b';
            mirror.appendChild(selectedSpan);
            mirror.appendChild(document.createTextNode('\u200b'));
            document.body.appendChild(mirror);
    
            try {
                const visibleRects = Array.from(selectedSpan.getClientRects())
                    .filter((rect) => rect.width || rect.height)
                    .map((rect) => ({
                        left: Math.max(rect.left, hostRect.left),
                        right: Math.min(rect.right, hostRect.right),
                        top: Math.max(rect.top, hostRect.top),
                        bottom: Math.min(rect.bottom, hostRect.bottom),
                    }))
                    .filter((rect) => rect.right > rect.left && rect.bottom > rect.top);
    
                if (!visibleRects.length) return null;
    
                const left = Math.min(...visibleRects.map((rect) => rect.left));
                const right = Math.max(...visibleRects.map((rect) => rect.right));
                const top = Math.min(...visibleRects.map((rect) => rect.top));
                const bottom = Math.max(...visibleRects.map((rect) => rect.bottom));
                return {
                    left,
                    right,
                    top,
                    bottom,
                    width: right - left,
                    height: bottom - top,
                };
            } finally {
                mirror.remove();
            }
        }
    
        function positionReferenceToolbar(selection) {
            if (!hasAdjacentChatComposer()) {
                hideReferenceToolbar();
                return;
            }
            const toolbar = getReferenceToolbar();
            const rect = selection?.rect;
            if (!rect || (!rect.width && !rect.height)) {
                hideReferenceToolbar();
                return;
            }
    
            state.referenceToolbarController?.updateLabels();
            toolbar.classList.remove('is-below');
    
            const margin = 8;
            const toolbarRect = state.referenceToolbarController?.measure() || toolbar.getBoundingClientRect();
            const viewportWidth = document.documentElement.clientWidth || window.innerWidth || 0;
            const viewportHeight = document.documentElement.clientHeight || window.innerHeight || 0;
            const centeredLeft = rect.left + rect.width / 2 - toolbarRect.width / 2;
            const left = Math.max(margin, Math.min(centeredLeft, viewportWidth - toolbarRect.width - margin));
            const topAbove = rect.top - toolbarRect.height - 10;
            const shouldPlaceBelow = topAbove < margin && rect.bottom + toolbarRect.height + 10 < viewportHeight - margin;
            const top = shouldPlaceBelow
                ? Math.min(rect.bottom + 10, viewportHeight - toolbarRect.height - margin)
                : Math.max(margin, topAbove);
    
            toolbar.classList.toggle('is-below', shouldPlaceBelow);
            state.referenceToolbarController?.showAt(left, top);
        }
    
        function setReferenceToolbarState(selection) {
            state.activeReferenceSelection = selection && String(selection.text || '').trim() ? selection : null;
            // Spreadsheet selections belong to the grid editor itself. Text from
            // a formula input or a cell must never open Canvas' document-level
            // Copy / Add reference tooltip over the interactive table surface.
            if (SPREADSHEET_CONTENT_TYPES.has(state.activeReferenceSelection?.contentType)) {
                hideReferenceToolbar();
                return;
            }
            if (!hasAdjacentChatComposer()) {
                // Do not retain a Workspace selection that could reappear later if
                // another layout event makes the chat surface visible.
                hideReferenceToolbar();
                return;
            }
            if (!state.activeReferenceSelection || state.activeReferenceSelection.contentType === 'markdown') {
                hideReferenceToolbar({ clearSelection: !state.activeReferenceSelection });
                return;
            }
            positionReferenceToolbar(state.activeReferenceSelection);
        }
    
        function selectionRangeIsInsidePanel(range) {
            if (!range || !previewPanel) return false;
            return previewPanel.contains(range.commonAncestorContainer);
        }
    
        function getTextareaSelectionRect(element, start, end) {
            if (!element || typeof element.selectionStart !== 'number') return null;
            const rangeRect = getVisibleTextareaRangeRect(element, start, end);
            if (rangeRect) return rangeRect;
    
            const rect = element.getBoundingClientRect();
            const styles = window.getComputedStyle(element);
            const lineHeight = parseFloat(styles.lineHeight) || parseFloat(styles.fontSize) * 1.5 || 20;
            const paddingLeft = parseFloat(styles.paddingLeft) || 0;
            const paddingTop = parseFloat(styles.paddingTop) || 0;
            const beforeSelection = String(element.value || '').slice(0, element.selectionStart);
            const lines = beforeSelection.split('\n');
            const lineIndex = Math.max(lines.length - 1, 0);
            const columnIndex = lines[lines.length - 1]?.length || 0;
            const approxCharWidth = Math.max((parseFloat(styles.fontSize) || 13) * 0.62, 7);
            const x = rect.left + paddingLeft + columnIndex * approxCharWidth - element.scrollLeft;
            const y = rect.top + paddingTop + lineIndex * lineHeight - element.scrollTop;
            const clampedLeft = Math.max(rect.left + 8, Math.min(x, rect.right - 8));
            const clampedTop = Math.max(rect.top + 8, Math.min(y, rect.bottom - lineHeight));
            return {
                left: clampedLeft,
                right: clampedLeft + 1,
                top: clampedTop,
                bottom: clampedTop + lineHeight,
                width: 1,
                height: lineHeight,
            };
        }
    
        function getSelectionFromTextarea(element, draft, contentType) {
            if (!element || typeof element.selectionStart !== 'number' || typeof element.selectionEnd !== 'number') return null;
            if (element.selectionStart === element.selectionEnd) return null;
            const start = Math.min(element.selectionStart, element.selectionEnd);
            const end = Math.max(element.selectionStart, element.selectionEnd);
            const text = String(element.value || '').slice(start, end).trim();
            if (!text) return null;
            return {
                text,
                source: contentType === 'latex'
                    ? 'latex_source'
                    : (contentType === 'html' ? 'html_source' : 'source'),
                start,
                end,
                startLine: String(element.value || '').slice(0, start).split('\n').length,
                endLine: String(element.value || '').slice(0, end).split('\n').length,
                rect: getTextareaSelectionRect(element, start, end) || element.getBoundingClientRect(),
                element,
                fileId: draft?.fileId || '',
                pdfFileId: draft?.pdfFileId || '',
            };
        }
    
        /** Return the PDF page numbers touched by a native DOM selection. */
        function getPdfSelectionPages(range) {
            if (!range || !previewTrack) return [];
    
            // A selection can run across several page text layers. Checking each
            // layer instead of only the range endpoints also covers browser-made
            // ranges whose boundary node is the surrounding page container.
            return Array.from(previewTrack.querySelectorAll('.canvas-pdf-text-layer'))
                .filter((textLayer) => {
                    try {
                        return range.intersectsNode(textLayer);
                    } catch (_) {
                        return false;
                    }
                })
                .map((textLayer) => Number(textLayer.dataset.pageNumber || 0))
                .filter((pageNumber) => Number.isInteger(pageNumber) && pageNumber > 0)
                .sort((first, second) => first - second);
        }
    
        function getSelectionFromWindowSelection(selection, draft, contentType) {
            if (!selection || selection.isCollapsed || selection.rangeCount < 1) return null;
            const range = selection.getRangeAt(0);
            if (!selectionRangeIsInsidePanel(range)) return null;
            const text = String(range.toString() || selection.toString() || '').trim();
            if (!text) return null;
    
            const normalizedContentType = normalizeContentType(contentType);
            const isRenderedPdf = normalizedContentType === 'pdf' || normalizedContentType === 'latex';
            const pdfViewer = range.commonAncestorContainer?.nodeType === Node.ELEMENT_NODE
                ? range.commonAncestorContainer.closest?.('.canvas-pdf-document-viewer')
                : range.commonAncestorContainer?.parentElement?.closest?.('.canvas-pdf-document-viewer');
    
            // PDF and LaTeX preview selections must originate in the app-owned
            // selectable text layer. This keeps panel chrome and loading labels
            // from becoming accidental references while allowing native PDF text
            // selection to use the same Copy / Add reference tooltip as Canvas.
            if (isRenderedPdf && !pdfViewer) return null;
    
            const selectedPages = isRenderedPdf ? getPdfSelectionPages(range) : [];
            if (isRenderedPdf && !selectedPages.length) return null;
    
            const selectionData = {
                text,
                source: isRenderedPdf ? 'pdf_preview' : 'preview',
                rect: range.getBoundingClientRect(),
                fileId: draft?.fileId || '',
                pdfFileId: draft?.pdfFileId || '',
            };
            if (selectedPages.length) {
                selectionData.pageStart = selectedPages[0];
                selectionData.pageEnd = selectedPages[selectedPages.length - 1];
            }
            return selectionData;
        }
    
        function readCurrentArtifactSelection() {
            if (!state.previewVisible || !state.activeDraftKey) return null;
            const draft = draftMap.get(state.activeDraftKey);
            if (!draft) return null;
            const contentType = normalizeContentType(draft.contentType);
            const activeElement = document.activeElement;
    
            // The browser may move focus to the document or floating tooltip just
            // after a textarea selection. Fall back to the visible Code-tab editor
            // so the marked range is not lost before the action can consume it.
            const selectedSourceEditor = activeElement?.classList?.contains('canvas-raw-editor')
                ? activeElement
                : previewTrack?.querySelector('.canvas-html-preview-wrapper.code-view .canvas-raw-editor');
            const textareaSelection = selectedSourceEditor
                ? getSelectionFromTextarea(selectedSourceEditor, draft, contentType)
                : null;
            if (textareaSelection) return decorateArtifactSelection(textareaSelection, draft, contentType);
    
            const domSelection = getSelectionFromWindowSelection(window.getSelection?.(), draft, contentType);
            if (domSelection) return decorateArtifactSelection(domSelection, draft, contentType);
    
            return null;
        }
    
        function buildArtifactSelectionFromCallbackData(data) {
            if (!data || !String(data.text || '').trim() || !state.activeDraftKey) return null;
            const draft = draftMap.get(state.activeDraftKey);
            if (!draft) return null;
            const contentType = normalizeContentType(draft.contentType);
            return decorateArtifactSelection({
                text: String(data.text || '').trim(),
                source: String(data.source || (contentType === 'markdown' ? 'editor' : 'preview')),
                start: typeof data.start === 'number' ? data.start : undefined,
                end: typeof data.end === 'number' ? data.end : undefined,
                startLine: typeof data.startLine === 'number' ? data.startLine : undefined,
                endLine: typeof data.endLine === 'number' ? data.endLine : undefined,
                pageStart: typeof data.pageStart === 'number' ? data.pageStart : undefined,
                pageEnd: typeof data.pageEnd === 'number' ? data.pageEnd : undefined,
                rect: data.rect || data.range?.getBoundingClientRect?.() || null,
                fileId: draft?.fileId || '',
                pdfFileId: draft?.pdfFileId || '',
            }, draft, contentType);
        }
    
        function decorateArtifactSelection(selection, draft, contentType) {
            const type = normalizeContentType(contentType);
            const fileName = resolveDisplayCanvasFileName(draft?.fileName, type);
            return {
                ...selection,
                draftKey: draft?.key || state.activeDraftKey || '',
                toolName: 'canvas',
                contentType: type,
                fileName,
                title: String(draft?.title || fileName || ''),
                fileId: String(selection.fileId || draft?.fileId || ''),
                sourceFileId: String(draft?.sourceFileId || draft?.fileId || ''),
                pdfFileId: String(selection.pdfFileId || draft?.pdfFileId || ''),
                pdfFileName: String(draft?.pdfFileName || ''),
            };
        }
    
        function refreshReferenceSelectionState() {
            setReferenceToolbarState(readCurrentArtifactSelection());
        }
    
        function normalizeReferenceSnippet(text) {
            return String(text || '').replace(/\r\n/g, '\n').trim();
        }
    
        function buildArtifactReferenceText(selection) {
            const selectedText = normalizeReferenceSnippet(selection?.text);
            const contentType = normalizeContentType(selection?.contentType);
            const typeLabel = getTypeLabel(contentType, 'canvas');
            const isPdf = contentType === 'pdf';
            const lines = [
                contentType === 'latex'
                    ? '[LaTeX PDF artifact reference]'
                    : (isPdf ? '[PDF file reference]' : '[Canvas artifact reference]'),
                isPdf
                    ? 'Reference type: source PDF file'
                    : 'Tool to edit: canvas',
                `Artifact: ${selection?.title || selection?.fileName || typeLabel}`,
                `Content type: ${contentType}`,
            ];
    
            if (contentType === 'latex') {
                if (selection?.sourceFileId) lines.push(`file_id: ${selection.sourceFileId}`);
                if (selection?.fileName) lines.push(`Source filename: ${selection.fileName}`);
                if (selection?.pdfFileName) lines.push(`PDF filename: ${selection.pdfFileName}`);
                lines.push("Edit guidance: use the canvas tool with type='latex' and file_id. Read the current source with type='view' first when the marked text came from the PDF preview; for a source selection, use exact start_snippet and end_snippet anchors when possible.");
            } else if (isPdf) {
                if (selection?.fileId) lines.push(`file_id: ${selection.fileId}`);
                if (selection?.fileName) lines.push(`Filename: ${selection.fileName}`);
                lines.push('Use file_id to inspect the original PDF together with the marked text.');
            } else {
                if (selection?.fileId) lines.push(`file_id: ${selection.fileId}`);
                if (selection?.fileName) lines.push(`Filename: ${selection.fileName}`);
                lines.push('Edit guidance: use the canvas tool with file_id. For a local change, use the marked text as the exact start_snippet and end_snippet when possible.');
            }
    
            if (typeof selection?.start === 'number' && typeof selection?.end === 'number') {
                lines.push(`Source offsets: ${selection.start}-${selection.end}`);
            }
            if (typeof selection?.startLine === 'number' && typeof selection?.endLine === 'number') {
                lines.push(`Source lines: ${selection.startLine}-${selection.endLine}`);
            }
            if (typeof selection?.pageStart === 'number') {
                const pageEnd = typeof selection?.pageEnd === 'number' ? selection.pageEnd : selection.pageStart;
                lines.push(`PDF pages: ${selection.pageStart}${pageEnd !== selection.pageStart ? `-${pageEnd}` : ''}`);
            }
            lines.push(`Marked from: ${selection?.source || 'artifact'}`);
            lines.push('Marked text:');
            lines.push('```');
            lines.push(selectedText);
            lines.push('```');
            return lines.join('\n');
        }
    
        function insertTextIntoChatComposer(text, { onlyWhenEmpty = false } = {}) {
            const input = document.getElementById('chatBoxInput');
            if (!input) return false;
            const currentValue = String(input.value || '');
            if (onlyWhenEmpty && currentValue.trim()) {
                input.focus();
                return true;
            }
            const insertText = String(text || '');
            if (!insertText) return false;
            if (onlyWhenEmpty) {
                input.value = insertText;
                input.setSelectionRange(insertText.length, insertText.length);
            } else {
                const start = typeof input.selectionStart === 'number' ? input.selectionStart : currentValue.length;
                const end = typeof input.selectionEnd === 'number' ? input.selectionEnd : start;
                const prefix = start > 0 && currentValue[start - 1] && !/\s/.test(currentValue[start - 1]) ? ' ' : '';
                const nextText = prefix + insertText;
                input.value = `${currentValue.slice(0, start)}${nextText}${currentValue.slice(end)}`;
                const cursor = start + nextText.length;
                input.setSelectionRange(cursor, cursor);
            }
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.focus();
            return true;
        }
    
        function notifyReferenceAdded() {
            const message = t('canvas_reference_added', 'Marked selection added as a reference');
            if (typeof window.notifySuccess === 'function') {
                window.notifySuccess(message);
            } else if (typeof showNotification === 'function') {
                showNotification(message, 'success');
            }
        }
    
        function notifyReferenceError(message) {
            const fallback = t('canvas_reference_select_text_first', 'Select text in the artifact first');
            if (typeof window.notifyWarning === 'function') {
                window.notifyWarning(message || fallback);
            } else if (typeof showNotification === 'function') {
                showNotification(message || fallback, 'warning');
            }
        }
    
        function addMarkedSelectionAsReference(selectionData = null) {
            // The Workspace file viewer shares this code with chat-owned Canvas
            // previews. Never write into the hidden global composer if the chat
            // surface disappeared after the selection toolbar was opened.
            if (!hasAdjacentChatComposer()) {
                hideReferenceToolbar();
                return false;
            }
    
            const selection = buildArtifactSelectionFromCallbackData(selectionData) || state.activeReferenceSelection || readCurrentArtifactSelection();
            if (!selection || !String(selection.text || '').trim()) {
                notifyReferenceError();
                refreshReferenceSelectionState();
                return false;
            }
    
            const referenceText = buildArtifactReferenceText(selection);
            if (typeof window.addReferencePart === 'function') {
                window.addReferencePart(referenceText);
                notifyReferenceAdded();
                return true;
            }
    
            if (!insertTextIntoChatComposer(referenceText)) {
                return false;
            }
            notifyReferenceAdded();
            return true;
        }
    

        return Object.freeze({ hasAdjacentChatComposer, hideReferenceToolbar, setReferenceToolbarState, refreshReferenceSelectionState, addMarkedSelectionAsReference });
    }

    modules.referenceSelection = Object.freeze({ create: createReferenceSelectionModule });
})(globalThis);
