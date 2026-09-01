const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');

function extractFunction(functionName) {
    const asyncStart = source.indexOf(`async function ${functionName}(`);
    const syncStart = source.indexOf(`function ${functionName}(`);
    const start = asyncStart === -1 ? syncStart : asyncStart;
    assert.notEqual(start, -1, `expected ${functionName} in chatBox.js`);

    const bodyStart = source.indexOf('{', start);
    assert.notEqual(bodyStart, -1, `expected ${functionName} body`);

    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') {
            depth += 1;
        } else if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }

    throw new Error(`Could not extract ${functionName}`);
}

function runDictationVisibility({ active = false, connecting = false, supported = true, featureEnabled = true } = {}) {
    const context = {
        button: {
            dataset: { featureEnabled: featureEnabled ? 'true' : 'false' },
            style: {},
            disabled: false,
        },
        active,
        connecting,
        supported,
    };

    vm.runInNewContext(
        [
            'const microphoneButtons = [button];',
            'function isRealtimeCallActive() { return active; }',
            'function isRealtimeCallConnecting() { return connecting; }',
            'function isDictationSupported() { return supported; }',
            extractFunction('updateDictationButtonVisibility'),
            'updateDictationButtonVisibility();',
        ].join('\n\n'),
        context,
        { filename: 'chatBox.js' },
    );

    return context.button;
}

test('dictation button is hidden and disabled while a realtime call is active', () => {
    const button = runDictationVisibility({ active: true });

    assert.equal(button.style.display, 'none');
    assert.equal(button.disabled, true);
});

test('dictation button is hidden while a realtime call is connecting', () => {
    const button = runDictationVisibility({ connecting: true });

    assert.equal(button.style.display, 'none');
    assert.equal(button.disabled, true);
});

test('dictation button returns after the realtime call ends when the feature is enabled', () => {
    const button = runDictationVisibility();

    assert.equal(button.style.display, 'flex');
    assert.equal(button.disabled, false);
});

test('dictation button remains hidden when dictation is disabled or unsupported', () => {
    assert.equal(runDictationVisibility({ featureEnabled: false }).style.display, 'none');

    const unsupportedButton = runDictationVisibility({ supported: false });
    assert.equal(unsupportedButton.style.display, 'none');
    assert.equal(unsupportedButton.disabled, true);
});

test('realtime state changes silently cancel an existing dictation session', () => {
    const context = {
        dictationState: { isRecording: true, isTranscribing: false },
        cancelOptions: null,
        toggleCalls: 0,
    };

    vm.runInNewContext(
        [
            'function isRealtimeCallActive() { return true; }',
            'function isRealtimeCallConnecting() { return false; }',
            'function cancelTranscription(options) { cancelOptions = options; }',
            'function toggleInputButtons() { toggleCalls += 1; }',
            extractFunction('handleRealtimeDictationStateChange'),
            'handleRealtimeDictationStateChange();',
        ].join('\n\n'),
        context,
        { filename: 'chatBox.js' },
    );

    assert.equal(JSON.stringify(context.cancelOptions), JSON.stringify({ notify: false }));
    assert.equal(context.toggleCalls, 1);
});

test('dictation start is guarded before and after microphone permission resolves', () => {
    const startSource = extractFunction('startDictationRecording');
    const microphoneRequestIndex = startSource.indexOf('await navigator.mediaDevices.getUserMedia({ audio: true })');
    const firstRealtimeGuardIndex = startSource.indexOf('isRealtimeCallActive()');
    const secondRealtimeGuardIndex = startSource.indexOf('isRealtimeCallActive()', firstRealtimeGuardIndex + 1);
    const sessionStartIndex = startSource.indexOf('const sessionId = beginDictationSession()');

    assert.ok(firstRealtimeGuardIndex !== -1 && firstRealtimeGuardIndex < microphoneRequestIndex);
    assert.ok(secondRealtimeGuardIndex > microphoneRequestIndex);
    assert.ok(secondRealtimeGuardIndex < sessionStartIndex);
    assert.match(startSource, /stream\.getTracks\(\)\.forEach\(\(track\) => track\.stop\(\)\)/);
});

test('dictation capability and realtime state synchronize microphone visibility', () => {
    const applySource = extractFunction('applyChatBoxFeatureVisibility');
    const toggleSource = extractFunction('toggleInputButtons');

    assert.match(applySource, /button\.dataset\.featureEnabled = 'true'/);
    assert.match(applySource, /updateDictationButtonVisibility\(\)/);
    assert.match(toggleSource, /updateDictationButtonVisibility\(\)/);
    assert.match(source, /window\.addEventListener\('realtime:state', handleRealtimeDictationStateChange\)/);
});
