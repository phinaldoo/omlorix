const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const {
    STREAM_MESSAGE_SCRIPT_URLS,
    readStreamMessagesSource,
} = require('./messages/source.cjs');
const { readSendMessageSource } = require('./sending/source.cjs');

const streamMessagesSource = readStreamMessagesSource();

test('message renderer scripts load completely and in dependency order', () => {
    for (const pageName of ['index.html', 'chat_share.html']) {
        const markup = readFrontendSource(path.join(__dirname, '..', '..', pageName), 'utf8');
        const uncommentedMarkup = markup.replace(/<!--[\s\S]*?-->/g, '');
        const scriptSources = Array.from(
            uncommentedMarkup.matchAll(/<script\b[^>]*\bsrc\s*=\s*(["'])([^"']+)\1[^>]*>/gi),
            (match) => match[2],
        );
        const scriptIndexes = STREAM_MESSAGE_SCRIPT_URLS.map((scriptUrl) => {
            assert.equal(
                scriptSources.filter((source) => source === scriptUrl).length,
                1,
                `${scriptUrl} must occur once in ${pageName}`,
            );
            return scriptSources.indexOf(scriptUrl);
        });

        assert.deepEqual(scriptIndexes, [...scriptIndexes].sort((left, right) => left - right), pageName);
    }
});

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}`);
    assert.notEqual(start, -1, `${functionName} not found`);

    const paramsStart = source.indexOf('(', start);
    let paramsDepth = 0;
    let bodyStart = -1;
    for (let index = paramsStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '(') {
            paramsDepth += 1;
        } else if (char === ')') {
            paramsDepth -= 1;
            if (paramsDepth === 0) {
                bodyStart = source.indexOf('{', index);
                break;
            }
        }
    }
    assert.notEqual(bodyStart, -1, `${functionName} body start not found`);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') {
            depth += 1;
        } else if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }

    throw new Error(`${functionName} body was not closed`);
}

/**
 * Extract a top-level object constant so focused tests can execute the real
 * display configuration without loading the browser-only chat module.
 */
function extractObjectConstant(source, constantName) {
    const start = source.indexOf(`const ${constantName} =`);
    assert.notEqual(start, -1, `${constantName} not found`);

    const bodyStart = source.indexOf('{', start);
    assert.notEqual(bodyStart, -1, `${constantName} body start not found`);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') {
            depth += 1;
        } else if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 2);
            }
        }
    }

    throw new Error(`${constantName} body was not closed`);
}

test('message edit quick-picks tolerate menus without quick-pick hooks', () => {
    const source = streamMessagesSource;
    const requiredHookGuard = /if \(!picker \|\| !picker\.list \|\| !picker\.empty \|\| !picker\.scrollRegion\) \{\s*return;\s*\}/;

    assert.match(extractFunction(source, 'loadUserMessageEditQuickpickFiles'), requiredHookGuard);
    assert.match(extractFunction(source, 'loadUserMessageEditChatReferences'), requiredHookGuard);
    assert.match(extractFunction(source, 'renderUserMessageEditFilesQuickpick'), requiredHookGuard);
    assert.match(extractFunction(source, 'renderUserMessageEditChatReferencesQuickpick'), requiredHookGuard);
});

test('assistant generated files register in the chat Files dropdown', async () => {
    const source = streamMessagesSource;
    assert.match(extractFunction(source, 'appendAssistantFile'), /registerGeneratedAssistantFile\(/);
    assert.match(extractFunction(source, 'renderAssistantFileBlock'), /registerGeneratedAssistantFile\(/);
    const registrations = [];
    const opened = [];
    const context = {
        window: {
            canvasFilesDropdown: {
                registerFile: (...args) => registrations.push(args),
            },
        },
        FilesPreview: {
            isOpen: false,
            activeFileId: null,
            close: () => {},
            open: async (file) => opened.push(file),
        },
        normalizeChatFileForPreview: (file) => ({
            ...file,
            file_type: 'text/html',
            meta: { original_filename: 'index.html' },
        }),
        shouldSkipCanvasAssistantFile: () => false,
        downloadChatFileById: async () => {},
        getStreamText: (_key, fallback) => fallback,
        notifyError: () => {},
        console,
    };
    const { registerGeneratedAssistantFile } = vm.runInNewContext(
        `${extractFunction(source, 'registerGeneratedAssistantFile')}
({ registerGeneratedAssistantFile });`,
        context,
        { filename: 'streamMessages.generatedFilesDropdown.js' },
    );

    assert.equal(registerGeneratedAssistantFile('file-1', { file_id: 'file-1' }), true);
    assert.equal(registrations.length, 1);
    assert.equal(registrations[0][0], 'file-1');
    assert.equal(registrations[0][1], 'index.html');
    assert.equal(registrations[0][2], 'file');

    registrations[0][3]();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(opened.length, 1);
    assert.equal(opened[0].file_id, 'file-1');
});

test('assistant SVG files use the vector preview path even with generic MIME metadata', () => {
    const source = streamMessagesSource;
    const { resolveAssistantFileType, isDisplayableImageType } = vm.runInNewContext(
        [
            extractFunction(source, 'resolveAssistantFileType'),
            extractFunction(source, 'isDisplayableImageType'),
            '({ resolveAssistantFileType, isDisplayableImageType });',
        ].join('\n'),
        {},
        { filename: 'streamMessages.svgPreview.js' },
    );

    const inferredType = resolveAssistantFileType('application/octet-stream', 'apple-qr.SVG');
    assert.equal(inferredType, 'image/svg+xml');
    assert.equal(isDisplayableImageType(inferredType), true);
    assert.equal(resolveAssistantFileType('image/svg+xml; charset=utf-8', 'vector'), 'image/svg+xml');
});

test('historical SVG rendering passes inferred MIME metadata to the inline preview', () => {
    const source = streamMessagesSource;
    const renderedFiles = [];
    const registeredFiles = [];
    const assistantMessageContainer = {};
    const context = {
        window: {},
        document: {
            getElementById: () => assistantMessageContainer,
        },
        console,
        finalizeThinkingBlocks: () => {},
        shouldSkipCanvasAssistantFile: () => false,
        resolveAssistantFileType: (mimeType, fileName) => (
            String(fileName || '').toLowerCase().endsWith('.svg')
                ? 'image/svg+xml'
                : String(mimeType || '').toLowerCase()
        ),
        registerGeneratedAssistantFile: (_fileId, fileData) => registeredFiles.push(fileData),
        isDisplayableImageType: (mimeType) => mimeType === 'image/svg+xml',
        createAssistantInlineImage: (_fileId, fileData) => {
            renderedFiles.push(fileData);
            return {};
        },
        appendBeforeAssistantList: () => {},
        refreshUnsupportedFileWarningsFromState: () => {},
    };
    const { renderAssistantFileBlock } = vm.runInNewContext(
        `${extractFunction(source, 'renderAssistantFileBlock')}
({ renderAssistantFileBlock });`,
        context,
        { filename: 'streamMessages.historicalSvg.js' },
    );

    renderAssistantFileBlock('message-1', 'file-1', {
        file_type: 'application/octet-stream',
        meta: { original_filename: 'legacy-vector.svg' },
    });

    assert.equal(renderedFiles.length, 1);
    assert.equal(renderedFiles[0].file_type, 'image/svg+xml');
    assert.equal(renderedFiles[0].meta.mime_type, 'image/svg+xml');
    assert.equal(registeredFiles[0].file_type, 'image/svg+xml');
});

test('SVG preview CSS expands tiny intrinsic images on inline and fullscreen surfaces', () => {
    const chatCss = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'chat.css'), 'utf8');
    const filesCss = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'files.css'), 'utf8');

    assert.match(chatCss, /\.assistant-inline-image\[data-file-type="image\/svg\+xml"\][^{]*\{[^}]*width:\s*min\(100%,\s*512px\)/s);
    assert.match(chatCss, /\.assistant-inline-image\[data-file-type="image\/svg\+xml"\][\s\S]*?\.assistant-inline-image-img\s*\{[^}]*width:\s*100%/);
    assert.match(filesCss, /\.files-preview-image\[data-file-type="image\/svg\+xml"\]\s*\{[^}]*width:\s*100%/s);
});

test('MCP tool display names hide the backend routing digest', () => {
    const source = streamMessagesSource;
    const { formatToolDisplayName } = vm.runInNewContext(
        `${extractFunction(source, 'formatToolDisplayName')}
({ formatToolDisplayName });`,
        {},
        { filename: 'streamMessages.toolDisplayName.js' },
    );

    assert.equal(
        formatToolDisplayName('mcp_exalidraw_read_me_2ffec9e2'),
        'MCP Exalidraw Read Me',
    );
    assert.equal(formatToolDisplayName('web_search'), 'Web Search');
    assert.equal(
        formatToolDisplayName('custom_report_deadbeef'),
        'Custom Report Deadbeef',
    );
});

test('deep research thinking titles never disclose the research query', () => {
    const source = streamMessagesSource;
    const titleHelpers = vm.runInNewContext(
        [
            extractObjectConstant(source, 'TOOL_HEADER_CONFIG'),
            extractObjectConstant(source, 'TOOL_NAME_ALIASES'),
            extractFunction(source, 'getStreamText'),
            extractFunction(source, 'getStreamTextFormatted'),
            extractFunction(source, 'normalizeToolNameForDisplay'),
            extractFunction(source, 'getToolConfig'),
            extractFunction(source, 'getArtifactToolOperation'),
            extractFunction(source, 'getToolActivityConfig'),
            extractFunction(source, 'formatToolDisplayName'),
            extractFunction(source, 'getToolDisplayName'),
            extractFunction(source, 'getToolInProgressText'),
            extractFunction(source, 'getToolCompletedText'),
            '({ getToolInProgressText, getToolCompletedText });',
        ].join('\n'),
        {
            Icons: { globe: '<svg></svg>' },
            window: {},
        },
        { filename: 'streamMessages.deepResearchTitle.js' },
    );
    const privateQuery = 'Confidential acquisition strategy for Example Corp';
    const args = { query: privateQuery };

    const inProgressTitle = titleHelpers.getToolInProgressText('deep_research', args);
    const completedTitle = titleHelpers.getToolCompletedText('deep_research', args);

    assert.equal(inProgressTitle, 'Performing a deep research');
    assert.equal(completedTitle, 'Performed a deep research');
    assert.ok(!inProgressTitle.includes(privateQuery));
    assert.ok(!completedTitle.includes(privateQuery));

    // Keep every locale on the query-free contract. Removing the old
    // placeholder keys prevents a future config change from reintroducing the
    // research request through a translated title.
    const i18nRoot = path.join(__dirname, '..', '..', 'i18n');
    const localeDirectories = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);
    localeDirectories.forEach((locale) => {
        const translations = JSON.parse(readFrontendSource(path.join(i18nRoot, locale, 'index.json'), 'utf8'));
        assert.equal(
            typeof translations.assistant_tool_deep_research_in_progress,
            'string',
            `${locale} is missing the in-progress deep research title`,
        );
        assert.equal(
            typeof translations.assistant_tool_deep_research_completed,
            'string',
            `${locale} is missing the completed deep research title`,
        );
        assert.equal(
            translations.assistant_tool_deep_research_in_progress_with_arg,
            undefined,
            `${locale} still has a query-bearing in-progress title`,
        );
        assert.equal(
            translations.assistant_tool_deep_research_completed_with_arg,
            undefined,
            `${locale} still has a query-bearing completed title`,
        );
    });
});

function createClassList(initial = []) {
    const values = new Set(initial);
    return {
        add(...names) {
            names.forEach((name) => values.add(name));
        },
        remove(...names) {
            names.forEach((name) => values.delete(name));
        },
        contains(name) {
            return values.has(name);
        },
    };
}

class FakeElement {
    constructor(tagName) {
        this.tagName = tagName.toUpperCase();
        this.children = [];
        this.attributes = {};
        this.classList = createClassList();
        this.dataset = {};
        this.parentNode = null;
        this._textContent = '';
        this.innerHtmlAssignments = [];
        this.hidden = false;
        this.scrollHeight = 0;
        this.offsetHeight = 0;
        this.eventListeners = {};
    }

    set className(value) {
        const className = String(value || '');
        this.attributes.class = className;
        this.classList = createClassList(className.split(/\s+/).filter(Boolean));
    }

    get className() {
        return this.attributes.class || '';
    }

    set textContent(value) {
        this._textContent = String(value || '');
        this.children = [];
    }

    get textContent() {
        return this._textContent;
    }

    set innerHTML(value) {
        this.innerHtmlAssignments.push(String(value || ''));
        this._textContent = String(value || '');
    }

    get innerHTML() {
        return this._textContent;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    insertAdjacentText(position, value) {
        assert.equal(position, 'beforeend');
        this._textContent += String(value || '');
    }

    getAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this.attributes, name)
            ? this.attributes[name]
            : null;
    }

    hasAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this.attributes, name);
    }

    appendChild(child) {
        // Match DOM reparenting semantics so structural tests can verify that
        // helpers move malformed nodes instead of leaving duplicate children.
        if (child.parentNode) {
            const previousIndex = child.parentNode.children.indexOf(child);
            if (previousIndex !== -1) {
                child.parentNode.children.splice(previousIndex, 1);
            }
        }
        this.children.push(child);
        child.parentNode = this;
        child.parentElement = this;
        return child;
    }

    remove() {
        if (!this.parentNode) {
            return;
        }
        const index = this.parentNode.children.indexOf(this);
        if (index !== -1) {
            this.parentNode.children.splice(index, 1);
        }
        this.parentNode = null;
        this.parentElement = null;
    }

    addEventListener(type, listener) {
        this.eventListeners[type] = this.eventListeners[type] || [];
        this.eventListeners[type].push(listener);
    }

    insertBefore(child, referenceNode) {
        const index = this.children.indexOf(referenceNode);
        if (index === -1) {
            return this.appendChild(child);
        }
        this.children.splice(index, 0, child);
        child.parentNode = this;
        child.parentElement = this;
        return child;
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
        const wantsClass = selector.startsWith('.');
        const className = wantsClass ? selector.slice(1) : '';
        const tagName = wantsClass ? '' : selector.toUpperCase();
        const matches = [];
        const visit = (element) => {
            if ((wantsClass && element.classList.contains(className)) || (!wantsClass && element.tagName === tagName)) {
                matches.push(element);
            }
            element.children.forEach(visit);
        };
        this.children.forEach(visit);
        return matches;
    }
}

test('inline attachment grids respond to chat container width, not viewport width', () => {
    const source = streamMessagesSource;
    const chatCss = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'chat.css'), 'utf8');
    const inlineElementsCss = readFrontendSource(
        path.join(__dirname, '..', '..', 'css', 'chat', 'chatBox', 'chatBoxInlineElements.css'),
        'utf8',
    );
    const chatBoxCss = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'chatBox', 'chatBox.css'), 'utf8');
    const splitScreenCss = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'splitScreen.css'), 'utf8');
    const appendUserFilesBody = extractFunction(source, 'appendUserFiles');

    assert.doesNotMatch(appendUserFilesBody, /matchMedia\(/);
    assert.doesNotMatch(chatCss, /inline-files|chat-file-card/);
    assert.doesNotMatch(inlineElementsCss, /@media \(max-width: 900px\)[\s\S]*\.inline-files\.active/);
    assert.doesNotMatch(inlineElementsCss, /@media \(max-width: 450px\)[\s\S]*\.inline-files\.active/);

    assert.match(chatCss, /\.chat-area[\s\S]*container-name: chat-files-layout/);
    assert.match(chatCss, /\.user-message-edit-chat-box[\s\S]*container-name: chat-files-layout/);
    assert.match(chatBoxCss, /\.chat-box[\s\S]*container-name: chat-files-layout/);
    assert.match(splitScreenCss, /\.split-chat-area[\s\S]*container-name: chat-files-layout/);
    assert.match(inlineElementsCss, /@container chat-files-layout \(max-width: 620px\)[\s\S]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
    assert.match(inlineElementsCss, /@container chat-files-layout \(max-width: 360px\)[\s\S]*grid-template-columns: repeat\(1, minmax\(0, 1fr\)\)/);
    assert.match(inlineElementsCss, /@container chat-files-layout \(min-width: 621px\)[\s\S]*inline-files-align-placeholder/);
});

test('user message bubbles cap intrinsic Markdown width at the chat column', () => {
    const chatCss = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'chat.css'), 'utf8');
    const userMessageRule = chatCss.match(/\.user-message\s*\{[^}]*\}/)?.[0] || '';

    // Non-wrapping code and other wide Markdown children must scroll inside
    // the bubble rather than increasing the width of its right-aligned flex item.
    assert.match(userMessageRule, /width:\s*fit-content/);
    assert.match(userMessageRule, /max-width:\s*100%/);
});

test('chat references remain message context and never render as transcript attachment cards', () => {
    const source = streamMessagesSource;
    const chatBoxCss = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'chatBox', 'chatBox.css'), 'utf8');
    const appendUserContentBody = extractFunction(source, 'appendUserContent');
    const rerenderUserMessageFilesBody = extractFunction(source, 'rerenderUserMessageFiles');

    // Live and historical messages both pass through appendUserContent. The
    // references stay available for editing, but only ordinary files are
    // mounted below the visible user message.
    assert.match(appendUserContentBody, /chatReferences:\s*Array\.isArray\(chatReferences\)/);
    assert.match(appendUserContentBody, /appendUserFiles\(messageId, files, columnWrapper\)/);
    assert.doesNotMatch(appendUserContentBody, /appendUserChatReferences/);

    // Saving an edited message rerenders the same file-only transcript surface.
    assert.match(rerenderUserMessageFilesBody, /appendUserFiles\(messageId, files, columnWrapper\)/);
    assert.doesNotMatch(rerenderUserMessageFilesBody, /chatReferences|inline-chat-reference-element/);
    assert.doesNotMatch(source, /function appendUserChatReferences/);

    // Composer references inherit the ordinary file tile instead of reviving
    // a feature-specific surface through CSS.
    assert.doesNotMatch(chatBoxCss, /\.inline-chat-reference-element(?::hover)?\s*\{/);
});

test('subagent launcher omits event counts and its transcript modal keeps a stable large size', () => {
    const source = streamMessagesSource;
    const chatCss = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'chat.css'), 'utf8');
    const launcherBody = extractFunction(source, 'ensureSubagentLauncher');
    const launcherUpdateBody = extractFunction(source, 'updateSubagentLauncher');
    const launcherRule = chatCss.match(/\.subagent-launcher\s*\{[^}]*\}/)?.[0] || '';

    // Streaming volume is an implementation detail and should not compete
    // with the model name and status in the compact chat launcher.
    assert.doesNotMatch(launcherBody, /subagent-launcher-count|subagent_event_count/);
    assert.doesNotMatch(launcherUpdateBody, /subagent-launcher-count|subagent_event_count/);

    // The launcher is intentionally substantial, while the dialog opts into
    // the shared large, fixed-size shell so incoming content cannot resize it.
    assert.match(launcherRule, /width:\s*min\(100%, 560px\)/);
    assert.match(launcherRule, /min-height:\s*70px/);
    assert.match(source, /dialog\.className = 'subagent-modal shared-modal shared-modal--large shared-modal--fixed'/);
    assert.doesNotMatch(chatCss, /\.subagent-modal\s*\{[^}]*\b(?:width|height|max-height)\s*:/);
});

test('live subagent events accept the nested subagent run identifier', () => {
    const source = streamMessagesSource;
    const state = { events: [], modalChat: null };
    let resolvedRunId = null;
    const context = {
        getSubagentState(_messageId, runId) {
            resolvedRunId = runId;
            return state;
        },
        updateSubagentStateMeta() {},
        ensureSubagentLauncher() {},
        normalizeSubagentEvent(eventName, data) {
            return { eventName, data };
        },
        updateSubagentLauncher() {},
        updateSubagentModalHeader() {},
        renderSubagentModalTranscript() {},
        renderSubagentEventAsChat() {},
        refreshAssistantStatsForMessage() {},
        window: {},
    };
    const handleSubagentStreamEvent = vm.runInNewContext(
        `${extractFunction(source, 'handleSubagentStreamEvent')}\nhandleSubagentStreamEvent;`,
        context,
        { filename: 'streamMessages.liveSubagent.js' },
    );

    handleSubagentStreamEvent(
        {
            event: 'message_delta',
            data: { subagent_run_id: 'run-1', content: 'Hello' },
        },
        'message-1',
    );

    assert.equal(resolvedRunId, 'run-1');
    assert.equal(state.events.length, 1);
    assert.equal(state.events[0].data.content, 'Hello');
});

test('persisted subagent replay rebinds a detached optimistic message state', () => {
    const source = streamMessagesSource;
    const persistedReplayBody = extractFunction(source, 'renderPersistedSubagentBlock');
    const optimisticBindingBody = extractFunction(source, 'bindOptimisticMessageToServerMessage');
    const context = {
        document: {
            querySelector() {
                return null;
            },
            body: {
                classList: {
                    remove() {},
                },
            },
        },
    };
    const helpers = vm.runInNewContext(
        `const subagentRunStates = new Map();
let activeSubagentModalState = null;
${extractFunction(source, 'releaseSubagentStateView')}
${extractFunction(source, 'rebindDetachedSubagentState')}
${extractFunction(source, 'registerSubagentParentMessageAlias')}
${extractFunction(source, 'updateSubagentStateMeta')}
${extractFunction(source, 'getSubagentState')}
({ getSubagentState, registerSubagentParentMessageAlias });`,
        context,
        { filename: 'streamMessages.subagentStateRebind.js' },
    );

    assert.match(persistedReplayBody, /rebindDetached:\s*true/);
    assert.match(persistedReplayBody, /Array\.isArray\(run\.events\)/);
    assert.doesNotMatch(source, /\/api\/v1\/subagents\/runs|hydrateSubagentTranscript/);
    assert.match(
        optimisticBindingBody,
        /registerSubagentParentMessageAlias\(normalizedLocalId,\s*normalizedServerId\)/,
    );

    const state = helpers.getSubagentState('optimistic-message', 'run-1');
    state.launcher = { isConnected: true };
    helpers.registerSubagentParentMessageAlias('optimistic-message', 'server-message');

    // Receiving the server ID must not interrupt the launcher that is still
    // receiving live events in the optimistic assistant container.
    const activeState = helpers.getSubagentState(
        'server-message',
        'run-1',
        { rebindDetached: true },
    );
    assert.equal(activeState.parentMessageId, 'optimistic-message');
    assert.equal(activeState.persistedParentMessageId, 'server-message');

    // Clearing the old transcript disconnects the launcher. Persisted replay
    // can now bind the run to the server-backed assistant container.
    state.launcher.isConnected = false;
    const replayedState = helpers.getSubagentState(
        'server-message',
        'run-1',
        { rebindDetached: true },
    );
    assert.equal(replayedState, state);
    assert.equal(replayedState.parentMessageId, 'server-message');
    assert.equal(replayedState.persistedParentMessageId, 'server-message');
    assert.equal(replayedState.launcher, null);
});

test('branching a newly streamed response uses its persisted assistant message ID', async () => {
    const assistantBranchButton = { disabled: true };
    const userAnchor = { dataset: { optimisticMessage: 'true' } };
    const userContainer = {
        dataset: { optimisticMessage: 'true' },
        __editState: {},
    };
    const assistantContainer = {
        id: 'a-optimistic-message',
        dataset: {
            optimisticMessage: 'true',
            referenceId: 'optimistic-message',
            retryCount: '0',
        },
        querySelector(selector) {
            return selector === '.assistant-branch-btn' ? assistantBranchButton : null;
        },
    };
    const requestedUrls = [];
    let chatListRefreshes = 0;
    const errors = [];
    const context = {
        CSS: { escape: (value) => value },
        URLSearchParams,
        document: {
            getElementById(id) {
                if (id === 'u-optimistic-message') return userAnchor;
                if (id === 'a-optimistic-message') return assistantContainer;
                return null;
            },
            querySelector(selector) {
                return selector.includes('optimistic-message') ? userContainer : null;
            },
        },
        registerSubagentParentMessageAlias() {},
        window: {
            async authedFetch(url) {
                requestedUrls.push(url);
                return {
                    ok: true,
                    async json() {
                        return { status: 'success', new_chat_id: 'branch-chat' };
                    },
                };
            },
        },
        getStreamText: (_key, fallback) => fallback,
        getStreamTextFormatted: (_key, fallback, values) => fallback.replace('{status}', values.status),
        notifyError: (message) => errors.push(message),
        async initChatList() {
            chatListRefreshes += 1;
        },
    };
    const helpers = vm.runInNewContext(
        [
            extractFunction(streamMessagesSource, 'bindOptimisticMessageToServerMessage'),
            extractFunction(streamMessagesSource, 'bindAssistantContainerToServerMessage'),
            extractFunction(streamMessagesSource, 'resolvePersistedAssistantMessageId'),
            extractFunction(streamMessagesSource, 'branchFromAssistantMessage').replace(/^function /, 'async function '),
            '({ bindOptimisticMessageToServerMessage, bindAssistantContainerToServerMessage, resolvePersistedAssistantMessageId, branchFromAssistantMessage });',
        ].join('\n'),
        context,
        { filename: 'streamMessages.branchPersistedAssistantId.js' },
    );

    assert.equal(
        helpers.bindOptimisticMessageToServerMessage('optimistic-message', 'persisted-user-message'),
        true,
    );
    assert.equal(assistantContainer.dataset.referenceId, 'persisted-user-message');
    assert.equal(assistantContainer.dataset.assistantMessageId, undefined);
    assert.equal(assistantContainer.dataset.optimisticMessage, 'true');
    assert.equal(
        helpers.resolvePersistedAssistantMessageId(assistantContainer, 'optimistic-message'),
        '',
        'the persisted user ID must never be treated as the assistant branch boundary',
    );

    assert.equal(
        helpers.bindAssistantContainerToServerMessage('optimistic-message', 'persisted-assistant-message'),
        true,
    );
    assert.equal(assistantContainer.dataset.assistantMessageId, 'persisted-assistant-message');
    assert.equal(assistantContainer.dataset.optimisticMessage, undefined);
    assert.equal(assistantBranchButton.disabled, false);

    assert.equal(
        await helpers.branchFromAssistantMessage(assistantContainer, 'optimistic-message'),
        true,
    );
    assert.deepEqual(requestedUrls, [
        '/api/v1/chats/branch?message_id=persisted-assistant-message',
    ]);
    assert.equal(chatListRefreshes, 1);
    assert.deepEqual(errors, []);
});

test('assistant branch ID fallback accepts only stable reconstructed response versions', () => {
    const resolvePersistedAssistantMessageId = vm.runInNewContext(
        `${extractFunction(streamMessagesSource, 'resolvePersistedAssistantMessageId')}\nresolvePersistedAssistantMessageId;`,
        {},
        { filename: 'streamMessages.branchPersistedFallback.js' },
    );
    const reconstructedVersion = {
        id: 'a-persisted-assistant-version',
        dataset: {
            referenceId: 'persisted-user-message',
            retryCount: '1',
        },
    };

    assert.equal(
        resolvePersistedAssistantMessageId(reconstructedVersion, 'persisted-assistant-version'),
        'persisted-assistant-version',
    );

    reconstructedVersion.dataset.optimisticMessage = 'true';
    assert.equal(
        resolvePersistedAssistantMessageId(reconstructedVersion, 'persisted-assistant-version'),
        '',
    );

    delete reconstructedVersion.dataset.optimisticMessage;
    reconstructedVersion.dataset.retryCount = '0';
    assert.equal(
        resolvePersistedAssistantMessageId(reconstructedVersion, 'persisted-assistant-version'),
        '',
    );
});

test('assistant branch failures emit one notification and do not refresh the chat list', async () => {
    const errors = [];
    let chatListRefreshes = 0;
    const context = {
        URLSearchParams,
        resolvePersistedAssistantMessageId: () => 'persisted-assistant-message',
        window: {
            async authedFetch() {
                return { ok: false, status: 404 };
            },
        },
        getStreamText: (_key, fallback) => fallback,
        getStreamTextFormatted: (_key, fallback, values) => fallback.replace('{status}', values.status),
        notifyError: (message) => errors.push(message),
        async initChatList() {
            chatListRefreshes += 1;
        },
    };
    const branchFromAssistantMessage = vm.runInNewContext(
        `${extractFunction(streamMessagesSource, 'branchFromAssistantMessage').replace(/^function /, 'async function ')}\nbranchFromAssistantMessage;`,
        context,
        { filename: 'streamMessages.branchSingleFailure.js' },
    );

    assert.equal(await branchFromAssistantMessage({}, 'optimistic-message'), false);
    assert.deepEqual(errors, ['HTTP 404']);
    assert.equal(chatListRefreshes, 0);
});

test('persisted subagent replay reads every event directly from message metadata', () => {
    const source = streamMessagesSource;
    const state = { events: [] };
    const context = {
        getSubagentState(messageId, runId, options) {
            assert.equal(messageId, 'message-1');
            assert.equal(runId, 'run-1');
            assert.equal(options.rebindDetached, true);
            return state;
        },
        normalizeSubagentEvent(eventName, data) {
            return { eventName, data };
        },
        ensureSubagentLauncher() {},
        updateSubagentLauncher() {},
        refreshAssistantStatsForMessage() {},
    };
    const renderPersistedSubagentBlock = vm.runInNewContext(
        `${extractFunction(source, 'renderPersistedSubagentBlock')}\nrenderPersistedSubagentBlock;`,
        context,
        { filename: 'streamMessages.persistedSubagent.js' },
    );

    const rendered = renderPersistedSubagentBlock('message-1', {
        subagent: {
            id: 'run-1',
            status: 'completed',
            model_id: 'model-1',
            result: 'Hello',
            meta: { model_name: 'Model One' },
            events: [
                { type: 'message_delta', raw: { t: 'c', d: 'Hel' } },
                { type: 'message_delta', raw: { t: 'c', d: 'lo' } },
                { type: 'complete', raw: { status: 'completed' } },
            ],
        },
    });

    assert.equal(rendered, true);
    assert.equal(state.events.length, 3);
    assert.equal(state.events[0].data.content, 'Hel');
    assert.equal(state.events[1].data.content, 'lo');
    assert.equal(state.events[2].data.result, 'Hello');
});

test('user message edit attachments use the shared composer dropdown and stay right aligned', () => {
    const source = streamMessagesSource;
    const chatCss = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'chat.css'), 'utf8');
    const inlineElementsCss = readFrontendSource(
        path.join(__dirname, '..', '..', 'css', 'chat', 'chatBox', 'chatBoxInlineElements.css'),
        'utf8',
    );
    const createComposerBody = extractFunction(source, 'createUserMessageEditComposer');
    const createAttachmentBody = extractFunction(source, 'createUserMessageEditAttachmentTile');

    // The attachment container must precede the chat box in DOM order so the
    // column layout places existing and newly uploaded files above the editor.
    assert.match(
        createComposerBody,
        /editContainer\.appendChild\(attachmentsContainer\);\s*editContainer\.appendChild\(chatShell\);/,
    );
    assert.match(
        chatCss,
        /\.user-message-edit-container\s*\{[^}]*flex-direction:\s*column;[^}]*align-items:\s*flex-end;/,
    );
    assert.match(
        inlineElementsCss,
        /\.user-message-edit-inline-files\s*\{[^}]*width:\s*min\(100%, 520px\);[^}]*align-self:\s*flex-end;/,
    );
    assert.doesNotMatch(
        inlineElementsCss.match(/\.user-message-edit-inline-files\s*\{[^}]*\}/)?.[0] || '',
        /padding-right/,
    );
    assert.doesNotMatch(createComposerBody, /uploadMenu\.hidden\s*=\s*true;/);
    assert.match(createComposerBody, /window\.ChatFilesMenu\?\.createMenuElement/);
    assert.match(createComposerBody, /onNavigate:\s*\(\{ panelName \}\)/);
    assert.match(createComposerBody, /loadUserMessageEditQuickpickFiles\(session\)/);
    assert.match(createComposerBody, /loadUserMessageEditChatReferences\(session\)/);
    assert.match(createComposerBody, /quickScreenCapture\?\.\(\{ attachmentTarget: editAttachmentTarget \}\)/);
    assert.match(createComposerBody, /openGoogleDrive\?\.\(\{ attachmentTarget: editAttachmentTarget \}\)/);
    assert.doesNotMatch(createComposerBody, /openUserMessageEdit(?:Modal|ChatReferencesModal)/);
    assert.match(chatCss, /\.user-message-edit-dropdown \.select-dropdown\s*\{[^}]*width:\s*320px;[^}]*overflow:\s*hidden;/s);
    assert.match(createAttachmentBody, /iconImg\.src\s*=\s*`\/assets\/file_svgs\/\$\{iconName\}`/);
});

test('user message edit attachment remove control has a translated filename-specific accessible name', () => {
    const createAttachmentTile = vm.runInNewContext(
        `${extractFunction(streamMessagesSource, 'createUserMessageEditAttachmentTile')}\ncreateUserMessageEditAttachmentTile;`,
        {
            document: {
                createElement: (tagName) => new FakeElement(tagName),
            },
            Icons: { close: '<svg aria-hidden="true"></svg>' },
            getUserMessageEditAttachmentHelpers: () => ({
                getFileExtensionLabel: () => 'PDF',
                getFileIconName: () => 'pdf.svg',
                formatBytes: () => '2 KB',
            }),
            getStreamTextFormatted(key, fallback, vars) {
                assert.equal(key, 'chat_sr_remove_attachment');
                assert.equal(fallback, 'Remove attachment: {name}');
                return `Anhang ${vars.name} entfernen`;
            },
            removeUserMessageEditAttachment() {},
        },
        { filename: 'streamMessages.editAttachmentA11y.js' },
    );

    const tile = createAttachmentTile({}, {
        id: 'file-1',
        original_name: 'report.pdf',
        mime_type: 'application/pdf',
        file_size: 2048,
    });
    const removeControl = tile.querySelector('.inline-files-element-delete');

    assert.ok(removeControl);
    assert.equal(removeControl.getAttribute('role'), 'button');
    assert.equal(removeControl.getAttribute('tabindex'), '0');
    assert.equal(removeControl.getAttribute('aria-label'), 'Anhang report.pdf entfernen');
    assert.equal(removeControl.title, 'Anhang report.pdf entfernen');
});

test('bundled file icons always use root-relative URLs on nested SPA routes', () => {
    // The chat application can be loaded at /workspace/* and /chat/<id>.
    // Bare and parent-relative "assets/..." paths inherit the current route
    // and can request a nonexistent directory, so guard every runtime source.
    const chatSourceDirectory = __dirname;
    const productionSources = fs.readdirSync(chatSourceDirectory, { recursive: true })
        .filter((sourcePath) => sourcePath.endsWith('.js') && !sourcePath.endsWith('.test.js'));
    const relativeFileIconReference =
        /(?:^|[^/])assets\/file_svgs\/|(?:^|\/)\.\.\/(?:\.\.\/)*assets\/file_svgs\//;

    for (const sourcePath of productionSources) {
        const source = readFrontendSource(path.join(chatSourceDirectory, sourcePath), 'utf8');
        assert.doesNotMatch(
            source,
            relativeFileIconReference,
            `${sourcePath} must resolve bundled file icons from /assets/file_svgs/`,
        );
    }
});

test('tool calls after empty reasoning stay inside the collapsible thinking content', () => {
    const source = streamMessagesSource;
    const document = {
        createElement(tagName) {
            return new FakeElement(tagName);
        },
    };
    const helpers = vm.runInNewContext(
        [
            extractFunction(source, 'ensureAssistantThinkingContent'),
            extractFunction(source, 'createAssistantToolStep'),
            extractFunction(source, 'findAssistantToolStep'),
            extractFunction(source, 'ensureAssistantToolStep'),
            '({ ensureAssistantToolStep });',
        ].join('\n'),
        { document, Icons: { globe: '<svg></svg>' } },
        { filename: 'streamMessages.ensureAssistantToolStep.js' },
    );

    // An empty persisted reasoning block creates only the container and header.
    // The following tool call must fill in the standard collapsible hierarchy.
    const thinkingContainer = new FakeElement('div');
    thinkingContainer.className = 'assistant-thinking';
    const header = new FakeElement('button');
    header.className = 'assistant-thinking-header';
    thinkingContainer.appendChild(header);

    const step = helpers.ensureAssistantToolStep(thinkingContainer, {
        toolConfig: { icon: () => '<svg></svg>' },
        displayName: 'Generate image',
        effectiveToolName: 'image_generation',
        toolId: 'call-image',
    });

    const thinkingContent = thinkingContainer.querySelector('.assistant-thinking-content');
    const thinkingBody = thinkingContainer.querySelector('.assistant-thinking-body');
    assert.ok(step);
    assert.ok(thinkingContent);
    assert.ok(thinkingBody);
    assert.equal(thinkingBody.parentNode, thinkingContent);
    assert.equal(step.parentNode, thinkingBody);
    assert.equal(step.classList.contains('thinking-step-function-call'), true);

    // Also normalize containers produced by the previous implementation, where
    // the body was inserted directly beside the header.
    const malformedContainer = new FakeElement('div');
    malformedContainer.className = 'assistant-thinking collapsed';
    const malformedHeader = new FakeElement('button');
    malformedHeader.className = 'assistant-thinking-header';
    const malformedBody = new FakeElement('div');
    malformedBody.className = 'assistant-thinking-body';
    malformedContainer.appendChild(malformedHeader);
    malformedContainer.appendChild(malformedBody);

    helpers.ensureAssistantToolStep(malformedContainer, {
        toolConfig: { icon: () => '<svg></svg>' },
        displayName: 'Generate image',
        effectiveToolName: 'image_generation',
        toolId: 'call-legacy-image',
    });

    const repairedContent = malformedContainer.querySelector('.assistant-thinking-content');
    assert.ok(repairedContent);
    assert.equal(malformedBody.parentNode, repairedContent);
    assert.equal(malformedContainer.children.includes(malformedBody), false);
});

test('assistant thinking starts collapsed and finalization preserves expansion state', () => {
    const source = streamMessagesSource;
    const defaultThinkingBlocks = source.match(
        /thinkingContainer\.className = 'assistant-thinking collapsed';/g,
    ) || [];
    const finalizeStart = source.indexOf('function finalizeThinkingBlocks(');
    const finalizeEnd = source.indexOf('function shouldSkipCanvasAssistantFile(', finalizeStart);
    const finalizeSource = source.slice(finalizeStart, finalizeEnd);

    assert.equal(defaultThinkingBlocks.length, 2);
    assert.match(source, /thinkingContainer\.className = 'assistant-thinking collapsed assistant-thinking-loading';/);
    assert.doesNotMatch(finalizeSource, /classList.*collapsed|aria-expanded/);
    assert.doesNotMatch(source, /classList\??\.add\(['"]collapsed['"]\)/);
});

test('title-only reasoning creates a chronological step after a tool call', () => {
    const source = streamMessagesSource;
    const elementsById = new Map();
    const document = {
        createElement(tagName) {
            return new FakeElement(tagName);
        },
        getElementById(id) {
            return elementsById.get(id) || null;
        },
    };
    const helpers = vm.runInNewContext(
        [
            extractFunction(source, 'ensureAssistantThinkingBody'),
            extractFunction(source, 'parseLeadingTitle'),
            extractFunction(source, 'detectEmbeddedTitle'),
            extractFunction(source, 'detectFirstTitle'),
            extractFunction(source, 'checkAndSplitStepForEmbeddedTitle'),
            extractFunction(source, 'getAssistantThinkingRawContent'),
            extractFunction(source, 'setAssistantThinkingContent'),
            extractFunction(source, 'appendStreamingReasoningText'),
            extractFunction(source, 'appendAssistantReasoning'),
            '({ appendAssistantReasoning, detectFirstTitle, parseLeadingTitle });',
        ].join('\n'),
        {
            document,
            updateThinkingHeaderForActivity() {},
            setAssistantThinkingHeaderTitle() {},
            ensureInitialThinkingStep() {},
            applyAssistantMessageAccessibility() {},
        },
        { filename: 'streamMessages.reasoningOrder.js' },
    );

    const assistantContainer = new FakeElement('div');
    const thinkingContainer = new FakeElement('div');
    const thinkingContent = new FakeElement('div');
    const thinkingBody = new FakeElement('div');
    const toolStep = new FakeElement('div');
    thinkingContent.className = 'assistant-thinking-content';
    thinkingBody.className = 'assistant-thinking-body';
    toolStep.className = 'thinking-step thinking-step-function-call';
    thinkingBody.appendChild(toolStep);
    thinkingContent.appendChild(thinkingBody);
    thinkingContainer.appendChild(thinkingContent);
    elementsById.set('a-message-1', assistantContainer);
    elementsById.set('at-1-message-1', thinkingContainer);

    const nextCount = helpers.appendAssistantReasoning(
        'message-1',
        '**Verifying results**',
        't',
        1,
    );

    assert.equal(nextCount, 1);
    assert.equal(thinkingBody.children.length, 2);
    assert.equal(thinkingBody.children[0], toolStep);
    const reasoningStep = thinkingBody.children[1];
    assert.equal(
        reasoningStep.querySelector('.thinking-step-title').textContent,
        'Verifying results',
    );
    assert.equal(reasoningStep.querySelector('.thinking-step-content').textContent, '');

    // A provider may split a subsequent title across protocol chunks. It must
    // become a new step after the existing title, not raw Markdown appended to
    // an earlier reasoning body.
    helpers.appendAssistantReasoning('message-1', '**', 'r', 1);
    helpers.appendAssistantReasoning('message-1', 'Checking persistence**', 'r', 1);
    assert.equal(thinkingBody.children.length, 3);
    assert.equal(
        thinkingBody.children[2].querySelector('.thinking-step-title').textContent,
        'Checking persistence',
    );
    assert.equal(
        thinkingBody.children[2].querySelector('.thinking-step-content').textContent,
        '',
    );

    const standaloneTitle = helpers.parseLeadingTitle('**Standalone title**');
    assert.equal(standaloneTitle.title, 'Standalone title');
    assert.equal(standaloneTitle.rest, '');
    const fragmentedTitle = helpers.detectFirstTitle('**Fragmented title**');
    assert.equal(fragmentedTitle.title, 'Fragmented title');
    assert.equal(fragmentedTitle.afterTitle, '');
});

test('ordinary reasoning deltas append without rereading the complete trace', () => {
    const source = streamMessagesSource;
    const { appendStreamingReasoningText } = vm.runInNewContext(
        [
            extractFunction(source, 'getAssistantThinkingRawContent'),
            extractFunction(source, 'setAssistantThinkingContent'),
            extractFunction(source, 'appendStreamingReasoningText'),
            '({ appendStreamingReasoningText });',
        ].join('\n'),
        {
            renderAssistantMessageContent() {
                // The renderer is stubbed here; the test focuses on the raw
                // source buffer used between streamed deltas.
            },
        },
        { filename: 'streamMessages.reasoningAppend.js' },
    );
    const element = {
        // Real thinking-step nodes are initialized with this attribute before
        // any streamed delta arrives.
        attributes: { 'data-raw-content': '' },
        setAttribute(name, value) {
            this.attributes[name] = String(value);
        },
        getAttribute(name) {
            return Object.prototype.hasOwnProperty.call(this.attributes, name)
                ? this.attributes[name]
                : null;
        },
        get textContent() {
            throw new Error('the full accumulated trace must not be read');
        },
    };

    assert.equal(appendStreamingReasoningText(element, 'ordinary delta'), false);
    assert.equal(appendStreamingReasoningText(element, '**'), true);
    assert.equal(element.attributes['data-raw-content'], 'ordinary delta**');
});

test('thinking Markdown is rendered while the raw source remains available', () => {
    const source = streamMessagesSource;
    const renderedSources = [];
    const { appendStreamingReasoningText } = vm.runInNewContext(
        [
            extractFunction(source, 'getAssistantThinkingRawContent'),
            extractFunction(source, 'setAssistantThinkingContent'),
            extractFunction(source, 'appendStreamingReasoningText'),
            '({ appendStreamingReasoningText });',
        ].join('\n'),
        {
            renderAssistantMessageContent(_element, raw) {
                renderedSources.push(raw);
            },
        },
        { filename: 'streamMessages.reasoningMarkdown.js' },
    );
    const element = new FakeElement('div');

    appendStreamingReasoningText(element, '**bold thinking**');

    assert.deepEqual(renderedSources, ['**bold thinking**']);
    assert.equal(element.getAttribute('data-raw-content'), '**bold thinking**');
});

test('streaming thinking Markdown uses the shared debounced render cadence', () => {
    const source = streamMessagesSource;
    const scheduled = [];
    const rendered = [];
    const { appendStreamingReasoningText } = vm.runInNewContext(
        [
            extractFunction(source, 'getAssistantThinkingRawContent'),
            extractFunction(source, 'setAssistantThinkingContent'),
            extractFunction(source, 'appendStreamingReasoningText'),
            '({ appendStreamingReasoningText });',
        ].join('\n'),
        {
            scheduleDebouncedElementRender(element, raw) {
                scheduled.push([element, raw]);
            },
            renderAssistantMessageContent(element, raw) {
                rendered.push([element, raw]);
            },
        },
        { filename: 'streamMessages.reasoningMarkdownDebounce.js' },
    );
    const element = new FakeElement('div');
    element.closest = () => ({ dataset: { isStreaming: 'true' } });

    appendStreamingReasoningText(element, 'first');
    appendStreamingReasoningText(element, ' second');

    assert.deepEqual(scheduled.map(([, raw]) => raw), ['first', 'first second']);
    assert.equal(rendered.length, 0);
    assert.equal(element.getAttribute('data-raw-content'), 'first second');
});

test('stream completion finalizes answer and thinking Markdown enhancements', () => {
    const source = streamMessagesSource;
    const answer = {
        getAttribute: () => '# Answer',
    };
    const thinking = {
        getAttribute: () => '$x^2$',
    };
    const rendered = [];
    let selector = '';
    const container = {
        querySelectorAll(value) {
            selector = value;
            return [answer, thinking];
        },
    };
    const { finalizeStreamingMarkdownInContainer } = vm.runInNewContext(
        [
            extractFunction(source, 'finalizeStreamingMarkdownInContainer'),
            '({ finalizeStreamingMarkdownInContainer });',
        ].join('\n'),
        {
            cancelScheduledStreamingRender() {},
            flushPendingRenders() {},
            renderAssistantMessageContent(element, raw) {
                rendered.push([element, raw]);
            },
        },
        { filename: 'streamMessages.reasoningMarkdownFinalize.js' },
    );

    finalizeStreamingMarkdownInContainer(container);

    assert.match(selector, /\.assistant-message-content/);
    assert.match(selector, /\.thinking-step-content/);
    assert.deepEqual(rendered, [
        [answer, '# Answer'],
        [thinking, '$x^2$'],
    ]);
});

test('every locale translates presentation index persistence failures', () => {
    const i18nRoot = path.join(__dirname, '..', '..', 'i18n');
    const localeDirectories = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    localeDirectories.forEach((locale) => {
        const translations = JSON.parse(
            readFrontendSource(path.join(i18nRoot, locale, 'index.json'), 'utf8'),
        );
        assert.equal(
            typeof translations.slide_presentation_index_persist_failed,
            'string',
            `${locale} is missing the presentation persistence error`,
        );
        assert.ok(translations.slide_presentation_index_persist_failed.trim());
    });
});

test('interrupted streams clear streaming state and finalize deferred Markdown', () => {
    const assistantContainer = new FakeElement('article');
    assistantContainer.id = 'a-interrupted-message';
    assistantContainer.style = {};
    assistantContainer.className = 'assistant-message-container';
    assistantContainer.dataset.isStreaming = 'true';
    assistantContainer.dataset.announceStreaming = 'true';
    const loadingBlock = new FakeElement('div');
    loadingBlock.className = 'assistant-thinking-loading';
    assistantContainer.appendChild(loadingBlock);

    const transcriptRoot = new FakeElement('div');
    transcriptRoot.appendChild(assistantContainer);
    const findById = (id) => {
        let match = null;
        const visit = (element) => {
            if (match) return;
            if (element.id === id) {
                match = element;
                return;
            }
            element.children.forEach(visit);
        };
        visit(transcriptRoot);
        return match;
    };
    const calls = [];
    const context = {
        document: {
            getElementById: findById,
            createElement: (tagName) => new FakeElement(tagName),
        },
        resolveAssistantVersionInfo: () => ({ current: 1, total: 1 }),
        getStreamText(key, fallback) {
            return key === 'chat_connection_interrupted_retry'
                ? 'Translated interruption'
                : fallback;
        },
        getChatA11yText(key, fallback) {
            if (key === 'chat_sr_response_error_status') return 'Translated response failed';
            return fallback;
        },
        announceChatMessage(message, options) {
            calls.push(['announce', message, options]);
        },
        finalizeStreamingMarkdownInContainer(container) {
            calls.push(['markdown', container]);
        },
        window: {
            ChatScrollManager: {
                endStream(container) {
                    calls.push(['endStream', container]);
                },
            },
        },
    };
    const helpers = vm.runInNewContext(
        [
            extractFunction(streamMessagesSource, 'ensureScreenReaderNode'),
            extractFunction(streamMessagesSource, 'applyAssistantMessageAccessibility'),
            extractFunction(streamMessagesSource, 'findStreamAssistantContainer'),
            extractFunction(streamMessagesSource, 'finalizeInterruptedAssistantStream'),
            '({ finalizeInterruptedAssistantStream });',
        ].join('\n'),
        context,
        { filename: 'streamMessages.interruptedFinalize.js' },
    );

    assert.equal(
        helpers.finalizeInterruptedAssistantStream('interrupted-message', transcriptRoot),
        true,
    );
    assert.equal(assistantContainer.dataset.isStreaming, undefined);
    assert.equal(assistantContainer.dataset.announceStreaming, 'false');
    assert.equal(assistantContainer.dataset.hasError, 'true');
    assert.equal(loadingBlock.parentNode, null);
    const errorBlock = assistantContainer.querySelector('.assistant-message-error');
    assert.ok(errorBlock);
    assert.equal(errorBlock.textContent, 'Translated interruption');
    assert.equal(errorBlock.attributes.role, 'alert');
    const status = assistantContainer.querySelector('.chat-message-sr-status');
    assert.ok(status);
    assert.equal(status.textContent, 'Translated response failed');
    assert.doesNotMatch(status.textContent, /complete/i);
    assert.equal(assistantContainer.attributes['aria-busy'], 'false');
    assert.deepEqual(calls.map(([name]) => name), [
        'markdown',
        'announce',
        'endStream',
    ]);
    assert.equal(calls[1][1], 'Assistant response failed');
    assert.equal(calls[1][2].assertive, true);

    calls.length = 0;
    assert.equal(
        helpers.finalizeInterruptedAssistantStream('interrupted-message', transcriptRoot),
        false,
        'already-stable responses must not be finalized twice',
    );
    assert.equal(calls.length, 0);
});

test('cancelled streams finalize every thinking block and build the assistant toolbar', () => {
    const source = streamMessagesSource;
    const calls = [];
    const loadingBlock = {
        remove() {
            calls.push(['remove-loading']);
        },
    };
    const thinkingBlocks = [{ name: 'first' }, { name: 'second' }];
    const assistantContainer = {
        id: 'a-cancelled-message',
        dataset: {
            isStreaming: 'true',
            assistantMetadata: '{"provider":"test"}',
        },
        classList: {
            contains(className) {
                return className === 'assistant-message-container';
            },
        },
        querySelectorAll(selector) {
            if (selector === '.assistant-thinking-loading') return [loadingBlock];
            if (selector === '.assistant-thinking') return thinkingBlocks;
            return [];
        },
    };
    const transcriptRoot = {
        querySelectorAll() {
            return [assistantContainer];
        },
    };
    const context = {
        assistantContainerHasMeaningfulOutput() {
            return true;
        },
        getChatA11yText(_key, fallback) {
            return fallback;
        },
        announceChatMessage(message) {
            calls.push(['announce', message]);
        },
        finalizeInterruptedAssistantStream(messageId, root) {
            calls.push(['interrupt-finalize', messageId, root]);
            delete assistantContainer.dataset.isStreaming;
            assistantContainer.dataset.announceStreaming = 'false';
            return true;
        },
        finalizeThinkingBlockHeader(block) {
            calls.push(['thinking-finalize', block.name]);
        },
        appendAssistantDone(messageId, metadata, regenerationInfo, root) {
            calls.push(['toolbar', messageId, metadata, regenerationInfo, root]);
        },
    };
    const helpers = vm.runInNewContext(
        [
            extractFunction(source, 'findStreamAssistantContainer'),
            extractFunction(source, 'finalizeThinkingBlocks'),
            extractFunction(source, 'finalizeCancelledAssistantStream'),
            '({ finalizeCancelledAssistantStream });',
        ].join('\n'),
        context,
        { filename: 'streamMessages.cancelledFinalize.js' },
    );

    assert.equal(
        helpers.finalizeCancelledAssistantStream('cancelled-message', transcriptRoot),
        true,
    );
    assert.equal(calls.filter(([name]) => name === 'remove-loading').length, 1);
    assert.deepEqual(
        calls.filter(([name]) => name === 'thinking-finalize').map(([, blockName]) => blockName),
        ['first', 'second'],
    );
    const toolbarCall = calls.find(([name]) => name === 'toolbar');
    assert.equal(toolbarCall[1], 'cancelled-message');
    assert.equal(toolbarCall[2].provider, 'test');
    assert.equal(toolbarCall[3], null);
    assert.equal(toolbarCall[4], transcriptRoot);
    assert.equal(assistantContainer.dataset.assistantTerminalState, 'cancelled');
    assert.equal(assistantContainer.dataset.cancelPresentationFinalized, 'true');
    assert.deepEqual(
        calls.filter(([name]) => name === 'announce').map(([, message]) => message),
        ['Assistant response stopped'],
    );

    assert.equal(
        helpers.finalizeCancelledAssistantStream('cancelled-message', transcriptRoot),
        false,
        'cancellation presentation finalization must be idempotent',
    );
    assert.equal(calls.filter(([name]) => name === 'toolbar').length, 1);
});

test('stopping before output removes the empty assistant turn and announces cancellation', () => {
    const assistantContainer = new FakeElement('article');
    assistantContainer.id = 'a-empty-cancelled-message';
    assistantContainer.className = 'assistant-message-container';
    assistantContainer.dataset.isStreaming = 'true';
    assistantContainer.dataset.announceStreaming = 'true';
    const loadingBlock = new FakeElement('div');
    loadingBlock.className = 'assistant-thinking assistant-thinking-loading';
    assistantContainer.appendChild(loadingBlock);

    const transcriptRoot = new FakeElement('div');
    transcriptRoot.appendChild(assistantContainer);
    const announcements = [];
    let toolbarCalls = 0;
    let endedStreams = 0;
    const context = {
        assistantContainerHasMeaningfulOutput: vm.runInNewContext(
            `${extractFunction(streamMessagesSource, 'assistantContainerHasMeaningfulOutput')}\nassistantContainerHasMeaningfulOutput;`,
        ),
        appendAssistantDone() {
            toolbarCalls += 1;
        },
        announceChatMessage(message) {
            announcements.push(message);
        },
        getChatA11yText(_key, fallback) {
            return fallback;
        },
        window: {
            ChatScrollManager: {
                endStream() {
                    endedStreams += 1;
                },
            },
        },
    };
    const { finalizeCancelledAssistantStream } = vm.runInNewContext(
        [
            extractFunction(streamMessagesSource, 'findStreamAssistantContainer'),
            extractFunction(streamMessagesSource, 'finalizeCancelledAssistantStream'),
            '({ finalizeCancelledAssistantStream });',
        ].join('\n'),
        context,
        { filename: 'streamMessages.emptyCancelledFinalize.js' },
    );

    assert.equal(finalizeCancelledAssistantStream('empty-cancelled-message', transcriptRoot), true);
    assert.equal(transcriptRoot.children.includes(assistantContainer), false);
    assert.equal(toolbarCalls, 0, 'an unpersisted empty response must not receive completion actions');
    assert.equal(endedStreams, 1);
    assert.deepEqual(announcements, ['Assistant response stopped']);
});

test('the real accessibility helper keeps partial cancellation out of the complete state', () => {
    const assistantContainer = new FakeElement('article');
    assistantContainer.id = 'a-partial-cancelled-message';
    assistantContainer.style = {};
    assistantContainer.className = 'assistant-message-container';
    assistantContainer.dataset.isStreaming = 'true';
    assistantContainer.dataset.announceStreaming = 'true';
    const content = new FakeElement('div');
    content.className = 'assistant-message';
    content.textContent = 'Partial answer';
    assistantContainer.appendChild(content);

    const transcriptRoot = new FakeElement('div');
    transcriptRoot.appendChild(assistantContainer);
    const findById = (id) => {
        let match = null;
        const visit = (element) => {
            if (match) return;
            if (element.id === id) {
                match = element;
                return;
            }
            element.children.forEach(visit);
        };
        visit(transcriptRoot);
        return match;
    };
    const announcements = [];
    const context = {
        window: { ChatScrollManager: { endStream() {} } },
        document: {
            getElementById: findById,
            createElement: (tagName) => new FakeElement(tagName),
        },
        resolveAssistantVersionInfo: () => ({ current: 1, total: 1 }),
        getChatA11yText(_key, fallback) {
            return fallback;
        },
        getStreamText(_key, fallback) {
            return fallback;
        },
        finalizeStreamingMarkdownInContainer() {},
        finalizeThinkingBlocks() {},
        assistantContainerHasMeaningfulOutput: vm.runInNewContext(
            `${extractFunction(streamMessagesSource, 'assistantContainerHasMeaningfulOutput')}\nassistantContainerHasMeaningfulOutput;`,
        ),
        announceChatMessage(message) {
            announcements.push(message);
        },
    };
    const helpers = vm.runInNewContext(
        [
            extractFunction(streamMessagesSource, 'ensureScreenReaderNode'),
            extractFunction(streamMessagesSource, 'applyAssistantMessageAccessibility'),
            extractFunction(streamMessagesSource, 'findStreamAssistantContainer'),
            extractFunction(streamMessagesSource, 'finalizeInterruptedAssistantStream'),
            extractFunction(streamMessagesSource, 'finalizeCancelledAssistantStream'),
            `function appendAssistantDone(messageId, _metadata, _regenerationInfo, root) {
                const container = findStreamAssistantContainer(messageId, root);
                applyAssistantMessageAccessibility(container, {
                    messageId,
                    streaming: false,
                    terminalState: container.dataset.assistantTerminalState,
                });
            }`,
            '({ finalizeCancelledAssistantStream });',
        ].join('\n'),
        context,
        { filename: 'streamMessages.cancelledAccessibility.js' },
    );

    assert.equal(
        helpers.finalizeCancelledAssistantStream('partial-cancelled-message', transcriptRoot),
        true,
    );
    const status = assistantContainer.querySelector('.chat-message-sr-status');
    assert.ok(status);
    assert.equal(status.textContent, 'Response stopped');
    assert.doesNotMatch(status.textContent, /complete/i);
    assert.equal(assistantContainer.attributes['aria-busy'], 'false');
    assert.deepEqual(announcements, ['Assistant response stopped']);
});

test('persisted cancellation metadata restores the visible stopped marker', () => {
    const assistantContainer = new FakeElement('article');
    const actionList = new FakeElement('div');
    actionList.className = 'assistant-message-list';
    assistantContainer.appendChild(actionList);
    const helpers = vm.runInNewContext(
        [
            extractFunction(streamMessagesSource, 'appendBeforeAssistantList'),
            extractFunction(streamMessagesSource, 'applyAssistantTerminalMetadata'),
            extractFunction(streamMessagesSource, 'syncAssistantCancelledStatus'),
            '({ applyAssistantTerminalMetadata, syncAssistantCancelledStatus });',
        ].join('\n'),
        {
            document: { createElement: (tagName) => new FakeElement(tagName) },
            getChatA11yText(_key, fallback) {
                return fallback;
            },
        },
        { filename: 'streamMessages.persistedCancelledStatus.js' },
    );

    const terminalState = helpers.applyAssistantTerminalMetadata(
        assistantContainer,
        { status: 'cancelled' },
    );
    helpers.syncAssistantCancelledStatus(assistantContainer, terminalState === 'cancelled');

    const status = assistantContainer.querySelector('.assistant-response-cancelled-status');
    assert.equal(assistantContainer.dataset.assistantTerminalState, 'cancelled');
    assert.ok(status);
    assert.equal(status.textContent, 'Response stopped');
    assert.equal(status.attributes['aria-hidden'], 'true');
    assert.ok(
        assistantContainer.children.indexOf(status) < assistantContainer.children.indexOf(actionList),
        'the stopped marker should remain adjacent to the partial response before its actions',
    );
});

test('every locale translates cancelled assistant response states', () => {
    const i18nRoot = path.join(__dirname, '..', '..', 'i18n');
    const localeDirectories = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    localeDirectories.forEach((locale) => {
        const translations = JSON.parse(
            readFrontendSource(path.join(i18nRoot, locale, 'index.json'), 'utf8'),
        );
        ['chat_sr_response_cancelled_status', 'chat_sr_response_cancelled'].forEach((key) => {
            assert.equal(typeof translations[key], 'string', `${locale} is missing ${key}`);
            assert.ok(translations[key].trim(), `${locale} has an empty ${key}`);
        });
    });
});

test('temporary chat history preserves a partial response cancellation', () => {
    const sendSource = readSendMessageSource();
    const collectAssistantBlocksFromDom = vm.runInNewContext(
        `${extractFunction(sendSource, 'collectAssistantBlocksFromDom')}\ncollectAssistantBlocksFromDom;`,
        {},
        { filename: 'sendMessage.cancelledTemporaryHistory.js' },
    );
    const contentNode = {
        textContent: 'Partial answer',
        getAttribute(name) {
            return name === 'data-raw-content' ? 'Partial answer' : null;
        },
    };
    const contentBlock = {
        matches(selector) {
            return selector === '.assistant-message';
        },
        querySelectorAll(selector) {
            return selector === '.assistant-message-content' ? [contentNode] : [];
        },
    };
    const container = {
        dataset: { assistantTerminalState: 'cancelled' },
        children: [contentBlock],
        querySelectorAll() {
            return [];
        },
    };

    assert.deepEqual(
        JSON.parse(JSON.stringify(collectAssistantBlocksFromDom(container))),
        [{
            type: 'content',
            content: 'Partial answer',
            meta: {
                status: 'cancelled',
                assistant_terminal_state: 'cancelled',
            },
        }],
    );
});

test('normal and split stream loops always invoke interrupted-response cleanup', () => {
    const sendSource = readSendMessageSource();
    const splitSource = readFrontendSource(path.join(__dirname, 'splitScreen.js'), 'utf8');
    const normalStreamLifecycle = sendSource.slice(
        sendSource.indexOf('const reader = res.body.getReader()'),
        sendSource.indexOf('// Expose for dynamic re-rendering'),
    );
    const splitStreamLifecycle = extractFunction(splitSource, 'processStream');

    assert.match(
        normalStreamLifecycle,
        /finally\s*\{[\s\S]*finalizeCancelledAssistantStream\?\.\([\s\S]*finalizeInterruptedAssistantStream\?\.\(/,
    );
    assert.match(
        splitStreamLifecycle,
        /finally\s*\{[\s\S]*finalizeCancelledAssistantStream\?\.\(messageId,\s*container\)[\s\S]*finalizeInterruptedAssistantStream\?\.\(messageId,\s*container\)/,
    );
    assert.doesNotMatch(
        extractFunction(
            streamMessagesSource,
            'finalizeInterruptedAssistantStream',
        ),
        /appendAssistantDone\(/,
        'interruption cleanup must not manufacture a structured completion',
    );
    assert.match(
        extractFunction(
            streamMessagesSource,
            'finalizeCancelledAssistantStream',
        ),
        /appendAssistantDone\(/,
        'explicit cancellation should build actions for the retained partial response',
    );
});

test('stream cleanup is restricted to the generation that still owns its viewport', () => {
    const sendSource = readSendMessageSource();
    const splitSource = readFrontendSource(path.join(__dirname, 'splitScreen.js'), 'utf8');
    const normalStreamLifecycle = sendSource.slice(
        sendSource.indexOf('const reader = res.body.getReader()'),
        sendSource.indexOf('// Expose for dynamic re-rendering'),
    );
    const normalFinally = normalStreamLifecycle.slice(normalStreamLifecycle.lastIndexOf('} finally {'));
    const splitLifecycle = extractFunction(splitSource, 'processStream');

    assert.match(
        normalFinally,
        /getAttribute\('data-active-generation'\)\s*===\s*generationRequestId/,
    );
    assert.match(
        normalFinally,
        /if \(ownsActiveGeneration\)\s*\{[\s\S]*clearMediaGenPlaceholderForNonFileEvent[\s\S]*generationTransport\.cancelled[\s\S]*finalizeCancelledAssistantStream[\s\S]*finalizeInterruptedAssistantStream[\s\S]*handleStreamEnd[\s\S]*finalizeGenerationState\(\)/,
    );
    const readerErrorSection = normalStreamLifecycle.slice(
        normalStreamLifecycle.indexOf('const scheduledReconnect'),
        normalStreamLifecycle.indexOf('const { done, value }'),
    );
    assert.doesNotMatch(
        readerErrorSection,
        /finalizeGenerationState\(\)/,
        'reader errors must retain ownership until the guarded finally cleanup',
    );

    assert.match(splitLifecycle, /let streamBecameStale = false;/);
    assert.match(splitLifecycle, /streamBecameStale = true;[\s\S]*reader\.cancel\(\)/);
    assert.match(
        splitLifecycle,
        /const ownsPanelCleanup = !streamBecameStale && isCurrentPanelStream\(\);[\s\S]*if \(ownsPanelCleanup\)\s*\{[\s\S]*clearMediaGenPlaceholderForNonFileEvent[\s\S]*isPanelCancellationRequested\(side\)[\s\S]*finalizeCancelledAssistantStream[\s\S]*finalizeInterruptedAssistantStream/,
    );
    assert.doesNotMatch(
        splitLifecycle,
        /if \(stopIfStreamIsStale\(\)\)\s*\{[\s\S]{0,180}clearMediaGenPlaceholderForNonFileEvent/,
        'stale return branches must not mutate shared panel content',
    );
});

test('hiding a tool preview cancels its queued streaming update first', () => {
    const source = streamMessagesSource;
    const updateSource = extractFunction(source, 'updateAssistantToolPreview');
    const hideBranchStart = updateSource.indexOf('if (!shouldShow)');
    const visibleBranchStart = updateSource.indexOf('preview.hidden = false');
    assert.notEqual(hideBranchStart, -1);
    assert.notEqual(visibleBranchStart, -1);
    assert.ok(hideBranchStart < visibleBranchStart);

    const hideBranch = updateSource.slice(hideBranchStart, visibleBranchStart);
    const clearIndex = hideBranch.indexOf('clearScheduledAssistantToolPreview(step)');
    const stopIndex = hideBranch.indexOf('stopAssistantToolPreviewScroll(preview)');
    assert.notEqual(clearIndex, -1, 'hide path must cancel a pending preview timer');
    assert.ok(clearIndex < stopIndex, 'queued preview cancellation must be the first hide cleanup');
});

test('refreshed transcripts replay persisted blocks in database order', () => {
    const source = readFrontendSource(path.join(__dirname, 'chatTranscriptRenderer.js'), 'utf8');
    const elementsById = new Map();
    const calls = [];
    const window = {};
    const chatArea = new FakeElement('main');
    chatArea.id = 'chatAreaContainer';
    elementsById.set(chatArea.id, chatArea);

    const context = {
        window,
        document: {
            getElementById(id) {
                return elementsById.get(id) || null;
            },
        },
        HTMLElement: FakeElement,
        appendUserContent() {},
        appendAssistantContainer(messageId) {
            const assistant = new FakeElement('article');
            assistant.id = `a-${messageId}`;
            elementsById.set(assistant.id, assistant);
            chatArea.appendChild(assistant);
        },
        appendAssistantReasoning(_messageId, content, _lastType, count) {
            calls.push({ type: 'reasoning', content });
            return count + 1;
        },
        appendAssistantContent(_messageId, content, _lastType, count) {
            calls.push({ type: 'content', content });
            return count + 1;
        },
        appendAssistantTool(_messageId, _lastType, count, content, toolName, args, meta) {
            calls.push({ type: 'tool_call', content, toolName, args, meta });
            return count;
        },
        appendAssistantWidget(_messageId, content, widgetType, _lastType, widgetMeta) {
            calls.push({ type: 'widget', content, widgetType, widgetMeta });
        },
    };
    vm.runInNewContext(source, context, {
        filename: 'chatTranscriptRenderer.persistedOrder.js',
    });

    window.renderChatTranscript([
        {
            id: 'user-1',
            role: 'user',
            content: [{ type: 'user', content: 'Inspect providers' }],
        },
        {
            id: 'assistant-1',
            role: 'assistant',
            reference_id: 'user-1',
            content: [
                { type: 'reasoning', content: '**Plan**', meta: {} },
                { type: 'content', content: 'First answer.', meta: {} },
                {
                    type: 'tool_call',
                    content: 'Search',
                    meta: {
                        tool_name: 'Search',
                        arguments: '{"query":"providers"}',
                        status: 'completed',
                        raw_output: { matches: 7 },
                    },
                },
                { type: 'reasoning', content: '**Verify**', meta: {} },
                { type: 'content', content: 'Final answer.', meta: {} },
                {
                    type: 'tool_call_result',
                    content: '',
                    meta: {
                        deep_research: true,
                        run_id: 'research-run-1',
                        deep_research_activity: {
                            schema_version: 1,
                            events: [
                                {
                                    event: 'tool_call',
                                    sequence: 4,
                                    tool: 'web_search',
                                    arguments: { query: 'primary sources' },
                                },
                            ],
                        },
                    },
                },
                {
                    type: 'widget',
                    content: '<section class="deep-research-widget"></section>',
                    meta: {
                        widget_type: 'deep_research',
                        tool_result: { run_id: 'research-run-1' },
                    },
                },
                {
                    type: 'reasoning',
                    content: '**Stopped**',
                    meta: {
                        status: 'cancelled',
                        timestamp: '2026-08-23T10:00:00Z',
                    },
                },
            ],
        },
    ]);

    assert.deepEqual(
        calls.map(({ type, content }) => ({ type, content })),
        [
            { type: 'reasoning', content: '**Plan**' },
            { type: 'content', content: 'First answer.' },
            { type: 'tool_call', content: 'Search' },
            { type: 'reasoning', content: '**Verify**' },
            { type: 'content', content: 'Final answer.' },
            { type: 'widget', content: '<section class="deep-research-widget"></section>' },
            { type: 'reasoning', content: '**Stopped**' },
        ],
    );
    assert.equal(calls[2].meta.status, 'completed');
    assert.deepEqual(calls[2].meta.raw_output, { matches: 7 });
    assert.equal(calls[5].widgetType, 'deep_research');
    assert.equal(calls[5].widgetMeta.deep_research_activity.schema_version, 1);
    assert.equal(
        elementsById.get('a-user-1').dataset.assistantTerminalState,
        'cancelled',
        'a persisted cancellation must survive later block metadata during transcript replay',
    );
    assert.deepEqual(
        calls[5].widgetMeta.deep_research_activity.events[0].arguments,
        { query: 'primary sources' },
    );
});

test('media placeholders keep the active tool title in progress', () => {
    const source = streamMessagesSource;
    const assistantMessage = new FakeElement('article');
    const thinkingBlock = new FakeElement('div');
    thinkingBlock.className = 'assistant-thinking';
    const title = new FakeElement('span');
    title.textContent = 'Generating image';
    thinkingBlock.appendChild(title);
    assistantMessage.appendChild(thinkingBlock);

    const messageList = new FakeElement('div');
    messageList.className = 'assistant-message-list';
    assistantMessage.appendChild(messageList);

    const document = {
        getElementById(id) {
            return id === 'a-message-1' ? assistantMessage : null;
        },
        createElement(tagName) {
            return new FakeElement(tagName);
        },
    };
    const insertMediaGenPlaceholder = vm.runInNewContext(
        [
            extractFunction(source, 'appendBeforeAssistantList'),
            extractFunction(source, 'insertMediaGenPlaceholder'),
            'insertMediaGenPlaceholder;',
        ].join('\n'),
        {
            document,
            Icons: { image_gen: '<svg></svg>', video_gen: '', audio_gen: '', music: '' },
            getStreamText(_key, fallback) {
                return fallback;
            },
        },
        { filename: 'streamMessages.insertMediaGenPlaceholder.js' },
    );

    insertMediaGenPlaceholder('message-1', 'image', 'call-image-1', 'image_generation');

    const placeholder = assistantMessage.querySelector('.assistant-image-gen-placeholder');
    assert.ok(placeholder);
    assert.equal(title.textContent, 'Generating image');
    assert.equal(thinkingBlock.classList.contains('collapsed'), false);
    assert.equal(placeholder.dataset.toolCallId, 'call-image-1');
    assert.equal(placeholder.dataset.generationStatus, 'in-progress');
});

test('media placeholder failure records a terminal failed title', () => {
    const source = streamMessagesSource;
    const placeholder = { dataset: { generationStatus: 'in-progress', toolName: 'image_generation' } };
    const title = {
        textContent: 'Generating image',
        dataset: { thinkingType: 'tool' },
        classList: createClassList(['assistant-thinking-shimmer']),
    };
    const thinkingBlock = {
        dataset: {},
        classList: createClassList(),
        querySelector() {
            return title;
        },
    };
    let cleared = 0;
    const markMediaGenPlaceholderFailed = vm.runInNewContext(
        `${extractFunction(source, 'markMediaGenPlaceholderFailed')}\nmarkMediaGenPlaceholderFailed;`,
        {
            removeImageGenPlaceholder() {
                return placeholder;
            },
            findMediaGenThinkingBlock() {
                return thinkingBlock;
            },
            getToolFailedText() {
                return 'Image Generation failed';
            },
            clearMediaGenPlaceholder() {
                cleared += 1;
            },
        },
        { filename: 'streamMessages.markMediaGenPlaceholderFailed.js' },
    );

    assert.equal(markMediaGenPlaceholderFailed('message-1'), true);
    assert.equal(thinkingBlock.dataset.mediaGenerationStatus, 'failed');
    assert.equal(thinkingBlock.dataset.mediaGenerationFailureLabel, 'Image Generation failed');
    assert.equal(thinkingBlock.classList.contains('collapsed'), false);
    assert.equal(title.textContent, 'Image Generation failed');
    assert.equal(title.dataset.thinkingType, 'tool-failed');
    assert.equal(title.classList.contains('assistant-thinking-shimmer'), false);
    assert.equal(cleared, 1);
});

test('completed media placeholders remain attached for pending metadata replacement', () => {
    const source = streamMessagesSource;
    const placeholder = { dataset: { generationStatus: 'completed', toolName: 'music_generation' } };
    let failedLookup = false;
    let cleared = 0;
    const markMediaGenPlaceholderFailed = vm.runInNewContext(
        `${extractFunction(source, 'markMediaGenPlaceholderFailed')}\nmarkMediaGenPlaceholderFailed;`,
        {
            removeImageGenPlaceholder() {
                return placeholder;
            },
            findMediaGenThinkingBlock() {
                failedLookup = true;
                return null;
            },
            getToolFailedText() {
                return 'Music Generation failed';
            },
            clearMediaGenPlaceholder() {
                cleared += 1;
            },
        },
        { filename: 'streamMessages.completedMediaPlaceholder.js' },
    );

    assert.equal(markMediaGenPlaceholderFailed('message-1'), false);
    assert.equal(failedLookup, false);
    assert.equal(cleared, 0);
});

test('generic thinking finalization preserves explicit media failures', () => {
    const source = streamMessagesSource;
    const title = {
        textContent: 'Thinking',
        dataset: { thinkingType: 'thinking' },
        classList: createClassList(),
    };
    const thinkingBlock = {
        dataset: {
            mediaGenerationStatus: 'failed',
            mediaGenerationFailureLabel: 'Video Generation failed',
        },
        querySelector() {
            return title;
        },
    };
    const finalizeThinkingBlockHeader = vm.runInNewContext(
        `${extractFunction(source, 'finalizeThinkingBlockHeader')}\nfinalizeThinkingBlockHeader;`,
        {},
        { filename: 'streamMessages.finalizeFailedMedia.js' },
    );

    finalizeThinkingBlockHeader(thinkingBlock, 0);
    assert.equal(title.textContent, 'Video Generation failed');
    assert.equal(title.dataset.thinkingType, 'tool-failed');
});

test('a different follow-up tool fails the unfinished media call while duplicate announcements do not', () => {
    const source = streamMessagesSource;
    const placeholder = {
        dataset: {
            generationStatus: 'in-progress',
            toolName: 'image_generation',
            toolCallId: 'call-image-1',
        },
    };
    let failureCount = 0;
    const transitionMediaGenPlaceholderForToolCall = vm.runInNewContext(
        `${extractFunction(source, 'transitionMediaGenPlaceholderForToolCall')}\ntransitionMediaGenPlaceholderForToolCall;`,
        {
            removeImageGenPlaceholder() {
                return placeholder;
            },
            markMediaGenPlaceholderFailed() {
                failureCount += 1;
                return true;
            },
        },
        { filename: 'streamMessages.transitionMediaToolCall.js' },
    );

    assert.equal(
        transitionMediaGenPlaceholderForToolCall('message-1', 'image_generation', 'call-image-1'),
        false,
    );
    assert.equal(failureCount, 0);
    assert.equal(
        transitionMediaGenPlaceholderForToolCall('message-1', 'weather', 'call-weather-1'),
        true,
    );
    assert.equal(failureCount, 1);
});

test('every locale translates the generic tool failure label', () => {
    const i18nRoot = path.join(__dirname, '..', '..', 'i18n');
    const localeDirectories = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    localeDirectories.forEach((locale) => {
        const translations = JSON.parse(readFrontendSource(path.join(i18nRoot, locale, 'index.json'), 'utf8'));
        assert.equal(
            typeof translations.assistant_tool_failed_named,
            'string',
            `${locale} is missing assistant_tool_failed_named`,
        );
        assert.ok(
            translations.assistant_tool_failed_named.includes('{name}'),
            `${locale} must preserve the {name} placeholder`,
        );
        [
            'assistant_tool_error_generic',
            'assistant_tool_error_automations_disabled',
            'assistant_tool_error_automations_operation',
            'assistant_tool_error_automations_webhook',
            'assistant_tool_error_automations_title',
            'assistant_tool_error_automations_prompt',
            'assistant_tool_error_automations_model',
            'assistant_tool_error_automations_id',
            'assistant_tool_error_automations_active',
            'assistant_tool_error_automations_inputs',
        ].forEach((key) => {
            assert.equal(typeof translations[key], 'string', `${locale} is missing ${key}`);
            assert.ok(translations[key].trim(), `${locale} has an empty ${key}`);
        });
    });
});

test('tool errors show safe details on the matching activity and survive finalization', () => {
    const assistant = new FakeElement('article');
    const thinking = new FakeElement('div');
    thinking.className = 'assistant-thinking';
    const titleWrapper = new FakeElement('div');
    titleWrapper.className = 'assistant-thinking-title';
    const title = new FakeElement('span');
    title.className = 'assistant-thinking-shimmer';
    titleWrapper.appendChild(title);
    thinking.appendChild(titleWrapper);
    const step = new FakeElement('div');
    step.className = 'thinking-step-function-call';
    step.dataset.toolCallId = 'call-1';
    step.dataset.toolName = 'automations';
    thinking.appendChild(step);
    assistant.appendChild(thinking);

    const context = {
        document: {
            getElementById(id) {
                return id === 'a-message-1' ? assistant : null;
            },
            createElement(tagName) {
                return new FakeElement(tagName);
            },
        },
        getStreamText(key, fallback) {
            if (key === 'assistant_tool_error_automations_disabled') {
                return 'Automatisierungen sind deaktiviert.';
            }
            return fallback;
        },
        getToolFailedText() {
            return 'Automatisierungen fehlgeschlagen';
        },
    };
    const applyAssistantToolError = vm.runInNewContext(
        [
            extractFunction(streamMessagesSource, 'findAssistantToolStep'),
            extractFunction(streamMessagesSource, 'resolveToolErrorDisplayMessage'),
            extractFunction(streamMessagesSource, 'applyAssistantToolError'),
            'applyAssistantToolError;',
        ].join('\n'),
        context,
        { filename: 'streamMessages.toolError.js' },
    );

    assert.equal(applyAssistantToolError('message-1', {
        id: 'call-1',
        name: 'automations',
        error: 'Automations are disabled for your group.',
        error_code: 'automations_feature_disabled',
    }), true);
    const renderedError = step.querySelector('.function-call-error');
    assert.equal(renderedError.textContent, 'Automatisierungen sind deaktiviert.');
    assert.equal(renderedError.getAttribute('role'), 'alert');
    assert.equal(step.classList.contains('is-tool-call-failed'), true);
    assert.equal(title.textContent, 'Automatisierungen fehlgeschlagen');
    assert.equal(title.dataset.thinkingType, 'tool-failed');
    assert.equal(thinking.dataset.toolFailureStatus, 'failed');
});

test('persisted tool errors are recognized without treating ordinary results as failures', () => {
    const parseToolErrorDescriptorFromResultBlock = vm.runInNewContext(
        `${extractFunction(streamMessagesSource, 'parseToolErrorDescriptorFromResultBlock')}\nparseToolErrorDescriptorFromResultBlock;`,
        {},
        { filename: 'streamMessages.persistedToolError.js' },
    );

    assert.equal(
        JSON.stringify(parseToolErrorDescriptorFromResultBlock({
            type: 'tool_call_result',
            content: '{"error":"Choose a valid operation.","error_code":"automations_invalid_operation","retry_allowed":true}',
            meta: { tool_call_id: 'call-1', tool_name: 'automations' },
        })),
        JSON.stringify({
            id: 'call-1',
            name: 'automations',
            error: 'Choose a valid operation.',
            error_code: 'automations_invalid_operation',
        }),
    );
    assert.equal(parseToolErrorDescriptorFromResultBlock({
        type: 'tool_call_result',
        content: '{"status":"success","error":"One item was skipped"}',
    }), null);
});

test('all chat streaming surfaces consume terminal tool error events', () => {
    const sendSource = readSendMessageSource();
    const regenerationSource = readFrontendSource(path.join(__dirname, 'sending', 'regeneration.js'), 'utf8');
    const splitSource = readFrontendSource(path.join(__dirname, 'splitScreen', 'streaming.js'), 'utf8');
    const transcriptSource = readFrontendSource(path.join(__dirname, 'chatTranscriptRenderer.js'), 'utf8');

    [sendSource, regenerationSource, splitSource].forEach((source) => {
        assert.match(source, /obj\.t === ["']t_e["'][\s\S]*applyAssistantToolError/);
    });
    assert.match(transcriptSource, /parseToolErrorDescriptorFromResultBlock\(block\)[\s\S]*applyAssistantToolError/);
});

test('applyMessageActionToolbarAccessibility labels the toolbar and enables keyboard entry to hidden actions', () => {
    const source = streamMessagesSource;
    const applyMessageActionToolbarAccessibility = vm.runInNewContext(
        `${extractFunction(source, 'applyMessageActionToolbarAccessibility')}\napplyMessageActionToolbarAccessibility;`,
        {},
        { filename: 'streamMessages.applyMessageActionToolbarAccessibility.js' },
    );

    const toolbar = new FakeElement('div');
    const container = new FakeElement('article');

    applyMessageActionToolbarAccessibility(toolbar, 'Message actions', container);

    assert.equal(toolbar.attributes.role, 'toolbar');
    assert.equal(toolbar.attributes['aria-label'], 'Message actions');
    assert.equal(container.attributes.tabindex, '0');
});

test('refreshUserMessageExpandableState only enables the expander for long user prompts', () => {
    const source = streamMessagesSource;
    const context = {
        window: {},
        Icons: {
            chevron: '<svg data-icon="down"></svg>',
            chevronTop: '<svg data-icon="up"></svg>',
        },
    };
    const helpers = vm.runInNewContext(
        [
            'const USER_MESSAGE_COLLAPSED_MAX_HEIGHT = 220;',
            'const USER_MESSAGE_COLLAPSE_MIN_CHARS = 700;',
            extractFunction(source, 'getStreamText'),
            extractFunction(source, 'getChatA11yText'),
            extractFunction(source, 'escapeStreamHtml'),
            extractFunction(source, 'updateUserMessageExpandControl'),
            extractFunction(source, 'refreshUserMessageExpandableState'),
            '({ refreshUserMessageExpandableState });',
        ].join('\n'),
        context,
        { filename: 'streamMessages.userMessageExpandableState.js' },
    );

    const container = new FakeElement('article');
    const content = new FakeElement('div');
    content.className = 'user-message-content';
    content.setAttribute('data-raw-content', 'Short prompt');
    content.scrollHeight = 80;
    const button = new FakeElement('button');
    button.className = 'user-message-expand-toggle';
    container.appendChild(content);
    container.appendChild(button);

    helpers.refreshUserMessageExpandableState(container);
    assert.equal(container.dataset.userMessageCollapsible, 'false');
    assert.equal(button.hidden, true);

    content.setAttribute('data-raw-content', 'x'.repeat(701));
    helpers.refreshUserMessageExpandableState(container);
    assert.equal(container.dataset.userMessageCollapsible, 'true');
    assert.equal(container.dataset.userMessageExpanded, 'false');
    assert.equal(button.hidden, false);
    assert.equal(button.attributes['aria-expanded'], 'false');
    assert.match(button.innerHTML, /Show more/);
});

test('scheduleUserMessageExpandableRefresh observes late user prompt layout changes', () => {
    const source = streamMessagesSource;
    const animationFrames = [];
    const resizeCallbacks = [];
    class FakeResizeObserver {
        constructor(callback) {
            resizeCallbacks.push(callback);
        }

        observe(element) {
            this.element = element;
        }

        disconnect() {
            this.disconnected = true;
        }
    }
    const context = {
        window: {},
        document: {},
        ResizeObserver: FakeResizeObserver,
        requestAnimationFrame(callback) {
            animationFrames.push(callback);
            return animationFrames.length;
        },
        Icons: {
            chevron: '<svg data-icon="down"></svg>',
            chevronTop: '<svg data-icon="up"></svg>',
        },
    };
    const helpers = vm.runInNewContext(
        [
            'const USER_MESSAGE_COLLAPSED_MAX_HEIGHT = 220;',
            'const USER_MESSAGE_COLLAPSE_MIN_CHARS = 700;',
            extractFunction(source, 'getStreamText'),
            extractFunction(source, 'getChatA11yText'),
            extractFunction(source, 'escapeStreamHtml'),
            extractFunction(source, 'updateUserMessageExpandControl'),
            extractFunction(source, 'refreshUserMessageExpandableState'),
            extractFunction(source, 'queueUserMessageExpandableRefresh'),
            extractFunction(source, 'bindUserMessageExpandableLateRefresh'),
            extractFunction(source, 'scheduleUserMessageExpandableRefresh'),
            '({ scheduleUserMessageExpandableRefresh });',
        ].join('\n'),
        context,
        { filename: 'streamMessages.userMessageLateRefresh.js' },
    );

    const container = new FakeElement('article');
    const content = new FakeElement('div');
    content.className = 'user-message-content';
    content.setAttribute('data-raw-content', 'Short markdown with a late-loading image');
    content.scrollHeight = 80;
    const button = new FakeElement('button');
    button.className = 'user-message-expand-toggle';
    container.appendChild(content);
    container.appendChild(button);

    helpers.scheduleUserMessageExpandableRefresh(container);
    assert.equal(container.dataset.userMessageCollapsible, 'false');
    assert.equal(resizeCallbacks.length, 1);
    animationFrames.shift()();

    content.scrollHeight = 280;
    resizeCallbacks[0]();
    animationFrames.shift()();

    assert.equal(container.dataset.userMessageCollapsible, 'true');
    assert.equal(container.dataset.userMessageExpanded, 'false');
    assert.equal(button.hidden, false);
});

test('setUserMessageExpanded updates the user prompt expander label and aria state', () => {
    const source = streamMessagesSource;
    const announcements = [];
    const context = {
        window: {},
        Icons: {
            chevron: '<svg data-icon="down"></svg>',
            chevronTop: '<svg data-icon="up"></svg>',
        },
        announceChatMessage(message) {
            announcements.push(message);
        },
    };
    const helpers = vm.runInNewContext(
        [
            extractFunction(source, 'getStreamText'),
            extractFunction(source, 'getChatA11yText'),
            extractFunction(source, 'escapeStreamHtml'),
            extractFunction(source, 'updateUserMessageExpandControl'),
            extractFunction(source, 'setUserMessageExpanded'),
            '({ setUserMessageExpanded });',
        ].join('\n'),
        context,
        { filename: 'streamMessages.userMessageExpanded.js' },
    );

    const container = new FakeElement('article');
    const button = new FakeElement('button');
    button.className = 'user-message-expand-toggle';
    container.appendChild(button);

    helpers.setUserMessageExpanded(container, true, { announce: true });
    assert.equal(container.dataset.userMessageExpanded, 'true');
    assert.equal(button.attributes['aria-expanded'], 'true');
    assert.equal(button.attributes['aria-label'], 'Collapse message');
    assert.match(button.innerHTML, /Show less/);
    assert.deepEqual(announcements, ['Collapse message']);

    helpers.setUserMessageExpanded(container, false);
    assert.equal(container.dataset.userMessageExpanded, 'false');
    assert.equal(button.attributes['aria-expanded'], 'false');
    assert.equal(button.attributes['aria-label'], 'Show full message');
    assert.match(button.innerHTML, /Show more/);
});

test('renderCanvasWidgetForFile prefers original filenames over stored UUID filenames', () => {
    const source = streamMessagesSource;
    const calls = [];
    const renderCanvasWidgetForFile = vm.runInNewContext(
        `${extractFunction(source, 'renderCanvasWidgetForFile')}\nrenderCanvasWidgetForFile;`,
        {
            window: {
                canvasMarkdownWidget: {
                    renderSavedWidgetFromFile(payload) {
                        calls.push(payload);
                        return true;
                    },
                },
            },
        },
        { filename: 'streamMessages.renderCanvasWidgetForFile.js' },
    );

    const rendered = renderCanvasWidgetForFile({
        messageId: 'message-1',
        fileId: 'file-1',
        fileData: {
            file_name: '9f61e6a7-6f30-4e7c-9d5b-0b438b7d8e3d.html',
            file_type: 'text/html',
            meta: { original_filename: 'website.html' },
        },
    });

    assert.equal(rendered, true);
    assert.equal(calls[0].fileName, 'website.html');
});

function createHarness() {
    const assistantMessage = new FakeElement('article');
    assistantMessage.dataset.isStreaming = 'true';

    const messageList = new FakeElement('div');
    messageList.className = 'assistant-message-list';
    assistantMessage.appendChild(messageList);

    const document = {
        getElementById(id) {
            return id === 'a-message-1' ? assistantMessage : null;
        },
        createElement(tagName) {
            return new FakeElement(tagName);
        },
    };

    const context = {
        document,
        applyAssistantMessageAccessibility() {},
        announceChatMessage() {},
        getChatA11yText(_key, fallback) {
            return fallback;
        },
    };

    const source = streamMessagesSource;
    const appendAssistantError = vm.runInNewContext(
        `${extractFunction(source, 'appendAssistantError')}\nappendAssistantError;`,
        context,
        { filename: 'streamMessages.appendAssistantError.js' },
    );

    return { appendAssistantError, assistantMessage };
}

test('appendAssistantError renders streamed provider errors as text', () => {
    const { appendAssistantError, assistantMessage } = createHarness();
    const payload = '<img src=x onerror="globalThis.__xss = true"><b>boom</b>';

    appendAssistantError('message-1', payload, '');

    const errorBlock = assistantMessage.children[0];
    assert.equal(errorBlock.className, 'assistant-message-error');
    assert.equal(errorBlock.textContent, payload);
    assert.deepEqual(errorBlock.innerHtmlAssignments, []);
    assert.equal(errorBlock.querySelector('img'), null);
    assert.equal(assistantMessage.children[1].className, 'assistant-message-list');
});

test('only explicit arbitrary HTML widgets use opaque iframe rendering', () => {
    const source = streamMessagesSource;
    const renderStart = source.indexOf('function renderBackendWidgetIframe(');
    const renderEnd = source.indexOf('\nfunction appendAssistantWidget(', renderStart);
    const renderSource = source.slice(renderStart, renderEnd);

    assert.match(source, /function shouldRenderBackendWidgetIframe\(widgetMeta, widgetType = ''\)/);
    assert.match(source, /renderMode === 'iframe'\s*\|\|\s*widgetMeta\.allow_scripts === true/);
    assert.doesNotMatch(source, /BACKEND_SCRIPT_WIDGET_TYPES/);
    assert.match(source, /renderMode === 'frontend'/);
    assert.match(source, /window\.nativeToolWidgets\?\.render\?\./);
    assert.match(source, /const BACKEND_WIDGET_IFRAME_SANDBOX_WITH_SCRIPTS = 'allow-scripts'/);
    assert.match(source, /iframe\.setAttribute\('sandbox', allowScripts \? BACKEND_WIDGET_IFRAME_SANDBOX_WITH_SCRIPTS : ''\)/);
    assert.doesNotMatch(source, /BACKEND_WIDGET_IFRAME_SANDBOX_WITH_SCRIPTS = 'allow-scripts allow-same-origin'/);
    assert.match(source, /function createBackendWidgetFrameUrl\(widgetHtml, widgetType\)/);
    assert.match(source, /\/api\/v1\/llm\/widgets\/frame/);
    assert.match(source, /theme_mode: document\.documentElement\?\.getAttribute\('data-mode'\)/);
    assert.match(source, /const frameId = String\(payload\?\.frame_id \|\| ''\)\.trim\(\)/);
    assert.match(source, /return \{ frameId, frameUrl \}/);
    assert.match(source, /iframe\.dataset\.backendWidgetFrameId = frameId/);
    assert.match(source, /iframe\.removeAttribute\('srcdoc'\)/);
    assert.match(source, /iframe\.src = frameUrl/);
    assert.match(source, /iframe\.addEventListener\('error'/);
    assert.match(source, /BACKEND_WIDGET_LOAD_TIMEOUT_MS/);
    assert.match(source, /frame\._backendWidgetMarkLoaded\(\)/);
    assert.match(source, /\.catch\(renderFrameLoadError\)/);
    assert.match(source, /errorMessage\.className = 'assistant-widget-backend-error'/);
    assert.match(source, /errorMessage\.setAttribute\('role', 'status'\)/);
    assert.match(source, /'chat_widget_load_error'/);
    assert.ok(
        renderSource.indexOf("iframe.addEventListener('error'")
        > renderSource.indexOf('.then(({ frameId, frameUrl }) => {'),
        'iframe listeners must ignore the initial about:blank document',
    );
    assert.ok(
        renderSource.indexOf("iframe.addEventListener('load'")
        < renderSource.indexOf('iframe.src = frameUrl'),
        'iframe listeners must be registered before the backend URL is assigned',
    );
    assert.doesNotMatch(source, /const frameBlob = new Blob/);
    assert.doesNotMatch(source, /iframe\.src = URL\.createObjectURL/);
    assert.doesNotMatch(source, /iframe\.srcdoc = buildBackendWidgetIframeDocument\(decodedHtml, frameId\)/);
});

test('visualization widgets use the shared static-first renderer and keep large arguments hidden', () => {
    const source = streamMessagesSource;
    const appendSource = extractFunction(source, 'appendAssistantWidget');

    assert.match(source, /'create_visualization',\s*\n\s*\]\);/);
    assert.match(source, /if \(normalizedName === 'create_visualization'\) return null;/);
    assert.match(source, /create_visualization:\s*\{[\s\S]*?inProgress: 'Visualization',[\s\S]*?argKey: null,/);
    assert.match(appendSource, /widgetType \|\| ''\)\.trim\(\)\.toLowerCase\(\) === 'visualization'/);
    assert.match(appendSource, /window\.OmlorixVisualizer\.mount/);
    assert.match(appendSource, /classList\.add\('markdown-body', 'assistant-visualization-widget'\)/);
    assert.match(appendSource, /allowScripts: false/);
    assert.match(appendSource, /visualizationMeta\.capabilities/);
    assert.ok(
        appendSource.indexOf("=== 'visualization'") < appendSource.indexOf('shouldRenderBackendWidgetIframe'),
        'visualization widgets must bypass the generic backend iframe renderer',
    );
});

test('subagent widgets auto-open only for live stream events', () => {
    const source = streamMessagesSource;

    assert.match(source, /function renderSubagentEventAsChat\(state, event, isLive = false\)/);
    assert.match(source, /\{ autoOpen: isLive \}/);
    assert.match(source, /renderSubagentEventAsChat\(state, event, false\)/);
    assert.match(source, /renderSubagentEventAsChat\(state, state\.events\[state\.events\.length - 1\], true\)/);
});

function createAssistantCopyHelpers() {
    const source = streamMessagesSource;
    const context = {
        window: {
            getComputedStyle(element) {
                return {
                    display: element.__display || 'block',
                    visibility: element.__visibility || 'visible',
                };
            },
        },
    };

    return vm.runInNewContext(
        `${extractFunction(source, 'getVisibleAssistantCopyContentElements')}
${extractFunction(source, 'getAssistantCopyText')}
({ getVisibleAssistantCopyContentElements, getAssistantCopyText });`,
        context,
        { filename: 'streamMessages.copyHelpers.js' },
    );
}

function createAssistantCopyEntry({
    rawContent = null,
    textContent = '',
    connected = true,
    hiddenContent = false,
    hiddenMessage = false,
    contentDisplay = 'block',
    messageDisplay = 'block',
} = {}) {
    const messageEl = {
        hidden: hiddenMessage,
        __display: messageDisplay,
        __visibility: 'visible',
    };

    const contentEl = {
        isConnected: connected,
        hidden: hiddenContent,
        innerText: textContent,
        textContent,
        __display: contentDisplay,
        __visibility: 'visible',
        getAttribute(name) {
            if (name === 'data-raw-content') {
                return rawContent;
            }
            return null;
        },
        closest(selector) {
            return selector === '.assistant-message' ? messageEl : null;
        },
    };

    return { messageEl, contentEl };
}

function createAssistantCopyContainer(entries) {
    return {
        querySelectorAll(selector) {
            assert.equal(selector, '.assistant-message .assistant-message-content');
            return entries.map((entry) => entry.contentEl);
        },
    };
}

test('assistant copy helpers concatenate all visible response sections in order', () => {
    const {
        getVisibleAssistantCopyContentElements,
        getAssistantCopyText,
    } = createAssistantCopyHelpers();

    const first = createAssistantCopyEntry({ rawContent: 'First section' });
    const hidden = createAssistantCopyEntry({
        rawContent: 'Hidden section',
        messageDisplay: 'none',
    });
    const second = createAssistantCopyEntry({ textContent: 'Second section' });
    const disconnected = createAssistantCopyEntry({
        rawContent: 'Disconnected section',
        connected: false,
    });

    const container = createAssistantCopyContainer([first, hidden, second, disconnected]);
    const visible = getVisibleAssistantCopyContentElements(container);

    assert.equal(visible.length, 2);
    assert.equal(visible[0], first.contentEl);
    assert.equal(visible[1], second.contentEl);
    assert.equal(getAssistantCopyText(container), 'First section\n\nSecond section');
});
