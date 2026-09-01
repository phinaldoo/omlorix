const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const INDEX_PATH = path.join(__dirname, '../../index.html');
const CSS_PATH = path.join(__dirname, '../../css/chat/modelSelect.css');
const SCRIPT_PATH = path.join(__dirname, 'modelSelect.js');

test('main-header model selector starts with a name skeleton', () => {
    const html = fs.readFileSync(INDEX_PATH, 'utf8');
    assert.doesNotMatch(html, /id="modelSelectLabel"/);
    assert.match(html, /id="modelSelectToggle"[\s\S]*?id="modelSelectTriggerSkeleton"/);
    assert.match(html, /id="modelSelectTriggerSkeleton"/);
});

test('model selector loading always settles the header skeleton', () => {
    const source = fs.readFileSync(SCRIPT_PATH, 'utf8');
    assert.match(source, /async function ModelSelectLoadModels\([^)]*\)[\s\S]*?finally\s*{\s*finishModelSelectTriggerLoading\(\);/);
    assert.match(source, /function updateModelSelectLabel\([^)]*\)[\s\S]*?finishModelSelectTriggerLoading\(\);/);
});

test('model name skeleton uses the shared animation and reduced-motion handling', () => {
    const css = fs.readFileSync(CSS_PATH, 'utf8');
    assert.match(css, /\.model-select-trigger-skeleton\s*{[\s\S]*animation:\s*skeleton-pulse/);
    assert.match(css, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.model-select-trigger-skeleton[\s\S]*animation:\s*none !important/);
});
