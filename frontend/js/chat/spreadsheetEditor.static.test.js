const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const vm = require('node:vm');

const FRONTEND = path.join(__dirname, '..', '..');
const editorSource = readFrontendSource(path.join(__dirname, 'spreadsheet_editor.js'), 'utf8');
const canvasSource = readFrontendSource(path.join(__dirname, 'canvas-widget.js'), 'utf8');
const filesSource = readFrontendSource(path.join(__dirname, 'files.js'), 'utf8');
const indexSource = readFrontendSource(path.join(FRONTEND, 'index.html'), 'utf8');
const editorStyles = readFrontendSource(path.join(FRONTEND, 'css', 'chat', 'spreadsheet-editor.css'), 'utf8');
const canvasStyles = readFrontendSource(path.join(FRONTEND, 'css', 'chat', 'canvas-widget.css'), 'utf8');

test('spreadsheet editor supports formulas, native XLSX preservation, and internal format serialization', () => {
    assert.match(editorSource, /cellFormula:\s*true/);
    assert.match(editorSource, /patchOriginalXlsx/);
    assert.match(editorSource, /xl\/charts\//);
    assert.match(editorSource, /fullCalcOnLoad/);
    assert.match(editorSource, /column-resize/);
    assert.match(editorSource, /handlePointerMove/);
    assert.match(editorSource, /'selectstart'/);
    assert.match(editorSource, /this\.commitCellEdit\(this\.editing\.input\.value\)/);
    assert.match(editorSource, /window\.getSelection\?\.\(\)\?\.removeAllRanges/);
    assert.match(editorSource, /bookType:\s*format === 'xls' \? 'biff8' : 'xlsx'/);
    assert.match(editorSource, /sheet_to_csv\(this\.sheetForDelimitedExport\(\)/);
    assert.match(editorSource, /const style = cellNode\?\.getAttribute\('s'\) \|\| '';/);
    assert.doesNotMatch(editorSource, /String\(cell\.s\)/);
    assert.doesNotMatch(editorSource, /\b(?:alert|confirm|prompt)\s*\(/);
});

test('workspace spreadsheets route through Canvas and binary autosave retains file identity', () => {
    assert.match(filesSource, /supportedSpreadsheetExtensions:\s*\['csv', 'tsv', 'xlsx', 'xls'\]/);
    assert.match(filesSource, /isSpreadsheetPreviewFile/);
    assert.match(canvasSource, /\/api\/v1\/files\/canvas\/spreadsheet\/save/);
    assert.match(canvasSource, /\/api\/v1\/files\/canvas\/spreadsheet\/content/);
    assert.match(canvasSource, /loadSpreadsheetFromFile/);
    assert.match(indexSource, /\/js\/vendor\/xlsx\.full\.min\.js/);
    assert.match(indexSource, /\/js\/chat\/spreadsheet_editor\.js/);
    assert.match(indexSource, /\/css\/chat\/spreadsheet-editor\.css/);
    assert.doesNotMatch(canvasSource, /\{ value: 'xls', key: 'canvas_spreadsheet_download_xls'/);
});

test('spreadsheet teardown starts pending persistence before detaching the editor', () => {
    const functionStart = canvasSource.indexOf('function destroyActiveSpreadsheetEditor');
    const functionEnd = canvasSource.indexOf('function schedulePreviewRender', functionStart);
    const teardownSource = canvasSource.slice(functionStart, functionEnd);
    const commitIndex = teardownSource.indexOf('editor.commitPendingEdit?.()');
    const saveIndex = teardownSource.indexOf('void saveActiveDraftEdits(draftKey)');
    const detachIndex = teardownSource.indexOf('delete draft.spreadsheetEditor');
    const destroyIndex = teardownSource.indexOf('editor.destroy()');

    assert.ok(commitIndex >= 0, 'teardown does not commit the focused cell');
    assert.ok(commitIndex < saveIndex, 'serialization starts before the focused cell is committed');
    assert.ok(saveIndex >= 0, 'teardown does not start the pending save');
    assert.ok(saveIndex < detachIndex, 'draft editor is detached before serialization starts');
    assert.ok(saveIndex < destroyIndex, 'editor is destroyed before serialization starts');
    assert.match(canvasSource, /\.\.\(isSpreadsheet \? \{ binaryContent: null \} : \{\}\)/);
    assert.match(canvasSource, /const pendingSave = draftSavePromises\.get\(fileId\);/);
    assert.match(canvasSource, /if \(pendingSave\) await pendingSave;/);
    assert.match(canvasSource, /activeSpreadsheetEditorDraftKey !== draftKey \|\| !spreadsheetData/);
    assert.doesNotMatch(canvasSource, /draftSavePromises\.clear\(\)/);
});

test('reset commits focused edits before checking dirty state and revert discards them', () => {
    const resetStart = canvasSource.indexOf('function reset()');
    const resetEnd = canvasSource.indexOf('function setupEventListeners', resetStart);
    const resetSource = canvasSource.slice(resetStart, resetEnd);
    const commitIndex = resetSource.indexOf('draft?.spreadsheetEditor?.commitPendingEdit?.()');
    const dirtyIndex = resetSource.indexOf('state?.dirty');

    assert.ok(commitIndex >= 0, 'reset does not commit the focused spreadsheet cell');
    assert.ok(commitIndex < dirtyIndex, 'reset checks dirty state before committing the focused cell');
    assert.match(canvasSource, /persistPending: false,\s*commitPending: false,/);
});

test('spreadsheet autosave sends and handles optimistic revisions', () => {
    assert.match(canvasSource, /formData\.append\('expected_revision', String\(Number\(draft\.canvasRevision\) \|\| 0\)\)/);
    assert.match(canvasSource, /spreadsheet_revision_conflict:/);
    assert.match(canvasSource, /response\.status/);
});

test('saving preserves formula recalculation state in the mounted editor', () => {
    const instrumentedSource = editorSource.replace(
        'window.ChatSpreadsheetEditor = {',
        'window.ChatSpreadsheetEditor = { __SpreadsheetEditor: SpreadsheetEditor,',
    );
    const context = {
        window: {},
        console,
        TextEncoder,
        TextDecoder,
        Blob,
        ArrayBuffer,
        Uint8Array,
        Map,
        Set,
    };
    vm.createContext(context);
    vm.runInContext(instrumentedSource, context);
    const SpreadsheetEditor = context.window.ChatSpreadsheetEditor.__SpreadsheetEditor;
    const editor = Object.create(SpreadsheetEditor.prototype);
    editor.format = 'xlsx';
    editor.requiresRecalculation = true;
    editor.originalBytes = new ArrayBuffer(1);
    editor.editedCells = new Map([['A1', { v: 1 }]]);
    editor.editedColumnWidths = new Map([[0, 120]]);
    editor.undoStack = [{}];
    editor.redoStack = [{}];
    editor.setDirty = (dirty) => { editor.dirty = dirty; };
    editor.updateToolbar = () => {};

    editor.markSaved({ format: 'xlsx', bytes: new ArrayBuffer(2) });

    assert.equal(editor.requiresRecalculation, true);
    assert.equal(editor.dirty, false);
    assert.equal(editor.editedCells.size, 0);
    assert.equal(editor.editedColumnWidths.size, 0);
    assert.equal(editor.displayCellValue({ f: 'A1+1', v: 2, w: '2' }), '=A1+1');
    editor.requiresRecalculation = false;
    assert.equal(editor.displayCellValue({ f: 'A1+1', v: 2, w: '2' }), '2');
});

test('spreadsheet mode suppresses document reference and single-table header controls', () => {
    assert.match(canvasSource, /SPREADSHEET_CONTENT_TYPES\.has\(state\.activeReferenceSelection\?\.contentType\)/);
    assert.match(canvasStyles, /data-content-type="xlsx"\] \.canvas-markdown-copy-btn/);
    assert.match(editorSource, /root\.dataset\.format = this\.format/);
    assert.match(editorSource, /const hasSheetNavigation = this\.workbook\.SheetNames\.length > 1/);
    assert.match(editorSource, /sheetControls\.hidden = !hasSheetNavigation && !canAddSheet/);
    assert.match(editorSource, /tabsBar\.hidden = sheetControls\.hidden && !this\.editable/);
    assert.match(editorStyles, /\.canvas-spreadsheet-sheet-controls\[hidden\]/);
});

test('spreadsheet translations exist in every supported index locale', () => {
    const i18nRoot = path.join(FRONTEND, 'i18n');
    const locales = fs.readdirSync(i18nRoot).filter((locale) => (
        fs.existsSync(path.join(i18nRoot, locale, 'index.json'))
    ));
    const requiredKeys = [
        'canvas_type_excel_workbook',
        'canvas_spreadsheet_download_xlsx',
        'spreadsheet_editor_aria',
        'spreadsheet_formula_bar_aria',
        'spreadsheet_feature_workbook_structure',
        'spreadsheet_compatibility_mode',
        'spreadsheet_legacy_xls_notice',
        'spreadsheet_legacy_xls_help',
        'spreadsheet_preview_too_large',
        'spreadsheet_archive_too_complex',
        'spreadsheet_save_failed',
        'spreadsheet_revision_conflict',
    ];
    locales.forEach((locale) => {
        const translations = JSON.parse(readFrontendSource(path.join(i18nRoot, locale, 'index.json'), 'utf8'));
        requiredKeys.forEach((key) => assert.equal(
            typeof translations[key],
            'string',
            `${locale} is missing ${key}`,
        ));
        assert.equal(translations.canvas_spreadsheet_download_xlsx, 'Excel');
        assert.equal(
            translations.spreadsheet_compatibility_mode.includes('{features}'),
            false,
            `${locale} compatibility badge is not concise`,
        );
        assert.equal(
            Object.prototype.hasOwnProperty.call(translations, 'canvas_spreadsheet_download_xls'),
            false,
            `${locale} still exposes XLS as an export format`,
        );
    });
});
