const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function createWorkspace() {
    let document;
    class Element {
        constructor(tag) {
            this.tagName = tag;
            this.children = [];
            this.attributes = new Map();
            this.handlers = new Map();
            this.style = { setProperty() {} };
            this.inert = false;
            this.classList = { add() {}, remove() {}, toggle() {} };
        }
        append(...children) { children.forEach((child) => this.appendChild(child)); }
        appendChild(child) { child.remove(); this.children.push(child); child.parentElement = this; return child; }
        remove() {
            if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
            this.parentElement = null;
        }
        get isConnected() { return this === document.body || Boolean(this.parentElement?.isConnected); }
        setAttribute(name, value) { this.attributes.set(name, value); }
        getAttribute(name) { return this.attributes.get(name); }
        removeAttribute(name) { this.attributes.delete(name); }
        addEventListener(name, callback) { this.handlers.set(name, callback); }
        contains(element) { return this === element || this.children.some((child) => child.contains(element)); }
        focus() { document.activeElement = this; }
        scrollIntoView() {}
    }
    const background = new Element('main');
    const media = { matches: false, addEventListener(_name, callback) { this.change = callback; } };
    document = {
        createElement: (tag) => new Element(tag),
        documentElement: { clientWidth: 1400, style: { setProperty() {} } },
        body: new Element('body'),
        querySelectorAll: () => [background],
        addEventListener() {}, dispatchEvent() {},
    };
    document.body.append(background);
    background.focus();
    const stored = new Map();
    const window = {
        innerWidth: 1400, matchMedia: () => media, addEventListener() {},
        localStorage: { getItem: (key) => stored.get(key), setItem: (key, value) => stored.set(key, value) },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'chatWorkspace.js'), 'utf8'), {
        document, window, Icons: { close: '' }, CustomEvent: class {},
    });
    const workspace = window.ChatWorkspace;
    const panel = document.body.children[1];
    const tabs = panel.children[1].children[0];
    function add(id, options = {}) {
        const element = new Element('section');
        workspace.register({ id, tabbed: id !== 'canvas', label: () => id, create: () => element, ...options });
        return element;
    }
    return { workspace, add, panel, tabs, document, background, media, window, stored };
}

test('background views never steal selection or reopen a dismissed panel; switching preserves view state', () => {
    const { workspace, add, tabs, background, document } = createWorkspace();
    let hidden = 0;
    const first = add('agent-1', { onHide: () => { hidden += 1; } });
    const second = add('agent-2');
    add('canvas', { available: false });
    workspace.show('agent-1', { focus: true, trigger: background });
    first.draft = 'Keep my reading state';
    workspace.show('canvas', { automatic: true });
    assert.equal(workspace.isSelected('agent-1'), true);
    assert.equal(tabs.children.length, 2, 'Canvas has no subagent tab');
    assert.equal(second.parentElement, undefined, 'unopened transcripts are not mounted');
    workspace.show('agent-2');
    assert.equal(first.hidden, true);
    assert.notEqual(first.id, second.id, 'lazy views have distinct accessible targets');
    workspace.close();
    assert.equal(document.activeElement, background);
    workspace.show('canvas', { automatic: true });
    assert.equal(workspace.isSelected('canvas'), false);
    workspace.show('agent-1');
    assert.equal(first.draft, 'Keep my reading state');
    assert.equal(first.hidden, false);
    assert.equal(hidden, 1, 'reopening must not hide an already hidden view a second time');
    assert.equal(tabs.children[0].getAttribute('aria-selected'), 'true');
    assert.equal(tabs.children[1].tabIndex, -1);
});

test('mobile layout restores preexisting inert state and keyboard tabs skip standalone previews', () => {
    const { workspace, add, media, panel, tabs, background, document } = createWorkspace();
    add('agent-1'); add('canvas'); add('agent-2');
    workspace.show('agent-1', { focus: true });
    let prevented = false;
    tabs.handlers.get('keydown')({ key: 'ArrowRight', target: tabs.children[0], preventDefault() { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(workspace.isSelected('agent-2'), true);
    assert.equal(document.activeElement, tabs.children[1]);
    media.matches = true; media.change();
    assert.equal(panel.getAttribute('aria-modal'), 'true');
    assert.equal(background.inert, true);
    workspace.close();
    assert.equal(background.inert, false);
    background.inert = true;
    workspace.show('agent-1'); workspace.close();
    assert.equal(background.inert, true, 'existing restrictions are preserved');
});

test('removing a departed run releases its view and panel; resize bounds survive narrow layouts', () => {
    const { workspace, add, panel, tabs, window } = createWorkspace();
    const view = add('agent-1');
    workspace.show('agent-1');
    workspace.remove('agent-1');
    assert.equal(view.isConnected, false);
    assert.equal(tabs.children.length, 0);
    assert.equal(panel.inert, true);
    assert.equal(workspace.resize.setPreviewWidthFromPixels(2000), 1040);
    assert.equal(workspace.resize.setPreviewWidthFromPixels(10), 420);
    window.innerWidth = 700;
    workspace.resize.applyPreviewWidthRatio();
    window.innerWidth = 1400;
    workspace.resize.applyPreviewWidthRatio();
    assert.equal(workspace.resize.getPreviewWidthRatio(), 0.3);
});


test('Canvas keeps its own header and receives focus instead of a hidden subagent tab', () => {
    const { workspace, add, panel, tabs, document, media } = createWorkspace();
    add('agent-1');
    const canvas = add('canvas');
    workspace.show('agent-1');
    const header = panel.children[1];
    assert.equal(header.hidden, false);
    workspace.show('canvas', { focus: true });
    assert.equal(header.hidden, true);
    assert.equal(tabs.children.length, 1);
    assert.equal(canvas.getAttribute('role'), 'region');
    assert.equal(canvas.getAttribute('aria-label'), 'canvas');
    assert.equal(canvas.getAttribute('aria-labelledby'), undefined);
    assert.equal(document.activeElement, canvas);
    media.matches = true; media.change();
    workspace.close();
    workspace.show('canvas');
    assert.equal(document.activeElement, canvas);
    assert.equal(header.hidden, true);
    workspace.show('agent-1');
    assert.equal(header.hidden, false);
    assert.equal(document.activeElement, tabs.children[0]);
});
