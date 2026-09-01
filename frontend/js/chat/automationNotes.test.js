const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const AUTOMATIONS_PATH = path.join(__dirname, 'automations.js');
const AUTOMATIONS_SOURCE = fs.readFileSync(AUTOMATIONS_PATH, 'utf8');

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName}`);
    const bodyStart = source.indexOf('{', start);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
    }
    throw new Error(`Could not extract ${functionName}`);
}

function loadNotesRenderer(notes, selectedNoteIds) {
    const container = {
        innerHTML: '',
        querySelector: () => null,
        querySelectorAll: () => [],
    };
    const context = {
        automationNotesSelect: container,
        automationEditNotesSelect: null,
        automationsNotesCache: notes,
        AutomationState: {
            create: { selectedNoteIds },
            edit: { selectedNoteIds: [] },
        },
        AutomationUtils: {
            escapeHtml(value) {
                return String(value)
                    .replaceAll('&', '&amp;')
                    .replaceAll('<', '&lt;')
                    .replaceAll('>', '&gt;')
                    .replaceAll('"', '&quot;')
                    .replaceAll("'", '&#39;');
            },
        },
        Icons: {
            notes_management: '<svg data-icon="note"></svg>',
            close: '<svg data-icon="close"></svg>',
            plus: '<svg data-icon="plus"></svg>',
            check: '<svg data-icon="check"></svg>',
        },
        automationT: (_key, fallback) => fallback,
        ensureAutomationNotesSelectOutsideHandler() {},
    };

    vm.runInNewContext(
        [
            extractFunction(AUTOMATIONS_SOURCE, 'getAutomationNoteLabel'),
            extractFunction(AUTOMATIONS_SOURCE, 'renderAutomationNotesSelect'),
            "renderAutomationNotesSelect('create');",
        ].join('\n\n'),
        context,
        { filename: AUTOMATIONS_PATH },
    );
    return container.innerHTML;
}

test('automation note picker and chips render the lightweight owned and subscribed note contract', () => {
    const longUnicodeTitle = '研究計画 🚀 Пример طويل للغاية لاختبار العناوين';
    const notes = [
        {
            id: 'owned-note',
            title: 'E2E Markdown',
            snippet: 'First list item and a second paragraph',
            is_subscribed: false,
        },
        {
            id: 'subscribed-note',
            title: longUnicodeTitle,
            snippet: 'Shared by another user',
            is_subscribed: true,
            share_type: 'live',
        },
        { id: 'duplicate-a', title: 'Duplicate', snippet: 'Alpha' },
        { id: 'duplicate-b', title: 'Duplicate', snippet: 'Beta' },
    ];

    const html = loadNotesRenderer(notes, ['owned-note', 'subscribed-note']);

    assert.match(html, /data-note-id="owned-note"[\s\S]*E2E Markdown/);
    assert.match(html, /data-note-id="subscribed-note"[\s\S]*研究計画 🚀 Пример طويل للغاية لاختبار العناوين/);
    assert.equal((html.match(/E2E Markdown/g) || []).length, 2, 'title should appear in its chip and picker item');
    assert.equal((html.match(new RegExp(longUnicodeTitle, 'g')) || []).length, 2, 'Unicode title should remain complete');
    assert.match(html, /data-note-id="duplicate-a"[\s\S]*Duplicate/);
    assert.match(html, /data-note-id="duplicate-b"[\s\S]*Duplicate/);
    assert.doesNotMatch(html, /Empty note/);
});

test('automation note labels fall back safely without mislabelling populated notes as empty', () => {
    const html = loadNotesRenderer([
        { id: 'snippet-only', title: '  ', snippet: 'Snippet fallback' },
        { id: 'full-note', content: '\n# Full response title\nBody' },
        { id: 'empty', title: '', snippet: '' },
    ], ['snippet-only', 'full-note', 'empty']);

    assert.match(html, /Snippet fallback/);
    assert.match(html, /Full response title/);
    assert.match(html, /Empty note/);
});
