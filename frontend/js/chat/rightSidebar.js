(function () {
    const VIS_VISIBLE_CLASS = 'right-sidebar--visible';
    const VIS_OPEN_CLASS = 'open';
    const TRANSITION_DURATION = 280;
    const TRANSITION_BUFFER = 50;

    const modelSettingsSidebar = document.getElementById('modelSettingsSidebar');
    const modelSettingsSidebarClose = document.getElementById('modelSettingsSidebarClose');
    const modelSettingsSidebarOpen = document.getElementById('openModelSettingsButton');
    const modelSettingsSidebarBackdrop = document.getElementById('modelSettingsSidebarBackdrop');
    const mobileSidebarMedia = window.matchMedia('(max-width: 768px)');

    const modelSettingsSidebarState = {
        transitionEndHandler: null,
        closingTimeoutId: null,
        returnFocusTarget: null,
    };

    const syncBodyRightSidebarState =
        window.syncBodyRightSidebarState ||
        function syncBodyRightSidebarStateImpl() {
            if (typeof document === 'undefined') return;
            const hasOpenSidebar = document.querySelector('.right-sidebar.right-sidebar--visible.open');
            document.body.classList.toggle('right-sidebar-active', Boolean(hasOpenSidebar));
        };

    window.syncBodyRightSidebarState = syncBodyRightSidebarState;

    function clearModelSettingsSidebarCloseState() {
        if (!modelSettingsSidebar) return;
        if (modelSettingsSidebarState.transitionEndHandler) {
            modelSettingsSidebar.removeEventListener('transitionend', modelSettingsSidebarState.transitionEndHandler);
            modelSettingsSidebarState.transitionEndHandler = null;
        }
        if (modelSettingsSidebarState.closingTimeoutId) {
            clearTimeout(modelSettingsSidebarState.closingTimeoutId);
            modelSettingsSidebarState.closingTimeoutId = null;
        }
    }

    function isMobileSidebarMode() {
        return mobileSidebarMedia.matches;
    }

    function updateModelSettingsBackdropVisibility() {
        if (!modelSettingsSidebarBackdrop) return;
        const shouldShow = Boolean(modelSettingsSidebar?.classList.contains(VIS_OPEN_CLASS)) && isMobileSidebarMode();
        modelSettingsSidebarBackdrop.classList.toggle('is-visible', shouldShow);
        modelSettingsSidebarBackdrop.setAttribute('aria-hidden', shouldShow ? 'false' : 'true');
    }

    function resolveModelSettingsReturnFocusTarget(invoker) {
        if (!invoker || invoker === document.body || invoker === modelSettingsSidebar) {
            return modelSettingsSidebarOpen || document.getElementById('headerDotsButton');
        }
        if (modelSettingsSidebar.contains?.(invoker)) {
            return modelSettingsSidebarState.returnFocusTarget
                || document.getElementById('headerDotsButton')
                || modelSettingsSidebarOpen;
        }

        // Both model-settings launchers live in menus that close as the sidebar
        // opens. Returning focus to a now-hidden menu item would recreate the
        // same accessibility-tree mismatch, so return to its menu trigger.
        if (invoker === modelSettingsSidebarOpen) {
            return document.getElementById('headerDotsButton') || invoker;
        }
        const splitPanelActions = invoker.closest?.('[data-split-panel-actions]');
        if (splitPanelActions) {
            return splitPanelActions.querySelector('[aria-haspopup="menu"][aria-controls]') || invoker;
        }
        return invoker;
    }

    function isAvailableFocusTarget(target) {
        if (!target?.isConnected || target.disabled || target.hidden) return false;
        return !target.closest?.('[hidden], [inert], [aria-hidden="true"]');
    }

    function restoreModelSettingsSidebarFocus() {
        const preferredTarget = modelSettingsSidebarState.returnFocusTarget;
        const fallbackTarget = document.getElementById('headerDotsButton') || modelSettingsSidebarOpen;
        const target = isAvailableFocusTarget(preferredTarget)
            ? preferredTarget
            : (isAvailableFocusTarget(fallbackTarget) ? fallbackTarget : null);
        modelSettingsSidebarState.returnFocusTarget = null;
        target?.focus({ preventScroll: true });
    }

    function removeModelSettingsSidebarVisibility() {
        if (!modelSettingsSidebar) return;
        clearModelSettingsSidebarCloseState();
        modelSettingsSidebar.classList.remove(VIS_VISIBLE_CLASS);
        syncBodyRightSidebarState();
        updateModelSettingsBackdropVisibility();
    }

    function openModelSettingsSidebar(options = {}) {
        if (!modelSettingsSidebar) return;
        const invoker = options?.invoker || document.activeElement;
        modelSettingsSidebarState.returnFocusTarget = resolveModelSettingsReturnFocusTarget(invoker);
        clearModelSettingsSidebarCloseState();
        modelSettingsSidebar.removeAttribute('inert');
        modelSettingsSidebar.classList.add(VIS_VISIBLE_CLASS);
        modelSettingsSidebar.setAttribute('aria-hidden', 'false');
        modelSettingsSidebarClose?.focus({ preventScroll: true });
        requestAnimationFrame(() => {
            modelSettingsSidebar.classList.add(VIS_OPEN_CLASS);
            syncBodyRightSidebarState();
            updateModelSettingsBackdropVisibility();
        });
    }

    function closeModelSettingsSidebar() {
        if (!modelSettingsSidebar) return;
        const wasOpen = modelSettingsSidebar.classList.contains(VIS_VISIBLE_CLASS)
            || modelSettingsSidebar.classList.contains(VIS_OPEN_CLASS);
        if (wasOpen) {
            restoreModelSettingsSidebarFocus();
        }
        modelSettingsSidebar.setAttribute('inert', '');
        modelSettingsSidebar.setAttribute('aria-hidden', 'true');

        if (!wasOpen) {
            return;
        }

        clearModelSettingsSidebarCloseState();

        modelSettingsSidebarState.transitionEndHandler = (event) => {
            if (event.target !== modelSettingsSidebar) return;
            if (event.propertyName !== 'width') return;
            if (modelSettingsSidebar.classList.contains(VIS_OPEN_CLASS)) return;
            removeModelSettingsSidebarVisibility();
        };

        modelSettingsSidebar.addEventListener('transitionend', modelSettingsSidebarState.transitionEndHandler);

        modelSettingsSidebarState.closingTimeoutId = window.setTimeout(() => {
            if (!modelSettingsSidebar.classList.contains(VIS_OPEN_CLASS)) {
                removeModelSettingsSidebarVisibility();
            }
        }, TRANSITION_DURATION + TRANSITION_BUFFER);

        requestAnimationFrame(() => {
            modelSettingsSidebar.classList.remove(VIS_OPEN_CLASS);
            syncBodyRightSidebarState();
            updateModelSettingsBackdropVisibility();
        });
    }

    function toggleModelSettingsSidebar() {
        if (!modelSettingsSidebar) return;
        if (modelSettingsSidebar.classList.contains(VIS_OPEN_CLASS)) {
            closeModelSettingsSidebar();
        } else {
            openModelSettingsSidebar();
        }
    }

    if (modelSettingsSidebar) {
        modelSettingsSidebar.setAttribute('inert', '');
        modelSettingsSidebar.setAttribute('aria-hidden', 'true');
    }

    if (modelSettingsSidebarOpen) {
        modelSettingsSidebarOpen.addEventListener('click', toggleModelSettingsSidebar);
    }

    if (modelSettingsSidebarClose) {
        modelSettingsSidebarClose.addEventListener('click', closeModelSettingsSidebar);
    }

    if (modelSettingsSidebarBackdrop) {
        modelSettingsSidebarBackdrop.addEventListener('click', closeModelSettingsSidebar);
    }

    if (mobileSidebarMedia?.addEventListener) {
        mobileSidebarMedia.addEventListener('change', () => {
            updateModelSettingsBackdropVisibility();
        });
    }

    // Close sidebar on Escape key
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && modelSettingsSidebar?.classList.contains(VIS_OPEN_CLASS)) {
            closeModelSettingsSidebar();
        }
    });

    // Prevent body scroll when sidebar is open on mobile
    function updateBodyScrollLock() {
        const isOpen = modelSettingsSidebar?.classList.contains(VIS_OPEN_CLASS);
        const isMobile = isMobileSidebarMode();
        document.body.style.overflow = (isOpen && isMobile) ? 'hidden' : '';
    }

    const originalOpen = openModelSettingsSidebar;
    const originalClose = closeModelSettingsSidebar;

    function openModelSettingsSidebarWithLock(options = {}) {
        originalOpen(options);
        updateBodyScrollLock();
    }

    function closeModelSettingsSidebarWithLock() {
        originalClose();
        updateBodyScrollLock();
    }

    // Re-bind event listeners with scroll lock
    if (modelSettingsSidebarOpen) {
        modelSettingsSidebarOpen.removeEventListener('click', toggleModelSettingsSidebar);
        modelSettingsSidebarOpen.addEventListener('click', () => {
            if (modelSettingsSidebar?.classList.contains(VIS_OPEN_CLASS)) {
                closeModelSettingsSidebarWithLock();
            } else {
                openModelSettingsSidebarWithLock();
            }
        });
    }

    if (modelSettingsSidebarClose) {
        modelSettingsSidebarClose.removeEventListener('click', closeModelSettingsSidebar);
        modelSettingsSidebarClose.addEventListener('click', closeModelSettingsSidebarWithLock);
    }

    if (modelSettingsSidebarBackdrop) {
        modelSettingsSidebarBackdrop.removeEventListener('click', closeModelSettingsSidebar);
        modelSettingsSidebarBackdrop.addEventListener('click', closeModelSettingsSidebarWithLock);
    }

    window.closeModelSettingsSidebar = closeModelSettingsSidebarWithLock;
    window.openModelSettingsSidebar = openModelSettingsSidebarWithLock;
})();
