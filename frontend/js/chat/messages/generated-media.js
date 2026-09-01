const IMAGE_GEN_TOOL_NAMES = new Set(['image_generation']);
const VIDEO_GEN_TOOL_NAMES = new Set(['video_generation']);
const AUDIO_GEN_TOOL_NAMES = new Set(['audio_generation']);
const MUSIC_GEN_TOOL_NAMES = new Set(['music_generation']);

function isImageGenTool(toolName) {
    return IMAGE_GEN_TOOL_NAMES.has(String(toolName || '').toLowerCase().trim());
}

function isVideoGenTool(toolName) {
    return VIDEO_GEN_TOOL_NAMES.has(String(toolName || '').toLowerCase().trim());
}

function isAudioGenTool(toolName) {
    return AUDIO_GEN_TOOL_NAMES.has(String(toolName || '').toLowerCase().trim());
}

function isMusicGenTool(toolName) {
    return MUSIC_GEN_TOOL_NAMES.has(String(toolName || '').toLowerCase().trim());
}

function insertMediaGenPlaceholder(messageId, mediaType, toolCallId = '', toolName = '') {
    const assistantMessageContainer = document.getElementById('a-' + messageId);
    if (!assistantMessageContainer) return;

    const rawType = String(mediaType || 'image').toLowerCase();
    const type = rawType === 'video'
        ? 'video'
        : (rawType === 'audio'
            ? 'audio'
            : (rawType === 'music' ? 'music' : 'image'));
    const placeholderClass = type === 'video'
        ? 'assistant-video-gen-placeholder'
        : (type === 'audio'
            ? 'assistant-audio-gen-placeholder'
            : (type === 'music' ? 'assistant-music-gen-placeholder' : 'assistant-image-gen-placeholder'));
    const shimmerClass = type === 'video'
        ? 'video-gen-placeholder-shimmer'
        : (type === 'audio'
            ? 'audio-gen-placeholder-shimmer'
            : (type === 'music' ? 'music-gen-placeholder-shimmer' : 'image-gen-placeholder-shimmer'));
    const iconClass = type === 'video'
        ? 'video-gen-placeholder-icon'
        : (type === 'audio'
            ? 'audio-gen-placeholder-icon'
            : (type === 'music' ? 'music-gen-placeholder-icon' : 'image-gen-placeholder-icon'));
    const labelClass = type === 'video'
        ? 'video-gen-placeholder-label'
        : (type === 'audio'
            ? 'audio-gen-placeholder-label'
            : (type === 'music' ? 'music-gen-placeholder-label' : 'image-gen-placeholder-label'));
    const labelText = type === 'video'
        ? getStreamText('assistant_media_generation_video_placeholder', 'Generating video...')
        : (type === 'audio'
            ? getStreamText('assistant_media_generation_audio_placeholder', 'Generating audio...')
            : (type === 'music'
                ? getStreamText('assistant_media_generation_music_placeholder', 'Generating music...')
                : getStreamText('assistant_media_generation_image_placeholder', 'Generating image...')));

    // Don't insert if one already exists for this message
    const existingPlaceholder = assistantMessageContainer.querySelector(`.${placeholderClass}`);
    if (existingPlaceholder) return;

    const placeholder = document.createElement('div');
    placeholder.className = type === 'video'
        ? 'assistant-inline-video assistant-video-gen-placeholder'
        : (type === 'audio'
            ? 'assistant-inline-audio assistant-audio-gen-placeholder'
            : (type === 'music'
                ? 'assistant-inline-audio assistant-music-gen-placeholder'
                : 'assistant-inline-image assistant-image-gen-placeholder'));
    // Retain the canonical tool identity while the provider is working. This
    // lets failure handling update the correct thinking block even when a
    // response contains several tool calls.
    placeholder.dataset.toolCallId = String(toolCallId || '').trim();
    placeholder.dataset.toolName = String(toolName || `${type}_generation`).toLowerCase().trim();
    placeholder.dataset.generationStatus = 'in-progress';

    const shimmerBox = document.createElement('div');
    shimmerBox.className = shimmerClass;

    const iconContainer = document.createElement('div');
    iconContainer.className = iconClass;
    iconContainer.innerHTML = type === 'video'
        ? Icons.video_gen
        : (type === 'audio'
            ? Icons.audio_gen
            : (type === 'music'
                ? Icons.music
                : Icons.image_gen));

    const label = document.createElement('span');
    label.className = labelClass;
    label.textContent = labelText;

    shimmerBox.appendChild(iconContainer);
    shimmerBox.appendChild(label);
    placeholder.appendChild(shimmerBox);

    appendBeforeAssistantList(assistantMessageContainer, placeholder);
}

function normalizeMediaGenPlaceholderType(mediaType) {
    const rawType = String(mediaType || '').toLowerCase().trim();
    if (rawType === 'video') {
        return 'video';
    }
    if (rawType === 'audio') {
        return 'audio';
    }
    if (rawType === 'music') {
        return 'music';
    }
    if (rawType === 'image') {
        return 'image';
    }
    return null;
}

function removeImageGenPlaceholder(messageId) {
    const assistantMessageContainer = document.getElementById('a-' + messageId);
    if (!assistantMessageContainer) return null;
    const placeholder = assistantMessageContainer.querySelector(
        '.assistant-image-gen-placeholder, .assistant-video-gen-placeholder, .assistant-audio-gen-placeholder, .assistant-music-gen-placeholder'
    );
    if (!placeholder) return null;
    return placeholder;
}

function getMediaGenPlaceholderType(messageId) {
    const placeholder = removeImageGenPlaceholder(messageId);
    if (!placeholder) {
        return null;
    }
    if (placeholder.classList.contains('assistant-video-gen-placeholder')) {
        return 'video';
    }
    if (placeholder.classList.contains('assistant-audio-gen-placeholder')) {
        return 'audio';
    }
    if (placeholder.classList.contains('assistant-music-gen-placeholder')) {
        return 'music';
    }
    if (placeholder.classList.contains('assistant-image-gen-placeholder')) {
        return 'image';
    }
    return null;
}

function clearMediaGenPlaceholder(messageId) {
    const placeholder = removeImageGenPlaceholder(messageId);
    if (placeholder && placeholder.parentElement) {
        placeholder.remove();
        return true;
    }
    return false;
}

/** Find the live thinking block associated with a media placeholder. */
function findMediaGenThinkingBlock(messageId, placeholder) {
    const assistantMessageContainer = document.getElementById('a-' + messageId);
    if (!assistantMessageContainer || !placeholder) return null;

    const toolCallId = String(placeholder.dataset.toolCallId || '').trim();
    const toolName = String(placeholder.dataset.toolName || '').toLowerCase().trim();
    const thinkingBlocks = Array.from(assistantMessageContainer.querySelectorAll('.assistant-thinking'));

    // Search newest-first because the active media call is normally in the
    // trailing thinking block. The call id disambiguates repeated media tools.
    for (let index = thinkingBlocks.length - 1; index >= 0; index -= 1) {
        const block = thinkingBlocks[index];
        const calls = getToolCallsFromThinkingContainer(block);
        const matches = calls.some((call) => {
            if (!call) return false;
            const callId = String(call.id || '').trim();
            const callName = String(call.name || '').toLowerCase().trim();
            return toolCallId ? callId === toolCallId : callName === toolName;
        });
        if (matches) return block;
    }

    return thinkingBlocks.length ? thinkingBlocks[thinkingBlocks.length - 1] : null;
}

/**
 * Record that the file event for the active media tool has arrived.
 *
 * This runs synchronously before file metadata is fetched. Stream consumers do
 * not await appendAssistantFile(), so the status prevents a following content
 * event from incorrectly converting a successful generation into a failure.
 */
function markMediaGenPlaceholderCompleted(messageId) {
    const placeholder = removeImageGenPlaceholder(messageId);
    if (!placeholder) return null;
    placeholder.dataset.generationStatus = 'completed';
    const thinkingBlock = findMediaGenThinkingBlock(messageId, placeholder);
    if (thinkingBlock) {
        thinkingBlock.dataset.mediaGenerationStatus = 'completed';
    }
    return placeholder;
}

/** Mark an active media tool as failed and preserve that outcome during finalization. */
function markMediaGenPlaceholderFailed(messageId) {
    const placeholder = removeImageGenPlaceholder(messageId);
    if (!placeholder) return false;

    // A file event is the authoritative success signal. Never let a later
    // content/done event overwrite it while asynchronous metadata loading runs.
    if (placeholder.dataset.generationStatus === 'completed') {
        return false;
    }

    const toolName = String(placeholder.dataset.toolName || '').toLowerCase().trim();
    const thinkingBlock = findMediaGenThinkingBlock(messageId, placeholder);
    if (thinkingBlock) {
        thinkingBlock.dataset.mediaGenerationStatus = 'failed';
        const headerSpan = thinkingBlock.querySelector('.assistant-thinking-title span');
        if (headerSpan) {
            const failureLabel = getToolFailedText(toolName, null);
            thinkingBlock.dataset.mediaGenerationFailureLabel = failureLabel;
            headerSpan.textContent = failureLabel;
            headerSpan.classList.remove('assistant-thinking-shimmer');
            headerSpan.dataset.thinkingType = 'tool-failed';
        }
    }

    clearMediaGenPlaceholder(messageId);
    return true;
}

/**
 * Resolve an unfinished media call before rendering a different tool call.
 * Duplicate announcements for the same tool call are left active.
 */
function transitionMediaGenPlaceholderForToolCall(messageId, toolName = '', toolCallId = '') {
    const placeholder = removeImageGenPlaceholder(messageId);
    if (!placeholder) return false;

    const incomingName = String(toolName || '').toLowerCase().trim();
    const incomingId = String(toolCallId || '').trim();
    const activeName = String(placeholder.dataset.toolName || '').toLowerCase().trim();
    const activeId = String(placeholder.dataset.toolCallId || '').trim();
    const sameCall = incomingId && activeId
        ? incomingId === activeId
        : incomingName === activeName;

    return sameCall ? false : markMediaGenPlaceholderFailed(messageId);
}

function syncMediaGenPlaceholder(messageId, toolName = '', toolCallId = '') {
    const normalizedToolName = String(toolName || '').toLowerCase().trim();
    const nextType = isImageGenTool(normalizedToolName)
        ? 'image'
        : (isVideoGenTool(normalizedToolName)
            ? 'video'
            : (isAudioGenTool(normalizedToolName)
                ? 'audio'
                : (isMusicGenTool(normalizedToolName) ? 'music' : null)));

    if (!nextType) {
        // Tool execution is sequential in the backend. Reaching another tool
        // without receiving the media file event means the media call failed.
        markMediaGenPlaceholderFailed(messageId);
        return null;
    }

    const currentType = normalizeMediaGenPlaceholderType(getMediaGenPlaceholderType(messageId));
    if (currentType === nextType) {
        const currentPlaceholder = removeImageGenPlaceholder(messageId);
        if (currentPlaceholder) {
            currentPlaceholder.dataset.toolCallId = String(toolCallId || currentPlaceholder.dataset.toolCallId || '').trim();
            currentPlaceholder.dataset.toolName = normalizedToolName;
        }
        return nextType;
    }

    markMediaGenPlaceholderFailed(messageId);
    insertMediaGenPlaceholder(messageId, nextType, toolCallId, normalizedToolName);
    return nextType;
}

function clearMediaGenPlaceholderForNonFileEvent(messageId) {
    // Media tools only emit their file event after successful generation and
    // persistence. Any other terminal/follow-up event while the placeholder is
    // active therefore represents a failed or interrupted generation.
    return markMediaGenPlaceholderFailed(messageId);
}

/** Finalize thinking headings without changing the user's expanded state. */
function finalizeThinkingBlocks(assistantMessageContainer) {
    if (!assistantMessageContainer) {
        return;
    }
    const priorThinkingBlocks = assistantMessageContainer.querySelectorAll('.assistant-thinking');
    priorThinkingBlocks.forEach((block) => {
        if (typeof finalizeThinkingBlockHeader === 'function') {
            finalizeThinkingBlockHeader(block);
        } else {
            const headerSpan = block.querySelector('.assistant-thinking-title span');
            if (headerSpan) {
                headerSpan.classList.remove('assistant-thinking-shimmer');
                headerSpan.dataset.thinkingType = 'done';
            }
        }
    });
}

function shouldSkipCanvasAssistantFile({ fileId, meta, fileType, fileName, sourceIsCanvas = false }) {
    if (sourceIsCanvas) {
        return true;
    }
    const widget = window.canvasMarkdownWidget;
    if (!widget) {
        return false;
    }
    if (typeof widget.isCanvasFile === 'function' && widget.isCanvasFile(fileId)) {
        return true;
    }
    if (typeof widget.isLikelyCanvasFile === 'function' && widget.isLikelyCanvasFile(meta || {}, fileType || '', fileName || '')) {
        return true;
    }
    return false;
}

function renderCanvasWidgetForFile({ messageId, fileId, fileData = null, fallbackName = '' }) {
    const widget = window.canvasMarkdownWidget;
    if (!widget || typeof widget.renderSavedWidgetFromFile !== 'function') {
        return false;
    }
    const meta = fileData?.meta || {};
    const fileName =
        meta.original_filename
        || meta.original_name
        || fileData?.original_filename
        || fileData?.original_name
        || fileData?.file_name
        || fallbackName
        || 'canvas.md';
    const fileType = fileData?.file_type || fileData?.mime_type || meta.file_type || meta.mime_type || '';
    const pageCount = fileData?.page_count || meta.page_count || 1;
    return Boolean(widget.renderSavedWidgetFromFile({
        messageId,
        fileId,
        fileName,
        contentType: fileType,
        pageCount,
    }));
}

async function appendAssistantFile(messageId, fileId, last_appended_message_type, fileName = '') {
    if (window.latexPdfWidget?.isLatexPdfFile?.(fileId)) {
        return;
    }

    // Try canvas rendering first so canvas widgets are still attempted even if the message container
    // is not yet mounted at this exact moment.
    if (shouldSkipCanvasAssistantFile({ fileId, fileName })) {
        const renderedCanvas = renderCanvasWidgetForFile({
            messageId,
            fileId,
            fileData: { file_name: fileName, meta: { original_filename: fileName } },
            fallbackName: fileName,
        });
        if (renderedCanvas) {
            return;
        }
    }

    const assistantMessageContainer = document.getElementById('a-' + messageId);
    if (!assistantMessageContainer) {
        return;
    }

    // Capture and mark the placeholder before the first await. A subsequent
    // stream event may be processed while file metadata is loading.
    const existingPlaceholder = markMediaGenPlaceholderCompleted(messageId);

    // Finalize existing thinking headings before rendering file output.
    finalizeThinkingBlocks(assistantMessageContainer);

    // First, fetch file metadata to determine file type
    let fileData = null;
    if (!isChatViewReadOnly()) {
        try {
            const response = await fetchChatFileMeta(fileId);
            if (response.ok) {
                fileData = await response.json();
            } else {
                console.warn('[AssistantFiles] Metadata fetch failed', { fileId, status: response.status });
            }
        } catch (error) {
            console.error('[AssistantFiles] Failed to fetch file metadata', { fileId, error });
        }
    }

    // A chat switch can finish while metadata is in flight. Do not register a
    // stale file in the newly active chat's header or append it to detached DOM.
    if (!assistantMessageContainer.isConnected
        || document.getElementById('a-' + messageId) !== assistantMessageContainer) {
        return;
    }

    // Skip canvas markdown files after metadata fetch
    if (shouldSkipCanvasAssistantFile({
        fileId,
        meta: fileData?.meta,
        fileType: fileData?.file_type || fileData?.mime_type,
        fileName: fileData?.original_filename || fileName,
    })) {
        const renderedCanvas = renderCanvasWidgetForFile({
            messageId,
            fileId,
            fileData,
            fallbackName: fileName,
        });
        if (renderedCanvas) {
            return;
        }
    }

    const meta = fileData?.meta || {};
    const originalName = meta.original_filename || (fileName && fileName.trim() ? fileName.trim() : 'Rendered Image');
    const fileSize = fileData?.file_size || 0;
    // Generated SVGs are occasionally returned as a generic binary or XML
    // response. The filename remains reliable in that case, so normalize the
    // type before choosing the renderer and before exposing it to CSS.
    const fileType = resolveAssistantFileType(
        fileData?.file_type || fileData?.mime_type || meta.file_type || meta.mime_type,
        originalName,
    ) || 'image/png';
    
    // Prepare normalized file data
    const normalizedFileData = {
        file_id: fileId,
        id: fileId,
        file_type: fileType,
        file_size: fileSize,
        original_filename: originalName,
        original_name: originalName,
        meta: {
            ...meta,
            original_filename: originalName,
            mime_type: fileType,
            file_size: fileSize,
            origin: 'assistant',
        },
    };

    registerGeneratedAssistantFile(fileId, normalizedFileData, originalName);
    
    // Check if this is a displayable image type
    if (isDisplayableImageType(fileType)) {
        // Render as inline image with download overlay
        const imageWrapper = createAssistantInlineImage(fileId, normalizedFileData, () => {
            // On error, replace with file element fallback
            const fallbackWrapper = createAssistantFileFallback(fileId, normalizedFileData);
            console.warn('[AssistantFiles] Image load failed, falling back to file element', { fileId });
            if (imageWrapper.parentElement) {
                imageWrapper.parentElement.replaceChild(fallbackWrapper, imageWrapper);
            }
        });

        if (existingPlaceholder && existingPlaceholder.parentElement) {
            // Smooth transition: replace placeholder with real image
            existingPlaceholder.classList.add('assistant-image-gen-placeholder-fade-out');
            existingPlaceholder.parentElement.replaceChild(imageWrapper, existingPlaceholder);
        } else {
            appendBeforeAssistantList(assistantMessageContainer, imageWrapper);
        }
    } else if (isDisplayableVideoType(fileType)) {
        const videoWrapper = createAssistantInlineVideo(fileId, normalizedFileData, () => {
            const fallbackWrapper = createAssistantFileFallback(fileId, normalizedFileData);
            if (videoWrapper.parentElement) {
                videoWrapper.parentElement.replaceChild(fallbackWrapper, videoWrapper);
            }
        });

        if (existingPlaceholder && existingPlaceholder.parentElement) {
            existingPlaceholder.classList.add('assistant-image-gen-placeholder-fade-out');
            existingPlaceholder.parentElement.replaceChild(videoWrapper, existingPlaceholder);
        } else {
            appendBeforeAssistantList(assistantMessageContainer, videoWrapper);
        }
    } else if (isDisplayableAudioType(fileType)) {
        const audioWrapper = createAssistantInlineAudio(fileId, normalizedFileData, () => {
            const fallbackWrapper = createAssistantFileFallback(fileId, normalizedFileData);
            if (audioWrapper.parentElement) {
                audioWrapper.parentElement.replaceChild(fallbackWrapper, audioWrapper);
            }
        }, { source: 'assistant' });
        if (existingPlaceholder && existingPlaceholder.parentElement) {
            existingPlaceholder.classList.add('assistant-image-gen-placeholder-fade-out');
            existingPlaceholder.parentElement.replaceChild(audioWrapper, existingPlaceholder);
        } else {
            appendBeforeAssistantList(assistantMessageContainer, audioWrapper);
        }
    } else {
        // Non-image files: render as file element
        const fileWrapper = createAssistantFileFallback(fileId, normalizedFileData);
        if (existingPlaceholder && existingPlaceholder.parentElement) {
            existingPlaceholder.parentElement.replaceChild(fileWrapper, existingPlaceholder);
        } else {
            appendBeforeAssistantList(assistantMessageContainer, fileWrapper);
        }
    }

    refreshUnsupportedFileWarningsFromState();
}

function formatFileSizeForDisplay(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    const k = 1024;
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    const size = (bytes / Math.pow(k, i)).toFixed(2);
    return `${size} ${units[i]}`;
}

/**
 * Resolve the MIME type used by assistant-file renderers.
 *
 * SVG generators and storage providers do not always preserve image/svg+xml;
 * common fallbacks include application/octet-stream, application/xml, and an
 * empty type. A .svg filename is sufficient to render the file through an
 * <img>, which keeps active SVG content isolated from the page document.
 */
function resolveAssistantFileType(fileType, fileName = '') {
    const normalizedType = String(fileType || '').split(';')[0].trim().toLowerCase();
    const normalizedName = String(fileName || '').trim().toLowerCase();
    if (normalizedName.endsWith('.svg')) {
        return 'image/svg+xml';
    }
    return normalizedType;
}

function isDisplayableImageType(fileType) {
    const displayableTypes = [
        'image/avif',
        'image/png',
        'image/jpeg',
        'image/jpg',
        'image/gif',
        'image/webp',
        'image/bmp',
        'image/svg+xml',
    ];
    return displayableTypes.includes(String(fileType || '').toLowerCase());
}

function isDisplayableVideoType(fileType) {
    const displayableTypes = [
        'video/mp4',
        'video/webm',
        'video/ogg',
        'video/quicktime',
        'video/x-matroska',
    ];
    return displayableTypes.includes(String(fileType || '').toLowerCase());
}

function isDisplayableAudioType(fileType) {
    return String(fileType || '').toLowerCase().startsWith('audio/');
}

function createAssistantInlineImage(fileId, fileData, onError) {
    const meta = fileData?.meta || {};
    const originalName = meta.original_filename
        || fileData?.original_filename
        || fileData?.original_name
        || getStreamText('chat_file_default_image_name', 'Image');
    const fileType = fileData?.file_type || meta.file_type || meta.mime_type || 'image/png';
    
    // Create image wrapper with download overlay
    const imageWrapper = document.createElement('div');
    imageWrapper.className = 'assistant-inline-image';
    imageWrapper.dataset.fileId = fileId;
    imageWrapper.dataset.fileType = String(fileType || '').toLowerCase();
    
    // Create the image element
    const img = document.createElement('img');
    img.alt = originalName;
    img.className = 'assistant-inline-image-img';
    img.loading = 'lazy';
    
    // Create download button overlay
    const downloadBtn = document.createElement('button');
    downloadBtn.className = 'assistant-inline-image-download';
    downloadBtn.type = 'button';
    downloadBtn.setAttribute('aria-label', getChatA11yText('chat_sr_download_image', 'Download image: {name}', { name: originalName }));
    downloadBtn.title = getStreamText('chat_download_image', 'Download image');
    downloadBtn.innerHTML = Icons.download;
    
    downloadBtn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        try {
            const response = await fetchChatFileDownload(fileId);
            if (!response.ok) throw new Error(getStreamText('chat_download_failed', 'Download failed'));
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = originalName;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        } catch (err) {
            console.error('Failed to download image:', err);
            if (typeof notifyError === 'function') {
                notifyError(getStreamText('chat_failed_download_image', 'Failed to download image'));
            }
        }
    });
    
    // Handle image load error - fallback to file element
    img.addEventListener('error', () => {
        console.error('[AssistantFiles] Inline image failed to load', { fileId, fileType });
        if (typeof onError === 'function') {
            onError();
        }
    });
    
    // Handle image load success
    img.addEventListener('load', () => {
        imageWrapper.classList.add('loaded');
    });
    
    imageWrapper.appendChild(img);
    imageWrapper.appendChild(downloadBtn);

    attachPreviewToInlineImage(imageWrapper, fileData);

    // Fetch image data with auth headers to avoid 401s
    loadAssistantImageWithAuth(img, fileId);

    return imageWrapper;
}

function createAssistantInlineVideo(fileId, fileData, onError) {
    const meta = fileData?.meta || {};
    const originalName = meta.original_filename
        || fileData?.original_filename
        || fileData?.original_name
        || getStreamText('chat_file_default_video_name', 'Video');
    const fileType = fileData?.file_type || meta.file_type || meta.mime_type || 'video/mp4';

    const videoWrapper = document.createElement('div');
    videoWrapper.className = 'assistant-inline-video';
    videoWrapper.dataset.fileId = fileId;
    videoWrapper.dataset.fileType = String(fileType || '').toLowerCase();

    const video = document.createElement('video');
    video.className = 'assistant-inline-video-player';
    video.setAttribute('aria-label', getChatA11yText('chat_sr_video_attachment', 'Video attachment: {name}', { name: originalName }));
    video.controls = true;
    // Preload enough data so we can render a poster frame before playback
    video.preload = 'auto';
    video.playsInline = true;
    video.setAttribute('playsinline', '');

    const placeholderOverlay = document.createElement('div');
    placeholderOverlay.className = 'assistant-video-gen-placeholder assistant-inline-video-placeholder';
    const shimmerBox = document.createElement('div');
    shimmerBox.className = 'video-gen-placeholder-shimmer';
    const icon = document.createElement('div');
    icon.className = 'video-gen-placeholder-icon';
    icon.innerHTML = Icons.video_gen;
    const label = document.createElement('span');
    label.className = 'video-gen-placeholder-label';
    label.textContent = getStreamText('chat_preparing_preview', 'Preparing preview...');
    shimmerBox.appendChild(icon);
    shimmerBox.appendChild(label);
    placeholderOverlay.appendChild(shimmerBox);

    const hidePlaceholderOverlay = () => {
        if (!placeholderOverlay || placeholderOverlay.dataset.hidden === 'true') {
            return;
        }
        placeholderOverlay.dataset.hidden = 'true';
        placeholderOverlay.classList.add('is-hidden');
        placeholderOverlay.addEventListener('transitionend', () => placeholderOverlay.remove(), { once: true });
    };

    let previewFrameReady = false;
    const markPreviewFrameReady = () => {
        if (previewFrameReady) {
            return;
        }
        previewFrameReady = true;
        hidePlaceholderOverlay();
    };

    const waitForRenderedPreviewFrame = () => {
        if (previewFrameReady) {
            return;
        }
        if (typeof video.requestVideoFrameCallback === 'function') {
            video.requestVideoFrameCallback(() => {
                markPreviewFrameReady();
            });
            return;
        }
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                markPreviewFrameReady();
            });
        });
    };

    setTimeout(() => {
        markPreviewFrameReady();
    }, 2500);

    const primePreviewFrame = () => {
        if (previewFrameReady) {
            return;
        }
        try {
            if (Number.isFinite(video.duration) && video.duration > 0.02 && video.currentTime === 0) {
                video.currentTime = 0.02;
            }
        } catch (_) {
            // Best effort only; some browsers can block early seeks.
        }
    };

    const bufferingOverlay = document.createElement('div');
    bufferingOverlay.className = 'assistant-inline-video-buffering';
    bufferingOverlay.innerHTML = '<span></span><span></span><span></span>';

    const playOverlay = document.createElement('button');
    playOverlay.type = 'button';
    playOverlay.className = 'assistant-inline-video-play-overlay';
    playOverlay.setAttribute('aria-label', getStreamText('chat_play_video', 'Play video'));
    playOverlay.innerHTML = Icons.play;

    playOverlay.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (video.paused) {
            video.play().catch(() => {});
        } else {
            video.pause();
        }
    });

    const setBuffering = (active) => {
        bufferingOverlay.classList.toggle('active', Boolean(active));
    };
    setBuffering(true);

    const setOverlayState = () => {
        videoWrapper.classList.toggle('is-playing', !video.paused && !video.ended);
    };

    video.addEventListener('loadstart', () => setBuffering(true));
    video.addEventListener('waiting', () => setBuffering(true));
    video.addEventListener('stalled', () => setBuffering(true));
    video.addEventListener('canplay', () => setBuffering(false));
    video.addEventListener('playing', () => {
        setBuffering(false);
        videoWrapper.classList.add('loaded');
        setOverlayState();
        markPreviewFrameReady();
    });
    video.addEventListener('pause', setOverlayState);
    video.addEventListener('ended', setOverlayState);
    video.addEventListener('loadedmetadata', () => {
        videoWrapper.classList.add('loaded');
        primePreviewFrame();
    });
    video.addEventListener('loadeddata', () => {
        if (!video.poster) {
            tryCreateVideoPoster(video);
        }
        waitForRenderedPreviewFrame();
    });
    video.addEventListener('seeked', waitForRenderedPreviewFrame);
    video.addEventListener('error', () => {
        hidePlaceholderOverlay();
        if (typeof onError === 'function') {
            onError();
        }
    });

    const downloadBtn = document.createElement('button');
    downloadBtn.className = 'assistant-inline-image-download';
    downloadBtn.type = 'button';
    downloadBtn.setAttribute('aria-label', getChatA11yText('chat_sr_download_video', 'Download video: {name}', { name: originalName }));
    downloadBtn.title = getStreamText('chat_download_video', 'Download video');
    downloadBtn.innerHTML = Icons.download;
    downloadBtn.addEventListener('click', async (event) => {
        event.preventDefault();
        event.stopPropagation();
        try {
            const response = await fetchChatFileDownload(fileId);
            if (!response.ok) throw new Error(getStreamText('chat_download_failed', 'Download failed'));
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = originalName;
            document.body.appendChild(anchor);
            anchor.click();
            document.body.removeChild(anchor);
            URL.revokeObjectURL(url);
        } catch (error) {
            console.error('Failed to download video:', error);
            notifyError?.(getStreamText('chat_failed_download_video', 'Failed to download video'));
        }
    });

    videoWrapper.appendChild(video);
    videoWrapper.appendChild(bufferingOverlay);
    videoWrapper.appendChild(playOverlay);
    videoWrapper.appendChild(downloadBtn);
    videoWrapper.appendChild(placeholderOverlay);

    loadAssistantVideoWithAuth(video, fileId);
    setOverlayState();
    return videoWrapper;
}

function createAssistantInlineAudio(fileId, fileData, onError, options = {}) {
    const meta = fileData?.meta || {};
    const source = options?.source === 'user' ? 'user' : 'assistant';
    const originalName = meta.original_filename
        || fileData?.original_filename
        || fileData?.original_name
        || getStreamText('chat_file_default_audio_name', 'Audio');
    const fileType = fileData?.file_type || meta.file_type || meta.mime_type || 'audio/mpeg';
    const fileSize = fileData?.file_size || meta.file_size || 0;
    const isMusicGeneration = source === 'assistant' && Boolean(meta.music_generation);

    const audioWrapper = document.createElement('div');
    audioWrapper.className = isMusicGeneration
        ? 'assistant-inline-audio assistant-inline-music'
        : 'assistant-inline-audio';
    audioWrapper.dataset.fileId = fileId;
    audioWrapper.dataset.fileType = String(fileType || '').toLowerCase();

    const topRow = document.createElement('div');
    topRow.className = 'assistant-inline-audio-top';
    const iconBox = document.createElement('div');
    iconBox.className = 'assistant-inline-audio-icon';
    iconBox.setAttribute('aria-hidden', 'true');
    const iconRegistry = typeof Icons !== 'undefined' ? Icons : null;
    iconBox.innerHTML = iconRegistry?.audio_gen || iconRegistry?.speaker || '';
    const metaBox = document.createElement('div');
    metaBox.className = 'assistant-inline-audio-meta';
    const nameEl = document.createElement('span');
    nameEl.className = 'assistant-inline-audio-name';
    nameEl.title = originalName;
    nameEl.textContent = originalName;
    const detailsEl = document.createElement('span');
    detailsEl.className = 'assistant-inline-audio-details';
    const normalizedType = String(fileType || '').toLowerCase();
    const formatLabelMap = {
        'audio/mpeg': 'MP3',
        'audio/mp3': 'MP3',
        'audio/wav': 'WAV',
        'audio/x-wav': 'WAV',
        'audio/flac': 'FLAC',
        'audio/aac': 'AAC',
        'audio/opus': 'OPUS',
        'audio/ogg': 'OGG',
        'audio/m4a': 'M4A',
    };
    const extLabel = formatLabelMap[normalizedType] || String(fileType || '').replace('audio/', '').toUpperCase();
    detailsEl.textContent = `${extLabel}${fileSize ? ` • ${formatFileSizeForDisplay(fileSize)}` : ''}`;
    metaBox.appendChild(nameEl);
    metaBox.appendChild(detailsEl);
    topRow.appendChild(iconBox);
    topRow.appendChild(metaBox);

    if (isMusicGeneration) {
        const badgeRow = document.createElement('div');
        badgeRow.className = 'assistant-inline-music-badges';

        const badges = [
            'Music',
            meta.model ? String(meta.model) : '',
            meta.response_format ? String(meta.response_format).toUpperCase() : '',
            Number(meta.reference_image_count || 0) > 0 ? `${meta.reference_image_count} refs` : '',
        ].filter(Boolean);

        badges.forEach((badge) => {
            const badgeEl = document.createElement('span');
            badgeEl.className = 'assistant-inline-music-badge';
            badgeEl.textContent = badge;
            badgeRow.appendChild(badgeEl);
        });
        metaBox.appendChild(badgeRow);
    }

    const audio = document.createElement('audio');
    audio.className = 'assistant-inline-audio-player';
    audio.setAttribute('aria-label', getChatA11yText('chat_sr_audio_attachment', 'Audio attachment: {name}', { name: originalName }));
    audio.controls = true;
    audio.preload = 'metadata';

    audio.addEventListener('loadedmetadata', () => {
        audioWrapper.classList.add('loaded');
    });
    audio.addEventListener('error', () => {
        if (typeof onError === 'function') {
            onError();
        }
    });

    const downloadBtn = document.createElement('button');
    downloadBtn.className = 'assistant-inline-image-download';
    downloadBtn.title = getStreamText('download_audio', 'Download audio');
    downloadBtn.setAttribute('aria-label', getChatA11yText('chat_sr_download_audio', 'Download audio: {name}', { name: originalName }));
    downloadBtn.innerHTML = iconRegistry?.download || '';
    downloadBtn.addEventListener('click', async (event) => {
        event.preventDefault();
        event.stopPropagation();
        try {
            const response = await fetchChatFileDownload(fileId);
            if (!response.ok) throw new Error(getStreamText('chat_download_failed', 'Download failed'));
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = originalName;
            document.body.appendChild(anchor);
            anchor.click();
            document.body.removeChild(anchor);
            URL.revokeObjectURL(url);
        } catch (error) {
            console.error('Failed to download audio:', error);
            notifyError?.(getStreamText('chat_failed_download_audio', 'Failed to download audio'));
        }
    });

    audioWrapper.appendChild(topRow);
    audioWrapper.appendChild(audio);

    if (isMusicGeneration) {
        const notes = [];
        const textBlocks = Array.isArray(meta.text_blocks)
            ? meta.text_blocks.map((item) => String(item || '').trim()).filter(Boolean)
            : [];
        const textContent = String(meta.text_content || '').trim();
        const lyrics = String(meta.custom_lyrics || '').trim();
        if (textContent) notes.push(textContent);
        textBlocks.forEach((block) => {
            if (!notes.includes(block)) notes.push(block);
        });

        if (notes.length || lyrics) {
            const details = document.createElement('details');
            details.className = 'assistant-inline-music-details';

            const summary = document.createElement('summary');
            summary.textContent = lyrics
                ? 'Track details and lyrics'
                : 'Track details';
            details.appendChild(summary);

            if (notes.length) {
                const noteText = document.createElement('div');
                noteText.className = 'assistant-inline-music-text';
                noteText.textContent = notes.join('\n\n');
                details.appendChild(noteText);
            }

            if (lyrics) {
                const lyricsBlock = document.createElement('pre');
                lyricsBlock.className = 'assistant-inline-music-lyrics';
                lyricsBlock.textContent = lyrics;
                details.appendChild(lyricsBlock);
            }

            audioWrapper.appendChild(details);
        }
    }

    audioWrapper.appendChild(downloadBtn);

    loadAssistantAudioWithAuth(audio, fileId);
    return audioWrapper;
}

function tryCreateVideoPoster(videoElement) {
    if (!videoElement || !videoElement.videoWidth || !videoElement.videoHeight) {
        return;
    }
    try {
        const canvas = document.createElement('canvas');
        canvas.width = videoElement.videoWidth;
        canvas.height = videoElement.videoHeight;
        const context = canvas.getContext('2d');
        if (!context) {
            return;
        }
        context.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
        const posterData = canvas.toDataURL('image/jpeg', 0.82);
        if (posterData) {
            videoElement.poster = posterData;
        }
    } catch (_) {
        // Poster generation is best-effort.
    }
}

async function loadAssistantImageWithAuth(imgElement, fileId) {
    if (!imgElement) return;
    try {
        const response = await fetchChatFileDownload(fileId);
        if (!response.ok) {
            console.error('[AssistantFiles] Failed to fetch inline image blob', { fileId, status: response.status });
            imgElement.dispatchEvent(new Event('error'));
            return;
        }
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        imgElement.dataset.objectUrl = objectUrl;
        const cleanup = () => {
            if (imgElement.dataset.objectUrl) {
                URL.revokeObjectURL(imgElement.dataset.objectUrl);
                delete imgElement.dataset.objectUrl;
            }
        };
        imgElement.addEventListener('load', cleanup, { once: true });
        imgElement.addEventListener('error', cleanup, { once: true });
        imgElement.src = objectUrl;
    } catch (error) {
        console.error('[AssistantFiles] Inline image fetch threw', { fileId, error });
        imgElement.dispatchEvent(new Event('error'));
    }
}

async function loadAssistantVideoWithAuth(videoElement, fileId) {
    if (!videoElement) return;
    try {
        const response = await fetchChatFileDownload(fileId);
        if (!response.ok) {
            videoElement.dispatchEvent(new Event('error'));
            return;
        }
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        videoElement.dataset.objectUrl = objectUrl;
        const cleanup = () => {
            if (videoElement.dataset.objectUrl) {
                URL.revokeObjectURL(videoElement.dataset.objectUrl);
                delete videoElement.dataset.objectUrl;
            }
        };
        videoElement.addEventListener('error', cleanup, { once: true });
        videoElement.src = objectUrl;
    } catch (_) {
        videoElement.dispatchEvent(new Event('error'));
    }
}

async function loadAssistantAudioWithAuth(audioElement, fileId) {
    if (!audioElement) return;
    try {
        const response = await fetchChatFileDownload(fileId);
        if (!response.ok) {
            audioElement.dispatchEvent(new Event('error'));
            return;
        }
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        audioElement.dataset.objectUrl = objectUrl;
        const cleanup = () => {
            if (audioElement.dataset.objectUrl) {
                URL.revokeObjectURL(audioElement.dataset.objectUrl);
                delete audioElement.dataset.objectUrl;
            }
        };
        audioElement.addEventListener('error', cleanup, { once: true });
        if (typeof MutationObserver !== 'undefined' && audioElement.parentNode) {
            const observer = new MutationObserver(() => {
                if (!document.contains(audioElement)) {
                    cleanup();
                    observer.disconnect();
                }
            });
            observer.observe(audioElement.parentNode, { childList: true });
        }
        audioElement.src = objectUrl;
    } catch (_) {
        audioElement.dispatchEvent(new Event('error'));
    }
}

function renderAssistantFileBlock(messageId, fileId, fileData, options = {}) {
    const sourceIsCanvas = Boolean(options?.sourceIsCanvas);
    if (window.latexPdfWidget?.isLatexPdfFile?.(fileId)) {
        return;
    }

    // Try canvas rendering first so historical canvas widgets are attempted even if
    // the message container is temporarily unavailable.
    if (shouldSkipCanvasAssistantFile({
        fileId,
        meta: fileData?.meta,
        fileType: fileData?.file_type || fileData?.mime_type,
        fileName: fileData?.original_filename || fileData?.original_name || fileData?.file_name,
        sourceIsCanvas,
    })) {
        const renderedCanvas = renderCanvasWidgetForFile({
            messageId,
            fileId,
            fileData,
            fallbackName: fileData?.file_name || fileData?.original_filename || fileData?.original_name,
        });
        if (renderedCanvas) {
            return;
        }
    }

    // Helper to render a file block when loading messages (not streaming)
    const assistantMessageContainer = document.getElementById('a-' + messageId);
    if (!assistantMessageContainer) {
        return;
    }

    // Ensure prior thinking blocks are finalized before rendering any file output (including canvas widgets)
    finalizeThinkingBlocks(assistantMessageContainer);

    const meta = fileData?.meta || {};
    const originalName = meta.original_filename
        || fileData?.original_filename
        || fileData?.original_name
        || fileData?.file_name
        || 'Rendered Image';
    const resolvedFileType = resolveAssistantFileType(
        fileData?.file_type || fileData?.mime_type || meta.file_type || meta.mime_type,
        originalName,
    );
    const hasKnownType = Boolean(resolvedFileType);
    if (!hasKnownType) {
        if (isChatViewReadOnly()) {
            registerGeneratedAssistantFile(fileId, fileData || {});
            const fallbackWrapper = createAssistantFileFallback(fileId, fileData || {});
            appendBeforeAssistantList(assistantMessageContainer, fallbackWrapper);
            refreshUnsupportedFileWarningsFromState();
            return;
        }
        fetchChatFileMeta(fileId)
            .then((response) => (response.ok ? response.json() : null))
            .then((resolvedData) => {
                if (!assistantMessageContainer.isConnected) return;
                if (!resolvedData) {
                    registerGeneratedAssistantFile(fileId, fileData || {});
                    const fallbackWrapper = createAssistantFileFallback(fileId, fileData || {});
                    appendBeforeAssistantList(assistantMessageContainer, fallbackWrapper);
                    refreshUnsupportedFileWarningsFromState();
                    return;
                }
                const mergedData = {
                    ...(fileData || {}),
                    ...resolvedData,
                    meta: { ...(fileData?.meta || {}), ...(resolvedData?.meta || {}) },
                };
                renderAssistantFileBlock(messageId, fileId, mergedData, options);
            })
            .catch(() => {
                if (!assistantMessageContainer.isConnected) return;
                registerGeneratedAssistantFile(fileId, fileData || {});
                const fallbackWrapper = createAssistantFileFallback(fileId, fileData || {});
                appendBeforeAssistantList(assistantMessageContainer, fallbackWrapper);
                refreshUnsupportedFileWarningsFromState();
            });
        return;
    }

    if (shouldSkipCanvasAssistantFile({ fileId, meta, fileType: resolvedFileType, fileName: originalName })) {
        const renderedCanvas = renderCanvasWidgetForFile({
            messageId,
            fileId,
            fileData,
            fallbackName: originalName,
        });
        if (renderedCanvas) {
            return;
        }
    }
    const fileType = resolvedFileType || 'image/png';
    // Carry filename-based MIME inference into every historical renderer. This
    // is especially important for SVG records stored as octet-stream,
    // because preview styling and the generated-files registry read this data.
    const normalizedFileData = {
        ...(fileData || {}),
        file_type: fileType,
        mime_type: fileType,
        meta: {
            ...meta,
            file_type: fileType,
            mime_type: fileType,
        },
    };
    registerGeneratedAssistantFile(fileId, normalizedFileData, originalName);
    
    // Check if this is a displayable image type
    if (isDisplayableImageType(fileType)) {
        // Render as inline image with download overlay
        const imageWrapper = createAssistantInlineImage(fileId, normalizedFileData, () => {
            // On error, replace with file element fallback
            const fallbackWrapper = createAssistantFileFallback(fileId, normalizedFileData);
            console.warn('[AssistantFiles] Historical image load failed, fallback to file element', { fileId });
            if (imageWrapper.parentElement) {
                imageWrapper.parentElement.replaceChild(fallbackWrapper, imageWrapper);
                refreshUnsupportedFileWarningsFromState();
            }
        });
        appendBeforeAssistantList(assistantMessageContainer, imageWrapper);
        refreshUnsupportedFileWarningsFromState();
        return;
    }

    if (isDisplayableVideoType(fileType)) {
        const videoWrapper = createAssistantInlineVideo(fileId, normalizedFileData, () => {
            const fallbackWrapper = createAssistantFileFallback(fileId, normalizedFileData);
            if (videoWrapper.parentElement) {
                videoWrapper.parentElement.replaceChild(fallbackWrapper, videoWrapper);
                refreshUnsupportedFileWarningsFromState();
            }
        });
        appendBeforeAssistantList(assistantMessageContainer, videoWrapper);
        refreshUnsupportedFileWarningsFromState();
        return;
    }

    if (isDisplayableAudioType(fileType)) {
        const audioWrapper = createAssistantInlineAudio(fileId, normalizedFileData, () => {
            const fallbackWrapper = createAssistantFileFallback(fileId, normalizedFileData);
            if (audioWrapper.parentElement) {
                audioWrapper.parentElement.replaceChild(fallbackWrapper, audioWrapper);
                refreshUnsupportedFileWarningsFromState();
            }
        }, { source: 'assistant' });
        appendBeforeAssistantList(assistantMessageContainer, audioWrapper);
        refreshUnsupportedFileWarningsFromState();
        return;
    }
    
    // Non-image files: render as file element
    const fileWrapper = createAssistantFileFallback(fileId, normalizedFileData);
    appendBeforeAssistantList(assistantMessageContainer, fileWrapper);
    refreshUnsupportedFileWarningsFromState();
}

function createAssistantFileFallback(fileId, fileData) {
    const meta = fileData?.meta || {};
    const originalName = meta.original_filename || fileData?.original_filename || fileData?.original_name || 'Rendered Image';
    const fileSize = fileData?.file_size || meta.file_size || 0;
    const fileType = resolveAssistantFileType(
        fileData?.file_type || meta.file_type || meta.mime_type,
        originalName,
    ) || 'image/png';
    
    // Create file wrapper
    const fileWrapper = document.createElement('div');
    fileWrapper.className = 'assistant-file';
    fileWrapper.dataset.fileId = fileId;
    fileWrapper.dataset.fileType = String(fileType || '').toLowerCase();
    
    // Create inline files container
    const inlineFilesContainer = document.createElement('div');
    inlineFilesContainer.className = 'inline-files active assistant-generated-file';
    
    // Create file element
    const fileElement = document.createElement('div');
    fileElement.className = 'inline-files-element';
    fileElement.dataset.fileId = fileId;
    fileElement.dataset.fileType = String(fileType || '').toLowerCase();
    
    // Icon wrapper
    const iconWrapper = document.createElement('div');
    iconWrapper.className = 'inline-files-element-icon';
    const iconImg = document.createElement('img');
    
    // Determine icon based on file type
    const iconMap = {
        'image/png': 'png.svg',
        'image/jpeg': 'jpg.svg',
        'image/jpg': 'jpg.svg',
        'image/gif': 'gif.svg',
        'image/svg+xml': 'svg.svg',
        'audio/mpeg': 'mp3.svg',
        'audio/mp3': 'mp3.svg',
        'audio/wav': 'mp3.svg',
        'audio/aac': 'aac.svg',
        'audio/ogg': 'mp3.svg',
        'audio/flac': 'mp3.svg',
        'audio/opus': 'mp3.svg',
        'video/mp4': 'mpg.svg',
        'video/webm': 'mov.svg',
        'video/quicktime': 'mov.svg',
        'video/x-matroska': 'avi.svg',
        'video/ogg': 'flv.svg',
    };
    iconImg.src = `/assets/file_svgs/${iconMap[fileType] || 'txt.svg'}`;
    iconImg.alt = originalName.split('.').pop()?.toUpperCase() || 'FILE';
    iconImg.width = 28;
    iconImg.height = 28;
    iconImg.style.display = 'block';
    iconImg.style.objectFit = 'contain';
    iconWrapper.appendChild(iconImg);
    
    // Content wrapper
    const contentWrapper = document.createElement('div');
    contentWrapper.className = 'inline-files-element-content';
    
    // Top row (filename)
    const topRow = document.createElement('div');
    topRow.className = 'inline-files-element-content-top';
    const nameEl = document.createElement('p');
    nameEl.textContent = originalName;
    topRow.appendChild(nameEl);
    
    // Bottom row (extension and size)
    const bottomRow = document.createElement('div');
    bottomRow.className = 'inline-files-element-content-bottom';
    const extensionEl = document.createElement('p');
    extensionEl.textContent = originalName.includes('.') ? originalName.split('.').pop().toUpperCase() : 'FILE';
    bottomRow.appendChild(extensionEl);
    
    if (fileSize > 0) {
        const sizeEl = document.createElement('p');
        sizeEl.textContent = formatFileSizeForDisplay(fileSize);
        bottomRow.appendChild(sizeEl);
    }
    
    contentWrapper.appendChild(topRow);
    contentWrapper.appendChild(bottomRow);
    
    fileElement.appendChild(iconWrapper);
    fileElement.appendChild(contentWrapper);
    inlineFilesContainer.appendChild(fileElement);
    fileWrapper.appendChild(inlineFilesContainer);
    
    // Attach preview click handler
    const normalizedFile = {
        file_id: fileId,
        id: fileId,
        file_type: fileType,
        file_size: fileSize,
        meta: {
            original_filename: originalName,
            mime_type: fileType,
            file_size: fileSize,
            origin: 'assistant',
        },
    };
    
    const previewTarget = enhanceChatTranscriptFileCard(fileElement, normalizedFile);
    attachPreviewToInlineFile(previewTarget, normalizedFile);
    
    return fileWrapper;
}

