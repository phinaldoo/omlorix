const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const { readStreamMessagesSource } = require('./messages/source.cjs');
const { readSendMessageSource } = require('./sending/source.cjs');

const NOTES_PATH = path.join(__dirname, 'notes.js');
const SEND_MESSAGE_SOURCE = readSendMessageSource();
const CHAT_BOX_PATH = path.join(__dirname, 'chatBox.js');
const NOTES_CSS_PATH = path.join(__dirname, '..', '..', 'css', 'chat', 'notes.css');
const MARKDOWN_EDITOR_PATH = path.join(__dirname, 'markdown_editor.js');
const STREAM_MESSAGES_SOURCE = readStreamMessagesSource();
const CANVAS_WIDGET_PATH = path.join(__dirname, 'canvas-widget.js');
const CHATS_PATH = path.join(__dirname, 'chats.js');
const CHAT_SCRIPT_PATH = path.join(__dirname, 'script.js');
const SPLIT_SCREEN_PATH = path.join(__dirname, 'splitScreen.js');
const USER_SETTINGS_INIT_PATH = path.join(__dirname, 'userSettings', 'init.js');
const TOOLS_HELPER_PATH = path.join(__dirname, '..', '..', '..', 'backend', 'app', 'tools', 'helper.py');
const NOTES_MODELS_PATH = path.join(__dirname, '..', '..', '..', 'backend', 'app', 'notes', 'models.py');
const I18N_ROOT = path.join(__dirname, '..', '..', 'i18n');

function loadStreamingHelpers() {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const start = notesSource.indexOf('    function parseNotesJson(rawValue)');
    const end = notesSource.indexOf('    function setStatus(', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    const helperSource = notesSource.slice(start, end);
    return Function(`${helperSource}\nreturn { extractStreamingNotesArgs, applyStreamingNoteEdit };`)();
}

function loadNoteReferenceHelpers() {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const start = notesSource.indexOf('function normalizeNoteReferenceSnippet(text)');
    const end = notesSource.indexOf('async function waitForNoteSaveToSettle(', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    const helperSource = notesSource.slice(start, end);
    return Function(
        `const notesT = (_key, fallback) => fallback;\n${helperSource}\nreturn { buildNoteArtifactReferenceText };`
    )();
}

function loadNotesApi(authedFetch) {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const start = notesSource.indexOf('const noteUpdateQueues = new Map();');
    const endMarker = '\n// ============================================================================\n// DOM Helpers';
    const end = notesSource.indexOf(endMarker, start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    const apiSource = notesSource.slice(start, end);
    return Function('window', 'notesT', `${apiSource}\nreturn NotesAPI;`)(
        { authedFetch },
        (_key, fallback) => fallback,
    );
}

function loadArtifactToolActivityHelpers() {
    const source = STREAM_MESSAGES_SOURCE;
    const configStart = source.indexOf('const TOOL_HEADER_CONFIG = {');
    const aliasesStart = source.indexOf('const TOOL_NAME_ALIASES = {', configStart);
    const aliasesEnd = source.indexOf('function getSubagentText(', aliasesStart);
    const helpersStart = source.indexOf('function getToolConfig(', aliasesEnd);
    const helpersEnd = source.indexOf('// Get final header text based on tool calls', helpersStart);
    assert.notEqual(configStart, -1);
    assert.notEqual(aliasesStart, -1);
    assert.notEqual(aliasesEnd, -1);
    assert.notEqual(helpersStart, -1);
    assert.notEqual(helpersEnd, -1);

    return Function(`
        const Icons = {};
        const getStreamText = (_key, fallback) => fallback;
        const getStreamTextFormatted = (_key, fallback, vars = {}) =>
            String(fallback).replace(/\\{(\\w+)\\}/g, (_match, key) => String(vars[key] ?? ''));
        ${source.slice(configStart, aliasesStart)}
        ${source.slice(aliasesStart, aliasesEnd)}
        ${source.slice(helpersStart, helpersEnd)}
        return { getToolActivityArgs, getToolInProgressText, getToolCompletedText };
    `)();
}

test('notes tool streams partial arguments into the preview sidebar', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const sendSource = SEND_MESSAGE_SOURCE;
    const streamSource = STREAM_MESSAGES_SOURCE;

    assert.match(notesSource, /function readStreamingJsonStringField\(buffer, fieldName\)/);
    assert.match(notesSource, /function extractStreamingNotesArgs\(rawArgs\)/);
    assert.match(notesSource, /function handleToolCallDeltaEvent\(obj, messageId\)/);
    assert.match(notesSource, /state\.streamingArgsBuffer \+= delta/);
    assert.match(notesSource, /setStatus\('notes_tool_status_streaming', 'Streaming note\.\.\.', 'generating'\)/);
    assert.match(notesSource, /setVisible\(true\)/);
    assert.match(notesSource, /handleToolCallEvent,[\s\S]*handleToolCallDeltaEvent,[\s\S]*handleNotesEvent/);

    // Both the normal send path and the regeneration path forward starts and
    // deltas, hence each handler must occur twice in the sending source.
    assert.equal((sendSource.match(/NotesToolSidebar\.handleToolCallEvent/g) || []).length, 4);
    assert.equal((sendSource.match(/NotesToolSidebar\.handleToolCallDeltaEvent/g) || []).length, 4);
    const hiddenArgsSet = streamSource.match(/TOOL_ARGS_HIDDEN = new Set\(\[([\s\S]*?)\]\);/)?.[1] || '';
    assert.match(hiddenArgsSet, /'canvas'/);
    assert.match(hiddenArgsSet, /'notes'/);
    assert.match(hiddenArgsSet, /'create_visualization'/);
    assert.match(streamSource, /const hideToolArgs = shouldHideToolArguments\(effectiveToolName\)/);
    assert.match(streamSource, /if \(hideToolArgs\) \{[\s\S]*previewText: '',[\s\S]*keepVisible: false/);
    assert.doesNotMatch(streamSource, /if \(!effectiveToolName \|\| !delta \|\| shouldHideToolArguments\(effectiveToolName\)\)/);
});

test('streamed note edits compose against the existing note and avoid undo history growth', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');

    assert.match(notesSource, /function applyStreamingNoteEdit\(baseContent, args\)/);
    assert.match(notesSource, /NotesAPI\.fetchNoteContent\(normalizedNoteId\)/);
    assert.match(notesSource, /startSnippet === endSnippet/);
    assert.match(notesSource, /function ensureStreamingPreviewElement\(\)/);
    assert.match(notesSource, /preview\.className = 'notes-tool-streaming-preview canvas-markdown-render markdown-body'/);
    assert.doesNotMatch(notesSource, /state\.editor\.setValue\(previewContent/);
});

test('persisted notes result boxes finalize the completed thinking section', () => {
    const streamSource = STREAM_MESSAGES_SOURCE;
    const appendStart = streamSource.indexOf('function appendAssistantWidget(messageId, widgetHtml, widgetType');
    const appendEnd = streamSource.indexOf('function hydrateWidgetByName(', appendStart);
    const appendSource = streamSource.slice(appendStart, appendEnd);

    assert.match(appendSource, /finalizeThinkingBlocks\(assistantMessageContainer\)/);
    assert.match(appendSource, /widgetWrapper\.dataset\.widgetType = widgetType \|\| 'unknown'/);
});

test('notes create streams a stable file box before persistence and upgrades it in place', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const streamSource = STREAM_MESSAGES_SOURCE;
    const liveWidgetStart = notesSource.indexOf('    function injectStreamingResultWidget(');
    const liveWidgetEnd = notesSource.indexOf('    function finalizeStreamingResultWidget(', liveWidgetStart);
    const finalWidgetEnd = notesSource.indexOf('    /**\n     * Register an active note', liveWidgetEnd);
    const liveWidgetSource = notesSource.slice(liveWidgetStart, liveWidgetEnd);
    const finalWidgetSource = notesSource.slice(liveWidgetEnd, finalWidgetEnd);
    const appendStart = streamSource.indexOf('function appendAssistantWidget(messageId, widgetHtml, widgetType');
    const appendEnd = streamSource.indexOf('function hydrateWidgetByName(', appendStart);
    const appendSource = streamSource.slice(appendStart, appendEnd);

    assert.doesNotMatch(liveWidgetSource, /finalizeThinkingForMessage/);
    assert.match(liveWidgetSource, /data-notes-call-id/);
    assert.match(liveWidgetSource, /data-note-open="true"/);
    assert.match(liveWidgetSource, /wrapper\.dataset\.widgetType = 'notes_result'/);
    assert.match(finalWidgetSource, /finalizeThinkingForMessage\(normalizedMessageId\)/);
    assert.match(finalWidgetSource, /widget\.dataset\.noteStatus = 'saved'/);
    assert.match(finalWidgetSource, /delete widget\.dataset\.notesCallId/);
    assert.match(notesSource, /injectStreamingResultWidget\(state\.activeMessageId,[\s\S]*const preview = ensureStreamingPreviewElement\(\)/);
    assert.match(notesSource, /if \(String\(obj\.event \|\| ''\) !== 'saved'\) return;[\s\S]*finalizeStreamingResultWidget\(messageId, data\)/);

    assert.match(streamSource, /notes: \{[\s\S]*inProgress: 'Writing note',[\s\S]*completed: 'Saved note'/);
    assert.match(appendSource, /if \(widgetType === 'notes_result'\)/);
    assert.match(appendSource, /existingWrapper\.__chatWidgetPayload = \{/);
    assert.match(appendSource, /\.notes-tool-result-widget\[data-note-id=/);
});

test('notes edits keep the tool activity row without rendering another file box', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const streamSource = STREAM_MESSAGES_SOURCE;
    const backendSource = readFrontendSource(TOOLS_HELPER_PATH, 'utf8');
    const removeStart = notesSource.indexOf('    function removeStreamingResultWidget(');
    const removeEnd = notesSource.indexOf('    /**\n     * Add the same generated-file card', removeStart);
    const removeSource = notesSource.slice(removeStart, removeEnd);

    assert.match(notesSource, /if \(normalizedOperation !== 'create'\) \{[\s\S]*removeStreamingResultWidget[\s\S]*return null/);
    assert.match(notesSource, /if \(operation !== 'create'\) \{[\s\S]*removeStreamingResultWidget[\s\S]*return null/);
    assert.doesNotMatch(removeSource, /finalizeThinkingForMessage/);
    assert.match(streamSource, /if \(operation && operation !== 'create'\) \{\s*return;\s*\}/);
    assert.match(backendSource, /if str\(operation or ""\)\.strip\(\)\.lower\(\) != "create":\s*return None/);
});

test('notes view calls preserve the current editor and report a viewed operation', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const streamSource = STREAM_MESSAGES_SOURCE;
    const beginStart = notesSource.indexOf('    function beginStreamingToolCall(');
    const beginEnd = notesSource.indexOf('    function handleToolCallEvent(', beginStart);
    const beginSource = notesSource.slice(beginStart, beginEnd);
    const eventStart = notesSource.indexOf('    function handleNotesEvent(');
    const eventEnd = notesSource.indexOf('    function initWidget(', eventStart);
    const eventSource = notesSource.slice(eventStart, eventEnd);

    assert.doesNotMatch(beginSource, /destroyEditor\(\)/);
    assert.doesNotMatch(beginSource, /state\.activeNoteId = ''/);
    assert.match(eventSource, /if \(operation === 'view'\) \{[\s\S]*clearStreamingToolCallState\(\);[\s\S]*return;/);
    assert.doesNotMatch(eventSource.match(/if \(operation === 'view'\)[\s\S]*?\n        \}/)?.[0] || '', /openNote\(/);
    assert.match(streamSource, /assistant_tool_notes_view_in_progress/);
    assert.match(streamSource, /assistant_tool_notes_view_completed/);
    assert.match(streamSource, /const toolActivityArgs = hideToolArgs[\s\S]*getToolActivityArgs/);
});

test('artifact tool headers classify view and edit without retaining document content', () => {
    const {
        getToolActivityArgs,
        getToolInProgressText,
        getToolCompletedText,
    } = loadArtifactToolActivityHelpers();

    const canvasActivity = getToolActivityArgs('canvas', {
        type: 'view',
        file_id: 'canvas-1',
        content: 'private canvas body',
    });
    assert.deepEqual(canvasActivity, {
        type: 'view',
        file_id: 'canvas-1',
        has_content: true,
    });
    assert.equal(Object.hasOwn(canvasActivity, 'content'), false);
    assert.equal(getToolInProgressText('canvas', canvasActivity), 'Viewing canvas');
    assert.equal(getToolCompletedText('canvas', canvasActivity), 'Viewed canvas');

    const partialNotesActivity = getToolActivityArgs('notes', '{"type":"view","note_id":"note-1"');
    assert.deepEqual(partialNotesActivity, { type: 'view' });
    assert.equal(getToolInProgressText('notes', partialNotesActivity), 'Viewing note');
    assert.equal(getToolCompletedText('notes', partialNotesActivity), 'Viewed note');
    assert.equal(getToolCompletedText('notes', { type: 'edit' }), 'Updated note');
    assert.equal(getToolInProgressText('notes', { type: 'list' }), 'Listing notes');
    assert.equal(getToolCompletedText('notes', { type: 'list' }), 'Listed notes');
});

test('notes preserve debounced edits across sidebar and navigation teardown', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const persistStart = notesSource.indexOf('    function persistPendingEditsBeforeTeardown()');
    const persistEnd = notesSource.indexOf('    function clearCopyFeedback()', persistStart);
    const persistSource = notesSource.slice(persistStart, persistEnd);
    const hideStart = notesSource.indexOf('    function hidePreviewPanel()');
    const hideEnd = notesSource.indexOf('    function refreshWidgetButtons()', hideStart);
    const resetStart = notesSource.indexOf('    function reset()');
    const resetEnd = notesSource.indexOf("    window.addEventListener('resize'", resetStart);

    assert.match(persistSource, /const noteId = String\(state\.activeNoteId/);
    assert.match(persistSource, /const content = String\(getEditorValue\(\)/);
    assert.match(persistSource, /backgroundSaveSignature === signature/);
    assert.match(persistSource, /waitForNoteSaveToSettle\(\(\) => state\.isSaving\)/);
    assert.match(persistSource, /NotesAPI\.updateNote\(noteId, content, revisionForSave\)/);
    assert.match(notesSource.slice(hideStart, hideEnd), /persistPendingEditsBeforeTeardown\(\)/);
    assert.match(notesSource.slice(resetStart, resetEnd), /persistPendingEditsBeforeTeardown\(\)/);
});

test('closing Notes commits hidden and inert state even when sidebar restoration fails', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const visibilityStart = notesSource.indexOf('    function setVisible(visible)');
    const visibilityEnd = notesSource.indexOf('    function refreshWidgetButtons()', visibilityStart);
    assert.notEqual(visibilityStart, -1);
    assert.notEqual(visibilityEnd, -1);

    const bodyClasses = new Set();
    const panelClasses = new Set(['visible']);
    const panelAttributes = new Map([['aria-hidden', 'false']]);
    const classList = (classes) => ({
        toggle(name, enabled) {
            if (enabled) classes.add(name);
            else classes.delete(name);
        },
    });
    const state = {
        isVisible: true,
        activeNoteId: 'note-1',
        activeMessageId: 'message-1',
        streamingMessageId: '',
        dismissedPreviewMessageId: '',
        panel: {
            classList: classList(panelClasses),
            setAttribute(name, value) {
                panelAttributes.set(name, String(value));
            },
            toggleAttribute(name, enabled) {
                if (enabled) panelAttributes.set(name, '');
                else panelAttributes.delete(name);
            },
        },
    };
    let saveSnapshots = 0;
    const runtime = Function(
        'state',
        'document',
        'window',
        'console',
        'applyWidthRatio',
        'stopResize',
        'closeSidebar',
        'setDownloadEnabled',
        'refreshWidgetButtons',
        'persistPendingEditsBeforeTeardown',
        `${notesSource.slice(visibilityStart, visibilityEnd)}
         return { hidePreviewPanel };`,
    )(
        state,
        { body: { classList: classList(bodyClasses) } },
        {
            setMainSidebarAutoCollapsed() {
                throw new Error('sidebar restore failed');
            },
        },
        { warn() {} },
        () => {},
        () => {},
        () => {},
        () => {},
        () => {},
        () => {
            saveSnapshots += 1;
            return Promise.resolve(true);
        },
    );

    runtime.hidePreviewPanel();

    assert.equal(saveSnapshots, 1);
    assert.equal(state.isVisible, false);
    assert.equal(state.dismissedPreviewMessageId, 'message-1');
    assert.equal(bodyClasses.has('notes-tool-preview-open'), false);
    assert.equal(panelClasses.has('visible'), false);
    assert.equal(panelAttributes.get('aria-hidden'), 'true');
    assert.equal(panelAttributes.has('inert'), true);
});

test('Notes close resists late events and binds dismissal before optional enhancements', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const closeBinding = notesSource.indexOf("state.closeBtn?.addEventListener('click', hidePreviewPanel)");
    const downloadEnhancement = notesSource.indexOf('enhanceDownloadFormatSelect?.(state.downloadFormat');

    assert.notEqual(closeBinding, -1);
    assert.notEqual(downloadEnhancement, -1);
    assert.ok(closeBinding < downloadEnhancement);
    assert.match(notesSource, /panel\.setAttribute\('inert', ''\)/);
    assert.match(notesSource, /state\.panel\?\.toggleAttribute\('inert', !state\.isVisible\)/);
    assert.match(notesSource, /String\(messageId \|\| ''\)\.trim\(\) === state\.dismissedPreviewMessageId/);
    assert.match(notesSource, /normalizedMessageId === state\.dismissedPreviewMessageId[\s\S]*clearStreamingToolCallState\(\)/);
    assert.match(notesSource, /options\.automatic === true[\s\S]*requestedMessageId === state\.dismissedPreviewMessageId/);
    assert.equal((notesSource.match(/if \(!isMessageMounted\(/g) || []).length, 3);
});

test('note writes are serialized and flushed before chat generations begin', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const sendSource = SEND_MESSAGE_SOURCE;
    const splitSource = readFrontendSource(SPLIT_SCREEN_PATH, 'utf8');
    const modelSource = readFrontendSource(NOTES_MODELS_PATH, 'utf8');

    assert.match(notesSource, /const noteUpdateQueues = new Map\(\)/);
    assert.match(notesSource, /const requestedRevision = normalizeNoteRevisionToken\(expectedUpdatedAt\)/);
    assert.match(notesSource, /const priorWrite = noteUpdateQueues\.get\(normalizedNoteId\) \|\| null/);
    assert.match(notesSource, /priorWrite\.baseRevision === requestedRevision/);
    assert.match(notesSource, /const payload = \{ content, expected_updated_at: expectedRevision \}/);
    assert.match(notesSource, /NotesState\.currentNoteUpdatedAt = normalizeNoteRevisionToken\(contentData\.updated_at\)/);
    assert.match(notesSource, /NotesAPI\.updateNote\(noteId, content, expectedUpdatedAt\)/);
    assert.match(notesSource, /state\.lastSavedUpdatedAt = normalizeNoteRevisionToken\(contentData\?\.updated_at\)/);
    assert.match(notesSource, /flushPendingEdits: persistPendingEditsBeforeTeardown/);
    assert.equal((sendSource.match(/NotesToolSidebar\?\.flushPendingEdits/g) || []).length, 2);
    assert.equal((splitSource.match(/NotesToolSidebar\?\.flushPendingEdits/g) || []).length, 1);
    assert.match(splitSource, /try \{[\s\S]*await window\.NotesToolSidebar\.flushPendingEdits\(\)[\s\S]*if \(!notesSaved\) \{[\s\S]*notifyError\(splitScreenT\('split_screen_send_cancelled_notes_unsaved'/);
    assert.match(splitSource, /catch \(error\) \{[\s\S]*notifyError\(error\?\.message \|\| splitScreenT\('notes_error_save_note'/);

    // The server is the final safety boundary: every write compares the
    // revision it observed, including full-note PATCH and tool replacements.
    assert.match(modelSource, /Notes\.updated_at == observed_updated_at/);
    assert.match(modelSource, /if updated_count != 1:[\s\S]*status_code=status\.HTTP_409_CONFLICT/);
    assert.doesNotMatch(modelSource, /else:\s*note\.content = note_content/);
});

test('queued note saves advance only their local revision chain', async () => {
    const requests = [];
    let releaseFirstRequest;
    const firstResponse = new Promise((resolve) => {
        releaseFirstRequest = resolve;
    });
    const NotesAPI = loadNotesApi(async (_url, init) => {
        requests.push(JSON.parse(init.body));
        if (requests.length === 1) return firstResponse;
        return {
            ok: true,
            json: async () => ({ updated_at: requests.length === 2 ? 'rev-3' : 'rev-4' }),
        };
    });

    const firstSave = NotesAPI.updateNote('note-1', 'first local edit', 'rev-1');
    const secondSave = NotesAPI.updateNote('note-1', 'second local edit', 'rev-1');
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(requests.length, 1);
    assert.equal(requests[0].expected_updated_at, 'rev-1');

    releaseFirstRequest({
        ok: true,
        json: async () => ({ updated_at: 'rev-2' }),
    });
    await Promise.all([firstSave, secondSave]);
    assert.equal(requests[1].expected_updated_at, 'rev-2');

    // Once the local queue is empty, an explicitly stale editor revision must
    // remain stale; the API helper must not silently replace it with rev-3.
    await NotesAPI.updateNote('note-1', 'stale independent edit', 'rev-1');
    assert.equal(requests[2].expected_updated_at, 'rev-1');
});

test('note update exposes a typed optimistic-lock conflict', async () => {
    const NotesAPI = loadNotesApi(async () => ({
        ok: false,
        status: 409,
        json: async () => ({ detail: 'Note changed before this edit could be applied.' }),
    }));

    await assert.rejects(
        NotesAPI.updateNote('note-conflict', 'local draft', 'revision-1'),
        (error) => {
            assert.equal(error.name, 'NoteRevisionConflictError');
            assert.equal(error.status, 409);
            assert.equal(error.code, 'note_revision_conflict');
            return true;
        },
    );
});

test('note deletion binds the user-observed revision', async () => {
    const requests = [];
    const NotesAPI = loadNotesApi(async (url, init) => {
        requests.push({ url, init });
        return { ok: true, json: async () => ({ ok: true }) };
    });

    await NotesAPI.deleteNote('note-1', 'revision-1');

    assert.equal(requests[0].init.method, 'DELETE');
    assert.deepEqual(JSON.parse(requests[0].init.body), { expected_updated_at: 'revision-1' });

    await assert.rejects(NotesAPI.deleteNote('note-1', ''), /Reload the note/);
});

test('non-artifact calls release the Notes preview slot for the next call', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const retireStart = notesSource.indexOf('    function retireSupersededNonArtifactCall(');
    const retireEnd = notesSource.indexOf('    function beginStreamingToolCall(', retireStart);
    const eventStart = notesSource.indexOf('    function handleToolCallEvent(');
    const eventEnd = notesSource.indexOf('    function handleToolCallDeltaEvent(', eventStart);
    const deltaEnd = notesSource.indexOf('    function handleNotesEvent(', eventEnd);

    assert.notEqual(retireStart, -1);
    assert.match(
        notesSource.slice(retireStart, retireEnd),
        /!\['list', 'delete'\]\.includes\(activeOperation\)/,
    );
    assert.match(notesSource.slice(retireStart, retireEnd), /clearStreamingToolCallState\(\)/);
    assert.ok(
        notesSource.slice(eventStart, eventEnd).indexOf('retireSupersededNonArtifactCall(callId, messageId)')
            < notesSource.slice(eventStart, eventEnd).indexOf('queueStreamingToolCall('),
    );
    assert.ok(
        notesSource.slice(eventEnd, deltaEnd).indexOf('retireSupersededNonArtifactCall(callId, normalizedMessageId)')
            < notesSource.slice(eventEnd, deltaEnd).indexOf('queueStreamingToolCall('),
    );
});

test('a delete call is retired before a later create or edit call in one response', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const retireStart = notesSource.indexOf('    function retireSupersededNonArtifactCall(');
    const retireEnd = notesSource.indexOf('    function beginStreamingToolCall(', retireStart);
    const retireSource = notesSource.slice(retireStart, retireEnd);

    for (const nextOperation of ['create', 'edit']) {
        const state = {
            streamingCallId: 'delete-call',
            streamingMessageId: 'assistant-response',
            streamingOperation: 'delete',
            streamingArgsBuffer: '{"operation":"delete"}',
        };
        const streamingCallIdsByMessage = new Map([
            ['assistant-response', 'delete-call'],
        ]);
        const queuedStreamingCalls = new Map();
        let cleared = false;
        const retireSupersededNonArtifactCall = Function(
            'state',
            'streamingCallIdsByMessage',
            'queuedStreamingCalls',
            'extractStreamingNotesArgs',
            'clearStreamingToolCallState',
            'refreshWidgetButtons',
            `${retireSource}\nreturn retireSupersededNonArtifactCall;`,
        )(
            state,
            streamingCallIdsByMessage,
            queuedStreamingCalls,
            () => ({ operation: 'delete' }),
            () => {
                cleared = true;
                state.streamingCallId = '';
                state.streamingMessageId = '';
                state.streamingOperation = '';
            },
            () => {},
        );

        assert.equal(
            retireSupersededNonArtifactCall(`${nextOperation}-call`, 'assistant-response'),
            true,
        );
        assert.equal(cleared, true);
        assert.equal(state.streamingCallId, '');
        assert.equal(streamingCallIdsByMessage.has('assistant-response'), false);
    }
});

test('concurrent split-screen Notes calls retain isolated argument buffers', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const queueStart = notesSource.indexOf('    function queueStreamingToolCall(');
    const queueEnd = notesSource.indexOf('    function beginStreamingToolCall(', queueStart);
    const deltaStart = notesSource.indexOf('    function handleToolCallDeltaEvent(');
    const deltaEnd = notesSource.indexOf('    function handleNotesEvent(', deltaStart);
    const eventStart = notesSource.indexOf('    function handleNotesEvent(');
    const eventEnd = notesSource.indexOf('    function handleStreamEnd(', eventStart);

    assert.match(notesSource, /const streamingCallIdsByMessage = new Map\(\)/);
    assert.match(notesSource, /const queuedStreamingCalls = new Map\(\)/);
    assert.match(notesSource.slice(queueStart, queueEnd), /argsBuffer: append \? `\$\{existing\.argsBuffer \|\| ''\}\$\{nextArgs\}` : nextArgs/);
    assert.match(notesSource.slice(deltaStart, deltaEnd), /state\.streamingCallId && !isNowActiveCall[\s\S]*queueStreamingToolCall\(descriptor, delta/);
    assert.match(notesSource.slice(eventStart, eventEnd), /eventCallId !== state\.streamingCallId[\s\S]*queuedStreamingCalls\.delete\(eventCallId\)/);
    assert.match(notesSource, /function promoteQueuedStreamingToolCall\(\)/);
    assert.match(notesSource, /streamingCallIdsByMessage\.clear\(\);[\s\S]*queuedStreamingCalls\.clear\(\)/);
});

test('notes streaming saves and restores a displaced dirty editor', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const beginStart = notesSource.indexOf('    function beginStreamingToolCall(');
    const beginEnd = notesSource.indexOf('    function handleToolCallEvent(', beginStart);
    const beginSource = notesSource.slice(beginStart, beginEnd);
    const cleanupStart = notesSource.indexOf('    function handleStreamEnd(messageId)');
    const cleanupEnd = notesSource.indexOf('    function initWidget(', cleanupStart);
    const cleanupSource = notesSource.slice(cleanupStart, cleanupEnd);

    assert.match(beginSource, /const originContent = originNoteId \? String\(getEditorValue\(\) \|\| ''\) : ''/);
    assert.match(beginSource, /const originSavePromise = originNoteId[\s\S]*persistPendingEditsBeforeTeardown\(\)/);
    assert.ok(
        beginSource.indexOf('persistPendingEditsBeforeTeardown()')
            < beginSource.indexOf('state.streamingOriginContent = originContent'),
    );
    assert.match(cleanupSource, /content: String\(state\.streamingOriginContent \|\| ''\)/);
    assert.match(cleanupSource, /if \(origin\.noteId\) \{/);
    assert.match(cleanupSource, /renderEditor\(origin\.content, \{ editable: origin\.canEdit, focus: false \}\)/);
    assert.match(cleanupSource, /restorePreviewTrackScroll\(/);
    assert.match(cleanupSource, /state\.referencedFiles = origin\.referencedFiles/);
});

test('unresolved notes previews are removed on errors, cancellation, and stream completion', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const sendSource = SEND_MESSAGE_SOURCE;
    const splitSource = readFrontendSource(SPLIT_SCREEN_PATH, 'utf8');
    const cleanupStart = notesSource.indexOf('    function handleStreamEnd(messageId)');
    const cleanupEnd = notesSource.indexOf('    function initWidget(', cleanupStart);
    const cleanupSource = notesSource.slice(cleanupStart, cleanupEnd);

    assert.match(cleanupSource, /removeStreamingResultWidget\(normalizedMessageId\)/);
    assert.match(cleanupSource, /clearStreamingToolCallState\(\)/);
    assert.match(cleanupSource, /state\.track\?\.contains\(preview\)/);
    assert.match(notesSource, /handleNotesEvent,[\s\S]*handleStreamEnd,[\s\S]*openNote/);
    assert.ok((sendSource.match(/NotesToolSidebar\.handleStreamEnd/g) || []).length >= 4);
    assert.ok((splitSource.match(/NotesToolSidebar\.handleStreamEnd/g) || []).length >= 2);
});

test('notes selections use the Canvas reference button and reach the next chat request', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const editorSource = readFrontendSource(MARKDOWN_EDITOR_PATH, 'utf8');
    const chatBoxSource = readFrontendSource(CHAT_BOX_PATH, 'utf8');
    const sendSource = SEND_MESSAGE_SOURCE;
    const { buildNoteArtifactReferenceText } = loadNoteReferenceHelpers();
    const reference = buildNoteArtifactReferenceText({
        text: 'Exact selected paragraph',
        noteId: 'note-123',
        title: 'Project notes',
        source: 'notes tool preview',
    });

    assert.match(reference, /^\[Notes artifact reference\]/);
    assert.match(reference, /Tool to edit: notes/);
    assert.match(reference, /note_id: note-123/);
    assert.match(reference, /start_snippet and end_snippet/);
    assert.match(reference, /Marked text:\n```\nExact selected paragraph\n```$/);

    assert.equal((notesSource.match(/onReferenceSelection: \(selectionData\) => addNoteSelectionToChatReferences\(/g) || []).length, 2);
    assert.match(notesSource, /window\.addReferencePart\(referenceText\)/);
    assert.match(notesSource, /window\.getSelectedReferenceParts\(\)/);
    assert.match(notesSource, /const inserted = insertNoteReferenceIntoComposer\(selectedText\)/);
    assert.doesNotMatch(notesSource, /insertNoteReferenceIntoComposer\(referenceText\)/);
    assert.match(editorSource, /typeof options\.onReferenceSelection !== 'function'/);
    assert.match(editorSource, /window\.createSelectionActionTooltip\(\{/);
    assert.match(editorSource, /data-action="add-reference"|onAddReference:/);
    assert.match(editorSource, /chat_selection_copy_label/);
    assert.match(editorSource, /canvas_add_selection_reference_label/);
    assert.match(chatBoxSource, /function createSelectionActionTooltip\(/);
    assert.match(chatBoxSource, /function addReferencePart\(text\)/);
    assert.match(chatBoxSource, /function extractMarkedReferenceText\(text\)/);
    assert.match(sendSource, /reference_parts: payloadReferenceParts\.length \? payloadReferenceParts : null/);
});

test('notes streaming throttles full renders and keeps auto-follow vertical and user-controlled', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const notesCss = readFrontendSource(NOTES_CSS_PATH, 'utf8');

    assert.match(notesSource, /const STREAM_RENDER_INTERVAL_MS = 100/);
    assert.match(notesSource, /function scheduleStreamingPreview\(messageId, \{ immediate = false \} = \{\}\)/);
    assert.match(notesSource, /if \(state\.streamingRenderTimer\) return/);
    assert.match(notesSource, /state\.track\.scrollLeft = 0/);
    assert.match(notesSource, /if \(event\.deltaY < 0\) stopStreamingAutoFollow\(\)/);
    assert.match(notesSource, /event\.clientX >= rect\.right - 18/);
    assert.match(notesSource, /if \(remaining > STREAM_SCROLL_BOTTOM_THRESHOLD\) stopStreamingAutoFollow\(\)/);
    assert.match(notesSource, /state\.streamingAutoFollow = true/);
    assert.match(notesSource, /state\.streamingAutoFollow = false/);
    assert.doesNotMatch(notesSource, /state\.streamingAutoFollow = remaining/);
    assert.match(notesSource, /editorView\.scrollLeft = autoFollow\s*\? 0\s*:\s*Math\.max\(Number\(editorScrollState\?\.editorScrollLeft\) \|\| 0, 0\)/);
    assert.match(notesCss, /\.notes-tool-preview-track[\s\S]*overflow-x: hidden;[\s\S]*overflow-anchor: none;/);
    assert.match(notesCss, /\.notes-tool-streaming-preview[\s\S]*max-width: 100%;[\s\S]*overflow-x: hidden;/);
});

test('notes preview follows the Canvas lifecycle across chat and app navigation', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const canvasSource = readFrontendSource(CANVAS_WIDGET_PATH, 'utf8');
    const chatsSource = readFrontendSource(CHATS_PATH, 'utf8');
    const chatScriptSource = readFrontendSource(CHAT_SCRIPT_PATH, 'utf8');
    const splitScreenSource = readFrontendSource(SPLIT_SCREEN_PATH, 'utf8');
    const userSettingsSource = readFrontendSource(USER_SETTINGS_INIT_PATH, 'utf8');

    // Both sidebars delegate exclusive handoff to the shared preview manager.
    assert.match(notesSource, /closeOtherArtifactPreviews\?\.\('notes-preview'\)/);
    assert.match(canvasSource, /closeOtherArtifactPreviews\('canvas-preview'\)/);

    // Chat switches first close stale sidebars and then reset artifact state
    // before rendering the replacement transcript.
    assert.match(chatsSource, /isSwitchingChats[\s\S]*window\.NotesToolSidebar\.hidePreviewPanel\(\)/);
    assert.match(chatsSource, /messagesContainer\.innerHTML = '';[\s\S]*window\.NotesToolSidebar\.reset\(\)/);

    // Leaving chat, starting a fresh chat, and every split-screen teardown use
    // the same reset contract as Canvas.
    assert.equal((chatScriptSource.match(/window\.NotesToolSidebar\.reset\(\)/g) || []).length, 2);
    const splitResetStart = splitScreenSource.indexOf('    function resetPanels()');
    const splitResetEnd = splitScreenSource.indexOf('    // ───── Panel Header Updates', splitResetStart);
    const splitResetSource = splitScreenSource.slice(splitResetStart, splitResetEnd);
    const temporaryRestoreStart = splitScreenSource.indexOf('    function restorePanelAsTemporaryMainView(');
    const temporaryRestoreEnd = splitScreenSource.indexOf('    function getFallbackPanelForRestore(', temporaryRestoreStart);
    const temporaryRestoreSource = splitScreenSource.slice(temporaryRestoreStart, temporaryRestoreEnd);
    assert.match(splitResetSource, /window\.canvasMarkdownWidget\.reset\(\)[\s\S]*window\.NotesToolSidebar\.reset\(\)/);
    assert.doesNotMatch(temporaryRestoreSource, /canvasMarkdownWidget\.reset|NotesToolSidebar\.reset/);
    assert.match(splitScreenSource, /function disable\(options = \{\}\)[\s\S]*resetPanels\(\)/);

    // Settings overlays the chat rather than rebuilding it, so it closes both
    // sidebars without discarding their underlying document state.
    assert.match(userSettingsSource, /function closeChatPreviewPanelsForUserSettings\(\)[\s\S]*window\.NotesToolSidebar/);
});

test('split-screen streams forward Canvas and Notes tool lifecycles consistently', () => {
    const splitScreenSource = readFrontendSource(SPLIT_SCREEN_PATH, 'utf8');

    assert.equal((splitScreenSource.match(/NotesToolSidebar\.handleToolCallEvent\(obj, messageId\)/g) || []).length, 1);
    assert.equal((splitScreenSource.match(/NotesToolSidebar\.handleToolCallDeltaEvent\(obj, messageId\)/g) || []).length, 1);
    assert.match(splitScreenSource, /obj\.t === 'notes_evt'[\s\S]*NotesToolSidebar\.handleNotesEvent\(obj, messageId\)/);
    assert.match(splitScreenSource, /Failed to start split-screen notes preview/);
    assert.match(splitScreenSource, /Failed to update split-screen notes preview/);
    assert.match(splitScreenSource, /Failed to finalize split-screen notes preview/);
});

test('reattached split-screen generations clean up unresolved notes previews', () => {
    const splitScreenSource = readFrontendSource(SPLIT_SCREEN_PATH, 'utf8');
    const attachStart = splitScreenSource.indexOf('    async function attachPanelToGeneration(');
    const attachEnd = splitScreenSource.indexOf('    // ───── Send Message to Panel', attachStart);
    const attachSource = splitScreenSource.slice(attachStart, attachEnd);

    assert.match(attachSource, /let streamedMessageId = ''/);
    assert.match(attachSource, /streamedMessageId = await processStream\(/);
    assert.match(attachSource, /onMessageId: \(nextMessageId\) => \{[\s\S]*streamedMessageId = String\(nextMessageId \|\| ''\)/);
    assert.match(attachSource, /NotesToolSidebar\.handleStreamEnd\(streamedMessageId\)/);
    assert.match(attachSource, /Failed to clean up reattached split-screen notes preview/);
});

test('streaming reuses the configured Markdown parser and preserves interrupted scroll on completion', () => {
    const notesSource = readFrontendSource(NOTES_PATH, 'utf8');
    const editorSource = readFrontendSource(MARKDOWN_EDITOR_PATH, 'utf8');

    assert.match(editorSource, /let sharedMarkdownRenderer = null/);
    assert.match(editorSource, /function getMarkdownRenderer\(\)/);
    assert.match(editorSource, /sharedMarkdownRenderer = renderer/);
    assert.match(editorSource, /const renderer = getMarkdownRenderer\(\)/);
    assert.match(notesSource, /function capturePreviewScrollState\(\)/);
    assert.match(notesSource, /state\.editor\?\.getScrollState\?\.\(\) \|\| null/);
    assert.match(notesSource, /const originScrollState = state\.streamingOriginScrollState/);
    assert.match(notesSource, /state\.streamingPreservedScrollTop/);
    assert.match(notesSource, /state\.streamingUserControlledScroll/);
    assert.match(notesSource, /originScrollState\?\.editorScrollState/);
    assert.match(notesSource, /state\.editor\.restoreScrollState\(editorScrollState\)/);
    assert.match(notesSource, /const originNoteId = String\(state\.activeNoteId \|\| ''\)\.trim\(\)/);
    assert.match(notesSource, /state\.streamingOriginNoteId = originNoteId/);
    assert.match(notesSource, /isEditingOriginNote && !state\.streamingUserControlledScroll/);
    assert.match(notesSource, /state\.streamingPreviewEl === preview && !state\.streamingAutoFollow/);
    assert.match(notesSource, /function reconcileStreamingNotesPreview\(target, renderedHtml\)/);
    assert.match(notesSource, /currentNodes\[stablePrefixLength\]\.isEqualNode\(nextNodes\[stablePrefixLength\]\)/);
    assert.match(notesSource, /reconcileStreamingNotesPreview\(preview, renderedHtml\)/);
    assert.doesNotMatch(notesSource, /preview\.innerHTML = window\.ChatMarkdownBlockEditor\.renderMarkdownToHtml/);
});

test('partial JSON decoding produces live Markdown before the tool call is complete', () => {
    const { extractStreamingNotesArgs, applyStreamingNoteEdit } = loadStreamingHelpers();
    const args = extractStreamingNotesArgs(
        '{"type":"edit","note_id":"note-1","start_snippet":"Old","end_snippet":"text","content":"# New\\n\\nLive',
    );

    assert.deepEqual(args, {
        operation: 'edit',
        noteId: 'note-1',
        content: '# New\n\nLive',
        startSnippet: 'Old',
        endSnippet: 'text',
        hasContent: true,
    });
    assert.equal(
        applyStreamingNoteEdit('Before Old middle text After', args),
        'Before # New\n\nLive After',
    );
});

test('notes live-stream and tool lifecycle states are translated in every supported locale', () => {
    const localeDirectories = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    for (const locale of localeDirectories) {
        const translationPath = path.join(I18N_ROOT, locale, 'index.json');
        const translations = JSON.parse(readFrontendSource(translationPath, 'utf8'));
        assert.equal(typeof translations.notes_tool_status_streaming, 'string', `${locale} is missing notes_tool_status_streaming`);
        assert.ok(translations.notes_tool_status_streaming.trim(), `${locale} has an empty notes_tool_status_streaming`);
        for (const key of [
            'assistant_tool_notes_name',
            'assistant_tool_notes_in_progress',
            'assistant_tool_notes_completed',
            'assistant_tool_notes_list_in_progress',
            'assistant_tool_notes_list_completed',
            'assistant_tool_notes_edit_in_progress',
            'assistant_tool_notes_edit_completed',
            'assistant_tool_notes_view_in_progress',
            'assistant_tool_notes_view_completed',
            'assistant_tool_canvas_edit_in_progress',
            'assistant_tool_canvas_edit_completed',
            'assistant_tool_canvas_view_in_progress',
            'assistant_tool_canvas_view_completed',
        ]) {
            assert.equal(typeof translations[key], 'string', `${locale} is missing ${key}`);
            assert.ok(translations[key].trim(), `${locale} has an empty ${key}`);
        }
    }
});
