const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const { readSendMessageSource } = require('./sending/source.cjs');

const ROOT = path.resolve(__dirname, '../..');

function assertSourceMarkers(markers) {
    for (const [name, index] of Object.entries(markers)) {
        assert.notEqual(index, -1, `Missing expected source marker: ${name}`);
    }
}

test('slide presentation sidebar reuses Canvas panel primitives', () => {
    const index = readFrontendSource(path.join(ROOT, 'index.html'), 'utf8');
    const widget = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-widget.js'), 'utf8');
    const slideCss = readFrontendSource(path.join(ROOT, 'css/chat/slide-presentation-widget.css'), 'utf8');

    assert.match(index, /class="slide-presentation-preview-panel canvas-markdown-preview-panel"/);
    assert.match(index, /slide-presentation-preview-panel-header canvas-markdown-preview-header/);
    assert.match(index, /slide-presentation-preview-panel-info canvas-markdown-preview-title-wrap/);
    assert.match(index, /slide-presentation-preview-panel-workspace canvas-markdown-preview-workspace/);
    assert.match(index, /slide-presentation-preview-resizer canvas-markdown-preview-resizer/);
    assert.match(index, /id="slide-presentation-PreviewResizer"[\s\S]*role="separator"/);
    assert.match(index, /slide_presentation_resize_preview_aria/);
    assert.match(widget, /setPreviewWidthFromPointerX/);
    assert.match(widget, /setPreviewWidthFromPixels/);
    assert.match(widget, /applyPreviewWidthRatio/);
    assert.match(widget, /canvas-markdown-preview-resizing/);
    assert.match(slideCss, /@container canvas-preview \(max-width: 600px\)\s*\{\s*\.slide-presentation-preview-btn-label\s*\{\s*display: none;/);
    assert.doesNotMatch(slideCss, /@media \(max-width: 1199px\)\s*\{\s*\.slide-presentation-preview-btn-label/);
    assert.doesNotMatch(slideCss, /\.slide-presentation-preview-panel\s*\{/);
    assert.doesNotMatch(slideCss, /\.slide-presentation-preview-panel-header\s*\{/);
    assert.doesNotMatch(slideCss, /\.slide-presentation-preview-panel-workspace\s*\{/);
});

test('slide presentation scrolling stays local and keeps one accessible selection', () => {
    const widget = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-widget.js'), 'utf8');
    const slideCss = readFrontendSource(path.join(ROOT, 'css/chat/slide-presentation-widget.css'), 'utf8');
    const animationsCss = readFrontendSource(path.join(ROOT, 'css/common/animations.css'), 'utf8');

    assert.doesNotMatch(widget, /\.scrollIntoView\s*\(/);
    assert.match(widget, /previewSlidesTrack\.scrollTo\(\{ top: targetTop, behavior: resolvedBehavior \}\)/);
    assert.match(widget, /item\.classList\.toggle\('active', idx === slidePresentationCurrentIndex\)/);
    assert.match(widget, /thumb\.setAttribute\('aria-current', 'true'\)/);
    assert.match(widget, /document\.createElement\('button'\)/);
    assert.match(widget, /_previewAutoFollowGeneration = false/);
    assert.match(widget, /addEventListener\('wheel', _handleManualPreviewScrollIntent/);
    assert.match(widget, /_SLIDE_SELECTION_HYSTERESIS_RATIO/);
    assert.match(widget, /currentDistance - closestDistance >= hysteresisPx/);
    assert.doesNotMatch(widget, /_playPreviewSlideSwitchAnimation/);
    assert.doesNotMatch(widget, /entry\.target\.classList\.toggle\('active'/);
    assert.match(slideCss, /\.slide-presentation-preview-slides-track\s*\{[\s\S]*overscroll-behavior: contain/);
    assert.match(slideCss, /\.slide-presentation-preview-slides-track\s*\{[\s\S]*scrollbar-gutter: stable/);
    assert.match(slideCss, /\.slide-presentation-preview-slide-item\s*\{[\s\S]*opacity 160ms ease-out/);
    assert.doesNotMatch(slideCss, /\.slide-presentation-preview-slide-item\.switching-in/);
    assert.doesNotMatch(animationsCss, /@keyframes slide-presentation-preview-crossfade-in/);
});

test('slide presentation result states reuse the Canvas file card', () => {
    const widget = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-widget.js'), 'utf8');
    const slideCss = readFrontendSource(path.join(ROOT, 'css/chat/slide-presentation-widget.css'), 'utf8');

    assert.match(widget, /slide-presentation-completion-card canvas-markdown-result-widget/);
    assert.match(widget, /slide-presentation-completion-header canvas-markdown-result-header/);
    assert.match(widget, /slide-presentation-completion-icon canvas-markdown-result-icon/);
    assert.match(widget, /slide-presentation-completion-info canvas-markdown-result-meta/);
    assert.match(widget, /slide-presentation-completion-title canvas-markdown-result-title/);
    assert.match(widget, /slide-presentation-completion-sub canvas-markdown-result-sub/);
    assert.match(widget, /slide-presentation-completion-view-btn canvas-markdown-result-open-btn/);
    assert.match(widget, /canvas-markdown-result-open-label/);
    assert.match(widget, /wrapper\.className = 'assistant-widget'/);
    assert.match(widget, /card\.dataset\.presentationStatus === 'generating'/);
    assert.match(widget, /btn\.setAttribute\('aria-pressed', shouldHide \? 'true' : 'false'\)/);

    // Canvas owns the card layout, and failed transient cards are removed.
    assert.doesNotMatch(slideCss, /\.slide-presentation-completion-card\s*\{/);
    assert.doesNotMatch(slideCss, /\.slide-presentation-completion-view-btn\s*\{/);
    assert.doesNotMatch(slideCss, /\.slide-presentation-completion-card\.error/);
    assert.match(widget, /function _removeGeneratingCard\(messageId\)/);
    assert.match(widget, /card\.closest\('\.assistant-widget\[data-widget-type="slide_presentation_result"\]'\)/);
    assert.match(widget, /\(wrapper \|\| card\)\.remove\(\)/);
    assert.match(widget, /function _discardFailedGenerationPreview\(\)/);
    assert.match(widget, /_discardFailedGenerationPreview\(\);[\s\S]*_removeGeneratingCard\(messageId\)/);
    assert.match(widget, /function handleStreamEnd\(messageId\)/);
    assert.match(widget, /function _isActiveGenerationForMessage\(messageId\)/);
    assert.match(widget, /handleStreamEnd: handleStreamEnd/);
    assert.doesNotMatch(widget, /_setCompletionCardError/);
});

test('slide presentation failures close live previews for sends and regenerations', () => {
    const sendMessage = readSendMessageSource();
    const splitScreen = readFrontendSource(path.join(ROOT, 'js/chat/splitScreen.js'), 'utf8');
    const cleanupCalls = sendMessage.match(/slidePresentationWidget\.handleStreamEnd\((?:messageId|targetMessageId)\)/g) || [];

    assert.ok(cleanupCalls.length >= 2);
    assert.match(sendMessage, /slidePresentationWidget\.handleStreamEnd\(messageId\)/);
    assert.match(sendMessage, /slidePresentationWidget\.handleStreamEnd\(targetMessageId\)/);
    assert.match(sendMessage, /slidePresentationWidget\.handleStreamEnd\(newMessageId \|\| originalMessageId\)/);
    assert.match(splitScreen, /slidePresentationWidget\.handleStreamEnd\(streamedMessageId\)/);
    assert.match(splitScreen, /slidePresentationWidget\.handleStreamEnd\(messageId\)/);
});

test('chat navigation resets detached live-presentation state before reattachment', () => {
    const chats = readFrontendSource(path.join(ROOT, 'js/chat/chats.js'), 'utf8');
    const widget = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-widget.js'), 'utf8');
    const resetStart = widget.indexOf('function reset() {');
    const resetEnd = widget.indexOf('function _setPanelVisible(', resetStart);
    assertSourceMarkers({ resetStart, resetEnd });
    const resetSource = widget.slice(resetStart, resetEnd);

    assert.match(chats, /slidePresentationWidget\.reset\(\)/);
    assert.match(resetSource, /slidePresentationImageLoadToken \+= 1/);
    assert.match(resetSource, /_removeGeneratingCard\(_activeMessageId\)/);
    assert.match(resetSource, /_finishGenerationTracking\(\)/);
    assert.match(widget, /reset: reset/);
});

test('presentation completion never upgrades a detached generation card', () => {
    const widget = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-widget.js'), 'utf8');
    const helperStart = widget.indexOf("function _getConnectedGeneratingCard(messageId = '')");
    const helperEnd = widget.indexOf('function _setAssistantMessageListVisible(', helperStart);
    assertSourceMarkers({ helperStart, helperEnd });
    const helperSource = widget.slice(helperStart, helperEnd);
    const mismatchStart = helperSource.indexOf('if (belongsToAnotherMessage)');
    const disconnectedStart = helperSource.indexOf('if (!_generatingCard.isConnected)', mismatchStart);
    assertSourceMarkers({ mismatchStart, disconnectedStart });
    const mismatchSource = helperSource.slice(mismatchStart, disconnectedStart);

    assert.match(widget, /function _getConnectedGeneratingCard\(messageId = ''\)/);
    assert.match(mismatchSource, /if \(belongsToAnotherMessage\) return null;/);
    assert.doesNotMatch(mismatchSource, /_generatingCard = null|_activeMessageId = null/);
    assert.match(helperSource, /if \(!_generatingCard\.isConnected\) \{[\s\S]*_generatingCard = null;[\s\S]*_activeMessageId = null;/);
    assert.match(widget, /const generatingCard = _getConnectedGeneratingCard\(messageId\)/);
    assert.match(widget, /if \(generatingCard\)[\s\S]*const card = generatingCard/);
    assert.match(widget, /if \(!_getConnectedGeneratingCard\(messageId\)\) \{[\s\S]*_addGeneratingCard\(messageId/);
});

test('slide presentation terminal card states are translated in every locale', () => {
    const locales = ['ar', 'de', 'en', 'es', 'fr', 'hi', 'it', 'ja', 'pt', 'ru', 'zh'];
    for (const locale of locales) {
        const translations = JSON.parse(
            readFrontendSource(path.join(ROOT, `i18n/${locale}/index.json`), 'utf8'),
        );
        assert.ok(translations.slide_presentation_generation_failed, `${locale} generation failure`);
        assert.ok(translations.slide_presentation_rendering_failed, `${locale} rendering failure`);
        assert.ok(translations.slide_presentation_resize_preview_aria, `${locale} resize preview label`);
        assert.ok(translations.slide_presentation_slide_number, `${locale} slide number label`);
        assert.ok(translations.slide_presentation_complete, `${locale} complete label`);
        assert.ok(translations.slide_presentation_reviewing, `${locale} review label`);
        assert.ok(translations.slide_presentation_rendering, `${locale} render label`);
        assert.ok(translations.slide_presentation_generating_slide, `${locale} generation progress`);
        assert.ok(translations.slide_presentation_go_to_slide, `${locale} slideshow dot label`);
        assert.ok(translations.slide_presentation_slideshow_dialog_aria, `${locale} slideshow dialog label`);
        assert.ok(translations.slide_presentation_editor_slide_limit, `${locale} slide limit`);
        assert.ok(translations.slide_presentation_draft_ready_rendering, `${locale} draft render status`);
        assert.ok(translations.slide_presentation_polishing_warning, `${locale} polishing warning`);
    }
});

test('failed editor close dialog uses localized authenticated-page copy', () => {
    const editor = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-editor.js'), 'utf8');
    const localeRoot = path.join(ROOT, 'i18n');
    const locales = fs.readdirSync(localeRoot, { withFileTypes: true })
        .filter(entry => entry.isDirectory())
        .map(entry => entry.name);
    const keys = [
        'modal_discard_changes_title',
        'modal_discard_changes_desc',
        'modal_discard_btn',
    ];
    const english = JSON.parse(
        readFrontendSource(path.join(localeRoot, 'en/index.json'), 'utf8'),
    );

    for (const key of keys) {
        assert.match(editor, new RegExp(`tr\\('${key}'`), `editor uses ${key}`);
    }

    for (const locale of locales) {
        const translations = JSON.parse(
            readFrontendSource(path.join(localeRoot, locale, 'index.json'), 'utf8'),
        );
        for (const key of keys) {
            assert.ok(translations[key], `${locale}/index.json provides ${key}`);
            if (locale !== 'en') {
                assert.notEqual(translations[key], english[key], `${locale} localizes ${key}`);
            }
        }
    }
});

test('slide presentation generation progressively upgrades live HTML to rendered revisions', () => {
    const index = readFrontendSource(path.join(ROOT, 'index.html'), 'utf8');
    const widget = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-widget.js'), 'utf8');
    const slideCss = readFrontendSource(path.join(ROOT, 'css/chat/slide-presentation-widget.css'), 'utf8');

    assert.match(widget, /case 'html_delta':[\s\S]*_queueHtmlDelta\(data\.delta \|\| ''\)/);
    assert.match(widget, /case 'draft_complete'/);
    assert.match(widget, /case 'revision_ready'/);
    assert.match(widget, /\{ draft: true \}/);
    assert.match(widget, /const endpointCollection = options\.draft \? 'draft-slides' : 'slides'/);
    assert.match(widget, /revision > slidePresentationRenderedRevision/);
    assert.doesNotMatch(widget, /revision >= slidePresentationRenderedRevision/);
    assert.match(widget, /first-pass draft writes directly into the live image[\s\S]*return 404[\s\S]*setTimeout/);
    assert.match(widget, /requestAnimationFrame\(\(\) => \{[\s\S]*appendHtmlDelta\(pending\)/);
    assert.match(widget, /existingImg\.src = imgUrl/);
    assert.match(widget, /previousRevokers\.forEach\(revoke => revoke\(\)\)/);
    assert.match(index, /id="slide-presentation-PreviewStatus" role="status" aria-live="polite"/);
    assert.match(index, /id="slide-presentation-PreviewMain" aria-busy="false"/);
    assert.doesNotMatch(index, /slide-presentation-gen-steps/);
    assert.match(slideCss, /\.slide-presentation-preview-slide-item\.revision-updated/);
    assert.match(slideCss, /\.slide-presentation-preview-slide-item img \{[\s\S]*?width: 100%;[\s\S]*?height: 100%;[\s\S]*?transform: none;/);
    assert.doesNotMatch(widget, /\bimg\.style\.transform\s*=/);
    assert.match(slideCss, /prefers-reduced-motion: reduce[\s\S]*\.slide-presentation-gen-icon/);
});

test('native full-site editor uses revisioned save and render APIs', () => {
    const index = readFrontendSource(path.join(ROOT, 'index.html'), 'utf8');
    const widget = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-widget.js'), 'utf8');
    const editor = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-editor.js'), 'utf8');
    const dockerfile = readFrontendSource(path.join(ROOT, 'Dockerfile'), 'utf8');
    const cacheBuster = readFrontendSource(path.resolve(ROOT, '../script/cache_buster.py'), 'utf8');

    assert.match(index, /id="slide-presentation-PreviewEdit"[\s\S]*slide_presentation_edit_fullscreen_aria/);
    assert.match(index, /id="slide-presentation-EditorOverlay"[\s\S]*aria-modal="true"/);
    assert.match(index, /id="slide-presentation-EditorFallbackClose"/);
    assert.match(index, /id="slide-presentation-EditorHost"/);
    assert.doesNotMatch(index, /id="slide-presentation-EditorFrame"/);
    assert.doesNotMatch(index, /slide-presentation-editor-frame/);
    assert.deepEqual(
        Array.from(
            index.matchAll(/<link rel="stylesheet" href="([^"]+)" data-slide-presentation-editor-stylesheet>/g),
            match => match[1],
        ),
        [
            '/css/common/animations.css',
            '/css/common/elementsNew.css',
            '/css/common/searchModal.css',
        ],
    );
    assert.match(editor, /querySelectorAll\('link\[data-slide-presentation-editor-stylesheet\]'\)/);
    assert.match(editor, /sourceStylesheet\.cloneNode\(false\)/);
    assert.match(editor, /root\.prepend\(\.\.\.sharedStylesheets\)/);
    assert.doesNotMatch(
        editor,
        /href="\/css\/common\/(?:animations|elementsNew|searchModal)\.css"/,
    );
    assert.match(widget, /slidePresentationNativeEditor/);
    assert.match(widget, /method: 'PUT'/);
    assert.match(widget, /\/editor\/render/);
    assert.doesNotMatch(widget, /postMessage/);
    assert.doesNotMatch(widget, /addEventListener\('message'/);
    assert.doesNotMatch(widget, /editorOverlay\.requestFullscreen/);
    assert.doesNotMatch(widget, /fullscreenElement === editorOverlay/);
    assert.match(editor, /attachShadow\(\{ mode: 'open' \}\)/);
    assert.match(editor, /window\.slidePresentationNativeEditor = Object\.freeze/);
    assert.match(editor, /expected_revision/);
    assert.match(editor, /function markServerDirty\(\)/);
    assert.match(editor, /setTimeout\(\(\) => flushServerSave\(\), 900\)/);
    assert.match(editor, /renderRevision >= server\.revision/);
    assert.match(editor, /sessionId: 0/);
    assert.match(editor, /const sessionId = server\.sessionId/);
    assert.match(editor, /if \(sessionId !== server\.sessionId\) return false/);
    assert.doesNotMatch(editor, /await activeRender\.catch\(\(\) => false\)/);
    assert.match(editor, /const requestedRevision = server\.revision/);
    assert.match(editor, /error\.status === 409 && \(server\.dirty \|\| server\.revision > requestedRevision\)/);
    assert.match(editor, /classes\.add\('slide'\)/);
    assert.match(
        editor,
        /function insertTemplate\(key\)[\s\S]*!el\.getAttribute\('data-slide-title'\)[\s\S]*el\.setAttribute\([\s\S]*'data-slide-title'/,
    );
    assert.match(editor, /server\.sessionId \+= 1/);
    assert.match(editor, /ownerDocument\.addEventListener\('i18n:updated', localizeEmbeddedChrome\)/);
    assert.match(editor, /function templateHtml\(\)/);
    assert.match(
        editor,
        /function openNativeEditor\(options = \{\}\) \{[\s\S]*localizeEmbeddedChrome\(\);[\s\S]*editorController = \{/,
    );
    assert.doesNotMatch(editor, /sandbox="allow-same-origin"/);
    assert.match(editor, /default-src 'none'/);
    assert.doesNotMatch(editor, /allow-same-origin allow-scripts/);
    assert.match(editor, /\$\$\('script, noscript, iframe, frame, object, embed', clone\)/);
    assert.match(widget, /function _sanitizeSlideFrameHtml\(bodyHtml\)/);
    assert.match(widget, /_fetchSlideImageWithRetry\(endpoint, loadToken\)/);
    assert.match(widget, /slidePresentationSlideImages = nextImageUrls/);
    assert.match(widget, /!element\.contains\(editorOverlay\)/);
    assert.doesNotMatch(dockerfile, /slide_presentation_demo/);
    assert.doesNotMatch(cacheBuster, /slide_presentation_demo/);
    assert.doesNotMatch(cacheBuster, /slide-presentation-editor\.html/);

    const locales = ['ar', 'de', 'en', 'es', 'fr', 'hi', 'it', 'ja', 'pt', 'ru', 'zh'];
    for (const locale of locales) {
        const translations = JSON.parse(
            readFrontendSource(path.join(ROOT, `i18n/${locale}/index.json`), 'utf8'),
        );
        assert.ok(translations.slide_presentation_edit_fullscreen_aria, `${locale} editor button`);
        assert.ok(translations.slide_presentation_editor_save_failed, `${locale} editor save error`);
        assert.ok(translations.slide_presentation_editor_unsaved, `${locale} editor dirty state`);
        assert.match(
            translations.slide_presentation_editor_hint_html,
            /\{keyboard_shortcut_ctrl\}/,
            `${locale} platform-aware editor hint`,
        );
        assert.match(
            translations.slide_presentation_editor_save_shortcut,
            /\{keyboard_shortcut_ctrl\}/,
            `${locale} platform-aware save shortcut`,
        );
        for (const key of [
            'slide_presentation_editor_deck_title',
            'slide_presentation_editor_undo',
            'slide_presentation_editor_redo',
            'slide_presentation_editor_zoom_out',
            'slide_presentation_editor_zoom_fit',
            'slide_presentation_editor_zoom_in',
            'slide_presentation_editor_slide_canvas',
            'slide_presentation_editor_font_size',
            'slide_presentation_editor_font_family',
            'slide_presentation_editor_vertical_align',
            'slide_presentation_editor_spacing',
            'slide_presentation_editor_lists',
            'slide_presentation_editor_layers',
            'slide_presentation_editor_template_blank_light',
            'slide_presentation_editor_template_blank_dark',
            'slide_presentation_editor_template_title_slide',
            'slide_presentation_editor_template_two_columns',
            'slide_presentation_editor_template_three_cards',
            'slide_presentation_editor_template_duplicate_current',
            'slide_presentation_editor_no_slide_sections_file',
            'slide_presentation_editor_requires_slide',
            'slide_presentation_editor_html_parse_failed',
            'slide_presentation_editor_no_slide_sections',
            'slide_presentation_editor_changes_applied',
            'slide_presentation_editor_template_section',
            'slide_presentation_editor_template_replace_content',
            'slide_presentation_editor_template_three_points',
            'slide_presentation_editor_template_describe_point',
        ]) {
            assert.ok(translations[key], `${locale} ${key}`);
        }
    }
});

test('closing the editor reconciles and atomically refreshes the latest preview revision', () => {
    const index = readFrontendSource(path.join(ROOT, 'index.html'), 'utf8');
    const widget = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-widget.js'), 'utf8');
    const editor = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-editor.js'), 'utf8');
    const slideCss = readFrontendSource(path.join(ROOT, 'css/chat/slide-presentation-widget.css'), 'utf8');
    const refreshStart = widget.indexOf('async function _refreshPreviewAfterEditorRender');
    const refreshEnd = widget.indexOf('// ── Shared Canvas split-panel sizing', refreshStart);
    assertSourceMarkers({ refreshStart, refreshEnd });
    const refreshSource = widget.slice(refreshStart, refreshEnd);

    assert.match(index, /id="slide-presentation-PreviewUpdating" role="status" aria-live="polite" aria-hidden="true" hidden/);
    assert.match(widget, /previewUpdating\?\.setAttribute\('aria-hidden', state === 'idle' \? 'true' : 'false'\)/);
    assert.match(widget, /previewUpdating\.hidden = state === 'idle'/);
    assert.match(index, /id="slide-presentation-PreviewUpdateRetry"[\s\S]*data-i18n="chat_load_retry"/);
    assert.match(editor, /renderRequestedRevision: 0/);
    assert.match(editor, /editorController\?\.onClose\?\.\(\{[\s\S]*canvasRevision: server\.revision/);
    assert.match(editor, /sourceChanged: server\.revision !== server\.openedRevision/);
    assert.match(editor, /renderPromise: server\.renderInFlight/);
    assert.doesNotMatch(editor, /Begin refreshing derivatives before closing/);
    assert.match(widget, /function _queueEditorClosePreviewRefresh\(presentationId, closeContext = \{\}\)/);
    assert.match(widget, /await Promise\.resolve\(closeContext\.renderPromise\)/);
    assert.match(widget, /\/editor`[\s\S]*render_revision/);
    assert.match(widget, /\/editor\/render`[\s\S]*expected_revision: canvasRevision/);
    assert.match(widget, /_isEditorPreviewRefreshCurrent\(refreshToken, presentationId\)/);
    assert.match(widget, /async function _preloadSlideImageUrls\(urls, loadToken\)/);
    assert.match(widget, /const _SLIDE_IMAGE_DECODE_TIMEOUT_MS = 15000/);
    assert.match(widget, /setTimeout\(\(\) => finish\(false\), _SLIDE_IMAGE_DECODE_TIMEOUT_MS\)/);
    assert.match(widget, /clearTimeout\(timeoutId\)/);
    assert.match(widget, /typeof image\.decode === 'function'/);
    assert.match(widget, /forceRebuild: requiresRebuild/);
    assert.match(widget, /preserveIndex: previousIndex/);
    assert.doesNotMatch(refreshSource, /_revokeSlideImages\(\)/);
    assert.doesNotMatch(refreshSource, /previewSlidesTrack\.innerHTML = ''/);
    assert.match(widget, /const refreshed = await _refreshPreviewAfterEditorRender\(renderPayload, refreshToken\);[\s\S]*if \(!refreshed\) return;/);
    assert.match(slideCss, /\.slide-presentation-preview-main\.is-updating[\s\S]*opacity: 0\.58/);
    assert.match(slideCss, /\.slide-presentation-preview-updating[\s\S]*position: absolute/);
    assert.match(slideCss, /\.slide-presentation-preview-updating[\s\S]*flex-direction: column/);
    assert.match(slideCss, /\.slide-presentation-preview-updating \.slide-presentation-preview-spinner[\s\S]*width: 30px/);
});

test('edited presentation cards resolve current server metadata before opening', () => {
    const widget = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-widget.js'), 'utf8');

    assert.match(widget, /function _refreshStoredPresentationContext\(options = \{\}\)/);
    assert.match(widget, /async function _resolveLatestPresentationContext\(options = \{\}\)/);
    assert.match(widget, /\/api\/v1\/presentations\/by-file\/\$\{encodeURIComponent\(lookupId\)\}/);
    assert.match(widget, /fileId: payload\.file_id \|\| fallback\.fileId/);
    assert.match(widget, /slideCount: payload\.slide_count \?\? fallback\.slideCount/);
    assert.match(widget, /_getCompletionCardContext\(card\) \|\| options/);
    assert.match(widget, /_fetchSlideCountAndLoad\(presentationId, 0\)/);
    assert.match(widget, /_refreshStoredPresentationContext\(\{ \.\.\.context, slideCount \}\)/);
    assert.ok(
        (widget.match(/_isEditorPreviewRefreshCurrent\(refreshToken, presentationId\)/g) || []).length >= 4,
        'late save, render, and error callbacks stay scoped to their original presentation',
    );
    assert.ok(
        (widget.match(/String\(slidePresentationPresentationId \|\| ''\) !== presentationId/g) || []).length >= 2,
        'preview refresh checks presentation ownership before and after image loading',
    );
});

test('native presentation editor delegates present and export and keeps status controls in the top bar', () => {
    const editor = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-editor.js'), 'utf8');
    const widget = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-widget.js'), 'utf8');

    assert.doesNotMatch(editor, /id="btnTheme"/);
    assert.doesNotMatch(editor, /deck-studio-theme/);
    assert.doesNotMatch(editor, /id="present"/);
    assert.doesNotMatch(editor, /class="logo"/);
    assert.match(editor, /<header id="topbar">[\s\S]*id="zoomOut"[\s\S]*id="zoomLabel"[\s\S]*id="zoomIn"[\s\S]*id="saveState"/);
    assert.doesNotMatch(editor, /id="statusbar"/);
    assert.match(editor, /const renderPromise = requestServerRender\(\);[\s\S]*editorController\.present\(\{[\s\S]*slideIndex: state\.active,[\s\S]*renderPromise/);
    assert.match(editor, /editorController\.export\(\{ format: \$\('#editorExportFormat'\)\.value \}\)/);
    assert.match(widget, /present: async \(\{ slideIndex, renderPromise \} = \{\}\) =>/);
    assert.match(widget, /closePresentationEditor\(\{ preserveNativeSession: true \}\);[\s\S]*openSlideshow\(\{ deferContent: true, slideIndex \}\)/);
    assert.match(widget, /const rendered = await Promise\.resolve\(renderPromise\)/);
    assert.match(widget, /if \(!rendered\) \{[\s\S]*closeSlideshow\(\);[\s\S]*return;[\s\S]*\}/);
    assert.match(widget, /_showSlideshowContent\(slideIndex\)/);
    assert.match(widget, /openSlideshow\(\{ deferContent: true, slideIndex \}\)/);
    assert.match(widget, /export: async \(\{ format \} = \{\}\) =>/);
    assert.match(widget, /await downloadPresentation\(format\)/);
    assert.equal((widget.match(/downloadBlobFromUrl\(/g) || []).length, 1);
});

test('presentation export waits for every saved edit and its newest rendered revision', () => {
    const editor = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-editor.js'), 'utf8');
    const widget = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-widget.js'), 'utf8');
    const renderStart = editor.indexOf('async function requestServerRender()');
    const saveStart = editor.indexOf('async function flushServerSave(', renderStart);
    const actionsStart = editor.indexOf('/* ---------------------------------------------------------------------\n   Shared presentation', saveStart);
    const exportStart = editor.indexOf('async function requestSharedExport()');
    const exportEnd = editor.indexOf("$('#btnPresent').addEventListener", exportStart);
    const refreshStateStart = widget.indexOf('function _setEditorPreviewRefreshState(');
    const refreshStateEnd = widget.indexOf('/**\n     * Batch token-level HTML updates', refreshStateStart);
    assertSourceMarkers({
        renderStart,
        saveStart,
        actionsStart,
        exportStart,
        exportEnd,
        refreshStateStart,
        refreshStateEnd,
    });
    const renderSource = editor.slice(renderStart, saveStart);
    const saveSource = editor.slice(saveStart, actionsStart);
    const exportSource = editor.slice(exportStart, exportEnd);
    const refreshStateSource = widget.slice(refreshStateStart, refreshStateEnd);

    // Joining an existing operation must return to the loop and re-check the
    // revision, rather than treating the older promise as the final result.
    assert.match(renderSource, /while \(sessionId === server\.sessionId && !server\.conflict\)/);
    assert.match(renderSource, /drainIterations > MAX_SERVER_RENDER_DRAIN_ITERATIONS/);
    assert.match(renderSource, /completedRevision < requestedRevision/);
    assert.match(renderSource, /server\.renderRevision <= revisionBeforeRender/);
    assert.match(renderSource, /if \(server\.renderInFlight\) \{[\s\S]*await server\.renderInFlight[\s\S]*continue;/);
    assert.match(renderSource, /if \(server\.renderRevision >= server\.revision\)[\s\S]*return true;/);
    assert.match(renderSource, /if \(server\.revision > requestedRevision\) continue;/);

    // Edits made during an earlier save/render cause another loop iteration.
    // Export only runs after the combined save-and-render drain returns true.
    assert.match(saveSource, /while \(sessionId === server\.sessionId && !server\.conflict\)/);
    assert.match(saveSource, /if \(server\.saveInFlight\) \{[\s\S]*await server\.saveInFlight[\s\S]*continue;/);
    assert.match(saveSource, /server\.dirty = server\.editVersion !== savedEditVersion/);
    assert.match(saveSource, /const rendered = await requestServerRender\(\)/);
    assert.match(saveSource, /if \(server\.dirty \|\| server\.saveInFlight\) continue;/);
    assert.match(saveSource, /if \(server\.renderRevision < server\.revision\) continue;/);
    assert.match(exportSource, /await flushServerSave\(\{ renderAfter: true \}\)/);
    assert.match(exportSource, /if \(!saved\) return;[\s\S]*await editorController\.export/);

    // Keep last-good imagery visible after a failed refresh, but never allow
    // that stale derivative to be presented or downloaded.
    assert.match(refreshStateSource, /previewPresent\.disabled = isError \|\| !hasPreview/);
    assert.match(refreshStateSource, /_setPreviewDownloadEnabled\(!isError && Boolean\(slidePresentationFileId\)\)/);
    assert.match(refreshStateSource, /_setPreviewEditEnabled\(Boolean\(slidePresentationPresentationId\)\)/);
});

test('editor preview opens its loader before waiting for the updated render', () => {
    const editor = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-editor.js'), 'utf8');
    const widget = readFrontendSource(path.join(ROOT, 'js/chat/slide-presentation-widget.js'), 'utf8');
    const slideCss = readFrontendSource(path.join(ROOT, 'css/chat/slide-presentation-widget.css'), 'utf8');
    const presentStart = editor.indexOf('async function requestSharedPresent()');
    const presentEnd = editor.indexOf('async function requestSharedExport()', presentStart);
    const editorPresentSource = editor.slice(presentStart, presentEnd);
    const parentPresentStart = widget.indexOf('present: async ({ slideIndex, renderPromise } = {}) =>');
    const parentPresentEnd = widget.indexOf('export: async', parentPresentStart);
    const parentPresentSource = widget.slice(parentPresentStart, parentPresentEnd);

    assert.match(editorPresentSource, /const saved = await flushServerSave\(\);/);
    assert.doesNotMatch(editorPresentSource, /flushServerSave\(\{ renderAfter: true \}\)/);
    assert.match(editorPresentSource, /const renderPromise = requestServerRender\(\)/);
    assert.match(editorPresentSource, /editorController\.present\(\{[\s\S]*renderPromise/);

    const openIndex = parentPresentSource.indexOf('openSlideshow({ deferContent: true, slideIndex })');
    const waitIndex = parentPresentSource.indexOf('await Promise.resolve(renderPromise)');
    const contentIndex = parentPresentSource.indexOf('_showSlideshowContent(slideIndex)');
    assert.ok(openIndex >= 0 && openIndex < waitIndex, 'slideshow shell opens before the render wait');
    assert.ok(waitIndex < contentIndex, 'new slide content appears only after rendering finishes');
    assert.match(widget, /function _showSlideshowContent\(requestedIndex = ssIndex\)/);
    assert.match(widget, /ssLoaderText\.textContent = t\('slide_presentation_editor_rendering', 'Updating preview…'\)/);
    assert.match(slideCss, /\.slide-presentation-slideshow-overlay\.is-rerendering \.slide-presentation-slideshow-loader-progress/);
    assert.match(slideCss, /\.slide-presentation-slideshow-overlay\.is-rerendering \.slide-presentation-slideshow-loader-spinner[\s\S]*width: 30px/);
});
