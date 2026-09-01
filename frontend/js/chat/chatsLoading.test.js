const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const CHATS_PATH = path.join(__dirname, 'chats.js');
const INDEX_PATH = path.join(__dirname, '../../index.html');
const I18N_ROOT = path.join(__dirname, '../../i18n');
const chatsSource = fs.readFileSync(CHATS_PATH, 'utf8');

/** Extract one top-level function without evaluating the rest of the browser bundle. */
function extractFunction(source, functionName) {
    const asyncStart = source.indexOf(`async function ${functionName}(`);
    const plainStart = source.indexOf(`function ${functionName}(`);
    const start = asyncStart >= 0 ? asyncStart : plainStart;
    assert.notEqual(start, -1, `expected ${functionName} in chats.js`);

    // Search after the complete parameter list because default object values
    // such as `options = {}` contain braces before the function body.
    const bodyStart = source.indexOf(') {', start) + 2;
    assert.ok(bodyStart > 1, `expected ${functionName} body`);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
    }
    throw new Error(`Could not extract ${functionName}`);
}

/** Minimal DOM element used by the transcript-loading lifecycle tests. */
function createElement(initialAttributes = {}) {
    const attributes = new Map(Object.entries(initialAttributes));
    return {
        hidden: false,
        innerHTML: '',
        style: {},
        setAttribute(name, value) {
            attributes.set(name, String(value));
        },
        getAttribute(name) {
            return attributes.has(name) ? attributes.get(name) : null;
        },
        hasAttribute(name) {
            return attributes.has(name);
        },
        removeAttribute(name) {
            attributes.delete(name);
        },
        toggleAttribute(name, force) {
            if (force) attributes.set(name, '');
            else attributes.delete(name);
        },
        querySelectorAll() {
            return [];
        },
    };
}

/**
 * Run loadChatView with deterministic browser and network fakes.
 *
 * Loading status updates intentionally mirror setMainChatLoadStatus's ownership
 * attributes so same-chat and retry behavior exercises the production guards.
 */
function createLoadRuntime(fetcher, { pathname = '/workspace' } = {}) {
    const chatContainer = createElement();
    const messagesContainer = createElement();
    messagesContainer.innerHTML = '<div>old transcript</div>';
    const chatArea = createElement();
    const chatBoxArea = createElement();
    const elements = {
        chatContainer,
        chatAreaContainer: messagesContainer,
        chatArea,
        chatBoxArea,
    };
    const statusCalls = [];
    const rendered = [];
    const notifications = [];
    const historyCalls = [];
    const focusCalls = [];
    const delayedScrolls = [];
    let showCount = 0;
    let presentationResetCount = 0;

    const windowObject = {
        location: { pathname, search: '' },
        authedFetch: fetcher,
        resetGenerationUIState() {},
        slidePresentationWidget: {
            reset() {
                presentationResetCount += 1;
            },
        },
        renderChatTranscript(messages) {
            rendered.push(messages);
        },
        ChatAttention: { markRead() {} },
        focusChatInput() {},
    };
    const history = {
        state: null,
        pushState(state, _title, nextPath) {
            this.state = state;
            const nextUrl = new URL(nextPath, 'https://omlorix.test');
            windowObject.location.pathname = nextUrl.pathname;
            windowObject.location.search = nextUrl.search;
            historyCalls.push({ method: 'push', state, path: nextPath });
        },
        replaceState(state, _title, nextPath) {
            this.state = state;
            const nextUrl = new URL(nextPath, 'https://omlorix.test');
            windowObject.location.pathname = nextUrl.pathname;
            windowObject.location.search = nextUrl.search;
            historyCalls.push({ method: 'replace', state, path: nextPath });
        },
    };

    const context = {
        AbortController,
        console,
        document: {
            getElementById(id) {
                return elements[id] || null;
            },
        },
        encodeURIComponent,
        buildChatRoute(chatId, messageId = '') {
            const base = `/chat/${encodeURIComponent(String(chatId).trim())}`;
            return messageId ? `${base}?message=${encodeURIComponent(String(messageId).trim())}` : base;
        },
        focusChatMessage(messageId, options) {
            focusCalls.push({ messageId, options: options || null });
            return true;
        },
        history,
        notifyError(message) {
            notifications.push(message);
        },
        requestAnimationFrame(callback) {
            callback();
        },
        resetChatScrollState() {},
        scrollChatToBottom() {},
        scrollChatToBottomAfterImagesLoad(options) {
            delayedScrolls.push(options);
        },
        sidebarDropdownT(_key, fallback) {
            return fallback;
        },
        sidebarDropdownTf(_key, fallback, vars) {
            return fallback.replace('{status}', String(vars.status));
        },
        setMainChatLoadStatus(status, message = '') {
            statusCalls.push({ status, message });
            chatContainer.toggleAttribute('data-chat-loading', status === 'loading');
            chatContainer.toggleAttribute('data-chat-load-error', status === 'error');
        },
        showChatContainer() {
            showCount += 1;
            chatContainer.style.display = 'flex';
        },
        async checkAndAttachOngoingStream() {},
        updateTabTitleIfActive() {},
        window: windowObject,
    };

    vm.runInNewContext(
        [
            'let activeChatLoadController = null;',
            'let activeChatLoadToken = 0;',
            'let pendingChatMessageFocus = null;',
            extractFunction(chatsSource, 'loadChatView'),
            'this.loadChatView = loadChatView;',
        ].join('\n\n'),
        context,
        { filename: 'chats.js' },
    );

    return {
        chatContainer,
        delayedScrolls,
        focusCalls,
        historyCalls,
        loadChatView: context.loadChatView,
        messagesContainer,
        notifications,
        rendered,
        statusCalls,
        getPresentationResetCount: () => presentationResetCount,
        getShowCount: () => showCount,
    };
}

test('active generation keeps the stop control outside the chat load lockout', () => {
    const chatContainer = createElement({ 'data-active-generation': 'generation-1' });
    const chatArea = createElement();
    const chatBoxArea = createElement();
    const elements = { chatContainer, chatArea, chatBoxArea };
    const context = {
        chatLoadStatusEl: null,
        document: {
            getElementById(id) {
                return elements[id] || null;
            },
        },
    };

    vm.runInNewContext(
        `${extractFunction(chatsSource, 'setMainChatLoadStatus')}\nthis.setMainChatLoadStatus = setMainChatLoadStatus;`,
        context,
        { filename: 'chats.js' },
    );

    context.setMainChatLoadStatus('loading');
    assert.equal(chatBoxArea.hasAttribute('inert'), false);
    assert.equal(chatBoxArea.hasAttribute('aria-disabled'), false);

    chatContainer.removeAttribute('data-active-generation');
    context.setMainChatLoadStatus('error');
    assert.equal(chatBoxArea.hasAttribute('inert'), true);
    assert.equal(chatBoxArea.getAttribute('aria-disabled'), 'true');
});

test('chat shell and route switch before the messages request resolves', async () => {
    let resolveFetch;
    const fetchPromise = new Promise((resolve) => {
        resolveFetch = resolve;
    });
    let requestOptions = null;
    const runtime = createLoadRuntime((_url, options) => {
        requestOptions = options;
        return fetchPromise;
    });

    const loadPromise = runtime.loadChatView('chat / two');

    assert.equal(runtime.getShowCount(), 1, 'the chat shell should be shown synchronously');
    assert.equal(runtime.chatContainer.style.display, 'flex');
    assert.equal(runtime.chatContainer.getAttribute('data-chat-id'), 'chat / two');
    assert.equal(runtime.messagesContainer.innerHTML, '', 'the previous transcript must disappear immediately');
    assert.equal(runtime.getPresentationResetCount(), 1, 'detached presentation state must be cleared before awaiting chat data');
    assert.deepEqual({
        ...runtime.historyCalls[0],
        state: { ...runtime.historyCalls[0].state },
    }, {
        method: 'push',
        state: { chatId: 'chat / two' },
        path: '/chat/chat%20%2F%20two',
    });
    assert.equal(runtime.statusCalls[0].status, 'loading');
    assert.ok(requestOptions.signal instanceof AbortSignal);

    resolveFetch({
        ok: true,
        async json() {
            return [{ id: 'message-1' }];
        },
    });
    assert.equal(await loadPromise, true);
    assert.equal(runtime.rendered.length, 1);
    assert.equal(runtime.statusCalls.at(-1).status, 'idle');
});

test('rapid navigation aborts the obsolete request and only renders the newest chat', async () => {
    const requests = [];
    const runtime = createLoadRuntime((url, options) => new Promise((resolve, reject) => {
        const request = { url, options, resolve, reject };
        requests.push(request);
        options.signal.addEventListener('abort', () => {
            const error = new Error('aborted');
            error.name = 'AbortError';
            reject(error);
        }, { once: true });
    }));

    const firstLoad = runtime.loadChatView('chat-a');
    const secondLoad = runtime.loadChatView('chat-b');

    assert.equal(requests.length, 2);
    assert.equal(requests[0].options.signal.aborted, true);
    requests[1].resolve({
        ok: true,
        async json() {
            return [{ id: 'message-b' }];
        },
    });

    assert.equal(await secondLoad, true);
    assert.equal(await firstLoad, false);
    assert.equal(runtime.chatContainer.getAttribute('data-chat-id'), 'chat-b');
    assert.deepEqual(runtime.rendered, [[{ id: 'message-b' }]]);
    assert.equal(runtime.notifications.length, 0, 'an aborted stale request must stay silent');
});

test('bookmark navigation commits a stable message route and focuses after rendering', async () => {
    const runtime = createLoadRuntime(async () => ({
        ok: true,
        async json() {
            return [{ id: 'assistant version / 1' }];
        },
    }));

    assert.equal(await runtime.loadChatView('archived chat', false, {
        focusMessageId: 'assistant version / 1',
    }), true);

    assert.deepEqual({
        ...runtime.historyCalls[0],
        state: { ...runtime.historyCalls[0].state },
    }, {
        method: 'push',
        state: { chatId: 'archived chat', messageId: 'assistant version / 1' },
        path: '/chat/archived%20chat?message=assistant%20version%20%2F%201',
    });
    assert.equal(runtime.focusCalls[0].messageId, 'assistant version / 1');
    assert.ok(
        runtime.focusCalls.some((call) => call.options?.moveFocus === false),
        'the target should be re-anchored after stream attachment without stealing focus again',
    );
});

test('a bookmark target can be attached to an in-flight same-chat load', async () => {
    let resolveFetch;
    const runtime = createLoadRuntime(() => new Promise((resolve) => {
        resolveFetch = resolve;
    }));

    const initialLoad = runtime.loadChatView('chat-1');
    assert.equal(await runtime.loadChatView('chat-1', false, { focusMessageId: 'message-2' }), true);

    resolveFetch({
        ok: true,
        async json() {
            return [{ id: 'message-2' }];
        },
    });
    assert.equal(await initialLoad, true);
    assert.ok(runtime.focusCalls.some((call) => call.messageId === 'message-2'));
    assert.equal(runtime.historyCalls.at(-1).path, '/chat/chat-1?message=message-2');
});

test('same-chat bookmark navigation returns from Workspace to the visible chat shell', async () => {
    const runtime = createLoadRuntime(async () => ({
        ok: true,
        async json() {
            return [{ id: 'message-1' }];
        },
    }));

    assert.equal(await runtime.loadChatView('chat-1'), true);
    const showCountAfterInitialLoad = runtime.getShowCount();
    assert.equal(await runtime.loadChatView('chat-1', false, { focusMessageId: 'message-1' }), true);

    assert.equal(runtime.getShowCount(), showCountAfterInitialLoad + 1);
    assert.ok(runtime.focusCalls.some((call) => call.messageId === 'message-1'));
});

test('delayed image scrolling expires when the same chat gets a new message target', async () => {
    const runtime = createLoadRuntime(async () => ({
        ok: true,
        async json() {
            return [{ id: 'message-a' }, { id: 'message-b' }];
        },
    }));

    assert.equal(await runtime.loadChatView('chat-1', false, { focusMessageId: 'message-a' }), true);
    assert.equal(runtime.delayedScrolls.length, 1);
    const messageAScroll = runtime.delayedScrolls[0];
    assert.equal(messageAScroll.focusMessageId, 'message-a');
    assert.equal(messageAScroll.isCurrent(), true);

    assert.equal(await runtime.loadChatView('chat-1', false, { focusMessageId: 'message-b' }), true);
    assert.equal(messageAScroll.isCurrent(), false);
});

test('a failed chat remains retryable without clearing its active chat id', async () => {
    let requestCount = 0;
    const runtime = createLoadRuntime(async () => {
        requestCount += 1;
        if (requestCount === 1) {
            return {
                ok: false,
                status: 503,
                async json() {
                    return {};
                },
            };
        }
        return {
            ok: true,
            async json() {
                return [{ id: 'message-after-retry' }];
            },
        };
    });

    assert.equal(await runtime.loadChatView('chat-retry'), false);
    assert.equal(runtime.chatContainer.getAttribute('data-chat-id'), 'chat-retry');
    assert.equal(runtime.chatContainer.hasAttribute('data-chat-load-error'), true);
    assert.equal(runtime.statusCalls.at(-1).status, 'error');

    assert.equal(await runtime.loadChatView('chat-retry'), true);
    assert.equal(requestCount, 2);
    assert.equal(runtime.statusCalls.at(-1).status, 'idle');
    assert.deepEqual(runtime.rendered, [[{ id: 'message-after-retry' }]]);
});

test('loading markup and translations exist for every supported locale', () => {
    const markup = fs.readFileSync(INDEX_PATH, 'utf8');
    assert.match(markup, /id="chatLoadStatus"[^>]*role="status"[^>]*aria-live="polite"/);
    assert.match(markup, /id="chatLoadRetryButton"[^>]*data-i18n="chat_load_retry"/);

    const localeDirectories = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);
    for (const locale of localeDirectories) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(I18N_ROOT, locale, 'index.json'), 'utf8'));
        assert.ok(dictionary.chat_loading_messages?.trim(), `${locale} must translate chat_loading_messages`);
        assert.ok(dictionary.chat_load_retry?.trim(), `${locale} must translate chat_load_retry`);
    }
});
