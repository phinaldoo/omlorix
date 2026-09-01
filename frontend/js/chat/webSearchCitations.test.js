const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const INDEX_HTML_PATH = path.join(__dirname, '..', '..', 'index.html');
const I18N_ROOT = path.join(__dirname, '..', '..', 'i18n');
const CITATION_KEYS = [
    'chat_citations_sidebar_title',
    'chat_citations_close_aria',
    'chat_citations_empty',
    'chat_citations_fallback_title',
    'chat_citations_unverified',
];

function createClassList() {
    const values = new Set();
    return {
        add(...names) {
            names.forEach((name) => values.add(name));
        },
        remove(...names) {
            names.forEach((name) => values.delete(name));
        },
        contains(name) {
            return values.has(name);
        },
    };
}

class FakeElement {
    constructor(tagName, ownerDocument) {
        this.tagName = tagName.toUpperCase();
        this.ownerDocument = ownerDocument || null;
        this.children = [];
        this.attributes = {};
        this.classList = createClassList();
        this.dataset = {};
        this.style = {};
        this.parentNode = null;
        this._textContent = '';
        this.listeners = new Map();
        this.hidden = false;
    }

    set className(value) {
        this.attributes.class = String(value || '');
    }

    get className() {
        return this.attributes.class || '';
    }

    set textContent(value) {
        this._textContent = String(value || '');
        this.children = [];
    }

    get textContent() {
        return this._textContent;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
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

    addEventListener(type, listener) {
        if (!this.listeners.has(type)) {
            this.listeners.set(type, []);
        }
        this.listeners.get(type).push(listener);
    }

    removeEventListener(type, listener) {
        const listeners = this.listeners.get(type);
        if (!listeners) {
            return;
        }
        this.listeners.set(type, listeners.filter((candidate) => candidate !== listener));
    }

    appendChild(child) {
        this.children.push(child);
        child.parentNode = this;
        return child;
    }

    replaceChildren(...children) {
        this.children = [];
        children.forEach((child) => this.appendChild(child));
    }

    contains(node) {
        if (!node) {
            return false;
        }
        if (node === this) {
            return true;
        }
        return this.children.some((child) => child.contains(node));
    }

    closest(selector) {
        if (selector !== '[hidden]') {
            return null;
        }
        let current = this;
        while (current) {
            if (current.hidden || current.hasAttribute('hidden')) {
                return current;
            }
            current = current.parentNode;
        }
        return null;
    }

    focus() {
        if (this.ownerDocument) {
            this.ownerDocument.activeElement = this;
        }
    }

    getClientRects() {
        return this.hidden ? [] : [{}];
    }

    querySelectorAll(selector) {
        const selectors = String(selector || '')
            .split(',')
            .map((entry) => entry.trim())
            .filter(Boolean);

        return findAll(this, (element) => {
            if (element === this) {
                return false;
            }
            return selectors.some((candidate) => matchesSelector(element, candidate));
        });
    }

    trigger(type, event = {}) {
        const listeners = this.listeners.get(type) || [];
        const payload = {
            target: this,
            currentTarget: this,
            preventDefault() {},
            stopPropagation() {},
            ...event,
        };
        listeners.forEach((listener) => listener(payload));
    }
}

function findAll(element, predicate, matches = []) {
    if (predicate(element)) {
        matches.push(element);
    }
    element.children.forEach((child) => findAll(child, predicate, matches));
    return matches;
}

function matchesSelector(element, selector) {
    if (selector === '[href]') {
        return Boolean(element.getAttribute('href') || element.href);
    }
    if (selector === '[tabindex]:not([tabindex="-1"])') {
        const tabindex = element.getAttribute('tabindex');
        return tabindex !== null && tabindex !== '-1';
    }
    if (selector === 'button:not([disabled])') {
        return element.tagName === 'BUTTON' && !element.hasAttribute('disabled');
    }
    if (selector === 'input:not([disabled]):not([type="hidden"])') {
        return element.tagName === 'INPUT'
            && !element.hasAttribute('disabled')
            && element.getAttribute('type') !== 'hidden';
    }
    if (selector === 'select:not([disabled])') {
        return element.tagName === 'SELECT' && !element.hasAttribute('disabled');
    }
    if (selector === 'textarea:not([disabled])') {
        return element.tagName === 'TEXTAREA' && !element.hasAttribute('disabled');
    }
    return false;
}

function createHarness(translations = {}) {
    const documentListeners = new Map();
    const document = {
        readyState: 'complete',
        activeElement: null,
        body: new FakeElement('body'),
        getElementById(id) {
            return elementsById[id] || null;
        },
        createElement(tagName) {
            return new FakeElement(tagName, document);
        },
        createElementNS(_namespace, tagName) {
            return new FakeElement(tagName, document);
        },
        addEventListener(type, listener) {
            if (!documentListeners.has(type)) {
                documentListeners.set(type, []);
            }
            documentListeners.get(type).push(listener);
        },
        dispatchEvent(event) {
            const listeners = documentListeners.get(event.type) || [];
            listeners.forEach((listener) => listener(event));
        },
    };

    const elementsById = {
        citationsSidebar: new FakeElement('aside', document),
        citationsSidebarClose: new FakeElement('button', document),
        citationsSidebarContent: new FakeElement('div', document),
        citationsSidebarBackdrop: new FakeElement('div', document),
        citationsTrigger: new FakeElement('button', document),
        'a-message-1': new FakeElement('article', document),
        'a-message-2': new FakeElement('article', document),
    };

    elementsById.citationsSidebar.appendChild(elementsById.citationsSidebarClose);
    elementsById.citationsSidebar.appendChild(elementsById.citationsSidebarContent);
    document.body.appendChild(elementsById.citationsTrigger);
    document.body.appendChild(elementsById.citationsSidebar);
    document.body.appendChild(elementsById.citationsSidebarBackdrop);

    elementsById['a-message-1'].dataset.citations = JSON.stringify([
        {
            url: 'https://example.com/article',
            title: 'Example article',
            snippet: 'Privacy-safe citation preview',
        },
    ]);
    elementsById['a-message-2'].dataset.citations = JSON.stringify([
        {
            url: 'notaurl',
            snippet: 'Needs manual verification',
        },
    ]);
    document.body.appendChild(elementsById['a-message-1']);
    document.body.appendChild(elementsById['a-message-2']);

    const window = {
        addEventListener() {},
        setTimeout,
        clearTimeout,
        getComputedStyle() {
            return {
                display: 'block',
                visibility: 'visible',
            };
        },
        getTranslation(key, fallback) {
            return Object.prototype.hasOwnProperty.call(translations, key) ? translations[key] : fallback;
        },
    };

    const context = {
        URL,
        console,
        document,
        requestAnimationFrame: (callback) => callback(),
        setTimeout,
        clearTimeout,
        window,
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'webSearchCitations.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'webSearchCitations.js' });

    return {
        document,
        sidebar: elementsById.citationsSidebar,
        closeButton: elementsById.citationsSidebarClose,
        sidebarContent: elementsById.citationsSidebarContent,
        trigger: elementsById.citationsTrigger,
        window,
    };
}

test('renders citation fallback badges without third-party favicon images', () => {
    const { sidebar, sidebarContent, window } = createHarness();

    window.showCitationsForMessage('message-1');

    const images = findAll(sidebarContent, (element) => element.tagName === 'IMG');
    const fallback = findAll(
        sidebarContent,
        (element) => element.className === 'citation-favicon-fallback',
    )[0];

    assert.equal(sidebar.classList.contains('visible'), true);
    assert.equal(sidebar.classList.contains('open'), true);
    assert.equal(images.length, 0);
    assert.ok(fallback);
    assert.equal(fallback.textContent, 'E');
});

test('citations sidebar behaves like a modal dialog with focus trapping and restore', () => {
    const { document, sidebar, closeButton, sidebarContent, trigger, window } = createHarness();
    let prevented = false;

    trigger.focus();
    window.showCitationsForMessage('message-1');

    const citationCard = findAll(sidebarContent, (element) => element.tagName === 'A')[0];

    assert.equal(sidebar.getAttribute('aria-hidden'), 'false');
    assert.equal(document.activeElement, closeButton);

    citationCard.focus();
    document.dispatchEvent({
        type: 'keydown',
        key: 'Tab',
        shiftKey: false,
        preventDefault() {
            prevented = true;
        },
    });
    assert.equal(prevented, true);
    assert.equal(document.activeElement, closeButton);

    prevented = false;
    closeButton.focus();
    document.dispatchEvent({
        type: 'keydown',
        key: 'Tab',
        shiftKey: true,
        preventDefault() {
            prevented = true;
        },
    });
    assert.equal(prevented, true);
    assert.equal(document.activeElement, citationCard);

    window.closeCitationsSidebar();
    sidebar.trigger('transitionend', { target: sidebar, propertyName: 'transform' });

    assert.equal(sidebar.classList.contains('visible'), false);
    assert.equal(document.activeElement, trigger);
});

test('citations sidebar uses translated fallback strings and rerenders on language updates', () => {
    const translations = {
        chat_citations_empty: 'Nothing here yet',
        chat_citations_fallback_title: 'Reference',
        chat_citations_unverified: 'Needs review',
    };
    const { document, sidebarContent, window } = createHarness(translations);

    window.showCitationsForMessage('missing-message');

    const emptyMessage = findAll(sidebarContent, (element) => element.tagName === 'P')[0];
    assert.equal(emptyMessage.textContent, 'Nothing here yet');

    window.showCitationsForMessage('message-2');
    let title = findAll(sidebarContent, (element) => element.className === 'citation-title')[0];
    let domain = findAll(sidebarContent, (element) => element.className === 'citation-domain')[0];

    assert.equal(title.textContent, 'Reference');
    assert.equal(domain.textContent, 'Needs review');

    translations.chat_citations_fallback_title = 'Source item';
    translations.chat_citations_unverified = 'Verification pending';
    document.dispatchEvent({ type: 'i18n:updated' });

    title = findAll(sidebarContent, (element) => element.className === 'citation-title')[0];
    domain = findAll(sidebarContent, (element) => element.className === 'citation-domain')[0];

    assert.equal(title.textContent, 'Source item');
    assert.equal(domain.textContent, 'Verification pending');
});

test('citations sidebar markup includes dialog semantics and translation hooks', () => {
    const markup = fs.readFileSync(INDEX_HTML_PATH, 'utf8');

    assert.match(markup, /id="citationsSidebar"[^>]*role="dialog"[^>]*aria-modal="true"[^>]*aria-labelledby="citationsSidebarTitle"[^>]*aria-hidden="true"[^>]*tabindex="-1"/);
    assert.match(markup, /id="citationsSidebarTitle" data-i18n="chat_citations_sidebar_title"/);
    assert.match(markup, /id="citationsSidebarClose"[^>]*data-i18n-attr="aria-label:chat_citations_close_aria"/);
});

test('citations sidebar translations exist in every supported locale', () => {
    const locales = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    locales.forEach((locale) => {
        const dictionary = JSON.parse(
            fs.readFileSync(path.join(I18N_ROOT, locale, 'index.json'), 'utf8'),
        );

        CITATION_KEYS.forEach((key) => {
            assert.ok(
                Object.prototype.hasOwnProperty.call(dictionary, key),
                `${locale} is missing ${key}`,
            );
        });
    });
});
