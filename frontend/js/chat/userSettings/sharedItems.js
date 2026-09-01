(() => {
    'use strict';

    const PAGE_ID = 'sharedItemsPage';
    const CONTAINER_ID = 'sharedItemsContainer';
    const FILTER_ID = 'sharedItemsFilter';
    const SEARCH_ID = 'sharedItemsSearch';
    const EMPTY_ID = 'sharedItemsEmpty';
    const LOADING_ID = 'sharedItemsLoading';
    const COUNT_ID = 'sharedItemsCount';
    const STATUS_ID = 'sharedItemsStatus';
    const VIEW_TABS_ID = 'sharedItemsViewTabs';
    const MANAGE_MODAL_ID = 'sharedItemsManageOverlay';

    const ICON_SVGS = {
        chat: Icons.chatFilesChooseChats,
        artifact: Icons.file,
        project: Icons.folder,
        folder: Icons.folder,
        note: Icons.notes_management,
        todo: Icons.todo_management,
        skill: Icons.skills_management,
        agent: Icons.omlorix,
        prompt: Icons.omlorix,
        attachment: Icons.attachment
    };

    const TYPE_CONFIG = {
        chat: { iconSvg: ICON_SVGS.chat, color: '#3b82f6' },
        artifact: { iconSvg: ICON_SVGS.artifact, color: '#8b5cf6' },
        project: { iconSvg: ICON_SVGS.project, color: '#f59e0b' },
        folder: { iconSvg: ICON_SVGS.folder, color: '#14b8a6' },
        note: { iconSvg: ICON_SVGS.note, color: '#10b981' },
        todo: { iconSvg: ICON_SVGS.todo, color: '#ef4444' },
        skill: { iconSvg: ICON_SVGS.skill, color: '#6366f1' },
        agent: { iconSvg: ICON_SVGS.agent, color: '#0f766e' },
        prompt: { iconSvg: ICON_SVGS.prompt, color: '#ec4899' },
    };

    const DEFAULT_CAPABILITIES = {
        chat: { password: true, expiry: true, share_type: false, rotate_link: false },
        artifact: { password: true, expiry: true, share_type: false, rotate_link: false },
        project: { password: true, expiry: true, share_type: false, rotate_link: true },
        folder: { password: false, expiry: false, share_type: true, rotate_link: false },
        note: { password: false, expiry: false, share_type: true, rotate_link: false },
        todo: { password: false, expiry: false, share_type: true, rotate_link: false },
        skill: { password: false, expiry: false, share_type: true, rotate_link: false },
        agent: { password: false, expiry: false, share_type: true, rotate_link: false },
        prompt: { password: false, expiry: false, share_type: true, rotate_link: false },
    };

    const SHARE_TYPE_OPTIONS = ['clone', 'live', 'collaborate'];

    let allItems = [];
    let currentView = 'outbound';
    let currentFilter = 'all';
    let searchQuery = '';
    let bindingsInitialized = false;
    let boundSharedItemsPage = null;
    let activeManagedItemKey = null;
    let manageModalMode = 'summary';
    let manageModalBusy = false;
    let manageModalLastFocused = null;
    let manageModalBodyHadModalOpen = false;
    let inventoryStatus = 'ok';
    let inventorySectionErrors = [];

    const t = (key, fallback) => {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    const tf = (key, fallback, vars = {}) => {
        if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars[token];
            return value === undefined || value === null ? '' : String(value);
        });
    };

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function notifySuccess(message) {
        if (typeof window.notifySuccess === 'function') {
            window.notifySuccess(message);
        }
    }

    function notifyError(message) {
        if (typeof window.notifyError === 'function') {
            window.notifyError(message);
        }
    }

    async function apiJson(url, options = {}) {
        const response = await window.authedFetch(url, options);
        let payload = null;
        try {
            payload = await response.json();
        } catch (_) {
            payload = null;
        }
        if (!response.ok) {
            throw new Error(payload?.detail || `Request failed (${response.status})`);
        }
        return payload;
    }

    function getItemKey(item) {
        return [item?.type || '', item?.id || '', item?.share_id || ''].join('::');
    }

    function getCapabilities(item) {
        const defaults = DEFAULT_CAPABILITIES[item?.type] || { password: false, expiry: false, share_type: false, rotate_link: false };
        const provided = item?.capabilities && typeof item.capabilities === 'object' ? item.capabilities : {};
        return {
            password: Boolean(provided.password ?? defaults.password),
            expiry: Boolean(provided.expiry ?? defaults.expiry),
            share_type: Boolean(provided.share_type ?? defaults.share_type),
            rotate_link: Boolean(provided.rotate_link ?? defaults.rotate_link),
        };
    }

    function getResourceId(item) {
        return String(item?.resource_id || item?.id || '').trim();
    }

    function getShareTypeMeta(shareType) {
        switch (String(shareType || '').trim()) {
            case 'link':
                return {
                    label: t('us_shared_items_share_type_link', 'Link'),
                    description: t('us_shared_items_share_type_desc_link', 'A standard public share link.'),
                };
            case 'clone':
                return {
                    label: t('us_shared_items_share_type_clone', 'Clone'),
                    description: t('us_shared_items_share_type_desc_clone', 'Recipients get their own copy they can change independently.'),
                };
            case 'live':
                return {
                    label: t('us_shared_items_share_type_live', 'Live view'),
                    description: t('us_shared_items_share_type_desc_live', 'Recipients can see live updates but cannot edit.'),
                };
            case 'collaborate':
                return {
                    label: t('us_shared_items_share_type_collaborate', 'Collaborate'),
                    description: t('us_shared_items_share_type_desc_collaborate', 'Recipients can work against the shared source with live sync.'),
                };
            case 'member':
                return {
                    label: t('us_shared_items_share_type_member', 'Member'),
                    description: t('us_shared_items_share_type_desc_member', 'You joined this shared workspace.'),
                };
            default:
                return {
                    label: shareType ? shareType.charAt(0).toUpperCase() + shareType.slice(1) : t('us_shared_items_share_type_link', 'Link'),
                    description: '',
                };
        }
    }

    function getTypeLabel(type, fallback = 'Item') {
        switch (String(type || '').trim()) {
            case 'chat':
                return t('us_shared_items_type_chat', 'Chat');
            case 'artifact':
                return t('us_shared_items_type_artifact', 'Canvas');
            case 'project':
                return t('us_shared_items_type_project', 'Project');
            case 'folder':
                return t('us_shared_items_type_folder', 'Folder');
            case 'note':
                return t('us_shared_items_type_note', 'Note');
            case 'todo':
                return t('us_shared_items_type_todo', 'Todo');
            case 'skill':
                return t('us_shared_items_type_skill', 'Skill');
            case 'agent':
                return t('us_shared_items_type_agent', 'Agent');
            case 'prompt':
                return t('us_shared_items_type_prompt', 'Prompt');
            default:
                return fallback;
        }
    }

    function formatDate(isoString) {
        if (!isoString) return '—';
        try {
            const d = new Date(isoString);
            if (Number.isNaN(d.getTime())) return '—';
            return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
        } catch {
            return '—';
        }
    }

    function formatDateTime(isoString) {
        if (!isoString) return '—';
        try {
            const d = new Date(isoString);
            if (Number.isNaN(d.getTime())) return '—';
            return d.toLocaleString(undefined, {
                year: 'numeric',
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
            });
        } catch {
            return '—';
        }
    }

    function toIso(localValue) {
        if (!localValue) return null;
        const parsed = new Date(localValue);
        if (Number.isNaN(parsed.getTime())) return null;
        return parsed.toISOString();
    }

    function toLocalDateTimeValue(isoString) {
        if (!isoString) return '';
        const parsed = new Date(isoString);
        if (Number.isNaN(parsed.getTime())) return '';
        const local = new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000);
        return local.toISOString().slice(0, 16);
    }

    function getFilteredItems() {
        let items = allItems.filter((item) => (item.direction || 'outbound') === currentView);
        if (currentFilter !== 'all') {
            items = items.filter((item) => item.type === currentFilter);
        }
        if (searchQuery) {
            const query = searchQuery.toLowerCase();
            items = items.filter((item) => {
                return [item.title, item.type, item.share_type, item.share_url, item.owner_name]
                    .some((value) => String(value || '').toLowerCase().includes(query));
            });
        }
        return items;
    }

    function getItemsForCurrentView() {
        return allItems.filter((item) => (item.direction || 'outbound') === currentView);
    }

    function updateInventoryStatus(payload = null) {
        inventoryStatus = payload?.status === 'degraded' ? 'degraded' : 'ok';
        inventorySectionErrors = Array.isArray(payload?.section_errors)
            ? payload.section_errors.filter((entry) => entry && typeof entry.section === 'string')
            : [];
    }

    function renderInventoryStatus() {
        const el = document.getElementById(STATUS_ID);
        if (!el) return;

        if (inventoryStatus !== 'degraded' || inventorySectionErrors.length === 0) {
            el.hidden = true;
            el.innerHTML = '';
            return;
        }

        const uniqueSections = [...new Set(inventorySectionErrors.map((entry) => entry.section))];
        const sectionPills = uniqueSections.map((section) => {
            return `<span class="si-status-pill">${escapeHtml(getTypeLabel(section, section || t('us_shared_items_item_fallback', 'Item')))}</span>`;
        }).join('');

        el.hidden = false;
        el.innerHTML = `
            <div class="si-status-icon" aria-hidden="true">
               ${Icons.warning}
            </div>
            <div class="si-status-content">
                <div class="si-status-title">${escapeHtml(t('us_shared_items_notice_degraded_title', 'Shared inventory may be incomplete'))}</div>
                <div class="si-status-text">${escapeHtml(t('us_shared_items_notice_degraded_body', 'Some share sections could not be loaded right now. The list below may be incomplete until you refresh.'))}</div>
                <div class="si-status-sections" aria-label="${escapeHtml(t('us_shared_items_notice_affected', 'Affected sections'))}">
                    ${sectionPills}
                </div>
            </div>
        `;
    }

    function updateCount(count) {
        const el = document.getElementById(COUNT_ID);
        if (el) {
            el.textContent = count === 1
                ? t('us_shared_items_count_one', '1 shared item')
                : tf('us_shared_items_count_other', '{count} shared items', { count });
        }
    }

    function updateViewTabs() {
        const tabsEl = document.getElementById(VIEW_TABS_ID);
        if (!tabsEl) return;

        const counts = {
            outbound: allItems.filter((item) => (item.direction || 'outbound') === 'outbound').length,
            inbound: allItems.filter((item) => item.direction === 'inbound').length,
        };

        tabsEl.querySelectorAll('.si-view-tab').forEach((tab) => {
            const view = tab.dataset.view;
            const isActive = view === currentView;
            tab.classList.toggle('active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
            const countEl = tab.querySelector('.si-view-count');
            if (countEl) {
                countEl.textContent = String(counts[view] || 0);
            }
        });
    }

    function itemHasManageActions(item) {
        const capabilities = getCapabilities(item);
        return capabilities.password || capabilities.expiry || capabilities.share_type || capabilities.rotate_link;
    }

    function itemHasAnyAdvancedState(item) {
        const capabilities = getCapabilities(item);
        return capabilities.password || capabilities.expiry || capabilities.share_type || capabilities.rotate_link;
    }

    function getCapabilityBadges(item) {
        const capabilities = getCapabilities(item);
        const badges = [];
        if (capabilities.password) badges.push(t('us_shared_items_badge_password', 'Password'));
        if (capabilities.expiry) badges.push(t('us_shared_items_badge_expiry', 'Expiry'));
        if (capabilities.share_type) badges.push(t('us_shared_items_badge_mode', 'Mode'));
        if (capabilities.rotate_link) badges.push(t('us_shared_items_badge_rotate', 'Rotate'));
        return badges;
    }

    function renderItems() {
        const container = document.getElementById(CONTAINER_ID);
        const emptyEl = document.getElementById(EMPTY_ID);
        const loadingEl = document.getElementById(LOADING_ID);
        const toolbarEl = document.querySelector('#sharedItemsPage .si-toolbar');
        if (!container) return;

        if (loadingEl) loadingEl.style.display = 'none';
        updateViewTabs();
        renderInventoryStatus();

        if (toolbarEl) toolbarEl.hidden = false;

        const items = getFilteredItems();
        updateCount(items.length);

        if (items.length === 0) {
            container.innerHTML = '';
            if (emptyEl) {
                emptyEl.style.display = 'flex';
                const emptyText = emptyEl.querySelector('p');
                if (emptyText) {
                    emptyText.textContent = inventoryStatus === 'degraded' && !searchQuery
                        ? t('us_shared_items_empty_degraded', 'Shared items could not be fully loaded. Active shares may be missing from this list.')
                        : (searchQuery || currentFilter !== 'all'
                            ? t('us_shared_items_empty_filtered', 'No shared items match your filters.')
                            : (currentView === 'inbound'
                                ? t('us_shared_items_empty_inbound', 'Nothing has been shared with you yet.')
                                : t('us_shared_items_empty_default', 'You have not shared anything yet.')));
                }
            }
            return;
        }

        if (emptyEl) emptyEl.style.display = 'none';

        container.innerHTML = items.map((item) => {
            const cfg = TYPE_CONFIG[item.type] || { iconSvg: ICON_SVGS.attachment, color: '#6b7280' };
            const typeLabel = getTypeLabel(item.type, item.type || t('us_shared_items_item_fallback', 'Item'));
            const shareType = getShareTypeMeta(item.share_type || 'link');
            const isInbound = item.direction === 'inbound';
            const capabilityBadges = getCapabilityBadges(item).map((badge) => (
                `<span class="si-item-badge si-item-badge-outline si-capability-badge">${escapeHtml(badge)}</span>`
            )).join('');
            const manageTitle = itemHasManageActions(item) ? t('us_shared_items_action_manage', 'Manage share settings') : '';
            const rowTitleParts = [
                tf('us_shared_items_row_title_link', '{shareType} link', { shareType: shareType.label }),
            ];
            if (item.has_password) {
                rowTitleParts.push(t('us_shared_items_row_title_password', 'password protected'));
            }
            if (item.expires_at) {
                rowTitleParts.push(tf('us_shared_items_row_title_expires', 'expires {date}', { date: formatDate(item.expires_at) }));
            }
            const rowTitle = rowTitleParts.join(', ');
            const lastAccessBadge = item.last_accessed_at
                ? `<span class="si-item-badge si-item-badge-outline" title="${escapeHtml(tf('us_shared_items_title_last_accessed_at', 'Last accessed {datetime}', { datetime: formatDateTime(item.last_accessed_at) }))}">${escapeHtml(tf('us_shared_items_badge_accessed', 'Accessed {date}', { date: formatDate(item.last_accessed_at) }))}</span>`
                : '';
            const ownerBadge = isInbound && item.owner_name
                ? `<span class="si-item-badge si-item-badge-outline">${escapeHtml(tf('us_shared_items_badge_owner', 'From {owner}', { owner: item.owner_name }))}</span>`
                : '';
            const accessBadge = isInbound
                ? `<span class="si-item-badge si-item-badge-outline">${escapeHtml(item.share_type === 'collaborate' || item.share_type === 'member' ? t('us_shared_items_badge_can_edit', 'Can edit') : t('us_shared_items_badge_view_only', 'View only'))}</span>`
                : '';

            return `
                <div class="si-item" data-item-key="${escapeHtml(getItemKey(item))}" title="${escapeHtml(rowTitle)}">
                    <div class="si-item-icon" style="background: ${cfg.color}15; color: ${cfg.color}">
                        ${cfg.iconSvg || ''}
                    </div>
                    <div class="si-item-info">
                        <div class="si-item-title">${escapeHtml(item.title || t('us_shared_items_item_untitled', 'Untitled'))}</div>
                        <div class="si-item-meta">
                            <span class="si-item-badge" style="background: ${cfg.color}18; color: ${cfg.color}">${escapeHtml(typeLabel)}</span>
                            <span class="si-item-badge si-item-badge-outline">${escapeHtml(shareType.label)}</span>
                            ${item.has_password ? `<span class="si-item-badge si-item-badge-outline" title="${escapeHtml(t('us_shared_items_password_protected', 'Password protected'))}">${escapeHtml(t('us_shared_items_badge_protected', 'Protected'))}</span>` : ''}
                            ${item.expires_at ? `<span class="si-item-badge si-item-badge-outline" title="${escapeHtml(tf('us_shared_items_title_expires_at', 'Expires {datetime}', { datetime: formatDateTime(item.expires_at) }))}">${escapeHtml(t('us_shared_items_badge_expires', 'Expires'))} ${escapeHtml(formatDate(item.expires_at))}</span>` : ''}
                            ${ownerBadge}
                            ${accessBadge}
                            ${lastAccessBadge}
                            ${isInbound ? '' : capabilityBadges}
                            <span class="si-item-date">${formatDate(item.created_at)}</span>
                        </div>
                    </div>
                    <div class="si-item-actions">
                        ${item.share_url ? `<button class="si-action-btn si-open-btn" title="${escapeHtml(t('us_shared_items_action_open_link', 'Open shared link'))}" data-url="${escapeHtml(item.share_url)}">
                            ${Icons.open_window}
                        </button>` : ''}
                        ${item.share_url && !isInbound ? `<button class="si-action-btn si-copy-btn" title="${escapeHtml(t('us_shared_items_action_copy_link', 'Copy share link'))}" data-url="${escapeHtml(item.share_url)}">
                            ${Icons.copy}
                        </button>` : ''}
                        ${!isInbound && itemHasManageActions(item) ? `<button class="si-action-btn si-manage-btn" title="${escapeHtml(manageTitle)}" data-item-key="${escapeHtml(getItemKey(item))}">
                            ${Icons.settings}
                        </button>` : ''}
                        ${!isInbound ? `<button class="si-action-btn si-unshare-btn" title="${escapeHtml(t('us_shared_items_action_unshare', 'Unshare'))}" data-item-key="${escapeHtml(getItemKey(item))}">
                            ${Icons.trash}
                        </button>` : ''}
                    </div>
                </div>
            `;
        }).join('');

        bindRenderedActions(container);
    }

    function bindRenderedActions(container) {
        container.querySelectorAll('.si-copy-btn').forEach((btn) => {
            btn.addEventListener('click', async (event) => {
                event.stopPropagation();
                const url = btn.dataset.url;
                try {
                    await navigator.clipboard.writeText(url);
                    notifySuccess(t('us_shared_items_success_link_copied', 'Link copied'));
                } catch (_) {
                    notifyError(t('us_shared_items_error_copy_link', 'Failed to copy link'));
                }
            });
        });

        container.querySelectorAll('.si-open-btn').forEach((btn) => {
            btn.addEventListener('click', (event) => {
                event.stopPropagation();
                const url = btn.dataset.url;
                if (!url) return;
                window.open(url, '_blank', 'noopener');
            });
        });

        container.querySelectorAll('.si-manage-btn').forEach((btn) => {
            btn.addEventListener('click', (event) => {
                event.stopPropagation();
                const item = getItemByKey(btn.dataset.itemKey);
                if (!item) {
                    notifyError(t('us_shared_items_error_not_found', 'Shared item not found'));
                    return;
                }
                openManageModal(item);
            });
        });

        container.querySelectorAll('.si-unshare-btn').forEach((btn) => {
            btn.addEventListener('click', async (event) => {
                event.stopPropagation();
                const item = getItemByKey(btn.dataset.itemKey);
                if (!item) {
                    notifyError(t('us_shared_items_error_not_found', 'Shared item not found'));
                    return;
                }
                await unshareItem(item, btn.closest('.si-item'));
            });
        });
    }

    function getItemByKey(itemKey) {
        return allItems.find((item) => getItemKey(item) === itemKey) || null;
    }

    function updateFilterCounts() {
        const filterEl = document.getElementById(FILTER_ID);
        if (!filterEl) return;
        const scopedItems = getItemsForCurrentView();
        filterEl.querySelectorAll('.si-filter-btn').forEach((btn) => {
            const type = btn.dataset.filter;
            const count = type === 'all'
                ? scopedItems.length
                : scopedItems.filter((item) => item.type === type).length;
            const countEl = btn.querySelector('.si-filter-count');
            if (countEl) countEl.textContent = count;
            if (type !== 'all') {
                btn.style.display = count === 0 ? 'none' : '';
            }
        });
    }

    function getUnshareEndpoint(item) {
        const id = getResourceId(item);
        const endpoints = {
            chat: { url: '/api/v1/chats/share/delete', body: { chat_id: id } },
            artifact: { url: '/api/v1/files/canvas/share/delete', body: { share_id: item.share_id } },
            project: { url: '/api/v1/projects/share/link/delete', body: { project_id: id } },
            note: { url: '/api/v1/notes/share/delete', body: { note_id: id, share_type: item.share_type || null } },
            todo: { url: '/api/v1/todo/lists/share/delete', body: { todo_list_id: id, share_type: item.share_type || null } },
            skill: { url: '/api/v1/skills/share/delete', body: { skill_id: id, share_type: item.share_type || null } },
            agent: { url: '/api/v1/agents/share/delete', body: { agent_id: id, share_type: item.share_type || null } },
            prompt: { url: '/api/v1/prompts/share/delete', body: { prompt_id: id, share_type: item.share_type || null } },
            folder: { url: '/api/v1/file-folders/share/delete', body: { folder_id: id, share_type: item.share_type || null } },
        };
        return endpoints[item.type] || null;
    }

    async function unshareItem(item, element = null) {
        const endpoint = getUnshareEndpoint(item);
        if (!endpoint) {
            notifyError(t('us_shared_items_error_unsupported_type', 'Unsupported item type'));
            return;
        }

        const label = getTypeLabel(item.type, item.type || t('us_shared_items_item_fallback', 'item'));
        if (!await window.showDeleteConfirm({
            title: t('us_shared_items_manage_stop_title', 'Stop sharing'),
            message: tf('us_shared_items_confirm_unshare', 'Stop sharing this {itemType}? The current link will stop working immediately.', { itemType: label.toLowerCase() }),
            confirmLabel: t('us_shared_items_manage_unshare_now', 'Unshare now'),
        })) {
            return;
        }

        try {
            await apiJson(endpoint.url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(endpoint.body),
            });

            if (element) {
                element.style.transition = 'opacity 0.22s ease, transform 0.22s ease';
                element.style.opacity = '0';
                element.style.transform = 'translateX(12px)';
                await new Promise((resolve) => setTimeout(resolve, 220));
            }

            allItems = allItems.filter((candidate) => getItemKey(candidate) !== getItemKey(item));
            renderItems();
            updateFilterCounts();

            if (activeManagedItemKey === getItemKey(item)) {
                closeManageModal();
            }

            notifySuccess(tf('us_shared_items_success_unshared', '{itemType} unshared', { itemType: label }));
        } catch (error) {
            notifyError(error.message || t('us_shared_items_error_unshare', 'Failed to unshare item'));
        }
    }

    async function loadSharedItems(options = {}) {
        const silent = options.silent === true;
        const loadingEl = document.getElementById(LOADING_ID);
        const container = document.getElementById(CONTAINER_ID);
        if (!silent) {
            if (loadingEl) loadingEl.style.display = 'flex';
            if (container) container.innerHTML = '';
        }

        try {
            const data = await apiJson('/api/v1/users/shared-items', { method: 'GET' });
            updateInventoryStatus(data);
            allItems = Array.isArray(data.items) ? data.items : [];
            renderItems();
            updateFilterCounts();
            return allItems;
        } catch (error) {
            updateInventoryStatus();
            if (!silent && loadingEl) loadingEl.style.display = 'none';
            notifyError(error.message || t('us_shared_items_error_load', 'Failed to load shared items'));
            return [];
        }
    }

    function bindFilters() {
        const filterEl = document.getElementById(FILTER_ID);
        if (!filterEl) return;
        filterEl.addEventListener('click', (event) => {
            const btn = event.target.closest('.si-filter-btn');
            if (!btn) return;
            currentFilter = btn.dataset.filter;
            filterEl.querySelectorAll('.si-filter-btn').forEach((node) => node.classList.remove('active'));
            btn.classList.add('active');
            renderItems();
        });
    }

    function bindViewTabs() {
        const tabsEl = document.getElementById(VIEW_TABS_ID);
        if (!tabsEl) return;
        tabsEl.addEventListener('click', (event) => {
            const tab = event.target.closest('.si-view-tab');
            if (!tab) return;
            currentView = tab.dataset.view || 'outbound';
            currentFilter = 'all';
            const filterEl = document.getElementById(FILTER_ID);
            if (filterEl) {
                filterEl.querySelectorAll('.si-filter-btn').forEach((btn) => {
                    btn.classList.toggle('active', btn.dataset.filter === 'all');
                });
            }
            renderItems();
            updateFilterCounts();
        });
    }

    function bindSearch() {
        const searchEl = document.getElementById(SEARCH_ID);
        if (!searchEl) return;
        searchEl.addEventListener('input', () => {
            searchQuery = searchEl.value.trim();
            renderItems();
        });
    }

    function ensureManageModal() {
        let overlay = document.getElementById(MANAGE_MODAL_ID);
        if (overlay) return overlay;

        overlay = document.createElement('div');
        overlay.id = MANAGE_MODAL_ID;
        /*
         * Reuse the chat-share shell instead of maintaining a second modal
         * design. Resource-specific controls are rendered inside the same
         * header/body/footer structure used by ChatShareModal.
         */
        overlay.className = 'cs-overlay shared-modal-overlay si-manage-overlay';
        overlay.hidden = true;
        overlay.setAttribute('aria-hidden', 'true');
        overlay.inert = true;
        overlay.innerHTML = `
            <div class="cs-modal shared-modal shared-modal--fit si-manage-modal" role="dialog" aria-modal="true" aria-labelledby="sharedItemsManageTitle" tabindex="-1">
                <header class="cs-header shared-modal-header shared-modal-header--main">
                    <div class="cs-header-text shared-modal-heading">
                        <h3 class="cs-title shared-modal-title" id="sharedItemsManageTitle"></h3>
                        <p class="cs-subtitle shared-modal-subtitle" id="sharedItemsManageSubtitle"></p>
                    </div>
                    <button type="button" class="cs-icon-btn shared-modal-close" data-close-manage-modal aria-label="${escapeHtml(t('us_shared_items_aria_close_manager', 'Close share manager'))}">
                        ${Icons.close}
                    </button>
                </header>
                <div class="cs-body shared-modal-body" id="sharedItemsManageBody"></div>
                <footer class="cs-footer shared-modal-footer">
                    <button type="button" class="cs-btn cs-btn-ghost om-button border cancel" id="sharedItemsManageSecondary"></button>
                    <button type="button" class="cs-btn cs-btn-primary om-button border submit" id="sharedItemsManagePrimary"></button>
                </footer>
            </div>
        `;

        overlay.addEventListener('click', (event) => {
            if (event.target === overlay || event.target.closest('[data-close-manage-modal]')) {
                closeManageModal();
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !overlay.hidden) {
                event.stopPropagation();
                closeManageModal();
                return;
            }
            if (event.key === 'Tab' && !overlay.hidden) {
                const focusable = [...overlay.querySelectorAll(
                    'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
                )].filter((element) => element.offsetParent !== null);
                if (focusable.length === 0) return;
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            }
        });

        overlay.querySelector('#sharedItemsManageSecondary')?.addEventListener('click', () => {
            if (manageModalMode === 'edit') {
                manageModalMode = 'summary';
                const current = getManagedItem();
                if (current) renderManageModal(current);
                return;
            }
            closeManageModal();
        });

        overlay.querySelector('#sharedItemsManagePrimary')?.addEventListener('click', () => {
            if (manageModalMode !== 'edit') return;
            const current = getManagedItem();
            if (current) void saveManagedSettings(current);
        });

        document.body.appendChild(overlay);
        return overlay;
    }

    /**
     * Disable every modal control while a share mutation is running.
     *
     * Keeping busy state on the shared `.cs-modal` class also gives this modal
     * the same cursor and pointer behavior as the chat-share modal.
     */
    function setManageModalBusy(busy) {
        manageModalBusy = Boolean(busy);
        const overlay = document.getElementById(MANAGE_MODAL_ID);
        const modal = overlay?.querySelector('.cs-modal');
        modal?.classList.toggle('cs-busy', manageModalBusy);
        overlay?.querySelectorAll('button, input, select').forEach((control) => {
            control.disabled = manageModalBusy;
        });
    }

    function closeManageModal() {
        const overlay = document.getElementById(MANAGE_MODAL_ID);
        if (!overlay) return;
        overlay.classList.remove('cs-active');

        /*
         * Clear focus inside the dialog before applying aria-hidden, then
         * restore it to the originating control. A refresh can replace that
         * row, so fall back to the matching action in the newly rendered row.
         */
        const refreshedTrigger = activeManagedItemKey
            ? document.querySelector(`.si-item[data-item-key="${CSS.escape(activeManagedItemKey)}"] .si-manage-btn`)
            : null;
        const focusTarget = manageModalLastFocused?.isConnected ? manageModalLastFocused : refreshedTrigger;
        if (overlay.contains(document.activeElement)) {
            document.activeElement?.blur?.();
        }
        overlay.inert = true;
        overlay.setAttribute('aria-hidden', 'true');
        if (focusTarget && typeof focusTarget.focus === 'function') {
            try {
                focusTarget.focus();
            } catch (_) {
                // The originating row may have disappeared after unsharing.
            }
        }

        setTimeout(() => {
            if (!overlay.classList.contains('cs-active')) {
                overlay.hidden = true;
                if (!manageModalBodyHadModalOpen) document.body.classList.remove('modal-open');
                manageModalBodyHadModalOpen = false;
            }
        }, 180);
        manageModalLastFocused = null;
        manageModalMode = 'summary';
        manageModalBusy = false;
        activeManagedItemKey = null;
    }

    function openManageModal(item) {
        const overlay = ensureManageModal();
        manageModalLastFocused = document.activeElement;
        activeManagedItemKey = getItemKey(item);
        manageModalMode = 'summary';
        renderManageModal(item);
        if (overlay.hidden) {
            manageModalBodyHadModalOpen = document.body.classList.contains('modal-open');
        }
        overlay.hidden = false;
        overlay.inert = false;
        overlay.setAttribute('aria-hidden', 'false');
        document.body.classList.add('modal-open');
        requestAnimationFrame(() => overlay.classList.add('cs-active'));
        setTimeout(() => {
            overlay.querySelector('button:not([disabled]), input:not([disabled])')?.focus();
        }, 80);
    }

    function getManagedItem() {
        return activeManagedItemKey ? getItemByKey(activeManagedItemKey) : null;
    }

    function renderManageModal(item) {
        const overlay = ensureManageModal();
        const titleEl = overlay.querySelector('#sharedItemsManageTitle');
        const subtitleEl = overlay.querySelector('#sharedItemsManageSubtitle');
        const bodyEl = overlay.querySelector('#sharedItemsManageBody');
        const secondaryBtn = overlay.querySelector('#sharedItemsManageSecondary');
        const primaryBtn = overlay.querySelector('#sharedItemsManagePrimary');
        if (!titleEl || !subtitleEl || !bodyEl || !secondaryBtn || !primaryBtn || !item) return;

        const typeLabel = getTypeLabel(item.type, item.type || t('us_shared_items_item_fallback', 'Item'));
        const shareType = getShareTypeMeta(item.share_type || 'link');
        titleEl.textContent = t('us_shared_items_action_manage', 'Manage share settings');
        subtitleEl.textContent = `${item.title || t('us_shared_items_item_untitled', 'Untitled')} • ${typeLabel}`;

        if (manageModalMode === 'edit') {
            bodyEl.innerHTML = renderManageEditForm(item);
            secondaryBtn.textContent = t('chat_share_cancel', 'Cancel');
            primaryBtn.textContent = t('chat_share_save_changes', 'Save changes');
            primaryBtn.style.display = '';
        } else {
            bodyEl.innerHTML = renderManageSummary(item, shareType);
            secondaryBtn.textContent = t('chat_share_done', 'Done');
            primaryBtn.textContent = '';
            primaryBtn.style.display = 'none';
        }

        bindManageModalActions(item);
        setManageModalBusy(manageModalBusy);
    }

    /**
     * Render the active share in the same card layout used by ChatShareModal.
     */
    function renderManageSummary(item, shareType) {
        const capabilities = getCapabilities(item);
        const accessCount = Math.max(0, Number.parseInt(item.access_count ?? 0, 10) || 0);
        const passwordChip = item.has_password
            ? `<span class="cs-chip">${Icons.lock}${escapeHtml(t('chat_share_chip_password', 'Password'))}</span>`
            : '';
        const expiryChip = item.expires_at
            ? `<span class="cs-chip">${Icons.clock}${escapeHtml(t('chat_share_chip_expires', 'Expires'))} ${escapeHtml(formatDateTime(item.expires_at))}</span>`
            : '';
        const createdChip = item.created_at
            ? `<span class="cs-chip cs-chip-muted">${escapeHtml(t('chat_share_chip_created', 'Created'))} ${escapeHtml(formatDateTime(item.created_at))}</span>`
            : '';
        const telemetryChips = item.type === 'artifact'
            ? `
                <span class="cs-chip cs-chip-muted">${escapeHtml(t('us_shared_items_meta_access_count', 'Accesses'))}: ${escapeHtml(String(accessCount))}</span>
                <span class="cs-chip cs-chip-muted">${escapeHtml(t('us_shared_items_meta_last_accessed', 'Last accessed'))}: ${item.last_accessed_at ? escapeHtml(formatDateTime(item.last_accessed_at)) : escapeHtml(t('us_shared_items_never_accessed', 'Never'))}</span>
            `
            : '';

        return `
            <section class="cs-section">
                <div class="cs-section-head">
                    <span class="cs-section-label">${escapeHtml(t('chat_share_active_link_label', 'Active link'))}</span>
                </div>
                <div class="cs-link-list">
                    <div class="cs-link-card">
                        <div class="cs-link-url-row">
                            <input type="text" class="cs-link-url" value="${escapeHtml(item.share_url || '')}" readonly aria-label="${escapeHtml(t('chat_share_link_aria', 'Share link'))}">
                        </div>
                        <div class="cs-link-meta">
                            <span class="cs-chip">${Icons.layers}${escapeHtml(shareType.label)}</span>
                            ${passwordChip}
                            ${expiryChip}
                            ${createdChip}
                            ${telemetryChips}
                        </div>
                        <div class="cs-link-actions">
                            ${item.share_url ? `<button type="button" class="om-button border cancel" data-manage-action="copy-link">${Icons.copy}${escapeHtml(t('chat_share_copy', 'Copy'))}</button>` : ''}
                            ${item.share_url ? `<button type="button" class="om-button border cancel" data-manage-action="open-link">${Icons.open_window}${escapeHtml(t('chat_share_open', 'Open'))}</button>` : ''}
                            ${itemHasManageActions(item) ? `<button type="button" class="om-button border cancel" data-manage-action="edit">${Icons.create}${escapeHtml(t('chat_share_edit', 'Edit'))}</button>` : ''}
                            ${capabilities.rotate_link ? `<button type="button" class="om-button border cancel" data-manage-action="rotate-link">${Icons.refresh}${escapeHtml(t('us_shared_items_manage_rotate_action', 'Rotate link'))}</button>` : ''}
                            <button type="button" class="om-button border danger-nofill" data-manage-action="unshare">${Icons.trash}${escapeHtml(t('chat_share_delete', 'Delete'))}</button>
                        </div>
                    </div>
                </div>
            </section>
            <div class="cs-notice" id="siManageNotice" aria-hidden="true" aria-live="polite" role="status"></div>
        `;
    }

    /**
     * Render only controls supported by the selected resource type.
     *
     * Chat, canvas, and project links expose password/expiry controls. The
     * clone/live/collaborate resources expose a mode radio group instead.
     */
    function renderManageEditForm(item) {
        const capabilities = getCapabilities(item);
        const passwordHelp = item.type === 'artifact'
            ? t('us_shared_items_manage_password_help_artifact', 'Protect this canvas link with a password.')
            : (item.type === 'project'
                ? t('us_shared_items_manage_password_help_project', 'Require a password before someone can join this shared project.')
                : t('us_shared_items_manage_password_help_chat', 'Require a password before the shared chat can be opened.'));
        const modeOptions = SHARE_TYPE_OPTIONS.map((value) => {
            const meta = getShareTypeMeta(value);
            return `
                <label class="cs-radio">
                    <input type="radio" name="siManageShareType" value="${value}" ${item.share_type === value ? 'checked' : ''}>
                    <span class="cs-radio-content">
                        <span class="cs-radio-title">${escapeHtml(meta.label)}</span>
                        <span class="cs-radio-desc">${escapeHtml(meta.description)}</span>
                    </span>
                </label>
            `;
        }).join('');

        return `
            <section class="cs-form">
                <div class="cs-section-head">
                    <span class="cs-section-label">${escapeHtml(t('chat_share_edit_link', 'Edit link'))}</span>
                </div>
                ${capabilities.share_type ? `
                    <div class="cs-field">
                        <div class="cs-field-label">${escapeHtml(t('us_shared_items_manage_link_mode_title', 'Link mode'))}</div>
                        <div class="cs-radio-group" role="radiogroup" aria-label="${escapeHtml(t('us_shared_items_manage_link_mode_title', 'Link mode'))}">
                            ${modeOptions}
                        </div>
                        <p class="cs-helper">${escapeHtml(t('us_shared_items_manage_link_mode_help', 'Change what the link does for recipients. The current link will be replaced with the selected mode.'))}</p>
                    </div>
                ` : ''}
                ${capabilities.password ? `
                    <div class="cs-field">
                        <div class="cs-toggle-row">
                            <div class="cs-toggle-info">
                                <span class="cs-toggle-label">${escapeHtml(t('us_shared_items_manage_password_title', 'Password protection'))}</span>
                                <span class="cs-toggle-desc">${escapeHtml(passwordHelp)}</span>
                            </div>
                            <label class="cs-switch">
                                <input type="checkbox" id="siManagePasswordToggle" ${item.has_password ? 'checked' : ''} aria-label="${escapeHtml(t('us_shared_items_manage_password_title', 'Password protection'))}">
                                <span class="cs-switch-slider"></span>
                            </label>
                        </div>
                        <div class="cs-toggle-content" id="siManagePasswordContent" ${item.has_password ? '' : 'hidden'}>
                            <input class="cs-input" id="siManagePasswordInput" type="password" placeholder="${escapeHtml(item.has_password ? t('us_shared_items_manage_password_placeholder_new', 'Set a new password') : t('us_shared_items_manage_password_placeholder_set', 'Set a password'))}" autocomplete="new-password" aria-describedby="siManagePasswordHelper siManagePasswordError" aria-invalid="false">
                            <p class="cs-helper" id="siManagePasswordHelper">${escapeHtml(item.has_password ? t('chat_share_password_keep_help', 'Leave blank to keep the current password.') : passwordHelp)}</p>
                            <p class="cs-field-error" id="siManagePasswordError" role="alert" hidden></p>
                        </div>
                    </div>
                ` : ''}
                ${capabilities.expiry ? `
                    <div class="cs-field">
                        <div class="cs-toggle-row">
                            <div class="cs-toggle-info">
                                <span class="cs-toggle-label">${escapeHtml(t('chat_share_expiry_toggle_label', 'Expiration'))}</span>
                                <span class="cs-toggle-desc">${escapeHtml(t('chat_share_expiry_toggle_desc', 'Disable the link automatically at a date and time'))}</span>
                            </div>
                            <label class="cs-switch">
                                <input type="checkbox" id="siManageExpiryToggle" ${item.expires_at ? 'checked' : ''} aria-label="${escapeHtml(t('chat_share_expiry_toggle_label', 'Expiration'))}">
                                <span class="cs-switch-slider"></span>
                            </label>
                        </div>
                        <div class="cs-toggle-content" id="siManageExpiryContent" ${item.expires_at ? '' : 'hidden'}>
                            <input class="cs-input" id="siManageExpiryInput" type="datetime-local" value="${escapeHtml(toLocalDateTimeValue(item.expires_at))}" aria-describedby="siManageExpiryError" aria-invalid="false">
                            <p class="cs-field-error" id="siManageExpiryError" role="alert" hidden></p>
                        </div>
                    </div>
                ` : ''}
            </section>
            <div class="cs-notice" id="siManageNotice" aria-hidden="true" aria-live="polite" role="status"></div>
        `;
    }

    function bindManageModalActions(item) {
        const overlay = ensureManageModal();
        overlay.querySelectorAll('[data-manage-action]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const current = getManagedItem();
                if (!current || getItemKey(current) !== getItemKey(item)) {
                    notifyError(t('us_shared_items_error_settings_stale', 'Share settings are out of date.'));
                    return;
                }
                await handleManageAction(current, btn.dataset.manageAction);
            });
        });

        const passwordToggle = overlay.querySelector('#siManagePasswordToggle');
        const passwordContent = overlay.querySelector('#siManagePasswordContent');
        const passwordInput = overlay.querySelector('#siManagePasswordInput');
        passwordToggle?.addEventListener('change', () => {
            if (passwordContent) passwordContent.hidden = !passwordToggle.checked;
            clearManageFieldError(passwordInput, overlay.querySelector('#siManagePasswordError'));
            if (passwordToggle.checked) {
                setTimeout(() => passwordInput?.focus(), 50);
            }
        });
        passwordInput?.addEventListener('input', () => {
            const value = String(passwordInput.value || '').trim();
            if (value && value.length < 8) {
                showManageFieldError(passwordInput, overlay.querySelector('#siManagePasswordError'), t('chat_share_password_min_error', 'Password must be at least 8 characters long.'));
            } else {
                clearManageFieldError(passwordInput, overlay.querySelector('#siManagePasswordError'));
            }
        });

        const expiryToggle = overlay.querySelector('#siManageExpiryToggle');
        const expiryContent = overlay.querySelector('#siManageExpiryContent');
        const expiryInput = overlay.querySelector('#siManageExpiryInput');
        expiryToggle?.addEventListener('change', () => {
            if (expiryContent) expiryContent.hidden = !expiryToggle.checked;
            clearManageFieldError(expiryInput, overlay.querySelector('#siManageExpiryError'));
            if (expiryToggle.checked) {
                if (expiryInput && !expiryInput.value) {
                    const future = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
                    expiryInput.value = toLocalDateTimeValue(future.toISOString());
                }
                setTimeout(() => expiryInput?.focus(), 50);
            }
        });
        expiryInput?.addEventListener('input', () => {
            clearManageFieldError(expiryInput, overlay.querySelector('#siManageExpiryError'));
        });
    }

    function showManageNotice(message, type = 'error') {
        const notice = document.getElementById(MANAGE_MODAL_ID)?.querySelector('#siManageNotice');
        if (!notice) return;
        if (!message) {
            notice.textContent = '';
            notice.className = 'cs-notice';
            notice.setAttribute('aria-hidden', 'true');
            return;
        }
        notice.textContent = message;
        notice.className = `cs-notice cs-notice-${type}`;
        notice.setAttribute('aria-hidden', 'false');
    }

    function showManageFieldError(input, errorEl, message) {
        if (!input || !errorEl) return;
        errorEl.textContent = message || '';
        errorEl.hidden = false;
        input.classList.add('cs-input-error');
        input.setAttribute('aria-invalid', 'true');
    }

    function clearManageFieldError(input, errorEl) {
        if (!input || !errorEl) return;
        errorEl.textContent = '';
        errorEl.hidden = true;
        input.classList.remove('cs-input-error');
        input.setAttribute('aria-invalid', 'false');
    }

    /**
     * Build a password mutation without changing the modal or refreshing data.
     * This lets the shared Save button apply password and expiry changes once,
     * then refresh the inventory a single time.
     */
    function getManagedPasswordRequest(item, operation, password = '') {
        const resourceId = getResourceId(item);
        const definitions = {
            chat: {
                create: { url: '/api/v1/chats/share/password/create', body: { chat_id: resourceId, password } },
                change: { url: '/api/v1/chats/share/password/change', body: { chat_id: resourceId, password } },
                remove: { url: '/api/v1/chats/share/password/remove', body: { chat_id: resourceId } },
            },
            artifact: {
                create: { url: '/api/v1/files/canvas/share/password/change', body: { share_id: item.share_id, password } },
                change: { url: '/api/v1/files/canvas/share/password/change', body: { share_id: item.share_id, password } },
                remove: { url: '/api/v1/files/canvas/share/password/remove', body: { share_id: item.share_id } },
            },
            project: {
                create: { url: '/api/v1/projects/share/link', body: { project_id: resourceId, password } },
                change: { url: '/api/v1/projects/share/link', body: { project_id: resourceId, password } },
                remove: { url: '/api/v1/projects/share/link', body: { project_id: resourceId, password: '' } },
            },
        };
        return definitions[item.type]?.[operation] || null;
    }

    /**
     * Build the resource-specific expiry request used by the unified form.
     */
    function getManagedExpiryRequest(item, operation, expiresAt = null) {
        const resourceId = getResourceId(item);
        const definitions = {
            chat: {
                create: { url: '/api/v1/chats/share/expiry/create', body: { chat_id: resourceId, expires_at: expiresAt } },
                change: { url: '/api/v1/chats/share/expiry/change', body: { chat_id: resourceId, expires_at: expiresAt } },
                remove: { url: '/api/v1/chats/share/expiry/delete', body: { chat_id: resourceId } },
            },
            artifact: {
                create: { url: '/api/v1/files/canvas/share/expiry/change', body: { share_id: item.share_id, expires_at: expiresAt } },
                change: { url: '/api/v1/files/canvas/share/expiry/change', body: { share_id: item.share_id, expires_at: expiresAt } },
                remove: { url: '/api/v1/files/canvas/share/expiry/remove', body: { share_id: item.share_id } },
            },
            project: {
                create: { url: '/api/v1/projects/share/link', body: { project_id: resourceId, expires_at: expiresAt } },
                change: { url: '/api/v1/projects/share/link', body: { project_id: resourceId, expires_at: expiresAt } },
                remove: { url: '/api/v1/projects/share/link', body: { project_id: resourceId, expires_at: null } },
            },
        };
        return definitions[item.type]?.[operation] || null;
    }

    async function runManagedRequest(request) {
        if (!request) return;
        await apiJson(request.url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(request.body),
        });
    }

    /**
     * Save the current resource's supported settings as one form submission.
     *
     * This mirrors ChatShareModal: toggles control whether password/expiry are
     * enabled, blank passwords preserve an existing password, and the modal
     * returns to its active-link summary after a successful update.
     */
    async function saveManagedSettings(item) {
        if (manageModalBusy) return;

        const overlay = ensureManageModal();
        const capabilities = getCapabilities(item);
        const passwordToggle = overlay.querySelector('#siManagePasswordToggle');
        const passwordInput = overlay.querySelector('#siManagePasswordInput');
        const passwordError = overlay.querySelector('#siManagePasswordError');
        const expiryToggle = overlay.querySelector('#siManageExpiryToggle');
        const expiryInput = overlay.querySelector('#siManageExpiryInput');
        const expiryError = overlay.querySelector('#siManageExpiryError');
        const selectedShareType = overlay.querySelector('input[name="siManageShareType"]:checked')?.value || '';

        clearManageFieldError(passwordInput, passwordError);
        clearManageFieldError(expiryInput, expiryError);
        showManageNotice('');

        if (capabilities.share_type && selectedShareType && selectedShareType !== item.share_type) {
            setManageModalBusy(true);
            try {
                await switchManagedShareType(item, selectedShareType);
            } finally {
                setManageModalBusy(false);
            }
            return;
        }

        const requests = [];

        if (capabilities.password) {
            const wantsPassword = Boolean(passwordToggle?.checked);
            const password = String(passwordInput?.value || '').trim();
            if (wantsPassword && !item.has_password && !password) {
                const message = t('us_shared_items_error_password_required', 'Enter a password first');
                showManageFieldError(passwordInput, passwordError, message);
                passwordInput?.focus();
                return;
            }
            if (wantsPassword && password && password.length < 8) {
                const message = t('chat_share_password_min_error', 'Password must be at least 8 characters long.');
                showManageFieldError(passwordInput, passwordError, message);
                passwordInput?.focus();
                return;
            }
            if (wantsPassword && password) {
                requests.push(getManagedPasswordRequest(item, item.has_password ? 'change' : 'create', password));
            } else if (!wantsPassword && item.has_password) {
                requests.push(getManagedPasswordRequest(item, 'remove'));
            }
        }

        if (capabilities.expiry) {
            const wantsExpiry = Boolean(expiryToggle?.checked);
            const expiresAt = wantsExpiry ? toIso(expiryInput?.value || '') : null;
            if (wantsExpiry && !expiresAt) {
                const message = t('chat_share_expiry_required_error', 'Please pick an expiration date and time.');
                showManageFieldError(expiryInput, expiryError, message);
                expiryInput?.focus();
                return;
            }
            if (wantsExpiry && new Date(expiresAt).getTime() <= Date.now()) {
                const message = t('chat_share_expiry_future_error', 'Expiration must be in the future.');
                showManageFieldError(expiryInput, expiryError, message);
                expiryInput?.focus();
                return;
            }

            const previousExpiryMs = item.expires_at ? new Date(item.expires_at).getTime() : null;
            const nextExpiryMs = expiresAt ? new Date(expiresAt).getTime() : null;
            if (wantsExpiry && nextExpiryMs !== previousExpiryMs) {
                requests.push(getManagedExpiryRequest(item, item.expires_at ? 'change' : 'create', expiresAt));
            } else if (!wantsExpiry && item.expires_at) {
                requests.push(getManagedExpiryRequest(item, 'remove'));
            }
        }

        const validRequests = requests.filter(Boolean);
        if (validRequests.length === 0) {
            notifySuccess(t('chat_share_no_changes_notice', 'No changes to save'));
            manageModalMode = 'summary';
            renderManageModal(item);
            return;
        }

        setManageModalBusy(true);
        try {
            for (const request of validRequests) {
                await runManagedRequest(request);
            }
            notifySuccess(t('chat_share_updated_notice', 'Share link updated'));
            manageModalMode = 'summary';
            await refreshManagedItem((candidate) => getItemKey(candidate) === getItemKey(item));
        } catch (error) {
            const message = error.message || t('us_shared_items_error_settings_stale', 'Share settings are out of date.');
            // Earlier requests in this sequential batch may already have
            // succeeded. Reconcile the modal with the backend before showing
            // the original mutation error.
            try {
                await refreshManagedItem((candidate) => getItemKey(candidate) === getItemKey(item));
            } catch (refreshError) {
                console.warn('Failed to refresh shared item after a partial settings update:', refreshError);
            }
            notifyError(message);
            showManageNotice(message, 'error');
        } finally {
            setManageModalBusy(false);
        }
    }

    async function handleManageAction(item, action) {
        switch (action) {
        case 'edit':
            manageModalMode = 'edit';
            renderManageModal(item);
            setTimeout(() => {
                ensureManageModal().querySelector('#sharedItemsManageBody input:not([disabled])')?.focus();
            }, 0);
            return;
        case 'copy-link':
            try {
                await navigator.clipboard.writeText(item.share_url || '');
                notifySuccess(t('us_shared_items_success_link_copied', 'Link copied'));
            } catch (_) {
                notifyError(t('us_shared_items_error_copy_link', 'Failed to copy link'));
            }
            return;
        case 'open-link':
            if (item.share_url) {
                window.open(item.share_url, '_blank', 'noopener');
            }
            return;
        case 'rotate-link':
            await rotateManagedProjectLink(item);
            return;
        case 'unshare':
            await unshareManagedItem(item);
            return;
        default:
            return;
        }
    }

    async function refreshManagedItem(locator) {
        await loadSharedItems({ silent: true });
        const nextItem = typeof locator === 'function' ? allItems.find(locator) : null;
        if (nextItem) {
            activeManagedItemKey = getItemKey(nextItem);
            renderManageModal(nextItem);
            return nextItem;
        }
        closeManageModal();
        return null;
    }

    function getShareTypeMutationEndpoints(item) {
        const resourceId = getResourceId(item);
        const definitions = {
            note: {
                createUrl: '/api/v1/notes/share',
                createBody: (shareType) => ({ note_id: resourceId, share_type: shareType }),
                deleteUrl: '/api/v1/notes/share/delete',
                deleteBody: (shareType) => ({ note_id: resourceId, share_type: shareType }),
            },
            todo: {
                createUrl: '/api/v1/todo/lists/share',
                createBody: (shareType) => ({ todo_list_id: resourceId, share_type: shareType }),
                deleteUrl: '/api/v1/todo/lists/share/delete',
                deleteBody: (shareType) => ({ todo_list_id: resourceId, share_type: shareType }),
            },
            skill: {
                createUrl: '/api/v1/skills/share',
                createBody: (shareType) => ({ skill_id: resourceId, share_type: shareType }),
                deleteUrl: '/api/v1/skills/share/delete',
                deleteBody: (shareType) => ({ skill_id: resourceId, share_type: shareType }),
            },
            agent: {
                createUrl: '/api/v1/agents/share',
                createBody: (shareType) => ({ agent_id: resourceId, share_type: shareType }),
                deleteUrl: '/api/v1/agents/share/delete',
                deleteBody: (shareType) => ({ agent_id: resourceId, share_type: shareType }),
            },
            prompt: {
                createUrl: '/api/v1/prompts/share',
                createBody: (shareType) => ({ prompt_id: resourceId, share_type: shareType }),
                deleteUrl: '/api/v1/prompts/share/delete',
                deleteBody: (shareType) => ({ prompt_id: resourceId, share_type: shareType }),
            },
            folder: {
                createUrl: '/api/v1/file-folders/share',
                createBody: (shareType) => ({ folder_id: resourceId, share_type: shareType }),
                deleteUrl: '/api/v1/file-folders/share/delete',
                deleteBody: (shareType) => ({ folder_id: resourceId, share_type: shareType }),
            },
        };
        return definitions[item.type] || null;
    }

    async function switchManagedShareType(item, selectedShareType = '') {
        const overlay = ensureManageModal();
        const checkedValue = overlay.querySelector('input[name="siManageShareType"]:checked')?.value || '';
        const nextShareType = String(selectedShareType || checkedValue).trim();
        if (!nextShareType || nextShareType === item.share_type) {
            notifyError(t('us_shared_items_error_share_mode_same', 'Select a different share mode first'));
            return false;
        }

        const endpoints = getShareTypeMutationEndpoints(item);
        if (!endpoints) {
            notifyError(t('us_shared_items_error_share_mode_unsupported', 'This share cannot change modes'));
            return false;
        }

        const previousShareType = item.share_type;
        try {
            // Delete first: these endpoints are keyed by resource + share type,
            // so cleanup after create could remove a pre-existing target share.
            await apiJson(endpoints.deleteUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(endpoints.deleteBody(previousShareType)),
            });
        } catch (error) {
            notifyError(error.message || t('us_shared_items_error_share_mode_change', 'Failed to change share mode'));
            return false;
        }

        try {
            const createdShare = await apiJson(endpoints.createUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(endpoints.createBody(nextShareType)),
            });

            if (!createdShare?.share_id) {
                throw new Error(t('us_shared_items_error_share_mode_missing_id', 'Share mode change did not return a share ID'));
            }
        } catch (error) {
            try {
                await apiJson(endpoints.createUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(endpoints.createBody(previousShareType)),
                });
                await refreshManagedItem((candidate) => {
                    return candidate.type === item.type && candidate.id === item.id && candidate.share_type === previousShareType;
                });
            } catch (rollbackError) {
                notifyError(rollbackError.message || error.message || t('us_shared_items_error_share_mode_change', 'Failed to change share mode'));
                return false;
            }
            notifyError(error.message || t('us_shared_items_error_share_mode_change', 'Failed to change share mode'));
            return false;
        }

        notifySuccess(tf('us_shared_items_success_share_mode_changed', 'Share mode changed to {mode}', {
            mode: getShareTypeMeta(nextShareType).label,
        }));
        try {
            manageModalMode = 'summary';
            await refreshManagedItem((candidate) => {
                return candidate.type === item.type && candidate.id === item.id && candidate.share_type === nextShareType;
            });
        } catch (refreshError) {
            console.error('[sharedItems] Failed to refresh share after mode change', refreshError);
        }
        return true;
    }

    async function rotateManagedProjectLink(item) {
        if (manageModalBusy) return;
        if (!await window.showDeleteConfirm({
            title: t('us_shared_items_manage_rotate_title', 'Rotate link'),
            message: t('us_shared_items_confirm_rotate_link', 'Rotate this project link? The current link will stop working immediately.'),
            confirmLabel: t('us_shared_items_manage_rotate_action', 'Rotate link'),
        })) {
            return;
        }

        const resourceId = getResourceId(item);
        setManageModalBusy(true);
        try {
            const createdShare = await apiJson('/api/v1/projects/share/link', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ project_id: resourceId, rotate: true }),
            });
            if (!createdShare?.share_id) {
                throw new Error(t('us_shared_items_error_rotate_create', 'Failed to create a new project link'));
            }
        } catch (error) {
            notifyError(error.message || t('us_shared_items_error_rotate', 'Failed to rotate link'));
            return;
        } finally {
            setManageModalBusy(false);
        }

        try {
            await refreshManagedItem((candidate) => candidate.type === 'project' && candidate.id === item.id);
        } catch (refreshError) {
            console.error('[sharedItems] Failed to refresh project after link rotation', refreshError);
        }
        notifySuccess(t('us_shared_items_success_rotate', 'Project link rotated'));
    }

    async function unshareManagedItem(item) {
        if (manageModalBusy) return;
        const element = document.querySelector(`.si-item[data-item-key="${CSS.escape(getItemKey(item))}"]`);
        setManageModalBusy(true);
        try {
            await unshareItem(item, element);
        } finally {
            setManageModalBusy(false);
        }
    }

    function bindGlobalEvents() {
        const page = document.getElementById(PAGE_ID);
        if (!page) {
            bindingsInitialized = false;
            boundSharedItemsPage = null;
            return;
        }
        if (bindingsInitialized && boundSharedItemsPage === page) return;

        bindingsInitialized = false;
        boundSharedItemsPage = page;
        bindingsInitialized = true;
        bindViewTabs();
        bindFilters();
        bindSearch();
        ensureManageModal();
    }

    window.SharedItemsSettings = {
        load() {
            if (!document.getElementById(PAGE_ID)) return;
            bindGlobalEvents();
            void loadSharedItems();
        },
        refresh() {
            if (!document.getElementById(PAGE_ID)) return;
            bindGlobalEvents();
            void loadSharedItems();
        },
    };
})();
