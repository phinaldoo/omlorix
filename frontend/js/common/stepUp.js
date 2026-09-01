(function () {
    let pendingStepUpPromise = null;

    function t(key, fallback, vars = {}) {
        let value = fallback;
        if (typeof window.resolveSetupTranslation === 'function') {
            value = window.resolveSetupTranslation(key, fallback);
        } else if (typeof window.getTranslation === 'function') {
            value = window.getTranslation(key, fallback);
        }
        return Object.entries(vars).reduce(
            (text, [name, replacement]) => text.replaceAll(`{${name}}`, String(replacement)),
            String(value),
        );
    }

    function ensureModal() {
        let overlay = document.getElementById('securityStepUpOverlay');
        if (overlay) return overlay;

        overlay = document.createElement('div');
        overlay.className = 'delete-warning-overlay shared-modal-overlay';
        overlay.id = 'securityStepUpOverlay';
        overlay.hidden = true;
        overlay.setAttribute('aria-hidden', 'true');
        overlay.innerHTML = `
            <form class="delete-warning-card shared-modal shared-modal--fit" id="securityStepUpForm" role="dialog" aria-modal="true" aria-labelledby="securityStepUpTitle" aria-describedby="securityStepUpDescription securityStepUpError" tabindex="-1" novalidate>
                <header class="shared-modal-header shared-modal-header--main">
                    <h3 class="delete-warning-card-title shared-modal-title" id="securityStepUpTitle"></h3>
                </header>
                <div class="shared-modal-body shared-modal-body--centered">
                    <div class="delete-warning-card-icon" aria-hidden="true">
                        ${Icons.lock}
                    </div>
                    <p class="delete-warning-card-desc" id="securityStepUpDescription"></p>
                    <div class="form-group" id="securityStepUpPasswordGroup">
                        <label class="form-label" for="securityStepUpPassword" id="securityStepUpPasswordLabel"></label>
                        <!-- Use the shared setup input styling so the step-up dialog matches the rest of the app. -->
                        <input class="input" id="securityStepUpPassword" type="password" autocomplete="current-password" autocapitalize="off" spellcheck="false">
                    </div>
                    <div class="form-group" id="securityStepUpOtpGroup">
                        <label class="form-label" for="securityStepUpOtp" id="securityStepUpOtpLabel"></label>
                        <input class="input" id="securityStepUpOtp" type="text" inputmode="numeric" autocomplete="one-time-code" autocapitalize="off" spellcheck="false" maxlength="32" aria-describedby="securityStepUpOtpHint">
                        <p class="field-hint" id="securityStepUpOtpHint" hidden></p>
                    </div>
                    <p class="field-error" id="securityStepUpError" role="alert"></p>
                </div>
                <footer class="warning-navigation shared-modal-footer">
                    <button type="button" class="om-button border cancel" id="securityStepUpCancel"></button>
                    <button type="button" class="om-button border cancel" id="securityStepUpPasskey"></button>
                    <button type="submit" class="om-button border submit" id="securityStepUpSubmit"></button>
                </footer>
            </form>
        `;
        document.body.appendChild(overlay);
        return overlay;
    }

    function fillModalText(overlay) {
        overlay.querySelector('#securityStepUpTitle').textContent = t('step_up_title', 'Confirm it is you');
        overlay.querySelector('#securityStepUpDescription').textContent = t(
            'step_up_desc',
            'Use one of your available verification methods to confirm your identity before changing security settings.',
        );
        overlay.querySelector('#securityStepUpPasswordLabel').textContent = t('step_up_password_label', 'Current password');
        overlay.querySelector('#securityStepUpOtpLabel').textContent = t('step_up_otp_label', 'Two-factor code');
        overlay.querySelector('#securityStepUpCancel').textContent = t('common_cancel', 'Cancel');
        overlay.querySelector('#securityStepUpPasskey').textContent = t('step_up_passkey_button', 'Use passkey');
        overlay.querySelector('#securityStepUpSubmit').textContent = t('step_up_submit', 'Confirm');
    }

    function setError(overlay, message) {
        const error = overlay.querySelector('#securityStepUpError');
        if (error) error.textContent = message || '';
    }

    async function submitStepUp(payload) {
        const response = await window.authedFetch('/api/v1/auth/step-up', {
            method: 'POST',
            body: JSON.stringify(payload),
        });
        if (response.ok) return true;
        throw new Error(t('step_up_failed', 'Authentication failed. Please try again.'));
    }

    async function loadStepUpMethods() {
        const response = await window.authedFetch('/api/v1/auth/step-up/methods');
        if (!response.ok) {
            throw new Error(t(
                'step_up_methods_load_failed',
                'Verification methods could not be loaded. Close this dialog and try again.',
            ));
        }
        const data = await response.json();
        return {
            password: data?.password === true,
            otp: data?.otp === true,
            passkey: data?.passkey === true,
            recentAuthSufficient: data?.recent_auth_sufficient === true,
        };
    }

    async function prepareOtpStepUp(overlay) {
        const hint = overlay.querySelector('#securityStepUpOtpHint');
        if (hint) {
            hint.hidden = true;
            hint.textContent = '';
        }

        try {
            const response = await window.authedFetch('/api/v1/auth/step-up/otp/begin', {
                method: 'POST',
                body: JSON.stringify({}),
            });
            if (!response.ok) return;

            const data = await response.json();
            if (data?.provider === 'email' && hint) {
                hint.textContent = t(
                    'tfa_email_code_sent_to_hint',
                    'Enter the 6-digit code sent to {hint}.',
                    { hint: data.delivery_hint || '' },
                );
                hint.hidden = !hint.textContent;
            }
        } catch (_) {
            // Password and passkey verification remain available if preparing
            // an optional delivery-based OTP fails.
        }
    }

    async function submitPasskeyStepUp(overlay) {
        if (typeof window.PublicKeyCredential !== 'function' || !navigator.credentials) {
            throw new Error(t('passkey_not_supported', 'Passkeys are not supported in this browser.'));
        }
        const beginResponse = await window.authedFetch('/api/v1/auth/step-up/passkey/begin', {
            method: 'POST',
            body: JSON.stringify({}),
        });
        if (!beginResponse.ok) {
            throw new Error(t('step_up_passkey_begin_failed', 'Unable to start passkey confirmation.'));
        }
        const beginData = await beginResponse.json();
        const publicKeyOptions = window.WebAuthnHelpers?.preformatGetOptions({ publicKey: (beginData.publicKey || {}) });
        if (!publicKeyOptions || !publicKeyOptions.publicKey) {
            throw new Error(t('step_up_passkey_begin_failed', 'Unable to start passkey confirmation.'));
        }
        const mismatch = window.WebAuthnHelpers?.getRpIdMismatchMessage?.(publicKeyOptions, {
            actionLabel: t('step_up_passkey_action', 'confirmation'),
            expectedOrigin: beginData?.expected_origin,
        });
        if (mismatch) {
            throw new Error(mismatch);
        }

        let credential;
        try {
            credential = await navigator.credentials.get(publicKeyOptions);
        } catch (error) {
            const domainMessage = window.WebAuthnHelpers?.getWebAuthnErrorMessage?.(error, publicKeyOptions, {
                actionLabel: t('step_up_passkey_action', 'confirmation'),
                expectedOrigin: beginData?.expected_origin,
            });
            throw new Error(domainMessage || t('step_up_passkey_failed', 'Passkey confirmation failed. Please try again.'));
        }
        if (!credential) {
            throw new Error(t('step_up_passkey_cancelled', 'Passkey confirmation was cancelled.'));
        }

        const credentialJson = window.WebAuthnHelpers?.publicKeyCredentialToJSON(credential);
        await submitStepUp({
            passkey_credential: credentialJson,
            expected_challenge: beginData.challenge,
        });
        setError(overlay, '');
        return true;
    }

    async function showStepUpModal() {
        const overlay = ensureModal();
        fillModalText(overlay);
        setError(overlay, '');

        const form = overlay.querySelector('#securityStepUpForm');
        const passwordGroup = overlay.querySelector('#securityStepUpPasswordGroup');
        const otpGroup = overlay.querySelector('#securityStepUpOtpGroup');
        const passwordInput = overlay.querySelector('#securityStepUpPassword');
        const otpInput = overlay.querySelector('#securityStepUpOtp');
        const otpHint = overlay.querySelector('#securityStepUpOtpHint');
        const cancelButton = overlay.querySelector('#securityStepUpCancel');
        const passkeyButton = overlay.querySelector('#securityStepUpPasskey');
        const submitButton = overlay.querySelector('#securityStepUpSubmit');
        const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const previousBodyOverflow = document.body.style.overflow;

        // Fetch enrollment before exposing controls. The backend remains the
        // authority for verification; these flags ensure the user is not asked
        // for a credential they never configured.
        let methods = { password: false, otp: false, passkey: false, recentAuthSufficient: false };
        let methodsError = '';
        try {
            methods = await loadStepUpMethods();
        } catch (error) {
            methodsError = error?.message || t(
                'step_up_methods_load_failed',
                'Verification methods could not be loaded. Close this dialog and try again.',
            );
        }
        const browserSupportsPasskeys = typeof window.PublicKeyCredential === 'function' && Boolean(navigator.credentials);
        methods.passkey = methods.passkey && browserSupportsPasskeys;

        if (!methods.password && !methods.otp && !methods.passkey && methods.recentAuthSufficient) {
            return true;
        }

        if (passwordGroup) passwordGroup.hidden = !methods.password;
        if (otpGroup) otpGroup.hidden = !methods.otp;
        if (passkeyButton) passkeyButton.hidden = !methods.passkey;
        if (submitButton) submitButton.hidden = !(methods.password || methods.otp);

        // The modal is reused, so never retain credentials from an earlier
        // verification or a cancelled attempt in the hidden DOM.
        if (passwordInput) passwordInput.value = '';
        if (otpInput) otpInput.value = '';
        document.body.style.overflow = 'hidden';
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        if (methodsError) {
            setError(overlay, methodsError);
        } else if (!methods.password && !methods.otp && !methods.passkey) {
            setError(overlay, t(
                'step_up_no_methods',
                'No local verification method is available. Sign in again, then retry this action.',
            ));
        }
        const initialFocus = methods.password
            ? passwordInput
            : methods.otp
                ? otpInput
                : methods.passkey
                    ? passkeyButton
                    : cancelButton;
        initialFocus?.focus();
        if (methods.otp) prepareOtpStepUp(overlay);

        return new Promise((resolve) => {
            const cleanup = (result) => {
                overlay.hidden = true;
                overlay.setAttribute('aria-hidden', 'true');
                document.body.style.overflow = previousBodyOverflow;
                form?.removeEventListener('submit', handleSubmit);
                cancelButton?.removeEventListener('click', handleCancel);
                passkeyButton?.removeEventListener('click', handlePasskey);
                overlay.removeEventListener('click', handleOverlayClick);
                overlay.removeEventListener('keydown', handleKeydown);
                if (passwordInput) passwordInput.value = '';
                if (otpInput) otpInput.value = '';
                if (otpHint) {
                    otpHint.textContent = '';
                    otpHint.hidden = true;
                }
                previousFocus?.focus?.();
                resolve(result);
            };

            const setBusy = (busy) => {
                [passwordInput, otpInput, cancelButton, passkeyButton, submitButton].forEach((element) => {
                    if (element) element.disabled = busy;
                });
            };

            const handleCancel = () => cleanup(false);
            const handleOverlayClick = (event) => {
                if (event.target === overlay) cleanup(false);
            };
            const handleKeydown = (event) => {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    cleanup(false);
                    return;
                }
                if (event.key !== 'Tab') return;

                // Keep keyboard focus inside the modal while it is open.
                const focusable = [passwordInput, otpInput, cancelButton, passkeyButton, submitButton]
                    .filter((element) => element && !element.disabled && !element.hidden && !element.closest('[hidden]'));
                if (!focusable.length) {
                    event.preventDefault();
                    return;
                }
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            };

            async function handleSubmit(event) {
                event.preventDefault();
                const password = String(passwordInput?.value || '');
                const otpCode = String(otpInput?.value || '').trim();
                const payload = methods.password && password
                    ? { password }
                    : methods.otp && otpCode
                        ? { otp_code: otpCode }
                        : null;
                if (!payload) {
                    setError(overlay, t('step_up_required', 'Use one of the available verification methods.'));
                    return;
                }
                setBusy(true);
                try {
                    await submitStepUp(payload);
                    cleanup(true);
                } catch (error) {
                    setError(overlay, error?.message || t('step_up_failed', 'Authentication failed. Please try again.'));
                    setBusy(false);
                    ('password' in payload ? passwordInput : otpInput)?.focus();
                }
            }

            async function handlePasskey() {
                setBusy(true);
                try {
                    await submitPasskeyStepUp(overlay);
                    cleanup(true);
                } catch (error) {
                    setError(overlay, error?.message || t('step_up_passkey_failed', 'Passkey confirmation failed. Please try again.'));
                    setBusy(false);
                }
            }

            form?.addEventListener('submit', handleSubmit);
            cancelButton?.addEventListener('click', handleCancel);
            passkeyButton?.addEventListener('click', handlePasskey);
            overlay.addEventListener('click', handleOverlayClick);
            overlay.addEventListener('keydown', handleKeydown);
        });
    }

    async function ensureSecurityStepUp() {
        if (pendingStepUpPromise) return pendingStepUpPromise;
        pendingStepUpPromise = showStepUpModal()
            .catch((error) => {
                console.error('Unable to open security verification', error);
                window.notifyError?.(t(
                    'step_up_methods_load_failed',
                    'Verification methods could not be loaded. Close this dialog and try again.',
                ));
                return false;
            })
            .finally(() => {
                pendingStepUpPromise = null;
            });
        return pendingStepUpPromise;
    }

    window.ensureSecurityStepUp = ensureSecurityStepUp;
})();
