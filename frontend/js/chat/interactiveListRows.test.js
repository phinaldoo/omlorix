const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

function read(relativePath) {
    return fs.readFileSync(path.join(__dirname, relativePath), 'utf8');
}

function returnedTemplateAfter(source, marker) {
    const markerIndex = source.indexOf(marker);
    assert.notEqual(markerIndex, -1, `missing source marker: ${marker}`);
    const start = source.indexOf('return `', markerIndex);
    const end = source.indexOf('`;', start);
    assert.ok(start >= 0 && end > start, `missing template after: ${marker}`);
    return source.slice(start, end);
}

function rowTemplateAfter(source, marker) {
    const markerIndex = source.indexOf(marker);
    assert.notEqual(markerIndex, -1, `missing source marker: ${marker}`);
    const start = source.indexOf('row.innerHTML = `', markerIndex);
    const end = source.indexOf('`;', start);
    assert.ok(start >= 0 && end > start, `missing row template after: ${marker}`);
    return source.slice(start, end);
}

function assignedTemplateAfter(source, marker) {
    const start = source.indexOf(marker);
    assert.notEqual(start, -1, `missing source marker: ${marker}`);
    const end = source.indexOf('`;', start + marker.length);
    assert.ok(end > start, `missing assigned template after: ${marker}`);
    return source.slice(start, end);
}

function assertSiblingButtons(template, rowClass, primaryClass, menuClass) {
    const rowStart = template.indexOf(`<div class="${rowClass}`);
    const rowEnd = template.indexOf('>', rowStart);
    assert.ok(rowStart >= 0 && rowEnd > rowStart, `missing ${rowClass} wrapper`);
    const openingTag = template.slice(rowStart, rowEnd + 1);
    assert.doesNotMatch(openingTag, /\b(?:role|tabindex|aria-pressed)=/);

    const primaryStart = template.indexOf(`<button type="button" class="${primaryClass}`, rowEnd);
    const primaryEnd = template.indexOf('</button>', primaryStart);
    const menuStart = template.indexOf(`<button type="button" class="${menuClass}`, rowEnd);
    assert.ok(primaryStart > rowEnd, `missing ${primaryClass}`);
    assert.ok(primaryEnd > primaryStart, `unclosed ${primaryClass}`);
    assert.ok(menuStart > primaryEnd, `${menuClass} must be a sibling of ${primaryClass}`);
}

test('Notes rows render sibling primary and options buttons without delegated keyboard leakage', () => {
    const renderSource = read('notes/render.js');
    const lifecycleSource = read('notes/manager-lifecycle.js');
    const managerSource = read('notes/manager.js');

    assertSiblingButtons(
        returnedTemplateAfter(renderSource, 'noteItem(note, isActive)'),
        'notes-list-item',
        'notes-list-item-select-btn',
        'notes-list-item-menu-btn',
    );
    assertSiblingButtons(
        returnedTemplateAfter(lifecycleSource, 'renderSearchResultItem(note, query)'),
        'notes-list-item',
        'notes-list-item-select-btn',
        'notes-list-item-menu-btn',
    );
    assert.match(managerSource, /closest\('\.notes-list-item-select-btn'\)/);
    assert.doesNotMatch(managerSource, /sidebarList\.addEventListener\('keydown'/);
});

test('Todo rows render sibling primary and options buttons and use native keyboard activation', () => {
    const source = read('todos.js');

    assertSiblingButtons(
        returnedTemplateAfter(source, 'listItem(list, isActive)'),
        'todos-list-item',
        'todos-list-item-select-btn',
        'todos-list-item-menu-btn',
    );
    const markedTemplate = returnedTemplateAfter(source, 'markedListItem(count)');
    const markedOpeningTag = markedTemplate.match(/<div class="todos-list-item[^>]+>/)?.[0] || '';
    assert.ok(markedOpeningTag);
    assert.doesNotMatch(markedOpeningTag, /\b(?:role|tabindex|aria-pressed)=/);
    assert.match(markedTemplate, /<button type="button" class="todos-list-item-select-btn"/);
    assert.match(source, /closest\('\.todos-list-item-select-btn'\)/);
    assert.doesNotMatch(source, /sidebarList\.addEventListener\('keydown'/);
});

test('normal and project chat rows keep the menu button outside the navigation link', () => {
    const cases = [
        [read('chatsHelper.js'), 'function createChatRow(chat)'],
        [read('projectsChat.js'), 'function createProjectChatRowElement(chat, isActive)'],
    ];

    cases.forEach(([source, marker]) => {
        const template = rowTemplateAfter(source, marker);
        const anchorEnd = template.indexOf('</a>');
        const triggerStart = template.indexOf('<button type="button" class="sidebar-element-menu-trigger"');
        assert.ok(anchorEnd >= 0 && triggerStart > anchorEnd, `${marker} nests its menu trigger in the link`);
        assert.doesNotMatch(template, /<span class="sidebar-element-menu-trigger"|role="button"|tabindex="0"/);

        const functionStart = source.indexOf(marker);
        const functionEnd = source.indexOf('return row;', functionStart);
        assert.ok(functionEnd > functionStart, `missing function end after: ${marker}`);
        const functionSource = source.slice(functionStart, functionEnd);
        assert.doesNotMatch(functionSource, /trigger\??\.addEventListener\('keydown'/);
    });
});

test('project memory cards are native checkbox labels with one tab stop', () => {
    const formsSource = read('createEditForms.js');
    const projectsSource = read('projects.js');
    const cardTemplate = assignedTemplateAfter(formsSource, 'const memoryCard = `');

    assert.match(cardTemplate, /<label class="memories-card projects-memory-toggle-card"[^>]+for="\$\{config\.memoryToggleId\}"/);
    assert.match(cardTemplate, /<input type="checkbox" id="\$\{config\.memoryToggleId\}"[^>]+aria-labelledby="\$\{config\.memoryLabelId\}"/);
    assert.doesNotMatch(cardTemplate, /role="button"|tabindex="0"|translated\('label'/);
    assert.doesNotMatch(projectsSource, /bindProjectMemoryToggleCard|projectMemoryToggleBound/);
});
