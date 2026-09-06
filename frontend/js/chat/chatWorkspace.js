/* Shared preview surface. Views own their content and opt into subagent tabs;
 * this controller owns layout, resizing and focus. Hidden views keep their state. */
(function () {
    'use strict';
    const entries = new Map();
    let activeId = null;
    let visible = false;
    let dismissed = false;
    let returnFocus = null;
    let sequence = 0;
    let compact = false;
    const backgroundInert = new Map();
    const narrow = window.matchMedia('(max-width: 900px)');
    const t = (key, fallback) => window.getTranslation?.(key, fallback) || fallback;
    const previewPanel = document.createElement('aside');
    previewPanel.id = 'chat-workspace-panel';
    previewPanel.className = 'chat-workspace-panel canvas-markdown-preview-panel';
    previewPanel.tabIndex = -1;
    previewPanel.inert = true;
    previewPanel.setAttribute('aria-hidden', 'true');
    const previewResizer = document.createElement('div');
    previewResizer.id = 'canvas-markdown-PreviewResizer';
    previewResizer.className = 'canvas-markdown-preview-resizer';
    previewResizer.tabIndex = 0;
    previewResizer.setAttribute('role', 'separator');
    previewResizer.setAttribute('aria-orientation', 'vertical');
    previewResizer.setAttribute('aria-controls', previewPanel.id);
    const header = document.createElement('div');
    header.className = 'chat-workspace-header';
    const tabs = document.createElement('div');
    tabs.className = 'chat-workspace-tabs';
    tabs.setAttribute('role', 'tablist');
    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'om-button chat-workspace-close';
    const closeIcon = document.createElement('span');
    closeIcon.setAttribute('aria-hidden', 'true');
    closeIcon.innerHTML = Icons.close;
    const backLabel = document.createElement('span');
    backLabel.className = 'chat-workspace-back-label';
    closeButton.append(closeIcon, backLabel);
    header.append(tabs, closeButton);
    previewPanel.append(previewResizer, header);
    document.body.appendChild(previewPanel);

    function updateLabels() {
        previewPanel.setAttribute('aria-label', t('chat_workspace_title', 'Chat details'));
        tabs.setAttribute('aria-label', t('chat_workspace_tabs', 'Open views'));
        previewResizer.setAttribute('aria-label', t('chat_workspace_resize', 'Resize details panel'));
        const label = narrow.matches
            ? t('chat_workspace_back', 'Back to chat')
            : t('chat_workspace_close', 'Hide details panel');
        closeButton.setAttribute('aria-label', label);
        closeButton.title = label;
        backLabel.textContent = t('chat_workspace_back', 'Back to chat');
        entries.forEach(updateTab);
    }

    function updateTab(entry) {
        entry.tab.hidden = !entry.available || !entry.tabbed;
        const selected = visible && entry.id === activeId;
        entry.tab.setAttribute('aria-selected', String(selected));
        entry.tab.tabIndex = selected ? 0 : -1;
        const label = entry.label();
        if (!entry.tabbed && entry.element) entry.element.setAttribute('aria-label', label);
        const status = entry.status?.() || '';
        if (entry.name.textContent !== label) entry.name.textContent = label;
        if (entry.badge.textContent !== status) entry.badge.textContent = status;
        entry.tab.title = status ? `${label} — ${status}` : label;
    }

    function register(options) {
        if (entries.has(options.id)) return;
        const tab = document.createElement('button');
        tab.type = 'button';
        tab.className = 'om-button chat-workspace-tab';
        tab.id = `chat-workspace-tab-${++sequence}`;
        tab.setAttribute('role', 'tab');
        const name = document.createElement('span');
        name.className = 'chat-workspace-tab-name';
        const badge = document.createElement('span');
        badge.className = 'chat-workspace-tab-status';
        tab.append(name, badge);
        const entry = { available: true, tabbed: false, ...options, tab, name, badge };
        entries.set(entry.id, entry);
        tab.addEventListener('click', () => show(entry.id, { focus: true }));
        if (entry.tabbed) tabs.appendChild(tab);
        if (entry.element) mount(entry);
        updateTab(entry);
    }

    function mount(entry) {
        entry.element ||= entry.create();
        entry.element.classList.add('chat-workspace-content');
        entry.element.id ||= `${entry.tab.id}-view`;
        if (entry.tabbed) {
            entry.element.setAttribute('role', 'tabpanel');
            entry.element.setAttribute('aria-labelledby', entry.tab.id);
            entry.tab.setAttribute('aria-controls', entry.element.id);
        } else {
            entry.element.setAttribute('role', 'region');
            entry.element.setAttribute('aria-label', entry.label());
            entry.element.tabIndex = -1;
        }
        entry.element.hidden = true;
        entry.element.inert = true;
        previewPanel.appendChild(entry.element);
    }

    function syncLayout() {
        header.hidden = !visible || !entries.get(activeId)?.tabbed;
        document.body.classList.toggle('canvas-markdown-preview-open', visible);
        document.body.classList.toggle('chat-workspace-open', visible);
        previewPanel.classList.toggle('visible', visible);
        previewPanel.inert = !visible;
        previewPanel.setAttribute('aria-hidden', String(!visible));
        previewPanel.setAttribute('role', narrow.matches ? 'dialog' : 'complementary');
        if (narrow.matches) previewPanel.setAttribute('aria-modal', 'true');
        else previewPanel.removeAttribute('aria-modal');
        document.body.classList.toggle('chat-workspace-mobile-open', visible && narrow.matches);
        // Only page surfaces are inert: separate dialogs (e.g. Canvas sharing)
        // must remain operable above this full-screen view.
        if (visible && narrow.matches) {
            document.querySelectorAll('.main-container, .sidebar-container, .chat-share-content, .chat-share-header').forEach((element) => {
                if (!backgroundInert.has(element)) backgroundInert.set(element, element.inert);
                element.inert = true;
            });
        } else {
            backgroundInert.forEach((inert, element) => { element.inert = inert; });
            backgroundInert.clear();
        }
        window.setMainSidebarAutoCollapsed?.('canvas-preview', visible);
        if (compact !== visible) {
            compact = visible;
            document.body.classList.toggle('canvas-markdown-compact-main-layout', compact);
            window.setMainSidebarCompactLayout?.('canvas-markdown-preview', compact);
            document.dispatchEvent(new CustomEvent('canvasMarkdownCompactLayoutChange', { detail: { active: compact } }));
        }
    }

    function deactivate() {
        const entry = entries.get(activeId);
        if (!entry?.element) return;
        entry.onHide?.();
        entry.element.hidden = true;
        entry.element.inert = true;
        entry.element.setAttribute('aria-hidden', 'true');
    }

    function show(id, { focus = false, automatic = false, trigger = null } = {}) {
        const entry = entries.get(id);
        if (!entry) return;
        entry.available = true;
        updateTab(entry);
        if (automatic && (dismissed || (visible && activeId !== id))) return;
        if (visible && activeId === id) return;
        if (trigger || (!visible && !previewPanel.contains(document.activeElement))) {
            returnFocus = trigger || document.activeElement;
        }
        if (visible) deactivate();
        if (!entry.element) mount(entry);
        activeId = id;
        visible = true;
        dismissed = false;
        entry.element.hidden = false;
        entry.element.inert = false;
        entry.element.setAttribute('aria-hidden', 'false');
        applyPreviewWidthRatio();
        syncLayout();
        window.closeOtherArtifactPreviews?.('canvas-preview');
        entry.onShow?.();
        entries.forEach(updateTab);
        if (focus || narrow.matches) focusActiveView();
        if (entry.tabbed) entry.tab.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
        document.dispatchEvent(new CustomEvent('chatWorkspace:changed'));
    }

    function focusActiveView() {
        const entry = entries.get(activeId);
        (entry?.tabbed ? entry.tab : entry?.element)?.focus({ preventScroll: true });
    }

    function close({ restoreFocus = true, user = true } = {}) {
        if (!visible) return;
        if (user) dismissed = true;
        deactivate();
        visible = false;
        endPreviewResize();
        syncLayout();
        entries.forEach(updateTab);
        if (restoreFocus && returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
        document.dispatchEvent(new CustomEvent('chatWorkspace:changed'));
    }

    function remove(id) {
        const entry = entries.get(id);
        if (!entry) return;
        if (activeId === id) {
            close({ restoreFocus: false, user: false });
            activeId = null;
        }
        entry.tab.remove();
        entry.element?.remove();
        entries.delete(id);
        entry.onRemove?.();
        if (![...entries.values()].some((item) => item.available)) dismissed = false;
    }

    function setAvailable(id, available) {
        const entry = entries.get(id);
        if (!entry) return;
        if (!available && activeId === id) close({ restoreFocus: false, user: false });
        entry.available = available;
        updateTab(entry);
        if (![...entries.values()].some((item) => item.available)) dismissed = false;
    }

    tabs.addEventListener('keydown', (event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        const available = [...entries.values()].filter((entry) => entry.available && entry.tabbed);
        const index = available.findIndex((entry) => entry.tab === event.target);
        if (index < 0) return;
        const direction = document.documentElement.dir === 'rtl' ? -1 : 1;
        const step = (event.key === 'ArrowRight' ? 1 : -1) * direction;
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? available.length - 1
            : (index + step + available.length) % available.length;
        event.preventDefault();
        show(available[next].id, { focus: true });
    });
    previewPanel.addEventListener('keydown', (event) => {
        if (event.defaultPrevented) return;
        if (event.key === 'Escape' && activeId !== 'canvas') {
            event.preventDefault();
            event.stopPropagation();
            close();
        }
        if (event.key !== 'Tab' || !narrow.matches) return;
        const focusable = [...previewPanel.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
            .filter((element) => !element.closest('[inert], [hidden]') && element.getClientRects().length);
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === previewPanel)) {
            event.preventDefault(); last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault(); first?.focus();
        }
    });
    window.registerEscapeHandler?.({
        id: 'subagent-preview', priority: 70,
        isActive: () => visible && activeId !== 'canvas',
        close: () => close(),
    });
    closeButton.addEventListener('click', () => close());
    narrow.addEventListener('change', () => {
        syncLayout(); updateLabels();
        if (visible && narrow.matches && !previewPanel.contains(document.activeElement)) focusActiveView();
    });
    document.addEventListener('i18n:updated', updateLabels);
    const PREVIEW_WIDTH_STORAGE_KEY = 'omlorix.canvasMarkdownPreviewWidthRatio';
    const PREVIEW_DEFAULT_WIDTH_RATIO = 0.5;
    const PREVIEW_MIN_PANEL_WIDTH = 420;
    const PREVIEW_MIN_MAIN_WIDTH = 360;
    const PREVIEW_RESIZE_KEYBOARD_STEP = 32;
    const PREVIEW_RESIZE_KEYBOARD_LARGE_STEP = 96;

    let canvasPreviewWidthRatio = readStoredPreviewWidthRatio();
    let previewResizeActive = false;
    function readStoredPreviewWidthRatio() {
        try {
            const stored = Number(window.localStorage?.getItem(PREVIEW_WIDTH_STORAGE_KEY));
            return Number.isFinite(stored) && stored > 0 ? stored : PREVIEW_DEFAULT_WIDTH_RATIO;
        } catch (_) {
            return PREVIEW_DEFAULT_WIDTH_RATIO;
        }
    }

    function writeStoredPreviewWidthRatio(ratio) {
        try {
            window.localStorage?.setItem(PREVIEW_WIDTH_STORAGE_KEY, String(ratio));
        } catch (_) {}
    }

    // Store preview width as a viewport ratio so the split survives window resizes
    // while still respecting the minimum sizes required by both panes.
    function getViewportWidth() {
        return window.innerWidth || document.documentElement.clientWidth || 0;
    }

    function isDesktopPreviewLayout() {
        return getViewportWidth() > 900;
    }

    function getPreviewWidthBounds() {
        const viewportWidth = Math.max(getViewportWidth(), 1);
        const minWidth = Math.min(PREVIEW_MIN_PANEL_WIDTH, Math.max(1, viewportWidth - PREVIEW_MIN_MAIN_WIDTH));
        const maxWidth = Math.max(minWidth, viewportWidth - PREVIEW_MIN_MAIN_WIDTH);
        return { viewportWidth, minWidth, maxWidth };
    }

    function clampPreviewWidth(width) {
        const { minWidth, maxWidth } = getPreviewWidthBounds();
        return Math.min(Math.max(Number(width) || 0, minWidth), maxWidth);
    }

    function updatePreviewResizerA11y(widthRatio) {
        if (!previewResizer) return;
        const { viewportWidth, minWidth, maxWidth } = getPreviewWidthBounds();
        const minPercent = Math.round((minWidth / viewportWidth) * 100);
        const maxPercent = Math.round((maxWidth / viewportWidth) * 100);
        const currentPercent = Math.round(widthRatio * 100);
        previewResizer.setAttribute('aria-valuemin', String(minPercent));
        previewResizer.setAttribute('aria-valuemax', String(maxPercent));
        previewResizer.setAttribute('aria-valuenow', String(currentPercent));
    }

    function setPreviewWidthFromPixels(width, { persist = false } = {}) {
        const { viewportWidth } = getPreviewWidthBounds();
        const clampedWidth = clampPreviewWidth(width);
        const nextRatio = clampedWidth / viewportWidth;
        const widthValue = `${(nextRatio * 100).toFixed(3)}vw`;
        canvasPreviewWidthRatio = nextRatio;
        document.documentElement.style.setProperty('--canvas-markdown-preview-width', widthValue);
        previewPanel?.style.setProperty('--canvas-markdown-preview-width', widthValue);
        updatePreviewResizerA11y(nextRatio);
        if (persist) writeStoredPreviewWidthRatio(nextRatio);
        return clampedWidth;
    }

    function applyPreviewWidthRatio(ratio = canvasPreviewWidthRatio) {
        if (!isDesktopPreviewLayout()) {
            updatePreviewResizerA11y(canvasPreviewWidthRatio);
            return;
        }
        const { viewportWidth } = getPreviewWidthBounds();
        setPreviewWidthFromPixels(viewportWidth * (Number(ratio) || PREVIEW_DEFAULT_WIDTH_RATIO));
    }

    function resetPreviewWidth({ persist = true } = {}) {
        const { viewportWidth } = getPreviewWidthBounds();
        setPreviewWidthFromPixels(viewportWidth * PREVIEW_DEFAULT_WIDTH_RATIO, { persist });
    }

    function setPreviewWidthFromPointerX(clientX, options = {}) {
        const { viewportWidth } = getPreviewWidthBounds();
        return setPreviewWidthFromPixels(viewportWidth - Number(clientX || 0), options);
    }

    function beginPreviewResize(event) {
        if (!previewResizer || !isDesktopPreviewLayout()) return;
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        event.preventDefault();
        previewResizeActive = true;
        document.body.classList.add('canvas-markdown-preview-resizing');
        previewResizer.setPointerCapture?.(event.pointerId);
        setPreviewWidthFromPointerX(event.clientX);
    }

    function updatePreviewResize(event) {
        if (!previewResizeActive) return;
        event.preventDefault();
        setPreviewWidthFromPointerX(event.clientX);
    }

    function endPreviewResize(event) {
        if (!previewResizeActive) return;
        previewResizeActive = false;
        document.body.classList.remove('canvas-markdown-preview-resizing');
        if (event?.pointerId !== undefined) {
            previewResizer?.releasePointerCapture?.(event.pointerId);
        }
        writeStoredPreviewWidthRatio(canvasPreviewWidthRatio);
    }

    function handlePreviewResizerKeydown(event) {
        if (!isDesktopPreviewLayout()) return;
        const { viewportWidth, minWidth, maxWidth } = getPreviewWidthBounds();
        const currentWidth = clampPreviewWidth(viewportWidth * canvasPreviewWidthRatio);
        const step = event.shiftKey ? PREVIEW_RESIZE_KEYBOARD_LARGE_STEP : PREVIEW_RESIZE_KEYBOARD_STEP;
        let nextWidth = null;

        if (event.key === 'ArrowLeft') {
            nextWidth = currentWidth + step;
        } else if (event.key === 'ArrowRight') {
            nextWidth = currentWidth - step;
        } else if (event.key === 'Home') {
            nextWidth = minWidth;
        } else if (event.key === 'End') {
            nextWidth = maxWidth;
        } else if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            resetPreviewWidth();
            return;
        }

        if (nextWidth === null) return;
        event.preventDefault();
        setPreviewWidthFromPixels(nextWidth, { persist: true });
    }

    previewResizer.addEventListener('pointerdown', beginPreviewResize);
    previewResizer.addEventListener('pointermove', updatePreviewResize);
    previewResizer.addEventListener('pointerup', endPreviewResize);
    previewResizer.addEventListener('pointercancel', endPreviewResize);
    previewResizer.addEventListener('dblclick', () => resetPreviewWidth());
    previewResizer.addEventListener('keydown', handlePreviewResizerKeydown);
    window.addEventListener('blur', () => endPreviewResize());
    window.addEventListener('resize', () => applyPreviewWidthRatio(), { passive: true });
    window.ChatWorkspace = {
        register, show, close, remove, setAvailable, syncLayout,
        update: (id) => { const entry = entries.get(id); if (entry) updateTab(entry); },
        isSelected: (id) => visible && activeId === id,
        resize: { applyPreviewWidthRatio, getPreviewWidthBounds, resetPreviewWidth,
            setPreviewWidthFromPixels, setPreviewWidthFromPointerX,
            getPreviewWidthRatio: () => canvasPreviewWidthRatio },
    };
    updateLabels();
    syncLayout();
})();
