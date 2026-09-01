(() => {
    const STORAGE_KEY = 'omlorix_byok_v1';
    const SESSION_CREDENTIAL_TOKENS_KEY = 'omlorix_byok_session_credentials_v2';
    const LEGACY_SESSION_SECRETS_KEY = 'omlorix_byok_session_secrets_v1';
    const SELECTED_MODEL_KEY = 'omlorix_selected_model_id';
    const REMOTE_MODELS_LIMIT = 200;
    const PROVIDER_URL_SUGGESTIONS_METADATA_KEY = 'provider_url_suggestions';
    const BYOK_SCHEMA_METADATA_KEY = 'byok';
    const BYOK_BASE_URL_SUGGESTIONS_KEY = 'base_url_suggestions';
    const CUSTOM_PROVIDER_URL_OPTION_VALUE = '__custom__';
    const MODEL_DESCRIPTION_MAX_LENGTH = 100;
    const OPTIONAL_API_KEY_PROVIDERS = new Set(['ollama', 'lmstudio']);
    const ANTHROPIC_DEPRECATED_MODEL_SETTING_KEYS = [
        'output_format',
        'temperature',
        'top_k',
        'top_p',
    ];
    // Keep the current tab usable when sessionStorage is blocked or full. Only
    // server-sealed tokens are copied here; raw keys live in memory solely
    // during initial entry or the one-time migration from older releases.
    const providerMemoryCredentialTokens = new Map();
    const legacyProviderSecrets = new Map();

    function formatTranslation(key, fallback, vars) {
        if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(fallback || key).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars && Object.prototype.hasOwnProperty.call(vars, token) ? vars[token] : '';
            return value == null ? '' : String(value);
        });
    }

    function translationRef(key, fallback, vars = null) {
        return { key, fallback, vars };
    }

    /** Resolve copy from stable keys each time a dynamic surface is rendered. */
    function resolveTranslationRef(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value) || !value.key) {
            return String(value ?? '');
        }
        if (value.vars && typeof value.vars === 'object') {
            const resolvedVars = Object.fromEntries(
                Object.entries(value.vars).map(([key, entry]) => [key, resolveTranslationRef(entry)]),
            );
            return formatTranslation(value.key, value.fallback || '', resolvedVars);
        }
        return byokT(value.key, value.fallback || '');
    }

    const PROVIDER_OPTIONS = [
        { value: 'openai', label: 'OpenAI', icon: 'openai' },
        { value: 'openrouter', label: 'OpenRouter', icon: 'openrouter' },
        { value: 'openai_responses', label: 'OpenAI Responses API', icon: 'openai' },
        { value: 'xai', label: 'xAI', icon: 'xai' },
        { value: 'openai_chat_completions', label: 'OpenAI Chat Completions API', icon: 'openai' },
        { value: 'microsoft_azure', label: 'Microsoft Azure', icon: 'microsoft' },
        { value: 'ollama', label: 'Ollama', icon: 'ollama' },
        { value: 'lmstudio', label: 'LM Studio', icon: 'lmstudio' },
        { value: 'anthropic', label: 'Anthropic', icon: 'anthropic' },
        { value: 'anthropic_base', label: 'Anthropic (Custom Base URL)', icon: 'anthropic' },
        { value: 'google_aistudio', label: 'Google AI Studio', icon: 'google_aistudio' },
    ];
    // These are the only BYOK protocols whose endpoint is intentionally a
    // compatible/custom service. Native providers keep their brand icon even
    // when their connection settings include an endpoint URL.
    const CUSTOM_PROVIDER_ICON_TYPES = new Set([
        'openai_responses',
        'openai_chat_completions',
        'anthropic_base',
    ]);

    // The backend owns the authoritative BYOK schema boundary. This client-side
    // list is a defensive guard for cached or older responses so shared inputs
    // and server lifecycle controls can never be rendered twice.
    const BYOK_PROVIDER_SCHEMA_EXCLUDED_FIELDS = new Set([
        'name',
        'api_key',
        'settings.base_url',
        'settings.disable_background_sync',
        'settings.enable_auto_delete_missing_models',
        'settings.enable_notify_model_changes',
    ]);
    const BYOK_PROVIDER_SPECIFIC_EXCLUDED_FIELDS = {
        openrouter: new Set([
            'settings.ranking_url',
            'settings.ranking_title',
        ]),
    };

    function normalizeProviderType(value) {
        return trimString(value).toLowerCase();
    }

    /**
     * Return the visual preset associated with a provider protocol. Several
     * protocols intentionally share a brand, so their stored icon must not use
     * the protocol key when no matching icon asset exists.
     */
    function getDefaultProviderIcon(providerType) {
        const normalized = normalizeProviderType(providerType);
        return PROVIDER_OPTIONS.find((option) => option.value === normalized)?.icon || normalized;
    }

    /**
     * Resolve provider icons according to the same policy as the admin UI and
     * backend. Native provider records are upgraded in memory to their fixed
     * brand icon; compatible custom endpoints retain the selected icon.
     */
    function resolveProviderIcon(providerType, iconValue = '') {
        const normalizedProviderType = normalizeProviderType(providerType);
        const sanitized = sanitizeIconValue(iconValue);
        const rawProviderType = trimString(providerType).toLowerCase();
        if (!CUSTOM_PROVIDER_ICON_TYPES.has(normalizedProviderType)) {
            return getDefaultProviderIcon(providerType);
        }
        if (!sanitized || sanitized === rawProviderType || sanitized === normalizeProviderType(providerType)) {
            return getDefaultProviderIcon(providerType);
        }
        return sanitized;
    }

    function normalizeModelDescription(value) {
        return String(value || '').trim().slice(0, MODEL_DESCRIPTION_MAX_LENGTH);
    }

    const state = {
        allow: false,
        defaultScrapeProvider: '',
        defaultSearchProvider: '',
        byokStatisticsEnabled: false,
        byokStatisticsRetentionDays: 90,
        byokStatsDays: 30,
        byokStatsLoading: false,
        byokStatsError: '',
        byokStatsLoadedDays: null,
        byokStats: {
            overview: null,
            providers: [],
            models: [],
            errors: [],
            toolOverview: null,
            tools: [],
        },
        data: loadStorage(),
        adminModels: [],
        providerSchema: null,
        // Provider schemas are static for the lifetime of this page. Cache the
        // in-flight promise as well as the resolved response so opening the
        // editor repeatedly cannot issue duplicate requests.
        providerSchemaCache: new Map(),
        providerSchemaRequestToken: 0,
        providerFormContext: null,
        providerSettingsHost: null,
        providerBaseUrlSuggestions: [],
        modelSchema: null,
        modelFormContext: null,
        modelSettingsHost: null,
        remoteModels: [],
        remoteModelsLoading: false,
        remoteModelsManualMode: false,
        remoteModelsStatus: null,
        remoteModelsRequestToken: 0,
        providerEditingId: null,
        modelEditingId: null,
        providerModalOpen: false,
        modelModalOpen: false,
        dialogOpen: false,
        dialogVariant: 'danger',
        dialogConfig: null,
        dialogResolver: null,
        lastFocusedElement: null,
        domReady: false,
    };

    function safeJsonParse(raw, fallback) {
        if (!raw) return fallback;
        try {
            return JSON.parse(raw);
        } catch (_) {
            return fallback;
        }
    }

    function generateLocalId(prefix) {
        return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    }

    function getLocalStorage() {
        try {
            return typeof window === 'undefined' ? null : window.localStorage;
        } catch (_error) {
            return null;
        }
    }

    function getSessionStorage() {
        try {
            return typeof window === 'undefined' ? null : window.sessionStorage;
        } catch (_error) {
            return null;
        }
    }

    function byokT(key, fallback) {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function providerSecretKey(providerId) {
        return String(providerId || '').trim();
    }

    function normalizeCredentialTokenRecord(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
        const token = String(value.token || '').trim();
        const expiresAt = String(value.expires_at || '').trim();
        if (!token || !expiresAt || !Number.isFinite(Date.parse(expiresAt))) return null;
        return { token, expires_at: expiresAt };
    }

    /** Read tab-scoped sealed tokens without ever reading a raw provider key. */
    function readProviderSessionCredentialTokens() {
        const storage = getSessionStorage();
        const memoryTokens = Object.fromEntries(providerMemoryCredentialTokens);
        if (!storage) return memoryTokens;
        try {
            const parsed = safeJsonParse(storage.getItem(SESSION_CREDENTIAL_TOKENS_KEY), {});
            const storedTokens = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
            return { ...storedTokens, ...memoryTokens };
        } catch (error) {
            console.warn('Failed to read BYOK session credential tokens:', error);
            return memoryTokens;
        }
    }

    /** Persist only opaque server-sealed tokens for the current browser tab. */
    function writeProviderSessionCredentialTokens(tokens) {
        providerMemoryCredentialTokens.clear();
        const sanitizedTokens = {};
        Object.entries(tokens).forEach(([key, value]) => {
            const record = normalizeCredentialTokenRecord(value);
            if (record) {
                providerMemoryCredentialTokens.set(key, record);
                sanitizedTokens[key] = record;
            }
        });
        const storage = getSessionStorage();
        if (!storage) return;
        try {
            storage.setItem(SESSION_CREDENTIAL_TOKENS_KEY, JSON.stringify(sanitizedTokens));
        } catch (error) {
            console.warn('Failed to save BYOK session credential tokens:', error);
        }
    }

    function getProviderCredentialToken(provider) {
        const key = providerSecretKey(provider?.id);
        if (!key) return '';
        const record = normalizeCredentialTokenRecord(readProviderSessionCredentialTokens()[key]);
        if (!record || Date.parse(record.expires_at) <= Date.now()) {
            deleteProviderCredentialToken(key);
            return '';
        }
        return record.token;
    }

    function setProviderCredentialToken(providerId, token, expiresAt) {
        const key = providerSecretKey(providerId);
        const record = normalizeCredentialTokenRecord({ token, expires_at: expiresAt });
        if (!key || !record) return;
        const tokens = readProviderSessionCredentialTokens();
        tokens[key] = record;
        writeProviderSessionCredentialTokens(tokens);
    }

    function deleteProviderCredentialToken(providerId) {
        const key = providerSecretKey(providerId);
        if (!key) return;
        const tokens = readProviderSessionCredentialTokens();
        if (!Object.prototype.hasOwnProperty.call(tokens, key)) return;
        delete tokens[key];
        writeProviderSessionCredentialTokens(tokens);
    }

    /** Clear every BYOK browser credential during logout or account changes. */
    function clearProviderSessionCredentials() {
        providerMemoryCredentialTokens.clear();
        legacyProviderSecrets.clear();
        const storage = getSessionStorage();
        if (!storage) return;
        try {
            storage.removeItem(SESSION_CREDENTIAL_TOKENS_KEY);
            storage.removeItem(LEGACY_SESSION_SECRETS_KEY);
        } catch (error) {
            console.warn('Failed to clear BYOK session credentials:', error);
        }
    }

    /**
     * Move plaintext left by an older release into closure memory and remove
     * the Web Storage entry immediately. It is exchanged for sealed tokens
     * after authenticated API helpers become available.
     */
    function collectLegacySessionSecrets() {
        const storage = getSessionStorage();
        if (!storage) return;
        try {
            const parsed = safeJsonParse(storage.getItem(LEGACY_SESSION_SECRETS_KEY), {});
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                Object.entries(parsed).forEach(([providerId, value]) => {
                    const key = providerSecretKey(providerId);
                    const secret = String(value || '').trim();
                    if (key && secret) legacyProviderSecrets.set(key, secret);
                });
            }
        } catch (error) {
            console.warn('Failed to read legacy BYOK session credentials:', error);
        } finally {
            try {
                storage.removeItem(LEGACY_SESSION_SECRETS_KEY);
            } catch (error) {
                console.warn('Failed to remove legacy BYOK session credentials:', error);
            }
        }
    }

    function sanitizeProviderForStorage(provider) {
        if (!provider || typeof provider !== 'object') return provider;
        const sanitized = { ...provider };
        delete sanitized.api_key;
        return sanitized;
    }

    /** Remove request fields that Anthropic no longer supports on new models. */
    function sanitizeAnthropicModelSettings(providerType, settings) {
        if (!settings || typeof settings !== 'object' || Array.isArray(settings)) return settings;
        if (!['anthropic', 'anthropic_base'].includes(normalizeProviderType(providerType))) return settings;
        if (!ANTHROPIC_DEPRECATED_MODEL_SETTING_KEYS.some((key) => Object.prototype.hasOwnProperty.call(settings, key))) return settings;
        const sanitized = { ...settings };
        ANTHROPIC_DEPRECATED_MODEL_SETTING_KEYS.forEach((key) => delete sanitized[key]);
        return sanitized;
    }

    function sanitizeModelForStorage(model) {
        if (!model || typeof model !== 'object') return model;
        const settings = sanitizeAnthropicModelSettings(model.provider, model.settings);
        return settings === model.settings ? model : { ...model, settings };
    }

    function sanitizeDataForStorage(data) {
        const raw = data && typeof data === 'object' ? data : {};
        return {
            version: 1,
            providers: Array.isArray(raw.providers) ? raw.providers.map(sanitizeProviderForStorage) : [],
            models: Array.isArray(raw.models) ? raw.models.map(sanitizeModelForStorage) : [],
        };
    }

    function loadStorage() {
        collectLegacySessionSecrets();
        const fallback = {
            version: 1,
            providers: [],
            models: [],
        };
        const storage = getLocalStorage();
        if (!storage) {
            return fallback;
        }
        let parsed;
        try {
            parsed = safeJsonParse(storage.getItem(STORAGE_KEY), fallback);
        } catch (error) {
            console.warn('Failed to read BYOK storage:', error);
            return fallback;
        }
        if (!parsed || typeof parsed !== 'object') {
            return fallback;
        }
        const providers = Array.isArray(parsed.providers) ? parsed.providers : [];
        const rawModels = Array.isArray(parsed.models) ? parsed.models : [];
        const models = rawModels.map(sanitizeModelForStorage);
        const removedDeprecatedSettings = models.some((model, index) => model !== rawModels[index]);
        let strippedLegacySecret = false;
        providers.forEach((provider) => {
            if (!provider || typeof provider !== 'object') return;
            provider.provider = normalizeProviderType(provider.provider);
            if (provider.api_key) {
                const key = providerSecretKey(provider.id);
                const secret = String(provider.api_key || '').trim();
                if (key && secret) legacyProviderSecrets.set(key, secret);
                delete provider.api_key;
                strippedLegacySecret = true;
            }
        });

        models.forEach((model) => {
            if (!model || typeof model !== 'object') return;
            if (model.provider) {
                model.provider = normalizeProviderType(model.provider);
            }
        });

        const sanitized = {
            version: 1,
            providers,
            models,
        };
        if (strippedLegacySecret || removedDeprecatedSettings) {
            try {
                storage.setItem(STORAGE_KEY, JSON.stringify(sanitizeDataForStorage(sanitized)));
            } catch (error) {
                console.warn('Failed to sanitize BYOK local storage:', error);
            }
        }
        return sanitized;
    }

    function saveStorage() {
        const storage = getLocalStorage();
        if (!storage) {
            return;
        }
        try {
            storage.setItem(STORAGE_KEY, JSON.stringify(sanitizeDataForStorage(state.data)));
        } catch (error) {
            console.warn('Failed to save BYOK storage:', error);
        }
    }

    function getStoredSelectedModelId() {
        const storage = getLocalStorage();
        if (!storage) {
            return null;
        }
        try {
            const raw = storage.getItem(SELECTED_MODEL_KEY);
            return raw ? String(raw) : null;
        } catch (error) {
            console.warn('Failed to read selected model ID:', error);
            return null;
        }
    }

    function setStoredSelectedModelId(modelId) {
        const storage = getLocalStorage();
        if (!storage) {
            return;
        }
        try {
            if (!modelId) {
                storage.removeItem(SELECTED_MODEL_KEY);
                return;
            }
            storage.setItem(SELECTED_MODEL_KEY, String(modelId));
        } catch (error) {
            console.warn('Failed to save selected model ID:', error);
        }
    }

    function deepClone(value) {
        if (Array.isArray(value)) {
            return value.map((item) => deepClone(item));
        }
        if (value && typeof value === 'object') {
            return Object.keys(value).reduce((acc, key) => {
                acc[key] = deepClone(value[key]);
                return acc;
            }, {});
        }
        return value;
    }

    /**
     * Resolve the stable i18n metadata attached to backend-generated schemas.
     * Keep the response immutable because cached schemas can be rendered again
     * after the selected locale changes.
     */
    function localizeByokSchema(schema) {
        const localized = deepClone(schema && typeof schema === 'object' ? schema : { sections: [] });
        localized.sections = (Array.isArray(localized?.sections) ? localized.sections : []).map((section) => {
            const nextSection = { ...section };
            if (nextSection.i18n_title) {
                nextSection.title = byokT(nextSection.i18n_title, nextSection.title || '');
            }
            if (nextSection.i18n_description) {
                nextSection.description = byokT(nextSection.i18n_description, nextSection.description || '');
            }
            if (nextSection.i18n_group_title) {
                nextSection.group_title = byokT(nextSection.i18n_group_title, nextSection.group_title || '');
            }
            if (nextSection.i18n_group_description) {
                nextSection.group_description = byokT(
                    nextSection.i18n_group_description,
                    nextSection.group_description || '',
                );
            }
            nextSection.fields = (Array.isArray(nextSection.fields) ? nextSection.fields : []).map((field) => {
                const nextField = { ...field };
                if (nextField.i18n_label) {
                    nextField.label = byokT(nextField.i18n_label, nextField.label || nextField.key || '');
                }
                if (nextField.i18n_description) {
                    nextField.description = byokT(nextField.i18n_description, nextField.description || '');
                }
                if (nextField.i18n_placeholder) {
                    nextField.placeholder = byokT(nextField.i18n_placeholder, nextField.placeholder || '');
                }
                if (Array.isArray(nextField.options)) {
                    nextField.options = nextField.options.map((option) => {
                        if (!option || typeof option !== 'object' || !option.i18n_label) {
                            return option;
                        }
                        return {
                            ...option,
                            label: byokT(
                                option.i18n_label,
                                option.label || option.name || String(option.value ?? option.id ?? ''),
                            ),
                        };
                    });
                }
                return nextField;
            });
            return nextSection;
        });
        return localized;
    }

    function stripEmptyValues(value) {
        if (Array.isArray(value)) {
            return value
                .map((entry) => stripEmptyValues(entry))
                .filter((entry) => entry !== undefined);
        }
        if (value && typeof value === 'object') {
            return Object.entries(value).reduce((acc, [key, entry]) => {
                const cleaned = stripEmptyValues(entry);
                if (cleaned !== undefined) {
                    acc[key] = cleaned;
                }
                return acc;
            }, {});
        }
        if (typeof value === 'string' && !value.trim()) {
            return undefined;
        }
        return value;
    }

    function getNestedValue(source, dottedKey) {
        if (!source || typeof source !== 'object' || !dottedKey) return undefined;
        let cursor = source;
        const parts = String(dottedKey).split('.');
        for (const part of parts) {
            if (!cursor || typeof cursor !== 'object' || !(part in cursor)) {
                return undefined;
            }
            cursor = cursor[part];
        }
        return cursor;
    }

    function setNestedValue(target, dottedKey, value) {
        if (!target || typeof target !== 'object' || !dottedKey) return;
        const parts = String(dottedKey).split('.');
        let cursor = target;
        for (let i = 0; i < parts.length - 1; i += 1) {
            const part = parts[i];
            if (!cursor[part] || typeof cursor[part] !== 'object' || Array.isArray(cursor[part])) {
                cursor[part] = {};
            }
            cursor = cursor[part];
        }
        cursor[parts[parts.length - 1]] = value;
    }

    function trimString(value) {
        return typeof value === 'string' ? value.trim() : '';
    }

    function normalizeProviderUrl(value) {
        return trimString(value).replace(/\/+$/, '');
    }

    function sanitizeIconValue(value) {
        if (window.IconPicker?.sanitizeIconValue) {
            return window.IconPicker.sanitizeIconValue(value);
        }
        return trimString(value);
    }

    function getProviders() {
        return Array.isArray(state.data.providers) ? state.data.providers.slice() : [];
    }

    function getProviderById(providerId) {
        if (!providerId) return null;
        return getProviders().find((provider) => String(provider.id) === String(providerId)) || null;
    }

    function getLocalModels() {
        const base = Array.isArray(state.data.models) ? state.data.models.slice() : [];
        if (!state.allow) {
            return [];
        }
        return base
            .map((model) => {
                const provider = getProviderById(model.provider_instance_id);
                if (!provider) {
                    return null;
                }
                return {
                    ...deepClone(model),
                    is_byok: true,
                    provider: provider.provider,
                    provider_name: provider.name,
                    provider_icon: resolveProviderIcon(provider.provider, provider.icon),
                    provider_type: provider.provider,
                    provider_id: provider.id,
                    source_section: 'byok',
                    capabilities: Array.isArray(model.capabilities) ? model.capabilities : [],
                    tools: Array.isArray(model.tools) ? model.tools : [],
                    status: model.status || 'normal',
                };
            })
            .filter(Boolean);
    }

    function getLocalModelById(modelId) {
        if (!modelId) return null;
        return getLocalModels().find((model) => String(model.model_id) === String(modelId)) || null;
    }

    function isByokModelId(modelId) {
        return !!getLocalModelById(modelId);
    }

    function setAdminModels(models) {
        state.adminModels = Array.isArray(models) ? models.map((item) => ({ ...item })) : [];
        renderRoot();
    }

    function getAdminModels() {
        return Array.isArray(state.adminModels) ? state.adminModels.slice() : [];
    }

    function getAllSelectableModels(adminModels) {
        const normalizedAdmin = (Array.isArray(adminModels) ? adminModels : []).map((model) => ({
            ...model,
            is_byok: false,
            source_section: 'admin',
        }));
        const localModels = getLocalModels();
        return {
            adminModels: normalizedAdmin,
            byokModels: localModels,
            allModels: [...normalizedAdmin, ...localModels],
        };
    }

    async function fetchJson(url, options = {}) {
        const response = await window.authedFetch(url, options);
        if (!response.ok) {
            const payload = await response.json().catch(() => null);
            const detail = payload?.detail || payload?.message || `HTTP ${response.status}`;
            let code = detail && typeof detail === 'object' ? String(detail.code || '').trim() : '';
            const detailMessage = detail && typeof detail === 'object'
                ? (typeof detail.message === 'string' ? detail.message.trim() : '')
                : detail;
            // Older servers returned raw provider-authored English errors for
            // discovery. Reduce those responses to the stable localized error
            // contract used by current servers.
            if (String(url).includes('/api/v1/llm/models/byok')) {
                const stableDiscoveryCodes = new Set([
                    'byok_credential_unavailable',
                    'byok_provider_authentication_failed',
                    'byok_provider_configuration_invalid',
                    'byok_model_discovery_failed',
                ]);
                const legacyAuthenticationCodes = new Set([
                    'authentication_error',
                    'invalid_api_key',
                    'unauthorized',
                ]);
                if (legacyAuthenticationCodes.has(code) || response.status === 401 || response.status === 403) {
                    code = 'byok_provider_authentication_failed';
                } else if (!stableDiscoveryCodes.has(code)) {
                    code = 'byok_model_discovery_failed';
                }
            }
            const errorMessages = {
                byok_credential_unavailable: byokT(
                    'byok_credential_unavailable',
                    'Your saved BYOK credential is unavailable. Re-enter the API key.',
                ),
                byok_provider_authentication_failed: byokT(
                    'byok_provider_authentication_failed',
                    'The provider rejected the API credentials. Check the API key and try again.',
                ),
                byok_provider_configuration_invalid: byokT(
                    'byok_provider_configuration_invalid',
                    'The provider configuration is invalid. Check the connection settings and try again.',
                ),
                byok_model_discovery_failed: byokT(
                    'byok_model_discovery_failed',
                    'Models could not be loaded from the provider.',
                ),
            };
            const message = errorMessages[code] || detailMessage;
            const error = new Error(typeof message === 'string' ? message : `HTTP ${response.status}`);
            error.code = code;
            error.status = response.status;
            throw error;
        }
        return response.json();
    }

    /** Exchange a raw key for the only credential representation we persist. */
    async function issueProviderCredentialToken(providerId, providerType, apiKey) {
        const payload = await fetchJson('/api/v1/llm/byok/credential-token', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                provider: normalizeProviderType(providerType),
                provider_id: providerSecretKey(providerId),
                api_key: String(apiKey || '').trim(),
            }),
        });
        const token = String(payload?.credential_token || '').trim();
        const expiresAt = String(payload?.expires_at || '').trim();
        if (!token || !Number.isFinite(Date.parse(expiresAt))) {
            throw new Error(byokT(
                'byok_credential_unavailable',
                'Your saved BYOK credential is unavailable. Re-enter the API key.',
            ));
        }
        setProviderCredentialToken(providerId, token, expiresAt);
        return token;
    }

    /** Seal raw credentials collected from pre-token releases, then forget them. */
    async function migrateLegacyProviderSecrets() {
        if (!legacyProviderSecrets.size) return;
        const entries = Array.from(legacyProviderSecrets.entries());
        for (const [providerId, apiKey] of entries) {
            legacyProviderSecrets.delete(providerId);
            const provider = getProviderById(providerId);
            if (!provider) continue;
            try {
                await issueProviderCredentialToken(providerId, provider.provider, apiKey);
            } catch (error) {
                // A failed migration deliberately drops the plaintext value.
                // The editor will ask the user to enter the key again.
                console.warn('Failed to migrate a legacy BYOK credential:', error);
            }
        }
        if (state.domReady) renderProviderList();
    }

    async function fetchProviderSchema(providerKey) {
        return fetchJson(`/api/v1/llm/byok/provider-schema?provider=${encodeURIComponent(providerKey)}`);
    }

    async function getCachedProviderSchema(providerKey) {
        /** Load one provider schema per page session and deduplicate races. */
        const cacheKey = normalizeProviderType(providerKey);
        const cachedRequest = state.providerSchemaCache.get(cacheKey);
        if (cachedRequest) {
            return cachedRequest;
        }

        const pendingRequest = fetchProviderSchema(cacheKey).catch((error) => {
            // A transient failure must remain retryable the next time the user
            // opens the editor or chooses this provider.
            if (state.providerSchemaCache.get(cacheKey) === pendingRequest) {
                state.providerSchemaCache.delete(cacheKey);
            }
            throw error;
        });
        state.providerSchemaCache.set(cacheKey, pendingRequest);
        return pendingRequest;
    }

    function sanitizeProviderSchemaForByok(schema, providerKey) {
        const next = deepClone(schema && typeof schema === 'object' ? schema : { sections: [] });
        const providerExcluded = BYOK_PROVIDER_SPECIFIC_EXCLUDED_FIELDS[normalizeProviderType(providerKey)];
        next.sections = (Array.isArray(next.sections) ? next.sections : [])
            .map((section) => {
                const fields = (Array.isArray(section?.fields) ? section.fields : [])
                    .filter((field) => {
                        const key = trimString(field?.key);
                        return !field?.hide_on_byok
                            && !BYOK_PROVIDER_SCHEMA_EXCLUDED_FIELDS.has(key)
                            && !providerExcluded?.has(key);
                    });
                if (!fields.length) {
                    return null;
                }
                return {
                    ...section,
                    fields,
                };
            })
            .filter(Boolean);
        return next;
    }

    function extractProviderUrlSuggestions(schema) {
        const directSuggestions = schema?.[BYOK_SCHEMA_METADATA_KEY]?.[BYOK_BASE_URL_SUGGESTIONS_KEY];
        let rawSuggestions = Array.isArray(directSuggestions) ? directSuggestions : null;

        // Keep compatibility with older servers that attached suggestions to
        // the duplicate settings.base_url field before it was sanitized.
        const sections = Array.isArray(schema?.sections) ? schema.sections : [];
        if (!rawSuggestions) {
            for (const section of sections) {
                const fields = Array.isArray(section?.fields) ? section.fields : [];
                for (const field of fields) {
                    if (field?.key !== 'settings.base_url') continue;
                    const suggestions = field?.metadata?.[PROVIDER_URL_SUGGESTIONS_METADATA_KEY];
                    if (Array.isArray(suggestions)) {
                        rawSuggestions = suggestions;
                    }
                    break;
                }
                if (rawSuggestions) break;
            }
        }

        return (rawSuggestions || [])
            .map((entry) => {
                const name = trimString(entry?.name);
                const url = trimString(entry?.url);
                if (!name || !url) {
                    return null;
                }
                return { name, url };
            })
            .filter(Boolean);
    }

    function applyProviderBaseUrlSuggestions(suggestions = []) {
        const input = document.getElementById('byokProviderBaseUrl');
        const controlWrap = input?.closest('.byok-control-wrap');
        if (!input || !controlWrap) {
            return;
        }

        let select = controlWrap.querySelector('.byok-provider-url-suggestion');
        if (!Array.isArray(suggestions) || !suggestions.length) {
            if (select) {
                select._singleSelect?.wrapper?.remove();
                select.remove();
            }
            controlWrap.classList.remove('byok-provider-url-stack');
            input._providerUrlSuggestions = [];
            return;
        }

        if (!select) {
            select = document.createElement('select');
            select.className = 'input us byok-provider-url-suggestion';
            controlWrap.insertBefore(select, input);
        }
        select.setAttribute('aria-label', byokT('byok_provider_url_suggestion_label', 'Suggested provider URL'));

        input._providerUrlSuggestions = suggestions;
        controlWrap.classList.add('byok-provider-url-stack');

        const syncSelection = () => {
            const options = Array.isArray(input._providerUrlSuggestions) ? input._providerUrlSuggestions : [];
            const currentUrl = normalizeProviderUrl(input.value);
            const matched = options.find((entry) => normalizeProviderUrl(entry.url) === currentUrl);
            select.value = matched ? matched.url : CUSTOM_PROVIDER_URL_OPTION_VALUE;
        };

        if (select.dataset.bound !== 'true') {
            select.addEventListener('change', () => {
                const options = Array.isArray(input._providerUrlSuggestions) ? input._providerUrlSuggestions : [];
                if (select.value === CUSTOM_PROVIDER_URL_OPTION_VALUE) {
                    syncSelection();
                    return;
                }
                const matched = options.find((entry) => entry.url === select.value);
                if (!matched) {
                    syncSelection();
                    return;
                }
                if (input.value !== matched.url) {
                    input.value = matched.url;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                } else {
                    syncSelection();
                }
            });
            select.dataset.bound = 'true';
        }

        if (input.dataset.providerUrlSuggestionsBound !== 'true') {
            input.addEventListener('input', syncSelection);
            input.addEventListener('change', syncSelection);
            input.dataset.providerUrlSuggestionsBound = 'true';
        }

        select.innerHTML = '';

        const customOption = document.createElement('option');
        customOption.value = CUSTOM_PROVIDER_URL_OPTION_VALUE;
        customOption.textContent = byokT('byok_provider_url_custom', 'Custom');
        select.appendChild(customOption);

        suggestions.forEach((suggestion) => {
            const option = document.createElement('option');
            option.value = suggestion.url;
            option.textContent = suggestion.name;
            select.appendChild(option);
        });

        input._providerUrlSuggestionSync = syncSelection;
        syncSelection();
        if (select._singleSelect) {
            syncEnhancedSelect(select, { refreshOptions: true });
        } else {
            enhanceByokSelect(select, 'byok_provider_url_suggestion');
        }

        // The shared single-select helper appends its visible wrapper to the
        // end of the parent. Put it back before the manual URL field so users
        // choose a suggested endpoint before optionally editing its value.
        const enhancedSelect = select._singleSelect?.wrapper;
        if (enhancedSelect) {
            controlWrap.insertBefore(enhancedSelect, input);
        }
    }

    function isIconSchemaField(field) {
        const key = String(field?.key || '').toLowerCase();
        if (!key) {
            return false;
        }
        if (window.IconPicker?.shouldUseIconPicker?.(field)) {
            return true;
        }
        return key === 'icon'
            || key === 'provider_icon'
            || key.endsWith('.icon')
            || key.endsWith('.provider_icon');
    }

    function getIconPresetTypeForField(field) {
        const key = String(field?.key || '').toLowerCase();
        if (key === 'icon' || key === 'provider_icon' || key.endsWith('.icon') || key.endsWith('.provider_icon')) {
            return 'provider';
        }
        return 'model';
    }

    function createIconPickerControl(field, initialValue) {
        const wrapper = document.createElement('div');
        wrapper.className = 'byok-icon-picker-host';
        const hiddenInput = document.createElement('input');
        hiddenInput.type = 'hidden';
        hiddenInput.dataset.fieldKey = field.key;

        const syncValue = (nextValue, emitEvents = false) => {
            hiddenInput.value = sanitizeIconValue(nextValue);
            if (emitEvents) {
                hiddenInput.dispatchEvent(new Event('input', { bubbles: true }));
                hiddenInput.dispatchEvent(new Event('change', { bubbles: true }));
            }
        };

        if (window.IconPicker?.createIconPicker) {
            const picker = window.IconPicker.createIconPicker({
                value: initialValue,
                presetType: getIconPresetTypeForField(field),
                onChange: (newValue) => syncValue(newValue, true),
            });
            wrapper.appendChild(picker.container);
            // The picker normalizes unsupported preset keys. Persist its
            // canonical value so the visible selection and saved value agree.
            syncValue(picker.getValue());
        } else {
            hiddenInput.type = 'text';
            hiddenInput.className = 'input us';
            hiddenInput.placeholder = getIconPresetTypeForField(field) === 'provider' ? 'openai' : 'sparkles';
            syncValue(initialValue);
            wrapper.appendChild(hiddenInput);
        }

        if (hiddenInput.type === 'hidden') {
            wrapper.appendChild(hiddenInput);
        }

        return { wrapper, control: hiddenInput };
    }

    function renderStoredIconMarkup(iconValue, { fallback = '', imageAlt = 'Icon' } = {}) {
        if (window.IconPicker?.renderIconMarkup) {
            return window.IconPicker.renderIconMarkup(iconValue, { fallback, imageAlt });
        }
        const trimmed = trimString(iconValue);
        if (!trimmed) {
            return fallback;
        }
        if (trimmed.startsWith('<')) {
            return trimmed;
        }
        return Icons?.[trimmed] || fallback;
    }

    async function fetchModelSchemaPayload({ provider, modelName = null, modelInfo = null, tools = [], modelProvider = null }) {
        return fetchJson('/api/v1/llm/byok/model-schema', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                provider,
                model_name: modelName,
                model_info: modelInfo,
                tools,
                model_provider: modelProvider,
            }),
        });
    }

    async function fetchRemoteModelsForProvider(providerId) {
        const provider = getProviderById(providerId);
        if (!provider) {
            throw new Error(byokT('byok_select_provider_first', 'Select a provider instance first.'));
        }
        const cleanedSettings = stripEmptyValues(provider.settings || {});
        const payload = {
            provider: provider.provider,
            provider_id: provider.id,
            credential_token: getProviderCredentialToken(provider) || undefined,
            config: {
                base_url: provider.base_url || undefined,
                ...cleanedSettings,
            },
        };
        const models = await fetchJson('/api/v1/llm/models/byok', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });
        return Array.isArray(models) ? models.slice(0, REMOTE_MODELS_LIMIT) : [];
    }

    function createFormContext(container) {
        return {
            container,
            controls: new Map(),
        };
    }

    function getFieldMetaType(field) {
        const inputType = String(field?.input_type || '').toLowerCase();
        const type = String(field?.type || '').toLowerCase();
        return inputType || type;
    }

    function parseListValue(raw) {
        if (!raw) return [];
        return String(raw)
            .split(/\r?\n|,/g)
            .map((entry) => entry.trim())
            .filter(Boolean);
    }

    function applyControlValue(control, field, value) {
        if (!control) return;
        const metaType = getFieldMetaType(field);
        if (control.type === 'checkbox') {
            control.checked = Boolean(value);
            return;
        }
        if (control.tagName === 'SELECT' && control.multiple) {
            const selected = new Set(Array.isArray(value) ? value.map((item) => String(item)) : []);
            Array.from(control.options).forEach((option) => {
                option.selected = selected.has(option.value);
            });
            return;
        }
        if (metaType === 'list[str]' && Array.isArray(value)) {
            control.value = value.join('\n');
            return;
        }
        control.value = value == null ? '' : String(value);
    }

    function extractControlValue(control, field) {
        if (!control) return undefined;
        const metaType = getFieldMetaType(field);
        if (control.type === 'checkbox') {
            return Boolean(control.checked);
        }
        if (control.tagName === 'SELECT' && control.multiple) {
            const values = Array.from(control.selectedOptions)
                .map((option) => option.value)
                .filter(Boolean);
            return values.length ? values : [];
        }
        const raw = control.value;
        if (metaType === 'list[str]') {
            return parseListValue(raw);
        }
        if (!String(raw || '').trim()) {
            if (metaType === 'int' || metaType === 'float') {
                return undefined;
            }
            return '';
        }
        if (metaType === 'int') {
            const parsed = Number.parseInt(raw, 10);
            return Number.isNaN(parsed) ? undefined : parsed;
        }
        if (metaType === 'float') {
            const parsed = Number.parseFloat(raw);
            return Number.isNaN(parsed) ? undefined : parsed;
        }
        return raw;
    }

    function getDependencyValue(context, dependencyKey) {
        const entry = context.controls.get(dependencyKey);
        if (!entry) return undefined;
        return extractControlValue(entry.control, entry.field);
    }

    function dependencySatisfied(context, field) {
        if (!field?.dependency) return true;
        if (!context.controls.has(field.dependency)) return true;
        const current = getDependencyValue(context, field.dependency);
        const required = field.dependency_value;
        if (Array.isArray(current)) {
            if (Array.isArray(required)) {
                return required.some((item) => current.includes(String(item)));
            }
            return current.includes(String(required));
        }
        if (Array.isArray(required)) {
            return required.includes(String(current));
        }
        if (typeof required === 'boolean') {
            return current === required;
        }
        return String(current) === String(required);
    }

    function updateDependencyVisibility(context) {
        context.controls.forEach(({ field, row }) => {
            if (!field?.dependency || !row) return;
            const visible = dependencySatisfied(context, field);
            row.style.display = visible ? '' : 'none';
        });
    }

    function createControl(field) {
        const metaType = getFieldMetaType(field);
        const options = Array.isArray(field?.options) ? field.options : [];
        const isMulti = metaType === 'list[str]' && options.length > 0;

        if (String(field?.type || '').toLowerCase() === 'boolean' || metaType === 'bool' || metaType === 'boolean') {
            const input = document.createElement('input');
            input.type = 'checkbox';
            input.required = Boolean(field?.required);
            return input;
        }

        if (options.length) {
            const select = document.createElement('select');
            select.className = 'input us';
            if (isMulti) {
                select.multiple = true;
                select.size = Math.min(Math.max(options.length, 3), 8);
            }
            select.required = Boolean(field?.required);
            if (!isMulti) {
                const empty = document.createElement('option');
                empty.value = '';
                empty.textContent = byokT('byok_select_placeholder', 'Select');
                select.appendChild(empty);
            }
            options.forEach((option) => {
                const entry = document.createElement('option');
                if (option && typeof option === 'object') {
                    entry.value = String(option.value ?? option.id ?? '');
                    entry.textContent = String(option.label ?? option.name ?? option.value ?? option.id ?? '');
                } else {
                    entry.value = String(option ?? '');
                    entry.textContent = String(option ?? '');
                }
                select.appendChild(entry);
            });
            return select;
        }

        if (metaType === 'list[str]' || String(field?.type || '').toLowerCase() === 'textarea') {
            const textarea = document.createElement('textarea');
            textarea.className = 'input us';
            textarea.rows = 4;
            textarea.required = Boolean(field?.required);
            if (field?.placeholder) textarea.placeholder = field.placeholder;
            if (field?.max_length !== undefined && field.max_length !== null && Number.isFinite(Number(field.max_length))) {
                textarea.maxLength = Number(field.max_length);
            }
            return textarea;
        }

        const input = document.createElement('input');
        input.className = 'input us';
        input.required = Boolean(field?.required);
        if (field?.placeholder) input.placeholder = field.placeholder;
        if (field?.max_length !== undefined && field.max_length !== null && Number.isFinite(Number(field.max_length))) {
            input.maxLength = Number(field.max_length);
        }
        if (metaType === 'int' || metaType === 'float') {
            input.type = 'number';
            const attributes = field?.attributes && typeof field.attributes === 'object'
                ? field.attributes
                : {};
            if (attributes.min !== undefined && attributes.min !== null) input.min = String(attributes.min);
            if (attributes.max !== undefined && attributes.max !== null) input.max = String(attributes.max);
            if (attributes.step !== undefined && attributes.step !== null) {
                input.step = String(attributes.step);
            } else if (metaType === 'float') {
                input.step = 'any';
            }
        } else {
            input.type = 'text';
        }
        return input;
    }

    function renderSchemaFields(host, schema, values = {}) {
        if (!host) return null;
        host.innerHTML = '';
        const localizedSchema = localizeByokSchema(schema);
        const sections = Array.isArray(localizedSchema?.sections) ? localizedSchema.sections : [];
        const context = createFormContext(host);

        if (!sections.length) {
            const empty = document.createElement('p');
            empty.className = 'provider-form-empty';
            empty.textContent = byokT('byok_no_extra_settings', 'No extra settings required.');
            host.appendChild(empty);
            return context;
        }

        const dependencyKeys = new Set();

        sections.forEach((section) => {
            if (!section || !Array.isArray(section.fields) || !section.fields.length) return;
            const sectionEl = document.createElement('section');
            sectionEl.className = 'us-settings-section';

            if (section.title || section.description) {
                const heading = document.createElement('div');
                heading.className = 'byok-section-heading';
                if (section.title) {
                    const title = document.createElement('h2');
                    title.className = 'us-section-title';
                    title.textContent = section.title;
                    heading.appendChild(title);
                }
                if (section.description) {
                    const desc = document.createElement('p');
                    desc.className = 'us-section-description';
                    desc.textContent = section.description;
                    heading.appendChild(desc);
                }
                sectionEl.appendChild(heading);
            }

            section.fields.forEach((field) => {
                if (!field?.key) return;
                if (field?.hide_on_byok) return;
                const row = document.createElement('div');
                row.className = 'us-setting-item byok-setting-item';

                const info = document.createElement('div');
                info.className = 'us-setting-info';
                const title = document.createElement('h3');
                const fieldId = `${host.id || 'byok-schema'}-${String(field.key).replace(/[^a-zA-Z0-9_-]/g, '-')}`;
                title.id = `${fieldId}-label`;
                title.textContent = field.label || field.key;
                info.appendChild(title);
                let descriptionId = '';
                if (field.description) {
                    const desc = document.createElement('p');
                    descriptionId = `${fieldId}-description`;
                    desc.id = descriptionId;
                    desc.textContent = field.description;
                    info.appendChild(desc);
                }

                const controlWrap = document.createElement('div');
                controlWrap.className = 'byok-control-wrap';
                const resolvedValue = getNestedValue(values, field.key);
                const initialValue = resolvedValue !== undefined
                    ? resolvedValue
                    : (field.value !== undefined ? field.value : field.default);
                let control;

                if (isIconSchemaField(field)) {
                    const iconControl = createIconPickerControl(field, initialValue);
                    control = iconControl.control;
                    controlWrap.appendChild(iconControl.wrapper);
                } else {
                    control = createControl(field);
                    applyControlValue(control, field, initialValue);
                    control.dataset.fieldKey = field.key;
                    controlWrap.appendChild(control);
                }

                control.setAttribute('aria-labelledby', title.id);
                if (descriptionId) {
                    control.setAttribute('aria-describedby', descriptionId);
                }

                if (control.type === 'checkbox') {
                    // Toggle rows need their own layout hook because their compact
                    // control should align to the full height of the text column.
                    row.classList.add('byok-toggle-setting');
                    controlWrap.classList.add('byok-toggle-control');
                    const toggle = document.createElement('label');
                    toggle.className = 'toggle-switch';
                    toggle.appendChild(control);
                    const slider = document.createElement('span');
                    slider.className = 'toggle-slider';
                    toggle.appendChild(slider);
                    controlWrap.innerHTML = '';
                    controlWrap.appendChild(toggle);
                }

                row.appendChild(info);
                row.appendChild(controlWrap);
                sectionEl.appendChild(row);

                context.controls.set(field.key, { field, row, control });
                if (control instanceof HTMLSelectElement && !control.multiple) {
                    enhanceByokSelect(control, `byok_schema_${fieldId}`);
                }
                if (field.dependency) {
                    dependencyKeys.add(field.dependency);
                }
            });

            host.appendChild(sectionEl);
        });

        dependencyKeys.forEach((dependencyKey) => {
            const dependency = context.controls.get(dependencyKey);
            if (!dependency?.control) return;
            dependency.control.addEventListener('change', () => updateDependencyVisibility(context));
            dependency.control.addEventListener('input', () => updateDependencyVisibility(context));
        });
        updateDependencyVisibility(context);

        return context;
    }

    function collectContextValues(context) {
        const result = {};
        if (!context || !context.controls) {
            return result;
        }
        context.controls.forEach(({ field, row, control }, key) => {
            if (row && row.style.display === 'none') {
                return;
            }
            const value = extractControlValue(control, field);
            if (value === undefined) {
                return;
            }
            setNestedValue(result, key, value);
        });
        return result;
    }

    function applySchemaValues(schema, values) {
        const next = deepClone(schema);
        const sections = Array.isArray(next?.sections) ? next.sections : [];
        sections.forEach((section) => {
            const fields = Array.isArray(section?.fields) ? section.fields : [];
            fields.forEach((field) => {
                const value = getNestedValue(values, field.key);
                if (value === undefined) return;
                field.value = deepClone(value);
                if (String(field.type || '').toLowerCase() === 'boolean') {
                    field.default = Boolean(value);
                }
            });
        });
        return next;
    }

    function stripRuntimeSchemaFields(schema) {
        const next = deepClone(schema);
        const blocked = new Set([
            'name',
            'description',
            'model_icon',
            'model_name',
            'tools',
            'status',
        ]);
        next.sections = (Array.isArray(next.sections) ? next.sections : [])
            .map((section) => {
                const fields = (Array.isArray(section?.fields) ? section.fields : [])
                    .filter((field) => !blocked.has(String(field?.key || '')));
                if (!fields.length) {
                    return null;
                }
                return {
                    ...section,
                    fields,
                };
            })
            .filter(Boolean);
        return next;
    }

    function renderModelSchemaPlaceholder(message) {
        if (!state.modelSettingsHost) {
            return;
        }
        state.modelSettingsHost.innerHTML = '';
        const empty = document.createElement('p');
        empty.className = 'provider-form-empty';
        empty.textContent = message;
        state.modelSettingsHost.appendChild(empty);
        state.modelFormContext = createFormContext(state.modelSettingsHost);
    }

    function getByokNavItem() {
        return document.getElementById('byokNavItem');
    }

    function getByokPage() {
        return document.getElementById('byokSettingsPage');
    }

    function getByokRoot() {
        return document.getElementById('byokSettingsRoot');
    }

    function applyVisibility() {
        const nav = getByokNavItem();
        const page = getByokPage();
        const visible = state.allow;
        if (nav) {
            nav.style.display = visible ? '' : 'none';
        }
        if (page) {
            page.style.display = visible ? '' : 'none';
            if (!visible && page.classList.contains('active') && typeof window.setUserSettingsActiveSection === 'function') {
                window.setUserSettingsActiveSection('profile');
            }
        }
    }

    function notifyModelChange({ rerender = true } = {}) {
        if (typeof window !== 'undefined') {
            window.dispatchEvent(new CustomEvent('byok:modelsChanged'));
        }
        if (rerender) renderRoot();
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function getByokElements() {
        return {
            providerOverlay: document.getElementById('byokProviderOverlay'),
            providerTitle: document.getElementById('byokProviderEditorTitle'),
            providerSubtitle: document.getElementById('byokProviderEditorSubtitle'),
            providerClose: document.getElementById('byokProviderCloseButton'),
            providerSave: document.getElementById('byokProviderSaveButton'),
            providerSaveLabel: document.getElementById('byokProviderSaveButtonLabel'),
            providerType: document.getElementById('byokProviderType'),
            providerName: document.getElementById('byokProviderName'),
            providerBaseUrl: document.getElementById('byokProviderBaseUrl'),
            providerApiKey: document.getElementById('byokProviderApiKey'),
            providerSchemaHost: document.getElementById('byokProviderSettingsFields'),
            providerList: document.getElementById('byokProviderList'),
            modelOverlay: document.getElementById('byokModelOverlay'),
            modelTitle: document.getElementById('byokModelEditorTitle'),
            modelSubtitle: document.getElementById('byokModelEditorSubtitle'),
            modelClose: document.getElementById('byokModelCloseButton'),
            modelSave: document.getElementById('byokModelSaveButton'),
            modelSaveLabel: document.getElementById('byokModelSaveButtonLabel'),
            modelProviderInstance: document.getElementById('byokModelProviderInstance'),
            modelRemoteSelect: document.getElementById('byokRemoteModelSelect'),
            modelRemoteStatus: document.getElementById('byokRemoteModelStatus'),
            modelSchemaHost: document.getElementById('byokModelSettingsFields'),
            modelList: document.getElementById('byokModelList'),
            dialogOverlay: document.getElementById('byokDialogOverlay'),
            dialogCard: document.getElementById('byokDialogCard'),
            dialogIcon: document.getElementById('byokDialogIcon'),
            dialogTitle: document.getElementById('byokDialogTitle'),
            dialogDescription: document.getElementById('byokDialogDescription'),
            dialogConfirm: document.getElementById('byokDialogConfirmButton'),
            dialogCancel: document.getElementById('byokDialogCancelButton'),
        };
    }

    function rememberFocus() {
        state.lastFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }

    function restoreFocus() {
        if (state.lastFocusedElement && typeof state.lastFocusedElement.focus === 'function') {
            state.lastFocusedElement.focus();
        }
        state.lastFocusedElement = null;
    }

    /**
     * Keep the page behind a BYOK modal stationary. This is recalculated from
     * the DOM so a confirmation dialog opened above an editor cannot
     * accidentally unlock background scrolling when only one surface closes.
     */
    function syncModalBodyState() {
        const hasOpenModal = [
            'byokProviderOverlay',
            'byokModelOverlay',
            'byokDialogOverlay',
        ].some((id) => {
            const overlay = document.getElementById(id);
            return overlay && !overlay.hidden;
        });
        document.body.classList.toggle('byok-modal-open', hasOpenModal);
    }

    function setHiddenState(element, visible) {
        if (!element) return;
        if (visible) {
            element.removeAttribute('hidden');
            element.setAttribute('aria-hidden', 'false');
        } else {
            element.setAttribute('hidden', '');
            element.setAttribute('aria-hidden', 'true');
        }
        syncModalBodyState();
    }

    function getTopByokOverlay() {
        const elements = getByokElements();
        if (state.dialogOpen) return elements.dialogOverlay;
        if (state.modelModalOpen) return elements.modelOverlay;
        if (state.providerModalOpen) return elements.providerOverlay;
        return null;
    }

    function getVisibleFocusableElements(root) {
        if (!root) return [];
        return Array.from(root.querySelectorAll(
            'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), details > summary, [tabindex]:not([tabindex="-1"])'
        )).filter((element) => (
            element.tabIndex >= 0
            && element.getAttribute('aria-hidden') !== 'true'
            && !element.closest('.admin-select-menu:not(.open)')
            && element.offsetParent !== null
        ));
    }

    /**
     * Trap keyboard focus inside the topmost BYOK surface. The shared Escape
     * registry still owns dismissal ordering; this handler only supplies the
     * missing Tab/Shift+Tab containment expected from an aria-modal dialog.
     */
    function trapByokModalFocus(event) {
        if (event.key !== 'Tab') return;
        const overlay = getTopByokOverlay();
        if (!overlay || overlay.hidden) return;
        const focusable = getVisibleFocusableElements(overlay);
        if (!focusable.length) {
            event.preventDefault();
            overlay.querySelector('[role="dialog"]')?.focus();
            return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first || !overlay.contains(document.activeElement))) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && (document.activeElement === last || !overlay.contains(document.activeElement))) {
            event.preventDefault();
            first.focus();
        }
    }

    function syncEnhancedSelect(select, { refreshOptions = false } = {}) {
        const meta = select?._singleSelect;
        if (!select || !meta) return;
        if (refreshOptions) {
            meta.refreshOptions?.();
        }
        meta.syncFromSelect?.();
        // Options are reached with arrow keys after opening the listbox. They
        // must not interrupt normal Tab navigation while the menu is closed.
        meta.wrapper?.querySelectorAll('.admin-select-option').forEach((option) => {
            option.tabIndex = -1;
        });
        const trigger = meta.wrapper?.querySelector('.admin-select-trigger');
        if (!trigger) return;
        trigger.disabled = Boolean(select.disabled);
        trigger.setAttribute('aria-disabled', String(Boolean(select.disabled)));
        // The shared select stores its placeholder at initialization time, but
        // BYOK updates empty options while providers and models are loading.
        // Mirror the current native option so the visible trigger always
        // communicates the real state (for example, "Loading models...").
        if (String(select.value) === '') {
            const selectedOption = select.selectedOptions?.[0];
            const selectedText = selectedOption?.textContent?.trim();
            const value = trigger.querySelector('.admin-select-value');
            if (value && selectedText) {
                value.textContent = selectedText;
                value.classList.add('placeholder');
            }
        }
        ['aria-labelledby', 'aria-describedby', 'aria-invalid'].forEach((name) => {
            if (select.hasAttribute(name)) {
                trigger.setAttribute(name, select.getAttribute(name));
            }
        });
    }

    /** Enhance a native BYOK select with the shared settings listbox. */
    function enhanceByokSelect(select, key, options = {}) {
        if (!select || typeof window.upgradeAdminSingleSelect !== 'function') return;
        const emptyOption = Array.from(select.options || []).find((option) => option.value === '');
        window.upgradeAdminSingleSelect(select, {
            key,
            placeholder: emptyOption?.textContent?.trim() || '',
            ...options,
        });
        syncEnhancedSelect(select);
    }

    function focusByokControl(control) {
        if (!control) return;
        const enhancedTrigger = control._singleSelect?.wrapper?.querySelector('.admin-select-trigger');
        (enhancedTrigger || control).focus?.();
    }

    function clearByokControlError(control) {
        if (!control) return;
        control.classList.remove('input-error');
        control.setAttribute('aria-invalid', 'false');
        const enhancedTrigger = control._singleSelect?.wrapper?.querySelector('.admin-select-trigger');
        enhancedTrigger?.classList.remove('input-error');
        enhancedTrigger?.setAttribute('aria-invalid', 'false');
    }

    function reportByokControlError(control, message) {
        if (control) {
            control.classList.add('input-error');
            control.setAttribute('aria-invalid', 'true');
            const enhancedTrigger = control._singleSelect?.wrapper?.querySelector('.admin-select-trigger');
            enhancedTrigger?.classList.add('input-error');
            enhancedTrigger?.setAttribute('aria-invalid', 'true');
            focusByokControl(control);
        }
        notifyError(message);
    }

    function openOverlay(key) {
        const elements = getByokElements();
        const overlay = elements[key];
        if (!overlay) return;
        rememberFocus();
        setHiddenState(overlay, true);
    }

    function closeOverlay(key, restore = true) {
        const elements = getByokElements();
        const overlay = elements[key];
        if (!overlay) return;
        setHiddenState(overlay, false);
        if (restore) {
            restoreFocus();
        }
    }

    function updateRemoteModelSelect(selectedModelName = '') {
        const elements = getByokElements();
        const select = elements.modelRemoteSelect;
        const status = elements.modelRemoteStatus;
        if (!select) return;

        const providerId = trimString(document.getElementById('byokModelProviderInstance')?.value);
        const preferredModelName = trimString(selectedModelName);
        const appendOption = (value, label, { disabled = false } = {}) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            option.disabled = disabled;
            select.appendChild(option);
        };

        select.innerHTML = '';
        select.disabled = true;

        let statusText = byokT('byok_remote_select_provider_status', 'Select a provider instance to load remote models automatically.');

        if (!providerId) {
            appendOption('', byokT('byok_remote_select_provider_first', 'Select a provider instance first'), { disabled: true });
        } else if (state.remoteModelsLoading) {
            appendOption('', byokT('byok_remote_loading_models', 'Loading models...'), { disabled: true });
            statusText = byokT('byok_remote_loading_status', 'Loading the remote model list automatically...');
        } else if (state.remoteModels.length) {
            const modelIds = new Set();
            appendOption('', byokT('byok_remote_select_model', 'Select a remote model'));
            state.remoteModels.forEach((model) => {
                const value = getRemoteModelIdentifier(model);
                if (!value) return;
                modelIds.add(value);
                appendOption(
                    value,
                    model.name
                    || model.label
                    || model.title
                    || model.model_name
                    || model.model
                    || model.id
                    || value
                );
            });
            if (preferredModelName && !modelIds.has(preferredModelName)) {
                appendOption(
                    preferredModelName,
                    formatTranslation('byok_remote_current_model_option', '{name} (current)', { name: preferredModelName }),
                );
            }
            select.disabled = false;
            if (preferredModelName) {
                select.value = preferredModelName;
            }
            statusText = formatTranslation(
                state.remoteModels.length === 1 ? 'byok_remote_loaded_count_one' : 'byok_remote_loaded_count_other',
                state.remoteModels.length === 1 ? '{count} remote model loaded automatically.' : '{count} remote models loaded automatically.',
                { count: state.remoteModels.length },
            );
            if (preferredModelName && !modelIds.has(preferredModelName)) {
                statusText = `${statusText} ${byokT('byok_remote_saved_model_missing', 'The saved model is not in the latest provider response.')}`;
            }
        } else if (state.remoteModelsManualMode) {
            appendOption('', byokT('byok_remote_manual_mode_enabled', 'Manual mode enabled'), { disabled: true });
            statusText = resolveTranslationRef(state.remoteModelsStatus)
                || byokT('byok_remote_discovery_unavailable_manual', 'Remote discovery was unavailable. Enter the model name manually below.');
        } else {
            appendOption('', byokT('byok_remote_models_unavailable', 'Remote models unavailable'), { disabled: true });
            statusText = byokT('byok_remote_will_load_after_provider', 'Remote models will load automatically after you choose a provider instance.');
        }

        if (status) {
            status.textContent = statusText;
        }
        syncEnhancedSelect(select, { refreshOptions: true });
    }

    function getRemoteModelIdentifier(model) {
        return String(
            model?.id
            || model?.model_id
            || model?.model_name
            || model?.model
            || model?.name
            || ''
        ).trim();
    }

    function getRemoteModelMatch(modelName) {
        const candidate = trimString(modelName);
        if (!candidate) {
            return null;
        }
        return state.remoteModels.find((model) => getRemoteModelIdentifier(model) === candidate) || null;
    }

    function buildRemoteModelPrefill(provider, remoteMatch, modelName = '') {
        const normalizedModelName = trimString(modelName);
        const remoteId = getRemoteModelIdentifier(remoteMatch);
        const resolvedModelName = remoteId || normalizedModelName;
        const resolvedName = trimString(
            remoteMatch?.name
            || remoteMatch?.label
            || remoteMatch?.title
            || remoteMatch?.display_name
            || ''
        );
        const resolvedDescription = normalizeModelDescription(
            remoteMatch?.description
            || remoteMatch?.summary
            || remoteMatch?.short_description
            || remoteMatch?.details
            || ''
        );
        const resolvedIcon = sanitizeIconValue(
            remoteMatch?.model_icon
            || remoteMatch?.icon
            || resolveProviderIcon(provider?.provider, provider?.icon)
            || ''
        );
        return {
            model_name: resolvedModelName,
            name: resolvedName,
            description: resolvedDescription,
            model_icon: resolvedIcon,
            status: 'normal',
        };
    }

    function showDialog({
        title,
        description,
        confirmLabel = translationRef('byok_action_confirm', 'Confirm'),
        cancelLabel = translationRef('byok_action_cancel', 'Cancel'),
        variant = 'danger',
    }) {
        const elements = getByokElements();
        if (!elements.dialogOverlay || !elements.dialogConfirm || !elements.dialogCancel) {
            return Promise.resolve(false);
        }

        if (state.dialogResolver) {
            state.dialogResolver(false);
            state.dialogResolver = null;
        }

        state.dialogConfig = { title, description, confirmLabel, cancelLabel, variant };
        state.dialogVariant = variant;
        state.dialogOpen = true;

        renderDialog();
        rememberFocus();
        setHiddenState(elements.dialogOverlay, true);

        requestAnimationFrame(() => {
            // Destructive dialogs default to the safe action; affirmative
            // consent dialogs lead with the action the user just requested.
            (variant === 'danger' ? elements.dialogCancel : elements.dialogConfirm)?.focus();
        });

        return new Promise((resolve) => {
            state.dialogResolver = resolve;
        });
    }

    function renderDialog() {
        const elements = getByokElements();
        const config = state.dialogConfig;
        if (!config) return;
        const title = resolveTranslationRef(config.title)
            || byokT('byok_dialog_confirm_action', 'Confirm action');
        const description = resolveTranslationRef(config.description);
        const confirmLabel = resolveTranslationRef(config.confirmLabel);
        const cancelLabel = resolveTranslationRef(config.cancelLabel);
        const variant = config.variant || 'danger';

        if (elements.dialogTitle) elements.dialogTitle.textContent = title;
        if (elements.dialogDescription) elements.dialogDescription.textContent = description;
        if (elements.dialogConfirm) {
            elements.dialogConfirm.textContent = confirmLabel;
            elements.dialogConfirm.classList.remove('cancel', 'confirm');
            elements.dialogConfirm.classList.toggle('danger', variant === 'danger');
            elements.dialogConfirm.classList.toggle('submit', variant !== 'danger');
        }
        if (elements.dialogCancel) {
            elements.dialogCancel.textContent = cancelLabel;
        }
        if (elements.dialogCard) {
            elements.dialogCard.classList.toggle('delete-warning-card-wide', variant === 'info');
            elements.dialogCard.classList.toggle('byok-dialog-card-info', variant === 'info');
        }
        if (elements.dialogIcon) {
            elements.dialogIcon.innerHTML = variant === 'info' ? (Icons.info || Icons.warning) : Icons.warning;
        }
    }

    function closeDialog(result = false) {
        const elements = getByokElements();
        state.dialogOpen = false;
        setHiddenState(elements.dialogOverlay, false);
        if (state.dialogResolver) {
            state.dialogResolver(Boolean(result));
            state.dialogResolver = null;
        }
        restoreFocus();
    }

    function closeTopSurface() {
        if (state.dialogOpen) {
            closeDialog(false);
            return;
        }
        if (state.modelModalOpen) {
            closeModelModal();
            return;
        }
        if (state.providerModalOpen) {
            closeProviderModal();
        }
    }

    function formatInteger(value) {
        const parsed = Number(value || 0);
        if (!Number.isFinite(parsed)) return '0';
        return Math.round(parsed).toLocaleString();
    }

    function formatPercent(value) {
        const parsed = Number(value || 0);
        if (!Number.isFinite(parsed)) return '0%';
        return `${parsed.toFixed(1)}%`;
    }

    function formatCurrency(value) {
        const parsed = Number(value || 0);
        if (!Number.isFinite(parsed)) return '$0.000000';
        return `$${parsed.toFixed(6)}`;
    }

    function formatDateTime(value) {
        if (!value) return 'Unknown';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return date.toLocaleString();
    }

    function resetByokStatsData() {
        state.byokStatsLoadedDays = null;
        state.byokStatsError = '';
        state.byokStatsLoading = false;
        state.byokStats = {
            overview: null,
            providers: [],
            models: [],
            errors: [],
            toolOverview: null,
            tools: [],
        };
    }

    function showByokStatsConsentModal() {
        const retentionDays = Number.isFinite(Number(state.byokStatisticsRetentionDays))
            ? Math.max(1, Math.round(Number(state.byokStatisticsRetentionDays)))
            : 90;
        return showDialog({
            title: translationRef('byok_stats_consent_title', 'Enable BYOK Usage Statistics'),
            description: translationRef(
                'byok_stats_consent_desc',
                'Enabling this stores BYOK request metadata, token usage, estimated costs, and redacted provider errors for your account. Records are automatically deleted after {days} days.',
                { days: retentionDays },
            ),
            confirmLabel: translationRef('byok_stats_consent_enable', 'Enable Tracking'),
            cancelLabel: translationRef('common_cancel', 'Cancel'),
            variant: 'info',
        });
    }

    async function setByokStatisticsEnabled(enabled, regulatoryConfirmed = false) {
        const response = await fetchJson('/api/v1/llmstats/user/byok/settings', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                enabled: Boolean(enabled),
                regulatory_confirmed: Boolean(regulatoryConfirmed),
            }),
        });
        state.byokStatisticsEnabled = Boolean(enabled);
        if (response && Number.isFinite(Number(response.retention_days))) {
            state.byokStatisticsRetentionDays = Math.max(1, Math.round(Number(response.retention_days)));
        }
        if (!state.byokStatisticsEnabled) {
            resetByokStatsData();
        }
    }

    async function ensureByokStatsLoaded(force = false) {
        if (!state.byokStatisticsEnabled) return;
        if (state.byokStatsLoading) return;
        if (!force && state.byokStatsLoadedDays === state.byokStatsDays && state.byokStats.overview) return;

        state.byokStatsLoading = true;
        state.byokStatsError = '';
        renderByokStatsContent();

        const query = `?days=${encodeURIComponent(String(state.byokStatsDays))}`;
        try {
            const [overview, providerData, modelData, errorData, toolOverview, toolData] = await Promise.all([
                fetchJson(`/api/v1/llmstats/user/byok/overview${query}`),
                fetchJson(`/api/v1/llmstats/user/byok/by-provider${query}`),
                fetchJson(`/api/v1/llmstats/user/byok/by-model${query}`),
                fetchJson(`/api/v1/llmstats/user/byok/errors${query}&page=1&per_page=10`),
                fetchJson(`/api/v1/llmstats/user/byok/tool-calls/overview${query}`),
                fetchJson(`/api/v1/llmstats/user/byok/tool-calls/by-tool${query}`),
            ]);

            state.byokStats = {
                overview: overview || null,
                providers: Array.isArray(providerData?.providers) ? providerData.providers : [],
                models: Array.isArray(modelData?.models) ? modelData.models : [],
                errors: Array.isArray(errorData?.errors) ? errorData.errors : [],
                toolOverview: toolOverview || null,
                tools: Array.isArray(toolData?.tools) ? toolData.tools : [],
            };
            state.byokStatsLoadedDays = state.byokStatsDays;
        } catch (error) {
            state.byokStatsError = error?.message || 'Failed to load BYOK usage statistics.';
        } finally {
            state.byokStatsLoading = false;
            renderByokStatsContent();
        }
    }

    /**
     * Fetch a fresh statistics snapshot when the user opens the BYOK settings
     * page. This explicit public entry point keeps settings navigation from
     * depending on the internal statistics cache.
     */
    async function refreshStatistics() {
        await ensureByokStatsLoaded(true);
    }

    function renderByokStatsContent() {
        const host = document.getElementById('byokStatisticsContent');
        const controls = document.getElementById('byokStatisticsControls');
        const reportingControls = document.getElementById('byokStatisticsReportingControls');
        const toggle = document.getElementById('byokStatisticsToggle');
        const periodSelect = document.getElementById('byokStatisticsPeriod');
        if (!host) return;

        if (toggle) {
            toggle.checked = Boolean(state.byokStatisticsEnabled);
        }
        if (periodSelect) {
            // The shared custom-select keeps a hidden native select as its
            // source of truth. Update it through the adapter so the visible
            // label and selected menu option stay synchronized.
            window.setCustomSelectValue?.('byok_statistics_period', String(state.byokStatsDays));
        }
        if (controls) {
            controls.style.display = '';
            controls.classList.toggle('is-tracking-off', !state.byokStatisticsEnabled);
        }
        if (reportingControls) {
            reportingControls.hidden = !state.byokStatisticsEnabled;
        }
        if (periodSelect) {
            const periodSelectState = periodSelect.__customSelectState;
            const periodSelectDisabled = !state.byokStatisticsEnabled;
            if (periodSelectState?.nativeSelect) {
                periodSelectState.nativeSelect.disabled = periodSelectDisabled;
            }
            if (periodSelectState?.trigger) {
                periodSelectState.trigger.disabled = periodSelectDisabled;
            }
        }
        if (!state.byokStatisticsEnabled) {
            // The switch already communicates the disabled state. Keeping the
            // empty analytics area out of the layout avoids a redundant card.
            host.hidden = true;
            host.replaceChildren();
            return;
        }

        host.hidden = false;

        if (state.byokStatsLoading) {
            host.innerHTML = `<p class="byok-placeholder">${escapeHtml(byokT('byok_stats_loading', 'Loading BYOK usage statistics...'))}</p>`;
            return;
        }

        if (state.byokStatsError) {
            host.innerHTML = `<p class="byok-placeholder">${escapeHtml(state.byokStatsError)}</p>`;
            return;
        }

        const overview = state.byokStats.overview || {};
        const toolOverview = state.byokStats.toolOverview || {};
        const providers = state.byokStats.providers || [];
        const models = state.byokStats.models || [];
        const tools = state.byokStats.tools || [];
        const errors = state.byokStats.errors || [];

        const providerRows = providers.length
            ? providers.map((entry) => `
                <tr>
                    <td>${escapeHtml(entry.provider_name || entry.provider || byokT('common_unknown', 'Unknown'))}</td>
                    <td>${formatInteger(entry.requests)}</td>
                    <td>${formatInteger(entry.total_tokens)}</td>
                    <td>${formatCurrency(entry.cost)}</td>
                </tr>
            `).join('')
            : `<tr><td colspan="4" class="byok-stats-empty-cell">${escapeHtml(byokT('byok_stats_no_provider_data', 'No provider usage data yet.'))}</td></tr>`;

        const modelRows = models.length
            ? models.slice(0, 10).map((entry) => `
                <tr>
                    <td>${escapeHtml(entry.model_name || entry.model_id || byokT('common_unknown', 'Unknown'))}</td>
                    <td>${formatInteger(entry.requests)}</td>
                    <td>${formatInteger(entry.total_tokens)}</td>
                    <td>${formatCurrency(entry.cost)}</td>
                </tr>
            `).join('')
            : `<tr><td colspan="4" class="byok-stats-empty-cell">${escapeHtml(byokT('byok_stats_no_model_data', 'No model usage data yet.'))}</td></tr>`;

        const toolRows = tools.length
            ? tools.map((entry) => `
                <tr>
                    <td>${escapeHtml(entry.tool_name || byokT('common_unknown', 'Unknown'))}</td>
                    <td>${formatInteger(entry.total_calls)}</td>
                    <td>${formatPercent(entry.success_rate)}</td>
                </tr>
            `).join('')
            : `<tr><td colspan="3" class="byok-stats-empty-cell">${escapeHtml(byokT('byok_stats_no_tool_data', 'No tool usage data yet.'))}</td></tr>`;

        const errorRows = errors.length
            ? `<div class="byok-error-list">${errors.map((entry) => `
                <div class="byok-error-item">
                    <div class="byok-error-head">
                        <span class="byok-error-model">${escapeHtml(entry.model_name || entry.model_id || byokT('byok_unknown_model', 'Unknown Model'))}</span>
                        <span class="byok-error-time">${escapeHtml(formatDateTime(entry.created_at))}</span>
                    </div>
                    <p class="byok-error-message">${escapeHtml(entry.error_message || entry.error_type || byokT('byok_unknown_error', 'Unknown error'))}</p>
                </div>
            `).join('')}</div>`
            : `<p class="byok-placeholder">${escapeHtml(byokT('byok_stats_no_recent_errors', 'No recent BYOK errors in the selected period.'))}</p>`;

        host.innerHTML = `
            <div class="byok-stats-grid">
                <div class="byok-stat"><span class="byok-stat-label">${escapeHtml(byokT('byok_stats_total_requests', 'Total Requests'))}</span><span class="byok-stat-value">${formatInteger(overview.total_requests)}</span></div>
                <div class="byok-stat"><span class="byok-stat-label">${escapeHtml(byokT('byok_stats_success_rate', 'Success Rate'))}</span><span class="byok-stat-value">${formatPercent(overview.success_rate)}</span></div>
                <div class="byok-stat"><span class="byok-stat-label">${escapeHtml(byokT('byok_stats_total_tokens', 'Total Tokens'))}</span><span class="byok-stat-value">${formatInteger(overview.total_tokens)}</span></div>
                <div class="byok-stat"><span class="byok-stat-label">${escapeHtml(byokT('byok_stats_estimated_cost', 'Estimated Cost'))}</span><span class="byok-stat-value">${formatCurrency(overview.estimated_total_cost)}</span></div>
                <div class="byok-stat"><span class="byok-stat-label">${escapeHtml(byokT('byok_stats_tool_calls', 'Tool Calls'))}</span><span class="byok-stat-value">${formatInteger(toolOverview.total_calls)}</span></div>
                <div class="byok-stat"><span class="byok-stat-label">${escapeHtml(byokT('byok_stats_tool_success', 'Tool Success'))}</span><span class="byok-stat-value">${formatPercent(toolOverview.success_rate)}</span></div>
            </div>
            <div class="byok-stats-table-card">
                <h4 class="byok-stats-table-title">${escapeHtml(byokT('byok_stats_by_provider', 'By Provider'))}</h4>
                <div class="byok-stats-table-wrap">
                    <table class="byok-stats-table">
                        <thead><tr><th>${escapeHtml(byokT('byok_stats_provider', 'Provider'))}</th><th>${escapeHtml(byokT('byok_stats_requests', 'Requests'))}</th><th>${escapeHtml(byokT('byok_stats_tokens', 'Tokens'))}</th><th>${escapeHtml(byokT('byok_stats_cost', 'Cost'))}</th></tr></thead>
                        <tbody>${providerRows}</tbody>
                    </table>
                </div>
            </div>
            <div class="byok-stats-table-card">
                <h4 class="byok-stats-table-title">${escapeHtml(byokT('byok_stats_top_models', 'Top Models'))}</h4>
                <div class="byok-stats-table-wrap">
                    <table class="byok-stats-table">
                        <thead><tr><th>${escapeHtml(byokT('byok_stats_model', 'Model'))}</th><th>${escapeHtml(byokT('byok_stats_requests', 'Requests'))}</th><th>${escapeHtml(byokT('byok_stats_tokens', 'Tokens'))}</th><th>${escapeHtml(byokT('byok_stats_cost', 'Cost'))}</th></tr></thead>
                        <tbody>${modelRows}</tbody>
                    </table>
                </div>
            </div>
            <div class="byok-stats-table-card">
                <h4 class="byok-stats-table-title">${escapeHtml(byokT('byok_stats_tool_calls', 'Tool Calls'))}</h4>
                <div class="byok-stats-table-wrap">
                    <table class="byok-stats-table">
                        <thead><tr><th>${escapeHtml(byokT('byok_stats_tool', 'Tool'))}</th><th>${escapeHtml(byokT('byok_stats_calls', 'Calls'))}</th><th>${escapeHtml(byokT('byok_stats_success_rate', 'Success Rate'))}</th></tr></thead>
                        <tbody>${toolRows}</tbody>
                    </table>
                </div>
            </div>
            <div class="byok-stats-table-card">
                <h4 class="byok-stats-table-title">${escapeHtml(byokT('byok_stats_recent_errors', 'Recent Errors'))}</h4>
                ${errorRows}
            </div>
        `;
    }

    function downloadJsonFile(filename, payload) {
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
    }

    function bindByokStatsHandlers() {
        const toggle = document.getElementById('byokStatisticsToggle');
        const periodSelect = document.getElementById('byokStatisticsPeriod');
        const exportButton = document.getElementById('byokStatisticsExport');
        const deleteButton = document.getElementById('byokStatisticsDelete');

        toggle?.addEventListener('change', async (event) => {
            const target = event.target;
            if (!(target instanceof HTMLInputElement)) return;
            const requested = target.checked;
            try {
                if (requested) {
                    const confirmed = await showByokStatsConsentModal();
                    if (!confirmed) {
                        target.checked = false;
                        return;
                    }
                    await setByokStatisticsEnabled(true, true);
                    await ensureByokStatsLoaded(true);
                } else {
                    await setByokStatisticsEnabled(false, false);
                }
            } catch (error) {
                target.checked = !requested;
                if (typeof notifyError === 'function') {
                    notifyError(error?.message || byokT('byok_stats_update_failed', 'Failed to update BYOK statistics setting.'));
                }
            } finally {
                renderByokStatsContent();
            }
        });

        periodSelect?.addEventListener('customSelectChange', async (event) => {
            const days = Number.parseInt(event.detail?.value, 10);
            if (![7, 30, 90, 365].includes(days)) return;
            state.byokStatsDays = days;
            await ensureByokStatsLoaded(true);
        });

        exportButton?.addEventListener('click', async () => {
            try {
                const payload = await fetchJson('/api/v1/llmstats/user/byok/export');
                const stamp = new Date().toISOString().replace(/[:.]/g, '-');
                downloadJsonFile(`byok-usage-stats-${stamp}.json`, payload);
                if (typeof notifySuccess === 'function') {
                    notifySuccess(byokT('byok_stats_exported', 'BYOK usage data exported.'));
                }
            } catch (error) {
                if (typeof notifyError === 'function') {
                    notifyError(error?.message || byokT('byok_stats_export_failed', 'Failed to export BYOK usage data.'));
                }
            }
        });

        deleteButton?.addEventListener('click', async () => {
            if (!await window.showDeleteConfirm({
                title: byokT('byok_stats_delete', 'Delete'),
                message: byokT('byok_stats_delete_confirm', 'Delete all stored BYOK usage statistics for your account? This cannot be undone.'),
                confirmLabel: byokT('byok_stats_delete', 'Delete'),
            })) {
                return;
            }
            try {
                await fetchJson('/api/v1/llmstats/user/byok/all', {
                    method: 'DELETE',
                });
                resetByokStatsData();
                await ensureByokStatsLoaded(true);
                if (typeof notifySuccess === 'function') {
                    notifySuccess(byokT('byok_stats_deleted', 'BYOK usage data deleted.'));
                }
            } catch (error) {
                if (typeof notifyError === 'function') {
                    notifyError(error?.message || byokT('byok_stats_delete_failed', 'Failed to delete BYOK usage data.'));
                }
            }
        });
    }

    function renderProviderList() {
        const host = document.getElementById('byokProviderList');
        if (!host) return;
        const providers = getProviders();
        host.innerHTML = '';

        if (!providers.length) {
            host.className = 'byok-empty';
            host.innerHTML = `
                <div class="byok-empty-icon" aria-hidden="true">
                    ${Icons.plus}
                </div>
                <h3 class="byok-empty-title">${escapeHtml(byokT('byok_provider_empty_title', 'No provider instances yet'))}</h3>
                <p class="byok-empty-desc">${escapeHtml(byokT('byok_provider_empty_desc', 'Create one reusable provider connection for multiple models.'))}</p>
            `;
            return;
        }

        host.className = 'byok-list';

        providers.forEach((provider) => {
            const providerLabel = PROVIDER_OPTIONS.find((entry) => entry.value === provider.provider)?.label || provider.provider;
            const providerIconAlt = byokT('byok_provider_icon_alt', 'Provider icon');
            const providerIconValue = resolveProviderIcon(provider.provider, provider.icon);
            const providerIcon = renderStoredIconMarkup(providerIconValue, {
                fallback: renderStoredIconMarkup(getDefaultProviderIcon(provider.provider), { imageAlt: providerIconAlt }),
                imageAlt: providerIconAlt,
            });
            const hasKey = Boolean(getProviderCredentialToken(provider));
            const requiresKey = !OPTIONAL_API_KEY_PROVIDERS.has(normalizeProviderType(provider.provider));
            const endpoint = trimString(provider.base_url);
            const providerMeta = endpoint ? `${providerLabel} • ${endpoint}` : providerLabel;
            const item = document.createElement('div');
            item.className = 'byok-list-item';
            item.innerHTML = `
                <div class="byok-item-icon" aria-hidden="true">${providerIcon}</div>
                <div class="byok-item-body">
                    <div class="byok-item-title-row">
                        <h3 class="byok-item-title">${escapeHtml(provider.name || provider.provider)}</h3>
                        ${hasKey || !requiresKey ? '' : `<span class="byok-item-tag is-warning">${escapeHtml(byokT('byok_provider_key_required', 'API key required'))}</span>`}
                    </div>
                    <p class="byok-item-meta">${escapeHtml(providerMeta)}</p>
                </div>
                <div class="byok-item-actions">
                    <button type="button" class="byok-icon-btn" data-action="edit" aria-label="${escapeHtml(formatTranslation('byok_action_edit_named', 'Edit {name}', { name: provider.name || provider.provider }))}" title="${escapeHtml(byokT('byok_action_edit', 'Edit'))}">
                        ${Icons.edit}
                    </button>
                    <button type="button" class="byok-icon-btn is-danger" data-action="delete" aria-label="${escapeHtml(formatTranslation('byok_action_delete_named', 'Delete {name}', { name: provider.name || provider.provider }))}" title="${escapeHtml(byokT('byok_action_delete', 'Delete'))}">
                        ${Icons.trash}
                    </button>
                </div>
            `;

            item.querySelector('[data-action="edit"]')?.addEventListener('click', () => openProviderEditor(provider.id));
            item.querySelector('[data-action="delete"]')?.addEventListener('click', () => deleteProvider(provider.id));
            host.appendChild(item);
        });

        const select = document.getElementById('byokModelProviderInstance');
        if (select) {
            populateProviderSelect(select);
        }
    }

    function renderModelList() {
        const host = document.getElementById('byokModelList');
        if (!host) return;
        const models = getLocalModels();
        host.innerHTML = '';

        if (!models.length) {
            host.className = 'byok-empty';
            host.innerHTML = `
                <div class="byok-empty-icon" aria-hidden="true">
                    ${Icons.assistant || Icons.plus}
                </div>
                <h3 class="byok-empty-title">${escapeHtml(byokT('byok_model_empty_title', 'No BYOK models yet'))}</h3>
                <p class="byok-empty-desc">${escapeHtml(byokT('byok_model_empty_desc', 'Create a local model definition that points to one of your saved provider instances.'))}</p>
            `;
            return;
        }

        host.className = 'byok-list';

        models.forEach((model) => {
            const toolCount = Array.isArray(model.tools) ? model.tools.length : 0;
            const providerIconAlt = byokT('byok_provider_icon_alt', 'Provider icon');
            const modelIconAlt = byokT('byok_model_icon_alt', 'Model icon');
            const modelIcon = renderStoredIconMarkup(model.model_icon, {
                fallback: renderStoredIconMarkup(model.provider_icon, {
                    fallback: renderStoredIconMarkup(getDefaultProviderIcon(model.provider), { imageAlt: providerIconAlt }),
                    imageAlt: providerIconAlt,
                }),
                imageAlt: modelIconAlt,
            });
            const providerLabel = model.provider_name || model.provider;
            const toolsLabel = toolCount
                ? formatTranslation(
                    toolCount === 1 ? 'byok_model_tool_count_one' : 'byok_model_tool_count_other',
                    toolCount === 1 ? '{count} tool' : '{count} tools',
                    { count: toolCount },
                )
                : byokT('byok_model_no_tools', 'No tools');
            const item = document.createElement('div');
            item.className = 'byok-list-item';
            item.innerHTML = `
                <div class="byok-item-icon" aria-hidden="true">${modelIcon}</div>
                <div class="byok-item-body">
                    <div class="byok-item-title-row">
                        <h3 class="byok-item-title">${escapeHtml(model.name || model.model_name)}</h3>
                        <span class="byok-item-tag">${escapeHtml(toolsLabel)}</span>
                    </div>
                    <p class="byok-item-meta">${escapeHtml(providerLabel)} • ${escapeHtml(model.model_name)}</p>
                </div>
                <div class="byok-item-actions">
                    <button type="button" class="byok-icon-btn" data-action="edit" aria-label="${escapeHtml(formatTranslation('byok_action_edit_named', 'Edit {name}', { name: model.name || model.model_name }))}" title="${escapeHtml(byokT('byok_action_edit', 'Edit'))}">
                    ${Icons.edit}
                    </button>
                    <button type="button" class="byok-icon-btn is-danger" data-action="delete" aria-label="${escapeHtml(formatTranslation('byok_action_delete_named', 'Delete {name}', { name: model.name || model.model_name }))}" title="${escapeHtml(byokT('byok_action_delete', 'Delete'))}">
                        ${Icons.trash}
                    </button>
                </div>
            `;

            item.querySelector('[data-action="edit"]')?.addEventListener('click', () => openModelEditor(model.model_id));
            item.querySelector('[data-action="delete"]')?.addEventListener('click', () => deleteModel(model.model_id));
            host.appendChild(item);
        });
    }

    function populateProviderSelect(select) {
        if (!select) return;
        const current = select.value;
        select.innerHTML = '';
        const empty = document.createElement('option');
        empty.value = '';
        empty.textContent = byokT('byok_choose_provider_instance', 'Choose provider instance');
        select.appendChild(empty);
        getProviders().forEach((provider) => {
            const option = document.createElement('option');
            option.value = String(provider.id);
            option.textContent = provider.name || provider.provider;
            option.selected = current && String(current) === String(provider.id);
            select.appendChild(option);
        });
        syncEnhancedSelect(select, { refreshOptions: true });
    }

    function renderRoot() {
        if (!state.domReady) return;
        applyVisibility();
        const root = getByokRoot();
        if (!root || !state.allow) {
            return;
        }

        const statisticsPeriods = [
            { value: 7, key: 'byok_stats_period_7_days', label: byokT('byok_stats_period_7_days', 'Last 7 days') },
            { value: 30, key: 'byok_stats_period_30_days', label: byokT('byok_stats_period_30_days', 'Last 30 days') },
            { value: 90, key: 'byok_stats_period_90_days', label: byokT('byok_stats_period_90_days', 'Last 90 days') },
            { value: 365, key: 'byok_stats_period_365_days', label: byokT('byok_stats_period_365_days', 'Last 365 days') },
        ];
        const selectedStatisticsPeriod = statisticsPeriods.find(
            (period) => period.value === state.byokStatsDays,
        ) || statisticsPeriods[1];

        root.innerHTML = `
            <div class="byok-shell">
                <details class="byok-disclosure">
                    <summary data-i18n="us_security_privacy_title">${escapeHtml(byokT('us_security_privacy_title', 'Privacy'))}</summary>
                    <div class="byok-disclosure-body">
                        <p data-i18n="byok_transfer_disclosure">${escapeHtml(byokT('byok_transfer_disclosure', 'Omlorix processes BYOK secrets transiently on the backend to contact the selected provider. Prompts, files, tool inputs, provider settings, base URL, model details, and keys are sent to that provider.'))}</p>
                        <p data-i18n="byok_model_transfer_disclosure">${escapeHtml(byokT('byok_model_transfer_disclosure', 'When you use a BYOK model, Omlorix sends the request payload, selected files, enabled tools, provider settings, and model details to the provider configured for that model.'))}</p>
                    </div>
                </details>

                <section class="byok-section">
                    <div class="byok-section-head">
                        <div class="byok-section-head-text">
                            <h2 class="byok-section-title" data-i18n="byok_provider_instances_title">${escapeHtml(byokT('byok_provider_instances_title', 'Provider Instances'))}</h2>
                        </div>
                        <button type="button" class="om-button border byok-add-btn" id="byokCreateProviderButton" data-i18n-attr="aria-label:byok_provider_add_instance" aria-label="${escapeHtml(byokT('byok_provider_add_instance', 'Add provider instance'))}">
                            ${Icons.plus}
                            <span data-i18n="byok_provider_add">${escapeHtml(byokT('byok_provider_add', 'Add provider'))}</span>
                        </button>
                    </div>
                    <div id="byokProviderList" class="byok-list"></div>
                </section>

                <section class="byok-section">
                    <div class="byok-section-head">
                        <div class="byok-section-head-text">
                            <h2 class="byok-section-title" data-i18n="byok_models_title">${escapeHtml(byokT('byok_models_title', 'BYOK Models'))}</h2>
                        </div>
                        <button type="button" class="om-button border byok-add-btn" id="byokCreateModelButton" data-i18n-attr="aria-label:byok_model_add" aria-label="${escapeHtml(byokT('byok_model_add', 'Add BYOK model'))}">
                            ${Icons.plus}
                            <span data-i18n="byok_model_add_short">${escapeHtml(byokT('byok_model_add_short', 'Add model'))}</span>
                        </button>
                    </div>
                    <div id="byokModelList" class="byok-list"></div>
                </section>

                <section class="byok-section">
                    <div class="byok-section-head">
                        <div class="byok-section-head-text">
                            <h2 class="byok-section-title" data-i18n="byok_stats_usage_title">${escapeHtml(byokT('byok_stats_usage_title', 'Usage Statistics'))}</h2>
                        </div>
                    </div>

                    <div class="us-setting-item byok-tracking-card">
                        <div class="byok-tracking-text">
                            <h3 class="byok-tracking-title" id="byokStatisticsToggleLabel" data-i18n="byok_stats_track_usage">${escapeHtml(byokT('byok_stats_track_usage', 'Track BYOK usage'))}</h3>
                            <p class="byok-tracking-desc" data-i18n="byok_stats_track_usage_desc">${escapeHtml(byokT('byok_stats_track_usage_desc', 'When enabled, requests store model/provider metadata, token usage, and estimated costs.'))}</p>
                        </div>
                        <label class="toggle-switch">
                            <input type="checkbox" class="toggle-input" id="byokStatisticsToggle" ${state.byokStatisticsEnabled ? 'checked' : ''} aria-labelledby="byokStatisticsToggleLabel">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>

                    <div id="byokStatisticsControls">
                        <div class="byok-stats-toolbar">
                            <div class="byok-stats-toolbar-group" id="byokStatisticsReportingControls">
                                <div class="custom-select us byok-period-select" id="byokStatisticsPeriod">
                                    <div
                                        class="select-trigger"
                                        data-field="byok_statistics_period"
                                        data-i18n-attr="aria-label:byok_stats_reporting_period"
                                        aria-label="${escapeHtml(byokT('byok_stats_reporting_period', 'Reporting period'))}"
                                    >
                                        <span>${escapeHtml(selectedStatisticsPeriod.label)}</span>
                                    </div>
                                    <div class="select-options">
                                        ${statisticsPeriods.map((period) => `
                                            <div
                                                class="select-option ${period.value === selectedStatisticsPeriod.value ? 'selected' : ''}"
                                                data-value="${period.value}"
                                                data-i18n="${period.key}"
                                            >${escapeHtml(period.label)}</div>
                                        `).join('')}
                                    </div>
                                </div>
                            </div>
                            <div class="byok-stats-toolbar-group">
                                <button type="button" class="om-button border" id="byokStatisticsExport" data-i18n="byok_stats_export">${escapeHtml(byokT('byok_stats_export', 'Export'))}</button>
                                <button type="button" class="om-button border danger-nofill" id="byokStatisticsDelete" data-i18n="byok_stats_delete">${escapeHtml(byokT('byok_stats_delete', 'Delete'))}</button>
                            </div>
                        </div>
                    </div>

                    <div id="byokStatisticsContent"></div>
                </section>

                <div class="byok-modal-overlay shared-modal-overlay" id="byokProviderOverlay" hidden aria-hidden="true">
                    <div class="byok-modal shared-modal shared-modal--wide shared-modal--fixed" role="dialog" aria-modal="true" aria-labelledby="byokProviderEditorTitle" aria-describedby="byokProviderEditorSubtitle" tabindex="-1">
                        <header class="byok-modal-header shared-modal-header shared-modal-header--main">
                            <div class="byok-modal-heading shared-modal-heading">
                                <h3 class="shared-modal-title" id="byokProviderEditorTitle" data-i18n="byok_provider_editor_title_add">${escapeHtml(byokT('byok_provider_editor_title_add', 'Add Provider Instance'))}</h3>
                                <p class="shared-modal-subtitle" id="byokProviderEditorSubtitle" data-i18n="byok_provider_editor_subtitle_add_session">${escapeHtml(byokT('byok_provider_editor_subtitle_add_session', 'Create a reusable local provider connection. The key is sealed by the server for reloads in this tab.'))}</p>
                            </div>
                            <button type="button" class="byok-modal-close shared-modal-close" id="byokProviderCloseButton" data-i18n-attr="aria-label:byok_provider_editor_close" aria-label="${escapeHtml(byokT('byok_provider_editor_close', 'Close provider editor'))}">
                                ${Icons.close}
                            </button>
                        </header>
                        <form class="byok-modal-form-shell" id="byokProviderForm" autocomplete="off" novalidate>
                            <div class="byok-modal-body shared-modal-body">
                                <div class="byok-modal-fields">
                            <div class="us-setting-item byok-setting-item align-start">
                                <div class="us-setting-info">
                                    <h3 id="byokProviderTypeLabel" data-i18n="byok_provider_type_label">${escapeHtml(byokT('byok_provider_type_label', 'Provider Type'))}</h3>
                                    <p id="byokProviderTypeDescription" data-i18n="byok_provider_type_desc">${escapeHtml(byokT('byok_provider_type_desc', 'Choose the provider protocol this instance should use.'))}</p>
                                </div>
                                <div class="byok-control-wrap">
                                    <select class="input us" id="byokProviderType" aria-labelledby="byokProviderTypeLabel" aria-describedby="byokProviderTypeDescription" required></select>
                                </div>
                            </div>
                            <div class="us-setting-item byok-setting-item align-start">
                                <div class="us-setting-info">
                                    <h3 id="byokProviderNameLabel" data-i18n="byok_provider_name_label">${escapeHtml(byokT('byok_provider_name_label', 'Provider Name'))}</h3>
                                    <p id="byokProviderNameDescription" data-i18n="byok_provider_name_desc">${escapeHtml(byokT('byok_provider_name_desc', 'Local label shown in the model selector.'))}</p>
                                </div>
                                <div class="byok-control-wrap">
                                    <input class="input us" id="byokProviderName" type="text" data-i18n-attr="placeholder:byok_provider_name_placeholder" placeholder="${escapeHtml(byokT('byok_provider_name_placeholder', 'My OpenAI Account'))}" aria-labelledby="byokProviderNameLabel" aria-describedby="byokProviderNameDescription" autocomplete="off" required>
                                </div>
                            </div>
                            <div class="us-setting-item byok-setting-item align-start">
                                <div class="us-setting-info">
                                    <h3 id="byokProviderBaseUrlLabel" data-i18n="byok_provider_base_url_label">${escapeHtml(byokT('byok_provider_base_url_label', 'Base URL'))}</h3>
                                    <p id="byokProviderBaseUrlDescription" data-i18n="byok_provider_base_url_desc">${escapeHtml(byokT('byok_provider_base_url_desc', 'Optional override for custom endpoints. Required for providers like Ollama.'))}</p>
                                </div>
                                <div class="byok-control-wrap">
                                    <input class="input us" id="byokProviderBaseUrl" type="url" inputmode="url" spellcheck="false" autocomplete="url" placeholder="https://api.example.com/v1" aria-labelledby="byokProviderBaseUrlLabel" aria-describedby="byokProviderBaseUrlDescription">
                                </div>
                            </div>
                            <div class="us-setting-item byok-setting-item align-start">
                                <div class="us-setting-info">
                                    <h3 id="byokProviderApiKeyLabel" data-i18n="byok_provider_api_key_label">${escapeHtml(byokT('byok_provider_api_key_label', 'API Key'))}</h3>
                                    <p id="byokProviderApiKeyDescription" data-i18n="byok_provider_api_key_desc_session">${escapeHtml(byokT('byok_provider_api_key_desc_session', 'Omlorix stores only a sealed credential token in this tab for up to 30 days, including across reloads.'))}</p>
                                </div>
                                <div class="byok-control-wrap">
                                    <input class="input us" id="byokProviderApiKey" type="password" placeholder="sk-..." aria-labelledby="byokProviderApiKeyLabel" aria-describedby="byokProviderApiKeyDescription" autocomplete="off" autocapitalize="none" spellcheck="false">
                                </div>
                            </div>
                                </div>
                                <div class="byok-modal-dynamic-fields" id="byokProviderSettingsFields"></div>
                                <details class="byok-disclosure byok-modal-disclosure">
                                    <summary data-i18n="us_security_privacy_title">${escapeHtml(byokT('us_security_privacy_title', 'Privacy'))}</summary>
                                    <div class="byok-disclosure-body">
                                        <p data-i18n="byok_provider_editor_disclosure">${escapeHtml(byokT('byok_provider_editor_disclosure', 'Create a provider connection. Omlorix uses the secret transiently on the backend and sends requests to the selected upstream provider.'))}</p>
                                        <p data-i18n="byok_api_key_transfer_disclosure">${escapeHtml(byokT('byok_api_key_transfer_disclosure', 'Omlorix sends this key once to the backend for sealing. The backend decrypts it only while contacting the selected upstream provider for your requests.'))}</p>
                                        <p data-i18n="byok_api_key_storage_disclosure">${escapeHtml(byokT('byok_api_key_storage_disclosure', 'The API key is sent once to Omlorix and sealed by the server. Only the encrypted token is stored in this tab for up to 30 days. Logout or closing the tab removes it sooner.'))}</p>
                                    </div>
                                </details>
                            </div>
                            <footer class="byok-modal-footer shared-modal-footer">
                                <button type="button" class="om-button border cancel" id="byokProviderCancelButton" data-i18n="byok_action_cancel">${escapeHtml(byokT('byok_action_cancel', 'Cancel'))}</button>
                                <button type="submit" class="om-button border submit" id="byokProviderSaveButton"><span id="byokProviderSaveButtonLabel" data-i18n="byok_provider_save">${escapeHtml(byokT('byok_provider_save', 'Save Provider'))}</span></button>
                            </footer>
                        </form>
                    </div>
                </div>

                <div class="byok-modal-overlay shared-modal-overlay" id="byokModelOverlay" hidden aria-hidden="true">
                    <div class="byok-modal shared-modal shared-modal--wide shared-modal--fixed" role="dialog" aria-modal="true" aria-labelledby="byokModelEditorTitle" aria-describedby="byokModelEditorSubtitle" tabindex="-1">
                        <header class="byok-modal-header shared-modal-header shared-modal-header--main">
                            <div class="byok-modal-heading shared-modal-heading">
                                <h3 class="shared-modal-title" id="byokModelEditorTitle" data-i18n="byok_model_editor_title_add">${escapeHtml(byokT('byok_model_editor_title_add', 'Add BYOK Model'))}</h3>
                                <p class="shared-modal-subtitle" id="byokModelEditorSubtitle" data-i18n="byok_model_editor_subtitle_add">${escapeHtml(byokT('byok_model_editor_subtitle_add', 'Build a local model definition with remote model discovery and schema-aware settings.'))}</p>
                            </div>
                            <button type="button" class="byok-modal-close shared-modal-close" id="byokModelCloseButton" data-i18n-attr="aria-label:byok_model_editor_close" aria-label="${escapeHtml(byokT('byok_model_editor_close', 'Close model editor'))}">
                                ${Icons.close}
                            </button>
                        </header>
                        <form class="byok-modal-form-shell" id="byokModelForm" novalidate>
                            <div class="byok-modal-body shared-modal-body">
                                <div class="byok-modal-fields">
                            <div class="us-setting-item byok-setting-item align-start">
                                <div class="us-setting-info">
                                    <h3 id="byokModelProviderLabel" data-i18n="byok_model_provider_instance_label">${escapeHtml(byokT('byok_model_provider_instance_label', 'Provider Instance'))}</h3>
                                    <p id="byokModelProviderDescription" data-i18n="byok_model_provider_instance_desc">${escapeHtml(byokT('byok_model_provider_instance_desc', 'Select which local provider instance this model should use.'))}</p>
                                </div>
                                <div class="byok-control-wrap">
                                    <select class="input us" id="byokModelProviderInstance" aria-labelledby="byokModelProviderLabel" aria-describedby="byokModelProviderDescription" required></select>
                                </div>
                            </div>
                            <div class="us-setting-item byok-setting-item align-start">
                                <div class="us-setting-info">
                                    <h3 id="byokRemoteModelLabel" data-i18n="byok_remote_model_label">${escapeHtml(byokT('byok_remote_model_label', 'Remote Model'))}</h3>
                                    <p id="byokRemoteModelDescription" data-i18n="byok_remote_model_desc">${escapeHtml(byokT('byok_remote_model_desc', 'Loaded automatically from the selected provider. Manual mode appears only when discovery fails or returns no models.'))}</p>
                                </div>
                                <div class="byok-control-wrap byok-remote-controls">
                                    <select class="input us" id="byokRemoteModelSelect" aria-labelledby="byokRemoteModelLabel" aria-describedby="byokRemoteModelDescription byokRemoteModelStatus">
                                        <option value="" data-i18n="byok_remote_select_provider_first">${escapeHtml(byokT('byok_remote_select_provider_first', 'Select a provider instance first'))}</option>
                                    </select>
                                    <p class="byok-remote-status" id="byokRemoteModelStatus" data-i18n="byok_remote_select_provider_status">${escapeHtml(byokT('byok_remote_select_provider_status', 'Select a provider instance to load remote models automatically.'))}</p>
                                </div>
                            </div>
                            <div class="us-setting-item byok-setting-item align-start">
                                <div class="us-setting-info">
                                    <h3 data-i18n="byok_model_schema_label">${escapeHtml(byokT('byok_model_schema_label', 'Model Schema'))}</h3>
                                    <p data-i18n="byok_model_schema_desc">${escapeHtml(byokT('byok_model_schema_desc', 'Load a settings schema tailored to the selected model and provider.'))}</p>
                                </div>
                                <div class="byok-control-wrap">
                                    <button type="button" class="om-button border" id="byokRefreshSchemaButton" data-i18n="byok_model_schema_load">${escapeHtml(byokT('byok_model_schema_load', 'Load Model Schema'))}</button>
                                </div>
                            </div>
                                </div>
                                <div class="byok-modal-dynamic-fields" id="byokModelSettingsFields"></div>
                            </div>
                            <footer class="byok-modal-footer shared-modal-footer">
                                <button type="button" class="om-button border cancel" id="byokModelCancelButton" data-i18n="byok_action_cancel">${escapeHtml(byokT('byok_action_cancel', 'Cancel'))}</button>
                                <button type="submit" class="om-button border submit" id="byokModelSaveButton"><span id="byokModelSaveButtonLabel" data-i18n="byok_model_save">${escapeHtml(byokT('byok_model_save', 'Save BYOK Model'))}</span></button>
                            </footer>
                        </form>
                    </div>
                </div>
                <div id="byokDialogMount"></div>
            </div>
        `;

        // User-settings custom selects are normally initialized on page load.
        // BYOK renders later, so enhance its newly mounted reporting-period
        // control before binding feature handlers.
        window.initializeCustomSelects?.();

        state.providerSettingsHost = document.getElementById('byokProviderSettingsFields');
        state.modelSettingsHost = document.getElementById('byokModelSettingsFields');
        const byokDialogOverlay = window.DeleteWarningModal?.create({
            id: 'byokDialogOverlay',
            overlayAttrs: { 'aria-hidden': 'true' },
            cardId: 'byokDialogCard',
            cardClass: 'byok-dialog-card',
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'byokDialogTitle',
            ariaDescribedby: 'byokDialogDescription',
            icon: 'warning',
            iconAttrs: { id: 'byokDialogIcon', 'aria-hidden': 'true' },
            title: { id: 'byokDialogTitle', text: byokT('byok_dialog_confirm_action', 'Confirm action') },
            descriptions: [{ id: 'byokDialogDescription' }],
            actions: [
                { id: 'byokDialogCancelButton', role: 'cancel', variant: 'cancel', text: byokT('byok_action_cancel', 'Cancel') },
                { id: 'byokDialogConfirmButton', variant: 'danger', text: byokT('byok_action_confirm', 'Confirm') },
            ],
        });
        if (byokDialogOverlay) {
            document.getElementById('byokDialogMount')?.replaceChildren(byokDialogOverlay);
        }

        const providerTypeSelect = document.getElementById('byokProviderType');
        PROVIDER_OPTIONS.forEach((provider) => {
            const option = document.createElement('option');
            option.value = provider.value;
            option.textContent = provider.label;
            providerTypeSelect.appendChild(option);
        });

        // Bring every select in the BYOK editors onto the same shared listbox
        // component used throughout user settings. Their native controls remain
        // the source of truth for the existing feature logic.
        enhanceByokSelect(providerTypeSelect, 'byok_provider_type');
        enhanceByokSelect(document.getElementById('byokModelProviderInstance'), 'byok_model_provider_instance');
        enhanceByokSelect(document.getElementById('byokRemoteModelSelect'), 'byok_remote_model', {
            searchable: true,
            search: {
                enabled: true,
                placeholder: byokT('byok_remote_search_placeholder', 'Search remote models...'),
                noResultsMessage: byokT('byok_remote_search_empty', 'No matching remote models'),
            },
        });

        providerTypeSelect.addEventListener('change', () => {
            syncProviderBaseUrlField(providerTypeSelect.value);
            loadProviderSchema(providerTypeSelect.value).catch((error) => {
                notifyError(error.message || byokT('byok_provider_schema_load_failed', 'Failed to load provider schema.'));
            });
        });

        document.getElementById('byokCreateProviderButton')?.addEventListener('click', () => openProviderEditor(null));
        document.getElementById('byokCreateModelButton')?.addEventListener('click', () => openModelEditor(null));
        document.getElementById('byokProviderForm')?.addEventListener('submit', (event) => {
            event.preventDefault();
            saveProvider();
        });
        document.getElementById('byokModelForm')?.addEventListener('submit', (event) => {
            event.preventDefault();
            saveModel();
        });
        document.getElementById('byokProviderCancelButton')?.addEventListener('click', closeProviderModal);
        document.getElementById('byokModelCancelButton')?.addEventListener('click', closeModelModal);
        document.getElementById('byokProviderCloseButton')?.addEventListener('click', closeProviderModal);
        document.getElementById('byokModelCloseButton')?.addEventListener('click', closeModelModal);
        document.getElementById('byokRefreshSchemaButton')?.addEventListener('click', () => {
            loadModelSchemaForEditor({
                requestToken: state.remoteModelsRequestToken,
            });
        });
        document.getElementById('byokRemoteModelSelect')?.addEventListener('change', handleRemoteModelSelection);
        document.getElementById('byokModelProviderInstance')?.addEventListener('change', () => {
            syncModelEditorForSelectedProvider().catch((error) => {
                notifyError(error.message || byokT('byok_provider_prepare_failed', 'Failed to prepare the selected provider.'));
            });
        });
        document.getElementById('byokProviderOverlay')?.addEventListener('click', (event) => {
            if (event.target === document.getElementById('byokProviderOverlay')) {
                closeProviderModal();
            }
        });
        document.getElementById('byokModelOverlay')?.addEventListener('click', (event) => {
            if (event.target === document.getElementById('byokModelOverlay')) {
                closeModelModal();
            }
        });
        document.getElementById('byokDialogOverlay')?.addEventListener('click', (event) => {
            if (event.target === document.getElementById('byokDialogOverlay')) {
                closeDialog(false);
            }
        });
        document.getElementById('byokDialogCancelButton')?.addEventListener('click', () => closeDialog(false));
        document.getElementById('byokDialogConfirmButton')?.addEventListener('click', () => closeDialog(true));

        [
            document.getElementById('byokProviderType'),
            document.getElementById('byokProviderName'),
            document.getElementById('byokModelProviderInstance'),
        ].forEach((control) => {
            control?.addEventListener(control instanceof HTMLSelectElement ? 'change' : 'input', () => {
                clearByokControlError(control);
            });
        });

        bindByokStatsHandlers();
        renderByokStatsContent();
        if (state.byokStatisticsEnabled) {
            ensureByokStatsLoaded();
        }

        renderProviderList();
        renderModelList();
        resetProviderEditor();
        resetModelEditor();
    }

    function captureFocusedControl() {
        const active = document.activeElement;
        const contexts = [
            ['provider', state.providerFormContext],
            ['model', state.modelFormContext],
        ];
        for (const [editor, context] of contexts) {
            for (const [fieldKey, entry] of context?.controls || []) {
                const wrapper = entry.control?._singleSelect?.wrapper;
                if (entry.control === active || wrapper?.contains(active)) {
                    return { editor, fieldKey };
                }
            }
        }
        return null;
    }

    function restoreFocusedControl(focus) {
        if (!focus) return;
        requestAnimationFrame(() => {
            const context = focus.editor === 'provider'
                ? state.providerFormContext
                : state.modelFormContext;
            focusByokControl(context?.controls?.get(focus.fieldKey)?.control);
        });
    }

    function refreshProviderEditorTranslations() {
        if (!state.providerModalOpen) return;
        const values = collectContextValues(state.providerFormContext);
        const type = document.getElementById('byokProviderType');
        const apiKey = document.getElementById('byokProviderApiKey');
        const title = document.getElementById('byokProviderEditorTitle');
        const subtitle = document.getElementById('byokProviderEditorSubtitle');
        const saveLabel = document.getElementById('byokProviderSaveButtonLabel');
        const isEditing = Boolean(state.providerEditingId);

        if (title) title.textContent = byokT(
            isEditing ? 'byok_provider_editor_title_edit' : 'byok_provider_editor_title_add',
            isEditing ? 'Edit Provider Instance' : 'Add Provider Instance',
        );
        if (subtitle) subtitle.textContent = byokT(
            isEditing ? 'byok_provider_editor_subtitle_edit_session' : 'byok_provider_editor_subtitle_add_session',
            isEditing
                ? 'Edit this existing local provider connection. Enter a new credential only to replace the current one.'
                : 'Create a reusable local provider connection. The key is sealed by the server for reloads in this tab.',
        );
        if (saveLabel) saveLabel.textContent = byokT(
            isEditing ? 'byok_action_save_changes' : 'byok_provider_save',
            isEditing ? 'Save Changes' : 'Save Provider',
        );
        if (apiKey) {
            const provider = state.providerEditingId ? getProviderById(state.providerEditingId) : null;
            apiKey.placeholder = provider && getProviderCredentialToken(provider)
                ? byokT('byok_provider_api_key_placeholder_keep_session', 'Leave empty to keep the current session key')
                : byokT('byok_provider_api_key_placeholder_session', 'Enter API key for this session');
        }
        state.providerFormContext = renderSchemaFields(
            state.providerSettingsHost,
            state.providerSchema || { sections: [] },
            values,
        );
        applyProviderBaseUrlSuggestions(state.providerBaseUrlSuggestions);
        syncEnhancedSelect(type, { refreshOptions: true });
    }

    function refreshModelEditorTranslations() {
        if (!state.modelModalOpen) return;
        const values = collectContextValues(state.modelFormContext);
        const selectedModelName = document.getElementById('byokRemoteModelSelect')?.value || '';
        const title = document.getElementById('byokModelEditorTitle');
        const subtitle = document.getElementById('byokModelEditorSubtitle');
        const saveLabel = document.getElementById('byokModelSaveButtonLabel');
        const providerSelect = document.getElementById('byokModelProviderInstance');
        const isEditing = Boolean(state.modelEditingId);
        if (title) title.textContent = byokT(
            isEditing ? 'byok_model_editor_title_edit' : 'byok_model_editor_title_add',
            isEditing ? 'Edit BYOK Model' : 'Add BYOK Model',
        );
        if (subtitle) subtitle.textContent = byokT(
            isEditing ? 'byok_model_editor_subtitle_edit' : 'byok_model_editor_subtitle_add',
            isEditing
                ? 'Update the model definition and provider-specific settings.'
                : 'Build a local model definition with remote model discovery and schema-aware settings.',
        );
        if (saveLabel) saveLabel.textContent = byokT(
            isEditing ? 'byok_action_save_changes' : 'byok_model_save',
            isEditing ? 'Save Changes' : 'Save BYOK Model',
        );
        updateRemoteModelSelect(selectedModelName);
        if (state.modelSchema) {
            state.modelFormContext = renderSchemaFields(
                state.modelSettingsHost,
                state.modelSchema,
                values,
            );
        } else {
            renderModelSchemaPlaceholder(byokT(
                providerSelect?.value
                    ? 'byok_remote_select_model_for_schema'
                    : 'byok_remote_select_provider_for_schema',
                providerSelect?.value
                    ? 'Select a remote model to load the model schema.'
                    : 'Select a provider instance to start loading models and schema.',
            ));
        }
    }

    /** Refresh state-derived copy after the shared data-i18n pass completes. */
    function handleByokI18nUpdated() {
        if (!state.domReady || !state.allow || !getByokRoot()) return;
        const focus = captureFocusedControl();
        renderProviderList();
        renderModelList();
        renderByokStatsContent();
        populateProviderSelect(document.getElementById('byokModelProviderInstance'));
        const remoteModelSelect = document.getElementById('byokRemoteModelSelect');
        remoteModelSelect?._singleSelect?.wrapper?._closeMenu?.();
        enhanceByokSelect(remoteModelSelect, 'byok_remote_model', {
            searchable: true,
            search: {
                enabled: true,
                placeholder: byokT('byok_remote_search_placeholder', 'Search remote models...'),
                noResultsMessage: byokT('byok_remote_search_empty', 'No matching remote models'),
            },
        });
        refreshProviderEditorTranslations();
        refreshModelEditorTranslations();
        if (state.dialogOpen) renderDialog();
        restoreFocusedControl(focus);
    }

    async function loadProviderSchema(providerKey, providerValues = null) {
        const requestToken = ++state.providerSchemaRequestToken;
        if (!providerKey || !state.providerSettingsHost) {
            state.providerSchema = null;
            state.providerBaseUrlSuggestions = [];
            applyProviderBaseUrlSuggestions([]);
            state.providerFormContext = renderSchemaFields(state.providerSettingsHost, { sections: [] }, {});
            return;
        }
        syncProviderBaseUrlField(providerKey);
        let rawSchema;
        try {
            rawSchema = await getCachedProviderSchema(providerKey);
        } catch (error) {
            // A rejected request is stale for the same reasons as a resolved
            // request. Swallow it after the editor closes or a newer provider
            // selection starts so the outer editor flow cannot show an error
            // notification for a form the user is no longer viewing.
            if (requestToken !== state.providerSchemaRequestToken || !state.providerModalOpen) {
                return;
            }
            throw error;
        }
        // Ignore a response when the user closed the editor or selected a
        // different provider while this request was in flight.
        if (requestToken !== state.providerSchemaRequestToken || !state.providerModalOpen) {
            return;
        }
        const schema = sanitizeProviderSchemaForByok(rawSchema, providerKey);
        state.providerSchema = schema;
        state.providerBaseUrlSuggestions = extractProviderUrlSuggestions(rawSchema);
        applyProviderBaseUrlSuggestions(state.providerBaseUrlSuggestions);
        // Seed the root icon independently from provider settings. This makes
        // each new provider visibly select its brand logo and keeps a saved
        // custom icon selected when the provider is edited later.
        const settingsValues = {
            icon: resolveProviderIcon(providerKey, providerValues?.icon),
            ...(providerValues?.settings ? { settings: providerValues.settings } : {}),
        };
        state.providerFormContext = renderSchemaFields(state.providerSettingsHost, schema, settingsValues);
        document.getElementById('byokProviderBaseUrl')?._providerUrlSuggestionSync?.();
    }

    function syncProviderBaseUrlField(providerType) {
        const baseUrlInput = document.getElementById('byokProviderBaseUrl');
        const baseUrlRow = baseUrlInput?.closest('.us-setting-item');
        if (!baseUrlRow) {
            return;
        }
        const normalized = trimString(providerType).toLowerCase();
        const show = [
            'openai_responses',
            'xai',
            'openai_chat_completions',
            'anthropic_base',
            'ollama',
            'lmstudio',
        ].includes(normalized);
        baseUrlRow.style.display = show ? '' : 'none';
        if (!show && baseUrlInput) {
            baseUrlInput.value = '';
        }
    }

    function resetProviderEditor() {
        state.providerEditingId = null;
        state.providerModalOpen = false;
        const type = document.getElementById('byokProviderType');
        const name = document.getElementById('byokProviderName');
        const baseUrl = document.getElementById('byokProviderBaseUrl');
        const apiKey = document.getElementById('byokProviderApiKey');
        const title = document.getElementById('byokProviderEditorTitle');
        const subtitle = document.getElementById('byokProviderEditorSubtitle');
        const saveLabel = document.getElementById('byokProviderSaveButtonLabel');
        if (title) title.textContent = byokT('byok_provider_editor_title_add', 'Add Provider Instance');
        if (subtitle) subtitle.textContent = byokT(
            'byok_provider_editor_subtitle_add_session',
            'Create a reusable local provider connection. The key is sealed by the server for reloads in this tab.',
        );
        if (saveLabel) saveLabel.textContent = byokT('byok_provider_save', 'Save Provider');
        if (type) type.value = PROVIDER_OPTIONS[0]?.value || 'openai';
        if (name) name.value = '';
        if (baseUrl) baseUrl.value = '';
        if (apiKey) {
            apiKey.value = '';
            apiKey.placeholder = byokT('byok_provider_api_key_placeholder_session', 'Enter API key for this session');
        }
        syncEnhancedSelect(type);
        clearByokControlError(type);
        clearByokControlError(name);
        syncProviderBaseUrlField(type?.value || PROVIDER_OPTIONS[0]?.value || 'openai');
        // Resetting a hidden form must be side-effect free. The schema is
        // loaded only after openProviderEditor makes the modal visible.
        state.providerSchemaRequestToken += 1;
        state.providerSchema = null;
        state.providerBaseUrlSuggestions = [];
        applyProviderBaseUrlSuggestions([]);
        state.providerFormContext = renderSchemaFields(state.providerSettingsHost, { sections: [] }, {});
    }

    async function openProviderEditor(providerId) {
        if (!providerId) {
            resetProviderEditor();
            state.providerModalOpen = true;
            openOverlay('providerOverlay');
            requestAnimationFrame(() => focusByokControl(document.getElementById('byokProviderType')));
            try {
                await loadProviderSchema(document.getElementById('byokProviderType')?.value || 'openai');
            } catch (error) {
                notifyError(error.message || byokT('byok_provider_schema_load_failed', 'Failed to load provider schema.'));
            }
            return;
        }
        const provider = getProviderById(providerId);
        if (!provider) return;
        state.providerEditingId = provider.id;
        state.providerModalOpen = true;
        const type = document.getElementById('byokProviderType');
        const name = document.getElementById('byokProviderName');
        const baseUrl = document.getElementById('byokProviderBaseUrl');
        const apiKey = document.getElementById('byokProviderApiKey');
        const title = document.getElementById('byokProviderEditorTitle');
        const subtitle = document.getElementById('byokProviderEditorSubtitle');
        const saveLabel = document.getElementById('byokProviderSaveButtonLabel');
        if (title) title.textContent = byokT('byok_provider_editor_title_edit', 'Edit Provider Instance');
        if (subtitle) subtitle.textContent = byokT(
            'byok_provider_editor_subtitle_edit_session',
            'Edit this existing local provider connection. Enter a new credential only to replace the current one.',
        );
        if (saveLabel) saveLabel.textContent = byokT('byok_action_save_changes', 'Save Changes');
        if (type) type.value = provider.provider;
        if (name) name.value = provider.name || '';
        if (baseUrl) baseUrl.value = provider.base_url || '';
        if (apiKey) {
            apiKey.value = '';
            apiKey.placeholder = getProviderCredentialToken(provider)
                ? byokT('byok_provider_api_key_placeholder_keep_session', 'Leave empty to keep the current session key')
                : byokT('byok_provider_api_key_placeholder_session', 'Enter API key for this session');
        }
        syncProviderBaseUrlField(provider.provider);
        // Clear fields from a previously opened provider before showing this
        // provider's cached or freshly loaded schema.
        state.providerSchemaRequestToken += 1;
        state.providerSchema = null;
        state.providerBaseUrlSuggestions = [];
        applyProviderBaseUrlSuggestions([]);
        state.providerFormContext = renderSchemaFields(state.providerSettingsHost, { sections: [] }, {});
        openOverlay('providerOverlay');
        syncEnhancedSelect(type);
        requestAnimationFrame(() => focusByokControl(document.getElementById('byokProviderName')));
        try {
            await loadProviderSchema(provider.provider, provider);
        } catch (error) {
            notifyError(error.message || byokT('byok_provider_schema_load_failed', 'Failed to load provider schema.'));
        }
    }

    function closeProviderModal() {
        state.providerModalOpen = false;
        // Invalidate any slow response so it cannot mutate the hidden editor.
        state.providerSchemaRequestToken += 1;
        closeOverlay('providerOverlay');
    }

    function takeProviderApiKeyForSave(input) {
        const apiKey = String(input?.value || '').trim();
        if (apiKey && input) {
            // Chrome deliberately ignores autocomplete="off" for fields it
            // classifies as login passwords. Ensure no key is present when its
            // asynchronous submission heuristics inspect the mounted form.
            input.value = '';
            input.disabled = true;
        }
        return apiKey;
    }

    function restoreProviderApiKeyAfterFailedSave(input, apiKey) {
        if (!input || !apiKey) return;
        input.disabled = false;
        input.value = apiKey;
    }

    async function saveProvider() {
        const providerType = trimString(document.getElementById('byokProviderType')?.value);
        const name = trimString(document.getElementById('byokProviderName')?.value);
        const baseUrl = trimString(document.getElementById('byokProviderBaseUrl')?.value);
        const providerSettingsValues = collectContextValues(state.providerFormContext);
        const providerIcon = resolveProviderIcon(providerType, providerSettingsValues.icon);
        const settings = providerSettingsValues.settings && typeof providerSettingsValues.settings === 'object'
            ? (stripEmptyValues(providerSettingsValues.settings) || {})
            : {};

        if (!providerType) {
            reportByokControlError(
                document.getElementById('byokProviderType'),
                byokT('byok_provider_type_required', 'Select a provider type.'),
            );
            return;
        }
        if (!name) {
            reportByokControlError(
                document.getElementById('byokProviderName'),
                byokT('byok_provider_name_required', 'Provider name is required.'),
            );
            return;
        }

        const existingProvider = state.providerEditingId
            ? getProviderById(state.providerEditingId)
            : null;
        const apiKeyInput = document.getElementById('byokProviderApiKey');
        const apiKey = takeProviderApiKeyForSave(apiKeyInput);
        const payload = {
            id: state.providerEditingId || generateLocalId('byok_provider'),
            provider: providerType,
            name,
            icon: providerIcon,
            base_url: baseUrl,
            settings,
        };
        const saveButton = document.getElementById('byokProviderSaveButton');
        if (saveButton) saveButton.disabled = true;
        try {
            // An empty password field keeps a valid sealed token. A newly
            // entered key is sent once to the authenticated sealing endpoint;
            // only the returned opaque token reaches browser storage.
            if (apiKey) {
                await issueProviderCredentialToken(payload.id, payload.provider, apiKey);
            } else if (existingProvider && normalizeProviderType(existingProvider.provider) !== normalizeProviderType(payload.provider)) {
                // Tokens are cryptographically bound to their provider type.
                deleteProviderCredentialToken(payload.id);
            }
        } catch (error) {
            restoreProviderApiKeyAfterFailedSave(apiKeyInput, apiKey);
            notifyError(error.message || byokT(
                'byok_credential_unavailable',
                'Your saved BYOK credential is unavailable. Re-enter the API key.',
            ));
            return;
        } finally {
            if (apiKeyInput) apiKeyInput.disabled = false;
            if (saveButton) saveButton.disabled = false;
        }

        const providers = getProviders();
        const nextProviders = providers.filter((provider) => String(provider.id) !== String(payload.id));
        nextProviders.push(payload);
        state.data.providers = nextProviders;
        saveStorage();
        renderProviderList();
        renderModelList();
        closeProviderModal();
        resetProviderEditor();
        // Chrome treats a password form disappearing after a successful fetch
        // as a login-submission signal. Keep the cleared editor mounted while
        // still refreshing every affected UI and external model consumer.
        notifyModelChange({ rerender: false });
    }

    async function deleteProvider(providerId) {
        const provider = getProviderById(providerId);
        const confirmed = await showDialog({
            title: translationRef('byok_provider_delete_title', 'Delete provider instance?'),
            description: translationRef(
                'byok_provider_delete_desc',
                'Remove {name} and any BYOK models using it. This action cannot be undone.',
                {
                    name: provider?.name
                        || translationRef('byok_provider_fallback', 'this provider'),
                },
            ),
            confirmLabel: translationRef('byok_provider_delete_confirm', 'Delete Provider'),
            cancelLabel: translationRef('byok_action_cancel', 'Cancel'),
            variant: 'danger',
        });
        if (!confirmed) return;
        state.data.providers = getProviders().filter((provider) => String(provider.id) !== String(providerId));
        deleteProviderCredentialToken(providerId);
        state.data.models = (Array.isArray(state.data.models) ? state.data.models : [])
            .filter((model) => String(model.provider_instance_id) !== String(providerId));
        saveStorage();
        renderProviderList();
        renderModelList();
        resetProviderEditor();
        resetModelEditor();
        notifyModelChange();
    }

    async function loadModelSchemaForEditor({ modelName, requestToken, resetForSelectedModel = false } = {}) {
        const providerInstanceId = trimString(document.getElementById('byokModelProviderInstance')?.value);
        const provider = getProviderById(providerInstanceId);
        if (!provider) {
            notifyError(byokT('byok_select_provider_first', 'Select a provider instance first.'));
            return;
        }

        const storedValues = resetForSelectedModel
            ? {}
            : (state.modelEditingId
            ? deepClone((Array.isArray(state.data.models) ? state.data.models : []).find((item) => String(item.model_id) === String(state.modelEditingId)) || {})
            : {});
        const currentValues = resetForSelectedModel ? {} : collectContextValues(state.modelFormContext);
        const existingValues = {
            ...storedValues,
            ...currentValues,
            settings: {
                ...(storedValues.settings || {}),
                ...(currentValues.settings || {}),
            },
        };

        const remoteModel = modelName !== undefined
            ? String(modelName || '')
            : (document.getElementById('byokRemoteModelSelect')?.value || '');
        const candidateModelName = trimString(remoteModel)
            || trimString(existingValues.model_name)
            || trimString(getNestedValue(currentValues, 'model_name'));
        const remoteMatch = getRemoteModelMatch(candidateModelName);
        const remotePrefill = buildRemoteModelPrefill(provider, remoteMatch, candidateModelName);
        const existingTools = getNestedValue(existingValues, 'tools');
        const currentTools = getNestedValue(currentValues, 'tools');
        const tools = Array.isArray(currentTools) && currentTools.length
            ? currentTools
            : (Array.isArray(existingTools) ? existingTools : []);

        try {
            const payload = await fetchModelSchemaPayload({
                provider: provider.provider,
                modelName: candidateModelName || null,
                modelInfo: remoteMatch,
                tools,
            });
            if (requestToken && requestToken !== state.remoteModelsRequestToken) {
                return;
            }
            const baseSchema = payload?.schema || { sections: [] };
            const mergedValues = {
                ...remotePrefill,
                ...existingValues,
                settings: {
                    ...((payload?.defaults && payload.defaults.settings) || {}),
                    ...(existingValues.settings || {}),
                },
            };
            if (candidateModelName) {
                mergedValues.model_name = candidateModelName;
            }
            state.modelSchema = baseSchema;
            state.modelFormContext = renderSchemaFields(
                state.modelSettingsHost,
                applySchemaValues(baseSchema, mergedValues),
                mergedValues,
            );
            const modelNameControl = state.modelFormContext?.controls?.get('model_name')?.control;
            modelNameControl?.addEventListener('input', () => clearByokControlError(modelNameControl));
            if (remoteMatch) {
                seedModelFromRemoteMatch(remoteMatch, false);
            }
        } catch (error) {
            notifyError(error.message || byokT('byok_model_schema_load_failed', 'Failed to load model schema.'));
        }
    }

    async function syncModelEditorForSelectedProvider({ preferredModelName = '' } = {}) {
        const providerId = trimString(document.getElementById('byokModelProviderInstance')?.value);
        const selectedModelName = trimString(preferredModelName);
        const requestToken = state.remoteModelsRequestToken + 1;
        state.remoteModelsRequestToken = requestToken;
        state.remoteModels = [];
        state.remoteModelsLoading = false;
        state.remoteModelsManualMode = false;
        state.remoteModelsStatus = null;
        state.modelSchema = null;
        renderModelSchemaPlaceholder(byokT('byok_remote_select_model_for_schema', 'Select a remote model to load the model schema.'));

        if (!providerId) {
            updateRemoteModelSelect();
            renderModelSchemaPlaceholder(byokT('byok_remote_select_provider_for_schema', 'Select a provider instance to start loading models and schema.'));
            return;
        }

        state.remoteModelsLoading = true;
        updateRemoteModelSelect(selectedModelName);

        try {
            const models = await fetchRemoteModelsForProvider(providerId);
            if (requestToken !== state.remoteModelsRequestToken) {
                return;
            }
            state.remoteModelsLoading = false;
            if (Array.isArray(models) && models.length) {
                state.remoteModels = models;
                state.remoteModelsManualMode = false;
                state.remoteModelsStatus = null;
            } else {
                state.remoteModels = [];
                state.remoteModelsManualMode = true;
                state.remoteModelsStatus = translationRef(
                    'byok_remote_no_models_manual',
                    'This provider returned no models. Manual mode is enabled, so enter the model name in the form below.',
                );
            }
        } catch (error) {
            if (requestToken !== state.remoteModelsRequestToken) {
                return;
            }
            console.error('Failed to load remote BYOK models:', error);
            state.remoteModelsLoading = false;
            state.remoteModels = [];
            state.remoteModelsManualMode = true;
            const detailKey = trimString(error?.code);
            const detail = detailKey
                ? translationRef(detailKey, trimString(error?.message))
                : null;
            state.remoteModelsStatus = detail
                ? translationRef(
                    'byok_remote_failed_manual_detail',
                    '{detail} Manual mode is enabled, so enter the model name in the form below.',
                    { detail },
                )
                : translationRef(
                    'byok_remote_failed_manual',
                    'Failed to load models from this provider. Manual mode is enabled, so enter the model name in the form below.',
                );
        }

        updateRemoteModelSelect(selectedModelName);

        if (requestToken !== state.remoteModelsRequestToken) {
            return;
        }

        if (selectedModelName) {
            await loadModelSchemaForEditor({
                modelName: selectedModelName,
                requestToken,
            });
        } else if (state.remoteModelsManualMode) {
            await loadModelSchemaForEditor({
                requestToken,
            });
        } else {
            renderModelSchemaPlaceholder(byokT('byok_remote_select_model_for_schema', 'Select a remote model to load the model schema.'));
        }
    }

    function seedModelFromRemoteMatch(remoteMatch, overwriteExisting = true) {
        if (!state.modelFormContext) return;
        const nameEntry = state.modelFormContext.controls.get('name');
        const modelNameEntry = state.modelFormContext.controls.get('model_name');
        const descriptionEntry = state.modelFormContext.controls.get('description');
        const currentName = nameEntry ? String(nameEntry.control.value || '').trim() : '';
        const currentDescription = descriptionEntry ? String(descriptionEntry.control.value || '').trim() : '';

        if (modelNameEntry) {
            modelNameEntry.control.value = getRemoteModelIdentifier(remoteMatch);
        }
        if (nameEntry && (overwriteExisting || !currentName)) {
            nameEntry.control.value = String(
                remoteMatch.name
                || remoteMatch.label
                || remoteMatch.title
                || remoteMatch.display_name
                || ''
            );
        }
        if (descriptionEntry && (overwriteExisting || !currentDescription)) {
            descriptionEntry.control.value = normalizeModelDescription(
                remoteMatch.description
                || remoteMatch.summary
                || remoteMatch.short_description
                || remoteMatch.details
                || ''
            );
        }
    }

    async function handleRemoteModelSelection(event) {
        const value = trimString(event?.target?.value);
        if (!value) return;
        await loadModelSchemaForEditor({
            modelName: value,
            requestToken: state.remoteModelsRequestToken,
            resetForSelectedModel: true,
        });
    }

    function resetModelEditor() {
        state.modelEditingId = null;
        state.modelModalOpen = false;
        state.remoteModelsRequestToken += 1;
        state.remoteModels = [];
        state.remoteModelsLoading = false;
        state.remoteModelsManualMode = false;
        state.remoteModelsStatus = null;
        updateRemoteModelSelect();
        const title = document.getElementById('byokModelEditorTitle');
        const subtitle = document.getElementById('byokModelEditorSubtitle');
        const saveLabel = document.getElementById('byokModelSaveButtonLabel');
        if (title) title.textContent = byokT('byok_model_editor_title_add', 'Add BYOK Model');
        if (subtitle) subtitle.textContent = byokT('byok_model_editor_subtitle_add', 'Build a local model definition with remote model discovery and schema-aware settings.');
        if (saveLabel) saveLabel.textContent = byokT('byok_model_save', 'Save BYOK Model');
        const providerSelect = document.getElementById('byokModelProviderInstance');
        if (providerSelect) {
            populateProviderSelect(providerSelect);
            providerSelect.value = '';
            syncEnhancedSelect(providerSelect);
            clearByokControlError(providerSelect);
        }
        state.modelSchema = null;
        renderModelSchemaPlaceholder(byokT('byok_remote_select_provider_for_schema', 'Select a provider instance to start loading models and schema.'));
    }

    async function openModelEditor(modelId) {
        if (!modelId) {
            resetModelEditor();
            state.modelModalOpen = true;
            openOverlay('modelOverlay');
            requestAnimationFrame(() => focusByokControl(document.getElementById('byokModelProviderInstance')));
            return;
        }
        const stored = (Array.isArray(state.data.models) ? state.data.models : [])
            .find((model) => String(model.model_id) === String(modelId));
        if (!stored) return;
        state.modelEditingId = stored.model_id;
        state.modelModalOpen = true;
        const title = document.getElementById('byokModelEditorTitle');
        const subtitle = document.getElementById('byokModelEditorSubtitle');
        const saveLabel = document.getElementById('byokModelSaveButtonLabel');
        if (title) title.textContent = byokT('byok_model_editor_title_edit', 'Edit BYOK Model');
        if (subtitle) subtitle.textContent = byokT('byok_model_editor_subtitle_edit', 'Update the model definition and provider-specific settings.');
        if (saveLabel) saveLabel.textContent = byokT('byok_action_save_changes', 'Save Changes');
        const providerSelect = document.getElementById('byokModelProviderInstance');
        if (providerSelect) {
            populateProviderSelect(providerSelect);
            providerSelect.value = stored.provider_instance_id || '';
            syncEnhancedSelect(providerSelect);
        }
        openOverlay('modelOverlay');
        requestAnimationFrame(() => focusByokControl(providerSelect));
        await syncModelEditorForSelectedProvider({ preferredModelName: stored.model_name || '' });
    }

    function closeModelModal() {
        state.modelModalOpen = false;
        closeOverlay('modelOverlay');
    }

    function normalizeToolList(rawTools) {
        if (Array.isArray(rawTools)) {
            return rawTools.map((entry) => String(entry || '').trim()).filter(Boolean);
        }
        if (typeof rawTools === 'string') {
            return parseListValue(rawTools);
        }
        return [];
    }

    function saveModel() {
        const providerInstanceId = trimString(document.getElementById('byokModelProviderInstance')?.value);
        const provider = getProviderById(providerInstanceId);
        if (!provider) {
            reportByokControlError(
                document.getElementById('byokModelProviderInstance'),
                byokT('byok_model_provider_required', 'Select a provider instance.'),
            );
            return;
        }
        const values = collectContextValues(state.modelFormContext);
        const modelName = trimString(values.model_name);
        const displayName = trimString(values.name) || modelName;
        if (!modelName) {
            reportByokControlError(
                state.modelFormContext?.controls?.get('model_name')?.control,
                byokT('byok_model_name_required', 'Model name is required.'),
            );
            return;
        }

        const remoteMatch = getRemoteModelMatch(modelName);
        const modelPayload = {
            model_id: state.modelEditingId || generateLocalId('byok_model'),
            provider_instance_id: providerInstanceId,
            provider: provider.provider,
            name: displayName,
            description: trimString(values.description),
            model_name: modelName,
            model_icon: sanitizeIconValue(values.model_icon) || resolveProviderIcon(provider.provider, provider.icon),
            tools: normalizeToolList(values.tools),
            settings: values.settings && typeof values.settings === 'object'
                ? (stripEmptyValues(values.settings) || {})
                : {},
            capabilities: Array.isArray(remoteMatch?.capabilities) ? remoteMatch.capabilities : (Array.isArray(values.capabilities) ? values.capabilities : []),
            status: trimString(values.status) || 'normal',
        };

        if (!modelPayload.settings.websearch_scrape_provider && state.defaultScrapeProvider) {
            modelPayload.settings.websearch_scrape_provider = state.defaultScrapeProvider;
        }
        if (!modelPayload.settings.websearch_search_provider && state.defaultSearchProvider) {
            modelPayload.settings.websearch_search_provider = state.defaultSearchProvider;
        }

        const models = Array.isArray(state.data.models) ? state.data.models : [];
        const nextModels = models.filter((model) => String(model.model_id) !== String(modelPayload.model_id));
        nextModels.push(modelPayload);
        state.data.models = nextModels;
        saveStorage();
        renderModelList();
        closeModelModal();
        resetModelEditor();
        notifyModelChange();
    }

    async function deleteModel(modelId) {
        const model = getLocalModelById(modelId);
        const confirmed = await showDialog({
            title: translationRef('byok_model_delete_title', 'Delete BYOK model?'),
            description: translationRef(
                'byok_model_delete_desc',
                'Remove {name} from your local BYOK setup. This action cannot be undone.',
                {
                    name: model?.name
                        || model?.model_name
                        || translationRef('byok_model_fallback', 'this model'),
                },
            ),
            confirmLabel: translationRef('byok_model_delete_confirm', 'Delete Model'),
            cancelLabel: translationRef('byok_action_cancel', 'Cancel'),
            variant: 'danger',
        });
        if (!confirmed) return;
        state.data.models = (Array.isArray(state.data.models) ? state.data.models : [])
            .filter((model) => String(model.model_id) !== String(modelId));
        if (String(getStoredSelectedModelId() || '') === String(modelId)) {
            setStoredSelectedModelId(null);
        }
        saveStorage();
        renderModelList();
        resetModelEditor();
        notifyModelChange();
    }

    function setPolicy(data = {}) {
        state.allow = Boolean(data.allow_byok);
        state.defaultScrapeProvider = trimString(data.byok_default_scrape_provider);
        state.defaultSearchProvider = trimString(data.byok_default_search_provider);
        state.byokStatisticsEnabled = Boolean(data.byok_statistics_enabled);
        if (Number.isFinite(Number(data.byok_statistics_retention_days))) {
            state.byokStatisticsRetentionDays = Math.max(1, Math.round(Number(data.byok_statistics_retention_days)));
        }
        if (!state.byokStatisticsEnabled) {
            resetByokStatsData();
        }
        applyVisibility();
        renderRoot();
    }

    async function getModelSettingsSchemaForModel(modelId) {
        const model = getLocalModelById(modelId);
        if (!model) {
            return null;
        }
        const payload = await fetchModelSchemaPayload({
            provider: model.provider,
            modelName: model.model_name,
            modelInfo: getRemoteModelMatch(model.model_name),
            tools: model.tools || [],
        });
        return {
            supported: true,
            schema: stripRuntimeSchemaFields(applySchemaValues(payload?.schema || { sections: [] }, {
                name: model.name,
                description: model.description,
                model_icon: model.model_icon,
                model_name: model.model_name,
                tools: model.tools,
                status: model.status,
                settings: model.settings || {},
            })),
        };
    }

    function buildRequestPayloadForModel(modelId, currentSidebarSettings) {
        const model = getLocalModelById(modelId);
        if (!model || !state.allow) {
            return null;
        }
        const provider = getProviderById(model.provider_id);
        if (!provider) {
            throw new Error(byokT('byok_provider_instance_missing', 'BYOK provider instance is missing.'));
        }
        const runtimeSettings = currentSidebarSettings && typeof currentSidebarSettings === 'object' && currentSidebarSettings.settings && typeof currentSidebarSettings.settings === 'object'
            ? currentSidebarSettings.settings
            : {};
        const mergedSettings = sanitizeAnthropicModelSettings(provider.provider, {
            ...(model.settings || {}),
            ...runtimeSettings,
        });
        if (!mergedSettings.websearch_scrape_provider && state.defaultScrapeProvider) {
            mergedSettings.websearch_scrape_provider = state.defaultScrapeProvider;
        }
        if (!mergedSettings.websearch_search_provider && state.defaultSearchProvider) {
            mergedSettings.websearch_search_provider = state.defaultSearchProvider;
        }
        const credentialToken = getProviderCredentialToken(provider);
        if (!credentialToken && !OPTIONAL_API_KEY_PROVIDERS.has(normalizeProviderType(provider.provider))) {
            throw new Error(byokT(
                'byok_credential_unavailable',
                'Your saved BYOK credential is unavailable. Re-enter the API key.',
            ));
        }
        return {
            provider: provider.provider,
            provider_name: provider.name,
            provider_label: provider.name,
            provider_id: provider.id,
            model_name: model.model_name,
            credential_token: credentialToken || undefined,
            base_url: provider.base_url || '',
            settings: mergedSettings,
            tools: Array.isArray(model.tools) ? model.tools : [],
            capabilities: Array.isArray(model.capabilities) ? model.capabilities : [],
            provider_settings: provider.settings || {},
            ...(provider.settings || {}),
        };
    }

    function initDom() {
        state.domReady = true;
        applyVisibility();
        renderRoot();
        void migrateLegacyProviderSecrets();
    }

    document.addEventListener('keydown', trapByokModalFocus);
    document.addEventListener('i18n:updated', handleByokI18nUpdated);

    if (typeof window !== 'undefined' && window.registerEscapeHandler) {
        window.registerEscapeHandler({
            id: 'byok-modals',
            priority: 205,
            isActive: () => state.providerModalOpen || state.modelModalOpen || state.dialogOpen,
            close: () => {
                closeTopSurface();
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDom, { once: true });
    } else {
        initDom();
    }

    if (typeof window !== 'undefined') {
        window.BYOK = {
            setPolicy,
            isAllowed: () => state.allow,
            setAdminModels,
            getAllSelectableModels,
            getLocalModels,
            getLocalModelById,
            isByokModelId,
            getModelSettingsSchemaForModel,
            buildRequestPayloadForModel,
            getStoredSelectedModelId,
            setStoredSelectedModelId,
            refreshStatistics,
            clearProviderSessionCredentials,
        };
    }
})();
