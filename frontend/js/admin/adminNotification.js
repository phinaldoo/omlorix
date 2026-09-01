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

    const el = {
        openButton: document.getElementById('openAdminNotificationsPage'),
        list: document.getElementById('adminNotificationsList'),
        empty: document.getElementById('adminNotificationsEmpty'),
        pagination: document.getElementById('adminNotificationsPagination'),
        info: document.getElementById('adminNotificationsInfo'),
        prev: document.getElementById('adminNotificationsPrev'),
        next: document.getElementById('adminNotificationsNext'),
        refresh: document.getElementById('refreshAdminNotifications'),
        pageInfo: document.getElementById('adminNotificationsPageInfo'),
        deleteBtn: document.getElementById('deleteAllNotificationsBtn'),
        deleteModal: document.getElementById('deleteNotificationsModal'),
        cancelDelete: document.getElementById('cancelDeleteNotifications'),
        confirmDelete: document.getElementById('confirmDeleteNotifications'),
        typeFilterSelect: document.getElementById('typeFilterSelect'),
        categoryFilterSelect: document.getElementById('categoryFilterSelect'),
        exportBtn: document.getElementById('exportNotificationsBtn'),
    };
    const notificationSettingsStatus = document.getElementById('notificationSettingsStatus');
    const notificationSettingsFields = document.getElementById('notificationSettingsFields');
    const notificationSettingsController = typeof window.createSettingsPageController === 'function'
        ? window.createSettingsPageController({
            pageKey: 'notifications',
            containerId: notificationSettingsFields,
            statusId: notificationSettingsStatus,
            stringDebounceMs: 600,
            loadErrorMessage: t('notif_settings_load_error', 'Unable to load notification settings.'),
            onError: (message) => notifyError?.(message),
        })
        : null;

    const state = {
        page: 1,
        pageSize: 20,
        total: 0,
        hasNext: false,
        currentItems: [], // Items for current page from server
        activeTypes: new Set(['info', 'warning', 'error']), // Default: all types selected
        allCategories: new Set(), // All unique categories from server
        allTypes: new Set(['info', 'warning', 'error']), // All available types from server
        activeCategories: new Set(), // Currently selected categories (default: all)
        isInitialLoad: true, // Track if this is the first load
    };

    const SHOW_MORE_LABEL = t('notif_show_more', 'Show more');
    const SHOW_LESS_LABEL = t('notif_show_less', 'Show less');
    const TRUNCATION_TOLERANCE_PX = 1;

    let loading = false;
    let refreshCooldown = false;
    let truncationCheckFrame = null;
    let listResizeObserver = null;
    let truncationObserversActive = false;

    const scrollNotificationsListToTop = () => {
        if (!el.list) return;

        if (typeof el.list.scrollTo === 'function') {
            el.list.scrollTo({ top: 0, left: 0, behavior: 'auto' });
            return;
        }

        el.list.scrollTop = 0;
    };

    const formatTimestamp = (value) => {
        if (!value) return t('dashboard_notifications_unknown_time', 'Unknown time');
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return t('dashboard_notifications_unknown_time', 'Unknown time');
        return date.toLocaleString([], {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    };

    const normalizeCategory = (category) => String(category || '').trim().toLowerCase();

    const formatCategoryFallback = (value, { upper = false } = {}) => {
        if (!value) {
            const fallback = t('dashboard_notification_category_default', upper ? 'GENERAL' : 'General');
            return upper ? String(fallback).toUpperCase() : fallback;
        }

        const normalized = String(value).replace(/_/g, ' ').trim();
        if (!normalized) {
            const fallback = t('dashboard_notification_category_default', upper ? 'GENERAL' : 'General');
            return upper ? String(fallback).toUpperCase() : fallback;
        }

        if (upper) {
            return normalized.toUpperCase();
        }

        return normalized
            .split(/\s+/)
            .filter(Boolean)
            .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
            .join(' ');
    };

    const getKnownCategoryMeta = (category) => {
        switch (normalizeCategory(category)) {
            case 'general':
                return { i18nKey: 'notif_category_general', fallback: 'General', badgeKey: 'general' };
            case 'security':
                return { i18nKey: 'notif_category_security', fallback: 'Security', badgeKey: 'security' };
            case 'user':
                return { i18nKey: 'notif_category_user', fallback: 'User', badgeKey: 'user' };
            case 'system':
                return { i18nKey: 'notif_category_system', fallback: 'System', badgeKey: 'system' };
            case 'success':
                return { i18nKey: 'notif_category_success', fallback: 'Success', badgeKey: 'success' };
            case 'payment':
                return { i18nKey: 'notif_category_payment', fallback: 'Payment', badgeKey: 'payment' };
            case 'admin':
                return { i18nKey: 'notif_category_admin', fallback: 'Admin', badgeKey: 'general' };
            case 'auth':
                return { i18nKey: 'notif_category_auth', fallback: 'Authentication', badgeKey: 'security' };
            case 'suspicious_auth':
                return { i18nKey: 'notif_category_suspicious_auth', fallback: 'Suspicious Authentication', badgeKey: 'security' };
            case 'ldap':
                return { i18nKey: 'notif_category_ldap', fallback: 'LDAP', badgeKey: 'system' };
            case 'user_pending':
                return { i18nKey: 'notif_category_user_pending', fallback: 'Pending User', badgeKey: 'user' };
            case 'llm_provider_availability':
                return { i18nKey: 'notif_category_llm_provider_availability', fallback: 'Provider Availability', badgeKey: 'system' };
            case 'llm_provider_decryption_error':
                return { i18nKey: 'notif_category_llm_provider_decryption_error', fallback: 'Provider Credential Error', badgeKey: 'security' };
            case 'model_health':
                return { i18nKey: 'notif_category_model_health', fallback: 'Model Health', badgeKey: 'system' };
            case 'llm_model_added':
                return { i18nKey: 'notif_category_llm_model_added', fallback: 'LLM Model Added', badgeKey: 'success' };
            case 'llm_model_removed':
                return { i18nKey: 'notif_category_llm_model_removed', fallback: 'LLM Model Removed', badgeKey: 'system' };
            case 'llm_model_auto_deleted':
                return { i18nKey: 'notif_category_llm_model_auto_deleted', fallback: 'LLM Model Auto-Deleted', badgeKey: 'system' };
            default:
                return null;
        }
    };

    const formatCategory = (value) => {
        const meta = getKnownCategoryMeta(value);
        if (meta) {
            return t(meta.i18nKey, meta.fallback);
        }
        return formatCategoryFallback(value, { upper: true });
    };

    const getCategoryKey = (category) => {
        const meta = getKnownCategoryMeta(category);
        if (meta?.badgeKey) return meta.badgeKey;

        if (!category) return 'general';
        const lower = normalizeCategory(category);
        if (lower.includes('security') || lower.includes('auth')) return 'security';
        if (lower.includes('user') || lower.includes('account')) return 'user';
        if (lower.includes('system') || lower.includes('config')) return 'system';
        if (lower.includes('success') || lower.includes('complete')) return 'success';
        if (lower.includes('payment') || lower.includes('billing')) return 'payment';
        return 'general';
    };

    const getTypeIcon = (type) => {
        const icons = {
            info: Icons.info,
            warning: Icons.warning,
            error: Icons.error,
        };
        return icons[type] || icons.info;
    };

    // Server-side filtering - no need for client-side getFilteredItems
    const getActiveFilters = () => {
        const filters = {};
        
        // Only send types filter if not all are selected
        if (state.activeTypes.size > 0 && state.activeTypes.size < state.allTypes.size) {
            filters.types = Array.from(state.activeTypes);
        }
        
        // Only send categories filter if not all are selected
        if (state.activeCategories.size > 0 && state.activeCategories.size < state.allCategories.size) {
            filters.categories = Array.from(state.activeCategories);
        }
        
        return filters;
    };


    const formatCategoryName = (category) => {
        const meta = getKnownCategoryMeta(category);
        if (meta) {
            return t(meta.i18nKey, meta.fallback);
        }
        return formatCategoryFallback(category, { upper: false });
    };

    const formatTypeLabel = (type) => {
        const normalized = String(type || 'info').toLowerCase();
        switch (normalized) {
            case 'warning':
                return t('notif_type_warning', 'Warning');
            case 'error':
                return t('notif_type_error', 'Error');
            case 'info':
            default:
                return t('notif_type_info', 'Info');
        }
    };

    const shouldRenderNotificationDetails = (category, details) => {
        if (details === undefined || details === null || details === '') {
            return false;
        }

        // Known categories already have a curated badge/label and a human-readable
        // message in the main notification row. Hiding the raw details payload for
        // those categories keeps the list scannable and avoids duplicate content.
        return getKnownCategoryMeta(category) === null;
    };

    const formatNotificationDetails = (details) => {
        if (details === undefined || details === null || details === '') {
            return '';
        }

        if (typeof details === 'string') {
            return details;
        }

        try {
            return JSON.stringify(details, null, 2);
        } catch (_error) {
            return String(details);
        }
    };

    const TYPE_FILTER_ORDER = ['info', 'warning', 'error'];

    const sortTypes = (types) =>
        Array.from(types).sort((a, b) => {
            const leftIndex = TYPE_FILTER_ORDER.indexOf(a);
            const rightIndex = TYPE_FILTER_ORDER.indexOf(b);
            const normalizedLeft = leftIndex === -1 ? Number.MAX_SAFE_INTEGER : leftIndex;
            const normalizedRight = rightIndex === -1 ? Number.MAX_SAFE_INTEGER : rightIndex;

            if (normalizedLeft !== normalizedRight) {
                return normalizedLeft - normalizedRight;
            }

            return formatTypeLabel(a).localeCompare(formatTypeLabel(b));
        });

    const upsertSelectOption = (select, value, label, selected) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        option.selected = selected;
        select.appendChild(option);
    };

    const applyFilter = () => {
        loadNotifications({ page: 1 });
    };

    const syncNotificationFilterControl = (select, field, wrapperClassName, menuClassName) => {
        if (!select || typeof window.upgradeAdminMultiSelect !== 'function') {
            return null;
        }

        window.upgradeAdminMultiSelect(select, field);

        const meta = select._multiSelect;
        if (!meta?.wrapper) {
            return null;
        }

        if (wrapperClassName) {
            meta.wrapper.classList.add(wrapperClassName);
        }
        if (menuClassName) {
            meta.wrapper.querySelector('.admin-multiselect-menu')?.classList.add(menuClassName);
        }
        return meta;
    };

    const renderTypeFilterControl = () => {
        if (!el.typeFilterSelect) {
            return;
        }

        const select = el.typeFilterSelect;
        const sortedTypes = sortTypes(state.allTypes);
        select.innerHTML = '';

        sortedTypes.forEach((type) => {
            upsertSelectOption(select, type, formatTypeLabel(type), state.activeTypes.has(type));
        });

        syncNotificationFilterControl(
            select,
            {
                key: 'notification-types',
                placeholder: t('notif_filter_all_types', 'All Types'),
                multiselectSummary: ({ selectedOptions, totalOptions }) => {
                    const selectedCount = selectedOptions.length;
                    const totalCount = totalOptions.length || TYPE_FILTER_ORDER.length;

                    if (selectedCount === 0) {
                        return { text: t('notif_filter_no_types', 'No Types'), placeholder: false };
                    }
                    if (selectedCount >= totalCount) {
                        return { text: t('notif_filter_all_types', 'All Types'), placeholder: false };
                    }
                    return {
                        text: selectedOptions.map((option) => option.textContent || '').filter(Boolean).join(', '),
                        placeholder: false,
                    };
                },
            },
            'admin-notifications-type-multiselect',
            'admin-notifications-type-multiselect-menu'
        );
    };

    const renderCategoryFilterControl = () => {
        if (!el.categoryFilterSelect) {
            return;
        }

        const select = el.categoryFilterSelect;
        const sortedCategories = Array.from(state.allCategories).sort((a, b) =>
            formatCategoryName(a).localeCompare(formatCategoryName(b))
        );

        select.innerHTML = '';

        sortedCategories.forEach((category) => {
            upsertSelectOption(
                select,
                category,
                formatCategoryName(category),
                state.activeCategories.has(category)
            );
        });

        syncNotificationFilterControl(
            select,
            {
                key: 'notification-categories',
                placeholder: t('notif_filter_all_categories', 'All Categories'),
                searchable: true,
                search: {
                    enabled: true,
                    disableMobileAutoFocus: true,
                    placeholder: t(
                        'notif_search_categories_placeholder',
                        'Search categories...'
                    ),
                    emptyMessage: t(
                        'notif_filter_no_categories_available',
                        'No categories available'
                    ),
                },
                multiselectSummary: ({ selectedOptions, totalOptions }) => {
                    const selectedCount = selectedOptions.length;
                    const totalCount = totalOptions.length;

                    if (selectedCount === 0) {
                        if (totalCount === 0 && state.isInitialLoad) {
                            return {
                                text: t('notif_filter_all_categories', 'All Categories'),
                                placeholder: false,
                            };
                        }
                        return {
                            text: t('notif_filter_no_categories', 'No Categories'),
                            placeholder: false,
                        };
                    }
                    if (selectedCount >= totalCount) {
                        return {
                            text: t('notif_filter_all_categories', 'All Categories'),
                            placeholder: false,
                        };
                    }
                    if (selectedCount <= 2) {
                        return {
                            text: selectedOptions.map((option) => option.textContent || '').filter(Boolean).join(', '),
                            placeholder: false,
                        };
                    }
                    return {
                        text: formatT(
                            'notif_filter_categories_selected',
                            `${selectedCount} Categories`,
                            { count: selectedCount }
                        ),
                        placeholder: false,
                    };
                },
            },
            'admin-notifications-category-multiselect',
            'admin-notifications-category-multiselect-menu'
        );
    };

    const setExpandButtonState = (expandBtn, isExpanded) => {
        const textSpan = expandBtn?.querySelector('.expand-text');
        if (textSpan) {
            textSpan.textContent = isExpanded ? SHOW_LESS_LABEL : SHOW_MORE_LABEL;
        }
        expandBtn?.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
    };

    const isMessageTruncatedInCollapsedState = (wrapper) => {
        const messageEl = wrapper?.querySelector('.admin-notification-message');
        if (!messageEl) return false;

        const wasExpanded = wrapper.classList.contains('is-expanded');
        if (wasExpanded) {
            wrapper.classList.remove('is-expanded');
        }

        const hasLayout = messageEl.clientWidth > 0;
        const isTruncated = hasLayout
            && (
                (messageEl.scrollHeight - messageEl.clientHeight) > TRUNCATION_TOLERANCE_PX
                || (messageEl.scrollWidth - messageEl.clientWidth) > TRUNCATION_TOLERANCE_PX
            );

        if (wasExpanded) {
            wrapper.classList.add('is-expanded');
        }

        return isTruncated;
    };

    const updateMessageTruncationState = (wrapper) => {
        const expandBtn = wrapper?.querySelector('.admin-notification-expand-btn');
        if (!expandBtn) return;

        const isTruncated = isMessageTruncatedInCollapsedState(wrapper);
        if (!isTruncated) {
            wrapper.classList.remove('is-expanded');
            expandBtn.hidden = true;
            setExpandButtonState(expandBtn, false);
            return;
        }

        expandBtn.hidden = false;
        setExpandButtonState(expandBtn, wrapper.classList.contains('is-expanded'));
    };

    const updateAllMessageTruncationStates = () => {
        if (!el.list) return;

        const messageWrappers = el.list.querySelectorAll('.admin-notification-message-wrapper');
        messageWrappers.forEach((wrapper) => updateMessageTruncationState(wrapper));
    };

    const scheduleTruncationCheck = () => {
        if (truncationCheckFrame !== null) return;

        truncationCheckFrame = requestAnimationFrame(() => {
            truncationCheckFrame = null;
            updateAllMessageTruncationStates();
        });
    };

    const handleTruncationLayoutChange = () => {
        scheduleTruncationCheck();
    };

    const attachTruncationObservers = () => {
        if (truncationObserversActive) return;
        truncationObserversActive = true;

        if (typeof ResizeObserver === 'function' && el.list) {
            listResizeObserver = new ResizeObserver(handleTruncationLayoutChange);
            listResizeObserver.observe(el.list);
        }

        window.addEventListener('resize', handleTruncationLayoutChange, { passive: true });
        window.visualViewport?.addEventListener?.('resize', handleTruncationLayoutChange, { passive: true });
        document.fonts?.addEventListener?.('loadingdone', handleTruncationLayoutChange);
    };

    const detachTruncationObservers = () => {
        if (!truncationObserversActive) return;
        truncationObserversActive = false;

        listResizeObserver?.disconnect();
        listResizeObserver = null;

        window.removeEventListener('resize', handleTruncationLayoutChange);
        window.visualViewport?.removeEventListener?.('resize', handleTruncationLayoutChange);
        document.fonts?.removeEventListener?.('loadingdone', handleTruncationLayoutChange);

        if (truncationCheckFrame !== null) {
            cancelAnimationFrame(truncationCheckFrame);
            truncationCheckFrame = null;
        }
    };

    const renderList = (items) => {
        el.list.innerHTML = '';
        const list = Array.isArray(items) ? items : [];

        if (!list.length) {
            if (el.empty) {
                el.empty.hidden = false;
                // Update empty message based on filter state
                const emptyTitle = el.empty.querySelector('.user-notifications-empty-title');
                const emptyText = el.empty.querySelector('.user-notifications-empty-text');
                const hasFilters = state.activeTypes.size < state.allTypes.size || state.activeCategories.size < state.allCategories.size;
                if (hasFilters) {
                    if (emptyTitle) emptyTitle.textContent = t('notif_empty_filtered_title', 'No matching notifications');
                    if (emptyText) emptyText.textContent = t('notif_empty_filtered_desc', 'No notifications match your current filters. Try adjusting your type or category selection.');
                } else {
                    if (emptyTitle) emptyTitle.textContent = t('notifications_empty_title', 'No notifications yet');
                    if (emptyText) emptyText.textContent = t('notifications_empty_text', 'When important events occur, they\'ll appear here. Check back later for updates.');
                }
                el.list.appendChild(el.empty);
            } else {
                el.list.textContent = t('notif_empty_plain', 'No notifications yet.');
            }
            return;
        }

        if (el.empty) {
            el.empty.hidden = true;
        }

        const fragment = document.createDocumentFragment();

        list.forEach(({ category, type, message, timestamp, details }) => {
            const categoryKey = getCategoryKey(category);
            const notifType = (type || 'info').toLowerCase();
            const item = document.createElement('article');
            item.className = 'admin-notification-item';
            item.setAttribute('data-type', notifType);

            // Type indicator icon (left side)
            const typeIconWrapper = document.createElement('div');
            typeIconWrapper.className = 'admin-notification-type-icon';
            typeIconWrapper.setAttribute('data-type', notifType);
            typeIconWrapper.innerHTML = getTypeIcon(notifType);

            // Content
            const content = document.createElement('div');
            content.className = 'admin-notification-content';

            // Header (category badge + type badge + time)
            const header = document.createElement('div');
            header.className = 'admin-notification-header';

            const badgesWrapper = document.createElement('div');
            badgesWrapper.className = 'admin-notification-badges';

            const categoryBadge = document.createElement('span');
            categoryBadge.className = 'admin-notification-category';
            categoryBadge.setAttribute('data-category', categoryKey);
            categoryBadge.textContent = formatCategory(category);

            const typeBadge = document.createElement('span');
            typeBadge.className = 'admin-notification-type-badge';
            typeBadge.setAttribute('data-type', notifType);
            typeBadge.textContent = formatTypeLabel(notifType);

            badgesWrapper.append(typeBadge, categoryBadge);

            const timeEl = document.createElement('span');
            timeEl.className = 'admin-notification-time';
            timeEl.innerHTML = `${Icons.clock}${formatTimestamp(timestamp)}`;

            header.append(badgesWrapper, timeEl);

            // Message wrapper for expandable content
            const messageWrapper = document.createElement('div');
            messageWrapper.className = 'admin-notification-message-wrapper';

            const messageEl = document.createElement('p');
            messageEl.className = 'admin-notification-message';
            const messageText = message || t('dashboard_notifications_no_message', 'No message provided.');
            messageEl.textContent = messageText;

            messageWrapper.appendChild(messageEl);

            // Check if message needs expand button (longer content)
            // We'll check after render if it's actually truncated
            const expandBtn = document.createElement('button');
            expandBtn.className = 'admin-notification-expand-btn';
            expandBtn.type = 'button';
            expandBtn.setAttribute('aria-expanded', 'false');
            expandBtn.innerHTML = `
                <span class="expand-text">${SHOW_MORE_LABEL}</span>
                ${Icons.chevron}
            `;
            expandBtn.hidden = true; // Hidden by default, shown if text is truncated

            expandBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                const isExpanded = messageWrapper.classList.toggle('is-expanded');
                setExpandButtonState(expandBtn, isExpanded);
            });

            messageWrapper.appendChild(expandBtn);

            content.append(header, messageWrapper);

            // Optional details
            const detailsText = shouldRenderNotificationDetails(category, details)
                ? formatNotificationDetails(details)
                : '';
            if (detailsText) {
                const detailsEl = document.createElement('pre');
                detailsEl.className = 'admin-notification-details';
                detailsEl.textContent = detailsText;
                detailsEl.style.fontFamily = 'var(--font-family-monospace, "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace)';
                detailsEl.style.whiteSpace = 'pre-wrap';
                detailsEl.style.overflowWrap = 'anywhere';
                detailsEl.style.maxHeight = '180px';
                detailsEl.style.overflow = 'auto';
                content.appendChild(detailsEl);
            }

            item.append(typeIconWrapper, content);
            fragment.appendChild(item);
        });

        el.list.appendChild(fragment);
        scheduleTruncationCheck();
    };

    const renderPagination = () => {
        const { pagination, prev, next, info, pageInfo } = el;
        const hasFilters = state.activeTypes.size < state.allTypes.size || state.activeCategories.size < state.allCategories.size;

        // Update page info in toolbar
        if (pageInfo) {
            if (state.total > 0) {
                if (hasFilters) {
                    pageInfo.textContent = formatT(
                        state.total === 1 ? 'notif_page_info_filtered_single' : 'notif_page_info_filtered_plural',
                        `${state.total} notification${state.total === 1 ? '' : 's'} (filtered)`,
                        { count: state.total }
                    );
                } else {
                    pageInfo.textContent = formatT(
                        state.total === 1 ? 'notif_page_info_single' : 'notif_page_info_plural',
                        `${state.total} notification${state.total === 1 ? '' : 's'}`,
                        { count: state.total }
                    );
                }
            } else {
                pageInfo.textContent = hasFilters
                    ? t('notif_empty_filtered_title', 'No matching notifications')
                    : t('notif_empty_none', 'No notifications');
            }
        }

        if (!pagination || !prev || !next || !info) {
            return;
        }

        const hasPrev = state.page > 1;
        const hasNext = state.hasNext;
        const singlePage = state.total <= state.pageSize;
        const shouldHide = singlePage && !hasPrev && !hasNext;

        pagination.hidden = shouldHide;
        prev.disabled = !hasPrev;
        next.disabled = !hasNext;

        if (!shouldHide) {
            const start = state.total === 0 ? 0 : (state.page - 1) * state.pageSize + 1;
            const end = state.total === 0 ? 0 : Math.min(state.page * state.pageSize, state.total);
            info.textContent = formatT('notif_pagination_showing', `Showing ${start}-${end} of ${state.total}`, {
                start,
                end,
                total: state.total,
            });
        } else {
            info.textContent = '';
        }
    };

    const loadNotifications = async ({ page = 1, isManualRefresh = false, resetScroll = false } = {}) => {
        if (loading) return;
        loading = true;

        if (resetScroll) {
            scrollNotificationsListToTop();
        }

        if (el.refresh) {
            if (typeof window.adminSetRefreshButtonLoadingState === 'function') {
                window.adminSetRefreshButtonLoadingState(el.refresh, true);
            } else {
                el.refresh.classList.add('is-loading');
                const refreshIcon = el.refresh.querySelector('.refresh-icon');
                const checkIcon = el.refresh.querySelector('.check-icon');
                if (refreshIcon) refreshIcon.hidden = false;
                if (checkIcon) checkIcon.hidden = true;
                el.refresh.classList.remove('is-success');
            }
        }
        
        // Add loading class to list for visual feedback
        if (el.list) {
            el.list.classList.add('is-loading');
        }

        const hasSelectedTypes = state.allTypes.size === 0 || state.activeTypes.size > 0;
        const hasSelectedCategories =
            state.allCategories.size === 0 || state.activeCategories.size > 0;

        if (!hasSelectedTypes || !hasSelectedCategories) {
            state.page = 1;
            state.total = 0;
            state.hasNext = false;
            state.currentItems = [];
            renderList([]);
            renderPagination();
            loading = false;
            if (el.refresh) {
                if (typeof window.adminSetRefreshButtonLoadingState === 'function') {
                    window.adminSetRefreshButtonLoadingState(el.refresh, false);
                } else {
                    el.refresh.classList.remove('is-loading');
                }
            }
            if (el.list) {
                el.list.classList.remove('is-loading');
            }
            return;
        }

        try {
            const origin = window.location.origin || `${window.location.protocol}//${window.location.host}`;
            const url = new URL('/api/v1/admin/notifications', origin);
            url.searchParams.set('page', String(page));
            url.searchParams.set('page_size', String(state.pageSize));
            
            // Add filter params - only send if filters are active (not all selected)
            const filters = getActiveFilters();
            if (filters.types) {
                filters.types.forEach(t => url.searchParams.append('types', t));
            }
            if (filters.categories) {
                filters.categories.forEach(c => url.searchParams.append('categories', c));
            }

            const response = await authedFetch(url.toString());

            if (response.status === 401 || response.status === 403) {
                if (typeof redirectToLogin === 'function') {
                    redirectToLogin();
                }
                return;
            }

            if (!response.ok) {
                if (typeof notifyError === 'function') {
                    notifyError(formatT('notif_load_failed_status', `Failed to load notifications (status ${response.status}).`, {
                        status: response.status,
                    }));
                }
                return;
            }

            const payload = await response.json();
            const items = Array.isArray(payload.items) ? payload.items : [];

            state.pageSize = Math.max(Number(payload.page_size) || state.pageSize, 1);
            state.total = Math.max(Number(payload.total) || 0, 0);
            state.currentItems = items;

            // Update available categories and types from server
            const serverCategories = new Set(payload.available_categories || []);
            const serverTypes = new Set(payload.available_types || ['info', 'warning', 'error']);
            
            // On first load, select all categories and types by default
            if (state.isInitialLoad) {
                state.allCategories = serverCategories;
                state.allTypes = serverTypes;
                state.activeCategories = new Set(serverCategories);
                state.activeTypes = new Set(serverTypes);
                state.isInitialLoad = false;
            } else {
                // Update available options but keep user selections
                state.allCategories = serverCategories;
                state.allTypes = serverTypes;
                
                // Remove any active filters that no longer exist
                state.activeCategories = new Set(
                    [...state.activeCategories].filter(cat => serverCategories.has(cat))
                );
                state.activeTypes = new Set(
                    [...state.activeTypes].filter(t => serverTypes.has(t))
                );
                
                // If all categories were removed, select all available ones
                if (state.activeCategories.size === 0 && serverCategories.size > 0) {
                    state.activeCategories = new Set(serverCategories);
                }
                // If all types were removed, select all available ones
                if (state.activeTypes.size === 0 && serverTypes.size > 0) {
                    state.activeTypes = new Set(serverTypes);
                }
            }
            
            renderTypeFilterControl();
            renderCategoryFilterControl();

            const maxPage = state.total > 0 ? Math.ceil(state.total / state.pageSize) : 1;
            const receivedPage = Number(payload.page);
            state.page = Math.min(
                Math.max(Number.isFinite(receivedPage) && receivedPage > 0 ? receivedPage : page, 1),
                Math.max(maxPage, 1),
            );
            state.hasNext = typeof payload.has_next === 'boolean'
                ? payload.has_next
                : state.page < maxPage;

            renderList(items);
            renderPagination();

            if (resetScroll) {
                requestAnimationFrame(scrollNotificationsListToTop);
            }

            if (isManualRefresh && el.refresh) {
                refreshCooldown = true;
                if (typeof window.adminShowRefreshButtonSuccessState === 'function') {
                    window.adminShowRefreshButtonSuccessState(el.refresh, {
                        duration: 3000,
                        onComplete: () => {
                            refreshCooldown = false;
                        },
                    });
                } else {
                    const refreshIcon = el.refresh.querySelector('.refresh-icon');
                    const checkIcon = el.refresh.querySelector('.check-icon');
                    if (refreshIcon) refreshIcon.hidden = true;
                    if (checkIcon) checkIcon.hidden = false;
                    el.refresh.classList.add('is-success');
                    el.refresh.disabled = true;
                    setTimeout(() => {
                        if (refreshIcon) refreshIcon.hidden = false;
                        if (checkIcon) checkIcon.hidden = true;
                        el.refresh.classList.remove('is-success');
                        el.refresh.disabled = false;
                        refreshCooldown = false;
                    }, 3000);
                }
            }
        } catch (error) {
            console.error('Failed to load admin notifications', error);
            refreshCooldown = false;
            if (el.refresh && typeof window.adminResetRefreshButtonState === 'function') {
                window.adminResetRefreshButtonState(el.refresh);
            }
            if (typeof notifyError === 'function') {
                notifyError(t('notif_load_failed', 'Could not load admin notifications.'));
            }
        } finally {
            loading = false;
            if (el.refresh) {
                if (typeof window.adminSetRefreshButtonLoadingState === 'function') {
                    window.adminSetRefreshButtonLoadingState(el.refresh, false);
                } else {
                    el.refresh.classList.remove('is-loading');
                }
            }
            if (el.list) {
                el.list.classList.remove('is-loading');
            }
        }
    };

    el.prev?.addEventListener('click', () => {
        if (!loading && state.page > 1) {
            loadNotifications({ page: state.page - 1, resetScroll: true });
        }
    });

    el.next?.addEventListener('click', () => {
        if (!loading && state.hasNext) {
            loadNotifications({ page: state.page + 1, resetScroll: true });
        }
    });

    el.refresh?.addEventListener('click', (event) => {
        event?.preventDefault?.();
        if (!loading && !refreshCooldown) {
            loadNotifications({ page: 1, isManualRefresh: true });
        }
    });

    el.openButton?.addEventListener('click', (event) => {
        event?.preventDefault?.();
        if (typeof window.activateAdminPage === 'function') {
            window.activateAdminPage('admin-notifications');
        } else {
            window.location.assign('/admin/admin-notifications');
        }
    });

    // Modal functions
    const showDeleteModal = () => {
        if (el.deleteModal) {
            el.deleteModal.hidden = false;
        }
    };

    const hideDeleteModal = () => {
        if (el.deleteModal) {
            el.deleteModal.hidden = true;
        }
    };

    // Delete all notifications
    const deleteAllNotifications = async () => {
        if (loading) return;
        loading = true;

        if (el.confirmDelete) {
            el.confirmDelete.disabled = true;
            el.confirmDelete.innerHTML = `
                ${Icons.refreshSpinning}
                <span>${t('admin_deleting', 'Deleting...')}</span>
            `;
        }

        try {
            const origin = window.location.origin || `${window.location.protocol}//${window.location.host}`;
            const url = new URL('/api/v1/admin/notifications', origin);

            const response = await authedFetch(url.toString(), {
                method: 'DELETE',
            });

            if (response.status === 401 || response.status === 403) {
                if (typeof redirectToLogin === 'function') {
                    redirectToLogin();
                }
                return;
            }

            if (!response.ok) {
                throw new Error(t('notif_delete_failed', 'Failed to delete notifications'));
            }

            const result = await response.json();
            
            // Reset state and reload
            state.page = 1;
            state.total = 0;
            state.hasNext = false;
            
            hideDeleteModal();
            
            if (typeof notifySuccess === 'function') {
                const deletedCount = result.deleted_count || 0;
                notifySuccess(formatT(
                    deletedCount === 1 ? 'notif_delete_success_single' : 'notif_delete_success_plural',
                    `Deleted ${deletedCount} notification${deletedCount === 1 ? '' : 's'}.`,
                    { count: deletedCount }
                ));
            }
            
            // Allow a fresh fetch after delete (loadNotifications guards when loading is true)
            loading = false;
            await loadNotifications({ page: 1 });
        } catch (error) {
            console.error('Failed to delete notifications', error);
            if (typeof notifyError === 'function') {
                notifyError(t('notif_delete_failed_user', 'Could not delete notifications.'));
            }
        } finally {
            loading = false;
            if (el.confirmDelete) {
                el.confirmDelete.disabled = false;
                el.confirmDelete.innerHTML = `
                    ${Icons?.trash || ''}
                    <span>${t('notif_delete_all_btn', 'Delete All')}</span>
                `;
            }
        }
    };

    // Delete button - open modal
    el.deleteBtn?.addEventListener('click', (event) => {
        event?.preventDefault?.();
        if (state.total > 0) {
            showDeleteModal();
        } else {
            if (typeof notifyError === 'function') {
                notifyError(t('notif_delete_none', 'No notifications to delete.'));
            }
        }
    });

    // Cancel delete - close modal
    el.cancelDelete?.addEventListener('click', (event) => {
        event?.preventDefault?.();
        hideDeleteModal();
    });

    // Confirm delete - execute deletion
    el.confirmDelete?.addEventListener('click', (event) => {
        event?.preventDefault?.();
        deleteAllNotifications();
    });

    // Close modal on overlay click
    el.deleteModal?.addEventListener('click', (event) => {
        if (event.target === el.deleteModal) {
            hideDeleteModal();
        }
    });

    // Close modal on Escape key
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && el.deleteModal && !el.deleteModal.hidden) {
            hideDeleteModal();
        }
    });

    const rerenderCurrentNotifications = () => {
        renderList(state.currentItems);
        renderPagination();
    };

    el.typeFilterSelect?.addEventListener('change', () => {
        state.activeTypes = new Set(
            Array.from(el.typeFilterSelect.selectedOptions, (option) => String(option.value || ''))
        );
        applyFilter();
    });

    el.categoryFilterSelect?.addEventListener('change', () => {
        state.activeCategories = new Set(
            Array.from(el.categoryFilterSelect.selectedOptions, (option) => String(option.value || ''))
        );
        applyFilter();
    });

    document.addEventListener('i18n:updated', () => {
        renderTypeFilterControl();
        renderCategoryFilterControl();
        rerenderCurrentNotifications();
    });

    // Export notifications
    const exportNotifications = async () => {
        if (!el.exportBtn || loading) return;

        const btnSpan = el.exportBtn.querySelector('span');
        const originalText = btnSpan?.textContent || t('notif_download_btn', 'Download JSON');

        try {
            if (btnSpan) btnSpan.textContent = t('notif_export_preparing', 'Preparing...');
            el.exportBtn.disabled = true;
            el.exportBtn.classList.add('loading');

            const origin = window.location.origin || `${window.location.protocol}//${window.location.host}`;
            const url = new URL('/api/v1/admin/notifications/export', origin);

            const response = await authedFetch(url.toString(), { method: 'POST' });

            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || t('notif_export_failed', 'Failed to export notifications'));
            }

            const data = await response.json();

            // Create and download the JSON file
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = blobUrl;

            // Generate filename with date
            const now = new Date();
            const dateStr = now.toISOString().split('T')[0];
            a.download = `admin-notifications-${dateStr}.json`;

            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(blobUrl);

            if (typeof notifySuccess === 'function') {
                notifySuccess(formatT(
                    data.total_count === 1 ? 'notif_export_success_single' : 'notif_export_success_plural',
                    `Exported ${data.total_count.toLocaleString()} notification${data.total_count === 1 ? '' : 's'}`,
                    { count: data.total_count.toLocaleString() }
                ));
            }
        } catch (err) {
            console.error('Failed to export notifications:', err);
            if (typeof notifyError === 'function') {
                notifyError(err.message || t('notif_export_failed', 'Failed to export notifications'));
            }
        } finally {
            if (btnSpan) btnSpan.textContent = originalText;
            el.exportBtn.disabled = false;
            el.exportBtn.classList.remove('loading');
        }
    };

    el.exportBtn?.addEventListener('click', (event) => {
        event?.preventDefault?.();
        exportNotifications();
    });

    window.initAdminNotificationsPage = () => {
        attachTruncationObservers();
        renderTypeFilterControl();
        renderCategoryFilterControl();
        loadNotifications({ page: 1 });
        scheduleTruncationCheck();
        notificationSettingsController?.init?.();
    };

    window.teardownAdminNotificationsPage = () => {
        detachTruncationObservers();
        notificationSettingsController?.teardown?.();
    };
})();
