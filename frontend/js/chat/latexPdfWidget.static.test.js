const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const { readStreamMessagesSource } = require('./messages/source.cjs');
const { readSendMessageSource } = require('./sending/source.cjs');

const CHAT_JS_DIR = __dirname;
const FRONTEND_DIR = path.join(CHAT_JS_DIR, '..', '..');

function cssRuleBlock(css, selector) {
    const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const match = css.match(new RegExp(`${escapedSelector}\\s*\\{([^}]*)\\}`));
    return match ? match[1] : '';
}

test('latex pdf preview is wired into chat streaming and saved transcript restore', () => {
    const widget = readFrontendSource(path.join(CHAT_JS_DIR, 'latex-pdf-widget.js'), 'utf8');
    const sendMessage = readSendMessageSource();
    const transcript = readFrontendSource(path.join(CHAT_JS_DIR, 'chatTranscriptRenderer.js'), 'utf8');
    const index = readFrontendSource(path.join(FRONTEND_DIR, 'index.html'), 'utf8');
    const canvasWidget = readFrontendSource(path.join(CHAT_JS_DIR, 'canvas-widget.js'), 'utf8');

    assert.match(index, /latex-pdf-PreviewPanel/);
    assert.match(canvasWidget, /canvas-markdown-DownloadFormat/);
    assert.match(canvasWidget, /function ensureCanvasPreviewHeader/);
    // The skill editor intentionally composes Canvas header classes. The
    // actual Canvas header remains runtime-mounted by ensureCanvasPreviewHeader.
    assert.doesNotMatch(index, /id="canvas-markdown-PreviewClose"/);
    assert.doesNotMatch(index, /latex-pdf-PreviewDownloadFormat/);
    assert.doesNotMatch(index, /latex-pdf-PreviewShare/);
    assert.match(index, /<div class="latex-pdf-preview-download-controls">\s*<button class="latex-pdf-preview-icon-btn disabled" id="latex-pdf-PreviewDownload" type="button"/);
    assert.match(index, /\/js\/chat\/downloadControls\.js/);
    assert.match(index, /\/js\/chat\/latex-pdf-widget\.js/);
    assert.match(widget, /window\.latexPdfWidget/);
    assert.match(widget, /handleLatexPdfEvent/);
    assert.match(widget, /renderLatexPdfResultBlock/);
    assert.doesNotMatch(widget, /shareActivePdf|latex-pdf-PreviewShare/);
    assert.match(widget, /openLatexPdfPreview/);
    assert.match(widget, /showLatexPdfStatus/);
    assert.match(sendMessage, /latex_pdf_evt/);
    assert.match(sendMessage, /last_appended_message_type = ['"]latex_pdf['"]/);
    assert.match(transcript, /meta\.latex_pdf/);
    assert.match(transcript, /lastAppendedMessageType = 'latex_pdf'/);
    assert.match(widget, /finalizePriorThinkingBlocks/);
    assert.match(widget, /finalizeThinkingBlocks/);
});

test('latex pdf result suppresses duplicate generic pdf attachment cards', () => {
    const widget = readFrontendSource(path.join(CHAT_JS_DIR, 'latex-pdf-widget.js'), 'utf8');
    const sendMessage = readSendMessageSource();
    const streamMessages = readStreamMessagesSource();
    const transcript = readFrontendSource(path.join(CHAT_JS_DIR, 'chatTranscriptRenderer.js'), 'utf8');
    const helper = readFrontendSource(path.join(FRONTEND_DIR, '..', 'backend', 'app', 'tools', 'helper.py'), 'utf8');

    assert.match(widget, /latexPdfFileIds = new Set/);
    assert.match(widget, /suppressGenericAttachmentForFile/);
    assert.match(widget, /isLatexPdfFile/);
    assert.match(widget, /registerRepresentedFileIds/);
    assert.match(widget, /\[payload\.fileId, payload\.sourceFileId\]/);
    assert.match(sendMessage, /fileSource !== 'latex_pdf'/);
    assert.match(streamMessages, /window\.latexPdfWidget\?\.isLatexPdfFile\?\.\(fileId\)/);
    assert.match(transcript, /latexPdfFileIdsFromMeta/);
    assert.match(transcript, /meta\.source_file_id \|\| meta\.sourceFileId/);
    assert.match(helper, /"source": "latex_pdf"/);
    assert.doesNotMatch(helper, /documents\.append\(str\(pdf_payload\.get\("source_file_id"\)\)\)/);
});

test('latex pdf preview uses inline file rendering and cleans up on chat transitions', () => {
    const widget = readFrontendSource(path.join(CHAT_JS_DIR, 'latex-pdf-widget.js'), 'utf8');
    const chats = readFrontendSource(path.join(CHAT_JS_DIR, 'chats.js'), 'utf8');
    const script = readFrontendSource(path.join(CHAT_JS_DIR, 'script.js'), 'utf8');

    assert.match(widget, /buildPreviewUrl\(fileId\)/);
    assert.match(widget, /PREVIEW_PDF_FRAGMENT = 'toolbar=1&navpanes=0&view=FitH&zoom=page-width'/);
    assert.match(widget, /buildDownloadUrl\(fileId, \{ inline: true \}\)/);
    assert.doesNotMatch(widget, /downloadFormat/);
    assert.match(widget, /setDownloadControls/);
    assert.match(widget, /setDownloadBusy/);
    assert.match(widget, /meta\.latex_display_title/);
    assert.match(widget, /meta\.latex_source_file_id/);
    assert.match(widget, /activeFrameUrl = buildPreviewUrl\(payload\.fileId\)/);
    assert.match(widget, /frame\.onload = \(\) => completePreviewLoad\(token\)/);
    assert.match(widget, /hidePreviewPanel: hidePanel/);
    assert.match(widget, /reset: hidePanel/);
    assert.match(chats, /window\.latexPdfWidget\.hidePreviewPanel/);
    assert.match(chats, /window\.latexPdfWidget\.reset/);
    assert.match(script, /window\.latexPdfWidget\.reset/);
});

test('latex pdf uses canvas sidebar with TeX and rendered PDF downloads', () => {
    const canvasWidget = readFrontendSource(path.join(CHAT_JS_DIR, 'canvas-widget.js'), 'utf8');
    const dropdown = readFrontendSource(path.join(CHAT_JS_DIR, 'canvasFilesDropdown.js'), 'utf8');
    const index = readFrontendSource(path.join(FRONTEND_DIR, 'index.html'), 'utf8');

    assert.match(canvasWidget, /openLatexPdfPreview/);
    assert.match(canvasWidget, /showLatexPdfStatus/);
    assert.match(canvasWidget, /\/api\/v1\/files\/canvas\/latex\/render/);
    assert.match(canvasWidget, /expected_revision: Number\(draft\.canvasRevision\)/);
    assert.match(canvasWidget, /saveCanvasFileContent/);
    assert.match(canvasWidget, /renderSavedLatexDraft/);
    assert.match(canvasWidget, /contentType === 'latex'/);
    assert.match(canvasWidget, /previewDownloadFormat/);
    assert.match(canvasWidget, /sourceFileId/);
    assert.match(canvasWidget, /pdfFileId/);
    assert.match(canvasWidget, /meta\.latex_display_title/);
    assert.match(canvasWidget, /meta\.latex_source_file_id/);
    assert.match(canvasWidget, /meta\.latex_log_excerpt/);
    assert.match(canvasWidget, /contentType === 'latex'\s*\? true/);
    assert.match(canvasWidget, /draft\?\.renderStatus === 'rendering'/);
    assert.match(canvasWidget, /async function requestActivePreview\(\)/);
    assert.match(canvasWidget, /if \(currentHtmlViewMode !== 'code'\) return;/);
    assert.match(canvasWidget, /await saveActiveDraftEdits\(draftKey\)/);
    assert.match(canvasWidget, /await renderSavedLatexDraft\(draftKey, \{ switchToPreview: false \}\)/);
    assert.match(canvasWidget, /className = 'canvas-latex-preview-spinner'/);
    assert.doesNotMatch(canvasWidget, /await renderSavedLatexDraft\(migratedKey/);
    assert.doesNotMatch(canvasWidget, /void renderSavedLatexDraft\(fileId/);
    assert.doesNotMatch(canvasWidget, /void renderSavedLatexDraft\(key/);
    assert.match(canvasWidget, /const sidebarTitle = contentType === 'latex'/);
    assert.match(canvasWidget, /previewTitle\.textContent = sidebarTitle/);
    assert.match(dropdown, /latex-pdf/);
    // Mermaid and ordinary PDFs remain direct downloads. LaTeX exposes its
    // editable source and current rendered derivative through the split menu.
    assert.match(canvasWidget, /normalizedType === 'mermaid'/);
    assert.match(canvasWidget, /normalizedType === 'pdf'/);
    assert.match(canvasWidget, /normalizedType === 'latex'/);
    assert.match(canvasWidget, /latex:\s*\[[\s\S]*value: 'tex', key: 'latex_pdf_download_tex'[\s\S]*value: 'pdf', key: 'latex_pdf_download_pdf'/);
    assert.match(canvasWidget, /new Blob\(\[texSource\], \{ type: 'text\/x-tex;charset=utf-8' \}\)/);
    assert.match(canvasWidget, /selectedFormat === 'pdf' && !hasCurrentLatexPdf\(draft, editState\)/);
    // Reopening a stored dedicated-tool result must preserve its already
    // rendered PDF while preferring newer Canvas metadata when it exists.
    assert.match(canvasWidget, /meta\.latex_render_revision/);
    assert.match(canvasWidget, /meta\.latex_render_status/);
    assert.match(canvasWidget, /\(pdfFileId \? 'ready' : 'not_rendered'\)/);
    assert.match(canvasWidget, /canvas_revision: draftOrPayload\?\.canvasRevision/);
    assert.match(canvasWidget, /Promise\.all\(\[\s*loadContentFromFile\(payload\.sourceFileId\),\s*loadCanvasFileRecord\(payload\.sourceFileId\)/);
    assert.match(canvasWidget, /pdf_file_id: sourceMeta\.latex_pdf_file_id \|\| payload\.pdfFileId/);
    assert.match(canvasWidget, /setHtmlViewMode\(hasCurrentPreview \? 'preview' : 'code'\)/);
});

test('latex pdf iframe remains laid out while loading so browser pdf zoom can fit width', () => {
    const css = readFrontendSource(path.join(FRONTEND_DIR, 'css', 'chat', 'latex-pdf-widget.css'), 'utf8');
    const hiddenFrameRule = cssRuleBlock(css, '.latex-pdf-preview-frame');
    const visibleFrameRule = cssRuleBlock(css, '.latex-pdf-preview-frame.visible');

    assert.match(hiddenFrameRule, /display: block;/);
    assert.match(hiddenFrameRule, /opacity: 0;/);
    assert.match(visibleFrameRule, /opacity: 1;/);
    assert.doesNotMatch(hiddenFrameRule, /display: none;/);
});

test('artifact sharing supports base64 pdf rendering and canvas dialog accepts pdf contexts', () => {
    const artifactShare = readFrontendSource(path.join(FRONTEND_DIR, 'js', 'canvas-share.js'), 'utf8');
    const canvasWidget = readFrontendSource(path.join(CHAT_JS_DIR, 'canvas-widget.js'), 'utf8');
    const sharingPy = readFrontendSource(path.join(FRONTEND_DIR, '..', 'backend', 'app', 'files', 'sharing.py'), 'utf8');

    assert.match(artifactShare, /pdf: 'pdf'/);
    assert.match(artifactShare, /function renderPdfArtifact/);
    assert.match(artifactShare, /encoding === 'base64'/);
    assert.match(canvasWidget, /window\.artifactShareDialog/);
    assert.match(canvasWidget, /canonicalType === 'pdf'/);
    assert.match(sharingPy, /"application\/pdf": "pdf"/);
    assert.match(sharingPy, /SHAREABLE_FILENAME_TO_ARTIFACT_TYPE/);
    assert.match(sharingPy, /base64\.b64encode/);
});
