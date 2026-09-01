const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const CHAT_DIR = __dirname;
const CHATS_SOURCE = fs.readFileSync(path.join(CHAT_DIR, 'chats.js'), 'utf8');
const WORKSPACE_SOURCE = fs.readFileSync(path.join(CHAT_DIR, 'workspace.js'), 'utf8');
const SCRIPT_SOURCE = fs.readFileSync(path.join(CHAT_DIR, 'script.js'), 'utf8');
const CHAT_CSS = fs.readFileSync(path.join(CHAT_DIR, '../../css/chat/chat.css'), 'utf8');
const ANIMATIONS_CSS = fs.readFileSync(path.join(CHAT_DIR, '../../css/common/animations.css'), 'utf8');
const I18N_ROOT = path.join(CHAT_DIR, '../../i18n');

function extractFunction(source, functionName) {
    const asyncStart = source.indexOf(`async function ${functionName}(`);
    const plainStart = source.indexOf(`function ${functionName}(`);
    const start = asyncStart >= 0 ? asyncStart : plainStart;
    assert.notEqual(start, -1, `expected ${functionName}`);
    const bodyStart = source.indexOf(') {', start) + 2;
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
    }
    throw new Error(`Could not extract ${functionName}`);
}

function extractObjectMethod(source, signature) {
    const start = source.indexOf(signature);
    assert.notEqual(start, -1, `expected ${signature}`);
    const bodyStart = source.indexOf('{', start);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) {
            return source.slice(start, index + 1).replace(signature, 'async function openBookmarkInChat(chatId, messageId)');
        }
    }
    throw new Error(`Could not extract ${signature}`);
}

function createClassList() {
    const values = new Set();
    return {
        add(value) { values.add(value); },
        remove(value) { values.delete(value); },
        contains(value) { return values.has(value); },
    };
}

function createMessageElement({ id = '', dataset = {}, display = '', parent = null } = {}) {
    const attributes = new Map();
    return {
        id,
        dataset: { ...dataset },
        style: { display },
        classList: createClassList(),
        offsetWidth: 100,
        scrollCalls: [],
        focusCalls: [],
        closest() { return parent; },
        getAttribute(name) { return attributes.get(name) ?? null; },
        hasAttribute(name) { return attributes.has(name); },
        setAttribute(name, value) { attributes.set(name, String(value)); },
        scrollIntoView(options) { this.scrollCalls.push(options); },
        focus(options) { this.focusCalls.push(options || null); },
    };
}

function createFocusRuntime({ userAnchor = null, assistantContainers = [] } = {}) {
    const versionSwitches = [];
    const elements = new Map();
    if (userAnchor) elements.set(userAnchor.id, userAnchor);
    const transcript = {
        querySelectorAll(selector) {
            assert.equal(selector, '.assistant-message-container');
            return assistantContainers;
        },
    };
    elements.set('chatAreaContainer', transcript);
    const context = {
        Array,
        Number,
        String,
        clearTimeout,
        document: {
            getElementById(id) { return elements.get(id) || null; },
        },
        highlightedChatMessage: null,
        highlightedChatMessageTimer: null,
        requestAnimationFrame(callback) { callback(); },
        setTimeout() { return 1; },
        updateScrollButtonVisibility() {},
        window: {
            switchAssistantVersion(referenceId, retryCount) {
                versionSwitches.push({ referenceId, retryCount });
                assistantContainers.forEach((container) => {
                    if (container.dataset.referenceId === referenceId) {
                        const selected = Number(container.dataset.retryCount) === retryCount;
                        container.style.display = selected ? '' : 'none';
                        container.dataset.hidden = selected ? 'false' : 'true';
                    }
                });
            },
        },
    };
    vm.runInNewContext([
        extractFunction(CHATS_SOURCE, 'resolveChatMessageFocusTarget'),
        extractFunction(CHATS_SOURCE, 'focusChatMessage'),
        'this.focusChatMessage = focusChatMessage;',
    ].join('\n\n'), context);
    return { focusChatMessage: context.focusChatMessage, versionSwitches };
}

test('user bookmark focus targets the complete user message area', () => {
    const messageArea = createMessageElement({ id: 'user-area' });
    const userAnchor = createMessageElement({ id: 'u-user-1', parent: messageArea });
    const runtime = createFocusRuntime({ userAnchor });

    assert.equal(runtime.focusChatMessage('user-1'), true);
    assert.equal(messageArea.scrollCalls.length, 1);
    assert.equal(messageArea.focusCalls.length, 1);
    assert.equal(messageArea.classList.contains('chat-message-bookmark-target'), true);
});

test('initial assistant response is found by its persisted assistant message id', () => {
    const initialResponse = createMessageElement({
        id: 'a-user-1',
        dataset: {
            assistantMessageId: 'assistant-1',
            referenceId: 'user-1',
            retryCount: '0',
            hidden: 'false',
        },
    });
    const runtime = createFocusRuntime({ assistantContainers: [initialResponse] });

    assert.equal(runtime.focusChatMessage('assistant-1'), true);
    assert.equal(initialResponse.scrollCalls.length, 1);
    assert.deepEqual(runtime.versionSwitches, []);
});

test('bookmarked older regenerated response is selected before it is focused', () => {
    const original = createMessageElement({
        id: 'a-user-1',
        dataset: { assistantMessageId: 'assistant-1', referenceId: 'user-1', retryCount: '0', hidden: 'true' },
        display: 'none',
    });
    const latest = createMessageElement({
        id: 'a-assistant-2',
        dataset: { assistantMessageId: 'assistant-2', referenceId: 'user-1', retryCount: '1', hidden: 'false' },
    });
    const runtime = createFocusRuntime({ assistantContainers: [original, latest] });

    assert.equal(runtime.focusChatMessage('assistant-1'), true);
    assert.deepEqual(runtime.versionSwitches, [{ referenceId: 'user-1', retryCount: 0 }]);
    assert.equal(original.style.display, '');
    assert.equal(original.scrollCalls.length, 1);
    assert.equal(latest.style.display, 'none');
});

test('latest regenerated assistant response focuses without switching versions', () => {
    const latest = createMessageElement({
        id: 'a-assistant-2',
        dataset: { assistantMessageId: 'assistant-2', referenceId: 'user-1', retryCount: '1', hidden: 'false' },
    });
    const runtime = createFocusRuntime({ assistantContainers: [latest] });

    assert.equal(runtime.focusChatMessage('assistant-2'), true);
    assert.deepEqual(runtime.versionSwitches, []);
    assert.equal(latest.scrollCalls.length, 1);
});

test('every bookmark card role and click surface uses the canonical chat loader', async () => {
    const calls = [];
    const context = {
        String,
        window: {
            async loadChatView(chatId, streaming, options) {
                calls.push({ chatId, streaming, options });
                return true;
            },
            async restoreProjectSidebarForChat(chatId) {
                calls.push({ restore: chatId });
            },
        },
    };
    vm.runInNewContext(
        `${extractObjectMethod(WORKSPACE_SOURCE, 'async openBookmarkInChat(chatId, messageId)')}\nthis.openBookmarkInChat = openBookmarkInChat;`,
        context,
    );

    for (const bookmark of [
        { role: 'user', chatId: 'chat-user', messageId: 'user-1' },
        { role: 'assistant', chatId: 'chat-assistant', messageId: 'assistant-1' },
    ]) {
        assert.equal(await context.openBookmarkInChat(bookmark.chatId, bookmark.messageId), true, bookmark.role);
    }

    assert.deepEqual(JSON.parse(JSON.stringify(calls.filter((call) => call.chatId))), [
        { chatId: 'chat-user', streaming: false, options: { focusMessageId: 'user-1' } },
        { chatId: 'chat-assistant', streaming: false, options: { focusMessageId: 'assistant-1' } },
    ]);
    assert.match(WORKSPACE_SOURCE, /bookmark-card-open[\s\S]*?void this\.openBookmarkInChat\(bookmark\.chat_id, bookmark\.id\)/);
    assert.match(WORKSPACE_SOURCE, /card\.addEventListener\('click',[\s\S]*?void this\.openBookmarkInChat\(bookmark\.chat_id, bookmark\.id\)/);
});

test('bookmark navigation handles rejected chat loads and notifies the user', async () => {
    const errors = [];
    const context = {
        String,
        console: { error() {} },
        notifyError(message) { errors.push(message); },
        t(_key, fallback) { return fallback; },
        window: {
            async loadChatView() {
                throw new Error('navigation failed');
            },
        },
    };
    vm.runInNewContext(
        `${extractObjectMethod(WORKSPACE_SOURCE, 'async openBookmarkInChat(chatId, messageId)')}\nthis.openBookmarkInChat = openBookmarkInChat;`,
        context,
    );

    assert.equal(await context.openBookmarkInChat('chat-1', 'message-1'), false);
    assert.deepEqual(errors, ['Failed to open the bookmarked message']);
});

test('bookmark navigation errors are translated in every supported locale', () => {
    const localeDirectories = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);
    for (const locale of localeDirectories) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(I18N_ROOT, locale, 'index.json'), 'utf8'));
        assert.ok(dictionary.bookmarks_open_failed?.trim(), `${locale} must translate bookmarks_open_failed`);
    }
});

test('message-focused routes survive direct load and history restoration', () => {
    const buildChatRoute = extractFunction(CHATS_SOURCE, 'buildChatRoute');
    const extractMessage = extractFunction(CHATS_SOURCE, 'extractChatMessageIdFromSearch');
    const context = { String, URLSearchParams, encodeURIComponent };
    vm.runInNewContext(`${buildChatRoute}\n${extractMessage}\nthis.helpers = { buildChatRoute, extractChatMessageIdFromSearch };`, context);

    assert.equal(
        context.helpers.buildChatRoute('chat / 1', 'message / 2'),
        '/chat/chat%20%2F%201?message=message%20%2F%202',
    );
    assert.equal(context.helpers.extractChatMessageIdFromSearch('?message=message%20%2F%202'), 'message / 2');
    assert.match(SCRIPT_SOURCE, /loadChatView\(chatId, false, \{ focusMessageId, preserveHistory: true \}\)/);
    assert.match(CHATS_SOURCE, /loadChatView\(chatId, false, \{ focusMessageId, preserveHistory: true \}\)/);
});

test('bookmark focus highlight supports reduced motion', () => {
    assert.match(ANIMATIONS_CSS, /@keyframes bookmark-message-highlight/);
    assert.match(CHAT_CSS, /\.chat-message-bookmark-target[\s\S]*animation: bookmark-message-highlight/);
    assert.match(CHAT_CSS, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.chat-message-bookmark-target/);
});
