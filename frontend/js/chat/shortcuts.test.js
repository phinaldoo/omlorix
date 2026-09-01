const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SHORTCUTS_PATH = path.join(__dirname, 'shortcuts.js');
const I18N_ROOT = path.join(__dirname, '..', '..', 'i18n');
const {
    detectShortcutPlatform,
    hasPrimaryModifier,
    matchesBinding,
    isProtectedEditorTarget,
    normalizeSearchText,
} = require('./shortcuts.js');

function keyEvent(overrides = {}) {
    return {
        key: '',
        code: '',
        ctrlKey: false,
        metaKey: false,
        shiftKey: false,
        altKey: false,
        getModifierState: () => false,
        ...overrides,
    };
}

function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function attributeValue(tag, name) {
    const attribute = escapeRegExp(name);
    const match = tag.match(new RegExp(`\\b${attribute}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`, 'i'));
    return match ? (match[1] ?? match[2]) : null;
}

function openingTagById(source, id) {
    const match = source.match(new RegExp(`<[a-z][^>]*\\bid=["']${escapeRegExp(id)}["'][^>]*>`, 'iu'));
    assert.ok(match, `Missing element #${id}`);
    return match[0];
}

function assertClassTokens(tag, expectedTokens) {
    const classes = new Set((attributeValue(tag, 'class') || '').split(/\s+/u).filter(Boolean));
    expectedTokens.forEach((token) => {
        assert.ok(classes.has(token), `${tag} is missing class ${token}`);
    });
}

function stylesheetHrefs(source) {
    return (source.match(/<link\b[^>]*>/giu) || [])
        .filter((tag) => attributeValue(tag, 'rel')?.split(/\s+/u).includes('stylesheet'))
        .map((tag) => attributeValue(tag, 'href'));
}

function cssRuleBody(source, selectorFragment) {
    const selectorIndex = source.indexOf(selectorFragment);
    assert.notEqual(selectorIndex, -1, `Missing selector ${selectorFragment}`);
    const blockStart = source.indexOf('{', selectorIndex);
    const blockEnd = source.indexOf('}', blockStart);
    assert.notEqual(blockStart, -1, `${selectorFragment} has no declaration block`);
    assert.notEqual(blockEnd, -1, `${selectorFragment} has an unterminated declaration block`);
    return source.slice(blockStart + 1, blockEnd);
}

/**
 * Load the browser half of shortcuts.js against a deliberately small DOM.
 * Keeping this fixture local makes the regression test exercise the real
 * document keydown handler without adding a browser-DOM dependency.
 */
function loadShortcutRuntime({ matchingSelectors = [] } = {}) {
    const source = readFrontendSource(SHORTCUTS_PATH, 'utf8');
    const listeners = new Map();
    const matchedElement = new (class FakeElement {
        constructor() {
            this.hidden = false;
        }

        getClientRects() {
            return [{}];
        }
    })();
    let settingsOpenCount = 0;

    const document = {
        readyState: 'loading',
        body: { classList: { contains: () => false } },
        activeElement: null,
        addEventListener(type, listener) {
            listeners.set(type, listener);
        },
        getElementById: () => null,
        querySelectorAll(selector) {
            const selectors = selector.split(',').map((part) => part.trim());
            return selectors.some((part) => matchingSelectors.includes(part))
                ? [matchedElement]
                : [];
        },
    };
    const FakeElement = matchedElement.constructor;
    const window = {
        document,
        navigator: { platform: 'Win32' },
        getComputedStyle: () => ({
            display: 'block',
            visibility: 'visible',
            opacity: '1',
            pointerEvents: 'auto',
        }),
        openUserSettings: () => {
            settingsOpenCount += 1;
        },
    };

    vm.runInNewContext(source, {
        window,
        globalThis: window,
        Element: FakeElement,
        console,
        AbortController,
        URLSearchParams,
        Promise,
        Map,
        Object,
        Number,
        String,
        Array,
        Boolean,
        TypeError,
    });

    return {
        dispatchKeydown(event) {
            listeners.get('keydown')(event);
        },
        settingsOpenCount: () => settingsOpenCount,
    };
}

test('primary modifier follows the platform and never accepts both modifiers', () => {
    const mac = detectShortcutPlatform({ platform: 'MacIntel' });
    const windows = detectShortcutPlatform({ platform: 'Win32' });

    assert.equal(hasPrimaryModifier(keyEvent({ metaKey: true }), mac), true);
    assert.equal(hasPrimaryModifier(keyEvent({ ctrlKey: true }), mac), false);
    assert.equal(hasPrimaryModifier(keyEvent({ ctrlKey: true }), windows), true);
    assert.equal(hasPrimaryModifier(keyEvent({ ctrlKey: true, metaKey: true }), windows), false);
});

test('layout-aware slash accepts shifted slash but exact commands reject extra modifiers', () => {
    const platform = { usesMeta: false };
    const slash = { primary: true, key: '/', allowShiftForCharacter: true };
    const palette = { primary: true, key: 'k' };

    assert.equal(matchesBinding(keyEvent({ key: '/', ctrlKey: true, shiftKey: true }), slash, platform), true);
    assert.equal(matchesBinding(keyEvent({ key: 'k', ctrlKey: true }), palette, platform), true);
    assert.equal(matchesBinding(keyEvent({ key: 'k', ctrlKey: true, shiftKey: true }), palette, platform), false);
    assert.equal(matchesBinding(keyEvent({ key: 'k', ctrlKey: true, altKey: true }), palette, platform), false);
    assert.equal(matchesBinding(keyEvent({ key: '/', ctrlKey: true, altKey: true }), slash, platform), false);
});

test('rich editors and terminals keep ownership of their keyboard maps', () => {
    const protectedTarget = { closest: (selector) => selector.includes('.CodeMirror') ? {} : null };
    const plainInput = { closest: () => null };

    assert.equal(isProtectedEditorTarget(protectedTarget), true);
    assert.equal(isProtectedEditorTarget(plainInput), false);
});

test('command searching is case- and accent-insensitive', () => {
    assert.equal(normalizeSearchText('  RéSumÉ  '), 'resume');
});

test('registry exposes only the reviewed safe direct global bindings', () => {
    const source = readFrontendSource(SHORTCUTS_PATH, 'utf8');
    const bindingMatches = source.match(/binding:\s*\{/g) || [];

    assert.equal(bindingMatches.length, 2);
    assert.match(source, /id: 'palette\.open'[\s\S]*?binding: \{ primary: true, key: 'k' \}/);
    assert.match(source, /id: 'settings\.toggle'[\s\S]*?code: 'Comma'/);
    assert.doesNotMatch(source, /id: 'help\.shortcuts'/);
    assert.doesNotMatch(source, /id: 'help\.open'/);
    assert.doesNotMatch(source, /binding: \{[^}]*key: '[fjpuyv]'/i);
});

test('a closed ARIA menu does not suppress the real global keydown handler', () => {
    // This models popovers such as .canvas-files-dropdown: they remain laid out
    // while closed, so a bare role selector would reproduce the original bug.
    const runtime = loadShortcutRuntime({ matchingSelectors: ['[role="menu"]'] });
    let prevented = false;
    let stopped = false;
    runtime.dispatchKeydown(keyEvent({
        key: '<',
        code: 'Comma',
        ctrlKey: true,
        shiftKey: true,
        target: { closest: () => null },
        preventDefault: () => { prevented = true; },
        stopPropagation: () => { stopped = true; },
    }));

    assert.equal(runtime.settingsOpenCount(), 1);
    assert.equal(prevented, true);
    assert.equal(stopped, true);
});

test('an explicitly open ARIA menu still owns global shortcuts', () => {
    const runtime = loadShortcutRuntime({ matchingSelectors: ['[role="menu"].open'] });
    runtime.dispatchKeydown(keyEvent({
        key: '<',
        code: 'Comma',
        ctrlKey: true,
        shiftKey: true,
        target: { closest: () => null },
        preventDefault: () => {},
        stopPropagation: () => {},
    }));

    assert.equal(runtime.settingsOpenCount(), 0);
});

test('stopping a generation is the lowest-priority Escape action', () => {
    const shortcuts = readFrontendSource(SHORTCUTS_PATH, 'utf8');
    const chatBox = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');

    assert.match(shortcuts, /id: 'active-generation-stop'/);
    assert.match(shortcuts, /priority: -100/);
    assert.match(shortcuts, /!hasBlockingSurface\(\)/);
    assert.match(chatBox, /function canCancelActiveGeneration\(/);
    assert.match(chatBox, /window\.canCancelActiveGeneration = canCancelActiveGeneration/);
});

test('the palette handles Escape through its own cleanup path', () => {
    const source = readFrontendSource(SHORTCUTS_PATH, 'utf8');
    const handler = source.slice(
        source.indexOf('function handlePaletteKeydown'),
        source.indexOf('function openShortcutHelp'),
    );

    assert.ok(handler.indexOf("event.key === 'Escape'") < handler.indexOf("event.key === 'ArrowDown'"));
    assert.match(handler, /event\.preventDefault\(\);\s*closePalette\(\);\s*return;/);
});

test('the Command-K binding closes an already-open command palette', () => {
    const source = readFrontendSource(SHORTCUTS_PATH, 'utf8');
    const handler = source.slice(
        source.indexOf('function handleGlobalKeydown'),
        source.indexOf('registerBuiltInCommands();'),
    );

    assert.match(handler, /if \(state\.paletteOpen\)[\s\S]*commandRegistry\.get\('palette\.open'\)/);
    assert.match(handler, /matchesBinding\(event, paletteCommand\?\.binding, shortcutPlatform\)/);
    assert.match(handler, /event\.stopPropagation\(\);\s*closePalette\(\);/);
});

test('every locale contains the command palette and generated-help strings', () => {
    const requiredKeys = [
        'command_palette_title',
        'command_palette_open',
        'command_palette_placeholder',
        'command_palette_hint_navigate',
        'command_palette_hint_open',
        'command_palette_hint_close',
        'command_palette_empty',
        'command_palette_result_count_one',
        'command_palette_result_count_other',
        'command_palette_temporary_chat_toggle',
        'command_palette_temporary_chat_toggle_desc',
        'command_palette_theme_system',
        'command_palette_theme_light',
        'command_palette_theme_dark',
        'workspace_tabs_aria',
        'us_help_shortcuts_group_global',
        'us_help_shortcuts_send_message',
        'us_help_shortcuts_dismiss_surface',
        'us_help_shortcuts_workspace_tabs_desc',
    ];

    fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .forEach((entry) => {
            const dictionary = JSON.parse(readFrontendSource(path.join(I18N_ROOT, entry.name, 'index.json'), 'utf8'));
            requiredKeys.forEach((key) => {
                assert.ok(Object.hasOwn(dictionary, key), `${entry.name} is missing ${key}`);
                assert.ok(String(dictionary[key]).trim(), `${entry.name} has an empty ${key}`);
            });
        });
});

test('the command palette exposes the requested contextual and account actions', () => {
    const source = readFrontendSource(SHORTCUTS_PATH, 'utf8');

    [
        'chat.temporary_toggle',
        'chat.share',
        'chat.model_settings',
        'appearance.theme_system',
        'appearance.theme_light',
        'appearance.theme_dark',
        'account.logout',
    ].forEach((commandId) => {
        assert.match(source, new RegExp(`id: '${commandId.replaceAll('.', '\\.')}'`));
    });

    assert.match(source, /id: 'chat\.share'[\s\S]*?root\.ChatShareModal\.open\(\)/);
    assert.match(source, /id: 'chat\.model_settings'[\s\S]*?root\.openModelSettingsSidebar\(\)/);
    assert.match(
        source,
        /id: 'chat\.model_settings'[\s\S]*?isVisible\(document\.getElementById\('openModelSettingsButton'\)\)/,
    );
    assert.match(source, /id: 'account\.logout'[\s\S]*?root\.logout\(\)/);
    assert.match(source, /applyThemeMode\('system'\)/);
    assert.match(source, /applyThemeMode\('light'\)/);
    assert.match(source, /applyThemeMode\('dark'\)/);
});

test('the command palette and archived chats reuse the accessible shared modal shell', () => {
    const source = readFrontendSource(SHORTCUTS_PATH, 'utf8');
    const styles = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'shortcuts.css'), 'utf8');
    const chatBoxStyles = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'chatBox', 'chatBox.css'), 'utf8');
    const archivedStyles = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'archived-chats.css'), 'utf8');
    const sharedStyles = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'common', 'searchModal.css'), 'utf8');
    const indexSource = readFrontendSource(path.join(__dirname, '..', '..', 'index.html'), 'utf8');
    const initStyles = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'common', 'init.css'), 'utf8');
    const animationStyles = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'common', 'animations.css'), 'utf8');

    assert.match(source, /shortcut-palette-search-icon/);
    assert.match(source, /shortcut-palette-count/);
    assert.match(source, /shortcut-palette-result-icon/);
    assert.match(source, /shortcut-palette-footer-hint/);
    assert.match(source, /overlay\.className\s*=\s*['"][^'"]*\bshared-modal-overlay\b[^'"]*['"]/u);
    assert.match(source, /<section\b(?=[^>]*\bshared-modal\b)(?=[^>]*role="dialog")(?=[^>]*aria-modal="true")/u);
    assert.match(source, /shortcut-palette-header[^"`]*\bshared-modal-header\b[^"`]*\bshared-modal-header--search\b/u);
    assert.match(source, /shortcut-palette-empty search-modal-empty/u);

    const overlayTag = openingTagById(indexSource, 'archivedChatsOverlay');
    const modalTag = openingTagById(indexSource, 'archivedChatsModal');
    const titleTag = openingTagById(indexSource, 'archivedChatsTitle');
    const closeTag = openingTagById(indexSource, 'archivedChatsCloseButton');
    const loadingTag = openingTagById(indexSource, 'archivedChatsLoading');
    const emptyTag = openingTagById(indexSource, 'archivedChatsEmpty');

    assertClassTokens(overlayTag, ['archived-chats-overlay', 'search-modal-overlay', 'shared-modal-overlay']);
    assert.equal(attributeValue(overlayTag, 'aria-hidden'), 'true');
    assert.match(overlayTag, /\shidden(?:\s|>)/u);
    assert.match(overlayTag, /\sinert(?:\s|>)/u);
    assertClassTokens(modalTag, ['search-modal', 'shared-modal', 'shared-modal--fixed']);
    assert.equal(attributeValue(modalTag, 'role'), 'dialog');
    assert.equal(attributeValue(modalTag, 'aria-modal'), 'true');
    assert.equal(attributeValue(modalTag, 'aria-labelledby'), 'archivedChatsTitle');
    assert.equal(attributeValue(modalTag, 'tabindex'), '-1');
    assertClassTokens(titleTag, ['search-modal-title', 'shared-modal-title']);
    assertClassTokens(closeTag, ['shared-modal-close']);
    assert.equal(attributeValue(closeTag, 'type'), 'button');
    assert.equal(attributeValue(closeTag, 'data-i18n-attr'), 'aria-label:common_close');
    assertClassTokens(loadingTag, ['search-modal-loading']);
    assertClassTokens(emptyTag, ['search-modal-empty']);

    const stylesheets = stylesheetHrefs(indexSource);
    const sharedStylesheetIndex = stylesheets.indexOf('/css/common/searchModal.css');
    assert.equal(sharedStylesheetIndex, stylesheets.length - 1);
    assert.ok(sharedStylesheetIndex > stylesheets.indexOf('/css/chat/archived-chats.css'));
    assert.ok(sharedStylesheetIndex > stylesheets.indexOf('/css/chat/shortcuts.css'));

    assert.match(sharedStyles, /width: min\(var\(--shared-modal-width, 560px\), 100%\)/);
    assert.match(sharedStyles, /--search-modal-vertical-gutter: clamp\(16px, 4vh, 48px\)/);
    assert.match(sharedStyles, /align-items: center;/);
    assert.match(sharedStyles, /height: min\(640px, calc\(100dvh - var\(--search-modal-vertical-gutter\) - var\(--search-modal-vertical-gutter\)\)\)/);
    assert.doesNotMatch(chatBoxStyles, /\.chat-picker-modal/);
    assert.match(initStyles, /--modal-overlay-backdrop-filter: blur\(8px\) saturate\(1\.2\)/);
    assert.match(sharedStyles, /backdrop-filter: var\(--modal-overlay-backdrop-filter\)/);
    assert.match(sharedStyles, /backdrop-filter: blur\(40px\) saturate\(1\.35\)/);
    assert.match(sharedStyles, /\.search-modal-header--main,\s*\.shared-modal-header--main\s*\{[\s\S]*?justify-content: space-between;[\s\S]*?padding: 16px 20px;/);
    assert.match(sharedStyles, /\.search-modal-title,\s*\.shared-modal-title\s*\{[\s\S]*?font-size: 18px;[\s\S]*?font-weight: 600;/);
    assert.match(sharedStyles, /\.shared-modal-close\s*\{/);
    assert.match(sharedStyles, /\.search-modal-header--search,[\s\S]*?\.shared-modal-header--search\s*\{[\s\S]*?min-height: 58px;[\s\S]*?padding: 14px 16px 13px 18px;/);
    assert.match(sharedStyles, /\.search-modal-loading,[\s\S]*?\.search-modal-empty\s*\{[\s\S]*?padding: 40px 20px;[\s\S]*?font-size: 14px;/);
    assert.match(sharedStyles, /\.search-modal-loading::before\s*\{[\s\S]*?width: 32px;[\s\S]*?border-top-color: var\(--accent-color\);/);
    assert.match(styles, /@media \(hover: hover\) and \(pointer: fine\)/);
    assert.match(sharedStyles, /search-modal-overlay\.is-closing/);
    assert.doesNotMatch(chatBoxStyles, /#chatBoxChooseFilesOverlay[^\{]*\{[^}]*background:/);
    assert.doesNotMatch(archivedStyles, /\.archived-chats-(?:title|close)\s*\{/);
    assert.doesNotMatch(archivedStyles, /\.archived-chats-(?:loading|empty|spinner)\s*\{/);
    assert.doesNotMatch(
        cssRuleBody(archivedStyles, '.archived-chats-overlay {'),
        /(?:^|\n)\s*(?:position|inset|padding|background|backdrop-filter|-webkit-backdrop-filter|border|border-radius|box-shadow)\s*:/u,
    );
    assert.doesNotMatch(
        cssRuleBody(styles, '.shortcut-palette-overlay {'),
        /(?:^|\n)\s*(?:position|inset|padding|background|backdrop-filter|-webkit-backdrop-filter|border|border-radius|box-shadow)\s*:/u,
    );
    assert.match(animationStyles, /@keyframes search-modal-in/);
    assert.match(animationStyles, /@keyframes search-modal-out/);
});
