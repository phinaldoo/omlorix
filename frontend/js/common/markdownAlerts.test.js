const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { readSendMessageSource } = require('../chat/sending/source.cjs');

const MarkdownIt = require('../vendor/markdown/markdown-it.min.js');
const markdownAlerts = require('./markdownAlerts.js');

const FRONTEND_ROOT = path.join(__dirname, '..', '..');
const ALERT_TYPES = ['note', 'tip', 'important', 'warning', 'caution'];

function createRenderer(options = {}) {
    return MarkdownIt({ html: false, breaks: true, ...options }).use(markdownAlerts.plugin);
}

test('GitHub alert syntax renders all supported types with semantic titles', () => {
    const markdown = ALERT_TYPES
        .map((type) => `> [!${type.toUpperCase()}]\n> ${type} body`)
        .join('\n\n');
    const html = createRenderer().render(markdown);

    ALERT_TYPES.forEach((type) => {
        assert.match(html, new RegExp(`<blockquote class="markdown-alert markdown-alert-${type}">`));
        assert.match(html, new RegExp(`markdown-alert-icon-${type}`));
    });
    assert.equal((html.match(/markdown-alert-title/g) || []).length, ALERT_TYPES.length);
    assert.doesNotMatch(html, /\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]/);
});

test('alert bodies preserve multiline Markdown blocks and translated labels', () => {
    const previousTranslation = globalThis.getTranslation;
    globalThis.getTranslation = (key, fallback) => (key === 'markdown_alert_tip' ? 'Translated tip' : fallback);

    try {
        const html = createRenderer().render('> [!tip]\n>\n> - First\n> - **Second**');
        assert.match(html, /<span class="markdown-alert-label">Translated tip<\/span>/);
        assert.match(html, /<ul>/);
        assert.match(html, /<strong>Second<\/strong>/);
        assert.doesNotMatch(html, /<p><\/p>/);
    } finally {
        if (previousTranslation === undefined) delete globalThis.getTranslation;
        else globalThis.getTranslation = previousTranslation;
    }
});

test('ordinary blockquotes and alert-looking code remain unchanged', () => {
    const renderer = createRenderer();
    const normalHtml = renderer.render('> Ordinary quote\n\n> [!NOTE] remains ordinary on one line');
    const codeHtml = renderer.render('```markdown\n> [!WARNING]\n> code only\n```');

    assert.equal((normalHtml.match(/<blockquote>/g) || []).length, 2);
    assert.doesNotMatch(normalHtml, /markdown-alert/);
    assert.match(normalHtml, /\[!NOTE\] remains ordinary on one line/);
    assert.doesNotMatch(codeHtml, /markdown-alert/);
    assert.match(codeHtml, /\[!WARNING\]/);
});

test('trusted alert icons are added only by the post-sanitization enhancer', () => {
    const iconElement = {
        childNodes: [],
        classList: { contains: (name) => name === 'markdown-alert-icon-tip' },
        innerHTML: '',
        querySelectorAll: () => [],
    };
    const container = {
        querySelectorAll: () => [iconElement],
    };
    const previousIcons = globalThis.Icons;
    globalThis.Icons = { markdownAlertTip: '<svg data-test-icon="tip"></svg>' };

    try {
        markdownAlerts.enhanceIcons(container);
        assert.equal(iconElement.innerHTML, '<svg data-test-icon="tip"></svg>');
    } finally {
        if (previousIcons === undefined) delete globalThis.Icons;
        else globalThis.Icons = previousIcons;
    }
});

test('every Markdown surface and locale includes alert support', () => {
    const rendererFiles = [
        'js/chat/markdown_editor.js',
        'js/chat-share.js',
        'js/canvas-share.js',
        'js/common/legalPage.js',
    ];
    const rendererSources = [
        readSendMessageSource(),
        ...rendererFiles.map((relativePath) => (
            fs.readFileSync(path.join(FRONTEND_ROOT, relativePath), 'utf8')
        )),
    ];
    rendererSources.forEach((source) => {
        assert.match(source, /markdownitAlerts/);
        assert.match(source, /ChatMarkdownAlerts\?\.enhanceIcons/);
    });

    const pageFiles = ['index.html', 'chat_share.html', 'canvas_share.html', 'legal.html'];
    pageFiles.forEach((relativePath) => {
        const source = fs.readFileSync(path.join(FRONTEND_ROOT, relativePath), 'utf8');
        assert.match(source, /\/js\/common\/markdownAlerts\.js/);
        assert.match(source, /\/css\/common\/markdownAlerts\.css/);
    });

    const localeRoot = path.join(FRONTEND_ROOT, 'i18n');
    fs.readdirSync(localeRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .forEach((entry) => {
            const dictionary = JSON.parse(fs.readFileSync(path.join(localeRoot, entry.name, 'index.json'), 'utf8'));
            ALERT_TYPES.forEach((type) => {
                assert.equal(typeof dictionary[`markdown_alert_${type}`], 'string');
                assert.ok(dictionary[`markdown_alert_${type}`].trim());
            });
        });
});
