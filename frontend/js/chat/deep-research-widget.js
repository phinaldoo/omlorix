(function () {
    'use strict';

    const TERMINAL_STATUSES = new Set(['completed', 'failed', 'error', 'cancelled']);
    const states = new Map();
    let activeRunId = '';
    let observer = null;
    let previewResizeActive = false;
    let sidebarRenderFrame = 0;
    let preferredExportFormat = 'pdf';
    let exportButtonDefaultHtml = '';

    function t(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function formatT(key, fallback, variables) {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, variables);
        }
        // `getTranslation` may already be available while the shared formatter
        // is still loading. Format the translated template, not only its
        // English fallback, so placeholders never leak during that window.
        return String(t(key, fallback) || '').replace(/\{(\w+)\}/g, (_, token) => {
            const value = variables?.[token];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    function normalizeStatus(value) {
        const status = String(value || '').trim().toLowerCase();
        return status === 'queued' ? 'running' : (status || 'running');
    }

    function normalizePhase(value) {
        const phase = String(value || '').trim().toLowerCase();
        return phase === 'queued' ? 'starting' : (phase || 'starting');
    }

    function isTerminal(status) {
        return TERMINAL_STATUSES.has(normalizeStatus(status));
    }

    function fileUrl(runId, path, download = false) {
        const encodedPath = String(path || '')
            .split('/')
            .filter(Boolean)
            .map(encodeURIComponent)
            .join('/');
        const url = `/api/v1/deep-research/runs/${encodeURIComponent(runId)}/files/${encodedPath}`;
        return download ? `${url}?download=true` : url;
    }

    function exportUrl(runId, format) {
        const params = new URLSearchParams({
            format: String(format || 'pdf').toLowerCase() === 'md' ? 'md' : 'pdf',
        });
        return `/api/v1/deep-research/runs/${encodeURIComponent(runId)}/export?${params}`;
    }

    function filenameFromDisposition(value) {
        const header = String(value || '');
        const encodedMatch = header.match(/filename\*=UTF-8''([^;]+)/i);
        if (encodedMatch?.[1]) {
            try {
                return decodeURIComponent(encodedMatch[1]);
            } catch (_error) {
                // Fall through to the ASCII filename for malformed headers.
            }
        }
        const fallbackMatch = header.match(/(?:^|;)\s*filename="?([^";]+)"?/i);
        return String(fallbackMatch?.[1] || '').trim();
    }

    function fallbackExportFilename(state, format) {
        const extension = format === 'md' ? 'md' : 'pdf';
        const heading = String(state?.report || '')
            .split(/\r?\n/)
            .map((line) => line.match(/^\s*#\s+(.+?)\s*#*\s*$/)?.[1] || '')
            .find(Boolean);
        const rawTitle = heading || state?.query || 'deep-research-report';
        const safeTitle = String(rawTitle)
            .replace(/[*_`[\]]+/g, '')
            .replace(/[\/:*?"<>|]/g, '-')
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, 160) || 'deep-research-report';
        return `${safeTitle}.${extension}`;
    }

    function saveExportBlob(blob, filename) {
        if (typeof window.chatDownloadControls?.saveBlobAsFile === 'function') {
            window.chatDownloadControls.saveBlobAsFile(blob, filename);
            return;
        }
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    }

    function publicReportMarkdown(markdown, runId) {
        const prefix = `/api/v1/deep-research/runs/${encodeURIComponent(runId)}/files/`;
        return String(markdown || '').replace(
            /(!?\[[^\]]*\]\()(artifacts\/[A-Za-z0-9._/-]+)/g,
            (_, opening, path) => opening + prefix + path.split('/').map(encodeURIComponent).join('/'),
        );
    }

    function createState(runId) {
        return {
            runId,
            widgets: new Set(),
            status: 'running',
            phase: 'starting',
            progress: 4,
            message: t('deep_research_starting', 'Starting research'),
            query: '',
            model: '',
            generationId: '',
            finalReportPath: '',
            archivePath: '',
            errorCode: '',
            warningCode: '',
            events: [],
            eventKeys: new Set(),
            appliedSequences: new Set(),
            requests: new Map(),
            requestOrder: [],
            knownPhases: [],
            restoredSteps: [],
            report: '',
            citations: [],
            files: [],
            cancelRequested: false,
            terminalHydrated: false,
            terminalHydrationPending: false,
            resetScrollAfterTerminalHydration: false,
            autoOpened: false,
            sidebarScrollLocked: false,
            sidebarScrollTop: 0,
            exportBusy: false,
        };
    }

    function getState(runId) {
        const normalized = String(runId || '').trim();
        if (!normalized) return null;
        if (!states.has(normalized)) states.set(normalized, createState(normalized));
        return states.get(normalized);
    }

    function icon(name) {
        const icons = typeof Icons !== 'undefined' ? Icons : (window.Icons || {});
        const mapping = {
            research: icons.lightning || icons.globe || '',
            activity: icons.clock || '',
            report: icons.book || icons.file || '',
            sources: icons.citations || icons.globe || '',
            files: icons.folder || icons.file || '',
            close: icons.close || '',
            open: icons.open_window || icons.arrow_right || '',
            arrow: icons.arrow_right || '',
            check: icons.check || '',
            download: icons.download || '',
            thinking: icons.thinking || icons.sparkle || icons.lightning || '',
            content: icons.file || icons.book || '',
            chevron: icons.chevron || '',
            loading: icons.loading_circle || '',
        };
        return mapping[name] || '';
    }

    /**
     * Return whether motion should be reduced for newly rendered activity UI.
     * The browser media query follows the operating-system accessibility choice.
     */
    function shouldReduceMotion() {
        try {
            return typeof window.matchMedia === 'function'
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        } catch (_error) {
            return false;
        }
    }

    /**
     * Render either the shared loading-circle icon or the stable step number.
     * Keeping a render key avoids replacing the animated SVG for every streamed
     * token, which would otherwise restart its animation continuously.
     */
    function renderRequestOrdinal(element, requestStatus, ordinalNumber) {
        if (!element) return;
        const running = normalizeStatus(requestStatus) === 'running';
        const reduceMotion = running && shouldReduceMotion();
        const renderKey = running
            ? `loading:${reduceMotion ? 'static' : 'animated'}`
            : `number:${ordinalNumber}`;
        if (element.dataset.renderKey === renderKey) return;

        if (running) {
            const loadingMarkup = icon('loading');
            // SVG SMIL animations are not stopped by CSS `animation: none`, so
            // remove only the rotation node when reduced motion is requested.
            element.innerHTML = reduceMotion
                ? loadingMarkup.replace(/<animateTransform\b[^>]*\/>/gi, '')
                : loadingMarkup;
        } else {
            element.textContent = String(ordinalNumber);
        }
        element.dataset.renderKey = renderKey;
    }

    function hydrateIcons(root = document) {
        root.querySelectorAll('[data-deep-research-icon]').forEach((element) => {
            const markup = icon(element.dataset.deepResearchIcon);
            if (markup && element.innerHTML !== markup) element.innerHTML = markup;
        });
        root.querySelectorAll('.deep-research-card-icon[data-role="icon"]').forEach((element) => {
            if (!element.innerHTML) element.innerHTML = icon('research');
        });
        root.querySelectorAll('.deep-research-card-chevron[data-role="chevron"]').forEach((element) => {
            if (!element.innerHTML) element.innerHTML = icon('arrow');
        });
    }

    function statusText(state) {
        const status = normalizeStatus(state.status);
        if (status === 'completed' && state.warningCode) {
            return t(
                'deep_research_completed_with_warnings',
                'Research complete with warnings',
            );
        }
        if (status === 'completed') return t('deep_research_completed', 'Research complete');
        if (status === 'failed' || status === 'error') return t('deep_research_failed', 'Deep research failed.');
        if (status === 'cancelled') return t('deep_research_cancelled', 'Deep research cancelled.');
        return state.message || phaseText(state.phase);
    }

    function phaseText(phase) {
        const normalized = normalizePhase(phase);
        if (normalized === 'starting') return t('deep_research_starting', 'Starting research');
        const key = `deep_research_phase_${normalized.replaceAll('-', '_')}`;
        const fallbacks = {
            planning: 'Planning the research',
            'deep-research': 'Collecting and synthesizing evidence',
            'native-research': 'Conducting research',
            'evidence-audit': 'Reviewing evidence and sources',
        };
        if (normalized.startsWith('release-gate')) {
            return t(key, t('deep_research_reviewing', 'Reviewing report quality'));
        }
        if (normalized.startsWith('final-revision')) {
            return t(key, t('deep_research_finalizing', 'Finalizing the report'));
        }
        if (normalized === 'failed' || normalized === 'error') {
            return t('deep_research_failed', 'Deep research failed.');
        }
        if (normalized === 'cancelled') {
            return t('deep_research_cancelled', 'Deep research cancelled.');
        }
        if (normalized === 'completed') return t('deep_research_completed', 'Research complete');
        return t(key, fallbacks[normalized] || t('deep_research_status_running', 'Researching'));
    }

    function progressForPhase(phase, status) {
        if (isTerminal(status)) return 100;
        const normalized = normalizePhase(phase);
        if (normalized === 'starting') return 4;
        if (normalized === 'planning') return 12;
        if (normalized === 'deep-research' || normalized === 'native-research') return 42;
        if (normalized === 'evidence-audit') return 68;
        if (normalized.startsWith('final-revision')) return 80;
        if (normalized.startsWith('release-gate')) return 92;
        return 8;
    }

    function registerWidget(widget) {
        if (!(widget instanceof Element)) return null;
        const runId = String(widget.dataset.runId || widget.dataset.widgetId || '').trim();
        const state = getState(runId);
        if (!state) return null;

        state.widgets.add(widget);
        state.query ||= String(widget.querySelector('.deep-research-query')?.textContent || '').trim();
        // Keep the model available to the detail sidebar without rendering it
        // inside the compact chat card. The element lookup supports cards
        // persisted before the model moved to a data attribute.
        state.model ||= String(
            widget.dataset.model
            || widget.querySelector('.deep-research-meta')?.textContent
            || '',
        ).trim();
        state.generationId ||= String(widget.dataset.generationId || '').trim();
        state.finalReportPath ||= String(widget.dataset.finalReportPath || '').trim();
        state.archivePath ||= String(widget.dataset.archivePath || '').trim();
        state.errorCode ||= String(widget.dataset.errorCode || '').trim();
        state.warningCode ||= String(widget.dataset.warningCode || '').trim();
        try {
            const knownPhases = JSON.parse(widget.dataset.knownPhases || '[]');
            if (Array.isArray(knownPhases) && knownPhases.length) {
                state.knownPhases = knownPhases.map(normalizePhase);
            }
        } catch (_error) {
            // A malformed historical widget must not prevent the sidebar from opening.
        }
        try {
            const restoredSteps = JSON.parse(widget.dataset.activitySteps || '[]');
            if (Array.isArray(restoredSteps) && restoredSteps.length) {
                state.restoredSteps = restoredSteps
                    .filter((step) => step && typeof step === 'object' && step.phase)
                    .slice(0, 24)
                    .map((step) => ({
                        phase: normalizePhase(step.phase),
                        status: ['pending', 'running', 'completed', 'failed', 'cancelled']
                            .includes(String(step.status || '').toLowerCase())
                            ? String(step.status).toLowerCase()
                            : 'completed',
                        durationSeconds: step.duration_seconds !== null
                            && step.duration_seconds !== undefined
                            && Number.isFinite(Number(step.duration_seconds))
                            ? Math.max(0, Number(step.duration_seconds))
                            : null,
                    }));
            }
        } catch (_error) {
            // Reloading an older widget without persisted activity stays supported.
        }
        try {
            normalizeFiles(state, JSON.parse(widget.dataset.files || '[]'));
        } catch (_error) {
            // Older persisted widgets did not contain a serialized file list.
        }
        state.status = normalizeStatus(widget.dataset.status || state.status);
        state.phase = normalizePhase(widget.dataset.phase || state.phase);
        state.progress = Math.max(state.progress, progressForPhase(state.phase, state.status));
        widget.dataset.status = state.status;
        widget.dataset.phase = state.phase;
        hydrateIcons(widget);
        renderCard(state, widget);
        if (isTerminal(state.status)) hydrateTerminalState(state);
        return state;
    }

    function renderCard(state, widget) {
        if (!widget?.isConnected) return;
        const message = statusText(state);
        const statusElement = widget.querySelector('[data-role="status"]');
        const progressElement = widget.querySelector('[data-role="progress"]');
        const progressRoot = progressElement?.closest('[role="progressbar"]');
        const errorElement = widget.querySelector('[data-role="error"]');
        const normalizedProgress = Math.max(0, Math.min(100, Number(state.progress) || 0));

        widget.dataset.status = normalizeStatus(state.status);
        widget.dataset.phase = normalizePhase(state.phase);
        widget.setAttribute('aria-busy', isTerminal(state.status) ? 'false' : 'true');
        if (statusElement) statusElement.textContent = message;
        if (progressElement) progressElement.style.width = `${normalizedProgress}%`;
        progressRoot?.setAttribute('aria-valuenow', String(normalizedProgress));

        const failed = ['failed', 'error'].includes(normalizeStatus(state.status));
        if (errorElement) {
            errorElement.hidden = !failed;
            errorElement.textContent = failed && state.errorCode
                ? formatT('deep_research_error_code', 'Error code: {code}', { code: state.errorCode })
                : (failed ? message : '');
        }
        const openButton = widget.querySelector('[data-action="open"]');
        if (openButton) {
            openButton.textContent = normalizeStatus(state.status) === 'completed'
                ? t('deep_research_view_report', 'View report')
                : t('deep_research_open_details', 'View research');
        }
        const toggleButton = widget.querySelector('[data-action="toggle"]');
        if (toggleButton) {
            const sidebar = document.getElementById('deepResearchSidebar');
            const expanded = Boolean(
                sidebar?.classList.contains('is-open')
                && activeRunId === state.runId,
            );
            const label = expanded
                ? t('deep_research_close_aria', 'Close research details')
                : t('deep_research_open_details', 'View research');
            toggleButton.setAttribute('aria-expanded', String(expanded));
            toggleButton.setAttribute('aria-label', label);
            toggleButton.title = label;
        }
    }

    function renderAllCards(state) {
        state.widgets.forEach((widget) => {
            if (widget.isConnected) renderCard(state, widget);
            else state.widgets.delete(widget);
        });
    }

    function addActivity(state, {
        sequence = 0,
        eventType = '',
        message = '',
        phase = '',
        createdAt = '',
    } = {}) {
        const text = String(message || '').trim();
        if (!text) return;
        const key = sequence
            ? `sequence:${sequence}`
            : `${eventType}:${phase}:${text}`;
        if (state.eventKeys.has(key)) return;
        state.eventKeys.add(key);
        state.events.push({
            sequence: Number(sequence || 0),
            eventType: String(eventType || ''),
            message: text,
            phase: normalizePhase(phase || state.phase),
            createdAt: String(createdAt || ''),
        });
    }

    function ensureRequest(state, requestId, phase, event = null) {
        const normalizedId = String(requestId || '').trim();
        const sequence = Number(event?.sequence || 0);
        if (!state.requests.has(normalizedId)) {
            state.requests.set(normalizedId, {
                id: normalizedId,
                phase: normalizePhase(phase || state.phase),
                status: 'running',
                streamBlocks: [],
                nextBlockOrdinal: 0,
                contentText: '',
                durationSeconds: null,
                sequence: Number.isFinite(sequence) ? sequence : 0,
            });
            state.requestOrder.push(normalizedId);
        }
        const request = state.requests.get(normalizedId);
        request.phase = normalizePhase(phase || request.phase || state.phase);
        if (!request.sequence && Number.isFinite(sequence)) request.sequence = sequence;
        return request;
    }

    function requestForEvent(state, event, phase) {
        return ensureRequest(state, event?.request_id, phase, event);
    }

    function completeOpenRequestBlock(request) {
        const block = request?.streamBlocks?.[request.streamBlocks.length - 1];
        if (block?.status === 'running') block.status = 'completed';
        return block || null;
    }

    function appendRequestTextBlock(request, type, event, delta) {
        let block = request.streamBlocks[request.streamBlocks.length - 1];
        if (!block || block.type !== type) {
            completeOpenRequestBlock(request);
            request.nextBlockOrdinal += 1;
            block = {
                id: `${request.id}:stream:${request.nextBlockOrdinal}`,
                type,
                text: '',
                status: 'running',
                sequence: Number(event?.sequence || 0),
                durationSeconds: null,
            };
            request.streamBlocks.push(block);
        }
        block.text = event?.replace ? String(delta || '') : block.text + String(delta || '');
        block.status = 'running';
        return block;
    }

    function appendRequestToolBlock(request, event) {
        completeOpenRequestBlock(request);
        request.nextBlockOrdinal += 1;
        const toolName = String(event?.name || event?.tool || 'tool').trim();
        const toolCallId = String(event?.tool_call_id || event?.id || '').trim();
        const block = {
            id: `${request.id}:stream:${request.nextBlockOrdinal}`,
            type: 'tool',
            name: toolName,
            toolCallId,
            arguments: event?.arguments ?? event?.args ?? null,
            status: 'running',
            sequence: Number(event?.sequence || 0),
        };
        request.streamBlocks.push(block);
        return block;
    }

    function completeRequestToolBlock(request, event) {
        const toolCallId = String(event?.tool_call_id || event?.id || '').trim();
        const toolName = String(event?.name || event?.tool || '').trim();
        const candidates = request.streamBlocks.filter((block) => (
            block.type === 'tool'
            && block.status === 'running'
            && (!toolCallId || block.toolCallId === toolCallId)
            && (!toolName || block.name === toolName)
        ));
        const block = candidates[candidates.length - 1]
            || [...request.streamBlocks].reverse().find((item) => item.type === 'tool');
        if (!block) return null;
        block.status = event?.success === false ? 'failed' : 'completed';
        return block;
    }

    function isReportPhase(phase) {
        const normalized = normalizePhase(phase);
        return normalized === 'deep-research'
            || normalized === 'native-research';
    }

    function scheduleSidebarRender(state) {
        if (activeRunId !== state.runId) return;
        if (sidebarRenderFrame) return;
        const render = () => {
            sidebarRenderFrame = 0;
            if (activeRunId === state.runId) renderSidebar(state);
        };
        if (typeof window.requestAnimationFrame === 'function') {
            sidebarRenderFrame = window.requestAnimationFrame(render);
        } else {
            sidebarRenderFrame = window.setTimeout(render, 0);
        }
    }

    function translatedEventMessage(event) {
        const key = String(event?.message_key || '').trim();
        const eventName = String(event?.event || event?.event_type || '').trim().toLowerCase();
        if (['tool_call', 'tool_started'].includes(eventName)) {
            return toolLabel(event, false);
        }
        if (['tool_result', 'tool_completed', 'tool_failed'].includes(eventName)) {
            return toolLabel(event, true);
        }
        if (key) {
            // Orchestrator events store translation keys so both live streams
            // and persisted snapshots localize in the browser. Tool events
            // carry their interpolation value as `name`; using plain `t()`
            // here previously exposed the literal "{name}" token.
            return formatT(key, String(event?.message || ''), {
                name: String(event?.name || event?.tool || 'tool').trim(),
                session_id: String(event?.session_id || '').trim(),
                code: String(event?.error_code || '').trim(),
            });
        }
        return String(event?.message || '').trim();
    }

    function humanReadableToolName(toolName, argumentsValue = null) {
        const rawName = String(toolName || 'tool').trim();
        if (typeof window.getToolDisplayName === 'function') {
            const config = typeof window.getToolActivityConfig === 'function'
                ? window.getToolActivityConfig(rawName, argumentsValue)
                : {};
            const sharedDisplayName = String(window.getToolDisplayName(config, rawName) || '').trim();
            if (sharedDisplayName) return sharedDisplayName;
        }

        // Keep the widget readable in isolated embeds where the message renderers
        // is unavailable. Match its identifier cleanup, including removal of
        // the private routing digest appended to MCP tool names.
        const uppercaseParts = new Set(['api', 'csv', 'html', 'http', 'https', 'id', 'json', 'mcp', 'pdf', 'sql', 'uri', 'url']);
        return rawName
            .replace(/^(mcp_.+)_([0-9a-f]{8})$/i, '$1')
            .split('_')
            .filter(Boolean)
            .map((part) => uppercaseParts.has(part.toLowerCase())
                ? part.toUpperCase()
                : part.charAt(0).toUpperCase() + part.slice(1))
            .join(' ') || t('assistant_tool_generic_name', 'Tool');
    }

    function toolLabel(event, completed = false) {
        const rawName = String(event?.name || event?.tool || 'tool').trim();
        const argumentsValue = event?.arguments ?? event?.args ?? null;
        if (!completed && typeof window.getToolInProgressText === 'function') {
            return window.getToolInProgressText(rawName, argumentsValue);
        }
        if (completed && event?.success === false && typeof window.getToolFailedText === 'function') {
            return window.getToolFailedText(rawName, argumentsValue);
        }
        if (completed && typeof window.getToolCompletedText === 'function') {
            return window.getToolCompletedText(rawName, argumentsValue);
        }

        const name = humanReadableToolName(rawName, argumentsValue);
        if (!completed) {
            return formatT('deep_research_tool_running', 'Using {name}', { name });
        }
        return event?.success === false
            ? formatT('deep_research_tool_failed', '{name} failed', { name })
            : formatT('deep_research_tool_completed', '{name} completed', { name });
    }

    function updateFromEvent(
        state,
        event,
        { render = true, autoOpen = true } = {},
    ) {
        const sequence = Number(event?.sequence || 0);
        if (sequence > 0) {
            if (state.appliedSequences.has(sequence)) return false;
            state.appliedSequences.add(sequence);
        }
        const eventName = String(event?.event || event?.event_type || '').trim().toLowerCase();
        const status = normalizeStatus(event?.status || state.status);
        const phase = normalizePhase(event?.phase || state.phase);
        const explicitProgress = Number(event?.progress);
        let message = translatedEventMessage(event);
        const requestId = String(event?.request_id || '').trim();
        const streamEvent = [
            'llm_request_started',
            'llm_request_completed',
            'llm_request_failed',
            'reasoning_delta',
            'reasoning_completed',
            'content_delta',
            'output_delta',
            'tool_call',
            'tool_started',
            'tool_result',
            'tool_completed',
            'tool_failed',
        ].includes(eventName);

        state.status = status;
        state.phase = phase;
        state.generationId ||= String(event?.generation_id || '').trim();
        state.finalReportPath = String(event?.final_report_path || state.finalReportPath || '').trim();
        state.archivePath = String(event?.archive_path || state.archivePath || '').trim();
        state.errorCode = String(event?.error_code || state.errorCode || '').trim();
        state.warningCode = String(event?.warning_code || state.warningCode || '').trim();
        state.progress = Number.isFinite(explicitProgress)
            ? Math.max(state.progress, Math.min(100, explicitProgress))
            : Math.max(state.progress, progressForPhase(phase, status));

        // Terminal events can carry useful partial artifacts even when the run
        // failed or was cancelled. Apply them before the status-specific branch
        // so live and persisted sidebars expose the same available information.
        if (['complete', 'completed', 'error', 'failed', 'cancelled'].includes(eventName)) {
            if (event?.report) state.report = publicReportMarkdown(event.report, state.runId);
            if (Array.isArray(event?.citations)) state.citations = normalizeCitations(event.citations);
            normalizeFiles(state, event?.files || []);
        }

        if (eventName === 'llm_request_started') {
            const request = ensureRequest(state, requestId, phase, event);
            request.status = 'running';
            request.durationSeconds = null;
            message ||= phaseText(phase);
        } else if (eventName === 'reasoning_delta') {
            const request = ensureRequest(state, requestId, phase, event);
            const delta = String(event?.delta || '');
            appendRequestTextBlock(request, 'reasoning', event, delta);
            message = '';
        } else if (eventName === 'reasoning_completed') {
            const request = ensureRequest(state, requestId, phase, event);
            const duration = Number(event?.duration_seconds);
            const block = [...request.streamBlocks]
                .reverse()
                .find((item) => item.type === 'reasoning');
            if (block) {
                block.status = 'completed';
                if (Number.isFinite(duration)) block.durationSeconds = duration;
            }
            message = '';
        } else if (eventName === 'content_delta' || eventName === 'output_delta') {
            const request = ensureRequest(state, requestId, phase, event);
            const delta = String(event?.preview || event?.delta || '');
            appendRequestTextBlock(request, 'content', event, delta);
            request.contentText = event?.replace ? delta : request.contentText + delta;
            if (isReportPhase(phase)) {
                state.report = publicReportMarkdown(request.contentText, state.runId);
            }
            message = '';
        } else if (eventName === 'llm_request_completed') {
            const request = ensureRequest(state, requestId, phase, event);
            request.status = 'completed';
            completeOpenRequestBlock(request);
            const duration = Number(event?.duration_seconds);
            if (Number.isFinite(duration)) request.durationSeconds = duration;
            message ||= phaseText(phase);
        } else if (eventName === 'llm_request_failed') {
            const request = ensureRequest(state, requestId, phase, event);
            request.status = 'failed';
            const block = completeOpenRequestBlock(request);
            if (block) block.status = 'failed';
            message ||= t('deep_research_failed', 'Deep research failed.');
        } else if (eventName === 'session') {
            normalizeFiles(state, event?.files || []);
            message ||= t('deep_research_workspace_ready', 'Research workspace ready');
        } else if (eventName === 'report_updated') {
            state.report = publicReportMarkdown(event?.report || '', state.runId);
            message ||= phaseText(phase);
        } else if (eventName === 'tool_call' || eventName === 'tool_started') {
            appendRequestToolBlock(requestForEvent(state, event, phase), event);
            message ||= toolLabel(event, false);
        } else if (['tool_result', 'tool_completed', 'tool_failed'].includes(eventName)) {
            completeRequestToolBlock(requestForEvent(state, event, phase), event);
            message ||= toolLabel(event, true);
        } else if (eventName === 'complete' || eventName === 'completed') {
            state.status = 'completed';
            state.phase = 'completed';
            state.progress = 100;
            if (event?.report) state.report = publicReportMarkdown(event.report, state.runId);
            if (Array.isArray(event?.citations)) state.citations = normalizeCitations(event.citations);
            normalizeFiles(state, event?.files || []);
            state.terminalHydrated = true;
            message ||= t('deep_research_completed', 'Research complete');
        } else if (eventName === 'error' || eventName === 'failed') {
            state.status = 'failed';
            state.progress = 100;
            message ||= t('deep_research_failed', 'Deep research failed.');
        } else if (eventName === 'cancelled') {
            state.status = 'cancelled';
            state.progress = 100;
            message ||= t('deep_research_cancelled', 'Deep research cancelled.');
        } else {
            message ||= phaseText(phase);
        }

        if (message) state.message = message;
        if (!streamEvent) {
            // Activity can arrive between two chunks of the same output type.
            // Close the current block at that exact boundary so the next delta
            // creates a new chronological block after the activity step.
            completeOpenRequestBlock(latestRequestForPhase(state, phase));
            addActivity(state, {
                sequence: event?.sequence,
                eventType: eventName,
                message,
                phase,
                createdAt: event?.created_at,
            });
        }
        if (render) {
            renderAllCards(state);
            scheduleSidebarRender(state);
        }

        if (autoOpen && !state.autoOpened && !isTerminal(state.status)) {
            state.autoOpened = true;
            openSidebar(state.runId, { resetScroll: false });
        }
        return true;
    }

    function normalizeCitations(citations) {
        if (!Array.isArray(citations)) return [];
        return citations.map((citation) => {
            const url = String(
                typeof citation === 'string'
                    ? citation
                    : citation?.url || citation?.canonical_url || '',
            ).trim();
            try {
                const protocol = new URL(url).protocol.toLowerCase();
                if (protocol !== 'http:' && protocol !== 'https:') return null;
            } catch (_error) {
                return null;
            }
            if (typeof citation === 'string') {
                return { url, title: url, snippet: '' };
            }
            return {
                url,
                title: String(citation?.title || url).trim(),
                snippet: String(citation?.snippet || citation?.excerpt || '').trim(),
            };
        }).filter(Boolean);
    }

    function normalizeFiles(state, files) {
        if (!Array.isArray(files)) return;
        const existing = new Set(state.files);
        files.forEach((file) => {
            const path = String(file?.path || file || '').trim();
            if (path) existing.add(path);
        });
        state.files = Array.from(existing);
    }

    function renderSidebar(state) {
        const sidebar = document.getElementById('deepResearchSidebar');
        if (!sidebar || activeRunId !== state.runId) return;

        const query = document.getElementById('deepResearchSidebarQuery');
        const error = document.getElementById('deepResearchSidebarError');
        const cancel = document.getElementById('deepResearchSidebarCancel');
        const exportControls = document.getElementById('deepResearchExportControls');
        const exportFormat = document.getElementById('deepResearchExportFormat');
        const exportButton = document.getElementById('deepResearchExportButton');

        if (query) query.textContent = state.query || t('deep_research_query_unavailable', 'Research request');
        if (error) {
            error.hidden = !state.errorCode;
            error.textContent = state.errorCode
                ? formatT('deep_research_error_code', 'Error code: {code}', { code: state.errorCode })
                : '';
        }

        renderRequestStreams(state);
        renderReport(state);
        renderSources(state);
        renderFiles(state);

        if (cancel) {
            cancel.hidden = isTerminal(state.status) || !state.generationId;
            const stopping = state.cancelRequested;
            cancel.disabled = stopping;
            if (stopping) {
                cancel.dataset.confirming = 'false';
                cancel.textContent = t('deep_research_cancel_requested', 'Stopping research…');
            } else if (cancel.dataset.confirming !== 'true') {
                cancel.disabled = false;
                cancel.dataset.confirming = 'false';
                cancel.textContent = t('deep_research_cancel_action', 'Cancel research');
            }
        }
        if (exportButton && exportFormat) {
            const canExport = normalizeStatus(state.status) === 'completed'
                && Boolean(state.finalReportPath);
            if (exportControls) exportControls.hidden = !canExport;
            if (!state.exportBusy) exportFormat.value = preferredExportFormat;

            // Deep Research deliberately uses the same state helper as canvas
            // and notes so disabled, busy, ARIA, and select state cannot drift.
            if (!exportButtonDefaultHtml) exportButtonDefaultHtml = exportButton.innerHTML;
            if (typeof window.chatDownloadControls?.setDownloadBusy === 'function') {
                window.chatDownloadControls.setDownloadBusy({
                    button: exportButton,
                    select: exportFormat,
                    busy: state.exportBusy,
                    enabled: canExport,
                    defaultHtml: exportButtonDefaultHtml,
                    disabledClass: 'disabled',
                    manageTabIndex: false,
                    busyLabel: t('deep_research_export_preparing', 'Preparing…'),
                    idleLabel: t('deep_research_export_action', 'Export'),
                    labelSelector: '.deep-research-export-label',
                });
            } else {
                const disabled = !canExport || state.exportBusy;
                exportButton.disabled = disabled;
                exportButton.classList.toggle('disabled', disabled);
                exportButton.classList.toggle('is-busy', state.exportBusy);
                exportButton.setAttribute('aria-disabled', String(disabled));
                exportFormat.disabled = disabled;
                exportButton.toggleAttribute('aria-busy', state.exportBusy);
            }

            const iconElement = exportButton.querySelector('.deep-research-export-icon');
            if (iconElement) {
                const iconMarkup = icon(state.exportBusy ? 'loading' : 'download');
                iconElement.innerHTML = state.exportBusy && shouldReduceMotion()
                    ? iconMarkup.replace(/<animateTransform\b[^>]*\/>/gi, '')
                    : iconMarkup;
            }
            const title = state.exportBusy
                ? t('deep_research_export_preparing', 'Preparing…')
                : t('deep_research_export_aria', 'Export report');
            exportButton.title = title;
            exportButton.setAttribute('aria-label', title);
        }
    }

    async function exportReport(state) {
        if (
            !state
            || state.exportBusy
            || normalizeStatus(state.status) !== 'completed'
            || !state.finalReportPath
        ) {
            return;
        }

        const formatSelect = document.getElementById('deepResearchExportFormat');
        const selectedValue = typeof window.chatDownloadControls?.getSelectedDownloadFormat === 'function'
            ? window.chatDownloadControls.getSelectedDownloadFormat(
                formatSelect,
                preferredExportFormat,
            )
            : String(formatSelect?.value || preferredExportFormat);
        const selectedFormat = String(selectedValue)
            .toLowerCase() === 'md' ? 'md' : 'pdf';
        preferredExportFormat = selectedFormat;
        state.exportBusy = true;
        renderSidebar(state);

        try {
            const fetchImpl = typeof window.authedFetch === 'function'
                ? window.authedFetch.bind(window)
                : window.fetch.bind(window);
            const response = await fetchImpl(exportUrl(state.runId, selectedFormat), {
                credentials: 'same-origin',
            });
            if (!response.ok) throw new Error(`report_export_failed:${response.status}`);
            const filename = filenameFromDisposition(
                response.headers?.get?.('content-disposition'),
            ) || fallbackExportFilename(state, selectedFormat);
            saveExportBlob(await response.blob(), filename);

            const formatLabel = selectedFormat === 'pdf'
                ? t('deep_research_export_pdf', 'PDF')
                : t('deep_research_export_markdown', 'Markdown');
            if (typeof showNotification === 'function') {
                showNotification(
                    formatT(
                        'deep_research_export_success',
                        'Report exported as {format}.',
                        { format: formatLabel },
                    ),
                    'success',
                );
            }
        } catch (error) {
            console.error('Failed to export Deep Research report:', error);
            if (typeof showNotification === 'function') {
                showNotification(
                    t(
                        'deep_research_export_failed',
                        'The report could not be exported. Please try again.',
                    ),
                    'error',
                );
            }
        } finally {
            state.exportBusy = false;
            if (activeRunId === state.runId) renderSidebar(state);
        }
    }

    function requestStatusText(request) {
        if (request.status === 'pending') {
            return t('deep_research_request_pending', 'Pending');
        }
        if (request.status === 'completed') {
            return t('deep_research_request_completed', 'Complete');
        }
        if (request.status === 'failed') {
            return t('deep_research_request_failed', 'Failed');
        }
        if (request.status === 'cancelled') {
            return t('deep_research_cancelled', 'Deep research cancelled.');
        }
        return t('deep_research_request_running', 'Generating');
    }

    function renderStreamMarkdown(element, source) {
        const markdown = String(source || '');
        if (element.dataset.markdownSource === markdown) return;
        const scrollSnapshot = {
            top: element.scrollTop,
            followEnd: element.clientHeight > 0
                && element.scrollHeight - element.scrollTop - element.clientHeight <= 8,
        };
        element.dataset.markdownSource = markdown;
        if (!markdown) {
            element.textContent = t('deep_research_stream_waiting', 'Waiting for output…');
            return;
        }
        if (typeof window.renderMarkdownContent === 'function') {
            window.renderMarkdownContent(element, markdown);
        } else {
            element.textContent = markdown;
        }
        // Markdown rendering replaces the stream's descendants. Restore the
        // nested preview viewport so reading an earlier paragraph remains
        // possible while new tokens arrive. A reader already at the end keeps
        // following the live output.
        const restoreScroll = () => {
            const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
            element.scrollTop = scrollSnapshot.followEnd
                ? maxScrollTop
                : Math.min(scrollSnapshot.top, maxScrollTop);
        };
        restoreScroll();
        window.requestAnimationFrame?.(restoreScroll);
    }

    function phaseStepStatus(state, phase, events) {
        const stopped = events.some((event) => [
            'error',
            'failed',
            'cancelled',
            'llm_request_failed',
        ].includes(String(event.eventType || '').toLowerCase()));
        if (stopped) return 'failed';
        if (normalizeStatus(state.status) === 'completed') return 'completed';
        if (!isTerminal(state.status) && normalizePhase(state.phase) === normalizePhase(phase)) {
            return 'running';
        }
        if (!events.length) return 'pending';
        return 'completed';
    }

    function knownPipelinePhases(state) {
        if (state.knownPhases.length) return state.knownPhases;
        // Older persisted widgets do not carry the explicit phase list. Keep
        // their preview useful by falling back to the custom pipeline.
        return ['planning', 'deep-research', 'evidence-audit'];
    }

    function buildRequestSteps(state) {
        const requestEntries = state.requestOrder
            .map((requestId, requestIndex) => {
                const request = state.requests.get(requestId);
                return request ? {
                    ...request,
                    events: [],
                    requestIndex,
                    synthetic: false,
                } : null;
            })
            .filter(Boolean);
        const requestsByPhase = new Map();
        requestEntries.forEach((entry) => {
            const phase = normalizePhase(entry.phase);
            if (!requestsByPhase.has(phase)) requestsByPhase.set(phase, []);
            requestsByPhase.get(phase).push(entry);
        });

        const syntheticByPhase = new Map();
        state.restoredSteps.forEach((step, stepIndex) => {
            const normalized = normalizePhase(step.phase);
            if (requestsByPhase.has(normalized) || syntheticByPhase.has(normalized)) return;
            syntheticByPhase.set(normalized, {
                id: `restored:${normalized}`,
                phase: normalized,
                status: step.status,
                streamBlocks: [],
                nextBlockOrdinal: 0,
                contentText: '',
                durationSeconds: step.durationSeconds,
                sequence: 0,
                requestIndex: state.requestOrder.length + stepIndex,
                events: [],
                synthetic: true,
                restored: true,
            });
        });
        knownPipelinePhases(state).forEach((phase, phaseIndex) => {
            const normalized = normalizePhase(phase);
            if (requestsByPhase.has(normalized) || syntheticByPhase.has(normalized)) return;
            syntheticByPhase.set(normalized, {
                id: `phase:${normalized}`,
                phase: normalized,
                status: 'pending',
                streamBlocks: [],
                nextBlockOrdinal: 0,
                contentText: '',
                durationSeconds: null,
                sequence: 0,
                requestIndex: state.requestOrder.length + phaseIndex,
                events: [],
                synthetic: true,
            });
        });
        state.events.forEach((event, eventIndex) => {
            const phase = normalizePhase(event.phase || 'starting');
            const candidates = requestsByPhase.get(phase) || [];
            const eventSequence = Number(event.sequence || 0);
            let target = null;
            if (candidates.length) {
                target = candidates[0];
                candidates.forEach((candidate) => {
                    const candidateSequence = Number(candidate.sequence || 0);
                    if (!eventSequence || !candidateSequence || candidateSequence <= eventSequence) {
                        target = candidate;
                    }
                });
            } else {
                if (!syntheticByPhase.has(phase)) {
                    syntheticByPhase.set(phase, {
                        id: `phase:${phase}`,
                        phase,
                        status: 'completed',
                        streamBlocks: [],
                        nextBlockOrdinal: 0,
                        contentText: '',
                        durationSeconds: null,
                        sequence: eventSequence,
                        requestIndex: state.requestOrder.length + eventIndex,
                        events: [],
                        synthetic: true,
                    });
                }
                target = syntheticByPhase.get(phase);
                if (!target.sequence && eventSequence) target.sequence = eventSequence;
            }
            target.events.push(event);
        });

        const entries = [...requestEntries, ...syntheticByPhase.values()];
        entries.forEach((entry) => {
            const eventSequences = entry.events
                .map((event) => Number(event.sequence || 0))
                .filter((sequence) => sequence > 0);
            const requestSequence = Number(entry.sequence || 0);
            const knownSequences = [
                ...(requestSequence > 0 ? [requestSequence] : []),
                ...eventSequences,
            ];
            entry.sortSequence = knownSequences.length
                ? Math.min(...knownSequences)
                : Number.MAX_SAFE_INTEGER;
            if (entry.synthetic && !entry.restored) {
                entry.status = phaseStepStatus(state, entry.phase, entry.events);
            }
        });
        return entries.sort((left, right) => (
            left.sortSequence - right.sortSequence
            || left.requestIndex - right.requestIndex
        ));
    }

    function createRequestStreamElement(request) {
        const card = document.createElement('details');
        card.className = 'deep-research-request';
        card.dataset.requestId = request.id;

        const header = document.createElement('summary');
        const heading = document.createElement('div');
        const ordinal = document.createElement('span');
        const title = document.createElement('h3');
        const status = document.createElement('span');
        const chevron = document.createElement('span');
        ordinal.className = 'deep-research-request-ordinal';
        ordinal.dataset.role = 'ordinal';
        title.dataset.role = 'title';
        status.className = 'deep-research-request-status';
        status.dataset.role = 'status';
        // Use the shared icon library so this disclosure indicator stays
        // visually consistent with the rest of Omlorix.
        chevron.className = 'deep-research-request-chevron';
        chevron.setAttribute('aria-hidden', 'true');
        chevron.innerHTML = icon('chevron');
        heading.append(ordinal, title);
        header.append(heading, status, chevron);

        const body = document.createElement('div');
        const timeline = document.createElement('div');
        body.className = 'deep-research-request-body';
        body.dataset.role = 'body';
        timeline.className = 'deep-research-request-timeline';
        timeline.dataset.role = 'timeline';
        body.appendChild(timeline);
        card.append(header, body);
        return card;
    }

    function chronologicalRequestBlocks(request) {
        const streamBlocks = (request.streamBlocks || []).map((block, index) => ({
            ...block,
            key: `stream:${block.id}`,
            renderOrder: index * 2,
        }));
        const activityBlocks = (request.events || []).map((event, index) => ({
            type: 'activity',
            key: `activity:${event.sequence || `${event.eventType}:${index}`}`,
            sequence: Number(event.sequence || 0),
            renderOrder: index * 2 + 1,
            event,
        }));
        return [...streamBlocks, ...activityBlocks].sort((left, right) => {
            const leftSequence = Number(left.sequence || 0);
            const rightSequence = Number(right.sequence || 0);
            if (leftSequence && rightSequence && leftSequence !== rightSequence) {
                return leftSequence - rightSequence;
            }
            if (leftSequence && !rightSequence) return -1;
            if (!leftSequence && rightSequence) return 1;
            return left.renderOrder - right.renderOrder;
        });
    }

    function bindThinkingToggle(header) {
        if (!header || header.dataset.thinkingBound === 'true') return;
        header.dataset.thinkingBound = 'true';
        header.addEventListener('click', () => {
            if (typeof window.toggleThinking === 'function') {
                window.toggleThinking(header);
                return;
            }
            const container = header.closest('.assistant-thinking');
            container?.classList.toggle('collapsed');
            header.setAttribute(
                'aria-expanded',
                String(!container?.classList.contains('collapsed')),
            );
        });
    }

    function thinkingTitle(block) {
        if (block.type === 'tool') {
            if (block.status === 'failed' && typeof window.getToolFailedText === 'function') {
                return window.getToolFailedText(block.name, block.arguments);
            }
            if (block.status === 'completed' && typeof window.getToolCompletedText === 'function') {
                return window.getToolCompletedText(block.name, block.arguments);
            }
            if (typeof window.getToolInProgressText === 'function') {
                return window.getToolInProgressText(block.name, block.arguments);
            }
            return toolLabel({ name: block.name, success: block.status !== 'failed' }, block.status !== 'running');
        }
        if (block.status !== 'running' && typeof window.getThinkingBlockFinalHeader === 'function') {
            return window.getThinkingBlockFinalHeader([], block.durationSeconds || 0);
        }
        return typeof window.getStreamText === 'function'
            ? window.getStreamText('chatbox_thinking_button_label', 'Thinking')
            : t('chatbox_thinking_button_label', 'Thinking');
    }

    function createThinkingStreamBlock(block) {
        const container = document.createElement('div');
        const header = document.createElement('button');
        const title = document.createElement('div');
        const titleText = document.createElement('span');
        const content = document.createElement('div');
        const body = document.createElement('div');
        const safeId = String(block.id || block.key).replace(/[^A-Za-z0-9_-]/g, '-');

        container.className = 'assistant-thinking collapsed deep-research-stream-thinking';
        container.dataset.streamBlockType = block.type;
        header.type = 'button';
        header.className = 'assistant-thinking-header';
        header.setAttribute('aria-expanded', 'false');
        header.setAttribute('aria-controls', `deep-research-thinking-${safeId}`);
        title.className = 'assistant-thinking-title';
        titleText.dataset.role = 'stream-block-title';
        title.appendChild(titleText);
        header.appendChild(title);
        content.id = `deep-research-thinking-${safeId}`;
        content.className = 'assistant-thinking-content';
        body.className = 'assistant-thinking-body';
        body.dataset.role = 'stream-block-body';
        content.appendChild(body);
        container.append(header, content);
        bindThinkingToggle(header);
        return container;
    }

    function reasoningSegments(text) {
        const normalized = String(text || '').replace(/\r\n/g, '\n').trim();
        if (!normalized) return [];
        const titlePattern = /\*\*([^\n*][^*]*?)\*\*(?:\s*\n+|\s+(?=[A-Z]))/g;
        const matches = Array.from(normalized.matchAll(titlePattern));
        if (!matches.length) return [{ title: '', content: normalized }];
        const segments = [];
        const leading = normalized.slice(0, matches[0].index).trim();
        if (leading) segments.push({ title: '', content: leading });
        matches.forEach((match, index) => {
            const start = Number(match.index || 0) + match[0].length;
            const end = index + 1 < matches.length ? Number(matches[index + 1].index || 0) : normalized.length;
            segments.push({ title: match[1].trim(), content: normalized.slice(start, end).trim() });
        });
        return segments;
    }

    function renderReasoningBlock(element, block) {
        const body = element.querySelector('[data-role="stream-block-body"]');
        const source = String(block.text || '');
        if (!body || body.dataset.reasoningSource === source) return;
        body.dataset.reasoningSource = source;
        body.replaceChildren();
        reasoningSegments(source).forEach((segment) => {
            const step = document.createElement('div');
            const stepContent = document.createElement('div');
            step.className = 'thinking-step';
            if (segment.title) {
                const stepHeader = document.createElement('div');
                const stepTitle = document.createElement('span');
                stepHeader.className = 'thinking-step-header';
                stepTitle.className = 'thinking-step-title';
                stepTitle.textContent = segment.title;
                stepHeader.appendChild(stepTitle);
                step.appendChild(stepHeader);
            }
            stepContent.className = 'thinking-step-content';
            stepContent.textContent = segment.content;
            step.appendChild(stepContent);
            body.appendChild(step);
        });
    }

    function toolActivityConfig(block) {
        if (typeof window.getToolActivityConfig === 'function') {
            return window.getToolActivityConfig(block.name, block.arguments);
        }
        return { icon: () => icon('tool') || icon('research') };
    }

    function toolArgumentsObject(value) {
        if (value === null || value === undefined || value === '') return null;
        if (typeof value === 'string') {
            try {
                const parsed = JSON.parse(value);
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
            } catch (_error) {
                return { input: value };
            }
            return { input: value };
        }
        if (typeof value === 'object' && !Array.isArray(value)) return value;
        return { input: value };
    }

    function renderToolBlock(element, block) {
        const body = element.querySelector('[data-role="stream-block-body"]');
        if (!body) return;
        let step = body.querySelector('.thinking-step-function-call');
        const config = toolActivityConfig(block);
        const displayName = typeof window.getToolDisplayName === 'function'
            ? window.getToolDisplayName(config, block.name)
            : humanReadableToolName(block.name, block.arguments);
        if (!step) {
            if (typeof window.createAssistantToolStep === 'function') {
                step = window.createAssistantToolStep(config, displayName, block.name, block.toolCallId);
            } else {
                step = document.createElement('div');
                step.className = 'thinking-step thinking-step-function-call';
                const toolHeader = document.createElement('div');
                const toolIcon = document.createElement('div');
                const toolName = document.createElement('span');
                toolHeader.className = 'function-call-header';
                toolIcon.className = 'function-call-icon';
                toolIcon.innerHTML = config.icon?.() || '';
                toolName.className = 'function-call-name';
                toolName.textContent = displayName;
                toolHeader.append(toolIcon, toolName);
                step.appendChild(toolHeader);
            }
            body.appendChild(step);
        }
        step.dataset.toolCallFinalized = String(block.status !== 'running');
        step.classList.toggle('is-tool-call-streaming', block.status === 'running');
        step.classList.toggle('is-tool-call-failed', block.status === 'failed');
        const argumentsObject = toolArgumentsObject(block.arguments);
        if (typeof window.renderAssistantToolParams === 'function') {
            window.renderAssistantToolParams(step, argumentsObject);
        } else if (typeof renderAssistantToolParams === 'function') {
            renderAssistantToolParams(step, argumentsObject);
        }
    }

    function updateThinkingStreamBlock(element, block) {
        const title = element.querySelector('[data-role="stream-block-title"]');
        if (title) {
            title.textContent = thinkingTitle(block);
            title.dataset.thinkingType = block.type === 'tool' ? 'tool' : 'thinking';
            title.classList.toggle('assistant-thinking-shimmer', block.status === 'running');
        }
        element.dataset.status = block.status;
        if (block.type === 'reasoning') renderReasoningBlock(element, block);
        if (block.type === 'tool') renderToolBlock(element, block);
    }

    function createTimelineBlock(block) {
        if (block.type === 'reasoning' || block.type === 'tool') {
            return createThinkingStreamBlock(block);
        }
        if (block.type === 'content') {
            const wrapper = document.createElement('div');
            const content = document.createElement('div');
            wrapper.className = 'assistant-message deep-research-stream-content';
            content.className = 'assistant-message-content';
            content.dataset.role = 'stream-block-content';
            wrapper.appendChild(content);
            return wrapper;
        }
        const activity = document.createElement('div');
        const marker = document.createElement('span');
        const message = document.createElement('div');
        activity.className = 'thinking-step deep-research-stream-activity';
        marker.className = 'deep-research-activity-marker';
        marker.setAttribute('aria-hidden', 'true');
        message.className = 'thinking-step-content';
        message.dataset.role = 'stream-block-activity';
        activity.append(marker, message);
        return activity;
    }

    function updateTimelineBlock(element, block) {
        if (block.type === 'reasoning' || block.type === 'tool') {
            updateThinkingStreamBlock(element, block);
            return;
        }
        if (block.type === 'content') {
            renderStreamMarkdown(
                element.querySelector('[data-role="stream-block-content"]'),
                block.text,
            );
            return;
        }
        const activity = element.querySelector('[data-role="stream-block-activity"]');
        if (!activity) return;
        activity.replaceChildren(document.createTextNode(block.event.message));
        if (block.event.createdAt) {
            const date = new Date(block.event.createdAt);
            if (!Number.isNaN(date.getTime())) {
                const time = document.createElement('time');
                time.dateTime = date.toISOString();
                time.textContent = new Intl.DateTimeFormat(undefined, {
                    hour: '2-digit',
                    minute: '2-digit',
                }).format(date);
                activity.append(' ', time);
            }
        }
    }

    function renderRequestTimeline(card, request) {
        const timeline = card.querySelector('[data-role="timeline"]');
        if (!timeline) return;
        const blocks = chronologicalRequestBlocks(request);
        const keys = new Set(blocks.map((block) => block.key));
        Array.from(timeline.children).forEach((element) => {
            if (!keys.has(element.dataset.blockKey)) element.remove();
        });
        let previous = null;
        blocks.forEach((block) => {
            let element = Array.from(timeline.children).find(
                (candidate) => candidate.dataset.blockKey === block.key,
            );
            if (!element) {
                element = createTimelineBlock(block);
                element.dataset.blockKey = block.key;
            }
            const expected = previous ? previous.nextElementSibling : timeline.firstElementChild;
            if (element !== expected) timeline.insertBefore(element, expected);
            previous = element;
            updateTimelineBlock(element, block);
        });
        timeline.hidden = blocks.length === 0;
    }

    function renderRequestStreams(state) {
        const container = document.getElementById('deepResearchRequestStreams');
        const empty = document.getElementById('deepResearchRequestStreamsEmpty');
        if (!container || !empty) return;
        const scrollRoot = document.querySelector('.deep-research-sidebar-scroll');
        // Only follow a growing stream while the user is already at its end.
        // Once they scroll upward, sidebarScrollLocked prevents later deltas
        // from pulling the viewport away from the content they are reading.
        const followLatest = scrollRoot && !state.sidebarScrollLocked
            ? scrollRoot.scrollHeight - scrollRoot.scrollTop - scrollRoot.clientHeight < 24
            : false;

        const steps = buildRequestSteps(state);
        const renderedIds = new Set(steps.map((step) => step.id));
        Array.from(container.children).forEach((card) => {
            if (!renderedIds.has(card.dataset.requestId)) card.remove();
        });
        let previousCard = null;
        steps.forEach((request, index) => {
            let card = Array.from(container.children).find(
                (child) => child.dataset.requestId === request.id,
            );
            if (!card) {
                card = createRequestStreamElement(request);
            }
            // Only move a card when its chronological position actually
            // changed. Re-appending every card for every token delta reset the
            // scroll anchoring of the entire sidebar in some browsers.
            const expectedCard = previousCard
                ? previousCard.nextElementSibling
                : container.firstElementChild;
            if (card !== expectedCard) container.insertBefore(card, expectedCard);
            previousCard = card;
            card.dataset.status = request.status;
            card.classList.toggle('is-running', request.status === 'running');
            card.setAttribute('aria-busy', String(request.status === 'running'));
            renderRequestOrdinal(
                card.querySelector('[data-role="ordinal"]'),
                request.status,
                index + 1,
            );
            card.querySelector('[data-role="title"]').textContent = phaseText(request.phase);
            const status = card.querySelector('[data-role="status"]');
            status.textContent = requestStatusText(request);
            status.dataset.status = request.status;
            if (request.durationSeconds !== null) {
                status.title = `${request.durationSeconds.toFixed(1)}s`;
            } else {
                status.removeAttribute('title');
            }
            renderRequestTimeline(card, request);
            const requestBody = card.querySelector('[data-role="body"]');
            requestBody.hidden = chronologicalRequestBlocks(request).length === 0;
        });

        const hasRequests = steps.length > 0;
        empty.hidden = hasRequests;
        container.hidden = !hasRequests;
        if (followLatest && scrollRoot) {
            window.requestAnimationFrame?.(() => {
                if (state.sidebarScrollLocked) return;
                scrollRoot.scrollTop = scrollRoot.scrollHeight;
                state.sidebarScrollTop = scrollRoot.scrollTop;
            });
        }
    }

    function handleSidebarScroll(event) {
        const state = getState(activeRunId);
        const scrollRoot = event.currentTarget;
        if (!state || !scrollRoot) return;
        const scrollTop = scrollRoot.scrollTop;
        const distanceFromEnd = scrollRoot.scrollHeight - scrollTop - scrollRoot.clientHeight;
        if (scrollTop < state.sidebarScrollTop) state.sidebarScrollLocked = true;
        if (distanceFromEnd <= 8) state.sidebarScrollLocked = false;
        state.sidebarScrollTop = scrollTop;
    }

    function renderReport(state) {
        const report = document.getElementById('deepResearchSidebarReport');
        const empty = document.getElementById('deepResearchReportEmpty');
        if (!report || !empty) return;
        const markdown = String(state.report || '').trim();
        empty.hidden = Boolean(markdown);
        report.hidden = !markdown;
        if (!markdown) {
            report.replaceChildren();
            return;
        }
        if (report.dataset.markdownSource === markdown) return;
        report.dataset.markdownSource = markdown;
        if (typeof window.renderMarkdownContent === 'function') {
            window.renderMarkdownContent(report, markdown);
        } else {
            report.textContent = markdown;
        }
    }

    function renderSources(state) {
        const list = document.getElementById('deepResearchSidebarSources');
        const empty = document.getElementById('deepResearchSourcesEmpty');
        if (!list || !empty) return;
        list.replaceChildren();
        state.citations.forEach((citation, index) => {
            const item = document.createElement('li');
            const number = document.createElement('span');
            const body = document.createElement('div');
            const link = document.createElement('a');
            number.className = 'deep-research-source-number';
            number.textContent = String(index + 1);
            link.href = citation.url;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = citation.title || citation.url;
            body.appendChild(link);
            if (citation.snippet) {
                const snippet = document.createElement('p');
                snippet.textContent = citation.snippet;
                body.appendChild(snippet);
            }
            item.append(number, body);
            list.appendChild(item);
        });
        empty.hidden = state.citations.length > 0;
        list.hidden = state.citations.length === 0;
    }

    function renderFiles(state) {
        const list = document.getElementById('deepResearchSidebarFiles');
        const empty = document.getElementById('deepResearchFilesEmpty');
        if (!list || !empty) return;
        list.replaceChildren();
        state.files.forEach((path) => {
            const item = document.createElement('li');
            const fileIcon = document.createElement('span');
            const link = document.createElement('a');
            const download = document.createElement('a');
            fileIcon.className = 'deep-research-file-icon';
            fileIcon.innerHTML = icon('files');
            fileIcon.setAttribute('aria-hidden', 'true');
            link.className = 'deep-research-file-name';
            link.href = fileUrl(state.runId, path);
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = path.split('/').pop() || path;
            download.className = 'deep-research-file-download';
            download.href = fileUrl(state.runId, path, true);
            download.innerHTML = icon('download');
            download.setAttribute('aria-label', formatT(
                'deep_research_download_file_aria',
                'Download {name}',
                { name: link.textContent },
            ));
            item.append(fileIcon, link, download);
            list.appendChild(item);
        });
        empty.hidden = state.files.length > 0;
        list.hidden = state.files.length === 0;
    }

    function setActiveTab(name, focus = false) {
        const tabs = Array.from(document.querySelectorAll('[data-deep-research-tab]'));
        tabs.forEach((tab) => {
            const selected = tab.dataset.deepResearchTab === name;
            tab.setAttribute('aria-selected', String(selected));
            tab.tabIndex = selected ? 0 : -1;
            const panel = document.getElementById(tab.getAttribute('aria-controls'));
            if (panel) panel.hidden = !selected;
            if (selected && focus) tab.focus();
        });
    }

    function canvasSizingController() {
        return window.canvasMarkdownWidget || null;
    }

    function updatePreviewResizerA11y() {
        const resizer = document.getElementById('deepResearchPreviewResizer');
        const sizing = canvasSizingController();
        if (!resizer || typeof sizing?.getPreviewWidthBounds !== 'function') return;
        const { viewportWidth, minWidth, maxWidth } = sizing.getPreviewWidthBounds();
        const ratio = Number(sizing.getPreviewWidthRatio?.() || 0.5);
        resizer.setAttribute('aria-valuemin', String(Math.round((minWidth / viewportWidth) * 100)));
        resizer.setAttribute('aria-valuemax', String(Math.round((maxWidth / viewportWidth) * 100)));
        resizer.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
    }

    function isDesktopPreviewLayout() {
        return !window.matchMedia('(max-width: 900px)').matches;
    }

    function beginPreviewResize(event) {
        const resizer = document.getElementById('deepResearchPreviewResizer');
        if (!resizer || !isDesktopPreviewLayout()) return;
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        event.preventDefault();
        previewResizeActive = true;
        document.body.classList.add('canvas-markdown-preview-resizing');
        resizer.setPointerCapture?.(event.pointerId);
        canvasSizingController()?.setPreviewWidthFromPointerX?.(event.clientX);
        updatePreviewResizerA11y();
    }

    function updatePreviewResize(event) {
        if (!previewResizeActive) return;
        event.preventDefault();
        canvasSizingController()?.setPreviewWidthFromPointerX?.(event.clientX);
        updatePreviewResizerA11y();
    }

    function endPreviewResize(event) {
        if (!previewResizeActive) return;
        previewResizeActive = false;
        document.body.classList.remove('canvas-markdown-preview-resizing');
        const resizer = document.getElementById('deepResearchPreviewResizer');
        if (event?.pointerId !== undefined) {
            resizer?.releasePointerCapture?.(event.pointerId);
        }
        const sizing = canvasSizingController();
        const bounds = sizing?.getPreviewWidthBounds?.();
        const ratio = sizing?.getPreviewWidthRatio?.();
        if (bounds && Number.isFinite(ratio)) {
            sizing.setPreviewWidthFromPixels?.(bounds.viewportWidth * ratio, { persist: true });
        }
        updatePreviewResizerA11y();
    }

    function handlePreviewResizerKeydown(event) {
        if (!isDesktopPreviewLayout()) return;
        const sizing = canvasSizingController();
        const bounds = sizing?.getPreviewWidthBounds?.();
        if (!bounds) return;
        const ratio = Number(sizing.getPreviewWidthRatio?.() || 0.5);
        const currentWidth = bounds.viewportWidth * ratio;
        const step = event.shiftKey ? 96 : 32;
        let nextWidth = null;
        if (event.key === 'ArrowLeft') nextWidth = currentWidth + step;
        if (event.key === 'ArrowRight') nextWidth = currentWidth - step;
        if (event.key === 'Home') nextWidth = bounds.minWidth;
        if (event.key === 'End') nextWidth = bounds.maxWidth;
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            sizing.resetPreviewWidth?.();
            updatePreviewResizerA11y();
            return;
        }
        if (nextWidth === null) return;
        event.preventDefault();
        sizing.setPreviewWidthFromPixels?.(nextWidth, { persist: true });
        updatePreviewResizerA11y();
    }

    function resetSidebarScrollToTop(state) {
        const scrollRoot = document.querySelector('.deep-research-sidebar-scroll');
        if (!state || !scrollRoot) return;

        // Explicitly opening a research card starts a new reading session. Lock
        // the live-stream auto-follow first so a queued render frame cannot pull
        // the reader back to the last activity/source at the bottom.
        state.sidebarScrollLocked = true;
        state.sidebarScrollTop = 0;
        const reset = () => {
            if (activeRunId !== state.runId) return;
            scrollRoot.scrollTop = 0;
            state.sidebarScrollTop = 0;
        };
        reset();

        // The selected tab and Markdown report can change the scroll height in
        // the next layout frames. Reassert the top after those layouts, which
        // also overrides browser restoration of an overflow element on reload.
        window.requestAnimationFrame?.(() => {
            reset();
            window.requestAnimationFrame?.(reset);
        });
    }

    function openSidebar(runId, { resetScroll = true } = {}) {
        const state = getState(runId);
        const sidebar = document.getElementById('deepResearchSidebar');
        if (!state || !sidebar) return;
        const previousRunId = activeRunId;
        activeRunId = state.runId;
        sidebar.dataset.runId = state.runId;
        sidebar.classList.add('is-open');
        sidebar.setAttribute('aria-hidden', 'false');
        sidebar.removeAttribute('inert');
        document.body.classList.add('deep-research-preview-open');
        canvasSizingController()?.applyPreviewWidthRatio?.();
        updatePreviewResizerA11y();
        if (typeof window.setMainSidebarAutoCollapsed === 'function') {
            // Register Research before closing another preview. The shared Set
            // keeps the persisted main-sidebar state untouched during handoff.
            window.setMainSidebarAutoCollapsed('deep-research-preview', true);
        } else if (typeof window.closeSidebar === 'function') {
            window.closeSidebar({ persist: false });
        }
        window.closeOtherArtifactPreviews?.('deep-research-preview');
        setActiveTab(normalizeStatus(state.status) === 'completed' ? 'report' : 'activity');
        renderSidebar(state);
        if (resetScroll) {
            // If the artifact request is still in flight, reset once now and
            // once more after Markdown is inserted into the report panel.
            state.resetScrollAfterTerminalHydration = state.terminalHydrationPending;
            resetSidebarScrollToTop(state);
        }
        if (previousRunId && previousRunId !== state.runId) {
            const previousState = getState(previousRunId);
            if (previousState) renderAllCards(previousState);
        }
        renderAllCards(state);
    }

    function closeSidebar({ restoreFocus = true } = {}) {
        const sidebar = document.getElementById('deepResearchSidebar');
        const wasOpen = Boolean(
            sidebar?.classList.contains('is-open')
            || document.body.classList.contains('deep-research-preview-open')
            || activeRunId,
        );
        const closingRunId = String(sidebar?.dataset.runId || activeRunId || '');
        sidebar?.classList.remove('is-open');
        sidebar?.setAttribute('aria-hidden', 'true');
        sidebar?.setAttribute('inert', '');
        document.body.classList.remove('deep-research-preview-open');
        endPreviewResize();

        // Always release the shared sidebar reservation when Research might
        // have been active. This also repairs an inconsistent DOM after a view
        // replacement removed the panel before its normal close callback ran.
        if (wasOpen && typeof window.setMainSidebarAutoCollapsed === 'function') {
            window.setMainSidebarAutoCollapsed('deep-research-preview', false);
        }
        activeRunId = '';
        const closingState = getState(closingRunId);
        if (closingState) renderAllCards(closingState);
        if (restoreFocus) {
            document.querySelector(
                `.deep-research-widget[data-widget-id="${closingRunId}"] [data-action="open"]`,
            )?.focus();
        }
        if (sidebar) delete sidebar.dataset.runId;
    }

    function hidePreviewPanel() {
        // Match the Canvas/Notes preview contract used by global navigation.
        // Navigation must not return focus to a widget in the view being hidden.
        closeSidebar({ restoreFocus: false });
    }

    async function hydrateTerminalState(state) {
        if (state.terminalHydrated) return;
        state.terminalHydrated = true;
        state.terminalHydrationPending = true;
        const requests = [];
        if (state.finalReportPath) {
            requests.push(
                fetch(fileUrl(state.runId, state.finalReportPath), { credentials: 'same-origin' })
                    .then((response) => response.ok ? response.text() : '')
                    .then((markdown) => {
                        if (markdown) state.report = publicReportMarkdown(markdown, state.runId);
                    })
                    .catch(() => {}),
            );
        }
        requests.push(
            fetch(fileUrl(state.runId, 'citations.json'), { credentials: 'same-origin' })
                .then((response) => response.ok ? response.json() : [])
                .then((citations) => {
                    state.citations = normalizeCitations(citations);
                })
                .catch(() => {}),
        );
        normalizeFiles(state, [
            state.finalReportPath,
            state.archivePath || (state.status === 'completed' ? 'workspace.zip' : ''),
            state.status === 'completed' ? 'citations.json' : '',
        ].filter(Boolean));
        await Promise.all(requests);
        state.terminalHydrationPending = false;
        renderAllCards(state);
        if (activeRunId === state.runId) renderSidebar(state);
        if (state.resetScrollAfterTerminalHydration) {
            state.resetScrollAfterTerminalHydration = false;
            resetSidebarScrollToTop(state);
        }
    }

    function handleDeepResearchEvent(event, messageId) {
        if (!event || event.t !== 'deep_research_evt') return;
        const runId = String(event.widget_id || event.run_id || '').trim();
        const state = getState(runId);
        if (!state) return;
        const messageContainer = messageId ? document.getElementById(`a-${messageId}`) : null;
        const widget = messageContainer?.querySelector(
            `.deep-research-widget[data-widget-id="${window.CSS?.escape ? window.CSS.escape(runId) : runId}"]`,
        ) || document.querySelector(`.deep-research-widget[data-widget-id="${runId}"]`);
        if (widget) registerWidget(widget);
        updateFromEvent(state, event);
    }

    function hydrateActivitySnapshot(state, snapshot) {
        if (!state || !snapshot || typeof snapshot !== 'object') return false;
        if (Number(snapshot.schema_version || 0) !== 1 || !Array.isArray(snapshot.events)) {
            return false;
        }

        // Reuse the live reducer so restored reasoning, content, tools, phase
        // activity, ordering, and completion state cannot drift from streaming
        // behavior. Rendering once after replay avoids hundreds of intermediate
        // DOM updates for long research transcripts.
        snapshot.events.forEach((event) => {
            if (event && typeof event === 'object') {
                updateFromEvent(state, event, { render: false, autoOpen: false });
            }
        });
        renderAllCards(state);
        if (activeRunId === state.runId) renderSidebar(state);
        return true;
    }

    function hydrateWidget(widget, activitySnapshot = null) {
        const state = registerWidget(widget);
        if (state && activitySnapshot) hydrateActivitySnapshot(state, activitySnapshot);
        return state;
    }

    function hydrateAllWidgets(root = document) {
        const widgets = [];
        if (root instanceof Element && root.matches('.deep-research-widget')) widgets.push(root);
        root.querySelectorAll?.('.deep-research-widget').forEach((widget) => widgets.push(widget));
        widgets.forEach(registerWidget);
        hydrateIcons(root instanceof Element ? root : document);
    }

    async function requestCancellation(state, button) {
        if (!state?.generationId || button.disabled) return;
        if (button.dataset.confirming !== 'true') {
            button.dataset.confirming = 'true';
            button.textContent = t('deep_research_confirm_cancel', 'Click again to cancel');
            window.setTimeout(() => {
                if (button.dataset.confirming === 'true') {
                    button.dataset.confirming = 'false';
                    button.textContent = t('deep_research_cancel_action', 'Cancel research');
                }
            }, 4000);
            return;
        }

        button.disabled = true;
        try {
            const fetchImpl = typeof window.authedFetch === 'function'
                ? window.authedFetch.bind(window)
                : window.fetch.bind(window);
            const response = await fetchImpl(
                `/api/v1/chats/cancel?generation_id=${encodeURIComponent(state.generationId)}`,
                { method: 'POST', credentials: 'same-origin', body: '' },
            );
            if (!response.ok) throw new Error('cancel_request_failed');
            const result = await response.json();
            if (result?.status !== 'success') throw new Error('cancel_request_failed');
            state.cancelRequested = true;
            state.message = t('deep_research_cancel_requested', 'Stopping research…');
            addActivity(state, { message: state.message, eventType: 'cancel_requested' });
            renderAllCards(state);
            renderSidebar(state);
        } catch (_error) {
            button.disabled = false;
            button.dataset.confirming = 'false';
            button.textContent = t('deep_research_cancel_action', 'Cancel research');
            state.message = t(
                'deep_research_action_failed',
                'The research action could not be completed.',
            );
            renderSidebar(state);
        }
    }

    function handleSidebarKeyboard(event) {
        const sidebar = document.getElementById('deepResearchSidebar');
        if (!sidebar?.classList.contains('is-open')) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            closeSidebar();
        }
    }

    function bindUi() {
        document.getElementById('deepResearchSidebarClose')?.addEventListener('click', closeSidebar);
        document.getElementById('deepResearchSidebarCancel')?.addEventListener('click', (event) => {
            requestCancellation(getState(activeRunId), event.currentTarget);
        });
        document.getElementById('deepResearchExportFormat')?.addEventListener('change', (event) => {
            preferredExportFormat = String(event.currentTarget.value || 'pdf') === 'md'
                ? 'md'
                : 'pdf';
        });
        document.getElementById('deepResearchExportButton')?.addEventListener('click', () => {
            exportReport(getState(activeRunId));
        });
        document.addEventListener('keydown', handleSidebarKeyboard);
        const resizer = document.getElementById('deepResearchPreviewResizer');
        resizer?.addEventListener('pointerdown', beginPreviewResize);
        resizer?.addEventListener('pointermove', updatePreviewResize);
        resizer?.addEventListener('pointerup', endPreviewResize);
        resizer?.addEventListener('pointercancel', endPreviewResize);
        resizer?.addEventListener('keydown', handlePreviewResizerKeydown);
        window.addEventListener('blur', endPreviewResize);
        window.addEventListener('resize', updatePreviewResizerA11y, { passive: true });
        document.querySelector('.deep-research-sidebar-scroll')?.addEventListener(
            'scroll',
            handleSidebarScroll,
            { passive: true },
        );

        document.addEventListener('click', (event) => {
            const toggleButton = event.target.closest(
                '.deep-research-widget [data-action="toggle"]',
            );
            if (toggleButton) {
                const widget = toggleButton.closest('.deep-research-widget');
                const state = registerWidget(widget);
                const sidebar = document.getElementById('deepResearchSidebar');
                if (
                    state
                    && sidebar?.classList.contains('is-open')
                    && activeRunId === state.runId
                ) {
                    closeSidebar({ restoreFocus: false });
                } else if (state) {
                    openSidebar(state.runId);
                }
                return;
            }
            const openButton = event.target.closest('.deep-research-widget [data-action="open"]');
            if (openButton) {
                const widget = openButton.closest('.deep-research-widget');
                const state = registerWidget(widget);
                if (state) openSidebar(state.runId);
                return;
            }
            const tab = event.target.closest('[data-deep-research-tab]');
            if (tab) setActiveTab(tab.dataset.deepResearchTab);
        });

        document.addEventListener('keydown', (event) => {
            const tab = event.target.closest?.('[data-deep-research-tab]');
            if (!tab || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            const tabs = Array.from(document.querySelectorAll('[data-deep-research-tab]'));
            const index = tabs.indexOf(tab);
            let nextIndex = index;
            if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
            if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
            if (event.key === 'Home') nextIndex = 0;
            if (event.key === 'End') nextIndex = tabs.length - 1;
            event.preventDefault();
            setActiveTab(tabs[nextIndex].dataset.deepResearchTab, true);
        });

        observer = new MutationObserver((records) => {
            records.forEach((record) => {
                record.addedNodes.forEach((node) => {
                    if (node instanceof Element) hydrateAllWidgets(node);
                });
            });
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }

    function init() {
        hydrateIcons();
        hydrateAllWidgets();
        bindUi();
    }

    window.deepResearchWidget = {
        handleDeepResearchEvent,
        hydrateWidget,
        openSidebar,
        closeSidebar,
        hidePreviewPanel,
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
