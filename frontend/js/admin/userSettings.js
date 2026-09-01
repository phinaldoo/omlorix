/**
 * Admin user settings editor controller.
 */
(function () {
    const pageEl = document.getElementById('page-user-settings');
    if (!pageEl) {
        window.initAdminUserSettingsPage = () => {};
        window.teardownAdminUserSettingsPage = () => {};
        window.openAdminUserSettingsPage = () => {};
        return;
    }

    const sidebarEl = document.getElementById('userSettingsSidebar');
    const contentEl = document.getElementById('userSettingsContent');
    const saveButton = document.getElementById('userSettingsSaveButton');
    const cancelButton = document.getElementById('userSettingsCancelButton');
    const headerTitle = document.getElementById('userSettingsTitle');
    const headerSubtitle = document.getElementById('userSettingsSubtitle');
    const avatarEl = document.getElementById('userSettingsAvatar');
    const resetTwofaConfirmOverlay = document.getElementById('resetTwofaConfirmOverlay');
    const resetTwofaConfirmCancelButton = document.getElementById('resetTwofaConfirmCancelButton');
    const resetTwofaConfirmPrimaryButton = document.getElementById('resetTwofaConfirmPrimaryButton');
    const UNSAVED_GUARD_ID = 'admin-user-settings-unsaved';

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const sidebarEmptyState = document.createElement('p');
    sidebarEmptyState.className = 'user-settings-sidebar-empty';
    sidebarEmptyState.textContent = t('admin_user_settings_sidebar_empty', 'No settings available.');

    const state = {
        initialized: false,
        sections: [],
        schemaPromise: null,
        sectionByPage: new Map(),
        schemaUserId: null,
        schemaIncludesValues: false,
        schemaAbortController: null,
        user: null,
        activePage: null,
        sidebarButtons: new Map(),
        originalValues: {},
        currentValues: {},
        pendingChanges: {},
        saveInFlight: false,
        // Profile section state
        profile: null,
        profileOriginal: null,
        profilePending: {},
        profileAccessReason: '',
        groups: [],
        activeControllers: new Map(),
        escapeRegistration: null,
        resetTwofaConfirmEscapeRegistration: null,
        resetTwofaConfirmResolver: null,
        resetTwofaConfirmLastFocusedElement: null,
        unsavedGuardRegistered: false,
        languageObserver: null,
    };

    const PROFILE_PAGE_KEY = '__profile__';
    const LLM_ACCESS_PRESET_FIELD_KEY = 'allow_llm_to_access_personal_information_preset';

    function init() {
        if (state.initialized) {
            return;
        }
        state.initialized = true;
        observeLanguageChanges();
        saveButton?.addEventListener('click', handleSaveClick);
        cancelButton?.addEventListener('click', handleBackNavigation);
        bindResetTwofaConfirmModal();
        registerEscapeShortcut();
        registerUnsavedGuard();
    }

    function observeLanguageChanges() {
        if (state.languageObserver || !document.documentElement) {
            return;
        }
        state.languageObserver = new MutationObserver((mutations) => {
            const langChanged = mutations.some((mutation) => mutation.type === 'attributes' && mutation.attributeName === 'lang');
            if (!langChanged || !state.initialized || pageEl.hidden) {
                return;
            }
            sidebarEmptyState.textContent = t('admin_user_settings_sidebar_empty', 'No settings available.');
            resetUserSummary();
            if (state.user) {
                setUserSummary(state.user);
            }
            if (state.sections.length) {
                createSidebar(state.sections);
            }
            if (state.activePage === PROFILE_PAGE_KEY) {
                renderProfilePage();
            } else if (state.activePage) {
                renderPage(state.activePage);
            }
            updateSaveButtonState();
        });
        state.languageObserver.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ['lang'],
        });
    }

    function registerEscapeShortcut() {
        if (state.escapeRegistration || typeof window === 'undefined' || !window.registerEscapeHandler) {
            return;
        }
        state.escapeRegistration = window.registerEscapeHandler({
            id: 'admin-user-settings-escape',
            priority: 140,
            isActive: () => Boolean(pageEl && !pageEl.hidden),
            close: handleBackNavigation,
        });
    }

    function registerUnsavedGuard() {
        if (typeof window.unsavedChangesManager?.register !== 'function') {
            return;
        }
        if (state.unsavedGuardRegistered) {
            return;
        }
        window.unsavedChangesManager.register({
            id: UNSAVED_GUARD_ID,
            priority: 170,
            isActive: () => Boolean(pageEl && !pageEl.hidden),
            isDirty: () => hasPendingChanges(),
            discard: () => discardPendingChanges(),
            getCopy: () => ({
                subtitle: t('admin_user_settings_unsaved_confirm', 'You have unsaved changes. Leave without saving?'),
            }),
        });
        state.unsavedGuardRegistered = true;
    }

    function teardown() {
        cancelSchemaRequest();
        closeResetTwofaConfirmModal(false);
        // Clear any existing field validation errors
        window.FieldValidation?.clearAllFieldErrors(contentEl);
        state.activePage = null;
        contentEl.innerHTML = '';
        sidebarEl.innerHTML = '';
        appendSidebarEmptyState();
        resetUserSummary();
        clearStatus();
        resetStateValues();
    }

    function resetStateValues() {
        state.originalValues = {};
        state.currentValues = {};
        state.pendingChanges = {};
        state.sidebarButtons.clear();
        state.sections = [];
        state.sectionByPage.clear();
        state.schemaUserId = null;
        state.schemaIncludesValues = false;
        state.activeControllers = new Map();
        updateSaveButtonState();
    }

    function cancelSchemaRequest() {
        if (state.schemaAbortController) {
            state.schemaAbortController.abort();
            state.schemaAbortController = null;
        }
    }

    function invalidateSchemaCache() {
        state.sections = [];
        state.sectionByPage.clear();
        state.schemaUserId = null;
        state.schemaIncludesValues = false;
    }

    function ensureSchema({ includeValuesForUser } = {}) {
        const needsValues = Boolean(includeValuesForUser);
        if (
            state.sections.length &&
            ((!needsValues && !state.schemaIncludesValues) ||
                (needsValues && state.schemaIncludesValues && state.schemaUserId === includeValuesForUser))
        ) {
            return Promise.resolve(state.sections);
        }

        if (state.schemaPromise) {
            return state.schemaPromise;
        }

        cancelSchemaRequest();
        const controller = new AbortController();
        state.schemaAbortController = controller;

        state.schemaPromise = fetchAdminUserSettingsSchema({
            includeValues: needsValues,
            userId: includeValuesForUser,
            signal: controller.signal,
        })
            .then((schema) => {
                if (controller.signal.aborted) {
                    notifyError(t('admin_user_settings_schema_aborted', 'Schema request aborted'));
                }
                // Admin user settings use the same Sections -> Section -> Field
                // response contract as the admin group settings editor.
                state.sections = Array.isArray(schema?.sections) ? schema.sections : [];
                state.sectionByPage = new Map(state.sections.map((section) => [section.key, section]));
                state.schemaUserId = includeValuesForUser || null;
                state.schemaIncludesValues = needsValues;
                hydrateValuesFromSchema();
                return state.sections;
            })
            .catch((error) => {
                state.sections = [];
                state.sectionByPage.clear();
                state.schemaUserId = null;
                state.schemaIncludesValues = false;
                state.originalValues = {};
                state.currentValues = {};
                throw error;
            })
            .finally(() => {
                if (state.schemaAbortController === controller) {
                    state.schemaAbortController = null;
                }
                state.schemaPromise = null;
            });

        return state.schemaPromise;
    }

    function hydrateValuesFromSchema() {
        state.originalValues = {};
        state.currentValues = {};

        state.sections.forEach((section) => {
            const pageKey = section.key;
            const fields = Array.isArray(section.fields) ? section.fields : [];
            const pageValues = {};

            fields.forEach((field) => {
                const value = field.value !== undefined ? field.value : field.default;
                pageValues[field.key] = cloneSettingsValue(value);
            });

            state.originalValues[pageKey] = cloneSettingsObject(pageValues);
            state.currentValues[pageKey] = cloneSettingsObject(pageValues);
        });

        state.pendingChanges = {};
        updateSaveButtonState();
    }

    async function refreshSchemaValues() {
        if (!state.user?.id) {
            return [];
        }
        invalidateSchemaCache();
        return ensureSchema({ includeValuesForUser: state.user.id });
    }

    function appendSidebarEmptyState() {
        if (!sidebarEl.contains(sidebarEmptyState)) {
            sidebarEl.appendChild(sidebarEmptyState);
        }
    }

    const SIDEBAR_ICONS = {
        '__profile__': Icons.user,
        'general': Icons.settings,
        'preferences': Icons.preferences,
        'security': Icons.security,
        'notifications': Icons.notification,
        'appearance': Icons.sun,
        'privacy': Icons.lock,
        'limits': Icons.lock,
        'permissions': Icons.lock,
        'default': Icons.info,
    };

    function getIconForPage(key) {
        const lowerKey = key.toLowerCase();
        return SIDEBAR_ICONS[key] || SIDEBAR_ICONS[lowerKey] || SIDEBAR_ICONS.default;
    }

    function createSidebar(sections) {
        sidebarEl.innerHTML = '';
        state.sidebarButtons.clear();

        const fragment = document.createDocumentFragment();

        // Add Profile section first
        const profileButton = document.createElement('button');
        profileButton.type = 'button';
        profileButton.className = 'user-settings-nav-item';
        profileButton.dataset.pageKey = PROFILE_PAGE_KEY;

        const profileIcon = document.createElement('span');
        profileIcon.className = 'user-settings-nav-icon';
        profileIcon.innerHTML = getIconForPage(PROFILE_PAGE_KEY);
        profileIcon.setAttribute('aria-hidden', 'true');
        profileButton.appendChild(profileIcon);

        const profileLabel = document.createElement('span');
        profileLabel.className = 'user-settings-nav-label';
        profileLabel.textContent = t('us_nav_profile', 'Profile');
        profileButton.appendChild(profileLabel);

        const profileIndicator = document.createElement('span');
        profileIndicator.className = 'user-settings-nav-indicator';
        profileIndicator.setAttribute('aria-hidden', 'true');
        profileButton.appendChild(profileIndicator);

        profileButton.addEventListener('click', () => setActivePage(PROFILE_PAGE_KEY));
        fragment.appendChild(profileButton);
        state.sidebarButtons.set(PROFILE_PAGE_KEY, profileButton);

        if (!Array.isArray(sections) || !sections.length) {
            sidebarEl.appendChild(fragment);
            return;
        }

        sections.forEach((section) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'user-settings-nav-item';
            button.dataset.pageKey = section.key;

            const icon = document.createElement('span');
            icon.className = 'user-settings-nav-icon';
            icon.innerHTML = getIconForPage(section.key);
            icon.setAttribute('aria-hidden', 'true');
            button.appendChild(icon);

            const label = document.createElement('span');
            label.className = 'user-settings-nav-label';
            label.textContent = section.i18n_title
                ? t(section.i18n_title, section.title || section.key)
                : (section.title || section.key);
            button.appendChild(label);

            const indicator = document.createElement('span');
            indicator.className = 'user-settings-nav-indicator';
            indicator.setAttribute('aria-hidden', 'true');
            button.appendChild(indicator);

            button.addEventListener('click', () => setActivePage(section.key));

            fragment.appendChild(button);
            state.sidebarButtons.set(section.key, button);
        });

        sidebarEl.appendChild(fragment);
        syncProfileTranslations();
    }

    function syncProfileTranslations() {
        if (state.activePage !== PROFILE_PAGE_KEY || !contentEl?.children?.length) {
            return;
        }
        renderProfilePage();
    }

    function clearStatus() {}

    function setStatus(kind, message) {
        if (!message) {
            return;
        }
        if (kind === 'warning' || kind === 'info') {
            notifyWarning?.(message);
            return;
        }
        if (kind === 'success') {
            notifySuccess?.(message);
            return;
        }
        notifyError?.(message);
    }

    function setContentLoading(isLoading) {
        if (!contentEl) {
            return;
        }
        if (isLoading) {
            contentEl.dataset.loading = 'true';
        } else {
            delete contentEl.dataset.loading;
        }
    }

    function renderPlaceholder(message) {
        setContentLoading(false);
        contentEl.innerHTML = '';
        const placeholder = document.createElement('p');
        placeholder.className = 'user-settings-empty';
        placeholder.textContent = message;
        contentEl.appendChild(placeholder);
    }

    function setActivePage(pageKey) {
        if (!state.user?.id) {
            renderPlaceholder(t('admin_user_settings_select_user', 'Select a user from the list to edit their settings.'));
            return;
        }
        if (!pageKey || state.activePage === pageKey) {
            return;
        }

        state.activePage = pageKey;
        state.sidebarButtons.forEach((button, key) => {
            button.classList.toggle('active', key === pageKey);
        });

        // Handle Profile page specially
        if (pageKey === PROFILE_PAGE_KEY) {
            renderProfilePage();
            return;
        }

        if (!state.currentValues[pageKey]) {
            renderPlaceholder(t('admin_user_settings_no_page_data', 'No settings data available for this page.'));
            return;
        }

        renderPage(pageKey);
    }

    async function loadUserProfile({ reason = '' } = {}) {
        if (!state.user?.id) return null;
        try {
            const profile = await fetchAdminUserProfile(state.user.id, {
                reason,
                includeSensitiveProfile: true,
                includeSecurity: true,
            });
            state.profile = profile;
            state.profileOriginal = JSON.parse(JSON.stringify(profile || {}));
            state.profilePending = {};
            state.profileAccessReason = reason;
            return profile;
        } catch (error) {
            console.error('Failed to load user profile', error);
            return null;
        }
    }

    async function loadGroups() {
        try {
            state.groups = await fetchAdminGroupsList();
        } catch (error) {
            console.error('Failed to load groups', error);
            state.groups = [];
        }
    }

    function renderProfilePage() {
        contentEl.innerHTML = '';
        if (!state.profile) {
            if (String(state.profileAccessReason || '').trim().length >= 3) {
                renderPlaceholder(t('admin_user_settings_profile_load_failed', 'Unable to load user profile.'));
                return;
            }
            renderProfileAccessGate();
            setContentLoading(false);
            return;
        }

        renderProfileContent();
        setContentLoading(false);
    }

    function renderProfileAccessGate() {
        const title = document.createElement('h3');
        title.textContent = t('admin_user_settings_profile_title', 'User Profile');
        contentEl.appendChild(title);

        const description = document.createElement('p');
        description.className = 'user-settings-description';
        description.textContent = t(
            'admin_user_settings_profile_reason_desc',
            'Provide a reason before loading this user’s personal profile and account security details.'
        );
        contentEl.appendChild(description);

        const gateSection = createProfileSection(
            t('admin_user_settings_profile_reason_title', 'Access reason'),
            t(
                'admin_user_settings_profile_reason_help',
                'This reason is stored in the audit log together with the categories of profile data you view.'
            )
        );

        const reasonRow = document.createElement('div');
        reasonRow.className = 'settings-row';

        const left = document.createElement('div');
        left.className = 'settings-row-left';

        const labelEl = document.createElement('p');
        labelEl.className = 'settings-row-title';
        labelEl.textContent = t('admin_user_settings_profile_reason_label', 'Reason');
        left.appendChild(labelEl);

        const descEl = document.createElement('p');
        descEl.className = 'settings-row-desc';
        descEl.textContent = t(
            'admin_user_settings_profile_reason_field_desc',
            'Describe why you need to inspect this user’s personal or security details.'
        );
        left.appendChild(descEl);
        reasonRow.appendChild(left);

        const controlWrapper = document.createElement('div');
        controlWrapper.className = 'settings-row-control';

        const reasonInput = document.createElement('textarea');
        reasonInput.className = 'input textarea';
        reasonInput.rows = 4;
        reasonInput.value = state.profileAccessReason || '';
        reasonInput.placeholder = t(
            'admin_user_settings_profile_reason_placeholder',
            'Example: Investigating a lockout report from the user.'
        );
        reasonInput.setAttribute('aria-label', t('admin_user_settings_profile_reason_label', 'Reason'));
        controlWrapper.appendChild(reasonInput);

        const actionRow = document.createElement('div');
        actionRow.className = 'settings-actions';

        const loadButton = document.createElement('button');
        loadButton.type = 'button';
        loadButton.className = 'btn btn-primary';
        loadButton.textContent = t('admin_user_settings_profile_reason_button', 'Load profile details');
        loadButton.addEventListener('click', () => {
            const reason = String(reasonInput.value || '').trim();
            if (reason.length < 3) {
                const message = t(
                    'admin_user_settings_profile_reason_required',
                    'Enter a short reason before loading sensitive profile details.'
                );
                setStatus('error', message);
                reasonInput.focus();
                reasonInput.setSelectionRange(0, reasonInput.value.length);
                return;
            }

            clearStatus();
            setContentLoading(true);
            Promise.all([
                loadUserProfile({ reason }),
                loadGroups(),
            ])
                .then(([profile]) => {
                    if (!profile) {
                        throw new Error(
                            t('admin_user_settings_profile_load_failed', 'Unable to load user profile.')
                        );
                    }
                    renderProfilePage();
                })
                .catch((error) => {
                    setContentLoading(false);
                    notifyError?.(error?.message || t('admin_user_settings_profile_load_failed', 'Unable to load user profile.'));
                });
        });
        actionRow.appendChild(loadButton);
        controlWrapper.appendChild(actionRow);

        reasonRow.appendChild(controlWrapper);
        gateSection.body.appendChild(reasonRow);
        contentEl.appendChild(gateSection.section);
    }

    function renderProfileContent() {
        contentEl.innerHTML = '';
        const profile = state.profile;

        if (!profile) {
            renderPlaceholder(t('admin_user_settings_profile_load_failed', 'Unable to load user profile.'));
            return;
        }

        const title = document.createElement('h3');
        title.textContent = t('admin_user_settings_profile_title', 'User Profile');
        contentEl.appendChild(title);

        const description = document.createElement('p');
        description.className = 'user-settings-description';
        description.textContent = t('admin_user_settings_profile_desc', 'Edit the user\'s personal information, group assignment, and security settings.');
        contentEl.appendChild(description);

        if (profile.externally_managed) {
            const managedNotice = document.createElement('p');
            managedNotice.className = 'user-settings-description';
            const provider = String(profile.external_auth_provider || '').trim().toUpperCase();
            managedNotice.textContent = provider
                ? `${t('users_externally_managed', 'Externally managed')} · ${provider}`
                : t('users_externally_managed', 'Externally managed');
            contentEl.appendChild(managedNotice);
        }

        const fragment = document.createDocumentFragment();

        // The upstream directory owns identity attributes for managed users.
        if (!profile.externally_managed) {
            const personalSection = createProfileSection(
                t('admin_user_settings_section_personal_title', 'Personal Information'),
                t('admin_user_settings_section_personal_desc', 'Basic user details and contact information.')
            );

            personalSection.body.appendChild(createProfileField({
                key: 'email',
                label: t('admin_user_settings_field_email_label', 'Email'),
                type: 'string',
                value: profile.email || '',
                placeholder: t('user_create_email_placeholder', 'user@example.com'),
            }));
            personalSection.body.appendChild(createProfileField({
                key: 'first_name',
                label: t('admin_user_settings_field_first_name_label', 'First Name'),
                type: 'string',
                value: profile.first_name || '',
                placeholder: t('user_create_first_name_label', 'First name'),
            }));
            personalSection.body.appendChild(createProfileField({
                key: 'last_name',
                label: t('admin_user_settings_field_last_name_label', 'Last Name'),
                type: 'string',
                value: profile.last_name || '',
                placeholder: t('user_create_last_name_label', 'Last name'),
            }));
            fragment.appendChild(personalSection.section);
        }

        // Group & Role Section
        const groupSection = createProfileSection(
            t('admin_user_settings_section_group_title', 'Group & Role'),
            t('admin_user_settings_section_group_desc', 'Manage user group assignment.')
        );
        
        // Group (select)
        groupSection.body.appendChild(createProfileField({
            key: 'group_id',
            label: t('admin_user_settings_field_group_label', 'Group'),
            type: 'select',
            value: profile.group_id || '',
            options: state.groups.map(g => ({ value: g.id, label: g.name })),
        }));

        fragment.appendChild(groupSection.section);

        // Local password, failed-attempt, and 2FA controls do not exist for a
        // managed account. Recovery and authentication remain with the IdP.
        if (!profile.externally_managed) {
            const securitySection = createProfileSection(
                t('admin_user_settings_section_security_title', 'Security'),
                t('admin_user_settings_section_security_desc', 'Password and account security settings.')
            );
            securitySection.body.appendChild(createProfileField({
                key: 'password',
                label: t('admin_user_settings_field_new_password_label', 'New Password'),
                type: 'password',
                value: '',
                placeholder: t('user_settings_password_placeholder', 'Leave empty to keep current password'),
                description: t(
                    'admin_user_settings_field_new_password_desc',
                    'Enter a new password to change it, or leave empty to keep the current password. Changing it signs the user out on all devices.'
                ),
            }));
            securitySection.body.appendChild(createProfileField({
                key: 'wrong_sign_in_attempts',
                label: t('admin_user_settings_field_failed_signin_label', 'Failed Sign-in Attempts'),
                type: 'number',
                value: profile.wrong_sign_in_attempts || 0,
                attributes: { min: 0 },
                description: t('admin_user_settings_field_failed_signin_desc', 'Number of consecutive failed sign-in attempts. Reset to 0 to unlock the user.'),
            }));
            securitySection.body.appendChild(createProfileActionRow({
                title: t('admin_user_settings_reset_2fa_title', 'Reset Two-Factor Authentication'),
                description: t(
                    'admin_user_settings_reset_2fa_desc',
                    'Clear the user’s 2FA enrollment and any pending verification state so they can enroll again.'
                ),
                buttonLabel: t('admin_user_settings_reset_2fa_button', 'Reset 2FA'),
                buttonClassName: 'btn btn-secondary',
                onClick: handleResetTwofaClick,
            }));
            fragment.appendChild(securitySection.section);
        }

        // Lock Section
        const lockSection = createProfileSection(
            t('admin_user_settings_section_lock_title', 'Account Lock'),
            t('admin_user_settings_section_lock_desc', 'Control account access restrictions.')
        );
        const lockData = profile.lock || {};

        // Is Locked
        lockSection.body.appendChild(createProfileField({
            key: 'lock.is_locked',
            label: t('admin_user_settings_field_account_locked_label', 'Account Locked'),
            type: 'boolean',
            value: lockData.is_locked || false,
            description: t('admin_user_settings_field_account_locked_desc', 'When enabled, the user cannot sign in.'),
        }));

        // Lock Until
        lockSection.body.appendChild(createProfileField({
            key: 'lock.lock_until',
            label: t('admin_user_settings_field_lock_until_label', 'Lock Until'),
            type: 'datetime-local',
            value: lockData.lock_until ? formatDateTimeLocal(lockData.lock_until) : '',
            description: t('admin_user_settings_field_lock_until_desc', 'Date and time until the account remains locked. Leave empty for indefinite lock.'),
        }));

        // Lock Type
        lockSection.body.appendChild(createProfileField({
            key: 'lock.type',
            label: t('admin_user_settings_field_lock_type_label', 'Lock Type'),
            type: 'select',
            value: lockData.type || '',
            description: t(
                'admin_user_settings_field_lock_type_desc',
                'Select why the account is locked to indicate whether it was manual, automatic, or security-related.'
            ),
            options: [
                { value: '', label: t('admin_user_settings_lock_type_none', 'None') },
                { value: 'manual', label: t('admin_user_settings_lock_type_manual', 'Manual') },
                { value: 'auto', label: t('admin_user_settings_lock_type_auto', 'Automatic (failed attempts)') },
                { value: 'security', label: t('admin_user_settings_lock_type_security', 'Security concern') },
            ],
        }));

        // Lock Reason
        lockSection.body.appendChild(createProfileField({
            key: 'lock.reason',
            label: t('admin_user_settings_field_lock_reason_label', 'Lock Reason'),
            type: 'textarea',
            value: lockData.reason || '',
            placeholder: t('user_settings_lock_reason_placeholder', 'Reason for locking the account...'),
            description: t(
                'admin_user_settings_field_lock_reason_desc',
                'Provide context for the lock so admins can understand and audit why access was restricted.'
            ),
        }));

        fragment.appendChild(lockSection.section);

        contentEl.appendChild(fragment);
        updateProfileDirtyIndicator();
    }

    function createProfileSection(title, description) {
        const section = document.createElement('section');
        section.className = 'settings-section';

        const header = document.createElement('div');
        header.className = 'settings-section-header';

        const titleEl = document.createElement('h4');
        titleEl.className = 'settings-section-title';
        titleEl.textContent = title;
        header.appendChild(titleEl);

        if (description) {
            const descEl = document.createElement('p');
            descEl.className = 'settings-section-description';
            descEl.textContent = description;
            header.appendChild(descEl);
        }

        section.appendChild(header);

        const body = document.createElement('div');
        body.className = 'settings-section-body';
        section.appendChild(body);

        return { section, body };
    }

    function createProfileActionRow({ title, description, buttonLabel, buttonClassName, onClick }) {
        const row = document.createElement('div');
        row.className = 'settings-row';

        const left = document.createElement('div');
        left.className = 'settings-row-left';

        const titleEl = document.createElement('p');
        titleEl.className = 'settings-row-title';
        titleEl.textContent = title;
        left.appendChild(titleEl);

        if (description) {
            const descEl = document.createElement('p');
            descEl.className = 'settings-row-desc';
            descEl.textContent = description;
            left.appendChild(descEl);
        }

        row.appendChild(left);

        const controlWrapper = document.createElement('div');
        controlWrapper.className = 'settings-row-control';

        const button = document.createElement('button');
        button.type = 'button';
        button.className = buttonClassName || 'btn btn-secondary';
        button.textContent = buttonLabel;
        button.addEventListener('click', () => {
            Promise.resolve(onClick?.(button)).catch((error) => {
                console.error('Profile action failed', error);
                notifyError?.(error?.message || t('admin_user_settings_reset_2fa_failed', 'Failed to reset 2FA.'));
            });
        });
        controlWrapper.appendChild(button);

        row.appendChild(controlWrapper);
        return row;
    }

    function createProfileField(field) {
        const row = document.createElement('div');
        row.className = 'settings-row';
        row.dataset.profileKey = field.key;

        const left = document.createElement('div');
        left.className = 'settings-row-left';

        const labelEl = document.createElement('p');
        labelEl.className = 'settings-row-title';
        labelEl.textContent = field.label;
        left.appendChild(labelEl);

        if (field.description) {
            const descEl = document.createElement('p');
            descEl.className = 'settings-row-desc';
            descEl.textContent = field.description;
            left.appendChild(descEl);
        }

        row.appendChild(left);

        const controlWrapper = document.createElement('div');
        controlWrapper.className = 'settings-row-control';

        let control;
        switch (field.type) {
            case 'boolean': {
                const label = document.createElement('label');
                label.className = 'toggle-switch';
                control = document.createElement('input');
                control.type = 'checkbox';
                control.className = 'toggle-input';
                control.checked = Boolean(field.value);
                label.appendChild(control);
                const slider = document.createElement('span');
                slider.className = 'toggle-slider';
                label.appendChild(slider);
                controlWrapper.appendChild(label);
                break;
            }
            case 'select': {
                control = document.createElement('select');
                control.className = 'select';
                (field.options || []).forEach((opt) => {
                    const option = document.createElement('option');
                    option.value = opt.value;
                    option.textContent = opt.i18n_label ? t(opt.i18n_label, opt.label) : opt.label;
                    if (opt.value === field.value) option.selected = true;
                    control.appendChild(option);
                });

                const singleSelectMeta = window.initializeAdminSingleSelect?.(control, field);
                if (singleSelectMeta?.wrapper) {
                    control._singleSelect = singleSelectMeta;
                    control.classList.add('admin-select-native');
                    controlWrapper.appendChild(singleSelectMeta.wrapper);
                } else {
                    controlWrapper.appendChild(control);
                }
                break;
            }
            case 'number': {
                control = document.createElement('input');
                control.type = 'number';
                control.className = 'input';
                control.value = field.value ?? '';
                if (field.attributes?.min !== undefined) control.min = field.attributes.min;
                if (field.attributes?.max !== undefined) control.max = field.attributes.max;
                controlWrapper.appendChild(control);
                break;
            }
            case 'date': {
                control = document.createElement('input');
                control.type = 'date';
                control.className = 'input';
                control.value = field.value || '';
                controlWrapper.appendChild(control);
                break;
            }
            case 'datetime-local': {
                control = document.createElement('input');
                control.type = 'datetime-local';
                control.className = 'input';
                control.value = field.value || '';
                controlWrapper.appendChild(control);
                break;
            }
            case 'password': {
                control = document.createElement('input');
                control.type = 'password';
                control.className = 'input';
                control.value = '';
                control.placeholder = field.placeholder || '';
                control.autocomplete = 'new-password';
                controlWrapper.appendChild(control);
                break;
            }
            case 'textarea': {
                control = document.createElement('textarea');
                control.className = 'input textarea';
                control.value = field.value || '';
                control.placeholder = field.placeholder || '';
                control.rows = 3;
                controlWrapper.appendChild(control);
                break;
            }
            case 'string':
            default: {
                control = document.createElement('input');
                control.type = 'text';
                control.className = 'input';
                control.value = field.value || '';
                control.placeholder = field.placeholder || '';
                controlWrapper.appendChild(control);
                break;
            }
        }

        control.dataset.profileKey = field.key;
        attachProfileFieldHandler(field, control, row);

        row.appendChild(controlWrapper);
        return row;
    }

    function attachProfileFieldHandler(field, control, row) {
        const handler = () => {
            let value;
            if (field.type === 'boolean') {
                value = control.checked;
            } else if (field.type === 'number') {
                value = control.value === '' ? null : Number(control.value);
            } else {
                value = control.value;
            }
            updateProfileFieldValue(field.key, value, row);
        };

        if (field.type === 'boolean' || field.type === 'select') {
            control.addEventListener('change', handler);
        } else {
            control.addEventListener('input', handler);
            control.addEventListener('blur', handler);
        }
    }

    function updateProfileFieldValue(key, value, row) {
        // Get original value for comparison
        let originalValue;
        if (key.startsWith('lock.')) {
            const lockKey = key.replace('lock.', '');
            originalValue = state.profileOriginal?.lock?.[lockKey];
        } else {
            originalValue = state.profileOriginal?.[key];
        }

        // Normalize for comparison
        const normalizeForCompare = (v) => {
            if (v === null || v === undefined || v === '') return '';
            return String(v);
        };

        const isDirty = normalizeForCompare(value) !== normalizeForCompare(originalValue);

        if (isDirty) {
            state.profilePending[key] = value;
        } else {
            delete state.profilePending[key];
        }

        if (row) {
            row.classList.toggle('user-settings-field-pending', isDirty);
        }

        updateProfileDirtyIndicator();
        updateSaveButtonState();
    }

    function updateProfileDirtyIndicator() {
        const button = state.sidebarButtons.get(PROFILE_PAGE_KEY);
        if (!button) return;
        const hasDirty = Object.keys(state.profilePending).length > 0;
        button.dataset.dirty = hasDirty ? 'true' : 'false';
    }

    function bindResetTwofaConfirmModal() {
        if (resetTwofaConfirmCancelButton && resetTwofaConfirmCancelButton.dataset.bound !== 'true') {
            resetTwofaConfirmCancelButton.addEventListener('click', () => closeResetTwofaConfirmModal(false));
            resetTwofaConfirmCancelButton.dataset.bound = 'true';
        }

        if (resetTwofaConfirmPrimaryButton && resetTwofaConfirmPrimaryButton.dataset.bound !== 'true') {
            resetTwofaConfirmPrimaryButton.addEventListener('click', () => closeResetTwofaConfirmModal(true));
            resetTwofaConfirmPrimaryButton.dataset.bound = 'true';
        }

        if (resetTwofaConfirmOverlay && resetTwofaConfirmOverlay.dataset.bound !== 'true') {
            resetTwofaConfirmOverlay.addEventListener('click', (event) => {
                if (event.target === resetTwofaConfirmOverlay) {
                    closeResetTwofaConfirmModal(false);
                }
            });
            resetTwofaConfirmOverlay.dataset.bound = 'true';
        }

        if (
            !state.resetTwofaConfirmEscapeRegistration
            && typeof window.registerEscapeHandler === 'function'
        ) {
            state.resetTwofaConfirmEscapeRegistration = window.registerEscapeHandler({
                id: 'admin-user-settings-reset-2fa-confirm',
                priority: 180,
                isActive: () => Boolean(resetTwofaConfirmOverlay && !resetTwofaConfirmOverlay.hidden),
                close: () => closeResetTwofaConfirmModal(false),
            });
        }
    }

    function openResetTwofaConfirmModal() {
        if (!resetTwofaConfirmOverlay || !resetTwofaConfirmPrimaryButton) {
            return Promise.resolve(false);
        }

        state.resetTwofaConfirmLastFocusedElement = document.activeElement;
        resetTwofaConfirmOverlay.hidden = false;
        resetTwofaConfirmOverlay.setAttribute('aria-hidden', 'false');
        requestAnimationFrame(() => resetTwofaConfirmPrimaryButton.focus());

        return new Promise((resolve) => {
            state.resetTwofaConfirmResolver = resolve;
        });
    }

    function closeResetTwofaConfirmModal(confirmed) {
        if (!resetTwofaConfirmOverlay || resetTwofaConfirmOverlay.hidden) {
            return;
        }

        resetTwofaConfirmOverlay.setAttribute('aria-hidden', 'true');
        resetTwofaConfirmOverlay.hidden = true;

        const resolver = state.resetTwofaConfirmResolver;
        state.resetTwofaConfirmResolver = null;
        if (resolver) {
            resolver(Boolean(confirmed));
        }

        // Put keyboard users back where they started unless the reset action is
        // about to move focus to the loading button state.
        if (!confirmed && state.resetTwofaConfirmLastFocusedElement instanceof HTMLElement) {
            state.resetTwofaConfirmLastFocusedElement.focus();
        }
        state.resetTwofaConfirmLastFocusedElement = null;
    }

    async function handleResetTwofaClick(button) {
        if (!state.user?.id) {
            return;
        }
        if (hasPendingChanges()) {
            notifyWarning?.(
                t(
                    'admin_user_settings_reset_2fa_pending_changes',
                    'Save or discard pending changes before resetting 2FA.'
                )
            );
            return;
        }

        const reason = String(state.profileAccessReason || '').trim();
        if (reason.length < 3) {
            notifyError?.(
                t(
                    'admin_user_settings_reset_2fa_reason_required',
                    'Load the profile with an audit reason before resetting 2FA.'
                )
            );
            return;
        }

        const confirmed = await openResetTwofaConfirmModal();
        if (!confirmed) {
            return;
        }

        setButtonLoadingState(button, true, t('admin_user_settings_reset_2fa_loading', 'Resetting…'));
        try {
            await resetAdminUserTwofa({
                user_id: state.user.id,
                reason,
            });
            await Promise.all([
                loadUserProfile({ reason }),
                refreshSchemaValues(),
            ]);
            renderProfilePage();
            notifySuccess?.(t('admin_user_settings_reset_2fa_success', 'User 2FA reset successfully.'));
        } finally {
            setButtonLoadingState(button, false);
        }
    }

    function formatDateTimeLocal(isoString) {
        if (!isoString) return '';
        try {
            const date = new Date(isoString);
            if (isNaN(date.getTime())) return '';
            return date.toISOString().slice(0, 16);
        } catch {
            return '';
        }
    }

    function renderPage(pageKey) {
        const pageSchema = state.sectionByPage.get(pageKey);
        if (!pageSchema) {
            renderPlaceholder(t('admin_user_settings_unknown_page', 'Unknown settings page.'));
            return;
        }

        const values = state.currentValues[pageKey] || {};
        contentEl.innerHTML = '';

        const sectionTitle = pageSchema.i18n_title
            ? t(pageSchema.i18n_title, pageSchema.title || pageSchema.key)
            : (pageSchema.title || pageSchema.key);
        const sectionDescription = pageSchema.description
            ? (pageSchema.i18n_description
                ? t(pageSchema.i18n_description, pageSchema.description)
                : pageSchema.description)
            : '';
        const section = createProfileSection(sectionTitle, sectionDescription);
        contentEl.appendChild(section.section);

        if (!Array.isArray(pageSchema.fields) || !pageSchema.fields.length) {
            const empty = document.createElement('p');
            empty.className = 'user-settings-empty';
            empty.textContent = t('admin_user_settings_no_fields', 'No fields defined for this page.');
            section.body.appendChild(empty);
            setContentLoading(false);
            return;
        }

        const fragment = document.createDocumentFragment();
        const controllers = new Map();
        pageSchema.fields.forEach((field) => {
            if (field.hidden) {
                return;
            }
            const renderField = { ...field };
            if (renderField.key === 'allow_llm_to_access_personal_information') {
                renderField.preset_value = values[LLM_ACCESS_PRESET_FIELD_KEY];
            }
            const { row, controlWrapper } = createFieldLayout(renderField);
            const { root, control } = createFieldControl(renderField, {
                value: values[renderField.key],
                datasetKey: renderField.key,
            });
            controlWrapper.appendChild(root);
            attachFieldHandlers({ field: renderField, control, row, pageKey });
            controllers.set(renderField.key, { field: renderField, control, row });

            const originalValue = state.originalValues[pageKey]?.[renderField.key];
            const isDirty = !valuesAreEqual(renderField, values[renderField.key], originalValue);
            row.classList.toggle('user-settings-field-pending', Boolean(isDirty));

            fragment.appendChild(row);
        });

        section.body.appendChild(fragment);
        state.activeControllers = controllers;
        attachDependencyListeners(controllers);
        updateDependentFieldsVisibility(controllers);
        setContentLoading(false);

        // Attach error clear listeners for validation
        const controlsArray = pageSchema.fields
            .filter((field) => !field.hidden)
            .map((field) => {
            const selector = `[data-setting-key="${field.key}"], [name="${field.key}"]`;
            const control = contentEl?.querySelector(selector);
            return control ? { field, control } : null;
        }).filter(Boolean);
        window.FieldValidation?.attachErrorClearListeners(controlsArray);
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
            if (field.key === 'allow_llm_to_access_personal_information') {
                control.addEventListener('llmaccesschange', handler);
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
        window.syncSectionBodyLastVisibleRow?.(contentEl);
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

    function attachFieldHandlers({ field, control, row, pageKey }) {
        const handleStandardChange = () => {
            try {
                const normalizedValue = normalizeFieldValue(field, readControlValue(field, control));
                updateFieldValue(pageKey, field, normalizedValue, row);
            } catch (error) {
                notifyError?.(error?.message || t('admin_user_settings_validation_failed', 'Validation failed.'));
                revertFieldValue(pageKey, field, control);
            }
        };

        let fieldType = field.type;
        // Special case: detect LLM access permissions field by key name
        if (field?.key === 'allow_llm_to_access_personal_information') {
            fieldType = 'llm_access_permissions';
        }

        switch (fieldType) {
            case 'boolean':
            case 'select':
            case 'number':
                control.addEventListener('change', handleStandardChange);
                break;
            case 'boolean_map':
                control.addEventListener('booleanmapchange', (event) => {
                    try {
                        const normalizedValue = normalizeFieldValue(
                            field,
                            event?.detail?.value ?? readControlValue(field, control)
                        );
                        updateFieldValue(pageKey, field, normalizedValue, row);
                    } catch (error) {
                        notifyError?.(error?.message || t('admin_user_settings_validation_failed', 'Validation failed.'));
                        revertFieldValue(pageKey, field, control);
                    }
                });
                break;
            case 'llm_access_permissions':
                control.addEventListener('llmaccesschange', (event) => {
                    try {
                        const detail = event?.detail || {};
                        const normalizedValue = normalizeFieldValue(
                            field,
                            detail.permissions ?? readControlValue(field, control)
                        );
                        updateFieldValue(pageKey, field, normalizedValue, row);
                        syncLlmAccessPreset(pageKey, detail.preset);
                    } catch (error) {
                        notifyError?.(error?.message || t('admin_user_settings_validation_failed', 'Validation failed.'));
                        revertFieldValue(pageKey, field, control);
                    }
                });
                break;
            case 'string_list':
            case 'string':
            default:
                control.addEventListener('input', handleStandardChange);
                control.addEventListener('blur', handleStandardChange);
                break;
        }
    }

    function syncLlmAccessPreset(pageKey, preset) {
        if (typeof preset !== 'string' || !preset) {
            return;
        }
        const presetField = state.sectionByPage
            .get(pageKey)
            ?.fields?.find((candidate) => candidate.key === LLM_ACCESS_PRESET_FIELD_KEY);
        if (!presetField) {
            return;
        }
        const normalizedPreset = normalizeFieldValue(presetField, preset);
        updateFieldValue(pageKey, presetField, normalizedPreset, null);
    }

    function revertFieldValue(pageKey, field, control) {
        const currentValue = state.currentValues[pageKey]?.[field.key];
        if (currentValue === undefined) {
            return;
        }
        applyControlValue(control, field, currentValue);
    }

    function readControlValue(field, control) {
        let fieldType = field.type;
        // Special case: detect LLM access permissions field by key name
        if (field?.key === 'allow_llm_to_access_personal_information') {
            fieldType = 'llm_access_permissions';
        }
        switch (fieldType) {
            case 'boolean':
                return control.checked;
            case 'llm_access_permissions':
                try {
                    return JSON.parse(control.dataset.llmAccessPermissions || '{}');
                } catch {
                    return {};
                }
            case 'boolean_map':
                try {
                    return JSON.parse(control.dataset.booleanMap || '{}');
                } catch {
                    return {};
                }
            case 'number':
            case 'string_list':
            case 'select':
            case 'string':
            default:
                return control.value;
        }
    }

    function updateFieldValue(pageKey, field, nextValue, row) {
        state.currentValues[pageKey] = state.currentValues[pageKey] || {};
        state.currentValues[pageKey][field.key] = cloneSettingsValue(nextValue);

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
            row.classList.toggle('user-settings-field-pending', isDirty);
        }

        updatePageDirtyIndicator(pageKey);
        updateSaveButtonState();
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

    function hasPendingChanges() {
        return Object.keys(state.pendingChanges).length > 0 || Object.keys(state.profilePending).length > 0;
    }

    function updateSaveButtonState() {
        if (state.saveInFlight) {
            saveButton.disabled = true;
            return;
        }
        saveButton.disabled = !state.user?.id || !hasPendingChanges();
    }

    async function handleSaveClick() {
        if (!state.user?.id || !hasPendingChanges()) {
            return;
        }

        // Validate required fields using shared FieldValidation
        const pageSchema = state.sectionByPage.get(state.activePage);
        if (pageSchema?.fields?.length) {
            const controlsArray = pageSchema.fields.map((field) => {
                const selector = `[data-setting-key="${field.key}"], [name="${field.key}"]`;
                const control = contentEl?.querySelector(selector);
                return control ? { field, control } : null;
            }).filter(Boolean);

            if (controlsArray.length && !window.FieldValidation?.validate(controlsArray)) {
                return;
            }
        }

        const profileNeedsStepUp = Object.keys(state.profilePending).some((key) => (
            key === 'email' || key === 'password'
        ));
        state.saveInFlight = true;
        updateSaveButtonState();
        if (profileNeedsStepUp) {
            if (typeof window.ensureSecurityStepUp !== 'function') {
                notifyError?.(t('step_up_methods_load_failed', 'Verification methods could not be loaded. Close this dialog and try again.'));
                state.saveInFlight = false;
                updateSaveButtonState();
                return;
            }
            if (!await window.ensureSecurityStepUp()) {
                state.saveInFlight = false;
                updateSaveButtonState();
                return;
            }
        }

        setButtonLoadingState(saveButton, true, t('admin_user_settings_busy_saving', 'Saving…'));
        clearStatus();

        const promises = [];

        // Save profile changes if any
        if (Object.keys(state.profilePending).length > 0) {
            const profilePayload = buildProfilePayload();
            promises.push(
                updateAdminUserProfile(profilePayload)
                    .then(() => {
                        applyProfilePendingToOriginal();
                    })
            );
        }

        // Save settings changes if any
        if (Object.keys(state.pendingChanges).length > 0) {
            const settingsPayload = {
                user_id: state.user.id,
                settings: clonePendingChanges(),
            };
            promises.push(
                updateAdminUserSettings(settingsPayload)
                    .then(() => {
                        applyPendingToOriginal();
                    })
            );
        }

        Promise.all(promises)
            .then(() => {
                notifySuccess?.(t('admin_user_settings_saved', 'User settings saved successfully.'));
                window.activateAdminPage?.('users');
            })
            .catch((error) => {
                console.error('Failed to save user settings', error);
                const fallbackMessage = t('admin_user_settings_save_failed', 'Failed to save user settings.');
                notifyError?.(error?.message || fallbackMessage);
                setStatus('error', error?.message || fallbackMessage);
            })
            .finally(() => {
                state.saveInFlight = false;
                setButtonLoadingState(saveButton, false);
                updateSaveButtonState();
            });
    }

    function buildProfilePayload() {
        const payload = { user_id: state.user.id };
        if (state.profileAccessReason) {
            payload.reason = state.profileAccessReason;
        }
        const lockFields = {};
        let hasLockChanges = false;

        Object.entries(state.profilePending).forEach(([key, value]) => {
            if (
                state.profile?.externally_managed
                && ['email', 'first_name', 'last_name', 'password', 'wrong_sign_in_attempts'].includes(key)
            ) {
                return;
            }
            if (key.startsWith('lock.')) {
                const lockKey = key.replace('lock.', '');
                lockFields[lockKey] = value;
                hasLockChanges = true;
            } else {
                payload[key] = value;
            }
        });

        if (hasLockChanges) {
            // Merge with existing lock data
            const currentLock = state.profile?.lock || {};
            payload.lock = {
                is_locked: lockFields.is_locked !== undefined ? lockFields.is_locked : currentLock.is_locked,
                lock_until: lockFields.lock_until !== undefined ? lockFields.lock_until : currentLock.lock_until,
                type: lockFields.type !== undefined ? lockFields.type : currentLock.type,
                reason: lockFields.reason !== undefined ? lockFields.reason : currentLock.reason,
            };
        }

        return payload;
    }

    function applyProfilePendingToOriginal() {
        Object.entries(state.profilePending).forEach(([key, value]) => {
            if (key.startsWith('lock.')) {
                const lockKey = key.replace('lock.', '');
                if (!state.profileOriginal.lock) state.profileOriginal.lock = {};
                state.profileOriginal.lock[lockKey] = value;
                if (state.profile?.lock) state.profile.lock[lockKey] = value;
            } else {
                state.profileOriginal[key] = value;
                if (state.profile) state.profile[key] = value;
            }
        });
        state.profilePending = {};
        updateProfileDirtyIndicator();

        // Update row indicators
        const dirtyRows = contentEl.querySelectorAll('.user-settings-field-pending');
        dirtyRows.forEach((row) => row.classList.remove('user-settings-field-pending'));
    }

    function clonePendingChanges() {
        const clone = {};
        Object.entries(state.pendingChanges).forEach(([page, changes]) => {
            if (!changes || !Object.keys(changes).length) {
                return;
            }
            clone[page] = Object.fromEntries(
                Object.entries(changes).map(([key, value]) => [key, cloneSettingsValue(value)])
            );
        });
        return clone;
    }

    function applyPendingToOriginal() {
        Object.entries(state.pendingChanges).forEach(([page, changes]) => {
            if (!changes) {
                return;
            }
            state.originalValues[page] = state.originalValues[page] || {};
            state.currentValues[page] = state.currentValues[page] || {};
            Object.entries(changes).forEach(([key, value]) => {
                state.originalValues[page][key] = cloneSettingsValue(value);
                state.currentValues[page][key] = cloneSettingsValue(value);
            });
        });
        state.pendingChanges = {};
        state.sidebarButtons.forEach((button) => {
            button.dataset.dirty = 'false';
        });
        const dirtyRows = contentEl.querySelectorAll('.user-settings-field-pending');
        dirtyRows.forEach((row) => row.classList.remove('user-settings-field-pending'));
        updateSaveButtonState();
    }

    function discardPendingChanges() {
        teardown();
        state.user = null;
        state.profile = null;
        state.profileOriginal = null;
        state.profilePending = {};
        state.profileAccessReason = '';
    }

    function handleBackNavigation() {
        if (typeof window.unsavedChangesManager?.confirmIfNeeded === 'function') {
            const prompted = window.unsavedChangesManager.confirmIfNeeded({
                id: UNSAVED_GUARD_ID,
                onConfirm: () => window.activateAdminPage?.('users'),
            });
            if (prompted) {
                return;
            }
        }
        window.activateAdminPage?.('users');
    }

    function cloneSettingsObject(source) {
        if (!source || typeof source !== 'object') {
            return {};
        }
        return Object.fromEntries(
            Object.entries(source).map(([key, value]) => [key, cloneSettingsValue(value)])
        );
    }

    function setUserSummary(user) {
        const initials = buildInitials(user);
        if (avatarEl) {
            avatarEl.textContent = initials;
        }
        if (headerTitle) {
            const fullName = [user.firstName, user.lastName].filter(Boolean).join(' ').trim();
            headerTitle.textContent = fullName || user.email || t('admin_user_settings_header_title', 'Edit User Settings');
        }
        if (headerSubtitle) {
            headerSubtitle.textContent = user.email
                ? `${t('admin_user_settings_editing_prefix', 'Editing')} ${user.email}`
                : t('admin_user_settings_selected_user_subtitle', 'Update the selected user’s preferences.');
        }
    }

    function resetUserSummary() {
        if (avatarEl) {
            avatarEl.textContent = 'US';
        }
        if (headerTitle) {
            headerTitle.textContent = t('admin_user_settings_header_title', 'Edit User Settings');
        }
        if (headerSubtitle) {
            headerSubtitle.textContent = t('admin_user_settings_header_subtitle', 'Select a user from the list to begin.');
        }
    }

    function buildInitials(user) {
        const first = (user.firstName || '').trim();
        const last = (user.lastName || '').trim();
        if (!first && !last && user.email) {
            return user.email.slice(0, 2).toUpperCase();
        }
        const letters = [first?.[0], last?.[0]].filter(Boolean).join('');
        return (letters || 'US').toUpperCase();
    }

    async function openUserSettingsPage(user) {
        if (!user || !user.id) {
            notifyError?.(t('admin_user_settings_missing_user_id', 'Failed to open user settings: missing user ID.'));
            return;
        }

        init();
        cancelSchemaRequest();
        resetStateValues();
        clearStatus();

        state.user = {
            id: user.id,
            firstName: user.firstName || '',
            lastName: user.lastName || '',
            email: user.email || '',
        };
        const accessReason = String(user.reason || '').trim();
        state.profile = null;
        state.profileOriginal = null;
        state.profilePending = {};
        state.profileAccessReason = accessReason;
        setUserSummary(state.user);

        setContentLoading(true);
        contentEl.innerHTML = '';
        sidebarEl.innerHTML = '';
        appendSidebarEmptyState();

        try {
            const schema = await ensureSchema({ includeValuesForUser: state.user.id });
            if (!schema.length) {
                renderPlaceholder(t('admin_user_settings_no_schema', 'No user settings schema available.'));
                setStatus('info', t('admin_user_settings_schema_empty', 'User settings schema is empty.'));
                return;
            }
            createSidebar(schema);
            state.activePage = null;
            if (accessReason.length >= 3) {
                // The users table already collected the audit reason before navigation,
                // so the edit page can load the sensitive profile without showing a
                // second reason form.
                const [profile] = await Promise.all([
                    loadUserProfile({ reason: accessReason }),
                    loadGroups(),
                ]);
                if (!profile) {
                    throw new Error(t('admin_user_settings_profile_load_failed', 'Unable to load user profile.'));
                }
            }
            // Start with Profile page
            setActivePage(PROFILE_PAGE_KEY);
        } catch (error) {
            console.error('Failed to load user settings schema', error);
            const fallbackMessage = state.profileAccessReason
                ? t('admin_user_settings_profile_load_failed', 'Unable to load user profile.')
                : t('admin_user_settings_failed_schema', 'Failed to load user settings schema.');
            notifyError?.(error?.message || fallbackMessage);
            renderPlaceholder(error?.message || t('admin_user_settings_schema_unavailable', 'Unable to load user settings schema.'));
        } finally {
            setContentLoading(false);
        }

        window.activateAdminPage?.('user-settings', { history: 'none' });
        updateSaveButtonState();
    }
    window.initAdminUserSettingsPage = init;
    window.teardownAdminUserSettingsPage = teardown;
    window.openAdminUserSettingsPage = openUserSettingsPage;
})();
