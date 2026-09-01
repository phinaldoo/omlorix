const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const FRONTEND_DIR = path.join(__dirname, '..', '..');

/**
 * Return the notes editor header markup for small, focused structural checks.
 */
function notesHeaderMarkup() {
    const index = fs.readFileSync(path.join(FRONTEND_DIR, 'index.html'), 'utf8');
    const start = index.indexOf('<div class="notes-editor-header">');
    const end = index.indexOf('<div class="notes-editor-shell">', start);

    assert.notEqual(start, -1, 'notes editor header should exist');
    assert.notEqual(end, -1, 'notes editor shell should follow the header');
    return index.slice(start, end);
}

test('notes header keeps status left and orders right-side actions as view, history, download', () => {
    const header = notesHeaderMarkup();
    const leadingStart = header.indexOf('id="notesEditorHeaderLeading"');
    const status = header.indexOf('id="notesSaveStatus"');
    const actionsStart = header.indexOf('class="notes-editor-header-actions"');
    const viewSwitch = header.indexOf('id="notesMarkdownEditorControls"');
    const history = header.indexOf('id="notesHistoryBtn"');
    const download = header.indexOf('id="notesDownloadBtn"');

    assert.ok(leadingStart < status, 'save status should be inside the leading group');
    assert.ok(status < actionsStart, 'save status should precede the right-side actions');
    assert.ok(actionsStart < viewSwitch, 'view switch should be inside the action group');
    assert.ok(viewSwitch < history, 'history should follow the view switch');
    assert.ok(history < download, 'download should be the final header action');
});

test('notes save status remains an accessible live status region', () => {
    assert.match(
        notesHeaderMarkup(),
        /id="notesSaveStatus" role="status" aria-live="polite"/,
    );
});
