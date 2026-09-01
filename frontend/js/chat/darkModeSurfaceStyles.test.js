const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

function readStyle(relativePath) {
    return fs.readFileSync(path.join(__dirname, '../../css', relativePath), 'utf8');
}

function getRuleDeclarations(source, selector, requiredDeclaration = '') {
    const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const matches = source.matchAll(new RegExp(`(?:^|\\n)\\s*${escapedSelector}\\s*\\{([^{}]*)\\}`, 'g'));

    for (const match of matches) {
        if (!requiredDeclaration || match[1].includes(requiredDeclaration)) {
            return match[1];
        }
    }

    assert.fail(`Missing ${selector} rule containing ${requiredDeclaration || 'the expected declarations'}`);
}

/**
 * Return the body of the media rule beginning at marker while respecting any
 * nested blocks. This keeps the regression assertion independent of CSS
 * whitespace and formatting.
 */
function getMediaRuleBody(source, marker, startIndex = 0) {
    const markerIndex = source.indexOf(marker, startIndex);
    assert.notEqual(markerIndex, -1, `Missing media rule: ${marker}`);

    const blockStart = source.indexOf('{', markerIndex);
    assert.notEqual(blockStart, -1, `Media rule has no declaration block: ${marker}`);

    let depth = 1;
    for (let index = blockStart + 1; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) {
            return {
                body: source.slice(blockStart + 1, index),
                endIndex: index + 1,
            };
        }
    }

    assert.fail(`Unterminated media rule: ${marker}`);
}

test('main sidebar element hover is scoped to fine-pointer devices', () => {
    const sidebarStyle = readStyle('chat/sidebar.css');
    const touchMarker = '@media (hover: none), (pointer: coarse)';
    const desktopMarker = '@media (hover: hover) and (pointer: fine)';
    const sidebarElementSection = sidebarStyle.indexOf('/* Ellipsis visibility:');
    assert.notEqual(sidebarElementSection, -1, 'Missing the sidebar element interaction section');
    const touchRule = getMediaRuleBody(sidebarStyle, touchMarker, sidebarElementSection);

    // A desktop media rule nested in this touch block can never match: its
    // hover and pointer capabilities contradict the enclosing conditions.
    assert.doesNotMatch(touchRule.body, /\.sidebar-element-button:hover/);
    assert.doesNotMatch(touchRule.body, /@media \(hover: hover\)/);

    // Locate the next desktop rule after the complete touch block so this
    // assertion also protects the brace placement that caused the regression.
    const desktopRule = getMediaRuleBody(sidebarStyle, desktopMarker, touchRule.endIndex);
    assert.match(
        desktopRule.body,
        /\.sidebar-element-button:hover\s*\{[^{}]*background-color:\s*var\(--hover\)/
    );

    const profileButtonSection = sidebarStyle.indexOf('.sidebar-profile-button {');
    assert.notEqual(profileButtonSection, -1, 'Missing the sidebar profile button styles');
    const profileDesktopRule = getMediaRuleBody(sidebarStyle, desktopMarker, profileButtonSection);
    assert.match(
        profileDesktopRule.body,
        /\.sidebar-profile-button:hover\s*\{[^{}]*background-color:\s*var\(--hover\)/
    );
});

test('index dark mode surfaces consume the shared semantic palette', () => {
    const initStyle = readStyle('common/init.css');
    const sidebarStyle = readStyle('chat/sidebar.css');
    const elementsStyle = readStyle('common/elements.css');
    const tooltipStyle = readStyle('common/tooltip.css');
    const chatBoxStyle = readStyle('chat/chatBox/chatBox.css');
    const elementsNewStyle = readStyle('common/elementsNew.css');
    const selectionTooltipStyle = readStyle('chat/chatBox/chatBoxSelectionTooltip.css');
    const chatStyle = readStyle('chat/chat.css');
    const darkSurfacePalette = getRuleDeclarations(initStyle, '[data-mode="dark"]', '--surface-elevated:');
    const lightComposerPalette = getRuleDeclarations(initStyle, '[data-mode="light"]', '--chat-composer-background:');
    const darkComposerPalette = getRuleDeclarations(initStyle, '[data-mode="dark"]', '--chat-composer-background:');
    const profileDropdown = getRuleDeclarations(sidebarStyle, '.sidebar-profile-dropdown');
    const selectOptions = getRuleDeclarations(elementsStyle, '.select-options');
    const selectDropdown = getRuleDeclarations(elementsStyle, '.select-dropdown');
    const tooltip = getRuleDeclarations(tooltipStyle, '.tooltip');
    const chatBox = getRuleDeclarations(chatBoxStyle, '.chat-box');
    const chatBoxButtonHover = getRuleDeclarations(
        elementsNewStyle,
        '.om-button:hover:not(:disabled):not(.disabled):not(.is-disabled)'
    );
    const selectionTooltip = getRuleDeclarations(
        selectionTooltipStyle,
        '[data-mode="dark"] .selection-tooltip'
    );
    const selectionTooltipArrow = getRuleDeclarations(
        selectionTooltipStyle,
        '[data-mode="dark"] .selection-tooltip::after'
    );
    const selectionTooltipButtonHover = getRuleDeclarations(
        selectionTooltipStyle,
        '[data-mode="dark"] .selection-tooltip-btn:hover'
    );
    const addReferenceHover = getRuleDeclarations(
        selectionTooltipStyle,
        '[data-mode="dark"] .selection-tooltip-btn[data-action="add-reference"]:hover'
    );
    const addReferenceFocus = getRuleDeclarations(
        selectionTooltipStyle,
        '[data-mode="dark"] .selection-tooltip-btn[data-action="add-reference"]:focus-visible'
    );
    const toolInputPreview = getRuleDeclarations(chatStyle, '.function-call-live-preview');
    const toolInputPreviewLabel = getRuleDeclarations(
        chatStyle,
        '.function-call-live-preview-label'
    );
    const toolInputPreviewViewport = getRuleDeclarations(
        chatStyle,
        '.function-call-live-preview-viewport'
    );
    const toolInputPreviewTopFade = getRuleDeclarations(
        chatStyle,
        '.function-call-live-preview-viewport::before',
        'background:'
    );
    const toolInputPreviewBottomFade = getRuleDeclarations(
        chatStyle,
        '.function-call-live-preview-viewport::after',
        'background:'
    );
    const toolInputPreviewCode = getRuleDeclarations(chatStyle, '.function-call-live-preview-code');

    assert.match(darkSurfacePalette, /--surface-elevated:\s*#242428/);
    assert.match(darkSurfacePalette, /--surface-interactive-hover:\s*#39393f/);
    assert.match(profileDropdown, /background-color:\s*var\(--surface-elevated/);
    assert.match(selectOptions, /box-shadow:\s*var\(--surface-elevated-shadow,\s*0 2px 4px var\(--shadow-color\)\)/);
    assert.match(selectDropdown, /background-color:\s*var\(--surface-elevated/);
    assert.match(tooltip, /background:\s*var\(--surface-elevated/);
    assert.match(lightComposerPalette, /--chat-composer-background:\s*#EFEDE8/);
    assert.match(lightComposerPalette, /--chat-composer-control-hover:\s*#DAD6CE/);
    assert.match(lightComposerPalette, /--chat-composer-placeholder:\s*#6F6B64/);
    assert.match(lightComposerPalette, /--chat-composer-primary-control-background:\s*#000000/);
    assert.match(lightComposerPalette, /--chat-composer-primary-control-text:\s*#FFFFFF/);
    assert.match(darkComposerPalette, /--chat-composer-background:\s*#2D2C29/);
    assert.match(darkComposerPalette, /--chat-composer-control-hover:\s*#44413C/);
    assert.match(darkComposerPalette, /--chat-composer-primary-control-background:\s*#FFFFFF/);
    assert.match(darkComposerPalette, /--chat-composer-primary-control-text:\s*#000000/);
    assert.match(chatBox, /background:\s*var\(--chat-composer-background/);
    assert.match(chatBox, /border:\s*1px solid var\(--chat-composer-border/);
    assert.match(chatBoxButtonHover, /background-color:\s*var\(--hover/);

    // The text-selection actions float over arbitrary chat content. Their
    // dark-mode hover must therefore be an opaque semantic surface rather
    // than an accent mixed with transparency and the underlying content.
    assert.match(selectionTooltip, /background-color:\s*var\(--surface-elevated/);
    assert.match(selectionTooltip, /border-color:\s*var\(--surface-elevated-border/);
    assert.match(selectionTooltipArrow, /background-color:\s*var\(--surface-elevated/);
    assert.match(selectionTooltipButtonHover, /background-color:\s*var\(--surface-interactive-hover/);
    assert.match(addReferenceHover, /var\(--surface-interactive-hover/);
    assert.doesNotMatch(addReferenceHover, /background-color:[^;]*transparent/);
    assert.match(addReferenceFocus, /var\(--surface-interactive-hover/);
    assert.match(addReferenceFocus, /color:/);
    // Streaming tool arguments used to fall back to a translucent white
    // surface because --input-background-color is not part of Omlorix's theme
    // palette. The card, edge fades, and native scrollbar must all derive
    // from semantic tokens so they remain legible during live theme changes.
    assert.match(toolInputPreview, /--tool-input-preview-surface:\s*var\(--surface-control/);
    assert.match(toolInputPreview, /--tool-input-preview-border:\s*var\(--surface-control-border/);
    assert.match(toolInputPreview, /--tool-input-preview-text:\s*color-mix\([^;]*--text-color-secondary/);
    assert.match(toolInputPreview, /border:\s*[^;]*var\(--tool-input-preview-border\)[^;]*;/);
    assert.match(toolInputPreview, /background:\s*[^;]*var\(--tool-input-preview-surface\)[^;]*;/);
    assert.match(toolInputPreviewLabel, /color:\s*var\(--tool-input-preview-text\)\s*;/);
    assert.match(toolInputPreviewCode, /color:\s*var\(--tool-input-preview-text\)\s*;/);
    assert.match(toolInputPreviewViewport, /scrollbar-color:[^;]*var\(--text-color-tertiary\)/);
    assert.match(toolInputPreviewTopFade, /background:\s*[^;]*var\(--tool-input-preview-surface\)[^;]*;/);
    assert.match(toolInputPreviewBottomFade, /background:\s*[^;]*var\(--tool-input-preview-surface\)[^;]*;/);
    assert.doesNotMatch(chatStyle, /--input-background-color/);
});

test('dark-mode markdown links use a distinct accessible foreground', () => {
    const initStyle = readStyle('chat/init.css');
    const markdownStyle = readStyle('chat/markdown.css');
    const darkPalette = getRuleDeclarations(initStyle, '[data-mode="dark"]', '--chat-markdown-link-color:');
    const markdownLink = getRuleDeclarations(markdownStyle, '.markdown-body a');
    const markdownLinkHover = getRuleDeclarations(markdownStyle, '.markdown-body a:hover');

    // A dedicated blue keeps links identifiable even when the monochrome
    // theme's general-purpose primary color is intentionally neutral.
    assert.match(darkPalette, /--chat-markdown-link-color:\s*#8AB4F8/);
    assert.match(darkPalette, /--chat-markdown-link-hover-color:\s*#AECBFA/);
    assert.match(darkPalette, /--chat-markdown-link-decoration-color:\s*rgba\(138,\s*180,\s*248,\s*0\.58\)/);
    assert.match(markdownLink, /color:\s*var\(--chat-markdown-link-color,\s*var\(--primary-color\)\)/);
    assert.match(markdownLink, /text-decoration-line:\s*underline/);
    assert.match(markdownLink, /text-decoration-color:\s*var\(--chat-markdown-link-decoration-color,\s*currentColor\)/);
    assert.match(markdownLinkHover, /color:\s*var\(--chat-markdown-link-hover-color/);
    assert.match(markdownLinkHover, /text-decoration-color:\s*currentColor/);
});
