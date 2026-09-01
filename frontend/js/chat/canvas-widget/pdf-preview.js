(function (root) {
    'use strict';

    const modules = root.__omlorixCanvasWidgetModules ||= {};

    function createPdfPreviewModule({ t, formatT }) {
        let abortController = null;
        let intersectionObserver = null;
        let resizeObserver = null;
        const pageObjectUrls = new Set();

        function releasePdfPageObjectUrl(objectUrl) {
            if (!objectUrl || !pageObjectUrls.delete(objectUrl)) return;
            URL.revokeObjectURL(objectUrl);
        }

        function releasePdfPageObjectUrls() {
            pageObjectUrls.forEach((objectUrl) => URL.revokeObjectURL(objectUrl));
            pageObjectUrls.clear();
        }

        function resetSelectablePdfPreviewRendering() {
            abortController?.abort();
            abortController = null;
            intersectionObserver?.disconnect();
            intersectionObserver = null;
            resizeObserver?.disconnect();
            resizeObserver = null;
            releasePdfPageObjectUrls();
        }

        function buildPdfPreviewEndpoint(path, fileId, pageNumber = null) {
            const params = new URLSearchParams({ file_id: String(fileId || '') });
            if (pageNumber !== null) params.set('page', String(pageNumber));
            return `/api/v1/files/pdf/preview${path}?${params.toString()}`;
        }

        function resizeSelectablePdfPage(pageShell) {
            const naturalWidth = Number(pageShell?._pdfNaturalWidth || 0);
            const naturalHeight = Number(pageShell?._pdfNaturalHeight || 0);
            const surface = pageShell?.querySelector('.canvas-pdf-page-surface');
            if (!pageShell || !surface || naturalWidth <= 0 || naturalHeight <= 0) return;
            const renderedWidth = Math.min(pageShell.clientWidth || naturalWidth, naturalWidth);
            const scale = renderedWidth / naturalWidth;
            pageShell.style.height = `${naturalHeight * scale}px`;
            surface.style.transform = `scale(${scale})`;
        }

        function renderSelectablePdfTextLayer(surface, pageData) {
            const words = Array.isArray(pageData?.words) ? pageData.words : [];
            const textLayer = document.createElement('div');
            textLayer.className = 'canvas-pdf-text-layer';
            textLayer.setAttribute('data-page-number', String(pageData?.page || ''));
            words.forEach((word, index) => {
                const span = document.createElement('span');
                const nextWord = words[index + 1];
                const endsLine = nextWord && (
                    Number(nextWord.block) !== Number(word.block)
                    || Number(nextWord.line) !== Number(word.line)
                );
                span.className = 'canvas-pdf-text-word';
                span.textContent = `${String(word.text || '')}${endsLine ? '\n' : ' '}`;
                span.style.left = `${Number(word.x) || 0}px`;
                span.style.top = `${Number(word.y) || 0}px`;
                span.style.fontSize = `${Math.max(Number(word.height) || 0, 1)}px`;
                span.dataset.targetWidth = String(Math.max(Number(word.width) || 0, 1));
                textLayer.appendChild(span);
            });
            surface.appendChild(textLayer);
            textLayer.querySelectorAll('.canvas-pdf-text-word').forEach((span) => {
                const naturalWidth = span.offsetWidth;
                const targetWidth = Number(span.dataset.targetWidth || 0);
                if (naturalWidth > 0 && targetWidth > 0) span.style.transform = `scaleX(${targetWidth / naturalWidth})`;
            });
        }

        function showPdfPreviewError(container) {
            if (!container) return;
            const error = document.createElement('div');
            error.className = 'canvas-pdf-document-status';
            error.setAttribute('role', 'alert');
            error.textContent = t('files_preview_load_error', 'Failed to load preview');
            container.replaceChildren(error);
            container.setAttribute('aria-busy', 'false');
        }

        async function fetchSelectablePdfPageImage(fileId, pageNumber, signal) {
            const response = await window.authedFetch(buildPdfPreviewEndpoint('/page-image', fileId, pageNumber), {
                method: 'GET', credentials: 'include', headers: { accept: 'image/png' }, signal,
            });
            if (!response.ok) throw new Error(`PDF page image failed: ${response.status}`);
            const contentType = String(response.headers?.get?.('content-type') || '').toLowerCase();
            if (contentType && !contentType.startsWith('image/png')) {
                throw new Error(`PDF page image returned an unsupported content type: ${contentType}`);
            }
            const blob = await response.blob();
            if (signal.aborted) return '';
            if (!blob || Number(blob.size) <= 0) throw new Error('PDF page image was empty');
            const objectUrl = URL.createObjectURL(blob);
            pageObjectUrls.add(objectUrl);
            return objectUrl;
        }

        async function decodeSelectablePdfPageImage(image, objectUrl, signal) {
            if (signal.aborted) throw new Error('PDF page image load was aborted');
            if (typeof image.decode === 'function') {
                image.src = objectUrl;
                await image.decode();
                return;
            }
            await new Promise((resolve, reject) => {
                const cleanup = () => {
                    image.removeEventListener('load', onLoad);
                    image.removeEventListener('error', onError);
                    signal.removeEventListener('abort', onAbort);
                };
                const settle = (callback) => {
                    cleanup();
                    callback();
                };
                const onLoad = () => settle(resolve);
                const onError = () => settle(() => reject(new Error('PDF page image could not be decoded')));
                const onAbort = () => settle(() => reject(new Error('PDF page image load was aborted')));
                image.addEventListener('load', onLoad, { once: true });
                image.addEventListener('error', onError, { once: true });
                signal.addEventListener('abort', onAbort, { once: true });
                if (signal.aborted) {
                    onAbort();
                    return;
                }
                image.src = objectUrl;
            });
        }

        async function loadSelectablePdfPage(pageShell, fileId, signal) {
            if (!pageShell || pageShell.dataset.loadState) return;
            pageShell.dataset.loadState = 'loading';
            const pageNumber = Number(pageShell.dataset.pageNumber || 0);
            const surface = pageShell.querySelector('.canvas-pdf-page-surface');
            const pageStatus = pageShell.querySelector('.canvas-pdf-page-status');
            const image = document.createElement('img');
            image.className = 'canvas-pdf-page-image';
            image.alt = '';
            image.draggable = false;
            image.decoding = 'async';
            surface?.appendChild(image);
            let imageObjectUrl = '';
            try {
                const [pageResult, imageResult] = await Promise.allSettled([
                    window.authedFetch(buildPdfPreviewEndpoint('/page', fileId, pageNumber), {
                        method: 'GET', credentials: 'include', headers: { accept: 'application/json' }, signal,
                    }).then(async (response) => {
                        if (!response.ok) throw new Error(`PDF page preview failed: ${response.status}`);
                        return response.json();
                    }),
                    fetchSelectablePdfPageImage(fileId, pageNumber, signal),
                ]);
                if (imageResult.status === 'fulfilled') imageObjectUrl = imageResult.value;
                if (pageResult.status === 'rejected') throw pageResult.reason;
                if (imageResult.status === 'rejected') throw imageResult.reason;
                const pageData = pageResult.value;
                if (signal.aborted || !pageShell.isConnected) {
                    releasePdfPageObjectUrl(imageObjectUrl);
                    return;
                }
                await decodeSelectablePdfPageImage(image, imageObjectUrl, signal);
                if (signal.aborted || !pageShell.isConnected) {
                    releasePdfPageObjectUrl(imageObjectUrl);
                    return;
                }
                renderSelectablePdfTextLayer(surface, pageData);
                pageShell.dataset.loadState = 'loaded';
                pageShell.setAttribute('aria-busy', 'false');
                pageStatus?.remove();
            } catch (error) {
                if (signal.aborted) return;
                releasePdfPageObjectUrl(imageObjectUrl);
                image.remove();
                pageShell.dataset.loadState = 'error';
                pageShell.setAttribute('aria-busy', 'false');
                if (pageStatus) {
                    pageStatus.setAttribute('role', 'alert');
                    pageStatus.textContent = t('files_preview_load_error', 'Failed to load preview');
                }
                console.error(error);
            }
        }

        async function renderSelectablePdfPreviewInto(viewer, pdfFileId) {
            if (!viewer) return;
            resetSelectablePdfPreviewRendering();
            viewer.replaceChildren();
            const normalizedPdfId = String(pdfFileId || '').trim();
            if (!normalizedPdfId || typeof window.authedFetch !== 'function') {
                showPdfPreviewError(viewer);
                return;
            }
            const loading = document.createElement('div');
            loading.className = 'canvas-pdf-document-status';
            loading.textContent = t('files_preview_loading', 'Loading preview...');
            viewer.appendChild(loading);
            viewer.setAttribute('aria-busy', 'true');
            const controller = new AbortController();
            abortController = controller;
            try {
                const response = await window.authedFetch(buildPdfPreviewEndpoint('', normalizedPdfId), {
                    method: 'GET', credentials: 'include', headers: { accept: 'application/json' }, signal: controller.signal,
                });
                if (!response.ok) throw new Error(`PDF preview failed: ${response.status}`);
                const documentData = await response.json();
                if (controller.signal.aborted || !viewer.isConnected) return;
                viewer.replaceChildren();
                viewer.setAttribute('aria-busy', 'false');
                const pages = (Array.isArray(documentData?.pages) ? documentData.pages : []).filter((page) => {
                    const pageNumber = Number(page?.page || 0);
                    const naturalWidth = Number(page?.width || 0);
                    const naturalHeight = Number(page?.height || 0);
                    return Number.isInteger(pageNumber)
                        && pageNumber > 0
                        && Number.isFinite(naturalWidth)
                        && naturalWidth > 0
                        && Number.isFinite(naturalHeight)
                        && naturalHeight > 0;
                });
                if (!pages.length) {
                    showPdfPreviewError(viewer);
                    return;
                }
                resizeObserver = typeof ResizeObserver === 'function'
                    ? new ResizeObserver((entries) => entries.forEach((entry) => resizeSelectablePdfPage(entry.target)))
                    : null;
                const loadPage = (pageShell) => void loadSelectablePdfPage(pageShell, normalizedPdfId, controller.signal);
                if (typeof IntersectionObserver === 'function') {
                    intersectionObserver = new IntersectionObserver((entries) => {
                        entries.forEach((entry) => {
                            if (!entry.isIntersecting) return;
                            intersectionObserver?.unobserve(entry.target);
                            loadPage(entry.target);
                        });
                    }, { root: viewer, rootMargin: '800px 0px' });
                }
                pages.forEach((page) => {
                    const pageNumber = Number(page.page || 0);
                    const naturalWidth = Number(page.width || 0);
                    const naturalHeight = Number(page.height || 0);
                    const pageShell = document.createElement('section');
                    pageShell.className = 'canvas-pdf-page-shell';
                    pageShell.dataset.pageNumber = String(pageNumber);
                    pageShell._pdfNaturalWidth = naturalWidth;
                    pageShell._pdfNaturalHeight = naturalHeight;
                    pageShell.style.width = `${naturalWidth}px`;
                    pageShell.style.height = `${naturalHeight}px`;
                    pageShell.setAttribute('role', 'group');
                    pageShell.setAttribute('aria-label', formatT('pdf_export_page', 'Page {page}', { page: pageNumber }));
                    pageShell.setAttribute('aria-busy', 'true');
                    const surface = document.createElement('div');
                    surface.className = 'canvas-pdf-page-surface';
                    surface.style.width = `${naturalWidth}px`;
                    surface.style.height = `${naturalHeight}px`;
                    const pageStatus = document.createElement('span');
                    pageStatus.className = 'canvas-pdf-page-status';
                    pageStatus.textContent = t('files_preview_loading', 'Loading preview...');
                    pageShell.append(surface, pageStatus);
                    viewer.appendChild(pageShell);
                    resizeSelectablePdfPage(pageShell);
                    resizeObserver?.observe(pageShell);
                    if (intersectionObserver) intersectionObserver.observe(pageShell);
                    else loadPage(pageShell);
                });
            } catch (error) {
                if (controller.signal.aborted) return;
                showPdfPreviewError(viewer);
                console.error(error);
            }
        }

        return Object.freeze({ resetSelectablePdfPreviewRendering, renderSelectablePdfPreviewInto });
    }

    modules.pdfPreview = Object.freeze({ create: createPdfPreviewModule });
})(globalThis);
