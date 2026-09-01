const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { readFrontendSource } = require('../splitSource.cjs');
const { readSendMessageSource } = require('./sending/source.cjs');

const frontendRoot = path.resolve(__dirname, '../..');
const chatBoxSource = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
const sendSource = readSendMessageSource();
const indexMarkup = fs.readFileSync(path.join(frontendRoot, 'index.html'), 'utf8');

test('composer exposes a translated model-access resolution status', () => {
    assert.match(indexMarkup, /id="chatModelUnavailable"[^>]*role="status"[^>]*aria-live="polite"[^>]*hidden/);
    assert.match(indexMarkup, /data-i18n="chat_model_unavailable_message"/);
    assert.match(indexMarkup, /id="chatModelUnavailableAction"/);
    assert.match(chatBoxSource, /window\.open\('\/admin\/models', '_blank', 'noopener,noreferrer'\)/);
    assert.match(chatBoxSource, /window\.openUserSettings\('byok'\)/);
    assert.match(chatBoxSource, /ModelSelectLoadModels\?\.\(\{ forceRefresh: true \}\)/);
});

test('button, Enter, queue, and direct send paths reject an empty model selection', () => {
    const dispatchStart = chatBoxSource.indexOf('function dispatchCurrentDraftMessage()');
    const dispatchEnd = chatBoxSource.indexOf('function flushInterruptedDraftSend()', dispatchStart);
    const dispatchSource = chatBoxSource.slice(dispatchStart, dispatchEnd);
    const guardIndex = dispatchSource.indexOf('!hasChatModelForSend()');
    const clearIndex = dispatchSource.indexOf("chatInput.value = ''");

    assert.ok(guardIndex >= 0, 'draft dispatch must check model selection');
    assert.ok(clearIndex > guardIndex, 'the draft must remain intact when model preflight fails');
    assert.match(chatBoxSource, /function tryQueueCurrentInput[\s\S]*?!hasChatModelForSend\(\)[\s\S]*?showChatModelUnavailableFeedback\(\)/);
    assert.match(chatBoxSource, /sendButton\.disabled = cancelPending \|\| \(!generating && \(!hasContent \|\| modelUnavailable\)\)/);
    assert.match(sendSource, /!attaching && !realtimeTextSessionActive && !modelId\.trim\(\)/);
});
