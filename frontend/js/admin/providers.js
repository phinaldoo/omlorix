// Elements
const providerListSelector = '#page-providers .provider-list';
const providerFilterSelect = document.getElementById('providerFilterSelect');
const providerSearchInput = document.getElementById('providerSearchInput');
const providerSearchClearButton = document.getElementById('providerSearchClear');
const exportProvidersButton = document.getElementById('exportProvidersButton');
const importProvidersButton = document.getElementById('importProvidersButton');
const importProvidersFileInput = document.getElementById('importProvidersFileInput');
const importProvidersOverlay = document.getElementById('importProvidersOverlay');
const importProvidersClose = document.getElementById('importProvidersClose');
const importProvidersCancel = document.getElementById('importProvidersCancel');
const importProvidersConfirm = document.getElementById('importProvidersConfirm');
const importProvidersList = document.getElementById('importProvidersList');
const importProvidersSelectAll = document.getElementById('importProvidersSelectAll');
const importProvidersFileName = document.getElementById('importProvidersFileName');
const importProvidersStatus = document.getElementById('importProvidersStatus');
const deleteProviderOverlay = document.getElementById('deleteProviderOverlay');
const deleteProviderCancelButton = document.getElementById('deleteProviderCancelButton');
const deleteProviderPrimaryButton = document.getElementById('deleteProviderPrimaryButton');
const deleteProviderPrimaryText = document.getElementById('deleteProviderPrimaryText');
const deleteProviderMessage = deleteProviderOverlay?.querySelector('.delete-warning-card-desc')
    || deleteProviderOverlay?.querySelector('.delete-account-content p');
let defaultDeleteProviderMessage = deleteProviderMessage?.textContent?.trim() || '';
let defaultDeleteProviderPrimaryText = deleteProviderPrimaryText?.textContent?.trim() || '';

// Provider Group Warning Modal Elements
const deleteProviderGroupWarningOverlay = document.getElementById('deleteProviderGroupWarningOverlay');
const deleteProviderGroupWarningTitle = document.getElementById('deleteProviderGroupWarningTitle');
const deleteProviderGroupWarningDesc = document.getElementById('deleteProviderGroupWarningDesc');
const deleteProviderGroupWarningList = document.getElementById('deleteProviderGroupWarningList');
const deleteProviderGroupWarningCancelButton = document.getElementById('deleteProviderGroupWarningCancelButton');
const deleteProviderGroupWarningConfirmButton = document.getElementById('deleteProviderGroupWarningConfirmButton');
const deleteProviderGroupWarningConfirmText = document.getElementById('deleteProviderGroupWarningConfirmText');

// Variables
let providersCache = [];
let deleteProviderId = null;
let providersInitialized = false;
let providersLanguageObserver = null;
let importProvidersState = {
    payload: null,
    providers: [],
    selected: new Set(),
    fileName: '',
    apiKeys: {}
};
const OPTIONAL_API_KEY_PROVIDER_KEYS = new Set([
    'ollama',
    'lmstudio',
    'anthropic_base',
]);

const t = window.adminT || ((key, fallback) => {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
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

defaultDeleteProviderMessage = defaultDeleteProviderMessage
    || t('modal_delete_provider_desc', 'Are you sure you want to delete this provider? This will also delete all models associated with this provider.');
defaultDeleteProviderPrimaryText = defaultDeleteProviderPrimaryText
    || t('modal_delete_provider_btn', 'Delete Provider');


const {
    fetchProvidersList,
    buildResponseError,
} = window.providersApi || {};

const fallbackFormatProviderLabel = (providerKey = '') => {
    const rawKey = (providerKey || '').toString().trim();
    const key = rawKey.toLowerCase();
    if (!key) {
        return '';
    }
    const mapped = window.PROVIDER_LABEL_MAP?.[key];
    if (mapped) {
        return mapped;
    }
    if (/[A-Z]/.test(rawKey) && !/[_\-]/.test(rawKey)) {
        return rawKey;
    }
    return key
        .split(/[_\-]/)
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
};

function updateProviderSearchClearVisibility() {
    if (!providerSearchInput || !providerSearchClearButton) {
        return;
    }
    const hasValue = providerSearchInput.value && providerSearchInput.value.trim().length > 0;
    providerSearchClearButton.hidden = !hasValue;
}

function handleProviderSearchInput() {
    updateProviderSearchClearVisibility();
    applyProviderFilters();
}

function handleProviderSearchClear(event) {
    event.preventDefault();
    if (!providerSearchInput) {
        return;
    }
    providerSearchInput.value = '';
    providerSearchInput.focus();
    providerSearchInput.dispatchEvent(new Event('input', { bubbles: true }));
}

function resolveProviderIconMarkup(iconValue, providerKey) {
    const normalizedProviderKey = String(providerKey || '').trim().toLowerCase();
    const defaultIconKey = typeof window.getDefaultProviderIconKey === 'function'
        ? window.getDefaultProviderIconKey(normalizedProviderKey)
        : normalizedProviderKey;
    const supportsCustomIcon = typeof window.providerSupportsCustomIcon === 'function'
        ? window.providerSupportsCustomIcon(normalizedProviderKey)
        : ['openai_responses', 'openai_chat_completions', 'anthropic_base'].includes(normalizedProviderKey);
    const displayIconValue = supportsCustomIcon ? iconValue : defaultIconKey;
    const fallback = Icons?.[defaultIconKey] || Icons?.omlorix || '';
    if (window.IconPicker?.renderIconMarkup) {
        return window.IconPicker.renderIconMarkup(displayIconValue, {
            fallback,
            imageAlt: t('providers_icon_alt', 'Provider icon'),
        });
    }

    if (typeof displayIconValue !== 'string') {
        return fallback;
    }
    const trimmed = displayIconValue.trim();
    if (!trimmed) {
        return fallback;
    }
    if (trimmed.startsWith('<')) {
        if (window.ChatSanitizer?.sanitizeSvg) {
            return window.ChatSanitizer.sanitizeSvg(trimmed) || fallback;
        }
        if (window.DOMPurify?.sanitize) {
            return window.DOMPurify.sanitize(trimmed, {
                USE_PROFILES: { svg: true },
                FORBID_ATTR: ['style', 'srcdoc'],
                ALLOW_DATA_ATTR: false,
            }) || fallback;
        }
        return fallback;
    }
    return Icons?.[trimmed] || fallback;
}

function upgradeProviderFilterSelect() {
    window.upgradeAdminSingleSelect?.(providerFilterSelect, {
        key: 'providers-filter',
        placeholder: t('providers_filter_all', 'All providers')
    });
}

const resolveProviderLabel = (providerKey = '') => {
    const globalFormatter = (typeof window !== 'undefined') ? window.formatProviderLabel : undefined;
    if (typeof globalFormatter === 'function' && globalFormatter !== resolveProviderLabel) {
        return globalFormatter(providerKey);
    }
    return fallbackFormatProviderLabel(providerKey);
};

function initProvidersPage() {
    if (!providersLanguageObserver && document.documentElement) {
        providersLanguageObserver = new MutationObserver((mutations) => {
            const langChanged = mutations.some((mutation) => mutation.type === 'attributes' && mutation.attributeName === 'lang');
            if (langChanged && providersInitialized) {
                applyProviderFilters();
            }
        });
        providersLanguageObserver.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ['lang'],
        });
    }
    if (providersInitialized) {
        loadProvidersList();
        return;
    }

    providersInitialized = true;
    bindProviderFilters();
    setupProviderActions();
    bindImportExportActions();
    loadProvidersList();
}


function resolveProvidersFromPayload(payload) {
    if (!payload || typeof payload !== 'object') {
        notifyError(t('providers_import_invalid_export', 'Invalid export file.'));
        return [];
    }
    if (payload.export_type !== 'llm_provider') {
        notifyError(t('providers_import_unsupported_type', 'Unsupported export file type.'));
        return [];
    }
    if (payload.export_version !== currentLlmsExportVersion()) {
        notifyError(t('providers_import_version_mismatch', 'Unsupported export version. Expected 1.0.'));
        return [];
    }
    const providers = payload?.data?.providers;
    return Array.isArray(providers) ? providers.map(sanitizeImportedProviderEntry) : [];
}

function currentLlmsExportVersion() {
    return 1.0;
}

function sanitizeImportedProviderEntry(provider) {
    if (!provider || typeof provider !== 'object') {
        return provider;
    }
    const sanitized = { ...provider };
    delete sanitized.api_key;
    return sanitized;
}

function providerRequiresImportApiKey(provider) {
    const explicitRequirement = provider?.credentials?.api_key_required;
    if (typeof explicitRequirement === 'boolean') {
        return explicitRequirement;
    }
    const providerKey = String(provider?.provider || '').trim();
    return providerKey ? !OPTIONAL_API_KEY_PROVIDER_KEYS.has(providerKey) : true;
}

function getImportApiKey(index) {
    return importProvidersState.apiKeys?.[String(index)] || '';
}

function setImportApiKey(index, value) {
    importProvidersState.apiKeys = {
        ...(importProvidersState.apiKeys || {}),
        [String(index)]: value,
    };
}

function getImportApiKeyInputId(index) {
    return `provider-import-api-key-${index}`;
}

function handleImportApiKeyInput(event) {
    const index = Number.parseInt(event.currentTarget.dataset.providerIndex, 10);
    if (Number.isNaN(index)) {
        return;
    }
    setImportApiKey(index, event.currentTarget.value || '');
}

function handleImportProviderEntryClick(event) {
    const target = event.target;
    if (!(target instanceof Element)) {
        return;
    }
    if (target.closest('input, button, a, select, textarea, label, .provider-import-credential-field')) {
        return;
    }

    const checkbox = event.currentTarget.querySelector('input[type="checkbox"][data-provider-index]');
    if (!(checkbox instanceof HTMLInputElement)) {
        return;
    }

    checkbox.checked = !checkbox.checked;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
}

function formatValidationErrorDetail(detail) {
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
}

function formatImportErrorEntry(entry) {
    if (!entry || typeof entry !== 'object') {
        return '';
    }

    const rawIndex = entry.index !== undefined ? Number(entry.index) : NaN;
    const displayIndex = Number.isFinite(rawIndex) ? rawIndex + 1 : '?';
    const name = entry.name ? ` (${entry.name})` : '';

    const errorDetail = entry.error;
    const message = formatValidationErrorDetail(errorDetail) || 'Unknown error.';

    return `• Item ${displayIndex}${name}: ${message}`;
}

function openImportProvidersModal() {
    if (!importProvidersOverlay) {
        return;
    }
    importProvidersOverlay.hidden = false;
    importProvidersOverlay.classList.add('active');
    setImportStatus();
    if (importProvidersFileName) {
        importProvidersFileName.textContent = importProvidersState.fileName || '';
    }
    if (importProvidersSelectAll) {
        importProvidersSelectAll.checked = importProvidersState.providers.length === importProvidersState.selected.size;
    }
    importProvidersConfirm?.focus();
}

function closeImportProvidersModal() {
    importProvidersOverlay?.classList.remove('active');
    if (importProvidersOverlay) {
        importProvidersOverlay.hidden = true;
    }
    importProvidersState = {
        payload: null,
        providers: [],
        selected: new Set(),
        fileName: '',
        apiKeys: {}
    };
    if (importProvidersList) {
        importProvidersList.innerHTML = '';
    }
    if (importProvidersFileName) {
        importProvidersFileName.textContent = '';
    }
    if (importProvidersSelectAll) {
        importProvidersSelectAll.checked = false;
    }
    setImportStatus();
}

function renderImportProvidersList() {
    if (!importProvidersList) {
        return;
    }
    importProvidersList.innerHTML = '';

    const { providers, selected } = importProvidersState;
    if (!providers.length) {
        const emptyState = document.createElement('div');
        emptyState.className = 'provider-import-empty';
        emptyState.textContent = t('providers_import_empty', 'No providers found in this file.');
        importProvidersList.appendChild(emptyState);
        return;
    }

    const fragment = document.createDocumentFragment();

    providers.forEach((provider, index) => {
        const entry = document.createElement('div');
        entry.className = 'provider-import-entry';
        entry.setAttribute('role', 'option');
        entry.setAttribute('aria-selected', selected.has(index) ? 'true' : 'false');
        entry.addEventListener('click', handleImportProviderEntryClick);

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = selected.has(index);
        checkbox.dataset.providerIndex = String(index);
        checkbox.setAttribute('aria-label', formatT('providers_import_select_provider_aria', 'Select {name}', {
            name: provider?.name || t('providers_import_unnamed', '(Unnamed provider)'),
        }));
        checkbox.addEventListener('change', handleImportProviderToggle);
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
            provider: resolveProviderLabel(provider?.provider) || t('common_unknown', 'Unknown'),
        });
        meta.appendChild(providerMeta);
        if (provider?.settings?.base_url) {
            const baseUrlMeta = document.createElement('span');
            baseUrlMeta.textContent = formatT('providers_import_base_url_meta', 'Base URL: {url}', {
                url: provider.settings.base_url,
            });
            meta.appendChild(baseUrlMeta);
        }
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

        if (providerRequiresImportApiKey(provider)) {
            const credentialField = document.createElement('div');
            credentialField.className = 'provider-import-credential-field';

            const credentialLabel = document.createElement('label');
            credentialLabel.className = 'settings-field-label';
            credentialLabel.setAttribute('for', getImportApiKeyInputId(index));
            credentialLabel.textContent = t('providers_import_api_key_label', 'API key');
            credentialField.appendChild(credentialLabel);

            const credentialInput = document.createElement('input');
            credentialInput.type = 'password';
            credentialInput.className = 'admin-form-input provider-import-api-key-input';
            credentialInput.id = getImportApiKeyInputId(index);
            credentialInput.dataset.providerIndex = String(index);
            credentialInput.value = getImportApiKey(index);
            credentialInput.autocomplete = 'off';
            credentialInput.spellcheck = false;
            credentialInput.placeholder = t('providers_import_api_key_placeholder', 'Enter a new API key');
            credentialInput.setAttribute('aria-required', 'true');
            credentialInput.addEventListener('input', handleImportApiKeyInput);
            credentialField.appendChild(credentialInput);

            const credentialHint = document.createElement('p');
            credentialHint.className = 'provider-import-entry-meta provider-import-credential-hint';
            credentialHint.textContent = t('providers_import_api_key_hint', 'Exports do not include stored API keys.');
            credentialField.appendChild(credentialHint);

            content.appendChild(credentialField);
        }

        entry.appendChild(content);
        fragment.appendChild(entry);
    });

    importProvidersList.appendChild(fragment);
}

function handleImportProviderToggle(event) {
    const checkbox = event.currentTarget;
    const index = Number.parseInt(checkbox.dataset.providerIndex, 10);
    if (Number.isNaN(index)) {
        return;
    }

    if (checkbox.checked) {
        importProvidersState.selected.add(index);
    } else {
        importProvidersState.selected.delete(index);
    }

    checkbox.closest('.provider-import-entry')?.setAttribute('aria-selected', checkbox.checked ? 'true' : 'false');

    if (importProvidersSelectAll) {
        importProvidersSelectAll.checked = importProvidersState.selected.size === importProvidersState.providers.length;
    }
    setImportStatus();
}

function toggleSelectAllImports(event) {
    const { checked } = event.currentTarget;
    const { providers, selected } = importProvidersState;
    selected.clear();
    if (checked) {
        providers.forEach((_, index) => selected.add(index));
    }
    renderImportProvidersList();
    setImportStatus();
}

function setImportStatus(message = '', type = '') {
    if (!importProvidersStatus) {
        return;
    }
    if (!message) {
        importProvidersStatus.hidden = true;
        importProvidersStatus.textContent = '';
        importProvidersStatus.className = 'provider-import-status';
        return;
    }
    importProvidersStatus.hidden = false;
    importProvidersStatus.textContent = message;
    importProvidersStatus.className = `provider-import-status ${type}`.trim();
}

async function submitSelectedImports() {
    const { providers, selected, payload } = importProvidersState;
    if (!payload) {
        setImportStatus(t('providers_import_choose_file_first', 'Please choose a provider file first.'), '');
        return;
    }
    if (!selected.size) {
        setImportStatus(t('providers_import_select_one', 'Select at least one provider to import.'), '');
        return;
    }

    try {
        setButtonLoadingState(importProvidersConfirm, true, t('admin_importing_ellipsis', 'Importing...'));
        const indices = Array.from(selected).sort((a, b) => a - b);
        const missingKeyIndex = indices.find((index) => {
            const provider = providers[index];
            return provider && providerRequiresImportApiKey(provider) && !getImportApiKey(index).trim();
        });
        if (missingKeyIndex !== undefined) {
            const provider = providers[missingKeyIndex];
            const message = formatT('providers_import_missing_api_key', 'Enter an API key for {name}.', {
                name: provider?.name || t('providers_import_unnamed', '(Unnamed provider)'),
            });
            setImportStatus(message, '');
            document.getElementById(getImportApiKeyInputId(missingKeyIndex))?.focus();
            return;
        }

        const filteredProviders = indices.map((index) => {
            const provider = providers[index];
            if (!provider) {
                return null;
            }
            const providerPayload = sanitizeImportedProviderEntry(provider);
            if (providerRequiresImportApiKey(providerPayload)) {
                providerPayload.api_key = getImportApiKey(index).trim();
            }
            return providerPayload;
        }).filter(Boolean);

        const filteredPayload = {
            ...payload,
            data: {
                ...(payload.data || {}),
                providers: filteredProviders
            }
        };

        const response = await authedFetch('/api/v1/llm/providers/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(filteredPayload)
        });

        if (!response.ok) {
            throw await buildResponseError(response, 'Failed to import providers.');
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

        await loadProvidersList();

        if (!errorCount) {
            closeImportProvidersModal();
        }
    } catch (error) {
        console.error('Failed to import providers', error);
        setImportStatus(error?.message || t('providers_import_failed', 'Failed to import providers.'), '');
        notifyError(error?.message || t('providers_import_failed', 'Failed to import providers.'));
    } finally {
        setButtonLoadingState(importProvidersConfirm, false);
    }
}


async function loadProvidersList() {
    renderProvidersList([], { message: t('providers_loading', 'Loading providers…'), isLoading: true });
    try {
        providersCache = await fetchProvidersList();
    } catch (error) {
        providersCache = [];
        notifyError(error?.message || t('providers_fetch_list_failed', 'Failed to fetch provider list'));
    }
    populateProviderFilterOptions(providersCache);
    applyProviderFilters();
}

function populateProviderFilterOptions(providers = []) {
    const previousValue = providerFilterSelect.value || 'all';
    const providerTypes = [...new Set(providers.map((provider) => provider?.provider).filter(Boolean))].sort();
    const options = ['all', ...providerTypes]
        .map((type) => `<option value="${type}">${type === 'all' ? t('providers_filter_all', 'All providers') : formatProviderType(type)}</option>`)
        .join('');
    providerFilterSelect.innerHTML = options;
    providerFilterSelect.value = providerTypes.includes(previousValue) ? previousValue : 'all';
    upgradeProviderFilterSelect();
}

function bindProviderFilters() {
    if (providerFilterSelect && providerFilterSelect.dataset.bound !== 'true') {
        providerFilterSelect.addEventListener('change', applyProviderFilters);
        providerFilterSelect.dataset.bound = 'true';
        upgradeProviderFilterSelect();
    }

    if (providerSearchInput && providerSearchInput.dataset.bound !== 'true') {
        providerSearchInput.addEventListener('input', handleProviderSearchInput);
        providerSearchInput.dataset.bound = 'true';
        updateProviderSearchClearVisibility();
    }

    if (providerSearchClearButton && providerSearchClearButton.dataset.bound !== 'true') {
        providerSearchClearButton.addEventListener('click', handleProviderSearchClear);
        providerSearchClearButton.dataset.bound = 'true';
        updateProviderSearchClearVisibility();
    }
}

function applyProviderFilters() {
    if (!Array.isArray(providersCache) || !providersCache.length) {
        renderProvidersList([]);
        return;
    }

    const selectedType = providerFilterSelect?.value || 'all';
    const searchTerm = providerSearchInput?.value?.trim().toLowerCase() || '';

    const filtered = providersCache.filter((provider) => {
        const matchesType = selectedType === 'all' || provider?.provider === selectedType;
        if (!matchesType) {
            return false;
        }

        if (!searchTerm) {
            return true;
        }

        const providerKey = provider?.provider?.toLowerCase() || '';
        const providerName = provider?.name?.toLowerCase() || '';
        return providerKey.includes(searchTerm) || providerName.includes(searchTerm);
    });

    if (!filtered.length) {
        const hasFilters = selectedType !== 'all' || Boolean(searchTerm);
        renderProvidersList([], hasFilters ? { message: t('providers_empty_filtered', 'No providers match your filters'), description: '' } : {});
        return;
    }

    renderProvidersList(filtered);
}


function getProvidersListContainer() {
    return document.querySelector(providerListSelector);
}

function renderProvidersLoadingState(container, message) {
    if (!container) {
        return;
    }

    container.innerHTML = '';

    const loadingState = window.createAdminLoadingPlaceholder({
        message,
        className: '',
    });
    container.appendChild(loadingState);
}

function renderProvidersList(providers = [], options = {}) {
    const { message, description } = options;
    const listContainer = getProvidersListContainer();
    if (!listContainer) {
        return;
    }

    listContainer.querySelectorAll('.provider-table-header, .provider-row, .provider-empty-state, .user-notifications-empty, .admin-loading-placeholder').forEach((row) => row.remove());

    if (options?.isLoading) {
        renderProvidersLoadingState(listContainer, message || t('providers_loading', 'Loading providers…'));
        return;
    }

    if (message || !providers.length) {
        const defaultDescription = t('providers_empty_desc', 'Connect a provider in the admin backend to see it listed here.');
        const descriptionText = description === undefined
            ? (!message ? defaultDescription : '')
            : description;

        const emptyState = window.createAdminEmptyPlaceholder({
            title: message || t('providers_empty_title', 'No providers yet'),
            description: descriptionText,
            icon: Icons?.omlorix || '',
            className: 'provider-empty-state',
        });

        if (emptyState) {
            listContainer.appendChild(emptyState);
        }
        return;
    }

    const header = document.createElement('div');
    header.className = 'provider-table-header';

    const headerCells = [
        { className: 'header-icon', text: t('table_header_icon', 'Icon') },
        { className: 'header-provider', text: t('table_header_provider_name', 'Provider Name') },
        { className: 'header-custom', text: t('table_header_custom_name', 'Custom Name') },
        { className: 'header-status', text: t('table_header_status', 'Status') },
        { className: 'header-actions', text: t('table_header_actions', 'Actions') }
    ];

    headerCells.forEach(({ className, text }) => {
        const cell = document.createElement('div');
        cell.className = className;
        cell.textContent = text;
        header.appendChild(cell);
    });

    listContainer.appendChild(header);

    const fragment = document.createDocumentFragment();

    providers.forEach((provider) => {
        const row = document.createElement('div');
        row.className = 'provider-row';
        if (provider.id) {
            row.dataset.providerId = provider.id;
        }
        if (provider?.provider) {
            row.dataset.providerKey = provider.provider;
        }
        if (provider?.name) {
            row.dataset.providerName = provider.name;
        }

        const providerKey = (provider.provider || '').toLowerCase();
        const providerLabel = formatProviderType(provider.provider);

        const iconCell = document.createElement('div');
        iconCell.className = 'provider-icon';
        iconCell.setAttribute('aria-hidden', 'true');
        const iconValue = provider.icon || providerKey;
        iconCell.innerHTML = resolveProviderIconMarkup(iconValue, providerKey);
        row.appendChild(iconCell);

        const providerNameCell = document.createElement('div');
        providerNameCell.className = 'provider-name';
        providerNameCell.dataset.label = t('table_header_provider_name', 'Provider Name');
        providerNameCell.textContent = providerLabel;
        row.appendChild(providerNameCell);

        const customNameCell = document.createElement('div');
        customNameCell.className = 'provider-custom';
        customNameCell.dataset.label = t('table_header_custom_name', 'Custom Name');
        customNameCell.textContent = provider?.name || '—';
        row.appendChild(customNameCell);

        const statusCell = document.createElement('div');
        statusCell.className = 'provider-status';
        statusCell.dataset.label = t('table_header_status', 'Status');
        const availability = (provider?.status?.available || '').toLowerCase();
        let statusState = 'inactive';
        if (availability === 'up') {
            statusState = 'active';
        } else if (availability === 'unknown') {
            statusState = 'unknown';
        }
        const statusIndicator = document.createElement('div');
        statusIndicator.className = `status-indicator status-${statusState}`;
        statusIndicator.setAttribute('aria-hidden', 'true');
        const statusIcon = statusState === 'active'
            ? Icons?.check
            : statusState === 'unknown'
                ? Icons?.question
                : Icons?.close;
        statusIndicator.innerHTML = statusIcon;
        statusCell.appendChild(statusIndicator);
        const statusLabel = document.createElement('span');
        statusLabel.className = 'provider-status-label';
        statusLabel.textContent = statusState === 'active'
            ? t('users_status_active', 'Active')
            : statusState === 'unknown'
                ? t('service_status_unknown', 'Unknown')
                : t('users_status_inactive', 'Inactive');
        statusCell.appendChild(statusLabel);
        row.appendChild(statusCell);

        const actionsCell = document.createElement('div');
        actionsCell.className = 'provider-actions';
        actionsCell.dataset.label = t('table_header_actions', 'Actions');
        const editButton = document.createElement('button');
        editButton.type = 'button';
        editButton.className = 'action-btn edit-btn';
        editButton.title = t('db_action_edit', 'Edit');
        editButton.setAttribute('aria-label', editButton.title);
        if (provider?.id) {
            editButton.dataset.providerId = provider.id;
        }
        if (provider?.provider) {
            editButton.dataset.providerKey = provider.provider;
        }
        if (provider?.name) {
            editButton.dataset.providerName = provider.name;
        }
        editButton.innerHTML = Icons?.edit;
        actionsCell.appendChild(editButton);

        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'action-btn delete-btn';
        deleteButton.title = t('db_action_delete', 'Delete');
        deleteButton.setAttribute('aria-label', deleteButton.title);
        if (provider?.id) {
            deleteButton.dataset.providerId = provider.id;
        }
        if (provider?.provider) {
            deleteButton.dataset.providerKey = provider.provider;
        }
        if (provider?.name) {
            deleteButton.dataset.providerName = provider.name;
        }
        deleteButton.innerHTML = Icons?.trash;
        actionsCell.appendChild(deleteButton);

        row.appendChild(actionsCell);
        fragment.appendChild(row);
    });

    listContainer.appendChild(fragment);
}


function formatProviderType(providerKey = '') {
    return resolveProviderLabel(providerKey);
}





function setupProviderActions() {
    const listContainer = getProvidersListContainer();

    if (listContainer) {
        listContainer.addEventListener('click', (event) => {
            const deleteButton = event.target.closest('.delete-btn');
            if (!deleteButton) {
                const editButton = event.target.closest('.edit-btn');
                if (editButton) {
                    const providerId = editButton.dataset.providerId;
                    if (!providerId) {
                        notifyError(t('providers_delete_failed_resolve_id', 'Failed to resolve provider ID.'));
                        return;
                    }
                    const providerKey = editButton.dataset.providerKey || editButton.closest('.provider-row')?.dataset.providerKey;
                    if (!providerKey) {
                        notifyError(t('providers_delete_failed_resolve_type', 'Failed to resolve provider type.'));
                        return;
                    }
                    const providerName = editButton.dataset.providerName || editButton.closest('.provider-row')?.dataset.providerName || '';
                    if (typeof window.openEditProvider === 'function') {
                        window.openEditProvider(providerId, providerKey, { name: providerName });
                    }
                } else {
                    const row = event.target.closest('.provider-row');
                    const rowActions = event.target.closest('.provider-actions');
                    if (row && row.dataset.providerId && !rowActions && typeof window.openEditProvider === 'function') {
                        const providerKey = row.dataset.providerKey;
                        if (!providerKey) {
                            notifyError(t('providers_delete_failed_resolve_type', 'Failed to resolve provider type.'));
                            return;
                        }
                        window.openEditProvider(row.dataset.providerId, providerKey, { name: row.dataset.providerName || '' });
                    }
                }
                return;
            }

            const providerId = deleteButton.dataset.providerId;
            if (!providerId) {
                notifyError(t('providers_delete_failed_resolve_id', 'Failed to resolve provider ID.'));
                return;
            }

            const row = deleteButton.closest('.provider-row');
            const customName = row?.querySelector('.provider-custom')?.textContent?.trim();
            const providerName = customName && customName !== '—'
                ? customName
                : row?.querySelector('.provider-name')?.textContent?.trim() || '';

            openDeleteProviderModal(providerId, providerName);
        });
    }

    if (deleteProviderCancelButton) {
        deleteProviderCancelButton.addEventListener('click', closeDeleteProviderModal);
    }

    if (deleteProviderOverlay) {
        deleteProviderOverlay.addEventListener('click', (event) => {
            if (event.target === deleteProviderOverlay) {
                closeDeleteProviderModal();
            }
        });
    }

    if (deleteProviderPrimaryButton) {
        deleteProviderPrimaryButton.addEventListener('click', async () => {
            if (!deleteProviderId || deleteProviderPrimaryButton.disabled) {
                return;
            }

            const originalIconHtml = deleteProviderPrimaryButton.querySelector('svg')?.outerHTML || '';
            const restoreIcon = () => {
                if (!originalIconHtml) {
                    return;
                }
                const currentIcon = deleteProviderPrimaryButton.querySelector('svg');
                if (currentIcon) {
                    currentIcon.outerHTML = originalIconHtml;
                } else {
                    deleteProviderPrimaryButton.insertAdjacentHTML('afterbegin', originalIconHtml);
                }
            };

            deleteProviderPrimaryButton.disabled = true;
            if (deleteProviderPrimaryText) {
                deleteProviderPrimaryText.textContent = t('admin_deleting_ellipsis', 'Deleting...');
            }

            const currentIcon = deleteProviderPrimaryButton.querySelector('svg');
            if (currentIcon) {
                currentIcon.outerHTML = Icons.refresh;
            }

            const success = await deleteProvider(deleteProviderId);
            if (success) {
                restoreIcon();
                closeDeleteProviderModal();
            } else {
                deleteProviderPrimaryButton.disabled = false;
                restoreIcon();
                if (deleteProviderPrimaryText) {
                    deleteProviderPrimaryText.textContent = defaultDeleteProviderPrimaryText;
                }
            }
        });
    }

    // Provider Group Warning Modal handlers
    if (deleteProviderGroupWarningCancelButton) {
        deleteProviderGroupWarningCancelButton.addEventListener('click', closeDeleteProviderModal);
    }

    if (deleteProviderGroupWarningOverlay) {
        deleteProviderGroupWarningOverlay.addEventListener('click', (event) => {
            if (event.target === deleteProviderGroupWarningOverlay) {
                closeDeleteProviderModal();
            }
        });
    }

    if (deleteProviderGroupWarningConfirmButton) {
        deleteProviderGroupWarningConfirmButton.addEventListener('click', async () => {
            if (!deleteProviderId || deleteProviderGroupWarningConfirmButton.disabled) {
                return;
            }

            const originalIconHtml = deleteProviderGroupWarningConfirmButton.querySelector('svg')?.outerHTML || '';
            const restoreIcon = () => {
                if (!originalIconHtml) {
                    return;
                }
                const currentIcon = deleteProviderGroupWarningConfirmButton.querySelector('svg');
                if (currentIcon) {
                    currentIcon.outerHTML = originalIconHtml;
                } else {
                    deleteProviderGroupWarningConfirmButton.insertAdjacentHTML('afterbegin', originalIconHtml);
                }
            };

            deleteProviderGroupWarningConfirmButton.disabled = true;
            if (deleteProviderGroupWarningConfirmText) {
                deleteProviderGroupWarningConfirmText.textContent = t('admin_deleting_ellipsis', 'Deleting...');
            }

            const currentIcon = deleteProviderGroupWarningConfirmButton.querySelector('svg');
            if (currentIcon) {
                currentIcon.outerHTML = Icons.refresh;
            }

            // Delete provider with handleGroups=true to cascade the group changes
            const success = await deleteProvider(deleteProviderId, true);
            if (success) {
                restoreIcon();
                closeDeleteProviderModal();
            } else {
                deleteProviderGroupWarningConfirmButton.disabled = false;
                restoreIcon();
                if (deleteProviderGroupWarningConfirmText) {
                    deleteProviderGroupWarningConfirmText.textContent = t('websearch_provider_delete_btn', 'Delete Provider');
                }
            }
        });
    }
}

function bindImportExportActions() {
    if (exportProvidersButton && exportProvidersButton.dataset.bound !== 'true') {
        exportProvidersButton.addEventListener('click', handleExportProviders);
        exportProvidersButton.dataset.bound = 'true';
    }

    if (importProvidersButton && importProvidersButton.dataset.bound !== 'true') {
        importProvidersButton.addEventListener('click', () => importProvidersFileInput?.click());
        importProvidersButton.dataset.bound = 'true';
    }

    if (importProvidersFileInput && importProvidersFileInput.dataset.bound !== 'true') {
        importProvidersFileInput.addEventListener('change', handleImportProviders);
        importProvidersFileInput.dataset.bound = 'true';
    }

    if (importProvidersOverlay && importProvidersOverlay.dataset.bound !== 'true') {
        importProvidersOverlay.addEventListener('click', (event) => {
            if (event.target === importProvidersOverlay) {
                closeImportProvidersModal();
            }
        });
        importProvidersOverlay.dataset.bound = 'true';
    }

    if (importProvidersClose && importProvidersClose.dataset.bound !== 'true') {
        importProvidersClose.addEventListener('click', closeImportProvidersModal);
        importProvidersClose.dataset.bound = 'true';
    }

    if (importProvidersCancel && importProvidersCancel.dataset.bound !== 'true') {
        importProvidersCancel.addEventListener('click', closeImportProvidersModal);
        importProvidersCancel.dataset.bound = 'true';
    }

    if (importProvidersConfirm && importProvidersConfirm.dataset.bound !== 'true') {
        importProvidersConfirm.addEventListener('click', submitSelectedImports);
        importProvidersConfirm.dataset.bound = 'true';
    }

    if (importProvidersSelectAll && importProvidersSelectAll.dataset.bound !== 'true') {
        importProvidersSelectAll.addEventListener('change', toggleSelectAllImports);
        importProvidersSelectAll.dataset.bound = 'true';
    }
}

function setButtonLoadingState(button, isLoading, loadingLabel = t('admin_loading_ellipsis', 'Loading...')) {
    if (!button) {
        return;
    }
    const labelTarget = button.querySelector('span');
    const getCurrentLabel = () => (labelTarget ? labelTarget.textContent : button.textContent);
    const setLabel = (text) => {
        if (labelTarget) {
            labelTarget.textContent = text;
        } else {
            button.textContent = text;
        }
    };

    if (isLoading) {
        if (!button.dataset.originalLabel) {
            button.dataset.originalLabel = getCurrentLabel()?.trim() || '';
        }
        button.disabled = true;
        button.classList.add('loading');
        setLabel(loadingLabel);
    } else {
        button.disabled = false;
        button.classList.remove('loading');
        if (button.dataset.originalLabel !== undefined) {
            setLabel(button.dataset.originalLabel || '');
            delete button.dataset.originalLabel;
        }
    }
}

async function handleExportProviders() {
    try {
        setButtonLoadingState(exportProvidersButton, true, t('admin_exporting_ellipsis', 'Exporting...'));
        const response = await providersApi.exportProviders();
        if (!response.ok) {
            throw await buildResponseError(response, t('providers_export_failed_retry', 'Failed to export providers. Please try again.'));
        }

        const exportData = await response.json();
        const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
        const timestamp = new Date().toISOString().replace(/[:\.]/g, '-');
        const filename = `llm-providers-${timestamp}.json`;

        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);

        notifySuccess(t('providers_export_success', 'Provider export downloaded successfully.'));
    } catch (error) {
        console.error('Failed to export providers', error);
        notifyError(error?.message || t('providers_export_failed', 'Failed to export providers.'));
    } finally {
        setButtonLoadingState(exportProvidersButton, false);
    }
}

async function handleImportProviders(event) {
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
        } catch (parseError) {
            notifyError(t('providers_import_invalid_json', 'Invalid JSON file.'));
            return;
        }

        const providers = resolveProvidersFromPayload(payload);
        if (!providers.length) {
            notifyWarning(t('providers_import_empty', 'No providers found in this file.'));
            return;
        }

        importProvidersState = {
            payload,
            providers,
            selected: new Set(providers.map((_, index) => index)),
            fileName: file.name || 'providers.json',
            apiKeys: {}
        };

        renderImportProvidersList();
        openImportProvidersModal();
    } catch (error) {
        console.error('Failed to import providers', error);
        notifyError(error?.message || t('providers_import_failed', 'Failed to import providers.'));
    }
}

async function openDeleteProviderModal(providerId, providerName = '') {
    deleteProviderId = providerId;
    // Check if provider is part of any provider groups
    try {
        const membership = await providersApi.checkProviderGroupMembership(providerId);
        if (membership?.groups?.length > 0) {
            openProviderGroupWarningModal(providerName, membership.groups);
            return;
        }
    } catch (error) {
        console.warn('Failed to check provider group membership:', error);
        // Continue with regular delete modal if check fails
    }

    // No groups - show standard delete modal
    showStandardDeleteModal(providerName);
}

function showStandardDeleteModal(providerName = '') {
    if (deleteProviderMessage) {
        deleteProviderMessage.textContent = providerName
            ? formatT('providers_delete_confirm_named', 'Are you sure you want to delete "{name}"? This will also delete all models associated with this provider.', { name: providerName })
            : defaultDeleteProviderMessage;
    }
    if (deleteProviderPrimaryButton) {
        deleteProviderPrimaryButton.disabled = false;
    }
    if (deleteProviderPrimaryText) {
        deleteProviderPrimaryText.textContent = defaultDeleteProviderPrimaryText;
    }
    if (deleteProviderOverlay) {
        deleteProviderOverlay.hidden = false;
        deleteProviderOverlay.classList.add('active');
    }
    deleteProviderPrimaryButton?.focus();
}

function openProviderGroupWarningModal(providerName, groups) {
    if (!deleteProviderGroupWarningOverlay) {
        // Fallback to standard modal if warning modal doesn't exist
        showStandardDeleteModal(providerName);
        return;
    }

    // Update title
    if (deleteProviderGroupWarningTitle) {
        deleteProviderGroupWarningTitle.textContent = t('providers_delete_group_warning_title', 'Provider Used in Groups');
    }

    // Update description
    if (deleteProviderGroupWarningDesc) {
        const groupWord = groups.length === 1 ? 'group' : 'groups';
        deleteProviderGroupWarningDesc.textContent = providerName
            ? formatT('providers_delete_group_warning_desc_named', '"{name}" is part of {count} provider {groupWord}. Deleting this provider will affect these groups:', { name: providerName, count: groups.length, groupWord })
            : formatT('providers_delete_group_warning_desc_default', 'This provider is part of {count} provider {groupWord}. Deleting it will affect these groups:', { count: groups.length, groupWord });
    }

    // Render group list
    if (deleteProviderGroupWarningList) {
        deleteProviderGroupWarningList.innerHTML = '';
        const fragment = document.createDocumentFragment();

        groups.forEach((group) => {
            const item = document.createElement('div');
            item.className = 'delete-provider-group-warning-item';

            const otherCount = group.other_member_count || 0;
            const willDelete = otherCount < 2;
            const impactClass = willDelete ? 'impact-delete' : 'impact-remove';
            const badgeClass = willDelete ? 'badge-delete' : 'badge-remove';
            const badgeText = willDelete
                ? t('providers_delete_badge_deleted', 'Will be deleted')
                : t('providers_delete_badge_removed', 'Provider removed');
            const impactText = willDelete
                ? formatT('providers_delete_impact_delete', 'Remaining provider count after deletion: {count}. The group requires at least 2 providers and will be deleted along with its models.', { count: otherCount })
                : formatT('providers_delete_impact_remove', 'Remaining provider count in this group after deletion: {count}.', { count: otherCount });

            const iconHtml = getGroupIcon(group.icon);

            item.innerHTML = `
                <div class="delete-provider-group-warning-icon">${iconHtml}</div>
                <div class="delete-provider-group-warning-content">
                    <p class="delete-provider-group-warning-name">${escapeHtml(group.name)}</p>
                    <p class="delete-provider-group-warning-impact ${impactClass}">${escapeHtml(impactText)}</p>
                </div>
                <span class="delete-provider-group-warning-badge ${badgeClass}">${escapeHtml(badgeText)}</span>
            `;

            fragment.appendChild(item);
        });

        deleteProviderGroupWarningList.appendChild(fragment);
    }

    // Reset button state
    if (deleteProviderGroupWarningConfirmButton) {
        deleteProviderGroupWarningConfirmButton.disabled = false;
    }
    if (deleteProviderGroupWarningConfirmText) {
        deleteProviderGroupWarningConfirmText.textContent = t('websearch_provider_delete_btn', 'Delete Provider');
    }

    // Show modal
    deleteProviderGroupWarningOverlay.hidden = false;
    deleteProviderGroupWarningConfirmButton?.focus();
}

function getGroupIcon(iconKey) {
    const iconsMap = (typeof Icons !== 'undefined' && Icons) || (window?.Icons) || {};
    if (iconKey && iconsMap[iconKey]) {
        return iconsMap[iconKey];
    }
    return Icons.grid;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

function closeProviderGroupWarningModal() {
    if (deleteProviderGroupWarningOverlay) {
        deleteProviderGroupWarningOverlay.hidden = true;
    }
    if (deleteProviderGroupWarningConfirmButton) {
        deleteProviderGroupWarningConfirmButton.disabled = false;
    }
    if (deleteProviderGroupWarningConfirmText) {
        deleteProviderGroupWarningConfirmText.textContent = t('websearch_provider_delete_btn', 'Delete Provider');
    }
}

function closeDeleteProviderModal() {
    deleteProviderId = null;
    if (deleteProviderMessage) {
        deleteProviderMessage.textContent = defaultDeleteProviderMessage;
    }
    if (deleteProviderPrimaryButton) {
        deleteProviderPrimaryButton.disabled = false;
    }
    if (deleteProviderPrimaryText) {
        deleteProviderPrimaryText.textContent = defaultDeleteProviderPrimaryText;
    }
    if (deleteProviderOverlay) {
        deleteProviderOverlay.classList.remove('active');
        deleteProviderOverlay.hidden = true;
    }
    closeProviderGroupWarningModal();
}

async function deleteProvider(providerId, handleGroups = false) {
    try {
        const response = await providersApi.deleteProvider(providerId, handleGroups);
        if (response.ok) {
            const result = await response.json();
            const groupActions = result?.group_actions;
            
            if (groupActions) {
                const updatedCount = groupActions.updated_groups?.length || 0;
                const deletedCount = groupActions.deleted_groups?.length || 0;
                
                if (deletedCount > 0 && updatedCount > 0) {
                    notifySuccess(formatT('providers_delete_groups_updated_removed', 'Provider deleted. Updated groups: {updated}. Deleted groups: {deleted}.', {
                        updated: updatedCount,
                        deleted: deletedCount,
                    }));
                } else if (deletedCount > 0) {
                    notifySuccess(formatT('providers_delete_groups_deleted', 'Provider deleted. Deleted groups: {count}.', { count: deletedCount }));
                } else if (updatedCount > 0) {
                    notifySuccess(formatT('providers_delete_groups_updated', 'Provider deleted. Updated groups: {count}.', { count: updatedCount }));
                } else {
                    notifySuccess(t('providers_delete_success', 'Provider deleted successfully'));
                }
            } else {
                notifySuccess(t('providers_delete_success', 'Provider deleted successfully'));
            }
            
            await loadProvidersList();
            return true;
        }
        const error = await buildResponseError(response, t('providers_delete_failed', 'Failed to delete provider'));
        notifyError(error.message);
        return false;
    } catch (error) {
        notifyError(error?.message || t('providers_delete_failed', 'Failed to delete provider'));
        return false;
    }
}

if (typeof window !== 'undefined') {
    window.initProvidersPage = initProvidersPage;
}
