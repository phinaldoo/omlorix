const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const chatDirectory = __dirname;
const frontendDirectory = path.join(chatDirectory, '..', '..');
const skillsSource = fs.readFileSync(path.join(chatDirectory, 'skills.js'), 'utf8');
const formValidationSource = fs.readFileSync(
    path.join(frontendDirectory, 'js', 'common', 'formValidation.js'),
    'utf8',
);

function createClassList() {
    const values = new Set();
    return {
        add(...names) { names.forEach(name => values.add(name)); },
        remove(...names) { names.forEach(name => values.delete(name)); },
        contains(name) { return values.has(name); },
        toggle(name, force) {
            if (force === undefined ? !values.has(name) : force) values.add(name);
            else values.delete(name);
        },
    };
}

function createElement(value = '') {
    const attributes = new Map();
    const listeners = new Map();
    const group = { classList: createClassList() };
    return {
        value,
        textContent: '',
        hidden: false,
        disabled: false,
        classList: createClassList(),
        focusCount: 0,
        addEventListener(type, listener) { listeners.set(type, listener); },
        dispatch(type) { listeners.get(type)?.({ target: this }); },
        closest(selector) { return selector === '.projects-create-input-group' ? group : null; },
        focus() { this.focusCount += 1; },
        scrollIntoView() {},
        setAttribute(name, valueToSet) { attributes.set(name, String(valueToSet)); },
        getAttribute(name) { return attributes.get(name) ?? null; },
        removeAttribute(name) { attributes.delete(name); },
    };
}

function loadSkillsHarness() {
    const elements = {
        skillNameInput: createElement('metadata-validation'),
        skillDescriptionInput: createElement('Metadata validation test'),
        skillContentInput: createElement('Use these test instructions.'),
        skillCompatibilityInput: createElement(''),
        skillLicenseInput: createElement(''),
        skillMetadataInput: createElement(''),
        skillNameError: createElement(),
        skillDescriptionError: createElement(),
        skillContentError: createElement(),
        skillMetadataError: createElement(),
        confirmCreateSkillBtn: createElement(),
        skillEditTitleInput: createElement('Metadata validation'),
        skillEditContentInput: createElement('Use these edited test instructions.'),
        skillEditCompatibilityInput: createElement(''),
        skillEditLicenseInput: createElement(''),
        skillEditMetadataInput: createElement(''),
        skillEditTitleError: createElement(),
        skillEditContentError: createElement(),
        skillEditMetadataError: createElement(),
        saveSkillChangesBtn: createElement(),
    };
    const requests = [];
    const documentListeners = new Map();
    const picker = {
        bind() {},
        render() {},
        updatePreview() {},
        setOpen() {},
        serialize() { return '{"preset":"tool","color":"#000000"}'; },
    };
    const document = {
        readyState: 'loading',
        documentElement: { lang: 'en' },
        getElementById(id) { return elements[id] || null; },
        querySelectorAll() { return []; },
        addEventListener(type, listener) { documentListeners.set(type, listener); },
    };
    const window = {
        document,
        WorkspaceIconUtils: {
            WORKSPACE_ICON_COLORS: [{ hex: '#000000' }],
            getWorkspaceIconOptions() { return [{ id: 'tool' }]; },
            createWorkspaceIconPicker() { return picker; },
            resolveWorkspaceStoredIcon() { return { iconId: 'tool', color: '#000000' }; },
        },
        formatTranslation(key, fallback) {
            const translations = {
                workspace_skills_validation_metadata_invalid: 'Invalid JSON in metadata field',
                workspace_skills_validation_metadata_object: 'Metadata must be a JSON object',
            };
            return translations[key] || fallback;
        },
        async authedFetch(url, init) {
            requests.push({ url, init });
            return {
                ok: true,
                headers: { get() { return null; } },
                async json() { return { id: 'skill-1' }; },
            };
        },
        dispatchEvent() {},
    };
    const context = vm.createContext({
        window,
        document,
        CustomEvent: class CustomEvent {
            constructor(type, options) {
                this.type = type;
                this.detail = options?.detail;
            }
        },
        Intl,
        JSON,
        console,
    });
    vm.runInContext(formValidationSource, context, { filename: 'formValidation.js' });
    vm.runInContext(skillsSource, context, { filename: 'skills.js' });
    window.SkillsManager.init();
    return { elements, requests, window };
}

test('create metadata validation is visible, accessible, and clears before a corrected submission', async () => {
    const { elements, requests, window } = loadSkillsHarness();
    const input = elements.skillMetadataInput;
    const error = elements.skillMetadataError;

    input.value = '[';
    await window.SkillsManager.handleCreate();

    assert.equal(requests.length, 0);
    assert.equal(input.getAttribute('aria-invalid'), 'true');
    assert.equal(input.focusCount, 1);
    assert.equal(error.hidden, false);
    assert.equal(error.getAttribute('aria-hidden'), 'false');
    assert.equal(error.textContent, 'Invalid JSON in metadata field');
    assert.equal(error.classList.contains('visible'), true);

    input.value = 'null';
    input.dispatch('input');
    await window.SkillsManager.handleCreate();

    assert.equal(requests.length, 0);
    assert.equal(error.textContent, 'Metadata must be a JSON object');
    assert.equal(error.getAttribute('data-i18n'), 'workspace_skills_validation_metadata_object');

    input.value = '{"source":"manual"}';
    input.dispatch('input');
    assert.equal(input.getAttribute('aria-invalid'), 'false');
    assert.equal(error.hidden, true);
    assert.equal(error.getAttribute('aria-hidden'), 'true');

    await window.SkillsManager.handleCreate();

    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/v1/skills');
    assert.equal(requests[0].init.method, 'POST');
    assert.deepEqual(JSON.parse(requests[0].init.body).metadata, { source: 'manual' });
});

test('edit metadata validation rejects arrays and allows correction and resubmission', async () => {
    const { elements, requests, window } = loadSkillsHarness();
    const input = elements.skillEditMetadataInput;
    const error = elements.skillEditMetadataError;
    window.SkillsState.activeSkillContext = {
        id: 'skill-1',
        is_admin_skill: false,
        is_subscribed: false,
    };

    input.value = '[]';
    await window.SkillsManager.handleUpdate();

    assert.equal(requests.length, 0);
    assert.equal(input.getAttribute('aria-invalid'), 'true');
    assert.equal(error.textContent, 'Metadata must be a JSON object');
    assert.equal(error.hidden, false);

    input.value = '{"version":2}';
    input.dispatch('input');
    await window.SkillsManager.handleUpdate();

    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/v1/skills/skill-1');
    assert.equal(requests[0].init.method, 'PATCH');
    assert.deepEqual(JSON.parse(requests[0].init.body).metadata, { version: 2 });
});

test('metadata validation messages exist in every supported locale', () => {
    const i18nDirectory = path.join(frontendDirectory, 'i18n');
    const locales = fs.readdirSync(i18nDirectory)
        .filter(locale => fs.existsSync(path.join(i18nDirectory, locale, 'index.json')));

    assert.equal(locales.length, 11);
    for (const locale of locales) {
        const translations = JSON.parse(
            fs.readFileSync(path.join(i18nDirectory, locale, 'index.json'), 'utf8'),
        );
        for (const key of [
            'workspace_skills_validation_metadata_invalid',
            'workspace_skills_validation_metadata_object',
        ]) {
            assert.equal(typeof translations[key], 'string', `${locale} is missing ${key}`);
            assert.ok(translations[key].trim(), `${locale} has an empty ${key}`);
        }
    }
});
