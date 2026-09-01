(function () {
    const dom = {
        button: document.getElementById('createProviderButton'),
        grid: document.querySelector('.available-provider-grid'),
        pages: {
            list: document.getElementById('page-providers'),
            select: document.getElementById('page-providers-create'),
            create: document.getElementById('page-providers-create-item'),
            edit: document.getElementById('page-providers-edit')
        },
        backSelect: document.getElementById('providersCreateBackButton'),
        forms: {
            create: {
                title: document.getElementById('providerCreateFormTitle'),
                subtitle: document.getElementById('providerCreateFormSubtitle'),
                form: document.getElementById('providerCreateForm'),
                fields: document.getElementById('providerCreateFormFields'),
                loading: document.getElementById('providerCreateFormLoading'),
                back: document.getElementById('providerCreateFormBack'),
                test: document.getElementById('providerCreateFormTest'),
                submit: document.getElementById('providerCreateFormSubmit')
            },
            edit: {
                wrapper: document.getElementById('providerEditFormWrapper'),
                title: document.getElementById('providerEditFormTitle'),
                subtitle: document.getElementById('providerEditFormSubtitle'),
                sidebar: document.getElementById('providerEditTabSettings')?.closest('.provider-form-sidebar')
                    || document.querySelector('#providerEditFormWrapper .provider-form-sidebar'),
                form: document.getElementById('providerEditForm'),
                fields: document.getElementById('providerEditFormFields'),
                loading: document.getElementById('providerEditFormLoading'),
                back: document.getElementById('providerEditFormBack'),
                test: document.getElementById('providerEditFormTest'),
                submit: document.getElementById('providerEditFormSubmit'),
                tabs: {
                    settings: document.getElementById('providerEditTabSettings'),
                    models: document.getElementById('providerEditTabModels')
                },
                pages: {
                    settings: document.getElementById('providerEditSettingsPage'),
                    models: document.getElementById('providerEditModelsPage')
                },
                modelsContainer: document.getElementById('providerEditModelsContainer'),
                modelsEmpty: document.getElementById('providerEditModelsEmpty')
            }
        }
    };

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };
    // Provider schemas are shared with BYOK, so keep the UI guard explicit:
    // only compatible custom endpoint protocols get an editable icon field.
    // The backend applies the same rule when it receives the final payload.
    const FALLBACK_CUSTOM_PROVIDER_ICON_KEYS = new Set([
        'openai_responses',
        'openai_chat_completions',
        'anthropic_base',
    ]);
    // Required-field metadata normally comes from the provider schema. Keep
    // this small fallback in the client so rolling upgrades or stale cached
    // schemas cannot send an empty credential to the backend and expose a raw
    // Pydantic validation message to administrators.
    const FALLBACK_REQUIRED_API_KEY_PROVIDER_KEYS = new Set([
        'openai',
        'openai_responses',
        'openai_chat_completions',
        'microsoft_azure',
        'anthropic',
        'google_aistudio',
        'openrouter',
        'elevenlabs',
        'xai',
    ]);
    const providerRequiresApiKey = (providerKey = '') => (
        FALLBACK_REQUIRED_API_KEY_PROVIDER_KEYS.has(String(providerKey || '').trim().toLowerCase())
    );
    const providerSupportsCustomIcon = (providerKey = '') => {
        if (typeof window.providerSupportsCustomIcon === 'function') {
            return window.providerSupportsCustomIcon(providerKey);
        }
        return FALLBACK_CUSTOM_PROVIDER_ICON_KEYS.has(String(providerKey || '').trim().toLowerCase());
    };
    const getDefaultProviderIconKey = (providerKey = '') => {
        if (typeof window.getDefaultProviderIconKey === 'function') {
            return window.getDefaultProviderIconKey(providerKey);
        }
        const normalized = String(providerKey || '').trim().toLowerCase();
        return {
            openai: 'openai',
            openai_responses: 'openai',
            openai_chat_completions: 'openai',
            microsoft_azure: 'microsoft',
            anthropic: 'anthropic',
            anthropic_base: 'anthropic',
            google_aistudio: 'google_aistudio',
            openrouter: 'openrouter',
            ollama: 'ollama',
            lmstudio: 'lmstudio',
            elevenlabs: 'elevenlabs',
            xai: 'xai',
        }[normalized] || normalized || 'omlorix';
    };
    const formatT = (key, fallback, vars) => {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        const template = t(key, fallback);
        return String(template).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars?.[token];
            return value === undefined || value === null ? '' : String(value);
        });
    };
    const DEFAULT_MODELS_PLACEHOLDER = dom.forms.edit?.modelsEmpty?.textContent?.trim()
        || t('provider_models_empty', 'Models are only available for Ollama and LM Studio providers.');
    const PROVIDER_TAB_KEYS = ['settings', 'models'];
    const FIELD_PLACEHOLDER_KEYS = {
        name: 'provider_field_name_placeholder',
        api_key: 'provider_field_api_key_placeholder',
        api_token: 'provider_field_api_token_placeholder',
        api_secret: 'provider_field_api_secret_placeholder',
        region: 'provider_field_region_placeholder',
        model: 'provider_field_model_placeholder',
        models: 'provider_field_models_placeholder',
        project: 'provider_field_project_placeholder',
        organization: 'provider_field_organization_placeholder',
        'settings.api_version': 'provider_field_api_version_placeholder',
        'settings.organization': 'provider_field_organization_placeholder',
        'settings.project': 'provider_field_project_placeholder'
    };

    const resolveFieldPlaceholder = (field = {}) => {
        const fallback = field.placeholder || FIELD_META[field.id]?.placeholder || '';
        const key = field.i18n_placeholder || FIELD_PLACEHOLDER_KEYS[field.id];
        return key ? t(key, fallback) : fallback;
    };

    const resolveSchemaOptionLabel = (option = {}) => (
        typeof window.resolveAdminSchemaOptionLabel === 'function'
            ? window.resolveAdminSchemaOptionLabel(option, t)
            : (option.i18n_label ? t(option.i18n_label, option.label || option.value || option.id || '') : (option.label || option.value || option.id || ''))
    );

    const setModelsPlaceholder = (message = DEFAULT_MODELS_PLACEHOLDER) => {
        const placeholder = dom.forms.edit?.modelsEmpty;
        if (!placeholder) {
            return;
        }
        placeholder.hidden = false;
        placeholder.textContent = message || DEFAULT_MODELS_PLACEHOLDER;
    };

    const hideModelsPlaceholder = () => {
        const placeholder = dom.forms.edit?.modelsEmpty;
        if (!placeholder) {
            return;
        }
        placeholder.hidden = true;
        placeholder.textContent = DEFAULT_MODELS_PLACEHOLDER;
    };

    const getNormalizedProviderKey = () => String(state.providerKey || '').toLowerCase();
    const providerSupportsNativeModelsTab = (providerKey = getNormalizedProviderKey()) => (
        providerKey === 'ollama' || providerKey === 'lmstudio'
    );
    const providerModelsTabIsUnsupported = (providerKey = getNormalizedProviderKey()) => (
        providerKey === 'ollama' && state.ollamaUnsupported
    );
    const getOllamaActionElements = (actionKey) => {
        const section = dom.forms.edit?.modelsContainer?.querySelector(`.${OLLAMA_LOADED_MODELS_CLASS}`);
        if (!section) {
            return null;
        }
        const actionEl = section.querySelector(`.provider-loaded-models-action-${actionKey}`);
        if (!actionEl) {
            return null;
        }
        return {
            container: actionEl,
            input: actionEl.querySelector('.ollama-models-input'),
            actionButton: actionEl.querySelector('.ollama-models-row-button')
        };
    };

    const setOllamaActionStatus = (actionKey, message, tone = 'info') => {
        const elements = getOllamaActionElements(actionKey);
        const statusEl = elements?.container?.querySelector('.ollama-models-row-status');
        if (!statusEl) {
            return;
        }
        statusEl.hidden = !message;
        statusEl.textContent = message || '';
        statusEl.dataset.tone = tone;
    };

    const getOllamaVersionElements = () => {
        const section = dom.forms.edit?.modelsContainer?.querySelector(`.${OLLAMA_LOADED_MODELS_CLASS}`);
        if (!section) {
            return null;
        }
        const container = section.querySelector(`.${OLLAMA_VERSION_CLASS}`);
        if (!container) {
            return null;
        }
        return {
            container,
            status: container.querySelector('.ollama-models-version-value')
        };
    };

    const setOllamaVersionStatus = (message, tone = 'info') => {
        const elements = getOllamaVersionElements();
        if (!elements?.status) {
            return;
        }
        elements.status.textContent = message || '';
    };

    const fetchOllamaVersion = async () => {
        const providerId = state.editingId;
        if (!providerId) {
            return;
        }

        ensureOllamaVersionBlock();
        setOllamaVersionStatus(t('provider_ollama_version_checking', 'Checking version...'), 'info');

        try {
            const response = await window.authedFetch(`/api/v1/llm/ollama/version?ollama_provider_id=${encodeURIComponent(providerId)}`);
            if (!response.ok) {
                notifyError(t('provider_ollama_version_failed', 'Failed to fetch version.'));
                return;
            }
            const data = await response.json();
            const versionText = data?.version || data?.ollama_version || data?.data || JSON.stringify(data);
            state.ollamaVersion = versionText;
            setOllamaVersionStatus(String(versionText || ''), 'success');
        } catch (error) {
            console.error('Failed to fetch Ollama version', error);
            setOllamaVersionStatus(t('provider_ollama_version_failed_later', 'Unable to fetch version. Please try again later.'), 'error');
        }
    };

    const state = {
        initialized: false,
        mode: 'create',
        providerKey: null,
        editingId: null,
        editingData: null,
        available: [],
        schema: [],
        controls: new Map(),
        definitions: new Map(),
        formValues: null,
        ollamaModels: null,
        ollamaAllModels: null,
        downloadingModel: false,
        ollamaDownloadInput: null,
        ollamaActionState: {
            delete: { running: false },
            load: { running: false },
            unload: { running: false }
        },
        ollamaVersion: null,
        ollamaUnsupported: false,
        ollamaUnsupportedMessage: null,
        lmstudioAllModels: [],
        lmstudioLoadedModels: [],
        lmstudioDownloadState: { running: false },
        lmstudioActionState: {
            load: { running: false },
            unload: { running: false }
        },
        lmstudioControls: {},
        submitting: false,
        active: true,
        view: 'list',
        isDirty: false,
        pendingNavigation: null,
        activeTab: 'settings',
        modelsTabVisible: false,
        modelsTabReady: false,
        modelsRefreshGeneration: 0,
        providerTabSelectionGeneration: 0,
        secretPreviews: {}
    };
    const UNSAVED_GUARD_ID = 'admin-provider-form-unsaved';
    let unsavedGuardRegistered = false;

    const setProviderTab = (tab = 'settings') => {
        const tabs = dom.forms.edit?.tabs;
        const pages = dom.forms.edit?.pages;
        if (!tabs || !pages) {
            return;
        }
        const wantsModels = tab === 'models';
        const allowModels = state.modelsTabVisible;
        const nextTab = wantsModels && allowModels ? 'models' : 'settings';
        state.activeTab = nextTab;
        PROVIDER_TAB_KEYS.forEach((key) => {
            const button = tabs[key];
            const page = pages[key];
            const isActive = key === nextTab;
            if (button) {
                button.classList.toggle('active', isActive);
                button.setAttribute('aria-selected', String(isActive));
                button.tabIndex = isActive ? 0 : -1;
            }
            if (page) {
                page.hidden = !isActive;
            }
        });
    };

    const updateModelsTabVisibility = () => {
        const normalizedProvider = getNormalizedProviderKey();
        const useProviderSidebar = state.mode === 'edit' && providerSupportsNativeModelsTab(normalizedProvider);
        const supportsModels = state.mode === 'edit'
            && providerSupportsNativeModelsTab(normalizedProvider)
            && state.modelsTabReady
            && !providerModelsTabIsUnsupported(normalizedProvider);
        if (!supportsModels && state.activeTab === 'models') {
            setProviderTab('settings');
        }
        state.modelsTabVisible = supportsModels;

        const sidebar = dom.forms.edit?.sidebar
            || dom.forms.edit?.wrapper?.querySelector?.('.provider-form-sidebar');
        if (!dom.forms.edit?.sidebar && sidebar) {
            dom.forms.edit.sidebar = sidebar;
        }
        if (sidebar) {
            sidebar.hidden = !useProviderSidebar;
            sidebar.setAttribute('aria-hidden', String(!useProviderSidebar));
            sidebar.style.display = useProviderSidebar ? '' : 'none';
        }

        const wrapper = dom.forms.edit?.wrapper;
        if (wrapper) {
            wrapper.classList.toggle('provider-form-no-sidebar', !useProviderSidebar);
        }

        const modelsTab = dom.forms.edit?.tabs?.models;
        if (modelsTab) {
            modelsTab.hidden = !supportsModels;
            modelsTab.setAttribute('aria-hidden', String(!supportsModels));
            if (!supportsModels) {
                modelsTab.setAttribute('aria-selected', 'false');
            }
        }

        const modelsPage = dom.forms.edit?.pages?.models;
        if (modelsPage && !supportsModels) {
            modelsPage.hidden = true;
        }
    };

    /**
     * Starts a model-list refresh without moving an administrator away from
     * the Models tab. The tab is still hidden during the initial provider
     * load, but an already visible Models tab remains available while its
     * contents are refreshed after a download or another model action.
     *
     * @param {string} providerId Provider that owns the request.
     * @returns {{ generation: number, providerId: string, providerKey: string, preserveModelsTab: boolean, tabSelectionGeneration: number }}
     * Refresh ownership used to reject stale completions.
     */
    const beginModelsTabRefresh = (providerId) => {
        const preserveModelsTab = state.activeTab === 'models' && state.modelsTabVisible;
        // Every refresh gets a monotonically increasing owner token. Besides
        // provider identity, the token prevents an older request for the same
        // provider from publishing readiness after a newer refresh has begun.
        const refresh = {
            generation: ++state.modelsRefreshGeneration,
            providerId,
            providerKey: getNormalizedProviderKey(),
            preserveModelsTab,
            tabSelectionGeneration: state.providerTabSelectionGeneration,
        };
        state.modelsTabReady = false;
        if (!preserveModelsTab) {
            updateModelsTabVisibility();
        }
        return refresh;
    };

    /**
     * Marks refreshed model data as ready and restores the tab selection that
     * was active before the refresh began.
     *
     * @param {{ generation: number, providerId: string, providerKey: string, preserveModelsTab: boolean, tabSelectionGeneration: number }} refresh
     * Refresh ownership captured by beginModelsTabRefresh.
     */
    const completeModelsTabRefresh = (refresh) => {
        const isCurrentRefresh = Boolean(
            refresh
            && refresh.generation === state.modelsRefreshGeneration
            && refresh.providerId === state.editingId
            && refresh.providerKey === getNormalizedProviderKey()
            && state.mode === 'edit'
        );
        if (!isCurrentRefresh) {
            return;
        }
        state.modelsTabReady = true;
        updateModelsTabVisibility();
        // Internal error paths may temporarily fall back to Settings while
        // modelsTabReady is false. Restore Models only when no explicit tab
        // selection occurred after this refresh started.
        if (
            refresh.preserveModelsTab
            && refresh.tabSelectionGeneration === state.providerTabSelectionGeneration
        ) {
            setProviderTab('models');
        }
    };

    const bindProviderEditTabs = () => {
        const tabs = dom.forms.edit?.tabs;
        if (!tabs) {
            return;
        }
        Object.entries(tabs).forEach(([key, button]) => {
            if (!button || button.dataset.bound === 'true') {
                return;
            }
            button.addEventListener('click', () => {
                const tab = button.dataset.tab || key;
                state.providerTabSelectionGeneration += 1;
                setProviderTab(tab);
            });
            button.addEventListener('keydown', (event) => {
                if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) {
                    return;
                }
                const visibleTabs = PROVIDER_TAB_KEYS
                    .map((tabKey) => ({ key: tabKey, button: tabs[tabKey] }))
                    .filter((tabEntry) => tabEntry.button && !tabEntry.button.hidden);
                const currentIndex = visibleTabs.findIndex((tabEntry) => tabEntry.button === button);
                if (currentIndex < 0 || visibleTabs.length < 2) {
                    return;
                }
                event.preventDefault();
                let nextIndex;
                if (event.key === 'Home') {
                    nextIndex = 0;
                } else if (event.key === 'End') {
                    nextIndex = visibleTabs.length - 1;
                } else {
                    const direction = ['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1;
                    nextIndex = (currentIndex + direction + visibleTabs.length) % visibleTabs.length;
                }
                const nextTab = visibleTabs[nextIndex];
                const nextButton = nextTab.button;
                setProviderTab(nextButton.dataset.tab || nextTab.key);
                nextButton.focus();
            });
            button.dataset.bound = 'true';
        });
    };

    const setDirtyFlag = (value) => {
        state.isDirty = Boolean(value);
    };

    const hasUnsavedChanges = () => (state.view === 'create' || state.view === 'edit') && state.isDirty;

    const markFormDirty = () => {
        if (state.view === 'create' || state.view === 'edit') {
            state.isDirty = true;
        }
    };

    const bindControlDirtyTracking = (control) => {
        if (!control || control.dataset.dirtyTracking === 'true') {
            return;
        }
        const tag = control.tagName?.toLowerCase();
        const type = control.type?.toLowerCase();
        const eventNames = (type === 'checkbox' || type === 'radio' || tag === 'select')
            ? ['change']
            : ['input', 'change'];
        eventNames.forEach((eventName) => control.addEventListener(eventName, markFormDirty));
        control.dataset.dirtyTracking = 'true';
    };

    const providerDependencyFieldExists = (dependencyKey) => {
        if (!dependencyKey) {
            return false;
        }
        return state.controls?.has?.(dependencyKey);
    };

    const getProviderFieldValue = (fieldKey) => {
        if (!fieldKey || !state.controls) {
            return undefined;
        }
        const control = state.controls.get(fieldKey);
        const field = state.definitions.get(fieldKey) || {};
        if (!(control instanceof HTMLElement)) {
            return undefined;
        }
        const fieldType = field.type || control.type;
        switch (fieldType) {
            case 'boolean':
            case 'toggle':
                if ('checked' in control) {
                    return Boolean(control.checked);
                }
                return control.value === 'true' || control.value === '1';
            case 'select':
                if (field.multiple && control.selectedOptions) {
                    return Array.from(control.selectedOptions).map((opt) => opt.value);
                }
                return control.value;
            case 'number':
                return control.value === '' ? null : Number(control.value);
            default:
                return control.value;
        }
    };

    const isProviderSingleDependencySatisfied = (dependencyKey, requiredValue) => {
        if (!dependencyKey) {
            return true;
        }
        if (!providerDependencyFieldExists(dependencyKey)) {
            return true;
        }
        const currentValue = getProviderFieldValue(dependencyKey);
        if (Array.isArray(currentValue)) {
            if (Array.isArray(requiredValue)) {
                return requiredValue.some((val) => currentValue.includes(String(val)));
            }
            return currentValue.includes(String(requiredValue));
        }
        if (typeof requiredValue === 'boolean') {
            return currentValue === requiredValue;
        }
        return String(currentValue) === String(requiredValue);
    };

    const isProviderDependencySatisfied = (field) => {
        if (!field) {
            return true;
        }
        const firstSatisfied = isProviderSingleDependencySatisfied(field.dependency, field.dependency_value);
        if (!firstSatisfied) {
            return false;
        }
        return isProviderSingleDependencySatisfied(field.dependency2, field.dependency2_value);
    };

    const updateProviderDependentFieldsVisibility = () => {
        if (!state.definitions?.size) {
            return;
        }
        state.definitions.forEach((field, key) => {
            if (!field?.dependency && !field?.dependency2) {
                return;
            }
            const control = state.controls.get(key);
            if (!(control instanceof HTMLElement) || typeof control.closest !== 'function') {
                return;
            }
            const row = control.closest('.settings-row');
            if (!row) {
                return;
            }
            const visible = isProviderDependencySatisfied(field);
            row.hidden = !visible;
            row.style.display = visible ? '' : 'none';
            if ('required' in control) {
                control.required = Boolean(visible && field.required);
            }
        });
        const formDom = activeFormDom();
        window.syncSectionBodyLastVisibleRow?.(formDom?.fields || null);
    };

    const attachProviderDependencyListeners = () => {
        if (!state.definitions?.size) {
            return;
        }
        const dependencyKeys = new Set();
        state.definitions.forEach((field) => {
            if (field?.dependency) {
                dependencyKeys.add(field.dependency);
            }
            if (field?.dependency2) {
                dependencyKeys.add(field.dependency2);
            }
        });
        if (!dependencyKeys.size) {
            return;
        }
        state.controls.forEach((control, key) => {
            if (!dependencyKeys.has(key) || !(control instanceof HTMLElement)) {
                return;
            }
            if (control.dataset.providerDependencyBound === 'true') {
                return;
            }
            const handler = () => updateProviderDependentFieldsVisibility();
            control.addEventListener('change', handler);
            if (control.tagName === 'INPUT' && control.type !== 'checkbox' && control.type !== 'radio') {
                control.addEventListener('input', handler);
            }
            control.dataset.providerDependencyBound = 'true';
        });
    };

    const requestUnsavedConfirmation = (onConfirm) => {
        if (typeof window.unsavedChangesManager?.confirmIfNeeded === 'function') {
            const prompted = window.unsavedChangesManager.confirmIfNeeded({
                id: UNSAVED_GUARD_ID,
                onConfirm,
            });
            if (prompted) {
                return;
            }
        }
        onConfirm?.();
    };

    const registerUnsavedGuard = () => {
        if (unsavedGuardRegistered || typeof window.unsavedChangesManager?.register !== 'function') {
            return;
        }
        window.unsavedChangesManager.register({
            id: UNSAVED_GUARD_ID,
            priority: 220,
            isActive: () => {
                const activePage = state.view === 'create'
                    ? dom.pages.create
                    : state.view === 'edit'
                        ? dom.pages.edit
                        : null;
                return Boolean(activePage && !activePage.hidden);
            },
            isDirty: () => hasUnsavedChanges(),
            discard: () => {
                setDirtyFlag(false);
                state.pendingNavigation = null;
            },
        });
        unsavedGuardRegistered = true;
    };

    const FIELD_META = {
        name: { titleKey: 'provider_field_name_label', title: 'Provider Name', placeholder: 'Enter provider display name' },
        api_key: { titleKey: 'provider_field_api_key_label', title: 'API Key', placeholder: 'Enter API key' },
        api_token: { titleKey: 'provider_field_api_token_label', title: 'API Token', placeholder: 'Enter API token' },
        api_secret: { titleKey: 'provider_field_api_secret_label', title: 'API Secret', placeholder: 'Enter API secret' },
        api_url: { titleKey: 'provider_field_api_url_label', title: 'API URL', placeholder: 'https://api.example.com' },
        region: { titleKey: 'provider_field_region_label', title: 'Region', placeholder: 'e.g. us-east-1' },
        model: { titleKey: 'provider_field_model_label', title: 'Default Model', placeholder: 'Enter default model ID' },
        models: { titleKey: 'provider_field_models_label', title: 'Models', placeholder: 'Comma separated model IDs' },
        project: { titleKey: 'provider_field_project_label', title: 'Project', placeholder: 'Enter project identifier' },
        organization: { titleKey: 'provider_field_organization_label', title: 'Organization', placeholder: 'Enter organization ID' },
        'settings.base_url': { titleKey: 'provider_field_base_url_label', title: 'Base URL', placeholder: 'https://api.example.com' },
        'settings.azure_endpoint': { titleKey: 'provider_field_azure_endpoint_label', title: 'Azure Endpoint', placeholder: 'https://my-resource.openai.azure.com' },
        'settings.api_version': { titleKey: 'provider_field_api_version_label', title: 'API Version', placeholder: 'preview or 2025-04-01-preview' },
        'settings.organization': { titleKey: 'provider_field_organization_label', title: 'Organization', placeholder: 'Enter organization ID' },
        'settings.project': { titleKey: 'provider_field_project_label', title: 'Project', placeholder: 'Enter project name' }
    };
    const PROVIDER_URL_SUGGESTIONS_METADATA_KEY = 'provider_url_suggestions';
    const CUSTOM_PROVIDER_URL_OPTION_VALUE = '__custom__';

    const OLLAMA_LOADED_MODELS_CLASS = 'provider-loaded-models';
    const OLLAMA_LOADED_MODELS_DOWNLOAD_CLASS = 'provider-loaded-models-download';
    const OLLAMA_DOWNLOAD_INPUT_ID = 'provider-ollama-download-input';
    const OLLAMA_VERSION_CLASS = 'provider-loaded-models-version';
    const OLLAMA_UNSUPPORTED_FORM_CLASS = 'provider-form-ollama-unsupported';
    const removeOllamaLoadedModelsSection = ({ showPlaceholder = true, message } = {}) => {
        const section = dom.forms.edit?.modelsContainer?.querySelector(`.${OLLAMA_LOADED_MODELS_CLASS}`);
        if (section) {
            section.remove();
        }
        state.ollamaDownloadInput = null;
        state.downloadingModel = false;
        state.ollamaActionState = {
            delete: { running: false },
            load: { running: false },
            unload: { running: false }
        };
        state.ollamaVersion = null;
        state.ollamaAllModels = null;
        state.lmstudioAllModels = [];
        state.lmstudioLoadedModels = [];
        state.lmstudioDownloadState = { running: false };
        state.lmstudioActionState = {
            load: { running: false },
            unload: { running: false }
        };
        state.lmstudioControls = {};
        state.secretPreviews = {};
        if (showPlaceholder) {
            setModelsPlaceholder(message);
        } else {
            hideModelsPlaceholder();
        }
        state.modelsTabReady = false;
    };

    const formatProviderLabel = (key = '') => window.formatProviderLabel?.(key) || '';

    const formatFieldTitle = (fieldId = '') => {
        const metadata = FIELD_META[fieldId];
        const fallback = metadata?.title || fieldId.replace(/settings\./g, '').replace(/[_.]/g, ' ')
            .split(' ')
            .filter(Boolean)
            .map((part) => part[0]?.toUpperCase() + part.slice(1))
            .join(' ');
        return metadata?.titleKey ? t(metadata.titleKey, fallback) : fallback;
    };

    const formatFieldDescription = (field = {}) => field.i18n_description
        ? t(field.i18n_description, field.description || '')
        : (field.description || (field.required ? '' : t('provider_field_optional_desc', 'Optional field')));

    const formatFieldLabel = (field = {}) => field.i18n_label
        ? t(field.i18n_label, field.label || formatFieldTitle(field.key))
        : (field.label || formatFieldTitle(field.key));

    const createProviderFieldLayout = (field) => createFieldLayout?.({
        ...field,
        label: formatFieldLabel(field),
        description: formatFieldDescription(field)
    }) ?? createFieldLayout(field);

    /**
     * Upgrade a provider-schema select to the shared admin single-select.
     *
     * Provider forms are rendered separately from the general admin schema
     * renderer. That means their native selects do not automatically pass
     * through the helper's custom-select enhancement. Keep the native select
     * as the authoritative form control, but move it into the shared widget
     * so value extraction, validation, and submission continue to use the
     * same element as before.
     *
     * @param {HTMLSelectElement} select The provider form select to enhance.
     * @param {object} field The backend field schema for the select.
     * @param {HTMLElement} row The field row containing the visible label.
     * @returns {?object} The shared select metadata, when available.
     */
    const enhanceProviderSelect = (select, field, row) => {
        if (!select || select.tagName !== 'SELECT' || typeof window.upgradeAdminSingleSelect !== 'function') {
            return null;
        }

        // The provider layout uses a paragraph as its visual field label. Give
        // it a stable ID and reference it from both the native source select
        // and the generated combobox trigger for an accessible name.
        const label = row?.querySelector?.('.settings-row-title');
        if (label && !select.hasAttribute('aria-labelledby')) {
            const labelId = `${select.id || field?.key || 'provider-select'}-label`;
            label.id = label.id || labelId;
            select.setAttribute('aria-labelledby', label.id);
        }

        const placeholder = resolveFieldPlaceholder(field)
            || t('admin_select_placeholder_single', 'Select an option...');
        const meta = window.upgradeAdminSingleSelect(select, {
            key: field?.key || select.id || 'provider-select',
            placeholder,
        });
        meta?.syncFromSelect?.();
        return meta;
    };

    const normalizeProviderUrl = (value) => String(value || '').trim().replace(/\/+$/, '');

    const getProviderUrlSuggestions = (field = {}) => {
        const suggestions = field?.metadata?.[PROVIDER_URL_SUGGESTIONS_METADATA_KEY];
        if (!Array.isArray(suggestions)) {
            return [];
        }
        return suggestions
            .map((entry) => {
                const name = String(entry?.name || '').trim();
                const url = String(entry?.url || '').trim();
                if (!name || !url) {
                    return null;
                }
                return { name, url };
            })
            .filter(Boolean);
    };

    const createProviderUrlSuggestionSelect = (field, control) => {
        const suggestions = getProviderUrlSuggestions(field);
        if (!suggestions.length || !control || control.tagName !== 'INPUT') {
            return null;
        }

        const wrapper = document.createElement('div');
        wrapper.className = 'provider-url-suggestion-stack';

        const select = document.createElement('select');
        select.className = 'provider-form-control provider-url-suggestion-select';
        select.setAttribute('aria-label', t('provider_url_suggestions_label', 'Suggested provider URL'));

        const customOption = document.createElement('option');
        customOption.value = CUSTOM_PROVIDER_URL_OPTION_VALUE;
        customOption.textContent = t('provider_url_suggestions_custom', 'Custom');
        select.appendChild(customOption);

        suggestions.forEach((suggestion) => {
            const option = document.createElement('option');
            option.value = suggestion.url;
            option.textContent = suggestion.name;
            select.appendChild(option);
        });

        let singleSelectMeta = null;

        const syncSelection = () => {
            const currentUrl = normalizeProviderUrl(control.value);
            const matched = suggestions.find((suggestion) => normalizeProviderUrl(suggestion.url) === currentUrl);
            select.value = matched ? matched.url : CUSTOM_PROVIDER_URL_OPTION_VALUE;
            singleSelectMeta?.syncFromSelect?.();
        };

        select.addEventListener('change', () => {
            if (select.value === CUSTOM_PROVIDER_URL_OPTION_VALUE) {
                syncSelection();
                return;
            }
            if (control.value !== select.value) {
                control.value = select.value;
                control.dispatchEvent(new Event('input', { bubbles: true }));
                control.dispatchEvent(new Event('change', { bubbles: true }));
            } else {
                syncSelection();
            }
        });

        control.addEventListener('input', syncSelection);
        control.addEventListener('change', syncSelection);
        control._providerUrlSuggestionSync = syncSelection;

        syncSelection();
        wrapper.appendChild(select);

        if (typeof window.upgradeAdminSingleSelect === 'function') {
            singleSelectMeta = window.upgradeAdminSingleSelect(select, {
                key: 'provider-url-suggestion-select',
                placeholder: t('admin_select_placeholder_single', 'Select an option...'),
            });
            singleSelectMeta?.syncFromSelect?.();
        }
        return wrapper;
    };

    const setView = (view) => {
        state.view = view;
        Object.entries(dom.pages).forEach(([key, element]) => {
            if (!element) {
                return;
            }
            if (key === view) {
                element.removeAttribute('hidden');
            } else {
                element.setAttribute('hidden', '');
            }
        });
    };

    const setMode = (mode) => {
        state.mode = mode;
        dom.active = dom.forms[mode === 'edit' ? 'edit' : 'create'];
        updateModelsTabVisibility();
    };

    const activeFormDom = () => dom.active || dom.forms.create;

    const clearFormState = () => {
        // Clear any existing field validation errors
        const formDom = activeFormDom();
        window.FieldValidation?.clearAllFieldErrors(formDom?.fields);
        state.schema = [];
        state.controls = new Map();
        state.definitions = new Map();
        state.formValues = null;
        state.ollamaModels = null;
        state.ollamaAllModels = null;
        state.downloadingModel = false;
        state.ollamaDownloadInput = null;
        state.ollamaActionState = {
            delete: { running: false },
            load: { running: false },
            unload: { running: false }
        };
        state.ollamaVersion = null;
        state.ollamaUnsupported = false;
        state.ollamaUnsupportedMessage = null;
        state.lmstudioAllModels = [];
        state.lmstudioLoadedModels = [];
        state.lmstudioDownloadState = { running: false };
        state.lmstudioActionState = {
            load: { running: false },
            unload: { running: false }
        };
        state.lmstudioControls = {};
        // Invalidate any request that was started for the form being cleared,
        // including the uncommon case where the same provider is reopened
        // before its previous request settles.
        state.modelsRefreshGeneration += 1;
        state.modelsTabReady = false;
        setDirtyFlag(false);
        if (state.mode === 'edit') {
            setProviderTab('settings');
        }
        if (formDom?.fields) {
            formDom.fields.innerHTML = '';
        }
        if (formDom?.loading) {
            formDom.loading.hidden = false;
        }
        removeOllamaLoadedModelsSection();
    };

    const showSelection = async (force = false) => {
        setMode('create');
        state.providerKey = null;
        state.editingId = null;
        state.editingData = null;
        state.active = true;
        setView('select');
        if (!state.available.length || force) {
            await loadAvailableProviders();
        } else {
            renderProviderGrid(state.available);
        }
    };

    const showCreateForm = async (providerKey) => {
        setMode('create');
        state.providerKey = providerKey;
        state.editingId = null;
        state.editingData = null;
        state.active = true;
        setView('create');
        const formDom = activeFormDom();
        formDom?.title && (formDom.title.textContent = formatT('provider_form_configure_title', 'Configure {provider}', {
            provider: formatProviderLabel(providerKey),
        }));
        formDom?.subtitle && (formDom.subtitle.textContent = formatT('provider_form_configure_subtitle', 'Provide the required details to finish setting up {provider}.', {
            provider: formatProviderLabel(providerKey),
        }));
        clearFormState();
        await loadProviderSchema(providerKey);
    };

    const goToProvidersList = () => {
        setMode('create');
        state.providerKey = null;
        state.editingId = null;
        state.editingData = null;
        state.active = true;
        clearFormState();
        setView('list');
        if (typeof initProvidersPage === 'function') {
            initProvidersPage();
        }
    };

    const loadAvailableProviders = async () => {
        renderProviderGrid([], { loading: true });
        try {
            const response = await authedFetch('/api/v1/llm/providers/available');
            if (!response.ok) {
                notifyError(t('providers_fetch_available_failed', 'Failed to fetch available providers'));
                return;
            }
            state.available = await response.json();
            renderProviderGrid(state.available);
        } catch (error) {
            console.error(error);
            notifyError(t('providers_fetch_available_failed', 'Failed to fetch available providers'));
            renderProviderGrid([], { error: true });
        }
    };

    const getProviderDisplayName = (provider = {}) => (
        String(provider?.name || formatProviderLabel(provider?.id) || provider?.id || '').trim()
    );

    const sortProvidersByName = (providers = []) => (
        [...providers].sort((a, b) => {
            const labelA = getProviderDisplayName(a);
            const labelB = getProviderDisplayName(b);
            const byLabel = labelA.localeCompare(labelB, undefined, {
                sensitivity: 'base',
                numeric: true,
            });
            if (byLabel !== 0) {
                return byLabel;
            }
            const idA = String(a?.id || '').toLowerCase();
            const idB = String(b?.id || '').toLowerCase();
            return idA.localeCompare(idB, undefined, { sensitivity: 'base', numeric: true });
        })
    );

    const renderProviderGrid = (providers = [], { loading, error } = {}) => {
        if (!dom.grid) {
            return;
        }
        dom.grid.innerHTML = '';
        if (loading) {
            dom.grid.appendChild(window.createAdminLoadingPlaceholder({
                message: t('providers_loading', 'Loading providers...'),
                className: 'provider-empty-state',
            }));
            return;
        }
        if (error || !providers.length) {
            dom.grid.appendChild(window.createAdminEmptyPlaceholder({
                title: error
                    ? t('providers_fetch_failed', 'Failed to load providers')
                    : t('provider_group_no_providers_available', 'No providers available. Create a provider first.'),
                description: error ? t('provider_groups_load_failed_text', 'Please try refreshing the page.') : '',
                icon: Icons?.omlorix || '',
                className: 'provider-empty-state',
            }));
            return;
        }
        const fragment = document.createDocumentFragment();
        const sortedProviders = sortProvidersByName(providers);
        sortedProviders.forEach((provider) => {
            if (!provider?.id) {
                return;
            }
            const card = document.createElement('div');
            card.className = 'available-provider-card';
            card.dataset.providerId = provider.id;
            let key = provider.id.toLowerCase();
            if (key === 'openai_responses' || key === 'openai_chat_completions') {
                key = 'openai';
            } else if (key === 'microsoft_azure') {
                key = 'microsoft';
            } else if (key === "anthropic_base") {
                key = 'anthropic';
            }
            const fallbackIcon = Icons?.omlorix || '';
            // Always uniquify IDs inside preset SVGs. Hidden admin pages remain
            // mounted, and duplicate gradient/clip/image IDs otherwise corrupt
            // later copies of complex icons such as Google AI Studio.
            const providerIcon = window.IconPicker?.renderIconMarkup
                ? window.IconPicker.renderIconMarkup(key, {
                    fallback: fallbackIcon,
                    imageAlt: t('providers_icon_alt', 'Provider icon'),
                })
                : (Icons?.[key] || fallbackIcon);
            card.innerHTML = `
                <div class="available-provider-icon">${providerIcon}</div>
                <div class="available-provider-card-title">${getProviderDisplayName(provider)}</div>`;
            card.addEventListener('click', () => {
                showCreateForm(provider.id);
            });
            fragment.appendChild(card);
        });
        dom.grid.appendChild(fragment);
    };

    const resolveProviderEditContext = (providerId, meta = {}) => {
        const context = { ...meta };
        if ((!context.providerKey || !context.name) && providerId && typeof document !== 'undefined') {
            const selector = `.provider-row[data-provider-id="${providerId}"]`;
            const row = document.querySelector(selector);
            if (row) {
                if (!context.providerKey && row.dataset.providerKey) {
                    context.providerKey = row.dataset.providerKey;
                }
                if (!context.name && row.dataset.providerName) {
                    context.name = row.dataset.providerName;
                }
            }
        }
        return context;
    };

    const openEditProvider = async (providerId, providerKey = null, meta = {}) => {
        if (!providerId) {
            notifyError(t('providers_delete_failed_resolve_id', 'Failed to resolve provider ID.'));
            return;
        }
        const context = resolveProviderEditContext(providerId, { providerKey, name: meta?.name });
        const resolvedProviderKey = context.providerKey;
        if (!resolvedProviderKey) {
            notifyError(t('providers_delete_failed_resolve_type', 'Failed to resolve provider type.'));
            return;
        }
        setMode('edit');
        state.providerKey = resolvedProviderKey;
        state.editingId = providerId;
        state.editingData = {
            name: context.name || null,
        };
        setView('edit');
        updateModelsTabVisibility();
        setProviderTab('settings');
        const formDom = activeFormDom();
        const label = formatProviderLabel(resolvedProviderKey) || context.name || 'Provider';
        if (formDom?.title) {
            formDom.title.textContent = formatT('provider_form_edit_title', 'Edit {provider}', { provider: label });
        }
        if (formDom?.subtitle) {
            formDom.subtitle.textContent = formatT('provider_form_edit_subtitle', 'Update the settings for {provider}.', { provider: label });
        }
        clearFormState();
        try {
            updateModelsTabVisibility();
            await loadProviderSchema(state.providerKey);
            await refreshProviderExtras();
        } catch (error) {
            console.error('Failed to open provider for editing', error);
            notifyError(error?.message || t('providers_fetch_failed', 'Failed to load providers'));
            formDom?.loading && (formDom.loading.hidden = true);
        }
    };

    const getNestedValue = (source, path) => {
        if (!source || !path) {
            return undefined;
        }
        return path.split('.').reduce((acc, key) => (acc == null ? acc : acc[key]), source);
    };

    const normalizeProviderSections = (payload) => {
        if (!payload) {
            return [];
        }

        let rawSections;
        if (Array.isArray(payload?.sections)) {
            rawSections = payload.sections;
        } else if (Array.isArray(payload)) {
            rawSections = [{ fields: payload }];
        } else if (Array.isArray(payload?.fields)) {
            rawSections = [{ ...payload, title: payload.title ?? null, description: payload.description ?? null }];
        } else {
            rawSections = [];
        }

        return rawSections
            .map((section = {}) => {
                const fields = Array.isArray(section.fields) ? section.fields.filter(Boolean) : [];
                return fields.length
                    ? {
                        title: section.title ?? null,
                        description: section.description ?? null,
                        i18n_title: section.i18n_title ?? null,
                        i18n_description: section.i18n_description ?? null,
                        fields,
                    }
                    : null;
            })
            .filter(Boolean);
    };

    function buildValuesFromSections(sections = []) {
        const values = {};
        const secrets = {};
        sections.forEach((section) => {
            (section?.fields || []).forEach((field) => {
                if (!field?.key) {
                    return;
                }
                if (typeof field.value === 'undefined') {
                    return;
                }
                if (SECRET_PLACEHOLDER_FIELDS.includes(field.key)) {
                    secrets[field.key] = field.value;
                    return;
                }
                setNestedValue(values, field.key.split('.'), field.value);
            });
        });
        return { values, secrets };
    }

    const populateControlsFromValues = (values = {}) => {
        if (!values || !state.controls?.size) {
            return;
        }
        state.controls.forEach((control, key) => {
            const fieldDef = state.definitions.get(key) || {};
            const value = getNestedValue(values, key);
            if (value !== undefined) {
                applyFieldValue(control, fieldDef, value);
            }
            applySecretPlaceholder(control, key);
        });
        setDirtyFlag(false);
    };

    const loadProviderSchema = async (providerKey) => {
        const formDom = activeFormDom();
        if (!providerKey || !formDom?.loading) {
            return;
        }
        formDom.loading.hidden = false;
        try {
            // Fetch schema - when editing, pass provider_id to get values populated in schema
            const providerId = state.mode === 'edit' ? state.editingId : null;
            const schema = await window.providersApi.fetchProviderSchema(providerKey, providerId);
            const normalizedSections = normalizeProviderSections(schema);
            const { values, secrets } = buildValuesFromSections(normalizedSections);
            if (state.mode === 'create') {
                values.name = formatProviderLabel(providerKey);
            }
            state.schema = normalizedSections;
            state.formValues = values;
            state.secretPreviews = secrets;
            if (state.mode === 'edit') {
                const derivedName = values?.name || state.editingData?.name || '';
                state.editingData = {
                    ...(state.editingData || {}),
                    name: derivedName,
                };
            }
            renderProviderForm(normalizedSections, values);
            populateControlsFromValues(values);
        } catch (error) {
            console.error('Failed to load provider schema', error);
            notifyError(error?.message || t('provider_form_load_failed', 'Failed to load provider configuration. Please try again.'));
            formDom.loading.hidden = true;
        }
    };

    const applyFieldValue = (control, field, value) => {
        if (!control) {
            return;
        }
        applyControlValue?.(control, field, value ?? field.default ?? '');
    };

    const SECRET_PLACEHOLDER_FIELDS = ['api_key'];

    const applySecretPlaceholder = (control, fieldKey) => {
        if (!control || !SECRET_PLACEHOLDER_FIELDS.includes(fieldKey)) {
            return;
        }
        const preview = state.secretPreviews[fieldKey];
        if (preview) {
            control.placeholder = preview;
        }
    };

    const renderProviderForm = (sections = [], values = null) => {
        const formDom = activeFormDom();
        if (!formDom?.fields) {
            return;
        }
        formDom.fields.innerHTML = '';
        formDom.loading && (formDom.loading.hidden = true);
        if (!sections.length) {
            formDom.fields.innerHTML = `<p class="provider-form-empty">${t('provider_form_no_configuration', 'No configuration required for this provider.')}</p>`;
            return;
        }
        const fragment = document.createDocumentFragment();
        state.controls = new Map();
        state.definitions = new Map();

        sections.forEach((section) => {
            const sectionEl = document.createElement('section');
            sectionEl.classList.add('settings-section', 'provider-settings-section');

            if (section.title || section.description) {
                const headerEl = document.createElement('div');
                headerEl.classList.add('settings-section-header');

                if (section.title) {
                    const titleEl = document.createElement('h3');
                    titleEl.classList.add('settings-section-title');
                    titleEl.textContent = (section.i18n_title && typeof window.getTranslation === 'function')
                        ? window.getTranslation(section.i18n_title, section.title)
                        : section.title;
                    headerEl.appendChild(titleEl);
                }

                if (section.description) {
                    const descriptionEl = document.createElement('p');
                    descriptionEl.classList.add('settings-section-description');
                    descriptionEl.textContent = (section.i18n_description && typeof window.getTranslation === 'function')
                        ? window.getTranslation(section.i18n_description, section.description)
                        : section.description;
                    headerEl.appendChild(descriptionEl);
                }

                sectionEl.appendChild(headerEl);
            }

            const bodyEl = document.createElement('div');
            bodyEl.classList.add('settings-section-body');

            section.fields.forEach((field) => {
                if (!field?.key) {
                    return;
                }

                const fieldDef = { ...field };
                const isSecretField = SECRET_PLACEHOLDER_FIELDS.includes(fieldDef.key);
                if (isSecretField) {
                    if (state.mode === 'edit') {
                        // Existing providers may retain the stored secret when
                        // the administrator leaves this input untouched.
                        fieldDef.required = false;
                    } else if (providerRequiresApiKey(state.providerKey)) {
                        // Enforce the backend credential policy even when an
                        // older provider schema omitted the required flag.
                        fieldDef.required = true;
                    }
                }

                // Native providers have a fixed brand icon. Skip an icon
                // field even if an older server still returns one; this keeps
                // the edit form correct during rolling upgrades as well.
                const isProviderIconField = fieldDef.key === 'icon';
                if (isProviderIconField && !providerSupportsCustomIcon(state.providerKey)) {
                    return;
                }

                // Custom compatible providers use the shared accessible icon
                // picker instead of a plain schema text input.
                const isIconField = isProviderIconField && providerSupportsCustomIcon(state.providerKey);
                if (isIconField && window.IconPicker) {
                    const existingValue = values ? getNestedValue(values, fieldDef.key) : (fieldDef.default || state.providerKey || '');
                    const { row, control } = window.IconPicker.createIconPickerControl(
                        {
                            ...fieldDef,
                            label: field.label || 'Provider Icon',
                            description: field.description || 'Select a preset icon or provide a custom SVG for this provider.',
                            iconPresetType: 'provider',
                        },
                        existingValue,
                        markFormDirty
                    );
                    row.classList.add('settings-row-provider');
                    bodyEl.appendChild(row);
                    state.controls.set(field.key, control);
                    state.definitions.set(field.key, field);
                    return;
                }

                const { row, controlWrapper } = createProviderFieldLayout(fieldDef);
                row.classList.add('settings-row-provider');
                controlWrapper.classList.add('settings-row-provider-control');
                const inputEl = createFieldInput(fieldDef);
                const control = resolveControlElement(inputEl);
                if (!control) {
                    return;
                }
                control.id = `provider-field-${fieldDef.key.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
                control.name = fieldDef.key;
                if (fieldDef.required) {
                    control.required = true;
                }
                const providerUrlSuggestionSelect = createProviderUrlSuggestionSelect(fieldDef, control);
                if (providerUrlSuggestionSelect) {
                    controlWrapper.appendChild(providerUrlSuggestionSelect);
                }
                controlWrapper.appendChild(inputEl);
                // Provider schemas can contain regular single selects (for
                // example Google AI Studio's API version). Enhance those
                // controls after they have a parent so the shared adapter can
                // replace the visible native UI while keeping the native
                // select available for form state and payload extraction.
                if (control.tagName === 'SELECT') {
                    enhanceProviderSelect(control, fieldDef, row);
                }
                row.appendChild(controlWrapper);
                bodyEl.appendChild(row);

                state.controls.set(fieldDef.key, control);
                state.definitions.set(fieldDef.key, fieldDef);
                bindControlDirtyTracking(control);
                const existingValue = values ? getNestedValue(values, fieldDef.key) : undefined;
                if (existingValue !== undefined) {
                    applyFieldValue(control, fieldDef, existingValue);
                }
                control._providerUrlSuggestionSync?.();
                applySecretPlaceholder(control, fieldDef.key);
            });

            sectionEl.appendChild(bodyEl);
            fragment.appendChild(sectionEl);
        });

        formDom.fields.appendChild(fragment);
        if (state.mode === 'create') {
            setDirtyFlag(false);
        }

        // Dependency handling
        attachProviderDependencyListeners();
        updateProviderDependentFieldsVisibility();

        // Attach error clear listeners for validation
        const controlsArray = [];
        state.definitions.forEach((field, key) => {
            const control = state.controls.get(key);
            if (control) {
                controlsArray.push({ field, control });
            }
        });
        window.FieldValidation?.attachErrorClearListeners(controlsArray);
    };

    const ensureOllamaLoadedModelsSection = () => {
        if (state.ollamaUnsupported) {
            removeOllamaLoadedModelsSection();
            return null;
        }
        const container = dom.forms.edit?.modelsContainer;
        if (!container) {
            return null;
        }
        hideModelsPlaceholder();
        let section = container.querySelector(`.${OLLAMA_LOADED_MODELS_CLASS}`);
        if (!section) {
            section = document.createElement('div');
            section.className = OLLAMA_LOADED_MODELS_CLASS;
            container.appendChild(section);
        }
        return section;
    };

    const ensureOllamaLoadedModelsBody = () => {
        const section = ensureOllamaLoadedModelsSection();
        if (!section) {
            return null;
        }
        let body = section.querySelector('.ollama-models-table-container');
        if (!body) {
            body = document.createElement('div');
            body.className = 'ollama-models-table-container';
            section.appendChild(body);
        }
        return body;
    };

    const setOllamaLoadedModelsMessage = (message, type = 'info') => {
        const body = ensureOllamaLoadedModelsBody();
        if (!body) {
            return;
        }
        body.innerHTML = '';
        const paragraph = document.createElement('p');
        paragraph.className = `ollama-models-empty`;
        paragraph.textContent = message;
        body.appendChild(paragraph);
    };

    /**
     * Creates a compact icon button placed next to the input of a model
     * management row, e.g. the trigger that starts a model download.
     *
     * @param {{ label: string, iconSvg?: string, onClick?: Function }} options
     * @returns {HTMLButtonElement} The rendered action button.
     */
    const createRowActionButton = ({ label, iconSvg, onClick }) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'ollama-models-row-button';
        button.title = label;
        button.setAttribute('aria-label', label);
        if (iconSvg) {
            // SVG markup comes from the trusted shared icons.js registry.
            button.innerHTML = iconSvg;
        }
        if (typeof onClick === 'function') {
            button.addEventListener('click', onClick);
        }
        return button;
    };

    /**
     * Builds the minimal download progress panel shown below a download row
     * while a model pull streams progress events. The layout follows a quiet
     * "icon + headline + slim bar + details" pattern and is shared between the
     * Ollama and LM Studio download rows.
     *
     * @param {{ titleId: string }} options
     * @returns {{ wrapper: HTMLElement, icon: HTMLElement, title: HTMLElement, percent: HTMLElement, bar: HTMLElement, fill: HTMLElement, text: HTMLElement }}
     */
    const createModelDownloadProgress = ({ titleId }) => {
        const wrapper = document.createElement('div');
        wrapper.className = 'ollama-models-progress';
        wrapper.hidden = true;

        const icon = document.createElement('span');
        icon.className = 'ollama-models-progress-icon';
        icon.setAttribute('aria-hidden', 'true');
        if (window.Icons?.download) {
            icon.innerHTML = window.Icons.download;
        }

        const main = document.createElement('div');
        main.className = 'ollama-models-progress-main';

        const headline = document.createElement('div');
        headline.className = 'ollama-models-progress-headline';

        const progressTitle = document.createElement('span');
        progressTitle.className = 'ollama-models-progress-title';
        progressTitle.id = titleId;

        const progressPercent = document.createElement('span');
        progressPercent.className = 'ollama-models-progress-percent';

        headline.append(progressTitle, progressPercent);

        const progressBar = document.createElement('div');
        progressBar.className = 'ollama-models-progress-bar';
        progressBar.setAttribute('role', 'progressbar');
        progressBar.setAttribute('aria-valuemin', '0');
        progressBar.setAttribute('aria-valuemax', '100');
        progressBar.setAttribute('aria-valuenow', '0');
        // The bar is named by the headline so screen readers announce e.g.
        // "Downloading model" for the progressbar.
        progressBar.setAttribute('aria-labelledby', titleId);

        const progressFill = document.createElement('div');
        progressFill.className = 'ollama-models-progress-fill';
        progressBar.appendChild(progressFill);

        const progressText = document.createElement('p');
        progressText.className = 'ollama-models-progress-text';

        main.append(headline, progressBar, progressText);
        wrapper.append(icon, main);

        return { wrapper, icon, title: progressTitle, percent: progressPercent, bar: progressBar, fill: progressFill, text: progressText };
    };

    /**
     * Pushes a download progress update into the shared progress panel refs.
     *
     * @param {object} refs Progress element refs from createModelDownloadProgress.
     * @param {object} progress Normalized progress payload ({ status, completed, total, percent }).
     * @param {string} defaultStatus Fallback status text when the payload has none.
     */
    const applyModelDownloadProgress = (refs, progress, defaultStatus) => {
        if (!refs?.wrapper) {
            return;
        }
        refs.wrapper.hidden = false;
        const percent = typeof progress.percent === 'number'
            ? Math.min(100, Math.max(0, progress.percent))
            : null;
        if (refs.fill && percent !== null) {
            refs.fill.style.width = `${percent}%`;
        }
        if (refs.bar && percent !== null) {
            // Unknown progress must not replace the last value announced to
            // assistive technology with a misleading zero.
            refs.bar.setAttribute('aria-valuenow', String(Math.round(percent)));
        }
        if (refs.percent) {
            refs.percent.textContent = percent !== null ? `${Math.round(percent)}%` : '';
        }
        if (refs.text) {
            const completedSize = formatDownloadSize(progress.completed);
            const totalSize = formatDownloadSize(progress.total);
            const pieces = [progress.status || defaultStatus];
            if (completedSize && totalSize) {
                pieces.push(`${completedSize} / ${totalSize}`);
            } else if (completedSize) {
                pieces.push(completedSize);
            }
            refs.text.textContent = pieces.filter(Boolean).join(' • ');
        }
    };

    /**
     * Resets the shared progress panel refs back to the hidden empty state.
     *
     * @param {object} refs Progress element refs from createModelDownloadProgress.
     */
    const resetModelDownloadProgress = (refs) => {
        if (!refs) {
            return;
        }
        if (refs.wrapper) {
            refs.wrapper.hidden = true;
        }
        if (refs.fill) {
            refs.fill.style.width = '0%';
        }
        if (refs.bar) {
            refs.bar.setAttribute('aria-valuenow', '0');
        }
        if (refs.title) {
            refs.title.textContent = '';
        }
        if (refs.percent) {
            refs.percent.textContent = '';
        }
        if (refs.text) {
            refs.text.textContent = '';
        }
    };

    /**
     * Populates a native model select with a placeholder option plus one
     * option per model, preserving the current selection when it still
     * exists, and syncs the enhanced admin-select UI if it is attached.
     *
     * @param {HTMLSelectElement} select The select to populate.
     * @param {Array<{ value: string, label: string }>} options Model options.
     * @param {string} placeholder Label of the empty placeholder option.
     */
    const setModelSelectOptions = (select, options, placeholder) => {
        if (!select) {
            return;
        }
        const previousValue = select.value;
        select.innerHTML = '';

        const placeholderOption = document.createElement('option');
        placeholderOption.value = '';
        placeholderOption.textContent = placeholder;
        select.appendChild(placeholderOption);

        (options || []).forEach(({ value, label }) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label || value;
            select.appendChild(option);
        });

        select.value = (options || []).some((option) => option.value === previousValue) ? previousValue : '';
        // Keep the enhanced custom dropdown in sync with the rebuilt options.
        select._singleSelect?.refreshOptions?.();
        select._singleSelect?.syncFromSelect?.();
    };

    /**
     * Upgrades a native model select to the shared searchable admin select
     * component when it is available. Without the enhancement the native
     * select keeps working, just without the styled dropdown.
     *
     * @param {HTMLSelectElement} select The select to upgrade.
     * @param {{ placeholder: string }} options
     * @returns {?object} The enhancement meta (`_singleSelect`) or null.
     */
    const enhanceModelActionSelect = (select, { placeholder }) => {
        if (!select || typeof window.upgradeAdminSingleSelect !== 'function') {
            return null;
        }
        const meta = window.upgradeAdminSingleSelect(select, {
            key: select.id || 'model-action',
            searchable: true,
            placeholder,
        });
        meta?.syncFromSelect?.();
        return meta;
    };

    /**
     * Toggles the disabled state of a model action control, keeping the
     * enhanced admin-select trigger (a separate button element) in sync.
     *
     * @param {HTMLElement} element Native input or select element.
     * @param {boolean} disabled Whether the control should be disabled.
     */
    const setModelControlDisabled = (element, disabled) => {
        if (!element) {
            return;
        }
        element.disabled = Boolean(disabled);
        const trigger = element._singleSelect?.wrapper?.querySelector('.admin-select-trigger');
        if (trigger) {
            trigger.disabled = Boolean(disabled);
        }
    };

    /**
     * Clears the value of a model action control and syncs the enhanced
     * admin-select summary back to the placeholder label.
     *
     * @param {HTMLElement} element Native input or select element.
     */
    const clearModelControlValue = (element) => {
        if (!element) {
            return;
        }
        element.value = '';
        element._singleSelect?.syncFromSelect?.();
    };

    const createOllamaRow = ({
        title,
        description,
        inputId,
        placeholder,
        onKeydown,
        showProgress = false,
        progressTitleId = null,
        useSelect = false,
        selectPlaceholder = null,
        action = null
    }) => {
        const row = document.createElement('div');
        row.className = 'ollama-models-row';

        const left = document.createElement('div');
        left.className = 'ollama-models-row-left';

        const titleEl = document.createElement('label');
        titleEl.className = 'ollama-models-row-title';
        titleEl.htmlFor = inputId;
        titleEl.textContent = title;
        left.appendChild(titleEl);

        const descEl = document.createElement('p');
        descEl.className = 'ollama-models-row-desc';
        descEl.textContent = description;
        left.appendChild(descEl);

        row.appendChild(left);

        const right = document.createElement('div');
        right.className = 'ollama-models-row-right';

        const inputWrapper = document.createElement('div');
        inputWrapper.className = 'ollama-models-input-wrapper';

        let input;
        if (useSelect) {
            // Select rows (load/unload/delete) pick from the models the
            // server actually knows about instead of free-text entry.
            input = document.createElement('select');
            input.id = inputId;
            input.className = 'ollama-models-input';
            input.setAttribute('aria-label', title);
            inputWrapper.appendChild(input);
            enhanceModelActionSelect(input, { placeholder: selectPlaceholder || placeholder || title });
        } else {
            input = document.createElement('input');
            input.type = 'text';
            input.id = inputId;
            input.className = 'ollama-models-input';
            input.placeholder = placeholder;
            input.autocomplete = 'off';
            if (onKeydown) {
                input.addEventListener('keydown', onKeydown);
            }
            inputWrapper.appendChild(input);
        }

        right.appendChild(inputWrapper);

        let actionButton = null;
        if (action) {
            actionButton = createRowActionButton(action);
            right.appendChild(actionButton);
        }

        row.appendChild(right);

        let progress = null;
        if (showProgress) {
            // The progress panel spans the full row width below the input.
            progress = createModelDownloadProgress({ titleId: progressTitleId });
            row.appendChild(progress.wrapper);
        }

        const statusEl = document.createElement('p');
        statusEl.className = 'ollama-models-row-status';
        statusEl.setAttribute('role', 'status');
        statusEl.hidden = true;
        row.appendChild(statusEl);

        return { row, input, statusEl, progress, actionButton };
    };

    /**
     * Builds select options for models downloaded on this Ollama instance.
     *
     * @returns {Array<{ value: string, label: string }>} Sorted model options.
     */
    const getOllamaDownloadedModelOptions = () => {
        if (!Array.isArray(state.ollamaAllModels)) {
            return [];
        }
        const seen = new Set();
        return state.ollamaAllModels
            .map((model) => String(model?.id || model?.model || model?.name || '').trim())
            .filter((name) => {
                if (!name || seen.has(name)) {
                    return false;
                }
                seen.add(name);
                return true;
            })
            .sort((a, b) => a.localeCompare(b))
            .map((name) => ({ value: name, label: name }));
    };

    /**
     * Builds select options for the models currently loaded in memory.
     *
     * @returns {Array<{ value: string, label: string }>} Sorted model options.
     */
    const getOllamaLoadedModelOptions = () => {
        if (!Array.isArray(state.ollamaModels)) {
            return [];
        }
        const seen = new Set();
        return state.ollamaModels
            .map((model) => String(model?.name || model?.model || '').trim())
            .filter((name) => {
                if (!name || seen.has(name)) {
                    return false;
                }
                seen.add(name);
                return true;
            })
            .sort((a, b) => a.localeCompare(b))
            .map((name) => ({ value: name, label: name }));
    };

    /**
     * Validates the selected model of a load/unload/delete select and runs
     * the action. Shared by the row action buttons.
     *
     * @param {string} actionKey One of "load", "unload", "delete".
     */
    const requestOllamaAction = async (actionKey) => {
        const elements = getOllamaActionElements(actionKey);
        const model = String(elements?.input?.value || '').trim();
        if (!model) {
            setOllamaActionStatus(actionKey, t('provider_ollama_action_required_selection', 'Please select a model.'), 'error');
            return;
        }
        await performOllamaAction(actionKey, model);
    };

    const ensureOllamaDownloadControls = () => {
        const section = ensureOllamaLoadedModelsSection();
        if (!section) {
            return null;
        }

        let downloadSection = section.querySelector('.ollama-models-download-section');
        if (!downloadSection) {
            downloadSection = document.createElement('section');
            downloadSection.className = 'ollama-models-section ollama-models-download-section';

            const header = document.createElement('div');
            header.className = 'ollama-models-header';

            const title = document.createElement('h3');
            title.className = 'ollama-models-title';
            title.textContent = t('provider_ollama_model_management_title', 'Model Management');

            const desc = document.createElement('p');
            desc.className = 'ollama-models-description';
            desc.textContent = t('provider_ollama_model_management_desc', 'Download, load, unload, or delete models from this Ollama instance.');

            header.appendChild(title);
            header.appendChild(desc);
            downloadSection.appendChild(header);

            const body = document.createElement('div');
            body.className = 'ollama-models-body';

            const { row: downloadRow, input: downloadInput } = createOllamaRow({
                title: t('provider_ollama_download_title', 'Download model'),
                description: t('provider_ollama_download_desc', 'Pull a model from the Ollama library'),
                inputId: OLLAMA_DOWNLOAD_INPUT_ID,
                placeholder: t('provider_ollama_download_placeholder', 'e.g. llama3:latest'),
                onKeydown: handleOllamaDownloadKeydown,
                showProgress: true,
                progressTitleId: 'provider-ollama-download-progress-title'
            });
            downloadRow.classList.add(OLLAMA_LOADED_MODELS_DOWNLOAD_CLASS);
            // Dedicated download trigger button next to the input; pressing
            // Enter inside the input starts the same download flow.
            const downloadButton = createRowActionButton({
                label: t('provider_ollama_download_title', 'Download model'),
                iconSvg: window.Icons?.download,
                onClick: () => requestOllamaModelDownload()
            });
            downloadRow.querySelector('.ollama-models-row-right')?.appendChild(downloadButton);
            body.appendChild(downloadRow);

            // Load, unload, and delete pick a model from a searchable list of
            // the models this Ollama instance actually knows about. Each row
            // gets a compact icon button that runs the action.
            const selectPlaceholder = t('provider_ollama_model_select_placeholder', 'Select a model...');
            const downloadedOptions = getOllamaDownloadedModelOptions();
            const actionRowDefs = [
                {
                    actionKey: 'load',
                    title: t('provider_ollama_load_title', 'Load model'),
                    description: t('provider_ollama_load_desc', 'Load a downloaded model into memory'),
                    inputId: 'ollama-load-select',
                    iconSvg: window.Icons?.play,
                    options: downloadedOptions
                },
                {
                    actionKey: 'unload',
                    title: t('provider_ollama_unload_title', 'Unload model'),
                    description: t('provider_ollama_unload_desc', 'Remove a model from memory'),
                    inputId: 'ollama-unload-select',
                    iconSvg: window.Icons?.stop,
                    options: getOllamaLoadedModelOptions()
                },
                {
                    actionKey: 'delete',
                    title: t('provider_ollama_delete_title', 'Delete model'),
                    description: t('provider_ollama_delete_desc', 'Permanently remove a model from disk'),
                    inputId: 'ollama-delete-select',
                    iconSvg: window.Icons?.trash,
                    options: downloadedOptions
                }
            ];
            actionRowDefs.forEach((def) => {
                const { row: actionRow, input: actionSelect } = createOllamaRow({
                    title: def.title,
                    description: def.description,
                    inputId: def.inputId,
                    placeholder: selectPlaceholder,
                    selectPlaceholder,
                    useSelect: true,
                    action: {
                        label: def.title,
                        iconSvg: def.iconSvg,
                        onClick: () => requestOllamaAction(def.actionKey)
                    }
                });
                actionRow.classList.add('provider-loaded-models-action', `provider-loaded-models-action-${def.actionKey}`);
                actionSelect.dataset.actionKey = def.actionKey;
                setModelSelectOptions(actionSelect, def.options, selectPlaceholder);
                body.appendChild(actionRow);
            });

            downloadSection.appendChild(body);

            const tableContainer = section.querySelector('.ollama-models-table-container');
            if (tableContainer) {
                section.insertBefore(downloadSection, tableContainer);
            } else {
                section.appendChild(downloadSection);
            }

            state.ollamaDownloadInput = downloadInput;
        }

        return downloadSection;
    };

    const ensureOllamaVersionBlock = () => {
        const section = ensureOllamaLoadedModelsSection();
        if (!section) {
            return null;
        }
        let container = section.querySelector(`.${OLLAMA_VERSION_CLASS}`);
        if (!container) {
            container = document.createElement('div');
            container.className = `${OLLAMA_VERSION_CLASS} ollama-models-version`;

            const label = document.createElement('span');
            label.className = 'ollama-models-version-label';
            label.textContent = t('provider_ollama_version_label', 'Version:');
            container.appendChild(label);

            const status = document.createElement('span');
            status.className = 'ollama-models-version-value';
            status.textContent = t('provider_ollama_version_checking', 'Checking version...');
            container.appendChild(status);

            const downloadSection = section.querySelector('.ollama-models-download-section');
            if (downloadSection) {
                const header = downloadSection.querySelector('.ollama-models-header');
                if (header) {
                    header.appendChild(container);
                } else {
                    downloadSection.insertBefore(container, downloadSection.firstChild);
                }
            } else {
                section.insertBefore(container, section.firstChild);
            }
        }
        return container;
    };

    const getOllamaDownloadElements = () => {
        const section = dom.forms.edit?.form?.querySelector(`.${OLLAMA_LOADED_MODELS_CLASS}`);
        if (!section) {
            return null;
        }
        const container = section.querySelector(`.${OLLAMA_LOADED_MODELS_DOWNLOAD_CLASS}`);
        if (!container) {
            return null;
        }
        return {
            container,
            input: container.querySelector('.ollama-models-input'),
            actionButton: container.querySelector('.ollama-models-row-button'),
            progressWrapper: container.querySelector('.ollama-models-progress'),
            progressTitle: container.querySelector('.ollama-models-progress-title'),
            progressPercent: container.querySelector('.ollama-models-progress-percent'),
            progressBar: container.querySelector('.ollama-models-progress-bar'),
            progressFill: container.querySelector('.ollama-models-progress-fill'),
            progressText: container.querySelector('.ollama-models-progress-text')
        };
    };

    const setOllamaDownloadStatus = (message, tone = 'info') => {
        const elements = getOllamaDownloadElements();
        const statusEl = elements?.container?.querySelector('.ollama-models-row-status');
        if (!statusEl) {
            return;
        }
        statusEl.hidden = !message;
        statusEl.textContent = message || '';
        statusEl.dataset.tone = tone;
    };

    const resetOllamaDownloadProgress = () => {
        const elements = getOllamaDownloadElements();
        resetModelDownloadProgress({
            wrapper: elements?.progressWrapper,
            fill: elements?.progressFill,
            bar: elements?.progressBar,
            title: elements?.progressTitle,
            percent: elements?.progressPercent,
            text: elements?.progressText
        });
    };

    const updateOllamaDownloadProgress = (progress) => {
        const elements = getOllamaDownloadElements();
        if (!elements) {
            return;
        }
        applyModelDownloadProgress({
            wrapper: elements.progressWrapper,
            fill: elements.progressFill,
            bar: elements.progressBar,
            title: elements.progressTitle,
            percent: elements.progressPercent,
            text: elements.progressText
        }, progress, t('provider_ollama_download_progress_default', 'Downloading...'));
    };

    /**
     * Validates the current download input value and starts the model
     * download. Shared between the Enter key handler and the download
     * trigger button.
     */
    const requestOllamaModelDownload = async () => {
        const model = getOllamaDownloadElements()?.input?.value?.trim();
        if (!model) {
            setOllamaDownloadStatus(t('provider_ollama_action_required_name', 'Please provide a model name.'), 'error');
            return;
        }
        if (state.downloadingModel) {
            setOllamaDownloadStatus(t('provider_ollama_download_status_in_progress', 'A download is already in progress. Please wait...'), 'info');
            return;
        }
        await downloadOllamaModel(model);
    };

    const handleOllamaDownloadKeydown = async (event) => {
        if (event.key !== 'Enter') {
            return;
        }
        event.preventDefault();
        await requestOllamaModelDownload();
    };

    const mapProgressPayload = (payload) => {
        const result = {
            status: payload?.status || '',
            completed: typeof payload?.completed === 'number' ? payload.completed : undefined,
            total: typeof payload?.total === 'number' ? payload.total : undefined,
            percent: typeof payload?.percent === 'number' ? payload.percent : undefined
        };
        if (result.percent === undefined && typeof result.completed === 'number' && typeof result.total === 'number' && result.total > 0) {
            result.percent = (result.completed / result.total) * 100;
        }
        return result;
    };

    /**
     * Formats a byte count as a compact, human readable "132 MB" / "2.3 GB"
     * label used in the download progress details line.
     *
     * @param {number} bytes Byte count from a progress payload.
     * @returns {?string} Formatted size label or null for invalid input.
     */
    const formatDownloadSize = (bytes) => {
        if (typeof bytes !== 'number' || Number.isNaN(bytes) || bytes < 0) {
            return null;
        }
        const megabytes = bytes / (1024 * 1024);
        if (megabytes >= 1024) {
            const gigabytes = megabytes / 1024;
            return `${gigabytes >= 10 ? gigabytes.toFixed(1) : gigabytes.toFixed(2)} GB`;
        }
        const precision = megabytes >= 100 ? 0 : megabytes >= 10 ? 1 : 2;
        return `${megabytes.toFixed(precision)} MB`;
    };

    const extractResponseErrorMessage = async (response, fallback) => {
        try {
            const body = await response.json();
            if (typeof body?.detail === 'string' && body.detail.trim()) {
                return body.detail.trim();
            }
            if (typeof body?.detail?.message === 'string' && body.detail.message.trim()) {
                return body.detail.message.trim();
            }
            if (typeof body?.message === 'string' && body.message.trim()) {
                return body.message.trim();
            }
            if (typeof body?.error === 'string' && body.error.trim()) {
                return body.error.trim();
            }
        } catch (parseError) {
            // ignore parse failures
        }
        return fallback;
    };

    const downloadOllamaModel = async (model) => {
        const providerId = state.editingId;
        if (!providerId) {
            setOllamaDownloadStatus(t('provider_ollama_download_provider_unavailable', 'Provider is not loaded. Please reopen the editor.'), 'error');
            return;
        }

        const elements = getOllamaDownloadElements();
        if (!elements?.input) {
            setOllamaDownloadStatus(t('provider_ollama_download_reload_required', 'Unable to start download. Please reload the page.'), 'error');
            return;
        }

        state.downloadingModel = true;
        elements.input.disabled = true;
        if (elements.actionButton) {
            elements.actionButton.disabled = true;
        }
        // Clear any previous status message: while the download runs, the
        // progress panel is the single source of truth for the live state.
        setOllamaDownloadStatus('', 'info');
        resetOllamaDownloadProgress();
        if (elements.progressWrapper) {
            elements.progressWrapper.hidden = false;
        }
        if (elements.progressTitle) {
            elements.progressTitle.textContent = formatT('provider_ollama_download_in_progress_named', 'Downloading "{model}"...', { model });
        }

        try {
            const response = await authedFetch('/api/v1/llm/ollama/model/download', {
                method: 'POST',
                body: JSON.stringify({
                    ollama_provider_id: providerId,
                    model
                })
            });
            if (!response.ok || !response.body) {
                resetOllamaDownloadProgress();
                notifyError(t('provider_ollama_download_start_failed', 'Download failed to start.'));
                return;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) {
                    break;
                }
                buffer += decoder.decode(value, { stream: true });

                let newlineIndex;
                while ((newlineIndex = buffer.indexOf('\n')) >= 0) {
                    const line = buffer.slice(0, newlineIndex).trim();
                    buffer = buffer.slice(newlineIndex + 1);
                    if (!line) {
                        continue;
                    }
                    try {
                        const payload = JSON.parse(line);
                        const mapped = mapProgressPayload(payload);
                        updateOllamaDownloadProgress(mapped);
                    } catch (parseError) {
                        console.warn('Failed to parse download progress line', parseError, line);
                    }
                }
            }

            if (buffer.trim()) {
                try {
                    const payload = JSON.parse(buffer.trim());
                    const mapped = mapProgressPayload(payload);
                    updateOllamaDownloadProgress(mapped);
                } catch (parseError) {
                    console.warn('Failed to parse terminal download progress line', parseError, buffer);
                }
            }

            resetOllamaDownloadProgress();
            await loadOllamaLoadedModels();
            // The loaded-models refresh rebuilds the section, so the success
            // message is set afterwards to not get wiped by the re-render.
            setOllamaDownloadStatus(formatT('provider_ollama_download_completed_named', 'Download of "{model}" completed.', { model }), 'success');
        } catch (error) {
            console.error('Failed to download Ollama model', error);
            resetOllamaDownloadProgress();
            setOllamaDownloadStatus(error?.message || t('provider_ollama_download_failed_retry', 'Failed to download model. Please try again.'), 'error');
        } finally {
            state.downloadingModel = false;
            if (elements?.input) {
                elements.input.disabled = false;
                elements.input.value = '';
            }
            if (elements?.actionButton) {
                elements.actionButton.disabled = false;
            }
        }
    };

    /**
     * Runs a load/unload/delete action for the selected model. Triggered by
     * the action buttons next to each model select row.
     *
     * @param {string} actionKey One of "load", "unload", "delete".
     * @param {string} model Model name selected in the row's select.
     */
    const performOllamaAction = async (actionKey, model) => {
        const providerId = state.editingId;
        if (!providerId) {
            setOllamaActionStatus(actionKey, t('provider_ollama_action_provider_unavailable', 'Provider is not loaded. Please reopen the editor.'), 'error');
            return;
        }

        const elements = getOllamaActionElements(actionKey);
        if (!elements?.input) {
            setOllamaActionStatus(actionKey, t('provider_ollama_action_reload_required', 'Unable to perform action. Please reload the page.'), 'error');
            return;
        }

        if (state.ollamaActionState[actionKey]?.running) {
            setOllamaActionStatus(actionKey, t('provider_ollama_action_in_progress', 'Action already in progress. Please wait...'), 'info');
            return;
        }

        state.ollamaActionState[actionKey] = { running: true };
        setModelControlDisabled(elements.input, true);
        if (elements.actionButton) {
            elements.actionButton.disabled = true;
        }
        const progressKeyMap = {
            delete: 'provider_ollama_action_delete_progress',
            load: 'provider_ollama_action_load_progress',
            unload: 'provider_ollama_action_unload_progress'
        };
        const progressFallbackMap = {
            delete: 'Deleting "{model}"...',
            load: 'Loading "{model}"...',
            unload: 'Unloading "{model}"...'
        };
        setOllamaActionStatus(actionKey, formatT(progressKeyMap[actionKey], progressFallbackMap[actionKey], { model }), 'info');

        const urlMap = {
            delete: '/api/v1/llm/ollama/model',
            load: '/api/v1/llm/ollama/model/load',
            unload: '/api/v1/llm/ollama/model/unload'
        };
        const methodMap = {
            delete: 'DELETE',
            load: 'POST',
            unload: 'POST'
        };
        const url = urlMap[actionKey];
        if (!url) {
            state.ollamaActionState[actionKey] = { running: false };
            elements.input.disabled = false;
            setOllamaActionStatus(actionKey, t('provider_ollama_action_unsupported', 'Unsupported action.'), 'error');
            return;
        }

        try {
            const response = await authedFetch(url, {
                method: methodMap[actionKey],
                body: JSON.stringify({
                    ollama_provider_id: providerId,
                    model
                })
            });
            if (!response.ok) {
                let message = t('provider_ollama_action_failed_retry', 'Action failed. Please try again.');
                try {
                    const body = await response.json();
                    if (body?.detail) {
                        message = body.detail;
                    } else if (body?.message) {
                        message = body.message;
                    }
                } catch (parseError) {
                    // ignore parse failures
                }
                notifyError(message);
                return;
            }

            const successKeyMap = {
                delete: 'provider_ollama_action_delete_success',
                load: 'provider_ollama_action_load_success',
                unload: 'provider_ollama_action_unload_success'
            };
            const successFallbackMap = {
                delete: 'Deleted "{model}" successfully.',
                load: 'Loaded "{model}" successfully.',
                unload: 'Unloaded "{model}" successfully.'
            };
            clearModelControlValue(elements.input);
            await loadOllamaLoadedModels();
            // The loaded-models refresh rebuilds the section, so the success
            // message is set afterwards to not get wiped by the re-render.
            setOllamaActionStatus(actionKey, formatT(successKeyMap[actionKey], successFallbackMap[actionKey], { model }), 'success');
        } catch (error) {
            console.error(`Failed to ${actionKey} Ollama model`, error);
            setOllamaActionStatus(actionKey, error?.message || t('provider_ollama_action_failed_retry', 'Action failed. Please try again.'), 'error');
        } finally {
            state.ollamaActionState[actionKey] = { running: false };
            setModelControlDisabled(elements.input, false);
            if (elements.actionButton) {
                elements.actionButton.disabled = false;
            }
        }
    };

    const formatGigabytes = (bytes) => {
        if (typeof bytes !== 'number' || Number.isNaN(bytes) || bytes <= 0) {
            return '—';
        }
        const gigabytes = bytes / (1024 ** 3);
        const precision = gigabytes >= 10 ? 1 : 2;
        return `${gigabytes.toFixed(precision)} GB`;
    };

    const formatTimeUntil = (isoString) => {
        if (!isoString) {
            return '—';
        }
        const target = new Date(isoString);
        if (Number.isNaN(target.getTime())) {
            return '—';
        }
        const diffMs = target.getTime() - Date.now();
        if (diffMs <= 0) {
            return t('provider_ollama_time_expired', 'expired');
        }
        const diffSeconds = Math.floor(diffMs / 1000);
        if (diffSeconds < 60) {
            return t('provider_ollama_time_less_than_minute', 'in <1 minute');
        }
        const diffMinutes = Math.floor(diffSeconds / 60);
        if (diffMinutes < 60) {
            return diffMinutes === 1
                ? formatT('provider_ollama_time_in_minute', 'in {count} minute', { count: diffMinutes })
                : formatT('provider_ollama_time_in_minutes', 'in {count} minutes', { count: diffMinutes });
        }
        const diffHours = Math.floor(diffMinutes / 60);
        if (diffHours < 24) {
            return diffHours === 1
                ? formatT('provider_ollama_time_in_hour', 'in {count} hour', { count: diffHours })
                : formatT('provider_ollama_time_in_hours', 'in {count} hours', { count: diffHours });
        }
        const diffDays = Math.floor(diffHours / 24);
        if (diffDays < 7) {
            return diffDays === 1
                ? formatT('provider_ollama_time_in_day', 'in {count} day', { count: diffDays })
                : formatT('provider_ollama_time_in_days', 'in {count} days', { count: diffDays });
        }
        return target.toLocaleString();
    };

    const renderOllamaLoadedModelsTable = (models) => {
        const section = ensureOllamaLoadedModelsSection();
        if (!section) {
            return;
        }

        let tableSection = section.querySelector('.ollama-models-table-section');
        if (tableSection) {
            tableSection.remove();
        }

        tableSection = document.createElement('section');
        tableSection.className = 'ollama-models-section ollama-models-table-section';

        const header = document.createElement('div');
        header.className = 'ollama-models-header';

        const title = document.createElement('h3');
        title.className = 'ollama-models-title';
        title.textContent = t('provider_ollama_loaded_models_title', 'Loaded Models');

        const desc = document.createElement('p');
        desc.className = 'ollama-models-description';
        desc.textContent = t('provider_ollama_loaded_models_desc', 'Models currently loaded in memory and available for inference.');

        header.appendChild(title);
        header.appendChild(desc);
        tableSection.appendChild(header);

        const body = document.createElement('div');
        body.className = 'ollama-models-body';

        const wrapper = document.createElement('div');
        wrapper.className = 'ollama-models-table-wrapper';

        const table = document.createElement('table');
        table.className = 'ollama-models-table';

        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        const headerLabels = [
            t('provider_ollama_table_header_model', 'Model'),
            t('provider_ollama_table_header_size', 'Size'),
            t('provider_ollama_table_header_parameters', 'Parameters'),
            t('provider_ollama_table_header_quantization', 'Quantization'),
            t('provider_ollama_table_header_context', 'Context'),
            t('provider_ollama_table_header_expires', 'Expires')
        ];
        headerLabels.forEach((label) => {
            const th = document.createElement('th');
            th.textContent = label;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        models.forEach((model) => {
            const row = document.createElement('tr');

            const modelName = model?.name || model?.model || '—';
            const sizeBytes = typeof model?.size === 'number' ? model.size : (typeof model?.size_vram === 'number' ? model.size_vram : null);
            const parameterSize = model?.details?.parameter_size || '—';
            const quantization = model?.details?.quantization_level || '—';
            const contextLength = typeof model?.context_length === 'number' ? model.context_length.toLocaleString() : '—';
            const expires = formatTimeUntil(model?.expires_at);

            [
                modelName,
                formatGigabytes(sizeBytes),
                parameterSize,
                quantization,
                contextLength,
                expires
            ].forEach((value, index) => {
                const td = document.createElement('td');
                // The responsive card layout uses this stable translated label
                // when the visual table header is hidden on narrow screens.
                td.dataset.label = headerLabels[index];
                td.textContent = value;
                row.appendChild(td);
            });

            tbody.appendChild(row);
        });

        table.appendChild(tbody);
        wrapper.appendChild(table);
        body.appendChild(wrapper);
        tableSection.appendChild(body);
        section.appendChild(tableSection);
    };

    const loadOllamaLoadedModels = async () => {
        const providerId = state.editingId;
        if (!providerId) {
            return;
        }

        const modelsRefresh = beginModelsTabRefresh(providerId);
        try {
            removeOllamaLoadedModelsSection({ showPlaceholder: false });
            dom.forms.edit?.form?.classList.remove(OLLAMA_UNSUPPORTED_FORM_CLASS);

            if (state.ollamaUnsupported) {
                setModelsPlaceholder(state.ollamaUnsupportedMessage);
                updateModelsTabVisibility();
                return;
            }

            let data;
            let isUnsupported = false;
            let hadError = false;
            let errorMessage = t('provider_ollama_loaded_models_failed', 'Unable to load loaded models. Please try again later.');

            try {
                const [loadedResult, allModelsResult] = await Promise.allSettled([
                    authedFetch(`/api/v1/llm/ollama/models/loaded?ollama_provider_id=${encodeURIComponent(providerId)}`),
                    // Downloaded models only feed the load/delete selects, so
                    // this secondary request must not take down the loaded
                    // models view when it fails independently.
                    authedFetch(`/api/v1/llm/ollama/models/all?ollama_provider_id=${encodeURIComponent(providerId)}`)
                ]);
                if (loadedResult.status === 'rejected') {
                    throw loadedResult.reason;
                }
                const response = loadedResult.value;
                if (response.status === 422) {
                    let message = 'Ollama cloud does not support this feature.';
                    try {
                        const body = await response.json();
                        if (typeof body?.detail === 'string') {
                            message = body.detail;
                        }
                    } catch (parseError) {
                        // ignore parse failure
                    }
                    state.ollamaUnsupported = true;
                    state.ollamaUnsupportedMessage = message;
                    dom.forms.edit?.form?.classList.add(OLLAMA_UNSUPPORTED_FORM_CLASS);
                    setModelsPlaceholder(message);
                    updateModelsTabVisibility();
                    isUnsupported = true;
                    return;
                }
                if (!response.ok) {
                    updateModelsTabVisibility();
                    return;
                }
                data = await response.json();
                if (providerId !== state.editingId) {
                    return;
                }
                state.ollamaModels = Array.isArray(data) ? data : [];
                // A failed model list must not break the loaded-models view;
                // the load/delete selects simply degrade to an empty list.
                state.ollamaAllModels = [];
                if (allModelsResult.status === 'fulfilled' && allModelsResult.value.ok) {
                    try {
                        const allData = await allModelsResult.value.json();
                        if (providerId !== state.editingId) {
                            return;
                        }
                        state.ollamaAllModels = Array.isArray(allData) ? allData : [];
                    } catch (error) {
                        // Malformed optional inventory data has the same safe
                        // fallback as an unavailable inventory endpoint.
                        console.warn('Failed to load Ollama downloaded model inventory', error);
                    }
                } else if (allModelsResult.status === 'rejected') {
                    console.warn('Failed to load Ollama downloaded model inventory', allModelsResult.reason);
                }
            } catch (error) {
                if (isUnsupported) {
                    return;
                }
                hadError = true;
                console.error('Failed to load Ollama loaded models', error);
                if (error?.message) {
                    errorMessage = error.message;
                }
            }

            if (isUnsupported) {
                return;
            }

            const section = ensureOllamaLoadedModelsSection();
            if (!section) {
                updateModelsTabVisibility();
                return;
            }

            const downloadControls = ensureOllamaDownloadControls();
            const versionBlock = ensureOllamaVersionBlock();

            if (hadError) {
                setOllamaLoadedModelsMessage(errorMessage, 'error');
                return;
            }

            section.hidden = false;
            downloadControls?.removeAttribute('hidden');
            versionBlock?.removeAttribute('hidden');

            if (!state.ollamaModels.length) {
                const existingTable = section.querySelector('.ollama-models-table-section');
                if (existingTable) {
                    existingTable.remove();
                }
            } else {
                renderOllamaLoadedModelsTable(state.ollamaModels);
            }

            state.ollamaUnsupported = false;
            state.ollamaUnsupportedMessage = null;
            dom.forms.edit?.form?.classList.remove(OLLAMA_UNSUPPORTED_FORM_CLASS);
            await fetchOllamaVersion();
        } finally {
            // Every exit path must restore the tab state captured above.
            completeModelsTabRefresh(modelsRefresh);
        }
    };

    const createLMStudioInput = ({ id, placeholder = '', type = 'text', options = null }) => {
        const input = document.createElement(type === 'select' ? 'select' : 'input');
        input.id = id;
        input.className = 'ollama-models-input';
        input.autocomplete = 'off';
        if (type === 'select') {
            (options || []).forEach((option) => {
                const optionEl = document.createElement('option');
                optionEl.value = option.value;
                optionEl.textContent = option.label;
                input.appendChild(optionEl);
            });
        } else {
            input.type = type;
            if (placeholder) {
                input.placeholder = placeholder;
            }
        }
        return input;
    };

    const createLMStudioRow = ({ title, description, inputId }) => {
        const row = document.createElement('div');
        row.className = 'ollama-models-row';

        const left = document.createElement('div');
        left.className = 'ollama-models-row-left';

        const titleEl = document.createElement('label');
        titleEl.className = 'ollama-models-row-title';
        titleEl.htmlFor = inputId;
        titleEl.textContent = title;
        left.appendChild(titleEl);

        const descEl = document.createElement('p');
        descEl.className = 'ollama-models-row-desc';
        descEl.textContent = description;
        left.appendChild(descEl);

        const right = document.createElement('div');
        right.className = 'ollama-models-row-right';

        const inputWrapper = document.createElement('div');
        inputWrapper.className = 'ollama-models-input-wrapper';
        right.appendChild(inputWrapper);

        row.appendChild(left);
        row.appendChild(right);

        // Status messages span the full row width below the controls.
        const statusEl = document.createElement('p');
        statusEl.className = 'ollama-models-row-status';
        statusEl.setAttribute('role', 'status');
        statusEl.hidden = true;
        row.appendChild(statusEl);

        return { row, inputWrapper, statusEl, right };
    };

    const setLMStudioStatus = (key, message, tone = 'info') => {
        const statusEl = state.lmstudioControls?.[key]?.statusEl;
        if (!statusEl) {
            return;
        }
        statusEl.hidden = !message;
        statusEl.textContent = message || '';
        statusEl.dataset.tone = tone;
    };

    const setLMStudioControlsDisabled = (key, disabled) => {
        const controls = state.lmstudioControls?.[key];
        if (!controls) {
            return;
        }
        Object.values(controls).forEach((control) => {
            // Enhanced selects keep their own trigger button, which must be
            // disabled alongside the hidden native select.
            if (control instanceof HTMLElement && 'disabled' in control) {
                setModelControlDisabled(control, disabled);
            }
        });
    };

    const resetLMStudioDownloadProgress = () => {
        const controls = state.lmstudioControls?.download;
        resetModelDownloadProgress({
            wrapper: controls?.progressWrapper,
            fill: controls?.progressFill,
            bar: controls?.progressBar,
            title: controls?.progressTitle,
            percent: controls?.progressPercent,
            text: controls?.progressText
        });
    };

    const updateLMStudioDownloadProgress = (progress) => {
        const controls = state.lmstudioControls?.download;
        applyModelDownloadProgress({
            wrapper: controls?.progressWrapper,
            fill: controls?.progressFill,
            bar: controls?.progressBar,
            title: controls?.progressTitle,
            percent: controls?.progressPercent,
            text: controls?.progressText
        }, progress, t('provider_lmstudio_download_progress_default', 'Downloading...'));
    };

    /**
     * Validates the LM Studio download input and starts the model download.
     * Shared between the Enter key handler and the download trigger button.
     */
    const requestLMStudioDownload = async () => {
        const model = state.lmstudioControls?.download?.input?.value?.trim();
        if (!model) {
            setLMStudioStatus('download', t('provider_lmstudio_action_required_name', 'Please provide a model name.'), 'error');
            return;
        }
        await downloadLMStudioModel(model);
    };

    const parseOptionalInteger = (value) => {
        const text = String(value || '').trim();
        if (!text) {
            return null;
        }
        const parsed = parseInt(text, 10);
        return Number.isNaN(parsed) ? null : parsed;
    };

    const parseOptionalBoolean = (value) => {
        const text = String(value || '').trim().toLowerCase();
        if (!text) {
            return null;
        }
        if (text === 'true') {
            return true;
        }
        if (text === 'false') {
            return false;
        }
        return null;
    };

    /**
     * Builds select options for the models installed on the LM Studio server.
     *
     * @returns {Array<{ value: string, label: string }>} Sorted model options.
     */
    const getLMStudioInstalledModelOptions = () => {
        if (!Array.isArray(state.lmstudioAllModels)) {
            return [];
        }
        const seen = new Set();
        return state.lmstudioAllModels
            .map((model) => ({
                value: String(model?.key || model?.name || '').trim(),
                label: String(model?.name || model?.key || '').trim()
            }))
            .filter(({ value }) => {
                if (!value || seen.has(value)) {
                    return false;
                }
                seen.add(value);
                return true;
            })
            .sort((a, b) => a.label.localeCompare(b.label));
    };

    /**
     * Builds select options for loaded LM Studio instances and model-wide
     * unload operations. Instance options are labeled "model (instance-id)"
     * while one additional option per model preserves the backend's ability
     * to unload every matching instance at once.
     *
     * @returns {Array<{ value: string, label: string }>} Sorted instance options.
     */
    const getLMStudioLoadedInstanceOptions = () => {
        if (!Array.isArray(state.lmstudioLoadedModels)) {
            return [];
        }
        const options = [];
        const modelLabels = new Map();
        state.lmstudioLoadedModels.forEach((instance) => {
            const instanceId = String(instance?.instance_id || '').trim();
            const modelKey = String(instance?.model || '').trim();
            const displayName = String(instance?.name || modelKey).trim();
            if (modelKey && !modelLabels.has(modelKey)) {
                modelLabels.set(modelKey, displayName || modelKey);
            }
            if (instanceId) {
                options.push({
                    value: instanceId,
                    label: displayName ? `${displayName} (${instanceId})` : instanceId,
                });
            }
        });

        modelLabels.forEach((displayName, modelKey) => {
            options.push({
                value: modelKey,
                label: formatT(
                    'provider_lmstudio_unload_all_instances_option',
                    'All instances of {model}',
                    { model: displayName || modelKey }
                ),
            });
        });

        const seen = new Set();
        return options
            .filter(({ value }) => {
                if (!value || seen.has(value)) {
                    return false;
                }
                seen.add(value);
                return true;
            })
            .sort((a, b) => a.label.localeCompare(b.label));
    };

    /**
     * Validates the selected model/instance of a load or unload select and
     * runs the action. Shared by the row action buttons.
     *
     * @param {string} actionKey Either "load" or "unload".
     */
    const requestLMStudioAction = async (actionKey) => {
        const controls = state.lmstudioControls?.[actionKey];
        const select = actionKey === 'load' ? controls?.modelInput : controls?.input;
        const model = String(select?.value || '').trim();
        if (!model) {
            setLMStudioStatus(actionKey, t('provider_lmstudio_action_required_selection', 'Please select a model.'), 'error');
            return;
        }
        await performLMStudioAction(actionKey);
    };

    const ensureLMStudioControls = () => {
        const section = ensureOllamaLoadedModelsSection();
        if (!section) {
            return null;
        }

        let managementSection = section.querySelector('.lmstudio-models-management-section');
        if (!managementSection) {
            managementSection = document.createElement('section');
            managementSection.className = 'ollama-models-section lmstudio-models-management-section';

            const header = document.createElement('div');
            header.className = 'ollama-models-header';

            const title = document.createElement('h3');
            title.className = 'ollama-models-title';
            title.textContent = t('provider_lmstudio_model_management_title', 'Model Management');

            const desc = document.createElement('p');
            desc.className = 'ollama-models-description';
            desc.textContent = t(
                'provider_lmstudio_model_management_desc',
                'Browse installed models, download new ones, and manage loaded model instances for this LM Studio server.'
            );

            header.appendChild(title);
            header.appendChild(desc);
            managementSection.appendChild(header);

            const body = document.createElement('div');
            body.className = 'ollama-models-body';

            const lmSelectPlaceholder = t('provider_lmstudio_model_select_placeholder', 'Select a model...');

            const downloadRow = createLMStudioRow({
                title: t('provider_lmstudio_download_title', 'Download model'),
                description: t('provider_lmstudio_download_desc', 'Download a model into LM Studio, with optional quantization.'),
                inputId: 'lmstudio-download-model',
            });
            const downloadGrid = document.createElement('div');
            // Stack the model and quantization fields vertically so the
            // optional quantization input does not crowd the model name.
            downloadGrid.className = 'ollama-models-input-stack';
            const downloadInput = createLMStudioInput({
                id: 'lmstudio-download-model',
                placeholder: t('provider_lmstudio_download_placeholder', 'e.g. llama-3.3-70b-instruct'),
            });
            const quantizationInput = createLMStudioInput({
                id: 'lmstudio-download-quantization',
                placeholder: t('provider_lmstudio_download_quantization_placeholder', 'Optional quantization, e.g. Q4_K_M'),
            });
            const syncQuantizationAvailability = () => {
                // Native v1 accepts an explicit quantization only when the
                // model is an exact Hugging Face URL.
                const modelValue = String(downloadInput.value || '').trim();
                const supported = /^https:\/\/huggingface\.co\//i.test(modelValue);
                quantizationInput.disabled = !supported;
                if (!supported) {
                    quantizationInput.value = '';
                }
            };
            downloadInput.dataset.actionKey = 'download';
            quantizationInput.dataset.actionKey = 'download';
            downloadInput.addEventListener('input', syncQuantizationAvailability);
            syncQuantizationAvailability();
            downloadGrid.append(downloadInput, quantizationInput);
            downloadRow.inputWrapper.appendChild(downloadGrid);
            // The progress panel spans the full row width below the controls,
            // above the inline status line.
            const downloadProgress = createModelDownloadProgress({ titleId: 'provider-lmstudio-download-progress-title' });
            downloadRow.row.insertBefore(downloadProgress.wrapper, downloadRow.statusEl);
            // Dedicated download trigger button next to the inputs; pressing
            // Enter inside any download field starts the same flow.
            const downloadButton = createRowActionButton({
                label: t('provider_lmstudio_download_title', 'Download model'),
                iconSvg: window.Icons?.download,
                onClick: () => requestLMStudioDownload()
            });
            downloadRow.right.appendChild(downloadButton);
            body.appendChild(downloadRow.row);

            const loadRow = createLMStudioRow({
                title: t('provider_lmstudio_load_title', 'Load model'),
                description: t('provider_lmstudio_load_desc', 'Load a model with native LM Studio runtime options. Press Enter in any field to start loading.'),
                inputId: 'lmstudio-load-model',
            });
            const loadStack = document.createElement('div');
            loadStack.className = 'ollama-models-input-stack';
            // The model key is picked from a searchable list of the models
            // installed on this LM Studio server instead of free-text entry.
            const loadModelSelect = createLMStudioInput({
                id: 'lmstudio-load-model',
                type: 'select',
            });
            loadModelSelect.dataset.actionKey = 'load';
            loadModelSelect.setAttribute('aria-label', t('provider_lmstudio_load_title', 'Load model'));
            setModelSelectOptions(loadModelSelect, getLMStudioInstalledModelOptions(), lmSelectPlaceholder);
            loadStack.append(loadModelSelect);
            enhanceModelActionSelect(loadModelSelect, { placeholder: lmSelectPlaceholder });

            const loadOptionsGrid = document.createElement('div');
            loadOptionsGrid.className = 'ollama-models-input-grid';
            const loadOptionDefs = [
                ['contextLengthInput', createLMStudioInput({ id: 'lmstudio-load-context-length', type: 'number', placeholder: t('provider_lmstudio_context_length_placeholder', 'Context length') })],
                ['evalBatchSizeInput', createLMStudioInput({ id: 'lmstudio-load-eval-batch-size', type: 'number', placeholder: t('provider_lmstudio_eval_batch_size_placeholder', 'Eval batch size') })],
                ['flashAttentionInput', createLMStudioInput({
                    id: 'lmstudio-load-flash-attention',
                    type: 'select',
                    options: [
                        { value: '', label: t('common_optional', 'Optional') },
                        { value: 'true', label: t('common_true', 'True') },
                        { value: 'false', label: t('common_false', 'False') },
                    ],
                })],
                ['numExpertsInput', createLMStudioInput({ id: 'lmstudio-load-num-experts', type: 'number', placeholder: t('provider_lmstudio_num_experts_placeholder', 'Number of experts') })],
                ['offloadKvCacheInput', createLMStudioInput({
                    id: 'lmstudio-load-offload-kv-cache',
                    type: 'select',
                    options: [
                        { value: '', label: t('common_optional', 'Optional') },
                        { value: 'true', label: t('common_true', 'True') },
                        { value: 'false', label: t('common_false', 'False') },
                    ],
                })],
            ];
            loadOptionDefs.forEach(([, input]) => {
                input.dataset.actionKey = 'load';
                loadOptionsGrid.appendChild(input);
            });
            const loadNote = document.createElement('div');
            loadNote.className = 'ollama-models-table-note';
            loadNote.textContent = t(
                'provider_lmstudio_load_note',
                'Leave optional fields empty to use LM Studio defaults.'
            );
            loadStack.append(loadOptionsGrid, loadNote);
            loadRow.inputWrapper.appendChild(loadStack);
            const loadButton = createRowActionButton({
                label: t('provider_lmstudio_load_title', 'Load model'),
                iconSvg: window.Icons?.play,
                onClick: () => requestLMStudioAction('load')
            });
            loadRow.right.appendChild(loadButton);
            body.appendChild(loadRow.row);

            const unloadRow = createLMStudioRow({
                title: t('provider_lmstudio_unload_title', 'Unload model'),
                description: t('provider_lmstudio_unload_desc', 'Unload a loaded instance by instance ID, or by model key to unload all matching instances.'),
                inputId: 'lmstudio-unload-model',
            });
            // Unload picks a loaded instance from a searchable list instead of
            // typing an instance ID or model key by hand.
            const unloadSelect = createLMStudioInput({
                id: 'lmstudio-unload-model',
                type: 'select',
            });
            unloadSelect.dataset.actionKey = 'unload';
            unloadSelect.setAttribute('aria-label', t('provider_lmstudio_unload_title', 'Unload model'));
            setModelSelectOptions(unloadSelect, getLMStudioLoadedInstanceOptions(), lmSelectPlaceholder);
            unloadRow.inputWrapper.appendChild(unloadSelect);
            enhanceModelActionSelect(unloadSelect, { placeholder: lmSelectPlaceholder });
            const unloadButton = createRowActionButton({
                label: t('provider_lmstudio_unload_title', 'Unload model'),
                iconSvg: window.Icons?.stop,
                onClick: () => requestLMStudioAction('unload')
            });
            unloadRow.right.appendChild(unloadButton);
            body.appendChild(unloadRow.row);

            managementSection.appendChild(body);
            section.appendChild(managementSection);

            state.lmstudioControls = {
                download: {
                    input: downloadInput,
                    quantizationInput,
                    progressWrapper: downloadProgress.wrapper,
                    progressTitle: downloadProgress.title,
                    progressPercent: downloadProgress.percent,
                    progressBar: downloadProgress.bar,
                    progressFill: downloadProgress.fill,
                    progressText: downloadProgress.text,
                    actionButton: downloadButton,
                    syncQuantizationAvailability,
                    statusEl: downloadRow.statusEl,
                },
                load: {
                    modelInput: loadModelSelect,
                    contextLengthInput: loadOptionDefs[0][1],
                    evalBatchSizeInput: loadOptionDefs[1][1],
                    flashAttentionInput: loadOptionDefs[2][1],
                    numExpertsInput: loadOptionDefs[3][1],
                    offloadKvCacheInput: loadOptionDefs[4][1],
                    actionButton: loadButton,
                    statusEl: loadRow.statusEl,
                },
                unload: {
                    input: unloadSelect,
                    actionButton: unloadButton,
                    statusEl: unloadRow.statusEl,
                },
            };

            [downloadInput, quantizationInput].forEach((input) => {
                input.addEventListener('keydown', async (event) => {
                    if (event.key !== 'Enter') {
                        return;
                    }
                    event.preventDefault();
                    await requestLMStudioDownload();
                });
            });

            Object.values(state.lmstudioControls.load).forEach((control) => {
                if (!(control instanceof HTMLElement) || control === state.lmstudioControls.load.statusEl || control.tagName === 'SELECT') {
                    return;
                }
                control.addEventListener('keydown', async (event) => {
                    if (event.key !== 'Enter') {
                        return;
                    }
                    event.preventDefault();
                    await performLMStudioAction('load');
                });
            });

            unloadSelect.addEventListener('keydown', async (event) => {
                if (event.key !== 'Enter') {
                    return;
                }
                event.preventDefault();
                await requestLMStudioAction('unload');
            });
        }

        return managementSection;
    };

    const renderLMStudioTableSection = ({
        sectionClass,
        title,
        description,
        headers,
        rows,
        emptyMessage,
    }) => {
        const section = ensureOllamaLoadedModelsSection();
        if (!section) {
            return;
        }

        const existing = section.querySelector(`.${sectionClass}`);
        if (existing) {
            existing.remove();
        }

        const tableSection = document.createElement('section');
        tableSection.className = `ollama-models-section ${sectionClass}`;

        const header = document.createElement('div');
        header.className = 'ollama-models-header';
        const titleEl = document.createElement('h3');
        titleEl.className = 'ollama-models-title';
        titleEl.textContent = title;
        const descEl = document.createElement('p');
        descEl.className = 'ollama-models-description';
        descEl.textContent = description;
        header.append(titleEl, descEl);
        tableSection.appendChild(header);

        const body = document.createElement('div');
        body.className = 'ollama-models-body';

        if (!rows.length) {
            const empty = document.createElement('p');
            empty.className = 'ollama-models-empty';
            empty.textContent = emptyMessage;
            body.appendChild(empty);
        } else {
            const wrapper = document.createElement('div');
            wrapper.className = 'ollama-models-table-wrapper';
            const table = document.createElement('table');
            table.className = 'ollama-models-table';
            const thead = document.createElement('thead');
            const headerRow = document.createElement('tr');
            headers.forEach((label) => {
                const th = document.createElement('th');
                th.textContent = label;
                headerRow.appendChild(th);
            });
            thead.appendChild(headerRow);
            table.appendChild(thead);

            const tbody = document.createElement('tbody');
            rows.forEach((values) => {
                const row = document.createElement('tr');
                values.forEach((value, index) => {
                    const td = document.createElement('td');
                    // Retain each column's meaning when mobile CSS transforms
                    // the semantic table row into a compact labelled card.
                    td.dataset.label = headers[index] || '';
                    td.textContent = value;
                    row.appendChild(td);
                });
                tbody.appendChild(row);
            });
            table.appendChild(tbody);
            wrapper.appendChild(table);
            body.appendChild(wrapper);
        }

        tableSection.appendChild(body);
        section.appendChild(tableSection);
    };

    const formatLMStudioQuantization = (value) => {
        // Native v1 returns an object ({ name, bits_per_weight }); older
        // versions returned a string. Support both shapes defensively.
        if (value && typeof value === 'object') {
            return value.name || '—';
        }
        return value || '—';
    };

    const renderLMStudioInstalledModelsTable = (models) => {
        renderLMStudioTableSection({
            sectionClass: 'lmstudio-models-installed-section',
            title: t('provider_lmstudio_installed_models_title', 'Installed Models'),
            description: t('provider_lmstudio_installed_models_desc', 'All models currently known to the LM Studio server.'),
            headers: [
                t('provider_lmstudio_table_header_model', 'Model'),
                t('provider_lmstudio_table_header_publisher', 'Publisher'),
                t('provider_lmstudio_table_header_architecture', 'Architecture'),
                t('provider_lmstudio_table_header_quantization', 'Quantization'),
                t('provider_lmstudio_table_header_context', 'Context'),
                t('provider_lmstudio_table_header_loaded', 'Loaded'),
            ],
            rows: (models || []).map((model) => [
                model?.name || model?.key || '—',
                model?.publisher || '—',
                model?.architecture || '—',
                formatLMStudioQuantization(model?.quantization),
                typeof model?.max_context_length === 'number' ? model.max_context_length.toLocaleString() : '—',
                String(Array.isArray(model?.loaded_instances) ? model.loaded_instances.length : 0),
            ]),
            emptyMessage: t('provider_lmstudio_installed_models_empty', 'No LM Studio models were returned by the server.'),
        });
    };

    const renderLMStudioLoadedInstancesTable = (models) => {
        renderLMStudioTableSection({
            sectionClass: 'lmstudio-models-loaded-section',
            title: t('provider_lmstudio_loaded_models_title', 'Loaded Instances'),
            description: t('provider_lmstudio_loaded_models_desc', 'Model instances currently loaded in memory and ready for inference.'),
            headers: [
                t('provider_lmstudio_loaded_header_instance', 'Instance'),
                t('provider_lmstudio_loaded_header_model', 'Model'),
                t('provider_lmstudio_loaded_header_size', 'Size'),
                t('provider_lmstudio_loaded_header_quantization', 'Quantization'),
                t('provider_lmstudio_loaded_header_context', 'Context'),
                t('provider_lmstudio_loaded_header_parallel', 'Parallel'),
            ],
            rows: (models || []).map((model) => [
                model?.instance_id || '—',
                model?.model || model?.name || '—',
                formatGigabytes(model?.size_bytes),
                formatLMStudioQuantization(model?.quantization),
                typeof model?.context_length === 'number'
                    ? model.context_length.toLocaleString()
                    : (typeof model?.max_context_length === 'number' ? model.max_context_length.toLocaleString() : '—'),
                typeof model?.parallel === 'number' ? model.parallel.toLocaleString() : '—',
            ]),
            emptyMessage: t('provider_lmstudio_loaded_models_empty', 'No LM Studio model instances are currently loaded.'),
        });
    };

    const downloadLMStudioModel = async (model) => {
        const providerId = state.editingId;
        if (!providerId) {
            setLMStudioStatus('download', t('provider_lmstudio_provider_unavailable', 'Provider is not loaded. Please reopen the editor.'), 'error');
            return;
        }
        if (state.lmstudioDownloadState.running) {
            setLMStudioStatus('download', t('provider_lmstudio_download_in_progress', 'A download is already in progress. Please wait...'), 'info');
            return;
        }

        const controls = state.lmstudioControls?.download;
        if (!controls?.input) {
            setLMStudioStatus('download', t('provider_lmstudio_reload_required', 'Unable to start the download. Please reload the page.'), 'error');
            return;
        }

        const quantization = String(controls.quantizationInput?.value || '').trim();
        const requestBody = {
            lmstudio_provider_id: providerId,
            model,
            quantization: quantization || null,
        };

        state.lmstudioDownloadState = { running: true };
        setLMStudioControlsDisabled('download', true);
        // Clear any previous status message: while the download runs, the
        // progress panel is the single source of truth for the live state.
        setLMStudioStatus('download', '', 'info');
        resetLMStudioDownloadProgress();
        if (controls.progressWrapper) {
            controls.progressWrapper.hidden = false;
        }
        if (controls.progressTitle) {
            controls.progressTitle.textContent = formatT('provider_lmstudio_download_started', 'Downloading "{model}"...', { model });
        }

        try {
            const response = await authedFetch('/api/v1/llm/lmstudio/model/download', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(requestBody),
            });
            if (!response.ok || !response.body) {
                resetLMStudioDownloadProgress();
                const message = await extractResponseErrorMessage(response, t('provider_lmstudio_download_failed_start', 'Download failed to start.'));
                setLMStudioStatus('download', message, 'error');
                return;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let finalPayload = null;

            while (true) {
                const { done, value } = await reader.read();
                if (done) {
                    break;
                }
                buffer += decoder.decode(value, { stream: true });
                let newlineIndex;
                while ((newlineIndex = buffer.indexOf('\n')) >= 0) {
                    const line = buffer.slice(0, newlineIndex).trim();
                    buffer = buffer.slice(newlineIndex + 1);
                    if (!line) {
                        continue;
                    }
                    try {
                        finalPayload = JSON.parse(line);
                        updateLMStudioDownloadProgress(mapProgressPayload(finalPayload));
                    } catch (parseError) {
                        console.warn('Failed to parse LM Studio download progress line', parseError, line);
                    }
                }
            }

            if (buffer.trim()) {
                try {
                    finalPayload = JSON.parse(buffer.trim());
                    updateLMStudioDownloadProgress(mapProgressPayload(finalPayload));
                } catch (parseError) {
                    console.warn('Failed to parse final LM Studio download progress line', parseError, buffer);
                }
            }

            const finalStatus = String(finalPayload?.status || '').trim().toLowerCase();
            if (finalStatus !== 'completed') {
                resetLMStudioDownloadProgress();
                const message = String(
                    finalPayload?.message
                    || finalPayload?.status
                    || t('provider_lmstudio_download_failed_retry', 'Failed to download the model. Please try again.')
                ).trim();
                setLMStudioStatus('download', message, 'error');
                return;
            }

            resetLMStudioDownloadProgress();
            await loadLMStudioModels();
            // The models refresh rebuilds the section, so the success message
            // is set afterwards to not get wiped by the re-render.
            setLMStudioStatus('download', formatT('provider_lmstudio_download_completed', 'Download of "{model}" completed.', { model }), 'success');
        } catch (error) {
            console.error('Failed to download LM Studio model', error);
            resetLMStudioDownloadProgress();
            setLMStudioStatus('download', error?.message || t('provider_lmstudio_download_failed_retry', 'Failed to download the model. Please try again.'), 'error');
        } finally {
            state.lmstudioDownloadState = { running: false };
            setLMStudioControlsDisabled('download', false);
            if (controls?.input) {
                controls.input.value = '';
            }
            if (controls?.quantizationInput) {
                controls.quantizationInput.value = '';
            }
            controls?.syncQuantizationAvailability?.();
        }
    };

    const performLMStudioAction = async (actionKey) => {
        const providerId = state.editingId;
        if (!providerId) {
            setLMStudioStatus(actionKey, t('provider_lmstudio_provider_unavailable', 'Provider is not loaded. Please reopen the editor.'), 'error');
            return;
        }

        const controls = state.lmstudioControls?.[actionKey];
        if (!controls) {
            setLMStudioStatus(actionKey, t('provider_lmstudio_reload_required', 'Unable to perform the action. Please reload the page.'), 'error');
            return;
        }
        if (state.lmstudioActionState[actionKey]?.running) {
            setLMStudioStatus(actionKey, t('provider_lmstudio_action_in_progress', 'Action already in progress. Please wait...'), 'info');
            return;
        }

        let url = '';
        let fetchOptions = null;
        let targetName = '';

        if (actionKey === 'load') {
            const model = String(controls.modelInput?.value || '').trim();
            if (!model) {
                setLMStudioStatus('load', t('provider_lmstudio_action_required_selection', 'Please select a model.'), 'error');
                return;
            }
            targetName = model;
            url = '/api/v1/llm/lmstudio/model/load';
            fetchOptions = {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    lmstudio_provider_id: providerId,
                    model,
                    context_length: parseOptionalInteger(controls.contextLengthInput?.value),
                    eval_batch_size: parseOptionalInteger(controls.evalBatchSizeInput?.value),
                    flash_attention: parseOptionalBoolean(controls.flashAttentionInput?.value),
                    num_experts: parseOptionalInteger(controls.numExpertsInput?.value),
                    offload_kv_cache_to_gpu: parseOptionalBoolean(controls.offloadKvCacheInput?.value),
                }),
            };
        } else if (actionKey === 'unload') {
            const model = String(controls.input?.value || '').trim();
            if (!model) {
                setLMStudioStatus('unload', t('provider_lmstudio_action_required_selection', 'Please select a model.'), 'error');
                return;
            }
            targetName = model;
            url = `/api/v1/llm/lmstudio/model/unload?${new URLSearchParams({
                lmstudio_provider_id: providerId,
                model,
            }).toString()}`;
            fetchOptions = { method: 'DELETE' };
        } else {
            setLMStudioStatus(actionKey, t('provider_lmstudio_action_unsupported', 'Unsupported action.'), 'error');
            return;
        }

        state.lmstudioActionState[actionKey] = { running: true };
        setLMStudioControlsDisabled(actionKey, true);
        setLMStudioStatus(
            actionKey,
            actionKey === 'load'
                ? formatT('provider_lmstudio_load_started', 'Loading "{model}"...', { model: targetName })
                : formatT('provider_lmstudio_unload_started', 'Unloading "{model}"...', { model: targetName }),
            'info'
        );

        try {
            const response = await authedFetch(url, fetchOptions || undefined);
            if (!response.ok) {
                const message = await extractResponseErrorMessage(response, t('provider_lmstudio_action_failed_retry', 'Action failed. Please try again.'));
                setLMStudioStatus(actionKey, message, 'error');
                return;
            }

            Object.values(controls).forEach((control) => {
                if (control instanceof HTMLInputElement || control instanceof HTMLSelectElement) {
                    clearModelControlValue(control);
                }
            });
            await loadLMStudioModels();
            // The models refresh rebuilds the section, so the success message
            // is set afterwards to not get wiped by the re-render.
            setLMStudioStatus(
                actionKey,
                actionKey === 'load'
                    ? formatT('provider_lmstudio_load_completed', 'Loaded "{model}" successfully.', { model: targetName })
                    : formatT('provider_lmstudio_unload_completed', 'Unloaded "{model}" successfully.', { model: targetName }),
                'success'
            );
        } catch (error) {
            console.error(`Failed to ${actionKey} LM Studio model`, error);
            setLMStudioStatus(actionKey, error?.message || t('provider_lmstudio_action_failed_retry', 'Action failed. Please try again.'), 'error');
        } finally {
            state.lmstudioActionState[actionKey] = { running: false };
            setLMStudioControlsDisabled(actionKey, false);
        }
    };

    const loadLMStudioModels = async () => {
        const providerId = state.editingId;
        if (!providerId) {
            return;
        }

        const modelsRefresh = beginModelsTabRefresh(providerId);
        removeOllamaLoadedModelsSection({ showPlaceholder: false });
        hideModelsPlaceholder();

        try {
            const [allResponse, loadedResponse] = await Promise.all([
                authedFetch(`/api/v1/llm/lmstudio/models/all?lmstudio_provider_id=${encodeURIComponent(providerId)}`),
                authedFetch(`/api/v1/llm/lmstudio/models/loaded?lmstudio_provider_id=${encodeURIComponent(providerId)}`),
            ]);

            if (!allResponse.ok) {
                const message = await extractResponseErrorMessage(
                    allResponse,
                    t('provider_lmstudio_models_failed', 'Unable to load LM Studio models. Please try again later.')
                );
                setModelsPlaceholder(message);
                updateModelsTabVisibility();
                return;
            }

            if (!loadedResponse.ok) {
                const message = await extractResponseErrorMessage(
                    loadedResponse,
                    t('provider_lmstudio_loaded_models_failed', 'Unable to load LM Studio loaded instances. Please try again later.')
                );
                setModelsPlaceholder(message);
                updateModelsTabVisibility();
                return;
            }

            const [allData, loadedData] = await Promise.all([
                allResponse.json(),
                loadedResponse.json(),
            ]);

            if (providerId !== state.editingId) {
                return;
            }

            if (!Array.isArray(allData) || !Array.isArray(loadedData)) {
                setModelsPlaceholder(t('provider_lmstudio_models_failed', 'Unable to load LM Studio models. Please try again later.'));
                updateModelsTabVisibility();
                return;
            }

            state.lmstudioAllModels = allData;
            state.lmstudioLoadedModels = loadedData;

            ensureLMStudioControls();
            renderLMStudioInstalledModelsTable(state.lmstudioAllModels);
            renderLMStudioLoadedInstancesTable(state.lmstudioLoadedModels);

        } catch (error) {
            console.error('Failed to load LM Studio model data', error);
            setModelsPlaceholder(error?.message || t('provider_lmstudio_models_failed', 'Unable to load LM Studio models. Please try again later.'));
            updateModelsTabVisibility();
        } finally {
            // Every exit path must restore the tab state captured above.
            completeModelsTabRefresh(modelsRefresh);
        }
    };

    const refreshProviderExtras = async () => {
        const normalizedProvider = (state.providerKey || '').toLowerCase();
        if (state.mode === 'edit' && normalizedProvider === 'ollama') {
            await loadOllamaLoadedModels();
        } else if (state.mode === 'edit' && normalizedProvider === 'lmstudio') {
            await loadLMStudioModels();
        } else {
            removeOllamaLoadedModelsSection();
            state.modelsTabReady = false;
        }
        updateModelsTabVisibility();
    };

    const createFieldInput = (field) => {
        const type = (field.type || 'input').toLowerCase();
        if (type === 'toggle' || type === 'boolean') {
            return createToggleInput(field);
        }
        if (type === 'select') {
            return createSelectInput(field);
        }
        return createTextInput(field);
    };

    const createTextInput = (field) => {
        const fieldType = (field.type || '').toLowerCase();
        const inputType = (field.input_type || 'str').toLowerCase();
        if (inputType === 'textarea') {
            const textarea = document.createElement('textarea');
            textarea.className = 'provider-form-control';
            textarea.rows = 12;
            textarea.spellcheck = false;
            const placeholder = resolveFieldPlaceholder(field);
            if (placeholder) {
                textarea.placeholder = placeholder;
            }
            if (field.default !== undefined && field.default !== null) {
                textarea.value = field.default;
            }
            return textarea;
        }

        const input = document.createElement('input');
        input.className = 'provider-form-control';
        if (['int', 'integer'].includes(fieldType) || ['int', 'integer'].includes(inputType)) {
            input.type = 'number';
            input.step = '1';
        } else if (fieldType === 'number' || ['float', 'double', 'number'].includes(inputType)) {
            input.type = 'number';
            input.step = 'any';
        } else if (inputType === 'password' || /secret|token/i.test(field.id)) {
            input.type = 'password';
        } else {
            input.type = 'text';
        }
        const placeholder = resolveFieldPlaceholder(field);
        if (placeholder) {
            input.placeholder = placeholder;
        }
        if (field.default !== undefined && field.default !== null) {
            input.value = field.default;
        }
        return input;
    };

    const createToggleInput = (field) => {
        const wrapper = document.createElement('label');
        wrapper.className = 'toggle-switch';
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.className = 'toggle-input';
        input.checked = field.default === true;
        const slider = document.createElement('span');
        slider.className = 'toggle-slider';
        wrapper.appendChild(input);
        wrapper.appendChild(slider);
        return wrapper;
    };

    const createSelectInput = (field) => {
        const select = document.createElement('select');
        select.className = 'provider-form-control';
        const metaOptions = FIELD_META[field.id]?.options;
        const options = Array.isArray(field.options) ? field.options : Array.isArray(metaOptions) ? metaOptions : [];
        const placeholder = resolveFieldPlaceholder(field);
        if (placeholder) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = placeholder;
            if (field.required) {
                option.disabled = true;
                option.selected = true;
            }
            select.appendChild(option);
        } else if (!field.required) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = t('common_optional', 'Optional');
            select.appendChild(option);
        }
        options.forEach((option) => {
            if (!option) {
                return;
            }
            const optionEl = document.createElement('option');
            optionEl.value = option.value ?? option.id ?? '';
            optionEl.textContent = resolveSchemaOptionLabel(option);
            select.appendChild(optionEl);
        });
        const defaultValue = field.default ?? FIELD_META[field.id]?.default;
        if (defaultValue !== undefined && defaultValue !== null) {
            select.value = defaultValue;
        }
        return select;
    };

    const resolveControlElement = (element) => {
        if (!element) {
            return null;
        }
        if (element.matches?.('input, select, textarea')) {
            return element;
        }
        return element.querySelector?.('input, select, textarea') || null;
    };

    const extractFieldValue = (control, definition) => {
        if (!control) {
            return undefined;
        }
        if (control.type === 'checkbox') {
            return control.checked;
        }
        const value = control.value;
        if (value === '' || value === null || value === undefined) {
            return definition.required ? value : undefined;
        }
        if ((definition.type || '').toLowerCase() === 'number') {
            const parsed = Number(value);
            return Number.isNaN(parsed) ? undefined : parsed;
        }
        const inputType = (definition.input_type || '').toLowerCase();
        if (['int', 'integer'].includes(inputType)) {
            const parsed = parseInt(value, 10);
            return Number.isNaN(parsed) ? undefined : parsed;
        }
        if (['float', 'double', 'number'].includes(inputType)) {
            const parsed = parseFloat(value);
            return Number.isNaN(parsed) ? undefined : parsed;
        }
        return value;
    };

    const setNestedValue = (target, segments, value) => {
        if (!Array.isArray(segments) || !segments.length) {
            return;
        }
        let current = target;
        segments.forEach((segment, index) => {
            if (index === segments.length - 1) {
                current[segment] = value;
                return;
            }
            if (typeof current[segment] !== 'object' || current[segment] === null) {
                current[segment] = {};
            }
            current = current[segment];
        });
    };

    const buildProviderPayload = () => {
        if (!state.providerKey) {
            return null;
        }

        const data = {};
        state.controls.forEach((control, fieldId) => {
            const definition = state.definitions.get(fieldId) || {};
            const value = extractFieldValue(control, definition);
            if (value === undefined) {
                return;
            }
            setNestedValue(data, fieldId.split('.'), value);
        });

        const payload = {
            name: data.name || state.editingData?.name || '',
            settings: data.settings || {},
        };
        // Do not send an icon for native providers. This keeps the wire
        // contract aligned with the schema and leaves the fixed brand value
        // under backend control. Custom endpoint providers retain the
        // selected preset/SVG/image and fall back to their brand when empty.
        if (providerSupportsCustomIcon(state.providerKey)) {
            payload.icon = data.icon || getDefaultProviderIconKey(state.providerKey);
        }
        if (data.api_key !== undefined) {
            payload.api_key = data.api_key;
        }

        if (state.mode === 'create') {
            payload.provider = state.providerKey;
        }

        return payload;
    };

    const toggleSubmitting = (isOn, targetFormDom = activeFormDom()) => {
        const submitButton = targetFormDom?.submit;
        if (!submitButton) {
            return;
        }
        const loadingLabel = state.mode === 'edit'
            ? t('admin_saving', 'Saving...')
            : t('admin_creating_ellipsis', 'Creating...');

        if (typeof window.setButtonLoadingState === 'function') {
            window.setButtonLoadingState(submitButton, isOn, loadingLabel);
            return;
        }

        const labelTarget = submitButton.querySelector('span');
        const getLabel = () => (labelTarget ? labelTarget.textContent : submitButton.textContent);
        const setLabel = (value) => {
            if (labelTarget) {
                labelTarget.textContent = value;
            } else {
                submitButton.textContent = value;
            }
        };

        if (isOn) {
            if (!submitButton.dataset.originalLabel) {
                submitButton.dataset.originalLabel = getLabel()?.trim() || '';
            }
            submitButton.disabled = true;
            submitButton.classList.add('loading');
            submitButton.setAttribute('aria-busy', 'true');
            setLabel(loadingLabel);
            void submitButton.offsetWidth;
        } else {
            submitButton.disabled = false;
            submitButton.classList.remove('loading');
            submitButton.removeAttribute('aria-busy');
            if (submitButton.dataset.originalLabel !== undefined) {
                setLabel(submitButton.dataset.originalLabel || '');
                delete submitButton.dataset.originalLabel;
            }
            void submitButton.offsetWidth;
        }
    };

    /**
     * Validate the visible provider draft before any create, update, or test
     * request. Keeping this boundary shared prevents secondary actions such as
     * "Test connection" from bypassing the form's accessible field errors.
     */
    const validateProviderDraft = () => {
        const controlsArray = [];
        state.definitions.forEach((field, key) => {
            const control = state.controls.get(key);
            if (control) {
                controlsArray.push({ field, control });
            }
        });
        if (controlsArray.length && !window.FieldValidation?.validate(controlsArray, { notify: false })) {
            return false;
        }

        const formDom = activeFormDom();
        return !(formDom?.form && !formDom.form.reportValidity?.());
    };

    const handleProviderFormSubmit = async (event) => {
        event.preventDefault();
        if (state.submitting) {
            return;
        }
        if (!state.providerKey) {
            notifyError(state.mode === 'edit'
                ? t('provider_form_edit_unavailable', 'Unable to edit provider.')
                : t('provider_form_select_provider_first', 'Please select a provider.'));
            return;
        }

        if (!validateProviderDraft()) {
            return;
        }
        const formDom = activeFormDom();
        const payload = buildProviderPayload();
        if (!payload) {
            notifyError(t('provider_form_submit_invalid', 'Unable to submit provider. Please review the form.'));
            return;
        }
        try {
            state.submitting = true;
            toggleSubmitting(true, formDom);
            const isEdit = state.mode === 'edit';
            if (isEdit) {
                await window.providersApi.updateProvider(state.editingId, payload);
            } else {
                await window.providersApi.createProvider(payload);
            }
            const label = state.editingData?.name || formatProviderLabel(state.providerKey);
            notifySuccess(formatT(
                isEdit ? 'provider_form_update_success' : 'provider_form_create_success',
                isEdit ? '{provider} provider updated successfully.' : '{provider} provider created successfully.',
                { provider: label }
            ));
            setDirtyFlag(false);
            goToProvidersList();
        } catch (error) {
            console.error('Failed to submit provider', error);
            notifyError(error?.message || t(
                state.mode === 'edit' ? 'provider_form_update_failed' : 'provider_form_create_failed',
                state.mode === 'edit' ? 'Failed to update provider.' : 'Failed to create provider.'
            ));
        } finally {
            state.submitting = false;
            toggleSubmitting(false, formDom);
        }
    };

    const handleFormBack = () => {
        if (state.mode === 'edit') {
            requestUnsavedConfirmation(() => goToProvidersList());
            return;
        }
        requestUnsavedConfirmation(() => showSelection());
    };

    const handleProviderListClick = (event) => {
        if (event.target.closest('.delete-btn')) {
            return;
        }

        const editButton = event.target.closest('.edit-btn');
        if (editButton?.dataset.providerId) {
            const providerKey = editButton.dataset.providerKey || editButton.closest('.provider-row')?.dataset.providerKey;
            const providerName = editButton.dataset.providerName || editButton.closest('.provider-row')?.dataset.providerName || '';
            openEditProvider(editButton.dataset.providerId, providerKey, { name: providerName });
            return;
        }

        const row = event.target.closest('.provider-row');
        if (!row?.dataset.providerId || event.target.closest('.provider-actions')) {
            return;
        }

        openEditProvider(row.dataset.providerId, row.dataset.providerKey, { name: row.dataset.providerName });
    };

    const handleGlobalKeydown = (event) => {
        if (event.key !== 'Escape' || event.defaultPrevented) {
            return;
        }

        if (state.view === 'edit') {
            event.preventDefault();
            requestUnsavedConfirmation(() => goToProvidersList());
            return;
        }

        if (state.view === 'create') {
            event.preventDefault();
            requestUnsavedConfirmation(() => showSelection());
            return;
        }

        if (state.view === 'select') {
            event.preventDefault();
            goToProvidersList();
        }
    };

    const toggleTesting = (isOn) => {
        const formDom = activeFormDom();
        if (!formDom?.test) {
            return;
        }
        formDom.test.disabled = isOn;
        formDom.test.classList.toggle('loading', isOn);
    };

    /**
     * Resolve stable backend error codes into administrator-facing copy.
     *
     * Provider APIs retain the parsed response on `error.payload`, allowing
     * this feature to translate known application errors while preserving the
     * normal request fallback for upstream and unexpected failures.
     */
    const getProviderTestErrorMessage = (error) => {
        const code = error?.payload?.detail?.code;
        switch (code) {
            case 'provider_test_saved_provider_type_mismatch':
                return t(
                    'provider_test_saved_provider_type_mismatch',
                    'The saved provider type no longer matches this configuration. Reopen the provider and try again.'
                );
            case 'provider_test_api_key_required':
                return t(
                    'provider_test_api_key_required',
                    'An API key is required to test this provider.'
                );
            default:
                return error?.message || t(
                    'provider_test_connection_failed',
                    'Failed to test provider connection.'
                );
        }
    };

    const handleProviderTest = async () => {
        if (!state.providerKey) {
            notifyError(t('provider_test_select_provider_first', 'Please select a provider before testing.'));
            return;
        }
        if (!validateProviderDraft()) {
            return;
        }

        const payload = buildProviderPayload();
        if (!payload) {
            notifyError(t('provider_test_fill_required_fields', 'Please fill in the required fields before testing.'));
            return;
        }

        const testPayload = {
            provider: state.providerKey,
            // The backend uses the saved provider only to recover secrets that
            // are intentionally unavailable to the browser. All visible draft
            // values below are still tested before the provider is saved.
            provider_id: state.mode === 'edit' ? state.editingId : undefined,
            api_key: payload.api_key,
            base_url: payload.settings?.base_url,
            settings: payload.settings || {},
        };

        try {
            toggleTesting(true);
            const result = await window.providersApi.testProviderConnection(testPayload);
            if (!result) {
                throw new Error(t('provider_test_failed', 'Provider test failed.'));
            }
            if (result.status === 'warning') {
                const warningMessage = result.message
                    || t('provider_test_warning_default', 'The provider could not confirm model availability. Custom endpoints may not expose model lists.');
                const modelCount = typeof result?.model_count === 'number' ? result.model_count : 0;
                const finalMessage = modelCount > 0
                    ? formatT('provider_test_warning_with_models', '{message} {count} models reported.', { message: warningMessage, count: modelCount })
                    : formatT('provider_test_warning_no_models', '{message} No models reported.', { message: warningMessage });
                notifyWarning(finalMessage);
                return;
            }
            if (result.status !== 'success') {
                const errorMessage = result?.error || result?.detail || t('provider_test_failed', 'Provider test failed.');
                throw new Error(errorMessage);
            }
            const modelCount = typeof result?.model_count === 'number' ? result.model_count : 0;
            notifySuccess(formatT('provider_test_success', 'Connection successful. {count} models detected.', { count: modelCount }));
        } catch (error) {
            console.error('Failed to test provider connection', error);
            notifyError(getProviderTestErrorMessage(error));
        } finally {
            toggleTesting(false);
        }
    };

    const bindFormControls = () => {
        ['create', 'edit'].forEach((mode) => {
            const formDom = dom.forms[mode];
            if (!formDom) {
                return;
            }
            formDom.back?.addEventListener('click', handleFormBack);
            formDom.form?.addEventListener('submit', handleProviderFormSubmit);
            formDom.test?.addEventListener('click', handleProviderTest);
        });
    };

    const init = () => {
        if (state.initialized) {
            return;
        }
        state.initialized = true;
        registerUnsavedGuard();
        dom.button?.addEventListener('click', () => showSelection(true));
        dom.backSelect?.addEventListener('click', () => requestUnsavedConfirmation(() => goToProvidersList()));
        bindFormControls();
        bindProviderEditTabs();
        setProviderTab('settings');
        updateModelsTabVisibility();
        const list = document.querySelector('.provider-list');
        if (list && list.dataset.providerEditBound !== 'true') {
            list.addEventListener('click', handleProviderListClick);
            list.dataset.providerEditBound = 'true';
        }

        document.addEventListener('keydown', handleGlobalKeydown);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    if (typeof window !== 'undefined') {
        window.initProvidersCreate = init;
        window.adminProvidersShowList = goToProvidersList;
        window.openEditProvider = openEditProvider;
    }
})();
