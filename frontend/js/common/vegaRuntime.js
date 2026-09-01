(function () {
    'use strict';

    if (typeof window === 'undefined' || window.OmlorixVegaRuntime) {
        return;
    }

    const VEGA_SCRIPT_URL = '/js/vendor/vega.min.js?v=1';
    const VEGA_LITE_SCRIPT_URL = '/js/vendor/vega-lite.min.js?v=1';
    const VEGA_EMBED_SCRIPT_URL = '/js/vendor/vega-embed.min.js?v=1';

    let vegaLoadPromise = null;

    function findRuntimeScript(url, dataKey) {
        if (typeof document === 'undefined' || typeof document.querySelector !== 'function') {
            return null;
        }
        return document.querySelector(`script[${dataKey}="true"], script[src="${url}"]`);
    }

    function loadScript(url, dataKey, isReady) {
        if (typeof document === 'undefined') {
            return Promise.resolve(null);
        }
        if (typeof isReady === 'function' && isReady()) {
            return Promise.resolve(true);
        }

        return new Promise((resolve, reject) => {
            const existingScript = findRuntimeScript(url, dataKey);
            const script = existingScript || document.createElement('script');

            const cleanup = () => {
                script.removeEventListener('load', handleLoad);
                script.removeEventListener('error', handleError);
            };

            const handleLoad = () => {
                cleanup();
                if (typeof isReady === 'function' && !isReady()) {
                    reject(new Error(`Loaded ${url} but runtime API is unavailable.`));
                    return;
                }
                resolve(true);
            };

            const handleError = () => {
                cleanup();
                if (!existingScript && script.parentNode) {
                    script.parentNode.removeChild(script);
                }
                reject(new Error(`Failed to load runtime script: ${url}`));
            };

            script.addEventListener('load', handleLoad, { once: true });
            script.addEventListener('error', handleError, { once: true });

            if (!existingScript) {
                script.src = url;
                script.async = true;
                script.defer = true;
                script.setAttribute(dataKey, 'true');
                document.head.appendChild(script);
            }
        });
    }

    function loadVegaRuntime() {
        if (vegaLoadPromise) {
            return vegaLoadPromise;
        }

        vegaLoadPromise = (async () => {
            await loadScript(
                VEGA_SCRIPT_URL,
                'data-vega-runtime',
                () => Boolean(window.vega && typeof window.vega.parse === 'function')
            );
            await loadScript(
                VEGA_LITE_SCRIPT_URL,
                'data-vega-lite-runtime',
                () => Boolean(window.vegaLite && typeof window.vegaLite.compile === 'function')
            );
            await loadScript(
                VEGA_EMBED_SCRIPT_URL,
                'data-vega-embed-runtime',
                () => typeof window.vegaEmbed === 'function'
            );

            return {
                vega: window.vega,
                vegaLite: window.vegaLite,
                vegaEmbed: window.vegaEmbed,
            };
        })().catch((error) => {
            vegaLoadPromise = null;
            throw error;
        });

        return vegaLoadPromise;
    }

    window.OmlorixVegaRuntime = Object.freeze({
        VEGA_SCRIPT_URL,
        VEGA_LITE_SCRIPT_URL,
        VEGA_EMBED_SCRIPT_URL,
        loadVegaRuntime,
    });
})();
