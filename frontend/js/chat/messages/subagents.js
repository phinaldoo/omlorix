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

    window.ChatWorkspace?.remove(`subagent:${state.runId}`);
    window.ChatScrollManager?.endStream?.(state.transcriptScroll);
    state.launcher = null;
    state.transcriptPanel = null;
    state.transcriptScroll = null;
    state.transcriptChat = null;
    state.renderedEventCount = 0;
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
            transcriptPanel: null,
            transcriptScroll: null,
            scrollTop: 0,
            autoFollow: true,
            renderedEventCount: 0,
            transcriptChat: null,
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
    const title = state.launcher.querySelector('.canvas-markdown-result-title');
    const status = state.launcher.querySelector('.canvas-markdown-result-sub');
    const button = state.launcher.querySelector('.canvas-markdown-result-open-btn');
    const selected = window.ChatWorkspace?.isSelected(`subagent:${state.runId}`) || false;
    const titleText = getSubagentTitleText(state);
    const statusText = getSubagentStatusText(state.status);
    const actionText = selected
        ? getSubagentText('subagent_modal_close', 'Close Subagent transcript')
        : getSubagentText('subagent_open_aria', 'Open Subagent transcript');
    button.setAttribute('aria-expanded', String(selected));
    button.setAttribute('aria-controls', 'chat-workspace-panel');
    button.setAttribute('aria-label', `${actionText}: ${titleText} (${statusText})`);
    button.querySelector('.canvas-markdown-result-open-label').textContent = selected
        ? getSubagentText('chat_workspace_hide', 'Hide')
        : getSubagentText('subagent_open_button', 'Open');
    title.textContent = titleText;
    title.title = titleText;
    status.textContent = statusText;
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

    const launcher = document.createElement('div');
    launcher.className = 'assistant-widget subagent-launcher';
    launcher.dataset.runId = state.runId;
    const card = window.ChatResultCard.create({
        icon: Icons.sparkle,
        openLabel: getSubagentText('subagent_open_button', 'Open'),
    });
    launcher.appendChild(card);
    card.querySelector('.canvas-markdown-result-open-btn').addEventListener('click', () => toggleSubagentPanel(state));

    appendBeforeAssistantList(assistantContainer, launcher);
    state.launcher = launcher;
    registerSubagentPanel(state);
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

function resetSubagentTranscriptRenderState(state) {
    if (!state) return;
    state.assistantContentCount = 0;
    state.assistantReasoningCount = 0;
    state.lastAppendedMessageType = '';
    state.tempReasoningTime = null;
    state.hasRenderedAssistantText = false;
    if (state.transcriptChat) {
        state.transcriptChat.innerHTML = '';
        state.transcriptChat.dataset.isStreaming = String(state.status).toLowerCase() === 'running' ? 'true' : 'false';
        state.transcriptChat.dataset.announceStreaming = 'false';
        delete state.transcriptChat.dataset.smoothStreaming;
    }
}

function renderSubagentEventAsChat(state, event, isLive = false) {
    if (!state?.transcriptChat || !event) return;
    const eventName = event.eventName || 'event';
    const data = event.data || {};
    const raw = data.raw && typeof data.raw === 'object' ? data.raw : {};
    const syntheticMessageId = state.syntheticMessageId;

    if (isLive && state.transcriptChat.dataset.isStreaming === 'true') {
        state.transcriptChat.dataset.smoothStreaming = 'true';
    }
    if (
        eventName !== 'message_delta'
        && state.lastAppendedMessageType === 'c'
        && typeof flushAssistantStreamingContentForMessage === 'function'
    ) {
        flushAssistantStreamingContentForMessage(syntheticMessageId, state.transcriptChat);
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
            { autoOpen: false },
        );
        state.lastAppendedMessageType = 'wg';
        return;
    }

    if (eventName === 'error') {
        appendAssistantError(syntheticMessageId, data.message || data.content || getSubagentText('subagent_status_error', 'Error'), state.lastAppendedMessageType);
        state.lastAppendedMessageType = 'error';
        state.transcriptChat.dataset.isStreaming = 'false';
        finalizeStreamingMarkdownInContainer(state.transcriptChat);
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
        finalizeThinkingBlocks(state.transcriptChat);
        state.transcriptChat.dataset.isStreaming = 'false';
        finalizeStreamingMarkdownInContainer(state.transcriptChat);
        return;
    }

    if (eventName === 'cancelled') {
        finalizeThinkingBlocks(state.transcriptChat);
        state.transcriptChat.dataset.isStreaming = 'false';
        finalizeStreamingMarkdownInContainer(state.transcriptChat);
    }
}

function renderSubagentTranscriptUnpreserved(state) {
    if (!state?.transcriptChat) return;
    resetSubagentTranscriptRenderState(state);

    const renderableEvents = state.events.filter((event) => event.eventName !== 'start' && event.eventName !== 'stream' && event.eventName !== 'done');
    if (!renderableEvents.length) {
        const empty = document.createElement('div');
        empty.className = 'subagent-transcript-empty';
        empty.textContent = getSubagentText('subagent_modal_empty', 'The Subagent has not produced visible output yet.');
        state.transcriptChat.appendChild(empty);
        return;
    }

    renderableEvents.forEach((event) => renderSubagentEventAsChat(state, event, false));
}

/** Rebuild a persisted transcript without moving a manually detached viewport. */
function renderSubagentTranscript(state) {
    if (!state?.transcriptChat) return;
    const scrollManager = window.ChatScrollManager;
    if (scrollManager && typeof scrollManager.preserveDuringMutation === 'function') {
        scrollManager.preserveDuringMutation(state.transcriptChat, () => renderSubagentTranscriptUnpreserved(state));
        return;
    }
    renderSubagentTranscriptUnpreserved(state);
}

function registerSubagentPanel(state) {
    state.tabNumber ||= Math.max(0, ...[...subagentRunStates.values()].map((item) => item.tabNumber || 0)) + 1;
    window.ChatWorkspace.register({
        id: `subagent:${state.runId}`,
        tabbed: true,
        label: () => {
            return getSubagentText('subagent_tab_label', '{name} · {number}', {
                name: getSubagentDisplayName(state) || getSubagentText('subagent_title', 'Subagent'),
                number: state.tabNumber,
            });
        },
        status: () => getSubagentStatusText(state.status),
        create: () => {
            const panel = document.createElement('section');
            panel.className = 'subagent-transcript-panel';
            const scroll = document.createElement('div');
            scroll.className = 'subagent-transcript-scroll';
            scroll.tabIndex = 0;
            const chat = document.createElement('div');
            chat.id = `a-${state.syntheticMessageId}`;
            chat.className = 'assistant-message-container subagent-transcript-chat';
            chat.dataset.referenceId = state.syntheticMessageId;
            chat.dataset.announceStreaming = 'false';
            scroll.appendChild(chat);
            panel.append(scroll);
            state.transcriptPanel = panel;
            state.transcriptChat = chat;
            state.transcriptScroll = scroll;
            return panel;
        },
        onShow: () => {
            // Replay only missed events. Keeping existing nodes preserves tool
            // expansion, selections and the reading position across tab switches.
            if (!state.renderedEventCount || state.transcriptChat.querySelector('.subagent-transcript-empty')) {
                renderSubagentTranscript(state);
            } else {
                state.events.slice(state.renderedEventCount).forEach((event) => renderSubagentEventAsChat(state, event, false));
            }
            state.renderedEventCount = state.events.length;
            state.transcriptScroll.scrollTop = state.scrollTop;
            window.ChatScrollManager?.beginStream?.(state.transcriptScroll, { autoFollow: state.autoFollow });
            if (state.autoFollow) {
                if (!window.ChatScrollManager?.scrollToBottom?.(state.transcriptScroll)) {
                    state.transcriptScroll.scrollTop = state.transcriptScroll.scrollHeight;
                }
            }
        },
        onHide: () => {
            if (typeof flushAssistantStreamingContentForMessage === 'function') {
                flushAssistantStreamingContentForMessage(state.syntheticMessageId, state.transcriptChat);
            }
            state.scrollTop = state.transcriptScroll.scrollTop;
            state.autoFollow = window.ChatScrollManager?.isFollowing?.(state.transcriptScroll) ?? true;
            window.ChatScrollManager?.endStream?.(state.transcriptScroll);
        },
    });
}

function toggleSubagentPanel(state) {
    const id = `subagent:${state.runId}`;
    if (window.ChatWorkspace.isSelected(id)) window.ChatWorkspace.close();
    else window.ChatWorkspace.show(id, { focus: true, trigger: state.launcher.querySelector('.canvas-markdown-result-open-btn') });
}

document.addEventListener('chatWorkspace:changed', () => {
    subagentRunStates.forEach(updateSubagentLauncher);
});
document.addEventListener('i18n:updated', () => {
    subagentRunStates.forEach(updateSubagentLauncher);
});

// Transcript replacement, deletion, and split-screen navigation can all detach
// launchers. Remove their views after synchronous optimistic-ID rebinding ends.
if (typeof MutationObserver !== 'undefined') {
    new MutationObserver((mutations) => {
        const removedLaunchers = mutations.some((mutation) => [...mutation.removedNodes].some((node) =>
            node.nodeType === 1 && (node.matches('.subagent-launcher') || node.querySelector('.subagent-launcher'))));
        if (!removedLaunchers) return;
        subagentRunStates.forEach((state, id) => {
            if (state.launcher?.isConnected) return;
            releaseSubagentStateView(state);
            subagentRunStates.delete(id);
        });
    }).observe(document.body, { childList: true, subtree: true });
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
    if (state.transcriptChat && window.ChatWorkspace.isSelected(`subagent:${state.runId}`)) {
        if (state.transcriptChat.querySelector('.subagent-transcript-empty')) {
            renderSubagentTranscript(state);
        } else {
            renderSubagentEventAsChat(state, state.events[state.events.length - 1], true);
            const scroll = state.transcriptPanel?.querySelector('.subagent-transcript-scroll');
            if (scroll) {
                window.ChatScrollManager?.scheduleFollow?.(scroll);
            }
        }
    }
    state.renderedEventCount = state.transcriptChat && window.ChatWorkspace.isSelected(`subagent:${state.runId}`)
        ? state.events.length : state.renderedEventCount;
    window.ChatWorkspace.update(`subagent:${state.runId}`);
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
