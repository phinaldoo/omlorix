// Pin a chat at a given position (default 1 = top)
function chatPinT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function chatPinTf(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(chatPinT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

async function pinChat(chatId, position = 1) {
    try {
        // Validate chatId
        if (!chatId || chatId === 'undefined' || chatId === 'null') {
            console.error('Invalid chatId for pin operation:', chatId);
            if (typeof notifyError === 'function') notifyError(chatPinT('chat_pin_invalid_chat_id', 'Invalid chat ID'));
            return;
        }

        const res = await window.authedFetch(`/api/v1/chats/pin`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ chat_id: String(chatId), position: Number(position) })
        });

        if (!res.ok) {
            const errorText = await res.text().catch(() => chatPinT('chat_pin_unknown_error', 'Unknown error'));
            notifyError(chatPinTf('chat_pin_http_error_status', 'HTTP {status}: {error}', {
                status: res.status,
                error: errorText,
            }));
        }

    } catch (err) {
        const errorMessage = err.message || chatPinT('chat_pin_failed', 'Failed to pin chat');
        if (typeof notifyError === 'function') {
            notifyError(errorMessage);
        }
    } finally {
        initChatList();
        if (typeof window.refreshProjectChatSidebarChats === 'function') {
            window.refreshProjectChatSidebarChats();
        }
    }
}

// Unpin a chat
async function unpinChat(chatId) {
    try {
        // Validate chatId
        if (!chatId || chatId === 'undefined' || chatId === 'null') {
            console.error('Invalid chatId for unpin operation:', chatId);
            if (typeof notifyError === 'function') notifyError(chatPinT('chat_pin_invalid_chat_id', 'Invalid chat ID'));
            return;
        }

        const res = await window.authedFetch(`/api/v1/chats/unpin`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ chat_id: String(chatId) })
        });

        if (!res.ok) {
            const errorText = await res.text().catch(() => chatPinT('chat_pin_unknown_error', 'Unknown error'));
            console.error('Unpin failed with status:', res.status, 'Error:', errorText);
            notifyError(chatPinTf('chat_pin_http_error_status', 'HTTP {status}: {error}', {
                status: res.status,
                error: errorText,
            }));
        }

    } catch (err) {
        console.error('Unpin chat error:', err);
        const errorMessage = err.message || chatPinT('chat_unpin_failed', 'Failed to unpin chat');
        if (typeof notifyError === 'function') {
            notifyError(errorMessage);
        } else {
            console.error(chatPinTf('chat_pin_alert_error', 'Error: {error}', { error: errorMessage }));
        }
    } finally {
        initChatList();
        if (typeof window.refreshProjectChatSidebarChats === 'function') {
            window.refreshProjectChatSidebarChats();
        }
    }
}

// Move a pinned chat to a new position
async function moveChat(chatId, position) {
    try {
        // Validate chatId
        if (!chatId || chatId === 'undefined' || chatId === 'null') {
            console.error('Invalid chatId for move operation:', chatId);
            if (typeof notifyError === 'function') notifyError(chatPinT('chat_pin_invalid_chat_id', 'Invalid chat ID'));
            return;
        }

        const res = await window.authedFetch(`/api/v1/chats/pin/move`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ chat_id: String(chatId), position: Number(position) })
        });

        if (!res.ok) {
            const errorText = await res.text().catch(() => chatPinT('chat_pin_unknown_error', 'Unknown error'));
            console.error('Move failed with status:', res.status, 'Error:', errorText);
            notifyError(chatPinTf('chat_pin_http_error_status', 'HTTP {status}: {error}', {
                status: res.status,
                error: errorText,
            }));
        }

    } catch (err) {
        console.error('Move chat error:', err);
        const errorMessage = err.message || chatPinT('chat_move_failed', 'Failed to move chat');
        if (typeof notifyError === 'function') {
            notifyError(errorMessage);
        } else {
            console.error(chatPinTf('chat_pin_alert_error', 'Error: {error}', { error: errorMessage }));
        }
    } finally {
        initChatList();
        if (typeof window.refreshProjectChatSidebarChats === 'function') {
            window.refreshProjectChatSidebarChats();
        }
    }
}
