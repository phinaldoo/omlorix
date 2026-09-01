/**
 * Shared request cache for cross-module data used by the chat shell.
 *
 * Several static frontend modules are loaded independently, so without a small
 * shared cache they can race and fetch the same startup data more than once.
 */
(function initSharedDataCache(global) {
    if (!global || global.SharedDataCache) {
        return;
    }

    const cache = new Map();

    function getFetcher() {
        if (typeof global.authedFetch === 'function') {
            return global.authedFetch.bind(global);
        }
        if (typeof global.fetch === 'function') {
            return global.fetch.bind(global);
        }
        return null;
    }

    async function fetchJsonOnce(key, url, { forceRefresh = false } = {}) {
        if (forceRefresh) {
            cache.delete(key);
        }

        if (!cache.has(key)) {
            const fetcher = getFetcher();
            if (!fetcher) {
                throw new Error('No fetch implementation available.');
            }

            const promise = fetcher(url, { method: 'GET' })
                .then(async (response) => {
                    if (!response?.ok) {
                        const status = response ? response.status : 'no-response';
                        throw new Error(`Failed to fetch ${url} (${status})`);
                    }
                    return response.json();
                })
                .catch((error) => {
                    cache.delete(key);
                    throw error;
                });

            cache.set(key, promise);
        }

        return cache.get(key);
    }

    function clear(key) {
        if (key) {
            cache.delete(key);
            return;
        }
        cache.clear();
    }

    global.SharedDataCache = {
        clear,
        getUserModels(options = {}) {
            return fetchJsonOnce('userModels', '/api/v1/llm/models/user', options);
        },
        getUserSettingsInit(options = {}) {
            return fetchJsonOnce('userSettingsInit', '/api/v1/users/user-settings/init', options);
        },
    };

    global.getCachedUserModels = (options = {}) => global.SharedDataCache.getUserModels(options);
    global.getCachedUserSettingsInit = (options = {}) => global.SharedDataCache.getUserSettingsInit(options);
})(typeof window !== 'undefined' && window ? window : globalThis);
