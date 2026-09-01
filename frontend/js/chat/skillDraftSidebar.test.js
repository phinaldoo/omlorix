const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const { readStreamMessagesSource } = require('./messages/source.cjs');
const { readSendMessageSource } = require('./sending/source.cjs');

const repoRoot = path.resolve(__dirname, '../../..');
const indexSource = readFrontendSource(path.join(repoRoot, 'frontend/index.html'), 'utf8');
const widgetSource = readFrontendSource(path.join(__dirname, 'skill-draft-widget.js'), 'utf8');
const widgetStyles = readFrontendSource(
    path.join(repoRoot, 'frontend/css/chat/skill-draft-widget.css'),
    'utf8',
);
const canvasStyles = readFrontendSource(
    path.join(repoRoot, 'frontend/css/chat/canvas-widget.css'),
    'utf8',
);
const structureStyles = readFrontendSource(
    path.join(repoRoot, 'frontend/css/common/structure.css'),
    'utf8',
);
const streamSource = readStreamMessagesSource();
const canvasSource = readFrontendSource(path.join(__dirname, 'canvas-widget.js'), 'utf8');
const sendMessageSource = readSendMessageSource();
const splitScreenSource = readFrontendSource(path.join(__dirname, 'splitScreen.js'), 'utf8');
const transcriptSource = readFrontendSource(path.join(__dirname, 'chatTranscriptRenderer.js'), 'utf8');
const latexSource = readFrontendSource(path.join(__dirname, 'latex-pdf-widget.js'), 'utf8');

test('skill drafts use a shared accessible canvas-style sidebar', () => {
    assert.ok(
        indexSource.indexOf('/css/chat/canvas-widget.css')
            < indexSource.indexOf('/css/chat/skill-draft-widget.css'),
        'skill extensions must load after the shared Canvas styles',
    );
    assert.match(indexSource, /class="skill-draft-preview-panel canvas-markdown-preview-panel"[^>]*id="skillDraftPreviewPanel"[^>]*role="dialog"/);
    assert.match(indexSource, /class="skill-draft-preview-resizer canvas-markdown-preview-resizer"[^>]*id="skillDraftPreviewResizer"[^>]*role="separator"/);
    assert.match(indexSource, /class="skill-draft-preview-header canvas-markdown-preview-header"/);
    assert.match(indexSource, /class="om-button" id="skillDraftPreviewClose"/);
    assert.match(indexSource, /id="skillDraftPreviewClose"[\s\S]*?<span data-skill-draft-icon="close" aria-hidden="true"><\/span>/);
    // Circular header actions may use spans as SVG mount points. Narrow-panel
    // rules must not hide spans generically or depend on a removed text-label
    // class.
    assert.doesNotMatch(
        canvasStyles,
        /\.canvas-markdown-preview-header\s+\.om-button\s*(?:>\s*)?span\s*\{[^}]*display:\s*none;/,
    );
    assert.doesNotMatch(canvasStyles, /\.om-button/);
    assert.doesNotMatch(
        structureStyles,
        /\.om-button\s*(?:>\s*)?span\s*\{[^}]*display:\s*none;/,
    );
    assert.doesNotMatch(structureStyles, /\.om-button/);
    assert.match(indexSource, /class="skill-draft-preview-view-btn canvas-markdown-editor-view-btn active"/);
    assert.match(indexSource, /data-skill-draft-view="edit"[^>]*aria-label="Edit source"/);
    assert.match(indexSource, /id="skillDraftFooterStatus"[^>]*aria-live="polite"/);
    assert.match(indexSource, /data-skill-draft-view="edit"[^>]*aria-controls="skillDraftEditor"/);
    assert.match(widgetStyles, /body\.skill-draft-preview-open \.main-container/);
    assert.match(widgetStyles, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.skill-draft-preview-panel/);
    assert.doesNotMatch(widgetStyles, /\.skill-draft-preview-panel\s*\{[^}]*position:\s*fixed/s);
    assert.doesNotMatch(widgetStyles, /\.skill-draft-result-card\s*\{[^}]*display:\s*flex/s);
    assert.doesNotMatch(widgetStyles, /var\(--admin-/);
});

test('skill draft cards launch the sidebar and save through the audited draft route', () => {
    assert.match(widgetSource, /const CARD_SELECTOR = '\.skill-draft-result-card'/);
    assert.match(widgetSource, /window\.setMainSidebarAutoCollapsed\('skill-draft-preview', true\)/);
    assert.match(widgetSource, /closeOtherArtifactPreviews\?\.\('skill-draft-preview'\)/);
    assert.match(latexSource, /closeOtherArtifactPreviews\?\.\('latex-pdf-preview'\)/);
    assert.match(widgetSource, /window\.renderMarkdownContent\(target, content\)/);
    assert.match(widgetSource, /skills-form-textarea/);
    assert.match(widgetSource, /sidebar-element-button/);
    assert.match(widgetSource, /'\/api\/v1\/skills\/draft\/save'/);
    assert.match(widgetSource, /credentials: 'include'/);
    assert.match(widgetSource, /typeof payload\?\.detail === 'string'/);
    assert.match(widgetSource, /workspaceSkills:changed/);
    assert.match(widgetSource, /event\.key === 'ArrowRight'/);
    assert.doesNotMatch(widgetSource, /window\.confirm|window\.alert|window\.prompt/);
});

test('Canvas label refresh ignores skill and other shared result cards', () => {
    const refreshStart = canvasSource.indexOf('    function refreshWidgetOpenButtonStates()');
    const refreshEnd = canvasSource.indexOf('\n    function normalizeName(', refreshStart);
    const refreshSource = canvasSource.slice(refreshStart, refreshEnd);

    assert.match(refreshSource, /const widgetKey = getWidgetKeyFromElement\(widget\);/);
    assert.match(refreshSource, /if \(!widgetKey\) return;/);
    assert.ok(
        refreshSource.indexOf('if (!widgetKey) return;')
            < refreshSource.indexOf('updateOpenButtonLabel(button, isOpen);'),
        'non-Canvas cards must be excluded before their button label is updated',
    );
});

test('only newly streamed skill drafts auto-open', () => {
    assert.match(
        streamSource,
        /const autoOpen = widgetOptions\?\.autoOpen === true;/,
    );
    assert.match(streamSource, /window\.skillDraftWidget\.initWidgets\(widgetWrapper, \{ autoOpen \}\)/);
    assert.doesNotMatch(streamSource, /closest\('\[data-is-streaming="true"\]'\)/);
    assert.match(sendMessageSource, /obj\.meta \?\? null,\s*\{ autoOpen: true \},/);
    assert.match(splitScreenSource, /obj\.meta \?\? null,\s*\{ autoOpen: true \},/);
    assert.match(transcriptSource, /widgetMeta,\s*\{ autoOpen: false \},/);
});

test('every supported locale contains the skill sidebar vocabulary', () => {
    const requiredKeys = [
        'skill_draft_open_editor',
        'skill_draft_close_editor_aria',
        'skill_draft_resize_editor_aria',
        'skill_draft_manifest_view_aria',
        'skill_draft_card_summary_one',
        'skill_draft_card_summary_other',
        'skill_draft_card_summary_saved_one',
        'skill_draft_card_summary_saved_other',
        'skill_draft_status_draft_files_one',
        'skill_draft_status_draft_files_other',
        'skill_draft_files_count_one',
        'skill_draft_files_count_other',
        'skill_draft_status_unsaved',
        'skill_draft_new_file',
        'skill_draft_open_workspace',
        'skill_draft_filename_invalid',
    ];
    const localeRoot = path.join(repoRoot, 'frontend/i18n');
    const locales = fs.readdirSync(localeRoot).filter((name) => (
        fs.existsSync(path.join(localeRoot, name, 'index.json'))
    ));

    for (const locale of locales) {
        const dictionary = JSON.parse(readFrontendSource(path.join(localeRoot, locale, 'index.json'), 'utf8'));
        for (const key of requiredKeys) {
            assert.equal(typeof dictionary[key], 'string', `${locale} is missing ${key}`);
            assert.ok(dictionary[key].trim(), `${locale}:${key} is empty`);
        }
    }
});

test('skill draft count labels pluralize and re-rendered fields regain focus', () => {
    assert.match(widgetSource, /isSingleFile \? 'skill_draft_card_summary_one' : 'skill_draft_card_summary_other'/);
    assert.match(widgetSource, /isSingleFile \? 'skill_draft_status_draft_files_one' : 'skill_draft_status_draft_files_other'/);
    assert.match(widgetSource, /isSingleFile \? 'skill_draft_files_count_one' : 'skill_draft_files_count_other'/);
    assert.match(
        widgetSource,
        /function handleChange\(event\)[\s\S]*?renderPanel\(\);[\s\S]*?querySelector\(`\[data-field="\$\{field\}"\]`\)\?\.focus\(\)/,
    );
});
