const assert = require('node:assert/strict');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

const FRONTEND_ROOT = path.join(__dirname, '..', '..');
const SHARED_STYLESHEET = '/css/common/searchModal.css';

function readSource(relativePath) {
    return readFrontendSource(path.join(FRONTEND_ROOT, relativePath), 'utf8');
}

function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function attributeValue(tag, name) {
    const attribute = escapeRegExp(name);
    const match = tag.match(new RegExp(`\\b${attribute}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`, 'i'));
    return match ? (match[1] ?? match[2]) : null;
}

function openingTags(source) {
    return source.match(/<[a-z][^>]*>/giu) || [];
}

function tagsWithClass(source, className) {
    return openingTags(source).filter((tag) => {
        const classes = attributeValue(tag, 'class');
        return classes?.split(/\s+/u).includes(className);
    });
}

function stylesheetHrefs(source) {
    return openingTags(source)
        .filter((tag) => tag.startsWith('<link'))
        .filter((tag) => attributeValue(tag, 'rel')?.split(/\s+/u).includes('stylesheet'))
        .map((tag) => attributeValue(tag, 'href'));
}

function ruleBody(source, selectorFragment) {
    const selectorIndex = source.indexOf(selectorFragment);
    assert.notEqual(selectorIndex, -1, `Missing selector ${selectorFragment}`);

    const blockStart = source.indexOf('{', selectorIndex);
    const blockEnd = source.indexOf('}', blockStart);
    assert.notEqual(blockStart, -1, `${selectorFragment} has no declaration block`);
    assert.notEqual(blockEnd, -1, `${selectorFragment} has an unterminated declaration block`);
    return source.slice(blockStart + 1, blockEnd);
}

test('the shared stylesheet is the single source of modal frame styling', () => {
    const sharedStyles = readSource('css/common/searchModal.css');
    const warningStyles = readSource('css/common/warning.css');

    const overlayRule = ruleBody(sharedStyles, '.search-modal-overlay,');
    assert.match(overlayRule, /position:\s*fixed/u);
    assert.match(overlayRule, /inset:\s*0/u);
    assert.match(overlayRule, /background:\s*color-mix/u);
    assert.match(overlayRule, /backdrop-filter:\s*var\(--modal-overlay-backdrop-filter\)/u);
    assert.match(overlayRule, /-webkit-backdrop-filter:\s*var\(--modal-overlay-backdrop-filter\)/u);

    const surfaceRule = ruleBody(sharedStyles, '.search-modal,\n.shared-modal {');
    assert.match(surfaceRule, /width:\s*min\(var\(--shared-modal-width, 560px\), 100%\)/u);
    assert.match(surfaceRule, /border-radius:\s*var\(--modal-border-radius, 16px\)/u);
    assert.match(surfaceRule, /background:\s*color-mix/u);
    assert.match(surfaceRule, /backdrop-filter:\s*blur\(40px\) saturate\(1\.35\)/u);

    assert.match(sharedStyles, /\.search-modal-header,\s*\.shared-modal-header\s*\{/u);
    assert.match(sharedStyles, /\.search-modal-body,\s*\.shared-modal-body\s*\{/u);
    assert.match(sharedStyles, /\.search-modal-footer,\s*\.shared-modal-footer\s*\{/u);
    assert.match(sharedStyles, /\.shared-modal-close\s*\{/u);
    assert.match(sharedStyles, /@media \(prefers-reduced-motion: reduce\)/u);

    // warning.css still supplies warning-content tokens and a legacy fallback,
    // but shared consumers must get their backdrop and surface from the shared
    // stylesheet rather than duplicating that frame in the warning system.
    const warningTokenRule = ruleBody(warningStyles, '.warning-overlay,');
    assert.doesNotMatch(
        warningTokenRule,
        /(?:^|\n)\s*(?:position|inset|display|padding|background|backdrop-filter|-webkit-backdrop-filter|box-shadow|border-radius)\s*:/u,
    );
    assert.match(warningStyles, /\.warning-overlay:not\(\.shared-modal-overlay\)/u);
    assert.match(warningStyles, /\.warning-card:not\(\.shared-modal\)/u);
});

test('admin, login, and chat pages load the shared shell last and expose accessible dialogs', () => {
    ['admin.html', 'login.html', 'index.html'].forEach((relativePath) => {
        const source = readSource(relativePath);
        const stylesheets = stylesheetHrefs(source);
        const sharedStylesheetIndexes = stylesheets
            .map((href, index) => href === SHARED_STYLESHEET ? index : -1)
            .filter((index) => index >= 0);

        assert.deepEqual(
            sharedStylesheetIndexes,
            [stylesheets.length - 1],
            `${relativePath} must load ${SHARED_STYLESHEET} once, after feature styles`,
        );

        const overlays = tagsWithClass(source, 'shared-modal-overlay');
        const dialogs = tagsWithClass(source, 'shared-modal');
        assert.ok(overlays.length > 0, `${relativePath} has no shared modal overlays`);
        assert.equal(
            dialogs.length,
            overlays.length,
            `${relativePath} must pair each shared overlay with one shared dialog surface`,
        );

        overlays.forEach((overlay) => {
            assert.ok(attributeValue(overlay, 'id'), `${relativePath} has a shared overlay without an id`);
            assert.equal(attributeValue(overlay, 'aria-hidden'), 'true');
            assert.notEqual(attributeValue(overlay, 'role'), 'dialog');
        });

        dialogs.forEach((dialog) => {
            assert.equal(attributeValue(dialog, 'role'), 'dialog');
            assert.equal(attributeValue(dialog, 'aria-modal'), 'true');
            const labelledBy = attributeValue(dialog, 'aria-labelledby');
            assert.ok(labelledBy, `${relativePath} has an unlabelled shared dialog`);
            labelledBy.split(/\s+/u).forEach((labelId) => {
                assert.match(
                    source,
                    new RegExp(`\\bid=["']${escapeRegExp(labelId)}["']`, 'u'),
                    `${relativePath} is missing dialog label #${labelId}`,
                );
            });
        });
    });
});
