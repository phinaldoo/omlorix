const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function createElement(id) {
    const classes = new Set();
    return {
        id,
        textContent: '',
        href: '',
        hidden: false,
        inert: false,
        tabIndex: 0,
        style: {},
        dataset: {},
        attributes: {},
        focusCalls: 0,
        listeners: {},
        classList: {
            add: (className) => classes.add(className),
            remove: (className) => classes.delete(className),
            toggle: (className, force) => {
                if (force) {
                    classes.add(className);
                } else {
                    classes.delete(className);
                }
            },
            contains: (className) => classes.has(className),
        },
        setAttribute(name, value) {
            this.attributes[name] = String(value);
        },
        getAttribute(name) {
            return Object.prototype.hasOwnProperty.call(this.attributes, name)
                ? this.attributes[name]
                : null;
        },
        addEventListener(eventName, handler) {
            this.listeners[eventName] = handler;
        },
        focus() {
            this.focusCalls += 1;
        },
    };
}

test('federated callback pages defer refresh until their one-time exchange navigates', async () => {
    const authPath = path.join(__dirname, 'auth.js');
    const callbackLocations = [
        {
            label: 'social OAuth fragment callback',
            search: '?return=%2F',
            hash: '#social_success=true',
        },
        {
            label: 'enterprise SSO query callback',
            search: '?sso_success=true&return=%2F',
            hash: '',
        },
    ];

    for (const callbackLocation of callbackLocations) {
        let fetchCalls = 0;
        const context = {
            console,
            URL,
            URLSearchParams,
            CustomEvent: function CustomEvent(type, init) {
                this.type = type;
                this.detail = init?.detail;
            },
            localStorage: { removeItem: () => {}, setItem: () => {} },
            sessionStorage: { removeItem: () => {} },
            document: {
                title: 'Login',
                documentElement: { setAttribute: () => {} },
                addEventListener: () => {},
                body: { dataset: { page: 'login' } },
            },
            fetch: async () => {
                fetchCalls += 1;
                throw new Error('Federated callback bootstrap must not start a refresh request');
            },
        };
        context.window = {
            location: {
                origin: 'https://chat.example',
                pathname: '/login',
                search: callbackLocation.search,
                hash: callbackLocation.hash,
                href: `https://chat.example/login${callbackLocation.search}${callbackLocation.hash}`,
            },
            localStorage: context.localStorage,
            sessionStorage: context.sessionStorage,
            fetch: context.fetch,
            dispatchEvent: () => {},
            setTimeout,
            clearTimeout,
        };
        context.globalThis = context.window;
        context.window.window = context.window;
        context.window.globalThis = context.window;
        context.window.document = context.document;
        vm.createContext(context);

        vm.runInContext(fs.readFileSync(authPath, 'utf8'), context, { filename: authPath });
        const refreshed = await context.window.__omlorixInitialAuthBootstrap;

        assert.equal(refreshed, false, callbackLocation.label);
        assert.equal(fetchCalls, 0, callbackLocation.label);
        assert.equal(context.window.location.href, `https://chat.example/login${callbackLocation.search}${callbackLocation.hash}`);
    }
});

test('logout follows the OIDC RP-initiated logout URL without refreshing locally', async () => {
    const authPath = path.join(__dirname, 'auth.js');
    const removedKeys = [];
    let fetchCalls = 0;
    const context = {
        console,
        URL,
        URLSearchParams,
        CustomEvent: function CustomEvent(type, init) {
            this.type = type;
            this.detail = init?.detail;
        },
        localStorage: {
            removeItem: (key) => removedKeys.push(key),
            setItem: () => {},
        },
        sessionStorage: { removeItem: () => {}, setItem: () => {} },
        document: {
            title: 'Login',
            documentElement: { setAttribute: () => {} },
            addEventListener: () => {},
            body: { dataset: { page: 'login' } },
        },
        fetch: async (url) => {
            fetchCalls += 1;
            assert.equal(url, '/api/v1/auth/logout');
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    status: 'success',
                    federated_logout_url: 'https://idp.example/application/o/omlorix/end-session/',
                }),
            };
        },
    };
    context.window = {
        location: {
            origin: 'https://chat.example',
            pathname: '/login',
            search: '?sso_success=true',
            hash: '',
            href: 'https://chat.example/login?sso_success=true',
        },
        localStorage: context.localStorage,
        sessionStorage: context.sessionStorage,
        fetch: context.fetch,
        dispatchEvent: () => {},
        setTimeout,
        clearTimeout,
    };
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.window.document = context.document;
    vm.createContext(context);

    vm.runInContext(fs.readFileSync(authPath, 'utf8'), context, { filename: authPath });
    await context.window.logout();

    assert.equal(fetchCalls, 1);
    assert.equal(
        context.window.location.href,
        'https://idp.example/application/o/omlorix/end-session/',
    );
    assert.deepEqual(removedKeys, ['firstName', 'lastName', 'email', 'is_admin']);
});

test('callback-looking parameters on a protected page do not suppress its initial refresh', async () => {
    const authPath = path.join(__dirname, 'auth.js');
    let refreshCalls = 0;
    const context = {
        console,
        URL,
        URLSearchParams,
        CustomEvent: function CustomEvent(type, init) {
            this.type = type;
            this.detail = init?.detail;
        },
        localStorage: { removeItem: () => {}, setItem: () => {} },
        sessionStorage: { removeItem: () => {} },
        document: {
            title: 'Omlorix',
            documentElement: { setAttribute: () => {} },
            addEventListener: () => {},
            body: { dataset: { page: 'index' } },
        },
        fetch: async (url) => {
            assert.equal(url, '/api/v1/auth/refresh');
            refreshCalls += 1;
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    session_authenticated: true,
                    has_to_change_password: false,
                    needs_password_setup: false,
                    needs_server_setup: false,
                    is_admin: false,
                    application_name: 'Omlorix',
                    active_account_slot: 1,
                    terms_of_service_policy: {
                        revision: 1,
                        accepted_current_revision: true,
                        require_current_revision_for_access: false,
                    },
                }),
            };
        },
    };
    context.window = {
        location: {
            origin: 'https://chat.example',
            pathname: '/',
            search: '?sso_success=true',
            hash: '#social_success=true',
            href: 'https://chat.example/?sso_success=true#social_success=true',
        },
        localStorage: context.localStorage,
        sessionStorage: context.sessionStorage,
        fetch: context.fetch,
        dispatchEvent: () => {},
        matchMedia: () => ({ matches: false }),
        AbortController,
        setTimeout,
        clearTimeout,
    };
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.window.document = context.document;
    vm.createContext(context);

    vm.runInContext(fs.readFileSync(authPath, 'utf8'), context, { filename: authPath });

    assert.equal(await context.window.__omlorixInitialAuthBootstrap, true);
    assert.equal(refreshCalls, 1);
});

test('forced password changes reject a stale set-password mode', async () => {
    const authPath = path.join(__dirname, 'auth.js');
    const context = {
        console,
        URL,
        URLSearchParams,
        CustomEvent: function CustomEvent(type, init) {
            this.type = type;
            this.detail = init?.detail;
        },
        localStorage: { removeItem: () => {}, setItem: () => {} },
        sessionStorage: { removeItem: () => {} },
        document: {
            title: 'Change Password',
            documentElement: { setAttribute: () => {} },
            addEventListener: () => {},
            body: { dataset: { page: 'login' } },
        },
        fetch: async () => ({
            ok: true,
            status: 200,
            json: async () => ({
                session_authenticated: true,
                has_to_change_password: true,
                needs_password_setup: false,
                needs_server_setup: false,
                is_admin: false,
                application_name: 'Omlorix',
                active_account_slot: 1,
                terms_of_service_policy: {
                    revision: 1,
                    accepted_current_revision: true,
                    require_current_revision_for_access: false,
                },
            }),
        }),
    };
    context.window = {
        location: {
            origin: 'https://chat.example',
            pathname: '/change_password',
            search: '?mode=set',
            hash: '',
            href: 'https://chat.example/change_password?mode=set',
        },
        localStorage: context.localStorage,
        sessionStorage: context.sessionStorage,
        fetch: context.fetch,
        dispatchEvent: () => {},
        matchMedia: () => ({ matches: false }),
        AbortController,
        setTimeout,
        clearTimeout,
    };
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.window.document = context.document;
    vm.createContext(context);

    vm.runInContext(fs.readFileSync(authPath, 'utf8'), context, { filename: authPath });
    const refreshed = await context.window.__omlorixInitialAuthBootstrap;

    assert.equal(refreshed, false);
    assert.equal(context.window.requiredPasswordActionMode, 'change');
    assert.equal(context.window.isSettingPassword, false);
    assert.equal(context.window.location.href, '/change_password');
});

test('refresh access-window denial redirects to login with modal payload', async () => {
    const authPath = path.join(__dirname, 'auth.js');
    const refreshDetail = {
        type: 'access_time_blocked',
        reason: 'outside_allowed_window',
        next_allowed_at: '2026-06-14T08:30:00+00:00',
        blocked_message: 'Come back during business hours',
    };
    const context = {
        console,
        URL,
        URLSearchParams,
        CustomEvent: function CustomEvent(type, init) {
            this.type = type;
            this.detail = init?.detail;
        },
        localStorage: { removeItem: () => {}, setItem: () => {} },
        sessionStorage: { removeItem: () => {} },
        document: {
            title: 'Omlorix',
            documentElement: { setAttribute: () => {} },
            addEventListener: () => {},
            body: { dataset: { page: 'index' } },
        },
        fetch: async () => ({
            ok: false,
            status: 403,
            headers: { get: () => 'application/json' },
            clone() {
                return this;
            },
            json: async () => ({ detail: refreshDetail }),
        }),
    };
    context.window = {
        location: {
            origin: 'https://chat.example',
            pathname: '/',
            search: '',
            hash: '',
            href: 'https://chat.example/',
        },
        localStorage: context.localStorage,
        sessionStorage: context.sessionStorage,
        fetch: context.fetch,
        dispatchEvent: () => {},
        AbortController,
        setTimeout,
        clearTimeout,
    };
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.window.document = context.document;
    vm.createContext(context);

    vm.runInContext(fs.readFileSync(authPath, 'utf8'), context, { filename: authPath });
    await context.window.__omlorixInitialAuthBootstrap;

    const redirected = new URL(context.window.location.href, 'https://chat.example');
    assert.equal(redirected.pathname, '/login');
    assert.equal(redirected.searchParams.get('redirect'), '/');
    assert.equal(redirected.searchParams.get('access_blocked'), 'access_time_blocked');
    assert.equal(redirected.searchParams.get('reason'), refreshDetail.reason);
    assert.equal(redirected.searchParams.get('next_allowed_at'), refreshDetail.next_allowed_at);
    assert.equal(redirected.searchParams.get('blocked_message'), refreshDetail.blocked_message);
    assert.equal(redirected.searchParams.has('error'), false);
});

test('refresh required Terms policy redirects to login without losing the acceptance intent', async () => {
    const authPath = path.join(__dirname, 'auth.js');
    const requiredPolicy = {
        revision: 7,
        accepted_current_revision: false,
        require_current_revision_for_access: true,
    };
    const context = {
        console,
        URL,
        URLSearchParams,
        CustomEvent: function CustomEvent(type, init) {
            this.type = type;
            this.detail = init?.detail;
        },
        localStorage: { removeItem: () => {}, setItem: () => {} },
        sessionStorage: { removeItem: () => {} },
        document: {
            title: 'Omlorix',
            documentElement: { setAttribute: () => {} },
            addEventListener: () => {},
            body: { dataset: { page: 'index' } },
        },
        fetch: async () => ({
            ok: true,
            status: 200,
            json: async () => ({
                session_authenticated: true,
                has_to_change_password: false,
                needs_password_setup: false,
                needs_server_setup: false,
                is_admin: false,
                application_name: 'Omlorix',
                active_account_slot: 1,
                terms_of_service_policy: requiredPolicy,
            }),
        }),
    };
    context.window = {
        location: {
            origin: 'https://chat.example',
            pathname: '/chats',
            search: '?view=all',
            hash: '#latest',
            href: 'https://chat.example/chats?view=all#latest',
        },
        localStorage: context.localStorage,
        sessionStorage: context.sessionStorage,
        fetch: context.fetch,
        dispatchEvent: () => {},
        matchMedia: () => ({ matches: false }),
        AbortController,
        setTimeout,
        clearTimeout,
    };
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.window.document = context.document;
    vm.createContext(context);

    vm.runInContext(fs.readFileSync(authPath, 'utf8'), context, { filename: authPath });
    const refreshed = await context.window.__omlorixInitialAuthBootstrap;

    const redirected = new URL(context.window.location.href, 'https://chat.example');
    assert.equal(refreshed, false);
    assert.equal(redirected.pathname, '/login');
    assert.equal(redirected.searchParams.get('redirect'), '/chats?view=all#latest');
    assert.equal(redirected.searchParams.get('terms_required'), 'true');
    assert.equal(context.window.omlorixTermsOfServicePolicy.revision, 7);
});

test('required Terms policy without a Terms redirect uses ordinary login navigation', async () => {
    const authPath = path.join(__dirname, 'auth.js');
    const context = {
        console,
        URL,
        URLSearchParams,
        CustomEvent: function CustomEvent(type, init) {
            this.type = type;
            this.detail = init?.detail;
        },
        localStorage: { removeItem: () => {}, setItem: () => {} },
        sessionStorage: { removeItem: () => {} },
        document: {
            title: 'Omlorix',
            documentElement: { setAttribute: () => {} },
            addEventListener: () => {},
            body: { dataset: { page: 'index' } },
        },
        fetch: async () => ({
            ok: false,
            status: 401,
            headers: { get: () => 'application/json' },
            clone() {
                return this;
            },
            json: async () => ({}),
        }),
    };
    context.window = {
        location: {
            origin: 'https://chat.example',
            pathname: '/chats',
            search: '',
            hash: '',
            href: 'https://chat.example/chats',
        },
        omlorixTermsOfServicePolicy: {
            revision: 7,
            accepted_current_revision: false,
            require_current_revision_for_access: true,
        },
        localStorage: context.localStorage,
        sessionStorage: context.sessionStorage,
        fetch: context.fetch,
        dispatchEvent: () => {},
        matchMedia: () => ({ matches: false }),
        AbortController,
        setTimeout,
        clearTimeout,
    };
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.window.document = context.document;
    vm.createContext(context);

    vm.runInContext(fs.readFileSync(authPath, 'utf8'), context, { filename: authPath });
    const refreshed = await context.window.__omlorixInitialAuthBootstrap;
    const redirected = new URL(context.window.location.href, 'https://chat.example');

    assert.equal(refreshed, false);
    assert.equal(redirected.pathname, '/login');
    assert.equal(redirected.searchParams.get('redirect'), '/chats');
    assert.equal(redirected.searchParams.has('terms_required'), false);
});

test('authenticated 423 refreshes policy and enters the same Terms login flow', async () => {
    const authPath = path.join(__dirname, 'auth.js');
    let refreshCalls = 0;
    const acceptedPolicy = {
        revision: 6,
        accepted_current_revision: true,
        require_current_revision_for_access: true,
    };
    const requiredPolicy = {
        revision: 7,
        accepted_current_revision: false,
        require_current_revision_for_access: true,
    };
    const fetch = async (url) => {
        if (String(url).includes('/api/v1/auth/refresh')) {
            refreshCalls += 1;
            const policy = refreshCalls === 1 ? acceptedPolicy : requiredPolicy;
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    session_authenticated: true,
                    has_to_change_password: false,
                    needs_password_setup: false,
                    needs_server_setup: false,
                    is_admin: false,
                    application_name: 'Omlorix',
                    active_account_slot: 1,
                    terms_of_service_policy: policy,
                }),
            };
        }
        return {
            ok: true,
            status: 200,
            json: async () => ({ accounts: [], active_slot: 1 }),
        };
    };
    const lockedResponse = {
        ok: false,
        status: 423,
        headers: { get: () => 'application/json' },
        clone() {
            return this;
        },
        json: async () => ({
            detail: {
                type: 'terms_of_service_acceptance_required',
                revision: 7,
            },
        }),
    };
    const context = {
        console,
        Headers,
        URL,
        URLSearchParams,
        CustomEvent: function CustomEvent(type, init) {
            this.type = type;
            this.detail = init?.detail;
        },
        localStorage: { removeItem: () => {}, setItem: () => {} },
        sessionStorage: { removeItem: () => {} },
        document: {
            title: 'Omlorix',
            documentElement: { setAttribute: () => {} },
            addEventListener: () => {},
            body: { dataset: { page: 'index' } },
        },
        fetch,
    };
    context.window = {
        location: {
            origin: 'https://chat.example',
            pathname: '/chats',
            search: '',
            hash: '',
            href: 'https://chat.example/chats',
        },
        localStorage: context.localStorage,
        sessionStorage: context.sessionStorage,
        fetch,
        dispatchEvent: () => {},
        matchMedia: () => ({ matches: false }),
        AbortController,
        setTimeout,
        clearTimeout,
    };
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.window.document = context.document;
    vm.createContext(context);

    vm.runInContext(fs.readFileSync(authPath, 'utf8'), context, { filename: authPath });
    assert.equal(await context.window.__omlorixInitialAuthBootstrap, true);

    const response = await context.window.authedFetch('/api/v1/chats', {
        adapter: async () => lockedResponse,
    });
    const redirected = new URL(context.window.location.href, 'https://chat.example');

    assert.equal(response, lockedResponse);
    assert.equal(refreshCalls, 2);
    assert.equal(redirected.pathname, '/login');
    assert.equal(redirected.searchParams.get('redirect'), '/chats');
    assert.equal(redirected.searchParams.get('terms_required'), 'true');
    assert.equal(context.window.omlorixTermsOfServicePolicy.revision, 7);
});

test('login bootstrap shows the cross-site warning when refresh is origin-blocked', async () => {
    const authPath = path.join(__dirname, 'auth.js');
    const blockedResponse = {
        ok: false,
        status: 403,
        headers: { get: () => 'application/json' },
        clone() {
            return this;
        },
        json: async () => ({ detail: 'Cross-site request blocked' }),
    };
    let handledResponse = null;
    const context = {
        console,
        URL,
        URLSearchParams,
        CustomEvent: function CustomEvent(type, init) {
            this.type = type;
            this.detail = init?.detail;
        },
        localStorage: { removeItem: () => {}, setItem: () => {} },
        sessionStorage: { removeItem: () => {} },
        document: {
            title: 'Login',
            documentElement: { setAttribute: () => {} },
            addEventListener: () => {},
            body: { dataset: { page: 'login' } },
        },
        fetch: async () => blockedResponse,
    };
    context.window = {
        location: {
            origin: 'https://unconfigured.example',
            pathname: '/login',
            search: '',
            hash: '',
            href: 'https://unconfigured.example/login',
        },
        localStorage: context.localStorage,
        sessionStorage: context.sessionStorage,
        fetch: context.fetch,
        dispatchEvent: () => {},
        AbortController,
        setTimeout,
        clearTimeout,
        handleCrossSiteRequestBlock: async (response) => {
            handledResponse = response;
            return true;
        },
    };
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.window.document = context.document;
    vm.createContext(context);

    vm.runInContext(fs.readFileSync(authPath, 'utf8'), context, { filename: authPath });
    const refreshed = await context.window.__omlorixInitialAuthBootstrap;

    assert.equal(refreshed, false);
    assert.equal(handledResponse, blockedResponse);
    assert.equal(context.window.location.href, 'https://unconfigured.example/login');
});

test('refresh race retries under a cross-tab Web Lock without logging out', async () => {
    const authPath = path.join(__dirname, 'auth.js');
    let refreshCalls = 0;
    const lockCalls = [];
    const refreshSignals = [];
    const clearedTimeouts = [];
    const successPayload = {
        session_authenticated: true,
        has_to_change_password: false,
        needs_password_setup: false,
        needs_server_setup: false,
        is_admin: false,
        application_name: 'Omlorix',
        active_account_slot: 1,
        terms_of_service_policy: {
            revision: 1,
            accepted_current_revision: true,
            require_current_revision_for_access: false,
        },
    };
    const fetch = async (url, init = {}) => {
        if (String(url).includes('/api/v1/auth/refresh')) {
            refreshCalls += 1;
            refreshSignals.push(init.signal);
            if (refreshCalls === 1) {
                return {
                    ok: false,
                    status: 409,
                    clone() { return this; },
                    json: async () => ({
                        detail: { type: 'refresh_race', retry_after_ms: 50 },
                    }),
                };
            }
            return {
                ok: true,
                status: 200,
                json: async () => successPayload,
            };
        }
        return { ok: true, status: 200, json: async () => ({ accounts: [] }) };
    };
    const context = {
        console,
        URL,
        URLSearchParams,
        CustomEvent: function CustomEvent(type, init) {
            this.type = type;
            this.detail = init?.detail;
        },
        localStorage: { removeItem: () => {}, setItem: () => {} },
        sessionStorage: { removeItem: () => {} },
        document: {
            title: 'Omlorix',
            documentElement: { setAttribute: () => {} },
            addEventListener: () => {},
            body: { dataset: { page: 'index' } },
        },
        fetch,
    };
    context.window = {
        location: {
            origin: 'https://chat.example',
            pathname: '/',
            search: '',
            hash: '',
            href: 'https://chat.example/',
        },
        localStorage: context.localStorage,
        sessionStorage: context.sessionStorage,
        fetch,
        dispatchEvent: () => {},
        matchMedia: () => ({ matches: false }),
        AbortController,
        AbortSignal,
        setTimeout: (handler, delay) => {
            if (delay <= 1000) {
                handler();
            }
            return delay;
        },
        clearTimeout: (timeoutId) => clearedTimeouts.push(timeoutId),
        navigator: {
            locks: {
                request: async (name, options, callback) => {
                    lockCalls.push({ name, options });
                    return callback();
                },
            },
        },
    };
    context.navigator = context.window.navigator;
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.window.document = context.document;
    vm.createContext(context);

    vm.runInContext(fs.readFileSync(authPath, 'utf8'), context, { filename: authPath });
    const refreshed = await context.window.__omlorixInitialAuthBootstrap;

    assert.equal(refreshed, true);
    assert.equal(refreshCalls, 2);
    assert.equal(lockCalls.length, 1);
    assert.equal(lockCalls[0].name, 'omlorix-auth-refresh');
    assert.equal(lockCalls[0].options.mode, 'exclusive');
    assert.ok(lockCalls[0].options.signal instanceof AbortSignal);
    assert.equal(refreshSignals.length, 2);
    assert.ok(refreshSignals.every((signal) => signal instanceof AbortSignal));
    assert.deepEqual(clearedTimeouts, [15000, 15000]);
    assert.equal(context.window.location.href, 'https://chat.example/');
});

test('refresh falls back to an unlocked bounded request when lock wait times out', async () => {
    const authPath = path.join(__dirname, 'auth.js');
    let refreshCalls = 0;
    let receivedLockSignal = null;
    const successPayload = {
        session_authenticated: true,
        has_to_change_password: false,
        needs_password_setup: false,
        needs_server_setup: false,
        is_admin: false,
        application_name: 'Omlorix',
        active_account_slot: 1,
        terms_of_service_policy: {
            revision: 1,
            accepted_current_revision: true,
            require_current_revision_for_access: false,
        },
    };
    const fetch = async (url, init = {}) => {
        if (String(url).includes('/api/v1/auth/refresh')) {
            refreshCalls += 1;
            assert.ok(init.signal instanceof AbortSignal);
            return { ok: true, status: 200, json: async () => successPayload };
        }
        return { ok: true, status: 200, json: async () => ({ accounts: [] }) };
    };
    const context = {
        console,
        URL,
        URLSearchParams,
        CustomEvent: function CustomEvent(type, init) {
            this.type = type;
            this.detail = init?.detail;
        },
        localStorage: { removeItem: () => {}, setItem: () => {} },
        sessionStorage: { removeItem: () => {} },
        document: {
            title: 'Omlorix',
            documentElement: { setAttribute: () => {} },
            addEventListener: () => {},
            body: { dataset: { page: 'index' } },
        },
        fetch,
    };
    context.window = {
        location: {
            origin: 'https://chat.example',
            pathname: '/',
            search: '',
            hash: '',
            href: 'https://chat.example/',
        },
        localStorage: context.localStorage,
        sessionStorage: context.sessionStorage,
        fetch,
        dispatchEvent: () => {},
        matchMedia: () => ({ matches: false }),
        AbortController,
        AbortSignal,
        setTimeout,
        clearTimeout,
        navigator: {
            locks: {
                request: async (_name, options) => {
                    receivedLockSignal = options.signal;
                    const error = new Error('lock wait timed out');
                    error.name = 'TimeoutError';
                    throw error;
                },
            },
        },
    };
    context.navigator = context.window.navigator;
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.window.document = context.document;
    vm.createContext(context);

    vm.runInContext(fs.readFileSync(authPath, 'utf8'), context, { filename: authPath });
    const refreshed = await context.window.__omlorixInitialAuthBootstrap;

    assert.equal(refreshed, true);
    assert.ok(receivedLockSignal instanceof AbortSignal);
    assert.equal(refreshCalls, 1);
});

test('refresh request aborts at its deadline and clears the attempt timer', async () => {
    const authPath = path.join(__dirname, 'auth.js');
    const clearedTimeouts = [];
    let receivedSignal = null;
    const context = {
        console: { ...console, error: () => {} },
        URL,
        URLSearchParams,
        CustomEvent: function CustomEvent(type, init) {
            this.type = type;
            this.detail = init?.detail;
        },
        localStorage: { removeItem: () => {}, setItem: () => {} },
        sessionStorage: { removeItem: () => {} },
        document: {
            title: 'Omlorix',
            documentElement: { setAttribute: () => {} },
            addEventListener: () => {},
            body: { dataset: { page: 'index' } },
        },
        fetch: async (_url, init = {}) => {
            receivedSignal = init.signal;
            if (receivedSignal?.aborted) {
                const error = new Error('request timed out');
                error.name = 'AbortError';
                throw error;
            }
            throw new Error('test timer did not abort the request');
        },
    };
    context.window = {
        location: {
            origin: 'https://chat.example',
            pathname: '/',
            search: '',
            hash: '',
            href: 'https://chat.example/',
        },
        localStorage: context.localStorage,
        sessionStorage: context.sessionStorage,
        fetch: context.fetch,
        dispatchEvent: () => {},
        AbortController,
        setTimeout: (handler, delay) => {
            handler();
            return delay;
        },
        clearTimeout: (timeoutId) => clearedTimeouts.push(timeoutId),
    };
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.window.document = context.document;
    vm.createContext(context);

    vm.runInContext(fs.readFileSync(authPath, 'utf8'), context, { filename: authPath });
    const refreshed = await context.window.__omlorixInitialAuthBootstrap;

    assert.equal(refreshed, false);
    assert.ok(receivedSignal instanceof AbortSignal);
    assert.equal(receivedSignal.aborted, true);
    assert.deepEqual(clearedTimeouts, [15000]);
});

test('login warning opens access-blocked modal from refresh redirect params', () => {
    const warningPath = path.join(__dirname, '..', 'login', 'warning.js');
    const elements = Object.fromEntries([
        'warningOverlay',
        'warningBackToLoginButton',
        'pendingOverlay',
        'pendingBackToLoginButton',
        'accessBlockedOverlay',
        'accessBlockedTitle',
        'accessBlockedMessage',
        'accessBlockedCustomMessage',
        'accessBlockedTimerSection',
        'accessBlockedNextTime',
        'accessBlockedCountdown',
        'accessBlockedBackButton',
        'accessBlockedContactSupport',
        'signinEmail',
    ].map((id) => [id, createElement(id)]));
    let cleanedUrl = '';
    const context = {
        console,
        URL,
        URLSearchParams,
        Date,
        setTimeout: (handler) => {
            handler();
            return 1;
        },
        setInterval: () => 1,
        clearInterval: () => {},
        document: {
            title: 'Login',
            readyState: 'complete',
            addEventListener: () => {},
            getElementById: (id) => elements[id] || null,
            querySelector: () => createElement('query-result'),
            createElement: (tagName) => createElement(tagName),
        },
    };
    const search = '?redirect=%2Fchats&access_blocked=access_time_blocked&reason=outside_allowed_window&next_allowed_at=2026-06-14T08%3A30%3A00%2B00%3A00&blocked_message=Come%20back%20later';
    context.window = {
        location: {
            href: `https://chat.example/login${search}`,
            search,
        },
        history: {
            replaceState: (_state, _title, url) => {
                cleanedUrl = url;
            },
        },
        getTranslation: (_key, fallback) => fallback,
        loginModalManager: { sync: () => {} },
    };
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.globalThis = context.window;
    context.window.document = context.document;
    vm.createContext(context);

    vm.runInContext(fs.readFileSync(warningPath, 'utf8'), context, { filename: warningPath });

    assert.equal(elements.accessBlockedOverlay.classList.contains('active'), true);
    assert.equal(elements.accessBlockedOverlay.getAttribute('aria-hidden'), 'false');
    assert.equal(elements.accessBlockedTitle.textContent, 'Access Currently Unavailable');
    assert.equal(elements.accessBlockedCustomMessage.textContent, 'Come back later');
    assert.equal(elements.accessBlockedCustomMessage.style.display, 'block');
    assert.equal(elements.accessBlockedTimerSection.style.display, 'block');
    assert.equal(elements.accessBlockedBackButton.focusCalls, 1);
    assert.equal(cleanedUrl, '/login?redirect=%2Fchats');
});
