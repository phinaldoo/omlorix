const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

const CANVAS_WIDGET_PATH = path.join(__dirname, 'canvas-widget.js');
const CANVAS_LIFECYCLE_PATH = path.join(__dirname, 'canvas-widget', 'lifecycle.js');
const CANVAS_WIDGET_CSS_PATH = path.join(__dirname, '..', '..', 'css', 'chat', 'canvas-widget.css');

test('Canvas HTML preview exposes a translated reload action in its header', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const css = readFrontendSource(CANVAS_WIDGET_CSS_PATH, 'utf8');

    assert.match(source, /id="canvas-html-ReloadBtn"/);
    assert.match(source, /getPreviewHeaderIcon\('redo_circle'\)/);
    assert.match(source, /aria-label:code_block_reload_preview;title:code_block_reload_preview/);
    assert.match(css, /\.canvas-html-reload-btn\s*\{[^}]*display: none;/);
    assert.match(
        css,
        /\.canvas-markdown-preview-panel\[data-content-type="html"\] \.canvas-html-reload-btn\s*\{[^}]*display: flex;/,
    );
});

test('Canvas HTML reload rebuilds the iframe from the latest draft and opens Preview', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const functionMatch = source.match(/function reloadHtmlPreview\(\) \{([\s\S]*?)\n    \}/);

    assert.ok(functionMatch, 'reloadHtmlPreview should exist');
    assert.match(functionMatch[1], /normalizeContentType\(draft\.contentType\) !== 'html'/);
    assert.match(functionMatch[1], /getRenderableContentForDraft\(state\.activeDraftKey, draft\.content \|\| ''\)/);
    assert.match(functionMatch[1], /clearPreviewRenderTimer\(state\.activeDraftKey\)/);
    assert.match(functionMatch[1], /setHtmlViewMode\('preview'\)/);
    assert.match(functionMatch[1], /renderHTMLPreviewInto\(iframe, htmlContent, state\.activeDraftKey\)/);
    assert.match(source, /htmlReloadBtn\.addEventListener\('click', reloadHtmlPreview\)/);
});

test('Canvas lifecycle reads pending HTML consent through mutable state', () => {
    const entrySource = fs.readFileSync(CANVAS_WIDGET_PATH, 'utf8');
    const lifecycleSource = fs.readFileSync(CANVAS_LIFECYCLE_PATH, 'utf8');

    assert.doesNotMatch(lifecycleSource, /openShareModal, pendingHtmlExternalResourceConsent/);
    assert.match(lifecycleSource, /state\.pendingHtmlExternalResourceConsent\?\.draftKey/);
    assert.match(entrySource, /get pendingHtmlExternalResourceConsent\(\)/);
    assert.match(entrySource, /set pendingHtmlExternalResourceConsent\(value\)/);
});
