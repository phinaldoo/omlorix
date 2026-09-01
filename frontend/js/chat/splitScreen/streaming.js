// Split Screen Manager internals. Load before ../splitScreen.js in the documented order.

// ───── Send Message to Panel ─────

async function splitScreenInternalSendToPanel(message, side, composerContext = {}, options = {}) {
    const chatId = side === 'left' ? splitScreenInternalState.leftChatId : splitScreenInternalState.rightChatId;
    const modelId = side === 'left' ? splitScreenInternalState.leftModelId : splitScreenInternalState.rightModelId;
    const container = side === 'left' ? splitScreenInternalGetLeftContainer() : splitScreenInternalGetRightContainer();
    const area = side === 'left' ? splitScreenInternalGetLeftArea() : splitScreenInternalGetRightArea();
    const panel = side === 'left' ? splitScreenInternalGetLeftPanel() : splitScreenInternalGetRightPanel();
    const onRequestSettled = typeof options.onRequestSettled === 'function'
        ? options.onRequestSettled
        : null;
    const generationRequestId = generateUUID();
    let requestSettled = false;
    const settleRequest = (accepted) => {
        if (requestSettled) return;
        requestSettled = true;
        try {
            onRequestSettled?.(Boolean(accepted), generationRequestId);
        } catch (error) {
            console.error(`Failed to settle the ${side} split-screen request`, error);
        }
    };

    if (!modelId) {
        settleRequest(false);
        return {
            ok: false,
            side,
            error: splitScreenInternalSplitScreenTf(
                'split_screen_no_model_selected',
                'No model selected for {side} panel',
                { side: splitScreenInternalGetTranslatedSideLabel(side) }
            ),
        };
    }

    if (!container) {
        settleRequest(false);
        return { ok: false, side, error: splitScreenInternalSplitScreenT('split_screen_panel_unavailable', 'Panel is unavailable') };
    }

    // Mark the panel busy before the first asynchronous preflight. This
    // closes the window where a rapid second send could dispatch another
    // request to the same panel before the first request was visible.
    const generationToken = splitScreenInternalStartPanelGeneration(side, generationRequestId);
    let streamedMessageId = '';
    try {
        // Gather custom model settings for the target panel, not whichever
        // split settings tab happens to be visible.
        const customModelSettings = await splitScreenInternalGetPanelCustomSettingsForSend(side);
        let byokPayload = null;
        try {
            if (typeof window.BYOK?.buildRequestPayloadForModel === 'function') {
                byokPayload = window.BYOK.buildRequestPayloadForModel(modelId, customModelSettings);
            }
        } catch (error) {
            return {
                ok: false,
                side,
                error: error.message || splitScreenInternalSplitScreenT('split_screen_byok_prepare_failed', 'Failed to prepare BYOK request'),
            };
        }

        const payloadImageIds = composerContext.imageIds || [];
        const payloadVideoIds = composerContext.videoIds || [];
        const payloadAudioIds = composerContext.audioIds || [];
        const payloadDocumentIds = composerContext.documentIds || [];
        const payloadSkillIds = composerContext.skillIds || [];
        const payloadNoteIds = composerContext.noteIds || [];
        const payloadPromptIds = composerContext.promptIds || [];
        const payloadReferenceParts = composerContext.referenceParts || [];
        const payloadChatReferenceIds = composerContext.chatReferenceIds || [];
        const tempChatHistory = (!chatId && splitScreenInternalIsPanelTemporary(side)) ? splitScreenInternalSerializePanelTemporaryHistory(side) : '';
        const panelProjectId = splitScreenInternalResolvePanelProjectIdForSend(
            chatId,
            splitScreenInternalGetPanelProjectId(side),
            composerContext.projectId
        );
        if (!chatId && panelProjectId) {
            splitScreenInternalSetPanelProjectId(side, panelProjectId);
        }

        const body = JSON.stringify({
            payload: {
                generation_id: generationRequestId,
                model_id: byokPayload ? '' : modelId,
                message,
                chat_id: chatId || '',
                image_ids: payloadImageIds,
                video_ids: payloadVideoIds,
                audio_ids: payloadAudioIds,
                document_ids: payloadDocumentIds,
                skill_ids: payloadSkillIds.length ? payloadSkillIds : null,
                note_ids: payloadNoteIds.length ? payloadNoteIds : [],
                prompt_ids: payloadPromptIds.length ? payloadPromptIds : null,
                reference_parts: payloadReferenceParts.length ? payloadReferenceParts : null,
                chat_reference_ids: payloadChatReferenceIds.length ? payloadChatReferenceIds : null,
                project_id: panelProjectId,
                temp_chat: tempChatHistory,
            },
            custom_settings: byokPayload
                ? {}
                : (customModelSettings && Object.keys(customModelSettings).length ? customModelSettings : {}),
            byok: byokPayload || {}
        });

        let res;
        try {
            res = await window.authedFetch('/api/v1/chats/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body,
                signal: side === 'left'
                    ? splitScreenInternalState.leftAbortController?.signal
                    : splitScreenInternalState.rightAbortController?.signal,
            });
        } catch (_) {
            if (splitScreenInternalIsPanelCancellationRequested(side)) {
                return { ok: true, side, cancelled: true };
            }
            return { ok: false, side, error: splitScreenInternalSplitScreenT('split_screen_send_failed', 'Failed to send message') };
        }

        if (!res.ok) {
            const errorData = await res.json().catch(() => ({}));
            const detail = resolveSplitScreenErrorMessage(errorData, splitScreenInternalSplitScreenT('split_screen_send_failed', 'Failed to send message'));
            if (res.status === 429) {
                showRateLimitCard({
                    container,
                    errorData,
                    fallbackDetail: detail,
                    switchLabel: splitScreenInternalSplitScreenT('split_screen_switch_panel_model', 'Switch panel model'),
                    onSwitchModel: () => {
                        SplitScreenManager?.openPanelModelPicker?.(side);
                    },
                });
                return { ok: false, side, error: detail, rateLimited: true };
            }
            const shouldExposeDetail = Boolean(byokPayload || isSplitScreenChatReferenceError(errorData));
            return {
                ok: false,
                side,
                error: shouldExposeDetail ? detail : splitScreenInternalSplitScreenT('split_screen_send_failed', 'Failed to send message'),
            };
        }

        // Clearing shared composer context is safe only after every target
        // request reaches this accepted point.
        settleRequest(true);
        panel?.classList.add('has-chat');

        let streamFailure = null;
        try {
            streamedMessageId = await splitScreenInternalProcessStream(res, side, message, container, area, {
                generationToken,
                pendingFiles: composerContext.attachmentFiles,
                pendingChatReferences: composerContext.chatReferencePayload,
                onDone: () => {
                    splitScreenInternalFinishPanelGeneration(side, generationToken, 'finished');
                },
                onFailure: (failure) => {
                    // Preserve the first terminal failure. Later cleanup
                    // events must not turn a failed turn back into success.
                    if (!streamFailure) {
                        streamFailure = failure;
                    }
                },
            });
        } catch (error) {
            console.error(`Failed to process the ${side} split-screen stream:`, error);
            streamFailure = streamFailure || {
                message: error?.message || splitScreenInternalSplitScreenT('split_screen_connection_interrupted', 'Connection interrupted'),
                rateLimited: false,
            };
        }
        if (streamFailure) {
            return {
                ok: false,
                side,
                error: streamFailure.message || splitScreenInternalSplitScreenT('split_screen_send_failed', 'Failed to send message'),
                rateLimited: Boolean(streamFailure.rateLimited),
            };
        }
        return { ok: true, side };
    } finally {
        settleRequest(false);
        window.ChatScrollManager?.endStream?.(area);
        // Fallback cleanup when a preflight or stream closes without an
        // explicit terminal event.
        if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleStreamEnd === 'function') {
            try {
                window.NotesToolSidebar.handleStreamEnd(streamedMessageId);
            } catch (error) {
                console.error('Failed to clean up split-screen notes preview after generation', error);
            }
        }
        if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleStreamEnd === 'function') {
            try {
                window.canvasMarkdownWidget.handleStreamEnd(streamedMessageId);
            } catch (error) {
                console.error('Failed to clean up split-screen canvas preview after generation', error);
            }
        }
        if (window.slidePresentationWidget && typeof window.slidePresentationWidget.handleStreamEnd === 'function') {
            try {
                window.slidePresentationWidget.handleStreamEnd(streamedMessageId);
            } catch (error) {
                console.error('Failed to clean up split-screen slide presentation preview after generation', error);
            }
        }
        splitScreenInternalFinishPanelGeneration(side, generationToken);
    }
}

async function splitScreenInternalProcessStream(res, side, message, container, area, options = {}) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let messageId = '';
    let assistantContentCount = 0;
    let assistantReasoningCount = 0;
    let last_appended_message_type = '';
    let temp_reasoning_time = 0;
    const onDone = typeof options?.onDone === 'function' ? options.onDone : null;
    const onMessageId = typeof options?.onMessageId === 'function' ? options.onMessageId : null;
    const onFailure = typeof options?.onFailure === 'function' ? options.onFailure : null;
    const generationToken = Number.isFinite(options?.generationToken) ? options.generationToken : null;
    const shouldAutoFollowStream = !String(message || '').trim();
    let didEmitDone = false;
    let didEmitFailure = false;
    let streamBecameStale = false;

    if (shouldAutoFollowStream) {
        window.ChatScrollManager?.beginStream?.(area);
    } else {
        window.ChatScrollManager?.bind?.(area);
    }

    function isCurrentPanelStream() {
        return generationToken === null || splitScreenInternalGetPanelGenerationToken(side) === generationToken;
    }

    // A response can arrive after a newer generation has claimed this
    // panel. Never let that stale stream replace the active reader.
    if (isCurrentPanelStream()) {
        if (side === 'left') {
            splitScreenInternalState.leftStreamReader = reader;
        } else {
            splitScreenInternalState.rightStreamReader = reader;
        }
    }

    function stopIfStreamIsStale() {
        if (isCurrentPanelStream()) {
            return false;
        }
        streamBecameStale = true;
        try {
            reader.cancel();
        } catch (_) {}
        return true;
    }

    function emitDone() {
        if (didEmitDone) return;
        didEmitDone = true;
        if (onDone) {
            onDone();
        }
    }

    function emitFailure(messageText, details = {}) {
        if (didEmitFailure) return;
        didEmitFailure = true;
        if (onFailure) {
            onFailure({
                message: String(messageText || splitScreenInternalSplitScreenT('split_screen_send_failed', 'Failed to send message')),
                rateLimited: Boolean(details.rateLimited),
            });
        }
    }

    function findSidebarRowByChatId(chatId) {
        const normalizedChatId = String(chatId || '').trim();
        if (!normalizedChatId) return null;
        const containers = [
            document.getElementById('pinnedChatsContainer'),
            document.getElementById('chatsContainer')
        ].filter(Boolean);

        for (const containerEl of containers) {
            const row = Array.from(containerEl.querySelectorAll('.sidebar-element'))
                .find(el => el.dataset.chatId === normalizedChatId);
            if (row) return row;
        }
        return null;
    }

    function ensureSidebarRowForChat(chatId, { initialTitle = '', projectId = null } = {}) {
        const normalizedChatId = String(chatId || '').trim();
        if (!normalizedChatId) return null;

        let row = findSidebarRowByChatId(normalizedChatId);
        const chatsContainerEl = document.getElementById('chatsContainer');
        if (!row && chatsContainerEl && typeof createChatRow === 'function') {
            row = createChatRow({
                id: normalizedChatId,
                title: initialTitle,
                project_id: String(projectId || '').trim() || null,
            });
            if (row) {
                chatsContainerEl.insertBefore(row, chatsContainerEl.firstChild);
            }
        }

        if (row) {
            const link = row.querySelector('a.sidebar-element-button');
            if (link) {
                link.href = `/chat/${encodeURIComponent(normalizedChatId)}`;
            }
        }

        const historySectionEl = document.getElementById('history');
        if (historySectionEl) historySectionEl.style.display = '';

        return row;
    }

    function applyGeneratedSidebarTitle(chatId, title) {
        const normalizedChatId = String(chatId || '').trim();
        const generatedTitle = typeof title === 'string' ? title : '';
        if (!normalizedChatId || !generatedTitle) return;

        const row = ensureSidebarRowForChat(normalizedChatId, {
            initialTitle: generatedTitle,
            projectId: splitScreenInternalGetPanelProjectId(side),
        });
        if (!row) return;

        row.dataset.chatTitle = generatedTitle;
        const titleEl = row.querySelector('a.sidebar-element-button > p');
        if (titleEl) {
            if (typeof typewriteText === 'function') {
                typewriteText(titleEl, generatedTitle);
            } else {
                titleEl.textContent = generatedTitle;
            }
        }

        if (typeof updateTabTitleIfActive === 'function') {
            updateTabTitleIfActive(normalizedChatId);
        }

        if (typeof window.addOrUpdateProjectChatRow === 'function') {
            try {
                window.addOrUpdateProjectChatRow(normalizedChatId, generatedTitle);
            } catch (_) {}
        }
    }

    try {
    while (true) {
        if (stopIfStreamIsStale()) {
            return messageId;
        }
        let readResult;
        try {
            readResult = await reader.read();
        } catch (error) {
            if (splitScreenInternalIsPanelCancellationRequested(side)) {
                break;
            }
            const connectionMessage = splitScreenInternalSplitScreenT('split_screen_connection_interrupted', 'Connection interrupted');
            emitFailure(connectionMessage);
            if (!onFailure && typeof notifyError === 'function') {
                notifyError(connectionMessage);
            }
            break;
        }

        const { done, value } = readResult;
        if (splitScreenInternalIsPanelCancellationRequested(side)) {
            break;
        }
        if (stopIfStreamIsStale()) {
            return messageId;
        }

        // Flush TextDecoder and process a final non-newline-terminated JSON
        // event before deciding whether EOF was a valid completion.
        const chunk = done
            ? decoder.decode()
            : decoder.decode(value, { stream: true });
        buffer += chunk;
        const parts = buffer.replace(/\r\n/g, '\n').split('\n');
        if (done) {
            buffer = '';
        } else {
            buffer = parts.pop() || '';
        }

        for (const line of parts) {
            if (stopIfStreamIsStale()) {
                return messageId;
            }
            const trimmed = line.trim();
            if (!trimmed) continue;

            let obj;
            try {
                obj = JSON.parse(trimmed);
            } catch (e) {
                continue;
            }

            if (obj.t === 'n_c') {
                // New chat created
                const newChatId = String(obj.d ?? '').trim();
                if (newChatId) {
                    if (side === 'left') {
                        splitScreenInternalState.leftChatId = newChatId;
                        splitScreenInternalState.leftTemporary = false;
                    } else {
                        splitScreenInternalState.rightChatId = newChatId;
                        splitScreenInternalState.rightTemporary = false;
                    }
                    splitScreenInternalUpdateURL();
                    splitScreenInternalUpdatePanelSaveButtons();

                    // Add to sidebar
                    try {
                        ensureSidebarRowForChat(newChatId, {
                            initialTitle: '',
                            projectId: splitScreenInternalGetPanelProjectId(side),
                        });
                    } catch (_) {}
                }
            } else if (obj.t === 's') {
                // Generation ID
                const generationId = String(obj.d ?? '');
                if (side === 'left') {
                    splitScreenInternalState.leftGenerationId = generationId;
                } else {
                    splitScreenInternalState.rightGenerationId = generationId;
                }
                const generationChatId = side === 'left' ? splitScreenInternalState.leftChatId : splitScreenInternalState.rightChatId;
                window.ChatAttention?.trackGeneration(generationChatId, generationId);
                const pendingCancel = side === 'left' ? splitScreenInternalState.leftPendingCancel : splitScreenInternalState.rightPendingCancel;
                if (pendingCancel) {
                    if (side === 'left') {
                        splitScreenInternalState.leftPendingCancel = false;
                    } else {
                        splitScreenInternalState.rightPendingCancel = false;
                    }
                    Promise.resolve(splitScreenInternalRequestCancelForSide(side)).catch(() => {});
                }
            } else if (obj.t === 'm_id') {
                // Message ID - create user message and assistant container
                messageId = obj.d;
                if (side === 'left') {
                    splitScreenInternalState.leftStreamMessageId = messageId;
                } else {
                    splitScreenInternalState.rightStreamMessageId = messageId;
                }
                if (onMessageId) onMessageId(messageId);

                // Temporarily swap container
                const origContainer = splitScreenInternalMainChatAreaContainer || document.getElementById('chatAreaContainer');
                if (origContainer) origContainer.id = '__splitStream_chatAreaContainer';
                const originalSplitId = container.id;
                container.dataset.splitId = originalSplitId;
                container.id = 'chatAreaContainer';

                try {
                    // The shared composer may already have accepted a newer
                    // turn by the time this panel receives m_id. Render from
                    // the immutable dispatch snapshot rather than whichever
                    // attachments are selected now.
                    const pendingFiles = Array.isArray(options.pendingFiles)
                        ? options.pendingFiles
                        : [];
                    const pendingChatReferences = Array.isArray(options.pendingChatReferences)
                        ? options.pendingChatReferences
                        : [];
                    if (typeof appendUserContent === 'function') {
                        appendUserContent(messageId, message, pendingFiles, pendingChatReferences);
                    }
                    if (typeof appendAssistantContainer === 'function') {
                        appendAssistantContainer(messageId, { announce: true });
                    }
                    if (typeof appendLoading === 'function') {
                        assistantReasoningCount = appendLoading(messageId, assistantReasoningCount);
                        last_appended_message_type = 'loading';
                    }
                } finally {
                    if (container.dataset.splitId) {
                        container.id = container.dataset.splitId;
                        delete container.dataset.splitId;
                    }
                    if (origContainer) {
                        origContainer.id = 'chatAreaContainer';
                    }
                }

                splitScreenInternalScrollSplitUserMessageToTop(messageId, container, area);

            } else if (obj.t === 'c') {
                // Content
                if (last_appended_message_type === 'loading' && typeof removeLoading === 'function') {
                    removeLoading(messageId);
                    last_appended_message_type = '';
                }
                const mediaGenerationFailed = typeof clearMediaGenPlaceholderForNonFileEvent === 'function'
                    ? clearMediaGenPlaceholderForNonFileEvent(messageId)
                    : false;
                if (typeof appendAssistantContent === 'function') {
                    assistantContentCount = appendAssistantContent(
                        messageId, obj.d,
                        mediaGenerationFailed ? 'media-generation-failed' : last_appended_message_type,
                        assistantContentCount, temp_reasoning_time, assistantReasoningCount
                    );
                    last_appended_message_type = 'c';
                }
                // Auto-scroll
                if (shouldAutoFollowStream && area) {
                    if (window.ChatScrollManager && typeof window.ChatScrollManager.scheduleFollow === 'function') {
                        window.ChatScrollManager.scheduleFollow(area);
                    } else {
                        const isNearBottom = area.scrollHeight - area.scrollTop - area.clientHeight < 100;
                        if (isNearBottom) splitScreenInternalScrollSplitAreaToBottom(area);
                    }
                }

            } else if (obj.t === 'r') {
                // Reasoning
                const wasLoading = last_appended_message_type === 'loading';
                if (wasLoading && typeof expandLoading === 'function') {
                    assistantReasoningCount = expandLoading(messageId, assistantReasoningCount);
                }
                const mediaGenerationFailed = typeof clearMediaGenPlaceholderForNonFileEvent === 'function'
                    ? clearMediaGenPlaceholderForNonFileEvent(messageId)
                    : false;
                if (typeof appendAssistantReasoning === 'function') {
                    assistantReasoningCount = appendAssistantReasoning(
                        messageId,
                        obj.d,
                        mediaGenerationFailed
                            ? 'media-generation-failed'
                            : (wasLoading ? 'r' : last_appended_message_type),
                        assistantReasoningCount
                    );
                    last_appended_message_type = 'r';
                }

            } else if (obj.t === 't_c') {
                // Tool call
                const wasLoading = last_appended_message_type === 'loading';
                if (wasLoading && typeof expandLoading === 'function') {
                    assistantReasoningCount = expandLoading(messageId, assistantReasoningCount);
                }
                const toolDescriptor = obj.d || {};
                const resolvedToolName = typeof toolDescriptor === 'string'
                    ? toolDescriptor : (toolDescriptor.name || '');
                const resolvedToolArgs = toolDescriptor.args ?? obj.c;
                const resolvedToolCallId = typeof toolDescriptor === 'object'
                    ? (toolDescriptor.id || toolDescriptor.tool_call_id || '')
                    : '';
                const mediaGenerationFailed = typeof transitionMediaGenPlaceholderForToolCall === 'function'
                    ? transitionMediaGenPlaceholderForToolCall(messageId, resolvedToolName, resolvedToolCallId)
                    : false;
                if (typeof appendAssistantTool === 'function') {
                    assistantReasoningCount = appendAssistantTool(
                        messageId,
                        mediaGenerationFailed ? 'media-generation-failed' : (wasLoading ? 'r' : last_appended_message_type),
                        assistantReasoningCount, null, resolvedToolName, resolvedToolArgs,
                        typeof toolDescriptor === 'object' ? toolDescriptor : null
                    );
                    last_appended_message_type = 't';
                }
                if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleToolCallEvent === 'function') {
                    try {
                        window.canvasMarkdownWidget.handleToolCallEvent(obj, messageId);
                    } catch (error) {
                        console.error('Failed to start split-screen canvas preview', error);
                    }
                }
                if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleToolCallEvent === 'function') {
                    try {
                        window.NotesToolSidebar.handleToolCallEvent(obj, messageId);
                    } catch (error) {
                        console.error('Failed to start split-screen notes preview', error);
                    }
                }
                if (typeof syncMediaGenPlaceholder === 'function') {
                    syncMediaGenPlaceholder(
                        messageId,
                        resolvedToolName,
                        resolvedToolCallId
                    );
                }

            } else if (obj.t === 't_e') {
                if (typeof applyAssistantToolError === 'function') {
                    applyAssistantToolError(messageId, obj.d || {}, { announce: true });
                }
            } else if (obj.t === 't_cd') {
                if (typeof processAssistantToolDeltaStreamEvent === 'function') {
                    const toolDeltaUpdate = processAssistantToolDeltaStreamEvent(
                        messageId,
                        last_appended_message_type,
                        assistantReasoningCount,
                        obj.d
                    );
                    assistantReasoningCount = toolDeltaUpdate.assistantReasoningCount;
                    last_appended_message_type = toolDeltaUpdate.lastAppendedMessageType;
                }
                if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleToolCallDeltaEvent === 'function') {
                    try {
                        window.canvasMarkdownWidget.handleToolCallDeltaEvent(obj, messageId);
                    } catch (error) {
                        console.error('Failed to update split-screen canvas preview', error);
                    }
                }
                if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleToolCallDeltaEvent === 'function') {
                    try {
                        window.NotesToolSidebar.handleToolCallDeltaEvent(obj, messageId);
                    } catch (error) {
                        console.error('Failed to update split-screen notes preview', error);
                    }
                }

            } else if (obj.t === 'r_f') {
                temp_reasoning_time = obj.d;

            } else if (obj.t === 'e') {
                if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleStreamEnd === 'function') {
                    try {
                        window.NotesToolSidebar.handleStreamEnd(messageId);
                    } catch (error) {
                        console.error('Failed to clean up split-screen notes preview after stream error', error);
                    }
                }
                if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleStreamEnd === 'function') {
                    try {
                        window.canvasMarkdownWidget.handleStreamEnd(messageId);
                    } catch (error) {
                        console.error('Failed to clean up split-screen canvas preview after stream error', error);
                    }
                }
                if (window.slidePresentationWidget && typeof window.slidePresentationWidget.handleStreamEnd === 'function') {
                    try {
                        window.slidePresentationWidget.handleStreamEnd(messageId);
                    } catch (error) {
                        console.error('Failed to clean up split-screen slide presentation preview after stream error', error);
                    }
                }
                // Error
                const translatedStreamError = obj.i18n_key
                    ? splitScreenInternalSplitScreenT(obj.i18n_key, obj.d || '')
                    : obj.d;
                const errorData = { detail: translatedStreamError };
                const detail = resolveSplitScreenErrorMessage(
                    errorData,
                    splitScreenInternalSplitScreenT('split_screen_send_failed', 'Failed to send message')
                );
                const isRateLimited = isRateLimitErrorPayload(errorData, detail);
                emitFailure(detail, { rateLimited: isRateLimited });
                console.error('[split-send] stream error event received', {
                    side,
                    detail: obj.d,
                    resolvedDetail: detail,
                    isRateLimited,
                    messageId,
                });
                if (last_appended_message_type === 'loading' && typeof removeLoading === 'function') {
                    removeLoading(messageId);
                    last_appended_message_type = '';
                }
                if (!messageId) {
                    if (isRateLimited) {
                        showRateLimitCard({
                            container,
                            errorData,
                            fallbackDetail: detail,
                            switchLabel: splitScreenInternalSplitScreenT('split_screen_switch_panel_model', 'Switch panel model'),
                            onSwitchModel: () => {
                                SplitScreenManager?.openPanelModelPicker?.(side);
                            },
                        });
                    } else if (typeof notifyError === 'function') {
                        notifyError(detail);
                    }
                    continue;
                }
                if (isRateLimited) {
                    showRateLimitCard({
                        container,
                        anchorElement: document.getElementById(`a-${messageId}`),
                        errorData,
                        fallbackDetail: detail,
                        switchLabel: splitScreenInternalSplitScreenT('split_screen_switch_panel_model', 'Switch panel model'),
                        onSwitchModel: () => {
                            SplitScreenManager?.openPanelModelPicker?.(side);
                        },
                    });
                    continue;
                }
                if (typeof appendAssistantError === 'function') {
                    appendAssistantError(messageId, detail, last_appended_message_type);
                }
                if (typeof clearMediaGenPlaceholderForNonFileEvent === 'function') {
                    clearMediaGenPlaceholderForNonFileEvent(messageId);
                }
                if (typeof appendAssistantDone === 'function') {
                    appendAssistantDone(messageId, '');
                }

            } else if (obj.t === 'w') {
                const warningFallback = obj.c ?? obj.d ?? obj.message ?? '';
                const warningMessage = obj.i18n_key
                    ? splitScreenInternalSplitScreenT(obj.i18n_key, warningFallback)
                    : warningFallback;
                if (warningMessage && typeof notifyWarning === 'function') {
                    notifyWarning(warningMessage);
                }

            } else if (obj.t === 'wg') {
                // Widget
                if (last_appended_message_type === 'loading' && typeof removeLoading === 'function') {
                    removeLoading(messageId);
                    last_appended_message_type = '';
                }
                if (typeof clearMediaGenPlaceholderForNonFileEvent === 'function') {
                    clearMediaGenPlaceholderForNonFileEvent(messageId);
                }
                const widgetHtml = obj.c ?? '';
                const widgetType = obj.widget_type ?? 'unknown';
                if (widgetHtml && typeof appendAssistantWidget === 'function') {
                    appendAssistantWidget(
                        messageId,
                        widgetHtml,
                        widgetType,
                        last_appended_message_type,
                        obj.meta ?? null,
                        { autoOpen: true },
                    );
                    last_appended_message_type = 'wg';
                }
            } else if (obj.t === 'deep_research_evt') {
                if (last_appended_message_type === 'loading' && typeof removeLoading === 'function') {
                    removeLoading(messageId);
                    last_appended_message_type = '';
                }
                if (typeof clearMediaGenPlaceholderForNonFileEvent === 'function') {
                    clearMediaGenPlaceholderForNonFileEvent(messageId);
                }
                if (window.deepResearchWidget && typeof window.deepResearchWidget.handleDeepResearchEvent === 'function') {
                    try {
                        window.deepResearchWidget.handleDeepResearchEvent(obj, messageId);
                    } catch (_) {}
                }

            } else if (obj.t === 'canvas_evt') {
                if (typeof clearMediaGenPlaceholderForNonFileEvent === 'function') {
                    clearMediaGenPlaceholderForNonFileEvent(messageId);
                }
                if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleCanvasEvent === 'function') {
                    try {
                        window.canvasMarkdownWidget.handleCanvasEvent(obj, messageId);
                    } catch (error) {
                        console.error('Failed to finalize split-screen canvas preview', error);
                    }
                }

            } else if (obj.t === 'notes_evt') {
                if (typeof clearMediaGenPlaceholderForNonFileEvent === 'function') {
                    clearMediaGenPlaceholderForNonFileEvent(messageId);
                }
                if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleNotesEvent === 'function') {
                    try {
                        window.NotesToolSidebar.handleNotesEvent(obj, messageId);
                    } catch (error) {
                        console.error('Failed to finalize split-screen notes preview', error);
                    }
                }

            } else if (obj.t === 'f') {
                // File
                if (last_appended_message_type === 'loading' && typeof removeLoading === 'function') {
                    removeLoading(messageId);
                    last_appended_message_type = '';
                }
                const fileId = obj.d ?? '';
                const fileName = obj.n ?? '';
                if (fileId && typeof appendAssistantFile === 'function') {
                    appendAssistantFile(messageId, fileId, last_appended_message_type, fileName);
                    last_appended_message_type = 'f';
                }

            } else if (obj.t === 'a_id') {
                const assistantMsgId = obj.d;
                if (assistantMsgId && typeof bindAssistantContainerToServerMessage === 'function') {
                    bindAssistantContainerToServerMessage(messageId, assistantMsgId, container);
                }

            } else if (obj.t === 'd') {
                // Done
                if (last_appended_message_type === 'loading' && typeof removeLoading === 'function') {
                    removeLoading(messageId);
                    last_appended_message_type = '';
                }
                if (typeof clearMediaGenPlaceholderForNonFileEvent === 'function') {
                    clearMediaGenPlaceholderForNonFileEvent(messageId);
                }
                const doneMetadata = obj.c;
                if (typeof appendAssistantDone === 'function') {
                    appendAssistantDone(messageId, doneMetadata);
                }
                // Finalize the thinking heading without changing its expanded state.
                try {
                    const c = document.getElementById('a-' + messageId);
                    if (c) {
                        const thinkingEls = c.querySelectorAll('.assistant-thinking');
                        const last = thinkingEls.length ? thinkingEls[thinkingEls.length - 1] : null;
                        if (last) {
                            if (typeof finalizeThinkingBlockHeader === 'function') {
                                finalizeThinkingBlockHeader(last, temp_reasoning_time);
                            }
                        }
                    }
                } catch (_) {}

                emitDone();

            } else if (obj.t === 't_g' || obj.t === 'n_t') {
                // Title generated through the normal or fallback event.
                const generatedTitle = typeof obj.d === 'string' ? obj.d : '';
                const currentChatId = side === 'left' ? splitScreenInternalState.leftChatId : splitScreenInternalState.rightChatId;
                if (currentChatId && generatedTitle) {
                    splitScreenInternalSetPanelChatTitle(side, generatedTitle);
                    try {
                        applyGeneratedSidebarTitle(currentChatId, generatedTitle);
                    } catch (_) {}
                }
            }
        }
        if (done) break;
    }
    if (
        !streamBecameStale
        && isCurrentPanelStream()
        && !didEmitDone
        && !didEmitFailure
        && !splitScreenInternalIsPanelCancellationRequested(side)
    ) {
        // A transport can close cleanly at the ReadableStream layer even
        // when the application stream was truncated. Only a structured
        // done event proves that the response completed successfully.
        emitFailure(splitScreenInternalSplitScreenT(
            'split_screen_connection_interrupted',
            'Connection interrupted'
        ));
    }
    } finally {
        // A superseding panel generation owns the shared transcript and
        // media state. The stale reader may return its message ID, but it
        // must not finalize or clear anything belonging to the new owner.
        const ownsPanelCleanup = !streamBecameStale && isCurrentPanelStream();
        if (ownsPanelCleanup) {
            // Resolve interrupted media generations when the active panel
            // stream closes without a structured done/error event.
            if (messageId && typeof clearMediaGenPlaceholderForNonFileEvent === 'function') {
                clearMediaGenPlaceholderForNonFileEvent(messageId);
            }
            // Keep presentation cleanup inside processStream so its local
            // message ID remains available when an active handler throws.
            // Only an explicit Stop receives a complete partial-response UI.
            if (splitScreenInternalIsPanelCancellationRequested(side)) {
                window.finalizeCancelledAssistantStream?.(messageId, container);
            } else {
                window.finalizeInterruptedAssistantStream?.(messageId, container);
            }
            if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleStreamEnd === 'function') {
                try {
                    window.canvasMarkdownWidget.handleStreamEnd(messageId);
                } catch (error) {
                    console.error('Failed to clean up split-screen canvas preview after stream completion', error);
                }
            }
            if (window.slidePresentationWidget && typeof window.slidePresentationWidget.handleStreamEnd === 'function') {
                try {
                    window.slidePresentationWidget.handleStreamEnd(messageId);
                } catch (error) {
                    console.error('Failed to clean up split-screen slide presentation preview after stream completion', error);
                }
            }
        }
    }
    return messageId;
}

// ───── Send Message (entry point called from chatbox) ─────

async function splitScreenInternalSend(message, options = {}) {
    const sendOptions = options && typeof options === 'object' ? options : {};
    const preserveComposerAfterDispatch = Boolean(sendOptions.preserveComposerAfterDispatch);
    const restoreDraftOnFailure = sendOptions.restoreDraftOnFailure !== false;
    const normalizedMessage = String(message || '').trim();
    const composerContext = splitScreenInternalGetComposerContextSnapshot();
    if (!normalizedMessage && !splitScreenInternalComposerContextHasContent(composerContext)) {
        return false;
    }

    const targetSides = splitScreenInternalResolveTargetSides(sendOptions.target || splitScreenInternalState.sendTarget);
    const missingModelSides = targetSides.filter((side) => !splitScreenInternalGetPanelModelId(side));
    if (missingModelSides.length) {
        const sideNames = missingModelSides.map(splitScreenInternalGetTranslatedSideLabel).join(', ');
        notifyError?.(splitScreenInternalSplitScreenTf(
            'split_screen_missing_target_models',
            'Select a model for: {sides}',
            { sides: sideNames }
        ));
        if (restoreDraftOnFailure) splitScreenInternalRestoreSplitDraftAfterFailedSend(normalizedMessage);
        return false;
    }

    const busySides = targetSides.filter((side) => splitScreenInternalIsSideGenerating(side));
    if (busySides.length) {
        notifyWarning?.(splitScreenInternalSplitScreenT(
            'split_screen_target_still_generating',
            'Wait for the targeted panel response to finish or stop it before sending again.'
        ));
        if (restoreDraftOnFailure) splitScreenInternalRestoreSplitDraftAfterFailedSend(normalizedMessage);
        return false;
    }

    const loadingSides = targetSides.filter((side) => splitScreenInternalIsSideLoading(side));
    if (loadingSides.length) {
        notifyWarning?.(splitScreenInternalSplitScreenT(
            'split_screen_target_still_loading',
            'Wait for the targeted panel to finish loading before sending.'
        ));
        if (restoreDraftOnFailure) splitScreenInternalRestoreSplitDraftAfterFailedSend(normalizedMessage);
        return false;
    }

    if (splitScreenInternalSplitComposerDispatchInProgress) {
        notifyWarning?.(splitScreenInternalSplitScreenT(
            'split_screen_target_still_generating',
            'Wait for the targeted panel response to finish or stop it before sending again.'
        ));
        if (restoreDraftOnFailure) splitScreenInternalRestoreSplitDraftAfterFailedSend(normalizedMessage);
        return false;
    }

    const releaseComposerDispatch = () => {
        if (!splitScreenInternalSplitComposerDispatchInProgress) return;
        splitScreenInternalSplitComposerDispatchInProgress = false;
        splitScreenInternalRefreshComposerControls();
        splitScreenInternalFlushInterruptedDraftIfReady();
    };
    splitScreenInternalSplitComposerDispatchInProgress = true;
    splitScreenInternalRefreshComposerControls();

    // Notes uses one shared editor. Flush it once before fan-out so a
    // dual-panel turn cannot race two saves or send only one side.
    if (typeof window.NotesToolSidebar?.flushPendingEdits === 'function') {
        try {
            const notesSaved = await window.NotesToolSidebar.flushPendingEdits();
            if (!notesSaved) {
                notifyError(splitScreenInternalSplitScreenT('split_screen_send_cancelled_notes_unsaved', 'Message send cancelled because note changes could not be saved'));
                if (restoreDraftOnFailure) splitScreenInternalRestoreSplitDraftAfterFailedSend(normalizedMessage);
                releaseComposerDispatch();
                return false;
            }
        } catch (error) {
            notifyError(error?.message || splitScreenInternalSplitScreenT('notes_error_save_note', 'Failed to save note'));
            if (restoreDraftOnFailure) splitScreenInternalRestoreSplitDraftAfterFailedSend(normalizedMessage);
            releaseComposerDispatch();
            return false;
        }
    }

    const requestSettlements = new Map();
    const settledFailures = [];
    let clearedComposerGuard = null;
    const handleRequestSettled = (side, accepted, generationId = '') => {
        if (requestSettlements.has(side)) return;
        requestSettlements.set(side, Boolean(accepted));
        if (accepted) {
            try {
                sendOptions.onRequestAccepted?.(String(generationId || ''), side);
            } catch (error) {
                console.error(`Failed to notify queued ${side} request acceptance`, error);
            }
        }
        if (requestSettlements.size !== targetSides.length) return;

        if (
            settledFailures.length === 0
            && targetSides.every((targetSide) => requestSettlements.get(targetSide) === true)
        ) {
            const restoreEligible = splitScreenInternalIsComposerEligibleForAcceptedFailureRestore(composerContext);
            if (!preserveComposerAfterDispatch) {
                splitScreenInternalClearComposerContextAfterSuccessfulSend();
                clearedComposerGuard = {
                    ...splitScreenInternalCaptureSplitComposerRestoreGuard(),
                    restoreEligible,
                };
            }
        }
        releaseComposerDispatch();
    };
    const handleSettledFailure = (failure) => {
        settledFailures.push(failure);
        // Make a failed target retryable as soon as it settles; do not wait
        // for another panel's potentially long-running response.
        splitScreenInternalSetSendTarget(settledFailures.length === 1 ? failure.side : 'both');
        // The shared composer draft belongs to the fan-out as a whole, so
        // restore it only for the first failed target.
        if (settledFailures.length === 1) {
            splitScreenInternalRestoreSplitComposerAfterFailedSend(
                normalizedMessage,
                composerContext,
                clearedComposerGuard,
                { enabled: restoreDraftOnFailure }
            );
        }
        if (!failure.rateLimited && failure.error) {
            notifyError?.(splitScreenInternalSplitScreenTf(
                'split_screen_panel_send_failed',
                '{side} panel: {message}',
                {
                    side: splitScreenInternalGetTranslatedSideLabel(failure.side),
                    message: failure.error,
                }
            ));
        }
    };
    const results = await Promise.all(targetSides.map(async (side) => {
        let result;
        try {
            result = await splitScreenInternalSendToPanel(normalizedMessage, side, composerContext, {
                onRequestSettled: (accepted, generationId) => handleRequestSettled(side, accepted, generationId),
            });
        } catch (error) {
            console.error(`Unexpected ${side} split-screen send failure`, error);
            handleRequestSettled(side, false);
            result = {
                ok: false,
                side,
                error: error?.message || splitScreenInternalSplitScreenT('split_screen_send_failed', 'Failed to send message'),
                rateLimited: false,
            };
        }
        if (!result?.ok) {
            handleSettledFailure(result || {
                ok: false,
                side,
                error: splitScreenInternalSplitScreenT('split_screen_send_failed', 'Failed to send message'),
                rateLimited: false,
            });
        }
        return result;
    }));
    targetSides.forEach((side) => {
        if (!requestSettlements.has(side)) {
            handleRequestSettled(side, false);
        }
    });
    releaseComposerDispatch();
    const failedResults = results.filter((result) => !result?.ok);
    if (failedResults.length) {
        return false;
    }

    return true;
}
