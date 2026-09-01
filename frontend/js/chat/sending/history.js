function generateUUID() {
    return ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
      (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16)
    );
}

function appendAttachmentFromTile(tile, buckets) {
    if (!tile || !buckets) {
        return;
    }
    const fileId = String(tile.dataset.fileId || '').trim();
    if (!fileId) {
        return;
    }
    const type = String(tile.dataset.fileType || '').toLowerCase();
    if (type.startsWith('image/')) {
        buckets.images.add(fileId);
    } else if (type.startsWith('video/')) {
        buckets.videos.add(fileId);
    } else if (type.startsWith('audio/')) {
        buckets.audios.add(fileId);
    } else {
        buckets.documents.add(fileId);
    }
}

function collectMessageAttachmentBuckets(container) {
    const buckets = {
        images: new Set(),
        videos: new Set(),
        audios: new Set(),
        documents: new Set(),
    };
    if (!container || typeof container.querySelectorAll !== 'function') {
        return buckets;
    }
    container.querySelectorAll('.inline-files-element[data-file-id]').forEach((tile) => {
        appendAttachmentFromTile(tile, buckets);
    });
    return buckets;
}

function applyAttachmentBucketsToMessage(message, buckets) {
    if (!message || !buckets) {
        return;
    }
    const images = Array.from(buckets.images || []);
    const videos = Array.from(buckets.videos || []);
    const audios = Array.from(buckets.audios || []);
    const documents = Array.from(buckets.documents || []);
    if (images.length) message.images = images;
    if (videos.length) message.videos = videos;
    if (audios.length) message.audios = audios;
    if (documents.length) message.documents = documents;
}

function cloneSerializableForTempHistory(value) {
    if (value === null || typeof value === 'undefined') {
        return value;
    }
    try {
        return JSON.parse(JSON.stringify(value));
    } catch (_) {
        if (Array.isArray(value)) {
            return [];
        }
        if (value && typeof value === 'object') {
            return {};
        }
        if (typeof value === 'bigint') {
            return String(value);
        }
        return null;
    }
}

function stringifyTempToolResultPayload(value) {
    if (typeof value === 'string') {
        return value;
    }
    try {
        return JSON.stringify(value);
    } catch (_) {
        return String(value ?? '');
    }
}

function collectAssistantBlocksFromDom(container) {
    const blocks = [];
    if (!container || typeof container.querySelectorAll !== 'function') {
        return blocks;
    }

    Array.from(container.children).forEach((child) => {
        if (child.matches('.assistant-thinking')) {
            const thinkingText = Array.from(child.querySelectorAll('.thinking-step-content'))
                // Thinking steps are rendered Markdown, so recover the raw
                // source for exports instead of serializing the presentation
                // text and losing Markdown delimiters.
                .map((el) => String(el.getAttribute('data-raw-content') || el.textContent || '').trim())
                .filter(Boolean)
                .join('\n\n');
            if (thinkingText) {
                blocks.push({
                    type: 'reasoning',
                    content: thinkingText,
                });
            }
            return;
        }

        if (child.matches('.assistant-message')) {
            const contentText = Array.from(child.querySelectorAll('.assistant-message-content'))
                .map((el) => String(el.getAttribute('data-raw-content') || el.textContent || '').trim())
                .filter(Boolean)
                .join('\n\n');
            if (contentText) {
                blocks.push({
                    type: 'content',
                    content: contentText,
                });
            }
            return;
        }

        if (!child.matches('.assistant-widget')) {
            return;
        }

        const widgetEl = child;
        const storedPayload = widgetEl.__chatWidgetPayload && typeof widgetEl.__chatWidgetPayload === 'object'
            ? widgetEl.__chatWidgetPayload
            : null;
        const widgetType = String(
            storedPayload?.type
            || widgetEl.dataset.widgetType
            || 'unknown'
        ).trim() || 'unknown';
        const widgetHtml = typeof storedPayload?.html === 'string'
            ? storedPayload.html
            : String(widgetEl.innerHTML || '').trim();
        const widgetMeta = storedPayload?.meta && typeof storedPayload.meta === 'object'
            ? cloneSerializableForTempHistory(storedPayload.meta)
            : { widget_type: widgetType };

        if (
            widgetMeta
            && Object.prototype.hasOwnProperty.call(widgetMeta, 'tool_result')
            && widgetMeta.tool_result !== undefined
        ) {
            const toolResultBlock = {
                type: 'tool_call_result',
                content: stringifyTempToolResultPayload(widgetMeta.tool_result),
            };
            const toolResultMeta = {};
            if (typeof widgetMeta.tool_name === 'string' && widgetMeta.tool_name.trim()) {
                toolResultMeta.tool_name = widgetMeta.tool_name.trim();
            }
            if (typeof widgetMeta.tool_call_id === 'string' && widgetMeta.tool_call_id.trim()) {
                toolResultMeta.tool_call_id = widgetMeta.tool_call_id.trim();
            }
            if (typeof widgetMeta.tool_namespace === 'string' && widgetMeta.tool_namespace.trim()) {
                toolResultMeta.tool_namespace = widgetMeta.tool_namespace.trim();
            }
            if (Object.keys(toolResultMeta).length) {
                toolResultBlock.meta = toolResultMeta;
            }
            blocks.push(toolResultBlock);
        }

        if (widgetHtml) {
            blocks.push({
                type: 'widget',
                content: widgetHtml,
                meta: widgetMeta,
            });
        }
    });

    const terminalState = String(container.dataset.assistantTerminalState || '').trim().toLowerCase();
    if ((terminalState === 'cancelled' || terminalState === 'canceled') && blocks.length) {
        const lastBlock = blocks[blocks.length - 1];
        lastBlock.meta = {
            ...(lastBlock.meta && typeof lastBlock.meta === 'object' ? lastBlock.meta : {}),
            status: 'cancelled',
            assistant_terminal_state: 'cancelled',
        };
    }

    return blocks;
}

function collectTemporaryChatHistoryFromDom(sourceContainer = null) {
    const container = sourceContainer && typeof sourceContainer.querySelectorAll === 'function'
        ? sourceContainer
        : document.getElementById('chatAreaContainer');
    if (!container) {
        return [];
    }

    const history = [];
    const children = Array.from(container.children || []);
    children.forEach((child, index) => {
        if (!child || !child.classList) {
            return;
        }

        if (child.classList.contains('user-message-area')) {
            const contentEl = child.querySelector('.user-message-content, [id^="u-"]');
            const rawContent = contentEl?.getAttribute('data-raw-content');
            const textContent = typeof rawContent === 'string'
                ? rawContent
                : String(contentEl?.textContent || '').trim();
            const messageId = String(contentEl?.id || `temp-user-${index}-${generateUUID()}`).replace(/^u-/, '');
            const entry = {
                id: messageId || `temp-user-${index}-${generateUUID()}`,
                role: 'user',
                content: textContent || '',
            };
            applyAttachmentBucketsToMessage(entry, collectMessageAttachmentBuckets(child));
            history.push(entry);
            return;
        }

        if (child.classList.contains('assistant-message-container')) {
            if (child.dataset.hidden === 'true' || child.style.display === 'none') {
                return;
            }
            const blocks = collectAssistantBlocksFromDom(child);
            const hasSerializableContent = blocks.some((block) => {
                if (!block || typeof block !== 'object') {
                    return false;
                }
                return (
                    block.content !== undefined
                    || block.meta !== undefined
                    || block.images
                    || block.videos
                    || block.audios
                    || block.documents
                );
            });
            if (!hasSerializableContent) {
                return;
            }
            const messageId = String(child.id || `temp-assistant-${index}-${generateUUID()}`).replace(/^a-/, '');
            const entry = {
                id: messageId || `temp-assistant-${index}-${generateUUID()}`,
                role: 'assistant',
                content: blocks,
            };
            applyAttachmentBucketsToMessage(entry, collectMessageAttachmentBuckets(child));
            history.push(entry);
        }
    });

    return history;
}

function serializeTemporaryChatHistory(sourceContainer = null) {
    try {
        return JSON.stringify(collectTemporaryChatHistoryFromDom(sourceContainer));
    } catch (_) {
        return '[]';
    }
}

if (typeof window !== 'undefined') {
    window.collectTemporaryChatHistoryFromDom = collectTemporaryChatHistoryFromDom;
    window.serializeTemporaryChatHistory = serializeTemporaryChatHistory;
}

