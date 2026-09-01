const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

const CANVAS_WIDGET_PATH = path.join(__dirname, 'canvas-widget.js');
const CANVAS_FILE_LOADING_PATH = path.join(__dirname, 'canvas-widget', 'file-loading.js');

/**
 * Load the real Canvas file-loading boundary without constructing the full
 * browser widget. Keeping the production functions intact makes these tests
 * sensitive to both request headers and the streaming byte limit.
 */
function loadCanvasFileLoader(authedFetch) {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const start = source.indexOf('    const CANVAS_FILE_PREVIEW_MAX_BYTES = ');
    const end = source.indexOf('    function detectContentTypeFromFileName(', start);

    assert.notEqual(start, -1, 'expected the Canvas file preview size boundary');
    assert.notEqual(end, -1, 'expected the end of the Canvas file loader');

    const loaderSource = source.slice(start, end);
    const formatT = (_key, fallback, values = {}) => String(fallback).replace(
        /\{(\w+)\}/g,
        (_match, token) => String(values[token] ?? ''),
    );

    return Function(
        'window',
        'Utils',
        'formatT',
        't',
        'TextDecoder',
        `${loaderSource}
        return {
            CANVAS_FILE_PREVIEW_MAX_BYTES,
            CODE_EXECUTION_HTML_PREVIEW_MAX_BYTES,
            CanvasPreviewTooLargeError,
            getCanvasFilePreviewMaxBytes,
            getCanvasFileLoadFailureStatus,
            loadContentFromFile,
        };`,
    )(
        { authedFetch },
        { formatFileSize: (bytes) => `${bytes / (1024 * 1024)} MB` },
        formatT,
        (_key, fallback) => fallback,
        TextDecoder,
    );
}

test('Canvas exports and binds the preview size error across the split module boundary', () => {
    const entrySource = fs.readFileSync(CANVAS_WIDGET_PATH, 'utf8');
    const fileLoadingSource = fs.readFileSync(CANVAS_FILE_LOADING_PATH, 'utf8');

    assert.match(fileLoadingSource, /return Object\.freeze\(\{[\s\S]*CanvasPreviewTooLargeError,/);
    assert.match(entrySource, /const \{[\s\S]*CanvasPreviewTooLargeError,[\s\S]*\} = canvasWidgetModules\.fileLoading\.create/);
});

/**
 * Execute the production openPreviewForFile function with a small stateful
 * harness so cross-file response ordering can be tested without a browser DOM.
 */
function loadOpenPreviewRuntime({ loadContentFromFile, loadCanvasFileRecord, failInitialRender = false }) {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const start = source.indexOf('    async function openPreviewForFile(');
    const end = source.indexOf('\n\n    function handleToolCallEvent(', start);
    assert.notEqual(start, -1, 'expected openPreviewForFile');
    assert.notEqual(end, -1, 'expected the end of openPreviewForFile');
    const openPreviewSource = source.slice(start, end);

    return Function(
        'loadContentFromFile',
        'loadCanvasFileRecord',
        'failInitialRender',
        `const SPREADSHEET_CONTENT_TYPES = new Set(['csv', 'tsv', 'xlsx']);
        const filePreviewLoadTokens = new Map();
        const draftSavePromises = new Map();
        const draftMap = new Map();
        const rendered = [];
        let renderAttempts = 0;
        let activeDraftKey = '';
        let previewVisible = false;
        const normalizeContentType = (value) => String(value || 'markdown').toLowerCase();
        const detectContentTypeFromFileName = () => 'markdown';
        const resolveDisplayCanvasFileName = (name) => name;
        const resetScrollState = () => {};
        const setPanelVisible = (visible) => { previewVisible = Boolean(visible); };
        const t = (_key, fallback) => fallback;
        const getCanvasFilePreviewMaxBytes = () => 1024 * 1024;
        const loadSpreadsheetFromFile = async () => ({ bytes: new ArrayBuffer(0) });
        const getDraftEditState = () => ({
            baselineContent: '', draftContent: '', dirty: false, saving: false,
            autoSavePending: false, error: '',
        });
        const syncDraftEditStateFromServer = () => {};
        const setHtmlViewMode = () => {};
        const getCanvasFileLoadFailureStatus = (error, _key, fallback) => error?.message || fallback;
        const console = { error() {} };
        class CanvasPreviewTooLargeError extends Error {
            constructor(message = 'too large') {
                super(message);
                this.maxBytes = 1024 * 1024;
            }
        }
        function updateDraft(draftKey, updates, { activate = true } = {}) {
            const next = { key: draftKey, content: '', allowHtmlPreview: false, ...draftMap.get(draftKey), ...updates };
            draftMap.set(draftKey, next);
            if (activate) activeDraftKey = draftKey;
            return next;
        }
        function renderDraft(draft) {
            renderAttempts += 1;
            if (failInitialRender && renderAttempts === 1) {
                throw new Error('loading shell failed');
            }
            rendered.push({
                key: draft.key,
                content: draft.content,
                statusKind: draft.statusKind,
                loadError: draft.loadError,
            });
        }
        ${openPreviewSource}
        return {
            openPreviewForFile,
            activeDraftKey: () => activeDraftKey,
            draft: (key) => draftMap.get(key),
            close: () => setPanelVisible(false),
            rendered,
        };`,
    )(
        loadContentFromFile,
        loadCanvasFileRecord,
        failInitialRender,
    );
}

/**
 * Build the subset of a Fetch Response used by the production loader.
 */
function createStreamingResponse({
    chunks,
    status = 206,
    headers = {},
    onBodyCancel = () => {},
    onReaderCancel = () => {},
    onRead = () => {},
}) {
    const normalizedHeaders = new Map(
        Object.entries(headers).map(([key, value]) => [key.toLowerCase(), String(value)]),
    );
    let chunkIndex = 0;

    return {
        ok: status >= 200 && status < 300,
        status,
        headers: {
            get(name) {
                return normalizedHeaders.get(String(name).toLowerCase()) ?? null;
            },
        },
        body: {
            async cancel() {
                onBodyCancel();
            },
            getReader() {
                return {
                    async read() {
                        onRead();
                        if (chunkIndex >= chunks.length) {
                            return { done: true, value: undefined };
                        }
                        const value = chunks[chunkIndex];
                        chunkIndex += 1;
                        return { done: false, value };
                    },
                    async cancel() {
                        onReaderCancel();
                    },
                    releaseLock() {},
                };
            },
        },
    };
}

test('Canvas file loading requests one byte beyond the preview limit and preserves small files', async () => {
    const calls = [];
    const content = new TextEncoder().encode('# Safe preview');
    const response = createStreamingResponse({
        chunks: [content],
        headers: {
            'Content-Length': content.byteLength,
            'Content-Range': `bytes 0-${content.byteLength - 1}/${content.byteLength}`,
        },
    });
    const loader = loadCanvasFileLoader(async (...args) => {
        calls.push(args);
        return response;
    });

    const loaded = await loader.loadContentFromFile('safe-markdown');

    assert.equal(loaded, '# Safe preview');
    assert.equal(calls.length, 1);
    assert.equal(calls[0][1].cache, 'no-store');
    assert.equal(calls[0][1].headers['Cache-Control'], 'no-cache');
    assert.equal(
        calls[0][1].headers.Range,
        `bytes=0-${loader.CANVAS_FILE_PREVIEW_MAX_BYTES}`,
    );
});

test('Canvas grants the larger bounded budget only to code-execution HTML', async () => {
    const calls = [];
    // Plotly's embedded runtime makes a generated document larger than the
    // ordinary 1 MiB budget. A compact synthetic payload exercises that exact
    // boundary without checking a multi-megabyte fixture into the repository.
    const generatedContent = new Uint8Array((1024 * 1024) + 1).fill(65);
    const loader = loadCanvasFileLoader(async (...args) => {
        calls.push(args);
        return createStreamingResponse({
            chunks: [generatedContent],
            headers: {
                'Content-Length': generatedContent.byteLength,
                'Content-Range': `bytes 0-${generatedContent.byteLength - 1}/${generatedContent.byteLength}`,
            },
        });
    });
    const eligibleRecord = {
        meta: {
            origin: 'assistant',
            code_execution: true,
        },
    };

    assert.equal(
        loader.getCanvasFilePreviewMaxBytes('html', eligibleRecord),
        loader.CODE_EXECUTION_HTML_PREVIEW_MAX_BYTES,
    );
    assert.equal(
        loader.getCanvasFilePreviewMaxBytes('markdown', eligibleRecord),
        loader.CANVAS_FILE_PREVIEW_MAX_BYTES,
    );
    assert.equal(
        loader.getCanvasFilePreviewMaxBytes('html', { meta: { code_execution: true } }),
        loader.CANVAS_FILE_PREVIEW_MAX_BYTES,
    );
    assert.equal(
        loader.getCanvasFilePreviewMaxBytes('html', { meta: { origin: 'assistant', code_execution: 'true' } }),
        loader.CANVAS_FILE_PREVIEW_MAX_BYTES,
    );

    const loaded = await loader.loadContentFromFile(
        'generated-plotly-html',
        loader.CODE_EXECUTION_HTML_PREVIEW_MAX_BYTES,
    );
    assert.equal(loaded.length, generatedContent.byteLength);
    assert.equal(
        calls[0][1].headers.Range,
        `bytes=0-${loader.CODE_EXECUTION_HTML_PREVIEW_MAX_BYTES}`,
    );
});

test('Canvas file loading rejects an oversized ranged response before reading its body', async () => {
    let bodyCancelled = false;
    let readCalled = false;
    const loader = loadCanvasFileLoader(async () => createStreamingResponse({
        chunks: [],
        headers: {
            'Content-Length': loader.CANVAS_FILE_PREVIEW_MAX_BYTES + 1,
            'Content-Range': `bytes 0-${loader.CANVAS_FILE_PREVIEW_MAX_BYTES}/${loader.CANVAS_FILE_PREVIEW_MAX_BYTES + 50}`,
        },
        onBodyCancel: () => {
            bodyCancelled = true;
        },
        onRead: () => {
            readCalled = true;
        },
    }));

    await assert.rejects(
        loader.loadContentFromFile('oversized-markdown'),
        (error) => {
            assert.equal(error.name, 'CanvasPreviewTooLargeError');
            assert.match(error.message, /too large to preview/i);
            return true;
        },
    );
    assert.equal(bodyCancelled, true);
    assert.equal(readCalled, false);
});

test('Canvas file loading enforces the byte limit when the server ignores Range', async () => {
    let readerCancelled = false;
    const oversizedChunk = new Uint8Array((1024 * 1024) + 1);
    const loader = loadCanvasFileLoader(async () => createStreamingResponse({
        status: 200,
        chunks: [oversizedChunk],
        onReaderCancel: () => {
            readerCancelled = true;
        },
    }));

    await assert.rejects(
        loader.loadContentFromFile('range-ignored-markdown'),
        { name: 'CanvasPreviewTooLargeError' },
    );
    assert.equal(readerCancelled, true);
});

test('Canvas file loading preserves empty previews and surfaces the size-limit message', async () => {
    let bodyCancelled = false;
    const loader = loadCanvasFileLoader(async () => createStreamingResponse({
        status: 416,
        chunks: [],
        headers: {
            'Content-Range': 'bytes */0',
        },
        onBodyCancel: () => {
            bodyCancelled = true;
        },
    }));

    assert.equal(await loader.loadContentFromFile('empty-markdown'), '');
    assert.equal(bodyCancelled, true);

    const tooLargeError = new loader.CanvasPreviewTooLargeError();
    assert.equal(
        loader.getCanvasFileLoadFailureStatus(
            tooLargeError,
            'files_preview_load_error',
            'Failed to load preview',
        ),
        'This file is too large to preview. Previewing is limited to 1 MB.',
    );
});

test('Canvas renders file-load details in the body and keeps the header concise', () => {
    const source = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const css = readFrontendSource(
        path.join(__dirname, '../../css/chat/canvas-widget.css'),
        'utf8',
    );

    assert.match(source, /function createCanvasFileLoadErrorView\(draft\)/);
    assert.match(source, /errorView\.setAttribute\('role', 'alert'\)/);
    assert.match(source, /className = 'secondary-button canvas-file-load-error-download'/);
    assert.match(source, /status: t\('files_preview_load_error', 'Failed to load preview'\)/);
    assert.match(source, /message: failureMessage/);
    assert.match(source, /fileRecord = await loadCanvasFileRecord\(fileId\);[\s\S]*getCanvasFilePreviewMaxBytes\(detectedType, fileRecord\);[\s\S]*loadContentFromFile\(fileId, maxBytes\)/);
    assert.match(source, /previewPanel\.setAttribute\('data-load-error', hasLoadError \? 'true' : 'false'\)/);
    const renderStart = source.indexOf('    function renderDraft(draft)');
    const renderEnd = source.indexOf('\n\n    function clearHtmlRenderTimer(', renderStart);
    const renderSource = source.slice(renderStart, renderEnd);
    assert.ok(
        renderSource.indexOf('const hasLoadError = Boolean(draft.loadError?.message)')
            < renderSource.indexOf('renderSpreadsheetDraft(draft, draftKey, contentType)'),
        'spreadsheet failures must be detected before spreadsheet rendering can return early',
    );
    assert.match(renderSource, /if \(!hasLoadError[\s\S]*SPREADSHEET_CONTENT_TYPES\.has\(contentType\)/);
    assert.match(css, /\.canvas-file-load-error-card\s*\{/);
    assert.match(css, /\[data-load-error="true"\]\[data-content-type\] \.canvas-html-view-toggle/);
});

test('a stale file preview success or failure cannot replace the newer active file', async () => {
    const deferred = () => {
        let resolve;
        let reject;
        const promise = new Promise((resolvePromise, rejectPromise) => {
            resolve = resolvePromise;
            reject = rejectPromise;
        });
        return { promise, resolve, reject };
    };

    for (const staleOutcome of ['success', 'failure']) {
        const fileA = deferred();
        const fileB = deferred();
        const loads = new Map([
            ['file-a', fileA.promise],
            ['file-b', fileB.promise],
        ]);
        const runtime = loadOpenPreviewRuntime({
            loadContentFromFile: (fileId) => loads.get(fileId),
            loadCanvasFileRecord: async () => null,
        });

        const openingA = runtime.openPreviewForFile('file-a', 'a.md', 'markdown');
        const openingB = runtime.openPreviewForFile('file-b', 'b.md', 'markdown');
        fileB.resolve('newer file B');
        await openingB;
        const renderedAfterB = runtime.rendered.length;

        if (staleOutcome === 'success') fileA.resolve('stale file A');
        else fileA.reject(new Error('stale file A failed'));
        await openingA;

        assert.equal(runtime.activeDraftKey(), 'file-b', staleOutcome);
        assert.equal(runtime.draft('file-b').content, 'newer file B', staleOutcome);
        assert.equal(runtime.draft('file-b').loadError, null, staleOutcome);
        assert.equal(runtime.rendered.length, renderedAfterB, staleOutcome);
        assert.equal(runtime.rendered.at(-1).key, 'file-b', staleOutcome);
        assert.equal(runtime.rendered.at(-1).content, 'newer file B', staleOutcome);
    }
});

test('a loading-shell render failure does not prevent the Markdown file from opening', async () => {
    const runtime = loadOpenPreviewRuntime({
        loadContentFromFile: async () => '# Loaded document',
        loadCanvasFileRecord: async () => null,
        failInitialRender: true,
    });

    await assert.doesNotReject(
        runtime.openPreviewForFile('file-markdown', 'document.md', 'markdown'),
    );
    assert.equal(runtime.draft('file-markdown').content, '# Loaded document');
    assert.equal(runtime.rendered.at(-1).statusKind, 'saved');
});

test('dismissing a loading preview prevents its late response from rendering', async () => {
    let resolveLoad;
    const pendingLoad = new Promise((resolve) => { resolveLoad = resolve; });
    const runtime = loadOpenPreviewRuntime({
        loadContentFromFile: async () => pendingLoad,
        loadCanvasFileRecord: async () => null,
    });

    const opening = runtime.openPreviewForFile('file-markdown', 'document.md', 'markdown');
    const renderedBeforeClose = runtime.rendered.length;
    runtime.close();
    resolveLoad('# Late document');
    await opening;

    assert.equal(runtime.rendered.length, renderedBeforeClose);
    assert.equal(runtime.draft('file-markdown').content, '');
});
