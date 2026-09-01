let streamingRenderDebounceTimer = null;
let streamingRenderFrame = 0;
let streamingLastRenderAt = 0;
let pendingRenderQueue = new Map();
const STREAMING_RENDER_MIN_INTERVAL_MS = 50;
const STREAMING_RENDER_MEDIUM_INTERVAL_MS = 90;
const STREAMING_RENDER_LONG_INTERVAL_MS = 140;
const CHAT_SR_STATUS_REGION_ID = 'chatScreenReaderStatus';
const CHAT_SR_ALERT_REGION_ID = 'chatScreenReaderAlert';
const USER_MESSAGE_COLLAPSED_MAX_HEIGHT = 220;
const USER_MESSAGE_COLLAPSE_MIN_CHARS = 700;
const chatLiveRegionTimers = new WeakMap();

function getChatA11yText(key, fallback, vars = null) {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars || undefined);
    }
    if (!vars || typeof vars !== 'object') {
        return fallback;
    }
    return String(fallback).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

function updateChatLiveRegion(region, message) {
    if (!region) {
        return;
    }
    const text = String(message || '').trim();
    if (!text) {
        region.textContent = '';
        return;
    }
    const existingTimer = chatLiveRegionTimers.get(region);
    if (existingTimer) {
        clearTimeout(existingTimer);
    }
    region.textContent = '';
    const timer = setTimeout(() => {
        region.textContent = text;
    }, 30);
    chatLiveRegionTimers.set(region, timer);
}

function announceChatMessage(message, { assertive = false } = {}) {
    const regionId = assertive ? CHAT_SR_ALERT_REGION_ID : CHAT_SR_STATUS_REGION_ID;
    updateChatLiveRegion(document.getElementById(regionId), message);
}

function reportChatCopyFeedback({ success, key, fallback }) {
    const message = getChatA11yText(key, fallback);
    if (success) {
        if (typeof notifySuccess === 'function') {
            notifySuccess(message);
        }
        announceChatMessage(message);
        return;
    }

    if (typeof notifyError === 'function') {
        notifyError(message);
    }
    announceChatMessage(message, { assertive: true });
}

function resolveAssistantVersionInfo(container, versionInfo = null) {
    if (versionInfo && Number.isFinite(versionInfo.current) && Number.isFinite(versionInfo.total)) {
        return {
            current: Math.max(1, Math.trunc(versionInfo.current)),
            total: Math.max(1, Math.trunc(versionInfo.total)),
        };
    }

    const versionDisplayText = String(
        container?.querySelector('.assistant-version-display')?.textContent || ''
    ).trim();
    const versionDisplayMatch = versionDisplayText.match(/^(\d+)\s*\/\s*(\d+)$/);
    if (versionDisplayMatch) {
        return {
            current: Math.max(1, parseInt(versionDisplayMatch[1], 10) || 1),
            total: Math.max(1, parseInt(versionDisplayMatch[2], 10) || 1),
        };
    }

    const datasetCurrent = parseInt(container?.dataset?.retryCount || '0', 10) + 1;
    const datasetTotal = parseInt(container?.dataset?.totalVersions || '1', 10);
    const referenceId = String(container?.dataset?.referenceId || '').trim();
    const matchingContainers = referenceId
        ? getAssistantContainersByReference(referenceId, { meaningfulOnly: true })
        : [];

    const total = Math.max(1, matchingContainers.length || (Number.isFinite(datasetTotal) ? datasetTotal : 1));
    const current = Math.max(1, Number.isFinite(datasetCurrent) ? datasetCurrent : 1);

    return {
        current: Math.min(current, total),
        total,
    };
}

function ensureTranscriptAccessibility(container = document.getElementById('chatAreaContainer')) {
    if (!container) {
        return;
    }
    if (!container.hasAttribute('role')) {
        container.setAttribute('role', 'log');
    }
    container.setAttribute('aria-live', 'polite');
    container.setAttribute('aria-relevant', 'additions');
    container.setAttribute('aria-atomic', 'false');
    if (!container.hasAttribute('aria-label')) {
        container.setAttribute('aria-label', getChatA11yText('chat_sr_transcript_label', 'Conversation transcript'));
    }
}

function ensureScreenReaderNode(container, className, id) {
    if (!container) {
        return null;
    }
    let node = id ? document.getElementById(id) : null;
    if (node && node.parentElement !== container) {
        node = null;
    }
    if (!node) {
        node = document.createElement('span');
        node.className = className;
        if (id) {
            node.id = id;
        }
        container.insertBefore(node, container.firstChild || null);
    }
    return node;
}

function applyUserMessageAccessibility(container, { messageId } = {}) {
    if (!container) {
        return;
    }
    const normalizedId = String(messageId || container.dataset.userMessageId || '').trim() || 'user-message';
    container.setAttribute('role', 'article');
    container.setAttribute('aria-roledescription', getChatA11yText('chat_sr_message_role', 'message'));
    container.setAttribute('aria-busy', 'false');
    container.setAttribute('aria-hidden', 'false');

    const label = ensureScreenReaderNode(container, 'sr-only chat-message-sr-label', `sr-user-label-${normalizedId}`);
    const status = ensureScreenReaderNode(container, 'sr-only chat-message-sr-status', `sr-user-status-${normalizedId}`);
    if (label) {
        label.textContent = getChatA11yText('chat_sr_user_message_label', 'Your message');
        container.setAttribute('aria-labelledby', label.id);
    }
    if (status) {
        status.textContent = getChatA11yText('chat_sr_user_message_status', 'Sent');
        container.setAttribute('aria-describedby', status.id);
    }
}

function applyAssistantMessageAccessibility(container, { messageId, streaming = false, hasError = false, terminalState = null, versionInfo = null } = {}) {
    if (!container) {
        return;
    }
    const normalizedId = String(
        messageId
        || container.dataset.assistantMessageId
        || container.dataset.referenceId
        || container.id
    ).replace(/^a-/, '').trim() || 'assistant-message';
    const resolvedVersionInfo = resolveAssistantVersionInfo(container, versionInfo);
    const totalVersions = resolvedVersionInfo.total;
    const currentVersion = resolvedVersionInfo.current;
    const isHidden = container.dataset.hidden === 'true' || container.style.display === 'none';
    const resolvedTerminalState = String(
        terminalState || container.dataset.assistantTerminalState || ''
    ).trim().toLowerCase();
    const wasCancelled = resolvedTerminalState === 'cancelled' || resolvedTerminalState === 'canceled';

    container.setAttribute('role', 'article');
    container.setAttribute('aria-roledescription', getChatA11yText('chat_sr_message_role', 'message'));
    container.setAttribute('aria-busy', streaming ? 'true' : 'false');
    container.setAttribute('aria-hidden', isHidden ? 'true' : 'false');

    const label = ensureScreenReaderNode(container, 'sr-only chat-message-sr-label', `sr-assistant-label-${normalizedId}`);
    const status = ensureScreenReaderNode(container, 'sr-only chat-message-sr-status', `sr-assistant-status-${normalizedId}`);
    if (label) {
        label.textContent = getChatA11yText('chat_sr_assistant_message_label', 'Assistant response');
        container.setAttribute('aria-labelledby', label.id);
    }
    if (status) {
        const parts = [];
        if (totalVersions > 1) {
            parts.push(getChatA11yText(
                'chat_sr_response_version_status',
                'Version {current} of {total}',
                { current: currentVersion, total: totalVersions }
            ));
        }
        if (hasError) {
            parts.push(getChatA11yText('chat_sr_response_error_status', 'Response failed'));
        } else if (streaming) {
            parts.push(getChatA11yText('chat_sr_response_generating_status', 'Generating response'));
        } else if (wasCancelled) {
            parts.push(getChatA11yText('chat_sr_response_cancelled_status', 'Response stopped'));
        } else {
            parts.push(getChatA11yText('chat_sr_response_complete_status', 'Response complete'));
        }
        status.textContent = parts.join('. ');
        container.setAttribute('aria-describedby', status.id);
    }
}

function applyMessageActionToolbarAccessibility(toolbar, fallbackLabel, focusHost = null) {
    if (!toolbar) {
        return;
    }
    toolbar.setAttribute('role', 'toolbar');
    toolbar.setAttribute('aria-label', fallbackLabel);
    if (focusHost && !focusHost.hasAttribute('tabindex')) {
        focusHost.setAttribute('tabindex', '0');
    }
}

function updateUserMessageExpandControl(button, expanded) {
    if (!button) {
        return;
    }
    const label = expanded
        ? getStreamText('chat_user_message_show_less', 'Show less')
        : getStreamText('chat_user_message_show_more', 'Show more');
    const ariaLabel = expanded
        ? getChatA11yText('chat_user_message_show_less_aria', 'Collapse message')
        : getChatA11yText('chat_user_message_show_more_aria', 'Show full message');
    const icon = expanded ? Icons?.chevronTop : Icons?.chevron;

    button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    button.setAttribute('aria-label', ariaLabel);
    button.title = ariaLabel;
    button.innerHTML = `
        <span class="user-message-expand-label">${escapeStreamHtml(label)}</span>
        <span class="user-message-expand-icon" aria-hidden="true">${icon || ''}</span>
    `;
}

function setUserMessageExpanded(container, expanded, { announce = false } = {}) {
    if (!container) {
        return;
    }
    const nextExpanded = Boolean(expanded);
    container.dataset.userMessageExpanded = nextExpanded ? 'true' : 'false';
    const button = container.querySelector('.user-message-expand-toggle');
    updateUserMessageExpandControl(button, nextExpanded);

    if (announce) {
        const key = nextExpanded ? 'chat_user_message_show_less_aria' : 'chat_user_message_show_more_aria';
        const fallback = nextExpanded ? 'Collapse message' : 'Show full message';
        announceChatMessage(getChatA11yText(key, fallback));
    }
}

function refreshUserMessageExpandableState(container) {
    if (!container) {
        return;
    }
    const content = container.querySelector('.user-message-content');
    const button = container.querySelector('.user-message-expand-toggle');
    if (!content || !button) {
        return;
    }

    // Height is the real trigger, while the character threshold covers test
    // environments and not-yet-laid-out markdown without making short prompts jump.
    const rawContent = content.getAttribute('data-raw-content') || content.textContent || '';
    const contentHeight = content.scrollHeight || content.offsetHeight || 0;
    const shouldCollapse = rawContent.length >= USER_MESSAGE_COLLAPSE_MIN_CHARS
        || contentHeight > USER_MESSAGE_COLLAPSED_MAX_HEIGHT + 8;

    container.dataset.userMessageCollapsible = shouldCollapse ? 'true' : 'false';
    button.hidden = !shouldCollapse;
    button.setAttribute('aria-hidden', shouldCollapse ? 'false' : 'true');

    if (!shouldCollapse) {
        delete container.dataset.userMessageExpanded;
        updateUserMessageExpandControl(button, false);
        return;
    }

    const expanded = container.dataset.userMessageExpanded === 'true';
    if (!container.dataset.userMessageExpanded) {
        container.dataset.userMessageExpanded = 'false';
    }
    updateUserMessageExpandControl(button, expanded);
}

function queueUserMessageExpandableRefresh(container) {
    if (!container) {
        return;
    }
    if (container.__userMessageExpandableRefreshQueued) {
        return;
    }

    container.__userMessageExpandableRefreshQueued = true;
    const runRefresh = () => {
        container.__userMessageExpandableRefreshQueued = false;
        refreshUserMessageExpandableState(container);
        bindUserMessageExpandableLateRefresh(container);
    };

    if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(runRefresh);
        return;
    }
    setTimeout(runRefresh, 0);
}

function bindUserMessageExpandableLateRefresh(container) {
    if (!container) {
        return;
    }
    const content = container.querySelector('.user-message-content');
    if (!content) {
        return;
    }

    if (container.__userMessageExpandableObservedContent !== content) {
        container.__userMessageExpandableResizeObserver?.disconnect?.();
        container.__userMessageExpandableResizeObserver = null;
        container.__userMessageExpandableObservedContent = content;

        if (typeof ResizeObserver === 'function') {
            const observer = new ResizeObserver(() => {
                queueUserMessageExpandableRefresh(container);
            });
            observer.observe(content);
            container.__userMessageExpandableResizeObserver = observer;
        }
    }

    content.querySelectorAll?.('img').forEach((image) => {
        if (image.__userMessageExpandableRefreshBound) {
            return;
        }
        image.__userMessageExpandableRefreshBound = true;
        const refreshAfterMediaLayout = () => queueUserMessageExpandableRefresh(container);
        image.addEventListener?.('load', refreshAfterMediaLayout);
        image.addEventListener?.('error', refreshAfterMediaLayout);
        if (image.complete) {
            queueUserMessageExpandableRefresh(container);
        }
    });

    if (
        !container.__userMessageExpandableFontsReadyBound
        && typeof document !== 'undefined'
        && document.fonts?.ready?.then
    ) {
        container.__userMessageExpandableFontsReadyBound = true;
        document.fonts.ready.then(() => queueUserMessageExpandableRefresh(container)).catch(() => {});
    }
}

function scheduleUserMessageExpandableRefresh(container) {
    refreshUserMessageExpandableState(container);
    bindUserMessageExpandableLateRefresh(container);
    queueUserMessageExpandableRefresh(container);
}

function createUserMessageExpandControl(messageId, container) {
    const button = document.createElement('button');
    button.className = 'user-message-expand-toggle';
    button.type = 'button';
    button.hidden = true;
    button.setAttribute('aria-hidden', 'true');
    button.setAttribute('aria-controls', 'u-' + messageId);
    updateUserMessageExpandControl(button, false);
    button.addEventListener('click', () => {
        setUserMessageExpanded(container, container?.dataset.userMessageExpanded !== 'true', { announce: true });
    });
    return button;
}

function ensureUnsupportedFileWarningBadge(container) {
    if (!container || !(container instanceof HTMLElement)) {
        return;
    }
    let badge = container.querySelector(':scope > .inline-file-unsupported-badge');
    if (badge) {
        return;
    }
    badge = document.createElement('span');
    badge.className = 'inline-file-unsupported-badge';
    badge.setAttribute('role', 'img');
    const warningLabel = getStreamText('chat_file_not_supported_by_selected_model', 'Not supported by selected model');
    badge.setAttribute('aria-label', warningLabel);
    badge.title = warningLabel;
    badge.innerHTML = Icons.info;
    container.appendChild(badge);
}

function removeUnsupportedFileWarningBadge(container) {
    if (!container || !(container instanceof HTMLElement)) {
        return;
    }
    const badge = container.querySelector(':scope > .inline-file-unsupported-badge');
    if (badge) {
        badge.remove();
    }
}

function resolveUnsupportedWarningHost(element) {
    if (!(element instanceof HTMLElement)) {
        return null;
    }
    const assistantFileHost = element.closest('.assistant-file');
    if (assistantFileHost) {
        return assistantFileHost;
    }
    if (
        element.classList.contains('inline-files-element')
        || element.classList.contains('assistant-inline-image')
        || element.classList.contains('assistant-inline-video')
        || element.classList.contains('assistant-inline-audio')
        || element.classList.contains('assistant-file')
    ) {
        return element;
    }
    return element.closest('.inline-files-element, .assistant-inline-image, .assistant-inline-video, .assistant-inline-audio');
}

function applyUnsupportedFileWarnings(rawFileIds = []) {
    const ids = Array.isArray(rawFileIds) ? rawFileIds : [];
    const normalizedIds = Array.from(new Set(ids.map((id) => String(id || '').trim()).filter(Boolean)));
    const unsupportedSet = new Set(normalizedIds);
    const allNodes = document.querySelectorAll('[data-file-id]');
    allNodes.forEach((node) => {
        const fileId = String(node?.dataset?.fileId || '').trim();
        const host = resolveUnsupportedWarningHost(node);
        if (!host) {
            return;
        }
        if (fileId && unsupportedSet.has(fileId)) {
            host.classList.add('inline-file-unsupported');
            ensureUnsupportedFileWarningBadge(host);
            return;
        }
        host.classList.remove('inline-file-unsupported');
        removeUnsupportedFileWarningBadge(host);
    });
}

function clearUnsupportedFileWarnings() {
    const nodes = document.querySelectorAll('.inline-file-unsupported');
    nodes.forEach((node) => {
        node.classList.remove('inline-file-unsupported');
        removeUnsupportedFileWarningBadge(node);
    });
}

function computeUnsupportedFileWarningIdsForCurrentModel() {
    if (typeof window.isChatFileSupportedForCurrentModel !== 'function') {
        return [];
    }
    const ids = new Set();
    const allNodes = document.querySelectorAll('[data-file-id]');
    allNodes.forEach((node) => {
        const fileId = String(node?.dataset?.fileId || '').trim();
        if (!fileId) {
            return;
        }
        const fileType = String(node?.dataset?.fileType || '').trim().toLowerCase();
        if (!fileType) {
            return;
        }
        const supported = window.isChatFileSupportedForCurrentModel({
            type: fileType,
            file_type: fileType,
            mime_type: fileType,
            meta: { mime_type: fileType },
        });
        if (!supported) {
            ids.add(fileId);
        }
    });
    return Array.from(ids);
}

function refreshUnsupportedFileWarningsFromState() {
    if (typeof window.getUnsupportedFileWarningIds === 'function') {
        const ids = window.getUnsupportedFileWarningIds();
        if (Array.isArray(ids) && ids.length > 0) {
            applyUnsupportedFileWarnings(ids);
            return;
        }
    }
    applyUnsupportedFileWarnings(computeUnsupportedFileWarningIdsForCurrentModel());
}

window.applyUnsupportedFileWarnings = applyUnsupportedFileWarnings;
window.clearUnsupportedFileWarnings = clearUnsupportedFileWarnings;

window.ensureChatTranscriptAccessibility = ensureTranscriptAccessibility;
window.applyUserMessageAccessibility = applyUserMessageAccessibility;
window.applyAssistantMessageAccessibility = applyAssistantMessageAccessibility;
window.announceChatMessage = announceChatMessage;

window.addEventListener('modelSelect:changed', () => {
    clearUnsupportedFileWarnings();
    const schedule = typeof queueMicrotask === 'function'
        ? queueMicrotask
        : (callback) => Promise.resolve().then(callback);
    schedule(() => {
        refreshUnsupportedFileWarningsFromState();
    });
});
