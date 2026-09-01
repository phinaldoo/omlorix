// Split Screen Manager internals. Load before ../splitScreen.js in the documented order.

// ───── Enable / Disable ─────

const splitScreenInternalMIN_SPLIT_SCREEN_WIDTH = 700;

function splitScreenInternalSetAmbiguousHeaderActionsHidden(hidden) {
    if (hidden) {
        if (typeof closeHeaderDropdown === 'function') {
            closeHeaderDropdown();
        }
        window.canvasFilesDropdown?.close?.();
    }
    [
        'headerShareButton',
        'headerCanvasButtonWrap',
        'headerSaveTempChatButton',
        'headerTempChatButton',
        'headerDotsButton',
        'headerDotsButtonDropdown',
    ].forEach((id) => {
        const action = document.getElementById(id);
        if (!action) return;
        action.setAttribute('aria-hidden', hidden ? 'true' : 'false');
    });
    splitScreenInternalScheduleSplitHeaderGutterSync();
}

/**
 * Keep the split-only composer control aligned with the authoritative
 * manager state. The markup starts hidden to prevent a pre-JavaScript
 * flash, while this runtime synchronization covers URL restoration and
 * every normal or panel-driven split-screen transition.
 */
function splitScreenInternalSyncSendTargetControlVisibility() {
    const wrapper = splitScreenInternalEl('splitSendTargetWrapper');
    if (!wrapper) return;
    const shouldShow = splitScreenInternalState.active === true;
    if (!shouldShow) {
        splitScreenInternalSetSendTargetDropdownOpen(false);
    }
    wrapper.hidden = !shouldShow;
}

function splitScreenInternalEnable(options = {}) {
    if (splitScreenInternalState.active) return;
    const chatContainer = document.getElementById('chatContainer');
    const currentChatId = String(chatContainer?.getAttribute('data-chat-id') || '').trim();
    const currentProjectId = String(chatContainer?.getAttribute('data-project-id') || '').trim();
    const currentChatTitle = splitScreenInternalGetSidebarChatTitle(currentChatId);
    const shouldRestoreCurrent = options.restoreCurrent !== false;
    const hasUnsavedMainConversation = (
        shouldRestoreCurrent
        && !currentChatId
        && splitScreenInternalMainChatHasConversation()
    );
    const hasUnattachableMainGeneration = Boolean(
        shouldRestoreCurrent
        && !currentChatId
        && (
            window.isGenerating
            || chatContainer?.getAttribute('data-active-generation')
        )
    );

    // A generation without a durable chat id cannot be reattached to a
    // split panel. Entering split mode would otherwise leave an invisible
    // main stream running beside independently sendable panel streams.
    if (hasUnattachableMainGeneration) {
        notifyWarning?.(splitScreenInternalSplitScreenT(
            'split_screen_wait_for_main_generation',
            'Wait for the current response to finish or stop it before opening split screen.'
        ));
        return false;
    }

    ['left', 'right'].forEach((side) => {
        if (!splitScreenInternalGetPanelChatId(side) && !splitScreenInternalHasUnsavedTemporaryPanelConversation(side)) {
            if (side === 'left') {
                splitScreenInternalState.leftTemporary = splitScreenInternalGetDefaultPanelTemporaryMode();
            } else {
                splitScreenInternalState.rightTemporary = splitScreenInternalGetDefaultPanelTemporaryMode();
            }
        }
    });
    splitScreenInternalState.active = true;
    document.body.classList.add('split-screen-active');
    splitScreenInternalSyncSendTargetControlVisibility();
    splitScreenInternalEmitStateChanged();

    const toggleBtn = splitScreenInternalGetToggleBtn();
    if (toggleBtn) {
        toggleBtn.classList.add('active');
        toggleBtn.setAttribute('aria-pressed', 'true');
    }
    splitScreenInternalSetAmbiguousHeaderActionsHidden(true);

    if (typeof window.closeModelSelect === 'function') {
        window.closeModelSelect();
    }

    splitScreenInternalApplyMainSelectedModelToPanels();

    splitScreenInternalUpdatePanelHeader('left');
    splitScreenInternalUpdatePanelHeader('right');
    splitScreenInternalUpdatePanelSaveButtons();
    splitScreenInternalSetSendTarget(splitScreenInternalState.sendTarget);
    splitScreenInternalUpdateURL({ push: options.pushHistory !== false });
    splitScreenInternalSetupPanelScrollListeners();

    // Hide the main model select when split is active
    const modelSelect = splitScreenInternalGetMainModelSelect();
    if (modelSelect) modelSelect.classList.add('hidden');
    splitScreenInternalScheduleSplitHeaderGutterSync();

    // Show model settings panel tabs
    splitScreenInternalShowSettingsPanelTabs();

    // Apply the compact layout immediately when the viewport is narrow.
    splitScreenInternalHandleResize();

    // The toolbar toggle should preserve the conversation the user is
    // looking at. Clear the now-hidden transcript first because message DOM
    // ids are shared by the renderer and must remain unique.
    if (shouldRestoreCurrent && !splitScreenInternalState.leftChatId && !splitScreenInternalState.rightChatId) {
        if (currentChatId) {
            const mainContainer = document.getElementById('chatAreaContainer');
            if (mainContainer) mainContainer.innerHTML = '';
            void splitScreenInternalLoadChatIntoPanel(currentChatId, 'left', {
                force: true,
                title: currentChatTitle,
                projectId: currentProjectId,
            });
        } else if (hasUnsavedMainConversation && !splitScreenInternalMoveMainConversationIntoPanel('left')) {
            // The move helper restores the original DOM on failure, so it
            // is safe to tear down the partially enabled split shell.
            splitScreenInternalDisable({ skipLoadFallback: true });
            return false;
        }
    }
    return true;
}

function splitScreenInternalDisable(options = {}) {
    const { skipLoadFallback = false } = options;
    if (!splitScreenInternalState.active) return;
    const fallbackTarget = skipLoadFallback ? null : splitScreenInternalGetFallbackPanelForRestore();
    splitScreenInternalState.active = false;
    document.body.classList.remove('split-screen-active');
    splitScreenInternalSyncSendTargetControlVisibility();
    splitScreenInternalEmitStateChanged();
    splitScreenInternalInvalidateHiddenMainChatBinding();

    const toggleBtn = splitScreenInternalGetToggleBtn();
    if (toggleBtn) {
        toggleBtn.classList.remove('active');
        toggleBtn.setAttribute('aria-pressed', 'false');
    }
    splitScreenInternalSetCompactMode(false);
    splitScreenInternalSetAmbiguousHeaderActionsHidden(false);

    if (typeof window.closeModelSelect === 'function') {
        window.closeModelSelect();
    }

    // Restore main model select
    const modelSelect = splitScreenInternalGetMainModelSelect();
    if (modelSelect) modelSelect.classList.remove('hidden');

    // Hide model settings panel tabs
    splitScreenInternalHideSettingsPanelTabs();

    if (!skipLoadFallback && fallbackTarget) {
        if (fallbackTarget.type === 'chat') {
            splitScreenInternalRestorePersistedChatAsMainView(fallbackTarget.chatId);
        } else if (fallbackTarget.type === 'temp') {
            splitScreenInternalRestorePanelAsTemporaryMainView(fallbackTarget.side);
        }
    }

    // Reset panel state
    splitScreenInternalResetPanels();
    splitScreenInternalUpdateURL();
    splitScreenInternalUpdatePanelSaveButtons();
}

async function splitScreenInternalToggle() {
    if (splitScreenInternalState.active) {
        if (!await splitScreenInternalConfirmExitSplitScreen()) return false;
        splitScreenInternalDisable();
    } else {
        if (splitScreenInternalEnable() === false) return false;
    }
    return true;
}

function splitScreenInternalResetPanels() {
    const hadAnyGeneration = splitScreenInternalState.leftIsGenerating || splitScreenInternalState.rightIsGenerating;
    splitScreenInternalClosePanelActionMenus();
    splitScreenInternalClearAllPanelVisibilityReconnectState();
    // Preview sidebars are shared with the main chat, so every split-screen
    // teardown must clear them, including disable({ skipLoadFallback: true }).
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
    const leftContainer = splitScreenInternalGetLeftContainer();
    const rightContainer = splitScreenInternalGetRightContainer();
    if (leftContainer) leftContainer.innerHTML = '';
    if (rightContainer) rightContainer.innerHTML = '';

    const leftPanel = splitScreenInternalGetLeftPanel();
    const rightPanel = splitScreenInternalGetRightPanel();
    if (leftPanel) leftPanel.classList.remove('has-chat');
    if (rightPanel) rightPanel.classList.remove('has-chat');

    splitScreenInternalState.leftChatId = null;
    splitScreenInternalState.rightChatId = null;
    splitScreenInternalState.leftProjectId = null;
    splitScreenInternalState.rightProjectId = null;
    splitScreenInternalState.leftChatTitle = null;
    splitScreenInternalState.rightChatTitle = null;
    splitScreenInternalState.leftTemporary = splitScreenInternalGetDefaultPanelTemporaryMode();
    splitScreenInternalState.rightTemporary = splitScreenInternalGetDefaultPanelTemporaryMode();
    splitScreenInternalState.leftLoadToken += 1;
    splitScreenInternalState.rightLoadToken += 1;
    splitScreenInternalState.leftGenerationId = null;
    splitScreenInternalState.rightGenerationId = null;
    splitScreenInternalState.leftGenerationToken = null;
    splitScreenInternalState.rightGenerationToken = null;
    splitScreenInternalState.leftPendingCancel = false;
    splitScreenInternalState.rightPendingCancel = false;
    splitScreenInternalState.leftCancelRequested = false;
    splitScreenInternalState.rightCancelRequested = false;
    splitScreenInternalState.leftIsGenerating = false;
    splitScreenInternalState.rightIsGenerating = false;
    splitScreenInternalState.leftIsLoading = false;
    splitScreenInternalState.rightIsLoading = false;
    splitScreenInternalState.leftSaveInProgress = false;
    splitScreenInternalState.rightSaveInProgress = false;
    splitScreenInternalState.leftSettings = {};
    splitScreenInternalState.rightSettings = {};
    splitScreenInternalState.leftSettingsSchema = null;
    splitScreenInternalState.rightSettingsSchema = null;
    splitScreenInternalState.leftThinkingState = null;
    splitScreenInternalState.rightThinkingState = null;
    splitScreenInternalState.leftModelId = null;
    splitScreenInternalState.leftModel = null;
    splitScreenInternalState.leftModelName = null;
    splitScreenInternalState.leftModelIcon = null;
    splitScreenInternalState.rightModelId = null;
    splitScreenInternalState.rightModel = null;
    splitScreenInternalState.rightModelName = null;
    splitScreenInternalState.rightModelIcon = null;
    splitScreenInternalRenderPanelThinkingControl('left');
    splitScreenInternalRenderPanelThinkingControl('right');
    splitScreenInternalSetPanelLoadStatus('left', 'idle');
    splitScreenInternalSetPanelLoadStatus('right', 'idle');
    splitScreenInternalUpdatePanelHeader('left');
    splitScreenInternalUpdatePanelHeader('right');

    splitScreenInternalRefreshComposerControls();
    splitScreenInternalFlushInterruptedDraftIfReady();

    if (hadAnyGeneration && typeof window.endGenerationUI === 'function') {
        window.endGenerationUI();
    }
}

// ───── Panel Header Updates ─────

function splitScreenInternalUpdatePanelHeader(side) {
    const triggerEl = splitScreenInternalEl(side === 'left' ? 'splitLeftModel' : 'splitRightModel');
    const areaEl = side === 'left' ? splitScreenInternalGetLeftArea() : splitScreenInternalGetRightArea();
    const transcriptEl = side === 'left' ? splitScreenInternalGetLeftContainer() : splitScreenInternalGetRightContainer();
    const modelName = side === 'left' ? splitScreenInternalState.leftModelName : splitScreenInternalState.rightModelName;
    const modelIcon = side === 'left' ? splitScreenInternalState.leftModelIcon : splitScreenInternalState.rightModelIcon;
    const sideLabel = splitScreenInternalGetTranslatedSideLabel(side);
    const chatTitle = splitScreenInternalGetPanelChatTitle(side) || splitScreenInternalSplitScreenT('split_screen_new_chat', 'New chat');
    const resolvedModelName = modelName || splitScreenInternalSplitScreenT('model_select_title', 'Select model');

    if (triggerEl) {
        if (typeof window.renderModelSelectTriggerContent === 'function') {
            window.renderModelSelectTriggerContent(triggerEl, {
                name: resolvedModelName,
                model_icon: modelIcon,
            });
        } else {
            // Keep the panel header usable if the shared model-select
            // renderer is unavailable because its script failed to load.
            triggerEl.textContent = resolvedModelName;
        }
        triggerEl.setAttribute('aria-label', splitScreenInternalSplitScreenTf(
            'split_screen_select_panel_model_aria',
            'Select model for {side} panel. Current model: {model}',
            { side: sideLabel, model: resolvedModelName }
        ));
    }
    const transcriptLabel = splitScreenInternalSplitScreenTf(
        'split_screen_transcript_aria',
        '{side} chat transcript: {title}, {model}',
        { side: sideLabel, title: chatTitle, model: resolvedModelName }
    );
    areaEl?.setAttribute('aria-label', transcriptLabel);
    transcriptEl?.setAttribute('aria-label', transcriptLabel);
    splitScreenInternalUpdateSettingsTabLabel(side);
    splitScreenInternalUpdatePanelActionsMenu(side);
}

function splitScreenInternalUpdateSettingsTabLabel(side) {
    const tabs = document.getElementById('splitSettingsPanelTabs');
    if (!tabs) return;
    const tab = tabs.querySelector(`.split-settings-tab[data-settings-panel="${side}"]`);
    if (!tab) return;
    const labelSpan = tab.querySelector('.split-settings-tab-label');
    if (!labelSpan) return;
    const modelName = side === 'left' ? splitScreenInternalState.leftModelName : splitScreenInternalState.rightModelName;
    const fallback = side === 'left'
        ? splitScreenInternalSplitScreenT('split_screen_left_chat', 'Left Chat')
        : splitScreenInternalSplitScreenT('split_screen_right_chat', 'Right Chat');
    labelSpan.textContent = modelName || fallback;
}

// ───── Load Chat Into Panel ─────

async function splitScreenInternalLoadChatIntoPanel(chatId, side, options = {}) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId || !['left', 'right'].includes(side)) return false;
    const hasExplicitProject = Object.prototype.hasOwnProperty.call(options, 'projectId');

    const otherSide = splitScreenInternalGetOtherSide(side);
    if (String(splitScreenInternalGetPanelChatId(otherSide) || '') === normalizedChatId) {
        notifyWarning?.(splitScreenInternalSplitScreenT(
            'split_screen_duplicate_chat_warning',
            'That chat is already open in the other panel.'
        ));
        return false;
    }

    const currentChatId = String(splitScreenInternalGetPanelChatId(side) || '').trim();
    if (
        currentChatId === normalizedChatId
        && (splitScreenInternalIsSideLoading(side) || splitScreenInternalIsSideGenerating(side))
    ) {
        if (hasExplicitProject) {
            splitScreenInternalSetPanelProjectId(side, options.projectId);
        }
        // Re-selecting the panel's current chat must not tear down its
        // in-flight transcript load or attached generation.
        return true;
    }
    const isReplacingCurrentPanel = splitScreenInternalPanelHasReplaceableContent(side) && currentChatId !== normalizedChatId;
    if (isReplacingCurrentPanel) {
        if (options.force !== true) {
            const confirmed = await splitScreenInternalConfirmPanelReplacement(side, { action: 'replace' });
            if (!confirmed) return false;
        }
        // "force" skips the prompt for controlled restore paths; it never
        // permits an old backend generation to continue without an owner.
        if (!await splitScreenInternalStopPanelGenerationForReplacement(side)) return false;
    }

    const container = side === 'left' ? splitScreenInternalGetLeftContainer() : splitScreenInternalGetRightContainer();
    const panel = side === 'left' ? splitScreenInternalGetLeftPanel() : splitScreenInternalGetRightPanel();
    const area = side === 'left' ? splitScreenInternalGetLeftArea() : splitScreenInternalGetRightArea();
    if (!container || !panel) return;

    const loadToken = side === 'left' ? ++splitScreenInternalState.leftLoadToken : ++splitScreenInternalState.rightLoadToken;
    splitScreenInternalSetSideLoading(side, true);
    splitScreenInternalFinishPanelGeneration(side);
    const handoffGenerationId = splitScreenInternalDetachMainChatStreamForSplit(normalizedChatId);

    // Clear existing content
    container.innerHTML = '';

    // Update state
    if (side === 'left') {
        splitScreenInternalState.leftChatId = normalizedChatId;
        splitScreenInternalState.leftTemporary = false;
    } else {
        splitScreenInternalState.rightChatId = normalizedChatId;
        splitScreenInternalState.rightTemporary = false;
    }
    splitScreenInternalSetPanelProjectId(side, hasExplicitProject ? options.projectId : null);
    splitScreenInternalSetPanelChatTitle(side, options.title || splitScreenInternalGetSidebarChatTitle(normalizedChatId));
    splitScreenInternalUpdatePanelSaveButtons();

    panel.classList.add('has-chat');
    splitScreenInternalSetPanelLoadStatus(
        side,
        'loading',
        splitScreenInternalSplitScreenTf('split_screen_loading_panel', 'Loading {side} chat…', { side: splitScreenInternalGetTranslatedSideLabel(side) })
    );

    try {
        const statusPromise = handoffGenerationId
            ? Promise.resolve({ active: true, generation_id: handoffGenerationId })
            : splitScreenInternalFetchPanelGenerationStatus(normalizedChatId);
        const [response, status] = await Promise.all([
            window.authedFetch(`/api/v1/chats/messages?chat_id=${encodeURIComponent(normalizedChatId)}`, {
                method: 'GET'
            }),
            statusPromise,
        ]);

        const isCurrentLoad = (side === 'left' ? splitScreenInternalState.leftLoadToken : splitScreenInternalState.rightLoadToken) === loadToken;
        if (!isCurrentLoad || !splitScreenInternalIsPanelStillBoundToChat(side, normalizedChatId)) {
            return false;
        }

        if (!response.ok) {
            const errorMessage = splitScreenInternalSplitScreenTf(
                'split_screen_load_chat_failed_status',
                'Failed to load chat ({status})',
                { status: response.status }
            );
            splitScreenInternalSetPanelLoadStatus(side, 'error', errorMessage);
            return false;
        }

        const messages = await response.json();
        if (!splitScreenInternalIsPanelStillBoundToChat(side, normalizedChatId)) {
            return false;
        }
        splitScreenInternalRenderMessagesIntoContainer(messages, container, {
            keepTrailingAssistantStreaming: Boolean(status?.active && status?.generation_id),
        });
        window.ChatAttention?.markRead(normalizedChatId);
        splitScreenInternalSetPanelLoadStatus(side, 'idle');

        // Scroll to bottom
        splitScreenInternalScrollSplitAreaToBottom(area);

        if (status?.active && status?.generation_id && splitScreenInternalIsPanelStillBoundToChat(side, normalizedChatId)) {
            // The attachment path marks generation state synchronously.
            // Clear only the transcript-loading guard before it takes over.
            splitScreenInternalFinishSideLoading(side, loadToken);
            await splitScreenInternalAttachPanelToGeneration(side, String(status.generation_id), { chatId: normalizedChatId });
        }
    } catch (error) {
        console.error('Failed to load chat into panel:', error);
        const isCurrentLoad = (side === 'left' ? splitScreenInternalState.leftLoadToken : splitScreenInternalState.rightLoadToken) === loadToken;
        if (isCurrentLoad && splitScreenInternalIsPanelStillBoundToChat(side, normalizedChatId)) {
            splitScreenInternalSetPanelLoadStatus(
                side,
                'error',
                splitScreenInternalSplitScreenT('chat_load_messages_failed', 'Failed to load chat messages')
            );
        }
        return false;
    } finally {
        splitScreenInternalFinishSideLoading(side, loadToken);
    }

    splitScreenInternalUpdateURL();
    splitScreenInternalUpdatePanelSaveButtons();
    return true;
}

function splitScreenInternalRenderMessagesIntoContainer(messages, container, options = {}) {
    if (typeof window.renderChatTranscript === 'function') {
        window.renderChatTranscript(messages, {
            container,
            clearContainer: false,
            trackAssistantVersions: false,
            readOnly: false,
            keepTrailingAssistantStreaming: options.keepTrailingAssistantStreaming === true,
        });
    }
}

async function splitScreenInternalAttachPanelToGeneration(side, generationId, options = {}) {
    const normalizedGenerationId = String(generationId || '').trim();
    const normalizedChatId = String(options.chatId || splitScreenInternalGetPanelChatId(side) || '').trim();
    const container = side === 'left' ? splitScreenInternalGetLeftContainer() : splitScreenInternalGetRightContainer();
    const area = side === 'left' ? splitScreenInternalGetLeftArea() : splitScreenInternalGetRightArea();
    if (!normalizedGenerationId || !container || !splitScreenInternalIsPanelStillBoundToChat(side, normalizedChatId)) {
        return false;
    }

    // Mark the panel busy before the attach request. Otherwise the composer
    // can submit another turn while an already-running response is between
    // status discovery and stream attachment.
    const generationToken = splitScreenInternalStartPanelGeneration(side, normalizedGenerationId);
    let streamedMessageId = '';
    try {
        let response;
        try {
            const params = new URLSearchParams({ generation_id: normalizedGenerationId });
            response = await window.authedFetch(`/api/v1/chats/attach?${params.toString()}`, {
                method: 'GET',
                signal: side === 'left'
                    ? splitScreenInternalState.leftAbortController?.signal
                    : splitScreenInternalState.rightAbortController?.signal,
            });
        } catch (_) {
            if (splitScreenInternalIsPanelCancellationRequested(side)) {
                return true;
            }
            if (typeof notifyError === 'function') {
                notifyError(splitScreenInternalSplitScreenTf('split_screen_attach_generation_failed', 'Error attaching to generation {generationId}', { generationId: normalizedGenerationId }));
            }
            return false;
        }

        if (!response.ok) {
            if (typeof notifyError === 'function') {
                notifyError(splitScreenInternalSplitScreenTf('split_screen_attach_generation_failed', 'Error attaching to generation {generationId}', { generationId: normalizedGenerationId }));
            }
            return false;
        }

        streamedMessageId = await splitScreenInternalProcessStream(response, side, '', container, area, {
            generationToken,
            onDone: () => {
                splitScreenInternalFinishPanelGeneration(side, generationToken, 'finished');
            },
            onMessageId: (nextMessageId) => {
                streamedMessageId = String(nextMessageId || '');
            },
        });
        return true;
    } finally {
        if (window.NotesToolSidebar && typeof window.NotesToolSidebar.handleStreamEnd === 'function') {
            try {
                // Reattached generations need the same fallback cleanup as
                // newly sent panel generations when the stream ends before
                // a Notes artifact is persisted.
                window.NotesToolSidebar.handleStreamEnd(streamedMessageId);
            } catch (error) {
                console.error('Failed to clean up reattached split-screen notes preview', error);
            }
        }
        if (window.canvasMarkdownWidget && typeof window.canvasMarkdownWidget.handleStreamEnd === 'function') {
            try {
                window.canvasMarkdownWidget.handleStreamEnd(streamedMessageId);
            } catch (error) {
                console.error('Failed to clean up reattached split-screen canvas preview', error);
            }
        }
        if (window.slidePresentationWidget && typeof window.slidePresentationWidget.handleStreamEnd === 'function') {
            try {
                window.slidePresentationWidget.handleStreamEnd(streamedMessageId);
            } catch (error) {
                console.error('Failed to clean up reattached split-screen slide presentation preview', error);
            }
        }
        splitScreenInternalFinishPanelGeneration(side, generationToken);
    }
}
