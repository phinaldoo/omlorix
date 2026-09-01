const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadConflictManager() {
    const source = readFrontendSource(path.join(__dirname, 'notesConflict.js'), 'utf8');
    const context = {
        window: {},
        console,
        Map,
        Promise,
        Uint32Array,
        setTimeout,
        clearTimeout,
    };
    vm.runInNewContext(source, context, { filename: 'notesConflict.js' });
    return context.window.NotesConflictManager;
}

test('three-way merge reapplies independent local and server edits', () => {
    const manager = loadConflictManager();
    const result = manager.threeWayMerge(
        'Heading\nOwner text\nLocal text\nFooter',
        'Heading\nOwner text\nMy revised text\nFooter',
        'Heading\nServer text\nLocal text\nFooter',
    );

    assert.equal(result.clean, true);
    assert.equal(result.conflicts, 0);
    assert.equal(result.content, 'Heading\nServer text\nMy revised text\nFooter');
});

test('three-way merge refuses to guess when edits overlap', () => {
    const manager = loadConflictManager();
    const result = manager.threeWayMerge(
        'Heading\nShared line\nFooter',
        'Heading\nMy version\nFooter',
        'Heading\nTheir version\nFooter',
    );

    assert.equal(result.clean, false);
    assert.ok(result.conflicts > 0);
    assert.equal(result.content, 'Heading\nMy version\nFooter');
});

test('identical concurrent edits merge without duplication', () => {
    const manager = loadConflictManager();
    const result = manager.threeWayMerge('A\nB', 'A\nC', 'A\nC');

    assert.deepEqual(
        { clean: result.clean, content: result.content, conflicts: result.conflicts },
        { clean: true, content: 'A\nC', conflicts: 0 },
    );
});

test('workspace integration blocks navigation after a failed save', () => {
    const source = readFrontendSource(path.join(__dirname, 'notes.js'), 'utf8');
    const selectStart = source.indexOf('    async selectNote(noteId)');
    const selectEnd = source.indexOf('    updateReadOnlyIndicator(', selectStart);
    const createStart = source.indexOf('    async createNewNote()');
    const createEnd = source.indexOf('    handleEditorInput(', createStart);

    assert.match(
        source.slice(selectStart, selectEnd),
        /const saved = await this\.ensureCurrentNoteSaved\(\);[\s\S]*if \(!saved\) \{[\s\S]*this\.startAutoRefresh\(NotesState\.selectedNoteId\);[\s\S]*return false;/,
    );
    assert.match(source.slice(createStart, createEnd), /const saved = await this\.ensureCurrentNoteSaved\(\);[\s\S]*if \(!saved\) return false;/);
    assert.match(source, /mobileBackBtn\.addEventListener\('click', async[\s\S]*if \(!await this\.ensureCurrentNoteSaved\(\)\) return;/);
});

test('shared-note polling checks revisions while preserving an active editor', () => {
    const source = readFrontendSource(path.join(__dirname, 'notes.js'), 'utf8');
    const start = source.indexOf('    async refreshSharedNoteContent(noteId)');
    const end = source.indexOf('    async refreshSidebarNote(', start);
    const method = source.slice(start, end);

    assert.match(method, /const contentData = await NotesAPI\.fetchNoteContent\(noteId\)/);
    assert.match(method, /if \(this\.isUserCurrentlyEditing\(\)/);
    assert.match(method, /this\.showRemoteUpdateBanner\(contentData/);
    assert.doesNotMatch(method, /if \(this\.isUserCurrentlyEditing\(\)\) \{\s*return;/);
});
