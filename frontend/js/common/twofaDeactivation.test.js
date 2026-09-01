const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const FRONTEND_ROOT = path.join(__dirname, '..', '..');

function createButton(id) {
    const attributes = new Map();
    return {
        id,
        hidden: false,
        disabled: false,
        style: {},
        listeners: {},
        addEventListener(eventName, handler) {
            this.listeners[eventName] = handler;
        },
        getAttribute(name) {
            return attributes.get(name) ?? null;
        },
        setAttribute(name, value) {
            attributes.set(name, String(value));
        },
        removeAttribute(name) {
            attributes.delete(name);
        },
    };
}

function createOverlay() {
    const classes = new Set();
    const attributes = new Map();
    return {
        dataset: {},
        classList: {
            add(name) {
                classes.add(name);
            },
            remove(name) {
                classes.delete(name);
            },
            contains(name) {
                return classes.has(name);
            },
        },
        addEventListener() {},
        removeEventListener() {},
        matches() {
            return false;
        },
        querySelector() {
            return null;
        },
        querySelectorAll() {
            return [];
        },
        getAttribute(name) {
            return attributes.get(name) ?? null;
        },
        removeAttribute(name) {
            attributes.delete(name);
        },
        setAttribute(name, value) {
            attributes.set(name, String(value));
        },
    };
}

function createInput(value = '') {
    const attributes = new Map();
    return {
        value,
        focusCount: 0,
        selectCount: 0,
        listeners: {},
        addEventListener(eventName, handler) {
            this.listeners[eventName] = handler;
        },
        focus() {
            this.focusCount += 1;
        },
        select() {
            this.selectCount += 1;
        },
        getAttribute(name) {
            return attributes.get(name) ?? null;
        },
        setAttribute(name, attributeValue) {
            attributes.set(name, String(attributeValue));
        },
        removeAttribute(name) {
            attributes.delete(name);
        },
    };
}

function createErrorRegion() {
    return {
        hidden: true,
        textContent: '',
        focusCount: 0,
        focus() {
            this.focusCount += 1;
        },
    };
}

function loadTwofa({ stepUpResult = true, response }) {
    const calls = [];
    const overlay = createOverlay();
    const digitInputs = Array.from({ length: 6 }, () => createInput());
    const errorRegion = createErrorRegion();
    const primaryLabel = { textContent: '' };
    const elements = {
        deactivate2FAButton: createButton('deactivate2FAButton'),
        setup2FABtn: createButton('setup2FABtn'),
        reset2FABtn: createButton('reset2FABtn'),
        tfaSetupPrimaryButton: createButton('tfaSetupPrimaryButton'),
        tfaSetupPrimaryText: primaryLabel,
        tfaSetupError: errorRegion,
        tfaSetupOverlay: overlay,
    };
    const context = {
        clearTimeout,
        console,
        HTMLElement: class HTMLElement {},
        document: {
            activeElement: null,
            body: { appendChild() {}, style: { overflow: '' } },
            contains: () => false,
            createElement: () => ({}),
            getElementById: (id) => elements[id] || null,
            querySelectorAll: (selector) => (
                selector === '#tfaSetupOverlay .tfa-digit' ? digitInputs : []
            ),
        },
        navigator: {},
        notifyError: (message) => calls.push(['error', message]),
        notifySuccess: (message) => calls.push(['success', message]),
        redirectToLogin: () => calls.push(['redirect']),
        setTimeout: () => 0,
    };
    context.window = {
        authedFetch: async (url, init) => {
            calls.push(['fetch', url, init]);
            return typeof response === 'function' ? response(url, init) : response;
        },
        ensureSecurityStepUp: async () => {
            calls.push(['step-up']);
            return stepUpResult;
        },
        getTranslation: (_key, fallback) => fallback,
    };
    context.globalThis = context.window;

    vm.createContext(context);
    const sourcePath = path.join(__dirname, 'twofa.js');
    vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context, { filename: sourcePath });
    return {
        calls,
        context,
        deactivateButton: elements.deactivate2FAButton,
        digitInputs,
        errorRegion,
        overlay,
        primaryButton: elements.tfaSetupPrimaryButton,
        primaryLabel,
    };
}

test('2FA deactivation cancellation never reaches the backend', async () => {
    const { calls, deactivateButton } = loadTwofa({
        stepUpResult: false,
        response: { ok: true, status: 200 },
    });

    await deactivateButton.listeners.click();

    assert.deepEqual(calls, [['step-up']]);
});

test('2FA deactivation reaches the backend only after successful step-up', async () => {
    const { calls, deactivateButton } = loadTwofa({
        stepUpResult: true,
        response: { ok: true, status: 200 },
    });

    await deactivateButton.listeners.click();

    assert.deepEqual(calls.map(([name]) => name), ['step-up', 'fetch', 'success']);
    assert.equal(calls[1][1], '/api/v1/auth/twofa/deactivate');
    assert.equal(calls[1][2].method, 'POST');
});

test('2FA deactivation reports a step-up rejection without logging out the session', async () => {
    const { calls, deactivateButton } = loadTwofa({
        stepUpResult: true,
        response: {
            ok: false,
            status: 403,
            json: async () => ({ detail: 'Step-up authentication required' }),
        },
    });

    await deactivateButton.listeners.click();

    assert.deepEqual(calls.map(([name]) => name), ['step-up', 'fetch', 'error']);
    assert.equal(calls.some(([name]) => name === 'redirect'), false);
});

test('dismissing 2FA setup invalidates an in-flight setup response', async () => {
    let resolveResponse;
    const response = new Promise((resolve) => {
        resolveResponse = resolve;
    });
    const { calls, context, overlay } = loadTwofa({ response });

    const setup = context.show2FASetup();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(overlay.classList.contains('active'), true);

    await context.hide2FASetup();
    resolveResponse({
        ok: true,
        status: 200,
        json: async () => ({ provider: 'email', delivery_hint: 'user@example.com' }),
    });
    await setup;

    assert.equal(overlay.classList.contains('active'), false);
    assert.equal(calls.some(([name]) => name === 'success'), false);
});

test('2FA setup never exposes enrollment material when step-up is cancelled', async () => {
    const { calls, context, overlay } = loadTwofa({
        stepUpResult: false,
        response: { ok: true, status: 200 },
    });

    await context.show2FASetup();

    assert.deepEqual(calls, [['step-up']]);
    assert.equal(overlay.classList.contains('active'), false);
});

test('invalid 2FA setup codes show an accessible inline error and prepare a clean retry', async () => {
    const harness = loadTwofa({
        response: {
            ok: true,
            status: 200,
            json: async () => ({ status: 'otp_invalid', provider: 'totp' }),
        },
    });
    harness.overlay.classList.add('active');
    harness.digitInputs.forEach((input) => {
        input.value = '0';
    });

    await harness.context.verify2FASetup();

    assert.equal(harness.errorRegion.hidden, false);
    assert.equal(
        harness.errorRegion.textContent,
        'That code is incorrect. Enter a new code and try again.',
    );
    harness.digitInputs.forEach((input) => {
        assert.equal(input.value, '');
        assert.equal(input.getAttribute('aria-invalid'), 'true');
        assert.equal(input.getAttribute('aria-describedby'), 'tfaSetupError');
    });
    assert.equal(harness.digitInputs[0].focusCount, 1);
    assert.equal(harness.primaryButton.disabled, false);
    assert.equal(harness.overlay.getAttribute('aria-busy'), null);
});

test('2FA setup exposes a busy state and prevents duplicate verification requests', async () => {
    let resolveResponse;
    const response = new Promise((resolve) => {
        resolveResponse = resolve;
    });
    const harness = loadTwofa({ response });
    harness.overlay.classList.add('active');
    harness.digitInputs.forEach((input, index) => {
        input.value = String(index + 1);
    });

    const firstRequest = harness.context.verify2FASetup();
    const duplicateRequest = harness.context.verify2FASetup();
    await Promise.resolve();

    assert.equal(harness.primaryButton.disabled, true);
    assert.equal(harness.primaryLabel.textContent, 'Verifying…');
    assert.equal(harness.overlay.getAttribute('aria-busy'), 'true');
    assert.equal(harness.calls.filter(([name]) => name === 'fetch').length, 1);

    resolveResponse({
        ok: true,
        status: 200,
        json: async () => ({ status: 'otp_invalid', provider: 'totp' }),
    });
    await Promise.all([firstRequest, duplicateRequest]);

    assert.equal(harness.primaryButton.disabled, false);
    assert.equal(harness.primaryLabel.textContent, 'Verify and Enable');
    assert.equal(harness.overlay.getAttribute('aria-busy'), null);
});

test('2FA setup error feedback is present in the dialog and translated in every locale', () => {
    const indexMarkup = fs.readFileSync(path.join(FRONTEND_ROOT, 'index.html'), 'utf8');
    assert.match(
        indexMarkup,
        /id="tfaSetupError"[^>]*role="alert"[^>]*aria-live="assertive"[^>]*aria-atomic="true"[^>]*hidden/,
    );

    const requiredKeys = [
        'tfa_setup_verifying',
        'tfa_setup_invalid_code',
        'tfa_setup_too_many_attempts',
        'tfa_setup_verify_failed',
    ];
    const localeRoot = path.join(FRONTEND_ROOT, 'i18n');
    for (const locale of fs.readdirSync(localeRoot)) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(localeRoot, locale, 'index.json'), 'utf8'));
        for (const key of requiredKeys) {
            assert.equal(typeof dictionary[key], 'string', `${locale} is missing ${key}`);
            assert.notEqual(dictionary[key].trim(), '', `${locale} has an empty ${key}`);
        }
    }
});
