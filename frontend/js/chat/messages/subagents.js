function getSubagentText(key, fallback, vars = null) {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars || undefined);
    }
    if (!vars || typeof vars !== 'object') {
        return fallback;
    }
    return String(fallback).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

const subagentRunStates = new Map();
let activeSubagentModalState = null;

function getSubagentStatusText(status) {
    const rawStatus = String(status || '').toLowerCase();
    if (rawStatus === 'completed') return getSubagentText('subagent_status_completed', 'Completed');
    if (rawStatus === 'error' || rawStatus === 'failed') return getSubagentText('subagent_status_error', 'Error');
    if (rawStatus === 'cancelled') return getSubagentText('subagent_status_cancelled', 'Cancelled');
    return getSubagentText('subagent_status_running', 'Running');
}

/**
 * Remove DOM references that belong to a transcript view which has already
 * been replaced. The recorded events remain on the state so reopening the same
 * run can render immediately from the persisted chat-message part.
 */
function releaseSubagentStateView(state) {
    if (!state) return;

    const overlay = state.modalOverlay;
    if (state.modalCloseTimer) {
        window.clearTimeout(state.modalCloseTimer);
        state.modalCloseTimer = null;
    }
    if (overlay?.isConnected) {
        overlay.remove();
        if (!document.querySelector('.subagent-modal-overlay')) {
            document.body?.classList.remove('modal-open');
        }
    }
    if (activeSubagentModalState === state) {
        activeSubagentModalState = null;
    }

    state.launcher = null;
    state.modalOverlay = null;
    state.modalDialog = null;
    state.modalChat = null;
    state.modalLastFocusedElement = null;
}

/**
 * Rebind a run from its optimistic client-side message ID to the persisted
 * server message ID once the old transcript DOM has been detached.
 *
 * Live stream events must continue targeting the optimistic container while it
 * is connected, so this deliberately never changes an active launcher.
 */
function rebindDetachedSubagentState(state, nextParentMessageId) {
    const normalizedNextId = String(nextParentMessageId || '').trim();
    if (!state || !normalizedNextId) return false;

    const currentParentMessageId = String(state.parentMessageId || '').trim();
    if (!currentParentMessageId) {
        state.parentMessageId = normalizedNextId;
        return true;
    }
    if (currentParentMessageId === normalizedNextId) {
        return false;
    }
    if (state.launcher?.isConnected) {
        return false;
    }

    // If the optimistic message was already associated with a server ID, only
    // accept that canonical ID. This prevents an unrelated transcript surface
    // from stealing a run state that happens to share the same run identifier.
    const persistedParentMessageId = String(state.persistedParentMessageId || '').trim();
    if (persistedParentMessageId && persistedParentMessageId !== normalizedNextId) {
        return false;
    }

    releaseSubagentStateView(state);
    state.parentMessageId = normalizedNextId;
    state.persistedParentMessageId = normalizedNextId;
    return true;
}

/**
 * Remember the server ID assigned to an optimistic message without disrupting
 * the still-active stream, whose DOM continues using the local ID until the
 * transcript is reconstructed.
 */
function registerSubagentParentMessageAlias(localMessageId, serverMessageId) {
    const normalizedLocalId = String(localMessageId || '').trim();
    const normalizedServerId = String(serverMessageId || '').trim();
    if (!normalizedLocalId || !normalizedServerId) return;

    subagentRunStates.forEach((state) => {
        if (String(state?.parentMessageId || '').trim() === normalizedLocalId) {
            state.persistedParentMessageId = normalizedServerId;
        }
    });
}

function getSubagentState(
    messageId,
    runId,
    {
        create = true,
        meta = null,
        rebindDetached = false,
    } = {},
) {
    const normalizedRunId = String(runId || '').trim();
    const normalizedMessageId = String(messageId || '').trim();
    if (!normalizedRunId || !normalizedMessageId) return null;

    let state = subagentRunStates.get(normalizedRunId);
    if (!state && create) {
        state = {
            runId: normalizedRunId,
            parentMessageId: normalizedMessageId,
            persistedParentMessageId: '',
            syntheticMessageId: `subagent-${normalizedRunId}`,
            events: [],
            status: 'running',
            modelName: '',
            modelId: '',
            agentId: '',
            launcher: null,
            modalOverlay: null,
            modalDialog: null,
            modalChat: null,
            modalLastFocusedElement: null,
            modalCloseTimer: null,
            assistantContentCount: 0,
            assistantReasoningCount: 0,
            lastAppendedMessageType: '',
            tempReasoningTime: null,
            hasRenderedAssistantText: false,
        };
        subagentRunStates.set(normalizedRunId, state);
    }
    if (!state) return null;
    if (!state.parentMessageId) {
        state.parentMessageId = normalizedMessageId;
    } else if (rebindDetached) {
        rebindDetachedSubagentState(state, normalizedMessageId);
    }
    updateSubagentStateMeta(state, meta || {});
    return state;
}

function updateSubagentStateMeta(state, data = {}) {
    if (!state || !data || typeof data !== 'object') return;
    const nextStatus = data.status || (data.error ? 'error' : '');
    if (nextStatus) state.status = String(nextStatus);
    state.modelName = data.model_name || state.modelName || '';
    state.modelId = data.model_id || state.modelId || '';
    state.agentId = data.agent_id || state.agentId || '';
}

function getSubagentDisplayName(state) {
    return state?.modelName || state?.modelId || state?.agentId || '';
}

function getSubagentTitleText(state) {
    const modelName = getSubagentDisplayName(state);
    return modelName
        ? getSubagentText('subagent_title_named', 'Subagent: {name}', { name: modelName })
        : getSubagentText('subagent_title', 'Subagent');
}

function updateSubagentLauncher(state) {
    if (!state?.launcher) return;
    const title = state.launcher.querySelector('.subagent-launcher-title');
    const status = state.launcher.querySelector('.subagent-launcher-status');
    const statusText = getSubagentStatusText(state.status);

    state.launcher.classList.toggle('is-completed', String(state.status).toLowerCase() === 'completed');
    state.launcher.classList.toggle('is-error', ['error', 'failed', 'cancelled'].includes(String(state.status).toLowerCase()));
    state.launcher.setAttribute('aria-label', getSubagentText('subagent_open_aria', 'Open Subagent transcript'));
    if (title) title.textContent = getSubagentTitleText(state);
    if (status) status.textContent = statusText;
}

function ensureSubagentLauncher(messageId, runId, { meta = null } = {}) {
    const state = getSubagentState(messageId, runId, { create: true, meta });
    if (!state) return null;
    const assistantContainer = document.getElementById(`a-${state.parentMessageId}`);
    if (!assistantContainer) return null;
    if (state.launcher?.isConnected) {
        if (state.launcher.parentElement !== assistantContainer) {
            if (typeof finalizeThinkingBlocks === 'function') {
                finalizeThinkingBlocks(assistantContainer);
            }
            appendBeforeAssistantList(assistantContainer, state.launcher);
        }
        updateSubagentLauncher(state);
        return state.launcher;
    }

    if (typeof finalizeThinkingBlocks === 'function') {
        finalizeThinkingBlocks(assistantContainer);
    }

    const launcher = document.createElement('button');
    launcher.type = 'button';
    launcher.className = 'subagent-launcher';
    launcher.dataset.runId = state.runId;

    const icon = document.createElement('span');
    icon.className = 'subagent-launcher-icon';
    icon.innerHTML = Icons.sparkle;
    launcher.appendChild(icon);

    const text = document.createElement('span');
    text.className = 'subagent-launcher-text';

    const title = document.createElement('span');
    title.className = 'subagent-launcher-title';
    text.appendChild(title);

    const metaRow = document.createElement('span');
    metaRow.className = 'subagent-launcher-meta';

    const status = document.createElement('span');
    status.className = 'subagent-launcher-status';
    metaRow.appendChild(status);

    text.appendChild(metaRow);
    launcher.appendChild(text);

    const expand = document.createElement('span');
    expand.className = 'subagent-launcher-expand';
    const expandLabel = document.createElement('span');
    expandLabel.textContent = getSubagentText('subagent_open_button', 'Open');
    expand.appendChild(expandLabel);

    // Keep the decorative arrow sourced from the shared icon registry so the
    // launcher stays visually consistent with the rest of the chat interface.
    const expandIcon = document.createElement('span');
    expandIcon.className = 'subagent-launcher-expand-icon';
    expandIcon.setAttribute('aria-hidden', 'true');
    expandIcon.innerHTML = Icons?.arrow_right || '';
    expand.appendChild(expandIcon);
    launcher.appendChild(expand);

    launcher.addEventListener('click', () => openSubagentModal(state));

    appendBeforeAssistantList(assistantContainer, launcher);
    state.launcher = launcher;
    updateSubagentLauncher(state);
    return launcher;
}

function normalizeSubagentEvent(eventName, data = {}) {
    return {
        eventName: String(eventName || 'event'),
        data: data && typeof data === 'object' ? data : {},
    };
}

function getSubagentStatesForParentMessage(messageId) {
    const normalizedMessageId = String(messageId || '').trim();
    if (!normalizedMessageId) return [];
    return Array.from(subagentRunStates.values())
        .filter((state) => String(state?.parentMessageId || '').trim() === normalizedMessageId)
        .sort((a, b) => {
            const aStart = a.events.find((event) => event.eventName === 'start')?.data?.started_at || '';
            const bStart = b.events.find((event) => event.eventName === 'start')?.data?.started_at || '';
            return String(aStart).localeCompare(String(bStart));
        });
}

function coerceSubagentMetricNumber(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim()) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : 0;
    }
    return 0;
}

function readFirstSubagentMetric(meta, keys) {
    if (!meta || typeof meta !== 'object') return 0;
    for (const key of keys) {
        if (Object.prototype.hasOwnProperty.call(meta, key)) {
            return coerceSubagentMetricNumber(meta[key]);
        }
    }
    return 0;
}

function extractSubagentTokenMeta(event) {
    if (!event || event.eventName !== 'done') return null;
    const raw = event?.data?.raw && typeof event.data.raw === 'object' ? event.data.raw : {};
    const meta = raw.c && typeof raw.c === 'object' ? raw.c : raw;
    return meta && typeof meta === 'object' ? meta : null;
}

function getSubagentTokenTotalsForMessage(messageId) {
    const totals = {
        input_tokens: 0,
        input_token_cached: 0,
        cache_write_tokens: 0,
        output_tokens: 0,
        reasoning_tokens: 0,
        total_tokens: 0,
    };

    getSubagentStatesForParentMessage(messageId).forEach((state) => {
        (state.events || []).forEach((event) => {
            const meta = extractSubagentTokenMeta(event);
            if (!meta) return;
            const inputTokens = readFirstSubagentMetric(meta, ['input_tokens']);
            const cachedInputTokens = readFirstSubagentMetric(meta, ['input_token_cached', 'cached_input_tokens', 'input_tokens_cached']);
            const cacheWriteTokens = readFirstSubagentMetric(meta, ['cache_write_tokens', 'input_token_cache_write']);
            const outputTokens = readFirstSubagentMetric(meta, ['output_tokens']);
            const reasoningTokens = readFirstSubagentMetric(meta, ['reasoning_tokens', 'thinking_tokens']);
            const totalTokens = readFirstSubagentMetric(meta, ['total_tokens']) || (inputTokens + outputTokens);

            totals.input_tokens += inputTokens;
            totals.input_token_cached += cachedInputTokens;
            totals.cache_write_tokens += cacheWriteTokens;
            totals.output_tokens += outputTokens;
            totals.reasoning_tokens += reasoningTokens;
            totals.total_tokens += totalTokens;
        });
    });

    return totals;
}

function hasPositiveSubagentTokenTotals(totals) {
    return Boolean(totals && Object.values(totals).some((value) => coerceSubagentMetricNumber(value) > 0));
}

function mergeSubagentTokenTotalsIntoMetadata(metadata, totals) {
    const output = metadata && typeof metadata === 'object' && !Array.isArray(metadata)
        ? { ...metadata }
        : {};
    if (!hasPositiveSubagentTokenTotals(totals)) return output;

    const addNumericField = (key, amount) => {
        const numericAmount = coerceSubagentMetricNumber(amount);
        if (!numericAmount) return;
        output[key] = coerceSubagentMetricNumber(output[key]) + numericAmount;
    };
    const addNumericAliasField = (keys, amount) => {
        const existingKey = keys.find((key) => Object.prototype.hasOwnProperty.call(output, key));
        addNumericField(existingKey || keys[0], amount);
    };

    addNumericField('input_tokens', totals.input_tokens);
    addNumericAliasField(['input_token_cached', 'cached_input_tokens', 'input_tokens_cached'], totals.input_token_cached);
    addNumericAliasField(['cache_write_tokens', 'input_token_cache_write'], totals.cache_write_tokens);
    addNumericField('output_tokens', totals.output_tokens);
    addNumericAliasField(['reasoning_tokens', 'thinking_tokens'], totals.reasoning_tokens);
    addNumericField('total_tokens', totals.total_tokens);
    return output;
}

function refreshAssistantStatsForMessage(messageId) {
    const container = document.getElementById(`a-${messageId}`);
    if (!container || container.dataset.isStreaming === 'true') return;

    let metadataPayload = null;
    const stored = container.dataset.assistantMetadata;
    if (stored) {
        try {
            metadataPayload = JSON.parse(stored);
        } catch (_) {
            metadataPayload = stored;
        }
    }
    appendAssistantDone(messageId, metadataPayload);
}

function resetSubagentModalRenderState(state) {
    if (!state) return;
    state.assistantContentCount = 0;
    state.assistantReasoningCount = 0;
    state.lastAppendedMessageType = '';
    state.tempReasoningTime = null;
    state.hasRenderedAssistantText = false;
    if (state.modalChat) {
        state.modalChat.innerHTML = '';
        state.modalChat.dataset.isStreaming = String(state.status).toLowerCase() === 'running' ? 'true' : 'false';
        state.modalChat.dataset.announceStreaming = 'false';
        delete state.modalChat.dataset.smoothStreaming;
    }
}

function renderSubagentEventAsChat(state, event, isLive = false) {
    if (!state?.modalChat || !event) return;
    const eventName = event.eventName || 'event';
    const data = event.data || {};
    const raw = data.raw && typeof data.raw === 'object' ? data.raw : {};
    const syntheticMessageId = state.syntheticMessageId;

    if (isLive && state.modalChat.dataset.isStreaming === 'true') {
        state.modalChat.dataset.smoothStreaming = 'true';
    }
    if (
        eventName !== 'message_delta'
        && state.lastAppendedMessageType === 'c'
        && typeof flushAssistantStreamingContentForMessage === 'function'
    ) {
        flushAssistantStreamingContentForMessage(syntheticMessageId, state.modalChat);
    }

    if (eventName === 'message_delta') {
        const content = data.content == null ? '' : String(data.content);
        if (!content) return;
        state.assistantContentCount = appendAssistantContent(
            syntheticMessageId,
            content,
            state.lastAppendedMessageType,
            state.assistantContentCount,
            state.tempReasoningTime,
            state.assistantReasoningCount
        );
        state.lastAppendedMessageType = 'c';
        state.hasRenderedAssistantText = true;
        return;
    }

    if (eventName === 'reasoning_delta') {
        const content = data.content == null ? '' : String(data.content);
        if (!content) return;
        state.assistantReasoningCount = appendAssistantReasoning(
            syntheticMessageId,
            content,
            state.lastAppendedMessageType,
            state.assistantReasoningCount
        );
        state.lastAppendedMessageType = 'r';
        return;
    }

    if (eventName === 'tool_call') {
        const descriptor = raw.payload?.d || raw.d || {};
        const toolName = typeof descriptor === 'string' ? descriptor : (descriptor.name || raw.name || '');
        const toolArgs = descriptor && typeof descriptor === 'object'
            ? (descriptor.args ?? raw.payload?.c ?? raw.c ?? null)
            : (raw.payload?.c ?? raw.c ?? null);
        state.assistantReasoningCount = appendAssistantTool(
            syntheticMessageId,
            state.lastAppendedMessageType,
            state.assistantReasoningCount,
            null,
            toolName,
            toolArgs,
            descriptor && typeof descriptor === 'object' ? descriptor : raw
        );
        state.lastAppendedMessageType = 't';
        return;
    }

    if (eventName === 'tool_delta') {
        const deltaUpdate = processAssistantToolDeltaStreamEvent(
            syntheticMessageId,
            state.lastAppendedMessageType,
            state.assistantReasoningCount,
            raw
        );
        state.assistantReasoningCount = deltaUpdate.assistantReasoningCount;
        state.lastAppendedMessageType = deltaUpdate.lastAppendedMessageType;
        return;
    }

    if (eventName === 'widget') {
        const widgetHtml = raw.c ?? '';
        if (!widgetHtml) return;
        appendAssistantWidget(
            syntheticMessageId,
            widgetHtml,
            raw.widget_type ?? 'unknown',
            state.lastAppendedMessageType,
            raw.meta ?? null,
            { autoOpen: isLive },
        );
        state.lastAppendedMessageType = 'wg';
        return;
    }

    if (eventName === 'error') {
        appendAssistantError(syntheticMessageId, data.message || data.content || getSubagentText('subagent_status_error', 'Error'), state.lastAppendedMessageType);
        state.lastAppendedMessageType = 'error';
        state.modalChat.dataset.isStreaming = 'false';
        finalizeStreamingMarkdownInContainer(state.modalChat);
        return;
    }

    if (eventName === 'complete') {
        const result = data.result || data.content || '';
        if (result && !state.hasRenderedAssistantText) {
            state.assistantContentCount = appendAssistantContent(
                syntheticMessageId,
                result,
                state.lastAppendedMessageType,
                state.assistantContentCount,
                state.tempReasoningTime,
                state.assistantReasoningCount
            );
            state.lastAppendedMessageType = 'c';
            state.hasRenderedAssistantText = true;
        }
        finalizeThinkingBlocks(state.modalChat);
        state.modalChat.dataset.isStreaming = 'false';
        finalizeStreamingMarkdownInContainer(state.modalChat);
        return;
    }

    if (eventName === 'cancelled') {
        finalizeThinkingBlocks(state.modalChat);
        state.modalChat.dataset.isStreaming = 'false';
        finalizeStreamingMarkdownInContainer(state.modalChat);
    }
}

function renderSubagentModalTranscriptUnpreserved(state) {
    if (!state?.modalChat) return;
    resetSubagentModalRenderState(state);

    const renderableEvents = state.events.filter((event) => event.eventName !== 'start' && event.eventName !== 'stream' && event.eventName !== 'done');
    if (!renderableEvents.length) {
        const empty = document.createElement('div');
        empty.className = 'subagent-modal-empty';
        empty.textContent = getSubagentText('subagent_modal_empty', 'The Subagent has not produced visible output yet.');
        state.modalChat.appendChild(empty);
        return;
    }

    renderableEvents.forEach((event) => renderSubagentEventAsChat(state, event, false));
}

/** Rebuild a persisted transcript without moving a manually detached viewport. */
function renderSubagentModalTranscript(state) {
    if (!state?.modalChat) return;
    const scrollManager = window.ChatScrollManager;
    if (scrollManager && typeof scrollManager.preserveDuringMutation === 'function') {
        scrollManager.preserveDuringMutation(state.modalChat, () => renderSubagentModalTranscriptUnpreserved(state));
        return;
    }
    renderSubagentModalTranscriptUnpreserved(state);
}

function updateSubagentModalHeader(state) {
    if (!state?.modalDialog) return;
    const title = state.modalDialog.querySelector('.subagent-modal-title');
    const status = state.modalDialog.querySelector('.subagent-modal-status');
    if (title) title.textContent = getSubagentTitleText(state);
    if (status) {
        status.textContent = getSubagentStatusText(state.status);
        status.classList.toggle('is-error', ['error', 'failed', 'cancelled'].includes(String(state.status).toLowerCase()));
        status.classList.toggle('is-completed', String(state.status).toLowerCase() === 'completed');
    }
}

function closeSubagentModal({ restoreFocus = true, state = activeSubagentModalState } = {}) {
    if (!state?.modalOverlay) return;
    const overlay = state.modalOverlay;
    const scroll = state.modalDialog?.querySelector('.subagent-modal-scroll');
    window.ChatScrollManager?.endStream?.(scroll);
    overlay.inert = true;
    overlay.setAttribute('aria-hidden', 'true');
    overlay.classList.add('is-closing');
    overlay.classList.remove('is-visible');
    const focusTarget = state.modalLastFocusedElement;
    if (state.modalCloseTimer) window.clearTimeout(state.modalCloseTimer);
    state.modalCloseTimer = window.setTimeout(() => {
        state.modalCloseTimer = null;
        if (overlay.parentElement) overlay.remove();
        if (!document.querySelector('.subagent-modal-overlay')) {
            document.body.classList.remove('modal-open');
        }
        state.modalOverlay = null;
        state.modalDialog = null;
        state.modalChat = null;
        state.modalLastFocusedElement = null;
        if (activeSubagentModalState === state) {
            activeSubagentModalState = null;
        }
        if (restoreFocus && focusTarget instanceof HTMLElement) {
            focusTarget.focus();
        }
    }, shouldReduceMotionForStreamMessages() ? 0 : 160);
}

function trapSubagentModalFocus(event, dialog) {
    if (event.key !== 'Tab' || !dialog) return;
    const focusable = Array.from(dialog.querySelectorAll(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )).filter((element) => !element.hidden && element.getClientRects().length > 0);
    if (!focusable.length) {
        event.preventDefault();
        dialog.focus({ preventScroll: true });
        return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
    }
}

function openSubagentModal(state) {
    if (!state) return;
    if (state.modalOverlay?.isConnected) {
        if (state.modalCloseTimer) {
            window.clearTimeout(state.modalCloseTimer);
            state.modalCloseTimer = null;
        }
        state.modalOverlay.inert = false;
        state.modalOverlay.setAttribute('aria-hidden', 'false');
        state.modalOverlay.classList.remove('is-closing');
        state.modalOverlay.classList.add('is-visible');
        updateSubagentModalHeader(state);
        renderSubagentModalTranscript(state);
        state.modalDialog?.querySelector('[data-subagent-close]')?.focus();
        activeSubagentModalState = state;
        return;
    }
    if (activeSubagentModalState && activeSubagentModalState !== state) {
        closeSubagentModal({ restoreFocus: false, state: activeSubagentModalState });
    }

    const titleId = `subagent-modal-title-${state.runId}`;
    const overlay = document.createElement('div');
    overlay.className = 'subagent-modal-overlay shared-modal-overlay';
    overlay.inert = true;
    overlay.setAttribute('aria-hidden', 'true');
    overlay.tabIndex = -1;
    overlay.dataset.runId = state.runId;
    overlay.addEventListener('click', (event) => {
        if (event.target === overlay) closeSubagentModal();
    });
    overlay.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeSubagentModal();
            return;
        }
        trapSubagentModalFocus(event, state.modalDialog);
    });

    const dialog = document.createElement('div');
    dialog.className = 'subagent-modal shared-modal shared-modal--large shared-modal--fixed';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-labelledby', titleId);
    dialog.tabIndex = -1;

    const header = document.createElement('div');
    header.className = 'subagent-modal-header shared-modal-header shared-modal-header--main';

    const heading = document.createElement('div');
    heading.className = 'subagent-modal-heading';
    const icon = document.createElement('span');
    icon.className = 'subagent-modal-icon';
    icon.innerHTML = Icons?.sparkles || Icons?.bot || '';
    heading.appendChild(icon);
    const titleBlock = document.createElement('div');
    titleBlock.className = 'subagent-modal-title-block';
    const title = document.createElement('h2');
    title.className = 'subagent-modal-title shared-modal-title';
    title.id = titleId;
    titleBlock.appendChild(title);
    const status = document.createElement('span');
    status.className = 'subagent-modal-status shared-modal-subtitle';
    titleBlock.appendChild(status);
    heading.appendChild(titleBlock);
    header.appendChild(heading);

    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'om-button shared-modal-close';
    closeButton.setAttribute('data-subagent-close', '');
    closeButton.setAttribute('aria-label', getSubagentText('subagent_modal_close', 'Close Subagent transcript'));
    closeButton.innerHTML = Icons.close;
    closeButton.addEventListener('click', () => closeSubagentModal());
    header.appendChild(closeButton);
    dialog.appendChild(header);

    const scroll = document.createElement('div');
    scroll.className = 'subagent-modal-scroll shared-modal-body';
    const chat = document.createElement('div');
    chat.id = `a-${state.syntheticMessageId}`;
    chat.className = 'assistant-message-container subagent-modal-chat';
    chat.dataset.referenceId = state.syntheticMessageId;
    chat.dataset.announceStreaming = 'false';
    scroll.appendChild(chat);
    dialog.appendChild(scroll);

    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    document.body.classList.add('modal-open');

    state.modalOverlay = overlay;
    state.modalDialog = dialog;
    state.modalChat = chat;
    state.modalLastFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    activeSubagentModalState = state;

    // A newly opened live transcript intentionally starts in follow mode. Any
    // wheel, touch, keyboard, or scrollbar gesture will detach it immediately.
    window.ChatScrollManager?.beginStream?.(scroll, { autoFollow: true });

    updateSubagentModalHeader(state);
    renderSubagentModalTranscript(state);
    requestAnimationFrame(() => {
        overlay.inert = false;
        overlay.setAttribute('aria-hidden', 'false');
        overlay.classList.add('is-visible');
        closeButton.focus();
        if (!window.ChatScrollManager?.scrollToBottom?.(scroll)) {
            scroll.scrollTop = scroll.scrollHeight;
        }
    });
}

function handleSubagentStreamEvent(obj, messageId) {
    const runId = obj?.run_id || obj?.data?.run_id || obj?.data?.subagent_run_id;
    if (!runId || !messageId) return;
    const eventName = obj.event || 'event';
    const data = obj.data && typeof obj.data === 'object' ? obj.data : {};
    const state = getSubagentState(messageId, runId, { create: true, meta: data });
    if (!state) return;

    if (eventName === 'start' || eventName === 'complete' || eventName === 'error' || eventName === 'cancelled') {
        updateSubagentStateMeta(state, data);
    }
    ensureSubagentLauncher(messageId, runId, { meta: data });
    state.events.push(normalizeSubagentEvent(eventName, data));
    updateSubagentLauncher(state);
    updateSubagentModalHeader(state);
    if (state.modalChat) {
        if (state.modalChat.querySelector('.subagent-modal-empty')) {
            renderSubagentModalTranscript(state);
        } else {
            renderSubagentEventAsChat(state, state.events[state.events.length - 1], true);
            const scroll = state.modalDialog?.querySelector('.subagent-modal-scroll');
            if (scroll) {
                window.ChatScrollManager?.scheduleFollow?.(scroll);
            }
        }
    }
    refreshAssistantStatsForMessage(messageId);
}

function renderPersistedSubagentBlock(messageId, meta = {}) {
    const run = meta.subagent && typeof meta.subagent === 'object' ? meta.subagent : null;
    const runId = run?.id;
    if (!runId) return false;
    const state = getSubagentState(messageId, runId, {
        create: true,
        rebindDetached: true,
        meta: {
            status: run.status || 'completed',
            model_id: run.model_id,
            agent_id: run.agent_id,
            model_name: run.meta?.model_name,
        },
    });
    if (!state) return false;
    state.events = (Array.isArray(run.events) ? run.events : []).map((event) => {
        const eventName = event?.type || event?.event_type || 'event';
        const raw = event?.raw || event?.meta || {};
        const eventContent = event?.content ?? (typeof raw?.d === 'string' ? raw.d : '');
        return normalizeSubagentEvent(eventName, {
            content: eventName === 'complete' ? '' : eventContent,
            result: eventName === 'complete' ? (run.result || eventContent) : '',
            raw,
            status: run.status,
        });
    });
    ensureSubagentLauncher(messageId, runId, { meta: run });
    updateSubagentLauncher(state);
    refreshAssistantStatsForMessage(messageId);
    return true;
}

window.handleSubagentStreamEvent = handleSubagentStreamEvent;
window.renderPersistedSubagentBlock = renderPersistedSubagentBlock;
