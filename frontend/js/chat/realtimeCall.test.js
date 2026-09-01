const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { readSendMessageSource } = require('./sending/source.cjs');


const source = fs.readFileSync(path.join(__dirname, 'realtimeCall.js'), 'utf8');
const sendMessageSource = readSendMessageSource();
const chatsSource = fs.readFileSync(path.join(__dirname, 'chats.js'), 'utf8');
const chatsHelperSource = fs.readFileSync(path.join(__dirname, 'chatsHelper.js'), 'utf8');
const callCssSource = fs.readFileSync(path.resolve(__dirname, '../../css/chat/realtimeCall.css'), 'utf8');


function getFunctionSource(functionName, nextFunctionName) {
    const asyncStart = source.indexOf(`async function ${functionName}(`);
    const syncStart = source.indexOf(`function ${functionName}(`);
    const start = asyncStart === -1 ? syncStart : asyncStart;
    const asyncEnd = source.indexOf(`\n    async function ${nextFunctionName}(`, start);
    const syncEnd = source.indexOf(`\n    function ${nextFunctionName}(`, start);
    const endCandidates = [asyncEnd, syncEnd].filter((value) => value !== -1);
    const end = endCandidates.length ? Math.min(...endCandidates) : -1;
    assert.notEqual(start, -1, `${functionName} was not found`);
    assert.notEqual(end, -1, `${nextFunctionName} was not found after ${functionName}`);
    return source.slice(start, end);
}


function loadStandaloneFunction(functionName, nextFunctionName) {
    const functionSource = getFunctionSource(functionName, nextFunctionName);
    return Function(`"use strict"; ${functionSource}; return ${functionName};`)();
}


function loadFunctionWithDependencies(functionName, nextFunctionName, dependencies) {
    const dependencyNames = Object.keys(dependencies);
    const dependencyValues = Object.values(dependencies);
    const functionSource = getFunctionSource(functionName, nextFunctionName);
    return Function(
        ...dependencyNames,
        `"use strict"; ${functionSource}; return ${functionName};`,
    )(...dependencyValues);
}


function getTopLevelFunctionSource(fileSource, functionName, nextFunctionName) {
    const asyncStart = fileSource.indexOf(`async function ${functionName}(`);
    const syncStart = fileSource.indexOf(`function ${functionName}(`);
    const start = asyncStart === -1 ? syncStart : asyncStart;
    const asyncEnd = fileSource.indexOf(`\nasync function ${nextFunctionName}(`, start);
    const syncEnd = fileSource.indexOf(`\nfunction ${nextFunctionName}(`, start);
    const endCandidates = [asyncEnd, syncEnd].filter((value) => value !== -1);
    const end = endCandidates.length ? Math.min(...endCandidates) : -1;
    assert.notEqual(start, -1, `${functionName} was not found`);
    assert.notEqual(end, -1, `${nextFunctionName} was not found after ${functionName}`);
    return fileSource.slice(start, end);
}


class RealtimeOrderTestContainer {
    constructor(children = []) {
        this.children = [];
        children.forEach((child) => this.insertBefore(child, null));
    }

    querySelector(selector) {
        if (selector !== '.dynamic-scroll-spacer') return null;
        return this.children.find((child) => child.className === 'dynamic-scroll-spacer') || null;
    }

    insertBefore(child, referenceChild) {
        const currentIndex = this.children.indexOf(child);
        if (currentIndex !== -1) {
            this.children.splice(currentIndex, 1);
        }
        const referenceIndex = referenceChild ? this.children.indexOf(referenceChild) : -1;
        const insertionIndex = referenceIndex === -1 ? this.children.length : referenceIndex;
        this.children.splice(insertionIndex, 0, child);
        child.parentElement = this;
    }
}


test('Gemini Live setup is owned by the backend proxy', () => {
    const connectSource = getFunctionSource('connectGoogleLiveSocket', 'reconnectGoogleLiveSocket');

    assert.match(connectSource, /new WebSocket\(buildRealtimeWebSocketUrl\(state\.websocketUrl\)\)/);
    assert.doesNotMatch(connectSource, /socket\.send\(JSON\.stringify\(\{ setup:/);
    assert.doesNotMatch(connectSource, /socket\.send\(JSON\.stringify\(\{ config:/);
});


test('Gemini Live decodes Safari Blob and ArrayBuffer websocket frames', async () => {
    const decodeMessage = loadStandaloneFunction(
        'decodeGoogleLiveMessageData',
        'connectGoogleLiveSocket',
    );
    const setupComplete = '{"setupComplete":{}}';
    const arrayBuffer = new TextEncoder().encode(setupComplete).buffer;

    assert.deepEqual(await decodeMessage(new Blob([setupComplete])), {
        setupComplete: {},
    });
    assert.deepEqual(await decodeMessage(arrayBuffer), {
        setupComplete: {},
    });
    assert.equal(await decodeMessage(new Blob(['not-json'])), null);
});


test('realtime call button labels use translated accessibility text', () => {
    const buttonSource = getFunctionSource('updateCallButton', 'updateActivity');

    assert.match(buttonSource, /t\('chat_stop_call', 'Stop call'\)/);
    assert.match(buttonSource, /t\('chat_call', 'Start call'\)/);
    assert.doesNotMatch(buttonSource, /const label = shouldStop \? 'Stop call' : 'Start call'/);
});


test('Gemini Live promotes and retires reconnect candidates atomically', () => {
    const connectSource = getFunctionSource('connectGoogleLiveSocket', 'reconnectGoogleLiveSocket');
    const reconnectSource = getFunctionSource('reconnectGoogleLiveSocket', 'startGoogleLiveCall');

    assert.doesNotMatch(connectSource, /const socket = new WebSocket\(state\.websocketUrl\);\s*state\.ws = socket;/);
    assert.match(connectSource, /parsed\.setupComplete[\s\S]*setupSucceeded = true;[\s\S]*state\.ws = socket;[\s\S]*resolve\(true\)/);
    assert.match(connectSource, /const failGoogleLiveSetup/);
    assert.match(connectSource, /socket\.__omlorixIntentionalClose = true;[\s\S]*socket\.close\(\)/);
    assert.match(connectSource, /socket\.onopen = \(\) => \{\s*if \(settled\) \{[\s\S]*retireGoogleLiveCandidate\(\)/);
    assert.match(connectSource, /chat_realtime_google_setup_timeout[\s\S]*failGoogleLiveSetup|failGoogleLiveSetup[\s\S]*chat_realtime_google_setup_timeout/);
    assert.ok(
        reconnectSource.indexOf('previousSocket.close()')
        < reconnectSource.indexOf('requestRealtimeConnection('),
        'the old backend proxy must release its cross-process provider slot before reconnect',
    );
    assert.match(reconnectSource, /GOOGLE_LIVE_PROXY_RELEASE_TIMEOUT_MS/);
    assert.match(reconnectSource, /reconnectDeadline/);
    assert.ok(
        Number(source.match(/const GOOGLE_LIVE_PROXY_RELEASE_TIMEOUT_MS = (\d+);/)?.[1] || 0) >= 6000,
        'reconnect retries must cover the backend upstream close timeout',
    );
});


test('Gemini Live call start rejects a socket lost during microphone setup', () => {
    const startGoogleSource = getFunctionSource('startGoogleLiveCall', 'start');

    assert.match(startGoogleSource, /await connectGoogleLiveSocket\(\)/);
    assert.match(startGoogleSource, /await startGoogleMicrophoneStreaming\(\)/);
    assert.match(startGoogleSource, /state\.ws\.readyState !== WebSocket\.OPEN/);
});


test('OpenAI accepts the output-item event emitted by the live protocol', () => {
    assert.match(source, /case 'response\.output_item\.added':/);
    assert.match(source, /case 'response\.output_item\.created':/);
});


test('late realtime user transcription is placed before the assistant response', () => {
    const placeMessages = loadStandaloneFunction(
        'placeLiveRealtimeMessagesInTurnOrder',
        'ensureLiveUserMessageElement',
    );
    const previousTurn = { name: 'previous-turn' };
    const assistantMessage = { name: 'assistant' };
    const userMessage = { name: 'user' };
    const spacer = { name: 'spacer', className: 'dynamic-scroll-spacer' };
    const container = new RealtimeOrderTestContainer([
        previousTurn,
        assistantMessage,
        spacer,
        // Realtime currently appends the late transcription after the spacer;
        // the ordering helper must repair both problems in one operation.
        userMessage,
    ]);

    placeMessages(container, userMessage, assistantMessage);

    assert.deepEqual(
        container.children,
        [previousTurn, userMessage, assistantMessage, spacer],
    );
});


test('both realtime message renderers enforce turn order', () => {
    const userRendererSource = getFunctionSource(
        'ensureLiveUserMessageElement',
        'ensureLiveAssistantMessageElement',
    );
    const assistantRendererSource = getFunctionSource(
        'ensureLiveAssistantMessageElement',
        'renderLiveUserTranscript',
    );

    assert.match(userRendererSource, /placeLiveRealtimeMessagesInTurnOrder/);
    assert.match(assistantRendererSource, /placeLiveRealtimeMessagesInTurnOrder/);
});


test('realtime tool calls use the shared renderer once with canonical metadata', () => {
    const calls = [];
    const state = {
        currentTurn: {
            assistantReasoningCount: 0,
            lastAppendedMessageType: '',
            renderedToolCallIds: new Set(),
        },
        liveAssistantMessageId: 'live-assistant',
        liveAssistantContainer: {},
    };
    const contentElement = {};
    const renderToolCall = loadFunctionWithDependencies(
        'renderLiveToolCall',
        'renderLiveUserTranscript',
        {
            state,
            ensureLiveAssistantMessageElement: () => contentElement,
            appendAssistantTool: (...args) => {
                calls.push(args);
                return 1;
            },
            placeLiveAssistantToolsBeforeContent: () => {},
            scrollChatAreaToBottom: () => {},
        },
    );

    assert.equal(renderToolCall('call-1', 'notes', { type: 'list' }), true);
    assert.equal(renderToolCall('call-1', 'notes', { type: 'list' }), false);
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], 'live-assistant');
    assert.equal(calls[0][4], 'notes');
    assert.deepEqual(calls[0][5], { type: 'list' });
    assert.equal(calls[0][6].tool_call_id, 'call-1');
    assert.equal(state.currentTurn.assistantReasoningCount, 1);
    assert.equal(state.currentTurn.lastAppendedMessageType, 't');
});


test('realtime renders a tool before starting backend execution', () => {
    const executeSource = getFunctionSource('executeToolCall', 'handleGoogleServerContent');
    const renderIndex = executeSource.indexOf('renderLiveToolCall(callId, toolName, argumentsPayload)');
    const requestIndex = executeSource.indexOf('/tool-call/pending');

    assert.ok(renderIndex !== -1, 'executeToolCall must render the provider call');
    assert.ok(requestIndex !== -1, 'executeToolCall must register the provider call');
    assert.ok(renderIndex < requestIndex, 'the live tool UI must appear before backend execution');
});


test('realtime tool blocks stay above final assistant text', () => {
    const placeTools = loadStandaloneFunction(
        'placeLiveAssistantToolsBeforeContent',
        'finalizeLiveAssistantToolBlocks',
    );
    const assistantMessage = {
        name: 'assistant-message',
        classList: { contains: () => false },
    };
    const firstTool = {
        name: 'first-tool',
        classList: { contains: (name) => name === 'assistant-thinking' },
    };
    const secondTool = {
        name: 'second-tool',
        classList: { contains: (name) => name === 'assistant-thinking' },
    };
    const container = new RealtimeOrderTestContainer([
        assistantMessage,
        firstTool,
        secondTool,
    ]);
    const assistantContent = {
        closest: (selector) => selector === '.assistant-message' ? assistantMessage : null,
    };

    placeTools(container, assistantContent);

    assert.deepEqual(container.children, [firstTool, secondTool, assistantMessage]);
});


test('realtime tool blocks finalize when assistant content begins', () => {
    const finalized = [];
    const toolBlock = {
        classList: {
            contains: (name) => name === 'assistant-thinking',
        },
    };
    const finalizeTools = loadFunctionWithDependencies(
        'finalizeLiveAssistantToolBlocks',
        'renderLiveToolCall',
        {
            finalizeThinkingBlockHeader: (block) => finalized.push(block),
        },
    );

    finalizeTools({ children: [toolBlock] });

    assert.deepEqual(finalized, [toolBlock]);
    const assistantTranscriptSource = getFunctionSource(
        'renderLiveAssistantTranscript',
        'finalizeLiveAssistantMessage',
    );
    assert.match(assistantTranscriptSource, /finalizeLiveAssistantToolBlocks/);
    assert.match(assistantTranscriptSource, /lastAppendedMessageType = 'c'/);
});


test('OpenAI uses output-buffer events instead of permanent stream playback for speaking state', () => {
    const audioElementSource = getFunctionSource('ensureRemoteAudioElement', 'unlockAudioPlayback');
    const interruptionSource = getFunctionSource('stopRemotePlaybackAndTruncate', 'attachRemoteAudio');
    const providerSource = getFunctionSource('handleProviderEvent', 'queueProviderEvent');

    assert.doesNotMatch(audioElementSource, /addEventListener\('play',[\s\S]*assistantSpeaking = true/);
    assert.match(providerSource, /case 'output_audio_buffer\.started':[\s\S]*assistantSpeaking = true/);
    assert.match(providerSource, /case 'output_audio_buffer\.stopped':[\s\S]*assistantSpeaking = false/);
    assert.match(interruptionSource, /cancelResponse && state\.currentTurn\.responseId && !state\.currentTurn\.responseDone/);
});


test('OpenAI provider events are serialized before mutating shared turn state', () => {
    const queueSource = getFunctionSource('queueProviderEvent', 'setupDataChannel');
    const dataChannelSource = getFunctionSource('setupDataChannel', 'clearPeerDisconnectTimer');
    const sendTextSource = getFunctionSource('sendText', 'isActive');

    assert.match(queueSource, /state\.providerEventQueue[\s\S]*\.then\(\(\) => handleProviderEvent\(event, providerEventOrigin\)\)/);
    assert.match(dataChannelSource, /queueProviderEvent\(parsed, providerEventOrigin\)/);
    assert.doesNotMatch(dataChannelSource, /handleProviderEvent\(parsed\)\.catch/);
    assert.match(sendTextSource, /await state\.providerEventQueue\.catch/);
});


test('OpenAI provider events retain and validate their originating call generation', () => {
    const providerSource = getFunctionSource('handleProviderEvent', 'queueProviderEvent');
    const queueSource = getFunctionSource('queueProviderEvent', 'setupDataChannel');
    const dataChannelSource = getFunctionSource('setupDataChannel', 'clearPeerDisconnectTimer');
    const peerSource = getFunctionSource('startPeerConnection', 'requestRealtimeConnection');

    assert.match(providerSource, /isCurrentProviderEventOrigin\(providerEventOrigin\)/);
    assert.match(queueSource, /handleProviderEvent\(event, providerEventOrigin\)/);
    assert.match(dataChannelSource, /queueProviderEvent\(parsed, providerEventOrigin\)/);
    assert.match(peerSource, /sessionId: state\.sessionId,[\s\S]*startGeneration: startAttemptId/);
});


test('Gemini Live provider messages are serialized and reject retired sockets', () => {
    const originSource = getFunctionSource('isCurrentProviderEventOrigin', 'executeToolCall');
    const handlerSource = getFunctionSource('handleGoogleLiveMessage', 'queueGoogleLiveMessage');
    const queueSource = getFunctionSource('queueGoogleLiveMessage', 'responseContainsFunctionCall');
    const connectSource = getFunctionSource('connectGoogleLiveSocket', 'reconnectGoogleLiveSocket');

    assert.match(originSource, /!origin\.socket \|\| origin\.socket === state\.ws/);
    assert.match(handlerSource, /isCurrentProviderEventOrigin\(providerEventOrigin\)/);
    assert.match(handlerSource, /providerEventOrigin/);
    assert.match(queueSource, /state\.providerEventQueue[\s\S]*handleGoogleLiveMessage\(message, providerEventOrigin\)/);
    assert.match(connectSource, /const providerEventOrigin = \{[\s\S]*socket/);
    assert.match(connectSource, /queueGoogleLiveMessage\(parsed, providerEventOrigin\)/);
    assert.doesNotMatch(connectSource, /handleGoogleLiveMessage\(parsed/);
});


test('Gemini Live transcription chunks are preserved without duplicate prefixes', () => {
    const mergeTranscript = loadStandaloneFunction(
        'mergeGoogleTranscriptChunk',
        'scheduleGoogleTurnCompletion',
    );

    assert.equal(mergeTranscript('', 'Hello'), 'Hello');
    assert.equal(mergeTranscript('Hello', ' world'), 'Hello world');
    assert.equal(mergeTranscript('Hello', 'Hello world'), 'Hello world');
    assert.equal(mergeTranscript('Hello world', 'world'), 'Hello world');
    assert.equal(mergeTranscript('The quick brown', 'brown fox'), 'The quick brown fox');
});


test('Gemini Live defers turn persistence for independently ordered transcripts', () => {
    const completionSource = getFunctionSource('scheduleGoogleTurnCompletion', 'handleGoogleServerContent');
    const serverContentSource = getFunctionSource('handleGoogleServerContent', 'handleGoogleLiveMessage');

    assert.match(completionSource, /googleTurnCompleteTimer/);
    assert.match(completionSource, /state\.providerEventQueue/);
    assert.match(completionSource, /persistCurrentTurn/);
    assert.match(serverContentSource, /scheduleGoogleTurnCompletion\(providerEventOrigin\)/);
    assert.doesNotMatch(serverContentSource, /if \(serverContent\.turnComplete\) \{\s*await persistCurrentTurn/);
});


test('OpenAI tool-call responses stay in the current turn until the continuation finishes', () => {
    const providerSource = getFunctionSource('handleProviderEvent', 'queueProviderEvent');

    assert.match(providerSource, /responseContainsFunctionCall\(response\)/);
    assert.match(
        providerSource,
        /if \([\s\S]*state\.currentTurn\.responseHasFunctionCall[\s\S]*responseStatus !== 'cancelled'[\s\S]*\) \{[\s\S]*updateActivity\('thinking'\);[\s\S]*return;/,
    );
    assert.match(providerSource, /case 'response\.output_text\.delta':/);
});


test('xAI realtime uses JSON PCM events and waits for playback before tool continuation', () => {
    const microphoneSource = getFunctionSource(
        'startGoogleMicrophoneStreaming',
        'stopGoogleMicrophoneStreaming',
    );
    const toolSource = getFunctionSource('executeToolCall', 'handleGoogleServerContent');

    assert.match(
        microphoneSource,
        /type: 'input_audio_buffer\.append',[\s\S]*audio: arrayBufferToBase64\(pcm16\.buffer\)/,
    );
    assert.match(toolSource, /isXaiLiveTransport\(\)[\s\S]*state\.googlePlaybackSources\.size/);
    assert.ok(
        toolSource.indexOf('state.googlePlaybackSources.size')
        < toolSource.lastIndexOf("sendRealtimeEvent({ type: 'response.create' })"),
        'xAI continuation must wait for queued output audio playback',
    );
});


test('xAI realtime batches parallel tool outputs before one continuation', () => {
    const toolSource = getFunctionSource('executeToolCall', 'handleGoogleServerContent');
    const providerSource = getFunctionSource('handleProviderEvent', 'queueProviderEvent');

    assert.match(source, /pendingToolCalls: new Map\(\)/);
    assert.match(toolSource, /async function flushPendingXaiToolCalls[\s\S]*await Promise\.all/);
    assert.match(toolSource, /requestContinuation: false/);
    assert.match(
        providerSource,
        /isXaiLiveTransport\(\)[\s\S]*flushPendingXaiToolCalls\(providerEventOrigin\)/,
    );
    assert.match(
        providerSource,
        /state\.currentTurn\.responseHasFunctionCall[\s\S]*responseStatus !== 'cancelled'[\s\S]*flushPendingXaiToolCalls/,
    );
    assert.match(
        providerSource,
        /responseStatus === 'cancelled'[\s\S]*pendingToolCalls\?\.clear\?\.\(\)/,
    );
});


test('OpenAI ignores an idempotent cancellation after response completion', () => {
    const cancellationSource = getFunctionSource('isBenignOpenAiCancellationError', 'persistCompletedOpenAiTurnIfReady');
    const providerSource = getFunctionSource('handleProviderEvent', 'queueProviderEvent');

    assert.match(cancellationSource, /cancellation failed/);
    assert.match(cancellationSource, /no active response/);
    assert.match(providerSource, /isBenignOpenAiCancellationError\(event\)[\s\S]*return;/);
});


test('realtime chat synchronization preserves the active call route', () => {
    assert.match(
        chatsSource,
        /showChatContainer\(\{ skipCallTeardown: preserveHistory \}\)/,
    );
    assert.match(
        source,
        /loadChatView\(chatId, false, \{ preserveHistory: preserveRoute \}\)/,
    );
});


test('realtime call start reuses the chat that opened the call route', () => {
    const routeSource = getFunctionSource('activateCallRoute', 'deactivateCallRoute');
    const contextSource = getFunctionSource('getRealtimeStartContext', 'syncChatForRealtimeSession');
    const startSource = getFunctionSource('start', 'stop');

    assert.match(routeSource, /state\.routeReturnChatId = currentChatId/);
    assert.match(contextSource, /if \(!context\.chatId && originatingChatId\)/);
    assert.match(contextSource, /chatId: originatingChatId/);
    assert.match(startSource, /const context = getRealtimeStartContext\(\)/);
});


test('realtime call start keeps a genuinely new chat unassigned', () => {
    const contextSource = getFunctionSource('getRealtimeStartContext', 'syncChatForRealtimeSession');

    assert.match(contextSource, /state\.routeModeActive[\s\S]*state\.routeReturnChatId/);
    assert.match(contextSource, /return context;/);
});


test('new realtime chats are inserted into the main sidebar before connection setup', () => {
    const startSource = getFunctionSource('start', 'stop');
    const sidebarIndex = startSource.indexOf('window.ensureChatSidebarRow(state.chatId');
    const synchronizationIndex = startSource.indexOf('await syncChatForRealtimeSession(state.chatId');

    assert.match(startSource, /data\.created_chat/);
    assert.ok(sidebarIndex !== -1, 'start must create a sidebar row for a new realtime chat');
    assert.ok(synchronizationIndex !== -1, 'start must synchronize the realtime chat view');
    assert.ok(sidebarIndex < synchronizationIndex, 'the sidebar row must exist before provider connection setup');
    assert.match(chatsHelperSource, /window\.ensureChatSidebarRow = ensureChatSidebarRow/);
});


test('saved realtime turns apply the generated title to the sidebar', () => {
    const savedSource = getFunctionSource('handleTurnSaved', 'persistCurrentTurn');

    assert.match(savedSource, /payload\?\.chat_title/);
    assert.match(savedSource, /window\.applyChatSidebarTitle\(chatId, generatedTitle\)/);
    assert.match(savedSource, /payload\?\.chat_title_pending/);
    assert.match(savedSource, /scheduleRealtimeTitleRefresh\(chatId, generatedTitle\)/);
    assert.match(source, /\/api\/v1\/chats\/detail\?chat_id=/);
    assert.match(chatsHelperSource, /window\.applyChatSidebarTitle = applyChatSidebarTitle/);
    assert.match(chatsHelperSource, /document\.querySelector\('\.project-sidebar-chats'\)/);
    assert.match(chatsHelperSource, /if \(existingProjectRow && typeof window\.addOrUpdateProjectChatRow/);
});


test('realtime call requests microphone permission from the initial click path', () => {
    const autostartSource = getFunctionSource('maybeAutostartFromRoute', 'activateCallRoute');
    const startSource = getFunctionSource('start', 'stop');
    const microphoneRequestIndex = startSource.indexOf('await ensureLocalMicrophoneStream(startAttemptId)');
    const sessionRequestIndex = startSource.indexOf("window.authedFetch('/api/v1/realtime/session/start'");

    assert.match(autostartSource, /state\.routeAutostartPromise = start\(\)/);
    assert.doesNotMatch(autostartSource, /Promise\.resolve/);
    assert.ok(microphoneRequestIndex !== -1, 'start must request microphone access');
    assert.ok(sessionRequestIndex !== -1, 'start must create a realtime session');
    assert.ok(microphoneRequestIndex < sessionRequestIndex, 'microphone access must be requested before session creation');
    assert.ok(
        startSource.indexOf('await unlockAudioPlayback()') < sessionRequestIndex,
        'audio output unlocking must run before session creation',
    );
});


test('realtime providers reuse the stream granted during call start', () => {
    const googleSource = getFunctionSource('startGoogleMicrophoneStreaming', 'stopGoogleMicrophoneStreaming');
    const peerSource = getFunctionSource('startPeerConnection', 'requestRealtimeConnection');

    assert.match(googleSource, /await ensureLocalMicrophoneStream\(\)/);
    assert.match(peerSource, /await ensureLocalMicrophoneStream\(\)/);
    assert.doesNotMatch(googleSource, /getUserMedia/);
    assert.doesNotMatch(peerSource, /getUserMedia/);
});


test('Gemini Live microphone contexts recover from browser suspension', () => {
    const recoverySource = getFunctionSource('resumeGoogleMicrophoneAudioContext', 'retryRemotePlaybackFromGesture');
    const gestureSource = getFunctionSource('retryRemotePlaybackFromGesture', 'installAudioRecoveryListeners');
    const microphoneSource = getFunctionSource('startGoogleMicrophoneStreaming', 'stopGoogleMicrophoneStreaming');

    assert.match(recoverySource, /googleMicAudioContext[\s\S]*\.resume\(\)/);
    assert.match(gestureSource, /resumeGoogleMicrophoneAudioContext/);
    assert.match(microphoneSource, /await resumeGoogleMicrophoneAudioContext\(\)/);
});


test('stopping while microphone permission is pending invalidates the start attempt', () => {
    const microphoneSource = getFunctionSource('ensureLocalMicrophoneStream', 'startGoogleMicrophoneStreaming');
    const stopSource = getFunctionSource('stop', 'toggleMute');

    assert.match(microphoneSource, /assertCurrentStartAttempt\(startAttemptId, stream\)/);
    assert.match(stopSource, /state\.startAttemptId \+= 1/);
    assert.match(stopSource, /stopLocalMediaStream\(state\.localStream\)/);
});


test('Safari media unlocking cannot block WebRTC negotiation indefinitely', () => {
    const timeoutSource = getFunctionSource('settleMediaPromiseWithin', 'resumeOutputAudioContext');
    const unlockSource = getFunctionSource('unlockAudioPlayback', 'syncRemoteAudioPlayback');

    assert.match(timeoutSource, /Promise\.race\(\[settledPromise, timeoutPromise\]\)/);
    assert.match(unlockSource, /settleMediaPromiseWithin\(audioEl\.play\(\), 500\)/);
});


test('cancelled WebRTC setup never posts to a cleared signaling URL', () => {
    const peerSource = getFunctionSource('startPeerConnection', 'requestRealtimeConnection');

    assert.match(peerSource, /const normalizedSignalingUrl = buildRealtimeSignalingUrl\(signalingUrl\)/);
    assert.match(peerSource, /window\.authedFetch\(normalizedSignalingUrl/);
    assert.doesNotMatch(peerSource, /authedFetch\(state\.signalingUrl/);
    assert.match(peerSource, /setLocalDescription\(offer\)[\s\S]*assertCurrentStartAttempt\(startAttemptId\)/);
});


test('OpenAI signaling URLs are normalized and restricted to the page origin', () => {
    const buildUrl = loadFunctionWithDependencies(
        'buildRealtimeSignalingUrl',
        'startPeerConnection',
        {
            window: {
                location: {
                    href: 'https://chat.example/call',
                    origin: 'https://chat.example',
                    protocol: 'https:',
                },
            },
            t: (_key, fallback) => fallback,
        },
    );

    assert.equal(
        buildUrl('/api/v1/realtime/session/session-1/webrtc-offer'),
        'https://chat.example/api/v1/realtime/session/session-1/webrtc-offer',
    );
    for (const invalidUrl of ['', 'https://provider.example/realtime', 'http://[']) {
        assert.throws(() => buildUrl(invalidUrl), /Invalid realtime session response/);
    }
});


test('OpenAI signaling rejects non-string URLs before authenticated fetch', async () => {
    const buildUrlSource = getFunctionSource('buildRealtimeSignalingUrl', 'startPeerConnection');
    const startPeerSource = getFunctionSource('startPeerConnection', 'requestRealtimeConnection');
    const authedFetchCalls = [];
    const startPeerConnection = Function(
        'window',
        't',
        `"use strict"; ${buildUrlSource}; ${startPeerSource}; return startPeerConnection;`,
    )(
        {
            location: {
                href: 'https://chat.example/call',
                origin: 'https://chat.example',
                protocol: 'https:',
            },
            authedFetch(...args) {
                authedFetchCalls.push(args);
            },
        },
        (_key, fallback) => fallback,
    );

    for (const invalidUrl of [true, { url: '/api/v1/realtime/session/session-1/webrtc-offer' }]) {
        await assert.rejects(
            startPeerConnection({ signalingUrl: invalidUrl, startAttemptId: 1 }),
            /Invalid realtime session response/,
        );
    }
    assert.equal(authedFetchCalls.length, 0);
});


test('OpenAI WebRTC negotiates one bidirectional audio section', () => {
    const peerSource = getFunctionSource('startPeerConnection', 'requestRealtimeConnection');

    assert.match(peerSource, /const \[audioTrack\] = localStream\.getAudioTracks\(\)/);
    assert.match(peerSource, /pc\.addTrack\(audioTrack, localStream\)/);
    assert.doesNotMatch(peerSource, /addTransceiver\(['"]audio['"]/);
    assert.match(peerSource, /window\.authedFetch\(normalizedSignalingUrl/);
    assert.match(peerSource, /'Content-Type': 'application\/json'/);
    assert.match(peerSource, /JSON\.stringify\(\{ sdp: offer\.sdp \}\)/);
    assert.doesNotMatch(peerSource, /Authorization:/);
    assert.doesNotMatch(peerSource, /clientSecret/);
});


test('OpenAI SDP answers preserve Safari-required final CRLF', () => {
    const readAnswer = loadFunctionWithDependencies(
        'readRealtimeSdpAnswer',
        'startPeerConnection',
        {
            t: (_key, fallback) => fallback,
        },
    );
    const providerAnswer = 'v=0\r\no=provider-answer\r\n';

    assert.equal(readAnswer({ sdp: providerAnswer }), providerAnswer);
    assert.throws(
        () => readAnswer({ sdp: ' \r\n' }),
        /Invalid realtime session response/,
    );
});


test('Gemini Live proxy URL is same-origin and upgraded to websocket transport', () => {
    const buildUrl = loadFunctionWithDependencies(
        'buildRealtimeWebSocketUrl',
        'decodeGoogleLiveMessageData',
        {
            window: {
                location: {
                    href: 'https://chat.example/call',
                    origin: 'https://chat.example',
                    protocol: 'https:',
                },
            },
            t: (_key, fallback) => fallback,
        },
    );

    assert.equal(
        buildUrl('/api/v1/realtime/session/session-1/google-live'),
        'wss://chat.example/api/v1/realtime/session/session-1/google-live',
    );
    assert.throws(
        () => buildUrl('https://provider.example/realtime'),
        /Invalid realtime session response/,
    );
});


test('OpenAI ephemeral sessions do not resend immutable configuration on data channel open', () => {
    const dataChannelSource = getFunctionSource('setupDataChannel', 'startPeerConnection');

    assert.doesNotMatch(dataChannelSource, /session\.update/);
    assert.doesNotMatch(source, /async function updateRealtimeSession/);
});


test('OpenAI WebRTC allows transient disconnects to recover', () => {
    const peerSource = getFunctionSource('startPeerConnection', 'requestRealtimeConnection');

    assert.match(peerSource, /pc\.connectionState === 'disconnected'[\s\S]*setTimeout[\s\S]*5000/);
    assert.match(peerSource, /pc\.connectionState === 'connected'[\s\S]*clearPeerDisconnectTimer\(\)/);
});


test('Gemini Live handles provider cancellation and proactive reconnect signals', () => {
    assert.match(source, /message\.toolCallCancellation\?\.ids\?\.length/);
    assert.match(source, /reconnectGoogleLiveSocket\(\{ previousSocket: providerEventOrigin\.socket \}\)/);
    assert.match(source, /Failed to reconnect after Google Live GoAway[\s\S]*await stop\(\{ skipServerStop: false, silent: true, reason: 'google_goaway_reconnect_failed' \}\)/);
});


test('Gemini Live sends current-model text through realtimeInput', () => {
    const sendTextSource = getFunctionSource('sendText', 'isActive');

    assert.match(sendTextSource, /data\?\.realtime_input/);
    assert.match(sendTextSource, /mode === 'realtime_input'/);
    assert.match(sendTextSource, /sendGoogleRealtimeMessage\(\{\s*realtimeInput:/);
    assert.doesNotMatch(sendTextSource, /clientContent:/);
});


test('realtime chat send blocks unsupported one-request context before transport', () => {
    const helperSource = getTopLevelFunctionSource(
        sendMessageSource,
        'hasUnsupportedRealtimeRequestContext',
        'clearAcceptedRealtimeFileAttachments',
    );
    const hasUnsupportedContext = Function(
        `"use strict"; ${helperSource}; return hasUnsupportedRealtimeRequestContext;`,
    )();
    const sendSource = getTopLevelFunctionSource(
        sendMessageSource,
        'sendMessage',
        'dispatchExternalChatMessage',
    );
    const realtimeBranchStart = sendSource.indexOf('if (realtimeCallIsActive) {');
    const realtimeBranchEnd = sendSource.indexOf('// Use one client-owned ID', realtimeBranchStart);
    const realtimeBranch = sendSource.slice(realtimeBranchStart, realtimeBranchEnd);

    assert.equal(hasUnsupportedContext({}), false);
    assert.equal(hasUnsupportedContext({ imageIds: ['file-1'], chatReferenceIds: ['chat-1'] }), false);
    assert.equal(hasUnsupportedContext({ noteIds: ['note-1'] }), true);
    assert.equal(hasUnsupportedContext({ promptIds: ['prompt-1'] }), true);
    assert.equal(hasUnsupportedContext({ referenceParts: ['selected passage'] }), true);
    assert.match(realtimeBranch, /if \(hasUnsupportedRealtimeRequestContext\(composerContext\)\)/);
    assert.match(realtimeBranch, /chat_realtime_request_context_unsupported/);
    assert.ok(
        realtimeBranch.indexOf('hasUnsupportedRealtimeRequestContext(composerContext)')
            < realtimeBranch.indexOf('await window.realtimeCall.sendText'),
        'unsupported context must be rejected before calling the realtime transport',
    );
    assert.match(realtimeBranch, /return false;/);
});


test('accepted realtime sends clear only the file IDs applied by transport', () => {
    const helperSource = getTopLevelFunctionSource(
        sendMessageSource,
        'clearAcceptedRealtimeFileAttachments',
        'clearChatRequestFiles',
    );
    const removedFileIds = [];
    const cancelledFileIds = [];
    const removedRequestIds = [];
    const deletedMetadataIds = [];
    const clearAcceptedFiles = Function(
        'isTemporaryAttachmentId',
        'cancelPendingUpload',
        'removeRequestFileId',
        'attachmentState',
        'removeExistingChatAttachment',
        `"use strict"; ${helperSource}; return clearAcceptedRealtimeFileAttachments;`,
    )(
        (fileId) => fileId.startsWith('temp-'),
        (fileId, options) => cancelledFileIds.push({ fileId, options }),
        (fileId) => removedRequestIds.push(fileId),
        { chatAttachmentMetadata: { delete: (fileId) => deletedMetadataIds.push(fileId) } },
        (fileId) => removedFileIds.push(fileId),
    );
    const sendSource = getTopLevelFunctionSource(
        sendMessageSource,
        'sendMessage',
        'dispatchExternalChatMessage',
    );
    const realtimeBranchStart = sendSource.indexOf('if (realtimeCallIsActive) {');
    const realtimeBranchEnd = sendSource.indexOf('// Use one client-owned ID', realtimeBranchStart);
    const realtimeBranch = sendSource.slice(realtimeBranchStart, realtimeBranchEnd);

    clearAcceptedFiles([' file-1 ', 'file-1', 'temp-upload-1']);

    assert.deepEqual(removedFileIds, ['file-1']);
    assert.deepEqual(cancelledFileIds, [{
        fileId: 'temp-upload-1',
        options: { removeFromUI: true, deleteEntry: true },
    }]);
    assert.deepEqual(removedRequestIds, ['temp-upload-1']);
    assert.deepEqual(deletedMetadataIds, ['temp-upload-1']);
    assert.match(realtimeBranch, /clearAcceptedRealtimeFileAttachments\(allFileIds\)/);
    assert.doesNotMatch(realtimeBranch, /clearChatRequestFiles/);
    assert.doesNotMatch(realtimeBranch, /clearAllReferenceParts/);
});


test('muting Gemini Live explicitly closes the current audio stream', () => {
    const muteSource = getFunctionSource('toggleMute', 'interrupt');

    assert.match(muteSource, /isGoogleLiveTransport\(\)/);
    assert.match(muteSource, /audioStreamEnd: true/);
});


test('realtime calls maintain backend activity and enforce provider limits', () => {
    const heartbeatSource = getFunctionSource('sendRealtimeHeartbeat', 'startRealtimeMaintenance');
    const maintenanceSource = getFunctionSource('startRealtimeMaintenance', 'stopLocalMediaStream');
    const startSource = getFunctionSource('start', 'stop');

    assert.match(source, /\/heartbeat`/);
    assert.match(source, /chat_realtime_session_limit_reached/);
    assert.match(heartbeatSource, /if \(response\.ok\) return true/);
    assert.match(heartbeatSource, /response\.status >= 400 && response\.status < 500[\s\S]*await stop/);
    assert.match(maintenanceSource, /Date\.parse\(state\.sessionExpiresAt\)/);
    assert.ok(
        startSource.indexOf('startRealtimeMaintenance(') < startSource.indexOf('await startPeerConnection('),
        'the absolute session deadline must be scheduled before provider negotiation',
    );
});


test('new realtime status copy is translated in every supported locale', () => {
    const i18nRoot = path.resolve(__dirname, '../../i18n');
    const localeDirectories = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);
    const requiredKeys = [
        'chat_realtime_call_ended',
        'chat_realtime_google_reconnect_exhausted',
        'chat_realtime_microphone_track_missing',
        'chat_realtime_provider_connection_failed_status',
        'chat_realtime_provider_error_empty',
        'chat_realtime_request_context_unsupported',
        'chat_realtime_session_limit_reached',
        'chat_stop_call',
        'chat_call_unmute_microphone',
        'chat_call_end',
        'call_surface_aria',
        'call_orb_aria',
        'call_transcript_aria',
        'call_status_idle',
        'call_status_connecting',
        'call_status_listening',
        'call_status_thinking',
        'call_status_speaking',
        'call_switch_to_text',
        'call_switch_to_orb',
        'call_show_transcript',
        'call_hide_transcript',
    ];

    for (const locale of localeDirectories) {
        const messages = JSON.parse(fs.readFileSync(path.join(i18nRoot, locale, 'index.json'), 'utf8'));
        for (const key of requiredKeys) {
            assert.equal(typeof messages[key], 'string', `${locale} is missing ${key}`);
            assert.ok(messages[key].trim(), `${locale} has an empty ${key}`);
        }
    }
});


test('dedicated realtime route restores a persisted view and defaults to the orb', () => {
    const routeSource = getFunctionSource('activateCallRoute', 'deactivateCallRoute');
    const viewSource = getFunctionSource('setCallViewMode', 'enterCallRouteUi');
    const enterSource = getFunctionSource('enterCallRouteUi', 'leaveCallRouteUi');
    const transcriptSource = getFunctionSource('updateCallTranscript', 'setCallViewMode');
    const preferenceSource = getFunctionSource('readCallViewPreference', 'getCallStatusLabel');

    assert.match(routeSource, /enterCallRouteUi\(\{ resetMode: true \}\)/);
    assert.match(viewSource, /normalizedMode = mode === 'text' \? 'text' : 'orb'/);
    assert.match(viewSource, /writeCallViewPreference\(normalizedMode\)/);
    assert.match(enterSource, /state\.callViewMode = readCallViewPreference\(\)/);
    assert.match(viewSource, /realtime-call-text-mode/);
    assert.match(preferenceSource, /localStorage\.getItem\(REALTIME_CALL_VIEW_STORAGE_KEY\) === 'text' \? 'text' : 'orb'/);
    assert.match(preferenceSource, /localStorage\.setItem\(REALTIME_CALL_VIEW_STORAGE_KEY, mode\)/);
    assert.match(transcriptSource, /ui\.transcriptText\.textContent = normalized/);
    assert.match(source, /updateCallTranscript\(normalized\)/);
    assert.match(source, /setupCallOrbAudioAnalyser/);
});

test('realtime header controls reuse icon-only standard header buttons', () => {
    const surfaceSource = getFunctionSource('ensureCallSurface', 'syncCallSurfaceTranslations');
    const iconSource = getFunctionSource('setCallButtonIcon', 'syncCallSurfaceState');

    assert.match(surfaceSource, /headerActions\.querySelector\('#realtimeCallHeaderControls'\)\?\.remove\(\)/);
    assert.match(surfaceSource, /om-button realtime-call-header-button/g);
    assert.match(iconSource, /button\.innerHTML = icon/);
    assert.doesNotMatch(iconSource, /createElement\('span'\)/);
});

test('realtime orb layout keeps the desktop composer and uses mobile call controls', () => {
    const desktopOrbRules = callCssSource.slice(0, callCssSource.indexOf('@media (max-width: 600px)'));
    const mobileOrbRules = callCssSource.slice(callCssSource.indexOf('@media (max-width: 600px)'));

    assert.doesNotMatch(desktopOrbRules, /#chatBoxArea/);
    assert.match(desktopOrbRules, /\.realtime-call-controls\s*\{[\s\S]*?display: none/);
    assert.match(mobileOrbRules, /body\.realtime-call-route:not\(\.realtime-call-text-mode\) #chatBoxArea\s*\{[\s\S]*?display: none !important/);
    assert.match(mobileOrbRules, /\.realtime-call-controls\s*\{[\s\S]*?display: flex/);
});


test('realtime orb blends motion profiles while preserving continuous phases', () => {
    const easeTransition = loadStandaloneFunction(
        'easeCallOrbStateTransition',
        'advanceCallOrbProfile',
    );
    const profileProperties = [
        'rotationSpeed',
        'baseAmplitude',
        'levelAmplitude',
        'noiseSpeed',
        'pulse',
        'minLevel',
    ];
    const profile = Object.fromEntries(profileProperties.map((property) => [property, 0]));
    const target = Object.fromEntries(profileProperties.map((property) => [property, 10]));
    const stateDependency = {
        callOrb: {
            profile,
            profileTransition: { from: { ...profile }, to: target, elapsedMs: 0 },
        },
    };
    const advanceProfile = loadFunctionWithDependencies(
        'advanceCallOrbProfile',
        'drawCallOrb',
        {
            state: stateDependency,
            REALTIME_ORB_PROFILES: { idle: profile },
            REALTIME_ORB_STATE_TRANSITION_MS: 700,
            REALTIME_ORB_PROFILE_PROPERTIES: profileProperties,
            easeCallOrbStateTransition: easeTransition,
        },
    );

    assert.equal(easeTransition(-1), 0);
    assert.equal(easeTransition(0.25), 0.15625);
    assert.equal(easeTransition(0.5), 0.5);
    assert.equal(easeTransition(2), 1);
    advanceProfile(0.35);
    for (const property of profileProperties) {
        assert.equal(stateDependency.callOrb.profile[property], 5);
    }
    advanceProfile(0.35);
    assert.deepEqual(stateDependency.callOrb.profile, target);
    assert.equal(stateDependency.callOrb.profileTransition, null);

    const drawSource = getFunctionSource('drawCallOrb', 'drawStaticCallOrb');
    const updateSource = getFunctionSource('updateCallOrbState', 'setupCallOrbAudioAnalyser');
    assert.match(drawSource, /advanceCallOrbProfile\(deltaSeconds\)/);
    assert.match(drawSource, /orb\.rotationAngle \+ profile\.rotationSpeed \* deltaSeconds/);
    assert.match(drawSource, /orb\.noisePhase \+ profile\.noiseSpeed \* deltaSeconds/);
    assert.doesNotMatch(drawSource, /timeSeconds \* profile\.(?:rotationSpeed|noiseSpeed)/);
    assert.match(updateSource, /from: \{ \.\.\.orb\.profile \}/);
});
