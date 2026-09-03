const ASSISTANT_STREAM_REVEAL_RATE_PER_SECOND = 180;
const ASSISTANT_STREAM_REVEAL_CATCH_UP_RATIO = 0.12;
const ASSISTANT_STREAM_REVEAL_MAX_PER_FRAME = 80;
const ASSISTANT_STREAM_REVEAL_ABSOLUTE_MAX_PER_FRAME = 2000;
const ASSISTANT_STREAM_REVEAL_WORD_LOOKAHEAD = 8;
const ASSISTANT_STREAM_CARET_CYCLE_MS = 1100;
// Protocol metadata and notifications do not change transcript order, so they
// must not force a still-smoothing answer to jump to the end of its backlog.
const ASSISTANT_STREAM_NON_BOUNDARY_EVENT_TYPES = new Set([
    'c', 's', 'm_id', 'a_id', 'n_c', 'n_t', 't_g', 'regen', 'w', 'uf', 'r_f',
]);
const assistantStreamingContentStates = new Map();
let assistantStreamingRevealFrame = 0;
let assistantStreamingGraphemeSegmenter = null;
let assistantStreamingGraphemeSegmenterResolved = false;

function shouldReduceAssistantStreamingMotion() {
    try {
        return typeof window !== 'undefined'
            && typeof window.matchMedia === 'function'
            && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (_) {
        return false;
    }
}

function getAssistantStreamingGraphemeSegmenter() {
    if (assistantStreamingGraphemeSegmenterResolved) {
        return assistantStreamingGraphemeSegmenter;
    }
    assistantStreamingGraphemeSegmenterResolved = true;
    try {
        if (typeof Intl !== 'undefined' && typeof Intl.Segmenter === 'function') {
            assistantStreamingGraphemeSegmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
        }
    } catch (_) {
        assistantStreamingGraphemeSegmenter = null;
    }
    return assistantStreamingGraphemeSegmenter;
}

function isAssistantStreamingWordBoundary(segment) {
    return /\s|[.,!?;:\u2026\u3002\uff0c\uff01\uff1f\uff1b\uff1a]/.test(segment);
}

/**
 * Return a Unicode-grapheme-safe prefix close to the requested visual budget.
 * A small look-ahead keeps ordinary words together without delaying long words
 * or languages that do not use spaces.
 */
function takeAssistantStreamingTextPrefix(text, desiredSegments, lookahead = ASSISTANT_STREAM_REVEAL_WORD_LOOKAHEAD) {
    const source = String(text || '');
    if (!source) return '';

    const desired = Math.max(1, Math.trunc(Number(desiredSegments) || 1));
    const extraLimit = Math.max(0, Math.trunc(Number(lookahead) || 0));
    let segmentCount = 0;
    let extraCount = 0;
    let cutoff = 0;
    let shouldStop = false;

    const visit = (segment, index) => {
        segmentCount += 1;
        cutoff = index + segment.length;
        if (segmentCount < desired) return;
        if (isAssistantStreamingWordBoundary(segment)) {
            shouldStop = true;
            return;
        }
        if (segmentCount > desired) {
            extraCount += 1;
            if (extraCount >= extraLimit) {
                shouldStop = true;
            }
        } else if (extraLimit === 0) {
            shouldStop = true;
        }
    };

    const segmenter = getAssistantStreamingGraphemeSegmenter();
    if (!segmenter) {
        // Avoid showing a partially composed emoji or combining sequence in
        // older engines. They get an instant reveal instead of unsafe pacing.
        return source;
    }
    for (const part of segmenter.segment(source)) {
        visit(part.segment, part.index);
        if (shouldStop) break;
    }

    return source.slice(0, cutoff || source.length);
}

function isAssistantSmoothStreamingElement(element) {
    if (!element || typeof element.closest !== 'function') return false;
    const container = element.closest('.assistant-message-container');
    return container?.dataset?.isStreaming === 'true'
        && container.dataset.smoothStreaming === 'true';
}

function getAssistantStreamingReceivedContent(element) {
    const state = assistantStreamingContentStates.get(element);
    if (state) {
        return state.displayedContent + state.pendingText;
    }
    return String(element?.getAttribute?.('data-raw-content') || '');
}

function removeAssistantStreamingCaret(element) {
    if (!element) return;
    element.querySelectorAll?.('.assistant-stream-caret').forEach((caret) => caret.remove());
    if (element._assistantStreamingCaret) {
        element._assistantStreamingCaret.remove?.();
        delete element._assistantStreamingCaret;
    }
}

function clearAssistantStreamingElementDecoration(element) {
    if (!element) return;
    element.removeAttribute?.('data-streaming-reveal');
    removeAssistantStreamingCaret(element);
    if (typeof resetAssistantStreamingMarkdownState === 'function') {
        resetAssistantStreamingMarkdownState(element);
    }
}

function queueAssistantStreamingDisplayedContent(state, content) {
    if (!state?.element) return;
    const nextContent = String(content || '');
    state.displayedContent = nextContent;
    state.element.setAttribute('data-raw-content', nextContent);
    scheduleDebouncedRender(state.element.id, nextContent);
}

function revealAllPendingAssistantStreamingContent(state) {
    if (!state?.pendingText) return false;
    const nextContent = state.displayedContent + state.pendingText;
    state.pendingText = '';
    state.revealCredit = 0;
    queueAssistantStreamingDisplayedContent(state, nextContent);
    return true;
}

function drainAssistantStreamingContentState(state, now) {
    if (!state?.pendingText) return false;
    if (shouldReduceAssistantStreamingMotion()) {
        revealAllPendingAssistantStreamingContent(state);
        return false;
    }

    const elapsed = state.lastFrameAt
        ? Math.min(Math.max(now - state.lastFrameAt, 8), 50)
        : (1000 / 60);
    state.lastFrameAt = now;
    state.revealCredit += ASSISTANT_STREAM_REVEAL_RATE_PER_SECOND * (elapsed / 1000);
    const pacedBudget = Math.max(1, Math.floor(state.revealCredit));
    state.revealCredit = Math.max(0, state.revealCredit - pacedBudget);

    const backlog = state.pendingText.length;
    const catchUpBudget = Math.ceil(Math.max(0, backlog - 24) * ASSISTANT_STREAM_REVEAL_CATCH_UP_RATIO);
    // Bound every paint even for a huge buffered response. The adaptive cap
    // catches up quickly without turning one network burst into one long task.
    const maxFrameBudget = Math.min(
        ASSISTANT_STREAM_REVEAL_ABSOLUTE_MAX_PER_FRAME,
        ASSISTANT_STREAM_REVEAL_MAX_PER_FRAME + Math.ceil(backlog / 8),
    );
    const budget = Math.min(
        backlog,
        Math.max(pacedBudget, catchUpBudget),
        maxFrameBudget,
    );
    const revealed = takeAssistantStreamingTextPrefix(state.pendingText, budget);
    state.pendingText = state.pendingText.slice(revealed.length);
    queueAssistantStreamingDisplayedContent(state, state.displayedContent + revealed);
    return Boolean(state.pendingText);
}

function scheduleAssistantStreamingRevealFrame() {
    if (assistantStreamingRevealFrame) return;
    const run = (timestamp) => {
        assistantStreamingRevealFrame = 0;
        const now = Number.isFinite(timestamp)
            ? timestamp
            : (typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now());
        let hasPendingContent = false;
        Array.from(assistantStreamingContentStates.values()).forEach((state) => {
            if (!state?.element || state.element.isConnected === false) {
                if (state?.element?.id) pendingRenderQueue.delete(state.element.id);
                assistantStreamingContentStates.delete(state?.element);
                return;
            }
            const stateHasPendingContent = drainAssistantStreamingContentState(state, now);
            if (!stateHasPendingContent) {
                // Once caught up, the DOM attribute is the source of truth.
                // Releasing the strong Map reference prevents a stalled stream
                // from retaining a transcript that was later navigated away.
                assistantStreamingContentStates.delete(state.element);
            }
            hasPendingContent = stateHasPendingContent || hasPendingContent;
        });
        if (hasPendingContent) scheduleAssistantStreamingRevealFrame();
    };

    if (typeof requestAnimationFrame === 'function') {
        assistantStreamingRevealFrame = requestAnimationFrame(run);
    } else {
        run();
    }
}

function enqueueAssistantStreamingContent(element, delta) {
    if (!element) return;
    const addition = String(delta ?? '');
    if (!addition) return;

    const smoothStreaming = isAssistantSmoothStreamingElement(element);
    const reduceMotion = shouldReduceAssistantStreamingMotion();
    let state = assistantStreamingContentStates.get(element);

    if (!smoothStreaming || reduceMotion) {
        const receivedContent = state
            ? state.displayedContent + state.pendingText + addition
            : getAssistantStreamingReceivedContent(element) + addition;
        if (state) {
            assistantStreamingContentStates.delete(element);
        }
        clearAssistantStreamingElementDecoration(element);
        element.setAttribute('data-raw-content', receivedContent);
        scheduleDebouncedRender(element.id, receivedContent);
        return;
    }

    if (!state) {
        state = {
            element,
            displayedContent: String(element.getAttribute('data-raw-content') || ''),
            pendingText: '',
            lastFrameAt: 0,
            revealCredit: 0,
        };
        assistantStreamingContentStates.set(element, state);
    }
    state.pendingText += addition;
    element.setAttribute('data-streaming-reveal', 'true');
    scheduleAssistantStreamingRevealFrame();
}

function flushAssistantStreamingContentElement(element, { discard = false } = {}) {
    const state = assistantStreamingContentStates.get(element);
    if (!state) {
        const hasQueuedRender = Boolean(element?.id && pendingRenderQueue.has(element.id));
        if (discard) {
            if (hasQueuedRender) pendingRenderQueue.delete(element.id);
        } else if (hasQueuedRender) {
            const queuedContent = pendingRenderQueue.get(element.id);
            pendingRenderQueue.delete(element.id);
            clearAssistantStreamingElementDecoration(element);
            element.setAttribute('data-raw-content', queuedContent);
            renderAssistantMessageContent(element, queuedContent);
        }
        clearAssistantStreamingElementDecoration(element);
        return hasQueuedRender && !discard;
    }

    assistantStreamingContentStates.delete(element);
    if (element.id) pendingRenderQueue.delete(element.id);
    if (!discard) {
        const receivedContent = state.displayedContent + state.pendingText;
        state.pendingText = '';
        clearAssistantStreamingElementDecoration(element);
        element.setAttribute('data-raw-content', receivedContent);
        renderAssistantMessageContent(element, receivedContent);
    }
    clearAssistantStreamingElementDecoration(element);

    if (assistantStreamingContentStates.size === 0 && assistantStreamingRevealFrame) {
        if (typeof cancelAnimationFrame === 'function') {
            cancelAnimationFrame(assistantStreamingRevealFrame);
        }
        assistantStreamingRevealFrame = 0;
    }
    return true;
}

function assistantStreamingStateBelongsToContainer(state, container) {
    if (!state?.element || !container) return false;
    if (state.element === container) return true;
    if (typeof container.contains === 'function' && container.contains(state.element)) return true;
    return state.element.closest?.('.assistant-message-container') === container;
}

function flushAssistantStreamingContentInContainer(container, options = {}) {
    if (!container) return false;
    const selector = options.discard
        ? '.assistant-message-content'
        : '.assistant-message-content[data-streaming-reveal="true"]';
    const contentElements = new Set(
        Array.from(container.querySelectorAll?.(selector) || []),
    );
    Array.from(assistantStreamingContentStates.values()).forEach((state) => {
        if (assistantStreamingStateBelongsToContainer(state, container)) {
            contentElements.add(state.element);
        }
    });
    let flushed = false;
    contentElements.forEach((element) => {
        flushed = flushAssistantStreamingContentElement(element, options) || flushed;
    });
    return flushed;
}

function flushAssistantStreamingContentForMessage(messageId, transcriptRoot = null, options = {}) {
    const container = findStreamAssistantContainer(messageId, transcriptRoot);
    return flushAssistantStreamingContentInContainer(container, options);
}

function flushAssistantStreamingContentBeforeEvent(messageId, previousType, eventType, transcriptRoot = null) {
    if (
        !messageId
        || previousType !== 'c'
        || ASSISTANT_STREAM_NON_BOUNDARY_EVENT_TYPES.has(String(eventType || ''))
    ) {
        return false;
    }
    return flushAssistantStreamingContentForMessage(messageId, transcriptRoot);
}

function clearAssistantStreamingPresentation(container) {
    if (!container) return;
    delete container.dataset.smoothStreaming;
    container.querySelectorAll?.('.assistant-message-content[data-streaming-reveal="true"]').forEach((element) => {
        clearAssistantStreamingElementDecoration(element);
    });
    container.querySelectorAll?.('.assistant-stream-caret').forEach((caret) => caret.remove());
}

function getAssistantStreamingCommonTextPrefixLength(previousText, nextText) {
    const before = String(previousText || '');
    const after = String(nextText || '');
    const limit = Math.min(before.length, after.length);
    let offset = 0;
    while (offset < limit && before.charCodeAt(offset) === after.charCodeAt(offset)) {
        offset += 1;
    }
    if (offset > 0 && /[\uD800-\uDBFF]/.test(after.charAt(offset - 1))) {
        offset -= 1;
    }
    return offset;
}

function getAssistantStreamingTextNodes(roots) {
    if (typeof document === 'undefined' || typeof document.createTreeWalker !== 'function') return [];
    const normalizedRoots = Array.isArray(roots) ? roots : [roots];
    const showText = typeof NodeFilter !== 'undefined' ? NodeFilter.SHOW_TEXT : 4;
    const nodes = [];
    normalizedRoots.forEach((root) => {
        if (!root) return;
        if (root.nodeType === 3) {
            nodes.push(root);
            return;
        }
        const walker = document.createTreeWalker(root, showText);
        let node = walker.nextNode();
        while (node) {
            nodes.push(node);
            node = walker.nextNode();
        }
    });
    return nodes;
}

function getAssistantStreamingRootsText(roots) {
    const normalizedRoots = Array.isArray(roots) ? roots : [roots];
    return normalizedRoots.map((root) => String(root?.textContent || '')).join('');
}

function wrapAssistantStreamingTextSuffix(roots, fromOffset) {
    const nodes = getAssistantStreamingTextNodes(roots);
    if (!nodes.length) return;

    let position = 0;
    nodes.forEach((textNode) => {
        const value = String(textNode.textContent || '');
        const start = position;
        const end = start + value.length;
        position = end;
        if (end <= fromOffset || !textNode.parentNode) return;

        let target = textNode;
        if (fromOffset > start) {
            target = textNode.splitText(fromOffset - start);
        }
        if (!String(target.textContent || '').trim()) return;
        const parentElement = target.parentElement;
        if (parentElement?.closest?.('button, [aria-hidden="true"], .assistant-stream-caret')) return;

        const wrapper = document.createElement('span');
        wrapper.className = 'assistant-stream-text-arrival';
        target.parentNode.insertBefore(wrapper, target);
        wrapper.appendChild(target);
        wrapper.addEventListener('animationend', (event) => {
            if (event.target === wrapper && wrapper.parentNode) {
                wrapper.replaceWith(...wrapper.childNodes);
            }
        }, { once: true });
    });
}

function appendAssistantStreamingCaret(element, preferredRoots = null) {
    if (!element || typeof document === 'undefined') return;
    let caret = element._assistantStreamingCaret;
    if (!caret) {
        caret = element.querySelector?.('.assistant-stream-caret') || document.createElement('span');
        caret.className = 'assistant-stream-caret';
        caret.setAttribute('aria-hidden', 'true');
        element._assistantStreamingCaret = caret;
    }
    if (caret.style) {
        const now = typeof performance !== 'undefined' && typeof performance.now === 'function'
            ? performance.now()
            : Date.now();
        // Tail nodes are replaced as Markdown grows. Align every reattachment
        // to a shared clock so the pulse does not visibly restart each time.
        caret.style.animationDelay = `-${Math.round(now % ASSISTANT_STREAM_CARET_CYCLE_MS)}ms`;
    }

    const preferredNodes = getAssistantStreamingTextNodes(preferredRoots || element);
    const fallbackNodes = preferredNodes.length || !preferredRoots
        ? preferredNodes
        : getAssistantStreamingTextNodes(element);
    let lastTextNode = null;
    for (let index = fallbackNodes.length - 1; index >= 0; index -= 1) {
        const node = fallbackNodes[index];
        const parentElement = node.parentElement;
        if (
            String(node.textContent || '').trim().length
            && !parentElement?.closest?.('button, [aria-hidden="true"], .assistant-stream-caret')
        ) {
            lastTextNode = node;
            break;
        }
    }

    if (!lastTextNode?.parentNode) {
        element.appendChild(caret);
        return;
    }
    const link = lastTextNode.parentElement?.closest?.('a');
    if (link?.parentNode && element.contains(link)) {
        link.parentNode.insertBefore(caret, link.nextSibling);
        return;
    }
    const arrivalWrapper = lastTextNode.parentElement?.closest?.('.assistant-stream-text-arrival');
    if (arrivalWrapper?.parentNode) {
        arrivalWrapper.parentNode.insertBefore(caret, arrivalWrapper.nextSibling);
        return;
    }
    lastTextNode.parentNode.insertBefore(caret, lastTextNode.nextSibling);
}

/** Decorate only the newly visible suffix; existing text never re-animates. */
function decorateAssistantStreamingRender(
    element,
    previousRenderedText,
    { animateSuffix = true, roots = null, previousScopeText = null } = {},
) {
    if (!element) return;
    if (
        element.getAttribute('data-streaming-reveal') !== 'true'
        || !isAssistantSmoothStreamingElement(element)
        || shouldReduceAssistantStreamingMotion()
    ) {
        removeAssistantStreamingCaret(element);
        return;
    }

    const hasScopedRoots = Array.isArray(roots);
    const animationRoots = hasScopedRoots ? roots : element;
    const nextRenderedText = hasScopedRoots
        ? getAssistantStreamingRootsText(roots)
        : String(element.textContent || '');
    const previousText = String(previousScopeText ?? previousRenderedText ?? '');
    const commonPrefix = getAssistantStreamingCommonTextPrefixLength(previousText, nextRenderedText);
    const isAppendLike = !previousText
        || commonPrefix === previousText.length
        || commonPrefix >= Math.floor(previousText.length * 0.9);
    if (animateSuffix && nextRenderedText.length > commonPrefix && isAppendLike) {
        wrapAssistantStreamingTextSuffix(animationRoots, commonPrefix);
    }
    appendAssistantStreamingCaret(element, hasScopedRoots ? roots : null);
    if (typeof window !== 'undefined') {
        window.ChatScrollManager?.scheduleFollow?.(element);
    }
}

function appendAssistantContainer(messageId, options = {}) {
    const smoothStreaming = options.smoothStreaming === true
        || (options.smoothStreaming !== false && options.announce === true);
    // Check if an assistant container with this ID already exists
    const existingContainer = document.getElementById('a-' + messageId);
    if (existingContainer) {
        // Reuse existing container - update the global reference
        assistantMessageContainer = existingContainer;
        if (options.announce) {
            existingContainer.dataset.announceStreaming = 'true';
        }
        if (smoothStreaming) {
            existingContainer.dataset.smoothStreaming = 'true';
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
    if (smoothStreaming) {
        assistantMessageContainer.dataset.smoothStreaming = 'true';
    }

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
        const previousRenderedText = String(element.textContent || '');
        if (typeof resetAssistantStreamingMarkdownState === 'function') {
            resetAssistantStreamingMarkdownState(element);
        }
        element.innerHTML = '';
        element.textContent = raw;
        element.classList.remove('markdown-body');
        element.setAttribute('data-rendered-raw-content', raw);
        decorateAssistantStreamingRender(element, previousRenderedText);
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
    if (typeof flushAssistantStreamingContentInContainer === 'function') {
        flushAssistantStreamingContentInContainer(container);
    }
    cancelScheduledStreamingRender();
    flushPendingRenders();
    container.querySelectorAll([
        '.assistant-message-content[data-streaming-markdown-needs-finalize="true"]',
        '.thinking-step-content[data-streaming-markdown-needs-finalize="true"]',
    ].join(', ')).forEach((element) => {
        const raw = element.getAttribute('data-raw-content') || '';
        renderAssistantMessageContent(element, raw);
    });
    if (typeof clearAssistantStreamingPresentation === 'function') {
        clearAssistantStreamingPresentation(container);
    }
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
    if (assistantContainer.dataset.smoothStreaming === 'true') {
        assistantMessage.classList.add('assistant-message-stream-enter');
    }
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

        const currentContent = getAssistantStreamingReceivedContent(assistantMessageContent);
        const newContent = currentContent + content;
        if (isSlidePresentationStructureDump(newContent, messageMetadata)) {
            flushAssistantStreamingContentElement(assistantMessageContent, { discard: true });
            assistantMessageContent.parentElement?.remove();
            return Math.max(assistantContentCount - 1, 0);
        }
        enqueueAssistantStreamingContent(assistantMessageContent, content);
    } else {
        assistantContentCount++;
        const assistantMessageContainer = document.getElementById('a-' + messageId);
        const assistantMessage = document.createElement('div');
        assistantMessage.className = 'assistant-message';
        if (assistantMessageContainer?.dataset.smoothStreaming === 'true') {
            assistantMessage.classList.add('assistant-message-stream-enter');
        }
        appendBeforeAssistantList(assistantMessageContainer, assistantMessage);

        const assistantMessageContent = document.createElement('div');
        assistantMessageContent.id = 'a-' + assistantContentCount + '-' + messageId;
        assistantMessageContent.className = 'assistant-message-content';
        assistantMessageContent.setAttribute('aria-live', 'off');
        assistantMessage.appendChild(assistantMessageContent);

        if (isAssistantSmoothStreamingElement(assistantMessageContent) && !shouldReduceAssistantStreamingMotion()) {
            enqueueAssistantStreamingContent(assistantMessageContent, content);
        } else {
            renderAssistantMessageContent(assistantMessageContent, content);
        }
    }
    applyAssistantMessageAccessibility(document.getElementById('a-' + messageId), { messageId, streaming: true });
    return assistantContentCount;
}
