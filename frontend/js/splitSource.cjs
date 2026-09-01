const fs = require('node:fs');
const path = require('node:path');

const FRONTEND_ROOT = path.join(__dirname, '..');

const SPLIT_SOURCE_PATHS = new Map([
    ['js/admin/helper.js', [
        'js/admin/helper/core.js',
        'js/admin/helper/generatedMarkup.js',
        'js/admin/helper/schemaMetadata.js',
        'js/admin/helper/uiState.js',
        'js/admin/helper/fieldLayout.js',
        'js/admin/helper/selectControls.js',
        'js/admin/helper/fieldControls.js',
        'js/admin/helper/api.js',
        'js/admin/helper/settingsController.js',
        'js/admin/helper/fieldValidation.js',
        'js/admin/helper.js',
    ]],
    ['js/chat/canvas-widget.js', [
        'js/chat/canvas-widget/header.js',
        'js/chat/canvas-widget/arguments.js',
        'js/chat/canvas-widget/csv.js',
        'js/chat/canvas-widget/status.js',
        'js/chat/canvas-widget/reference-selection.js',
        'js/chat/canvas-widget/sharing.js',
        'js/chat/canvas-widget/editor-persistence.js',
        'js/chat/canvas-widget/file-loading.js',
        'js/chat/canvas-widget/html-documents.js',
        'js/chat/canvas-widget/pdf-preview.js',
        'js/chat/canvas-widget/rendering.js',
        'js/chat/canvas-widget/lifecycle.js',
        'js/chat/canvas-widget.js',
    ]],
    ['js/chat/chatBox.js', [
        'js/chat/chatBox/references-and-files.js',
        'js/chat/chatBox/meeting-transcript.js',
        'js/chat/chatBox/composer-controls.js',
        'js/chat/chatBox/attachments-and-generation.js',
        'js/chat/chatBox/event-handlers.js',
        'js/chat/chatBox/mentions.js',
        'js/chat/chatBox.js',
    ]],
    ['js/chat/notes.js', [
        'js/chat/notes/state.js',
        'js/chat/notes/api.js',
        'js/chat/notes/dom.js',
        'js/chat/notes/render.js',
        'js/chat/notes/manager.js',
        'js/chat/notes/manager-lifecycle.js',
        'js/chat/notes/manager-history.js',
        'js/chat/notes/sidebar.js',
        'js/chat/notes.js',
    ]],
    ['js/chat/splitScreen.js', [
        'js/chat/splitScreen/core.js',
        'js/chat/splitScreen/lifecycle.js',
        'js/chat/splitScreen/streaming.js',
        'js/chat/splitScreen/controls.js',
        'js/chat/splitScreen/routing.js',
        'js/chat/splitScreen.js',
    ]],
    ['css/admin/style.css', [
        'css/admin/style/base-and-components.css',
        'css/admin/style/stats-and-controls.css',
        'css/admin/style/provider-user-and-icon-management.css',
        'css/admin/style/groups-forms-and-responsive-tables.css',
        'css/admin/style/model-actions-and-local-models.css',
        'css/admin/style/access-rules-and-mobile-layout.css',
    ]],
]);

function dedentSource(source, spaces) {
    const prefix = ' '.repeat(spaces);
    return source
        .split('\n')
        .map((line) => (line.startsWith(prefix) ? line.slice(spaces) : line))
        .join('\n');
}

function normalizeFrontendRelativePath(relativePath) {
    return relativePath.replaceAll('\\', '/');
}

function readCanvasWidgetTestSource(sourcePaths, encoding) {
    const helperPaths = sourcePaths.slice(0, -1);
    const entryPath = sourcePaths.at(-1);
    const helperSource = helperPaths.map((sourcePath) => {
        const source = fs.readFileSync(path.join(FRONTEND_ROOT, sourcePath), encoding);
        const modulesDeclaration = source.indexOf('const modules = root.__omlorixCanvasWidgetModules');
        const contentStart = source.indexOf('\n', modulesDeclaration) + 1;
        const moduleExportStart = source.lastIndexOf('\n    modules.');
        const content = source.slice(contentStart, moduleExportStart);
        const factoryMatch = content.match(/    function create[A-Za-z]+Module\([^\n]*\) \{/);

        if (factoryMatch) {
            const factoryStart = factoryMatch.index;
            const bodyStart = factoryStart + factoryMatch[0].length;
            const returnStart = content.lastIndexOf('\n        return Object.freeze(');
            const prelude = content.slice(0, factoryStart);
            const body = content.slice(bodyStart, returnStart);
            return `${prelude}${dedentSource(body, 4)}`;
        }

        const lifecycleMarker = '    function initializeLifecycle(deps, state) {';
        const lifecycleStart = content.indexOf(lifecycleMarker);
        if (lifecycleStart >= 0) {
            const bodyStart = lifecycleStart + lifecycleMarker.length;
            const bodyEnd = content.lastIndexOf('\n    }');
            return dedentSource(content.slice(bodyStart, bodyEnd), 4);
        }

        return content;
    }).join('');

    return helperSource + fs.readFileSync(path.join(FRONTEND_ROOT, entryPath), encoding);
}

function readFrontendSource(filePath, encoding = 'utf8') {
    const relativePath = normalizeFrontendRelativePath(
        path.relative(FRONTEND_ROOT, path.resolve(filePath)),
    );
    const sourcePaths = SPLIT_SOURCE_PATHS.get(relativePath);

    if (!sourcePaths) {
        return fs.readFileSync(filePath, encoding);
    }

    if (relativePath === 'js/chat/canvas-widget.js') {
        return readCanvasWidgetTestSource(sourcePaths, encoding);
    }

    const source = sourcePaths
        .map((sourcePath) => fs.readFileSync(path.join(FRONTEND_ROOT, sourcePath), encoding))
        .join('');

    if (relativePath !== 'js/chat/splitScreen.js') {
        return source;
    }

    // splitScreen.js used one private IIFE before it was divided into classic
    // scripts. Its internal bindings are prefixed at runtime to retain that
    // privacy in the shared global lexical scope. Present their original names
    // to source-level tests so those tests keep validating the same component.
    const sourceWithOriginalNames = source.replace(/\bsplitScreenInternal([A-Z][A-Za-z0-9_]*)/g, (_match, suffix) => {
        if (suffix.length > 1 && /[A-Z_]/.test(suffix[1])) {
            return suffix;
        }
        return `${suffix[0].toLowerCase()}${suffix.slice(1)}`;
    });

    // The former private body lived one indentation level inside its IIFE.
    return sourceWithOriginalNames
        .split('\n')
        .map((line) => (line ? `    ${line}` : line))
        .join('\n');
}

module.exports = {
    SPLIT_SOURCE_PATHS,
    normalizeFrontendRelativePath,
    readFrontendSource,
};
