// Fetch the schema (and optionally values) from the backend
async function fetchSettingsSchema({ page, includeValues = false, signal } = {}) {
    if (typeof page !== 'string' || !page.trim()) {
        notifyError(helperT('admin_settings_page_key_required', 'A settings page key is required.'));
        return;
    }

    const query = new URLSearchParams({ page: page.trim() });
    query.set('include_values', includeValues ? 'true' : 'false');

    return fetchAdminJson(
        `/api/v1/admin/schema?${query.toString()}`,
        { signal },
        'Failed to fetch schema'
    );
}

async function updateSettingsValues(page, payload, { signal } = {}) {
    return fetchAdminJson(
        `/api/v1/admin/values/?page=${encodeURIComponent(page)}`,
        {
            method: 'POST',
            body: payload,
            signal,
        },
        'Failed to update settings values'
    );
}

async function fetchAdminUserSettingsSchema({ includeValues = false, userId, signal } = {}) {
    const params = new URLSearchParams();
    if (includeValues) {
        params.set('include_values', 'true');
    }
    if (userId) {
        params.set('user_id', userId);
    }
    const query = params.toString();
    const url = query ? `/api/v1/admin/user/settings?${query}` : '/api/v1/admin/user/settings';
    return fetchAdminJson(url, { signal }, 'Failed to load user settings schema');
}

async function updateAdminUserSettings(payload, { signal } = {}) {
    return fetchAdminJson(
        '/api/v1/admin/user/settings',
        {
            method: 'PATCH',
            body: payload,
            signal,
        },
        'Failed to update user settings'
    );
}

async function fetchAdminGroupsList() {
    const response = await fetchAdminJson('/api/v1/groups/list', {}, 'Failed to load groups');
    if (!response) {
        return [];
    }
    if (Array.isArray(response)) {
        return response;
    }
    if (Array.isArray(response?.groups)) {
        return response.groups;
    }
    return [];
}

async function fetchAdminUserProfile(
    userId,
    {
        signal,
        reason = '',
        includeSensitiveProfile = false,
        includeSecurity = false,
        includeActivity = false,
    } = {}
) {
    if (!userId) {
        return null;
    }
    const body = {
        user_id: userId,
        include_sensitive_profile: Boolean(includeSensitiveProfile),
        include_security: Boolean(includeSecurity),
        include_activity: Boolean(includeActivity),
    };
    if (reason) {
        body.reason = reason;
    }
    return fetchAdminJson(
        '/api/v1/admin/user/profile',
        {
            method: 'POST',
            body,
            signal,
        },
        'Failed to load user profile'
    );
}

async function updateAdminUserProfile(payload, { signal } = {}) {
    return fetchAdminJson(
        '/api/v1/admin/user/profile',
        {
            method: 'PATCH',
            body: payload,
            signal,
        },
        'Failed to update user profile'
    );
}

async function resetAdminUserTwofa(payload, { signal } = {}) {
    return fetchAdminJson(
        '/api/v1/admin/user/security/reset-2fa',
        {
            method: 'POST',
            body: payload,
            signal,
        },
        'Failed to reset user 2FA'
    );
}

async function createAdminUser(payload, { signal } = {}) {
    return fetchAdminJson(
        '/api/v1/admin/user/create',
        {
            method: 'POST',
            body: payload,
            signal,
        },
        'Failed to create user'
    );
}

function createDebounced(fn, delay) {
    if (typeof delay !== 'number' || delay <= 0) {
        return Object.assign(fn.bind(null), {
            cancel: () => { },
        });
    }

    let timerId = null;
    const debounced = (...args) => {
        if (timerId) {
            clearTimeout(timerId);
        }
        timerId = setTimeout(() => {
            timerId = null;
            fn(...args);
        }, delay);
    };

    debounced.cancel = () => {
        if (timerId) {
            clearTimeout(timerId);
            timerId = null;
        }
    };

    return debounced;
}

