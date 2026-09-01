let enableSignin = true;
let enableSignup = false;
let enablePasswordReset = false;
let contactSupportEmail = "";
let showCustomLogo = false;
let customLogoHeight = "";
let themeObserverInitialized = false;
let showPrivacyNoticeLink = false;
let showTermsOfServiceLink = false;
let termsOfServicePolicy = {};
let adminLoginMode = false; // Track if we're in admin-only login mode
let enablePasskeys = true;
const DEFAULT_BRANDING_TITLE = "Welcome back";
const DEFAULT_BRANDING_SUBTITLE = "Sign in to continue to your account";
// Background loading is cosmetic. It must never be able to hold the complete
// sign-in surface behind the boot-time pending class indefinitely.
const LOGIN_BACKGROUND_READY_TIMEOUT_MS = 8000;

// New design customization settings
let loginDesign = "classic";
let showBackgroundImage = false;
let backgroundOverlayOpacity = 40;
let backgroundImageFit = "cover";
let backgroundImageSizePercent = 100;
let designBackgroundColor = "";
let brandingTitle = DEFAULT_BRANDING_TITLE;
let brandingSubtitle = DEFAULT_BRANDING_SUBTITLE;
let showBrandingText = true;

window.omlorixTermsOfServicePolicy = termsOfServicePolicy;

const loginLogoState = {
    entries: {
        light: undefined,
        dark: undefined,
    },
    pending: {
        light: null,
        dark: null,
    },
    objectUrls: new Map(),
};
let loginBackgroundObjectUrl = null;

function revokeLoginBackgroundUrl() {
    if (!loginBackgroundObjectUrl) {
        return;
    }
    URL.revokeObjectURL(loginBackgroundObjectUrl);
    loginBackgroundObjectUrl = null;
}

function showLoginInitErrorBanner(message) {
    const banner = document.getElementById('loginInitErrorBanner');
    if (!banner) return;
    banner.hidden = false;
    banner.textContent = message;
}

function resolveBrandingCopy(value, fallback) {
    if (typeof value !== 'string') {
        return fallback;
    }
    const normalized = value.trim();
    return normalized || fallback;
}

function normalizeHexColor(value, fallback = '#ffffff') {
    const raw = String(value || '').trim();
    if (!raw) return fallback;
    let normalized = raw.startsWith('#') ? raw : `#${raw}`;
    if (/^#[0-9a-fA-F]{3}$/.test(normalized)) {
        normalized = `#${normalized[1]}${normalized[1]}${normalized[2]}${normalized[2]}${normalized[3]}${normalized[3]}`;
    }
    if (!/^#[0-9a-fA-F]{6}$/.test(normalized)) {
        return fallback;
    }
    return normalized.toLowerCase();
}

function hexToRgb(value) {
    const normalized = normalizeHexColor(value, '');
    if (!normalized) {
        return null;
    }
    return {
        r: Number.parseInt(normalized.slice(1, 3), 16),
        g: Number.parseInt(normalized.slice(3, 5), 16),
        b: Number.parseInt(normalized.slice(5, 7), 16),
    };
}

function getReadablePrivacyColors(backgroundHex) {
    const rgb = hexToRgb(backgroundHex);
    if (!rgb) {
        return {
            text: '',
            link: '',
        };
    }
    const brightness = (rgb.r * 299 + rgb.g * 587 + rgb.b * 114) / 1000;
    if (brightness >= 160) {
        return {
            text: 'rgba(17, 24, 39, 0.78)',
            link: '#0f172a',
        };
    }
    return {
        text: '#f5f7fb',
        link: '#ffffff',
    };
}

function getReadableBrandingColors(backgroundHex) {
    const rgb = hexToRgb(backgroundHex);
    if (!rgb) {
        return {
            text: '#ffffff',
            shadow: '0 2px 12px rgba(0, 0, 0, 0.5)',
        };
    }
    const brightness = (rgb.r * 299 + rgb.g * 587 + rgb.b * 114) / 1000;
    if (brightness >= 160) {
        return {
            text: '#111827',
            shadow: '0 1px 2px rgba(255, 255, 255, 0.55)',
        };
    }
    return {
        text: '#ffffff',
        shadow: '0 2px 12px rgba(0, 0, 0, 0.5)',
    };
}

function shouldRequireCurrentTermsForSignup() {
    const revision = Number(termsOfServicePolicy?.revision || 0);
    return Boolean(
        enableSignup
        && termsOfServicePolicy?.require_current_revision_for_signup
        && revision > 0
    );
}

function setTermsConsentControlState(container, checkbox, isVisible, { required = false } = {}) {
    if (!container || !checkbox) {
        return;
    }

    container.hidden = !isVisible;
    container.style.display = isVisible ? '' : 'none';
    checkbox.disabled = !isVisible;
    checkbox.required = Boolean(required && isVisible);

    if (!isVisible) {
        checkbox.checked = false;
    }
}




// Fetch settings and initialize UI
function initializeUI() {
    // Fetch the settings from the backend
    fetch(`/api/v1/settings/login/setup`, { credentials: 'include' })
        .then(async response => {
            if (!response.ok) {
                if (
                    typeof window.handleCrossSiteRequestBlock === 'function'
                    && await window.handleCrossSiteRequestBlock(response)
                ) {
                    throw Object.assign(new Error('Cross-site request blocked'), { crossSiteRequestBlocked: true });
                }
                throw new Error(`Failed to fetch settings (${response.status})`);
            }
            return response.json();
        })
        .then(async data => {
            
            // Extract settings from the response
            const loginCustomization = data.login_customization?.data || {};
            
            // Assign values from the response
            enableSignin = data.enable_signin
            enableSignup = Boolean(data.enable_signup)
            enablePasswordReset = Boolean(data.enable_password_reset && data.password_reset_ready)
            contactSupportEmail = data.contact_support_email
            showPrivacyNoticeLink = Boolean(data.show_privacy_notice_link)
            showTermsOfServiceLink = Boolean(data.show_terms_of_service_link)
            termsOfServicePolicy = data.terms_of_service_policy || {}
            window.omlorixTermsOfServicePolicy = termsOfServicePolicy
            enablePasskeys = Boolean(data.enable_passkeys)

            showCustomLogo = loginCustomization.show_custom_logo
            customLogoHeight = loginCustomization.custom_logo_height
            
            // Extract new design settings
            loginDesign = loginCustomization.login_design || "classic"
            showBackgroundImage = Boolean(loginCustomization.show_background_image)
            const overlayOpacitySetting = Number(loginCustomization.background_overlay_opacity)
            backgroundOverlayOpacity = Number.isFinite(overlayOpacitySetting)
                ? Math.min(100, Math.max(0, overlayOpacitySetting))
                : 40
            const backgroundFitSetting = String(loginCustomization.background_image_fit || '').trim().toLowerCase()
            backgroundImageFit = ['cover', 'contain', 'custom_percent'].includes(backgroundFitSetting)
                ? backgroundFitSetting
                : 'cover'
            const backgroundSizeSetting = Number(loginCustomization.background_image_size_percent)
            backgroundImageSizePercent = Number.isFinite(backgroundSizeSetting)
                ? Math.min(300, Math.max(10, backgroundSizeSetting))
                : 100
            const rawDesignBackgroundColor = String(loginCustomization.design_background_color || '').trim();
            designBackgroundColor = rawDesignBackgroundColor
                ? normalizeHexColor(rawDesignBackgroundColor, '#ffffff')
                : ''
            brandingTitle = resolveBrandingCopy(loginCustomization.branding_title, DEFAULT_BRANDING_TITLE)
            brandingSubtitle = resolveBrandingCopy(loginCustomization.branding_subtitle, DEFAULT_BRANDING_SUBTITLE)
            showBrandingText = loginCustomization.show_branding_text !== false
            
            if (typeof window.setApplicationName === 'function') {
                window.setApplicationName(data.application_name);
            } else {
                applicationName = data.application_name;
            }

            // Do not reveal the login surface until the selected design and
            // its optional background image have both finished initializing.
            await applyUISettings();
        })
        .catch(async error => {
            if (error?.crossSiteRequestBlocked) {
                return;
            }
            console.error('Failed to fetch login settings', error);
            enableSignin = true;
            enableSignup = false;
            termsOfServicePolicy = {};
            window.omlorixTermsOfServicePolicy = termsOfServicePolicy;
            enablePasswordReset = false;
            showCustomLogo = false;
            showPrivacyNoticeLink = false;
            showTermsOfServiceLink = false;
            enablePasskeys = false;
            showLoginInitErrorBanner(
                typeof window.getTranslation === 'function'
                    ? window.getTranslation('login_init_error_banner', 'Some login settings could not be loaded. Core sign-in remains available.')
                    : 'Some login settings could not be loaded. Core sign-in remains available.',
            );
            // A settings or image failure must still expose the safe classic
            // fallback instead of leaving the page permanently hidden.
            await applyUISettings();
        })
        .finally(() => {
            window.__loginUIReady = true;
            if (typeof window.__revealLoginUI === 'function') {
                window.__revealLoginUI();
            } else {
                document.documentElement.classList.remove('login-ui-pending');
            }
        });
}

// Apply all UI settings based on the current values
async function applyUISettings() {
    // Cache commonly used elements (used across multiple branches)
    const loginForm = document.getElementById('loginFormContainer');
    const loginFormElement = document.getElementById('loginForm');
    const loginTab = document.querySelector('.login-tab[data-tab="login"]');
    const registerTab = document.querySelector('.login-tab[data-tab="register"]');
    const switchTabs = document.getElementById('switchTabs');
    const signupForm = document.getElementById('registerFormContainer');
    const disabledState = document.getElementById('disabledState');
    const formContainer = document.querySelector('.form-container');
    const privacyNotice = document.getElementById('privacyNotice');
    const privacyNoticeRow = document.getElementById('privacyNoticeRow');
    const privacyNoticeInfo = document.getElementById('privacyNoticeInfo');
    const privacyNoticeLink = document.getElementById('privacyNoticeLink');
    const termsNoticeRow = document.getElementById('termsNoticeRow');
    const termsNoticeInfo = document.getElementById('termsNoticeInfo');
    const termsNoticeLink = document.getElementById('termsNoticeLink');
    const signupTermsConsent = document.getElementById('signupTermsConsent');
    const signupTermsConsentCheckbox = document.getElementById('signupTermsConsentCheckbox');
    const passkeyButton = document.getElementById('passkeySigninButton');
    const adminLoginButton = document.getElementById('adminLoginButton');

    // Enable Signin
    if (!enableSignin) {
        if (loginFormElement) loginFormElement.style.display = 'none';
    } else {
        // Ensure login form and tab remain visible when signin is enabled
        if (loginForm) loginForm.style.display = '';
        if (loginTab) loginTab.style.display = '';
        if (loginFormElement) loginFormElement.style.display = '';
    }

    // Enable Signup
    if (!enableSignup) {
        if (signupForm) signupForm.style.display = 'none';
        if (switchTabs) switchTabs.style.display = 'none';
        if (registerTab) registerTab.style.display = 'none';
    } else if (registerTab) {
        registerTab.style.display = '';
    }

    // Show global disabled state when both sign-in and sign-up are disabled
    if (!enableSignin && !enableSignup) {
        updateDisabledStateCopy();
        // Hide any form-related UI parts
        if (formContainer) formContainer.style.display = 'none';
        if (switchTabs) switchTabs.style.display = 'none';
        // Show the disabled-state message card
        if (disabledState) disabledState.style.display = 'block';
        // Show admin login button so admins can still access
        if (adminLoginButton && disabledState && adminLoginButton.parentElement !== disabledState) {
            disabledState.appendChild(adminLoginButton);
        }
        if (adminLoginButton) adminLoginButton.style.display = '';
    } else if (!enableSignin && enableSignup) {
        if (switchTabs) switchTabs.style.display = '';
        if (loginTab) loginTab.style.display = '';
        if (registerTab) registerTab.style.display = '';
        if (disabledState) disabledState.style.display = 'none';
        if (formContainer) formContainer.style.display = '';
        if (loginForm) loginForm.style.display = '';
        if (signupForm) signupForm.style.display = '';
        const activeTab = document.querySelector('.login-tab.active');
        const targetTab = activeTab || loginTab;
        if (targetTab) {
            setActiveTab(targetTab, { shouldFocus: false, shouldAnimate: false });
        }
    } else {
        // Ensure disabled-state is hidden when at least one auth method is active
        if (disabledState) disabledState.style.display = 'none';
        if (formContainer) formContainer.style.display = '';
        if (adminLoginButton) adminLoginButton.style.display = 'none';
    }

    // Setup admin login button click handler
    if (adminLoginButton) {
        adminLoginButton.onclick = showAdminLoginForm;
    }

    // Enable Password Reset
    if (!enablePasswordReset) {
        const forgotPasswordReset = document.getElementById('forgotPasswordReset');
        if (forgotPasswordReset) forgotPasswordReset.hidden = true;
    }
    if (typeof window.updateSigninPasswordResetVisibility === 'function') {
        window.updateSigninPasswordResetVisibility();
    }
    // Show custom logo
    if (!showCustomLogo) {
        hideLoginLogo();
    } else {
        applyLoginLogoHeight(getLoginLogoContainer());
        applyLoginLogoHeight(getBrandingLogoContainer());
        void updateLogoForTheme();
        if (!themeObserverInitialized) {
            observeThemeChangeForLogo();
            themeObserverInitialized = true;
        }
    }

    // Update title, which is shown in the brosers tab
    const translatedLoginTitle = typeof window.getTranslation === 'function'
        ? window.getTranslation('login_title', 'Login')
        : 'Login';
    if (typeof window.setDocumentTitleWithAppName === 'function') {
        window.setDocumentTitleWithAppName(translatedLoginTitle);
    } else {
        const newTitle = applicationName + " - " + translatedLoginTitle;
        document.title = newTitle;
    }
    if (typeof window.syncPasswordActionFlowVisibility === 'function') {
        window.syncPasswordActionFlowVisibility();
    }

    // Applying an image-backed split design includes fetching and decoding the
    // image so the first visible design frame is already complete.
    await applyLoginDesign();

    // Update Contact Support button visibility and link
    updateContactSupportButton();

    if (passkeyButton) {
        const canUsePasskeys = Boolean(enablePasskeys) && typeof window.PublicKeyCredential === 'function';
        if (!canUsePasskeys) {
            passkeyButton.hidden = true;
            passkeyButton.style.display = 'none';
        }
    }

    if (privacyNotice) {
        const shouldShowPrivacyNotice = Boolean(showPrivacyNoticeLink);
        const shouldShowTermsNotice = Boolean(showTermsOfServiceLink);
        const shouldShowFooterNotice = shouldShowPrivacyNotice || shouldShowTermsNotice;

        privacyNotice.style.display = shouldShowFooterNotice ? '' : 'none';
        privacyNotice.hidden = !shouldShowFooterNotice;

        if (privacyNoticeInfo) {
            privacyNoticeInfo.hidden = !shouldShowPrivacyNotice;
        }
        if (privacyNoticeLink) {
            privacyNoticeLink.hidden = !shouldShowPrivacyNotice;
        }
        if (privacyNoticeRow) {
            privacyNoticeRow.hidden = !shouldShowPrivacyNotice;
        }
        if (termsNoticeRow) {
            termsNoticeRow.hidden = !shouldShowTermsNotice;
        }
        if (termsNoticeInfo) {
            termsNoticeInfo.hidden = !shouldShowTermsNotice;
        }
        if (termsNoticeLink) {
            termsNoticeLink.hidden = !shouldShowTermsNotice;
        }

        if (shouldShowFooterNotice && typeof getTranslation === 'function') {
            if (privacyNoticeInfo) {
                privacyNoticeInfo.textContent = getTranslation('privacy_notice_info', privacyNoticeInfo.textContent);
            }

            if (privacyNoticeLink) {
                privacyNoticeLink.textContent = getTranslation('privacy_notice_link', privacyNoticeLink.textContent);
            }

            if (termsNoticeInfo) {
                termsNoticeInfo.textContent = getTranslation('terms_notice_info', termsNoticeInfo.textContent);
            }

            if (termsNoticeLink) {
                termsNoticeLink.textContent = getTranslation('terms_notice_link', termsNoticeLink.textContent);
            }
        }
    }

    if (signupTermsConsent && signupTermsConsentCheckbox) {
        const shouldRequireConsent = shouldRequireCurrentTermsForSignup();
        setTermsConsentControlState(signupTermsConsent, signupTermsConsentCheckbox, shouldRequireConsent, {
            required: true,
        });
    }

    if (typeof window.updateSignupButtonState === 'function') {
        window.updateSignupButtonState();
    }
}


// ------------------------------
// Login Design Variants
// ------------------------------
async function applyLoginDesign() {
    const layout = document.querySelector('.login-layout');
    const brandingPanel = document.querySelector('.login-branding');
    const brandingTitleEl = document.querySelector('.branding-title');
    const brandingSubtitleEl = document.querySelector('.branding-subtitle');
    const brandingLogo = document.querySelector('.branding-logo');
    const brandingText = document.querySelector('.branding-text');
    
    if (!layout) return;
    
    // Remove all design variant classes first
    layout.classList.remove(
        'design-classic',
        'design-split',
        'design-split-image',
        'design-centered',
        'design-minimal',
        'design-glass'
    );
    
    // Apply the selected design variant
    layout.classList.add(`design-${loginDesign.replace('_', '-')}`);
    
    // Update branding text
    if (brandingTitleEl) {
        brandingTitleEl.textContent = resolveBrandingCopy(brandingTitle, DEFAULT_BRANDING_TITLE);
    }
    if (brandingSubtitleEl) {
        brandingSubtitleEl.textContent = resolveBrandingCopy(brandingSubtitle, DEFAULT_BRANDING_SUBTITLE);
    }
    
    // Handle branding text visibility
    if (brandingText) {
        const hidesBrandingPanel = loginDesign === 'centered' || loginDesign === 'glass';
        const shouldShowBrandingText = !hidesBrandingPanel && showBrandingText;
        brandingText.style.display = shouldShowBrandingText ? '' : 'none';
    }

    if (brandingLogo) {
        const hidesBrandingPanel = loginDesign === 'centered' || loginDesign === 'glass';
        const shouldShowBrandingLogo = !hidesBrandingPanel && showCustomLogo;
        brandingLogo.style.display = shouldShowBrandingLogo ? '' : 'none';
    }
    
    const shouldUseBackgroundImage = showBackgroundImage && loginDesign === 'split_image';

    // Handle background image
    if (shouldUseBackgroundImage) {
        await loadLoginBackgroundImage();
    } else if (layout) {
        revokeLoginBackgroundUrl();
        layout.style.removeProperty('--login-bg-image');
        if (brandingPanel) {
            brandingPanel.classList.remove('has-background-image');
        }
    }
    
    // Set overlay opacity CSS variable
    if (layout) {
        layout.style.setProperty('--bg-overlay-opacity', backgroundOverlayOpacity / 100);
        const useCustomImageSizing = shouldUseBackgroundImage;
        const backgroundSize = useCustomImageSizing
            ? (backgroundImageFit === 'custom_percent' ? `${backgroundImageSizePercent}%` : backgroundImageFit)
            : 'cover';
        layout.style.setProperty('--login-bg-size', backgroundSize);
        const designSupportsSolidBackground = ['split', 'split_image', 'centered', 'glass'].includes(loginDesign);
        // Centered and glass designs place the legal notice over the configured
        // design background. Split designs keep it in the content pane, where
        // it must inherit the global light/dark semantic color variables.
        const privacyNoticeUsesDesignBackground = ['centered', 'glass'].includes(loginDesign);
        if (designSupportsSolidBackground) {
            if (designBackgroundColor) {
                layout.style.setProperty('--login-design-bg', designBackgroundColor);
            } else {
                layout.style.removeProperty('--login-design-bg');
            }
        } else {
            layout.style.removeProperty('--login-design-bg');
        }

        if (privacyNoticeUsesDesignBackground && designBackgroundColor) {
            const privacyColors = getReadablePrivacyColors(designBackgroundColor);
            if (privacyColors.text) {
                layout.style.setProperty('--privacy-note-color', privacyColors.text);
            } else {
                layout.style.removeProperty('--privacy-note-color');
            }
            if (privacyColors.link) {
                layout.style.setProperty('--privacy-note-link-color', privacyColors.link);
            } else {
                layout.style.removeProperty('--privacy-note-link-color');
            }
        } else {
            layout.style.removeProperty('--privacy-note-color');
            layout.style.removeProperty('--privacy-note-link-color');
        }

        if (loginDesign === 'split' || loginDesign === 'split_image') {
            if (designBackgroundColor) {
                const brandingColors = getReadableBrandingColors(designBackgroundColor);
                layout.style.setProperty('--login-branding-text-color', brandingColors.text);
                layout.style.setProperty('--login-branding-text-shadow', brandingColors.shadow);
            } else {
                layout.style.removeProperty('--login-branding-text-color');
                layout.style.removeProperty('--login-branding-text-shadow');
            }
        } else {
            layout.style.removeProperty('--login-branding-text-color');
            layout.style.removeProperty('--login-branding-text-shadow');
        }
    }

    if (brandingLogo) {
        applyLoginLogoHeight(brandingLogo);
    }
    
    // Handle branding logo in branding panel
    if (brandingLogo && showCustomLogo && loginDesign !== 'centered' && loginDesign !== 'glass') {
        void updateBrandingLogoForTheme();
    }
}

/**
 * Wait until an object URL can be painted as an image.
 *
 * Fetching the blob alone is insufficient: CSS may otherwise expose the
 * fallback background for a frame while the browser decodes the image. The
 * load handlers also provide compatibility for browsers without decode().
 *
 * @param {string} url Object URL for the downloaded login background.
 * @returns {Promise<void>} Resolves once the image is ready to paint.
 */
function decodeLoginBackgroundImage(url) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        let settled = false;
        let timeoutId = null;

        const resolveOnce = () => {
            if (settled) return;
            settled = true;
            if (timeoutId !== null) window.clearTimeout(timeoutId);
            resolve();
        };
        const rejectOnce = (error = new Error('Failed to decode the login background image')) => {
            if (settled) return;
            settled = true;
            if (timeoutId !== null) window.clearTimeout(timeoutId);
            reject(error);
        };

        timeoutId = window.setTimeout(() => {
            rejectOnce(new Error('Timed out while decoding the login background image'));
        }, LOGIN_BACKGROUND_READY_TIMEOUT_MS);

        image.onload = resolveOnce;
        image.onerror = rejectOnce;
        image.src = url;

        if (typeof image.decode === 'function') {
            image.decode().then(resolveOnce).catch(() => {
                // Some browsers reject decode() for images they can still
                // display. In that case the load/error handlers decide.
                if (image.complete && image.naturalWidth > 0) {
                    resolveOnce();
                }
            });
        }
    });
}

// Load and apply the login background image
async function loadLoginBackgroundImage() {
    const brandingPanel = document.querySelector('.login-branding');
    const loginLayout = document.querySelector('.login-layout');
    if (!brandingPanel || !loginLayout) return;

    const abortController = typeof AbortController === 'function'
        ? new AbortController()
        : null;
    const timeoutId = window.setTimeout(() => {
        abortController?.abort();
    }, LOGIN_BACKGROUND_READY_TIMEOUT_MS);
    
    try {
        const response = await fetch('/api/v1/settings/login-background/get', {
            credentials: 'include',
            ...(abortController ? { signal: abortController.signal } : {}),
        });
        
        if (!response.ok) {
            // No background image available
            revokeLoginBackgroundUrl();
            loginLayout.style.removeProperty('--login-bg-image');
            brandingPanel.classList.remove('has-background-image');
            return;
        }
        
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);

        try {
            await decodeLoginBackgroundImage(url);
        } catch (error) {
            URL.revokeObjectURL(url);
            throw error;
        }

        // Keep any previously displayed object URL alive until its replacement
        // is decoded, then swap the CSS value without an intermediate frame.
        revokeLoginBackgroundUrl();
        loginBackgroundObjectUrl = url;
        loginLayout.style.setProperty('--login-bg-image', `url(${url})`);
        brandingPanel.classList.add('has-background-image');
    } catch (error) {
        revokeLoginBackgroundUrl();
        loginLayout.style.removeProperty('--login-bg-image');
        brandingPanel.classList.remove('has-background-image');
        console.error('Failed to load login background image:', error);
    } finally {
        window.clearTimeout(timeoutId);
    }
}

// Update branding logo for the current theme (on the branding panel)
async function updateBrandingLogoForTheme() {
    if (!showCustomLogo) return;
    
    const brandingLogo = document.querySelector('.branding-logo');
    if (!brandingLogo) return;

    applyLoginLogoHeight(brandingLogo);
    
    // For branding panel, always use light theme logo (inverted for dark backgrounds)
    const entry = await loadLoginLogoEntry('light') || await loadLoginLogoEntry('dark');
    if (!entry) {
        brandingLogo.style.display = 'none';
        return;
    }
    
    brandingLogo.replaceChildren();

    if (appendLogoImage(brandingLogo, entry)) {
        brandingLogo.style.display = 'flex';
        return;
    }

    brandingLogo.style.display = 'none';
}


// ------------------------------
// Tab switching (with ARIA management)
// ------------------------------
const tabs = document.querySelectorAll('.login-tab');
const forms = document.querySelectorAll('.form-content');
const formAnimationSelector = '.input-group, .button, .text-button, .signin-step-copy, .signin-stage-heading, .forgot-password-reset, .social-login-divider, .social-login-buttons, .social-divider, .social-buttons, .social-btn, .form-group, .btn';

function isElementVisibleForAnimation(element) {
    if (!(element instanceof HTMLElement)) {
        return false;
    }
    if (element.hidden || element.closest('[hidden]')) {
        return false;
    }
    return element.getClientRects().length > 0;
}

function getAnimatedFormElements(form) {
    if (!form) {
        return [];
    }
    return Array.from(form.querySelectorAll(formAnimationSelector)).filter(isElementVisibleForAnimation);
}

function setActiveTab(activeTab, options = {}) {
    const { shouldFocus = true, shouldAnimate = true } = options;
    const target = activeTab.dataset.tab;
    let activeForm = null;

    // Update visual active state and ARIA for tabs
    tabs.forEach(t => {
        const isActive = t === activeTab;
        t.classList.toggle('active', isActive);
        t.setAttribute('aria-selected', isActive ? 'true' : 'false');
        t.setAttribute('tabindex', isActive ? '0' : '-1');
        if (isActive) {
            updateTabUnderline(t);
        }
    });

    // Show/hide corresponding tabpanels with proper ARIA
    forms.forEach(form => {
        const shouldShow = form.id === `${target}FormContainer`;
        form.classList.toggle('active', shouldShow);
        form.setAttribute('aria-hidden', shouldShow ? 'false' : 'true');
        if (shouldShow) {
            activeForm = form;
            form.removeAttribute('hidden');
        } else {
            form.setAttribute('hidden', '');
        }
    });

    // Reset signin flow when switching to login tab to show email input
    if (
        target === 'login'
        && typeof window.resetSigninFlow === 'function'
        && !window.isPasswordActionFlowActive?.()
    ) {
        window.resetSigninFlow();
    }

    syncSigninDisabledSignupEnabledLayout(target);

    if (shouldAnimate && activeForm) {
        const animatedElements = getAnimatedFormElements(activeForm);
        animateItems(animatedElements);
    }

    // Move focus to the newly active tab for keyboard users
    if (shouldFocus) {
        activeTab.focus();
    }
}

function placeAdminLoginButton(parent, anchor = null) {
    const adminLoginButton = document.getElementById('adminLoginButton');
    if (!parent || !adminLoginButton) {
        return;
    }

    if (anchor && anchor.parentElement === parent) {
        const nextSibling = anchor.nextSibling;
        if (nextSibling) {
            parent.insertBefore(adminLoginButton, nextSibling);
        } else {
            parent.appendChild(adminLoginButton);
        }
    } else if (adminLoginButton.parentElement !== parent) {
        parent.appendChild(adminLoginButton);
    }
}

function updateDisabledStateCopy({
    variant = 'default',
} = {}) {
    const disabledStateTitle = document.getElementById('disabledStateTitle');
    const disabledStateMessage = document.getElementById('disabledStateMessage');
    const translate = (key, fallback) =>
        typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback)
            : fallback;
    let titleText = translate('disabled_title', 'Sign In Temporarily Disabled');
    let messageText = translate(
        'disabled_message',
        'Sign-in and registration are currently unavailable. Please try again later.',
    );

    if (variant === 'signin-disabled-for-users') {
        messageText = translate(
            'signin_error_disabled_for_users',
            'Sign-in is currently disabled for users. Only administrators can sign in.',
        );
    }

    if (disabledStateTitle) {
        disabledStateTitle.textContent = titleText;
    }

    if (disabledStateMessage) {
        disabledStateMessage.textContent = messageText;
    }
}

function syncSigninDisabledSignupEnabledLayout(activeTabName = null) {
    const adminLoginButton = document.getElementById('adminLoginButton');
    const disabledState = document.getElementById('disabledState');
    const formContainer = document.querySelector('.form-container');
    const loginForm = document.getElementById('loginForm');

    if (!adminLoginButton || !loginForm) {
        return;
    }

    if (enableSignin || !enableSignup || adminLoginMode) {
        if (disabledState) disabledState.style.display = 'none';
        if (formContainer) formContainer.style.display = '';
        loginForm.style.display = '';
        adminLoginButton.style.display = 'none';
        return;
    }

    const resolvedTabName = activeTabName
        || document.querySelector('.login-tab.active')?.dataset?.tab
        || 'login';

    if (resolvedTabName === 'login') {
        updateDisabledStateCopy({
            variant: 'signin-disabled-for-users',
        });
        if (disabledState) {
            disabledState.style.display = 'block';
            placeAdminLoginButton(disabledState);
        }
        if (formContainer) formContainer.style.display = 'none';
        loginForm.style.display = 'none';
        adminLoginButton.style.display = '';
    } else {
        if (disabledState) disabledState.style.display = 'none';
        if (formContainer) formContainer.style.display = '';
        loginForm.style.display = '';
        adminLoginButton.style.display = 'none';
    }
}

// Mouse/touch activate
tabs.forEach(tab => {
    tab.addEventListener('click', () => setActiveTab(tab, { shouldFocus: false }));
});

// Keyboard support for tabs (ArrowLeft/Right, Home/End, Enter/Space)
tabs.forEach((tab, index) => {
    tab.addEventListener('keydown', (e) => {
        const key = e.key;
        let newIndex = index;
        if (key === 'ArrowRight') {
            e.preventDefault();
            newIndex = (index + 1) % tabs.length;
            setActiveTab(tabs[newIndex]);
        } else if (key === 'ArrowLeft') {
            e.preventDefault();
            newIndex = (index - 1 + tabs.length) % tabs.length;
            setActiveTab(tabs[newIndex]);
        } else if (key === 'Home') {
            e.preventDefault();
            setActiveTab(tabs[0]);
        } else if (key === 'End') {
            e.preventDefault();
            setActiveTab(tabs[tabs.length - 1]);
        } else if (key === 'Enter' || key === ' ') {
            e.preventDefault();
            setActiveTab(tab);
        }
    });
});

function updateTabUnderline(activeTab) {
    const allTabs = document.querySelectorAll('.login-tab');
    activeTab.style.color = 'var(--primary-color)';

    // Update the underline
    activeTab.classList.add('active');
    const otherTabs = Array.from(allTabs).filter(tab => tab !== activeTab);
    otherTabs.forEach(tab => {
        tab.classList.remove('active');
        tab.style.color = 'var(--text-color-secondary)';
    });
}









// ------------------------------
// Logo settings
// ------------------------------
function getLoginLogoContainer() {
    // For mobile view, use the mobile logo container
    return document.getElementById('mobileLogoContainer');
}

function getBrandingLogoContainer() {
    // For desktop view, use the branding panel logo container
    return document.querySelector('.branding-logo');
}

function getEffectiveLogoTheme() {
    const root = document.documentElement;
    const modeAttr = root.getAttribute('data-mode');
    const themeAttr = root.getAttribute('data-theme');

    if (modeAttr === 'dark' || modeAttr === 'light') {
        return modeAttr;
    }

    if (themeAttr === 'dark' || themeAttr === 'light') {
        return themeAttr;
    }

    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function getLoginLogoHeight() {
    if (customLogoHeight === 'small') {
        return '40px';
    }
    if (customLogoHeight === 'medium') {
        return '60px';
    }
    if (customLogoHeight === 'big') {
        return '100px';
    }
    return '80px';
}

function applyLoginLogoHeight(container) {
    if (!container) {
        return;
    }
    container.style.setProperty('--login-logo-height', getLoginLogoHeight());
}

function hideLoginLogo() {
    // Hide mobile logo
    const mobileContainer = getLoginLogoContainer();
    if (mobileContainer) {
        mobileContainer.replaceChildren();
        mobileContainer.style.display = 'none';
    }
    // Hide branding panel logo
    const brandingContainer = getBrandingLogoContainer();
    if (brandingContainer) {
        brandingContainer.replaceChildren();
        brandingContainer.style.display = 'none';
    }
}

function normalizeLogoMimeType(value) {
    return String(value || '').split(';')[0].trim().toLowerCase();
}

function appendLogoImage(container, entry, options = {}) {
    if (!container || !entry?.url) {
        return false;
    }

    const image = document.createElement('img');
    image.className = 'logo';
    if (options.id) {
        image.id = options.id;
    }
    image.alt = typeof window.getTranslation === 'function' ? window.getTranslation('logo_aria', 'Logo') : 'Logo';
    image.src = entry.url;
    container.appendChild(image);
    return true;
}

function revokeLoginLogoUrl(theme) {
    const existingUrl = loginLogoState.objectUrls.get(theme);
    if (!existingUrl) {
        return;
    }
    URL.revokeObjectURL(existingUrl);
    loginLogoState.objectUrls.delete(theme);
}

async function loadLoginLogoEntry(theme) {
    if (loginLogoState.entries[theme] !== undefined) {
        return loginLogoState.entries[theme];
    }

    if (loginLogoState.pending[theme]) {
        return loginLogoState.pending[theme];
    }

    loginLogoState.pending[theme] = fetch(`/api/v1/settings/logo/get?theme=${encodeURIComponent(theme)}`, {
        credentials: 'include',
    })
        .then(async (response) => {
            if (!response.ok) {
                loginLogoState.entries[theme] = null;
                return null;
            }

            const blob = await response.blob();
            const contentType = normalizeLogoMimeType(response.headers.get('content-type')) || normalizeLogoMimeType(blob.type);

            revokeLoginLogoUrl(theme);
            const url = URL.createObjectURL(blob);
            loginLogoState.objectUrls.set(theme, url);
            loginLogoState.entries[theme] = {
                type: contentType,
                url,
            };
            return loginLogoState.entries[theme];
        })
        .catch((error) => {
            console.error(`Failed to load ${theme} login logo`, error);
            loginLogoState.entries[theme] = null;
            return null;
        })
        .finally(() => {
            loginLogoState.pending[theme] = null;
        });

    return loginLogoState.pending[theme];
}

function renderLoginLogo(entry) {
    const container = getLoginLogoContainer();
    if (!container) {
        return;
    }

    container.replaceChildren();
    applyLoginLogoHeight(container);

    if (!entry) {
        container.style.display = 'none';
        return;
    }

    if (appendLogoImage(container, entry, { id: 'logoImage' })) {
        container.style.display = 'flex';
        return;
    }

    container.style.display = 'none';
}

async function updateLogoForTheme() {
    if (!showCustomLogo) {
        hideLoginLogo();
        return;
    }

    const preferredTheme = getEffectiveLogoTheme();
    const fallbackTheme = preferredTheme === 'dark' ? 'light' : 'dark';
    const entry = await loadLoginLogoEntry(preferredTheme) || await loadLoginLogoEntry(fallbackTheme);
    renderLoginLogo(entry);
}

function observeThemeChangeForLogo() {
    const observer = new MutationObserver(() => {
        void updateLogoForTheme();
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-mode'] });
}


// ------------------------------
// Admin Login Mode
// ------------------------------
function showAdminLoginForm() {
    adminLoginMode = true;
    const preserveTabSwitcher = !enableSignin && enableSignup;
    const disabledState = document.getElementById('disabledState');
    const formContainer = document.querySelector('.form-container');
    const loginForm = document.getElementById('loginFormContainer');
    const loginFormElement = document.getElementById('loginForm');
    const signupForm = document.getElementById('registerFormContainer');
    const switchTabs = document.getElementById('switchTabs');
    const loginFormTitle = document.getElementById('loginFormTitle');
    const adminLoginButton = document.getElementById('adminLoginButton');

    // Hide disabled state and show login form
    if (disabledState) disabledState.style.display = 'none';
    if (adminLoginButton) adminLoginButton.style.display = 'none';
    if (formContainer) formContainer.style.display = '';
    if (loginForm) {
        loginForm.style.display = '';
        loginForm.classList.add('active');
    }
    if (loginFormElement) loginFormElement.style.display = '';
    if (signupForm) signupForm.style.display = preserveTabSwitcher ? '' : 'none';
    if (switchTabs) switchTabs.style.display = preserveTabSwitcher ? '' : 'none';

    // Update form title to indicate admin login
    if (loginFormTitle) {
        const translate = (key, fallback) =>
            typeof window.getTranslation === 'function'
                ? window.getTranslation(key, fallback)
                : fallback;
        loginFormTitle.textContent = translate('admin_login_title', 'Admin Sign In');
    }

    // Animate form elements
    if (loginForm) {
        const animatedElements = getAnimatedFormElements(loginForm);
        animateItems(animatedElements);
    }
}

// Initialize the UI after all functions are defined
initializeUI();




// ------------------------------
// Contact Support button handling
// ------------------------------
// Show or hide the support buttons and set their mailto link
function updateContactSupportButton() {
    const warningSupport = document.getElementById('warningContactSupport');
    const pendingSupport = document.getElementById('pendingContactSupport');

    const email = (typeof contactSupportEmail === 'string' ? contactSupportEmail.trim() : '');
    const hasValidEmail = email && isValidEmail(email);

    [warningSupport, pendingSupport].forEach(btn => {
        if (!btn) return;
        if (hasValidEmail) {
            btn.href = `mailto:${email}`;
            btn.style.display = '';
        } else {
            // Hide the button when no valid email is configured
            btn.style.display = 'none';
        }
    });
}

// ------------------------------
// Last Used Login Method Label
// ------------------------------
const LAST_USED_LOGIN_KEY = 'lastUsedLoginMethod';

/**
 * Save the last used login method to localStorage.
 * @param {string} method - The login method: 'email' or provider name like 'google', 'microsoft', 'apple'
 */
function saveLastUsedLoginMethod(method) {
    try {
        localStorage.setItem(LAST_USED_LOGIN_KEY, method);
    } catch (e) {
        console.error('Failed to save last used login method:', e);
    }
}

/**
 * Get the last used login method from localStorage.
 * @returns {string|null} The last used login method or null if not set
 */
function getLastUsedLoginMethod() {
    try {
        return localStorage.getItem(LAST_USED_LOGIN_KEY);
    } catch (e) {
        console.error('Failed to get last used login method:', e);
        return null;
    }
}

/**
 * Show the "Last used" label on the appropriate login button.
 * Call this after the page has loaded and social login buttons are shown.
 */
function showLastUsedLabel() {
    const lastUsed = getLastUsedLoginMethod();
    if (!lastUsed) return;

    // Hide all last used labels first
    const emailLabel = document.getElementById('lastUsedEmail');
    const googleLabel = document.getElementById('lastUsedGoogle');
    const githubLabel = document.getElementById('lastUsedGithub');
    const slackLabel = document.getElementById('lastUsedSlack');
    const microsoftLabel = document.getElementById('lastUsedMicrosoft');
    const appleLabel = document.getElementById('lastUsedApple');
    const passkeyLabel = document.getElementById('lastUsedPasskey');

    if (emailLabel) emailLabel.style.display = 'none';
    if (googleLabel) googleLabel.style.display = 'none';
    if (githubLabel) githubLabel.style.display = 'none';
    if (slackLabel) slackLabel.style.display = 'none';
    if (microsoftLabel) microsoftLabel.style.display = 'none';
    if (appleLabel) appleLabel.style.display = 'none';
    if (passkeyLabel) passkeyLabel.style.display = 'none';

    // Show the label for the last used method
    if (lastUsed === 'email' && emailLabel) {
        emailLabel.style.display = 'inline-block';
    } else if (lastUsed === 'google' && googleLabel) {
        googleLabel.style.display = 'inline-block';
    } else if (lastUsed === 'github' && githubLabel) {
        githubLabel.style.display = 'inline-block';
    } else if (lastUsed === 'slack' && slackLabel) {
        slackLabel.style.display = 'inline-block';
    } else if (lastUsed === 'microsoft' && microsoftLabel) {
        microsoftLabel.style.display = 'inline-block';
    } else if (lastUsed === 'apple' && appleLabel) {
        appleLabel.style.display = 'inline-block';
    } else if (lastUsed === 'passkey' && passkeyLabel) {
        passkeyLabel.style.display = 'inline-block';
    }
}

// Expose functions globally for use in authentication.js and socialLogin.js
window.loginMethodTracker = {
    saveLastUsedLoginMethod,
    getLastUsedLoginMethod,
    showLastUsedLabel
};
