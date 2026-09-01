// ===== Message Delete Modal =====
let pendingDeleteMessageContext = null;
let deleteMessageModalBound = false;
const DELETE_MESSAGE_GENERATION_WAIT_MS = 10000;
const DELETE_MESSAGE_GENERATION_POLL_MS = 150;

function getDeleteMessageModalElements() {
    const overlay = document.getElementById('deleteMessageOverlay');
    if (!overlay) {
        return null;
    }
    return {
        overlay,
        title: document.getElementById('deleteMessageTitle'),
        description: document.getElementById('deleteMessageDescription'),
        cancel: document.getElementById('deleteMessageCancelBtn'),
        confirm: document.getElementById('confirmDeleteMessageBtn'),
        confirmText: document.getElementById('deleteMessagePrimaryText'),
    };
}

function bindDeleteMessageModalHandlers() {
    if (deleteMessageModalBound) {
        return;
    }
    const els = getDeleteMessageModalElements();
    if (!els) {
        return;
    }
    deleteMessageModalBound = true;

    els.cancel?.addEventListener('click', () => {
        closeDeleteMessageModal();
    });

    els.overlay.addEventListener('click', (e) => {
        if (e.target === els.overlay) {
            closeDeleteMessageModal();
        }
    });

    els.confirm?.addEventListener('click', async () => {
        await confirmDeleteMessage();
    });

    if (typeof window !== 'undefined' && window.registerEscapeHandler) {
        window.registerEscapeHandler({
            id: 'delete-message-modal',
            priority: 101,
            isActive: () => {
                const modalEls = getDeleteMessageModalElements();
                return Boolean(modalEls && !modalEls.overlay.hidden);
            },
            close: () => {
                closeDeleteMessageModal();
            }
        });
    }
}

function getDeleteMessageModalIdleConfirmText() {
    if (pendingDeleteMessageContext?.role === 'assistant') {
        return getStreamText('chat_delete_assistant_message', 'Delete assistant message');
    }
    if (pendingDeleteMessageContext?.role === 'user') {
        return getStreamText('delete_message_and_below', 'Delete message and below');
    }
    return getStreamText('chat_delete_message', 'Delete message');
}

function setDeleteMessageModalBusy(isBusy, options = {}) {
    const els = getDeleteMessageModalElements();
    if (!els) {
        return;
    }
    if (els.cancel) {
        els.cancel.disabled = isBusy;
    }
    if (els.confirm) {
        els.confirm.disabled = isBusy;
    }
    if (els.confirmText) {
        els.confirmText.textContent = options.confirmText
            || (isBusy ? getStreamText('chat_delete_deleting', 'Deleting...') : getDeleteMessageModalIdleConfirmText());
    }
}

function openDeleteMessageModal({ messageId, role }) {
    bindDeleteMessageModalHandlers();
    const els = getDeleteMessageModalElements();
    if (!els) {
        if (typeof notifyError === 'function') {
            notifyError(getStreamText('chat_delete_confirmation_unavailable', 'Delete confirmation modal is unavailable'));
        }
        return;
    }

    const normalizedMessageId = String(messageId || '').trim();
    if (!normalizedMessageId) {
        if (typeof notifyError === 'function') {
            notifyError(getStreamText('chat_delete_missing_message_later_error', 'Cannot delete: message ID not available yet'));
        }
        return;
    }

    pendingDeleteMessageContext = {
        messageId: normalizedMessageId,
        role: role === 'assistant' ? 'assistant' : 'user',
    };

    if (els.title) {
        els.title.textContent = pendingDeleteMessageContext.role === 'assistant'
            ? getStreamText('chat_delete_assistant_message', 'Delete assistant message')
            : getStreamText('chat_delete_user_cascade_title', 'Delete this message and everything after it?');
    }
    if (els.description) {
        els.description.textContent = pendingDeleteMessageContext.role === 'assistant'
            ? getStreamText('chat_delete_assistant_description', 'This assistant message will be permanently removed from this chat.')
            : getStreamText('chat_delete_user_cascade_description', 'This will permanently remove this user message and every message below it in this chat. If a response is currently generating later in the conversation, it will be stopped first.');
    }

    setDeleteMessageModalBusy(false);
    els.overlay.hidden = false;
}

function closeDeleteMessageModal() {
    const els = getDeleteMessageModalElements();
    if (!els) {
        pendingDeleteMessageContext = null;
        return;
    }
    if (els.overlay) {
        els.overlay.hidden = true;
    }
    setDeleteMessageModalBusy(false);
    pendingDeleteMessageContext = null;
}

function getCurrentOpenChatId() {
    const chatContainer = document.getElementById('chatContainer');
    if (!chatContainer) {
        return '';
    }
    return String(chatContainer.getAttribute('data-chat-id') || '').trim();
}

function waitForDeleteMessageDelay(ms) {
    return new Promise((resolve) => {
        window.setTimeout(resolve, ms);
    });
}

async function getChatGenerationStatus(chatId) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId) {
        return null;
    }

    const params = new URLSearchParams({ chat_id: normalizedChatId });
    const response = await window.authedFetch(`/api/v1/chats/status?${params.toString()}`, {
        method: 'GET',
        headers: {
            accept: 'application/json',
        },
    });

    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const error = new Error(errorData.detail || getStreamTextFormatted(
            'chat_generation_status_fetch_failed_status',
            'Failed to fetch generation status (status {status})',
            { status: response.status }
        ));
        error.status = response.status;
        throw error;
    }

    return response.json();
}

async function requestGenerationCancel(generationId) {
    const normalizedGenerationId = String(generationId || '').trim();
    if (!normalizedGenerationId) {
        return false;
    }

    const params = new URLSearchParams({ generation_id: normalizedGenerationId });
    const response = await window.authedFetch(`/api/v1/chats/cancel?${params.toString()}`, {
        method: 'POST',
        headers: {
            accept: 'application/json',
        },
        body: '',
    });

    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const error = new Error(errorData.detail || getStreamTextFormatted(
            'chat_generation_stop_failed_status',
            'Failed to stop active generation (status {status})',
            { status: response.status }
        ));
        error.status = response.status;
        throw error;
    }

    return true;
}

async function waitForChatGenerationToStop(chatId, options = {}) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId) {
        return true;
    }

    const expectedGenerationId = String(options.expectedGenerationId || '').trim();
    const timeoutMs = Number.isFinite(options.timeoutMs) ? Number(options.timeoutMs) : DELETE_MESSAGE_GENERATION_WAIT_MS;
    const pollMs = Number.isFinite(options.pollMs) ? Number(options.pollMs) : DELETE_MESSAGE_GENERATION_POLL_MS;
    const startedAt = Date.now();

    while ((Date.now() - startedAt) < timeoutMs) {
        const status = await getChatGenerationStatus(normalizedChatId);
        const active = Boolean(status?.active);
        const currentGenerationId = String(status?.generation_id || '').trim();
        if (!active) {
            return true;
        }
        if (expectedGenerationId && currentGenerationId && currentGenerationId !== expectedGenerationId) {
            return true;
        }
        await waitForDeleteMessageDelay(pollMs);
    }

    const finalStatus = await getChatGenerationStatus(normalizedChatId).catch(() => null);
    const finalGenerationId = String(finalStatus?.generation_id || '').trim();
    return !finalStatus?.active || (
        Boolean(expectedGenerationId)
        && Boolean(finalGenerationId)
        && finalGenerationId !== expectedGenerationId
    );
}

async function deleteChatMessageRequest(messageId) {
    const params = new URLSearchParams({ message_id: messageId });
    const response = await window.authedFetch(`/api/v1/chats/messages/delete?${params.toString()}`, {
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json',
        }
    });

    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const error = new Error(errorData.detail || getStreamTextFormatted(
            'chat_delete_message_failed_status',
            'Failed to delete message (status {status})',
            { status: response.status }
        ));
        error.status = response.status;
        throw error;
    }

    const payload = await response.json().catch(() => null);
    if (Array.isArray(payload)) {
        return {
            chat_deleted: false,
            chat_id: getCurrentOpenChatId(),
            messages: payload,
        };
    }
    return payload;
}

async function deleteChatMessageWithGenerationRetry({ messageId, chatId, role }) {
    const shouldHandleGeneration = role === 'user' && String(chatId || '').trim();

    for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
            return await deleteChatMessageRequest(messageId);
        } catch (error) {
            if (!shouldHandleGeneration || error?.status !== 409 || attempt === 2) {
                throw error;
            }

            setDeleteMessageModalBusy(true, { confirmText: getStreamText('chat_stopping_generation_tooltip', 'Stopping response...') });
            const stopped = await waitForChatGenerationToStop(chatId, {
                timeoutMs: DELETE_MESSAGE_GENERATION_WAIT_MS,
                pollMs: DELETE_MESSAGE_GENERATION_POLL_MS,
            });
            if (!stopped && attempt === 2) {
                throw error;
            }
        }
    }

    return null;
}

async function confirmDeleteMessage() {
    if (!pendingDeleteMessageContext || !pendingDeleteMessageContext.messageId) {
        closeDeleteMessageModal();
        return;
    }
    const { messageId, role } = pendingDeleteMessageContext;
    setDeleteMessageModalBusy(true);
    const chatArea = document.getElementById('chatArea');
    const wasNearBottom = chatArea
        ? (chatArea.scrollHeight - chatArea.clientHeight - chatArea.scrollTop) <= 80
        : true;
    const activeChatId = getCurrentOpenChatId();

    try {
        if (role === 'user' && activeChatId) {
            const status = await getChatGenerationStatus(activeChatId).catch(() => null);
            const generationId = String(status?.generation_id || '').trim();
            if (status?.active && generationId) {
                setDeleteMessageModalBusy(true, { confirmText: getStreamText('chat_stopping_generation_tooltip', 'Stopping response...') });
                await requestGenerationCancel(generationId);
                const stopped = await waitForChatGenerationToStop(activeChatId, {
                    expectedGenerationId: generationId,
                    timeoutMs: DELETE_MESSAGE_GENERATION_WAIT_MS,
                    pollMs: DELETE_MESSAGE_GENERATION_POLL_MS,
                });
                if (!stopped) {
                    throw new Error(getStreamText('chat_generation_stop_timeout', 'Timed out while waiting for the active generation to stop.'));
                }
            }
        }

        setDeleteMessageModalBusy(true, { confirmText: getStreamText('chat_delete_deleting', 'Deleting...') });
        const deleteResult = await deleteChatMessageWithGenerationRetry({
            messageId,
            chatId: activeChatId,
            role,
        });
        closeDeleteMessageModal();
        const chatDeleted = Boolean(deleteResult?.chat_deleted);
        const updatedMessages = Array.isArray(deleteResult?.messages) ? deleteResult.messages : [];

        const messagesContainer = document.getElementById('chatAreaContainer');
        if (chatDeleted) {
            if (typeof window.showChatStartContainer === 'function') {
                window.showChatStartContainer();
            } else {
                const chatContainer = document.getElementById('chatContainer');
                chatContainer?.removeAttribute('data-chat-id');
            }
        } else if (messagesContainer && Array.isArray(updatedMessages) && typeof window.renderChatTranscript === 'function') {
            window.renderChatTranscript(updatedMessages, {
                container: messagesContainer,
                clearContainer: true,
                trackAssistantVersions: true,
                readOnly: false,
            });
            if (wasNearBottom && typeof scrollChatToBottom === 'function') {
                scrollChatToBottom();
            }
        } else {
            const chatContainer = document.getElementById('chatContainer');
            if (activeChatId && chatContainer && typeof loadChatView === 'function') {
                chatContainer.removeAttribute('data-chat-id');
                await loadChatView(activeChatId, true);
            }
        }

        if (typeof initChatList === 'function') {
            await initChatList();
        }

        if (typeof notifySuccess === 'function') {
            if (chatDeleted) {
                notifySuccess(getStreamText('chat_deleted_success', 'Chat deleted'));
            } else {
                notifySuccess(role === 'assistant'
                    ? getStreamText('chat_assistant_message_deleted', 'Assistant message deleted')
                    : getStreamText('chat_message_and_following_deleted', 'Message and all following messages deleted'));
            }
        }
    } catch (error) {
        console.error('Failed to delete message:', error);
        if (typeof notifyError === 'function') {
            notifyError(error.message || getStreamText('chat_delete_message_failed', 'Failed to delete message'));
        }
        setDeleteMessageModalBusy(false);
    }
}


