const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');


const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(__dirname, 'acpModelSelect.js'), 'utf8');
const sendSource = [
    fs.readFileSync(path.join(__dirname, 'sending/send.js'), 'utf8'),
    fs.readFileSync(path.join(__dirname, 'sending/regeneration.js'), 'utf8'),
].join('\n');
const streamSource = fs.readFileSync(path.join(__dirname, 'messages/completion.js'), 'utf8');
const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const styles = fs.readFileSync(path.join(root, 'css/chat/chatBox/chatBox.css'), 'utf8');
const modelSelectStyles = fs.readFileSync(path.join(root, 'css/chat/modelSelect.css'), 'utf8');


function createClassList(element) {
    const names = new Set();
    return {
        add(...values) {
            values.forEach((value) => names.add(value));
        },
        remove(...values) {
            values.forEach((value) => names.delete(value));
        },
        contains(value) {
            return names.has(value);
        },
        toggle(value, force) {
            const enabled = force === undefined ? !names.has(value) : Boolean(force);
            if (enabled) names.add(value);
            else names.delete(value);
            return enabled;
        },
        replaceFromClassName(value) {
            names.clear();
            String(value || '').split(/\s+/).filter(Boolean).forEach((name) => names.add(name));
        },
        toString() {
            return [...names].join(' ');
        },
    };
}


function createElement(id = '', ownerDocument = null) {
    const element = {
        id,
        hidden: false,
        disabled: false,
        type: '',
        title: '',
        tabIndex: 0,
        textContent: '',
        innerHTML: '',
        style: {},
        dataset: {},
        children: [],
        listeners: {},
        attributes: {},
        parentNode: null,
        append(...children) {
            for (const child of children) {
                child.parentNode = this;
                this.children.push(child);
            }
        },
        appendChild(child) {
            this.append(child);
            return child;
        },
        replaceChildren(...children) {
            this.children = [];
            this.append(...children);
        },
        addEventListener(type, listener) {
            const previous = this.listeners[type];
            this.listeners[type] = previous
                ? (event) => {
                    previous(event);
                    listener(event);
                }
                : listener;
        },
        dispatchEvent(event) {
            this.listeners[event.type]?.(event);
        },
        getAttribute(name) {
            return this.attributes[name] || '';
        },
        setAttribute(name, value) {
            this.attributes[name] = String(value);
        },
        removeAttribute(name) {
            delete this.attributes[name];
            if (name === 'title') this.title = '';
        },
        contains(target) {
            if (target === this) return true;
            return this.children.some((child) => child.contains?.(target));
        },
        focus(options) {
            if (ownerDocument) ownerDocument.activeElement = this;
            this.focusOptions = options;
        },
    };
    element.classList = createClassList(element);
    Object.defineProperty(element, 'className', {
        get() {
            return element.classList.toString();
        },
        set(value) {
            element.classList.replaceFromClassName(value);
        },
    });
    return element;
}


function createAcpHarness() {
    const document = {
        readyState: 'complete',
        activeElement: null,
        listeners: {},
        addEventListener(type, listener) {
            this.listeners[type] = listener;
        },
    };
    document.body = createElement('body', document);
    document.body.style.overflow = '';
    const ids = [
        'chatContainer',
        'chatBox',
        'chatBoxAcpSessionControls',
        ...['Model', 'Reasoning', 'Security'].flatMap((name) => [
            `chatBoxAcp${name}Container`,
            `chatBoxAcp${name}Trigger`,
            `chatBoxAcp${name}Value`,
            `chatBoxAcp${name}Menu`,
        ]),
    ];
    const elements = Object.fromEntries(
        ids.map((id) => [id, createElement(id, document)]),
    );
    document.getElementById = (id) => elements[id] || null;
    document.createElement = () => createElement('', document);
    elements.chatContainer.setAttribute('data-chat-id', 'chat-1');
    const requests = [];
    const responses = [
        { ok: true, json: async () => ({ meta: { acp_sessions: {} } }) },
        {
            ok: true,
            json: async () => ({
                session_id: 'session-1',
                current_model_id: 'model-a',
                models: [
                    { id: 'model-a', name: 'Model A', description: 'First model' },
                    { id: 'model-b', name: 'Model B', description: 'Second model' },
                ],
                current_security_level: 'agent',
                security_levels: [
                    { id: 'read-only', name: 'Read-only' },
                    { id: 'agent', name: 'Agent' },
                ],
                current_reasoning_effort: 'medium',
                reasoning_efforts: [
                    { id: 'low', name: 'Low' },
                    { id: 'medium', name: 'Medium' },
                ],
                reasoning_efforts_by_model: {
                    'model-a': ['low', 'medium'],
                    'model-b': ['low'],
                },
            }),
        },
        { ok: true, json: async () => ({ status: 'success' }) },
        { ok: true, json: async () => ({ status: 'success' }) },
    ];
    const window = {
        createDropdownController: ({ trigger, dropdown }) => {
            let open = false;
            const setOpen = (nextOpen, detail = {}) => {
                open = Boolean(nextOpen);
                dropdown.classList.toggle('open', open);
                dropdown.setAttribute('aria-hidden', open ? 'false' : 'true');
                trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
                if (!open && detail.restoreFocus) trigger.focus({ preventScroll: true });
            };
            trigger.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                setOpen(!open, { restoreFocus: open });
            });
            return {
                open: () => setOpen(true),
                close: (detail) => setOpen(false, detail),
                isOpen: () => open,
            };
        },
        Icons: {
            terminal: '<t/>',
            security: '<s/>',
            thinking: '<r/>',
            chevron: '<c/>',
            close: '<x/>',
            check: '<check/>',
        },
        isGenerating: false,
        getSelectedModel: () => ({
            provider_type: 'acp',
            acp_terminal_available: true,
            model_id: 'connection-1',
        }),
        getTranslation: (_key, fallback) => fallback,
        listeners: {},
        addEventListener(type, listener) {
            this.listeners[type] = listener;
        },
        authedFetch: async (url, options = {}) => {
            requests.push({ url, options });
            return responses.shift();
        },
    };
    const context = {
        window,
        document,
        MutationObserver: class {
            observe() {}
        },
        getComputedStyle: () => ({ display: 'block' }),
        clearTimeout() {},
        setTimeout: () => 1,
        console,
    };
    vm.runInNewContext(source, context);
    return {
        document,
        elements,
        requests,
        window,
    };
}


function pointerEvent() {
    return {
        preventDefault() {},
        stopPropagation() {},
    };
}


test('ACP discovery renders and persists all three independent controls', async () => {
    const harness = createAcpHarness();
    const { elements, requests, window } = harness;

    await window.syncAcpModelSelect({ force: true });

    assert.equal(elements.chatBoxAcpSessionControls.hidden, false);
    assert.equal(elements.chatBoxAcpModelValue.textContent, 'Model A');
    assert.equal(elements.chatBoxAcpSecurityValue.textContent, 'Agent');
    assert.equal(elements.chatBoxAcpReasoningValue.textContent, 'Medium');
    assert.equal(elements.chatBoxAcpModelMenu.children.length, 2);
    assert.equal(window.getSelectedAcpSessionId(), 'session-1');

    elements.chatBoxAcpModelMenu.children[1].children[0].listeners.click(pointerEvent());
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(elements.chatBoxAcpModelValue.textContent, 'Model B');
    assert.equal(elements.chatBoxAcpReasoningValue.textContent, 'Low');
    assert.equal(elements.chatBoxAcpReasoningMenu.children.length, 1);

    elements.chatBoxAcpSecurityMenu.children[0].children[0].listeners.click(pointerEvent());
    await new Promise((resolve) => setImmediate(resolve));
    const persisted = JSON.parse(requests.at(-1).options.body);
    assert.equal(persisted.acp_model_id, 'model-b');
    assert.equal(persisted.acp_security_level, 'read-only');
    assert.equal(persisted.acp_reasoning_effort, 'low');

    window.applyAcpConfigUpdate({ current_reasoning_effort: 'low' });
    assert.equal(elements.chatBoxAcpReasoningValue.textContent, 'Low');
});


test('ACP selectors reuse the chatbox Thinking markup and shared styles', () => {
    assert.match(source, /\(model\?\.provider \|\| model\?\.provider_type\) === 'acp'/);
    assert.match(source, /acp_terminal_available === true/);
    assert.match(source, /\/api\/v1\/remote-connections\/acp\/models/);
    assert.match(source, /session_id: savedSessionId/);
    assert.match(source, /window\.getSelectedAcpModelId/);
    assert.match(source, /window\.getSelectedAcpSecurityLevel/);
    assert.match(source, /window\.getSelectedAcpReasoningEffort/);
    assert.match(source, /window\.getSelectedAcpSessionId/);
    assert.match(source, /window\.applyAcpConfigUpdate/);
    assert.match(source, /state\.discoveryPending/);
    assert.match(source, /state\.resolvedRequestVersion !== state\.requestVersion/);
    assert.match(source, /state\.pendingPersistenceBody = body/);
    assert.match(source, /while \(state\.pendingPersistenceBody\)/);
    assert.match(source, /createDropdownController/);
    assert.doesNotMatch(source, /model-select-dropdown|acp-session-select|positionMenu/);

    assert.match(index, /id="chatBoxAcpSessionControls"[\s\S]*hidden/);
    for (const name of ['Model', 'Reasoning', 'Security']) {
        assert.match(
            index,
            new RegExp(`class="om-button" id="chatBoxAcp${name}Trigger"`),
        );
        assert.match(
            index,
            new RegExp(`class="select-dropdown chat-box-thinking-menu" id="chatBoxAcp${name}Menu"`),
        );
    }
    assert.doesNotMatch(
        index.slice(
            index.indexOf('id="chatBoxAcpSessionControls"'),
            index.indexOf('id="chatBoxVoiceButton"'),
        ),
        /admin-select|model-select|acp-session-select|<select/,
    );
    assert.doesNotMatch(styles, /chat-box-acp|acp-session-select/);
    assert.match(styles, /\.chat-box-dropdown \.select-dropdown/);
    assert.match(modelSelectStyles, /\.model-select-dropdown\.open/);
});


test('ACP controls open and close through the shared dropdown controller', async () => {
    const { elements, window } = createAcpHarness();
    await window.syncAcpModelSelect({ force: true });

    elements.chatBoxAcpModelTrigger.listeners.click(pointerEvent());

    assert.equal(elements.chatBoxAcpModelMenu.classList.contains('open'), true);
    assert.equal(elements.chatBoxAcpModelMenu.getAttribute('aria-hidden'), 'false');
    assert.equal(elements.chatBoxAcpModelTrigger.getAttribute('aria-expanded'), 'true');

    elements.chatBoxAcpModelTrigger.listeners.click(pointerEvent());
    assert.equal(elements.chatBoxAcpModelMenu.classList.contains('open'), false);
    assert.equal(elements.chatBoxAcpModelTrigger.getAttribute('aria-expanded'), 'false');
});


test('ACP controls keep the Thinking dropdown behavior on mobile', async () => {
    const { document, elements, window } = createAcpHarness();
    await window.syncAcpModelSelect({ force: true });
    document.body.style.overflow = 'clip';

    elements.chatBoxAcpSecurityTrigger.listeners.click(pointerEvent());

    assert.equal(elements.chatBoxAcpSecurityMenu.classList.contains('open'), true);
    assert.equal(document.body.style.overflow, 'clip');

    elements.chatBoxAcpSecurityTrigger.listeners.click(pointerEvent());
    assert.equal(elements.chatBoxAcpSecurityMenu.classList.contains('open'), false);
    assert.equal(document.body.style.overflow, 'clip');
});


test('ACP controls have no separate positioning or responsive CSS', () => {
    assert.doesNotMatch(source, /scrollIntoView|positionMenu|visualViewport|model-select-dropdown/);
    assert.doesNotMatch(source, /document\.body\.style/);
    assert.doesNotMatch(styles, /chat-box-acp|acp-session-select|@media \(min-width: 769px\)[\s\S]*position: fixed/);
});


test('ACP session controls live in the right-side composer action cluster', () => {
    const controlsIndex = index.indexOf('id="chatBoxAcpSessionControls"');
    const microphoneIndex = index.indexOf('id="chatBoxVoiceButton"');

    assert.match(index, /class="chat-box-bottom-div" id="chatBoxAcpSessionControls"/);
    assert.ok(controlsIndex < microphoneIndex);
    assert.match(styles, /\.chat-box-bottom-div \{[\s\S]*display: flex[\s\S]*gap: 4px/);
});


test('ACP buttons keep the shared Thinking button markup at every viewport size', () => {
    const acpMarkup = index.slice(
        index.indexOf('id="chatBoxAcpSessionControls"'),
        index.indexOf('id="chatBoxVoiceButton"'),
    );
    assert.match(acpMarkup, /class="chat-box-dropdown"/);
    assert.match(acpMarkup, /class="om-button"/);
    assert.match(acpMarkup, /class="select-dropdown chat-box-thinking-menu"/);
    assert.doesNotMatch(styles, /chat-box-acp|acp-session-select|@container[\s\S]*acp/);
});


test('all ACP selections are sent for normal and regenerated turns', () => {
    for (const field of [
        'acp_model_id',
        'acp_session_id',
        'acp_security_level',
        'acp_reasoning_effort',
    ]) {
        const occurrences = sendSource.match(new RegExp(`${field}:`, 'g')) || [];
        assert.ok(occurrences.length >= 2, `${field} is not sent on both paths`);
    }
    assert.match(sendSource, /obj\.t === ["']acp_config["']/);
    assert.doesNotMatch(sendSource, /startsWith\(['"]allow['"]\)/);
    assert.match(sendSource, /catch \(_promptError\)[\s\S]*confirmed = false/);
});


test('ACP model selector labels are translated in every supported locale', () => {
    const localeRoot = path.join(root, 'i18n');
    const required = [
        'acp_model_select_label',
        'acp_security_select_label',
        'acp_reasoning_effort_select_label',
        'acp_session_controls_label',
        'acp_control_loading',
        'acp_controls_error',
        'acp_reasoning_effort_option_ultra',
        'model_select_close',
    ];
    for (const locale of fs.readdirSync(localeRoot)) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(localeRoot, locale, 'index.json'), 'utf8'));
        for (const key of required) {
            assert.equal(typeof dictionary[key], 'string', `${locale} is missing ${key}`);
            assert.ok(dictionary[key].trim(), `${locale} has an empty ${key}`);
        }
    }
});


test('ACP completion metadata uses the standard assistant statistics panel', () => {
    assert.match(
        streamSource,
        /\['acp_model_id', 'model_id', 'modelId', 'model', 'model_name', 'modelName'\]/,
    );
    assert.match(streamSource, /\['acp_session_id'\]/);
    assert.match(streamSource, /\['context_tokens_used'\]/);
    assert.match(streamSource, /\['context_window_size'\]/);

    const localeRoot = path.join(root, 'i18n');
    const required = [
        'assistant_metadata_acp_session',
        'assistant_metadata_acp_security_level',
        'assistant_metadata_acp_reasoning_effort',
        'assistant_metadata_context_tokens_used',
        'assistant_metadata_context_window_size',
    ];
    for (const locale of fs.readdirSync(localeRoot)) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(localeRoot, locale, 'index.json'), 'utf8'));
        for (const key of required) {
            assert.equal(typeof dictionary[key], 'string', `${locale} is missing ${key}`);
            assert.ok(dictionary[key].trim(), `${locale} has an empty ${key}`);
        }
    }
});
