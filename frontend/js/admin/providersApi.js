(function () {
    if (window.providersApi) {
        return;
    }

    const t = window.adminT || ((key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback ?? key);
        }
        return fallback !== undefined ? fallback : key;
    });

    const DEFAULT_ERROR = t('admin_request_failed', 'Request failed.');

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

        const { detail, message } = payload;

        if (typeof detail === 'string' && detail.trim()) {
            return detail;
        }

        if (Array.isArray(detail) && detail.length) {
            const first = detail[0];
            if (typeof first === 'string') {
                return first;
            }
            if (first && typeof first === 'object') {
                return first.msg || first.message || JSON.stringify(first);
            }
        }

        if (detail && typeof detail === 'object') {
            const nested = detail.msg || detail.message;
            if (typeof nested === 'string' && nested.trim()) {
                return nested;
            }
        }

        if (typeof message === 'string' && message.trim()) {
            return message;
        }

        return fallback;
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

    const fetchJsonWithMeta = async (url, options = {}) => {
        const response = await window.authedFetch(url, options);
        const data = await readJsonSafe(response);
        return { response, data };
    };

    /**
     * Fetch provider schema, optionally with values populated.
     * When providerId is given, the backend returns field values directly in the schema.
     * @param {string} providerKey - The provider type (e.g., 'openai', 'anthropic')
     * @param {string|null} providerId - Optional provider ID to get schema with values
     * @returns {Promise<object>} Schema object with sections and fields
     */
    const fetchProviderSchema = (providerKey, providerId = null) => {
        if (!providerKey) {
            return Promise.reject(new Error(t('providers_type_missing', 'Provider type missing.')));
        }
        const params = new URLSearchParams({ provider: providerKey });
        if (providerId) {
            params.set('provider_id', providerId);
        }
        return fetchJson(
            `/api/v1/llm/provider?${params.toString()}`,
            { errorMessage: t('provider_schema_fetch_failed', 'Failed to fetch provider schema.') }
        );
    };

    const fetchProvidersList = () => fetchJson(
        '/api/v1/llm/providers',
        { errorMessage: t('providers_fetch_list_failed', 'Failed to fetch provider list') }
    );

    const fetchAvailableProviders = () => fetchJson(
        '/api/v1/llm/providers/available',
        { errorMessage: t('providers_fetch_available_failed', 'Failed to fetch available providers') }
    );

    const exportProviders = () => window.authedFetch('/api/v1/llm/providers/export');

    const testProviderConnection = (payload) => {
        if (!payload?.provider) {
            return Promise.reject(new Error(t('providers_type_missing', 'Provider type missing.')));
        }
        return fetchJson('/api/v1/llm/provider/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            errorMessage: t('provider_test_connection_failed', 'Failed to test provider connection.')
        });
    };

    /**
     * Create a new provider.
     * @param {object} payload - Provider data { provider, name, api_key, settings }
     * @returns {Promise<object>} Created provider
     */
    const createProvider = (payload) => {
        if (!payload?.provider) {
            return Promise.reject(new Error(t('providers_type_missing', 'Provider type missing.')));
        }
        return fetchJson('/api/v1/llm/provider', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            errorMessage: t('provider_form_create_failed', 'Failed to create provider.')
        });
    };

    /**
     * Update an existing provider.
     * @param {string} providerId - The provider ID to update
     * @param {object} payload - Provider data { name, api_key, settings }
     * @returns {Promise<object>} Updated provider
     */
    const updateProvider = (providerId, payload) => {
        if (!providerId) {
            return Promise.reject(new Error(t('providers_id_missing', 'Provider ID missing.')));
        }
        return fetchJson(`/api/v1/llm/provider?provider_id=${encodeURIComponent(providerId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            errorMessage: t('provider_form_update_failed', 'Failed to update provider.')
        });
    };

    const deleteProvider = (providerId, handleGroups = false) => {
        if (!providerId) {
            return Promise.reject(new Error(t('providers_id_missing', 'Provider ID missing.')));
        }
        const params = new URLSearchParams({ provider_id: providerId });
        if (handleGroups) {
            params.set('handle_groups', 'true');
        }
        return window.authedFetch(`/api/v1/llm/provider?${params.toString()}`, {
            method: 'DELETE'
        });
    };

    const checkProviderGroupMembership = (providerId) => {
        if (!providerId) {
            return Promise.reject(new Error(t('providers_id_missing', 'Provider ID missing.')));
        }
        return fetchJson(
            `/api/v1/llm/provider/groups?provider_id=${encodeURIComponent(providerId)}`,
            { errorMessage: t('providers_group_membership_failed', 'Failed to check provider group membership.') }
        );
    };

    window.providersApi = {
        authedFetch: window.authedFetch,
        fetchJson,
        fetchJsonWithMeta,
        fetchProviderSchema,
        fetchProvidersList,
        fetchAvailableProviders,
        exportProviders,
        testProviderConnection,
        createProvider,
        updateProvider,
        deleteProvider,
        checkProviderGroupMembership,
        buildResponseError
    };
})();
