const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadEditorPersistence() {
    const frames = [];
    const context = vm.createContext({
        requestAnimationFrame: (callback) => frames.push(callback),
        clearTimeout,
    });
    vm.runInContext(fs.readFileSync(
        path.join(__dirname, 'canvas-widget/editor-persistence.js'), 'utf8',
    ), context);
    const state = { activeDraftKey: 'draft', suppressUserScrollEvents: false };
    const previewTrack = {
        dataset: {}, scrollTop: 120, scrollLeft: 15,
        querySelector: () => null,
        addEventListener() {},
    };
    const rendered = [];
    let toolbarHidden = 0;
    const deps = {
        previewTrack,
        draftScrollStates: new Map(),
        draftEditStateMap: new Map(),
        draftMap: new Map(),
        autoSaveTimers: new Map(),
        SPREADSHEET_CONTENT_TYPES: new Set(['csv', 'tsv', 'xlsx', 'xls']),
        normalizeContentType: (value) => value,
        hideReferenceToolbar: () => { toolbarHidden += 1; },
        renderDraft: (...args) => rendered.push(args),
    };
    const editor = context.__omlorixCanvasWidgetModules.editorPersistence.create(deps, state);
    return { editor, state, deps, previewTrack, rendered,
        toolbarHidden: () => toolbarHidden,
        flushFrames: () => { while (frames.length) frames.shift()(); },
    };
}

test('Canvas user scrolling records each viewport and stops following without changing module state', () => {
    const { editor, state, previewTrack, toolbarHidden } = loadEditorPersistence();
    const targets = [
        [previewTrack, 'track'],
        ['canvas-html-code-view', 'code'],
        ['canvas-md-editor-view', 'markdownEditor'],
        ['canvas-md-source-editor', 'markdownSource'],
    ];
    for (const [element, prefix] of targets) {
        editor.resetScrollState('draft');
        const scrollState = editor.getScrollState('draft');
        scrollState.restoreOnNextRender = true;
        const target = typeof element === 'string'
            ? { scrollTop: 120, scrollLeft: 15, classList: { contains: (name) => name === element } }
            : element;
        editor.handleUserScrollEvent({ currentTarget: target });
        assert.equal(scrollState[`${prefix}ScrollTop`], 120);
        assert.equal(scrollState[`${prefix}ScrollLeft`], 15);
        assert.equal(scrollState.autoFollow, false);
        assert.equal(scrollState.userInterrupted, true);
        assert.equal(scrollState.restoreOnNextRender, false);
    }
    assert.equal(toolbarHidden(), targets.length);
    assert.deepEqual(state, { activeDraftKey: 'draft', suppressUserScrollEvents: false });
});

test('Canvas ignores programmatic scrolling and handles gestures and missing active drafts', () => {
    const { editor, state, deps, previewTrack, toolbarHidden } = loadEditorPersistence();
    state.suppressUserScrollEvents = true;
    editor.handleUserScrollEvent({ currentTarget: previewTrack });
    assert.equal(deps.draftScrollStates.size, 0);
    assert.equal(toolbarHidden(), 0);

    state.suppressUserScrollEvents = false;
    state.activeDraftKey = '';
    editor.handleUserScrollEvent({ currentTarget: previewTrack });
    editor.handleUserGestureEvent();
    assert.equal(deps.draftScrollStates.size, 0);

    state.activeDraftKey = 'draft';
    const scrollState = editor.getScrollState('draft');
    scrollState.restoreOnNextRender = true;
    editor.handleUserGestureEvent();
    assert.equal(scrollState.autoFollow, false);
    assert.equal(scrollState.userInterrupted, true);
    assert.equal(scrollState.restoreOnNextRender, false);
});

test('Canvas captures and restores the active Markdown editor viewport', () => {
    const { editor, state, previewTrack, flushFrames } = loadEditorPersistence();
    const viewport = { view: 'source', editorScrollTop: 70, editorScrollLeft: 3,
        sourceScrollTop: 210, sourceScrollLeft: 9 };
    const restored = [];
    state.activeMarkdownEditorInstance = {
        getScrollState: () => viewport,
        restoreScrollState: (value) => restored.push({ ...value }),
    };
    editor.handleUserGestureEvent();
    editor.captureScrollState('draft');
    assert.equal(editor.getStoredMarkdownScrollTop(editor.getScrollState('draft')), 210);
    previewTrack.scrollTop = 0;
    previewTrack.scrollLeft = 0;
    editor.applyScrollState('draft', { contentType: 'markdown' });
    assert.equal(state.suppressUserScrollEvents, true);
    flushFrames();
    assert.deepEqual(restored, [viewport, viewport]);
    assert.equal(previewTrack.scrollTop, 120);
    assert.equal(previewTrack.scrollLeft, 15);
    assert.equal(state.suppressUserScrollEvents, false);
});

test('Canvas reverts the active saved draft and preserves inactive drafts and saving edits', () => {
    const { editor, state, deps, rendered } = loadEditorPersistence();
    const draft = { fileId: 'file', contentType: 'markdown', content: 'saved' };
    deps.draftMap.set('draft', draft);
    const editState = editor.getDraftEditState('draft', 'saved');
    editor.updateDraftEditStateFromInput('draft', 'edited');
    const other = editor.getDraftEditState('other', 'other saved');
    editor.updateDraftEditStateFromInput('other', 'other edited');
    editState.saving = true;
    editor.revertActiveDraftEdits();
    assert.equal(editState.draftContent, 'edited');
    assert.equal(rendered.length, 0);
    editState.saving = false;
    editor.revertActiveDraftEdits();
    assert.equal(editState.draftContent, 'saved');
    assert.equal(editState.dirty, false);
    assert.equal(editState.autoSavePending, false);
    assert.equal(editState.error, '');
    assert.equal(other.draftContent, 'other edited');
    assert.equal(other.dirty, true);
    assert.deepEqual(rendered, [[draft, true]]);
    assert.equal(state.activeDraftKey, 'draft');
});
