const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const RUNTIME_PATH = path.join(__dirname, 'canvasHtmlPreview.js');

function loadRuntime() {
    const source = fs.readFileSync(RUNTIME_PATH, 'utf8');
    const context = {
        CustomEvent: class CustomEvent {},
        Date,
        HTMLIFrameElement: class HTMLIFrameElement {},
        Object,
        String,
        URL,
        WeakMap,
        window: {
            addEventListener() {},
            location: { origin: 'https://omlorix.example' },
        },
    };
    context.globalThis = context;
    vm.createContext(context);
    vm.runInContext(source, context, { filename: RUNTIME_PATH });
    return { runtime: context.window.OmlorixCanvasHtmlPreview, source };
}

test('canvas HTML runtime detects active and external capabilities', () => {
    const { runtime } = loadRuntime();

    assert.deepEqual(
        { ...runtime.analyze('<button onclick="run()">Go</button><script>fetch("https://api.example/x")</script>') },
        { scripts: true, externalContent: true },
    );
    assert.deepEqual(
        { ...runtime.analyze('<main><details><summary>More</summary></details></main>') },
        { scripts: false, externalContent: false },
    );
    assert.deepEqual(
        { ...runtime.analyze("<script>location.href = atob('aHR0cHM6Ly9hdHRhY2tlci5leGFtcGxl')</script>") },
        { scripts: true, externalContent: true },
        'arbitrary scripts must be treated as external-content capable even when the URL is hidden',
    );
});

test('canvas HTML runtime never enables authored scripts without external-content permission', () => {
    const { runtime } = loadRuntime();

    assert.deepEqual(
        { ...runtime.normalizePermissions({ allowScripts: true, allowExternalContent: false }) },
        { allowScripts: false, allowExternalContent: false, trustedLocalScripts: false },
    );
    assert.deepEqual(
        { ...runtime.normalizePermissions({ allowScripts: true, allowExternalContent: true }) },
        { allowScripts: true, allowExternalContent: true, trustedLocalScripts: false },
    );
    assert.deepEqual(
        { ...runtime.normalizePermissions({
            allowScripts: true,
            allowExternalContent: false,
            trustedLocalScripts: true,
            allowEval: true,
            relayVisualizationMessages: true,
            hydrateAuthenticatedFiles: false,
        }) },
        { allowScripts: false, allowExternalContent: false, trustedLocalScripts: false },
        'trusted local scripts must fail closed when eval is requested',
    );
    assert.deepEqual(
        { ...runtime.normalizePermissions({
            allowScripts: true,
            allowExternalContent: false,
            trustedLocalScripts: true,
            allowEval: false,
            relayVisualizationMessages: true,
            hydrateAuthenticatedFiles: false,
        }) },
        { allowScripts: true, allowExternalContent: false, trustedLocalScripts: true },
        'the host-generated static visualization bridge remains available without authored scripts',
    );
});

test('canvas HTML runtime lists concrete external connection URLs', () => {
    const { runtime } = loadRuntime();
    const resources = runtime.collectExternalResources(`
        <link rel="stylesheet" href="https://cdn.example/theme.css">
        <link rel="preconnect" href="https://assets.example">
        <img srcset="https://images.example/card.png 1x, /local-card.png 2x">
        <a href="https://docs.example/help">Help</a>
        <style>@font-face { src: url('//fonts.example/font.woff2'); }</style>
        <script>
            fetch('https://api.example/items');
            const socket = new WebSocket('wss://events.example/live');
            import widget from 'https://modules.example/widget.js';
        </script>
    `);

    assert.deepEqual(Array.from(resources), [
        'https://api.example/items',
        'https://assets.example/',
        'https://cdn.example/theme.css',
        'https://fonts.example/font.woff2',
        'https://images.example/card.png',
        'https://modules.example/widget.js',
        'wss://events.example/live',
    ]);
});

test('canvas HTML runtime excludes inert, local, and ordinary navigation URLs', () => {
    const { runtime } = loadRuntime();

    assert.deepEqual(Array.from(runtime.collectExternalResources(`
        <!-- <img src="https://comment.example/tracker.png"> -->
        <img src="/api/v1/files/download?file_id=local">
        <img src="data:image/png;base64,AA==">
        <a href="https://docs.example/help">Help</a>
        <script type="application/json">{"url":"https://data.example/not-loaded"}</script>
    `)), []);
});

test('canvas HTML runtime mounts only the trusted proxy as same-origin', () => {
    const { source } = loadRuntime();

    assert.match(source, /PROXY_SANDBOX = 'allow-scripts allow-same-origin allow-modals allow-downloads allow-forms allow-popups'/);
    assert.match(source, /frame\.removeAttribute\('srcdoc'\)/);
    assert.match(source, /frame\.src = PROXY_URL/);
    assert.match(source, /target\.postMessage\(\{[\s\S]*allowExternalContent: state\.allowExternalContent/);
    assert.match(source, /allowAuthenticatedFileHydration: state\.hydrateAuthenticatedFiles/);
});

test('canvas HTML runtime forwards the strict visualization proxy options', () => {
    const { source } = loadRuntime();

    assert.match(source, /allowEval: state\.allowEval/);
    assert.match(source, /relayVisualizationMessages: state\.relayVisualizationMessages/);
    assert.match(source, /trustedLocalScripts: state\.trustedLocalScripts/);
    assert.match(source, /state\.allowEval = state\.allowScripts && !permissions\.trustedLocalScripts/);
    assert.match(source, /state\.relayVisualizationMessages = options\.relayVisualizationMessages === true/);
});
