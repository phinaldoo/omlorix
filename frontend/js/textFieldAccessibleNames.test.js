const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const frontendRoot = path.join(__dirname, '..');
const indexSource = fs.readFileSync(path.join(frontendRoot, 'index.html'), 'utf8');

function startTagById(source, id) {
    const match = source.match(new RegExp(`<[^>]+\\bid="${id}"[^>]*>`, 'i'));
    assert.ok(match, `expected #${id}`);
    return match[0];
}

test('compact authenticated text fields have localized durable accessible names', () => {
    const fields = new Map([
        ['todosSidebarSearchInput', 'todos_search_placeholder'],
        ['todosAddInput', 'todos_add_placeholder'],
        ['todosAddNotes', 'todos_add_notes_placeholder'],
        ['notesSidebarSearchInput', 'notes_search_placeholder'],
        ['chatBoxInput', 'chat_input_placeholder'],
    ]);

    for (const [id, translationKey] of fields) {
        const tag = startTagById(indexSource, id);
        assert.match(tag, /\baria-label="[^"]+"/i, `#${id} needs an explicit accessible name`);
        assert.match(
            tag,
            new RegExp(`data-i18n-attr="[^"]*aria-label:${translationKey}(?:;|"|$)`),
            `#${id} needs a localized accessible name`,
        );
    }
});

test('the location setting is named and described by its visible translated copy', () => {
    const input = startTagById(indexSource, 'userLocation');
    assert.match(input, /\baria-labelledby="userLocationLabel"/);
    assert.match(input, /\baria-describedby="userLocationDescription"/);
    assert.match(startTagById(indexSource, 'userLocationLabel'), /data-i18n="us_general_location_title"/);
    assert.match(startTagById(indexSource, 'userLocationDescription'), /data-i18n="us_general_location_desc"/);
});

test('dynamic Admin voice searches have live localized accessible names', () => {
    for (const relativePath of ['admin/audioGeneration.js', 'admin/models.js']) {
        const source = fs.readFileSync(path.join(__dirname, relativePath), 'utf8');
        assert.match(source, /searchInput\.setAttribute\('aria-label', voiceSearchLabel\)/);
        assert.match(
            source,
            /placeholder:audio_generation_voice_search_placeholder;aria-label:audio_generation_voice_search_placeholder/,
        );
    }
});
