const userFirstNameInput = document.getElementById('usUserFirstName');
const userLastNameInput = document.getElementById('usUserLastName');
const userEmailInput = document.getElementById('usUserEmail');
const saveUserInfoBtn = document.getElementById('usSaveUserInfo');
const errorMessageUserInfo = document.getElementById('errorMessageUserInfo');

const userFirstNameContainer = document.getElementById('userFirstNameContainer');
const userLastNameContainer = document.getElementById('userLastNameContainer');
const userEmailContainer = document.getElementById('userEmailContainer');

let originalUserData = null;
let lastSetupPayload = null;
let permissionState = {
    allow_change_name: true,
    allow_change_email: true,
};
let suppressUpdates = false;
let personalInfoFormEventsBound = false;
let saveInProgress = false;
let saveSuccessTimeoutId = null;

// Keep transient success feedback in one place so repeated setup events or a
// second save can never leave the button stuck in its success presentation.
const SAVE_SUCCESS_FEEDBACK_MS = 2000;

function personalInfoT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function setFieldEditable(inputEl, containerEl, allowed) {
    if (!inputEl || !containerEl) return;
    inputEl.readOnly = !allowed;
    if (!allowed) {
        inputEl.classList.add('input-field--readonly');
        containerEl.classList.add('input-disabled');
    } else {
        inputEl.classList.remove('input-field--readonly');
        containerEl.classList.remove('input-disabled');
    }
}

function clearFieldError(inputEl, containerEl) {
    if (!inputEl || !containerEl) return;
    inputEl.classList.remove('input-error');
    let errorEl = containerEl.querySelector('.field-error');
    if (errorEl) {
        errorEl.remove();
    }
}

function setFieldError(inputEl, containerEl, message) {
    if (!inputEl || !containerEl) return;
    inputEl.classList.add('input-error');
    clearFieldError(inputEl, containerEl);
    const messageEl = document.createElement('div');
    messageEl.className = 'field-error';
    messageEl.textContent = message;
    containerEl.appendChild(messageEl);
}

function allowPersonalInfoForm(chatSetup) {
    if (!chatSetup) return;
    permissionState = {
        allow_change_name: chatSetup.allow_change_name !== false,
        allow_change_email: chatSetup.allow_change_email !== false,
    };
    setFieldEditable(userFirstNameInput, userFirstNameContainer, permissionState.allow_change_name);
    setFieldEditable(userLastNameInput, userLastNameContainer, permissionState.allow_change_name);
    setFieldEditable(userEmailInput, userEmailContainer, permissionState.allow_change_email);

    updateSaveButtonState();
}

function getTrimmedValue(inputEl) {
    if (!inputEl) return '';
    return (inputEl.value || '').trim();
}

function getCurrentUserSettingsEmail() {
    return originalUserData?.email || getTrimmedValue(userEmailInput);
}

function getChangedPayload() {
    if (!originalUserData) return {};
    const payload = {};

    const firstName = getTrimmedValue(userFirstNameInput);
    const lastName = getTrimmedValue(userLastNameInput);
    const email = getTrimmedValue(userEmailInput);

    if (permissionState.allow_change_name) {
        if (firstName && firstName !== originalUserData.first_name) {
            payload.first_name = firstName;
        }
        if (lastName && lastName !== originalUserData.last_name) {
            payload.last_name = lastName;
        }
    }

    if (permissionState.allow_change_email && email && email !== originalUserData.email) {
        payload.email = email;
    }

    return payload;
}

function updateSaveButtonState() {
    if (!saveUserInfoBtn || !originalUserData) return;
    const payload = getChangedPayload();
    const hasChanges = Object.keys(payload).length > 0;
    const canSave = hasChanges && !saveInProgress;
    saveUserInfoBtn.disabled = !canSave;
    if (canSave) {
        saveUserInfoBtn.classList.add('save-changes-btn--active');
    } else {
        saveUserInfoBtn.classList.remove('save-changes-btn--active');
    }
}

async function saveUserProfile() {
    if (!saveUserInfoBtn || saveInProgress) return;

    const payload = getChangedPayload();
    if (Object.keys(payload).length === 0) {
        return;
    }

    saveInProgress = true;
    updateSaveButtonState();
    if (payload.email) {
        if (typeof window.ensureSecurityStepUp !== 'function') {
            if (errorMessageUserInfo) {
                errorMessageUserInfo.style.display = 'block';
                errorMessageUserInfo.textContent = personalInfoT('step_up_methods_load_failed', 'Verification methods could not be loaded. Close this dialog and try again.');
            }
            saveInProgress = false;
            updateSaveButtonState();
            return;
        }
        if (!await window.ensureSecurityStepUp()) {
            saveInProgress = false;
            updateSaveButtonState();
            return;
        }
    }

    // A new save supersedes any success feedback from the previous request.
    // Restoring first also guarantees that the success view is never captured
    // as the button's baseline state by an overlapping interaction.
    clearSaveSuccessButton();
    saveUserInfoBtn.disabled = true;
    saveUserInfoBtn.classList.add('save-changes-btn--loading');
    clearFieldError(userEmailInput, userEmailContainer);
    if (errorMessageUserInfo) {
        errorMessageUserInfo.style.display = 'none';
        errorMessageUserInfo.textContent = '';
    }

    try {
        const response = await window.authedFetch(`/api/v1/users/personal-details/update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            if (response.status === 409) {
                if (payload.email) {
                    const message = personalInfoT(
                        'us_personal_info_email_change_unavailable',
                        'The email change could not be started. Check the address or ask an administrator to verify email delivery settings.',
                    );
                    setFieldError(userEmailInput, userEmailContainer, message);
                } else if (errorMessageUserInfo) {
                    errorMessageUserInfo.style.display = 'block';
                    errorMessageUserInfo.textContent = personalInfoT(
                        'us_personal_info_save_conflict',
                        'Conflict while saving changes.',
                    );
                }
            } else {
                if (errorMessageUserInfo) {
                    errorMessageUserInfo.style.display = 'block';
                    errorMessageUserInfo.textContent = personalInfoT('us_personal_info_save_failed_retry', 'Failed to save changes. Please try again.');
                }
            }
            updateSaveButtonState();
            return;
        }

        const responseData = await response.json().catch(() => ({}));

        const nextState = {
            ...(originalUserData || {}),
            ...payload,
            first_name: responseData.first_name ?? payload.first_name ?? originalUserData?.first_name,
            last_name: responseData.last_name ?? payload.last_name ?? originalUserData?.last_name,
            // The canonical address does not change until the recipient proves
            // control of it. Always trust the server's authoritative value.
            email: responseData.email ?? originalUserData?.email,
        };
        const updatedProfile = {
            first_name: nextState.first_name || '',
            last_name: nextState.last_name || '',
            email: nextState.email || '',
        };

        const mergedSetup = {
            ...(lastSetupPayload || {}),
            ...updatedProfile,
        };

        lastSetupPayload = mergedSetup;
        originalUserData = { ...updatedProfile };

        if (window.chatSetup && typeof window.chatSetup === 'object') {
            window.chatSetup = {
                ...window.chatSetup,
                ...updatedProfile,
            };
        }

        if (typeof window.initUserProfileUI === 'function') {
            window.initUserProfileUI(
                updatedProfile.first_name,
                updatedProfile.last_name,
                updatedProfile.email,
            );
        }
        if (typeof window.ProfilePictureManager?.applySetup === 'function' && window.chatSetup) {
            window.ProfilePictureManager.applySetup(window.chatSetup).catch((error) => {
                console.error('Failed to refresh profile picture after personal info update', error);
            });
        }
        if (typeof window.fetchBrowserAccounts === 'function') {
            // Refresh the sidebar account switcher so account labels immediately
            // reflect the newly saved name without needing a full page reload.
            window.fetchBrowserAccounts({ silent: true }).catch((error) => {
                console.error('Failed to refresh browser account list after personal info update', error);
            });
        }

        suppressUpdates = true;
        try {
            loadUserInfo(mergedSetup);
        } finally {
            suppressUpdates = false;
        }
        clearFieldError(userEmailInput, userEmailContainer);
        if (responseData.email_change_pending) {
            notifySuccess?.(personalInfoT(
                'us_personal_info_email_verification_sent',
                'Verification links were sent. Your current email remains active until the new address is verified.',
            ));
        }
        showSaveSuccessButton();
        updateSaveButtonState();
    } catch (error) {
        if (errorMessageUserInfo) {
            errorMessageUserInfo.style.display = 'block';
            errorMessageUserInfo.textContent = personalInfoT('us_personal_info_save_unavailable', 'Unable to save changes right now.');
        }
    } finally {
        saveInProgress = false;
        saveUserInfoBtn.classList.remove('save-changes-btn--loading');
        updateSaveButtonState();
    }
}

/**
 * Remove transient success feedback and restore the translated default label.
 * Clearing the outstanding timer makes this safe to call repeatedly.
 */
function clearSaveSuccessButton() {
    if (!saveUserInfoBtn) return;

    if (saveSuccessTimeoutId !== null) {
        clearTimeout(saveSuccessTimeoutId);
        saveSuccessTimeoutId = null;
    }

    saveUserInfoBtn.classList.remove('save-changes-btn--success');
    saveUserInfoBtn.textContent = personalInfoT('us_btn_save_changes', 'Save Changes');
}

/**
 * Show short-lived, translated confirmation without using inline colors or
 * retaining DOM from a previous success state.
 */
function showSaveSuccessButton() {
    if (!saveUserInfoBtn) return;

    clearSaveSuccessButton();

    // This success state is inserted after the page-wide i18n pass, so resolve
    // its label now instead of relying on data-i18n to revisit the new node.
    const savedLabel = personalInfoT('us_personal_info_saved', 'Saved');

    saveUserInfoBtn.classList.add('save-changes-btn--success');
    saveUserInfoBtn.innerHTML = `
        <div class="save-changes-btn__success-content">
            ${Icons.check} 
            <span data-i18n="us_personal_info_saved"></span>
        </div>
    `;
    const savedLabelElement = saveUserInfoBtn.querySelector('[data-i18n="us_personal_info_saved"]');
    if (savedLabelElement) {
        savedLabelElement.textContent = savedLabel;
    }

    saveSuccessTimeoutId = setTimeout(() => {
        saveSuccessTimeoutId = null;
        saveUserInfoBtn.classList.remove('save-changes-btn--success');
        saveUserInfoBtn.textContent = personalInfoT('us_btn_save_changes', 'Save Changes');
        updateSaveButtonState();
    }, SAVE_SUCCESS_FEEDBACK_MS);
}

function handlePersonalInfoInput() {
    clearFieldError(this, this.closest('.form-group'));
    updateSaveButtonState();
}

function loadUserInfo(userSettings = {}) {
    if (suppressUpdates) return;
    if (!userSettings || typeof userSettings !== 'object') return;

    const base = lastSetupPayload && typeof lastSetupPayload === 'object' ? lastSetupPayload : {};
    const mergedPayload = { ...base, ...userSettings };
    lastSetupPayload = mergedPayload;

    const firstName = mergedPayload.first_name ?? getTrimmedValue(userFirstNameInput);
    const lastName = mergedPayload.last_name ?? getTrimmedValue(userLastNameInput);
    const email = mergedPayload.email ?? getTrimmedValue(userEmailInput);

    if (userFirstNameInput) {
        userFirstNameInput.value = firstName;
    }
    if (userLastNameInput) {
        userLastNameInput.value = lastName;
    }
    if (userEmailInput) {
        userEmailInput.value = email;
    }
    originalUserData = {
        first_name: firstName || '',
        last_name: lastName || '',
        email: email || '',
    };

    allowPersonalInfoForm(mergedPayload);
    updateSaveButtonState();
}

function initPersonalInfoForm(chatSetup) {
    if (!userFirstNameInput || !userLastNameInput || !userEmailInput || !saveUserInfoBtn) return;

    const setup = chatSetup || window.chatSetup || {};
    allowPersonalInfoForm(setup);

    // This initializer can run once at DOM readiness and again when chat setup
    // data arrives. Bind handlers only once while still applying fresh setup
    // permissions on every call.
    if (!personalInfoFormEventsBound) {
        userFirstNameInput.addEventListener('input', handlePersonalInfoInput);
        userLastNameInput.addEventListener('input', handlePersonalInfoInput);
        userEmailInput.addEventListener('input', handlePersonalInfoInput);
        saveUserInfoBtn.addEventListener('click', saveUserProfile);
        personalInfoFormEventsBound = true;
    }

    updateSaveButtonState();
}

document.addEventListener('chatSetupReady', (event) => {
    const detail = event?.detail || {};
    initPersonalInfoForm(detail);
    loadUserInfo(detail);
});

if (document.readyState === 'complete' || document.readyState === 'interactive') {
    initPersonalInfoForm(window.chatSetup || {});
    loadUserInfo(window.chatSetup || {});
} else {
    document.addEventListener('DOMContentLoaded', () => {
        initPersonalInfoForm(window.chatSetup || {});
        loadUserInfo(window.chatSetup || {});
    });
}

if (typeof window !== 'undefined') {
    window.getCurrentUserSettingsEmail = getCurrentUserSettingsEmail;
}
