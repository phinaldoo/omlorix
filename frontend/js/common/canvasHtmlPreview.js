/* ========================================================================== 
   Canvas HTML Preview Runtime
   Hosts authored HTML through a trusted proxy and a nested opaque iframe.
   ========================================================================== */

(function () {
    'use strict';

    const PROXY_URL = '/api/v1/files/canvas/html-preview-proxy';
    const PROXY_SANDBOX = 'allow-scripts allow-same-origin allow-modals allow-downloads allow-forms allow-popups';
    const READY_MESSAGE = 'omlorix-canvas-html-preview-ready';
    const READY_REQUEST_MESSAGE = 'omlorix-canvas-html-preview-ready-request';
    const RENDER_MESSAGE = 'omlorix-canvas-html-preview-render';
    const LOADED_MESSAGE = 'omlorix-canvas-html-preview-loaded';
    const frameStates = new WeakMap();
    const sourceWindows = new WeakMap();
    let listenerInstalled = false;
    let previewSequence = 0;

    /**
     * Normalize one authored URL that points outside the current Omlorix
     * origin. Relative, data, Blob, fragment, and same-origin URLs do not need
     * the external-network grant and are therefore omitted from consent UI.
     */
    function normalizeExternalResourceUrl(value) {
        const rawValue = String(value || '').trim().replace(/^(['"])([\s\S]*)\1$/, '$2');
        if (!rawValue
            || rawValue.includes('${')
            || !/^(?:https?:|wss?:|\/\/)/i.test(rawValue)
            || typeof URL !== 'function') {
            return '';
        }

        try {
            const currentOrigin = String(window.location?.origin || 'http://localhost');
            const parsed = new URL(rawValue, `${currentOrigin}/`);
            if (!['http:', 'https:', 'ws:', 'wss:'].includes(parsed.protocol)) return '';
            if (['http:', 'https:'].includes(parsed.protocol) && parsed.origin === currentOrigin) return '';
            return parsed.href;
        } catch (_) {
            return '';
        }
    }

    /**
     * Collect concrete external URLs that authored HTML will try to load.
     *
     * This is intentionally a discovery aid rather than a security boundary:
     * the nested iframe's CSP remains authoritative and blocks anything this
     * static scan cannot resolve, such as a URL assembled dynamically at run
     * time. Returned values are normalized, deduplicated, and sorted so the UI
     * can make stable per-canvas consent decisions.
     */
    function collectExternalResources(source) {
        const markup = String(source || '').replace(/<!--[\s\S]*?-->/g, '');
        const resources = new Set();
        const add = (value) => {
            const normalized = normalizeExternalResourceUrl(value);
            if (normalized) resources.add(normalized);
        };

        // Resource-loading elements cover scripts, stylesheets, images,
        // fonts declared through preload, media, and nested frames.
        const resourceTagPattern = /<(script|link|img|source|video|audio|track|iframe|input|image|use)\b([^>]*)>/gi;
        let tagMatch;
        while ((tagMatch = resourceTagPattern.exec(markup)) !== null) {
            const tagName = String(tagMatch[1] || '').toLowerCase();
            const attributes = tagMatch[2] || '';
            if (tagName === 'link') {
                const relMatch = attributes.match(/\brel\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i);
                const rel = String(relMatch?.[1] || relMatch?.[2] || relMatch?.[3] || '').toLowerCase();
                if (!/(?:^|\s)(?:stylesheet|icon|preload|modulepreload|prefetch|preconnect|dns-prefetch|manifest)(?:\s|$)/.test(rel)) {
                    continue;
                }
            }

            const referencePattern = /\b(src|srcset|href|xlink:href|poster)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi;
            let referenceMatch;
            while ((referenceMatch = referencePattern.exec(attributes)) !== null) {
                const name = String(referenceMatch[1] || '').toLowerCase();
                const value = referenceMatch[2] || referenceMatch[3] || referenceMatch[4] || '';
                if (name === 'srcset') {
                    value.split(',').forEach((candidate) => add(candidate.trim().split(/\s+/)[0]));
                } else {
                    add(value);
                }
            }
        }

        // CSS can initiate requests from both style elements and inline style
        // attributes without a resource URL appearing in an HTML attribute.
        const cssSources = [];
        const styleBlockPattern = /<style\b[^>]*>([\s\S]*?)<\/style>/gi;
        let styleBlockMatch;
        while ((styleBlockMatch = styleBlockPattern.exec(markup)) !== null) {
            cssSources.push(styleBlockMatch[1] || '');
        }
        const htmlTagPattern = /<[a-z][^>]*>/gi;
        let htmlTagMatch;
        while ((htmlTagMatch = htmlTagPattern.exec(markup)) !== null) {
            const styleMatch = htmlTagMatch[0].match(/\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i);
            if (styleMatch) cssSources.push(styleMatch[1] || styleMatch[2] || styleMatch[3] || '');
        }
        const cssReferencePattern = /(?:url\(\s*([^)]*?)\s*\)|@import\s+(?:url\(\s*)?("[^"]+"|'[^']+'|[^\s;\)]+))/gi;
        cssSources.forEach((cssSource) => {
            cssReferencePattern.lastIndex = 0;
            let cssMatch;
            while ((cssMatch = cssReferencePattern.exec(cssSource)) !== null) {
                add(cssMatch[1] || cssMatch[2]);
            }
        });

        // Common JavaScript networking APIs are discoverable when passed a
        // literal URL. Dynamically assembled destinations remain CSP-blocked
        // until the user explicitly enables the dropdown switch.
        const connectionPattern = /\b(?:fetch|WebSocket|EventSource|Worker|SharedWorker|importScripts|import)\s*\(\s*(["'`])([^"'`]+)\1/gi;
        let connectionMatch;
        while ((connectionMatch = connectionPattern.exec(markup)) !== null) add(connectionMatch[2]);

        const xhrPattern = /\.open\s*\(\s*(["'])(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\1\s*,\s*(["'`])([^"'`]+)\2/gi;
        let xhrMatch;
        while ((xhrMatch = xhrPattern.exec(markup)) !== null) add(xhrMatch[3]);

        const modulePattern = /\b(?:import|export)\s+(?:[^;]*?\sfrom\s*)?(["'])(https?:\/\/[^"']+)\1/gi;
        let moduleMatch;
        while ((moduleMatch = modulePattern.exec(markup)) !== null) add(moduleMatch[2]);

        return Array.from(resources).sort((left, right) => left.localeCompare(right));
    }

    /** Return capabilities that deserve visible controls in the host UI. */
    function analyze(source) {
        const html = String(source || '');
        const markup = html.replace(/<!--[\s\S]*?-->/g, '');
        const scripts = /<script\b/i.test(markup)
            || /<[a-z][^>]*\son[a-z][\w:.-]*\s*=/i.test(markup)
            || /\bjavascript\s*:/i.test(markup)
            || /<iframe\b[^>]*\bsrcdoc\s*=/i.test(markup);

        // Dynamic JavaScript can construct destinations at runtime, so this is
        // intentionally conservative.  It controls whether the network button
        // is emphasized; the host can still expose the button unconditionally.
        const discoveredExternalContent = /(?:https?:)?\/\//i.test(markup)
            || /<(?:link|img|script|source|video|audio|track|iframe)\b[^>]*\b(?:src|href|srcset)\s*=/i.test(markup)
            || /@import\b|url\s*\(|\b(?:fetch|WebSocket|EventSource)\s*\(/i.test(markup)
            || /\.open\s*\(\s*['"](?:GET|POST|PUT|PATCH|DELETE)/i.test(markup);
        // Arbitrary authored JavaScript can navigate its own sandboxed frame
        // with location.href. Browsers provide no shipped CSP or iframe
        // sandbox control that blocks self-navigation while retaining scripts,
        // so every executable document requires the external-content grant.
        const externalContent = scripts || discoveredExternalContent;
        return { scripts, externalContent };
    }

    /**
     * Normalize requested permissions before they cross into the trusted
     * proxy. Authored scripts are never allowed without external content.
     *
     * The sole exception is the static visualization bridge: Omlorix removes
     * all authored scripts before adding its own no-eval message/resize
     * bootstrap. Requiring the bridge's other strict options keeps this escape
     * hatch narrow and prevents ordinary preview callers from selecting it.
     */
    function normalizePermissions(options = {}) {
        const allowExternalContent = options.allowExternalContent === true;
        const trustedLocalScripts = options.trustedLocalScripts === true
            && options.allowEval === false
            && options.relayVisualizationMessages === true
            && options.hydrateAuthenticatedFiles === false
            && !allowExternalContent;
        const allowScripts = options.allowScripts === true
            && (allowExternalContent || trustedLocalScripts);
        return { allowScripts, allowExternalContent, trustedLocalScripts };
    }

    function postCurrentDocument(frame, state) {
        const target = frame?.contentWindow;
        if (!target || !state?.ready) return false;
        target.postMessage({
            type: RENDER_MESSAGE,
            previewId: state.previewId,
            title: state.title,
            html: state.html,
            allowScripts: state.allowScripts,
            allowEval: state.allowEval,
            allowExternalContent: state.allowExternalContent,
            trustedLocalScripts: state.trustedLocalScripts,
            allowAuthenticatedFileHydration: state.hydrateAuthenticatedFiles,
            relayVisualizationMessages: state.relayVisualizationMessages,
        }, window.location.origin);
        return true;
    }

    function ensureMessageListener() {
        if (listenerInstalled) return;
        listenerInstalled = true;
        window.addEventListener('message', (event) => {
            if (event.origin !== window.location.origin || !event.source) return;
            const frame = sourceWindows.get(event.source);
            const state = frame ? frameStates.get(frame) : null;
            if (!frame || !state) return;

            if (event.data?.type === READY_MESSAGE) {
                state.ready = true;
                postCurrentDocument(frame, state);
                return;
            }
            if (event.data?.type === LOADED_MESSAGE && event.data?.previewId === state.previewId) {
                frame.dataset.canvasHtmlPreviewState = 'ready';
                frame.dispatchEvent(new CustomEvent('canvashtmlpreviewload', {
                    detail: {
                        allowScripts: state.allowScripts,
                        allowExternalContent: state.allowExternalContent,
                    },
                }));
            }
        });
    }

    /**
     * Mount or update an interactive HTML document in a host iframe.
     *
     * The visible iframe is trusted proxy code and therefore retains its
     * Omlorix origin.  Authored code runs one level deeper in an opaque-origin
     * iframe and cannot reach this window or Omlorix's credentials.
     */
    function render(frame, source, options = {}) {
        if (!(frame instanceof HTMLIFrameElement)) return false;
        ensureMessageListener();

        let state = frameStates.get(frame);
        if (!state) {
            previewSequence += 1;
            state = {
                previewId: `canvas-html-${Date.now()}-${previewSequence}`,
                ready: false,
                html: '',
                title: '',
                allowScripts: false,
                allowEval: true,
                allowExternalContent: false,
                trustedLocalScripts: false,
                relayVisualizationMessages: false,
                // Authenticated Canvas previews may hydrate their own file
                // references. Public artifact shares explicitly opt out.
                hydrateAuthenticatedFiles: true,
            };
            frameStates.set(frame, state);
            if (frame.contentWindow) sourceWindows.set(frame.contentWindow, frame);
            frame.addEventListener('load', () => {
                if (frame.contentWindow) {
                    sourceWindows.set(frame.contentWindow, frame);
                    // Explicitly request the handshake after the parent has
                    // registered this WindowProxy. This avoids a race when a
                    // newly inserted iframe loads faster than the host can
                    // associate its contentWindow with the preview state.
                    frame.contentWindow.postMessage({
                        type: READY_REQUEST_MESSAGE,
                    }, window.location.origin);
                }
                frame.dataset.canvasHtmlPreviewState = 'loading';
            });
            frame.setAttribute('sandbox', PROXY_SANDBOX);
            frame.setAttribute('referrerpolicy', 'no-referrer');
            frame.dataset.canvasHtmlPreviewState = 'loading';
            frame.removeAttribute('srcdoc');
            frame.src = PROXY_URL;
        }

        state.html = String(source || '');
        state.title = String(options.title || frame.title || 'Canvas HTML preview');
        const permissions = normalizePermissions(options);
        state.allowScripts = permissions.allowScripts;
        state.allowEval = state.allowScripts && !permissions.trustedLocalScripts && options.allowEval !== false;
        state.allowExternalContent = permissions.allowExternalContent;
        state.trustedLocalScripts = permissions.trustedLocalScripts;
        state.hydrateAuthenticatedFiles = options.hydrateAuthenticatedFiles !== false;
        state.relayVisualizationMessages = options.relayVisualizationMessages === true;
        frame.dataset.canvasHtmlScripts = state.allowScripts ? 'enabled' : 'disabled';
        frame.dataset.canvasHtmlExternalContent = state.allowExternalContent ? 'enabled' : 'blocked';
        postCurrentDocument(frame, state);
        return true;
    }

    /** Force a fresh proxy navigation while preserving the latest document. */
    function reload(frame) {
        const state = frameStates.get(frame);
        if (!state) return false;
        state.ready = false;
        frame.dataset.canvasHtmlPreviewState = 'loading';
        frame.src = `${PROXY_URL}?reload=${Date.now()}`;
        return true;
    }

    window.OmlorixCanvasHtmlPreview = Object.freeze({
        analyze,
        collectExternalResources,
        normalizePermissions,
        reload,
        render,
        PROXY_SANDBOX,
        PROXY_URL,
    });
})();
