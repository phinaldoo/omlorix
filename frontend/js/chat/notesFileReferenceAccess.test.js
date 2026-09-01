const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');


const NOTES_PATH = path.join(__dirname, 'notes.js');
const NOTES_CSS_PATH = path.join(__dirname, '..', '..', 'css', 'chat', 'notes.css');
const I18N_ROOT = path.join(__dirname, '..', '..', 'i18n');


function loadNotesApi(authedFetch) {
    const source = readFrontendSource(NOTES_PATH, 'utf8');
    const start = source.indexOf('const noteUpdateQueues = new Map();');
    const end = source.indexOf('\n// ============================================================================\n// DOM Helpers', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    return Function('window', 'notesT', `${source.slice(start, end)}\nreturn NotesAPI;`)(
        { authedFetch },
        (_key, fallback) => fallback,
    );
}


function loadReferenceHelpers() {
    const source = readFrontendSource(NOTES_PATH, 'utf8');
    const start = source.indexOf('function isNoteFileReferenceUnavailable(error)');
    const end = source.indexOf('\nconst NotesAPI = {', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    return Function(`${source.slice(start, end)}\nreturn { normalizeNoteFileReferenceIssue, replaceFirstNoteReferenceToken };`)();
}


test('note update preserves the exact structured unavailable-reference failure', async () => {
    const detail = {
        code: 'note_file_reference_unavailable',
        message: 'A newly added file reference is unavailable to you.',
        reference: {
            kind: 'file',
            owner_id: 'owner-1',
            file_id: 'file-private',
            label: 'Owner plan.pdf',
            raw_token: '{{note:file:owner-1:file-private|Owner plan.pdf}}',
            occurrence: 2,
        },
    };
    const NotesAPI = loadNotesApi(async () => ({
        ok: false,
        status: 400,
        json: async () => ({ detail }),
    }));

    await assert.rejects(
        NotesAPI.updateNote('note-1', 'draft', 'revision-1'),
        (error) => {
            assert.equal(error.code, 'note_file_reference_unavailable');
            assert.deepEqual(error.payload.detail.reference, detail.reference);
            return true;
        },
    );
});


test('reference issue helpers normalize and replace only the first exact marker', () => {
    const { normalizeNoteFileReferenceIssue, replaceFirstNoteReferenceToken } = loadReferenceHelpers();
    const marker = '{{note:file:owner-1:file-private|Owner plan.pdf}}';
    const issue = normalizeNoteFileReferenceIssue({
        payload: {
            detail: {
                reference: {
                    kind: 'file',
                    owner_id: 'owner-1',
                    file_id: 'file-private',
                    label: 'Owner plan.pdf',
                    raw_token: marker,
                    occurrence: 2,
                },
            },
        },
    });

    assert.equal(issue.raw_token, marker);
    assert.equal(issue.occurrence, 2);
    assert.equal(
        replaceFirstNoteReferenceToken(`${marker}\n${marker}`, marker, '{{note:file:user-2:file-new|Replacement}}'),
        `{{note:file:user-2:file-new|Replacement}}\n${marker}`,
    );
    assert.equal(replaceFirstNoteReferenceToken('plain text', marker), null);
});


test('Notes UI exposes exact-reference owner request, replace, and remove actions', () => {
    const source = readFrontendSource(NOTES_PATH, 'utf8');
    const css = readFrontendSource(NOTES_CSS_PATH, 'utf8');

    assert.match(source, /data-reference-action="owner-request"/);
    assert.match(source, /data-reference-action="replace"/);
    assert.match(source, /data-reference-action="remove"/);
    assert.match(source, /this\.openFilePicker\('replace'\)/);
    assert.match(source, /replaceUnavailableReferenceWithFile/);
    assert.match(source, /removeUnavailableReference/);
    assert.match(css, /\.notes-reference-issue/);
});


test('all supported locales include the unavailable-reference action copy', () => {
    const keys = [
        'notes_reference_unavailable_title',
        'notes_reference_unavailable_message',
        'notes_reference_exact_marker',
        'notes_reference_copy_owner_request',
        'notes_reference_replace_action',
        'notes_reference_remove_action',
        'notes_reference_owner_request_text',
        'notes_reference_replace_title',
        'notes_reference_replace_selected',
    ];
    const locales = fs.readdirSync(I18N_ROOT).filter((locale) => (
        fs.existsSync(path.join(I18N_ROOT, locale, 'index.json'))
    ));

    for (const locale of locales) {
        const messages = JSON.parse(readFrontendSource(path.join(I18N_ROOT, locale, 'index.json'), 'utf8'));
        for (const key of keys) {
            assert.equal(typeof messages[key], 'string', `${locale} is missing ${key}`);
            assert.ok(messages[key].trim(), `${locale} has an empty ${key}`);
        }
    }
});
