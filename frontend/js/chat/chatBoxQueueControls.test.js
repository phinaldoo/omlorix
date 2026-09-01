const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

const frontendRoot = path.resolve(__dirname, '../..');
const indexMarkup = readFrontendSource(path.join(frontendRoot, 'index.html'), 'utf8');
const composerStyles = readFrontendSource(path.join(frontendRoot, 'css/chat/chatBox/chatBoxControls.css'), 'utf8');
const chatBoxSource = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
const tooltipSource = readFrontendSource(path.join(frontendRoot, 'js/common/tooltip.js'), 'utf8');

test('queued-message action sits immediately before the dedicated stop action', () => {
    const sendWrapperStart = indexMarkup.lastIndexOf('<span class="om-button-wrapper tooltip-container chat-send-tooltip"');
    const sendWrapperEnd = indexMarkup.indexOf('</span>', sendWrapperStart);
    const stopButtonStart = indexMarkup.indexOf('id="chatBoxStopButton"');

    assert.ok(sendWrapperStart >= 0, 'Expected the send/queue action wrapper in the composer');
    assert.ok(sendWrapperEnd >= 0, 'Expected the send/queue action wrapper to close');
    assert.ok(stopButtonStart > sendWrapperEnd, 'Expected the queue action to be directly before Stop');
});

test('send is a high-contrast primary action while the dedicated stop action stays neutral', () => {
    assert.match(
        composerStyles,
        /\.chat-box \.om-button\.send\s*\{[^}]*background-color:\s*var\(--chat-composer-primary-control-background[^}]*color:\s*var\(--chat-composer-primary-control-text/s,
    );
    assert.match(
        composerStyles,
        /\.om-button\.chat-box-stop-button\s*\{\s*background-color:\s*var\(--chat-composer-control/,
    );
    assert.doesNotMatch(
        composerStyles,
        /\.om-button\.chat-box-stop-button[^,{]*\{[^}]*var\(--error-color\)/s,
    );
});

test('leaving Stop mode dismisses its tooltip before the send trigger is hidden', () => {
    assert.match(
        tooltipSource,
        /container\.addEventListener\('omlorix:tooltip-dismiss', hide\)/,
        'the shared tooltip controller must expose a synchronous dismissal path',
    );
    assert.match(
        chatBoxSource,
        /function setChatSendTooltipEnabled\(enabled\)[\s\S]*if \(!enabled\)[\s\S]*dispatchEvent\(new Event\('omlorix:tooltip-dismiss'\)\)/,
        'disabling the dynamic Send/Stop tooltip must dismiss any visible instance',
    );
});
