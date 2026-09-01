const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'signinMethods.js'), 'utf8');
const indexSource = fs.readFileSync(path.join(__dirname, '../../../index.html'), 'utf8');
const settingsStyles = fs.readFileSync(path.join(__dirname, '../../../css/userSettings/style.css'), 'utf8');

test('sign-in method controls use protected link and unlink endpoints', () => {
    assert.match(source, /ensureSecurityStepUp/);
    assert.match(source, /\/api\/v1\/auth\/social\/\$\{encodeURIComponent\(provider\)\}\/link\/init/);
    assert.match(source, /method: 'DELETE'/);
    assert.match(source, /\/api\/v1\/auth\/sign-in-methods/);
});

test('disconnect confirmation is accessible and avoids native browser dialogs', () => {
    assert.match(source, /class="delete-warning-card shared-modal shared-modal--compact shared-modal--fit" role="dialog" aria-modal="true"/);
    assert.match(source, /overlay\.className = 'delete-warning-overlay shared-modal-overlay'/);
    assert.match(source, /class="shared-modal-header shared-modal-header--main"/);
    assert.match(source, /class="shared-modal-body shared-modal-body--centered"/);
    assert.match(source, /class="warning-navigation shared-modal-footer"/);
    assert.match(source, /trapDisconnectDialogFocus/);
    assert.match(source, /setBackgroundInert/);
    assert.doesNotMatch(source, /\b(?:alert|confirm|prompt)\s*\(/);
});

test('security settings contain the sign-in method surface and load its controller', () => {
    assert.match(indexSource, /id="signInMethodsSettingsSection"/);
    assert.match(indexSource, /id="socialSignInMethodsList"[^>]*aria-live="polite"[^>]*hidden/);
    assert.match(indexSource, /\/js\/chat\/userSettings\/signinMethods\.js/);
    assert.match(settingsStyles, /\.us-setting-item\[hidden\]\s*\{\s*display: none;/);
});

test('security settings present privacy before the authentication methods', () => {
    const orderedMarkers = [
        'data-i18n="us_security_privacy_title"',
        'id="changePasswordSettingsSection"',
        'id="socialSignInMethodsList"',
        'id="twoFactorSettingsSection"',
        'id="passkeySection"',
    ];
    const positions = orderedMarkers.map((marker) => indexSource.indexOf(marker));

    assert.ok(positions.every((position) => position >= 0));
    assert.deepEqual(positions, [...positions].sort((left, right) => left - right));
});

test('social sign-in methods stay hidden when no providers are relevant', () => {
    assert.match(source, /list\.hidden = methods\.length === 0/);
    assert.doesNotMatch(source, /us_sign_in_methods_empty/);
});
