const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function createButton(id, label = '') {
    const listeners = {};
    const labelNode = { textContent: label };
    return {
        id,
        disabled: false,
        dataset: {},
        style: { display: 'none' },
        tabIndex: -1,
        attributes: {},
        classList: {
            add: () => {},
            remove: () => {},
        },
        addEventListener(eventName, handler) {
            listeners[eventName] = handler;
        },
        dispatch(eventName) {
            return listeners[eventName]?.({ currentTarget: this });
        },
        setAttribute(name, value) {
            this.attributes[name] = String(value);
        },
        getAttribute(name) {
            return Object.prototype.hasOwnProperty.call(this.attributes, name)
                ? this.attributes[name]
                : null;
        },
        removeAttribute(name) {
            delete this.attributes[name];
        },
        querySelector(selector) {
            if (selector === '[data-auth-label]' || selector === 'span:not(.last-used-badge)' || selector === 'span') {
                return labelNode;
            }
            return null;
        },
        focusCalls: [],
        focus(options) {
            this.focusCalls.push(options || null);
        },
        get labelNode() {
            return labelNode;
        },
    };
}

function createBasicContext() {
    const notifiedMessages = [];
    const sessionStorageState = {};
    const documentListeners = {};
    const windowListeners = {};
    const context = {
        console,
        URL,
        URLSearchParams,
        sessionStorage: {
            getItem: (key) => (Object.prototype.hasOwnProperty.call(sessionStorageState, key) ? sessionStorageState[key] : null),
            setItem: (key, value) => {
                sessionStorageState[key] = value;
            },
            removeItem: (key) => {
                delete sessionStorageState[key];
            },
        },
        document: {
            activeElement: null,
            visibilityState: 'visible',
            addEventListener: (eventName, handler) => {
                documentListeners[eventName] = handler;
            },
            getElementById: () => null,
        },
        window: {
            location: { href: '/login', search: '' },
            history: { replaceState: () => {} },
            addEventListener: (eventName, handler) => {
                windowListeners[eventName] = handler;
            },
            notifyError: (message) => notifiedMessages.push(message),
            getTranslation: (key, fallback) => (key === 'login_redirect_pending' ? 'Connecting now' : fallback),
        },
    };
    context.globalThis = context.window;
    context.window.document = context.document;
    context.window.sessionStorage = context.sessionStorage;
    context.window.console = console;
    context.window.URL = URL;
    context.window.URLSearchParams = URLSearchParams;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.notifiedMessages = notifiedMessages;
    context.sessionStorageState = sessionStorageState;
    context.dispatchDocumentEvent = (eventName) => documentListeners[eventName]?.();
    context.dispatchWindowEvent = (eventName) => windowListeners[eventName]?.();
    return context;
}

test('native callback validation accepts only bounded HTTPS auth callbacks', () => {
    const context = createBasicContext();
    vm.createContext(context);
    const authFlowPath = path.join(__dirname, 'authFlowContext.js');
    vm.runInContext(fs.readFileSync(authFlowPath, 'utf8'), context, { filename: authFlowPath });

    const state = 'native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE';
    const validate = context.window.loginAuthFlowContext.isTrustedNativeCallbackUrl;
    assert.equal(validate(`https://native.example/auth/federated#state=${state}&status=social`), true);
    assert.equal(validate(`https://native.example/auth/link#state=${state}&status=pending`), true);
    assert.equal(validate(`http://native.example/auth/federated#state=${state}`), false);
    assert.equal(validate(`https://native.example/other#state=${state}`), false);
    assert.equal(validate('https://native.example/auth/federated#state=short'), false);
    assert.equal(validate(`https://native.example/auth/federated?next=elsewhere#state=${state}`), false);
    assert.equal(validate(`https://native.example/auth/federated#state=${state}&next=elsewhere`), false);
});

test('initiateAuthRedirect restores the login button state after init failure', async () => {
    const context = createBasicContext();
    const button = createButton('googleLoginBtn', 'Continue with Google');
    context.document.activeElement = button;
    context.fetch = async () => ({
        ok: false,
        json: async () => ({ detail: 'Provider unavailable' }),
    });
    context.window.fetch = context.fetch;

    vm.createContext(context);
    const authFlowPath = path.join(__dirname, 'authFlowContext.js');
    vm.runInContext(fs.readFileSync(authFlowPath, 'utf8'), context, { filename: authFlowPath });

    const result = await context.window.loginAuthFlowContext.initiateAuthRedirect({
        endpoint: '/api/v1/auth/social/google/init',
        pendingButton: button,
        pendingLabel: 'Connecting now',
        initFailureMessage: 'Init failed',
    });

    assert.equal(result, null);
    assert.equal(button.disabled, false);
    assert.equal(button.getAttribute('aria-busy'), null);
    assert.equal(button.labelNode.textContent, 'Continue with Google');
    assert.equal(button.focusCalls.length, 1);
    assert.equal(button.focusCalls[0].preventScroll, true);
    assert.equal(context.notifiedMessages[0], 'Provider unavailable');
});

test('initiateAuthRedirect renders cross-site block responses without a toast', async () => {
    const context = createBasicContext();
    const button = createButton('googleLoginBtn', 'Continue with Google');
    let renderedDetail = '';
    context.window.handleCrossSiteRequestBlock = async (response) => {
        const payload = await response.clone().json();
        renderedDetail = payload.detail;
        return true;
    };
    context.fetch = async () => ({
        ok: false,
        status: 403,
        clone: () => ({
            json: async () => ({ detail: 'Cross-site request blocked' }),
        }),
        json: async () => ({ detail: 'Cross-site request blocked' }),
    });
    context.window.fetch = context.fetch;

    vm.createContext(context);
    const authFlowPath = path.join(__dirname, 'authFlowContext.js');
    vm.runInContext(fs.readFileSync(authFlowPath, 'utf8'), context, { filename: authFlowPath });

    const result = await context.window.loginAuthFlowContext.initiateAuthRedirect({
        endpoint: '/api/v1/auth/social/google/init',
        pendingButton: button,
        pendingLabel: 'Connecting now',
        initFailureMessage: 'Init failed',
    });

    assert.equal(result, null);
    assert.equal(button.disabled, false);
    assert.equal(renderedDetail, 'Cross-site request blocked');
    assert.deepEqual(context.notifiedMessages, []);
});

test('initiateAuthRedirect keeps the login button pending while redirecting', async () => {
    const context = createBasicContext();
    const button = createButton('googleLoginBtn', 'Continue with Google');
    context.fetch = async () => ({
        ok: true,
        json: async () => ({ state: 'oauth-state', authorization_url: '/oauth/provider' }),
    });
    context.window.fetch = context.fetch;

    vm.createContext(context);
    const authFlowPath = path.join(__dirname, 'authFlowContext.js');
    vm.runInContext(fs.readFileSync(authFlowPath, 'utf8'), context, { filename: authFlowPath });

    const result = await context.window.loginAuthFlowContext.initiateAuthRedirect({
        endpoint: '/api/v1/auth/social/google/init',
        stateStorageKey: 'social_oauth_state',
        pendingButton: button,
        pendingLabel: 'Connecting now',
        initFailureMessage: 'Init failed',
    });

    assert.deepEqual(result, { state: 'oauth-state', authorization_url: '/oauth/provider' });
    assert.equal(button.disabled, true);
    assert.equal(button.getAttribute('aria-busy'), 'true');
    assert.equal(button.labelNode.textContent, 'Connecting now');
    assert.equal(context.window.location.href, '/oauth/provider');
    assert.equal(context.sessionStorageState.social_oauth_state, 'oauth-state');
});

test('provider cancellation resume signals restore handed-off social login buttons', async () => {
    const resumeSignals = [
        { target: 'window', eventName: 'pageshow' },
        { target: 'window', eventName: 'focus' },
        { target: 'document', eventName: 'visibilitychange' },
    ];

    for (const { target, eventName } of resumeSignals) {
        const context = createBasicContext();
        const button = createButton('appleLoginBtn', 'Continue with Apple');
        context.document.activeElement = button;
        context.fetch = async () => ({
            ok: true,
            json: async () => ({ state: 'oauth-state', authorization_url: '/oauth/apple' }),
        });
        context.window.fetch = context.fetch;

        vm.createContext(context);
        const authFlowPath = path.join(__dirname, 'authFlowContext.js');
        vm.runInContext(fs.readFileSync(authFlowPath, 'utf8'), context, { filename: authFlowPath });

        await context.window.loginAuthFlowContext.initiateAuthRedirect({
            endpoint: '/api/v1/auth/social/apple/init',
            pendingButton: button,
            pendingLabel: 'Connecting now',
            initFailureMessage: 'Init failed',
        });

        assert.equal(button.disabled, true, `${eventName} starts with a pending button`);
        assert.equal(button.getAttribute('aria-busy'), 'true');

        if (target === 'window') {
            context.dispatchWindowEvent(eventName);
        } else {
            context.dispatchDocumentEvent(eventName);
        }

        assert.equal(button.disabled, false, `${eventName} restores the button`);
        assert.equal(button.getAttribute('aria-busy'), null);
        assert.equal(button.labelNode.textContent, 'Continue with Apple');
        assert.equal(button.focusCalls.length, 1);
    }
});

test('social and SSO login handlers pass the clicked button into the redirect helper', async () => {
    const socialElements = {
        socialLoginDivider: createButton('socialLoginDivider'),
        socialLoginButtons: createButton('socialLoginButtons'),
        googleLoginBtn: createButton('googleLoginBtn', 'Continue with Google'),
        googleBtnText: createButton('googleBtnText', 'Continue with Google'),
        microsoftLoginBtn: createButton('microsoftLoginBtn', 'Continue with Microsoft'),
        microsoftBtnText: createButton('microsoftBtnText', 'Continue with Microsoft'),
        appleLoginBtn: createButton('appleLoginBtn', 'Continue with Apple'),
        appleBtnText: createButton('appleBtnText', 'Continue with Apple'),
        githubLoginBtn: createButton('githubLoginBtn', 'Continue with GitHub'),
        githubBtnText: createButton('githubBtnText', 'Continue with GitHub'),
        slackLoginBtn: createButton('slackLoginBtn', 'Sign in with Slack'),
        slackBtnText: createButton('slackBtnText', 'Sign in with Slack'),
    };

    let socialDomReady = null;
    const socialInitCalls = [];
    const socialContext = {
        console,
        URLSearchParams,
        sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
        document: {
            addEventListener: (eventName, handler) => {
                if (eventName === 'DOMContentLoaded') {
                    socialDomReady = handler;
                }
            },
            getElementById: (id) => socialElements[id] || null,
        },
        fetch: async (url) => ({
            ok: true,
            json: async () => (url === '/api/v1/auth/social/providers' ? {
                providers: {
                    google: {},
                    microsoft: {},
                    apple: {},
                    github: {},
                    slack: {},
                },
            } : {}),
        }),
    };
    socialContext.window = {
        location: { search: '', hash: '', href: '/login' },
        loginAuthFlowContext: {
            initiateAuthRedirect: async (options) => {
                socialInitCalls.push(options);
                return null;
            },
        },
        getTranslation: (key, fallback) => (key === 'login_redirect_pending' ? 'Connecting now' : fallback),
        setTimeout,
    };
    socialContext.globalThis = socialContext.window;
    socialContext.window.fetch = socialContext.fetch;
    socialContext.window.document = socialContext.document;
    socialContext.window.sessionStorage = socialContext.sessionStorage;
    socialContext.window.window = socialContext.window;
    socialContext.window.globalThis = socialContext.window;
    vm.createContext(socialContext);

    const socialPath = path.join(__dirname, 'socialLogin.js');
    vm.runInContext(fs.readFileSync(socialPath, 'utf8'), socialContext, { filename: socialPath });
    socialDomReady();
    await new Promise((resolve) => setImmediate(resolve));
    for (const provider of ['google', 'microsoft', 'apple', 'github', 'slack']) {
        await socialElements[`${provider}LoginBtn`].dispatch('click');
    }

    assert.equal(socialInitCalls.length, 5);
    socialInitCalls.forEach((call, index) => {
        const provider = ['google', 'microsoft', 'apple', 'github', 'slack'][index];
        assert.equal(call.endpoint, `/api/v1/auth/social/${provider}/init`);
        assert.equal(call.pendingButton, socialElements[`${provider}LoginBtn`]);
        assert.equal(call.pendingLabel, 'Connecting now');
    });

    const ssoElements = Object.fromEntries([
        'ssoLoginDivider',
        'samlLoginBtn',
        'samlBtnText',
        'oidcLoginBtn',
        'oidcBtnText',
        'signinEmail',
    ].map((id) => [id, createButton(id, id)]));

    let ssoDomReady = null;
    let ssoInitCall = null;
    const ssoContext = {
        console,
        URLSearchParams,
        Number,
        setTimeout: (handler) => {
            handler();
            return 1;
        },
        clearTimeout: () => {},
        requestAnimationFrame: () => null,
        AbortController,
        sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
        document: {
            addEventListener: (eventName, handler) => {
                if (eventName === 'DOMContentLoaded') {
                    ssoDomReady = handler;
                }
            },
            getElementById: (id) => ssoElements[id] || null,
        },
        fetch: async (url) => ({
            ok: true,
            json: async () => ({ providers: { saml: { button_text: 'Sign in with SAML' } } }),
        }),
    };
    ssoContext.window = {
        location: { search: '', href: '/login' },
        loginAuthFlowContext: {
            initiateAuthRedirect: async (options) => {
                ssoInitCall = options;
                return null;
            },
        },
        getTranslation: (key, fallback) => (key === 'login_redirect_pending' ? 'Connecting now' : fallback),
        setTimeout: ssoContext.setTimeout,
        __omlorixI18nReady: true,
    };
    ssoContext.globalThis = ssoContext.window;
    ssoContext.window.fetch = ssoContext.fetch;
    ssoContext.window.document = ssoContext.document;
    ssoContext.window.sessionStorage = ssoContext.sessionStorage;
    ssoContext.window.window = ssoContext.window;
    ssoContext.window.globalThis = ssoContext.window;
    vm.createContext(ssoContext);

    const ssoPath = path.join(__dirname, 'enterpriseSSO.js');
    vm.runInContext(fs.readFileSync(ssoPath, 'utf8'), ssoContext, { filename: ssoPath });
    ssoDomReady();
    await new Promise((resolve) => setImmediate(resolve));
    ssoElements.samlLoginBtn.dispatch('click');
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(ssoInitCall.endpoint, '/api/v1/auth/sso/saml/init');
    assert.equal(ssoInitCall.payload.provider_type, 'saml');
    assert.equal(Object.hasOwn(ssoInitCall.payload, 'config_id'), false);
    assert.equal(ssoInitCall.pendingButton, ssoElements.samlLoginBtn);
    assert.equal(ssoInitCall.pendingLabel, 'Connecting now');
});
