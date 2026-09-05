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
    assert.match(fs.readFileSync(path.join(frontend, 'index.html'), 'utf8'), /value="html" data-i18n="slide_presentation_editor_html_source"/);
    assert.match(source('slide-presentation-editor.js'), /option value="html"/);
    for (const locale of fs.readdirSync(path.join(frontend, 'i18n'))) {
        const file = path.join(frontend, 'i18n', locale, 'index.json');
        if (fs.existsSync(file)) assert.ok(JSON.parse(fs.readFileSync(file)).slide_presentation_editor_html_source, locale);
    }
});

test('HTML export saves pending edits without waiting for rendered derivatives', async () => {
    const exportSource = section('slide-presentation-editor.js', 'async function requestSharedExport()', "$('#btnPresent').addEventListener");
    for (const format of ['html', 'pptx', 'pdf', 'slides_zip']) {
        const select = { value: format };
        let release;
        const calls = [];
        const context = vm.createContext({
            state: { loaded: true }, $: () => select, stopTextEdit() {},
            flushServerSave(options) {
                assert.equal(options.renderAfter, select.value !== 'html');
                return new Promise(resolve => { release = resolve; });
            },
            editorController: { export: options => calls.push(options.format) },
        });
        vm.runInContext(exportSource, context);
        const pending = context.requestSharedExport();
        assert.deepEqual(calls, []);
        select.value = 'changed-during-save';
        release(true);
        await pending;
        assert.deepEqual(calls, [format]);
        const failed = context.requestSharedExport();
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
        previewDownloadBtnDefaultHtml: '',
        previewDownloadBtn: { getAttribute: key => key === 'data-file-id' ? 'pptx-id' : 'source/id' },
        previewDownloadFormat: { value: 'pptx', options: ['pptx', 'pdf', 'slides_zip', 'html'].map(value => ({ value })) },
        previewTitle: { textContent: 'Quarterly report' },
        t: (_, fallback) => fallback, tf: (_, fallback) => fallback,
        console,
        window: { chatDownloadControls: {
            setDownloadBusy: options => controls.push(options), syncDownloadFormatSelect() {},
            getSelectedDownloadFormat: select => select.value,
            downloadBlobFromUrl: async (url, filename) => requests.push({ url, filename }),
        } },
    });
    vm.runInContext(section('slide-presentation-widget.js', '    function _setPreviewDownloadBusy(', '    function _setPreviewEditEnabled('), context);
    vm.runInContext(section('slide-presentation-widget.js', '    async function downloadPresentation(', '    // Both the sidebar button'), context);
    return { context, requests, controls };
}

test('HTML remains selectable and downloads the canonical source while previews are unavailable', async () => {
    const { context, requests, controls } = downloadContext();
    context._setPreviewDownloadEnabled(false);
    assert.equal(context.previewDownloadFormat.disabled, false);
    assert.deepEqual(context.previewDownloadFormat.options.map(option => option.disabled), [true, true, true, false]);
    assert.equal(controls.at(-1).enabled, false);
    context.previewDownloadFormat.value = 'html';
    context._syncPreviewDownloadControls();
    assert.equal(controls.at(-1).enabled, true);
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
