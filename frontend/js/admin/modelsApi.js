(function () {
    if (window.modelsApi) {
        return;
    }

    const t = window.adminT || ((key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback ?? key);
        }
        return fallback !== undefined ? fallback : key;
    });

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

    const buildResponseError = async (response, fallback = t('admin_request_failed', 'Request failed.')) => {
        const payload = await readJsonSafe(response);
        const message = resolveErrorMessage(payload, fallback);
        const error = new Error(message || fallback);
        error.status = response.status;
        error.payload = payload;
        return error;
    };

    const fetchJson = async (url, { errorMessage = t('admin_request_failed', 'Request failed.'), ...options } = {}) => {
        const response = await window.authedFetch(url, options);
        if (!response.ok) {
            throw await buildResponseError(response, errorMessage);
        }
        return response.json();
    };

    const fetchAdminModels = () =>
        fetchJson('/api/v1/llm/models/admin', { errorMessage: t('models_load_failed', 'Failed to load models.') });

    const fetchProviderList = ({ modelCapableOnly = false } = {}) => {
        const params = new URLSearchParams();
        if (modelCapableOnly) {
            params.set('model_capable_only', 'true');
        }
        const query = params.toString();
        const url = query ? `/api/v1/llm/providers?${query}` : '/api/v1/llm/providers';
        return fetchJson(url, { errorMessage: t('providers_fetch_failed', 'Failed to load providers') });
    };

    const fetchProviderAvailableList = () =>
        fetchJson('/api/v1/llm/providers/available', {
            errorMessage: t('providers_fetch_available_failed', 'Failed to fetch available providers'),
        });

    const fetchProviderModels = (providerId) => {
        if (!providerId) {
            return Promise.reject(new Error(t('providers_id_missing', 'Provider ID missing.')));
        }
        const params = new URLSearchParams({ provider_id: providerId });
        return fetchJson(`/api/v1/llm/models?${params.toString()}`, {
            errorMessage: t('models_create_provider_models_failed', 'Failed to load provider models.'),
        });
    };

    const fetchTranscriptionModels = (providerId) => {
        const params = new URLSearchParams();
        if (providerId) {
            params.set('provider_id', providerId);
        }
        const query = params.toString();
        const url = query
            ? `/api/v1/admin/settings/dictation/transcription/models?${query}`
            : '/api/v1/admin/settings/dictation/transcription/models';
        return fetchJson(url, {
            errorMessage: t('models_transcription_load_failed', 'Failed to load transcription models.'),
        });
    };

    const fetchRealtimeModels = (providerId) => {
        const params = new URLSearchParams();
        if (providerId) {
            params.set('provider_id', providerId);
        }
        const query = params.toString();
        const url = query
            ? `/api/v1/admin/settings/realtime/models?${query}`
            : '/api/v1/admin/settings/realtime/models';
        return fetchJson(url, {
            errorMessage: t('models_realtime_load_failed', 'Failed to load realtime models.'),
        });
    };

    const fetchLiveTranscriptionModels = (providerId) => {
        const params = new URLSearchParams();
        if (providerId) {
            params.set('provider_id', providerId);
        }
        const query = params.toString();
        const url = query
            ? `/api/v1/admin/settings/dictation/live-transcription/models?${query}`
            : '/api/v1/admin/settings/dictation/live-transcription/models';
        return fetchJson(url, {
            errorMessage: t(
                'models_live_transcription_load_failed',
                'Failed to load live transcription models.'
            ),
        });
    };

    const fetchReadAloudProviders = () =>
        fetchJson('/api/v1/admin/settings/audio_generation/providers', {
            errorMessage: t('audio_generation_providers_load_failed', 'Failed to load read aloud providers.'),
        });

    const searchReadAloudVoices = ({
        providerId,
        search = '',
        pageSize = 24,
        nextPageToken = '',
        voiceIds = [],
    } = {}) => {
        if (!providerId) {
            return Promise.reject(new Error(t('providers_id_missing', 'Provider ID missing.')));
        }
        const params = new URLSearchParams({ provider_id: providerId });
        const normalizedSearch = String(search || '').trim();
        if (normalizedSearch) {
            params.set('search', normalizedSearch);
        }
        const normalizedNextPageToken = String(nextPageToken || '').trim();
        if (normalizedNextPageToken) {
            params.set('next_page_token', normalizedNextPageToken);
        }
        if (Number.isFinite(pageSize)) {
            params.set('page_size', String(pageSize));
        }
        const normalizedVoiceIds = Array.isArray(voiceIds)
            ? voiceIds.map((voiceId) => String(voiceId || '').trim()).filter(Boolean)
            : [];
        if (normalizedVoiceIds.length) {
            params.set('voice_ids', normalizedVoiceIds.join(','));
        }
        return fetchJson(`/api/v1/admin/settings/audio_generation/voices?${params.toString()}`, {
            errorMessage: t('audio_generation_voice_search_failed', 'Failed to load provider voices.'),
        });
    };

    const fetchProviderModelSchema = (providerKey, providerId, thirdArg = {}) => {
        if (!providerKey || !providerId) {
            return Promise.resolve({});
        }
        const options = typeof thirdArg === 'string'
            ? { modelId: thirdArg }
            : (thirdArg && typeof thirdArg === 'object' ? thirdArg : {});
        const { modelId = null, modelName = null, modelProvider = null } = options;
        const params = new URLSearchParams({ provider: providerKey, provider_id: providerId });
        if (modelId) {
            params.set('model_id', modelId);
        }
        if (modelName) {
            params.set('model_name', modelName);
        }
        if (modelProvider) {
            params.set('model_provider', modelProvider);
        }
        return fetchJson(`/api/v1/llm/model?${params.toString()}`, {
            errorMessage: t('models_schema_load_failed', 'Failed to load model schema.'),
        });
    };

    const fetchProviderValues = (providerId) => {
        if (!providerId) {
            return Promise.reject(new Error(t('providers_id_missing', 'Provider ID missing.')));
        }
        const params = new URLSearchParams({ provider_id: providerId });
        return fetchJson(`/api/v1/llm/provider/values?${params.toString()}`, {
            errorMessage: t('providers_values_load_failed', 'Failed to load provider values.'),
        });
    };

    const fetchOpenRouterModelProviders = (openrouterProviderId, modelName) => {
        if (!openrouterProviderId) {
            return Promise.reject(new Error(t('models_openrouter_provider_missing', 'OpenRouter provider ID missing.')));
        }
        if (!modelName) {
            return Promise.reject(new Error(t('models_name_missing', 'Model name missing.')));
        }
        const params = new URLSearchParams({
            openrouter_provider_id: openrouterProviderId,
            model_name: modelName,
        });
        return fetchJson(`/api/v1/llm/model/openrouter/providers/byname?${params.toString()}`, {
            errorMessage: t('models_create_openrouter_load_failed', 'Failed to load OpenRouter model providers.'),
        });
    };


    const createModel = (payload) =>
        fetchJson('/api/v1/llm/model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            errorMessage: t('models_create_failed', 'Failed to create model.'),
        });

    const duplicateModel = (modelId) =>
        window.authedFetch(`/api/v1/llm/model/duplicate?model_id=${encodeURIComponent(modelId)}`, {
            method: 'POST',
        });

    const exportModels = () => window.authedFetch('/api/v1/llm/models/export');

    const importModels = (payload) =>
        window.authedFetch('/api/v1/llm/models/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

    const deleteModel = (modelId) =>
        window.authedFetch(`/api/v1/llm/model?model_id=${encodeURIComponent(modelId)}`, {
            method: 'DELETE',
        });

    const updateModel = (modelId, payload) =>
        window.authedFetch(`/api/v1/llm/model?model_id=${encodeURIComponent(modelId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

    const bulkUpdateModels = (payload) =>
        window.authedFetch('/api/v1/llm/models/bulk-update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

    window.modelsApi = {
        authedFetch: window.authedFetch,
        fetchJson,
        buildResponseError,
        fetchAdminModels,
        fetchProviderList,
        fetchProviderAvailableList,
        fetchProviderModels,
        fetchTranscriptionModels,
        fetchLiveTranscriptionModels,
        fetchRealtimeModels,
        fetchReadAloudProviders,
        searchReadAloudVoices,
        fetchProviderModelSchema,
        fetchProviderValues,
        fetchOpenRouterModelProviders,
        createModel,
        exportModels,
        importModels,
        deleteModel,
        updateModel,
        bulkUpdateModels,
        duplicateModel,
    };
})();
