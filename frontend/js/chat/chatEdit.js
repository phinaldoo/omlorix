if (typeof activeChatActionContext === 'undefined') {
    window.activeChatActionContext = null;
}
if (typeof previousChatLayoutState === 'undefined') {
    window.previousChatLayoutState = null;
}

function chatEditT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function chatEditFormatT(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(chatEditT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

function showEditChatModal(chat) {
    const overlay = document.getElementById('editChatOverlay');
    if (!overlay) return;

    const title = chat?.title ?? '';
    activeChatActionContext = {
        id: chat?.id ?? null,
        title,
        mode: 'edit'
    };

    const titleEl = document.getElementById('editChatModalTitle');
    if (titleEl) {
        titleEl.textContent = title
            ? chatEditFormatT('chat_edit_title_named', 'Edit Chat "{title}"', { title })
            : chatEditT('chat_edit_title', 'Edit Chat');
    }

    const inputEl = document.getElementById('editChatNameInput');
    if (inputEl) {
        inputEl.value = title;
        requestAnimationFrame(() => inputEl.focus());
    }

    overlay.hidden = false;
}

function hideEditChatModal() {
    const overlay = document.getElementById('editChatOverlay');
    if (overlay) {
        overlay.hidden = true;
    }
    if (activeChatActionContext?.mode === 'edit') {
        activeChatActionContext = null;
    }
}

function showChatEditContainer(chat) {
    showEditChatModal(chat);
}

function showDeleteChatModal(chat) {
    const overlay = document.getElementById('deleteChatOverlay');
    if (!overlay) return;

    const title = chat?.title ?? '';
    activeChatActionContext = {
        id: chat?.id ?? null,
        title,
        mode: 'delete'
    };

    const titleEl = document.getElementById('deleteChatTitle');
    if (titleEl) {
        titleEl.textContent = title
            ? chatEditFormatT('chat_delete_title_named', 'Delete "{title}"', { title })
            : chatEditT('delete_chat_modal_title', 'Delete Chat');
    }

    overlay.hidden = false;
}

function hideDeleteChatModal() {
    const overlay = document.getElementById('deleteChatOverlay');
    if (overlay) {
        overlay.hidden = true;
    }
    if (activeChatActionContext?.mode === 'delete') {
        activeChatActionContext = null;
    }
}

function showChatDeleteContainer(chat) {
    showDeleteChatModal(chat);
}

const editChatCancelBtn = document.getElementById('editChatCancelBtn');
if (editChatCancelBtn) {
    editChatCancelBtn.addEventListener('click', () => {
        hideEditChatModal();
    });
}

const editChatOverlay = document.getElementById('editChatOverlay');
if (editChatOverlay) {
    editChatOverlay.addEventListener('click', (e) => {
        if (e.target === editChatOverlay) {
            hideEditChatModal();
        }
    });
}

const deleteChatCancelBtn = document.getElementById('deleteChatCancelBtn');
if (deleteChatCancelBtn) {
    deleteChatCancelBtn.addEventListener('click', () => {
        hideDeleteChatModal();
    });
}

const deleteChatOverlay = document.getElementById('deleteChatOverlay');
if (deleteChatOverlay) {
    deleteChatOverlay.addEventListener('click', (e) => {
        if (e.target === deleteChatOverlay) {
            hideDeleteChatModal();
        }
    });
}

const editChatNameInput = document.getElementById('editChatNameInput');
const confirmEditChatBtn = document.getElementById('confirmEditChatBtn');

if (editChatNameInput && confirmEditChatBtn) {
    editChatNameInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            confirmEditChatBtn.click();
        }
    });
}

if (confirmEditChatBtn) {
    confirmEditChatBtn.addEventListener('click', async () => {
        if (!activeChatActionContext || activeChatActionContext.mode !== 'edit' || !activeChatActionContext.id) {
            hideEditChatModal();
            return;
        }
        const newTitle = (editChatNameInput?.value || '').trim();
        if (!newTitle) {
            editChatNameInput?.focus();
            return;
        }
        if (newTitle === (activeChatActionContext.title || '')) {
            hideEditChatModal();
            return;
        }
        confirmEditChatBtn.disabled = true;
        if (editChatCancelBtn) editChatCancelBtn.disabled = true;

        try {
            const params = new URLSearchParams({ chat_id: activeChatActionContext.id, title: newTitle });
            const res = await window.authedFetch(`/api/v1/chats/rename?${params.toString()}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                }
            });
            if (res.ok) {
                activeChatActionContext.title = newTitle;
                initChatList();
                hideEditChatModal();
            } else {
                console.error(`Failed to edit chat (status ${res.status})`);
                notifyError?.(chatEditFormatT(
                    'chat_edit_failed_status',
                    'Failed to edit chat (status {status})',
                    { status: res.status }
                ));
            }
        } catch (err) {
            console.error('Error editing chat', err);
            notifyError?.(chatEditT('chat_edit_error', 'Error editing chat'));
        } finally {
            confirmEditChatBtn.disabled = false;
            if (editChatCancelBtn) editChatCancelBtn.disabled = false;
        }
    });
}

const confirmDeleteChatBtn = document.getElementById('confirmDeleteChatBtn');
if (confirmDeleteChatBtn) {
    confirmDeleteChatBtn.addEventListener('click', async () => {
        if (!activeChatActionContext || activeChatActionContext.mode !== 'delete' || !activeChatActionContext.id) {
            hideDeleteChatModal();
            return;
        }
        confirmDeleteChatBtn.disabled = true;
        if (deleteChatCancelBtn) deleteChatCancelBtn.disabled = true;

        try {
            const params = new URLSearchParams({ chat_id: activeChatActionContext.id });
            const res = await window.authedFetch(`/api/v1/chats/delete?${params.toString()}`, {
                method: 'DELETE',
                headers: {
                    'Content-Type': 'application/json',
                }
            });
            if (res.ok) {
                hideDeleteChatModal();
                try {
                    if (typeof getActiveChatId === 'function' && typeof showChatStartContainer === 'function') {
                        const activeChatId = getActiveChatId();
                        if (activeChatId && String(activeChatId) === String(activeChatActionContext.id)) {
                            showChatStartContainer();
                        }
                    }
                } catch (_) { /* no-op */ }
                initChatList();
                if (typeof showChatStartContainer === 'function') {
                    showChatStartContainer();
                }
            } else {
                console.error(`Failed to delete chat (status ${res.status})`);
                notifyError?.(chatEditFormatT(
                    'chat_delete_failed_status',
                    'Failed to delete chat (status {status})',
                    { status: res.status }
                ));
            }
        } catch (err) {
            console.error('Error deleting chat', err);
            notifyError?.(chatEditT('chat_delete_error', 'Error deleting chat'));
        } finally {
            confirmDeleteChatBtn.disabled = false;
            if (deleteChatCancelBtn) deleteChatCancelBtn.disabled = false;
        }
    });
}

// Register escape handler for edit chat modal
if (typeof window !== 'undefined' && window.registerEscapeHandler) {
    window.registerEscapeHandler({
        id: 'edit-chat-modal',
        priority: 100,
        isActive: () => {
            const overlay = document.getElementById('editChatOverlay');
            return overlay && !overlay.hidden;
        },
        close: () => {
            hideEditChatModal();
        }
    });
}

// Export functions
if (typeof window !== 'undefined') {
    window.showEditChatModal = showEditChatModal;
    window.hideEditChatModal = hideEditChatModal;
}
