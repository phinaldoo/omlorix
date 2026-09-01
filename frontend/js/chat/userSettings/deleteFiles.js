function deleteFilesT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function deleteFilesFormatT(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return Object.entries(vars).reduce(
        (message, [name, value]) => message.replace(new RegExp(`\\{${name}\\}`, 'g'), String(value)),
        fallback
    );
}

async function parseDeleteFilesResponse(res) {
    try {
        return await res.json();
    } catch (error) {
        return null;
    }
}

function buildDeleteFilesErrorMessage(payload) {
    const detail = payload?.detail && typeof payload.detail === 'object' ? payload.detail : payload;
    const errorCount = Array.isArray(detail?.errors) ? detail.errors.length : 0;
    const deletedCount = Number(detail?.deleted_count || 0);

    if (errorCount > 0) {
        return deleteFilesFormatT(
            'delete_files_error_partial',
            'Deleted {deletedCount} files, but {errorCount} could not be deleted.',
            { deletedCount, errorCount }
        );
    }

    if (typeof payload?.detail === 'string' && payload.detail) {
        return payload.detail;
    }

    return deleteFilesT('delete_files_error_failed', 'Failed to delete files.');
}

async function deleteFiles(options = {}) {
    const { scope = 'all', time = 'all' } = options;
    try {
        let res;
        if (scope === 'websearch') {
            res = await window.authedFetch('/api/v1/files/all?delete_all=false', {
                method: 'DELETE',
            });
        } else {
            const params = new URLSearchParams();
            if (time) {
                params.set('time', time);
            }

            const endpoint = params.toString()
                ? `/api/v1/files?${params.toString()}`
                : '/api/v1/files';

            res = await window.authedFetch(endpoint, {
                method: 'DELETE',
            });
        }

        const payload = await parseDeleteFilesResponse(res);

        if (!res.ok) {
            notifyError(buildDeleteFilesErrorMessage(payload));
            await FilesManager.refresh(true);
            return false;
        }

        if (Array.isArray(payload?.errors) && payload.errors.length > 0) {
            notifyError(buildDeleteFilesErrorMessage(payload));
            await FilesManager.refresh(true);
            return false;
        }

        if (typeof window.handleFilesDeletedForChat === 'function') {
            try {
                window.handleFilesDeletedForChat({
                    clearAll: scope !== 'websearch',
                    fileIds: [],
                });
            } catch (error) {
                console.error('handleFilesDeletedForChat threw during bulk delete', error);
            }
        }

        if (scope === 'websearch') {
            notifySuccess(deleteFilesT('delete_files_success_websearch', 'Deleted web search files.'));
        } else {
            notifySuccess(deleteFilesT('delete_files_success_all', 'Files deleted successfully.'));
        }
        await FilesManager.refresh(true);
        return true;
    } catch (error) {
        notifyError(deleteFilesT('delete_files_error_failed', 'Failed to delete files.'));
        return false;
    }
}

(() => {
    const SELECTORS = {
        overlayId: 'deleteAllFilesOverlay',
        openButtonId: 'deleteAllFilesButton', // This needs to match the HTML button ID
        cancelButtonId: 'deleteAllFilesCancelButton',
        confirmButtonId: 'deleteAllFilesPrimaryButton',
        confirmTextId: 'deleteAllFilesPrimaryText',
        cancelTextId: 'deleteAllFilesCancelText',
        cardSelector: '.delete-warning-card',
        timeGroupId: 'deleteAllFilesTimeGroup',
        timeSelectId: 'deleteAllFilesTimeSelect',
        timeHiddenInputId: 'deleteAllFilesTimeValue',
        scopeSelectId: 'deleteAllFilesScopeSelect',
        scopeHiddenInputId: 'deleteAllFilesScopeValue'
    };

    const state = {
        overlay: null,
        card: null,
        openButton: null,
        cancelButton: null,
        confirmButton: null,
        confirmText: null,
        cancelText: null,
        timeGroup: null,
        timeSelect: null,
        timeHiddenInput: null,
        scopeSelect: null,
        scopeHiddenInput: null,
        defaultConfirmLabel: 'Delete Files',
        defaultCancelLabel: 'Cancel',
        lastFocusedElement: null,
        isProcessing: false
    };

    const updateHiddenTimeValue = (value = 'all') => {
        if (state.timeHiddenInput) {
            state.timeHiddenInput.value = value || 'all';
        }
    };

    const updateHiddenScopeValue = (value = 'all') => {
        if (state.scopeHiddenInput) {
            state.scopeHiddenInput.value = value || 'all';
        }
    };

    const updateTimeGroupVisibility = (scopeValue) => {
        const shouldShowTime = scopeValue !== 'websearch';
        if (state.timeGroup) {
            state.timeGroup.style.display = shouldShowTime ? '' : 'none';
        }

        if (!shouldShowTime) {
            updateHiddenTimeValue('all');
        }
    };

    const getSelectedTimeOption = () => {
        if (typeof window.getCustomSelectValue === 'function') {
            const value = window.getCustomSelectValue(state.timeSelect);
            if (value) {
                return value;
            }
        }

        const selectedOption = state.timeSelect?.querySelector('.select-option.selected');
        if (selectedOption?.dataset?.value) {
            return selectedOption.dataset.value;
        }

        if (state.timeHiddenInput?.value) {
            return state.timeHiddenInput.value;
        }

        return 'all';
    };

    const getSelectedScopeOption = () => {
        if (typeof window.getCustomSelectValue === 'function') {
            const value = window.getCustomSelectValue(state.scopeSelect);
            if (value) {
                return value;
            }
        }

        const selectedOption = state.scopeSelect?.querySelector('.select-option.selected');
        if (selectedOption?.dataset?.value) {
            return selectedOption.dataset.value;
        }

        if (state.scopeHiddenInput?.value) {
            return state.scopeHiddenInput.value;
        }

        return 'all';
    };

    const focusElement = (element) => {
        if (!element || typeof element.focus !== 'function') {
            return;
        }

        try {
            element.focus({ preventScroll: true });
        } catch (error) {
            element.focus();
        }
    };

    const setProcessingState = (isProcessing) => {
        state.isProcessing = isProcessing;

        if (state.confirmButton) {
            state.confirmButton.disabled = isProcessing;
        }
        if (state.cancelButton) {
            state.cancelButton.disabled = isProcessing;
        }
        if (state.confirmText) {
            state.confirmText.textContent = isProcessing
                ? deleteFilesT('delete_files_deleting', 'Deleting...')
                : state.defaultConfirmLabel;
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

        if (state.lastFocusedElement) {
            focusElement(state.lastFocusedElement);
            state.lastFocusedElement = null;
        }
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

        state.lastFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        state.overlay.removeAttribute('hidden');
        document.addEventListener('keydown', handleKeydown);
        state.overlay.addEventListener('click', handleBackdropClick);
        setProcessingState(false);

        requestAnimationFrame(() => {
            focusElement(state.confirmButton || state.cancelButton);
        });
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
            const scopeOption = getSelectedScopeOption();
            const timeOption = getSelectedTimeOption();
            success = await deleteFiles({ scope: scopeOption, time: timeOption });
        } catch (error) {
            console.error('[deleteFiles] Unexpected error while confirming deletion', error);
            success = false;
        }

        if (success) {
            closeModal();
            return;
        }

        setProcessingState(false);
    };

    const init = () => {
        state.overlay = document.getElementById(SELECTORS.overlayId);
        state.card = state.overlay?.querySelector(SELECTORS.cardSelector);
        state.openButton = document.getElementById(SELECTORS.openButtonId);
        state.cancelButton = document.getElementById(SELECTORS.cancelButtonId);
        state.confirmButton = document.getElementById(SELECTORS.confirmButtonId);
        state.confirmText = document.getElementById(SELECTORS.confirmTextId);
        state.cancelText = document.getElementById(SELECTORS.cancelTextId);
        state.timeGroup = document.getElementById(SELECTORS.timeGroupId);
        state.timeSelect = document.getElementById(SELECTORS.timeSelectId);
        state.timeHiddenInput = document.getElementById(SELECTORS.timeHiddenInputId);
        state.scopeSelect = document.getElementById(SELECTORS.scopeSelectId);
        state.scopeHiddenInput = document.getElementById(SELECTORS.scopeHiddenInputId);

        if (!state.overlay || !state.openButton || !state.cancelButton || !state.confirmButton) {
            return;
        }

        if (state.card) {
            state.card.setAttribute('role', 'dialog');
            state.card.setAttribute('aria-modal', 'true');
            state.card.setAttribute('aria-labelledby', 'deleteAllFilesHeaderTitle');
        }

        if (state.scopeSelect) {
            const initialSelectedScope = state.scopeSelect.querySelector('.select-option.selected');
            const initialScopeValue =
                (typeof window.getCustomSelectValue === 'function' && window.getCustomSelectValue(state.scopeSelect))
                || initialSelectedScope?.dataset?.value
                || 'all';
            updateHiddenScopeValue(initialScopeValue);
            updateTimeGroupVisibility(initialScopeValue);

            state.scopeSelect.addEventListener('customSelectChange', (event) => {
                const value = event?.detail?.value || 'all';
                updateHiddenScopeValue(value);
                updateTimeGroupVisibility(value);
            });
        } else {
            updateHiddenScopeValue();
        }

        if (state.timeSelect) {
            const initialSelected = state.timeSelect.querySelector('.select-option.selected');
            const initialTimeValue =
                (typeof window.getCustomSelectValue === 'function' && window.getCustomSelectValue(state.timeSelect))
                || initialSelected?.dataset?.value;
            updateHiddenTimeValue(initialTimeValue);

            state.timeSelect.addEventListener('customSelectChange', (event) => {
                const value = event?.detail?.value || 'all';
                updateHiddenTimeValue(value);
            });
        } else {
            updateHiddenTimeValue();
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
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
