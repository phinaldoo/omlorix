// Shared password action logic
// - Fetch password requirements from init endpoint
// - Live validate new password against requirements
// - Ensure confirm matches
// - Submit change to backend and show notifications


// Elements
const passwordForm = document.getElementById('changePasswordForm') || document.getElementById('resetPasswordForm');
const passwordRequirementsContainer = document.getElementById('passwordRequirements');
const oldPasswordInput = passwordForm?.querySelector('[data-password-role="current"]') || document.getElementById('currentPassword');
const newPasswordInput = passwordForm?.querySelector('[data-password-role="new"]') || document.getElementById('newPassword');
const confirmPasswordInput = passwordForm?.querySelector('[data-password-role="confirm"]') || document.getElementById('confirmPassword');
const changePasswordSubmitBtn = document.getElementById('changePasswordBtn');

let req = {
    min_len: 0,
    min_special: 0,
    min_upper: 0,
    min_lower: 0,
    min_num: 0,
    special_characters: `!"#$%&'()*+,-./:;<=>?@[\\]^_\`{|}~`,
    character_class_mode: 'unicode_letter_digit_with_ascii_special',
};
let passwordSubmitInFlight = false;

const upperCaseRegex = (() => {
    try {
        return new RegExp('\\p{Lu}', 'u');
    } catch (error) {
        return /[A-Z]/;
    }
})();

const lowerCaseRegex = (() => {
    try {
        return new RegExp('\\p{Ll}', 'u');
    } catch (error) {
        return /[a-z]/;
    }
})();

const digitRegex = (() => {
    try {
        return new RegExp('\\p{Nd}', 'u');
    } catch (error) {
        return /[0-9]/;
    }
})();

function countPasswordRequirementChars(str, specialCharactersRaw, defaultSpecialCharacters) {
    const specialCharacters = new Set(Array.from(specialCharactersRaw || defaultSpecialCharacters || ''));
    let upper = 0, lower = 0, num = 0, special = 0;
    for (const ch of str) {
        if (specialCharacters.has(ch)) special++;
        else if (upperCaseRegex.test(ch)) upper++;
        else if (lowerCaseRegex.test(ch)) lower++;
        else if (digitRegex.test(ch)) num++;
    }
    return { upper, lower, num, special, len: Array.from(str).length };
}

// Expose shared helpers for other pages (e.g. signup) without requiring module bundling.
window.passwordRequirementUtils = window.passwordRequirementUtils || {};

function handlePasswordFormSubmit(e) {
    e.preventDefault();
    submitChangePassword();
}

const formatWithCount = (template, count) =>
    (template || '')
        .replace(/\{count\}/g, count)
        .replace(/\{plural\|([^|]+)\|([^}]+)\}/g, (_, singular, plural) =>
            count === 1 ? singular : plural
        )
        .replace(/\{pluralSuffix\}/g, count === 1 ? '' : 's');

function getVisiblePasswordRequirementItems(requirements, translateFn = getTranslation) {
    const items = [
        {
            key: 'min_len',
            label: (v) => formatWithCount(translateFn('req_min_len', 'At least {count} characters'), v)
        },
        {
            key: 'min_special',
            label: (v) => formatWithCount(translateFn('req_min_special', 'At least {count} special character{pluralSuffix}'), v)
        },
        {
            key: 'min_upper',
            label: (v) => formatWithCount(translateFn('req_min_upper', 'At least {count} uppercase letter{pluralSuffix}'), v)
        },
        {
            key: 'min_lower',
            label: (v) => formatWithCount(translateFn('req_min_lower', 'At least {count} lowercase letter{pluralSuffix}'), v)
        },
        {
            key: 'min_num',
            label: (v) => formatWithCount(translateFn('req_min_num', 'At least {count} digit{pluralSuffix}'), v)
        },
    ];

    return items.flatMap(({ key, label }) => {
        const rawVal = requirements?.[key];
        const val = Number(rawVal || 0);
        if (!val) return [];
        if (key === 'min_len' && val <= 1) return [];
        return [{ key, label: label(val) }];
    });
}

function createDefaultPasswordRequirementIcon() {
    const icon = document.createElement('span');
    icon.className = 'pw-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = '○';
    return icon;
}

function renderPasswordRequirementChecklist({
    checklistEl,
    requirements,
    wrapperEl = null,
    itemClassName = 'pw-item muted',
    textClassName = 'pw-text',
    createIconElement = createDefaultPasswordRequirementIcon,
    translateFn = getTranslation,
}) {
    if (!checklistEl) return 0;

    checklistEl.innerHTML = '';

    const items = getVisiblePasswordRequirementItems(requirements, translateFn);
    items.forEach(({ key, label }) => {
        const item = document.createElement('div');
        item.className = itemClassName;
        item.dataset.key = key;

        const icon = createIconElement();
        const text = document.createElement('span');
        text.className = textClassName;
        text.textContent = label;

        item.appendChild(icon);
        item.appendChild(text);
        checklistEl.appendChild(item);
    });

    if (wrapperEl) {
        wrapperEl.style.display = items.length === 0 ? 'none' : '';
    }

    return items.length;
}

async function fetchPasswordRequirements() {
    try {
        const response = await fetch(`/api/v1/users/password/requirements`, {
            method: 'GET',
            credentials: 'include',
        });
        if (response.ok) {
            const data = await response.json();
            req = {
                min_len: Number(data?.min_len || 0),
                min_special: Number(data?.min_special || 0),
                min_upper: Number(data?.min_upper || 0),
                min_lower: Number(data?.min_lower || 0),
                min_num: Number(data?.min_num || 0),
                special_characters: typeof data?.special_characters === 'string' ? data.special_characters : req.special_characters,
                character_class_mode: typeof data?.character_class_mode === 'string' ? data.character_class_mode : req.character_class_mode,
            };
            return req;
        }
        if (!response.ok) {
            notifyError(getTranslation('error_requirements_load', 'Failed to load password requirements.'));
            return null;
        }
    } catch (e) {
        console.error('fetchPasswordRequirements error', e);
        notifyError(getTranslation('error_requirements_unavailable', 'Unable to load password requirements. Please try again.'));
        return null;
    }
}

function renderPasswordRequirementsView(requirements) {
    if (!passwordRequirementsContainer) return 0;

    passwordRequirementsContainer.innerHTML = '';

    const ui = document.createElement('div');
    ui.className = 'pw-ui';
    const checklist = document.createElement('div');
    checklist.className = 'pw-checklist';

    const visibleCount = renderPasswordRequirementChecklist({
        checklistEl: checklist,
        requirements,
    });

    if (visibleCount === 0) {
        passwordRequirementsContainer.style.display = 'none';
        return visibleCount;
    }

    passwordRequirementsContainer.style.display = '';
    ui.appendChild(checklist);
    passwordRequirementsContainer.appendChild(ui);
    return visibleCount;
}

async function renderPasswordRequirements() {
    if (!passwordRequirementsContainer) return null;

    const requirements = await fetchPasswordRequirements();
    if (!requirements) return null;

    renderPasswordRequirementsView(requirements);
    return requirements;
}

function countChars(str) {
    return countPasswordRequirementChars(str, req.special_characters || '', '');
}

function updatePasswordRequirements() {
    if (!passwordRequirementsContainer || !newPasswordInput) return false;
    const stats = countChars(newPasswordInput.value || '');
    let allOk = true;

    const items = passwordRequirementsContainer.querySelectorAll('.pw-item');
    items.forEach((item) => {
        const key = item.dataset.key;
        const icon = item.querySelector('.pw-icon');
        const text = item.querySelector('.pw-text');
        let ok = false;
        switch (key) {
            case 'min_len': ok = stats.len >= (req.min_len || 0); break;
            case 'min_special': ok = stats.special >= (req.min_special || 0); break;
            case 'min_upper': ok = stats.upper >= (req.min_upper || 0); break;
            case 'min_lower': ok = stats.lower >= (req.min_lower || 0); break;
            case 'min_num': ok = stats.num >= (req.min_num || 0); break;
            default: ok = true;
        }
        if (ok) {
            item.classList.remove('bad', 'muted');
            item.classList.add('ok');
            icon.textContent = '✓';
            text.style.color = 'var(--text-color, #111827)';
        } else {
            item.classList.remove('ok');
            item.classList.add('bad');
            icon.textContent = '✕';
            text.style.color = 'var(--text-color-secondary, #6b7280)';
            allOk = false;
        }
    });

    newPasswordInput.setAttribute('aria-invalid', stats.len > 0 && !allOk ? 'true' : 'false');
    return allOk;
}


function getConfirmPasswordErrorElement() {
    let errorEl = document.getElementById('passwordConfirmError');
    if (errorEl) {
        errorEl.style.color = 'var(--danger-color, #b42318)';
        errorEl.style.fontSize = '0.875rem';
        errorEl.style.marginTop = '0.5rem';
        return errorEl;
    }
    return null;
}


function updateConfirmPasswordState(options = {}) {
    if (!newPasswordInput || !confirmPasswordInput) return false;

    const {
        showError = true,
        requireMatch = true,
    } = options;

    const confirmValue = confirmPasswordInput.value || '';
    const newValue = newPasswordInput.value || '';
    const mismatch = confirmValue.length > 0 && newValue !== confirmValue;
    const errorEl = getConfirmPasswordErrorElement();

    const shouldEnforceMatch = requireMatch && mismatch;
    confirmPasswordInput.setAttribute('aria-invalid', shouldEnforceMatch ? 'true' : 'false');
    if (errorEl) {
        const shouldShowMismatch = showError && shouldEnforceMatch;
        errorEl.hidden = !shouldShowMismatch;
        errorEl.textContent = shouldShowMismatch
            ? getTranslation('error_password_mismatch', 'New password and confirmation do not match.')
            : '';
    }
    return confirmValue.length > 0 && !shouldEnforceMatch;
}


function syncPasswordFieldRequirements() {
    const isSetPassword = window.isSettingPassword || false;
    const isPasswordResetFlow = window.isPasswordResetFlow || false;

    if (oldPasswordInput) {
        oldPasswordInput.required = !(isSetPassword || isPasswordResetFlow);
    }
    if (newPasswordInput) {
        newPasswordInput.required = true;
    }
    if (confirmPasswordInput) {
        confirmPasswordInput.required = true;
        const describedBy = (confirmPasswordInput.getAttribute('aria-describedby') || '')
            .split(/\s+/)
            .filter(Boolean)
            .filter((value) => value !== 'passwordConfirmError');
        describedBy.push('passwordConfirmError');
        confirmPasswordInput.setAttribute('aria-describedby', describedBy.join(' '));
    }
}


function updateSubmitState() {
    syncPasswordFieldRequirements();
    const reqOk = updatePasswordRequirements();
    const isSetPassword = window.isSettingPassword || false;
    const isPasswordResetFlow = window.isPasswordResetFlow || false;
    const currentProvided = (isSetPassword || isPasswordResetFlow) ? true : !!(oldPasswordInput && (oldPasswordInput.value || '').length > 0);
    // Keep the reset page permissive while typing; mismatch is only enforced on submit there.
    const confirmMatches = isPasswordResetFlow
        ? updateConfirmPasswordState({
            showError: false,
            requireMatch: false,
        })
        : updateConfirmPasswordState();
    if (changePasswordSubmitBtn) changePasswordSubmitBtn.disabled = passwordSubmitInFlight || !(reqOk && currentProvided && confirmMatches);
}


function setPasswordSubmitPending(isPending) {
    passwordSubmitInFlight = isPending;

    if (!changePasswordSubmitBtn) {
        return;
    }

    changePasswordSubmitBtn.disabled = true;
    changePasswordSubmitBtn.setAttribute('aria-busy', isPending ? 'true' : 'false');

    const textNode = changePasswordSubmitBtn.querySelector('span');
    if (textNode) {
        if (textNode.dataset.originalText === undefined) {
            textNode.dataset.originalText = textNode.textContent || '';
        }
        textNode.textContent = isPending
            ? getTranslation('password_submit_pending', 'Submitting...')
            : (textNode.dataset.originalText !== undefined
                ? textNode.dataset.originalText
                : (textNode.textContent || ''));
    }

    if (!isPending) {
        updateSubmitState();
    }
}






async function submitChangePassword() {
    if (passwordSubmitInFlight) {
        return;
    }

    try {
        const isSetPassword = window.isSettingPassword || false;
        const isPasswordResetFlow = window.isPasswordResetFlow || false;
        
        let payload, endpoint;
        if (isPasswordResetFlow) {
            payload = {
                token: window.passwordResetToken || '',
                new_password: newPasswordInput.value || ''
            };
            endpoint = '/api/v1/auth/password-reset/confirm';
        } else if (isSetPassword) {
            // Setting password for social login user
            payload = {
                new_password: newPasswordInput.value || ''
            };
            endpoint = '/api/v1/users/password/set';
        } else {
            // Changing existing password
            payload = {
                old_password: oldPasswordInput.value || '',
                new_password: newPasswordInput.value || ''
            };
            endpoint = '/api/v1/users/password/change';
        }

        if (!updateConfirmPasswordState({
            showError: true,
            requireMatch: true,
        })) {
            notifyError(getTranslation('error_password_mismatch', 'New password and confirmation do not match.'));
            return;
        }

        setPasswordSubmitPending(true);
        
        const requestFn = isPasswordResetFlow
            ? (url, init) => fetch(url, { credentials: 'include', ...init })
            : window.authedFetch;
        const res = await requestFn(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            const reauthRequired = Boolean(data?.reauth_required);
            if (isPasswordResetFlow) {
                if (typeof window.clearPasswordResetToken === 'function') {
                    window.clearPasswordResetToken();
                }
                notifySuccess(getTranslation('password_reset_success', 'Password reset successfully. You can now sign in.'));
                setTimeout(() => {
                    window.location.href = '/login';
                }, 1200);
            } else {
                const successMessage = isSetPassword
                    ? reauthRequired
                        ? getTranslation('success_set_reauth', 'Password set successfully. Please sign in again.')
                        : getTranslation('success_set', 'Password set successfully.')
                    : reauthRequired
                        ? getTranslation('success_change_reauth', 'Password changed successfully. Please sign in again.')
                        : getTranslation('success_change', 'Password changed successfully.');
                notifySuccess(successMessage);
                removePasswordRequirementsEventListener();

                if (reauthRequired) {
                    setTimeout(() => {
                        try {
                            if (typeof window.clearActiveUserLocalState === 'function') {
                                window.clearActiveUserLocalState();
                            }
                        } catch (_) {}
                        window.location.href = '/login';
                    }, 1200);
                } else {
                    if (typeof toggleModalDisplay === 'function') {
                        toggleModalDisplay('changePasswordOverlay');
                    }

                    if (window.isRequiredPasswordChangeFlow) {
                        setTimeout(() => {
                            window.location.href = '/';
                        }, 1200);
                    } else if (isSetPassword && typeof window.openUserSettings === 'function') {
                        setTimeout(() => {
                            if (typeof window.closeUserSettings === 'function') {
                                window.closeUserSettings();
                            }
                            window.openUserSettings('security');
                        }, 1000);
                    }
                }
            }
            // Clear fields after success (avoid leaving sensitive data in DOM)
            if (oldPasswordInput) oldPasswordInput.value = '';
            newPasswordInput.value = '';
            confirmPasswordInput.value = '';
            if (!reauthRequired && !isPasswordResetFlow) {
                setPasswordSubmitPending(false);
            }
        } else {
            const detail = typeof data?.detail === 'string' ? data.detail : '';
            if (detail === 'Old password is incorrect.') {
                notifyError(getTranslation('error_old_password_incorrect', detail));
            } else if (detail === 'Invalid or expired password reset token.') {
                notifyError(getTranslation('password_reset_invalid', detail));
            } else if (detail === 'Too many password reset attempts. Please try again later.') {
                notifyError(getTranslation('password_reset_rate_limited', detail));
            } else if (detail) {
                notifyError(detail);
            } else {
                const errorMessage = isPasswordResetFlow
                    ? getTranslation('password_reset_failed', 'Failed to reset password.')
                    : isSetPassword
                    ? getTranslation('error_set_failed', 'Failed to set password.')
                    : getTranslation('error_change_failed', 'Failed to change password.');
                notifyError(errorMessage);
            }
            setPasswordSubmitPending(false);
        }
    } catch (e) {
        console.error('submitChangePassword error', e);
        notifyError(getTranslation('error_unexpected', 'An unexpected error occurred while changing password.'));
        setPasswordSubmitPending(false);
    }
}




function bindPasswordRequirementsEventListener() {
    if (newPasswordInput) newPasswordInput.addEventListener('input', updateSubmitState);
    if (oldPasswordInput) oldPasswordInput.addEventListener('input', updateSubmitState);
    if (confirmPasswordInput) confirmPasswordInput.addEventListener('input', updateSubmitState);
    if (passwordForm) passwordForm.addEventListener('submit', handlePasswordFormSubmit);
}
function removePasswordRequirementsEventListener() {
    if (newPasswordInput) newPasswordInput.removeEventListener('input', updateSubmitState);
    if (oldPasswordInput) oldPasswordInput.removeEventListener('input', updateSubmitState);
    if (confirmPasswordInput) confirmPasswordInput.removeEventListener('input', updateSubmitState);
    if (passwordForm) passwordForm.removeEventListener('submit', handlePasswordFormSubmit);
}



function resetPasswordRequirementsInputs() {
    if (newPasswordInput) newPasswordInput.value = '';
    if (oldPasswordInput) oldPasswordInput.value = '';
    if (confirmPasswordInput) confirmPasswordInput.value = '';
    updateSubmitState();
    // Focus the first input element after a short delay to ensure modal is visible
    setTimeout(() => {
        const shouldFocusOldPassword = oldPasswordInput
            && !(window.isSettingPassword || window.isPasswordResetFlow)
            && oldPasswordInput.offsetParent !== null;
        if (shouldFocusOldPassword) {
            oldPasswordInput.focus();
        } else if (newPasswordInput) {
            newPasswordInput.focus();
        }
    }, 100);
}

function handlePasswordRequirementsI18nUpdated() {
    if (!passwordRequirementsContainer || !req) return;
    renderPasswordRequirementsView(req);
    updateSubmitState();
}

document.addEventListener('i18n:updated', handlePasswordRequirementsI18nUpdated);

window.passwordRequirementUtils.countChars = countPasswordRequirementChars;
window.passwordRequirementUtils.formatWithCount = formatWithCount;
window.passwordRequirementUtils.getVisibleItems = getVisiblePasswordRequirementItems;
window.passwordRequirementUtils.renderChecklist = renderPasswordRequirementChecklist;
