async function sendMessage(message="", attaching=false, attachGenerationId=null, options={}) {
    const sendOptions = options && typeof options === 'object' ? options : {};
    const preserveComposerAfterDispatch = Boolean(sendOptions.preserveComposerAfterDispatch);
    const restoreDraftOnFailure = sendOptions.restoreDraftOnFailure !== false;
    // Capture before the first await. Queue processing restores the user's live
    // composer as soon as this function yields.
    const composerContext = !attaching ? captureChatSendComposerContext() : null;
    const generationRequestId = attaching && attachGenerationId
        ? String(attachGenerationId)
        : generateUUID();
    let requestAccepted = false;
    let generationTerminalNotified = false;
    const queueOwnsRequest = typeof sendOptions.onRequestAccepted === 'function';
    const notifyRequestAccepted = () => {
        if (requestAccepted) return;
        requestAccepted = true;
        try {
            sendOptions.onRequestAccepted?.(generationRequestId);
        } catch (error) {
            console.error('Failed to notify queued request acceptance', error);
        }
    };
    const notifyGenerationTerminal = (status) => {
        if (generationTerminalNotified || (!requestAccepted && queueOwnsRequest)) return;
        generationTerminalNotified = true;
        try {
            window.messageQueue?.handleGenerationTerminal?.({
                generationId: generationRequestId,
                surface: 'chat',
                status: String(status || 'finished'),
            });
        } catch (error) {
            console.error('Failed to notify queued generation completion', error);
        }
    };
    const messageAttachments = Array.isArray(composerContext?.attachments)
        ? composerContext.attachments
        : [];
    const messageChatReferences = Array.isArray(composerContext?.chatReferencePayload)
        ? composerContext.chatReferencePayload
        : [];
    let last_appended_message_type = "";
    let assistantContentCount = 0;
    let assistantReasoningCount = 0;
    let messageId = "";
    let files = [];
    let temp_reasoning_time = 0;
    let regenState = null;
    let trackedGenerationId = attaching && attachGenerationId ? String(attachGenerationId) : '';
    const chatContainer = document.getElementById('chatContainer');
    const modelId = String(
        sendOptions.modelId
        || document.getElementById('modelSelect')?.getAttribute('data-model-id')
        || ''
    );
    const realtimeTextSessionActive = Boolean(
        !attaching
        && window.realtimeCall
        && typeof window.realtimeCall.isActive === 'function'
        && window.realtimeCall.isActive()
    );
    if (!attaching && !realtimeTextSessionActive && !modelId.trim()) {
        window.showChatModelUnavailableFeedback?.();
        return false;
    }
    if (
        !sendOptions.customModelSettings
        && typeof window.validateCurrentModelSettings === 'function'
        && !window.validateCurrentModelSettings()
    ) {
        notifyError(getChatPreviewTranslation(
            'model_settings_invalid_structured_value',
            'Correct the invalid model setting before sending.'
        ));
        return;
    }
    const customModelSettings = sendOptions.customModelSettings
        && typeof sendOptions.customModelSettings === 'object'
        ? { ...sendOptions.customModelSettings }
        : (typeof window.getCurrentModelSettingValues === 'function'
            ? window.getCurrentModelSettingValues()
            : {});
    let chatId = chatContainer.getAttribute('data-chat-id');
    const startedWithExistingChat = Boolean(String(chatId || '').trim());
    let tempModeActive = Boolean(
        !attaching
        && !chatId
        && typeof window.isTemporaryChatModeActive === 'function'
        && window.isTemporaryChatModeActive()
    );

    if (
        !attaching
        && !chatId
        && !tempModeActive
        && typeof window.hasUnsavedTemporaryConversation === 'function'
        && window.hasUnsavedTemporaryConversation()
        && typeof window.saveTemporaryChatConversation === 'function'
    ) {
        const savedChatId = await window.saveTemporaryChatConversation({
            silentOnEmpty: true,
            suppressToast: true,
        });
        if (!savedChatId) {
            return;
        }
        chatId = savedChatId;
        tempModeActive = false;
    }

    const pendingSyntheticMessageId = !attaching ? generateUUID() : null;
    let usedSyntheticMessageId = false;
    let optimisticMessageInitialized = false;
    let streamReceivedAnyEvent = false;
    let generationTerminalStatus = 'interrupted';
    if (chatId && typeof window.moveChatRowToTop === 'function') {
        window.moveChatRowToTop(chatId);
    }
    if (chatId && trackedGenerationId) {
        window.ChatAttention?.trackGeneration(chatId, trackedGenerationId);
    }

    const realtimeCallIsActive = Boolean(
        !attaching
        && window.realtimeCall
        && typeof window.realtimeCall.isActive === 'function'
        && window.realtimeCall.isActive()
        && typeof window.realtimeCall.sendText === 'function'
    );
    const realtimeCallConnecting = Boolean(
        !attaching
        && window.realtimeCall
        && typeof window.realtimeCall.getSessionState === 'function'
        && window.realtimeCall.getSessionState()?.connecting
    );

    if (realtimeCallConnecting && !realtimeCallIsActive) {
        notifyWarning?.(getChatPreviewTranslation('chat_realtime_connecting_wait', 'Realtime call is still connecting. Please wait a moment.'));
        return;
    }

    if (realtimeCallIsActive) {
        if (hasUnsupportedRealtimeRequestContext(composerContext)) {
            // Realtime input currently accepts text and files only. Reject the
            // whole turn so one-request context is never silently omitted or
            // consumed by the successful-send cleanup path.
            notifyWarning?.(getChatPreviewTranslation(
                'chat_realtime_request_context_unsupported',
                'Prompts, notes, and selected passages are not supported during a live call. End the call to send them. They remain attached.',
            ));
            return false;
        }
        const payloadImageIds = composerContext?.imageIds || [];
        const payloadVideoIds = composerContext?.videoIds || [];
        const payloadAudioIds = composerContext?.audioIds || [];
        const payloadDocumentIds = composerContext?.documentIds || [];
        const selectedChatReferenceIds = composerContext?.chatReferenceIds || [];
        const allFileIds = Array.from(
            new Set(
                [
                    ...payloadImageIds,
                    ...payloadVideoIds,
                    ...payloadAudioIds,
                    ...payloadDocumentIds,
                ].map((id) => String(id || '').trim()).filter(Boolean)
            )
        );

        const normalizedMessage = String(message || '').trim();
        if (selectedChatReferenceIds.length) {
            notifyWarning?.(getChatPreviewTranslation('chat_realtime_references_unsupported', 'Chat references are not supported while a live call is active. End the call to send them.'));
            return;
        }
        const realtimeMcpServerIds = customModelSettings?.settings?.enabled_mcp_servers;
        if (Array.isArray(realtimeMcpServerIds) && realtimeMcpServerIds.length) {
            // Realtime transport currently accepts text and files only. Do not
            // pretend a connector-backed turn was accepted or clear the user's
            // connector selection; let them end the call and send it normally.
            notifyWarning?.(getChatPreviewTranslation(
                'chat_realtime_connectors_unsupported',
                'Connectors are not supported while a live call is active. End the call to use them.',
            ));
            return;
        }
        if (!normalizedMessage && !allFileIds.length) {
            return;
        }

        clearUnsupportedFileWarningState();
        const sent = await window.realtimeCall.sendText(normalizedMessage, { fileIds: allFileIds });
        if (!sent) {
            notifyError?.(getChatPreviewTranslation('chat_realtime_send_failed', 'Failed to send realtime turn.'));
            return;
        }

        notifyRequestAccepted();
        if (!preserveComposerAfterDispatch) {
            // Only remove file IDs acknowledged by the realtime send. Context
            // chips are unsupported here and must never be consumed.
            clearAcceptedRealtimeFileAttachments(allFileIds);
        }
        notifyGenerationTerminal('finished');
        return;
    }

    // Use one client-owned ID for UI ownership, server stream identity, and
    // cancellation. Attach mode adopts the already-running server ID.
    chatContainer.setAttribute('data-active-generation', generationRequestId);
    const generationTransport = beginChatGenerationTransport(generationRequestId);

    let generationFinalized = false;
    const finalizeGenerationState = ({ clearActiveAttr = true } = {}) => {
        if (generationFinalized) {
            return;
        }
        generationFinalized = true;
        if (typeof window.resetGenerationUIState === 'function') {
            window.resetGenerationUIState({ clearActiveAttr });
        } else {
            window.currentGenerationId = null;
            window.pendingCancelGeneration = false;
            if (clearActiveAttr && chatContainer) {
                chatContainer.removeAttribute('data-active-generation');
            }
            if (typeof window.endGenerationUI === 'function') {
                window.endGenerationUI();
            }
        }
        clearVisibilityReconnectState();
        releaseChatGenerationTransport(generationRequestId);
        if (!generationTransport.cancelled && !requestAccepted) {
            notifyGenerationTerminal('error');
        }
        if (typeof window.flushInterruptedDraftSend === 'function') {
            Promise.resolve().then(() => {
                try {
                    window.flushInterruptedDraftSend();
                } catch (error) {
                    console.error('Failed to flush interrupted chat draft send', error);
                }
            });
        }
    };

    const markGenerationStart = () => {
        window.startGenerationUI();
        window.pendingCancelGeneration = false;
        window.currentGenerationId = generationRequestId;
    };

    const requestInlineCancel = (generationId) => {
        if (!generationId) {
            return;
        }
        (async () => {
            try {
                await requestGenerationCancellation(String(generationId));
            } catch (_) {}
        })();
    };

    const prepareRegenerationContainer = (referenceId, retryCount) => {
        const regenTarget = prepareAssistantRegenerationTarget(referenceId, retryCount, { announce: true });
        if (!regenTarget || !regenTarget.newMessageId) {
            return null;
        }

        if (typeof appendLoading === 'function') {
            assistantReasoningCount = appendLoading(regenTarget.newMessageId, assistantReasoningCount);
            last_appended_message_type = 'loading';
        }

        return regenTarget;
    };

    const initializeOptimisticOutgoingMessage = () => {
        if (attaching || optimisticMessageInitialized) {
            return;
        }
        const fallbackMessageId = pendingSyntheticMessageId || generateUUID();
        messageId = fallbackMessageId;
        usedSyntheticMessageId = true;
        generationTransport.messageId = messageId;

        files = messageAttachments;
        const chatReferences = messageChatReferences;
        if (tempModeActive) {
            if (typeof window.prepareTemporaryChatConversationView === 'function') {
                window.prepareTemporaryChatConversationView();
            }
        } else if (!chatId) {
            if (typeof window.preparePendingChatConversationView === 'function') {
                window.preparePendingChatConversationView();
            } else if (typeof window.showChatContainer === 'function') {
                window.showChatContainer();
            }
        }
        appendUserContent(messageId, message, files, chatReferences);
        appendAssistantContainer(messageId, { announce: true });
        generationTransport.transcriptRoot = document.getElementById('chatAreaContainer');
        const optimisticUserContainer = document.querySelector(`.user-message-container[data-user-message-id="${CSS.escape(messageId)}"]`);
        if (optimisticUserContainer) {
            optimisticUserContainer.dataset.optimisticMessage = 'true';
        }
        const optimisticUserAnchor = document.getElementById('u-' + messageId);
        if (optimisticUserAnchor) {
            optimisticUserAnchor.dataset.optimisticMessage = 'true';
        }
        const optimisticAssistantContainer = document.getElementById('a-' + messageId);
        if (optimisticAssistantContainer) {
            optimisticAssistantContainer.dataset.optimisticMessage = 'true';
        }
        assistantReasoningCount = appendLoading(messageId, assistantReasoningCount);
        last_appended_message_type = 'loading';
        optimisticMessageInitialized = true;

        if (typeof scrollUserMessageToTop === 'function') {
            requestAnimationFrame(() => scrollUserMessageToTop(messageId));
        }
    };

    const rollbackOptimisticOutgoingMessage = () => {
        if (!optimisticMessageInitialized || !messageId) {
            return;
        }

        removeLoading(messageId);

        const userAnchor = document.getElementById('u-' + messageId);
        const userArea = userAnchor?.closest('.user-message-area');
        if (userArea) {
            userArea.remove();
        }

        const assistantContainerEl = document.getElementById('a-' + messageId);
        if (assistantContainerEl) {
            assistantContainerEl.remove();
        }
        const chatAreaEl = document.getElementById('chatArea');
        const chatAreaContainerEl = document.getElementById('chatAreaContainer');
        window.ChatScrollCoordinator?.cancel(chatAreaEl, {
            container: chatAreaContainerEl,
            messageId,
            removeSpacer: true,
        });

        optimisticMessageInitialized = false;

        if (!startedWithExistingChat && !chatId) {
            const hasTranscript = Boolean(
                chatAreaContainerEl?.querySelector('.user-message-area')
                || chatAreaContainerEl?.querySelector('.assistant-message-container')
            );
            if (!hasTranscript && typeof window.showChatStartContainer === 'function') {
                window.showChatStartContainer({ skipHistory: true });
            } else if (chatContainer) {
                chatContainer.removeAttribute('data-pending-chat');
            }
        }
    };

    markGenerationStart();

    // add a data-active-generation true

    let res;
    if (attaching) {
        clearUnsupportedFileWarningState();
        try {
            res = await window.authedFetch(`/api/v1/chats/attach?generation_id=${attachGenerationId}`, {
                method: 'GET',
                signal: generationTransport.abortController.signal,
            });
        } catch (error) {
            const wasCancelled = generationTransport.cancelled;
            if (chatContainer?.getAttribute('data-active-generation') === generationRequestId) {
                finalizeGenerationState();
            }
            if (wasCancelled) {
                return;
            }
            notifyError(formatChatPreviewTranslation('chat_attach_generation_error', 'Error attaching to generation {id}', { id: attachGenerationId }));
            return;
        }
        if (!res.ok) {
            notifyError(formatChatPreviewTranslation('chat_attach_generation_error', 'Error attaching to generation {id}', { id: attachGenerationId }));
            finalizeGenerationState();
            return;
        }
        notifyRequestAccepted();
    } else {
        if (typeof window.NotesToolSidebar?.flushPendingEdits === 'function') {
            const notesSaved = await window.NotesToolSidebar.flushPendingEdits();
            if (!notesSaved) {
                finalizeGenerationState();
                return;
            }
        }
        const payloadImageIds = composerContext?.imageIds || [];
        const payloadVideoIds = composerContext?.videoIds || [];
        const payloadAudioIds = composerContext?.audioIds || [];
        const payloadDocumentIds = composerContext?.documentIds || [];
        const payloadSkillIds = composerContext?.skillIds || [];
        const payloadNoteIds = composerContext?.noteIds || [];
        const payloadPromptIds = composerContext?.promptIds || [];
        const payloadReferenceParts = composerContext?.referenceParts || [];
        const payloadChatReferenceIds = composerContext?.chatReferenceIds || [];
        const payloadSubagentTargets = Object.prototype.hasOwnProperty.call(sendOptions, 'subagentTargets')
            ? sendOptions.subagentTargets
            : composerContext?.subagentTargets;
        const tempChatHistory = tempModeActive ? serializeTemporaryChatHistory() : '';

        const projectId = chatContainer.getAttribute('data-project-id') || '';
        let byokPayload = null;
        try {
            if (typeof window.BYOK?.buildRequestPayloadForModel === 'function') {
                byokPayload = window.BYOK.buildRequestPayloadForModel(modelId, customModelSettings);
            }
        } catch (error) {
            finalizeGenerationState();
            notifyError(error.message || getChatPreviewTranslation('chat_byok_prepare_failed', 'Failed to prepare BYOK request.'));
            return;
        }
        initializeOptimisticOutgoingMessage();
        const body = JSON.stringify({
            payload: {
                generation_id: generationRequestId,
                model_id: byokPayload ? '' : modelId,
                message,
                chat_id: tempModeActive ? '' : (chatId || ''),
                image_ids: payloadImageIds,
                video_ids: payloadVideoIds,
                audio_ids: payloadAudioIds,
                document_ids: payloadDocumentIds,
                skill_ids: payloadSkillIds.length ? payloadSkillIds : null,
                note_ids: payloadNoteIds,
                prompt_ids: payloadPromptIds.length ? payloadPromptIds : null,
                reference_parts: payloadReferenceParts.length ? payloadReferenceParts : null,
                chat_reference_ids: payloadChatReferenceIds.length ? payloadChatReferenceIds : null,
                subagent_targets: Array.isArray(payloadSubagentTargets) ? payloadSubagentTargets : null,
                project_id: projectId,
                temp_chat: tempChatHistory,
            },
            custom_settings: byokPayload ? {} : (customModelSettings && Object.keys(customModelSettings).length ? customModelSettings : {}),
            byok: byokPayload || {}
        });
        clearUnsupportedFileWarningState();
        try {
            res = await window.authedFetch(`/api/v1/chats/send`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: body,
                signal: generationTransport.abortController.signal,
            });
        } catch (error) {
            if (generationTransport.cancelled) {
                // The stream-level finally block has not been entered yet, so
                // finish the optimistic partial response directly.
                if (messageId) {
                    clearMediaGenPlaceholderForNonFileEvent(messageId);
                }
                window.finalizeCancelledAssistantStream?.(
                    messageId,
                    document.getElementById('chatAreaContainer')
                );
                if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleStreamEnd === 'function') {
                    try {
                        window.NotesToolSidebar.handleStreamEnd(messageId);
                    } catch (cleanupError) {
                        console.error('Failed to clean up notes live preview after cancellation', cleanupError);
                    }
                }
                if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleStreamEnd === 'function') {
                    try {
                        window.canvasMarkdownWidget.handleStreamEnd(messageId);
                    } catch (cleanupError) {
                        console.error('Failed to clean up canvas preview after cancellation', cleanupError);
                    }
                }
                if (window.slidePresentationWidget && typeof window.slidePresentationWidget.handleStreamEnd === 'function') {
                    try {
                        window.slidePresentationWidget.handleStreamEnd(messageId);
                    } catch (cleanupError) {
                        console.error('Failed to clean up slide presentation preview after cancellation', cleanupError);
                    }
                }
                finalizeGenerationState();
                return;
            }
            rollbackOptimisticOutgoingMessage();
            if (!tempModeActive && restoreDraftOnFailure) {
                restoreChatDraftAfterFailedSend(message);
            }
            finalizeGenerationState();
            notifyError(error.message || getChatPreviewTranslation('chat_send_failed_retry', 'Failed to send message. Please try again later.'));
            return;
        }
        if (!res.ok) {
            const errorData = await res.json().catch(() => ({}));
            const sendFailedMessage = getChatPreviewTranslation('chat_send_failed_retry', 'Failed to send message. Please try again later.');
            const detail = resolveApiErrorMessage(errorData, sendFailedMessage);

            if (res.status === 429) {
                rollbackOptimisticOutgoingMessage();
                showRateLimitCard({
                    container: document.getElementById('chatAreaContainer'),
                    errorData,
                    fallbackDetail: detail,
                });
                if (!tempModeActive && restoreDraftOnFailure) {
                    restoreChatDraftAfterFailedSend(message);
                }
                finalizeGenerationState();
                return;
            }

            const isAdmin = typeof localStorage !== 'undefined' && localStorage.getItem('is_admin') === 'true';
            const isSafeModelSelectionError = errorData?.detail?.code === 'chat_model_required';
            const shouldExposeDetail = Boolean(byokPayload || isAdmin || isChatReferenceApiError(errorData) || isSafeModelSelectionError);
            rollbackOptimisticOutgoingMessage();
            if (!tempModeActive && restoreDraftOnFailure) {
                restoreChatDraftAfterFailedSend(message);
            }
            notifyError(shouldExposeDetail ? detail : sendFailedMessage);
            finalizeGenerationState();
            return;
        }
        notifyRequestAccepted();
        if (!preserveComposerAfterDispatch) {
            clearChatRequestFiles({ preserveSkills: true });
            if (typeof window.clearAllReferenceParts === 'function') {
                window.clearAllReferenceParts();
            }
        }
    }
    const reader = res.body.getReader();
    if (activeChatGenerationTransport?.generationId === generationRequestId) {
        activeChatGenerationTransport.reader = reader;
    }
    const decoder = new TextDecoder();
    let buffer = '';

    function findSidebarRowByChatId(chatIdToFind) {
        const normalizedChatId = String(chatIdToFind || '').trim();
        if (!normalizedChatId) {
            return null;
        }

        const containers = [
            document.getElementById('pinnedChatsContainer'),
            document.getElementById('chatsContainer'),
        ].filter(Boolean);

        for (const container of containers) {
            const row = Array.from(container.querySelectorAll('.sidebar-element'))
                .find((element) => element.dataset.chatId === normalizedChatId);
            if (row) {
                return row;
            }
        }

        return null;
    }

    function ensureSidebarRowForChat(chatIdToEnsure, { initialTitle = '', projectId = null } = {}) {
        const normalizedChatId = String(chatIdToEnsure || '').trim();
        if (!normalizedChatId) {
            return null;
        }

        let row = findSidebarRowByChatId(normalizedChatId);
        const chatsContainerEl = document.getElementById('chatsContainer');

        if (!row && chatsContainerEl && typeof createChatRow === 'function') {
            const nowIso = new Date().toISOString();
            row = createChatRow({
                id: normalizedChatId,
                title: initialTitle,
                project_id: projectId,
                last_updated_at: nowIso,
                pinned_position: null,
            });

            if (row) {
                row.dataset.lastUpdatedAt = nowIso;
                const firstChatRow = chatsContainerEl.querySelector('.sidebar-element');
                if (firstChatRow) {
                    chatsContainerEl.insertBefore(row, firstChatRow);
                } else {
                    chatsContainerEl.insertBefore(row, chatsContainerEl.firstChild);
                }
                if (typeof refreshTimeDividers === 'function') {
                    refreshTimeDividers(chatsContainerEl);
                }
            }
        }

        if (row) {
            const link = row.querySelector('a.sidebar-element-button');
            if (link) {
                link.href = `/chat/${encodeURIComponent(normalizedChatId)}`;
            }
        }

        const historySectionEl = document.getElementById('history');
        if (historySectionEl) {
            historySectionEl.style.display = '';
        }

        return row;
    }

    function applyGeneratedSidebarTitle(chatIdToUpdate, nextTitle) {
        const normalizedChatId = String(chatIdToUpdate || '').trim();
        if (!normalizedChatId) {
            return;
        }

        const generatedTitle = typeof nextTitle === 'string' ? nextTitle : '';
        const row = ensureSidebarRowForChat(normalizedChatId, { initialTitle: generatedTitle });
        if (!row) {
            return;
        }

        row.dataset.chatTitle = generatedTitle;
        const titleEl = row.querySelector('a.sidebar-element-button > p');
        if (titleEl) {
            if (typeof typewriteText === 'function' && generatedTitle) {
                typewriteText(titleEl, generatedTitle);
            } else {
                titleEl.textContent = generatedTitle;
            }
        }

        if (typeof updateTabTitleIfActive === 'function') {
            updateTabTitleIfActive(normalizedChatId);
        }
    }

    try {
    while (true) {
        let readResult;
        try {
            readResult = await reader.read();
        } catch (error) {
            if (generationTransport.cancelled) {
                break;
            }
            const chatContainer = document.getElementById('chatContainer');
            const chatIdForReconnect = chatContainer ? chatContainer.getAttribute('data-chat-id') : '';
            const isBackgrounded = typeof document !== 'undefined' && document.hidden;
            const scheduledReconnect = Boolean(
                isBackgrounded &&
                chatIdForReconnect &&
                typeof scheduleVisibilityReconnect === 'function' &&
                scheduleVisibilityReconnect(chatIdForReconnect)
            );
            if (!streamReceivedAnyEvent && !scheduledReconnect) {
                rollbackOptimisticOutgoingMessage();
                if (!tempModeActive && restoreDraftOnFailure) {
                    restoreChatDraftAfterFailedSend(message);
                }
            }
            if (scheduledReconnect) {
                if (typeof notifyWarning === 'function') {
                    notifyWarning(getChatPreviewTranslation('chat_connection_paused_background', 'Connection paused in background. Resuming when you return.'));
                }
            } else {
                notifyError(getChatPreviewTranslation('chat_connection_interrupted_retry', 'Connection interrupted. Please try again.'));
            }
            break;
        }
        const { done, value } = readResult;
        if (generationTransport.cancelled) break;
        const chunk = done
            ? decoder.decode()
            : decoder.decode(value, { stream: true });
        buffer += chunk;
        // Normalize line endings and split into lines
        const parts = buffer.replace(/\r\n/g, '\n').split('\n');
        // At EOF the final record may not have a trailing newline.
        if (done) {
            buffer = '';
        } else {
            buffer = parts.pop() || '';
        }
        for (const line of parts) {
            const trimmed = line.trim();
            if (!trimmed) {
                continue;
            }
            const chatContainer = document.getElementById('chatContainer');
            const RawUuid = chatContainer?.getAttribute('data-active-generation');
            let obj;
            try {
                obj = JSON.parse(trimmed);
            } catch (parseError) {
                console.warn('Failed to parse chat stream line:', parseError);
                continue;
            }
            const isLateTitleEvent = (obj?.t === 't_g' || obj?.t === 'n_t')
                && Boolean(chatId)
                && chatContainer?.getAttribute('data-chat-id') === String(chatId);
            if (RawUuid !== generationRequestId && !isLateTitleEvent) {
                if (typeof flushAssistantStreamingContentForMessage === 'function') {
                    flushAssistantStreamingContentForMessage(
                        messageId,
                        document.getElementById('chatAreaContainer'),
                        { discard: true },
                    );
                }
                return;
            }
            if (RawUuid === generationRequestId && typeof flushAssistantStreamingContentBeforeEvent === 'function') {
                flushAssistantStreamingContentBeforeEvent(
                    messageId,
                    last_appended_message_type,
                    obj?.t,
                    document.getElementById('chatAreaContainer'),
                );
            }
            const hasMaterializedPayload = obj?.t === 'm_id'
                || obj?.t === 'regen'
                || ['c', 'r', 't_c', 't_cd', 'e', 'wg', 'subagent_evt', 'deep_research_evt', 'slide_presentation_evt', 'latex_pdf_evt', 'canvas_evt', 'notes_evt', 'f', 'a_id', 't_g', 'n_t'].includes(obj?.t);
            if (hasMaterializedPayload) {
                streamReceivedAnyEvent = true;
            }
            if (obj.t === 'n_c') {
                // This returns the new chat id
                const newChatId = String(obj.d ?? '').trim();
                if (!newChatId) {
                    continue;
                }
                chatId = newChatId;

                if (startedWithExistingChat) {
                    await loadChatView(newChatId, true);
                } else {
                    chatContainer?.setAttribute('data-chat-id', newChatId);
                    if (typeof updateTabTitleIfActive === 'function') {
                        updateTabTitleIfActive(newChatId);
                    }
                    if (typeof window.showChatContainer === 'function') {
                        window.showChatContainer();
                    }
                    history.pushState({ chatId: newChatId }, '', `/chat/${encodeURIComponent(newChatId)}`);
                }

                try {
                    const projectId = chatContainer?.getAttribute('data-project-id') || null;
                    ensureSidebarRowForChat(newChatId, {
                        initialTitle: '',
                        projectId,
                    });

                    // Also add to project sidebar if in a project context
                    if (projectId && typeof window.addOrUpdateProjectChatRow === 'function') {
                        try {
                            window.addOrUpdateProjectChatRow(newChatId, '');
                        } catch (_) {}
                    }
                } catch (err) {
                    console.error('Failed to create sidebar entry for new chat:', err);
                }
            } else if (obj.t === "s") {
                // This return the generation id
                const generationId = obj.d;
                const normalizedId = typeof generationId === 'string' ? generationId : String(generationId ?? '');
                if (normalizedId) {
                    trackedGenerationId = normalizedId;
                    window.ChatAttention?.trackGeneration(chatId, normalizedId);
                    try {
                        window.currentGenerationId = normalizedId;
                    } catch (_) {}
                }
                if (window.pendingCancelGeneration) {
                    try {
                        window.pendingCancelGeneration = false;
                    } catch (_) {}
                    if (typeof window.cancelGeneration === 'function') {
                        try {
                            window.cancelGeneration();
                        } catch (_) {}
                    } else if (normalizedId) {
                        requestInlineCancel(normalizedId);
                    }
                }
            } else if (obj.t === "m_id") {
                if (usedSyntheticMessageId && messageId) {
                    if (typeof bindOptimisticMessageToServerMessage === 'function') {
                        bindOptimisticMessageToServerMessage(messageId, obj.d);
                    }
                    continue;
                }
                // This returns the message id
                messageId = obj.d;
                generationTransport.messageId = messageId;
                generationTransport.transcriptRoot = document.getElementById('chatAreaContainer');
                // Use the immutable composer context captured before preflight.
                files = messageAttachments;
                const chatReferences = messageChatReferences;
                appendUserContent(messageId, message, files, chatReferences);
                appendAssistantContainer(messageId, { announce: true });
                assistantReasoningCount = appendLoading(messageId, assistantReasoningCount);
                last_appended_message_type = "loading";
                // Scroll user message to top with gap
                if (typeof scrollUserMessageToTop === 'function') {
                    requestAnimationFrame(() => scrollUserMessageToTop(messageId));
                }
            } else if (obj.t === 'regen') {
                const regenData = obj.d || {};
                const userMessageId = regenData.user_message_id || regenData.userMessageId || '';
                const retryCountRaw = regenData.retry_count;
                const retryCount = typeof retryCountRaw === 'number'
                    ? retryCountRaw
                    : parseInt(retryCountRaw || '0', 10) || 0;

                const regenPrep = prepareRegenerationContainer(userMessageId, retryCount);
                if (regenPrep && regenPrep.newMessageId) {
                    messageId = regenPrep.newMessageId;
                    generationTransport.messageId = messageId;
                    generationTransport.transcriptRoot = document.getElementById('chatAreaContainer');
                    regenState = {
                        referenceId: userMessageId,
                        retryCount,
                        messageId: regenPrep.newMessageId,
                    };
                } else {
                    regenState = {
                        referenceId: userMessageId,
                        retryCount,
                        messageId,
                    };
                }
                continue;
            } else if (obj.t === "c") {
                if (last_appended_message_type === "loading") {
                    removeLoading(messageId);
                    last_appended_message_type = "";
                }
                const mediaGenerationFailed = clearMediaGenPlaceholderForNonFileEvent(messageId);
                const previousMessageType = mediaGenerationFailed ? 'media-generation-failed' : last_appended_message_type;
                assistantContentCount = appendAssistantContent(messageId, obj.d, previousMessageType, assistantContentCount, temp_reasoning_time, assistantReasoningCount);
                last_appended_message_type = "c";
            } else if (obj.t === "r") {
                const wasLoading = last_appended_message_type === "loading";
                if (wasLoading) {
                    assistantReasoningCount = expandLoading(messageId, assistantReasoningCount);
                }
                const mediaGenerationFailed = clearMediaGenPlaceholderForNonFileEvent(messageId);
                const previousMessageType = mediaGenerationFailed
                    ? 'media-generation-failed'
                    : (wasLoading ? "r" : last_appended_message_type);
                assistantReasoningCount = appendAssistantReasoning(messageId, obj.d, previousMessageType, assistantReasoningCount);
                last_appended_message_type = "r";
            } else if (obj.t === "t_c") {
                // Tool call
                const wasLoading = last_appended_message_type === "loading";
                if (wasLoading) {
                    assistantReasoningCount = expandLoading(messageId, assistantReasoningCount);
                }
                const toolDescriptor = obj.d || {};
                const resolvedToolName = typeof toolDescriptor === "string"
                    ? toolDescriptor
                    : (toolDescriptor.name || "");
                const resolvedToolArgs = toolDescriptor.args ?? obj.c;
                const resolvedToolCallId = typeof toolDescriptor === 'object'
                    ? (toolDescriptor.id || toolDescriptor.tool_call_id || '')
                    : '';
                const mediaGenerationFailed = transitionMediaGenPlaceholderForToolCall(
                    messageId,
                    resolvedToolName,
                    resolvedToolCallId
                );
                assistantReasoningCount = appendAssistantTool(
                    messageId,
                    mediaGenerationFailed ? 'media-generation-failed' : (wasLoading ? "r" : last_appended_message_type),
                    assistantReasoningCount,
                    null,
                    resolvedToolName,
                    resolvedToolArgs,
                    typeof toolDescriptor === 'object' ? toolDescriptor : null
                );
                last_appended_message_type = "t";

                if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleToolCallEvent === 'function') {
                    try {
                        window.canvasMarkdownWidget.handleToolCallEvent(obj, messageId);
                    } catch (_) {}
                }
                if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleToolCallEvent === 'function') {
                    try {
                        window.NotesToolSidebar.handleToolCallEvent(obj, messageId);
                    } catch (error) {
                        console.error('Failed to start notes live preview', error);
                    }
                }

                syncMediaGenPlaceholder(
                    messageId,
                    resolvedToolName,
                    resolvedToolCallId
                );
            } else if (obj.t === "t_e") {
                if (typeof applyAssistantToolError === 'function') {
                    applyAssistantToolError(messageId, obj.d || {}, { announce: true });
                }
            } else if (obj.t === "t_cd") {
                const toolDeltaUpdate = processAssistantToolDeltaStreamEvent(
                    messageId,
                    last_appended_message_type,
                    assistantReasoningCount,
                    obj.d
                );
                assistantReasoningCount = toolDeltaUpdate.assistantReasoningCount;
                last_appended_message_type = toolDeltaUpdate.lastAppendedMessageType;
                if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleToolCallDeltaEvent === 'function') {
                    try {
                        window.canvasMarkdownWidget.handleToolCallDeltaEvent(obj, messageId);
                    } catch (_) {}
                }
                if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleToolCallDeltaEvent === 'function') {
                    try {
                        window.NotesToolSidebar.handleToolCallDeltaEvent(obj, messageId);
                    } catch (error) {
                        console.error('Failed to update notes live preview', error);
                    }
                }
            } else if (obj.t === "r_f") {
                // Reasoning finished, change the assistant reasoning title
                temp_reasoning_time = obj.d;
            } else if (obj.t === "e") {
                generationTerminalStatus = 'error';
                if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleStreamEnd === 'function') {
                    try {
                        window.NotesToolSidebar.handleStreamEnd(messageId);
                    } catch (error) {
                        console.error('Failed to clean up notes live preview after stream error', error);
                    }
                }
                if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleStreamEnd === 'function') {
                    try {
                        window.canvasMarkdownWidget.handleStreamEnd(messageId);
                    } catch (error) {
                        console.error('Failed to clean up canvas preview after stream error', error);
                    }
                }
                if (window.slidePresentationWidget && typeof window.slidePresentationWidget.handleStreamEnd === 'function') {
                    try {
                        window.slidePresentationWidget.handleStreamEnd(messageId);
                    } catch (error) {
                        console.error('Failed to clean up slide presentation preview after stream error', error);
                    }
                }
                const translatedStreamError = obj.i18n_key
                    ? getChatPreviewTranslation(obj.i18n_key, obj.d || '')
                    : obj.d;
                const errorData = { detail: translatedStreamError };
                const detail = resolveApiErrorMessage(
                    errorData,
                    getChatPreviewTranslation('chat_send_failed_retry', 'Failed to send message. Please try again later.')
                );
                const isRateLimited = isRateLimitErrorPayload(errorData, detail);
                console.error('[chat-send] stream error event received', {
                    detail: obj.d,
                    resolvedDetail: detail,
                    isRateLimited,
                    messageId,
                    tempModeActive,
                    chatId: chatContainer?.getAttribute('data-chat-id') || '',
                    modelId,
                });

                if (last_appended_message_type === "loading" && messageId) {
                    removeLoading(messageId);
                    last_appended_message_type = "";
                } else if (messageId && !assistantReasoningCount && !assistantContentCount) {
                    // No other assistant output yet but error arrived
                    removeLoading(messageId);
                }

                if (!messageId) {
                    if (!tempModeActive) {
                        if (typeof window.showChatContainer === 'function') {
                            window.showChatContainer();
                        }
                        if (restoreDraftOnFailure) {
                            restoreChatDraftAfterFailedSend(message);
                        }
                    }

                    if (isRateLimited) {
                        showRateLimitCard({
                            container: document.getElementById('chatAreaContainer'),
                            errorData,
                            fallbackDetail: detail,
                        });
                    } else {
                        notifyError(detail);
                    }
                    continue;
                }

                clearMediaGenPlaceholderForNonFileEvent(messageId);
                if (isRateLimited) {
                    showRateLimitCard({
                        container: document.getElementById('chatAreaContainer'),
                        anchorElement: document.getElementById(`a-${messageId}`),
                        errorData,
                        fallbackDetail: detail,
                    });
                    continue;
                }
                appendAssistantError(messageId, detail, last_appended_message_type);
                appendAssistantDone(messageId, "");
            } else if (obj.t === "w") {
                const warningFallback = obj.c ?? obj.d ?? obj.message ?? '';
                const warningMessage = obj.i18n_key
                    ? getChatPreviewTranslation(obj.i18n_key, warningFallback)
                    : warningFallback;
                if (warningMessage) {
                    notifyWarning(warningMessage);
                }
            } else if (obj.t === 'uf') {
                updateUnsupportedFileWarnings(obj.file_ids || obj.d || [], { replace: true });
            } else if (obj.t === "wg") {
                // Widget rendering
                if (last_appended_message_type === "loading") {
                    removeLoading(messageId);
                    last_appended_message_type = "";
                }
                clearMediaGenPlaceholderForNonFileEvent(messageId);
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
                    last_appended_message_type = "wg";
                }
            } else if (obj.t === "subagent_evt") {
                if (last_appended_message_type === "loading") {
                    removeLoading(messageId);
                    last_appended_message_type = "";
                }
                clearMediaGenPlaceholderForNonFileEvent(messageId);
                if (typeof window.handleSubagentStreamEvent === 'function') {
                    try {
                        window.handleSubagentStreamEvent(obj, messageId);
                        last_appended_message_type = "subagent";
                    } catch (error) {
                        console.error('Failed to handle subagent stream event', error);
                    }
                }
            } else if (obj.t === "deep_research_evt") {
                if (last_appended_message_type === "loading") {
                    removeLoading(messageId);
                    last_appended_message_type = "";
                }
                clearMediaGenPlaceholderForNonFileEvent(messageId);
                if (window.deepResearchWidget && typeof window.deepResearchWidget.handleDeepResearchEvent === 'function') {
                    try {
                        window.deepResearchWidget.handleDeepResearchEvent(obj, messageId);
                    } catch (_) {}
                }
            } else if (obj.t === "slide_presentation_evt") {
                // Slides Presentation generation pipeline events
                if (last_appended_message_type === "loading") {
                    removeLoading(messageId);
                    last_appended_message_type = "";
                }
                clearMediaGenPlaceholderForNonFileEvent(messageId);
                if (window.slidePresentationWidget && typeof window.slidePresentationWidget.handleSlidePresentationEvent === 'function') {
                    window.slidePresentationWidget.handleSlidePresentationEvent(obj, messageId);
                }
            } else if (obj.t === "latex_pdf_evt") {
                if (last_appended_message_type === "loading") {
                    removeLoading(messageId);
                    last_appended_message_type = "";
                }
                clearMediaGenPlaceholderForNonFileEvent(messageId);
                if (window.latexPdfWidget && typeof window.latexPdfWidget.handleLatexPdfEvent === 'function') {
                    try {
                        window.latexPdfWidget.handleLatexPdfEvent(obj, messageId);
                    } catch (error) {
                        console.error('Failed to handle LaTeX PDF stream event', error);
                    }
                }
                // Break the reasoning stream so post-PDF reasoning starts a new thinking block.
                last_appended_message_type = "latex_pdf";
            } else if (obj.t === "canvas_evt") {
                clearMediaGenPlaceholderForNonFileEvent(messageId);
                if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleCanvasEvent === 'function') {
                    try {
                        window.canvasMarkdownWidget.handleCanvasEvent(obj, messageId);
                    } catch (_) {}
                }
            } else if (obj.t === "notes_evt") {
                clearMediaGenPlaceholderForNonFileEvent(messageId);
                if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleNotesEvent === 'function') {
                    try {
                        window.NotesToolSidebar.handleNotesEvent(obj, messageId);
                    } catch (error) {
                        console.error('Failed to handle notes stream event', error);
                    }
                }
            } else if (obj.t === "f") {
                // File rendering
                if (last_appended_message_type === "loading") {
                    removeLoading(messageId);
                    last_appended_message_type = "";
                }
                const fileId = obj.d ?? '';
                const fileName = obj.n ?? '';
                const fileSource = String(obj.source || obj.file_source || '').trim().toLowerCase();
                if (fileId && fileSource !== 'latex_pdf' && typeof appendAssistantFile === 'function') {
                    appendAssistantFile(messageId, fileId, last_appended_message_type, fileName);
                    last_appended_message_type = "f";
                }
            } else if (obj.t === "a_id") {
                // Assistant message ID received from backend
                const assistantMsgId = obj.d;
                if (assistantMsgId && typeof bindAssistantContainerToServerMessage === 'function') {
                    bindAssistantContainerToServerMessage(messageId, assistantMsgId);
                }
            } else if (obj.t === "d") {
                if (last_appended_message_type === "loading") {
                    removeLoading(messageId);
                    last_appended_message_type = "";
                }
                clearMediaGenPlaceholderForNonFileEvent(messageId);
                const doneMetadata = obj.c;
                const targetContainer = document.getElementById('a-' + messageId);
                if (targetContainer) {
                    delete targetContainer.dataset.isStreaming;
                    
                    // Store citations from metadata
                    if (doneMetadata && doneMetadata.citations && Array.isArray(doneMetadata.citations) && doneMetadata.citations.length > 0) {
                        try {
                            targetContainer.dataset.citations = JSON.stringify(doneMetadata.citations);
                        } catch (e) {
                            console.error('[Citations] Error storing citations:', e);
                        }
                    }
                }
                if (regenState && regenState.referenceId) {
                    const storedTotal = parseInt(
                        targetContainer?.dataset.totalVersions
                        || String(regenState.retryCount + 1)
                        || '1',
                        10,
                    );
                    appendAssistantDone(messageId, doneMetadata, {
                        retry_count: regenState.retryCount,
                        total_versions: storedTotal,
                        reference_id: regenState.referenceId,
                        is_latest_version: true,
                    });
                } else {
                    appendAssistantDone(messageId, doneMetadata);
                }

                try {
                    const container = document.getElementById('a-' + messageId);
                    if (container) {
                        const assistantThinking = container.querySelectorAll('.assistant-thinking');
                        const lastAssistantThinking = assistantThinking.length ? assistantThinking[assistantThinking.length - 1] : null;
                        if (lastAssistantThinking) {
                            // Finalize the header with proper text based on tool calls
                            // The appendAssistantReasoningFinish already handles this during streaming,
                            // but if no r_f event was received, finalize now
                            const headerSpan = lastAssistantThinking.querySelector('.assistant-thinking-title span');
                            if (headerSpan && headerSpan.classList.contains('assistant-thinking-shimmer')) {
                                if (typeof finalizeThinkingBlockHeader === 'function') {
                                    finalizeThinkingBlockHeader(lastAssistantThinking, temp_reasoning_time);
                                }
                            }
                        }
                    }
                } catch (_) {}
                window.endGenerationUI();
                generationTerminalStatus = 'finished';
                notifyGenerationTerminal('finished');
            } else if (obj.t === 't_g' || obj.t === 'n_t') {
                // Title is generated, if new chat
                const generatedTitle = typeof obj.d === 'string' ? obj.d : '';
                const chatContainerEl = document.getElementById('chatContainer');
                const currentChatId = chatContainerEl ? chatContainerEl.getAttribute('data-chat-id') : '';

                if (!currentChatId) {
                    continue;
                }

                try {
                    applyGeneratedSidebarTitle(currentChatId, generatedTitle);

                    // Also update project sidebar if in a project context
                    if (typeof window.addOrUpdateProjectChatRow === 'function') {
                        try {
                            window.addOrUpdateProjectChatRow(currentChatId, generatedTitle);
                        } catch (_) {}
                    }
                } catch (err) {
                    console.error('Failed to update sidebar title for chat:', err);
                }
            }
        }
        if (done) break;
    }
    } finally {
        const activeChatContainer = document.getElementById('chatContainer');
        const ownsActiveGeneration = activeChatContainer?.getAttribute('data-active-generation') === generationRequestId;

        // A newer send can supersede this reader before its next iteration.
        // Only the stream that still owns the active UUID may mutate shared
        // response, tool-preview, or generation UI state.
        if (ownsActiveGeneration) {
            // The stream may end because of cancellation or a dropped
            // connection without emitting a structured error/done event.
            if (messageId) {
                clearMediaGenPlaceholderForNonFileEvent(messageId);
            }
            const transcriptRoot = document.getElementById('chatAreaContainer');
            if (generationTransport.cancelled) {
                window.finalizeCancelledAssistantStream?.(messageId, transcriptRoot);
            } else {
                // Every other owned exit, including a thrown stream handler,
                // stabilizes Markdown without presenting a network failure as a
                // user-completed response. Structured `done` makes this a no-op.
                window.finalizeInterruptedAssistantStream?.(messageId, transcriptRoot);
            }
            if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleStreamEnd === 'function') {
                try {
                    // Also covers user cancellation, disconnects, and tool-level
                    // failures returned to the model without an `e` event.
                    window.NotesToolSidebar.handleStreamEnd(messageId);
                } catch (error) {
                    console.error('Failed to clean up notes live preview after generation', error);
                }
            }
            if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleStreamEnd === 'function') {
                try {
                    window.canvasMarkdownWidget.handleStreamEnd(messageId);
                } catch (error) {
                    console.error('Failed to clean up canvas preview after generation', error);
                }
            }
            if (window.slidePresentationWidget && typeof window.slidePresentationWidget.handleStreamEnd === 'function') {
                try {
                    window.slidePresentationWidget.handleStreamEnd(messageId);
                } catch (error) {
                    console.error('Failed to clean up slide presentation preview after generation', error);
                }
            }
            finalizeGenerationState();
            notifyGenerationTerminal(generationTerminalStatus);
        }
        releaseChatGenerationTransport(generationRequestId);
    }
}



// Expose for dynamic re-rendering from user settings
window.renderMarkdownContent = renderMarkdownContent;
window.renderMermaidDiagram = renderMermaidDiagram;
window.finalizeCodeBlockPreviewState = finalizeCodeBlockPreviewState;
window.cleanupMarkdownCodeBlockPreviews = cleanupMarkdownCodeBlockPreviews;
window.prepareMarkdownCodeBlocksForTransfer = prepareMarkdownCodeBlocksForTransfer;
