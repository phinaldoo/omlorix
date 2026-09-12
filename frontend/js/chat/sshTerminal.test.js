const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');


const source = fs.readFileSync(path.join(__dirname, 'sshTerminal.js'), 'utf8');
const index = fs.readFileSync(path.join(__dirname, '../../index.html'), 'utf8');
const styles = fs.readFileSync(path.join(__dirname, '../../css/chat/sshTerminal.css'), 'utf8');
const elementStyles = fs.readFileSync(path.join(__dirname, '../../css/common/elementsNew.css'), 'utf8');
const messageNavStyles = fs.readFileSync(path.join(__dirname, '../../css/chat/messageNav.css'), 'utf8');
const animationStyles = fs.readFileSync(path.join(__dirname, '../../css/common/animations.css'), 'utf8');
const iconSource = fs.readFileSync(path.join(__dirname, '../common/icons.js'), 'utf8');


test('ACP terminal is gated by the selected model capability', () => {
    assert.match(source, /provider === 'acp'/);
    assert.match(source, /model\?\.acp_terminal_available === true/);
    assert.match(source, /function isChatSurfaceActive\(\)/);
    assert.match(source, /const available = Boolean\(model && chatSurfaceActive && !splitActive\)/);
    assert.match(source, /window\.addEventListener\('modelSelect:changed', syncAvailability\)/);
    assert.match(source, /window\.addEventListener\('splitScreen:stateChanged', syncAvailability\)/);
    assert.match(index, /class="om-button" id="headerAcpTerminalButton"[^>]*hidden/);
    assert.match(elementStyles, /\.om-button\[hidden\][\s\S]*display: none !important/);
    assert.match(source, /surfaceObserver\.observe\(el\('chatContainer'\)/);
});


test('ACP terminal accepts an explicit split-panel model and preserves pane ownership', () => {
    assert.match(source, /function terminalContextIsAvailable\(\)/);
    assert.match(source, /state\.splitSide === 'left' \? split\?\.leftModelId : split\?\.rightModelId/);
    assert.match(source, /function openTerminal\(\{ model: requestedModel = null, side = null, returnFocus = null \} = \{\}\)/);
    assert.match(source, /const model = requestedModel[\s\S]*isTerminalModel\(requestedModel\)[\s\S]*selectedTerminalModel\(\)/);
    assert.match(source, /openForModel\(model, options = \{\}\)/);
    assert.match(source, /openTerminal\(\{ \.\.\.options, model \}\)/);
});


test('ACP terminal uses a cookie-authenticated same-origin websocket and raw bytes', () => {
    assert.match(source, /wss:' : 'ws:'/);
    assert.match(source, /\/api\/v1\/remote-connections\/acp-terminal/);
    assert.match(source, /socket\.binaryType = 'arraybuffer'/);
    assert.match(source, /state\.terminal\?\.write\(new Uint8Array\(event\.data\)\)/);
    assert.ok(
        (source.match(/if \(state\.socket !== socket\) return;/g) || []).length >= 3,
        'message, close, and error handlers all reject stale sockets',
    );
});


test('terminal panel exposes accessible controls and keyboard resizing', () => {
    assert.match(index, /id="acpTerminalPanel" role="region"/);
    assert.match(index, /id="acpTerminalResizeHandle" role="separator"[\s\S]*tabindex="0"/);
    assert.match(source, /\['ArrowUp', 'ArrowDown', 'Home', 'End'\]/);
    assert.match(source, /aria-expanded/);
});


test('terminal toolbar can maximize and restore the live terminal', () => {
    assert.match(index, /id="acpTerminalFullscreenButton"[\s\S]*aria-pressed="false"/);
    assert.match(index, /id="acpTerminalFullscreenIcon"/);
    assert.match(iconSource, /expand: '<svg/);
    assert.match(iconSource, /contract: '<svg/);
    assert.match(source, /function setTerminalFullscreen\(fullscreen/);
    assert.match(source, /classList\.toggle\('acp-terminal-fullscreen', nextFullscreen\)/);
    assert.match(source, /main\.setAttribute\('inert', ''\)/);
    assert.match(source, /main\.removeAttribute\('inert'\)/);
    assert.match(source, /handle\.tabIndex = nextFullscreen \? -1 : 0/);
    assert.match(source, /event\.key !== 'Escape' \|\| !state\.isFullscreen/);
    assert.match(source, /toggleFullscreen: toggleTerminalFullscreen/);
    assert.match(
        styles,
        /\.chat-container\.acp-terminal-fullscreen > \.acp-terminal-panel\.is-open[\s\S]*height: 100%[\s\S]*max-height: none/,
    );
});


test('terminal fullscreen labels are translated in every supported locale', () => {
    const i18nRoot = path.join(__dirname, '../../i18n');
    for (const locale of fs.readdirSync(i18nRoot)) {
        const dictionaryPath = path.join(i18nRoot, locale, 'index.json');
        if (!fs.existsSync(dictionaryPath)) continue;
        const dictionary = JSON.parse(fs.readFileSync(dictionaryPath, 'utf8'));
        assert.equal(typeof dictionary.acp_terminal_fullscreen, 'string', `${locale} fullscreen label`);
        assert.ok(dictionary.acp_terminal_fullscreen.trim(), `${locale} fullscreen label is non-empty`);
        assert.equal(typeof dictionary.acp_terminal_restore, 'string', `${locale} restore label`);
        assert.ok(dictionary.acp_terminal_restore.trim(), `${locale} restore label is non-empty`);
    }
});


test('terminal typography uses isolated monospace metrics', () => {
    assert.match(source, /getPropertyValue\('--acp-terminal-font-family'\)/);
    assert.match(source, /fontWeight: '400'/);
    assert.match(source, /letterSpacing: 0/);
    assert.match(styles, /--acp-terminal-font-family: ui-monospace, SFMono-Regular/);
    assert.match(styles, /\.acp-terminal-host \.xterm \*/);
    assert.match(styles, /box-sizing: content-box/);
    assert.match(styles, /font-variant-ligatures: none/);
    assert.match(styles, /letter-spacing: 0/);
});


test('terminal contains iPad touch and boundary wheel scrolling', () => {
    assert.match(source, /function installTerminalScrollInput\(host, terminal\)/);
    assert.match(source, /querySelector\('\.xterm-screen'\)/);
    assert.match(source, /host\.addEventListener\('touchmove', handleTouchMove, \{ passive: false \}\)/);
    assert.match(source, /terminal\.scrollLines\(lineDelta\)/);
    assert.match(source, /host\.addEventListener\('wheel', containEscapedWheel, \{ passive: false \}\)/);
    assert.match(source, /activeRowHeight = terminalRowHeight\(terminal\)/);
    assert.equal((source.match(/= terminalRowHeight\(terminal\)/g) || []).length, 1);
    assert.match(source, /if \(event\.ctrlKey\) return;\s*if \(event\.cancelable\) event\.preventDefault\(\)/);
    assert.match(source, /state\.scrollInputCleanup\?\.\(\)/);
    assert.match(styles, /\.acp-terminal-host[\s\S]*touch-action: pan-x pinch-zoom/);
    assert.match(styles, /\.acp-terminal-host \.xterm-scrollable-element[\s\S]*overscroll-behavior: none/);
});


test('message navigation remains centered in the chat viewport above the terminal', () => {
    assert.match(messageNavStyles, /top: 50%/);
    assert.doesNotMatch(messageNavStyles, /--acp-terminal-panel-height/);
    assert.match(source, /new ResizeObserver\(\(\) => \{\s*fitTerminal\(\);/);
    assert.match(source, /state\.resizeObserver\.observe\(el\('acpTerminalPanel'\)\)/);
});


test('terminal panel animates open and closed while respecting reduced motion', () => {
    assert.match(styles, /height 280ms cubic-bezier\(0\.32, 0\.72, 0, 1\)/);
    assert.match(styles, /transform: translateY\(24px\)/);
    assert.match(styles, /border-top: 0 solid transparent/);
    assert.match(styles, /\.acp-terminal-panel\.is-open[\s\S]*--acp-terminal-target-height/);
    assert.match(styles, /\.acp-terminal-panel\.is-closing/);
    assert.match(styles, /\.acp-terminal-panel\.is-resizing[\s\S]*transition: none/);
    assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
    assert.match(animationStyles, /@keyframes acp-terminal-content-reveal/);
    assert.match(source, /function reducedMotionEnabled\(\)/);
    assert.match(source, /event\.propertyName === 'height'/);
    assert.match(source, /setProperty\('--acp-terminal-target-height'/);
    assert.match(source, /immediate: true/);
});


test('terminal is a sibling that resizes the main chat viewport', () => {
    assert.match(
        index,
        /id="chatBoxWarning"><\/div>[\s\S]*?id="chatModelUnavailable"[\s\S]*?<\/div>\s*<\/div>\s*<\/div>\s*<section class="acp-terminal-panel"/,
    );
    assert.match(
        styles,
        /\.chat-container\.acp-terminal-is-open > \.chat-container-main[\s\S]*flex: 1 1 0/,
    );
    assert.match(styles, /\.chat-container\.acp-terminal-is-open[\s\S]*overflow: hidden/);
    assert.match(source, /el\('chatContainer'\)\?\.classList\.add\('acp-terminal-is-open'\)/);
    assert.doesNotMatch(source, /acp-terminal-start-screen/);
});


test('new-chat and existing-chat surfaces share the same bounded terminal height', () => {
    assert.match(source, /function syncDefaultPanelHeight\(\)/);
    assert.match(source, /const preferredRatio = mobile \? 0\.44 : 0\.38/);
    assert.match(source, /setPanelHeight\(Math\.min\(preferred, maximum\), \{ customized: false \}\)/);
    assert.match(source, /syncDefaultPanelHeight\(\);\s*state\.modelId/);
});
