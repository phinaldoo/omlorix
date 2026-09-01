const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeClassList {
    constructor() {
        this.values = new Set();
    }

    add(...names) {
        names.forEach((name) => this.values.add(name));
    }

    remove(...names) {
        names.forEach((name) => this.values.delete(name));
    }

    contains(name) {
        return this.values.has(name);
    }

    toggle(name, force) {
        const shouldAdd = force === undefined ? !this.contains(name) : Boolean(force);
        if (shouldAdd) this.add(name);
        else this.remove(name);
        return shouldAdd;
    }
}

class FakeElement {
    constructor(id = '') {
        this.id = id;
        this.attributes = new Map();
        this.classList = new FakeClassList();
        this.dataset = {};
        this.hidden = false;
        this.innerHTML = '';
        this.listeners = new Map();
        this.focused = false;
        this.style = {
            setProperty() {},
            removeProperty() {},
        };
    }

    addEventListener(type, callback) {
        if (!this.listeners.has(type)) this.listeners.set(type, []);
        this.listeners.get(type).push(callback);
    }

    removeEventListener(type, callback) {
        this.listeners.set(
            type,
            (this.listeners.get(type) || []).filter((listener) => listener !== callback),
        );
    }

    dispatch(type, event = {}) {
        const normalizedEvent = {
            preventDefault() {},
            stopPropagation() {},
            ...event,
        };
        (this.listeners.get(type) || []).forEach((callback) => callback(normalizedEvent));
        return normalizedEvent;
    }

    setAttribute(name, value) {
        this.attributes.set(name, String(value));
    }

    getAttribute(name) {
        return this.attributes.get(name) ?? null;
    }

    hasAttribute(name) {
        return this.attributes.has(name);
    }

    contains(target) {
        return target === this;
    }

    querySelectorAll() {
        return [];
    }

    getBoundingClientRect() {
        return {
            top: 20,
            right: 64,
            bottom: 64,
            left: 20,
            width: 44,
            height: 44,
        };
    }

    focus() {
        this.focused = true;
    }
}

function loadWorkspaceIconUtils() {
    const dropdownSource = fs.readFileSync(path.join(__dirname, 'dropdown.js'), 'utf8');
    const source = fs.readFileSync(path.join(__dirname, 'workspaceIcons.js'), 'utf8');
    const documentListeners = new Map();
    const context = {
        console,
        document: {
            documentElement: { clientWidth: 1024, clientHeight: 768 },
            addEventListener(type, callback) {
                if (!documentListeners.has(type)) documentListeners.set(type, []);
                documentListeners.get(type).push(callback);
            },
            removeEventListener(type, callback) {
                documentListeners.set(
                    type,
                    (documentListeners.get(type) || []).filter((listener) => listener !== callback),
                );
            },
            querySelectorAll() {
                return [];
            },
            dispatch(type, event = {}) {
                const normalizedEvent = {
                    preventDefault() {},
                    stopPropagation() {},
                    ...event,
                };
                (documentListeners.get(type) || []).slice().forEach((callback) => callback(normalizedEvent));
            },
        },
        window: {
            innerWidth: 1024,
            innerHeight: 768,
            setTimeout(callback) { callback(); },
        },
    };
    context.globalThis = context;
    vm.runInNewContext(dropdownSource, context, { filename: 'dropdown.js' });
    vm.runInNewContext(source, context, { filename: 'workspaceIcons.js' });
    return {
        document: context.document,
        iconUtils: context.window.WorkspaceIconUtils,
    };
}

function createPickerFixture() {
    const picker = new FakeElement('picker');
    const trigger = new FakeElement('iconTrigger');
    trigger.setAttribute('aria-label', 'Choose icon');
    const preview = new FakeElement('preview');
    const dropdown = new FakeElement();
    dropdown.offsetWidth = 296;
    dropdown.offsetHeight = 320;

    return {
        picker,
        trigger,
        preview,
        dropdown,
        svgGrid: new FakeElement('svgGrid'),
        colorGrid: new FakeElement('colorGrid'),
        saveButton: new FakeElement('saveButton'),
        cancelButton: new FakeElement('cancelButton'),
    };
}

test('shared SVG-select picker delegates state and accessibility to the dropdown controller', () => {
    const { document, iconUtils } = loadWorkspaceIconUtils();
    const refs = createPickerFixture();
    const state = {};
    const picker = iconUtils.createWorkspaceIconPicker({
        state,
        refs,
        iconOptions: [
            { id: 'folder', name: 'Folder', svg: '<svg><path d="folder"/></svg>' },
            { id: 'archive', name: 'Archive', svg: '<svg><path d="archive"/></svg>' },
        ],
        colors: [
            { id: 'red', name: 'Red', hex: '#E53935' },
            { id: 'blue', name: 'Blue', hex: '#1E88E5' },
        ],
        defaultIconId: 'folder',
        defaultColor: '#E53935',
        variant: 'svg-select',
    });

    picker.bind();
    picker.render();
    picker.updatePreview();

    assert.equal(refs.trigger.getAttribute('aria-expanded'), 'false');
    assert.equal(refs.trigger.getAttribute('aria-haspopup'), 'dialog');
    assert.equal(refs.trigger.getAttribute('aria-controls'), 'iconTriggerDropdown');
    assert.equal(refs.dropdown.getAttribute('role'), 'dialog');
    assert.equal(refs.dropdown.getAttribute('aria-hidden'), 'true');
    assert.match(refs.svgGrid.innerHTML, /<button/);
    assert.match(refs.svgGrid.innerHTML, /aria-pressed="true"/);

    picker.setOpen(true);
    picker.selectPreset('archive');
    picker.selectColor(1);
    picker.close();

    assert.equal(picker.getIconData().iconId, 'folder');
    assert.equal(picker.getIconData().color, '#E53935');
    assert.equal(refs.trigger.getAttribute('aria-expanded'), 'false');
    assert.equal(refs.dropdown.getAttribute('aria-hidden'), 'true');

    picker.setOpen(true);
    picker.selectPreset('archive');
    document.dispatch('keydown', { key: 'Escape', target: refs.dropdown });

    assert.equal(picker.getIconData().iconId, 'folder');
    assert.equal(refs.trigger.focused, true);
    assert.equal(refs.dropdown.classList.contains('open'), false);
});

test('shared SVG-select picker keeps a selection when rerender detaches the click target', () => {
    const { document, iconUtils } = loadWorkspaceIconUtils();
    const refs = createPickerFixture();
    const picker = iconUtils.createWorkspaceIconPicker({
        refs,
        iconOptions: [
            { id: 'folder', name: 'Folder', svg: '<svg><path d="folder"/></svg>' },
            { id: 'archive', name: 'Archive', svg: '<svg><path d="archive"/></svg>' },
        ],
        defaultIconId: 'folder',
        variant: 'svg-select',
    });
    const clickedOption = {
        dataset: { iconId: 'archive' },
        closest: () => clickedOption,
    };

    picker.bind();
    picker.setOpen(true);
    const event = refs.svgGrid.dispatch('click', {
        target: clickedOption,
        composedPath: () => [clickedOption, refs.svgGrid, refs.picker],
    });

    // The option was replaced by render(), so contains(target) is false by
    // the time the event reaches document. Its original path still proves
    // that the click came from inside the picker and must not cancel it.
    document.dispatch('click', event);

    assert.equal(refs.picker.contains(clickedOption), false);
    assert.equal(picker.state.isOpen, true);
    assert.equal(picker.getIconData().iconId, 'archive');
});

test('shared picker resolves the canonical serialized preset', () => {
    const { iconUtils } = loadWorkspaceIconUtils();
    const resolved = iconUtils.resolveWorkspaceStoredIcon(JSON.stringify({
        preset: 'archive',
        color: '#1E88E5',
    }), {
        iconOptions: [
            { id: 'folder', name: 'Folder', svg: '<svg><path d="folder"/></svg>' },
            { id: 'archive', name: 'Archive', svg: '<svg><path d="archive"/></svg>' },
        ],
        defaultIconId: 'folder',
        defaultColor: '#E53935',
    });

    assert.equal(JSON.stringify(resolved), JSON.stringify({
        type: 'preset',
        iconId: 'archive',
        svg: '<svg><path d="archive"/></svg>',
        color: '#1E88E5',
    }));

});

test('Projects and Automations configure the shared picker instead of local picker engines', () => {
    const chatDir = path.join(__dirname, '..', 'chat');
    const projectsSource = fs.readFileSync(path.join(chatDir, 'projects.js'), 'utf8');
    const automationsSource = fs.readFileSync(path.join(chatDir, 'automations.js'), 'utf8');

    [projectsSource, automationsSource].forEach((source) => {
        assert.equal((source.match(/createWorkspaceIconPicker\(/g) || []).length, 2);
        assert.doesNotMatch(source, /function init(?:Automation)?SvgGrid/);
        assert.doesNotMatch(source, /\.current(?:SvgIndex|Color)\b/);
        assert.doesNotMatch(source, /emoji/i);
    });

    const assertClosedBeforeSerialization = (source, handler, closeCall, serializeCall) => {
        const handlerIndex = source.indexOf(handler);
        const closeIndex = source.indexOf(closeCall, handlerIndex);
        const serializeIndex = source.indexOf(serializeCall, handlerIndex);

        assert.notEqual(handlerIndex, -1, `${handler} should exist`);
        assert.notEqual(closeIndex, -1, `${closeCall} should run from ${handler}`);
        assert.notEqual(serializeIndex, -1, `${serializeCall} should run from ${handler}`);
        assert.ok(closeIndex < serializeIndex, `${closeCall} should run before ${serializeCall}`);
    };

    // The outer form buttons must cancel an open, unconfirmed picker preview
    // before reading the committed icon selection for their request payloads.
    assertClosedBeforeSerialization(
        projectsSource,
        "confirmCreateProjectBtn.addEventListener('click'",
        'ProjectCreateIconPicker?.close?.()',
        "ProjectUtils.serializeSelection('create')",
    );
    assertClosedBeforeSerialization(
        projectsSource,
        'async function updateProject()',
        'ProjectEditIconPicker?.close?.()',
        "ProjectUtils.serializeSelection('edit')",
    );
    assertClosedBeforeSerialization(
        automationsSource,
        "confirmCreateAutomationBtn.addEventListener('click'",
        'AutomationCreateIconPicker?.close?.()',
        "AutomationIconUtils.serialize('create')",
    );
    assertClosedBeforeSerialization(
        automationsSource,
        "saveAutomationChangesBtn.addEventListener('click'",
        'AutomationEditIconPicker?.close?.()',
        "AutomationIconUtils.serialize('edit')",
    );
});
