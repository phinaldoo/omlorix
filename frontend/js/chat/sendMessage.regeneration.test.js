const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readStreamMessagesSource } = require('./messages/source.cjs');
const { readSendMessageSource } = require('./sending/source.cjs');

const source = readSendMessageSource();
const streamSource = readStreamMessagesSource();

/** Return one top-level function body for focused source-order assertions. */
function extractFunction(functionName) {
    const start = source.indexOf(`function ${functionName}`);
    assert.notEqual(start, -1, `${functionName} not found`);

    const bodyStart = source.indexOf('{', source.indexOf(')', start));
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
    }

    throw new Error(`${functionName} body was not closed`);
}

test('confirmed assistant regeneration replaces the old response before requesting the stream', () => {
    const triggerSource = extractFunction('triggerRegeneration');
    const protectedTryIndex = triggerSource.indexOf('try {');
    const prepareIndex = triggerSource.indexOf('prepareAssistantRegenerationTarget(');
    const loadingIndex = triggerSource.indexOf('appendLoading(optimisticMessageId, 0)');
    const requestIndex = triggerSource.indexOf("window.authedFetch('/api/v1/chats/regenerate'");

    assert.notEqual(protectedTryIndex, -1);
    assert.notEqual(prepareIndex, -1);
    assert.notEqual(loadingIndex, -1);
    assert.notEqual(requestIndex, -1);
    assert.ok(prepareIndex < loadingIndex, 'the pending version must exist before its loader is rendered');
    assert.ok(loadingIndex < requestIndex, 'the old response must be replaced before awaiting the server');
    assert.ok(protectedTryIndex < prepareIndex, 'optimistic setup must run inside generation error protection');
});

test('normal and regenerated sends provide client-owned generation IDs and abort signals', () => {
    const normalSendSource = extractFunction('sendMessage');
    const regenerationSource = extractFunction('triggerRegeneration');
    const requestBodySource = extractFunction('buildRegenerationRequestBody');

    assert.match(normalSendSource, /generation_id: generationRequestId/);
    assert.match(normalSendSource, /signal: generationTransport\.abortController\.signal/);
    assert.match(normalSendSource, /RawUuid !== generationRequestId/);
    assert.doesNotMatch(normalSendSource, /\buuid\b/);
    assert.match(regenerationSource, /const generationRequestId = generateUUID\(\)/);
    assert.match(regenerationSource, /generationId: generationRequestId/);
    assert.match(regenerationSource, /signal: generationTransport\.abortController\.signal/);
    assert.match(requestBodySource, /generation_id: generationId/);
});

test('regeneration reports its generation-scoped terminal state to the queue', () => {
    const triggerSource = extractFunction('triggerRegeneration');
    const streamSource = extractFunction('processRegenerationStream');

    assert.match(triggerSource, /messageQueue\?\.handleGenerationTerminal\?\.\(\{/);
    assert.match(triggerSource, /generationId: generationRequestId/);
    assert.match(triggerSource, /status: queueTerminalStatus/);
    assert.match(streamSource, /completed = true/);
    assert.match(streamSource, /return \{ completed \}/);
});

test('regeneration binds the persisted assistant ID returned after completion', () => {
    const streamSource = extractFunction('processRegenerationStream');

    assert.match(
        streamSource,
        /obj\.t === 'a_id'[\s\S]*bindAssistantContainerToServerMessage\(targetMessageId, obj\.d\)/,
    );
});

test('Stop closes the browser stream immediately and cancels the backend independently', () => {
    const cancelSource = extractFunction('cancelChatGenerationTransport');

    const presentationIndex = cancelSource.indexOf('finalizeCancelledAssistantStream?.(');
    const backendCancelIndex = cancelSource.indexOf('requestGenerationCancellation(generationId)');
    const readerCancelIndex = cancelSource.indexOf('reader?.cancel?.()');
    const transportAbortIndex = cancelSource.indexOf('abortController?.abort?.()');
    const uiResetIndex = cancelSource.indexOf('window.endGenerationUI?.()');

    assert.ok(presentationIndex >= 0);
    assert.ok(backendCancelIndex > presentationIndex, 'the partial response must settle before the composer resets');
    assert.ok(readerCancelIndex > backendCancelIndex);
    assert.ok(transportAbortIndex > readerCancelIndex);
    assert.ok(uiResetIndex > transportAbortIndex);
});

test('split-screen sends own and immediately abort independent panel transports', () => {
    const splitSource = readFrontendSource(path.join(__dirname, 'splitScreen.js'), 'utf8');
    const requestCancelStart = splitSource.indexOf('async function requestCancelForSide');
    const requestCancelEnd = splitSource.indexOf('async function cancelGenerationForTarget', requestCancelStart);
    const requestCancelSource = splitSource.slice(requestCancelStart, requestCancelEnd);

    assert.match(splitSource, /const generationRequestId = generateUUID\(\);/);
    assert.match(splitSource, /generation_id: generationRequestId/);
    assert.match(splitSource, /leftStreamReader : state\.rightStreamReader\)\?\.cancel\?\.\(\)/s);
    assert.match(splitSource, /leftAbortController : state\.rightAbortController\)\?\.abort\?\.\(\)/);
    assert.ok(
        requestCancelSource.indexOf('finalizeCancelledAssistantStream?.(')
        < requestCancelSource.indexOf('leftStreamReader : state.rightStreamReader)?.cancel?.()'),
        'each panel must settle its partial response before closing the reader',
    );
});

test('regeneration failures restore the previously visible assistant response', () => {
    const triggerSource = extractFunction('triggerRegeneration');

    assert.match(triggerSource, /rollbackAssistantRegenerationTarget\(\{/);
    assert.match(triggerSource, /previousTotalVersions/);
    assert.match(triggerSource, /reusedContainerSnapshot/);
    assert.match(
        triggerSource,
        /reusedContainerSnapshot = captureAssistantRetryContainerSnapshot\(assistantContainer\);\s*const optimisticTarget = prepareAssistantRegenerationTarget/
    );
    assert.match(triggerSource, /preparedTargetMessageId: optimisticMessageId/);
    assert.match(source, /let last_appended_message_type = preparedTargetMessageId \? 'loading' : '';/);
});

test('streamed regeneration rate limits restore the old response and render the quota card', () => {
    const triggerSource = extractFunction('triggerRegeneration');
    const processSource = extractFunction('processRegenerationStream');

    assert.match(triggerSource, /if \(regenerationResult\?\.rateLimited\) \{/);
    assert.match(triggerSource, /errorData: regenerationResult\.errorData/);
    assert.match(triggerSource, /fallbackDetail: regenerationResult\.detail/);
    assert.match(processSource, /const errorData = \{ detail: translatedStreamError \};/);
    assert.match(processSource, /isRateLimitErrorPayload\(errorData, detail\)/);
    assert.match(processSource, /return \{ rateLimited: true, errorData, detail \};/);
    assert.doesNotMatch(
        processSource,
        /appendAssistantError\(targetMessageId, obj\.d, last_appended_message_type\)/
    );
});

test('reused regeneration containers restore cleared content and metadata on rollback', () => {
    const captureSource = extractFunction('captureAssistantRetryContainerSnapshot');
    const restoreSource = extractFunction('restoreAssistantRetryContainerSnapshot');
    const prepareSource = extractFunction('prepareAssistantRegenerationTarget');
    const rollbackSource = extractFunction('rollbackAssistantRegenerationTarget');

    assert.match(captureSource, /innerHTML: container\.innerHTML/);
    assert.match(captureSource, /'hasError'/);
    assert.match(captureSource, /'assistantMetadata'/);
    assert.match(captureSource, /'citations'/);
    assert.match(restoreSource, /container\.innerHTML = snapshot\.innerHTML/);
    assert.match(prepareSource, /snapshot: reusedContainerSnapshot/);
    assert.match(prepareSource, /resetResult\.snapshot \|\| reusedContainerSnapshot/);
    assert.match(rollbackSource, /restoreAssistantRetryContainerSnapshot\(originalContainer, reusedContainerSnapshot\)/);
});

test('switching assistant versions closes message-specific preview sidebars', () => {
    const closeSource = extractFunction('closeAssistantVersionPreviewSidebars');
    const switchSource = extractFunction('switchAssistantVersion');

    assert.match(closeSource, /window\.slidePresentationWidget/);
    assert.match(closeSource, /window\.canvasMarkdownWidget/);
    assert.match(closeSource, /window\.latexPdfWidget/);
    assert.match(closeSource, /window\.NotesToolSidebar/);
    assert.match(closeSource, /window\.closeCitationsSidebar/);
    assert.match(switchSource, /closeAssistantVersionPreviewSidebars\(\);/);
});

test('regeneration keeps version controls hidden until the completed action list renders', () => {
    const prepareSource = extractFunction('prepareAssistantRegenerationTarget');

    // Preparing the optimistic loading response must not render controls on the
    // pending container or refresh controls on the hidden completed versions.
    assert.doesNotMatch(prepareSource, /updateAssistantVersionSwitcher/);
    assert.match(
        streamSource,
        /if \(container\.dataset\.isStreaming === 'true'\) \{\s*removeAssistantVersionSwitcher\(container\);\s*return;/
    );
    assert.match(
        streamSource,
        /const listDiv = container\.querySelector\('\.assistant-message-list'\);\s*if \(!listDiv\) return;/
    );
    assert.match(
        streamSource,
        /const totalVersions = Math\.max\(referenceContainers\.length, storedTotalVersions\);/
    );
    assert.match(
        streamSource,
        /\/\/ Version switcher - show when there are multiple versions\s*updateAssistantVersionSwitcher\(container\);/
    );
    assert.doesNotMatch(streamSource, /streamingOnly/);
});

test('version navigation preserves the largest server-tracked version total', () => {
    const switchSource = extractFunction('switchAssistantVersion');

    assert.match(switchSource, /const trackedTotalVersions = matchingContainers\.reduce\(/);
    assert.match(
        switchSource,
        /const totalVersions = Math\.max\(1, meaningfulVersionCount, trackedTotalVersions\);/
    );
});

test('version navigation localizes its screen-reader announcement', () => {
    const announcements = [];
    const translationCalls = [];
    const containers = [0, 1].map((retryCount) => ({
        id: `response-version-${retryCount}`,
        dataset: {
            referenceId: 'response-reference',
            retryCount: String(retryCount),
            totalVersions: '2',
        },
        style: {},
        setAttribute() {},
        querySelector() {
            return null;
        },
    }));
    const context = {
        console,
        document: {
            getElementById(id) {
                if (id !== 'chatAreaContainer') return null;
                return {
                    querySelectorAll() {
                        return containers;
                    },
                };
            },
        },
        getChatA11yText(key, fallback, vars) {
            translationCalls.push({ key, fallback, vars });
            return `Version ${vars.current} von ${vars.total}`;
        },
        window: {
            getAssistantContainersByReference() {
                return containers;
            },
            applyAssistantMessageAccessibility() {},
            announceChatMessage(message) {
                announcements.push(message);
            },
        },
    };

    vm.runInNewContext(
        [
            extractFunction('closeAssistantVersionPreviewSidebars'),
            extractFunction('switchAssistantVersion'),
            "switchAssistantVersion('response-reference', 0);",
        ].join('\n'),
        context,
        { filename: 'regeneration.versionAnnouncement.js' },
    );

    assert.deepEqual(announcements, ['Version 1 von 2']);
    assert.deepEqual(
        JSON.parse(JSON.stringify(translationCalls)),
        [{
            key: 'chat_sr_response_version_status',
            fallback: 'Version {current} of {total}',
            vars: { current: 1, total: 2 },
        }],
    );
});

test('normal, regenerated, and split-screen streams isolate follow-up activity after media failure', () => {
    const splitSource = readFrontendSource(path.join(__dirname, 'splitScreen.js'), 'utf8');
    const normalAndRegenerationTransitions = source.match(/transitionMediaGenPlaceholderForToolCall\(/g) || [];
    const splitTransitions = splitSource.match(/transitionMediaGenPlaceholderForToolCall\(/g) || [];

    assert.equal(normalAndRegenerationTransitions.length, 2);
    assert.equal(splitTransitions.length, 1);
    assert.match(source, /mediaGenerationFailed \? 'media-generation-failed'/);
    assert.match(splitSource, /mediaGenerationFailed \? 'media-generation-failed'/);
});
