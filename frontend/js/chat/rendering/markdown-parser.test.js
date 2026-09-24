const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function createRenderer() {
    const context = vm.createContext({
        window: {
            markdownit: require('../../vendor/markdown/markdown-it.min.js'),
            markdownitSup: require('../../vendor/markdown/markdown-it-sup.min.js'),
            markdownitSub: require('../../vendor/markdown/markdown-it-sub.min.js'),
            ChatSanitizer: { isSafeUrl: url => url.startsWith('https://') },
        },
        Icons: { copy: '' },
        getChatPreviewTranslation: (_key, fallback) => fallback,
    });
    for (const file of ['state.js', 'shared-utils.js', 'markdown-parser.js']) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, file), 'utf8'), context);
    }
    return context.getMarkdownRenderer();
}

test('exponential limits table preserves every formula for the KaTeX pass', () => {
    const source = fs.readFileSync(path.join(__dirname, 'fixtures/exponential-limits.md'), 'utf8');
    const renderer = createRenderer();
    const html = renderer.render(source);
    assert.equal((html.match(/<th\b/g) || []).length, 3);
    assert.equal((html.match(/<td\b/g) || []).length, 6);
    for (const [, formula] of source.matchAll(/\\\(([\s\S]*?)\\\)/g)) {
        const escaped = formula.replace(/</g, '&lt;').replace(/>/g, '&gt;');
        assert.ok(html.includes(`\\(${escaped}\\)`), formula);
    }
    assert.match(html, /\\\[\n\\lim_/);
    assert.doesNotMatch(html, /LATEXPLACEHOLDER|<sup>|<sub>|<a<1/);
    assert.equal(renderer.render(source), html, 'rerenders must preserve formulas too');
});

test('table cells retain inline markup and literal code alongside math', () => {
    const html = createRenderer().render(String.raw`| Content |
|---|
| **bold** and *italic* with \(x_1^2\) |
| [**link**](https://example.com) and \*literal\* |
| ` + '`\\(0<a<1\\)` and `*code*` |');
    assert.match(html, /<strong>bold<\/strong> and <em>italic<\/em> with \\\(x_1\^2\\\)/);
    assert.match(html, /<a [^>]*><strong>link<\/strong><\/a> and \*literal\*/);
    assert.match(html, /<code>\\\(0&lt;a&lt;1\\\)<\/code> and <code>\*code\*<\/code>/);
});

test('restored math is escaped text and preserves dollar signs and nested environments', () => {
    const html = createRenderer().render(String.raw`\(<img src=x onerror=alert(1)> & x\)

$$\begin{align}a&<b\\c&>d\end{align}$$`);
    assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt; &amp; x/);
    assert.ok(html.includes(String.raw`$$\begin{align}a&amp;&lt;b\\c&amp;&gt;d\end{align}$$`));
    assert.doesNotMatch(html, /<img|LATEXPLACEHOLDER/);
});
