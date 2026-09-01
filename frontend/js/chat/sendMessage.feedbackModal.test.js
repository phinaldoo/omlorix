const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readStreamMessagesSource } = require('./messages/source.cjs');
const { readSendMessageSource } = require('./sending/source.cjs');

const SEND_MESSAGE_SOURCE = readSendMessageSource();
const STREAM_MESSAGES_SOURCE = readStreamMessagesSource();
const I18N_ROOT = path.join(__dirname, '..', '..', 'i18n');
const FEEDBACK_KEYS = [
    'chat_feedback_group_label',
    'chat_feedback_thumbs_up_label',
    'chat_feedback_thumbs_up_title',
    'chat_feedback_thumbs_down_label',
    'chat_feedback_thumbs_down_title',
    'chat_feedback_positive_title',
    'chat_feedback_negative_title',
    'chat_feedback_prompt',
    'chat_feedback_close',
    'chat_feedback_add_comment',
    'chat_feedback_submit_without_comment',
    'chat_feedback_comment_label',
    'chat_feedback_positive_placeholder',
    'chat_feedback_negative_placeholder',
    'chat_feedback_cancel',
    'chat_feedback_submit',
    'chat_feedback_char_count',
    'chat_feedback_submit_with_comment_success',
    'chat_feedback_submit_failed',
    'chat_feedback_submit_failed_status',
];

function extractFunction(source, functionName) {
    const asyncStart = source.indexOf(`async function ${functionName}`);
    const start = asyncStart >= 0 ? asyncStart : source.indexOf(`function ${functionName}`);
    assert.notEqual(start, -1, `${functionName} not found`);

    const bodyStart = source.indexOf('{', source.indexOf(')', start));
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
    }

    throw new Error(`${functionName} body was not closed`);
}

test('feedback modal source includes dialog semantics, focus handling, and translation lookups', () => {
    const source = SEND_MESSAGE_SOURCE;

    assert.match(source, /<div class="feedback-modal shared-modal shared-modal--compact shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="\$\{ids\.titleId\}" aria-describedby="\$\{ids\.descriptionId\}" tabindex="-1">/);
    assert.match(source, /<footer class="feedback-modal-actions shared-modal-footer">/);
    assert.match(source, /<footer class="feedback-modal-input-footer shared-modal-footer">/);
    assert.match(source, /function trapFeedbackModalFocus\(event, overlay\)/);
    assert.match(source, /function closeFeedbackTooltip\(\{ restoreFocus = true \} = \{\}\)/);
    assert.match(source, /focusFeedbackModalPrimaryAction\(overlay\)/);
    assert.match(source, /submitFeedbackReaction\(\{ controls, button, messageId, reaction, comment, restoreFocusTarget: button \}\)/);

    FEEDBACK_KEYS.forEach((key) => {
        assert.match(source, new RegExp(key));
    });
});

test('feedback modal translations exist in every supported locale', () => {
    const locales = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    locales.forEach((locale) => {
        const file = path.join(I18N_ROOT, locale, 'index.json');
        const dictionary = JSON.parse(fs.readFileSync(file, 'utf8'));

        FEEDBACK_KEYS.forEach((key) => {
            assert.ok(
                Object.prototype.hasOwnProperty.call(dictionary, key),
                `${locale} is missing ${key}`,
            );
        });
    });
});

test('streamed feedback uses only the persisted assistant message ID', async () => {
    const requests = [];
    const notifications = [];
    const assistantContainer = {
        id: 'a-optimistic-message',
        dataset: {
            optimisticMessage: 'true',
            referenceId: 'optimistic-message',
            retryCount: '0',
        },
        querySelector() {
            return null;
        },
    };
    const button = { disabled: false };
    const controls = {
        dataset: { messageId: 'optimistic-message' },
        parentElement: { dataset: {} },
        closest(selector) {
            assert.equal(selector, '.assistant-message-container');
            return assistantContainer;
        },
        querySelectorAll() {
            return [button];
        },
    };
    const context = {
        document: {
            getElementById(id) {
                return id === 'a-optimistic-message' ? assistantContainer : null;
            },
        },
        window: {
            async authedFetch(url, options) {
                requests.push({ url, options });
                return { ok: true };
            },
        },
        updateAssistantFeedbackVisualState() {},
        showAssistantFeedbackSuccess() {},
        notifySuccess: (message) => notifications.push(message),
        notifyError: (message) => notifications.push(message),
        getChatPreviewTranslation: (_key, fallback) => fallback,
        focusFeedbackModalTarget() {},
        console,
    };
    const helpers = vm.runInNewContext(
        [
            extractFunction(STREAM_MESSAGES_SOURCE, 'bindAssistantContainerToServerMessage'),
            extractFunction(STREAM_MESSAGES_SOURCE, 'resolvePersistedAssistantMessageId'),
            extractFunction(SEND_MESSAGE_SOURCE, 'submitFeedbackReaction'),
            '({ bindAssistantContainerToServerMessage, submitFeedbackReaction });',
        ].join('\n'),
        context,
        { filename: 'sendMessage.feedbackPersistedAssistantId.js' },
    );

    assert.equal(
        helpers.bindAssistantContainerToServerMessage(
            'optimistic-message',
            'persisted-assistant-message',
        ),
        true,
    );
    assert.equal(assistantContainer.dataset.assistantMessageId, 'persisted-assistant-message');

    await helpers.submitFeedbackReaction({
        controls,
        button,
        messageId: 'optimistic-message',
        reaction: 'thumbs_up',
        comment: 'Helpful response',
    });

    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/v1/feedback/react');
    assert.deepEqual(JSON.parse(requests[0].options.body), {
        message_id: 'persisted-assistant-message',
        reaction: 'thumbs_up',
        comment: 'Helpful response',
    });
    assert.equal(controls.dataset.messageId, 'persisted-assistant-message');
    assert.equal(controls.dataset.selectedReaction, 'thumbs_up');
    assert.equal(button.disabled, false);
    assert.deepEqual(notifications, ['Feedback submitted with comment']);

    await helpers.submitFeedbackReaction({
        controls,
        button,
        messageId: controls.dataset.messageId,
        reaction: 'thumbs_down',
        comment: null,
    });

    assert.equal(requests.length, 2);
    assert.deepEqual(JSON.parse(requests[1].options.body), {
        message_id: 'persisted-assistant-message',
        reaction: 'thumbs_down',
    });

    delete assistantContainer.dataset.assistantMessageId;
    assistantContainer.dataset.optimisticMessage = 'true';
    controls.dataset.messageId = 'optimistic-message';
    await helpers.submitFeedbackReaction({
        controls,
        button,
        messageId: 'optimistic-message',
        reaction: 'thumbs_down',
        comment: null,
    });

    assert.equal(requests.length, 2, 'an optimistic ID must never reach the feedback API');
    assert.deepEqual(notifications, [
        'Feedback submitted with comment',
        'Failed to submit feedback',
    ]);
});
