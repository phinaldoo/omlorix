const chatAreaEl = document.getElementById('chatArea');
const chatLoadStatusEl = document.getElementById('chatLoadStatus');
const chatLoadStatusMessageEl = document.getElementById('chatLoadStatusMessage');
const chatLoadSpinnerEl = document.getElementById('chatLoadSpinner');
const chatLoadRetryButtonEl = document.getElementById('chatLoadRetryButton');




let activeChatActionContext = null;
let previousChatLayoutState = null;

let chatProjectsCache = null;
let chatProjectsPromise = null;

// Only one main-transcript request may own the chat view at a time. The
// monotonically increasing token protects every post-request side effect, while
// AbortController also releases browser/network work as soon as the user moves on.
let activeChatLoadController = null;
let activeChatLoadToken = 0;
let pendingChatMessageFocus = null;
let highlightedChatMessage = null;
let highlightedChatMessageTimer = null;

function sidebarDropdownT(key, fallback) {
    if (typeof sidebarChatT === 'function') {
        return sidebarChatT(key, fallback);
    }
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function sidebarDropdownTf(key, fallback, vars) {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(sidebarDropdownT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars?.[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

/**
 * Update the persistent loading surface for the main chat transcript.
 *
 * The status element lives outside the role="log" transcript so progress and
 * failure announcements are not mistaken for conversation messages. The
 * composer remains inert until history is available, preventing a newly sent
 * message from racing the initial transcript render.
 */
function setMainChatLoadStatus(status, message = '') {
    const chatContainer = document.getElementById('chatContainer');
    const chatArea = document.getElementById('chatArea');
    const chatBoxArea = document.getElementById('chatBoxArea');
    const isLoading = status === 'loading';
    const isError = status === 'error';
    const hasActiveGeneration = chatContainer?.hasAttribute('data-active-generation') === true;

    chatContainer?.toggleAttribute('data-chat-loading', isLoading);
    chatContainer?.toggleAttribute('data-chat-load-error', isError);
    chatArea?.setAttribute('aria-busy', isLoading ? 'true' : 'false');

    if (chatBoxArea) {
        if ((isLoading || isError) && !hasActiveGeneration) {
            chatBoxArea.setAttribute('inert', '');
            chatBoxArea.setAttribute('aria-disabled', 'true');
        } else {
            chatBoxArea.removeAttribute('inert');
            chatBoxArea.removeAttribute('aria-disabled');
        }
    }

    if (!chatLoadStatusEl) {
        return;
    }
    chatLoadStatusEl.hidden = status === 'idle';
    chatLoadStatusEl.setAttribute('role', isError ? 'alert' : 'status');
    chatLoadStatusEl.setAttribute('aria-live', isError ? 'assertive' : 'polite');
    if (chatLoadStatusMessageEl) {
        // Keep the declarative translation hook synchronized too, so changing
        // language while this state is visible cannot restore the loading copy
        // over an error (or vice versa).
        chatLoadStatusMessageEl.dataset.i18n = isError
            ? 'chat_load_messages_failed'
            : 'chat_loading_messages';
        chatLoadStatusMessageEl.textContent = message;
    }
    if (chatLoadSpinnerEl) {
        chatLoadSpinnerEl.hidden = !isLoading;
    }
    if (chatLoadRetryButtonEl) {
        chatLoadRetryButtonEl.hidden = !isError;
    }
}

/**
 * Cancel an obsolete transcript request and invalidate all of its callbacks.
 * Navigation functions call this when leaving the chat surface entirely.
 */
function cancelActiveChatLoad({ resetStatus = true } = {}) {
    activeChatLoadToken += 1;
    if (activeChatLoadController) {
        activeChatLoadController.abort();
        activeChatLoadController = null;
    }
    if (resetStatus) {
        setMainChatLoadStatus('idle');
    }
}

function escapeSidebarDropdownHtml(value) {
    if (typeof escapeHtml === 'function') {
        return escapeHtml(value);
    }
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function areProjectsEnabled() {
    return Boolean(
        (typeof window !== 'undefined' && window.chatSetup?.enable_projects) ||
        (typeof window !== 'undefined' && window.enableProjectsFeature)
    );
}

function invalidateChatProjectsCache() {
    chatProjectsCache = null;
}

async function fetchChatProjects() {
    if (!areProjectsEnabled()) {
        return [];
    }
    if (chatProjectsCache) {
        return chatProjectsCache;
    }
    if (chatProjectsPromise) {
        return chatProjectsPromise;
    }
    chatProjectsPromise = (async () => {
        try {
            const res = await window.authedFetch(`/api/v1/projects/list`);
            if (!res.ok) {
                notifyError(sidebarDropdownTf('sidebar_chat_project_load_failed_status', 'Failed to load projects ({status})', { status: res.status }));
            }
            const data = await res.json().catch(() => ({}));
            chatProjectsCache = Array.isArray(data) ? data : data?.projects ?? [];
            return chatProjectsCache;
        } finally {
            chatProjectsPromise = null;
        }
    })();
    return chatProjectsPromise;
}

async function updateChatProjectAssignment(chatId, projectId) {
    const payload = {
        chat_id: String(chatId),
        project_id: projectId ?? null,
    };
    const res = await window.authedFetch(`/api/v1/chats/project/update`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
    });
    if (!res.ok) {
        const detail = await res.text().catch(() => '');
        notifyError(detail || sidebarDropdownTf('sidebar_chat_project_update_failed_status', 'Request failed ({status})', { status: res.status }));
    }
    await res.json().catch(() => ({}));
    invalidateChatProjectsCache();
    if (typeof initChatList === 'function') {
        await initChatList();
    }
}



function scrollChatToBottom() {
    const scrollHost = document.getElementById('chatArea') || document.getElementById('chatAreaContainer');
    if (!scrollHost) {
        return;
    }
    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (window.ChatScrollCoordinator && scrollHost.id === 'chatArea') {
        window.ChatScrollCoordinator.scrollToBottom(scrollHost, chatAreaContainer);
    } else {
        scrollHost.scrollTop = scrollHost.scrollHeight;
    }
    // Update scroll button visibility after scrolling
    if (typeof updateScrollButtonVisibility === 'function') {
        requestAnimationFrame(updateScrollButtonVisibility);
    }
}

function scrollChatToBottomAfterImagesLoad({ focusMessageId = '', isCurrent = null } = {}) {
    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (!chatAreaContainer) {
        if (!focusMessageId) scrollChatToBottom();
        return;
    }

    const followTarget = () => {
        if (typeof isCurrent === 'function' && !isCurrent()) {
            return;
        }
        if (focusMessageId && focusChatMessage(focusMessageId, { highlight: false, moveFocus: false })) {
            return;
        }
        scrollChatToBottom();
    };
    
    const images = chatAreaContainer.querySelectorAll('img');
    const pendingImages = Array.from(images).filter(img => !img.complete);
    
    if (pendingImages.length === 0) {
        followTarget();
        return;
    }
    
    let loadedCount = 0;
    const onImageLoad = () => {
        loadedCount++;
        if (loadedCount >= pendingImages.length) {
            followTarget();
        }
    };
    
    pendingImages.forEach(img => {
        img.addEventListener('load', onImageLoad, { once: true });
        img.addEventListener('error', onImageLoad, { once: true });
    });
    
    // Fallback: re-check follow state after 500 ms in case an image never fires.
    setTimeout(followTarget, 500);
}

function buildChatRoute(chatId, messageId = '') {
    const normalizedChatId = String(chatId || '').trim();
    const normalizedMessageId = String(messageId || '').trim();
    if (!normalizedChatId) return '/';
    const basePath = `/chat/${encodeURIComponent(normalizedChatId)}`;
    return normalizedMessageId
        ? `${basePath}?message=${encodeURIComponent(normalizedMessageId)}`
        : basePath;
}

function extractChatMessageIdFromSearch(search = '') {
    try {
        return String(new URLSearchParams(String(search || '')).get('message') || '').trim() || null;
    } catch (_) {
        return null;
    }
}

function resolveChatMessageFocusTarget(messageId) {
    const normalizedMessageId = String(messageId || '').trim();
    if (!normalizedMessageId) return null;

    const userAnchor = document.getElementById(`u-${normalizedMessageId}`);
    if (userAnchor) {
        return userAnchor.closest?.('.user-message-container, .user-message-area') || userAnchor;
    }

    const chatAreaContainer = document.getElementById('chatAreaContainer');
    const assistantContainers = Array.from(
        chatAreaContainer?.querySelectorAll?.('.assistant-message-container') || []
    );
    const assistantTarget = assistantContainers.find((container) => (
        String(container.dataset?.assistantMessageId || '').trim() === normalizedMessageId
        || container.id === `a-${normalizedMessageId}`
    ));
    if (!assistantTarget) return null;

    const referenceId = String(assistantTarget.dataset?.referenceId || '').trim();
    const retryCount = Number.parseInt(assistantTarget.dataset?.retryCount || '0', 10) || 0;
    const isHidden = assistantTarget.dataset?.hidden === 'true'
        || assistantTarget.style?.display === 'none'
        || assistantTarget.getAttribute?.('aria-hidden') === 'true';
    if (isHidden && referenceId && typeof window.switchAssistantVersion === 'function') {
        window.switchAssistantVersion(referenceId, retryCount);
    }
    return assistantTarget;
}

function focusChatMessage(messageId, { highlight = true, moveFocus = true } = {}) {
    const target = resolveChatMessageFocusTarget(messageId);
    if (!target) return false;

    target.scrollIntoView?.({ block: 'center', inline: 'nearest', behavior: 'auto' });

    if (moveFocus && typeof target.focus === 'function') {
        if (!target.hasAttribute?.('tabindex')) {
            target.setAttribute?.('tabindex', '-1');
        }
        try {
            target.focus({ preventScroll: true });
        } catch (_) {
            target.focus();
        }
    }

    if (highlight && target.classList) {
        if (highlightedChatMessage && highlightedChatMessage !== target) {
            highlightedChatMessage.classList?.remove('chat-message-bookmark-target');
        }
        if (highlightedChatMessageTimer) {
            clearTimeout(highlightedChatMessageTimer);
        }
        highlightedChatMessage = target;
        target.classList.remove('chat-message-bookmark-target');
        // Restart the existing shared highlight animation on repeated navigation.
        void target.offsetWidth;
        target.classList.add('chat-message-bookmark-target');
        highlightedChatMessageTimer = setTimeout(() => {
            if (highlightedChatMessage === target) {
                target.classList?.remove('chat-message-bookmark-target');
                highlightedChatMessage = null;
            }
            highlightedChatMessageTimer = null;
        }, 1800);
    }

    if (typeof updateScrollButtonVisibility === 'function') {
        requestAnimationFrame(updateScrollButtonVisibility);
    }
    return true;
}

// ===== Scroll to Bottom Button Logic =====
const scrollToBottomBtn = document.getElementById('scrollToBottomBtn');
let scrollButtonDebounceTimer = null;
const SCROLL_THRESHOLD = 100; // pixels from bottom to show button

function isNearBottom(element) {
    if (!element) return true;
    const scrollTop = element.scrollTop;
    const scrollHeight = element.scrollHeight;
    const clientHeight = element.clientHeight;
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    return distanceFromBottom <= SCROLL_THRESHOLD;
}

function updateScrollButtonVisibility() {
    const chatArea = document.getElementById('chatArea');
    if (!chatArea || !scrollToBottomBtn) return;
    
    // Don't show if chat area has minimal content
    if (chatArea.scrollHeight <= chatArea.clientHeight + 50) {
        scrollToBottomBtn.classList.remove('visible');
        return;
    }
    
    if (isNearBottom(chatArea)) {
        scrollToBottomBtn.classList.remove('visible');
    } else {
        scrollToBottomBtn.classList.add('visible');
    }
}

function handleChatScroll() {
    if (scrollButtonDebounceTimer) {
        cancelAnimationFrame(scrollButtonDebounceTimer);
    }
    scrollButtonDebounceTimer = requestAnimationFrame(updateScrollButtonVisibility);
}

function initScrollToBottomButton() {
    const chatArea = document.getElementById('chatArea');
    if (!chatArea || !scrollToBottomBtn) return;
    
    // Scroll event listener with passive for performance
    chatArea.addEventListener('scroll', handleChatScroll, { passive: true });
    
    // Click handler for smooth scroll
    scrollToBottomBtn.addEventListener('click', () => {
        const scrollHost = document.getElementById('chatArea');
        if (!scrollHost) return;
        const chatAreaContainer = document.getElementById('chatAreaContainer');

        if (window.ChatScrollCoordinator) {
            window.ChatScrollCoordinator.scrollToBottom(
                scrollHost,
                chatAreaContainer,
                { behavior: 'smooth' },
            );
        } else {
            scrollHost.scrollTo({
                top: scrollHost.scrollHeight,
                behavior: 'smooth'
            });
        }
    });
    
    // Also update on window resize (chat area height may change)
    let resizeDebounce = null;
    window.addEventListener('resize', () => {
        if (resizeDebounce) clearTimeout(resizeDebounce);
        resizeDebounce = setTimeout(updateScrollButtonVisibility, 100);
    }, { passive: true });
    
    // Initial check
    updateScrollButtonVisibility();
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initScrollToBottomButton);
} else {
    initScrollToBottomButton();
}

// Export for use elsewhere if needed
if (typeof window !== 'undefined') {
    window.updateScrollButtonVisibility = updateScrollButtonVisibility;
}

// ===== New user-message alignment =====
/**
 * Align a newly-sent prompt under the chat viewport's top edge.
 *
 * ChatScrollCoordinator owns the animation and its temporary bottom spacer.
 * Keeping this small global wrapper preserves existing callers in send,
 * attachment-only, edit, and split-screen flows.
 */
function scrollUserMessageToTop(messageId, options = {}) {
    if (!window.ChatScrollCoordinator) {
        return false;
    }
    const started = window.ChatScrollCoordinator.alignUserMessage(messageId, options);
    if (started && typeof updateScrollButtonVisibility === 'function') {
        setTimeout(updateScrollButtonVisibility, 400);
    }
    return started;
}

/**
 * Reset both visible controls and temporary scroll geometry when the active
 * chat changes or the conversation view is cleared.
 */
function resetChatScrollState() {
    const scrollBtn = document.getElementById('scrollToBottomBtn');
    if (scrollBtn) {
        scrollBtn.classList.remove('visible');
    }
    const chatArea = document.getElementById('chatArea');
    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (window.ChatScrollCoordinator) {
        window.ChatScrollCoordinator.reset(chatArea, chatAreaContainer);
        return;
    }
    const dynamicSpacer = chatAreaContainer?.querySelector('.dynamic-scroll-spacer');
    if (dynamicSpacer) {
        dynamicSpacer.remove();
    }
}

if (typeof window !== 'undefined') {
    window.scrollUserMessageToTop = scrollUserMessageToTop;
    window.resetChatScrollState = resetChatScrollState;
}

function initDynamicSpacerListener() {
    const chatArea = document.getElementById('chatArea');
    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (chatArea && chatAreaContainer && window.ChatScrollCoordinator) {
        window.ChatScrollCoordinator.bindViewport(chatArea, chatAreaContainer);
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDynamicSpacerListener);
} else {
    initDynamicSpacerListener();
}

const CHAT_SIDEBAR_DROPDOWN_SELECTOR = '#history .select-dropdown.open, #pinnedChats .select-dropdown.open, .project-sidebar [data-section-list] .select-dropdown.open';
const CHAT_SIDEBAR_ROW_SELECTOR = '#history .sidebar-element, #pinnedChats .sidebar-element, .project-sidebar [data-section-list] .sidebar-element';

function closeAllChatDropdowns() {
    document.querySelectorAll(CHAT_SIDEBAR_DROPDOWN_SELECTOR).forEach((dropdown) => {
        dropdown.classList.remove('open');
    });
}

function initChatSidebarDropdownScrollClose() {
    const scrollHost = document.querySelector('.sidebar-main');
    if (!scrollHost || scrollHost.dataset.chatDropdownScrollBound === 'true') {
        return;
    }

    scrollHost.addEventListener('scroll', () => {
        closeAllChatDropdowns();
    }, { passive: true });
    scrollHost.dataset.chatDropdownScrollBound = 'true';
}

if (typeof window !== 'undefined' && window.registerEscapeHandler) {
    window.registerEscapeHandler({
        id: 'chat-history-dropdowns',
        priority: 80,
        isActive: () => Boolean(document.querySelector(CHAT_SIDEBAR_DROPDOWN_SELECTOR)),
        close: () => {
            closeAllChatDropdowns();
        }
    });
}

document.addEventListener('click', (e) => {
    if (!e.target.closest(CHAT_SIDEBAR_ROW_SELECTOR)) {
        closeAllChatDropdowns();
    }
});

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initChatSidebarDropdownScrollClose);
} else {
    initChatSidebarDropdownScrollClose();
}


async function loadChatView(chatId, already_streaming=false, options = {}) {
    const chatContainerEl = document.getElementById('chatContainer');
    if (!chatContainerEl) return false;
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId) return false;
    const preserveHistory = Boolean(options?.preserveHistory);
    const forceReload = Boolean(options?.forceReload);
    const requestedFocusMessageId = String(options?.focusMessageId || '').trim();
    pendingChatMessageFocus = requestedFocusMessageId
        ? { chatId: normalizedChatId, messageId: requestedFocusMessageId }
        : null;
    const getFocusMessageId = () => (
        pendingChatMessageFocus?.chatId === normalizedChatId
            ? pendingChatMessageFocus.messageId
            : requestedFocusMessageId
    );
    const commitChatRoute = () => {
        if (preserveHistory) return;
        const focusMessageId = getFocusMessageId();
        const targetPath = buildChatRoute(normalizedChatId, focusMessageId);
        const currentPath = `${window.location.pathname || ''}${window.location.search || ''}`;
        const routeState = focusMessageId
            ? { chatId: normalizedChatId, messageId: focusMessageId }
            : { chatId: normalizedChatId };
        if (currentPath !== targetPath) {
            history.pushState(routeState, '', targetPath);
        } else if (
            !history.state
            || String(history.state.chatId || '') !== normalizedChatId
            || String(history.state.messageId || '') !== focusMessageId
        ) {
            history.replaceState(routeState, '', targetPath);
        }
    };
    if (
        !preserveHistory
        && typeof window.realtimeCall?.isCallRouteActive === 'function'
        && window.realtimeCall.isCallRouteActive()
    ) {
        window.realtimeCall.deactivateCallRoute({ restorePath: false, stopActive: true });
    }

    // Reset scroll state (hide button, remove spacer) when loading any chat
    if (typeof resetChatScrollState === 'function') {
        resetChatScrollState();
    }

    const currentChatId = chatContainerEl.getAttribute('data-chat-id');
    const isSwitchingChats = currentChatId && currentChatId !== normalizedChatId;

    if (isSwitchingChats) {
        if (window.slidePresentationWidget && typeof window.slidePresentationWidget.hidePreviewPanel === 'function') {
            try {
                window.slidePresentationWidget.hidePreviewPanel();
            } catch (_) {}
        }
        if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.hidePreviewPanel === 'function') {
            try {
                window.canvasMarkdownWidget.hidePreviewPanel();
            } catch (_) {}
        }
        // Notes are message-scoped artifacts just like canvases. Close their
        // sidebar before the transcript changes so an artifact from the old
        // chat cannot remain visible beside the newly selected conversation.
        if (window.NotesToolSidebar && typeof window.NotesToolSidebar.hidePreviewPanel === 'function') {
            try {
                window.NotesToolSidebar.hidePreviewPanel();
            } catch (_) {}
        }
        if (window.deepResearchWidget && typeof window.deepResearchWidget.hidePreviewPanel === 'function') {
            try {
                window.deepResearchWidget.hidePreviewPanel();
            } catch (_) {}
        }
        if (window.latexPdfWidget && typeof window.latexPdfWidget.hidePreviewPanel === 'function') {
            try {
                window.latexPdfWidget.hidePreviewPanel();
            } catch (_) {}
        }
        if (typeof window.resetChatAttachmentsState === 'function') {
            try {
                window.resetChatAttachmentsState({ preserveSkills: false });
            } catch (_) {}
        }
        if (typeof window.resetGenerationUIState === 'function') {
            window.resetGenerationUIState({ clearActiveAttr: true });
        } else {
            window.currentGenerationId = null;
            window.pendingCancelGeneration = false;
            window.isGenerating = false;
            chatContainerEl.removeAttribute('data-active-generation');
            if (typeof window.endGenerationUI === 'function') {
                window.endGenerationUI();
            }
        }
    }

    if (
        currentChatId === normalizedChatId
        && !forceReload
        && !chatContainerEl.hasAttribute('data-chat-load-error')
    ) {
        // Re-selecting a chat that is already loading must not cancel and restart
        // the same request. The in-flight load reads pendingChatMessageFocus once
        // rendering completes, so bookmark navigation can still update its target.
        commitChatRoute();
        if (typeof showChatContainer === 'function') {
            showChatContainer({ skipCallTeardown: preserveHistory });
        }
        if (chatContainerEl.hasAttribute('data-chat-loading')) {
            return true;
        }
        chatContainerEl.removeAttribute('data-pending-chat');
        updateTabTitleIfActive(normalizedChatId);
        window.ChatAttention?.markRead(normalizedChatId);
        if (getFocusMessageId()) {
            focusChatMessage(getFocusMessageId());
        }
        return true;
    }

    // Cancel the previous chat's request before rebinding the shared transcript.
    // The token remains necessary because a response may already have left the
    // network stack when abort() is called.
    if (activeChatLoadController) {
        activeChatLoadController.abort();
    }
    const loadController = new AbortController();
    const loadToken = ++activeChatLoadToken;
    activeChatLoadController = loadController;

    chatContainerEl.removeAttribute('data-pending-chat');
    chatContainerEl.setAttribute('data-chat-id', normalizedChatId);
    updateTabTitleIfActive(normalizedChatId);
    if (typeof window.syncChatInputDraftContext === 'function') {
        window.syncChatInputDraftContext();
    }

    const isChatContainerStillBoundToChat = () => (
        loadToken === activeChatLoadToken
        && !loadController.signal.aborted
        && String(chatContainerEl.getAttribute('data-chat-id') || '') === normalizedChatId
    );

    const messagesContainer = document.getElementById('chatAreaContainer');
    if (!messagesContainer) {
        if (activeChatLoadController === loadController) {
            activeChatLoadController = null;
        }
        return false;
    }
    messagesContainer.innerHTML = '';

    // Navigation and the visible chat shell commit immediately. The backend
    // response now controls only transcript content, never whether the user sees
    // that their selection was accepted.
    commitChatRoute();
    setMainChatLoadStatus(
        'loading',
        sidebarDropdownT('chat_loading_messages', 'Loading chat messages…')
    );
    if (typeof showChatContainer === 'function') {
        showChatContainer({ skipCallTeardown: preserveHistory });
    }

    if (window.canvasFilesDropdown) window.canvasFilesDropdown.clearFiles();
    if (window.slidePresentationWidget && typeof window.slidePresentationWidget.reset === 'function') {
        try {
            window.slidePresentationWidget.reset();
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
    if (window.latexPdfWidget && typeof window.latexPdfWidget.reset === 'function') {
        try {
            window.latexPdfWidget.reset();
        } catch (_) {}
    }

    try {
        const response = await window.authedFetch(`/api/v1/chats/messages?chat_id=${encodeURIComponent(normalizedChatId)}`, {
            method: 'GET',
            signal: loadController.signal,
        });
        if (!isChatContainerStillBoundToChat()) {
            return false;
        }
        if (!response.ok) {
            let detailMessage = null;
            try {
                const errorData = await response.json();
                const detail = errorData?.detail;
                if (typeof detail === 'string' && detail.trim()) {
                    detailMessage = detail.trim();
                }
            } catch (_) {}

            if (!isChatContainerStillBoundToChat()) {
                return false;
            }

            const normalizedDetail = detailMessage ? detailMessage.toLowerCase() : '';
            if (
                response.status === 404 ||
                normalizedDetail.includes('chat not found') ||
                normalizedDetail.includes('not found')
            ) {
                setMainChatLoadStatus('idle');
                if (typeof window.showChatStartContainer === 'function') {
                    await window.showChatStartContainer({ skipHistory: true });
                } else {
                    chatContainerEl.removeAttribute('data-chat-id');
                }
                // Replace the invalid optimistic route instead of adding a second
                // history entry that would navigate back to the missing chat.
                history.replaceState(null, '', '/');
                if (detailMessage) notifyError(detailMessage);
                return false;
            }

            const fallbackMessage = sidebarDropdownTf(
                'chat_load_http_error_status',
                'HTTP error! status: {status}',
                { status: response.status }
            );
            setMainChatLoadStatus(
                'error',
                sidebarDropdownT('chat_load_messages_failed', 'Failed to load chat messages')
            );
            notifyError(detailMessage || fallbackMessage);
            return false;
        }
        const messages = await response.json();
        if (!isChatContainerStillBoundToChat()) {
            return false;
        }
        if (typeof window.renderChatTranscript === 'function') {
            window.renderChatTranscript(messages, {
                container: messagesContainer,
                clearContainer: false,
                trackAssistantVersions: true,
                readOnly: false,
                keepTrailingAssistantStreaming: already_streaming === true,
            });
        }

        if (!isChatContainerStillBoundToChat()) {
            return false;
        }
        const focusMessageId = getFocusMessageId();
        const focusedMessage = focusMessageId ? focusChatMessage(focusMessageId) : false;
        if (!focusedMessage) {
            scrollChatToBottom();
        }
        setMainChatLoadStatus('idle');
        // Only clear the durable unread marker after the transcript request
        // succeeded and the chat is actually visible to the user.
        window.ChatAttention?.markRead(normalizedChatId);
        if (!focusMessageId && typeof window.focusChatInput === 'function') {
            window.focusChatInput();
        }
        // After rendering messages, check if an ongoing stream is active and attach to it
        if (!already_streaming) {
            try {
                await checkAndAttachOngoingStream(normalizedChatId, {
                    signal: loadController.signal,
                    isCurrent: isChatContainerStillBoundToChat,
                });
            } catch (error) {
                if (error?.name !== 'AbortError') {
                    // Transcript loading succeeded, so a transient status-probe
                    // failure must not replace the rendered chat with an error.
                    console.warn('Failed to check for an ongoing chat stream:', error);
                }
            }
        }

        // A newer navigation may have aborted the status probe after this chat's
        // messages rendered. Report the obsolete load as unsuccessful so callers
        // do not apply its project/sidebar state over the newly selected chat.
        if (!isChatContainerStillBoundToChat()) {
            return false;
        }

        if (getFocusMessageId()) {
            focusChatMessage(getFocusMessageId(), { highlight: false, moveFocus: false });
        }

        requestAnimationFrame(() => {
            if (!isChatContainerStillBoundToChat()) {
                return;
            }
            const focusMessageId = getFocusMessageId();
            scrollChatToBottomAfterImagesLoad({
                focusMessageId,
                isCurrent: () => (
                    isChatContainerStillBoundToChat()
                    && getFocusMessageId() === focusMessageId
                ),
            });
            if (typeof updateVisibleCodeBlockHeights === 'function') {
                const refreshedContainer = document.getElementById('chatAreaContainer');
                if (refreshedContainer) {
                    updateVisibleCodeBlockHeights(refreshedContainer);
                }
            }
        });

        return true;

    } catch (error) {
        if (error?.name === 'AbortError' || !isChatContainerStillBoundToChat()) {
            return false;
        }
        setMainChatLoadStatus(
            'error',
            sidebarDropdownT('chat_load_messages_failed', 'Failed to load chat messages')
        );
        notifyError(sidebarDropdownT('chat_load_messages_failed', 'Failed to load chat messages'));
        console.error('Failed to load chat messages:', error);
        return false;
    } finally {
        if (activeChatLoadController === loadController && loadToken === activeChatLoadToken) {
            activeChatLoadController = null;
        }
    }
}

if (chatLoadRetryButtonEl) {
    chatLoadRetryButtonEl.addEventListener('click', () => {
        const chatContainer = document.getElementById('chatContainer');
        const chatId = String(chatContainer?.getAttribute('data-chat-id') || '').trim();
        if (!chatId) return;
        void loadChatView(chatId, false, { forceReload: true });
    });
}


function attachDropdownHandlers(menu, chat) {
    if (!menu) return;

    window.getDropdownPanelNavigator?.(menu)?.destroy();

    let mainPanel = menu.querySelector(':scope > [data-dropdown-panel="main"]');
    let mainContent = mainPanel?.querySelector(':scope > [data-dropdown-panel-content]');
    if (!mainPanel || !mainContent) {
        mainPanel = document.createElement('div');
        mainPanel.className = 'select-dropdown-panel is-active';
        mainPanel.dataset.dropdownPanel = 'main';
        mainContent = document.createElement('div');
        mainContent.className = 'select-dropdown-panel-scroll select-dropdown-panel-content';
        mainContent.dataset.dropdownPanelContent = '';
        Array.from(menu.children).forEach((child) => mainContent.appendChild(child));
        mainPanel.appendChild(mainContent);
        menu.appendChild(mainPanel);
    }

    // Remove any existing dynamic pin/unpin toggle to avoid duplicates
    menu.querySelectorAll('.pin-toggle-item').forEach((existing) => existing.remove());
    menu.querySelectorAll('.add-project-item').forEach((existing) => existing.remove());
    menu.querySelectorAll(':scope > .chat-project-dropdown-panel').forEach((existing) => existing.remove());

    const dropdown = menu;
    const item = document.createElement('div');
    item.className = 'select-dropdown-item pin-toggle-item';
    let panelNavigator = null;
    let loadProjectPanel = null;

    if (chat.pinned_position === null || typeof chat.pinned_position === 'undefined') {
        item.innerHTML = `<div class="select-dropdown-button pin-btn">${Icons.pin} <p data-i18n="sidebar_chat_action_pin">${sidebarDropdownT('sidebar_chat_action_pin', 'Pin chat')}</p></div>`;
        mainContent.insertBefore(item, mainContent.firstElementChild ?? null);
        const pinBtn = item.querySelector('.pin-btn');
        pinBtn?.addEventListener('click', async (e) => {
            e.stopPropagation();
            dropdown.classList.remove('open');
            try {
                await pinChat(chat.id, 1);
            } catch (err) {
                console.error('Pin chat failed', err);
            }
        });
    } else {
        item.innerHTML = `<div class="select-dropdown-button unpin-btn">${Icons.unpin} <p data-i18n="sidebar_chat_action_unpin">${sidebarDropdownT('sidebar_chat_action_unpin', 'Unpin chat')}</p></div>`;
        mainContent.insertBefore(item, mainContent.firstElementChild ?? null);
        const unpinBtn = item.querySelector('.unpin-btn');
        unpinBtn?.addEventListener('click', async (e) => {
            e.stopPropagation();
            dropdown.classList.remove('open');
            try {
                await unpinChat(chat.id);
            } catch (err) {
                console.error('Unpin chat failed', err);
            }
        });
    }

    if (areProjectsEnabled()) {
        const projectPanelId = `chat-project-panel-${chat.id}`;
        const projectItem = document.createElement('div');
        projectItem.className = 'select-dropdown-item add-project-item';
        projectItem.innerHTML = `
            <button type="button" class="select-dropdown-button add-project-btn" data-dropdown-open-panel="projects" aria-expanded="false" aria-controls="${escapeSidebarDropdownHtml(projectPanelId)}">
                ${Icons.addFolder}
                <span data-i18n="sidebar_chat_action_add_to_project">${sidebarDropdownT('sidebar_chat_action_add_to_project', 'Add to project')}</span>
            </button>
        `;
        item.insertAdjacentElement('afterend', projectItem);

        const projectPanel = document.createElement('div');
        projectPanel.className = 'select-dropdown-panel chat-project-dropdown-panel';
        projectPanel.id = projectPanelId;
        projectPanel.dataset.dropdownPanel = 'projects';
        projectPanel.setAttribute('aria-hidden', 'true');
        projectPanel.inert = true;
        projectPanel.innerHTML = `
            <header class="select-dropdown-panel-header" data-dropdown-panel-header>
                <button class="select-dropdown-panel-back" type="button" data-dropdown-panel-back aria-label="${escapeSidebarDropdownHtml(sidebarDropdownT('dropdown_back_aria', 'Back'))}" data-i18n-attr="aria-label:dropdown_back_aria"><span aria-hidden="true"></span></button>
                <div class="select-dropdown-panel-heading"><strong data-i18n="sidebar_chat_action_add_to_project">${escapeSidebarDropdownHtml(sidebarDropdownT('sidebar_chat_action_add_to_project', 'Add to project'))}</strong></div>
            </header>
            <div class="select-dropdown-panel-scroll select-dropdown-panel-content" data-dropdown-panel-content>
                <div class="select-dropdown-panel-status project-panel-placeholder" data-i18n="sidebar_chat_project_loading">${escapeSidebarDropdownHtml(sidebarDropdownT('sidebar_chat_project_loading', 'Loading projects...'))}</div>
            </div>
        `;
        menu.appendChild(projectPanel);

        const projectContent = projectPanel.querySelector('[data-dropdown-panel-content]');
        const placeholder = projectPanel.querySelector('.project-panel-placeholder');
        let panelLoaded = false;
        let panelError = false;
        const renderProjectOptions = (projects) => {
            projectContent.innerHTML = '';
            const options = [{ id: null, title: sidebarDropdownT('sidebar_chat_project_none', 'No project') }, ...projects.map((p) => ({
                id: p.id,
                title: p.title ?? sidebarDropdownT('sidebar_chat_project_untitled', 'Untitled project'),
            }))];
            const currentProjectId = chat.project_id ?? null;

            options.forEach((option) => {
                const row = document.createElement('button');
                row.type = 'button';
                row.className = 'select-dropdown-button';
                const titleI18nAttr = option.id === null ? ' data-i18n="sidebar_chat_project_none"' : '';
                row.innerHTML = `
                    <span${titleI18nAttr}>${escapeSidebarDropdownHtml(option.title)}</span>
                    ${currentProjectId === option.id ? Icons.check : ''}
                `;
                row.addEventListener('click', async (event) => {
                    event.stopPropagation();
                    dropdown.classList.remove('open');
                    panelNavigator?.reset({ focus: false });
                    try {
                        await updateChatProjectAssignment(chat.id, option.id);
                        chat.project_id = option.id || null;
                        const successMessage = option.id
                            ? sidebarDropdownTf('sidebar_chat_project_added', 'Chat added to "{project}"', { project: option.title })
                            : sidebarDropdownT('sidebar_chat_project_removed', 'Chat removed from project');
                        notifySuccess?.(successMessage);
                    } catch (err) {
                        console.error(err);
                        notifyError?.(err.message || sidebarDropdownT('sidebar_chat_project_update_failed', 'Failed to update project'));
                    }
                });
                projectContent.appendChild(row);
            });
            panelNavigator?.syncHeight('projects');
        };

        loadProjectPanel = async () => {
            if (panelLoaded || panelError) return;
            try {
                const projects = await fetchChatProjects();
                renderProjectOptions(projects);
                panelLoaded = true;
            } catch (err) {
                panelError = true;
                placeholder.textContent = sidebarDropdownT('sidebar_chat_project_load_failed', 'Failed to load projects');
                console.error(err);
                panelNavigator?.syncHeight('projects');
            }
        };

    }

    const dropdownRow = menu.closest('.sidebar-element');
    panelNavigator = window.createDropdownPanelNavigator?.({
        dropdown,
        maxHeight: () => Math.max(160, Math.min(420, window.innerHeight - 16)),
        onNavigate: ({ panelName }) => {
            if (panelName === 'projects') {
                void loadProjectPanel?.();
            }
        },
        onHeightChange: () => {
            if (dropdown.classList.contains('open') && dropdownRow && typeof positionChatDropdown === 'function') {
                requestAnimationFrame(() => positionChatDropdown(dropdown, dropdownRow));
            }
        },
    });
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





window.addEventListener('popstate', async (event) => {
    if (typeof window.SplitScreenManager?.syncFromURL === 'function') {
        const handledBySplitScreen = window.SplitScreenManager.syncFromURL();
        if (handledBySplitScreen) {
            return;
        }
    }

    if (window.SplitScreenManager?.active) {
        const canLeave = typeof window.SplitScreenManager.requestDisable === 'function'
            ? await window.SplitScreenManager.requestDisable({ skipLoadFallback: true })
            : (window.SplitScreenManager.disable({ skipLoadFallback: true }), true);
        if (!canLeave) return;
    }

    const stateChatId = event && event.state && event.state.chatId ? String(event.state.chatId) : null;
    const chatIdFromPath = extractChatIdFromPath(window.location.pathname);
    const chatId = stateChatId || chatIdFromPath;

    if (chatId) {
        const focusMessageId = extractChatMessageIdFromSearch(window.location.search);
        loadChatView(chatId, false, { focusMessageId, preserveHistory: true }).then((loaded) => {
            if (!loaded || typeof window.restoreProjectSidebarForChat !== 'function') {
                return;
            }
            return window.restoreProjectSidebarForChat(chatId);
        }).catch((error) => {
            console.error('Failed to load chat from history navigation or restore its project sidebar:', error);
        });
        return;
    }

    if (typeof window.handleAppRoute === 'function') {
        const handled = window.handleAppRoute(window.location.pathname);
        if (handled) {
            return;
        }
    }
    showChatContainer();
});

if (typeof window !== 'undefined') {
    window.loadChatView = loadChatView;
    window.cancelActiveChatLoad = cancelActiveChatLoad;
    window.buildChatRoute = buildChatRoute;
    window.extractChatMessageIdFromSearch = extractChatMessageIdFromSearch;
    window.focusChatMessage = focusChatMessage;
}
