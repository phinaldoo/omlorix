(function () {
    if (window.websearchProvidersApi) {
        return;
    }

    const t = window.adminT || ((key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    });

    const DEFAULT_ERROR = t('websearch_provider_request_failed', 'Web search provider request failed.');

    const readJsonSafe = async (response) => {
        try {
            return await response.json();
        } catch (error) {
            return null;
        }
    };

    const resolveErrorMessage = (payload, fallback) => {
        if (!payload || typeof payload !== 'object') {
            return fallback;
        }
        return payload.detail || payload.message || fallback;
    };

    const buildResponseError = async (response, fallback = DEFAULT_ERROR) => {
        const payload = await readJsonSafe(response);
        const message = resolveErrorMessage(payload, fallback);
        const error = new Error(message || fallback);
        error.status = response.status;
        error.payload = payload;
        return error;
    };

    const fetchJson = async (url, { errorMessage = DEFAULT_ERROR, ...options } = {}) => {
        const response = await window.authedFetch(url, options);
        if (!response.ok) {
            throw await buildResponseError(response, errorMessage);
        }
        return response.json();
    };

    const fetchProvidersList = () => fetchJson('/api/v1/websearch/providers', {
        errorMessage: t('websearch_providers_load_failed', 'Failed to load providers.')
    });

    const fetchAvailableProviders = () => fetchJson('/api/v1/websearch/providers/available', {
        errorMessage: t('websearch_providers_available_load_failed', 'Failed to load available providers.')
    });

    const fetchProviderSchema = (providerKey, { providerId } = {}) => {
        if (!providerKey) {
            return Promise.reject(new Error(t('websearch_provider_type_missing', 'Provider type missing.')));
        }
        const params = new URLSearchParams({ provider: providerKey });
        if (providerId) {
            params.set('provider_id', providerId);
        }
        return fetchJson(`/api/v1/websearch/provider/schema?${params.toString()}`, {
            errorMessage: t('websearch_provider_schema_load_failed', 'Failed to load provider schema.')
        });
    };

    const fetchProviderDetail = (providerId) => {
        if (!providerId) {
            return Promise.reject(new Error(t('websearch_provider_id_missing', 'Provider ID missing.')));
        }
        return fetchJson(`/api/v1/websearch/provider?provider_id=${encodeURIComponent(providerId)}`, {
            errorMessage: t('websearch_provider_open_failed', 'Failed to open provider.')
        });
    };

    const createProvider = (payload) => window.authedFetch('/api/v1/websearch/providers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    const updateProvider = (providerId, payload) => {
        if (!providerId) {
            return Promise.reject(new Error(t('websearch_provider_id_missing', 'Provider ID missing.')));
        }
        return window.authedFetch(`/api/v1/websearch/provider/${encodeURIComponent(providerId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
    };

    const deleteProvider = (providerId) => {
        if (!providerId) {
            return Promise.reject(new Error(t('websearch_provider_id_missing', 'Provider ID missing.')));
        }
        return window.authedFetch(`/api/v1/websearch/provider/${encodeURIComponent(providerId)}`, {
            method: 'DELETE'
        });
    };

    const exportProviders = () => window.authedFetch('/api/v1/websearch/providers/export');

    const importProviders = (payload) => window.authedFetch('/api/v1/websearch/providers/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });

    window.websearchProvidersApi = {
        authedFetch: window.authedFetch,
        buildResponseError,
        fetchProvidersList,
        fetchAvailableProviders,
        fetchProviderSchema,
        fetchProviderDetail,
        createProvider,
        updateProvider,
        deleteProvider,
        exportProviders,
        importProviders,
    };
})();
