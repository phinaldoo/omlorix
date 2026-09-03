(function () {
    const pages = {
        list: document.getElementById('page-models'),
        step1: document.getElementById('page-models-create-step-1'),
        step2: document.getElementById('page-models-create-step-2'),
        stepOpenRouterProvider: document.getElementById('page-models-create-step-openrouter-provider'),
        step3: document.getElementById('page-models-create-step-3'),
    };

    if (!pages.list || !pages.step1 || !pages.step2 || !pages.step3) {
        window.startModelsCreateFlow = () => {};
        window.adminModelsShowList = () => {};
        return;
    }

    const dom = {
        providerGrid: document.getElementById('modelCreateProviderGrid'),
        providerBack: document.getElementById('modelCreateProviderBack'),
        providerSort: document.getElementById('modelCreateProviderSortSelect'),
        providerSearch: document.getElementById('modelCreateProviderSearchInput'),
        providerSearchClear: document.getElementById('modelCreateProviderSearchClear'),
        base: {
            title: document.getElementById('modelCreateBaseTitle'),
            subtitle: document.getElementById('modelCreateBaseSubtitle'),
            search: document.getElementById('modelCreateBaseSearch'),
            list: document.getElementById('modelCreateBaseModelsList'),
            back: document.getElementById('modelCreateBaseBack'),
            next: document.getElementById('modelCreateBaseNext'),
            select: document.getElementById('modelCreateBaseSelect'),
            toggle: document.getElementById('modelCreateBaseSelectToggle'),
            dropdown: document.getElementById('modelCreateBaseSelectDropdown'),
            selectedLabel: document.getElementById('modelCreateBaseSelectedLabel'),
        },
        form: {
            title: document.getElementById('modelCreateFormTitle'),
            subtitle: document.getElementById('modelCreateFormSubtitle'),
            form: document.getElementById('modelCreateForm'),
            back: document.getElementById('modelCreateFormBack'),
            submit: document.getElementById('modelCreateSubmit'),
            modelId: document.getElementById('modelCreateModelIdInput'),
            name: document.getElementById('modelCreateNameInput'),
            description: document.getElementById('modelCreateDescriptionInput'),
            icon: document.getElementById('modelCreateIconInput'),
            status: document.getElementById('modelCreateStatusSelect'),
            accessEveryone: document.getElementById('modelCreateAccessEveryone'),
            accessUsers: document.getElementById('modelCreateAccessUsers'),
            accessGroups: document.getElementById('modelCreateAccessGroups'),
            schemaFields: document.getElementById('modelCreateSchemaFields'),
            schemaLoading: document.getElementById('modelCreateSchemaLoading'),
        },
        openrouterProvider: {
            title: document.getElementById('modelCreateOpenRouterProviderTitle'),
            subtitle: document.getElementById('modelCreateOpenRouterProviderSubtitle'),
            list: document.getElementById('modelCreateOpenRouterProvidersList'),
            loading: document.getElementById('modelCreateOpenRouterProvidersLoading'),
            back: document.getElementById('modelCreateOpenRouterProviderBack'),
            next: document.getElementById('modelCreateOpenRouterProviderNext'),
            mode: document.querySelector('.openrouter-provider-mode'),
            modeRadios: document.querySelectorAll('input[name="openrouterProviderMode"]'),
            sortRow: document.getElementById('modelCreateOpenRouterProviderSortRow'),
            sortSelect: document.getElementById('modelCreateOpenRouterProviderSortSelect'),
            autoHint: document.getElementById('modelCreateOpenRouterProviderAutoHint'),
        },
    };

    const modelsApi = window.modelsApi || {};
    const t = window.adminT || ((key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback ?? key);
        }
        return fallback !== undefined ? fallback : key;
    });
    const formatT = window.adminFormatT || ((key, fallback, vars) => {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        const template = t(key, fallback);
        return String(template).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars?.[token];
            return value === undefined || value === null ? '' : String(value);
        });
    });
    const getFieldPlaceholder = window.getFieldPlaceholder || ((field, fallback = '') => {
        if (!field) {
            return fallback;
        }
        if (field.i18n_placeholder && typeof window.getTranslation === 'function') {
            return window.getTranslation(field.i18n_placeholder, field.placeholder || fallback);
        }
        return field.placeholder || fallback;
    });

    const state = {
        view: 'list',
        providers: [],
        providerGroups: [],
        providerSort: 'recommended',
        providerSearchTerm: '',
        baseModels: [],
        provider: null,
        isProviderGroup: false,
        selectedBaseModelId: null,
        selectedBaseModel: null,
        schemaFields: [],
        schemaControls: [],
        loadingProviders: false,
        loadingBaseModels: false,
        loadingSchema: false,
        skippingBaseModelStep: false,
        openrouterProviders: [],
        selectedOpenRouterProvider: null,
        loadingOpenRouterProviders: false,
        openrouterProviderMode: 'auto',
        openrouterProviderSort: 'price',
        step3Snapshot: null,
    };
    let providerSortSelectMeta = null;
    let providerSortI18nBound = false;
    const UNSAVED_GUARD_ID = 'admin-models-create-step3-unsaved';
    let unsavedGuardRegistered = false;

    const MODEL_DESCRIPTION_MAX_LENGTH = 100;

    const normalizeModelDescription = (value) => String(value || '').trim().slice(0, MODEL_DESCRIPTION_MAX_LENGTH);

    const toggleElementVisibility = (element, show) => {
        if (!element) return;
        element.hidden = !show;
    };

    const resolveProviderIconMarkup = (provider = {}) => {
        const iconsMap = (typeof Icons !== 'undefined' && Icons) || (window?.Icons) || {};
        const iconValue = typeof provider.icon === 'string' ? provider.icon.trim() : '';
        const normalizeKey = (value) => {
            if (!value || typeof value !== 'string') {
                return '';
            }
            const lowered = value.trim().toLowerCase();
            if (lowered === 'openai_responses' || lowered === 'openai_chat_completions') {
                return 'openai';
            } else if (lowered === 'microsoft_azure') {
                return 'microsoft';
            } else if (lowered === "anthropic_base") {
                return 'anthropic';
            }
            return lowered;
        };
        const lookupIcon = (key) => {
            if (!key) {
                return '';
            }
            const mapped = iconsMap?.[key];
            return typeof mapped === 'string' && mapped.trim() ? mapped : '';
        };

        const fallbackKey = normalizeKey(provider.provider);
        const fallback = lookupIcon(fallbackKey) || iconsMap?.omlorix || '';

        // Provider icons can contain gradients, clip paths, masks, and embedded
        // images referenced by SVG IDs. The admin app keeps hidden pages in the
        // DOM, so rendering the raw preset more than once can make those IDs
        // collide. IconPicker rewrites each ID and reference for every instance.
        if (window.IconPicker?.renderIconMarkup) {
            return window.IconPicker.renderIconMarkup(iconValue || fallbackKey, {
                fallback,
                imageAlt: t('providers_icon_alt', 'Provider icon'),
            });
        }

        if (iconValue.startsWith('<')) {
            return iconValue;
        }

        if (iconValue) {
            const customIcon = lookupIcon(iconValue);
            if (customIcon) {
                return customIcon;
            }
        }

        return fallback;
    };

    const DEFAULT_BASE_MODEL_LABEL = t('model_base_select_placeholder', 'Select a base model');
    let baseDropdownOpen = false;

    const setSelectedBaseLabel = (model) => {
        if (!dom.base.selectedLabel) {
            return;
        }
        const labelText = model
            ? (model.name || model.model || model.id || DEFAULT_BASE_MODEL_LABEL)
            : DEFAULT_BASE_MODEL_LABEL;
        dom.base.selectedLabel.textContent = labelText;
        dom.base.select?.classList.toggle('has-selection', Boolean(model));
    };

    const handleBaseOutsideClick = (event) => {
        if (!dom.base.select) {
            return;
        }
        if (!dom.base.select.contains(event.target)) {
            closeBaseDropdown();
        }
    };

    const handleBaseDropdownKeydown = (event) => {
        if (event.key === 'Escape') {
            closeBaseDropdown();
        }
    };

    const openBaseDropdown = () => {
        if (!dom.base.dropdown || baseDropdownOpen) {
            return;
        }
        dom.base.dropdown.hidden = false;
        dom.base.toggle?.setAttribute('aria-expanded', 'true');
        dom.base.select?.classList.add('open');
        baseDropdownOpen = true;
        document.addEventListener('click', handleBaseOutsideClick, true);
        document.addEventListener('keydown', handleBaseDropdownKeydown);
        requestAnimationFrame(() => {
            dom.base.search?.focus();
            dom.base.search?.select();
        });
    };

    const closeBaseDropdown = () => {
        if (!dom.base.dropdown || !baseDropdownOpen) {
            return;
        }
        dom.base.dropdown.hidden = true;
        dom.base.toggle?.setAttribute('aria-expanded', 'false');
        dom.base.select?.classList.remove('open');
        baseDropdownOpen = false;
        document.removeEventListener('click', handleBaseOutsideClick, true);
        document.removeEventListener('keydown', handleBaseDropdownKeydown);
    };

    const renderBaseModelsPlaceholder = (message) => {
        if (!dom.base.list) {
            return;
        }
        dom.base.list.innerHTML = `<p class="model-base-select-placeholder">${message}</p>`;
    };

    const clearBaseSelection = () => {
        state.selectedBaseModelId = null;
        state.selectedBaseModel = null;
        setSelectedBaseLabel(null);
        if (dom.base.next) {
            dom.base.next.disabled = true;
        }
    };

    const resetBaseSelectUI = (placeholderMessage = t('models_create_select_provider_first', 'Select a provider to load models.')) => {
        clearBaseSelection();
        closeBaseDropdown();
        if (dom.base.search) {
            dom.base.search.value = '';
        }
        renderBaseModelsPlaceholder(placeholderMessage);
    };

    const notifyWarn = (message) => {
        if (typeof notifyWarning === 'function') {
            notifyWarning(message);
        } else if (typeof console !== 'undefined') {
            console.warn(message);
        }
    };

    const notifyErr = (message) => {
        if (typeof notifyError === 'function') {
            notifyError(message);
        } else if (typeof console !== 'undefined') {
            console.error(message);
        }
    };

    const notifyInfo = (message) => {
        if (typeof notifySuccess === 'function') {
            notifySuccess(message);
        }
    };

    const setButtonLoadingState = window.setButtonLoadingState || ((button, isLoading) => {
        if (!button) return;
        button.disabled = Boolean(isLoading);
    });

    const PROVIDER_SORT_STORAGE_KEY = 'admin.modelsCreate.providerSort';

    const getSavedProviderSort = () => {
        try {
            const saved = window.localStorage?.getItem(PROVIDER_SORT_STORAGE_KEY);
            return saved || 'recommended';
        } catch (_error) {
            return 'recommended';
        }
    };

    const saveProviderSort = (sortValue) => {
        try {
            window.localStorage?.setItem(PROVIDER_SORT_STORAGE_KEY, sortValue);
        } catch (_error) {
            // Ignore storage failures (private mode, quota, etc.)
        }
    };

    const normalizeProviderSort = (sortValue) => {
        const allowed = new Set(['recommended', 'name_asc', 'name_desc', 'type_asc', 'group_first', 'provider_first']);
        return allowed.has(sortValue) ? sortValue : 'recommended';
    };

    const getProviderSortLabels = () => ({
        recommended: t('models_create_provider_sort_recommended', 'Recommended'),
        name_asc: t('models_create_provider_sort_name_asc', 'Name (A-Z)'),
        name_desc: t('models_create_provider_sort_name_desc', 'Name (Z-A)'),
        type_asc: t('models_create_provider_sort_type_asc', 'Provider type (A-Z)'),
        group_first: t('models_create_provider_sort_group_first', 'Groups first'),
        provider_first: t('models_create_provider_sort_provider_first', 'Providers first'),
    });

    const applyProviderSortSelectLabels = () => {
        if (!dom.providerSort) {
            return;
        }
        const providerSortLabels = getProviderSortLabels();
        const options = Array.from(dom.providerSort.options || []);
        options.forEach((option) => {
            const label = providerSortLabels[option.value];
            if (label) {
                option.textContent = label;
            }
        });
        const ariaLabel = t('models_create_provider_sort_label', 'Sort providers');
        dom.providerSort.setAttribute('aria-label', ariaLabel);
        providerSortSelectMeta?.syncFromSelect?.();
    };

    const upgradeProviderSortSelect = () => {
        if (!dom.providerSort || typeof window.upgradeAdminSingleSelect !== 'function') {
            return;
        }
        const providerSortLabels = getProviderSortLabels();
        providerSortSelectMeta = window.upgradeAdminSingleSelect(dom.providerSort, {
            key: 'models-create-provider-sort',
            placeholder: providerSortLabels.recommended || t('models_create_provider_sort_recommended', 'Recommended'),
        }) || null;
        providerSortSelectMeta?.syncFromSelect?.();
    };

    const refreshProviderSortSelectTranslations = () => {
        if (!dom.providerSort) {
            return;
        }
        applyProviderSortSelectLabels();
        providerSortSelectMeta?.syncFromSelect?.();
    };

    const normalizeSearchText = (value = '') => String(value).toLowerCase().replace(/[\s_-]+/g, ' ').trim();

    const updateProviderSearchClearVisibility = () => {
        if (!dom.providerSearchClear) {
            return;
        }
        dom.providerSearchClear.hidden = !state.providerSearchTerm;
    };

    const showPage = (key) => {
        Object.entries(pages).forEach(([name, node]) => {
            if (!node) return;
            node.hidden = name !== key;
        });
        state.view = key;
    };

    const goToList = () => {
        showPage('list');
        state.provider = null;
        state.isProviderGroup = false;
        state.baseModels = [];
        state.selectedBaseModelId = null;
        state.selectedBaseModel = null;
        state.openrouterProviders = [];
        state.selectedOpenRouterProvider = null;
        resetBaseSelectUI(t('models_create_select_provider_first', 'Select a provider to load models.'));
    };

    const resetFormValues = () => {
        if (!dom.form.form) return;
        // Clear any existing field validation errors
        const errorRows = dom.form.schemaFields?.querySelectorAll('.settings-row.has-error') || [];
        errorRows.forEach((row) => {
            row.classList.remove('has-error', 'shake-error');
            const errorEl = row.querySelector('.field-error-message');
            if (errorEl) errorEl.remove();
        });
        dom.form.form.reset();
        if (dom.form.modelId) {
            dom.form.modelId.value = '';
        }
        dom.form.schemaFields.innerHTML = '';
        if (dom.form.schemaLoading) {
            dom.form.schemaLoading.hidden = false;
            dom.form.schemaFields.appendChild(dom.form.schemaLoading);
        }
        state.schemaFields = [];
        state.schemaControls = [];
        state.step3Snapshot = null;
    };

    const applyProvidersLoadingState = (message = t('providers_loading', 'Loading providers...')) => {
        if (!dom.providerGrid) return;
        dom.providerGrid.innerHTML = '';
        dom.providerGrid.appendChild(window.createAdminLoadingPlaceholder({
            message,
            className: '',
        }));
    };

    const applyProvidersEmptyState = (title, description = '') => {
        if (!dom.providerGrid) return;
        dom.providerGrid.innerHTML = '';
        dom.providerGrid.appendChild(window.createAdminEmptyPlaceholder({
            title,
            description,
            className: 'provider-empty-state',
        }));
    };

    const renderProviderCards = (providers, providerGroups = []) => {
        if (!dom.providerGrid) {
            return;
        }

        const providerEntries = [];
        providerGroups.forEach((group, index) => {
            providerEntries.push({
                kind: 'group',
                item: group,
                defaultRank: index,
                displayName: String(group?.name || '').trim().toLowerCase(),
                providerType: String(group?.provider || '').trim().toLowerCase(),
            });
        });
        providers.forEach((provider, index) => {
            providerEntries.push({
                kind: 'provider',
                item: provider,
                defaultRank: providerGroups.length + index,
                displayName: String(provider?.name || formatProviderLabel?.(provider?.provider) || provider?.provider || '').trim().toLowerCase(),
                providerType: String(provider?.provider || '').trim().toLowerCase(),
            });
        });

        if (!providerEntries.length) {
            applyProvidersEmptyState(t('provider_group_no_providers_available', 'No providers available. Create a provider first.'));
            return;
        }

        const searchTerm = normalizeSearchText(state.providerSearchTerm);
        const filteredEntries = searchTerm
            ? providerEntries.filter((entry) => {
                const current = entry.item || {};
                const searchableName = normalizeSearchText(
                    entry.kind === 'group'
                        ? current.name
                        : (current.name || formatProviderLabel?.(current.provider) || current.provider)
                );
                const searchableType = normalizeSearchText(
                    entry.kind === 'group'
                        ? (current.provider || t('provider_group_badge', 'Group'))
                        : current.provider
                );
                return searchableName.includes(searchTerm) || searchableType.includes(searchTerm);
            })
            : providerEntries;

        if (!filteredEntries.length) {
            applyProvidersEmptyState(t('providers_search_no_results', 'No providers match your search.'));
            return;
        }

        const currentSort = normalizeProviderSort(state.providerSort);
        const sortedEntries = [...filteredEntries].sort((a, b) => {
            const byNameAsc = a.displayName.localeCompare(b.displayName);
            const byTypeAsc = a.providerType.localeCompare(b.providerType) || byNameAsc;
            if (currentSort === 'name_asc') {
                return byNameAsc;
            }
            if (currentSort === 'name_desc') {
                return b.displayName.localeCompare(a.displayName);
            }
            if (currentSort === 'type_asc') {
                return byTypeAsc;
            }
            if (currentSort === 'group_first') {
                const byKind = (a.kind === 'group' ? 0 : 1) - (b.kind === 'group' ? 0 : 1);
                return byKind || byNameAsc;
            }
            if (currentSort === 'provider_first') {
                const byKind = (a.kind === 'provider' ? 0 : 1) - (b.kind === 'provider' ? 0 : 1);
                return byKind || byNameAsc;
            }
            return a.defaultRank - b.defaultRank;
        });

        const fragment = document.createDocumentFragment();

        sortedEntries.forEach((entry) => {
            if (entry.kind === 'group') {
                const group = entry.item;
                const card = document.createElement('button');
                card.type = 'button';
                card.className = 'available-provider-card provider-group-card';
                card.dataset.providerId = group.id;
                card.dataset.isGroup = 'true';

                const iconsMap = (typeof Icons !== 'undefined' && Icons) || (window?.Icons) || {};
                const fallbackKey = iconsMap?.layers ? 'layers' : 'omlorix';
                const fallbackIcon = iconsMap?.[fallbackKey] || '';
                const configuredGroupIcon = typeof group.icon === 'string' ? group.icon.trim() : '';
                const isConfiguredGroupImage = Boolean(window.IconPicker?.isImageIconValue?.(configuredGroupIcon));
                const groupIconValue = configuredGroupIcon && (configuredGroupIcon.startsWith('<') || iconsMap[configuredGroupIcon] || isConfiguredGroupImage)
                    ? configuredGroupIcon
                    : fallbackKey;
                const groupIcon = window.IconPicker?.renderIconMarkup
                    ? window.IconPicker.renderIconMarkup(groupIconValue, {
                        fallback: fallbackIcon,
                        imageAlt: t('providers_icon_alt', 'Provider icon'),
                    })
                    : (group.icon && iconsMap[group.icon] ? iconsMap[group.icon] : fallbackIcon);

                card.innerHTML = `
                    <div class="available-provider-icon">${groupIcon}</div>
                    <div class="available-provider-card-title">${group.name}</div>
                    <div class="available-provider-card-description">
                        <span class="provider-group-badge">${t('provider_group_badge', 'Group')}</span>
                        <span>${formatT(group.member_count === 1 ? 'provider_group_member_count_single' : 'provider_group_member_count_plural', '{count} providers', { count: group.member_count })}</span>
                    </div>
                `;
                card.addEventListener('click', () => handleProviderGroupSelected(group));
                fragment.appendChild(card);
                return;
            }

            const provider = entry.item;
            const card = document.createElement('button');
            card.type = 'button';
            card.className = 'available-provider-card';
            card.dataset.providerId = provider.id;
            const providerIcon = resolveProviderIconMarkup(provider);

            card.innerHTML = `
                <div class="available-provider-icon">${providerIcon}</div>
                <div class="available-provider-card-title">${provider.name || formatProviderLabel?.(provider.provider) || provider.provider}</div>
            `;
            card.addEventListener('click', () => handleProviderSelected(provider));
            fragment.appendChild(card);
        });

        dom.providerGrid.innerHTML = '';
        dom.providerGrid.appendChild(fragment);
    };

    const loadProviders = async () => {
        state.loadingProviders = true;
        applyProvidersLoadingState();
        state.providerSort = normalizeProviderSort(getSavedProviderSort());
        if (dom.providerSort) {
            dom.providerSort.value = state.providerSort;
            providerSortSelectMeta?.syncFromSelect?.();
        }
        try {
            // Fetch both providers and provider groups in parallel
            const [providers, groups] = await Promise.all([
                modelsApi.fetchProviderList({ modelCapableOnly: true }),
                fetchProviderGroups().catch(() => []),
            ]);
            state.providers = providers;
            state.providerGroups = groups;
            renderProviderCards(state.providers, state.providerGroups);
        } catch (error) {
            notifyErr(error?.message || t('providers_fetch_failed', 'Failed to load providers'));
            applyProvidersEmptyState(
                t('providers_fetch_failed', 'Failed to load providers'),
                t('provider_groups_load_failed_text', 'Please try refreshing the page.')
            );
        } finally {
            state.loadingProviders = false;
        }
    };
    
    const fetchProviderGroups = async () => {
        const response = await window.authedFetch('/api/v1/llm/provider-groups');
        if (!response.ok) {
            return [];
        }
        return response.json();
    };
    
    const fetchProviderGroupModels = async (groupId) => {
        const response = await window.authedFetch(`/api/v1/llm/provider-group/models?group_id=${encodeURIComponent(groupId)}`);
        if (!response.ok) {
            const error = new Error(t('models_create_group_models_failed', 'Failed to load common models for this group. One or more providers may be offline.'));
            error.status = response.status;
            throw error;
        }
        return response.json();
    };
    
    const handleProviderGroupSelected = async (group) => {
        if (!group) {
            notifyWarn(t('provider_group_missing', 'Provider group is missing.'));
            return;
        }
        
        // Fetch group details to get the provider type from first member
        let providerKey = 'openai';
        try {
            const groupDetails = await window.authedFetch(`/api/v1/llm/provider-group?group_id=${encodeURIComponent(group.id)}`);
            if (groupDetails.ok) {
                const data = await groupDetails.json();
                if (data.members && data.members.length > 0) {
                    providerKey = data.members[0].provider || 'openai';
                }
            }
        } catch (e) {
            console.warn('Failed to fetch group details for provider type:', e);
        }
        
        state.provider = {
            id: group.id,
            key: providerKey,
            name: group.name,
        };
        state.isProviderGroup = true;
        state.baseModels = [];
        state.selectedBaseModelId = null;
        state.selectedBaseModel = null;
        state.skippingBaseModelStep = false;
        resetBaseSelectUI(t('models_create_loading_common_models', 'Loading common models...'));
        if (dom.base.title) {
            dom.base.title.textContent = formatT('models_create_select_base_model_for', 'Select Base Model ({name})', { name: group.name });
        }
        if (dom.base.subtitle) {
            dom.base.subtitle.textContent = t('models_create_group_subtitle', 'Only models supported by all providers in this group are shown.');
        }
        showPage('step2');
        loadBaseModelsForGroup();
    };
    
    const loadBaseModelsForGroup = async () => {
        if (!state.provider?.id || !state.isProviderGroup) {
            notifyWarn(t('models_create_select_group_first', 'Select a provider group first.'));
            return;
        }
        state.loadingBaseModels = true;
        if (dom.base.search) {
            dom.base.search.value = '';
        }
        setBaseModelsLoading(t('models_create_loading_common_models', 'Loading common models...'));
        try {
            state.baseModels = await fetchProviderGroupModels(state.provider.id);
            if (!state.baseModels.length) {
                setBaseModelsLoading(t('models_create_no_common_models', 'No common models found. The providers in this group may not share any models.'));
                return;
            }
            renderBaseModels(state.baseModels);
        } catch (error) {
            notifyErr(error?.message || t('models_create_group_models_failed', 'Failed to load common models for this group. One or more providers may be offline.'));
            setBaseModelsLoading(t('models_create_group_models_unavailable', 'Unable to load common models. The provider group may have offline members.'));
        } finally {
            state.loadingBaseModels = false;
        }
    };

    const setBaseModelsLoading = (message = t('models_loading', 'Loading models...')) => {
        renderBaseModelsPlaceholder(message);
        if (dom.base.next) {
            dom.base.next.disabled = true;
        }
    };

    const handleProviderSelected = (provider) => {
        if (!provider) {
            notifyWarn(t('models_create_provider_missing', 'Provider is missing.'));
            return;
        }
        state.provider = {
            id: provider.id,
            key: provider.provider,
            name: provider.name || provider.provider,
        };
        state.isProviderGroup = false;
        state.baseModels = [];
        state.selectedBaseModelId = null;
        state.selectedBaseModel = null;
        state.skippingBaseModelStep = false;
        resetBaseSelectUI(t('models_loading', 'Loading models...'));
        if (dom.base.title) {
            dom.base.title.textContent = formatT('models_create_select_base_model_for', 'Select Base Model ({name})', { name: state.provider.name });
        }
        if (dom.base.subtitle) {
            dom.base.subtitle.textContent = t('models_create_provider_subtitle', 'Pick an available model from this provider as the foundation.');
        }
        showPage('step2');
        loadBaseModels();
    };

    const renderBaseModels = (models) => {
        if (!dom.base.list) {
            return;
        }
        if (!models.length) {
            setBaseModelsLoading(t('models_create_no_provider_models', 'No models returned by this provider.'));
            return;
        }

        const normalize = (value = '') => value.toLowerCase().replace(/[\s-]+/g, '');
        const searchTermRaw = (dom.base.search?.value || '').trim();
        const searchTerm = normalize(searchTermRaw);
        const filtered = searchTerm
            ? models.filter((model) => {
                const name = normalize(model.name || '');
                const providerModel = normalize(model.model || '');
                const internalId = normalize(model.id || '');
                const description = normalize(model.description || '');
                return (
                    name.includes(searchTerm)
                    || providerModel.includes(searchTerm)
                    || internalId.includes(searchTerm)
                    || description.includes(searchTerm)
                );
            })
            : models;

        if (!filtered.length) {
            renderBaseModelsPlaceholder(t('models_empty_filtered', 'No models match your filters'));
            if (dom.base.next) {
                dom.base.next.disabled = true;
            }
            return;
        }

        const fragment = document.createDocumentFragment();
        filtered.forEach((model) => {
            const providerModel = model.model || '';
            const internalId = model.id || '';
            const modelId = internalId || providerModel;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'model-base-select-item';
            button.dataset.modelId = modelId;
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', state.selectedBaseModelId === modelId ? 'true' : 'false');

            if (state.selectedBaseModelId === modelId) {
                button.classList.add('selected');
            }

            const nameText = (model.name || '').trim();
            const descText = (model.description || '').trim();
            const idText = modelId || t('models_create_id_unavailable', 'ID unavailable');

            if (nameText) {
                const title = document.createElement('div');
                title.className = 'model-base-select-item-title';
                title.textContent = nameText;
                button.appendChild(title);
            }

            const meta = document.createElement('div');
            meta.className = 'model-base-select-item-meta';
            meta.textContent = idText;
            button.appendChild(meta);

            if (descText) {
                const desc = document.createElement('p');
                desc.className = 'model-base-select-item-desc';
                desc.textContent = descText;
                button.appendChild(desc);
            }

            button.addEventListener('click', () => selectBaseModel(model));
            fragment.appendChild(button);
        });

        dom.base.list.innerHTML = '';
        dom.base.list.appendChild(fragment);
        if (dom.base.next) {
            dom.base.next.disabled = !state.selectedBaseModelId;
        }
    };

    const selectBaseModel = (model) => {
        if (!model) {
            return;
        }
        state.selectedBaseModelId = model.id || model.model;
        state.selectedBaseModel = model;
        setSelectedBaseLabel(model);
        renderBaseModels(state.baseModels);
        if (dom.base.next) {
            dom.base.next.disabled = false;
        }
        closeBaseDropdown();
    };

    const loadBaseModels = async () => {
        if (!state.provider?.id) {
            notifyWarn(t('models_create_select_provider_first_warning', 'Select a provider first.'));
            return;
        }
        state.loadingBaseModels = true;
        if (dom.base.search) {
            dom.base.search.value = '';
        }
        setBaseModelsLoading();
        try {
            state.baseModels = await modelsApi.fetchProviderModels(state.provider.id);
            renderBaseModels(state.baseModels);
        } catch (error) {
            notifyErr(error?.message || t('models_create_provider_models_failed', 'Failed to load provider models.'));
            setBaseModelsLoading(t('models_create_proceed_without_models', 'Unable to load models. Proceeding to configuration...'));
            state.skippingBaseModelStep = true;
            notifyWarn(t('models_create_skipping_base_model', 'Skipping base model selection. Configure the model manually in the next step.'));
            await prepareStepThree({ allowMissingBaseModel: true });
        } finally {
            state.loadingBaseModels = false;
        }
    };

    const isOpenRouterProvider = () => {
        return state.provider?.key === 'openrouter';
    };

    const getSelectedBaseModelIdentifier = () => {
        const selectedModel = state.selectedBaseModel || {};

        // OpenRouter's `model` property is only the short slug, while its `id`
        // is the canonical author/slug value required by metadata endpoints.
        // Other integrations retain their established preference for `model`.
        const catalogIdentifier = isOpenRouterProvider()
            ? (selectedModel.id || selectedModel.model)
            : (selectedModel.model || selectedModel.id);
        return String(catalogIdentifier || state.selectedBaseModelId || '').trim();
    };

    const formatPrice = (price) => {
        if (price === null || price === undefined || price === '') return '—';
        const num = Number(price);
        if (Number.isNaN(num)) return '—';
        if (num === 0) return 'Free';
        const rounded = num.toFixed(3);
        const [intPart, fracPart = ''] = rounded.split('.');
        let displayedFraction = fracPart;
        if (displayedFraction.length === 3 && displayedFraction[2] === '0') {
            displayedFraction = displayedFraction.slice(0, 2);
        }
        const decimalPortion = displayedFraction ? `.${displayedFraction}` : '';
        return `$${intPart}${decimalPortion}`;
    };

    const formatContextLength = (length) => {
        if (!length) return '—';
        const num = parseInt(length, 10);
        if (isNaN(num)) return '—';
        if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
        if (num >= 1000) return `${(num / 1000).toFixed(0)}K`;
        return num.toString();
    };

    const getStatusBadgeClass = (status) => {
        const s = typeof status === 'string' ? status.trim().toLowerCase() : '';
        if (!s) return 'status-unknown';
        if (s === 'up' || s === 'online' || s === 'active') return 'status-up';
        if (s === 'down' || s === 'offline') return 'status-down';
        if (s === 'limited' || s === 'degraded') return 'status-limited';
        return 'status-unknown';
    };

    const formatUptimePercentage = (uptimeValue) => {
        if (uptimeValue === null || uptimeValue === undefined || uptimeValue === '') {
            return '—';
        }
        const parsed = Number(uptimeValue);
        if (Number.isNaN(parsed)) {
            return '—';
        }
        const normalized = parsed > 1 ? parsed : parsed * 100;
        const clamped = Math.min(Math.max(normalized, 0), 100);
        return `${clamped.toFixed(1)}%`;
    };

    const renderOpenRouterProvidersLoading = (message = 'Loading available providers…') => {
        const resolvedMessage = message || t('providers_loading', 'Loading providers...');
        if (!dom.openrouterProvider.list) return;
        dom.openrouterProvider.list.innerHTML = '';
        dom.openrouterProvider.list.appendChild(window.createAdminLoadingPlaceholder({
            message: resolvedMessage,
            className: '',
        }));
    };

    const clearOpenRouterProviderSelection = () => {
        state.selectedOpenRouterProvider = null;
        if (dom.openrouterProvider.next) {
            dom.openrouterProvider.next.disabled = true;
        }
        const cards = dom.openrouterProvider.list?.querySelectorAll('.openrouter-provider-card') || [];
        cards.forEach(card => card.classList.remove('selected'));
    };

    const selectOpenRouterProvider = (provider) => {
        state.selectedOpenRouterProvider = provider;
        updateOpenRouterNextButtonState();
        const cards = dom.openrouterProvider.list?.querySelectorAll('.openrouter-provider-card') || [];
        cards.forEach(card => {
            const isSelected = card.dataset.providerName === (provider.provider_name || provider.name);
            card.classList.toggle('selected', isSelected);
            card.setAttribute('aria-checked', isSelected ? 'true' : 'false');
        });
    };

    const updateOpenRouterNextButtonState = () => {
        if (!dom.openrouterProvider.next) return;
        if (state.openrouterProviderMode === 'specific') {
            dom.openrouterProvider.next.disabled = !state.selectedOpenRouterProvider;
        } else {
            dom.openrouterProvider.next.disabled = false;
        }
    };

    const setOpenRouterProviderMode = (mode) => {
        const normalized = ['specific', 'auto', 'sort'].includes(mode) ? mode : 'auto';
        state.openrouterProviderMode = normalized;

        if (dom.openrouterProvider.modeRadios) {
            dom.openrouterProvider.modeRadios.forEach((radio) => {
                radio.checked = radio.value === normalized;
                radio.closest('.openrouter-provider-mode-option')?.classList.toggle('selected', radio.checked);
            });
        }

        const showList = normalized === 'specific';
        toggleElementVisibility(dom.openrouterProvider.list, showList);
        toggleElementVisibility(dom.openrouterProvider.sortRow, normalized === 'sort');
        toggleElementVisibility(dom.openrouterProvider.autoHint, normalized === 'auto');

        if (normalized !== 'specific') {
            clearOpenRouterProviderSelection();
        }

        if (normalized === 'specific' && !state.openrouterProviders.length && !state.loadingOpenRouterProviders) {
            loadOpenRouterProviders();
        }

        updateOpenRouterNextButtonState();
    };

    const resetOpenRouterProviderStep = () => {
        state.openrouterProviders = [];
        state.selectedOpenRouterProvider = null;
        state.openrouterProviderMode = 'auto';
        state.openrouterProviderSort = dom.openrouterProvider.sortSelect?.value || 'price';
        if (dom.openrouterProvider.sortSelect) {
            dom.openrouterProvider.sortSelect.value = state.openrouterProviderSort;
        }
        toggleElementVisibility(dom.openrouterProvider.autoHint, true);
        toggleElementVisibility(dom.openrouterProvider.sortRow, false);
        toggleElementVisibility(dom.openrouterProvider.list, false);
        updateOpenRouterNextButtonState();
        setOpenRouterProviderMode('auto');
    };

    const renderOpenRouterProviders = (providers) => {
        if (!dom.openrouterProvider.list) return;
        
        if (!providers || !providers.length) {
            dom.openrouterProvider.list.innerHTML = `
                <div class="openrouter-providers-empty">
                    <p>${t('models_create_openrouter_no_providers', 'No providers available for this model.')}</p>
                    <p class="openrouter-providers-empty-hint">${t('models_create_openrouter_no_providers_hint', 'You can proceed without selecting a specific provider.')}</p>
                </div>
            `;
            if (dom.openrouterProvider.next) {
                dom.openrouterProvider.next.disabled = false;
            }
            return;
        }

        const statIcon = (type) => {
            const icons = {
                context: Icons.file,
                output: Icons.code_execution,
                uptime: Icons.uptime,
                input: Icons.cost,
                outputPrice: Icons.cost,
            };
            return icons[type] || '';
        };

        const fragment = document.createDocumentFragment();
        
        providers.forEach((provider, index) => {
            const card = document.createElement('div');
            card.className = 'openrouter-provider-card';
            card.dataset.providerName = provider.provider_name || provider.name || '';
            card.setAttribute('role', 'radio');
            card.setAttribute('aria-checked', 'false');
            card.setAttribute('tabindex', index === 0 ? '0' : '-1');
            
            const statusClass = getStatusBadgeClass(provider.status);
            const uptimePercent = formatUptimePercentage(provider.uptime_last_30m);
            const statusLabel = typeof provider.status === 'string' ? provider.status.trim() : '';
            const quantizationLabel = (() => {
                if (typeof provider.quantization !== 'string') return '';
                const trimmed = provider.quantization.trim();
                if (!trimmed) return '';
                return trimmed.toLowerCase() === 'unknown' ? '' : trimmed;
            })();

            const tags = [];
            if (quantizationLabel) tags.push(quantizationLabel);
            if (provider.tags && provider.tags.length) {
                provider.tags.slice(0, 3).forEach(tag => tags.push(tag));
            }

            const buildStats = () => {
                const parts = [];
                parts.push(`<span class="openrouter-provider-stat"><span class="openrouter-provider-stat-icon">${statIcon('context')}</span> <span class="openrouter-provider-stat-value">${formatContextLength(provider.context_length)}</span></span>`);
                parts.push(`<span class="openrouter-provider-stat-divider"></span>`);
                parts.push(`<span class="openrouter-provider-stat"><span class="openrouter-provider-stat-icon">${statIcon('output')}</span> <span class="openrouter-provider-stat-value">${formatContextLength(provider.max_completion_tokens)}</span></span>`);
                parts.push(`<span class="openrouter-provider-stat-divider"></span>`);
                parts.push(`<span class="openrouter-provider-stat"><span class="openrouter-provider-stat-icon">${statIcon('uptime')}</span> <span class="openrouter-provider-stat-value">${uptimePercent}</span></span>`);
                parts.push(`<span class="openrouter-provider-stat-divider"></span>`);
                parts.push(`<span class="openrouter-provider-stat"><span class="openrouter-provider-stat-icon">${statIcon('input')}</span> <span class="openrouter-provider-stat-value">${formatPrice(provider.pricing_prompt)}/M</span></span>`);
                parts.push(`<span class="openrouter-provider-stat-divider"></span>`);
                parts.push(`<span class="openrouter-provider-stat"><span class="openrouter-provider-stat-icon">${statIcon('outputPrice')}</span> <span class="openrouter-provider-stat-value">${formatPrice(provider.pricing_completion)}/M</span></span>`);
                return parts.join('');
            };

            card.innerHTML = `
                <div class="openrouter-provider-card-info">
                    <div class="openrouter-provider-card-header">
                        <span class="openrouter-provider-card-name">${provider.provider_name || provider.name || t('common_unknown', 'Unknown')}</span>
                        ${statusLabel ? `<span class="openrouter-provider-status-badge ${statusClass}">${statusLabel}</span>` : ''}
                    </div>
                    <div class="openrouter-provider-card-stats">
                        ${buildStats()}
                    </div>
                    ${tags.length ? `<div class="openrouter-provider-tags">${tags.map(t => `<span class="openrouter-provider-tag">${t}</span>`).join('')}</div>` : ''}
                </div>
                <div class="openrouter-provider-card-check">
                    ${Icons.check}
                </div>
            `;
            
            card.addEventListener('click', () => selectOpenRouterProvider(provider));
            card.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    selectOpenRouterProvider(provider);
                }
            });
            
            fragment.appendChild(card);
        });
        
        dom.openrouterProvider.list.innerHTML = '';
        dom.openrouterProvider.list.appendChild(fragment);
    };

    const loadOpenRouterProviders = async () => {
        if (!state.provider?.id || !state.selectedBaseModel) {
            notifyWarn(t('models_create_select_base_model_first', 'Select a base model first.'));
            return;
        }
        
        state.loadingOpenRouterProviders = true;
        state.openrouterProviders = [];
        state.selectedOpenRouterProvider = null;
        
        if (dom.openrouterProvider.next) {
            dom.openrouterProvider.next.disabled = true;
        }
        
        renderOpenRouterProvidersLoading();
        
        const modelName = state.selectedBaseModel.id || state.selectedBaseModel.model || state.selectedBaseModelId;
        
        if (dom.openrouterProvider.title) {
            dom.openrouterProvider.title.textContent = formatT('models_create_openrouter_title', 'Select Provider for {name}', {
                name: state.selectedBaseModel.name || modelName,
            });
        }
        if (dom.openrouterProvider.subtitle) {
            dom.openrouterProvider.subtitle.textContent = t('models_create_openrouter_subtitle', 'Choose which provider endpoint to use. Different providers may have varying pricing, context limits, and availability.');
        }
        
        try {
            const response = await modelsApi.fetchOpenRouterModelProviders(state.provider.id, modelName);
            state.openrouterProviders = response?.providers || [];
            renderOpenRouterProviders(state.openrouterProviders);
        } catch (error) {
            notifyErr(error?.message || t('models_create_openrouter_load_failed', 'Failed to load OpenRouter model providers.'));
            renderOpenRouterProvidersLoading(t('models_create_openrouter_load_failed_hint', 'Unable to load providers. You can proceed without selecting one.'));
            updateOpenRouterNextButtonState();
        } finally {
            state.loadingOpenRouterProviders = false;
        }
    };

    const handleOpenRouterProviderNext = async () => {
        await prepareStepThree({ allowMissingBaseModel: false });
    };

    const highlightFirstResult = () => {
        const first = dom.base.list?.querySelector('.model-base-select-item');
        if (first) {
            first.focus();
        }
    };

    const handleBaseSearchInput = () => {
        renderBaseModels(state.baseModels);
    };

    const handleBaseToggleClick = (event) => {
        event?.preventDefault?.();
        if (baseDropdownOpen) {
            closeBaseDropdown();
        } else {
            openBaseDropdown();
            highlightFirstResult();
        }
    };

    const handleBaseKeydown = (event) => {
        if (!baseDropdownOpen) {
            return;
        }
        const items = Array.from(dom.base.list?.querySelectorAll('.model-base-select-item') || []);
        if (!items.length) {
            return;
        }
        const currentIndex = items.findIndex((el) => el === document.activeElement);
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            const nextIndex = currentIndex < 0 ? 0 : Math.min(items.length - 1, currentIndex + 1);
            items[nextIndex].focus();
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            const prevIndex = currentIndex < 0 ? items.length - 1 : Math.max(0, currentIndex - 1);
            items[prevIndex].focus();
        } else if (event.key === 'Enter' && document.activeElement?.classList.contains('model-base-select-item')) {
            event.preventDefault();
            const modelId = document.activeElement.dataset.modelId;
            const model = state.baseModels.find((m) => (m.id || m.model) === modelId);
            if (model) {
                selectBaseModel(model);
            }
        }
    };

    const parseMultiLineInput = (value) => {
        return (value || '')
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter((line) => line.length > 0);
    };

    const buildAccessPayload = (accessValues) => {
        const access = (typeof accessValues === 'object' && accessValues !== null) ? accessValues : {};
        const normalizeList = (value) => {
            if (Array.isArray(value)) {
                return value.map((entry) => String(entry)).filter((entry) => entry.length > 0);
            }
            if (typeof value === 'string') {
                return parseMultiLineInput(value);
            }
            return [];
        };
        return {
            everyone: Boolean(access.everyone),
            users: normalizeList(access.users),
            groups: normalizeList(access.groups),
        };
    };

    const isStopSequencesField = (field) => {
        const segments = (field?.key || '').split('.');
        return segments[segments.length - 1] === 'stop_sequences';
    };

    const coerceValueByInputType = (field, value) => {
        if (value == null) {
            return value;
        }
        if (typeof value !== 'string') {
            return value;
        }
        const trimmed = value.trim();
        if (!trimmed) {
            return null;
        }
        const inputType = (field?.input_type || '').toLowerCase();
        if (inputType === 'int' || inputType === 'integer') {
            const parsed = Number.parseInt(trimmed, 10);
            return Number.isNaN(parsed) ? null : parsed;
        }
        if (inputType === 'float' || inputType === 'number') {
            const parsed = Number.parseFloat(trimmed);
            return Number.isNaN(parsed) ? null : parsed;
        }
        return value;
    };

    const normalizeSchemaFieldValue = (field, value) => {
        if (isStopSequencesField(field)) {
            if (Array.isArray(value)) {
                return value;
            }
            return parseMultiLineInput(value);
        }
        const coerced = coerceValueByInputType(field, value);
        if (typeof coerced === 'string' && coerced.trim() === '') {
            return null;
        }
        return coerced;
    };

    const setNestedValue = (target, segments, value) => {
        if (!segments.length) return;
        if (value === null || value === undefined) {
            return;
        }
        const [first, ...rest] = segments;
        if (!rest.length) {
            target[first] = value;
            return;
        }
        if (typeof target[first] !== 'object' || target[first] === null) {
            target[first] = {};
        }
        setNestedValue(target[first], rest, value);
    };

    const ensureArray = (value) => (Array.isArray(value) ? value : []);

    const resolveSchemaOptionLabel = (option = {}) => (
        typeof window.resolveAdminSchemaOptionLabel === 'function'
            ? window.resolveAdminSchemaOptionLabel(option, t)
            : (option.i18n_label ? t(option.i18n_label, option.label || option.value || option.id || '') : (option.label || option.value || option.id || ''))
    );

    /**
     * Proxy to shared initializeAdminSingleSelect helper to avoid duplication.
     */
    const initializeAdminSingleSelect = (select, field) => {
        if (typeof window.initializeAdminSingleSelect === 'function') {
            return window.initializeAdminSingleSelect(select, field);
        }
        throw new Error('initializeAdminSingleSelect helper is not available');
    };

    /**
     * Proxy to the shared searchable admin multi-select helper.
     */
    const initializeAdminMultiSelect = (select, field) => {
        if (typeof window.initializeAdminMultiSelect === 'function') {
            return window.initializeAdminMultiSelect(select, field);
        }
        throw new Error('initializeAdminMultiSelect helper is not available');
    };

    const coerceAttributeLength = (value) => {
        if (value === null || value === undefined || value === '') {
            return null;
        }
        const numeric = Number(value);
        return Number.isFinite(numeric) && numeric >= 0 ? numeric : null;
    };

    const applyFieldAttributesToControl = (control, field) => {
        if (!control || !field?.attributes) {
            return;
        }
        const { attributes } = field;
        const hasMin = attributes.min !== undefined && attributes.min !== null && attributes.min !== '';
        const hasMax = attributes.max !== undefined && attributes.max !== null && attributes.max !== '';

        const tagName = control.tagName?.toLowerCase();
        const inputType = control.type;

        const isNumberInput = tagName === 'input' && inputType === 'number';
        if (isNumberInput) {
            if (hasMin) {
                control.min = attributes.min;
            }
            if (hasMax) {
                control.max = attributes.max;
            }
            if (attributes.step !== undefined && attributes.step !== null && attributes.step !== '') {
                control.step = attributes.step;
            }
            return;
        }

        const isTextInput = tagName === 'textarea' || (tagName === 'input' && inputType !== 'number');
        if (!isTextInput) {
            return;
        }
        if (hasMin) {
            const minLength = coerceAttributeLength(attributes.min);
            if (minLength !== null) {
                control.minLength = minLength;
            }
        }
        if (hasMax) {
            const maxLength = coerceAttributeLength(attributes.max);
            if (maxLength !== null) {
                control.maxLength = maxLength;
            }
        }
    };

    const createSchemaControl = (field, value) => {
        const { row, controlWrapper } = typeof createFieldLayout === 'function'
            ? createFieldLayout(field)
            : { row: document.createElement('div'), controlWrapper: document.createElement('div') };
        row.classList.add('settings-row');
        controlWrapper.classList.add('settings-row-control');

        let control;

        // Check if this is a model_icon field and use the icon picker
        if (window.IconPicker && window.IconPicker.shouldUseIconPicker(field)) {
            const { row: iconRow, control: iconControl } = window.IconPicker.createIconPickerControl(field, value);
            return { row: iconRow, control: iconControl };
        }

        switch (field.type) {
            case 'boolean':
            case 'toggle': {
                const label = document.createElement('label');
                label.className = 'toggle-switch';
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.className = 'toggle-input';
                checkbox.checked = Boolean(value ?? field.default);
                const slider = document.createElement('span');
                slider.className = 'toggle-slider';
                label.append(checkbox, slider);
                control = checkbox;
                controlWrapper.appendChild(label);
                break;
            }
            case 'select': {
                const select = document.createElement('select');
                select.className = 'select';
                if (field.multiple) {
                    select.multiple = true;
                }

                const rawSelected = value ?? field.default;
                const selectedValues = field.multiple
                    ? new Set(
                        Array.isArray(rawSelected)
                            ? rawSelected.map((val) => String(val))
                            : rawSelected != null
                                ? [String(rawSelected)]
                                : []
                    )
                    : null;

                // Check if this is a websearch provider field
                const isWebsearchField = window.WebsearchProviderLogic?.isWebsearchProviderField(field);
                const rawOptions = ensureArray(field.options);
                const options = isWebsearchField && typeof window.WebsearchProviderLogic?.sortedProviderOptions === 'function'
                    ? window.WebsearchProviderLogic.sortedProviderOptions(rawOptions)
                    : rawOptions;

                options.forEach((option) => {
                    const opt = document.createElement('option');
                    opt.value = option.value;
                    const optionLabel = resolveSchemaOptionLabel(option);
                    
                    // Store metadata for websearch provider options
                    if (isWebsearchField && option.metadata) {
                        opt.dataset.metadata = JSON.stringify(option.metadata);
                        // Add visual indicator for combined providers
                        if (option.metadata.has_combined) {
                            opt.textContent = formatT('websearch_provider_combined_suffix', '{label} (combined)', { label: optionLabel });
                        } else {
                            opt.textContent = optionLabel;
                        }
                    } else {
                        opt.textContent = optionLabel;
                    }
                    
                    if (field.multiple && selectedValues) {
                        opt.selected = selectedValues.has(String(option.value));
                    }
                    select.appendChild(opt);
                });

                if (!field.multiple) {
                    const selectedValue = rawSelected;
                    if (selectedValue !== undefined && selectedValue !== null) {
                        select.value = selectedValue;
                    }
                }

                control = select;
                if (field.multiple) {
                    const multiSelectMeta = initializeAdminMultiSelect(select, field);
                    select._multiSelect = multiSelectMeta;
                    controlWrapper.appendChild(multiSelectMeta.wrapper);
                } else {
                    const singleSelectMeta = initializeAdminSingleSelect(select, field);
                    select._singleSelect = singleSelectMeta;
                    controlWrapper.appendChild(singleSelectMeta.wrapper);
                }
                break;
            }
            case 'number': {
                const input = document.createElement('input');
                input.type = 'number';
                input.className = 'input';
                if (field.attributes?.min !== undefined) input.min = field.attributes.min;
                if (field.attributes?.max !== undefined) input.max = field.attributes.max;
                input.value = value ?? field.default ?? '';
                control = input;
                controlWrapper.appendChild(input);
                break;
            }
            case 'string_list': {
                const textarea = document.createElement('textarea');
                textarea.className = 'input';
                textarea.rows = 3;
                textarea.value = Array.isArray(value) ? value.join('\n') : value ?? field.default ?? '';
                control = textarea;
                controlWrapper.appendChild(textarea);
                break;
            }
            case 'input':
            case 'string':
            default: {
                const input = document.createElement(field.input_type === 'textarea' ? 'textarea' : 'input');
                if (field.input_type && field.input_type !== 'textarea') {
                    input.type = field.input_type;
                } else if (!field.input_type || field.input_type === 'textarea') {
                    input.type = 'text';
                }
                input.className = 'input';
                input.value = value ?? field.default ?? '';
                control = input;
                controlWrapper.appendChild(input);
                break;
            }
        }

        if (control) {
            const placeholder = getFieldPlaceholder(field);
            if (placeholder) {
                control.placeholder = placeholder;
            }
        }

        applyFieldAttributesToControl(control, field);

        return { row, control };
    };

    /**
     * Check if a dependency field exists in the schema controls.
     */
    const dependencyFieldExists = (dependencyKey) => {
        if (!dependencyKey) return false;
        return state.schemaControls.some(({ field }) => field.key === dependencyKey);
    };

    /**
     * Get the current value of a field by its key.
     */
    const getFieldValue = (fieldKey) => {
        const entry = state.schemaControls.find(({ field }) => field.key === fieldKey);
        if (!entry) return undefined;
        const { field, control } = entry;
        if (!control) return undefined;
        switch (field.type) {
            case 'boolean':
            case 'toggle':
                return Boolean(control.checked);
            case 'select':
                if (field.multiple) {
                    return Array.from(control.selectedOptions || []).map((opt) => opt.value);
                }
                return control.value;
            case 'number':
                return control.value === '' ? null : Number(control.value);
            default:
                return control.value;
        }
    };

    const isSingleDependencySatisfied = (dependencyKey, requiredValue) => {
        if (!dependencyKey) return true;
        if (!dependencyFieldExists(dependencyKey)) return true;
        const currentValue = getFieldValue(dependencyKey);
        if (window.SchemaDependencyUtils?.matchesDependencyValue) {
            return window.SchemaDependencyUtils.matchesDependencyValue(currentValue, requiredValue);
        }

        if (Array.isArray(requiredValue)) {
            const normalizedRequiredValues = requiredValue.map((value) => String(value));
            if (Array.isArray(currentValue)) {
                return normalizedRequiredValues.some((value) => currentValue.includes(value));
            }
            return normalizedRequiredValues.includes(String(currentValue));
        }

        if (Array.isArray(currentValue)) {
            return currentValue.includes(String(requiredValue));
        }
        if (typeof requiredValue === 'boolean') {
            return currentValue === requiredValue;
        }
        return String(currentValue) === String(requiredValue);
    };

    /**
     * Check if a field's dependency condition is satisfied.
     * Returns true if the field should be visible.
     */
    const isDependencySatisfied = (field) => {
        const firstSatisfied = isSingleDependencySatisfied(field.dependency, field.dependency_value);
        if (!firstSatisfied) {
            return false;
        }
        return isSingleDependencySatisfied(field.dependency2, field.dependency2_value);
    };

    /**
     * Update visibility of all dependent fields.
     */
    const refreshWebsearchCombinedState = () => {
        if (!window.WebsearchProviderLogic?.refreshScrapeFieldState) {
            return;
        }
        if (!Array.isArray(state.schemaControls) || !state.schemaControls.length) {
            return;
        }
        window.WebsearchProviderLogic.refreshScrapeFieldState(state.schemaControls);
    };

    const updateDependentFieldsVisibility = () => {
        state.schemaControls.forEach(({ field, control }) => {
            if (!field.dependency && !field.dependency2) return;
            const row = control?.closest?.('.settings-row');
            if (!row) return;
            const visible = isDependencySatisfied(field);
            row.hidden = !visible;
            row.style.display = visible ? '' : 'none';
        });
        refreshWebsearchCombinedState();
        window.syncSchemaSectionVisibility?.(dom.form.schemaFields);
        window.syncSectionBodyLastVisibleRow?.(dom.form.schemaFields);
    };

    /**
     * Attach change listeners to all controls that might be dependencies.
     */
    const attachDependencyListeners = () => {
        // Collect all dependency keys
        const dependencyKeys = new Set();
        state.schemaControls.forEach(({ field }) => {
            if (field.dependency) {
                dependencyKeys.add(field.dependency);
            }
            if (field.dependency2) {
                dependencyKeys.add(field.dependency2);
            }
        });
        // Attach listeners to controls that are dependencies
        state.schemaControls.forEach(({ field, control }) => {
            if (!dependencyKeys.has(field.key)) return;
            if (!control) return;
            control.addEventListener('change', updateDependentFieldsVisibility);
        });
    };

    const normalizeSchemaSections = (schema) => {
        if (!schema) {
            return [];
        }

        if (Array.isArray(schema.sections)) {
            return schema.sections
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
        }

        if (Array.isArray(schema.fields)) {
            return [
                {
                    title: schema.title ?? null,
                    description: schema.description ?? null,
                    i18n_title: schema.i18n_title ?? null,
                    i18n_description: schema.i18n_description ?? null,
                    fields: schema.fields.filter(Boolean),
                },
            ];
        }

        if (Array.isArray(schema)) {
            return [
                {
                    title: null,
                    description: null,
                    i18n_title: null,
                    i18n_description: null,
                    fields: schema.filter(Boolean),
                },
            ];
        }

        return [];
    };

    const loadSchemaSections = async (options = {}) => {
        if (!state.provider?.key || !state.provider?.id) {
            return [];
        }
        const { skipModelName = false } = options;
        const selectedModel = state.selectedBaseModel || {};
        const selectedModelName = selectedModel.id || selectedModel.model || state.selectedBaseModelId || null;
        const schemaParams = {
            modelName: skipModelName ? null : selectedModelName,
        };
        if (isOpenRouterProvider() && state.selectedOpenRouterProvider) {
            const providerName = state.selectedOpenRouterProvider.provider_name || state.selectedOpenRouterProvider.name;
            if (providerName) {
                schemaParams.modelProvider = providerName;
            }
        }
        try {
            const schema = await modelsApi.fetchProviderModelSchema(
                state.provider.key,
                state.provider.id,
                schemaParams,
            );
            return normalizeSchemaSections(schema);
        } catch (error) {
            notifyErr(error?.message || t('models_schema_load_failed', 'Failed to load model schema.'));
            return [];
        }
    };

    const renderSchema = (sections) => {
        if (!dom.form.schemaFields) return;
        dom.form.schemaFields.innerHTML = '';
        state.schemaControls = [];
        if (!sections.length) {
            dom.form.schemaFields.innerHTML = `<p class="provider-form-empty">${t('models_schema_empty', 'This provider does not require extra model settings.')}</p>`;
            return;
        }
        const fragment = document.createDocumentFragment();
        sections.forEach((section) => {
            const sectionEl = document.createElement('section');
            sectionEl.classList.add('settings-section');

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
                    const descEl = document.createElement('p');
                    descEl.classList.add('settings-section-description');
                    descEl.textContent = (section.i18n_description && typeof window.getTranslation === 'function')
                        ? window.getTranslation(section.i18n_description, section.description)
                        : section.description;
                    headerEl.appendChild(descEl);
                }

                sectionEl.appendChild(headerEl);
            }

            const bodyEl = document.createElement('div');
            bodyEl.classList.add('settings-section-body');

            section.fields.forEach((field) => {
                const initialValue = field.value !== undefined ? field.value : field.default;
                const { row, control } = createSchemaControl(field, initialValue);
                row.dataset.fieldKey = field.key;
                bodyEl.appendChild(row);
                state.schemaControls.push({ field, control });
            });

            sectionEl.appendChild(bodyEl);
            fragment.appendChild(sectionEl);
        });
        dom.form.schemaFields.appendChild(fragment);
        // Set up dependency handling
        attachDependencyListeners();
        updateDependentFieldsVisibility();
        // Attach listeners to clear errors on input
        attachErrorClearListeners();
        // Set up websearch provider combined logic
        if (window.WebsearchProviderLogic) {
            window.WebsearchProviderLogic.attachWebsearchProviderLogic(state.schemaControls);
        }
    };

    const collectSchemaValues = () => {
        const data = {};
        state.schemaControls.forEach(({ field, control }) => {
            if (!control) return;
            // Skip hidden fields (dependency not satisfied)
            const row = control.closest?.('.settings-row');
            if (row && row.hidden) return;
            let value;
            switch (field.type) {
                case 'boolean':
                case 'toggle':
                    value = Boolean(control.checked);
                    break;
                case 'select':
                    if (field.multiple) {
                        value = Array.from(control.selectedOptions || []).map((option) => option.value);
                        break;
                    }
                    value = control.value;
                    break;
                case 'number':
                    value = control.value === '' ? null : Number(control.value);
                    break;
                case 'string_list':
                    value = parseMultiLineInput(control.value);
                    break;
                case 'string':
                case 'input':
                default:
                    value = control.value;
            }
            const segments = (field.key || '').split('.').filter(Boolean);
            if (!segments.length) return;
            value = normalizeSchemaFieldValue(field, value);
            const normalizedSegments = segments[0] === 'settings' ? segments.slice(1) : segments;
            if (!normalizedSegments.length) {
                if (value && typeof value === 'object' && !Array.isArray(value)) {
                    Object.assign(data, value);
                }
                return;
            }
            setNestedValue(data, normalizedSegments, value);
        });
        return data;
    };

    const getStepThreeSnapshot = () => JSON.stringify({
        modelId: String(dom.form.modelId?.value || '').trim(),
        name: String(dom.form.name?.value || '').trim(),
        description: String(dom.form.description?.value || '').trim(),
        status: String(dom.form.status?.value || 'normal'),
        schema: collectSchemaValues(),
    });

    const rememberStepThreeSnapshot = () => {
        state.step3Snapshot = getStepThreeSnapshot();
    };

    const hasUnsavedStepThreeChanges = () => {
        if (state.view !== 'step3' || !state.step3Snapshot) {
            return false;
        }
        return getStepThreeSnapshot() !== state.step3Snapshot;
    };

    const requestUnsavedStepThreeConfirmation = (onConfirm) => {
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

    const splitSchemaValuesByTools = (values) => {
        if (!values || typeof values !== 'object') {
            return { settings: {}, tools: [] };
        }
        const { tools, ...settings } = values;
        const normalizedTools = Array.isArray(tools)
            ? tools.filter((tool) => typeof tool === 'string').map((tool) => tool.trim()).filter(Boolean)
            : typeof tools === 'string'
                ? parseMultiLineInput(tools)
                : [];
        return { settings, tools: normalizedTools };
    };

    /**
     * Check if a field value is empty (for required field validation).
     */
    const isFieldValueEmpty = (field, control) => {
        if (!control) return true;
        switch (field.type) {
            case 'boolean':
            case 'toggle':
                return false; // Booleans are never empty
            case 'select':
                return !control.value;
            case 'number':
                return control.value === '';
            case 'string_list':
                return control.value?.trim() === '';
            default:
                return control.value?.trim() === '';
        }
    };

    /**
     * Add error state to a field row.
     */
    const setFieldError = (row, message = t('admin_field_required', 'This field is required')) => {
        if (!row) return;
        row.classList.add('has-error');
        // Add error message if not already present
        let errorEl = row.querySelector('.field-error-message');
        if (!errorEl) {
            errorEl = document.createElement('p');
            errorEl.className = 'field-error-message';
            const controlWrapper = row.querySelector('.settings-row-control');
            if (controlWrapper) {
                controlWrapper.appendChild(errorEl);
            } else {
                row.appendChild(errorEl);
            }
        }
        errorEl.textContent = message;
        // Trigger shake animation
        row.classList.remove('shake-error');
        void row.offsetWidth; // Force reflow
        row.classList.add('shake-error');
    };

    /**
     * Clear error state from a field row.
     */
    const clearFieldError = (row) => {
        if (!row) return;
        row.classList.remove('has-error', 'shake-error');
        const errorEl = row.querySelector('.field-error-message');
        if (errorEl) {
            errorEl.remove();
        }
    };

    /**
     * Validate all required schema fields.
     * Returns array of invalid field rows.
     */
    const validateRequiredSchemaFields = () => {
        const invalidRows = [];
        state.schemaControls.forEach(({ field, control }) => {
            if (!field || !control) return;
            const row = control.closest?.('.settings-row');
            if (!row) return;
            // Skip hidden fields (dependency not satisfied)
            if (row.hidden) return;
            // Clear any previous error
            clearFieldError(row);
            // Check if field is required and empty
            if (field.required && isFieldValueEmpty(field, control)) {
                const label = field.label || field.key || t('admin_this_field', 'This field');
                setFieldError(row, formatT('admin_field_required_named', '{field} is required', { field: label }));
                invalidRows.push(row);
            }
        });
        return invalidRows;
    };

    /**
     * Scroll to the first invalid field with smooth animation.
     */
    const scrollToFirstInvalidField = (invalidRows) => {
        if (!invalidRows.length) return;
        const firstRow = invalidRows[0];
        firstRow.scrollIntoView({
            behavior: 'smooth',
            block: 'center',
        });
        // Focus the input if possible
        const control = firstRow.querySelector('input, select, textarea');
        if (control) {
            setTimeout(() => control.focus(), 300);
        }
    };

    /**
     * Attach input listeners to clear errors on user input.
     */
    const attachErrorClearListeners = () => {
        state.schemaControls.forEach(({ field, control }) => {
            if (!control) return;
            const row = control.closest?.('.settings-row');
            if (!row) return;
            const clearOnInput = () => {
                if (row.classList.contains('has-error')) {
                    clearFieldError(row);
                }
            };
            control.addEventListener('input', clearOnInput);
            control.addEventListener('change', clearOnInput);
        });
    };

    const prepareStepThree = async (options = {}) => {
        const { allowMissingBaseModel = false } = options;
        if (!state.provider || (!state.selectedBaseModel && !allowMissingBaseModel)) {
            notifyWarn(t('models_create_select_provider_model_first', 'Select provider and model first.'));
            showPage('step2');
            return;
        }
        resetFormValues();
        if (dom.form.title) {
            const modelTitle = state.selectedBaseModel
                ? (state.selectedBaseModel.name || state.selectedBaseModel.model || t('common_model', 'model'))
                : t('common_model', 'model');
            dom.form.title.textContent = formatT('models_create_configure_title', 'Configure {name}', { name: modelTitle });
        }
        if (dom.form.subtitle) {
            dom.form.subtitle.textContent = formatT('models_edit_form_subtitle', 'Provider: {provider}', { provider: state.provider.name });
        }
        if (dom.form.name) {
            dom.form.name.value = state.selectedBaseModel?.name || '';
        }
        if (dom.form.modelId) {
            dom.form.modelId.value = getSelectedBaseModelIdentifier();
        }
        if (dom.form.description) {
            dom.form.description.value = normalizeModelDescription(state.selectedBaseModel?.description || '');
        }
        if (dom.form.status) {
            dom.form.status.value = 'normal';
        }

        const schemaSections = await loadSchemaSections({ skipModelName: !state.selectedBaseModel });
        renderSchema(schemaSections || []);
        rememberStepThreeSnapshot();
        showPage('step3');
    };

    const handleSubmit = async (event) => {
        event?.preventDefault?.();
        if (!state.provider) {
            notifyWarn(t('models_create_select_provider_first_warning', 'Select a provider first.'));
            showPage('step1');
            return;
        }
        if (!state.selectedBaseModel && !state.skippingBaseModelStep) {
            notifyWarn(t('models_create_select_base_model_retry', 'Select a base model or retry fetching models.'));
            showPage('step2');
            return;
        }

        // Validate required schema fields first
        const invalidRows = validateRequiredSchemaFields();
        if (invalidRows.length > 0) {
            const fieldCount = invalidRows.length;
            notifyErr(formatT('admin_required_fields_count', 'Please fill in {count} required field(s).', { count: fieldCount }));
            scrollToFirstInvalidField(invalidRows);
            return;
        }

        const schemaValues = collectSchemaValues();
        const schemaModelId = typeof schemaValues.model_name === 'string' ? schemaValues.model_name.trim() : '';
        const modelIdInput = dom.form.modelId?.value?.trim();
        const selectedModelId = getSelectedBaseModelIdentifier();
        const modelId = modelIdInput || schemaModelId || selectedModelId;
        if (!modelId) {
            notifyErr(t('models_create_model_id_required', 'Model ID is required.'));
            (dom.form.modelId || document.querySelector('[name="model_name"]'))?.focus?.();
            return;
        }
        const schemaName = typeof schemaValues.name === 'string' ? schemaValues.name.trim() : '';
        const nameInput = dom.form.name?.value?.trim();
        const name = nameInput || schemaName;
        if (!name) {
            notifyErr(t('models_create_model_name_required', 'Model name is required.'));
            (dom.form.name || document.querySelector('[name="name"]'))?.focus?.();
            return;
        }
        const schemaDescription = typeof schemaValues.description === 'string' ? schemaValues.description.trim() : '';
        const descriptionInput = dom.form.description?.value?.trim();
        const description = normalizeModelDescription(descriptionInput || schemaDescription);
        // Extract model_icon from schema values before splitting (icon picker provides this)
        const rawModelIcon = schemaValues.model_icon || '';
        const modelIcon = window.IconPicker?.sanitizeIconValue
            ? window.IconPicker.sanitizeIconValue(rawModelIcon)
            : rawModelIcon;
        const accessPayload = buildAccessPayload(schemaValues.access);
        delete schemaValues.access;
        delete schemaValues.model_icon;
        delete schemaValues.model_name;
        delete schemaValues.name;
        delete schemaValues.description;
        delete schemaValues.status;
        const { settings: schemaSettings, tools: schemaTools } = splitSchemaValuesByTools(schemaValues);
        
        // Process websearch provider values for combined providers
        let finalSettings = schemaSettings;
        if (window.WebsearchProviderLogic) {
            finalSettings = window.WebsearchProviderLogic.processWebsearchValuesForSubmit(
                schemaSettings,
                state.schemaControls
            );
        }
        
        if (!finalSettings || typeof finalSettings !== 'object') {
            finalSettings = {};
        }

        if (isOpenRouterProvider()) {
            const selectedMode = state.openrouterProviderMode || 'auto';
            const normalizedMode = ['specific', 'auto', 'sort'].includes(selectedMode) ? selectedMode : 'auto';

            const ensureProviderSelection = () => {
                const providerName = state.selectedOpenRouterProvider?.provider_name
                    || state.selectedOpenRouterProvider?.name
                    || state.selectedOpenRouterProvider?.model_name
                    || finalSettings.only_provider;
                return typeof providerName === 'string' ? providerName.trim() : '';
            };

            if (normalizedMode === 'specific') {
                const providerName = ensureProviderSelection();
                if (!providerName) {
                    notifyErr(t('models_create_select_provider_before_continue', 'Select a provider before continuing.'));
                    if (!state.skippingBaseModelStep) {
                        showPage('stepOpenRouterProvider');
                    }
                    setButtonLoadingState(dom.form.submit, false);
                    return;
                }
                finalSettings.provider_mode = 'specific';
                finalSettings.only_provider = providerName;
                delete finalSettings.provider_sort;
            } else if (normalizedMode === 'auto') {
                finalSettings.provider_mode = 'auto';
                delete finalSettings.only_provider;
                delete finalSettings.provider_sort;
            } else if (normalizedMode === 'sort') {
                const allowedSortOptions = ['price', 'throughput', 'latency'];
                const sortValue = allowedSortOptions.includes(state.openrouterProviderSort)
                    ? state.openrouterProviderSort
                    : 'price';
                finalSettings.provider_mode = 'sort';
                finalSettings.provider_sort = sortValue;
                delete finalSettings.only_provider;
            }
        }

        const payload = {
            provider: state.provider.key,
            provider_id: state.provider.id,
            model: {
                model: modelId,
                name,
                description: description || '',
                model_icon: modelIcon,
                tools: schemaTools,
                status: dom.form.status?.value || 'normal',
            },
            settings: finalSettings,
            access: accessPayload,
        };
        
        // Validate websearch providers if web_search tool is enabled
        if (window.WebsearchProviderLogic?.validateWebsearchProviders) {
            const validation = window.WebsearchProviderLogic.validateWebsearchProviders({
                tools: schemaTools,
                settings: finalSettings,
                schemaControls: state.schemaControls,
            });
            if (!validation.valid) {
                notifyErr(validation.error);
                return;
            }
        }

        try {
            setButtonLoadingState(dom.form.submit, true, t('admin_creating_ellipsis', 'Creating...'));
            await modelsApi.createModel(payload);
            notifyInfo(formatT('models_create_success_named', '{name} created successfully.', { name }));
            goToList();
            window.initModelsPage?.({ reloadSchema: true });
        } catch (error) {
            notifyErr(error?.message || t('models_create_failed', 'Failed to create model.'));
        } finally {
            setButtonLoadingState(dom.form.submit, false);
        }
    };

    const startFlow = () => {
        window.activateAdminPage?.('models', { history: 'none' });
        showPage('step1');
        applyProviderSortSelectLabels();
        upgradeProviderSortSelect();
        state.providerSearchTerm = '';
        if (dom.providerSearch) {
            dom.providerSearch.value = '';
        }
        updateProviderSearchClearVisibility();
        loadProviders();
    };

    /**
     * Check if any overlay/modal/dropdown is currently open that should consume ESC.
     */
    const isOverlayOpen = () => {
        // Check for open base dropdown
        if (baseDropdownOpen) return true;
        // Check for open multi-selects
        if (document.querySelector('.admin-multiselect.open')) return true;
        // Check for open single-selects
        if (document.querySelector('.admin-select.open')) return true;
        // Check for open icon picker
        if (document.querySelector('.icon-picker-dropdown:not([hidden])')) return true;
        // Check for open modals/overlays
        if (document.querySelector('.overlay-container.visible, .modal-overlay.visible, .overlay:not([hidden])')) return true;
        return false;
    };

    /**
     * Handle global keyboard navigation for the create flow.
     */
    const handleCreateKeydown = (event) => {
        // Only handle Escape key
        if (event.key !== 'Escape') return;
        
        // Don't navigate if an overlay/dropdown is open (let it handle ESC)
        if (isOverlayOpen()) return;
        
        // Don't navigate if focus is in a text input that has content (allow clearing)
        const activeEl = document.activeElement;
        if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA')) {
            if (activeEl.value && activeEl.value.trim()) {
                // If input has content, first ESC clears/blurs, don't navigate
                activeEl.blur();
                return;
            }
        }
        
        event.preventDefault();
        event.stopPropagation();
        
        switch (state.view) {
            case 'step3':
                requestUnsavedStepThreeConfirmation(() => {
                    if (isOpenRouterProvider() && !state.skippingBaseModelStep) {
                        showPage('stepOpenRouterProvider');
                    } else {
                        showPage('step2');
                    }
                });
                break;
            case 'stepOpenRouterProvider':
                showPage('step2');
                break;
            case 'step2':
                showPage('step1');
                break;
            case 'step1':
                goToList();
                window.initModelsPage?.();
                break;
            default:
                break;
        }
    };

    const bindEvents = () => {
        if (!providerSortI18nBound) {
            document.addEventListener('i18n:updated', refreshProviderSortSelectTranslations);
            providerSortI18nBound = true;
        }
        registerUnsavedGuard();
        dom.providerBack?.addEventListener('click', () => {
            goToList();
            window.initModelsPage?.();
        });

        dom.providerSort?.addEventListener('change', (event) => {
            state.providerSort = normalizeProviderSort(event.target.value);
            saveProviderSort(state.providerSort);
            renderProviderCards(state.providers, state.providerGroups);
        });

        dom.providerSearch?.addEventListener('input', (event) => {
            state.providerSearchTerm = (event.target.value || '').trim();
            updateProviderSearchClearVisibility();
            renderProviderCards(state.providers, state.providerGroups);
        });

        dom.providerSearchClear?.addEventListener('click', () => {
            state.providerSearchTerm = '';
            if (dom.providerSearch) {
                dom.providerSearch.value = '';
                dom.providerSearch.focus();
            }
            updateProviderSearchClearVisibility();
            renderProviderCards(state.providers, state.providerGroups);
        });

        dom.base.back?.addEventListener('click', () => {
            showPage('step1');
        });
        dom.base.next?.addEventListener('click', async () => {
            if (!state.selectedBaseModelId && !state.skippingBaseModelStep) {
                notifyWarn(t('models_create_select_model_first', 'Select a model first.'));
                return;
            }
            if (isOpenRouterProvider() && !state.skippingBaseModelStep) {
                resetOpenRouterProviderStep();
                showPage('stepOpenRouterProvider');
                if (state.openrouterProviderMode === 'specific') {
                    await loadOpenRouterProviders();
                }
            } else {
                prepareStepThree({ allowMissingBaseModel: state.skippingBaseModelStep && !state.selectedBaseModelId });
            }
        });
        dom.base.search?.addEventListener('input', handleBaseSearchInput);
        dom.base.toggle?.addEventListener('click', handleBaseToggleClick);
        dom.base.dropdown?.addEventListener('keydown', handleBaseKeydown);

        dom.openrouterProvider.back?.addEventListener('click', () => {
            showPage('step2');
        });
        dom.openrouterProvider.next?.addEventListener('click', handleOpenRouterProviderNext);

        if (dom.openrouterProvider.modeRadios) {
            dom.openrouterProvider.modeRadios.forEach((radio) => {
                radio.addEventListener('change', (event) => {
                    setOpenRouterProviderMode(event.target.value);
                });
            });
        }

        dom.openrouterProvider.sortSelect?.addEventListener('change', (event) => {
            state.openrouterProviderSort = event.target.value || 'price';
        });

        dom.form.back?.addEventListener('click', () => {
            requestUnsavedStepThreeConfirmation(() => {
                if (isOpenRouterProvider() && !state.skippingBaseModelStep) {
                    showPage('stepOpenRouterProvider');
                } else {
                    showPage('step2');
                }
            });
        });
        dom.form.form?.addEventListener('submit', handleSubmit);

        // Global keyboard navigation for create flow
        document.addEventListener('keydown', handleCreateKeydown);
    };

    bindEvents();

    function registerUnsavedGuard() {
        if (unsavedGuardRegistered || typeof window.unsavedChangesManager?.register !== 'function') {
            return;
        }
        window.unsavedChangesManager.register({
            id: UNSAVED_GUARD_ID,
            priority: 200,
            isActive: () => Boolean(pages.step3 && !pages.step3.hidden),
            isDirty: () => hasUnsavedStepThreeChanges(),
            discard: () => {
                resetFormValues();
            },
        });
        unsavedGuardRegistered = true;
    }

    window.startModelsCreateFlow = startFlow;
    window.adminModelsShowList = () => {
        goToList();
    };

    // Ensure list is shown by default
    showPage('list');
})();
