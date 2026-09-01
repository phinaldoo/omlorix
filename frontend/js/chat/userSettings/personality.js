(() => {
    const DISABLED_PRESET = 'none';
    const VALID_PRESETS = new Set([
        'none',
        'standard',
        'professional',
        'friendly',
        'honest',
        'quirky',
        'efficient',
        'cynical',
        'custom',
    ]);

    const state = {
        preset: DISABLED_PRESET,
        customInstruction: '',
        isBound: false,
        saveRequestId: 0,
        lastAppliedRequestId: 0,
        customSaveTimeoutId: undefined,
    };

    const personalityT = (key, fallback) => {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    const getElements = () => ({
        settingsCard: document.getElementById('personalitySettingsCard'),
        presetSelect: document.getElementById('personalityPresetSelect'),
        customField: document.getElementById('personalityCustomField'),
        customInstruction: document.getElementById('personalityCustomInstruction'),
        customHelper: document.getElementById('personalityCustomHelper'),
        customError: document.getElementById('personalityCustomError'),
    });

    const normalizePreset = (value) => {
        const normalized = String(value || '').trim().toLowerCase();
        return VALID_PRESETS.has(normalized) ? normalized : DISABLED_PRESET;
    };

    const normalizeInstruction = (value) => String(value || '').trim();

    const setPresetControlValue = (selectElement, value) => {
        if (!selectElement) {
            return;
        }

        if (selectElement.__customSelectState && typeof window.setCustomSelectValue === 'function') {
            window.setCustomSelectValue('personality_preset', value);
            return;
        }

        const option = selectElement.querySelector(`.select-option[data-value="${value}"]`);
        const triggerText = selectElement.querySelector('.select-trigger span');
        if (option && triggerText) {
            triggerText.textContent = option.textContent;
        }
        selectElement.querySelectorAll('.select-option').forEach((currentOption) => {
            const isSelected = currentOption === option;
            currentOption.classList.toggle('selected', isSelected);
            currentOption.setAttribute('aria-selected', isSelected ? 'true' : 'false');
        });
    };

    const syncStateFromPayload = (payload = {}) => {
        if (Object.prototype.hasOwnProperty.call(payload, 'personality_preset')) {
            state.preset = normalizePreset(payload.personality_preset);
        }
        if (Object.prototype.hasOwnProperty.call(payload, 'personality_custom_instruction')) {
            state.customInstruction = normalizeInstruction(payload.personality_custom_instruction);
        }
    };

    const render = () => {
        const elements = getElements();
        if (!elements.settingsCard || !elements.presetSelect) {
            return;
        }

        elements.settingsCard.classList.toggle('is-saving', state.saveRequestId > state.lastAppliedRequestId);
        elements.presetSelect.classList.toggle('is-saving', state.saveRequestId > state.lastAppliedRequestId);
        setPresetControlValue(elements.presetSelect, state.preset);

        const showCustomField = state.preset === 'custom';
        if (elements.customField) {
            elements.customField.hidden = !showCustomField;
            elements.customField.classList.toggle('is-visible', showCustomField);
        }

        if (elements.customInstruction && document.activeElement !== elements.customInstruction) {
            if (elements.customInstruction.value !== state.customInstruction) {
                elements.customInstruction.value = state.customInstruction;
            }
        }

        const customIsBlank = normalizeInstruction(state.customInstruction).length === 0;
        if (elements.customHelper) {
            elements.customHelper.hidden = !showCustomField || customIsBlank;
        }
        if (elements.customError) {
            elements.customError.hidden = !showCustomField || !customIsBlank;
        }
    };

    const parseErrorMessage = async (response) => {
        const fallbackResponse = response.clone();
        try {
            const payload = await response.json();
            return payload?.detail || payload?.message || personalityT(
                'user_settings_personality_request_failed_status',
                'Request failed with status {status}'
            ).replace('{status}', String(response.status));
        } catch (_) {
            const text = await fallbackResponse.text().catch(() => '');
            return text || personalityT(
                'user_settings_personality_request_failed_status',
                'Request failed with status {status}'
            ).replace('{status}', String(response.status));
        }
    };

    const savePersonalitySettings = async (payload) => {
        const requestId = ++state.saveRequestId;
        render();

        try {
            const response = await window.authedFetch('/api/v1/users/settings/personality', {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload),
            });

            if (!response.ok) {
                throw new Error(await parseErrorMessage(response));
            }

            const responsePayload = await response.json().catch(() => ({}));
            const updatedChat = responsePayload?.updated?.chat || {};
            if (requestId >= state.lastAppliedRequestId) {
                state.lastAppliedRequestId = requestId;
                syncStateFromPayload(updatedChat);
            }
        } catch (error) {
            console.error('Failed to save personality settings', error);
            if (typeof notifyError === 'function') {
                notifyError(error?.message || personalityT('user_settings_personality_save_failed', 'Failed to save personality settings'));
            }
        } finally {
            if (requestId > state.lastAppliedRequestId) {
                state.lastAppliedRequestId = requestId;
            }
            render();
        }
    };

    const clearScheduledCustomSave = () => {
        if (!state.customSaveTimeoutId) {
            return;
        }
        clearTimeout(state.customSaveTimeoutId);
        state.customSaveTimeoutId = undefined;
    };

    const scheduleCustomSave = () => {
        clearScheduledCustomSave();
        state.customSaveTimeoutId = setTimeout(() => {
            state.customSaveTimeoutId = undefined;
            savePersonalitySettings({ custom_instruction: state.customInstruction });
        }, 350);
    };

    const flushCustomSave = () => {
        if (!state.customSaveTimeoutId) {
            return;
        }
        clearScheduledCustomSave();
        savePersonalitySettings({ custom_instruction: state.customInstruction });
    };

    const bindEvents = () => {
        if (state.isBound) {
            return;
        }

        const elements = getElements();
        if (!elements.presetSelect || !elements.customInstruction) {
            return;
        }

        elements.presetSelect.addEventListener('customSelectChange', (event) => {
            const detail = event.detail || {};
            if (detail.field !== 'personality_preset') {
                return;
            }

            const nextPreset = normalizePreset(detail.value);
            if (nextPreset === state.preset) {
                render();
                return;
            }
            state.preset = nextPreset;
            render();
            savePersonalitySettings({ preset: state.preset });
        });

        elements.customInstruction.addEventListener('input', (event) => {
            const target = event.target;
            if (!(target instanceof HTMLTextAreaElement)) {
                return;
            }
            state.customInstruction = normalizeInstruction(target.value);
            render();
            scheduleCustomSave();
        });

        elements.customInstruction.addEventListener('blur', () => {
            flushCustomSave();
        });

        state.isBound = true;
    };

    window.initUserPersonalitySettings = (data = {}) => {
        bindEvents();
        syncStateFromPayload({
            personality_preset: data?.personality_preset,
            personality_custom_instruction: data?.personality_custom_instruction,
        });
        render();
    };
})();
