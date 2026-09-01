const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const vm = require('node:vm');
const { readStreamMessagesSource } = require('./messages/source.cjs');
const { readSendMessageSource } = require('./sending/source.cjs');

const FRONTEND_ROOT = path.resolve(__dirname, '../..');
const SPLIT_PATH = path.join(__dirname, 'splitScreen.js');
const MODEL_SELECT_PATH = path.join(__dirname, 'modelSelect.js');
const CHAT_DOWNLOAD_PATH = path.join(__dirname, 'chatDownload.js');
const CHATS_HELPER_PATH = path.join(__dirname, 'chatsHelper.js');
const SCRIPT_PATH = path.join(__dirname, 'script.js');
const PROJECTS_CHAT_PATH = path.join(__dirname, 'projectsChat.js');
const WORKSPACE_PATH = path.join(__dirname, 'workspace.js');
const INDEX_PATH = path.join(FRONTEND_ROOT, 'index.html');
const CSS_PATH = path.join(FRONTEND_ROOT, 'css/chat/splitScreen.css');
const MODEL_SELECT_CSS_PATH = path.join(FRONTEND_ROOT, 'css/chat/modelSelect.css');
const I18N_ROOT = path.join(FRONTEND_ROOT, 'i18n');

function read(filePath) {
    return readFrontendSource(filePath, 'utf8');
}

/** Return the sorted, unique interpolation tokens used by a translation. */
function extractInterpolationPlaceholders(value) {
    return [...new Set(value.match(/\{[A-Za-z_][A-Za-z0-9_]*\}/g) || [])].sort();
}

function readNamedFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `Missing function ${functionName}`);

    const bodyStart = source.indexOf('{', source.indexOf(')', start));
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) {
            return source.slice(start, index + 1);
        }
    }
    throw new Error(`Unterminated function ${functionName}`);
}

test('split-screen sends the complete composer context and retains failed turns', () => {
    const splitSource = read(SPLIT_PATH);
    const sendSource = readSendMessageSource();

    assert.match(sendSource, /window\.getCurrentChatAttachmentPayload = \(\) =>/);
    for (const payloadKey of [
        'image_ids',
        'video_ids',
        'audio_ids',
        'document_ids',
        'skill_ids',
        'note_ids',
        'prompt_ids',
        'reference_parts',
        'chat_reference_ids',
        'project_id',
    ]) {
        assert.match(splitSource, new RegExp(`${payloadKey}:`));
    }
    assert.match(splitSource, /composerContextHasContent\(composerContext\)/);
    assert.match(splitSource, /setSendTarget\(settledFailures\.length === 1 \? failure\.side : 'both'\)/);
    assert.match(splitSource, /if \(settledFailures\.length === 1\) \{\s*restoreSplitComposerAfterFailedSend\(/);
    assert.match(splitSource, /clearComposerContextAfterSuccessfulSend\(\)/);
});

test('closing one panel invalidates the hidden main chat before restoring its survivor', () => {
    const splitSource = read(SPLIT_PATH);
    const closePanelSource = readNamedFunction(splitSource, 'closePanel');
    const invalidationIndex = closePanelSource.indexOf('invalidateHiddenMainChatBinding()');
    const restoreIndex = closePanelSource.indexOf(
        'restorePersistedChatAsMainView(survivingChatId)'
    );

    assert.notEqual(invalidationIndex, -1, 'closePanel must clear the stale main-chat binding');
    assert.notEqual(restoreIndex, -1, 'closePanel must restore the surviving saved chat');
    assert.ok(
        invalidationIndex < restoreIndex,
        'the stale binding must be cleared before loadChatView can short-circuit'
    );
});

test('split-screen guards replacement and duplicates without suppressing transcript actions', () => {
    const splitSource = read(SPLIT_PATH);

    assert.match(splitSource, /async function confirmPanelReplacement/);
    assert.match(splitSource, /async function confirmExitSplitScreen/);
    assert.match(splitSource, /stopPanelGenerationForReplacement/);
    assert.match(splitSource, /split_screen_duplicate_chat_warning/);
    assert.match(splitSource, /readOnly: false/);
    assert.doesNotMatch(splitSource, /readOnly: 'split'/);
    assert.doesNotMatch(splitSource, /withSplitTranscriptReadOnly/);
    const streamSource = readStreamMessagesSource();
    assert.doesNotMatch(streamSource, /isSplitChatViewReadOnly|splitReadOnly/);
    assert.match(streamSource, /const shouldShowAssistantBranch = true/);
    assert.match(streamSource, /getChatBooleanSetting\('allow_rate_response', false\)/);
    assert.match(streamSource, /getChatBooleanSetting\('allow_delete_messages', false\)/);
    assert.match(streamSource, /const hasCitations = \(\(\) =>/);
    assert.match(streamSource, /safeGetLocalStorageItem\('show_assistant_message_metadata'\)/);
    assert.doesNotMatch(
        streamSource,
        /getChatBooleanSetting\('show_assistant_message_metadata'/,
    );
    assert.match(streamSource, /shouldShowAssistantCopy/);
    assert.match(splitSource, /leftLoadToken/);
    assert.match(splitSource, /rightLoadToken/);
});

test('split-screen supports accessible compact panels, resizing, and send targeting', () => {
    const splitSource = read(SPLIT_PATH);
    const modelSelectSource = read(MODEL_SELECT_PATH);
    const html = read(INDEX_PATH);
    const css = read(CSS_PATH);
    const modelSelectCss = read(MODEL_SELECT_CSS_PATH);

    assert.match(html, /id="splitCompactTabs"[^>]+role="tablist"/);
    assert.match(html, /id="splitCompactTabs"[^>]+aria-describedby="splitCompactTabsDescription"/);
    assert.match(html, /id="splitCompactTabsDescription"[^>]+hidden/);
    const compactTabsMarkup = html.match(/<div\b[^>]*id="splitCompactTabs"[\s\S]*?<\/div>/)?.[0];
    assert.ok(compactTabsMarkup, 'Missing compact split-screen tablist markup');
    assert.doesNotMatch(compactTabsMarkup, /split_screen_too_narrow_desc/);
    assert.equal((compactTabsMarkup.match(/role="tab"/g) || []).length, 2);
    assert.match(html, /id="splitCompactTabLeft"[^>]+aria-controls="splitScreenLeft"/);
    assert.match(html, /id="splitCompactTabRight"[^>]+aria-controls="splitScreenRight"/);
    assert.match(html, /id="splitScreenDivider"[^>]+role="separator"[^>]+tabindex="0"/);
    assert.match(html, /id="splitSendTargetWrapper"[^>]+hidden/);
    assert.match(html, /id="splitSendTargetDropdown"[^>]+class="select-dropdown upward"|class="select-dropdown upward"[^>]+id="splitSendTargetDropdown"/);
    assert.match(html, /class="select-dropdown-button"[^>]+role="menuitemradio"[^>]+data-split-send-target="both"/);
    assert.doesNotMatch(html, /split-send-target-(?:dropdown|option)/);
    assert.doesNotMatch(css, /split-send-target-(?:dropdown|option)/);
    assert.doesNotMatch(splitSource, /split-send-target-option/);
    assert.match(splitSource, /window\.createDropdownController\(\{/);
    assert.match(splitSource, /group: 'chat-box-composer-dropdowns'/);
    assert.match(splitSource, /function syncSendTargetControlVisibility\(\)/);
    const enableSource = readNamedFunction(splitSource, 'enable');
    const disableSource = readNamedFunction(splitSource, 'disable');
    const closePanelSource = readNamedFunction(splitSource, 'closePanel');
    const initSource = readNamedFunction(splitSource, 'init');
    [enableSource, disableSource, closePanelSource, initSource].forEach((source) => {
        assert.match(source, /syncSendTargetControlVisibility\(\)/);
    });
    ['modelSelectToggle', 'splitLeftModel', 'splitRightModel'].forEach((id) => {
        assert.match(html, new RegExp(`class="om-button model-select-trigger" id="${id}"`));
    });
    assert.doesNotMatch(html, /split-screen-panel-model/);
    assert.doesNotMatch(css, /split-screen-panel-model/);
    assert.doesNotMatch(splitSource, /split(?:Left|Right)Model(?:Icon|Name)/);
    assert.match(modelSelectCss, /\.model-select-trigger\s*\{/);
    assert.doesNotMatch(modelSelectCss, /\.model-select > #modelSelectToggle|#modelSelectToggle \.label-name/);
    assert.match(modelSelectSource, /function renderModelSelectTriggerContent\(/);
    assert.match(modelSelectSource, /function updateModelSelectLabel\([^)]*\)[\s\S]*renderModelSelectTriggerContent\(toggle, model\)/);
    assert.match(modelSelectSource, /window\.renderModelSelectTriggerContent = renderModelSelectTriggerContent/);
    const panelHeaderSource = readNamedFunction(splitSource, 'updatePanelHeader');
    assert.match(panelHeaderSource, /typeof window\.renderModelSelectTriggerContent === 'function'/);
    assert.match(panelHeaderSource, /window\.renderModelSelectTriggerContent\(triggerEl,/);
    assert.match(panelHeaderSource, /triggerEl\.textContent = resolvedModelName/);
    assert.match(splitSource, /event\.key === 'Home'/);
    assert.match(splitSource, /event\.key === 'End'/);
    assert.match(splitSource, /pointercancel/);
    assert.match(splitSource, /ResizeObserver/);
    assert.match(splitSource, /panel\.setAttribute\('role', 'tabpanel'\)/);
    assert.match(splitSource, /panel\.setAttribute\('aria-labelledby', tabId\)/);
    assert.match(splitSource, /getCompactDescription\(\)\?\.toggleAttribute\('hidden', !compact\)/);
    assert.match(css, /body\.split-screen-compact \.split-screen-panel:not\(\.compact-active\)/);
    assert.doesNotMatch(css, /@media\s*\(max-width:\s*700px\)[\s\S]*split-screen-panel:nth-of-type/);
});

test('split send-target control stays hidden outside split-screen mode', () => {
    const splitSource = read(SPLIT_PATH);
    const syncVisibilitySource = readNamedFunction(
        splitSource,
        'syncSendTargetControlVisibility',
    );
    const visibilityChanges = [];
    const dropdownOpenChanges = [];
    const wrapper = {};
    Object.defineProperty(wrapper, 'hidden', {
        set(value) {
            visibilityChanges.push(value);
        },
    });
    const context = {
        state: { active: false },
        el: (id) => (id === 'splitSendTargetWrapper' ? wrapper : null),
        setSendTargetDropdownOpen: (open) => dropdownOpenChanges.push(open),
    };

    vm.runInNewContext(`
        ${syncVisibilitySource}
        syncSendTargetControlVisibility();
        state.active = true;
        syncSendTargetControlVisibility();
    `, context);

    assert.deepEqual(visibilityChanges, [true, false]);
    assert.deepEqual(dropdownOpenChanges, [false]);
});

test('compact layout description follows the actual responsive mode', () => {
    const splitSource = read(SPLIT_PATH);
    const setCompactModeSource = readNamedFunction(splitSource, 'setCompactMode');
    const createElement = (initiallyHidden = false) => {
        const attributes = new Set(initiallyHidden ? ['hidden'] : []);
        return {
            attributes,
            toggleAttribute(name, force) {
                if (force) attributes.add(name);
                else attributes.delete(name);
            },
            setAttribute(name) {
                attributes.add(name);
            },
            removeAttribute(name) {
                attributes.delete(name);
            },
        };
    };
    const compactTabs = createElement(true);
    const compactDescription = createElement(true);
    const leftPanel = createElement();
    const rightPanel = createElement();
    const leftHeader = createElement();
    const rightHeader = createElement();
    const compactPanelChanges = [];
    const bodyClassChanges = [];
    const context = {
        document: {
            body: {
                classList: {
                    toggle(name, enabled) {
                        bodyClassChanges.push([name, enabled]);
                    },
                },
            },
        },
        state: { compactSide: 'left' },
        getCompactTabs: () => compactTabs,
        getCompactDescription: () => compactDescription,
        getLeftPanel: () => leftPanel,
        getRightPanel: () => rightPanel,
        getPanelHeaderSlot: (side) => (side === 'left' ? leftHeader : rightHeader),
        setCompactPanel: (side) => compactPanelChanges.push(side),
        scheduleSplitHeaderGutterSync: () => {},
    };

    vm.runInNewContext(`${setCompactModeSource}; setCompactMode(true);`, context);

    assert.equal(compactTabs.attributes.has('hidden'), false);
    assert.equal(compactDescription.attributes.has('hidden'), false);
    assert.equal(leftPanel.attributes.has('role'), true);
    assert.equal(rightPanel.attributes.has('role'), true);
    assert.deepEqual(compactPanelChanges, ['left']);

    vm.runInNewContext('setCompactMode(false);', context);

    assert.equal(compactTabs.attributes.has('hidden'), true);
    assert.equal(compactDescription.attributes.has('hidden'), true);
    assert.equal(leftPanel.attributes.has('role'), false);
    assert.equal(rightPanel.attributes.has('role'), false);
    assert.deepEqual(bodyClassChanges, [
        ['split-screen-compact', true],
        ['split-screen-compact', false],
    ]);
});

test('split-screen exposes panel-targeted chat actions without visible side/title headers', () => {
    const splitSource = read(SPLIT_PATH);
    const downloadSource = read(CHAT_DOWNLOAD_PATH);
    const html = read(INDEX_PATH);
    const css = read(CSS_PATH);

    assert.match(html, /id="splitLeftActionsButton"[^>]+aria-haspopup="menu"/);
    assert.match(html, /id="splitRightActionsButton"[^>]+aria-haspopup="menu"/);
    assert.match(html, /id="splitScreenMainHeader"[^>]+role="group"/);
    assert.match(html, /id="splitScreenHeaderLeft"[^>]+data-split-header-panel="left"/);
    assert.match(html, /id="splitScreenHeaderRight"[^>]+data-split-header-panel="right"/);
    assert.equal((html.match(/data-split-panel-action="share"/g) || []).length, 2);
    assert.equal((html.match(/data-split-panel-action="settings"/g) || []).length, 2);
    assert.doesNotMatch(html, /data-split-panel-action="terminal"/);
    assert.equal((html.match(/data-split-panel-action="temporary"/g) || []).length, 2);
    assert.equal((html.match(/data-split-download-format=/g) || []).length, 10);
    assert.doesNotMatch(html, /split-screen-panel-identity/);
    assert.doesNotMatch(html, /id="split(?:Left|Right)ChatTitle"/);
    assert.doesNotMatch(css, /\.split-screen-panel-(?:identity|side|title)\b/);

    assert.match(splitSource, /function mountPanelToolbarsInMainHeader\(\)/);
    assert.match(splitSource, /slot\.appendChild\(toolbar\)/);
    assert.match(splitSource, /setSplitHeaderRatio\(leftPercent\)/);
    assert.match(splitSource, /headerSlot\?\.classList\.toggle\('compact-active', active\)/);
    assert.match(css, /grid-template-columns: var\(--split-left-percent, 50%\)/);
    assert.match(css, /body\.split-screen-compact \.split-screen-main-header-slot\.compact-active/);
    assert.match(css, /\.split-screen-panel-header\s*\{[\s\S]*?justify-content:\s*flex-start/);
    assert.match(css, /\.split-screen-panel-header > \.main-header-div\s*\{[\s\S]*?margin-inline-start:\s*auto/);
    assert.match(css, /body\.split-screen-active #modelSelectToggle,[\s\S]*#headerCanvasButtonWrap,[\s\S]*#headerTempChatButton,[\s\S]*#headerDotsButtonDropdown[\s\S]*display: none !important/);
    assert.doesNotMatch(css, /body\.split-screen-active #modelSelect\s*[,\{]/);
    assert.match(splitSource, /mode: 'split',[\s\S]*side,[\s\S]*anchorEl: trigger/);
    assert.match(splitSource, /onSelect: async \(model\)[\s\S]*selectModelForPanel\(side, model\)/);
    assert.match(css, /body\.split-screen-active #headerSplitScreenButton/);
    assert.match(splitSource, /'headerCanvasButtonWrap',[\s\S]*'headerTempChatButton',[\s\S]*'headerDotsButtonDropdown'/);
    assert.match(splitSource, /ChatShareModal\?\.openForChat\?\.\(chatId\)/);
    assert.match(splitSource, /switchSettingsTab\(side\);[\s\S]*openModelSettingsSidebar/);
    assert.match(splitSource, /window\.downloadChat\(format, \{[\s\S]*chatId,[\s\S]*title:/);
    assert.match(splitSource, /function isPanelTemporary\(side\)/);
    assert.match(splitSource, /tempChatHistory = \(!chatId && isPanelTemporary\(side\)\)/);
    assert.match(downloadSource, /async function downloadChat\(format, options = \{\}\)/);
    assert.match(downloadSource, /options\.chatId/);
    assert.match(downloadSource, /window\.downloadChat = downloadChat/);
});

test('sidebar omits split dropdown actions while drag-and-drop uses explicit chat payloads', () => {
    const splitSource = read(SPLIT_PATH);
    const chatsSource = read(CHATS_HELPER_PATH);

    assert.doesNotMatch(chatsSource, /sidebar_chat_action_open_split_left/);
    assert.doesNotMatch(chatsSource, /sidebar_chat_action_open_split_right/);
    assert.doesNotMatch(chatsSource, /split-open-(?:left|right)-btn/);
    assert.match(chatsSource, /effectAllowed = 'copyMove'/);
    assert.match(splitSource, /application\/x-omlorix-chat-reference/);
    assert.match(splitSource, /function openSidebarChatInPanel/);
    assert.match(splitSource, /enable\(\{ restoreCurrent: false \}\)/);
});

test('every locale contains the complete split-screen vocabulary', () => {
    const english = JSON.parse(read(path.join(I18N_ROOT, 'en/index.json')));
    const requiredKeys = Object.keys(english).filter((key) => key.startsWith('split_screen_'));
    const locales = fs.readdirSync(I18N_ROOT).filter((locale) => (
        fs.existsSync(path.join(I18N_ROOT, locale, 'index.json'))
    ));

    for (const locale of locales) {
        const dictionary = JSON.parse(read(path.join(I18N_ROOT, locale, 'index.json')));
        for (const key of requiredKeys) {
            assert.equal(typeof dictionary[key], 'string', `${locale} is missing ${key}`);
            assert.ok(dictionary[key].trim(), `${locale} has an empty ${key}`);
            assert.deepEqual(
                extractInterpolationPlaceholders(dictionary[key]),
                extractInterpolationPlaceholders(english[key]),
                `${locale} does not preserve the placeholders for ${key}`
            );
        }
    }
});

test('split-screen preserves temporary entry state and reports terminal stream failures', () => {
    const splitSource = read(SPLIT_PATH);

    // Entering split mode from a temporary main chat must move a serialized
    // transcript with rollback protection instead of clearing it blindly.
    assert.match(splitSource, /function moveMainConversationIntoPanel\(side\)/);
    assert.match(splitSource, /const rollbackFragment = document\.createDocumentFragment\(\)/);
    assert.match(splitSource, /mainContainer\.appendChild\(rollbackFragment\)/);
    assert.match(splitSource, /!currentChatId && mainGenerationActive/);

    // Stream error events and read failures must reach sendToPanel's result so
    // the coordinating send path retains the failed turn.
    assert.match(splitSource, /onFailure: \(failure\) =>/);
    assert.match(splitSource, /emitFailure\(detail, \{ rateLimited: isRateLimited \}\)/);
    assert.match(splitSource, /if \(streamFailure\) \{[\s\S]*ok: false/);

    // Preflight restoration is deferred until chatBox has synchronously cleared
    // the dispatched input value.
    assert.match(splitSource, /function restoreSplitDraftAfterFailedSend\(message\)/);
    assert.match(splitSource, /queueMicrotask\(restore\)/);
});

test('split-screen safely tears down temporary survivor streams and resolves real sidebar titles', () => {
    const splitSource = read(SPLIT_PATH);

    assert.match(splitSource, /generatingTemporaryFallbackSides/);
    assert.match(splitSource, /generationSidesToStop = Array\.from\(new Set/);
    assert.match(splitSource, /unpersistedNormalGenerationSides/);
    assert.match(splitSource, /mustStopTemporarySurvivor/);
    assert.match(splitSource, /stopPanelGenerationForReplacement\(survivingSide\)/);
    assert.match(splitSource, /row\?\.dataset\?\.chatTitle/);
    assert.match(splitSource, /a\.sidebar-element-button > p/);
});

test('split-screen preserves the main temporary transcript for toolbar entry', () => {
    const splitSource = read(SPLIT_PATH);

    // The generic enable path is used by the header button. It must provide the
    // same no-id generation guard and transcript migration as sidebar entry.
    assert.match(
        splitSource,
        /function enable\(options = \{\}\)[\s\S]*hasUnattachableMainGeneration[\s\S]*split_screen_wait_for_main_generation/
    );
    assert.match(
        splitSource,
        /hasUnsavedMainConversation && !moveMainConversationIntoPanel\('left'\)/
    );
    assert.match(
        splitSource,
        /function getFallbackPanelForRestore\(\)[\s\S]*if \(leftHasTemp\) return \{ type: 'temp'/
    );
});

test('split-screen guards history teardown, transcript loading, and truncated streams', () => {
    const splitSource = read(SPLIT_PATH);

    assert.match(
        splitSource,
        /allowNextNonSplitHistoryNavigation[\s\S]*requestDisable\(\{ skipLoadFallback: true \}\)[\s\S]*history\.back\(\)/
    );
    assert.match(splitSource, /function isSideLoading\(side\)/);
    assert.match(
        splitSource,
        /const loadingSides = targetSides\.filter\(\(side\) => isSideLoading\(side\)\)/
    );
    assert.match(
        splitSource,
        /const generationToken = startPanelGeneration\(side, normalizedGenerationId\);[\s\S]*\/api\/v1\/chats\/attach/
    );
    assert.match(
        splitSource,
        /!didEmitDone[\s\S]*!didEmitFailure[\s\S]*!isPanelCancellationRequested\(side\)[\s\S]*emitFailure/
    );
});

test('split-screen invalidates the hidden main binding and guards app navigation', () => {
    const splitSource = read(SPLIT_PATH);
    const scriptSource = read(SCRIPT_PATH);
    const workspaceSource = read(WORKSPACE_PATH);

    assert.match(
        splitSource,
        /function disable\(options = \{\}\)[\s\S]*invalidateHiddenMainChatBinding\(\)[\s\S]*restorePersistedChatAsMainView/
    );
    assert.match(
        splitSource,
        /function invalidateHiddenMainChatBinding\(\)[\s\S]*removeAttribute\('data-chat-id'\)[\s\S]*removeAttribute\('data-project-id'\)/
    );
    assert.match(scriptSource, /async function requestSplitScreenExitForNavigation\(\)/);
    assert.match(
        scriptSource,
        /async function showChatStartContainer\(options = \{\}\)[\s\S]*await requestSplitScreenExitForNavigation\(\)/
    );
    assert.match(
        scriptSource,
        /async function hideChatContainer\(\)[\s\S]*await requestSplitScreenExitForNavigation\(\)/
    );
    assert.match(
        scriptSource,
        /async function navigateTo\([\s\S]*await requestSplitScreenExitForNavigation\(\)[\s\S]*history\.pushState/
    );
    assert.match(
        workspaceSource,
        /async function showWorkspaceContainer\(options = \{\}\)[\s\S]*!await hideChatContainer\(\)/
    );
});

test('split-screen derives persisted project scope and snapshots shared composer data', () => {
    const splitSource = read(SPLIT_PATH);
    const resolveProjectSource = readNamedFunction(splitSource, 'resolvePanelProjectIdForSend');
    const resolveProject = vm.runInNewContext(`(${resolveProjectSource})`);

    assert.equal(resolveProject('saved-chat', 'panel-project', 'fallback-project'), '');
    assert.equal(resolveProject('', 'panel-project', 'fallback-project'), 'panel-project');
    assert.equal(resolveProject(null, null, 'fallback-project'), 'fallback-project');
    assert.match(splitSource, /leftProjectId: null/);
    assert.match(splitSource, /rightProjectId: null/);
    assert.match(splitSource, /attachmentFiles: Array\.isArray\(attachmentFiles\)/);
    assert.match(splitSource, /pendingFiles: composerContext\.attachmentFiles/);
    assert.match(splitSource, /pendingChatReferences: composerContext\.chatReferencePayload/);
});

test('split-screen settles shared composer context after request acceptance', () => {
    const splitSource = read(SPLIT_PATH);

    assert.match(
        splitSource,
        /const generationRequestId = generateUUID\(\);[\s\S]*const generationToken = startPanelGeneration\(side, generationRequestId\);[\s\S]*await getPanelCustomSettingsForSend\(side\)/
    );
    assert.match(
        splitSource,
        /settleRequest\(true\);[\s\S]*await processStream\(res/
    );
    assert.match(
        splitSource,
        /settledFailures\.length === 0[\s\S]*targetSides\.every\([\s\S]*clearComposerContextAfterSuccessfulSend\(\)/
    );
    assert.match(splitSource, /splitComposerDispatchInProgress = true/);
});

test('split-screen exposes partial fan-out failures as soon as they settle', () => {
    const splitSource = read(SPLIT_PATH);

    assert.match(splitSource, /const handleSettledFailure = \(failure\) =>/);
    assert.match(
        splitSource,
        /setSendTarget\(settledFailures\.length === 1 \? failure\.side : 'both'\)/
    );
    assert.match(
        splitSource,
        /result = await sendToPanel\(normalizedMessage, side, composerContext, \{[\s\S]*handleSettledFailure/
    );
});

test('accepted split stream failures restore the full untouched composer context', () => {
    const splitSource = read(SPLIT_PATH);
    const fingerprintSource = readNamedFunction(splitSource, 'getComposerSnapshotFingerprint');
    const getFingerprint = vm.runInNewContext(`(${fingerprintSource})`);

    assert.match(splitSource, /composerStateSnapshot: typeof window\.captureChatComposerStateSnapshot/);
    assert.match(splitSource, /function captureSplitComposerRestoreGuard\(\)/);
    assert.match(splitSource, /function splitComposerRestoreGuardMatches\(guard\)/);
    assert.match(splitSource, /function isComposerEligibleForAcceptedFailureRestore\(composerContext\)/);
    assert.match(
        splitSource,
        /window\.applyChatComposerStateSnapshot\(\{[\s\S]*composerContext\.composerStateSnapshot[\s\S]*message,/
    );

    const original = {
        message: '',
        uploadedFiles: [{ file_id: 'file-1', name: 'Original name' }],
        skills: [{ id: 'skill-1', title: 'Original title' }],
        notes: [{ id: 'note-1' }],
        prompts: [],
        chatReferences: [{ chat_id: 'chat-1', title: 'Original chat' }],
        referenceParts: ['selected text'],
    };
    const metadataOnlyChange = {
        ...original,
        uploadedFiles: [{ file_id: 'file-1', name: 'Enriched name' }],
        skills: [{ id: 'skill-1', title: 'Enriched title' }],
        chatReferences: [{ chat_id: 'chat-1', title: 'Enriched chat' }],
    };
    assert.equal(getFingerprint(original), getFingerprint(metadataOnlyChange));
    assert.notEqual(
        getFingerprint(original),
        getFingerprint({ ...original, notes: [{ id: 'note-2' }] }),
        'a new message-scoped selection must make the restore guard fail'
    );
    assert.notEqual(
        getFingerprint(original),
        getFingerprint({ ...original, message: 'new draft' }),
        'newly typed text must make the restore guard fail'
    );
});

test('clearing a split panel removes its project scope', () => {
    const splitSource = read(SPLIT_PATH);
    const clearPanelSource = readNamedFunction(splitSource, 'clearPanelState');

    assert.match(clearPanelSource, /state\.leftProjectId = null/);
    assert.match(clearPanelSource, /state\.rightProjectId = null/);
});

test('project navigation waits for cancellable split-screen exits', () => {
    const projectsSource = read(PROJECTS_CHAT_PATH);
    const openSettingsSource = readNamedFunction(projectsSource, 'openCurrentProjectSettings');
    const createChatSource = readNamedFunction(projectsSource, 'handleProjectCreateChat');

    assert.match(projectsSource, /async function openCurrentProjectSettings\(\)/);
    assert.match(openSettingsSource, /await window\.showProjectsContainer\(\)/);
    assert.ok(
        openSettingsSource.indexOf('await window.showProjectsContainer()')
            < openSettingsSource.indexOf('window.showProjectsEditContainer(currentProject)'),
        'the projects view must finish opening before its editor is shown'
    );
    assert.match(projectsSource, /async function handleProjectCreateChat\(\)/);
    assert.match(createChatSource, /await window\.showChatStartContainer\(\)/);
    assert.ok(
        createChatSource.indexOf('await window.showChatStartContainer()')
            < createChatSource.indexOf("chatContainer.setAttribute('data-project-id', project.id)"),
        'the split-screen exit guard must run before the chat binding changes'
    );
});
