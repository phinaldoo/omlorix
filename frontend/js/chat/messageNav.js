/**
 * Message Turn Navigation
 * ──────────────────────
 * Vertical rail on the right edge of the chat area with:
 * - Up / Down arrow buttons to cycle between messages
 * - One tick per user or assistant turn
 * - Active tick tracks the currently-visible message
 * - Tooltip on tick hover showing role + text preview
 */

(function () {
    'use strict';

    function messageNavT(key, fallback) {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function messageNavFormatT(key, fallback, vars = {}) {
        if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(messageNavT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars[token];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    /* ── Config ── */
    const SCROLL_AREA_ID = 'chatArea';
    const MESSAGE_CONTAINER_ID = 'chatAreaContainer';
    const PARENT_CONTAINER_ID = 'chatContainerMain';
    const TOOLTIP_DELAY = 180;   // ms before tooltip shows
    const PREVIEW_MAX_LEN = 100; // chars to show in preview
    const DEBOUNCE_REBUILD = 120;
    const DEBOUNCE_SCROLL = 60;
    const SHOW_MESSAGE_NAV_SETTING_KEY = 'show_message_nav';

    /* ── State ── */
    let navEl = null;
    let trackEl = null;
    let tooltipEl = null;
    let arrowUpEl = null;
    let arrowDownEl = null;
    let counterEl = null;
    let ticks = [];            // { el, messageEl, role } per turn
    let activeIndex = -1;
    let tooltipTimer = null;
    let rebuildTimer = null;
    let scrollTimer = null;
    let domObserver = null;
    let intersectionObserver = null;
    let isDestroyed = false;
    let scrollAreaEl = null;
    let onScrollHandler = null;
    let onChatSwitchedHandler = null;
    let onChatLoadedHandler = null;
    let onChatSetupReadyHandler = null;
    let retryIntervalId = null;
    let retryGiveUpTimeoutId = null;
    let isMessageNavEnabled = true;
    let modelPublicNameById = new Map();
    let modelNameLookupPromise = null;
    let hasLoadedModelNameLookup = false;

    /* ── SVGs ── */
    const CHEVRON_UP_SVG = Icons.chevronTop;
    const CHEVRON_DOWN_SVG = Icons.chevron;

    /* ── Bootstrap ── */
    function init() {
        const parent = document.getElementById(PARENT_CONTAINER_ID);
        if (!parent || navEl) return;

        isMessageNavEnabled = readMessageNavPreference();

        // Build the navigation rail
        navEl = document.createElement('nav');
        navEl.className = 'message-nav';
        navEl.id = 'messageNav';

        // Up arrow
        arrowUpEl = document.createElement('button');
        arrowUpEl.type = 'button';
        arrowUpEl.className = 'message-nav-arrow';
        arrowUpEl.innerHTML = CHEVRON_UP_SVG;
        arrowUpEl.addEventListener('click', () => navigate(-1));
        navEl.appendChild(arrowUpEl);

        // Track
        const trackOuterEl = document.createElement('div');
        trackOuterEl.className = 'message-nav-track';
        navEl.appendChild(trackOuterEl);

        trackEl = document.createElement('div');
        trackEl.className = 'message-nav-track-scroll';
        trackOuterEl.appendChild(trackEl);

        // Turn counter
        counterEl = document.createElement('div');
        counterEl.className = 'message-nav-counter';
        navEl.appendChild(counterEl);

        // Down arrow
        arrowDownEl = document.createElement('button');
        arrowDownEl.type = 'button';
        arrowDownEl.className = 'message-nav-arrow';
        arrowDownEl.innerHTML = CHEVRON_DOWN_SVG;
        arrowDownEl.addEventListener('click', () => navigate(1));
        navEl.appendChild(arrowDownEl);

        updateLocalizedLabels();

        // Tooltip (appended to nav so it follows the rail)
        tooltipEl = document.createElement('div');
        tooltipEl.className = 'message-nav-tooltip';
        tooltipEl.setAttribute('role', 'tooltip');
        tooltipEl.innerHTML = '<div class="message-nav-tooltip-role"></div><div class="message-nav-tooltip-preview"></div>';
        document.body.appendChild(tooltipEl);

        parent.appendChild(navEl);
        applyEnabledState();
        primeModelNameLookup();

        // Observe DOM for message changes
        const messageContainer = document.getElementById(MESSAGE_CONTAINER_ID);
        if (messageContainer) {
            domObserver = new MutationObserver(scheduleRebuild);
            domObserver.observe(messageContainer, { childList: true, subtree: false });
        }

        // Scroll tracking
        scrollAreaEl = document.getElementById(SCROLL_AREA_ID);
        if (scrollAreaEl) {
            onScrollHandler = scheduleScrollUpdate;
            scrollAreaEl.addEventListener('scroll', onScrollHandler, { passive: true });
        }

        // Initial build
        rebuildTicks();

        // Listen for chat switches (chat area cleared / repopulated)
        onChatSwitchedHandler = scheduleRebuild;
        onChatLoadedHandler = scheduleRebuild;
        document.addEventListener('chatSwitched', onChatSwitchedHandler);
        document.addEventListener('chatLoaded', onChatLoadedHandler);
        document.addEventListener('i18n:updated', updateLocalizedLabels);
    }

    /* ── Tear-down ── */
    function destroy() {
        isDestroyed = true;
        if (retryIntervalId) {
            clearInterval(retryIntervalId);
            retryIntervalId = null;
        }
        if (retryGiveUpTimeoutId) {
            clearTimeout(retryGiveUpTimeoutId);
            retryGiveUpTimeoutId = null;
        }
        if (scrollAreaEl && onScrollHandler) {
            scrollAreaEl.removeEventListener('scroll', onScrollHandler);
        }
        if (onChatSetupReadyHandler) {
            document.removeEventListener('chatSetupReady', onChatSetupReadyHandler);
        }
        if (onChatSwitchedHandler) {
            document.removeEventListener('chatSwitched', onChatSwitchedHandler);
        }
        if (onChatLoadedHandler) {
            document.removeEventListener('chatLoaded', onChatLoadedHandler);
        }
        document.removeEventListener('i18n:updated', updateLocalizedLabels);
        scrollAreaEl = null;
        onScrollHandler = null;
        onChatSwitchedHandler = null;
        onChatLoadedHandler = null;
        onChatSetupReadyHandler = null;
        if (domObserver) { domObserver.disconnect(); domObserver = null; }
        if (intersectionObserver) { intersectionObserver.disconnect(); intersectionObserver = null; }
        if (navEl && navEl.parentElement) navEl.parentElement.removeChild(navEl);
        if (tooltipEl && tooltipEl.parentElement) tooltipEl.parentElement.removeChild(tooltipEl);
        clearTimeout(rebuildTimer);
        clearTimeout(scrollTimer);
        clearTimeout(tooltipTimer);
        ticks = [];
        navEl = null;
    }

    /* ── Rebuild ticks from current DOM ── */
    function scheduleRebuild() {
        clearTimeout(rebuildTimer);
        rebuildTimer = setTimeout(rebuildTicks, DEBOUNCE_REBUILD);
    }

    function rebuildTicks() {
        if (isDestroyed || !trackEl) return;

        if (!isMessageNavEnabled) {
            hideNavContent();
            return;
        }

        const messageContainer = document.getElementById(MESSAGE_CONTAINER_ID);
        if (!messageContainer) return;

        // Gather all top-level message elements
        const children = Array.from(messageContainer.children);
        const messageTurns = [];

        children.forEach((child) => {
            if (!child || !child.classList) return;
            if (child.classList.contains('user-message-area')) {
                messageTurns.push({ el: child, role: 'user' });
            } else if (child.classList.contains('assistant-message-container')) {
                // Skip hidden / non-visible containers
                if (child.dataset.hidden === 'true' || child.style.display === 'none') return;
                messageTurns.push({ el: child, role: 'assistant' });
            }
        });

        // Quick exit: hide nav if fewer than 2 turns
        if (messageTurns.length < 2) {
            navEl.classList.remove('has-messages');
            trackEl.innerHTML = '';
            ticks = [];
            counterEl.textContent = '';
            return;
        }

        navEl.classList.add('has-messages');

        // Diff against existing ticks to avoid full rerender
        const needsFullRebuild = (
            ticks.length !== messageTurns.length
            || ticks.some((t, i) => t.messageEl !== messageTurns[i].el)
        );

        if (!needsFullRebuild) {
            // Just update counter
            updateCounter();
            return;
        }

        // Full rebuild
        hideTooltip();
        trackEl.innerHTML = '';
        ticks = [];

        // Disconnect old IntersectionObserver
        if (intersectionObserver) {
            intersectionObserver.disconnect();
        }

        // Create IntersectionObserver to track active message
        const scrollArea = document.getElementById(SCROLL_AREA_ID);
        intersectionObserver = new IntersectionObserver(
            handleIntersection,
            {
                root: scrollArea || null,
                rootMargin: '-10% 0px -60% 0px', // bias towards the top of the viewport
                threshold: 0,
            }
        );

        messageTurns.forEach((turn, index) => {
            const tick = document.createElement('div');
            tick.className = 'message-nav-tick';
            tick.dataset.role = turn.role;
            tick.dataset.index = String(index);
            tick.setAttribute('role', 'button');
            tick.setAttribute('tabindex', '0');
            updateTickLabel(tick, turn.role, index);

            tick.addEventListener('click', () => scrollToTurn(index));
            tick.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    scrollToTurn(index);
                }
            });
            tick.addEventListener('mouseenter', (e) => showTooltipDelayed(index, e));
            tick.addEventListener('mouseleave', hideTooltip);

            trackEl.appendChild(tick);

            const entry = { el: tick, messageEl: turn.el, role: turn.role };
            ticks.push(entry);

            // Observe
            intersectionObserver.observe(turn.el);
        });

        updateCounter();
        updateActiveFromScroll();
    }

    function hideNavContent() {
        if (!navEl || !trackEl || !counterEl) return;
        if (intersectionObserver) {
            intersectionObserver.disconnect();
            intersectionObserver = null;
        }
        navEl.classList.remove('has-messages');
        trackEl.innerHTML = '';
        ticks = [];
        activeIndex = -1;
        counterEl.textContent = '';
        hideTooltip();
        updateArrowStates();
    }

    /* ── IntersectionObserver handler ── */
    function handleIntersection(entries) {
        // Find the topmost visible message
        let topMostIndex = -1;
        let topMostTop = Infinity;

        entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            const idx = ticks.findIndex((t) => t.messageEl === entry.target);
            if (idx === -1) return;

            const rect = entry.boundingClientRect;
            if (rect.top < topMostTop) {
                topMostTop = rect.top;
                topMostIndex = idx;
            }
        });

        if (topMostIndex !== -1) {
            setActiveTick(topMostIndex);
        }
    }

    /* ── Scroll-based fallback for active tracking ── */
    function scheduleScrollUpdate() {
        clearTimeout(scrollTimer);
        scrollTimer = setTimeout(updateActiveFromScroll, DEBOUNCE_SCROLL);
    }

    function updateActiveFromScroll() {
        if (!ticks.length) return;

        const scrollArea = document.getElementById(SCROLL_AREA_ID);
        if (!scrollArea) return;

        const areaRect = scrollArea.getBoundingClientRect();
        const midY = areaRect.top + areaRect.height * 0.35; // bias to upper part

        let closest = -1;
        let closestDist = Infinity;

        ticks.forEach((t, i) => {
            const rect = t.messageEl.getBoundingClientRect();
            const dist = Math.abs(rect.top - midY);
            if (dist < closestDist) {
                closestDist = dist;
                closest = i;
            }
        });

        if (closest !== -1) {
            setActiveTick(closest);
        }
    }

    /* ── Active tick management ── */
    function setActiveTick(index) {
        if (index === activeIndex) return;
        if (activeIndex >= 0 && activeIndex < ticks.length) {
            ticks[activeIndex].el.classList.remove('is-active');
        }
        activeIndex = index;
        if (activeIndex >= 0 && activeIndex < ticks.length) {
            ticks[activeIndex].el.classList.add('is-active');
            // Scroll tick into view within the track
            const tick = ticks[activeIndex].el;
            tick.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
        updateArrowStates();
        updateCounter();
    }

    /* ── Arrow states ── */
    function updateArrowStates() {
        if (!arrowUpEl || !arrowDownEl) return;
        if (!isMessageNavEnabled || !ticks.length) {
            arrowUpEl.disabled = true;
            arrowDownEl.disabled = true;
            return;
        }
        arrowUpEl.disabled = activeIndex <= 0;
        arrowDownEl.disabled = activeIndex >= ticks.length - 1;
    }

    function readMessageNavPreference() {
        if (typeof window !== 'undefined' && typeof window.getChatBooleanSetting === 'function') {
            return window.getChatBooleanSetting(SHOW_MESSAGE_NAV_SETTING_KEY, true);
        }
        try {
            const stored = localStorage.getItem(SHOW_MESSAGE_NAV_SETTING_KEY);
            if (stored === 'true' || stored === '1') return true;
            if (stored === 'false' || stored === '0') return false;
        } catch (_) {}
        return true;
    }

    function applyEnabledState() {
        if (!navEl) return;
        navEl.classList.toggle('is-disabled', !isMessageNavEnabled);
        if (!isMessageNavEnabled) {
            hideNavContent();
        }
    }

    function setEnabled(value) {
        isMessageNavEnabled = value === true || value === 'true';
        applyEnabledState();
        if (isMessageNavEnabled) {
            scheduleRebuild();
        }
    }

    /* ── Counter ── */
    function updateCounter() {
        if (!counterEl) return;
        if (ticks.length < 2) {
            counterEl.textContent = '';
            return;
        }
        const current = Math.max(0, activeIndex) + 1;
        counterEl.textContent = `${current}/${ticks.length}`;
    }

    /* ── Navigation ── */
    function navigate(direction) {
        if (!ticks.length) return;
        const target = Math.max(0, Math.min(ticks.length - 1, activeIndex + direction));
        if (target === activeIndex) return;
        scrollToTurn(target);
    }

    function scrollToTurn(index) {
        if (index < 0 || index >= ticks.length) return;

        const messageEl = ticks[index].messageEl;
        const scrollArea = document.getElementById(SCROLL_AREA_ID);
        if (!scrollArea || !messageEl) return;

        // Scroll the message into view with some top padding
        const areaRect = scrollArea.getBoundingClientRect();
        const msgRect = messageEl.getBoundingClientRect();
        const offsetTop = msgRect.top - areaRect.top + scrollArea.scrollTop;
        const targetScroll = Math.max(0, offsetTop - 24);

        scrollArea.scrollTo({ top: targetScroll, behavior: 'smooth' });
        setActiveTick(index);
    }

    /* ── Tooltip ── */
    function showTooltipDelayed(index, event) {
        clearTimeout(tooltipTimer);
        tooltipTimer = setTimeout(() => showTooltip(index, event), TOOLTIP_DELAY);
    }

    function showTooltip(index) {
        if (!tooltipEl || index < 0 || index >= ticks.length) return;

        const tick = ticks[index];
        const roleEl = tooltipEl.querySelector('.message-nav-tooltip-role');
        const previewEl = tooltipEl.querySelector('.message-nav-tooltip-preview');
        tooltipEl.dataset.tickIndex = String(index);

        // Role label
        roleEl.textContent = tick.role === 'user' ? messageNavT('message_nav_role_you', 'You') : getAssistantLabel(tick.messageEl);
        if (tick.role === 'assistant' && !hasLoadedModelNameLookup) {
            loadModelNameLookup().then((loaded) => {
                if (!loaded || isDestroyed) return;
                if (tooltipEl?.classList.contains('is-visible') && tooltipEl.dataset.tickIndex === String(index)) {
                    roleEl.textContent = getAssistantLabel(tick.messageEl);
                }
            });
        }

        // Message preview
        const preview = getMessagePreview(tick.messageEl, tick.role);
        previewEl.textContent = preview || messageNavT('message_nav_empty_preview', '(empty)');

        // Position – to the left of the tick
        const tickRect = tick.el.getBoundingClientRect();
        tooltipEl.style.display = '';
        tooltipEl.classList.remove('is-visible');

        // Measure tooltip after applying its final horizontal constraint.
        // Anchoring with `right` keeps the preview consistently left of the rail.
        const navRect = navEl?.getBoundingClientRect();
        const viewportPadding = 8;
        const tooltipGap = 10;
        const anchorRect = navRect || tickRect;
        const availableLeftWidth = Math.max(
            140,
            anchorRect.left - tooltipGap - viewportPadding
        );
        const maxTooltipWidth = Math.min(260, availableLeftWidth);

        tooltipEl.style.left = 'auto';
        tooltipEl.style.right = `${Math.max(viewportPadding, window.innerWidth - anchorRect.left + tooltipGap)}px`;
        tooltipEl.style.maxWidth = `${maxTooltipWidth}px`;
        tooltipEl.style.visibility = 'hidden';
        tooltipEl.style.opacity = '0';
        tooltipEl.style.display = 'block';
        tooltipEl.classList.add('is-visible');

        const ttRect = tooltipEl.getBoundingClientRect();
        const ttHeight = ttRect.height;

        // Put tooltip next to the rail, vertically centered on the hovered tick.
        let top = tickRect.top + tickRect.height / 2 - ttHeight / 2;

        const viewportMinTop = viewportPadding;
        const viewportMaxTop = Math.max(viewportMinTop, window.innerHeight - ttHeight - viewportPadding);
        top = clamp(top, viewportMinTop, viewportMaxTop);

        tooltipEl.style.top = top + 'px';
        tooltipEl.style.visibility = '';
        tooltipEl.style.opacity = '';
    }

    function hideTooltip() {
        clearTimeout(tooltipTimer);
        if (tooltipEl) {
            tooltipEl.classList.remove('is-visible');
            delete tooltipEl.dataset.tickIndex;
        }
    }

    /* ── Helpers ── */
    function updateLocalizedLabels() {
        if (navEl) {
            navEl.setAttribute('aria-label', messageNavT('message_nav_aria_label', 'Message navigation'));
        }
        if (arrowUpEl) {
            arrowUpEl.setAttribute('aria-label', messageNavT('message_nav_previous_aria', 'Previous message'));
        }
        if (arrowDownEl) {
            arrowDownEl.setAttribute('aria-label', messageNavT('message_nav_next_aria', 'Next message'));
        }
        ticks.forEach((tick, index) => updateTickLabel(tick.el, tick.role, index));
        refreshVisibleTooltipContent();
    }

    function updateTickLabel(element, role, index) {
        const key = role === 'user' ? 'message_nav_tick_user_aria' : 'message_nav_tick_ai_aria';
        const fallback = role === 'user' ? 'Your message {index}' : 'AI message {index}';
        element.setAttribute('aria-label', messageNavFormatT(key, fallback, { index: index + 1 }));
    }

    function getAssistantLabel(el) {
        const metadata = getAssistantMetadata(el);
        const modelName = getAssistantModelName(metadata);
        if (modelName) return modelName;
        return messageNavT('message_nav_role_ai', 'AI');
    }

    function primeModelNameLookup() {
        loadModelNameLookup().then((loaded) => {
            if (!loaded || isDestroyed) return;
            refreshVisibleTooltipContent();
        });
    }

    function loadModelNameLookup(options = {}) {
        const forceRefresh = options.forceRefresh === true;
        if (hasLoadedModelNameLookup && !forceRefresh) {
            return Promise.resolve(true);
        }
        if (modelNameLookupPromise && !forceRefresh) {
            return modelNameLookupPromise;
        }
        if (typeof window === 'undefined' || typeof window.getCachedUserModels !== 'function') {
            return Promise.resolve(false);
        }

        modelNameLookupPromise = window.getCachedUserModels({ forceRefresh })
            .then((models) => {
                modelPublicNameById = buildModelPublicNameLookup(models);
                hasLoadedModelNameLookup = true;
                return true;
            })
            .catch((error) => {
                modelNameLookupPromise = null;
                console.error('Failed to load message navigation model labels:', error);
                return false;
            });

        return modelNameLookupPromise;
    }

    function buildModelPublicNameLookup(models) {
        const lookup = new Map();
        const aliasCandidates = new Map();
        const modelList = getAllSelectableModels(models);

        modelList.forEach((model) => {
            if (!model || typeof model !== 'object') return;
            const label = String(model.name || model.display_name || '').trim();
            if (!label) return;

            [
                model.model_id,
                model.id,
                model.base_model_id,
            ].forEach((value) => {
                const key = String(value || '').trim();
                if (key && !lookup.has(key)) {
                    lookup.set(key, label);
                }
            });

            [
                model.model,
                model.model_name,
            ].forEach((value) => {
                const key = String(value || '').trim();
                if (!key) return;
                if (!aliasCandidates.has(key)) {
                    aliasCandidates.set(key, new Set());
                }
                aliasCandidates.get(key).add(label);
            });
        });

        aliasCandidates.forEach((labels, key) => {
            if (labels.size === 1 && !lookup.has(key)) {
                lookup.set(key, Array.from(labels)[0]);
            }
        });

        return lookup;
    }

    function getAllSelectableModels(models) {
        if (!Array.isArray(models)) return [];
        if (typeof window !== 'undefined' && typeof window.BYOK?.getAllSelectableModels === 'function') {
            const groupedModels = window.BYOK.getAllSelectableModels(models);
            if (Array.isArray(groupedModels?.allModels)) {
                return groupedModels.allModels;
            }
        }
        return models;
    }

    function refreshVisibleTooltipContent() {
        if (!tooltipEl || !tooltipEl.classList.contains('is-visible')) return;
        const index = Number(tooltipEl.dataset.tickIndex || '-1');
        if (!Number.isInteger(index) || index < 0 || index >= ticks.length) return;
        const tick = ticks[index];
        const roleEl = tooltipEl.querySelector('.message-nav-tooltip-role');
        if (roleEl) {
            roleEl.textContent = tick.role === 'user'
                ? messageNavT('message_nav_role_you', 'You')
                : getAssistantLabel(tick.messageEl);
        }
        const previewEl = tooltipEl.querySelector('.message-nav-tooltip-preview');
        if (previewEl) {
            const preview = getMessagePreview(tick.messageEl, tick.role);
            previewEl.textContent = preview || messageNavT('message_nav_empty_preview', '(empty)');
        }
    }

    function getMessagePreview(el, role) {
        let text = '';
        try {
            if (role === 'user') {
                const contentEl = el.querySelector('.user-message-content');
                if (contentEl) {
                    text = contentEl.getAttribute('data-raw-content') || contentEl.textContent || '';
                }
            } else {
                // Assistant: get from .assistant-message-content
                const contentEls = el.querySelectorAll('.assistant-message-content, .assistant-message');
                contentEls.forEach((ce) => {
                    if (!text) {
                        const raw = ce.getAttribute('data-raw-content');
                        text = raw || ce.textContent || '';
                    }
                });
            }
        } catch (_) {}

        text = text.trim().replace(/\s+/g, ' ');
        if (text.length > PREVIEW_MAX_LEN) {
            text = text.substring(0, PREVIEW_MAX_LEN) + '…';
        }
        return text;
    }

    function getAssistantMetadata(el) {
        try {
            const raw = el?.dataset?.assistantMetadata;
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (_) {
            return null;
        }
    }

    function getAssistantModelName(metadata) {
        if (!metadata || typeof metadata !== 'object') return '';

        const publicName = getPublicModelNameFromMetadata(metadata);
        if (publicName) return publicName;

        const candidates = [
            metadata.model_public_name,
            metadata.modelPublicName,
            metadata.public_name,
            metadata.publicName,
            metadata.display_name,
            metadata.displayName,
            metadata.model_name,
            metadata.modelName,
            metadata.model,
            metadata.model_id,
            metadata.modelId,
        ];

        for (const value of candidates) {
            const normalized = normalizeAssistantModelName(value);
            if (normalized) return normalized;
        }

        return '';
    }

    function getPublicModelNameFromMetadata(metadata) {
        const idCandidates = [
            metadata.base_model_id,
            metadata.baseModelId,
            metadata.model_id,
            metadata.modelId,
            metadata.id,
            metadata.model,
            metadata.model_name,
            metadata.modelName,
        ];

        for (const value of idCandidates) {
            const key = String(value || '').trim();
            if (!key) continue;
            const publicName = modelPublicNameById.get(key);
            if (publicName) return publicName;
        }

        return '';
    }

    function normalizeAssistantModelName(value) {
        const text = String(value || '').trim();
        if (!text) return '';
        const parts = text.split('/');
        return (parts[parts.length - 1] || text).trim();
    }

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), max);
    }

    /* ── Initialization trigger ── */
    function boot() {
        if (document.getElementById(PARENT_CONTAINER_ID)) {
            init();
        } else {
            // Wait and retry
            retryIntervalId = setInterval(() => {
                if (document.getElementById(PARENT_CONTAINER_ID)) {
                    if (retryIntervalId) {
                        clearInterval(retryIntervalId);
                        retryIntervalId = null;
                    }
                    if (retryGiveUpTimeoutId) {
                        clearTimeout(retryGiveUpTimeoutId);
                        retryGiveUpTimeoutId = null;
                    }
                    init();
                }
            }, 300);
            // Give up after 10s
            retryGiveUpTimeoutId = setTimeout(() => {
                if (retryIntervalId) {
                    clearInterval(retryIntervalId);
                    retryIntervalId = null;
                }
                retryGiveUpTimeoutId = null;
            }, 10_000);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    // Expose for external use
    window.MessageNav = {
        rebuild: scheduleRebuild,
        destroy,
        setEnabled,
    };

    onChatSetupReadyHandler = () => {
        setEnabled(readMessageNavPreference());
    };
    document.addEventListener('chatSetupReady', onChatSetupReadyHandler);
})();
