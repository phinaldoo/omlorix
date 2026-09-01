const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const { sanitizeLoginCallbackError, sanitizeSocialLoginError } = require('./socialLoginErrors.js');

test('returns configured messages for known error codes', () => {
    const message = sanitizeSocialLoginError('invalid-state', {
        fallback: 'safe fallback',
        knownMessages: {
            invalid_state: 'safe invalid state',
        },
    });

    assert.equal(message, 'safe invalid state');
});

test('replaces stack traces and file paths with the fallback', () => {
    const message = sanitizeSocialLoginError(
        'Error: failed\n    at /Users/admin/app/backend/app/auth/router.py:42:7',
        { fallback: 'safe fallback' },
    );

    assert.equal(message, 'safe fallback');
    assert.equal(message.includes('/Users/admin'), false);
    assert.equal(message.includes('router.py'), false);
});

test('replaces database and system details with the fallback', () => {
    const message = sanitizeSocialLoginError(
        'psycopg2.OperationalError: password authentication failed for user app at postgresql://db.internal',
        { fallback: 'safe fallback' },
    );

    assert.equal(message, 'safe fallback');
    assert.equal(message.includes('postgresql://'), false);
    assert.equal(message.includes('password'), false);
});

test('generic callback sanitizer preserves configured known SSO messages', () => {
    const message = sanitizeLoginCallbackError('invalid-state', {
        fallback: 'safe fallback',
        knownMessages: {
            invalid_state: 'localized invalid SSO state',
        },
    });

    assert.equal(message, 'localized invalid SSO state');
});

test('shared callback renderer appends only the safe support reference', () => {
    const notifications = [];
    const context = {
        document: {
            cookie: '',
            addEventListener: () => {},
            visibilityState: 'visible',
        },
    };
    context.window = {
        addEventListener: () => {},
        notifyError: (message) => notifications.push(message),
        history: { replaceState: () => {} },
        buildLoginUrl: () => '/login',
    };
    context.globalThis = context.window;
    vm.createContext(context);
    const flowPath = path.join(__dirname, 'authFlowContext.js');
    vm.runInContext(fs.readFileSync(flowPath, 'utf8'), context, { filename: flowPath });

    context.window.loginAuthFlowContext.renderLoginCallbackError({
        error: 'sso_login_failed',
        errorMessages: { sso_login_failed: 'SSO failed.' },
        reference: 'AUTH-12345',
        formatTranslate: (_key, fallback, vars) => fallback.replace('{reference}', vars.reference),
    });

    assert.deepEqual(notifications, ['SSO failed. Reference: AUTH-12345']);
});

test('shared callback renderer rejects URL-controlled reference text', () => {
    const notifications = [];
    const context = {
        document: {
            cookie: '',
            addEventListener: () => {},
            visibilityState: 'visible',
        },
    };
    context.window = {
        addEventListener: () => {},
        notifyError: (message) => notifications.push(message),
        history: { replaceState: () => {} },
        buildLoginUrl: () => '/login',
    };
    context.globalThis = context.window;
    vm.createContext(context);
    const flowPath = path.join(__dirname, 'authFlowContext.js');
    vm.runInContext(fs.readFileSync(flowPath, 'utf8'), context, { filename: flowPath });

    for (const reference of [
        'AUTH-123\nCall fake support',
        '<script>alert(1)</script>',
        'A'.repeat(65),
        'AUTH_UNEXPECTED_SEPARATOR',
    ]) {
        context.window.loginAuthFlowContext.renderLoginCallbackError({
            error: 'sso_login_failed',
            errorMessages: { sso_login_failed: 'SSO failed.' },
            reference,
        });
    }

    assert.deepEqual(notifications, [
        'SSO failed.',
        'SSO failed.',
        'SSO failed.',
        'SSO failed.',
    ]);
});

test('support reference label is translated in every login locale', () => {
    const i18nRoot = path.resolve(__dirname, '../../i18n');
    for (const locale of fs.readdirSync(i18nRoot)) {
        const loginPath = path.join(i18nRoot, locale, 'login.json');
        if (!fs.existsSync(loginPath)) continue;
        const translations = JSON.parse(fs.readFileSync(loginPath, 'utf8'));
        assert.equal(Boolean(translations.auth_error_reference?.trim()), true, `${locale}/login.json`);
    }
});

test('social callback ignores stale SSO session state for social errors', async () => {
    const notifiedMessages = [];
    let resetCalled = false;
    const stored = { sso_login_provider: 'oidc' };
    const context = {
        console,
        URLSearchParams,
        sessionStorage: {
            getItem: (key) => stored[key] || null,
            setItem: (key, value) => { stored[key] = value; },
            removeItem: (key) => { delete stored[key]; },
        },
        document: {
            addEventListener: () => {},
            getElementById: () => null,
        },
        fetch: async () => ({ ok: true, json: async () => ({ providers: {} }) }),
    };
    context.window = {
        location: { search: '?error=social_account_conflict', hash: '' },
        loginAuthFlowContext: {
            resetLoginCallbackUrl: () => { resetCalled = true; },
            notifyAuthError: (message) => notifiedMessages.push(message),
        },
        getTranslation: (_key, fallback) => fallback,
    };
    context.globalThis = context.window;
    context.window.fetch = context.fetch;
    context.window.sessionStorage = context.sessionStorage;
    vm.createContext(context);

    const helperPath = path.join(__dirname, 'socialLoginErrors.js');
    const socialPath = path.join(__dirname, 'socialLogin.js');
    vm.runInContext(fs.readFileSync(helperPath, 'utf8'), context, { filename: helperPath });
    vm.runInContext(fs.readFileSync(socialPath, 'utf8'), context, { filename: socialPath });

    const handled = await context.window.socialLogin.handleSocialCallback();

    assert.equal(handled, true);
    assert.equal(resetCalled, true);
    assert.deepEqual(notifiedMessages, ['This provider account is already connected to another Omlorix user.']);
    assert.equal(stored.sso_login_provider, undefined);
});

test('social callback leaves explicitly SSO-owned errors for enterprise handling', async () => {
    let resetCalled = false;
    const context = {
        console,
        URLSearchParams,
        sessionStorage: {
            getItem: () => null,
            setItem: () => {},
            removeItem: () => {},
        },
        document: {
            addEventListener: () => {},
            getElementById: () => null,
        },
        fetch: async () => ({ ok: true, json: async () => ({ providers: {} }) }),
    };
    context.window = {
        location: { search: '?error=sso_state_missing&auth_flow=sso', hash: '' },
        loginAuthFlowContext: {
            resetLoginCallbackUrl: () => { resetCalled = true; },
            notifyAuthError: () => { throw new Error('social handler must not render SSO errors'); },
        },
        getTranslation: (_key, fallback) => fallback,
    };
    context.globalThis = context.window;
    context.window.fetch = context.fetch;
    context.window.sessionStorage = context.sessionStorage;
    vm.createContext(context);

    const helperPath = path.join(__dirname, 'socialLoginErrors.js');
    const socialPath = path.join(__dirname, 'socialLogin.js');
    vm.runInContext(fs.readFileSync(helperPath, 'utf8'), context, { filename: helperPath });
    vm.runInContext(fs.readFileSync(socialPath, 'utf8'), context, { filename: socialPath });

    const handled = await context.window.socialLogin.handleSocialCallback();

    assert.equal(handled, false);
    assert.equal(resetCalled, false);
});

test('SSO callback unknown errors render a localized sanitized fallback', async () => {
    const notifiedMessages = [];
    let resetCalled = false;
    const context = {
        console,
        URLSearchParams,
        Number,
        setTimeout: () => null,
        requestAnimationFrame: () => null,
        sessionStorage: {
            getItem: () => null,
            setItem: () => {},
            removeItem: () => {},
        },
        document: {
            addEventListener: () => {},
            getElementById: () => null,
        },
    };
    context.window = {
        location: {
            search: '?error=sso_Trace%3A%20%3Cscript%3Ealert(1)%3C%2Fscript%3E%20at%20%2FUsers%2Fadmin%2Fapp.py',
        },
        loginAuthFlowContext: {
            notifyAuthError: (message) => notifiedMessages.push(message),
            resetLoginCallbackUrl: () => {
                resetCalled = true;
            },
        },
        getTranslation: (key, fallback) => ({
            sso_unknown_error: 'SSO localized wrapper ({error})',
            sso_unknown_error_detail: 'Localized unknown SSO error',
            sso_login_failed: 'Localized SSO failure',
            sso_state_invalid: 'Localized invalid SSO state',
        }[key] || fallback),
        formatTranslation: (key, fallback, vars = {}) => {
            const template = context.window.getTranslation(key, fallback);
            return template.replace(/\{(\w+)\}/g, (_, token) => String(vars[token] ?? ''));
        },
    };
    context.globalThis = context.window;
    vm.createContext(context);

    const helperPath = path.join(__dirname, 'socialLoginErrors.js');
    const ssoPath = path.join(__dirname, 'enterpriseSSO.js');
    vm.runInContext(fs.readFileSync(helperPath, 'utf8'), context, { filename: helperPath });
    vm.runInContext(fs.readFileSync(ssoPath, 'utf8'), context, { filename: ssoPath });

    const handled = await context.window.enterpriseSSO.handleSSOCallback();

    assert.equal(handled, true);
    assert.equal(resetCalled, true);
    assert.equal(notifiedMessages[0], 'SSO localized wrapper (Localized unknown SSO error)');
    assert.equal(notifiedMessages[0].includes('<script>'), false);
    assert.equal(notifiedMessages[0].includes('/Users/admin'), false);
});

test('social callback account_pending shows the shared pending warning', async () => {
    let resetCalled = false;
    let pendingShown = false;
    const context = {
        console,
        URLSearchParams,
        sessionStorage: {
            getItem: () => null,
            setItem: () => {},
            removeItem: () => {},
        },
        document: {
            addEventListener: () => {},
            getElementById: () => null,
        },
        fetch: async () => ({
            ok: true,
            json: async () => ({ providers: {} }),
        }),
    };
    context.window = {
        location: {
            search: '?error=account_pending',
            hash: '',
        },
        loginAuthFlowContext: {
            resetLoginCallbackUrl: () => {
                resetCalled = true;
            },
            notifyAuthError: () => {
                throw new Error('account_pending should not render a toast');
            },
        },
        showPendingNotification: () => {
            pendingShown = true;
        },
        getTranslation: (_key, fallback) => fallback,
    };
    context.globalThis = context.window;
    context.window.fetch = context.fetch;
    context.window.sessionStorage = context.sessionStorage;
    vm.createContext(context);

    const helperPath = path.join(__dirname, 'socialLoginErrors.js');
    const socialPath = path.join(__dirname, 'socialLogin.js');
    vm.runInContext(fs.readFileSync(helperPath, 'utf8'), context, { filename: helperPath });
    vm.runInContext(fs.readFileSync(socialPath, 'utf8'), context, { filename: socialPath });

    const handled = await context.window.socialLogin.handleSocialCallback();

    assert.equal(handled, true);
    assert.equal(pendingShown, true);
    assert.equal(resetCalled, true);
});

test('social account-not-linked errors name every allowlisted provider consistently', async () => {
    const providers = {
        google: 'Google',
        microsoft: 'Microsoft',
        github: 'GitHub',
        apple: 'Apple',
        slack: 'Slack',
    };

    for (const [provider, label] of Object.entries(providers)) {
        const notifiedMessages = [];
        const context = {
            console,
            URLSearchParams,
            sessionStorage: {
                getItem: () => null,
                setItem: () => {},
                removeItem: () => {},
            },
            document: {
                addEventListener: () => {},
                getElementById: () => null,
            },
            fetch: async () => ({
                ok: true,
                json: async () => ({ providers: {} }),
            }),
        };
        context.window = {
            location: {
                search: `?error=social_account_not_linked&provider=${provider}`,
                hash: '',
            },
            loginAuthFlowContext: {
                resetLoginCallbackUrl: () => {},
                notifyAuthError: (message) => notifiedMessages.push(message),
            },
            getTranslation: (_key, fallback) => fallback,
        };
        context.globalThis = context.window;
        context.window.fetch = context.fetch;
        context.window.sessionStorage = context.sessionStorage;
        vm.createContext(context);

        const helperPath = path.join(__dirname, 'socialLoginErrors.js');
        const socialPath = path.join(__dirname, 'socialLogin.js');
        vm.runInContext(fs.readFileSync(helperPath, 'utf8'), context, { filename: helperPath });
        vm.runInContext(fs.readFileSync(socialPath, 'utf8'), context, { filename: socialPath });

        const handled = await context.window.socialLogin.handleSocialCallback();

        assert.equal(handled, true);
        assert.equal(
            notifiedMessages[0],
            `Your Omlorix account is not linked to ${label}. Sign in with another method first, then link ${label} to this account.`,
        );
    }
});

test('social account-not-linked error does not display an untrusted provider value', async () => {
    const notifiedMessages = [];
    const context = {
        console,
        URLSearchParams,
        sessionStorage: {
            getItem: () => null,
            setItem: () => {},
            removeItem: () => {},
        },
        document: {
            addEventListener: () => {},
            getElementById: () => null,
        },
        fetch: async () => ({ ok: true, json: async () => ({ providers: {} }) }),
    };
    context.window = {
        location: {
            search: '?error=social_account_not_linked&provider=%3Cscript%3Ebad%3C%2Fscript%3E',
            hash: '',
        },
        loginAuthFlowContext: {
            resetLoginCallbackUrl: () => {},
            notifyAuthError: (message) => notifiedMessages.push(message),
        },
        getTranslation: (_key, fallback) => fallback,
    };
    context.globalThis = context.window;
    context.window.fetch = context.fetch;
    context.window.sessionStorage = context.sessionStorage;
    vm.createContext(context);

    const helperPath = path.join(__dirname, 'socialLoginErrors.js');
    const socialPath = path.join(__dirname, 'socialLogin.js');
    vm.runInContext(fs.readFileSync(helperPath, 'utf8'), context, { filename: helperPath });
    vm.runInContext(fs.readFileSync(socialPath, 'utf8'), context, { filename: socialPath });

    await context.window.socialLogin.handleSocialCallback();

    assert.equal(notifiedMessages[0], 'Social login failed. Please try again.');
    assert.equal(notifiedMessages[0].includes('<script>'), false);
});

test('SSO callback account_locked shows the shared account lock warning', async () => {
    let resetCalled = false;
    let lockWarningResult = null;
    const context = {
        console,
        URLSearchParams,
        Number,
        setTimeout: () => null,
        requestAnimationFrame: () => null,
        sessionStorage: {
            getItem: () => null,
            setItem: () => {},
            removeItem: () => {},
        },
        document: {
            addEventListener: () => {},
            getElementById: () => null,
        },
    };
    context.window = {
        location: {
            search: '?error=account_locked',
        },
        loginAuthFlowContext: {
            resetLoginCallbackUrl: () => {
                resetCalled = true;
            },
            notifyAuthError: () => {
                throw new Error('account_locked should not render a toast');
            },
        },
        showLoginAccountLockWarning: (result) => {
            lockWarningResult = result;
        },
        getTranslation: (_key, fallback) => fallback,
        formatTranslation: (_key, fallback, vars = {}) => fallback.replace(/\{(\w+)\}/g, (_, token) => String(vars[token] ?? '')),
    };
    context.globalThis = context.window;
    vm.createContext(context);

    const helperPath = path.join(__dirname, 'socialLoginErrors.js');
    const ssoPath = path.join(__dirname, 'enterpriseSSO.js');
    vm.runInContext(fs.readFileSync(helperPath, 'utf8'), context, { filename: helperPath });
    vm.runInContext(fs.readFileSync(ssoPath, 'utf8'), context, { filename: ssoPath });

    const handled = await context.window.enterpriseSSO.handleSSOCallback();

    assert.equal(handled, true);
    assert.equal(lockWarningResult && Object.keys(lockWarningResult).length, 0);
    assert.equal(resetCalled, true);
});
