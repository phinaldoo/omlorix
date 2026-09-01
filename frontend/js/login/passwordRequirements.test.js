const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeClassList {
    constructor(owner) {
        this.owner = owner;
        this.classes = new Set();
    }

    setFromString(value) {
        this.classes = new Set(String(value || '').split(/\s+/).filter(Boolean));
    }

    sync() {
        this.owner._className = Array.from(this.classes).join(' ');
    }

    add(...tokens) {
        tokens.filter(Boolean).forEach((token) => this.classes.add(token));
        this.sync();
    }

    remove(...tokens) {
        tokens.forEach((token) => this.classes.delete(token));
        this.sync();
    }

    toggle(token, force) {
        if (force === undefined) {
            if (this.classes.has(token)) {
                this.classes.delete(token);
            } else {
                this.classes.add(token);
            }
        } else if (force) {
            this.classes.add(token);
        } else {
            this.classes.delete(token);
        }
        this.sync();
        return this.classes.has(token);
    }

    contains(token) {
        return this.classes.has(token);
    }
}

class FakeElement {
    constructor(id = '') {
        this.id = id;
        this._className = '';
        this.classList = new FakeClassList(this);
        this.children = [];
        this.dataset = {};
        this.attributes = {};
        this.listeners = {};
        this.style = {
            setProperty: (name, value) => {
                this.style[name] = value;
            },
        };
        this.parentNode = null;
        this.parentElement = null;
        this.textContent = '';
        this._innerHTML = '';
        this.value = '';
    }

    get className() {
        return this._className;
    }

    set className(value) {
        this._className = String(value || '');
        this.classList.setFromString(this._className);
    }

    get innerHTML() {
        return this._innerHTML;
    }

    set innerHTML(value) {
        this._innerHTML = String(value || '');
        this.children = [];

        // Parse the outer SVG element used by the password-status component.
        // The harness only needs the root element because production code adds
        // classes and accessibility attributes directly to that node.
        const svgMatch = this._innerHTML.match(/<svg\b([^>]*)>/i);
        if (svgMatch) {
            const svg = new FakeElement();
            svg.tagName = 'SVG';
            const classMatch = svgMatch[1].match(/\bclass=["']([^"']*)["']/i);
            if (classMatch) {
                svg.className = classMatch[1];
            }
            this.appendChild(svg);
        }
    }

    appendChild(child) {
        child.parentNode = this;
        child.parentElement = this;
        this.children.push(child);
        return child;
    }

    addEventListener(eventName, handler) {
        this.listeners[eventName] = this.listeners[eventName] || [];
        this.listeners[eventName].push(handler);
    }

    dispatchEvent(event) {
        (this.listeners[event.type] || []).forEach((handler) => handler(event));
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    getAttribute(name) {
        return this.attributes[name] || '';
    }

    querySelectorAll(selector) {
        const matches = [];
        const className = selector.startsWith('.') ? selector.slice(1) : null;
        const tagName = selector.toUpperCase() === 'SVG' ? 'SVG' : null;
        const visit = (node) => {
            if (
                (className && node.classList.contains(className))
                || (tagName && node.tagName === tagName)
            ) {
                matches.push(node);
            }
            node.children.forEach(visit);
        };
        this.children.forEach(visit);
        return matches;
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    contains(target) {
        if (target === this) return true;
        return this.children.some((child) => child.contains(target));
    }

    closest(selector) {
        if (!selector.startsWith('.')) return null;
        const className = selector.slice(1);
        let current = this;
        while (current) {
            if (current.classList.contains(className)) return current;
            current = current.parentElement;
        }
        return null;
    }

    getBoundingClientRect() {
        return { left: 0, top: 0, bottom: 0, width: 0, height: 0 };
    }
}

class FakeDocument {
    constructor(elements) {
        this.elements = elements;
        this.listeners = {};
        this.readyState = 'complete';
    }

    getElementById(id) {
        return this.elements[id] || null;
    }

    createElement() {
        return new FakeElement();
    }

    addEventListener(eventName, handler) {
        this.listeners[eventName] = this.listeners[eventName] || [];
        this.listeners[eventName].push(handler);
    }

    dispatchEvent(event) {
        (this.listeners[event.type] || []).forEach((handler) => handler(event));
    }
}

function createHarness() {
    const signupPasswordRequirements = new FakeElement('signupPasswordRequirements');
    signupPasswordRequirements.className = 'password-requirements';
    const formGroup = new FakeElement('formGroup');
    formGroup.className = 'form-group';
    formGroup.appendChild(signupPasswordRequirements);

    const elements = {
        signupPassword: new FakeElement('signupPassword'),
        confirmPassword: new FakeElement('confirmPassword'),
        signupPasswordRequirements,
        pwReqTooltip: new FakeElement('pwReqTooltip'),
        pwChecklist: new FakeElement('pwChecklist'),
        pwInfoBtn: new FakeElement('pwInfoBtn'),
        registerForm: new FakeElement('registerForm'),
    };
    elements.pwReqTooltip.setAttribute('aria-hidden', 'true');

    const document = new FakeDocument(elements);
    let translationsReady = false;
    const translations = {
        req_min_len: 'Mindestens {count} Zeichen',
    };

    const icons = {
        check: '<svg data-icon="check" viewBox="0 0 20 20"></svg>',
        close: '<svg data-icon="close" viewBox="0 0 20 20"></svg>',
    };
    const window = {
        innerHeight: 800,
        addEventListener: () => {},
        getTranslation: (key, fallback) => (translationsReady ? (translations[key] || fallback) : fallback),
        Icons: icons,
    };

    const context = {
        console,
        document,
        fetch: async () => ({
            ok: true,
            json: async () => ({
                min_len: 8,
                min_special: 0,
                min_upper: 0,
                min_lower: 0,
                min_num: 0,
            }),
        }),
        getTranslation: window.getTranslation,
        window,
    };
    context.globalThis = context;

    return {
        context,
        document,
        elements,
        setTranslationsReady(nextValue) {
            translationsReady = nextValue;
        },
    };
}

test('signup password requirement labels re-render after i18n becomes ready', async () => {
    const { context, document, elements, setTranslationsReady } = createHarness();
    const commonSource = fs.readFileSync(path.join(__dirname, '../common/passwordRequirements.js'), 'utf8');
    const signupSource = fs.readFileSync(path.join(__dirname, 'passwordRequirements.js'), 'utf8');

    vm.runInNewContext(commonSource, context, { filename: 'common/passwordRequirements.js' });
    vm.runInNewContext(signupSource, context, { filename: 'login/passwordRequirements.js' });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(elements.pwChecklist.querySelector('.pw-text')?.textContent, 'At least 8 characters');

    setTranslationsReady(true);
    document.dispatchEvent({ type: 'i18n:updated' });

    assert.equal(elements.pwChecklist.querySelector('.pw-text')?.textContent, 'Mindestens 8 Zeichen');
});

test('login loads shared icons before the signup password requirements module', () => {
    const loginHtml = fs.readFileSync(path.join(__dirname, '../../login.html'), 'utf8');
    const iconsIndex = loginHtml.indexOf('/js/common/icons.js');
    const requirementsIndex = loginHtml.indexOf('/js/login/passwordRequirements.js');

    assert.notEqual(iconsIndex, -1);
    assert.notEqual(requirementsIndex, -1);
    assert.ok(iconsIndex < requirementsIndex);
});

test('signup password requirements render failure and success status icons', async () => {
    const { context, elements } = createHarness();
    const commonSource = fs.readFileSync(path.join(__dirname, '../common/passwordRequirements.js'), 'utf8');
    const signupSource = fs.readFileSync(path.join(__dirname, 'passwordRequirements.js'), 'utf8');

    vm.runInNewContext(commonSource, context, { filename: 'common/passwordRequirements.js' });
    vm.runInNewContext(signupSource, context, { filename: 'login/passwordRequirements.js' });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));

    const iconWrapper = elements.pwChecklist.querySelector('.pw-icon-wrapper');
    let statusIcon = iconWrapper?.querySelector('svg');
    assert.match(iconWrapper?.innerHTML || '', /data-icon="close"/);
    assert.equal(statusIcon?.classList.contains('pw-status-icon'), true);
    assert.equal(statusIcon?.classList.contains('pw-cross'), true);
    assert.equal(statusIcon?.getAttribute('aria-hidden'), 'true');
    assert.equal(statusIcon?.getAttribute('focusable'), 'false');

    elements.signupPassword.value = 'abcdefgh';
    elements.signupPassword.dispatchEvent({ type: 'input', target: elements.signupPassword });

    statusIcon = iconWrapper?.querySelector('svg');
    assert.match(iconWrapper?.innerHTML || '', /data-icon="check"/);
    assert.equal(statusIcon?.classList.contains('pw-status-icon'), true);
    assert.equal(statusIcon?.classList.contains('pw-check'), true);
});

test('focusing the signup password input does not open the requirements tooltip', async () => {
    const { context, elements } = createHarness();
    const commonSource = fs.readFileSync(path.join(__dirname, '../common/passwordRequirements.js'), 'utf8');
    const signupSource = fs.readFileSync(path.join(__dirname, 'passwordRequirements.js'), 'utf8');

    vm.runInNewContext(commonSource, context, { filename: 'common/passwordRequirements.js' });
    vm.runInNewContext(signupSource, context, { filename: 'login/passwordRequirements.js' });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));

    // Entering the password input must leave the tooltip closed. Users can
    // still open it explicitly with the adjacent information button.
    elements.signupPassword.dispatchEvent({ type: 'focus', target: elements.signupPassword });
    assert.equal(elements.pwReqTooltip.classList.contains('visible'), false);
    assert.equal(elements.pwReqTooltip.getAttribute('aria-hidden'), 'true');

    elements.pwInfoBtn.dispatchEvent({
        type: 'click',
        target: elements.pwInfoBtn,
        preventDefault() {},
        stopPropagation() {},
    });
    assert.equal(elements.pwReqTooltip.classList.contains('visible'), true);
    assert.equal(elements.pwReqTooltip.getAttribute('aria-hidden'), 'false');
});
