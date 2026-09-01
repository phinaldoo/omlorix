function dispatchExternalChatMessage(payload = {}) {
    if (window.isGenerating) {
        if (typeof notifyWarning === 'function') {
            notifyWarning(getChatPreviewTranslation('chat_current_response_wait', 'Please wait for the current response to finish first.'));
        }
        return Promise.resolve(null);
    }

    const normalizedPayload = typeof payload === 'object' && payload !== null ? payload : {};
    const message = typeof normalizedPayload.message === 'string' ? normalizedPayload.message : '';

    return sendMessage(message, false, null);
}

window.sendChatMessage = function(message = '') {
    return dispatchExternalChatMessage({ message });
};

window.sendChatMessageWithPayload = function(payload = {}) {
    return dispatchExternalChatMessage(payload);
};


// -------------------
// Message Regeneration
// -------------------

const DEFAULT_MAX_ASSISTANT_REGENERATIONS = 10; // Keep in sync with backend/app/chats/utils.py
const MAX_ASSISTANT_REGENERATIONS = typeof window.MAX_ASSISTANT_REGENERATIONS === 'number'
    ? window.MAX_ASSISTANT_REGENERATIONS
    : DEFAULT_MAX_ASSISTANT_REGENERATIONS;
window.MAX_ASSISTANT_REGENERATIONS = MAX_ASSISTANT_REGENERATIONS;

/** Capture content and dataset values cleared when a retry reuses a container. */
function captureAssistantRetryContainerSnapshot(container) {
    if (!container) return null;

    const datasetKeys = [
        'referenceId',
        'retryCount',
        'totalVersions',
        'isLatestVersion',
        'hidden',
        'isStreaming',
        'announceStreaming',
        'hasError',
        'assistantMetadata',
        'citations',
        'assistantMessageId',
        'optimisticMessage',
        'assistantTerminalState',
        'cancelPresentationFinalized',
    ];
    const dataset = {};
    datasetKeys.forEach((key) => {
        dataset[key] = {
            present: Object.prototype.hasOwnProperty.call(container.dataset, key),
            value: container.dataset[key],
        };
    });

    return {
        innerHTML: container.innerHTML,
        dataset,
    };
}

/** Restore content and dataset values captured before reusing a retry container. */
function restoreAssistantRetryContainerSnapshot(container, snapshot) {
    if (!container || !snapshot) return;

    container.innerHTML = snapshot.innerHTML || '';
    Object.entries(snapshot.dataset || {}).forEach(([key, state]) => {
        if (state?.present) {
            container.dataset[key] = state.value;
        } else {
            delete container.dataset[key];
        }
    });
}

function prepareAssistantRegenerationTarget(referenceId, retryCount, { announce = true } = {}) {
    const normalizedReferenceId = String(referenceId || '').trim();
    if (!normalizedReferenceId) {
        return null;
    }

    const fallbackHasMeaningfulOutput = (container) => {
        if (!container) {
            return false;
        }
        return Array.from(container.children).some((child) => {
            if (!child || !child.classList) {
                return false;
            }
            if (child.classList.contains('sr-only')) {
                return false;
            }
            if (child.classList.contains('assistant-message-list')) {
                return false;
            }
            if (child.classList.contains('assistant-message-error')) {
                return false;
            }
            if (child.classList.contains('assistant-thinking-loading')) {
                return false;
            }
            return true;
        });
    };

    const hasMeaningfulOutput = typeof window.assistantContainerHasMeaningfulOutput === 'function'
        ? window.assistantContainerHasMeaningfulOutput
        : fallbackHasMeaningfulOutput;

    const getContainersByReference = typeof window.getAssistantContainersByReference === 'function'
        ? window.getAssistantContainersByReference
        : (refId) => {
            const chatAreaContainer = document.getElementById('chatAreaContainer');
            if (!chatAreaContainer) {
                return [];
            }
            return Array.from(chatAreaContainer.querySelectorAll('.assistant-message-container'))
                .filter((container) => container.dataset.referenceId === refId);
        };

    const existingContainers = getContainersByReference(normalizedReferenceId);
    const visibleContainer = existingContainers.find((container) => container.dataset.hidden !== 'true' && container.style.display !== 'none')
        || existingContainers[existingContainers.length - 1]
        || null;

    existingContainers.forEach((container) => {
        if (container !== visibleContainer && !hasMeaningfulOutput(container)) {
            container.remove();
        }
    });

    const meaningfulContainers = existingContainers.filter((container) => container.isConnected && hasMeaningfulOutput(container));
    const shouldReuseVisibleContainer = Boolean(visibleContainer && visibleContainer.isConnected && !hasMeaningfulOutput(visibleContainer));
    const updatedTotalVersions = Math.max(meaningfulContainers.length + 1, 1);

    let targetContainer = visibleContainer;
    let targetMessageId = targetContainer?.id && targetContainer.id.startsWith('a-')
        ? targetContainer.id.slice(2)
        : null;
    let reusedContainerSnapshot = null;

    if (shouldReuseVisibleContainer) {
        reusedContainerSnapshot = captureAssistantRetryContainerSnapshot(visibleContainer);
        if (typeof window.resetAssistantContainerForRetry === 'function') {
            try {
                const resetResult = window.resetAssistantContainerForRetry(visibleContainer, {
                    messageId: targetMessageId,
                    referenceId: normalizedReferenceId,
                    retryCount,
                    totalVersions: updatedTotalVersions,
                    announce,
                    snapshot: reusedContainerSnapshot,
                });
                // Allow the shared reset helper to return its own richer snapshot
                // while remaining compatible with its existing element return.
                if (resetResult?.container) {
                    targetContainer = resetResult.container;
                    reusedContainerSnapshot = resetResult.snapshot || reusedContainerSnapshot;
                } else {
                    targetContainer = resetResult;
                }
            } catch (error) {
                restoreAssistantRetryContainerSnapshot(visibleContainer, reusedContainerSnapshot);
                throw error;
            }
        } else if (visibleContainer) {
            try {
                visibleContainer.innerHTML = '';
                visibleContainer.style.display = '';
                visibleContainer.dataset.referenceId = normalizedReferenceId;
                visibleContainer.dataset.retryCount = String(parseInt(retryCount, 10) || 0);
                visibleContainer.dataset.totalVersions = String(updatedTotalVersions);
                visibleContainer.dataset.isLatestVersion = 'true';
                visibleContainer.dataset.hidden = 'false';
                visibleContainer.dataset.isStreaming = 'true';
                visibleContainer.dataset.announceStreaming = announce ? 'true' : 'false';
                delete visibleContainer.dataset.hasError;
                delete visibleContainer.dataset.assistantMetadata;
                delete visibleContainer.dataset.citations;
                delete visibleContainer.dataset.assistantMessageId;
                delete visibleContainer.dataset.assistantTerminalState;
                delete visibleContainer.dataset.cancelPresentationFinalized;
                targetContainer = visibleContainer;
            } catch (error) {
                restoreAssistantRetryContainerSnapshot(visibleContainer, reusedContainerSnapshot);
                throw error;
            }
        }
    } else {
        if (visibleContainer) {
            visibleContainer.style.display = 'none';
            visibleContainer.dataset.hidden = 'true';
            visibleContainer.dataset.isLatestVersion = 'false';
        }

        targetMessageId = generateUUID();
        if (typeof appendAssistantContainer === 'function') {
            appendAssistantContainer(targetMessageId, { announce });
        }
        targetContainer = document.getElementById('a-' + targetMessageId);
    }

    const retainedContainers = getContainersByReference(normalizedReferenceId);
    retainedContainers.forEach((container) => {
        if (!container || !container.isConnected || container === targetContainer) {
            return;
        }
        container.dataset.totalVersions = String(updatedTotalVersions);
        container.dataset.isLatestVersion = 'false';
    });

    if (targetContainer) {
        targetContainer.dataset.referenceId = normalizedReferenceId;
        targetContainer.dataset.retryCount = String(parseInt(retryCount, 10) || 0);
        targetContainer.dataset.totalVersions = String(updatedTotalVersions);
        targetContainer.dataset.isLatestVersion = 'true';
        targetContainer.dataset.hidden = 'false';
        targetContainer.dataset.isStreaming = 'true';
        targetContainer.dataset.announceStreaming = announce ? 'true' : 'false';
        targetContainer.dataset.optimisticMessage = 'true';
        targetContainer.style.display = '';
    }

    return {
        newMessageId: targetMessageId,
        updatedTotalVersions,
        referenceContainers: retainedContainers.filter((container) => container !== targetContainer),
        reusedExisting: shouldReuseVisibleContainer,
        reusedContainerSnapshot,
    };
}

/**
 * Remove an optimistic regeneration container and restore the response that was
 * visible before the request started. The optimistic switch happens before the
 * network request so the stale response disappears as soon as the user confirms.
 */
function rollbackAssistantRegenerationTarget({
    referenceId,
    originalMessageId,
    optimisticMessageId,
    previousTotalVersions,
    reusedContainerSnapshot,
} = {}) {
    const normalizedReferenceId = String(referenceId || '').trim();
    const normalizedOriginalMessageId = String(originalMessageId || '').trim();
    const normalizedOptimisticMessageId = String(optimisticMessageId || '').trim();

    if (normalizedOptimisticMessageId && normalizedOptimisticMessageId !== normalizedOriginalMessageId) {
        document.getElementById(`a-${normalizedOptimisticMessageId}`)?.remove();
    } else if (normalizedOptimisticMessageId && typeof removeLoading === 'function') {
        removeLoading(normalizedOptimisticMessageId);
    }

    const originalContainer = document.getElementById(`a-${normalizedOriginalMessageId}`);
    if (originalContainer) {
        if (normalizedOptimisticMessageId === normalizedOriginalMessageId && reusedContainerSnapshot) {
            restoreAssistantRetryContainerSnapshot(originalContainer, reusedContainerSnapshot);
        }
        originalContainer.style.display = '';
        originalContainer.dataset.hidden = 'false';
        originalContainer.dataset.isLatestVersion = 'true';
        delete originalContainer.dataset.isStreaming;
    }

    // Preparing the optimistic version increments the total on every retained
    // version. Put that count back when the server rejects the regeneration.
    const restoredTotal = Math.max(1, parseInt(previousTotalVersions, 10) || 1);
    const referenceContainers = typeof window.getAssistantContainersByReference === 'function'
        ? window.getAssistantContainersByReference(normalizedReferenceId)
        : [];
    referenceContainers.forEach((container) => {
        container.dataset.totalVersions = String(restoredTotal);
        if (container !== originalContainer) {
            container.dataset.isLatestVersion = 'false';
        }
        if (typeof window.updateAssistantVersionSwitcher === 'function') {
            window.updateAssistantVersionSwitcher(container);
        }
    });
}

// Store for assistant message versions by reference_id
const assistantMessageVersions = new Map(); // reference_id -> { messages: [...], currentVersion: number }

function registerAssistantMessageVersion(referenceId, messageData) {
    if (!referenceId) return;
    
    if (!assistantMessageVersions.has(referenceId)) {
        assistantMessageVersions.set(referenceId, { messages: [], currentVersion: 0 });
    }
    
    const versionData = assistantMessageVersions.get(referenceId);
    const retryCount = messageData.retry_count || 0;
    
    // Find or add this version
    const existingIndex = versionData.messages.findIndex(m => m.retry_count === retryCount);
    if (existingIndex >= 0) {
        versionData.messages[existingIndex] = messageData;
    } else {
        versionData.messages.push(messageData);
        versionData.messages.sort((a, b) => (a.retry_count || 0) - (b.retry_count || 0));
    }
    
    // Set current version to the highest (latest)
    versionData.currentVersion = versionData.messages.length - 1;
}

function getAssistantMessageVersions(referenceId) {
    return assistantMessageVersions.get(referenceId) || null;
}

function clearAssistantMessageVersions() {
    assistantMessageVersions.clear();
}

const MAX_RETRY_GUIDANCE_CHARS = 2000; // Keep in sync with messages/actions.js and backend/app/chats/schemas.py
const SUPPORTED_RETRY_GUIDANCE_PRESETS = new Set(['try_again', 'add_details', 'more_concise']);

function normalizeRetryGuidancePayload(retryGuidance = null) {
    if (!retryGuidance || typeof retryGuidance !== 'object') {
        return null;
    }

    const mode = String(retryGuidance.mode || '').trim();
    if (!mode || mode === 'default') {
        return null;
    }

    if (mode === 'preset') {
        const preset = String(retryGuidance.preset || '').trim();
        if (!SUPPORTED_RETRY_GUIDANCE_PRESETS.has(preset)) {
            return null;
        }
        return { mode: 'preset', preset };
    }

    if (mode === 'custom') {
        const instruction = String(retryGuidance.instruction || '').trim();
        if (!instruction) {
            return null;
        }
        if (instruction.length > MAX_RETRY_GUIDANCE_CHARS) {
            throw new Error(
                typeof t === 'function'
                    ? t('chat_regenerate_custom_too_long', 'Retry guidance must be 2000 characters or less.')
                    : 'Retry guidance must be 2000 characters or less.'
            );
        }
        return { mode: 'custom', instruction };
    }

    return null;
}

function buildRegenerationRequestBody({ chatId, userMessageId, modelId, generationId, retryGuidance = null }) {
    const payload = {
        generation_id: generationId,
        chat_id: chatId,
        user_message_id: userMessageId,
        model_id: modelId,
    };

    const normalizedRetryGuidance = normalizeRetryGuidancePayload(retryGuidance);
    if (normalizedRetryGuidance) {
        payload.retry_guidance = normalizedRetryGuidance;
    }

    const payloadSkillIds = typeof window.getSelectedSkillIds === 'function' ? window.getSelectedSkillIds() : [];
    if (payloadSkillIds && payloadSkillIds.length) {
        payload.skill_ids = payloadSkillIds;
    }

    const noteIds = typeof window.getSelectedNoteIds === 'function' ? window.getSelectedNoteIds() : [];
    if (noteIds && noteIds.length) {
        payload.note_ids = noteIds;
    }

    const promptIds = typeof window.getSelectedPromptIds === 'function' ? window.getSelectedPromptIds() : [];
    if (promptIds && promptIds.length) {
        payload.prompt_ids = promptIds;
    }

    const subagentTargets = window.SubagentTargets?.getSelection?.();
    payload.subagent_targets = Array.isArray(subagentTargets) ? subagentTargets : null;

    const customModelSettings = typeof window.getCurrentModelSettingValues === 'function'
        ? window.getCurrentModelSettingValues()
        : {};
    let byokPayload = null;
    if (typeof window.BYOK?.buildRequestPayloadForModel === 'function') {
        byokPayload = window.BYOK.buildRequestPayloadForModel(modelId, customModelSettings);
    }

    return {
        byokPayload,
        body: JSON.stringify({
            payload: {
                ...payload,
                model_id: byokPayload ? '' : payload.model_id,
            },
            custom_settings: byokPayload ? {} : (customModelSettings && Object.keys(customModelSettings).length ? customModelSettings : {}),
            byok: byokPayload || {},
        }),
    };
}

async function triggerRegeneration(assistantMessageId, { retryGuidance = null, onRegenerationStarted = null } = {}) {
    // Regeneration owns a different scroll flow. Cancel any pending new-prompt
    // alignment before removing its temporary geometry.
    const chatArea = document.getElementById('chatArea');
    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (window.ChatScrollCoordinator) {
        window.ChatScrollCoordinator.cancel(chatArea, {
            container: chatAreaContainer,
            removeSpacer: true,
        });
    } else {
        const dynamicSpacer = chatAreaContainer?.querySelector('.dynamic-scroll-spacer');
        if (dynamicSpacer) {
            dynamicSpacer.remove();
        }
    }

    // Get the assistant container to find the reference_id (user message id)
    const assistantContainer = document.getElementById('a-' + assistantMessageId);
    if (!assistantContainer) {
        console.error('Assistant container not found');
        return false;
    }
    
    const referenceId = assistantContainer.dataset.serverReferenceId || assistantContainer.dataset.referenceId;
    if (!referenceId) {
        // If no reference_id stored, try to find the user message
        const userMessageArea = assistantContainer.previousElementSibling;
        if (userMessageArea && userMessageArea.classList.contains('user-message-area')) {
            const userContent = userMessageArea.querySelector('.user-message-content');
            if (userContent && userContent.id && userContent.id.startsWith('u-')) {
                assistantContainer.dataset.referenceId = userContent.id.replace('u-', '');
            }
        }
    }
    
    const userMessageId = assistantContainer.dataset.serverReferenceId
        || assistantContainer.dataset.referenceId
        || assistantMessageId;

    const currentRetryCount = parseInt(assistantContainer.dataset.retryCount || '0', 10);
    const isLatestVersion = assistantContainer.dataset.isLatestVersion === 'true';
    const hasRemainingRegenerations = currentRetryCount < (MAX_ASSISTANT_REGENERATIONS - 1);

    if (!isLatestVersion) {
        if (typeof notifyWarning === 'function') {
            notifyWarning(getChatPreviewTranslation('chat_regenerate_latest_only', 'Only the latest response can be regenerated'));
        }
        return false;
    }

    if (!hasRemainingRegenerations) {
        if (typeof notifyWarning === 'function') {
            notifyWarning(getChatPreviewTranslation('chat_regenerate_limit_reached', 'Maximum regenerations reached for this message'));
        }
        return false;
    }
    
    // Get the current chat ID
    const chatContainerEl = document.getElementById('chatContainer');
    const chatId = chatContainerEl?.getAttribute('data-chat-id');
    if (!chatId) {
        if (typeof notifyError === 'function') {
            notifyError(getChatPreviewTranslation('chat_no_active_chat', 'No active chat'));
        }
        return false;
    }
    
    // Get the currently selected model (stored on container attribute)
    const modelSelect = document.getElementById('modelSelect');
    let modelId = modelSelect?.getAttribute('data-model-id') || null;
    if (!modelId && typeof window.getActiveModelId === 'function') {
        modelId = window.getActiveModelId();
    }
    if (!modelId) {
        if (typeof notifyError === 'function') {
            notifyError(getChatPreviewTranslation('chat_regenerate_select_model_required', 'Select a model before regenerating'));
        }
        return false;
    }

    if (
        typeof window.validateCurrentModelSettings === 'function'
        && !window.validateCurrentModelSettings()
    ) {
        if (typeof notifyError === 'function') {
            notifyError(getChatPreviewTranslation(
                'model_settings_invalid_structured_value_regenerate',
                'Correct the invalid model setting before regenerating.'
            ));
        }
        return false;
    }
    
    // Check if already generating
    if (window.isGenerating) {
        if (typeof notifyWarning === 'function') {
            notifyWarning(getChatPreviewTranslation('chat_generation_wait_current', 'Please wait for the current generation to complete'));
        }
        return false;
    }
    
    if (typeof window.startGenerationUI === 'function') {
        window.startGenerationUI();
    } else {
        window.isGenerating = true;
        if (typeof toggleInputButtons === 'function') {
            toggleInputButtons();
        }
        if (typeof applySendButtonMode === 'function') {
            applySendButtonMode();
        }
    }
    window.pendingCancelGeneration = false;
    const generationRequestId = generateUUID();
    const generationTransport = beginChatGenerationTransport(generationRequestId);
    window.currentGenerationId = generationRequestId;
    document.getElementById('chatContainer')?.setAttribute('data-active-generation', generationRequestId);

    const previousTotalVersions = parseInt(assistantContainer.dataset.totalVersions || '1', 10) || 1;
    let optimisticMessageId = assistantMessageId;
    let reusedContainerSnapshot = null;
    let queueTerminalStatus = 'error';
    const regenerationViewport = assistantContainer.closest('.chat-area') || document.getElementById('chatArea');
    window.ChatScrollManager?.beginStream?.(regenerationViewport);
    
    try {
        try {
            if (typeof window.NotesToolSidebar?.flushPendingEdits === 'function') {
                const notesSaved = await window.NotesToolSidebar.flushPendingEdits();
                if (!notesSaved) return false;
            }
            // Switch to the pending response immediately. Keeping this setup in
            // the protected request flow ensures setup failures also roll back.
            // Retain a caller-side snapshot until preparation returns, because a
            // shared reset helper could throw after it has cleared the container.
            reusedContainerSnapshot = captureAssistantRetryContainerSnapshot(assistantContainer);
            const optimisticTarget = prepareAssistantRegenerationTarget(
                userMessageId,
                currentRetryCount + 1,
                { announce: true }
            );
            optimisticMessageId = optimisticTarget?.newMessageId || assistantMessageId;
            generationTransport.messageId = optimisticMessageId;
            generationTransport.transcriptRoot = document.getElementById('chatAreaContainer');
            reusedContainerSnapshot = optimisticTarget?.reusedExisting
                ? (optimisticTarget.reusedContainerSnapshot || reusedContainerSnapshot)
                : null;
            if (typeof appendLoading === 'function') {
                appendLoading(optimisticMessageId, 0);
            }

            if (typeof window.closeAllAssistantRegeneratePopovers === 'function') {
                try {
                    window.closeAllAssistantRegeneratePopovers();
                } catch (error) {
                    console.warn('Failed to close assistant regenerate popovers:', error);
                }
            }
            if (typeof onRegenerationStarted === 'function') {
                try {
                    onRegenerationStarted();
                } catch (error) {
                    console.warn('Regeneration start callback failed:', error);
                }
            }

            const requestBody = buildRegenerationRequestBody({
                chatId,
                userMessageId,
                modelId,
                generationId: generationRequestId,
                retryGuidance,
            });
            const { byokPayload, body } = requestBody;
            clearUnsupportedFileWarningState();
            const response = await window.authedFetch('/api/v1/chats/regenerate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body,
                signal: generationTransport.abortController.signal,
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                const detail = resolveApiErrorMessage(errorData, `HTTP ${response.status}`);
                if (response.status === 429) {
                    rollbackAssistantRegenerationTarget({
                        referenceId: userMessageId,
                        originalMessageId: assistantMessageId,
                        optimisticMessageId,
                        previousTotalVersions,
                        reusedContainerSnapshot,
                    });
                    showRateLimitCard({
                        container: document.getElementById('chatAreaContainer'),
                        anchorElement: document.getElementById(`a-${assistantMessageId}`),
                        errorData,
                        fallbackDetail: detail,
                    });
                    return false;
                }
                const isAdmin = typeof localStorage !== 'undefined' && localStorage.getItem('is_admin') === 'true';
                const shouldExposeDetail = Boolean(byokPayload || isAdmin || response.status === 403 || isChatReferenceApiError(errorData));
                throw new Error(shouldExposeDetail ? detail : getChatPreviewTranslation('chat_regenerate_failed', 'Failed to regenerate response'));
            }

            const regenerationResult = await processRegenerationStream(response, assistantMessageId, userMessageId, {
                preparedTargetMessageId: optimisticMessageId,
                generationId: generationRequestId,
            });
            if (regenerationResult?.rateLimited) {
                // A model quota failure can arrive as an error event inside an
                // otherwise successful streaming response. Restore the response
                // that was visible before the optimistic regeneration, then put
                // the same structured quota card used by normal sends beside it.
                rollbackAssistantRegenerationTarget({
                    referenceId: userMessageId,
                    originalMessageId: assistantMessageId,
                    optimisticMessageId,
                    previousTotalVersions,
                    reusedContainerSnapshot,
                });
                showRateLimitCard({
                    container: document.getElementById('chatAreaContainer'),
                    anchorElement: document.getElementById(`a-${assistantMessageId}`),
                    errorData: regenerationResult.errorData,
                    fallbackDetail: regenerationResult.detail,
                });
                return false;
            }
            queueTerminalStatus = regenerationResult?.completed ? 'finished' : 'interrupted';
            return true;
        } catch (error) {
            throw new Error(error.message || getChatPreviewTranslation('chat_regenerate_prepare_failed', 'Failed to prepare regeneration request'));
        }
    } catch (error) {
        if (generationTransport.cancelled) {
            clearMediaGenPlaceholderForNonFileEvent(optimisticMessageId);
            window.finalizeCancelledAssistantStream?.(
                optimisticMessageId,
                document.getElementById('chatAreaContainer')
            );
            return true;
        }
        console.error('Regeneration failed:', error);
        if (typeof notifyError === 'function') {
            notifyError(error.message || getChatPreviewTranslation('chat_regenerate_failed', 'Failed to regenerate response'));
        }
        rollbackAssistantRegenerationTarget({
            referenceId: userMessageId,
            originalMessageId: assistantMessageId,
            optimisticMessageId,
            previousTotalVersions,
            reusedContainerSnapshot,
        });
        return false;
    } finally {
        window.ChatScrollManager?.endStream?.(regenerationViewport);
        const ownsGeneration = activeChatGenerationTransport?.generationId === generationRequestId;
        if (ownsGeneration) {
            if (typeof window.resetGenerationUIState === 'function') {
                window.resetGenerationUIState({ clearActiveAttr: true });
            } else {
                window.currentGenerationId = null;
                window.pendingCancelGeneration = false;
                window.isGenerating = false;
                document.getElementById('chatContainer')?.removeAttribute('data-active-generation');
                if (typeof toggleInputButtons === 'function') {
                    toggleInputButtons();
                }
                if (typeof applySendButtonMode === 'function') {
                    applySendButtonMode();
                }
            }
            releaseChatGenerationTransport(generationRequestId);
        }
        if (!generationTransport.cancelled) {
            window.messageQueue?.handleGenerationTerminal?.({
                generationId: generationRequestId,
                surface: 'chat',
                status: queueTerminalStatus,
            });
        }
    }
}

async function processRegenerationStream(
    response,
    originalMessageId,
    userMessageId,
    { preparedTargetMessageId = null, generationId = null } = {}
) {
    const reader = response.body.getReader();
    if (activeChatGenerationTransport?.generationId === String(generationId || '')) {
        activeChatGenerationTransport.reader = reader;
    }
    const decoder = new TextDecoder();
    let buffer = '';
    // A confirmed regeneration prepares its new version before the request so
    // all stream events, even ones preceding `regen`, target the loading card.
    let newMessageId = preparedTargetMessageId;
    let newRetryCount = 0;
    let last_appended_message_type = preparedTargetMessageId ? 'loading' : '';
    let assistantContentCount = 0;
    let assistantReasoningCount = 0;
    let containerCreated = Boolean(preparedTargetMessageId);
    let temp_reasoning_time = 0;
    let completed = false;
    const regenerationChatId = document.getElementById('chatContainer')?.getAttribute('data-chat-id') || '';
    let regenerationGenerationId = '';
    
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (
                activeChatGenerationTransport?.generationId === String(generationId || '')
                && activeChatGenerationTransport.cancelled
            ) {
                break;
            }
            if (done) break;
            
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            
            for (const line of lines) {
                if (!line.trim()) continue;
                
                try {
                    const obj = JSON.parse(line);
                    
                    // Handle regeneration info event
                    if (obj.t === 'regen') {
                        newRetryCount = obj.d?.retry_count || 0;

                        // The target normally already exists because the UI switches
                        // versions on confirmation. Reconcile its server retry count
                        // without clearing the loading state that is currently shown.
                        if (containerCreated && newMessageId) {
                            const preparedContainer = document.getElementById('a-' + newMessageId);
                            if (preparedContainer) {
                                preparedContainer.dataset.retryCount = String(newRetryCount);
                            }
                        }
                        
                        // Generate new message ID and create container when we get regen info
                        if (!containerCreated) {
                            const regenPrep = prepareAssistantRegenerationTarget(userMessageId, newRetryCount, { announce: true });
                            if (!regenPrep || !regenPrep.newMessageId) {
                                continue;
                            }

                            newMessageId = regenPrep.newMessageId;

                            if (typeof appendLoading === 'function') {
                                assistantReasoningCount = appendLoading(newMessageId, assistantReasoningCount);
                                last_appended_message_type = 'loading';
                            }

                            if (newMessageId !== originalMessageId && typeof removeLoading === 'function') {
                                removeLoading(originalMessageId);
                            }
                            
                            containerCreated = true;
                        }
                        continue;
                    }
                    
                    // Handle start event
                    if (obj.t === 's') {
                        window.currentGenerationId = obj.d;
                        regenerationGenerationId = String(obj.d || '');
                        window.ChatAttention?.trackGeneration(regenerationChatId, regenerationGenerationId);
                        continue;
                    }
                    
                    // Handle content events - use the new message ID if available
                    const targetMessageId = newMessageId || originalMessageId;
                    
                    if (obj.t === 'a_id') {
                        if (obj.d && typeof bindAssistantContainerToServerMessage === 'function') {
                            bindAssistantContainerToServerMessage(targetMessageId, obj.d);
                        }
                    } else if (obj.t === 'r') {
                        // Reasoning/thinking
                        const wasLoading = last_appended_message_type === 'loading';
                        if (wasLoading && typeof expandLoading === 'function') {
                            assistantReasoningCount = expandLoading(targetMessageId, assistantReasoningCount);
                        }
                        const mediaGenerationFailed = clearMediaGenPlaceholderForNonFileEvent(targetMessageId);
                        if (typeof appendAssistantReasoning === 'function') {
                            assistantReasoningCount = appendAssistantReasoning(
                                targetMessageId,
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
                            assistantReasoningCount = expandLoading(targetMessageId, assistantReasoningCount);
                        }
                        const toolDescriptor = obj.d || {};
                        const resolvedToolName = typeof toolDescriptor === 'string'
                            ? toolDescriptor
                            : (toolDescriptor.name || '');
                        const resolvedToolArgs = toolDescriptor.args ?? obj.c;
                        const resolvedToolCallId = typeof toolDescriptor === 'object'
                            ? (toolDescriptor.id || toolDescriptor.tool_call_id || '')
                            : '';
                        const mediaGenerationFailed = transitionMediaGenPlaceholderForToolCall(
                            targetMessageId,
                            resolvedToolName,
                            resolvedToolCallId
                        );
                        if (typeof appendAssistantTool === 'function') {
                            assistantReasoningCount = appendAssistantTool(
                                targetMessageId,
                                mediaGenerationFailed ? 'media-generation-failed' : (wasLoading ? 'r' : last_appended_message_type),
                                assistantReasoningCount,
                                null,
                                resolvedToolName,
                                resolvedToolArgs,
                                typeof toolDescriptor === 'object' ? toolDescriptor : null
                            );
                            last_appended_message_type = 't';
                        }

                        if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleToolCallEvent === 'function') {
                            try {
                                window.canvasMarkdownWidget.handleToolCallEvent(obj, targetMessageId);
                            } catch (_) {}
                        }
                        if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleToolCallEvent === 'function') {
                            try {
                                window.NotesToolSidebar.handleToolCallEvent(obj, targetMessageId);
                            } catch (error) {
                                console.error('Failed to start notes live preview', error);
                            }
                        }

                        syncMediaGenPlaceholder(
                            targetMessageId,
                            resolvedToolName,
                            resolvedToolCallId
                        );
                    } else if (obj.t === 't_e') {
                        if (typeof applyAssistantToolError === 'function') {
                            applyAssistantToolError(targetMessageId, obj.d || {}, { announce: true });
                        }
                    } else if (obj.t === 't_cd') {
                        const toolDeltaUpdate = processAssistantToolDeltaStreamEvent(
                            targetMessageId,
                            last_appended_message_type,
                            assistantReasoningCount,
                            obj.d
                        );
                        assistantReasoningCount = toolDeltaUpdate.assistantReasoningCount;
                        last_appended_message_type = toolDeltaUpdate.lastAppendedMessageType;
                        if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleToolCallDeltaEvent === 'function') {
                            try {
                                window.canvasMarkdownWidget.handleToolCallDeltaEvent(obj, targetMessageId);
                            } catch (_) {}
                        }
                        if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleToolCallDeltaEvent === 'function') {
                            try {
                                window.NotesToolSidebar.handleToolCallDeltaEvent(obj, targetMessageId);
                            } catch (error) {
                                console.error('Failed to update notes live preview', error);
                            }
                        }
                    } else if (obj.t === 'c') {
                        // Content
                        if (last_appended_message_type === 'loading') {
                            if (typeof removeLoading === 'function') {
                                removeLoading(targetMessageId);
                            }
                            last_appended_message_type = '';
                        }
                        const mediaGenerationFailed = clearMediaGenPlaceholderForNonFileEvent(targetMessageId);
                        if (typeof appendAssistantContent === 'function') {
                            assistantContentCount = appendAssistantContent(
                                targetMessageId,
                                obj.d,
                                mediaGenerationFailed ? 'media-generation-failed' : last_appended_message_type,
                                assistantContentCount,
                                null,
                                assistantReasoningCount
                            );
                            last_appended_message_type = 'c';
                        }
                    } else if (obj.t === 'wg') {
                        if (last_appended_message_type === 'loading') {
                            if (typeof removeLoading === 'function') {
                                removeLoading(targetMessageId);
                            }
                            last_appended_message_type = '';
                        }
                        clearMediaGenPlaceholderForNonFileEvent(targetMessageId);
                        const widgetHtml = obj.c ?? '';
                        const widgetType = obj.widget_type ?? 'unknown';
                        if (widgetHtml && typeof appendAssistantWidget === 'function') {
                            appendAssistantWidget(
                                targetMessageId,
                                widgetHtml,
                                widgetType,
                                last_appended_message_type,
                                obj.meta ?? null,
                                { autoOpen: true },
                            );
                            last_appended_message_type = 'wg';
                        }
                    } else if (obj.t === 'subagent_evt') {
                        if (last_appended_message_type === 'loading') {
                            if (typeof removeLoading === 'function') {
                                removeLoading(targetMessageId);
                            }
                            last_appended_message_type = '';
                        }
                        clearMediaGenPlaceholderForNonFileEvent(targetMessageId);
                        if (typeof window.handleSubagentStreamEvent === 'function') {
                            try {
                                window.handleSubagentStreamEvent(obj, targetMessageId);
                                last_appended_message_type = 'subagent';
                            } catch (error) {
                                console.error('Failed to handle subagent stream event', error);
                            }
                        }
                    } else if (obj.t === 'deep_research_evt') {
                        if (last_appended_message_type === 'loading') {
                            if (typeof removeLoading === 'function') {
                                removeLoading(targetMessageId);
                            }
                            last_appended_message_type = '';
                        }
                        clearMediaGenPlaceholderForNonFileEvent(targetMessageId);
                        if (window.deepResearchWidget && typeof window.deepResearchWidget.handleDeepResearchEvent === 'function') {
                            try {
                                window.deepResearchWidget.handleDeepResearchEvent(obj, targetMessageId);
                            } catch (_) {}
                        }
                    } else if (obj.t === 'latex_pdf_evt') {
                        if (last_appended_message_type === 'loading') {
                            if (typeof removeLoading === 'function') {
                                removeLoading(targetMessageId);
                            }
                            last_appended_message_type = '';
                        }
                        clearMediaGenPlaceholderForNonFileEvent(targetMessageId);
                        if (window.latexPdfWidget && typeof window.latexPdfWidget.handleLatexPdfEvent === 'function') {
                            try {
                                window.latexPdfWidget.handleLatexPdfEvent(obj, targetMessageId);
                            } catch (_) {}
                        }
                        last_appended_message_type = 'latex_pdf';
                    } else if (obj.t === 'canvas_evt') {
                        clearMediaGenPlaceholderForNonFileEvent(targetMessageId);
                        if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleCanvasEvent === 'function') {
                            try {
                                window.canvasMarkdownWidget.handleCanvasEvent(obj, targetMessageId);
                            } catch (_) {}
                        }
                    } else if (obj.t === 'notes_evt') {
                        clearMediaGenPlaceholderForNonFileEvent(targetMessageId);
                        if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleNotesEvent === 'function') {
                            try {
                                window.NotesToolSidebar.handleNotesEvent(obj, targetMessageId);
                            } catch (error) {
                                console.error('Failed to handle notes stream event', error);
                            }
                        }
                    } else if (obj.t === 'f') {
                        if (last_appended_message_type === 'loading') {
                            if (typeof removeLoading === 'function') {
                                removeLoading(targetMessageId);
                            }
                            last_appended_message_type = '';
                        }
                        const fileId = obj.d ?? '';
                        const fileName = obj.n ?? '';
                        const fileSource = String(obj.source || obj.file_source || '').trim().toLowerCase();
                        if (fileId && fileSource !== 'latex_pdf' && typeof appendAssistantFile === 'function') {
                            appendAssistantFile(targetMessageId, fileId, last_appended_message_type, fileName);
                            last_appended_message_type = 'f';
                        }
                    } else if (obj.t === 'r_f') {
                        temp_reasoning_time = obj.d;
                    } else if (obj.t === 'w') {
                        const warningFallback = obj.c ?? obj.d ?? obj.message ?? '';
                        const warningMessage = obj.i18n_key
                            ? getChatPreviewTranslation(obj.i18n_key, warningFallback)
                            : warningFallback;
                        if (warningMessage && typeof notifyWarning === 'function') {
                            notifyWarning(warningMessage);
                        }
                    } else if (obj.t === 'uf') {
                        updateUnsupportedFileWarnings(obj.file_ids || obj.d || [], { replace: true });
                    } else if (obj.t === 'd' && obj.d === 'f') {
                        // Done/finish
                        completed = true;
                        const metadata = obj.c || {};
                        if (last_appended_message_type === 'loading') {
                            if (typeof removeLoading === 'function') {
                                removeLoading(targetMessageId);
                            }
                            last_appended_message_type = '';
                        }
                        clearMediaGenPlaceholderForNonFileEvent(targetMessageId);
                        // Clear streaming flag before adding buttons
                        const targetContainerEl = document.getElementById('a-' + targetMessageId);
                        if (targetContainerEl) {
                            delete targetContainerEl.dataset.isStreaming;
                        }
                        if (typeof appendAssistantDone === 'function') {
                            // Get total versions from the old or new container (already updated during regen start)
                            const oldContainer = document.getElementById('a-' + originalMessageId);
                            const storedTotal = parseInt(
                                targetContainerEl?.dataset.totalVersions
                                || oldContainer?.dataset.totalVersions
                                || '1',
                                10
                            );

                            appendAssistantDone(targetMessageId, metadata, {
                                retry_count: newRetryCount,
                                total_versions: storedTotal,
                                reference_id: userMessageId,
                                is_latest_version: true,
                            });
                        }
                        
                        // Update the old container's version info
                        const oldContainer = document.getElementById('a-' + originalMessageId);
                        if (oldContainer) {
                            const storedTotal = parseInt(oldContainer.dataset.totalVersions || '1', 10);
                            oldContainer.dataset.totalVersions = String(storedTotal);
                            oldContainer.dataset.isLatestVersion = 'false';
                        }
                        
                        if (typeof finalizeThinkingBlocks === 'function') {
                            const container = document.getElementById('a-' + targetMessageId);
                            if (container) {
                                finalizeThinkingBlocks(container);
                                const assistantThinking = container.querySelectorAll('.assistant-thinking');
                                const lastAssistantThinking = assistantThinking.length ? assistantThinking[assistantThinking.length - 1] : null;
                                if (lastAssistantThinking) {
                                    const headerSpan = lastAssistantThinking.querySelector('.assistant-thinking-title span');
                                    if (headerSpan && headerSpan.classList.contains('assistant-thinking-shimmer')) {
                                        if (typeof finalizeThinkingBlockHeader === 'function') {
                                            finalizeThinkingBlockHeader(lastAssistantThinking, temp_reasoning_time);
                                        }
                                    }
                                }
                            }
                        }
                        last_appended_message_type = '';
                    } else if (obj.t === 'e') {
                        if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleStreamEnd === 'function') {
                            try {
                                window.NotesToolSidebar.handleStreamEnd(targetMessageId);
                            } catch (error) {
                                console.error('Failed to clean up regenerated notes preview after stream error', error);
                            }
                        }
                        if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleStreamEnd === 'function') {
                            try {
                                window.canvasMarkdownWidget.handleStreamEnd(targetMessageId);
                            } catch (error) {
                                console.error('Failed to clean up regenerated canvas preview after stream error', error);
                            }
                        }
                        if (window.slidePresentationWidget && typeof window.slidePresentationWidget.handleStreamEnd === 'function') {
                            try {
                                window.slidePresentationWidget.handleStreamEnd(targetMessageId);
                            } catch (error) {
                                console.error('Failed to clean up regenerated slide presentation preview after stream error', error);
                            }
                        }
                        const translatedStreamError = obj.i18n_key
                            ? getChatPreviewTranslation(obj.i18n_key, obj.d || '')
                            : obj.d;
                        // Stream errors may contain a structured rate-limit
                        // payload. Never pass that object to the generic message
                        // renderer, because text conversion produces
                        // "[object Object]" instead of the quota warning card.
                        const errorData = { detail: translatedStreamError };
                        const detail = resolveApiErrorMessage(
                            errorData,
                            getChatPreviewTranslation('chat_regenerate_failed', 'Failed to regenerate response')
                        );
                        if (last_appended_message_type === 'loading') {
                            if (typeof removeLoading === 'function') {
                                removeLoading(targetMessageId);
                            }
                            last_appended_message_type = '';
                        }
                        clearMediaGenPlaceholderForNonFileEvent(targetMessageId);
                        if (isRateLimitErrorPayload(errorData, detail)) {
                            // Let the caller roll back the optimistic version. It
                            // owns the snapshot required to restore reused cards.
                            try {
                                await reader.cancel();
                            } catch (_) {}
                            return { rateLimited: true, errorData, detail };
                        }
                        if (typeof appendAssistantError === 'function') {
                            appendAssistantError(targetMessageId, detail, last_appended_message_type);
                        }
                        const targetContainerEl = document.getElementById('a-' + targetMessageId);
                        if (targetContainerEl) {
                            delete targetContainerEl.dataset.isStreaming;
                        }
                        if (typeof appendAssistantDone === 'function') {
                            const oldContainer = document.getElementById('a-' + originalMessageId);
                            const storedTotal = parseInt(
                                targetContainerEl?.dataset.totalVersions
                                || oldContainer?.dataset.totalVersions
                                || '1',
                                10
                            );

                            appendAssistantDone(targetMessageId, '', {
                                retry_count: newRetryCount,
                                total_versions: storedTotal,
                                reference_id: userMessageId,
                                is_latest_version: true,
                            });
                        }
                    }
                    
                    // Keep following only while the user remains attached to
                    // the bottom. Direct scroll input invalidates this frame
                    // before it can overwrite the user's position.
                    const regenerationViewport = document.getElementById('chatArea');
                    if (window.ChatScrollManager && typeof window.ChatScrollManager.scheduleFollow === 'function') {
                        window.ChatScrollManager.scheduleFollow(regenerationViewport);
                    } else if (typeof scrollChatToBottom === 'function') {
                        scrollChatToBottom();
                    }
                    
                } catch (parseError) {
                    console.warn('Failed to parse stream line:', line, parseError);
                }
            }
        }
    } finally {
        const finalMessageId = newMessageId || originalMessageId;
        if (finalMessageId && typeof clearMediaGenPlaceholderForNonFileEvent === 'function') {
            clearMediaGenPlaceholderForNonFileEvent(finalMessageId);
        }
        const generationWasCancelled = (
            activeChatGenerationTransport?.generationId === String(generationId || '')
            && activeChatGenerationTransport.cancelled
        );
        const transcriptRoot = document.getElementById('chatAreaContainer');
        if (generationWasCancelled) {
            window.finalizeCancelledAssistantStream?.(finalMessageId, transcriptRoot);
        } else {
            window.finalizeInterruptedAssistantStream?.(finalMessageId, transcriptRoot);
        }
        if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleStreamEnd === 'function') {
            try {
                window.NotesToolSidebar.handleStreamEnd(newMessageId || originalMessageId);
            } catch (error) {
                console.error('Failed to clean up regenerated notes preview after generation', error);
            }
        }
        if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleStreamEnd === 'function') {
            try {
                window.canvasMarkdownWidget.handleStreamEnd(newMessageId || originalMessageId);
            } catch (error) {
                console.error('Failed to clean up regenerated canvas preview after generation', error);
            }
        }
        if (window.slidePresentationWidget && typeof window.slidePresentationWidget.handleStreamEnd === 'function') {
            try {
                window.slidePresentationWidget.handleStreamEnd(newMessageId || originalMessageId);
            } catch (error) {
                console.error('Failed to clean up regenerated slide presentation preview after generation', error);
            }
        }
        if (String(window.currentGenerationId || '') === String(generationId || '')) {
            window.currentGenerationId = null;
        }
    }
    return { completed };
}

/**
 * Close message-specific preview sidebars before displaying another response
 * version. A preview can belong to the version that is about to be hidden, so
 * leaving it open would show stale canvas, presentation, note, PDF, or citation
 * content beside the newly selected response.
 */
function closeAssistantVersionPreviewSidebars() {
    const previewControllers = [
        window.slidePresentationWidget,
        window.canvasMarkdownWidget,
        window.latexPdfWidget,
        window.NotesToolSidebar,
    ];

    previewControllers.forEach((controller) => {
        if (!controller || typeof controller.hidePreviewPanel !== 'function') {
            return;
        }
        try {
            controller.hidePreviewPanel();
        } catch (error) {
            console.warn('Failed to close assistant preview sidebar while switching versions:', error);
        }
    });

    if (typeof window.closeCitationsSidebar === 'function') {
        try {
            window.closeCitationsSidebar();
        } catch (error) {
            console.warn('Failed to close citations sidebar while switching assistant versions:', error);
        }
    }
}

function switchAssistantVersion(referenceId, targetRetryCount) {
    // Find all assistant containers for this reference_id
    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (!chatAreaContainer) return;
    
    const toInt = (value, fallback = 0) => {
        const parsed = parseInt(value, 10);
        return Number.isNaN(parsed) ? fallback : parsed;
    };

    const allAssistantContainers = chatAreaContainer.querySelectorAll('.assistant-message-container');
    const matchingContainers = [];

    allAssistantContainers.forEach((container) => {
        if (container.dataset.referenceId === referenceId) {
            matchingContainers.push(container);
        }
    });

    if (!matchingContainers.length) {
        return;
    }

    const versionedContainers = typeof window.getAssistantContainersByReference === 'function'
        ? window.getAssistantContainersByReference(referenceId, { meaningfulOnly: true })
        : matchingContainers;
    const meaningfulVersionCount = versionedContainers.length || matchingContainers.length;
    const trackedTotalVersions = matchingContainers.reduce(
        (maximum, container) => Math.max(maximum, toInt(container.dataset.totalVersions || '1', 1)),
        1
    );
    const totalVersions = Math.max(1, meaningfulVersionCount, trackedTotalVersions);
    let maxRetryCount = -Infinity;
    let targetContainer = null;

    matchingContainers.forEach((container) => {
        maxRetryCount = Math.max(maxRetryCount, toInt(container.dataset.retryCount || '0'));
    });

    matchingContainers.forEach((container) => {
        const retryCount = toInt(container.dataset.retryCount || '0');
        const isTarget = retryCount === targetRetryCount;
        container.dataset.totalVersions = String(totalVersions);
        container.dataset.isLatestVersion = retryCount === maxRetryCount ? 'true' : 'false';
        container.style.display = isTarget ? '' : 'none';
        container.dataset.hidden = isTarget ? 'false' : 'true';
        container.setAttribute('aria-hidden', isTarget ? 'false' : 'true');
        if (typeof window.applyAssistantMessageAccessibility === 'function') {
            window.applyAssistantMessageAccessibility(container, {
                messageId: container.id && container.id.startsWith('a-') ? container.id.slice(2) : '',
                streaming: container.dataset.isStreaming === 'true',
                hasError: container.dataset.hasError === 'true',
                versionInfo: { current: retryCount + 1, total: totalVersions },
            });
        }
        if (isTarget) {
            targetContainer = container;
        }
    });

    if (!targetContainer) {
        return;
    }

    // Preview sidebars are tied to the response version that opened them. Close
    // them only after confirming that the requested version exists.
    closeAssistantVersionPreviewSidebars();

    const messageId = targetContainer.id && targetContainer.id.startsWith('a-')
        ? targetContainer.id.slice(2)
        : null;

    const isStreaming = targetContainer.dataset.isStreaming === 'true';
    const isLatest = targetRetryCount === maxRetryCount;

    if (isStreaming) {
        if (typeof window.updateAssistantVersionSwitcher === 'function') {
            window.updateAssistantVersionSwitcher(targetContainer);
        }
        return;
    }

    const metadataPayload = (() => {
        const stored = targetContainer.dataset.assistantMetadata;
        if (!stored) return null;
        try {
            return JSON.parse(stored);
        } catch (_) {
            return stored;
        }
    })();

    if (messageId && typeof appendAssistantDone === 'function') {
        appendAssistantDone(messageId, metadataPayload, {
            retry_count: targetRetryCount,
            total_versions: totalVersions,
            reference_id: referenceId,
            is_latest_version: isLatest,
        });
    }

    // Re-query after potential re-render
    const listDiv = targetContainer.querySelector('.assistant-message-list');
    if (listDiv) {
        const versionSwitcher = listDiv.querySelector('.assistant-version-switcher');
        if (versionSwitcher) {
            const versionDisplay = versionSwitcher.querySelector('.assistant-version-display');
            const prevBtn = versionSwitcher.querySelector('.assistant-version-prev');
            const nextBtn = versionSwitcher.querySelector('.assistant-version-next');

            if (versionDisplay) {
                versionDisplay.textContent = `${targetRetryCount + 1}/${totalVersions}`;
            }
            if (prevBtn) {
                prevBtn.disabled = targetRetryCount === 0;
                prevBtn.classList.toggle('disabled', targetRetryCount === 0);
            }
            if (nextBtn) {
                nextBtn.disabled = targetRetryCount >= totalVersions - 1;
                nextBtn.classList.toggle('disabled', targetRetryCount >= totalVersions - 1);
            }
        }

        // Update regenerate button visibility - only show for latest version
    }

    if (typeof window.announceChatMessage === 'function') {
        window.announceChatMessage(getChatA11yText(
            'chat_sr_response_version_status',
            'Version {current} of {total}',
            { current: targetRetryCount + 1, total: totalVersions }
        ));
    }
}

// Expose functions globally
window.triggerRegeneration = triggerRegeneration;
window.switchAssistantVersion = switchAssistantVersion;
window.registerAssistantMessageVersion = registerAssistantMessageVersion;
window.getAssistantMessageVersions = getAssistantMessageVersions;
window.clearAssistantMessageVersions = clearAssistantMessageVersions;
