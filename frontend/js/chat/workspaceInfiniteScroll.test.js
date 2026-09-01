const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

const read = (name) => readFrontendSource(path.join(__dirname, name), 'utf8');

test('notes use server search and scrolling for the sidebar, histories, and file picker', () => {
    const source = read('notes.js');

    assert.match(source, /if \(String\(query \|\| ''\)\.trim\(\)\) params\.set\('q'/);
    assert.match(source, /setupNotesInfiniteScroll\(\)[\s\S]*IntersectionObserver/);
    assert.match(source, /loadMoreNoteHistory\(\)[\s\S]*append: true/);
    assert.match(source, /setupFilePickerInfiniteScroll\(\)[\s\S]*loadMoreFilePickerFiles/);
    assert.doesNotMatch(source, /NotesState\.notes\.filter\(note =>/);
});

test('todos use the authorized search endpoint and scroll every bounded collection', () => {
    const source = read('todos.js');

    assert.match(source, /TodosAPI\.searchTodos\(\{ q: normalizedQuery \}, offset\)/);
    assert.match(source, /setupTodoListsInfiniteScroll\(\)[\s\S]*loadMoreLists/);
    assert.match(source, /setupTodoContentInfiniteScroll\(mode\)[\s\S]*loadMoreSearchResults/);
    assert.match(source, /filters = \{ view: TodosState\.activeView, sort: TodosState\.sortBy \}/);
});

test('chat note mentions request more authorized notes when the menu scrolls', () => {
    const source = read('chatBox.js');

    assert.match(source, /body\.addEventListener\('scroll'[\s\S]*loadMoreMentionNotes/);
    assert.match(source, /params\.set\('q', normalizedQuery\)/);
    assert.match(source, /fetchNotes\(\{ query: mentionMatch\.query, forceRefresh: queryChanged \}\)/);
    assert.match(source, /serverQuery === normalized/);
});
