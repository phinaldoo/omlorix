const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const CANVAS_WIDGET_PATH = path.join(__dirname, 'canvas-widget.js');
const PDF_PREVIEW_PATH = path.join(__dirname, 'canvas-widget', 'pdf-preview.js');

class FakeClassList {
    constructor(element) {
        this.element = element;
    }

    values() {
        return new Set(String(this.element.className || '').split(/\s+/).filter(Boolean));
    }

    add(...names) {
        const values = this.values();
        names.forEach((name) => values.add(name));
        this.element.className = [...values].join(' ');
    }

    remove(...names) {
        const values = this.values();
        names.forEach((name) => values.delete(name));
        this.element.className = [...values].join(' ');
    }

    contains(name) {
        return this.values().has(name);
    }
}

class FakeElement {
    constructor(tagName, { decodeImage } = {}) {
        this.tagName = String(tagName || '').toUpperCase();
        this.children = [];
        this.className = '';
        this.classList = new FakeClassList(this);
        this.dataset = {};
        this.style = {};
        this.attributes = new Map();
        this.parentElement = null;
        this.isConnected = false;
        this.clientWidth = 300;
        this.offsetWidth = 10;
        this.textContent = '';
        if (this.tagName === 'IMG') this.decode = decodeImage;
    }

    setConnected(isConnected) {
        this.isConnected = Boolean(isConnected);
        this.children.forEach((child) => child.setConnected?.(isConnected));
    }

    appendChild(child) {
        child.parentElement = this;
        child.setConnected?.(this.isConnected);
        this.children.push(child);
        return child;
    }

    append(...children) {
        children.forEach((child) => this.appendChild(child));
    }

    replaceChildren(...children) {
        this.children.forEach((child) => {
            child.parentElement = null;
            child.setConnected?.(false);
        });
        this.children = [];
        this.append(...children);
    }

    remove() {
        if (!this.parentElement) return;
        const siblings = this.parentElement.children;
        const index = siblings.indexOf(this);
        if (index !== -1) siblings.splice(index, 1);
        this.parentElement = null;
        this.setConnected(false);
    }

    setAttribute(name, value) {
        this.attributes.set(String(name), String(value));
    }

    getAttribute(name) {
        return this.attributes.get(String(name)) ?? null;
    }

    addEventListener() {}

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
        const className = String(selector).startsWith('.') ? String(selector).slice(1) : '';
        const matches = [];
        const visit = (element) => {
            element.children.forEach((child) => {
                if (className && child.classList.contains(className)) matches.push(child);
                visit(child);
            });
        };
        visit(this);
        return matches;
    }
}

function createResponse({ status = 200, json, blob, contentType = 'application/json' }) {
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: { get: () => contentType },
        json: async () => json,
        blob: async () => blob,
    };
}

function createPdfPreviewRuntime({ pageImageStatus = 200 } = {}) {
    const requests = [];
    const revokedUrls = [];
    const errors = [];
    let objectUrlSequence = 0;
    const decodeImage = async () => {};
    const document = {
        createElement: (tagName) => new FakeElement(tagName, { decodeImage }),
    };
    const window = {
        authedFetch: async (url, options) => {
            requests.push({ url, options });
            if (url.includes('/page-image?')) {
                return createResponse({
                    status: pageImageStatus,
                    blob: new Blob(['png'], { type: 'image/png' }),
                    contentType: 'image/png',
                });
            }
            if (url.includes('/page?')) {
                return createResponse({
                    json: {
                        page: 1,
                        words: [{ text: 'Hello', x: 10, y: 20, width: 30, height: 12, block: 0, line: 0 }],
                    },
                });
            }
            return createResponse({ json: { pages: [{ page: 1, width: 300, height: 400 }] } });
        },
    };
    class ImmediateIntersectionObserver {
        constructor(callback) {
            this.callback = callback;
        }

        observe(target) {
            this.callback([{ target, isIntersecting: true }]);
        }

        unobserve() {}

        disconnect() {}
    }
    class FakeResizeObserver {
        observe() {}

        disconnect() {}
    }
    const context = vm.createContext({
        AbortController,
        Blob,
        DOMException,
        IntersectionObserver: ImmediateIntersectionObserver,
        ResizeObserver: FakeResizeObserver,
        URL: {
            createObjectURL: () => `blob:pdf-page-${++objectUrlSequence}`,
            revokeObjectURL: (url) => revokedUrls.push(url),
        },
        URLSearchParams,
        console: { error: (error) => errors.push(error) },
        document,
        window,
    });
    vm.runInContext(fs.readFileSync(PDF_PREVIEW_PATH, 'utf8'), context, { filename: PDF_PREVIEW_PATH });
    const module = context.__omlorixCanvasWidgetModules.pdfPreview.create({
        t: (_key, fallback) => fallback,
        formatT: (_key, fallback, values) => fallback.replace('{page}', String(values.page)),
    });
    return { errors, module, requests, revokedUrls };
}

async function waitFor(predicate) {
    for (let attempt = 0; attempt < 20; attempt += 1) {
        if (predicate()) return;
        await new Promise((resolve) => setImmediate(resolve));
    }
    assert.fail('condition was not reached');
}

test('PDF-only Canvas wrappers cannot inherit a hidden code view', () => {
    const source = fs.readFileSync(CANVAS_WIDGET_PATH, 'utf8');
    const start = source.indexOf('    function updateHtmlViewMode(');
    const end = source.indexOf('\n\n    function setHtmlViewMode(', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    const updateHtmlViewModeSource = source.slice(start, end);
    const updateHtmlViewMode = Function(
        'currentHtmlViewMode',
        `${updateHtmlViewModeSource}\nreturn updateHtmlViewMode;`,
    )('code');
    const pdfWrapper = new FakeElement('div');
    pdfWrapper.className = 'canvas-html-preview-wrapper code-view';
    pdfWrapper.dataset.contentType = 'pdf';

    updateHtmlViewMode(pdfWrapper, false);

    assert.equal(pdfWrapper.classList.contains('preview-view'), true);
    assert.equal(pdfWrapper.classList.contains('code-view'), false);

    const htmlWrapper = new FakeElement('div');
    htmlWrapper.className = 'canvas-html-preview-wrapper preview-view';
    htmlWrapper.dataset.contentType = 'html';
    updateHtmlViewMode(htmlWrapper, true);
    assert.equal(htmlWrapper.classList.contains('code-view'), true);
});

test('PDF pages load their image through authenticated fetch before becoming ready', async () => {
    const runtime = createPdfPreviewRuntime();
    const viewer = new FakeElement('div');
    viewer.setConnected(true);

    await runtime.module.renderSelectablePdfPreviewInto(viewer, 'file id');
    const pageShell = viewer.querySelector('.canvas-pdf-page-shell');
    await waitFor(() => pageShell?.dataset.loadState === 'loaded');

    assert.deepEqual(
        runtime.requests.map(({ url }) => url.split('?')[0]),
        [
            '/api/v1/files/pdf/preview',
            '/api/v1/files/pdf/preview/page',
            '/api/v1/files/pdf/preview/page-image',
        ],
    );
    const imageRequest = runtime.requests.find(({ url }) => url.includes('/page-image?'));
    assert.equal(imageRequest.options.credentials, 'include');
    assert.equal(imageRequest.options.headers.accept, 'image/png');
    assert.equal(pageShell.getAttribute('aria-busy'), 'false');
    assert.equal(pageShell.querySelector('.canvas-pdf-page-status'), null);
    assert.equal(pageShell.querySelector('.canvas-pdf-page-image').src, 'blob:pdf-page-1');
    assert.ok(pageShell.querySelector('.canvas-pdf-text-layer'));

    runtime.module.resetSelectablePdfPreviewRendering();
    assert.deepEqual(runtime.revokedUrls, ['blob:pdf-page-1']);
});

test('a failed PDF page image remains a visible translated error instead of a blank loaded page', async () => {
    const runtime = createPdfPreviewRuntime({ pageImageStatus: 401 });
    const viewer = new FakeElement('div');
    viewer.setConnected(true);

    await runtime.module.renderSelectablePdfPreviewInto(viewer, 'file-id');
    const pageShell = viewer.querySelector('.canvas-pdf-page-shell');
    await waitFor(() => pageShell?.dataset.loadState === 'error');

    assert.equal(pageShell.getAttribute('aria-busy'), 'false');
    assert.equal(pageShell.querySelector('.canvas-pdf-page-image'), null);
    const pageStatus = pageShell.querySelector('.canvas-pdf-page-status');
    assert.equal(pageStatus.getAttribute('role'), 'alert');
    assert.equal(pageStatus.textContent, 'Failed to load preview');
    assert.equal(runtime.errors.length, 1);
});
