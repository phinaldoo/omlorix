const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

function readFile(relativePath) {
    return readFrontendSource(path.join(__dirname, relativePath), 'utf8');
}

function getRuleDeclarations(source, selector, requiredDeclaration = '') {
    const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const matches = source.matchAll(new RegExp(`(?:^|\\n)\\s*${escapedSelector}\\s*\\{([^{}]*)\\}`, 'g'));

    for (const match of matches) {
        if (!requiredDeclaration || match[1].includes(requiredDeclaration)) {
            return match[1];
        }
    }

    assert.fail(`Missing ${selector} rule containing ${requiredDeclaration || 'the expected declarations'}`);
}

test('admin dark mode uses its page-specific stepped surface palette', () => {
    const initStyle = readFile('../../css/admin/init.css');
    const sidebarStyle = readFile('../../css/admin/sidebar.css');
    const darkAdminPalette = getRuleDeclarations(initStyle, '[data-mode="dark"]', '--bg-normal:');
    const activeNavigation = getRuleDeclarations(sidebarStyle, '.admin-nav-item.active');

    assert.match(darkAdminPalette, /--bg-normal:\s*#242428/);
    assert.match(darkAdminPalette, /--bg-normal-elevated:\s*#2b2b30/);
    assert.match(darkAdminPalette, /--admin-bg:\s*#18181a/);
    assert.match(darkAdminPalette, /--admin-border:\s*#3f3f46/);
    assert.match(darkAdminPalette, /--admin-selection-bg:\s*#34343b/);
    assert.match(darkAdminPalette, /--admin-table-row-hover:\s*#2e2e34/);
    assert.match(activeNavigation, /background:\s*var\(--admin-selection-bg/);
});

test('mobile admin document keeps one background while its body grows', () => {
    const adminStyle = readFile('../../css/admin/style.css');

    // Safari can expose the root canvas during toolbar resizing and elastic
    // overscroll, so it must match the body rather than the general app canvas.
    assert.match(
        adminStyle,
        /html\s*\{\s*background:\s*var\(--admin-bg\);\s*\}/
    );

    // Mobile switches from the fixed inner scroller to document scrolling.
    // Resetting the shared 100% body height prevents long pages overflowing a
    // one-viewport background box and revealing the root beneath it.
    assert.match(
        adminStyle,
        /@media \(max-width: 768px\) \{\s*body\s*\{[^}]*height:\s*auto;[^}]*overflow-y:\s*auto;/
    );
});
