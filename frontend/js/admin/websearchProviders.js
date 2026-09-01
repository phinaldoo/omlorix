            (function () {
    if (typeof window === 'undefined') {
        return;
    }

    const dom = {
        pages: {
            list: document.getElementById('page-websearch-providers'),
            select: document.getElementById('page-websearch-providers-select'),
            form: document.getElementById('page-websearch-providers-form'),
        },
        list: {
            container: document.getElementById('websearchProviderList'),
            filter: document.getElementById('websearchProviderFilterSelect'),
            search: document.getElementById('websearchProviderSearchInput'),
            searchClear: document.getElementById('websearchProviderSearchClear'),
            createButton: document.getElementById('websearchCreateProviderButton'),
            exportButton: document.getElementById('exportWebsearchProvidersButton'),
            importButton: document.getElementById('importWebsearchProvidersButton'),
            importFileInput: document.getElementById('importWebsearchProvidersFileInput'),
        },
        select: {
            grid: document.getElementById('websearchAvailableProvidersGrid'),
            back: document.getElementById('websearchProvidersSelectBack'),
        },
        form: {
            title: document.getElementById('websearchProviderFormTitle'),
            subtitle: document.getElementById('websearchProviderFormSubtitle'),
            form: document.getElementById('websearchProviderForm'),
            fields: document.getElementById('websearchProviderFormFields'),
            loading: document.getElementById('websearchProviderFormLoading'),
            submit: document.getElementById('websearchProviderFormSubmit'),
            back: document.getElementById('websearchProviderFormBack'),
        },
        delete: {
            overlay: document.getElementById('deleteWebsearchProviderOverlay'),
            message: document.getElementById('deleteWebsearchProviderMessage'),
            primary: document.getElementById('deleteWebsearchProviderPrimaryButton'),
            primaryText: document.getElementById('deleteWebsearchProviderPrimaryText'),
            cancel: document.getElementById('deleteWebsearchProviderCancelButton'),
        },
        import: {
            overlay: document.getElementById('importWebsearchProvidersOverlay'),
            close: document.getElementById('importWebsearchProvidersClose'),
            cancel: document.getElementById('importWebsearchProvidersCancel'),
            confirm: document.getElementById('importWebsearchProvidersConfirm'),
            list: document.getElementById('importWebsearchProvidersList'),
            selectAll: document.getElementById('importWebsearchProvidersSelectAll'),
            fileName: document.getElementById('importWebsearchProvidersFileName'),
            status: document.getElementById('importWebsearchProvidersStatus'),
        },
    };

    const state = {
        initialized: false,
        view: 'list',
        providers: [],
        availableProviders: [],
        providerKey: null,
        editingId: null,
        mode: 'create',
        schemaSections: [],
        controls: new Map(),
        isDirty: false,
        submitting: false,
        filterValue: 'all',
        searchTerm: '',
        deleteTargetId: null,
        importPayload: null,
        importProviders: [],
        importSelected: new Set(),
        importFileName: '',
        modalLastFocusedElement: null,
    };
    const UNSAVED_GUARD_ID = 'admin-websearch-provider-form-unsaved';
    let unsavedGuardRegistered = false;
    let escapeRegistration = null;
    let modalEscapeRegistration = null;

    const t = window.adminT || ((key, fallback) =>
        (typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback ?? key)
            : fallback ?? key));
    const PROVIDER_URL_SUGGESTIONS_METADATA_KEY = 'provider_url_suggestions';
    const CUSTOM_PROVIDER_URL_OPTION_VALUE = '__custom__';

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

    let languageObserver = null;

    const formatProviderLabel = (key = '') => window.formatProviderLabel?.(key) || key;
    const setButtonLoadingState = window.setButtonLoadingState || ((button, isLoading, loadingLabel = 'Loading...') => {
        if (!button) {
            return;
        }
        const labelTarget = button.querySelector('span');
        const getLabel = () => (labelTarget ? labelTarget.textContent : button.textContent);
        const setLabel = (value) => {
            if (labelTarget) {
                labelTarget.textContent = value;
            } else {
                button.textContent = value;
            }
        };

        if (isLoading) {
            if (button.dataset.originalLabel === undefined) {
                button.dataset.originalLabel = getLabel()?.trim?.() || '';
            }
            button.disabled = true;
            button.classList.add('loading');
            setLabel(loadingLabel);
            return;
        }

        button.disabled = false;
        button.classList.remove('loading');
        if (button.dataset.originalLabel !== undefined) {
            setLabel(button.dataset.originalLabel || '');
            delete button.dataset.originalLabel;
        }
    });

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

    const BASE_PROVIDER_SECTION = {
        title: 'Provider basics',
        description: 'Name the integration before configuring provider-specific settings.',
        i18n_title: 'websearch_provider_basics_title',
        i18n_description: 'websearch_provider_basics_desc',
        fields: [
            {
                key: 'name',
                scope: 'root',
                label: 'Display Name',
                description: 'Name shown to admins when selecting the provider.',
                i18n_label: 'websearch_provider_display_name',
                i18n_description: 'websearch_provider_display_name_desc',
                type: 'string',
                placeholder: t('websearch_provider_name_placeholder', 'Enter provider name'),
                required: true,
            },
        ],
    };

    const cloneBaseSection = () => JSON.parse(JSON.stringify(BASE_PROVIDER_SECTION));
    const currentWebsearchExportVersion = () => 1.0;

    const confirmNavigation = (next) => {
        if (typeof window.unsavedChangesManager?.confirmIfNeeded === 'function') {
            const prompted = window.unsavedChangesManager.confirmIfNeeded({
                id: UNSAVED_GUARD_ID,
                onConfirm: next,
            });
            if (prompted) {
                return;
            }
        }
        next();
    };

    const registerUnsavedGuard = () => {
        if (unsavedGuardRegistered || typeof window.unsavedChangesManager?.register !== 'function') {
            return;
        }
        window.unsavedChangesManager.register({
            id: UNSAVED_GUARD_ID,
            priority: 210,
            isActive: () => Boolean(dom.pages.form && !dom.pages.form.hidden),
            isDirty: () => Boolean(state.isDirty),
            discard: () => {
                state.isDirty = false;
            },
            getCopy: () => ({
                subtitle: t('modal_discard_changes_desc', 'You have unsaved changes. Are you sure you want to leave without saving?'),
            }),
        });
        unsavedGuardRegistered = true;
    };

    const setView = (view) => {
        state.view = view;
        Object.entries(dom.pages).forEach(([key, element]) => {
            if (!element) {
                return;
            }
            element.hidden = key !== view;
        });
        if (view !== 'form') {
            state.isDirty = false;
        }
    };

    const isViewVisible = (view) => Boolean(dom.pages[view] && !dom.pages[view].hidden);

    const hasBlockingEscapeTarget = () => Boolean(document.querySelector([
        '.admin-select.open',
        '.admin-multiselect.open',
        '.icon-picker-dropdown:not([hidden])',
        '.shared-modal-overlay:not([hidden])',
        '.overlay-container.visible',
        '.modal-overlay.visible',
    ].join(', ')));

    const handleEscapeNavigation = () => {
        if (state.view === 'form') {
            confirmNavigation(() => setView(state.mode === 'edit' ? 'list' : 'select'));
            return;
        }

        if (state.view === 'select') {
            setView('list');
        }
    };

    const registerEscapeShortcut = () => {
        if (typeof window === 'undefined' || typeof window.registerEscapeHandler !== 'function') {
            return;
        }

        if (!escapeRegistration) {
            escapeRegistration = window.registerEscapeHandler({
                id: 'admin-websearch-providers-escape',
                priority: 140,
                isActive: () => (isViewVisible('select') || isViewVisible('form')) && !hasBlockingEscapeTarget(),
                close: handleEscapeNavigation,
            });
        }
        if (!modalEscapeRegistration) {
            modalEscapeRegistration = window.registerEscapeHandler({
                id: 'admin-websearch-providers-modal-escape',
                priority: 180,
                isActive: () => Boolean(
                    (dom.delete.overlay && !dom.delete.overlay.hidden)
                    || (dom.import.overlay && !dom.import.overlay.hidden)
                ),
                close: () => {
                    if (dom.delete.overlay && !dom.delete.overlay.hidden) {
                        closeDeleteModal();
                    } else {
                        closeImportModal();
                    }
                },
            });
        }
    };

    const renderEmptyState = (container, title, description = '', icon = Icons?.omlorix || '') => {
        if (!container) {
            return;
        }
        container.innerHTML = '';
        const emptyState = window.createAdminEmptyPlaceholder({
            title,
            description,
            icon,
            className: 'provider-empty-state',
        });
        container.appendChild(emptyState);
    };

    const renderProvidersLoadingState = (container, message = t('websearch_providers_loading', 'Loading providers…')) => {
        if (!container) {
            return;
        }
        container.innerHTML = '';

        const loadingState = window.createAdminLoadingPlaceholder({
            message,
            className: '',
        });
        container.appendChild(loadingState);
    };

    const filterProviders = () => {
        const term = state.searchTerm.toLowerCase();
        return state.providers.filter((provider) => {
            const matchesType = state.filterValue === 'all' || provider.provider === state.filterValue;
            if (!matchesType) {
                return false;
            }
            if (!term) {
                return true;
            }
            return (
                provider.provider?.toLowerCase().includes(term) ||
                provider.name?.toLowerCase().includes(term)
            );
        });
    };

    const renderProvidersList = () => {
        const container = dom.list.container;
        if (!container) {
            return;
        }
        const providers = filterProviders();
        container.innerHTML = '';
        if (!providers.length) {
            renderEmptyState(
                container,
                state.providers.length ? t('websearch_providers_empty_filtered', 'No providers match your filters') : t('websearch_providers_empty_title', 'No web search providers yet'),
                state.providers.length ? '' : t('websearch_providers_empty_desc', 'Connect a provider to enable search and scraping.')
            );
            return;
        }

        const header = document.createElement('div');
        header.className = 'provider-table-header';
        [
            { className: 'header-icon', text: t('table_header_icon', 'Icon') },
            { className: 'header-provider', text: t('table_header_provider', 'Provider') },
            { className: 'header-custom', text: t('table_header_display_name', 'Display name') },
            { className: 'header-actions', text: t('table_header_actions', 'Actions') },
        ].forEach(({ className, text }) => {
            const cell = document.createElement('div');
            if (className) {
                cell.className = className;
            }
            cell.textContent = text;
            header.appendChild(cell);
        });
        container.appendChild(header);

        const fragment = document.createDocumentFragment();
        providers.forEach((provider) => {
            const row = document.createElement('div');
            row.className = 'provider-row';
            row.dataset.providerId = provider.id;
            if (provider.provider) {
                row.dataset.providerKey = provider.provider;
            }

            const providerKey = (provider.provider || '').toLowerCase();

            const iconCell = document.createElement('div');
            iconCell.className = 'provider-icon';
            iconCell.innerHTML = Icons?.[providerKey] || Icons?.omlorix || '';
            row.appendChild(iconCell);

            const providerCell = document.createElement('div');
            providerCell.className = 'provider-name';
            providerCell.textContent = formatProviderLabel(provider.provider);
            row.appendChild(providerCell);

            const customCell = document.createElement('div');
            customCell.className = 'provider-custom';
            customCell.textContent = provider.name || '—';
            row.appendChild(customCell);

            const actionsCell = document.createElement('div');
            actionsCell.className = 'provider-actions';

            const editButton = document.createElement('button');
            editButton.type = 'button';
            editButton.className = 'action-btn edit-btn';
            editButton.dataset.providerId = provider.id;
            editButton.innerHTML = Icons?.edit || 'Edit';
            actionsCell.appendChild(editButton);

            const deleteButton = document.createElement('button');
            deleteButton.type = 'button';
            deleteButton.className = 'action-btn delete-btn';
            deleteButton.dataset.providerId = provider.id;
            deleteButton.dataset.providerName = provider.name || formatProviderLabel(provider.provider);
            deleteButton.innerHTML = Icons?.trash || 'Delete';
            actionsCell.appendChild(deleteButton);

            row.appendChild(actionsCell);
            fragment.appendChild(row);
        });

        container.appendChild(fragment);
    };

    const resolveProvidersFromPayload = (payload) => {
        if (!payload || typeof payload !== 'object') {
            notifyError(t('providers_import_invalid_export', 'Invalid export file.'));
            return [];
        }
        if (payload.export_type !== 'websearch_provider') {
            notifyError(t('providers_import_unsupported_type', 'Unsupported export file type.'));
            return [];
        }
        if (payload.export_version !== currentWebsearchExportVersion()) {
            notifyError(t('providers_import_version_mismatch', 'Unsupported export version. Expected 1.0.'));
            return [];
        }
        const providers = payload?.data?.providers;
        return Array.isArray(providers) ? providers : [];
    };

    const formatValidationErrorDetail = (detail) => {
        if (!detail) {
            return '';
        }
        if (typeof detail === 'string') {
            return detail;
        }
        if (Array.isArray(detail)) {
            return detail.map((item) => formatValidationErrorDetail(item)).filter(Boolean).join('; ');
        }
        if (typeof detail === 'object') {
            const message = detail.msg || detail.message || detail.error || JSON.stringify(detail);
            const location = detail.loc;
            if (Array.isArray(location) && location.length) {
                return `${message} (at ${location.join('.')})`;
            }
            if (typeof location === 'string' && location) {
                return `${message} (at ${location})`;
            }
            return message;
        }
        return String(detail);
    };

    const formatImportErrorEntry = (entry) => {
        if (!entry || typeof entry !== 'object') {
            return '';
        }

        const rawIndex = entry.index !== undefined ? Number(entry.index) : NaN;
        const displayIndex = Number.isFinite(rawIndex) ? rawIndex + 1 : '?';
        const name = entry.name ? ` (${entry.name})` : '';
        const message = formatValidationErrorDetail(entry.error) || 'Unknown error.';

        return `• Item ${displayIndex}${name}: ${message}`;
    };

    const setImportStatus = (message = '', type = '') => {
        if (!dom.import.status) {
            return;
        }
        if (!message) {
            dom.import.status.hidden = true;
            dom.import.status.textContent = '';
            dom.import.status.className = 'provider-import-status';
            return;
        }
        dom.import.status.hidden = false;
        dom.import.status.textContent = message;
        dom.import.status.className = `provider-import-status ${type}`.trim();
    };

    const closeImportModal = () => {
        if (dom.import.overlay) {
            dom.import.overlay.setAttribute('aria-hidden', 'true');
            dom.import.overlay.hidden = true;
        }
        state.importPayload = null;
        state.importProviders = [];
        state.importSelected = new Set();
        state.importFileName = '';
        if (dom.import.list) {
            dom.import.list.innerHTML = '';
        }
        if (dom.import.fileName) {
            dom.import.fileName.textContent = '';
        }
        if (dom.import.selectAll) {
            dom.import.selectAll.checked = false;
        }
        setImportStatus();
        state.modalLastFocusedElement?.focus?.();
        state.modalLastFocusedElement = null;
    };

    const openImportModal = () => {
        if (!dom.import.overlay) {
            return;
        }
        state.modalLastFocusedElement = document.activeElement;
        dom.import.overlay.hidden = false;
        dom.import.overlay.setAttribute('aria-hidden', 'false');
        setImportStatus();
        if (dom.import.fileName) {
            dom.import.fileName.textContent = state.importFileName || '';
        }
        if (dom.import.selectAll) {
            dom.import.selectAll.checked = state.importProviders.length === state.importSelected.size;
        }
        dom.import.confirm?.focus();
    };

    const renderImportProvidersList = () => {
        if (!dom.import.list) {
            return;
        }
        dom.import.list.innerHTML = '';

        if (!state.importProviders.length) {
            const emptyState = document.createElement('div');
            emptyState.className = 'provider-import-empty';
            emptyState.textContent = t('providers_import_empty', 'No providers found in this file.');
            dom.import.list.appendChild(emptyState);
            return;
        }

        const fragment = document.createDocumentFragment();

        state.importProviders.forEach((provider, index) => {
            const entry = document.createElement('label');
            entry.className = 'provider-import-entry';
            entry.setAttribute('role', 'option');
            entry.setAttribute('aria-selected', state.importSelected.has(index) ? 'true' : 'false');

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = state.importSelected.has(index);
            checkbox.dataset.providerIndex = String(index);
            checkbox.addEventListener('change', (event) => {
                const target = event.currentTarget;
                const currentIndex = Number.parseInt(target.dataset.providerIndex || '', 10);
                if (Number.isNaN(currentIndex)) {
                    return;
                }

                if (target.checked) {
                    state.importSelected.add(currentIndex);
                } else {
                    state.importSelected.delete(currentIndex);
                }

                target.closest('.provider-import-entry')?.setAttribute('aria-selected', target.checked ? 'true' : 'false');

                if (dom.import.selectAll) {
                    dom.import.selectAll.checked = state.importSelected.size === state.importProviders.length;
                }
                setImportStatus();
            });
            entry.appendChild(checkbox);

            const content = document.createElement('div');
            content.className = 'provider-import-entry-content';

            const title = document.createElement('p');
            title.className = 'provider-import-entry-title';
            title.textContent = provider?.name || t('providers_import_unnamed', '(Unnamed provider)');
            content.appendChild(title);

            const meta = document.createElement('div');
            meta.className = 'provider-import-entry-meta';
            const providerMeta = document.createElement('span');
            providerMeta.textContent = formatT('providers_import_provider_meta', 'Provider: {provider}', {
                provider: formatProviderLabel(provider?.provider) || t('common_unknown', 'Unknown'),
            });
            meta.appendChild(providerMeta);
            content.appendChild(meta);

            if (provider?.settings && typeof provider.settings === 'object') {
                const details = document.createElement('div');
                details.className = 'provider-import-entry-meta';
                const keys = Object.keys(provider.settings).slice(0, 3);
                if (keys.length) {
                    details.textContent = formatT('providers_import_settings_meta', 'Settings: {keys}', { keys: keys.join(', ') });
                    content.appendChild(details);
                }
            }

            entry.appendChild(content);
            fragment.appendChild(entry);
        });

        dom.import.list.appendChild(fragment);
    };

    const submitSelectedImports = async () => {
        if (!state.importPayload) {
            setImportStatus(t('providers_import_choose_file_first', 'Please choose a provider file first.'), '');
            return;
        }
        if (!state.importSelected.size) {
            setImportStatus(t('providers_import_select_one', 'Select at least one provider to import.'), '');
            return;
        }

        try {
            setButtonLoadingState(dom.import.confirm, true, t('admin_importing_ellipsis', 'Importing...'));
            const indices = Array.from(state.importSelected).sort((a, b) => a - b);
            const filteredProviders = indices.map((index) => state.importProviders[index]).filter(Boolean);
            const filteredPayload = {
                ...state.importPayload,
                data: {
                    ...(state.importPayload.data || {}),
                    providers: filteredProviders,
                },
            };

            const response = await websearchProvidersApi.importProviders(filteredPayload);
            if (!response.ok) {
                throw await websearchProvidersApi.buildResponseError(
                    response,
                    t('providers_import_failed', 'Failed to import providers.')
                );
            }

            const result = await response.json();
            const createdCount = result?.created?.length || 0;
            const errorCount = result?.errors?.length || 0;
            const formattedErrors = Array.isArray(result?.errors)
                ? result.errors.map((entry) => formatImportErrorEntry(entry)).filter(Boolean)
                : [];

            if (createdCount) {
                const successMessage = formatT(
                    createdCount === 1 ? 'providers_import_success_single' : 'providers_import_success_plural',
                    createdCount === 1 ? 'Imported {count} provider successfully.' : 'Imported {count} providers successfully.',
                    { count: createdCount }
                );
                notifySuccess(successMessage);
                setImportStatus(successMessage, 'success');
            }

            if (errorCount) {
                const errorSummary = formatT(
                    errorCount === 1 ? 'providers_import_issues_single' : 'providers_import_issues_plural',
                    errorCount === 1 ? '{count} provider has issues.' : '{count} providers have issues.',
                    { count: errorCount }
                );
                const errorDetails = formattedErrors.length ? `\n${formattedErrors.join('\n')}` : '';
                const warningMessage = `${errorSummary} ${t('providers_import_check_file', 'Check the import file.')}${errorDetails}`;
                setImportStatus(warningMessage, '');
                notifyWarning(warningMessage);
            }

            await loadProviders();

            if (!errorCount) {
                closeImportModal();
            }
        } catch (error) {
            console.error('Failed to import web search providers', error);
            setImportStatus(error?.message || t('providers_import_failed', 'Failed to import providers.'), '');
            notifyError(error?.message || t('providers_import_failed', 'Failed to import providers.'));
        } finally {
            setButtonLoadingState(dom.import.confirm, false);
        }
    };

    const handleExportProviders = async () => {
        try {
            setButtonLoadingState(dom.list.exportButton, true, t('admin_exporting_ellipsis', 'Exporting...'));
            const response = await websearchProvidersApi.exportProviders();
            if (!response.ok) {
                throw await websearchProvidersApi.buildResponseError(
                    response,
                    t('providers_export_failed_retry', 'Failed to export providers. Please try again.')
                );
            }

            const exportData = await response.json();
            const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
            const timestamp = new Date().toISOString().replace(/[:\.]/g, '-');
            const filename = `websearch-providers-${timestamp}.json`;

            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(link.href);

            notifySuccess(t('providers_export_success', 'Provider export downloaded successfully.'));
        } catch (error) {
            console.error('Failed to export web search providers', error);
            notifyError(error?.message || t('providers_export_failed', 'Failed to export providers.'));
        } finally {
            setButtonLoadingState(dom.list.exportButton, false);
        }
    };

    const handleImportProviders = async (event) => {
        const input = event?.target;
        if (!input?.files?.length) {
            return;
        }

        const [file] = input.files;
        input.value = '';

        const isJsonFile = file && (file.type === 'application/json' || file.name?.toLowerCase().endsWith('.json'));
        if (!isJsonFile) {
            notifyError(t('providers_import_select_json', 'Please select a valid JSON file.'));
            return;
        }

        try {
            const fileContent = await file.text();
            let payload;
            try {
                payload = JSON.parse(fileContent);
            } catch (error) {
                notifyError(t('providers_import_invalid_json', 'Invalid JSON file.'));
                return;
            }

            const providers = resolveProvidersFromPayload(payload);
            if (!providers.length) {
                notifyWarning(t('providers_import_empty', 'No providers found in this file.'));
                return;
            }

            state.importPayload = payload;
            state.importProviders = providers;
            state.importSelected = new Set(providers.map((_, index) => index));
            state.importFileName = file.name || 'websearch-providers.json';

            renderImportProvidersList();
            openImportModal();
        } catch (error) {
            console.error('Failed to import web search providers', error);
            notifyError(error?.message || t('providers_import_failed', 'Failed to import providers.'));
        }
    };

    const toggleSelectAllImports = (event) => {
        const { checked } = event.currentTarget;
        state.importSelected.clear();
        if (checked) {
            state.importProviders.forEach((_, index) => state.importSelected.add(index));
        }
        renderImportProvidersList();
        setImportStatus();
    };

    const populateFilterOptions = () => {
        const select = dom.list.filter;
        if (!select) {
            return;
        }
        const providers = [...new Set(state.providers.map((item) => item.provider).filter(Boolean))]
            .sort((left, right) => {
                const labelCompare = formatProviderLabel(left).localeCompare(formatProviderLabel(right), undefined, {
                    sensitivity: 'base',
                    numeric: true,
                });
                return labelCompare || String(left).localeCompare(String(right), undefined, {
                    sensitivity: 'base',
                    numeric: true,
                });
            });
        select.innerHTML = ['all', ...providers]
            .map((provider) => `<option value="${provider}">${provider === 'all' ? t('providers_filter_all', 'All providers') : formatProviderLabel(provider)}</option>`)
            .join('');
        if (!providers.includes(state.filterValue)) {
            state.filterValue = 'all';
        }
        select.value = state.filterValue;
        upgradeWebsearchFilterSelect();
    };

    const upgradeWebsearchFilterSelect = () => {
        window.upgradeAdminSingleSelect?.(dom.list.filter, {
            key: 'websearch-filter',
            placeholder: t('providers_filter_all', 'All providers')
        });
    };

    const renderAvailableProviders = () => {
        const grid = dom.select.grid;
        if (!grid) {
            return;
        }
        grid.innerHTML = '';
        if (!state.availableProviders.length) {
            renderEmptyState(grid, t('websearch_providers_available_empty', 'No providers available'));
            return;
        }
        const fragment = document.createDocumentFragment();
        const sortedProviders = [...state.availableProviders].sort((left, right) => {
            const leftId = left?.id || '';
            const rightId = right?.id || '';
            const labelCompare = formatProviderLabel(leftId).localeCompare(formatProviderLabel(rightId), undefined, {
                sensitivity: 'base',
                numeric: true,
            });
            return labelCompare || String(leftId).localeCompare(String(rightId), undefined, {
                sensitivity: 'base',
                numeric: true,
            });
        });
        sortedProviders.forEach((provider) => {
            const card = document.createElement('button');
            card.type = 'button';
            card.className = 'available-provider-card';
            card.dataset.providerKey = provider.id;
            card.innerHTML = `
                <div class="available-provider-icon">${Icons?.[provider.id] || Icons?.omlorix || ''}</div>
                <div class="available-provider-card-title">${formatProviderLabel(provider.id)}</div>
            `;
            card.addEventListener('click', () => openCreateForm(provider.id));
            fragment.appendChild(card);
        });
        grid.appendChild(fragment);
    };

    const resetForm = () => {
        // Clear any existing field validation errors
        window.FieldValidation?.clearAllFieldErrors(dom.form.fields);
        state.controls.clear();
        if (dom.form.fields) {
            dom.form.fields.innerHTML = '';
        }
        if (dom.form.loading) {
            dom.form.loading.hidden = false;
        }
        state.isDirty = false;
    };

    const getSectionsArray = (schema) => {
        if (!schema) {
            return [];
        }
        if (Array.isArray(schema.sections)) {
            return schema.sections;
        }
        if (Array.isArray(schema)) {
            return schema;
        }
        return [];
    };

    const resolveFieldValue = (field, value) => (
        value ?? field?.value ?? field?.default
    );

    const getCurrentFormValues = () => {
        if (!state.controls.size) {
            return null;
        }
        const { root, settings } = collectFormValues();
        return { ...root, settings };
    };

    const updateFormCopy = (values = null) => {
        if (!dom.form.title || !dom.form.subtitle || !state.providerKey) {
            return;
        }
        const label = formatProviderLabel(state.providerKey);
        const currentName = typeof values?.name === 'string' && values.name.trim()
            ? values.name.trim()
            : label;

        if (state.mode === 'edit') {
            dom.form.title.textContent = formatT('websearch_provider_form_title_edit', 'Edit {provider}', { provider: currentName });
            dom.form.subtitle.textContent = t('websearch_provider_edit_subtitle', 'Update the settings for this provider.');
            return;
        }

        dom.form.title.textContent = formatT('websearch_provider_form_title_create', 'Configure {provider}', { provider: label });
        dom.form.subtitle.textContent = formatT('websearch_provider_form_subtitle_create', 'Provide the required details to finish setting up {provider}.', { provider: label });
    };

    const renderSchema = (schema, values = {}) => {
        const container = dom.form.fields;
        if (!container) {
            return;
        }
        state.controls.clear();
        container.innerHTML = '';
        const sections = [cloneBaseSection(), ...getSectionsArray(schema)];
        if (!sections.length) {
            container.innerHTML = `<p class="provider-form-empty">${t('websearch_provider_no_config_required', 'No configuration required for this provider.')}</p>`;
            dom.form.loading.hidden = true;
            return;
        }
        const fragment = document.createDocumentFragment();
        sections.forEach((section) => {
            const sectionEl = document.createElement('section');
            sectionEl.className = 'settings-section provider-settings-section';

            if (section.title || section.description) {
                const header = document.createElement('div');
                header.className = 'settings-section-header';
                if (section.title) {
                    const title = document.createElement('h3');
                    title.className = 'settings-section-title';
                    title.textContent = (section.i18n_title && typeof window.getTranslation === 'function')
                        ? window.getTranslation(section.i18n_title, section.title)
                        : section.title;
                    header.appendChild(title);
                }
                if (section.description) {
                    const description = document.createElement('p');
                    description.className = 'settings-section-description';
                    description.textContent = (section.i18n_description && typeof window.getTranslation === 'function')
                        ? window.getTranslation(section.i18n_description, section.description)
                        : section.description;
                    header.appendChild(description);
                }
                sectionEl.appendChild(header);
            }

            const body = document.createElement('div');
            body.className = 'settings-section-body';

            section.fields?.forEach((field) => {
                if (!field?.key) {
                    return;
                }
                const { row, controlWrapper } = createFieldLayout(field);
                row.classList.add('settings-row-provider');
                const { root, control } = createFieldControl(field, { datasetKey: field.key });
                control.id = `websearch-field-${field.key.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
                control.name = field.key;
                const hasSecretPlaceholder = field.key === 'api_key'
                    && typeof field.placeholder === 'string'
                    && field.placeholder.endsWith('...');

                if (hasSecretPlaceholder) {
                    field.required = false;
                }

                const shouldApplyRequired = field.required && control.type !== 'checkbox';
                if (shouldApplyRequired) {
                    control.required = true;
                } else if (control.removeAttribute) {
                    control.required = false;
                    control.removeAttribute('required');
                }

                if (hasSecretPlaceholder) {
                    control.dataset.allowEmpty = 'true';
                }
                const scope = field.scope || section.scope || (section === BASE_PROVIDER_SECTION ? 'root' : 'settings');
                const value = (() => {
                    const segments = field.key.split('.');
                    if (!segments.length) {
                        return undefined;
                    }
                    const source = scope === 'root' ? values || {} : values.settings || {};
                    const providedValue = segments.reduce((acc, key) => (acc == null ? acc : acc[key]), source);
                    return resolveFieldValue(field, providedValue);
                })();
                applyControlValue?.(control, field, value);
                const providerUrlSuggestionSelect = createProviderUrlSuggestionSelect(field, control);
                if (providerUrlSuggestionSelect) {
                    controlWrapper.appendChild(providerUrlSuggestionSelect);
                }
                controlWrapper.appendChild(root);
                body.appendChild(row);
                const normalizedField = { ...field, scope };
                if (hasSecretPlaceholder) {
                    normalizedField.required = false;
                }
                state.controls.set(field.key, { field: normalizedField, control });
                control.addEventListener('input', () => {
                    state.isDirty = true;
                });
                control._providerUrlSuggestionSync?.();
            });

            sectionEl.appendChild(body);
            fragment.appendChild(sectionEl);
        });
        container.appendChild(fragment);
        dom.form.loading.hidden = true;
        updateFormCopy(values);

        // Attach error clear listeners for validation
        window.FieldValidation?.attachErrorClearListeners(state.controls);
    };

    const extractControlValue = (field, control) => {
        if (control.dataset.keywordTags !== undefined) {
            try {
                return JSON.parse(control.dataset.keywordTags || '[]');
            } catch (error) {
                return [];
            }
        }
        if (control.type === 'checkbox') {
            return control.checked;
        }
        if (control.type === 'number') {
            const parsed = Number(control.value);
            return Number.isNaN(parsed) ? null : parsed;
        }
        return control.value?.trim?.() ?? control.value ?? null;
    };

    const collectFormValues = () => {
        const root = {};
        const settings = {};
        state.controls.forEach(({ field, control }) => {
            const value = extractControlValue(field, control);
            const segments = field.key.split('.');
            if (!segments.length) {
                return;
            }
            const target = (field.scope || 'settings') === 'root' ? root : settings;
            let cursor = target;
            segments.forEach((segment, index) => {
                if (index === segments.length - 1) {
                    cursor[segment] = value;
                    return;
                }
                if (typeof cursor[segment] !== 'object' || cursor[segment] === null) {
                    cursor[segment] = {};
                }
                cursor = cursor[segment];
            });
        });
        return { root, settings };
    };

    const submitForm = async (event) => {
        event.preventDefault();

        // Validate required fields using shared FieldValidation
        if (state.controls.size && !window.FieldValidation?.validate(state.controls)) {
            return;
        }

        if (!dom.form.form?.reportValidity?.()) {
            return;
        }
        if (!state.providerKey) {
            notifyError(t('websearch_provider_type_missing', 'Provider type missing.'));
            return;
        }
        const { root, settings } = collectFormValues();
        if (typeof root.name !== 'string' || !root.name.trim()) {
            notifyError(t('websearch_provider_name_required', 'Provider name is required.'));
            return;
        }
        const payload = {
            name: root.name.trim(),
            settings,
        };
        const isEdit = state.mode === 'edit' && state.editingId;
        const label = formatProviderLabel(state.providerKey);
        try {
            state.submitting = true;
            setButtonLoadingState(
                dom.form.submit,
                true,
                isEdit
                    ? t('websearch_provider_busy_saving', 'Saving…')
                    : t('websearch_provider_busy_creating', 'Creating…')
            );
            let response;
            if (isEdit) {
                response = await websearchProvidersApi.updateProvider(state.editingId, payload);
            } else {
                response = await websearchProvidersApi.createProvider({ ...payload, provider: state.providerKey });
            }
            if (!response.ok) {
                throw await websearchProvidersApi.buildResponseError(
                    response,
                    isEdit
                        ? t('websearch_provider_update_failed', 'Failed to update provider.')
                        : t('websearch_provider_create_failed', 'Failed to create provider.')
                );
            }
            notifySuccess(
                isEdit
                    ? formatT('websearch_provider_update_success_named', '{provider} provider updated successfully.', { provider: label })
                    : formatT('websearch_provider_create_success_named', '{provider} provider created successfully.', { provider: label })
            );
            state.isDirty = false;
            setView('list');
            await loadProviders();
        } catch (error) {
            notifyError(error?.message || t('websearch_provider_save_failed', 'Failed to save provider.'));
        } finally {
            state.submitting = false;
            setButtonLoadingState(dom.form.submit, false);
        }
    };

    const loadProviderSchema = async (providerKey, options = {}) => {
        if (!providerKey) {
            return;
        }
        try {
            dom.form.loading.hidden = false;
            const schema = await websearchProvidersApi.fetchProviderSchema(providerKey, options);
            state.schemaSections = schema;
            renderSchema(schema, options.values || {});
        } catch (error) {
            notifyError(error?.message || t('websearch_provider_schema_load_failed', 'Failed to load provider schema.'));
            dom.form.loading.hidden = true;
            dom.form.fields.innerHTML = `<p class="provider-form-empty">${t('websearch_provider_config_load_failed', 'Unable to load provider configuration.')}</p>`;
        }
    };

    const openCreateForm = async (providerKey) => {
        state.mode = 'create';
        state.providerKey = providerKey;
        state.editingId = null;
        resetForm();
        setView('form');
        const label = formatProviderLabel(providerKey);
        updateFormCopy({ name: label, settings: {} });
        await loadProviderSchema(providerKey, {
            values: { name: formatT('websearch_provider_default_name', '{provider}', { provider: label }), settings: {} },
        });
    };

    const openEditForm = async (providerId) => {
        if (!providerId) {
            notifyError(t('websearch_provider_id_missing', 'Provider ID missing.'));
            return;
        }
        state.mode = 'edit';
        state.editingId = providerId;
        resetForm();
        setView('form');
        try {
            const detail = await websearchProvidersApi.fetchProviderDetail(providerId);
            state.providerKey = detail.provider;
            updateFormCopy({ name: detail.name || formatProviderLabel(detail.provider), settings: detail.settings || {} });
            await loadProviderSchema(detail.provider, {
                providerId,
                values: { ...detail, settings: detail.settings || {} },
            });
        } catch (error) {
            notifyError(error?.message || t('websearch_provider_open_failed', 'Failed to open provider.'));
            setView('list');
        }
    };

    const openCreateFlow = async () => {
        setView('select');
        if (!state.availableProviders.length) {
            try {
                const data = await websearchProvidersApi.fetchAvailableProviders();
                state.availableProviders = Array.isArray(data) ? data : [];
            } catch (error) {
                notifyError(error?.message || t('websearch_providers_available_load_failed', 'Failed to load available providers.'));
                renderEmptyState(dom.select.grid, t('websearch_providers_unable_to_load', 'Unable to load providers'));
                return;
            }
        }
        renderAvailableProviders();
    };

    const openDeleteModal = (providerId, providerName) => {
        state.deleteTargetId = providerId;
        state.modalLastFocusedElement = document.activeElement;
        if (dom.delete.message) {
            const impactCopy = t('websearch_provider_delete_impact', 'Deleting a web search provider clears it from every model that references it and removes the web_search tool from those models.');
            const baseCopy = providerName
                ? formatT('websearch_provider_delete_confirm_named', 'Are you sure you want to delete "{name}"?', { name: providerName })
                : t('websearch_provider_delete_confirm_default', 'Are you sure you want to delete this web search provider?');
            dom.delete.message.textContent = `${baseCopy} ${impactCopy}`;
        }
        dom.delete.primaryText.textContent = t('websearch_provider_delete_btn', 'Delete Provider');
        dom.delete.primary.disabled = false;
        if (dom.delete.overlay) {
            dom.delete.overlay.hidden = false;
            dom.delete.overlay.setAttribute('aria-hidden', 'false');
            window.requestAnimationFrame(() => dom.delete.cancel?.focus());
        }
    };

    const closeDeleteModal = () => {
        state.deleteTargetId = null;
        if (dom.delete.overlay) {
            dom.delete.overlay.setAttribute('aria-hidden', 'true');
            dom.delete.overlay.hidden = true;
        }
        state.modalLastFocusedElement?.focus?.();
        state.modalLastFocusedElement = null;
    };

    const deleteProvider = async () => {
        if (!state.deleteTargetId) {
            return;
        }
        dom.delete.primary.disabled = true;
        dom.delete.primaryText.textContent = t('admin_deleting', 'Deleting...');
        try {
            const response = await websearchProvidersApi.deleteProvider(state.deleteTargetId);
            if (!response.ok) {
                throw await websearchProvidersApi.buildResponseError(response, t('websearch_provider_delete_failed', 'Failed to delete provider.'));
            }
            notifySuccess(t('websearch_provider_delete_success', 'Web search provider deleted successfully.'));
            closeDeleteModal();
            await loadProviders();
        } catch (error) {
            notifyError(error?.message || t('websearch_provider_delete_failed', 'Failed to delete provider.'));
            dom.delete.primary.disabled = false;
            dom.delete.primaryText.textContent = t('websearch_provider_delete_btn', 'Delete Provider');
        }
    };

    const onListClick = (event) => {
        const deleteButton = event.target.closest('.delete-btn');
        if (deleteButton?.dataset.providerId) {
            openDeleteModal(deleteButton.dataset.providerId, deleteButton.dataset.providerName);
            return;
        }
        const editButton = event.target.closest('.edit-btn');
        if (editButton?.dataset.providerId) {
            confirmNavigation(() => openEditForm(editButton.dataset.providerId));
            return;
        }
        const row = event.target.closest('.provider-row');
        if (row?.dataset.providerId && !event.target.closest('.provider-actions')) {
            confirmNavigation(() => openEditForm(row.dataset.providerId));
        }
    };

    const onFilterChange = (event) => {
        state.filterValue = event.target.value || 'all';
        renderProvidersList();
    };

    const updateSearchClearVisibility = () => {
        if (!dom.list.search || !dom.list.searchClear) {
            return;
        }
        const hasValue = Boolean(dom.list.search.value && dom.list.search.value.trim().length);
        dom.list.searchClear.hidden = !hasValue;
    };

    const onSearchInput = (event) => {
        state.searchTerm = event.target.value || '';
        updateSearchClearVisibility();
        renderProvidersList();
    };

    const onSearchClear = (event) => {
        event.preventDefault();
        if (!dom.list.search) {
            return;
        }
        dom.list.search.value = '';
        state.searchTerm = '';
        updateSearchClearVisibility();
        renderProvidersList();
        dom.list.search.focus();
    };

    const bindEvents = () => {
        registerUnsavedGuard();
        dom.list.filter?.addEventListener('change', onFilterChange);
        dom.list.search?.addEventListener('input', onSearchInput);
        if (dom.list.searchClear) {
            dom.list.searchClear.addEventListener('click', onSearchClear);
            updateSearchClearVisibility();
        }
        dom.list.createButton?.addEventListener('click', () => confirmNavigation(openCreateFlow));
        dom.list.exportButton?.addEventListener('click', handleExportProviders);
        dom.list.importButton?.addEventListener('click', () => dom.list.importFileInput?.click());
        dom.list.importFileInput?.addEventListener('change', handleImportProviders);
        dom.select.back?.addEventListener('click', () => setView('list'));
        dom.form.form?.addEventListener('submit', submitForm);
        dom.form.back?.addEventListener('click', () => confirmNavigation(() => setView(state.mode === 'edit' ? 'list' : 'select')));
        dom.list.container?.addEventListener('click', onListClick);
        dom.delete.cancel?.addEventListener('click', closeDeleteModal);
        dom.delete.overlay?.addEventListener('click', (event) => {
            if (event.target === dom.delete.overlay) {
                closeDeleteModal();
            }
        });
        dom.delete.primary?.addEventListener('click', deleteProvider);
        dom.import.close?.addEventListener('click', closeImportModal);
        dom.import.cancel?.addEventListener('click', closeImportModal);
        dom.import.confirm?.addEventListener('click', submitSelectedImports);
        dom.import.selectAll?.addEventListener('change', toggleSelectAllImports);
        dom.import.overlay?.addEventListener('click', (event) => {
            if (event.target === dom.import.overlay) {
                closeImportModal();
            }
        });
    };

    const loadProviders = async () => {
        if (!dom.list.container) {
            return;
        }
        renderProvidersLoadingState(dom.list.container, t('websearch_providers_loading', 'Loading providers…'));
        try {
            const providers = await websearchProvidersApi.fetchProvidersList();
            state.providers = Array.isArray(providers) ? providers : [];
            populateFilterOptions();
            renderProvidersList();
        } catch (error) {
            notifyError(error?.message || t('websearch_providers_load_failed', 'Failed to load providers.'));
            renderEmptyState(
                dom.list.container,
                t('websearch_providers_unable_to_load', 'Unable to load providers'),
                t('websearch_providers_try_again_later', 'Please try again later.'),
                Icons?.warning || ''
            );
        }
    };

    const init = () => {
        const visibleView = Object.entries(dom.pages).find(([, element]) => element && !element.hidden)?.[0] || 'list';

        if (!languageObserver && document.documentElement) {
            languageObserver = new MutationObserver((mutations) => {
                const langChanged = mutations.some((mutation) => mutation.type === 'attributes' && mutation.attributeName === 'lang');
                if (!langChanged || !state.initialized) {
                    return;
                }
                if (state.view === 'list') {
                    populateFilterOptions();
                    renderProvidersList();
                    return;
                }
                if (state.view === 'select') {
                    renderAvailableProviders();
                    return;
                }
                if (state.view === 'form') {
                    const currentValues = getCurrentFormValues();
                    renderSchema(state.schemaSections, currentValues || {});
                    return;
                }
                if (!dom.import.overlay?.hidden && state.importProviders.length) {
                    renderImportProvidersList();
                }
            });
            languageObserver.observe(document.documentElement, {
                attributes: true,
                attributeFilter: ['lang'],
            });
        }
        if (state.initialized) {
            setView(state.view);
            loadProviders();
            return;
        }
        state.initialized = true;
        setView(visibleView);
        bindEvents();
        registerEscapeShortcut();
        loadProviders();
    };

    window.initWebsearchProvidersPage = init;
})();
