const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readSendMessageSource } = require('./sending/source.cjs');

const sendMessageSource = readSendMessageSource();
const chatBoxSource = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');

/** Extract one top-level function for focused behavior tests. */
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

function loadFingerprintHelpers() {
    const context = {};
    vm.runInNewContext([
        'const reservedChatAttachmentFingerprints = new Set();',
        extractFunction(sendMessageSource, 'createChatAttachmentFileFingerprint'),
        extractFunction(sendMessageSource, 'reserveChatAttachmentFingerprint'),
        extractFunction(sendMessageSource, 'releaseChatAttachmentFingerprints'),
        `this.helpers = {
            createChatAttachmentFileFingerprint,
            reserveChatAttachmentFingerprint,
            releaseChatAttachmentFingerprints,
        };`,
    ].join('\n\n'), context, { filename: 'sendMessage.js' });
    return context.helpers;
}

test('chatbox rejects the same local file until its attachment is released', () => {
    const helpers = loadFingerprintHelpers();
    const original = {
        name: 'report.pdf',
        size: 12345,
        type: 'application/pdf',
        lastModified: 1720000000000,
    };
    const repeatedPickerSelection = { ...original };
    const fingerprint = helpers.createChatAttachmentFileFingerprint(original);

    assert.equal(
        helpers.createChatAttachmentFileFingerprint(repeatedPickerSelection),
        fingerprint,
        'new File objects for the same local file should share an identity',
    );
    assert.equal(helpers.reserveChatAttachmentFingerprint(fingerprint), true);
    assert.equal(helpers.reserveChatAttachmentFingerprint(fingerprint), false);

    helpers.releaseChatAttachmentFingerprints({ file_fingerprints: [fingerprint] });
    assert.equal(
        helpers.reserveChatAttachmentFingerprint(fingerprint),
        true,
        'removing the attachment should allow it to be chosen again',
    );
});

test('chatbox allows a modified version of a previously selected file', () => {
    const helpers = loadFingerprintHelpers();
    const first = helpers.createChatAttachmentFileFingerprint({
        name: 'report.pdf',
        size: 12345,
        type: 'application/pdf',
        lastModified: 1720000000000,
    });
    const changed = helpers.createChatAttachmentFileFingerprint({
        name: 'report.pdf',
        size: 12345,
        type: 'application/pdf',
        lastModified: 1720000001000,
    });

    assert.notEqual(changed, first);
    assert.equal(helpers.reserveChatAttachmentFingerprint(first), true);
    assert.equal(helpers.reserveChatAttachmentFingerprint(changed), true);
});

test('server-reused file IDs replace temporary chips without duplicating the existing chip', () => {
    const replaceStart = chatBoxSource.indexOf('  replaceAttachmentId(oldId, attachment) {');
    const removeStart = chatBoxSource.indexOf('  removeAttachment(id) {', replaceStart);
    const replaceSource = chatBoxSource.slice(replaceStart, removeStart);

    assert.match(replaceSource, /const existingElement = chatBoxAttachmentElements\.get\(attachment\.id\)/);
    assert.match(replaceSource, /existingElement && existingElement !== element/);
    assert.match(replaceSource, /element\.remove\(\)/);
    assert.doesNotMatch(replaceSource, /if \(!element\) \{\s*this\.addAttachment\(attachment\)/);
});
