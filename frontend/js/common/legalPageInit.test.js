const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, 'legalPageInit.js'), 'utf8');

function createElement({ documentKey } = {}) {
    const attributes = new Map();
    const listeners = new Map();
    return {
        dataset: documentKey ? { legalDocument: documentKey } : {},
        hidden: false,
        children: [],
        textContent: '',
        setAttribute(name, value) {
            attributes.set(name, String(value));
        },
        removeAttribute(name) {
            attributes.delete(name);
        },
        getAttribute(name) {
            return attributes.get(name) ?? null;
        },
        addEventListener(type, listener) {
            listeners.set(type, listener);
        },
        replaceChildren(...children) {
            this.children = children;
        },
        focus() {
            this.focused = true;
        },
        async dispatch(type) {
            return listeners.get(type)?.({
                preventDefault() {},
            });
        },
    };
}

async function runBootstrap({
    availability,
    availabilityError = false,
    availabilityStalls = false,
    documentFailures = {},
    pathname = '/privacy',
    search = '',
    hash = '',
}) {
    const domContentLoadedListeners = [];
    const documentListeners = new Map();
    const windowListeners = new Map();
    const navigationCalls = [];
    const renderedDocuments = [];
    const consoleErrors = [];
    const remainingDocumentFailures = new Map(Object.entries(documentFailures));
    let availabilitySignalAborted = false;
    let availabilityTimeoutCleared = false;
    const nav = createElement();
    nav.hidden = true;
    const privacyLink = createElement({ documentKey: 'privacy' });
    const termsLink = createElement({ documentKey: 'terms' });
    const container = createElement();

    const document = {
        title: '',
        addEventListener(type, listener) {
            if (type === 'DOMContentLoaded') {
                domContentLoadedListeners.push(listener);
            } else {
                documentListeners.set(type, listener);
            }
        },
        removeEventListener(type) {
            documentListeners.delete(type);
        },
        querySelectorAll(selector) {
            return selector === '[data-legal-document]' ? [privacyLink, termsLink] : [];
        },
        getElementById(id) {
            return {
                legalDocumentNav: nav,
                mainContainer: container,
                'main-container': container,
            }[id] || null;
        },
        createElement() {
            return createElement();
        },
    };

    const location = {
        pathname,
        search,
        hash,
        replace(url) {
            navigationCalls.push({ type: 'location.replace', url });
        },
    };
    const history = {
        pushState(_state, _unused, url) {
            navigationCalls.push({ type: 'push', url });
        },
        replaceState(_state, _unused, url) {
            navigationCalls.push({ type: 'replace', url });
        },
    };

    const window = {
        document,
        location,
        history,
        legalPageUtils: {
            async initLegalPage(config) {
                renderedDocuments.push(config.pageKey);
                const remainingFailures = remainingDocumentFailures.get(config.pageKey) || 0;
                if (remainingFailures > 0) {
                    remainingDocumentFailures.set(config.pageKey, remainingFailures - 1);
                    return () => {};
                }
                config.onLoaded?.();
                return () => {};
            },
        },
        getTranslation(_key, fallback) {
            return fallback;
        },
        addEventListener(type, listener) {
            windowListeners.set(type, listener);
        },
        removeEventListener(type) {
            windowListeners.delete(type);
        },
        setTimeout(callback, timeout) {
            assert.equal(timeout, 10_000);
            if (availabilityStalls) {
                queueMicrotask(callback);
            }
            return 1;
        },
        clearTimeout(timerId) {
            assert.equal(timerId, 1);
            availabilityTimeoutCleared = true;
        },
        scrollTo() {},
    };

    const context = {
        AbortController,
        URLSearchParams,
        console: {
            error(...args) {
                consoleErrors.push(args);
            },
        },
        document,
        fetch: async (url, options) => {
            assert.equal(url, '/api/v1/legal/availability');
            assert.equal(container.children[0]?.className, 'legal-loading-state');
            assert.ok(options.signal instanceof AbortSignal);
            if (availabilityStalls) {
                return new Promise((_resolve, reject) => {
                    options.signal.addEventListener('abort', () => {
                        availabilitySignalAborted = true;
                        const error = new Error('availability timed out');
                        error.name = 'AbortError';
                        reject(error);
                    }, { once: true });
                });
            }
            if (availabilityError) {
                throw new Error('availability unavailable');
            }
            return {
                ok: true,
                async json() {
                    return availability;
                },
            };
        },
        window,
    };
    context.globalThis = context;

    vm.runInNewContext(SOURCE, context, { filename: 'legalPageInit.js' });
    assert.equal(domContentLoadedListeners.length, 1);
    await domContentLoadedListeners[0]();

    return {
        availabilitySignalAborted,
        availabilityTimeoutCleared,
        container,
        consoleErrors,
        nav,
        navigationCalls,
        privacyLink,
        renderedDocuments,
        termsLink,
    };
}

test('shows the switcher and honors the requested path when both documents are enabled', async () => {
    const page = await runBootstrap({
        availability: { privacy: true, terms: true },
        pathname: '/terms',
    });

    assert.equal(page.nav.hidden, false);
    assert.deepEqual(page.renderedDocuments, ['terms']);
    assert.equal(page.termsLink.getAttribute('aria-current'), 'page');
    assert.equal(page.privacyLink.getAttribute('aria-current'), null);
    assert.deepEqual(page.navigationCalls, []);

    await page.privacyLink.dispatch('click');
    assert.deepEqual(page.renderedDocuments, ['terms', 'privacy']);
    assert.deepEqual(page.navigationCalls, [{ type: 'push', url: '/privacy' }]);
    assert.equal(page.privacyLink.getAttribute('aria-current'), 'page');
});

test('hides the switcher and uses the only visible document for the generic legal route', async () => {
    const page = await runBootstrap({
        availability: { privacy: true, terms: false },
        pathname: '/legal',
    });

    assert.equal(page.nav.hidden, true);
    assert.equal(page.termsLink.hidden, true);
    assert.deepEqual(page.renderedDocuments, ['privacy']);
    assert.deepEqual(page.navigationCalls, [{ type: 'replace', url: '/privacy' }]);
});

test('keeps direct legal routes readable when their optional navigation links are hidden', async () => {
    const page = await runBootstrap({
        availability: { privacy: false, terms: false },
        pathname: '/terms',
    });

    assert.equal(page.nav.hidden, true);
    assert.equal(page.privacyLink.hidden, true);
    assert.equal(page.termsLink.hidden, true);
    assert.deepEqual(page.renderedDocuments, ['terms']);
    assert.deepEqual(page.navigationCalls, []);
});

test('accepts a query-selected document and replaces it with the canonical path', async () => {
    const page = await runBootstrap({
        availability: { privacy: true, terms: true },
        pathname: '/legal',
        search: '?document=terms',
    });

    assert.deepEqual(page.renderedDocuments, ['terms']);
    assert.deepEqual(page.navigationCalls, [{ type: 'replace', url: '/terms' }]);
});

test('keeps direct document access available when link visibility cannot be loaded', async () => {
    const page = await runBootstrap({
        availabilityError: true,
        pathname: '/terms',
    });

    assert.equal(page.nav.hidden, true);
    assert.equal(page.privacyLink.hidden, true);
    assert.equal(page.termsLink.hidden, true);
    assert.deepEqual(page.renderedDocuments, ['terms']);
    assert.deepEqual(page.navigationCalls, []);
    assert.equal(page.consoleErrors.length, 1);
});

test('times out stalled link visibility while preserving direct document access', async () => {
    const page = await runBootstrap({
        availabilityStalls: true,
        pathname: '/terms',
    });

    assert.equal(page.availabilitySignalAborted, true);
    assert.equal(page.availabilityTimeoutCleared, true);
    assert.deepEqual(page.renderedDocuments, ['terms']);
    assert.equal(page.consoleErrors.length, 1);
});

test('allows retry after a failed document load and guards the successful document', async () => {
    const page = await runBootstrap({
        availability: { privacy: true, terms: true },
        documentFailures: { terms: 1 },
        pathname: '/privacy',
    });

    await page.termsLink.dispatch('click');
    assert.deepEqual(page.renderedDocuments, ['privacy', 'terms']);

    await page.termsLink.dispatch('click');
    assert.deepEqual(page.renderedDocuments, ['privacy', 'terms', 'terms']);

    await page.termsLink.dispatch('click');
    assert.deepEqual(page.renderedDocuments, ['privacy', 'terms', 'terms']);
});
