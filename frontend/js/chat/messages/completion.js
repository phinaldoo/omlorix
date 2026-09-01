async function branchFromAssistantMessage(container, messageId) {
    const persistedMessageId = resolvePersistedAssistantMessageId(container, messageId);
    if (!persistedMessageId) {
        notifyError(getStreamText('chat_branching_failed', 'Branching failed'));
        return false;
    }

    const params = new URLSearchParams({ message_id: persistedMessageId });
    const res = await window.authedFetch(`/api/v1/chats/branch?${params.toString()}`, {
        method: 'POST',
        body: '',
    });
    if (!res.ok) {
        notifyError(getStreamTextFormatted('chat_branch_http_error_status', 'HTTP {status}', { status: res.status }));
        return false;
    }

    const data = await res.json().catch(() => null);
    if (!data || data.status !== 'success') {
        notifyError(getStreamText('chat_branching_failed', 'Branching failed'));
        return false;
    }

    await initChatList();
    return true;
}

function applyAssistantTerminalMetadata(container, metadata) {
    if (!container) {
        return '';
    }

    const metadataStatus = metadata && typeof metadata === 'object'
        ? String(metadata.status || '').trim().toLowerCase()
        : '';
    if (metadataStatus === 'cancelled' || metadataStatus === 'canceled') {
        container.dataset.assistantTerminalState = 'cancelled';
    }

    const terminalState = String(container.dataset.assistantTerminalState || '').trim().toLowerCase();
    return terminalState === 'canceled' ? 'cancelled' : terminalState;
}

function syncAssistantCancelledStatus(container, cancelled) {
    if (!container) {
        return;
    }

    let status = container.querySelector('.assistant-response-cancelled-status');
    if (!cancelled) {
        status?.remove();
        return;
    }
    if (!status) {
        status = document.createElement('div');
        status.className = 'assistant-response-cancelled-status';
        status.setAttribute('aria-hidden', 'true');
        appendBeforeAssistantList(container, status);
    }
    status.textContent = getChatA11yText('chat_sr_response_cancelled_status', 'Response stopped');
}

function appendAssistantDone(messageId, metadata, regenerationInfo = null, transcriptRoot = null) {
    const container = findStreamAssistantContainer(messageId, transcriptRoot);
    if (!container) return;
    const terminalState = applyAssistantTerminalMetadata(container, metadata);
    const wasCancelled = terminalState === 'cancelled';
    const shouldAnnounceCompletion = !wasCancelled && container.dataset.announceStreaming === 'true' && container.dataset.isStreaming === 'true';
    const shouldAnnounceCancellation = wasCancelled && container.dataset.announceStreaming === 'true';
    const resolvedVersionInfo = regenerationInfo
        ? {
            current: (parseInt(regenerationInfo.retry_count || '0', 10) || 0) + 1,
            total: parseInt(regenerationInfo.total_versions || '1', 10) || 1,
        }
        : null;

    // Mark the DOM stable before flushing so the last render can run the
    // deferred Markdown enhancement pass. If no content was queued, rerender
    // only nodes explicitly marked as needing finalization.
    if (container.dataset.isStreaming) {
        delete container.dataset.isStreaming;
    }
    finalizeStreamingMarkdownInContainer(container);
    window.ChatScrollManager?.endStream?.(container);
    const hasMeaningfulOutput = typeof assistantContainerHasMeaningfulOutput === 'function'
        ? assistantContainerHasMeaningfulOutput(container)
        : true;
    if (wasCancelled && !hasMeaningfulOutput) {
        if (shouldAnnounceCancellation) {
            announceChatMessage(getChatA11yText('chat_sr_response_cancelled', 'Assistant response stopped'));
        }
        container.dataset.announceStreaming = 'false';
        container.remove();
        return;
    }
    syncAssistantCancelledStatus(container, wasCancelled);

    if (isChatViewReadOnly()) {
        const existingList = container.querySelector('.assistant-message-list');
        if (existingList) {
            existingList.remove();
        }
        applyAssistantMessageAccessibility(container, {
            messageId,
            streaming: false,
            hasError: container.dataset.hasError === 'true',
            terminalState,
            versionInfo: resolvedVersionInfo,
        });
        if (shouldAnnounceCancellation) {
            announceChatMessage(getChatA11yText('chat_sr_response_cancelled', 'Assistant response stopped'));
        }
        container.dataset.announceStreaming = 'false';
        return;
    }
    
    // Ensure baseline dataset attributes exist so UI helpers don't break on first render
    if (!container.dataset.referenceId) {
        container.dataset.referenceId = container.id?.startsWith('a-') ? container.id.slice(2) : '';
    }
    if (!container.dataset.retryCount) {
        container.dataset.retryCount = '0';
    }
    if (!container.dataset.totalVersions) {
        container.dataset.totalVersions = '1';
    }
    if (!container.dataset.isLatestVersion) {
        container.dataset.isLatestVersion = 'true';
    }
    if (!container.dataset.hidden) {
        container.dataset.hidden = 'false';
    }

    // Store regeneration info on the container for later use
    if (regenerationInfo) {
        container.dataset.retryCount = String(regenerationInfo.retry_count || 0);
        container.dataset.totalVersions = String(regenerationInfo.total_versions || 1);
        container.dataset.referenceId = regenerationInfo.reference_id || '';
        container.dataset.isLatestVersion = regenerationInfo.is_latest_version ? 'true' : 'false';
    }

    // Persist metadata so the button list can be rebuilt when switching versions
    if (metadata === null || typeof metadata === 'undefined') {
        delete container.dataset.assistantMetadata;
    } else {
        try {
            container.dataset.assistantMetadata =
                typeof metadata === 'string' ? metadata : JSON.stringify(metadata);
        } catch (_) {
            container.dataset.assistantMetadata = String(metadata);
        }
    }

    const visibleAssistantCopyContents = getVisibleAssistantCopyContentElements(container);
    const assistantMessageContent = visibleAssistantCopyContents.length
        ? visibleAssistantCopyContents[visibleAssistantCopyContents.length - 1]
        : null;

    let targetElement = assistantMessageContent ? assistantMessageContent.closest('.assistant-message') : null;
    if (!targetElement) {
        const thinkingContainers = container.querySelectorAll('.assistant-thinking');
        targetElement = thinkingContainers.length ? thinkingContainers[thinkingContainers.length - 1] : null;
    }
    if (!targetElement) {
        const errorBlocks = container.querySelectorAll('.assistant-message-error');
        targetElement = errorBlocks.length ? errorBlocks[errorBlocks.length - 1] : null;
    }
    if (!targetElement) {
        targetElement = container.lastElementChild || container;
    }

    // Assistant action visibility is no longer customizable per user. Intrinsic
    // actions remain available, while sensitive actions follow group policy.
    const shouldShowAssistantCopy = visibleAssistantCopyContents.length > 0;
    const shouldShowAssistantBranch = true;
    const shouldShowAssistantFeedback = getChatBooleanSetting('allow_rate_response', false);
    const shouldShowAssistantDelete = getChatBooleanSetting('allow_delete_messages', false);
    const regenerationAllowed = getChatBooleanSetting('allow_regenerate_response', false);
    // Metadata visibility remains a user preference. Read the value mirrored by
    // chat setup and the toggle handler, then fall back to the live user payload.
    const showAssistantMessageMetadataRaw = safeGetLocalStorageItem('show_assistant_message_metadata');
    const showAssistantMessageMetadata = showAssistantMessageMetadataRaw === null
        ? Boolean(window.chatSetup?.show_assistant_message_metadata)
        : showAssistantMessageMetadataRaw === 'true' || showAssistantMessageMetadataRaw === '1';
    const hasCitations = (() => {
        try {
            if (typeof window.extractCitationsFromMessage === 'function') {
                const citations = window.extractCitationsFromMessage(messageId);
                return citations && citations.length > 0;
            }
        } catch (e) {
            console.error('[Citations] Error checking citations:', e);
        }
        return false;
    })();

    const isPlainObject = (value) => value && typeof value === 'object' && !Array.isArray(value);
    const subagentTokenTotals = getSubagentTokenTotalsForMessage(messageId);
    const hasProviderMeta = isPlainObject(metadata) && Object.keys(metadata).length > 0;
    const hasSubagentTokenStats = hasPositiveSubagentTokenTotals(subagentTokenTotals);
    const metadataForStats = mergeSubagentTokenTotalsIntoMetadata(
        hasProviderMeta ? metadata : {},
        subagentTokenTotals
    );
    const hasMeta = showAssistantMessageMetadata && (hasProviderMeta || hasSubagentTokenStats);
    const totalVersions = parseInt(container.dataset.totalVersions || '1', 10);
    const shouldForceListForVersions = totalVersions > 1;
    const mayShowRegenerate = regenerationAllowed && canRegenerateAssistantMessage(container);
    const baseNeedList = shouldShowAssistantCopy || shouldShowAssistantBranch || shouldShowAssistantFeedback || shouldShowAssistantDelete || hasCitations || hasMeta || mayShowRegenerate;
    const needList = baseNeedList || shouldForceListForVersions;

    let listDiv = container.querySelector('.assistant-message-list');

    if (!needList) {
        if (listDiv) listDiv.remove();
        applyAssistantMessageAccessibility(container, {
            messageId,
            streaming: false,
            hasError: container.dataset.hasError === 'true',
            terminalState,
            versionInfo: resolvedVersionInfo,
        });
        if (shouldAnnounceCompletion) {
            announceChatMessage(getChatA11yText('chat_sr_response_complete', 'Assistant response complete'));
        } else if (shouldAnnounceCancellation) {
            announceChatMessage(getChatA11yText('chat_sr_response_cancelled', 'Assistant response stopped'));
        }
        container.dataset.announceStreaming = 'false';
        return;
    }

    if (!listDiv) {
        listDiv = ensureAssistantMessageList(container);
    }

    const finalizeTrailingThinkingBlocks = () => {
        const trailingThinkingBlocks = Array.from(container.querySelectorAll('.assistant-thinking'));
        if (!trailingThinkingBlocks.length) {
            return;
        }

        let encounteredNonThinking = false;
        for (let i = trailingThinkingBlocks.length - 1; i >= 0; i--) {
            const block = trailingThinkingBlocks[i];
            if (!block || encounteredNonThinking) {
                continue;
            }
            const nextSibling = block.nextElementSibling;
            if (nextSibling && !nextSibling.classList.contains('assistant-thinking')) {
                encounteredNonThinking = true;
                continue;
            }
            finalizeThinkingBlockHeader(block);
        }
    };

    const ensureListPlacement = () => {
        finalizeTrailingThinkingBlocks();
        const errorBlocks = container.querySelectorAll('.assistant-message-error');
        const lastErrorBlock = errorBlocks.length ? errorBlocks[errorBlocks.length - 1] : null;
        if (lastErrorBlock) {
            const errorNextSibling = lastErrorBlock.nextSibling;
            if (errorNextSibling !== listDiv) {
                container.insertBefore(listDiv, errorNextSibling);
            }
            return;
        }

        const findAnchorElement = () => {
            const children = Array.from(container.children);
            for (let i = children.length - 1; i >= 0; i--) {
                const child = children[i];
                if (!child || child === listDiv) continue;
                return child;
            }
            return container.lastElementChild || null;
        };

        const anchorElement = findAnchorElement();
        if (!anchorElement) {
            container.appendChild(listDiv);
            return;
        }

        const anchorNextSibling = anchorElement.nextSibling;
        if (anchorNextSibling !== listDiv) {
            if (anchorNextSibling) {
                container.insertBefore(listDiv, anchorNextSibling);
            } else {
                container.appendChild(listDiv);
            }
        }
    };

    ensureListPlacement();

    const insertBeforeStats = (element) => {
        const statsEl = listDiv.querySelector('.assistant-message-stats');
        if (statsEl) {
            listDiv.insertBefore(element, statsEl);
        } else {
            listDiv.appendChild(element);
        }
    };

    let copyBtn = listDiv.querySelector('.assistant-copy-btn');
    if (shouldShowAssistantCopy) {
        if (!copyBtn) {
            copyBtn = document.createElement('button');
            copyBtn.className = 'assistant-message-list-button assistant-copy-btn';
            copyBtn.type = 'button';
            copyBtn.setAttribute('aria-label', getChatA11yText('chat_sr_copy_user_message', 'Copy message'));
            copyBtn.title = getChatA11yText('chat_sr_copy_user_message', 'Copy message');
            ensureListPlacement();
            insertBeforeStats(copyBtn);
        }
        if (typeof Icons.copy !== 'undefined') copyBtn.innerHTML = Icons.copy;
        if (!copyBtn.dataset.listenerAttached) {
            copyBtn.dataset.listenerAttached = 'true';
            copyBtn.addEventListener('click', async (e) => {
                e.preventDefault();
                e.stopPropagation();

                const textToCopy = getAssistantCopyText(container);

                try {
                    await writeTextToClipboardWithFallback(appendComplianceWatermarkIfNeeded(textToCopy));
                    reportChatCopyFeedback({
                        success: true,
                        key: 'chat_copy_message_success',
                        fallback: 'Message copied to clipboard.',
                    });

                    const originalHTML = copyBtn.innerHTML;
                    if (typeof Icons.check !== 'undefined') copyBtn.innerHTML = Icons.check;

                    if (!shouldReduceMotionForStreamMessages()) {
                        try {
                            copyBtn.animate(
                                [
                                    { transform: 'scale(1)' },
                                    { transform: 'scale(1.15)' },
                                    { transform: 'scale(1)' }
                                ],
                                { duration: 300, easing: 'ease-out' }
                            );
                        } catch (_) { /* ignore */ }
                    }

                    copyBtn.disabled = true;
                    setTimeout(() => {
                        copyBtn.innerHTML = originalHTML;
                        copyBtn.disabled = false;
                    }, 3000);
                } catch (err) {
                    console.error('Copy failed:', err);
                    reportChatCopyFeedback({
                        success: false,
                        key: 'chat_copy_message_error',
                        fallback: 'Failed to copy message.',
                    });
                    copyBtn.disabled = false;
                }
            });
        }
    } else if (copyBtn) {
        copyBtn.remove();
        copyBtn = null;
    }

    let citationsBtn = listDiv.querySelector('.assistant-citations-btn');
    if (hasCitations) {
        if (!citationsBtn) {
            citationsBtn = document.createElement('button');
            citationsBtn.className = 'assistant-message-list-button assistant-citations-btn';
            citationsBtn.type = 'button';
            citationsBtn.setAttribute('aria-label', getStreamText('chat_citations_show_sources', 'Show sources'));
            citationsBtn.title = getStreamText('chat_citations_show_sources', 'Show sources');
            insertBeforeStats(citationsBtn);
        }
        const citationsIcon = Icons.citations;
        citationsBtn.innerHTML = citationsIcon;
        if (!citationsBtn.dataset.listenerAttached) {
            citationsBtn.dataset.listenerAttached = 'true';
            citationsBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                if (typeof window.showCitationsForMessage === 'function') {
                    window.showCitationsForMessage(messageId);
                }
            });
        }
    } else if (citationsBtn) {
        citationsBtn.remove();
        citationsBtn = null;
    }

    let branchBtn = listDiv.querySelector('.assistant-branch-btn');
    if (shouldShowAssistantBranch) {
        if (!branchBtn) {
            branchBtn = document.createElement('button');
            branchBtn.className = 'assistant-message-list-button assistant-branch-btn';
            branchBtn.type = 'button';
            branchBtn.setAttribute('aria-label', getStreamText('chat_branch_from_message', 'Branch chat from this message'));
            branchBtn.title = getStreamText('chat_branch_chat', 'Branch chat');
            insertBeforeStats(branchBtn);
        }
        if (typeof Icons.branch !== 'undefined') branchBtn.innerHTML = Icons.branch;
        branchBtn.disabled = !resolvePersistedAssistantMessageId(container, messageId);
        if (!branchBtn.dataset.listenerAttached) {
            branchBtn.dataset.listenerAttached = 'true';
            branchBtn.addEventListener('click', async (e) => {
                e.preventDefault();
                e.stopPropagation();

                const originalBranchHTML = branchBtn.innerHTML;
                const spinner = Icons.loading_circle;

                try {
                    branchBtn.disabled = true;
                    branchBtn.innerHTML = spinner;

                    await branchFromAssistantMessage(container, messageId);
                } catch (err) {
                    console.error('Branch chat failed:', err);
                    notifyError?.(getStreamText('chat_branch_failed', 'Failed to branch chat'));
                } finally {
                    branchBtn.disabled = !resolvePersistedAssistantMessageId(container, messageId);
                    branchBtn.innerHTML = originalBranchHTML;
                }
            });
        }
    } else if (branchBtn) {
        branchBtn.remove();
        branchBtn = null;
    }

    if (shouldShowAssistantFeedback) {
        const initialFeedbackReaction = (() => {
            if (metadata?.feedback?.reaction) return metadata.feedback.reaction;
            if (metadata?.feedback_reaction) return metadata.feedback_reaction;
            if (listDiv.dataset.selectedFeedbackReaction) return listDiv.dataset.selectedFeedbackReaction;
            return null;
        })();

        ensureAssistantFeedbackControls({
            listDiv,
            insertBeforeStats,
            messageId,
            initialReaction: initialFeedbackReaction,
        });
    } else {
        const existingControls = listDiv.querySelector('.assistant-feedback-controls');
        if (existingControls) {
            existingControls.remove();
        }
    }

    // More menu (three-dot) with bookmark and delete actions
    ensureAssistantMoreMenu({
        listDiv,
        insertBeforeStats,
        messageId,
        container,
        isBookmarked: container.dataset.bookmarked === 'true',
        showDelete: shouldShowAssistantDelete,
    });

    // Regenerate button - only show for latest message within limit
    updateAssistantRegenerateButton(container, listDiv, messageId);

    // Version switcher - show when there are multiple versions
    updateAssistantVersionSwitcher(container);

    refreshAssistantRegenerateButtons();
    applyAssistantMessageAccessibility(container, {
        messageId,
        streaming: false,
        hasError: container.dataset.hasError === 'true',
        terminalState,
        versionInfo: resolvedVersionInfo,
    });
    if (shouldAnnounceCompletion) {
        announceChatMessage(getChatA11yText('chat_sr_response_complete', 'Assistant response complete'));
    } else if (shouldAnnounceCancellation) {
        announceChatMessage(getChatA11yText('chat_sr_response_cancelled', 'Assistant response stopped'));
    }
    container.dataset.announceStreaming = 'false';

    if (typeof window.finalizeCodeBlockPreviewState === 'function') {
        try {
            window.finalizeCodeBlockPreviewState(container);
        } catch (error) {
            console.error('Failed to finalize code block preview state', error);
        }
    }

    const existingStats = listDiv.querySelector('.assistant-message-stats');

    if (!hasMeta) {
        if (existingStats) existingStats.remove();
        if (!shouldForceListForVersions && !shouldShowAssistantCopy && !shouldShowAssistantBranch && listDiv.childElementCount === 0) {
            listDiv.remove();
        }
        return;
    }

    const statsIconSVG = Icons.info;
    const tooltipId = 'assistant-stats-tooltip-' + messageId;

    const ensureStatsContainer = () => {
        if (existingStats) {
            if (!existingStats.dataset.tooltipId) {
                existingStats.dataset.tooltipId = tooltipId;
            }
            return existingStats;
        }

        const containerDiv = document.createElement('div');
        containerDiv.className = 'tooltip-container assistant-message-stats';
        containerDiv.dataset.tooltipId = tooltipId;

        const triggerWrapper = document.createElement('div');
        triggerWrapper.className = 'tooltip-content';

        const triggerButton = document.createElement('button');
        triggerButton.className = 'assistant-message-list-button assistant-stats-btn';
        triggerButton.type = 'button';
        triggerButton.setAttribute('aria-label', getStreamText('chat_generation_statistics_show', 'Show generation statistics'));
        triggerButton.innerHTML = statsIconSVG;

        triggerWrapper.appendChild(triggerButton);
        containerDiv.appendChild(triggerWrapper);

        const tooltip = document.createElement('div');
        tooltip.className = 'tooltip';
        tooltip.id = tooltipId;

        const tooltipContent = document.createElement('div');
        tooltipContent.className = 'tooltip-content';

        tooltip.appendChild(tooltipContent);
        containerDiv.appendChild(tooltip);

        containerDiv._tooltipElement = tooltip;

        listDiv.appendChild(containerDiv);

        return containerDiv;
    };

    const statsContainer = ensureStatsContainer();
    if (statsContainer && typeof window.setupTooltip === 'function') {
        window.setupTooltip(statsContainer);
    }
    let tooltipElement = statsContainer._tooltipElement || null;
    if (!tooltipElement) {
        const resolvedId = statsContainer.dataset.tooltipId || tooltipId;
        tooltipElement = resolvedId ? document.getElementById(resolvedId) : null;
    }
    if (!tooltipElement) {
        tooltipElement = statsContainer.querySelector('.tooltip');
    }
    if (!tooltipElement) return;
    if (!tooltipElement.id) {
        tooltipElement.id = statsContainer.dataset.tooltipId || tooltipId;
    }
    statsContainer._tooltipElement = tooltipElement;
    const tooltipContentEl = tooltipElement.querySelector('.tooltip-content');
    if (!tooltipContentEl) return;

    tooltipContentEl.innerHTML = '';

    const toNumber = (value) => {
        if (typeof value === 'number') return value;
        if (typeof value === 'string' && value.trim() !== '') {
            const parsed = Number(value);
            return Number.isNaN(parsed) ? null : parsed;
        }
        return null;
    };

    const formatInteger = (value) => {
        const numeric = toNumber(value);
        if (numeric === null) return String(value);
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(Math.round(numeric));
    };

    const formatDecimal = (value) => {
        const numeric = toNumber(value);
        if (numeric === null) return String(value);
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(numeric);
    };

    const formatSeconds = (value) => {
        const numeric = toNumber(value);
        if (numeric === null) return String(value);
        const formatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: numeric >= 10 ? 1 : 2 });
        return `${formatter.format(numeric)}s`;
    };

    const formatText = (value) => {
        if (value === null || value === undefined) return '';
        return String(value);
    };

    const parseMetadataTimestamp = (rawValue) => {
        if (rawValue instanceof Date && !Number.isNaN(rawValue.getTime())) {
            return rawValue;
        }
        if (typeof rawValue === 'number' && Number.isFinite(rawValue)) {
            const millis = rawValue > 1e12 ? rawValue : rawValue * 1000;
            const numericDate = new Date(millis);
            return Number.isNaN(numericDate.getTime()) ? null : numericDate;
        }
        if (typeof rawValue !== 'string') {
            return null;
        }
        const trimmed = rawValue.trim();
        if (!trimmed) return null;

        const candidates = [];
        if (/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(:\d{2})?$/.test(trimmed)) {
            const base = trimmed.replace(' ', 'T');
            const withSeconds = base.length === 16 ? `${base}:00` : base;
            candidates.push(`${withSeconds}Z`, withSeconds);
        } else if (!trimmed.endsWith('Z') && trimmed.includes('T')) {
            candidates.push(`${trimmed}Z`, trimmed);
        } else {
            candidates.push(trimmed);
        }

        for (const candidate of candidates) {
            const date = new Date(candidate);
            if (!Number.isNaN(date.getTime())) {
                return date;
            }
        }
        return null;
    };

    const formatTimestamp = (value) => {
        const parsedDate = parseMetadataTimestamp(value);
        if (!parsedDate) {
            return formatText(value);
        }
        try {
            return new Intl.DateTimeFormat(undefined, {
                dateStyle: 'medium',
                timeStyle: 'short',
            }).format(parsedDate);
        } catch (_) {
            return parsedDate.toLocaleString();
        }
    };

    const formatModel = (value) => {
        const text = formatText(value);
        if (!text) return text;
        const parts = text.split('/');
        return parts[parts.length - 1] || text;
    };

    // Provider metadata keys remain raw, but their user-facing labels use stable
    // translation keys so the diagnostics panel follows the selected language.
    const displayOrder = [
        { keys: ['timestamp'], labelKey: 'assistant_metadata_timestamp', label: 'Timestamp', formatter: formatTimestamp },
        { keys: ['status'], labelKey: 'assistant_metadata_status', label: 'Status', formatter: formatModel },
        {
            keys: ['model_id', 'modelId', 'model', 'model_name', 'modelName'],
            labelKey: 'assistant_metadata_model',
            label: 'Model',
            formatter: formatModel,
        },
        { keys: ['request_count'], labelKey: 'assistant_metadata_request_count', label: 'Request Count', formatter: formatInteger },
        { keys: ['input_tokens'], labelKey: 'assistant_metadata_input_tokens', label: 'Input Tokens', formatter: formatInteger },
        {
            keys: ['input_token_cached', 'cached_input_tokens', 'input_tokens_cached'],
            labelKey: 'assistant_metadata_cached_input_tokens',
            label: 'Cached Input Tokens',
            formatter: formatInteger,
        },
        { keys: ['cache_write_tokens', 'input_token_cache_write'], labelKey: 'assistant_metadata_cache_write_tokens', label: 'Cache Write Tokens', formatter: formatInteger },
        { keys: ['ephemeral_1h_input_tokens'], labelKey: 'assistant_metadata_ephemeral_1h_input_tokens', label: 'Ephemeral 1h Input Tokens', formatter: formatInteger },
        { keys: ['ephemeral_5m_input_tokens'], labelKey: 'assistant_metadata_ephemeral_5m_input_tokens', label: 'Ephemeral 5m Input Tokens', formatter: formatInteger },
        { keys: ['cache_creation_input_tokens'], labelKey: 'assistant_metadata_cache_creation_input_tokens', label: 'Cache Creation Input Tokens', formatter: formatInteger },
        { keys: ['cache_read_input_tokens'], labelKey: 'assistant_metadata_cache_read_input_tokens', label: 'Cache Read Input Tokens', formatter: formatInteger },
        { keys: ['output_tokens'], labelKey: 'assistant_metadata_output_tokens', label: 'Output Tokens', formatter: formatInteger },
        { keys: ['reasoning_tokens', 'thinking_tokens'], labelKey: 'assistant_metadata_reasoning_tokens', label: 'Reasoning Tokens', formatter: formatInteger },
        { keys: ['total_thinking_time'], labelKey: 'assistant_metadata_thinking_time', label: 'Thinking Time', formatter: formatSeconds },
        { keys: ['total_tokens'], labelKey: 'assistant_metadata_total_tokens', label: 'Total Tokens', formatter: formatInteger },
        { keys: ['context_tokens_used'], labelKey: 'assistant_metadata_context_tokens_used', label: 'Context Tokens Used', formatter: formatInteger },
        { keys: ['context_window_size'], labelKey: 'assistant_metadata_context_window_size', label: 'Context Window', formatter: formatInteger },
        { keys: ['tokens_per_second', 'tokens_per_seconds', 'token_rate'], labelKey: 'assistant_metadata_tokens_per_second', label: 'Tokens per Second', formatter: formatDecimal },
        { keys: ['time_to_first_token'], labelKey: 'assistant_metadata_time_to_first_token', label: 'Time to first token', formatter: formatDecimal },
        { keys: ['provider'], labelKey: 'assistant_metadata_provider', label: 'Provider', formatter: formatText },
        { keys: ['load_duration'], labelKey: 'assistant_metadata_load_duration', label: 'Load Duration', formatter: formatSeconds },
        { keys: ['queue_duration'], labelKey: 'assistant_metadata_queue_duration', label: 'Queue Duration', formatter: formatSeconds },
        { keys: ['input_duration'], labelKey: 'assistant_metadata_input_duration', label: 'Input Duration', formatter: formatSeconds },
        { keys: ['output_duration'], labelKey: 'assistant_metadata_output_duration', label: 'Output Duration', formatter: formatSeconds },
        { keys: ['total_duration', 'generation_time'], labelKey: 'assistant_metadata_total_duration', label: 'Total Duration', formatter: formatSeconds },
        { keys: ['stop_reason'], labelKey: 'assistant_metadata_stop_reason', label: 'Stop Reason', formatter: formatModel },
        { keys: ['service_tier'], labelKey: 'assistant_metadata_service_tier', label: 'Service Tier', formatter: formatModel },
        { keys: ['reasoning_effort'], labelKey: 'assistant_metadata_reasoning_effort', label: 'Reasoning Effort', formatter: formatModel },
        { keys: ['verbosity'], labelKey: 'assistant_metadata_verbosity', label: 'Verbosity', formatter: formatModel },
        { keys: ['store'], labelKey: 'assistant_metadata_store', label: 'Store', formatter: formatModel },
    ];

    const consumedKeys = new Set();
    const tooltipRows = [];

    const appendRow = (label, value) => {
        if (value === null || value === undefined) return;
        const textValue = String(value).trim();
        if (!textValue) return;

        const row = document.createElement('div');
        row.className = 'tooltip-row';

        const labelEl = document.createElement('span');
        labelEl.className = 'tooltip-label';
        labelEl.textContent = `${label}:`;
        row.appendChild(labelEl);

        const valueEl = document.createElement('span');
        valueEl.className = 'tooltip-value';
        valueEl.textContent = textValue;
        row.appendChild(valueEl);

        tooltipRows.push(row);
    };

    displayOrder.forEach(({ keys, labelKey, label, formatter }) => {
        const keyList = Array.isArray(keys) ? keys : [keys];
        const matchedKey = keyList.find((key) => Object.prototype.hasOwnProperty.call(metadataForStats, key));
        if (!matchedKey) return;
        keyList.forEach((key) => {
            if (Object.prototype.hasOwnProperty.call(metadataForStats, key)) consumedKeys.add(key);
        });
        appendRow(getStreamText(labelKey, label), formatter(metadataForStats[matchedKey]));
    });

    if (!tooltipRows.length) {
        statsContainer.remove();
        return;
    }

    tooltipRows.forEach(row => tooltipContentEl.appendChild(row));


}

function refreshAssistantMetadataVisibility() {
    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (!chatAreaContainer) return;
    const assistantContainers = chatAreaContainer.querySelectorAll('.assistant-message-container');
    assistantContainers.forEach((container) => {
        if (container.dataset.isStreaming === 'true') {
            return;
        }
        const messageId = container.id && container.id.startsWith('a-') ? container.id.slice(2) : '';
        if (!messageId) return;

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
    });
}

if (typeof window !== 'undefined') {
    window.refreshAssistantMetadataVisibility = refreshAssistantMetadataVisibility;
}



function appendAssistantError(messageId, error, last_appended_message_type) {
    const assistantMessageContainer = document.getElementById('a-' + messageId);
    if (!assistantMessageContainer) return;

    assistantMessageContainer.dataset.hasError = 'true';
    assistantMessageContainer.dataset.announceStreaming = 'false';
    delete assistantMessageContainer.dataset.isStreaming;

    if (last_appended_message_type == "t" || last_appended_message_type == "r") {
    const thinkingContainers = assistantMessageContainer.querySelectorAll('.assistant-thinking');
    const lastThinking = thinkingContainers.length ? thinkingContainers[thinkingContainers.length - 1] : null;
    if (lastThinking) {
        const headerSpan = lastThinking.querySelector('.assistant-thinking-title span');
        if (headerSpan) {
            headerSpan.dataset.thinkingType = 'error';
            headerSpan.classList.remove('assistant-thinking-shimmer');
            headerSpan.textContent = getStreamText('common_error', 'Error');
            }
        }
    }

    const errorDiv = document.createElement('div');
    errorDiv.className = 'assistant-message-error';
    errorDiv.setAttribute('role', 'alert');
    errorDiv.textContent = error == null ? '' : String(error);

    const listDiv = assistantMessageContainer.querySelector('.assistant-message-list');
    if (listDiv) {
        assistantMessageContainer.insertBefore(errorDiv, listDiv);
    } else {
        assistantMessageContainer.appendChild(errorDiv);
    }
    applyAssistantMessageAccessibility(assistantMessageContainer, { messageId, streaming: false, hasError: true });
    announceChatMessage(getChatA11yText('chat_sr_response_failed', 'Assistant response failed'), { assertive: true });
}








// Helper
function setAssistantThinkingHeaderTitle(thinkingContainer, titleText) {
    if (!thinkingContainer) return;
    const headerSpan = thinkingContainer.querySelector('.assistant-thinking-title span');
    if (!headerSpan) return;
    const trimmed = String(titleText || '').trim();
    if (!trimmed) return;
    headerSpan.textContent = trimmed;
}

// Reverse map display names back to tool names for config lookup
// This is automatically built from TOOL_HEADER_CONFIG
function buildReverseToolNameMap() {
    const map = {};
    Object.keys(TOOL_HEADER_CONFIG).forEach(toolName => {
        if (toolName === '_default') return;
        const config = TOOL_HEADER_CONFIG[toolName];
        if (config.displayName) {
            map[config.displayName] = toolName;
        }
        if (config.displayNameKey && config.displayName) {
            map[getStreamText(config.displayNameKey, config.displayName)] = toolName;
        }
        // Also add the formatted version
        const formatted = formatToolDisplayName(toolName);
        if (formatted) {
            map[formatted] = toolName;
        }
    });
    Object.keys(TOOL_NAME_ALIASES).forEach(alias => {
        const formatted = formatToolDisplayName(alias);
        if (formatted) {
            map[formatted] = TOOL_NAME_ALIASES[alias];
        }
    });
    return map;
}

// Finalize thinking block headers for existing messages (when loading from API)
// This detects tool calls in the DOM and sets the appropriate completed header text
function finalizeThinkingBlockHeader(thinkingContainer, reasoningTime) {
    if (!thinkingContainer) return;
    
    const headerSpan = thinkingContainer.querySelector('.assistant-thinking-title span');
    if (!headerSpan) return;

    // Preserve explicit media failure labels when the response-level done path
    // performs its generic finalization pass.
    if (thinkingContainer.dataset.mediaGenerationStatus === 'failed'
        || thinkingContainer.dataset.toolFailureStatus === 'failed'
        || headerSpan.dataset.thinkingType === 'tool-failed') {
        const failureLabel = String(
            thinkingContainer.dataset.toolFailureLabel
            || thinkingContainer.dataset.mediaGenerationFailureLabel
            || ''
        ).trim();
        if (failureLabel) {
            headerSpan.textContent = failureLabel;
        }
        headerSpan.classList.remove('assistant-thinking-shimmer');
        headerSpan.dataset.thinkingType = 'tool-failed';
        return;
    }
    
    // First check if we have tool calls tracked in the container's data (from streaming)
    let toolCalls = getToolCallsFromThinkingContainer(thinkingContainer);
    
    // If no tracked tool calls, try to detect them from the DOM (for loaded messages)
    if (!toolCalls || toolCalls.length === 0) {
        const functionCalls = thinkingContainer.querySelectorAll('.thinking-step-function-call');
        toolCalls = [];
        
        const reverseMap = buildReverseToolNameMap();
        
        functionCalls.forEach(callEl => {
            // Try to extract tool name from the function-call-name element
            const nameEl = callEl.querySelector('.function-call-name');
            const displayName = nameEl ? nameEl.textContent.trim() : '';
            
            // Try to extract first argument for display
            const paramEl = callEl.querySelector('.function-call-param');
            const argValue = paramEl ? paramEl.textContent.trim() : null;
            
            // Reverse-map display name to tool name for config lookup
            const normalizedToolName = reverseMap[displayName] || displayName.toLowerCase().replace(/\s+/g, '_');
            
            if (normalizedToolName) {
                const config = getToolConfig(normalizedToolName);
                const argKey = config.argKey || 'query';
                toolCalls.push({
                    name: normalizedToolName,
                    args: argValue ? { [argKey]: argValue } : null
                });
            }
        });
    }
    
    // Generate the appropriate header text
    const headerText = getThinkingBlockFinalHeader(toolCalls, reasoningTime || 0);
    
    headerSpan.classList.remove('assistant-thinking-shimmer');
    headerSpan.dataset.thinkingType = toolCalls.length > 0 ? 'tool-done' : 'done';
    headerSpan.textContent = headerText;
}

function parseLeadingTitle(text) {
    if (!text) return null;
    // Allow bold titles: **Title** followed by:
    // 1. Newline(s) and content
    // 2. Space and content starting with capital letter (for inline titles)
    // The title can appear at the start OR directly after other content (no gap required)
    
    // A title-only reasoning update is a complete chronological step. Some
    // providers stream the opening "**" separately, so accepting the
    // final standalone form also lets the accumulated-step repair below
    // convert fragmented titles without displaying raw Markdown markers.
    const standaloneBoldMatch = text.match(/^\s*\*\*([^\n*][^*]*?)\*\*\s*$/);
    if (standaloneBoldMatch) {
        return { title: standaloneBoldMatch[1].trim(), rest: '' };
    }

    // Match at start: **Title** followed by newline(s) or space+capital letter
    const boldMatch = text.match(/^(\s*)\*\*([^\n*][^*]*?)\*\*(?:\s*\n+([\s\S]*)|\s+((?=[A-Z])[\s\S]*))$/);
    if (boldMatch) {
        const rest = boldMatch[3] !== undefined ? boldMatch[3] : (boldMatch[4] || '');
        return { title: boldMatch[2].trim(), rest: rest };
    }
    
    // Also try to match bold title anywhere in the text (for mid-stream titles)
    // This catches cases like "some text**Title** More content..." or "some text**Title**\nrest"
    const midMatch = text.match(/\*\*([^\n*][^*]*?)\*\*(?:\s*\n+([\s\S]*)|\s+((?=[A-Z])[\s\S]*))$/);
    if (midMatch) {
        const beforeTitle = text.slice(0, text.indexOf('**' + midMatch[1] + '**'));
        const rest = midMatch[2] !== undefined ? midMatch[2] : (midMatch[3] || '');
        return { 
            title: midMatch[1].trim(), 
            rest: rest,
            prefix: beforeTitle.trim() || null
        };
    }
    return null;
}

// Detect embedded titles in accumulated streaming content
// Returns null if no embedded title found, or { beforeTitle, title, afterTitle } if found
function detectEmbeddedTitle(text) {
    if (!text) return null;
    
    // Find ALL **Title** patterns and check the LAST one that has content after it
    // The title must be followed by newline(s)+content OR space+capital letter
    const titlePattern = /\*\*([^\n*][^*]*?)\*\*(?:\s*\n+([\s\S]*)|\s+((?=[A-Z])[\s\S]*))/g;
    
    let lastMatch = null;
    let lastMatchIndex = -1;
    let match;
    
    while ((match = titlePattern.exec(text)) !== null) {
        // Check if there's meaningful content BEFORE this title
        const beforeContent = text.slice(0, match.index);
        if (beforeContent.trim()) {
            lastMatch = match;
            lastMatchIndex = match.index;
        }
    }
    
    if (lastMatch && lastMatchIndex > 0) {
        const beforeTitle = text.slice(0, lastMatchIndex);
        const title = lastMatch[1].trim();
        const afterTitle = lastMatch[2] !== undefined ? lastMatch[2] : (lastMatch[3] || '');
        
        return {
            beforeTitle: beforeTitle.trim(),
            title: title,
            afterTitle: afterTitle
        };
    }
    
    return null;
}

// Detect first title at the START of accumulated content (for streaming where title comes in chunks)
function detectFirstTitle(text) {
    if (!text) return null;
    
    // Match **Title** at the very start. The body is optional because providers
    // may emit title-only thought steps, often split across protocol chunks.
    const startMatch = text.match(/^\s*\*\*([^\n*][^*]*?)\*\*(?:(?:\s*\n+)([\s\S]*)|\s+((?=[A-Z])[\s\S]+))?\s*$/);
    if (startMatch) {
        const title = startMatch[1].trim();
        const afterTitle = startMatch[2] !== undefined ? startMatch[2] : (startMatch[3] || '');
        return {
            title: title,
            afterTitle: afterTitle
        };
    }
    return null;
}

// Check accumulated step content for titles and restructure if found
function checkAndSplitStepForEmbeddedTitle(thinkingContainer, messageId, assistantReasoningCount) {
    if (!thinkingContainer) return false;
    
    const body = thinkingContainer.querySelector('.assistant-thinking-body');
    if (!body) return false;
    
    // Get the last thinking step content
    const steps = body.querySelectorAll('.thinking-step');
    const lastStep = steps.length ? steps[steps.length - 1] : null;
    if (!lastStep) return false;
    
    const lastContent = lastStep.querySelector('.thinking-step-content');
    if (!lastContent) return false;
    
    const accumulatedText = getAssistantThinkingRawContent(lastContent);
    
    // First, check whether accumulated content starts with a title. If this
    // step already has a header, the accumulated title belongs to a new
    // thought step (typically because its opening "**" arrived separately).
    const hasHeader = lastStep.querySelector('.thinking-step-header');
    const firstTitle = detectFirstTitle(accumulatedText);
    if (firstTitle) {
        if (!hasHeader) {
            // Add a header to the current step
            const stepHeader = document.createElement('div');
            stepHeader.className = 'thinking-step-header';
            const stepTitle = document.createElement('span');
            stepTitle.className = 'thinking-step-title';
            stepTitle.textContent = firstTitle.title;
            stepHeader.appendChild(stepTitle);
            lastStep.insertBefore(stepHeader, lastContent);
            
            // Update the content to only have the afterTitle part
            setAssistantThinkingContent(lastContent, firstTitle.afterTitle);
            
            // Update the main header
            setAssistantThinkingHeaderTitle(thinkingContainer, firstTitle.title);
            
            return true;
        }

        // The current step already represents an earlier titled thought.
        // Clear the temporary fragment buffer and append the newly completed
        // title after it, preserving the protocol event order in the DOM.
        setAssistantThinkingContent(lastContent, '');

        const newStep = document.createElement('div');
        newStep.className = 'thinking-step';

        const stepHeader = document.createElement('div');
        stepHeader.className = 'thinking-step-header';
        const stepTitle = document.createElement('span');
        stepTitle.className = 'thinking-step-title';
        stepTitle.textContent = firstTitle.title;
        stepHeader.appendChild(stepTitle);
        newStep.appendChild(stepHeader);

        const stepContent = document.createElement('div');
        stepContent.className = 'thinking-step-content';
        setAssistantThinkingContent(stepContent, firstTitle.afterTitle);
        newStep.appendChild(stepContent);
        body.appendChild(newStep);

        setAssistantThinkingHeaderTitle(thinkingContainer, firstTitle.title);
        return true;
    }
    
    // Then check for embedded titles (titles appearing after content)
    const embedded = detectEmbeddedTitle(accumulatedText);
    
    if (!embedded) return false;
    
    // Found an embedded title - split the content
    // Update the current step content to only contain beforeTitle
    setAssistantThinkingContent(lastContent, embedded.beforeTitle);
    
    // Create a new step with the new title and afterTitle content
    const newStep = document.createElement('div');
    newStep.className = 'thinking-step';
    
    const stepHeader = document.createElement('div');
    stepHeader.className = 'thinking-step-header';
    const stepTitle = document.createElement('span');
    stepTitle.className = 'thinking-step-title';
    stepTitle.textContent = embedded.title;
    stepHeader.appendChild(stepTitle);
    newStep.appendChild(stepHeader);
    
    const stepContent = document.createElement('div');
    stepContent.className = 'thinking-step-content';
    setAssistantThinkingContent(stepContent, embedded.afterTitle);
    newStep.appendChild(stepContent);
    
    body.appendChild(newStep);
    
    // Update the main header to show the new title
    setAssistantThinkingHeaderTitle(thinkingContainer, embedded.title);
    
    return true;
}
