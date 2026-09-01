const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName} in chatBox.js`);

    const bodyStart = source.indexOf('{', start);
    assert.notEqual(bodyStart, -1, `expected ${functionName} body`);

    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') {
            depth += 1;
        } else if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }

    throw new Error(`Could not extract ${functionName}`);
}

function extractNumericConstant(source, constantName) {
    const pattern = new RegExp(`const\\s+${constantName}\\s*=\\s*(\\d+)\\s*;`);
    const match = source.match(pattern);
    assert.ok(match, `expected numeric constant ${constantName} in chatBox.js`);
    return Number(match[1]);
}

function createOverlay() {
    const elements = new Map();

    function createElement() {
        return {
            attributes: {},
            textContent: '',
            setAttribute(name, value) {
                this.attributes[name] = String(value);
            },
        };
    }

    elements.set('.large-paste-modal', createElement());
    elements.set('[data-large-paste-role="title-text"]', createElement());
    elements.set('[data-large-paste-action="cancel"]', createElement());
    elements.set('[data-large-paste-role="info"]', createElement());
    elements.set('[data-large-paste-role="paste-action-text"]', createElement());
    elements.set('[data-large-paste-role="file-action-text"]', createElement());
    elements.set('[data-large-paste-stat="chars"]', createElement());
    elements.set('[data-large-paste-stat="lines"]', createElement());
    elements.set('[data-large-paste-stat="size"]', createElement());

    return {
        elements,
        querySelector(selector) {
            return elements.get(selector) || null;
        },
    };
}

function loadLargePasteHelpers(translations = {}) {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const context = {
        Blob,
        document: {
            documentElement: {
                lang: 'de',
            },
        },
        getChatI18nString: (key, fallback) => translations[key] ?? fallback,
        window: {
            formatTranslation: (key, fallback, vars = {}) => String(translations[key] ?? fallback).replace(/\{(\w+)\}/g, (_, token) => String(vars[token] ?? '')),
        },
        _largePasteOverlay: null,
        _largePasteCurrentText: '',
    };
    context.globalThis = context;

    vm.runInNewContext(
        [
            extractFunction(source, '_getLargePasteTranslation'),
            extractFunction(source, '_formatLargePasteTranslation'),
            extractFunction(source, '_formatLargePasteStatCount'),
            extractFunction(source, '_formatPasteSize'),
            extractFunction(source, '_updateLargePasteStats'),
            extractFunction(source, '_updateLargePasteModalTranslations'),
            `this.helpers = {
                updateLargePasteStats: _updateLargePasteStats,
                updateLargePasteModalTranslations: _updateLargePasteModalTranslations,
            };`,
        ].join('\n\n'),
        context,
        { filename: 'chatBox.js' },
    );

    return {
        context,
        helpers: context.helpers,
    };
}

test('large paste stats use translated labels', () => {
    const { helpers } = loadLargePasteHelpers({
        chat_large_paste_stat_characters: '{count} Zeichen',
        chat_large_paste_stat_lines: '{count} Zeilen',
    });
    const overlay = createOverlay();

    helpers.updateLargePasteStats(overlay, 'A\nB');

    assert.equal(overlay.elements.get('[data-large-paste-stat="chars"]').textContent, '3 Zeichen');
    assert.equal(overlay.elements.get('[data-large-paste-stat="lines"]').textContent, '2 Zeilen');
    assert.equal(overlay.elements.get('[data-large-paste-stat="size"]').textContent, '3 B');
});

test('large paste modal translations refresh existing overlay copy', () => {
    const { context, helpers } = loadLargePasteHelpers({
        chat_large_paste_title: 'Langer Text eingefügt',
        chat_large_paste_close: 'Abbrechen',
        chat_large_paste_info: 'Langer Hinweistext.',
        chat_large_paste_action_paste: 'Als Text einfügen',
        chat_large_paste_action_file: 'Als Datei anhängen',
        chat_large_paste_stat_characters: '{count} Zeichen',
        chat_large_paste_stat_lines: '{count} Zeilen',
    });
    const overlay = createOverlay();
    context._largePasteOverlay = overlay;
    context._largePasteCurrentText = 'Hallo';

    helpers.updateLargePasteModalTranslations();

    assert.equal(overlay.elements.get('.large-paste-modal').attributes['aria-label'], 'Langer Text eingefügt');
    assert.equal(overlay.elements.get('[data-large-paste-role="title-text"]').textContent, 'Langer Text eingefügt');
    assert.equal(overlay.elements.get('[data-large-paste-action="cancel"]').attributes['aria-label'], 'Abbrechen');
    assert.equal(overlay.elements.get('[data-large-paste-role="info"]').textContent, 'Langer Hinweistext.');
    assert.equal(overlay.elements.get('[data-large-paste-role="paste-action-text"]').textContent, 'Als Text einfügen');
    assert.equal(overlay.elements.get('[data-large-paste-role="file-action-text"]').textContent, 'Als Datei anhängen');
    assert.equal(overlay.elements.get('[data-large-paste-stat="chars"]').textContent, '5 Zeichen');
});

test('large paste text insertion defers expensive input work', () => {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const largeText = 'x'.repeat(1600);
    let execCommandCalled = false;
    let deferredWorkScheduled = false;
    let deferredFlagDuringInput = false;

    const context = {
        LARGE_PASTE_CHAR_THRESHOLD: 1500,
        deferNextChatInputExpensiveInputWork: false,
        scheduleDeferredChatInputWork(reason) {
            deferredWorkScheduled = reason === 'large-paste';
        },
        document: {
            execCommand() {
                execCommandCalled = true;
                return true;
            },
        },
        Event: class {
            constructor(type, options = {}) {
                this.type = type;
                this.bubbles = Boolean(options.bubbles);
            }
        },
        chatInput: {
            value: 'hello world',
            selectionStart: 6,
            selectionEnd: 11,
            focus() {},
            setRangeText(text, start, end, selectionMode) {
                this.value = this.value.slice(0, start) + text + this.value.slice(end);
                if (selectionMode === 'end') {
                    this.selectionStart = start + text.length;
                    this.selectionEnd = start + text.length;
                }
            },
            dispatchEvent(event) {
                assert.equal(event.type, 'input');
                assert.equal(event.bubbles, true);
                deferredFlagDuringInput = context.deferNextChatInputExpensiveInputWork;
            },
        },
    };
    context.globalThis = context;

    vm.runInNewContext(
        [
            extractFunction(source, '_insertTextIntoChatInput'),
            `_insertTextIntoChatInput(${JSON.stringify(largeText)});`,
        ].join('\n\n'),
        context,
        { filename: 'chatBox.js' },
    );

    assert.equal(execCommandCalled, false);
    assert.equal(context.chatInput.value, `hello ${largeText}`);
    assert.equal(context.chatInput.selectionStart, 6 + largeText.length);
    assert.equal(context.chatInput.selectionEnd, 6 + largeText.length);
    assert.equal(deferredFlagDuringInput, true);
    assert.equal(context.deferNextChatInputExpensiveInputWork, false);
    assert.equal(deferredWorkScheduled, true);
});

test('deferred large paste work persists through timeout when requestAnimationFrame is paused', () => {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const fallbackDelay = extractNumericConstant(source, 'DEFERRED_CHAT_INPUT_WORK_FALLBACK_MS');
    const timers = [];
    const calls = [];
    const context = {
        DEFERRED_CHAT_INPUT_WORK_FALLBACK_MS: fallbackDelay,
        deferredChatInputWorkScheduled: false,
        chatInput: {
            value: 'large pasted text',
            scrollHeight: 42,
            scrollTop: 0,
        },
        adjustHeight(reason) {
            calls.push(['adjust', reason]);
        },
        writeChatInputDraft(value) {
            calls.push(['draft', value]);
        },
        handleSkillMentionInput() {
            calls.push(['mention']);
        },
        requestAnimationFrame() {
            // Simulate a hidden/background tab where rAF is paused.
        },
        setTimeout(callback, delay) {
            timers.push({ callback, delay });
            return timers.length;
        },
        clearTimeout() {},
    };
    context.globalThis = context;

    vm.runInNewContext(
        [
            extractFunction(source, 'scheduleDeferredChatInputWork'),
            `scheduleDeferredChatInputWork('large-paste');`,
        ].join('\n\n'),
        context,
        { filename: 'chatBox.js' },
    );

    assert.equal(timers.length, 1);
    assert.equal(timers[0].delay, fallbackDelay);

    timers[0].callback();

    assert.deepEqual(calls, [
        ['adjust', 'large-paste'],
        ['draft', 'large pasted text'],
        ['mention'],
    ]);
    assert.equal(context.deferredChatInputWorkScheduled, false);
});

test('deferred large paste work guard prevents fallback and late rAF duplicate execution', () => {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const fallbackDelay = extractNumericConstant(source, 'DEFERRED_CHAT_INPUT_WORK_FALLBACK_MS');
    const timers = [];
    const rafCallbacks = [];
    let workRuns = 0;
    const context = {
        DEFERRED_CHAT_INPUT_WORK_FALLBACK_MS: fallbackDelay,
        deferredChatInputWorkScheduled: false,
        chatInput: {
            value: 'large pasted text',
            scrollHeight: 42,
            scrollTop: 0,
        },
        adjustHeight() {
            workRuns += 1;
        },
        writeChatInputDraft() {},
        handleSkillMentionInput() {},
        requestAnimationFrame(callback) {
            rafCallbacks.push(callback);
        },
        setTimeout(callback, delay) {
            timers.push({ callback, delay });
            return timers.length;
        },
        clearTimeout() {},
    };
    context.globalThis = context;

    vm.runInNewContext(
        [
            extractFunction(source, 'scheduleDeferredChatInputWork'),
            `scheduleDeferredChatInputWork('large-paste');`,
        ].join('\n\n'),
        context,
        { filename: 'chatBox.js' },
    );

    assert.equal(timers.length, 1);
    timers[0].callback();
    assert.equal(workRuns, 1);

    rafCallbacks[0]();
    assert.equal(workRuns, 1);
});
