const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

const CHAT_DIR = __dirname;

test('icon-only preview busy controls do not replace icons with text labels', () => {
    const source = readFrontendSource(path.join(CHAT_DIR, 'canvas-widget.js'), 'utf8');
    assert.match(source, /window.chatDownloadControls.setDownloadBusy/);
    assert.doesNotMatch(source, /previewDownloadDefaultHtml|busyLabel:/);
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





test('preview format actions reuse the shared dropdown and omit custom split-button styling', () => {
    const controls = readFrontendSource(path.join(CHAT_DIR, 'downloadControls.js'), 'utf8');
    const canvas = readFrontendSource(path.join(CHAT_DIR, 'canvas-widget.js'), 'utf8');
    const css = readFrontendSource(path.join(CHAT_DIR, '../../css/chat/slide-presentation-widget.css'), 'utf8');
    assert.match(controls, /window.openDropdownMenu/);
    assert.match(controls, /await onDownload\(event\)/);
    assert.match(canvas, /bindDownloadFormatMenu\(previewDownloadFormat/);
    assert.doesNotMatch(css, /custom-download|preview-download-controls|preview-download-select/);
});
