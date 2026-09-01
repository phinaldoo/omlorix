const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const frontendRoot = path.join(__dirname, '..', '..');

/** Read a frontend asset used by the static search-control regression tests. */
function readFrontendFile(...segments) {
    return fs.readFileSync(path.join(frontendRoot, ...segments), 'utf8');
}

test('shared styles suppress the native search cancel button across app pages', () => {
    const elementsCss = readFrontendFile('css', 'common', 'elements.css');
    const searchCancelRule = elementsCss.match(
        /input\[type="search"\]::-webkit-search-cancel-button\s*\{([^}]+)\}/u,
    );

    assert.ok(searchCancelRule, 'the shared stylesheet must own the search cancel reset');
    assert.match(searchCancelRule[1], /-webkit-appearance:\s*none/u);
    assert.match(searchCancelRule[1], /appearance:\s*none/u);

    // Both primary browser pages load the shared rule, so dynamically created
    // and static search inputs receive the same browser-control normalization.
    for (const page of ['index.html', 'admin.html']) {
        const html = readFrontendFile(page);
        assert.match(html, /<link rel="stylesheet" href="\/css\/common\/elements\.css">/u);
    }
});

test('the workspace file search keeps its single accessible app clear button', () => {
    const indexHtml = readFrontendFile('index.html');

    assert.match(
        indexHtml,
        /<input type="search" id="filesSearchInput"[^>]*>/u,
    );
    assert.match(
        indexHtml,
        /<button type="button" id="filesSearchClear"[^>]*aria-label="Clear file search"[^>]*>/u,
    );
});
