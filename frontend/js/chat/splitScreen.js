// Split Screen Manager
// Orchestrates the split-screen modules loaded immediately before this entry file.

// ───── Initialize ─────

function splitScreenInternalInit() {
    // Cache the primary chat area container for ID swaps
    splitScreenInternalMainChatAreaContainer = document.getElementById('chatAreaContainer');
    splitScreenInternalMountPanelToolbarsInMainHeader();
    splitScreenInternalHydrateSharedSplitIcons();
    splitScreenInternalUpdateSendTargetLabel();

    // Toggle button
    const toggleBtn = splitScreenInternalGetToggleBtn();
    if (toggleBtn) {
        toggleBtn.addEventListener('click', splitScreenInternalToggle);
    }

    // Reuse the shared dropdown controller so this control stays aligned
    // with every other chat composer menu as the common behavior evolves.
    const sendTargetBtn = splitScreenInternalGetSendTargetBtn();
    const sendTargetDd = splitScreenInternalGetSendTargetDd();
    if (sendTargetBtn && sendTargetDd && typeof window.createDropdownController === 'function') {
        splitScreenInternalSendTargetDropdownController = window.createDropdownController({
            id: 'split-send-target-controller',
            group: 'chat-box-composer-dropdowns',
            trigger: sendTargetBtn,
            dropdown: sendTargetDd,
            root: splitScreenInternalEl('splitSendTargetWrapper'),
            escapePriority: 90,
            focusOnOpen: () => sendTargetDd.querySelector('[data-split-send-target][aria-checked="true"]')
                || sendTargetDd.querySelector('[data-split-send-target]'),
            onBeforeOpen: () => {
                splitScreenInternalClosePanelActionMenus();
            },
        });
    }
    splitScreenInternalSyncSendTargetControlVisibility();

    // Send target options
    if (sendTargetDd) {
        sendTargetDd.querySelectorAll('[data-split-send-target]').forEach(opt => {
            opt.addEventListener('click', () => {
                splitScreenInternalSetSendTarget(opt.dataset.splitSendTarget);
                sendTargetBtn?.focus();
            });
            opt.addEventListener('keydown', (event) => {
                const options = Array.from(sendTargetDd.querySelectorAll('[data-split-send-target]'));
                const index = options.indexOf(opt);
                let nextIndex = null;
                if (event.key === 'ArrowUp') nextIndex = (index - 1 + options.length) % options.length;
                if (event.key === 'ArrowDown') nextIndex = (index + 1) % options.length;
                if (event.key === 'Home') nextIndex = 0;
                if (event.key === 'End') nextIndex = options.length - 1;
                if (nextIndex === null) return;
                event.preventDefault();
                options[nextIndex].focus();
            });
        });
    }

    // Panel close buttons
    const leftClose = splitScreenInternalEl('splitLeftClose');
    const rightClose = splitScreenInternalEl('splitRightClose');
    if (leftClose) leftClose.addEventListener('click', () => splitScreenInternalClosePanel('left'));
    if (rightClose) rightClose.addEventListener('click', () => splitScreenInternalClosePanel('right'));
    const leftSave = splitScreenInternalGetLeftSaveBtn();
    const rightSave = splitScreenInternalGetRightSaveBtn();
    if (leftSave) leftSave.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        splitScreenInternalSavePanelTemporaryChat('left');
    });
    if (rightSave) rightSave.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        splitScreenInternalSavePanelTemporaryChat('right');
    });

    // Model select click handlers on panel headers
    const leftModel = splitScreenInternalEl('splitLeftModel');
    const rightModel = splitScreenInternalEl('splitRightModel');
    if (leftModel) leftModel.addEventListener('click', (e) => { e.stopPropagation(); splitScreenInternalTogglePanelModelDropdown('left'); });
    if (rightModel) rightModel.addEventListener('click', (e) => { e.stopPropagation(); splitScreenInternalTogglePanelModelDropdown('right'); });

    // Compact split view keeps both conversations alive and exposes them as
    // an accessible tab pair instead of forcing the user to discard one.
    const compactTabs = splitScreenInternalGetCompactTabs();
    if (compactTabs) {
        const activateCompactTab = (tab, { focus = false } = {}) => {
            if (!tab?.dataset?.compactPanel) return;
            splitScreenInternalSetCompactPanel(tab.dataset.compactPanel, { focusPanel: false });
            if (focus) tab.focus();
        };
        compactTabs.querySelectorAll('[data-compact-panel]').forEach((tab) => {
            tab.addEventListener('click', () => activateCompactTab(tab));
            tab.addEventListener('keydown', (event) => {
                const tabs = Array.from(compactTabs.querySelectorAll('[data-compact-panel]'));
                const index = tabs.indexOf(tab);
                let nextIndex = null;
                if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (index - 1 + tabs.length) % tabs.length;
                if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (index + 1) % tabs.length;
                if (event.key === 'Home') nextIndex = 0;
                if (event.key === 'End') nextIndex = tabs.length - 1;
                if (nextIndex === null) return;
                event.preventDefault();
                activateCompactTab(tabs[nextIndex], { focus: true });
            });
        });
    }

    // Responsive modal buttons
    // Model select listener
    splitScreenInternalSetupModelSelectListener();
    window.addEventListener('modelSelect:changed', () => {
        splitScreenInternalApplyMainSelectedModelToPanels();
    });
    window.addEventListener('modelSettings:stateChanged', (event) => {
        if (!splitScreenInternalState.active) {
            return;
        }
        const side = splitScreenInternalGetActiveSettingsTabSide();
        const eventModelId = event?.detail?.modelId;
        if (String(eventModelId || '') !== String(splitScreenInternalGetPanelModelId(side) || '')) {
            return;
        }
        splitScreenInternalSetPanelSettings(side, event?.detail?.settings || {});
        splitScreenInternalUpdatePanelThinkingState(side);
    });

    // Settings panel tabs
    splitScreenInternalSetupSettingsTabs();
    splitScreenInternalSetupPanelThinkingControls();
    splitScreenInternalSetupPanelActionMenus();

    // Divider resize
    splitScreenInternalInitDividerResize();

    // Sidebar drag and drop
    splitScreenInternalSetupSidebarDragAndDrop();

    if (typeof MutationObserver !== 'undefined') {
        const mutationObserverOptions = { childList: true, subtree: true };
        [splitScreenInternalGetLeftContainer(), splitScreenInternalGetRightContainer()].filter(Boolean).forEach((container) => {
            const observer = new MutationObserver(() => {
                splitScreenInternalUpdatePanelSaveButtons();
            });
            observer.observe(container, mutationObserverOptions);
        });
    }

    // Responsive handler
    let resizeTimeout;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(splitScreenInternalHandleResize, 250);
    });
    if (typeof ResizeObserver !== 'undefined' && splitScreenInternalGetWrapper()) {
        const wrapperResizeObserver = new ResizeObserver(() => {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(splitScreenInternalHandleResize, 80);
        });
        wrapperResizeObserver.observe(splitScreenInternalGetWrapper());
    }
    if (typeof ResizeObserver !== 'undefined') {
        const headerResizeObserver = new ResizeObserver(() => {
            splitScreenInternalScheduleSplitHeaderGutterSync();
        });
        [
            splitScreenInternalGetSplitMainHeader()?.closest('.main-container-header'),
            document.querySelector('.main-header-leading'),
            document.querySelector('.main-header-actions'),
        ].filter(Boolean).forEach((target) => headerResizeObserver.observe(target));
    }

    // Restore from URL on page load
    splitScreenInternalSyncFromURL();
    splitScreenInternalApplyMainSelectedModelToPanels();
    splitScreenInternalUpdatePanelSaveButtons();
}

const SplitScreenManager = (function () {
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', splitScreenInternalInit);
    } else {
        splitScreenInternalInit();
    }

    // ───── Public API ─────
    return {
        get active() { return splitScreenInternalState.active; },
        get sendTarget() { return splitScreenInternalState.sendTarget; },
        get leftChatId() { return splitScreenInternalState.leftChatId; },
        get rightChatId() { return splitScreenInternalState.rightChatId; },
        get leftModelId() { return splitScreenInternalState.leftModelId; },
        get rightModelId() { return splitScreenInternalState.rightModelId; },
        get leftIsGenerating() { return splitScreenInternalState.leftIsGenerating; },
        get rightIsGenerating() { return splitScreenInternalState.rightIsGenerating; },

        enable: splitScreenInternalEnable,
        disable: splitScreenInternalDisable,
        requestDisable: splitScreenInternalRequestDisable,
        toggle: splitScreenInternalToggle,
        send: splitScreenInternalSend,
        sendToPanel: splitScreenInternalSendToPanel,
        loadChatIntoPanel: splitScreenInternalLoadChatIntoPanel,
        openSidebarChatInPanel: splitScreenInternalOpenSidebarChatInPanel,
        closePanel: splitScreenInternalClosePanel,
        setSendTarget: splitScreenInternalSetSendTarget,
        openPanelModelPicker: splitScreenInternalTogglePanelModelDropdown,
        savePanelTemporaryChat: splitScreenInternalSavePanelTemporaryChat,
        isSendTargetGenerating: splitScreenInternalIsTargetGenerating,
        cancelSendTargetGeneration: splitScreenInternalCancelGenerationForTarget,
        syncFromURL: splitScreenInternalSyncFromURL,
        selectMentionModel(model) {
            if (!model?.model_id) return;
            splitScreenInternalResolveTargetSides(splitScreenInternalState.sendTarget).forEach((side) => splitScreenInternalSelectModelForPanel(side, model));
        },
    };
})();

// Expose globally
if (typeof window !== 'undefined') {
    window.SplitScreenManager = SplitScreenManager;
}
function resolveSplitScreenErrorMessage(errorData, fallback) {
    const detail = errorData?.detail;
    if (typeof detail === 'string' && detail.trim()) {
        return detail;
    }
    if (detail && typeof detail === 'object') {
        const code = typeof detail.code === 'string' ? detail.code.trim() : '';
        if (code === 'byok_credential_unavailable') {
            return splitScreenT(
                'byok_credential_unavailable',
                'Your saved BYOK credential is unavailable. Re-enter the API key.',
            );
        }
        const message = typeof detail.message === 'string' ? detail.message.trim() : '';
        if (message) {
            return message;
        }
    }
    return fallback;
}

function isSplitScreenChatReferenceError(errorData) {
    const code = errorData?.detail?.code;
    return code === 'chat_reference_context_too_large' || code === 'chat_reference_invalid';
}
