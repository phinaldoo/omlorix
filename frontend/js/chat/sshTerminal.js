(() => {
    'use strict';

    const state = {
        socket: null,
        terminal: null,
        fitAddon: null,
        resizeObserver: null,
        scrollInputCleanup: null,
        modelId: null,
        intentionalClose: false,
        resizeFrame: null,
        closeTimer: null,
        closeTransitionHandler: null,
        isClosing: false,
        restoreFocusAfterClose: false,
        returnFocus: null,
        model: null,
        splitSide: null,
        panelHeightCustomized: false,
        isFullscreen: false,
        mainWasInert: false,
        mainAriaHidden: null,
    };

    const PANEL_CLOSE_FALLBACK_MS = 400;
    const el = (id) => document.getElementById(id);
    const t = (key, fallback) => window.getTranslation?.(key, fallback) || fallback;

    /** Validate the explicit terminal capability instead of assuming every ACP model supports SSH. */
    function isTerminalModel(model) {
        const provider = String(model?.provider || model?.provider_type || '').trim().toLowerCase();
        return provider === 'acp' && model?.acp_terminal_available === true;
    }

    function selectedTerminalModel() {
        const model = window.getSelectedModel?.();
        return isTerminalModel(model) ? model : null;
    }

    /**
     * Check whether the model that owns the live terminal is still selected in
     * the surface that launched it. Split panes must not fall back to the hidden
     * global selector, because the two panes can use different ACP models.
     */
    function terminalContextIsAvailable() {
        if (!state.modelId || !isTerminalModel(state.model)) return false;
        if (state.splitSide) {
            const split = window.SplitScreenManager;
            const panelModelId = state.splitSide === 'left' ? split?.leftModelId : split?.rightModelId;
            return split?.active === true && String(panelModelId || '') === state.modelId;
        }
        const model = selectedTerminalModel();
        return !window.SplitScreenManager?.active
            && String(model?.model_id || '') === state.modelId;
    }

    function isChatSurfaceActive() {
        const chatContainer = el('chatContainer');
        if (!chatContainer || chatContainer.hidden) return false;

        /*
         * Omlorix's internal pages share the main header and hide the chat using
         * an inline display declaration. Checking the actual chat surface keeps
         * this feature independent from URL aliases and project-chat routes.
         */
        return chatContainer.style.display !== 'none'
            && getComputedStyle(chatContainer).display !== 'none';
    }

    function setStatus(key, fallback, kind = '') {
        const status = el('acpTerminalStatus');
        if (!status) return;
        status.textContent = t(key, fallback);
        status.classList.toggle('is-connected', kind === 'connected');
        status.classList.toggle('is-error', kind === 'error');
        status.dataset.i18nKey = key;
        status.dataset.i18nFallback = fallback;
    }

    function syncTranslations() {
        const status = el('acpTerminalStatus');
        if (status?.dataset.i18nKey) {
            status.textContent = t(status.dataset.i18nKey, status.dataset.i18nFallback || '');
        }
        if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
            el('acpTerminalTitle').textContent = t('acp_terminal_title', 'Terminal');
        }
        syncFullscreenButton();
    }

    function syncFullscreenButton() {
        const button = el('acpTerminalFullscreenButton');
        const icon = el('acpTerminalFullscreenIcon');
        if (!button || !icon) return;
        const translationKey = state.isFullscreen
            ? 'acp_terminal_restore'
            : 'acp_terminal_fullscreen';
        const label = state.isFullscreen
            ? t(translationKey, 'Restore terminal size')
            : t(translationKey, 'Maximize terminal');

        /*
         * Both the label and icon describe the action that the next click will
         * perform. Updating data-i18n-attr keeps later language changes aligned
         * with the terminal's current display mode.
         */
        button.setAttribute('aria-label', label);
        button.setAttribute('title', label);
        button.setAttribute('aria-pressed', String(state.isFullscreen));
        button.setAttribute(
            'data-i18n-attr',
            `aria-label:${translationKey};title:${translationKey}`,
        );
        icon.innerHTML = state.isFullscreen
            ? (window.Icons?.contract || '')
            : (window.Icons?.expand || '');
    }

    function setTerminalFullscreen(fullscreen, { restoreFocus = false } = {}) {
        const panel = el('acpTerminalPanel');
        const container = el('chatContainer');
        const main = el('chatContainerMain');
        const handle = el('acpTerminalResizeHandle');
        const nextFullscreen = Boolean(fullscreen);
        if (!panel || !container || !main) return;
        if (nextFullscreen && !panel.classList.contains('is-open')) return;

        if (state.isFullscreen !== nextFullscreen) {
            if (nextFullscreen) {
                state.mainWasInert = main.hasAttribute('inert');
                state.mainAriaHidden = main.getAttribute('aria-hidden');
                main.setAttribute('inert', '');
                main.setAttribute('aria-hidden', 'true');
            } else {
                if (state.mainWasInert) main.setAttribute('inert', '');
                else main.removeAttribute('inert');
                if (state.mainAriaHidden === null) main.removeAttribute('aria-hidden');
                else main.setAttribute('aria-hidden', state.mainAriaHidden);
            }

            state.isFullscreen = nextFullscreen;
            container.classList.toggle('acp-terminal-fullscreen', nextFullscreen);
            if (handle) {
                handle.tabIndex = nextFullscreen ? -1 : 0;
                handle.setAttribute('aria-hidden', String(nextFullscreen));
            }
            syncFullscreenButton();
            fitTerminal();
        }

        if (restoreFocus) el('acpTerminalFullscreenButton')?.focus();
    }

    function toggleTerminalFullscreen() {
        setTerminalFullscreen(!state.isFullscreen);
    }

    function syncTerminalTheme() {
        if (!state.terminal) return;
        const styles = getComputedStyle(document.documentElement);
        const color = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
        state.terminal.options.theme = {
            background: color('--background', '#ffffff'),
            foreground: color('--text-color', '#222222'),
            cursor: color('--text-color', '#222222'),
            cursorAccent: color('--background', '#ffffff'),
            selectionBackground: color('--surface-interactive-active', '#d7d7d7'),
            black: color('--text-color', '#222222'),
        };
    }

    function sendSize() {
        if (!state.terminal || !state.socket || state.socket.readyState !== WebSocket.OPEN) return;
        state.socket.send(JSON.stringify({
            type: 'resize',
            cols: state.terminal.cols,
            rows: state.terminal.rows,
        }));
    }

    function fitTerminal() {
        if (!state.fitAddon || !el('acpTerminalPanel')?.classList.contains('is-open')) return;
        cancelAnimationFrame(state.resizeFrame);
        state.resizeFrame = requestAnimationFrame(() => {
            try {
                state.fitAddon.fit();
                sendSize();
            } catch {}
        });
    }

    function disposeSocket() {
        if (!state.socket) return;
        const socket = state.socket;
        state.socket = null;
        if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) {
            socket.close(1000, 'Terminal closed');
        }
    }

    /**
     * Return the rendered height of one terminal row in CSS pixels.
     *
     * xterm exposes scrolling in whole lines, while touch events report their
     * movement in pixels. Reading the rendered screen keeps the conversion
     * correct after fitting, fullscreen changes, browser zoom, and iPad display
     * scaling. The option-based value is only a startup fallback for the brief
     * period before xterm has measured its canvas.
     */
    function terminalRowHeight(terminal) {
        const screenHeight = terminal.element
            ?.querySelector('.xterm-screen')
            ?.getBoundingClientRect().height;
        if (Number.isFinite(screenHeight) && screenHeight > 0 && terminal.rows > 0) {
            return screenHeight / terminal.rows;
        }

        const fontSize = Number(terminal.options?.fontSize) || 13;
        const lineHeight = Number(terminal.options?.lineHeight) || 1;
        return fontSize * lineHeight;
    }

    /**
     * Make xterm's virtual scrollback own vertical gestures inside its host.
     *
     * xterm 6 replaced its native scrolling viewport with a logical VS Code
     * scrollbar. Wheel input is supported by that scrollbar, but touch panning
     * is not translated into terminal lines. On iPadOS, Safari consequently
     * hands the gesture to the page and moves its elastic visual viewport. The
     * bubble-phase wheel listener also catches only wheel events xterm did not
     * consume, which is what happens at the top and bottom of scrollback.
     *
     * @param {HTMLElement} host Terminal mount element that bounds the gesture.
     * @param {Terminal} terminal Open xterm instance to scroll.
     * @returns {Function} Cleanup callback for the terminal lifecycle.
     */
    function installTerminalScrollInput(host, terminal) {
        let activeTouchId = null;
        let lastTouchY = null;
        let pendingPixels = 0;
        let activeRowHeight = null;

        const resetTouch = () => {
            activeTouchId = null;
            lastTouchY = null;
            pendingPixels = 0;
            activeRowHeight = null;
        };

        /*
         * TouchList was not consistently iterable in older Safari releases, so
         * use indexed access instead of Array.from/find for broad iPad support.
         */
        const findActiveTouch = (touches) => {
            for (let index = 0; index < touches.length; index += 1) {
                if (touches[index].identifier === activeTouchId) return touches[index];
            }
            return null;
        };

        const handleTouchStart = (event) => {
            /* A multi-touch gesture is reserved for browser pinch zoom. */
            if (event.touches.length !== 1) {
                resetTouch();
                return;
            }
            activeTouchId = event.touches[0].identifier;
            lastTouchY = event.touches[0].clientY;
            pendingPixels = 0;
            activeRowHeight = terminalRowHeight(terminal);
        };

        const handleTouchMove = (event) => {
            if (activeTouchId === null || activeRowHeight === null || event.touches.length !== 1) {
                resetTouch();
                return;
            }

            const touch = findActiveTouch(event.touches);
            if (!touch || lastTouchY === null) {
                resetTouch();
                return;
            }

            const pixelDelta = lastTouchY - touch.clientY;
            lastTouchY = touch.clientY;

            /*
             * Consume every active one-finger move, including movement at the
             * scrollback boundaries. Boundary consumption is what prevents the
             * Safari page/rubber-band scroll reported on iPad.
             */
            if (event.cancelable) event.preventDefault();
            event.stopPropagation();

            if (!Number.isFinite(pixelDelta) || pixelDelta === 0) return;
            pendingPixels += pixelDelta;
            const lineDelta = Math.trunc(pendingPixels / activeRowHeight);
            if (lineDelta === 0) return;

            terminal.scrollLines(lineDelta);
            pendingPixels -= lineDelta * activeRowHeight;
        };

        const handleTouchEnd = (event) => {
            if (activeTouchId === null || !findActiveTouch(event.touches)) resetTouch();
        };

        const containEscapedWheel = (event) => {
            /*
             * xterm stops propagation whenever its logical scrollbar moves.
             * Therefore a wheel event reaching this parent is padding input or
             * boundary overflow and must be contained instead of scrolling the
             * application page. Keep this listener in the bubble phase so it
             * never prevents xterm from handling ordinary trackpad scrolling.
             */
            if (event.ctrlKey) return;
            if (event.cancelable) event.preventDefault();
            event.stopPropagation();
        };

        host.addEventListener('touchstart', handleTouchStart, { passive: true });
        host.addEventListener('touchmove', handleTouchMove, { passive: false });
        host.addEventListener('touchend', handleTouchEnd, { passive: true });
        host.addEventListener('touchcancel', resetTouch, { passive: true });
        host.addEventListener('wheel', containEscapedWheel, { passive: false });

        return () => {
            resetTouch();
            host.removeEventListener('touchstart', handleTouchStart);
            host.removeEventListener('touchmove', handleTouchMove);
            host.removeEventListener('touchend', handleTouchEnd);
            host.removeEventListener('touchcancel', resetTouch);
            host.removeEventListener('wheel', containEscapedWheel);
        };
    }

    function resetTerminal() {
        disposeSocket();
        state.resizeObserver?.disconnect();
        state.resizeObserver = null;
        state.scrollInputCleanup?.();
        state.scrollInputCleanup = null;
        state.terminal?.dispose();
        state.terminal = null;
        state.fitAddon = null;
        el('acpTerminalHost')?.replaceChildren();
    }

    function showReconnect(show) {
        const button = el('acpTerminalReconnectButton');
        if (button) button.hidden = !show;
    }

    function reducedMotionEnabled() {
        return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
    }

    function cancelPendingClose() {
        if (state.closeTimer !== null) {
            clearTimeout(state.closeTimer);
            state.closeTimer = null;
        }
        if (state.closeTransitionHandler) {
            el('acpTerminalPanel')?.removeEventListener('transitionend', state.closeTransitionHandler);
            state.closeTransitionHandler = null;
        }
    }

    function finishTerminalClose() {
        const restoreFocus = state.restoreFocusAfterClose;
        const returnFocus = state.returnFocus;
        cancelPendingClose();
        state.isClosing = false;
        state.restoreFocusAfterClose = false;
        const panel = el('acpTerminalPanel');
        panel?.classList.remove('is-closing', 'is-resizing');
        setTerminalFullscreen(false);
        resetTerminal();
        state.modelId = null;
        state.model = null;
        state.splitSide = null;
        state.returnFocus = null;
        el('chatContainer')?.classList.remove('acp-terminal-is-open');
        if (restoreFocus) {
            const focusTarget = returnFocus?.isConnected === false
                ? null
                : returnFocus;
            (focusTarget || el('headerAcpTerminalButton'))?.focus?.();
        }
    }

    function closeTerminal({ restoreFocus = true, immediate = false } = {}) {
        if (state.isClosing) {
            state.restoreFocusAfterClose ||= restoreFocus;
            if (immediate) finishTerminalClose();
            return;
        }

        state.intentionalClose = true;
        disposeSocket();
        setTerminalFullscreen(false);
        state.isClosing = true;
        state.restoreFocusAfterClose = restoreFocus;
        const panel = el('acpTerminalPanel');
        panel?.classList.remove('is-open', 'is-resizing');
        panel?.classList.add('is-closing');
        panel?.setAttribute('aria-hidden', 'true');
        const button = el('headerAcpTerminalButton');
        button?.classList.remove('active');
        button?.setAttribute('aria-expanded', 'false');
        button?.setAttribute('aria-pressed', 'false');
        showReconnect(false);

        if (immediate || reducedMotionEnabled()) {
            finishTerminalClose();
            return;
        }

        state.closeTransitionHandler = (event) => {
            if (event.target === panel && event.propertyName === 'height') {
                finishTerminalClose();
            }
        };
        panel?.addEventListener('transitionend', state.closeTransitionHandler);
        state.closeTimer = setTimeout(finishTerminalClose, PANEL_CLOSE_FALLBACK_MS);
    }

    function createTerminal() {
        if (typeof window.Terminal !== 'function' || typeof window.FitAddon?.FitAddon !== 'function') {
            throw new Error('Terminal runtime is unavailable');
        }
        const host = el('acpTerminalHost');
        const styles = getComputedStyle(host);
        const fontFamily = styles.getPropertyValue('--acp-terminal-font-family').trim()
            || 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, Consolas, "Liberation Mono", monospace';
        state.terminal = new window.Terminal({
            cursorBlink: true,
            cursorStyle: 'bar',
            convertEol: false,
            /*
             * Use the exact same dedicated font stack as the xterm DOM. Explicit
             * weights and disabled ligatures keep one shell character aligned to
             * one terminal cell on every supported browser.
             */
            fontFamily,
            fontSize: 13,
            fontWeight: '400',
            fontWeightBold: '600',
            letterSpacing: 0,
            lineHeight: 1.18,
            scrollback: 5000,
            allowProposedApi: false,
        });
        state.fitAddon = new window.FitAddon.FitAddon();
        state.terminal.loadAddon(state.fitAddon);
        syncTerminalTheme();
        state.terminal.open(host);
        state.scrollInputCleanup = installTerminalScrollInput(host, state.terminal);
        state.terminal.onData((data) => {
            if (state.socket?.readyState === WebSocket.OPEN) {
                state.socket.send(JSON.stringify({ type: 'input', data }));
            }
        });
        state.resizeObserver = new ResizeObserver(() => {
            fitTerminal();
        });
        state.resizeObserver.observe(host);
        state.resizeObserver.observe(el('acpTerminalPanel'));
        fitTerminal();
    }

    function websocketUrl(modelId) {
        const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const query = new URLSearchParams({
            model_id: modelId,
            cols: String(state.terminal?.cols || 80),
            rows: String(state.terminal?.rows || 24),
        });
        return `${scheme}//${window.location.host}/api/v1/remote-connections/acp-terminal?${query}`;
    }

    function connectTerminal() {
        const model = state.model;
        if (!terminalContextIsAvailable() || !state.terminal) {
            closeTerminal({ restoreFocus: false });
            return;
        }

        disposeSocket();
        showReconnect(false);
        state.intentionalClose = false;
        setStatus('acp_terminal_connecting', 'Connecting…');
        const socket = new WebSocket(websocketUrl(state.modelId));
        state.socket = socket;
        socket.binaryType = 'arraybuffer';

        socket.addEventListener('open', sendSize);
        socket.addEventListener('message', (event) => {
            if (state.socket !== socket) return;
            if (event.data instanceof ArrayBuffer) {
                state.terminal?.write(new Uint8Array(event.data));
                return;
            }
            let message;
            try {
                message = JSON.parse(event.data);
            } catch (_error) {
                return;
            }
            if (message.type === 'ready') {
                el('acpTerminalTitle').textContent = message.title || model.name || t('acp_terminal_title', 'Terminal');
                setStatus('acp_terminal_connected', 'Connected', 'connected');
                state.terminal?.focus();
                sendSize();
            } else if (message.type === 'exit') {
                setStatus('acp_terminal_session_ended', 'Session ended', 'error');
                showReconnect(true);
            } else if (message.type === 'error') {
                setStatus('acp_terminal_connection_failed', 'Connection failed', 'error');
                showReconnect(true);
            }
        });
        socket.addEventListener('close', () => {
            if (state.socket !== socket) return;
            state.socket = null;
            if (!state.intentionalClose && el('acpTerminalPanel')?.classList.contains('is-open')) {
                const status = el('acpTerminalStatus');
                if (!status?.classList.contains('is-error')) {
                    setStatus('acp_terminal_disconnected', 'Disconnected', 'error');
                }
                showReconnect(true);
            }
        });
        socket.addEventListener('error', () => {
            if (state.socket !== socket) return;
            setStatus('acp_terminal_connection_failed', 'Connection failed', 'error');
            showReconnect(true);
        });
    }

    /**
     * Open the shared terminal panel for either the main selected model or an
     * explicit split-pane model. Only one terminal is visible at a time; asking
    * for the other pane's model switches the live connection in place.
     */
    function openTerminal({ model: requestedModel = null, side = null, returnFocus = null } = {}) {
        const model = requestedModel
            ? (isTerminalModel(requestedModel) ? requestedModel : null)
            : selectedTerminalModel();
        if (!model || !isChatSurfaceActive()) return;
        const panel = el('acpTerminalPanel');
        const modelId = String(model.model_id);
        const normalizedSide = side === 'left' || side === 'right' ? side : null;
        const sameContext = modelId === state.modelId && normalizedSide === state.splitSide;
        if (panel?.classList.contains('is-open') && sameContext) {
            closeTerminal();
            return;
        }

        if (panel?.classList.contains('is-open')) {
            resetTerminal();
        }

        /*
         * Clicking the header button while the close transition is running
         * reverses that transition and reconnects the existing terminal instead
         * of disposing and recreating xterm midway through the animation.
         */
        if (state.isClosing && state.terminal && sameContext) {
            cancelPendingClose();
            state.isClosing = false;
            state.restoreFocusAfterClose = false;
            state.intentionalClose = false;
            panel.classList.remove('is-closing');
            panel.classList.add('is-open');
            panel.setAttribute('aria-hidden', 'false');
            el('chatContainer')?.classList.add('acp-terminal-is-open');
            const button = el('headerAcpTerminalButton');
            button?.classList.add('active');
            button?.setAttribute('aria-expanded', 'true');
            button?.setAttribute('aria-pressed', 'true');
            connectTerminal();
            return;
        }

        if (state.isClosing) {
            finishTerminalClose();
        }
        syncDefaultPanelHeight();
        state.modelId = modelId;
        state.model = { ...model };
        state.splitSide = normalizedSide;
        state.returnFocus = returnFocus || el('headerAcpTerminalButton');
        state.intentionalClose = false;
        panel?.classList.remove('is-closing');
        panel?.classList.add('is-open');
        panel?.setAttribute('aria-hidden', 'false');
        el('chatContainer')?.classList.add('acp-terminal-is-open');
        const button = el('headerAcpTerminalButton');
        button?.classList.add('active');
        button?.setAttribute('aria-expanded', 'true');
        button?.setAttribute('aria-pressed', 'true');
        el('acpTerminalTitle').textContent = model.name || t('acp_terminal_title', 'Terminal');
        try {
            createTerminal();
            connectTerminal();
        } catch (error) {
            console.error('Unable to initialize ACP terminal', error);
            setStatus('acp_terminal_connection_failed', 'Connection failed', 'error');
            showReconnect(true);
        }
    }

    function syncAvailability() {
        const model = selectedTerminalModel();
        const chatSurfaceActive = isChatSurfaceActive();
        const splitActive = window.SplitScreenManager?.active === true;
        const available = Boolean(model && chatSurfaceActive && !splitActive);
        const button = el('headerAcpTerminalButton');
        if (button) button.hidden = !available;
        if (state.modelId && (!chatSurfaceActive || !terminalContextIsAvailable())) {
            closeTerminal({
                restoreFocus: false,
                immediate: !chatSurfaceActive,
            });
        }
    }

    function setPanelHeight(height, { customized = true } = {}) {
        const panel = el('acpTerminalPanel');
        if (!panel) return;
        const maximumRatio = window.innerWidth <= 700 ? 0.75 : 0.7;
        const maximum = Math.max(160, Math.round(window.innerHeight * maximumRatio));
        const bounded = Math.max(160, Math.min(Math.round(height), maximum));
        state.panelHeightCustomized = customized;
        panel.style.setProperty('--acp-terminal-target-height', `${bounded}px`);
        el('acpTerminalResizeHandle')?.setAttribute('aria-valuenow', String(bounded));
        fitTerminal();
    }

    function syncDefaultPanelHeight() {
        if (state.panelHeightCustomized) return;
        const mobile = window.innerWidth <= 700;
        const preferredRatio = mobile ? 0.44 : 0.38;
        const maximumRatio = mobile ? 0.75 : 0.7;
        const preferred = Math.min(360, Math.round(window.innerHeight * preferredRatio));
        const maximum = Math.max(160, Math.round(window.innerHeight * maximumRatio));
        setPanelHeight(Math.min(preferred, maximum), { customized: false });
    }

    function initializeResizeHandle() {
        const handle = el('acpTerminalResizeHandle');
        if (!handle) return;
        handle.addEventListener('pointerdown', (event) => {
            event.preventDefault();
            const panel = el('acpTerminalPanel');
            const startY = event.clientY;
            const startHeight = panel.getBoundingClientRect().height;
            panel.classList.add('is-resizing');
            handle.setPointerCapture(event.pointerId);
            const move = (moveEvent) => setPanelHeight(startHeight + startY - moveEvent.clientY);
            const finish = () => {
                panel.classList.remove('is-resizing');
                handle.removeEventListener('pointermove', move);
                handle.removeEventListener('pointerup', finish);
                handle.removeEventListener('pointercancel', finish);
            };
            handle.addEventListener('pointermove', move);
            handle.addEventListener('pointerup', finish);
            handle.addEventListener('pointercancel', finish);
        });
        handle.addEventListener('keydown', (event) => {
            if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const current = el('acpTerminalPanel').getBoundingClientRect().height;
            if (event.key === 'Home') setPanelHeight(160);
            else if (event.key === 'End') setPanelHeight(window.innerHeight * 0.7);
            else setPanelHeight(current + (event.key === 'ArrowUp' ? 24 : -24));
        });
    }

    function initialize() {
        const button = el('headerAcpTerminalButton');
        if (!button || !el('acpTerminalPanel')) return;
        el('headerAcpTerminalButton').innerHTML = window.Icons?.terminal || '';
        el('acpTerminalTabIcon').innerHTML = window.Icons?.terminal || '';
        el('acpTerminalReconnectIcon').innerHTML = window.Icons?.refresh || '';
        el('acpTerminalCloseIcon').innerHTML = window.Icons?.close || '';
        syncFullscreenButton();
        button.addEventListener('click', () => openTerminal());
        el('acpTerminalCloseButton')?.addEventListener('click', () => closeTerminal());
        el('acpTerminalFullscreenButton')?.addEventListener('click', toggleTerminalFullscreen);
        el('acpTerminalReconnectButton')?.addEventListener('click', () => {
            state.terminal?.clear();
            connectTerminal();
        });
        document.addEventListener('keydown', (event) => {
            if (event.key !== 'Escape' || !state.isFullscreen || state.isClosing) return;
            /*
             * Capture Escape before xterm sends it to the remote shell. This is
             * the conventional, keyboard-accessible way to leave fullscreen.
             */
            event.preventDefault();
            event.stopPropagation();
            setTerminalFullscreen(false, { restoreFocus: true });
        }, true);
        initializeResizeHandle();
        window.addEventListener('modelSelect:changed', syncAvailability);
        window.addEventListener('splitScreen:stateChanged', syncAvailability);
        document.addEventListener('i18n:updated', syncTranslations);
        window.addEventListener('resize', () => {
            syncDefaultPanelHeight();
            fitTerminal();
        });
        window.addEventListener('beforeunload', () => closeTerminal({
            restoreFocus: false,
            immediate: true,
        }));
        new MutationObserver(syncTerminalTheme).observe(document.documentElement, {
            attributes: true,
            attributeFilter: ['data-mode'],
        });
        const surfaceObserver = new MutationObserver(syncAvailability);
        surfaceObserver.observe(el('chatContainer'), {
            attributes: true,
            attributeFilter: ['class', 'hidden', 'style'],
        });
        surfaceObserver.observe(document.body, {
            attributes: true,
            attributeFilter: ['class'],
        });
        syncDefaultPanelHeight();
        syncAvailability();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize, { once: true });
    } else {
        initialize();
    }

    window.AcpSshTerminal = {
        open: openTerminal,
        openForModel(model, options = {}) {
            openTerminal({ ...options, model });
        },
        close: closeTerminal,
        toggleFullscreen: toggleTerminalFullscreen,
        syncAvailability,
    };
})();
