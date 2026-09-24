// Run with Playwright installed: node docs/pr-evidence/markdown-latex-tables/verify.cjs
// Optional: --baseline <git-ref> captures the old parser for comparison.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../../..');
const baseline = process.argv[2] === '--baseline' ? process.argv[3] : null;
const parserPath = 'frontend/js/chat/rendering/markdown-parser.js';
const source = fs.readFileSync(path.join(root, 'frontend/js/chat/rendering/fixtures/exponential-limits.md'), 'utf8');
const scripts = [
    'vendor/markdown/markdown-it.min.js', 'vendor/markdown/markdown-it-sup.min.js',
    'vendor/markdown/markdown-it-sub.min.js', 'vendor/purify.min.js',
    'vendor/katex/katex.min.js', 'vendor/katex/auto-render.min.js',
    'common/icons.js', 'chat/chatSanitizer.js', 'chat/rendering/state.js',
    'chat/rendering/shared-utils.js', 'chat/rendering/markdown-parser.js',
];
const pageHtml = `<!doctype html><html lang="de" data-mode="light"><meta charset="utf-8">
<link rel="stylesheet" href="/css/common/init.css">
<link rel="stylesheet" href="/css/chat/markdown.css">
<link rel="stylesheet" href="/css/katex/katex.min.css">
<style>body { margin: 0; padding: 32px; background: var(--background); }
main { max-width: 1040px; margin: auto; }</style>
<main class="markdown-body"></main>
${scripts.map(src => `<script src="/js/${src}"></script>`).join('\n')}
<script>function getChatPreviewTranslation(key, fallback) { return fallback; }</script></html>`;
const mime = { '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf' };
const server = http.createServer((req, res) => {
    if (req.url === '/') { res.setHeader('Content-Type', 'text/html'); res.end(pageHtml); return; }
    const file = path.join(root, 'frontend', decodeURIComponent(req.url.split('?')[0]));
    if (!file.startsWith(path.join(root, 'frontend') + path.sep)) { res.writeHead(403).end(); return; }
    try {
        const data = baseline && file === path.join(root, parserPath)
            ? execFileSync('git', ['show', `${baseline}:${parserPath}`], { cwd: root })
            : fs.readFileSync(file);
        res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream');
        res.end(data);
    } catch { res.writeHead(404).end(); }
});
(async () => {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const browser = await chromium.launch({ channel: 'chrome', headless: true });
    try {
        const page = await browser.newPage({ viewport: { width: 1120, height: 490 }, deviceScaleFactor: 2 });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(`http://127.0.0.1:${server.address().port}/`);
        const render = async markdown => page.evaluate(text => {
            const element = document.querySelector('main');
            element.innerHTML = window.ChatSanitizer.sanitizeHtml(getMarkdownRenderer().render(text));
            wrapImplicitMathSegments(element);
            renderMathWithRetry(element, 0);
        }, markdown);
        await render(source);
        await page.evaluate(() => document.fonts.ready);
        const formulas = await page.locator('table annotation[encoding="application/x-tex"]').allTextContents();
        if (!baseline) {
            assert.deepEqual(formulas, [String.raw`x\to\infty`, String.raw`x\to-\infty`, 'a>1', '2^x', String.raw`\infty`, '0', '0<a<1', String.raw`(\tfrac12)^x`, '0', String.raw`\infty`]);
            assert.equal(await page.locator('.katex-error').count(), 0);
            assert.equal(await page.locator('table th').count(), 3);
            assert.equal(await page.locator('table td').count(), 6);
            assert.equal(await page.locator('table .katex-mathml math').count(), 10);
        }
        await page.screenshot({ path: path.join(__dirname, baseline ? 'before.png' : 'after.png'), fullPage: true });
        if (!baseline) {
            await render('| Content |\n|---|\n| **bold** and *italic* with \\(x_1^2\\) |\n| [**link**](https://example.com) and \\*literal\\* |\n| `\\(0<a<1\\)` and `*code*` |');
            assert.equal(await page.locator('td strong').first().textContent(), 'bold');
            assert.equal(await page.locator('td a strong').textContent(), 'link');
            assert.deepEqual(await page.locator('td code').allTextContents(), [String.raw`\(0<a<1\)`, '*code*']);
            assert.equal(await page.locator('td .katex').count(), 1);
            assert.equal(await page.locator('code .katex').count(), 0);
            await render(source);
            assert.deepEqual(await page.locator('table annotation[encoding="application/x-tex"]').allTextContents(), formulas);
        }
        assert.deepEqual(errors, []);
        console.log(JSON.stringify({ baseline, tableFormulas: formulas, browserChecks: 'passed' }, null, 2));
    } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
