(function () {
    const dom = {
        listPage: document.getElementById('page-rate-limits'),
        formPage: document.getElementById('page-rate-limits-form'),
        list: document.getElementById('rateLimitList'),
        searchInput: document.getElementById('rateLimitSearchInput'),
        searchClear: document.getElementById('rateLimitSearchClear'),
        createButton: document.getElementById('rateLimitCreateButton'),
        form: document.getElementById('rateLimitForm'),
        formTitle: document.getElementById('rateLimitFormTitle'),
        formSubtitle: document.getElementById('rateLimitFormSubtitle'),
        nameInput: document.getElementById('rateLimitNameInput'),
        targetTypeSelect: document.getElementById('rateLimitTargetTypeSelect'),
        modelsSection: document.getElementById('rateLimitModelsSection'),
        toolsSection: document.getElementById('rateLimitToolsSection'),
        modelsList: document.getElementById('rateLimitModelsList'),
        toolsList: document.getElementById('rateLimitToolsList'),
        usersList: document.getElementById('rateLimitUsersList'),
        groupsList: document.getElementById('rateLimitGroupsList'),
        periodSelect: document.getElementById('rateLimitPeriodSelect'),
        timezoneSelect: document.getElementById('rateLimitTimezoneSelect'),
        quotaUnitSelect: document.getElementById('rateLimitQuotaUnitSelect'),
        maxRequestsInput: document.getElementById('rateLimitMaxRequestsInput'),
        quotaValueDesc: document.getElementById('rateLimitQuotaValueDesc'),
        isActiveInput: document.getElementById('rateLimitIsActiveInput'),
        formBack: document.getElementById('rateLimitFormBack'),
        formSubmit: document.getElementById('rateLimitFormSubmit'),
        deleteOverlay: document.getElementById('deleteRateLimitOverlay'),
        deleteMessage: document.getElementById('deleteRateLimitMessage'),
        deleteCancel: document.getElementById('deleteRateLimitCancelButton'),
        deleteConfirm: document.getElementById('deleteRateLimitPrimaryButton'),
        deleteConfirmText: document.getElementById('deleteRateLimitPrimaryText'),
        conflictOverlay: document.getElementById('rateLimitConflictOverlay'),
        conflictList: document.getElementById('rateLimitConflictList'),
        conflictCancel: document.getElementById('rateLimitConflictCancelButton'),
        conflictBack: document.getElementById('rateLimitConflictBackButton'),
    };

    if (!dom.listPage || !dom.formPage || !window.rateLimitsApi) {
        return;
    }

    const state = {
        initialized: false,
        catalogLoaded: false,
        languageObserver: null,
        rateLimits: [],
        models: [],
        tools: [],
        users: [],
        groups: [],
        editingId: null,
        pendingDelete: null,
        formReady: false,
        initialSnapshot: null,
        pendingStatusIds: new Set(),
        selectMeta: {
            period: null,
            timezone: null,
            quotaUnit: null,
            targetType: null,
        },
        escapeRegistrations: {
            form: null,
            deleteOverlay: null,
            conflictOverlay: null,
        },
    };
    const UNSAVED_GUARD_ID = 'admin-rate-limits-form-unsaved';
    let unsavedGuardRegistered = false;

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const formatT = (key, fallback, values = {}) => {
        let text = t(key, fallback);
        Object.entries(values).forEach(([name, value]) => {
            text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), String(value));
        });
        return text;
    };

    const periodLabels = {
        day: () => t('rate_limit_period_day_short', 'day'),
        week: () => t('rate_limit_period_week_short', 'week'),
        month: () => t('rate_limit_period_month_short', 'month'),
    };
    const quotaUnitOptions = [
        {
            value: 'requests',
            labelKey: 'rate_limit_quota_unit_requests',
            fallbackLabel: 'Requests',
            targetTypes: ['model'],
        },
        {
            value: 'tokens',
            labelKey: 'rate_limit_quota_unit_tokens',
            fallbackLabel: 'Tokens',
            targetTypes: ['model'],
        },
        {
            value: 'invocations',
            labelKey: 'rate_limit_quota_unit_invocations',
            fallbackLabel: 'Invocations',
            targetTypes: ['tool'],
        },
        {
            value: 'minutes',
            labelKey: 'rate_limit_quota_unit_minutes',
            fallbackLabel: 'Minutes',
            targetTypes: ['dictation', 'realtime'],
        },
    ];
    const numberFormatter = new Intl.NumberFormat(undefined);

    const escapeHtml = (value) => {
        const div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    };

    const getUserDisplayName = (user) => {
        const firstName = String(user?.first_name || '').trim();
        const lastName = String(user?.last_name || '').trim();
        const fullName = [firstName, lastName].filter(Boolean).join(' ').trim();
        return fullName || user?.email || user?.id || t('rate_limit_unknown_user', 'Unknown user');
    };

    const getToolLabel = (tool) => {
        const fallback = tool?.label || tool?.name || tool?.key || t('rate_limit_unknown_tool', 'Unknown tool');
        return tool?.label_key ? t(tool.label_key, fallback) : fallback;
    };

    const formatInteger = (value) => {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) {
            return '0';
        }
        return numberFormatter.format(Math.round(numeric));
    };

    // Both admin and user settings consume one timezone catalog and formatter.
    // These small wrappers keep the rest of this feature focused on rate-limit
    // behavior while allowing UTC to remain a safe bootstrap fallback.
    const getBrowserTimeZone = () => window.OmlorixTimeZones?.getBrowserTimeZone?.() || 'UTC';
    const formatTimeZoneLabel = (timeZone) =>
        window.OmlorixTimeZones?.formatTimeZoneLabel?.(timeZone) || String(timeZone || 'UTC');

    const populateTimeZoneSelect = (selectedValue = null) => {
        if (!dom.timezoneSelect) {
            return;
        }
        const nextValue = String(selectedValue || dom.timezoneSelect.value || getBrowserTimeZone() || 'UTC').trim() || 'UTC';
        const values = window.OmlorixTimeZones?.getSupportedTimeZoneValues?.([nextValue]) || ['UTC'];
        dom.timezoneSelect.innerHTML = values
            .map((timeZone) => `<option value="${escapeHtml(timeZone)}">${escapeHtml(formatTimeZoneLabel(timeZone))}</option>`)
            .join('');
        dom.timezoneSelect.value = values.includes(nextValue) ? nextValue : (values[0] || 'UTC');
        dom.timezoneSelect._singleSelect?.refreshOptions?.();
        dom.timezoneSelect._singleSelect?.syncFromSelect?.();
    };

    const getQuotaUnitLabel = (unit, count) => {
        const normalizedUnit = String(unit || 'requests').trim().toLowerCase();
        if (normalizedUnit === 'invocations') {
            return Number(count) === 1
                ? t('rate_limit_unit_invocation_one', 'invocation')
                : t('rate_limit_unit_invocation_other', 'invocations');
        }
        if (normalizedUnit === 'tokens') {
            return Number(count) === 1
                ? t('rate_limit_unit_token_one', 'token')
                : t('rate_limit_unit_token_other', 'tokens');
        }
        if (normalizedUnit === 'minutes') {
            return Number(count) === 1
                ? t('rate_limit_unit_minute_one', 'minute')
                : t('rate_limit_unit_minute_other', 'minutes');
        }
        return Number(count) === 1
            ? t('rate_limit_unit_request_one', 'request')
            : t('rate_limit_unit_request_other', 'requests');
    };

    const getQuotaValueDescription = () => {
        const unit = String(dom.quotaUnitSelect?.value || 'requests').trim().toLowerCase();
        if (unit === 'invocations') {
            return t('rate_limit_quota_value_invocations_desc', 'Total tool invocations allowed per period.');
        }
        if (unit === 'minutes') {
            return t('rate_limit_quota_value_minutes_desc', 'Total minutes allowed per period for the selected feature.');
        }
        return unit === 'tokens'
            ? t('rate_limit_quota_value_tokens_desc', 'Combined input and output tokens allowed per period.')
            : t('rate_limit_quota_value_requests_desc', 'Total requests allowed per period.');
    };

    const formatQuotaSubtitle = (quotaValue, quotaUnit, periodLabel) =>
        `${formatInteger(quotaValue)} ${getQuotaUnitLabel(quotaUnit, quotaValue)} / ${periodLabel}`;

    const getSupportedQuotaUnitOptions = (targetType) => {
        const normalizedTargetType = String(targetType || 'model').trim().toLowerCase();
        return quotaUnitOptions.filter((option) => option.targetTypes.includes(normalizedTargetType));
    };

    const populateQuotaUnitSelect = (targetType, selectedValue = null) => {
        if (!dom.quotaUnitSelect) {
            return;
        }
        // Rebuild the native options instead of hiding unsupported ones. The admin
        // custom select mirrors the native option list when it is initialized, so
        // stale hidden options would otherwise remain visible in the styled menu.
        const supportedOptions = getSupportedQuotaUnitOptions(targetType);
        const supportedValues = supportedOptions.map((option) => option.value);
        const requestedValue = String(selectedValue || dom.quotaUnitSelect.value || '').trim().toLowerCase();
        const nextValue = supportedValues.includes(requestedValue)
            ? requestedValue
            : supportedValues[0] || 'requests';

        dom.quotaUnitSelect.innerHTML = supportedOptions
            .map((option) => `
                <option value="${escapeHtml(option.value)}" data-i18n="${escapeHtml(option.labelKey)}">
                    ${escapeHtml(t(option.labelKey, option.fallbackLabel))}
                </option>
            `)
            .join('');
        dom.quotaUnitSelect.value = nextValue;
    };

    const syncQuotaFormState = ({ rebuildQuotaOptions = true } = {}) => {
        const targetType = String(dom.targetTypeSelect?.value || 'model').trim().toLowerCase();
        if (rebuildQuotaOptions) {
            populateQuotaUnitSelect(targetType);
        }
        if (dom.modelsSection) {
            dom.modelsSection.hidden = targetType !== 'model';
        }
        if (dom.toolsSection) {
            dom.toolsSection.hidden = targetType !== 'tool';
        }
        if (dom.quotaValueDesc) {
            dom.quotaValueDesc.textContent = getQuotaValueDescription();
        }
        if (rebuildQuotaOptions) {
            state.selectMeta.quotaUnit = null;
            initializeCustomSelects();
        }
        syncCustomSelects();
    };

    const renderBulletList = (items, { emptyText }) => {
        const labels = Array.isArray(items)
            ? items
                .map((item) => String(item || '').trim())
                .filter(Boolean)
            : [];

        if (!labels.length) {
            return `<p class="rate-limit-meta-value">${escapeHtml(emptyText)}</p>`;
        }

        return `
            <ul class="rate-limit-meta-list">
                ${labels.map((label) => `<li class="rate-limit-meta-list-item">${escapeHtml(label)}</li>`).join('')}
            </ul>
        `;
    };

    const getAppliesToLabels = (rateLimit) => {
        const userLabels = Array.isArray(rateLimit?.users)
            ? rateLimit.users.map((user) => `${getUserDisplayName(user)} (${user.email || user.id})`)
            : [];
        const groupLabels = Array.isArray(rateLimit?.groups)
            ? rateLimit.groups.map((group) => formatT('rate_limit_group_target', 'Group: {name}', { name: group.name || group.id }))
            : [];
        return [...userLabels, ...groupLabels];
    };

    const syncStaticTranslations = () => {
        if (dom.deleteMessage && !state.pendingDelete) {
            dom.deleteMessage.textContent = t('modal_delete_rate_limit_desc', 'Are you sure you want to delete this rate limit?');
        }
        if (dom.deleteConfirmText && !state.pendingDelete) {
            dom.deleteConfirmText.textContent = t('modal_delete_rate_limit_btn', 'Delete Rate Limit');
        }
    };

    const observeLanguageChanges = () => {
        if (state.languageObserver || !document.documentElement) {
            return;
        }
        state.languageObserver = new MutationObserver((mutations) => {
            const langChanged = mutations.some((mutation) => mutation.type === 'attributes' && mutation.attributeName === 'lang');
            if (!langChanged || !state.initialized) {
                return;
            }
            syncStaticTranslations();
            initializeCustomSelects();
            if (!dom.formPage.hidden) {
                const current = state.editingId
                    ? state.rateLimits.find((item) => item.id === state.editingId) || null
                    : null;
                setFormMode(current);
            }
            if (!dom.listPage.hidden) {
                renderList();
            }
        });
        state.languageObserver.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ['lang'],
        });
    };

    const setSearchClearVisibility = () => {
        if (!dom.searchClear || !dom.searchInput) {
            return;
        }
        dom.searchClear.hidden = !String(dom.searchInput.value || '').trim();
    };

    const showListPage = () => {
        window.activateAdminPage?.('rate-limits');
    };

    const showFormPage = () => {
        window.activateAdminPage?.('rate-limits-form', { history: 'none' });
        initializeCustomSelects();
    };

    const initializeCustomSelects = () => {
        if (typeof window.upgradeAdminSingleSelect !== 'function') {
            return;
        }

        if (dom.periodSelect) {
            state.selectMeta.period = window.upgradeAdminSingleSelect(dom.periodSelect, {
                key: 'rate_limit_period',
                placeholder: t('admin_select_placeholder_single', 'Select an option...'),
            });
        }

        if (dom.timezoneSelect) {
            state.selectMeta.timezone = window.upgradeAdminSingleSelect(dom.timezoneSelect, {
                key: 'rate_limit_timezone',
                placeholder: t('admin_select_placeholder_single', 'Select an option...'),
            });
        }

        if (dom.quotaUnitSelect) {
            state.selectMeta.quotaUnit = window.upgradeAdminSingleSelect(dom.quotaUnitSelect, {
                key: 'rate_limit_quota_unit',
                placeholder: t('admin_select_placeholder_single', 'Select an option...'),
            });
        }

        if (dom.targetTypeSelect) {
            state.selectMeta.targetType = window.upgradeAdminSingleSelect(dom.targetTypeSelect, {
                key: 'rate_limit_target_type',
                placeholder: t('admin_select_placeholder_single', 'Select an option...'),
            });
        }
    };

    const syncCustomSelects = () => {
        state.selectMeta.period?.syncFromSelect?.();
        state.selectMeta.timezone?.syncFromSelect?.();
        state.selectMeta.quotaUnit?.syncFromSelect?.();
        state.selectMeta.targetType?.syncFromSelect?.();
    };

    const isFormVisible = () => Boolean(dom.formPage && !dom.formPage.hidden);

    const hasBlockingEscapeTarget = () => Boolean(document.querySelector([
        '.admin-select.open',
        '.admin-multiselect.open',
        '.icon-picker-dropdown:not([hidden])',
        '.overlay-container.visible',
        '.modal-overlay.visible',
    ].join(', ')));

    const renderChecklist = (container, items, selectedIds, renderMeta) => {
        if (!container) {
            return;
        }

        const selectedSet = new Set(Array.isArray(selectedIds) ? selectedIds : []);
        if (!Array.isArray(items) || !items.length) {
            container.innerHTML = `<div class="rate-limit-empty">${t('rate_limits_empty_unavailable', 'Nothing available yet.')}</div>`;
            return;
        }

        container.innerHTML = items
            .map((item) => {
                const id = String(item.id || '');
                const checked = selectedSet.has(id) ? ' checked' : '';
                const title = renderMeta(item).title;
                const meta = renderMeta(item).meta;
                return `
                    <label class="rate-limit-checklist-item">
                        <input type="checkbox" class="form-checkbox" value="${escapeHtml(id)}"${checked}>
                        <span>
                            <span class="rate-limit-checklist-title">${escapeHtml(title)}</span>
                            ${meta ? `<span class="rate-limit-checklist-meta">${escapeHtml(meta)}</span>` : ''}
                        </span>
                    </label>
                `;
            })
            .join('');
    };

    const collectCheckedValues = (container) => {
        if (!container) {
            return [];
        }
        return Array.from(container.querySelectorAll('input[type="checkbox"]:checked'))
            .map((input) => String(input.value || '').trim())
            .filter(Boolean);
    };

    const renderFormOptions = (selected = {}) => {
        renderChecklist(
            dom.modelsList,
            state.models,
            selected.model_ids || [],
            (model) => ({
                title: model.name || model.model_name || model.id,
                meta: model.provider_name || model.provider || '',
            })
        );
        renderChecklist(
            dom.toolsList,
            state.tools,
            selected.tool_keys || [],
            (tool) => ({
                title: getToolLabel(tool),
                meta: [
                    tool.source || '',
                    tool.available === false ? t('rate_limit_tool_unavailable', 'Unavailable') : '',
                ].filter(Boolean).join(' · '),
            })
        );
        renderChecklist(
            dom.usersList,
            state.users,
            selected.user_ids || [],
            (user) => ({
                title: getUserDisplayName(user),
                meta: user.email || '',
            })
        );
        renderChecklist(
            dom.groupsList,
            state.groups,
            selected.group_ids || [],
            (group) => ({
                title: group.name || group.id,
                meta: group.id || '',
            })
        );
    };

    const setFormMode = (rateLimit = null) => {
        state.editingId = rateLimit?.id || null;
        state.formReady = true;

        if (dom.formTitle) {
            dom.formTitle.textContent = state.editingId
                ? t('rate_limit_form_edit_title', 'Edit Rate Limit')
                : t('rate_limit_form_create_title', 'Create Rate Limit');
        }
        if (dom.formSubtitle) {
            dom.formSubtitle.textContent = state.editingId
                ? t('rate_limit_form_edit_subtitle', 'Update who the rule applies to, what shares the counter, and whether the rule is active.')
                : t('rate_limit_form_create_subtitle', 'Choose what shares a counter and who the rule applies to.');
        }
        if (dom.formSubmit) {
            dom.formSubmit.textContent = state.editingId
                ? t('btn_save_changes', 'Save Changes')
                : t('rate_limit_save_btn', 'Save Rate Limit');
        }

        populateTimeZoneSelect(rateLimit?.timezone || getBrowserTimeZone());
        dom.nameInput.value = rateLimit?.name || '';
        if (dom.targetTypeSelect) {
            dom.targetTypeSelect.value = rateLimit?.target_type || 'model';
        }
        dom.periodSelect.value = rateLimit?.period || 'day';
        if (dom.timezoneSelect) {
            dom.timezoneSelect.value = rateLimit?.timezone || getBrowserTimeZone();
        }
        if (dom.quotaUnitSelect) {
            populateQuotaUnitSelect(rateLimit?.target_type || 'model', rateLimit?.quota_unit || 'requests');
        }
        dom.maxRequestsInput.value = String(rateLimit?.quota_value || rateLimit?.max_requests || 1);
        dom.isActiveInput.checked = rateLimit?.is_active !== false;
        syncCustomSelects();

        renderFormOptions(rateLimit || {});
        syncQuotaFormState();
        rememberFormSnapshot();
    };

    const getFormSnapshot = () => ({
        name: String(dom.nameInput?.value || '').trim(),
        targetType: String(dom.targetTypeSelect?.value || 'model').trim(),
        modelIds: collectCheckedValues(dom.modelsList),
        toolKeys: collectCheckedValues(dom.toolsList),
        userIds: collectCheckedValues(dom.usersList),
        groupIds: collectCheckedValues(dom.groupsList),
        period: String(dom.periodSelect?.value || 'day'),
        timezone: String(dom.timezoneSelect?.value || 'UTC').trim(),
        quotaUnit: String(dom.quotaUnitSelect?.value || 'requests').trim(),
        quotaValue: String(dom.maxRequestsInput?.value || '').trim(),
        isActive: Boolean(dom.isActiveInput?.checked),
    });

    const rememberFormSnapshot = () => {
        state.initialSnapshot = JSON.stringify(getFormSnapshot());
    };

    const markFormSaved = (rateLimit = null) => {
        if (rateLimit?.id) {
            state.editingId = rateLimit.id;
        }
        rememberFormSnapshot();
    };

    const hasUnsavedChanges = () => {
        if (!dom.formPage || dom.formPage.hidden || !state.formReady || state.initialSnapshot === null) {
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

    const renderList = () => {
        if (!dom.list) {
            return;
        }

        const searchTerm = String(dom.searchInput?.value || '').trim().toLowerCase();
        const rows = searchTerm
            ? state.rateLimits.filter((item) => {
                const haystack = [
                    item.name,
                    ...(item.models || []).map((model) => model.name),
                    ...(item.tools || []).map((tool) => getToolLabel(tool)),
                    ...(item.users || []).map((user) => user.email),
                    ...(item.users || []).map((user) => getUserDisplayName(user)),
                    ...(item.groups || []).map((group) => group.name),
                ]
                    .filter(Boolean)
                    .join(' ')
                    .toLowerCase();
                return haystack.includes(searchTerm);
            })
            : state.rateLimits;

        if (!rows.length) {
            dom.list.innerHTML = '';
            const emptyState = window.createAdminEmptyPlaceholder({
                title: searchTerm
                    ? t('rate_limits_empty_filtered', 'No rate limits match your search.')
                    : t('rate_limits_empty_default', 'No rate limits yet'),
                description: searchTerm
                    ? ''
                    : t('rate_limits_empty_desc', 'Create one to control per-user model usage.'),
                icon: Icons?.omlorix || '',
                className: 'provider-empty-state',
            });
            dom.list.appendChild(emptyState);
            return;
        }

        dom.list.innerHTML = rows
            .map((rateLimit) => {
                const modelLabels = (rateLimit.models || []).map((model) => model.name || model.id);
                const toolLabels = (rateLimit.tools || []).map((tool) => getToolLabel(tool));
                const targetType = String(rateLimit.target_type || 'model').toLowerCase();
                const featureLabels = {
                    dictation: t('rate_limit_feature_dictation', 'Dictation mode'),
                    realtime: t('rate_limit_feature_realtime', 'Realtime calls'),
                };
                const targetLabels = targetType === 'tool'
                    ? toolLabels
                    : (targetType === 'model' ? modelLabels : [featureLabels[targetType] || targetType]);
                const targetMarkup = renderBulletList(targetLabels, {
                    emptyText: targetType === 'tool'
                        ? t('rate_limit_unknown_tools', 'Unknown tools')
                        : t('rate_limit_unknown_models', 'Unknown models'),
                });
                const targetLabel = targetType === 'tool'
                    ? t('rate_limit_card_tools_label', 'Tools')
                    : (targetType === 'model'
                        ? t('rate_limit_card_models_label', 'Models')
                        : t('rate_limit_card_feature_label', 'Feature'));
                const appliesToLabels = getAppliesToLabels(rateLimit);
                const appliesToMarkup = renderBulletList(appliesToLabels, {
                    emptyText: t('rate_limit_no_targets', 'No targets'),
                });
                const statusClass = rateLimit.is_active ? 'active' : 'inactive';
                const statusLabel = rateLimit.is_active
                    ? t('users_status_active', 'Active')
                    : t('users_status_inactive', 'Inactive');
                const isStatusPending = state.pendingStatusIds.has(rateLimit.id);
                const periodLabel = periodLabels[rateLimit.period]?.() || rateLimit.period;
                const quotaUnit = rateLimit.quota_unit || 'requests';
                const quotaValue = rateLimit.quota_value || rateLimit.max_requests || 0;
                const timeZoneLabel = formatTimeZoneLabel(rateLimit.timezone || 'UTC');
                return `
                    <div class="rate-limit-card" data-rate-limit-id="${escapeHtml(rateLimit.id)}">
                        <div class="rate-limit-card-top">
                            <div>
                                <h3 class="rate-limit-card-title">${escapeHtml(rateLimit.name)}</h3>
                                <p class="rate-limit-card-subtitle">${escapeHtml(formatQuotaSubtitle(quotaValue, quotaUnit, periodLabel))}</p>
                            </div>
                            <div class="rate-limit-card-top-right">
                                <button
                                    type="button"
                                    class="rate-limit-status ${statusClass}"
                                    data-action="toggle-status"
                                    aria-pressed="${rateLimit.is_active ? 'true' : 'false'}"
                                    aria-label="${escapeHtml(formatT(
                                        rateLimit.is_active ? 'rate_limit_toggle_inactive' : 'rate_limit_toggle_active',
                                        rateLimit.is_active ? 'Set rate limit "{name}" inactive' : 'Set rate limit "{name}" active',
                                        { name: rateLimit.name }
                                    ))}"
                                    ${isStatusPending ? 'disabled' : ''}
                                >${statusLabel}</button>
                                <div class="rate-limit-card-actions">
                                    <button type="button" class="om-button border ghost" data-action="edit">${t('rate_limit_action_edit', 'Edit')}</button>
                                    <button type="button" class="om-button border danger-nofill" data-action="delete">${t('btn_delete', 'Delete')}</button>
                                </div>
                            </div>
                        </div>
                        <div class="rate-limit-meta-grid">
                            <div class="rate-limit-meta-block">
                                <p class="rate-limit-meta-label">${targetLabel}</p>
                                ${targetMarkup}
                            </div>
                            <div class="rate-limit-meta-block">
                                <p class="rate-limit-meta-label">${t('rate_limit_card_applies_to_label', 'Applies To')}</p>
                                ${appliesToMarkup}
                            </div>
                            <div class="rate-limit-meta-block">
                                <p class="rate-limit-meta-label">${t('rate_limit_card_timezone_label', 'Time Zone')}</p>
                                <p class="rate-limit-meta-value">${escapeHtml(timeZoneLabel)}</p>
                            </div>
                        </div>
                    </div>
                `;
            })
            .join('');
    };

    const fetchUsers = async () => {
        const response = await window.authedFetch('/api/v1/admin/users', { method: 'GET' });
        if (!response.ok) {
            throw await window.rateLimitsApi.buildResponseError(response, t('rate_limit_fetch_users_failed', 'Failed to load users.'));
        }
        return response.json();
    };

    const fetchGroups = async () => {
        const response = await window.authedFetch('/api/v1/groups/list', { method: 'GET' });
        if (!response.ok) {
            throw await window.rateLimitsApi.buildResponseError(response, t('rate_limit_fetch_groups_failed', 'Failed to load groups.'));
        }
        const payload = await response.json();
        if (Array.isArray(payload)) {
            return payload;
        }
        return Array.isArray(payload?.groups) ? payload.groups : [];
    };

    const loadCatalogs = async ({ force = false } = {}) => {
        if (state.catalogLoaded && !force) {
            return;
        }

        const [models, tools, users, groups] = await Promise.all([
            window.modelsApi?.fetchAdminModels?.() || Promise.resolve([]),
            window.rateLimitsApi.fetchRateLimitTools(),
            fetchUsers(),
            fetchGroups(),
        ]);

        state.models = Array.isArray(models) ? models : [];
        state.tools = Array.isArray(tools) ? tools : [];
        state.users = Array.isArray(users) ? users : [];
        state.groups = Array.isArray(groups) ? groups : [];
        state.catalogLoaded = true;
    };

    const loadRateLimits = async () => {
        state.rateLimits = await window.rateLimitsApi.fetchRateLimits();
        renderList();
    };

    const buildPayload = () => {
        const name = String(dom.nameInput?.value || '').trim();
        const targetType = String(dom.targetTypeSelect?.value || 'model').trim().toLowerCase();
        const modelIds = collectCheckedValues(dom.modelsList);
        const toolKeys = collectCheckedValues(dom.toolsList);
        const userIds = collectCheckedValues(dom.usersList);
        const groupIds = collectCheckedValues(dom.groupsList);
        const quotaUnit = String(dom.quotaUnitSelect?.value || 'requests').trim().toLowerCase();
        const quotaValue = Number.parseInt(dom.maxRequestsInput?.value || '0', 10);
        const period = String(dom.periodSelect?.value || 'day');
        const timeZone = String(dom.timezoneSelect?.value || 'UTC').trim() || 'UTC';

        if (!name) {
            throw new Error(t('rate_limit_name_required', 'Name is required.'));
        }
        if (targetType === 'model' && !modelIds.length) {
            throw new Error(t('rate_limit_model_required', 'Select at least one model.'));
        }
        if (targetType === 'tool' && !toolKeys.length) {
            throw new Error(t('rate_limit_tool_required', 'Select at least one tool.'));
        }
        if (!userIds.length && !groupIds.length) {
            throw new Error(t('rate_limit_target_required', 'Select at least one user or one group.'));
        }
        if (!Number.isFinite(quotaValue) || quotaValue < 1) {
            if (quotaUnit === 'tokens') {
                throw new Error(t('rate_limit_quota_value_tokens_invalid', 'Quota value must be at least 1 token.'));
            }
            if (quotaUnit === 'invocations') {
                throw new Error(t('rate_limit_quota_value_invocations_invalid', 'Quota value must be at least 1 invocation.'));
            }
            if (quotaUnit === 'minutes') {
                throw new Error(t('rate_limit_quota_value_minutes_invalid', 'Quota value must be at least 1 minute.'));
            }
            throw new Error(t('rate_limit_max_requests_invalid', 'Max requests must be at least 1.'));
        }
        const payload = {
            name,
            target_type: targetType,
            model_ids: targetType === 'model' ? modelIds : [],
            tool_keys: targetType === 'tool' ? toolKeys : [],
            user_ids: userIds,
            group_ids: groupIds,
            period,
            timezone: timeZone,
            quota_unit: targetType === 'tool'
                ? 'invocations'
                : (['dictation', 'realtime'].includes(targetType) ? 'minutes' : quotaUnit),
            quota_value: quotaValue,
            is_active: Boolean(dom.isActiveInput?.checked),
        };
        if (payload.quota_unit === 'requests') {
            payload.max_requests = quotaValue;
        }
        return payload;
    };

    const describeIds = (ids, lookup, formatter) =>
        ids
            .map((id) => {
                const item = lookup.get(id);
                return item ? formatter(item) : id;
            })
            .join(', ');

    const openConflictOverlay = (conflicts) => {
        if (!dom.conflictOverlay || !dom.conflictList) {
            return;
        }

        const modelLookup = new Map(state.models.map((item) => [item.id, item]));
        const toolLookup = new Map(state.tools.map((item) => [item.key || item.id, item]));
        const userLookup = new Map(state.users.map((item) => [item.id, item]));
        const groupLookup = new Map(state.groups.map((item) => [item.id, item]));

        dom.conflictList.innerHTML = conflicts
            .map((conflict) => {
                const targetType = String(conflict.target_type || 'model').toLowerCase();
                const targets = targetType === 'tool'
                    ? describeIds(conflict.overlapping_tool_keys || [], toolLookup, (item) => getToolLabel(item))
                    : (targetType === 'model'
                        ? describeIds(conflict.overlapping_model_ids || [], modelLookup, (item) => item.name || item.id)
                        : (targetType === 'dictation'
                            ? t('rate_limit_feature_dictation', 'Dictation mode')
                            : t('rate_limit_feature_realtime', 'Realtime calls')));
                const users = describeIds(conflict.overlapping_user_ids || [], userLookup, (item) => getUserDisplayName(item));
                const groups = describeIds(conflict.overlapping_group_ids || [], groupLookup, (item) => item.name || item.id);
                return `
                    <div class="rate-limit-conflict-item">
                        <strong>${escapeHtml(conflict.rate_limit_name || conflict.rate_limit_id)}</strong>
                        <div>${escapeHtml(targets || (targetType === 'tool'
                            ? t('rate_limit_conflict_no_tools', 'No overlapping tools')
                            : t('rate_limit_conflict_no_models', 'No overlapping models')))}</div>
                        <div>${escapeHtml(users || t('rate_limit_conflict_no_users', 'No overlapping users'))}</div>
                        <div>${escapeHtml(groups || t('rate_limit_conflict_no_groups', 'No overlapping groups'))}</div>
                    </div>
                `;
            })
            .join('');

        dom.conflictOverlay.hidden = false;
    };

    const closeConflictOverlay = () => {
        if (dom.conflictOverlay) {
            dom.conflictOverlay.hidden = true;
        }
    };

    const openDeleteOverlay = (rateLimit) => {
        state.pendingDelete = rateLimit;
        if (dom.deleteMessage) {
            dom.deleteMessage.textContent = formatT('rate_limit_delete_named_desc', 'Delete "{name}"? This takes effect immediately.', {
                name: rateLimit.name,
            });
        }
        if (dom.deleteConfirmText) {
            dom.deleteConfirmText.textContent = t('modal_delete_rate_limit_btn', 'Delete Rate Limit');
        }
        if (dom.deleteOverlay) {
            dom.deleteOverlay.hidden = false;
        }
    };

    const closeDeleteOverlay = () => {
        state.pendingDelete = null;
        if (dom.deleteOverlay) {
            dom.deleteOverlay.hidden = true;
        }
        if (dom.deleteConfirmText) {
            dom.deleteConfirmText.textContent = t('modal_delete_rate_limit_btn', 'Delete Rate Limit');
        }
        if (dom.deleteMessage) {
            dom.deleteMessage.textContent = t('modal_delete_rate_limit_desc', 'Are you sure you want to delete this rate limit?');
        }
    };

    const handleFormBackNavigation = () => {
        requestUnsavedConfirmation(showListPage);
    };

    const registerEscapeShortcuts = () => {
        if (typeof window === 'undefined' || typeof window.registerEscapeHandler !== 'function') {
            return;
        }

        if (!state.escapeRegistrations.deleteOverlay) {
            state.escapeRegistrations.deleteOverlay = window.registerEscapeHandler({
                id: 'admin-rate-limits-delete-overlay-escape',
                priority: 210,
                isActive: () => Boolean(dom.deleteOverlay && !dom.deleteOverlay.hidden),
                close: closeDeleteOverlay,
            });
        }

        if (!state.escapeRegistrations.conflictOverlay) {
            state.escapeRegistrations.conflictOverlay = window.registerEscapeHandler({
                id: 'admin-rate-limits-conflict-overlay-escape',
                priority: 210,
                isActive: () => Boolean(dom.conflictOverlay && !dom.conflictOverlay.hidden),
                close: closeConflictOverlay,
            });
        }

        if (!state.escapeRegistrations.form) {
            state.escapeRegistrations.form = window.registerEscapeHandler({
                id: 'admin-rate-limits-form-escape',
                priority: 140,
                isActive: () => isFormVisible() && !hasBlockingEscapeTarget(),
                close: handleFormBackNavigation,
            });
        }
    };

    const openCreateForm = async () => {
        try {
            await loadCatalogs();
            setFormMode();
            showFormPage();
        } catch (error) {
            notifyError?.(error.message || t('rate_limit_load_form_failed', 'Failed to load rate limit form.'));
        }
    };

    const openEditForm = async (rateLimitId) => {
        try {
            await loadCatalogs();
            const rateLimit = await window.rateLimitsApi.fetchRateLimit(rateLimitId);
            setFormMode(rateLimit);
            showFormPage();
        } catch (error) {
            notifyError?.(error.message || t('rate_limit_load_one_failed', 'Failed to load rate limit.'));
        }
    };

    const toggleRateLimitStatus = async (rateLimit) => {
        if (!rateLimit?.id || state.pendingStatusIds.has(rateLimit.id)) {
            return;
        }

        state.pendingStatusIds.add(rateLimit.id);
        renderList();

        try {
            const response = await window.rateLimitsApi.updateRateLimit(rateLimit.id, {
                is_active: !Boolean(rateLimit.is_active),
            });
            const conflicts = Array.isArray(response?.conflicts) ? response.conflicts : [];
            if (conflicts.length) {
                openConflictOverlay(conflicts);
                return;
            }

            const updated = response?.updated || null;
            if (updated?.id) {
                state.rateLimits = state.rateLimits.map((item) => item.id === updated.id ? updated : item);
            } else {
                await loadRateLimits();
                return;
            }

            notifySuccess?.(updated.is_active
                ? t('rate_limit_activated_success', 'Rate limit activated.')
                : t('rate_limit_deactivated_success', 'Rate limit deactivated.'));
        } catch (error) {
            notifyError?.(error.message || t('rate_limit_toggle_failed', 'Failed to update rate limit status.'));
        } finally {
            state.pendingStatusIds.delete(rateLimit.id);
            renderList();
        }
    };

    const submitForm = async (event) => {
        event.preventDefault();

        try {
            const payload = buildPayload();
            const isEditing = Boolean(state.editingId);
            let response;

            if (isEditing) {
                response = await window.rateLimitsApi.updateRateLimit(state.editingId, payload);
            } else {
                response = await window.rateLimitsApi.createRateLimit(payload);
            }

            const conflicts = Array.isArray(response?.conflicts) ? response.conflicts : [];
            if (conflicts.length) {
                openConflictOverlay(conflicts);
                return;
            }

            markFormSaved(response?.updated || response?.created || null);
            await loadRateLimits();
            notifySuccess?.(isEditing
                ? t('rate_limit_updated_success', 'Rate limit updated.')
                : t('rate_limit_created_success', 'Rate limit created.'));
            showListPage();
        } catch (error) {
            notifyError?.(error.message || t('rate_limit_save_failed', 'Failed to save rate limit.'));
        }
    };

    const handleListClick = (event) => {
        const card = event.target.closest('[data-rate-limit-id]');
        if (!card) {
            return;
        }

        const rateLimitId = card.dataset.rateLimitId;
        const row = state.rateLimits.find((item) => item.id === rateLimitId);
        if (!row) {
            return;
        }

        const actionButton = event.target.closest('[data-action]');
        const action = actionButton?.dataset.action;

        if (action === 'toggle-status') {
            toggleRateLimitStatus(row);
            return;
        }

        if (action === 'delete') {
            openDeleteOverlay(row);
            return;
        }

        openEditForm(rateLimitId);
    };

    const confirmDelete = async () => {
        if (!state.pendingDelete?.id) {
            return;
        }

        try {
            const response = await window.rateLimitsApi.deleteRateLimit(state.pendingDelete.id);
            if (!response.ok) {
                throw await window.rateLimitsApi.buildResponseError(response, t('rate_limit_delete_failed', 'Failed to delete rate limit.'));
            }
            closeDeleteOverlay();
            await loadRateLimits();
            notifySuccess?.(t('rate_limit_deleted_success', 'Rate limit deleted.'));
        } catch (error) {
            notifyError?.(error.message || t('rate_limit_delete_failed', 'Failed to delete rate limit.'));
        }
    };

    const bindEvents = () => {
        registerUnsavedGuard();
        registerEscapeShortcuts();
        if (state.initialized) {
            return;
        }

        initializeCustomSelects();
        populateTimeZoneSelect(getBrowserTimeZone());
        syncCustomSelects();

        dom.createButton?.addEventListener('click', openCreateForm);
        dom.form?.addEventListener('submit', submitForm);
        dom.formBack?.addEventListener('click', handleFormBackNavigation);
        dom.searchInput?.addEventListener('input', () => {
            setSearchClearVisibility();
            renderList();
        });
        dom.quotaUnitSelect?.addEventListener('change', () => syncQuotaFormState({ rebuildQuotaOptions: false }));
        dom.targetTypeSelect?.addEventListener('change', syncQuotaFormState);
        dom.searchClear?.addEventListener('click', () => {
            if (!dom.searchInput) {
                return;
            }
            dom.searchInput.value = '';
            setSearchClearVisibility();
            renderList();
            dom.searchInput.focus();
        });
        dom.list?.addEventListener('click', handleListClick);
        dom.deleteCancel?.addEventListener('click', closeDeleteOverlay);
        dom.deleteConfirm?.addEventListener('click', confirmDelete);
        dom.conflictCancel?.addEventListener('click', closeConflictOverlay);
        dom.conflictBack?.addEventListener('click', closeConflictOverlay);

        state.initialized = true;
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
            discard: () => {
                if (state.formReady) {
                    rememberFormSnapshot();
                } else {
                    state.initialSnapshot = null;
                }
            },
        });
        unsavedGuardRegistered = true;
    };

    window.initRateLimitsPage = async () => {
        observeLanguageChanges();
        bindEvents();
        setSearchClearVisibility();
        syncStaticTranslations();

        try {
            if (!state.catalogLoaded || !dom.listPage.hidden || !dom.formReady) {
                await loadCatalogs();
            }
            if (!dom.listPage.hidden) {
                await loadRateLimits();
            } else if (!dom.formPage.hidden && !state.formReady) {
                setFormMode();
                initializeCustomSelects();
            }
        } catch (error) {
            notifyError?.(error.message || t('rate_limit_load_failed', 'Failed to load rate limits.'));
        }
    };
})();
