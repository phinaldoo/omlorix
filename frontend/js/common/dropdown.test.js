const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const DROPDOWN_PATH = path.join(__dirname, 'dropdown.js');

class FakeClassList {
    constructor(owner) {
        this.owner = owner;
        this.tokens = new Set();
    }

    add(token) {
        this.hydrate();
        this.tokens.add(token);
        this.sync();
    }

    remove(token) {
        this.hydrate();
        this.tokens.delete(token);
        this.sync();
    }

    toggle(token, force) {
        this.hydrate();
        if (force === undefined) {
            if (this.tokens.has(token)) {
                this.tokens.delete(token);
            } else {
                this.tokens.add(token);
            }
        } else if (force) {
            this.tokens.add(token);
        } else {
            this.tokens.delete(token);
        }
        this.sync();
        return this.tokens.has(token);
    }

    contains(token) {
        this.hydrate();
        return this.tokens.has(token);
    }

    hydrate() {
        String(this.owner.className || '').split(/\s+/).filter(Boolean).forEach((token) => {
            this.tokens.add(token);
        });
    }

    sync() {
        this.owner.className = Array.from(this.tokens).join(' ');
    }
}

class FakeElement {
    constructor(tagName = 'div') {
        this.tagName = tagName.toUpperCase();
        this.children = [];
        this.parentNode = null;
        this.attributes = {};
        this.dataset = {};
        this.listeners = {};
        this.className = '';
        this.classList = new FakeClassList(this);
        this.tabIndex = 0;
        this.focusCount = 0;
        this.inert = false;
        this.style = {};
        this.offsetWidth = 0;
        this.offsetHeight = 0;
    }

    appendChild(child) {
        child.parentNode = this;
        this.children.push(child);
        return child;
    }

    addEventListener(type, handler) {
        this.listeners[type] = this.listeners[type] || [];
        this.listeners[type].push(handler);
    }

    removeEventListener(type, handler) {
        this.listeners[type] = (this.listeners[type] || []).filter((candidate) => candidate !== handler);
    }

    dispatchEvent(event) {
        event.target = event.target || this;
        (this.listeners[event.type] || []).forEach((handler) => handler(event));
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
        if (name === 'tabindex') {
            this.tabIndex = Number(value);
        }
    }

    getAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
    }

    hasAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this.attributes, name);
    }

    removeAttribute(name) {
        delete this.attributes[name];
    }

    contains(target) {
        if (target === this) {
            return true;
        }
        return this.children.some((child) => child.contains(target));
    }

    getBoundingClientRect() {
        return this.rect || {
            top: 0,
            right: this.offsetWidth,
            bottom: this.offsetHeight,
            left: 0,
        };
    }

    querySelectorAll() {
        const results = [];
        const visit = (node) => {
            results.push(node);
            node.children.forEach(visit);
        };
        this.children.forEach(visit);
        return results;
    }

    focus() {
        this.focusCount += 1;
    }

    remove() {
        if (!this.parentNode) return;
        this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
        this.parentNode = null;
    }
}

class FakeDocument {
    constructor() {
        this.listeners = {};
        this.elements = [];
        this.body = new FakeElement('body');
        this.documentElement = { clientWidth: 0, clientHeight: 0 };
    }

    createElement(tagName) {
        return new FakeElement(tagName);
    }

    addEventListener(type, handler) {
        this.listeners[type] = this.listeners[type] || [];
        this.listeners[type].push(handler);
    }

    removeEventListener(type, handler) {
        this.listeners[type] = (this.listeners[type] || []).filter((candidate) => candidate !== handler);
    }

    dispatchEvent(event) {
        (this.listeners[event.type] || []).slice().forEach((handler) => handler(event));
    }

    querySelectorAll() {
        return this.elements;
    }

    querySelector() {
        return this.elements[0] || null;
    }
}

function loadDropdown() {
    const document = new FakeDocument();
    const context = {
        console,
        document,
        window: {
            addEventListener() {},
            removeEventListener() {},
            setTimeout(callback, delay = 0) {
                if (delay === 0) callback();
                return 0;
            },
            clearTimeout() {},
        },
        Icons: { check: '<svg data-icon="check"></svg>' },
    };
    context.globalThis = context;
    vm.runInNewContext(fs.readFileSync(DROPDOWN_PATH, 'utf8'), context, {
        filename: DROPDOWN_PATH,
    });
    return { context, document };
}

function clickEvent(target) {
    return {
        type: 'click',
        target,
        preventDefault() {},
        stopPropagation() {},
    };
}

test('dropdown controller toggles classes and closes on outside click', () => {
    const { context, document } = loadDropdown();
    const root = new FakeElement();
    const trigger = new FakeElement('button');
    const dropdown = new FakeElement();
    const outside = new FakeElement();
    root.appendChild(trigger);
    root.appendChild(dropdown);

    context.window.createDropdownController({ trigger, dropdown, root });

    assert.equal(trigger.getAttribute('aria-expanded'), 'false');
    assert.equal(dropdown.getAttribute('aria-hidden'), 'true');

    trigger.dispatchEvent(clickEvent(trigger));

    assert.equal(dropdown.classList.contains('open'), true);
    assert.equal(trigger.getAttribute('aria-expanded'), 'true');
    assert.equal(dropdown.getAttribute('aria-hidden'), 'false');

    document.dispatchEvent(clickEvent(outside));

    assert.equal(dropdown.classList.contains('open'), false);
    assert.equal(trigger.getAttribute('aria-expanded'), 'false');
    assert.equal(dropdown.getAttribute('aria-hidden'), 'true');
});

test('dropdown controller restores focus on Escape', () => {
    const { context, document } = loadDropdown();
    const trigger = new FakeElement('button');
    const dropdown = new FakeElement();
    context.window.createDropdownController({ trigger, dropdown });

    trigger.dispatchEvent(clickEvent(trigger));
    document.dispatchEvent({
        type: 'keydown',
        key: 'Escape',
        target: dropdown,
        preventDefault() {},
        stopPropagation() {},
    });

    assert.equal(dropdown.classList.contains('open'), false);
    assert.equal(trigger.focusCount, 1);
});

test('dropdown controller can manage inert and focusable children', () => {
    const { context } = loadDropdown();
    const trigger = new FakeElement('button');
    const dropdown = new FakeElement();
    const menuButton = new FakeElement('button');
    dropdown.appendChild(menuButton);

    context.window.createDropdownController({
        trigger,
        dropdown,
        inert: true,
        manageFocusable: true,
    });

    assert.equal(dropdown.inert, true);
    assert.equal(menuButton.tabIndex, -1);

    trigger.dispatchEvent(clickEvent(trigger));

    assert.equal(dropdown.inert, false);
    assert.equal(menuButton.hasAttribute('tabindex'), false);
});

test('dropdown controller closes matching group peers on open', () => {
    const { context } = loadDropdown();
    const firstTrigger = new FakeElement('button');
    const firstDropdown = new FakeElement();
    const secondTrigger = new FakeElement('button');
    const secondDropdown = new FakeElement();

    context.window.createDropdownController({
        group: 'shared',
        trigger: firstTrigger,
        dropdown: firstDropdown,
    });
    context.window.createDropdownController({
        group: 'shared',
        trigger: secondTrigger,
        dropdown: secondDropdown,
    });

    firstTrigger.dispatchEvent(clickEvent(firstTrigger));
    secondTrigger.dispatchEvent(clickEvent(secondTrigger));

    assert.equal(firstDropdown.classList.contains('open'), false);
    assert.equal(secondDropdown.classList.contains('open'), true);
});

test('dropdown panel navigator swaps panels, sizes the shell, and handles Escape as back', () => {
    const { context } = loadDropdown();
    const dropdown = new FakeElement();
    const mainPanel = new FakeElement();
    const formatsPanel = new FakeElement();
    const formatsTrigger = new FakeElement('button');
    dropdown.id = 'test-dropdown';
    mainPanel.dataset.dropdownPanel = 'main';
    formatsPanel.dataset.dropdownPanel = 'formats';
    formatsTrigger.dataset.dropdownOpenPanel = 'formats';
    dropdown.appendChild(mainPanel);
    mainPanel.appendChild(formatsTrigger);
    dropdown.appendChild(formatsPanel);

    const navigations = [];
    const navigator = context.window.createDropdownPanelNavigator({
        dropdown,
        panels: [mainPanel, formatsPanel],
        triggers: [formatsTrigger],
        addChevrons: false,
        maxHeight: 200,
        getPanelHeight: (panelName) => panelName === 'formats' ? 250 : 90,
        onNavigate: ({ panelName }) => navigations.push(panelName),
    });

    assert.equal(navigator.activePanel, 'main');
    assert.equal(context.window.getDropdownPanelNavigator(dropdown), navigator);
    assert.equal(dropdown.style.height, '90px');
    assert.equal(mainPanel.classList.contains('is-active'), true);
    assert.equal(formatsPanel.inert, true);
    assert.equal(formatsPanel.id, 'test-dropdown-formats-panel');
    assert.equal(formatsTrigger.getAttribute('aria-controls'), 'test-dropdown-formats-panel');

    navigator.open('formats', { focus: false });

    assert.equal(navigator.activePanel, 'formats');
    assert.equal(dropdown.style.height, '200px');
    assert.equal(mainPanel.classList.contains('is-behind'), true);
    assert.equal(formatsPanel.classList.contains('is-active'), true);
    assert.equal(formatsTrigger.getAttribute('aria-expanded'), 'true');
    assert.deepEqual(navigations, ['formats']);

    dropdown.dispatchEvent({
        type: 'keydown',
        key: 'Escape',
        preventDefault() {},
        stopPropagation() {},
    });

    assert.equal(navigator.activePanel, 'main');
    assert.equal(dropdown.style.height, '90px');
    assert.equal(formatsTrigger.getAttribute('aria-expanded'), 'false');

    navigator.destroy();
    assert.equal(context.window.getDropdownPanelNavigator(dropdown), null);
});

test('dropdown panel height animates only for panel navigation while already open', () => {
    const { context } = loadDropdown();
    const dropdown = new FakeElement();
    const mainPanel = new FakeElement();
    const detailsPanel = new FakeElement();
    dropdown.className = 'select-dropdown';
    mainPanel.dataset.dropdownPanel = 'main';
    detailsPanel.dataset.dropdownPanel = 'details';
    dropdown.appendChild(mainPanel);
    dropdown.appendChild(detailsPanel);

    let mainHeight = 90;
    const heightChanges = [];
    const navigator = context.window.createDropdownPanelNavigator({
        dropdown,
        panels: [mainPanel, detailsPanel],
        getPanelHeight: (panelName) => panelName === 'details' ? 180 : mainHeight,
        onHeightChange: ({ panelName, animated }) => heightChanges.push({ panelName, animated }),
    });

    assert.equal(dropdown.style.height, '90px');
    assert.equal(dropdown.classList.contains('is-panel-height-animating'), false);
    assert.deepEqual(heightChanges, [{ panelName: 'main', animated: false }]);

    mainHeight = 110;
    navigator.syncHeight();

    assert.equal(dropdown.style.height, '110px');
    assert.equal(dropdown.classList.contains('is-panel-height-animating'), false);

    dropdown.classList.add('open');
    mainHeight = 120;
    navigator.syncHeight();

    assert.equal(dropdown.style.height, '120px');
    assert.equal(dropdown.classList.contains('is-panel-height-animating'), false);

    navigator.open('details', { focus: false });

    assert.equal(dropdown.style.height, '180px');
    assert.equal(dropdown.classList.contains('is-panel-height-animating'), true);
    assert.deepEqual(heightChanges.at(-1), { panelName: 'details', animated: true });

    dropdown.dispatchEvent({
        type: 'transitionend',
        propertyName: 'height',
    });
    assert.equal(dropdown.classList.contains('is-panel-height-animating'), false);

    dropdown.classList.remove('open');
    navigator.reset({ focus: false });

    assert.equal(dropdown.style.height, '120px');
    assert.equal(dropdown.classList.contains('is-panel-height-animating'), false);
    assert.deepEqual(heightChanges.at(-1), { panelName: 'main', animated: false });
});

test('dropdown panel navigation respects reduced motion', () => {
    const { context } = loadDropdown();
    const dropdown = new FakeElement();
    const mainPanel = new FakeElement();
    const detailsPanel = new FakeElement();
    dropdown.className = 'select-dropdown open';
    mainPanel.dataset.dropdownPanel = 'main';
    detailsPanel.dataset.dropdownPanel = 'details';
    dropdown.appendChild(mainPanel);
    dropdown.appendChild(detailsPanel);
    context.window.matchMedia = () => ({ matches: true });

    const navigator = context.window.createDropdownPanelNavigator({
        dropdown,
        panels: [mainPanel, detailsPanel],
        getPanelHeight: (panelName) => panelName === 'details' ? 180 : 90,
    });
    navigator.open('details', { focus: false });

    assert.equal(dropdown.style.height, '180px');
    assert.equal(dropdown.classList.contains('is-panel-height-animating'), false);
});

test('shared dropdown positioning keeps a menu above its trigger when needed', () => {
    const { context } = loadDropdown();
    context.window.innerWidth = 800;
    context.window.innerHeight = 600;

    const trigger = new FakeElement('button');
    trigger.rect = { top: 520, right: 760, bottom: 556, left: 724 };
    const dropdown = new FakeElement();
    dropdown.className = 'select-dropdown';
    dropdown.offsetWidth = 200;
    dropdown.offsetHeight = 180;

    const placement = context.window.positionDropdownAtTrigger(trigger, dropdown);

    assert.equal(placement, 'top');
    assert.equal(dropdown.style.position, 'fixed');
    assert.equal(dropdown.style.left, '560px');
    assert.equal(dropdown.style.top, '332px');
    assert.equal(dropdown.classList.contains('upward'), true);
    assert.equal(dropdown.dataset.dropdownAnimationOrigin, 'bottom-right');
    assert.equal(dropdown.style.transformOrigin, 'bottom right');
});

test('shared dropdown animation derives all four origins from final geometry', () => {
    const { context } = loadDropdown();
    const trigger = new FakeElement('button');
    trigger.rect = { top: 100, right: 140, bottom: 140, left: 100 };

    const cases = [
        [{ top: 148, right: 300, bottom: 248, left: 100 }, 'top-left', '-8px'],
        [{ top: 148, right: 140, bottom: 248, left: -60 }, 'top-right', '-8px'],
        [{ top: -8, right: 300, bottom: 92, left: 100 }, 'bottom-left', '8px'],
        [{ top: -8, right: 140, bottom: 92, left: -60 }, 'bottom-right', '8px'],
    ];

    cases.forEach(([rect, expectedOrigin, expectedOffset]) => {
        const dropdown = new FakeElement();
        dropdown.className = 'select-dropdown';
        dropdown.rect = rect;

        const origin = context.window.prepareDropdownOpeningAnimation(trigger, dropdown);

        assert.equal(origin, expectedOrigin);
        assert.equal(dropdown.dataset.dropdownAnimationOrigin, expectedOrigin);
        assert.equal(dropdown.style.transformOrigin, expectedOrigin.replace('-', ' '));
        assert.equal(dropdown.style['--select-dropdown-animation-offset-y'], expectedOffset);
    });
});

test('dropdown controller prepares shared animation direction before opening', () => {
    const { context } = loadDropdown();
    const trigger = new FakeElement('button');
    trigger.rect = { top: 200, right: 240, bottom: 240, left: 200 };
    const dropdown = new FakeElement();
    dropdown.className = 'select-dropdown';
    dropdown.rect = { top: 248, right: 240, bottom: 348, left: 40 };

    const controller = context.window.createDropdownController({ trigger, dropdown });
    controller.open();

    assert.equal(dropdown.classList.contains('open'), true);
    assert.equal(dropdown.dataset.dropdownAnimationOrigin, 'top-right');
});

test('shared transient menu owns item markup, selection, and cleanup', async () => {
    const { context, document } = loadDropdown();
    context.window.innerWidth = 800;
    context.window.innerHeight = 600;

    const trigger = new FakeElement('button');
    trigger.rect = { top: 100, right: 200, bottom: 136, left: 164 };
    let selectedValue = null;
    context.window.openDropdownMenu({
        trigger,
        ariaLabel: 'Choose item',
        items: [{ value: 'safe', label: '<Safe>', checked: true }],
        onSelect: ({ value }) => { selectedValue = value; },
    });

    const menu = document.body.children[0];
    const button = menu.children[0].children[0];
    assert.equal(menu.classList.contains('select-dropdown-portal'), true);
    assert.equal(button.innerHTML.includes('&lt;Safe&gt;'), true);
    assert.equal(button.getAttribute('aria-checked'), 'true');
    assert.equal(trigger.getAttribute('aria-expanded'), 'true');

    button.dispatchEvent(clickEvent(button));
    await Promise.resolve();

    assert.equal(selectedValue, 'safe');
    assert.equal(document.body.children.length, 0);
    assert.equal(trigger.getAttribute('aria-expanded'), 'false');
});
