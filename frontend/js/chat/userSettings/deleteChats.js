(() => {
    const state = {
        overlay: null,
        card: null,
        openButton: null,
        cancelButton: null,
        confirmButton: null,
        confirmText: null,
        title: null,
        description: null,
        cancelText: null,
        defaultConfirmLabel: 'Delete All Chats',
        defaultTitle: 'Delete All Chats',
        defaultDescription: 'Are you sure you want to delete all chats?',
        defaultCancelLabel: 'Cancel',
        lastFocusedElement: null,
        isProcessing: false
    };

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    const isShadowDeletionEnabled = () => {
        if (window.chatSetup && typeof window.chatSetup === 'object') {
            return window.chatSetup.shadow_chat_deletion === true;
        }
        try {
            return localStorage.getItem('shadow_chat_deletion') === 'true';
        } catch (error) {
            return false;
        }
    };

    const getPolicyCopy = () => {
        if (!isShadowDeletionEnabled()) {
            return {
                title: t('delete_all_chats_title', state.defaultTitle),
                description: t('delete_all_chats_confirm', state.defaultDescription),
                confirmLabel: t('delete_all_chats_confirm_button', state.defaultConfirmLabel),
                processingLabel: t('delete_all_chats_deleting', 'Deleting...'),
                success: t('delete_all_chats_success', 'Chats deleted successfully'),
                error: t('delete_all_chats_error', 'Failed to delete chats'),
            };
        }

        return {
            title: t('delete_all_chats_shadow_title', 'Hide All Chats'),
            description: t(
                'delete_all_chats_shadow_confirm',
                'Your chats will be removed from your chat list, but their content may be retained according to your administrator\'s retention policy.'
            ),
            confirmLabel: t('delete_all_chats_shadow_confirm_button', 'Hide All Chats'),
            processingLabel: t('delete_all_chats_shadow_processing', 'Hiding...'),
            success: t('delete_all_chats_shadow_success', 'Chats hidden successfully'),
            error: t('delete_all_chats_error', 'Failed to delete chats'),
        };
    };

    const applyPolicyCopy = () => {
        const copy = getPolicyCopy();
        if (state.title) {
            state.title.textContent = copy.title;
        }
        if (state.description) {
            state.description.textContent = copy.description;
        }
        if (state.confirmText && !state.isProcessing) {
            state.confirmText.textContent = copy.confirmLabel;
        }
    };

    async function deleteChats() {
        const copy = getPolicyCopy();
        try {
            const res = await window.authedFetch('/api/v1/chats/delete/all', {
                method: 'DELETE',
            });
            if (!res.ok) {
                notifyError(copy.error);
                return false;
            }
            notifySuccess(copy.success);
            showChatStartContainer();
            window.initChatList();
            return true;
        } catch (error) {
            notifyError(copy.error);
            return false;
        }
    }



    const setProcessingState = (isProcessing) => {
        state.isProcessing = isProcessing;

        if (state.confirmButton) {
            state.confirmButton.disabled = isProcessing;
        }
        if (state.cancelButton) {
            state.cancelButton.disabled = isProcessing;
        }
        if (state.confirmText) {
            const copy = getPolicyCopy();
            state.confirmText.textContent = isProcessing ? copy.processingLabel : copy.confirmLabel;
        }
    };

    const closeModal = () => {
        if (!state.overlay || state.overlay.hasAttribute('hidden')) {
            return;
        }

        state.overlay.setAttribute('hidden', '');
        document.removeEventListener('keydown', handleKeydown);
        state.overlay.removeEventListener('click', handleBackdropClick);
        setProcessingState(false);
    };

    const handleKeydown = (event) => {
        if (event.key === 'Escape' && !state.isProcessing) {
            event.preventDefault();
            closeModal();
        }
    };

    const handleBackdropClick = (event) => {
        if (event.target === state.overlay && !state.isProcessing) {
            closeModal();
        }
    };

    const openModal = () => {
        if (!state.overlay || !state.overlay.hasAttribute('hidden')) {
            return;
        }

        state.overlay.removeAttribute('hidden');
        document.addEventListener('keydown', handleKeydown);
        state.overlay.addEventListener('click', handleBackdropClick);
        applyPolicyCopy();
        setProcessingState(false);
    };

    const handleCancel = (event) => {
        if (event) {
            event.preventDefault();
        }
        if (!state.isProcessing) {
            closeModal();
        }
    };

    const handleConfirm = async (event) => {
        if (event) {
            event.preventDefault();
        }
        if (state.isProcessing) {
            return;
        }

        setProcessingState(true);

        let success = false;
        try {
            success = await deleteChats();
        } catch (error) {
            console.error('[deleteChats] Unexpected error while confirming deletion', error);
            success = false;
        }

        if (success) {
            closeModal();
            return;
        }

        setProcessingState(false);
    };

    const init = () => {
        state.overlay = document.getElementById('deleteAllChatsOverlay');
        state.card = state.overlay?.querySelector('.delete-warning-card');
        state.openButton = document.getElementById('deleteAllChatsButton');
        state.cancelButton = document.getElementById('deleteAllChatsCancelButton');
        state.confirmButton = document.getElementById('deleteAllChatsPrimaryButton');
        state.confirmText = document.getElementById('deleteAllChatsPrimaryText');
        state.title = document.getElementById('deleteAllChatsHeaderTitle');
        state.description = document.getElementById('deleteAllChatsDescription');
        state.cancelText = document.getElementById( 'deleteAllChatsCancelText');

        if (!state.overlay || !state.openButton || !state.cancelButton || !state.confirmButton) {
            return;
        }

        if (state.card) {
            state.card.setAttribute('role', 'dialog');
            state.card.setAttribute('aria-modal', 'true');
            state.card.setAttribute('aria-labelledby', 'deleteAllChatsHeaderTitle');
            state.card.setAttribute('aria-describedby', 'deleteAllChatsDescription');
        }

        if (state.title && state.title.textContent) {
            state.defaultTitle = state.title.textContent;
        }
        if (state.description && state.description.textContent) {
            state.defaultDescription = state.description.textContent;
        }
        if (state.confirmText && state.confirmText.textContent) {
            state.defaultConfirmLabel = state.confirmText.textContent;
        }

        if (state.cancelText && state.cancelText.textContent) {
            state.defaultCancelLabel = state.cancelText.textContent;
        }

        state.openButton.addEventListener('click', (event) => {
            event.preventDefault();
            openModal();
        });

        state.cancelButton.addEventListener('click', handleCancel);
        state.confirmButton.addEventListener('click', handleConfirm);
        document.addEventListener('i18n:updated', applyPolicyCopy);
        applyPolicyCopy();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
