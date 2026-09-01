(function () {
    'use strict';

    const ASSISTANT_ATTACHMENT_FIELDS = ['images', 'videos', 'audios', 'documents'];

    function splitThinkingSegments(text) {
        if (text === null || text === undefined) {
            return [];
        }

        const normalized = String(text).replace(/\r\n/g, '\n');
        const titleRegex = /\*\*([^\n*][^*]*?)\*\*(?:\s*\n+|\s+(?=[A-Z]))/g;
        const segments = [];
        let lastIndex = 0;
        let match;

        while ((match = titleRegex.exec(normalized)) !== null) {
            const titleStart = match.index;
            if (titleStart > lastIndex) {
                const leading = normalized.slice(lastIndex, titleStart).trim();
                if (leading) {
                    segments.push(leading);
                }
            }
            lastIndex = titleStart;
        }

        if (lastIndex < normalized.length) {
            const trailing = normalized.slice(lastIndex).trim();
            if (trailing) {
                segments.push(trailing);
            }
        }

        if (!segments.length) {
            const trimmed = normalized.trim();
            return trimmed ? [trimmed] : [];
        }

        return segments;
    }

    function resolveAssistantMessageTerminalState(message) {
        const messageMeta = message?.meta && typeof message.meta === 'object'
            ? message.meta
            : null;
        const finalBlock = Array.isArray(message?.content) && message.content.length
            ? message.content[message.content.length - 1]
            : null;
        const finalBlockMeta = finalBlock?.meta && typeof finalBlock.meta === 'object'
            ? finalBlock.meta
            : null;
        return [messageMeta, finalBlockMeta].some((meta) => {
            if (!meta) return false;
            const explicitState = String(meta.assistant_terminal_state || '').trim().toLowerCase();
            if (explicitState === 'cancelled' || explicitState === 'canceled') {
                return true;
            }
            const status = String(meta.status || '').trim().toLowerCase();
            return (status === 'cancelled' || status === 'canceled') && Boolean(meta.timestamp);
        }) ? 'cancelled' : '';
    }

    function isCanvasAttachmentSource(source) {
        const toolName = String(source?.meta?.tool_name || source?.meta?.name || '').trim().toLowerCase();
        return toolName === 'canvas';
    }

    function latexPdfFileIdsFromMeta(meta) {
        if (!meta || typeof meta !== 'object' || !meta.latex_pdf) {
            return [];
        }
        return [
            meta.file_id || meta.fileId || meta.pdf_file_id || meta.pdfFileId || '',
            meta.source_file_id || meta.sourceFileId || '',
        ]
            .map((item) => String(item || '').trim())
            .filter(Boolean);
    }

    function renderAssistantAttachmentsForSource(messageId, source, renderedIds) {
        if (!source || typeof renderAssistantFileBlock !== 'function') {
            return false;
        }
        const canvasWidget = window.canvasMarkdownWidget;
        const sourceIsCanvas = isCanvasAttachmentSource(source);
        let appended = false;

        ASSISTANT_ATTACHMENT_FIELDS.forEach((field) => {
            const files = source[field];
            if (!Array.isArray(files) || !files.length) {
                return;
            }

            files.forEach((file) => {
                if (!file) {
                    return;
                }
                const fileId = typeof file === 'string' ? file : (file.id || file.file_id);
                if (!fileId || renderedIds.has(fileId)) {
                    return;
                }
                if (window.latexPdfWidget?.isLatexPdfFile?.(fileId)) {
                    renderedIds.add(fileId);
                    return;
                }

                if (typeof file === 'string') {
                    renderAssistantFileBlock(messageId, fileId, null, {
                        sourceMeta: source?.meta,
                        sourceIsCanvas,
                    });
                    renderedIds.add(fileId);
                    appended = true;
                    return;
                }

                const fileMeta = file?.meta || {};
                const fileType = file?.file_type || file?.mime_type || fileMeta?.file_type || fileMeta?.mime_type || '';
                const fileName = fileMeta?.original_filename || fileMeta?.original_name || file?.original_filename || file?.original_name || file?.file_name || '';
                const isCanvasFile = Boolean(
                    sourceIsCanvas
                    || file?.meta?.canvas === true
                    || canvasWidget?.isCanvasFile?.(fileId)
                    || canvasWidget?.isLikelyCanvasFile?.(fileMeta, fileType, fileName)
                );

                if (isCanvasFile && typeof canvasWidget?.renderSavedWidgetFromFile === 'function') {
                    const rendered = canvasWidget.renderSavedWidgetFromFile({
                        messageId,
                        fileId,
                        fileName: fileName || 'canvas.md',
                        contentType: fileType,
                        pageCount: file?.page_count || fileMeta?.page_count || 1,
                    });
                    if (rendered) {
                        renderedIds.add(fileId);
                        appended = true;
                        return;
                    }
                }

                renderAssistantFileBlock(messageId, fileId, file, {
                    sourceMeta: source?.meta,
                    sourceIsCanvas,
                });
                renderedIds.add(fileId);
                appended = true;
            });
        });

        return appended;
    }

    function withChatAreaContainer(container, fn) {
        const target = container || document.getElementById('chatAreaContainer');
        if (!target || typeof fn !== 'function') {
            return;
        }

        const original = document.getElementById('chatAreaContainer');
        if (target === original) {
            fn();
            return;
        }

        const targetOriginalId = target.id;
        if (original) {
            original.id = '__chatTranscriptRenderer_original';
        }
        target.id = 'chatAreaContainer';

        try {
            fn();
        } finally {
            target.id = targetOriginalId || '';
            if (original) {
                original.id = 'chatAreaContainer';
            }
        }
    }

    function renderChatTranscript(messages, options = {}) {
        const safeMessages = Array.isArray(messages) ? messages : [];
        const container = options.container || document.getElementById('chatAreaContainer');
        if (!container) {
            return;
        }
        const keepTrailingAssistantStreaming = options.keepTrailingAssistantStreaming === true;
        const trailingAssistantMessageId = keepTrailingAssistantStreaming
            ? (() => {
                for (let index = safeMessages.length - 1; index >= 0; index -= 1) {
                    const candidate = safeMessages[index];
                    if (candidate?.role !== 'assistant') {
                        continue;
                    }
                    return String(candidate.id || '').trim();
                }
                return '';
            })()
            : '';

        if (options.clearContainer !== false) {
            container.innerHTML = '';
        }

        const trackAssistantVersions = options.trackAssistantVersions !== false;

        const hasReadOnlyOverride = Object.prototype.hasOwnProperty.call(options, 'readOnly');
        const hadExistingReadOnlyFlag = Object.prototype.hasOwnProperty.call(window, 'chatViewReadOnly');
        const previousReadOnly = window.chatViewReadOnly;

        if (hasReadOnlyOverride) {
            window.chatViewReadOnly = Boolean(options.readOnly);
        }

        try {
            withChatAreaContainer(container, () => {
                if (trackAssistantVersions && typeof window.clearAssistantMessageVersions === 'function') {
                    window.clearAssistantMessageVersions();
                }

            const assistantVersionsByRef = new Map();
            safeMessages.forEach((msg) => {
                if (msg.role === 'assistant' && msg.reference_id) {
                    if (!assistantVersionsByRef.has(msg.reference_id)) {
                        assistantVersionsByRef.set(msg.reference_id, { messages: [], maxRetryCount: 0 });
                    }
                    const group = assistantVersionsByRef.get(msg.reference_id);
                    group.messages.push(msg);
                    const retryCount = msg.retry_count || 0;
                    if (retryCount > group.maxRetryCount) {
                        group.maxRetryCount = retryCount;
                    }
                }
            });

            let messageId = '';
            let assistantContentCount = 0;
            let assistantReasoningCount = 0;
            let lastAppendedMessageType = '';
            let metadataToAppend = null;
            let lastReasoningTime = 0;
            let currentRegenerationInfo = null;

            const finalizeLastAssistantThinking = (targetMessageId, reasoningTime) => {
                if (!targetMessageId) {
                    return;
                }
                try {
                    const assistantContainer = document.getElementById('a-' + targetMessageId);
                    if (!assistantContainer) {
                        return;
                    }
                    const assistantThinking = assistantContainer.querySelectorAll('.assistant-thinking');
                    const lastAssistantThinking = assistantThinking.length ? assistantThinking[assistantThinking.length - 1] : null;
                    if (lastAssistantThinking) {
                        if (typeof finalizeThinkingBlockHeader === 'function') {
                            finalizeThinkingBlockHeader(lastAssistantThinking, reasoningTime);
                        }
                    }
                } catch (_) {
                    // Ignore renderer finalization failures.
                }
            };

            const assistantContainerHasRenderableContent = () => {
                if (!messageId) {
                    return false;
                }
                const assistantContainer = document.getElementById('a-' + messageId);
                if (!assistantContainer) {
                    return false;
                }
                return Array.from(assistantContainer.children).some((child) => {
                    if (!child || !(child instanceof HTMLElement)) {
                        return false;
                    }
                    if (child.classList.contains('assistant-message-list')) {
                        return false;
                    }
                    if (child.classList.contains('sr-only')) {
                        return false;
                    }
                    if (
                        child.classList.contains('chat-message-sr-label')
                        || child.classList.contains('chat-message-sr-status')
                    ) {
                        return false;
                    }
                    return true;
                });
            };

            const finalizeAssistantTurnIfNeeded = () => {
                if (!assistantContainerHasRenderableContent()) {
                    metadataToAppend = null;
                    currentRegenerationInfo = null;
                    lastReasoningTime = 0;
                    return;
                }
                if (keepTrailingAssistantStreaming && messageId === trailingAssistantMessageId) {
                    const assistantContainer = document.getElementById('a-' + messageId);
                    if (assistantContainer) {
                        assistantContainer.dataset.isStreaming = 'true';
                        assistantContainer.dataset.announceStreaming = 'false';
                        assistantContainer.querySelector('.assistant-message-list')?.remove();
                        if (typeof applyAssistantMessageAccessibility === 'function') {
                            applyAssistantMessageAccessibility(assistantContainer, {
                                messageId,
                                streaming: true,
                                hasError: assistantContainer.dataset.hasError === 'true',
                            });
                        }
                    }
                    currentRegenerationInfo = null;
                    metadataToAppend = null;
                    lastReasoningTime = 0;
                    return;
                }
                if (typeof appendAssistantDone === 'function') {
                    appendAssistantDone(messageId, metadataToAppend, currentRegenerationInfo);
                }
                finalizeLastAssistantThinking(messageId, lastReasoningTime);
                currentRegenerationInfo = null;
                metadataToAppend = null;
                lastReasoningTime = 0;
            };

            const collectFilesFromBlock = (block) => {
                if (!block) {
                    return [];
                }
                return [
                    ...(Array.isArray(block.images) ? block.images : []),
                    ...(Array.isArray(block.videos) ? block.videos : []),
                    ...(Array.isArray(block.audios) ? block.audios : []),
                    ...(Array.isArray(block.documents) ? block.documents : []),
                ];
            };

            const collectFilesFromBlocks = (blocks) => {
                if (!Array.isArray(blocks) || !blocks.length) {
                    return [];
                }
                const seen = new Set();
                const files = [];
                blocks.forEach((block) => {
                    collectFilesFromBlock(block).forEach((file) => {
                        const fileId = typeof file === 'string' ? file : (file?.id || file?.file_id);
                        if (fileId) {
                            if (seen.has(fileId)) {
                                return;
                            }
                            seen.add(fileId);
                        }
                        files.push(file);
                    });
                });
                return files;
            };

            const collectChatReferencesFromBlock = (block) => {
                if (!block || !Array.isArray(block.chat_references)) {
                    return [];
                }
                return block.chat_references
                    .map((item) => {
                        const chatId = String(item?.chat_id ?? item?.id ?? '').trim();
                        if (!chatId) {
                            return null;
                        }
                        return {
                            chat_id: chatId,
                            title: item?.title || 'Untitled chat',
                            last_updated_at: item?.last_updated_at || null,
                            snippet: item?.snippet || '',
                            message_count: Number(item?.message_count || 0) || 0,
                            estimated_chars: Number(item?.estimated_chars || 0) || 0,
                        };
                    })
                    .filter(Boolean);
            };

            const collectChatReferencesFromBlocks = (blocks) => {
                if (!Array.isArray(blocks) || !blocks.length) {
                    return [];
                }
                const seen = new Set();
                const references = [];
                blocks.forEach((block) => {
                    collectChatReferencesFromBlock(block).forEach((item) => {
                        if (seen.has(item.chat_id)) {
                            return;
                        }
                        seen.add(item.chat_id);
                        references.push(item);
                    });
                });
                return references;
            };

            safeMessages.forEach((message) => {
                const contentIsArray = Array.isArray(message.content);
                const renderedAttachmentIds = new Set();

                if (message.role === 'user') {
                    finalizeAssistantTurnIfNeeded();
                    lastReasoningTime = 0;
                    messageId = message.id;

                    let userText = '';
                    let files = [];
                    let chatReferences = [];

                    if (contentIsArray) {
                        const userBlock = message.content.find((b) => b.type === 'user') || message.content.find((b) => b.type === 'content');
                        if (userBlock) {
                            userText = String(userBlock.content ?? '');
                            files = collectFilesFromBlock(userBlock);
                            chatReferences = collectChatReferencesFromBlock(userBlock);
                        }
                        if (!files.length) {
                            files = collectFilesFromBlocks(message.content);
                        }
                        if (!chatReferences.length) {
                            chatReferences = collectChatReferencesFromBlocks(message.content);
                        }
                    } else {
                        userText = String(message.content ?? '');
                        files = collectFilesFromBlock(message);
                        chatReferences = collectChatReferencesFromBlock(message);
                    }

                    if (typeof appendUserContent === 'function') {
                        appendUserContent(message.id, userText, files, chatReferences);
                    }

                    if (message.bookmarked && typeof window.updateUserMessageBookmarkState === 'function') {
                        window.updateUserMessageBookmarkState(message.id, true);
                    }

                    if (typeof appendAssistantContainer === 'function') {
                        appendAssistantContainer(messageId);
                    }
                    assistantContentCount = 0;
                    assistantReasoningCount = 0;
                    lastAppendedMessageType = '';
                    return;
                }

                if (message.role === 'assistant') {
                    const referenceId = message.reference_id;
                    const retryCount = message.retry_count || 0;
                    const versionGroup = referenceId ? assistantVersionsByRef.get(referenceId) : null;
                    const totalVersions = versionGroup ? versionGroup.messages.length : 1;
                    const maxRetryCount = versionGroup ? versionGroup.maxRetryCount : 0;
                    const isLatestVersion = retryCount === maxRetryCount;

                    if (retryCount > 0) {
                        finalizeAssistantTurnIfNeeded();
                        if (typeof appendAssistantContainer === 'function') {
                            appendAssistantContainer(message.id);
                        }
                        messageId = message.id;
                        assistantContentCount = 0;
                        assistantReasoningCount = 0;
                        lastAppendedMessageType = '';
                    }

                    const assistantContainer = document.getElementById('a-' + messageId);
                    if (assistantContainer) {
                        assistantContainer.dataset.assistantMessageId = message.id;
                        assistantContainer.dataset.retryCount = String(retryCount);
                        assistantContainer.dataset.totalVersions = String(totalVersions);
                        assistantContainer.dataset.referenceId = referenceId || '';
                        assistantContainer.dataset.isLatestVersion = isLatestVersion ? 'true' : 'false';
                        assistantContainer.dataset.bookmarked = message.bookmarked ? 'true' : 'false';
                        const terminalState = resolveAssistantMessageTerminalState(message);
                        if (terminalState) {
                            assistantContainer.dataset.assistantTerminalState = terminalState;
                        } else {
                            delete assistantContainer.dataset.assistantTerminalState;
                        }

                        if (contentIsArray) {
                            const allCitations = [];
                            message.content.forEach((block) => {
                                if (block.type === 'tool_call_result' && block.meta && block.meta.citations) {
                                    allCitations.push(...block.meta.citations);
                                }
                            });
                            if (allCitations.length > 0) {
                                try {
                                    assistantContainer.dataset.citations = JSON.stringify(allCitations);
                                } catch (_) {
                                    // ignore citation serialization errors
                                }
                            }
                        }

                        if (!isLatestVersion) {
                            assistantContainer.style.display = 'none';
                            assistantContainer.dataset.hidden = 'true';
                        }
                    }

                    if (trackAssistantVersions && typeof window.registerAssistantMessageVersion === 'function' && referenceId) {
                        window.registerAssistantMessageVersion(referenceId, {
                            id: message.id,
                            retry_count: retryCount,
                            reference_id: referenceId,
                        });
                    }

                    currentRegenerationInfo = {
                        retry_count: retryCount,
                        total_versions: totalVersions,
                        reference_id: referenceId,
                        is_latest_version: isLatestVersion,
                    };

                    if (contentIsArray) {
                        let accumulatedReasoningTime = 0;
                        const deepResearchActivityByRunId = new Map();

                        // Deep Research keeps its display-only replay snapshot on
                        // the persisted tool-result block. Associate it with the
                        // following widget by run ID without exposing tool-result
                        // content or feeding the activity back to the model.
                        message.content.forEach((block) => {
                            const meta = block?.type === 'tool_call_result'
                                && block.meta && typeof block.meta === 'object'
                                ? block.meta
                                : null;
                            const runId = String(meta?.run_id || '').trim();
                            if (
                                meta?.deep_research === true
                                && runId
                                && meta.deep_research_activity
                                && typeof meta.deep_research_activity === 'object'
                            ) {
                                deepResearchActivityByRunId.set(
                                    runId,
                                    meta.deep_research_activity,
                                );
                            }
                        });

                        message.content.forEach((block) => {
                            if (block.type === 'reasoning') {
                                const reasoningText = String(block.content ?? '');
                                const reasoningTime = block.meta?.reasoning_time ?? 0;
                                accumulatedReasoningTime += reasoningTime;
                                lastReasoningTime = accumulatedReasoningTime;

                                if (reasoningText) {
                                    const thinkingChunks = splitThinkingSegments(reasoningText);
                                    thinkingChunks.forEach((chunk) => {
                                        if (typeof appendAssistantReasoning === 'function') {
                                            assistantReasoningCount = appendAssistantReasoning(
                                                messageId,
                                                chunk,
                                                lastAppendedMessageType,
                                                assistantReasoningCount
                                            );
                                            lastAppendedMessageType = 'r';
                                        }
                                    });
                                } else if (lastAppendedMessageType !== 'r' && typeof appendAssistantReasoning === 'function') {
                                    assistantReasoningCount = appendAssistantReasoning(
                                        messageId,
                                        '',
                                        lastAppendedMessageType,
                                        assistantReasoningCount
                                    );
                                    lastAppendedMessageType = 'r';
                                }
                            } else if (block.type === 'content') {
                                const contentText = String(block.content ?? '');
                                if (contentText && typeof appendAssistantContent === 'function') {
                                    assistantContentCount = appendAssistantContent(
                                        messageId,
                                        contentText,
                                        lastAppendedMessageType,
                                        assistantContentCount,
                                        accumulatedReasoningTime > 0 ? accumulatedReasoningTime : null,
                                        assistantReasoningCount,
                                        block.meta ?? null
                                    );
                                    metadataToAppend = block.meta ?? null;
                                    lastAppendedMessageType = 'c';
                                    accumulatedReasoningTime = 0;
                                }
                            } else if (block.type === 'tool_call') {
                                const toolContent = String(block.content ?? '');
                                const toolName = block.meta?.tool_name || block.meta?.name || null;
                                // Canonical persisted calls use `arguments`; the
                                // aliases remain supported for imported chats.
                                const toolArgs = block.meta?.arguments
                                    ?? block.meta?.tool_args
                                    ?? block.meta?.args
                                    ?? null;
                                if (typeof appendAssistantTool === 'function') {
                                    assistantReasoningCount = appendAssistantTool(
                                        messageId,
                                        lastAppendedMessageType,
                                        assistantReasoningCount,
                                        toolContent,
                                        toolName,
                                        toolArgs,
                                        block.meta ?? null
                                    );
                                    lastAppendedMessageType = 't';
                                }
                            } else if (block.type === 'tool_call_result') {
                                const meta = block.meta && typeof block.meta === 'object' ? block.meta : {};
                                const toolError = typeof parseToolErrorDescriptorFromResultBlock === 'function'
                                    ? parseToolErrorDescriptorFromResultBlock(block)
                                    : null;
                                if (toolError && typeof applyAssistantToolError === 'function') {
                                    applyAssistantToolError(messageId, toolError, { announce: false });
                                    lastAppendedMessageType = 't';
                                } else if (meta.slide_presentation === true && window.slidePresentationWidget?.renderSlidePresentationResultBlock) {
                                    window.slidePresentationWidget.renderSlidePresentationResultBlock(messageId, meta);
                                    [meta.html_file_id, meta.pptx_file_id, meta.file_id]
                                        .map((value) => String(value || '').trim())
                                        .filter(Boolean)
                                        .forEach((fileId) => renderedAttachmentIds.add(fileId));
                                    lastAppendedMessageType = 'slide_presentation';
                                } else if (meta.subagent?.id && typeof window.renderPersistedSubagentBlock === 'function') {
                                    const rendered = window.renderPersistedSubagentBlock(
                                        messageId,
                                        meta
                                    );
                                    if (rendered) {
                                        lastAppendedMessageType = 'subagent';
                                    }
                                } else if (meta.latex_pdf && window.latexPdfWidget && typeof window.latexPdfWidget.renderLatexPdfResultBlock === 'function') {
                                    window.latexPdfWidget.renderLatexPdfResultBlock(messageId, meta);
                                    latexPdfFileIdsFromMeta(meta).forEach((fileId) => renderedAttachmentIds.add(fileId));
                                    lastAppendedMessageType = 'latex_pdf';
                                }
                            } else if (block.type === 'widget') {
                                const widgetHtml = String(block.content ?? '');
                                const widgetType = block.meta?.widget_type ?? 'unknown';
                                let widgetMeta = block.meta ?? null;
                                if (widgetType === 'deep_research') {
                                    const runId = String(widgetMeta?.tool_result?.run_id || '').trim();
                                    const activity = deepResearchActivityByRunId.get(runId);
                                    if (activity) {
                                        widgetMeta = {
                                            ...(widgetMeta || {}),
                                            deep_research_activity: activity,
                                        };
                                    }
                                }
                                if (block.meta) {
                                    metadataToAppend = widgetMeta;
                                }
                                if (widgetHtml && typeof appendAssistantWidget === 'function') {
                                    // Restored result cards must stay collapsed.
                                    // Automatic opening is reserved for the
                                    // live event that first creates the draft.
                                    appendAssistantWidget(
                                        messageId,
                                        widgetHtml,
                                        widgetType,
                                        lastAppendedMessageType,
                                        widgetMeta,
                                        { autoOpen: false },
                                    );
                                    lastAppendedMessageType = 'wg';
                                }
                            } else if (block.type === 'slide_presentation_result') {
                                let renderedSlideResult = false;
                                if (block.meta && window.slidePresentationWidget && typeof window.slidePresentationWidget.renderSlidePresentationResultBlock === 'function') {
                                    window.slidePresentationWidget.renderSlidePresentationResultBlock(messageId, block.meta);
                                    renderedSlideResult = true;
                                }
                                if (!renderedSlideResult && typeof appendAssistantContent === 'function') {
                                    const title = String(block?.meta?.title || 'Presentation generated');
                                    const slideCount = Number(block?.meta?.slide_count || 0);
                                    const fallbackText = slideCount > 0 ? `${title} (${slideCount} slides)` : title;
                                    assistantContentCount = appendAssistantContent(
                                        messageId,
                                        fallbackText,
                                        lastAppendedMessageType,
                                        assistantContentCount,
                                        null,
                                        assistantReasoningCount,
                                        block.meta ?? null
                                    );
                                }
                                lastAppendedMessageType = 'wg';
                            } else if (block.type === 'slide_presentation_error') {
                                let renderedSlideError = false;
                                if (block.meta && window.slidePresentationWidget && typeof window.slidePresentationWidget.renderSlidePresentationErrorBlock === 'function') {
                                    window.slidePresentationWidget.renderSlidePresentationErrorBlock(messageId, block.meta);
                                    renderedSlideError = true;
                                }
                                if (!renderedSlideError && typeof appendAssistantContent === 'function') {
                                    const phase = String(block?.meta?.phase || '');
                                    const msg = String(block?.meta?.message || 'An error occurred.');
                                    const title = phase === 'rendering' ? 'Rendering failed' : 'Generation failed';
                                    assistantContentCount = appendAssistantContent(
                                        messageId,
                                        `${title}: ${msg}`,
                                        lastAppendedMessageType,
                                        assistantContentCount,
                                        null,
                                        assistantReasoningCount,
                                        block.meta ?? null
                                    );
                                }
                                lastAppendedMessageType = 'wg';
                            } else if (block.type === 'file') {
                                if (renderAssistantAttachmentsForSource(messageId, block, renderedAttachmentIds)) {
                                    lastAppendedMessageType = 'f';
                                }
                            } else if (
                                (block.type === 'share_omission' || block.type === 'shared_tool_output')
                                && typeof window.renderSharedChatPublicationBlock === 'function'
                            ) {
                                if (window.renderSharedChatPublicationBlock(messageId, block)) {
                                    lastAppendedMessageType = block.type === 'share_omission' ? 'share_omission' : 'shared_tool_output';
                                }
                            } else if (!block.type && !block.content && block.meta) {
                                metadataToAppend = block.meta;
                            }

                            if (block.type !== 'file') {
                                renderAssistantAttachmentsForSource(messageId, block, renderedAttachmentIds);
                            }
                        });

                        renderAssistantAttachmentsForSource(messageId, message, renderedAttachmentIds);
                    } else {
                        if (message.thinking) {
                            lastReasoningTime = message.thinking_time ?? message.meta?.thinking_time ?? 0;
                            const thinkingChunks = splitThinkingSegments(message.thinking);
                            thinkingChunks.forEach((chunk) => {
                                if (typeof appendAssistantReasoning === 'function') {
                                    assistantReasoningCount = appendAssistantReasoning(
                                        messageId,
                                        chunk,
                                        lastAppendedMessageType,
                                        assistantReasoningCount
                                    );
                                    lastAppendedMessageType = 'r';
                                }
                            });
                        }
                        if (message.content && typeof appendAssistantContent === 'function') {
                            assistantContentCount = appendAssistantContent(
                                messageId,
                                message.content,
                                lastAppendedMessageType,
                                assistantContentCount,
                                message.thinking_time ?? message.meta?.thinking_time ?? null,
                                assistantReasoningCount,
                                message.meta ?? null
                            );
                            metadataToAppend = message.meta ?? null;
                            lastAppendedMessageType = 'c';
                        }
                    }
                    return;
                }

                if (message.role === 'share_notice') {
                    finalizeAssistantTurnIfNeeded();
                    if (typeof window.renderSharedChatTimelineNotice === 'function') {
                        window.renderSharedChatTimelineNotice(message);
                    }
                    lastReasoningTime = 0;
                    return;
                }

                if (message.role === 'tool' && typeof appendAssistantTool === 'function') {
                    assistantReasoningCount = appendAssistantTool(
                        messageId,
                        lastAppendedMessageType,
                        assistantReasoningCount,
                        message.name,
                        null,
                        null
                    );
                    lastAppendedMessageType = 't';
                }
            });

                finalizeAssistantTurnIfNeeded();
            });
        } finally {
            if (hasReadOnlyOverride) {
                if (hadExistingReadOnlyFlag) {
                    window.chatViewReadOnly = previousReadOnly;
                } else {
                    delete window.chatViewReadOnly;
                }
            }
        }
    }

    window.renderAssistantAttachmentsForSource = renderAssistantAttachmentsForSource;
    window.renderChatTranscript = renderChatTranscript;
    if (typeof window.splitThinkingIntoSteps !== 'function') {
        window.splitThinkingIntoSteps = splitThinkingSegments;
    }
})();
