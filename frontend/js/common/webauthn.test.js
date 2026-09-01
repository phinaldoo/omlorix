const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const WEBAUTHN_PATH = path.join(__dirname, 'webauthn.js');

function loadWebAuthn({
    hostname = '127.0.0.1',
    origin = 'http://127.0.0.1:18080',
} = {}) {
    const window = {
        location: { hostname, origin },
        getTranslation: (_key, fallback) => fallback,
    };
    window.formatTranslation = (key, fallback, vars = {}) => Object.entries(vars).reduce(
        (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
        window.getTranslation(key, fallback),
    );
    window.window = window;

    const context = { console, window };
    context.globalThis = window;
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(WEBAUTHN_PATH, 'utf8'), context, { filename: WEBAUTHN_PATH });
    return window.WebAuthnHelpers;
}

test('compatible HTTP IP SecurityError recommends a secure public origin, not the current URL', () => {
    const helpers = loadWebAuthn();
    const options = { publicKey: { rpId: '127.0.0.1' } };
    const context = {
        actionLabel: 'sign-in',
        expectedOrigin: 'http://127.0.0.1:18080',
    };

    assert.equal(helpers.getRpIdMismatchMessage(options, context), '');

    const message = helpers.getWebAuthnErrorMessage({
        name: 'SecurityError',
        message: "The effective domain of this request's client is not a valid domain.",
    }, options, context);

    assert.match(message, /configured HTTPS public URL with a valid domain name/);
    assert.doesNotMatch(message, /configured for "127\.0\.0\.1"/);
    assert.doesNotMatch(message, /Open http:\/\/127\.0\.0\.1:18080/);
});

test('an actual RP ID mismatch keeps the mismatch guidance', () => {
    const helpers = loadWebAuthn({
        hostname: 'chat.example.net',
        origin: 'https://chat.example.net',
    });
    const options = { publicKey: { rpId: 'example.com' } };

    const message = helpers.getWebAuthnErrorMessage({
        name: 'SecurityError',
        message: 'The RP ID is invalid.',
    }, options, {
        actionLabel: 'sign-in',
        expectedOrigin: 'https://example.com',
    });

    assert.match(message, /You are on "chat\.example\.net"/);
    assert.match(message, /configured for "example\.com"/);
    assert.match(message, /Open https:\/\/example\.com/);
});

test('a compatible domain remains valid before the browser request', () => {
    const helpers = loadWebAuthn({
        hostname: 'login.example.com',
        origin: 'https://login.example.com',
    });

    assert.equal(
        helpers.getRpIdMismatchMessage({ publicKey: { rpId: 'example.com' } }),
        '',
    );
});

test('unrelated credential errors are not reclassified as origin failures', () => {
    const helpers = loadWebAuthn({
        hostname: 'login.example.com',
        origin: 'https://login.example.com',
    });
    const options = { publicKey: { rpId: 'example.com' } };

    assert.equal(helpers.getWebAuthnErrorMessage({
        name: 'NotAllowedError',
        message: 'The request was cancelled.',
    }, options), '');
    assert.equal(helpers.getWebAuthnErrorMessage({
        name: 'SecurityError',
        message: 'Blocked by an unrelated policy.',
    }, options), '');
});

test('secure-origin guidance is translated for every shared locale', () => {
    const i18nRoot = path.join(__dirname, '..', '..', 'i18n');
    for (const locale of fs.readdirSync(i18nRoot)) {
        const indexPath = path.join(i18nRoot, locale, 'index.json');
        const translations = JSON.parse(fs.readFileSync(indexPath, 'utf8'));
        assert.ok(
            translations.passkey_origin_security_error_message?.trim(),
            `${locale}/index.json is missing passkey_origin_security_error_message`,
        );
    }
});
