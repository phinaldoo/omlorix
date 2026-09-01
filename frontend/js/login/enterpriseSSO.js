/**
 * Enterprise SSO Login Handler
 * Handles SAML and OIDC flows for enterprise Single Sign-On
 */
(function() {
    'use strict';

    const authFlowContext = window.loginAuthFlowContext || {};
    const getAuthContextPayload = authFlowContext.getAuthContextPayload || function() {
        return { account_mode: 'primary' };
    };
    const resetLoginCallbackUrl = authFlowContext.resetLoginCallbackUrl || function() {
        window.history.replaceState({}, '', '/login');
    };
    const notifyAuthError = authFlowContext.notifyAuthError || function(message) {
        if (typeof notifyError === 'function' && message) {
            notifyError(message);
        }
    };
    const translate = (key, fallback) =>
        typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback)
            : fallback;
    const formatTranslate = (key, fallback, vars = {}) => {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        let text = translate(key, fallback);
        Object.entries(vars).forEach(([name, value]) => {
            text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), String(value));
        });
        return text;
    };
    const initiateAuthRedirect = authFlowContext.initiateAuthRedirect;
    const exchangeAuthCode = authFlowContext.exchangeAuthCode;
    const MAX_STORED_ACCOUNTS = 5;
    const sanitizeError = (error, fallback, knownMessages) => {
        if (typeof window.sanitizeLoginCallbackError === 'function') {
            return window.sanitizeLoginCallbackError(error, { fallback, knownMessages });
        }
        if (typeof window.sanitizeSocialLoginError === 'function') {
            return window.sanitizeSocialLoginError(error, { fallback, knownMessages });
        }
        return fallback;
    };
    const renderLoginCallbackError = authFlowContext.renderLoginCallbackError || function(options = {}) {
        const {
            error,
            errorMessages = {},
            unknownKey,
            unknownFallback,
            unknownDetailFallback,
            knownMessages,
        } = options;
        const message = errorMessages[error] || formatTranslate(
            unknownKey,
            unknownFallback,
            { error: sanitizeError(error, unknownDetailFallback, knownMessages) },
        );
        notifyAuthError(message);
        resetLoginCallbackUrl();
        return true;
    };

    // IMMEDIATELY check for SSO login callback before any other code runs
    const urlParams = new URLSearchParams(window.location.search);
    
    // Handle successful SSO login - exchange code for token securely
    if (urlParams.get('sso_success') === 'true') {
        (async function() {
            await exchangeAuthCode({
                endpoint: '/api/v1/auth/sso/exchange',
                logPrefix: '[Enterprise SSO]',
                failureRedirectUrl: '/login?error=sso_login_failed',
            });
        })();
        return;
    }

    let ssoProviders = {};
    let ssoCallbackHandled = false;
    const SSO_PROVIDER_BUTTON_IDS = {
        saml: 'samlLoginBtn',
        oidc: 'oidcLoginBtn',
    };
    const SSO_BUTTON_IDS = Object.values(SSO_PROVIDER_BUTTON_IDS);

    function resolveTriggerButton(eventOrButton) {
        if (!eventOrButton || typeof eventOrButton !== 'object') {
            return null;
        }
        if (eventOrButton.currentTarget) {
            return eventOrButton.currentTarget;
        }
        if (typeof eventOrButton.setAttribute === 'function' || Object.prototype.hasOwnProperty.call(eventOrButton, 'disabled')) {
            return eventOrButton;
        }
        return null;
    }

    function updateSsoDividerVisibility() {
        const divider = document.getElementById('ssoLoginDivider');
        if (!divider) return;
        const hasVisibleButton = SSO_BUTTON_IDS.some((id) => {
            const button = document.getElementById(id);
            return button && button.style.display !== 'none';
        });
        divider.style.display = hasVisibleButton ? 'flex' : 'none';
    }

    function setSsoButtonVisibility(button, isVisible) {
        if (!button) {
            return;
        }
        button.style.display = isVisible ? 'flex' : 'none';
        button.tabIndex = isVisible ? 0 : -1;
        button.setAttribute('aria-hidden', isVisible ? 'false' : 'true');
        updateSsoDividerVisibility();
    }

    // Initialize enterprise SSO on page load
    async function initEnterpriseSSO() {
        try {
            // Fetch available SSO providers
            const response = await fetch('/api/v1/auth/sso/providers');
            if (!response.ok) return;
            
            const data = await response.json();
            ssoProviders = data.providers || {};
            
            // Show SSO buttons if any providers are enabled
            if (Object.keys(ssoProviders).length > 0) {
                // Show SAML button if enabled
                if (ssoProviders.saml) {
                    const samlBtn = document.getElementById('samlLoginBtn');
                    if (samlBtn) {
                        setSsoButtonVisibility(samlBtn, true);
                        const btnText = document.getElementById('samlBtnText');
                        if (btnText && ssoProviders.saml.button_text) {
                            btnText.textContent = ssoProviders.saml.button_text;
                        }
                    }
                }
                
                // Show OIDC button if enabled
                if (ssoProviders.oidc) {
                    const oidcBtn = document.getElementById('oidcLoginBtn');
                    if (oidcBtn) {
                        setSsoButtonVisibility(oidcBtn, true);
                        const btnText = document.getElementById('oidcBtnText');
                        if (btnText && ssoProviders.oidc.button_text) {
                            btnText.textContent = ssoProviders.oidc.button_text;
                        }
                    }
                }

            }
        } catch (error) {
            console.error('Failed to load SSO providers:', error);
        }
    }

    async function handleSSOProviderLogin(providerType, options, pendingButton = null) {
        if (typeof initiateAuthRedirect !== 'function') {
            notifyAuthError(options.initFailureMessage);
            return;
        }
        await initiateAuthRedirect({
            endpoint: `/api/v1/auth/sso/${providerType}/init`,
            payload: { provider_type: providerType, ...getAuthContextPayload() },
            stateStorageKey: 'sso_oauth_state',
            loginMethod: options.loginMethod,
            initFailureMessage: options.initFailureMessage,
            connectionFailureMessage: options.connectionFailureMessage,
            logLabel: options.logLabel,
            pendingButton,
            pendingLabel: translate('login_redirect_pending', 'Connecting...'),
        });
    }

    // Handle SAML login button click
    async function handleSAMLLogin(eventOrButton = null) {
        const pendingButton = resolveTriggerButton(eventOrButton);
        await handleSSOProviderLogin('saml', {
            initFailureMessage: formatTranslate('sso_login_init_failed', 'Failed to initiate {provider} login.', { provider: 'SAML' }),
            connectionFailureMessage: formatTranslate('sso_login_connect_failed', 'Failed to connect to {provider}. Please try again.', { provider: 'SAML' }),
            logLabel: 'SAML login error',
        }, pendingButton);
    }

    // Handle OIDC login button click
    async function handleOIDCLogin(eventOrButton = null) {
        const pendingButton = resolveTriggerButton(eventOrButton);
        await handleSSOProviderLogin('oidc', {
            initFailureMessage: formatTranslate('sso_login_init_failed', 'Failed to initiate {provider} login.', { provider: 'OIDC' }),
            connectionFailureMessage: formatTranslate('sso_login_connect_failed', 'Failed to connect to {provider}. Please try again.', { provider: 'OIDC' }),
            logLabel: 'OIDC login error',
        }, pendingButton);
    }

    // Handle SSO callback (URL parameters)
    async function handleSSOCallback() {
        const urlParams = new URLSearchParams(window.location.search);
        
        // Handle successful SSO login - exchange code for token
        if (urlParams.get('sso_success') === 'true') {
            await exchangeAuthCode({
                endpoint: '/api/v1/auth/sso/exchange',
                logPrefix: '[Enterprise SSO]',
                failureNotifyMessage: translate('sso_login_failed', 'SSO login failed. Please try again.'),
                resetUrlOnFailure: true,
            });
            return true;
        }
        
        // Handle 2FA required for SSO login
        if (urlParams.get('sso_2fa')) {
            const tfaType = urlParams.get('sso_2fa');
            const provider = urlParams.get('provider');
            
            if (provider) {
                // Store for 2FA completion
                sessionStorage.setItem('sso_login_provider', provider);
                
                if (tfaType === 'setup') {
                    const provider2fa = urlParams.get('provider_2fa');
                    const deliveryHint = urlParams.get('delivery_hint');
                    const resendSeconds = Number(urlParams.get('resend_available_in_seconds') || 0);
                    if (typeof window.set2FAContextFromResult === 'function') {
                        window.set2FAContextFromResult({
                            provider: provider2fa,
                            delivery_hint: deliveryHint,
                            resend_available_in_seconds: resendSeconds,
                            setup_material_available: urlParams.get('setup_material_available') === 'True'
                                || urlParams.get('setup_material_available') === 'true',
                        });
                    }
                    
                    if (typeof show2FASetup === 'function') {
                        show2FASetup();
                    }
                } else if (tfaType === 'verify') {
                    const provider2fa = urlParams.get('provider_2fa');
                    const deliveryHint = urlParams.get('delivery_hint');
                    const resendSeconds = Number(urlParams.get('resend_available_in_seconds') || 0);
                    if (typeof window.set2FAContextFromResult === 'function') {
                        window.set2FAContextFromResult({
                            provider: provider2fa,
                            delivery_hint: deliveryHint,
                            resend_available_in_seconds: resendSeconds,
                        });
                    }
                    if (typeof show2FAVerify === 'function') {
                        show2FAVerify();
                    }
                }
                
                resetLoginCallbackUrl();
                return true;
            }
        }
        
        // Handle SSO login errors
        const error = urlParams.get('error');
        const errorMessages = {
            'sso_login_failed': translate('sso_login_failed', 'SSO login failed. Please try again.'),
            'provider_disabled': translate('sso_provider_disabled', 'This SSO provider is currently disabled.'),
            'no_email': translate('sso_no_email', 'Could not retrieve email from SSO provider.'),
            'email_not_verified': translate('sso_email_not_verified', 'Your email is not verified with the SSO provider.'),
            'domain_not_allowed': translate('sso_domain_not_allowed', 'Your email domain is not allowed for SSO login.'),
            'terms_configuration_required': translate('terms_configuration_required_error', 'Account registration is unavailable until the operator publishes custom Terms of Service on the login page.'),
            'terms_acceptance_required': translate('terms_acceptance_required_error', 'Accept the current Terms of Service to create a new account.'),
            'terms_revision_mismatch': translate('terms_revision_mismatch_error', 'The Terms of Service changed. Review the latest version and try again.'),
            'signup_not_allowed': translate('sso_signup_not_allowed', 'New account registration is not available via SSO.'),
            'user_creation_failed': translate('sso_user_creation_failed', 'Failed to create your account. Please try again.'),
            'account_inactive': translate('sso_account_inactive', 'Your account is inactive. Please contact support.'),
            'account_locked': translate('social_login_account_locked', 'Your account is temporarily locked. Please try again later.'),
            'account_deleted': translate('sso_account_deleted', 'This account has been deleted.'),
            'account_pending': translate('sso_account_pending', 'Your account is pending approval. Please wait for an administrator to approve your account.'),
            'sso_state_missing': translate('sso_state_missing', 'SSO session expired. Please try again.'),
            'sso_state_invalid': translate('sso_state_invalid', 'SSO session invalid. Please try again.'),
            'sso_security_missing': translate('sso_security_missing', 'SSO security validation failed. Please try again.'),
            'max_accounts_reached': formatTranslate(
                'sso_max_accounts_reached',
                'Maximum of {maxAccounts} stored accounts reached. Remove or replace an account first.',
                { maxAccounts: MAX_STORED_ACCOUNTS },
            ),
        };

        if (error) {
            const handledAccountState = typeof window.handleLoginCallbackAccountState === 'function'
                && window.handleLoginCallbackAccountState(error, {
                    resetLoginCallbackUrl,
                    expires: urlParams.get('expires'),
                    accessBlocked: {
                        reason: urlParams.get('reason') || '',
                        next_allowed_at: urlParams.get('next_allowed_at') || '',
                        blocked_message: urlParams.get('blocked_message') || '',
                    },
                });
            if (handledAccountState) {
                return true;
            }
        }

        if (error && (error.includes('sso') || Object.prototype.hasOwnProperty.call(errorMessages, error))) {
            return renderLoginCallbackError({
                error,
                reference: urlParams.get('reference'),
                errorMessages,
                formatTranslate,
                unknownKey: 'sso_unknown_error',
                unknownFallback: 'An error occurred during SSO login. ({error})',
                unknownDetailFallback: translate('sso_unknown_error_detail', 'Unknown error'),
                knownMessages: {
                    access_denied: translate('sso_login_failed', 'SSO login failed. Please try again.'),
                    invalid_request: translate('sso_login_failed', 'SSO login failed. Please try again.'),
                    invalid_state: translate('sso_state_invalid', 'SSO session invalid. Please try again.'),
                    server_error: translate('sso_login_failed', 'SSO login failed. Please try again.'),
                },
            });
        }
        
        return false;
    }

    async function handleSSOCallbackOnce() {
        if (ssoCallbackHandled) {
            return true;
        }
        const handled = await handleSSOCallback();
        if (handled) {
            ssoCallbackHandled = true;
        }
        return handled;
    }

    function handleSSOCallbackAfterTranslations() {
        if (window.__omlorixI18nReady) {
            void handleSSOCallbackOnce();
            return;
        }

        document.addEventListener('i18n:updated', () => {
            void handleSSOCallbackOnce();
        }, { once: true });

        window.setTimeout(() => {
            void handleSSOCallbackOnce();
        }, 1000);
    }

    // Complete SSO login with 2FA
    async function completeSSOLoginWith2FA(otpCode, otpType, otpDestination = null) {
        const provider = sessionStorage.getItem('sso_login_provider');
        
        if (!provider) {
            if (typeof notifyError === 'function') {
                notifyError(translate('sso_login_session_expired', 'SSO login session expired. Please try again.'));
            }
            return null;
        }
        
        try {
            const response = await fetch(`/api/v1/auth/sso/${provider}/complete`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    otp_code: otpCode,
                    otp_type: otpType,
                    otp_action: otpType === 'setup' ? 'setup' : 'verify',
                    ...(otpDestination ? { otp_destination: otpDestination } : {}),
                })
            });
            
            const result = await response.json();
            if (!response.ok && typeof window.isCrossSiteRequestBlockDetail === 'function' && window.isCrossSiteRequestBlockDetail(result?.detail)) {
                window.showCrossSiteRequestBlocked(result.detail);
                return null;
            }
            
            if (result.status === 'success') {
                sessionStorage.removeItem('sso_login_provider');

                // Keep the 2FA continuation on the configured app-associated
                // HTTPS callback; a custom scheme could be claimed by another app.
                if (window.loginAuthFlowContext?.isTrustedNativeCallbackUrl?.(result.native_callback_url)) {
                    window.location.assign(result.native_callback_url);
                    return result;
                }
                
                window.location.href = typeof window.resolvePostAuthRedirect === 'function'
                    ? window.resolvePostAuthRedirect(result)
                    : '/';
                return result;
            }
            return result;
        } catch (error) {
            console.error('SSO 2FA completion error:', error);
            return { status: 'error', detail: translate('sso_completion_failed', 'Failed to complete authentication.') };
        }
    }

    // Check if we're in an SSO login 2FA flow
    function isInSSOLogin2FAFlow() {
        return !!sessionStorage.getItem('sso_login_provider');
    }

    // Event listeners
    document.addEventListener('DOMContentLoaded', function() {
        // Initialize enterprise SSO
        initEnterpriseSSO();
        
        // Handle callback parameters
        handleSSOCallbackAfterTranslations();
        
        // SAML login button
        const samlBtn = document.getElementById('samlLoginBtn');
        if (samlBtn) {
            samlBtn.addEventListener('click', handleSAMLLogin);
        }
        
        // OIDC login button
        const oidcBtn = document.getElementById('oidcLoginBtn');
        if (oidcBtn) {
            oidcBtn.addEventListener('click', handleOIDCLogin);
        }

    });

    // Expose functions globally for 2FA integration
    window.enterpriseSSO = {
        completeSSOLoginWith2FA,
        isInSSOLogin2FAFlow,
        handleSSOCallback,
    };

    function renderQrCodeWhenVisible(uri, retries = 5) {
        const container = document.getElementById('tfaQrCode');
        if (!container) return;
        
        const isReady = container.offsetParent !== null && container.clientWidth > 0 && container.clientHeight > 0;
        if (!isReady && retries > 0) {
            requestAnimationFrame(() => renderQrCodeWhenVisible(uri, retries - 1));
            return;
        }
        renderQrCode(uri);
    }

    function safeDecodeQrPayload(value) {
        if (!value) return '';
        
        const hasEncodedBytes = (text) => /%[0-9a-fA-F]{2}/.test(text);
        let attempts = 0;
        let current = value;
        
        while (attempts < 5 && hasEncodedBytes(current)) {
            try {
                current = decodeURIComponent(current);
            } catch (err) {
                return '';
            }
            attempts += 1;
        }
        return current;
    }
})();
