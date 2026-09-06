const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

function section(file, start, end) {
    const source = fs.readFileSync(path.join(__dirname, file), 'utf8');
    const first = source.indexOf(start);
    const last = source.indexOf(end, first);
    assert.ok(first >= 0 && last > first);
    return source.slice(first, last);
}

const openSource = section('slide-presentation-widget.js', '    async function openSlideshow(', '    function closeSlideshow(');

function playbackContext(fetchSource) {
    const node = () => ({ classList: { add() {}, remove() {} }, setAttribute() {}, replaceChildren() {}, focus() {} });
    const displayed = [];
    const context = vm.createContext({
        AbortController, clearTimeout() {}, ssRuntimeReadyTimer: null, ssRuntimeFrame: null, ssRuntimeChannel: null, slidePresentationPresentationId: 'deck-a', slidePresentationSlides: [],
        slidePresentationCurrentIndex: 0, ssLoadToken: 0, ssSourceController: null,
        ssOpen: false, ssNavigationToken: 0, ssSlideCount: 0, ssDocument: null,
        ssPreviouslyFocused: null, ssScaleObserver: null, ssIndex: 0,
        ssOverlay: node(), ssViewport: node(), ssLoader: node(), ssStage: node(), ssClose: node(),
        ssLoaderBar: null, ssLoaderCount: null, ssLoaderText: null,
        document: { activeElement: null }, window: {},
        t: (_, fallback) => fallback, _editorFetchJson: fetchSource,
        _buildProgressDots() {}, _ssUpdateCounter() {}, _hideSlideshowControlsImmediately() {}, _setInteractivePreviewVisibility() {},
        _prepareSlideshowDocument(html) { displayed.push(html); }, _ssGoTo() {},
        _mountInteractiveSlideshow(payload) { displayed.push(payload.html); },
    });
    vm.runInContext(openSource, context);
    return { context, displayed };
}

test('closing or replacing a deck during source loading cannot revive its slideshow', async () => {
    const requests = [];
    const { context, displayed } = playbackContext((url, options) => {
        assert.equal(url, '/api/v1/presentations/deck-a/playback');
        return new Promise(resolve => { requests.push({ resolve, signal: options.signal }); });
    });
    const first = context.openSlideshow();
    const second = context.openSlideshow();
    assert.equal(requests[0].signal.aborted, true);
    assert.equal(requests[1].signal.aborted, false);
    requests[0].resolve({ html: 'superseded source' });
    await first;
    assert.deepEqual(displayed, []);
    context.ssOpen = false;
    context.ssLoadToken += 1;
    requests[1].resolve({ html: 'late source' });
    await second;
    assert.deepEqual(displayed, []);
    // A stale response must also be ignored when another deck owns the UI.
    context.ssOpen = true;
    const third = context.openSlideshow();
    context.slidePresentationPresentationId = 'deck-b';
    requests[2].resolve({ html: 'wrong deck' });
    await third;
    assert.deepEqual(displayed, []);
});

test('presenting a saved deck reads fresh HTML each time without fetching images', async () => {
    let revision = 1;
    const { context, displayed } = playbackContext(async () => ({ html: `revision ${revision++}` }));
    await context.openSlideshow();
    await context.openSlideshow({ slideIndex: 2 });
    assert.deepEqual(displayed, ['revision 1', 'revision 2']);
    assert.equal(context.ssIndex, 2);
});

test('editor Present waits for saving but not an in-flight image render', async () => {
    const source = section('slide-presentation-editor.js', 'async function requestSharedPresent()', 'async function requestSharedExport()');
    const calls = [];
    let saved = false;
    let releaseSave;
    const renderPromise = new Promise(() => {});
    const context = vm.createContext({
        state: { loaded: true, active: 2 },
        server: { revision: 3, openedRevision: 1, renderRevision: 1, renderInFlight: renderPromise },
        stopTextEdit() {}, clearTimeout() {},
        flushServerSave: () => new Promise(resolve => { releaseSave = () => { saved = true; resolve(true); }; }),
        editorController: { present(options) { assert.ok(saved); calls.push(options); } },
    });
    vm.runInContext(source, context);
    const pending = context.requestSharedPresent();
    assert.equal(calls.length, 0);
    releaseSave();
    await pending;
    assert.equal(calls.length, 1);
    assert.equal(calls[0].slideIndex, 2);
    assert.equal(calls[0].refreshContext.canvasRevision, 3);
    assert.equal(calls[0].refreshContext.renderPromise, renderPromise);
});
