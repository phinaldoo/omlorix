const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readFrontendSource } = require('../splitSource.cjs');
const { readStreamMessagesSource } = require('./messages/source.cjs');


const readSource = (relativePath) => readFrontendSource(
    path.join(__dirname, relativePath),
    'utf8',
);

const loadLiveTranscriptionController = () => {
    const context = {
        window: {
            location: {
                protocol: 'https:',
                host: 'omlorix.example',
            },
        },
        navigator: {
            mediaDevices: {
                getUserMedia() {},
            },
        },
        localStorage: {
            getItem() {
                return 'true';
            },
        },
        WebSocket: class FakeWebSocket {},
        AudioContext: class FakeAudioContext {},
        Float32Array,
        Uint8Array,
        DataView,
        Map,
        Promise,
        Error,
        String,
        Number,
        Math,
        JSON,
        setTimeout,
        clearTimeout,
        btoa() {
            return '';
        },
    };
    context.window.WebSocket = context.WebSocket;
    context.window.AudioContext = context.AudioContext;
    vm.runInNewContext(
        readSource('./liveTranscription.js'),
        context,
        { filename: 'liveTranscription.js' },
    );
    return context.window.LiveTranscription;
};


test('live transcription controller loads before both dictation composers', () => {
    const indexSource = readSource('../../index.html');
    const controllerIndex = indexSource.indexOf('/js/chat/liveTranscription.js');
    const chatBoxIndex = indexSource.indexOf('/js/chat/chatBox.js');
    const messageStreamIndex = indexSource.indexOf('/js/chat/messages/shared.js');

    assert.ok(controllerIndex >= 0);
    assert.ok(controllerIndex < chatBoxIndex);
    assert.ok(controllerIndex < messageStreamIndex);
});


test('live transcription uses the authenticated same-origin proxy and PCM24k', () => {
    const controllerSource = readSource('./liveTranscription.js');

    assert.match(
        controllerSource,
        /\/api\/v1\/realtime\/transcription\/live/,
    );
    assert.match(controllerSource, /const TARGET_SAMPLE_RATE = 24000/);
    assert.doesNotMatch(controllerSource, /Authorization|api[_-]?key/i);
});


test('xAI finalized chunks remain visible while the next sentence changes', () => {
    const controllerSource = readSource('./liveTranscription.js');

    assert.match(controllerSource, /if \(payload\.is_final\)/);
    assert.match(controllerSource, /item\.committed = \[item\.committed, transcript\]/);
    assert.match(controllerSource, /item\?\.final \|\| item\?\.delta/);
    assert.match(controllerSource, /return `\$\{committed\} \$\{tail\}`/);
    assert.match(controllerSource, /tail\.startsWith\(`\$\{committed\} `\)/);
    assert.match(
        controllerSource,
        /payload\.transcript \|\| item\.committed \|\| item\.delta/,
    );
});


test('main and edit composers prefer live transcription when it is ready', () => {
    const chatBoxSource = readSource('./chatBox.js');
    const messageStreamSource = readStreamMessagesSource();

    for (const source of [chatBoxSource, messageStreamSource]) {
        assert.match(source, /LiveTranscription\?\.isReady/);
        assert.match(source, /LiveTranscription\?\.isSupported/);
        assert.match(source, /LiveTranscription\.start/);
        assert.match(source, /LiveTranscription\.stop/);
    }
});

test('main composer binds dictation before asynchronous setup resolves', () => {
    const chatBoxSource = readSource('./chatBox.js');
    const initializationStart = chatBoxSource.indexOf(
        '// Bind behavior independently of the cached capability snapshot.',
    );
    const initializationEnd = chatBoxSource.indexOf(
        'updateQuickScreenCaptureButtonVisibility();',
        initializationStart,
    );
    const initialization = chatBoxSource.slice(
        initializationStart,
        initializationEnd,
    );

    assert.ok(initializationStart >= 0);
    assert.ok(initializationEnd > initializationStart);
    assert.match(initialization, /initDictationFeature\(\);/);
    assert.doesNotMatch(
        initialization,
        /if \(isDictationSupported\(\)\)\s*\{\s*initDictationFeature\(\);/,
    );
    assert.match(
        chatBoxSource,
        /dataset\.dictationFeatureBound === 'true'/,
    );
});

test('both composers fall back only when live startup fails recoverably', () => {
    const chatBoxSource = readSource('./chatBox.js');
    const messageStreamSource = readStreamMessagesSource();
    const chatStart = chatBoxSource.indexOf(
        'async function startLiveDictationRecording()',
    );
    const chatEnd = chatBoxSource.indexOf(
        'function stopDictationRecording()',
        chatStart,
    );
    const editStart = messageStreamSource.indexOf(
        'async function startUserMessageEditLiveDictation(session)',
    );
    const editEnd = messageStreamSource.indexOf(
        'async function transcribeUserMessageEditAudio',
        editStart,
    );
    const chatLiveFunction = chatBoxSource.slice(chatStart, chatEnd);
    const editLiveFunction = messageStreamSource.slice(editStart, editEnd);

    assert.match(chatLiveFunction, /!liveCaptureStarted/);
    assert.match(chatLiveFunction, /shouldFallbackToFile\?\.\(error\)/);
    assert.match(chatLiveFunction, /void startDictationRecording\(\)/);
    assert.match(editLiveFunction, /!liveCaptureStarted/);
    assert.match(editLiveFunction, /shouldFallbackToFile\?\.\(error\)/);
    assert.match(editLiveFunction, /dictation\.skipLiveOnce = true/);
    assert.match(
        messageStreamSource,
        /const canUseLive = Boolean\(\s*!skipLiveOnce/,
    );
});


test('live partial transcripts render in the normal textarea instead of the visualizer', () => {
    const chatBoxSource = readSource('./chatBox.js');
    const partialStart = chatBoxSource.indexOf('onPartial: (text) => {');
    const partialEnd = chatBoxSource.indexOf('onFinal: (text) => {', partialStart);
    const partialCallback = chatBoxSource.slice(partialStart, partialEnd);
    const liveStart = chatBoxSource.indexOf('async function startLiveDictationRecording()');
    const liveEnd = chatBoxSource.indexOf('function stopDictationRecording()', liveStart);
    const liveDictationFunction = chatBoxSource.slice(liveStart, liveEnd);

    assert.ok(partialStart >= 0);
    assert.ok(partialEnd > partialStart);
    assert.ok(liveStart >= 0);
    assert.ok(liveEnd > liveStart);
    assert.match(partialCallback, /applyChatBoxLiveTranscript\(text\)/);
    assert.doesNotMatch(liveDictationFunction, /startDictationVisualizerLoop/);
    assert.doesNotMatch(liveDictationFunction, /startDictationAudioTracking/);
    assert.match(
        chatBoxSource,
        /if \(dictationState\.usesLiveTranscription\)[\s\S]*?dictationVisualizer\.hidden = true;[\s\S]*?input\.hidden = false;/,
    );
    assert.match(
        chatBoxSource,
        /input\.value = `\$\{base\.value\.slice\(0, base\.start\)\}\$\{insertText\}\$\{base\.value\.slice\(base\.end\)\}`/,
    );
});


test('obsolete live transcription errors always unlock the chat textarea', () => {
    const chatBoxSource = readSource('./chatBox.js');
    const handlerStart = chatBoxSource.indexOf('const handleError = (error) => {');
    const handlerEnd = chatBoxSource.indexOf('  };', handlerStart);
    const handler = chatBoxSource.slice(handlerStart, handlerEnd);
    const unlockIndex = handler.indexOf('input.readOnly = false');
    const activeSessionCheckIndex = handler.indexOf(
        'if (!isActiveDictationSession(sessionId) || errorHandled) return;',
    );

    assert.ok(handlerStart >= 0);
    assert.ok(handlerEnd > handlerStart);
    assert.ok(unlockIndex >= 0);
    assert.ok(activeSessionCheckIndex > unlockIndex);
});


test('live transcription distinguishes app quota, active sessions, and provider throttling', () => {
    const controllerSource = readSource('./liveTranscription.js');
    const chatBoxSource = readSource('./chatBox.js');
    const messageStreamSource = readStreamMessagesSource();

    assert.match(
        controllerSource,
        /code === 'user_dictation_rate_limited'/,
    );
    assert.match(
        controllerSource,
        /code === 'user_dictation_in_progress'/,
    );
    assert.match(
        controllerSource,
        /code === 'provider_rate_limited'/,
    );
    assert.doesNotMatch(
        controllerSource,
        /isDictationRateLimit = code === 'rate_limited'/,
    );

    for (const source of [chatBoxSource, messageStreamSource]) {
        assert.match(source, /chat_live_transcription_in_progress/);
        assert.match(source, /chat_live_transcription_provider_rate_limited/);
    }
});

test('completed-file composers preserve active-reservation messaging', () => {
    const chatBoxSource = readSource('./chatBox.js');
    const messageStreamSource = readStreamMessagesSource();

    for (const source of [chatBoxSource, messageStreamSource]) {
        assert.match(source, /omlorixClassifyTranscriptionLimit\(errorData\)/);
        assert.match(source, /requestError\.isDictationInProgress/);
        assert.match(source, /chat_live_transcription_in_progress/);
    }
});

test('only recoverable live startup failures use file transcription fallback', () => {
    const controller = loadLiveTranscriptionController();

    assert.equal(
        controller.shouldFallbackToFile({ code: 'connection_failed' }),
        true,
    );
    assert.equal(
        controller.shouldFallbackToFile({ code: 'provider_rate_limited' }),
        true,
    );
    assert.equal(
        controller.shouldFallbackToFile({
            code: 'user_dictation_rate_limited',
            isDictationRateLimit: true,
        }),
        false,
    );
    assert.equal(
        controller.shouldFallbackToFile({
            code: 'user_dictation_in_progress',
            isDictationInProgress: true,
        }),
        false,
    );
    assert.equal(
        controller.shouldFallbackToFile({
            code: 'microphone_denied',
            name: 'NotAllowedError',
        }),
        false,
    );
    assert.equal(
        controller.shouldFallbackToFile({ code: 'already_active' }),
        false,
    );
});


test('every locale translates the new live transcription errors', () => {
    const localeNames = [
        'ar', 'de', 'en', 'es', 'fr', 'hi', 'it', 'ja', 'pt', 'ru', 'zh',
    ];

    for (const localeName of localeNames) {
        const locale = JSON.parse(readSource(`../../i18n/${localeName}/index.json`));
        assert.ok(locale.chat_live_transcription_in_progress);
        assert.ok(locale.chat_live_transcription_provider_rate_limited);
    }
});
