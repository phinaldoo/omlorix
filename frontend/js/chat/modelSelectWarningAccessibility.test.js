const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SCRIPT_PATH = path.join(__dirname, 'modelSelect.js');
const TOOLTIP_SCRIPT_PATH = path.join(__dirname, '..', 'common', 'tooltip.js');
const I18N_ROOT = path.join(__dirname, '..', '..', 'i18n');

class FakeElement {
    constructor(tagName) {
        this.tagName = String(tagName).toUpperCase();
        this.attributes = new Map();
        this.children = [];
        const classNames = new Set();
        this.classList = {
            add: (...names) => names.forEach((name) => classNames.add(name)),
            contains: (name) => classNames.has(name),
            remove: (...names) => names.forEach((name) => classNames.delete(name)),
            toggle: (name, force) => {
                const enabled = force === undefined ? !classNames.has(name) : Boolean(force);
                if (enabled) classNames.add(name);
                else classNames.delete(name);
                return enabled;
            },
        };
        this.className = '';
        this.dataset = {};
        this.id = '';
        this.innerHTML = '';
        this.listeners = new Map();
        this.parentElement = null;
        this.style = {};
        this.textContent = '';
        this.type = '';
    }

    appendChild(child) {
        child.parentElement = this;
        this.children.push(child);
        return child;
    }

    addEventListener(type, listener) {
        const listeners = this.listeners.get(type) || [];
        listeners.push(listener);
        this.listeners.set(type, listeners);
    }

    removeEventListener(type, listener) {
        const listeners = this.listeners.get(type) || [];
        this.listeners.set(type, listeners.filter((candidate) => candidate !== listener));
    }

    contains(candidate) {
        return candidate === this || this.children.some((child) => child.contains(candidate));
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
}

function findByClass(root, className) {
    if (root.className.split(/\s+/).includes(className)) return root;
    for (const child of root.children) {
        const match = findByClass(child, className);
        if (match) return match;
    }
    return null;
}

function createContext() {
    const document = {
        body: new FakeElement('body'),
        addEventListener() {},
        createElement(tagName) {
            return new FakeElement(tagName);
        },
        getElementById() {
            return null;
        },
        querySelectorAll() {
            return [];
        },
    };
    const translations = {
        model_select_elevated_errors: 'Localized elevated error message',
        model_select_warning_label: 'Localized warning',
    };
    const window = {
        addEventListener() {},
        getTranslation(key, fallback) {
            return translations[key] || fallback;
        },
        innerHeight: 800,
        innerWidth: 1280,
        removeEventListener() {},
        setupTooltip() {},
    };
    const iconMarkup = '<svg aria-hidden="true"></svg>';
    const Icons = new Proxy({ warning: iconMarkup }, {
        get(target, property) {
            if (property === 'createSvgElement' || property === 'wrapSvgBody' || property === 'featureIconBodies') {
                return undefined;
            }
            return target[property] || iconMarkup;
        },
    });
    const context = {
        clearTimeout,
        console,
        document,
        Icons,
        setTimeout,
        URLSearchParams,
        window,
    };

    vm.runInNewContext(fs.readFileSync(SCRIPT_PATH, 'utf8'), context, { filename: SCRIPT_PATH });
    return context;
}

function renderWarning(context, modelId) {
    const item = context.createModelItem({
        increased_errors: true,
        model_id: modelId,
        name: `Model ${modelId}`,
    });
    return {
        item,
        tooltip: findByClass(item, 'tooltip'),
        trigger: findByClass(item, 'model-select-warning'),
    };
}

test('elevated-error warnings are named, described, and uniquely linked', () => {
    const context = createContext();
    const first = renderWarning(context, 'one');
    const second = renderWarning(context, 'two');

    for (const { tooltip, trigger } of [first, second]) {
        assert.equal(trigger.tagName, 'BUTTON');
        assert.equal(trigger.type, 'button');
        assert.equal(trigger.id, '');
        assert.equal(trigger.getAttribute('aria-label'), 'Localized warning');
        assert.equal(trigger.hasAttribute('aria-haspopup'), false);
        assert.equal(trigger.hasAttribute('aria-expanded'), false);
        assert.equal(tooltip.getAttribute('role'), 'tooltip');
        assert.equal(tooltip.textContent, 'Localized elevated error message');
        assert.equal(trigger.getAttribute('aria-describedby'), tooltip.id);
        assert.match(trigger.innerHTML, /aria-hidden="true"/);
    }

    assert.notEqual(first.tooltip.id, second.tooltip.id);
});

test('warning interaction does not activate its model option', async () => {
    const { item, trigger } = renderWarning(createContext(), 'one');
    let clickPropagationStopped = false;
    trigger.listeners.get('click')[0]({
        stopPropagation() {
            clickPropagationStopped = true;
        },
    });
    assert.equal(clickPropagationStopped, true);

    let rowKeyHandled = false;
    await item.listeners.get('keydown')[0]({
        key: 'Enter',
        target: trigger,
        preventDefault() {
            rowKeyHandled = true;
        },
        stopPropagation() {
            rowKeyHandled = true;
        },
    });
    assert.equal(rowKeyHandled, false);
});

test('shared tooltips respond to keyboard focus on coarse-pointer devices', () => {
    const timers = [];
    const body = new FakeElement('body');
    const trigger = new FakeElement('button');
    const tooltip = new FakeElement('div');
    const container = new FakeElement('span');
    container.appendChild(trigger);
    container.appendChild(tooltip);
    container.querySelector = (selector) => (
        selector === ':scope > .tooltip-content' ? trigger : tooltip
    );
    trigger.getBoundingClientRect = () => ({ bottom: 20, height: 10, left: 10, top: 10, width: 10 });
    tooltip.offsetHeight = 20;
    tooltip.offsetWidth = 100;

    const document = {
        addEventListener() {},
        body,
        documentElement: { clientHeight: 800, clientWidth: 1280 },
        removeEventListener() {},
        querySelectorAll() {
            return [];
        },
    };
    const window = {
        innerHeight: 800,
        innerWidth: 1280,
        matchMedia() {
            return { matches: true };
        },
    };
    const context = {
        cancelAnimationFrame() {},
        clearTimeout() {},
        console,
        document,
        navigator: { maxTouchPoints: 1 },
        requestAnimationFrame() {
            return 1;
        },
        setTimeout(callback) {
            timers.push(callback);
            return timers.length;
        },
        window,
    };

    vm.runInNewContext(fs.readFileSync(TOOLTIP_SCRIPT_PATH, 'utf8'), context, {
        filename: TOOLTIP_SCRIPT_PATH,
    });
    context.window.setupTooltip(container);

    container.listeners.get('focusin')[0]();
    assert.equal(tooltip.classList.contains('visible'), true);

    container.listeners.get('focusout')[0]({ relatedTarget: null });
    timers.shift()();
    assert.equal(tooltip.classList.contains('visible'), false);
});

test('the warning label is translated in every supported locale', () => {
    const locales = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    for (const locale of locales) {
        const translations = JSON.parse(fs.readFileSync(path.join(I18N_ROOT, locale, 'index.json'), 'utf8'));
        assert.equal(typeof translations.model_select_warning_label, 'string', locale);
        assert.notEqual(translations.model_select_warning_label.trim(), '', locale);
    }
});
