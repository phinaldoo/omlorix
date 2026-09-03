const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const { readStreamMessagesSource } = require('./messages/source.cjs');
const { readSendMessageSource } = require('./sending/source.cjs');

const CANVAS_WIDGET_PATH = path.join(__dirname, 'canvas-widget.js');
const CANVAS_RENDERING_PATH = path.join(__dirname, 'canvas-widget', 'rendering.js');
const CHAT_BOX_PATH = path.join(__dirname, 'chatBox.js');
const CHAT_TRANSCRIPT_RENDERER_PATH = path.join(__dirname, 'chatTranscriptRenderer.js');
const DELETE_WARNING_MODALS_PATH = path.join(__dirname, 'deleteWarningModals.js');
const INDEX_HTML_PATH = path.join(__dirname, '..', '..', 'index.html');
const MARKDOWN_EDITOR_PATH = path.join(__dirname, 'markdown_editor.js');
const MARKDOWN_EDITOR_CSS_PATH = path.join(__dirname, '..', '..', 'css', 'chat', 'markdown_editor.css');
const CANVAS_WIDGET_CSS_PATH = path.join(__dirname, '..', '..', 'css', 'chat', 'canvas-widget.css');
const FILES_CSS_PATH = path.join(__dirname, '..', '..', 'css', 'chat', 'files.css');
const MODEL_SELECT_CSS_PATH = path.join(__dirname, '..', '..', 'css', 'chat', 'modelSelect.css');
const SIDEBAR_PATH = path.join(__dirname, 'sidebar.js');
const WORKSPACE_CSS_PATH = path.join(__dirname, '..', '..', 'css', 'chat', 'workspace-core.css');
const SEND_MESSAGE_SOURCE = readSendMessageSource();
const SPLIT_SCREEN_PATH = path.join(__dirname, 'splitScreen.js');
const TOOLS_HELPER_PATH = path.join(__dirname, '..', '..', '..', 'backend', 'app', 'tools', 'helper.py');
const I18N_ROOT = path.join(__dirname, '..', '..', 'i18n');
const CANVAS_PREVIEW_KEYS = [
    'canvas_resize_preview_aria',
    'canvas_view_mode_label',
    'canvas_html_download_html',
    'canvas_html_download_png',
    'canvas_html_interactions',
    'canvas_html_external_content',
    'canvas_html_external_prompt_title',
    'canvas_html_external_prompt_desc',
    'canvas_html_external_prompt_list_label',
    'canvas_html_external_prompt_deny',
    'canvas_html_external_prompt_allow',
    'canvas_html_preview_settings',
    'files_preview_too_large_limit',
    'canvas_status_writing_type',
    'canvas_status_loading',
    'canvas_status_saved',
    'canvas_status_not_saved',
];
const CANVAS_SHARE_KEYS = [
    'canvas_share_title',
    'canvas_share_selected_file',
    'canvas_share_close_aria',
    'canvas_share_active_links',
    'canvas_share_empty',
    'canvas_share_loading',
    'canvas_share_button_enabled',
    'canvas_share_button_unavailable',
    'canvas_share_button_disabled_admin',
    'canvas_share_link_url_aria',
    'canvas_share_success_created',
    'canvas_share_error_create',
    'canvas_share_error_copy',
    'canvas_share_request_failed_status',
    'canvas_share_password_label',
    'canvas_share_password_required',
    'canvas_share_delete_confirm',
    'canvas_share_success_deleted',
    'canvas_share_error_delete',
    'canvas_copy_output_failed',
    'canvas_add_selection_reference_aria',
    'canvas_add_selection_reference_label',
    'canvas_reference_select_text_first',
    'canvas_reference_added',
];

test('canvas share uses the chat share modal primitives and delete confirmation flow', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const modalRegistry = readFrontendSource(DELETE_WARNING_MODALS_PATH, 'utf8');
    const markup = readFrontendSource(INDEX_HTML_PATH, 'utf8');

    assert.doesNotMatch(source, /window\.(prompt|confirm)\s*\(/);
    assert.doesNotMatch(source, /`Request failed \(\$\{response\.status\}\)`/);
    assert.doesNotMatch(source, /function openShareActionDialog/);
    assert.doesNotMatch(markup, /id="canvas-artifact-ShareActionDialog"/);
    assert.doesNotMatch(markup, /id="canvas-artifact-ShareExpirySelect"/);
    assert.match(modalRegistry, /id: 'canvas-artifact-ShareOverlay'/);
    assert.match(modalRegistry, /overlayClass: 'cs-overlay'/);
    assert.match(modalRegistry, /cardClass: 'cs-modal'/);
    assert.match(modalRegistry, /titleId: 'canvas-artifact-ShareTitle'/);
    assert.match(modalRegistry, /id="canvas-artifact-SharePasswordToggle"/);
    assert.match(modalRegistry, /id="canvas-artifact-ShareExpiryInput"[\s\S]*type="datetime-local"/);
    assert.match(modalRegistry, /submit\('canvas-artifact-SharePrimaryBtn', 'chat_share_create_link', 'Create link'\)/);
    assert.match(modalRegistry, /cancel\('canvas-artifact-ShareSecondaryBtn', 'chat_share_done', 'Done'\)/);
    assert.match(source, /id="canvas-markdown-PreviewDownload"[\s\S]*files_preview_download/);
    assert.doesNotMatch(markup, /id="canvas-markdown-PreviewDownload"/);
    assert.match(source, /Math\.max\(1, Math\.ceil\(\(new Date\(expiresAt\)\.getTime\(\) - Date\.now\(\)\) \/ 3600000\)\)/);
    assert.match(source, /\/api\/v1\/files\/canvas\/share\/expiry\/change/);
    assert.match(source, /window\.showDeleteConfirm/);
    assert.match(source, /function enterShareEditMode\(link\)/);
    assert.match(source, /trapFocus\(event, shareModal\)/);
    assert.match(source, /showShareControlError\(sharePasswordInput, sharePasswordError/);
    assert.match(source, /showShareControlError\(shareExpiryInput, shareExpiryError/);
    assert.match(source, /canvas_copy_output_failed/);
    assert.doesNotMatch(source, /window\.notifyError\('Failed to copy canvas output'\)/);
});

test('single-format canvas types use a direct button while LaTeX exposes its format picker', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const canvasWidgetEntrySource = fs.readFileSync(CANVAS_WIDGET_PATH, 'utf8');
    const renderingSource = fs.readFileSync(CANVAS_RENDERING_PATH, 'utf8');
    const css = readFrontendSource(CANVAS_WIDGET_CSS_PATH, 'utf8');
    const renderingFactoryCall = canvasWidgetEntrySource.match(
        /canvasWidgetModules\.rendering\.create\(\{([\s\S]*?)\}, \{/,
    );
    const renderingDependencies = [
        'addMarkedSelectionAsReference',
        'getPreviewHeaderIcon',
        'hasAdjacentChatComposer',
        'hideReferenceToolbar',
        'openHtmlFullscreen',
        'prepareInteractiveHtmlPreviewSource',
        'refreshReferenceSelectionState',
        'replaceOmlorixFileUrls',
        'updateShareButtonState',
        'withIframeSecurityGuard',
    ];

    assert.match(source, /const usesDirectDownload = normalizedType === 'mermaid'[\s\S]*\|\| normalizedType === 'pdf';/);
    assert.doesNotMatch(source, /usesDirectDownload[\s\S]{0,160}normalizedType === 'latex'/);
    assert.match(source, /previewDownloadControls\?\.classList\.toggle\('is-direct-download', usesDirectDownload\)/);
    assert.match(source, /state\.previewVisible\s*&&\s*\(state\.sharingAllowedByGroup \|\| hasExistingShareLinks\)/);
    assert.match(source, /previewPanel\.setAttribute\('data-content-type', contentType\);[\s\S]*updateShareButtonState\(\)/);
    assert.ok(renderingFactoryCall);
    renderingDependencies.forEach((dependency) => {
        assert.match(renderingSource, new RegExp(`\\b${dependency}\\b`));
        assert.match(renderingFactoryCall[1], new RegExp(`\\b${dependency}\\b`));
    });
    assert.match(css, /\.canvas-markdown-preview-header-right \.is-direct-download > \.custom-download-format-trigger/);
    assert.doesNotMatch(css, /\[data-content-type="pdf"\] \.canvas-markdown-share-btn/);
});

test('canvas preview is closed by reset and empty tool-call starts do not flash markdown', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');

    assert.match(source, /function reset\(\)[\s\S]*setPanelVisible\(false\)/);
    assert.match(source, /id: 'canvas-preview',[\s\S]*isActive: \(\) => state\.previewVisible,[\s\S]*hidePreviewPanel\(\)/);
    assert.match(source, /filePreviewLoadTokens\.delete\(activeDraftKey\);[\s\S]*setPanelVisible\(false\)/);
    assert.match(source, /previewPanel\.toggleAttribute\('inert', !previewVisible\)/);
    assert.match(readFrontendSource(INDEX_HTML_PATH, 'utf8'), /id="canvas-markdown-PreviewPanel" aria-hidden="true" inert/);
    assert.match(source, /const hasRenderableInitialArgs = Boolean\(/);
    assert.match(source, /if \(resultKind === 'view' \|\| !hasRenderableInitialArgs\) \{[\s\S]*return;[\s\S]*\}[\s\S]*setPanelVisible\(true\)/);
});

test('canvas tool deltas open the live sidebar in normal, regenerated, and split-screen chats', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const sendSource = SEND_MESSAGE_SOURCE;
    const splitSource = readFrontendSource(SPLIT_SCREEN_PATH, 'utf8');

    assert.match(source, /function handleToolCallDeltaEvent\(obj, messageId\)/);
    assert.match(source, /const toolMessageId = normalizedMessageId \|\| lastActiveMessageId \|\| ''/);
    assert.match(source, /getLatestCanvasToolCallForMessage\(toolMessageId\)/);
    assert.match(source, /extractCanvasArgsFromBuffer\(buffer\)/);
    assert.match(source, /if \(!updated\.content && !updated\.fileId && !updated\.hasExplicitContentType\) \{[\s\S]*syncInlineWidgetForResultKind\(toolMessageId, updated\);[\s\S]*return;/);
    assert.match(source, /setPanelVisible\(true\)/);
    assert.match(source, /const existingDraft = draftMap\.get\(draftKey\);[\s\S]*getScrollState\(draftKey\)/);
    assert.equal((sendSource.match(/canvasMarkdownWidget\.handleToolCallDeltaEvent/g) || []).length, 4);
    assert.match(splitSource, /canvasMarkdownWidget\.handleToolCallEvent\(obj, messageId\)/);
    assert.match(splitSource, /canvasMarkdownWidget\.handleToolCallDeltaEvent\(obj, messageId\)/);
    assert.match(splitSource, /canvasMarkdownWidget\.handleCanvasEvent\(obj, messageId\)/);
});

test('markdown canvas streaming keeps rendered blocks stable and preserves user scroll', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const css = readFrontendSource(CANVAS_WIDGET_CSS_PATH, 'utf8');

    assert.match(source, /const MARKDOWN_STREAM_RENDER_INTERVAL_MS = 100/);
    assert.match(source, /function scheduleMarkdownStreamingRender\(draft, \{ immediate = false \} = \{\}\)/);
    assert.match(source, /if \(state\.markdownStreamRenderTimer\) return/);
    assert.match(source, /function reconcileStreamingMarkdown\(target, renderedHtml\)/);
    assert.match(source, /function renderStreamingMarkdownHtml\(content\)[\s\S]*renderMarkdownInto\(staging, markdown\)/);
    assert.match(source, /window\.prepareMarkdownCodeBlocksForTransfer\?\.\(staging\)/);
    assert.doesNotMatch(source, /renderStreamingMarkdownHtml\(content\)[\s\S]{0,500}ChatMarkdownBlockEditor\.renderMarkdownToHtml/);
    assert.match(source, /currentNodes\[stablePrefixLength\]\.isEqualNode\(nextNodes\[stablePrefixLength\]\)/);
    assert.match(source, /while \(target\.childNodes\.length > stablePrefixLength\)/);
    assert.match(source, /function renderStreamingMarkdownDraft\(draft\)/);
    assert.match(source, /preview\.className = 'canvas-markdown-streaming-preview canvas-markdown-render markdown-body'/);
    assert.match(source, /const shouldFollow = Boolean\(scrollState\?\.autoFollow && !scrollState\?\.userInterrupted\)/);
    assert.match(source, /function rememberCanvasScrollForToolCall\(draftKey\)/);
    assert.match(source, /function restoreCanvasScrollForToolEdit\(draftKey, fileId\)/);
    assert.match(source, /restoreOnNextRender: true/);
    assert.match(source, /streamingScrollState\?\.restoreOnNextRender[\s\S]*getStoredMarkdownScrollTop\(streamingScrollState\)/);
    assert.match(source, /state\.restoreOnNextRender = false/);
    assert.match(source, /activeMarkdownEditorInstance\?\.getScrollState\?\.\(\) \|\| null/);
    assert.match(source, /activeMarkdownEditorInstance\?\.restoreScrollState\?\.\(\{/);
    assert.match(source, /previewTrack\.addEventListener\('pointerdown', handlePreviewTrackPointerDown/);
    assert.match(source, /event\.clientX >= rect\.right - 18/);
    assert.match(source, /function restoreScrollAfterMarkdownStream\(key, scrollTop, autoFollow\)/);
    assert.doesNotMatch(source, /else if \(contentType === 'markdown'\) \{\s*renderDraft\(updated\)/);
    assert.match(css, /\.canvas-markdown-preview-track[\s\S]*overflow-anchor: none;/);
    assert.match(css, /\.canvas-markdown-streaming-preview[\s\S]*overflow-x: hidden;/);
});

test('canvas stream creates a chat file box only after arguments prove it is new', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const handlerStart = source.indexOf('    function handleToolCallDeltaEvent(obj, messageId)');
    const handlerEnd = source.indexOf('    function handleCanvasEvent(obj, messageId)', handlerStart);
    const handlerSource = source.slice(handlerStart, handlerEnd);
    const widgetStart = source.indexOf('    function injectInlineWidget(messageId, draft)');
    const widgetEnd = source.indexOf('    function updateInlineWidget(messageId, draft)', widgetStart);
    const widgetSource = source.slice(widgetStart, widgetEnd);

    assert.match(handlerSource, /classifyCanvasResultKind\(parsedArgs, extracted\)/);
    assert.match(handlerSource, /syncInlineWidgetForResultKind\(toolMessageId, updated\);[\s\S]*setPanelVisible\(true\)/);
    assert.doesNotMatch(handlerSource, /injectInlineWidget\(lastActiveMessageId, current\)/);
    assert.match(widgetSource, /if \(existing\) \{\s*updateInlineWidget\(messageId, draft\);\s*return existing;/);
    assert.doesNotMatch(widgetSource, /finalizeThinkingForMessage/);
});

test('canvas edits keep the tool activity row and do not emit another file box', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const backendSource = readFrontendSource(TOOLS_HELPER_PATH, 'utf8');
    const removeStart = source.indexOf('    function removeInlineWidget(messageId, draftKey');
    const removeEnd = source.indexOf('    function syncInlineWidgetForResultKind(', removeStart);
    const removeSource = source.slice(removeStart, removeEnd);

    assert.match(source, /if \(String\(extracted\.fileId \|\| ''\)\.trim\(\)\) return 'edit'/);
    assert.match(source, /if \(hasCanvasContentArgument\(args\)\) return 'create'/);
    assert.match(source, /if \(resultKind === 'create'\) \{[\s\S]*updateInlineWidgetFinal[\s\S]*\} else \{[\s\S]*removeInlineWidget/);
    assert.doesNotMatch(removeSource, /finalizeThinkingForMessage/);
    assert.match(backendSource, /if save_result\.get\("created"\) and save_result\.get\("file_id"\):[\s\S]*"t": "f"/);
    assert.match(backendSource, /documents\.append\(save_result\["file_id"\]\)/);
});

test('canvas view calls preserve the active preview and use view-specific activity text', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const streamSource = readStreamMessagesSource();
    const classifyStart = source.indexOf('    function classifyCanvasResultKind(');
    const classifyEnd = source.indexOf('    function updateStatusClass(', classifyStart);
    const classifySource = source.slice(classifyStart, classifyEnd);

    assert.ok(classifySource.indexOf("rawType === 'view'") < classifySource.indexOf("return 'edit'"));
    assert.match(source, /function updateDraft\(draftKey, updates, \{ activate = true \} = \{\}\)/);
    assert.match(source, /Viewing an artifact is observational[\s\S]*if \(updated\.resultKind === 'view'\) \{[\s\S]*return;/);
    assert.match(source, /resultKind !== 'view' && hasRenderableInitialArgs/);
    assert.match(streamSource, /assistant_tool_canvas_view_in_progress/);
    assert.match(streamSource, /assistant_tool_canvas_view_completed/);
});

test('concurrent split-screen canvas streams keep draft and widget ownership per message', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const splitSource = readFrontendSource(SPLIT_SCREEN_PATH, 'utf8');

    assert.match(source, /const canvasToolCallKeysByMessage = new Map\(\)/);
    assert.match(source, /function trackCanvasToolCallForMessage\(rawMessageId, rawDraftKey\)/);
    assert.match(source, /canvasToolCallKeysByMessage\.set\(messageId, keys\)/);
    assert.match(source, /function handleCanvasEvent\(obj, messageId\)/);
    assert.match(source, /getLatestCanvasToolCallForMessage\(eventMessageId\)[\s\S]*\|\| activeDraftKey/);
    assert.match(source, /updateInlineWidgetFinal\(eventMessageId, updated, updated\.fileId\)/);
    assert.match(source, /removeInlineWidget\(eventMessageId, sourceKey \|\| key\)/);
    assert.match(source, /forgetCanvasToolCallForMessage\(eventMessageId, eventToolCallId \|\| sourceKey\)/);
    assert.match(splitSource, /canvasMarkdownWidget\.handleCanvasEvent\(obj, messageId\)/);
});

test('saved canvas events are terminal and cannot be revived by late tool packets', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const sendSource = SEND_MESSAGE_SOURCE;
    const splitSource = readFrontendSource(SPLIT_SCREEN_PATH, 'utf8');
    const callStart = source.indexOf('    function handleToolCallEvent(obj, messageId)');
    const deltaStart = source.indexOf('    function handleToolCallDeltaEvent(obj, messageId)', callStart);
    const savedStart = source.indexOf('    function handleCanvasEvent(obj, messageId)', deltaStart);
    const streamEndStart = source.indexOf('    function handleStreamEnd(messageId)', savedStart);

    const callSource = source.slice(callStart, deltaStart);
    const deltaSource = source.slice(deltaStart, savedStart);
    const savedSource = source.slice(savedStart, streamEndStart);

    assert.match(callSource, /if \(isCanvasToolCallTerminal\(draftKey\)\) return;/);
    assert.match(deltaSource, /if \(isCanvasToolCallTerminal\(draftKey\)\) return;/);
    assert.match(savedSource, /const eventToolCallId = String\(data\.tool_call_id/);
    assert.match(savedSource, /eventToolCallId[\s\S]*\|\| getLatestCanvasToolCallForMessage/);
    assert.match(savedSource, /markCanvasToolCallTerminal\(eventToolCallId \|\| sourceKey\)/);
    assert.match(source, /function handleStreamEnd\(messageId\)[\s\S]*canvas_status_not_saved/);
    assert.match(source, /handleCanvasEvent,[\s\S]*handleStreamEnd,/);
    assert.match(sendSource, /canvasMarkdownWidget\.handleStreamEnd\(messageId\)/);
    assert.match(splitSource, /canvasMarkdownWidget\.handleStreamEnd\(messageId\)/);
});

test('canvas file load failures replace the generating visual state', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const loadStart = source.indexOf('    async function openPreviewForFile(');
    const loadEnd = source.indexOf('\n\n    function handleToolCallEvent(', loadStart);
    const loadSource = source.slice(loadStart, loadEnd);

    assert.match(loadSource, /statusKind: detectedType === 'pdf' \? 'saved' : 'generating'/);
    assert.match(loadSource, /catch \(error\)[\s\S]*statusKind: 'error'/);
});

test('saved canvas file boxes finalize thinking without changing expansion state', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const finalizeStart = source.indexOf('    function finalizeThinkingForMessage(messageId)');
    const finalizeEnd = source.indexOf('    function renderSavedWidgetFromFile', finalizeStart);
    const finalizeSource = source.slice(finalizeStart, finalizeEnd);
    const finalStart = source.indexOf('    function updateInlineWidgetFinal(messageId, draft, fileId)');
    const finalEnd = source.indexOf('    function registerCanvasFile', finalStart);
    const finalSource = source.slice(finalStart, finalEnd);

    assert.match(finalizeSource, /if \(typeof finalizeThinkingBlocks === 'function'\)/);
    assert.match(finalizeSource, /if \(typeof finalizeThinkingBlockHeader === 'function'\)/);
    assert.doesNotMatch(finalizeSource, /classList.*collapsed|aria-expanded/);
    assert.match(finalSource, /finalizeThinkingForMessage\(messageId\)/);
    assert.match(finalSource, /widget\.setAttribute\('data-canvas-status', 'saved'\)/);
});

test('canvas preview sidebar can be resized from its border', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const css = readFrontendSource(CANVAS_WIDGET_CSS_PATH, 'utf8');

    assert.match(source, /id = 'canvas-markdown-PreviewResizer'/);
    assert.match(source, /role', 'separator'/);
    assert.match(source, /PREVIEW_DEFAULT_WIDTH_RATIO = 0\.5/);
    assert.match(source, /setPreviewWidthFromPointerX/);
    assert.match(source, /handlePreviewResizerKeydown/);
    assert.match(source, /localStorage\?\.setItem\(PREVIEW_WIDTH_STORAGE_KEY/);
    assert.match(source, /document\.documentElement\.style\.setProperty\('--canvas-markdown-preview-width', widthValue\)/);
    assert.match(source, /previewPanel\?\.style\.setProperty\('--canvas-markdown-preview-width', widthValue\)/);
    assert.match(css, /--canvas-markdown-preview-width: 50vw/);
    assert.match(css, /--canvas-markdown-preview-min-width: 420px/);
    assert.match(source, /PREVIEW_MIN_PANEL_WIDTH = 420/);
    assert.match(css, /body\.canvas-markdown-preview-open \.main-container[\s\S]*var\(--canvas-markdown-resolved-preview-width\)/);
    assert.match(css, /\.canvas-markdown-preview-resizer/);
    assert.match(css, /body\.canvas-markdown-preview-resizing/);
});

test('narrow chat panes collapse secondary header labels', () => {
    const modelSelectCss = readFrontendSource(MODEL_SELECT_CSS_PATH, 'utf8');

    assert.match(modelSelectCss, /@container chat-layout \(max-width: 560px\)/);
    assert.match(modelSelectCss, /\.model-select-label \.label-name,[\s\S]*\.model-select-trigger-status[\s\S]*display: none/);
});

test('canvas and notes headers truncate filenames before shrinking controls', () => {
    const canvasCss = readFrontendSource(CANVAS_WIDGET_CSS_PATH, 'utf8');

    assert.match(canvasCss, /\.canvas-markdown-preview-header-left\s*\{[^}]*flex: 1 1 0;[^}]*min-width: 0;[^}]*overflow: hidden;/);
    assert.match(canvasCss, /\.canvas-markdown-preview-header-right\s*\{[^}]*flex: 0 0 auto;[^}]*min-width: max-content;/);
    assert.match(canvasCss, /\.canvas-markdown-preview-header-right > \*\s*\{[^}]*flex: 0 0 auto;/);
    assert.match(canvasCss, /\.canvas-markdown-preview-title-wrap\s*\{[^}]*flex: 1 1 0;[^}]*min-width: 0;[^}]*overflow: hidden;/);
    assert.match(canvasCss, /\.canvas-markdown-preview-title\s*\{[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap;[^}]*overflow: hidden;/);
});

test('narrow canvas and notes previews collapse view tabs to accessible icon buttons', () => {
    const canvasSource = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const notesSource = readFrontendSource(path.join(__dirname, 'notes.js'), 'utf8');
    const canvasCss = readFrontendSource(CANVAS_WIDGET_CSS_PATH, 'utf8');

    assert.match(canvasCss, /@container canvas-preview \(max-width: 600px\)\s*\{[\s\S]*\.canvas-markdown-editor-view-btn-label\s*\{\s*display: none;/);
    assert.match(canvasCss, /@container canvas-preview \(max-width: 600px\)[\s\S]*\.canvas-markdown-editor-view-btn\s*\{[^}]*width: 30px;[^}]*height: 30px;[^}]*padding: 0;/);
    assert.doesNotMatch(canvasCss, /\.om-button/);
    assert.match(canvasCss, /@container canvas-preview \(max-width: 600px\)[\s\S]*\.slide-presentation-preview-download-controls\.is-direct-download\s*\{[^}]*width: 34px;/);
    assert.match(canvasSource, /id="canvas-markdown-MarkdownTab"[^>]*aria-label="Markdown"[^>]*data-i18n-attr="aria-label:markdown_editor_tab_markdown;title:markdown_editor_tab_markdown"/);
    assert.match(canvasSource, /id="canvas-markdown-EditorTab"[^>]*aria-label="Editor"[^>]*data-i18n-attr="aria-label:markdown_editor_tab_editor;title:markdown_editor_tab_editor"/);
    assert.match(canvasSource, /class="canvas-html-view-toggle canvas-markdown-editor-view-toggle"[^>]*role="tablist"[^>]*canvas_view_mode_label/);
    assert.match(canvasSource, /id="canvas-html-ViewCodeBtn"[^>]*role="tab"[^>]*aria-selected="false"[^>]*canvas_edit_source_aria/);
    assert.match(canvasSource, /id="canvas-html-ViewPreviewBtn"[^>]*role="tab"[^>]*aria-selected="true"[^>]*canvas_view_preview_aria/);
    assert.match(canvasSource, /code_block_tab_code">Code<\/span>/);
    assert.match(canvasSource, /code_block_tab_preview">Preview<\/span>/);
    assert.match(canvasSource, /codeBtn\.setAttribute\('aria-selected', effectiveMode === 'code'/);
    assert.match(canvasSource, /previewBtn\.setAttribute\('aria-selected', effectiveMode === 'preview'/);
    assert.match(notesSource, /id="notes-tool-MarkdownTab"[^>]*aria-label=/);
    assert.match(notesSource, /id="notes-tool-EditorTab"[^>]*aria-label=/);
    assert.match(notesSource, /state\.markdownTab\.setAttribute\('aria-label', markdownLabel\)/);
    assert.match(notesSource, /state\.editorTab\.setAttribute\('aria-label', editorLabel\)/);
});

test('saved HTML canvases use the editable autosaving source editor with line numbers', () => {
    const canvasSource = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const canvasCss = readFrontendSource(CANVAS_WIDGET_CSS_PATH, 'utf8');

    // Saved HTML follows the same persistable-draft rule as the other Canvas
    // formats; there must be no content-type veto that disables its textarea.
    assert.doesNotMatch(canvasSource, /normalizeContentType\(draft\.contentType\) === 'html'\) return false/);
    assert.match(canvasSource, /function isDraftEditorInteractive\(draft\)[\s\S]*if \(isDraftPersistable\(draft\)\) return true/);
    assert.match(canvasSource, /editor\.readOnly = !editable;[\s\S]*editor\.disabled = false;/);
    assert.match(canvasSource, /editor\.setAttribute\('aria-readonly', editable \? 'false' : 'true'\)/);
    assert.match(canvasSource, /editor\.addEventListener\('input',[\s\S]*handleDraftContentChange\(editor\.value\)[\s\S]*schedulePreviewRender/);
    assert.match(canvasSource, /queueAutoSaveForDraft\(draftKey\)/);

    // Autosave refreshes only the surrounding chrome so focus, selection,
    // scroll, and native undo history remain owned by the mounted textarea.
    assert.match(canvasSource, /function refreshActiveHtmlDraftAfterSave\(draft, editState\)/);
    assert.match(canvasSource, /!refreshActiveHtmlDraftAfterSave\(updated, nextState\)/);
    assert.doesNotMatch(
        canvasSource.match(/function refreshActiveHtmlDraftAfterSave[\s\S]*?\n    }/)?.[0] || '',
        /renderDraft\(/,
    );

    assert.match(canvasSource, /className = 'canvas-html-code-gutter'/);
    assert.match(canvasSource, /syncCanvasCodeGutter\(editor, editorGutter, \{ refreshLines: true \}\)/);
    assert.match(canvasSource, /editor\.addEventListener\('scroll',[\s\S]*syncCanvasCodeGutter\(editor, editorGutter\)/);
    assert.match(canvasCss, /\.canvas-html-code-shell\s*\{[^}]*grid-template-columns: auto minmax\(0, 1fr\);/s);
    assert.match(canvasCss, /\.canvas-html-code-gutter\s*\{[^}]*min-width: 44px;[^}]*text-align: right;/s);
    assert.match(canvasCss, /\.canvas-html-code-gutter span\s*\{[^}]*display: block;[^}]*min-height: 1\.6em;/s);
});

test('HTML-family MIME types and filenames resolve to the Code and Preview Canvas', () => {
    const canvasSource = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');

    for (const mimeType of [
        'text/html',
        'application/html',
        'application/xhtml+xml',
        'application/x-html',
        'text/xhtml',
    ]) {
        assert.match(canvasSource, new RegExp(`['"]${mimeType.replace(/[.+]/g, '\\$&')}['"]`));
    }
    for (const suffix of ['.html', '.htm', '.xhtml', '.xht', '.xhtm', '.shtml', '.shtm']) {
        assert.match(canvasSource, new RegExp(`['"]${suffix.replace('.', '\\.')}['"]`));
    }

    assert.match(canvasSource, /function hasHtmlFileExtension\(fileName\)/);
    assert.match(canvasSource, /if \(hasHtmlFileExtension\(name\)\) return 'html';/);
    assert.match(canvasSource, /if \(detectedType === 'html' \|\| detectedType === 'mermaid'\) \{\s*setHtmlViewMode\('preview'\);/);
    assert.match(canvasSource, /htmlViewCodeBtn\.addEventListener\('click', \(\) => setHtmlViewMode\('code'\)\)/);
    assert.match(canvasSource, /htmlViewPreviewBtn\.addEventListener\('click', \(\) => \{\s*void requestActivePreview\(\);/);
    assert.match(canvasSource, /if \(!draft \|\| normalizeContentType\(draft\.contentType\) !== 'latex'\) \{\s*setHtmlViewMode\('preview'\);/);
});

test('open Markdown, HTML, and PDF previews give the remaining Workspace and Files pane its mobile layout', () => {
    const canvasSource = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const filesSource = readFrontendSource(path.join(__dirname, 'files.js'), 'utf8');
    const sidebarSource = readFrontendSource(SIDEBAR_PATH, 'utf8');
    const canvasCss = readFrontendSource(CANVAS_WIDGET_CSS_PATH, 'utf8');
    const filesCss = readFrontendSource(FILES_CSS_PATH, 'utf8');
    const workspaceCss = readFrontendSource(WORKSPACE_CSS_PATH, 'utf8');

    // All document preview types use the compact layout while the preview is
    // visible, so keep this source contract aligned with the shared condition.
    assert.match(canvasSource, /previewVisible\s*&&\s*\['markdown',\s*'html',\s*'pdf'\]\.includes\(\s*previewPanel\?\.dataset\.contentType\s*\)/);
    assert.match(canvasSource, /classList\.toggle\(\s*'canvas-markdown-compact-main-layout'/);
    assert.match(canvasSource, /setMainSidebarCompactLayout\('canvas-markdown-preview', shouldUseCompactLayout\)/);
    assert.match(canvasSource, /new CustomEvent\('canvasMarkdownCompactLayoutChange'/);
    assert.match(sidebarSource, /const _sidebarCompactLayoutSources = new Set\(\)/);
    assert.match(sidebarSource, /if \(compactLayoutRequested\) \{\s*shouldOverlay = true;/);
    assert.match(canvasCss, /--canvas-markdown-compact-main-width:/);

    assert.match(workspaceCss, /body\.canvas-markdown-compact-main-layout\.workspace-view-active \.workspace-header-desktop-tabs\s*\{[^}]*display:\s*none;/);
    assert.match(workspaceCss, /body\.canvas-markdown-compact-main-layout\.workspace-view-active \.workspace-header-mobile\s*\{[^}]*display:\s*block;/);
    assert.doesNotMatch(workspaceCss, /workspace-mobile-dock|workspace-more-sheet/);

    assert.match(filesCss, /body\.canvas-markdown-compact-main-layout \.files-sidebar[\s\S]*position: fixed;[\s\S]*transform: translateX\(-100%\)/);
    assert.match(filesCss, /body\.canvas-markdown-compact-main-layout \.files-main-header-btn\s*\{[\s\S]*display: flex;/);
    assert.match(filesSource, /document\.body\.classList\.contains\('canvas-markdown-compact-main-layout'\)/);
    assert.match(filesSource, /document\.addEventListener\('canvasMarkdownCompactLayoutChange'/);
});

test('canvas preview header omits undo and redo buttons', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');

    assert.doesNotMatch(source, /canvas-markdown-(?:Undo|Redo)Btn/);
});

test('html canvas streaming infers content-first HTML and throttles live renders', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const applyScrollStateStart = source.indexOf('    function applyScrollState(key, draft)');
    const applyScrollStateEnd = source.indexOf('    function restoreScrollAfterMarkdownStream', applyScrollStateStart);
    const applyScrollStateSource = source.slice(applyScrollStateStart, applyScrollStateEnd);
    const autoFollowStart = applyScrollStateSource.indexOf('        if (shouldAuto)');
    const autoFollowEnd = applyScrollStateSource.indexOf('        if (state.userInterrupted)', autoFollowStart);
    const autoFollowSource = applyScrollStateSource.slice(autoFollowStart, autoFollowEnd);

    assert.match(source, /function inferCanvasContentType\(\{ explicitType = '', currentType = '', fileName = '', content = '' \} = \{\}\)/);
    assert.match(source, /<html\\b\|<head\\b\|<body\\b\|<main\\b/);
    assert.match(source, /function scheduleHtmlStreamingRender\(draft\)/);
    assert.match(source, /renderDraft\(state\.pendingHtmlRenderDraft\);[\s\S]*setTimeout\(\(\) => \{/);
    assert.doesNotMatch(source, /setTimeout\(\(\) => \{\s*renderDraft\(updated\);\s*\}, RENDER_DEBOUNCE_MS\)/);
    assert.match(autoFollowSource, /codeView\.scrollTop = Math\.max\(maxCodeTop, 0\)/);
    assert.doesNotMatch(
        autoFollowSource,
        /(?:previewTrack|codeView)\.scrollLeft\s*=/,
        'HTML stream following must update only the vertical code position',
    );
});

test('html canvas previews preserve active source behind the isolated proxy', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const modalRegistry = readFrontendSource(DELETE_WARNING_MODALS_PATH, 'utf8');

    assert.match(source, /const HTML_PREVIEW_CSP = "default-src 'none'; img-src 'self' data: blob:/);
    assert.match(source, /const OMLORIX_FILE_URL_PATTERN = \/omlorix-file:\\\/\\\//);
    assert.match(source, /function rewriteCanvasHtmlPreviewHtml\([\s\S]*providedCanvasFileId/);
    assert.match(source, /providedCanvasFileId[\s\S]*activeDraft\?\.fileId[\s\S]*activeDraftKey/);
    assert.match(source, /buildFileDownloadUrl\(fileId, \{ inline: true \}\)/);
    assert.match(source, /\/api\/v1\/files\/canvas\/assets\/content/);
    assert.match(source, /canvas_file_id: normalizedCanvasId/);
    assert.match(source, /asset_file_id: normalizedAssetId/);
    assert.match(source, /HTML_FILE_REFERENCE_ATTRS = \['href', 'src', 'poster', 'data', 'xlink:href'\]/);
    assert.match(source, /doc\.querySelectorAll\('script'\)\.forEach\(\(script\) => script\.remove\(\)\)/);
    assert.match(source, /function prepareInteractiveHtmlPreviewSource\(htmlContent, providedCanvasFileId = ''\)/);
    assert.match(source, /resolvedCanvasFileId = String\(resolvedDraft\?\.fileId \|\| resolvedDraftKey \|\| ''\)/);
    assert.match(source, /runtime\.render\(iframe, prepareInteractiveHtmlPreviewSource\(htmlContent \|\| '', resolvedCanvasFileId\)/);
    assert.match(source, /allowScripts: permissions\.allowScripts && allowExternalContent/);
    assert.match(source, /const allowExternalContent = permissions\.allowExternalContent\s*&& externalResources\.every/);
    assert.match(source, /id="canvas-html-SettingsBtn"[^>]*aria-haspopup="dialog"/);
    assert.match(source, /id="canvas-html-SettingsMenu" role="dialog"/);
    assert.match(source, /class="toggle-input" id="canvas-html-ScriptsBtn" type="checkbox" role="switch" disabled/);
    assert.match(source, /class="toggle-input" id="canvas-html-ExternalContentBtn" type="checkbox" role="switch"/);
    assert.match(source, /scriptsUnavailable = isUnavailable \|\| !permissions\.allowExternalContent/);
    assert.match(source, /if \(!enabled\) permissions\.allowScripts = false/);
    assert.match(source, /addEventListener\('change', \(event\) => \{[\s\S]*setActiveHtmlPermission\('allowScripts', event\.currentTarget\.checked\)/);
    assert.match(source, /scheduleHtmlExternalResourcePrompt\(resolvedDraftKey, htmlContent\)/);
    assert.match(source, /runtime\.collectExternalResources/);
    assert.match(source, /reviewedExternalResources: new Set\(\)/);
    assert.match(source, /resolveHtmlExternalResourceConsent\(true\)/);
    assert.match(source, /resolveHtmlExternalResourceConsent\(false\)/);
    assert.match(modalRegistry, /id: 'canvas-html-ExternalResourcesOverlay'/);
    assert.match(modalRegistry, /id="canvas-html-ExternalResourcesList"/);
    assert.match(modalRegistry, /canvas_html_external_prompt_allow/);
    assert.match(modalRegistry, /canvas_html_external_prompt_deny/);
    assert.match(source, /function setHtmlSettingsMenuOpen\(isOpen/);
    assert.match(source, /htmlSettingsMenu\?\.addEventListener\('keydown'/);
    assert.match(source, /iframe\.srcdoc = withIframeSecurityGuard\(''\)/);
    assert.match(source, /const needsExternalDecision = externalResources\s*\.some\(\(url\) => !permissions\.reviewedExternalResources\.has\(url\)\)/);
    assert.match(source, /iframe\.dataset\.canvasHtmlExternalConsent = 'pending'/);
    assert.match(source, /runtime\.render\(iframe, '', \{/);
    assert.match(source, /No authored HTML was mounted while the decision was pending/);
    assert.doesNotMatch(source, /const IFRAME_SECURITY_GUARD = `<script>/);
});

test('html canvas preview sanitizer strips redirect and base URL primitives', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const sanitizerStart = source.indexOf('function sanitizeHtmlPreviewDocument(doc)');
    assert.notEqual(sanitizerStart, -1);
    const serializerStart = source.indexOf('function serializeHtmlPreviewDocument(doc)', sanitizerStart);
    assert.notEqual(serializerStart, -1);
    const sanitizerSource = source.slice(sanitizerStart, serializerStart);

    assert.match(sanitizerSource, /httpEquiv === 'refresh'/);
    assert.match(sanitizerSource, /doc\.querySelectorAll\('base'\)\.forEach\(\(base\) => base\.remove\(\)\)/);
});

test('html canvas fragment links scroll inside srcdoc instead of navigating to Omlorix', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const handlerStart = source.indexOf("const HTML_PREVIEW_SRCDOC_URL = 'about:srcdoc'");
    assert.notEqual(handlerStart, -1);
    const binderStart = source.indexOf('function bindIframePreviewNavigation(', handlerStart);
    assert.notEqual(binderStart, -1);
    const handlerSource = source.slice(handlerStart, binderStart);

    assert.match(handlerSource, /normalizedHref\.startsWith\('#'\)/);
    assert.match(handlerSource, /HTML_PREVIEW_SRCDOC_URL = 'about:srcdoc'/);
    assert.match(handlerSource, /normalizedHref\.toLowerCase\(\)\.startsWith\(`\$\{HTML_PREVIEW_SRCDOC_URL\}#`\)/);
    assert.match(handlerSource, /event\.preventDefault\(\)/);
    assert.match(handlerSource, /frameDocument\.getElementById\(fragment\)/);
    assert.match(handlerSource, /frameDocument\.getElementsByName\(fragment\)\[0\]/);
    assert.match(handlerSource, /target\?\.scrollIntoView\(\{ block: 'start' \}\)/);
    assert.match(source, /frameDocument\.addEventListener\('click',[\s\S]*handleIframeFragmentNavigation\(event, frameDocument, frameWindow\)/);
    assert.match(source, /function rewriteCanvasHtmlPreviewHtml\([\s\S]*providedCanvasFileId/);
    assert.match(source, /anchor\.setAttribute\('href', `\$\{HTML_PREVIEW_SRCDOC_URL\}\$\{href\}`\)/);
    assert.match(source, /rewriteCanvasHtmlPreviewHtml\(htmlContent, \{ forSrcdoc: true, canvasFileId \}\)/);
});

test('canvas widget resolves default filenames by content type', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');

    assert.match(source, /const DEFAULT_NAMES_BY_TYPE = \{ markdown: 'canvas\.md', mermaid: 'diagram\.mmd', csv: 'data\.csv', tsv: 'data\.tsv', xlsx: 'workbook\.xlsx', xls: 'workbook\.xls', html: 'website\.html', latex: 'document\.tex', pdf: 'document\.pdf' \}/);
    assert.match(source, /function resolveDisplayCanvasFileName\(fileName, contentType\)/);
    assert.doesNotMatch(source, /const defaultNames = \{ markdown: 'canvas\.md', mermaid: 'diagram\.mmd', csv: 'data\.csv', html: 'website\.html' \}/);
});

test('canvas and source editors can add marked selections as editable references', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const chatBox = readFrontendSource(CHAT_BOX_PATH, 'utf8');
    const markup = readFrontendSource(INDEX_HTML_PATH, 'utf8');
    const markdownEditor = readFrontendSource(MARKDOWN_EDITOR_PATH, 'utf8');
    const markdownEditorCss = readFrontendSource(MARKDOWN_EDITOR_CSS_PATH, 'utf8');
    const canvasWidgetCss = readFrontendSource(CANVAS_WIDGET_CSS_PATH, 'utf8');

    assert.doesNotMatch(markup, /id="canvas-markdown-ReferenceBtn"/);
    assert.match(source, /function readCurrentArtifactSelection\(\)/);
    assert.match(source, /function buildArtifactReferenceText\(selection\)/);
    assert.match(source, /function positionReferenceToolbar\(selection\)/);
    assert.match(source, /function getVisibleTextareaRangeRect\(element, start, end\)/);
    assert.match(source, /const centeredLeft = rect\.left \+ rect\.width \/ 2 - toolbarRect\.width \/ 2/);
    assert.match(source, /previewTrack\.addEventListener\('scroll', \(\) => \{\s*if \(!state\.suppressUserScrollEvents\) hideReferenceToolbar\(\);/);
    assert.match(source, /canvas-reference-floating-toolbar/);
    assert.match(source, /window\.createSelectionActionTooltip\(\{/);
    assert.match(source, /copyLabel:\s*t\('chat_selection_copy_label'/);
    assert.doesNotMatch(source, /bindIframePreviewNavigation\(previewPane\)/);
    assert.match(source, /window\.addReferencePart\(referenceText\)/);
    assert.match(source, /file_id: \$\{selection\.sourceFileId\}/);
    assert.doesNotMatch(source, /Edit guidance: use the latex_pdf tool/);
    assert.match(source, /file_id: \$\{selection\.fileId\}/);
    assert.match(source, /start_snippet and end_snippet/);
    assert.match(source, /canvas_add_selection_reference_label/);
    assert.doesNotMatch(source, /canvas_reference_edit_prompt/);
    assert.doesNotMatch(source, /seedComposerForArtifactEdit/);
    assert.match(source, /onReferenceSelection:.*=>.*addMarkedSelectionAsReference\(/);
    assert.match(chatBox, /function createSelectionActionTooltip\(/);
    assert.match(chatBox, /data-action="copy"/);
    assert.match(chatBox, /data-action="add-reference"/);
    assert.match(chatBox, /window\.createSelectionActionTooltip = createSelectionActionTooltip/);
    assert.match(markdownEditor, /window\.createSelectionActionTooltip\(\{/);
    assert.match(markdownEditor, /function readReferenceSelectionData\(\)/);
    assert.match(markdownEditor, /sourceCodeMirror\.on\('cursorActivity'/);
    assert.match(markdownEditor, /canvas_add_selection_reference_label/);
    assert.match(markdownEditor, /options\.onReferenceSelection/);
    assert.match(markdownEditor, /const centeredLeft = rect\.left - shellRect\.left \+ \(rect\.width \/ 2\) - \(toolbarWidth \/ 2\)/);
    assert.match(markdownEditor, /addListener\(editorView, 'scroll', \(\) => \{\s*hideReferenceToolbar\(\);/);
    assert.match(chatBox, /function extractMarkedReferenceText\(text\)/);
    assert.match(['Marked text:', '```', 'selected artifact text', '```'].join('\n'), /Marked text:\s*\n```/);
    assert.match(markdownEditorCss, /\.canvas-md-reference-toolbar\.selection-tooltip/);
    assert.match(canvasWidgetCss, /\.canvas-reference-floating-toolbar\.selection-tooltip/);
    assert.doesNotMatch(markdownEditorCss, /--canvas-md-reference-btn-bg/);
    assert.doesNotMatch(canvasWidgetCss, /--canvas-reference-btn-bg/);
});

test('canvas selection references require a visible adjacent chat composer', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const markdownEditor = readFrontendSource(MARKDOWN_EDITOR_PATH, 'utf8');

    // Workspace file previews reuse Canvas while the global chat callback and
    // composer DOM still exist. The live layout, rather than API presence,
    // must control whether a selection can become a chat reference.
    assert.match(source, /function hasAdjacentChatComposer\(\)/);
    assert.match(source, /document\.body\?\.classList\?\.contains\('workspace-view-active'\)/);
    assert.match(source, /const chatContainer = document\.getElementById\('chatContainer'\)/);
    assert.match(source, /const chatComposer = document\.getElementById\('chatBoxArea'\)/);
    assert.match(source, /function positionReferenceToolbar\(selection\) \{\s*if \(!hasAdjacentChatComposer\(\)\)/);
    assert.match(source, /function setReferenceToolbarState\(selection\)[\s\S]*?if \(!hasAdjacentChatComposer\(\)\) \{[\s\S]*?hideReferenceToolbar\(\);\s*return;/);
    assert.match(source, /function addMarkedSelectionAsReference\(selectionData = null\) \{\s*[\s\S]*?if \(!hasAdjacentChatComposer\(\)\) \{\s*hideReferenceToolbar\(\);\s*return false;/);

    // Markdown owns an inner selection toolbar, so Canvas passes the same live
    // capability into that editor instead of fixing only PDF/source previews.
    assert.match(source, /canReferenceSelection: hasAdjacentChatComposer/);
    assert.match(markdownEditor, /typeof options\.canReferenceSelection !== 'function'[\s\S]*options\.canReferenceSelection\(\)/);
    assert.match(markdownEditor, /!editable \|\| typeof options\.onReferenceSelection !== 'function' \|\| !canReferenceSelection/);
});

test('rendered PDF selections expose reference actions while rendered HTML omits them', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const css = readFrontendSource(CANVAS_WIDGET_CSS_PATH, 'utf8');

    // The app-owned PDF text layer supplies both native selection and the
    // shared Canvas Copy / Add reference tooltip, including page metadata.
    assert.match(source, /function renderSelectablePdfPreviewInto\(viewer, pdfFileId\)/);
    assert.match(source, /buildPdfPreviewEndpoint\('\/page-image', fileId, pageNumber\)/);
    assert.match(source, /renderSelectablePdfTextLayer\(surface, pageData\)/);
    assert.match(source, /className = 'canvas-pdf-text-word'/);
    assert.match(source, /function getPdfSelectionPages\(range\)/);
    assert.match(source, /range\.intersectsNode\(textLayer\)/);
    assert.match(source, /source: isRenderedPdf \? 'pdf_preview' : 'preview'/);
    assert.match(source, /selectionData\.pageStart = selectedPages\[0\]/);
    assert.match(source, /selectionData\.pageEnd = selectedPages\[selectedPages\.length - 1\]/);
    assert.doesNotMatch(source, /if \(normalizedContentType === 'pdf' \|\| normalizedContentType === 'latex'\) return null;/);
    assert.match(css, /\.canvas-pdf-text-word\s*\{[^}]*user-select: text;/s);
    assert.match(css, /\.canvas-pdf-text-word\s*\{[^}]*-webkit-text-fill-color: transparent;/s);
    assert.match(css, /\.canvas-pdf-text-word::selection\s*\{[^}]*color: transparent;[^}]*-webkit-text-fill-color: transparent;/s);

    // Rendered HTML binds only its safe fragment navigation. It does not
    // forward iframe selection events to the host reference toolbar.
    const iframeBinderStart = source.indexOf('function bindIframePreviewNavigation(iframe)');
    assert.notEqual(iframeBinderStart, -1);
    const iframeBinderEnd = source.indexOf('\n    function renderContentPreview(', iframeBinderStart);
    assert.notEqual(iframeBinderEnd, -1);
    const iframeBinderSource = source.slice(iframeBinderStart, iframeBinderEnd);
    assert.match(iframeBinderSource, /handleIframeFragmentNavigation/);
    assert.doesNotMatch(iframeBinderSource, /selectionchange|refreshReferenceSelectionState/);
    assert.doesNotMatch(source, /function getSelectionFromActiveIframe\(/);
    assert.match(source, /function setHtmlViewMode\(mode\) \{[\s\S]*?hideReferenceToolbar\(\);/);

    // Read-only HTML source remains focusable/selectable and every source
    // editor, not just persistable drafts, binds selection refresh events.
    assert.match(source, /editor\.readOnly = !editable;[\s\S]*editor\.disabled = false;/);
    assert.match(source, /source: contentType === 'latex'[\s\S]*contentType === 'html' \? 'html_source'/);
    assert.match(source, /previewTrack\?\.querySelector\('\.canvas-html-preview-wrapper\.code-view \.canvas-raw-editor'\)/);
    assert.match(source, /\['select', 'keyup', 'mouseup', 'pointerup', 'touchend'\]\.forEach/);
    assert.match(source, /Source lines: \$\{selection\.startLine\}-\$\{selection\.endLine\}/);
});

test('saved canvas widgets prefer original filenames when transcripts are reloaded', () => {
    const source = readFrontendSource(CHAT_TRANSCRIPT_RENDERER_PATH, 'utf8');

    assert.match(
        source,
        /const fileName =\s*fileMeta\?\.original_filename \|\| fileMeta\?\.original_name \|\| file\?\.original_filename \|\| file\?\.original_name \|\| file\?\.file_name \|\| '';/,
    );
});

test('canvas share translations exist in every supported locale', () => {
    const locales = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    locales.forEach((locale) => {
        const dictionary = JSON.parse(
            readFrontendSource(path.join(I18N_ROOT, locale, 'index.json'), 'utf8'),
        );

        [...CANVAS_PREVIEW_KEYS, ...CANVAS_SHARE_KEYS].forEach((key) => {
            assert.ok(
                Object.prototype.hasOwnProperty.call(dictionary, key),
                `${locale} is missing ${key}`,
            );
        });
    });
});
