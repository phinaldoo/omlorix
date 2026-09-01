/**
 * Workspace User Notifications Module
 * Handles fetching and rendering user notifications in the workspace
 */

// ============================================================================
// State Management
// ============================================================================

const WorkspaceNotificationsState = {
    notifications: [],
    loading: false,
    initialized: false,
    currentPage: 1,
    pageSize: 20,
    totalPages: 1,
    total: 0,
};

// ============================================================================
// DOM Helpers
// ============================================================================

const WorkspaceNotificationsDOM = {
    get container() { return document.getElementById('workspaceNotificationsContainer'); },
    get list() { return document.getElementById('workspaceNotificationsList'); },
    get loading() { return document.getElementById('workspaceNotificationsLoading'); },
    get empty() { return document.getElementById('workspaceNotificationsEmpty'); },
    get pagination() { return document.getElementById('workspaceNotificationsPagination'); },
    get paginationInfo() { return document.getElementById('workspaceNotificationsPaginationInfo'); },
    get paginationPages() { return document.getElementById('workspaceNotificationsPaginationPages'); },
    get prevBtn() { return document.getElementById('workspaceNotificationsPrevBtn'); },
    get nextBtn() { return document.getElementById('workspaceNotificationsNextBtn'); },
};

// ============================================================================
// API
// ============================================================================

async function fetchUserNotifications(page = 1, pageSize = 20) {
    const url = `/api/v1/user/notifications?page=${page}&page_size=${pageSize}`;
    const requestInit = {
        method: 'GET',
        headers: {
            'Content-Type': 'application/json',
        },
    };

    if (typeof window.authedFetch !== 'function') {
        throw new Error(getWorkspaceNotificationTranslation('workspace_notifications_not_authenticated', 'Not authenticated'));
    }
    const response = await window.authedFetch(url, requestInit);

    if (!response.ok) {
        throw new Error(formatWorkspaceNotificationTranslation('workspace_notifications_fetch_failed_status', 'Failed to fetch notifications: {status}', { status: response.status }));
    }

    return response.json();
}

async function markUserNotificationsSeen() {
    const endpoint = '/api/v1/user/notifications/mark-seen';
    const requestInit = {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
    };

    if (typeof window.authedFetch !== 'function') {
        throw new Error(getWorkspaceNotificationTranslation('workspace_notifications_not_authenticated', 'Not authenticated'));
    }
    const response = await window.authedFetch(endpoint, requestInit);
    if (!response.ok) {
        throw new Error(formatWorkspaceNotificationTranslation('workspace_notifications_mark_seen_failed_status', 'Failed to mark notifications as seen: {status}', { status: response.status }));
    }
}


async function deleteShareInvitationNotification(notificationId) {
    if (!notificationId) {
        throw new Error(getWorkspaceNotificationTranslation('workspace_notifications_id_required', 'Notification ID is required'));
    }

    const endpoint = `/api/v1/user/notifications/share-invitations/${encodeURIComponent(notificationId)}`;
    const requestInit = {
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json',
        },
    };

    if (typeof window.authedFetch !== 'function') {
        throw new Error(getWorkspaceNotificationTranslation('workspace_notifications_not_authenticated', 'Not authenticated'));
    }
    const response = await window.authedFetch(endpoint, requestInit);
    if (!response.ok) {
        throw new Error(getWorkspaceNotificationTranslation('workspace_notifications_remove_invitation_failed', 'Failed to remove share invitation notification'));
    }
}

async function decideCanvasAssetNotification(notification, decision) {
    const details = notification?.details;
    if (!details?.canvas_file_id || !details?.request_id) {
        throw new Error(getWorkspaceNotificationTranslation('workspace_notifications_canvas_asset_invalid', 'This asset request is no longer valid.'));
    }
    const response = await window.authedFetch('/api/v1/files/canvas/assets/decision', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            canvas_file_id: String(details.canvas_file_id),
            request_id: String(details.request_id),
            notification_id: String(notification.id || ''),
            decision,
            scope: details.scope === 'public' ? 'public' : 'canvas_members',
        }),
    });
    if (!response.ok) {
        throw new Error(getWorkspaceNotificationTranslation('workspace_notifications_canvas_asset_decision_failed', 'The asset permission could not be updated.'));
    }
    return response.json();
}


function removeNotificationFromUI(notificationId) {
    if (!notificationId) return;

    WorkspaceNotificationsState.notifications = WorkspaceNotificationsState.notifications.filter(
        (notification) => notification.id !== notificationId
    );
    WorkspaceNotificationsState.total = Math.max(WorkspaceNotificationsState.total - 1, 0);
    WorkspaceNotificationsState.totalPages = WorkspaceNotificationsState.total > 0
        ? Math.ceil(WorkspaceNotificationsState.total / WorkspaceNotificationsState.pageSize)
        : 1;
    WorkspaceNotificationsState.currentPage = Math.min(
        WorkspaceNotificationsState.currentPage,
        WorkspaceNotificationsState.totalPages,
    );

    if (
        WorkspaceNotificationsState.notifications.length === 0 &&
        WorkspaceNotificationsState.total > 0 &&
        !WorkspaceNotificationsState.loading
    ) {
        if (WorkspaceNotificationsState.currentPage > 1) {
            WorkspaceNotificationsState.currentPage = Math.max(
                WorkspaceNotificationsState.currentPage - 1,
                1
            );
        }
        loadWorkspaceNotifications(false);
        return;
    }

    renderNotifications(WorkspaceNotificationsState.notifications);
}

// ============================================================================
// Rendering
// ============================================================================

function getWorkspaceNotificationTranslation(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function formatWorkspaceNotificationTranslation(key, fallback, vars) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }

    return String(fallback).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars?.[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

function getWorkspaceNotificationLocale() {
    const lang = document.documentElement?.getAttribute('lang');
    if (lang) return lang;
    return navigator.language || 'en';
}

function formatWorkspaceLabel(value, fallback = '') {
    const raw = String(value || fallback || '').trim();
    if (!raw) return '';
    return raw
        .replace(/[_-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .replace(/\b\w/g, (char) => char.toUpperCase());
}

function translateNotificationType(type) {
    const normalizedType = normalizeNotificationType(type);
    const labels = {
        info: ['workspace_notifications_type_info', 'Info'],
        warning: ['workspace_notifications_type_warning', 'Warning'],
        error: ['workspace_notifications_type_error', 'Error'],
    };
    const [key, fallback] = labels[normalizedType] || ['workspace_notifications_type_info', formatWorkspaceLabel(normalizedType, 'Info')];
    return getWorkspaceNotificationTranslation(key, fallback);
}

function normalizeNotificationType(type) {
    const normalizedType = String(type || 'info').toLowerCase();
    return ['info', 'warning', 'error'].includes(normalizedType) ? normalizedType : 'info';
}

function translateNotificationCategory(category) {
    const normalizedCategory = String(category || '').toLowerCase();
    const labels = {
        share_invitation: ['workspace_notifications_category_share_invitation', 'Share Invitation'],
        canvas_assets: ['workspace_notifications_category_canvas_assets', 'Canvas files'],
        automations: ['workspace_notifications_category_automations', 'Automations'],
        system: ['workspace_notifications_category_system', 'System'],
    };
    const match = labels[normalizedCategory];
    if (match) {
        return getWorkspaceNotificationTranslation(match[0], match[1]);
    }
    return formatWorkspaceLabel(category, getWorkspaceNotificationTranslation('workspace_notifications_category_default', 'Notification'));
}

function translateShareItemType(itemType) {
    const normalizedType = String(itemType || '').toLowerCase();
    const labels = {
        agent: ['workspace_notifications_item_agent', 'agent'],
        chat: ['workspace_notifications_item_chat', 'chat'],
        file_folder: ['workspace_notifications_item_file_folder', 'file folder'],
        note: ['workspace_notifications_item_note', 'note'],
        project: ['workspace_notifications_item_project', 'project'],
        prompt: ['workspace_notifications_item_prompt', 'prompt'],
        skill: ['workspace_notifications_item_skill', 'skill'],
        todo_list: ['workspace_notifications_item_todo_list', 'to-do list'],
    };
    const match = labels[normalizedType];
    if (match) {
        return getWorkspaceNotificationTranslation(match[0], match[1]);
    }
    return formatWorkspaceLabel(itemType, getWorkspaceNotificationTranslation('workspace_notifications_item_default', 'item')).toLowerCase();
}

function translateShareType(shareType) {
    const normalizedType = String(shareType || 'share').toLowerCase();
    const labels = {
        authenticated: ['workspace_notifications_share_type_authenticated', 'Signed-in chat link'],
        clone: ['workspace_notifications_share_type_clone', 'Clone'],
        collaborate: ['workspace_notifications_share_type_collaborate', 'Collaborate'],
        live: ['workspace_notifications_share_type_live', 'View Only'],
        share: ['workspace_notifications_share_type_share', 'Share'],
    };
    const [key, fallback] = labels[normalizedType] || ['workspace_notifications_share_type_share', formatWorkspaceLabel(normalizedType, 'Share')];
    return getWorkspaceNotificationTranslation(key, fallback);
}

function getShareInvitationMessage(details, fallbackMessage) {
    const inviterName = details.inviter_name || getWorkspaceNotificationTranslation('workspace_notifications_inviter_unknown', 'Someone');
    const itemType = translateShareItemType(details.item_type);
    const itemTitle = details.item_title || getWorkspaceNotificationTranslation('workspace_notifications_item_untitled', 'Untitled item');

    return formatWorkspaceNotificationTranslation(
        'workspace_notifications_invitation_message',
        '{inviter} invited you to {itemType}: {title}',
        {
            inviter: inviterName,
            itemType,
            title: itemTitle || fallbackMessage || '',
        }
    );
}

function getNotificationMessage(notification) {
    const details = notification.details;
    if (details?.type === 'share_invitation') {
        return getShareInvitationMessage(details, notification.message);
    }
    if (details?.type === 'canvas_asset_approval') {
        return formatWorkspaceNotificationTranslation(
            details.scope === 'public'
                ? 'workspace_notifications_canvas_asset_public_request_message'
                : 'workspace_notifications_canvas_asset_request_message',
            details.scope === 'public'
                ? '{requester} wants to make {asset} publicly visible through {canvas}.'
                : '{requester} wants to use {asset} in {canvas}.',
            {
                requester: details.requester_name || getWorkspaceNotificationTranslation('workspace_notifications_inviter_unknown', 'Someone'),
                asset: details.asset_name || getWorkspaceNotificationTranslation('workspace_notifications_canvas_asset_file', 'a file'),
                canvas: details.canvas_title || getWorkspaceNotificationTranslation('workspace_notifications_canvas_asset_canvas', 'a Canvas'),
            },
        );
    }
    if (details?.type === 'canvas_asset_decision') {
        const approved = details.decision === 'approve';
        return formatWorkspaceNotificationTranslation(
            approved
                ? 'workspace_notifications_canvas_asset_approved_message'
                : 'workspace_notifications_canvas_asset_rejected_message',
            approved
                ? '{asset} was approved for {canvas}.'
                : '{asset} was rejected for {canvas}.',
            {
                asset: details.asset_name || getWorkspaceNotificationTranslation('workspace_notifications_canvas_asset_file', 'A file'),
                canvas: details.canvas_title || getWorkspaceNotificationTranslation('workspace_notifications_canvas_asset_canvas', 'a Canvas'),
            },
        );
    }
    return notification.message || '';
}

function getAcceptInvitationButtonHtml(label) {
    return Icons.check;
}

function getAcceptInvitationLoadingHtml(label) {
    return `${Icons.aura} ${escapeHtml(label)}`;
}

function getNotificationIcon(type) {
    const icons = {
        info: Icons.info,
        warning: Icons.warning,
        error: Icons.error,
    };
    return icons[type] || icons.info;
}

const formatWorkspaceRelativeTime = (timestamp) => {
    if (!timestamp) return '';
    
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return '';

    const now = new Date();
    const diffMs = Math.max(0, now - date);
    const diffSeconds = Math.floor(diffMs / 1000);
    const diffMinutes = Math.floor(diffSeconds / 60);
    const diffHours = Math.floor(diffMinutes / 60);
    const diffDays = Math.floor(diffHours / 24);
    const locale = getWorkspaceNotificationLocale();
    const formatter = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });

    if (diffSeconds < 60) {
        return formatter.format(0, 'second');
    } else if (diffMinutes < 60) {
        return formatter.format(-diffMinutes, 'minute');
    } else if (diffHours < 24) {
        return formatter.format(-diffHours, 'hour');
    } else if (diffDays < 7) {
        return formatter.format(-diffDays, 'day');
    }

    return date.toLocaleDateString(locale, { month: 'short', day: 'numeric' });
};

function createNotificationElement(notification) {
    const item = document.createElement('div');
    item.className = 'workspace-notification-item';
    item.setAttribute('data-notification-id', String(notification.id ?? ''));
    const normalizedType = normalizeNotificationType(notification.type);
    item.setAttribute('data-type', normalizedType);

    const typeClass = `type-${normalizedType}`;
    const isShareInvitation = notification.details?.type === 'share_invitation';
    const isCanvasAssetApproval = notification.details?.type === 'canvas_asset_approval';
    const isAutomationNotification = String(notification.category || '').toLowerCase() === 'automations';

    const translatedCategory = translateNotificationCategory(notification.category);
    const translatedType = translateNotificationType(normalizedType);

    const icon = document.createElement('div');
    icon.className = `workspace-notification-icon ${typeClass}`;
    icon.setAttribute('data-type', normalizedType);
    icon.innerHTML = getNotificationIcon(normalizedType);
    item.appendChild(icon);

    const content = document.createElement('div');
    content.className = 'workspace-notification-content';

    const header = document.createElement('div');
    header.className = 'workspace-notification-header';

    const meta = document.createElement('div');
    meta.className = 'workspace-notification-meta';

    const category = document.createElement('span');
    category.className = 'workspace-notification-category';
    category.setAttribute('data-category', String(notification.category ?? ''));
    category.textContent = translatedCategory;
    meta.appendChild(category);

    const typeBadge = document.createElement('span');
    typeBadge.className = `workspace-notification-type-badge ${typeClass}`;
    typeBadge.setAttribute('data-type', normalizedType);
    typeBadge.textContent = translatedType;
    meta.appendChild(typeBadge);

    const time = document.createElement('span');
    time.className = 'workspace-notification-time';
    time.textContent = formatWorkspaceRelativeTime(notification.timestamp);

    header.appendChild(meta);
    header.appendChild(time);
    content.appendChild(header);

    const message = document.createElement('p');
    message.className = 'workspace-notification-message';
    message.textContent = getNotificationMessage(notification);
    content.appendChild(message);

    if (isShareInvitation) {
        const detailWrapper = document.createElement('div');
        detailWrapper.innerHTML = formatShareInvitationDetails(notification.details);
        const detailElement = detailWrapper.firstElementChild;
        if (detailElement) {
            content.appendChild(detailElement);
        }
    } else if (isCanvasAssetApproval || notification.details?.type === 'canvas_asset_decision') {
        // The translated message contains every useful detail. Internal IDs
        // remain hidden and are used only by the action request below.
    } else if (isAutomationNotification) {
        // Successful automation notifications remain message-only. Failed runs
        // expose just the useful error and keep navigation metadata private.
        const automationError = normalizedType === 'error' ? notification.details?.error : null;
        const detailsHtml = automationError ? formatDetails({ error: automationError }) : '';
        if (detailsHtml) {
            const details = document.createElement('p');
            details.className = 'workspace-notification-details';
            details.innerHTML = detailsHtml;
            content.appendChild(details);
        }
    } else if (notification.details) {
        const detailsHtml = formatDetails(notification.details);
        if (detailsHtml) {
            const details = document.createElement('p');
            details.className = 'workspace-notification-details';
            details.innerHTML = detailsHtml;
            content.appendChild(details);
        }
    }

    if (isShareInvitation) {
        const shareId = String(notification.details.share_id ?? '');
        const itemType = String(notification.details.item_type ?? '');
        const shareType = String(notification.details.share_type ?? '');
        const acceptLabel = getWorkspaceNotificationTranslation('workspace_notifications_accept', 'Accept');
        const dismissLabel = getWorkspaceNotificationTranslation('workspace_notifications_dismiss', 'Dismiss');

        const actions = document.createElement('div');
        actions.className = 'workspace-notification-actions';
        actions.setAttribute('data-share-id', shareId);
        actions.setAttribute('data-item-type', itemType);
        actions.setAttribute('data-share-type', shareType);

        const acceptButton = document.createElement('button');
        acceptButton.type = 'button';
        acceptButton.className = 'om-button border submit';
        acceptButton.setAttribute('data-action', 'accept');
        acceptButton.setAttribute('aria-label', acceptLabel);
        acceptButton.innerHTML = getAcceptInvitationButtonHtml(acceptLabel);
        actions.appendChild(acceptButton);

        const dismissButton = document.createElement('button');
        dismissButton.type = 'button';
        dismissButton.className = 'om-button border cancel';
        dismissButton.setAttribute('data-action', 'dismiss');
        dismissButton.setAttribute('aria-label', dismissLabel);
        dismissButton.textContent = dismissLabel;
        actions.appendChild(dismissButton);

        content.appendChild(actions);
    }

    if (isCanvasAssetApproval) {
        const approveLabel = getWorkspaceNotificationTranslation(
            notification.details?.scope === 'public'
                ? 'workspace_notifications_canvas_asset_approve_public'
                : 'workspace_notifications_canvas_asset_approve',
            notification.details?.scope === 'public' ? 'Allow publicly' : 'Allow in Canvas',
        );
        const rejectLabel = getWorkspaceNotificationTranslation('workspace_notifications_canvas_asset_reject', 'Reject');
        const actions = document.createElement('div');
        actions.className = 'workspace-notification-actions';

        const approveButton = document.createElement('button');
        approveButton.type = 'button';
        approveButton.className = 'om-button border submit';
        approveButton.setAttribute('aria-label', approveLabel);
        approveButton.innerHTML = getAcceptInvitationButtonHtml(approveLabel);

        const rejectButton = document.createElement('button');
        rejectButton.type = 'button';
        rejectButton.className = 'om-button border cancel';
        rejectButton.setAttribute('aria-label', rejectLabel);
        rejectButton.textContent = rejectLabel;
        actions.append(approveButton, rejectButton);
        content.appendChild(actions);

        approveButton.addEventListener('click', (event) => {
            event.stopPropagation();
            handleCanvasAssetDecision(notification, item, 'approve');
        });
        rejectButton.addEventListener('click', (event) => {
            event.stopPropagation();
            handleCanvasAssetDecision(notification, item, 'reject');
        });
    }

    item.appendChild(content);

    // Add event listeners for share invitation actions
    if (isShareInvitation) {
        const acceptBtn = item.querySelector('[data-action="accept"]');
        const dismissBtn = item.querySelector('[data-action="dismiss"]');
        
        if (acceptBtn) {
            acceptBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                handleAcceptInvitation(notification, item);
            });
        }
        
        if (dismissBtn) {
            dismissBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                handleDismissInvitation(notification, item);
            });
        }
    }

    return item;
}

async function handleCanvasAssetDecision(notification, itemElement, decision) {
    const buttons = Array.from(itemElement.querySelectorAll('.workspace-notification-actions button'));
    buttons.forEach((button) => { button.disabled = true; });
    try {
        await decideCanvasAssetNotification(notification, decision);
        removeNotificationFromUI(notification.id);
        if (typeof showNotification === 'function') {
            showNotification(
                getWorkspaceNotificationTranslation(
                    decision === 'approve'
                        ? 'workspace_notifications_canvas_asset_approve_success'
                        : 'workspace_notifications_canvas_asset_reject_success',
                    decision === 'approve' ? 'The file is now available in this Canvas.' : 'The file request was rejected.',
                ),
                decision === 'approve' ? 'success' : 'info',
            );
        }
    } catch (error) {
        console.error('Failed to decide Canvas asset permission:', error);
        buttons.forEach((button) => { button.disabled = false; });
        if (typeof showNotification === 'function') {
            showNotification(error?.message || getWorkspaceNotificationTranslation('workspace_notifications_canvas_asset_decision_failed', 'The asset permission could not be updated.'), 'error');
        }
    }
}

function formatShareInvitationDetails(details) {
    const shareType = details.share_type || 'share';
    const inviterName = details.inviter_name || getWorkspaceNotificationTranslation('workspace_notifications_inviter_unknown', 'Someone');
    const shareTypeLabel = translateShareType(shareType);
    const typeLabel = getWorkspaceNotificationTranslation('workspace_notifications_detail_type', 'Type');
    const fromLabel = getWorkspaceNotificationTranslation('workspace_notifications_detail_from', 'From');
    
    return `<p class="workspace-notification-details workspace-notification-invite-details">
        <span class="detail-item"><strong>${escapeHtml(typeLabel)}:</strong> ${escapeHtml(shareTypeLabel)}</span>
        <span class="detail-item"><strong>${escapeHtml(fromLabel)}:</strong> ${escapeHtml(inviterName)}</span>
    </p>`;
}

async function handleAcceptInvitation(notification, itemElement) {
    const details = notification.details;
    if (!details || !details.share_id || !details.item_type) return;
    
    const acceptBtn = itemElement.querySelector('[data-action="accept"]');
    if (acceptBtn) {
        acceptBtn.disabled = true;
        const acceptingLabel = getWorkspaceNotificationTranslation('workspace_notifications_accepting', 'Accepting...');
        acceptBtn.setAttribute('aria-label', acceptingLabel);
        acceptBtn.innerHTML = getAcceptInvitationLoadingHtml(acceptingLabel);
    }
    
    try {
        const shareId = details.share_id;
        const itemType = details.item_type;
        const shareType = details.share_type;
        let endpoint = '';
        if (itemType === 'todo_list') {
            if (shareType === 'clone') {
                endpoint = `/api/v1/todo/clone/${encodeURIComponent(shareId)}`;
            } else {
                endpoint = `/api/v1/todo/shared/${encodeURIComponent(shareId)}/accept`;
            }
        } else if (itemType === 'note') {
            if (shareType === 'clone') {
                endpoint = `/api/v1/notes/clone/${encodeURIComponent(shareId)}`;
            } else {
                endpoint = `/api/v1/notes/shared/${encodeURIComponent(shareId)}/accept`;
            }
        } else if (itemType === 'skill') {
            if (shareType === 'clone') {
                endpoint = `/api/v1/skills/clone/${encodeURIComponent(shareId)}`;
            } else {
                endpoint = `/api/v1/skills/shared/${encodeURIComponent(shareId)}/accept`;
            }
        } else if (itemType === 'file_folder') {
            if (shareType === 'clone') {
                endpoint = `/api/v1/file-folders/clone/${encodeURIComponent(shareId)}`;
            } else {
                endpoint = `/api/v1/file-folders/shared/${encodeURIComponent(shareId)}/accept`;
            }
        } else if (itemType === 'prompt') {
            if (shareType === 'clone') {
                endpoint = `/api/v1/prompts/clone/${encodeURIComponent(shareId)}`;
            } else {
                endpoint = `/api/v1/prompts/shared/${encodeURIComponent(shareId)}/accept`;
            }
        } else if (itemType === 'agent') {
            if (shareType === 'clone') {
                endpoint = `/api/v1/agents/shared/${encodeURIComponent(shareId)}/clone`;
            } else {
                endpoint = `/api/v1/agents/shared/${encodeURIComponent(shareId)}/accept`;
            }
        } else if (itemType === 'project') {
            const shareUrl = details.share_url || `/projects/join/${encodeURIComponent(shareId)}`;
            window.location.href = shareUrl;
            return;
        } else if (itemType === 'chat') {
            await deleteShareInvitationNotification(notification.id);
            removeNotificationFromUI(notification.id);
            const shareUrl = details.share_url || `/chats/shared/${encodeURIComponent(shareId)}`;
            window.location.href = shareUrl;
            return;
        }
        
        if (!endpoint) {
            throw new Error(getWorkspaceNotificationTranslation('workspace_notifications_unknown_item_type', 'Unknown item type'));
        }
        
        const fetchFn = typeof authedFetch === 'function' ? authedFetch : fetch;
        const response = await fetchFn(endpoint, { method: 'POST' });
        
        if (!response.ok) {
            throw new Error(getWorkspaceNotificationTranslation('workspace_notifications_accept_error', 'Failed to accept invitation'));
        }
        
        await deleteShareInvitationNotification(notification.id);
        removeNotificationFromUI(notification.id);

        if (typeof showNotification === 'function') {
            showNotification(getWorkspaceNotificationTranslation('workspace_notifications_accept_success', 'Invitation accepted. The item has been added to your workspace.'), 'success');
        }
        
    } catch (error) {
        console.error('Failed to accept invitation:', error);
        if (acceptBtn) {
            acceptBtn.disabled = false;
            const acceptLabel = getWorkspaceNotificationTranslation('workspace_notifications_accept', 'Accept');
            acceptBtn.setAttribute('aria-label', acceptLabel);
            acceptBtn.innerHTML = getAcceptInvitationButtonHtml(acceptLabel);
        }
        if (typeof showNotification === 'function') {
            showNotification(getWorkspaceNotificationTranslation('workspace_notifications_accept_error', 'Failed to accept invitation'), 'error');
        }
    }
}

async function handleDismissInvitation(notification, itemElement) {
    const dismissBtn = itemElement.querySelector('[data-action="dismiss"]');
    if (dismissBtn) {
        dismissBtn.disabled = true;
        dismissBtn.textContent = getWorkspaceNotificationTranslation('workspace_notifications_removing', 'Removing...');
    }

    try {
        await deleteShareInvitationNotification(notification.id);
        removeNotificationFromUI(notification.id);
        if (typeof showNotification === 'function') {
            showNotification(getWorkspaceNotificationTranslation('workspace_notifications_dismiss_success', 'Invitation dismissed'), 'info');
        }
    } catch (error) {
        console.error('Failed to dismiss invitation:', error);
        if (dismissBtn) {
            dismissBtn.disabled = false;
            dismissBtn.textContent = getWorkspaceNotificationTranslation('workspace_notifications_dismiss', 'Dismiss');
        }
        if (typeof showNotification === 'function') {
            showNotification(getWorkspaceNotificationTranslation('workspace_notifications_dismiss_error', 'Failed to dismiss invitation'), 'error');
        }
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatDetails(details) {
    if (!details || typeof details !== 'object') return '';
    // These identifiers support invitation actions but are implementation
    // metadata and should never be exposed in a generic notification detail row.
    const internalDetailKeys = new Set([
        'item_id', 'share_id', 'inviter_id', 'canvas_file_id', 'asset_file_id',
        'request_id', 'requester_id', 'public_request_id',
    ]);
    const entries = Object.entries(details)
        .filter(([key]) => !internalDetailKeys.has(key))
        .slice(0, 3);
    return entries.map(([key, value]) => {
        const detailLabel = formatWorkspaceLabel(key);
        return `<span class="detail-item"><strong>${escapeHtml(detailLabel)}:</strong> ${escapeHtml(String(value))}</span>`;
    }).join(' · ');
}

function renderNotifications(notifications) {
    const list = WorkspaceNotificationsDOM.list;
    const empty = WorkspaceNotificationsDOM.empty;
    const pagination = WorkspaceNotificationsDOM.pagination;

    if (!list) return;

    list.innerHTML = '';

    if (!notifications || notifications.length === 0) {
        list.style.display = 'none';
        if (empty) empty.style.display = 'flex';
        if (pagination) pagination.style.display = 'none';
        return;
    }

    list.style.display = 'flex';
    if (empty) empty.style.display = 'none';

    const fragment = document.createDocumentFragment();
    notifications.forEach(notification => {
        fragment.appendChild(createNotificationElement(notification));
    });
    list.appendChild(fragment);

    // Update pagination
    updatePagination();
}

function updatePagination() {
    const { currentPage, totalPages, total, pageSize } = WorkspaceNotificationsState;
    const pagination = WorkspaceNotificationsDOM.pagination;
    const paginationInfo = WorkspaceNotificationsDOM.paginationInfo;
    const paginationPages = WorkspaceNotificationsDOM.paginationPages;
    const prevBtn = WorkspaceNotificationsDOM.prevBtn;
    const nextBtn = WorkspaceNotificationsDOM.nextBtn;

    if (!pagination) return;

    if (total === 0) {
        pagination.style.display = 'none';
        return;
    }

    pagination.style.display = 'flex';

    // Update info text
    if (paginationInfo) {
        const start = (currentPage - 1) * pageSize + 1;
        const end = Math.min(currentPage * pageSize, total);
        paginationInfo.textContent = formatWorkspaceNotificationTranslation(
            'workspace_notifications_pagination_info',
            'Showing {start}-{end} of {total}',
            { start, end, total }
        );
    }

    // Update prev/next buttons
    if (prevBtn) {
        prevBtn.disabled = currentPage <= 1;
    }
    if (nextBtn) {
        nextBtn.disabled = currentPage >= totalPages;
    }

    // Render page numbers
    if (paginationPages) {
        paginationPages.innerHTML = '';
        const pages = generatePageNumbers(currentPage, totalPages);
        
        pages.forEach(page => {
            if (page === '...') {
                const ellipsis = document.createElement('span');
                ellipsis.className = 'workspace-notifications-pagination-ellipsis';
                ellipsis.textContent = '...';
                paginationPages.appendChild(ellipsis);
            } else {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'workspace-notifications-pagination-page';
                if (page === currentPage) {
                    btn.classList.add('active');
                    btn.setAttribute('aria-current', 'page');
                }
                btn.textContent = page;
                btn.setAttribute(
                    'aria-label',
                    formatWorkspaceNotificationTranslation(
                        'workspace_notifications_page_aria',
                        'Go to page {page}',
                        { page }
                    )
                );
                btn.addEventListener('click', () => goToPage(page));
                paginationPages.appendChild(btn);
            }
        });
    }
}

function generatePageNumbers(current, total) {
    if (total <= 7) {
        return Array.from({ length: total }, (_, i) => i + 1);
    }

    const pages = [];
    
    if (current <= 4) {
        pages.push(1, 2, 3, 4, 5, '...', total);
    } else if (current >= total - 3) {
        pages.push(1, '...', total - 4, total - 3, total - 2, total - 1, total);
    } else {
        pages.push(1, '...', current - 1, current, current + 1, '...', total);
    }

    return pages;
}

function goToPage(page) {
    if (page < 1 || page > WorkspaceNotificationsState.totalPages) return;
    if (page === WorkspaceNotificationsState.currentPage) return;
    
    WorkspaceNotificationsState.currentPage = page;
    loadWorkspaceNotifications(false);
}

function showLoading(show) {
    const loading = WorkspaceNotificationsDOM.loading;
    const list = WorkspaceNotificationsDOM.list;
    const empty = WorkspaceNotificationsDOM.empty;
    const pagination = WorkspaceNotificationsDOM.pagination;

    if (loading) loading.style.display = show ? 'flex' : 'none';
    if (list && show) list.style.display = 'none';
    if (empty && show) empty.style.display = 'none';
    if (pagination && show) pagination.style.display = 'none';
}

// ============================================================================
// Main Functions
// ============================================================================

async function loadWorkspaceNotifications(showLoadingState = true) {
    if (WorkspaceNotificationsState.loading) return;

    WorkspaceNotificationsState.loading = true;

    if (showLoadingState) {
        showLoading(true);
    }

    try {
        const { currentPage, pageSize } = WorkspaceNotificationsState;
        const response = await fetchUserNotifications(currentPage, pageSize);
        
        WorkspaceNotificationsState.notifications = response.notifications;
        WorkspaceNotificationsState.total = response.total;
        WorkspaceNotificationsState.totalPages = response.total_pages;
        WorkspaceNotificationsState.currentPage = response.page;

        // Finish the loading state before revealing either the list or its empty
        // placeholder. Marking notifications as seen is a separate request, so
        // rendering first would otherwise leave the spinner and empty state visible
        // together until that request completes.
        showLoading(false);
        renderNotifications(response.notifications);

        try {
            await markUserNotificationsSeen();
            clearNotificationBadges();
        } catch (markSeenError) {
            console.error('Failed to mark notifications as seen:', markSeenError);
        }
    } catch (error) {
        console.error('Failed to load notifications:', error);
        WorkspaceNotificationsState.notifications = [];
        WorkspaceNotificationsState.total = 0;
        WorkspaceNotificationsState.totalPages = 1;
        // Keep the error fallback consistent with the successful response path:
        // the empty state must not overlap the loading spinner.
        showLoading(false);
        renderNotifications([]);
    } finally {
        WorkspaceNotificationsState.loading = false;
        showLoading(false);
    }
}

function initWorkspaceNotifications() {
    if (WorkspaceNotificationsState.initialized) return;

    const prevBtn = WorkspaceNotificationsDOM.prevBtn;
    if (prevBtn) {
        prevBtn.addEventListener('click', () => {
            if (WorkspaceNotificationsState.currentPage > 1) {
                WorkspaceNotificationsState.currentPage--;
                loadWorkspaceNotifications(false);
            }
        });
    }

    const nextBtn = WorkspaceNotificationsDOM.nextBtn;
    if (nextBtn) {
        nextBtn.addEventListener('click', () => {
            if (WorkspaceNotificationsState.currentPage < WorkspaceNotificationsState.totalPages) {
                WorkspaceNotificationsState.currentPage++;
                loadWorkspaceNotifications(false);
            }
        });
    }

    WorkspaceNotificationsState.initialized = true;
}

// ============================================================================
// Integration with Workspace Manager
// ============================================================================

const originalSwitchToTab = WorkspaceManager.switchToTab.bind(WorkspaceManager);
WorkspaceManager.switchToTab = function(tabId) {
    originalSwitchToTab(tabId);

    if (tabId === 'notifications') {
        initWorkspaceNotifications();
        // Always load fresh when switching to notifications tab
        if (!WorkspaceNotificationsState.loading) {
            loadWorkspaceNotifications();
        }
    }
};

document.addEventListener('i18n:updated', () => {
    if (!WorkspaceNotificationsState.initialized) return;

    if (WorkspaceNotificationsState.notifications.length > 0) {
        renderNotifications(WorkspaceNotificationsState.notifications);
        return;
    }

    updatePagination();
});

// ============================================================================
// Notification Badge Functions
// ============================================================================

function initNotificationBadge(hasNewNotifications) {
    if (typeof window !== 'undefined') {
        window.hasNewNotifications = hasNewNotifications;
    }
    updateNotificationBadges(hasNewNotifications);
}

function updateNotificationBadges(hasNew) {
    if (typeof window.ChatSidebarMid?.setWorkspaceBadge === 'function') {
        window.ChatSidebarMid.setWorkspaceBadge(hasNew);
    } else {
        // Keep the old direct-DOM path for isolated tests and partial page loads.
        const sidebarWorkspaceBtn = document.getElementById('sidebarWorkspace');
        if (sidebarWorkspaceBtn) {
            let badge = sidebarWorkspaceBtn.querySelector('.notification-badge');
            if (hasNew) {
                if (!badge) {
                    badge = document.createElement('span');
                    badge.className = 'notification-badge';
                    sidebarWorkspaceBtn.style.position = 'relative';
                    sidebarWorkspaceBtn.appendChild(badge);
                }
                badge.style.display = '';
            } else if (badge) {
                badge.style.display = 'none';
            }
        }
    }
    
    // Update workspace notifications navigation badges
    const notificationNavItems = document.querySelectorAll('[data-workspace-tab="notifications"]');
    notificationNavItems.forEach((notificationsTab) => {
        let badge = notificationsTab.querySelector(':scope > .notification-badge');
        if (hasNew) {
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'notification-badge';
                notificationsTab.style.position = 'relative';
                notificationsTab.appendChild(badge);
            }
            badge.style.display = '';
        } else if (badge) {
            badge.style.display = 'none';
        }
    });
}

function clearNotificationBadges() {
    if (typeof window !== 'undefined') {
        window.hasNewNotifications = false;
    }
    updateNotificationBadges(false);
}

// ============================================================================
// Global Exports
// ============================================================================

if (typeof window !== 'undefined') {
    window.loadWorkspaceNotifications = loadWorkspaceNotifications;
    window.initWorkspaceNotifications = initWorkspaceNotifications;
    window.initNotificationBadge = initNotificationBadge;
    window.updateNotificationBadges = updateNotificationBadges;
    window.clearNotificationBadges = clearNotificationBadges;
}
