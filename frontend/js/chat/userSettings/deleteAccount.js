// Elements
const openDeleteAccountModalButton = document.getElementById('openDeleteAccountModalButton');
const deleteAccountCancelButton = document.getElementById('deleteAccountCancelButton');
const deleteAccountPrimaryButton = document.getElementById('deleteAccountPrimaryButton');
const deleteAccountPrimaryText = document.getElementById('deleteAccountPrimaryText');
const deleteAccountPolicyText = document.getElementById('deleteAccountPolicyText');
const deleteAccountPurgeText = document.getElementById('deleteAccountPurgeText');

let deleteAccountPolicy = null;
let deleteAccountInProgress = false;


function isDeleteAccountModalOpen() {
    const overlay = document.getElementById('deleteAccountOverlay');
    return Boolean(overlay && !overlay.hasAttribute('hidden'));
}


function openDeleteAccountModal() {
    if (!isDeleteAccountModalOpen()) toggleModalDisplay('deleteAccountOverlay');
}


function closeDeleteAccountModal() {
    if (isDeleteAccountModalOpen()) toggleModalDisplay('deleteAccountOverlay');
}


function handleDeleteAccountPrimaryClick() {
    deleteAccount();
}



function bindDeleteAccountEventListener() {
    if (openDeleteAccountModalButton) openDeleteAccountModalButton.addEventListener('click', openDeleteAccountModal);
    if (deleteAccountCancelButton) deleteAccountCancelButton.addEventListener('click', closeDeleteAccountModal);
    if (deleteAccountPrimaryButton) deleteAccountPrimaryButton.addEventListener('click', handleDeleteAccountPrimaryClick);
}


function removeDeleteAccountEventListener() {
    if (openDeleteAccountModalButton) openDeleteAccountModalButton.removeEventListener('click', openDeleteAccountModal);
    if (deleteAccountCancelButton) deleteAccountCancelButton.removeEventListener('click', closeDeleteAccountModal);
    if (deleteAccountPrimaryButton) deleteAccountPrimaryButton.removeEventListener('click', handleDeleteAccountPrimaryClick);
}


if (typeof window !== 'undefined' && typeof window.registerEscapeHandler === 'function') {
    window.registerEscapeHandler({
        id: 'delete-account-modal',
        priority: 180,
        isActive: isDeleteAccountModalOpen,
        close: closeDeleteAccountModal,
    });
}


async function deleteAccount() {
    if (deleteAccountInProgress) return false;
    deleteAccountInProgress = true;
    let deletionCompleted = false;
    if (deleteAccountPrimaryButton) deleteAccountPrimaryButton.disabled = true;
    try {
        if (typeof window.ensureSecurityStepUp !== 'function') {
            notifyError(getDeleteAccountTranslation('step_up_methods_load_failed', 'Verification methods could not be loaded. Close this dialog and try again.'));
            return false;
        }
        if (!await window.ensureSecurityStepUp()) return false;
        const res = await window.authedFetch('/api/v1/users/delete', {
            method: 'DELETE',
        });
        if (!res.ok) {
            notifyError(getDeleteAccountTranslation('delete_account_failed', 'Failed to delete account'));
            return false;
        }
        const result = await res.json().catch(() => null);
        notifySuccess(getDeleteAccountSuccessMessage(result?.account_deletion || deleteAccountPolicy));
        setTimeout(() => {
            redirectToLogin();
        }, 2000);
        deletionCompleted = true;
        return true;
    } finally {
        if (!deletionCompleted) {
            deleteAccountInProgress = false;
            if (deleteAccountPrimaryButton) deleteAccountPrimaryButton.disabled = false;
        }
    }
}


async function initDeleteAccountSection(allowed, policy = null) {
    const deleteAccountSection = openDeleteAccountModalButton?.closest('.us-settings-section');
    if (!deleteAccountSection) return;

    deleteAccountPolicy = policy || null;
    updateDeleteAccountModalText();

    const shouldHide = allowed === false;
    deleteAccountSection.toggleAttribute('hidden', shouldHide);
    deleteAccountSection.style.removeProperty('display');
}


function getDeleteAccountTranslation(key, fallback, vars) {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}


function formatDeletionDate(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const lang = document.documentElement.getAttribute('lang') || navigator.language || 'en';
    return new Intl.DateTimeFormat(lang, {
        dateStyle: 'medium',
        timeStyle: 'short',
    }).format(date);
}


function updateDeleteAccountModalText() {
    const effect = deleteAccountPolicy?.effect;
    const purgeDate = formatDeletionDate(deleteAccountPolicy?.purge_scheduled_at);

    if (deleteAccountPolicyText) {
        if (effect === 'erasure') {
            deleteAccountPolicyText.textContent = getDeleteAccountTranslation(
                'delete_account_erasure_desc',
                'Your account and associated data will be permanently erased immediately. This cannot be restored.'
            );
        } else if (effect === 'scheduled_deletion') {
            deleteAccountPolicyText.textContent = getDeleteAccountTranslation(
                'delete_account_scheduled_desc',
                'Your account will be deactivated immediately. An administrator can restore it until permanent erasure.',
                { purgeDate }
            );
        } else {
            deleteAccountPolicyText.textContent = getDeleteAccountTranslation(
                'delete_account_retain_desc',
                'Your account will be deactivated immediately. Your data remains restorable by an administrator unless it is permanently erased later.'
            );
        }
    }

    if (deleteAccountPurgeText) {
        deleteAccountPurgeText.textContent = effect === 'scheduled_deletion' && purgeDate
            ? getDeleteAccountTranslation(
                'delete_account_purge_date',
                'Permanent erasure is scheduled for {purgeDate}.',
                { purgeDate }
            )
            : '';
        deleteAccountPurgeText.toggleAttribute('hidden', !deleteAccountPurgeText.textContent);
    }

    if (deleteAccountPrimaryText) {
        deleteAccountPrimaryText.textContent = getDeleteAccountTranslation(
            effect === 'erasure' ? 'delete_account_erasure_button' : 'delete_account_deactivate_button',
            effect === 'erasure' ? 'Erase Account Now' : 'Deactivate Account'
        );
    }
}


function getDeleteAccountSuccessMessage(policy) {
    const effect = policy?.effect;
    const purgeDate = formatDeletionDate(policy?.purge_scheduled_at);
    if (effect === 'erasure') {
        return getDeleteAccountTranslation('delete_account_erased_success', 'Account permanently erased.');
    }
    if (effect === 'scheduled_deletion' && purgeDate) {
        return getDeleteAccountTranslation(
            'delete_account_scheduled_success',
            'Account deactivated. Permanent erasure is scheduled for {purgeDate}.',
            { purgeDate }
        );
    }
    return getDeleteAccountTranslation(
        'delete_account_deactivated_success',
        'Account deactivated. Your data remains restorable by an administrator.'
    );
}


document.addEventListener('i18n:updated', updateDeleteAccountModalText);
