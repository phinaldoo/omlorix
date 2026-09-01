function safeLocalStorageGet(key) {
    try {
        return localStorage.getItem(key);
    } catch (_) {
        return null;
    }
}

function getComplianceWatermarkPayload() {
    const storedEnabled = safeLocalStorageGet('compliance_enable_watermark');
    const storedWatermark = safeLocalStorageGet('compliance_watermark');
    const fallbackCompliance = (window.chatSetup && window.chatSetup.compliance) || {};

    let enabled;
    if (storedEnabled === 'true' || storedEnabled === '1') {
        enabled = true;
    } else if (storedEnabled === 'false' || storedEnabled === '0') {
        enabled = false;
    } else {
        enabled = Boolean(fallbackCompliance.enable_watermark);
    }

    let watermarkText = '';
    if (typeof storedWatermark === 'string' && storedWatermark.trim().length) {
        watermarkText = storedWatermark.trim();
    } else if (typeof fallbackCompliance.watermark === 'string' && fallbackCompliance.watermark.trim().length) {
        watermarkText = fallbackCompliance.watermark.trim();
    }

    return { enabled, watermarkText };
}

function appendComplianceWatermarkIfNeeded(rawText) {
    const base = typeof rawText === 'string' ? rawText : '';
    const { enabled, watermarkText } = getComplianceWatermarkPayload();
    if (!enabled || !watermarkText) {
        return base;
    }

    const trimmedBase = base.replace(/\s+$/, '');
    if (!trimmedBase.length) {
        return watermarkText;
    }
    return `${trimmedBase}\n\n${watermarkText}`;
}

// Expose the same export helper to the chat composer, selection toolbar, and
// Canvas modules.  Those modules are separate scripts but share this page's
// chat bootstrap and therefore must apply the same compliance policy.
if (typeof window !== 'undefined') {
    window.getComplianceWatermarkPayload = getComplianceWatermarkPayload;
    window.appendComplianceWatermarkIfNeeded = appendComplianceWatermarkIfNeeded;
}

function getVisibleAssistantCopyContentElements(container) {
    if (!container) {
        return [];
    }

    const contentElements = Array.from(
        container.querySelectorAll('.assistant-message .assistant-message-content')
    );

    if (!contentElements.length) {
        return [];
    }

    const getElementStyle = (element) => {
        if (!element || typeof window === 'undefined' || typeof window.getComputedStyle !== 'function') {
            return null;
        }
        try {
            return window.getComputedStyle(element);
        } catch (_) {
            return null;
        }
    };

    return contentElements.filter((contentEl) => {
        if (!contentEl || !contentEl.isConnected || contentEl.hidden) {
            return false;
        }

        const messageEl = contentEl.closest('.assistant-message');
        if (!messageEl || messageEl.hidden) {
            return false;
        }

        const contentStyle = getElementStyle(contentEl);
        if (contentStyle && (contentStyle.display === 'none' || contentStyle.visibility === 'hidden')) {
            return false;
        }

        const messageStyle = getElementStyle(messageEl);
        if (messageStyle && (messageStyle.display === 'none' || messageStyle.visibility === 'hidden')) {
            return false;
        }

        return true;
    });
}

function getAssistantCopyText(container) {
    const chunks = [];
    getVisibleAssistantCopyContentElements(container).forEach((contentEl) => {
        if (!contentEl) {
            return;
        }

        const raw = contentEl.getAttribute('data-raw-content') || contentEl.innerText || contentEl.textContent || '';
        const cleaned = String(raw || '').replace(/\s+$/, '');
        if (cleaned) {
            chunks.push(cleaned);
        }
    });
    return chunks.join('\n\n');
}

async function writeTextToClipboardWithFallback(text) {
    const normalizedText = typeof text === 'string' ? text : '';
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        await navigator.clipboard.writeText(normalizedText);
        return true;
    }

    const ta = document.createElement('textarea');
    ta.value = normalizedText;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);

    let copied = false;
    try {
        ta.focus();
        ta.select();
        copied = document.execCommand('copy');
    } finally {
        document.body.removeChild(ta);
    }

    if (!copied) {
        const errorMessage = typeof getStreamText === 'function'
            ? getStreamText('chat_clipboard_copy_fallback_failed', 'Clipboard copy fallback failed')
            : 'Clipboard copy fallback failed';
        throw new Error(errorMessage);
    }

    return true;
}


function checkIfLatestAssistantMessage(messageId) {
    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (!chatAreaContainer) return false;
    
    // Get all assistant message containers
    const assistantContainers = chatAreaContainer.querySelectorAll('.assistant-message-container');
    if (!assistantContainers.length) return false;
    
    // Check if this message is the last assistant container
    const lastAssistant = assistantContainers[assistantContainers.length - 1];
    return lastAssistant && lastAssistant.id === 'a-' + messageId;
}

function ensureAssistantMessageList(container) {
    if (!container) return null;
    let listDiv = container.querySelector('.assistant-message-list');
    if (!listDiv) {
        listDiv = document.createElement('div');
        listDiv.className = 'assistant-message-list';
        container.appendChild(listDiv);
    }
    applyMessageActionToolbarAccessibility(
        listDiv,
        getChatA11yText('chat_sr_assistant_actions_toolbar', 'Response actions')
    );
    return listDiv;
}

function assistantContainerHasMeaningfulOutput(container) {
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
}

function getAssistantContainersByReference(referenceId, { meaningfulOnly = false } = {}) {
    const normalizedReferenceId = String(referenceId || '').trim();
    if (!normalizedReferenceId) {
        return [];
    }

    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (!chatAreaContainer) {
        return [];
    }

    const containers = Array.from(chatAreaContainer.querySelectorAll('.assistant-message-container'))
        .filter((container) => container.dataset.referenceId === normalizedReferenceId);

    return meaningfulOnly
        ? containers.filter((container) => assistantContainerHasMeaningfulOutput(container))
        : containers;
}

function resetAssistantContainerForRetry(container, { messageId = null, referenceId = '', retryCount = 0, totalVersions = 1, announce = false } = {}) {
    if (!container) {
        return null;
    }

    const resolvedMessageId = String(
        messageId
        || container.dataset.assistantMessageId
        || container.dataset.referenceId
        || container.id
    ).replace(/^a-/, '').trim() || 'assistant-message';

    container.innerHTML = '';
    container.style.display = '';
    container.dataset.referenceId = String(referenceId || '').trim();
    container.dataset.retryCount = String(parseInt(retryCount, 10) || 0);
    container.dataset.totalVersions = String(Math.max(1, parseInt(totalVersions, 10) || 1));
    container.dataset.isLatestVersion = 'true';
    container.dataset.hidden = 'false';
    container.dataset.isStreaming = 'true';
    container.dataset.announceStreaming = announce ? 'true' : 'false';
    delete container.dataset.hasError;
    delete container.dataset.assistantMetadata;
    delete container.dataset.citations;
    delete container.dataset.assistantMessageId;
    delete container.dataset.assistantTerminalState;
    delete container.dataset.cancelPresentationFinalized;

    applyAssistantMessageAccessibility(container, {
        messageId: resolvedMessageId,
        streaming: true,
        hasError: false,
    });

    return container;
}

function appendBeforeAssistantList(container, element) {
    if (!container || !element) {
        return;
    }
    const listDiv = container.querySelector('.assistant-message-list');
    if (listDiv && listDiv.parentElement === container) {
        container.insertBefore(element, listDiv);
    } else {
        container.appendChild(element);
    }
}

const ASSISTANT_REGENERATE_ICON_SVG = Icons.refresh;
const ASSISTANT_REGENERATE_POPOVER_SELECTOR = '.assistant-regenerate-popover';
const ASSISTANT_REGENERATE_POPOVER_VIEWPORT_PADDING = 8;
const ASSISTANT_REGENERATE_POPOVER_OFFSET = 6;
const ASSISTANT_REGENERATE_POPOVER_TRANSITION_MS = 150; // Keep in sync with frontend/css/chat/chat.css.
const ASSISTANT_RETRY_GUIDANCE_MAX_CHARS = 2000; // Keep in sync with sending/regeneration.js and backend/app/chats/schemas.py

function getAssistantRegenerateText(key, fallback) {
    return typeof t === 'function' ? t(key, fallback) : fallback;
}

function autoResizeAssistantRegenerateTextarea(textarea) {
    if (!textarea) {
        return;
    }
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 96), 220)}px`;
}

function updateAssistantRegenerateCharCount(textarea, countEl) {
    if (!textarea || !countEl) {
        return;
    }
    countEl.textContent = `${textarea.value.length}/${ASSISTANT_RETRY_GUIDANCE_MAX_CHARS}`;
    countEl.classList.toggle('near-limit', textarea.value.length >= Math.floor(ASSISTANT_RETRY_GUIDANCE_MAX_CHARS * 0.9));
}

function getAssistantRegenerateUserMessageTarget(assistantContainer) {
    if (!assistantContainer) {
        return null;
    }

    const fromPreviousArea = assistantContainer.previousElementSibling;
    if (fromPreviousArea?.classList?.contains('user-message-area')) {
        const userMessageContainer = fromPreviousArea.querySelector('.user-message-container');
        const userMessage = userMessageContainer?.querySelector('.user-message');
        const userMessageContent = userMessageContainer?.querySelector('.user-message-content');
        const messageId = String(
            userMessageContainer?.dataset?.userMessageId
            || userMessageContent?.id?.replace(/^u-/, '')
            || ''
        ).trim();

        if (messageId && userMessageContainer && userMessage && userMessageContent) {
            return {
                messageId,
                userMessageContainer,
                userMessage,
                userMessageContent,
            };
        }
    }

    const referenceId = String(
        assistantContainer.dataset.serverReferenceId
        || assistantContainer.dataset.referenceId
        || ''
    ).trim();
    if (!referenceId) {
        return null;
    }

    const userMessageContent = document.getElementById(`u-${referenceId}`);
    const userMessage = userMessageContent?.closest('.user-message');
    const userMessageContainer = userMessageContent?.closest('.user-message-container');
    const messageId = String(
        userMessageContainer?.dataset?.userMessageId
        || userMessageContent?.id?.replace(/^u-/, '')
        || ''
    ).trim();

    if (!messageId || !userMessageContainer || !userMessage || !userMessageContent) {
        return null;
    }

    return {
        messageId,
        userMessageContainer,
        userMessage,
        userMessageContent,
    };
}

function restoreAssistantRegeneratePopover(popover) {
    if (!popover) return;
    if (popover.__regeneratePopoverCloseTimer) {
        clearTimeout(popover.__regeneratePopoverCloseTimer);
        popover.__regeneratePopoverCloseTimer = 0;
    }
    const originalParent = popover.__regeneratePopoverParent;
    if (originalParent && popover.parentElement !== originalParent) {
        originalParent.appendChild(popover);
    }
    popover.classList.remove(
        'open',
        'assistant-regenerate-popover-portal',
        'assistant-regenerate-popover-below'
    );
    popover.style.left = '';
    popover.style.top = '';
}

function detachAssistantRegeneratePopoverListeners(popover) {
    if (!popover?.__regeneratePopoverRepositionHandler) return;
    window.removeEventListener('resize', popover.__regeneratePopoverRepositionHandler);
    popover.__regeneratePopoverRepositionHandler = null;
}

function closeAssistantRegeneratePopover(popover, { restoreFocus = false } = {}) {
    if (!popover) return;
    const wasOpen = popover.classList.contains('open');
    if (popover.__regeneratePopoverFrame) {
        cancelAnimationFrame(popover.__regeneratePopoverFrame);
        popover.__regeneratePopoverFrame = 0;
    }
    detachAssistantRegeneratePopoverListeners(popover);
    popover.classList.remove('open');
    popover.__regeneratePopoverTrigger?.setAttribute('aria-expanded', 'false');
    if (restoreFocus) {
        try {
            popover.__regeneratePopoverTrigger?.focus();
        } catch (_) {}
    }

    if (!wasOpen) {
        restoreAssistantRegeneratePopover(popover);
        return;
    }

    // Keep the portal position and above/below class until the fade finishes.
    // Reparenting immediately would make a below-positioned popover jump above
    // its trigger before playing the closing movement.
    if (popover.__regeneratePopoverCloseTimer) {
        clearTimeout(popover.__regeneratePopoverCloseTimer);
    }
    popover.__regeneratePopoverCloseTimer = setTimeout(() => {
        popover.__regeneratePopoverCloseTimer = 0;
        if (!popover.classList.contains('open')) {
            restoreAssistantRegeneratePopover(popover);
        }
    }, ASSISTANT_REGENERATE_POPOVER_TRANSITION_MS);
}

function closeAllAssistantRegeneratePopovers(exceptPopover = null) {
    document.querySelectorAll(ASSISTANT_REGENERATE_POPOVER_SELECTOR).forEach((popover) => {
        if (popover !== exceptPopover && popover.classList.contains('open')) {
            closeAssistantRegeneratePopover(popover);
        }
    });
}

function positionAssistantRegeneratePopover(popover) {
    if (!popover) return;

    const trigger = popover.__regeneratePopoverTrigger;
    if (!trigger?.isConnected) {
        closeAssistantRegeneratePopover(popover);
        return;
    }

    if (popover.parentElement !== document.body) {
        document.body.appendChild(popover);
    }

    popover.classList.add('assistant-regenerate-popover-portal');

    const triggerRect = trigger.getBoundingClientRect();
    const popoverRect = popover.getBoundingClientRect();
    const maxLeft = Math.max(
        ASSISTANT_REGENERATE_POPOVER_VIEWPORT_PADDING,
        window.innerWidth - popoverRect.width - ASSISTANT_REGENERATE_POPOVER_VIEWPORT_PADDING
    );
    const maxTop = Math.max(
        ASSISTANT_REGENERATE_POPOVER_VIEWPORT_PADDING,
        window.innerHeight - popoverRect.height - ASSISTANT_REGENERATE_POPOVER_VIEWPORT_PADDING
    );

    const left = Math.min(
        Math.max(ASSISTANT_REGENERATE_POPOVER_VIEWPORT_PADDING, triggerRect.right - popoverRect.width),
        maxLeft
    );
    const preferredTop = triggerRect.top - popoverRect.height - ASSISTANT_REGENERATE_POPOVER_OFFSET;
    const fallbackTop = triggerRect.bottom + ASSISTANT_REGENERATE_POPOVER_OFFSET;
    const top = preferredTop >= ASSISTANT_REGENERATE_POPOVER_VIEWPORT_PADDING
        ? preferredTop
        : Math.min(fallbackTop, maxTop);

    // The closed transform must point toward the trigger. This class also
    // remains in place during closing so both halves of the transition agree.
    popover.classList.toggle('assistant-regenerate-popover-below', top >= triggerRect.bottom);
    popover.style.left = `${Math.round(left)}px`;
    popover.style.top = `${Math.round(top)}px`;
}

function scheduleAssistantRegeneratePopoverPosition(popover) {
    if (!popover?.classList.contains('open')) return;
    if (popover.__regeneratePopoverFrame) {
        cancelAnimationFrame(popover.__regeneratePopoverFrame);
    }
    popover.__regeneratePopoverFrame = requestAnimationFrame(() => {
        popover.__regeneratePopoverFrame = 0;
        positionAssistantRegeneratePopover(popover);
    });
}

function openAssistantRegeneratePopover(popover, trigger) {
    if (!popover || !trigger) return;

    if (popover.__regeneratePopoverCloseTimer) {
        clearTimeout(popover.__regeneratePopoverCloseTimer);
        popover.__regeneratePopoverCloseTimer = 0;
    }
    popover.__regeneratePopoverParent = popover.__regeneratePopoverParent || popover.parentElement;
    popover.__regeneratePopoverTrigger = trigger;
    closeAllAssistantRegeneratePopovers(popover);
    if (typeof closeAllMoreMenuDropdowns === 'function') {
        closeAllMoreMenuDropdowns();
    }
    positionAssistantRegeneratePopover(popover);
    popover.classList.add('open');
    trigger.setAttribute('aria-expanded', 'true');

    if (!popover.__regeneratePopoverRepositionHandler) {
        popover.__regeneratePopoverRepositionHandler = () => scheduleAssistantRegeneratePopoverPosition(popover);
        window.addEventListener('resize', popover.__regeneratePopoverRepositionHandler);
    }
}

function removeAssistantRegenerateButton(container) {
    if (!container) return;
    const listDiv = container.querySelector('.assistant-message-list');
    const existingMenu = listDiv?.querySelector('.assistant-regenerate-menu-container');
    if (existingMenu) {
        closeAssistantRegeneratePopover(existingMenu.__regeneratePopover);
        existingMenu.remove();
    }
}

function clearAssistantRegenerateButtons() {
    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (!chatAreaContainer) return;
    const assistantContainers = chatAreaContainer.querySelectorAll('.assistant-message-container');
    assistantContainers.forEach(removeAssistantRegenerateButton);
}

function refreshAssistantRegenerateButtons() {
    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (!chatAreaContainer) return;
    const assistantContainers = Array.from(chatAreaContainer.querySelectorAll('.assistant-message-container'));
    assistantContainers.forEach((container, index) => {
        if (index === assistantContainers.length - 1) {
            updateAssistantRegenerateButton(container);
        } else {
            removeAssistantRegenerateButton(container);
        }
    });
}

function getMaxAssistantRegenerations() {
    if (typeof window.MAX_ASSISTANT_REGENERATIONS === 'number') {
        return window.MAX_ASSISTANT_REGENERATIONS;
    }
    return 10;
}

function canRegenerateAssistantMessage(container) {
    if (!container) return false;
    if (container.dataset.isStreaming === 'true') return false;
    if (container.dataset.isLatestVersion !== 'true') return false;
    if (container.dataset.hidden === 'true') return false;
    const retryCount = parseInt(container.dataset.retryCount || '0', 10);
    const messageId = container.id && container.id.startsWith('a-')
        ? container.id.slice(2)
        : null;
    if (!messageId || !checkIfLatestAssistantMessage(messageId)) {
        return false;
    }
    return retryCount < (getMaxAssistantRegenerations() - 1);
}

function updateAssistantRegenerateButton(container, listDiv = null, messageId = null) {
    if (!container) return;
    // Regeneration is an operator-controlled capability, not a per-user
    // toolbar preference. Keep the button aligned with the group policy.
    const regenerationAllowed = typeof getChatBooleanSetting === 'function'
        ? getChatBooleanSetting('allow_regenerate_response', false)
        : false;

    listDiv = listDiv || container.querySelector('.assistant-message-list');
    const existingMenu = listDiv?.querySelector('.assistant-regenerate-menu-container') || null;
    const shouldShow = Boolean(regenerationAllowed) && canRegenerateAssistantMessage(container);

    if (!shouldShow) {
        removeAssistantRegenerateButton(container);
        return;
    }

    listDiv = listDiv || ensureAssistantMessageList(container);
    if (!listDiv) return;

    let menuContainer = existingMenu;
    if (!menuContainer) {
        menuContainer = document.createElement('div');
        menuContainer.className = 'assistant-regenerate-menu-container';

        const regenerateBtn = document.createElement('button');
        regenerateBtn.className = 'assistant-message-list-button assistant-regenerate-btn';
        regenerateBtn.type = 'button';
        regenerateBtn.setAttribute('aria-label', getAssistantRegenerateText('chat_regenerate_menu_aria', 'Open revise response options'));
        regenerateBtn.title = getAssistantRegenerateText('chat_regenerate_menu_title', 'Revise response');
        regenerateBtn.setAttribute('aria-haspopup', 'dialog');
        regenerateBtn.setAttribute('aria-expanded', 'false');
        regenerateBtn.innerHTML = ASSISTANT_REGENERATE_ICON_SVG;

        const popover = document.createElement('div');
        popover.className = 'assistant-regenerate-popover';
        popover.setAttribute('role', 'dialog');
        popover.setAttribute('aria-label', getAssistantRegenerateText('chat_regenerate_menu_title', 'Revise response'));

        const title = document.createElement('div');
        title.className = 'assistant-regenerate-popover-title';
        title.textContent = getAssistantRegenerateText('chat_regenerate_menu_title', 'Revise response');

        const description = document.createElement('p');
        description.className = 'assistant-regenerate-popover-description';
        description.textContent = getAssistantRegenerateText(
            'chat_regenerate_description',
            'Retry the same prompt with new guidance, or edit your previous message instead.'
        );

        const presetsRow = document.createElement('div');
        presetsRow.className = 'assistant-regenerate-preset-row';

        const customRow = document.createElement('div');
        customRow.className = 'assistant-regenerate-custom-row';

        const customInput = document.createElement('textarea');
        customInput.className = 'assistant-regenerate-input';
        customInput.rows = 4;
        customInput.maxLength = ASSISTANT_RETRY_GUIDANCE_MAX_CHARS;
        customInput.placeholder = getAssistantRegenerateText('chat_regenerate_input_placeholder', 'Describe how to revise the next response…');
        customInput.setAttribute('aria-label', getAssistantRegenerateText('chat_regenerate_input_label', 'Revision guidance'));

        const metaRow = document.createElement('div');
        metaRow.className = 'assistant-regenerate-meta-row';

        const shortcutHint = document.createElement('div');
        shortcutHint.className = 'assistant-regenerate-shortcut-hint';
        shortcutHint.textContent = getAssistantRegenerateText('chat_regenerate_shortcut_hint', 'Ctrl/Cmd+Enter to revise');

        const charCount = document.createElement('div');
        charCount.className = 'assistant-regenerate-char-count';

        const actionsRow = document.createElement('div');
        actionsRow.className = 'assistant-regenerate-actions-row';

        const editPreviousBtn = document.createElement('button');
        editPreviousBtn.className = 'assistant-regenerate-secondary-btn';
        editPreviousBtn.type = 'button';
        editPreviousBtn.textContent = getAssistantRegenerateText('chat_regenerate_edit_previous', 'Edit previous message');

        const applyBtn = document.createElement('button');
        applyBtn.className = 'assistant-regenerate-apply-btn';
        applyBtn.type = 'button';
        applyBtn.textContent = getAssistantRegenerateText('chat_regenerate_apply', 'Revise');

        const getTargetId = () => (
            menuContainer.dataset.targetMessageId
            || (container.id && container.id.startsWith('a-') ? container.id.slice(2) : '')
        );

        let isSubmitting = false;
        const setSubmittingState = (submitting) => {
            isSubmitting = submitting;
            popover.querySelectorAll('button').forEach((button) => {
                button.disabled = submitting;
            });
            customInput.disabled = submitting;
            if (!submitting) {
                refreshActions();
            }
        };

        const refreshActions = () => {
            editPreviousBtn.disabled = !getAssistantRegenerateUserMessageTarget(container);
        };

        const submitGuidance = async (retryGuidance, { clearCustomInput = false } = {}) => {
            if (isSubmitting || typeof window.triggerRegeneration !== 'function') {
                return;
            }
            const targetId = getTargetId();
            if (!targetId) {
                return;
            }
            setSubmittingState(true);
            let popoverClosed = false;
            const closePopoverAfterRegenerationStarts = () => {
                if (popoverClosed) return;
                popoverClosed = true;
                closeAssistantRegeneratePopover(popover, { restoreFocus: false });
                if (clearCustomInput) {
                    customInput.value = '';
                    autoResizeAssistantRegenerateTextarea(customInput);
                    updateAssistantRegenerateCharCount(customInput, charCount);
                }
            };
            try {
                const success = await window.triggerRegeneration(targetId, {
                    retryGuidance,
                    onRegenerationStarted: closePopoverAfterRegenerationStarts,
                });
                if (success) {
                    closePopoverAfterRegenerationStarts();
                }
            } finally {
                setSubmittingState(false);
            }
        };

        const createPresetButton = (preset, labelKey, fallback) => {
            const button = document.createElement('button');
            button.className = 'assistant-regenerate-preset-btn';
            button.type = 'button';
            button.textContent = getAssistantRegenerateText(labelKey, fallback);
            button.addEventListener('click', async (e) => {
                e.preventDefault();
                e.stopPropagation();
                await submitGuidance({ mode: 'preset', preset });
            });
            return button;
        };

        presetsRow.appendChild(createPresetButton('try_again', 'chat_regenerate_try_again', 'Try Again'));
        presetsRow.appendChild(createPresetButton('add_details', 'chat_regenerate_add_details', 'Add Details'));
        presetsRow.appendChild(createPresetButton('more_concise', 'chat_regenerate_more_concise', 'More Concise'));

        const submitCustomGuidance = async () => {
            const instruction = customInput.value.trim();
            await submitGuidance(
                instruction ? { mode: 'custom', instruction } : null,
                { clearCustomInput: true }
            );
        };

        customInput.addEventListener('input', () => {
            autoResizeAssistantRegenerateTextarea(customInput);
            updateAssistantRegenerateCharCount(customInput, charCount);
        });

        customInput.addEventListener('keydown', async (e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                e.stopPropagation();
                await submitCustomGuidance();
                return;
            }
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                closeAssistantRegeneratePopover(popover, { restoreFocus: true });
            }
        });

        popover.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                closeAssistantRegeneratePopover(popover, { restoreFocus: true });
            }
        });

        applyBtn.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            await submitCustomGuidance();
        });

        editPreviousBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const target = getAssistantRegenerateUserMessageTarget(container);
            if (!target) {
                notifyWarning?.(
                    getAssistantRegenerateText(
                        'chat_regenerate_edit_previous_unavailable',
                        'Could not find the previous user message to edit.'
                    )
                );
                return;
            }
            closeAssistantRegeneratePopover(popover, { restoreFocus: false });
            enterUserMessageEditMode(
                target.messageId,
                target.userMessageContainer,
                target.userMessage,
                target.userMessageContent
            );
        });

        regenerateBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const isOpen = popover.classList.contains('open');
            if (!isOpen) {
                refreshActions();
                openAssistantRegeneratePopover(popover, regenerateBtn);
                requestAnimationFrame(() => {
                    try {
                        customInput.focus();
                    } catch (_) {}
                });
            } else {
                closeAssistantRegeneratePopover(popover, { restoreFocus: true });
            }
        });

        document.addEventListener('click', (e) => {
            if (!menuContainer.contains(e.target) && !popover.contains(e.target)) {
                closeAssistantRegeneratePopover(popover);
            }
        });

        popover.appendChild(title);
        popover.appendChild(description);
        popover.appendChild(presetsRow);
        customRow.appendChild(customInput);
        popover.appendChild(customRow);
        metaRow.appendChild(shortcutHint);
        metaRow.appendChild(charCount);
        popover.appendChild(metaRow);
        actionsRow.appendChild(editPreviousBtn);
        actionsRow.appendChild(applyBtn);
        popover.appendChild(actionsRow);
        menuContainer.appendChild(regenerateBtn);
        menuContainer.appendChild(popover);
        menuContainer.__regeneratePopover = popover;
        menuContainer.__regenerateTrigger = regenerateBtn;

        autoResizeAssistantRegenerateTextarea(customInput);
        updateAssistantRegenerateCharCount(customInput, charCount);
        refreshActions();

        const statsEl = listDiv.querySelector('.assistant-message-stats');
        if (statsEl) {
            listDiv.insertBefore(menuContainer, statsEl);
        } else {
            listDiv.appendChild(menuContainer);
        }
    }

    const resolvedTargetId = messageId
        || (container.id && container.id.startsWith('a-') ? container.id.slice(2) : '');
    menuContainer.dataset.targetMessageId = resolvedTargetId;
    const regenerateBtn = menuContainer.querySelector('.assistant-regenerate-btn');
    if (regenerateBtn) {
        regenerateBtn.innerHTML = ASSISTANT_REGENERATE_ICON_SVG;
    }
}

window.updateAssistantRegenerateButton = updateAssistantRegenerateButton;
window.refreshAssistantRegenerateButtons = refreshAssistantRegenerateButtons;
window.closeAllAssistantRegeneratePopovers = closeAllAssistantRegeneratePopovers;

/** Remove a response-version switcher without disturbing completed message actions. */
function removeAssistantVersionSwitcher(container) {
    const existingList = container?.querySelector('.assistant-message-list');
    if (!existingList) return;

    existingList.querySelector('.assistant-version-switcher')?.remove();
    if (!existingList.childElementCount) {
        existingList.remove();
    }
}

function updateAssistantVersionSwitcher(container) {
    if (!container) return;

    // Streaming responses intentionally have no action list or version controls.
    // The completed-response renderer calls this function again after clearing
    // `isStreaming`, at which point it is safe to expose the new version.
    if (container.dataset.isStreaming === 'true') {
        removeAssistantVersionSwitcher(container);
        return;
    }

    const referenceId = container.dataset.referenceId || '';
    const referenceContainers = referenceId
        ? getAssistantContainersByReference(referenceId, { meaningfulOnly: true })
        : [];
    const storedTotalVersions = parseInt(container.dataset.totalVersions || '1', 10) || 1;
    // The server-backed total can include a completed error response that is not
    // considered meaningful transcript output. Keep whichever count is larger.
    const totalVersions = Math.max(referenceContainers.length, storedTotalVersions);

    container.dataset.totalVersions = String(Math.max(1, totalVersions));

    if (totalVersions <= 1) {
        removeAssistantVersionSwitcher(container);
        return;
    }

    // Version controls belong to the completed assistant action list. Never let
    // this helper create that list independently during loading or streaming.
    const listDiv = container.querySelector('.assistant-message-list');
    if (!listDiv) return;

    let versionSwitcher = listDiv.querySelector('.assistant-version-switcher');
    if (!versionSwitcher) {
        versionSwitcher = document.createElement('div');
        versionSwitcher.className = 'assistant-version-switcher';
        versionSwitcher.setAttribute('role', 'group');
        versionSwitcher.setAttribute('aria-label', getChatA11yText('chat_sr_response_versions', 'Response versions'));

        const prevBtn = document.createElement('button');
        prevBtn.className = 'assistant-message-list-button assistant-version-prev';
        prevBtn.type = 'button';
        prevBtn.innerHTML = Icons.chevronLeft;
        prevBtn.setAttribute('aria-label', getStreamText('chat_previous_version', 'Previous version'));
        prevBtn.title = getStreamText('chat_previous_version', 'Previous version');

        const versionDisplay = document.createElement('span');
        versionDisplay.className = 'assistant-version-display';

        const nextBtn = document.createElement('button');
        nextBtn.className = 'assistant-message-list-button assistant-version-next';
        nextBtn.type = 'button';
        nextBtn.innerHTML = Icons.chevronRight;
        nextBtn.setAttribute('aria-label', getStreamText('chat_next_version', 'Next version'));
        nextBtn.title = getStreamText('chat_next_version', 'Next version');

        versionSwitcher.appendChild(prevBtn);
        versionSwitcher.appendChild(versionDisplay);
        versionSwitcher.appendChild(nextBtn);

        listDiv.insertBefore(versionSwitcher, listDiv.firstChild);

        prevBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (typeof window.switchAssistantVersion === 'function') {
                const current = parseInt(container.dataset.retryCount || '0', 10);
                if (current > 0) {
                    window.switchAssistantVersion(referenceId, current - 1);
                }
            }
        });

        nextBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (typeof window.switchAssistantVersion === 'function') {
                const current = parseInt(container.dataset.retryCount || '0', 10);
                const total = parseInt(container.dataset.totalVersions || '1', 10);
                if (current < total - 1) {
                    window.switchAssistantVersion(referenceId, current + 1);
                }
            }
        });
    }

    if (versionSwitcher && versionSwitcher !== listDiv.firstElementChild) {
        listDiv.insertBefore(versionSwitcher, listDiv.firstChild);
    }

    const retryCount = parseInt(container.dataset.retryCount || '0', 10);
    const currentVersion = Math.min(totalVersions, Math.max(1, retryCount + 1));
    const versionDisplay = versionSwitcher.querySelector('.assistant-version-display');
    const prevBtn = versionSwitcher.querySelector('.assistant-version-prev');
    const nextBtn = versionSwitcher.querySelector('.assistant-version-next');

    if (versionDisplay) {
        versionDisplay.textContent = `${currentVersion}/${totalVersions}`;
        versionDisplay.setAttribute(
            'aria-label',
            getChatA11yText('chat_sr_response_version_status', 'Version {current} of {total}', {
                current: currentVersion,
                total: totalVersions,
            })
        );
    }
    if (prevBtn) {
        prevBtn.disabled = retryCount === 0;
        prevBtn.classList.toggle('disabled', retryCount === 0);
    }
    if (nextBtn) {
        nextBtn.disabled = retryCount >= totalVersions - 1;
        nextBtn.classList.toggle('disabled', retryCount >= totalVersions - 1);
    }
    applyAssistantMessageAccessibility(container, {
        messageId: container.id?.replace(/^a-/, ''),
        streaming: container.dataset.isStreaming === 'true',
        hasError: container.dataset.hasError === 'true',
        versionInfo: { current: retryCount + 1, total: totalVersions },
    });
}

window.updateAssistantVersionSwitcher = updateAssistantVersionSwitcher;
window.assistantContainerHasMeaningfulOutput = assistantContainerHasMeaningfulOutput;
window.getAssistantContainersByReference = getAssistantContainersByReference;
window.resetAssistantContainerForRetry = resetAssistantContainerForRetry;

function getAssistantSpeechPreferredSpeed() {
    if (window.AssistantSpeech && typeof window.AssistantSpeech.getPreferredSpeed === 'function') {
        return window.AssistantSpeech.getPreferredSpeed();
    }
    const stored = Number(safeGetLocalStorageItem('speech_playback_speed'));
    if (Number.isFinite(stored)) {
        return Math.min(2, Math.max(0.5, stored));
    }
    return 1;
}

function formatAssistantSpeechSpeed(value) {
    const speed = Number(value);
    if (!Number.isFinite(speed)) {
        return '1.0x';
    }
    return `${speed.toFixed(1)}x`;
}

function getAssistantSpeechText(container) {
    if (!container) {
        return '';
    }
    if (window.AssistantSpeech && typeof window.AssistantSpeech.extractTextFromContainer === 'function') {
        return window.AssistantSpeech.extractTextFromContainer(container);
    }

    const chunks = [];
    container.querySelectorAll('.assistant-message .assistant-message-content').forEach((contentEl) => {
        if (!contentEl) return;
        const raw = contentEl.getAttribute('data-raw-content') || contentEl.innerText || contentEl.textContent || '';
        const cleaned = String(raw || '').replace(/\s+/g, ' ').trim();
        if (cleaned) {
            chunks.push(cleaned);
        }
    });
    return chunks.join(' ').trim();
}

function updateAssistantSpeechButtonState(btn, { isPlaying = false, isLoading = false, hasError = false } = {}) {
    if (!btn) return;
    const playIcon = Icons.play;
    const stopIcon = Icons.stop;
    const loadingIcon = `<span class="assistant-speech-loading-indicator" aria-hidden="true"><span></span><span></span><span></span></span>`;
    let icon = playIcon;
    let label = getStreamText('assistant_speech_read_aloud', 'Read aloud');
    let title = label;
    if (isLoading) {
        icon = loadingIcon;
        label = getStreamText('assistant_speech_preparing_audio', 'Preparing audio');
        title = getStreamText('assistant_speech_preparing_read_aloud_audio', 'Preparing read aloud audio');
    } else if (isPlaying) {
        icon = stopIcon;
        label = getStreamText('assistant_speech_stop', 'Stop');
        title = getStreamText('assistant_speech_stop_reading_aloud', 'Stop reading aloud');
    } else if (hasError) {
        label = getStreamText('assistant_speech_retry', 'Retry');
        title = label;
    }
    btn.innerHTML = `${icon}<span>${label}</span>`;
    btn.classList.toggle('is-loading', isLoading);
    btn.classList.toggle('is-active', isPlaying);
    btn.setAttribute('aria-pressed', (isPlaying || isLoading) ? 'true' : 'false');
    btn.setAttribute('aria-busy', isLoading ? 'true' : 'false');
    btn.title = title;
}

function refreshAssistantSpeechMenus() {
    const preferredSpeed = getAssistantSpeechPreferredSpeed();
    document.querySelectorAll('.assistant-more-menu-container').forEach((menuContainer) => {
        if (!menuContainer) return;
        const dropdown = menuContainer.__moreMenuDropdown || menuContainer.querySelector('.more-menu-dropdown');
        const speedRange = dropdown?.querySelector('.assistant-speech-speed-range');
        const speedValue = dropdown?.querySelector('.assistant-speech-speed-value');
        if (speedRange) {
            speedRange.value = String(preferredSpeed);
        }
        if (speedValue) {
            speedValue.textContent = formatAssistantSpeechSpeed(preferredSpeed);
        }

        const speechBtn = dropdown?.querySelector('.assistant-speech-toggle-btn');
        if (!speechBtn) return;

        const speechState = window.AssistantSpeech?.getState?.() || {};
        const container = menuContainer.closest('.assistant-message-container');
        const speechMessageId = String(container?.dataset?.assistantMessageId || container?.id?.replace(/^a-/, '') || '');
        const isTargetMessage = Boolean(
            speechMessageId
            && String(speechState.activeMessageId || '') === speechMessageId
        );
        const isLoading = Boolean(isTargetMessage && speechState.isLoading);
        const isPlaying = Boolean(isTargetMessage && speechState.isPlaying);
        const hasError = Boolean(
            speechState.lastError
            && String(speechState.errorMessageId || '') === speechMessageId
        );
        updateAssistantSpeechButtonState(speechBtn, {
            isPlaying,
            isLoading,
            hasError,
        });
    });
}

let assistantSpeechUiHooksBound = false;
let lastNotifiedAssistantSpeechError = null;
function ensureAssistantSpeechUiHooks() {
    if (assistantSpeechUiHooksBound) return;
    assistantSpeechUiHooksBound = true;

    if (window.AssistantSpeech && typeof window.AssistantSpeech.subscribe === 'function') {
        window.AssistantSpeech.subscribe((speechState = {}) => {
            const currentError = String(speechState.lastError || '').trim();
            if (currentError && currentError !== lastNotifiedAssistantSpeechError) {
                lastNotifiedAssistantSpeechError = currentError;
                notifyError?.(currentError);
            } else if (!currentError) {
                lastNotifiedAssistantSpeechError = null;
            }
            refreshAssistantSpeechMenus();
        });
    }

    window.addEventListener('assistantSpeechSpeedChanged', () => {
        refreshAssistantSpeechMenus();
    });
}

const MORE_MENU_DROPDOWN_SELECTOR = '.more-menu-dropdown';
const MORE_MENU_VIEWPORT_PADDING = 8;
const MORE_MENU_OFFSET = 4;
const CHAT_MENU_SCROLL_CLOSE_SELECTOR = '#chatArea, .split-chat-area';

let messageMenuScrollCloseHandlersBound = false;

function isChatMenuScrollCloseTarget(target) {
    if (!(target instanceof Element)) {
        return false;
    }
    return Boolean(target.closest(CHAT_MENU_SCROLL_CLOSE_SELECTOR));
}

function closeFloatingMessageMenus() {
    closeAllMoreMenuDropdowns();
    if (typeof closeAllAssistantRegeneratePopovers === 'function') {
        closeAllAssistantRegeneratePopovers();
    }
}

function ensureMessageMenusCloseOnChatScroll() {
    if (messageMenuScrollCloseHandlersBound || typeof document === 'undefined') {
        return;
    }
    messageMenuScrollCloseHandlersBound = true;

    const handleChatScrollStart = (event) => {
        const hasOpenMoreMenu = Boolean(document.querySelector(`${MORE_MENU_DROPDOWN_SELECTOR}.open`));
        const hasOpenRegeneratePopover = Boolean(document.querySelector(`${ASSISTANT_REGENERATE_POPOVER_SELECTOR}.open`));
        if (!hasOpenMoreMenu && !hasOpenRegeneratePopover) {
            return;
        }
        if (!isChatMenuScrollCloseTarget(event.target)) {
            return;
        }
        closeFloatingMessageMenus();
    };

    document.addEventListener('scroll', handleChatScrollStart, { passive: true, capture: true });
    document.addEventListener('wheel', handleChatScrollStart, { passive: true, capture: true });
    document.addEventListener('touchmove', handleChatScrollStart, { passive: true, capture: true });
}

ensureMessageMenusCloseOnChatScroll();

function restoreMoreMenuDropdown(dropdown) {
    if (!dropdown) return;

    const originalParent = dropdown.__moreMenuParent;
    if (originalParent && dropdown.parentElement !== originalParent) {
        originalParent.appendChild(dropdown);
    }

    dropdown.classList.remove('more-dropdown-portal');
    dropdown.style.left = '';
    dropdown.style.top = '';
}

function detachMoreMenuDropdownListeners(dropdown) {
    if (!dropdown?.__moreMenuRepositionHandler) return;

    window.removeEventListener('resize', dropdown.__moreMenuRepositionHandler);
    dropdown.__moreMenuRepositionHandler = null;
}

function closeMoreMenuDropdown(dropdown) {
    if (!dropdown) return;

    if (dropdown.__moreMenuFrame) {
        cancelAnimationFrame(dropdown.__moreMenuFrame);
        dropdown.__moreMenuFrame = 0;
    }

    detachMoreMenuDropdownListeners(dropdown);
    dropdown.classList.remove('open');
    dropdown.__moreMenuTrigger?.setAttribute('aria-expanded', 'false');
    restoreMoreMenuDropdown(dropdown);
}

function closeAllMoreMenuDropdowns(exceptDropdown = null) {
    document.querySelectorAll(MORE_MENU_DROPDOWN_SELECTOR).forEach((dropdown) => {
        if (dropdown !== exceptDropdown && dropdown.classList.contains('open')) {
            closeMoreMenuDropdown(dropdown);
        }
    });
}

function positionMoreMenuDropdown(dropdown) {
    if (!dropdown) return;

    const trigger = dropdown.__moreMenuTrigger;
    if (!trigger?.isConnected) {
        closeMoreMenuDropdown(dropdown);
        return;
    }

    if (dropdown.parentElement !== document.body) {
        document.body.appendChild(dropdown);
    }

    dropdown.classList.add('more-dropdown-portal');

    const triggerRect = trigger.getBoundingClientRect();
    const dropdownRect = dropdown.getBoundingClientRect();
    const maxLeft = Math.max(MORE_MENU_VIEWPORT_PADDING, window.innerWidth - dropdownRect.width - MORE_MENU_VIEWPORT_PADDING);
    const maxTop = Math.max(MORE_MENU_VIEWPORT_PADDING, window.innerHeight - dropdownRect.height - MORE_MENU_VIEWPORT_PADDING);
    const spaceAbove = Math.max(0, triggerRect.top - MORE_MENU_VIEWPORT_PADDING);
    const spaceBelow = Math.max(0, window.innerHeight - triggerRect.bottom - MORE_MENU_VIEWPORT_PADDING);

    const left = Math.min(
        Math.max(MORE_MENU_VIEWPORT_PADDING, triggerRect.right - dropdownRect.width),
        maxLeft
    );
    const preferredTopAbove = triggerRect.top - dropdownRect.height - MORE_MENU_OFFSET;
    const preferredTopBelow = triggerRect.bottom + MORE_MENU_OFFSET;
    const shouldOpenBelow = dropdownRect.height > spaceAbove && spaceBelow > spaceAbove;
    const top = Math.min(
        Math.max(
            MORE_MENU_VIEWPORT_PADDING,
            shouldOpenBelow ? preferredTopBelow : preferredTopAbove
        ),
        maxTop
    );

    dropdown.style.left = `${Math.round(left)}px`;
    dropdown.style.top = `${Math.round(top)}px`;
}

function scheduleMoreMenuDropdownPosition(dropdown) {
    if (!dropdown?.classList.contains('open')) return;

    if (dropdown.__moreMenuFrame) {
        cancelAnimationFrame(dropdown.__moreMenuFrame);
    }

    dropdown.__moreMenuFrame = requestAnimationFrame(() => {
        dropdown.__moreMenuFrame = 0;
        positionMoreMenuDropdown(dropdown);
    });
}

function openMoreMenuDropdown(dropdown, trigger) {
    if (!dropdown || !trigger) return;

    dropdown.__moreMenuParent = dropdown.__moreMenuParent || dropdown.parentElement;
    dropdown.__moreMenuTrigger = trigger;

    closeAllMoreMenuDropdowns(dropdown);
    if (typeof closeAllAssistantRegeneratePopovers === 'function') {
        closeAllAssistantRegeneratePopovers();
    }
    dropdown.classList.add('open');
    positionMoreMenuDropdown(dropdown);
    trigger.setAttribute('aria-expanded', 'true');

    if (!dropdown.__moreMenuRepositionHandler) {
        dropdown.__moreMenuRepositionHandler = () => scheduleMoreMenuDropdownPosition(dropdown);
        window.addEventListener('resize', dropdown.__moreMenuRepositionHandler);
    }
}

// ===== Assistant More Menu (three-dot) with bookmark and delete =====
function ensureAssistantMoreMenu({ listDiv, insertBeforeStats, messageId, container, isBookmarked, showDelete = false }) {
    if (!listDiv) return;

    let moreMenuContainer = listDiv.querySelector('.assistant-more-menu-container');
    
    if (container.dataset.hasError === 'true') {
        if (moreMenuContainer) {
            closeMoreMenuDropdown(moreMenuContainer.__moreMenuDropdown);
            moreMenuContainer.remove();
        }
        return;
    }
    
    if (!moreMenuContainer) {
        moreMenuContainer = document.createElement('div');
        moreMenuContainer.className = 'assistant-more-menu-container';
        
        const moreBtn = document.createElement('button');
        moreBtn.className = 'assistant-message-list-button assistant-more-btn';
        moreBtn.type = 'button';
        moreBtn.setAttribute('aria-label', getStreamText('chat_more_options', 'More options'));
        moreBtn.setAttribute('aria-expanded', 'false');
        moreBtn.title = getStreamText('chat_more_options', 'More options');
        moreBtn.innerHTML = Icons.ellipsisVertical;
        
        const dropdown = document.createElement('div');
        dropdown.className = 'select-dropdown message-more-dropdown more-menu-dropdown';
        moreMenuContainer.__moreMenuDropdown = dropdown;

        const speechSection = document.createElement('div');
        speechSection.className = 'select-dropdown-item assistant-speech-section';

        const speechBtn = document.createElement('button');
        speechBtn.className = 'select-dropdown-button assistant-speech-toggle-btn';
        speechBtn.type = 'button';

        const speedControl = document.createElement('div');
        speedControl.className = 'assistant-speech-speed-control';

        const speedLabel = document.createElement('span');
        speedLabel.className = 'assistant-speech-speed-label';
        speedLabel.textContent = getStreamText('assistant_speech_speed', 'Speed');

        const speedRange = document.createElement('input');
        speedRange.className = 'assistant-speech-speed-range';
        speedRange.type = 'range';
        speedRange.min = '0.5';
        speedRange.max = '2';
        speedRange.step = '0.1';
        speedRange.setAttribute(
            'aria-label',
            getStreamText('user_settings_speech_speed_aria', 'Assistant speech playback speed')
        );

        const speedValue = document.createElement('span');
        speedValue.className = 'assistant-speech-speed-value';

        speedControl.appendChild(speedLabel);
        speedControl.appendChild(speedRange);
        speedControl.appendChild(speedValue);
        speechSection.appendChild(speechBtn);
        speechSection.appendChild(speedControl);
        
        const bookmarksEnabled = typeof window === 'undefined' || window.enableBookmarksFeature !== false;
        const bookmarkBtn = bookmarksEnabled ? document.createElement('button') : null;
        if (bookmarkBtn) {
            bookmarkBtn.className = 'select-dropdown-button assistant-bookmark-btn';
            bookmarkBtn.type = 'button';
            updateBookmarkButtonState(bookmarkBtn, isBookmarked);
        }

        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'select-dropdown-button assistant-delete-dropdown-btn select-dropdown-button-red';
        deleteBtn.type = 'button';
        deleteBtn.innerHTML = `${typeof Icons.trash !== 'undefined' ? Icons.trash : ''}<span>${getStreamText('chat_delete_message', 'Delete message')}</span>`;

        const getSpeechMessageId = () => {
            return String(container.dataset.assistantMessageId || messageId || '');
        };

        const setSpeechSpeedInUi = (value) => {
            const speed = Number.isFinite(Number(value)) ? Math.min(2, Math.max(0.5, Number(value))) : 1;
            speedRange.value = String(speed);
            speedValue.textContent = formatAssistantSpeechSpeed(speed);
        };

        const updateSpeechUiState = () => {
            setSpeechSpeedInUi(getAssistantSpeechPreferredSpeed());
            if (!window.AssistantSpeech) {
                updateAssistantSpeechButtonState(speechBtn, { isPlaying: false });
                speechBtn.disabled = true;
                speedRange.disabled = true;
                speedLabel.textContent = getStreamText('assistant_speech_unavailable', 'Speech unavailable');
                return;
            }

            const speechMessageId = getSpeechMessageId();
            const speechState = window.AssistantSpeech.getState?.() || {};
            const isTargetMessage = Boolean(
                speechMessageId
                && String(speechState.activeMessageId || '') === speechMessageId
            );
            const isPlaying = Boolean(isTargetMessage && speechState.isPlaying);
            const isLoading = Boolean(isTargetMessage && speechState.isLoading);
            const hasError = Boolean(
                speechState.lastError
                && String(speechState.errorMessageId || '') === speechMessageId
            );
            speechBtn.disabled = false;
            speedRange.disabled = isLoading;
            speedLabel.textContent = isLoading
                ? getStreamText('assistant_speech_preparing_audio', 'Preparing audio')
                : getStreamText('assistant_speech_speed', 'Speed');
            updateAssistantSpeechButtonState(speechBtn, {
                isPlaying,
                isLoading,
                hasError,
            });
        };

        speechBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();

            if (!window.AssistantSpeech) {
                notifyError?.(getStreamText('assistant_speech_unavailable_error', 'Speech playback is not available in this browser.'));
                return;
            }

            const speechMessageId = getSpeechMessageId();
            if (!speechMessageId) {
                notifyError?.(getStreamText('assistant_speech_message_loading_error', 'Cannot play speech: message is still loading.'));
                return;
            }

            const speechState = window.AssistantSpeech.getState?.() || {};
            const isBusy = Boolean(
                speechMessageId
                && String(speechState.activeMessageId || '') === speechMessageId
                && (speechState.isPlaying || speechState.isLoading)
            );

            if (isBusy) {
                window.AssistantSpeech.stop?.();
                updateSpeechUiState();
                return;
            }

            const speechText = getAssistantSpeechText(container);
            if (!speechText) {
                notifyError?.(getStreamText('assistant_speech_no_text_error', 'No assistant text available to read aloud.'));
                return;
            }

            try {
                window.AssistantSpeech.speakMessage({
                    messageId: speechMessageId,
                    text: speechText,
                    speed: getAssistantSpeechPreferredSpeed(),
                });
            } catch (err) {
                notifyError?.(err?.message || getStreamText('assistant_speech_start_failed', 'Failed to start speech playback.'));
            }
            updateSpeechUiState();
        });

        speedRange.addEventListener('input', (e) => {
            e.stopPropagation();
            const rawSpeed = Number(e.target.value);
            const speed = Number.isFinite(rawSpeed) ? Math.min(2, Math.max(0.5, rawSpeed)) : 1;

            if (typeof window.updateSpeechPlaybackSpeedPreference === 'function') {
                window.updateSpeechPlaybackSpeedPreference(speed, {
                    persist: true,
                    restartIfPlaying: true,
                    source: 'assistant-more-menu',
                });
            } else {
                safeSetLocalStorageItem('speech_playback_speed', String(speed));
                if (window.AssistantSpeech && typeof window.AssistantSpeech.setPreferredSpeed === 'function') {
                    window.AssistantSpeech.setPreferredSpeed(speed, { restartIfPlaying: true });
                }
            }
            setSpeechSpeedInUi(speed);
        });

        speedRange.addEventListener('click', (e) => {
            e.stopPropagation();
        });

        speedRange.addEventListener('pointerdown', (e) => {
            e.stopPropagation();
        });

        ensureAssistantSpeechUiHooks();
        updateSpeechUiState();
        
        if (bookmarkBtn) {
            bookmarkBtn.addEventListener('click', async (e) => {
                e.preventDefault();
                e.stopPropagation();

                const assistantMsgId = container.dataset.assistantMessageId;
                if (!assistantMsgId) {
                    if (typeof notifyError === 'function') {
                        notifyError(getStreamText('chat_bookmark_missing_message_later_error', 'Cannot bookmark: message ID not available yet'));
                    }
                    closeMoreMenuDropdown(dropdown);
                    return;
                }

                bookmarkBtn.disabled = true;

                try {
                    const res = await window.authedFetch('/api/v1/chats/messages/bookmark', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message_id: assistantMsgId }),
                    });

                    if (!res.ok) {
                        const errorData = await res.json().catch(() => ({}));
                        throw new Error(errorData.detail || getStreamText('chat_bookmark_update_failed', 'Failed to update bookmark'));
                    }

                    const data = await res.json();
                    const newBookmarked = data.bookmarked;
                    container.dataset.bookmarked = newBookmarked ? 'true' : 'false';
                    updateBookmarkButtonState(bookmarkBtn, newBookmarked);

                    if (typeof notifySuccess === 'function') {
                        notifySuccess(newBookmarked
                            ? getStreamText('chat_response_bookmarked', 'Response bookmarked')
                            : getStreamText('bookmarks_remove_success', 'Bookmark removed'));
                    }
                } catch (err) {
                    console.error('Bookmark toggle failed:', err);
                    if (typeof notifyError === 'function') {
                        notifyError(err.message || getStreamText('chat_bookmark_update_failed', 'Failed to update bookmark'));
                    }
                } finally {
                    bookmarkBtn.disabled = false;
                    closeMoreMenuDropdown(dropdown);
                }
            });
        }

        deleteBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const assistantMsgId = String(container.dataset.assistantMessageId || messageId || '').trim();
            closeMoreMenuDropdown(dropdown);
            openDeleteMessageModal({ messageId: assistantMsgId, role: 'assistant' });
        });
        
        dropdown.appendChild(speechSection);
        if (bookmarkBtn) {
            const bookmarkItem = document.createElement('div');
            bookmarkItem.className = 'select-dropdown-item';
            bookmarkItem.appendChild(bookmarkBtn);
            dropdown.appendChild(bookmarkItem);
        }
        if (showDelete) {
            const deleteItem = document.createElement('div');
            deleteItem.className = 'select-dropdown-item';
            deleteItem.appendChild(deleteBtn);
            dropdown.appendChild(deleteItem);
        }
        moreMenuContainer.appendChild(moreBtn);
        moreMenuContainer.appendChild(dropdown);
        
        // Toggle dropdown on button click
        moreBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const isOpen = dropdown.classList.contains('open');
            if (!isOpen) {
                updateSpeechUiState();
                openMoreMenuDropdown(dropdown, moreBtn);
            } else {
                closeMoreMenuDropdown(dropdown);
            }
        });
        
        // Close dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (!moreMenuContainer.contains(e.target) && !dropdown.contains(e.target)) {
                closeMoreMenuDropdown(dropdown);
            }
        });
        
        insertBeforeStats(moreMenuContainer);
    } else {
        // Update bookmark state for existing menu
        const existingBookmarkBtn = moreMenuContainer.querySelector('.assistant-bookmark-btn');
        if (existingBookmarkBtn) {
            updateBookmarkButtonState(existingBookmarkBtn, isBookmarked);
        }
        let deleteBtn = moreMenuContainer.querySelector('.assistant-delete-dropdown-btn');
        const dropdown = moreMenuContainer.querySelector('.more-menu-dropdown');
        if (showDelete) {
            if (!deleteBtn) {
                deleteBtn = document.createElement('button');
                deleteBtn.className = 'select-dropdown-button assistant-delete-dropdown-btn select-dropdown-button-red';
                deleteBtn.type = 'button';
                deleteBtn.innerHTML = `${typeof Icons.trash !== 'undefined' ? Icons.trash : ''}<span>${getStreamText('chat_delete_message', 'Delete message')}</span>`;
                deleteBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const assistantMsgId = String(container.dataset.assistantMessageId || messageId || '').trim();
                    closeMoreMenuDropdown(dropdown);
                    openDeleteMessageModal({ messageId: assistantMsgId, role: 'assistant' });
                });
            }
            if (dropdown && !dropdown.contains(deleteBtn)) {
                const deleteItem = document.createElement('div');
                deleteItem.className = 'select-dropdown-item';
                deleteItem.appendChild(deleteBtn);
                dropdown.appendChild(deleteItem);
            }
        } else if (deleteBtn) {
            deleteBtn.closest('.select-dropdown-item')?.remove();
        }
        refreshAssistantSpeechMenus();
    }
}

function updateBookmarkButtonState(btn, isBookmarked) {
    if (!btn) return;
    
    const bookmarkIcon = Icons.bookmark;
    const bookmarkedIcon = Icons.bookmarkFilled;
    
    btn.innerHTML = isBookmarked
        ? `${bookmarkedIcon}<p>${getStreamText('bookmarks_remove_bookmark', 'Remove bookmark')}</p>`
        : `${bookmarkIcon}<p>${getStreamText('chat_bookmark', 'Bookmark')}</p>`;
    btn.classList.toggle('bookmarked', isBookmarked);
}

function getUserMessagePromptContent(messageId, container) {
    const storedText = String(container?.__editState?.text || '').trim();
    if (storedText) {
        return storedText;
    }

    const contentEl = container?.querySelector('.user-message-content') || document.getElementById(`u-${messageId}`);
    return String(contentEl?.getAttribute('data-raw-content') || contentEl?.innerText || '').trim();
}

function getUserMessageBackendId(messageId, container = null) {
    const normalizedLocalId = String(messageId || '').trim();
    const localContainer = container || document.querySelector(`.user-message-container[data-user-message-id="${CSS.escape(normalizedLocalId)}"]`);
    const contentEl = localContainer?.querySelector('.user-message-content') || document.getElementById(`u-${normalizedLocalId}`);
    const resolvedServerId = String(
        localContainer?.dataset?.serverMessageId
        || localContainer?.__editState?.serverMessageId
        || contentEl?.dataset?.serverMessageId
        || ''
    ).trim();
    if (resolvedServerId) {
        return resolvedServerId;
    }
    if (localContainer?.dataset?.optimisticMessage === 'true' || contentEl?.dataset?.optimisticMessage === 'true') {
        return '';
    }
    return normalizedLocalId;
}

// ===== User Message More Menu with bookmark and delete =====
function createUserMessageMoreMenu(messageId, container, options = {}) {
    const { showDelete = false } = options;
    const moreMenuContainer = document.createElement('div');
    moreMenuContainer.className = 'user-more-menu-container';
    
    const moreBtn = document.createElement('button');
    moreBtn.className = 'user-message-list-button user-more-btn';
    moreBtn.type = 'button';
    moreBtn.setAttribute('aria-label', getStreamText('chat_more_options', 'More options'));
    moreBtn.setAttribute('aria-expanded', 'false');
    moreBtn.title = getStreamText('chat_more_options', 'More options');
    moreBtn.innerHTML = Icons.ellipsisVertical;
    
    const dropdown = document.createElement('div');
    dropdown.className = 'select-dropdown message-more-dropdown more-menu-dropdown';
    moreMenuContainer.__moreMenuDropdown = dropdown;
    
    const bookmarksEnabled = typeof window === 'undefined' || window.enableBookmarksFeature !== false;
    const bookmarkBtn = bookmarksEnabled ? document.createElement('button') : null;
    if (bookmarkBtn) {
        bookmarkBtn.className = 'select-dropdown-button user-bookmark-btn';
        bookmarkBtn.type = 'button';
        updateBookmarkButtonState(bookmarkBtn, false);
    }

    const savePromptBtn = document.createElement('button');
    savePromptBtn.className = 'select-dropdown-button user-save-prompt-btn';
    savePromptBtn.type = 'button';
    savePromptBtn.innerHTML = `${typeof Icons.archive !== 'undefined' ? Icons.archive : ''}<p>${getStreamText('prompt_library_save_message', 'Save to prompt library')}</p>`;

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'select-dropdown-button user-delete-dropdown-btn select-dropdown-button-red';
    deleteBtn.type = 'button';
    deleteBtn.innerHTML = `${typeof Icons.trash !== 'undefined' ? Icons.trash : ''}<span>${getStreamText('chat_delete_message', 'Delete message')}</span>`;
    
    if (bookmarkBtn) {
        bookmarkBtn.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();

            const backendMessageId = getUserMessageBackendId(messageId, container);
            if (!backendMessageId) {
                if (typeof notifyError === 'function') {
                    notifyError(getStreamText('chat_bookmark_missing_message_error', 'Cannot bookmark: message ID not available'));
                }
                closeMoreMenuDropdown(dropdown);
                return;
            }

            bookmarkBtn.disabled = true;

            try {
                const res = await window.authedFetch('/api/v1/chats/messages/bookmark', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message_id: backendMessageId }),
                });

                if (!res.ok) {
                    const errorData = await res.json().catch(() => ({}));
                    throw new Error(errorData.detail || getStreamText('chat_bookmark_update_failed', 'Failed to update bookmark'));
                }

                const data = await res.json();
                const newBookmarked = data.bookmarked;
                container.dataset.bookmarked = newBookmarked ? 'true' : 'false';
                updateBookmarkButtonState(bookmarkBtn, newBookmarked);

                if (typeof notifySuccess === 'function') {
                    notifySuccess(newBookmarked
                        ? getStreamText('chat_message_bookmarked', 'Message bookmarked')
                        : getStreamText('bookmarks_remove_success', 'Bookmark removed'));
                }
            } catch (err) {
                console.error('Bookmark toggle failed:', err);
                if (typeof notifyError === 'function') {
                    notifyError(err.message || getStreamText('chat_bookmark_update_failed', 'Failed to update bookmark'));
                }
            } finally {
                bookmarkBtn.disabled = false;
                closeMoreMenuDropdown(dropdown);
            }
        });
    }

    savePromptBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();

        const promptContent = getUserMessagePromptContent(messageId, container);
        const promptLibraryManager = window.PromptLibraryManager;

        if (!promptContent) {
            if (typeof notifyWarning === 'function') {
                notifyWarning(getStreamText('prompt_library_nothing_to_save', 'Nothing to save to prompt library'));
            }
            closeMoreMenuDropdown(dropdown);
            return;
        }

        if (!promptLibraryManager || typeof promptLibraryManager.openCreateFromContent !== 'function') {
            if (typeof notifyError === 'function') {
                notifyError(getStreamText('prompt_library_unavailable', 'Prompt library is not available'));
            }
            closeMoreMenuDropdown(dropdown);
            return;
        }

        promptLibraryManager.openCreateFromContent(promptContent);
        closeMoreMenuDropdown(dropdown);
    });
    
    if (bookmarkBtn) {
        const bookmarkItem = document.createElement('div');
        bookmarkItem.className = 'select-dropdown-item';
        bookmarkItem.appendChild(bookmarkBtn);
        dropdown.appendChild(bookmarkItem);
    }
    if (typeof window === 'undefined' || window.enablePromptsFeature !== false) {
        const savePromptItem = document.createElement('div');
        savePromptItem.className = 'select-dropdown-item';
        savePromptItem.appendChild(savePromptBtn);
        dropdown.appendChild(savePromptItem);
    }
    if (showDelete) {
        deleteBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            closeMoreMenuDropdown(dropdown);
            const backendMessageId = getUserMessageBackendId(messageId, container);
            if (!backendMessageId) {
                notifyError?.(getStreamText('chat_delete_missing_message_later_error', 'Cannot delete: message ID not available yet'));
                return;
            }
            openDeleteMessageModal({ messageId: backendMessageId, role: 'user' });
        });
        const deleteItem = document.createElement('div');
        deleteItem.className = 'select-dropdown-item';
        deleteItem.appendChild(deleteBtn);
        dropdown.appendChild(deleteItem);
    }
    moreMenuContainer.appendChild(moreBtn);
    moreMenuContainer.appendChild(dropdown);
    
    // Toggle dropdown on button click
    moreBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const isOpen = dropdown.classList.contains('open');
        if (!isOpen) {
            openMoreMenuDropdown(dropdown, moreBtn);
        } else {
            closeMoreMenuDropdown(dropdown);
        }
    });
    
    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
        if (!moreMenuContainer.contains(e.target) && !dropdown.contains(e.target)) {
            closeMoreMenuDropdown(dropdown);
        }
    });
    
    return moreMenuContainer;
}

// Update user message bookmark state from loaded data
function updateUserMessageBookmarkState(messageId, isBookmarked) {
    const userMessageContent = document.getElementById('u-' + messageId);
    if (!userMessageContent) return;
    
    const container = userMessageContent.closest('.user-message-container');
    if (!container) return;
    
    container.dataset.bookmarked = isBookmarked ? 'true' : 'false';
    const bookmarkBtn = container.querySelector('.user-bookmark-btn');
    if (bookmarkBtn) {
        updateBookmarkButtonState(bookmarkBtn, isBookmarked);
    }
}

window.updateUserMessageBookmarkState = updateUserMessageBookmarkState;
