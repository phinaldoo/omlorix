// Split Screen Manager internals. Load before ../splitScreen.js in the documented order.

// ───── State ─────
const splitScreenInternalSPLIT_SCREEN_PATHNAME = '/chat';

function splitScreenInternalSplitScreenT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function splitScreenInternalSplitScreenTf(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(splitScreenInternalSplitScreenT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

const splitScreenInternalState = {
    active: false,
    leftChatId: null,
    rightChatId: null,
    leftProjectId: null,
    rightProjectId: null,
    leftModelId: null,
    rightModelId: null,
    leftModel: null,
    rightModel: null,
    leftModelName: null,
    rightModelName: null,
    leftModelIcon: null,
    rightModelIcon: null,
    leftChatTitle: null,
    rightChatTitle: null,
    leftTemporary: false,
    rightTemporary: false,
    compactSide: 'left',
    sendTarget: 'both', // 'both' | 'left' | 'right'
    resizeStartX: 0,
    resizeStartLeftWidth: 0,
    isResizing: false,
    leftGenerationId: null,
    rightGenerationId: null,
    leftGenerationToken: null,
    rightGenerationToken: null,
    leftAbortController: null,
    rightAbortController: null,
    leftStreamReader: null,
    rightStreamReader: null,
    leftStreamMessageId: null,
    rightStreamMessageId: null,
    leftPendingCancel: false,
    rightPendingCancel: false,
    leftCancelRequested: false,
    rightCancelRequested: false,
    nextGenerationToken: 1,
    leftIsGenerating: false,
    rightIsGenerating: false,
    leftIsLoading: false,
    rightIsLoading: false,
    leftSaveInProgress: false,
    rightSaveInProgress: false,
    leftSettings: {},
    rightSettings: {},
    leftSettingsSchema: null,
    rightSettingsSchema: null,
    leftThinkingState: null,
    rightThinkingState: null,
    leftLoadToken: 0,
    rightLoadToken: 0,
};
let splitScreenInternalPanelScrollListenersInitialized = false;
let splitScreenInternalSplitHistoryExitInProgress = false;
let splitScreenInternalAllowNextNonSplitHistoryNavigation = false;
let splitScreenInternalSplitComposerDispatchInProgress = false;
let splitScreenInternalSendTargetDropdownController = null;
const splitScreenInternalPanelVisibilityReconnectState = {
    left: null,
    right: null,
};

// ───── DOM References ─────
let splitScreenInternalMainChatAreaContainer = null;
function splitScreenInternalEl(id) { return document.getElementById(id); }

function splitScreenInternalGetWrapper()        { return splitScreenInternalEl('splitScreenWrapper'); }
function splitScreenInternalGetLeftPanel()      { return splitScreenInternalEl('splitScreenLeft'); }
function splitScreenInternalGetRightPanel()     { return splitScreenInternalEl('splitScreenRight'); }
function splitScreenInternalGetSplitMainHeader() { return splitScreenInternalEl('splitScreenMainHeader'); }
function splitScreenInternalGetPanelHeaderSlot(side) { return splitScreenInternalEl(side === 'left' ? 'splitScreenHeaderLeft' : 'splitScreenHeaderRight'); }
function splitScreenInternalGetDivider()        { return splitScreenInternalEl('splitScreenDivider'); }
function splitScreenInternalGetLeftContainer()  { return splitScreenInternalEl('splitChatAreaContainerLeft'); }
function splitScreenInternalGetRightContainer() { return splitScreenInternalEl('splitChatAreaContainerRight'); }
function splitScreenInternalGetLeftArea()       { return splitScreenInternalEl('splitChatAreaLeft'); }
function splitScreenInternalGetRightArea()      { return splitScreenInternalEl('splitChatAreaRight'); }
function splitScreenInternalGetLeftEmpty()      { return splitScreenInternalEl('splitLeftEmpty'); }
function splitScreenInternalGetRightEmpty()     { return splitScreenInternalEl('splitRightEmpty'); }
function splitScreenInternalGetPanelStatus(side) { return splitScreenInternalEl(side === 'left' ? 'splitLeftStatus' : 'splitRightStatus'); }
function splitScreenInternalGetPanelThinkingContainer(side) { return splitScreenInternalEl(side === 'left' ? 'splitLeftThinkingContainer' : 'splitRightThinkingContainer'); }
function splitScreenInternalGetPanelThinkingButton(side) { return splitScreenInternalEl(side === 'left' ? 'splitLeftThinkingButton' : 'splitRightThinkingButton'); }
function splitScreenInternalGetPanelThinkingLabel(side) { return splitScreenInternalEl(side === 'left' ? 'splitLeftThinkingLabel' : 'splitRightThinkingLabel'); }
function splitScreenInternalGetPanelThinkingIcon(side) { return splitScreenInternalEl(side === 'left' ? 'splitLeftThinkingIcon' : 'splitRightThinkingIcon'); }
function splitScreenInternalGetPanelThinkingDropdown(side) { return splitScreenInternalEl(side === 'left' ? 'splitLeftThinkingDropdown' : 'splitRightThinkingDropdown'); }
function splitScreenInternalGetPanelActionsButton(side) { return splitScreenInternalEl(side === 'left' ? 'splitLeftActionsButton' : 'splitRightActionsButton'); }
function splitScreenInternalGetPanelActionsMenu(side) { return splitScreenInternalEl(side === 'left' ? 'splitLeftActionsMenu' : 'splitRightActionsMenu'); }
function splitScreenInternalGetLeftSaveBtn()    { return splitScreenInternalEl('splitLeftSave'); }
function splitScreenInternalGetRightSaveBtn()   { return splitScreenInternalEl('splitRightSave'); }
function splitScreenInternalGetToggleBtn()      { return splitScreenInternalEl('headerSplitScreenButton'); }
function splitScreenInternalGetSendTargetBtn()  { return splitScreenInternalEl('splitSendTargetBtn'); }
function splitScreenInternalGetSendTargetDd()   { return splitScreenInternalEl('splitSendTargetDropdown'); }
function splitScreenInternalGetSendTargetIcon() { return splitScreenInternalEl('splitSendTargetIcon'); }
function splitScreenInternalGetSendTargetLabel(){ return splitScreenInternalEl('splitSendTargetLabel'); }
function splitScreenInternalGetCompactTabs()    { return splitScreenInternalEl('splitCompactTabs'); }
function splitScreenInternalGetCompactDescription() { return splitScreenInternalEl('splitCompactTabsDescription'); }
function splitScreenInternalGetMainModelSelect(){ return splitScreenInternalEl('modelSelect'); }

/**
 * The panel toolbars are declared beside their conversations so their
 * ownership stays obvious in the markup, then moved once into the sticky
 * main header. Moving the real nodes preserves every existing listener,
 * dropdown, id, and accessibility relationship without duplicate state.
 */
function splitScreenInternalMountPanelToolbarsInMainHeader() {
    ['left', 'right'].forEach((side) => {
        const panel = side === 'left' ? splitScreenInternalGetLeftPanel() : splitScreenInternalGetRightPanel();
        const slot = splitScreenInternalGetPanelHeaderSlot(side);
        if (!panel || !slot) return;
        const toolbar = Array.from(panel.children).find((child) => (
            child.classList?.contains('split-screen-panel-header')
        ));
        if (toolbar && toolbar.parentElement !== slot) {
            slot.appendChild(toolbar);
        }
    });
}

function splitScreenInternalSetSplitHeaderRatio(leftPercent) {
    const mainHeader = splitScreenInternalGetSplitMainHeader()?.closest('.main-container-header');
    if (!mainHeader) return;
    const numericPercent = Number(leftPercent);
    const normalizedPercent = Number.isFinite(numericPercent)
        ? Math.min(100, Math.max(0, numericPercent))
        : 50;
    mainHeader.style.setProperty('--split-left-percent', `${normalizedPercent}%`);
}

/**
 * Reserve only the physical space currently occupied by global header
 * controls. This remains correct when sidebars, feature buttons, locale
 * direction, or compact mode change which group sits at either edge.
 */
function splitScreenInternalSyncSplitHeaderGutters() {
    if (!splitScreenInternalState.active) return;
    const mainHeader = splitScreenInternalGetSplitMainHeader()?.closest('.main-container-header');
    if (!mainHeader) return;
    const headerRect = mainHeader.getBoundingClientRect();
    if (!headerRect.width) return;

    let leftGutter = 12;
    let rightGutter = 12;
    mainHeader.querySelectorAll('.main-header-leading, .main-header-actions').forEach((group) => {
        const rect = group.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        if ((rect.left + rect.right) / 2 < (headerRect.left + headerRect.right) / 2) {
            leftGutter = Math.max(leftGutter, rect.right - headerRect.left + 8);
        } else {
            rightGutter = Math.max(rightGutter, headerRect.right - rect.left + 8);
        }
    });
    mainHeader.style.setProperty('--split-header-left-gutter', `${Math.ceil(leftGutter)}px`);
    mainHeader.style.setProperty('--split-header-right-gutter', `${Math.ceil(rightGutter)}px`);
}

function splitScreenInternalScheduleSplitHeaderGutterSync() {
    if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(splitScreenInternalSyncSplitHeaderGutters);
    } else {
        splitScreenInternalSyncSplitHeaderGutters();
    }
}

function splitScreenInternalSetPanelLoadStatus(side, status, message = '') {
    const panel = side === 'left' ? splitScreenInternalGetLeftPanel() : splitScreenInternalGetRightPanel();
    const statusEl = splitScreenInternalGetPanelStatus(side);
    if (!panel || !statusEl) return;
    panel.classList.toggle('is-loading', status === 'loading');
    panel.classList.toggle('has-load-error', status === 'error');
    statusEl.hidden = status === 'idle';
    statusEl.textContent = message;
    statusEl.setAttribute('role', status === 'error' ? 'alert' : 'status');
    statusEl.setAttribute('aria-live', status === 'error' ? 'assertive' : 'polite');
}

const splitScreenInternalSPLIT_TARGET_ICON_KEYS = Object.freeze({
    both: 'splitPanelsBoth',
    left: 'splitPanelsLeft',
    right: 'splitPanelsRight',
});

function splitScreenInternalGetSplitTargetIconMarkup(target) {
    const iconKey = splitScreenInternalSPLIT_TARGET_ICON_KEYS[target];
    if (!iconKey || typeof Icons !== 'object') {
        return '';
    }
    return Icons?.[iconKey] || '';
}

function splitScreenInternalRenderSplitTargetOptionIcons() {
    document.querySelectorAll('[data-split-target-icon]').forEach((iconEl) => {
        const target = iconEl.getAttribute('data-split-target-icon');
        iconEl.innerHTML = splitScreenInternalGetSplitTargetIconMarkup(target);
    });
}

function splitScreenInternalRenderSplitSettingsTabIcons() {
    document.querySelectorAll('[data-split-settings-icon]').forEach((iconEl) => {
        const side = iconEl.getAttribute('data-split-settings-icon');
        iconEl.innerHTML = splitScreenInternalGetSplitTargetIconMarkup(side);
    });
}

function splitScreenInternalUpdateSendTargetButtonIcon() {
    const iconEl = splitScreenInternalGetSendTargetIcon();
    if (!iconEl) return;
    iconEl.innerHTML = splitScreenInternalGetSplitTargetIconMarkup(splitScreenInternalState.sendTarget);
}

function splitScreenInternalHydrateSharedSplitIcons() {
    splitScreenInternalRenderSplitTargetOptionIcons();
    splitScreenInternalRenderSplitSettingsTabIcons();
    splitScreenInternalUpdateSendTargetButtonIcon();
    const actionIcons = {
        more: 'ellipsisVertical',
        share: 'share',
        settings: 'settings',
        temporary: 'clock',
        download: 'download',
        check: 'check',
    };
    document.querySelectorAll('[data-split-panel-action-icon]').forEach((iconEl) => {
        const iconKey = actionIcons[iconEl.dataset.splitPanelActionIcon];
        iconEl.innerHTML = typeof Icons === 'object' && iconKey ? (Icons?.[iconKey] || '') : '';
    });
}

function splitScreenInternalGetMainSelectedModelSnapshot() {
    const selectedModel = typeof window.getSelectedModel === 'function'
        ? window.getSelectedModel()
        : null;
    if (selectedModel?.model_id) {
        return selectedModel;
    }

    const modelSelectEl = splitScreenInternalGetMainModelSelect();
    const currentModelId = String(modelSelectEl?.getAttribute('data-model-id') || '').trim();
    if (!currentModelId) {
        return null;
    }

    const modelToggleEl = document.getElementById('modelSelectToggle');
    const currentModelName = modelToggleEl?.querySelector('.label-name')?.textContent?.trim() || currentModelId;
    const labelIconEl = modelToggleEl?.querySelector('.label-icon');
    return {
        model_id: currentModelId,
        name: currentModelName,
        model_icon: labelIconEl ? labelIconEl.innerHTML : null,
    };
}

function splitScreenInternalPanelNeedsMainModel(side) {
    if (side === 'left') {
        return !splitScreenInternalState.leftModelId || !splitScreenInternalState.leftModelName;
    }
    return !splitScreenInternalState.rightModelId || !splitScreenInternalState.rightModelName;
}

function splitScreenInternalApplyMainSelectedModelToPanels({ force = false } = {}) {
    if (!splitScreenInternalState.active) {
        return false;
    }

    const model = splitScreenInternalGetMainSelectedModelSnapshot();
    if (!model?.model_id) {
        return false;
    }

    let applied = false;
    if (force || splitScreenInternalPanelNeedsMainModel('left')) {
        splitScreenInternalSelectModelForPanel('left', model);
        applied = true;
    }
    if (force || splitScreenInternalPanelNeedsMainModel('right')) {
        splitScreenInternalSelectModelForPanel('right', model);
        applied = true;
    }
    if (applied) {
        splitScreenInternalRefreshVisibleSettingsPanel();
    }
    return applied;
}

function splitScreenInternalHasSplitRouteParams(url = new URL(window.location.href)) {
    return url.searchParams.has('left')
        || url.searchParams.has('right')
        || url.searchParams.get('split') === '1';
}

function splitScreenInternalGetPanelChatId(side) {
    return side === 'left' ? splitScreenInternalState.leftChatId : splitScreenInternalState.rightChatId;
}

function splitScreenInternalGetPanelProjectId(side) {
    return side === 'left' ? splitScreenInternalState.leftProjectId : splitScreenInternalState.rightProjectId;
}

function splitScreenInternalSetPanelProjectId(side, projectId) {
    const normalizedProjectId = String(projectId || '').trim() || null;
    if (side === 'left') {
        splitScreenInternalState.leftProjectId = normalizedProjectId;
    } else if (side === 'right') {
        splitScreenInternalState.rightProjectId = normalizedProjectId;
    }
}

/**
 * Existing chats must let the backend derive project ownership from the
 * persisted chat. A client-supplied project is only valid while creating a
 * new chat, where it describes the panel's intended project destination.
 */
function splitScreenInternalResolvePanelProjectIdForSend(chatId, panelProjectId, fallbackProjectId = '') {
    if (String(chatId || '').trim()) {
        return '';
    }
    return String(panelProjectId || fallbackProjectId || '').trim();
}

function splitScreenInternalGetOtherSide(side) {
    return side === 'left' ? 'right' : 'left';
}

function splitScreenInternalGetTranslatedSideLabel(side) {
    return side === 'left'
        ? splitScreenInternalSplitScreenT('split_screen_side_left', 'Left')
        : splitScreenInternalSplitScreenT('split_screen_side_right', 'Right');
}

function splitScreenInternalGetPanelChatTitle(side) {
    return side === 'left' ? splitScreenInternalState.leftChatTitle : splitScreenInternalState.rightChatTitle;
}

function splitScreenInternalSetPanelChatTitle(side, title) {
    const normalizedTitle = String(title || '').trim();
    if (side === 'left') {
        splitScreenInternalState.leftChatTitle = normalizedTitle || null;
    } else {
        splitScreenInternalState.rightChatTitle = normalizedTitle || null;
    }
    splitScreenInternalUpdatePanelHeader(side);
}

function splitScreenInternalGetSidebarChatTitle(chatId) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId) return '';
    const row = Array.from(document.querySelectorAll('.sidebar-element[data-chat-id]'))
        .find((candidate) => String(candidate.dataset.chatId || '') === normalizedChatId);
    return String(
        row?.dataset?.chatTitle
        || row?.querySelector('a.sidebar-element-button > p, .chat-title, .sidebar-element-title, [data-chat-title]')?.textContent
        || row?.getAttribute('aria-label')
        || ''
    ).trim();
}

/**
 * Detect a rendered conversation in the main chat before split-screen takes
 * ownership of the visible transcript area.
 */
function splitScreenInternalMainChatHasConversation() {
    const mainContainer = document.getElementById('chatAreaContainer');
    return Boolean(
        mainContainer?.querySelector('.user-message-area')
        || mainContainer?.querySelector('.assistant-message-container')
    );
}

/**
 * Move an unsaved, non-streaming main conversation into a split panel.
 *
 * The original DOM is detached before rendering so the shared transcript
 * renderer does not see duplicate message IDs. The detached nodes are kept
 * as a rollback buffer until the split transcript has rendered successfully.
 */
function splitScreenInternalMoveMainConversationIntoPanel(side) {
    const mainContainer = document.getElementById('chatAreaContainer');
    const chatContainer = document.getElementById('chatContainer');
    const panelContainer = side === 'left' ? splitScreenInternalGetLeftContainer() : splitScreenInternalGetRightContainer();
    const panel = side === 'left' ? splitScreenInternalGetLeftPanel() : splitScreenInternalGetRightPanel();
    if (!mainContainer || !panelContainer || !panel || !splitScreenInternalMainChatHasConversation()) {
        return false;
    }
    if (typeof window.serializeTemporaryChatHistory !== 'function'
        || typeof window.renderChatTranscript !== 'function') {
        return false;
    }

    let messages;
    try {
        const serialized = window.serializeTemporaryChatHistory(mainContainer);
        messages = JSON.parse(serialized || '[]');
    } catch (_) {
        return false;
    }
    if (!Array.isArray(messages) || !messages.length) {
        return false;
    }

    const rollbackFragment = document.createDocumentFragment();
    while (mainContainer.firstChild) {
        rollbackFragment.appendChild(mainContainer.firstChild);
    }

    try {
        panelContainer.innerHTML = '';
        window.renderChatTranscript(messages, {
            container: panelContainer,
            clearContainer: false,
            trackAssistantVersions: false,
            readOnly: false,
        });
        const renderedConversation = Boolean(
            panelContainer.querySelector('.user-message-area')
            || panelContainer.querySelector('.assistant-message-container')
        );
        if (!renderedConversation) {
            throw new Error('Split transcript renderer produced no conversation content');
        }
    } catch (error) {
        console.error('Failed to preserve the main conversation in split screen:', error);
        panelContainer.innerHTML = '';
        mainContainer.appendChild(rollbackFragment);
        return false;
    }

    if (side === 'left') {
        splitScreenInternalState.leftChatId = null;
        splitScreenInternalState.leftProjectId = null;
        splitScreenInternalState.leftChatTitle = null;
        splitScreenInternalState.leftTemporary = splitScreenInternalIsTemporaryModeEnabled();
        splitScreenInternalState.leftLoadToken += 1;
    } else {
        splitScreenInternalState.rightChatId = null;
        splitScreenInternalState.rightProjectId = null;
        splitScreenInternalState.rightChatTitle = null;
        splitScreenInternalState.rightTemporary = splitScreenInternalIsTemporaryModeEnabled();
        splitScreenInternalState.rightLoadToken += 1;
    }
    splitScreenInternalSetPanelProjectId(side, chatContainer?.getAttribute('data-project-id'));
    panel.classList.add('has-chat');
    splitScreenInternalSetPanelLoadStatus(side, 'idle');
    splitScreenInternalUpdatePanelHeader(side);
    splitScreenInternalUpdatePanelSaveButtons();
    return true;
}

function splitScreenInternalGetComposerContextSnapshot() {
    const attachmentPayload = typeof window.getCurrentChatAttachmentPayload === 'function'
        ? window.getCurrentChatAttachmentPayload()
        : {};
    const attachmentFiles = typeof window.getCurrentChatAttachmentFiles === 'function'
        ? window.getCurrentChatAttachmentFiles()
        : (typeof gatherPendingAttachments === 'function' ? gatherPendingAttachments() : []);
    const chatContainer = document.getElementById('chatContainer');
    return {
        // Keep the complete UI snapshot as well as the request-oriented
        // ID lists below. The richer snapshot contains the metadata needed
        // to reconstruct attachment chips after an accepted stream fails.
        composerStateSnapshot: typeof window.captureChatComposerStateSnapshot === 'function'
            ? window.captureChatComposerStateSnapshot()
            : null,
        imageIds: Array.isArray(attachmentPayload.imageIds) ? attachmentPayload.imageIds : [],
        videoIds: Array.isArray(attachmentPayload.videoIds) ? attachmentPayload.videoIds : [],
        audioIds: Array.isArray(attachmentPayload.audioIds) ? attachmentPayload.audioIds : [],
        documentIds: Array.isArray(attachmentPayload.documentIds) ? attachmentPayload.documentIds : [],
        skillIds: typeof window.getSelectedSkillIds === 'function' ? window.getSelectedSkillIds() : [],
        noteIds: typeof window.getSelectedNoteIds === 'function' ? window.getSelectedNoteIds() : [],
        promptIds: typeof window.getSelectedPromptIds === 'function' ? window.getSelectedPromptIds() : [],
        referenceParts: typeof window.getSelectedReferenceParts === 'function' ? window.getSelectedReferenceParts() : [],
        chatReferenceIds: typeof window.getSelectedChatReferenceIds === 'function' ? window.getSelectedChatReferenceIds() : [],
        chatReferencePayload: typeof window.getSelectedChatReferencePayload === 'function'
            ? window.getSelectedChatReferencePayload()
            : [],
        attachmentFiles: Array.isArray(attachmentFiles) ? attachmentFiles : [],
        projectId: String(chatContainer?.getAttribute('data-project-id') || '').trim(),
    };
}

function splitScreenInternalComposerContextHasContent(context) {
    return [
        'imageIds',
        'videoIds',
        'audioIds',
        'documentIds',
        'skillIds',
        'noteIds',
        'promptIds',
        'referenceParts',
        'chatReferenceIds',
    ].some((key) => Array.isArray(context?.[key]) && context[key].length > 0);
}

function splitScreenInternalClearComposerContextAfterSuccessfulSend() {
    if (typeof clearChatRequestFiles === 'function') {
        clearChatRequestFiles({ preserveSkills: true });
    }
    if (typeof window.clearAllReferenceParts === 'function') {
        window.clearAllReferenceParts();
    }
}

/**
 * Reduce a rich composer snapshot to the ordered values that describe its
 * user-visible content. Metadata is deliberately excluded because it may
 * be enriched while a request is in flight without changing the draft.
 */
function splitScreenInternalGetComposerSnapshotFingerprint(snapshot) {
    const source = snapshot && typeof snapshot === 'object' ? snapshot : {};
    const normalizeId = (value) => String(value ?? '').trim();
    const collectIds = (items, resolver) => (
        Array.isArray(items)
            ? items.map(resolver).map(normalizeId).filter(Boolean)
            : []
    );
    const uploadedFileIds = Array.isArray(source.uploadedFileIds)
        ? source.uploadedFileIds.map(normalizeId).filter(Boolean)
        : collectIds(source.uploadedFiles, (item) => item?.file_id ?? item?.id);

    return JSON.stringify({
        message: String(source.message || ''),
        uploadedFileIds,
        skillIds: collectIds(source.skills, (item) => item?.id),
        noteIds: collectIds(source.notes, (item) => item?.id),
        promptIds: collectIds(source.prompts, (item) => item?.id),
        chatReferenceIds: collectIds(source.chatReferences, (item) => item?.chat_id ?? item?.id),
        referenceParts: Array.isArray(source.referenceParts)
            ? source.referenceParts.map((part) => String(part || '').trim()).filter(Boolean)
            : [],
    });
}

/**
 * Capture the cleared composer state. A later stream failure may restore
 * the dispatched turn only while this state is still unchanged, otherwise
 * it would overwrite a new draft the user started during generation.
 */
function splitScreenInternalCaptureSplitComposerRestoreGuard() {
    const snapshot = typeof window.captureChatComposerStateSnapshot === 'function'
        ? window.captureChatComposerStateSnapshot()
        : null;
    return {
        snapshot,
        fingerprint: splitScreenInternalGetComposerSnapshotFingerprint(snapshot),
    };
}

function splitScreenInternalSplitComposerRestoreGuardMatches(guard) {
    if (!guard) return false;
    const current = splitScreenInternalCaptureSplitComposerRestoreGuard();
    return current.fingerprint === guard.fingerprint;
}

/**
 * The normal dispatcher has already cleared the message by the time an
 * accepted HTTP response arrives. Only an otherwise unchanged composer is
 * eligible for automatic restoration; this protects text or attachments
 * the user added while the request was being accepted.
 */
function splitScreenInternalIsComposerEligibleForAcceptedFailureRestore(composerContext) {
    const dispatchedSnapshot = composerContext?.composerStateSnapshot;
    if (
        !dispatchedSnapshot
        || typeof window.captureChatComposerStateSnapshot !== 'function'
        || typeof window.applyChatComposerStateSnapshot !== 'function'
    ) {
        return null;
    }
    const expectedClearedMessageSnapshot = {
        ...dispatchedSnapshot,
        message: '',
    };
    const currentSnapshot = window.captureChatComposerStateSnapshot();
    return splitScreenInternalGetComposerSnapshotFingerprint(currentSnapshot)
            === splitScreenInternalGetComposerSnapshotFingerprint(expectedClearedMessageSnapshot);
}

function splitScreenInternalScheduleSplitComposerRestore(restore) {
    if (typeof queueMicrotask === 'function') {
        queueMicrotask(restore);
    } else {
        Promise.resolve().then(restore);
    }
}

/**
 * The chat-box dispatcher clears its input immediately after invoking the
 * split send promise. Defer restoration until that synchronous cleanup has
 * completed so preflight failures cannot lose the user's draft.
 */
function splitScreenInternalRestoreSplitDraftAfterFailedSend(message) {
    if (!message || typeof restoreChatDraftAfterFailedSend !== 'function') {
        return;
    }
    const restore = () => restoreChatDraftAfterFailedSend(message);
    splitScreenInternalScheduleSplitComposerRestore(restore);
}

/**
 * Restore every message-scoped composer selection after an accepted stream
 * fails. Preflight and HTTP failures never clear those selections, so they
 * continue to use the text-only fallback above.
 */
function splitScreenInternalRestoreSplitComposerAfterFailedSend(message, composerContext, clearedGuard, options = {}) {
    if (options.enabled === false) {
        return;
    }
    if (!clearedGuard) {
        splitScreenInternalRestoreSplitDraftAfterFailedSend(message);
        return;
    }
    // Older or partially loaded chat-box bundles may not expose the rich
    // snapshot APIs. Preserve their established text-only recovery path.
    if (clearedGuard.restoreEligible === null) {
        splitScreenInternalRestoreSplitDraftAfterFailedSend(message);
        return;
    }
    if (
        !clearedGuard.restoreEligible
        || !composerContext?.composerStateSnapshot
        || typeof window.applyChatComposerStateSnapshot !== 'function'
    ) {
        return;
    }

    splitScreenInternalScheduleSplitComposerRestore(() => {
        if (!splitScreenInternalSplitComposerRestoreGuardMatches(clearedGuard)) {
            return;
        }

        try {
            window.applyChatComposerStateSnapshot({
                ...composerContext.composerStateSnapshot,
                message,
            }, {
                focusInput: true,
                dispatchInputEvent: true,
                includeMessage: true,
                persistDraft: true,
            });
        } catch (error) {
            console.error('Failed to restore split-screen composer context:', error);
            splitScreenInternalRestoreSplitDraftAfterFailedSend(message);
        }
    });
}

function splitScreenInternalGetPanelGenerationToken(side) {
    return side === 'left' ? splitScreenInternalState.leftGenerationToken : splitScreenInternalState.rightGenerationToken;
}

function splitScreenInternalScrollSplitAreaToBottom(area, { behavior = 'auto' } = {}) {
    if (typeof HTMLElement === 'undefined' || !(area instanceof HTMLElement)) {
        return;
    }
    const container = area.querySelector('.chat-area-container');

    // The coordinator owns the temporary prompt-alignment spacer. Remove
    // that geometry before the shared follow manager measures the bottom.
    const coordinatorHandled = Boolean(
        window.ChatScrollCoordinator
        && container
        && window.ChatScrollCoordinator.scrollToBottom(area, container, { behavior })
    );

    // Re-arm automatic following after the spacer-free scroll. Calling the
    // manager second also synchronizes its smooth-scroll and intent state.
    if (window.ChatScrollManager && typeof window.ChatScrollManager.scrollToBottom === 'function') {
        window.ChatScrollManager.scrollToBottom(area, { behavior });
        return;
    }

    if (coordinatorHandled) return;
    if (behavior === 'smooth' && typeof area.scrollTo === 'function') {
        area.scrollTo({ top: area.scrollHeight, behavior: 'smooth' });
    } else {
        area.scrollTop = area.scrollHeight;
    }
}

function splitScreenInternalScrollSplitUserMessageToTop(messageId, container, area) {
    if (
        !messageId
        || typeof HTMLElement === 'undefined'
        || !(container instanceof HTMLElement)
        || !(area instanceof HTMLElement)
    ) {
        return false;
    }

    if (typeof window.scrollUserMessageToTop === 'function') {
        window.scrollUserMessageToTop(messageId, {
            container,
            viewport: area,
        });
        return true;
    }

    splitScreenInternalScrollSplitAreaToBottom(area);
    return false;
}

function splitScreenInternalIsPanelStillBoundToChat(side, chatId) {
    return splitScreenInternalState.active && String(splitScreenInternalGetPanelChatId(side) || '') === String(chatId || '');
}

function splitScreenInternalClearPanelVisibilityReconnectState(side) {
    if (side !== 'left' && side !== 'right') {
        return;
    }
    const reconnectState = splitScreenInternalPanelVisibilityReconnectState[side];
    if (!reconnectState) {
        return;
    }
    if (typeof document !== 'undefined' && reconnectState.visibilityHandler) {
        document.removeEventListener('visibilitychange', reconnectState.visibilityHandler);
    }
    if (typeof window !== 'undefined' && reconnectState.focusHandler) {
        window.removeEventListener('focus', reconnectState.focusHandler);
    }
    if (typeof window !== 'undefined' && reconnectState.pageShowHandler) {
        window.removeEventListener('pageshow', reconnectState.pageShowHandler);
    }
    splitScreenInternalPanelVisibilityReconnectState[side] = null;
}

function splitScreenInternalClearAllPanelVisibilityReconnectState() {
    splitScreenInternalClearPanelVisibilityReconnectState('left');
    splitScreenInternalClearPanelVisibilityReconnectState('right');
}

function splitScreenInternalDetachMainChatStreamForSplit(chatId) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId) {
        return null;
    }

    const chatContainer = document.getElementById('chatContainer');
    if (!chatContainer) {
        return null;
    }

    const activeChatId = String(chatContainer.getAttribute('data-chat-id') || '').trim();
    const activeGenerationId = String(chatContainer.getAttribute('data-active-generation') || '').trim();
    if (!activeGenerationId || activeChatId !== normalizedChatId) {
        return null;
    }

    chatContainer.removeAttribute('data-active-generation');
    if (typeof window.resetGenerationUIState === 'function') {
        window.resetGenerationUIState({ clearActiveAttr: false });
    } else if (typeof window.endGenerationUI === 'function') {
        window.endGenerationUI();
    }
    if (typeof clearVisibilityReconnectState === 'function') {
        try {
            clearVisibilityReconnectState();
        } catch (_) {}
    }
    try {
        if (String(window.currentGenerationId || '').trim() === activeGenerationId) {
            window.currentGenerationId = null;
        }
        window.pendingCancelGeneration = false;
    } catch (_) {}
    return activeGenerationId;
}

async function splitScreenInternalFetchPanelGenerationStatus(chatId) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId || typeof window.authedFetch !== 'function') {
        return null;
    }

    try {
        const params = new URLSearchParams({ chat_id: normalizedChatId });
        const response = await window.authedFetch(`/api/v1/chats/status?${params.toString()}`, {
            method: 'GET',
        });
        if (!response.ok) {
            return null;
        }
        return await response.json().catch(() => null);
    } catch (_) {
        return null;
    }
}

function splitScreenInternalClearPanelState(side) {
    const hadAnyGeneration = splitScreenInternalState.leftIsGenerating || splitScreenInternalState.rightIsGenerating;
    const container = side === 'left' ? splitScreenInternalGetLeftContainer() : splitScreenInternalGetRightContainer();
    const panel = side === 'left' ? splitScreenInternalGetLeftPanel() : splitScreenInternalGetRightPanel();
    splitScreenInternalClearPanelVisibilityReconnectState(side);
    if (container) {
        container.innerHTML = '';
    }
    if (panel) {
        panel.classList.remove('has-chat');
    }
    if (side === 'left') {
        splitScreenInternalState.leftChatId = null;
        splitScreenInternalState.leftProjectId = null;
        splitScreenInternalState.leftChatTitle = null;
        splitScreenInternalState.leftTemporary = splitScreenInternalGetDefaultPanelTemporaryMode();
        splitScreenInternalState.leftLoadToken += 1;
        splitScreenInternalState.leftGenerationId = null;
        splitScreenInternalState.leftGenerationToken = null;
        splitScreenInternalState.leftPendingCancel = false;
        splitScreenInternalState.leftCancelRequested = false;
        splitScreenInternalState.leftIsGenerating = false;
        splitScreenInternalState.leftIsLoading = false;
        splitScreenInternalState.leftSaveInProgress = false;
        splitScreenInternalState.leftSettings = {};
        splitScreenInternalState.leftSettingsSchema = null;
        splitScreenInternalState.leftThinkingState = null;
    } else {
        splitScreenInternalState.rightChatId = null;
        splitScreenInternalState.rightProjectId = null;
        splitScreenInternalState.rightChatTitle = null;
        splitScreenInternalState.rightTemporary = splitScreenInternalGetDefaultPanelTemporaryMode();
        splitScreenInternalState.rightLoadToken += 1;
        splitScreenInternalState.rightGenerationId = null;
        splitScreenInternalState.rightGenerationToken = null;
        splitScreenInternalState.rightPendingCancel = false;
        splitScreenInternalState.rightCancelRequested = false;
        splitScreenInternalState.rightIsGenerating = false;
        splitScreenInternalState.rightIsLoading = false;
        splitScreenInternalState.rightSaveInProgress = false;
        splitScreenInternalState.rightSettings = {};
        splitScreenInternalState.rightSettingsSchema = null;
        splitScreenInternalState.rightThinkingState = null;
    }
    splitScreenInternalRenderPanelThinkingControl(side);
    splitScreenInternalSetPanelLoadStatus(side, 'idle');
    splitScreenInternalUpdatePanelHeader(side);
    splitScreenInternalUpdatePanelSaveButtons();
    splitScreenInternalRefreshComposerControls();
    splitScreenInternalFlushInterruptedDraftIfReady();
    if (hadAnyGeneration && !splitScreenInternalState.leftIsGenerating && !splitScreenInternalState.rightIsGenerating && typeof window.endGenerationUI === 'function') {
        window.endGenerationUI();
    }
}

function splitScreenInternalEmitStateChanged() {
    if (typeof window === 'undefined' || typeof window.dispatchEvent !== 'function') {
        return;
    }
    window.dispatchEvent(new CustomEvent('splitScreen:stateChanged', {
        detail: {
            active: splitScreenInternalState.active,
            sendTarget: splitScreenInternalState.sendTarget,
        },
    }));
}

function splitScreenInternalIsTemporaryModeEnabled() {
    return Boolean(
        typeof window.isTemporaryChatModeActive === 'function'
        && window.isTemporaryChatModeActive()
    );
}

function splitScreenInternalIsTemporaryChatAllowedForSplit() {
    if (window?.chatSetup && Object.prototype.hasOwnProperty.call(window.chatSetup, 'temporary_chat_allowed')) {
        return Boolean(window.chatSetup.temporary_chat_allowed);
    }
    return true;
}

/**
 * A split panel owns its temporary-chat choice. Relying on the global
 * header toggle made the hidden main-chat control silently affect both
 * conversations and prevented users from choosing different behavior.
 */
function splitScreenInternalGetDefaultPanelTemporaryMode() {
    return splitScreenInternalIsTemporaryModeEnabled();
}

function splitScreenInternalIsPanelTemporary(side) {
    if (splitScreenInternalGetPanelChatId(side)) {
        return false;
    }
    return side === 'left' ? splitScreenInternalState.leftTemporary : splitScreenInternalState.rightTemporary;
}

function splitScreenInternalSetPanelTemporary(side, enabled) {
    if (!['left', 'right'].includes(side) || splitScreenInternalGetPanelChatId(side)) {
        return false;
    }
    if (!splitScreenInternalIsTemporaryChatAllowedForSplit()) {
        enabled = false;
    }
    if (splitScreenInternalHasUnsavedTemporaryPanelConversation(side) || splitScreenInternalIsSideGenerating(side)) {
        return false;
    }
    if (side === 'left') {
        splitScreenInternalState.leftTemporary = Boolean(enabled);
    } else {
        splitScreenInternalState.rightTemporary = Boolean(enabled);
    }
    splitScreenInternalUpdatePanelSaveButton(side);
    splitScreenInternalUpdatePanelActionsMenu(side);
    return true;
}

function splitScreenInternalSerializePanelTemporaryHistory(side) {
    const container = side === 'left' ? splitScreenInternalGetLeftContainer() : splitScreenInternalGetRightContainer();
    if (!container || typeof window.serializeTemporaryChatHistory !== 'function') {
        return '[]';
    }
    return window.serializeTemporaryChatHistory(container);
}

function splitScreenInternalHasUnsavedTemporaryPanelConversation(side) {
    const container = side === 'left' ? splitScreenInternalGetLeftContainer() : splitScreenInternalGetRightContainer();
    if (!container) return false;
    return Boolean(
        container.querySelector('.user-message-area')
        || container.querySelector('.assistant-message-container')
    );
}

function splitScreenInternalPanelHasReplaceableContent(side) {
    return Boolean(splitScreenInternalGetPanelChatId(side) || splitScreenInternalHasUnsavedTemporaryPanelConversation(side) || splitScreenInternalIsSideGenerating(side));
}

async function splitScreenInternalConfirmPanelReplacement(side, { action = 'replace' } = {}) {
    if (!splitScreenInternalPanelHasReplaceableContent(side)) {
        return true;
    }

    const hasUnsaved = !splitScreenInternalGetPanelChatId(side) && splitScreenInternalHasUnsavedTemporaryPanelConversation(side);
    const isGenerating = splitScreenInternalIsSideGenerating(side);
    if (!hasUnsaved && !isGenerating) {
        return true;
    }
    if (typeof window.showWarningConfirm !== 'function') {
        notifyWarning?.(splitScreenInternalSplitScreenT(
            'split_screen_confirmation_unavailable',
            'This panel cannot be replaced safely right now.'
        ));
        return false;
    }

    const sideLabel = splitScreenInternalGetTranslatedSideLabel(side);
    const title = action === 'close'
        ? splitScreenInternalSplitScreenTf('split_screen_confirm_close_title', 'Close the {side} panel?', { side: sideLabel })
        : splitScreenInternalSplitScreenTf('split_screen_confirm_replace_title', 'Replace the {side} panel?', { side: sideLabel });
    let message;
    if (hasUnsaved && isGenerating) {
        message = splitScreenInternalSplitScreenT(
            'split_screen_confirm_unsaved_and_generating',
            'Its response is still running and this temporary conversation has not been saved. Continuing will stop the response and discard the conversation.'
        );
    } else if (hasUnsaved) {
        message = splitScreenInternalSplitScreenT(
            'split_screen_confirm_unsaved',
            'This temporary conversation has not been saved. Continuing will discard it.'
        );
    } else {
        message = splitScreenInternalSplitScreenT(
            'split_screen_confirm_generating',
            'A response is still running in this panel. Continuing will stop it.'
        );
    }

    return Boolean(await window.showWarningConfirm({
        title,
        message,
        confirmLabel: action === 'close'
            ? splitScreenInternalSplitScreenT('split_screen_confirm_close', 'Close panel')
            : splitScreenInternalSplitScreenT('split_screen_confirm_replace', 'Replace panel'),
        cancelLabel: splitScreenInternalSplitScreenT('common_cancel', 'Cancel'),
        danger: hasUnsaved,
    }));
}

function splitScreenInternalUpdatePanelSaveButton(side) {
    const btn = side === 'left' ? splitScreenInternalGetLeftSaveBtn() : splitScreenInternalGetRightSaveBtn();
    if (!btn) return;
    const chatId = side === 'left' ? splitScreenInternalState.leftChatId : splitScreenInternalState.rightChatId;
    const isGenerating = side === 'left' ? splitScreenInternalState.leftIsGenerating : splitScreenInternalState.rightIsGenerating;
    const isSaving = side === 'left' ? splitScreenInternalState.leftSaveInProgress : splitScreenInternalState.rightSaveInProgress;
    const shouldShow = splitScreenInternalState.active && !chatId && splitScreenInternalIsPanelTemporary(side) && splitScreenInternalHasUnsavedTemporaryPanelConversation(side);
    // Keep the shared om-button display rules intact; hidden controls use
    // the native attribute instead of a split-screen-specific display style.
    btn.hidden = !shouldShow;
    const disabled = Boolean(isGenerating || isSaving);
    btn.disabled = disabled;
    btn.setAttribute(
        'title',
        disabled
            ? splitScreenInternalSplitScreenT('save_temp_chat_finish_generating_title', 'Finish generating before saving this chat')
            : splitScreenInternalSplitScreenT('save_temp_chat_title', 'Save chat')
    );
    splitScreenInternalUpdatePanelActionsMenu(side);
}

function splitScreenInternalUpdatePanelSaveButtons() {
    splitScreenInternalUpdatePanelSaveButton('left');
    splitScreenInternalUpdatePanelSaveButton('right');
}

function splitScreenInternalShouldShowPanelModelSettings() {
    const fallback = Boolean(window?.chatSetup?.show_model_settings);
    if (typeof window.getChatBooleanSetting === 'function') {
        return window.getChatBooleanSetting('show_model_settings', fallback);
    }
    try {
        const stored = localStorage.getItem('show_model_settings');
        if (stored === 'true' || stored === '1') return true;
        if (stored === 'false' || stored === '0') return false;
    } catch (_) {}
    return fallback;
}

function splitScreenInternalGetPanelActionMenuItems(side) {
    const menu = splitScreenInternalGetPanelActionsMenu(side);
    if (!menu) return [];
    return Array.from(menu.querySelectorAll('[role^="menuitem"]')).filter((item) => (
        !item.hidden && !item.disabled
    ));
}

function splitScreenInternalSetPanelActionsMenuOpen(side, open, { restoreFocus = false } = {}) {
    const button = splitScreenInternalGetPanelActionsButton(side);
    const menu = splitScreenInternalGetPanelActionsMenu(side);
    if (!button || !menu) return;
    const shouldOpen = Boolean(open);
    if (shouldOpen) {
        splitScreenInternalUpdatePanelActionsMenu(side);
        window.prepareDropdownOpeningAnimation?.(button, menu);
    }
    menu.classList.toggle('open', shouldOpen);
    menu.setAttribute('aria-hidden', shouldOpen ? 'false' : 'true');
    button.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
    if (shouldOpen) {
        splitScreenInternalGetPanelActionMenuItems(side)[0]?.focus();
    } else if (restoreFocus) {
        button.focus();
    }
}

function splitScreenInternalClosePanelActionMenus(exceptSide = null) {
    ['left', 'right'].forEach((side) => {
        if (side !== exceptSide) {
            splitScreenInternalSetPanelActionsMenuOpen(side, false);
        }
    });
}

/**
 * Keep each panel menu truthful to the selected conversation. Saved-chat
 * actions never fall back to the hidden main chat, while temporary mode is
 * only editable before the panel has started a conversation.
 */
function splitScreenInternalUpdatePanelActionsMenu(side) {
    const menu = splitScreenInternalGetPanelActionsMenu(side);
    if (!menu) return;
    const chatId = String(splitScreenInternalGetPanelChatId(side) || '').trim();
    const hasConversation = splitScreenInternalHasUnsavedTemporaryPanelConversation(side);
    const isGenerating = splitScreenInternalIsSideGenerating(side);

    const shareButton = menu.querySelector('[data-split-panel-action="share"]');
    if (shareButton) shareButton.hidden = !chatId;

    const settingsButton = menu.querySelector('[data-split-panel-action="settings"]');
    if (settingsButton) {
        settingsButton.hidden = !splitScreenInternalShouldShowPanelModelSettings() || !splitScreenInternalGetPanelModelId(side);
    }

    const temporaryButton = menu.querySelector('[data-split-panel-action="temporary"]');
    if (temporaryButton) {
        const temporaryAllowed = splitScreenInternalIsTemporaryChatAllowedForSplit();
        temporaryButton.hidden = Boolean(chatId) || !temporaryAllowed;
        temporaryButton.disabled = Boolean(hasConversation || isGenerating);
        temporaryButton.setAttribute('aria-checked', splitScreenInternalIsPanelTemporary(side) ? 'true' : 'false');
    }

    const downloadHeading = menu.querySelector('.split-panel-download-heading');
    if (downloadHeading) downloadHeading.hidden = !chatId;
    menu.querySelectorAll('[data-split-download-format]').forEach((button) => {
        button.hidden = !chatId;
    });
}

async function splitScreenInternalRunPanelAction(side, actionButton) {
    const action = actionButton?.dataset?.splitPanelAction;
    const chatId = String(splitScreenInternalGetPanelChatId(side) || '').trim();
    if (action === 'share' && chatId) {
        splitScreenInternalSetPanelActionsMenuOpen(side, false);
        window.ChatShareModal?.openForChat?.(chatId);
        return;
    }
    if (action === 'settings') {
        splitScreenInternalSetPanelActionsMenuOpen(side, false);
        splitScreenInternalSwitchSettingsTab(side);
        window.openModelSettingsSidebar?.();
        return;
    }
    if (action === 'temporary') {
        splitScreenInternalSetPanelTemporary(side, !splitScreenInternalIsPanelTemporary(side));
        splitScreenInternalUpdatePanelActionsMenu(side);
    }
}

async function splitScreenInternalRunPanelDownload(side, button) {
    const format = String(button?.dataset?.splitDownloadFormat || '').trim();
    const chatId = String(splitScreenInternalGetPanelChatId(side) || '').trim();
    if (!format || !chatId || typeof window.downloadChat !== 'function') return;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
        await window.downloadChat(format, {
            chatId,
            title: splitScreenInternalGetPanelChatTitle(side),
        });
    } finally {
        button.disabled = false;
        button.removeAttribute('aria-busy');
        splitScreenInternalSetPanelActionsMenuOpen(side, false, { restoreFocus: true });
    }
}

function splitScreenInternalSetupPanelActionMenus() {
    ['left', 'right'].forEach((side) => {
        const button = splitScreenInternalGetPanelActionsButton(side);
        const menu = splitScreenInternalGetPanelActionsMenu(side);
        if (!button || !menu) return;

        button.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            const shouldOpen = !menu.classList.contains('open');
            splitScreenInternalClosePanelActionMenus(side);
            splitScreenInternalClosePanelDropdowns();
            splitScreenInternalClosePanelThinkingDropdowns();
            splitScreenInternalSetSendTargetDropdownOpen(false);
            splitScreenInternalSetPanelActionsMenuOpen(side, shouldOpen);
        });

        menu.addEventListener('click', (event) => {
            event.stopPropagation();
            const actionButton = event.target.closest('[data-split-panel-action]');
            const downloadButton = event.target.closest('[data-split-download-format]');
            if (actionButton) {
                void splitScreenInternalRunPanelAction(side, actionButton);
            } else if (downloadButton) {
                void splitScreenInternalRunPanelDownload(side, downloadButton);
            }
        });

        menu.addEventListener('keydown', (event) => {
            const items = splitScreenInternalGetPanelActionMenuItems(side);
            const currentIndex = items.indexOf(document.activeElement);
            let nextIndex = null;
            if (event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + items.length) % items.length;
            if (event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % items.length;
            if (event.key === 'Home') nextIndex = 0;
            if (event.key === 'End') nextIndex = items.length - 1;
            if (event.key === 'Escape') {
                event.preventDefault();
                splitScreenInternalSetPanelActionsMenuOpen(side, false, { restoreFocus: true });
                return;
            }
            if (event.key === 'Tab') {
                splitScreenInternalSetPanelActionsMenuOpen(side, false);
                return;
            }
            if (nextIndex === null || !items.length) return;
            event.preventDefault();
            items[nextIndex].focus();
        });
    });

    document.addEventListener('click', () => splitScreenInternalClosePanelActionMenus());
}

function splitScreenInternalRefreshComposerControls() {
    if (typeof window.toggleInputButtons === 'function') {
        try {
            window.toggleInputButtons();
        } catch (_) {}
    }
}

function splitScreenInternalFlushInterruptedDraftIfReady() {
    if (typeof window.flushInterruptedDraftSend === 'function') {
        try {
            window.flushInterruptedDraftSend();
        } catch (_) {}
    }
}

function splitScreenInternalResolveTargetSides(target = splitScreenInternalState.sendTarget) {
    const normalizedTarget = typeof target === 'string' ? target : splitScreenInternalState.sendTarget;
    if (normalizedTarget === 'left') return ['left'];
    if (normalizedTarget === 'right') return ['right'];
    return ['left', 'right'];
}

function splitScreenInternalCloneSettings(settings) {
    if (!settings || typeof settings !== 'object') {
        return {};
    }
    try {
        return JSON.parse(JSON.stringify(settings));
    } catch (_) {
        return { ...settings };
    }
}

function splitScreenInternalGetPanelModelId(side) {
    return side === 'left' ? splitScreenInternalState.leftModelId : splitScreenInternalState.rightModelId;
}

/** Return the complete selected model so capability-gated panel actions stay independent. */
function splitScreenInternalGetPanelModel(side) {
    return side === 'left' ? splitScreenInternalState.leftModel : splitScreenInternalState.rightModel;
}

function splitScreenInternalGetPanelSettings(side) {
    return side === 'left' ? splitScreenInternalState.leftSettings : splitScreenInternalState.rightSettings;
}

function splitScreenInternalSetPanelSettings(side, settings) {
    if (side === 'left') {
        splitScreenInternalState.leftSettings = splitScreenInternalCloneSettings(settings);
    } else {
        splitScreenInternalState.rightSettings = splitScreenInternalCloneSettings(settings);
    }
}

function splitScreenInternalGetPanelSettingsSchema(side) {
    return side === 'left' ? splitScreenInternalState.leftSettingsSchema : splitScreenInternalState.rightSettingsSchema;
}

function splitScreenInternalSetPanelSettingsSchema(side, schema) {
    if (side === 'left') {
        splitScreenInternalState.leftSettingsSchema = schema || null;
    } else {
        splitScreenInternalState.rightSettingsSchema = schema || null;
    }
}

function splitScreenInternalGetPanelThinkingState(side) {
    return side === 'left' ? splitScreenInternalState.leftThinkingState : splitScreenInternalState.rightThinkingState;
}

function splitScreenInternalSetPanelThinkingState(side, thinkingState) {
    if (side === 'left') {
        splitScreenInternalState.leftThinkingState = thinkingState || null;
    } else {
        splitScreenInternalState.rightThinkingState = thinkingState || null;
    }
}

function splitScreenInternalGetActiveSettingsTabSide() {
    const tabs = document.getElementById('splitSettingsPanelTabs');
    const activeTab = tabs?.querySelector('.split-settings-tab.active');
    const side = activeTab?.dataset?.settingsPanel;
    return side === 'right' ? 'right' : 'left';
}

function splitScreenInternalIsPanelSettingsVisible(side) {
    if (!splitScreenInternalState.active || side !== splitScreenInternalGetActiveSettingsTabSide()) {
        return false;
    }
    const loadedModelId = typeof window.getLoadedModelSettingsModelId === 'function'
        ? window.getLoadedModelSettingsModelId()
        : null;
    return String(loadedModelId || '') === String(splitScreenInternalGetPanelModelId(side) || '');
}

function splitScreenInternalPersistVisibleSettingsPanel() {
    if (!splitScreenInternalState.active || typeof window.getCurrentModelSettingValues !== 'function') {
        return;
    }
    const side = splitScreenInternalGetActiveSettingsTabSide();
    if (!splitScreenInternalIsPanelSettingsVisible(side)) {
        return;
    }
    splitScreenInternalSetPanelSettings(side, window.getCurrentModelSettingValues());
    splitScreenInternalUpdatePanelThinkingState(side);
}

function splitScreenInternalBuildPanelThinkingState(side) {
    const schema = splitScreenInternalGetPanelSettingsSchema(side);
    if (!schema || typeof window.getQuickThinkingControlStateFromSchema !== 'function') {
        return null;
    }
    return window.getQuickThinkingControlStateFromSchema(
        schema,
        splitScreenInternalGetPanelModelId(side),
        splitScreenInternalGetPanelSettings(side)
    );
}

function splitScreenInternalUpdatePanelThinkingState(side) {
    const thinkingState = splitScreenInternalBuildPanelThinkingState(side);
    splitScreenInternalSetPanelThinkingState(side, thinkingState);
    splitScreenInternalRenderPanelThinkingControl(side);
    return thinkingState;
}

async function splitScreenInternalEnsurePanelSettingsSchema(side) {
    const modelId = splitScreenInternalGetPanelModelId(side);
    if (!modelId || typeof window.fetchModelSettingsSchemaForModel !== 'function') {
        splitScreenInternalSetPanelSettingsSchema(side, null);
        splitScreenInternalUpdatePanelThinkingState(side);
        return null;
    }
    const existingSchema = splitScreenInternalGetPanelSettingsSchema(side);
    if (existingSchema) {
        splitScreenInternalUpdatePanelThinkingState(side);
        return existingSchema;
    }
    try {
        const schemaPayload = await window.fetchModelSettingsSchemaForModel(modelId);
        if (String(splitScreenInternalGetPanelModelId(side) || '') !== String(modelId || '')) {
            return null;
        }
        const schema = schemaPayload?.supported && schemaPayload?.schema ? schemaPayload.schema : null;
        splitScreenInternalSetPanelSettingsSchema(side, schema);
        splitScreenInternalUpdatePanelThinkingState(side);
        return schema;
    } catch (error) {
        if (String(splitScreenInternalGetPanelModelId(side) || '') !== String(modelId || '')) {
            return null;
        }
        console.error(`[split-screen] failed to load ${side} model settings schema`, error);
        splitScreenInternalSetPanelSettingsSchema(side, null);
        splitScreenInternalUpdatePanelThinkingState(side);
        return null;
    }
}

function splitScreenInternalSetPanelThinkingDropdownOpen(side, open) {
    const dropdown = splitScreenInternalGetPanelThinkingDropdown(side);
    const button = splitScreenInternalGetPanelThinkingButton(side);
    if (!dropdown || !button) {
        return;
    }
    if (open) {
        window.prepareDropdownOpeningAnimation?.(button, dropdown);
    }
    dropdown.classList.toggle('open', Boolean(open));
    dropdown.setAttribute('aria-hidden', open ? 'false' : 'true');
    button.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function splitScreenInternalClosePanelThinkingDropdowns(exceptSide = null) {
    ['left', 'right'].forEach((side) => {
        if (side !== exceptSide) {
            splitScreenInternalSetPanelThinkingDropdownOpen(side, false);
        }
    });
}

function splitScreenInternalRenderPanelThinkingControl(side) {
    const container = splitScreenInternalGetPanelThinkingContainer(side);
    const button = splitScreenInternalGetPanelThinkingButton(side);
    const label = splitScreenInternalGetPanelThinkingLabel(side);
    const icon = splitScreenInternalGetPanelThinkingIcon(side);
    const dropdown = splitScreenInternalGetPanelThinkingDropdown(side);
    const thinkingState = splitScreenInternalGetPanelThinkingState(side);
    const supported = Boolean(thinkingState && Array.isArray(thinkingState.options) && thinkingState.options.length);

    if (icon) {
        icon.innerHTML = (typeof Icons === 'object' && Icons?.thinking) ? Icons.thinking : '';
    }
    if (!container || !button || !label || !dropdown) {
        return;
    }

    container.hidden = !supported;
    container.style.display = supported ? 'flex' : 'none';
    button.disabled = !supported;
    if (!supported) {
        label.textContent = splitScreenInternalSplitScreenT('chatbox_thinking_button_label', 'Thinking');
        dropdown.innerHTML = '';
        splitScreenInternalSetPanelThinkingDropdownOpen(side, false);
        return;
    }

    const currentLabel = thinkingState.currentLabel || thinkingState.label || splitScreenInternalSplitScreenT('chatbox_thinking_button_label', 'Thinking');
    label.textContent = currentLabel;
    const title = `${thinkingState.label || splitScreenInternalSplitScreenT('chatbox_thinking_button_label', 'Thinking')}: ${currentLabel}`.trim();
    button.setAttribute('title', title);
    button.setAttribute('aria-label', title);

    dropdown.innerHTML = '';
    thinkingState.options.forEach((option) => {
        const item = document.createElement('div');
        item.className = 'select-dropdown-item';

        const optionButton = document.createElement('button');
        optionButton.type = 'button';
        optionButton.className = 'select-dropdown-button';
        optionButton.setAttribute('role', 'menuitemradio');
        const isSelected = option.value === thinkingState.currentValue;
        optionButton.setAttribute('aria-checked', isSelected ? 'true' : 'false');

        const optionLabel = document.createElement('span');
        optionLabel.textContent = option.label;
        optionButton.appendChild(optionLabel);

        if (isSelected && typeof Icons === 'object' && Icons?.check) {
            optionButton.insertAdjacentHTML('beforeend', Icons.check);
        }

        optionButton.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            splitScreenInternalApplyPanelThinkingValue(side, option.value);
            splitScreenInternalSetPanelThinkingDropdownOpen(side, false);
            if (typeof window.focusChatInput === 'function') {
                window.focusChatInput();
            }
        });

        item.appendChild(optionButton);
        dropdown.appendChild(item);
    });
}

function splitScreenInternalApplyPanelThinkingValue(side, value) {
    const thinkingState = splitScreenInternalGetPanelThinkingState(side);
    if (!thinkingState || typeof window.applyQuickThinkingValueToSettings !== 'function') {
        return false;
    }
    const nextSettings = window.applyQuickThinkingValueToSettings(splitScreenInternalGetPanelSettings(side), thinkingState, value);
    splitScreenInternalSetPanelSettings(side, nextSettings);

    if (splitScreenInternalIsPanelSettingsVisible(side) && typeof window.applyModelSettingValues === 'function') {
        window.applyModelSettingValues(nextSettings);
    }
    splitScreenInternalUpdatePanelThinkingState(side);
    return true;
}

async function splitScreenInternalRefreshPanelSettingsSidebar(side) {
    const modelId = splitScreenInternalGetPanelModelId(side);
    if (!modelId) {
        return;
    }
    if (typeof window.loadModelSettingsFor === 'function') {
        await window.loadModelSettingsFor(modelId);
        if (
            !splitScreenInternalState.active
            || splitScreenInternalGetActiveSettingsTabSide() !== side
            || String(splitScreenInternalGetPanelModelId(side) || '') !== String(modelId || '')
        ) {
            return;
        }
        if (typeof window.applyModelSettingValues === 'function') {
            window.applyModelSettingValues(splitScreenInternalGetPanelSettings(side));
        }
        const schemaPayload = typeof window.fetchModelSettingsSchemaForModel === 'function'
            ? await window.fetchModelSettingsSchemaForModel(modelId).catch(() => null)
            : null;
        if (
            !splitScreenInternalState.active
            || splitScreenInternalGetActiveSettingsTabSide() !== side
            || String(splitScreenInternalGetPanelModelId(side) || '') !== String(modelId || '')
        ) {
            return;
        }
        if (schemaPayload?.supported && schemaPayload?.schema) {
            splitScreenInternalSetPanelSettingsSchema(side, schemaPayload.schema);
            splitScreenInternalUpdatePanelThinkingState(side);
        }
    } else if (typeof window.initializeModelSettings === 'function') {
        window.initializeModelSettings(modelId);
    }
}

async function splitScreenInternalGetPanelCustomSettingsForSend(side) {
    if (
        splitScreenInternalIsPanelSettingsVisible(side)
        && typeof window.validateCurrentModelSettings === 'function'
        && !window.validateCurrentModelSettings()
    ) {
        throw new Error(splitScreenInternalSplitScreenT(
            'model_settings_invalid_structured_value',
            'Correct the invalid model setting before sending.'
        ));
    }
    splitScreenInternalPersistVisibleSettingsPanel();
    await splitScreenInternalEnsurePanelSettingsSchema(side);
    const settings = splitScreenInternalCloneSettings(splitScreenInternalGetPanelSettings(side));
    const mentionedServerIds = typeof window.getSelectedMcpServerIds === 'function'
        ? window.getSelectedMcpServerIds()
        : [];
    if (!settings.settings || typeof settings.settings !== 'object') {
        settings.settings = {};
    }
    const panelSelection = Array.isArray(settings.settings.enabled_mcp_servers)
        ? settings.settings.enabled_mcp_servers
        : [];
    settings.settings.enabled_mcp_servers = Array.from(new Set([
        ...panelSelection,
        ...(Array.isArray(mentionedServerIds) ? mentionedServerIds : []),
    ].map((value) => String(value || '').trim()).filter(Boolean)));
    return settings;
}

function splitScreenInternalIsSideGenerating(side) {
    if (side === 'left') return splitScreenInternalState.leftIsGenerating;
    if (side === 'right') return splitScreenInternalState.rightIsGenerating;
    return false;
}

function splitScreenInternalIsSideLoading(side) {
    if (side === 'left') return splitScreenInternalState.leftIsLoading;
    if (side === 'right') return splitScreenInternalState.rightIsLoading;
    return false;
}

/**
 * Track transcript loading independently from response generation.
 *
 * A panel becomes addressable as soon as its chat id is assigned, but it is
 * not safe to append a new turn until the existing transcript and active
 * generation status have both been resolved.
 */
function splitScreenInternalSetSideLoading(side, loading) {
    if (side === 'left') {
        splitScreenInternalState.leftIsLoading = Boolean(loading);
    } else if (side === 'right') {
        splitScreenInternalState.rightIsLoading = Boolean(loading);
    } else {
        return;
    }
    splitScreenInternalRefreshComposerControls();
}

function splitScreenInternalFinishSideLoading(side, loadToken) {
    const currentLoadToken = side === 'left' ? splitScreenInternalState.leftLoadToken : splitScreenInternalState.rightLoadToken;
    if (currentLoadToken !== loadToken) {
        return false;
    }
    splitScreenInternalSetSideLoading(side, false);
    return true;
}

function splitScreenInternalIsPanelCancellationRequested(side) {
    return side === 'left' ? splitScreenInternalState.leftCancelRequested : splitScreenInternalState.rightCancelRequested;
}

function splitScreenInternalIsTargetGenerating(target = splitScreenInternalState.sendTarget) {
    return splitScreenInternalSplitComposerDispatchInProgress
        || splitScreenInternalResolveTargetSides(target).some((side) => splitScreenInternalIsSideGenerating(side));
}

async function splitScreenInternalRequestCancelForSide(side) {
    if (!splitScreenInternalIsSideGenerating(side)) {
        return false;
    }

    const generationId = side === 'left' ? splitScreenInternalState.leftGenerationId : splitScreenInternalState.rightGenerationId;
    if (!generationId) {
        if (side === 'left') {
            splitScreenInternalState.leftPendingCancel = true;
            splitScreenInternalState.leftCancelRequested = true;
        } else {
            splitScreenInternalState.rightPendingCancel = true;
            splitScreenInternalState.rightCancelRequested = true;
        }
        return true;
    }

    if (side === 'left') {
        splitScreenInternalState.leftPendingCancel = false;
        splitScreenInternalState.leftCancelRequested = true;
    } else {
        splitScreenInternalState.rightPendingCancel = false;
        splitScreenInternalState.rightCancelRequested = true;
    }

    const streamedMessageId = side === 'left'
        ? splitScreenInternalState.leftStreamMessageId
        : splitScreenInternalState.rightStreamMessageId;
    const transcriptRoot = side === 'left' ? splitScreenInternalGetLeftContainer() : splitScreenInternalGetRightContainer();
    if (streamedMessageId) {
        if (typeof clearMediaGenPlaceholderForNonFileEvent === 'function') {
            clearMediaGenPlaceholderForNonFileEvent(streamedMessageId);
        }
        window.finalizeCancelledAssistantStream?.(streamedMessageId, transcriptRoot);
    }

    // Stop rendering and release the browser connection immediately. The
    // control request below independently cancels the background provider.
    Promise.resolve(
        (side === 'left' ? splitScreenInternalState.leftStreamReader : splitScreenInternalState.rightStreamReader)?.cancel?.()
    ).catch(() => {});
    try {
        (side === 'left' ? splitScreenInternalState.leftAbortController : splitScreenInternalState.rightAbortController)?.abort?.();
    } catch (_) {}

    if (typeof window.authedFetch !== 'function') {
        return false;
    }

    try {
        if (typeof window.requestGenerationCancellation === 'function') {
            return await window.requestGenerationCancellation(generationId);
        }
        const params = new URLSearchParams({ generation_id: String(generationId) });
        const response = await window.authedFetch(`/api/v1/chats/cancel?${params.toString()}`, {
            method: 'POST', headers: { accept: 'application/json' }, body: '',
        });
        return Boolean(response?.ok);
    } catch (error) {
        console.error(`[split-send] failed to cancel ${side} generation`, error);
        return false;
    }
}

async function splitScreenInternalCancelGenerationForTarget(target = splitScreenInternalState.sendTarget) {
    if (!splitScreenInternalState.active) {
        return false;
    }
    const sidesToCancel = splitScreenInternalResolveTargetSides(target).filter((side) => splitScreenInternalIsSideGenerating(side));
    if (!sidesToCancel.length) {
        return false;
    }
    const results = await Promise.all(sidesToCancel.map((side) => splitScreenInternalRequestCancelForSide(side)));
    return results.some(Boolean);
}

async function splitScreenInternalStopPanelGenerationForReplacement(side) {
    if (!splitScreenInternalIsSideGenerating(side)) {
        return true;
    }
    const generationId = side === 'left' ? splitScreenInternalState.leftGenerationId : splitScreenInternalState.rightGenerationId;
    if (!generationId) {
        notifyWarning?.(splitScreenInternalSplitScreenTf(
            'split_screen_wait_for_generation_start',
            'The {side} response is still starting. Try again in a moment.',
            { side: splitScreenInternalGetTranslatedSideLabel(side) }
        ));
        return false;
    }
    const cancelAccepted = await splitScreenInternalRequestCancelForSide(side);
    if (!cancelAccepted) {
        notifyWarning?.(splitScreenInternalSplitScreenTf(
            'split_screen_cancel_before_replace_failed',
            'Could not stop the response in the {side} panel.',
            { side: splitScreenInternalGetTranslatedSideLabel(side) }
        ));
        return false;
    }
    splitScreenInternalFinishPanelGeneration(side);
    return true;
}

function splitScreenInternalStartPanelGeneration(side, generationId = null) {
    const token = splitScreenInternalState.nextGenerationToken++;
    const hadActiveGeneration = splitScreenInternalState.leftIsGenerating || splitScreenInternalState.rightIsGenerating;
    splitScreenInternalClearPanelVisibilityReconnectState(side);
    if (side === 'left') {
        splitScreenInternalState.leftGenerationToken = token;
        splitScreenInternalState.leftGenerationId = generationId ? String(generationId) : null;
        splitScreenInternalState.leftAbortController = new AbortController();
        splitScreenInternalState.leftStreamReader = null;
        splitScreenInternalState.leftStreamMessageId = null;
        splitScreenInternalState.leftPendingCancel = false;
        splitScreenInternalState.leftCancelRequested = false;
        splitScreenInternalState.leftIsGenerating = true;
    } else {
        splitScreenInternalState.rightGenerationToken = token;
        splitScreenInternalState.rightGenerationId = generationId ? String(generationId) : null;
        splitScreenInternalState.rightAbortController = new AbortController();
        splitScreenInternalState.rightStreamReader = null;
        splitScreenInternalState.rightStreamMessageId = null;
        splitScreenInternalState.rightPendingCancel = false;
        splitScreenInternalState.rightCancelRequested = false;
        splitScreenInternalState.rightIsGenerating = true;
    }
    splitScreenInternalUpdatePanelSaveButtons();
    splitScreenInternalRefreshComposerControls();
    if (!hadActiveGeneration && typeof window.startGenerationUI === 'function') {
        window.startGenerationUI();
    }
    return token;
}

function splitScreenInternalFinishPanelGeneration(side, token = null, status = 'interrupted') {
    if (side !== 'left' && side !== 'right') {
        return false;
    }

    splitScreenInternalClearPanelVisibilityReconnectState(side);
    let generationId = '';
    if (side === 'left') {
        if (token !== null && splitScreenInternalState.leftGenerationToken !== token) {
            return false;
        }
        generationId = String(splitScreenInternalState.leftGenerationId || '');
        splitScreenInternalState.leftGenerationToken = null;
        splitScreenInternalState.leftGenerationId = null;
        splitScreenInternalState.leftAbortController = null;
        splitScreenInternalState.leftStreamReader = null;
        splitScreenInternalState.leftStreamMessageId = null;
        splitScreenInternalState.leftPendingCancel = false;
        splitScreenInternalState.leftCancelRequested = false;
        splitScreenInternalState.leftIsGenerating = false;
    } else {
        if (token !== null && splitScreenInternalState.rightGenerationToken !== token) {
            return false;
        }
        generationId = String(splitScreenInternalState.rightGenerationId || '');
        splitScreenInternalState.rightGenerationToken = null;
        splitScreenInternalState.rightGenerationId = null;
        splitScreenInternalState.rightAbortController = null;
        splitScreenInternalState.rightStreamReader = null;
        splitScreenInternalState.rightStreamMessageId = null;
        splitScreenInternalState.rightPendingCancel = false;
        splitScreenInternalState.rightCancelRequested = false;
        splitScreenInternalState.rightIsGenerating = false;
    }

    splitScreenInternalUpdatePanelSaveButtons();
    splitScreenInternalRefreshComposerControls();
    splitScreenInternalFlushInterruptedDraftIfReady();
    if (!splitScreenInternalState.leftIsGenerating && !splitScreenInternalState.rightIsGenerating && typeof window.endGenerationUI === 'function') {
        window.endGenerationUI();
    }
    if (generationId) {
        window.messageQueue?.handleGenerationTerminal?.({
            generationId,
            surface: 'split',
            side,
            status,
        });
    }
    return true;
}

async function splitScreenInternalSavePanelTemporaryChat(side, { silentOnEmpty = false, suppressToast = false } = {}) {
    const existingChatId = side === 'left' ? splitScreenInternalState.leftChatId : splitScreenInternalState.rightChatId;
    if (existingChatId) {
        return existingChatId;
    }
    if (!splitScreenInternalIsPanelTemporary(side)) {
        return null;
    }

    const isSideGenerating = side === 'left' ? splitScreenInternalState.leftIsGenerating : splitScreenInternalState.rightIsGenerating;
    if (isSideGenerating) {
        notifyWarning?.(splitScreenInternalSplitScreenT('save_temp_chat_finish_generating_warning', 'Finish generating before saving this chat.'));
        return null;
    }

    const isSaving = side === 'left' ? splitScreenInternalState.leftSaveInProgress : splitScreenInternalState.rightSaveInProgress;
    if (isSaving) {
        return null;
    }

    if (!splitScreenInternalHasUnsavedTemporaryPanelConversation(side)) {
        if (!silentOnEmpty) {
            notifyWarning?.(splitScreenInternalSplitScreenT('save_temp_chat_empty_warning', 'There is no temporary chat to save.'));
        }
        return null;
    }

    const modelId = side === 'left' ? splitScreenInternalState.leftModelId : splitScreenInternalState.rightModelId;
    const projectId = splitScreenInternalGetPanelProjectId(side);
    const tempChat = splitScreenInternalSerializePanelTemporaryHistory(side);

    if (side === 'left') {
        splitScreenInternalState.leftSaveInProgress = true;
    } else {
        splitScreenInternalState.rightSaveInProgress = true;
    }
    splitScreenInternalUpdatePanelSaveButtons();

    try {
        const response = await window.authedFetch('/api/v1/chats/save-temp', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                temp_chat: tempChat,
                model_id: modelId || null,
                project_id: projectId || null,
            }),
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData?.detail || splitScreenInternalSplitScreenTf('save_temp_chat_failed_status', 'Failed to save chat ({status})', { status: response.status }));
        }

        const payload = await response.json().catch(() => ({}));
        const savedChatId = String(payload?.chat_id || '').trim();
        if (!savedChatId) {
            throw new Error(splitScreenInternalSplitScreenT('save_temp_chat_missing_id', 'Saved chat response did not include a chat id.'));
        }

        if (side === 'left') {
            splitScreenInternalState.leftChatId = savedChatId;
            splitScreenInternalState.leftTemporary = false;
        } else {
            splitScreenInternalState.rightChatId = savedChatId;
            splitScreenInternalState.rightTemporary = false;
        }
        splitScreenInternalUpdateURL();

        if (typeof window.initChatList === 'function') {
            await window.initChatList();
        }
        await splitScreenInternalLoadChatIntoPanel(savedChatId, side, { projectId });

        if (!suppressToast) {
            notifySuccess?.(splitScreenInternalSplitScreenT('save_temp_chat_success', 'Chat saved'));
        }
        return savedChatId;
    } catch (error) {
        console.error(`Failed to save ${side} temporary chat:`, error);
        if (!suppressToast) {
            notifyError?.(error.message || splitScreenInternalSplitScreenT('save_temp_chat_failed', 'Failed to save chat'));
        }
        return null;
    } finally {
        if (side === 'left') {
            splitScreenInternalState.leftSaveInProgress = false;
        } else {
            splitScreenInternalState.rightSaveInProgress = false;
        }
        splitScreenInternalUpdatePanelSaveButtons();
    }
}

function splitScreenInternalRestorePanelAsTemporaryMainView(side) {
    const panelContainer = side === 'left' ? splitScreenInternalGetLeftContainer() : splitScreenInternalGetRightContainer();
    const mainContainer = document.getElementById('chatAreaContainer');
    const chatContainer = document.getElementById('chatContainer');
    if (!panelContainer || !mainContainer) {
        return false;
    }

    const hasTranscript = Boolean(
        panelContainer.querySelector('.user-message-area')
        || panelContainer.querySelector('.assistant-message-container')
    );
    if (!hasTranscript) {
        return false;
    }

    if (typeof window.prepareTemporaryChatConversationView === 'function') {
        window.prepareTemporaryChatConversationView();
    } else if (typeof window.showChatStartContainer === 'function') {
        window.showChatStartContainer({ forceTemporary: true });
    }

    const serialized = splitScreenInternalSerializePanelTemporaryHistory(side);
    let tempMessages = [];
    try {
        const parsed = JSON.parse(serialized || '[]');
        if (Array.isArray(parsed)) {
            tempMessages = parsed;
        }
    } catch (_) {
        tempMessages = [];
    }

    mainContainer.innerHTML = '';
    if (typeof window.renderChatTranscript === 'function') {
        window.renderChatTranscript(tempMessages, {
            container: mainContainer,
            clearContainer: false,
            trackAssistantVersions: true,
            readOnly: false,
        });
    }
    if (chatContainer) {
        chatContainer.removeAttribute('data-chat-id');
        chatContainer.removeAttribute('data-project-id');
    }
    if (typeof window.syncTemporaryChatModeWithPreference === 'function') {
        window.syncTemporaryChatModeWithPreference();
    }
    if (typeof window.setTemporaryChatMode === 'function') {
        window.setTemporaryChatMode(true, { persistPreference: false });
    }
    if (typeof window.focusChatInput === 'function') {
        window.focusChatInput();
    }
    return true;
}

/**
 * Split mode empties the hidden main transcript while leaving its chat id
 * attached to the outer container. Clear that stale binding before any
 * full-screen restore or navigation so loadChatView cannot mistake an empty
 * transcript for an already-loaded chat and return early.
 */
function splitScreenInternalInvalidateHiddenMainChatBinding() {
    const mainContainer = document.getElementById('chatAreaContainer');
    const chatContainer = document.getElementById('chatContainer');
    if (!chatContainer || mainContainer?.querySelector(
        '.user-message-area, .assistant-message-container'
    )) {
        return false;
    }
    chatContainer.removeAttribute('data-chat-id');
    chatContainer.removeAttribute('data-project-id');
    return true;
}

/**
 * Restore a saved panel chat without adding an intermediate `/chat` entry
 * to browser history. Split mode owns the route transition, while
 * loadChatView continues to own transcript loading and stream reattachment.
 */
function splitScreenInternalRestorePersistedChatAsMainView(chatId) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId || typeof loadChatView !== 'function') return false;
    const url = new URL(window.location.href);
    url.pathname = `/chat/${encodeURIComponent(normalizedChatId)}`;
    url.searchParams.delete('split');
    url.searchParams.delete('left');
    url.searchParams.delete('right');
    history.replaceState({ chatId: normalizedChatId }, '', url.toString());
    void Promise.resolve(loadChatView(normalizedChatId, false, { preserveHistory: true }))
        .then((loaded) => {
            if (loaded && typeof window.restoreProjectSidebarForChat === 'function') {
                return window.restoreProjectSidebarForChat(normalizedChatId);
            }
            return null;
        })
        .catch((error) => {
            console.error('Failed to restore split-screen chat as the main view', error);
        });
    return true;
}

function splitScreenInternalGetFallbackPanelForRestore() {
    if (splitScreenInternalState.leftChatId) return { type: 'chat', chatId: splitScreenInternalState.leftChatId, side: 'left' };
    if (splitScreenInternalState.rightChatId) return { type: 'chat', chatId: splitScreenInternalState.rightChatId, side: 'right' };
    // A no-id transcript is recoverable as a temporary main conversation
    // even if the global preference changed after the panel was created.
    // Never make preservation depend on a preference flag when real
    // conversation DOM is present.
    const leftHasTemp = Boolean(
        splitScreenInternalGetLeftContainer()?.querySelector('.user-message-area')
        || splitScreenInternalGetLeftContainer()?.querySelector('.assistant-message-container')
    );
    const rightHasTemp = Boolean(
        splitScreenInternalGetRightContainer()?.querySelector('.user-message-area')
        || splitScreenInternalGetRightContainer()?.querySelector('.assistant-message-container')
    );
    if (leftHasTemp) return { type: 'temp', side: 'left' };
    if (rightHasTemp) return { type: 'temp', side: 'right' };
    return null;
}

async function splitScreenInternalConfirmExitSplitScreen() {
    const fallback = splitScreenInternalGetFallbackPanelForRestore();
    const unpersistedNormalGenerationSides = ['left', 'right'].filter((side) => (
        !splitScreenInternalGetPanelChatId(side) && !splitScreenInternalIsPanelTemporary(side) && splitScreenInternalIsSideGenerating(side)
    ));
    if (unpersistedNormalGenerationSides.length) {
        notifyWarning?.(splitScreenInternalSplitScreenTf(
            'split_screen_wait_for_generation_start',
            'The {side} response is still starting. Try again in a moment.',
            { side: splitScreenInternalGetTranslatedSideLabel(unpersistedNormalGenerationSides[0]) }
        ));
        return false;
    }
    const discardedSides = ['left', 'right'].filter((side) => side !== fallback?.side);
    const hasUnsavedDiscard = discardedSides.some((side) => (
        !splitScreenInternalGetPanelChatId(side) && splitScreenInternalHasUnsavedTemporaryPanelConversation(side)
    ));
    const generatingDiscardSides = discardedSides.filter((side) => splitScreenInternalIsSideGenerating(side));
    // Persisted fallback chats can reattach their generation in loadChatView.
    // Temporary fallback chats have no chat id to reattach through, so stop
    // them explicitly before copying their completed partial transcript.
    const generatingTemporaryFallbackSides = (
        fallback?.type === 'temp' && splitScreenInternalIsSideGenerating(fallback.side)
    ) ? [fallback.side] : [];
    const generationSidesToStop = Array.from(new Set([
        ...generatingDiscardSides,
        ...generatingTemporaryFallbackSides,
    ]));
    if (!hasUnsavedDiscard && !generationSidesToStop.length) {
        return true;
    }
    if (typeof window.showWarningConfirm !== 'function') return false;

    const confirmationMessages = [];
    if (hasUnsavedDiscard) {
        confirmationMessages.push(splitScreenInternalSplitScreenT(
            'split_screen_exit_confirm_unsaved',
            'Only one conversation can remain in the main view. The other temporary conversation will be discarded.'
        ));
    }
    if (generationSidesToStop.length) {
        confirmationMessages.push(splitScreenInternalSplitScreenT(
            'split_screen_exit_all_generating',
            'Running split-screen responses will be stopped.'
        ));
    }
    const confirmed = await window.showWarningConfirm({
        title: splitScreenInternalSplitScreenT('split_screen_exit_confirm_title', 'Leave split screen?'),
        message: confirmationMessages.join(' '),
        confirmLabel: splitScreenInternalSplitScreenT('split_screen_exit_confirm_button', 'Leave split screen'),
        cancelLabel: splitScreenInternalSplitScreenT('common_cancel', 'Cancel'),
        danger: hasUnsavedDiscard,
    });
    if (!confirmed) return false;
    for (const side of generationSidesToStop) {
        if (!await splitScreenInternalStopPanelGenerationForReplacement(side)) return false;
    }
    return true;
}

async function splitScreenInternalRequestDisable(options = {}) {
    if (!splitScreenInternalState.active) return true;
    if (options.skipLoadFallback !== true) {
        if (!await splitScreenInternalConfirmExitSplitScreen()) return false;
        splitScreenInternalDisable(options);
        return true;
    }

    const affectedSides = ['left', 'right'].filter((side) => (
        splitScreenInternalIsSideGenerating(side)
        || (!splitScreenInternalGetPanelChatId(side) && splitScreenInternalHasUnsavedTemporaryPanelConversation(side))
    ));
    if (affectedSides.length && typeof window.showWarningConfirm === 'function') {
        const hasUnsaved = affectedSides.some((side) => (
            !splitScreenInternalGetPanelChatId(side) && splitScreenInternalHasUnsavedTemporaryPanelConversation(side)
        ));
        const confirmed = await window.showWarningConfirm({
            title: splitScreenInternalSplitScreenT('split_screen_exit_confirm_title', 'Leave split screen?'),
            message: hasUnsaved
                ? splitScreenInternalSplitScreenT('split_screen_exit_all_unsaved', 'Unsaved split-screen conversations will be discarded.')
                : splitScreenInternalSplitScreenT('split_screen_exit_all_generating', 'Running split-screen responses will be stopped.'),
            confirmLabel: splitScreenInternalSplitScreenT('split_screen_exit_confirm_button', 'Leave split screen'),
            cancelLabel: splitScreenInternalSplitScreenT('common_cancel', 'Cancel'),
            danger: hasUnsaved,
        });
        if (!confirmed) return false;
    } else if (affectedSides.length) {
        return false;
    }
    for (const side of affectedSides.filter((candidate) => splitScreenInternalIsSideGenerating(candidate))) {
        if (!await splitScreenInternalStopPanelGenerationForReplacement(side)) return false;
    }
    splitScreenInternalDisable(options);
    return true;
}
