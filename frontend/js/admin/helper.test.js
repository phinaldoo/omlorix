const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeClassList {
    constructor(element) {
        this.element = element;
    }

    _tokens() {
        return new Set(String(this.element.className || '').split(/\s+/).filter(Boolean));
    }

    add(...tokens) {
        const next = this._tokens();
        tokens.forEach((token) => next.add(token));
        this.element.className = Array.from(next).join(' ');
    }

    remove(...tokens) {
        const next = this._tokens();
        tokens.forEach((token) => next.delete(token));
        this.element.className = Array.from(next).join(' ');
    }

    toggle(token, force) {
        const next = this._tokens();
        const shouldAdd = force === undefined ? !next.has(token) : Boolean(force);
        if (shouldAdd) {
            next.add(token);
        } else {
            next.delete(token);
        }
        this.element.className = Array.from(next).join(' ');
        return shouldAdd;
    }

    contains(token) {
        return this._tokens().has(token);
    }
}

class FakeElement {
    constructor(tagName) {
        this.tagName = String(tagName || '').toUpperCase();
        this.children = [];
        this.attributes = {};
        this.dataset = {};
        this.style = {};
        this.listeners = {};
        this.parentNode = null;
        this.value = '';
        this.name = '';
        this.type = '';
        this._innerHTML = '';
        this._textContent = '';
        this.classList = new FakeClassList(this);
    }

    set className(value) {
        this.attributes.class = String(value || '');
    }

    get className() {
        return this.attributes.class || '';
    }

    set textContent(value) {
        this._textContent = String(value ?? '');
        this.children = [];
        this._innerHTML = '';
    }

    get textContent() {
        return this._textContent;
    }

    set innerHTML(value) {
        this._innerHTML = String(value ?? '');
        this.children = [];
        this._textContent = '';
    }

    get innerHTML() {
        return this._innerHTML;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    getAttribute(name) {
        return this.attributes[name];
    }

    removeAttribute(name) {
        delete this.attributes[name];
    }

    appendChild(child) {
        this.children.push(child);
        child.parentNode = this;
        return child;
    }

    removeChild(child) {
        this.children = this.children.filter((candidate) => candidate !== child);
        child.parentNode = null;
        return child;
    }

    append(...children) {
        children.forEach((child) => this.appendChild(child));
    }

    replaceChildren(...children) {
        this.children = [];
        this._textContent = '';
        this._innerHTML = '';
        this.append(...children);
    }

    remove() {
        if (!this.parentNode) {
            return;
        }
        this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
        this.parentNode = null;
    }

    focus() {
        this.focused = true;
    }

    scrollIntoView() {}

    addEventListener(type, handler) {
        this.listeners[type] = this.listeners[type] || [];
        this.listeners[type].push(handler);
    }

    dispatchEvent(event) {
        event.target = event.target || this;
        (this.listeners[event.type] || []).forEach((handler) => handler(event));
        return true;
    }

    closest(selector) {
        let current = this;
        while (current) {
            if (matchesSelector(current, selector)) {
                return current;
            }
            current = current.parentNode;
        }
        return null;
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
        const selectors = selector.split(',').map((entry) => entry.trim()).filter(Boolean);
        const results = [];
        const visit = (node) => {
            if (selectors.some((candidate) => matchesSelector(node, candidate))) {
                results.push(node);
            }
            (node.children || []).forEach(visit);
        };
        (this.children || []).forEach(visit);
        return results;
    }

    get options() {
        return this.tagName === 'SELECT' ? (this._options || this.children) : undefined;
    }

    set options(value) {
        this._options = value;
    }

    get selectedOptions() {
        return this._selectedOptions || (this.tagName === 'SELECT'
            ? this.children.filter((option) => option.selected)
            : undefined);
    }

    set selectedOptions(value) {
        this._selectedOptions = value;
    }
}

function matchesSelector(element, selector) {
    if (!element || !selector) {
        return false;
    }
    if (selector.startsWith('.')) {
        return element.classList.contains(selector.slice(1));
    }
    return element.tagName.toLowerCase() === selector.toLowerCase();
}

function findPresetButton(root, presetKey) {
    if (root.tagName === 'BUTTON' && root.dataset?.preset === presetKey) {
        return root;
    }
    for (const child of root.children || []) {
        const match = findPresetButton(child, presetKey);
        if (match) {
            return match;
        }
    }
    return null;
}

function createHarness({ getTranslation, includeIcons = true } = {}) {
    const iconStub = new Proxy({}, {
        get: (_target, property) => `<svg data-icon="${String(property)}"></svg>`,
    });
    const document = {
        body: new FakeElement('body'),
        createElement(tagName) {
            return new FakeElement(tagName);
        },
        getElementById() {
            return null;
        },
        querySelector() {
            return null;
        },
        querySelectorAll() {
            return [];
        },
        addEventListener() {},
        removeEventListener() {},
    };
    const context = {
        console,
        document,
        URLSearchParams,
        setTimeout(handler) {
            handler();
        },
        Event: class Event {
            constructor(type, options = {}) {
                this.type = type;
                this.bubbles = Boolean(options.bubbles);
            }
        },
        CustomEvent: class CustomEvent {
            constructor(type, options = {}) {
                this.type = type;
                this.detail = options.detail;
            }
        },
        window: {
            getTranslation: getTranslation || ((_key, fallback) => fallback),
        },
    };
    if (includeIcons) {
        context.window.Icons = iconStub;
        context.Icons = iconStub;
    }
    context.globalThis = context;
    const source = readFrontendSource(path.join(__dirname, 'helper.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'helper.js' });
    return context;
}

test('admin single select initializes without a preloaded Icons global', () => {
    const context = createHarness({ includeIcons: false });
    const select = new FakeElement('select');
    const placeholder = new FakeElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Select Language';
    placeholder.selected = true;
    const english = new FakeElement('option');
    english.value = 'en';
    english.textContent = 'English';
    select.options = [placeholder, english];
    select.selectedOptions = [placeholder];

    const meta = context.window.initializeAdminSingleSelect(select, {
        key: 'language',
        placeholder: 'Select Language',
    });

    assert.ok(meta.wrapper.classList.contains('admin-select'));
    assert.equal(meta.wrapper.querySelector('.admin-select-caret').innerHTML, '');
});

test('field validation highlights, announces, and clears a missing required control without a toast', () => {
    const context = createHarness();
    let notificationCount = 0;
    context.notifyError = () => {
        notificationCount += 1;
    };
    const row = new FakeElement('div');
    row.classList.add('settings-row');
    const wrapper = new FakeElement('div');
    wrapper.classList.add('settings-row-control');
    const input = new FakeElement('input');
    wrapper.appendChild(input);
    row.appendChild(wrapper);

    const valid = context.window.FieldValidation.validate(
        [{ field: { key: 'api_key', label: 'API key', required: true }, control: input }],
        { notify: false },
    );

    assert.equal(valid, false);
    assert.equal(notificationCount, 0);
    assert.ok(row.classList.contains('has-error'));
    assert.ok(input.classList.contains('field-error'));
    assert.equal(input.getAttribute('aria-invalid'), 'true');
    assert.equal(wrapper.querySelector('.field-error-message').getAttribute('role'), 'alert');

    context.window.FieldValidation.clearFieldError(row);
    assert.equal(row.classList.contains('has-error'), false);
    assert.equal(input.classList.contains('field-error'), false);
    assert.equal(input.getAttribute('aria-invalid'), undefined);
});

test('schema JSON fields render effective values and submit parsed objects', () => {
    const context = createHarness();
    const field = {
        key: 'example_json',
        label: 'Example JSON',
        type: 'json',
        rows: 8,
    };
    const value = [{ name: 'example' }];

    const rendered = context.createFieldControl(field, { value });

    assert.equal(rendered.control.tagName, 'TEXTAREA');
    assert.equal(rendered.control.rows, 8);
    assert.equal(rendered.control.getAttribute('aria-label'), field.label);
    assert.equal(rendered.control.value, JSON.stringify(value, null, 2));
    assert.equal(
        JSON.stringify(context.normalizeFieldValue(field, rendered.control.value)),
        JSON.stringify(value),
    );
    assert.throws(
        () => context.normalizeFieldValue(field, '{not valid json'),
        /valid JSON/i,
    );

    const submitted = [];
    const row = context.createSettingsRow(field, value, {
        onSubmit: (nextValue) => submitted.push(nextValue),
    });
    row.controller.control.value = '[{"name":"updated"}]';
    row.controller.control.dispatchEvent(new context.Event('input'));
    assert.equal(submitted.length, 0, 'partial JSON must not autosave while typing');
    row.controller.control.dispatchEvent(new context.Event('blur'));
    assert.equal(
        JSON.stringify(submitted),
        JSON.stringify([[{ name: 'updated' }]]),
    );
});

test('schema JSON fields preserve the root type of their default when the value is missing', () => {
    const context = createHarness();
    const field = {
        key: 'example_json',
        label: 'Example JSON',
        type: 'json',
        default: [],
    };

    const rendered = context.createFieldControl(field, { value: null });

    assert.equal(rendered.control.value, '[]');
});

test('admin single select preserves its accessible name and can render an empty-value option', () => {
    const context = createHarness();
    const select = new FakeElement('select');
    select.setAttribute('aria-labelledby', 'country-filter-label');
    const allOption = new FakeElement('option');
    allOption.value = '';
    allOption.textContent = 'All';
    allOption.selected = true;
    select.options = [allOption];
    select.selectedOptions = [allOption];

    const meta = context.window.initializeAdminSingleSelect(select, {
        key: 'country_filter',
        emptyValueIsOption: true,
    });
    const trigger = meta.wrapper.querySelector('.admin-select-trigger');

    assert.equal(trigger.getAttribute('aria-labelledby'), 'country-filter-label');
    assert.equal(trigger.classList.contains('placeholder'), false);
    assert.equal(trigger.querySelector('.admin-select-value').textContent, 'All');
});

test('admin single select refreshes its accessible name after localization', () => {
    const context = createHarness();
    const select = new FakeElement('select');
    select.setAttribute('aria-label', 'Notice behavior');
    const option = new FakeElement('option');
    option.value = 'none';
    option.textContent = 'No notice';
    option.selected = true;
    select.options = [option];
    select.selectedOptions = [option];

    const meta = context.window.initializeAdminSingleSelect(select, {
        key: 'privacy-policy-notice-mode',
    });
    assert.equal(meta.wrapper.querySelector('.admin-select-trigger').getAttribute('aria-label'), 'Notice behavior');

    select.setAttribute('aria-label', 'Hinweisverhalten');
    meta.syncFromSelect();

    assert.equal(meta.wrapper.querySelector('.admin-select-trigger').getAttribute('aria-label'), 'Hinweisverhalten');
});

test('admin single select keeps its accessible value tied to the committed option', () => {
    const context = createHarness();
    const select = new FakeElement('select');
    select.id = 'personality-preset-native';
    const none = new FakeElement('option');
    none.value = 'none';
    none.textContent = 'None';
    none.selected = true;
    const standard = new FakeElement('option');
    standard.value = 'standard';
    standard.textContent = 'Standard';
    select.options = [none, standard];
    select.value = 'none';
    select.selectedOptions = [none];

    const meta = context.window.initializeAdminSingleSelect(select, {
        key: 'personality_preset',
    });
    const trigger = meta.wrapper.querySelector('.admin-select-trigger');
    const optionButtons = meta.wrapper.querySelectorAll('.admin-select-option');

    assert.equal(trigger.getAttribute('aria-activedescendant'), optionButtons[0].id);
    assert.equal(optionButtons[0].getAttribute('aria-selected'), 'true');

    none.selected = false;
    standard.selected = true;
    select.value = 'standard';
    select.selectedOptions = [standard];
    meta.syncFromSelect();

    assert.equal(trigger.querySelector('.admin-select-value').textContent, 'Standard');
    assert.equal(trigger.getAttribute('aria-activedescendant'), optionButtons[1].id);
    assert.equal(optionButtons[0].getAttribute('aria-selected'), 'false');
    assert.equal(optionButtons[1].getAttribute('aria-selected'), 'true');
});

test('timezone search exposes live translation metadata for its placeholder and accessible name', () => {
    const context = createHarness({
        getTranslation: (key, fallback) => key === 'admin_search_placeholder'
            ? 'Suchen...'
            : fallback,
    });
    const select = new FakeElement('select');
    const berlin = new FakeElement('option');
    berlin.value = 'Europe/Berlin';
    berlin.textContent = 'Europe/Berlin (UTC+2)';
    berlin.selected = true;
    select.options = [berlin];
    select.selectedOptions = [berlin];

    const meta = context.window.initializeAdminSingleSelect(select, { key: 'timezone' });
    const searchInput = meta.wrapper.querySelector('.admin-select-search-input');

    assert.ok(searchInput, 'timezone selects should be searchable');
    assert.equal(searchInput.placeholder, 'Suchen...');
    assert.equal(searchInput.getAttribute('aria-label'), 'Suchen...');
    assert.equal(
        searchInput.getAttribute('data-i18n-attr'),
        'placeholder:admin_search_placeholder;aria-label:admin_search_placeholder',
    );
});

test('explicit select search placeholders are not replaced by the default translation key', () => {
    const context = createHarness();
    const select = new FakeElement('select');
    const option = new FakeElement('option');
    option.value = 'member';
    option.textContent = 'Member';
    option.selected = true;
    select.options = [option];
    select.selectedOptions = [option];

    const meta = context.window.initializeAdminSingleSelect(select, {
        key: 'members',
        searchable: true,
        search: { placeholder: 'Filter members' },
    });
    const searchInput = meta.wrapper.querySelector('.admin-select-search-input');

    assert.equal(searchInput.placeholder, 'Filter members');
    assert.equal(searchInput.getAttribute('aria-label'), 'Filter members');
    assert.equal(searchInput.getAttribute('data-i18n-attr'), undefined);
});

test('schema single select keeps an unset provider-backed model on its placeholder', () => {
    const context = createHarness();
    const field = {
        key: 'live_transcription_model',
        label: 'Live transcription model',
        type: 'select',
        placeholder: 'Select a live transcription model',
        options: [
            { value: 'gpt-live-transcribe', label: 'gpt-live-transcribe' },
            { value: 'grok-transcribe', label: 'grok-transcribe' },
        ],
    };

    const rendered = context.createFieldControl(field, { value: null });
    const options = Array.from(rendered.control.options);
    const trigger = rendered.root.querySelector('.admin-select-trigger');

    assert.equal(options.length, 3);
    assert.equal(options[0].value, '');
    assert.equal(options[0].textContent, field.placeholder);
    assert.equal(rendered.control.value, '');
    assert.equal(rendered.control.dataset.placeholder, field.placeholder);
    assert.ok(trigger.classList.contains('placeholder'));
    assert.equal(
        trigger.querySelector('.admin-select-value').textContent,
        field.placeholder,
    );
});

test('schema single select does not duplicate an explicit empty option', () => {
    const context = createHarness();
    const field = {
        key: 'provider_filter',
        type: 'select',
        placeholder: 'All providers',
        emptyValueIsOption: true,
        options: [
            { value: '', label: 'All providers' },
            { value: 'openai', label: 'OpenAI' },
        ],
    };

    const rendered = context.createFieldControl(field, { value: '' });

    assert.equal(
        Array.from(rendered.control.options).filter((option) => option.value === '').length,
        1,
    );
});

test('schema single select returns to its placeholder when a saved value is cleared', () => {
    const context = createHarness();
    const field = {
        key: 'realtime_model',
        type: 'select',
        placeholder: 'Select a realtime model',
        options: [{ value: 'gpt-realtime', label: 'gpt-realtime' }],
    };
    const rendered = context.createFieldControl(field, { value: 'gpt-realtime' });

    assert.equal(rendered.control.value, 'gpt-realtime');

    context.applyControlValue(rendered.control, field, null);

    assert.equal(rendered.control.value, '');
    assert.ok(
        rendered.root.querySelector('.admin-select-trigger').classList.contains('placeholder'),
    );
});

test('admin required single select exposes valid combobox and listbox semantics', () => {
    const context = createHarness();
    const select = new FakeElement('select');
    select.id = 'meeting-legal-basis-native';
    select.required = true;
    const placeholder = new FakeElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Choose a legal basis';
    placeholder.selected = true;
    select.options = [placeholder];
    select.selectedOptions = [placeholder];

    const meta = context.window.initializeAdminSingleSelect(select, {
        key: 'meeting_legal_basis',
    });
    const trigger = meta.wrapper.querySelector('.admin-select-trigger');
    const listbox = meta.wrapper.querySelector('.admin-select-menu');

    assert.equal(trigger.getAttribute('role'), 'combobox');
    assert.equal(trigger.getAttribute('aria-required'), 'true');
    assert.equal(trigger.getAttribute('aria-controls'), listbox.id);
    assert.equal(listbox.getAttribute('role'), 'listbox');
});

test('remote admin multi-select keeps selected values while loading a bounded page', async () => {
    const context = createHarness();
    const requests = [];
    context.window.authedFetch = async (url) => {
        requests.push(url);
        return {
            ok: true,
            async json() {
                return {
                    options: [{ value: 'candidate', label: 'Candidate (candidate@example.com)' }],
                    offset: 0,
                    limit: 100,
                    total: 1,
                    has_more: false,
                };
            },
        };
    };

    const select = new FakeElement('select');
    Object.defineProperty(select, 'options', {
        get: () => select.children,
    });
    Object.defineProperty(select, 'selectedOptions', {
        get: () => select.children.filter((option) => option.selected),
    });
    const selected = new FakeElement('option');
    selected.value = 'owner';
    selected.textContent = 'Owner (owner@example.com)';
    selected.selected = true;
    select.appendChild(selected);

    const meta = context.window.initializeAdminMultiSelect(select, {
        key: 'owner_user_ids',
        multiple: true,
        searchable: true,
        metadata: {
            remote_options: {
                url: '/api/v1/groups/manager-candidates',
                limit: 100,
            },
        },
    });
    meta.openMenu();
    await new Promise((resolve) => setImmediate(resolve));

    assert.match(requests[0], /\/api\/v1\/groups\/manager-candidates\?offset=0&limit=100/);
    assert.deepEqual(select.children.map((option) => option.value), ['owner', 'candidate']);
    assert.equal(select.children[0].selected, true);
    assert.equal(meta.wrapper.querySelector('.admin-multiselect-actions').hidden, true);
});

test('structured IP location setup errors become translated notifyError messages', async () => {
    const translations = {
        security_ip_country_provider_api_key_missing_error:
            'Configure the {provider} credential before enabling country rules.',
    };
    const context = createHarness({
        getTranslation: (key, fallback) => translations[key] || fallback,
    });
    let notification = '';
    context.notifyError = (message) => {
        notification = message;
    };
    context.window.authedFetch = async () => ({
        ok: false,
        async json() {
            return {
                detail: {
                    code: 'ip_country_provider_api_key_missing',
                    message: 'Backend fallback',
                    provider: 'IP Info',
                },
            };
        },
    });

    await context.fetchAdminJson('/api/v1/admin/values/?page=security');

    assert.equal(
        notification,
        'Configure the IP Info credential before enabling country rules.'
    );
});

test('admin settings validation errors use the shared translated message', async () => {
    const context = createHarness({
        getTranslation: (key, fallback) => key === 'admin_validation_failed'
            ? 'Translated validation failure.'
            : fallback,
    });
    let notification = '';
    context.notifyError = (message) => {
        notification = message;
    };
    context.window.authedFetch = async () => ({
        ok: false,
        async json() {
            return {
                detail: {
                    code: 'admin_settings_validation_failed',
                    message: 'Backend fallback',
                },
            };
        },
    });

    await context.fetchAdminJson('/api/v1/admin/values/?page=models');

    assert.equal(notification, 'Translated validation failure.');
});

test('invalid country codes become translated notifyError messages', async () => {
    const translations = {
        security_ip_country_code_invalid_error:
            'Country code {countryCode} is invalid. Enter DE or US.',
    };
    const context = createHarness({
        getTranslation: (key, fallback) => translations[key] || fallback,
    });
    let notification = '';
    context.notifyError = (message) => {
        notification = message;
    };
    context.window.authedFetch = async () => ({
        ok: false,
        async json() {
            return {
                detail: {
                    code: 'ip_country_code_invalid',
                    message: 'Backend fallback',
                    country_code: 'Germany',
                },
            };
        },
    });

    await context.fetchAdminJson('/api/v1/admin/values/?page=security');

    assert.equal(
        notification,
        'Country code Germany is invalid. Enter DE or US.'
    );
});

test('invalid IP addresses become translated notifyError messages', async () => {
    const translations = {
        security_ip_address_invalid_error:
            'IP address {ipAddress} is invalid. Enter a valid IPv4 or IPv6 address.',
    };
    const context = createHarness({
        getTranslation: (key, fallback) => translations[key] || fallback,
    });
    let notification = '';
    context.notifyError = (message) => {
        notification = message;
    };
    context.window.authedFetch = async () => ({
        ok: false,
        async json() {
            return {
                detail: {
                    code: 'ip_address_invalid',
                    message: 'Backend fallback',
                    ip_address: '192.168.1.999',
                },
            };
        },
    });

    await context.fetchAdminJson('/api/v1/admin/values/?page=security');

    assert.equal(
        notification,
        'IP address 192.168.1.999 is invalid. Enter a valid IPv4 or IPv6 address.'
    );
});

test('public URL lists use translated placeholders and ordered controls', () => {
    const context = createHarness({
        getTranslation: (key, fallback) => key === 'schema_general_public_url_placeholder'
            ? 'Translated public URL example'
            : fallback,
    });

    const { root, control } = context.createFieldControl(
        {
            key: 'public_url',
            type: 'string_list',
            label: 'Public URLs',
            placeholder: 'E.g. https://chat.example.com',
            i18n_placeholder: 'schema_general_public_url_placeholder',
            metadata: {
                ordered: true,
                primary_first: true,
            },
        },
        {
            value: [
                'https://primary.example',
                'https://secondary.example',
            ],
        }
    );

    assert.equal(root.querySelector('.keyword-tags-input').placeholder, 'Translated public URL example');
    assert.equal(control.dataset.orderedList, 'true');

    const initialRows = root.querySelectorAll('.keyword-tag');
    assert.equal(initialRows.length, 2);
    assert.equal(
        initialRows[0].querySelector('.keyword-tag-primary').textContent,
        'Primary'
    );
    assert.equal(
        initialRows[0].querySelector('.keyword-tag-move-up').disabled,
        true
    );
    assert.equal(
        initialRows[1].querySelector('.keyword-tag-move-down').disabled,
        true
    );

    let savedOrder = null;
    control.addEventListener('keywordschange', (event) => {
        savedOrder = event.detail.keywords;
    });
    initialRows[1]
        .querySelector('.keyword-tag-move-up')
        .dispatchEvent(new context.Event('click'));

    assert.deepEqual(
        JSON.parse(control.dataset.keywordTags),
        ['https://secondary.example', 'https://primary.example']
    );
    assert.deepEqual(
        Array.from(savedOrder),
        ['https://secondary.example', 'https://primary.example']
    );
    const reorderedRows = root.querySelectorAll('.keyword-tag');
    assert.equal(
        reorderedRows[0].querySelector('.keyword-tag-value').textContent,
        'https://secondary.example'
    );
    assert.equal(
        reorderedRows[0].querySelector('.keyword-tag-primary').textContent,
        'Primary'
    );
});

test('night block preset switches access windows to blocklist mode', () => {
    const context = createHarness();
    const form = new FakeElement('form');
    const modeSelect = new FakeElement('select');
    modeSelect.name = 'settings.access_windows.mode';
    modeSelect.value = 'allowlist';
    let modeChangeCount = 0;
    modeSelect.addEventListener('change', () => {
        modeChangeCount += 1;
    });
    form.appendChild(modeSelect);

    const { root, control } = context.createFieldControl(
        {
            key: 'settings.access_windows.rules',
            type: 'access_rules',
            label: 'Access rules',
        },
        { value: [] }
    );
    control.name = 'settings.access_windows.rules';
    form.appendChild(root);

    const nightBlockButton = findPresetButton(root, 'night_block');
    assert.ok(nightBlockButton, 'expected the Night Block preset button to render');

    nightBlockButton.dispatchEvent(new context.Event('click'));

    assert.equal(modeSelect.value, 'blocklist');
    assert.equal(modeChangeCount, 1);
    assert.deepEqual(JSON.parse(control.dataset.accessRules), [
        {
            start: '22:00',
            end: '06:00',
            days: [0, 1, 2, 3, 4, 5, 6],
            label: 'Night hours (block)',
        },
    ]);
});

test('LLM access custom permissions use boolean map item controls', () => {
    const context = createHarness();
    const { root, control } = context.createFieldControl(
        {
            key: 'allow_llm_to_access_personal_information',
            type: 'object',
            label: 'LLM personal info access',
            preset_value: 'custom',
        },
        {
            value: {
                first_name: true,
                language: false,
                country: false,
                timezone: false,
                location: false,
            },
        }
    );

    const customItems = root.querySelectorAll('.boolean-map-item');
    const fieldsContainer = root.querySelector('.llm-access-fields');
    const firstNameInput = root.querySelector('.llm-field-input');
    let latestDetail = null;
    control.addEventListener('llmaccesschange', (event) => {
        latestDetail = event.detail;
    });

    assert.equal(customItems.length, 5);
    assert.ok(fieldsContainer.classList.contains('boolean-map-control'));

    firstNameInput.checked = false;
    firstNameInput.dispatchEvent(new context.Event('change'));

    assert.equal(latestDetail.preset, 'custom');
    assert.equal(latestDetail.permissions.first_name, false);
});

test('boolean map settings render and submit as one multi-select', () => {
    const context = createHarness();
    const field = {
        key: 'sidebar_button_visibility',
        type: 'boolean_map',
        label: 'Sidebar Button Visibility',
        default: {
            create_chat: true,
            search_chats: true,
            workspace: true,
            automations: true,
            projects: true,
        },
        metadata: {
            items: [
                { key: 'create_chat', label: 'Create Chat' },
                { key: 'search_chats', label: 'Search Chats' },
                { key: 'workspace', label: 'Workspace' },
                { key: 'automations', label: 'Automations' },
                { key: 'projects', label: 'Projects' },
            ],
        },
    };

    const { root, control } = context.createFieldControl(field, {
        value: {
            create_chat: true,
            search_chats: false,
            workspace: true,
            automations: true,
            projects: false,
        },
    });

    assert.equal(control.tagName, 'SELECT');
    assert.equal(control.multiple, true);
    assert.ok(root.classList.contains('admin-multiselect'));
    assert.equal(root.querySelectorAll('.boolean-map-item').length, 0);
    const menu = root.querySelector('.admin-multiselect-menu');
    assert.equal(menu.hidden, true);
    control._multiSelect.openMenu();
    assert.equal(menu.hidden, false);
    control._multiSelect.closeMenu();
    assert.equal(menu.hidden, true);
    assert.deepEqual(
        Array.from(control.selectedOptions, (option) => option.value),
        ['create_chat', 'workspace', 'automations'],
    );

    let submittedValue = null;
    control.addEventListener('booleanmapchange', (event) => {
        submittedValue = event.detail.value;
    });
    control.options.find((option) => option.value === 'projects').selected = true;
    control.dispatchEvent(new context.Event('change', { bubbles: true }));

    assert.equal(submittedValue.search_chats, false);
    assert.equal(submittedValue.projects, true);
    assert.equal(
        JSON.stringify(JSON.parse(control.dataset.booleanMap)),
        JSON.stringify(submittedValue),
    );
});

test('redacted masked password fields treat an empty missing value as unchanged', () => {
    const context = createHarness();
    const field = {
        key: 'smtp_password',
        type: 'string',
        input_type: 'password',
        redact_value: true,
        masked_placeholder: true,
        placeholder: 'Enter SMTP password',
    };

    assert.equal(context.valuesAreEqual(field, '', undefined), true);
    assert.equal(context.valuesAreEqual(field, '', null), true);
    assert.equal(context.valuesAreEqual(field, '', ''), true);
});

test('existing masked password marker overrides the translated entry placeholder', () => {
    const context = createHarness({
        getTranslation(key, fallback) {
            if (key === 'artificial_analysis_key_placeholder') {
                return 'Enter your Artificial Analysis API key';
            }
            return fallback;
        },
    });
    const field = {
        key: 'settings.leaderboard.artificial_analysis_api_key',
        type: 'string',
        input_type: 'password',
        redact_value: true,
        masked_placeholder: true,
        masked_value_set: true,
        placeholder: 'aa-...',
        i18n_placeholder: 'artificial_analysis_key_placeholder',
    };

    const { control } = context.createFieldControl(field, { value: '' });

    assert.equal(control.placeholder, 'aa-...');
    assert.equal(control.value, '');
    assert.equal(context.getMaskedFieldSubmissionMarker(field), 'aa-...');
});

test('redacted masked textarea fields treat an empty missing value as unchanged', () => {
    const context = createHarness();
    const field = {
        key: 'apple_private_key',
        type: 'textarea',
        input_type: 'password',
        redact_value: true,
        masked_placeholder: true,
        placeholder: '-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----',
    };

    assert.equal(context.valuesAreEqual(field, '', undefined), true);
});

test('non-redacted password fields still compare empty missing values as changed', () => {
    const context = createHarness();
    const field = {
        key: 'new_password',
        type: 'string',
        input_type: 'password',
        placeholder: 'Enter password',
    };

    assert.equal(context.valuesAreEqual(field, '', undefined), false);
});

test('service connections action rows resolve translations immediately for client-side admin navigation', () => {
    const context = createHarness({
        getTranslation(key, fallback) {
            if (key === 'service_connections_slide_render_row_desc') {
                return 'Verwalte Renderer-Endpunkte fuer Praesentations-Rendering.';
            }
            if (key === 'service_connections_settings_row_title') {
                return 'Serviceverbindungen';
            }
            if (key === 'service_connections_settings_row_button') {
                return 'Verbindungstabelle oeffnen';
            }
            if (key === 'service_connections_settings_row_button_aria') {
                return 'Serviceverbindungen oeffnen';
            }
            return fallback;
        },
    });
    const mount = new FakeElement('div');

    context.window.renderAdminServiceConnectionsSettingsRow(mount, {
        descriptionKey: 'service_connections_slide_render_row_desc',
        description: 'Manage renderer service endpoints, weights, and availability checks for presentation rendering.',
    });

    const title = mount.querySelector('.settings-row-title');
    const description = mount.querySelector('.settings-row-desc');
    const button = mount.querySelector('.om-button');

    assert.equal(title.textContent, 'Serviceverbindungen');
    assert.equal(description.textContent, 'Verwalte Renderer-Endpunkte fuer Praesentations-Rendering.');
    assert.equal(description.getAttribute('data-i18n'), 'service_connections_slide_render_row_desc');
    assert.equal(button.querySelector('span').textContent, 'Verbindungstabelle oeffnen');
    assert.equal(button.getAttribute('aria-label'), 'Serviceverbindungen oeffnen');
});

test('admin export jobs submit the configured JSON request body', async () => {
    const context = createHarness();
    const requests = [];
    context.window.setButtonLoadingState = () => {};
    context.window.notifySuccess = () => {};
    context.window.authedFetch = async (url, init = {}) => {
        requests.push({ url, init });
        return {
            ok: true,
            async json() {
                return url.includes('?limit=') ? [] : { id: 'job-1', status: 'queued' };
            },
        };
    };

    const controller = context.window.createAdminExportJobsController({
        dom: {
            createButton: new FakeElement('button'),
            list: new FakeElement('div'),
            status: new FakeElement('div'),
        },
        endpoints: {
            list: '/api/v1/admin/users/export/jobs?limit=50',
            buildCreateRequest: () => ({
                url: '/api/v1/admin/users/export/jobs',
                init: {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reason: 'Compliance review' }),
                },
            }),
        },
    });

    await controller.queue();

    assert.equal(requests[0].url, '/api/v1/admin/users/export/jobs');
    assert.equal(requests[0].init.method, 'POST');
    assert.deepEqual(JSON.parse(requests[0].init.body), { reason: 'Compliance review' });
});

test('admin export downloads use one authenticated GET and save its response blob', async () => {
    const context = createHarness();
    const requests = [];
    const archive = new Blob(['admin export']);
    const objectUrls = [];
    const revokedUrls = [];
    context.URL = {
        createObjectURL(blob) {
            assert.equal(blob, archive);
            const url = 'blob:admin-export';
            objectUrls.push(url);
            return url;
        },
        revokeObjectURL(url) {
            revokedUrls.push(url);
        },
    };
    context.window.notifySuccess = () => {};
    context.window.authedFetch = async (url, init = {}) => {
        requests.push({ url, init });
        return {
            ok: true,
            headers: { get: () => 'attachment; filename="admin-users.zip"' },
            async blob() {
                return archive;
            },
        };
    };
    const nativeLink = new FakeElement('a');
    let downloadClicks = 0;
    const originalCreateElement = context.document.createElement;
    context.document.createElement = (tagName) => {
        const element = originalCreateElement(tagName);
        if (String(tagName).toLowerCase() === 'a') {
            element.click = () => {
                downloadClicks += 1;
            };
        }
        return element;
    };

    const controller = context.window.createAdminExportJobsController({
        dom: {},
        endpoints: {
            download: (jobId) => `/api/v1/admin/users/export/jobs/${jobId}/download`,
        },
    });

    await controller.download('job-1', nativeLink);

    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/v1/admin/users/export/jobs/job-1/download');
    assert.equal(requests[0].init.method, 'GET');
    assert.equal(downloadClicks, 1);
    assert.deepEqual(objectUrls, ['blob:admin-export']);
    assert.deepEqual(revokedUrls, ['blob:admin-export']);
    assert.equal(context.document.body.children.length, 0);
    assert.equal(nativeLink.getAttribute('aria-disabled'), undefined);
});
