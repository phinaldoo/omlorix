const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const CSS_PATH = path.join(__dirname, '../../css/chat/modelSelect.css');
const SCRIPT_PATH = path.join(__dirname, 'modelSelect.js');

test('mobile model sheet constrains its list to a real scroll area', () => {
    const css = fs.readFileSync(CSS_PATH, 'utf8');

    assert.match(css, /\.model-select-main-panel\s*{[\s\S]*?flex:\s*1 1 auto;[\s\S]*?min-height:\s*0;[\s\S]*?overflow:\s*hidden;/);
    assert.match(css, /\.model-select-list\s*{[\s\S]*?min-height:\s*0;[\s\S]*?overscroll-behavior-y:\s*contain;/);
});

test('mobile model sheet uses one gesture event API and preserves active drags across resize', () => {
    const source = fs.readFileSync(SCRIPT_PATH, 'utf8');

    assert.match(source, /if \(msMobile\.isDragging\) return;[\s\S]*?dropdown\.style\.transform = '';/);
    assert.match(source, /if \(msMobile\.supportsPointerEvents\(\)\)\s*{[\s\S]*?addEventListener\('pointerdown'[\s\S]*?}\s*else\s*{[\s\S]*?addEventListener\('touchstart'/);
    assert.match(source, /function getMobileDragCloseThreshold\(dropdown\)[\s\S]*?sheetHeight \* 0\.18/);
});
