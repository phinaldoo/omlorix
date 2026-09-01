(function () {
    const backButton = document.getElementById('imageGenSettingsBack');
    const settingsContainer = document.getElementById('imageGenCurrentConfig');
    const legacyWizardContainer = document.getElementById('imageGenWizard');

    const UI = window.MediaGenerationUI;
    const t = (key, fallback) =>
        (typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback ?? key)
            : fallback ?? key);

    const resolveOptionLabel = (option = {}) =>
        (typeof window.resolveAdminSchemaOptionLabel === 'function'
            ? window.resolveAdminSchemaOptionLabel(option, t)
            : (option.i18n_label ? t(option.i18n_label, option.label || option.value || '') : (option.label || option.value || '')));

    /** Resolve a schema field label through the admin translation catalogue. */
    const resolveFieldLabel = (field = {}, fallback = '') =>
        (field.i18n_label ? t(field.i18n_label, field.label || fallback) : (field.label || fallback));

    /** Resolve schema help text so dynamic model settings follow the active locale. */
    const resolveFieldDescription = (field = {}) =>
        (field.i18n_description ? t(field.i18n_description, field.description || '') : (field.description || ''));

    let initialized = false;
    let abortController = null;
    let currentValues = {};
    let autoSaveTimer = null;

    const API_BASE = '/api/v1/admin';

    async function apiFetch(path, opts = {}) {
        const init = { method: opts.method || 'GET', ...opts };
        if (opts.body && typeof opts.body !== 'string') {
            init.body = JSON.stringify(opts.body);
            init.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
        }
        if (abortController) {
            init.signal = abortController.signal;
        }
        const res = await window.authedFetch(API_BASE + path, init);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    }

    function showStatus(msg, type = 'error') {
        if (!msg) return;
        if (type === 'success') return window.notifySuccess?.(msg);
        if (type === 'warning' || type === 'info') return window.notifyWarning?.(msg);
        window.notifyError?.(msg);
    }

    function setSelectMessage(select, message) {
        select.innerHTML = '';
        const option = document.createElement('option');
        option.value = '';
        option.textContent = message;
        select.appendChild(option);
    }

    function readFieldValue(field, control) {
        if (field.type === 'boolean') {
            return Boolean(control.checked);
        }
        if (field.type === 'number') {
            return control.value === '' ? null : Number(control.value);
        }
        if (field.type === 'select' && field.multiple) {
            return Array.from(control.selectedOptions || []).map((option) => option.value);
        }
        return control.value;
    }

    function applyFieldValue(field, control, rawValue) {
        if (field.type === 'boolean') {
            control.checked = typeof rawValue === 'string'
                ? ['1', 'true', 'yes', 'on'].includes(rawValue.trim().toLowerCase())
                : Boolean(rawValue);
            return;
        }
        if (field.type === 'select' && field.multiple) {
            const selected = new Set(Array.isArray(rawValue) ? rawValue.map(String) : []);
            Array.from(control.options || []).forEach((option) => {
                option.selected = selected.has(String(option.value));
            });
            return;
        }
        control.value = rawValue == null ? '' : String(rawValue);
    }

    function renderModelSettingsRows(fields, target, modelSettings, onValueChange) {
        UI.clearContainer(target);

        if (!fields.length) {
            target.appendChild(UI.buildEmptyStateSection({
                title: t('image_generation_wizard_step3', 'Step 3: Model Settings'),
                description: t('image_generation_model_settings_empty', 'This model has no additional settings.'),
            }));
            return;
        }

        // Keep both schema keys and persisted setting keys addressable so
        // provider schemas can declare dependencies with either spelling.
        const renderedFields = [];
        const normalizeSettingsKey = (key = '') => String(key).replace(/^settings\./, '');

        for (const field of fields) {
            const settingsKey = normalizeSettingsKey(field.key);
            const initialValue = field.value !== undefined ? field.value : field.default;
            let control;
            let valueControl;

            if (field.type === 'boolean') {
                const toggle = UI.buildToggle();
                control = toggle.wrap;
                valueControl = toggle.input;
            } else if (field.type === 'select' && field.options) {
                control = UI.buildSelect();
                valueControl = control;
                if (field.multiple) {
                    control.multiple = true;
                    control.size = Math.min(Math.max(field.options.length, 4), 8);
                }
                for (const option of field.options) {
                    const optionEl = document.createElement('option');
                    optionEl.value = option.value;
                    optionEl.textContent = resolveOptionLabel(option);
                    control.appendChild(optionEl);
                }
            } else {
                control = UI.buildInput({
                    type: field.type === 'number' ? 'number' : 'text',
                    placeholder: field.placeholder || '',
                    attributes: field.attributes,
                });
                valueControl = control;
            }

            // Schema rows provide the visible label; mirror it onto the native
            // control so toggles and upgraded selects have an accessible name.
            valueControl.id = `image-gen-setting-${settingsKey.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
            valueControl.setAttribute('aria-label', resolveFieldLabel(field, settingsKey));
            applyFieldValue(field, valueControl, initialValue);
            modelSettings[settingsKey] = readFieldValue(field, valueControl);
            valueControl.addEventListener(field.type === 'select' || field.type === 'boolean' ? 'change' : 'input', () => {
                modelSettings[settingsKey] = readFieldValue(field, valueControl);
                onValueChange?.();
            });

            const row = UI.buildSettingsRow({
                title: resolveFieldLabel(field, settingsKey),
                description: resolveFieldDescription(field),
                control,
            });
            target.appendChild(row);
            renderedFields.push({ field, settingsKey, row, control: valueControl });

            if (field.type === 'select') {
                const upgradedSelect = UI.upgradeSelect(control, {
                    ...field,
                    key: settingsKey,
                    placeholder: field.placeholder || '',
                });
                if (upgradedSelect?.triggerId) {
                    document.getElementById(upgradedSelect.triggerId)?.setAttribute(
                        'aria-label',
                        resolveFieldLabel(field, settingsKey),
                    );
                }
            }
        }

        /** Compare dependency values without confusing boolean HTML controls with strings. */
        const dependencyMatches = (field) => {
            if (!field.dependency) return true;
            const dependencyKey = normalizeSettingsKey(field.dependency);
            const dependency = renderedFields.find((entry) => entry.settingsKey === dependencyKey);
            if (!dependency) return true;
            const actualValue = readFieldValue(dependency.field, dependency.control);
            const expectedValue = field.dependency_value;
            if (typeof expectedValue === 'boolean') return Boolean(actualValue) === expectedValue;
            return String(actualValue ?? '') === String(expectedValue ?? '');
        };

        /** Hide dependent rows while preserving their configured values for later re-enabling. */
        const updateDependentRows = () => {
            renderedFields.forEach(({ field, row }) => {
                const isVisible = dependencyMatches(field);
                row.hidden = !isVisible;

                // `.settings-row` declares `display: flex`, which can override
                // the browser's built-in `[hidden]` rule. Set the inline
                // display state as well so the fixed and assistant-controlled
                // branches can never be visible at the same time.
                row.style.display = isVisible ? '' : 'none';
            });
        };

        renderedFields.forEach(({ field, control }) => {
            if (renderedFields.some((entry) => normalizeSettingsKey(entry.field.dependency) === normalizeSettingsKey(field.key))) {
                control.addEventListener('change', updateDependentRows);
            }
        });
        updateDependentRows();
    }

    async function loadPage() {
        if (!settingsContainer) return;

        UI.clearContainer(legacyWizardContainer);
        UI.showLoading(settingsContainer, t('admin_settings_loading', 'Loading settings...'));

        try {
            const [settingsPayload, providersPayload] = await Promise.all([
                apiFetch('/settings/image_generation?include_values=true'),
                apiFetch('/settings/image_generation/providers'),
            ]);
            currentValues = settingsPayload?.values || {};

            UI.clearContainer(settingsContainer);
            const { section, body } = UI.buildSettingsSection({
                title: t('image_generation_wizard_title', 'Configure Image Generation'),
                description: t('image_generation_card_subtitle', 'The image generation model currently used in chats.'),
            });
            settingsContainer.appendChild(section);

            const providerSelect = UI.buildSelect({
                id: 'imageGenProviderSelect',
                placeholder: t('image_generation_provider_select_default', 'Select a provider'),
            });
            const modelSelect = UI.buildSelect({
                id: 'imageGenModelSelect',
                placeholder: t('image_generation_model_select_default', 'Select a model'),
            });
            const settingsTarget = document.createElement('div');
            settingsTarget.className = 'media-gen-step-fields';

            body.appendChild(UI.buildSettingsRow({
                title: t('image_generation_label_provider', 'Provider'),
                description: t('image_generation_label_provider_desc', 'Active provider serving image generation requests.'),
                control: providerSelect,
            }));
            const modelRow = UI.buildSettingsRow({
                title: t('image_generation_label_model', 'Model'),
                description: t('image_generation_label_model_desc', 'Model selected for image generation.'),
                control: modelSelect,
            });
            body.appendChild(modelRow);
            body.appendChild(settingsTarget);

            const providers = Array.isArray(providersPayload?.providers) ? providersPayload.providers : [];
            providerSelect.innerHTML = `<option value="">${UI.escapeHtml(t('image_generation_provider_select_default', 'Select a provider'))}</option>`;
            providers.forEach((provider) => {
                const option = document.createElement('option');
                option.value = provider.id;
                const label = window.formatProviderLabel?.(provider.provider) || provider.provider;
                option.textContent = `${provider.name} (${label})`;
                providerSelect.appendChild(option);
            });
            providerSelect.value = currentValues.provider_id || '';
            modelSelect.disabled = true;

            const pageState = {
                providerId: providerSelect.value,
                modelName: currentValues.model_name || '',
                modelSettings: {},
                fields: [],
            };
            const updateWizardVisibility = () => {
                UI.setStepVisible(modelRow, Boolean(pageState.providerId));
            };
            updateWizardVisibility();

            const persist = async () => {
                if (!initialized) return;
                await apiFetch('/settings/image_generation', {
                    method: 'PATCH',
                    body: {
                        provider_id: pageState.providerId || '',
                        model_name: pageState.modelName || '',
                        settings: pageState.providerId && pageState.modelName ? pageState.modelSettings : {},
                    },
                });
                currentValues = {
                    ...currentValues,
                    provider_id: pageState.providerId || '',
                    model_name: pageState.modelName || '',
                    settings: pageState.providerId && pageState.modelName ? { ...pageState.modelSettings } : {},
                };
            };

            const scheduleAutoSave = () => {
                if (autoSaveTimer) {
                    clearTimeout(autoSaveTimer);
                }
                autoSaveTimer = setTimeout(() => {
                    autoSaveTimer = null;
                    persist().catch((error) => {
                        console.error('Failed to autosave image generation settings', error);
                        showStatus(t('image_generation_status_save_failed', 'Failed to save configuration.'));
                    });
                }, 350);
            };

            const refreshModelSettings = async ({ preserveCurrent = false, autosaveAfterLoad = false } = {}) => {
                UI.clearContainer(settingsTarget);
                pageState.modelSettings = {};
                pageState.fields = [];
                if (!pageState.providerId || !pageState.modelName) {
                    return;
                }
                settingsTarget.appendChild(UI.buildLoadingPlaceholder(t('admin_settings_loading', 'Loading settings...')));
                const data = await apiFetch(
                    `/settings/image_generation/model_settings?provider_id=${encodeURIComponent(pageState.providerId)}&model_name=${encodeURIComponent(pageState.modelName)}`
                );
                const fields = (data.sections || []).flatMap((item) => item.fields || []);
                pageState.fields = fields;
                const useCurrentValues =
                    preserveCurrent && currentValues.provider_id === pageState.providerId && currentValues.model_name === pageState.modelName;
                renderModelSettingsRows(fields, settingsTarget, pageState.modelSettings, scheduleAutoSave, useCurrentValues);
                if (autosaveAfterLoad) {
                    scheduleAutoSave();
                }
            };

            const refreshModels = async ({ preserveCurrent = false } = {}) => {
                setSelectMessage(modelSelect, t('image_generation_model_select_loading', 'Loading models...'));
                modelSelect.disabled = true;
                UI.clearContainer(settingsTarget);
                pageState.modelName = '';
                pageState.modelSettings = {};
                if (!pageState.providerId) {
                    updateWizardVisibility();
                    setSelectMessage(modelSelect, t('image_generation_model_select_default', 'Select a model'));
                    if (!preserveCurrent) {
                        scheduleAutoSave();
                    }
                    return;
                }
                updateWizardVisibility();
                const data = await apiFetch(`/settings/image_generation/models?provider_id=${encodeURIComponent(pageState.providerId)}`);
                const modelField = (data.sections || []).flatMap((item) => item.fields || []).find((field) => field.key === 'model_name');
                const options = modelField?.options || [];
                modelSelect.innerHTML = `<option value="">${UI.escapeHtml(t('image_generation_model_select_default', 'Select a model'))}</option>`;
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
                    await refreshModelSettings({ preserveCurrent: true });
                }
                UI.upgradeSelect(modelSelect, {
                    key: 'imageGenModelSelect',
                    placeholder: t('image_generation_model_select_default', 'Select a model'),
                });
            };

            providerSelect.addEventListener('change', async () => {
                pageState.providerId = providerSelect.value;
                pageState.modelName = '';
                updateWizardVisibility();
                try {
                    await refreshModels();
                } catch (error) {
                    console.error('Failed to refresh image generation models', error);
                    showStatus(t('image_generation_model_select_failed', 'Failed to load models'));
                }
            });

            modelSelect.addEventListener('change', async () => {
                pageState.modelName = modelSelect.value;
                try {
                    await refreshModelSettings({ autosaveAfterLoad: true });
                } catch (error) {
                    console.error('Failed to refresh image generation model settings', error);
                    showStatus(t('image_generation_model_settings_error', 'Failed to load model settings.'));
                }
            });

            UI.upgradeSelect(providerSelect, {
                key: 'imageGenProviderSelect',
                placeholder: t('image_generation_provider_select_default', 'Select a provider'),
            });
            await refreshModels({ preserveCurrent: true });
        } catch (error) {
            UI.clearContainer(settingsContainer);
            showStatus(t('image_generation_status_load_failed', 'Unable to load image generation settings.'));
        }
    }

    const handleBackClick = () => {
        window.activateAdminPage?.('tools');
    };

    window.initImageGenerationSettingsPage = () => {
        if (initialized) return;
        initialized = true;
        abortController = new AbortController();
        backButton?.addEventListener('click', handleBackClick);
        loadPage();
    };

    window.teardownImageGenerationSettingsPage = () => {
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
        UI.clearContainer(settingsContainer);
        UI.clearContainer(legacyWizardContainer);
    };
})();
