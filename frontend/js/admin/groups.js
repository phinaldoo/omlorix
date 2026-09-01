(function () {
    const dom = {
        listPage: document.getElementById('page-groups'),
        listContainer: document.getElementById('groupsList'),
        searchInput: document.getElementById('groupsSearchInput'),
        searchClear: document.getElementById('groupsSearchClear'),
        createButton: document.getElementById('groupCreateButton'),
        formPage: document.getElementById('page-groups-edit'),
        form: document.getElementById('groupForm'),
        formFields: document.getElementById('groupFormFields'),
        formSidebar: document.getElementById('groupSettingsSidebar'),
        formLoading: document.getElementById('groupFormLoading'),
        formBackButton: document.getElementById('groupFormBack'),
        formSubmitButton: document.getElementById('groupFormSubmit'),
        formTitle: document.getElementById('groupFormTitle'),
        formSubtitle: document.getElementById('groupFormSubtitle'),
        formAvatar: document.getElementById('groupSettingsAvatar'),
        exportButton: document.getElementById('groupExportButton'),
        importButton: document.getElementById('groupImportButton'),
        importFileInput: document.getElementById('groupImportFileInput'),
        importOverlay: document.getElementById('groupImportOverlay'),
        importClose: document.getElementById('groupImportClose'),
        importCancel: document.getElementById('groupImportCancel'),
        importConfirm: document.getElementById('groupImportConfirm'),
        importSelectAll: document.getElementById('groupImportSelectAll'),
        importList: document.getElementById('groupImportList'),
        importStatus: document.getElementById('groupImportStatus'),
        importFileName: document.getElementById('groupImportFileName'),
        importChooseFile: document.getElementById('groupImportChooseFile'),
        importHiddenFileInput: document.getElementById('groupImportHiddenFileInput'),
        importCount: document.getElementById('groupImportCount'),
        deleteOverlay: document.getElementById('deleteGroupOverlay'),
        deleteMessage: document.getElementById('deleteGroupMessage'),
        deleteCancelButton: document.getElementById('deleteGroupCancelButton'),
        deletePrimaryButton: document.getElementById('deleteGroupPrimaryButton'),
        deletePrimaryText: document.getElementById('deleteGroupPrimaryText'),
        defaultSettings: document.getElementById('groupsDefaultSettings'),
    };

    if (!dom.listPage || !dom.listContainer) {
        return;
    }

    const t = (key, fallback) =>
        (typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback ?? key)
            : fallback ?? key);

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

    const state = {
        initialized: false,
        groups: [],
        filtered: [],
        editingId: null,
        schema: [],
        schemaByPage: new Map(),
        activePage: null,
        sidebarButtons: new Map(),
        formValues: {},
        originalValues: {},
        pendingChanges: {},
        saveInFlight: false,
        savedDefaultGroupId: 'default',
        activeControllers: new Map(),
    };
    const UNSAVED_GUARD_ID = 'admin-groups-form-unsaved';
    let unsavedGuardRegistered = false;

    const SIDEBAR_ICONS = {
        general: Icons.admin_sidebar_general,
        management: Icons.groups,
        skills: Icons.admin_sidebar_skills,
        projects: Icons.folder,
        automations: Icons.admin_sidebar_automations,
        todo: Icons.admin_sidebar_todo,
        notes: Icons.admin_sidebar_notes,
        memories: Icons.admin_sidebar_memories,
        prompts: Icons.admin_sidebar_prompts,
        bookmarks: Icons.admin_sidebar_bookmarks,
        agents: Icons.admin_sidebar_agents, 
        byok: Icons.admin_sidebar_byok,
        temporary_access: Icons.admin_sidebar_temporary_access,
        sharing: Icons.admin_sidebar_sharing,
        chat: Icons.admin_sidebar_chat,
        context: Icons.admin_sidebar_context,
        data_controls: Icons.admin_sidebar_data_controls,
        files: Icons.admin_sidebar_files, 
        users: Icons.admin_sidebar_users,
        connections: Icons.admin_sidebar_connections,
        leaderboard: Icons.admin_sidebar_leaderboard,
        compliance: Icons.admin_sidebar_compliance,
        access_windows: Icons.admin_sidebar_access_windows,
        preferences: Icons.admin_sidebar_preferences,
        security: Icons.admin_sidebar_security,
        limits: Icons.admin_sidebar_limits,
        permissions: Icons.admin_sidebar_permissions,
        default: Icons.admin_sidebar_default
    };

    let importState = {
        payload: null,
        groups: [],
        selected: new Set(),
        fileName: '',
        errors: [],
    };
    let i18nUpdateHandlerBound = false;
    let defaultSettingsController = null;

    const groupsApi = {
        list: () => fetchAdminGroupsList(),
        create: (payload) =>
            fetchAdminJson(
                '/api/v1/groups/',
                {
                    method: 'POST',
                    body: payload,
                },
                'Failed to create group'
            ),
        update: (groupId, payload) =>
            fetchAdminJson(
                `/api/v1/groups/${encodeURIComponent(groupId)}`,
                {
                    method: 'PUT',
                    body: payload,
                },
                'Failed to update group'
            ),
        delete: (groupId) =>
            fetchAdminJson(
                `/api/v1/groups/${encodeURIComponent(groupId)}`,
                {
                    method: 'DELETE',
                },
                'Failed to delete group'
            ),
        duplicate: (groupId) =>
            fetchAdminJson(
                `/api/v1/groups/${encodeURIComponent(groupId)}/duplicate`,
                {
                    method: 'POST',
                },
                'Failed to duplicate group'
            ),
        form: (groupId) => {
            const params = new URLSearchParams();
            if (groupId) {
                params.set('group_id', groupId);
            }
            return fetchAdminJson(
                `/api/v1/groups/form${params.toString() ? `?${params.toString()}` : ''}`,
                {},
                'Failed to load group form'
            );
        },
    };

    function normalizeDefaultGroupId(value) {
        return typeof value === 'string' && value.trim() ? value.trim() : 'default';
    }

    function syncDefaultGroupFromValues(values = {}) {
        state.savedDefaultGroupId = normalizeDefaultGroupId(values.default_user_group);
        renderList();
    }

    function getDefaultSettingsController() {
        if (defaultSettingsController || !dom.defaultSettings || typeof window.createSettingsPageController !== 'function') {
            return defaultSettingsController;
        }
        defaultSettingsController = window.createSettingsPageController({
            pageKey: 'groups_defaults',
            containerId: dom.defaultSettings,
            loadErrorMessage: t('groups_default_load_failed', 'Failed to load default group setting.'),
            onError: (message) => notifyError?.(message),
            onLoad: syncDefaultGroupFromValues,
            onFieldSaved: ({ fieldKey, value }) => {
                if (fieldKey !== 'default_user_group') {
                    return;
                }
                syncDefaultGroupFromValues({ default_user_group: value });
                notifySuccess?.(t('groups_default_saved', 'Default group saved.'));
            },
        });
        return defaultSettingsController;
    }

    function initDefaultSettingsController({ reload = false } = {}) {
        const controller = getDefaultSettingsController();
        if (!controller) {
            return;
        }
        if (reload) {
            controller.teardown();
        }
        controller.init();
    }

    async function loadGroupFormSchema(groupId = null) {
        try {
            setFormLoading(true);
            const schema = await groupsApi.form(groupId);
            if (!schema) {
                showFormError(t('group_form_load_failed', 'Failed to load group form.'));
                return;
            }
            renderSchema(schema);
        } catch (error) {
            console.error('Failed to load group schema', error);
            showFormError(error?.message || t('group_form_load_failed', 'Failed to load group form.'));
        }
    }

    function showFormError(message) {
        setFormLoading(false);
        notifyError?.(message);
    }

    function setFormLoading(isLoading) {
        if (dom.formLoading) {
            dom.formLoading.hidden = !isLoading;
            dom.formLoading.style.display = isLoading ? 'flex' : 'none';
        }
        if (dom.formFields) {
            dom.formFields.hidden = Boolean(isLoading);
            dom.formFields.style.display = isLoading ? 'none' : '';
        }
    }

    function getIconForSection(key = '') {
        const normalized = String(key || '').trim().toLowerCase();
        return SIDEBAR_ICONS[normalized] || SIDEBAR_ICONS.default;
    }

    function getGroupNameFromState() {
        const explicitName = state.formValues?.[state.activePage]?.name;
        if (typeof explicitName === 'string' && explicitName.trim()) {
            return explicitName.trim();
        }
        for (const values of Object.values(state.formValues || {})) {
            if (typeof values?.name === 'string' && values.name.trim()) {
                return values.name.trim();
            }
        }
        const currentGroup = state.groups.find((group) => group.id === state.editingId);
        return currentGroup?.name?.trim() || '';
    }

    function getInitials(value, fallback = 'GR') {
        const parts = String(value || '')
            .trim()
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 2);
        if (!parts.length) {
            return fallback;
        }
        return parts.map((part) => part[0]?.toUpperCase() || '').join('') || fallback;
    }

    function updateFormSummary() {
        const groupName = getGroupNameFromState();
        if (dom.formAvatar) {
            dom.formAvatar.textContent = getInitials(groupName, state.editingId ? 'EG' : 'NG');
        }
        if (!dom.formTitle || !dom.formSubtitle) {
            return;
        }
        if (state.editingId) {
            dom.formTitle.textContent = t('group_form_title_edit', 'Edit Group');
            dom.formSubtitle.textContent = groupName
                ? `${t('group_form_subtitle_edit_prefix', 'Update settings for')} ${groupName}.`
                : t('group_form_subtitle_edit', 'Update the name or settings for this group.');
            return;
        }
        dom.formTitle.textContent = t('group_form_title_create', 'New Group');
        dom.formSubtitle.textContent = groupName
            ? `${t('group_form_subtitle_create_prefix', 'Create the group')} ${groupName} ${t('group_form_subtitle_create_suffix', 'and configure its settings.')}`
            : t('group_form_subtitle_create', 'Create a fresh group to control access for subsets of users.');
    }

    function renderSchema(schema) {
        if (!dom.formFields || !dom.formSidebar) {
            return;
        }
        const sections = normalizeSchemaSections(schema);
        state.schema = sections;
        state.schemaByPage = new Map(sections.map((section, index) => [section.key || `section-${index}`, section]));
        state.sidebarButtons.clear();
        state.formValues = {};
        state.originalValues = {};
        state.pendingChanges = {};
        state.activePage = null;
        dom.formFields.innerHTML = '';
        dom.formSidebar.innerHTML = '';
        setFormLoading(false);
        sections.forEach((section, index) => {
            const key = section.key || `section-${index}`;
            initializePageState(section, key);
        });
        updateFormSummary();

        if (!sections.length) {
            appendSidebarEmptyState();
            dom.formFields.innerHTML = `<p class="user-settings-empty">${t('group_form_no_schema', 'No group schema available.')}</p>`;
            return;
        }

        createSidebar(sections);
        setActivePage(sections[0].key || 'section-0');
    }

    function appendSidebarEmptyState() {
        if (!dom.formSidebar) {
            return;
        }
        const empty = document.createElement('p');
        empty.className = 'user-settings-sidebar-empty';
        empty.textContent = t('group_form_no_sections', 'No sections available.');
        dom.formSidebar.appendChild(empty);
    }

    function createSidebar(sections) {
        if (!dom.formSidebar) {
            return;
        }
        const fragment = document.createDocumentFragment();
        sections.forEach((section, index) => {
            const key = section.key || `section-${index}`;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'user-settings-nav-item';
            button.dataset.pageKey = key;

            const icon = document.createElement('span');
            icon.className = 'user-settings-nav-icon';
            icon.innerHTML = getIconForSection(key);
            icon.setAttribute('aria-hidden', 'true');
            button.appendChild(icon);

            const label = document.createElement('span');
            label.className = 'user-settings-nav-label';
            const sectionFallback = section.title || section.label || `${t('group_form_section_fallback', 'Section')} ${index + 1}`;
            label.textContent = section.i18n_title
                ? t(section.i18n_title, sectionFallback)
                : sectionFallback;
            button.appendChild(label);

            const indicator = document.createElement('span');
            indicator.className = 'user-settings-nav-indicator';
            indicator.setAttribute('aria-hidden', 'true');
            button.appendChild(indicator);

            button.addEventListener('click', () => setActivePage(key));
            fragment.appendChild(button);
            state.sidebarButtons.set(key, button);
        });
        dom.formSidebar.appendChild(fragment);
    }

    function rebuildSidebarForLanguage() {
        if (!dom.formSidebar) {
            return;
        }
        dom.formSidebar.innerHTML = '';
        state.sidebarButtons.clear();
        if (!state.schema.length) {
            appendSidebarEmptyState();
            return;
        }
        createSidebar(state.schema);
        state.sidebarButtons.forEach((button, key) => {
            button.classList.toggle('active', key === state.activePage);
        });
    }

    function refreshFormTranslations() {
        if (!dom.formPage || dom.formPage.hidden) {
            return;
        }
        updateFormSummary();
        if (!state.schema.length) {
            if (dom.formSidebar) {
                dom.formSidebar.innerHTML = `<p class="user-settings-sidebar-empty">${t('group_form_loading_schema', 'Loading group schema…')}</p>`;
            }
            return;
        }
        rebuildSidebarForLanguage();
        const activePageKey = state.activePage || state.schema[0]?.key || 'section-0';
        const pageSchema = state.schemaByPage.get(activePageKey);
        if (pageSchema) {
            state.activePage = activePageKey;
            renderPage(activePageKey, pageSchema);
        }
    }

    function setActivePage(pageKey) {
        if (!pageKey || state.activePage === pageKey) {
            return;
        }
        const pageSchema = state.schemaByPage.get(pageKey);
        if (!pageSchema) {
            dom.formFields.innerHTML = `<p class="user-settings-empty">${t('group_form_unknown_section', 'Unknown section.')}</p>`;
            return;
        }
        state.activePage = pageKey;
        state.sidebarButtons.forEach((button, key) => {
            button.classList.toggle('active', key === pageKey);
        });
        renderPage(pageKey, pageSchema);
    }

    function initializePageState(pageSchema, pageKey) {
        const fields = Array.isArray(pageSchema.fields) ? pageSchema.fields : [];
        const values = {};
        fields.forEach((field) => {
            if (!field?.key) {
                return;
            }
            // Store the safe marker for an existing redacted value. The input
            // remains visually empty, but an untouched full-form submission
            // can now be distinguished from an intentional clear.
            const maskedMarker = window.getMaskedFieldSubmissionMarker?.(field) ?? null;
            const initialValue = maskedMarker ?? field.value ?? field.default;
            values[field.key] = cloneSettingsValue(initialValue);
        });
        state.formValues[pageKey] = state.formValues[pageKey] || values;
        state.originalValues[pageKey] = state.originalValues[pageKey] || clonePageValues(values);
    }

    function renderPage(pageKey, pageSchema) {
        if (!dom.formFields) {
            return;
        }
        dom.formFields.innerHTML = '';
        const values = state.formValues[pageKey] || {};
        const fragment = document.createDocumentFragment();
        const controllers = new Map();

        const pageTitleFallback = pageSchema.title || pageSchema.label || pageSchema.key || t('group_form_untitled_section', 'Untitled section');
        const pageTitle = pageSchema.i18n_title
            ? t(pageSchema.i18n_title, pageTitleFallback)
            : pageTitleFallback;
        const pageDescription = pageSchema.description
            ? (pageSchema.i18n_description
                ? t(pageSchema.i18n_description, pageSchema.description)
                : pageSchema.description)
            : '';
        const section = createSettingsSection(pageTitle, pageDescription);
        fragment.appendChild(section.element);

        const fields = Array.isArray(pageSchema.fields) ? pageSchema.fields : [];
        if (!fields.length) {
            const empty = document.createElement('p');
            empty.className = 'user-settings-empty';
            empty.textContent = t('group_form_no_fields', 'No fields defined for this section.');
            section.body.appendChild(empty);
            dom.formFields.appendChild(fragment);
            return;
        }

        fields.forEach((field) => {
            if (!field?.key) {
                return;
            }
            const { row, controlWrapper } = createFieldLayout(field);
            const controlValue = values[field.key] ?? field.value ?? field.default;
            const maskedMarker = window.getMaskedFieldSubmissionMarker?.(field) ?? null;
            const renderedControlValue = maskedMarker !== null && controlValue === maskedMarker
                ? ''
                : controlValue;
            const fieldAttributes = field.attributes || null;
            const { root, control } = createFieldControl(field, {
                value: renderedControlValue,
                datasetKey: field.key,
                attributes: fieldAttributes,
            });
            control.id = `group-field-${field.key.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
            control.name = field.key;
            if (field.required) {
                control.required = true;
            }
            if (field.type === 'context_files' && state.editingId) {
                control.dataset.groupId = state.editingId;
            }
            controlWrapper.appendChild(root);
            section.body.appendChild(row);
            attachFieldHandlers(pageKey, field, control, row);
            const originalValue = state.originalValues[pageKey]?.[field.key];
            const isDirty = !valuesAreEqual(field, controlValue, originalValue);
            row.classList.toggle('user-settings-field-pending', Boolean(isDirty));

            controllers.set(field.key, { field, control, row });
        });

        dom.formFields.appendChild(fragment);
        state.activeControllers = controllers;
        attachDependencyListeners(controllers);
        updateDependentFieldsVisibility(controllers);
        window.WebsearchProviderLogic?.attachProviderPairLogic?.(
            Array.from(controllers.values()),
            {
                searchFieldKey: 'settings.chat.byok_default_search_provider',
                scrapeFieldKey: 'settings.chat.byok_default_scrape_provider',
                searchValueKey: 'byok_default_search_provider',
                scrapeValueKey: 'byok_default_scrape_provider',
            }
        );

        // Attach error clear listeners for validation
        const controlsArray = fields.map((field) => {
            const selector = `[name="${field.key}"], [data-setting-key="${field.key}"]`;
            const control = dom.formFields?.querySelector(selector);
            return control ? { field, control } : null;
        }).filter(Boolean);
        window.FieldValidation?.attachErrorClearListeners(controlsArray);
    }

    function createSettingsSection(title, description) {
        const section = document.createElement('section');
        section.className = 'settings-section';

        const header = document.createElement('div');
        header.className = 'settings-section-header';

        if (title) {
            const titleEl = document.createElement('h4');
            titleEl.className = 'settings-section-title';
            titleEl.textContent = title;
            header.appendChild(titleEl);
        }

        if (description) {
            const descEl = document.createElement('p');
            descEl.className = 'settings-section-description';
            descEl.textContent = description;
            header.appendChild(descEl);
        }

        if (header.childElementCount) {
            section.appendChild(header);
        }

        const body = document.createElement('div');
        body.className = 'settings-section-body';
        section.appendChild(body);

        return { element: section, body };
    }

    function attachDependencyListeners(controllers = state.activeControllers) {
        if (!controllers || !controllers.size) {
            return;
        }
        const dependencyKeys = new Set();
        controllers.forEach(({ field }) => {
            if (field.dependency) {
                dependencyKeys.add(field.dependency);
            }
            if (field.dependency2) {
                dependencyKeys.add(field.dependency2);
            }
        });
        if (!dependencyKeys.size) {
            return;
        }
        controllers.forEach(({ field, control }) => {
            if (!control || !dependencyKeys.has(field.key)) {
                return;
            }
            const handler = () => updateDependentFieldsVisibility(controllers);
            control.addEventListener('change', handler);
            if (field.type === 'string') {
                control.addEventListener('input', handler);
            }
            if (field.type === 'string_list') {
                control.addEventListener('keywordschange', handler);
            }
            if (field.type === 'access_rules') {
                control.addEventListener('ruleschange', handler);
            }
        });
    }

    function updateDependentFieldsVisibility(controllers = state.activeControllers) {
        if (!controllers || !controllers.size) {
            return;
        }
        controllers.forEach(({ field, row }) => {
            if (!row || (!field.dependency && !field.dependency2)) {
                return;
            }
            const visible = isDependencySatisfied(field, controllers);
            row.hidden = !visible;
            row.style.display = visible ? '' : 'none';
        });
        refreshByokWebsearchCombinedState(controllers);
        window.syncSectionBodyLastVisibleRow?.(dom.formFields);
    }

    function refreshByokWebsearchCombinedState(controllers = state.activeControllers) {
        if (!window.WebsearchProviderLogic?.refreshScrapeFieldStateForKeys) {
            return;
        }
        if (!controllers || !controllers.size) {
            return;
        }
        window.WebsearchProviderLogic.refreshScrapeFieldStateForKeys(
            Array.from(controllers.values()),
            {
                searchFieldKey: 'settings.chat.byok_default_search_provider',
                scrapeFieldKey: 'settings.chat.byok_default_scrape_provider',
                searchValueKey: 'byok_default_search_provider',
                scrapeValueKey: 'byok_default_scrape_provider',
            }
        );
    }

    function isDependencySatisfied(field, controllers = state.activeControllers) {
        const firstSatisfied = isSingleDependencySatisfied(field.dependency, field.dependency_value, controllers);
        if (!firstSatisfied) {
            return false;
        }
        return isSingleDependencySatisfied(field.dependency2, field.dependency2_value, controllers);
    }

    function isSingleDependencySatisfied(dependencyKey, requiredValue, controllers = state.activeControllers) {
        if (!dependencyKey) {
            return true;
        }
        if (!controllers || !controllers.size) {
            return true;
        }
        const entry = controllers.get(dependencyKey);
        if (!entry || !entry.control) {
            return true;
        }
        let currentValue;
        try {
            currentValue = readControlValue(entry.field, entry.control);
        } catch {
            currentValue = undefined;
        }
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
    }

    function clonePageValues(values = {}) {
        return Object.fromEntries(Object.entries(values).map(([key, value]) => [key, cloneSettingsValue(value)]));
    }

    function attachFieldHandlers(pageKey, field, control, row) {
        if (!control) {
            return;
        }
        const handler = () => {
            try {
                const normalizedValue = normalizeFieldValue(field, readControlValue(field, control));
                updateFieldValue(pageKey, field, normalizedValue, row);
            } catch (error) {
                notifyError(error?.message || t('group_form_validation_failed', 'Validation failed.'));
                revertFieldValue(pageKey, field, control);
            }
        };
        switch (field.type) {
            case 'boolean':
            case 'select':
            case 'number':
                control.addEventListener('change', handler);
                break;
            case 'access_rules':
                control.addEventListener('ruleschange', handler);
                break;
            case 'context_files':
                control.addEventListener('contextfileschange', handler);
                break;
            case 'string_list':
            case 'string':
            default:
                control.addEventListener('input', handler);
                control.addEventListener('blur', handler);
                break;
        }
        if (field.type === 'string_list' && control.dataset.keywordTags !== undefined) {
            control.addEventListener('keywordschange', handler);
        }
    }

    function revertFieldValue(pageKey, field, control) {
        if (!control) {
            return;
        }
        const currentValue = state.formValues[pageKey]?.[field.key];
        if (currentValue === undefined) {
            return;
        }
        applyControlValue(control, field, currentValue);
    }

    function readControlValue(field, control) {
        if (!control) {
            return null;
        }
        switch (field.type) {
            case 'boolean':
                return control.checked;
            case 'number': {
                const raw = control.value.trim();
                if (!raw) {
                    return null;
                }
                const parsed = Number(raw);
                if (Number.isNaN(parsed)) {
                    throw new Error(formatT(
                        'admin_field_must_be_valid_number',
                        '{field} must be a valid number.',
                        { field: field.label || field.key },
                    ));
                }
                return parsed;
            }
            case 'string_list': {
                if (control.dataset.keywordTags !== undefined) {
                    try {
                        return JSON.parse(control.dataset.keywordTags || '[]');
                    } catch (error) {
                        return [];
                    }
                }
                return control.value
                    .split('\n')
                    .map((line) => line.trim())
                    .filter(Boolean);
            }
            case 'access_rules': {
                if (control.dataset.accessRules !== undefined) {
                    try {
                        return JSON.parse(control.dataset.accessRules || '[]');
                    } catch (error) {
                        return [];
                    }
                }
                return [];
            }
            case 'context_files': {
                if (control.dataset.contextFiles !== undefined) {
                    try {
                        return JSON.parse(control.dataset.contextFiles || '[]');
                    } catch (error) {
                        return [];
                    }
                }
                return [];
            }
            case 'select':
                if (control.multiple || field.multiple) {
                    return Array.from(control.selectedOptions || []).map((option) => option.value);
                }
                return control.value?.trim() ?? '';
            case 'string':
            default:
                return control.value?.trim() ?? '';
        }
    }

    function normalizeFieldValue(field, value) {
        if (field.type === 'string_list' && !Array.isArray(value)) {
            return [];
        }
        if (field.type === 'access_rules' && !Array.isArray(value)) {
            return [];
        }
        if (field.type === 'context_files' && !Array.isArray(value)) {
            return [];
        }
        if (field.type === 'number' && typeof value === 'string') {
            const parsed = Number(value);
            if (Number.isNaN(parsed)) {
                throw new Error(formatT(
                    'admin_field_must_be_valid_number',
                    '{field} must be a valid number.',
                    { field: field.label || field.key },
                ));
            }
            return parsed;
        }
        return value;
    }

    function updateFieldValue(pageKey, field, nextValue, row) {
        state.formValues[pageKey] = state.formValues[pageKey] || {};
        state.formValues[pageKey][field.key] = cloneSettingsValue(nextValue);

        const originalValue = state.originalValues[pageKey]?.[field.key];
        const isDirty = !valuesAreEqual(field, nextValue, originalValue);

        if (isDirty) {
            state.pendingChanges[pageKey] = state.pendingChanges[pageKey] || {};
            state.pendingChanges[pageKey][field.key] = cloneSettingsValue(nextValue);
        } else if (state.pendingChanges[pageKey]) {
            delete state.pendingChanges[pageKey][field.key];
            if (!Object.keys(state.pendingChanges[pageKey]).length) {
                delete state.pendingChanges[pageKey];
            }
        }

        if (row) {
            row.classList.toggle('user-settings-field-pending', Boolean(isDirty));
        }

        if (field.key === 'name') {
            updateFormSummary();
        }
        updatePageDirtyIndicator(pageKey);
        updateSubmitButtonState();
        updateDependentFieldsVisibility(state.activeControllers);
    }

    function updatePageDirtyIndicator(pageKey) {
        const button = state.sidebarButtons.get(pageKey);
        if (!button) {
            return;
        }
        const hasDirtyFields = Boolean(state.pendingChanges[pageKey] && Object.keys(state.pendingChanges[pageKey]).length);
        button.dataset.dirty = hasDirtyFields ? 'true' : 'false';
    }

    function normalizeSchemaSections(schemaPayload) {
        if (!schemaPayload) {
            return [];
        }
        if (Array.isArray(schemaPayload.sections)) {
            return schemaPayload.sections;
        }
        if (Array.isArray(schemaPayload)) {
            return schemaPayload;
        }
        return [];
    }

    function updateSubmitButtonState() {
        if (!dom.formSubmitButton) {
            return;
        }
        if (state.saveInFlight) {
            dom.formSubmitButton.disabled = true;
            return;
        }
        const hasChanges = Object.keys(state.pendingChanges).length > 0;
        dom.formSubmitButton.disabled = !hasChanges;
    }

    function hasPendingFormChanges() {
        return Object.keys(state.pendingChanges).length > 0;
    }

    function buildPayloadFromSchema() {
        if (!Object.keys(state.formValues).length) {
            notifyError(t('group_form_not_ready', 'Form is not ready yet.'));
            return null;
        }

        const payload = {};
        const settingsPayload = {};
        Object.entries(state.formValues).forEach(([, values]) => {
            Object.entries(values || {}).forEach(([fieldKey, value]) => {
                const segments = fieldKey.split('.').filter(Boolean);
                if (!segments.length) {
                    return;
                }
                if (segments[0] === 'settings') {
                    setNestedValue(settingsPayload, segments.slice(1), value, true);
                } else {
                    setNestedValue(payload, segments, value, true);
                }
            });
        });

        if (Object.keys(settingsPayload).length) {
            payload.settings = settingsPayload;
        }

        if (!payload.name || typeof payload.name !== 'string' || !payload.name.trim()) {
            notifyError(t('group_form_name_required', 'Group name is required.'));
            return null;
        }

        return payload;
    }

    function validateManagerRoleSelections(payload) {
        const roleLists = [
            payload?.owner_user_ids || [],
            payload?.manager_user_ids || [],
            payload?.coordinator_user_ids || [],
        ];
        const selectedUsers = new Set();
        for (const userIds of roleLists) {
            for (const userId of userIds) {
                if (selectedUsers.has(userId)) {
                    notifyError(t(
                        'group_form_manager_role_conflict',
                        'Each user can only have one management role in a group.'
                    ));
                    return false;
                }
                selectedUsers.add(userId);
            }
        }
        return true;
    }

    function extractFieldValue(field, control) {
        switch (field.type) {
            case 'boolean':
                return Boolean(control.checked);
            case 'number': {
                const raw = control.value.trim();
                if (!raw) {
                    return null;
                }
                const parsed = Number(raw);
                if (Number.isNaN(parsed)) {
                    notifyError(formatT(
                        'admin_field_must_be_valid_number',
                        '{field} must be a valid number.',
                        { field: field.label || field.key },
                    ));
                    throw new Error('Invalid number');
                }
                return parsed;
            }
            case 'string_list': {
                if (control.dataset.keywordTags !== undefined) {
                    try {
                        return JSON.parse(control.dataset.keywordTags || '[]');
                    } catch (error) {
                        return [];
                    }
                }
                return control.value
                    .split('\n')
                    .map((line) => line.trim())
                    .filter(Boolean);
            }
            case 'select':
            case 'string':
            default:
                return control.value?.trim() ?? '';
        }
    }

    function setNestedValue(target, segments, value, ensureRoot = false) {
        if (!segments.length) {
            return;
        }
        let current = target;
        segments.forEach((segment, index) => {
            if (index === segments.length - 1) {
                if (ensureRoot && segment === 'settings' && typeof current[segment] !== 'object') {
                    current[segment] = {};
                }
                if (value === undefined) {
                    return;
                }
                if (index === segments.length - 1) {
                    if (segments.length === 1) {
                        current[segment] = value;
                    } else {
                        current[segment] = value;
                    }
                }
                return;
            }
            if (typeof current[segment] !== 'object' || current[segment] === null) {
                current[segment] = {};
            }
            current = current[segment];
        });
    }

    function updateSearchClearVisibility() {
        if (!dom.searchInput || !dom.searchClear) {
            return;
        }
        const hasValue = dom.searchInput.value && dom.searchInput.value.trim().length > 0;
        dom.searchClear.hidden = !hasValue;
    }

    function handleSearchInput() {
        updateSearchClearVisibility();
        applyFilters();
    }

    function handleSearchClear(event) {
        event.preventDefault();
        if (!dom.searchInput) {
            return;
        }
        dom.searchInput.value = '';
        dom.searchInput.focus();
        dom.searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function initListeners() {
        registerUnsavedGuard();
        if (dom.searchInput && dom.searchInput.dataset.bound !== 'true') {
            dom.searchInput.addEventListener('input', handleSearchInput);
            dom.searchInput.dataset.bound = 'true';
            updateSearchClearVisibility();
        }

        if (dom.searchClear && dom.searchClear.dataset.bound !== 'true') {
            dom.searchClear.addEventListener('click', handleSearchClear);
            dom.searchClear.dataset.bound = 'true';
            updateSearchClearVisibility();
        }

        dom.createButton?.addEventListener('click', () => openFormForCreate());
        dom.formBackButton?.addEventListener('click', () => requestUnsavedConfirmation(showListView));
        dom.form?.addEventListener('submit', handleFormSubmit);
        dom.listContainer.addEventListener('click', handleListClick);
        dom.listContainer.addEventListener('keydown', handleListKeydown);
        bindImportExportControls();
        bindDeleteModalEvents();
    }

    function registerUnsavedGuard() {
        if (unsavedGuardRegistered || typeof window.unsavedChangesManager?.register !== 'function') {
            return;
        }
        window.unsavedChangesManager.register({
            id: UNSAVED_GUARD_ID,
            priority: 170,
            isActive: () => Boolean(dom.formPage && !dom.formPage.hidden),
            isDirty: () => hasPendingFormChanges(),
            discard: () => {
                resetFormState();
                state.editingId = null;
            },
        });
        unsavedGuardRegistered = true;
    }

    function requestUnsavedConfirmation(onConfirm) {
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
    }

    function showView(viewKey) {
        const isForm = viewKey === 'form';
        dom.listPage.hidden = isForm;
        dom.formPage.hidden = !isForm;
    }

    function showListView() {
        resetFormState();
        showView('list');
    }

    function resetFormState() {
        // Clear any existing field validation errors
        window.FieldValidation?.clearAllFieldErrors(dom.formFields);
        state.schema = [];
        state.schemaByPage.clear();
        state.formValues = {};
        state.originalValues = {};
        state.pendingChanges = {};
        state.sidebarButtons.clear();
        state.activePage = null;
        state.saveInFlight = false;
        state.activeControllers = new Map();
        if (dom.formFields) {
            dom.formFields.innerHTML = `<p class="user-settings-empty">${t('group_form_select_group', 'Select a group to load sections.')}</p>`;
        }
        if (dom.formSidebar) {
            dom.formSidebar.innerHTML = `<p class="user-settings-sidebar-empty">${t('group_form_loading_schema', 'Loading group schema…')}</p>`;
        }
        setFormLoading(true);
        updateFormSummary();
        updateSubmitButtonState();
    }

    async function openFormForCreate() {
        state.editingId = null;
        resetFormState();
        dom.formSubmitButton.querySelector('span').textContent = t('provider_group_create_btn', 'Create Group');
        showView('form');
        await loadGroupFormSchema();
    }

    async function openFormForEdit(groupId) {
        state.editingId = groupId;
        resetFormState();
        dom.formSubmitButton.querySelector('span').textContent = t('btn_save_changes', 'Save Changes');
        updateFormSummary();
        showView('form');
        await loadGroupFormSchema(groupId);
    }

    async function handleFormSubmit(event) {
        event.preventDefault();

        // Validate required fields using shared FieldValidation
        const controlsArray = [];
        state.schema.forEach((section) => {
            const fields = Array.isArray(section.fields) ? section.fields : [];
            fields.forEach((field) => {
                if (!field?.key) return;
                const selector = `[name="${field.key}"], [data-setting-key="${field.key}"]`;
                const control = dom.formFields?.querySelector(selector);
                if (control) {
                    controlsArray.push({ field, control });
                }
            });
        });

        if (controlsArray.length && !window.FieldValidation?.validate(controlsArray)) {
            return;
        }

        const payload = buildPayloadFromSchema();
        if (!payload || !validateManagerRoleSelections(payload)) {
            return;
        }
        if (payload.settings?.chat && window.WebsearchProviderLogic?.processProviderPairValuesForSubmit) {
            payload.settings.chat = window.WebsearchProviderLogic.processProviderPairValuesForSubmit(
                payload.settings.chat,
                Array.from(state.activeControllers.values()),
                {
                    searchFieldKey: 'settings.chat.byok_default_search_provider',
                    scrapeFieldKey: 'settings.chat.byok_default_scrape_provider',
                    searchValueKey: 'byok_default_search_provider',
                    scrapeValueKey: 'byok_default_scrape_provider',
                }
            );
        }

        const isEditing = Boolean(state.editingId);
        const button = dom.formSubmitButton;

        try {
            state.saveInFlight = true;
            setButtonLoadingState(button, true, isEditing ? t('group_form_saving', 'Saving…') : t('group_form_creating', 'Creating…'));
            const response = isEditing
                ? await groupsApi.update(state.editingId, payload)
                : await groupsApi.create(payload);
            if (!response) {
                notifyError(t('group_form_request_failed', 'Request failed.'));
                return;
            }
            notifySuccess(
                isEditing
                    ? t('group_form_updated', 'Group updated successfully.')
                    : t('group_form_created', 'Group created successfully.')
            );
            await loadGroups();
            showListView();
        } catch (error) {
            console.error('Failed to submit group form', error);
            notifyError(error?.message || t('group_form_save_failed', 'Failed to save group.'));
        } finally {
            state.saveInFlight = false;
            setButtonLoadingState(button, false);
            updateSubmitButtonState();
        }
    }

    function handleListClick(event) {
        const editButton = event.target.closest('[data-group-edit]');
        if (editButton) {
            const groupId = editButton.dataset.groupEdit;
            openFormForEdit(groupId);
            return;
        }
        const duplicateButton = event.target.closest('[data-group-duplicate]');
        if (duplicateButton) {
            const groupId = duplicateButton.dataset.groupDuplicate;
            duplicateGroupFromList(groupId, duplicateButton);
            return;
        }
        const deleteButton = event.target.closest('[data-group-delete]');
        if (deleteButton) {
            const groupId = deleteButton.dataset.groupDelete;
            confirmDeleteGroup(groupId);
            return;
        }

        // Keep the action cell independent from the row-wide edit target. This
        // prevents an incidental click beside an action button from opening the
        // form, matching the interaction used by the LLM models table.
        if (event.target.closest('.user-actions')) {
            return;
        }

        const row = event.target.closest('.group-row');
        if (row?.dataset.groupId) {
            openFormForEdit(row.dataset.groupId);
        }
    }

    function handleListKeydown(event) {
        if (event.key !== 'Enter' && event.key !== ' ') {
            return;
        }

        // Buttons in the action cell retain their native keyboard behavior and
        // must not also trigger the row's edit action as the event bubbles.
        if (event.target.closest('.user-actions')) {
            return;
        }

        const row = event.target.closest('.group-row');
        if (!row?.dataset.groupId) {
            return;
        }

        event.preventDefault();
        openFormForEdit(row.dataset.groupId);
    }

    async function confirmDeleteGroup(groupId) {
        const group = state.groups.find((item) => item.id === groupId);
        if (!group) {
            notifyError(t('group_form_group_not_found', 'Group not found.'));
            return;
        }
        openDeleteModal(groupId, group.name);
    }

    function bindDeleteModalEvents() {
        if (dom.deleteCancelButton && dom.deleteCancelButton.dataset.bound !== 'true') {
            dom.deleteCancelButton.addEventListener('click', closeDeleteModal);
            dom.deleteCancelButton.dataset.bound = 'true';
        }

        if (dom.deleteOverlay && dom.deleteOverlay.dataset.bound !== 'true') {
            dom.deleteOverlay.addEventListener('click', (event) => {
                if (event.target === dom.deleteOverlay) {
                    closeDeleteModal();
                }
            });
            dom.deleteOverlay.dataset.bound = 'true';
        }

        if (dom.deletePrimaryButton && dom.deletePrimaryButton.dataset.bound !== 'true') {
            dom.deletePrimaryButton.addEventListener('click', handleDeleteConfirmed);
            dom.deletePrimaryButton.dataset.bound = 'true';
        }
    }

    function openDeleteModal(groupId, groupName) {
        dom.deleteOverlay?.classList.add('active');
        dom.deleteOverlay?.removeAttribute('hidden');
        dom.deleteOverlay?.setAttribute('data-group-id', groupId);
        if (dom.deleteMessage) {
            dom.deleteMessage.textContent = formatT(
                'group_delete_confirm_named',
                'Delete the group "{name}"? This action cannot be undone.',
                { name: groupName }
            );
        }
        if (dom.deletePrimaryText) {
            dom.deletePrimaryText.textContent = t('group_delete_btn', 'Delete Group');
        }
        if (dom.deletePrimaryButton) {
            dom.deletePrimaryButton.disabled = false;
        }
    }

    function closeDeleteModal() {
        if (dom.deleteOverlay) {
            dom.deleteOverlay.classList.remove('active');
            dom.deleteOverlay.hidden = true;
            dom.deleteOverlay.removeAttribute('data-group-id');
        }
        if (dom.deletePrimaryButton) {
            dom.deletePrimaryButton.disabled = false;
        }
        if (dom.deletePrimaryText) {
            dom.deletePrimaryText.textContent = t('group_delete_btn', 'Delete Group');
        }
    }

    async function handleDeleteConfirmed() {
        if (!dom.deleteOverlay) {
            return;
        }
        const groupId = dom.deleteOverlay.getAttribute('data-group-id');
        if (!groupId) {
            return;
        }
        try {
            if (dom.deletePrimaryButton) {
                dom.deletePrimaryButton.disabled = true;
            }
            if (dom.deletePrimaryText) {
                dom.deletePrimaryText.textContent = t('admin_deleting', 'Deleting...');
            }
            await groupsApi.delete(groupId);
            notifySuccess(t('group_delete_success', 'Group deleted successfully.'));
            await loadGroups();
            closeDeleteModal();
        } catch (error) {
            console.error('Failed to delete group', error);
            notifyError(error?.message || t('group_delete_failed', 'Failed to delete group.'));
            if (dom.deletePrimaryButton) {
                dom.deletePrimaryButton.disabled = false;
            }
            if (dom.deletePrimaryText) {
                dom.deletePrimaryText.textContent = t('group_delete_btn', 'Delete Group');
            }
        }
    }

    function bindImportExportControls() {
        if (dom.exportButton && dom.exportButton.dataset.bound !== 'true') {
            dom.exportButton.addEventListener('click', handleGroupExport);
            dom.exportButton.dataset.bound = 'true';
        }

        if (dom.importButton && dom.importButton.dataset.bound !== 'true') {
            dom.importButton.addEventListener('click', triggerGroupImportFile);
            dom.importButton.dataset.bound = 'true';
        }

        const fileInputs = [dom.importFileInput, dom.importHiddenFileInput].filter(Boolean);
        fileInputs.forEach((input) => {
            if (input.dataset.bound === 'true') {
                return;
            }
            input.addEventListener('change', handleGroupImportFileChange);
            input.dataset.bound = 'true';
        });

        if (dom.importChooseFile && dom.importChooseFile.dataset.bound !== 'true') {
            dom.importChooseFile.addEventListener('click', triggerGroupImportFile);
            dom.importChooseFile.dataset.bound = 'true';
        }

        if (dom.importClose && dom.importClose.dataset.bound !== 'true') {
            dom.importClose.addEventListener('click', closeGroupImportOverlay);
            dom.importClose.dataset.bound = 'true';
        }

        if (dom.importCancel && dom.importCancel.dataset.bound !== 'true') {
            dom.importCancel.addEventListener('click', closeGroupImportOverlay);
            dom.importCancel.dataset.bound = 'true';
        }

        if (dom.importConfirm && dom.importConfirm.dataset.bound !== 'true') {
            dom.importConfirm.addEventListener('click', submitGroupImportSelection);
            dom.importConfirm.dataset.bound = 'true';
        }

        if (dom.importSelectAll && dom.importSelectAll.dataset.bound !== 'true') {
            dom.importSelectAll.addEventListener('change', toggleGroupImportSelectAll);
            dom.importSelectAll.dataset.bound = 'true';
        }
    }

    function applyFilters() {
        const searchTerm = dom.searchInput?.value?.trim().toLowerCase() || '';
        if (!searchTerm) {
            state.filtered = [...state.groups];
        } else {
            state.filtered = state.groups.filter((group) => {
                const name = group.name?.toLowerCase() || '';
                const id = group.id?.toLowerCase() || '';
                return name.includes(searchTerm) || id.includes(searchTerm);
            });
        }
        renderList();
    }

    async function handleGroupExport() {
        if (!dom.exportButton) {
            return;
        }
        try {
            setButtonLoadingState(dom.exportButton, true, t('admin_exporting_ellipsis', 'Exporting...'));
            const response = await window.authedFetch('/api/v1/groups/export', {
                method: 'GET',
            });
            if (!response.ok) {
                notifyError(t('groups_export_failed', 'Failed to export groups.'));
                return;
            }
            const blob = await response.blob();
            const filename = `groups-export-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
            downloadBlob(blob, filename);
            notifySuccess(t('groups_export_success', 'Groups exported successfully.'));
        } catch (error) {
            console.error('Failed to export groups', error);
            notifyError(error?.message || t('groups_export_failed', 'Failed to export groups.'));
        } finally {
            setButtonLoadingState(dom.exportButton, false);
        }
    }

    function downloadBlob(blob, filename) {
        if (!(blob instanceof Blob)) {
            return;
        }
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        URL.revokeObjectURL(url);
    }

    function triggerGroupImportFile() {
        const input = dom.importFileInput || dom.importHiddenFileInput;
        input?.click();
    }

    async function handleGroupImportFileChange(event) {
        const file = event.target.files?.[0];
        if (!file) {
            return;
        }
        try {
            const text = await file.text();
            const payload = JSON.parse(text);
            processGroupImportPayload(payload, file.name || 'groups.json');
            // reset input value so selecting same file again triggers change
            event.target.value = '';
        } catch (error) {
            console.error('Invalid import file', error);
            notifyError(t('groups_import_read_failed', 'Failed to read import file. Ensure it is a valid JSON export.'));
        }
    }

    function processGroupImportPayload(payload, fileName) {
        if (!payload || typeof payload !== 'object') {
            notifyError(t('groups_import_invalid_file', 'Invalid groups export file.'));
            return;
        }
        const groups = Array.isArray(payload?.data?.groups) ? payload.data.groups : [];
        if (!groups.length) {
            notifyWarning(t('groups_import_no_groups', 'No groups found in this file.'));
        }
        importState = {
            payload,
            groups,
            selected: new Set(groups.map((_, index) => index)),
            fileName,
            errors: [],
        };
        renderGroupImportList();
        updateGroupImportStatus();
        if (dom.importFileName) {
            dom.importFileName.textContent = fileName;
        }
        if (dom.importSelectAll) {
            dom.importSelectAll.checked = true;
        }
        openGroupImportOverlay();
        setGroupImportConfirmState();
    }

    function openGroupImportOverlay() {
        if (!dom.importOverlay) {
            return;
        }
        dom.importOverlay.hidden = false;
        dom.importOverlay.classList.add('active');
    }

    function closeGroupImportOverlay() {
        if (!dom.importOverlay) {
            return;
        }
        dom.importOverlay.classList.remove('active');
        dom.importOverlay.hidden = true;
        importState = {
            payload: null,
            groups: [],
            selected: new Set(),
            fileName: '',
            errors: [],
        };
        renderGroupImportList();
        updateGroupImportStatus();
        setGroupImportConfirmState();
        if (dom.importFileName) {
            dom.importFileName.textContent = '';
        }
    }

    /**
     * Update the "X of Y selected" counter shown in the import list header.
     * Kept in sync with every render/toggle so the admin always sees how many
     * groups will be created. Hidden entirely when no file is loaded.
     */
    function updateGroupImportCount() {
        if (!dom.importCount) {
            return;
        }
        const total = importState.groups.length;
        if (!total) {
            dom.importCount.textContent = '';
            dom.importCount.hidden = true;
            return;
        }
        dom.importCount.hidden = false;
        dom.importCount.textContent = formatT(
            'groups_import_selected_count',
            '{selected} of {total} selected',
            { selected: importState.selected.size, total }
        );
    }

    function renderGroupImportList() {
        if (!dom.importList) {
            return;
        }
        // Refresh the selection counter alongside the list itself.
        updateGroupImportCount();
        dom.importList.innerHTML = '';
        const { groups, selected } = importState;
        const errorsByIndex = getGroupImportErrorsByOriginalIndex();
        if (!groups.length) {
            const empty = document.createElement('div');
            empty.className = 'provider-import-entry provider-import-empty';
            empty.textContent = t('groups_import_empty', 'No groups available in this file.');
            dom.importList.appendChild(empty);
            return;
        }
        const fragment = document.createDocumentFragment();
        groups.forEach((group, index) => {
            const rowError = errorsByIndex.get(index);
            const entry = document.createElement('label');
            entry.className = rowError ? 'provider-import-entry has-error' : 'provider-import-entry';
            entry.setAttribute('role', 'option');
            entry.setAttribute('aria-selected', selected.has(index) ? 'true' : 'false');

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = selected.has(index);
            checkbox.dataset.groupImportIndex = String(index);
            checkbox.addEventListener('change', handleGroupImportToggle);
            entry.appendChild(checkbox);

            const content = document.createElement('div');
            content.className = 'provider-import-entry-content';

            const title = document.createElement('p');
            title.className = 'provider-import-entry-title';
            title.textContent = group?.name || t('groups_import_unnamed', '(Unnamed group)');
            content.appendChild(title);

            const meta = document.createElement('div');
            meta.className = 'provider-import-entry-meta';
            const idMeta = document.createElement('span');
            idMeta.textContent = formatT('groups_import_id_meta', 'ID: {id}', {
                id: group?.id || t('groups_import_auto_generated', 'auto-generated')
            });
            meta.appendChild(idMeta);
            content.appendChild(meta);

            if (rowError) {
                const errorMessage = document.createElement('p');
                errorMessage.className = 'provider-import-entry-error';
                errorMessage.textContent = rowError.reason;
                content.appendChild(errorMessage);
            }

            entry.appendChild(content);
            fragment.appendChild(entry);
        });
        dom.importList.appendChild(fragment);
    }

    function handleGroupImportToggle(event) {
        const checkbox = event.currentTarget;
        const index = Number.parseInt(checkbox.dataset.groupImportIndex || '', 10);
        if (Number.isNaN(index)) {
            return;
        }
        if (checkbox.checked) {
            importState.selected.add(index);
        } else {
            importState.selected.delete(index);
        }
        importState.errors = [];
        if (dom.importSelectAll) {
            dom.importSelectAll.checked = importState.selected.size === importState.groups.length;
        }
        renderGroupImportList();
        updateGroupImportStatus();
        setGroupImportConfirmState();
    }

    function toggleGroupImportSelectAll(event) {
        const { checked } = event.currentTarget;
        importState.selected.clear();
        if (checked) {
            importState.groups.forEach((_, index) => importState.selected.add(index));
        }
        importState.errors = [];
        renderGroupImportList();
        updateGroupImportStatus();
        setGroupImportConfirmState();
    }

    function setGroupImportConfirmState() {
        if (!dom.importConfirm) {
            return;
        }
        dom.importConfirm.disabled = importState.selected.size === 0;
    }

    function getGroupImportErrorsByOriginalIndex() {
        const errorsByIndex = new Map();
        (importState.errors || []).forEach((error) => {
            if (Number.isInteger(error.originalIndex)) {
                errorsByIndex.set(error.originalIndex, error);
            }
        });
        return errorsByIndex;
    }

    function normalizeGroupImportErrors(errors, submittedIndices = []) {
        if (!Array.isArray(errors)) {
            return [];
        }
        return errors.map((error, fallbackIndex) => {
            const submittedIndex = Number.isInteger(error?.index) ? error.index : fallbackIndex;
            const originalIndex = Number.isInteger(submittedIndices[submittedIndex])
                ? submittedIndices[submittedIndex]
                : null;
            const group = Number.isInteger(originalIndex) ? importState.groups[originalIndex] : null;
            const fallbackGroupName = group?.name || error?.name || error?.id || '';
            const reason = String(error?.error || t('common_unknown_error', 'Unknown error'));
            const groupLabel = fallbackGroupName
                ? String(fallbackGroupName)
                : formatT('groups_import_error_entry_label', 'Entry {index}', {
                    index: Number.isInteger(originalIndex) ? originalIndex + 1 : submittedIndex + 1,
                });

            return {
                ...error,
                originalIndex,
                groupLabel,
                reason,
            };
        });
    }

    function appendGroupImportErrorList(container, errors) {
        if (!Array.isArray(errors) || !errors.length) {
            return;
        }
        const list = document.createElement('ul');
        list.className = 'provider-import-error-list';
        errors.forEach((error) => {
            const item = document.createElement('li');
            item.className = 'provider-import-error-item';
            const label = document.createElement('span');
            label.className = 'provider-import-error-label';
            label.textContent = error.groupLabel;
            const reason = document.createElement('span');
            reason.className = 'provider-import-error-reason';
            reason.textContent = error.reason;
            item.append(label, document.createTextNode(': '), reason);
            list.appendChild(item);
        });
        container.appendChild(list);
    }

    function updateGroupImportStatus(message = '', type = '', errors = []) {
        if (!dom.importStatus) {
            return;
        }
        if (!message) {
            dom.importStatus.hidden = true;
            dom.importStatus.replaceChildren();
            dom.importStatus.className = 'provider-import-status';
            return;
        }
        dom.importStatus.hidden = false;
        dom.importStatus.className = `provider-import-status ${type}`.trim();
        const text = document.createElement('p');
        text.className = 'provider-import-status-message';
        text.textContent = message;
        dom.importStatus.replaceChildren(text);
        appendGroupImportErrorList(dom.importStatus, errors);
    }

    async function submitGroupImportSelection() {
        if (!importState.payload || !importState.selected.size) {
            updateGroupImportStatus(t('groups_import_select_one', 'Select at least one group to import.'), '');
            return;
        }
        try {
            setButtonLoadingState(dom.importConfirm, true, t('admin_importing_ellipsis', 'Importing...'));
            const indices = Array.from(importState.selected).sort((a, b) => a - b);
            const groupsToImport = indices.map((index) => importState.groups[index]).filter(Boolean);
            const selectedGroupIds = new Set(groupsToImport.map((group) => group?.id).filter(Boolean));
            const managersToImport = Array.isArray(importState.payload?.data?.group_managers)
                ? importState.payload.data.group_managers.filter((manager) => selectedGroupIds.has(manager?.group_id))
                : [];
            const filteredPayload = {
                ...importState.payload,
                data: {
                    ...(importState.payload.data || {}),
                    groups: groupsToImport,
                    group_managers: managersToImport,
                },
            };
            const response = await window.authedFetch('/api/v1/groups/import', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(filteredPayload),
            });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                updateGroupImportStatus(payload?.detail || t('groups_import_failed', 'Failed to import groups.'), 'error');
                return;
            }
            const result = await response.json();
            const createdCount = result?.created?.length || 0;
            const importErrors = normalizeGroupImportErrors(result?.errors, indices);
            for (const managerError of result?.manager_errors || []) {
                const managerIndex = Number.isInteger(managerError?.index)
                    ? managerError.index
                    : null;
                importErrors.push({
                    groupLabel: String(managerError?.group_id || managerError?.user_id || ''),
                    reason: String(managerError?.error || t('common_unknown_error', 'Unknown error')),
                    originalIndex: managerIndex,
                });
            }
            const errorCount = importErrors.length;
            importState.errors = importErrors;
            renderGroupImportList();

            if (createdCount) {
                notifySuccess(formatT(
                    'groups_import_success_count',
                    'Imported {count} group(s) successfully.',
                    { count: createdCount }
                ));
            }
            await loadGroups();
            if (!errorCount) {
                closeGroupImportOverlay();
            } else {
                updateGroupImportStatus(formatT(
                    'groups_import_warning_count',
                    '{count} group(s) could not be imported. Details are shown below.',
                    { count: errorCount }
                ), 'warning', importErrors);
            }
        } catch (error) {
            console.error('Failed to import groups', error);
            updateGroupImportStatus(error?.message || t('groups_import_failed', 'Failed to import groups.'), 'error');
        } finally {
            setButtonLoadingState(dom.importConfirm, false);
        }
    }

    async function duplicateGroupFromList(groupId, button) {
        if (!groupId) {
            notifyError(t('groups_duplicate_missing_id', 'Group ID missing.'));
            return;
        }
        try {
            if (button) {
                setButtonLoadingState(button, true, t('admin_duplicating_ellipsis', 'Duplicating...'));
            }
            const duplicated = await groupsApi.duplicate(groupId);
            if (!duplicated) {
                return;
            }
            notifySuccess(t('groups_duplicate_success', 'Group duplicated successfully.'));
            await loadGroups();
        } catch (error) {
            console.error('Failed to duplicate group', error);
            notifyError(error?.message || t('groups_duplicate_failed', 'Failed to duplicate group.'));
        } finally {
            if (button) {
                setButtonLoadingState(button, false);
            }
        }
    }

    function renderList() {
        const container = dom.listContainer;
        container.innerHTML = '';
        container.removeAttribute('role');
        container.removeAttribute('aria-label');

        if (!state.filtered.length) {
            const emptyState = window.createAdminEmptyPlaceholder({
                title: state.groups.length ? t('groups_empty_filtered', 'No groups match your search') : t('groups_empty_title', 'No groups yet'),
                description: t('groups_empty_desc', 'Create a group to start managing scoped access.'),
                icon: Icons.groups,
                className: 'provider-empty-state',
            });
            container.appendChild(emptyState);
            return;
        }

        container.setAttribute('role', 'table');
        container.setAttribute('aria-label', t('page_groups', 'Groups'));
        const header = window.createAdminTableHeader({
            className: 'group-table-header',
            cells: [
                { className: 'header-name', text: t('table_header_group_name', 'Group name') },
                { className: 'header-path', text: t('table_header_group_path', 'Group path') },
                { className: 'header-members', text: t('table_header_members', 'Members') },
                { className: 'header-managers', text: t('table_header_managers', 'Managers') },
                { className: 'header-actions', text: t('table_header_actions', 'Actions') },
            ],
        });
        container.appendChild(header);

        const fragment = document.createDocumentFragment();
        state.filtered.forEach((group) => {
            const row = document.createElement('div');
            row.className = 'group-row groups-row';
            row.dataset.groupId = group.id;
            row.setAttribute('role', 'row');

            const nameCell = document.createElement('div');
            nameCell.className = 'group-name';
            nameCell.setAttribute('role', 'cell');
            nameCell.dataset.label = t('table_header_group_name', 'Group name');
            const path = Array.isArray(group.path) && group.path.length ? group.path.join(' / ') : group.name || '—';
            const actionTargetName = group.name || path;
            // The whole data portion of the row behaves like the edit button.
            // Make that shortcut keyboard reachable and describe its action to
            // assistive technology with the existing translated edit label.
            row.setAttribute('tabindex', '0');
            row.setAttribute('aria-label', formatT('provider_group_edit_aria', 'Edit {name}', { name: actionTargetName }));
            const memberCount = Number(group.direct_member_count || 0);
            const managerCount = Number(group.direct_manager_count || 0);
            const primary = document.createElement('div');
            primary.className = 'group-name-primary';
            primary.textContent = group.name || '—';
            nameCell.appendChild(primary);
            if (group.id === state.savedDefaultGroupId) {
                const defaultBadge = document.createElement('span');
                defaultBadge.className = 'group-default-badge';
                defaultBadge.textContent = t('groups_default_badge', 'Default');
                primary.appendChild(defaultBadge);
            }
            row.appendChild(nameCell);

            const pathCell = window.createAdminTableCell({
                className: 'group-path',
                label: t('table_header_group_path', 'Group path'),
                text: path,
            });
            row.appendChild(pathCell);

            const membersCell = window.createAdminTableCell({
                className: 'group-members-count',
                label: t('table_header_members', 'Members'),
                text: String(memberCount),
            });
            row.appendChild(membersCell);

            const managersCell = window.createAdminTableCell({
                className: 'group-managers-count',
                label: t('table_header_managers', 'Managers'),
                text: String(managerCount),
            });
            row.appendChild(managersCell);

            const actionsCell = document.createElement('div');
            actionsCell.className = 'user-actions';
            actionsCell.setAttribute('role', 'cell');
            actionsCell.dataset.label = t('table_header_actions', 'Actions');

            const editButton = window.createAdminIconActionButton({
                className: 'action-btn edit-btn',
                title: t('provider_group_edit_title', 'Edit group'),
                ariaLabel: formatT('provider_group_edit_aria', 'Edit {name}', { name: actionTargetName }),
                icon: Icons?.edit,
                fallback: '✎',
                dataset: { groupEdit: group.id },
            });
            actionsCell.appendChild(editButton);

            const duplicateButton = window.createAdminIconActionButton({
                className: 'action-btn edit-btn',
                title: t('provider_group_duplicate_title', 'Duplicate group'),
                ariaLabel: formatT('provider_group_duplicate_aria', 'Duplicate {name}', { name: actionTargetName }),
                icon: Icons?.copy,
                fallback: '⧉',
                dataset: { groupDuplicate: group.id },
            });
            actionsCell.appendChild(duplicateButton);

            if (group.id !== 'default') {
                const deleteButton = window.createAdminIconActionButton({
                    className: 'action-btn delete-btn',
                    title: t('provider_group_delete_title', 'Delete group'),
                    ariaLabel: formatT('provider_group_delete_aria', 'Delete {name}', { name: actionTargetName }),
                    icon: Icons?.trash,
                    fallback: '🗑',
                    dataset: { groupDelete: group.id },
                });
                actionsCell.appendChild(deleteButton);
            }

            row.appendChild(actionsCell);
            fragment.appendChild(row);
        });

        container.appendChild(fragment);
    }

    async function loadGroups() {
        try {
            state.groups = await groupsApi.list();
        } catch (error) {
            console.error('Failed to load groups', error);
            state.groups = [];
            notifyError(error?.message || t('rate_limit_fetch_groups_failed', 'Failed to load groups.'));
        }
        state.filtered = [...state.groups];
        renderList();
        if (state.initialized) {
            initDefaultSettingsController({ reload: true });
        }
    }

    async function initGroupsPage() {
        if (!i18nUpdateHandlerBound) {
            document.addEventListener('i18n:updated', () => {
                if (!state.initialized) {
                    return;
                }
                renderList();
                initDefaultSettingsController({ reload: true });
                refreshFormTranslations();
            });
            i18nUpdateHandlerBound = true;
        }
        if (state.initialized) {
            await loadGroups();
            showListView();
            return;
        }
        initListeners();
        await loadGroups();
        state.initialized = true;
        initDefaultSettingsController();
    }

    window.initGroupsPage = initGroupsPage;
})();
