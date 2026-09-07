const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

function source(file) {
    return fs.readFileSync(path.join(__dirname, file), 'utf8');
}

function section(file, start, end) {
    const text = source(file);
    const first = text.indexOf(start);
    const last = text.indexOf(end, first);
    assert.ok(first >= 0 && last > first);
    return text.slice(first, last);
}

test('both presentation download menus expose the translated HTML source option', () => {
    const frontend = path.resolve(__dirname, '../..');
    assert.match(source('slide-presentation-widget.js'), /value: 'html', label: t\('slide_presentation_editor_html_source', 'HTML source'\)/);
    assert.match(source('slide-presentation-editor.js'), /value: 'html', label: tr\('slide_presentation_editor_html_source', 'HTML source'\)/);
    for (const locale of fs.readdirSync(path.join(frontend, 'i18n'))) {
        const file = path.join(frontend, 'i18n', locale, 'index.json');
        if (fs.existsSync(file)) assert.ok(JSON.parse(fs.readFileSync(file)).slide_presentation_editor_html_source, locale);
    }
});

test('HTML export saves pending edits without waiting for rendered derivatives', async () => {
    const exportSource = section('slide-presentation-editor.js', 'async function requestSharedExport(format)', "$('#btnPresent').addEventListener");
    for (const format of ['html', 'pptx', 'pdf', 'slides_zip']) {
        let release;
        const calls = [];
        const context = vm.createContext({
            state: { loaded: true }, stopTextEdit() {},
            flushServerSave(options) {
                assert.equal(options.renderAfter, format !== 'html');
                return new Promise(resolve => { release = resolve; });
            },
            editorController: { export: options => calls.push(options.format) },
        });
        vm.runInContext(exportSource, context);
        const pending = context.requestSharedExport(format);
        assert.deepEqual(calls, []);
        release(true);
        await pending;
        assert.deepEqual(calls, [format]);
        const failed = context.requestSharedExport(format);
        release(false);
        await failed;
        assert.equal(calls.length, 1, 'a failed save must block the export');
    }
});

function downloadContext() {
    const requests = [];
    const controls = [];
    const context = vm.createContext({
        _previewDownloadEnabled: false, _previewDownloadIsBusy: false,
        slidePresentationPresentationId: 'source/id', slidePresentationFileId: 'pptx-id',
        previewDownloadBtn: { getAttribute: key => key === 'data-file-id' ? 'pptx-id' : 'source/id' },
        _previewDownloadFormat: 'pptx',
        previewTitle: { textContent: 'Quarterly report' },
        t: (_, fallback) => fallback, tf: (_, fallback) => fallback,
        console,
        _editorFetchJson: async () => ({ canvas_revision: 1, render_revision: 1, file_id: 'pptx-id' }),
        window: { chatDownloadControls: {
            setDownloadBusy: options => controls.push(options),
            downloadBlobFromUrl: async (url, filename) => requests.push({ url, filename }),
        } },
    });
    vm.runInContext(section('slide-presentation-widget.js', '    function _setPreviewDownloadBusy(', '    function _setPreviewEditEnabled('), context);
    vm.runInContext(section('slide-presentation-widget.js', '    async function downloadPresentation(', '    // Each format is a direct download action'), context);
    return { context, requests, controls };
}

test('HTML remains selectable and downloads the canonical source while previews are unavailable', async () => {
    const { context, requests, controls } = downloadContext();
    context._setPreviewDownloadEnabled(false);
    assert.equal(controls.at(-1).enabled, true, 'the menu remains available for HTML');
    assert.deepEqual(['pptx', 'pdf', 'slides_zip', 'html'].map(context._canDownloadPresentation), [false, false, false, true]);
    context._previewDownloadFormat = 'html';
    await context.downloadPresentation();
    assert.deepEqual(requests, [{ url: '/api/v1/files/download?file_id=source%2Fid', filename: 'Quarterly-report.html' }]);
    await context.downloadPresentation('pptx');
    assert.equal(requests.length, 1, 'stale rendered files must remain blocked');
    context._setPreviewDownloadEnabled(true);
    await context.downloadPresentation('pptx');
    assert.equal(requests[1].url, '/api/v1/files/download?file_id=pptx-id');
    assert.equal(requests[1].filename, 'Quarterly-report.pptx');
    context._setPreviewDownloadBusy(true);
    await context.downloadPresentation('html');
    assert.equal(requests.length, 2, 'a second download cannot start while busy');
});

test('raster downloads render a stale saved revision without delaying HTML preview', async () => {
    const { context, requests } = downloadContext();
    const calls = [];
    context._editorFetchJson = async (url, options) => {
        calls.push({ url, options });
        return options
            ? { file_id: 'fresh-pptx', canvas_revision: 2, render_revision: 2 }
            : { file_id: 'old-pptx', canvas_revision: 2, render_revision: 1 };
    };
    context._setPreviewDownloadEnabled(true);
    await context.downloadPresentation('pptx');
    assert.equal(calls.length, 2);
    assert.equal(calls[1].options.body, JSON.stringify({ expected_revision: 2 }));
    assert.equal(requests[0].url, '/api/v1/files/download?file_id=fresh-pptx');
});
