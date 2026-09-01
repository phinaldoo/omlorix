let isSettingPassword = false;

function getChangePasswordOverlay() {
    return document.getElementById('changePasswordOverlay');
}

function getOpenChangePasswordModalButton() {
    return document.getElementById('openChangePasswordModal');
}

function getOpenSetPasswordModalButton() {
    return document.getElementById('openSetPasswordModal');
}

function getCloseChangePasswordModalButton() {
    return document.getElementById('changePasswordCancelButton') || document.getElementById('changePasswordCancelBtn');
}

function changePasswordT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function isChangePasswordModalOpen() {
    const overlay = getChangePasswordOverlay();
    if (!overlay) return false;
    if (overlay.classList.contains('delete-warning-overlay')) {
        return !overlay.hasAttribute('hidden');
    }
    return overlay.classList.contains('active');
}

function closeChangePasswordModal() {
    if (!isChangePasswordModalOpen()) return;
    toggleModalDisplay('changePasswordOverlay');
}

// Expose to global scope for passwordRequirements.js
if (typeof window !== 'undefined') {
    window.isSettingPassword = false;
}

function handleOpenChangePasswordModal() {
    openChangePasswordModal(false);
}

function handleOpenSetPasswordModal() {
    openChangePasswordModal(true);
}

function bindChangePasswordEventListener() {
    const openChangePasswordModalButton = getOpenChangePasswordModalButton();
    const openSetPasswordModalButton = getOpenSetPasswordModalButton();
    const closeChangePasswordModalButton = getCloseChangePasswordModalButton();

    if (openChangePasswordModalButton) openChangePasswordModalButton.addEventListener('click', handleOpenChangePasswordModal);
    if (openSetPasswordModalButton) openSetPasswordModalButton.addEventListener('click', handleOpenSetPasswordModal);
    if (closeChangePasswordModalButton) closeChangePasswordModalButton.addEventListener('click', closeChangePasswordModal);
}


function removeChangePasswordEventListener() {
    const openChangePasswordModalButton = getOpenChangePasswordModalButton();
    const openSetPasswordModalButton = getOpenSetPasswordModalButton();
    const closeChangePasswordModalButton = getCloseChangePasswordModalButton();

    if (openChangePasswordModalButton) openChangePasswordModalButton.removeEventListener('click', handleOpenChangePasswordModal);
    if (openSetPasswordModalButton) openSetPasswordModalButton.removeEventListener('click', handleOpenSetPasswordModal);
    if (closeChangePasswordModalButton) closeChangePasswordModalButton.removeEventListener('click', closeChangePasswordModal);
}

if (typeof window !== 'undefined' && typeof window.registerEscapeHandler === 'function') {
    window.registerEscapeHandler({
        id: 'change-password-modal',
        priority: 180,
        isActive: isChangePasswordModalOpen,
        close: closeChangePasswordModal,
    });
}

async function openChangePasswordModal(isSetPassword = false) {
    const currentPasswordGroup = document.getElementById('currentPasswordGroup');
    const changePasswordHeaderTitle = document.getElementById('changePasswordHeaderTitle');
    const changePasswordBtnText = document.getElementById('changePasswordBtnText');

    isSettingPassword = isSetPassword;
    if (typeof window !== 'undefined') {
        window.isSettingPassword = isSetPassword;
    }
    
    // Update modal UI based on mode
    if (isSetPassword) {
        // Hide current password field for setting password
        if (currentPasswordGroup) currentPasswordGroup.style.display = 'none';
        if (changePasswordHeaderTitle) changePasswordHeaderTitle.textContent = changePasswordT('us_set_password_title', 'Set Password');
        if (changePasswordBtnText) changePasswordBtnText.textContent = changePasswordT('us_set_password_button', 'Set Password');
    } else {
        // Show current password field for changing password
        if (currentPasswordGroup) currentPasswordGroup.style.display = '';
        if (changePasswordHeaderTitle) changePasswordHeaderTitle.textContent = changePasswordT('us_change_password_title', 'Change Password');
        if (changePasswordBtnText) changePasswordBtnText.textContent = changePasswordT('us_change_password_button', 'Change Password');
    }
    
    toggleModalDisplay('changePasswordOverlay');
    await renderPasswordRequirements();
    bindPasswordRequirementsEventListener();
    resetPasswordRequirementsInputs();
}



async function initChangePasswordSection(allowed, needsPasswordSetup = false) {
    const changePasswordItem = document.getElementById('changePasswordItem');
    const setPasswordItem = document.getElementById('setPasswordItem');
    const changePasswordSection = document.getElementById('changePasswordSettingsSection');
    if (!changePasswordSection) return;

    const passwordActionAvailable = allowed !== false || needsPasswordSetup;
    changePasswordSection.toggleAttribute('hidden', !passwordActionAvailable);
    
    // Show appropriate button based on whether user needs to set password
    if (needsPasswordSetup) {
        if (changePasswordItem) changePasswordItem.hidden = true;
        if (setPasswordItem) setPasswordItem.hidden = false;
    } else {
        if (changePasswordItem) changePasswordItem.hidden = false;
        if (setPasswordItem) setPasswordItem.hidden = true;
    }
}
