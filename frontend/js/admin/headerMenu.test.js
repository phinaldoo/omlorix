const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function readFrontendFile(relativePath) {
    return fs.readFileSync(path.join(__dirname, '..', '..', relativePath), 'utf8');
}

/**
 * Return one element's complete source range by tracking nested HTML tags.
 * This lightweight helper is sufficient for validating static project markup
 * without treating later, unrelated elements as descendants.
 */
function getElementMarkupById(html, targetId) {
    const tagPattern = /<\/?([a-z][\w:-]*)\b[^>]*>/gi;
    const voidElements = new Set([
        'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
        'meta', 'param', 'source', 'track', 'wbr',
    ]);
    const stack = [];
    let match;

    while ((match = tagPattern.exec(html)) !== null) {
        const source = match[0];
        const tagName = match[1].toLowerCase();
        const isClosingTag = source.startsWith('</');

        if (isClosingTag) {
            const element = stack.pop();
            assert.equal(element?.tagName, tagName, `Unexpected closing tag </${tagName}>`);
            if (element.id === targetId) {
                return html.slice(element.start, tagPattern.lastIndex);
            }
            continue;
        }

        const idMatch = source.match(/\bid\s*=\s*(["'])(.*?)\1/i);
        const element = {
            id: idMatch?.[2] || null,
            start: match.index,
            tagName,
        };
        const isVoidElement = voidElements.has(tagName) || /\/\s*>$/.test(source);

        if (isVoidElement) {
            if (element.id === targetId) return source;
            continue;
        }

        stack.push(element);
    }

    assert.fail(`Unable to find complete markup for #${targetId}`);
}

test('admin header exposes theme selection only inside the profile dropdown', () => {
    const adminHtml = readFrontendFile('admin.html');

    assert.doesNotMatch(adminHtml, /id="adminThemeToggle"/);
    assert.doesNotMatch(adminHtml, /id="adminThemeDropdown"/);
    const profileDropdownMarkup = getElementMarkupById(adminHtml, 'adminProfileDropdown');
    assert.match(profileDropdownMarkup, /class=(["'])[^"']*\btheme-selector\b[^"']*\1/);

    for (const mode of ['system', 'light', 'dark']) {
        assert.match(adminHtml, new RegExp(`class="theme-btn"[^>]*data-theme="${mode}"[^>]*aria-pressed="false"`));
    }
});

test('admin and login load the shared theme-selector component', () => {
    const adminHtml = readFrontendFile('admin.html');
    const loginHtml = readFrontendFile('login.html');
    const sharedStyles = readFrontendFile('css/common/themeSelector.css');

    assert.match(adminHtml, /href="\/css\/common\/themeSelector\.css"/);
    assert.match(loginHtml, /href="\/css\/common\/themeSelector\.css"/);
    assert.match(sharedStyles, /\.theme-btn\.active/);
    assert.match(sharedStyles, /@media \(prefers-reduced-motion: reduce\)/);
});

test('admin theme behavior targets the integrated buttons and persists the selected mode', () => {
    const themeScript = readFrontendFile('js/admin/theme.js');
    const profileScript = readFrontendFile('js/admin/profile.js');

    assert.match(themeScript, /getElementById\('adminProfileDropdown'\)/);
    assert.match(themeScript, /querySelectorAll\('\.theme-btn\[data-theme\]'\)/);
    assert.match(themeScript, /button\.classList\.toggle\('active', isActive\)/);
    assert.match(themeScript, /button\.setAttribute\('aria-pressed', String\(isActive\)\)/);
    assert.match(themeScript, /persistTheme\(mode\)/);
    assert.match(profileScript, /inert:\s*true/);
    assert.match(profileScript, /manageFocusable:\s*true/);
});

test('rapid admin theme selections persist to the server in selection order', async () => {
    const themeScript = readFrontendFile('js/admin/theme.js');
    const requests = [];
    const pendingResponses = [];
    const domReadyListeners = [];

    const createButton = (theme) => {
        const listeners = new Map();
        return {
            dataset: { theme },
            classList: { toggle() {} },
            setAttribute() {},
            addEventListener(type, listener) {
                listeners.set(type, listener);
            },
            click() {
                listeners.get('click')?.();
            },
        };
    };
    const buttons = ['system', 'light', 'dark'].map(createButton);
    const documentElement = {
        getAttribute() { return 'system'; },
        setAttribute() {},
    };
    const context = {
        Array,
        console,
        document: {
            documentElement,
            addEventListener(type, listener) {
                if (type === 'DOMContentLoaded') domReadyListeners.push(listener);
            },
            getElementById(id) {
                if (id !== 'adminProfileDropdown') return null;
                return { querySelectorAll: () => buttons };
            },
        },
        localStorage: { getItem: () => 'system', setItem() {} },
        MutationObserver: class {
            observe() {}
        },
        window: {
            setTheme() {},
            authedFetch(url, options) {
                requests.push({ url, options });
                return new Promise((resolve) => pendingResponses.push(resolve));
            },
        },
    };

    vm.runInNewContext(themeScript, context);
    domReadyListeners.forEach((listener) => listener());

    buttons[1].click();
    buttons[2].click();
    await Promise.resolve();

    assert.equal(requests.length, 1, 'the dark PATCH must wait for the light PATCH');
    assert.deepEqual(JSON.parse(requests[0].options.body), { theme: 'light' });

    pendingResponses[0]({ ok: true });
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(requests.length, 2);
    assert.deepEqual(JSON.parse(requests[1].options.body), { theme: 'dark' });
    pendingResponses[1]({ ok: true });
    await new Promise((resolve) => setImmediate(resolve));
});
