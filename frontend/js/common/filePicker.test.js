const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeEventTarget {
    constructor() {
        this.listeners = new Map();
    }

    addEventListener(type, handler) {
        const handlers = this.listeners.get(type) || [];
        handlers.push(handler);
        this.listeners.set(type, handlers);
    }

    removeEventListener(type, handler) {
        const handlers = this.listeners.get(type) || [];
        this.listeners.set(type, handlers.filter((entry) => entry !== handler));
    }

    dispatchEvent(event) {
        const payload = event || {};
        payload.type = payload.type || '';
        payload.target = payload.target || this;
        const handlers = [...(this.listeners.get(payload.type) || [])];
        handlers.forEach((handler) => handler(payload));
        return true;
    }
}

class FakeElement extends FakeEventTarget {
    constructor(tagName) {
        super();
        this.tagName = String(tagName || '').toUpperCase();
        this.children = [];
        this.parentNode = null;
        this.attributes = {};
        this.hidden = false;
        this.tabIndex = 0;
    }

    appendChild(child) {
        child.parentNode = this;
        this.children.push(child);
        return child;
    }

    removeChild(child) {
        this.children = this.children.filter((entry) => entry !== child);
        child.parentNode = null;
        return child;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }
}

class FakeInputElement extends FakeElement {
    constructor() {
        super('input');
        this.type = '';
        this.accept = '';
        this.multiple = false;
        this.id = '';
        this.files = [];
        this.value = '';
        this.clickCount = 0;
    }

    click() {
        this.clickCount += 1;
    }
}

function loadFilePickerContext() {
    const elementsById = new Map();
    const body = new FakeElement('body');
    const document = {
        body,
        createElement(tagName) {
            if (String(tagName).toLowerCase() === 'input') {
                return new FakeInputElement();
            }
            return new FakeElement(tagName);
        },
        getElementById(id) {
            return elementsById.get(id) || null;
        },
    };

    const window = new FakeEventTarget();
    window.setTimeout = (handler) => {
        handler();
    };

    const originalAppendChild = body.appendChild.bind(body);
    body.appendChild = (child) => {
        if (child.id) {
            elementsById.set(child.id, child);
        }
        return originalAppendChild(child);
    };

    const context = {
        console,
        document,
        window,
        HTMLInputElement: FakeInputElement,
        setTimeout(handler) {
            handler();
        },
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'filePicker.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'filePicker.js' });

    return context;
}

test('persistent file picker resolves selected files and resets the input value before reopening', async () => {
    const context = loadFilePickerContext();
    const picker = context.window.createPersistentFilePicker({
        id: 'brand-picker',
        accept: 'image/*,.svg',
    });

    assert.ok(picker);
    assert.equal(context.document.body.children.length, 1);
    assert.equal(picker.input.accept, 'image/*,.svg');

    const firstOpen = picker.open();
    assert.equal(picker.input.clickCount, 1);

    picker.input.files = [{ name: 'logo-light.svg' }];
    picker.input.dispatchEvent({ type: 'change' });

    const firstFile = await firstOpen;
    assert.deepEqual(firstFile, { name: 'logo-light.svg' });

    picker.input.value = 'keep-old-value';
    const secondOpen = picker.open();
    assert.equal(picker.input.value, '');
    assert.equal(picker.input.clickCount, 2);

    picker.input.files = [];
    context.window.dispatchEvent({ type: 'focus' });

    const secondFile = await secondOpen;
    assert.equal(secondFile, null);
});

test('persistent file picker reuses an existing hidden input when the id already exists', () => {
    const context = loadFilePickerContext();
    const existingInput = new FakeInputElement();
    existingInput.id = 'existing-picker';
    context.document.body.appendChild(existingInput);

    const picker = context.window.createPersistentFilePicker({
        id: 'existing-picker',
        accept: '.pem',
    });

    assert.equal(picker.input, existingInput);
    assert.equal(context.document.body.children.length, 1);
    assert.equal(existingInput.accept, '.pem');
});
