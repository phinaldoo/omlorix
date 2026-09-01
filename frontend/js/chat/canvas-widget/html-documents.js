(function (root) {
    'use strict';

    const modules = root.__omlorixCanvasWidgetModules ||= {};

    function createHtmlDocumentsModule({ buildCanvasAssetUrl, getActiveDraft, srcdocUrl }) {
        const HTML_PREVIEW_SRCDOC_URL = srcdocUrl;
        const HTML_PREVIEW_CSP = "default-src 'none'; img-src 'self' data: blob:; media-src 'self' data: blob:; frame-src 'self'; child-src 'none'; object-src 'none'; style-src 'unsafe-inline'; font-src 'self' data:; script-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'";
        const HTML_PREVIEW_CSP_META = `<meta http-equiv="Content-Security-Policy" content="${HTML_PREVIEW_CSP}">`;
        const OMLORIX_FILE_URL_PATTERN = /omlorix-file:\/\/([a-zA-Z0-9][a-zA-Z0-9._-]{0,127})([?#][^\s"'<>)]*)?/g;
        const HTML_FILE_REFERENCE_ATTRS = ['href', 'src', 'poster', 'data', 'xlink:href'];
    
        function getOmlorixFileIdFromUrl(value) {
            const raw = String(value || '').trim();
            if (!raw) return '';
    
            if (raw.toLowerCase().startsWith('omlorix-file://')) {
                const withoutScheme = raw.slice('omlorix-file://'.length).split(/[?#]/, 1)[0];
                let decodedId = withoutScheme || '';
                try {
                    decodedId = decodeURIComponent(decodedId).trim();
                } catch (_) {
                    decodedId = decodedId.trim();
                }
                return /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(decodedId) ? decodedId : '';
            }
    
            try {
                const parsed = new URL(raw, window.location.origin);
                if (parsed.pathname === '/api/v1/files/download') {
                    const fileId = String(parsed.searchParams.get('file_id') || '').trim();
                    return /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(fileId) ? fileId : '';
                }
            } catch (_) {}
    
            return '';
        }
    
        function replaceOmlorixFileUrls(value, canvasFileId = '') {
            return String(value || '').replace(
                OMLORIX_FILE_URL_PATTERN,
                (_match, fileId) => buildCanvasAssetUrl(canvasFileId, fileId),
            );
        }
    
        function rewriteHtmlFileReferenceAttr(node, attrName, canvasFileId = '') {
            if (!node.hasAttribute(attrName)) return;
            const value = node.getAttribute(attrName) || '';
            const fileId = getOmlorixFileIdFromUrl(value);
            if (fileId) {
                node.setAttribute(attrName, buildCanvasAssetUrl(canvasFileId, fileId));
                if (node.tagName && node.tagName.toLowerCase() === 'a') {
                    node.setAttribute('rel', 'noopener noreferrer nofollow');
                }
                return;
            }
            if (/^\s*omlorix-file:/i.test(value)) {
                node.removeAttribute(attrName);
            }
        }
    
        function rewriteHtmlSrcset(node, canvasFileId = '') {
            if (!node.hasAttribute('srcset')) return;
            const srcset = node.getAttribute('srcset') || '';
            const rewritten = srcset
                .split(',')
                .map((part) => {
                    const trimmed = part.trim();
                    if (!trimmed) return '';
                    const [url, ...descriptorParts] = trimmed.split(/\s+/);
                    const fileId = getOmlorixFileIdFromUrl(url);
                    if (!fileId && /^\s*omlorix-file:/i.test(url)) return '';
                    const nextUrl = fileId
                        ? buildCanvasAssetUrl(canvasFileId, fileId)
                        : replaceOmlorixFileUrls(url, canvasFileId);
                    return [nextUrl, ...descriptorParts].filter(Boolean).join(' ');
                })
                .filter(Boolean)
                .join(', ');
            if (rewritten) node.setAttribute('srcset', rewritten);
            else node.removeAttribute('srcset');
        }
    
        function sanitizeHtmlPreviewDocument(doc) {
            const canvasFileId = String(arguments[1] || '');
            doc.querySelectorAll('meta[http-equiv]').forEach((meta) => {
                const httpEquiv = String(meta.getAttribute('http-equiv') || '').trim().toLowerCase();
                if (httpEquiv === 'content-security-policy' || httpEquiv === 'refresh') {
                    meta.remove();
                }
            });
            doc.querySelectorAll('base').forEach((base) => base.remove());
            doc.querySelectorAll('script').forEach((script) => script.remove());
            doc.querySelectorAll('*').forEach((node) => {
                Array.from(node.attributes || []).forEach((attr) => {
                    const name = String(attr.name || '');
                    if (name.toLowerCase().startsWith('on') || name.toLowerCase() === 'srcdoc') {
                        node.removeAttribute(name);
                    }
                });
                HTML_FILE_REFERENCE_ATTRS.forEach((attrName) => rewriteHtmlFileReferenceAttr(node, attrName, canvasFileId));
                rewriteHtmlSrcset(node, canvasFileId);
                if (node.hasAttribute('style')) {
                    node.setAttribute('style', replaceOmlorixFileUrls(node.getAttribute('style') || '', canvasFileId));
                }
            });
            doc.querySelectorAll('style').forEach((style) => {
                style.textContent = replaceOmlorixFileUrls(style.textContent || '', canvasFileId);
            });
        }
    
        function serializeHtmlPreviewDocument(doc) {
            const doctype = doc.doctype
                ? `<!DOCTYPE ${doc.doctype.name}${doc.doctype.publicId ? ` PUBLIC "${doc.doctype.publicId}"` : ''}${doc.doctype.systemId ? ` "${doc.doctype.systemId}"` : ''}>`
                : '<!DOCTYPE html>';
            return `${doctype}\n${doc.documentElement.outerHTML}`;
        }
    
        /** Unwrap generated whole-document code containers before preview or print. */
        function normalizeCanvasHtmlSource(htmlContent) {
            let normalized = String(htmlContent || '').trim();
            const fenced = normalized.match(/^```(?:html?)?\s*\r?\n([\s\S]*?)\r?\n```\s*$/i);
            if (fenced) normalized = fenced[1].trim();
    
            const documentMarker = /<(?:!doctype\s+html|html|head|body)(?:\s|>)/i;
            const escapedDocumentMarker = /&lt;(?:!doctype\s+html|html|head|body)(?:\s|&gt;)/i;
            const wrappedCode = normalized.match(/^<pre\b[^>]*>\s*<code\b[^>]*>([\s\S]*?)<\/code>\s*<\/pre>$/i);
            if (wrappedCode && typeof document !== 'undefined') {
                const textarea = document.createElement('textarea');
                textarea.innerHTML = wrappedCode[1];
                if (documentMarker.test(textarea.value)) normalized = textarea.value.trim();
            }
    
            for (let pass = 0; pass < 2 && !documentMarker.test(normalized); pass += 1) {
                if (typeof document === 'undefined') break;
                const textarea = document.createElement('textarea');
                textarea.innerHTML = normalized;
                const decoded = textarea.value;
                if (
                    decoded === normalized
                    || (!documentMarker.test(decoded) && !escapedDocumentMarker.test(decoded))
                ) break;
                normalized = decoded.trim();
            }
            return normalized;
        }
    
        /**
         * Preserve authored behavior while removing document-level directives that
         * could compete with the trusted preview host.
         *
         * Active code is safe here because it is sent to the nested opaque-origin
         * frame, never inserted into the authenticated Omlorix document.  Known
         * ``omlorix-file://`` references are still rewritten to their authenticated
         * download URLs so existing Canvas file embedding continues to work.
         */
        function prepareInteractiveHtmlPreviewSource(htmlContent, providedCanvasFileId = '') {
            const { draft: activeDraft, key: activeDraftKey } = getActiveDraft();
            const canvasFileId = String(
                providedCanvasFileId
                || activeDraft?.fileId
                || activeDraftKey
                || ''
            );
            const html = normalizeCanvasHtmlSource(htmlContent);
            if (typeof DOMParser === 'undefined') return replaceOmlorixFileUrls(html, canvasFileId);
    
            const parser = new DOMParser();
            const doc = parser.parseFromString(html || '<!doctype html><html><head></head><body></body></html>', 'text/html');
            doc.querySelectorAll('meta[http-equiv]').forEach((meta) => {
                const directive = String(meta.getAttribute('http-equiv') || '').trim().toLowerCase();
                if (directive === 'content-security-policy' || directive === 'refresh') meta.remove();
            });
            doc.querySelectorAll('base').forEach((base) => base.remove());
            doc.querySelectorAll('*').forEach((node) => {
                HTML_FILE_REFERENCE_ATTRS.forEach((attrName) => rewriteHtmlFileReferenceAttr(node, attrName, canvasFileId));
                rewriteHtmlSrcset(node, canvasFileId);
                if (node.hasAttribute('style')) {
                    node.setAttribute('style', replaceOmlorixFileUrls(node.getAttribute('style') || '', canvasFileId));
                }
            });
            doc.querySelectorAll('style, script').forEach((node) => {
                node.textContent = replaceOmlorixFileUrls(node.textContent || '', canvasFileId);
            });
            return serializeHtmlPreviewDocument(doc);
        }
    
        function rewriteCanvasHtmlPreviewHtml(
            htmlContent,
            { forSrcdoc = false, canvasFileId: providedCanvasFileId = '' } = {},
        ) {
            const { draft: activeDraft, key: activeDraftKey } = getActiveDraft();
            const canvasFileId = String(
                providedCanvasFileId
                || activeDraft?.fileId
                || activeDraftKey
                || ''
            );
            const html = normalizeCanvasHtmlSource(htmlContent);
            if (typeof DOMParser === 'undefined') {
                const rewritten = replaceOmlorixFileUrls(html, canvasFileId).replace(/<script\b[\s\S]*?<\/script>/gi, '');
                return rewritten || '';
            }
    
            const parser = new DOMParser();
            const doc = parser.parseFromString(html || '<!doctype html><html><head></head><body></body></html>', 'text/html');
            sanitizeHtmlPreviewDocument(doc, canvasFileId);
            if (forSrcdoc) {
                // Safari may activate links through accessibility without dispatching
                // a cancellable click to the iframe document. Give bare fragments a
                // native srcdoc URL as well, so that path can never resolve to Omlorix.
                doc.querySelectorAll('a[href]').forEach((anchor) => {
                    const href = String(anchor.getAttribute('href') || '').trim();
                    if (href.startsWith('#')) {
                        anchor.setAttribute('href', `${HTML_PREVIEW_SRCDOC_URL}${href}`);
                    }
                });
            }
            const head = doc.head || doc.documentElement.insertBefore(doc.createElement('head'), doc.body || null);
            head.insertAdjacentHTML('afterbegin', HTML_PREVIEW_CSP_META);
            return serializeHtmlPreviewDocument(doc);
        }
    
        function withIframeSecurityGuard(htmlContent, { canvasFileId = '' } = {}) {
            return rewriteCanvasHtmlPreviewHtml(htmlContent, { forSrcdoc: true, canvasFileId });
        }
    
        const HTML_CANVAS_IMAGE_MAX_DIMENSION = 16384;
        const HTML_CANVAS_IMAGE_MAX_PIXELS = 40_000_000;
        const HTML_CANVAS_IMAGE_MAX_SCALE = 2;
        const canvasWidgetScriptUrl = document.currentScript?.src || '';
        const HTML_CANVAS_RENDERER_URL = canvasWidgetScriptUrl
            ? new URL('../vendor/html2canvas.min.js', canvasWidgetScriptUrl).href
            : '/js/vendor/html2canvas.min.js';
        const htmlCanvasRendererPromises = new WeakMap();
    
        /** Wait briefly for fonts and images without hanging on failed resources. */
        async function waitForHtmlCanvasResources(doc) {
            const resourcePromises = [];
            if (doc?.fonts?.ready) {
                resourcePromises.push(Promise.resolve(doc.fonts.ready).catch(() => undefined));
            }
            Array.from(doc?.images || []).forEach((image) => {
                if (image.complete) return;
                resourcePromises.push(new Promise((resolve) => {
                    image.addEventListener('load', resolve, { once: true });
                    image.addEventListener('error', resolve, { once: true });
                }));
            });
            if (!resourcePromises.length) return;
    
            await Promise.race([
                Promise.all(resourcePromises),
                new Promise((resolve) => window.setTimeout(resolve, 5000)),
            ]);
        }
    
        /** Load the self-hosted DOM renderer only when PNG export is requested. */
        function ensureHtmlCanvasRenderer(targetWindow = window) {
            if (typeof targetWindow.html2canvas === 'function') return Promise.resolve(targetWindow.html2canvas);
            const existingPromise = htmlCanvasRendererPromises.get(targetWindow);
            if (existingPromise) return existingPromise;
    
            const rendererPromise = new Promise((resolve, reject) => {
                const script = targetWindow.document.createElement('script');
                script.src = HTML_CANVAS_RENDERER_URL;
                script.async = true;
                script.addEventListener('load', () => {
                    if (typeof targetWindow.html2canvas === 'function') resolve(targetWindow.html2canvas);
                    else reject(new Error('HTML image renderer is unavailable.'));
                }, { once: true });
                script.addEventListener('error', () => reject(new Error('HTML image renderer failed to load.')), { once: true });
                targetWindow.document.head.appendChild(script);
            }).catch((error) => {
                htmlCanvasRendererPromises.delete(targetWindow);
                throw error;
            });
            htmlCanvasRendererPromises.set(targetWindow, rendererPromise);
            return rendererPromise;
        }
    
        /** Allow only Omlorix's self-hosted image renderer in the export iframe. */
        function buildHtmlCanvasImageDocument(htmlContent) {
            const guardedHtml = withIframeSecurityGuard(htmlContent);
            if (typeof DOMParser === 'undefined') {
                return guardedHtml.replace("script-src 'none'", "script-src 'self'");
            }
    
            const parser = new DOMParser();
            const doc = parser.parseFromString(guardedHtml, 'text/html');
            const cspMeta = doc.querySelector('meta[http-equiv="Content-Security-Policy"]');
            if (cspMeta) {
                cspMeta.setAttribute(
                    'content',
                    String(cspMeta.getAttribute('content') || '').replace("script-src 'none'", "script-src 'self'")
                );
            }
            return serializeHtmlPreviewDocument(doc);
        }
    
        /** Return the complete rendered page size, including content below the fold. */
        function getHtmlCanvasDocumentSize(doc) {
            const root = doc.documentElement;
            const body = doc.body;
            return {
                width: Math.max(root?.scrollWidth || 0, root?.offsetWidth || 0, body?.scrollWidth || 0, body?.offsetWidth || 0, 1),
                height: Math.max(root?.scrollHeight || 0, root?.offsetHeight || 0, body?.scrollHeight || 0, body?.offsetHeight || 0, 1),
            };
        }
    
        /** Use the authored page background, falling back to normal browser white. */
        function getHtmlCanvasBackgroundColor(doc) {
            const transparentColors = new Set(['', 'transparent', 'rgba(0, 0, 0, 0)']);
            const bodyColor = doc.body ? doc.defaultView?.getComputedStyle(doc.body).backgroundColor : '';
            if (!transparentColors.has(String(bodyColor || '').toLowerCase())) return bodyColor;
            const rootColor = doc.documentElement
                ? doc.defaultView?.getComputedStyle(doc.documentElement).backgroundColor
                : '';
            return transparentColors.has(String(rootColor || '').toLowerCase()) ? '#ffffff' : rootColor;
        }
    
        /** Render the safe HTML Canvas document into one downloadable PNG Blob. */
        async function renderHtmlCanvasPngBlob(htmlContent) {
            const exportFrame = document.createElement('iframe');
            exportFrame.className = 'canvas-html-export-frame';
            const visiblePreviewWidth = previewTrack
                ?.querySelector('.canvas-html-preview-iframe')
                ?.getBoundingClientRect().width;
            exportFrame.style.width = `${Math.max(320, Math.round(visiblePreviewWidth || 1440))}px`;
            // Scripts are enabled only in this sanitized export document so the
            // self-hosted renderer can run. Authored scripts and event handlers
            // were removed, and the export CSP permits scripts from self only.
            exportFrame.setAttribute('sandbox', 'allow-same-origin allow-scripts');
            exportFrame.setAttribute('aria-hidden', 'true');
            exportFrame.setAttribute('title', t('canvas_html_preview_title', 'HTML Preview'));
    
            const loaded = new Promise((resolve, reject) => {
                const timeout = window.setTimeout(() => reject(new Error('HTML image document timed out.')), 8000);
                exportFrame.addEventListener('load', () => {
                    window.clearTimeout(timeout);
                    resolve();
                }, { once: true });
                exportFrame.addEventListener('error', () => {
                    window.clearTimeout(timeout);
                    reject(new Error('HTML image document failed to load.'));
                }, { once: true });
            });
    
            exportFrame.srcdoc = buildHtmlCanvasImageDocument(htmlContent);
            // Assign srcdoc before insertion so the load promise cannot resolve
            // against the iframe's transient initial about:blank document.
            document.body.appendChild(exportFrame);
    
            try {
                await loaded;
                const exportDocument = exportFrame.contentDocument;
                const exportWindow = exportFrame.contentWindow;
                if (!exportDocument || !exportWindow) throw new Error('HTML image document is unavailable.');
                await waitForHtmlCanvasResources(exportDocument);
                const html2canvas = await ensureHtmlCanvasRenderer(exportWindow);
    
                const { width, height } = getHtmlCanvasDocumentSize(exportDocument);
                const backgroundColor = getHtmlCanvasBackgroundColor(exportDocument);
                const scale = Math.min(
                    window.devicePixelRatio || 1,
                    HTML_CANVAS_IMAGE_MAX_SCALE,
                    HTML_CANVAS_IMAGE_MAX_DIMENSION / width,
                    HTML_CANVAS_IMAGE_MAX_DIMENSION / height,
                    Math.sqrt(HTML_CANVAS_IMAGE_MAX_PIXELS / (width * height))
                );
                const canvas = await html2canvas(exportDocument.documentElement, {
                    allowTaint: false,
                    backgroundColor,
                    height,
                    imageTimeout: 5000,
                    logging: false,
                    scale,
                    scrollX: 0,
                    scrollY: 0,
                    useCORS: true,
                    width,
                    windowHeight: height,
                    windowWidth: width,
                });
    
                return await new Promise((resolve, reject) => {
                    canvas.toBlob((blob) => {
                        if (blob) resolve(blob);
                        else reject(new Error('PNG encoding failed.'));
                    }, 'image/png');
                });
            } finally {
                exportFrame.remove();
            }
        }
    

        return Object.freeze({
            replaceOmlorixFileUrls,
            normalizeCanvasHtmlSource,
            prepareInteractiveHtmlPreviewSource,
            rewriteCanvasHtmlPreviewHtml,
            withIframeSecurityGuard,
            renderHtmlCanvasPngBlob,
        });
    }

    modules.htmlDocuments = Object.freeze({ create: createHtmlDocumentsModule });
})(globalThis);
