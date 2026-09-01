(function () {
let authSessionReady = false;
let refreshPromise = null; // in-flight refresh promise to dedupe concurrent refreshes
let applicationName = "Omlorix";
let activeAccountSlot = null;
let browserAccounts = [];
let initialAuthBootstrapPromise = null;
let initialAuthBootstrapFailed = false;
let lastAccessTimeBlockedDetail = null;
let termsAcceptanceRedirectPending = false;
let passwordChangeRedirectPending = false;
const AUTH_REFRESH_LOCK_NAME = 'omlorix-auth-refresh';
const AUTH_REFRESH_RACE_MAX_ATTEMPTS = 4;
const AUTH_REFRESH_RACE_DEFAULT_DELAY_MS = 250;
const AUTH_REFRESH_REQUEST_TIMEOUT_MS = 15000;
const AUTH_REFRESH_LOCK_TIMEOUT_MS = 10000;
const PROMPT_SHARE_PENDING_STORAGE_KEY = 'omlorix_pending_prompt_share';
const documentTitleState = {
    initialized: false,
    section: '',
    sectionKey: '',
};

/**
 * Capture prompt bearer links before authentication can copy them into a login
 * redirect query. The workspace prompt manager consumes the session-scoped
 * intent after authentication and treats the preview response as authoritative.
 */
function capturePromptShareRedirectIntent() {
    if (typeof window === 'undefined') return false;
    const match = /^\/prompts\/(clone|live|collaborate)\/([^/]+)\/?$/.exec(window.location.pathname || '');
    if (!match) return false;

    let shareId;
    try {
        shareId = decodeURIComponent(match[2]).trim();
    } catch (_) {
        return false;
    }
    if (
        !shareId
        || shareId.length > 512
        || shareId.includes('/')
        || shareId.includes('\\')
    ) return false;

    const intent = JSON.stringify({ shareId, shareType: match[1] });
    let preserved = false;
    try {
        window.sessionStorage?.setItem(PROMPT_SHARE_PENDING_STORAGE_KEY, intent);
        preserved = window.sessionStorage?.getItem(PROMPT_SHARE_PENDING_STORAGE_KEY) === intent;
    } catch (error) {
        console.warn('Could not preserve the prompt share through sign-in:', error);
    }

    // Without a stored copy the capability exists only in the address bar.
    // Keep the original route so the prompt manager can consume it directly.
    if (!preserved) return false;

    try {
        window.history.replaceState(
            { ...(window.history.state || {}), workspaceTab: 'prompts', pendingPromptShare: preserved },
            '',
            '/workspace/prompts',
        );
    } catch (error) {
        console.warn('Could not replace the prompt share URL before sign-in:', error);
        window.location.replace('/workspace/prompts');
    }
    return preserved;
}

function normalizeApplicationName(name) {
    if (typeof name !== 'string') {
        return 'Omlorix';
    }
    const trimmed = name.trim();
    return trimmed || 'Omlorix';
}

function setApplicationName(nextName) {
    const resolvedName = normalizeApplicationName(nextName);
    applicationName = resolvedName;
    if (typeof window !== 'undefined') {
        window.applicationName = resolvedName;
        refreshDocumentTitleWithAppName();
        window.dispatchEvent(new CustomEvent('app:applicationNameUpdated', {
            detail: { applicationName: resolvedName },
        }));
    }
    return resolvedName;
}

function getApplicationName() {
    return normalizeApplicationName(applicationName);
}

function translateDocumentTitleValue(key, fallback) {
    if (key && typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function formatDocumentTitle(appName, section) {
    if (!section) {
        return appName;
    }
    const template = translateDocumentTitleValue(
        'document_title_format',
        '{appName} - {section}'
    );
    return String(template)
        .replace(/\{appName\}/g, appName)
        .replace(/\{section\}/g, section);
}

function refreshDocumentTitleWithAppName() {
    if (!documentTitleState.initialized) {
        return document.title;
    }
    const appName = getApplicationName();
    const section = translateDocumentTitleValue(
        documentTitleState.sectionKey,
        documentTitleState.section
    );
    const title = formatDocumentTitle(appName, section);
    document.title = title;
    return title;
}

function setDocumentTitleWithAppName(section = '', options = {}) {
    const opts = options && typeof options === 'object' ? options : {};
    documentTitleState.initialized = true;
    documentTitleState.section = typeof section === 'string' ? section.trim() : '';
    documentTitleState.sectionKey = typeof opts.sectionKey === 'string' ? opts.sectionKey : '';
    return refreshDocumentTitleWithAppName();
}

function handleDocumentTitleI18nUpdated() {
    if (documentTitleState.sectionKey) {
        refreshDocumentTitleWithAppName();
    }
}

setApplicationName(applicationName);

if (typeof window !== 'undefined') {
    window.getApplicationName = getApplicationName;
    window.setApplicationName = setApplicationName;
    window.setDocumentTitleWithAppName = setDocumentTitleWithAppName;
    window.refreshDocumentTitleWithAppName = refreshDocumentTitleWithAppName;
    document.addEventListener('i18n:updated', handleDocumentTitleI18nUpdated);
}


function getUrlParams() {
    return new URLSearchParams(window.location.search);
}

function getLegacyRedirectTarget() {
    const raw = getUrlParams().get('redirect') || '';
    if (!raw) {
        return '';
    }
    try {
        return normalizeInternalPath(decodeURIComponent(raw));
    } catch (error) {
        return normalizeInternalPath(raw);
    }
}

function normalizeInternalPath(path) {
    if (typeof path !== 'string') {
        return '';
    }
    const trimmed = path.trim();
    if (!trimmed || trimmed.includes('\\')) {
        return '';
    }
    try {
        const parsed = new URL(trimmed, window.location.origin);
        if (parsed.origin !== window.location.origin) {
            return '';
        }
        if (!parsed.pathname.startsWith('/') || parsed.pathname.startsWith('//')) {
            return '';
        }
        return `${parsed.pathname}${parsed.search}${parsed.hash}`;
    } catch (error) {
        return '';
    }
}

function isTransientAuthRoute(pathname = window.location.pathname) {
    const normalized = normalizeInternalPath(pathname) || '/';
    return normalized === '/login'
        || normalized.endsWith('/login')
        || normalized === '/server_setup'
        || normalized.endsWith('/server_setup')
        || normalized === '/change_password'
        || normalized.endsWith('/change_password');
}

function getMainPageUrl() {
    return '/';
}

function isChangePasswordRoute(pathname = window.location.pathname) {
    const normalized = normalizeInternalPath(pathname) || '/';
    return normalized === '/change_password' || normalized.endsWith('/change_password');
}

function hasPasswordResetConfirmIntent() {
    try {
        const hash = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : window.location.hash;
        if (new URLSearchParams(hash).get('token')) {
            return true;
        }
        if (new URLSearchParams(window.location.search).get('token')) {
            return true;
        }
        return Boolean(window.sessionStorage?.getItem('password_reset_token'));
    } catch (error) {
        return false;
    }
}

function getCurrentAppPath() {
    if (isTransientAuthRoute()) {
        return getAccountReturnUrl() || getLegacyRedirectTarget() || getMainPageUrl();
    }
    const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    return normalizeInternalPath(currentPath) || getMainPageUrl();
}

function resolveAccountTransitionUrl() {
    return getMainPageUrl();
}

function normalizeAccessTimeBlockedDetail(detail = {}) {
    if (!detail || typeof detail !== 'object' || detail.type !== 'access_time_blocked') {
        return null;
    }
    return {
        type: 'access_time_blocked',
        reason: typeof detail.reason === 'string' ? detail.reason : '',
        next_allowed_at: typeof detail.next_allowed_at === 'string' ? detail.next_allowed_at : '',
        blocked_message: typeof detail.blocked_message === 'string' ? detail.blocked_message : '',
    };
}

function rememberAccessTimeBlockedDetail(detail = {}) {
    const normalized = normalizeAccessTimeBlockedDetail(detail);
    if (!normalized) {
        return null;
    }
    lastAccessTimeBlockedDetail = normalized;
    if (typeof window !== 'undefined') {
        window.__omlorixAccessBlockedRefreshDetail = normalized;
    }
    return normalized;
}

function consumeAccessTimeBlockedRefreshDetail() {
    const detail = lastAccessTimeBlockedDetail
        || (typeof window !== 'undefined' ? window.__omlorixAccessBlockedRefreshDetail : null);
    lastAccessTimeBlockedDetail = null;
    if (typeof window !== 'undefined') {
        delete window.__omlorixAccessBlockedRefreshDetail;
    }
    return normalizeAccessTimeBlockedDetail(detail);
}

function appendAccessTimeBlockedLoginParams(params, detail = {}) {
    const normalized = normalizeAccessTimeBlockedDetail(detail);
    if (!normalized) {
        return params;
    }
    params.set('access_blocked', 'access_time_blocked');
    if (normalized.reason) params.set('reason', normalized.reason);
    if (normalized.next_allowed_at) params.set('next_allowed_at', normalized.next_allowed_at);
    if (normalized.blocked_message) params.set('blocked_message', normalized.blocked_message);
    return params;
}

function buildLoginUrl({ redirect = '', mode = '', returnUrl = '', replaceSlot = null, accessBlocked = null, termsRequired = false } = {}) {
    const params = new URLSearchParams();
    if (redirect) params.set('redirect', redirect);
    if (mode === 'add') params.set('mode', 'add');
    if (returnUrl) params.set('return', returnUrl);
    if (replaceSlot) params.set('replace_slot', String(replaceSlot));
    if (termsRequired) params.set('terms_required', 'true');
    appendAccessTimeBlockedLoginParams(params, accessBlocked);
    const query = params.toString();
    return query ? `/login?${query}` : '/login';
}

function clearActiveUserLocalState() {
    // BYOK credential tokens are user-bound, but clearing them here prevents
    // stale tab state from surviving logout or an in-tab account transition.
    window.BYOK?.clearProviderSessionCredentials?.();
    [
        'firstName',
        'lastName',
        'email',
        'is_admin',
    ].forEach((key) => {
        try {
            localStorage.removeItem(key);
        } catch (error) {
            console.warn(`Failed to remove ${key} from localStorage:`, error);
        }
    });
    if (typeof window !== 'undefined') {
        delete window.chatSetup;
        // Do not let a language published for the signed-out account remain
        // authoritative if the next session belongs to another account.
        window.resetAuthenticatedLanguagePreference?.();
        delete window.__omlorixAuthenticatedLanguage;
    }
}

function resolvePostAuthRedirect(result = {}) {
    const returnUrl = getAccountReturnUrl();
    if (result.needs_server_setup) {
        return returnUrl ? `/server_setup?return=${encodeURIComponent(returnUrl)}` : '/server_setup';
    }
    return returnUrl || getLegacyRedirectTarget() || '/';
}

function isAddAccountMode() {
    return getUrlParams().get('mode') === 'add';
}

function getRequestedReplacementSlot() {
    const raw = getUrlParams().get('replace_slot');
    const parsed = Number.parseInt(raw || '', 10);
    return Number.isInteger(parsed) && parsed >= 1 && parsed <= 5 ? parsed : null;
}

function getAccountReturnUrl() {
    return normalizeInternalPath(getUrlParams().get('return') || '') || getLegacyRedirectTarget();
}

function resolveTermsAcceptanceReturnUrl() {
    return getAccountReturnUrl() || getLegacyRedirectTarget() || getMainPageUrl();
}

function normalizeTermsOfServicePolicy(policy = {}) {
    const source = policy && typeof policy === 'object' ? policy : {};
    return {
        ...source,
        revision: Number(source.revision || 0),
        accepted_current_revision: Boolean(source.accepted_current_revision),
        require_current_revision_for_access: Boolean(source.require_current_revision_for_access),
    };
}

function isTermsAcceptanceRequired(policy = {}) {
    const normalized = normalizeTermsOfServicePolicy(policy);
    return Boolean(
        normalized.revision > 0
        && normalized.require_current_revision_for_access
        && !normalized.accepted_current_revision
    );
}

function rememberTermsAcceptancePolicy(policy = {}) {
    const normalized = normalizeTermsOfServicePolicy(policy);
    if (typeof window !== 'undefined') {
        window.omlorixTermsOfServicePolicy = normalized;
        if (isTermsAcceptanceRequired(normalized)) {
            window.dispatchEvent(new CustomEvent('auth:termsAcceptanceRequired', {
                detail: {
                    policy: normalized,
                    returnUrl: resolveTermsAcceptanceReturnUrl(),
                },
            }));
        }
    }
    return normalized;
}

function redirectToTermsAcceptanceLogin(policy = {}) {
    const pathMeta = resolvePathMeta(window.location.pathname);
    const targetLogin = pathMeta.isAdminSection
        ? (typeof window !== 'undefined' && window.__OMLORIX_ADMIN_LOGIN_PATH ? window.__OMLORIX_ADMIN_LOGIN_PATH : '/login')
        : '/login';
    const current = getCurrentAppPath();
    rememberTermsAcceptancePolicy(policy);
    // Navigation assignments do not stop the current JavaScript task
    // immediately. Remember the intent so the generic unauthenticated
    // bootstrap fallback cannot overwrite the Terms-specific URL.
    termsAcceptanceRedirectPending = true;
    window.location.href = buildLoginUrl({
        redirect: current,
        termsRequired: true,
    }).replace('/login', targetLogin);
}

function authT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

/**
 * Publish the current account's language before the protected page finishes
 * booting. The language module is loaded after auth.js, so the global value is
 * needed for the early-script case; the event handles the case where i18n has
 * already registered its listener. Keeping this preference account-bound in
 * memory prevents a previous account's shared localStorage value from winning.
 *
 * @param {unknown} language Language returned by the authenticated session.
 */
function publishAuthenticatedLanguage(language) {
    if (typeof window === 'undefined') {
        return;
    }

    const normalized = typeof language === 'string' ? language.trim().toLowerCase() : '';
    window.__omlorixAuthenticatedLanguage = normalized;
    if (typeof window.dispatchEvent === 'function' && typeof window.CustomEvent === 'function') {
        window.dispatchEvent(new CustomEvent('auth:languageReady', {
            detail: { language: normalized },
        }));
    }
}

async function fetchBrowserAccounts({ silent = true } = {}) {
    try {
        const response = await fetch('/api/v1/auth/accounts', {
            method: 'GET',
            credentials: 'include',
        });
        if (!response.ok) {
            if (!silent) {
                notifyError(authT('account_load_failed', 'Failed to load accounts.'));
            }
            return null;
        }
        const payload = await response.json();
        browserAccounts = Array.isArray(payload.accounts) ? payload.accounts : [];
        activeAccountSlot = payload.active_slot ?? activeAccountSlot;
        window.dispatchEvent(new CustomEvent('auth:accountsUpdated', {
            detail: {
                accounts: browserAccounts,
                activeSlot: activeAccountSlot,
                canAddAccount: payload.can_add_account,
                maxAccounts: payload.max_accounts,
            },
        }));
        return payload;
    } catch (error) {
        if (!silent) {
            notifyError(authT('account_load_failed', 'Failed to load accounts.'));
        }
        return null;
    }
}

async function switchBrowserAccount(slot) {
    const response = await fetch('/api/v1/auth/accounts/switch', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slot }),
    });
    if (!response.ok) {
        throw new Error(authT('account_switch_failed_status', `Failed to switch account (${response.status})`).replace('{status}', response.status));
    }
    clearActiveUserLocalState();
    authSessionReady = false;
    window.location.href = resolveAccountTransitionUrl();
}

async function removeBrowserAccount(slot, { reload = true } = {}) {
    const removedActiveAccount = String(activeAccountSlot ?? '') === String(slot);
    const response = await fetch(`/api/v1/auth/accounts/${slot}`, {
        method: 'DELETE',
        credentials: 'include',
    });
    if (!response.ok) {
        throw new Error(authT('account_remove_failed_status', `Failed to remove account (${response.status})`).replace('{status}', response.status));
    }
    const payload = await response.json().catch(() => null);
    browserAccounts = Array.isArray(payload?.accounts) ? payload.accounts : browserAccounts.filter((account) => account.slot !== slot);
    activeAccountSlot = payload?.active_slot ?? activeAccountSlot;
    window.dispatchEvent(new CustomEvent('auth:accountsUpdated', {
        detail: {
            accounts: browserAccounts,
            activeSlot: activeAccountSlot,
            canAddAccount: payload?.can_add_account ?? browserAccounts.length < 5,
            maxAccounts: payload?.max_accounts ?? 5,
        },
    }));
    if (!reload || !removedActiveAccount) {
        return payload;
    }
    clearActiveUserLocalState();
    authSessionReady = false;
    const refreshed = await refreshToken();
    if (refreshed) {
        window.location.href = resolveAccountTransitionUrl();
        return;
    }
    window.location.href = '/login';
}

function startAddAccount(replaceSlot = null) {
    const returnUrl = getCurrentAppPath();
    window.location.href = buildLoginUrl({
        mode: 'add',
        returnUrl,
        replaceSlot,
    });
}


if (typeof window !== 'undefined' && typeof window.fetch === 'function') {
    const originalFetch = window.fetch.bind(window);
    let redirectingToError = false;

    window.fetch = async (...args) => {
        const response = await originalFetch(...args);
        if (response && response.status >= 500 && response.status < 600) {
            const isAlreadyOnErrorPage = window.location.pathname === '/error' || window.location.pathname.endsWith('/error');
            if (!redirectingToError && !isAlreadyOnErrorPage) {
                redirectingToError = true;
                setTimeout(() => {
                    // window.location.href = '/error';
                }, 0);
            }
        }
        return response;
    };
}

function isIndexPageShell() {
    return typeof document !== 'undefined' && document.body?.dataset?.page === 'index';
}


/**
 * Return whether the login document is completing a federated auth exchange.
 *
 * Social OAuth callbacks use a URL fragment, while enterprise SSO callbacks use
 * query parameters. Their provider-specific scripts exchange a one-time browser
 * code for the real Omlorix session as soon as the callback page loads. Starting
 * the ordinary refresh bootstrap at the same time can rotate that new refresh
 * token immediately before the exchange script navigates away. If navigation
 * aborts the refresh response, the browser never receives the rotated cookie and
 * arrives at the app with a consumed token. Keep callback ownership with the
 * exchange script and let the destination page perform the first refresh.
 */
function hasPendingFederatedAuthExchange() {
    if (typeof window === 'undefined' || !window.location) {
        return false;
    }

    // The callback markers are only meaningful on one of the login documents.
    // A regular application URL may legitimately contain either key (for
    // example, in a saved/shared link). Treating it as a callback there would
    // skip the only initial refresh and leave the protected shell unbootstrapped.
    if (!resolvePathMeta(window.location.pathname).isAnyLoginPage) {
        return false;
    }

    const searchParams = new URLSearchParams(window.location.search || '');
    const hashParams = new URLSearchParams(String(window.location.hash || '').replace(/^#/, ''));
    return searchParams.get('sso_success') === 'true'
        || hashParams.get('social_success') === 'true';
}


function shouldSuppressAuthBootstrapErrors() {
    return isIndexPageShell() && (Boolean(initialAuthBootstrapPromise) || initialAuthBootstrapFailed);
}


async function fetchAuthedBlobUrl(resourceUrl, init = {}) {
    const response = await authedFetch(resourceUrl, init);
    if (!response || !response.ok) {
        const status = response ? response.status : 'no-response';
        throw new Error(`fetchAuthedBlobUrl: failed to fetch ${resourceUrl} (status ${status})`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const revoke = () => {
        URL.revokeObjectURL(url);
    };
    return { url, revoke, blob };
}

async function runInitialAuthBootstrap() {
    authSessionReady = false;
    initialAuthBootstrapFailed = false;
    passwordChangeRedirectPending = false;
    if (!refreshPromise) {
        refreshPromise = refreshToken();
    }
    const activeRefresh = refreshPromise;
    const refreshed = await activeRefresh.finally(() => {
        if (refreshPromise === activeRefresh) {
            refreshPromise = null;
        }
    });
    const pathMeta = resolvePathMeta(window.location.pathname);
    // If user is on the public login screen and already authenticated, go to the original target (if provided)
    const urlParams = new URLSearchParams(window.location.search);
    const back = urlParams.get('redirect');
    if (pathMeta.isPublicLoginPage && refreshed && !isAddAccountMode() && !hasPasswordResetConfirmIntent()) {
        if (isTermsAcceptanceRequired(window.omlorixTermsOfServicePolicy)) {
            return true;
        }
        window.location.href = back ? decodeURIComponent(back) : '/';
        return false;
    }
    // If user is on a protected page and not authenticated, go to login
    if (!pathMeta.isPublicAuthPage && !refreshed) {
        if (termsAcceptanceRedirectPending || passwordChangeRedirectPending) {
            // ``refreshTokenWithoutLock`` already selected a prerequisite
            // route. Do not replace it with the ordinary login redirect while
            // that navigation is pending.
            initialAuthBootstrapFailed = true;
            return false;
        }
        initialAuthBootstrapFailed = true;
        redirectToLogin();
        return false;
    }
    return refreshed;
}

function ensureInitialAuthBootstrap() {
    if (!initialAuthBootstrapPromise) {
        initialAuthBootstrapPromise = runInitialAuthBootstrap().finally(() => {
            initialAuthBootstrapPromise = null;
        });
    }
    return initialAuthBootstrapPromise;
}

function waitForAuthRefreshRetry(delayMs) {
    return new Promise((resolve) => {
        window.setTimeout(resolve, Math.max(50, Math.min(Number(delayMs) || AUTH_REFRESH_RACE_DEFAULT_DELAY_MS, 1000)));
    });
}

async function fetchRefreshWithRaceRetry() {
    for (let attempt = 1; attempt <= AUTH_REFRESH_RACE_MAX_ATTEMPTS; attempt += 1) {
        const controller = new window.AbortController();
        const timeoutId = window.setTimeout(
            () => controller.abort(),
            AUTH_REFRESH_REQUEST_TIMEOUT_MS,
        );
        let response;
        try {
            response = await fetch(`/api/v1/auth/refresh`, {
                method: 'POST',
                credentials: 'include',
                signal: controller.signal,
            });
        } finally {
            window.clearTimeout(timeoutId);
        }
        if (response.status !== 409) {
            return response;
        }

        let detail = null;
        try {
            detail = (await response.clone().json())?.detail;
        } catch (error) {
            console.warn('Failed to parse concurrent auth refresh response', error);
        }
        if (detail?.type !== 'refresh_race' || attempt === AUTH_REFRESH_RACE_MAX_ATTEMPTS) {
            return response;
        }

        // The winning request updates the shared HttpOnly cookie before this
        // retry, so the next request uses the newly rotated refresh token.
        await waitForAuthRefreshRetry(detail.retry_after_ms);
    }
    throw new Error('Authentication refresh retry loop ended unexpectedly');
}

async function refreshTokenWithoutLock() {
    try {
        const response = await fetchRefreshWithRaceRetry();
        if (response.ok) {
            const tokenData = await response.json();
            // The refresh response is the earliest authenticated, account-aware
            // payload available on the index page. Publish its language before
            // the frontend's DOMContentLoaded i18n bootstrap chooses a locale.
            publishAuthenticatedLanguage(tokenData.language);
            const needsPasswordSetup = Boolean(tokenData.needs_password_setup);
            if (tokenData.has_to_change_password) {
                // The refresh payload is the trusted source for which password
                // operation the account may perform. Keep both the route and
                // the shared form state aligned with it so a stale or edited
                // ``?mode=set`` URL cannot select the password-set endpoint.
                const requiredPasswordActionMode = needsPasswordSetup ? 'set' : 'change';
                const changePasswordUrl = requiredPasswordActionMode === 'set'
                    ? '/change_password?mode=set'
                    : '/change_password';
                const currentMode = (new URLSearchParams(window.location.search).get('mode') || '').trim().toLowerCase();
                const onSetup = isChangePasswordRoute();
                const onExpectedSetup = onSetup && currentMode === (requiredPasswordActionMode === 'set' ? 'set' : '');
                if (typeof window !== 'undefined') {
                    window.requiredPasswordActionMode = requiredPasswordActionMode;
                    window.isSettingPassword = requiredPasswordActionMode === 'set';
                }
                if (!onExpectedSetup) {
                    passwordChangeRedirectPending = true;
                    window.location.href = changePasswordUrl;
                    return false;
                }
                authSessionReady = true;
                return true;
            }
            // Check if admin needs to complete server setup (first-time setup)
            if (tokenData.needs_server_setup) {
                const onServerSetup = window.location.pathname === '/server_setup' || window.location.pathname.endsWith('/server_setup');
                if (!onServerSetup) {
                    window.location.href = resolvePostAuthRedirect({ needs_server_setup: true });
                    return false;
                }
                authSessionReady = true;
                return true;
            }
            // when you are on the server_setup page, but server setup is complete, redirect to index.html
            const onServerSetupPage = window.location.pathname === '/server_setup' || window.location.pathname.endsWith('/server_setup');
            if (!tokenData.needs_server_setup && onServerSetupPage) {
                window.location.href = '/';
                return false;
            }
            // The standalone password page exists only for an enforced password
            // action. Voluntary password changes remain in User Settings.
            const onChangePasswordPage = isChangePasswordRoute();
            if (!tokenData.has_to_change_password && onChangePasswordPage) {
                window.location.href = '/';
                return false;
            }
            // when you are on the admin settings page, but not a admin, redirect to index.html
            const onAdminPage = window.location.pathname === '/admin'
                || window.location.pathname.startsWith('/admin/');
            if (!tokenData.is_admin && onAdminPage) {
                window.location.href = '/';
            }

            const termsPolicy = rememberTermsAcceptancePolicy(tokenData.terms_of_service_policy || {});
            if (isTermsAcceptanceRequired(termsPolicy)) {
                const onLoginPage = resolvePathMeta(window.location.pathname).isPublicLoginPage;
                if (!onLoginPage) {
                    redirectToTermsAcceptanceLogin(termsPolicy);
                    return false;
                }
                authSessionReady = true;
                activeAccountSlot = tokenData.active_account_slot ?? activeAccountSlot;
                return true;
            }

            authSessionReady = true;
            activeAccountSlot = tokenData.active_account_slot ?? activeAccountSlot;
            
            // Initialize theme settings if available in tokenData
            if (typeof window.initTheme === 'function' && tokenData.color_theme && tokenData.theme) {
                window.initTheme(tokenData.color_theme, tokenData.theme);
            }

            // save to local storage is_admin
            try {
                localStorage.setItem('is_admin', tokenData.is_admin);
            } catch (error) {
                console.warn('Failed to save admin status:', error);
            }

            if (typeof window !== 'undefined') {
                window.dispatchEvent(new CustomEvent('auth:isAdminUpdated', {
                    detail: { isAdmin: tokenData.is_admin }
                }));
            }

            // save application name
            setApplicationName(tokenData.application_name);

            fetchBrowserAccounts({ silent: true }).catch(() => {});
            return true;
        }

        // The login page installs a dedicated handler for same-origin failures.
        // Run it before treating the refresh as an ordinary unauthenticated
        // session so a disallowed public URL is reported as soon as the page
        // loads, without requiring the user to submit credentials first.
        if (
            typeof window.handleCrossSiteRequestBlock === 'function'
            && await window.handleCrossSiteRequestBlock(response)
        ) {
            authSessionReady = false;
            return false;
        }

        const refreshBlockDetail = await parseAccessTimeBlockedResponse(response);
        if (refreshBlockDetail) {
            rememberAccessTimeBlockedDetail(refreshBlockDetail);
        }
        authSessionReady = false;
        return false;
    } catch (e) {
        console.error('refreshToken failed', e);
        authSessionReady = false;
        return false;
    }
}

async function refreshToken() {
    const lockManager = typeof window !== 'undefined' ? window.navigator?.locks : null;
    if (lockManager && typeof lockManager.request === 'function') {
        const timeoutFactory = window.AbortSignal?.timeout;
        if (typeof timeoutFactory !== 'function') {
            return refreshTokenWithoutLock();
        }

        // Web Locks serialize refreshes across same-origin tabs. The backend's
        // 409 path remains the fallback for browsers without this API and for
        // requests that were already in flight before the lock was acquired.
        const lockSignal = timeoutFactory.call(window.AbortSignal, AUTH_REFRESH_LOCK_TIMEOUT_MS);
        try {
            return await lockManager.request(
                AUTH_REFRESH_LOCK_NAME,
                { mode: 'exclusive', signal: lockSignal },
                refreshTokenWithoutLock,
            );
        } catch (error) {
            if (lockSignal.aborted || error?.name === 'AbortError' || error?.name === 'TimeoutError') {
                return refreshTokenWithoutLock();
            }
            throw error;
        }
    }
    return refreshTokenWithoutLock();
}

async function parseAccessTimeBlockedResponse(response) {
    if (!response || response.status !== 403) {
        return null;
    }
    try {
        const contentType = response.headers?.get?.('content-type') || '';
        if (contentType && !contentType.includes('application/json')) {
            return null;
        }
        const errorData = await response.clone().json();
        const detail = errorData?.detail;
        if (detail?.type === 'access_time_blocked') {
            return normalizeAccessTimeBlockedDetail(detail);
        }
    } catch (error) {
        console.warn('Failed to parse refresh access-window response', error);
    }
    return null;
}


async function parseTermsAcceptanceRequiredResponse(response) {
    // HTTP 423 is also used by other account prerequisites, so only treat the
    // structured Terms error as a signal to enter the acceptance flow.
    if (!response || response.status !== 423) {
        return null;
    }
    try {
        const contentType = response.headers?.get?.('content-type') || '';
        if (contentType && !contentType.includes('application/json')) {
            return null;
        }
        const errorData = await response.clone().json();
        const detail = errorData?.detail;
        if (detail?.type !== 'terms_of_service_acceptance_required') {
            return null;
        }
        return {
            type: detail.type,
            revision: Math.max(1, Number(detail.revision) || 1),
        };
    } catch (error) {
        console.warn('Failed to parse Terms acceptance response', error);
        return null;
    }
}


async function refreshAuthSession() {
    if (!refreshPromise) {
        refreshPromise = refreshToken();
    }
    return refreshPromise.finally(() => { refreshPromise = null; });
}


function normalizeFederatedLogoutUrl(value) {
    if (typeof value !== 'string' || !value.trim()) {
        return null;
    }
    try {
        const url = new URL(value, window.location.origin);
        if (url.protocol !== 'http:' && url.protocol !== 'https:') {
            return null;
        }
        return url.href;
    } catch (_) {
        return null;
    }
}


async function logout() {
    const options = {
        method: 'POST',
        credentials: 'include'
    };

    try {
        const res = await fetch(`/api/v1/auth/logout`, options);
        if (res.ok || res.status === 204) {
            let logoutPayload = {};
            if (res.status !== 204) {
                try {
                    logoutPayload = await res.json();
                } catch (_) {
                    logoutPayload = {};
                }
            }
            clearActiveUserLocalState();
            authSessionReady = false;
            const federatedLogoutUrl = normalizeFederatedLogoutUrl(
                logoutPayload?.federated_logout_url
            );
            if (federatedLogoutUrl) {
                window.location.href = federatedLogoutUrl;
                return;
            }
            const refreshed = await refreshToken();
            if (refreshed) {
                window.location.href = resolveAccountTransitionUrl();
                return;
            }
            window.location.href = '/login';
            return;
        }
        if (res.status === 401) {
            redirectToLogin();
            return;
        }
        console.warn('logout: unexpected response status', res.status);
    } catch (error) {
        console.error('logout: request failed', error);
    }

    redirectToLogin();
}

async function authedFetch(input, init = {}) {
    const { adapter, ...requestInit } = init || {};
    const executor = typeof adapter === 'function' ? adapter : fetch;

    async function attempt(hasRetried) {
        if (!authSessionReady && !hasRetried) {
            let refreshed = false;
            try {
                refreshed = await refreshAuthSession();
            } catch (error) {
                console.error('authedFetch: failed to refresh auth session', error);
            }
            if (!refreshed) {
                authSessionReady = false;
            }
        }

        if (!authSessionReady) {
            if (typeof redirectToLogin === 'function') {
                redirectToLogin();
            }
            if (!shouldSuppressAuthBootstrapErrors() && typeof notifyError === 'function') {
                notifyError(typeof window.getTranslation === 'function'
                    ? window.getTranslation('auth_missing_session_error', 'Your session is unavailable. Please sign in again.')
                    : 'Your session is unavailable. Please sign in again.');
            }
            return new Response(null, {
                status: 401,
                statusText: 'Unauthorized',
            });
        }

        const mergedHeaders = new Headers();

        const applyCustomHeaders = (headerEntries) => {
            headerEntries.forEach(([key, value]) => {
                if (value === null || value === undefined || value === '') {
                    mergedHeaders.delete(key);
                } else {
                    mergedHeaders.set(key, value);
                }
            });
        };

        if (requestInit?.headers instanceof Headers) {
            applyCustomHeaders(Array.from(requestInit.headers.entries()));
        } else if (Array.isArray(requestInit?.headers)) {
            applyCustomHeaders(requestInit.headers);
        } else if (requestInit?.headers && typeof requestInit.headers === 'object') {
            applyCustomHeaders(Object.entries(requestInit.headers));
        }

        const finalInit = {
            ...requestInit,
            credentials: 'include',
            headers: mergedHeaders,
        };

        const body = finalInit.body ?? requestInit?.body;
        const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
        if (isFormData) {
            mergedHeaders.delete('Content-Type');
        } else if (!mergedHeaders.has('Content-Type')) {
            mergedHeaders.set('Content-Type', 'application/json');
        }

        const response = await executor(input, finalInit);

        // Handle 403 access_time_blocked errors
        if (response.status === 403) {
            try {
                const contentType = response.headers.get('content-type');
                if (contentType && contentType.includes('application/json')) {
                    const errorData = await response.clone().json();
                    if (errorData && errorData.detail && errorData.detail.type === 'access_time_blocked') {
                        // Call global handler if available
                        if (typeof window.handleAccessBlocked === 'function') {
                            window.handleAccessBlocked(errorData.detail);
                        }
                        return response;
                    }
                }
            } catch (e) {
                // If parsing fails, continue with normal flow
                console.warn('Failed to parse 403 response', e);
            }
        }

        const termsBlockDetail = await parseTermsAcceptanceRequiredResponse(response);
        if (termsBlockDetail) {
            // A Terms revision can change while a page is already open. Ask
            // /refresh for the authoritative policy so this uses the same
            // login redirect and modal flow as the initial page bootstrap.
            authSessionReady = false;
            if (!hasRetried) {
                try {
                    const refreshed = await refreshAuthSession();
                    if (refreshed) {
                        // Another tab may have accepted the revision before the
                        // refresh completed. In that case, retry exactly once.
                        return attempt(true);
                    }
                } catch (error) {
                    console.error('authedFetch: failed to refresh after Terms lock', error);
                }
            }

            if (!termsAcceptanceRedirectPending) {
                // If refresh itself failed, the authenticated 423 response is
                // still enough to route the user to the acceptance screen.
                redirectToTermsAcceptanceLogin({
                    revision: termsBlockDetail.revision,
                    accepted_current_revision: false,
                    require_current_revision_for_access: true,
                });
            }
            return response;
        }

        if (response.status === 401 && !hasRetried) {
            try {
                authSessionReady = false;
                const refreshed = await refreshAuthSession();
                if (refreshed) {
                    return attempt(true);
                }
            } catch (error) {
                console.error('authedFetch: failed to refresh token after 401', error);
            }

            authSessionReady = false;
            if (typeof redirectToLogin === 'function') {
                redirectToLogin();
            }
        }

        return response;
    }

    return attempt(false);
}



function normalizePathname(pathname) {
    if (typeof pathname !== 'string' || !pathname.length) {
        return '/';
    }
    const trimmed = pathname.replace(/\/+$/, '');
    return trimmed || '/';
}

function resolvePathMeta(pathname) {
    const normalized = normalizePathname(pathname);
    const isAdminLoginPage = normalized === '/admin/login';
    const isPublicLoginPage = normalized === '/login' || (normalized.endsWith('/login') && !isAdminLoginPage);
    const isPasswordResetPage = normalized === '/reset_password' || normalized.endsWith('/reset_password');
    const isAnyLoginPage = isAdminLoginPage || isPublicLoginPage;
    const isPublicAuthPage = isAnyLoginPage || isPasswordResetPage;
    const isAdminSection = normalized === '/admin' || normalized.startsWith('/admin/');
    return {
        normalized,
        isAdminLoginPage,
        isPublicLoginPage,
        isAnyLoginPage,
        isPasswordResetPage,
        isPublicAuthPage,
        isAdminSection,
    };
}

async function redirectToLogin(options = {}) {
    const pathMeta = resolvePathMeta(window.location.pathname);
    const adminLoginOverride = typeof window !== 'undefined' ? window.__OMLORIX_ADMIN_LOGIN_PATH : null;
    const isRealLoginPage = pathMeta.isPublicAuthPage && !(pathMeta.isAdminLoginPage && !adminLoginOverride);
    if (isRealLoginPage) {
        return;
    }
    const current = window.location.pathname + window.location.search + window.location.hash;
    const targetLogin = pathMeta.isAdminSection
        ? (adminLoginOverride || '/login')
        : '/login';
    const accessBlocked = normalizeAccessTimeBlockedDetail(options.accessBlocked)
        || normalizeAccessTimeBlockedDetail(lastAccessTimeBlockedDetail);
    window.location.href = buildLoginUrl({ redirect: current, accessBlocked }).replace('/login', targetLogin);
}

if (typeof window !== 'undefined') {
    capturePromptShareRedirectIntent();
    // A federated callback already owns session creation and the imminent page
    // navigation. Resolving this compatibility promise without refreshing keeps
    // other login-page initializers stable while preventing a lost Set-Cookie
    // response from invalidating the brand-new session.
    window.__omlorixInitialAuthBootstrap = hasPendingFederatedAuthExchange()
        ? Promise.resolve(false)
        : ensureInitialAuthBootstrap();
    window.logout = logout;
    window.authedFetch = authedFetch;
    window.fetchAuthedBlobUrl = fetchAuthedBlobUrl;
    window.fetchBrowserAccounts = fetchBrowserAccounts;
    window.refreshAuthSession = refreshAuthSession;
    window.switchBrowserAccount = switchBrowserAccount;
    window.removeBrowserAccount = removeBrowserAccount;
    window.startAddAccount = startAddAccount;
    window.resolvePostAuthRedirect = resolvePostAuthRedirect;
    window.getRequestedReplacementSlot = getRequestedReplacementSlot;
    window.isAddAccountMode = isAddAccountMode;
    window.getAccountReturnUrl = getAccountReturnUrl;
    window.resolveTermsAcceptanceReturnUrl = resolveTermsAcceptanceReturnUrl;
    window.isTermsAcceptanceRequired = isTermsAcceptanceRequired;
    window.clearActiveUserLocalState = clearActiveUserLocalState;
    window.buildLoginUrl = buildLoginUrl;
    window.redirectToLogin = redirectToLogin;
    window.consumeAccessTimeBlockedRefreshDetail = consumeAccessTimeBlockedRefreshDetail;
}
})();
