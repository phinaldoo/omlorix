const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PERSONAL_INFO_PATH = path.join(__dirname, 'personalInfo.js');
const INDEX_PATH = path.join(__dirname, '..', '..', '..', 'index.html');

class FakeClassList {
    constructor() {
        this.tokens = new Set();
    }

    add(...tokens) {
        tokens.forEach((token) => this.tokens.add(token));
    }

    remove(...tokens) {
        tokens.forEach((token) => this.tokens.delete(token));
    }

    contains(token) {
        return this.tokens.has(token);
    }
}

class FakeElement {
    constructor(id = '') {
        this.id = id;
        this.value = '';
        this.innerHTML = '';
        this.textContent = '';
        this.style = {};
        this.disabled = false;
        this.readOnly = false;
        this.hidden = true;
        this.classList = new FakeClassList();
        this.listeners = new Map();
        this.container = null;
    }

    addEventListener(type, handler) {
        const handlers = this.listeners.get(type) || [];
        handlers.push(handler);
        this.listeners.set(type, handlers);
    }

    querySelector() {
        return null;
    }

    closest() {
        return this.container;
    }
}

function createHarness() {
    const ids = [
        'usUserFirstName',
        'usUserLastName',
        'usUserEmail',
        'usSaveUserInfo',
        'errorMessageUserInfo',
        'userFirstNameContainer',
        'userLastNameContainer',
        'userEmailContainer',
    ];
    const elements = new Map(ids.map((id) => [id, new FakeElement(id)]));
    elements.get('usUserFirstName').container = elements.get('userFirstNameContainer');
    elements.get('usUserLastName').container = elements.get('userLastNameContainer');
    elements.get('usUserEmail').container = elements.get('userEmailContainer');

    const timers = new Map();
    let nextTimerId = 1;
    const documentListeners = new Map();
    const context = {
        console,
        Icons: { check: '<svg aria-hidden="true"></svg>' },
        setTimeout(callback) {
            const id = nextTimerId++;
            timers.set(id, callback);
            return id;
        },
        clearTimeout(id) {
            timers.delete(id);
        },
        document: {
            readyState: 'loading',
            getElementById(id) {
                return elements.get(id) || null;
            },
            createElement() {
                return new FakeElement();
            },
            addEventListener(type, handler) {
                const handlers = documentListeners.get(type) || [];
                handlers.push(handler);
                documentListeners.set(type, handlers);
            },
        },
    };
    context.window = {
        getTranslation(key, fallback) {
            return key === 'us_btn_save_changes' ? 'Translated Save Changes' : fallback;
        },
    };

    vm.runInNewContext(fs.readFileSync(PERSONAL_INFO_PATH, 'utf8'), context, {
        filename: PERSONAL_INFO_PATH,
    });

    return {
        context,
        elements,
        runTimers() {
            // Run a stable snapshot so callbacks may safely mutate the timer map.
            Array.from(timers.entries()).forEach(([id, callback]) => {
                if (!timers.has(id)) return;
                timers.delete(id);
                callback();
            });
        },
        timerCount() {
            return timers.size;
        },
    };
}

test('profile field labels reference their input IDs', () => {
    const html = fs.readFileSync(INDEX_PATH, 'utf8');

    for (const id of ['usUserFirstName', 'usUserLastName', 'usUserEmail']) {
        assert.match(html, new RegExp(`<label\\b[^>]*\\bfor="${id}"[^>]*>`));
        assert.match(html, new RegExp(`<input\\b[^>]*\\bid="${id}"[^>]*>`));
    }
});

test('profile form binds save and input handlers only once', () => {
    const { context, elements } = createHarness();
    const setup = { first_name: 'Ada', last_name: 'Lovelace', email: 'ada@example.com' };

    context.initPersonalInfoForm(setup);
    context.initPersonalInfoForm(setup);

    assert.equal(elements.get('usUserFirstName').listeners.get('input').length, 1);
    assert.equal(elements.get('usUserLastName').listeners.get('input').length, 1);
    assert.equal(elements.get('usUserEmail').listeners.get('input').length, 1);
    assert.equal(elements.get('usSaveUserInfo').listeners.get('click').length, 1);
});

test('repeated save confirmations share one timer and restore the normal button', () => {
    const { context, elements, runTimers, timerCount } = createHarness();
    const button = elements.get('usSaveUserInfo');

    context.loadUserInfo({ first_name: 'Ada', last_name: 'Lovelace', email: 'ada@example.com' });
    context.showSaveSuccessButton();
    context.showSaveSuccessButton();

    assert.equal(timerCount(), 1);
    assert.equal(button.classList.contains('save-changes-btn--success'), true);

    runTimers();

    assert.equal(button.classList.contains('save-changes-btn--success'), false);
    assert.equal(button.textContent, 'Translated Save Changes');
    assert.equal(button.style.backgroundColor, undefined);
    assert.equal(button.style.color, undefined);
    assert.equal(button.disabled, true);
});
