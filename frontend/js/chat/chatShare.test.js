const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const CHAT_SHARE_PATH = path.join(__dirname, 'chatShare.js');
const CHAT_SHARE_MODAL_PATH = path.join(__dirname, 'deleteWarningModals.js');
const CHAT_TRANSCRIPT_RENDERER_PATH = path.join(__dirname, 'chatTranscriptRenderer.js');
const PUBLIC_CHAT_SHARE_PATH = path.join(__dirname, '..', 'chat-share.js');

class MockElement {
    constructor(id = '') {
        this.id = id;
        this.hidden = false;
        this.style = {};
        this.attributes = new Map();
        this.listeners = new Map();
        this.children = [];
        this.classNames = new Set();
        this.classList = {
            add: (...names) => names.forEach((name) => this.classNames.add(name)),
            remove: (...names) => names.forEach((name) => this.classNames.delete(name)),
            contains: (name) => this.classNames.has(name),
            toggle: (name, force) => {
                const shouldAdd = typeof force === 'boolean' ? force : !this.classNames.has(name);
                if (shouldAdd) {
                    this.classNames.add(name);
                } else {
                    this.classNames.delete(name);
                }
                return shouldAdd;
            },
        };
    }

    setAttribute(name, value) {
        this.attributes.set(name, String(value));
        this._notifyMutation(name);
    }

    getAttribute(name) {
        return this.attributes.has(name) ? this.attributes.get(name) : null;
    }

    removeAttribute(name) {
        this.attributes.delete(name);
        this._notifyMutation(name);
    }

    addEventListener(type, handler) {
        if (!this.listeners.has(type)) {
            this.listeners.set(type, []);
        }
        this.listeners.get(type).push(handler);
    }

    querySelector(selector) {
        if (selector === '.cs-modal') {
            return this.children.find((child) => child.classNames.has('cs-modal')) || null;
        }
        return null;
    }

    querySelectorAll() {
        return [];
    }

    _notifyMutation(attributeName) {
        for (const observer of MockMutationObserver.instances) {
            if (observer.target !== this) continue;
            const filter = observer.options?.attributeFilter;
            if (Array.isArray(filter) && !filter.includes(attributeName)) continue;
            observer.callback([{ type: 'attributes', attributeName, target: this }]);
        }
    }
}

class MockMutationObserver {
    static instances = [];

    constructor(callback) {
        this.callback = callback;
        this.target = null;
        this.options = null;
        MockMutationObserver.instances.push(this);
    }

    observe(target, options) {
        this.target = target;
        this.options = options;
    }

    disconnect() {
        this.target = null;
        this.options = null;
    }
}

function createElements() {
    const ids = [
        'chatShareOverlay',
        'chatShareCloseBtn',
        'chatShareSubtitle',
        'chatShareLinksSection',
        'chatShareLinkList',
        'chatShareEmptySection',
        'chatShareForm',
        'chatShareFormTitle',
        'chatShareAccessPublic',
        'chatShareAccessAuthenticated',
        'chatShareAccessInvite',
        'chatSharePasswordField',
        'chatSharePasswordToggle',
        'chatSharePasswordContent',
        'chatSharePasswordInput',
        'chatSharePasswordHelper',
        'chatSharePasswordError',
        'chatShareExpiryToggle',
        'chatShareExpiryContent',
        'chatShareExpiryInput',
        'chatShareInviteField',
        'chatShareInviteSearch',
        'chatShareInviteUserList',
        'chatShareInviteSelected',
        'chatShareInviteSelectedCount',
        'chatShareInviteSelectedList',
        'chatShareNotice',
        'chatSharePrimaryBtn',
        'chatShareSecondaryBtn',
        'headerShareButton',
        'chatContainer',
    ];
    const elements = new Map(ids.map((id) => [id, new MockElement(id)]));
    const modal = new MockElement('chatShareModal');
    modal.classNames.add('cs-modal');
    elements.get('chatShareOverlay').children.push(modal);
    return elements;
}

async function waitForAsyncVisibilitySync() {
    for (let index = 0; index < 5; index += 1) {
        await Promise.resolve();
    }
}

async function loadChatShareRuntime({
    chatSetup = { enable_chat_sharing: true },
    chatId = '',
    statusPayload = {},
} = {}) {
    MockMutationObserver.instances = [];
    const elements = createElements();
    const chatContainer = elements.get('chatContainer');
    if (chatId) {
        chatContainer.setAttribute('data-chat-id', chatId);
    }

    const fetchCalls = [];
    const documentListeners = new Map();
    const windowObject = {
        chatSetup,
        authedFetch: async (url, options) => {
            fetchCalls.push({ url, options });
            return {
                ok: true,
                status: 200,
                json: async () => statusPayload,
            };
        },
        getTranslation: (_key, fallback) => fallback,
        showDeleteConfirm: async () => false,
    };

    const context = {
        console,
        CSS: { escape: (value) => String(value).replace(/"/g, '\\"') },
        MutationObserver: MockMutationObserver,
        document: {
            activeElement: null,
            addEventListener(type, handler) {
                if (!documentListeners.has(type)) {
                    documentListeners.set(type, []);
                }
                documentListeners.get(type).push(handler);
            },
            getElementById(id) {
                return elements.get(id) || null;
            },
            querySelectorAll() {
                return [];
            },
        },
        navigator: { clipboard: { writeText: async () => {} } },
        requestAnimationFrame: (callback) => callback(),
        setTimeout: (callback) => callback(),
        window: windowObject,
    };
    context.globalThis = context;

    vm.runInNewContext(fs.readFileSync(CHAT_SHARE_PATH, 'utf8'), context, {
        filename: CHAT_SHARE_PATH,
    });
    await waitForAsyncVisibilitySync();

    return {
        chatContainer,
        fetchCalls,
        headerShareButton: elements.get('headerShareButton'),
        window: windowObject,
    };
}

test('chat share header button stays hidden on the start page even when sharing is enabled', async () => {
    const { fetchCalls, headerShareButton } = await loadChatShareRuntime({
        chatSetup: { enable_chat_sharing: true },
    });

    assert.equal(headerShareButton.style.display, 'none');
    assert.equal(fetchCalls.length, 0);
});

test('chat share header button appears only after an active chat id exists', async () => {
    const { chatContainer, headerShareButton } = await loadChatShareRuntime({
        chatSetup: { enable_chat_sharing: true },
    });

    assert.equal(headerShareButton.style.display, 'none');

    chatContainer.setAttribute('data-chat-id', 'chat-123');
    await waitForAsyncVisibilitySync();
    assert.equal(headerShareButton.style.display, 'flex');

    chatContainer.removeAttribute('data-chat-id');
    await waitForAsyncVisibilitySync();
    assert.equal(headerShareButton.style.display, 'none');
});

test('existing share links remain reachable when new chat sharing is disabled', async () => {
    const { fetchCalls, headerShareButton } = await loadChatShareRuntime({
        chatSetup: { enable_chat_sharing: false },
        chatId: 'chat-456',
        statusPayload: { share_id: 'share-456' },
    });

    assert.equal(headerShareButton.style.display, 'flex');
    assert.equal(fetchCalls.length, 1);
    assert.match(fetchCalls[0].url, /chat_id=chat-456/);
});

test('chat share publication review is wired through modal, API payloads, and transcript rendering', () => {
    const modalSource = fs.readFileSync(CHAT_SHARE_MODAL_PATH, 'utf8');
    const ownerSource = fs.readFileSync(CHAT_SHARE_PATH, 'utf8');
    const rendererSource = fs.readFileSync(CHAT_TRANSCRIPT_RENDERER_PATH, 'utf8');
    const publicSource = fs.readFileSync(PUBLIC_CHAT_SHARE_PATH, 'utf8');

    assert.match(modalSource, /id="chatSharePublicationField"/);
    assert.match(modalSource, /id="chatSharePublicationOptions" role="group"/);
    assert.match(ownerSource, /\/api\/v1\/chats\/share\/publication\/options\?chat_id=/);
    assert.match(ownerSource, /\/api\/v1\/chats\/share\/publication'/);
    assert.match(ownerSource, /publication:\s*collectPublicationSelection\(\)/);
    assert.match(ownerSource, /response_versions:\s*responseVersions/);
    assert.match(ownerSource, /approved_output_ids:\s*approvedOutputIds/);
    assert.match(ownerSource, /const restoreFocus = document\.activeElement === radio/);
    assert.match(ownerSource, /String\(candidate\.dataset\.referenceId \|\| ''\) === referenceId/);
    assert.match(ownerSource, /replacement\?\.focus\(\)/);
    assert.match(ownerSource, /chat_share_publication_quiz_preview_one/);
    assert.match(ownerSource, /chat_share_publication_flashcards_preview_other/);
    assert.match(rendererSource, /block\.type === 'share_omission'/);
    assert.match(rendererSource, /block\.type === 'shared_tool_output'/);
    assert.match(rendererSource, /message\.role === 'share_notice'/);
    assert.match(publicSource, /renderSharedChatPublicationBlock/);
    assert.match(publicSource, /text\.textContent = String\(block\.text\)/);
    assert.doesNotMatch(publicSource, /block\.html/);
});

test('public chat share refreshes dynamic transcript labels after translations load', () => {
    const publicSource = fs.readFileSync(PUBLIC_CHAT_SHARE_PATH, 'utf8');

    assert.match(publicSource, /document\.addEventListener\('i18n:updated'/);
    assert.match(publicSource, /renderTranscript\(state\.lastTranscriptPayload, \{ force: true \}\)/);
});
