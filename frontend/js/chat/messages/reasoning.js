function appendStreamingReasoningText(element, text) {
    const normalizedText = String(text || '');
    if (!element || !normalizedText) return false;
    const nextRawContent = getAssistantThinkingRawContent(element) + normalizedText;
    setAssistantThinkingContent(element, nextRawContent);
    return normalizedText.includes('*');
}

function appendAssistantReasoning(messageId, reasoning, last_appended_message_type, assistantReasoningCount) {
    const assistantMessageContainer = document.getElementById('a-' + messageId);
    const normalizedReasoning = typeof reasoning === 'string'
        ? reasoning
        : (reasoning == null ? '' : String(reasoning));
    const parsedReasoning = parseLeadingTitle(normalizedReasoning);
    const reasoningBodyText = parsedReasoning ? (parsedReasoning.rest || '') : normalizedReasoning;
    const hasBodyContent = Boolean(reasoningBodyText && reasoningBodyText.trim().length);

    if (last_appended_message_type == "r" || last_appended_message_type == "t") {
        if (last_appended_message_type == "t") {
            const thinkingContainer = document.getElementById('at-' + assistantReasoningCount + '-' + messageId);
            // Update header back to "Thinking" when reasoning comes after a tool call
            updateThinkingHeaderForActivity(thinkingContainer, 'thinking', null, null);
            if (!hasBodyContent && !(parsedReasoning && parsedReasoning.title)) {
                return assistantReasoningCount;
            }
            const body = ensureAssistantThinkingBody(thinkingContainer);
            if (!body) {
                return assistantReasoningCount;
            }
            const step = document.createElement('div');
            step.className = 'thinking-step';
            
            const stepHeader = document.createElement('div');
            stepHeader.className = 'thinking-step-header';
            const stepTitle = document.createElement('span');
            stepTitle.className = 'thinking-step-title';
            if (parsedReasoning) {
                stepTitle.textContent = parsedReasoning.title;
                stepHeader.appendChild(stepTitle);
            }
            
            const stepContent = document.createElement('div');
            stepContent.className = 'thinking-step-content';
            stepContent.id = 'atc-' + assistantReasoningCount + '-' + messageId;
            setAssistantThinkingContent(stepContent, hasBodyContent ? reasoningBodyText : '');
            if (parsedReasoning) {
                step.appendChild(stepHeader);
            }
            // Keep an empty content node for title-only updates. A later
            // chunk may contain the opening half of the next Markdown title,
            // and that fragment needs a local buffer so it can be promoted to
            // another chronological reasoning step.
            step.appendChild(stepContent);
            body.appendChild(step);
            
            if (parsedReasoning && parsedReasoning.title) {
                setAssistantThinkingHeaderTitle(thinkingContainer, parsedReasoning.title);
            }
        } else { 
            const thinkingContainer = document.getElementById('at-' + assistantReasoningCount + '-' + messageId);
            if (!hasBodyContent && !(parsedReasoning && parsedReasoning.title)) {
                return assistantReasoningCount;
            }
            ensureInitialThinkingStep(thinkingContainer, messageId, assistantReasoningCount);

            const placeholderContent = thinkingContainer.querySelector('.thinking-step-content[data-placeholder="true"]');
            if (placeholderContent) {
                placeholderContent.removeAttribute('data-placeholder');
                if (parsedReasoning) {
                    const placeholderStep = placeholderContent.closest('.thinking-step');
                    if (placeholderStep && placeholderStep.parentElement) {
                        placeholderStep.parentElement.removeChild(placeholderStep);
                    }
                } else {
                    const mayContainCompletedTitle = appendStreamingReasoningText(placeholderContent, reasoningBodyText);
                    // Check if the accumulated content now contains an embedded title
                    if (mayContainCompletedTitle) {
                        checkAndSplitStepForEmbeddedTitle(thinkingContainer, messageId, assistantReasoningCount);
                    }
                    return assistantReasoningCount;
                }
            }

            if (parsedReasoning) {
                const body = ensureAssistantThinkingBody(thinkingContainer);
                if (body) {
                    const step = document.createElement('div');
                    step.className = 'thinking-step';
                    const stepHeader = document.createElement('div');
                    stepHeader.className = 'thinking-step-header';
                    const stepTitle = document.createElement('span');
                    stepTitle.className = 'thinking-step-title';
                    stepTitle.textContent = parsedReasoning.title;
                    stepHeader.appendChild(stepTitle);
                    const stepContent = document.createElement('div');
                    stepContent.className = 'thinking-step-content';
                    setAssistantThinkingContent(stepContent, hasBodyContent ? reasoningBodyText : '');
                    step.appendChild(stepHeader);
                    // Title-only steps retain an empty content node so
                    // fragmented subsequent titles are accumulated on the
                    // correct step instead of an earlier reasoning block.
                    step.appendChild(stepContent);
                    body.appendChild(step);
                    setAssistantThinkingHeaderTitle(thinkingContainer, parsedReasoning.title);
                }
            } else {
                const contents = thinkingContainer.querySelectorAll('.thinking-step-content');
                const lastContent = contents.length ? contents[contents.length - 1] : null;
                if (lastContent) {
                    const mayContainCompletedTitle = appendStreamingReasoningText(lastContent, reasoningBodyText);
                    // Check if the accumulated content now contains an embedded title
                    if (mayContainCompletedTitle) {
                        checkAndSplitStepForEmbeddedTitle(thinkingContainer, messageId, assistantReasoningCount);
                    }
                }
            }
        }
    } else {
        assistantReasoningCount++;
        const thinkingContainer = document.createElement('div');
        thinkingContainer.id = 'at-' + assistantReasoningCount + '-' + messageId;
        thinkingContainer.className = 'assistant-thinking collapsed';
        
        const headerBtn = document.createElement('button');
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
        thinkingContainer.appendChild(headerBtn);
        
        if (hasBodyContent) {
            const thinkingContent = document.createElement('div');
            thinkingContent.className = 'assistant-thinking-content';
            const thinkingBody = document.createElement('div');
            thinkingBody.className = 'assistant-thinking-body';
            
            const step = document.createElement('div');
            step.className = 'thinking-step';
            
            if (parsedReasoning) {
                const stepHeader = document.createElement('div');
                stepHeader.className = 'thinking-step-header';
                const stepTitle = document.createElement('span');
                stepTitle.className = 'thinking-step-title';
                stepTitle.textContent = parsedReasoning.title;
                stepHeader.appendChild(stepTitle);
                step.appendChild(stepHeader);
            }
            
            const stepContent = document.createElement('div');
            stepContent.className = 'thinking-step-content';
            stepContent.id = 'atc-' + assistantReasoningCount + '-' + messageId;
            setAssistantThinkingContent(stepContent, reasoningBodyText);
            step.appendChild(stepContent);
            
            thinkingBody.appendChild(step);
            thinkingContent.appendChild(thinkingBody);
            thinkingContainer.appendChild(thinkingContent);
        }
        
        appendBeforeAssistantList(assistantMessageContainer, thinkingContainer);
        
        const chatAreaContainer = document.getElementById('chatAreaContainer');
        if (chatAreaContainer && assistantMessageContainer && !assistantMessageContainer.parentElement) {
            chatAreaContainer.appendChild(assistantMessageContainer);
        }
        try {
            if (typeof toggleThinking === 'function') {
                headerBtn.addEventListener('click', () => toggleThinking(headerBtn));
            } else {
                headerBtn.addEventListener('click', () => {
                    thinkingContainer.classList.toggle('collapsed');
                });
            }
        } catch (_) {
            // No-op if thinking.js not available
        }
        if (parsedReasoning && parsedReasoning.title) {
            setAssistantThinkingHeaderTitle(thinkingContainer, parsedReasoning.title);
        }
        if (!hasBodyContent) {
            return assistantReasoningCount;
        }
        if (parsedReasoning && parsedReasoning.title) {
            setAssistantThinkingHeaderTitle(thinkingContainer, parsedReasoning.title);
        }
        // Check if the initial content has any embedded/first titles to detect
        checkAndSplitStepForEmbeddedTitle(thinkingContainer, messageId, assistantReasoningCount);
    }
    applyAssistantMessageAccessibility(assistantMessageContainer, { messageId, streaming: true });
    return assistantReasoningCount;
}



function appendAssistantReasoningFinish(messageId, time, assistantReasoningCount) {
    if (assistantReasoningCount <= 0) return;

    const assistantThinking = document.getElementById('at-' + assistantReasoningCount + '-' + messageId);
    if (!assistantThinking) return;

    const numericDuration = Number(time);
    
    // Get tool calls from the thinking container
    const toolCalls = getToolCallsFromThinkingContainer(assistantThinking);
    
    // Generate the appropriate final header text
    const headerText = getThinkingBlockFinalHeader(toolCalls, numericDuration);

    const headerSpan = assistantThinking.querySelector('.assistant-thinking-title span');
    // A media failure is an explicit terminal outcome. The following model
    // explanation must not rewrite it as a successful "Generated ..." label.
    if (assistantThinking.dataset.mediaGenerationStatus === 'failed'
        || assistantThinking.dataset.toolFailureStatus === 'failed'
        || headerSpan?.dataset.thinkingType === 'tool-failed') {
        if (headerSpan) {
            const failureLabel = String(
                assistantThinking.dataset.toolFailureLabel
                || assistantThinking.dataset.mediaGenerationFailureLabel
                || ''
            ).trim();
            if (failureLabel) {
                headerSpan.textContent = failureLabel;
            }
            headerSpan.classList.remove('assistant-thinking-shimmer');
            headerSpan.dataset.thinkingType = 'tool-failed';
        }
        return;
    }
    if (headerSpan) {
        headerSpan.classList.remove('assistant-thinking-shimmer');
        // Set thinking type based on whether there were tool calls
        headerSpan.dataset.thinkingType = toolCalls.length > 0 ? 'tool-done' : 'done';
        headerSpan.textContent = headerText;
    }

}

