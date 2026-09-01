function appendAssistantContainer(messageId, options = {}) {
    // Check if an assistant container with this ID already exists
    const existingContainer = document.getElementById('a-' + messageId);
    if (existingContainer) {
        // Reuse existing container - update the global reference
        assistantMessageContainer = existingContainer;
        if (options.announce) {
            existingContainer.dataset.announceStreaming = 'true';
        }
        applyAssistantMessageAccessibility(existingContainer, { messageId, streaming: existingContainer.dataset.isStreaming === 'true' });
        return;
    }

    assistantMessageContainer = document.createElement('div');
    assistantMessageContainer.id = 'a-' + messageId;
    assistantMessageContainer.className = 'assistant-message-container';

    // Ensure downstream UI logic (regeneration/version buttons) has the metadata it expects
    assistantMessageContainer.dataset.referenceId = messageId;
    assistantMessageContainer.dataset.retryCount = '0';
    assistantMessageContainer.dataset.totalVersions = '1';
    assistantMessageContainer.dataset.isLatestVersion = 'true';
    assistantMessageContainer.dataset.hidden = 'false';
    assistantMessageContainer.dataset.isStreaming = 'true';
    assistantMessageContainer.dataset.announceStreaming = options.announce ? 'true' : 'false';

    const chatAreaContainer = document.getElementById('chatAreaContainer');
    const existingSpacer = chatAreaContainer?.querySelector('.dynamic-scroll-spacer');
    ensureTranscriptAccessibility(chatAreaContainer);
    applyAssistantMessageAccessibility(assistantMessageContainer, { messageId, streaming: true });
    if (existingSpacer && existingSpacer.parentElement === chatAreaContainer) {
        chatAreaContainer.insertBefore(assistantMessageContainer, existingSpacer);
    } else {
        chatAreaContainer.appendChild(assistantMessageContainer);
    }
    const scrollViewport = assistantMessageContainer.closest('.chat-area, .split-chat-area');
    window.ChatScrollManager?.bind?.(scrollViewport);

    if (options.announce) {
        announceChatMessage(getChatA11yText('chat_sr_response_started', 'Assistant response started'));
    }
}

function bindOptimisticMessageToServerMessage(localMessageId, serverMessageId) {
    const normalizedLocalId = String(localMessageId || '').trim();
    const normalizedServerId = String(serverMessageId || '').trim();
    if (!normalizedLocalId || !normalizedServerId) {
        return false;
    }

    // Subagent launchers are created before the server message ID is known.
    // Record the canonical ID now so a later transcript reconstruction can
    // remount the run without breaking the currently active stream.
    registerSubagentParentMessageAlias(normalizedLocalId, normalizedServerId);

    const userAnchor = document.getElementById(`u-${normalizedLocalId}`);
    if (userAnchor) {
        userAnchor.dataset.serverMessageId = normalizedServerId;
        delete userAnchor.dataset.optimisticMessage;
    }

    const userContainer = document.querySelector(`.user-message-container[data-user-message-id="${CSS.escape(normalizedLocalId)}"]`);
    if (userContainer) {
        userContainer.dataset.serverMessageId = normalizedServerId;
        delete userContainer.dataset.optimisticMessage;
        if (userContainer.__editState && typeof userContainer.__editState === 'object') {
            userContainer.__editState.serverMessageId = normalizedServerId;
        }
    }

    const assistantContainer = document.getElementById(`a-${normalizedLocalId}`);
    if (assistantContainer) {
        assistantContainer.dataset.serverReferenceId = normalizedServerId;
        assistantContainer.dataset.referenceId = normalizedServerId;
    }

    return true;
}

function bindAssistantContainerToServerMessage(localMessageId, serverMessageId, transcriptRoot = null) {
    const normalizedLocalId = String(localMessageId || '').trim();
    const normalizedServerId = String(serverMessageId || '').trim();
    if (!normalizedLocalId || !normalizedServerId) {
        return false;
    }

    const assistantContainer = transcriptRoot && typeof transcriptRoot.querySelector === 'function'
        ? transcriptRoot.querySelector(`#a-${CSS.escape(normalizedLocalId)}`)
        : document.getElementById(`a-${normalizedLocalId}`);
    if (!assistantContainer) {
        return false;
    }

    assistantContainer.dataset.assistantMessageId = normalizedServerId;
    delete assistantContainer.dataset.optimisticMessage;

    const branchButton = assistantContainer.querySelector('.assistant-branch-btn');
    if (branchButton) {
        branchButton.disabled = false;
    }

    return true;
}

function resolvePersistedAssistantMessageId(container, messageId) {
    if (!container) {
        return '';
    }

    const persistedMessageId = String(container.dataset.assistantMessageId || '').trim();
    if (persistedMessageId) {
        return persistedMessageId;
    }

    const normalizedMessageId = String(messageId || '').trim();
    const containerMessageId = String(container.id || '').replace(/^a-/, '').trim();
    const referenceId = String(container.dataset.referenceId || '').trim();
    const retryCount = parseInt(container.dataset.retryCount || '0', 10) || 0;
    const isReconstructedPersistedVersion = (
        normalizedMessageId
        && containerMessageId === normalizedMessageId
        && referenceId
        && referenceId !== normalizedMessageId
        && retryCount > 0
        && container.dataset.optimisticMessage !== 'true'
        && container.dataset.isStreaming !== 'true'
    );

    return isReconstructedPersistedVersion ? normalizedMessageId : '';
}




function shouldRenderAssistantMarkdown() {
    const stored = safeGetLocalStorageItem('render_assistant_messages_markdown');
    if (stored === 'true' || stored === '1') return true;
    if (stored === 'false' || stored === '0') return false;
    if (window.chatSetup && Object.prototype.hasOwnProperty.call(window.chatSetup, 'render_assistant_messages_markdown')) {
        return Boolean(window.chatSetup.render_assistant_messages_markdown);
    }
    return true;
}

function renderAssistantMessageContent(element, content) {
    if (!element) {
        return;
    }
    const raw = String(content ?? '');
    element.setAttribute('data-raw-content', raw);
    if (shouldRenderAssistantMarkdown()) {
        renderMarkdownContent(element, raw);
    } else {
        element.innerHTML = '';
        element.textContent = raw;
        element.classList.remove('markdown-body');
        element.setAttribute('data-rendered-raw-content', raw);
    }
}

function flushPendingRenders() {
    if (pendingRenderQueue.size === 0) return;
    streamingLastRenderAt = typeof performance !== 'undefined' && typeof performance.now === 'function'
        ? performance.now()
        : Date.now();
    const items = Array.from(pendingRenderQueue.entries());
    pendingRenderQueue.clear();
    items.forEach(([renderTarget, content]) => {
        const element = typeof renderTarget === 'string'
            ? document.getElementById(renderTarget)
            : renderTarget;
        // Object keys are used for thinking nodes, some of which intentionally
        // have no globally unique ID. Ignore a node removed by title splitting
        // before its queued render reaches the next paint.
        if (element && element.isConnected === false) {
            return;
        }
        if (element) {
            renderAssistantMessageContent(element, content);
        }
    });
}

/** Choose a lower render cadence as full-response Markdown work grows. */
function getStreamingRenderInterval(content) {
    const length = String(content || '').length;
    if (length >= 16000) return STREAMING_RENDER_LONG_INTERVAL_MS;
    if (length >= 6000) return STREAMING_RENDER_MEDIUM_INTERVAL_MS;
    return STREAMING_RENDER_MIN_INTERVAL_MS;
}

/** Queue one paint-aligned render without resetting a trailing debounce. */
function requestStreamingRenderFrame() {
    streamingRenderDebounceTimer = null;
    if (streamingRenderFrame) return;
    const flush = () => {
        streamingRenderFrame = 0;
        flushPendingRenders();
    };
    if (typeof requestAnimationFrame === 'function') {
        streamingRenderFrame = requestAnimationFrame(flush);
    } else {
        flush();
    }
}

/** Cancel timers before the final synchronous render. */
function cancelScheduledStreamingRender() {
    if (streamingRenderDebounceTimer) {
        clearTimeout(streamingRenderDebounceTimer);
        streamingRenderDebounceTimer = null;
    }
    if (streamingRenderFrame && typeof cancelAnimationFrame === 'function') {
        cancelAnimationFrame(streamingRenderFrame);
        streamingRenderFrame = 0;
    }
}

/** Run deferred Markdown enhancements once a response container is stable. */
function finalizeStreamingMarkdownInContainer(container) {
    if (!container) return;
    cancelScheduledStreamingRender();
    flushPendingRenders();
    container.querySelectorAll([
        '.assistant-message-content[data-streaming-markdown-needs-finalize="true"]',
        '.thinking-step-content[data-streaming-markdown-needs-finalize="true"]',
    ].join(', ')).forEach((element) => {
        const raw = element.getAttribute('data-raw-content') || '';
        renderAssistantMessageContent(element, raw);
    });
}

/**
 * Resolve one assistant response inside the transcript that owns its stream.
 *
 * Split-screen panels can temporarily contain message IDs that also exist in
 * another panel. Scoping the lookup to the supplied transcript prevents an
 * interrupted panel stream from finalizing the matching response elsewhere.
 */
function findStreamAssistantContainer(messageId, transcriptRoot = null) {
    const normalizedMessageId = String(messageId || '').trim();
    if (!normalizedMessageId) return null;

    const expectedId = `a-${normalizedMessageId}`;
    if (
        transcriptRoot?.classList?.contains('assistant-message-container')
        && transcriptRoot.id === expectedId
    ) {
        return transcriptRoot;
    }

    const root = transcriptRoot && typeof transcriptRoot.querySelectorAll === 'function'
        ? transcriptRoot
        : document;
    return Array.from(root.querySelectorAll('.assistant-message-container'))
        .find((candidate) => candidate.id === expectedId) || null;
}

/**
 * Fail a response when its transport closes without a structured terminal event.
 *
 * This deliberately does not call appendAssistantDone(): disconnection does
 * not prove a completed response and must not manufacture completion controls
 * or announcements. Explicit user cancellation uses
 * finalizeCancelledAssistantStream() below instead.
 */
function finalizeInterruptedAssistantStream(messageId, transcriptRoot = null) {
    const container = findStreamAssistantContainer(messageId, transcriptRoot);
    if (!container) return false;

    const wasStreaming = container.dataset.isStreaming === 'true';
    const hasDeferredMarkdown = Boolean(
        container.querySelector(
            '.assistant-message-content[data-streaming-markdown-needs-finalize="true"]'
        )
    );
    if (!wasStreaming && !hasDeferredMarkdown) {
        return false;
    }

    const wasCancelled = container.dataset.assistantTerminalState === 'cancelled';
    if (wasStreaming && !wasCancelled) {
        container.querySelectorAll('.assistant-thinking-loading').forEach((loadingBlock) => {
            loadingBlock.remove();
        });

        container.dataset.hasError = 'true';
        const errorMessage = getStreamText(
            'chat_connection_interrupted_retry',
            'Connection interrupted. Please try again.'
        );
        let errorDiv = container.querySelector('.assistant-message-error');
        if (!errorDiv) {
            errorDiv = document.createElement('div');
            errorDiv.className = 'assistant-message-error';
            errorDiv.setAttribute('role', 'alert');
            const listDiv = container.querySelector('.assistant-message-list');
            if (listDiv) {
                container.insertBefore(errorDiv, listDiv);
            } else {
                container.appendChild(errorDiv);
            }
        }
        errorDiv.textContent = errorMessage;
    }

    delete container.dataset.isStreaming;
    container.dataset.announceStreaming = 'false';
    finalizeStreamingMarkdownInContainer(container);
    applyAssistantMessageAccessibility(container, {
        messageId,
        streaming: false,
        hasError: container.dataset.hasError === 'true',
        terminalState: container.dataset.assistantTerminalState || null,
    });
    if (wasStreaming && !wasCancelled) {
        announceChatMessage(
            getChatA11yText('chat_sr_response_failed', 'Assistant response failed'),
            { assertive: true }
        );
    }
    window.ChatScrollManager?.endStream?.(container);
    return true;
}

/**
 * Finish the visible presentation of a response that the user stopped.
 *
 * Cancellation is a real terminal state, even though it is not a successful
 * model completion. The partial response should therefore become a normal,
 * usable transcript entry: finish deferred Markdown, stop active thinking
 * indicators, finalize every completed thinking heading, and build the same
 * action toolbar used by a completed response. Network interruptions continue
 * to use finalizeInterruptedAssistantStream() and do not receive this treatment.
 */
function finalizeCancelledAssistantStream(messageId, transcriptRoot = null) {
    const container = findStreamAssistantContainer(messageId, transcriptRoot);
    if (!container || container.dataset.cancelPresentationFinalized === 'true') {
        return false;
    }

    // A loading skeleton contains no model output and cannot be expanded after
    // cancellation. Remove it before collapsing the real thinking/tool blocks.
    container.querySelectorAll('.assistant-thinking-loading').forEach((loadingBlock) => {
        loadingBlock.remove();
    });

    container.dataset.assistantTerminalState = 'cancelled';
    const hasMeaningfulOutput = typeof assistantContainerHasMeaningfulOutput === 'function'
        ? assistantContainerHasMeaningfulOutput(container)
        : Array.from(container.children || []).some((child) => (
            child?.classList
            && !child.classList.contains('sr-only')
            && !child.classList.contains('assistant-message-list')
            && !child.classList.contains('assistant-message-error')
            && !child.classList.contains('assistant-thinking-loading')
        ));

    if (!hasMeaningfulOutput) {
        delete container.dataset.isStreaming;
        container.dataset.announceStreaming = 'false';
        window.ChatScrollManager?.endStream?.(container);
        container.remove();
        announceChatMessage(getChatA11yText('chat_sr_response_cancelled', 'Assistant response stopped'));
        return true;
    }

    // This is intentionally called before appendAssistantDone(). Besides
    // flushing Markdown it clears the completion announcement flag, so a user
    // cancellation is never announced as "Assistant response complete".
    finalizeInterruptedAssistantStream(messageId, transcriptRoot);
    finalizeThinkingBlocks(container);

    let storedMetadata = null;
    if (container.dataset.assistantMetadata) {
        try {
            storedMetadata = JSON.parse(container.dataset.assistantMetadata);
        } catch (_) {
            storedMetadata = container.dataset.assistantMetadata;
        }
    }

    // appendAssistantDone is the project's existing, idempotent presentation
    // builder for copy/branch/feedback/more/regenerate/version controls. Passing
    // the transcript root keeps duplicate IDs in split-screen panels scoped.
    appendAssistantDone(messageId, storedMetadata, null, transcriptRoot);
    container.dataset.cancelPresentationFinalized = 'true';
    announceChatMessage(getChatA11yText('chat_sr_response_cancelled', 'Assistant response stopped'));
    return true;
}

if (typeof window !== 'undefined') {
    window.finalizeInterruptedAssistantStream = finalizeInterruptedAssistantStream;
    window.finalizeCancelledAssistantStream = finalizeCancelledAssistantStream;
}

function scheduleDebouncedRender(elementId, content) {
    pendingRenderQueue.set(elementId, content);
    if (streamingRenderDebounceTimer || streamingRenderFrame) return;

    const now = typeof performance !== 'undefined' && typeof performance.now === 'function'
        ? performance.now()
        : Date.now();
    const remaining = Math.max(getStreamingRenderInterval(content) - (now - streamingLastRenderAt), 0);
    if (remaining <= 0) {
        requestStreamingRenderFrame();
        return;
    }
    streamingRenderDebounceTimer = setTimeout(requestStreamingRenderFrame, remaining);
}

/** Queue Markdown for a DOM node that may not have a globally unique ID. */
function scheduleDebouncedElementRender(element, content) {
    if (!element) return;
    pendingRenderQueue.set(element, content);
    if (streamingRenderDebounceTimer || streamingRenderFrame) return;

    const now = typeof performance !== 'undefined' && typeof performance.now === 'function'
        ? performance.now()
        : Date.now();
    const remaining = Math.max(getStreamingRenderInterval(content) - (now - streamingLastRenderAt), 0);
    if (remaining <= 0) {
        requestStreamingRenderFrame();
        return;
    }
    streamingRenderDebounceTimer = setTimeout(requestStreamingRenderFrame, remaining);
}

function isSlidePresentationStructureDump(content, messageMetadata = null) {
    if (content == null) return false;

    let parsed = content;
    if (typeof content === 'string') {
        const trimmed = content.trim();
        if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) {
            return false;
        }
        try {
            parsed = JSON.parse(trimmed);
        } catch (_) {
            return false;
        }
    }

    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return false;
    }

    const hasExplicitFlag =
        parsed.__internal_slide_dump === true ||
        messageMetadata?.isInternalSlideDump === true;
    if (!hasExplicitFlag) {
        return false;
    }

    const requiredStringKeys = ['id', 'number_of_slides', 'design', 'structure', 'title', 'language'];
    const hasRequiredShape = requiredStringKeys.every((key) => {
        const value = parsed[key];
        return typeof value === 'string' && value.trim().length > 0;
    });

    return hasRequiredShape;
}

function ensureAssistantContentNode(messageId, assistantContentCount) {
    const normalizedCount = Math.max(Number(assistantContentCount) || 0, 1);
    const nodeId = `a-${normalizedCount}-${messageId}`;
    let contentNode = document.getElementById(nodeId);
    if (contentNode) {
        return { node: contentNode, count: normalizedCount };
    }

    const assistantContainer = document.getElementById(`a-${messageId}`);
    if (!assistantContainer) {
        return { node: null, count: normalizedCount };
    }

    const assistantMessage = document.createElement('div');
    assistantMessage.className = 'assistant-message';
    appendBeforeAssistantList(assistantContainer, assistantMessage);

    contentNode = document.createElement('div');
    contentNode.id = nodeId;
    contentNode.className = 'assistant-message-content';
    contentNode.setAttribute('aria-live', 'off');
    assistantMessage.appendChild(contentNode);

    return { node: contentNode, count: normalizedCount };
}

function appendAssistantContent(messageId, content, last_appended_message_type, assistantContentCount, temp_reasoning_time, assistantReasoningCount, messageMetadata = null) {
    if (isSlidePresentationStructureDump(content, messageMetadata)) {
        return assistantContentCount;
    }
    if (last_appended_message_type == "r" || last_appended_message_type == "t") {
        appendAssistantReasoningFinish(messageId, temp_reasoning_time, assistantReasoningCount);
    }
    if (last_appended_message_type == "c") {
        const ensuredContent = ensureAssistantContentNode(messageId, assistantContentCount);
        const assistantMessageContent = ensuredContent.node;
        assistantContentCount = ensuredContent.count;
        if (!assistantMessageContent) {
            return assistantContentCount;
        }

        const currentContent = assistantMessageContent.getAttribute('data-raw-content') || '';
        const newContent = currentContent + content;
        if (isSlidePresentationStructureDump(newContent, messageMetadata)) {
            assistantMessageContent.parentElement?.remove();
            return Math.max(assistantContentCount - 1, 0);
        }
        assistantMessageContent.setAttribute('data-raw-content', newContent);
        scheduleDebouncedRender(assistantMessageContent.id, newContent);
    } else {
        assistantContentCount++;
        let assistantMessageContainer = document.getElementById('a-' + messageId);
        const assistantMessage = document.createElement('div');
        assistantMessage.className = 'assistant-message';
        appendBeforeAssistantList(assistantMessageContainer, assistantMessage);

        const assistantMessageContent = document.createElement('div');
        assistantMessageContent.id = 'a-' + assistantContentCount + '-' + messageId;
        assistantMessageContent.className = 'assistant-message-content';
        assistantMessageContent.setAttribute('aria-live', 'off');
        assistantMessage.appendChild(assistantMessageContent);

        renderAssistantMessageContent(assistantMessageContent, content);
    }
    applyAssistantMessageAccessibility(document.getElementById('a-' + messageId), { messageId, streaming: true });
    return assistantContentCount;
}
