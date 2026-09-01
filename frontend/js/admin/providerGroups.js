(function () {
    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
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

    const dom = {
        listPage: document.getElementById('page-provider-groups'),
        formPage: document.getElementById('page-provider-groups-form'),
        list: document.getElementById('providerGroupList'),
        searchInput: document.getElementById('providerGroupSearchInput'),
        searchClear: document.getElementById('providerGroupSearchClear'),
        createButton: document.getElementById('providerGroupCreateButton'),
        form: document.getElementById('providerGroupForm'),
        formTitle: document.getElementById('providerGroupFormTitle'),
        formSubtitle: document.getElementById('providerGroupFormSubtitle'),
        nameInput: document.getElementById('providerGroupNameInput'),
        iconInput: document.getElementById('providerGroupIconInput'),
        iconPickerContainer: document.getElementById('providerGroupIconPickerContainer'),
        membersContainer: document.getElementById('providerGroupMembers'),
        membersEmpty: document.getElementById('providerGroupMembersEmpty'),
        addMemberBtn: document.getElementById('providerGroupAddMemberBtn'),
        providerTypeIndicator: document.getElementById('providerGroupTypeIndicator'),
        providerTypeIndicatorValue: document.getElementById('providerGroupTypeIndicatorValue'),
        formBack: document.getElementById('providerGroupFormBack'),
        formSubmit: document.getElementById('providerGroupFormSubmit'),
        deleteOverlay: document.getElementById('deleteProviderGroupOverlay'),
        deleteMessage: document.getElementById('deleteProviderGroupMessage'),
        deleteCancelBtn: document.getElementById('deleteProviderGroupCancelButton'),
        deleteConfirmBtn: document.getElementById('deleteProviderGroupConfirmButton'),
        deleteConfirmText: document.getElementById('deleteProviderGroupConfirmText'),
    };

    if (!dom.listPage || !dom.formPage) {
        return;
    }

    const state = {
        groups: [],
        providers: [],
        editingGroupId: null,
        members: [],
        loading: false,
        pendingDeleteGroup: null,
        lockedProviderType: null,
        initialSnapshot: null,
    };
    const UNSAVED_GUARD_ID = 'admin-provider-groups-form-unsaved';
    let unsavedGuardRegistered = false;

    const providerGroupSaveErrorMessage = (detail) => {
        if (detail === 'provider_group_provider_not_model_capable') {
            return t(
                'provider_group_provider_not_model_capable',
                'Only chat-model providers can be added to provider groups.'
            );
        }
        return detail || t('provider_group_save_failed', 'Failed to save provider group');
    };

    const api = {
        async fetchGroups() {
            const res = await window.authedFetch('/api/v1/llm/provider-groups');
            if (!res.ok) throw new Error(t('provider_groups_fetch_failed', 'Failed to fetch provider groups'));
            return res.json();
        },
        async fetchGroup(groupId) {
            const res = await window.authedFetch(`/api/v1/llm/provider-group?group_id=${encodeURIComponent(groupId)}`);
            if (!res.ok) throw new Error(t('provider_group_fetch_failed', 'Failed to load provider group'));
            return res.json();
        },
        async fetchProviders() {
            const res = await window.authedFetch('/api/v1/llm/providers?model_capable_only=true');
            if (!res.ok) throw new Error(t('providers_fetch_failed', 'Failed to fetch providers'));
            return res.json();
        },
        async createGroup(payload) {
            const res = await window.authedFetch('/api/v1/llm/provider-group', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(providerGroupSaveErrorMessage(data.detail));
            }
            return res.json();
        },
        async updateGroup(groupId, payload) {
            const res = await window.authedFetch(`/api/v1/llm/provider-group?group_id=${encodeURIComponent(groupId)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(providerGroupSaveErrorMessage(data.detail));
            }
            return res.json();
        },
        async deleteGroup(groupId) {
            const res = await window.authedFetch(`/api/v1/llm/provider-group?group_id=${encodeURIComponent(groupId)}`, {
                method: 'DELETE',
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || t('provider_group_delete_failed', 'Failed to delete provider group'));
            }
            return res.json();
        },
    };

    const renderCollisionSafeIcon = (iconValue, fallback) => {
        if (window.IconPicker?.renderIconMarkup) {
            return window.IconPicker.renderIconMarkup(iconValue, {
                fallback,
                imageAlt: t('providers_icon_alt', 'Provider icon'),
            });
        }
        return fallback;
    };

    const getIconHtml = (iconKey) => {
        const iconsMap = (typeof Icons !== 'undefined' && Icons) || (window?.Icons) || {};
        const fallbackKey = iconsMap.layout ? 'layout' : (iconsMap.layers ? 'layers' : 'omlorix');
        const fallback = iconsMap[fallbackKey] || '';
        const configuredIcon = typeof iconKey === 'string' ? iconKey.trim() : '';
        // Provider groups use the same picker as providers and models. Preserve
        // image values here instead of replacing them with the fallback after
        // they have already been accepted and saved by the form.
        const isConfiguredImage = Boolean(window.IconPicker?.isImageIconValue?.(configuredIcon));
        const iconValue = configuredIcon && (configuredIcon.startsWith('<') || iconsMap[configuredIcon] || isConfiguredImage)
            ? configuredIcon
            : fallbackKey;
        return renderCollisionSafeIcon(iconValue, fallback);
    };

    const getProviderIconHtml = (providerKey) => {
        const iconsMap = (typeof Icons !== 'undefined' && Icons) || (window?.Icons) || {};
        let key = String(providerKey || '').trim().toLowerCase();
        if (key === 'openai_responses' || key === 'openai_chat_completions') {
            key = 'openai';
        } else if (key === 'microsoft_azure') {
            key = 'microsoft';
        } else if (key === 'anthropic_base') {
            key = 'anthropic';
        }
        const fallbackKey = iconsMap.server ? 'server' : 'omlorix';
        const fallback = iconsMap[fallbackKey] || '';
        return renderCollisionSafeIcon(iconsMap[key] ? key : fallbackKey, fallback);
    };

    const formatProviderType = (providerKey = '') => {
        if (!providerKey) return '';
        if (typeof window.formatProviderLabel === 'function') {
            return window.formatProviderLabel(providerKey);
        }
        return providerKey
            .split(/[_\-]/)
            .filter(Boolean)
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
            .join(' ');
    };

    const findProviderById = (providerId) => state.providers.find((provider) => provider.id === providerId);

    const setLockedProviderType = (providerType) => {
        state.lockedProviderType = providerType || null;
        if (!dom.providerTypeIndicator || !dom.providerTypeIndicatorValue) {
            return;
        }
        if (!state.lockedProviderType) {
            dom.providerTypeIndicator.hidden = true;
            dom.providerTypeIndicatorValue.textContent = '';
            return;
        }

        dom.providerTypeIndicator.hidden = false;
        dom.providerTypeIndicatorValue.textContent = formatProviderType(state.lockedProviderType);
    };

    const syncLockedProviderType = () => {
        if (!state.members.length) {
            setLockedProviderType(null);
            return;
        }

        const firstMember = state.members[0];
        const providerType = firstMember.provider || findProviderById(firstMember.provider_id)?.provider || null;
        setLockedProviderType(providerType);
    };

    const renderGroupList = (groups) => {
        if (!dom.list) return;

        if (!groups.length) {
            dom.list.innerHTML = `
                <div class="provider-group-empty">
                    <div class="provider-group-empty-icon">
                        ${Icons.layout}
                    </div>
                    <p class="provider-group-empty-title">${t('provider_groups_empty_title', 'No provider groups yet')}</p>
                    <p class="provider-group-empty-text">${t('provider_groups_empty_text', 'Create a group to load balance requests across multiple providers.')}</p>
                </div>
            `;
            return;
        }

        const searchTerm = (dom.searchInput?.value || '').toLowerCase().trim();
        const filtered = searchTerm
            ? groups.filter(g => g.name.toLowerCase().includes(searchTerm))
            : groups;

        if (!filtered.length) {
            dom.list.innerHTML = `
                <div class="provider-group-empty">
                    <div class="provider-group-empty-icon">
                        ${Icons.magnifyingGlass}
                    </div>
                    <p class="provider-group-empty-title">${t('provider_groups_search_empty_title', 'No results found')}</p>
                    <p class="provider-group-empty-text">${t('provider_groups_search_empty_text', 'No groups match your search. Try a different query.')}</p>
                </div>
            `;
            return;
        }

        const fragment = document.createDocumentFragment();
        filtered.forEach((group) => {
            const card = document.createElement('div');
            card.className = 'provider-group-card';
            card.dataset.groupId = group.id;

            const iconHtml = getIconHtml(group.icon);

            card.innerHTML = `
                <div class="provider-group-card-icon">${iconHtml}</div>
                <div class="provider-group-card-content">
                    <div class="provider-group-card-name">${escapeHtml(group.name)}</div>
                    <div class="provider-group-card-meta">${formatT(group.member_count === 1 ? 'provider_group_member_count_single' : 'provider_group_member_count_plural', `${group.member_count} provider${group.member_count !== 1 ? 's' : ''}`, { count: group.member_count })}</div>
                </div>
                <div class="provider-group-card-actions">
                    <button type="button" class="provider-group-card-btn edit" title="${t('provider_group_edit_title', 'Edit group')}" aria-label="${escapeHtml(formatT('provider_group_edit_aria', 'Edit {name}', { name: group.name }))}">
                        ${Icons.create}
                    </button>
                    <button type="button" class="provider-group-card-btn delete" title="${t('provider_group_delete_title', 'Delete group')}" aria-label="${escapeHtml(formatT('provider_group_delete_aria', 'Delete {name}', { name: group.name }))}">
                        ${Icons?.trash || ''}
                    </button>
                </div>
            `;

            card.querySelector('.edit').addEventListener('click', (e) => {
                e.stopPropagation();
                openEditForm(group.id);
            });

            card.querySelector('.delete').addEventListener('click', (e) => {
                e.stopPropagation();
                confirmDeleteGroup(group);
            });

            card.addEventListener('click', () => openEditForm(group.id));
            fragment.appendChild(card);
        });

        dom.list.innerHTML = '';
        dom.list.appendChild(fragment);
    };

    const escapeHtml = (str) => {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    };

    const loadGroups = async () => {
        if (state.loading) return;
        state.loading = true;

        if (dom.list) {
            dom.list.innerHTML = '';
            dom.list.appendChild(window.createAdminLoadingPlaceholder({
                message: t('provider_groups_loading', 'Loading provider groups…'),
                className: '',
            }));
        }

        try {
            state.groups = await api.fetchGroups();
            renderGroupList(state.groups);
        } catch (error) {
            if (dom.list) {
                dom.list.innerHTML = `
                    <div class="provider-group-empty">
                        <div class="provider-group-empty-icon">
                            ${Icons.info}
                        </div>
                        <p class="provider-group-empty-title">${t('provider_groups_load_failed_title', 'Failed to load provider groups')}</p>
                        <p class="provider-group-empty-text">${t('provider_groups_load_failed_text', 'Please try refreshing the page.')}</p>
                    </div>
                `;
            }
            if (typeof notifyError === 'function') {
                notifyError(error.message || t('provider_groups_fetch_failed', 'Failed to load provider groups'));
            }
        } finally {
            state.loading = false;
        }
    };

    const loadProviders = async () => {
        try {
            state.providers = await api.fetchProviders();
        } catch (error) {
            state.providers = [];
            if (typeof notifyError === 'function') {
                notifyError(t('providers_fetch_failed', 'Failed to load providers'));
            }
        }
    };

    const openCreateForm = () => {
        state.editingGroupId = null;
        state.members = [];
        setLockedProviderType(null);

        if (dom.formTitle) dom.formTitle.textContent = t('page_create_provider_group', 'Create Provider Group');
        if (dom.formSubtitle) dom.formSubtitle.textContent = t('page_create_provider_group_subtitle', 'Configure a group of providers for load balancing. Requests will be distributed based on weights.');
        if (dom.nameInput) dom.nameInput.value = '';
        if (dom.iconInput) dom.iconInput.value = '';
        if (iconPickerInstance) {
            iconPickerInstance.setValue('');
        }
        if (dom.formSubmit) {
            const span = dom.formSubmit.querySelector('span');
            if (span) span.textContent = t('provider_group_create_btn', 'Create Group');
        }

        renderMembersList();
        rememberFormSnapshot();
        showFormPage();
    };

    const openEditForm = async (groupId) => {
        state.editingGroupId = groupId;

        if (dom.formTitle) dom.formTitle.textContent = t('page_edit_provider_group', 'Edit Provider Group');
        if (dom.formSubtitle) dom.formSubtitle.textContent = t('page_edit_provider_group_subtitle', 'Update the group configuration and member providers.');
        if (dom.formSubmit) {
            const span = dom.formSubmit.querySelector('span');
            if (span) span.textContent = t('btn_save_changes', 'Save Changes');
        }

        try {
            const group = await api.fetchGroup(groupId);
            if (dom.nameInput) dom.nameInput.value = group.name || '';
            if (dom.iconInput) dom.iconInput.value = group.icon || '';
            if (iconPickerInstance) {
                iconPickerInstance.setValue(group.icon || '');
            }

            state.members = (group.members || []).map(m => ({
                provider_id: m.provider_id,
                weight: m.weight || 1,
                name: m.name || '',
                provider: m.provider || '',
            }));

            renderMembersList();
            rememberFormSnapshot();
            showFormPage();
        } catch (error) {
            if (typeof notifyError === 'function') {
                notifyError(error.message || t('provider_group_fetch_failed', 'Failed to load provider group'));
            }
        }
    };

    const showFormPage = () => {
        if (typeof window.activateAdminPage === 'function') {
            window.activateAdminPage('provider-groups-form', { history: 'none' });
        }
    };

    const showListPage = () => {
        if (typeof window.activateAdminPage === 'function') {
            window.activateAdminPage('provider-groups');
        }
    };

    const renderMembersList = () => {
        if (!dom.membersContainer) return;

        syncLockedProviderType();

        if (!state.members.length) {
            dom.membersContainer.innerHTML = `
                <div class="provider-group-members-empty">
                    <div class="provider-group-members-empty-icon">
                        ${Icons.server}
                    </div>
                    <p class="provider-group-members-empty-title">${t('provider_group_empty_title', 'No providers added')}</p>
                    <p class="provider-group-members-empty-text">${t('provider_group_empty_text', 'Click the button below to add providers to this group.')}</p>
                </div>
            `;
            return;
        }

        const fragment = document.createDocumentFragment();
        state.members.forEach((member, index) => {
            const row = document.createElement('div');
            row.className = 'provider-group-member-row';
            row.dataset.index = index;

            const providerMeta = findProviderById(member.provider_id);
            const providerName = member.name || providerMeta?.name || member.provider_id;
            const providerType = member.provider || providerMeta?.provider || '';
            const providerIcon = getProviderIconHtml(providerType);

            row.innerHTML = `
                <div class="provider-group-member-icon">${providerIcon}</div>
                <div class="provider-group-member-info">
                    <span class="provider-group-member-name">${escapeHtml(providerName)}</span>
                    <span class="provider-group-member-type">${formatProviderType(providerType)}</span>
                </div>
                <div class="provider-group-member-weight">
                    <label class="sr-only" for="member-weight-${index}">${escapeHtml(formatT('provider_group_weight_for', 'Weight for {name}', { name: providerName }))}</label>
                    <input type="number" id="member-weight-${index}" class="provider-group-weight-input" value="${member.weight}" min="1" max="100">
                    <span class="provider-group-weight-label">${t('provider_group_weight_label', 'weight')}</span>
                </div>
                <button type="button" class="provider-group-member-remove" title="${t('provider_group_remove_provider_title', 'Remove provider')}" aria-label="${escapeHtml(formatT('provider_group_remove_provider_aria', 'Remove {name}', { name: providerName }))}">
                    ${Icons.close}
                </button>
            `;

            const weightInput = row.querySelector('.provider-group-weight-input');
            weightInput.addEventListener('change', (e) => {
                const newWeight = parseInt(e.target.value, 10);
                state.members[index].weight = Math.max(1, Math.min(100, newWeight || 1));
                e.target.value = state.members[index].weight;
            });

            row.querySelector('.provider-group-member-remove').addEventListener('click', () => {
                state.members.splice(index, 1);
                renderMembersList();
            });

            fragment.appendChild(row);
        });

        dom.membersContainer.innerHTML = '';
        dom.membersContainer.appendChild(fragment);
    };

    const showAddMemberDropdown = () => {
        const existingDropdown = document.querySelector('.provider-group-add-dropdown');
        if (existingDropdown) {
            existingDropdown.remove();
            return;
        }

        if (!state.providers.length) {
            if (typeof notifyInfo === 'function') {
                notifyInfo(t('loading_providers', 'Loading providers…'));
            }
            loadProviders().then(() => {
                if (state.providers.length) {
                    showAddMemberDropdown();
                } else if (typeof notifyError === 'function') {
                    notifyError(t('provider_group_no_providers_available', 'No providers available. Create a provider first.'));
                }
            });
            return;
        }

        const lockedType = state.lockedProviderType;
        const availableProviders = state.providers.filter((provider) => {
            const alreadyAdded = state.members.some((member) => member.provider_id === provider.id);
            if (alreadyAdded) {
                return false;
            }
            if (lockedType && provider.provider !== lockedType) {
                return false;
            }
            return true;
        });

        if (!availableProviders.length) {
            if (typeof notifyWarning === 'function') {
                const message = lockedType
                    ? formatT('provider_group_no_additional_locked_type', `No additional ${formatProviderType(lockedType)} providers are available for this group.`, { type: formatProviderType(lockedType) })
                    : t('provider_group_all_providers_already_added', 'All available providers are already in this group');
                notifyWarning(message);
            }
            return;
        }

        const dropdown = document.createElement('div');
        dropdown.className = 'provider-group-add-dropdown';

        availableProviders.forEach((provider) => {
            const item = document.createElement('button');
            item.type = 'button';
            item.className = 'provider-group-add-dropdown-item';
            const providerIcon = getProviderIconHtml(provider.provider);
            item.innerHTML = `
                <div class="provider-group-add-dropdown-icon">${providerIcon}</div>
                <div class="provider-group-add-dropdown-content">
                    <span class="provider-group-add-dropdown-name">${escapeHtml(provider.name || provider.provider)}</span>
                    <span class="provider-group-add-dropdown-type">${formatProviderType(provider.provider)}</span>
                </div>
            `;
            item.addEventListener('click', () => {
                if (state.lockedProviderType && provider.provider !== state.lockedProviderType) {
                    if (typeof notifyError === 'function') {
                        notifyError(formatT('provider_group_locked_type_error', `This group is locked to ${formatProviderType(state.lockedProviderType)} providers.`, { type: formatProviderType(state.lockedProviderType) }));
                    }
                    dropdown.remove();
                    return;
                }

                state.members.push({
                    provider_id: provider.id,
                    weight: 1,
                    name: provider.name,
                    provider: provider.provider,
                });
                renderMembersList();
                dropdown.remove();
            });
            dropdown.appendChild(item);
        });

        const wrapper = dom.addMemberBtn?.parentElement;
        if (wrapper) {
            wrapper.appendChild(dropdown);
        }

        const closeDropdown = (e) => {
            if (!dropdown.contains(e.target) && e.target !== dom.addMemberBtn) {
                dropdown.remove();
                document.removeEventListener('click', closeDropdown);
            }
        };
        setTimeout(() => document.addEventListener('click', closeDropdown), 0);
    };

    const handleFormSubmit = async (e) => {
        e.preventDefault();

        const name = (dom.nameInput?.value || '').trim();
        const icon = (dom.iconInput?.value || '').trim() || null;

        if (!name) {
            if (typeof notifyError === 'function') {
                notifyError(t('provider_group_name_required', 'Please enter a group name'));
            }
            dom.nameInput?.focus();
            return;
        }

        if (state.members.length < 2) {
            if (typeof notifyError === 'function') {
                notifyError(t('provider_group_min_members', 'Please add at least 2 providers to the group'));
            }
            return;
        }

        const payload = {
            name,
            icon,
            members: state.members.map(m => ({
                provider_id: m.provider_id,
                weight: m.weight,
            })),
        };

        if (dom.formSubmit) dom.formSubmit.disabled = true;
        const submitSpan = dom.formSubmit?.querySelector('span');
        const originalText = submitSpan?.textContent || '';
        if (submitSpan) submitSpan.textContent = t('admin_saving', 'Saving…');

        try {
            if (state.editingGroupId) {
                await api.updateGroup(state.editingGroupId, payload);
                if (typeof notifySuccess === 'function') {
                    notifySuccess(t('provider_group_update_success', 'Provider group updated successfully'));
                }
            } else {
                await api.createGroup(payload);
                if (typeof notifySuccess === 'function') {
                    notifySuccess(t('provider_group_create_success', 'Provider group created successfully'));
                }
            }
            showListPage();
            loadGroups();
        } catch (error) {
            if (typeof notifyError === 'function') {
                notifyError(error.message || t('provider_group_save_failed', 'Failed to save provider group'));
            }
        } finally {
            if (dom.formSubmit) dom.formSubmit.disabled = false;
            if (submitSpan) submitSpan.textContent = originalText;
        }
    };

    const openDeleteModal = (group) => {
        state.pendingDeleteGroup = group;
        if (dom.deleteMessage) {
            dom.deleteMessage.textContent = formatT('provider_group_delete_confirm', `Are you sure you want to delete the provider group "${group.name}"? Models using this group will need to be reassigned.`, { name: group.name });
        }
        if (dom.deleteOverlay) {
            dom.deleteOverlay.hidden = false;
        }
    };

    const closeDeleteModal = () => {
        state.pendingDeleteGroup = null;
        if (dom.deleteOverlay) {
            dom.deleteOverlay.hidden = true;
        }
        if (dom.deleteConfirmBtn) {
            dom.deleteConfirmBtn.disabled = false;
        }
        if (dom.deleteConfirmText) {
            dom.deleteConfirmText.textContent = t('modal_delete_group_btn', 'Delete Group');
        }
    };

    const confirmDeleteGroup = (group) => {
        openDeleteModal(group);
    };

    const executeDelete = async () => {
        if (!state.pendingDeleteGroup || dom.deleteConfirmBtn?.disabled) return;

        const groupId = state.pendingDeleteGroup.id;

        if (dom.deleteConfirmBtn) dom.deleteConfirmBtn.disabled = true;
        if (dom.deleteConfirmText) dom.deleteConfirmText.textContent = t('admin_deleting_ellipsis', 'Deleting…');

        try {
            await api.deleteGroup(groupId);
            if (typeof notifySuccess === 'function') {
                notifySuccess(t('provider_group_delete_success', 'Provider group deleted successfully'));
            }
            closeDeleteModal();
            loadGroups();
        } catch (error) {
            if (typeof notifyError === 'function') {
                notifyError(error.message || t('provider_group_delete_failed', 'Failed to delete provider group'));
            }
            if (dom.deleteConfirmBtn) dom.deleteConfirmBtn.disabled = false;
            if (dom.deleteConfirmText) dom.deleteConfirmText.textContent = t('modal_delete_group_btn', 'Delete Group');
        }
    };

    const handleSearchInput = () => {
        renderGroupList(state.groups);
        if (dom.searchClear) {
            dom.searchClear.hidden = !(dom.searchInput?.value || '').trim();
        }
    };

    const handleSearchClear = () => {
        if (dom.searchInput) dom.searchInput.value = '';
        if (dom.searchClear) dom.searchClear.hidden = true;
        renderGroupList(state.groups);
        dom.searchInput?.focus();
    };

    const getFormSnapshot = () => ({
        name: String(dom.nameInput?.value || '').trim(),
        icon: String(dom.iconInput?.value || '').trim(),
        members: state.members.map((member) => ({
            provider_id: String(member.provider_id || ''),
            weight: Number(member.weight || 1),
        })),
    });

    const rememberFormSnapshot = () => {
        state.initialSnapshot = JSON.stringify(getFormSnapshot());
    };

    const hasUnsavedChanges = () => {
        if (!dom.formPage || dom.formPage.hidden || state.initialSnapshot === null) {
            return false;
        }
        return JSON.stringify(getFormSnapshot()) !== state.initialSnapshot;
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

    let iconPickerInstance = null;

    const initIconPicker = () => {
        if (!window.IconPicker || !dom.iconPickerContainer) return;

        dom.iconPickerContainer.innerHTML = '';

        const currentValue = dom.iconInput?.value || '';
        const picker = window.IconPicker.createIconPicker({
            value: currentValue,
            presetType: 'provider',
            onChange: (newValue) => {
                if (dom.iconInput) {
                    dom.iconInput.value = newValue;
                }
            },
        });

        dom.iconPickerContainer.appendChild(picker.container);
        iconPickerInstance = picker;
    };

    const init = () => {
        registerUnsavedGuard();
        dom.createButton?.addEventListener('click', openCreateForm);
        dom.formBack?.addEventListener('click', () => requestUnsavedConfirmation(showListPage));
        dom.form?.addEventListener('submit', handleFormSubmit);
        dom.addMemberBtn?.addEventListener('click', showAddMemberDropdown);
        dom.searchInput?.addEventListener('input', handleSearchInput);
        dom.searchClear?.addEventListener('click', handleSearchClear);

        dom.deleteCancelBtn?.addEventListener('click', closeDeleteModal);
        dom.deleteConfirmBtn?.addEventListener('click', executeDelete);
        dom.deleteOverlay?.addEventListener('click', (e) => {
            if (e.target === dom.deleteOverlay) {
                closeDeleteModal();
            }
        });

        initIconPicker();
    };

    const registerUnsavedGuard = () => {
        if (unsavedGuardRegistered || typeof window.unsavedChangesManager?.register !== 'function') {
            return;
        }
        window.unsavedChangesManager.register({
            id: UNSAVED_GUARD_ID,
            priority: 175,
            isActive: () => Boolean(dom.formPage && !dom.formPage.hidden),
            isDirty: () => hasUnsavedChanges(),
        });
        unsavedGuardRegistered = true;
    };

    window.initProviderGroupsPage = async () => {
        await loadProviders();
        await loadGroups();
    };

    init();
})();
