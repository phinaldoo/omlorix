const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName} in chatBox.js`);

    let signatureDepth = 0;
    let bodyStart = -1;
    for (let index = start; index < source.length; index += 1) {
        const char = source[index];
        if (char === '(') {
            signatureDepth += 1;
        } else if (char === ')') {
            signatureDepth -= 1;
        } else if (char === '{' && signatureDepth === 0) {
            bodyStart = index;
            break;
        }
    }
    assert.notEqual(bodyStart, -1, `expected ${functionName} body`);

    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') {
            depth += 1;
        } else if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }

    throw new Error(`Could not extract ${functionName}`);
}

function createChatContainer() {
    const attributes = new Map();
    return {
        getAttribute(name) {
            return attributes.has(name) ? attributes.get(name) : null;
        },
        setAttribute(name, value) {
            attributes.set(name, String(value));
        },
        removeAttribute(name) {
            attributes.delete(name);
        },
    };
}

function createLocalStorage() {
    const values = new Map();
    return {
        get length() {
            return values.size;
        },
        key(index) {
            return Array.from(values.keys())[index] || null;
        },
        getItem(key) {
            return values.has(key) ? values.get(key) : null;
        },
        setItem(key, value) {
            values.set(key, String(value));
        },
        removeItem(key) {
            values.delete(key);
        },
        snapshot() {
            return new Map(values);
        },
    };
}

function loadDraftHelpers() {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const storageKeyDeclaration = source.match(/const CHAT_INPUT_STORAGE_KEY = .*?;/)?.[0];
    const storageKeyPrefixDeclaration = source.match(/const CHAT_INPUT_STORAGE_KEY_PREFIX = .*?;/)?.[0];
    const storageTtlDeclaration = source.match(/const CHAT_INPUT_DRAFT_TTL_MS = .*?;/)?.[0];
    const storagePruneIntervalDeclaration = source.match(/const CHAT_INPUT_DRAFT_PRUNE_INTERVAL_MS = .*?;/)?.[0];
    assert.ok(storageKeyDeclaration, 'expected draft storage key declaration');
    assert.ok(storageKeyPrefixDeclaration, 'expected draft storage key prefix declaration');
    assert.ok(storageTtlDeclaration, 'expected draft TTL declaration');
    assert.ok(storagePruneIntervalDeclaration, 'expected draft prune interval declaration');

    const chatContainer = createChatContainer();
    const localStorage = createLocalStorage();
    let randomCallCount = 0;
    const context = {
        Date: class MockDate extends Date {
            static now() {
                return 1710000000000;
            }
        },
        Math: {
            random: () => {
                randomCallCount += 1;
                return randomCallCount === 1 ? 0.123456789 : 0.987654321;
            },
        },
        chatContainer,
        document: {
            getElementById(id) {
                return id === 'chatContainer' ? chatContainer : null;
            },
        },
        localStorage,
    };

    vm.runInNewContext(
        [
            storageKeyDeclaration,
            storageKeyPrefixDeclaration,
            storageTtlDeclaration,
            storagePruneIntervalDeclaration,
            'let lastChatInputDraftPruneAt = 0;',
            extractFunction(source, 'getCurrentChatComposerContext'),
            extractFunction(source, 'generateChatInputDraftTempId'),
            extractFunction(source, 'getChatInputDraftTempId'),
            extractFunction(source, 'resetChatInputDraftTempContext'),
            extractFunction(source, 'getCurrentChatInputDraftContext'),
            extractFunction(source, 'createChatInputDraftStorageEntry'),
            extractFunction(source, 'parseChatInputDraftStorageEntry'),
            extractFunction(source, 'isChatInputDraftExpired'),
            extractFunction(source, 'removeExpiredChatInputDrafts'),
            extractFunction(source, 'maybeRemoveExpiredChatInputDrafts'),
            extractFunction(source, 'persistChatInputDraftValue'),
            extractFunction(source, 'readChatInputDraftEntry'),
            'this.helpers = { getCurrentChatInputDraftContext, getChatInputDraftTempId, resetChatInputDraftTempContext, readChatInputDraftEntry, persistChatInputDraftValue, parseChatInputDraftStorageEntry, removeExpiredChatInputDrafts };',
        ].join('\n\n'),
        context,
        { filename: 'chatBox.js' },
    );

    return {
        chatContainer,
        helpers: context.helpers,
        localStorage,
    };
}

test('chat draft contexts are scoped per chat, temp chat, project, and default start state', () => {
    const { chatContainer, helpers } = loadDraftHelpers();

    chatContainer.setAttribute('data-chat-id', 'chat-123');
    assert.equal(helpers.getCurrentChatInputDraftContext().storageKey, 'chat_box_input_draft:chat:chat-123');

    chatContainer.removeAttribute('data-chat-id');
    chatContainer.setAttribute('data-project-id', 'project-123');
    assert.equal(helpers.getCurrentChatInputDraftContext().storageKey, 'chat_box_input_draft:project:project-123');

    chatContainer.setAttribute('data-temp-chat', 'true');
    const tempContext = helpers.getCurrentChatInputDraftContext();
    assert.match(tempContext.storageKey, /^chat_box_input_draft:temp:temp-/);
    assert.equal(tempContext.storageKey, helpers.getCurrentChatInputDraftContext().storageKey);

    chatContainer.removeAttribute('data-temp-chat');
    chatContainer.removeAttribute('data-project-id');
    assert.equal(helpers.getCurrentChatInputDraftContext().storageKey, 'chat_box_input_draft:start:default');
});

test('temporary chat draft ids stay stable until the temp context is reset', () => {
    const { chatContainer, helpers } = loadDraftHelpers();
    chatContainer.setAttribute('data-temp-chat', 'true');

    const firstId = helpers.getChatInputDraftTempId();
    const secondId = helpers.getChatInputDraftTempId();
    assert.equal(firstId, secondId);

    helpers.resetChatInputDraftTempContext();
    const nextId = helpers.getChatInputDraftTempId();
    assert.notEqual(nextId, firstId);
});

test('chat drafts are timestamped and expire after 30 days without edits', () => {
    const { helpers, localStorage } = loadDraftHelpers();
    const freshKey = 'chat_box_input_draft:chat:fresh';
    const oldKey = 'chat_box_input_draft:chat:old';
    const now = 1710000000000;

    helpers.persistChatInputDraftValue('Fresh draft', freshKey);
    localStorage.setItem(oldKey, JSON.stringify({
        value: 'Old draft',
        updatedAt: now - (31 * 24 * 60 * 60 * 1000),
    }));

    helpers.removeExpiredChatInputDrafts(now);

    assert.equal(helpers.parseChatInputDraftStorageEntry(localStorage.getItem(freshKey)).value, 'Fresh draft');
    assert.equal(localStorage.getItem(oldKey), null);
});

test('chat drafts only persist message text and ignore composer snapshots', () => {
    const { helpers, localStorage } = loadDraftHelpers();
    const storageKey = 'chat_box_input_draft:chat:attachments';
    const composerState = {
        message: '',
        uploadedFileIds: ['file-1'],
        skills: [{ id: 'skill-1', title: 'Skill one' }],
        notes: [{ id: 'note-1', title: 'Note one' }],
        prompts: [{ id: 'prompt-1', title: 'Prompt one' }],
        chatReferences: [{ chat_id: 'chat-ref-1', title: 'Referenced chat' }],
        referenceParts: ['quoted assistant text'],
    };

    helpers.persistChatInputDraftValue('', storageKey, { composerState });

    assert.equal(localStorage.getItem(storageKey), null);

    helpers.persistChatInputDraftValue('Message only', storageKey, { composerState });
    const parsed = helpers.parseChatInputDraftStorageEntry(localStorage.getItem(storageKey));
    assert.equal(parsed.value, 'Message only');
    assert.equal(Object.prototype.hasOwnProperty.call(parsed, 'composerState'), false);

    localStorage.setItem(storageKey, JSON.stringify({
        value: 'Stored text',
        updatedAt: 1710000000000,
        composerState,
    }));
    const restored = helpers.readChatInputDraftEntry({ storageKey });
    assert.equal(restored.value, 'Stored text');
    assert.equal(restored.composerState, null);
});
