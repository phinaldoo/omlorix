const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

const SIDEBAR_PATH = path.join(__dirname, 'sidebar.js');
const SIDEBAR_STYLES_PATH = path.join(__dirname, '../../css/chat/sidebar.css');
const STRUCTURE_STYLES_PATH = path.join(__dirname, '../../css/common/structure.css');
const INDEX_PATH = path.join(__dirname, '../../index.html');
const CANVAS_WIDGET_PATH = path.join(__dirname, 'canvas-widget.js');
const NOTES_PATH = path.join(__dirname, 'notes.js');
const PRESENTATION_WIDGET_PATH = path.join(__dirname, 'slide-presentation-widget.js');

/**
 * Load the persisted-state and preview-constraint portion of sidebar.js with
 * small dependency fakes. Keeping the production functions intact makes these
 * tests sensitive to preference writes and multi-preview handoff behavior.
 */
function loadSidebarPreferenceController({ savedState = null, overlay = false } = {}) {
    const source = readFrontendSource(SIDEBAR_PATH, 'utf8');
    const start = source.indexOf("const SIDEBAR_OPEN_STATE_STORAGE_KEY = 'omlorix.sidebar.openState';");
    const end = source.indexOf('function getChatAreaWidth()', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);

    const storage = new Map();
    if (savedState) {
        storage.set('omlorix.sidebar.openState', savedState);
    }

    const calls = [];
    const window = {};
    const controller = Function(
        'localStorage',
        'window',
        'console',
        'closeSidebar',
        'openSidebar',
        'isOverlayMode',
        `${source.slice(start, end)}
         return {
             applyEffectiveSidebarOpenState,
             setMainSidebarAutoCollapsed,
             closeOtherArtifactPreviews,
         };`,
    )(
        {
            getItem(key) {
                return storage.get(key) ?? null;
            },
            setItem(key, value) {
                storage.set(key, String(value));
            },
        },
        window,
        console,
        (options) => calls.push({ action: 'close', options }),
        (options) => calls.push({ action: 'open', options }),
        () => overlay,
    );

    return { calls, controller, storage, window };
}

/**
 * Load the transition-suppression helper directly from production source so
 * the regression test verifies the same layout transaction used at runtime.
 */
function loadTransitionSuppressionHelper() {
    const source = readFrontendSource(SIDEBAR_PATH, 'utf8');
    const start = source.indexOf('function runWithoutSidebarSizeTransition(');
    const end = source.indexOf('function setDesktopSidebarCollapsedState(', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    return Function(`${source.slice(start, end)}
        return runWithoutSidebarSizeTransition;`)();
}

test('main header sidebar button becomes visible in overlay mode', () => {
    const indexSource = readFrontendSource(INDEX_PATH, 'utf8');
    const structureStyles = readFrontendSource(STRUCTURE_STYLES_PATH, 'utf8');
    const sidebarStyles = readFrontendSource(SIDEBAR_STYLES_PATH, 'utf8');
    const buttonMarkup = indexSource.match(/<button[^>]+id="mainContainerSidebarCloseButton"[^>]*>/)?.[0];

    assert.ok(buttonMarkup);
    assert.doesNotMatch(buttonMarkup, /style=/);
    assert.match(
        structureStyles,
        /#mainContainerSidebarCloseButton\s*\{[\s\S]*?display:\s*none;[\s\S]*?\}/,
    );
    assert.match(
        sidebarStyles,
        /body\.sidebar-overlay-mode \.main-container-header #mainContainerSidebarCloseButton\s*\{[\s\S]*?display:\s*flex;[\s\S]*?\}/,
    );
});

test('sticky sidebar surfaces stay above chat row menu controls while scrolling', () => {
    const sidebarStyles = readFrontendSource(SIDEBAR_STYLES_PATH, 'utf8');
    const menuLayerMatch = sidebarStyles.match(
        /\.sidebar-element-menu-trigger\s*\{[^}]*z-index:\s*(\d+);/,
    );
    assert.ok(menuLayerMatch, 'missing chat row menu layer');
    const menuLayer = Number(menuLayerMatch[1]);

    ['sidebar-header', 'sidebar-mid', 'sidebar-footer'].forEach((className) => {
        const stickyLayerMatch = sidebarStyles.match(
            new RegExp(`\\.${className}\\s*\\{[^}]*z-index:\\s*(\\d+);`),
        );
        assert.ok(stickyLayerMatch, `missing ${className} layer`);
        assert.ok(
            Number(stickyLayerMatch[1]) > menuLayer,
            `${className} must paint above chat row menu controls`,
        );
    });
});

test('artifact preview auto-collapse preserves and restores the saved sidebar preference', () => {
    const runtime = loadSidebarPreferenceController({ savedState: 'open' });

    runtime.controller.setMainSidebarAutoCollapsed('canvas-preview', true);
    assert.deepEqual(runtime.calls, [
        { action: 'close', options: { persist: false } },
    ]);
    assert.equal(runtime.storage.get('omlorix.sidebar.openState'), 'open');

    runtime.controller.setMainSidebarAutoCollapsed('canvas-preview', false);
    assert.deepEqual(runtime.calls.at(-1), {
        action: 'open',
        options: { persist: false },
    });
    assert.equal(runtime.storage.get('omlorix.sidebar.openState'), 'open');
});

test('artifact preview release reapplies a saved collapsed state without deferred collapse', () => {
    const runtime = loadSidebarPreferenceController({ savedState: 'closed' });

    runtime.controller.setMainSidebarAutoCollapsed('canvas-preview', true);
    runtime.controller.setMainSidebarAutoCollapsed('canvas-preview', false);

    assert.deepEqual(runtime.calls.at(-1), {
        action: 'close',
        options: { persist: false, defer: false },
    });
    assert.equal(runtime.storage.get('omlorix.sidebar.openState'), 'closed');
});

test('artifact preview handoffs do not restore the sidebar between panels', () => {
    const runtime = loadSidebarPreferenceController({ savedState: 'open' });

    runtime.controller.setMainSidebarAutoCollapsed('canvas-preview', true);
    runtime.controller.setMainSidebarAutoCollapsed('notes-preview', true);
    runtime.controller.setMainSidebarAutoCollapsed('canvas-preview', false);

    assert.equal(runtime.calls.filter((call) => call.action === 'open').length, 0);

    runtime.controller.setMainSidebarAutoCollapsed('notes-preview', false);
    assert.equal(runtime.calls.filter((call) => call.action === 'open').length, 1);
});

test('artifact preview handoff closes every competing surface and preserves the active one', () => {
    const runtime = loadSidebarPreferenceController();
    const closed = [];

    runtime.window.canvasMarkdownWidget = {
        hidePreviewPanel: () => closed.push('canvas-preview'),
    };
    runtime.window.slidePresentationWidget = {
        hidePreviewPanel: () => closed.push('slide-presentation-preview'),
    };
    runtime.window.NotesToolSidebar = {
        hidePreviewPanel: () => closed.push('notes-preview'),
    };
    runtime.window.deepResearchWidget = {
        closeSidebar: (options) => closed.push(['deep-research-preview', options]),
    };
    runtime.window.skillDraftWidget = {
        closeSidebar: (options) => closed.push(['skill-draft-preview', options]),
    };
    runtime.window.latexPdfWidget = {
        hidePreviewPanel: () => closed.push('latex-pdf-preview'),
    };
    runtime.window.FilesPreview = {
        close: () => closed.push('files-preview'),
    };

    runtime.controller.closeOtherArtifactPreviews('slide-presentation-preview');

    assert.deepEqual(closed, [
        'canvas-preview',
        'notes-preview',
        ['deep-research-preview', { restoreFocus: false }],
        ['skill-draft-preview', { restoreFocus: false }],
        'latex-pdf-preview',
        'files-preview',
    ]);
});

test('redundant artifact preview releases do not restore the sidebar', () => {
    const runtime = loadSidebarPreferenceController({ savedState: 'open' });

    runtime.controller.setMainSidebarAutoCollapsed('canvas-preview', false);
    assert.deepEqual(runtime.calls, []);

    runtime.controller.setMainSidebarAutoCollapsed('canvas-preview', true);
    runtime.controller.setMainSidebarAutoCollapsed('canvas-preview', false);
    const callsAfterValidRelease = runtime.calls.length;

    runtime.controller.setMainSidebarAutoCollapsed('canvas-preview', false);
    assert.equal(runtime.calls.length, callsAfterValidRelease);
});

test('artifact preview release restores the default open desktop state without a saved preference', () => {
    const runtime = loadSidebarPreferenceController();

    runtime.controller.setMainSidebarAutoCollapsed('canvas-preview', true);
    runtime.controller.setMainSidebarAutoCollapsed('canvas-preview', false);

    assert.deepEqual(runtime.calls.at(-1), {
        action: 'open',
        options: { persist: false },
    });
    assert.equal(runtime.storage.has('omlorix.sidebar.openState'), false);
});

test('releasing a preview in overlay mode does not automatically open the overlay sidebar', () => {
    const runtime = loadSidebarPreferenceController({ savedState: 'open', overlay: true });

    runtime.controller.setMainSidebarAutoCollapsed('slide-presentation-preview', true);
    runtime.controller.setMainSidebarAutoCollapsed('slide-presentation-preview', false);

    assert.equal(runtime.calls.filter((call) => call.action === 'open').length, 0);
    assert.equal(runtime.storage.get('omlorix.sidebar.openState'), 'open');
});

test('responsive mode changes commit the final sidebar width with transitions disabled', () => {
    const runWithoutSidebarSizeTransition = loadTransitionSuppressionHelper();
    const transitionLog = [];
    let transition = 'min-width 0.3s ease, max-width 0.3s ease, width 0.3s ease';
    let forcedLayouts = 0;
    const sidebar = {
        style: {
            get transition() {
                return transition;
            },
            set transition(value) {
                transition = value;
                transitionLog.push(value);
            },
            width: '250px',
        },
        get offsetHeight() {
            forcedLayouts += 1;
            return 900;
        },
    };

    runWithoutSidebarSizeTransition(sidebar, () => {
        assert.equal(sidebar.style.transition, 'none');
        sidebar.style.width = '50px';
    });

    assert.equal(sidebar.style.width, '50px');
    assert.equal(forcedLayouts, 1);
    assert.deepEqual(transitionLog, [
        'none',
        'min-width 0.3s ease, max-width 0.3s ease, width 0.3s ease',
    ]);
});

test('all space-reserving artifact previews use the shared temporary-collapse lifecycle', () => {
    const sidebarSource = readFrontendSource(SIDEBAR_PATH, 'utf8');
    const canvasSource = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const presentationSource = readFrontendSource(PRESENTATION_WIDGET_PATH, 'utf8');
    const deepResearchSource = readFrontendSource(
        path.join(__dirname, 'deep-research-widget.js'),
        'utf8',
    );

    assert.match(canvasSource, /setMainSidebarAutoCollapsed\('canvas-preview', previewVisible\)/);
    assert.match(notesSource, /setMainSidebarAutoCollapsed\('notes-preview', state\.isVisible\)/);
    assert.match(presentationSource, /setMainSidebarAutoCollapsed\('slide-presentation-preview', visible\)/);
    assert.match(
        deepResearchSource,
        /setMainSidebarAutoCollapsed\('deep-research-preview', true\)/,
    );
    assert.match(
        deepResearchSource,
        /setMainSidebarAutoCollapsed\('deep-research-preview', false\)/,
    );
    assert.match(sidebarSource, /function closeOtherArtifactPreviews\(activeSource\)/);
    assert.match(sidebarSource, /\['canvas-preview',[\s\S]*\['slide-presentation-preview'/);
    assert.match(canvasSource, /closeOtherArtifactPreviews\('canvas-preview'\)/);
    assert.match(presentationSource, /closeOtherArtifactPreviews\('slide-presentation-preview'\)/);

    const exitOverlayStart = sidebarSource.indexOf('    } else {', sidebarSource.indexOf('function updateSidebarMode()'));
    const exitOverlayEnd = sidebarSource.indexOf('\n    }\n\n    // Unlock after', exitOverlayStart);
    const exitOverlaySource = sidebarSource.slice(exitOverlayStart, exitOverlayEnd);
    assert.match(exitOverlaySource, /runWithoutSidebarSizeTransition\(sidebar, \(\) => \{/);
    assert.match(exitOverlaySource, /classList\.remove\('sidebar-overlay-mode'\);[\s\S]*applyEffectiveSidebarOpenState/);
});
