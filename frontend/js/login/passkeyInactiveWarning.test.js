const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeClassList {
    constructor() {
        this.classes = new Set();
    }

    add(...tokens) {
        tokens.filter(Boolean).forEach((token) => this.classes.add(token));
    }

    remove(...tokens) {
        tokens.filter(Boolean).forEach((token) => this.classes.delete(token));
    }

    toggle(token, force) {
        if (force === undefined) {
            if (this.classes.has(token)) {
                this.classes.delete(token);
            } else {
                this.classes.add(token);
            }
        } else if (force) {
            this.classes.add(token);
        } else {
            this.classes.delete(token);
        }
        return this.classes.has(token);
    }

    contains(token) {
        return this.classes.has(token);
    }
}

function createFakeElement(id = '') {
    return {
        id,
        hidden: false,
        disabled: false,
        value: '',
        textContent: '',
        dataset: {},
        style: {},
        classList: new FakeClassList(),
        listeners: {},
        focusCalls: 0,
        clickCalls: 0,
        addEventListener(eventName, handler) {
            this.listeners[eventName] = handler;
        },
        setAttribute(name, value) {
            this[name] = String(value);
        },
        removeAttribute(name) {
            delete this[name];
        },
        getAttribute(name) {
            return Object.prototype.hasOwnProperty.call(this, name) ? this[name] : null;
        },
        focus() {
            this.focusCalls += 1;
        },
        click() {
            this.clickCalls += 1;
            return this.listeners.click?.({ target: this });
        },
        select() {},
        querySelector() {
            return null;
        },
        querySelectorAll() {
            return [];
        },
        closest() {
            return null;
        },
    };
}

function createHarness({ signupResponse } = {}) {
    const elements = new Map();
    const getElement = (id) => {
        if (!elements.has(id)) {
            elements.set(id, createFakeElement(id));
        }
        return elements.get(id);
    };

    const loginContainer = createFakeElement('container');
    const warningCalls = [];
    const notificationMessages = [];
    const successMessages = [];
    const fetchCalls = [];
    const sessionStorageState = {};

    const window = {
        location: {
            href: '/login',
            pathname: '/login',
            search: '',
            hash: '',
            origin: 'https://chat.example.com',
            hostname: 'chat.example.com',
        },
        PublicKeyCredential: function PublicKeyCredential() {},
        WebAuthnHelpers: {
            preformatGetOptions: (options) => options,
            getRpIdMismatchMessage: () => '',
            publicKeyCredentialToJSON: () => ({ id: 'credential-1', response: {} }),
        },
        getTranslation: (key, fallback) => ({
            inactive_title: 'Account Inactive',
            inactive_message: 'Your account has been <strong>deactivated</strong>. Please contact support for further assistance.',
        }[key] || fallback),
        formatTranslation: (key, fallback, vars = {}) => Object.entries(vars).reduce(
            (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
            window.getTranslation(key, fallback),
        ),
        showWarning: (...args) => warningCalls.push(args),
        notifyError: (message) => notificationMessages.push(message),
        notifySuccess: (message) => successMessages.push(message),
        loginMethodTracker: {
            saveLastUsedLoginMethod: () => {},
        },
        resolvePostAuthRedirect: () => '/',
        isAddAccountMode: () => false,
        getRequestedReplacementSlot: () => null,
        getAccountReturnUrl: () => '',
        getTermsOfServiceAcceptancePayload: () => ({}),
        isValidEmail: () => true,
        sessionStorage: {
            getItem: (key) => (Object.prototype.hasOwnProperty.call(sessionStorageState, key) ? sessionStorageState[key] : null),
            setItem: (key, value) => {
                sessionStorageState[key] = String(value);
            },
            removeItem: (key) => {
                delete sessionStorageState[key];
            },
        },
        navigator: {
            credentials: {
                get: async () => ({ id: 'credential-1' }),
            },
        },
        document: null,
    };
    window.window = window;
    window.globalThis = window;

    const document = {
        body: { children: [] },
        activeElement: null,
        addEventListener: () => {},
        querySelector: (selector) => (selector === '.container' ? loginContainer : null),
        querySelectorAll: () => [],
        getElementById: (id) => getElement(id),
    };

    const fetchImpl = async (url) => {
        fetchCalls.push(url);
        if (url === '/api/v1/auth/ldap/status') {
            return {
                ok: true,
                json: async () => ({ enabled: false }),
            };
        }
        if (url === '/api/v1/auth/passkeys/authenticate/begin') {
            return {
                ok: true,
                json: async () => ({
                    challenge: 'challenge-1',
                    expected_origin: 'https://chat.example.com',
                    publicKey: {
                        challenge: 'challenge-1',
                        rpId: 'chat.example.com',
                    },
                }),
            };
        }
        if (url === '/api/v1/auth/passkeys/authenticate/finish') {
            return {
                ok: true,
                json: async () => ({ status: 'inactive' }),
            };
        }
        if (url === '/api/v1/auth/signup') {
            return signupResponse || {
                ok: true,
                status: 201,
                json: async () => ({ status: 'success' }),
            };
        }
        throw new Error(`Unexpected fetch request: ${url}`);
    };

    window.fetch = fetchImpl;

    const requiredIds = [
        'loginForm',
        'signinEntryStage',
        'signinMethodStage',
        'forgotPasswordReset',
        'signinEmail',
        'signinPassword',
        'signinContinueButton',
        'signinButton',
        'passkeySigninButton',
        'signinMethodDivider',
        'signinSelectedIdentifier',
        'changeSigninIdentifierButton',
        'forgotPasswordLink',
        'passwordResetIdentifier',
        'passwordResetRequestBtn',
        'passwordResetRequestStatus',
        'cancelPasswordResetButton',
        'ldapLoginHint',
        'registerForm',
        'signupButton',
        'firstName',
        'lastName',
        'signupEmail',
        'signupEmailError',
        'signupPassword',
        'confirmPassword',
        'signupTermsConsentCheckbox',
        'signupTermsConsent',
        'tabLogin',
    ];
    requiredIds.forEach((id) => getElement(id));

    const context = {
        console,
        window,
        document,
        fetch: fetchImpl,
        navigator: window.navigator,
        sessionStorage: window.sessionStorage,
        setTimeout: (fn) => {
            if (typeof fn === 'function') fn();
            return 0;
        },
        clearTimeout: () => {},
        URLSearchParams,
        isValidEmail: () => true,
        showWarning: (...args) => warningCalls.push(args),
        notifyError: (message) => notificationMessages.push(message),
        notifySuccess: (message) => successMessages.push(message),
    };
    window.isValidEmail = () => true;
    context.globalThis = window;
    window.document = document;
    return {
        context,
        warningCalls,
        notificationMessages,
        successMessages,
        fetchCalls,
        elements,
    };
}

function loadAuthentication(context) {
    const formValidationSource = fs.readFileSync(
        path.join(__dirname, '..', 'common', 'formValidation.js'),
        'utf8',
    );
    vm.runInNewContext(formValidationSource, context, {
        filename: 'frontend/js/common/formValidation.js',
    });

    const authenticationSource = fs.readFileSync(path.join(__dirname, 'authentication.js'), 'utf8');
    vm.runInNewContext(authenticationSource, context, {
        filename: 'frontend/js/login/authentication.js',
    });
}

test('passkey sign-in shows the inactive account warning instead of a generic error', async () => {
    const { context, warningCalls, notificationMessages, fetchCalls } = createHarness();
    loadAuthentication(context);

    const result = await context.window.signinWithPasskey({ identifierOverride: 'inactive@example.com' });

    assert.equal(result, false);
    assert.deepEqual(fetchCalls, [
        '/api/v1/auth/ldap/status',
        '/api/v1/auth/passkeys/authenticate/begin',
        '/api/v1/auth/passkeys/authenticate/finish',
    ]);
    assert.equal(warningCalls.length, 1);
    assert.equal(warningCalls[0][1], 'Account Inactive');
    assert.equal(String(warningCalls[0][2]).includes('deactivated'), true);
    assert.equal(notificationMessages.length, 0);
});

test('successful signup stays on login and prefills the sign-in email', async () => {
    const {
        context,
        elements,
        fetchCalls,
        successMessages,
    } = createHarness();
    loadAuthentication(context);

    elements.get('firstName').value = 'Ada';
    elements.get('lastName').value = 'Lovelace';
    elements.get('signupEmail').value = '  ADA@Example.COM  ';
    elements.get('signupPassword').value = 'correct horse battery staple';
    elements.get('confirmPassword').value = 'correct horse battery staple';
    elements.get('signupTermsConsent').hidden = true;

    await elements.get('registerForm').listeners.submit({ preventDefault() {} });

    assert.equal(context.window.location.href, '/login');
    assert.equal(elements.get('tabLogin').clickCalls, 1);
    assert.equal(elements.get('signinEmail').value, 'ada@example.com');
    assert.equal(elements.get('signinEmail').focusCalls, 1);
    assert.equal(elements.get('signupEmail').value, '');
    assert.equal(elements.get('signupPassword').value, '');
    assert.equal(elements.get('confirmPassword').value, '');
    assert.deepEqual(successMessages, ['Registration successful. Sign in now!']);
    assert.equal(fetchCalls.includes('/api/v1/auth/signup'), true);
});

test('signup rejects reserved test domains with an inline email error before submission', () => {
    const {
        context,
        elements,
        fetchCalls,
        notificationMessages,
    } = createHarness();
    loadAuthentication(context);

    elements.get('firstName').value = 'E2E';
    elements.get('lastName').value = 'Owner';
    elements.get('signupEmail').value = 'e2e-owner@example.test';
    elements.get('signupPassword').value = 'correct horse battery staple';
    elements.get('confirmPassword').value = 'correct horse battery staple';
    elements.get('signupTermsConsent').hidden = true;

    context.window.updateSignupButtonState();
    elements.get('signupEmail').listeners.blur();

    assert.equal(elements.get('signupButton').disabled, true);
    assert.equal(elements.get('signupEmail')['aria-invalid'], 'true');
    assert.equal(elements.get('signupEmail').classList.contains('input-error'), true);
    assert.notEqual(elements.get('signupEmailError').hidden, true);
    assert.equal(elements.get('signupEmailError')['aria-hidden'], 'false');
    assert.match(elements.get('signupEmailError').textContent, /Reserved domains such as \.test/);
    assert.equal(fetchCalls.includes('/api/v1/auth/signup'), false);
    assert.deepEqual(notificationMessages, []);
});

test('signup renders FastAPI email validation errors beside the email field', async () => {
    const {
        context,
        elements,
        notificationMessages,
    } = createHarness({
        signupResponse: {
            ok: false,
            status: 422,
            json: async () => ({
                detail: [{
                    type: 'value_error',
                    loc: ['body', 'email'],
                    msg: 'value is not a valid email address',
                }],
            }),
        },
    });
    loadAuthentication(context);

    elements.get('firstName').value = 'Ada';
    elements.get('lastName').value = 'Lovelace';
    elements.get('signupEmail').value = 'ada@example.com';
    elements.get('signupPassword').value = 'correct horse battery staple';
    elements.get('confirmPassword').value = 'correct horse battery staple';
    elements.get('signupTermsConsent').hidden = true;

    await elements.get('registerForm').listeners.submit({ preventDefault() {} });

    assert.equal(elements.get('signupEmail')['aria-invalid'], 'true');
    assert.notEqual(elements.get('signupEmailError').hidden, true);
    assert.equal(elements.get('signupEmailError')['aria-hidden'], 'false');
    assert.equal(elements.get('signupEmailError').textContent, 'Invalid email.');
    assert.equal(elements.get('signupEmail').focusCalls, 1);
    assert.deepEqual(notificationMessages, []);
});

test('signup markup associates the email input with its accessible inline error', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', '..', 'login.html'), 'utf8');

    assert.match(html, /id="signupEmail"[^>]+aria-describedby="signupEmailError"[^>]+aria-invalid="false"/);
    assert.match(html, /id="signupEmailError"[^>]+role="alert"[^>]+hidden/);
    assert.match(html, /src="\/js\/common\/formValidation\.js" defer/);
});
