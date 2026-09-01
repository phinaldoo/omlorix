(() => {

    const selectsByField = new Map();
    let isInitializing = false;

    const selectSettingsT = (key, fallback) => {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    const SUPPORTED_FIELDS = new Set([
        'profile_visibility',
        'language',
        'country',
        'timezone',
        'font',
    ]);

    const SUPPORTED_LANGUAGES = new Set([
        'en',
        'de',
        'es',
        'fr',
        'zh',
        'hi',
        'ar',
        'ja',
        'it',
        'pt',
        'ru'
    ]);

    /**
     * Expand the timezone setting with the same complete IANA option list used
     * by administrator rate-limit forms. The existing value is retained while
     * the shared custom-select adapter rebuilds its native options.
     */
    const populateTimeZoneOptions = (persistedValue = '') => {
        if (typeof document.querySelector !== 'function') {
            return;
        }
        const timeZoneRoot = document
            .querySelector('.select-trigger[data-field="timezone"], .admin-select-trigger[data-field="timezone"]')
            ?.closest('.custom-select');
        const timeZoneHelpers = window.OmlorixTimeZones;
        if (!timeZoneRoot || !timeZoneHelpers || typeof window.refreshCustomSelect !== 'function') {
            return;
        }
        const normalizedPersistedValue = String(persistedValue || '').trim();
        const persistedOptionExists = Array.from(timeZoneRoot.querySelectorAll('.select-option'))
            .some((option) => option.dataset.value === normalizedPersistedValue);
        if (timeZoneRoot.dataset.timeZoneOptionsReady === 'true'
            && (!normalizedPersistedValue || persistedOptionExists)) {
            return;
        }

        const existingValue =
            window.getCustomSelectValue?.('timezone')
            || timeZoneRoot.querySelector('.select-option.selected')?.dataset.value
            || timeZoneRoot.querySelector('.select-option')?.dataset.value
            || timeZoneHelpers.getBrowserTimeZone();
        const options = timeZoneHelpers.getSupportedTimeZoneOptions([
            existingValue,
            normalizedPersistedValue,
        ]);
        const preferredValue = normalizedPersistedValue || existingValue;
        const selectedValue = options.some((option) => option.value === preferredValue)
            ? preferredValue
            : timeZoneHelpers.getBrowserTimeZone();

        const refreshed = window.refreshCustomSelect(timeZoneRoot, {
            options,
            value: selectedValue,
        });
        if (refreshed) {
            timeZoneRoot.dataset.timeZoneOptionsReady = 'true';
        }
    };

    const setSavingState = (config, isSaving) => {
        config.isSaving = isSaving;
        const { selectElement, trigger } = config;
        if (trigger) {
            trigger.classList.toggle('is-saving', isSaving);
            trigger.setAttribute('aria-busy', isSaving ? 'true' : 'false');
            trigger.setAttribute('aria-disabled', isSaving ? 'true' : 'false');
        }
        if (selectElement) {
            selectElement.classList.toggle('is-saving', isSaving);
        }
    };

    const applyValueToSelect = (config, value) => {
        config.currentValue = value;

        if (typeof window.setCustomSelectValue === 'function') {
            window.setCustomSelectValue(config.field, value ?? '');
        } else if (config.trigger) {
            const span = config.trigger.querySelector('span');
            if (span) {
                const options = config.trigger.nextElementSibling;
                const option = options ? options.querySelector(`.select-option[data-value="${value}"]`) : null;
                span.textContent = option ? option.textContent : span.textContent;
            }
        }
    };

    const applyImmediateAction = (field, value) => {
        if (field === 'language') {
            if (value && SUPPORTED_LANGUAGES.has(value)
                && typeof window.applyUserLanguagePreference === 'function') {
                // The saved setting is also a direct user choice. Let the
                // shared i18n helper update the active dictionary and protect
                // it from a late auth-bootstrap event.
                void window.applyUserLanguagePreference(value, { source: 'user' });
                return;
            }
            try {
                if (value && SUPPORTED_LANGUAGES.has(value)) {
                    localStorage.setItem('lang', value);
                } else {
                    localStorage.removeItem('lang');
                }
            } catch (error) {
                console.warn('Failed to update language in localStorage:', error);
            }
            initI18n(true);
        } else if (field === 'font') {
            setFontFamilyPreference(value);
        }
    };

    const syncSuccessfulSelectUpdate = (field, value, payload) => {
        // Settings init data is shared across modules while the page stays open.
        // Invalidate it after a save so reopening User Settings reads the freshly
        // persisted select value instead of replaying the old cached payload.
        if (typeof window.SharedDataCache?.clear === 'function') {
            window.SharedDataCache.clear('userSettingsInit');
        }

        // The chat bootstrap payload uses font_family for the applied font, while
        // User Settings uses font. Keep both live objects aligned until the next
        // full bootstrap fetch happens on page reload.
        if (field === 'font' && window.chatSetup && typeof window.chatSetup === 'object') {
            const updatedFont = payload?.updated?.font || value;
            window.chatSetup.font = updatedFont;
            window.chatSetup.font_family = updatedFont;
        }
    };

    const ensureSelectRegistered = (selectElement) => {
        if (!selectElement) {
            return;
        }

        const state = selectElement.__customSelectState || null;
        const trigger = state?.trigger || selectElement.querySelector('.admin-select-trigger[data-field], .select-trigger[data-field]');
        if (!trigger) {
            return;
        }

        const field = state?.field || trigger.dataset.field;
        if (!field || !SUPPORTED_FIELDS.has(field) || selectsByField.has(field)) {
            return;
        }

        const config = {
            field,
            selectElement,
            trigger,
            currentValue: undefined,
            isSaving: false
        };

        const onCustomSelectChange = async (event) => {
            if (isInitializing) {
                return;
            }

            const { detail } = event;
            if (!detail || detail.field !== field) {
                return;
            }

            const newValue = typeof detail.value === 'undefined' ? '' : detail.value;
            const previousValue = config.currentValue;

            if (config.isSaving) {
                if (typeof window.setCustomSelectValue === 'function') {
                    window.setCustomSelectValue(field, previousValue ?? '');
                }
                return;
            }

            if (newValue === previousValue) {
                return;
            }

            setSavingState(config, true);

            try {
                const payload = await uploadUserSettingsSelect(field, newValue);
                const savedValue = payload?.updated?.[field] ?? newValue;
                config.currentValue = savedValue;
                syncSuccessfulSelectUpdate(field, savedValue, payload);
                
                // Apply immediate actions for specific fields
                applyImmediateAction(field, savedValue);
            } catch (error) {
                if (typeof window.setCustomSelectValue === 'function') {
                    window.setCustomSelectValue(field, previousValue ?? '');
                }
                notifyError(selectSettingsT('user_settings_select_update_failed', 'Failed to update select setting'));
            } finally {
                setSavingState(config, false);
            }
        };

        selectElement.addEventListener('customSelectChange', onCustomSelectChange);
        selectsByField.set(field, config);
    };

    const registerAllSelects = () => {
        const selects = document.querySelectorAll('.custom-select');
        selects.forEach(ensureSelectRegistered);
    };

    const initUserSettingsSelect = (data = {}) => {
        populateTimeZoneOptions(data.timezone);
        registerAllSelects();

        isInitializing = true;

        Object.entries(data).forEach(([key, value]) => {
            const config = selectsByField.get(key);
            if (!config) {
                return;
            }
            if (typeof value === 'undefined' || value === null) {
                value = '';
            }
            applyValueToSelect(config, value);
        });

        isInitializing = false;

        // Applying a custom-select value does not emit its change event. The
        // server value therefore needs an explicit synchronization call so the
        // visible dropdown and the active dictionary cannot diverge.
        if (data.language && typeof window.applyAuthenticatedLanguage === 'function') {
            void window.applyAuthenticatedLanguage(data.language);
        }
    };


    const uploadUserSettingsSelect = async (key, value) => {
        const response = await window.authedFetch('/api/v1/users/settings/select', {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ [key]: value })
        });

        if (!response.ok) {
            const errorPayload = await response.json().catch(() => null);
            const detail = errorPayload?.detail || errorPayload?.message;
            throw new Error(detail || `Failed to update select setting (${response.status})`);
        }

        const payload = await response.json().catch(() => null);
        if (payload && payload.status && payload.status !== 'success') {
            throw new Error(`Unexpected select update status: ${payload.status}`);
        }

        return payload;
    };

    const init = () => {
        populateTimeZoneOptions();
        registerAllSelects();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }

    window.initUserSettingsSelect = initUserSettingsSelect;
    window.uploadUserSettingsSelect = uploadUserSettingsSelect;
})();
