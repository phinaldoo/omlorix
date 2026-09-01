function isChatViewReadOnly() {
    return Boolean(window.chatViewReadOnly);
}

function getChatFileFetchFn() {
    if (typeof window.chatFileFetch === 'function') {
        return window.chatFileFetch;
    }
    if (typeof window.authedFetch === 'function') {
        return window.authedFetch.bind(window);
    }
    return window.fetch.bind(window);
}

function resolveChatFileDownloadUrl(fileId, options = {}) {
    const normalizedId = String(fileId || '').trim();
    const inline = options?.inline === true;
    if (typeof window.getChatFileDownloadUrl === 'function') {
        const resolved = window.getChatFileDownloadUrl(normalizedId, { inline });
        if (resolved) {
            return resolved;
        }
    }
    const params = new URLSearchParams({ file_id: normalizedId });
    if (inline) {
        params.set('inline', 'true');
    }
    return `/api/v1/files/download?${params.toString()}`;
}

function resolveChatFileMetaUrl(fileId) {
    const normalizedId = String(fileId || '').trim();
    if (typeof window.getChatFileMetaUrl === 'function') {
        const resolved = window.getChatFileMetaUrl(normalizedId);
        if (resolved) {
            return resolved;
        }
    }
    return `/api/v1/files/${encodeURIComponent(normalizedId)}`;
}

function fetchChatFileDownload(fileId, options = {}) {
    const fetchFn = getChatFileFetchFn();
    return fetchFn(resolveChatFileDownloadUrl(fileId, options), { method: 'GET' });
}

function fetchChatFileMeta(fileId) {
    const fetchFn = getChatFileFetchFn();
    return fetchFn(resolveChatFileMetaUrl(fileId), { method: 'GET' });
}

async function downloadChatFileById(fileId, filenameHint = 'download') {
    const response = await fetchChatFileDownload(fileId);
    if (!response.ok) {
        throw new Error(getStreamTextFormatted('chat_download_failed_status', 'Download failed ({status})', {
            status: response.status,
        }));
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filenameHint || 'download';
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
}

/**
 * Apply the shared option 9 presentation to a file that is already part of the
 * chat transcript. Composer and edit-composer attachments never call this
 * helper, which deliberately keeps their existing compact design unchanged.
 */
function enhanceChatTranscriptFileCard(card, file) {
    if (!card || !window.ChatFileCards?.enhance) {
        return card;
    }

    const normalized = normalizeChatFileForPreview(file) || file || {};
    const meta = normalized.meta || {};
    const fileId = normalized.file_id || normalized.id || file?.file_id || file?.id || '';
    const fileName = String(
        meta.original_filename
        || normalized.original_filename
        || normalized.original_name
        || normalized.file_name
        || getStreamText('chat_share_file_default_name', 'attachment')
    );
    const mimeType = normalized.file_type || normalized.mime_type || meta.file_type || meta.mime_type || '';

    return window.ChatFileCards.enhance(card, {
        fileName,
        mimeType,
        downloadLabel: getStreamTextFormatted(
            'deep_research_download_file_aria',
            'Download {name}',
            { name: fileName },
        ),
        downloadTitle: getStreamText('files_preview_download', 'Download'),
        onDownload: () => downloadChatFileById(fileId, fileName),
        onDownloadError: (error) => {
            console.error('Failed to download chat file:', error);
            notifyError?.(getStreamText('files_download_failed', 'Failed to download file'));
        },
    });
}

/**
 * Add a durable assistant-generated file to the chat header's Files menu.
 *
 * Canvas, presentation, LaTeX, and note widgets register richer handlers in
 * their own modules. Generic generated files (including subagent code-execution
 * outputs) use the shared file preview and fall back to an authenticated
 * download when the preview component is unavailable.
 */
function registerGeneratedAssistantFile(fileId, fileData = null, fallbackName = '') {
    const normalizedId = String(fileId || '').trim();
    const dropdown = window.canvasFilesDropdown;
    if (!normalizedId || !dropdown || typeof dropdown.registerFile !== 'function') {
        return false;
    }

    const candidate = normalizeChatFileForPreview({
        ...(fileData || {}),
        file_id: normalizedId,
        id: normalizedId,
    }) || {
        file_id: normalizedId,
        id: normalizedId,
        meta: {},
    };
    const meta = candidate.meta || {};
    const fileName = String(
        meta.original_filename
        || candidate.original_filename
        || candidate.original_name
        || candidate.file_name
        || fallbackName
        || getStreamText('canvas_files_untitled', 'Untitled')
    ).trim();
    const fileType = candidate.file_type || candidate.mime_type || meta.file_type || meta.mime_type || '';
    const previewFile = {
        ...candidate,
        meta: {
            ...meta,
            original_filename: fileName,
        },
    };

    // Rich artifact widgets own their menu entry and open behavior. Avoid
    // replacing those registrations with the generic preview callback.
    if (shouldSkipCanvasAssistantFile({
        fileId: normalizedId,
        meta,
        fileType,
        fileName,
    }) || window.latexPdfWidget?.isLatexPdfFile?.(normalizedId)) {
        return false;
    }

    dropdown.registerFile(normalizedId, fileName, 'file', () => {
        if (typeof FilesPreview !== 'undefined') {
            if (FilesPreview.isOpen && FilesPreview.activeFileId === normalizedId) {
                FilesPreview.close();
                return;
            }
            FilesPreview.open(previewFile).catch((error) => {
                console.error('Failed to open generated file preview', error);
                notifyError?.(getStreamText('files_preview_open_error', 'Failed to open file preview.'));
            });
            return;
        }

        downloadChatFileById(normalizedId, fileName).catch((error) => {
            console.error('Failed to download generated file', error);
            notifyError?.(getStreamText('files_download_failed', 'Failed to download file'));
        });
    });
    return true;
}

function attachPreviewToInlineImage(element, file) {
    attachPreviewToInlineFile(element, file);
}

function attachPreviewToInlineFile(element, file) {
    if (!element) {
        return;
    }

    const normalizedFile = normalizeChatFileForPreview(file);
    if (!normalizedFile || !normalizedFile.file_id) {
        return;
    }

    element.dataset.fileId = normalizedFile.file_id;
    element.dataset.fileType = String(normalizedFile.file_type || '').toLowerCase();
    if (!element.hasAttribute('tabindex')) {
        element.setAttribute('tabindex', '0');
    }
    if (!element.hasAttribute('role')) {
        element.setAttribute('role', 'button');
    }
    const previewName = normalizedFile?.meta?.original_filename
        || normalizedFile?.original_name
        || normalizedFile?.file_name
        || 'attachment';
    const previewActionLabel = typeof FilesPreview === 'undefined'
        ? getChatA11yText('chat_sr_download_attachment', 'Download attachment: {name}', { name: previewName })
        : getChatA11yText('chat_sr_open_attachment_preview', 'Open attachment preview: {name}', { name: previewName });
    element.setAttribute('aria-label', previewActionLabel);
    element.title = previewActionLabel;
    element.classList.add('assistant-inline-image-previewable');

    if (element.dataset.previewBound === 'true') {
        return;
    }
    element.dataset.previewBound = 'true';

    if (typeof FilesPreview === 'undefined') {
        const downloadFromInline = async (event) => {
            if (event) {
                event.preventDefault();
            }
            const suggestedName = normalizedFile?.meta?.original_filename || normalizedFile?.original_name || normalizedFile?.file_name || 'download';
            try {
                await downloadChatFileById(normalizedFile.file_id, suggestedName);
            } catch (error) {
                console.error('Failed to download inline file', error);
                notifyError?.(getStreamText('files_download_failed', 'Failed to download file'));
            }
        };

        element.addEventListener('click', downloadFromInline);
        element.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                void downloadFromInline(event);
            }
        });
        return;
    }

    const openPreview = async (event) => {
        if (event) {
            event.preventDefault();
        }

        if (FilesPreview.isOpen && FilesPreview.activeFileId === normalizedFile.file_id) {
            FilesPreview.close();
            return;
        }

        try {
            await FilesPreview.open(normalizedFile);
        } catch (error) {
            console.error('Failed to open inline image preview', error);
            notifyError?.(getStreamText('files_preview_open_error', 'Failed to open file preview.'));
        }
    };

    element.addEventListener('click', (event) => {
        void openPreview(event);
    });
    element.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
            void openPreview(event);
        }
    });
}

// Canvas and notes stream their large content payloads into dedicated sidebars.
// Keep those arguments out of the compact thinking/tool block to avoid showing
// the same generated document twice and retaining a second large DOM copy.
const TOOL_ARGS_HIDDEN = new Set([
    'image_generation',
    'video_generation',
    'audio_generation',
    'music_generation',
    'canvas',
    'notes',
    // Visualization HTML can approach one megabyte. The rendered artifact is
    // the useful output; duplicating its source in the thinking row wastes
    // memory and can expose a distracting wall of generated markup.
    'create_visualization',
]);
const TOOL_PREVIEW_HIDDEN = new Set(['web_search', 'websearch']);

function shouldHideToolArguments(toolName) {
    return TOOL_ARGS_HIDDEN.has(String(toolName || '').toLowerCase());
}

function shouldHideToolPreview(toolName) {
    return TOOL_PREVIEW_HIDDEN.has(String(toolName || '').toLowerCase());
}

// Get tool config, falling back to default for unknown tools
function getToolConfig(toolName) {
    const normalized = normalizeToolNameForDisplay(toolName);
    return TOOL_HEADER_CONFIG[normalized] || TOOL_HEADER_CONFIG._default;
}

/** Decode tool arguments for display-only operation classification. */
function parseToolActivityArgs(args) {
    if (args && typeof args === 'object' && !Array.isArray(args)) return args;
    if (typeof args !== 'string' || !args.trim()) return null;
    try {
        const parsed = JSON.parse(args);
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
    } catch (_) {
        // Tool deltas are incomplete JSON. The operation fields are small and
        // normally arrive first, so decode only those safe fields while the
        // full artifact body continues streaming outside the chat DOM.
        const typeMatch = args.match(/"(?:type|operation|content_type)"\s*:\s*"([^"]*)"/);
        const fileIdMatch = args.match(/"(?:file_id|fileId|id)"\s*:\s*"([^"]*)"/);
        if (!typeMatch && !fileIdMatch) return null;
        return {
            type: String(typeMatch?.[1] || '').trim().toLowerCase(),
            file_id: String(fileIdMatch?.[1] || '').trim(),
            has_content: /"(?:content|markdown|text)"\s*:/.test(args),
        };
    }
}

/**
 * Retain only the small, non-content fields needed for an artifact activity
 * label. Canvas/Notes bodies remain hidden from the thinking block and DOM.
 */
function getToolActivityArgs(toolName, args) {
    const normalizedName = normalizeToolNameForDisplay(toolName);
    // No visualization argument is needed to classify the activity. Returning
    // null also prevents incomplete streamed JSON from becoming header text.
    if (normalizedName === 'create_visualization') return null;
    if (!['canvas', 'notes'].includes(normalizedName)) return args;
    const parsed = parseToolActivityArgs(args);
    if (!parsed) return null;
    const activityArgs = {
        type: String(parsed.type || parsed.operation || parsed.content_type || '').trim().toLowerCase(),
    };
    if (normalizedName === 'canvas') {
        activityArgs.file_id = String(parsed.file_id || parsed.fileId || parsed.id || '').trim();
        activityArgs.has_content = parsed.has_content === true
            || ['content', 'markdown', 'text'].some((key) => (
                Object.prototype.hasOwnProperty.call(parsed, key)
            ));
    }
    return activityArgs;
}

/** Resolve create/edit/view semantics without retaining artifact content. */
function getArtifactToolOperation(toolName, args) {
    const normalizedName = normalizeToolNameForDisplay(toolName);
    if (!['canvas', 'notes'].includes(normalizedName)) return '';
    const parsed = parseToolActivityArgs(args);
    if (!parsed) return '';
    const declaredType = String(parsed.type || parsed.operation || parsed.content_type || '').trim().toLowerCase();
    if (declaredType === 'view') return 'view';
    if (normalizedName === 'notes') {
        return ['create', 'edit', 'list', 'delete'].includes(declaredType) ? declaredType : '';
    }
    if (String(parsed.file_id || parsed.fileId || parsed.id || '').trim()) return 'edit';
    if (parsed.has_content === true || ['content', 'markdown', 'text'].some((key) => (
        Object.prototype.hasOwnProperty.call(parsed, key)
    ))) return 'create';
    return '';
}

/** Overlay operation-specific wording onto the tool's base header config. */
function getToolActivityConfig(toolName, args) {
    const config = getToolConfig(toolName);
    const operation = getArtifactToolOperation(toolName, args);
    const operationConfig = operation ? config.operations?.[operation] : null;
    return operationConfig ? { ...config, ...operationConfig } : config;
}

// Generate a user-facing display name from the provider-facing tool name.
// MCP tools end with an eight-character routing digest so similarly named tools
// from different servers remain unique. That digest is an implementation detail
// and must not leak into the chat UI.
function formatToolDisplayName(toolName) {
    const normalizedToolName = String(toolName || '').replace(
        /^(mcp_.+)_([0-9a-f]{8})$/i,
        '$1',
    );

    return normalizedToolName
        .split('_')
        .filter(Boolean)
        .map(part => part.toLowerCase() === 'mcp'
            ? 'MCP'
            : part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ') || getStreamText('assistant_tool_generic_name', 'Tool');
}

function getToolDisplayName(config, toolName) {
    if (config.displayNameKey && config.displayName) {
        return getStreamText(config.displayNameKey, config.displayName);
    }
    return config.displayName || formatToolDisplayName(toolName);
}

// Get the header text for a tool during generation
function getToolInProgressText(toolName, args) {
    const config = getToolActivityConfig(toolName, args);
    const displayName = getToolDisplayName(config, toolName);
    
    // Try to get the argument value for display
    let argValue = null;
    if (config.argKey && args) {
        if (typeof args === 'object' && !Array.isArray(args)) {
            argValue = args[config.argKey];
        } else if (typeof args === 'string') {
            try {
                const parsed = JSON.parse(args);
                argValue = parsed[config.argKey];
            } catch (_) {
                // If it's a simple string, use it directly
                argValue = args;
            }
        }
    }
    
    if (argValue && config.inProgressWithArgKey && config.inProgressWithArg) {
        return getStreamTextFormatted(config.inProgressWithArgKey, config.inProgressWithArg, {
            value: argValue,
        });
    }
    
    // Fall back to generic in-progress text
    if (config.inProgressKey === 'assistant_tool_default_in_progress') {
        return getStreamTextFormatted('assistant_tool_running_named', 'Running {name}', {
            name: displayName,
        });
    }
    return getStreamText(config.inProgressKey, config.inProgress);
}

// Get the header text for a tool after completion
function getToolCompletedText(toolName, args) {
    const config = getToolActivityConfig(toolName, args);
    const displayName = getToolDisplayName(config, toolName);
    
    // Try to get the argument value for display
    let argValue = null;
    if (config.argKey && args) {
        if (typeof args === 'object' && !Array.isArray(args)) {
            argValue = args[config.argKey];
        } else if (typeof args === 'string') {
            try {
                const parsed = JSON.parse(args);
                argValue = parsed[config.argKey];
            } catch (_) {
                argValue = args;
            }
        }
    }
    
    if (argValue && config.completedWithArgKey && config.completedWithArg) {
        return getStreamTextFormatted(config.completedWithArgKey, config.completedWithArg, {
            value: argValue,
        });
    }
    
    // Fall back to generic completed text
    if (config.completedKey === 'assistant_tool_default_completed') {
        return getStreamTextFormatted('assistant_tool_ran_named', 'Ran {name}', {
            name: displayName,
        });
    }
    return getStreamText(config.completedKey, config.completed);
}

/**
 * Return a translated failure label for a tool activity.
 *
 * Tool failures use the translated display name so every built-in media tool
 * can share one stable translation key while still producing a specific label
 * such as "Image Generation failed" or "Music Generation failed".
 */
function getToolFailedText(toolName, args) {
    const config = getToolActivityConfig(toolName, args);
    const displayName = getToolDisplayName(config, toolName);
    return getStreamTextFormatted('assistant_tool_failed_named', '{name} failed', {
        name: displayName,
    });
}

// Get final header text based on tool calls in a thinking block
function getThinkingBlockFinalHeader(toolCalls, reasoningTime) {
    if (!toolCalls || toolCalls.length === 0) {
        // Only reasoning, no tool calls
        const roundedSeconds = Math.max(0, Math.round(reasoningTime || 0));
        const durationText = roundedSeconds === 0
            ? getStreamText('assistant_thought_duration_less_than_one_second', 'less than 1 second')
            : getStreamTextFormatted(
                roundedSeconds === 1 ? 'assistant_thought_duration_second_one' : 'assistant_thought_duration_second_other',
                roundedSeconds === 1 ? '{count} second' : '{count} seconds',
                { count: roundedSeconds }
            );
        return getStreamTextFormatted('assistant_thought_for_duration', 'Thought for {duration}', {
            duration: durationText,
        });
    }
    
    if (toolCalls.length === 1) {
        // Single tool call - show tool-specific completed text
        const tool = toolCalls[0];
        return getToolCompletedText(tool.name, tool.args);
    }
    
    // Multiple tool calls
    return getStreamTextFormatted('assistant_tool_performed_actions', 'Performed {count} actions', {
        count: toolCalls.length,
    });
}

// Track tool calls within a thinking container
function addToolCallToThinkingContainer(thinkingContainer, toolName, toolArgs, toolId = '') {
    if (!thinkingContainer) return;
    
    // Initialize tool calls array if not exists
    if (!thinkingContainer._toolCalls) {
        thinkingContainer._toolCalls = [];
    }
    if (toolId) {
        const existing = thinkingContainer._toolCalls.find((call) => call && call.id === toolId);
        if (existing) {
            existing.name = toolName;
            existing.args = toolArgs;
            return;
        }
    }

    thinkingContainer._toolCalls.push({
        id: toolId || '',
        name: toolName,
        args: toolArgs
    });
}

// Get tool calls from a thinking container
function getToolCallsFromThinkingContainer(thinkingContainer) {
    if (!thinkingContainer) return [];
    return thinkingContainer._toolCalls || [];
}

// Update the thinking header to show current activity
function updateThinkingHeaderForActivity(thinkingContainer, activityType, toolName, toolArgs) {
    if (!thinkingContainer) return;
    
    const headerSpan = thinkingContainer.querySelector('.assistant-thinking-title span');
    if (!headerSpan) return;
    
    if (activityType === 'thinking') {
        headerSpan.textContent = getStreamText('chatbox_thinking_button_label', 'Thinking');
        headerSpan.dataset.thinkingType = 'thinking';
        headerSpan.classList.add('assistant-thinking-shimmer');
    } else if (activityType === 'tool') {
        headerSpan.textContent = getToolInProgressText(toolName, toolArgs);
        headerSpan.dataset.thinkingType = 'tool';
        headerSpan.classList.add('assistant-thinking-shimmer');
    }
}

function appendLoading(messageId, assistantReasoningCount) {
    const assistantMessageContainer = document.getElementById('a-' + messageId);
    if (!assistantMessageContainer) {
        return assistantReasoningCount;
    }

    const existing = document.getElementById('at-loading-' + messageId);
    if (existing) {
        return assistantReasoningCount;
    }

    const thinkingContainer = document.createElement('div');
    thinkingContainer.id = 'at-loading-' + messageId;
    thinkingContainer.className = 'assistant-thinking collapsed assistant-thinking-loading';
    thinkingContainer.dataset.reasoningIndex = String(assistantReasoningCount + 1);

    const headerBtn = document.createElement('button');
    headerBtn.type = 'button';
    headerBtn.disabled = true;
    headerBtn.className = 'assistant-thinking-header';
    headerBtn.setAttribute('aria-expanded', 'false');

    const headerTitleDiv = document.createElement('div');
    headerTitleDiv.className = 'assistant-thinking-title';
    const headerTitleSpan = document.createElement('span');
    headerTitleSpan.className = 'assistant-thinking-shimmer';
    headerTitleSpan.dataset.thinkingType = 'loading';
    headerTitleSpan.textContent = getStreamText('assistant_loading', 'Loading...');
    headerTitleDiv.appendChild(headerTitleSpan);
    headerBtn.appendChild(headerTitleDiv);
    thinkingContainer.appendChild(headerBtn);

    const skeleton = document.createElement('div');
    skeleton.className = 'assistant-thinking-loading-skeleton';
    skeleton.innerHTML = '<span></span><span></span><span></span>';
    thinkingContainer.appendChild(skeleton);
    appendBeforeAssistantList(assistantMessageContainer, thinkingContainer);
    return assistantReasoningCount;
}

function removeLoading(messageId) {
    const loadingContainer = document.getElementById('at-loading-' + messageId);
    if (!loadingContainer) {
        return false;
    }
    try {
        loadingContainer.remove();
    } catch (_) {
        if (loadingContainer.parentElement) {
            loadingContainer.parentElement.removeChild(loadingContainer);
        }
    }
    return true;
}

function expandLoading(messageId, assistantReasoningCount) {
    const loadingContainer = document.getElementById('at-loading-' + messageId);
    if (!loadingContainer) {
        return assistantReasoningCount;
    }

    const targetIndex = Math.max(assistantReasoningCount + 1, Number(loadingContainer.dataset.reasoningIndex) || 0);
    loadingContainer.removeAttribute('data-reasoning-index');
    loadingContainer.id = 'at-' + targetIndex + '-' + messageId;
    loadingContainer.classList.remove('assistant-thinking-loading');
    loadingContainer.innerHTML = '';

    const headerBtn = document.createElement('button');
    headerBtn.type = 'button';
    headerBtn.className = 'assistant-thinking-header';
    headerBtn.setAttribute('aria-expanded', 'false');

    const headerTitleDiv = document.createElement('div');
    headerTitleDiv.className = 'assistant-thinking-title';
    const headerTitleSpan = document.createElement('span');
    headerTitleSpan.className = 'assistant-thinking-shimmer';
    headerTitleSpan.dataset.thinkingType = 'thinking';
    headerTitleSpan.textContent = getStreamText('chatbox_thinking_button_label', 'Thinking');
    headerTitleDiv.appendChild(headerTitleSpan);
    headerBtn.appendChild(headerTitleDiv);
    loadingContainer.appendChild(headerBtn);

    try {
        if (typeof toggleThinking === 'function') {
            headerBtn.addEventListener('click', () => toggleThinking(headerBtn));
        } else {
            headerBtn.addEventListener('click', () => {
                loadingContainer.classList.toggle('collapsed');
            });
        }
    } catch (_) {
        // Ignore toggle binding errors
    }

    return targetIndex;
}

function ensureAssistantThinkingBody(thinkingContainer) {
    if (!thinkingContainer) {
        return null;
    }
    const thinkingContent = thinkingContainer.querySelector('.assistant-thinking-content');
    if (!thinkingContent) {
        return null;
    }
    const thinkingBody = thinkingContent.querySelector('.assistant-thinking-body');
    return thinkingBody || null;
}

function ensureAssistantThinkingContent(thinkingContainer) {
    if (!thinkingContainer) {
        return null;
    }
    let thinkingContent = thinkingContainer.querySelector('.assistant-thinking-content');
    if (!thinkingContent) {
        thinkingContent = document.createElement('div');
        thinkingContent.className = 'assistant-thinking-content';
        thinkingContainer.appendChild(thinkingContent);
    }
    return thinkingContent;
}

function ensureInitialThinkingStep(thinkingContainer, messageId, reasoningIndex) {
    const thinkingContent = ensureAssistantThinkingContent(thinkingContainer);
    if (!thinkingContent) {
        return null;
    }
    let thinkingBody = thinkingContent.querySelector('.assistant-thinking-body');
    if (!thinkingBody) {
        thinkingBody = document.createElement('div');
        thinkingBody.className = 'assistant-thinking-body';
        thinkingContent.appendChild(thinkingBody);
    }
    const existingStep = thinkingBody.querySelector('.thinking-step-content');
    if (existingStep) {
        return existingStep;
    }

    const step = document.createElement('div');
    step.className = 'thinking-step';
    const stepContent = document.createElement('div');
    stepContent.className = 'thinking-step-content';
    stepContent.id = 'atc-' + reasoningIndex + '-' + messageId;
    stepContent.setAttribute('data-placeholder', 'true');
    // Keep an explicit raw-value attribute even for an empty placeholder. The
    // visible DOM may later contain rendered Markdown, so it cannot be used as
    // the source of truth for the next streamed reasoning delta.
    setAssistantThinkingContent(stepContent, '');
    step.appendChild(stepContent);
    thinkingBody.appendChild(step);
    return stepContent;
}

/**
 * Return the unrendered Markdown accumulated for one thinking step.
 *
 * Thinking content is rendered into HTML as it streams. Reading
 * `textContent` after that point would return the rendered text and would
 * discard Markdown delimiters needed by the title/step parser.
 */
function getAssistantThinkingRawContent(element) {
    if (!element) {
        return '';
    }

    if (typeof element.getAttribute === 'function') {
        const raw = element.getAttribute('data-raw-content');
        if (raw !== null) {
            return String(raw);
        }
    }

    // The property fallback supports lightweight DOM doubles and preserves
    // compatibility with nodes created before the raw-value attribute existed.
    if (typeof element.__assistantThinkingRawContent === 'string') {
        return element.__assistantThinkingRawContent;
    }

    return typeof element.textContent === 'string' ? element.textContent : '';
}

/**
 * Store raw thinking Markdown and update its sanitized rendered presentation.
 *
 * The normal assistant renderer is intentionally reused so thinking follows
 * the same Markdown setting, URL validation, sanitizer, math support, and
 * accessibility behavior as the final assistant response.
 */
function setAssistantThinkingContent(element, content) {
    if (!element) {
        return;
    }

    const raw = String(content ?? '');
    element.__assistantThinkingRawContent = raw;
    if (typeof element.setAttribute === 'function') {
        element.setAttribute('data-raw-content', raw);
    }

    if (typeof renderAssistantMessageContent === 'function') {
        const streamingContainer = typeof element.closest === 'function'
            ? element.closest('.assistant-message-container')
            : null;
        if (
            streamingContainer?.dataset?.isStreaming === 'true'
            && typeof scheduleDebouncedElementRender === 'function'
        ) {
            // Thinking deltas can be as frequent and as long as answer deltas.
            // Use the same bounded paint cadence instead of reparsing and
            // sanitizing the complete accumulated Markdown for every token.
            scheduleDebouncedElementRender(element, raw);
        } else {
            renderAssistantMessageContent(element, raw);
        }
    } else {
        // This fallback is used by isolated unit tests and during an unusual
        // early-load race before the Markdown renderer has been exposed.
        element.textContent = raw;
    }
}

