const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

const CHAT_DIR = __dirname;

test('canvas preview download busy state captures default HTML lazily', () => {
    const source = readFrontendSource(path.join(CHAT_DIR, 'canvas-widget.js'), 'utf8');

    assert.match(source, /let previewDownloadDefaultHtml = '';/);
    assert.match(source, /if \(!previewDownloadDefaultHtml\) \{\s*previewDownloadDefaultHtml = previewDownload\.innerHTML;\s*\}/);
});

test('HTML canvas downloads expose runnable source and frontend PNG rendering', () => {
    const source = readFrontendSource(path.join(CHAT_DIR, 'canvas-widget.js'), 'utf8');
    const dockerfile = readFrontendSource(path.join(CHAT_DIR, '../../../backend/Dockerfile'), 'utf8');
    const filesRouter = readFrontendSource(path.join(CHAT_DIR, '../../../backend/app/files/router.py'), 'utf8');

    assert.match(source, /html:\s*\[[\s\S]*value: 'html',[^\n]*canvas_html_download_html[\s\S]*value: 'png',[^\n]*canvas_html_download_png/);
    assert.match(source, /downloadContentType === 'html'[\s\S]*getRenderableContentForDraft\(state\.activeDraftKey/);
    assert.match(source, /const normalizedHtml = normalizeCanvasHtmlSource\(html\)/);
    // Downloaded HTML is the normalized authored source. The interactive
    // preview receives its isolation policy at render time instead, so the
    // download remains runnable outside Omlorix.
    assert.match(source, /new Blob\(\[normalizedHtml\], \{ type: 'text\/html;charset=utf-8' \}\)/);
    assert.match(source, /normalizeCanvasHtmlSource\(htmlContent\)/);
    assert.match(source, /renderHtmlCanvasPngBlob\(normalizedHtml\)/);
    assert.match(source, /ensureHtmlCanvasRenderer\(exportWindow\)/);
    assert.match(source, /sandbox', 'allow-same-origin allow-scripts'/);
    assert.match(source, /html2canvas\(exportDocument\.documentElement, \{/);
    assert.match(source, /allowTaint: false,[\s\S]*useCORS: true/);
    assert.match(source, /canvas\.toBlob\([\s\S]*'image\/png'/);
    assert.match(source, /saveBlob\(await renderHtmlCanvasPngBlob\(normalizedHtml\), pngFilename\)/);
    assert.doesNotMatch(source, /printWindow\.print\(\)/);
    assert.doesNotMatch(source, /\/api\/v1\/files\/canvas\/html\/pdf/);
    assert.doesNotMatch(filesRouter, /canvas\/html\/pdf/);
    assert.doesNotMatch(dockerfile, /^\s*chromium\s*\\?$/m);
    assert.ok(fs.existsSync(path.join(CHAT_DIR, '../vendor/html2canvas.min.js')));
    assert.ok(fs.existsSync(path.join(CHAT_DIR, '../vendor/html2canvas.LICENSE.txt')));
});

test('LaTeX canvas downloads expose live TeX source and the current rendered PDF', () => {
    const source = readFrontendSource(path.join(CHAT_DIR, 'canvas-widget.js'), 'utf8');

    assert.match(source, /latex:\s*\[[\s\S]*value: 'tex',[^\n]*latex_pdf_download_tex[\s\S]*value: 'pdf',[^\n]*latex_pdf_download_pdf/);
    assert.match(source, /function hasCurrentLatexPdf\(draft, editState = null\)/);
    assert.match(source, /pdfAvailable: hasCurrentLatexPdf\(currentDraft, nextState\)/);
    assert.match(source, /selectedFormat === 'tex' \? sourceFileId : \(selectedFormat === 'pdf' \? pdfFileId : ''\)/);
    assert.match(source, /const texSource = getRenderableContentForDraft\(state\.activeDraftKey, draft\.content \|\| ''\)/);
    assert.match(source, /new Blob\(\[texSource\], \{ type: 'text\/x-tex;charset=utf-8' \}\)/);
    assert.match(source, /selectedFormat === 'pdf' && !hasCurrentLatexPdf\(draft, editState\)/);
});

test('notes download uses guarded format helper and pinned note id', () => {
    const source = readFrontendSource(path.join(CHAT_DIR, 'notes.js'), 'utf8');

    assert.match(source, /const selectedNoteId = NotesState\.selectedNoteId;/);
    assert.match(source, /typeof window\.chatDownloadControls\?\.getSelectedDownloadFormat === 'function'/);
    assert.match(source, /await this\.saveCurrentNote\(selectedNoteId\)/);
    assert.match(source, /waitForNoteSaveToSettle\(\(\) => NotesState\.isSaving\)/);
    assert.match(source, /NotesAPI\.downloadNote\(selectedNoteId, 'pdf'\)/);
    assert.match(source, /async saveCurrentNote\(noteId = NotesState\.selectedNoteId\)/);
    assert.match(source, /const selectedNoteId = state\.activeNoteId;/);
    assert.match(source, /waitForNoteSaveToSettle\(\(\) => state\.isSaving\)/);
    assert.match(source, /NotesAPI\.downloadNote\(selectedNoteId, 'pdf'\)/);
    assert.match(source, /const savedNoteId = state\.activeNoteId;/);
    assert.match(source, /saveRequest = NotesAPI\.updateNote\(savedNoteId, nextContent, expectedUpdatedAt\);/);
    assert.match(source, /state\.activeSavePromise = saveRequest;[\s\S]*const updated = await saveRequest;/);
    assert.match(source, /if \(state\.activeNoteId !== savedNoteId\) return true;/);
    assert.doesNotMatch(source, /window\.chatDownloadControls\s*\?\s*window\.chatDownloadControls\.getSelectedDownloadFormat/);
});

test('notes tool preview exposes downloads and canvas-style result widget', () => {
    const notesSource = readFrontendSource(path.join(CHAT_DIR, 'notes.js'), 'utf8');
    const nativeWidgetsSource = readFrontendSource(path.join(CHAT_DIR, 'native-tool-widgets.js'), 'utf8');
    const dropdownSource = readFrontendSource(path.join(CHAT_DIR, 'canvasFilesDropdown.js'), 'utf8');
    const helperSource = readFrontendSource(path.join(CHAT_DIR, '../../../backend/app/tools/helper.py'), 'utf8');

    assert.match(notesSource, /id="notes-tool-DownloadFormat"[\s\S]*value="md"[\s\S]*notes_download_md/);
    assert.match(notesSource, /id="notes-tool-DownloadFormat"[\s\S]*value="pdf"[\s\S]*notes_download_pdf/);
    assert.match(notesSource, /id="notes-tool-PreviewDownload"[\s\S]*notes_download_aria/);
    assert.doesNotMatch(notesSource, /notes-tool-(?:Undo|Redo)Btn/);
    assert.match(notesSource, /id="notes-tool-CopyBtn"[\s\S]*notes_share_copy_action/);
    assert.match(notesSource, /NotesAPI\.downloadNote\(selectedNoteId, 'pdf'\)/);
    assert.match(notesSource, /function registerHeaderNote\(noteId, title = ''\)/);
    assert.match(notesSource, /dropdown\.registerFile\(`note:\$\{id\}`, displayTitle, 'note'/);
    assert.match(notesSource, /canvasFilesDropdown\.unregisterFile\(`note:\$\{noteId\}`\)/);
    assert.match(dropdownSource, /if \(type === 'note'\) return _t\('canvas_files_type_note', 'Note'\);/);
    assert.match(helperSource, /_build_frontend_widget_payload\(\s*"notes_result"/);
    assert.match(nativeWidgetsSource, /element\('div', 'canvas-markdown-result-widget notes-tool-result-widget'\)/);
});

test('canvas and notes use the shared custom download format menu', () => {
    const controlsSource = readFrontendSource(path.join(CHAT_DIR, 'downloadControls.js'), 'utf8');
    const canvasSource = readFrontendSource(path.join(CHAT_DIR, 'canvas-widget.js'), 'utf8');
    const notesSource = readFrontendSource(path.join(CHAT_DIR, 'notes.js'), 'utf8');
    const controlsCss = readFrontendSource(path.join(CHAT_DIR, '../../css/chat/slide-presentation-widget.css'), 'utf8');

    assert.match(controlsSource, /function enhanceDownloadFormatSelect\(selectEl, options = \{\}\)/);
    assert.match(controlsSource, /trigger\.setAttribute\('aria-haspopup', 'listbox'\)/);
    assert.match(controlsSource, /item\.setAttribute\('role', 'option'\)/);
    assert.match(controlsSource, /event\.key === 'Escape'/);
    assert.match(controlsSource, /open\([^)]*\) \{\s*if \(trigger\.disabled \|\| trigger\.hidden\) return;/);
    assert.match(controlsSource, /wrapper\.hidden = false;\s*trigger\.hidden = Boolean\(selectEl\.hidden\);/);
    assert.match(canvasSource, /enhanceDownloadFormatSelect\?\.\(previewDownloadFormat/);
    assert.equal((notesSource.match(/enhanceDownloadFormatSelect\?\./g) || []).length, 2);
    assert.match(controlsCss, /\.custom-download-format-menu[\s\S]*top: calc\(100% \+ 6px\)/);
    assert.match(controlsCss, /\.custom-download-format-menu[\s\S]*width: calc\(100% \+ 2px\)/);
    assert.match(controlsCss, /@media \(hover: hover\) and \(pointer: fine\)[\s\S]*\.custom-download-format-option/);
});
