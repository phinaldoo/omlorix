const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');


const chatStyles = fs.readFileSync(path.join(__dirname, '../../css/chat/chat.css'), 'utf8');
const shellStyles = fs.readFileSync(path.join(__dirname, '../../css/chat/modelSettings.css'), 'utf8');
const splitStyles = fs.readFileSync(path.join(__dirname, '../../css/chat/splitScreen.css'), 'utf8');
const composerStyles = fs.readFileSync(path.join(__dirname, '../../css/chat/chatBox/chatBox.css'), 'utf8');
const chatFunctions = fs.readFileSync(path.join(__dirname, 'functions.js'), 'utf8');


/** Return the declaration block for a literal CSS selector. */
function cssBlock(source, selector) {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const match = source.match(new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\}`));
    assert.ok(match, `Missing CSS selector: ${selector}`);
    return match[1];
}


test('chat and split-screen composer share one parent-owned flex height', () => {
    const body = cssBlock(shellStyles, '.main-container-body');
    const header = cssBlock(shellStyles, '.main-container-body > .main-container-header');
    const chat = cssBlock(chatStyles, '.chat-container');
    const main = cssBlock(chatStyles, '.chat-container-main');
    const transcript = cssBlock(chatStyles, '.chat-area');
    const split = cssBlock(splitStyles, '.split-screen-wrapper');
    const composer = cssBlock(composerStyles, '.chat-box-area');

    assert.match(body, /min-height:\s*0/);
    assert.match(header, /flex:\s*0 0 auto/);
    assert.match(chat, /flex:\s*1 1 0/);
    assert.match(chat, /min-height:\s*0/);
    assert.match(main, /flex:\s*1 1 0/);
    assert.match(main, /min-height:\s*0/);
    assert.match(main, /max-height:\s*none/);
    assert.doesNotMatch(main, /100d?vh/);
    assert.match(transcript, /flex:\s*1 1 0/);
    assert.match(transcript, /min-height:\s*0/);
    assert.match(split, /flex:\s*1 1 0/);
    assert.match(composer, /flex:\s*0 0 auto/);
    assert.match(composer, /max\(8px, env\(safe-area-inset-bottom, 0px\)\)/);
});


test('empty chat warning does not reserve composer space before settings load', () => {
    const warning = cssBlock(composerStyles, '.chat-box-warning');

    assert.match(warning, /display:\s*none/);
    assert.match(chatFunctions, /chatBoxWarning\.style\.display = shouldShow \? "flex" : "none"/);
});


test('welcome title remains prominent and separated from the composer at mid sizes', () => {
    const welcome = cssBlock(chatStyles, '.chat-container-welcome');

    assert.match(welcome, /font-size:\s*clamp\(25px,\s*2\.2vw,\s*30px\)/);
    assert.match(welcome, /margin-bottom:\s*clamp\(44px,\s*5vh,\s*58px\)/);
    assert.match(
        chatStyles,
        /@container chat-layout \(max-width:\s*1000px\)[\s\S]*?\.chat-container-welcome\s*\{[\s\S]*?font-size:\s*clamp\(26px,\s*4cqw,\s*32px\)[\s\S]*?margin-bottom:\s*clamp\(44px,\s*5cqw,\s*52px\)/,
    );
});
