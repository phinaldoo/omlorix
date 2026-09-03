// Elements
const historySection = document.getElementById('history');
const chatsContainer = document.getElementById('chatsContainer');
const pinnedSection = document.getElementById('pinnedChats');
const pinnedContainer = document.getElementById('pinnedChatsContainer')

const CHAT_LIST_CACHE_KEY = 'omlorix.sidebarChats.v1';
const CHAT_LIST_CACHE_LIMIT = 20;
var chatTitleUtils = window.ChatTitleUtils || {};

function sidebarChatT(key, fallback) {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function getUntitledChatTitle() {
    return sidebarChatT('sidebar_untitled_chat', 'Untitled chat');
}

function getSidebarLocale() {
    const lang = document.documentElement?.getAttribute('lang');
    return lang || undefined;
}


// =============================================
// Lazy Loading State
// =============================================
const lazyLoadState = {
    offset: 0,
    limit: 20,
    hasMore: true,
    isLoading: false,
    totalUnpinned: 0,
    projectId: null,          // Current project filter
    loadedChatIds: new Set(), // Track already-loaded chat IDs to prevent duplicates
};

function parsePinnedPosition(value) {
    if (value === null || typeof value === 'undefined' || value === '') {
        return null;
    }
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
}

function normalizeCachedChat(chat) {
    if (!chat || typeof chat !== 'object' || chat.id === null || typeof chat.id === 'undefined') {
        return null;
    }

    const source = chatTitleUtils.isAutomationChat?.(chat)
        ? 'automation'
        : String(chat.source ?? chat?.meta?.source ?? '').trim().toLowerCase();
    return {
        id: String(chat.id),
        title: chatTitleUtils.getChatDisplayTitle?.(chat, getUntitledChatTitle()) || getUntitledChatTitle(),
        last_updated_at: typeof chat.last_updated_at === 'string' ? chat.last_updated_at : new Date(0).toISOString(),
        pinned_position: parsePinnedPosition(chat.pinned_position),
        project_id: chat.project_id ?? null,
        preview: typeof chat.preview === 'string' ? chat.preview : '',
        message_count: Number.isFinite(Number(chat.message_count)) ? Number(chat.message_count) : 0,
        estimated_chars: Number.isFinite(Number(chat.estimated_chars)) ? Number(chat.estimated_chars) : 0,
        has_unread_response: chat.has_unread_response === true,
        source,
    };
}

function sortChatsForSidebarSnapshot(chats) {
    return [...chats].sort((a, b) => {
        const aPinned = a.pinned_position !== null;
        const bPinned = b.pinned_position !== null;
        if (aPinned && bPinned) {
            return a.pinned_position - b.pinned_position;
        }
        if (aPinned) return -1;
        if (bPinned) return 1;
        return new Date(b.last_updated_at) - new Date(a.last_updated_at);
    });
}

function trimChatsForCache(chats) {
    return sortChatsForSidebarSnapshot(chats).slice(0, CHAT_LIST_CACHE_LIMIT);
}

function readCachedChatList() {
    try {
        const raw = localStorage.getItem(CHAT_LIST_CACHE_KEY);
        if (!raw) return [];

        const parsed = JSON.parse(raw);
        const chats = Array.isArray(parsed?.chats) ? parsed.chats : [];
        return trimChatsForCache(chats.map(normalizeCachedChat).filter(Boolean));
    } catch (error) {
        console.error('Failed to read cached chat list:', error);
        return [];
    }
}

function writeCachedChatList(chats) {
    try {
        const normalized = trimChatsForCache((Array.isArray(chats) ? chats : []).map(normalizeCachedChat).filter(Boolean));
        if (!normalized.length) {
            localStorage.removeItem(CHAT_LIST_CACHE_KEY);
            return;
        }
        localStorage.setItem(CHAT_LIST_CACHE_KEY, JSON.stringify({
            saved_at: Date.now(),
            chats: normalized,
        }));
    } catch (error) {
        console.error('Failed to write cached chat list:', error);
    }
}

function updateCachedChatListEntry(chatUpdate) {
    const normalized = normalizeCachedChat(chatUpdate);
    if (!normalized) return;

    const patch = { id: normalized.id };
    if (Object.prototype.hasOwnProperty.call(chatUpdate, 'title')) {
        patch.title = normalized.title;
    }
    if (Object.prototype.hasOwnProperty.call(chatUpdate, 'last_updated_at')) {
        patch.last_updated_at = normalized.last_updated_at;
    }
    if (Object.prototype.hasOwnProperty.call(chatUpdate, 'pinned_position')) {
        patch.pinned_position = normalized.pinned_position;
    }
    if (Object.prototype.hasOwnProperty.call(chatUpdate, 'project_id')) {
        patch.project_id = normalized.project_id;
    }
    if (Object.prototype.hasOwnProperty.call(chatUpdate, 'preview')) {
        patch.preview = normalized.preview;
    }
    if (Object.prototype.hasOwnProperty.call(chatUpdate, 'message_count')) {
        patch.message_count = normalized.message_count;
    }
    if (Object.prototype.hasOwnProperty.call(chatUpdate, 'estimated_chars')) {
        patch.estimated_chars = normalized.estimated_chars;
    }
    if (Object.prototype.hasOwnProperty.call(chatUpdate, 'has_unread_response')) {
        patch.has_unread_response = normalized.has_unread_response;
    }
    if (Object.prototype.hasOwnProperty.call(chatUpdate, 'source') || Object.prototype.hasOwnProperty.call(chatUpdate, 'meta')) {
        patch.source = normalized.source;
    }

    const cachedChats = readCachedChatList();
    const nextChats = [];
    let hasMatch = false;

    cachedChats.forEach((chat) => {
        if (String(chat.id) !== normalized.id) {
            nextChats.push(chat);
            return;
        }

        hasMatch = true;
        nextChats.push({
            ...chat,
            ...patch,
            title: patch.title ?? chat.title,
            last_updated_at: patch.last_updated_at ?? chat.last_updated_at,
        });
    });

    if (!hasMatch) {
        nextChats.push(normalized);
    }

    writeCachedChatList(nextChats);
}

async function hydrateChatListFromCache() {
    const cachedChats = readCachedChatList();
    if (!cachedChats.length) {
        return false;
    }

    try {
        await renderChatSidebarList(cachedChats);
    } catch (error) {
        console.error('Failed to render cached chat list:', error);
        return false;
    }
    return true;
}

const CHAT_REFERENCE_DRAG_WINDOW_STATE_KEY = '__omlorixActiveChatReferenceDragPayload';

function buildSidebarChatReferenceDragPayload(chat, title) {
    const chatId = String(chat?.id ?? chat?.chat_id ?? '').trim();
    if (!chatId) {
        return null;
    }
    const normalizedTitle = String(title ?? chat?.title ?? getUntitledChatTitle()).trim() || getUntitledChatTitle();
    const snippet = String(chat?.preview ?? chat?.description ?? '').trim();
    return {
        chat_id: chatId,
        title: normalizedTitle,
        last_updated_at: chat?.last_updated_at || null,
        snippet,
        message_count: Number(chat?.message_count || 0) || 0,
        estimated_chars: Number(chat?.estimated_chars || 0) || 0,
    };
}

function setActiveSidebarChatReferenceDragPayload(payload) {
    if (typeof window === 'undefined') {
        return;
    }
    window[CHAT_REFERENCE_DRAG_WINDOW_STATE_KEY] = payload || null;
}

function clearActiveSidebarChatReferenceDragPayload(chatId) {
    if (typeof window === 'undefined') {
        return;
    }
    const currentPayload = window[CHAT_REFERENCE_DRAG_WINDOW_STATE_KEY];
    if (!chatId) {
        window[CHAT_REFERENCE_DRAG_WINDOW_STATE_KEY] = null;
        return;
    }
    const normalizedId = String(chatId).trim();
    const currentId = String(currentPayload?.chat_id || '').trim();
    if (!currentId || currentId === normalizedId) {
        window[CHAT_REFERENCE_DRAG_WINDOW_STATE_KEY] = null;
    }
}

// Loading skeleton element for infinite scroll
function createLoadingSkeleton() {
    const skeleton = document.createElement('div');
    skeleton.className = 'sidebar-element sidebar-element-loading';
    skeleton.innerHTML = `
        <div class="sidebar-element-button">
            <div class="skeleton-text"></div>
        </div>
    `;
    return skeleton;
}

// Sentinel element for IntersectionObserver
let loadingSentinel = null;
let infiniteScrollObserver = null;

function createLoadingSentinel() {
    if (loadingSentinel) return loadingSentinel;
    loadingSentinel = document.createElement('div');
    loadingSentinel.className = 'chats-loading-sentinel';
    loadingSentinel.style.height = '1px';
    loadingSentinel.style.width = '100%';
    return loadingSentinel;
}

function setupInfiniteScroll() {
    if (infiniteScrollObserver) {
        infiniteScrollObserver.disconnect();
    }

    const sentinel = createLoadingSentinel();
    const scrollHost = document.querySelector('.sidebar-main');

    infiniteScrollObserver = new IntersectionObserver((entries) => {
        const entry = entries[0];
        if (entry.isIntersecting && lazyLoadState.hasMore && !lazyLoadState.isLoading) {
            loadMoreChats();
        }
    }, {
        root: scrollHost || null,
        rootMargin: '100px', // Start loading before sentinel is visible
        threshold: 0
    });

    // Append sentinel to container and observe
    if (!chatsContainer.contains(sentinel)) {
        chatsContainer.appendChild(sentinel);
    }
    infiniteScrollObserver.observe(sentinel);
}

function resetLazyLoadState(projectId = null) {
    lazyLoadState.offset = 0;
    lazyLoadState.hasMore = true;
    lazyLoadState.isLoading = false;
    lazyLoadState.totalUnpinned = 0;
    lazyLoadState.projectId = projectId;
    lazyLoadState.loadedChatIds.clear();
}


function findCachedChatById(chatId) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId) {
        return null;
    }

    return readCachedChatList().find((chat) => String(chat.id) === normalizedChatId) || null;
}

async function fetchChatDetailForProjectRestore(chatId) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId || typeof window.authedFetch !== 'function') {
        return null;
    }

    try {
        const response = await window.authedFetch(`/api/v1/chats/detail?chat_id=${encodeURIComponent(normalizedChatId)}`, {
            method: 'GET',
        });
        if (!response.ok) {
            return null;
        }

        const chat = await response.json().catch(() => null);
        const resolvedChatId = String(chat?.id || '').trim();
        if (!resolvedChatId) {
            return null;
        }

        updateCachedChatListEntry(chat);
        return chat;
    } catch (error) {
        console.warn('Failed to fetch chat detail for project sidebar restore', error);
        return null;
    }
}

function queuePendingProjectSidebarRestore(chatId, projectId) {
    if (typeof window === 'undefined') {
        return;
    }

    window.__pendingProjectSidebarRestore = {
        chatId: String(chatId || '').trim() || null,
        projectId: String(projectId || '').trim() || null,
    };
}

function applyProjectSidebarStateForResolvedChat(chat) {
    const normalizedChatId = String(chat?.id || '').trim();
    if (!normalizedChatId) {
        return false;
    }

    const chatContainer = document.getElementById('chatContainer');
    const activeChatId = String(chatContainer?.getAttribute('data-chat-id') || '').trim();
    if (activeChatId !== normalizedChatId) {
        return false;
    }

    const projectId = String(chat?.project_id || '').trim() || null;
    window._projectSidebarSyncState = { chatId: normalizedChatId, projectId };

    if (projectId) {
        // Persist the active project on the chat container immediately so the rest
        // of the UI can recognize the scope before the sidebar script finishes loading.
        chatContainer?.setAttribute('data-project-id', projectId);

        if (typeof window.loadProject === 'function') {
            window.loadProject(projectId, normalizedChatId);
        } else {
            queuePendingProjectSidebarRestore(normalizedChatId, projectId);
        }
        return true;
    }

    if (typeof window.hideProjectSidebar === 'function') {
        window.hideProjectSidebar();
    } else {
        chatContainer?.removeAttribute('data-project-id');
        queuePendingProjectSidebarRestore(normalizedChatId, null);
    }
    return true;
}

async function restoreProjectSidebarForChat(chatId) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId) {
        return null;
    }

    // Prefer live metadata so reloads still restore project scope even when the
    // active chat is not part of the currently rendered sidebar page.
    const detail = await fetchChatDetailForProjectRestore(normalizedChatId);
    const fallbackChat = findCachedChatById(normalizedChatId);
    const resolvedChat = detail || fallbackChat;
    if (!resolvedChat) {
        return null;
    }

    applyProjectSidebarStateForResolvedChat({ ...resolvedChat, id: normalizedChatId });
    return resolvedChat;
}

if (typeof window !== 'undefined') {
    window.restoreProjectSidebarForChat = restoreProjectSidebarForChat;
}


// Typewriter animation for updating a chat title inline
function typewriteText(element, newText, speed = 30) {
    if (!element) return;
    // Cancel previous animation if any
    if (element._typingInterval) {
        clearInterval(element._typingInterval);
        element._typingInterval = null;
    }
    const target = String(newText ?? '');
    if (target.length === 0) {
        element.textContent = '';
        return;
    }
    element.textContent = '';
    let i = 0;
    element._typingInterval = setInterval(() => {
        if (!element.isConnected) {
            clearInterval(element._typingInterval);
            element._typingInterval = null;
            return;
        }
        element.textContent = target.slice(0, i + 1);
        i += 1;
        if (i >= target.length) {
            clearInterval(element._typingInterval);
            element._typingInterval = null;
        }
    }, speed);
}

async function openChatWithProjectContext(chat) {
    // If split-screen is active, exit it first and load this chat normally
    if (window.SplitScreenManager && window.SplitScreenManager.active) {
        const canLeave = typeof window.SplitScreenManager.requestDisable === 'function'
            ? await window.SplitScreenManager.requestDisable({ skipLoadFallback: true })
            : (window.SplitScreenManager.disable({ skipLoadFallback: true }), true);
        if (!canLeave) {
            return;
        }
    }

    const normalizedChatId = String(chat.id);
    const chatContainer = document.getElementById('chatContainer');
    const currentChatId = chatContainer?.getAttribute('data-chat-id') || null;
    const projectId = chat?.project_id ? String(chat.project_id) : null;
    const lastSync = window._projectSidebarSyncState || {};
    const isChatAlreadyActive = currentChatId === normalizedChatId;
    const isProjectStateSynced =
        lastSync.chatId === normalizedChatId &&
        ((projectId && lastSync.projectId === projectId) || (!projectId && !lastSync.projectId));

    if (isChatAlreadyActive && isProjectStateSynced) {
        return;
    }

    try {
        const loaded = await loadChatView(chat.id);
        if (!loaded) {
            return;
        }
    } catch (error) {
        console.error('Failed to load chat view', error);
        return;
    }

    if (chat.project_id && typeof window.loadProject === 'function') {
        window._projectSidebarSyncState = { chatId: normalizedChatId, projectId: projectId };
        window.loadProject(chat.project_id, chat.id);
        return;
    }

    if (typeof window.hideProjectSidebar === 'function') {
        window.hideProjectSidebar();
    }
    chatContainer?.removeAttribute('data-project-id');
    window._projectSidebarSyncState = { chatId: normalizedChatId, projectId: null };

    // Reload model settings schema when switching to non-project chat
    if (typeof window.reloadModelSettingsIfNeeded === 'function') {
        window.reloadModelSettingsIfNeeded();
    }
}


function syncProjectSidebarWithActiveChat(chats) {
    if (!Array.isArray(chats) || typeof window === 'undefined') {
        return;
    }
    const chatContainer = document.getElementById('chatContainer');
    if (!chatContainer) {
        return;
    }
    const activeChatId = chatContainer.getAttribute('data-chat-id');
    if (!activeChatId) {
        return;
    }
    const normalizedActiveId = String(activeChatId);
    const activeChat = chats.find(chat => String(chat.id) === normalizedActiveId);
    if (!activeChat) {
        // The active chat is not guaranteed to be part of the currently rendered
        // sidebar page during reloads or partial lazy-load refreshes. In that case
        // we must preserve the already restored project state instead of treating
        // the missing row as a non-project chat.
        return;
    }
    const projectId = activeChat?.project_id ? String(activeChat.project_id) : null;
    const lastSync = window._projectSidebarSyncState || {};
    if (lastSync.chatId === normalizedActiveId && lastSync.projectId === projectId) {
        return;
    }
    window._projectSidebarSyncState = { chatId: normalizedActiveId, projectId };

    if (projectId && typeof window.loadProject === 'function') {
        window.loadProject(projectId, normalizedActiveId);
    } else if (typeof window.hideProjectSidebar === 'function') {
        window.hideProjectSidebar();
    }
}




function updateTabTitleIfActive(chatId) {
    const getActiveChatId = window.getActiveChatId;
    const updateTabTitleForActiveChat = window.updateTabTitleForActiveChat;
    if (typeof getActiveChatId === 'function' && typeof updateTabTitleForActiveChat === 'function') {
        if (String(getActiveChatId()) === String(chatId)) {
            updateTabTitleForActiveChat(chatId);
        }
    }
}

function positionChatDropdown(dropdown, anchorEl) {
    if (!dropdown || !anchorEl) return;

    const triggerEl = anchorEl.querySelector('.sidebar-element-menu-trigger') || anchorEl;
    const prevVis = dropdown.style.visibility;

    // Measure without flashing
    dropdown.style.visibility = 'hidden';
    dropdown.classList.add('open');

    const trigger = triggerEl.getBoundingClientRect();
    const { offsetWidth: w, offsetHeight: h } = dropdown;
    const gap = 4;
    const pad = 8;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    // Open upward if trigger is in bottom 25% of screen
    const openUp = trigger.top > vh * 0.75;

    // Calculate position
    const alignRight = vw <= 1024 || document.body.classList.contains('sidebar-overlay-mode');
    let proposedLeft = alignRight ? trigger.right - w : trigger.left;
    proposedLeft = Math.min(proposedLeft, vw - w - pad);
    let left = Math.max(pad, proposedLeft);
    let top = openUp
        ? Math.max(pad, trigger.top - h - gap)
        : Math.min(trigger.bottom + gap, vh - h - pad);

    // In overlay mode the sidebar is transformed, so this fixed-position dropdown is
    // positioned relative to the sidebar container instead of the viewport. Compensate
    // for the chat history's internal scroll so the menu stays aligned with the row trigger.
    if (vw <= 1024 && document.body.classList.contains('sidebar-overlay-mode')) {
        const scrollHost = anchorEl.closest('.sidebar-main');
        if (scrollHost) {
            top += scrollHost.scrollTop;
        }
    }

    // Apply styles
    Object.assign(dropdown.style, {
        position: 'fixed',
        top: `${top}px`,
        left: `${left}px`,
        visibility: prevVis || '',
        right: 'auto',
        bottom: 'auto'
    });

    dropdown.classList.toggle('upward', openUp);
}




function checkChatDeletionAllowed() {
    try {
        const item = localStorage.getItem('allowChatDeletion');
        if (item == null) {
            return true;
        }
        const normalized = String(item).trim().toLowerCase();
        if (['false', '0', 'no', 'off'].includes(normalized)) {
            return false;
        }
        if (['true', '1', 'yes', 'on'].includes(normalized)) {
            return true;
        }
        return Boolean(normalized);
    } catch (error) {
        return true;
    }
}

function getChatSidebarDropdownItemsMarkup() {
    const iconSet = typeof Icons === 'object' ? Icons : (window.Icons || {});
    return [
        `<div class="select-dropdown-item"><div class="select-dropdown-button edit-btn">${iconSet.edit || ''} <p data-i18n="sidebar_chat_action_edit">${sidebarChatT('sidebar_chat_action_edit', 'Edit')}</p></div></div>`,
        `<div class="select-dropdown-item"><div class="select-dropdown-button duplicate-btn">${iconSet.copy || ''} <p data-i18n="sidebar_chat_action_duplicate">${sidebarChatT('sidebar_chat_action_duplicate', 'Duplicate')}</p></div></div>`,
        `<div class="select-dropdown-item"><div class="select-dropdown-button archive-btn">${iconSet.archive || ''} <p data-i18n="sidebar_chat_action_archive">${sidebarChatT('sidebar_chat_action_archive', 'Archive')}</p></div></div>`,
        checkChatDeletionAllowed() ? `<div class="select-dropdown-item"><div class="select-dropdown-button select-dropdown-button-red delete-btn">${iconSet.trash || ''} <p data-i18n="sidebar_chat_action_delete">${sidebarChatT('sidebar_chat_action_delete', 'Delete')}</p></div></div>` : ''
    ].filter(Boolean).join('');
}

function bindChatSidebarDropdownActionHandlers(row, chat, options = {}) {
    if (!row || !chat) return;

    const dropdown = row.querySelector('.select-dropdown');
    if (!dropdown) return;
    if (dropdown.dataset.chatSidebarActionHandlersBound === 'true') return;
    dropdown.dataset.chatSidebarActionHandlersBound = 'true';

    const getTitle = typeof options.getTitle === 'function'
        ? options.getTitle
        : () => row.dataset.chatTitle || row.querySelector('a.sidebar-element-button > p')?.textContent?.trim() || getUntitledChatTitle();
    const closeContainingPanel = typeof options.closePanel === 'function' ? options.closePanel : null;
    const afterListRefresh = typeof options.afterListRefresh === 'function' ? options.afterListRefresh : null;

    const closeForDialog = () => {
        dropdown.classList.remove('open');
        closeContainingPanel?.();
    };

    const refreshChatLists = async () => {
        if (typeof initChatList === 'function') {
            await initChatList();
        }
        if (afterListRefresh) {
            await afterListRefresh();
        }
    };

    row.querySelector('.edit-btn')?.addEventListener('click', (event) => {
        event.stopPropagation();
        closeForDialog();
        if (typeof showChatEditContainer === 'function') {
            showChatEditContainer({ ...chat, title: getTitle() });
        }
    });

    row.querySelector('.duplicate-btn')?.addEventListener('click', async (event) => {
        event.stopPropagation();
        dropdown.classList.remove('open');
        try {
            const res = await window.authedFetch(`/api/v1/chats/duplicate?${new URLSearchParams({ chat_id: chat.id })}`, { method: 'POST' });
            if (res.ok) {
                await refreshChatLists();
                notifySuccess?.(sidebarChatT('sidebar_chat_duplicated', 'Chat duplicated'));
            } else {
                notifyError?.(sidebarChatT('sidebar_chat_duplicate_failed', 'Failed to duplicate chat'));
            }
        } catch (error) {
            console.error(error);
            notifyError?.(error?.message || sidebarChatT('sidebar_chat_duplicate_failed', 'Failed to duplicate chat'));
        }
    });

    row.querySelector('.delete-btn')?.addEventListener('click', (event) => {
        event.stopPropagation();
        closeForDialog();
        if (typeof showChatDeleteContainer === 'function') {
            showChatDeleteContainer({ ...chat, title: getTitle() });
        }
    });

    row.querySelector('.archive-btn')?.addEventListener('click', async (event) => {
        event.stopPropagation();
        dropdown.classList.remove('open');
        try {
            const res = await window.authedFetch('/api/v1/chats/archive', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ chat_id: chat.id })
            });
            if (res.ok) {
                const currentChatId = document.getElementById('chatContainer')?.getAttribute('data-chat-id');
                if (currentChatId === chat.id && typeof showChatStartContainer === 'function') {
                    showChatStartContainer();
                }
                await refreshChatLists();
                notifySuccess?.(sidebarChatT('sidebar_chat_archived', 'Chat archived'));
            } else {
                notifyError?.(sidebarChatT('sidebar_chat_archive_failed', 'Failed to archive chat'));
            }
        } catch (error) {
            console.error('Archive chat failed', error);
            notifyError?.(sidebarChatT('sidebar_chat_archive_failed', 'Failed to archive chat'));
        }
    });
}



async function hideAllChatSections() {
    historySection.style.display = 'none';
    pinnedSection.style.display = 'none';
    chatsContainer.innerHTML = '';
    pinnedContainer.innerHTML = '';
}


// =============================================
// Paginated Chat Fetching
// =============================================

/**
 * Fetch chats with pagination (for lazy loading)
 */
async function fetchChatListPaginated(offset = 0, limit = 20, projectId = null) {
    const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    if (projectId) params.append('project_id', projectId);

    const response = await window.authedFetch(`/api/v1/chats/paginated?${params.toString()}`, { method: 'GET' });
    if (!response.ok) {
        notifyError(sidebarChatT('sidebar_chat_list_fetch_failed', 'Failed to fetch chat list'));
        return null;
    }
    return await response.json();
}

/**
 * Load more chats when scrolling (called by IntersectionObserver)
 */
async function loadMoreChats() {
    if (lazyLoadState.isLoading || !lazyLoadState.hasMore) return;

    lazyLoadState.isLoading = true;

    // Show loading skeletons
    const skeletons = [];
    for (let i = 0; i < 3; i++) {
        const skeleton = createLoadingSkeleton();
        // Insert before sentinel
        if (loadingSentinel && chatsContainer.contains(loadingSentinel)) {
            chatsContainer.insertBefore(skeleton, loadingSentinel);
        } else {
            chatsContainer.appendChild(skeleton);
        }
        skeletons.push(skeleton);
    }

    try {
        const data = await fetchChatListPaginated(
            lazyLoadState.offset,
            lazyLoadState.limit,
            lazyLoadState.projectId
        );

        if (!data) {
            lazyLoadState.hasMore = false;
            return;
        }

        // Remove skeletons
        skeletons.forEach(s => s.remove());

        // Append new unpinned chats
        for (const chat of data.items) {
            const idStr = String(chat.id);
            // Skip if already loaded (prevent duplicates)
            if (lazyLoadState.loadedChatIds.has(idStr)) continue;
            lazyLoadState.loadedChatIds.add(idStr);

            const row = createChatRow(chat);
            // Insert before sentinel
            if (loadingSentinel && chatsContainer.contains(loadingSentinel)) {
                chatsContainer.insertBefore(row, loadingSentinel);
            } else {
                chatsContainer.appendChild(row);
            }
        }

        // Update state
        lazyLoadState.offset += data.items.length;
        lazyLoadState.hasMore = data.has_more;
        lazyLoadState.totalUnpinned = data.total_unpinned;

        // Show history section if we have unpinned chats
        if (data.items.length > 0 || lazyLoadState.offset > 0) {
            historySection.style.display = '';
        }

        // Refresh time-based dividers after loading more chats
        refreshTimeDividers(chatsContainer);

    } catch (err) {
        console.error('Failed to load more chats', err);
        skeletons.forEach(s => s.remove());
    } finally {
        lazyLoadState.isLoading = false;
    }
}





// =============================================
// Time-based Chat History Dividers
// =============================================
function getTimeBucket(dateStr) {
    if (!dateStr) return null;
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return null;
    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startOfYesterday = new Date(startOfToday);
    startOfYesterday.setDate(startOfYesterday.getDate() - 1);
    const startOf7DaysAgo = new Date(startOfToday);
    startOf7DaysAgo.setDate(startOf7DaysAgo.getDate() - 7);
    const startOf30DaysAgo = new Date(startOfToday);
    startOf30DaysAgo.setDate(startOf30DaysAgo.getDate() - 30);

    if (date >= startOfToday) {
        return {
            id: 'today',
            label: sidebarChatT('sidebar_time_today', 'Today'),
            i18nKey: 'sidebar_time_today',
        };
    }
    if (date >= startOfYesterday) {
        return {
            id: 'yesterday',
            label: sidebarChatT('sidebar_time_yesterday', 'Yesterday'),
            i18nKey: 'sidebar_time_yesterday',
        };
    }
    if (date >= startOf7DaysAgo) {
        return {
            id: 'last_7_days',
            label: sidebarChatT('sidebar_time_last_7_days', 'Last 7 Days'),
            i18nKey: 'sidebar_time_last_7_days',
        };
    }
    if (date >= startOf30DaysAgo) {
        return {
            id: 'last_30_days',
            label: sidebarChatT('sidebar_time_last_30_days', 'Last 30 Days'),
            i18nKey: 'sidebar_time_last_30_days',
        };
    }

    const chatYear = date.getFullYear();
    if (chatYear === now.getFullYear()) {
        const label = new Intl.DateTimeFormat(getSidebarLocale(), { month: 'long' }).format(date);
        return {
            id: `month_${date.getMonth()}`,
            label,
        };
    }
    return {
        id: `year_${chatYear}`,
        label: String(chatYear),
    };
}

function createTimeDivider(bucket) {
    const divider = document.createElement('div');
    divider.className = 'chat-time-divider';
    divider.dataset.timeBucket = bucket.id;
    const text = document.createElement('span');
    text.className = 'chat-time-divider-label';
    if (bucket.i18nKey) {
        text.setAttribute('data-i18n', bucket.i18nKey);
    }
    text.textContent = bucket.label;
    divider.appendChild(text);
    return divider;
}

function refreshTimeDividers(container) {
    if (!container) return;
    container.querySelectorAll('.chat-time-divider').forEach(d => d.remove());
    let lastBucket = null;
    const chatRows = container.querySelectorAll('.sidebar-element:not(.sidebar-element-loading)');
    for (const row of chatRows) {
        const bucket = getTimeBucket(row.dataset.lastUpdatedAt);
        const shouldShowDivider = bucket && bucket.id !== 'today';
        if (shouldShowDivider && bucket.id !== lastBucket) {
            container.insertBefore(createTimeDivider(bucket), row);
            lastBucket = bucket.id;
        }
    }
}


async function renderChatSidebarList(chats) {
    // This function is generally used for "Reset/Full Render" of the visible list (e.g. initial load or after unpin).
    // For pagination, we usually pass the *currently loaded* batched list or just the first page if resetting.

    // Hide sections if no chats
    if (!chats || chats.length === 0) {
        hideAllChatSections();
        return;
    }

    // Separate pinned and unpinned chats
    const pinnedChats = chats.filter(chat => chat.pinned_position !== null).sort((a, b) => a.pinned_position - b.pinned_position);
    const unpinnedChats = chats.filter(chat => chat.pinned_position === null).sort((a, b) => new Date(b.last_updated_at) - new Date(a.last_updated_at));

    // Hide pinned section, if pinned chats do not exist
    if (pinnedChats.length === 0) {
        pinnedSection.style.display = 'none';
    } else {
        pinnedSection.style.display = '';
    }

    // Hide unpinned section, if unpinned chats do not exist
    if (unpinnedChats.length === 0) {
        historySection.style.display = 'none';
    } else {
        historySection.style.display = '';
    }

    // Build maps of existing rows by chatId for both sections
    const pinnedRows = Array.from(pinnedContainer.querySelectorAll('.sidebar-element'));
    const pinnedById = new Map(pinnedRows.map(r => [r.dataset.chatId, r]));

    const existingRows = Array.from(chatsContainer.querySelectorAll('.sidebar-element:not(.sidebar-element-loading):not(.chats-loading-sentinel)'));
    const existingById = new Map(existingRows.map(r => [r.dataset.chatId, r]));

    // Sync pinned section
    const pinnedIds = new Set(pinnedChats.map(c => String(c.id)));
    // Remove deleted pinned
    pinnedRows.forEach(row => {
        if (!pinnedIds.has(row.dataset.chatId)) row.remove();
    });

    // Sync unpinned section
    // NOTE: When rendering from initChatList (page 0), this implies we remove any rows not in page 0.
    // This is desired behavior for a "Reset/Init".
    const unpinnedIds = new Set(unpinnedChats.map(c => String(c.id)));
    existingRows.forEach(row => {
        if (!unpinnedIds.has(row.dataset.chatId)) row.remove();
    });

    // Update loaded IDs state
    lazyLoadState.loadedChatIds.clear();
    pinnedIds.forEach(id => lazyLoadState.loadedChatIds.add(id));
    unpinnedIds.forEach(id => lazyLoadState.loadedChatIds.add(id));


    // Add/update rows in pinned section
    const allowDeletionFlag = await checkChatDeletionAllowed(); // await if needed (it was async in code)

    pinnedChats.forEach(chat => {
        const idStr = String(chat.id);
        let row = pinnedById.get(idStr);
        if (!row) {
            row = createChatRow(chat);
        } else {
            const shouldRebuild = row.dataset.allowChatDeletion !== allowDeletionFlag;
            if (shouldRebuild) {
                const newRow = createChatRow(chat);
                row.replaceWith(newRow);
                row = newRow;
                pinnedById.set(idStr, row);
            } else {
                updateChatRowContent(row, chat, allowDeletionFlag);
            }
        }
        pinnedContainer.appendChild(row);
        updateTabTitleIfActive(chat.id);
    });

    // Add/update rows in unpinned section
    unpinnedChats.forEach(chat => {
        const idStr = String(chat.id);
        let row = existingById.get(idStr);
        if (!row) {
            row = createChatRow(chat);
        } else {
            const shouldRebuild = row.dataset.allowChatDeletion !== allowDeletionFlag;
            if (shouldRebuild) {
                const newRow = createChatRow(chat);
                row.replaceWith(newRow);
                row = newRow;
                existingById.set(idStr, row);
            } else {
                updateChatRowContent(row, chat, allowDeletionFlag);
            }
        }
        // Ensure we don't append after sentinel if it exists
        if (loadingSentinel && chatsContainer.contains(loadingSentinel)) {
            chatsContainer.insertBefore(row, loadingSentinel);
        } else {
            chatsContainer.appendChild(row);
        }
        updateTabTitleIfActive(chat.id);
    });

    syncProjectSidebarWithActiveChat(chats);

    // Insert time-based dividers for chat history
    refreshTimeDividers(chatsContainer);

    return { pinnedChats, unpinnedChats };
}

// Helper to update row content without full replace
function updateChatRowContent(row, chat, allowDeletionFlag) {
    const link = row.querySelector('a.sidebar-element-button');
    if (link) link.href = `/chat/${encodeURIComponent(chat.id)}`;

    const titleEl = row.querySelector('a.sidebar-element-button > p');
    const newTitle = chatTitleUtils.getChatDisplayTitle?.(chat, getUntitledChatTitle()) || chat.title || '';
    const newSource = chatTitleUtils.isAutomationChat?.(chat)
        ? 'automation'
        : String(chat.source ?? chat?.meta?.source ?? '').trim().toLowerCase();
    if (titleEl && chatTitleUtils.getChatTitleTextFromElement?.(titleEl, row.dataset.chatTitle || '') !== newTitle) {
        if (typeof chatTitleUtils.setChatTitleElement === 'function') {
            chatTitleUtils.setChatTitleElement(titleEl, chat, { fallbackTitle: getUntitledChatTitle() });
        } else {
            typewriteText(titleEl, newTitle);
        }
    }
    row.dataset.chatTitle = newTitle;
    row.dataset.chatSource = newSource === 'automation' ? 'automation' : newSource;
    row.dataset.lastUpdatedAt = chat.last_updated_at || '';
    row.dataset.pinnedPosition = chat.pinned_position ?? '';
    row.dataset.allowChatDeletion = allowDeletionFlag;
    window.ChatAttention?.registerChat(chat);
    window.ChatAttention?.decorateRow(row, chat.id);
}


async function initChatList() {
    // Reset state
    resetLazyLoadState();

    // Fetch first page
    const data = await fetchChatListPaginated(0, lazyLoadState.limit);
    if (!data) return; // Error handled inside fetch

    // data = { pinned: [], items: [], total_unpinned, has_more, ... }

    // Update state
    lazyLoadState.offset = data.items.length;
    lazyLoadState.hasMore = data.has_more;
    lazyLoadState.totalUnpinned = data.total_unpinned;
    lazyLoadState.loadedChatIds.clear(); // Will be populated in render

    // Flatten result for render function (compatibility)
    const combined = [...data.pinned, ...data.items];

    // Render (this will clear existing list and show only these items)
    await renderChatSidebarList(combined);
    writeCachedChatList(combined);

    // Setup Infinite Scroll
    setupInfiniteScroll();

    // Setup Search (passing full combined list might be incomplete for search if search is client side)
    // NOTE: initChatSearchList likely needs to know that this is partial data OR we rely on server search.
    // If the original initChatSearchList was doing client-side filtering on `chats` array, it will now only search top 20.
    // However, given the requirement "load existing chats... lazy loading", client-side search on ALL chats would defeat the purpose (loading all).
    // So search should ideally use the backend search endpoint.
    // We pass the partial list so it at least initializes basic logic if needed.
    if (typeof initChatSearchList === 'function') {
        initChatSearchList(combined);
    }
}

// Global exposure
window.initChatList = initChatList;
window.renderChatSidebarList = renderChatSidebarList;
window.getCachedChatList = readCachedChatList;
window.updateCachedChatListEntry = updateCachedChatListEntry;

if (typeof window !== 'undefined' && !window.__chatImportRefreshListenerBound) {
    window.addEventListener('dataControls:importedDataChanged', async (event) => {
        if (!event?.detail?.refreshChats) {
            return;
        }
        try {
            await initChatList();
        } catch (error) {
            console.warn('[chatsHelper] Failed to refresh chat list after imported data change', error);
        }
    });
    window.__chatImportRefreshListenerBound = true;
}

if (typeof document !== 'undefined' && !window.__chatSidebarTimeDividerI18nBound) {
    document.addEventListener('i18n:updated', () => {
        refreshTimeDividers(chatsContainer);
    });
    window.__chatSidebarTimeDividerI18nBound = true;
}

async function bootstrapChatList() {
    await hydrateChatListFromCache();
    await initChatList();
}

bootstrapChatList().catch((error) => {
    console.error('Failed to initialize chat list', error);
});



function startInlineRename(row, chat) {
    if (row.querySelector('.inline-rename-input')) return;

    const anchor = row.querySelector('a.sidebar-element-button');
    const titleP = anchor?.querySelector('p');
    if (!anchor || !titleP) return;

    const currentTitle = row.dataset.chatTitle || chatTitleUtils.getChatTitleTextFromElement?.(titleP, '') || titleP.textContent.trim() || '';

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'inline-rename-input';
    input.value = currentTitle;

    titleP.style.display = 'none';
    anchor.insertBefore(input, titleP);
    row.classList.add('inline-renaming');

    input.focus();
    input.select();

    // Prevent the anchor click from navigating while editing
    const blockClick = (e) => { e.preventDefault(); e.stopPropagation(); };
    anchor.addEventListener('click', blockClick, true);

    let committed = false;
    const commit = async () => {
        if (committed) return;
        committed = true;
        const newTitle = input.value.trim();
        cleanup();
        if (!newTitle || newTitle === currentTitle) return;

        // Optimistically update the UI
        if (typeof chatTitleUtils.setChatTitleElement === 'function') {
            chatTitleUtils.setChatTitleElement(titleP, { title: newTitle, source: row.dataset.chatSource }, { fallbackTitle: getUntitledChatTitle() });
        } else {
            titleP.textContent = newTitle;
        }
        row.dataset.chatTitle = newTitle;
        updateCachedChatListEntry({
            id: chat.id,
            title: newTitle,
            last_updated_at: row.dataset.lastUpdatedAt || chat.last_updated_at,
            pinned_position: parsePinnedPosition(row.dataset.pinnedPosition),
            project_id: chat.project_id ?? null,
            source: row.dataset.chatSource || '',
        });

        try {
            const params = new URLSearchParams({ chat_id: chat.id, title: newTitle });
            const res = await window.authedFetch(`/api/v1/chats/rename?${params.toString()}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' }
            });
            if (!res.ok) {
                if (typeof chatTitleUtils.setChatTitleElement === 'function') {
                    chatTitleUtils.setChatTitleElement(titleP, { title: currentTitle, source: row.dataset.chatSource }, { fallbackTitle: getUntitledChatTitle() });
                } else {
                    titleP.textContent = currentTitle;
                }
                row.dataset.chatTitle = currentTitle;
                updateCachedChatListEntry({
                    id: chat.id,
                    title: currentTitle,
                    last_updated_at: row.dataset.lastUpdatedAt || chat.last_updated_at,
                    pinned_position: parsePinnedPosition(row.dataset.pinnedPosition),
                    project_id: chat.project_id ?? null,
                    source: row.dataset.chatSource || '',
                });
                notifyError?.(sidebarChatT('sidebar_chat_rename_failed', 'Failed to rename chat'));
            }
        } catch (err) {
            if (typeof chatTitleUtils.setChatTitleElement === 'function') {
                chatTitleUtils.setChatTitleElement(titleP, { title: currentTitle, source: row.dataset.chatSource }, { fallbackTitle: getUntitledChatTitle() });
            } else {
                titleP.textContent = currentTitle;
            }
            row.dataset.chatTitle = currentTitle;
            updateCachedChatListEntry({
                id: chat.id,
                title: currentTitle,
                last_updated_at: row.dataset.lastUpdatedAt || chat.last_updated_at,
                pinned_position: parsePinnedPosition(row.dataset.pinnedPosition),
                project_id: chat.project_id ?? null,
                source: row.dataset.chatSource || '',
            });
            console.error('Inline rename failed', err);
            notifyError?.(sidebarChatT('sidebar_chat_rename_failed', 'Failed to rename chat'));
        }
    };

    const cancel = () => {
        if (committed) return;
        committed = true;
        cleanup();
    };

    const cleanup = () => {
        input.remove();
        titleP.style.display = '';
        row.classList.remove('inline-renaming');
        anchor.removeEventListener('click', blockClick, true);
    };

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            commit();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            cancel();
        }
        e.stopPropagation();
    });
    input.addEventListener('blur', () => commit());
    input.addEventListener('click', (e) => e.stopPropagation());
    input.addEventListener('dblclick', (e) => e.stopPropagation());
}

function createChatRow(chat) {
    const row = document.createElement('div');
    row.className = 'sidebar-element';
    row.dataset.chatId = chat.id;
    row.dataset.chatSource = chatTitleUtils.isAutomationChat?.(chat)
        ? 'automation'
        : String(chat.source ?? chat?.meta?.source ?? '').trim().toLowerCase();
    row.dataset.chatTitle = chatTitleUtils.getChatDisplayTitle?.(chat, getUntitledChatTitle()) || getUntitledChatTitle();
    row.dataset.lastUpdatedAt = chat.last_updated_at || '';
    row.dataset.pinnedPosition = chat.pinned_position ?? '';
    row.draggable = true;

    row.innerHTML = `
        <a class="sidebar-element-button space-between" href="/chat/${encodeURIComponent(chat.id)}">
            <p class="chat-title-with-badge"></p>
        </a>
        <button type="button" class="sidebar-element-menu-trigger" aria-label="${sidebarChatT('sidebar_chat_open_menu_aria', 'Open chat menu')}" data-i18n-attr="aria-label:sidebar_chat_open_menu_aria">${Icons.ellipsis}</button>
        <div class="select-dropdown">${getChatSidebarDropdownItemsMarkup()}</div>
    `;
    const querySelector = row.querySelector.bind(row);
    querySelector('a.sidebar-element-button > p').textContent = row.dataset.chatTitle;
    const titleEl = querySelector('a.sidebar-element-button > p');
    if (typeof chatTitleUtils.setChatTitleElement === 'function') {
        chatTitleUtils.setChatTitleElement(titleEl, chat, { fallbackTitle: getUntitledChatTitle() });
    } else if (titleEl) {
        titleEl.textContent = row.dataset.chatTitle;
    }
    window.ChatAttention?.registerChat(chat);
    window.ChatAttention?.decorateRow(row, chat.id);

    const getTitle = () => row.dataset.chatTitle || (row.dataset.chatTitle = chatTitleUtils.getChatTitleTextFromElement?.(row.querySelector('a.sidebar-element-button > p'), getUntitledChatTitle()) || row.querySelector('a.sidebar-element-button > p')?.textContent.trim() || getUntitledChatTitle());

    // Chat navigation (delayed to allow double-click to intercept)
    let clickTimer = null;
    const anchorEl = row.querySelector('.sidebar-element-button');
    anchorEl.addEventListener('click', (e) => {
        e.preventDefault();
        if (row.classList.contains('inline-renaming')) return;
        if (clickTimer) clearTimeout(clickTimer);
        clickTimer = setTimeout(async () => {
            clickTimer = null;
            if (row.classList.contains('inline-renaming')) return;
            await openChatWithProjectContext(chat);
            if (window.innerWidth <= 1024 && document.body.classList.contains('sidebar-open') && typeof closeSidebar === 'function') {
                closeSidebar();
            }
        }, 250);
    });

    // Dropdown toggle
    const toggleDropdown = (e) => {
        e.preventDefault();
        e.stopPropagation();
        const dropdown = row.querySelector('.select-dropdown');
        const shouldOpen = !dropdown.classList.contains('open');
        closeAllChatDropdowns();
        if (shouldOpen) {
            if (typeof window.closeModelSelect === 'function') {
                window.closeModelSelect();
            }
            window.getDropdownPanelNavigator?.(dropdown)?.reset({ focus: false });
            dropdown.classList.add('open');
            positionChatDropdown(dropdown, row);
        }
    };

    const trigger = row.querySelector('.sidebar-element-menu-trigger');
    trigger.addEventListener('click', toggleDropdown);

    // Action handlers
    const dropdown = row.querySelector('.select-dropdown');
    if (typeof attachDropdownHandlers === 'function') {
        attachDropdownHandlers(dropdown, chat);
    }
    bindChatSidebarDropdownActionHandlers(row, chat, {
        getTitle,
        closePanel: () => {
            if (window.innerWidth <= 1024 && typeof closeSidebar === 'function') {
                closeSidebar();
            }
        },
    });

    // Inline rename on double-click
    const titleP = row.querySelector('a.sidebar-element-button > p');
    if (titleP) {
        titleP.addEventListener('dblclick', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
            startInlineRename(row, chat);
        });
    }

    // Drag handlers
    row.addEventListener('dragstart', (e) => {
        const chatReferencePayload = buildSidebarChatReferenceDragPayload(chat, getTitle());
        // Use a minimal pill-shaped ghost (chat icon + title) as the drag
        // image instead of the browser's default row snapshot.
        setSidebarChatDragImage(e, getTitle());
        if (chatReferencePayload) {
            e.dataTransfer.setData('text/plain', chatReferencePayload.chat_id);
            setActiveSidebarChatReferenceDragPayload(chatReferencePayload);
        }
        try {
            if (chatReferencePayload) {
                e.dataTransfer.setData('application/x-omlorix-chat-reference', JSON.stringify({
                    ...chatReferencePayload,
                    project_id: chat.project_id || null,
                }));
            }
        } catch (_) {
            // Ignore drag payload serialization issues so split-screen drag still works.
        }
        // Sidebar rows can be reordered (move) or opened in split-screen
        // (copy). Advertising both operations prevents browsers from rejecting
        // the split target's copy drop effect as incompatible.
        e.dataTransfer.effectAllowed = 'copyMove';
        row.classList.add('dragging');
        if (chat.pinned_position !== null) {
            e.dataTransfer.setData('original-position', chat.pinned_position);
        }
    });

    row.addEventListener('dragend', () => {
        row.classList.remove('dragging');
        removeSidebarChatDragGhost();
        clearActiveSidebarChatReferenceDragPayload(chat?.id);
    });

    return row;
}

// ───── Sidebar chat drag ghost ─────

/** The off-screen element currently used as the drag image, if any. */
let activeSidebarChatDragGhost = null;

/**
 * Build a minimal pill-shaped drag ghost (chat icon + title) and use it as
 * the drag image for a sidebar chat drag. The element must be attached to
 * the DOM when setDragImage is called, so it is placed off-screen and
 * removed again on dragend.
 *
 * Fails silently (falling back to the browser default drag image) if the
 * environment does not support setDragImage.
 */
function setSidebarChatDragImage(event, title) {
    try {
        if (typeof event?.dataTransfer?.setDragImage !== 'function') return;
        removeSidebarChatDragGhost();

        const ghost = document.createElement('div');
        ghost.className = 'sidebar-chat-drag-ghost';
        ghost.setAttribute('aria-hidden', 'true');

        const icon = document.createElement('span');
        icon.className = 'sidebar-chat-drag-ghost-icon';
        icon.innerHTML = (typeof Icons !== 'undefined' && Icons.admin_sidebar_chat) || '';
        ghost.appendChild(icon);

        const text = document.createElement('span');
        text.className = 'sidebar-chat-drag-ghost-title';
        text.textContent = title || '';
        ghost.appendChild(text);

        document.body.appendChild(ghost);
        activeSidebarChatDragGhost = ghost;
        // Anchor the ghost slightly below-right of the cursor
        event.dataTransfer.setDragImage(ghost, 18, 18);
    } catch (_) {
        // Keep the default drag image if anything goes wrong.
        removeSidebarChatDragGhost();
    }
}

/** Remove the off-screen drag ghost element after the drag finishes. */
function removeSidebarChatDragGhost() {
    if (activeSidebarChatDragGhost) {
        activeSidebarChatDragGhost.remove();
        activeSidebarChatDragGhost = null;
    }
}








/** Find a chat row in either main sidebar section. */
function findChatSidebarRow(chatId) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId) return null;

    const containers = [
        document.getElementById('pinnedChatsContainer'),
        document.getElementById('chatsContainer'),
    ].filter(Boolean);
    for (const container of containers) {
        const row = Array.from(container.querySelectorAll('.sidebar-element'))
            .find((element) => element.dataset.chatId === normalizedChatId);
        if (row) return row;
    }
    return null;
}


/**
 * Materialize a newly-created chat in the main sidebar immediately.
 *
 * Normal streamed chats and realtime calls are created through different API
 * paths. Keeping this helper global lets both paths provide the same sidebar
 * behavior without reloading the entire paginated chat list.
 */
function ensureChatSidebarRow(chatId, { initialTitle = '', projectId = null } = {}) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId) return null;

    let row = findChatSidebarRow(normalizedChatId);
    const chatsContainerEl = document.getElementById('chatsContainer');
    if (!row && chatsContainerEl) {
        const nowIso = new Date().toISOString();
        row = createChatRow({
            id: normalizedChatId,
            title: initialTitle,
            project_id: projectId,
            last_updated_at: nowIso,
            pinned_position: null,
        });
        row.dataset.lastUpdatedAt = nowIso;
        const firstChatRow = chatsContainerEl.querySelector('.sidebar-element');
        chatsContainerEl.insertBefore(row, firstChatRow || chatsContainerEl.firstChild);
        refreshTimeDividers(chatsContainerEl);
        lazyLoadState.loadedChatIds.add(normalizedChatId);
        updateCachedChatListEntry({
            id: normalizedChatId,
            title: initialTitle,
            project_id: projectId,
            last_updated_at: nowIso,
            pinned_position: null,
        });
    }

    if (row) {
        const link = row.querySelector('a.sidebar-element-button');
        if (link) link.href = `/chat/${encodeURIComponent(normalizedChatId)}`;
    }
    if (historySection) historySection.style.display = '';

    if (projectId && typeof window.addOrUpdateProjectChatRow === 'function') {
        window.addOrUpdateProjectChatRow(normalizedChatId, initialTitle);
    }
    return row;
}


/** Update a materialized sidebar row after automatic title generation. */
function applyChatSidebarTitle(chatId, title) {
    const normalizedChatId = String(chatId || '').trim();
    const normalizedTitle = String(title || '').trim();
    if (!normalizedChatId || !normalizedTitle) return;

    const row = ensureChatSidebarRow(normalizedChatId, { initialTitle: normalizedTitle });
    if (!row) return;
    row.dataset.chatTitle = normalizedTitle;
    const titleElement = row.querySelector('a.sidebar-element-button > p');
    if (titleElement) {
        if (typeof chatTitleUtils.setChatTitleElement === 'function') {
            chatTitleUtils.setChatTitleElement(
                titleElement,
                { title: normalizedTitle, source: row.dataset.chatSource || '' },
                { fallbackTitle: getUntitledChatTitle() },
            );
        } else {
            titleElement.textContent = normalizedTitle;
        }
    }
    updateCachedChatListEntry({
        id: normalizedChatId,
        title: normalizedTitle,
        last_updated_at: row.dataset.lastUpdatedAt || new Date().toISOString(),
        pinned_position: parsePinnedPosition(row.dataset.pinnedPosition),
    });
    const projectChatsContainer = document.querySelector('.project-sidebar-chats');
    const existingProjectRow = projectChatsContainer
        ? Array.from(projectChatsContainer.querySelectorAll('.sidebar-element'))
            .find((element) => element.dataset.chatId === normalizedChatId)
        : null;
    // The project sidebar remains mounted when hidden. Only update a row that
    // was materialized for this chat's actual project; creating a missing row
    // here could leak a non-project chat into the previously viewed project.
    if (existingProjectRow && typeof window.addOrUpdateProjectChatRow === 'function') {
        window.addOrUpdateProjectChatRow(normalizedChatId, normalizedTitle);
    }
    if (typeof updateTabTitleIfActive === 'function') {
        updateTabTitleIfActive(normalizedChatId);
    }
}


window.ensureChatSidebarRow = ensureChatSidebarRow;
window.applyChatSidebarTitle = applyChatSidebarTitle;


function moveChatRowToTop(chatId) {
    if (!chatId) return;
    const idStr = String(chatId);
    const chatsContainerEl = document.getElementById('chatsContainer');
    if (!chatsContainerEl) return;

    const pinnedContainerEl = document.getElementById('pinnedChatsContainer');
    if (pinnedContainerEl) {
        const isPinned = Array.from(pinnedContainerEl.querySelectorAll('.sidebar-element')).some(row => row.dataset.chatId === idStr);
        if (isPinned) return;
    }

    const row = Array.from(chatsContainerEl.querySelectorAll('.sidebar-element')).find(el => el.dataset.chatId === idStr);
    if (!row) return;

    // Update timestamp since this chat was just active
    row.dataset.lastUpdatedAt = new Date().toISOString();
    updateCachedChatListEntry({
        id: idStr,
        title: row.dataset.chatTitle || chatTitleUtils.getChatTitleTextFromElement?.(row.querySelector('a.sidebar-element-button > p'), getUntitledChatTitle()) || row.querySelector('a.sidebar-element-button > p')?.textContent?.trim() || getUntitledChatTitle(),
        last_updated_at: row.dataset.lastUpdatedAt,
        pinned_position: null,
        source: row.dataset.chatSource || '',
    });

    const firstChatRow = chatsContainerEl.querySelector('.sidebar-element');
    if (firstChatRow !== row) {
        chatsContainerEl.insertBefore(row, chatsContainerEl.firstElementChild);
    }
    refreshTimeDividers(chatsContainerEl);
    if (historySection) {
        historySection.style.display = '';
    }
}
window.moveChatRowToTop = moveChatRowToTop;
