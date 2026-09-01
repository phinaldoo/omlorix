const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readFrontendSource } = require('../splitSource.cjs');

function extractFunction(source, functionName) {
    const asyncStart = source.indexOf(`async function ${functionName}(`);
    const start = asyncStart >= 0
        ? asyncStart
        : source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName} in chatBox.js`);

    const bodyStart = source.indexOf('{', start);
    assert.notEqual(bodyStart, -1, `expected ${functionName} body`);

    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') {
            depth += 1;
        } else if (source[index] === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }

    throw new Error(`could not extract ${functionName}`);
}

function createCaptureHarness({ encodeImmediately = true, playError = null } = {}) {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const videoListeners = new Map();
    const trackListeners = new Map();
    const readyTimers = new Map();
    const stopCalls = [0, 0];
    let animationFrameCalls = 0;
    let drawCalls = 0;
    let encodeCallback = null;

    const tracks = [
        {
            readyState: 'live',
            addEventListener(type, callback) {
                trackListeners.set(type, callback);
            },
            getSettings() {
                return { width: 1280, height: 720 };
            },
            removeEventListener(type, callback) {
                if (trackListeners.get(type) === callback) {
                    trackListeners.delete(type);
                }
            },
            stop() {
                stopCalls[0] += 1;
                this.readyState = 'ended';
            },
        },
        {
            stop() {
                stopCalls[1] += 1;
            },
        },
    ];
    const stream = {
        getVideoTracks() {
            return [tracks[0]];
        },
        getTracks() {
            return tracks;
        },
    };
    const video = {
        muted: false,
        playsInline: false,
        readyState: 0,
        srcObject: null,
        videoWidth: 1280,
        videoHeight: 720,
        addEventListener(type, callback) {
            videoListeners.set(type, callback);
        },
        removeEventListener(type, callback) {
            if (videoListeners.get(type) === callback) {
                videoListeners.delete(type);
            }
        },
        play() {
            return playError ? Promise.reject(playError) : Promise.resolve();
        },
        pause() {},
    };
    const canvas = {
        width: 0,
        height: 0,
        getContext() {
            return {
                drawImage(sourceVideo, x, y, width, height) {
                    assert.equal(sourceVideo, video);
                    assert.deepEqual([x, y, width, height], [0, 0, 1280, 720]);
                    drawCalls += 1;
                },
            };
        },
        toBlob(callback, type) {
            assert.equal(type, 'image/png');
            assert.deepEqual(stopCalls, [1, 1], 'capture tracks must stop before PNG encoding');
            if (encodeImmediately) {
                callback({ type });
            } else {
                encodeCallback = callback;
            }
        },
    };

    class FakeFile {
        constructor(parts, name, options) {
            this.parts = parts;
            this.name = name;
            this.type = options.type;
            this.lastModified = options.lastModified;
        }
    }

    const context = {
        Date,
        Error,
        File: FakeFile,
        Math,
        Number,
        Promise,
        String,
        buildScreenCaptureFilename() {
            return 'screen-capture-test.png';
        },
        document: {
            createElement(tagName) {
                if (tagName === 'video') return video;
                if (tagName === 'canvas') return canvas;
                throw new Error(`unexpected element: ${tagName}`);
            },
        },
        getChatI18nString(_key, fallback) {
            return fallback;
        },
        isQuickScreenCaptureSupported() {
            return true;
        },
        navigator: {
            mediaDevices: {
                async getDisplayMedia() {
                    return stream;
                },
            },
        },
        requestAnimationFrame() {
            animationFrameCalls += 1;
            // Simulate Chrome pausing animation frames in a background tab.
        },
        clearTimeout(timerId) {
            readyTimers.delete(timerId);
        },
        setTimeout(callback) {
            const timerId = readyTimers.size + 1;
            readyTimers.set(timerId, callback);
            return timerId;
        },
    };
    context.globalThis = context;

    vm.runInNewContext(
        [
            extractFunction(source, 'captureScreenAsFile'),
            'this.captureScreenAsFile = captureScreenAsFile;',
        ].join('\n\n'),
        context,
        { filename: 'chatBox.js' },
    );

    return {
        captureScreenAsFile: context.captureScreenAsFile,
        completeEncoding() {
            assert.equal(typeof encodeCallback, 'function');
            encodeCallback({ type: 'image/png' });
        },
        dispatchVideoEvent(type) {
            if (type === 'loadeddata') {
                video.readyState = 2;
            }
            videoListeners.get(type)?.();
        },
        dispatchTrackEvent(type) {
            if (type === 'ended') {
                tracks[0].readyState = 'ended';
            }
            trackListeners.get(type)?.();
        },
        fireReadyTimeout() {
            const callback = readyTimers.values().next().value;
            assert.equal(typeof callback, 'function');
            callback();
        },
        get animationFrameCalls() {
            return animationFrameCalls;
        },
        get drawCalls() {
            return drawCalls;
        },
        stopCalls,
    };
}

const nextTask = () => new Promise((resolve) => setImmediate(resolve));

test('quick screenshot completes and stops every track while animation frames are paused', async () => {
    const harness = createCaptureHarness();
    const capturePromise = harness.captureScreenAsFile();

    await nextTask();
    harness.dispatchVideoEvent('loadedmetadata');
    harness.dispatchVideoEvent('loadeddata');
    await nextTask();

    assert.equal(harness.drawCalls, 1);
    assert.deepEqual(harness.stopCalls, [1, 1]);
    assert.equal(harness.animationFrameCalls, 0);

    const file = await capturePromise;
    assert.equal(file.name, 'screen-capture-test.png');
    assert.equal(file.type, 'image/png');
});

test('quick screenshot releases capture before asynchronous PNG encoding finishes', async () => {
    const harness = createCaptureHarness({ encodeImmediately: false });
    let captureSettled = false;
    const capturePromise = harness.captureScreenAsFile().then((file) => {
        captureSettled = true;
        return file;
    });

    await nextTask();
    harness.dispatchVideoEvent('loadedmetadata');
    harness.dispatchVideoEvent('loadeddata');
    await nextTask();

    assert.deepEqual(harness.stopCalls, [1, 1]);
    assert.equal(captureSettled, false);

    harness.completeEncoding();
    const file = await capturePromise;
    assert.equal(file.name, 'screen-capture-test.png');
});

test('quick screenshot rejects and releases capture when readiness cannot complete', async (t) => {
    await t.test('the display track ends before video data arrives', async () => {
        const harness = createCaptureHarness();
        const rejection = assert.rejects(
            harness.captureScreenAsFile(),
            (error) => error.name === 'AbortError' && /cancelled/i.test(error.message),
        );

        await nextTask();
        harness.dispatchTrackEvent('ended');
        await rejection;
        assert.deepEqual(harness.stopCalls, [1, 1]);
    });

    await t.test('video playback rejects', async () => {
        const playError = new Error('Playback failed.');
        const harness = createCaptureHarness({ playError });

        await assert.rejects(harness.captureScreenAsFile(), /Playback failed/);
        assert.deepEqual(harness.stopCalls, [1, 1]);
    });

    await t.test('video readiness times out', async () => {
        const harness = createCaptureHarness();
        const rejection = assert.rejects(harness.captureScreenAsFile(), /Failed to read captured stream/);

        await nextTask();
        harness.fireReadyTimeout();
        await rejection;
        assert.deepEqual(harness.stopCalls, [1, 1]);
    });
});
