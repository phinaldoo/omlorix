/**
 * Shared API error helpers used across chat scripts.
 * Loaded before the sending flow, chatBox.js, splitScreen.js, and the message renderers.
 */

function resolveApiErrorMessage(errorData, fallback) {
    const detail = errorData?.detail;
    if (typeof detail === 'string' && detail.trim()) {
        return detail;
    }
    if (detail && typeof detail === 'object') {
        const code = typeof detail.code === 'string' ? detail.code.trim() : '';
        if (code === 'byok_credential_unavailable') {
            return apiErrorT(
                'byok_credential_unavailable',
                'Your saved BYOK credential is unavailable. Re-enter the API key.',
            );
        }
        if (code === 'chat_model_required') {
            return apiErrorT(
                'chat_model_unavailable_message',
                'No model is available for your account. Ask an administrator for access, or add your own model if your account allows it.',
            );
        }
        const message = typeof detail.message === 'string' ? detail.message.trim() : '';
        if (message) {
            return message;
        }
    }
    return fallback;
}

function apiErrorT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function omlorixFormatTranscriptionErrorMessage(
    errorData,
    fallbackMessage,
    fallbackStatusCode,
) {
    const detail = errorData?.detail;
    if (typeof detail === 'string' && detail.trim()) {
        return detail.trim();
    }

    if (detail && typeof detail === 'object') {
        const code = typeof detail.code === 'string' ? detail.code.trim() : '';
        if (code === 'transcription_not_enabled') {
            return apiErrorT(
                'chat_transcription_not_enabled',
                'Transcription is not enabled.',
            );
        }
        const message = typeof detail.message === 'string' ? detail.message.trim() : '';
        const status = typeof detail.status === 'string' ? detail.status.trim() : '';
        const parsedStatusCode = Number(detail.status_code);
        const statusCode = Number.isFinite(parsedStatusCode)
            ? parsedStatusCode
            : (Number.isFinite(Number(fallbackStatusCode)) ? Number(fallbackStatusCode) : null);

        if (message && status) {
            return `${message} (${status})`;
        }
        if (message && statusCode) {
            return `${message} (HTTP ${statusCode})`;
        }
        if (message) {
            return message;
        }
        if (status) {
            return `${fallbackMessage} (${status})`;
        }
        if (statusCode) {
            return `${fallbackMessage} (HTTP ${statusCode})`;
        }
    }

    return fallbackMessage;
}

/**
 * Classify duration-limit responses shared by completed-file transcription.
 *
 * The backend keeps the historic ``user_dictation_rate_limited`` code for
 * compatibility, but an active reservation is not consumed quota. Keeping
 * this distinction in one helper prevents individual composers from replacing
 * the accurate "another dictation is active" response with reset-time copy.
 */
function omlorixClassifyTranscriptionLimit(errorData) {
    const detail = errorData?.detail;
    const normalizedDetail = detail && typeof detail === 'object' ? detail : {};
    const code = typeof normalizedDetail.code === 'string'
        ? normalizedDetail.code.trim()
        : '';
    const reason = typeof normalizedDetail.reason === 'string'
        ? normalizedDetail.reason.trim()
        : '';
    const isDictationInProgress = code === 'user_dictation_in_progress'
        || (
            code === 'user_dictation_rate_limited'
            && reason === 'active_reservation'
        );

    return {
        code,
        reason,
        isDictationInProgress,
        isDictationRateLimit: (
            code === 'user_dictation_rate_limited'
            && !isDictationInProgress
        ),
    };
}

function parseRateLimitErrorContext(errorData, fallbackDetail) {
    const detail = errorData?.detail;
    const normalizedDetail = detail && typeof detail === 'object' ? detail : {};
    const toFiniteNumber = (value) => {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    };

    return {
        code: typeof normalizedDetail.code === 'string' ? normalizedDetail.code.trim() : '',
        blockReason: typeof normalizedDetail.block_reason === 'string' ? normalizedDetail.block_reason.trim() : '',
        message: resolveApiErrorMessage(errorData, fallbackDetail),
        targetType: typeof normalizedDetail.target_type === 'string' ? normalizedDetail.target_type.trim() : 'model',
        modelId: typeof normalizedDetail.model_id === 'string' ? normalizedDetail.model_id.trim() : '',
        modelName: typeof normalizedDetail.model_name === 'string' ? normalizedDetail.model_name.trim() : '',
        toolKey: typeof normalizedDetail.tool_key === 'string' ? normalizedDetail.tool_key.trim() : '',
        toolLabel: typeof normalizedDetail.tool_label === 'string' ? normalizedDetail.tool_label.trim() : '',
        resetsAt: typeof normalizedDetail.resets_at === 'string' ? normalizedDetail.resets_at.trim() : '',
        timeZone: typeof normalizedDetail.timezone === 'string' ? normalizedDetail.timezone.trim() : 'UTC',
        periodLabel: typeof normalizedDetail.period_label === 'string' ? normalizedDetail.period_label.trim() : '',
        quotaUnit: typeof normalizedDetail.quota_unit === 'string' ? normalizedDetail.quota_unit.trim() : 'requests',
        quotaValue: toFiniteNumber(normalizedDetail.quota_value ?? normalizedDetail.max_requests),
        currentUsage: toFiniteNumber(normalizedDetail.current_usage ?? normalizedDetail.current_count),
        maxRequests: toFiniteNumber(normalizedDetail.max_requests),
        currentCount: toFiniteNumber(normalizedDetail.current_count),
    };
}

function parseRateLimitResetTimestamp(isoTimestamp) {
    const raw = String(isoTimestamp || '').trim();
    if (!raw) {
        return '';
    }
    const normalized = /([+-]\d{2}:\d{2}|Z)$/i.test(raw) ? raw : `${raw}Z`;
    const parsed = Date.parse(normalized);
    if (!Number.isFinite(parsed)) {
        return raw;
    }
    return parsed;
}

function formatRateLimitResetLabel(isoTimestamp) {
    const parsed = parseRateLimitResetTimestamp(isoTimestamp);
    if (parsed === '') {
        return '';
    }
    if (typeof parsed !== 'number') {
        return parsed;
    }
    try {
        return new Intl.DateTimeFormat(undefined, {
            dateStyle: 'medium',
            timeStyle: 'short',
        }).format(parsed);
    } catch (_) {
        return new Date(parsed).toLocaleString();
    }
}

function isSupportedTimeZone(timeZone) {
    const normalizedTimeZone = String(timeZone || '').trim();
    if (!normalizedTimeZone) {
        return false;
    }
    try {
        new Intl.DateTimeFormat(undefined, { timeZone: normalizedTimeZone }).format(0);
        return true;
    } catch (_) {
        return false;
    }
}

function formatRateLimitResetLabelInZone(isoTimestamp, timeZone) {
    const parsed = parseRateLimitResetTimestamp(isoTimestamp);
    if (parsed === '') {
        return '';
    }
    if (typeof parsed !== 'number') {
        return parsed;
    }
    const normalizedTimeZone = String(timeZone || '').trim();
    const supportedTimeZone = isSupportedTimeZone(normalizedTimeZone);
    try {
        const formatted = new Intl.DateTimeFormat(undefined, {
            ...(supportedTimeZone ? { timeZone: normalizedTimeZone, timeZoneName: 'short' } : {}),
            dateStyle: 'medium',
            timeStyle: 'short',
        }).format(parsed);
        return supportedTimeZone || !normalizedTimeZone
            ? formatted
            : `${formatted} (${normalizedTimeZone})`;
    } catch (_) {
        const fallback = supportedTimeZone
            ? new Date(parsed).toLocaleString(undefined, { timeZone: normalizedTimeZone })
            : new Date(parsed).toLocaleString();
        return supportedTimeZone || !normalizedTimeZone
            ? fallback
            : `${fallback} (${normalizedTimeZone})`;
    }
}

function buildRateLimitCardCopy(rateLimitContext) {
    const modelName = String(rateLimitContext?.modelName || '').trim();
    const toolLabel = String(rateLimitContext?.toolLabel || rateLimitContext?.toolKey || '').trim();
    const isToolLimit = String(rateLimitContext?.targetType || '').trim().toLowerCase() === 'tool'
        || rateLimitContext?.code === 'user_tool_rate_limited';
    const periodLabel = String(rateLimitContext?.periodLabel || '').trim();
    const quotaUnit = String(rateLimitContext?.quotaUnit || 'requests').trim().toLowerCase();
    const quotaValue = rateLimitContext?.quotaValue;
    const currentUsage = rateLimitContext?.currentUsage;
    const preserveMessage = String(rateLimitContext?.blockReason || '').trim().toLowerCase() === 'in_flight';
    const timeZone = String(rateLimitContext?.timeZone || 'UTC').trim() || 'UTC';
    const resetLabel = formatRateLimitResetLabel(rateLimitContext?.resetsAt);
    const resetLabelInLimitZone = formatRateLimitResetLabelInZone(rateLimitContext?.resetsAt, timeZone);
    const unitLabel = quotaUnit === 'tokens' ? 'tokens' : (quotaUnit === 'invocations' ? 'invocations' : 'requests');
    const hasStructuredLimit = periodLabel && Number.isFinite(quotaValue) && quotaValue > 0;
    const title = isToolLimit
        ? `${toolLabel || 'Tool'} limit reached`
        : modelName
        ? `${modelName} ${quotaUnit === 'tokens' ? 'token' : 'request'} limit reached`
        : 'Rate limit reached';
    let message = String(rateLimitContext?.message || (isToolLimit
        ? 'This tool is currently rate limited for your account.'
        : 'You have exceeded your usage limit for this model.'));
    if (hasStructuredLimit && !preserveMessage) {
        const usageCount = Number.isFinite(currentUsage) && currentUsage > 0 ? currentUsage : quotaValue;
        message = `You have used ${usageCount} of ${quotaValue} ${periodLabel} ${unitLabel} for ${isToolLimit ? (toolLabel || 'this tool') : (modelName || 'this model')}.`;
    }
    const meta = resetLabelInLimitZone
        ? `Resets ${resetLabelInLimitZone} in ${timeZone}.${resetLabel ? ` Your local time: ${resetLabel}.` : ''}`
        : (resetLabel ? `Resets ${resetLabel}.` : '');
    return { title, message, meta };
}

function getDefaultRateLimitContainer() {
    return document.getElementById('chatAreaContainer');
}

function removeRateLimitCards(container) {
    if (!container || typeof container.querySelectorAll !== 'function') {
        return;
    }
    container.querySelectorAll('.chat-rate-limit-card').forEach((card) => {
        card.remove();
    });
}

function isRateLimitErrorPayload(errorData, fallbackDetail) {
    const context = parseRateLimitErrorContext(errorData, fallbackDetail);
    if (context.code === 'user_model_rate_limited' || context.code === 'user_tool_rate_limited') {
        return true;
    }
    const normalized = String(context.message || '').toLowerCase();
    return normalized.includes('rate limit')
        || normalized.includes('request limit')
        || normalized.includes('try switching to a different model');
}

function showRateLimitCard(options = {}) {
    const {
        container = getDefaultRateLimitContainer(),
        anchorElement = null,
        errorData = null,
        fallbackDetail = 'You have exceeded your request limit for this model.',
    } = options;
    const rateLimitContext = parseRateLimitErrorContext(errorData, fallbackDetail);
    if (!container) {
        return null;
    }

    removeRateLimitCards(container);
    const copy = buildRateLimitCardCopy(rateLimitContext);
    const card = document.createElement('div');
    card.className = 'chat-rate-limit-card';
    card.setAttribute('role', 'alert');
    if (rateLimitContext.modelId) {
        card.dataset.modelId = rateLimitContext.modelId;
    }
    if (rateLimitContext.toolKey) {
        card.dataset.toolKey = rateLimitContext.toolKey;
    }
    if (rateLimitContext.code) {
        card.dataset.rateLimitCode = rateLimitContext.code;
    }

    const iconSvg = Icons.clock;

    const icon = document.createElement('div');
    icon.className = 'chat-rate-limit-icon';
    icon.innerHTML = iconSvg;
    card.appendChild(icon);

    const body = document.createElement('div');
    body.className = 'chat-rate-limit-body';

    const title = document.createElement('div');
    title.className = 'chat-rate-limit-title';
    title.textContent = copy.title;
    body.appendChild(title);

    const msg = document.createElement('div');
    msg.className = 'chat-rate-limit-message';
    msg.textContent = copy.message;
    body.appendChild(msg);

    if (copy.meta) {
        const meta = document.createElement('div');
        meta.className = 'chat-rate-limit-meta';
        meta.textContent = copy.meta;
        body.appendChild(meta);
    }

    const tip = document.createElement('div');
    tip.className = 'chat-rate-limit-tip';

    const tipText = document.createElement('span');
    tipText.textContent = apiErrorT(
        'rate_limit_wait_for_reset_tip',
        'Please wait for the limit window to reset before trying again.'
    );
    tip.appendChild(tipText);

    body.appendChild(tip);
    card.appendChild(body);

    if (anchorElement && anchorElement.parentElement === container) {
        anchorElement.insertAdjacentElement('afterend', card);
    } else {
        container.appendChild(card);
    }

    requestAnimationFrame(() => {
        card.classList.add('chat-rate-limit-card--visible');
        const chatArea = container.closest('.chat-area, .split-chat-area');
        if (chatArea) {
            chatArea.scrollTop = chatArea.scrollHeight;
        }
    });

    return card;
}

function restoreChatDraftAfterFailedSend(message) {
    const input = document.getElementById('chatBoxInput');
    if (!input) {
        console.warn('[chat-send] Unable to restore draft: chatBoxInput not found');
        return;
    }
    if (String(input.value || '').trim()) {
        return;
    }
    input.value = String(message || '');
    input.dispatchEvent(new Event('input', { bubbles: true }));
    if (typeof window.focusChatInput === 'function') {
        window.focusChatInput({ defer: false });
    } else {
        try {
            input.focus();
        } catch (_) {}
    }
}
