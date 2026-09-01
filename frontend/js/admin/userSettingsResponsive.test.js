const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const userSettingsStyles = fs.readFileSync(
    path.join(__dirname, '../../css/admin/userSettings.css'),
    'utf8'
);

test('stacked admin user and group settings remain scrollable above the mobile breakpoint', () => {
    // The global admin layout switches from a viewport-locked scroll region to
    // normal document scrolling at 768px. The stacked settings form begins at
    // 900px, so its flex body must be shrinkable throughout the range between
    // those breakpoints for .user-settings-content overflow scrolling to work.
    const viewportLockedTabletRule = userSettingsStyles.match(
        /@media\s*\(min-width:\s*769px\)\s*and\s*\(max-width:\s*900px\)\s*\{\s*\.user-settings-body\s*\{([^}]*)\}/
    );

    assert.ok(viewportLockedTabletRule, 'missing the 769px-900px settings layout rule');
    assert.match(viewportLockedTabletRule[1], /min-height:\s*0\s*;/);
});

test('stacked settings use one horizontally scrollable section bar', () => {
    // Group schemas can contain more than twenty sections. Wrapping full-width
    // section buttons creates a tall navigation stack that leaves no usable
    // height for the actual edit fields at tablet widths.
    const stackedLayoutRule = userSettingsStyles.match(
        /@media\s*\(max-width:\s*900px\)\s*\{([\s\S]*?)\n\}/
    );

    assert.ok(stackedLayoutRule, 'missing the stacked settings layout rule');
    assert.match(
        stackedLayoutRule[1],
        /\.user-settings-sidebar\s*\{[\s\S]*?flex-wrap:\s*nowrap\s*;[\s\S]*?overflow-x:\s*auto\s*;[\s\S]*?overflow-y:\s*hidden\s*;/
    );
    assert.match(
        stackedLayoutRule[1],
        /\.user-settings-nav-item\s*\{[\s\S]*?flex:\s*0\s+0\s+auto\s*;[\s\S]*?width:\s*auto\s*;/
    );
});
