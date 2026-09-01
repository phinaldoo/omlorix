const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ICONS_PATH = path.join(__dirname, 'icons.js');
const EDITOR_CSS_PATH = path.join(__dirname, '..', '..', 'css', 'chat', 'markdown_editor.css');
const EXPECTED_ICON_NAMES = [
    'undo', 'redo', 'bold', 'italic', 'underline', 'strike', 'code', 'sup',
    'sub', 'color', 'highlight', 'paint', 'quote', 'list', 'ordered', 'task',
    'outdent', 'indent', 'alignLeft', 'alignCenter', 'alignRight', 'alignJustify',
    'link', 'image', 'table', 'divider', 'more', 'plus', 'trash', 'clear',
    'check', 'omlorix',
];

/**
 * Evaluates the shared icon registry without a browser. The registry only
 * references the DOM from helper functions that are not called at load time,
 * so a minimal VM context is enough to inspect the generated SVG markup.
 *
 * @returns {Record<string, string>} The canvas markdown editor icon registry.
 */
function loadMarkdownEditorIcons() {
    const context = vm.createContext({});
    vm.runInContext(fs.readFileSync(ICONS_PATH, 'utf8'), context, { filename: ICONS_PATH });
    return context.Icons.markdownEditorIcons;
}

test('canvas markdown editor exposes a complete SVG icon registry', () => {
    const icons = loadMarkdownEditorIcons();
    assert.deepEqual(Object.keys(icons), EXPECTED_ICON_NAMES);

    for (const [name, svg] of Object.entries(icons)) {
        // The shared registry intentionally includes both filled artwork and
        // regular-weight stroke artwork. Toolbar CSS supplies only the common
        // 20 px dimensions, so the registry test verifies
        // that each entry remains an SVG with drawable path data instead of
        // locking every icon to one rendering technique.
        assert.match(svg, /<svg\b/u, `${name} must provide SVG markup`);
        assert.match(svg, /<path\b[^>]*\bd=["'][^"']+["']/u, `${name} must contain drawable path data`);
    }

    const css = fs.readFileSync(EDITOR_CSS_PATH, 'utf8');
    const toolbarSvgRule = css.match(/\.canvas-md-toolbar-btn svg\s*\{(?<body>[^}]+)\}/u);
    assert.ok(toolbarSvgRule, 'toolbar SVG rule must exist');
    assert.match(toolbarSvgRule.groups.body, /width:\s*20px/u);
    assert.match(toolbarSvgRule.groups.body, /height:\s*20px/u);
    assert.doesNotMatch(toolbarSvgRule.groups.body, /fill:/u);
    assert.doesNotMatch(toolbarSvgRule.groups.body, /stroke:/u);

    // Toolbar hover backgrounds must use the shared theme surface. The retired
    // `--hover-color` token is undefined in dark mode and previously exposed
    // the light fallback shown as a white heading button.
    assert.match(css, /--canvas-md-bg-softer:\s*var\(--surface-interactive-hover,\s*var\(--hover,/u);
    assert.doesNotMatch(css, /--canvas-md-bg-softer:\s*var\(--hover-color,/u);

    // Both the lightweight textarea gutter and the CodeMirror gutter must use
    // the application background so source line numbers never become a white
    // strip in dark mode.
    const sourceGutterRule = css.match(/^\.canvas-md-source-gutter\s*\{(?<body>[^}]+)\}/mu);
    const codeMirrorGutterRule = css.match(/^\.canvas-md-source-shell \.CodeMirror-gutters\s*\{(?<body>[^}]+)\}/mu);
    assert.match(sourceGutterRule?.groups.body || '', /background:\s*var\(--background\)/u);
    assert.match(codeMirrorGutterRule?.groups.body || '', /background:\s*var\(--background\)/u);
});
