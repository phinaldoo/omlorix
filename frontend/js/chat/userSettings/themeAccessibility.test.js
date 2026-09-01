const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const frontendRoot = path.resolve(__dirname, '../../..');
const indexSource = fs.readFileSync(path.join(frontendRoot, 'index.html'), 'utf8');
const themeSource = fs.readFileSync(path.join(__dirname, 'theme.js'), 'utf8');
const shortcutsSource = fs.readFileSync(path.join(__dirname, '..', 'shortcuts.js'), 'utf8');
const stylesSource = fs.readFileSync(path.join(frontendRoot, 'css/userSettings/style.css'), 'utf8');

test('theme cards expose a named native radio group', () => {
    assert.match(
        indexSource,
        /class="theme-mode-button-container" role="radiogroup" aria-labelledby="themeModeLabel" aria-describedby="themeModeDescription"/,
    );

    for (const mode of ['system', 'light', 'dark']) {
        assert.match(
            indexSource,
            new RegExp(`<input class="theme-mode-input" type="radio" name="theme-mode" value="${mode}" data-theme-mode="${mode}"`),
        );
    }

    assert.match(indexSource, /data-theme-mode="system" checked/);
    assert.equal((indexSource.match(/name="theme-mode"/g) || []).length, 3);
    assert.equal((indexSource.match(/class="theme-mode-button" src="\/assets\/theme\/(?:system|light|dark)\.png" alt=""/g) || []).length, 3);
});

test('theme state and all activation paths target the native radios', () => {
    assert.match(themeSource, /themeModeButtons: '\.theme-mode-input'/);
    assert.match(themeSource, /button\.checked = isSelected/);
    assert.match(themeSource, /button\.addEventListener\('change'/);
    assert.match(themeSource, /if \(!button\.checked\) return/);
    assert.match(shortcutsSource, /\.theme-mode-input\[data-theme-mode=/);
});

test('loading and changing the theme synchronize checked state and persistence', () => {
    const requests = [];
    const appliedThemes = [];

    const createRadio = (mode) => {
        const listeners = new Map();
        const container = { classList: { toggle() {} } };
        return {
            checked: false,
            dataset: { themeMode: mode },
            addEventListener(type, listener) {
                listeners.set(type, listener);
            },
            closest() {
                return container;
            },
            dispatchChange() {
                listeners.get('change')?.();
            },
        };
    };
    const radios = ['system', 'light', 'dark'].map(createRadio);
    const context = {
        console,
        document: {
            querySelectorAll(selector) {
                return selector === '.theme-mode-input' ? radios : [];
            },
        },
        notifyError() {},
        setColorTheme() {},
        setTheme(mode) {
            appliedThemes.push(mode);
        },
        window: {
            authedFetch(url, options) {
                requests.push({ url, options });
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({ status: 'success' }),
                });
            },
        },
    };

    vm.runInNewContext(themeSource, context);
    context.initializeThemeSettings('dark', 'mono');

    assert.deepEqual(radios.map((radio) => radio.checked), [false, false, true]);

    radios[1].checked = true;
    radios[1].dispatchChange();

    assert.deepEqual(radios.map((radio) => radio.checked), [false, true, false]);
    assert.deepEqual(appliedThemes, ['light']);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/v1/users/color-theme/update');
    assert.deepEqual(JSON.parse(requests[0].options.body), {
        theme: 'light',
        color_theme: 'mono',
    });
});

test('keyboard focus is visibly exposed on each theme card', () => {
    assert.match(stylesSource, /\.theme-mode-input:focus-visible \+ \.theme-mode-button/);
    assert.match(stylesSource, /outline:\s*2px solid var\(--primary-color\)/);
    assert.match(stylesSource, /outline-offset:\s*2px/);
});
