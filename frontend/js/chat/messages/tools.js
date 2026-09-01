function parseToolInput({ tool, tool_name, tool_args } = {}) {
    let name = "";
    let paramKind = "";
    let args = [""];
  
    try {
      // Case 1: tool_name and tool_args explicitly provided
      if (tool_name) {
        name = tool_name;
        if (typeof tool_args === "string") {
          try {
            tool_args = JSON.parse(tool_args);
          } catch {
            // not valid JSON, ignore
          }
        }
  
        if (typeof tool_args === "object" && tool_args !== null) {
          const keys = Object.keys(tool_args);
          if (keys.length > 0) {
            paramKind = keys[0];
            const val = tool_args[paramKind];
            args = Array.isArray(val) ? val : [val];
          }
        }
      }
  
      // Case 2: tool given as a string like weather({"location":"München"})
      else if (typeof tool === "string") {
        const match = tool.match(/^(\w+)\s*\((\{.*\})\)$/);
        if (match) {
          name = match[1];
          const obj = JSON.parse(match[2]);
          const keys = Object.keys(obj);
          if (keys.length > 0) {
            paramKind = keys[0];
            const val = obj[paramKind];
            args = Array.isArray(val) ? val : [val];
          }
        }
      }
  
      // Case 3: tool given as a function call (actual JS object)
      else if (typeof tool === "object" && tool !== null) {
        // try to detect something like weather({location:"München"})
        const str = JSON.stringify(tool);
        const match = str.match(/"(\w+)":/);
        if (match) {
          paramKind = match[1];
          const val = tool[paramKind];
          args = Array.isArray(val) ? val : [val];
        }
      }
  
      // fallback if name not set
      if (!name && typeof tool === "function") name = tool.name;
  
    } catch (err) {
      console.error("Parsing error:", err);
    }
  
    const normalizedArgs = [];
    args.forEach((value) => {
        const stringValue = (value ?? '').toString();
        if (stringValue.trim().length) {
            normalizedArgs.push(stringValue);
        }
    });

    return {
      tool_name: name || "",
      tool_param_kind: paramKind || "",
      tool_args: normalizedArgs,
    };
}

function ensureAssistantToolThinkingContainer(messageId, last_appended_message_type, assistantReasoningCount, effectiveToolName, sanitizedToolArgs) {
    const assistantMessageContainer = document.getElementById('a-' + messageId);
    if (!assistantMessageContainer) {
        return {
            assistantMessageContainer: null,
            assistantReasoningCount,
            thinkingContainer: null,
        };
    }

    let thinkingContainer = null;
    if ((last_appended_message_type === 'r' || last_appended_message_type === 't') && assistantReasoningCount > 0) {
        thinkingContainer = document.getElementById('at-' + assistantReasoningCount + '-' + messageId);
    }

    if (!thinkingContainer) {
        assistantReasoningCount += 1;
        thinkingContainer = document.createElement('div');
        thinkingContainer.id = 'at-' + assistantReasoningCount + '-' + messageId;
        thinkingContainer.className = 'assistant-thinking collapsed';

        const headerBtn = document.createElement('button');
        headerBtn.className = 'assistant-thinking-header';
        headerBtn.setAttribute('aria-expanded', 'false');

        const headerTitleDiv = document.createElement('div');
        headerTitleDiv.className = 'assistant-thinking-title';
        const headerTitleSpan = document.createElement('span');
        headerTitleSpan.dataset.thinkingType = 'tool';
        headerTitleSpan.classList.add('assistant-thinking-shimmer');
        headerTitleSpan.textContent = getToolInProgressText(effectiveToolName, sanitizedToolArgs);
        headerTitleDiv.appendChild(headerTitleSpan);
        headerBtn.appendChild(headerTitleDiv);
        thinkingContainer.appendChild(headerBtn);

        const thinkingContent = document.createElement('div');
        thinkingContent.className = 'assistant-thinking-content';
        const thinkingBody = document.createElement('div');
        thinkingBody.className = 'assistant-thinking-body';
        thinkingContent.appendChild(thinkingBody);
        thinkingContainer.appendChild(thinkingContent);

        appendBeforeAssistantList(assistantMessageContainer, thinkingContainer);

        try {
            if (typeof toggleThinking === 'function') {
                headerBtn.addEventListener('click', () => toggleThinking(headerBtn));
            } else {
                headerBtn.addEventListener('click', () => {
                    thinkingContainer.classList.toggle('collapsed');
                });
            }
        } catch (_) {
            // ignore toggle failures
        }
    }

    return {
        assistantMessageContainer,
        assistantReasoningCount,
        thinkingContainer,
    };
}

function createAssistantToolStep(toolConfig, displayName, effectiveToolName, toolId = '') {
    const step = document.createElement('div');
    step.className = 'thinking-step thinking-step-function-call';
    step.dataset.toolName = effectiveToolName || '';
    step.dataset.toolCallFinalized = 'false';
    if (toolId) {
        step.dataset.toolCallId = toolId;
    }

    const header = document.createElement('div');
    header.className = 'function-call-header';

    const icon = document.createElement('div');
    icon.className = 'function-call-icon';
    icon.innerHTML = (toolConfig.icon && toolConfig.icon()) || Icons?.globe || '';
    header.appendChild(icon);

    const nameEl = document.createElement('span');
    nameEl.className = 'function-call-name';
    nameEl.textContent = displayName;
    header.appendChild(nameEl);
    step.appendChild(header);

    return step;
}

function findAssistantToolStep(thinkingContainer, { toolId = '', toolName = '', includeFinalized = false } = {}) {
    if (!thinkingContainer) return null;

    const steps = Array.from(thinkingContainer.querySelectorAll('.thinking-step-function-call'));
    if (!steps.length) return null;

    if (toolId) {
        const byId = steps.find((step) => step.dataset.toolCallId === toolId);
        if (byId) return byId;
    }

    const candidates = includeFinalized
        ? steps
        : steps.filter((step) => step.dataset.toolCallFinalized !== 'true');

    if (toolName) {
        for (let i = candidates.length - 1; i >= 0; i -= 1) {
            if (candidates[i].dataset.toolName === toolName) {
                return candidates[i];
            }
        }
    }

    return candidates.length ? candidates[candidates.length - 1] : null;
}

function ensureAssistantToolStep(thinkingContainer, { toolConfig, displayName, effectiveToolName, toolId = '' } = {}) {
    if (!thinkingContainer) return null;

    // Empty reasoning segments create a header-only thinking container. Always
    // materialize the collapsible wrapper before adding a tool body so the
    // `collapsed` state can hide the complete tool-call UI. Reparenting also
    // repairs a body produced by older code that inserted it beside the wrapper.
    const thinkingContent = ensureAssistantThinkingContent(thinkingContainer);
    if (!thinkingContent) return null;

    let thinkingBody = thinkingContainer.querySelector('.assistant-thinking-body');
    if (!thinkingBody) {
        thinkingBody = document.createElement('div');
        thinkingBody.className = 'assistant-thinking-body';
    }
    if (thinkingBody.parentNode !== thinkingContent) {
        thinkingContent.appendChild(thinkingBody);
    }

    const existing = findAssistantToolStep(thinkingContainer, { toolId, toolName: effectiveToolName });
    if (existing) {
        if (toolId && !existing.dataset.toolCallId) {
            existing.dataset.toolCallId = toolId;
        }
        return existing;
    }

    const step = createAssistantToolStep(toolConfig, displayName, effectiveToolName, toolId);
    thinkingBody.appendChild(step);
    return step;
}

function normalizeToolPreviewText(rawValue) {
    if (rawValue == null) return '';

    if (typeof rawValue === 'string') {
        const normalized = rawValue.replace(/\r\n/g, '\n');
        const trimmed = normalized.trim();
        if (!trimmed) return normalized;
        try {
            const parsed = JSON.parse(trimmed);
            return JSON.stringify(parsed, null, 2);
        } catch (_) {
            return normalized;
        }
    }

    if (typeof rawValue === 'object') {
        try {
            return JSON.stringify(rawValue, null, 2);
        } catch (_) {
            return String(rawValue);
        }
    }

    return String(rawValue);
}

function isComplexToolPreviewValue(value) {
    if (value == null) return false;

    if (typeof value === 'string') {
        return value.length > 72 || /[\r\n]/.test(value);
    }

    if (Array.isArray(value)) {
        return value.length > 1 || value.some((entry) => isComplexToolPreviewValue(entry));
    }

    if (typeof value === 'object') {
        const entries = Object.entries(value);
        return entries.length > 1 || entries.some(([, entryValue]) => isComplexToolPreviewValue(entryValue));
    }

    return false;
}

function shouldKeepToolPreviewVisible(toolArgs) {
    if (toolArgs == null) return false;

    if (typeof toolArgs === 'string') {
        const normalized = normalizeToolPreviewText(toolArgs);
        return normalized.trim().length > 72 || /[\r\n]/.test(normalized);
    }

    if (Array.isArray(toolArgs) || typeof toolArgs === 'object') {
        return isComplexToolPreviewValue(toolArgs);
    }

    return false;
}

function buildToolArgsObject({ sanitizedToolArgs, toolParamKind, parsedArgs }) {
    if (!sanitizedToolArgs) {
        return null;
    }

    if (typeof sanitizedToolArgs === 'object' && !Array.isArray(sanitizedToolArgs)) {
        return sanitizedToolArgs;
    }

    if (Array.isArray(sanitizedToolArgs)) {
        if (toolParamKind) {
            return { [toolParamKind]: parsedArgs };
        }
        return { arguments: parsedArgs };
    }

    if (typeof sanitizedToolArgs === 'string') {
        try {
            return JSON.parse(sanitizedToolArgs);
        } catch (_) {
            return null;
        }
    }

    return null;
}

function buildToolStepParams(argsObject) {
    const params = [];
    const paramsIndex = new Map();

    const getParamEntry = (rawLabel) => {
        const label = rawLabel ?? '';
        if (!paramsIndex.has(label)) {
            const entry = { label, values: [] };
            paramsIndex.set(label, entry);
            params.push(entry);
        }
        return paramsIndex.get(label);
    };

    const addParam = (label, value) => {
        if (value === undefined || value === null) return;
        const stringValue = Array.isArray(value) || typeof value === 'object'
            ? JSON.stringify(value)
            : String(value);
        getParamEntry(label).values.push(stringValue);
    };

    const formatLabel = (key) => {
        if (!key) return '';
        return key.charAt(0).toUpperCase() + key.slice(1);
    };

    if (argsObject && typeof argsObject === 'object') {
        Object.entries(argsObject).forEach(([key, value]) => {
            const label = formatLabel(key);
            if (Array.isArray(value)) {
                value.forEach((entry) => addParam(label, entry));
            } else {
                addParam(label, value);
            }
        });
    }

    return params.filter((param) => param.values.length);
}

function renderAssistantToolParams(step, argsObject) {
    if (!step) return;

    const existing = step.querySelector('.function-call-params');
    if (existing) {
        existing.remove();
    }

    const paramsToRender = buildToolStepParams(argsObject);
    if (!paramsToRender.length) {
        return;
    }

    const paramsContainer = document.createElement('div');
    paramsContainer.className = 'function-call-params';

    paramsToRender.forEach((param) => {
        if (param.label) {
            const labelEl = document.createElement('span');
            labelEl.className = 'function-call-params-label';
            labelEl.textContent = param.label;
            paramsContainer.appendChild(labelEl);
        }

        param.values.forEach((value) => {
            const valueEl = document.createElement('span');
            valueEl.className = 'function-call-param';
            valueEl.textContent = value;
            paramsContainer.appendChild(valueEl);
        });
    });

    step.appendChild(paramsContainer);
}

function ensureAssistantToolPreview(step) {
    if (!step) return null;

    let preview = step.querySelector('.function-call-live-preview');
    if (preview) {
        return preview;
    }

    preview = document.createElement('div');
    preview.className = 'function-call-live-preview';
    preview.hidden = true;

    const label = document.createElement('div');
    label.className = 'function-call-live-preview-label';
    preview.appendChild(label);

    const viewport = document.createElement('div');
    viewport.className = 'function-call-live-preview-viewport';
    viewport.dataset.autoFollow = 'true';

    const code = document.createElement('pre');
    code.className = 'function-call-live-preview-code';
    viewport.appendChild(code);
    preview.appendChild(viewport);

    // Reuse the same user-intent semantics as the main transcript. This makes
    // a verbose tool-input preview independently scrollable without a stream
    // animation pulling it back to the end.
    window.ChatScrollManager?.bind?.(viewport);

    viewport.addEventListener('scroll', () => {
        if (window.ChatScrollManager) {
            const isFollowing = window.ChatScrollManager.isFollowing(viewport);
            viewport.dataset.autoFollow = isFollowing ? 'true' : 'false';
            preview.classList.toggle('is-manual-scroll', !isFollowing);
            preview.classList.toggle('is-overflowing', viewport.scrollHeight > viewport.clientHeight + 1);
            return;
        }
        if (viewport.dataset.programmaticScroll === 'true') {
            return;
        }
        const remaining = Math.max(viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop, 0);
        viewport.dataset.autoFollow = remaining <= 12 ? 'true' : 'false';
        preview.classList.toggle('is-manual-scroll', viewport.dataset.autoFollow !== 'true');
        preview.classList.toggle('is-overflowing', viewport.scrollHeight > viewport.clientHeight + 1);
    });

    step.appendChild(preview);
    return preview;
}

function stopAssistantToolPreviewScroll(preview) {
    const viewport = preview?.querySelector('.function-call-live-preview-viewport');
    if (!viewport || !viewport._autoScrollFrame) {
        return;
    }
    cancelAnimationFrame(viewport._autoScrollFrame);
    viewport._autoScrollFrame = 0;
}

function refreshAssistantToolPreviewOverflow(preview) {
    const viewport = preview?.querySelector('.function-call-live-preview-viewport');
    if (!preview || !viewport) return;

    const overflow = viewport.scrollHeight > viewport.clientHeight + 1;
    preview.classList.toggle('is-overflowing', overflow);
}

function scheduleAssistantToolPreviewScroll(preview) {
    const viewport = preview?.querySelector('.function-call-live-preview-viewport');
    if (!preview || !viewport) {
        return;
    }

    refreshAssistantToolPreviewOverflow(preview);

    if (window.ChatScrollManager && typeof window.ChatScrollManager.scheduleFollow === 'function') {
        window.ChatScrollManager.scheduleFollow(viewport);
        return;
    }

    if (viewport.dataset.autoFollow !== 'true') {
        return;
    }

    stopAssistantToolPreviewScroll(preview);

    const targetScrollTop = Math.max(viewport.scrollHeight - viewport.clientHeight, 0);
    if (Math.abs(targetScrollTop - viewport.scrollTop) < 1) {
        return;
    }

    const startScrollTop = viewport.scrollTop;
    const delta = targetScrollTop - startScrollTop;
    const duration = Math.min(360, Math.max(120, Math.abs(delta) * 0.8));
    const startTime = performance.now();

    const animate = (timestamp) => {
        const progress = Math.min((timestamp - startTime) / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        viewport.dataset.programmaticScroll = 'true';
        viewport.scrollTop = startScrollTop + delta * eased;
        viewport.dataset.programmaticScroll = 'false';

        if (progress < 1) {
            viewport._autoScrollFrame = requestAnimationFrame(animate);
        } else {
            viewport._autoScrollFrame = 0;
            refreshAssistantToolPreviewOverflow(preview);
        }
    };

    viewport._autoScrollFrame = requestAnimationFrame(animate);
}

function updateAssistantToolPreview(step, { previewText = '', live = false, keepVisible = false } = {}) {
    if (!step) return;

    const preview = ensureAssistantToolPreview(step);
    if (!preview) return;

    const label = preview.querySelector('.function-call-live-preview-label');
    const code = preview.querySelector('.function-call-live-preview-code');
    const viewport = preview.querySelector('.function-call-live-preview-viewport');
    const normalizedText = normalizeToolPreviewText(previewText);
    const shouldShow = Boolean(keepVisible && normalizedText.trim().length);

    step.classList.toggle('is-tool-call-streaming', Boolean(live));

    if (!shouldShow) {
        clearScheduledAssistantToolPreview(step);
        stopAssistantToolPreviewScroll(preview);
        preview.hidden = true;
        preview.classList.remove('is-overflowing', 'is-manual-scroll');
        preview.dataset.previewMode = '';
        if (code) {
            code.textContent = '';
        }
        if (label) {
            label.textContent = '';
        }
        if (viewport) {
            viewport.dataset.autoFollow = 'true';
        }
        return;
    }

    preview.hidden = false;
    preview.dataset.previewMode = live ? 'live' : 'final';
    if (label) {
        label.textContent = live
            ? getStreamText('assistant_tool_live_input_preview', 'Live input preview')
            : getStreamText('assistant_tool_input_preview', 'Input preview');
    }
    if (code) {
        code.textContent = normalizedText;
    }
    if (viewport && live && viewport.dataset.autoFollow !== 'false') {
        viewport.dataset.autoFollow = 'true';
    }
    refreshAssistantToolPreviewOverflow(preview);
    scheduleAssistantToolPreviewScroll(preview);
}

/**
 * Tool arguments can arrive one token at a time. Coalesce their DOM preview
 * into bounded updates instead of replacing an ever-growing <pre> per token.
 */
function scheduleStreamingAssistantToolPreview(step, options) {
    if (!step) return;
    step._pendingToolPreviewOptions = options;
    if (step._toolPreviewTimer) return;
    step._toolPreviewTimer = setTimeout(() => {
        step._toolPreviewTimer = 0;
        const pending = step._pendingToolPreviewOptions;
        step._pendingToolPreviewOptions = null;
        if (pending) updateAssistantToolPreview(step, pending);
    }, 60);
}

/** Flush or discard a pending tool preview before a final tool event. */
function clearScheduledAssistantToolPreview(step) {
    if (!step) return;
    if (step._toolPreviewTimer) {
        clearTimeout(step._toolPreviewTimer);
        step._toolPreviewTimer = 0;
    }
    step._pendingToolPreviewOptions = null;
}

function appendAssistantToolDelta(messageId, last_appended_message_type, assistantReasoningCount, toolMeta = null) {
    const descriptor = toolMeta && typeof toolMeta === 'object' ? toolMeta : {};
    const effectiveToolName = typeof descriptor.name === 'string' ? descriptor.name.trim() : '';
    const toolId = typeof descriptor.id === 'string' ? descriptor.id.trim() : '';
    const delta = typeof descriptor.delta === 'string' ? descriptor.delta : '';
    const hideToolArgs = shouldHideToolArguments(effectiveToolName);

    if (!effectiveToolName || !delta) {
        return {
            assistantReasoningCount,
            appended: false,
        };
    }

    const toolConfig = getToolConfig(effectiveToolName);
    const displayName = getToolDisplayName(toolConfig, effectiveToolName);
    const resolved = ensureAssistantToolThinkingContainer(
        messageId,
        last_appended_message_type,
        assistantReasoningCount,
        effectiveToolName,
        null
    );

    assistantReasoningCount = resolved.assistantReasoningCount;
    const thinkingContainer = resolved.thinkingContainer;
    if (!thinkingContainer) {
        return {
            assistantReasoningCount,
            appended: false,
        };
    }

    updateThinkingHeaderForActivity(thinkingContainer, 'tool', effectiveToolName, null);

    const step = ensureAssistantToolStep(thinkingContainer, {
        toolConfig,
        displayName,
        effectiveToolName,
        toolId,
    });
    if (!step) {
        return {
            assistantReasoningCount,
            appended: false,
        };
    }

    if (toolId && !step.dataset.toolCallId) {
        step.dataset.toolCallId = toolId;
    }
    step.dataset.toolName = effectiveToolName;
    step.dataset.toolCallFinalized = 'false';

    // Canvas and notes arguments can contain an entire generated document.
    // Their deltas must still create the visible tool activity row immediately,
    // but the private/large argument payload stays out of the chat DOM.
    if (hideToolArgs) {
        // Keep only a short private prefix long enough to identify create/edit/
        // view. It is never written to data attributes or rendered as content.
        step._toolActivityArgsBuffer = `${step._toolActivityArgsBuffer || ''}${delta}`.slice(0, 4096);
        const activityArgs = getToolActivityArgs(effectiveToolName, step._toolActivityArgsBuffer);
        updateThinkingHeaderForActivity(thinkingContainer, 'tool', effectiveToolName, activityArgs);
        updateAssistantToolPreview(step, {
            previewText: '',
            live: true,
            keepVisible: false,
        });
    } else {
        // Keep the growing payload off data-* attributes. Attribute writes
        // serialize the full buffer and trigger DOM mutation work per token.
        step._toolArgsBuffer = (step._toolArgsBuffer || step.dataset.toolArgsBuffer || '') + delta;
        scheduleStreamingAssistantToolPreview(step, {
            previewText: step._toolArgsBuffer,
            live: true,
            keepVisible: !shouldHideToolPreview(effectiveToolName),
        });
    }
    syncStreamingMcpAppWidget(messageId, descriptor);

    return {
        assistantReasoningCount,
        appended: true,
    };
}

function processAssistantToolDeltaStreamEvent(messageId, lastAppendedMessageType, assistantReasoningCount, toolMeta = null) {
    const wasLoading = lastAppendedMessageType === 'loading';
    let nextAssistantReasoningCount = assistantReasoningCount;

    if (wasLoading && typeof expandLoading === 'function') {
        nextAssistantReasoningCount = expandLoading(messageId, nextAssistantReasoningCount);
    }

    if (typeof appendAssistantToolDelta === 'function') {
        const toolDeltaUpdate = appendAssistantToolDelta(
            messageId,
            wasLoading ? 'r' : lastAppendedMessageType,
            nextAssistantReasoningCount,
            toolMeta
        );
        const appended = typeof toolDeltaUpdate === 'object'
            ? Boolean(toolDeltaUpdate.appended)
            : toolDeltaUpdate !== nextAssistantReasoningCount;
        nextAssistantReasoningCount = typeof toolDeltaUpdate === 'object'
            ? toolDeltaUpdate.assistantReasoningCount
            : toolDeltaUpdate;
        return {
            assistantReasoningCount: nextAssistantReasoningCount,
            lastAppendedMessageType: appended ? 't' : lastAppendedMessageType,
        };
    }

    return {
        assistantReasoningCount: nextAssistantReasoningCount,
        lastAppendedMessageType,
    };
}


function appendAssistantTool(messageId, last_appended_message_type, assistantReasoningCount, tool=null, tool_name=null, tool_args=null, tool_meta=null) {
    const toolInput = parseToolInput({ tool, tool_name, tool_args });
    const effectiveToolName = toolInput.tool_name
        || tool_name
        || (typeof tool === 'string' ? tool.split('(')[0]?.trim() : '')
        || (typeof tool === 'function' ? tool.name : '')
        || '';
    const parsedArgs = toolInput.tool_args || [];
    const toolParamKind = toolInput.tool_param_kind;
    const resolvedToolArgs = tool_args ?? (toolParamKind ? { [toolParamKind]: parsedArgs } : (parsedArgs.length ? parsedArgs : null));
    const hideToolArgs = shouldHideToolArguments(effectiveToolName);
    const sanitizedToolArgs = hideToolArgs ? null : resolvedToolArgs;
    const toolActivityArgs = hideToolArgs
        ? getToolActivityArgs(effectiveToolName, resolvedToolArgs)
        : sanitizedToolArgs;
    const rawToolId = tool_meta && typeof tool_meta === 'object'
        ? tool_meta.id ?? tool_meta.tool_call_id ?? tool_meta.call_id ?? tool_meta.tool_use_id
        : null;
    const toolId = typeof rawToolId === 'string' ? rawToolId.trim() : '';

    const toolConfig = getToolConfig(effectiveToolName);
    const displayName = getToolDisplayName(toolConfig, effectiveToolName);
    const resolved = ensureAssistantToolThinkingContainer(
        messageId,
        last_appended_message_type,
        assistantReasoningCount,
        effectiveToolName,
        toolActivityArgs
    );
    assistantReasoningCount = resolved.assistantReasoningCount;
    const thinkingContainer = resolved.thinkingContainer;
    if (!thinkingContainer) {
        return assistantReasoningCount;
    }

    // Track this tool call in the thinking container
    addToolCallToThinkingContainer(thinkingContainer, effectiveToolName, toolActivityArgs, toolId);

    // Update header to show current tool action
    updateThinkingHeaderForActivity(thinkingContainer, 'tool', effectiveToolName, toolActivityArgs);

    const step = ensureAssistantToolStep(thinkingContainer, {
        toolConfig,
        displayName,
        effectiveToolName,
        toolId,
    });
    if (!step) {
        return assistantReasoningCount;
    }
    step.dataset.toolCallFinalized = 'true';
    if (toolId) {
        step.dataset.toolCallId = toolId;
    }

    const bufferedToolArgs = step._toolArgsBuffer || step.dataset.toolArgsBuffer || '';
    const hasNewArgsPayload = sanitizedToolArgs !== null && sanitizedToolArgs !== undefined;
    const shouldRenderParams = !hideToolArgs && Boolean(hasNewArgsPayload || bufferedToolArgs);
    const toolArgsSource = hasNewArgsPayload ? sanitizedToolArgs : bufferedToolArgs;
    const argsObject = shouldRenderParams
        ? buildToolArgsObject({ sanitizedToolArgs: toolArgsSource, toolParamKind, parsedArgs })
        : null;

    renderAssistantToolParams(step, argsObject);

    const toolPreviewText = shouldRenderParams ? normalizeToolPreviewText(toolArgsSource) : '';
    clearScheduledAssistantToolPreview(step);
    if (hasNewArgsPayload) {
        step._toolArgsBuffer = toolPreviewText;
    }
    delete step._toolActivityArgsBuffer;
    updateAssistantToolPreview(step, {
        previewText: toolPreviewText,
        live: false,
        keepVisible: !shouldHideToolPreview(effectiveToolName) && shouldKeepToolPreviewVisible(toolArgsSource),
    });
    syncStreamingMcpAppWidget(messageId, {
        ...(tool_meta && typeof tool_meta === 'object' ? tool_meta : {}),
        id: toolId || (tool_meta && typeof tool_meta === 'object' ? String(tool_meta.id || '').trim() : ''),
        name: effectiveToolName,
        args: toolArgsSource,
    });
    delete step._toolArgsBuffer;
    delete step.dataset.toolArgsBuffer;

    return assistantReasoningCount;
}


function resolveToolErrorDisplayMessage(descriptor) {
    const errorCode = String(descriptor?.error_code || '').trim();
    const translatedErrors = {
        automations_feature_disabled: ['assistant_tool_error_automations_disabled', 'Automations are disabled for your group.'],
        automations_invalid_operation: ['assistant_tool_error_automations_operation', 'Choose a valid Automations operation.'],
        automations_webhook_user_managed: ['assistant_tool_error_automations_webhook', 'Webhook triggers must be managed in the Automations interface.'],
        automations_missing_title: ['assistant_tool_error_automations_title', 'A title is required to create an automation.'],
        automations_missing_prompt: ['assistant_tool_error_automations_prompt', 'A prompt is required to create an automation.'],
        automations_missing_model_id: ['assistant_tool_error_automations_model', 'A model is required to create an automation.'],
        automations_missing_automation_id: ['assistant_tool_error_automations_id', 'An automation ID is required for this operation.'],
        automations_invalid_is_active: ['assistant_tool_error_automations_active', 'The active state must be true or false.'],
        automations_invalid_arguments: ['assistant_tool_error_automations_inputs', 'Check the Automations inputs and try again.'],
    };
    const translated = translatedErrors[errorCode];
    if (translated) {
        return getStreamText(translated[0], translated[1]);
    }
    if (!errorCode) {
        return getStreamText('assistant_tool_error_generic', 'An error occurred during tool execution.');
    }
    return getStreamText('assistant_tool_error_generic', 'An error occurred during tool execution.');
}

function applyAssistantToolError(messageId, descriptor = {}, { announce = true } = {}) {
    const assistantMessageContainer = document.getElementById('a-' + messageId);
    if (!assistantMessageContainer) return false;

    const toolName = String(descriptor?.name || '').trim();
    const toolId = String(descriptor?.id || descriptor?.tool_call_id || '').trim();
    const thinkingContainers = Array.from(
        assistantMessageContainer.querySelectorAll('.assistant-thinking')
    ).reverse();
    let matchedThinkingContainer = null;
    let step = null;
    for (const thinkingContainer of thinkingContainers) {
        step = findAssistantToolStep(thinkingContainer, {
            toolId,
            toolName,
            includeFinalized: true,
        });
        if (step) {
            matchedThinkingContainer = thinkingContainer;
            break;
        }
    }
    if (!step || !matchedThinkingContainer) return false;

    const existing = step.querySelector('.function-call-error');
    const errorElement = existing || document.createElement('div');
    errorElement.className = 'function-call-error';
    errorElement.textContent = resolveToolErrorDisplayMessage(descriptor);
    if (announce) {
        errorElement.setAttribute('role', 'alert');
    } else if (errorElement.hasAttribute('role')) {
        errorElement.removeAttribute('role');
    }
    if (!existing) {
        step.appendChild(errorElement);
    }
    step.classList.add('is-tool-call-failed');

    const failureLabel = getToolFailedText(toolName || step.dataset.toolName, null);
    matchedThinkingContainer.dataset.toolFailureStatus = 'failed';
    matchedThinkingContainer.dataset.toolFailureLabel = failureLabel;
    const headerSpan = matchedThinkingContainer
        .querySelector('.assistant-thinking-title')
        ?.querySelector('span');
    if (headerSpan) {
        headerSpan.textContent = failureLabel;
        headerSpan.classList.remove('assistant-thinking-shimmer');
        headerSpan.dataset.thinkingType = 'tool-failed';
    }
    return true;
}

function parseToolErrorDescriptorFromResultBlock(block) {
    if (!block || block.type !== 'tool_call_result') return null;
    let payload = block.content;
    if (typeof payload === 'string') {
        try {
            payload = JSON.parse(payload);
        } catch (_) {
            return null;
        }
    }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null;
    const payloadKeys = Object.keys(payload);
    const error = String(payload.error || '').trim();
    if (!error || payloadKeys.some((key) => !['error', 'error_code', 'retry_allowed'].includes(key))) {
        return null;
    }
    const meta = block.meta && typeof block.meta === 'object' ? block.meta : {};
    return {
        id: String(meta.tool_call_id || '').trim(),
        name: String(meta.tool_name || meta.name || '').trim(),
        error,
        error_code: String(payload.error_code || '').trim(),
    };
}
