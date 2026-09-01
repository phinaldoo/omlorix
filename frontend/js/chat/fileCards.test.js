const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readStreamMessagesSource } = require('./messages/source.cjs');

const sourcePath = path.join(__dirname, 'fileCards.js');
const source = readFrontendSource(sourcePath, 'utf8');

/** Load the browser script with only the DOM surface used during initialization. */
function loadFileCards() {
    const window = {
        Icons: { download: '<svg></svg>' },
    };
    const context = {
        console,
        document: { querySelectorAll: () => [] },
        window,
    };
    vm.runInNewContext(source, context, { filename: sourcePath });
    return window.ChatFileCards;
}

test('file cards resolve synchronously, prefer extensions, support grouped MIME types, and fall back safely', () => {
    const cards = loadFileCards();

    assert.equal(cards.resolveProfile('report.pdf', 'application/octet-stream').id, 'pdf');
    assert.equal(cards.resolveProfile('proposal.DOCX', 'application/octet-stream').id, 'word');
    assert.equal(cards.resolveProfile('launch-deck.pptx', '').id, 'presentation');
    assert.equal(cards.resolveProfile('metrics.csv?version=2', '').id, 'spreadsheet');
    assert.equal(cards.resolveProfile('recording', 'audio/ogg; charset=binary').id, 'audio');
    assert.equal(cards.resolveProfile('unknown.custom-format', 'application/octet-stream').id, 'file');
    assert.equal(cards.getExtension('/folder/Quarterly.Report.PDF#page=1'), 'pdf');
});

test('both transcript renderers use file cards while composer tiles remain independent', () => {
    const streamSource = readStreamMessagesSource();
    const chatBoxSource = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const indexHtml = readFrontendSource(path.join(__dirname, '..', '..', 'index.html'), 'utf8');
    const shareHtml = readFrontendSource(path.join(__dirname, '..', '..', 'chat_share.html'), 'utf8');

    assert.match(streamSource, /function enhanceChatTranscriptFileCard/);
    assert.ok((streamSource.match(/enhanceChatTranscriptFileCard\(/g) || []).length >= 3);
    assert.match(streamSource, /onDownload:\s*\(\)\s*=>\s*downloadChatFileById\(fileId, fileName\)/);
    assert.doesNotMatch(chatBoxSource, /ChatFileCards|chat-file-card/);
    assert.ok(indexHtml.indexOf('/js/chat/fileCards.js') < indexHtml.indexOf('/js/chat/messages/shared.js'));
    assert.ok(shareHtml.indexOf('/js/chat/fileCards.js') < shareHtml.indexOf('/js/chat/messages/shared.js'));
    assert.notEqual(shareHtml.indexOf('/css/chat/chatBox/chatBoxInlineElements.css'), -1);
});

test('the option 9 mini-preview uses a non-collapsing grid track and explicit line elements', () => {
    const cssSource = readFrontendSource(
        path.join(__dirname, '..', '..', 'css', 'chat', 'chatBox', 'chatBoxInlineElements.css'),
        'utf8',
    );

    assert.match(source, /line\.className = 'chat-file-card-preview-line'/);
    assert.match(cssSource, /grid-template-columns:\s*minmax\(0, 1fr\)/);
    assert.match(cssSource, /justify-content:\s*stretch/);
    assert.match(cssSource, /> \.chat-file-card-preview-line/);
});
