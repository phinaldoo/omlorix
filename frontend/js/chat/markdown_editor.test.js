const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const MARKDOWN_EDITOR_PATH = path.join(__dirname, 'markdown_editor.js');
const MARKDOWN_EDITOR_CSS_PATH = path.join(__dirname, '..', '..', 'css', 'chat', 'markdown_editor.css');
const I18N_ROOT = path.join(__dirname, '..', '..', 'i18n');
const MARKDOWN_EDITOR_HISTORY_KEYS = [
    'markdown_editor_history',
    'markdown_editor_undo',
    'markdown_editor_redo',
];

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}`);
    assert.notEqual(start, -1, `${functionName} not found`);

    let bodyStart = -1;
    let parenDepth = 0;
    let sawOpeningParen = false;
    for (let index = start; index < source.length; index += 1) {
        const char = source[index];
        if (char === '(') {
            parenDepth += 1;
            sawOpeningParen = true;
            continue;
        }
        if (char === ')') {
            parenDepth -= 1;
            continue;
        }
        if (char === '{' && sawOpeningParen && parenDepth === 0) {
            bodyStart = index;
            break;
        }
    }
    assert.notEqual(bodyStart, -1, `${functionName} body start not found`);

    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') {
            depth += 1;
        } else if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }

    throw new Error(`${functionName} body was not closed`);
}

function escapeHtmlValue(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function decodeHtmlValue(value) {
    return String(value ?? '')
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
        .replace(/&#([0-9]+);/g, (_, decimal) => String.fromCodePoint(parseInt(decimal, 10)))
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&amp;/g, '&');
}

function createDocumentShim() {
    return {
        createElement(tagName) {
            const node = {
                _innerHTML: '',
                _textContent: '',
                value: '',
                set textContent(value) {
                    this._textContent = String(value ?? '');
                    this._innerHTML = escapeHtmlValue(value);
                },
                get textContent() {
                    return this._textContent;
                },
                set innerHTML(value) {
                    this._innerHTML = String(value ?? '');
                    if (String(tagName).toLowerCase() === 'textarea') {
                        this.value = decodeHtmlValue(value);
                        this._textContent = this.value;
                    }
                },
                get innerHTML() {
                    return this._innerHTML;
                },
            };
            return node;
        },
    };
}

function loadInlineMarkdownHelpers() {
    const source = fs.readFileSync(MARKDOWN_EDITOR_PATH, 'utf8');
    const functionNames = [
        'escapeHtml',
        'withLineBreaks',
        'decodeHtmlEntities',
        'getOmlorixFileIdFromUrl',
        'buildOmlorixFileUrl',
        'buildOmlorixFileDownloadUrl',
        'sanitizeMarkdownUrl',
        'sanitizeMarkdownLinkUrl',
        'sanitizeMarkdownImageUrl',
        'safeUrl',
        'applyInlineMarkdown',
    ];
    const context = {
        Set,
        String,
        OMLORIX_FILE_SCHEME: 'omlorix-file://',
        document: createDocumentShim(),
    };
    context.globalThis = context;
    vm.createContext(context);
    vm.runInContext(
        `${functionNames.map((name) => extractFunction(source, name)).join('\n\n')}\nthis.__exports = { ${functionNames.join(', ')} };`,
        context,
        { filename: 'markdown_editor.test.js' },
    );
    return context.__exports;
}

test('applyInlineMarkdown rejects dangerous link and image URL schemes', () => {
    const { applyInlineMarkdown } = loadInlineMarkdownHelpers();

    assert.equal(
        applyInlineMarkdown('[click](javascript:window.poc=1337)'),
        'click',
    );
    assert.equal(
        applyInlineMarkdown('[click][bad]', { bad: 'javascript:window.poc=1337' }),
        'click',
    );
    assert.equal(
        applyInlineMarkdown('[click](java&#x73;cript:window.poc=1337)'),
        'click',
    );
    assert.equal(
        applyInlineMarkdown('![x](javascript:window.poc=1337)'),
        'x',
    );
    assert.equal(
        applyInlineMarkdown('![x][bad]', { bad: 'javascript:window.poc=1337' }),
        'x',
    );
});

test('applyInlineMarkdown preserves safe markdown links and images', () => {
    const { applyInlineMarkdown } = loadInlineMarkdownHelpers();

    assert.equal(
        applyInlineMarkdown('[docs](https://example.com/path?a=1&b=2)'),
        '<a href="https://example.com/path?a=1&amp;b=2">docs</a>',
    );
    assert.equal(
        applyInlineMarkdown('[email](mailto:user@example.com)'),
        '<a href="mailto:user@example.com">email</a>',
    );
    assert.equal(
        applyInlineMarkdown('[relative](/docs/readme.md)'),
        '<a href="/docs/readme.md">relative</a>',
    );
    assert.equal(
        applyInlineMarkdown('![logo](https://example.com/logo.png)'),
        '<img src="https://example.com/logo.png" alt="logo" class="canvas-md-inline-image">',
    );
});

test('safeUrl converts valid omlorix file URLs and rejects invalid custom schemes', () => {
    const { safeUrl } = loadInlineMarkdownHelpers();

    assert.equal(
        safeUrl('omlorix-file://0c3e625d-d147-464b-8f02-9360667384be', true),
        '/api/v1/files/download?file_id=0c3e625d-d147-464b-8f02-9360667384be&inline=true',
    );
    assert.equal(safeUrl('omlorix-file://', true), '');
    assert.equal(safeUrl('omlorix-file://bad id', true), '');
    assert.equal(safeUrl('custom-scheme://example.test/image.png', true), '');
});

test('rendered file references are parsed through an inert template before rewriting', () => {
    const source = fs.readFileSync(MARKDOWN_EDITOR_PATH, 'utf8');
    const renderBody = extractFunction(source, 'renderMarkdownToHtml');
    const helperBody = extractFunction(source, 'prepareRenderedHtmlFileRefs');
    const templateBody = extractFunction(source, 'createInertHtmlTemplate');

    assert.match(renderBody, /const sanitized = sanitizeEditorHtml\(rendered\);[\s\S]*const prepared = prepareRenderedHtmlFileRefs\(sanitized\);/);
    assert.match(helperBody, /createInertHtmlTemplate\(html\)/);
    assert.match(templateBody, /document\.createElement\('template'\)/);
    assert.doesNotMatch(helperBody, /document\.createElement\('div'\);[\s\S]*container\.innerHTML = String\(html \|\| ''\)/);
});

test('renderMarkdownToHtml upgrades every fenced block through the shared chat renderer', () => {
    const source = fs.readFileSync(MARKDOWN_EDITOR_PATH, 'utf8');
    const renderBody = extractFunction(source, 'renderMarkdownToHtml');
    const postProcessBody = extractFunction(source, 'postProcessRenderedHtml');
    const upgradeBody = extractFunction(source, 'upgradeEditorFencedCodeBlocks');
    const sharedBlockBody = extractFunction(source, 'createSharedCodeBlockHost');
    const sanitizerBody = extractFunction(source, 'sanitizeEditorHtml');

    assert.match(renderBody, /const renderer = getMarkdownRenderer\(\)/);
    assert.match(postProcessBody, /upgradeEditorFencedCodeBlocks\(container\)/);
    assert.match(upgradeBody, /container\.querySelectorAll\('pre'\)/);
    assert.match(sharedBlockBody, /window\.renderMarkdownContent\(staging, buildFencedMarkdown\(language, source\)\)/);
    assert.match(sharedBlockBody, /className = 'canvas-md-shared-code-block markdown-body'/);
    assert.match(sharedBlockBody, /contenteditable', 'false'/);
    assert.match(sanitizerBody, /FORBID_TAGS: \[[^\]]*'button'/);
});

test('optional Markdown plugin failures do not block or repeat registration', () => {
    const source = fs.readFileSync(MARKDOWN_EDITOR_PATH, 'utf8');
    const registerBody = extractFunction(source, 'registerAvailableMarkdownPlugins');
    const pluginNames = [
        'markdownitDeflist',
        'markdownitAbbr',
        'markdownitMark',
        'markdownitSub',
        'markdownitSup',
        'markdownitTaskLists',
    ];
    const attempts = [];
    const context = {
        registeredMarkdownPlugins: new Set(),
        console: { error() {} },
        window: {},
    };
    pluginNames.forEach((name) => {
        context.window[name] = { name };
    });
    const renderer = {
        use(plugin) {
            attempts.push(plugin.name);
            if (plugin.name === 'markdownitAbbr') throw new Error('broken plugin');
        },
    };
    context.renderer = renderer;
    vm.createContext(context);
    vm.runInContext(`${registerBody}\nregisterAvailableMarkdownPlugins(renderer); registerAvailableMarkdownPlugins(renderer);`, context);

    assert.deepEqual(attempts, pluginNames);
    assert.deepEqual([...context.registeredMarkdownPlugins], pluginNames);
});

test('shared preview code blocks serialize only their source back to fenced markdown', () => {
    const source = fs.readFileSync(MARKDOWN_EDITOR_PATH, 'utf8');
    const blockToMarkdownBody = extractFunction(source, 'blockToMarkdown');
    const sharedSerializeBody = extractFunction(source, 'fencedCodeFromSharedBlock');
    const fenceBody = extractFunction(source, 'buildFencedMarkdown');

    assert.match(blockToMarkdownBody, /node\.classList\.contains\('canvas-md-shared-code-block'\)/);
    assert.match(blockToMarkdownBody, /fencedCodeFromSharedBlock\(node\)/);
    assert.match(sharedSerializeBody, /\.code-block-panel-code code\[data-code-id\]/);
    assert.match(sharedSerializeBody, /host\?\.dataset\?\.canvasMdLanguage/);
    assert.match(fenceBody, /Math\.max\(3, longestRun \+ 1\)/);
    assert.doesNotMatch(sharedSerializeBody, /code-block-actions|code-block-preview-pane/);
});

test('Markdown alerts serialize back to GitHub alert syntax without generated presentation', () => {
    const source = fs.readFileSync(MARKDOWN_EDITOR_PATH, 'utf8');
    const blockToMarkdownBody = extractFunction(source, 'blockToMarkdown');

    assert.match(blockToMarkdownBody, /node\.classList\.contains\('markdown-alert'\)/);
    assert.match(blockToMarkdownBody, /markdown-alert-\$\{type\}/);
    assert.match(blockToMarkdownBody, /markdown-alert-title/);
    assert.match(blockToMarkdownBody, /alertType\.toUpperCase\(\)/);
    assert.match(blockToMarkdownBody, /return alertMarkdown\.split\('\\n'\)/);
});

test('shared code preview resources are cleaned up whenever editor markup is replaced', () => {
    const source = fs.readFileSync(MARKDOWN_EDITOR_PATH, 'utf8');
    const cleanupBody = extractFunction(source, 'cleanupEditorCodeBlockPreviews');
    const hydrateBody = extractFunction(source, 'hydrateEditorCodeBlocks');
    const historyBody = extractFunction(source, 'getEditorHistoryHtml');
    const replaceBody = extractFunction(source, 'replaceEditorMarkdown');

    assert.match(cleanupBody, /window\.cleanupMarkdownCodeBlockPreviews\(editor\)/);
    assert.match(replaceBody, /cleanupEditorCodeBlockPreviews\(editor\)/);
    assert.match(replaceBody, /hydrateEditorCodeBlocks\(editor\)/);
    assert.match(hydrateBody, /if \(!editor\.isConnected\) return false/);
    assert.match(hydrateBody, /window\.finalizeCodeBlockPreviewState\?\.\(host\)/);
    assert.match(hydrateBody, /queueMicrotask/);
    assert.match(historyBody, /window\.prepareMarkdownCodeBlocksForTransfer\?\.\(clone\)/);
    assert.match(source, /destroy\(\) \{[\s\S]*cleanupEditorCodeBlockPreviews\(editor\)/);
});

test('compact markdown editor more menu exposes undo and redo actions', () => {
    const source = fs.readFileSync(MARKDOWN_EDITOR_PATH, 'utf8');
    const menuItemBody = extractFunction(source, 'menuItem');
    const compactBody = extractFunction(source, 'isCompactEditorChrome');
    const moreMenuBody = extractFunction(source, 'openMoreMenu');

    assert.match(menuItemBody, /options = \{\}/);
    assert.match(menuItemBody, /if \(options\.disabled\)/);
    assert.match(menuItemBody, /button\.disabled = true/);
    assert.match(menuItemBody, /if \(button\.disabled\) return/);
    assert.match(compactBody, /shell\.getBoundingClientRect\(\)\.width <= 600/);
    assert.match(moreMenuBody, /if \(isCompactEditorChrome\(\)\)/);
    assert.match(moreMenuBody, /markdown_editor_history/);
    assert.match(moreMenuBody, /markdown_editor_undo[\s\S]*'undo'[\s\S]*\(\) => undo\(\)[\s\S]*disabled: !historyState\.canUndo/);
    assert.match(moreMenuBody, /markdown_editor_redo[\s\S]*'redo'[\s\S]*\(\) => redo\(\)[\s\S]*disabled: !historyState\.canRedo/);
});

test('markdown value refreshes preserve the active nested viewport without stealing focus', () => {
    const source = fs.readFileSync(MARKDOWN_EDITOR_PATH, 'utf8');
    const captureBody = extractFunction(source, 'captureViewportScrollState');
    const restoreBody = extractFunction(source, 'restoreViewportScrollState');
    const switchBody = extractFunction(source, 'switchView');

    assert.match(captureBody, /sourceCodeMirror\?\.getScrollInfo\?\.\(\) \|\| null/);
    assert.match(captureBody, /editorScrollTop: editorView\.scrollTop/);
    assert.match(restoreBody, /switchView\(requestedView, \{ focus: false \}\)/);
    assert.match(restoreBody, /sourceCodeMirror\.scrollTo\(sourceLeft, sourceTop\)/);
    assert.match(restoreBody, /window\.requestAnimationFrame\(restore\)/);
    assert.match(switchBody, /\{ focus = true \} = \{\}/);
    assert.match(source, /setValue\(value\) \{[\s\S]*captureViewportScrollState\(\)[\s\S]*restoreViewportScrollState\(viewportScrollState\)/);
    assert.match(source, /getScrollState: captureViewportScrollState/);
    assert.match(source, /restoreScrollState: restoreViewportScrollState/);
});

test('shared markdown renderer registers optional plugins that load later exactly once', () => {
    const source = fs.readFileSync(MARKDOWN_EDITOR_PATH, 'utf8');
    const start = source.indexOf('    function registerAvailableMarkdownPlugins(');
    const end = source.indexOf('    function renderMarkdownToHtml(', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);

    const calls = [];
    const renderer = {
        renderer: { rules: {} },
        use(plugin, options) {
            calls.push({ plugin, options });
            return this;
        },
    };
    const windowStub = {
        markdownit: () => renderer,
    };
    const { getMarkdownRenderer } = Function('window', `
        let sharedMarkdownRenderer = null;
        const registeredMarkdownPlugins = new Set();
        const isMermaidLanguage = () => false;
        const buildMermaidBlockHtml = () => '';
        ${source.slice(start, end)}
        return { getMarkdownRenderer };
    `)(windowStub);

    assert.equal(getMarkdownRenderer(), renderer);
    assert.equal(calls.length, 0);

    const markPlugin = () => {};
    windowStub.markdownitMark = markPlugin;
    assert.equal(getMarkdownRenderer(), renderer);
    assert.deepEqual(calls, [{ plugin: markPlugin, options: undefined }]);

    // Reusing the shared renderer must not register the same plugin twice.
    assert.equal(getMarkdownRenderer(), renderer);
    assert.equal(calls.length, 1);
});

test('completed markdown tables wrap before using horizontal overflow', () => {
    const css = fs.readFileSync(MARKDOWN_EDITOR_CSS_PATH, 'utf8');

    assert.match(css, /\.canvas-md-editor-table-wrap\s*\{[^}]*max-width: 100%;[^}]*overflow-x: auto;/s);
    assert.match(css, /\.canvas-md-rich-editor table\s*\{[^}]*width: 100%;[^}]*min-width: 100%;/s);
    assert.match(css, /\.canvas-md-rich-editor th,\s*\.canvas-md-rich-editor td\s*\{[^}]*min-width: 0;[^}]*overflow-wrap: anywhere;/s);
    assert.match(css, /\.canvas-md-rich-editor th\s*\{[^}]*background: var\(--surface-muted, var\(--canvas-md-bg-soft\)\);[^}]*color: var\(--canvas-md-text\);/s);
    assert.match(css, /--canvas-md-text-muted: var\(--text-color-secondary, #6b7280\);/);
    assert.match(css, /--canvas-md-bg-soft: var\(--surface-muted, var\(--input-bg, #f7f7f8\)\);/);
    assert.match(css, /--canvas-md-interactive-accent: var\(--chat-markdown-link-color, var\(--canvas-md-accent\)\);/);
    assert.match(css, /\.canvas-md-editor-table-toolbar\s*\{[^}]*background: var\(--surface-muted, var\(--canvas-md-bg-soft\)\);[^}]*color: var\(--canvas-md-text-muted\);/s);
    assert.match(css, /\.canvas-md-toolbar-btn\.is-active\s*\{[^}]*color: var\(--canvas-md-interactive-accent\);[^}]*background: color-mix\(in srgb, var\(--canvas-md-interactive-accent\) 12%, transparent\);/s);
    assert.match(css, /\.canvas-md-toolbar-btn:not\(:disabled\):hover,[^}]*background: var\(--canvas-md-bg-softer\);[^}]*color: var\(--canvas-md-text\);/s);
    assert.doesNotMatch(css, /\.canvas-md-rich-editor table\s*\{[^}]*min-width: max-content;/s);
});

test('markdown editor history menu translations exist in every supported locale', () => {
    const locales = fs.readdirSync(I18N_ROOT)
        .filter((entry) => fs.existsSync(path.join(I18N_ROOT, entry, 'index.json')));

    locales.forEach((locale) => {
        const dictionary = JSON.parse(
            fs.readFileSync(path.join(I18N_ROOT, locale, 'index.json'), 'utf8'),
        );

        MARKDOWN_EDITOR_HISTORY_KEYS.forEach((key) => {
            assert.ok(
                Object.prototype.hasOwnProperty.call(dictionary, key),
                `${locale} is missing ${key}`,
            );
        });
    });
});
