function ensureAssistantFeedbackControls({ listDiv, insertBeforeStats, messageId, initialReaction }) {
    if (!listDiv || typeof insertBeforeStats !== 'function') {
        return;
    }

    const normalizedMessageId = String(messageId ?? '').trim();
    if (!normalizedMessageId) {
        return;
    }

    let controls = listDiv.querySelector('.assistant-feedback-controls');
    if (!controls) {
        controls = document.createElement('div');
        controls.className = 'assistant-feedback-controls';
        controls.dataset.messageId = normalizedMessageId;
        controls.setAttribute('role', 'group');
        controls.setAttribute('aria-label', getChatPreviewTranslation('chat_feedback_group_label', 'Response feedback'));
        insertBeforeStats(controls);

        const buttonsConfig = [
            {
                reaction: 'thumbs_up',
                label: getChatPreviewTranslation('chat_feedback_thumbs_up_label', 'Thumbs up'),
                title: getChatPreviewTranslation('chat_feedback_thumbs_up_title', 'Mark response as helpful'),
                icon: Icons?.thumbUp || '👍',
            },
            {
                reaction: 'thumbs_down',
                label: getChatPreviewTranslation('chat_feedback_thumbs_down_label', 'Thumbs down'),
                title: getChatPreviewTranslation('chat_feedback_thumbs_down_title', 'Mark response as not helpful'),
                icon: Icons?.thumbDown || '👎',
            },
        ];

        buttonsConfig.forEach((config) => {
            const button = document.createElement('button');
            button.className = 'assistant-message-list-button assistant-feedback-button';
            button.type = 'button';
            button.setAttribute('aria-label', config.label);
            button.setAttribute('aria-pressed', 'false');
            button.title = config.title;
            button.dataset.reaction = config.reaction;
            button.dataset.icon = config.icon;
            button.innerHTML = config.icon;
            button.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                handleAssistantFeedbackButtonClick(event.currentTarget);
            });
            controls.appendChild(button);
        });
    } else {
        controls.dataset.messageId = normalizedMessageId;
    }

    if (initialReaction) {
        controls.dataset.selectedReaction = initialReaction;
        listDiv.dataset.selectedFeedbackReaction = initialReaction;
    }

    updateAssistantFeedbackVisualState(controls, controls.dataset.selectedReaction || null);
}


function handleAssistantFeedbackButtonClick(button) {
    if (!(button instanceof Element)) {
        return;
    }
    const controls = button.closest('.assistant-feedback-controls');
    if (!controls) {
        return;
    }
    const messageId = (controls.dataset.messageId || '').trim();
    const reaction = button.dataset.reaction;
    if (!messageId || !reaction) {
        return;
    }

    showFeedbackTooltip({ controls, button, messageId, reaction });
}


function showFeedbackTooltip({ controls, button, messageId, reaction }) {
    closeFeedbackTooltip();

    const overlay = document.createElement('div');
    overlay.className = 'feedback-modal-overlay shared-modal-overlay';
    overlay.hidden = true;
    overlay.inert = true;
    overlay.setAttribute('aria-hidden', 'true');
    overlay.dataset.messageId = messageId;
    overlay.dataset.reaction = reaction;
    overlay.tabIndex = -1;
    overlay._returnFocus = button instanceof HTMLElement
        ? button
        : (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    overlay._feedbackModalIds = createFeedbackModalIds();

    const isPositive = reaction === 'thumbs_up';
    const iconClass = isPositive ? 'thumbs-up' : 'thumbs-down';
    const modalText = getFeedbackModalText(reaction);
    const titleText = modalText.title;
    const subtitleText = modalText.prompt;
    const ids = overlay._feedbackModalIds;

    const thumbsUpSvg = Icons.thumbUp;
    const thumbsDownSvg = Icons.thumbDown;

    overlay.innerHTML = `
        <div class="feedback-modal shared-modal shared-modal--compact shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="${ids.titleId}" aria-describedby="${ids.descriptionId}" tabindex="-1">
            <div class="feedback-modal-header shared-modal-header shared-modal-header--main">
                <div class="feedback-modal-header-left">
                    <div class="feedback-modal-icon ${iconClass}">
                        ${isPositive ? thumbsUpSvg : thumbsDownSvg}
                    </div>
                    <span class="feedback-modal-title shared-modal-title" id="${ids.titleId}">${escapeHtml(titleText)}</span>
                </div>
                <button type="button" class="om-button shared-modal-close" data-feedback-action="close" aria-label="${escapeHtml(modalText.close)}">
                    ${Icons?.close}
                </button>
            </div>
            <div class="feedback-modal-body shared-modal-body">
                <p class="feedback-modal-subtitle shared-modal-subtitle" id="${ids.descriptionId}">${escapeHtml(subtitleText)}</p>
            </div>
            <footer class="feedback-modal-actions shared-modal-footer">
                <button type="button" class="om-button border submit" data-feedback-action="comment" aria-label="${escapeHtml(modalText.addComment)}">
                    ${Icons?.edit}
                    <span>${escapeHtml(modalText.addComment)}</span>
                </button>
                <button type="button" class="om-button border cancel" data-feedback-action="skip" aria-label="${escapeHtml(modalText.submitWithoutComment)}">
                    ${Icons?.check}
                    <span>${escapeHtml(modalText.submitWithoutComment)}</span>
                </button>
            </footer>
        </div>
    `;

    document.body.appendChild(overlay);
    overlay.hidden = false;
    overlay.inert = false;
    overlay.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');

    // Event handlers
    const closeBtn = overlay.querySelector('[data-feedback-action="close"]');
    const addCommentBtn = overlay.querySelector('[data-feedback-action="comment"]');
    const skipBtn = overlay.querySelector('[data-feedback-action="skip"]');

    closeBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        closeFeedbackTooltip();
    });

    addCommentBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        expandFeedbackTooltip(overlay, controls, button, messageId, reaction);
    });

    skipBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        closeFeedbackTooltip({ restoreFocus: false });
        submitFeedbackReaction({ controls, button, messageId, reaction, comment: null, restoreFocusTarget: button });
    });

    // Close on overlay background click
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
            closeFeedbackTooltip();
        }
    });

    overlay.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            e.preventDefault();
            closeFeedbackTooltip();
            return;
        }
        trapFeedbackModalFocus(e, overlay);
    });

    // Animate in
    requestAnimationFrame(() => {
        overlay.classList.add('is-visible');
        focusFeedbackModalPrimaryAction(overlay);
    });
}


function expandFeedbackTooltip(overlay, controls, button, messageId, reaction) {
    if (!overlay) return;

    const modal = overlay.querySelector('.feedback-modal');
    if (!modal) return;

    const isPositive = reaction === 'thumbs_up';
    const iconClass = isPositive ? 'thumbs-up' : 'thumbs-down';
    const thumbsUpSvg = Icons.thumbUp;
    const thumbsDownSvg = Icons.thumbDown;
    const modalText = getFeedbackModalText(reaction);
    const ids = overlay._feedbackModalIds || createFeedbackModalIds();
    overlay._feedbackModalIds = ids;
    modal.setAttribute('aria-labelledby', ids.titleId);
    modal.setAttribute('aria-describedby', ids.labelId);

    modal.innerHTML = `
        <div class="feedback-modal-header shared-modal-header shared-modal-header--main">
            <div class="feedback-modal-header-left">
                <div class="feedback-modal-icon ${iconClass}">
                    ${isPositive ? thumbsUpSvg : thumbsDownSvg}
                </div>
                <span class="feedback-modal-title shared-modal-title" id="${ids.titleId}">${escapeHtml(modalText.title)}</span>
            </div>
            <button type="button" class="om-button shared-modal-close" data-feedback-action="close" aria-label="${escapeHtml(modalText.close)}">
                ${Icons?.close}
            </button>
        </div>
        <div class="feedback-modal-body shared-modal-body">
            <label class="sr-only" id="${ids.labelId}" for="${ids.textareaId}">${escapeHtml(modalText.commentLabel)}</label>
            <textarea
                id="${ids.textareaId}"
                class="feedback-modal-textarea"
                aria-labelledby="${ids.labelId}"
                aria-describedby="${ids.charCountId}"
                placeholder="${escapeHtml(modalText.placeholder)}"
                maxlength="${FEEDBACK_MODAL_COMMENT_MAX_LENGTH}"
                rows="4"
            ></textarea>
        </div>
        <footer class="feedback-modal-input-footer shared-modal-footer">
            <span class="feedback-modal-char-count" id="${ids.charCountId}" aria-live="polite"></span>
            <div class="feedback-modal-footer-buttons">
                <button type="button" class="om-button border cancel" data-feedback-action="cancel" aria-label="${escapeHtml(modalText.cancel)}">${escapeHtml(modalText.cancel)}</button>
                <button type="button" class="om-button border submit" data-feedback-action="submit" aria-label="${escapeHtml(modalText.submit)}">
                    ${Icons?.send}
                    <span>${escapeHtml(modalText.submit)}</span>
                </button>
            </div>
        </footer>
    `;

    const textarea = modal.querySelector('.feedback-modal-textarea');
    const charCount = modal.querySelector('.feedback-modal-char-count');
    const closeBtn = modal.querySelector('[data-feedback-action="close"]');
    const cancelBtn = modal.querySelector('[data-feedback-action="cancel"]');
    const submitBtn = modal.querySelector('[data-feedback-action="submit"]');

    updateFeedbackModalCharCount(charCount, 0);
    focusFeedbackModalTarget(textarea);

    textarea.addEventListener('input', () => {
        updateFeedbackModalCharCount(charCount, textarea.value.length);
    });

    closeBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        closeFeedbackTooltip();
    });

    cancelBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        closeFeedbackTooltip();
    });

    submitBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const comment = textarea.value.trim() || null;
        closeFeedbackTooltip({ restoreFocus: false });
        submitFeedbackReaction({ controls, button, messageId, reaction, comment, restoreFocusTarget: button });
    });

    textarea.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            const comment = textarea.value.trim() || null;
            closeFeedbackTooltip({ restoreFocus: false });
            submitFeedbackReaction({ controls, button, messageId, reaction, comment, restoreFocusTarget: button });
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            closeFeedbackTooltip();
        }
    });
}


function closeFeedbackTooltip({ restoreFocus = true } = {}) {
    const existing = document.querySelector('.feedback-modal-overlay');
    if (existing) {
        existing.inert = true;
        existing.setAttribute('aria-hidden', 'true');
        existing.classList.remove('is-visible');
        existing.classList.add('is-closing');
        document.body.classList.remove('modal-open');
        const returnFocus = restoreFocus ? existing._returnFocus : null;
        setTimeout(() => {
            existing.remove();
            if (returnFocus) {
                focusFeedbackModalTarget(returnFocus);
            }
        }, 200);
    }
}


async function submitFeedbackReaction({ controls, button, messageId, reaction, comment, restoreFocusTarget = null }) {
    if (!controls || !button || !messageId || !reaction) {
        return;
    }
    if (controls.dataset.submitting === 'true') {
        return;
    }

    const assistantContainer = controls.closest('.assistant-message-container');
    const persistedMessageId = resolvePersistedAssistantMessageId(assistantContainer, messageId);
    if (!persistedMessageId) {
        if (typeof notifyError === 'function') {
            notifyError(getChatPreviewTranslation('chat_feedback_submit_failed', 'Failed to submit feedback'));
        }
        if (restoreFocusTarget) {
            focusFeedbackModalTarget(restoreFocusTarget);
        }
        return;
    }
    controls.dataset.messageId = persistedMessageId;

    const buttons = Array.from(controls.querySelectorAll('button.assistant-feedback-button'));
    controls.dataset.submitting = 'true';
    buttons.forEach((btn) => {
        btn.disabled = true;
    });

    try {
        const payload = {
            message_id: persistedMessageId,
            reaction,
        };
        if (comment) {
            payload.comment = comment;
        }

        const response = await window.authedFetch(`/api/v1/feedback/react`, {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });

        if (!response.ok) {
            let detail = formatChatPreviewTranslation(
                'chat_feedback_submit_failed_status',
                'Failed to submit feedback ({status})',
                { status: response.status }
            );
            try {
                const respPayload = await response.json();
                if (respPayload?.detail) {
                    detail = typeof window.translateBackendDetail === 'function'
                        ? window.translateBackendDetail(respPayload.detail, respPayload.detail)
                        : respPayload.detail;
                }
            } catch (_) {
                // Ignore JSON parse errors
            }
            if (typeof notifyError === 'function') {
                notifyError(detail);
            }
            return;
        }

        controls.dataset.selectedReaction = reaction;
        if (controls.parentElement) {
            controls.parentElement.dataset.selectedFeedbackReaction = reaction;
        }
        updateAssistantFeedbackVisualState(controls, reaction);
        showAssistantFeedbackSuccess(button);

        if (comment && typeof notifySuccess === 'function') {
            notifySuccess(getChatPreviewTranslation('chat_feedback_submit_with_comment_success', 'Feedback submitted with comment'));
        }
    } catch (error) {
        console.error('Assistant feedback submission failed', error);
        if (typeof notifyError === 'function') {
            notifyError(getChatPreviewTranslation('chat_feedback_submit_failed', 'Failed to submit feedback'));
        }
    } finally {
        controls.dataset.submitting = 'false';
        buttons.forEach((btn) => {
            btn.disabled = false;
        });
        if (restoreFocusTarget) {
            focusFeedbackModalTarget(restoreFocusTarget);
        }
    }
}


function updateAssistantFeedbackVisualState(controls, selectedReaction) {
    if (!controls) {
        return;
    }
    const buttons = controls.querySelectorAll('button[data-reaction]');
    buttons.forEach((btn) => {
        if (!btn.dataset.icon) {
            btn.dataset.icon = btn.innerHTML;
        }
        const isSelected = Boolean(selectedReaction && btn.dataset.reaction === selectedReaction);
        btn.classList.toggle('is-selected', isSelected);
        btn.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
        if (!btn.classList.contains('showing-check') && btn.innerHTML !== btn.dataset.icon) {
            btn.innerHTML = btn.dataset.icon;
        }
    });
}


function showAssistantFeedbackSuccess(button) {
    if (!button) {
        return;
    }
    if (!button.dataset.icon) {
        button.dataset.icon = button.innerHTML;
    }
    if (button._assistantFeedbackTimeout) {
        clearTimeout(button._assistantFeedbackTimeout);
    }
    button.classList.add('showing-check');
    button.innerHTML = Icons?.check || '✓';
    button._assistantFeedbackTimeout = setTimeout(() => {
        button.classList.remove('showing-check');
        button.innerHTML = button.dataset.icon || '';
    }, 2000);
}

