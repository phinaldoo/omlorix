/**
 * Social Login Handler
 * Handles OAuth flows for social login providers (Google, etc.)
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
        let text = translate(key, fallback);
        Object.entries(vars).forEach(([name, value]) => {
            text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), String(value));
        });
        return text;
    };
    const MAX_STORED_ACCOUNTS = 5;
    const sanitizeError = (error) => {
        const fallback = translate('social_login_unknown_error_detail', 'Unknown error');
        const knownMessages = {
            access_denied: translate('social_login_failed', 'Social login failed. Please try again.'),
            invalid_request: translate('social_login_failed', 'Social login failed. Please try again.'),
            invalid_state: translate('social_login_session_expired', 'Social login session expired. Please try again.'),
            server_error: translate('social_login_failed', 'Social login failed. Please try again.'),
        };

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
            {
                error: typeof window.sanitizeLoginCallbackError === 'function'
                    ? window.sanitizeLoginCallbackError(error, { fallback: unknownDetailFallback, knownMessages })
                    : sanitizeError(error),
            },
        );
        notifyAuthError(message);
        resetLoginCallbackUrl();
        return true;
    };
    const initiateAuthRedirect = authFlowContext.initiateAuthRedirect;
    const exchangeAuthCode = authFlowContext.exchangeAuthCode;

    // IMMEDIATELY check for social login callback before any other code runs
    // This prevents race conditions with other initialization code
    function parseUrlParams() {
        const searchParams = new URLSearchParams(window.location.search);
        const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''));
        return { searchParams, hashParams };
    }

    const { hashParams } = parseUrlParams();
    
    // Handle successful social login - exchange code for token securely
    if (hashParams.get('social_success') === 'true') {
        (async function() {
            await exchangeAuthCode({
                endpoint: '/api/v1/auth/social/exchange',
                logPrefix: '[Social Login]',
                failureRedirectUrl: '/login?error=social_login_failed',
            });
        })();
        return;
    }

    let socialProviders = {};
    let offlineModeEnabled = false;

    const SOCIAL_BUTTON_IDS = {
        google: { buttonId: 'googleLoginBtn', textId: 'googleBtnText' },
        microsoft: { buttonId: 'microsoftLoginBtn', textId: 'microsoftBtnText' },
        apple: { buttonId: 'appleLoginBtn', textId: 'appleBtnText' },
        github: { buttonId: 'githubLoginBtn', textId: 'githubBtnText' },
        slack: { buttonId: 'slackLoginBtn', textId: 'slackBtnText' },
    };
    const SOCIAL_PROVIDER_LABELS = {
        google: 'Google',
        microsoft: 'Microsoft',
        apple: 'Apple',
        github: 'GitHub',
        slack: 'Slack',
    };

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

    function setLoginProviderButtonVisibility(button, isVisible) {
        if (!button) {
            return;
        }
        button.style.display = isVisible ? 'flex' : 'none';
        button.tabIndex = isVisible ? 0 : -1;
        button.setAttribute('aria-hidden', isVisible ? 'false' : 'true');
    }

    function hideSocialLoginSection() {
        const divider = document.getElementById('socialLoginDivider');
        const buttons = document.getElementById('socialLoginButtons');
        if (divider) divider.style.display = 'none';
        if (buttons) buttons.style.display = 'none';
        Object.values(SOCIAL_BUTTON_IDS).forEach(({ buttonId }) => {
            setLoginProviderButtonVisibility(document.getElementById(buttonId), false);
        });
    }

    function showSocialProviderButton(providerName, providerConfig) {
        const config = SOCIAL_BUTTON_IDS[providerName];
        if (!config) {
            return;
        }

        const button = document.getElementById(config.buttonId);
        if (!button) {
            return;
        }

        setLoginProviderButtonVisibility(button, true);

        const buttonText = document.getElementById(config.textId);
        const customButtonText = String(providerConfig?.button_text || '').trim();
        if (buttonText && customButtonText) {
            buttonText.textContent = customButtonText;
        }
    }

    // Initialize social login on page load
    async function initSocialLogin() {
        try {
            // Fetch available social providers
            const response = await fetch('/api/v1/auth/social/providers');
            if (!response.ok) return;
            
            const data = await response.json();
            socialProviders = data.providers || {};
            offlineModeEnabled = Boolean(data.offline_mode);

            if (offlineModeEnabled) {
                hideSocialLoginSection();
                return;
            }
            
            // Show social login section if any providers are enabled
            if (Object.keys(socialProviders).length > 0) {
                const divider = document.getElementById('socialLoginDivider');
                const buttons = document.getElementById('socialLoginButtons');
                if (divider) divider.style.display = 'flex';
                if (buttons) buttons.style.display = 'flex';
                
                // Show configured provider buttons and apply admin-defined labels.
                Object.entries(socialProviders).forEach(([providerName, providerConfig]) => {
                    if (!providerConfig) {
                        return;
                    }
                    showSocialProviderButton(providerName, providerConfig);
                });

                // Show last used label after buttons are displayed
                if (window.loginMethodTracker) {
                    window.loginMethodTracker.showLastUsedLabel();
                }
            }
        } catch (error) {
            console.error('Failed to load social providers:', error);
        }
    }

    function handleSocial2FAResult(result, provider) {
        if (!result || !provider) {
            return false;
        }
        if (result.status !== 'otp_setup' && result.status !== 'otp_required_already_setup') {
            return false;
        }

        sessionStorage.setItem('social_login_provider', provider);

        if (typeof window.set2FAContextFromResult === 'function') {
            window.set2FAContextFromResult(result);
        }

        if (result.status === 'otp_setup') {
            if (typeof show2FASetup === 'function') {
                show2FASetup();
            }
            if (result.qrcode && typeof renderQrCodeWhenVisible === 'function') {
                const decodedQr = safeDecodeQrPayload(result.qrcode);
                if (decodedQr) {
                    renderQrCodeWhenVisible(decodedQr);
                }
            } else if (result.secret && typeof window.refresh2FASetupCopyState === 'function') {
                window.refresh2FASetupCopyState({ secret: result.secret || '', otpauthUri: '' });
            }
            return true;
        }

        if (typeof show2FAVerify === 'function') {
            show2FAVerify();
        }
        return true;
    }

    async function handleSocialProviderLogin(provider, options, pendingButton = null) {
        if (typeof initiateAuthRedirect !== 'function') {
            notifyAuthError(options.initFailureMessage);
            return;
        }
        // A social redirect starts a new authentication flow. Discard any SSO
        // 2FA marker left behind by an interrupted same-tab navigation so the
        // eventual social callback cannot be dispatched to the SSO completer.
        sessionStorage.removeItem('sso_login_provider');
        await initiateAuthRedirect({
            endpoint: `/api/v1/auth/social/${provider}/init`,
            payload: getAuthContextPayload(),
            stateStorageKey: 'social_oauth_state',
            loginMethod: provider,
            initFailureMessage: formatTranslate('social_login_init_failed', 'Failed to initiate {provider} login.', {
                provider: options.providerLabel,
            }),
            connectionFailureMessage: formatTranslate('social_login_connect_failed', 'Failed to connect to {provider}. Please try again.', {
                provider: options.providerLabel,
            }),
            logLabel: options.logLabel,
            pendingButton,
            pendingLabel: translate('login_redirect_pending', 'Connecting...'),
        });
    }

    async function handleGoogleLogin(eventOrButton = null) {
        await handleSocialProviderLogin('google', {
            providerLabel: 'Google',
            logLabel: 'Google login error',
        }, resolveTriggerButton(eventOrButton));
    }

    // Handle Microsoft login button click
    async function handleMicrosoftLogin(eventOrButton = null) {
        await handleSocialProviderLogin('microsoft', {
            providerLabel: 'Microsoft',
            logLabel: 'Microsoft login error',
        }, resolveTriggerButton(eventOrButton));
    }

    // Handle Apple login button click
    async function handleAppleLogin(eventOrButton = null) {
        await handleSocialProviderLogin('apple', {
            providerLabel: 'Apple',
            logLabel: 'Apple login error',
        }, resolveTriggerButton(eventOrButton));
    }

    // Handle GitHub login button click
    async function handleGitHubLogin(eventOrButton = null) {
        await handleSocialProviderLogin('github', {
            providerLabel: 'GitHub',
            logLabel: 'GitHub login error',
        }, resolveTriggerButton(eventOrButton));
    }

    // Handle Slack OpenID Connect login without requesting workspace data scopes.
    async function handleSlackLogin(eventOrButton = null) {
        await handleSocialProviderLogin('slack', {
            providerLabel: 'Slack',
            logLabel: 'Slack login error',
        }, resolveTriggerButton(eventOrButton));
    }

    // Handle social login callback (URL parameters)
    async function handleSocialCallback() {
        const { searchParams: currentSearch, hashParams: currentHash } = parseUrlParams();
        const isSsoOwnedError = currentSearch.get('auth_flow') === 'sso';
        const isSocialCallback = currentHash.get('social_success') === 'true'
            || Boolean(currentHash.get('social_2fa'))
            || (Boolean(currentSearch.get('error')) && !isSsoOwnedError);
        if (isSocialCallback) {
            // Session storage outlives a single redirect. Once the URL identifies
            // a social callback, stale SSO state must not influence error or 2FA
            // dispatch for this request.
            sessionStorage.removeItem('sso_login_provider');
        }
        
        // Handle successful social login - exchange code for token
        if (currentHash.get('social_success') === 'true') {
            await exchangeAuthCode({
                endpoint: '/api/v1/auth/social/exchange',
                logPrefix: '[Social Login]',
                failureNotifyMessage: translate('social_login_failed', 'Social login failed. Please try again.'),
                resetUrlOnFailure: true,
            });
            return true;
        }
        
        // Handle 2FA required for social login
        if (currentHash.get('social_2fa')) {
            const tfaType = currentHash.get('social_2fa');
            const provider = currentHash.get('provider');
            
            if (provider) {
                if (tfaType !== 'setup' && tfaType !== 'verify') {
                    resetLoginCallbackUrl();
                    return false;
                }
                const provider2fa = currentHash.get('provider_2fa');
                const deliveryHint = currentHash.get('delivery_hint');
                const resendSeconds = Number(currentHash.get('resend_available_in_seconds') || 0);
                const handled = handleSocial2FAResult({
                    status: tfaType === 'setup' ? 'otp_setup' : 'otp_required_already_setup',
                    provider: provider2fa,
                    delivery_hint: deliveryHint,
                    resend_available_in_seconds: resendSeconds,
                    setup_material_available: currentHash.get('setup_material_available') === 'True'
                        || currentHash.get('setup_material_available') === 'true',
                }, provider);
                if (!handled) {
                    resetLoginCallbackUrl();
                    return false;
                }
                
                // Clear URL parameters
                resetLoginCallbackUrl();
                return true;
            }
        }
        
        // Handle social login errors
        const error = currentSearch.get('error');
        if (error) {
            // Enterprise SSO and social OAuth share the login callback page.  Do
            // not let this earlier handler consume an explicitly SSO-owned error.
            if (isSsoOwnedError) {
                return false;
            }
            const handledAccountState = typeof window.handleLoginCallbackAccountState === 'function'
                && window.handleLoginCallbackAccountState(error, {
                    resetLoginCallbackUrl,
                    expires: currentSearch.get('expires'),
                    accessBlocked: {
                        reason: currentSearch.get('reason') || '',
                        next_allowed_at: currentSearch.get('next_allowed_at') || '',
                        blocked_message: currentSearch.get('blocked_message') || '',
                    },
                });
            if (handledAccountState) {
                return true;
            }

            // The provider query value is URL-controlled. Resolve it through
            // a fixed allowlist before placing a brand name in translated UI.
            const unlinkedProviderLabel = SOCIAL_PROVIDER_LABELS[
                String(currentSearch.get('provider') || '').toLowerCase()
            ];
            const accountNotLinkedMessage = unlinkedProviderLabel
                ? formatTranslate(
                    'social_account_not_linked',
                    'Your Omlorix account is not linked to {provider}. Sign in with another method first, then link {provider} to this account.',
                    { provider: unlinkedProviderLabel },
                )
                : translate('social_login_failed', 'Social login failed. Please try again.');

            const errorMessages = {
                provider_disabled: translate('social_login_provider_disabled', 'This login method is currently disabled.'),
                token_exchange_failed: translate('social_login_token_exchange_failed', 'Failed to complete authentication. Please try again.'),
                no_email: translate('social_login_no_email', 'Could not retrieve email from your account.'),
                email_not_verified: translate(
                    'social_login_email_not_verified',
                    'Your email address is not verified with the social login provider.',
                ),
                domain_not_allowed: translate('signup_error_domain_not_allowed', 'Your email domain is not allowed for this application.'),
                workspace_not_allowed: translate('social_login_workspace_not_allowed', 'Your Slack workspace is not allowed for this application.'),
                provider_subject_missing: translate(
                    'social_login_provider_subject_missing',
                    'Could not verify the social login provider account identity.',
                ),
                provider_subject_mismatch: translate(
                    'social_login_provider_subject_mismatch',
                    'The linked social login account does not match this user.',
                ),
                social_account_conflict: translate(
                    'social_account_conflict',
                    'This provider account is already connected to another Omlorix user.',
                ),
                terms_configuration_required: translate(
                    'terms_configuration_required_error',
                    'Account registration is unavailable until the operator publishes custom Terms of Service on the login page.',
                ),
                terms_acceptance_required: translate(
                    'terms_acceptance_required_error',
                    'Accept the current Terms of Service to create a new account.',
                ),
                terms_revision_mismatch: translate(
                    'terms_revision_mismatch_error',
                    'The Terms of Service changed. Review the latest version and try again.',
                ),
                signup_not_allowed: translate('social_login_signup_not_allowed', 'New account registration is not available with this login method.'),
                user_creation_failed: translate('social_login_user_creation_failed', 'Failed to create your account. Please try again.'),
                account_inactive: translate('social_login_account_inactive', 'Your account is inactive. Please contact support.'),
                account_locked: translate('social_login_account_locked', 'Your account is temporarily locked. Please try again later.'),
                account_deleted: translate('social_login_account_deleted', 'This account has been deleted.'),
                account_pending: translate('social_login_account_pending', 'Your account is pending approval. Please wait for an administrator to approve your account.'),
                auth_failed: translate('social_login_auth_failed', 'Authentication failed. Please try again.'),
                social_login_failed: translate('social_login_failed', 'Social login failed. Please try again.'),
                social_account_not_linked: accountNotLinkedMessage,
                max_accounts_reached: formatTranslate(
                    'social_login_max_accounts_reached',
                    'Maximum of {maxAccounts} stored accounts reached. Remove or replace an account first.',
                    { maxAccounts: MAX_STORED_ACCOUNTS },
                ),
            };

            return renderLoginCallbackError({
                error,
                errorMessages,
                formatTranslate,
                unknownKey: 'social_login_unknown_error',
                unknownFallback: 'An error occurred during login. ({error})',
                unknownDetailFallback: translate('social_login_unknown_error_detail', 'Unknown error'),
                knownMessages: {
                    access_denied: translate('social_login_failed', 'Social login failed. Please try again.'),
                    invalid_request: translate('social_login_failed', 'Social login failed. Please try again.'),
                    invalid_state: translate('social_login_session_expired', 'Social login session expired. Please try again.'),
                    server_error: translate('social_login_failed', 'Social login failed. Please try again.'),
                },
            });
        }
        
        return false;
    }

    // Complete social login with 2FA
    async function completeSocialLoginWith2FA(otpCode, otpType, otpDestination = null) {
        const provider = sessionStorage.getItem('social_login_provider');
        
        if (!provider) {
            notifyAuthError(translate('social_login_session_expired', 'Social login session expired. Please try again.'));
            return null;
        }
        
        try {
            const response = await fetch(`/api/v1/auth/social/${provider}/complete`, {
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
                // Clear session storage
                sessionStorage.removeItem('social_login_provider');

                // Only hand control to the configured app-associated HTTPS
                // origin. Private URL schemes are globally claimable and must
                // never carry native authentication codes.
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
            console.error('Social 2FA completion error:', error);
            return { status: 'error', detail: translate('social_login_token_exchange_failed', 'Failed to complete authentication. Please try again.') };
        }
    }

    // Check if we're in a social login 2FA flow
    function isInSocialLogin2FAFlow() {
        return !!sessionStorage.getItem('social_login_provider');
    }

    // Event listeners
    document.addEventListener('DOMContentLoaded', function() {
        // Initialize social login
        initSocialLogin();
        
        // Handle callback parameters
        handleSocialCallback();

        // Google login button
        const googleBtn = document.getElementById('googleLoginBtn');
        if (googleBtn) {
            googleBtn.addEventListener('click', handleGoogleLogin);
        }

        // Microsoft login button
        const microsoftBtn = document.getElementById('microsoftLoginBtn');
        if (microsoftBtn) {
            microsoftBtn.addEventListener('click', handleMicrosoftLogin);
        }
        
        // Apple login button
        const appleBtn = document.getElementById('appleLoginBtn');
        if (appleBtn) {
            appleBtn.addEventListener('click', handleAppleLogin);
        }

        // GitHub login button
        const githubBtn = document.getElementById('githubLoginBtn');
        if (githubBtn) {
            githubBtn.addEventListener('click', handleGitHubLogin);
        }

        // Slack login button
        const slackBtn = document.getElementById('slackLoginBtn');
        if (slackBtn) {
            slackBtn.addEventListener('click', handleSlackLogin);
        }
    });

    // Expose functions globally for 2FA integration
    window.socialLogin = {
        completeSocialLoginWith2FA,
        isInSocialLogin2FAFlow,
        handleSocialCallback,
        handleSocial2FAResult,
    };

    function renderQrCodeWhenVisible(uri, retries = 5) {
        const container = document.getElementById('tfaQrCode');
        if (!container) {
            return;
        }
        const isReady = container.offsetParent !== null && container.clientWidth > 0 && container.clientHeight > 0;
        if (!isReady && retries > 0) {
            requestAnimationFrame(() => renderQrCodeWhenVisible(uri, retries - 1));
            return;
        }
        renderQrCode(uri);
    }

    function safeDecodeQrPayload(value) {
        if (!value) {
            return '';
        }
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
