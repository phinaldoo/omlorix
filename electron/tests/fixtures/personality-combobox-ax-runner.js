const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { app, BrowserWindow } = require('electron');

const ROOT = path.resolve(__dirname, '..', '..', '..');
const PRESETS = [
    ['none', 'None'],
    ['standard', 'Standard'],
    ['professional', 'Professional'],
    ['friendly', 'Friendly'],
    ['honest', 'Honest'],
    ['quirky', 'Quirky'],
    ['efficient', 'Efficient'],
    ['cynical', 'Cynical'],
    ['custom', 'Custom'],
];
const GERMAN_LABELS = {
    none: 'Keine',
    standard: 'Standard',
    professional: 'Professionell',
    friendly: 'Freundlich',
    honest: 'Ehrlich',
    quirky: 'Unkonventionell',
    efficient: 'Effizient',
    cynical: 'Zynisch',
    custom: 'Benutzerdefiniert',
};

function scriptSource(relativePath) {
    return fs.readFileSync(path.join(ROOT, relativePath), 'utf8');
}

function scriptTag(source) {
    return `<script>${source.replace(/<\/script/gi, '<\\/script')}</script>`;
}

function fixtureHtml() {
    const options = PRESETS.map(([value, label], index) => (
        `<div class="select-option${index === 0 ? ' selected' : ''}" data-value="${value}">${label}</div>`
    )).join('');
    const markup = `
        <div class="personality-settings-card" id="personalitySettingsCard">
            <p id="personalitySelectDescription">Choose a personality.</p>
            <div class="custom-select personality-select" id="personalityPresetSelect">
                <div class="select-trigger" data-field="personality_preset" aria-label="Personality" aria-describedby="personalitySelectDescription"><span>None</span></div>
                <div class="select-options">${options}</div>
            </div>
            <div id="personalityCustomField" hidden>
                <textarea id="personalityCustomInstruction"></textarea>
                <p id="personalityCustomHelper"></p>
                <p id="personalityCustomError"></p>
            </div>
        </div>`;
    const supportSource = `
        window.Icons = { chevron: '' };
        window.state = { userData: { personality_preset: 'none' } };
        window.__personalityRequests = [];
        function helperT(_key, fallback) { return fallback; }
        function getFieldPlaceholder(field, fallback) { return field?.placeholder || fallback; }
        function getAdminIconMarkup() { return ''; }
        window.authedFetch = (_url, options) => new Promise((resolve) => {
            window.__personalityRequests.push({
                payload: JSON.parse(options.body),
                resolve,
            });
        });
        window.__resolvePersonalityRequest = (preset) => {
            const request = window.__personalityRequests.shift();
            if (!request) { throw new Error('No pending personality request'); }
            request.resolve({
                ok: true,
                json: async () => ({
                    updated: {
                        chat: {
                            personality_preset: preset,
                            personality_custom_instruction: '',
                        },
                    },
                }),
            });
            return request.payload;
        };
    `;
    const readySource = `
        document.addEventListener('DOMContentLoaded', () => {
            window.initUserPersonalitySettings({
                personality_preset: 'none',
                personality_custom_instruction: '',
            });
            window.__personalityFixtureReady = true;
        });
    `;

    return [
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Personality combobox AX test</title></head><body>',
        markup,
        scriptTag(supportSource),
        scriptTag(scriptSource('frontend/js/admin/helper/selectControls.js')),
        scriptTag(scriptSource('frontend/js/common/customSelect.js')),
        scriptTag(scriptSource('frontend/js/chat/userSettings/personality.js')),
        scriptTag(readySource),
        '</body></html>',
    ].join('');
}

async function waitFor(browserWindow, expression, message) {
    for (let attempt = 0; attempt < 100; attempt += 1) {
        if (await browserWindow.webContents.executeJavaScript(Boolean(expression) ? expression : 'false')) {
            return;
        }
        await new Promise((resolve) => setTimeout(resolve, 10));
    }
    throw new Error(message);
}

async function accessibilityState(browserWindow) {
    const domState = await browserWindow.webContents.executeJavaScript(`(() => {
        const root = document.getElementById('personalityPresetSelect');
        const state = root.__customSelectState;
        const trigger = state.trigger;
        const selected = state.menu.querySelector('.admin-select-option[aria-selected="true"]');
        return {
            activeDescendant: trigger.getAttribute('aria-activedescendant'),
            expanded: trigger.getAttribute('aria-expanded'),
            label: trigger.querySelector('.admin-select-value').textContent,
            nativeValue: state.nativeSelect.value,
            selectedId: selected?.id || '',
            selectedLabel: selected?.querySelector('.admin-select-option-text')?.textContent || '',
            selectedValue: selected?.dataset.value || '',
        };
    })()`);
    const tree = await browserWindow.webContents.debugger.sendCommand('Accessibility.getFullAXTree');
    const combobox = tree.nodes.find((node) => (
        node.role?.value === 'combobox' && node.name?.value === 'Personality'
    ));
    assert.ok(combobox, 'Chrome accessibility tree must contain the Personality combobox');
    const activeDescendant = combobox.properties?.find(
        (property) => property.name === 'activedescendant'
    )?.value?.relatedNodes?.[0]?.idref;

    return {
        ...domState,
        axActiveDescendant: activeDescendant || '',
        axValue: String(combobox.value?.value ?? ''),
    };
}

async function assertAccessiblePreset(browserWindow, value, label, phase) {
    const state = await accessibilityState(browserWindow);
    assert.equal(state.nativeValue, value, `${phase}: native value`);
    assert.equal(state.selectedValue, value, `${phase}: selected option`);
    assert.equal(state.label, label, `${phase}: visible value`);
    assert.equal(state.selectedLabel, label, `${phase}: selected option label`);
    assert.equal(state.activeDescendant, state.selectedId, `${phase}: DOM active descendant`);
    assert.equal(state.axActiveDescendant, state.selectedId, `${phase}: AX active descendant`);
    assert.equal(state.axValue, label, `${phase}: Chrome AX value`);
}

async function chooseWithPointer(browserWindow, value) {
    return browserWindow.webContents.executeJavaScript(`(() => {
        const state = document.getElementById('personalityPresetSelect').__customSelectState;
        const option = Array.from(state.menu.querySelectorAll('.admin-select-option'))
            .find((candidate) => candidate.dataset.value === ${JSON.stringify(value)});
        if (!option) { throw new Error('Missing option: ${value}'); }
        option.click();
        return {
            pendingCount: window.__personalityRequests.length,
            saving: document.getElementById('personalitySettingsCard').classList.contains('is-saving'),
        };
    })()`);
}

async function chooseStandardWithKeyboard(browserWindow) {
    return browserWindow.webContents.executeJavaScript(`(() => {
        const root = document.getElementById('personalityPresetSelect');
        const trigger = root.__customSelectState.trigger;
        const press = (element, key) => element.dispatchEvent(new KeyboardEvent('keydown', {
            key,
            bubbles: true,
            cancelable: true,
        }));
        trigger.focus();
        press(trigger, 'ArrowDown');
        press(document.activeElement, 'ArrowDown');
        const focusedValue = document.activeElement?.dataset?.value;
        press(document.activeElement, 'Enter');
        return {
            focusedValue,
            pendingCount: window.__personalityRequests.length,
            saving: document.getElementById('personalitySettingsCard').classList.contains('is-saving'),
        };
    })()`);
}

async function resolvePersistence(browserWindow, value) {
    const payload = await browserWindow.webContents.executeJavaScript(
        `window.__resolvePersonalityRequest(${JSON.stringify(value)})`
    );
    assert.equal(payload.preset, value, `persisted request for ${value}`);
    await waitFor(
        browserWindow,
        `!document.getElementById('personalitySettingsCard').classList.contains('is-saving')`,
        `Personality save did not settle for ${value}`,
    );
}

async function exercisePersistedPreset(browserWindow, value, label, keyboard = false) {
    const selection = keyboard
        ? await chooseStandardWithKeyboard(browserWindow)
        : await chooseWithPointer(browserWindow, value);
    if (keyboard) {
        assert.equal(selection.focusedValue, value, 'keyboard navigation focuses Standard');
    }
    assert.equal(selection.pendingCount, 1, `${value}: one persistence request`);
    assert.equal(selection.saving, true, `${value}: pending state is visible`);
    await assertAccessiblePreset(browserWindow, value, label, `${value} before persistence`);
    await resolvePersistence(browserWindow, value);
    await assertAccessiblePreset(browserWindow, value, label, `${value} after persistence`);
}

async function run() {
    const browserWindow = new BrowserWindow({
        show: false,
        webPreferences: {
            sandbox: true,
        },
    });
    await browserWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(fixtureHtml())}`);
    browserWindow.webContents.debugger.attach('1.3');
    await waitFor(
        browserWindow,
        'window.__personalityFixtureReady === true',
        'Personality fixture did not initialize',
    );

    await assertAccessiblePreset(browserWindow, 'none', 'None', 'initial state');
    for (const [index, [value, label]] of PRESETS.slice(1).entries()) {
        await exercisePersistedPreset(browserWindow, value, label, index === 0);
        if (value !== PRESETS.at(-1)[0]) {
            await exercisePersistedPreset(browserWindow, 'none', 'None');
        }
    }

    await browserWindow.webContents.executeJavaScript(`(() => {
        const state = document.getElementById('personalityPresetSelect').__customSelectState;
        const labels = ${JSON.stringify(GERMAN_LABELS)};
        Array.from(state.nativeSelect.options).forEach((option) => {
            option.textContent = labels[option.value];
        });
        document.dispatchEvent(new Event('i18n:updated'));
    })()`);
    for (const [value] of PRESETS) {
        await browserWindow.webContents.executeJavaScript(
            `window.setCustomSelectValue('personality_preset', ${JSON.stringify(value)})`
        );
        await assertAccessiblePreset(browserWindow, value, GERMAN_LABELS[value], `${value} translated`);
    }

    await browserWindow.webContents.executeJavaScript(`(() => {
        const root = document.getElementById('personalityPresetSelect');
        const labels = ${JSON.stringify(GERMAN_LABELS)};
        window.refreshCustomSelect(root, {
            options: ${JSON.stringify(PRESETS.map(([value]) => ({ value })))}.map((option) => ({
                ...option,
                label: labels[option.value],
            })),
            value: 'professional',
        });
        window.initUserPersonalitySettings({
            personality_preset: 'professional',
            personality_custom_instruction: '',
        });
    })()`);
    await assertAccessiblePreset(browserWindow, 'professional', GERMAN_LABELS.professional, 'reinitialized');

    browserWindow.webContents.debugger.detach();
    browserWindow.destroy();
    process.stdout.write(`${JSON.stringify({ presets: PRESETS.length, status: 'passed' })}\n`);
}

app.whenReady()
    .then(run)
    .then(() => app.quit())
    .catch((error) => {
        process.stderr.write(`${error.stack || error}\n`);
        app.exit(1);
    });
