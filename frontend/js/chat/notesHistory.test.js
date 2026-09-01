const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

const notesSource = readFrontendSource(path.join(__dirname, 'notes.js'), 'utf8');
const i18nRoot = path.join(__dirname, '..', '..', 'i18n');

/** Return one Notes module method for focused request-ordering assertions. */
function notesMethod(startMarker, endMarker) {
    const start = notesSource.indexOf(startMarker);
    const end = notesSource.indexOf(endMarker, start);

    assert.notEqual(start, -1, `${startMarker} should exist`);
    assert.notEqual(end, -1, `${endMarker} should follow ${startMarker}`);
    return notesSource.slice(start, end);
}

function loadNotesPlural(locale, translations) {
    const start = notesSource.indexOf('function notesT(key, fallback)');
    const end = notesSource.indexOf('/** Build the history diff', start);

    assert.notEqual(start, -1, 'notes translation helpers should exist');
    assert.notEqual(end, -1, 'notes plural helper should precede the history diff formatter');
    return Function(
        'window',
        'document',
        `${notesSource.slice(start, end)}\nreturn notesPluralT;`,
    )(
        {
            getTranslation: (key, fallback) => translations[key] ?? fallback,
            formatTranslation: (key, fallback, vars = {}) => (
                String(translations[key] ?? fallback).replace(
                    /\{(\w+)\}/g,
                    (_match, token) => String(vars[token] ?? ''),
                )
            ),
        },
        { documentElement: { lang: locale } },
    );
}

test('note history version count follows the active locale and its plural rules', () => {
    const method = notesMethod(
        'async loadNoteHistory(noteId, { append = false } = {})',
        '/** Render the version list',
    );
    assert.match(method, /notesPluralT\(\s*'notes_history_version_count'/);

    const locales = fs.readdirSync(i18nRoot).filter((locale) => (
        fs.existsSync(path.join(i18nRoot, locale, 'index.json'))
    ));
    for (const locale of locales) {
        const translations = JSON.parse(
            fs.readFileSync(path.join(i18nRoot, locale, 'index.json'), 'utf8'),
        );
        const categories = new Intl.PluralRules(locale).resolvedOptions().pluralCategories;
        for (const category of categories) {
            const key = `notes_history_version_count_${category}`;
            assert.ok(translations[key]?.trim(), `${locale} is missing ${key}`);
        }
    }

    const german = JSON.parse(fs.readFileSync(path.join(i18nRoot, 'de', 'index.json'), 'utf8'));
    const translateGermanCount = loadNotesPlural('de', german);
    assert.equal(
        translateGermanCount('notes_history_version_count', 1, '{count} version', '{count} versions'),
        '1 Version',
    );
    assert.equal(
        translateGermanCount('notes_history_version_count', 2, '{count} version', '{count} versions'),
        '2 Versionen',
    );
});

test('note history list ignores responses from an older note request', () => {
    const method = notesMethod(
        'async loadNoteHistory(noteId, { append = false } = {})',
        '/** Render the version list',
    );
    const responseGuard = method.indexOf('if (!isCurrentRequest()) return;');
    const stateUpdate = method.indexOf('NotesState.historyEntries = append');

    assert.match(method, /const requestToken = append \? NotesState\.historyRequestToken : Symbol\('note-history'\)/);
    assert.match(method, /NotesState\.historyNoteId\) === String\(noteId\)/);
    assert.match(method, /NotesState\.historyPanelOpen/);
    assert.ok(responseGuard >= 0, 'the history response should be guarded');
    assert.ok(responseGuard < stateUpdate, 'the stale-response guard should precede state updates');
    assert.match(method, /NotesAPI\.getNoteHistory\(noteId, NOTES_HISTORY_PAGE_LIMIT, offset\)/);
    assert.match(method, /NotesState\.historyHasMore = Boolean\(data\.has_more\)/);
});

test('note history detail ignores responses after the note or selection changes', () => {
    const method = notesMethod(
        'async selectHistoryEntry(historyId)',
        '/**\n     * Render the diff',
    );
    const responseGuard = method.indexOf('if (!isCurrentRequest()) return;');
    const previewUpdate = method.indexOf('NotesState.historyPreviewContent = entry;');

    assert.match(method, /const noteId = NotesState\.historyNoteId;/);
    assert.match(method, /const requestToken = Symbol\('note-history-entry'\)/);
    assert.match(method, /NotesAPI\.getHistoryEntry\(noteId, historyId\)/);
    assert.match(method, /NotesState\.selectedHistoryId\) === id/);
    assert.ok(responseGuard >= 0, 'the entry response should be guarded');
    assert.ok(responseGuard < previewUpdate, 'the stale-response guard should precede preview updates');
});

test('closing note history invalidates pending list and detail requests', () => {
    const method = notesMethod(
        '    hideHistoryPanel() {',
        '/**\n     * Toggle the right pane',
    );

    assert.match(method, /NotesState\.historyNoteId = null;/);
    assert.match(method, /NotesState\.historyRequestToken = null;/);
    assert.match(method, /NotesState\.historyEntryRequestToken = null;/);
});

test('reopening note history cancels its pending transition hide', () => {
    const showMethod = notesMethod(
        '    async showHistoryPanel(noteId = null) {',
        '    /**\n     * Build the version history modal DOM',
    );
    const hideMethod = notesMethod(
        '    hideHistoryPanel() {',
        '/**\n     * Toggle the right pane',
    );

    assert.match(showMethod, /clearTimeout\(this\._historyHideTimer\);\s*this\._historyHideTimer = null;/);
    assert.match(
        hideMethod,
        /this\._historyHideTimer = setTimeout\(\(\) => \{\s*panel\.setAttribute\('hidden', ''\);\s*this\._historyHideTimer = null;/,
    );
});

test('note history exposes manual and scroll-driven paging through every batch', () => {
    const renderMethod = notesMethod(
        '    renderHistoryList(entries) {',
        '    /** Build up-to-two-letter initials',
    );

    assert.match(renderMethod, /notes_history_load_more/);
    assert.match(renderMethod, /id="notesHistoryLoadMoreBtn"/);
    assert.match(renderMethod, /this\.loadMoreNoteHistory\(\)/);
    assert.match(renderMethod, /new IntersectionObserver/);
    assert.match(renderMethod, /root: list/);
});
