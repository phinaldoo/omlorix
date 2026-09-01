// Cross-device unread assistant-response state.
//
// The backend owns the durable response/read versions. This module only
// mirrors that state into every rendered sidebar row and coordinates the
// small read/synchronization requests needed by the current browser.
(function initChatAttentionModule() {
    const SYNC_INTERVAL_MS = 30000;
    const MAX_SYNC_CHAT_IDS = 200;
    const unreadByChatId = new Map();
    const pendingReadRequests = new Map();
    const trackedGenerations = new Map();
    const announcedGenerations = new Set();
    let syncInFlight = null;

    function attentionT(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function normalizeChatId(chatId) {
        return String(chatId || '').trim();
    }

    function getChatTitle(chatId) {
        const normalizedChatId = normalizeChatId(chatId);
        if (!normalizedChatId) return attentionT('sidebar_untitled_chat', 'Untitled chat');
        const row = Array.from(document.querySelectorAll('.sidebar-element[data-chat-id]'))
            .find((element) => element.dataset.chatId === normalizedChatId);
        return row?.dataset.chatTitle
            || row?.querySelector('.chat-title-text')?.textContent?.trim()
            || row?.querySelector('.chat-title-with-badge')?.textContent?.trim()
            || attentionT('sidebar_untitled_chat', 'Untitled chat');
    }

    function createUnreadIndicator() {
        const indicator = document.createElement('span');
        indicator.className = 'chat-unread-indicator';

        const dot = document.createElement('span');
        dot.className = 'chat-unread-dot';
        dot.setAttribute('aria-hidden', 'true');

        const label = document.createElement('span');
        label.className = 'sr-only';
        label.textContent = attentionT('sidebar_chat_unread_response', 'Unread assistant response');

        indicator.append(dot, label);
        return indicator;
    }

    function decorateRow(row, unread) {
        if (!row) return;
        const anchor = row.querySelector('a.sidebar-element-button');
        if (!anchor) return;

        let indicator = anchor.querySelector('.chat-unread-indicator');
        if (unread && !indicator) {
            indicator = createUnreadIndicator();
            anchor.appendChild(indicator);
        } else if (!unread && indicator) {
            indicator.remove();
        }
        row.classList.toggle('has-unread-response', Boolean(unread));
    }

    function updateRenderedRows(chatId) {
        const normalizedChatId = normalizeChatId(chatId);
        if (!normalizedChatId) return;
        const unread = unreadByChatId.get(normalizedChatId) === true;
        document.querySelectorAll('.sidebar-element[data-chat-id]').forEach((row) => {
            if (row.dataset.chatId === normalizedChatId) {
                decorateRow(row, unread);
            }
        });
    }

    function setUnread(chatId, unread) {
        const normalizedChatId = normalizeChatId(chatId);
        if (!normalizedChatId) return;
        unreadByChatId.set(normalizedChatId, Boolean(unread));
        updateRenderedRows(normalizedChatId);
    }

    function registerChat(chat) {
        const chatId = normalizeChatId(chat?.id ?? chat?.chat_id);
        if (!chatId) return;
        if (Object.prototype.hasOwnProperty.call(chat || {}, 'has_unread_response')) {
            unreadByChatId.set(chatId, chat.has_unread_response === true);
        }
        updateRenderedRows(chatId);
    }

    function isChatVisible(chatId) {
        const normalizedChatId = normalizeChatId(chatId);
        if (!normalizedChatId) return false;
        const split = window.SplitScreenManager;
        if (split?.active) {
            return normalizeChatId(split.leftChatId) === normalizedChatId
                || normalizeChatId(split.rightChatId) === normalizedChatId;
        }
        return normalizeChatId(document.getElementById('chatContainer')?.getAttribute('data-chat-id')) === normalizedChatId;
    }

    async function markRead(chatId) {
        const normalizedChatId = normalizeChatId(chatId);
        if (!normalizedChatId) return false;
        if (pendingReadRequests.has(normalizedChatId)) {
            return pendingReadRequests.get(normalizedChatId);
        }

        const request = (async () => {
            try {
                const response = await window.authedFetch(`/api/v1/chats/${encodeURIComponent(normalizedChatId)}/read`, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                    body: '',
                });
                if (!response.ok) return false;
                setUnread(normalizedChatId, false);
                return true;
            } catch (error) {
                console.error('Failed to mark chat response as read', error);
                return false;
            } finally {
                pendingReadRequests.delete(normalizedChatId);
            }
        })();
        pendingReadRequests.set(normalizedChatId, request);
        return request;
    }

    async function handleGenerationCompleted(chatId, title = '', generationId = '') {
        const normalizedChatId = normalizeChatId(chatId);
        if (!normalizedChatId) return;
        const normalizedGenerationId = String(generationId || '').trim();
        if (normalizedGenerationId && announcedGenerations.has(normalizedGenerationId)) return;
        if (normalizedGenerationId) {
            announcedGenerations.add(normalizedGenerationId);
            if (announcedGenerations.size > 500) {
                const oldestGenerationId = announcedGenerations.values().next().value;
                announcedGenerations.delete(oldestGenerationId);
            }
        }
        trackedGenerations.delete(normalizedChatId);
        if (isChatVisible(normalizedChatId)) {
            await markRead(normalizedChatId);
            return;
        }

        setUnread(normalizedChatId, true);
        if (typeof window.notifyInfo === 'function') {
            const chatTitle = String(title || getChatTitle(normalizedChatId)).trim();
            const message = typeof window.formatTranslation === 'function'
                ? window.formatTranslation(
                    'chat_response_ready_toast',
                    'Response ready in “{title}”.',
                    { title: chatTitle },
                )
                : `Response ready in “${chatTitle}”.`;
            window.notifyInfo(message, {
                duration: 8000,
                actionLabel: attentionT('chat_response_ready_open', 'Open chat'),
                onAction: async () => {
                    if (typeof window.loadChatView === 'function') {
                        await window.loadChatView(normalizedChatId);
                    }
                },
            });
        }
    }

    function getRenderedChatIds() {
        const ids = [];
        const seen = new Set();
        document.querySelectorAll('.sidebar-element[data-chat-id]').forEach((row) => {
            const chatId = normalizeChatId(row.dataset.chatId);
            if (chatId && !seen.has(chatId) && ids.length < MAX_SYNC_CHAT_IDS) {
                seen.add(chatId);
                ids.push(chatId);
            }
        });
        return ids;
    }

    async function queryAttention(chatIds) {
        const normalizedIds = Array.from(new Set((chatIds || []).map(normalizeChatId).filter(Boolean)))
            .slice(0, MAX_SYNC_CHAT_IDS);
        if (!normalizedIds.length) return {};
        const response = await window.authedFetch('/api/v1/chats/attention/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chat_ids: normalizedIds }),
        });
        if (!response.ok) return {};
        const data = await response.json();
        return data?.unread_by_chat_id || {};
    }

    async function syncRenderedChats() {
        if (syncInFlight) return syncInFlight;
        const chatIds = getRenderedChatIds();
        if (!chatIds.length) return false;

        syncInFlight = (async () => {
            try {
                const flags = await queryAttention(chatIds);
                Object.entries(flags).forEach(([chatId, unread]) => setUnread(chatId, unread === true));
                return true;
            } catch (error) {
                console.error('Failed to synchronize chat attention state', error);
                return false;
            } finally {
                syncInFlight = null;
            }
        })();
        return syncInFlight;
    }

    function trackGeneration(chatId, generationId) {
        const normalizedChatId = normalizeChatId(chatId);
        const normalizedGenerationId = String(generationId || '').trim();
        if (!normalizedChatId || !normalizedGenerationId) return;
        trackedGenerations.set(normalizedChatId, normalizedGenerationId);

        const poll = async () => {
            if (trackedGenerations.get(normalizedChatId) !== normalizedGenerationId) return;
            try {
                const params = new URLSearchParams({ chat_id: normalizedChatId });
                const response = await window.authedFetch(`/api/v1/chats/status?${params.toString()}`, { method: 'GET' });
                if (!response.ok) {
                    trackedGenerations.delete(normalizedChatId);
                    return;
                }
                const status = await response.json();
                if (status?.active && String(status.generation_id || '') === normalizedGenerationId) {
                    window.setTimeout(poll, 2000);
                    return;
                }

                trackedGenerations.delete(normalizedChatId);
                const flags = await queryAttention([normalizedChatId]);
                const unread = flags[normalizedChatId] === true;
                if (unread) {
                    await handleGenerationCompleted(normalizedChatId, '', normalizedGenerationId);
                } else {
                    setUnread(normalizedChatId, false);
                }
            } catch (error) {
                console.error('Failed to monitor background chat generation', error);
                if (trackedGenerations.get(normalizedChatId) === normalizedGenerationId) {
                    window.setTimeout(poll, 4000);
                }
            }
        };
        window.setTimeout(poll, 2000);
    }

    function handlePageReturn() {
        if (document.visibilityState !== 'hidden') {
            syncRenderedChats();
        }
    }

    document.addEventListener('visibilitychange', handlePageReturn);
    window.addEventListener('focus', handlePageReturn);
    window.addEventListener('pageshow', handlePageReturn);
    window.setInterval(() => {
        if (document.visibilityState !== 'hidden') {
            syncRenderedChats();
        }
    }, SYNC_INTERVAL_MS);

    window.ChatAttention = {
        registerChat,
        decorateRow: (row, chatId) => decorateRow(row, unreadByChatId.get(normalizeChatId(chatId)) === true),
        setUnread,
        isUnread: (chatId) => unreadByChatId.get(normalizeChatId(chatId)) === true,
        isChatVisible,
        markRead,
        handleGenerationCompleted,
        trackGeneration,
        syncRenderedChats,
        updateRenderedRows,
    };
})();
