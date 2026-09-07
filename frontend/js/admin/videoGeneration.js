(function () {
    const backButton = document.getElementById('videoGenerationSettingsBack');
    const fieldsContainer = document.getElementById('videoGenerationSettingsFields');

    const UI = window.MediaGenerationUI;

    let initialized = false;
    let abortController = null;
    let currentValues = {};
    let autoSaveTimer = null;

    const MODEL_SETTING_KEYS = [
        'duration_seconds',
        'size',
        'aspect_ratio',
        'resolution',
        'enable_reference_files',
        'timeout_seconds',
        'poll_interval_seconds',
        'max_retries',
    ];

    const translate = (key, fallback) =>
        (typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback)
            : fallback ?? key);

    const getSettingLabel = (key, fallback) => {
        switch (key) {
            case 'duration_seconds':
                return translate('schema_video_generation_duration_seconds', fallback || 'Duration (seconds)');
            case 'size':
                return translate('schema_video_generation_size', fallback || 'Size');
            case 'aspect_ratio':
                return translate('schema_video_generation_aspect_ratio', fallback || 'Aspect Ratio');
            case 'resolution':
                return translate('schema_video_generation_resolution', fallback || 'Resolution');
            case 'fps':
                return translate('schema_video_generation_fps', fallback || 'FPS');
            case 'negative_prompt':
                return translate('schema_video_generation_negative_prompt', fallback || 'Negative Prompt');
            case 'seed':
                return translate('schema_video_generation_seed', fallback || 'Seed');
            case 'enable_reference_files':
                return translate('schema_video_generation_enable_reference_files', fallback || 'Enable Reference Files');
            case 'timeout_seconds':
                return translate('schema_video_generation_timeout_seconds', fallback || 'Job Timeout (seconds)');
            case 'poll_interval_seconds':
                return translate('schema_video_generation_poll_interval_seconds', fallback || 'Poll Interval (seconds)');
            case 'max_retries':
                return translate('schema_video_generation_max_retries', fallback || 'Max Retries');
            default:
                return fallback || key;
        }
    };

    const apiFetch = UI.createApiClient(() => abortController?.signal);
    const { showStatus, setSelectMessage } = UI;

    function renderModelSettingsRows(fields, target, pageState, onValueChange) {
        UI.clearContainer(target);

        if (!fields.length) {
            target.appendChild(UI.buildEmptyStateSection({
                title: translate('video_generation_wizard_step3', 'Step 3: Model Settings'),
                description: translate('video_generation_model_settings_empty', 'This model has no additional settings.'),
            }));
            return;
        }

        for (const field of fields) {
            const initialValue =
                currentValues.provider_id === pageState.providerId && currentValues.model_name === pageState.modelName
                    ? currentValues[field.key] ?? field.default
                    : field.default;
            const { control, valueControl } = UI.buildFieldControl(field);

            UI.bindFieldValue(field, valueControl, initialValue, pageState.modelSettings, onValueChange);

            target.appendChild(UI.buildSettingsRow({
                title: getSettingLabel(field.key, field.label) || field.key,
                description: field.description || '',
                control,
            }));

            if (field.type === 'select') {
                UI.upgradeSelect(control, {
                    ...field,
                    placeholder: field.placeholder || '',
                });
            }
        }
    }

    async function loadPage() {
        if (!fieldsContainer) return;
        UI.showLoading(fieldsContainer, translate('admin_settings_loading', 'Loading settings...'));

        try {
            const [settingsPayload, providersPayload] = await Promise.all([
                apiFetch('/settings/video_generation?include_values=true'),
                apiFetch('/settings/video_generation/providers'),
            ]);
            currentValues = settingsPayload?.values || {};

            UI.clearContainer(fieldsContainer);
            const { section, body } = UI.buildSettingsSection({
                title: translate('video_generation_wizard_title', 'Configure Video Generation'),
                description: translate('video_generation_card_subtitle', 'The video generation model currently used in chats.'),
            });
            fieldsContainer.appendChild(section);

            const providerSelect = UI.buildSelect({
                id: 'videoGenProviderSelect',
                placeholder: translate('video_generation_provider_select_default', 'Select a provider'),
            });
            const modelSelect = UI.buildSelect({
                id: 'videoGenModelSelect',
                placeholder: translate('video_generation_model_select_default', 'Select a model'),
            });
            const settingsTarget = document.createElement('div');
            settingsTarget.className = 'media-gen-step-fields';

            body.appendChild(UI.buildSettingsRow({
                title: translate('schema_video_generation_provider_id', 'Provider'),
                description: translate('schema_video_generation_provider_id_desc', 'Select an OpenAI, OpenRouter, or Google AI Studio provider.'),
                control: providerSelect,
            }));
            const modelRow = UI.buildSettingsRow({
                title: translate('schema_video_generation_model_name', 'Model'),
                description: translate('schema_video_generation_model_name_desc', 'Choose which model is used for video generation.'),
                control: modelSelect,
            });
            body.appendChild(modelRow);
            body.appendChild(settingsTarget);

            providerSelect.innerHTML = `<option value="">${UI.escapeHtml(translate('video_generation_provider_select_default', 'Select a provider'))}</option>`;
            (providersPayload.providers || []).forEach((provider) => {
                const option = document.createElement('option');
                option.value = provider.id;
                const providerLabel = window.formatProviderLabel?.(provider.provider) || provider.provider;
                option.textContent = `${provider.name} (${providerLabel})`;
                providerSelect.appendChild(option);
            });
            providerSelect.value = currentValues.provider_id || '';
            modelSelect.disabled = true;

            const pageState = {
                providerId: providerSelect.value,
                modelName: currentValues.model_name || '',
                modelSettings: {},
                activeFieldKeys: new Set(),
            };
            const updateWizardVisibility = () => {
                UI.setStepVisible(modelRow, Boolean(pageState.providerId));
            };
            updateWizardVisibility();

            const persist = async () => {
                if (!initialized) return;
                const cleared = {};
                for (const key of MODEL_SETTING_KEYS) {
                    if (!pageState.activeFieldKeys.has(key)) cleared[key] = null;
                }
                const payload = pageState.providerId && pageState.modelName
                    ? {
                        provider_id: pageState.providerId,
                        model_name: pageState.modelName,
                        ...cleared,
                        ...pageState.modelSettings,
                    }
                    : { provider_id: '', model_name: '' };
                await apiFetch('/settings/video_generation', {
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
                        console.error('Failed to autosave video generation settings', error);
                        showStatus(translate('video_generation_status_save_failed', 'Failed to save configuration.'));
                    });
                }, 350);
            };

            const refreshModelSettings = async ({ autosaveAfterLoad = false } = {}) => {
                UI.clearContainer(settingsTarget);
                pageState.modelSettings = {};
                pageState.activeFieldKeys = new Set();
                if (!pageState.providerId || !pageState.modelName) {
                    return;
                }
                settingsTarget.appendChild(UI.buildLoadingPlaceholder(translate('admin_settings_loading', 'Loading settings...')));
                const data = await apiFetch(
                    `/settings/video_generation/model_settings?provider_id=${encodeURIComponent(pageState.providerId)}&model_name=${encodeURIComponent(pageState.modelName)}`
                );
                const fields = (data.sections || []).flatMap((item) => item.fields || []);
                pageState.activeFieldKeys = new Set(fields.map((field) => field.key));
                renderModelSettingsRows(fields, settingsTarget, pageState, scheduleAutoSave);
                if (autosaveAfterLoad) {
                    scheduleAutoSave();
                }
            };

            const refreshModels = async ({ preserveCurrent = false } = {}) => {
                setSelectMessage(modelSelect, translate('video_generation_model_select_loading', 'Loading models...'));
                modelSelect.disabled = true;
                UI.clearContainer(settingsTarget);
                pageState.modelName = '';
                pageState.modelSettings = {};
                pageState.activeFieldKeys = new Set();
                if (!pageState.providerId) {
                    updateWizardVisibility();
                    setSelectMessage(modelSelect, translate('video_generation_model_select_default', 'Select a model'));
                    if (!preserveCurrent) {
                        scheduleAutoSave();
                    }
                    return;
                }
                updateWizardVisibility();
                const data = await apiFetch(`/settings/video_generation/models?provider_id=${encodeURIComponent(pageState.providerId)}`);
                const modelField = (data.sections || []).flatMap((item) => item.fields || []).find((field) => field.key === 'model_name');
                const options = modelField?.options || [];
                modelSelect.innerHTML = `<option value="">${UI.escapeHtml(translate('video_generation_model_select_default', 'Select a model'))}</option>`;
                options.forEach((option) => {
                    const optionEl = document.createElement('option');
                    optionEl.value = option.value;
                    optionEl.textContent = option.label || option.value;
                    modelSelect.appendChild(optionEl);
                });
                modelSelect.disabled = !options.length;
                if (preserveCurrent && currentValues.model_name && options.some((option) => option.value === currentValues.model_name)) {
                    modelSelect.value = currentValues.model_name;
                    pageState.modelName = currentValues.model_name;
                    await refreshModelSettings();
                }
                UI.upgradeSelect(modelSelect, {
                    key: 'videoGenModelSelect',
                    placeholder: translate('video_generation_model_select_default', 'Select a model'),
                });
            };

            providerSelect.addEventListener('change', async () => {
                pageState.providerId = providerSelect.value;
                pageState.modelName = '';
                updateWizardVisibility();
                try {
                    await refreshModels();
                } catch (error) {
                    console.error('Failed to refresh video generation models', error);
                    showStatus(translate('video_generation_model_select_failed', 'Failed to load models'));
                }
            });

            modelSelect.addEventListener('change', async () => {
                pageState.modelName = modelSelect.value;
                try {
                    await refreshModelSettings({ autosaveAfterLoad: true });
                } catch (error) {
                    console.error('Failed to refresh video generation model settings', error);
                    showStatus(translate('video_generation_model_settings_error', 'Failed to load model settings.'));
                }
            });

            UI.upgradeSelect(providerSelect, {
                key: 'videoGenProviderSelect',
                placeholder: translate('video_generation_provider_select_default', 'Select a provider'),
            });
            await refreshModels({ preserveCurrent: true });
        } catch (error) {
            UI.clearContainer(fieldsContainer);
            showStatus(translate('video_generation_status_load_failed', 'Unable to load video generation settings.'));
        }
    }

    const handleBackClick = () => {
        window.activateAdminPage?.('tools');
    };

    window.initVideoGenerationSettingsPage = () => {
        if (initialized) return;
        initialized = true;
        abortController = new AbortController();
        backButton?.addEventListener('click', handleBackClick);
        loadPage();
    };

    window.teardownVideoGenerationSettingsPage = () => {
        if (!initialized) return;
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
