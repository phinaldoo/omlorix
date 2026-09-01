(() => {
    const togglesByKey = new Map();
    let isInitializing = false;

    const toggleSettingsT = (key, fallback) => {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    const toggleSettingsTf = (key, fallback, vars = {}) => {
        if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(toggleSettingsT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars[token];
            return value === undefined || value === null ? '' : String(value);
        });
    };

    const persistLocalStorageValue = (key, value) => {
        try {
            localStorage.setItem(key, value);
        } catch (error) {
            console.warn(`Failed to persist ${key} to localStorage:`, error);
        }
    };

    const applyShowModelSettingsPreference = (value) => {
        if (typeof document === 'undefined') {
            return;
        }

        const boolValue = (value === true || value === 'true');
        persistLocalStorageValue('show_model_settings', boolValue ? 'true' : 'false');
        if (window.chatSetup && typeof window.chatSetup === 'object') {
            window.chatSetup.show_model_settings = boolValue;
        }
        if (typeof window.refreshModelSettingsVisibility === 'function') {
            window.refreshModelSettingsVisibility();
        }
    };

    const applyAssistantMessageMetadataPreference = (value) => {
        if (typeof document === 'undefined') {
            return;
        }

        const boolValue = (value === true || value === 'true');
        persistLocalStorageValue('show_assistant_message_metadata', boolValue ? 'true' : 'false');
        if (window.chatSetup && typeof window.chatSetup === 'object') {
            window.chatSetup.show_assistant_message_metadata = boolValue;
        }
        if (typeof window.refreshAssistantMetadataVisibility === 'function') {
            window.refreshAssistantMetadataVisibility();
        }
    };

    const applyMessageNavPreference = (value) => {
        if (typeof document === 'undefined') {
            return;
        }

        const boolValue = (value === true || value === 'true');
        persistLocalStorageValue('show_message_nav', boolValue ? 'true' : 'false');
        if (window.chatSetup && typeof window.chatSetup === 'object') {
            window.chatSetup.show_message_nav = boolValue;
        }
        if (window.MessageNav && typeof window.MessageNav.setEnabled === 'function') {
            window.MessageNav.setEnabled(boolValue);
        }
    };

    const applyAssistantMessageMarkdownPreference = (value) => {
        if (typeof document === 'undefined') {
            return;
        }

        const boolValue = (value === true || value === 'true');
        persistLocalStorageValue('render_assistant_messages_markdown', boolValue ? 'true' : 'false');

        document.querySelectorAll('.assistant-message-content').forEach(contentElement => {
            const rawContent = contentElement.getAttribute('data-raw-content');
            if (!rawContent) return;

            if (boolValue) {
                if (typeof window.renderMarkdownContent === 'function') {
                    window.renderMarkdownContent(contentElement, rawContent);
                }
            } else {
                contentElement.innerHTML = '';
                contentElement.textContent = rawContent;
                contentElement.classList.remove('markdown-body');
            }
        });
    };

    const applyUserMessageMarkdownPreference = (value) => {
        if (typeof document === 'undefined') {
            return;
        }

        const boolValue = (value === true || value === 'true');
        persistLocalStorageValue('render_user_messages_markdown', boolValue ? 'true' : 'false');
        

        // Re-render all existing user messages
        document.querySelectorAll('.user-message-content').forEach(contentElement => {
            const rawContent = contentElement.getAttribute('data-raw-content');
            if (!rawContent) return;

            if (boolValue) {
                // Render as markdown
                if (typeof window.renderMarkdownContent === 'function') {
                    window.renderMarkdownContent(contentElement, rawContent);
                }
            } else {
                // Render as plain text
                contentElement.innerHTML = '';
                contentElement.textContent = rawContent;
                contentElement.classList.remove('markdown-body');
            }
        });
    };

    const applyCtrlEnterToSendPreference = (value) => {
        const boolValue = (value === true || value === 'true');
        persistLocalStorageValue('ctrl_enter_to_send', boolValue ? 'true' : 'false');
        if (window.chatSetup && typeof window.chatSetup === 'object') {
            window.chatSetup.ctrl_enter_to_send = boolValue;
        }
    };

    const applyTemporaryChatPreference = (value) => {
        if (typeof document === 'undefined') {
            return;
        }

        const boolValue = value === true || value === 'true';
        persistLocalStorageValue('always_use_temporary_chat', boolValue ? 'true' : 'false');
        if (window.chatSetup && typeof window.chatSetup === 'object') {
            window.chatSetup.always_use_temporary_chat = boolValue;
        }
        if (typeof window.syncTemporaryChatModeWithPreference === 'function') {
            window.syncTemporaryChatModeWithPreference();
        }
    };

    const parseDatasetValue = (value, fallback) => {
        if (typeof value === 'undefined') {
            return fallback;
        }
        if (value === 'true') {
            return true;
        }
        if (value === 'false') {
            return false;
        }
        return value;
    };

    const valueToChecked = (value, config) => {
        if (typeof value === 'undefined') {
            return Boolean(config.defaultChecked);
        }
        return value === config.trueValue;
    };

    const checkedToValue = (checked, config) => {
        return checked ? config.trueValue : config.falseValue;
    };

    const setSavingState = (input, isSaving) => {
        input.disabled = isSaving;
        const container = input.closest('.toggle-switch');
        if (container) {
            container.classList.toggle('is-saving', isSaving);
        }
    };

    const ensureToggleRegistered = (input) => {
        if (!(input instanceof HTMLInputElement)) {
            return;
        }

        const key = input.dataset.settingKey;
        if (!key || togglesByKey.has(key)) {
            return;
        }

        const config = {
            key,
            input,
            trueValue: parseDatasetValue(input.dataset.settingTrueValue, true),
            falseValue: parseDatasetValue(input.dataset.settingFalseValue, false),
            defaultChecked: input.checked,
            currentValue: undefined,
            isSaving: false
        };

        const onChange = async (event) => {
            if (isInitializing) {
                return;
            }

            const target = event.target;
            if (!(target instanceof HTMLInputElement)) {
                return;
            }

            if (config.isSaving) {
                target.checked = valueToChecked(config.currentValue, config);
                return;
            }

            const newValue = checkedToValue(target.checked, config);
            const previousValue = config.currentValue;

            if (newValue === previousValue) {
                return;
            }

            config.isSaving = true;
            setSavingState(target, true);

            try {
                await uploadUserSettingsToogle(config.key, newValue);
                config.currentValue = newValue;
                
                // Apply immediate actions for specific toggle settings
                if (config.key === 'show_model_settings') {
                    applyShowModelSettingsPreference(config.currentValue);
                } else if (config.key === 'show_message_nav') {
                    applyMessageNavPreference(config.currentValue);
                } else if (config.key === 'show_assistant_message_metadata') {
                    applyAssistantMessageMetadataPreference(config.currentValue);
                } else if (config.key === 'chat_full_width' && typeof window.applyChatFullWidthPreference === 'function') {
                    window.applyChatFullWidthPreference(config.currentValue);
                } else if (config.key === 'render_user_messages_markdown') {
                    applyUserMessageMarkdownPreference(config.currentValue);
                } else if (config.key === 'render_assistant_messages_markdown') {
                    applyAssistantMessageMarkdownPreference(config.currentValue);
                } else if (config.key === 'ctrl_enter_to_send') {
                    applyCtrlEnterToSendPreference(config.currentValue);
                } else if (config.key === 'always_use_temporary_chat') {
                    applyTemporaryChatPreference(config.currentValue);
                }
            } catch (error) {
                target.checked = valueToChecked(previousValue, config);
                notifyError(config.key);
            } finally {
                config.isSaving = false;
                setSavingState(target, false);
            }
        };

        input.addEventListener('change', onChange);
        togglesByKey.set(key, config);
    };

    const registerAllToggles = () => {
        const inputs = document.querySelectorAll('input.toggle-input[data-setting-key]');
        inputs.forEach(ensureToggleRegistered);
    };

    const applyValueToToggle = (config, value) => {
        config.currentValue = value;
        config.input.checked = valueToChecked(value, config);
        
        // Apply immediate actions for specific toggle settings during initialization
        if (config.key === 'show_model_settings') {
            applyShowModelSettingsPreference(config.currentValue);
        } else if (config.key === 'show_message_nav') {
            applyMessageNavPreference(config.currentValue);
        } else if (config.key === 'show_assistant_message_metadata') {
            applyAssistantMessageMetadataPreference(config.currentValue);
        } else if (config.key === 'chat_full_width' && typeof window.applyChatFullWidthPreference === 'function') {
            window.applyChatFullWidthPreference(config.currentValue);
        } else if (config.key === 'render_user_messages_markdown') {
            applyUserMessageMarkdownPreference(config.currentValue);
        } else if (config.key === 'render_assistant_messages_markdown') {
            applyAssistantMessageMarkdownPreference(config.currentValue);
        } else if (config.key === 'ctrl_enter_to_send') {
            applyCtrlEnterToSendPreference(config.currentValue);
        } else if (config.key === 'always_use_temporary_chat') {
            applyTemporaryChatPreference(config.currentValue);
        }
    };

    const initUserSettingsToogle = (data = {}) => {
        if (!togglesByKey.size) {
            registerAllToggles();
        }

        isInitializing = true;

        Object.entries(data).forEach(([key, value]) => {
            const config = togglesByKey.get(key);
            if (!config) {
                return;
            }
            if (typeof value === 'undefined' || value === null) {
                return;
            }
            applyValueToToggle(config, value);
        });

        isInitializing = false;
    };

    const uploadUserSettingsToogle = async (key, value) => {
        const response = await window.authedFetch('/api/v1/users/settings/toogle', {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ [key]: value })
        });

        if (!response.ok) {
            const errorText = await response.text().catch(() => '');
            notifyError(toggleSettingsTf('user_settings_toggle_request_failed_status', 'Request failed with status {status}: {error}', {
                status: response.status,
                error: errorText,
            }));
        }

        const payload = await response.json().catch(() => null);
        if (payload && payload.status && payload.status !== 'success') {
            notifyError(toggleSettingsTf('user_settings_toggle_unexpected_status', 'Unexpected response status: {status}', {
                status: payload.status,
            }));
        }

        return payload;
    };

    const init = () => {
        registerAllToggles();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }

    window.initUserSettingsToogle = initUserSettingsToogle;
    window.uploadUserSettingsToogle = uploadUserSettingsToogle;
})();
