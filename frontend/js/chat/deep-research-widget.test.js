const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const vm = require('node:vm');
const { readSendMessageSource } = require('./sending/source.cjs');

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}`);
    assert.notEqual(start, -1, `${functionName} not found`);
    const parametersStart = source.indexOf('(', start);
    let parametersDepth = 0;
    let parametersEnd = -1;
    for (let index = parametersStart; index < source.length; index += 1) {
        if (source[index] === '(') parametersDepth += 1;
        if (source[index] === ')') {
            parametersDepth -= 1;
            if (parametersDepth === 0) {
                parametersEnd = index;
                break;
            }
        }
    }
    assert.notEqual(parametersEnd, -1, `${functionName} parameters were not closed`);
    const bodyStart = source.indexOf('{', parametersEnd);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') {
            depth -= 1;
            if (depth === 0) return source.slice(start, index + 1);
        }
    }
    throw new Error(`${functionName} body was not closed`);
}

const repoRoot = path.resolve(__dirname, '../../..');
const widgetSource = readFrontendSource(
    path.join(repoRoot, 'frontend/js/chat/deep-research-widget.js'),
    'utf8',
);
const widgetStyles = readFrontendSource(
    path.join(repoRoot, 'frontend/css/chat/deep-research-widget.css'),
    'utf8',
);
const sendMessageSource = readSendMessageSource();
const streamSource = readFrontendSource(
    path.join(repoRoot, 'frontend/js/chat/stream.js'),
    'utf8',
);
const chatsSource = readFrontendSource(
    path.join(repoRoot, 'frontend/js/chat/chats.js'),
    'utf8',
);
const chatScriptSource = readFrontendSource(
    path.join(repoRoot, 'frontend/js/chat/script.js'),
    'utf8',
);
const splitScreenSource = readFrontendSource(
    path.join(repoRoot, 'frontend/js/chat/splitScreen.js'),
    'utf8',
);
const userSettingsSource = readFrontendSource(
    path.join(repoRoot, 'frontend/js/chat/userSettings/init.js'),
    'utf8',
);
const indexSource = readFrontendSource(
    path.join(repoRoot, 'frontend/index.html'),
    'utf8',
);

test('deep research widget is loaded and consumes the normal chat stream', () => {
    assert.match(indexSource, /css\/chat\/deep-research-widget\.css/);
    assert.match(indexSource, /js\/chat\/deep-research-widget\.js/);
    assert.match(indexSource, /id="deepResearchSidebar"/);
    assert.match(indexSource, /data-deep-research-tab="activity"/);
    assert.match(indexSource, /data-deep-research-tab="report"/);
    assert.match(indexSource, /data-deep-research-tab="sources"/);
    assert.match(indexSource, /data-deep-research-tab="files"/);
    assert.match(indexSource, /id="deepResearchRequestStreams" role="log"/);
    assert.match(indexSource, /class="deep-research-query-disclosure"/);
    assert.match(indexSource, /class="deep-research-query-chevron" data-deep-research-icon="chevron"/);
    assert.match(indexSource, /id="deepResearchExportFormat"[\s\S]*value="pdf"[\s\S]*value="md"/);
    assert.match(indexSource, /slide-presentation-preview-download-controls[\s\S]*id="deepResearchExportControls"/);
    assert.match(indexSource, /slide-presentation-preview-download-select[\s\S]*id="deepResearchExportFormat"/);
    assert.match(indexSource, /om-button deep-research-export-button[\s\S]*deep_research_export_action/);
    assert.match(widgetStyles, /\.deep-research-query-disclosure\[open\] \.deep-research-query-chevron svg/);
    assert.match(widgetStyles, /\.deep-research-export-controls/);
    assert.match(widgetStyles, /\.deep-research-export-button\.disabled/);
    assert.match(widgetStyles, /\.deep-research-export-button \.deep-research-export-icon\s*\{[\s\S]*display: inline-flex;/);
    assert.doesNotMatch(widgetStyles, /\.deep-research-query-disclosure summary::after/);
    assert.doesNotMatch(indexSource, /id="deepResearchSteps"/);
    assert.doesNotMatch(indexSource, /deepResearchModelStreamHeading/);
    assert.doesNotMatch(indexSource, /deep-research-sidebar-eyebrow/);
    assert.doesNotMatch(indexSource, /deep-research-sidebar-icon/);
    assert.match(widgetSource, /handleDeepResearchEvent/);
    assert.match(widgetSource, /event\.t !== 'deep_research_evt'/);
    assert.match(sendMessageSource, /\/api\/v1\/chats\/attach\?generation_id=/);
    assert.match(sendMessageSource, /handleDeepResearchEvent\(obj,/);
    assert.match(streamSource, /sendMessage\("", true, String\(data\.generation_id\)\)/);
    assert.doesNotMatch(widgetSource, /\/events\?after=/);
    assert.doesNotMatch(widgetSource, /startPolling/);
    assert.match(widgetSource, /openSidebar/);
    assert.match(widgetSource, /\[data-action="toggle"\]/);
    assert.match(widgetSource, /closeSidebar\(\{ restoreFocus: false \}\)/);
    assert.match(widgetSource, /toggleButton\.setAttribute\('aria-expanded', String\(expanded\)\)/);
    assert.match(widgetSource, /widget\.dataset\.model/);
    assert.match(widgetSource, /widget\.dataset\.errorCode/);
    assert.doesNotMatch(widgetStyles, /\.deep-research-meta\s*\{/);
    assert.match(widgetSource, /handleSidebarKeyboard/);
});

test('deep research exports only completed reports through the authenticated report endpoint', () => {
    assert.match(widgetSource, /function exportReport\(state\)/);
    assert.match(widgetSource, /normalizeStatus\(state\.status\) !== 'completed'/);
    assert.match(widgetSource, /!state\.finalReportPath/);
    assert.match(widgetSource, /\/api\/v1\/deep-research\/runs\/\$\{encodeURIComponent\(runId\)\}\/export/);
    assert.match(widgetSource, /window\.authedFetch/);
    assert.match(widgetSource, /filenameFromDisposition/);
    assert.match(widgetSource, /saveExportBlob\(await response\.blob\(\), filename\)/);
    assert.match(widgetSource, /deep_research_export_success/);
    assert.match(widgetSource, /deep_research_export_failed/);
    assert.match(widgetSource, /window\.chatDownloadControls\.setDownloadBusy/);
    assert.match(widgetSource, /window\.chatDownloadControls\.getSelectedDownloadFormat/);
    assert.match(widgetSource, /exportControls\.hidden = !canExport/);
    assert.match(widgetSource, /preferredExportFormat = selectedFormat/);
});

test('deep research export filenames prefer server disposition and remain readable', () => {
    const helpers = vm.runInNewContext(
        [
            extractFunction(widgetSource, 'filenameFromDisposition'),
            extractFunction(widgetSource, 'fallbackExportFilename'),
            '({ filenameFromDisposition, fallbackExportFilename });',
        ].join('\n'),
    );

    assert.equal(
        helpers.filenameFromDisposition(
            'attachment; filename="Evidence.pdf"; filename*=UTF-8\'\'R%C3%A9sum%C3%A9.pdf',
        ),
        'Résumé.pdf',
    );
    assert.equal(
        helpers.fallbackExportFilename(
            { report: '# **Market / outlook**\n\nBody', query: 'Fallback' },
            'md',
        ),
        'Market - outlook.md',
    );
});

test('deep research renders each model request as an interleaved main-chat timeline', () => {
    assert.match(widgetSource, /requests: new Map\(\)/);
    assert.match(widgetSource, /requestOrder: \[\]/);
    assert.match(widgetSource, /eventName === 'reasoning_delta'/);
    assert.match(widgetSource, /appendRequestTextBlock\(request, 'reasoning', event, delta\)/);
    assert.match(widgetSource, /eventName === 'content_delta'/);
    assert.match(widgetSource, /appendRequestTextBlock\(request, 'content', event, delta\)/);
    assert.match(widgetSource, /appendRequestToolBlock\(requestForEvent\(state, event, phase\), event\)/);
    assert.match(widgetSource, /arguments: event\?\.arguments \?\? event\?\.args \?\? null/);
    assert.match(widgetSource, /renderAssistantToolParams\(step, argumentsObject\)/);
    assert.match(widgetSource, /humanReadableToolName\(block\.name, block\.arguments\)/);
    assert.match(widgetSource, /completeRequestToolBlock\(requestForEvent\(state, event, phase\), event\)/);
    assert.match(widgetSource, /chronologicalRequestBlocks\(request\)/);
    assert.match(widgetSource, /leftSequence - rightSequence/);
    assert.match(widgetSource, /eventName === 'report_updated'/);
    assert.match(widgetSource, /state\.report = publicReportMarkdown\(event\?\.report/);
    assert.match(
        widgetSource,
        /return normalized === 'deep-research'\s*\|\| normalized === 'native-research';/,
    );
    assert.match(widgetSource, /renderRequestStreams\(state\)/);
    assert.doesNotMatch(widgetSource, /state\.events\.length > 80/);
    assert.match(widgetStyles, /\.deep-research-request-streams/);
    assert.match(widgetSource, /assistant-thinking collapsed deep-research-stream-thinking/);
    assert.match(widgetSource, /assistant-thinking-header/);
    assert.match(widgetSource, /assistant-thinking-content/);
    assert.match(widgetSource, /assistant-thinking-body/);
    assert.match(widgetSource, /thinking-step thinking-step-function-call/);
    assert.match(widgetSource, /assistant-message-content/);
    assert.match(widgetSource, /document\.createElement\('details'\)/);
    assert.match(widgetSource, /buildRequestSteps\(state\)/);
    assert.match(widgetSource, /knownPipelinePhases\(state\)/);
    assert.match(widgetSource, /restoredSteps: \[\]/);
    assert.match(widgetSource, /widget\.dataset\.activitySteps/);
    assert.match(widgetSource, /id: `restored:\$\{normalized\}`/);
    assert.match(widgetSource, /hydrateActivitySnapshot\(state, activitySnapshot\)/);
    assert.match(widgetSource, /updateFromEvent\(state, event, \{ render: false, autoOpen: false \}\)/);
    assert.match(widgetSource, /appliedSequences: new Set\(\)/);
    assert.match(widgetSource, /normalizeStatus\(state\.status\) === 'completed' \? 'report' : 'activity'/);
    assert.match(widgetSource, /const TERMINAL_STATUSES = new Set\(\['completed', 'failed', 'error', 'cancelled'\]\)/);
    assert.match(widgetSource, /document\.getElementById\('deepResearchSidebarError'\)/);
    assert.match(widgetSource, /deep_research_error_code/);
    assert.doesNotMatch(widgetSource, /normalizeStatus\(state\.status\) !== 'completed'\) return/);
    assert.match(widgetSource, /normalizeStatus\(state\.status\) === 'completed'/);
    assert.match(widgetSource, /status: 'pending'/);
    assert.match(widgetSource, /deep_research_request_pending/);
    assert.match(widgetSource, /renderRequestTimeline\(card, request\)/);
    assert.doesNotMatch(widgetSource, /renderRequestActivity/);
    assert.doesNotMatch(widgetSource, /data-role="thinking-section"/);
    assert.doesNotMatch(widgetSource, /data-role="content-section"/);
    assert.doesNotMatch(indexSource, /id="deepResearchMilestonesHeading"/);
    assert.doesNotMatch(indexSource, /id="deepResearchActivityList"/);
    assert.doesNotMatch(
        widgetStyles,
        /\.deep-research-request\.is-running \.deep-research-request-ordinal::before/,
    );
    assert.doesNotMatch(widgetStyles, /animation: spin 850ms linear infinite/);
    assert.match(widgetSource, /loading: icons\.loading_circle/);
    assert.match(widgetSource, /function renderRequestOrdinal\(/);
    assert.match(widgetSource, /const loadingMarkup = icon\('loading'\)/);
    assert.match(widgetStyles, /\.deep-research-request-ordinal > svg/);
    assert.match(widgetStyles, /data-status="pending"/);
    assert.match(widgetStyles, /\.deep-research-card-chevron\[aria-expanded="true"\] svg/);
    assert.match(widgetSource, /chevron: icons\.chevron/);
    assert.match(widgetSource, /chevron\.className = 'deep-research-request-chevron'/);
    assert.match(widgetStyles, /\.deep-research-request\[open\] \.deep-research-request-chevron svg/);
    assert.doesNotMatch(widgetStyles, /\.deep-research-request > summary::after/);
    assert.match(widgetStyles, /\.deep-research-request-timeline > \.assistant-thinking/);
    assert.match(indexSource, /id="deepResearchRequestStreams" role="log"/);
    assert.match(widgetSource, /container\.insertBefore\(card, expectedCard\)/);
    assert.doesNotMatch(widgetSource, /container\.appendChild\(card\)/);
    assert.match(widgetSource, /sidebarScrollLocked/);
    assert.match(widgetSource, /handleSidebarScroll/);
    assert.match(widgetSource, /function resetSidebarScrollToTop\(state\)/);
    assert.match(widgetSource, /state\.sidebarScrollLocked = true/);
    assert.match(widgetSource, /scrollRoot\.scrollTop = 0/);
    assert.match(widgetSource, /window\.requestAnimationFrame\?\.\(\(\) => \{\s*reset\(\);\s*window\.requestAnimationFrame\?\.\(reset\)/);
    assert.match(widgetSource, /function openSidebar\(runId, \{ resetScroll = true \} = \{\}\)/);
    assert.match(widgetSource, /openSidebar\(state\.runId, \{ resetScroll: false \}\)/);
    assert.match(widgetSource, /state\.resetScrollAfterTerminalHydration = state\.terminalHydrationPending/);
    assert.match(widgetSource, /if \(state\.resetScrollAfterTerminalHydration\) \{/);
    assert.match(widgetSource, /const scrollSnapshot =/);
    assert.match(widgetSource, /window\.requestAnimationFrame\?\.\(restoreScroll\)/);
});

test('deep research interpolates localized event placeholders for live and restored tools', () => {
    const translations = {
        deep_research_tool_completed: '{name} abgeschlossen',
        deep_research_tool_failed: '{name} fehlgeschlagen',
    };
    const translatedEventMessage = vm.runInNewContext(
        [
            extractFunction(widgetSource, 't'),
            extractFunction(widgetSource, 'formatT'),
            extractFunction(widgetSource, 'humanReadableToolName'),
            extractFunction(widgetSource, 'toolLabel'),
            extractFunction(widgetSource, 'translatedEventMessage'),
            'translatedEventMessage;',
        ].join('\n'),
        {
            window: {
                // Exercise the local formatting fallback too. This mirrors the
                // short startup window before the shared formatter is present.
                getTranslation(key, fallback) {
                    return translations[key] ?? fallback;
                },
            },
        },
        { filename: 'deep-research-widget.eventTranslation.js' },
    );

    assert.equal(
        translatedEventMessage({
            message_key: 'deep_research_tool_completed',
            event: 'tool_result',
            name: 'web_search',
        }),
        'Web Search abgeschlossen',
    );
    assert.equal(
        translatedEventMessage({
            message_key: 'deep_research_tool_failed',
            event: 'tool_failed',
            tool: 'fetch_url',
            success: false,
        }),
        'Fetch URL fehlgeschlagen',
    );
    assert.equal(
        translatedEventMessage({ message: 'Unkeyed event' }),
        'Unkeyed event',
    );
});

test('deep research citations retain only absolute HTTP and HTTPS URLs', () => {
    const normalizeCitations = vm.runInNewContext(
        `${extractFunction(widgetSource, 'normalizeCitations')}\nnormalizeCitations;`,
        { URL },
        { filename: 'deep-research-widget.normalizeCitations.js' },
    );

    assert.deepEqual(
        Array.from(normalizeCitations([
            'https://example.com/source',
            { url: 'HTTP://example.org/article', title: 'Article' },
            { url: 'javascript:alert(1)', title: 'Unsafe' },
            { canonical_url: 'data:text/html,unsafe' },
            { url: '/relative/source' },
        ]), (citation) => ({ ...citation })),
        [
            {
                url: 'https://example.com/source',
                title: 'https://example.com/source',
                snippet: '',
            },
            {
                url: 'HTTP://example.org/article',
                title: 'Article',
                snippet: '',
            },
        ],
    );
});

test('running deep research steps use the shared loading-circle icon', () => {
    let prefersReducedMotion = false;
    const renderRequestOrdinal = vm.runInNewContext(
        [
            extractFunction(widgetSource, 'normalizeStatus'),
            extractFunction(widgetSource, 'icon'),
            extractFunction(widgetSource, 'shouldReduceMotion'),
            extractFunction(widgetSource, 'renderRequestOrdinal'),
            'renderRequestOrdinal;',
        ].join('\n'),
        {
            Icons: {
                loading_circle: '<svg data-loading="shared"><animateTransform /></svg>',
            },
            window: {
                matchMedia: () => ({ matches: prefersReducedMotion }),
            },
        },
        { filename: 'deep-research-widget.requestOrdinal.js' },
    );
    const ordinal = { dataset: {}, innerHTML: '', textContent: '' };

    renderRequestOrdinal(ordinal, 'running', 1);
    assert.equal(
        ordinal.innerHTML,
        '<svg data-loading="shared"><animateTransform /></svg>',
    );

    prefersReducedMotion = true;
    renderRequestOrdinal(ordinal, 'running', 1);
    assert.equal(ordinal.innerHTML, '<svg data-loading="shared"></svg>');

    renderRequestOrdinal(ordinal, 'completed', 1);
    assert.equal(ordinal.textContent, '1');
});

test('deep research uses normal localized tool activity labels when available', () => {
    const labels = vm.runInNewContext(
        [
            extractFunction(widgetSource, 't'),
            extractFunction(widgetSource, 'formatT'),
            extractFunction(widgetSource, 'humanReadableToolName'),
            extractFunction(widgetSource, 'toolLabel'),
            extractFunction(widgetSource, 'translatedEventMessage'),
            '({ translatedEventMessage, humanReadableToolName });',
        ].join('\n'),
        {
            window: {
                getToolInProgressText: (name) => name === 'web_search' ? 'Websuche läuft' : '',
                getToolCompletedText: (name) => name === 'web_search' ? 'Websuche abgeschlossen' : '',
                getToolFailedText: (name) => name === 'web_search' ? 'Websuche fehlgeschlagen' : '',
            },
        },
        { filename: 'deep-research-widget.toolLabels.js' },
    );

    assert.equal(
        labels.translatedEventMessage({ event: 'tool_call', name: 'web_search' }),
        'Websuche läuft',
    );
    assert.equal(
        labels.translatedEventMessage({ event: 'tool_result', name: 'web_search', success: true }),
        'Websuche abgeschlossen',
    );
    assert.equal(
        labels.translatedEventMessage({ event: 'tool_result', name: 'web_search', success: false }),
        'Websuche fehlgeschlagen',
    );
    assert.equal(
        labels.humanReadableToolName('mcp_google_drive_search_a1b2c3d4'),
        'MCP Google Drive Search',
    );
});

test('explicitly opening a persisted report resets restored sidebar scroll to the top', () => {
    const scrollRoot = {
        scrollTop: 940,
    };
    const animationFrames = [];
    const resetSidebarScrollToTop = vm.runInNewContext(
        [
            "let activeRunId = 'run-1';",
            extractFunction(widgetSource, 'resetSidebarScrollToTop'),
            'resetSidebarScrollToTop;',
        ].join('\n'),
        {
            document: {
                querySelector(selector) {
                    return selector === '.deep-research-sidebar-scroll' ? scrollRoot : null;
                },
            },
            window: {
                requestAnimationFrame(callback) {
                    animationFrames.push(callback);
                },
            },
        },
        { filename: 'deep-research-widget.scrollReset.js' },
    );
    const state = {
        runId: 'run-1',
        sidebarScrollLocked: false,
        sidebarScrollTop: 940,
    };

    resetSidebarScrollToTop(state);
    assert.equal(scrollRoot.scrollTop, 0);
    assert.equal(state.sidebarScrollTop, 0);
    assert.equal(state.sidebarScrollLocked, true);

    // Simulate the browser restoring the overflow position after the first
    // layout, and the Markdown report changing height after the second.
    scrollRoot.scrollTop = 720;
    animationFrames.shift()();
    assert.equal(scrollRoot.scrollTop, 0);
    scrollRoot.scrollTop = 480;
    animationFrames.shift()();
    assert.equal(scrollRoot.scrollTop, 0);
});

test('deep research sidebar supports cancellation and terminal report files without resume', () => {
    assert.match(widgetSource, /\/api\/v1\/chats\/cancel\?generation_id=/);
    assert.doesNotMatch(widgetSource, /\/deep-research\/runs\/.*\/cancel/);
    assert.match(widgetSource, /finalReportPath/);
    assert.doesNotMatch(widgetSource, /finalHtmlPath/);
    assert.match(widgetSource, /workspace\.zip/);
    assert.doesNotMatch(widgetSource, /\/resume/);
    assert.doesNotMatch(indexSource, /deep_research_resume_action/);
    assert.doesNotMatch(indexSource, /deepResearchSidebarOpenReport/);
    assert.doesNotMatch(indexSource, /deep_research_open_report/);
});

test('deep research styles respect hover capability and the browser motion preference', () => {
    assert.match(
        widgetStyles,
        /\.deep-research-sidebar-header\s*\{[\s\S]*?height: 50px;[\s\S]*?border-bottom: 1px solid var\(--border-color\);/,
    );
    assert.match(widgetStyles, /@media \(hover: hover\) and \(pointer: fine\)/);
    assert.match(
        widgetStyles,
        /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.deep-research-progress-bar/,
    );
});

test('deep research uses the same space-reserving preview lifecycle as Canvas', () => {
    assert.match(widgetStyles, /\.deep-research-widget\s*\{[\s\S]*?width: 100%;/);
    assert.doesNotMatch(widgetStyles, /\.deep-research-widget\s*\{[\s\S]*?width: min\(100%, 42rem\);/);
    assert.match(widgetStyles, /\.deep-research-sidebar\s*\{/);
    assert.match(widgetStyles, /@media \(max-width: 900px\)/);
    assert.match(widgetStyles, /var\(--canvas-markdown-preview-width\)/);
    assert.match(widgetStyles, /body\.deep-research-preview-open \.main-container/);
    assert.match(indexSource, /id="deepResearchSidebar" role="complementary"/);
    assert.match(
        indexSource,
        /id="deepResearchSidebar"[^>]*aria-hidden="true"[^>]*inert/,
    );
    assert.match(indexSource, /id="deepResearchPreviewResizer" role="separator"/);
    assert.doesNotMatch(indexSource, /id="deepResearchSidebarBackdrop"/);
    assert.match(
        widgetSource,
        /setMainSidebarAutoCollapsed\('deep-research-preview', true\)/,
    );
    assert.match(widgetSource, /closeOtherArtifactPreviews\?\.\('deep-research-preview'\)/);
    assert.match(widgetSource, /canvasSizingController\(\)\?\.applyPreviewWidthRatio/);
    assert.match(widgetSource, /sidebar\.removeAttribute\('inert'\)/);
    assert.match(widgetSource, /sidebar\?\.setAttribute\('inert', ''\)/);
});

test('deep research sidebar summary contains only the research query', () => {
    const summaryStart = indexSource.indexOf('<section class="deep-research-sidebar-summary">');
    const summaryEnd = indexSource.indexOf('</section>', summaryStart);
    assert.notEqual(summaryStart, -1);
    assert.notEqual(summaryEnd, -1);
    const summarySource = indexSource.slice(summaryStart, summaryEnd);

    assert.match(summarySource, /id="deepResearchSidebarQuery"/);
    assert.doesNotMatch(summarySource, /deep-research-sidebar-run-meta/);
    assert.doesNotMatch(summarySource, /deep-research-sidebar-progress/);
    assert.doesNotMatch(summarySource, /deepResearchSidebarError/);
    assert.doesNotMatch(indexSource, /id="deepResearchSidebarStatus"/);
    assert.doesNotMatch(indexSource, /id="deepResearchSidebarModel"/);
    assert.match(
        indexSource,
        /id="deepResearchPanelActivity"[\s\S]*id="deepResearchSidebarError"/,
    );
});

test('deep research preview closes across chat and app navigation like Canvas', () => {
    assert.match(widgetSource, /function hidePreviewPanel\(\)[\s\S]*closeSidebar\(\{ restoreFocus: false \}\)/);
    assert.match(widgetSource, /window\.deepResearchWidget = \{[\s\S]*hidePreviewPanel/);

    // Switching transcripts closes the old message-scoped preview before the
    // replacement chat is rendered.
    const loadChatViewSource = extractFunction(chatsSource, 'loadChatView');
    assert.match(
        loadChatViewSource,
        /isSwitchingChats[\s\S]*window\.deepResearchWidget\.hidePreviewPanel\(\)/,
    );

    // Both leaving chat for another app section and creating a fresh chat go
    // through script.js, so the close must be present in both reset paths.
    assert.match(
        extractFunction(chatScriptSource, 'hideChatContainer'),
        /window\.deepResearchWidget\.hidePreviewPanel\(\)/,
    );
    assert.match(
        extractFunction(chatScriptSource, 'showChatStartContainer'),
        /window\.deepResearchWidget\.hidePreviewPanel\(\)/,
    );

    // Settings uses the common preview interface without special-casing the
    // Deep Research implementation.
    const settingsCloseSource = extractFunction(
        userSettingsSource,
        'closeChatPreviewPanelsForUserSettings',
    );
    assert.match(
        settingsCloseSource,
        /window\.deepResearchWidget/,
    );
    assert.match(
        settingsCloseSource,
        /typeof widget\.hidePreviewPanel === 'function'/,
    );

    const splitResetStart = splitScreenSource.indexOf('    function resetPanels()');
    const splitResetEnd = splitScreenSource.indexOf(
        '    // ───── Panel Header Updates',
        splitResetStart,
    );
    const splitResetSource = splitScreenSource.slice(splitResetStart, splitResetEnd);
    assert.match(splitResetSource, /window\.deepResearchWidget\.hidePreviewPanel\(\)/);
});

test('deep research preserves degraded completion warnings across reloads', () => {
    assert.match(widgetSource, /warningCode: ''/);
    assert.match(widgetSource, /widget\.dataset\.warningCode/);
    assert.match(widgetSource, /event\?\.warning_code/);
    assert.match(widgetSource, /deep_research_completed_with_warnings/);

    const localeRoot = path.join(repoRoot, 'frontend/i18n');
    fs.readdirSync(localeRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .forEach((entry) => {
            const dictionary = JSON.parse(
                readFrontendSource(path.join(localeRoot, entry.name, 'index.json'), 'utf8'),
            );
            assert.ok(
                dictionary.deep_research_stream_interrupted_retrying,
                `${entry.name} is missing the interrupted-stream retry translation`,
            );
            assert.ok(
                dictionary.deep_research_completed_with_warnings,
                `${entry.name} is missing the degraded-completion translation`,
            );
        });
});
