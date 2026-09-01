const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const CANVAS_SHARE_PATH = path.join(__dirname, 'canvas-share.js');
const CANVAS_SHARE_HTML_PATH = path.join(__dirname, '..', 'canvas_share.html');
const CHAT_SHARE_CSS_PATH = path.join(__dirname, '..', 'css', 'chatShare', 'chat-share.css');
const MERMAID_RUNTIME_PATH = path.join(__dirname, 'common', 'mermaidRuntime.js');
const I18N_ROOT = path.join(__dirname, '..', 'i18n');
const CANVAS_SHARE_KEYS = [
    'canvas_share_page_title',
    'canvas_share_document_title',
    'canvas_share_loading_label',
    'canvas_share_html_preview_title',
    'canvas_share_html_permissions_aria',
    'canvas_share_html_interactive_notice',
    'canvas_share_html_external_blocked_notice',
    'canvas_share_html_external_allowed_notice',
    'canvas_share_html_enable_interactions',
    'canvas_share_html_disable_interactions',
    'canvas_share_html_allow_external_content',
    'canvas_share_html_block_external_content',
    'canvas_share_password_title',
    'canvas_share_password_help',
    'canvas_share_password_label',
    'canvas_share_password_unlock_btn',
    'canvas_share_password_required',
    'canvas_share_password_error_empty',
    'canvas_share_password_error_invalid',
    'canvas_share_error_title',
    'canvas_share_error_default',
    'canvas_share_error_not_found',
    'canvas_share_error_invalid_url',
    'canvas_share_request_failed_status',
    'canvas_share_mermaid_rendering',
    'canvas_share_mermaid_empty',
    'canvas_share_mermaid_unavailable',
    'canvas_share_mermaid_error',
    'canvas_share_mermaid_unknown_error',
];

function extractConst(source, name) {
    const match = source.match(new RegExp(`const ${name} = [^;]+;`));
    assert.ok(match, `${name} not found`);
    return match[0];
}

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

class FakeNode {
    constructor(tagName, attributes = {}, textContent = '') {
        this.tagName = String(tagName || '').toUpperCase();
        this.textContent = textContent;
        this.removed = false;
        this._attributes = { ...attributes };
        this._syncAttributes();
        this.innerHTML = '';
    }

    _syncAttributes() {
        this.attributes = Object.entries(this._attributes).map(([name, value]) => ({ name, value }));
    }

    setAttribute(name, value) {
        this._attributes[name] = String(value);
        this._syncAttributes();
    }

    removeAttribute(name) {
        delete this._attributes[name];
        this._syncAttributes();
    }

    getAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this._attributes, name)
            ? this._attributes[name]
            : null;
    }

    hasAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this._attributes, name);
    }

    remove() {
        this.removed = true;
    }
}

class FakeRoot {
    constructor({ resourceNodes = [], styleNodes = [], allNodes = [] } = {}) {
        this._resourceNodes = resourceNodes;
        this._styleNodes = styleNodes;
        this._allNodes = allNodes;
    }

    querySelectorAll(selector) {
        if (selector === 'iframe, frame, object, embed, portal, link, audio, video, source, track') {
            return this._resourceNodes.filter((node) => !node.removed);
        }
        if (selector === 'style') {
            return this._styleNodes.filter((node) => !node.removed);
        }
        if (selector === '*') {
            return this._allNodes.filter((node) => !node.removed);
        }
        return [];
    }
}

function loadFunctions(functionNames, extraContext = {}) {
    const source = fs.readFileSync(path.join(__dirname, 'canvas-share.js'), 'utf8');
    const fragments = [
        extractConst(source, 'RESOURCE_TAGS'),
        extractConst(source, 'URL_ATTRS'),
        ...functionNames.map((name) => extractFunction(source, name)),
    ];
    const context = {
        Set,
        String,
        window: {},
        ...extraContext,
    };
    context.globalThis = context;
    vm.createContext(context);
    vm.runInContext(
        `${fragments.join('\n\n')}\nthis.__exports = { ${functionNames.join(', ')} };`,
        context,
        { filename: 'canvas-share.test.js' },
    );
    return context.__exports;
}

test('setSanitizedHtml and setSanitizedSvg delegate to shared sanitizer helpers', () => {
    const calls = [];
    const target = { innerHTML: '' };
    const { setSanitizedHtml, setSanitizedSvg } = loadFunctions(
        ['setSanitizedHtml', 'setSanitizedSvg'],
        {
            window: {
                ChatSanitizer: {
                    setInnerHtml(node, html, options) {
                        calls.push(['html', node, html, options]);
                        node.innerHTML = 'sanitized-html';
                    },
                    setSvg(node, svg) {
                        calls.push(['svg', node, svg]);
                        node.innerHTML = 'sanitized-svg';
                    },
                },
            },
        },
    );

    assert.equal(setSanitizedHtml(target, '<p>unsafe</p>', { allowDataAttrs: true }), true);
    assert.equal(target.innerHTML, 'sanitized-html');
    assert.deepEqual(calls[0], ['html', target, '<p>unsafe</p>', { allowDataAttrs: true }]);

    assert.equal(setSanitizedSvg(target, '<svg><script/></svg>'), true);
    assert.equal(target.innerHTML, 'sanitized-svg');
    assert.deepEqual(calls[1], ['svg', target, '<svg><script/></svg>']);
});

test('public Canvas HTML shares never enable authenticated file hydration', () => {
    const source = fs.readFileSync(path.join(__dirname, 'canvas-share.js'), 'utf8');

    assert.match(source, /hydrateAuthenticatedFiles:\s*false/);
    assert.match(source, /payload\?\.assets/);
    assert.match(source, /replaceSharedAssetReferences/);
    assert.match(source, /URL\.createObjectURL\(blob\)/);
});

test('public Canvas asset URLs survive back-forward cache page hides', () => {
    const source = fs.readFileSync(path.join(__dirname, 'canvas-share.js'), 'utf8');

    assert.match(source, /window\.addEventListener\('pagehide', \(event\) => \{/);
    assert.match(source, /if \(event\.persisted\) return;/);
    assert.doesNotMatch(source, /sharedAssetObjectUrls\.clear\(\);\s*}, \{ once: true \}\);/);
});

test('sanitizeRenderedArtifactNode removes unsafe resources and unsafe anchor URLs', () => {
    const { sanitizeRenderedArtifactNode } = loadFunctions(
        [
            'isSameDocumentFragmentUrl',
            'cssContainsExternalFetch',
            'isSafeArtifactAnchorUrl',
            'sanitizeRenderedArtifactNode',
        ],
        {
            window: {
                ChatSanitizer: {
                    isSafeUrl(url) {
                        return /^https:\/\/safe\.example\/|^#/i.test(String(url || '').trim());
                    },
                },
            },
        },
    );

    const audio = new FakeNode('audio', { src: 'https://safe.example/audio.mp3' });
    const style = new FakeNode('style', {}, '@import url(https://evil.example/payload.css);');
    const safeAnchor = new FakeNode('a', { href: 'https://safe.example/docs' });
    const unsafeAnchor = new FakeNode('a', { href: 'data:text/html,<svg/onload=alert(1)>' });
    const eventDiv = new FakeNode('div', { onclick: 'alert(1)', style: 'background:url(https://evil.example/x.png)' });
    const fragmentDiv = new FakeNode('div', { data: '#local-fragment' });
    const remoteDiv = new FakeNode('div', { data: 'https://evil.example/file.bin' });
    const root = new FakeRoot({
        resourceNodes: [audio],
        styleNodes: [style],
        allNodes: [safeAnchor, unsafeAnchor, eventDiv, fragmentDiv, remoteDiv],
    });

    const result = sanitizeRenderedArtifactNode(root);

    assert.equal(result, root);
    assert.equal(audio.removed, true);
    assert.equal(style.removed, true);

    assert.equal(safeAnchor.getAttribute('href'), 'https://safe.example/docs');
    assert.equal(safeAnchor.getAttribute('target'), '_blank');
    assert.equal(safeAnchor.getAttribute('rel'), 'noopener noreferrer');
    assert.equal(safeAnchor.getAttribute('referrerpolicy'), 'no-referrer');

    assert.equal(unsafeAnchor.hasAttribute('href'), false);
    assert.equal(eventDiv.hasAttribute('onclick'), false);
    assert.equal(eventDiv.hasAttribute('style'), false);
    assert.equal(fragmentDiv.getAttribute('data'), '#local-fragment');
    assert.equal(remoteDiv.hasAttribute('data'), false);
});

test('canvas share runtime uses translation keys for public page status text', () => {
    const source = fs.readFileSync(CANVAS_SHARE_PATH, 'utf8');
    const mermaidRuntimeSource = fs.readFileSync(MERMAID_RUNTIME_PATH, 'utf8');

    assert.match(source, /canvas_share_request_failed_status/);
    assert.match(source, /canvas_share_html_preview_title/);
    assert.match(source, /canvas_share_mermaid_rendering/);
    assert.match(source, /canvas_share_mermaid_empty/);
    assert.match(source, /canvas_share_mermaid_unavailable/);
    assert.match(source, /canvas_share_mermaid_error/);
    assert.match(source, /canvas_share_mermaid_unknown_error/);
    assert.match(source, /initializeMermaidRuntime\(\{[\s\S]*htmlLabels: false/);
    assert.match(mermaidRuntimeSource, /config\.htmlLabels = false/);
    assert.match(mermaidRuntimeSource, /config\.flowchart = \{ htmlLabels: false \}/);
    assert.doesNotMatch(source, /payload\?\.detail \|\| `Request failed \(\$\{response\.status\}\)`/);
    assert.doesNotMatch(source, /previewEl\.textContent = 'Rendering Mermaid diagram\.\.\.'/);
});

test('canvas share public page markup reuses chat share shell and localizes static placeholders', () => {
    const markup = fs.readFileSync(CANVAS_SHARE_HTML_PATH, 'utf8');

    assert.match(markup, /class="chat-share-page"[\s\S]*data-page="canvas-share"/);
    assert.match(markup, /href="\/css\/chatShare\/chat-share\.css"/);
    assert.match(markup, /id="canvasContent"[\s\S]*data-i18n-attr="aria-label:canvas_share_content_aria"/);
    assert.match(markup, /class="chat-share-password-view"/);
    assert.match(markup, /class="chat-share-password-form"/);
    assert.doesNotMatch(markup, /id="sharedHeader"/);
    assert.doesNotMatch(markup, /canvas-share-toolbar/);
    assert.doesNotMatch(markup, /copyCanvasBtn|downloadCanvasBtn/);
    assert.doesNotMatch(markup, />omlorix\.example</);
});

test('canvas share password button keeps visible fallback colors', () => {
    const chatShareCss = fs.readFileSync(CHAT_SHARE_CSS_PATH, 'utf8');

    assert.match(chatShareCss, /background:\s*var\(--primary-color,\s*var\(--admin-accent,\s*#3b82f6\)\)/);
    assert.match(chatShareCss, /border-top-color:\s*var\(--primary-color,\s*var\(--admin-accent,\s*#3b82f6\)\)/);
});

test('canvas share HTML previews use the isolated runtime with explicit network permission', () => {
    const source = fs.readFileSync(CANVAS_SHARE_PATH, 'utf8');

    assert.match(source, /const RESOURCE_TAGS = 'iframe, frame, object, embed, portal, link, audio, video, source, track';/);
    assert.match(source, /const HTML_PREVIEW_CSP = "default-src 'none';[\s\S]*navigate-to 'none'"/);
    assert.match(source, /doc\.querySelectorAll\('script, meta, base'\)\.forEach\(\(node\) => node\.remove\(\)\);/);
    assert.match(source, /function buildSandboxedPreviewDocument\(doc\)/);
    assert.match(source, /<meta charset="utf-8">/);
    assert.match(source, /window\.OmlorixCanvasHtmlPreview/);
    assert.match(source, /allowScripts: sharedHtmlPreviewState\.allowScripts\s*&& sharedHtmlPreviewState\.allowExternalContent/);
    assert.match(source, /allowExternalContent: sharedHtmlPreviewState\.allowExternalContent/);
    assert.match(source, /allowScripts: false,\s*\n\s*allowExternalContent: false/);
    assert.match(source, /scriptsButton\.disabled = !sharedHtmlPreviewState\.allowExternalContent/);
    assert.match(source, /if \(!sharedHtmlPreviewState\.allowExternalContent\) return/);
    assert.match(source, /sharedHtmlPreviewState\.allowScripts = false/);
    assert.match(source, /canvas_share_html_allow_external_content/);
    assert.doesNotMatch(source, /return `<!DOCTYPE html>\\n\$\{doc\.documentElement\.outerHTML\}`;/);
});

test('canvas share translations exist in every supported locale', () => {
    const locales = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    locales.forEach((locale) => {
        const dictionary = JSON.parse(
            fs.readFileSync(path.join(I18N_ROOT, locale, 'canvas-share.json'), 'utf8'),
        );

        CANVAS_SHARE_KEYS.forEach((key) => {
            assert.ok(
                Object.prototype.hasOwnProperty.call(dictionary, key),
                `${locale} is missing ${key}`,
            );
        });
    });
});
