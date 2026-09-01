// Elements
const workspaceContainer = document.getElementById('workspaceContainer');
const automationsContainer = document.getElementById('automationsContainer');
const projectsContainer = document.getElementById('projectsContainer');
const chatsSearchContainer = document.getElementById('chatsSearchContainer');
const chatContainer = document.getElementById('chatContainer');
const chatContainerWelcome = document.getElementById('chatContainerWelcome');


const modelSelect = document.getElementById('modelSelect');

const headerShareButton = document.getElementById('headerShareButton');
const headerCanvasButton = document.getElementById('headerCanvasButton');
const headerCanvasButtonWrap = document.getElementById('headerCanvasButtonWrap');
const headerTempChatButton = document.getElementById('headerTempChatButton');
const headerSaveTempChatButton = document.getElementById('headerSaveTempChatButton');
const headerDotsButton = document.getElementById('headerDotsButton');
const headerSplitScreenButton = document.getElementById('headerSplitScreenButton');
const mainContainerHeaderFilesUpload = document.getElementById('mainContainerHeaderFilesUpload');
const filesCategoryFilterButton = document.getElementById('filesCategoryFilterButton');
const openModelSettingsButton = document.getElementById('openModelSettingsButton');
const downloadChatMenuButton = document.getElementById('downloadChatMenuItem');

// Narrow viewports use SplitScreenManager's compact tabbed mode, so the
// feature remains available on phones instead of disappearing entirely.
const MIN_SPLIT_SCREEN_VIEWPORT_WIDTH = 320;
let splitScreenHeaderButtonPreferredVisible = false;

const chatArea = document.getElementById('chatArea');
const chatBoxArea = document.getElementById('chatBoxArea');
const chatAreaContainer = document.getElementById('chatAreaContainer');
const tempChatSubtitle = document.getElementById('tempChatSubtitle');

function t(key, fallback) {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}


function formatT(key, fallback, vars) {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars && Object.prototype.hasOwnProperty.call(vars, token) ? vars[token] : '';
        return value == null ? '' : String(value);
    });
}


const sidebarAutomationsButton = document.getElementById('sidebarAutomations');
const sidebarProjectsButton = document.getElementById('sidebarProjects');
const sidebarWorkspaceButton = document.getElementById('sidebarWorkspace');
const sidebarButton = document.getElementById('sidebarChatsSearch');





function getDocumentTitleSection(sectionId) {
    switch (sectionId) {
        case 'chat':
            return {
                key: 'document_title_chat',
                text: typeof window.getTranslation === 'function'
                    ? window.getTranslation('document_title_chat', 'Chat')
                    : 'Chat',
            };
        case 'workspace':
            return {
                key: 'document_title_workspace',
                text: typeof window.getTranslation === 'function'
                    ? window.getTranslation('document_title_workspace', 'Workspace')
                    : 'Workspace',
            };
        case 'automations':
            return {
                key: 'document_title_automations',
                text: typeof window.getTranslation === 'function'
                    ? window.getTranslation('document_title_automations', 'Automations')
                    : 'Automations',
            };
        case 'projects':
            return {
                key: 'document_title_projects',
                text: typeof window.getTranslation === 'function'
                    ? window.getTranslation('document_title_projects', 'Projects')
                    : 'Projects',
            };
        case 'chatsSearch':
            return {
                key: 'document_title_chats_search',
                text: typeof window.getTranslation === 'function'
                    ? window.getTranslation('document_title_chats_search', 'Chats Search')
                    : 'Chats Search',
            };
        default:
            return {
                key: '',
                text: sectionId || '',
            };
    }
}


function setAppSectionTitle(sectionId) {
    const section = getDocumentTitleSection(sectionId);
    if (typeof window.setDocumentTitleWithAppName === 'function') {
        window.setDocumentTitleWithAppName(section.text, { sectionKey: section.key });
        return;
    }
    document.title = `${window.applicationName || 'Omlorix'} - ${section.text}`;
}

if (typeof window !== 'undefined') {
    window.setAppSectionTitle = setAppSectionTitle;
}

function readStoredBoolean(key, fallback = false) {
    try {
        const stored = localStorage.getItem(key);
        if (stored === 'true' || stored === '1') return true;
        if (stored === 'false' || stored === '0') return false;
    } catch (_) {
        // ignore
    }
    return fallback;
}

function shouldAlwaysUseTemporaryChat() {
    if (!isTemporaryChatAllowed()) {
        return false;
    }
    if (typeof window !== 'undefined' && typeof window.getChatBooleanSetting === 'function') {
        return window.getChatBooleanSetting('always_use_temporary_chat', false);
    }
    return readStoredBoolean('always_use_temporary_chat', false);
}

function isPendingChatOpen() {
    if (!chatContainer) {
        return false;
    }
    return chatContainer.getAttribute('data-pending-chat') === 'true';
}

function isTemporaryChatAllowed() {
    if (typeof window !== 'undefined' && window.chatSetup && Object.prototype.hasOwnProperty.call(window.chatSetup, 'temporary_chat_allowed')) {
        return Boolean(window.chatSetup.temporary_chat_allowed);
    }
    return true;
}

function parseTemporaryChatQueryValue(value) {
    if (typeof value !== 'string') {
        return null;
    }
    const normalized = value.trim().toLowerCase();
    if (['1', 'true', 'yes', 'on'].includes(normalized)) {
        return true;
    }
    return null;
}

function readTemporaryChatUrlOverride() {
    if (!isTemporaryChatAllowed()) {
        return false;
    }
    if (typeof window === 'undefined' || typeof window.location?.search !== 'string') {
        return null;
    }
    try {
        const params = new URLSearchParams(window.location.search);
        const requestedMode = parseTemporaryChatQueryValue(params.get('temporary-chat'));
        return requestedMode === true ? true : null;
    } catch (_) {
        return null;
    }
}

let temporaryChatSessionOverride = readTemporaryChatUrlOverride();

function getResolvedTemporaryChatMode() {
    const urlOverride = readTemporaryChatUrlOverride();
    if (typeof urlOverride === 'boolean') {
        temporaryChatSessionOverride = urlOverride;
    }
    if (typeof temporaryChatSessionOverride === 'boolean') {
        return temporaryChatSessionOverride;
    }
    return shouldAlwaysUseTemporaryChat();
}

let temporaryChatActive = false;

function hasUnsavedTemporaryConversation() {
    if (!chatContainer || !chatAreaContainer) {
        return false;
    }
    const chatId = String(chatContainer.getAttribute('data-chat-id') || '').trim();
    if (chatId) {
        return false;
    }
    return Boolean(
        chatAreaContainer.querySelector('.user-message-area')
        || chatAreaContainer.querySelector('.assistant-message-container')
    );
}

function teardownCallRouteIfActive() {
    if (typeof window.realtimeCall?.isCallRouteActive === 'function' && window.realtimeCall.isCallRouteActive()) {
        window.realtimeCall.deactivateCallRoute({ restorePath: false, stopActive: true });
    }
}

function hasSplitScreenUrlState() {
    if (typeof window === 'undefined' || typeof window.location?.search !== 'string') {
        return false;
    }
    try {
        const params = new URLSearchParams(window.location.search);
        return params.has('left') || params.has('right') || params.get('split') === '1';
    } catch (_) {
        return false;
    }
}

function updateTemporaryChatButtonState() {
    if (!headerTempChatButton) {
        return;
    }

    headerTempChatButton.classList.toggle('temp-chat-active', temporaryChatActive);
    headerTempChatButton.setAttribute('aria-pressed', temporaryChatActive ? 'true' : 'false');
    const labelKey = temporaryChatActive ? 'temp_chat_on_aria' : 'temp_chat_off_aria';
    const label = temporaryChatActive
        ? t('temp_chat_on_aria', 'Temporary chat on')
        : t('temp_chat_off_aria', 'Temporary chat off');
    headerTempChatButton.setAttribute('data-i18n-attr', `aria-label:${labelKey};title:${labelKey}`);
    headerTempChatButton.setAttribute('aria-label', label);
    headerTempChatButton.setAttribute('title', label);

    updateTemporaryChatSubtitle();
}

function buildTemporaryChatSubtitle() {
    const setup = window?.chatSetup;
    if (!setup) {
        return t(
            'temporaryMode.bannerNoPersistence',
            'Temporary mode is active. Messages in this session are not stored on the server.'
        );
    }

    const hasSavingFlag = Object.prototype.hasOwnProperty.call(setup, 'temporary_chat_saving_enabled');
    const hasPersistenceFlag = Object.prototype.hasOwnProperty.call(setup, 'temporary_chat_persistence_enabled');
    const savingEnabled = hasSavingFlag
        ? Boolean(setup.temporary_chat_saving_enabled)
        : (hasPersistenceFlag ? Boolean(setup.temporary_chat_persistence_enabled) : false);
    const retentionEnabled = Boolean(setup.temporary_chat_retention_enabled);
    const retentionDays = Number(setup.temporary_chat_retention_days);

    if (!savingEnabled) {
        return t(
            'temporaryMode.bannerNoPersistence',
            'Temporary mode is active. Messages in this session are not stored on the server.'
        );
    }

    if (retentionEnabled && Number.isFinite(retentionDays) && retentionDays > 0) {
        if (retentionDays === 1) {
            return t(
                'temporaryMode.bannerRetentionOne',
                'Temporary mode is active. Chats are stored and automatically removed after 1 day by admin retention policy.'
            );
        }
        return formatT(
            'temporaryMode.bannerRetentionMany',
            'Temporary mode is active. Chats are stored and automatically removed after {count} days by admin retention policy.',
            { count: retentionDays }
        );
    }

    return t(
        'temporaryMode.bannerPersistent',
        'Temporary mode is active. Chats are stored by admin policy and may remain until manually deleted.'
    );
}

function updateTemporaryChatSubtitle() {
    if (!tempChatSubtitle) {
        return;
    }

    const subtitleWrapper = tempChatSubtitle.closest('.temp-chat-subtitle-wrapper');
    if (subtitleWrapper) {
        subtitleWrapper.hidden = !temporaryChatActive;
        if (temporaryChatActive) {
            subtitleWrapper.removeAttribute('aria-hidden');
        } else {
            subtitleWrapper.setAttribute('aria-hidden', 'true');
        }
    }

    if (!temporaryChatActive) {
        return;
    }
    tempChatSubtitle.textContent = buildTemporaryChatSubtitle();
}

function updateTemporaryChatButtonVisibility({ force } = {}) {
    if (!headerTempChatButton || !chatContainer) {
        return;
    }

    if (!isTemporaryChatAllowed()) {
        headerTempChatButton.style.display = 'none';
        return;
    }

    if (typeof force === 'boolean') {
        headerTempChatButton.style.display = force ? 'flex' : 'none';
        return;
    }

    const chatId = chatContainer.getAttribute('data-chat-id');
    const containerVisible = chatContainer.style.display !== 'none';
    const shouldShow = containerVisible && !chatId && !isPendingChatOpen();
    headerTempChatButton.style.display = shouldShow ? 'flex' : 'none';
}

function isSplitScreenViewportSupported() {
    return typeof window !== 'undefined' && window.innerWidth >= MIN_SPLIT_SCREEN_VIEWPORT_WIDTH;
}

function syncSplitScreenHeaderButtonVisibility() {
    if (!headerSplitScreenButton) {
        return;
    }
    headerSplitScreenButton.style.display = (
        splitScreenHeaderButtonPreferredVisible && isSplitScreenViewportSupported()
    ) ? 'flex' : 'none';
}

function setSplitScreenHeaderButtonVisibility(shouldShow) {
    splitScreenHeaderButtonPreferredVisible = Boolean(shouldShow);
    syncSplitScreenHeaderButtonVisibility();
}

function updateSaveTempChatButtonVisibility() {
    if (!headerSaveTempChatButton || !chatContainer) {
        return;
    }

    const chatId = String(chatContainer.getAttribute('data-chat-id') || '').trim();
    const containerVisible = chatContainer.style.display !== 'none';
    const shouldShow = containerVisible && !chatId && !isPendingChatOpen() && hasUnsavedTemporaryConversation();

    headerSaveTempChatButton.style.display = shouldShow ? 'flex' : 'none';
    headerSaveTempChatButton.disabled = Boolean(window.isGenerating);
    headerSaveTempChatButton.setAttribute(
        'title',
        window.isGenerating
            ? t('save_temp_chat_finish_generating_title', 'Finish generating before saving this chat')
            : t('save_temp_chat_title', 'Save chat')
    );
}

async function saveTemporaryChatConversation({ silentOnEmpty = false, suppressToast = false } = {}) {
    if (!chatContainer) {
        return null;
    }

    const existingChatId = String(chatContainer.getAttribute('data-chat-id') || '').trim();
    if (existingChatId) {
        return existingChatId;
    }

    if (window.isGenerating) {
        notifyWarning?.(t('save_temp_chat_finish_generating_warning', 'Finish generating before saving this chat.'));
        return null;
    }

    if (!hasUnsavedTemporaryConversation()) {
        if (!silentOnEmpty) {
            notifyWarning?.(t('save_temp_chat_empty_warning', 'There is no temporary chat to save.'));
        }
        return null;
    }

    const serializeHistory = typeof window.serializeTemporaryChatHistory === 'function'
        ? window.serializeTemporaryChatHistory
        : null;
    const tempChat = serializeHistory ? serializeHistory() : '[]';
    const modelId = String(modelSelect?.getAttribute('data-model-id') || '').trim();
    const projectId = String(chatContainer.getAttribute('data-project-id') || '').trim();

    if (headerSaveTempChatButton) {
        headerSaveTempChatButton.disabled = true;
    }

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
            throw new Error(errorData?.detail || formatT('save_temp_chat_failed_status', 'Failed to save chat ({status})', { status: response.status }));
        }

        const payload = await response.json().catch(() => ({}));
        const savedChatId = String(payload?.chat_id || '').trim();
        if (!savedChatId) {
            throw new Error(t('save_temp_chat_missing_id', 'Saved chat response did not include a chat id.'));
        }

        temporaryChatSessionOverride = false;
        setTemporaryChatMode(false, { persistPreference: false });

        if (typeof window.loadChatView === 'function') {
            await window.loadChatView(savedChatId);
        } else {
            chatContainer.setAttribute('data-chat-id', savedChatId);
            showChatContainer();
        }

        if (typeof window.initChatList === 'function') {
            await window.initChatList();
        }

        if (!suppressToast) {
            notifySuccess?.(t('save_temp_chat_success', 'Chat saved'));
        }
        return savedChatId;
    } catch (error) {
        console.error('Failed to save temporary chat:', error);
        if (!suppressToast) {
            notifyError?.(error.message || t('save_temp_chat_failed', 'Failed to save chat'));
        }
        return null;
    } finally {
        updateSaveTempChatButtonVisibility();
    }
}

function setTemporaryChatMode(nextValue, { persistPreference = false } = {}) {
    if (!isTemporaryChatAllowed()) {
        temporaryChatActive = false;
    } else {
        temporaryChatActive = Boolean(nextValue);
    }
    if (chatContainer) {
        chatContainer.dataset.tempChat = temporaryChatActive ? 'true' : 'false';
    }

    if (persistPreference) {
        try {
            localStorage.setItem('always_use_temporary_chat', temporaryChatActive ? 'true' : 'false');
        } catch (_) {
            // ignore
        }
    }

    updateTemporaryChatButtonState();
    updateTemporaryChatButtonVisibility();
    updateSaveTempChatButtonVisibility();
}

function syncTemporaryChatModeWithPreference() {
    setTemporaryChatMode(getResolvedTemporaryChatMode(), { persistPreference: false });
}

function prepareTemporaryChatConversationView() {
    if (!chatContainer || !chatArea || !chatContainerWelcome) {
        return;
    }

    chatContainer.removeAttribute('data-pending-chat');
    chatContainer.style.display = 'flex';
    chatArea.style.display = 'flex';
    chatContainerWelcome.style.display = 'none';
    modelSelect.style.display = 'flex';
    headerShareButton.style.display = 'none';
    if (headerCanvasButtonWrap) {
        headerCanvasButtonWrap.style.display = 'none';
    }
    if (headerSplitScreenButton) {
        setSplitScreenHeaderButtonVisibility(false);
    }
    initThreeDotsButton(false);
    updateTemporaryChatButtonVisibility({ force: true });
    updateTemporaryChatButtonState();
    updateSaveTempChatButtonVisibility();
}

function preparePendingChatConversationView() {
    if (!chatContainer || !chatArea || !chatContainerWelcome) {
        return;
    }

    chatContainer.setAttribute('data-pending-chat', 'true');
    chatContainer.style.display = 'flex';
    chatArea.style.display = 'flex';
    chatContainerWelcome.style.display = 'none';
    modelSelect.style.display = 'flex';
    headerShareButton.style.display = 'none';
    if (headerCanvasButtonWrap) {
        headerCanvasButtonWrap.style.display = 'none';
    }
    if (headerSplitScreenButton) {
        setSplitScreenHeaderButtonVisibility(false);
    }
    initThreeDotsButton(false);
    updateTemporaryChatButtonVisibility({ force: false });
    updateTemporaryChatButtonState();
    updateSaveTempChatButtonVisibility();
}

if (headerTempChatButton) {
    headerTempChatButton.addEventListener('click', () => {
        temporaryChatSessionOverride = !temporaryChatActive;
        setTemporaryChatMode(temporaryChatSessionOverride, { persistPreference: false });
    });
}

if (headerSaveTempChatButton) {
    headerSaveTempChatButton.addEventListener('click', async () => {
        await saveTemporaryChatConversation();
    });
}

if (chatAreaContainer && typeof MutationObserver !== 'undefined') {
    const tempChatMutationObserver = new MutationObserver(() => {
        updateSaveTempChatButtonVisibility();
    });
    tempChatMutationObserver.observe(chatAreaContainer, { childList: true, subtree: true });
}

setTemporaryChatMode(getResolvedTemporaryChatMode(), { persistPreference: false });

document.addEventListener('chatSetupReady', () => {
    syncTemporaryChatModeWithPreference();
    updateTemporaryChatSubtitle();
    // The initial router runs while /settings/chat/setup is still in flight.
    // Re-run only routes whose availability depends on that response so a
    // cold deep link can open as soon as its feature policy is known.
    resumeSetupDependentRouteAfterChatSetup();
});

document.addEventListener('i18n:updated', () => {
    // The button state and disclosure must be refreshed together. A generic
    // translation pass must never leave the control saying "off" while the
    // active temporary-chat disclosure remains visible.
    updateTemporaryChatButtonState();
    refreshCurrentRouteTitle();
});

function hideWorkspaceContainer() {
    if (typeof WorkspaceManager !== 'undefined' && typeof WorkspaceManager.hide === 'function') {
        WorkspaceManager.hide();
        return;
    }

    document.body?.classList.remove('workspace-view-active');

    if (workspaceContainer) {
        workspaceContainer.style.display = 'none';
    }

    // Hide the main header files elements
    if (mainContainerHeaderFilesUpload) {
        mainContainerHeaderFilesUpload.style.display = 'none';
    }
    if (filesCategoryFilterButton) {
        filesCategoryFilterButton.style.display = 'none';
    }
}



/**
 * Ask split screen to leave through its guarded teardown path.
 *
 * Navigation views discard both panels, so skipLoadFallback is intentional:
 * requestDisable still confirms unsaved conversations and running generations
 * before it clears either panel.
 */
async function requestSplitScreenExitForNavigation() {
    const manager = window.SplitScreenManager;
    if (!manager?.active) return true;
    if (typeof manager.requestDisable === 'function') {
        return manager.requestDisable({ skipLoadFallback: true });
    }
    manager.disable({ skipLoadFallback: true });
    return true;
}

async function showAutomationsContainer(options = {}) {
    // Feature policy is resolved asynchronously during application startup.
    // The router owns unavailable-route fallback; direct callers simply learn
    // that navigation was denied and leave the current view untouched.
    if (typeof window !== 'undefined' && window.enableAutomationsFeature !== true) return false;
    if (!await hideChatContainer()) return false;
    teardownCallRouteIfActive();
    hideWorkspaceContainer();
    hideProjectsContainer();
    hideChatsSearchContainer();
    if (typeof window.hideProjectSidebar === 'function') {
        window.hideProjectSidebar();
    }
    setAppSectionTitle('automations');
    automationsContainer.style.display = 'flex';
    // Deep links and popstate already have the correct URL. Avoid adding a
    // duplicate history entry when the router is restoring either one.
    if (!options.skipHistory && normalizeRoutePath(window.location.pathname) !== '/automations') {
        window.history.pushState(null, '', '/automations');
    }
    if (typeof window.initAutomations === 'function') {
        window.initAutomations();
    } else {
        window.__pendingAutomationsInit = true;
    }
    return true;
}
function hideAutomationsContainer() {
    automationsContainer.style.display = 'none';
    if (typeof window.cleanupAutomationSelectOutsideHandlers === 'function') {
        window.cleanupAutomationSelectOutsideHandlers();
    }
}
sidebarAutomationsButton.addEventListener('click', showAutomationsContainer);





async function showProjectsContainer(options = {}) {
    // Deep links, command-palette actions, and programmatic callers must obey
    // the same feature gate as the sidebar button.
    if (window.enableProjectsFeature !== true) return false;
    if (!await hideChatContainer()) return false;
    teardownCallRouteIfActive();
    hideWorkspaceContainer();
    hideAutomationsContainer();
    hideChatsSearchContainer();
    if (typeof window.hideProjectSidebar === 'function') {
        window.hideProjectSidebar();
    }
    // Show the main header projects elements
    setAppSectionTitle('projects');
    projectsContainer.style.display = 'flex';
    // Deep links and popstate already have the correct URL. Avoid adding a
    // duplicate history entry when the router is restoring either one.
    if (!options.skipHistory && normalizeRoutePath(window.location.pathname) !== '/projects') {
        window.history.pushState(null, '', '/projects');
    }
    initProjects();
    return true;
}
function hideProjectsContainer() {
    projectsContainer.style.display = 'none';
}
sidebarProjectsButton.addEventListener('click', showProjectsContainer);
    


async function showChatsSearchContainer() {
    if (!await hideChatContainer()) return false;
    teardownCallRouteIfActive();
    hideWorkspaceContainer();
    hideAutomationsContainer();
    hideProjectsContainer();
    if (typeof window.hideProjectSidebar === 'function') {
        window.hideProjectSidebar();
    }
    // Show the main header chats search elements
    setAppSectionTitle('chatsSearch');
    chatsSearchContainer.style.display = 'flex';
    window.history.pushState(null, '', '/chats/search');
    if (typeof window.focusChatsSearchInput === 'function') {
        window.focusChatsSearchInput();
    } else {
        requestAnimationFrame(() => {
            document.getElementById('chatsSearchInput')?.focus();
        });
    }
    return true;
}
function hideChatsSearchContainer() {
    chatsSearchContainer.style.display = 'none';
}
sidebarButton.addEventListener('click', showChatsSearchContainer);





function showChatContainer(options = {}) {
    if (!options.skipCallTeardown) {
        teardownCallRouteIfActive();
    }
    chatContainer.removeAttribute('data-pending-chat');
    setTemporaryChatMode(false, { persistPreference: false });
    initThreeDotsButton(true);
    hideWorkspaceContainer();
    hideAutomationsContainer();
    hideProjectsContainer();
    hideChatsSearchContainer();
    if (typeof window.showChatActionStart === 'function') {
        window.showChatActionStart();
    }
    setAppSectionTitle('chat');
    chatContainer.style.display = 'flex';

    const isSplit = window.SplitScreenManager && window.SplitScreenManager.active;
    chatArea.style.display = isSplit ? 'none' : 'flex';
    chatContainerWelcome.style.display = 'none';

    modelSelect.style.display = 'flex';
    modelSelect.classList.toggle('hidden', Boolean(isSplit));
    const canShareChat = window.chatSetup ? Boolean(window.chatSetup.enable_chat_sharing) : true;
    const hasActiveChatId = Boolean(String(chatContainer.getAttribute('data-chat-id') || '').trim());
    headerShareButton.style.display = canShareChat && hasActiveChatId ? 'flex' : 'none';
    if (typeof window.ChatShareModal?.syncHeaderShareVisibility === 'function') {
        void window.ChatShareModal.syncHeaderShareVisibility();
    }
    // headerCanvasButtonWrap visibility is managed by canvasFilesDropdown based on registered files
    updateTemporaryChatButtonVisibility({ force: false });
    headerDotsButton.style.display = 'flex';
    setSplitScreenHeaderButtonVisibility(false);
    if (typeof window.focusChatInput === 'function') {
        window.focusChatInput();
    }
}
async function hideChatContainer() {
    if (window.SplitScreenManager?.active && !await requestSplitScreenExitForNavigation()) {
        return false;
    }
    // A transcript response is no longer relevant once its chat surface is
    // hidden. Cancel it before clearing the shared chat binding.
    window.cancelActiveChatLoad?.();
    chatContainer.removeAttribute('data-pending-chat');
    setTemporaryChatMode(false, { persistPreference: false });
    chatContainer.style.display = 'none';
    chatContainer.removeAttribute('data-chat-id');
    closeModelSettingsSidebar();
    // Hide the main container header chat elements
    modelSelect.style.display = 'none';
    headerShareButton.style.display = 'none';
    if (headerCanvasButtonWrap) headerCanvasButtonWrap.style.display = 'none';
    if (window.canvasFilesDropdown) window.canvasFilesDropdown.clearFiles();
    if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.reset === 'function') {
        try {
            window.canvasMarkdownWidget.reset();
        } catch (_) {}
    }
    if (window.NotesToolSidebar && typeof window.NotesToolSidebar.reset === 'function') {
        try {
            window.NotesToolSidebar.reset();
        } catch (_) {}
    }
    if (window.deepResearchWidget && typeof window.deepResearchWidget.hidePreviewPanel === 'function') {
        try {
            window.deepResearchWidget.hidePreviewPanel();
        } catch (_) {}
    }
    if (window.latexPdfWidget && typeof window.latexPdfWidget.reset === 'function') {
        try {
            window.latexPdfWidget.reset();
        } catch (_) {}
    }
    updateTemporaryChatButtonVisibility({ force: false });
    headerDotsButton.style.display = 'none';
    setSplitScreenHeaderButtonVisibility(false);
    return true;
}





async function showChatStartContainer(options = {}) {
    if (window.SplitScreenManager?.active && !await requestSplitScreenExitForNavigation()) {
        return false;
    }
    // A guarded caller may have been superseded while split-screen teardown
    // was waiting for confirmation. Stop before changing any visible view.
    if (typeof options.navigationGuard === 'function' && !options.navigationGuard()) {
        return false;
    }
    // Starting a fresh conversation invalidates any in-flight saved-chat load.
    window.cancelActiveChatLoad?.();
    if (!options.skipCallTeardown) {
        teardownCallRouteIfActive();
    }
    if (window.slidePresentationWidget && typeof window.slidePresentationWidget.hidePreviewPanel === 'function') {
        try {
            window.slidePresentationWidget.hidePreviewPanel();
        } catch (_) {}
    }
    if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.reset === 'function') {
        try {
            window.canvasMarkdownWidget.reset();
        } catch (_) {}
    }
    if (window.NotesToolSidebar && typeof window.NotesToolSidebar.reset === 'function') {
        try {
            window.NotesToolSidebar.reset();
        } catch (_) {}
    }
    // Deep Research keeps its run state while behaving like every other
    // message-scoped preview: a fresh conversation must never inherit the
    // previously open report sidebar.
    if (window.deepResearchWidget && typeof window.deepResearchWidget.hidePreviewPanel === 'function') {
        try {
            window.deepResearchWidget.hidePreviewPanel();
        } catch (_) {}
    }
    if (window.latexPdfWidget && typeof window.latexPdfWidget.reset === 'function') {
        try {
            window.latexPdfWidget.reset();
        } catch (_) {}
    }
    hideWorkspaceContainer();
    hideAutomationsContainer();
    hideProjectsContainer();
    hideChatsSearchContainer();
    if (typeof window.showChatActionStart === 'function') {
        window.showChatActionStart();
    }
    resetGenerationUIState({ clearActiveAttr: true });
    if (typeof window.resetChatAttachmentsState === 'function') {
        try {
            window.resetChatAttachmentsState({ preserveSkills: false });
        } catch (_) {}
    }
    // Reset scroll state (hide button, remove spacer)
    if (typeof window.resetChatScrollState === 'function') {
        window.resetChatScrollState();
    }
    if (typeof window.resetChatInputDraftTempContext === 'function') {
        window.resetChatInputDraftTempContext();
    }
    chatContainer.removeAttribute('data-pending-chat');
    chatContainer.removeAttribute('data-chat-id');
    chatContainer.style.display = 'flex';
    setAppSectionTitle('chat');
    initThreeDotsButton(false);
    chatArea.style.display = 'none';
    if (chatAreaContainer) {
        chatAreaContainer.innerHTML = '';
    }
    chatContainerWelcome.style.display = 'flex';
    initWelcomeMessage();   
    modelSelect.style.display = 'flex';
    headerShareButton.style.display = 'none';
    if (headerCanvasButtonWrap) headerCanvasButtonWrap.style.display = 'none';
    if (window.canvasFilesDropdown) window.canvasFilesDropdown.clearFiles();
    if (Object.prototype.hasOwnProperty.call(options, 'forceTemporary')) {
        setTemporaryChatMode(Boolean(options.forceTemporary), { persistPreference: false });
    } else {
        syncTemporaryChatModeWithPreference();
    }
    updateTemporaryChatButtonVisibility({ force: true });
    setSplitScreenHeaderButtonVisibility(true);
    if (!options.skipHistory) {
        window.history.pushState(null, '', '/');
    }
    if (typeof window.focusChatInput === 'function') {
        window.focusChatInput();
    }
    if (typeof window.syncChatInputDraftContext === 'function') {
        window.syncChatInputDraftContext();
    }
    return true;
}

function refreshCurrentRouteTitle() {
    const path = normalizeRoutePath(window.location.pathname);
    if (path === '/files' || path === '/workspace' || path.startsWith('/workspace/')) {
        setAppSectionTitle('workspace');
    } else if (path === '/' || path === '' || path === '/index' || path === '/chat' || path.startsWith('/chat/')) {
        setAppSectionTitle('chat');
    } else if (path === '/automations') {
        setAppSectionTitle('automations');
    } else if (path === '/projects') {
        setAppSectionTitle('projects');
    } else if (path === '/chats/search') {
        setAppSectionTitle('chatsSearch');
    }
}



function initThreeDotsButton(showChatDownload) {
    const showModelSettings = readStoredBoolean(
        'show_model_settings',
        Boolean(window.chatSetup && window.chatSetup.show_model_settings)
    );
    if (showChatDownload) {
        // Must show the button
        headerDotsButton.style.display = 'flex';
        if (downloadChatMenuButton) {
            downloadChatMenuButton.style.display = 'flex';
        }
        if (showModelSettings) {
            // show the openModelSettingsButton
            openModelSettingsButton.style.display = 'flex';
        } else {
            // hide the openModelSettingsButton
            openModelSettingsButton.style.display = 'none';
        }
    } else {
        if (showModelSettings) {
            // show the openModelSettingsButton
            if (downloadChatMenuButton) {
                downloadChatMenuButton.style.display = 'none';
            }
            headerDotsButton.style.display = 'flex';
            openModelSettingsButton.style.display = 'flex';
        } else {
            // hide the openModelSettingsButton
            openModelSettingsButton.style.display = 'none';
            if (downloadChatMenuButton) {
                downloadChatMenuButton.style.display = 'none';
            }
            headerDotsButton.style.display = 'none';
        }
    }
}

function refreshModelSettingsVisibility() {
    if (!chatContainer || chatContainer.style.display !== 'flex') {
        return;
    }
    const hasActiveChat = Boolean(chatContainer.getAttribute('data-chat-id'));
    initThreeDotsButton(hasActiveChat);
}




async function navigateTo(path, state = {}, { replace = false } = {}) {
    if (typeof window === 'undefined' || typeof history === 'undefined') {
        return false;
    }
    // Guard before changing the URL. If the user keeps an unsaved split panel,
    // the visible view and browser history must both remain on the split route.
    if (window.SplitScreenManager?.active && !await requestSplitScreenExitForNavigation()) {
        return false;
    }
    const targetPath = typeof path === 'string' && path.length ? path : '/';
    const currentPath = window.location?.pathname;
    const nextState = state || null;
    const currentState = history.state || null;
    const isSamePath = currentPath === targetPath;
    const statesDiffer = JSON.stringify(currentState) !== JSON.stringify(nextState);
    if (isSamePath && !statesDiffer && !replace) {
        return true;
    }
    if (replace || isSamePath) {
        history.replaceState(nextState, '', targetPath);
    } else {
        history.pushState(nextState, '', targetPath);
    }
    if (!handleAppRoute(targetPath)) {
        showChatStartContainer({ skipHistory: true });
    }
    return true;
}




function normalizeRoutePath(pathname) {
    if (typeof pathname !== 'string') {
        return '/';
    }
    const trimmed = pathname.trim();
    if (!trimmed) {
        return '/';
    }
    if (trimmed.length > 1 && trimmed.endsWith('/')) {
        return trimmed.slice(0, -1);
    }
    return trimmed;
}

/**
 * Parse a prompt share deep link without accepting prefixes or extra segments.
 * The route mode is only a presentation hint; the backend preview remains the
 * authority for the capability's actual clone/live/collaborate permission.
 */
function parsePromptShareRoute(pathname) {
    if (typeof pathname !== 'string') return null;
    const match = /^\/prompts\/(clone|live|collaborate)\/([^/]+)$/.exec(normalizeRoutePath(pathname));
    if (!match) return null;

    try {
        const shareId = decodeURIComponent(match[2]).trim();
        if (!shareId || shareId.length > 512) return null;
        if (shareId.includes('/') || shareId.includes('\\')) return null;
        return { shareId, shareType: match[1] };
    } catch (_) {
        return null;
    }
}

function isConnectionsWorkspaceAllowed() {
    if (typeof window === 'undefined') {
        return true;
    }
    if (window.enableConnectionsFeature === false) {
        return false;
    }
    if (window.connectionsAllowed === false) {
        return false;
    }
    return true;
}

/**
 * Return whether setup-dependent route permissions are available.
 *
 * The static scripts finish before the authenticated chat setup request in the
 * normal cold-start path. Treating an undefined flag as a denial would either
 * redirect `/automations` to `/` or leave `/projects` with every view hidden.
 */
function isChatSetupReadyForFeatureRoutes() {
    return Boolean(window.chatSetup && typeof window.chatSetup === 'object');
}

let routeTransitionToken = 0;

/**
 * Replace an unavailable feature deep link with the chat start view.
 *
 * This fallback runs only after chat setup has positively resolved the feature
 * as unavailable. It uses replaceState so an unsupported URL does not become a
 * dead entry in browser history.
 */
async function showUnavailableFeatureRouteFallback(routePath) {
    const transitionToken = routeTransitionToken;
    const isCurrentTransition = () => (
        routeTransitionToken === transitionToken
        && normalizeRoutePath(window.location.pathname) === routePath
    );
    const shown = await showChatStartContainer({
        skipHistory: true,
        navigationGuard: isCurrentTransition,
    });
    if (shown === false || !isCurrentTransition()) return false;

    window.history.replaceState(null, '', '/');
    return true;
}

/**
 * Resume a cold setup-dependent deep link once chat setup has set its flags.
 */
function resumeSetupDependentRouteAfterChatSetup() {
    const currentPath = normalizeRoutePath(window.location.pathname);
    if (currentPath === '/automations' || currentPath === '/projects') {
        handleAppRoute(currentPath);
    }
}

function handleAppRoute(pathname) {
    routeTransitionToken += 1;
    const path = normalizeRoutePath(pathname);
    const shouldPreserveSplitUrl = hasSplitScreenUrlState();
    if (path !== '/call' && typeof window.realtimeCall?.isCallRouteActive === 'function' && window.realtimeCall.isCallRouteActive()) {
        window.realtimeCall.deactivateCallRoute({ restorePath: false, stopActive: true });
    }
    if (shouldPreserveSplitUrl && (path === '/chat' || path.startsWith('/chat/'))) {
        showChatStartContainer({ skipHistory: true });
        return true;
    }
    if (path.startsWith('/chat/')) {
        return false;
    }
    const promptShareIntent = parsePromptShareRoute(path);
    if (promptShareIntent && window.PromptLibraryManager?.handleSharedPromptRoute) {
        return window.PromptLibraryManager.handleSharedPromptRoute(promptShareIntent) !== false;
    }
    // Todo list creation and editing are first-class workspace pages. Keep
    // these nested routes inside the todo tab on reload and history changes.
    if (/^\/workspace\/todo\/lists\/(?:new|[^/]+\/edit)$/.test(path)) {
        if (typeof showWorkspaceContainer === 'function') {
            showWorkspaceContainer({ tab: 'todo' });
        }
        return true;
    }
    switch (path) {
        case '':
        case '/':
            if (shouldPreserveSplitUrl) {
                showChatStartContainer({ skipHistory: true });
                return true;
            }
            showChatStartContainer({});
            return true;
        case '/chat':
            showChatStartContainer({ skipHistory: true });
            return true;
        case '/files':
        case '/workspace/files':
            if (typeof showWorkspaceContainer === 'function') {
                showWorkspaceContainer({ tab: 'files' });
            }
            return true;
        case '/workspace':
        case '/workspace/home':
            if (typeof showWorkspaceContainer === 'function') {
                showWorkspaceContainer({ tab: 'home' });
            }
            return true;
        case '/workspace/notifications':
            if (typeof showWorkspaceContainer === 'function') {
                showWorkspaceContainer({ tab: 'notifications' });
            }
            return true;
        case '/workspace/messages':
            if (typeof showWorkspaceContainer === 'function') {
                showWorkspaceContainer({ tab: 'messages' });
            }
            return true;
        case '/workspace/connections':
            if (!isConnectionsWorkspaceAllowed()) {
                if (typeof showWorkspaceContainer === 'function') {
                    showWorkspaceContainer({ tab: 'home' });
                }
                return true;
            }
            if (typeof showWorkspaceContainer === 'function') {
                showWorkspaceContainer({ tab: 'connections' });
            }
            return true;
        case '/workspace/skills':
            if (typeof showWorkspaceContainer === 'function') {
                showWorkspaceContainer({ tab: 'skills' });
            }
            return true;
        case '/workspace/agents':
            if (typeof showWorkspaceContainer === 'function') {
                showWorkspaceContainer({ tab: 'agents' });
            }
            return true;
        case '/workspace/todo':
            if (typeof showWorkspaceContainer === 'function') {
                showWorkspaceContainer({ tab: 'todo' });
            }
            return true;
        case '/workspace/notes':
            if (typeof showWorkspaceContainer === 'function') {
                showWorkspaceContainer({ tab: 'notes' });
            }
            return true;
        case '/workspace/memories':
            if (typeof showWorkspaceContainer === 'function') {
                showWorkspaceContainer({ tab: 'memories' });
            }
            return true;
        case '/workspace/prompts':
            if (typeof showWorkspaceContainer === 'function') {
                showWorkspaceContainer({ tab: 'prompts' });
            }
            return true;
        case '/workspace/bookmarks':
            if (typeof showWorkspaceContainer === 'function') {
                showWorkspaceContainer({ tab: 'bookmarks' });
            }
            return true;
        case '/automations':
            // Keep the deep-link URL intact until the asynchronous setup
            // response can distinguish "not loaded yet" from "not allowed".
            if (!isChatSetupReadyForFeatureRoutes()) return true;
            if (window.enableAutomationsFeature !== true) {
                void showUnavailableFeatureRouteFallback('/automations');
                return true;
            }
            void showAutomationsContainer({ skipHistory: true });
            return true;
        case '/projects':
            // Projects uses the same setup-dependent policy lifecycle as
            // Automations and must follow the same cold-route behavior.
            if (!isChatSetupReadyForFeatureRoutes()) return true;
            if (window.enableProjectsFeature !== true) {
                void showUnavailableFeatureRouteFallback('/projects');
                return true;
            }
            void showProjectsContainer({ skipHistory: true });
            return true;
        case '/chats/search':
            showChatsSearchContainer({});
            return true;
        case '/index':
            showChatStartContainer({});
            return true;
        case '/call':
            if (typeof window.realtimeCall?.activateCallRoute === 'function') {
                window.realtimeCall.activateCallRoute();
            } else {
                showChatStartContainer({ skipHistory: true, skipCallTeardown: true });
                showChatContainer({ skipCallTeardown: true });
            }
            return true;
        default:
            showChatStartContainer({});
            return true;
    }
}
const path = window.location.pathname;
if (!handleAppRoute(path)) {
    const chatId = extractChatIdFromPath(path);
    const focusMessageId = typeof window.extractChatMessageIdFromSearch === 'function'
        ? window.extractChatMessageIdFromSearch(window.location.search)
        : null;
    loadChatView(chatId, false, { focusMessageId, preserveHistory: true }).then((loaded) => {
        if (!loaded || typeof window.restoreProjectSidebarForChat !== 'function') {
            return;
        }
        return window.restoreProjectSidebarForChat(chatId);
    }).catch((error) => {
        console.warn('Failed to load initial chat route or restore its project sidebar', error);
    });
}

if (typeof window !== 'undefined') {
    window.addEventListener('app:applicationNameUpdated', refreshCurrentRouteTitle);
    window.addEventListener('resize', syncSplitScreenHeaderButtonVisibility);
    window.addEventListener('orientationchange', syncSplitScreenHeaderButtonVisibility);
}

function extractChatIdFromPath(pathname) {
    if (typeof pathname !== 'string') {
        return null;
    }
    const CHAT_ROUTE_REGEX = /^\/chat\/([^/]+)$/;
    const match = CHAT_ROUTE_REGEX.exec(pathname);
    if (!match) {
        return null;
    }
    try {
        return decodeURIComponent(match[1]);
    } catch (err) {
        console.error('Failed to decode chat id from path:', err);
        return match[1];
    }
}

// Expose functions to window for keyboard shortcuts and external access
if (typeof window !== 'undefined') {
    window.hideWorkspaceContainer = hideWorkspaceContainer;
    window.showAutomationsContainer = showAutomationsContainer;
    window.showProjectsContainer = showProjectsContainer;
    window.showChatsSearchContainer = showChatsSearchContainer;
    window.showChatStartContainer = showChatStartContainer;
    window.showChatContainer = showChatContainer;
    window.hideChatContainer = hideChatContainer;
    window.hideProjectsContainer = hideProjectsContainer;
    window.isTemporaryChatModeActive = () => temporaryChatActive;
    window.setTemporaryChatMode = setTemporaryChatMode;
    window.syncTemporaryChatModeWithPreference = syncTemporaryChatModeWithPreference;
    window.prepareTemporaryChatConversationView = prepareTemporaryChatConversationView;
    window.preparePendingChatConversationView = preparePendingChatConversationView;
    window.hasUnsavedTemporaryConversation = hasUnsavedTemporaryConversation;
    window.saveTemporaryChatConversation = saveTemporaryChatConversation;
    window.refreshModelSettingsVisibility = refreshModelSettingsVisibility;
    window.handleAppRoute = handleAppRoute;
    window.normalizeRoutePath = normalizeRoutePath;
    window.getTemporaryChatSessionOverride = () => temporaryChatSessionOverride;
}
