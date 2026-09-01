const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const INDEX_PATH = path.join(__dirname, '../../index.html');

/**
 * Return the opening tag for a routed main view.
 *
 * Reading only the opening tag keeps this static regression test independent of the
 * large and frequently changing contents inside each view.
 */
function getOpeningTag(markup, id) {
    const escapedId = id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return markup.match(new RegExp(`<[^>]+id=["']${escapedId}["'][^>]*>`))?.[0] || '';
}

test('routed main views are hidden in markup before JavaScript initializes', () => {
    const markup = fs.readFileSync(INDEX_PATH, 'utf8');
    const mainViewIds = [
        'chatsSearchContainer',
        'automationsContainer',
        'projectsContainer',
        'workspaceContainer',
        'chatContainer',
    ];

    for (const id of mainViewIds) {
        const openingTag = getOpeningTag(markup, id);
        assert.ok(openingTag, `${id} must exist in index.html`);
        assert.match(
            openingTag,
            /style=["'][^"']*display:\s*none;?[^"']*["']/,
            `${id} must be hidden until the client-side router selects it`,
        );
    }
});
