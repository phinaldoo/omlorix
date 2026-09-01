function extractMcpAppMetaFromToolDescriptor(toolDescriptor) {
    if (!toolDescriptor || typeof toolDescriptor !== 'object') {
        return null;
    }
    const metaSource = toolDescriptor.meta && typeof toolDescriptor.meta === 'object'
        ? toolDescriptor.meta
        : toolDescriptor;
    if (!metaSource.mcp_app || typeof metaSource.mcp_app !== 'object') {
        return null;
    }
    try {
        return JSON.parse(JSON.stringify(metaSource.mcp_app));
    } catch (_) {
        return metaSource.mcp_app;
    }
}

function findAssistantMcpAppWidget(container, { toolCallId = '', toolName = '', serverId = '', liveOnly = false } = {}) {
    if (!container) {
        return null;
    }
    const widgets = Array.from(container.querySelectorAll('.assistant-widget[data-widget-type="mcp_app"]'));
    const matches = widgets.filter((widget) => {
        if (!(widget instanceof HTMLElement)) {
            return false;
        }
        if (liveOnly && widget.dataset.mcpLive !== 'true') {
            return false;
        }
        if (toolCallId) {
            return (widget.dataset.mcpToolCallId || '') === toolCallId;
        }
        if (serverId && (widget.dataset.mcpServerId || '') !== serverId) {
            return false;
        }
        if (toolName && (widget.dataset.mcpToolName || '') !== toolName) {
            return false;
        }
        return Boolean(serverId || toolName);
    });
    return matches.length ? matches[matches.length - 1] : null;
}

function tagAssistantMcpAppWidget(widgetWrapper, mcpAppMeta, { toolCallId = '', live = true } = {}) {
    if (!widgetWrapper || !mcpAppMeta || typeof mcpAppMeta !== 'object') {
        return;
    }
    widgetWrapper.dataset.widgetType = 'mcp_app';
    widgetWrapper.dataset.mcpServerId = String(mcpAppMeta.server_id || '').trim();
    widgetWrapper.dataset.mcpToolName = String(mcpAppMeta.tool_name || mcpAppMeta.public_name || '').trim();
    const normalizedToolCallId = String(toolCallId || mcpAppMeta.tool_call_id || '').trim();
    if (normalizedToolCallId) {
        widgetWrapper.dataset.mcpToolCallId = normalizedToolCallId;
    }
    widgetWrapper.dataset.mcpLive = live ? 'true' : 'false';
    widgetWrapper.classList.toggle('assistant-widget-live', live);
}

function parseAssistantMcpToolInputState(rawValue) {
    if (rawValue == null) {
        return null;
    }
    if (typeof rawValue === 'object') {
        try {
            return {
                value: JSON.parse(JSON.stringify(rawValue)),
                complete: true,
            };
        } catch (_) {
            return {
                value: rawValue,
                complete: true,
            };
        }
    }
    if (typeof rawValue !== 'string') {
        return null;
    }
    const trimmed = rawValue.trim();
    if (!trimmed) {
        return {
            value: {},
            complete: true,
        };
    }
    try {
        return {
            value: JSON.parse(trimmed),
            complete: true,
        };
    } catch (_) {
        return recoverAssistantMcpToolInputPrefix(trimmed);
    }
}

function recoverAssistantMcpToolInputPrefix(rawValue) {
    const source = String(rawValue || '');
    if (!source) {
        return null;
    }

    let index = 0;

    function skipWhitespace() {
        while (index < source.length && /\s/.test(source[index])) {
            index += 1;
        }
    }

    function parseString() {
        if (source[index] !== '"') {
            return { hasValue: false };
        }
        index += 1;
        let result = '';

        while (index < source.length) {
            const ch = source[index];
            index += 1;

            if (ch === '"') {
                return {
                    hasValue: true,
                    value: result,
                    complete: true,
                };
            }

            if (ch === '\\') {
                if (index >= source.length) {
                    return {
                        hasValue: true,
                        value: result,
                        complete: false,
                    };
                }

                const escapeChar = source[index];
                index += 1;

                if (escapeChar === '"' || escapeChar === '\\' || escapeChar === '/') {
                    result += escapeChar;
                    continue;
                }
                if (escapeChar === 'b') {
                    result += '\b';
                    continue;
                }
                if (escapeChar === 'f') {
                    result += '\f';
                    continue;
                }
                if (escapeChar === 'n') {
                    result += '\n';
                    continue;
                }
                if (escapeChar === 'r') {
                    result += '\r';
                    continue;
                }
                if (escapeChar === 't') {
                    result += '\t';
                    continue;
                }
                if (escapeChar === 'u') {
                    const unicodeDigits = source.slice(index, index + 4);
                    if (!/^[0-9a-fA-F]{4}$/.test(unicodeDigits)) {
                        return {
                            hasValue: true,
                            value: result,
                            complete: false,
                        };
                    }
                    result += String.fromCharCode(parseInt(unicodeDigits, 16));
                    index += 4;
                    continue;
                }

                result += escapeChar;
                continue;
            }

            result += ch;
        }

        return {
            hasValue: true,
            value: result,
            complete: false,
        };
    }

    function parseNumber() {
        const numberStart = index;
        while (index < source.length && /[0-9eE+.\-]/.test(source[index])) {
            index += 1;
        }

        const token = source.slice(numberStart, index);
        if (!token) {
            return { hasValue: false };
        }

        for (let end = token.length; end > 0; end -= 1) {
            const candidate = token.slice(0, end);
            if (/^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?$/.test(candidate)) {
                index = numberStart + end;
                return {
                    hasValue: true,
                    value: Number(candidate),
                    complete: end === token.length,
                };
            }
        }

        index = numberStart;
        return { hasValue: false };
    }

    function parseLiteral(literal, value) {
        const remaining = source.slice(index, index + literal.length);
        if (remaining === literal) {
            index += literal.length;
            return {
                hasValue: true,
                value,
                complete: true,
            };
        }
        if (literal.startsWith(source.slice(index))) {
            index = source.length;
            return { hasValue: false };
        }
        return { hasValue: false };
    }

    function parseArray() {
        if (source[index] !== '[') {
            return { hasValue: false };
        }
        index += 1;
        const result = [];
        let complete = false;

        while (index <= source.length) {
            skipWhitespace();
            if (index >= source.length) {
                break;
            }
            if (source[index] === ']') {
                index += 1;
                complete = true;
                break;
            }

            const valueState = parseValue();
            if (!valueState?.hasValue) {
                break;
            }
            result.push(valueState.value);
            if (!valueState.complete) {
                break;
            }

            skipWhitespace();
            if (index >= source.length) {
                break;
            }
            if (source[index] === ',') {
                index += 1;
                continue;
            }
            if (source[index] === ']') {
                index += 1;
                complete = true;
                break;
            }
            break;
        }

        return {
            hasValue: true,
            value: result,
            complete,
        };
    }

    function parseObject() {
        if (source[index] !== '{') {
            return { hasValue: false };
        }
        index += 1;
        const result = {};
        let complete = false;

        while (index <= source.length) {
            skipWhitespace();
            if (index >= source.length) {
                break;
            }
            if (source[index] === '}') {
                index += 1;
                complete = true;
                break;
            }

            const keyState = parseString();
            if (!keyState?.hasValue || !keyState.complete) {
                break;
            }

            skipWhitespace();
            if (index >= source.length || source[index] !== ':') {
                break;
            }
            index += 1;

            skipWhitespace();
            if (index >= source.length) {
                break;
            }

            const valueState = parseValue();
            if (!valueState?.hasValue) {
                break;
            }
            result[keyState.value] = valueState.value;
            if (!valueState.complete) {
                break;
            }

            skipWhitespace();
            if (index >= source.length) {
                break;
            }
            if (source[index] === ',') {
                index += 1;
                continue;
            }
            if (source[index] === '}') {
                index += 1;
                complete = true;
                break;
            }
            break;
        }

        return {
            hasValue: true,
            value: result,
            complete,
        };
    }

    function parseValue() {
        skipWhitespace();
        if (index >= source.length) {
            return { hasValue: false };
        }

        const ch = source[index];
        if (ch === '{') return parseObject();
        if (ch === '[') return parseArray();
        if (ch === '"') return parseString();
        if (ch === '-' || /\d/.test(ch)) return parseNumber();
        if (ch === 't') return parseLiteral('true', true);
        if (ch === 'f') return parseLiteral('false', false);
        if (ch === 'n') return parseLiteral('null', null);
        return { hasValue: false };
    }

    const parsed = parseValue();
    if (!parsed?.hasValue) {
        return null;
    }

    return {
        value: parsed.value,
        complete: false,
    };
}

function syncStreamingMcpAppWidget(messageId, toolDescriptor) {
    if (!toolDescriptor || typeof toolDescriptor !== 'object') {
        return null;
    }
    if (
        typeof window.mcpAppsWidget?.renderWidget !== 'function'
        || typeof window.mcpAppsWidget?.updateWidget !== 'function'
    ) {
        return null;
    }

    const assistantMessageContainer = document.getElementById('a-' + messageId);
    const mcpAppMeta = extractMcpAppMetaFromToolDescriptor(toolDescriptor);
    if (!assistantMessageContainer || !mcpAppMeta) {
        return null;
    }

    const toolCallId = String(toolDescriptor.id || mcpAppMeta.tool_call_id || '').trim();
    const toolName = String(toolDescriptor.name || mcpAppMeta.tool_name || mcpAppMeta.public_name || '').trim();
    const serverId = String(mcpAppMeta.server_id || '').trim();

    let widgetWrapper = findAssistantMcpAppWidget(assistantMessageContainer, {
        toolCallId,
        toolName,
        serverId,
        liveOnly: true,
    });

    if (!widgetWrapper) {
        widgetWrapper = document.createElement('div');
        widgetWrapper.className = 'assistant-widget assistant-widget-live';
        appendBeforeAssistantList(assistantMessageContainer, widgetWrapper);
    }

    const currentBuffer = String(widgetWrapper.dataset.mcpToolArgsBuffer || '');
    const delta = typeof toolDescriptor.delta === 'string' ? toolDescriptor.delta : '';
    const nextBuffer = delta ? currentBuffer + delta : currentBuffer;
    if (delta) {
        widgetWrapper.dataset.mcpToolArgsBuffer = nextBuffer;
    }

    const widgetMeta = { mcp_app: JSON.parse(JSON.stringify(mcpAppMeta)) };
    if (toolCallId) {
        widgetMeta.mcp_app.tool_call_id = toolCallId;
    }

    let parsedState = null;
    if (Object.prototype.hasOwnProperty.call(toolDescriptor, 'args')) {
        parsedState = parseAssistantMcpToolInputState(toolDescriptor.args);
        if (parsedState?.value != null) {
            widgetWrapper.dataset.mcpToolArgsBuffer = typeof toolDescriptor.args === 'string'
                ? toolDescriptor.args
                : JSON.stringify(toolDescriptor.args);
        }
    } else if (nextBuffer) {
        parsedState = parseAssistantMcpToolInputState(nextBuffer);
    }

    if (parsedState?.value != null) {
        widgetMeta.mcp_app.tool_input = parsedState.value;
    }
    if (Object.prototype.hasOwnProperty.call(toolDescriptor, 'args')) {
        widgetMeta.mcp_app.tool_input_raw_prefix = typeof toolDescriptor.args === 'string'
            ? toolDescriptor.args
            : JSON.stringify(toolDescriptor.args);
        widgetMeta.mcp_app.tool_input_done = Boolean(parsedState?.complete);
    } else if (nextBuffer) {
        widgetMeta.mcp_app.tool_input_raw_prefix = nextBuffer;
        widgetMeta.mcp_app.tool_input_done = Boolean(parsedState?.complete);
    }

    tagAssistantMcpAppWidget(widgetWrapper, widgetMeta.mcp_app, { toolCallId, live: true });
    applyAssistantMessageAccessibility(assistantMessageContainer, { messageId, streaming: true });

    const hasMountedWidget = Boolean(String(widgetWrapper.dataset.mcpAppWidgetId || '').trim());
    Promise.resolve()
        .then(() => {
            if (hasMountedWidget) {
                return window.mcpAppsWidget.updateWidget(widgetWrapper, widgetMeta);
            }
            return window.mcpAppsWidget.renderWidget(widgetWrapper, widgetMeta);
        })
        .catch((error) => {
            console.error('[mcp-app] Failed to sync streaming widget host', error);
        });

    return widgetWrapper;
}

const BACKEND_WIDGET_IFRAME_SANDBOX_WITH_SCRIPTS = 'allow-scripts';
const BACKEND_WIDGET_MIN_HEIGHT = 160;
const BACKEND_WIDGET_MAX_HEIGHT = 2400;
const BACKEND_WIDGET_LOAD_TIMEOUT_MS = 15000;
let backendWidgetResizeListenerBound = false;

function cloneSerializableWidgetMeta(widgetMeta) {
    if (!widgetMeta || typeof widgetMeta !== 'object') {
        return null;
    }
    return JSON.parse(JSON.stringify(widgetMeta));
}

function shouldRenderBackendWidgetIframe(widgetMeta, widgetType = '') {
    if (!widgetMeta || typeof widgetMeta !== 'object') {
        return false;
    }
    const renderMode = String(widgetMeta.render_mode || '').trim().toLowerCase();
    return renderMode === 'iframe'
        || widgetMeta.allow_scripts === true;
}

function getBackendWidgetTitle(widgetType, widgetMeta = null) {
    const toolName = widgetMeta && typeof widgetMeta.tool_name === 'string' && widgetMeta.tool_name.trim()
        ? widgetMeta.tool_name.trim()
        : widgetType;
    const displayName = getToolDisplayName(getToolConfig(toolName), toolName);
    return displayName || getSubagentText('subagent_event_widget', 'Widget');
}

async function createBackendWidgetFrameUrl(widgetHtml, widgetType) {
    const fetcher = typeof window.authedFetch === 'function' ? window.authedFetch : window.fetch;
    if (typeof fetcher !== 'function') {
        throw new Error('Authenticated fetch is unavailable.');
    }
    const response = await fetcher.call(window, '/api/v1/llm/widgets/frame', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        credentials: 'same-origin',
        body: JSON.stringify({
            html: String(widgetHtml || ''),
            widget_type: String(widgetType || 'unknown'),
            theme_mode: document.documentElement?.getAttribute('data-mode') || '',
        }),
    });
    if (!response || !response.ok) {
        throw new Error('Failed to create backend widget frame.');
    }
    const payload = await response.json();
    const frameId = String(payload?.frame_id || '').trim();
    const frameUrl = String(payload?.frame_url || '').trim();
    if (!frameId) {
        throw new Error('Backend widget frame ID is missing.');
    }
    if (!frameUrl) {
        throw new Error('Backend widget frame URL is missing.');
    }
    return { frameId, frameUrl };
}

function ensureBackendWidgetResizeListener() {
    if (backendWidgetResizeListenerBound || typeof window === 'undefined') {
        return;
    }
    backendWidgetResizeListenerBound = true;
    window.addEventListener('message', (event) => {
        const data = event?.data;
        if (!data || data.type !== 'omlorix:backend-widget-resize') {
            return;
        }
        const frameId = String(data.frameId || '').trim();
        const height = Number(data.height);
        if (!frameId || !Number.isFinite(height)) {
            return;
        }
        const frame = Array.from(document.querySelectorAll('.assistant-widget-backend-frame'))
            .find((candidate) => candidate.dataset.backendWidgetFrameId === frameId);
        if (!frame || event.source !== frame.contentWindow) {
            return;
        }
        // Script-enabled frames report their first measured height only after
        // the isolated document has executed successfully. Treat that message
        // as the explicit readiness signal used to clear the load timeout.
        if (typeof frame._backendWidgetMarkLoaded === 'function') {
            frame._backendWidgetMarkLoaded();
        }
        const nextHeight = Math.max(
            BACKEND_WIDGET_MIN_HEIGHT,
            Math.min(BACKEND_WIDGET_MAX_HEIGHT, Math.ceil(height)),
        );
        frame.style.height = `${nextHeight}px`;
    });
}

function renderBackendWidgetIframe(widgetWrapper, decodedHtml, widgetType, widgetMeta = null) {
    ensureBackendWidgetResizeListener();
    const allowScripts = widgetMeta?.allow_scripts === true;
    const iframe = document.createElement('iframe');
    iframe.className = 'assistant-widget-backend-frame';
    iframe.title = getBackendWidgetTitle(widgetType, widgetMeta);
    iframe.setAttribute('sandbox', allowScripts ? BACKEND_WIDGET_IFRAME_SANDBOX_WITH_SCRIPTS : '');
    iframe.setAttribute('referrerpolicy', 'no-referrer');
    iframe.style.height = `${BACKEND_WIDGET_MIN_HEIGHT}px`;
    iframe.removeAttribute('srcdoc');
    widgetWrapper.classList.add('assistant-widget-backend-rendered');
    widgetWrapper.appendChild(iframe);
    let loadSettled = false;
    let loadTimeout = null;

    const markFrameLoaded = () => {
        if (loadSettled) {
            return;
        }
        loadSettled = true;
        if (loadTimeout !== null) {
            window.clearTimeout(loadTimeout);
            loadTimeout = null;
        }
        delete iframe._backendWidgetMarkLoaded;
    };
    const renderFrameLoadError = (error) => {
        if (loadSettled) {
            return;
        }
        loadSettled = true;
        if (loadTimeout !== null) {
            window.clearTimeout(loadTimeout);
            loadTimeout = null;
        }
        delete iframe._backendWidgetMarkLoaded;
        console.error('[backend-widget] Failed to load backend-rendered widget frame', error);
        if (!iframe.isConnected) {
            return;
        }
        iframe.remove();
        const errorMessage = document.createElement('div');
        errorMessage.className = 'assistant-widget-backend-error';
        errorMessage.setAttribute('role', 'status');
        errorMessage.setAttribute('aria-live', 'polite');
        errorMessage.textContent = getStreamText(
            'chat_widget_load_error',
            'This interactive widget could not be loaded. Try again in a moment.',
        );
        widgetWrapper.appendChild(errorMessage);
    };

    iframe._backendWidgetMarkLoaded = markFrameLoaded;
    createBackendWidgetFrameUrl(decodedHtml, widgetType)
        .then(({ frameId, frameUrl }) => {
            if (!iframe.isConnected) {
                markFrameLoaded();
                return;
            }
            iframe.dataset.backendWidgetFrameId = frameId;
            iframe.removeAttribute('srcdoc');
            loadTimeout = window.setTimeout(() => {
                renderFrameLoadError(new Error('Backend widget iframe load timed out.'));
            }, BACKEND_WIDGET_LOAD_TIMEOUT_MS);
            iframe.addEventListener('error', () => {
                renderFrameLoadError(new Error('Backend widget iframe failed to load.'));
            }, { once: true });
            iframe.addEventListener('load', () => {
                // Sandboxed static frames cannot run the resize reporter, so their
                // native load event is the strongest available success signal.
                if (!allowScripts) {
                    markFrameLoaded();
                }
            }, { once: true });
            iframe.src = frameUrl;
        })
        .catch(renderFrameLoadError);
}

function appendAssistantWidget(messageId, widgetHtml, widgetType,
    last_appended_message_type,
    widgetMeta = null,
    widgetOptions = {},
) {
    const assistantMessageContainer = document.getElementById('a-' + messageId);
    if (!assistantMessageContainer) {
        return;
    }

    // Skip canvas_result widgets if an inline widget was already injected during generation
    if (widgetType === 'canvas_result') {
        const existingWidget = assistantMessageContainer.querySelector('.canvas-markdown-result-widget');
        if (existingWidget) return;
    }

    // A notes create stream injects its result card before persistence.
    // Upgrade that same wrapper with the canonical backend payload instead of
    // appending a second card after notes_evt:saved.
    if (widgetType === 'notes_result') {
        const toolResult = widgetMeta && typeof widgetMeta === 'object' && widgetMeta.tool_result
            && typeof widgetMeta.tool_result === 'object'
            ? widgetMeta.tool_result
            : null;
        const operation = String(toolResult?.operation || '').trim().toLowerCase();
        // Notes cards represent artifact creation only. Keep the tool activity
        // row for edit/view calls, including older persisted widget payloads,
        // without appending another card for the same note.
        if (operation && operation !== 'create') {
            return;
        }
        const noteId = String(toolResult?.note_id || '').trim();
        const existingWidget = noteId
            ? assistantMessageContainer.querySelector(
                `.notes-tool-result-widget[data-note-id="${CSS.escape(noteId)}"]`
            )
            : null;
        if (existingWidget) {
            finalizeThinkingBlocks(assistantMessageContainer);
            const existingWrapper = existingWidget.closest('.assistant-widget');
            if (existingWrapper) {
                existingWrapper.dataset.widgetType = 'notes_result';
                existingWrapper.__chatWidgetPayload = {
                    type: 'notes_result',
                    html: String(widgetHtml ?? ''),
                    meta: cloneSerializableWidgetMeta(widgetMeta),
                };
            }
            if (window.NotesToolSidebar && typeof window.NotesToolSidebar.scanForWidgets === 'function') {
                window.NotesToolSidebar.scanForWidgets(existingWrapper || existingWidget);
            }
            applyAssistantMessageAccessibility(assistantMessageContainer, { messageId, streaming: true });
            return;
        }
    }

    if (
        widgetType === 'mcp_app'
        && widgetMeta
        && typeof window.mcpAppsWidget?.updateWidget === 'function'
    ) {
        const mcpAppMeta = widgetMeta && typeof widgetMeta === 'object' ? widgetMeta.mcp_app : null;
        const existingLiveWidget = mcpAppMeta && typeof mcpAppMeta === 'object'
            ? findAssistantMcpAppWidget(assistantMessageContainer, {
                toolCallId: String(mcpAppMeta.tool_call_id || '').trim(),
                toolName: String(mcpAppMeta.tool_name || mcpAppMeta.public_name || '').trim(),
                serverId: String(mcpAppMeta.server_id || '').trim(),
                liveOnly: true,
            })
            : null;
        if (existingLiveWidget) {
            finalizeThinkingBlocks(assistantMessageContainer);
            tagAssistantMcpAppWidget(existingLiveWidget, mcpAppMeta, {
                toolCallId: String(mcpAppMeta.tool_call_id || existingLiveWidget.dataset.mcpToolCallId || '').trim(),
                live: false,
            });
            Promise.resolve()
                .then(() => window.mcpAppsWidget.updateWidget(existingLiveWidget, widgetMeta))
                .catch((error) => {
                    console.error('[mcp-app] Failed to upgrade streaming widget host', error);
                });
            applyAssistantMessageAccessibility(assistantMessageContainer, { messageId, streaming: true });
            return;
        }
    }

    // Complete prior thinking headings before rendering the widget.
    finalizeThinkingBlocks(assistantMessageContainer);

    // Create widget wrapper
    const widgetWrapper = document.createElement('div');
    widgetWrapper.className = 'assistant-widget';
    widgetWrapper.dataset.widgetType = widgetType || 'unknown';
    // Insert the widget HTML (decode if HTML entities were escaped)
    let decodedHtml = widgetHtml;
    if (typeof widgetHtml === 'string' && widgetHtml.includes('&lt;')) {
        // HTML was entity-encoded, decode it
        const decoder = document.createElement('textarea');
        decoder.innerHTML = widgetHtml;
        decodedHtml = decoder.value;
    }
    widgetWrapper.__chatWidgetPayload = {
        type: widgetType || 'unknown',
        html: String(decodedHtml ?? ''),
        meta: cloneSerializableWidgetMeta(widgetMeta),
    };

    const renderMode = String(widgetMeta?.render_mode || '').trim().toLowerCase();
    const isNativeWidget = renderMode === 'frontend'
        && window.nativeToolWidgets?.isSupported?.(widgetType) === true;
    if (isNativeWidget) {
        appendBeforeAssistantList(assistantMessageContainer, widgetWrapper);
        try {
            const rendered = window.nativeToolWidgets?.render?.(
                widgetWrapper,
                widgetType,
                decodedHtml,
            );
            if (!rendered) {
                throw new Error(`No frontend renderer is registered for ${widgetType || 'unknown'}.`);
            }
            hydrateWidgetByName(widgetType, widgetWrapper, widgetMeta, widgetOptions);
        } catch (error) {
            console.error('[native-widget] Failed to render structured widget data', error);
            const errorMessage = document.createElement('div');
            errorMessage.className = 'assistant-widget-backend-error';
            errorMessage.setAttribute('role', 'status');
            errorMessage.setAttribute('aria-live', 'polite');
            errorMessage.textContent = getStreamText(
                'chat_widget_load_error',
                'This interactive widget could not be loaded. Try again in a moment.',
            );
            widgetWrapper.replaceChildren(errorMessage);
        }
        applyAssistantMessageAccessibility(assistantMessageContainer, { messageId, streaming: true });
        return;
    }

    if (String(widgetType || '').trim().toLowerCase() === 'visualization') {
        // The shared renderer's host chrome uses the Markdown preview design
        // system. Widget wrappers sit outside assistant Markdown content, so
        // opt this one wrapper into that scoped styling explicitly.
        widgetWrapper.classList.add('markdown-body', 'assistant-visualization-widget');
        appendBeforeAssistantList(assistantMessageContainer, widgetWrapper);
        const visualizationMeta = widgetMeta?.visualization && typeof widgetMeta.visualization === 'object'
            ? widgetMeta.visualization
            : {};
        const renderVisualizationError = () => {
            const errorMessage = document.createElement('div');
            errorMessage.className = 'assistant-widget-backend-error';
            errorMessage.setAttribute('role', 'status');
            errorMessage.setAttribute('aria-live', 'polite');
            errorMessage.textContent = getStreamText(
                'visualization_runtime_unavailable',
                'The visualization runtime could not be loaded.'
            );
            widgetWrapper.replaceChildren(errorMessage);
        };
        if (!window.OmlorixVisualizer || typeof window.OmlorixVisualizer.mount !== 'function') {
            renderVisualizationError();
        } else {
            Promise.resolve(window.OmlorixVisualizer.mount(widgetWrapper, decodedHtml, {
                title: visualizationMeta.title || '',
                mode: visualizationMeta.mode || 'normal',
                capabilities: visualizationMeta.capabilities || {
                    scripts: widgetMeta?.allow_scripts === true,
                    external_data: false,
                    chat_followup: false,
                    download: false,
                },
                allowExpand: true,
                allowScripts: false,
                isWidget: true,
            })).catch((error) => {
                renderVisualizationError();
                console.error('[visualization] Failed to mount visualization widget', error);
            });
        }
        applyAssistantMessageAccessibility(assistantMessageContainer, { messageId, streaming: true });
        return;
    }

    if (shouldRenderBackendWidgetIframe(widgetMeta, widgetType)) {
        renderBackendWidgetIframe(widgetWrapper, decodedHtml, widgetType, widgetMeta);
        appendBeforeAssistantList(assistantMessageContainer, widgetWrapper);
        applyAssistantMessageAccessibility(assistantMessageContainer, { messageId, streaming: true });
        return;
    }

    // Extract embedded JSON data from <script> tags and <style> blocks before sanitization strips them
    const extractedDataScripts = [];
    const extractedStyleBlocks = [];
    if (typeof decodedHtml === 'string') {
        try {
            const extractionTemplate = document.createElement('template');
            extractionTemplate.innerHTML = decodedHtml;
            extractionTemplate.content
                .querySelectorAll('script')
                .forEach((scriptNode) => {
                    const scriptType = String(scriptNode.getAttribute('type') || '').trim().toLowerCase();
                    const className = scriptNode.getAttribute('class');

                    if (scriptType === 'application/json' && className) {
                        extractedDataScripts.push({
                            className,
                            content: scriptNode.textContent || '',
                        });
                    }
                });
            extractionTemplate.content
                .querySelectorAll('style')
                .forEach((styleNode) => {
                    extractedStyleBlocks.push(styleNode.textContent || '');
                });
        } catch (error) {
            console.error('Failed to extract widget embedded assets', error);
        }
    }

    const sanitizer = window.ChatSanitizer;
    if (!sanitizer || typeof sanitizer.sanitizeHtml !== 'function') {
        console.error('ChatSanitizer is unavailable; dropping assistant widget HTML for safety.');
        widgetWrapper.textContent = '';
    } else {
        const sanitizedWidgetHtml = sanitizer.sanitizeHtml(decodedHtml, { allowDataAttrs: true });
        widgetWrapper.innerHTML = sanitizedWidgetHtml;
    }
    // Re-inject extracted JSON data as hidden divs (since <script> tags are stripped by sanitizer)
    for (const scriptData of extractedDataScripts) {
        const dataDiv = document.createElement('div');
        dataDiv.className = scriptData.className;
        dataDiv.setAttribute('data-json-store', 'true');
        dataDiv.style.display = 'none';
        dataDiv.textContent = scriptData.content;
        let targetContainer = widgetWrapper;
        if (widgetWrapper.firstElementChild instanceof HTMLElement) {
            targetContainer = widgetWrapper.firstElementChild;
        }

        targetContainer.appendChild(dataDiv);
    }

    // Re-inject extracted <style> blocks (since <style> tags are stripped by sanitizer).
    // Widgets that previously loaded their styles from external CSS files now embed
    // them inline in the Python widget generators, so we must preserve them here.
    // Append at the end so it does not become widgetWrapper.firstElementChild
    // (the JSON re-injection above relies on the widget container being firstElementChild).
    for (const styleContent of extractedStyleBlocks) {
        if (!styleContent) continue;
        const styleEl = document.createElement('style');
        styleEl.setAttribute('data-widget-style', 'true');
        styleEl.textContent = styleContent;
        widgetWrapper.appendChild(styleEl);
    }

    // Append to assistant message container
    appendBeforeAssistantList(assistantMessageContainer, widgetWrapper);
    applyAssistantMessageAccessibility(assistantMessageContainer, { messageId, streaming: true });

    hydrateWidgetByName(widgetType, widgetWrapper, widgetMeta, widgetOptions);
}

function hydrateWidgetByName(widgetType, widgetWrapper, widgetMeta = null, widgetOptions = {}) {
    if (
        widgetType === 'deep_research'
        && window.deepResearchWidget
        && typeof window.deepResearchWidget.hydrateWidget === 'function'
    ) {
        const activity = widgetMeta?.deep_research_activity;
        widgetWrapper.querySelectorAll('.deep-research-widget').forEach((widget) => {
            window.deepResearchWidget.hydrateWidget(widget, activity);
        });
        return;
    }

    if (
        widgetType === 'mcp_app'
        && widgetMeta
        && typeof window.mcpAppsWidget?.renderWidget === 'function'
    ) {
        Promise.resolve()
            .then(() => window.mcpAppsWidget.renderWidget(widgetWrapper, widgetMeta))
            .catch((error) => {
                console.error('[mcp-app] Failed to initialize widget host', error);
            });
        return;
    }


    if (widgetType === 'skill_draft'
        && window.skillDraftWidget
        && typeof window.skillDraftWidget.initWidgets === 'function') {
        try {
            // Only the live stream caller may request automatic opening. DOM
            // streaming markers are also present briefly during transcript
            // restoration and therefore cannot distinguish reloads safely.
            const autoOpen = widgetOptions?.autoOpen === true;
            window.skillDraftWidget.initWidgets(widgetWrapper, { autoOpen });
        } catch (error) {
            console.error('[skill-draft] Failed to initialize draft widget after append', error);
        }
    }

    if (widgetType === 'notes_result'
        && window.NotesToolSidebar
        && typeof window.NotesToolSidebar.scanForWidgets === 'function') {
        try {
            window.NotesToolSidebar.scanForWidgets(widgetWrapper);
        } catch (error) {
            console.error('[notes-tool] Failed to initialize note widget after append', error);
        }
    }
}

/**
 * Append a reasoning delta without reading and rewriting the complete trace.
 * Return whether the new delta can complete a Markdown-style reasoning title,
 * allowing the more expensive full title scan to run only when useful.
 */
