const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('chat title utils strip automation prefix and render badge markup', () => {
    const source = fs.readFileSync(path.join(__dirname, '../common/chatTitleUtils.js'), 'utf8');
    const context = {
        window: {},
        Icons: {
            automation: '<svg viewBox="0 0 24 24"></svg>',
        },
    };
    context.window = context;

    vm.runInNewContext(source, context, { filename: 'chatTitleUtils.js' });

    const utils = context.window.ChatTitleUtils;
    assert.ok(utils);
    assert.equal(utils.normalizeChatTitle('[Automation] Daily brief'), 'Daily brief');
    assert.equal(utils.isAutomationChat({ title: 'Daily brief', meta: { source: 'automation' } }), true);

    const markup = utils.buildChatTitleMarkup(
        { title: 'Daily brief', meta: { source: 'automation' } },
        'Daily brief',
        { fallbackTitle: 'Untitled chat' },
    );

    assert.match(markup, /chat-title-badge/);
    assert.match(markup, /chat-title-text/);
    assert.match(markup, /Daily brief/);
});
