const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const scriptPath = path.join(__dirname, 'script.js');
const stylePath = path.join(__dirname, '../../css/login/style.css');
const initStylePath = path.join(__dirname, '../../css/common/init.css');
const warningStylePath = path.join(__dirname, '../../css/common/warning.css');
const sharedModalStylePath = path.join(__dirname, '../../css/common/searchModal.css');

function readFile(filePath) {
    return fs.readFileSync(filePath, 'utf8');
}

test('login page does not apply a separate accent color scheme', () => {
    const source = readFile(scriptPath);

    assert.doesNotMatch(source, /applyLoginColorTheme/);
    assert.doesNotMatch(source, /loginCustomization\.color_theme/);
    assert.doesNotMatch(source, /window\.setColorTheme/);
});

test('the complete login layout remains hidden until settings and imagery are ready', () => {
    const source = readFile(path.join(__dirname, '../common/loginBoot.js'));
    const loginHtml = readFile(path.join(__dirname, '../../login.html'));
    const loginStyle = readFile(stylePath);

    assert.match(source, /document\.documentElement\.classList\.add\('login-ui-pending'\)/);
    assert.match(source, /window\.__loginUIReady = false/);
    assert.match(source, /document\.documentElement\.classList\.remove\('login-ui-pending'\)/);
    assert.match(loginHtml, /\.login-ui-pending \.login-layout\s*\{[\s\S]*visibility:\s*hidden !important/);
    assert.match(loginStyle, /\.login-ui-pending \.login-layout\s*\{[\s\S]*visibility:\s*hidden !important/);
    assert.doesNotMatch(loginHtml, /login-left-static-pending/);
    assert.doesNotMatch(loginStyle, /login-left-static-pending/);
});

test('login UI has a bounded reveal fallback when deferred initialization stalls', () => {
    const bootSource = readFile(path.join(__dirname, '../common/loginBoot.js'));
    const source = readFile(scriptPath);

    assert.match(bootSource, /LOGIN_UI_FAILSAFE_REVEAL_MS = 5000/);
    assert.match(bootSource, /setTimeout\([\s\S]*login-ui-pending[\s\S]*clearTimeout/);
    assert.match(source, /LOGIN_BACKGROUND_READY_TIMEOUT_MS = 8000/);
    assert.match(source, /new AbortController\(\)/);
    assert.match(source, /Timed out while decoding the login background image/);
});

test('image-backed split design is decoded before the login UI is revealed', () => {
    const source = readFile(scriptPath);

    assert.match(source, /\.then\(async data =>/);
    assert.match(source, /async function applyUISettings\(\)/);
    assert.match(source, /await applyLoginDesign\(\)/);
    assert.match(source, /async function applyLoginDesign\(\)/);
    assert.match(source, /await loadLoginBackgroundImage\(\)/);
    assert.match(source, /function decodeLoginBackgroundImage\(url\)/);
    assert.match(source, /await decodeLoginBackgroundImage\(url\)/);
    assert.match(source, /window\.__loginUIReady = true;[\s\S]*window\.__revealLoginUI\(\)/);
});

test('classic login design is the default before and after settings load', () => {
    const source = readFile(scriptPath);
    const loginHtml = readFile(path.join(__dirname, '../../login.html'));

    assert.match(source, /let loginDesign = "classic";/);
    assert.match(source, /loginCustomization\.login_design \|\| "classic"/);
    assert.match(loginHtml, /<div class="login-layout design-classic">/);
});

test('split login designs can use the configured design background color', () => {
    const source = readFile(scriptPath);
    const style = readFile(stylePath);

    assert.match(source, /\['split', 'split_image', 'centered', 'glass'\]\.includes\(loginDesign\)/);
    assert.match(style, /\.login-layout\.design-split \.login-branding \{[\s\S]*background:\s*var\(--login-design-bg,/);
    assert.match(style, /\.login-layout\.design-split-image \.login-branding \{[\s\S]*background:\s*var\(--login-bg-image,\s*var\(--login-design-bg,/);
    assert.match(source, /--login-branding-text-color/);
});

test('split login legal notices inherit the global mode palette', () => {
    const source = readFile(scriptPath);

    assert.match(source, /privacyNoticeUsesDesignBackground\s*=\s*\['centered', 'glass'\]\.includes\(loginDesign\)/);
    assert.match(source, /if \(privacyNoticeUsesDesignBackground && designBackgroundColor\)/);
});

test('light-mode login overlay keys off the document mode, not a fake theme name', () => {
    const source = readFile(stylePath);

    assert.match(source, /html\[data-mode="light"\] \.login-branding::before/);
    assert.doesNotMatch(source, /\[data-theme="light"\] \.login-branding::before/);
});

test('login supporting content uses the readable secondary text token', () => {
    const script = readFile(scriptPath);
    const style = readFile(stylePath);
    const warningStyle = readFile(warningStylePath);

    // Login hints and supporting copy are normal text, so they must not use the
    // lower-contrast tertiary token on the light login surface.
    assert.doesNotMatch(style, /--text-color-tertiary/u);
    assert.match(style, /\.login-tab\s*\{[^{}]*color:\s*var\(--text-color-secondary\)/u);
    assert.match(style, /\.form-hint\s*\{[^{}]*color:\s*var\(--text-color-secondary\)/u);
    assert.match(style, /\.privacy-notice\s*\{[^{}]*var\(--privacy-note-color, var\(--text-color-secondary\)\)/u);
    assert.doesNotMatch(style, /\.tfa-qr-copy-hint\s*\{/u);
    assert.match(warningStyle, /\.tfa-qr-copy-hint\s*\{[^{}]*color:\s*var\(--warning-text-secondary, var\(--text-color-secondary,/u);
    assert.match(script, /tab\.style\.color = 'var\(--text-color-secondary\)'/u);
});

test('every login dialog uses the shared modal shell and card semantics', () => {
    const loginHtml = readFile(path.join(__dirname, '../../login.html'));
    const modalIds = [
        'federatedTermsOverlay',
        'warningOverlay',
        'pendingOverlay',
        'tfaSetupOverlay',
        'tfaVerifyOverlay',
        'accessBlockedOverlay',
    ];

    assert.match(
        loginHtml,
        /\/css\/login\/style\.css[\s\S]*\/css\/common\/searchModal\.css/,
    );
    modalIds.forEach((id) => {
        assert.match(
            loginHtml,
            new RegExp(`<div class="shared-modal-overlay login-modal-overlay" id="${id}"[^>]*aria-hidden="true"[^>]*inert hidden>`),
        );
    });
    assert.equal((loginHtml.match(/class="shared-modal shared-modal--fit/g) || []).length, modalIds.length);
    assert.equal((loginHtml.match(/role="dialog" aria-modal="true"/g) || []).length, modalIds.length);
    assert.equal((loginHtml.match(/class="shared-modal-header shared-modal-header--main"/g) || []).length, modalIds.length);
    assert.equal((loginHtml.match(/class="shared-modal-footer"/g) || []).length, modalIds.length);
    assert.doesNotMatch(loginHtml, /class="warning-(?:overlay|card)/);
});

test('two-factor modal uses the shared modal color system', () => {
    const initStyle = readFile(initStylePath);
    const warningStyle = readFile(warningStylePath);
    const sharedModalStyle = readFile(sharedModalStylePath);
    const loginHtml = readFile(path.join(__dirname, '../../login.html'));

    assert.match(initStyle, /\[data-mode="dark"\][\s\S]*--modal-background:\s*#202023/);
    assert.match(initStyle, /--modal-primary-background:\s*#f4f4f5/);
    assert.match(warningStyle, /background:\s*var\(--modal-background/);
    assert.match(sharedModalStyle, /\.shared-modal-footer \.om-button\.border\.submit[\s\S]*var\(--modal-primary-background/);
    assert.match(warningStyle, /\.tfa-digit[\s\S]*var\(--modal-surface/);
    assert.match(loginHtml, /<p class="warning-message" id="tfaVerifyInstruction"/);
});
