const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function createClassList() {
    const values = new Set();
    return {
        add(...names) {
            names.forEach((name) => values.add(name));
        },
        remove(...names) {
            names.forEach((name) => values.delete(name));
        },
        toggle(name, force) {
            const enabled = force == null ? !values.has(name) : Boolean(force);
            if (enabled) {
                values.add(name);
            } else {
                values.delete(name);
            }
            return enabled;
        },
        contains(name) {
            return values.has(name);
        },
    };
}

class FakeElement {
    constructor(tagName) {
        this.tagName = tagName.toUpperCase();
        this.children = [];
        this.attributes = {};
        this.classList = createClassList();
        this.dataset = {};
        this.style = {};
        this.hidden = false;
        this.isConnected = true;
        this.textContent = '';
        this.clientWidth = 640;
        this.clientHeight = 480;
        this.contentWindow = tagName === 'iframe' ? { postMessage() {} } : null;
        this._listeners = new Map();
        this._innerHTML = '';
        this.checked = false;
        this.type = '';
        this._src = '';
        this._srcdoc = '';
        this.srcdocAssignments = 0;
    }

    set className(value) {
        this.attributes.class = String(value || '');
    }

    get className() {
        return this.attributes.class || '';
    }

    set src(value) {
        this._src = String(value || '');
        if (this._src) {
            this.attributes.src = this._src;
        } else {
            delete this.attributes.src;
        }
        this.onSrcSet?.(this._src);
    }

    get src() {
        return this._src;
    }

    set srcdoc(value) {
        this.srcdocAssignments += 1;
        this._srcdoc = String(value || '');
        if (this._srcdoc) {
            this.attributes.srcdoc = this._srcdoc;
        } else {
            delete this.attributes.srcdoc;
        }
    }

    get srcdoc() {
        return this._srcdoc;
    }

    set innerHTML(value) {
        this._innerHTML = String(value || '');
        this.children = [];
    }

    get innerHTML() {
        return this._innerHTML;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    removeAttribute(name) {
        delete this.attributes[name];
        if (name === 'src') {
            this._src = '';
        }
        if (name === 'srcdoc') {
            this._srcdoc = '';
        }
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

    addEventListener(type, listener) {
        const listeners = this._listeners.get(type) || [];
        listeners.push(listener);
        this._listeners.set(type, listeners);
    }

    removeEventListener(type, listener) {
        const listeners = this._listeners.get(type) || [];
        this._listeners.set(type, listeners.filter((item) => item !== listener));
    }

    click() {
        const listeners = this._listeners.get('click') || [];
        listeners.forEach((listener) => listener({ type: 'click', target: this }));
    }

    focus() {
        this.focused = true;
    }
}

function stripHtmlCspMetaTags(html) {
    return String(html || '').replace(/<meta\b(?=[^>]*\bhttp-equiv\s*=\s*(?:"content-security-policy"|'content-security-policy'|content-security-policy))[^>]*>/gi, '');
}

function injectHeadMarkup(html, additions) {
    const source = String(html || '');
    const headMatch = source.match(/<head[^>]*>/i);
    if (headMatch) {
        const index = source.indexOf(headMatch[0]) + headMatch[0].length;
        return source.slice(0, index) + additions + source.slice(index);
    }
    const htmlMatch = source.match(/<html[^>]*>/i);
    if (htmlMatch) {
        const index = source.indexOf(htmlMatch[0]) + htmlMatch[0].length;
        return source.slice(0, index) + `<head>${additions}</head>` + source.slice(index);
    }
    return `<head>${additions}</head>${source}`;
}

function normalizeCspSources(values) {
    const input = Array.isArray(values) ? values : (values ? [values] : []);
    const sourcePattern = /^(?:(?:[a-z][a-z0-9+.-]*):\/\/)?(?:\*|\*\.[a-z0-9-]+(?:\.[a-z0-9-]+)*|(?:[a-z0-9-]+|\[[0-9a-f:.]+\])(?:\.[a-z0-9-]+)*)(?::(?:\*|[0-9]{1,5}))?(?:\/[^\s;,"'<>]*)?$/i;
    return input
        .map((item) => String(item || '').trim())
        .filter((source) => {
            if (!source || /[\s;,"'<>]/.test(source)) return false;
            return source === "'self'" || source === "'none'" || /^[a-z][a-z0-9+.-]*:$/i.test(source) || sourcePattern.test(source);
        });
}

function buildFrameCsp(meta) {
    const csp = meta && typeof meta === 'object' && meta.csp && typeof meta.csp === 'object' ? meta.csp : {};
    const resourceDomains = normalizeCspSources(csp.resourceDomains || csp.resource_domains);
    const connectDomains = normalizeCspSources(csp.connectDomains || csp.connect_domains);
    const frameDomains = normalizeCspSources(csp.frameDomains || csp.frame_domains);
    const baseUriDomains = normalizeCspSources(csp.baseUriDomains || csp.base_uri_domains);
    const resourceSrc = ["'self'", 'data:', 'blob:', ...resourceDomains].join(' ');
    const scriptSrc = ["'self'", "'unsafe-inline'", 'blob:', ...resourceDomains].join(' ');
    const styleSrc = ["'self'", "'unsafe-inline'", ...resourceDomains].join(' ');
    return [
        "default-src 'none'",
        "object-src 'none'",
        `script-src ${scriptSrc}`,
        `style-src ${styleSrc}`,
        `img-src ${resourceSrc}`,
        `font-src ${resourceSrc}`,
        `media-src ${resourceSrc}`,
        `connect-src ${connectDomains.length ? connectDomains.join(' ') : "'none'"}`,
        `frame-src ${frameDomains.length ? frameDomains.join(' ') : "'none'"}`,
        `child-src ${frameDomains.length ? frameDomains.join(' ') : "'none'"}`,
        'worker-src blob:',
        `base-uri ${baseUriDomains.length ? baseUriDomains.join(' ') : "'none'"}`,
        "form-action 'none'",
    ].join('; ');
}

function buildServedFrameHtml(html, meta) {
    const csp = buildFrameCsp(meta);
    return injectHeadMarkup(stripHtmlCspMetaTags(html), [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        `<meta http-equiv="Content-Security-Policy" content="${String(csp).replace(/"/g, '&quot;')}">`,
    ].join(''));
}

function createHarness({
    html,
    resourceMeta,
    responseResourceMeta,
    expireFirstResourceRead = false,
    chatMessageResult = {},
}) {
    const requests = [];
    const postedMessages = [];
    const sentChatMessages = [];
    const windowListeners = new Map();
    let iframe = null;
    const localStorageStore = new Map();
    const frameHtmlStore = new Map();
    let nextFrameId = 0;
    let expiredResourceReadReturned = false;
    const document = {
        documentElement: { dataset: {} },
        body: new FakeElement('body'),
        createElement(tagName) {
            const element = new FakeElement(tagName);
            if (tagName === 'iframe') {
                iframe = element;
                iframe.contentWindow.postMessage = (message) => postedMessages.push(message);
                iframe.onSrcSet = (src) => {
                    if (String(src || '') !== '/api/v1/llm/mcp/apps/sandbox-proxy') return;
                    setTimeout(() => {
                        (windowListeners.get('message') || []).forEach((listener) => listener({
                            source: iframe.contentWindow,
                            data: {
                                jsonrpc: '2.0',
                                method: 'ui/notifications/sandbox-proxy-ready',
                                params: {},
                            },
                        }));
                    }, 0);
                };
            }
            return element;
        },
    };
    const window = {
        applicationName: 'Omlorix',
        ChatSanitizer: { isSafeUrl: (url) => /^https?:\/\//i.test(String(url || '')) },
        crypto: {
            getRandomValues(bytes) {
                for (let i = 0; i < bytes.length; i += 1) {
                    bytes[i] = i + 1;
                }
                return bytes;
            },
        },
        addEventListener(type, listener) {
            const listeners = windowListeners.get(type) || [];
            listeners.push(listener);
            windowListeners.set(type, listeners);
        },
        sendChatMessage: async (message) => {
            sentChatMessages.push(message);
            return typeof chatMessageResult === 'function'
                ? chatMessageResult(message)
                : chatMessageResult;
        },
        showWarningConfirm: async () => true,
        open() {},
        localStorage: {
            getItem(key) {
                return localStorageStore.has(key) ? localStorageStore.get(key) : null;
            },
            setItem(key, value) {
                localStorageStore.set(key, String(value));
            },
            removeItem(key) {
                localStorageStore.delete(key);
            },
        },
        authedFetch: async (requestPath, options) => {
            const body = JSON.parse(options.body);
            requests.push({ path: requestPath, body });
            if (
                expireFirstResourceRead
                && requestPath === '/api/v1/llm/mcp/apps/resources/read'
                && !expiredResourceReadReturned
            ) {
                expiredResourceReadReturned = true;
                return {
                    ok: false,
                    status: 403,
                    text: async () => '{"detail":"MCP app access token expired."}',
                };
            }
            if (requestPath === '/api/v1/llm/mcp/apps/token/refresh') {
                return {
                    ok: true,
                    json: async () => ({ app_access_token: 'token-refreshed' }),
                    text: async () => '',
                };
            }
            if (requestPath === '/api/v1/llm/mcp/apps/frame') {
                nextFrameId += 1;
                const frameUrl = `/api/v1/llm/mcp/apps/frame/test-frame-${nextFrameId}`;
                frameHtmlStore.set(frameUrl, buildServedFrameHtml(body.html, body.resource_meta));
                return {
                    ok: true,
                    json: async () => ({ frame_id: `test-frame-${nextFrameId}`, frame_url: frameUrl }),
                    text: async () => '',
                };
            }
            return {
                ok: true,
                json: async () => ({
                    contents: [
                        {
                            mimeType: 'text/html',
                            uri: 'ui://test-app',
                            text: html,
                            _meta: { ui: responseResourceMeta === undefined ? resourceMeta : responseResourceMeta },
                        },
                    ],
                }),
                text: async () => '',
            };
        },
    };
    const context = {
        AbortController,
        Array,
        Boolean,
        Error,
        Intl,
        JSON,
        Map,
        Math,
        MutationObserver: class {
            observe() {}
        },
        Number,
        Object,
        Promise,
        RegExp,
        Set,
        String,
        Uint8Array,
        WeakMap,
        atob: (value) => Buffer.from(value, 'base64').toString('binary'),
        btoa: (value) => Buffer.from(value, 'binary').toString('base64'),
        console,
        document,
        navigator: { language: 'en-US' },
        setTimeout,
        window,
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'mcpAppsWidget.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'mcpAppsWidget.js' });
    return {
        frameHtmlStore,
        iframe: () => iframe,
        localStorageStore,
        postedMessages,
        requests,
        sentChatMessages,
        dispatchIframeMessage(data) {
            (windowListeners.get('message') || []).forEach((listener) => listener({
                source: iframe?.contentWindow,
                data,
            }));
        },
        widgetWrapper: new FakeElement('div'),
        window,
    };
}

function flushMount() {
    // Allow both the async resource fetch and the proxy-ready task to settle.
    return new Promise((resolve) => setTimeout(resolve, 5));
}

function findElements(root, predicate, results = []) {
    if (!root) return results;
    if (predicate(root)) {
        results.push(root);
    }
    (root.children || []).forEach((child) => findElements(child, predicate, results));
    return results;
}

function findButtonByText(root, text) {
    return findElements(root, (element) => element.tagName === 'BUTTON' && element.textContent === text)[0] || null;
}

function findInputByType(root, type) {
    return findElements(root, (element) => element.tagName === 'INPUT' && element.type === type)[0] || null;
}

function findText(root, text) {
    return findElements(root, (element) => element.textContent === text)[0] || null;
}

function loadedHtml(harness) {
    const message = [...harness.postedMessages]
        .reverse()
        .find((candidate) => candidate.method === 'ui/notifications/sandbox-resource-ready');
    return harness.frameHtmlStore.get(String(message?.params?.url || '')) || '';
}

function unsignedToken(payload) {
    const json = JSON.stringify(payload);
    const encoded = Buffer.from(json, 'utf8')
        .toString('base64')
        .replace(/\+/g, '-')
        .replace(/\//g, '_')
        .replace(/=+$/g, '');
    return `${encoded}.test-signature`;
}

test('mounts MCP app resources through the server-hosted sandbox proxy with guarded CSP', async () => {
    const harness = createHarness({
        html: '<html><head></head><body><script nonce="app">window.ready = true;</script></body></html>',
        resourceMeta: {
            csp: {
                resourceDomains: [
                    'https://cdn.example.com',
                    'https://evil.example; script-src *',
                ],
                connectDomains: [
                    'https://api.example.com',
                    'https://evil.example; connect-src *',
                ],
            },
        },
    });
    const { iframe, requests, widgetWrapper, window } = harness;

    assert.equal(window.mcpAppsWidget.renderWidget(widgetWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            app_access_token: 'token-1',
        },
    }), true);
    await flushMount();

    assert.equal(requests[0].body.app_access_token, 'token-1');
    assert.equal(loadedHtml(harness), '');
    assert.ok(findText(widgetWrapper, 'cdn.example.com'));
    assert.ok(findText(widgetWrapper, 'api.example.com'));
    assert.ok(findText(widgetWrapper, 'Remember for this MCP server and these domains'));
    findButtonByText(widgetWrapper, 'Allow connections').click();
    await flushMount();

    assert.equal(iframe().attributes.sandbox, 'allow-scripts allow-same-origin');
    assert.equal(iframe().attributes.allow, 'fullscreen *');
    assert.equal(iframe().src, '/api/v1/llm/mcp/apps/sandbox-proxy');
    assert.equal(iframe().srcdoc, '');
    assert.equal(iframe().srcdocAssignments, 0);
    assert.match(
        harness.postedMessages.find((message) => message.method === 'ui/notifications/sandbox-resource-ready').params.url,
        /^\/api\/v1\/llm\/mcp\/apps\/frame\/test-frame-/
    );
    assert.match(loadedHtml(harness), /Content-Security-Policy/);
    assert.match(loadedHtml(harness), /https:\/\/cdn\.example\.com/);
    assert.doesNotMatch(loadedHtml(harness), /script-src \*/);
    assert.doesNotMatch(loadedHtml(harness), /connect-src \*/);
    assert.doesNotMatch(loadedHtml(harness), /https:\/\/evil\.example;/);
    assert.match(loadedHtml(harness), /script-src[^"]*'unsafe-inline'/);
    assert.match(loadedHtml(harness), /<script nonce="app">/);
});

test('accepts snake_case MCP app CSP metadata fields', async () => {
    const harness = createHarness({
        html: '<html><head><meta http-equiv="Content-Security-Policy" content="default-src none; script-src none; style-src self"></head><body><script>window.ready = true;</script></body></html>',
        resourceMeta: {
            csp: {
                resource_domains: ['https://esm.sh'],
                connect_domains: ['https://api.example.com'],
                frame_domains: ['https://frame.example.com'],
                base_uri_domains: ['https://base.example.com'],
            },
        },
    });
    const { requests, widgetWrapper, window } = harness;

    assert.equal(window.mcpAppsWidget.renderWidget(widgetWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            app_access_token: 'token-1',
        },
    }), true);
    await flushMount();

    assert.equal(loadedHtml(harness), '');
    assert.ok(findText(widgetWrapper, 'esm.sh'));
    findButtonByText(widgetWrapper, 'Allow connections').click();
    await flushMount();

    assert.doesNotMatch(loadedHtml(harness), /script-src none/);
    assert.doesNotMatch(loadedHtml(harness), /style-src self/);
    assert.match(loadedHtml(harness), /style-src[^"]*https:\/\/esm\.sh/);
    assert.match(loadedHtml(harness), /font-src[^"]*https:\/\/esm\.sh/);
    assert.match(loadedHtml(harness), /connect-src https:\/\/api\.example\.com/);
    assert.match(loadedHtml(harness), /frame-src https:\/\/frame\.example\.com/);
    assert.match(loadedHtml(harness), /base-uri https:\/\/base\.example\.com/);
});

test('falls back to widget resource metadata when resource read metadata is empty', async () => {
    const harness = createHarness({
        html: '<html><head></head><body><script>window.ready = true;</script></body></html>',
        resourceMeta: {
            csp: {
                resourceDomains: ['https://esm.sh'],
            },
        },
        responseResourceMeta: {},
    });
    const { widgetWrapper, window } = harness;

    assert.equal(window.mcpAppsWidget.renderWidget(widgetWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            resource_meta: {
                csp: {
                    resourceDomains: ['https://esm.sh'],
                },
            },
            app_access_token: 'token-1',
        },
    }), true);
    await flushMount();

    assert.equal(loadedHtml(harness), '');
    assert.ok(findText(widgetWrapper, 'esm.sh'));
    findButtonByText(widgetWrapper, 'Allow connections').click();
    await flushMount();

    assert.match(loadedHtml(harness), /style-src[^"]*https:\/\/esm\.sh/);
    assert.match(loadedHtml(harness), /font-src[^"]*https:\/\/esm\.sh/);
});

test('keeps MCP app iframe unloaded when external resource consent is cancelled', async () => {
    const harness = createHarness({
        html: '<html><head></head><body><script>window.ready = true;</script></body></html>',
        resourceMeta: {
            csp: {
                resourceDomains: ['https://cdn.example.com'],
            },
        },
    });
    const { widgetWrapper, window } = harness;

    assert.equal(window.mcpAppsWidget.renderWidget(widgetWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            app_access_token: 'token-1',
        },
    }), true);
    await flushMount();

    findButtonByText(widgetWrapper, 'Cancel').click();

    assert.equal(loadedHtml(harness), '');
    assert.equal(harness.iframe().src, 'about:blank');
    assert.equal(harness.iframe().srcdocAssignments, 0);
    assert.ok(findText(widgetWrapper, 'External connections were not allowed.'));
});

test('refreshes an expired MCP app token and retries resource mount once', async () => {
    const harness = createHarness({
        html: '<html><head></head><body><script>window.ready = true;</script></body></html>',
        resourceMeta: {},
        expireFirstResourceRead: true,
    });
    const { requests, widgetWrapper, window } = harness;

    assert.equal(window.mcpAppsWidget.renderWidget(widgetWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            app_access_token: 'token-expired',
            tool_call_id: 'call-1',
        },
    }), true);
    await flushMount();

    assert.deepEqual(requests.map((request) => request.path), [
        '/api/v1/llm/mcp/apps/resources/read',
        '/api/v1/llm/mcp/apps/token/refresh',
        '/api/v1/llm/mcp/apps/resources/read',
        '/api/v1/llm/mcp/apps/frame',
    ]);
    assert.equal(requests[2].body.app_access_token, 'token-refreshed');
    assert.equal(requests[3].body.app_access_token, 'token-refreshed');
    assert.match(loadedHtml(harness), /Content-Security-Policy/);
});

test('refreshes an expired MCP app token before first resource read', async () => {
    const harness = createHarness({
        html: '<html><head></head><body><script>window.ready = true;</script></body></html>',
        resourceMeta: {},
    });
    const { requests, widgetWrapper, window } = harness;
    const expiredToken = unsignedToken({
        exp: Math.floor(Date.now() / 1000) - 1,
    });

    assert.equal(window.mcpAppsWidget.renderWidget(widgetWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            app_access_token: expiredToken,
            tool_call_id: 'call-1',
        },
    }), true);
    await flushMount();

    assert.deepEqual(requests.map((request) => request.path), [
        '/api/v1/llm/mcp/apps/token/refresh',
        '/api/v1/llm/mcp/apps/resources/read',
        '/api/v1/llm/mcp/apps/frame',
    ]);
    assert.equal(requests[1].body.app_access_token, 'token-refreshed');
    assert.equal(requests[2].body.app_access_token, 'token-refreshed');
    assert.match(loadedHtml(harness), /Content-Security-Policy/);
});

test('negotiates spec host capabilities and dispatches ui messages into chat', async () => {
    const harness = createHarness({
        html: '<html><body>App</body></html>',
        resourceMeta: {},
    });
    const { dispatchIframeMessage, postedMessages, sentChatMessages, widgetWrapper, window } = harness;

    assert.equal(window.mcpAppsWidget.renderWidget(widgetWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            app_access_token: 'token-1',
        },
    }), true);
    await flushMount();

    dispatchIframeMessage({
        jsonrpc: '2.0',
        id: 1,
        method: 'ui/initialize',
        params: {
            protocolVersion: '2026-01-26',
            appCapabilities: { availableDisplayModes: ['inline'] },
        },
    });
    dispatchIframeMessage({
        jsonrpc: '2.0',
        id: 2,
        method: 'ui/message',
        params: { role: 'user', content: { type: 'text', text: 'Continue with this selection' } },
    });
    await flushMount();

    const initializeResponse = postedMessages.find((message) => message.id === 1);
    assert.ok(initializeResponse.result.hostCapabilities.serverTools);
    assert.ok(initializeResponse.result.hostCapabilities.serverResources);
    assert.ok(initializeResponse.result.hostCapabilities.sandbox);
    assert.ok(initializeResponse.result.hostCapabilities.message.text);
    assert.ok(initializeResponse.result.hostCapabilities.message.structuredContent);
    assert.equal(initializeResponse.result.hostCapabilities.resources, undefined);
    assert.deepEqual(sentChatMessages, ['Continue with this selection']);
    assert.equal(Object.keys(postedMessages.find((message) => message.id === 2).result).length, 0);
});

test('rejects unsupported MCP Apps protocol versions without initializing', async () => {
    const harness = createHarness({
        html: '<html><body>App</body></html>',
        resourceMeta: {},
    });
    const { dispatchIframeMessage, postedMessages, widgetWrapper, window } = harness;

    assert.equal(window.mcpAppsWidget.renderWidget(widgetWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            app_access_token: 'token-1',
        },
    }), true);
    await flushMount();

    dispatchIframeMessage({
        jsonrpc: '2.0',
        id: 9,
        method: 'ui/initialize',
        params: { protocolVersion: '1999-01-01' },
    });

    const response = postedMessages.find((message) => message.id === 9);
    assert.equal(response.result, undefined);
    assert.equal(response.error.code, -32602);
    assert.match(response.error.message, /unsupported/i);
});

test('negotiates the deployed MCP Apps 2025-11-21 compatibility version', async () => {
    const harness = createHarness({
        html: '<html><body>Legacy app</body></html>',
        resourceMeta: {},
    });
    const { dispatchIframeMessage, postedMessages, widgetWrapper, window } = harness;

    assert.equal(window.mcpAppsWidget.renderWidget(widgetWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            app_access_token: 'token-1',
        },
    }), true);
    await flushMount();

    dispatchIframeMessage({
        jsonrpc: '2.0',
        id: 11,
        method: 'ui/initialize',
        params: { protocolVersion: '2025-11-21' },
    });

    const response = postedMessages.find((message) => message.id === 11);
    assert.equal(response.error, undefined);
    assert.equal(response.result.protocolVersion, '2025-11-21');
    assert.ok(response.result.hostCapabilities.serverTools);
});

test('requests resource teardown before destroying an initialized app', async () => {
    const harness = createHarness({
        html: '<html><body>App</body></html>',
        resourceMeta: {},
    });
    const { dispatchIframeMessage, postedMessages, widgetWrapper, window } = harness;

    assert.equal(window.mcpAppsWidget.renderWidget(widgetWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            app_access_token: 'token-1',
        },
    }), true);
    await flushMount();
    dispatchIframeMessage({
        jsonrpc: '2.0',
        id: 10,
        method: 'ui/initialize',
        params: { protocolVersion: '2026-01-26' },
    });

    const widgetId = widgetWrapper.dataset.mcpAppWidgetId;
    assert.equal(window.mcpAppsWidget.destroyWidget(widgetId), true);

    const teardown = postedMessages.find((message) => message.method === 'ui/resource-teardown');
    assert.equal(teardown.params.reason, 'host-destroyed');
    assert.equal(widgetWrapper.dataset.mcpAppWidgetId, undefined);
});

test('returns a protocol error when chat rejects an MCP app message', async () => {
    const harness = createHarness({
        html: '<html><body>App</body></html>',
        resourceMeta: {},
        chatMessageResult: null,
    });
    const { dispatchIframeMessage, postedMessages, sentChatMessages, widgetWrapper, window } = harness;

    assert.equal(window.mcpAppsWidget.renderWidget(widgetWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            app_access_token: 'token-1',
        },
    }), true);
    await flushMount();

    dispatchIframeMessage({
        jsonrpc: '2.0',
        id: 1,
        method: 'ui/message',
        params: { role: 'user', content: { type: 'text', text: 'Try while busy' } },
    });
    await flushMount();

    assert.deepEqual(sentChatMessages, ['Try while busy']);
    const response = postedMessages.find((message) => message.id === 1);
    assert.equal(response.result, undefined);
    assert.equal(response.error.code, -32000);
    assert.match(response.error.message, /unavailable/i);
});

test('remembers approved MCP app external resources for the same server and source set', async () => {
    const harness = createHarness({
        html: '<html><head></head><body><script>window.ready = true;</script></body></html>',
        resourceMeta: {
            csp: {
                resourceDomains: ['https://cdn.example.com'],
            },
        },
    });
    const { localStorageStore, widgetWrapper, window } = harness;

    assert.equal(window.mcpAppsWidget.renderWidget(widgetWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            app_access_token: 'token-1',
        },
    }), true);
    await flushMount();

    const rememberCheckbox = findInputByType(widgetWrapper, 'checkbox');
    rememberCheckbox.checked = true;
    findButtonByText(widgetWrapper, 'Allow connections').click();
    await flushMount();
    assert.match(loadedHtml(harness), /Content-Security-Policy/);
    assert.equal(localStorageStore.size, 1);

    const secondWrapper = new FakeElement('div');
    assert.equal(window.mcpAppsWidget.renderWidget(secondWrapper, {
        mcp_app: {
            server_id: 'server-1',
            resource_uri: 'ui://test-app',
            app_access_token: 'token-2',
        },
    }), true);
    await flushMount();

    assert.match(loadedHtml(harness), /Content-Security-Policy/);
    assert.equal(findButtonByText(secondWrapper, 'Allow connections'), null);
});
