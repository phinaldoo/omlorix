(function () {
    const dom = {
        page: document.getElementById('managedGroupsPage'),
        navItem: document.getElementById('managedGroupsNavItem'),
        search: document.getElementById('managedGroupsSearch'),
        list: document.getElementById('managedGroupsList'),
        empty: document.getElementById('managedGroupsEmpty'),
        detail: document.getElementById('managedGroupsDetail'),
        title: document.getElementById('managedGroupsTitle'),
        path: document.getElementById('managedGroupsPath'),
        meta: document.getElementById('managedGroupsMeta'),
        tabs: Array.from(document.querySelectorAll('.managed-groups-tab')),
        panels: Array.from(document.querySelectorAll('.managed-groups-panel')),
        contextEnabled: document.getElementById('managedGroupContextEnabled'),
        context: document.getElementById('managedGroupContext'),
        allowTemporaryChat: document.getElementById('managedGroupAllowTemporaryChat'),
        allowFileUploads: document.getElementById('managedGroupAllowFileUploads'),
        maxFiles: document.getElementById('managedGroupMaxFiles'),
        maxFileSize: document.getElementById('managedGroupMaxFileSize'),
        tempEnabled: document.getElementById('managedGroupTempEnabled'),
        tempMaxActive: document.getElementById('managedGroupTempMaxActive'),
        tempCredentialLength: document.getElementById('managedGroupTempCredentialLength'),
        enableProjects: document.getElementById('managedGroupEnableProjects'),
        enableTodo: document.getElementById('managedGroupEnableTodo'),
        enableNotes: document.getElementById('managedGroupEnableNotes'),
        enableMemories: document.getElementById('managedGroupEnableMemories'),
        memoryModel: document.getElementById('managedGroupMemoryModel'),
        enableSkills: document.getElementById('managedGroupEnableSkills'),
        enablePrompts: document.getElementById('managedGroupEnablePrompts'),
        enableBookmarks: document.getElementById('managedGroupEnableBookmarks'),
        enableAgents: document.getElementById('managedGroupEnableAgents'),
        enableAutomations: document.getElementById('managedGroupEnableAutomations'),
        allowProjectShare: document.getElementById('managedGroupAllowProjectShare'),
        allowTodoShare: document.getElementById('managedGroupAllowTodoShare'),
        allowNotesShare: document.getElementById('managedGroupAllowNotesShare'),
        allowSkillShare: document.getElementById('managedGroupAllowSkillShare'),
        allowPromptShare: document.getElementById('managedGroupAllowPromptShare'),
        allowBookmarkShare: document.getElementById('managedGroupAllowBookmarkShare'),
        allowAgentShare: document.getElementById('managedGroupAllowAgentShare'),
        allowChatShare: document.getElementById('managedGroupAllowChatShare'),
        allowArtifactShare: document.getElementById('managedGroupAllowArtifactShare'),
        saveSettings: document.getElementById('managedGroupsSaveSettings'),
        promotionUser: document.getElementById('managedGroupsPromotionUser'),
        promotionRole: document.getElementById('managedGroupsPromotionRole'),
        promoteMember: document.getElementById('managedGroupsPromoteMember'),
        managersList: document.getElementById('managedGroupsManagersList'),
        managersMore: document.getElementById('managedGroupsManagersMore'),
        membersList: document.getElementById('managedGroupsMembersList'),
        membersMore: document.getElementById('managedGroupsMembersMore'),
        tempCreateCount: document.getElementById('managedGroupsTempCount'),
        tempCreateExpiryHours: document.getElementById('managedGroupsTempExpiryHours'),
        createTemp: document.getElementById('managedGroupsCreateTemp'),
        tempCredentials: document.getElementById('managedGroupsTempCredentials'),
        temporaryList: document.getElementById('managedGroupsTemporaryList'),
        temporaryMore: document.getElementById('managedGroupsTemporaryMore'),
    };

    if (!dom.page) {
        return;
    }

    function managedGroupsT(key, fallback) {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    /**
     * Resolve an icon from the shared icon library (js/common/icons.js).
     * The library is a global lexical binding that is absent in unit tests,
     * so resolve it defensively and fall back to icon-less rendering.
     */
    function iconMarkup(name) {
        const library = (typeof Icons !== 'undefined' && Icons) || null;
        return library?.[name] || '';
    }

    /**
     * Build 1–2 letter initials for avatar chips. Names and emails are split
     * on whitespace and common separators; the first and last word contribute
     * one letter each.
     */
    function initialsFromText(text) {
        const words = String(text || '')
            .trim()
            .split(/[\s@._+\-/\\]+/)
            .filter(Boolean);
        if (!words.length) return '–';
        const first = words[0].charAt(0);
        const last = words.length > 1 ? words[words.length - 1].charAt(0) : '';
        return (first + last).toUpperCase();
    }

    /**
     * Copy text with clipboard availability guards so failures can be shown
     * as translated errors instead of unhandled rejections.
     */
    async function copyTextToClipboard(text) {
        const clipboard = (typeof navigator !== 'undefined') ? navigator.clipboard : null;
        if (!clipboard || typeof clipboard.writeText !== 'function') {
            throw new Error(managedGroupsT('us_managed_groups_credential_copy_failed', 'Could not copy to the clipboard.'));
        }
        await clipboard.writeText(text);
    }

    /**
     * Rebuild the content of a copy button (icon + label) in its default or
     * transient "copied" state. `dataset.defaultLabel` carries the idle label.
     */
    function setCopyButtonState(button, copied) {
        button.classList.toggle('is-copied', copied);
        button.innerHTML = '';
        const iconWrapper = document.createElement('span');
        iconWrapper.className = 'managed-groups-copy-btn-icon';
        iconWrapper.setAttribute('aria-hidden', 'true');
        iconWrapper.innerHTML = iconMarkup(copied ? 'check' : 'copy');
        const label = document.createElement('span');
        label.className = 'managed-groups-copy-btn-label';
        label.textContent = copied
            ? managedGroupsT('us_managed_groups_credential_copied', 'Copied')
            : button.dataset.defaultLabel || managedGroupsT('us_managed_groups_copy_credential', 'Copy');
        button.appendChild(iconWrapper);
        button.appendChild(label);
    }

    /**
     * Create a small ghost "copy" button with an optimistic copied state that
     * reverts after a short delay.
     */
    function createCopyButton({ defaultLabel, getText }) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'managed-groups-copy-btn';
        button.dataset.defaultLabel = defaultLabel;
        setCopyButtonState(button, false);
        button.addEventListener('click', async () => {
            try {
                await copyTextToClipboard(getText());
                setCopyButtonState(button, true);
                if (button._copiedTimer) clearTimeout(button._copiedTimer);
                button._copiedTimer = setTimeout(() => setCopyButtonState(button, false), 1600);
            } catch (error) {
                window.notifyError?.(error.message);
            }
        });
        return button;
    }

    const state = {
        visible: false,
        loaded: false,
        groups: [],
        filtered: [],
        selectedGroupId: null,
        detail: null,
        detailRequestId: 0,
        promotionCandidates: [],
        renderedTempExpiryGroupId: null,
    };

    // Keep the feature controls declarative so rendering and request payloads
    // cannot drift apart when a new manager-editable switch is introduced.
    const FEATURE_SETTING_CONTROLS = [
        { control: dom.enableProjects, page: 'projects', key: 'enable_projects' },
        { control: dom.enableTodo, page: 'todo', key: 'enabled_todo' },
        { control: dom.enableNotes, page: 'notes', key: 'enabled_notes' },
        { control: dom.enableMemories, page: 'memories', key: 'enabled_memories' },
        { control: dom.enableSkills, page: 'skills', key: 'enabled_skills' },
        { control: dom.enablePrompts, page: 'prompts', key: 'enabled_prompts' },
        { control: dom.enableBookmarks, page: 'bookmarks', key: 'enabled_bookmarks' },
        { control: dom.enableAgents, page: 'agents', key: 'allow_agents' },
        { control: dom.enableAutomations, page: 'automations', key: 'enabled_automations' },
        { control: dom.allowProjectShare, page: 'projects', key: 'allow_project_share' },
        { control: dom.allowTodoShare, page: 'todo', key: 'allow_todo_list_share' },
        { control: dom.allowNotesShare, page: 'notes', key: 'allow_notes_share' },
        { control: dom.allowSkillShare, page: 'skills', key: 'allow_skill_share' },
        { control: dom.allowPromptShare, page: 'prompts', key: 'allow_prompt_share' },
        { control: dom.allowBookmarkShare, page: 'bookmarks', key: 'allow_bookmark_share' },
        { control: dom.allowAgentShare, page: 'agents', key: 'allow_agent_share' },
        { control: dom.allowChatShare, page: 'sharing', key: 'enable_chat_sharing' },
        { control: dom.allowArtifactShare, page: 'sharing', key: 'enable_artifact_sharing' },
    ];
    const FEATURE_SHARE_DEPENDENCIES = [
        { feature: dom.enableProjects, share: dom.allowProjectShare },
        { feature: dom.enableTodo, share: dom.allowTodoShare },
        { feature: dom.enableNotes, share: dom.allowNotesShare },
        { feature: dom.enableSkills, share: dom.allowSkillShare },
        { feature: dom.enablePrompts, share: dom.allowPromptShare },
        { feature: dom.enableBookmarks, share: dom.allowBookmarkShare },
        { feature: dom.enableAgents, share: dom.allowAgentShare },
    ];

    const ROLE_TRANSLATIONS = {
        member: ['us_managed_groups_role_member', 'Member'],
        owner: ['us_managed_groups_role_owner', 'Owner'],
        manager: ['us_managed_groups_role_manager', 'Manager'],
        coordinator: ['us_managed_groups_role_coordinator', 'Coordinator'],
    };
    const ROLE_PRIORITY = { member: 0, coordinator: 1, manager: 2, owner: 3 };
    const STATUS_TRANSLATIONS = {
        active: ['us_managed_groups_status_active', 'Active'],
        expired: ['us_managed_groups_status_expired', 'Expired'],
        revoked: ['us_managed_groups_status_revoked', 'Revoked'],
        inactive: ['us_managed_groups_status_inactive', 'Inactive'],
        pending: ['us_managed_groups_status_pending', 'Pending'],
        deleted: ['us_managed_groups_status_deleted', 'Deleted'],
    };

    function translatedValue(table, value, fallback) {
        const translation = table[value];
        return translation ? managedGroupsT(translation[0], translation[1]) : fallback;
    }

    function translatedRole(role) {
        return translatedValue(ROLE_TRANSLATIONS, role, managedGroupsT('us_managed_groups_role_unknown', 'Unknown role'));
    }

    function translatedStatus(status) {
        return translatedValue(STATUS_TRANSLATIONS, status, managedGroupsT('us_managed_groups_status_unknown', 'Unknown status'));
    }

    function hasCapability(capability, detail = state.detail) {
        return Boolean(detail?.group?.capabilities?.includes(capability));
    }

    async function apiJson(path, options = {}) {
        const response = await window.authedFetch(path, {
            method: options.method || 'GET',
            headers: {
                'Content-Type': 'application/json',
                ...(options.headers || {}),
            },
            body: options.body ? JSON.stringify(options.body) : undefined,
        });
        if (!response.ok) {
            const statusKeys = {
                400: ['us_managed_groups_error_invalid', 'The request is not valid. Check the values and try again.'],
                403: ['us_managed_groups_error_forbidden', 'You do not have permission to perform this action.'],
                404: ['us_managed_groups_error_not_found', 'The requested group or user no longer exists.'],
                409: ['us_managed_groups_error_conflict', 'The action conflicts with the current group state. Refresh and try again.'],
            };
            const translation = statusKeys[response.status];
            const message = translation
                ? managedGroupsT(translation[0], translation[1])
                : managedGroupsT('us_managed_groups_request_failed', 'Request failed');
            try {
                // Consume the response so the connection can be reused. Backend
                // details are intentionally not shown because they are not a
                // stable, translated user interface contract.
                await response.json();
            } catch (_) {
                // ignore
            }
            throw new Error(message);
        }
        // Handle no-content responses (204, empty body, or no JSON content-type)
        if (response.status === 204 ||
            response.headers.get('content-length') === '0' ||
            !response.headers.get('content-type')?.includes('application/json')) {
            return null;
        }
        return response.json();
    }

    function setVisibility(visible) {
        state.visible = Boolean(visible);
        if (dom.navItem) {
            dom.navItem.style.display = visible ? '' : 'none';
        }
        if (dom.page) {
            dom.page.style.display = visible ? '' : 'none';
        }
    }

    function activateTab(tabKey) {
        dom.tabs.forEach((tab) => {
            const isActive = tab.dataset.managedTab === tabKey;
            tab.classList.toggle('active', isActive);
            tab.setAttribute('aria-selected', isActive.toString());
            tab.setAttribute('tabindex', isActive ? '0' : '-1');
        });
        dom.panels.forEach((panel) => {
            const isActive = panel.dataset.managedPanel === tabKey;
            panel.classList.toggle('active', isActive);
            panel.setAttribute('aria-hidden', (!isActive).toString());
            panel.hidden = !isActive;
        });
    }

    // Optional count badges inside tab buttons. querySelector is guarded so
    // the unit test harness (whose fake tabs lack that API) keeps working.
    const tabCountBadges = {};
    dom.tabs.forEach((tab) => {
        tabCountBadges[tab.dataset.managedTab] = tab.querySelector?.('.managed-groups-tab-count') || null;
    });

    /**
     * Reflect the loaded member/manager/temporary account totals on the tab
     * badges. The badge is hidden when the count is unknown or zero so tabs
     * stay visually calm.
     */
    function renderTabCounts(detail) {
        const collections = {
            managers: { items: detail.managers, paginationKey: 'managers' },
            members: { items: detail.members, paginationKey: 'members' },
            temporary: { items: detail.temporary_accounts, paginationKey: 'temporary_accounts' },
        };
        Object.entries(collections).forEach(([tabKey, { items, paginationKey }]) => {
            const badge = tabCountBadges[tabKey];
            if (!badge) return;
            const total = detail.pagination?.[paginationKey]?.total;
            const count = Number.isFinite(total) ? total : (Array.isArray(items) ? items.length : 0);
            badge.hidden = count <= 0;
            badge.textContent = count > 0 ? String(count) : '';
        });
    }

    function renderGroupList() {
        if (!dom.list) {
            return;
        }
        dom.list.innerHTML = '';
        const groups = state.filtered || [];
        if (!groups.length) {
            const empty = document.createElement('div');
            empty.className = 'managed-groups-list-empty';
            empty.textContent = managedGroupsT('us_managed_groups_list_empty', 'No managed groups found.');
            dom.list.appendChild(empty);
            return;
        }
        groups.forEach((group) => {
            const isActive = group.id === state.selectedGroupId;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'managed-groups-list-item';
            if (isActive) {
                button.classList.add('active');
                button.setAttribute('aria-current', 'true');
            }
            const avatar = document.createElement('span');
            avatar.className = 'managed-groups-list-item-avatar';
            avatar.setAttribute('aria-hidden', 'true');
            avatar.textContent = initialsFromText(group.name);
            const text = document.createElement('div');
            text.className = 'managed-groups-list-item-text';
            const title = document.createElement('div');
            title.className = 'managed-groups-list-item-title';
            title.textContent = group.name;
            const meta = document.createElement('div');
            meta.className = 'managed-groups-list-item-meta';
            // The last path segment is the group itself, so show only the
            // parent path as hierarchy context.
            const parentPath = (group.path || []).slice(0, -1).join(' / ');
            meta.textContent = parentPath;
            if (!meta.textContent) meta.hidden = true;
            text.appendChild(title);
            text.appendChild(meta);
            const role = document.createElement('span');
            role.className = 'managed-groups-list-item-role';
            role.textContent = translatedRole(group.role);
            button.appendChild(avatar);
            button.appendChild(text);
            button.appendChild(role);
            button.addEventListener('click', () => loadGroupDetail(group.id));
            dom.list.appendChild(button);
        });
    }

    function filterGroups() {
        const needle = (dom.search?.value || '').trim().toLowerCase();
        if (!needle) {
            state.filtered = [...state.groups];
        } else {
            state.filtered = state.groups.filter((group) => {
                const haystack = [group.name, ...(group.path || []), group.role].join(' ').toLowerCase();
                return haystack.includes(needle);
            });
        }
        renderGroupList();
    }

    function renderMeta(detail) {
        dom.meta.innerHTML = '';
        const badges = [translatedRole(detail.group.role)];
        badges.forEach((value) => {
            const badge = document.createElement('span');
            badge.textContent = value;
            dom.meta.appendChild(badge);
        });
    }

    function setInputValue(input, value, fallback = '') {
        if (!input) return;
        if (input.type === 'checkbox') {
            input.checked = Boolean(value);
        } else {
            input.value = value ?? fallback;
        }
    }

    /**
     * The group context textarea only exists while the feature is enabled —
     * hiding it keeps the settings card free of dead inputs.
     */
    function syncContextFieldVisibility() {
        const contextField = dom.context?.closest?.('.managed-groups-input-field');
        if (!contextField || !dom.contextEnabled) return;
        contextField.hidden = !dom.contextEnabled.checked;
    }

    function syncFeatureControlAvailability(detail = state.detail) {
        const canManageSettings = hasCapability('manage_settings', detail);
        FEATURE_SETTING_CONTROLS.forEach(({ control }) => {
            if (!control) return;
            control.disabled = !canManageSettings;
            control.setAttribute('aria-disabled', control.disabled.toString());
        });
        FEATURE_SHARE_DEPENDENCIES.forEach(({ feature, share }) => {
            if (!share) return;
            if (!feature?.checked) share.disabled = true;
            share.setAttribute('aria-disabled', share.disabled.toString());
        });
        if (dom.memoryModel) {
            dom.memoryModel.disabled = !canManageSettings || !dom.enableMemories?.checked;
            dom.memoryModel.setAttribute('aria-disabled', dom.memoryModel.disabled.toString());
            syncCustomSelect(dom.memoryModel, dom.memoryModel._singleSelect);
        }
    }

    function renderMemoryModelOptions(detail) {
        if (!dom.memoryModel) return;
        const selectedValue = String(detail.settings?.memories?.memory_model_id || '');
        const modelMeta = ensureCustomSelect(dom.memoryModel, {
            placeholderKey: 'us_managed_groups_memory_model_current',
            placeholderFallback: 'Use current chat model',
            emptyValueIsOption: true,
        });
        dom.memoryModel.innerHTML = '';
        const currentOption = document.createElement('option');
        currentOption.value = '';
        currentOption.textContent = managedGroupsT(
            'us_managed_groups_memory_model_current',
            'Use current chat model',
        );
        dom.memoryModel.appendChild(currentOption);
        (detail.memory_model_options || []).forEach((entry) => {
            const value = String(entry?.value || '').trim();
            if (!value) return;
            const option = document.createElement('option');
            option.value = value;
            option.textContent = String(entry?.label || value);
            dom.memoryModel.appendChild(option);
        });
        dom.memoryModel.value = Array.from(dom.memoryModel.options || []).some(
            (option) => option.value === selectedValue,
        ) ? selectedValue : '';
        syncCustomSelect(dom.memoryModel, modelMeta);
    }

    function renderSettings(detail) {
        const settings = detail.settings || {};
        setInputValue(dom.contextEnabled, settings.context?.enable_group_context);
        setInputValue(dom.context, settings.context?.group_context, '');
        setInputValue(dom.allowTemporaryChat, settings.chat?.allow_temporary_chat);
        setInputValue(dom.allowFileUploads, settings.files?.allow_file_uploads);
        setInputValue(dom.maxFiles, settings.files?.max_files_upload_count, '');
        setInputValue(dom.maxFileSize, settings.files?.max_user_files_size_gb, '');
        setInputValue(dom.tempEnabled, settings.temporary_accounts?.enabled);
        setInputValue(dom.tempMaxActive, settings.temporary_accounts?.max_active_accounts, '');
        setInputValue(dom.tempCredentialLength, settings.temporary_accounts?.credential_length, '');
        const renderedGroupId = detail.group?.id || null;
        if (
            !String(dom.tempCreateExpiryHours?.value || '').trim()
            || state.renderedTempExpiryGroupId !== renderedGroupId
        ) {
            setInputValue(dom.tempCreateExpiryHours, 8, '');
        }
        state.renderedTempExpiryGroupId = renderedGroupId;
        FEATURE_SETTING_CONTROLS.forEach(({ control, page, key }) => {
            setInputValue(control, settings[page]?.[key]);
        });
        renderMemoryModelOptions(detail);

        const canManageSettings = hasCapability('manage_settings', detail);
        const settingsPanel = document.querySelector('[data-managed-panel="settings"]');
        settingsPanel?.querySelectorAll('input, textarea, select').forEach((control) => {
            control.disabled = !canManageSettings;
        });
        syncContextFieldVisibility();
        syncFeatureControlAvailability(detail);
        if (dom.saveSettings) {
            dom.saveSettings.hidden = !canManageSettings;
            dom.saveSettings.disabled = !canManageSettings;
        }

        const canPromoteMembers = hasCapability('promote_members', detail);
        const promotionForm = dom.promoteMember?.closest('.managed-groups-inline-form');
        if (promotionForm) promotionForm.hidden = !canPromoteMembers;
        renderPromotionCandidates();

        const temporaryEnabled = Boolean(settings.temporary_accounts?.enabled);
        const canManageTemporary = hasCapability('manage_temporary_accounts', detail) && temporaryEnabled;
        const temporaryForm = dom.createTemp?.closest('.managed-groups-inline-form');
        temporaryForm?.querySelectorAll('input, button').forEach((control) => {
            control.disabled = !canManageTemporary;
        });
        if (temporaryForm) {
            temporaryForm.hidden = !hasCapability('manage_temporary_accounts', detail);
        }
    }

    function formatTimestamp(value) {
        if (!value) return managedGroupsT('us_managed_groups_no_expiry', 'No expiry');
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return managedGroupsT('us_managed_groups_no_expiry', 'No expiry');
        return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
    }

    function temporaryRetentionLabel(entry) {
        if (entry?.deletion_scheduled_for) {
            return `${managedGroupsT('us_managed_groups_deletion_scheduled', 'Deletion scheduled')} · ${formatTimestamp(entry.deletion_scheduled_for)}`;
        }
        if (entry?.deleted_at && (entry.status === 'expired' || entry.status === 'revoked')) {
            return managedGroupsT('us_managed_groups_retained_indefinitely', 'Retained indefinitely');
        }
        return '';
    }

    /**
     * Render freshly created one-time credentials as copyable rows.
     * The container carries aria-live so assistive tech announces the batch.
     */
    function renderCreatedCredentials(created) {
        if (!dom.tempCredentials) return;
        dom.tempCredentials.innerHTML = '';
        dom.tempCredentials.hidden = !created.length;
        if (!created.length) return;

        const head = document.createElement('div');
        head.className = 'managed-groups-credentials-head';

        const iconWrapper = document.createElement('span');
        iconWrapper.className = 'managed-groups-credentials-icon';
        iconWrapper.setAttribute('aria-hidden', 'true');
        iconWrapper.innerHTML = iconMarkup('lock');

        const headText = document.createElement('div');
        headText.className = 'managed-groups-credentials-head-text';
        const heading = document.createElement('strong');
        heading.textContent = managedGroupsT('us_managed_groups_created_credentials', 'Created credentials');
        const help = document.createElement('p');
        help.textContent = managedGroupsT('us_managed_groups_credentials_help', 'Shown once. Copy and share these one-time sign-ins securely.');
        headText.appendChild(heading);
        headText.appendChild(help);

        const copyAll = createCopyButton({
            defaultLabel: managedGroupsT('us_managed_groups_copy_all', 'Copy all'),
            getText: () => created.map((entry) => `${entry.email} / ${entry.password}`).join('\n'),
        });
        copyAll.classList.add('managed-groups-credentials-copy-all');

        head.appendChild(iconWrapper);
        head.appendChild(headText);
        head.appendChild(copyAll);
        dom.tempCredentials.appendChild(head);

        created.forEach((entry) => {
            const row = document.createElement('div');
            row.className = 'managed-groups-credential-row';

            const main = document.createElement('div');
            main.className = 'managed-groups-credential-main';

            const email = document.createElement('span');
            email.className = 'managed-groups-credential-email';
            email.textContent = entry.email;

            const password = document.createElement('code');
            password.className = 'managed-groups-credential-password';
            password.textContent = entry.password;

            const expiry = document.createElement('span');
            expiry.className = 'managed-groups-credential-expiry';
            const expiryIcon = document.createElement('span');
            expiryIcon.setAttribute('aria-hidden', 'true');
            expiryIcon.style.display = 'inline-flex';
            expiryIcon.innerHTML = iconMarkup('clock');
            const expiryText = document.createElement('span');
            expiryText.textContent = formatTimestamp(entry.expires_at);
            expiry.appendChild(expiryIcon);
            expiry.appendChild(expiryText);

            main.appendChild(email);
            main.appendChild(password);
            main.appendChild(expiry);

            const copyButton = createCopyButton({
                defaultLabel: managedGroupsT('us_managed_groups_copy_credential', 'Copy'),
                getText: () => `${entry.email} / ${entry.password}`,
            });

            row.appendChild(main);
            row.appendChild(copyButton);
            dom.tempCredentials.appendChild(row);
        });
    }

    async function confirmDestructiveAction({ title, message, confirmLabel }) {
        if (typeof window.showDeleteConfirm !== 'function') return false;
        return Boolean(await window.showDeleteConfirm({
            title,
            message,
            confirmLabel,
            variant: 'warning',
        }));
    }

    async function withBusyButton(button, action) {
        if (!button || button.disabled) return;
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        try {
            return await action();
        } finally {
            const disabledByCapability = (
                (button === dom.saveSettings && !hasCapability('manage_settings'))
                || (button === dom.promoteMember && (
                    !hasCapability('promote_members')
                    || !dom.promotionUser?.value
                    || !dom.promotionRole?.value
                ))
                || (button === dom.createTemp && (
                    !hasCapability('manage_temporary_accounts')
                    || !state.detail?.settings?.temporary_accounts?.enabled
                ))
            );
            button.disabled = disabledByCapability;
            button.removeAttribute('aria-busy');
        }
    }

    function renderEntityList(container, items, renderActions) {
        if (!container) {
            return;
        }
        container.innerHTML = '';
        if (!Array.isArray(items) || !items.length) {
            const empty = document.createElement('div');
            empty.className = 'managed-groups-entity-card is-empty';
            empty.setAttribute('role', 'listitem');
            empty.textContent = managedGroupsT('us_managed_groups_entity_empty', 'Nothing here yet.');
            container.appendChild(empty);
            return;
        }
        items.forEach((item) => {
            const card = document.createElement('div');
            card.className = 'managed-groups-entity-card';
            card.setAttribute('role', 'listitem');
            // The initials avatar is rendered in CSS from data-avatar so this
            // card keeps its simple card > (title, subtitle) DOM structure.
            card.dataset.avatar = initialsFromText(item.title);
            if (item.status) {
                card.dataset.status = item.status;
            }
            const left = document.createElement('div');
            const title = document.createElement('div');
            title.textContent = item.title;
            const subtitle = document.createElement('div');
            subtitle.className = 'managed-groups-entity-subtitle';
            subtitle.textContent = item.subtitle;
            left.appendChild(title);
            left.appendChild(subtitle);
            card.appendChild(left);
            if (typeof renderActions === 'function') {
                const actions = document.createElement('div');
                actions.className = 'managed-groups-entity-actions';
                renderActions(actions, item.raw);
                // Keep read-only cards free of empty action containers.
                if (actions.children.length) {
                    card.appendChild(actions);
                }
            }
            container.appendChild(card);
        });
    }

    function renderManagers(detail) {
        renderEntityList(
            dom.managersList,
            (detail.managers || []).map((entry) => ({
                title: entry.user
                    ? (`${entry.user.first_name || ''} ${entry.user.last_name || ''}`.trim() || entry.user.email)
                    : managedGroupsT('us_managed_groups_unavailable_user', 'Unavailable user'),
                subtitle: entry.user
                    ? `${translatedRole(entry.role)} · ${translatedStatus(entry.user.status)} · ${entry.user.email || entry.user.id}`
                    : translatedRole(entry.role),
                status: entry.user?.status,
                raw: entry,
            })),
        );
    }

    function selectedPromotionCandidate() {
        return state.promotionCandidates.find((candidate) => candidate.id === dom.promotionUser?.value) || null;
    }

    /**
     * Upgrade a native select to the shared custom select widget (the same
     * single-select used across user and admin settings) exactly once.
     * The native select stays the single source of truth: the widget writes
     * back into it and fires a regular "change" event.
     */
    function ensureCustomSelect(select, {
        placeholderKey,
        placeholderFallback,
        searchable = false,
        emptyValueIsOption = false,
    } = {}) {
        if (!select) return null;
        if (select._singleSelect?.wrapper?.parentNode) return select._singleSelect;
        if (typeof window === 'undefined' || typeof window.upgradeAdminSingleSelect !== 'function') {
            return null; // Widget unavailable (e.g. unit tests) → native select fallback.
        }
        return window.upgradeAdminSingleSelect(select, {
            key: select.id,
            placeholder: managedGroupsT(placeholderKey, placeholderFallback),
            emptyValueIsOption,
            ...(searchable
                ? {
                    searchable: true,
                    search: {
                        enabled: true,
                        placeholder: managedGroupsT('us_managed_groups_search_members', 'Search members'),
                        noResultsMessage: managedGroupsT('us_managed_groups_search_members_empty', 'No members found'),
                        // Match the admin notification selects: never pop the
                        // virtual keyboard on mobile when the menu opens.
                        disableMobileAutoFocus: true,
                    },
                }
                : {}),
        });
    }

    /**
     * Re-sync the widget after programmatic option/value/disabled mutations.
     * The widget has no disabled handling of its own, so mirror the native
     * select's disabled flag onto the generated trigger button.
     */
    function syncCustomSelect(select, meta) {
        if (!select || !meta) return;
        meta.refreshOptions?.();
        meta.syncFromSelect?.();
        const trigger = meta.wrapper?.querySelector?.('.admin-select-trigger');
        if (trigger) {
            trigger.disabled = Boolean(select.disabled);
        }
        meta.wrapper?.classList?.toggle('is-disabled', Boolean(select.disabled));
    }

    function renderPromotionRoles() {
        if (!dom.promotionRole) return;
        const roleMeta = ensureCustomSelect(dom.promotionRole, {
            placeholderKey: 'us_managed_groups_select_higher_role',
            placeholderFallback: 'Select higher role',
        });
        const selectedCandidate = selectedPromotionCandidate();
        const currentPriority = ROLE_PRIORITY[selectedCandidate?.current_role || 'member'];
        dom.promotionRole.innerHTML = '';
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = managedGroupsT('us_managed_groups_select_higher_role', 'Select higher role');
        dom.promotionRole.appendChild(placeholder);
        for (const role of ['coordinator', 'manager', 'owner']) {
            if (!selectedCandidate?.eligible || ROLE_PRIORITY[role] <= currentPriority) continue;
            const option = document.createElement('option');
            option.value = role;
            option.textContent = translatedRole(role);
            dom.promotionRole.appendChild(option);
        }
        dom.promotionRole.value = '';
        dom.promotionRole.disabled = !selectedCandidate?.eligible;
        syncCustomSelect(dom.promotionRole, roleMeta);
        if (dom.promoteMember) dom.promoteMember.disabled = true;
    }

    function renderPromotionCandidates() {
        if (!dom.promotionUser) return;
        const userMeta = ensureCustomSelect(dom.promotionUser, {
            placeholderKey: 'us_managed_groups_select_member',
            placeholderFallback: 'Select member',
            searchable: true,
        });
        const previousValue = dom.promotionUser.value;
        dom.promotionUser.innerHTML = '';
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = managedGroupsT('us_managed_groups_select_member', 'Select member');
        dom.promotionUser.appendChild(placeholder);
        state.promotionCandidates.forEach((candidate) => {
            const option = document.createElement('option');
            const fullName = `${candidate.first_name || ''} ${candidate.last_name || ''}`.trim();
            const role = translatedRole(candidate.current_role || 'member');
            option.value = candidate.id;
            option.disabled = !candidate.eligible;
            option.textContent = `${fullName || candidate.email} · ${candidate.email} · ${role} · ${translatedStatus(candidate.status)}`;
            dom.promotionUser.appendChild(option);
        });
        const previousCandidate = state.promotionCandidates.find((candidate) => (
            candidate.id === previousValue && candidate.eligible
        ));
        dom.promotionUser.value = previousCandidate?.id || '';
        dom.promotionUser.disabled = !hasCapability('promote_members') || !state.promotionCandidates.length;
        syncCustomSelect(dom.promotionUser, userMeta);
        renderPromotionRoles();
    }

    async function loadPromotionCandidates(groupId, requestId) {
        const candidates = [];
        let offset = 0;
        let hasMore = true;
        while (hasMore) {
            const params = new URLSearchParams({ offset: String(offset), limit: '500' });
            const page = await apiJson(`/api/v1/group-management/groups/${encodeURIComponent(groupId)}/manager-candidates?${params}`);
            if (requestId !== state.detailRequestId || state.selectedGroupId !== groupId) return;
            candidates.push(...(page?.items || []));
            hasMore = Boolean(page?.has_more);
            offset = Number(page?.offset || offset) + Number(page?.limit || 500);
        }
        state.promotionCandidates = candidates;
        renderPromotionCandidates();
    }

    function renderMembers(detail) {
        // Membership is intentionally read-only here. Only verified admins
        // may reassign a user's group through the admin user profile surface.
        renderEntityList(
            dom.membersList,
            (detail.members || []).map((entry) => ({
                title: `${entry.first_name || ''} ${entry.last_name || ''}`.trim() || entry.email,
                subtitle: `${translatedStatus(entry.status)} · ${entry.email || entry.id}`,
                status: entry.status,
                raw: entry,
            })),
        );
    }

    function renderTemporary(detail) {
        renderEntityList(
            dom.temporaryList,
            (detail.temporary_accounts || []).map((entry) => {
                const subtitleParts = [
                    translatedStatus(entry.status),
                    formatTimestamp(entry.temporary_expires_at),
                    temporaryRetentionLabel(entry),
                ].filter(Boolean);
                return {
                    title: entry.email || entry.first_name || entry.id,
                    subtitle: subtitleParts.join(' · '),
                    status: entry.status,
                    raw: entry,
                };
            }),
            (actions, entry) => {
                if (entry.status === 'revoked' || !hasCapability('manage_temporary_accounts', detail)) {
                    return;
                }
                const revoke = document.createElement('button');
                revoke.type = 'button';
                revoke.className = 'om-button border danger-nofill';
                revoke.textContent = managedGroupsT('us_managed_groups_action_revoke', 'Revoke');
                revoke.addEventListener('click', async () => {
                    const groupId = state.selectedGroupId;
                    const confirmed = await confirmDestructiveAction({
                        title: managedGroupsT('us_managed_groups_confirm_revoke_title', 'Revoke temporary account?'),
                        message: managedGroupsT('us_managed_groups_confirm_revoke_message', 'The account will be signed out and cannot be used again.'),
                        confirmLabel: managedGroupsT('us_managed_groups_action_revoke', 'Revoke'),
                    });
                    if (!confirmed || !groupId) return;
                    await withBusyButton(revoke, async () => {
                        try {
                            await apiJson(`/api/v1/group-management/temporary-accounts/${encodeURIComponent(entry.id)}`, { method: 'DELETE' });
                            window.notifySuccess?.(managedGroupsT('us_managed_groups_temp_revoked', 'Temporary account revoked.'));
                            if (state.selectedGroupId === groupId) await loadGroupDetail(groupId, false);
                        } catch (error) {
                            window.notifyError?.(error.message);
                        }
                    });
                });
                actions.appendChild(revoke);
            }
        );
    }

    function renderDetail(detail) {
        state.detail = detail;
        dom.empty.style.display = 'none';
        dom.detail.hidden = false;
        dom.title.textContent = detail.group.name || managedGroupsT('us_managed_groups_group_fallback', 'Group');
        dom.path.textContent = (detail.group.path || []).join(' / ');
        renderMeta(detail);
        renderTabCounts(detail);
        renderSettings(detail);
        renderManagers(detail);
        renderMembers(detail);
        renderTemporary(detail);
        if (dom.managersMore) dom.managersMore.hidden = !detail.pagination?.managers?.has_more;
        if (dom.membersMore) dom.membersMore.hidden = !detail.pagination?.members?.has_more;
        if (dom.temporaryMore) dom.temporaryMore.hidden = !detail.pagination?.temporary_accounts?.has_more;
    }

    async function loadMore(collection, button) {
        const groupId = state.selectedGroupId;
        const page = state.detail?.pagination?.[collection];
        if (!groupId || !page?.has_more) return;
        const offsetName = {
            managers: 'manager_offset',
            members: 'member_offset',
            temporary_accounts: 'temporary_offset',
        }[collection];
        await withBusyButton(button, async () => {
            try {
                const params = new URLSearchParams({
                    [offsetName]: String(page.offset + page.limit),
                    limit: String(page.limit),
                });
                const next = await apiJson(`/api/v1/group-management/groups/${encodeURIComponent(groupId)}?${params}`);
                if (state.selectedGroupId !== groupId) return;
                const merged = {
                    ...state.detail,
                    [collection]: [...(state.detail?.[collection] || []), ...(next?.[collection] || [])],
                    pagination: {
                        ...(state.detail?.pagination || {}),
                        [collection]: next?.pagination?.[collection],
                    },
                };
                renderDetail(merged);
            } catch (error) {
                window.notifyError?.(error.message);
            }
        });
    }

    async function loadGroupDetail(groupId, rerenderList = true) {
        const requestId = ++state.detailRequestId;
        const changedGroup = state.selectedGroupId !== groupId;
        if (changedGroup && dom.tempCredentials) {
            // One-time passwords must never remain visible after navigating to
            // another group.
            dom.tempCredentials.hidden = true;
            dom.tempCredentials.innerHTML = '';
        }
        if (changedGroup) {
            state.promotionCandidates = [];
            renderPromotionCandidates();
        }
        state.selectedGroupId = groupId;
        dom.detail?.setAttribute('aria-busy', 'true');
        if (rerenderList) {
            renderGroupList();
        }
        try {
            const detail = await apiJson(`/api/v1/group-management/groups/${encodeURIComponent(groupId)}`);
            if (requestId !== state.detailRequestId || state.selectedGroupId !== groupId) return;
            renderDetail(detail);
            if (hasCapability('promote_members', detail)) {
                try {
                    await loadPromotionCandidates(groupId, requestId);
                } catch (error) {
                    // Promotion is an auxiliary owner-only action. A failed
                    // candidate page must not discard the already loaded group
                    // settings, roster, or temporary-account controls.
                    if (requestId !== state.detailRequestId || state.selectedGroupId !== groupId) return;
                    state.promotionCandidates = [];
                    renderPromotionCandidates();
                    window.notifyError?.(
                        managedGroupsT('us_managed_groups_request_failed', 'Request failed'),
                        error,
                    );
                }
            }
        } catch (error) {
            if (requestId !== state.detailRequestId || state.selectedGroupId !== groupId) return;
            window.notifyError?.(managedGroupsT('us_managed_groups_load_detail_failed', 'Failed to load group details'), error);
            state.selectedGroupId = null;
            if (dom.detail) dom.detail.hidden = true;
            if (dom.empty) dom.empty.style.display = '';
            if (rerenderList) {
                renderGroupList();
            }
        } finally {
            if (requestId === state.detailRequestId) dom.detail?.removeAttribute('aria-busy');
        }
    }

    async function load() {
        if (!state.visible) {
            return;
        }
        try {
            const payload = await apiJson('/api/v1/group-management/groups');
            state.groups = payload.groups || [];
            state.filtered = [...state.groups];
            renderGroupList();
            if (state.selectedGroupId && state.groups.some((group) => group.id === state.selectedGroupId)) {
                await loadGroupDetail(state.selectedGroupId, false);
            } else {
                // Never auto-select a group: the sidebar selection is an
                // explicit user action, so the empty state is the honest
                // initial surface and the active list item always matches
                // the open detail view.
                state.selectedGroupId = null;
                state.detail = null;
                state.promotionCandidates = [];
                renderPromotionCandidates();
                if (dom.detail) dom.detail.hidden = true;
                if (dom.empty) dom.empty.style.display = '';
                renderGroupList();
            }
        } catch (error) {
            window.notifyError?.(managedGroupsT('us_managed_groups_load_failed', 'Failed to load groups'), error);
            state.groups = [];
            state.filtered = [];
            state.selectedGroupId = null;
            state.detail = null;
            if (dom.detail) dom.detail.hidden = true;
            if (dom.empty) dom.empty.style.display = '';
            renderGroupList();
        }
    }

    function currentSettingsPayload() {
        const settings = {
            context: {
                enable_group_context: Boolean(dom.contextEnabled?.checked),
                group_context: dom.context?.value?.trim() || '',
            },
            chat: {
                allow_temporary_chat: Boolean(dom.allowTemporaryChat?.checked),
            },
            files: {
                allow_file_uploads: Boolean(dom.allowFileUploads?.checked),
                max_files_upload_count: Number(dom.maxFiles?.value || 0),
                max_user_files_size_gb: Number(dom.maxFileSize?.value || 0),
            },
            temporary_accounts: {
                enabled: Boolean(dom.tempEnabled?.checked),
                max_active_accounts: Number(dom.tempMaxActive?.value || 0),
                credential_length: Number(dom.tempCredentialLength?.value || 0),
            },
        };
        FEATURE_SETTING_CONTROLS.forEach(({ control, page, key }) => {
            settings[page] ||= {};
            settings[page][key] = Boolean(control?.checked);
        });
        settings.memories ||= {};
        settings.memories.memory_model_id = String(dom.memoryModel?.value || '').trim();
        return { settings };
    }

    function bindOnce() {
        if (dom.search) {
            dom.search.addEventListener('input', filterGroups);
        }
        dom.tabs.forEach((tab) => {
            tab.addEventListener('click', () => activateTab(tab.dataset.managedTab));
            tab.addEventListener('keydown', (event) => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                const currentIndex = dom.tabs.indexOf(tab);
                let nextIndex = currentIndex;
                if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + dom.tabs.length) % dom.tabs.length;
                if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % dom.tabs.length;
                if (event.key === 'Home') nextIndex = 0;
                if (event.key === 'End') nextIndex = dom.tabs.length - 1;
                const nextTab = dom.tabs[nextIndex];
                activateTab(nextTab.dataset.managedTab);
                nextTab.focus();
            });
        });
        FEATURE_SETTING_CONTROLS.forEach(({ control }) => {
            control?.addEventListener('change', () => syncFeatureControlAvailability());
        });
        dom.contextEnabled?.addEventListener('change', syncContextFieldVisibility);
        dom.saveSettings?.addEventListener('click', async () => {
            const groupId = state.selectedGroupId;
            if (!groupId || !hasCapability('manage_settings')) return;
            await withBusyButton(dom.saveSettings, async () => {
                try {
                    await apiJson(`/api/v1/group-management/groups/${encodeURIComponent(groupId)}/settings`, {
                        method: 'PUT',
                        body: currentSettingsPayload(),
                    });
                    window.notifySuccess?.(managedGroupsT('us_managed_groups_settings_saved', 'Group settings saved.'));
                    if (state.selectedGroupId === groupId) await loadGroupDetail(groupId, false);
                } catch (error) {
                    window.notifyError?.(error.message);
                }
            });
        });
        dom.promotionUser?.addEventListener('change', renderPromotionRoles);
        dom.promotionRole?.addEventListener('change', () => {
            if (dom.promoteMember) {
                dom.promoteMember.disabled = !dom.promotionUser?.value || !dom.promotionRole?.value;
            }
        });
        dom.promoteMember?.addEventListener('click', async () => {
            const groupId = state.selectedGroupId;
            if (!groupId || !hasCapability('promote_members')) return;
            await withBusyButton(dom.promoteMember, async () => {
                try {
                    await apiJson(`/api/v1/group-management/groups/${encodeURIComponent(groupId)}/manager-promotions`, {
                        method: 'POST',
                        body: {
                            user_id: dom.promotionUser?.value,
                            role: dom.promotionRole?.value,
                        },
                    });
                    window.notifySuccess?.(managedGroupsT('us_managed_groups_member_promoted', 'Member promoted.'));
                    if (state.selectedGroupId === groupId) await loadGroupDetail(groupId, false);
                } catch (error) {
                    window.notifyError?.(error.message);
                }
            });
        });
        dom.createTemp?.addEventListener('click', async () => {
            const groupId = state.selectedGroupId;
            if (!groupId || !hasCapability('manage_temporary_accounts') || !state.detail?.settings?.temporary_accounts?.enabled) return;
            await withBusyButton(dom.createTemp, async () => {
              try {
                const result = await apiJson(`/api/v1/group-management/groups/${encodeURIComponent(groupId)}/temporary-accounts`, {
                    method: 'POST',
                    body: {
                        count: Number(dom.tempCreateCount?.value || 0),
                        expiry_hours: Number(dom.tempCreateExpiryHours?.value || 0),
                    },
                });
                renderCreatedCredentials(result.created || []);
                window.notifySuccess?.(managedGroupsT('us_managed_groups_temp_created', 'Temporary accounts created.'));
                if (state.selectedGroupId === groupId) await loadGroupDetail(groupId, false);
              } catch (error) {
                window.notifyError?.(error.message);
              }
            });
        });
        dom.managersMore?.addEventListener('click', () => loadMore('managers', dom.managersMore));
        dom.membersMore?.addEventListener('click', () => loadMore('members', dom.membersMore));
        dom.temporaryMore?.addEventListener('click', () => loadMore('temporary_accounts', dom.temporaryMore));
    }

    bindOnce();
    activateTab('settings');

    window.ManagedGroupsSettings = {
        setVisibility,
        load,
    };
})();
