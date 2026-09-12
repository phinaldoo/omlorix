(function () {
    const backButton = document.getElementById('musicGenerationSettingsBack');
    const fieldsContainer = document.getElementById('musicGenerationSettingsFields');

    const UI = window.MediaGenerationUI;

    let initialized = false;
    let abortController = null;
    let currentValues = {};
    let autoSaveTimer = null;

    const MODEL_SETTING_KEYS = [
        'response_format',
        'enable_reference_images',
        'max_reference_images',
    ];

    const translate = (key, fallback) =>
        (typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback ?? key)
            : fallback ?? key);

    const apiFetch = UI.createApiClient(() => abortController?.signal);
    const { showStatus, setSelectMessage } = UI;

    function renderModelSettings(fields, target, pageState, onValueChange) {
        UI.clearContainer(target);
        pageState.modelSettings = {};

        if (!fields.length) {
            target.appendChild(UI.buildEmptyStateSection({
                title: translate('page_music_generation_settings', 'Music Generation Settings'),
                description: translate('music_generation_model_settings_empty', 'This model has no additional settings.'),
            }));
            return;
        }

        fields.forEach((field) => {
            const initialValue =
                currentValues.provider_id === pageState.providerId && currentValues.model_name === pageState.modelName
                    ? currentValues[field.key] ?? field.default
                    : field.default;
            const { control, valueControl } = UI.buildFieldControl(field, { optionLabel: (option) => option.label ?? option.value ?? '' });
            if (field.type === 'number' && !valueControl.step) {
                valueControl.step = field.input_type === 'float' ? 'any' : '1';
            }

            UI.bindFieldValue(field, valueControl, initialValue, pageState.modelSettings, onValueChange);

            target.appendChild(UI.buildSettingsRow({
                title: field.label || field.key,
                description: field.description || '',
                control,
            }));

            if (field.type === 'select') {
                UI.upgradeSelect(control, {
                    ...field,
                    placeholder: field.placeholder || '',
                });
            }
        });
    }

    async function loadPage() {
        if (!fieldsContainer) return;

        UI.showLoading(fieldsContainer, translate('admin_settings_loading', 'Loading settings...'));

        try {
            const [settingsPayload, providersPayload] = await Promise.all([
                apiFetch('/settings/music_generation?include_values=true'),
                apiFetch('/settings/music_generation/providers'),
            ]);
            currentValues = settingsPayload?.values || {};

            UI.clearContainer(fieldsContainer);
            const { section, body } = UI.buildSettingsSection({
                title: translate('page_music_generation_settings', 'Music Generation Settings'),
                description: translate('page_music_generation_settings_subtitle', 'Configure provider, model, and default generation parameters for chat music generation.'),
            });
            fieldsContainer.appendChild(section);

            const providerSelect = UI.buildSelect({
                id: 'musicGenProvider',
                placeholder: translate('music_generation_provider_placeholder', 'Select a provider'),
            });
            const modelSelect = UI.buildSelect({
                id: 'musicGenModel',
                placeholder: translate('music_generation_model_placeholder', 'Select a model'),
            });
            const settingsTarget = document.createElement('div');
            settingsTarget.className = 'media-gen-step-fields';

            body.appendChild(UI.buildSettingsRow({
                title: translate('schema_music_generation_provider_id', 'Provider'),
                description: translate('schema_music_generation_provider_id_desc', 'Choose which provider handles music generation requests.'),
                control: providerSelect,
            }));
            const modelRow = UI.buildSettingsRow({
                title: translate('schema_music_generation_model_name', 'Model'),
                description: translate('schema_music_generation_model_name_desc', 'Choose which model is used for music generation.'),
                control: modelSelect,
            });
            body.appendChild(modelRow);
            body.appendChild(settingsTarget);

            providerSelect.innerHTML = `<option value="">${UI.escapeHtml(translate('music_generation_provider_placeholder', 'Select a provider'))}</option>`;
            (providersPayload.providers || []).forEach((provider) => {
                const option = document.createElement('option');
                option.value = provider.id;
                option.textContent = provider.name || provider.id;
                providerSelect.appendChild(option);
            });
            providerSelect.value = currentValues.provider_id || '';
            modelSelect.disabled = true;

            const pageState = {
                providerId: providerSelect.value,
                modelName: currentValues.model_name || '',
                modelSettings: {},
            };
            const updateWizardVisibility = () => {
                UI.setStepVisible(modelRow, Boolean(pageState.providerId));
            };
            updateWizardVisibility();

            const persist = async () => {
                if (!initialized) return;
                const payload = pageState.providerId && pageState.modelName
                    ? {
                        provider_id: pageState.providerId,
                        model_name: pageState.modelName,
                        ...pageState.modelSettings,
                    }
                    : { provider_id: '', model_name: '' };
                await apiFetch('/settings/music_generation', {
                    method: 'PATCH',
                    body: payload,
                });
                currentValues = { ...currentValues, ...payload };
            };

            const scheduleAutoSave = () => {
                if (autoSaveTimer) clearTimeout(autoSaveTimer);
                autoSaveTimer = setTimeout(() => {
                    autoSaveTimer = null;
                    persist().catch((error) => {
                        console.error('Failed to autosave music generation settings', error);
                        showStatus(translate('music_generation_status_save_failed', 'Failed to save music generation settings.'));
                    });
                }, 350);
            };

            const refreshModelSettings = async ({ autosaveAfterLoad = false } = {}) => {
                UI.clearContainer(settingsTarget);
                pageState.modelSettings = {};
                if (!pageState.providerId || !pageState.modelName) return;
                settingsTarget.appendChild(UI.buildLoadingPlaceholder(translate('admin_settings_loading', 'Loading settings...')));
                const payload = await apiFetch(
                    `/settings/music_generation/model_settings?provider_id=${encodeURIComponent(pageState.providerId)}&model_name=${encodeURIComponent(pageState.modelName)}`
                );
                const fields = (payload.sections || []).flatMap((item) => item.fields || [])
                    .filter((field) => MODEL_SETTING_KEYS.includes(field.key));
                renderModelSettings(fields, settingsTarget, pageState, scheduleAutoSave);
                if (autosaveAfterLoad) {
                    scheduleAutoSave();
                }
            };

            const refreshModels = async ({ preserveCurrent = false } = {}) => {
                setSelectMessage(modelSelect, translate('music_generation_model_placeholder', 'Select a model'));
                modelSelect.disabled = true;
                UI.clearContainer(settingsTarget);
                pageState.modelName = '';
                pageState.modelSettings = {};
                if (!pageState.providerId) {
                    updateWizardVisibility();
                    if (!preserveCurrent) {
                        scheduleAutoSave();
                    }
                    return;
                }
                updateWizardVisibility();
                const modelsPayload = await apiFetch(`/settings/music_generation/models?provider_id=${encodeURIComponent(pageState.providerId)}`);
                const modelField = (modelsPayload.sections || []).flatMap((item) => item.fields || []).find((field) => field.key === 'model_name');
                const modelOptions = modelField?.options || [];
                modelSelect.innerHTML = `<option value="">${UI.escapeHtml(translate('music_generation_model_placeholder', 'Select a model'))}</option>`;
                modelOptions.forEach((option) => {
                    const optionEl = document.createElement('option');
                    optionEl.value = String(option.value ?? '');
                    optionEl.textContent = option.label ?? option.value ?? '';
                    modelSelect.appendChild(optionEl);
                });
                modelSelect.disabled = !modelOptions.length;
                if (preserveCurrent && currentValues.model_name && modelOptions.some((option) => String(option.value ?? '') === currentValues.model_name)) {
                    modelSelect.value = currentValues.model_name;
                    pageState.modelName = currentValues.model_name;
                    await refreshModelSettings();
                }
                UI.upgradeSelect(modelSelect, {
                    key: 'musicGenModel',
                    placeholder: translate('music_generation_model_placeholder', 'Select a model'),
                });
            };

            providerSelect.addEventListener('change', async () => {
                pageState.providerId = providerSelect.value;
                pageState.modelName = '';
                updateWizardVisibility();
                try {
                    await refreshModels();
                } catch (error) {
                    console.error('Failed to refresh music generation models', error);
                    showStatus(translate('music_generation_status_models_load_failed', 'Failed to load models for the selected provider.'));
                }
            });

            modelSelect.addEventListener('change', async () => {
                pageState.modelName = modelSelect.value;
                try {
                    await refreshModelSettings({ autosaveAfterLoad: true });
                } catch (error) {
                    console.error('Failed to refresh music generation model settings', error);
                    showStatus(translate('music_generation_status_model_settings_load_failed', 'Failed to load model settings.'));
                }
            });

            UI.upgradeSelect(providerSelect, {
                key: 'musicGenProvider',
                placeholder: translate('music_generation_provider_placeholder', 'Select a provider'),
            });
            await refreshModels({ preserveCurrent: true });
        } catch (error) {
            UI.clearContainer(fieldsContainer);
            showStatus(translate('music_generation_status_load_failed', 'Failed to load music generation settings.'));
        }
    }

    const handleBackClick = () => {
        if (typeof window.activateAdminPage === 'function') {
            window.activateAdminPage('tools');
        }
    };

    window.initMusicGenerationSettingsPage = () => {
        if (initialized) return;
        initialized = true;
        abortController = new AbortController();
        backButton?.addEventListener('click', handleBackClick);
        loadPage();
    };

    window.teardownMusicGenerationSettingsPage = () => {
        initialized = false;
        if (abortController) {
            abortController.abort();
            abortController = null;
        }
        if (autoSaveTimer) {
            clearTimeout(autoSaveTimer);
            autoSaveTimer = null;
        }
        backButton?.removeEventListener('click', handleBackClick);
        UI.clearContainer(fieldsContainer);
    };
})();
