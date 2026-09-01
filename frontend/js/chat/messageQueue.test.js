const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeClassList {
    constructor(owner) {
        this.owner = owner;
        this.values = new Set();
    }

    setFromString(value) {
        this.values = new Set(String(value || '').split(/\s+/).filter(Boolean));
        this.sync();
    }

    sync() {
        this.owner._className = Array.from(this.values).join(' ');
    }

    add(...tokens) {
        tokens.forEach((token) => {
            if (token) this.values.add(token);
        });
        this.sync();
    }

    remove(...tokens) {
        tokens.forEach((token) => this.values.delete(token));
        this.sync();
    }

    toggle(token, force) {
        if (force === true) {
            this.values.add(token);
        } else if (force === false) {
            this.values.delete(token);
        } else if (this.values.has(token)) {
            this.values.delete(token);
        } else {
            this.values.add(token);
        }
        this.sync();
        return this.values.has(token);
    }

    contains(token) {
        return this.values.has(token);
    }
}

class FakeFragment {
    constructor(ownerDocument) {
        this.ownerDocument = ownerDocument;
        this.children = [];
        this.isFragment = true;
    }

    appendChild(child) {
        if (child.parentNode) {
            child.parentNode.removeChild(child);
        }
        this.children.push(child);
        child.parentNode = this;
        return child;
    }

    removeChild(child) {
        const index = this.children.indexOf(child);
        if (index !== -1) {
            this.children.splice(index, 1);
            child.parentNode = null;
        }
        return child;
    }
}

class FakeElement {
    constructor(tagName, ownerDocument) {
        this.tagName = String(tagName || 'div').toUpperCase();
        this.ownerDocument = ownerDocument;
        this.parentNode = null;
        this.children = [];
        this.dataset = {};
        this.attributes = {};
        this.style = {};
        this.listeners = {};
        this.disabled = false;
        this.hidden = false;
        this.value = '';
        this.textContent = '';
        this.title = '';
        this.type = '';
        this._className = '';
        this._innerHTML = '';
        this.classList = new FakeClassList(this);
    }

    get className() {
        return this._className;
    }

    set className(value) {
        this.classList.setFromString(value);
    }

    get innerHTML() {
        return this._innerHTML;
    }

    set innerHTML(value) {
        this._innerHTML = String(value || '');
        this.children = [];
    }

    get firstChild() {
        return this.children[0] || null;
    }

    appendChild(child) {
        if (child && child.isFragment) {
            child.children.slice().forEach((fragmentChild) => {
                this.appendChild(fragmentChild);
            });
            child.children = [];
            return child;
        }
        if (child.parentNode && typeof child.parentNode.removeChild === 'function') {
            child.parentNode.removeChild(child);
        }
        this.children.push(child);
        child.parentNode = this;
        return child;
    }

    insertBefore(child, referenceChild) {
        if (!referenceChild) {
            return this.appendChild(child);
        }
        if (child.parentNode && typeof child.parentNode.removeChild === 'function') {
            child.parentNode.removeChild(child);
        }
        const index = this.children.indexOf(referenceChild);
        if (index === -1) {
            return this.appendChild(child);
        }
        this.children.splice(index, 0, child);
        child.parentNode = this;
        return child;
    }

    removeChild(child) {
        const index = this.children.indexOf(child);
        if (index !== -1) {
            this.children.splice(index, 1);
            child.parentNode = null;
        }
        return child;
    }

    remove() {
        if (this.parentNode && typeof this.parentNode.removeChild === 'function') {
            this.parentNode.removeChild(this);
        }
    }

    addEventListener(eventName, handler) {
        if (!this.listeners[eventName]) {
            this.listeners[eventName] = [];
        }
        this.listeners[eventName].push(handler);
    }

    dispatchEvent(event) {
        const normalizedEvent = event || {};
        normalizedEvent.target = normalizedEvent.target || this;
        normalizedEvent.currentTarget = this;
        normalizedEvent.preventDefault = normalizedEvent.preventDefault || function preventDefault() {};
        normalizedEvent.stopPropagation = normalizedEvent.stopPropagation || function stopPropagation() {};
        const handlers = this.listeners[normalizedEvent.type] || [];
        handlers.forEach((handler) => handler(normalizedEvent));
        return true;
    }

    click() {
        return this.dispatchEvent({ type: 'click' });
    }

    focus() {
        this.ownerDocument.activeElement = this;
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

    setAttribute(name, value) {
        const normalizedValue = String(value);
        this.attributes[name] = normalizedValue;
        if (name === 'id') {
            this.id = normalizedValue;
            return;
        }
        if (name === 'class') {
            this.className = normalizedValue;
            return;
        }
        if (name === 'hidden') {
            this.hidden = true;
            return;
        }
        if (name === 'draggable') {
            this.draggable = normalizedValue === 'true';
            return;
        }
        if (name.startsWith('data-')) {
            const dataKey = name
                .slice(5)
                .replace(/-([a-z])/g, (_, token) => token.toUpperCase());
            this.dataset[dataKey] = normalizedValue;
        }
    }

    getAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this.attributes, name)
            ? this.attributes[name]
            : null;
    }

    removeAttribute(name) {
        delete this.attributes[name];
        if (name === 'hidden') {
            this.hidden = false;
        }
    }

    toggleAttribute(name, force) {
        const nextValue = force == null ? !Object.prototype.hasOwnProperty.call(this.attributes, name) : Boolean(force);
        if (nextValue) {
            this.setAttribute(name, '');
        } else {
            this.removeAttribute(name);
        }
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
        const matches = [];
        const visit = (node) => {
            node.children.forEach((child) => {
                if (matchesSelector(child, selector)) {
                    matches.push(child);
                }
                visit(child);
            });
        };
        visit(this);
        return matches;
    }
}

function matchesSelector(element, selector) {
    if (!(element instanceof FakeElement)) {
        return false;
    }
    if (selector.startsWith('.')) {
        return element.classList.contains(selector.slice(1));
    }
    if (selector.startsWith('#')) {
        return element.id === selector.slice(1);
    }
    const dataMatch = selector.match(/^\[data-([a-z0-9-]+)="([^"]+)"\]$/i);
    if (dataMatch) {
        const dataKey = dataMatch[1].replace(/-([a-z])/g, (_, token) => token.toUpperCase());
        return element.dataset[dataKey] === dataMatch[2];
    }
    return false;
}

class FakeDocument {
    constructor() {
        this.readyState = 'complete';
        this.activeElement = null;
        this.body = new FakeElement('body', this);
        this.listeners = {};
    }

    createElement(tagName) {
        return new FakeElement(tagName, this);
    }

    createDocumentFragment() {
        return new FakeFragment(this);
    }

    getElementById(id) {
        return this.body.querySelector(`#${id}`);
    }

    addEventListener(eventName, handler) {
        if (!this.listeners[eventName]) {
            this.listeners[eventName] = [];
        }
        this.listeners[eventName].push(handler);
    }

    dispatchEvent(event) {
        const normalizedEvent = event || {};
        normalizedEvent.target = normalizedEvent.target || this;
        normalizedEvent.currentTarget = this;
        const handlers = this.listeners[normalizedEvent.type] || [];
        handlers.forEach((handler) => handler(normalizedEvent));
        return true;
    }
}

function createHarness(options = {}) {
    const { windowOverrides = {}, contextOverrides = {}, sendMessage } = options;
    const document = new FakeDocument();
    const chatBox = document.createElement('div');
    chatBox.setAttribute('id', 'chatBox');
    const chatBoxBottomDiv = document.createElement('div');
    chatBoxBottomDiv.className = 'chat-box-bottom-div';
    chatBox.appendChild(chatBoxBottomDiv);
    const chatInput = document.createElement('textarea');
    chatInput.setAttribute('id', 'chatBoxInput');
    document.body.appendChild(chatBox);
    document.body.appendChild(chatInput);

    const translations = {};
    const window = {
        getTranslation: (key, fallback) => translations[key] ?? fallback,
        formatTranslation: (key, fallback, vars = {}) => String(translations[key] ?? fallback).replace(/\{(\w+)\}/g, (_, token) => String(vars[token] ?? '')),
        writeChatInputDraft() {},
        focusChatInput: () => chatInput.focus(),
        toggleInputButtons() {},
        requestAnimationFrame: (handler) => handler(),
        ...windowOverrides,
    };

    const context = {
        console,
        document,
        Event: class FakeEvent {
            constructor(type, init = {}) {
                this.type = type;
                this.bubbles = Boolean(init.bubbles);
            }
        },
        Intl,
        Date,
        setTimeout: (handler) => handler(),
        window,
        ...contextOverrides,
        sendMessage: sendMessage ?? contextOverrides.sendMessage,
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'messageQueue.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'messageQueue.js' });

    return {
        chatInput,
        document,
        queueChip: document.getElementById('chatQueueChip'),
        queueList: document.getElementById('messageQueueList'),
        translations,
        window,
    };
}

function getQueueMessages(queueList) {
    return queueList.querySelectorAll('.message-queue-item-message').map((element) => element.textContent);
}

test('queued message button restores the item to the composer', () => {
    const { chatInput, document, queueList, window } = createHarness();

    window.messageQueue.add('First queued message');

    const editButton = queueList.querySelector('[data-queue-focus-target="edit"]');
    assert.ok(editButton, 'expected an editable queue button');

    editButton.click();

    assert.equal(chatInput.value, 'First queued message');
    assert.equal(window.messageQueue.length(), 0);
    assert.equal(document.activeElement, chatInput);
});

test('move controls reorder queue items and keep focus on the moved item control', () => {
    const { document, queueList, window } = createHarness();

    window.messageQueue.add('First');
    window.messageQueue.add('Second');
    window.messageQueue.add('Third');

    const queueItems = queueList.querySelectorAll('.message-queue-item');
    const moveUpButton = queueItems[2].querySelector('[data-queue-focus-target="move-up"]');
    assert.ok(moveUpButton, 'expected a move-up button on the third item');

    moveUpButton.click();

    assert.deepEqual(getQueueMessages(queueList), ['First', 'Third', 'Second']);
    assert.equal(document.activeElement?.dataset.queueFocusTarget, 'move-up');
    assert.equal(queueList.querySelectorAll('.message-queue-item')[1].dataset.queueId, document.activeElement.parentNode.parentNode.dataset.queueId);
    assert.equal(document.getElementById('messageQueueLiveRegion').textContent, 'Moved to position 2.');
});

test('queued items stay compact until the queue chip opens the list', () => {
    const { document, queueChip, window } = createHarness();

    assert.ok(queueChip, 'expected a queue chip');
    assert.equal(queueChip.hidden, true);

    window.messageQueue.add('First queued message');

    const overlay = document.getElementById('messageQueueOverlay');
    const chipToggle = document.getElementById('chatQueueChipToggle');
    const chipCount = document.getElementById('chatQueueChipCount');

    assert.equal(queueChip.hidden, false);
    assert.equal(chipCount.textContent, '1');
    assert.equal(overlay.getAttribute('aria-hidden'), 'true');

    chipToggle.click();

    assert.equal(window.messageQueue.isPanelOpen(), true);
    assert.equal(overlay.getAttribute('aria-hidden'), 'false');
});

test('composer queue summary stays compact while management actions live in the panel', () => {
    const { document, queueChip } = createHarness();

    const headerActions = document.body.querySelector('.message-queue-header-actions');
    assert.ok(headerActions, 'expected queue management actions in the expanded panel');
    assert.equal(queueChip.children.length, 1, 'expected only the summary toggle in the composer chip');
    assert.equal(document.getElementById('chatQueuePauseButton').parentNode, headerActions);
    assert.equal(document.getElementById('chatQueueSendNextButton').parentNode, headerActions);
    assert.equal(document.getElementById('chatQueueClearButton').parentNode, headerActions);
});

test('queue chip hides again once the active queue is cleared', () => {
    const { queueChip, window } = createHarness();

    window.messageQueue.add('First queued message');
    assert.equal(queueChip.hidden, false);

    window.messageQueue.clear();

    assert.equal(window.messageQueue.length(), 0);
    assert.equal(queueChip.hidden, true);
});

test('send next works while paused and keeps the remaining queue paused', async () => {
    const sentMessages = [];
    let document;
    let window;
    ({ document, window } = createHarness({
        sendMessage: async (message, _attaching, _generationId, options) => {
            sentMessages.push(message);
            options.onRequestAccepted('generation-1');
            window.messageQueue.handleGenerationTerminal({
                generationId: 'generation-1',
                status: 'finished',
            });
        },
    }));

    window.messageQueue.add('First');
    window.messageQueue.add('Second');
    window.messageQueue.pause();

    const pauseButton = document.getElementById('chatQueuePauseButton');
    const sendNextButton = document.getElementById('chatQueueSendNextButton');

    assert.equal(window.messageQueue.isPaused(), true);
    assert.ok(pauseButton, 'expected a pause button');
    assert.ok(sendNextButton, 'expected a send-next button');

    sendNextButton.click();
    await Promise.resolve();
    await Promise.resolve();

    assert.deepEqual(sentMessages, ['First']);
    assert.equal(window.messageQueue.length(), 1);
    assert.equal(window.messageQueue.peek().message, 'Second');
    assert.equal(window.messageQueue.isPaused(), true);
});

test('visible completion hands off immediately without waiting for the prior stream promise', async () => {
    const sentMessages = [];
    let resolveFirstStream;
    let resolveSecondStream;
    let window;

    ({ window } = createHarness({
        sendMessage: (message, _attaching, _generationId, options) => {
            sentMessages.push(message);
            options.onRequestAccepted(`generation-${sentMessages.length}`);
            if (sentMessages.length === 1) {
                return new Promise((resolve) => {
                    resolveFirstStream = resolve;
                });
            }
            if (sentMessages.length === 2) {
                return new Promise((resolve) => {
                    resolveSecondStream = resolve;
                });
            }
            return Promise.resolve();
        },
    }));

    window.messageQueue.add('First');
    window.messageQueue.add('Second');
    window.messageQueue.add('Third');

    const firstDispatch = window.messageQueue.processNext();
    await Promise.resolve();

    assert.deepEqual(sentMessages, ['First']);
    window.messageQueue.handleGenerationTerminal({
        generationId: 'generation-1',
        status: 'finished',
    });
    await Promise.resolve();
    assert.deepEqual(sentMessages, ['First', 'Second']);
    assert.equal(window.messageQueue.length(), 1);

    // Settling the old stream must not clear the second dispatch's ownership
    // and accidentally send the third message before the second is complete.
    resolveFirstStream();
    await firstDispatch;
    assert.deepEqual(sentMessages, ['First', 'Second']);

    window.messageQueue.handleGenerationTerminal({
        generationId: 'generation-2',
        status: 'finished',
    });
    await Promise.resolve();
    assert.deepEqual(sentMessages, ['First', 'Second', 'Third']);
    assert.equal(window.messageQueue.length(), 0);

    resolveSecondStream();
    await Promise.resolve();
    assert.deepEqual(sentMessages, ['First', 'Second', 'Third']);
});

test('blocked completion waits for the owning lifecycle to request another handoff', async () => {
    const sentMessages = [];
    let cleanupPending = true;
    let window;
    ({ window } = createHarness({
        windowOverrides: {
            isInterruptedDraftDispatchPending: () => cleanupPending,
        },
        sendMessage: async (message, _attaching, _generationId, options) => {
            sentMessages.push(message);
            options.onRequestAccepted('generation-queued');
            window.messageQueue.handleGenerationTerminal({
                generationId: 'generation-queued',
                status: 'finished',
            });
        },
    }));

    window.messageQueue.add('Queued while cleanup is pending');
    window.messageQueue.handleGenerationTerminal({ generationId: 'prior', status: 'finished' });
    await Promise.resolve();

    assert.deepEqual(sentMessages, []);
    assert.equal(window.messageQueue.length(), 1);

    cleanupPending = false;
    await window.messageQueue.processNext();
    await Promise.resolve();

    assert.deepEqual(sentMessages, ['Queued while cleanup is pending']);
    assert.equal(window.messageQueue.length(), 0);
});

test('queueInput accepts attachment-only queued turns and renders a placeholder label', () => {
    const { chatInput, queueList, window } = createHarness({
        windowOverrides: {
            captureChatComposerStateSnapshot: () => ({
                message: '',
                uploadedFiles: [{ file_id: 'file-1', name: 'spec.pdf' }],
            }),
        },
    });

    chatInput.value = '';

    const queued = window.messageQueue.queueInput({ showOverlay: false });

    assert.equal(queued, true);
    assert.equal(window.messageQueue.length(), 1);
    assert.deepEqual(getQueueMessages(queueList), ['No text (attachments/context only)']);
    assert.equal(
        queueList.querySelector('.message-queue-item-message').classList.contains('message-queue-item-message--placeholder'),
        true,
    );
});

test('processNext sends attachment-only queued turns after restoring the composer snapshot', async () => {
    const restoredSnapshots = [];
    const sentMessages = [];
    let window;
    ({ window } = createHarness({
        windowOverrides: {
            applyChatComposerStateSnapshot(snapshot) {
                restoredSnapshots.push(snapshot);
                return true;
            },
        },
        contextOverrides: {
            sendMessage: async (message, _attaching, _generationId, options) => {
                sentMessages.push(message);
                options.onRequestAccepted('generation-attachment');
                window.messageQueue.handleGenerationTerminal({
                    generationId: 'generation-attachment',
                    status: 'finished',
                });
            },
        },
    }));

    window.messageQueue.add('', {
        composerState: {
            message: '',
            uploadedFiles: [{ file_id: 'file-1', name: 'spec.pdf' }],
        },
    });

    await window.messageQueue.processNext();

    assert.equal(window.messageQueue.length(), 0);
    assert.equal(restoredSnapshots.length, 2);
    assert.equal(restoredSnapshots[0].uploadedFiles[0].file_id, 'file-1');
    assert.deepEqual(sentMessages, ['']);
});

test('queued dispatch restores the live draft instead of leaving sent text in the composer', async () => {
    const observedComposerValues = [];
    let chatInput;
    let window;
    ({ chatInput, window } = createHarness({
        windowOverrides: {
            captureChatComposerStateSnapshot() {
                return { message: chatInput.value };
            },
            applyChatComposerStateSnapshot(snapshot) {
                chatInput.value = snapshot.message;
                return true;
            },
        },
        sendMessage: async (_message, _attaching, _generationId, options) => {
            observedComposerValues.push(chatInput.value);
            options.onRequestAccepted('generation-draft');
            window.messageQueue.handleGenerationTerminal({
                generationId: 'generation-draft',
                status: 'finished',
            });
        },
    }));

    window.messageQueue.add('Queued payload', {
        composerState: { message: 'Queued payload' },
    });
    chatInput.value = 'My newer unsent draft';

    await window.messageQueue.processNext();

    assert.deepEqual(observedComposerValues, ['Queued payload']);
    assert.equal(chatInput.value, 'My newer unsent draft');
    assert.equal(window.messageQueue.length(), 0);
});

test('an unaccepted queued request stays queued for retry', async () => {
    const { window } = createHarness({
        sendMessage: async () => false,
    });

    window.messageQueue.add('Retry me');
    await window.messageQueue.processNext();

    assert.equal(window.messageQueue.length(), 1);
    assert.equal(window.messageQueue.peek().message, 'Retry me');
    assert.equal(window.messageQueue.isPaused(), true);
});

test('split dispatch remains queued when only one panel accepts it', async () => {
    let window;
    const splitManager = {
        active: true,
        sendTarget: 'both',
        leftChatId: 'chat-left',
        rightChatId: 'chat-right',
        leftModelId: 'model-left',
        rightModelId: 'model-right',
        async send(_message, options) {
            options.onRequestAccepted('generation-left', 'left');
            window.messageQueue.handleGenerationTerminal({
                generationId: 'generation-left',
                status: 'finished',
            });
            return false;
        },
    };

    ({ window } = createHarness({
        windowOverrides: { SplitScreenManager: splitManager },
    }));

    window.messageQueue.add('Send to both panels');
    await window.messageQueue.processNext();

    assert.equal(window.messageQueue.length(), 1);
    assert.equal(window.messageQueue.peek().message, 'Send to both panels');
    assert.equal(window.messageQueue.peek().dispatchContext.target, 'right');
    assert.equal(window.messageQueue.isPaused(), true);
});

test('finishing a queued stream does not overwrite text typed during generation', async () => {
    let resolveStream;
    let chatInput;
    let window;
    ({ chatInput, window } = createHarness({
        sendMessage: (_message, _attaching, _generationId, options) => {
            options.onRequestAccepted('generation-typing');
            return new Promise((resolve) => {
                resolveStream = resolve;
            });
        },
    }));

    window.messageQueue.add('Queued payload');
    chatInput.value = 'Draft before dispatch';
    const dispatchPromise = window.messageQueue.processNext();
    chatInput.value = 'Draft typed during generation';

    window.messageQueue.handleGenerationTerminal({
        generationId: 'generation-typing',
        status: 'finished',
    });
    resolveStream();
    await dispatchPromise;

    assert.equal(chatInput.value, 'Draft typed during generation');
});

test('queue translations refresh after i18n finishes loading', () => {
    const { document, queueList, translations, window } = createHarness();

    window.messageQueue.add('Queued item');

    translations.chat_queue_title = 'Nachrichtenwarteschlange';
    translations.chat_queue_empty = 'Keine Nachrichten';
    translations.chat_queue_status_idle = 'Leerlauf';
    translations.chat_queue_status_waiting = '{count} wartend';
    translations.chat_queue_list_aria = 'Warteschlangennachrichten';
    translations.chat_queue_item_actions_aria = 'Aktionen';
    translations.chat_queue_item_move_up = 'Nach oben';
    translations.chat_queue_item_move_down = 'Nach unten';
    translations.chat_queue_item_remove = 'Entfernen';

    document.dispatchEvent({ type: 'i18n:updated' });

    const title = document.body.querySelector('.message-queue-title');
    assert.equal(title.children[1].textContent, 'Nachrichtenwarteschlange');
    assert.equal(document.getElementById('messageQueueMeta').textContent, '1 wartend');
    assert.equal(document.getElementById('messageQueueList').attributes['aria-label'], 'Warteschlangennachrichten');

    const actions = queueList.querySelector('.message-queue-item-actions');
    assert.equal(actions.attributes['aria-label'], 'Aktionen');
    assert.equal(queueList.querySelector('[data-queue-focus-target="move-up"]').title, 'Nach oben');
    assert.equal(queueList.querySelector('[data-queue-focus-target="delete"]').title, 'Entfernen');
});
