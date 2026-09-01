// Split Screen Manager internals. Load before ../splitScreen.js in the documented order.

// ───── Send Target ─────

function splitScreenInternalSetSendTarget(target) {
    if (!['both', 'left', 'right'].includes(target)) return;
    splitScreenInternalState.sendTarget = target;
    splitScreenInternalUpdateSendTargetLabel();

    // Keep the target menu on the same shared lifecycle as the other
    // composer dropdowns, including focus, Escape, and ARIA state.
    splitScreenInternalSetSendTargetDropdownOpen(false);

    // The shared dropdown design marks the selected radio item with the
    // same trailing check icon used by the main composer menus.
    splitScreenInternalGetSendTargetDd()?.querySelectorAll('[data-split-send-target]').forEach(opt => {
        const isActive = opt.dataset.splitSendTarget === target;
        opt.setAttribute('aria-checked', isActive ? 'true' : 'false');
        opt.querySelector('[data-split-send-target-check]')?.remove();
        if (isActive && typeof Icons === 'object' && Icons?.check) {
            opt.insertAdjacentHTML('beforeend', Icons.check);
            const checkIcon = opt.lastElementChild;
            checkIcon?.setAttribute('data-split-send-target-check', '');
            checkIcon?.setAttribute('aria-hidden', 'true');
        }
    });

    ['left', 'right'].forEach((side) => {
        const panel = side === 'left' ? splitScreenInternalGetLeftPanel() : splitScreenInternalGetRightPanel();
        const targeted = target === 'both' || target === side;
        panel?.classList.toggle('is-send-target', targeted);
        panel?.setAttribute('data-send-target', targeted ? 'true' : 'false');
    });
    if (document.body.classList.contains('split-screen-compact') && target !== 'both') {
        splitScreenInternalSetCompactPanel(target);
    }

    splitScreenInternalEmitStateChanged();
    splitScreenInternalRefreshComposerControls();
    splitScreenInternalFlushInterruptedDraftIfReady();
}

function splitScreenInternalUpdateSendTargetLabel() {
    const label = splitScreenInternalGetSendTargetLabel();
    if (!label) return;
    const labels = {
        both: splitScreenInternalSplitScreenT('split_screen_target_both_short', 'Both'),
        left: splitScreenInternalSplitScreenT('split_screen_target_left_short', 'Left'),
        right: splitScreenInternalSplitScreenT('split_screen_target_right_short', 'Right'),
    };
    label.textContent = labels[splitScreenInternalState.sendTarget] || labels.both;
    splitScreenInternalUpdateSendTargetButtonIcon();
}

function splitScreenInternalSetSendTargetDropdownOpen(open) {
    if (!splitScreenInternalSendTargetDropdownController) return;
    splitScreenInternalSendTargetDropdownController[open ? 'open' : 'close']({ reason: 'api' });
}

// ───── Model Selection for Panels (shared selector) ─────

function splitScreenInternalClosePanelDropdowns() {
    ['splitLeftModel', 'splitRightModel'].forEach(id => {
        const trigger = splitScreenInternalEl(id);
        if (trigger) {
            trigger.classList.remove('open');
            trigger.setAttribute('aria-expanded', 'false');
        }
    });
    const context = typeof window.getModelSelectContext === 'function'
        ? window.getModelSelectContext()
        : null;
    if (context?.mode === 'split' && typeof window.closeModelSelect === 'function') {
        window.closeModelSelect();
    }
}

async function splitScreenInternalTogglePanelModelDropdown(side) {
    const triggerId = side === 'left' ? 'splitLeftModel' : 'splitRightModel';
    const trigger = splitScreenInternalEl(triggerId);
    if (!trigger || typeof window.toggleModelSelect !== 'function') return;

    const context = typeof window.getModelSelectContext === 'function'
        ? window.getModelSelectContext()
        : null;
    const isAlreadyOpen = context?.mode === 'split' && context?.side === side;
    splitScreenInternalClosePanelActionMenus();
    splitScreenInternalClosePanelDropdowns();
    if (isAlreadyOpen) {
        return;
    }
    trigger.classList.add('open');
    trigger.setAttribute('aria-expanded', 'true');
    await window.toggleModelSelect({
        mode: 'split',
        side,
        anchorEl: trigger,
        selectedModelId: side === 'left' ? splitScreenInternalState.leftModelId : splitScreenInternalState.rightModelId,
        onSelect: async (model) => {
            splitScreenInternalSelectModelForPanel(side, model);
        },
        onClose: () => {
            splitScreenInternalClosePanelDropdowns();
        },
    });
}

function splitScreenInternalSelectModelForPanel(side, model) {
    splitScreenInternalPersistVisibleSettingsPanel();
    const modelId = model.model_id;
    const modelName = model.name || model.model_id;
    const modelIcon = typeof resolveModelIcon === 'function' ? resolveModelIcon(model.model_icon) : null;

    if (side === 'left') {
        splitScreenInternalState.leftModelId = modelId;
        splitScreenInternalState.leftModel = { ...model };
        splitScreenInternalState.leftModelName = modelName;
        splitScreenInternalState.leftModelIcon = modelIcon;
        splitScreenInternalState.leftSettings = {};
        splitScreenInternalState.leftSettingsSchema = null;
        splitScreenInternalState.leftThinkingState = null;
    } else {
        splitScreenInternalState.rightModelId = modelId;
        splitScreenInternalState.rightModel = { ...model };
        splitScreenInternalState.rightModelName = modelName;
        splitScreenInternalState.rightModelIcon = modelIcon;
        splitScreenInternalState.rightSettings = {};
        splitScreenInternalState.rightSettingsSchema = null;
        splitScreenInternalState.rightThinkingState = null;
    }

    splitScreenInternalUpdatePanelHeader(side);
    splitScreenInternalRenderPanelThinkingControl(side);
    splitScreenInternalEnsurePanelSettingsSchema(side).catch(() => {});
    splitScreenInternalRefreshVisibleSettingsPanel();
}

function splitScreenInternalSetupModelSelectListener() {
    // Close panel dropdowns when clicking elsewhere
    document.addEventListener('click', (e) => {
        const leftModel = splitScreenInternalEl('splitLeftModel');
        const rightModel = splitScreenInternalEl('splitRightModel');
        if (leftModel && leftModel.contains(e.target)) return;
        if (rightModel && rightModel.contains(e.target)) return;
        splitScreenInternalClosePanelDropdowns();
    });
}

// ───── Model Settings Sidebar Tabs ─────

function splitScreenInternalShowSettingsPanelTabs() {
    const tabs = document.getElementById('splitSettingsPanelTabs');
    if (!tabs) return;
    tabs.style.display = 'flex';
    splitScreenInternalUpdateSettingsTabLabel('left');
    splitScreenInternalUpdateSettingsTabLabel('right');
    // Default to left tab active
    const activeTab = tabs.querySelector('.split-settings-tab.active');
    if (!activeTab) {
        splitScreenInternalSwitchSettingsTab('left');
    } else {
        splitScreenInternalSwitchSettingsTab(activeTab.dataset.settingsPanel);
    }
}

function splitScreenInternalHideSettingsPanelTabs() {
    const tabs = document.getElementById('splitSettingsPanelTabs');
    if (!tabs) return;
    tabs.style.display = 'none';
    // Load global model settings when exiting split mode
    const activeModelId = typeof window.getActiveModelId === 'function' ? window.getActiveModelId() : null;
    if (activeModelId && typeof window.loadModelSettingsFor === 'function') {
        window.loadModelSettingsFor(activeModelId);
    }
}

function splitScreenInternalSwitchSettingsTab(side) {
    const tabs = document.getElementById('splitSettingsPanelTabs');
    if (!tabs) return;

    splitScreenInternalPersistVisibleSettingsPanel();
    tabs.querySelectorAll('.split-settings-tab').forEach(tab => {
        const active = tab.dataset.settingsPanel === side;
        tab.classList.toggle('active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
    });

    // Load model settings for the selected panel's model
    const modelId = side === 'left' ? splitScreenInternalState.leftModelId : splitScreenInternalState.rightModelId;
    if (modelId) {
        splitScreenInternalRefreshPanelSettingsSidebar(side);
    }
}

function splitScreenInternalSetupSettingsTabs() {
    const tabs = document.getElementById('splitSettingsPanelTabs');
    if (!tabs) return;

    tabs.querySelectorAll('.split-settings-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            splitScreenInternalSwitchSettingsTab(tab.dataset.settingsPanel);
        });
        tab.addEventListener('keydown', (event) => {
            const allTabs = Array.from(tabs.querySelectorAll('.split-settings-tab'));
            const currentIndex = allTabs.indexOf(tab);
            let nextIndex = null;
            if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + allTabs.length) % allTabs.length;
            if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % allTabs.length;
            if (event.key === 'Home') nextIndex = 0;
            if (event.key === 'End') nextIndex = allTabs.length - 1;
            if (nextIndex === null) return;
            event.preventDefault();
            const nextTab = allTabs[nextIndex];
            splitScreenInternalSwitchSettingsTab(nextTab.dataset.settingsPanel);
            nextTab.focus();
        });
    });
}

function splitScreenInternalSetupPanelThinkingControls() {
    ['left', 'right'].forEach((side) => {
        const container = splitScreenInternalGetPanelThinkingContainer(side);
        const button = splitScreenInternalGetPanelThinkingButton(side);
        const dropdown = splitScreenInternalGetPanelThinkingDropdown(side);
        if (container) {
            container.addEventListener('click', (event) => {
                event.stopPropagation();
            });
        }
        if (button) {
            button.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                if (button.disabled) {
                    return;
                }
                splitScreenInternalClosePanelActionMenus();
                splitScreenInternalClosePanelDropdowns();
                splitScreenInternalClosePanelThinkingDropdowns(side);
                const shouldOpen = !dropdown?.classList.contains('open');
                splitScreenInternalSetPanelThinkingDropdownOpen(side, shouldOpen);
            });
        }
    });

    document.addEventListener('click', () => {
        splitScreenInternalClosePanelThinkingDropdowns();
    });

    document.addEventListener('i18n:updated', () => {
        splitScreenInternalUpdatePanelThinkingState('left');
        splitScreenInternalUpdatePanelThinkingState('right');
        splitScreenInternalUpdatePanelHeader('left');
        splitScreenInternalUpdatePanelHeader('right');
        splitScreenInternalUpdateSendTargetLabel();
    });
}

function splitScreenInternalRefreshVisibleSettingsPanel() {
    const tabs = document.getElementById('splitSettingsPanelTabs');
    if (!tabs) return;
    splitScreenInternalPersistVisibleSettingsPanel();
    const activeTab = tabs.querySelector('.split-settings-tab.active');
    const side = activeTab?.dataset.settingsPanel || 'left';
    const modelId = side === 'left' ? splitScreenInternalState.leftModelId : splitScreenInternalState.rightModelId;
    if (!modelId) return;
    splitScreenInternalRefreshPanelSettingsSidebar(side);
}

// ───── Close Panel ─────

async function splitScreenInternalClosePanel(side) {
    const confirmed = await splitScreenInternalConfirmPanelReplacement(side, { action: 'close' });
    if (!confirmed) return false;
    // Determine which chat survives (the OTHER panel)
    const survivingChatId = side === 'left' ? splitScreenInternalState.rightChatId : splitScreenInternalState.leftChatId;
    const survivingModelId = side === 'left' ? splitScreenInternalState.rightModelId : splitScreenInternalState.leftModelId;
    const survivingSide = side === 'left' ? 'right' : 'left';
    const shouldRestoreTemp = !survivingChatId && splitScreenInternalIsPanelTemporary(survivingSide);
    if (!shouldRestoreTemp && !survivingChatId && splitScreenInternalIsSideGenerating(survivingSide)) {
        // A normal new chat receives its durable chat id through the stream.
        // Wait for that handoff rather than cancelling a stream that cannot
        // yet be restored in either persisted or temporary main-chat mode.
        notifyWarning?.(splitScreenInternalSplitScreenTf(
            'split_screen_wait_for_generation_start',
            'The {side} response is still starting. Try again in a moment.',
            { side: splitScreenInternalGetTranslatedSideLabel(survivingSide) }
        ));
        return false;
    }
    const mustStopTemporarySurvivor = shouldRestoreTemp && splitScreenInternalIsSideGenerating(survivingSide);

    // A temporary generation cannot be reattached after its transcript moves
    // to the main view. Confirm this separately before stopping either side,
    // so cancelling the second prompt leaves all current streams untouched.
    if (mustStopTemporarySurvivor) {
        if (typeof window.showWarningConfirm !== 'function') return false;
        const stopConfirmed = await window.showWarningConfirm({
            title: splitScreenInternalSplitScreenT('split_screen_exit_confirm_title', 'Leave split screen?'),
            message: splitScreenInternalSplitScreenT(
                'split_screen_exit_all_generating',
                'Running split-screen responses will be stopped.'
            ),
            confirmLabel: splitScreenInternalSplitScreenT('split_screen_exit_confirm_button', 'Leave split screen'),
            cancelLabel: splitScreenInternalSplitScreenT('common_cancel', 'Cancel'),
            danger: false,
        });
        if (!stopConfirmed) return false;
    }

    if (!await splitScreenInternalStopPanelGenerationForReplacement(side)) return false;
    if (mustStopTemporarySurvivor
        && !await splitScreenInternalStopPanelGenerationForReplacement(survivingSide)) {
        return false;
    }

    // Disable split-screen entirely
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
    const modelSelectWrap = splitScreenInternalGetMainModelSelect();
    if (modelSelectWrap) modelSelectWrap.classList.remove('hidden');

    // Hide model settings panel tabs
    splitScreenInternalHideSettingsPanelTabs();

    // Clear URL split params
    const url = new URL(window.location);
    url.searchParams.delete('split');
    url.searchParams.delete('left');
    url.searchParams.delete('right');
    window.history.replaceState(null, '', url);

    // If the surviving panel had a model, select it globally
    if (survivingModelId && typeof window.selectModel === 'function') {
        // Find the model object to pass to selectModel
        const models = typeof window.BYOK?.getAllSelectableModels === 'function'
            ? (window.BYOK.getAllSelectableModels(window.BYOK.getAdminModels?.() || []).allModels || [])
            : null;
        if (models) {
            const model = models.find(m => String(m.model_id) === String(survivingModelId));
            if (model) {
                window.selectModel(model);
            }
        }
    }

    // Load the surviving chat as full-screen, or go to welcome
    const restoredTempPanel = shouldRestoreTemp ? splitScreenInternalRestorePanelAsTemporaryMainView(survivingSide) : false;
    if (survivingChatId) {
        splitScreenInternalRestorePersistedChatAsMainView(survivingChatId);
    } else if (!restoredTempPanel && typeof showChatStartContainer === 'function') {
        showChatStartContainer();
    }

    // Reset panel state
    splitScreenInternalResetPanels();
    splitScreenInternalUpdatePanelSaveButtons();
    return true;
}

// ───── Divider Resize ─────

function splitScreenInternalInitDividerResize() {
    const divider = splitScreenInternalGetDivider();
    if (!divider) return;

    const MIN_PANEL_WIDTH = 260;

    const applyLeftPanelWidth = (requestedWidth) => {
        const wrapper = splitScreenInternalGetWrapper();
        const leftPanel = splitScreenInternalGetLeftPanel();
        const rightPanel = splitScreenInternalGetRightPanel();
        if (!wrapper || !leftPanel || !rightPanel) return false;

        const wrapperWidth = wrapper.getBoundingClientRect().width;
        const dividerWidth = divider.getBoundingClientRect().width || 1;
        const availableWidth = Math.max(0, wrapperWidth - dividerWidth);
        if (availableWidth < MIN_PANEL_WIDTH * 2) return false;

        const clampedWidth = Math.min(
            availableWidth - MIN_PANEL_WIDTH,
            Math.max(MIN_PANEL_WIDTH, requestedWidth)
        );
        const leftPercent = (clampedWidth / availableWidth) * 100;
        leftPanel.style.flex = 'none';
        leftPanel.style.width = `${leftPercent}%`;
        rightPanel.style.flex = '1';
        const roundedPercent = Math.round(leftPercent);
        splitScreenInternalSetSplitHeaderRatio(leftPercent);
        divider.setAttribute('aria-valuenow', String(roundedPercent));
        divider.setAttribute('aria-valuetext', splitScreenInternalSplitScreenTf(
            'split_screen_divider_value',
            'Left panel {percent} percent',
            { percent: roundedPercent }
        ));
        return true;
    };

    const resetPanelWidths = () => {
        const leftPanel = splitScreenInternalGetLeftPanel();
        const rightPanel = splitScreenInternalGetRightPanel();
        if (!leftPanel || !rightPanel) return;
        leftPanel.style.removeProperty('width');
        leftPanel.style.removeProperty('flex');
        rightPanel.style.removeProperty('flex');
        splitScreenInternalSetSplitHeaderRatio(50);
        divider.setAttribute('aria-valuenow', '50');
        divider.setAttribute('aria-valuetext', splitScreenInternalSplitScreenTf(
            'split_screen_divider_value',
            'Left panel {percent} percent',
            { percent: 50 }
        ));
    };

    const finishResize = (pointerId = null) => {
        splitScreenInternalState.isResizing = false;
        divider.classList.remove('dragging');
        document.body.classList.remove('split-screen-resizing');
        if (pointerId !== null && divider.hasPointerCapture?.(pointerId)) {
            divider.releasePointerCapture(pointerId);
        }
    };

    divider.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        splitScreenInternalState.isResizing = true;
        splitScreenInternalState.resizeStartX = e.clientX;
        const leftPanel = splitScreenInternalGetLeftPanel();
        splitScreenInternalState.resizeStartLeftWidth = leftPanel ? leftPanel.getBoundingClientRect().width : 0;
        divider.classList.add('dragging');
        document.body.classList.add('split-screen-resizing');
        divider.setPointerCapture(e.pointerId);
    });

    divider.addEventListener('pointermove', (e) => {
        if (!splitScreenInternalState.isResizing) return;
        const wrapper = splitScreenInternalGetWrapper();
        const leftPanel = splitScreenInternalGetLeftPanel();
        if (!wrapper || !leftPanel) return;
        const delta = e.clientX - splitScreenInternalState.resizeStartX;
        const newLeftWidth = splitScreenInternalState.resizeStartLeftWidth + delta;
        applyLeftPanelWidth(newLeftWidth);
    });

    divider.addEventListener('pointerup', (e) => {
        finishResize(e.pointerId);
    });
    divider.addEventListener('pointercancel', (e) => finishResize(e.pointerId));
    divider.addEventListener('lostpointercapture', () => finishResize());
    divider.addEventListener('dblclick', resetPanelWidths);
    divider.addEventListener('keydown', (event) => {
        if (!splitScreenInternalState.active) return;
        if (event.key === 'Home') {
            event.preventDefault();
            applyLeftPanelWidth(MIN_PANEL_WIDTH);
            return;
        }
        if (event.key === 'End') {
            event.preventDefault();
            const wrapperWidth = splitScreenInternalGetWrapper()?.getBoundingClientRect().width || 0;
            applyLeftPanelWidth(wrapperWidth - MIN_PANEL_WIDTH);
            return;
        }
        if (event.key === 'Enter') {
            event.preventDefault();
            resetPanelWidths();
            return;
        }
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        const leftWidth = splitScreenInternalGetLeftPanel()?.getBoundingClientRect().width || 0;
        const step = event.shiftKey ? 40 : 16;
        applyLeftPanelWidth(leftWidth + (event.key === 'ArrowRight' ? step : -step));
    });

    resetPanelWidths();
}

// ───── Drag and Drop from Sidebar ─────

const splitScreenInternalCHAT_DRAG_MIME = 'application/x-omlorix-chat-reference';

function splitScreenInternalHasSidebarChatDragPayload(dataTransfer) {
    return Boolean(dataTransfer && Array.from(dataTransfer.types || []).includes(splitScreenInternalCHAT_DRAG_MIME));
}

function splitScreenInternalReadSidebarChatDragPayload(dataTransfer) {
    if (!splitScreenInternalHasSidebarChatDragPayload(dataTransfer)) return null;
    try {
        const payload = JSON.parse(dataTransfer.getData(splitScreenInternalCHAT_DRAG_MIME) || '{}');
        const chatId = String(payload?.chat_id || '').trim();
        if (!chatId) return null;
        return {
            chatId,
            title: String(payload?.title || '').trim(),
            projectId: String(payload?.project_id || '').trim() || null,
        };
    } catch (_) {
        return null;
    }
}

function splitScreenInternalSetupSidebarDragAndDrop() {
    // Setup drop zones on split panels
    // Only the dedicated chat-reference MIME payload is accepted. This
    // prevents selected text and file drags from being mistaken for chats.
    ['splitScreenLeft', 'splitScreenRight'].forEach(panelId => {
        const panel = document.getElementById(panelId);
        if (!panel) return;

        panel.addEventListener('dragover', (e) => {
            if (!splitScreenInternalState.active || !splitScreenInternalHasSidebarChatDragPayload(e.dataTransfer)) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
            panel.classList.add('drop-target');
        });

        panel.addEventListener('dragleave', (e) => {
            if (!panel.contains(e.relatedTarget)) {
                panel.classList.remove('drop-target');
            }
        });

        panel.addEventListener('drop', async (e) => {
            if (!splitScreenInternalState.active) return;
            panel.classList.remove('drop-target');
            const payload = splitScreenInternalReadSidebarChatDragPayload(e.dataTransfer);
            if (!payload) return;
            e.preventDefault();

            const side = panelId === 'splitScreenLeft' ? 'left' : 'right';
            await splitScreenInternalLoadChatIntoPanel(payload.chatId, side, {
                title: payload.title,
                projectId: payload.projectId,
            });
        });
    });

    // Drop zone on the main chat area (when NOT in split mode)
    // Dragging a different chat onto the open chat enters split-screen.
    // A two-half preview overlay (#splitDropPreview) highlights the side
    // the dropped chat would open on while the drag hovers the area.
    const mainChatArea = document.getElementById('chatArea');

    /** Remove all drop-preview state from the main chat area. */
    function clearMainAreaDropPreview() {
        if (!mainChatArea) return;
        mainChatArea.classList.remove('drop-target-split', 'drop-split-left', 'drop-split-right');
    }

    /**
     * Align the drop preview overlay with the visible chat area.
     * The preview lives next to #chatArea (inside .chat-container-main)
     * because #chatArea scrolls; an absolutely positioned child would
     * move with the scrolled content.
     */
    function positionMainAreaDropPreview() {
        const preview = document.getElementById('splitDropPreview');
        if (!preview || !mainChatArea) return;
        preview.style.top = `${mainChatArea.offsetTop}px`;
        preview.style.height = `${mainChatArea.offsetHeight}px`;
    }

    if (mainChatArea) {
        mainChatArea.addEventListener('dragover', (e) => {
            if (splitScreenInternalState.active) return; // already in split mode
            if (!splitScreenInternalHasSidebarChatDragPayload(e.dataTransfer)) return;
            const chatContainer = document.getElementById('chatContainer');
            const currentChatId = chatContainer?.getAttribute('data-chat-id');
            if (!currentChatId) return; // no chat open
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
            if (!mainChatArea.classList.contains('drop-target-split')) {
                positionMainAreaDropPreview();
                mainChatArea.classList.add('drop-target-split');
            }
            // Highlight the half the chat would open on
            const rect = mainChatArea.getBoundingClientRect();
            const isLeftHalf = (e.clientX - rect.left) < rect.width / 2;
            mainChatArea.classList.toggle('drop-split-left', isLeftHalf);
            mainChatArea.classList.toggle('drop-split-right', !isLeftHalf);
        });

        mainChatArea.addEventListener('dragleave', (e) => {
            if (!mainChatArea.contains(e.relatedTarget)) {
                clearMainAreaDropPreview();
            }
        });

        mainChatArea.addEventListener('drop', async (e) => {
            clearMainAreaDropPreview();
            if (splitScreenInternalState.active) return;
            e.preventDefault();
            const payload = splitScreenInternalReadSidebarChatDragPayload(e.dataTransfer);
            if (!payload) return;
            const droppedChatId = payload.chatId;

            const chatContainer = document.getElementById('chatContainer');
            const currentChatId = chatContainer?.getAttribute('data-chat-id');
            const currentProjectId = chatContainer?.getAttribute('data-project-id');
            if (!currentChatId) return;

            // Don't split with the same chat
            if (String(droppedChatId) === String(currentChatId)) return;

            // Determine drop side: left half → put dropped chat on left, current on right
            const rect = mainChatArea.getBoundingClientRect();
            const dropX = e.clientX - rect.left;
            const isLeftHalf = dropX < rect.width / 2;

            // Enter split-screen
            if (splitScreenInternalEnable({ restoreCurrent: false }) === false) return;

            // Clear main chat container to prevent ID conflicts (duplicate message IDs)
            // which would prevent the current chat from rendering in the new panel
            const mainContainer = document.getElementById('chatAreaContainer');
            if (mainContainer) mainContainer.innerHTML = '';

            if (isLeftHalf) {
                await splitScreenInternalLoadChatIntoPanel(droppedChatId, 'left', {
                    title: payload.title,
                    projectId: payload.projectId,
                    force: true,
                });
                await splitScreenInternalLoadChatIntoPanel(currentChatId, 'right', {
                    projectId: currentProjectId,
                    force: true,
                });
            } else {
                await splitScreenInternalLoadChatIntoPanel(currentChatId, 'left', {
                    projectId: currentProjectId,
                    force: true,
                });
                await splitScreenInternalLoadChatIntoPanel(droppedChatId, 'right', {
                    title: payload.title,
                    projectId: payload.projectId,
                    force: true,
                });
            }
        });
    }

    // Safety net: when any drag ends (cancelled or dropped elsewhere),
    // clear lingering drop highlights from the panels and the main area.
    document.addEventListener('dragend', () => {
        clearMainAreaDropPreview();
        document.querySelectorAll('.split-screen-panel.drop-target').forEach((panel) => {
            panel.classList.remove('drop-target');
        });
    });
}

// ───── Panel Scroll Listeners ─────

function splitScreenInternalSetupPanelScrollListeners() {
    if (splitScreenInternalPanelScrollListenersInitialized) return;
    splitScreenInternalPanelScrollListenersInitialized = true;
    ['left', 'right'].forEach(side => {
        const area = side === 'left' ? splitScreenInternalGetLeftArea() : splitScreenInternalGetRightArea();
        const container = side === 'left' ? splitScreenInternalGetLeftContainer() : splitScreenInternalGetRightContainer();
        const btnId = side === 'left' ? 'splitScrollToBottomBtnLeft' : 'splitScrollToBottomBtnRight';

        if (!area) return;
        if (container && window.ChatScrollCoordinator) {
            window.ChatScrollCoordinator.bindViewport(area, container);
        }

        area.addEventListener('scroll', () => {
            const btn = document.getElementById(btnId);
            if (!btn) return;
            const distanceFromBottom = area.scrollHeight - area.scrollTop - area.clientHeight;
            btn.classList.toggle('visible', distanceFromBottom > 200);
        }, { passive: true });

        const btn = document.getElementById(btnId);
        if (btn) {
            btn.addEventListener('click', () => {
                splitScreenInternalScrollSplitAreaToBottom(area, { behavior: 'smooth' });
            });
        }
    });
}
