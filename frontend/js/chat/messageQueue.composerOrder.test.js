const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const attachmentsSource = fs.readFileSync(
    path.join(__dirname, 'chatBox', 'attachments-and-generation.js'),
    'utf8',
);
const controlsSource = fs.readFileSync(
    path.join(__dirname, 'chatBox', 'references-and-files.js'),
    'utf8',
);

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `${functionName} not found`);

    const bodyStart = source.indexOf('{', start);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
    }
    throw new Error(`${functionName} body was not closed`);
}

function createDispatchContext({
    chatInput,
    queuedMessages,
    processNext,
    sendMessage,
    tryQueueCurrentInput,
}) {
    return {
        chatInput,
        clearChatInputDraft() {},
        clearPendingInterruptedDraftSend() {},
        console,
        Event: class FakeEvent {},
        getQueuedMessageCount: () => queuedMessages.length,
        hasChatModelForSend: () => true,
        hasSendableChatContent: () => true,
        interruptedDraftDispatchInFlight: false,
        isChatUploading: () => false,
        isRealtimeCallActive: () => false,
        resetDoubleEnterTimer() {},
        sendMessage,
        showUploadInProgressWarning() {},
        toggleInputButtons() {},
        tryQueueCurrentInput,
        updateChatModelAvailabilityUi() {},
        window: {
            messageQueue: { processNext },
            SplitScreenManager: null,
        },
    };
}

test('a composer send appends behind an older queued message during handoff', () => {
    const queuedMessages = ['Queue item A'];
    const dispatchedMessages = [];
    const chatInput = {
        value: 'Queue item B',
        dispatchEvent() {},
    };
    let directSendCalls = 0;

    const context = createDispatchContext({
        chatInput,
        sendMessage: () => {
            directSendCalls += 1;
        },
        tryQueueCurrentInput: () => {
            queuedMessages.push(chatInput.value);
            chatInput.value = '';
            return true;
        },
        processNext() {
            dispatchedMessages.push(queuedMessages.shift());
        },
        queuedMessages,
    });

    vm.runInNewContext([
        extractFunction(attachmentsSource, 'dispatchCurrentDraftMessage'),
        'this.result = dispatchCurrentDraftMessage();',
    ].join('\n\n'), context, { filename: 'attachments-and-generation.js' });

    assert.equal(context.result, true);
    assert.equal(directSendCalls, 0, 'the newer composer draft must not use direct send');
    assert.deepEqual(dispatchedMessages, ['Queue item A']);
    assert.deepEqual(queuedMessages, ['Queue item B']);
});

test('composer cleanup retries a queue handoff that was blocked by the finishing send', async () => {
    const queuedMessages = [];
    const dispatchedMessages = [];
    const chatInput = {
        value: 'Initial message',
        dispatchEvent() {},
    };
    let finishInitialSend;
    const initialSend = new Promise((resolve) => {
        finishInitialSend = resolve;
    });
    const context = createDispatchContext({
        chatInput,
        queuedMessages,
        processNext() {
            dispatchedMessages.push(queuedMessages.shift());
        },
        sendMessage: () => initialSend,
        tryQueueCurrentInput: () => false,
    });

    vm.runInNewContext([
        extractFunction(attachmentsSource, 'dispatchCurrentDraftMessage'),
        'this.result = dispatchCurrentDraftMessage();',
    ].join('\n\n'), context, { filename: 'attachments-and-generation.js' });

    queuedMessages.push('Queue item A');
    finishInitialSend();
    await initialSend;
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(context.result, true);
    assert.deepEqual(dispatchedMessages, ['Queue item A']);
    assert.deepEqual(queuedMessages, []);
});

test('the composer presents enqueue, not direct send, while an item is waiting', () => {
    const sendButton = {
        dataset: { uploading: 'false' },
        innerHTML: '',
        setAttribute(name, value) {
            this[name] = value;
        },
    };
    const context = {
        __originalSendIconHTML: '<send>',
        applyChatStopButtonMode() {},
        CHAT_QUEUE_MESSAGE_TOOLTIP_KEY: 'chat_queue_message_tooltip',
        CHAT_QUEUE_TOOLTIP_KEY: 'chat_queueing_messages_tooltip',
        CHAT_SEND_TOOLTIP_KEY: 'chat_send_message',
        CHAT_STOPPING_TOOLTIP_KEY: 'chat_stopping_response',
        CHAT_STOP_TOOLTIP_KEY: 'chat_stop_response',
        chatSendQueueIconMarkup: '<queue>',
        chatSendStoppingSpinnerMarkup: '<stopping>',
        getChatI18nString: (_key, fallback) => fallback,
        getQueuedMessageCount: () => 1,
        hasChatModelForSend: () => true,
        hasQueueableChatContent: () => true,
        isChatModelSelectionReady: () => true,
        isChatSendCancellationPending: () => false,
        isCurrentSendContextGenerating: () => false,
        sendButton,
        setChatSendCancellationPending() {},
        setChatSendTooltipEnabled() {},
        updateChatSendTooltip() {},
    };

    vm.runInNewContext([
        extractFunction(controlsSource, 'applySendButtonMode'),
        'applySendButtonMode();',
    ].join('\n\n'), context, { filename: 'references-and-files.js' });

    assert.equal(sendButton.dataset.mode, 'queue');
    assert.equal(sendButton.innerHTML, '<queue>');
    assert.equal(sendButton['aria-label'], 'Queue message (1 queued)');
});
