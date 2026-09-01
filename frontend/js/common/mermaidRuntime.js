(function () {
    'use strict';

    if (typeof window === 'undefined' || window.OmlorixMermaidRuntime) {
        return;
    }

    let mermaidLoadPromise = null;
    const MERMAID_SCRIPT_URL = '/js/vendor/mermaid.min.js?v=1';

    function normalizeMermaidSource(source) {
        const text = String(source ?? '');
        if (!text) {
            return text;
        }

        // Mermaid 11.x currently interprets plain labels like "1. Title" or "- Item"
        // as markdown lists in flowchart labels. Use Mermaid entity codes so the
        // rendered text stays unchanged while avoiding the markdown-list parser.
        let normalized = text.replace(
            /([\[\(\{\|"'\`])([ \t]*)(\d+)\.(?=(?:\s|$|<|[\]\)\}\|"'\`]))/g,
            '$1$2$3#46;'
        );
        normalized = normalized.replace(
            /([\[\(\{\|"'\`])([ \t]*)([-+*])(?=\s)/g,
            (_, prefix, spacing, marker) => `${prefix}${spacing}#${marker.charCodeAt(0)};`
        );
        normalized = normalized.replace(
            /(<br\s*\/?>|\n)([ \t]*)(\d+)\.(?=(?:\s|$|<|[\]\)\}\|"'\`]))/gi,
            '$1$2$3#46;'
        );
        normalized = normalized.replace(
            /(<br\s*\/?>|\n)([ \t]*)([-+*])(?=\s)/gi,
            (_, lineBreak, spacing, marker) => `${lineBreak}${spacing}#${marker.charCodeAt(0)};`
        );

        return normalized;
    }

    function findMermaidRuntimeScript() {
        if (typeof document === 'undefined' || typeof document.querySelector !== 'function') {
            return null;
        }
        return document.querySelector(`script[data-mermaid-runtime="true"], script[src="${MERMAID_SCRIPT_URL}"]`);
    }

    function loadMermaidRuntime() {
        if (typeof document === 'undefined') {
            return Promise.resolve(null);
        }
        if (window.mermaid && typeof window.mermaid.initialize === 'function') {
            return Promise.resolve(window.mermaid);
        }
        if (mermaidLoadPromise) {
            return mermaidLoadPromise;
        }

        mermaidLoadPromise = new Promise((resolve, reject) => {
            const existingScript = findMermaidRuntimeScript();
            const script = existingScript || document.createElement('script');

            const cleanup = () => {
                script.removeEventListener('load', handleLoad);
                script.removeEventListener('error', handleError);
            };

            const handleLoad = () => {
                cleanup();
                if (window.mermaid && typeof window.mermaid.initialize === 'function') {
                    resolve(window.mermaid);
                    return;
                }
                mermaidLoadPromise = null;
                reject(new Error('Mermaid loaded without runtime API.'));
            };

            const handleError = () => {
                cleanup();
                mermaidLoadPromise = null;
                if (!existingScript && script.parentNode) {
                    script.parentNode.removeChild(script);
                }
                reject(new Error('Failed to load Mermaid runtime.'));
            };

            script.addEventListener('load', handleLoad, { once: true });
            script.addEventListener('error', handleError, { once: true });

            if (!existingScript) {
                script.src = MERMAID_SCRIPT_URL;
                script.async = true;
                script.defer = true;
                script.dataset.mermaidRuntime = 'true';
                document.head.appendChild(script);
            }
        });

        return mermaidLoadPromise;
    }

    async function initializeMermaidRuntime({ theme = 'default', htmlLabels = true } = {}) {
        const mermaidApi = await loadMermaidRuntime();
        if (!mermaidApi || typeof mermaidApi.initialize !== 'function') {
            return null;
        }
        try {
            const config = {
                startOnLoad: false,
                securityLevel: 'strict',
                theme,
                suppressErrorRendering: true,
            };

            // Public previews sanitize Mermaid's SVG before inserting it. When
            // HTML labels are disabled, both node and flowchart edge labels are
            // emitted as SVG text and survive that sanitizer. Keep HTML labels
            // enabled by default for the authenticated preview, which inserts
            // Mermaid's trusted local render directly.
            if (htmlLabels === false) {
                config.htmlLabels = false;
                config.flowchart = { htmlLabels: false };
            }

            mermaidApi.initialize(config);
        } catch (_) {}
        return mermaidApi;
    }

    window.OmlorixMermaidRuntime = Object.freeze({
        MERMAID_SCRIPT_URL,
        loadMermaidRuntime,
        initializeMermaidRuntime,
        normalizeMermaidSource,
    });
})();
