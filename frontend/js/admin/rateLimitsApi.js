(function () {
    if (window.rateLimitsApi) {
        return;
    }

    const readJsonSafe = async (response) => {
        try {
            return await response.json();
        } catch (error) {
            return null;
        }
    };

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const resolveErrorMessage = (payload, fallback) => {
        if (!payload || typeof payload !== 'object') {
            return fallback;
        }
        return payload.detail || payload.message || fallback;
    };

    const buildResponseError = async (response, fallback = 'Request failed.') => {
        const payload = await readJsonSafe(response);
        const error = new Error(resolveErrorMessage(payload, fallback));
        error.status = response.status;
        error.payload = payload;
        return error;
    };

    const fetchJson = async (url, { errorMessage = 'Request failed.', ...options } = {}) => {
        const response = await window.authedFetch(url, options);
        if (!response.ok) {
            throw await buildResponseError(response, errorMessage);
        }
        return response.json();
    };

    window.rateLimitsApi = {
        fetchJson,
        buildResponseError,
        fetchRateLimits: () =>
            fetchJson('/api/v1/llm/rate-limits', {
                errorMessage: t('rate_limit_load_failed', 'Failed to load rate limits.'),
            }),
        fetchRateLimit: (id) =>
            fetchJson(`/api/v1/llm/rate-limit?rate_limit_id=${encodeURIComponent(id)}`, {
                errorMessage: t('rate_limit_load_one_failed', 'Failed to load rate limit.'),
            }),
        fetchRateLimitTools: () =>
            fetchJson('/api/v1/llm/rate-limit-tools', {
                errorMessage: t('rate_limit_fetch_tools_failed', 'Failed to load tools.'),
            }),
        createRateLimit: (payload) =>
            fetchJson('/api/v1/llm/rate-limit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                errorMessage: t('rate_limit_save_failed', 'Failed to save rate limit.'),
            }),
        updateRateLimit: (id, payload) =>
            fetchJson(`/api/v1/llm/rate-limit?rate_limit_id=${encodeURIComponent(id)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                errorMessage: t('rate_limit_save_failed', 'Failed to save rate limit.'),
            }),
        deleteRateLimit: (id) =>
            window.authedFetch(`/api/v1/llm/rate-limit?rate_limit_id=${encodeURIComponent(id)}`, {
                method: 'DELETE',
            }),
    };
})();
