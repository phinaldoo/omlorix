(function() {
    const state = {
        data: [],
        dataLevel: 'free',
        providerTier: 'free',
        intelligenceIndexVersion: null,
        sourceMetadataReady: false,
        sortColumn: 'artificial_analysis_intelligence_index',
        sortDirection: 'desc',
        currentPage: 1,
        pageSize: 25,
    };

    let _tooltipIdCounter = 0;
    function _nextTooltipId() {
        return 'lb-tt-' + (++_tooltipIdCounter);
    }

    const contentElement = document.getElementById('content');

    // Artificial Analysis intentionally exposes different evaluation shapes
    // for the Free and Full datasets. Keep the supported fields and their
    // translations explicit so API-provided names never become dynamic i18n
    // keys or unexpected columns.
    const metricDefinitions = {
        artificial_analysis_intelligence_index: {
            i18n: 'leaderboard_column_intelligence_index',
            fallback: 'Intelligence Index',
        },
        artificial_analysis_coding_index: {
            i18n: 'leaderboard_column_coding_index',
            fallback: 'Coding Index',
        },
        artificial_analysis_agentic_index: {
            i18n: 'leaderboard_column_agentic_index',
            fallback: 'Agentic Index',
        },
        hle: {
            i18n: 'leaderboard_column_hle',
            fallback: 'HLE',
        },
        gpqa_diamond: {
            i18n: 'leaderboard_column_gpqa_diamond',
            fallback: 'GPQA Diamond',
        },
        scicode: {
            i18n: 'leaderboard_column_scicode',
            fallback: 'SciCode',
        },
        terminalbench_v2_1: {
            i18n: 'leaderboard_column_terminalbench',
            fallback: 'Terminal-Bench 2.1',
        },
        critpt: {
            i18n: 'leaderboard_column_critpt',
            fallback: 'CritPt',
        },
    };

    const metricProfiles = {
        free: [
            'artificial_analysis_intelligence_index',
            'artificial_analysis_coding_index',
            'artificial_analysis_agentic_index',
        ],
        full: [
            'artificial_analysis_intelligence_index',
            'artificial_analysis_coding_index',
            'artificial_analysis_agentic_index',
            'hle',
            'gpqa_diamond',
            'scicode',
            'terminalbench_v2_1',
            'critpt',
        ],
    };

    function t(key, fallback) {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function reapplyTranslations() {
        if (typeof window !== 'undefined' && typeof window.initI18n === 'function') {
            window.initI18n(true);
        }
    }

    function getColumnDataI18nAttr(key) {
        switch (key) {
            case 'model_name':
                return 'data-i18n="leaderboard_column_model"';
            case 'input_capabilities':
                return 'data-i18n="leaderboard_column_input"';
            case 'output_capabilities':
                return 'data-i18n="leaderboard_column_output"';
            case 'tools':
                return 'data-i18n="leaderboard_column_tools"';
            case 'training_data':
                return 'data-i18n="leaderboard_column_training_data"';
            default:
                return metricDefinitions[key]
                    ? `data-i18n="${metricDefinitions[key].i18n}"`
                    : '';
        }
    }
    const capabilityColumns = ['input_capabilities', 'output_capabilities'];
    const listColumns = ['tools'];
    const auxiliaryColumns = ['training_data'];
    function renderCapabilities(capabilities) {
        if (!Array.isArray(capabilities) || capabilities.length === 0) {
            return '<span class="metric-value null-value">\u2014</span>';
        }

        const uniqueCaps = Array.from(new Set(capabilities));
        const capability_icons_to_icon = {
            text: Icons.text,
            audio: Icons.audio,
            image: Icons.image,
            text_document: Icons.text_document,
            file: Icons.file,
            pdf: Icons.pdf,
            default: Icons.info
        }
        const icons = capability_icons_to_icon;
        return `
            <div class="capabilities-icons">
                ${uniqueCaps.map(cap => {
                    const icon = icons[cap] || icons.default;
                    const fallbackName = formatTitleCase(cap);
                    let label = t('leaderboard_capability_name_default', fallbackName);
                    switch (String(cap || '').trim().toLowerCase()) {
                        case 'text':
                            label = t('leaderboard_capability_name_text', fallbackName);
                            break;
                        case 'audio':
                            label = t('leaderboard_capability_name_audio', fallbackName);
                            break;
                        case 'image':
                            label = t('leaderboard_capability_name_image', fallbackName);
                            break;
                        case 'text_document':
                            label = t('leaderboard_capability_name_text_document', fallbackName);
                            break;
                        case 'file':
                            label = t('leaderboard_capability_name_file', fallbackName);
                            break;
                        case 'pdf':
                            label = t('leaderboard_capability_name_pdf', fallbackName);
                            break;
                        default:
                            break;
                    }
                    const safeLabel = escapeHtml(label);
                    const tooltipId = _nextTooltipId();
                    return `
                        <span class="tooltip-container capability-tooltip">
                            <span class="tooltip-content">
                                <button type="button" class="capability-icon" aria-label="${safeLabel}" aria-describedby="${tooltipId}">
                                    ${icon}
                                </button>
                            </span>
                            <div class="tooltip" id="${tooltipId}" data-tooltip-origin="leaderboard-capability" role="tooltip">${safeLabel}</div>
                        </span>
                    `;
                }).join('')}
            </div>
        `;
    }

    function getToolCategoryLabel(category) {
        switch (category) {
            case 'websearch':
                return t('leaderboard_tool_category_websearch', 'Web Search');
            case 'todo_management':
                return t('leaderboard_tool_category_todo', 'Todo Management');
            case 'notes_management':
                return t('leaderboard_tool_category_notes', 'Notes Management');
            case 'automations_management':
                return t('leaderboard_tool_category_automations', 'Automations Management');
            case 'skills_management':
                return t('leaderboard_tool_category_skills', 'Skills Management');
            case 'memory_management':
                return t('leaderboard_tool_category_memory', 'Memory Management');
            case 'information':
                return t('leaderboard_tool_category_information', 'Information');
            case 'education':
                return t('leaderboard_tool_category_education', 'Education');
            case 'media_generation':
                return t('leaderboard_tool_category_media_generation', 'Media Generation');
            case 'presentations':
                return t('leaderboard_tool_category_presentations', 'Presentations');
            case 'research':
                return t('leaderboard_tool_category_research', 'Research');
            case 'canvas':
                return t('leaderboard_tool_category_canvas', 'Canvas');
            case 'code_execution':
                return t('leaderboard_tool_category_code_execution', 'Code Execution');
            default:
                return category;
        }
    }

    function getToolCategoryStatusLabel(status) {
        switch (status) {
            case 'full':
                return t('leaderboard_tool_status_full', 'All tools enabled');
            case 'partial':
                return t('leaderboard_tool_status_partial', 'Some tools enabled');
            case 'none':
                return t('leaderboard_tool_status_none', 'No tools enabled');
            default:
                return status;
        }
    }

    const TOOL_CATEGORY_ORDER = ['websearch', 'todo_management', 'notes_management', 'automations_management', 'skills_management', 'memory_management', 'information', 'education', 'media_generation', 'presentations', 'research', 'canvas', 'code_execution'];

    const TOOL_CATEGORY_TOOL_MAP = {
        'websearch': ['web_search'],
        'todo_management': ['todos'],
        'notes_management': ['notes'],
        'automations_management': ['automations'],
        'skills_management': ['skills'],
        'memory_management': ['memories'],
        'information': ['weather'],
        'education': ['quiz', 'flashcards'],
        'media_generation': ['image_generation', 'video_generation', 'audio_generation', 'music_generation'],
        'presentations': ['slide_presentation'],
        'research': ['deep_research'],
        'canvas': ['canvas'],
        'code_execution': ['code_execution'],
    };

    function escapeHtml(value) {
        if (value === null || value === undefined) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatTitleCase(str) {
        return String(str || '')
            .replace(/_/g, ' ')
            .replace(/\b\w/g, c => c.toUpperCase());
    }

    function formatToolName(tool) {
        return formatTitleCase(tool);
    }

    function clearTooltips() {
        ['leaderboard-capability', 'leaderboard-training', 'tool-category'].forEach(origin => {
            document.querySelectorAll(`.tooltip[data-tooltip-origin="${origin}"]`).forEach(el => el.remove());
        });
    }

    function renderToolCategories(toolCategories, tools) {
        const allTools = Array.isArray(tools) ? Array.from(new Set(tools)) : [];
        const categories = (toolCategories && toolCategories.categories) || {};
        const uncategorized = (toolCategories && Array.isArray(toolCategories.uncategorized))
            ? toolCategories.uncategorized
            : [];

        const allNone = TOOL_CATEGORY_ORDER.every(cat => (categories[cat] || 'none') === 'none')
            && uncategorized.length === 0
            && allTools.length === 0;

        if (allNone) {
            return '<span class="metric-value null-value">\u2014</span>';
        }

        const totalCount = allTools.length || (
            TOOL_CATEGORY_ORDER.reduce((sum, cat) => {
                const status = categories[cat] || 'none';
                const catTools = TOOL_CATEGORY_TOOL_MAP[cat] || [];
                if (status === 'full') return sum + catTools.length;
                if (status === 'partial') return sum + Math.max(1, Math.floor(catTools.length / 2));
                return sum;
            }, 0) + uncategorized.length
        );

        const knownCategoryStatuses = TOOL_CATEGORY_ORDER.map(cat => categories[cat] || 'none');
        let overallStatus;
        if (knownCategoryStatuses.every(s => s === 'full')) {
            overallStatus = 'full';
        } else if (knownCategoryStatuses.some(s => s === 'full' || s === 'partial') || uncategorized.length > 0) {
            overallStatus = 'partial';
        } else {
            overallStatus = 'none';
        }

        const countLabel = totalCount === 1
            ? t('leaderboard_tools_count_one', 'tool')
            : t('leaderboard_tools_count_other', 'tools');

        const ariaLabel = t('leaderboard_tools_aria_label', 'Supported tools, hover or focus for details')
            + ` (${totalCount} ${countLabel})`;

        const tooltipHeader = totalCount === 1
            ? t('leaderboard_tools_tooltip_header_one', '1 supported tool')
            : `${totalCount} ${t('leaderboard_tools_tooltip_header_other', 'supported tools')}`;

        let categoriesHtml = '';
        const category_to_icon = {
            websearch: Icons.globe,
            todo_management: Icons.todo_management,
            notes_management: Icons.notes_management,
            automations_management: Icons.automations_management,
            skills_management: Icons.skills_management,
            memory_management: Icons.memory_management,
            media_generation: Icons.media_generation,
            information: Icons.info,
            education: Icons.education,
            presentations: Icons.presentations,
            research: Icons.globe,
            canvas: Icons.grid,
            code_execution: Icons.grid,
        }
        const status_category_to_icon = {
            full: Icons.check,
            partial: Icons.partial,
            none: Icons.close,
        }
        for (const cat of TOOL_CATEGORY_ORDER) {
            const status = categories[cat] || 'none';
            const label = getToolCategoryLabel(cat);
            const icon = category_to_icon?.[cat] || '';
            const statusIcon = status_category_to_icon[status] || '';
            const statusLabel = getToolCategoryStatusLabel(status);

            categoriesHtml += `
                <div class="tools-tooltip-category">
                    <span class="tools-tooltip-category-name">
                        <span class="tools-tooltip-category-icon">${icon}</span>
                        <span>${escapeHtml(label)}</span>
                    </span>
                    <span class="tools-tooltip-status" data-status="${status}">
                        ${statusIcon}
                        <span>${escapeHtml(statusLabel)}</span>
                    </span>
                </div>
            `;
        }

        let uncategorizedHtml = '';
        if (uncategorized.length > 0) {
            const uncategorizedTitle = t('leaderboard_tools_tooltip_uncategorized', 'Other supported tools');
            uncategorizedHtml = `
                <div class="tools-tooltip-section">
                    <div class="tools-tooltip-section-title">${escapeHtml(uncategorizedTitle)}</div>
                    <div class="tools-tooltip-uncategorized">
                        ${uncategorized.map(tool => {
                            const display = formatToolName(tool);
                            return `<span class="tools-tooltip-tool" title="${escapeHtml(tool)}">${escapeHtml(display)}</span>`;
                        }).join('')}
                    </div>
                </div>
            `;
        }

        const categoriesTitle = t('leaderboard_tools_tooltip_categories', 'Tool categories');

        const tooltipBody = `
            <div class="tools-tooltip-body">
                <div class="tools-tooltip-header">${escapeHtml(tooltipHeader)}</div>
                <div class="tools-tooltip-section">
                    <div class="tools-tooltip-section-title">${escapeHtml(categoriesTitle)}</div>
                    <div class="tools-tooltip-categories">${categoriesHtml}</div>
                </div>
                ${uncategorizedHtml}
            </div>
        `;

        const tooltipId = _nextTooltipId();

        return `
            <span class="tooltip-container tools-pill-tooltip">
                <span class="tooltip-content">
                    <button type="button"
                            class="tools-pill"
                            data-status="${overallStatus}"
                            aria-label="${escapeHtml(ariaLabel)}"
                            aria-describedby="${tooltipId}">
                        <span class="tools-pill-icon">${Icons.tool}</span>
                        <span class="tools-pill-count">${totalCount}</span>
                        <span class="tools-pill-label">${escapeHtml(countLabel)}</span>
                    </button>
                </span>
                <div class="tooltip tooltip-wide" id="${tooltipId}" data-tooltip-origin="tool-category" role="tooltip">${tooltipBody}</div>
            </span>
        `;
    }

    function renderTrainingData(rawValue) {
        const stateKey = (() => {
            if (typeof rawValue === 'string') {
                const normalized = rawValue.trim().toLowerCase();
                if (normalized === 'true' || normalized === 'yes') return 'true';
                if (normalized === 'false' || normalized === 'no') return 'false';
                return 'unknown';
            }
            if (rawValue === true) return 'true';
            if (rawValue === false) return 'false';
            return 'unknown';
        })();

        const trainingIcons = {
            true: {
                className: 'training-data-used',
                labelKey: 'leaderboard_training_data_used',
                fallback: 'Uses training data',
                svg: (typeof Icons !== 'undefined' && Icons.close) ? Icons.close : ''
            },
            false: {
                className: 'training-data-not-used',
                labelKey: 'leaderboard_training_data_not_used',
                fallback: 'Does not use training data',
                svg: (typeof Icons !== 'undefined' && Icons.check) ? Icons.check : ''
            },
            unknown: {
                className: 'training-data-unknown',
                labelKey: 'leaderboard_training_data_unknown',
                fallback: 'Unknown',
                svg: (typeof Icons !== 'undefined' && Icons.question) ? Icons.question : ''
            }
        };

        const iconState = trainingIcons[stateKey] || trainingIcons.unknown;
        let tooltipText = t('leaderboard_training_data_unknown', 'Unknown');
        if (stateKey === 'true') {
            tooltipText = t('leaderboard_training_data_used', 'Uses training data');
        } else if (stateKey === 'false') {
            tooltipText = t('leaderboard_training_data_not_used', 'Does not use training data');
        }
        const tooltipId = _nextTooltipId();

        return `
            <span class="tooltip-container training-data-tooltip">
                <span class="tooltip-content">
                    <button type="button" class="training-data-badge ${iconState.className}" aria-label="${escapeHtml(tooltipText)}" aria-describedby="${tooltipId}">
                        ${iconState.svg}
                    </button>
                </span>
                <div class="tooltip" id="${tooltipId}" data-tooltip-origin="leaderboard-training" role="tooltip">${escapeHtml(tooltipText)}</div>
            </span>
        `;
    }

    function getColumnLabel(key) {
        const fallback = formatTitleCase(key);
        switch (key) {
            case 'model_name':
                return t('leaderboard_column_model', fallback);
            case 'input_capabilities':
                return t('leaderboard_column_input', fallback);
            case 'output_capabilities':
                return t('leaderboard_column_output', fallback);
            case 'tools':
                return t('leaderboard_column_tools', fallback);
            case 'training_data':
                return t('leaderboard_column_training_data', fallback);
            default:
                return metricDefinitions[key]
                    ? t(metricDefinitions[key].i18n, metricDefinitions[key].fallback)
                    : fallback;
        }
    }

    function getProviderErrorMessage(payload) {
        const detail = payload && typeof payload === 'object' && payload.detail
            ? payload.detail
            : payload;
        const errorType = detail && typeof detail === 'object' ? detail.type : null;
        const translatedErrors = {
            leaderboard_provider_api_key_invalid: [
                'leaderboard_provider_api_key_invalid',
                'The configured Artificial Analysis API key is invalid.',
            ],
            leaderboard_provider_full_tier_required: [
                'leaderboard_provider_full_tier_required',
                'Full model data requires an Artificial Analysis Pro or Commercial API key.',
            ],
            leaderboard_provider_rate_limited: [
                'leaderboard_provider_rate_limited',
                'The Artificial Analysis daily request limit has been reached.',
            ],
            leaderboard_provider_unavailable: [
                'leaderboard_provider_unavailable',
                'Artificial Analysis is temporarily unavailable.',
            ],
            leaderboard_provider_invalid_data: [
                'leaderboard_provider_invalid_data',
                'Artificial Analysis returned invalid leaderboard data.',
            ],
            leaderboard_provider_unexpected_response: [
                'leaderboard_provider_unexpected_response',
                'Artificial Analysis returned an unexpected response.',
            ],
        };
        const translation = translatedErrors[errorType];
        return translation ? t(translation[0], translation[1]) : null;
    }

    function updateSourceInformation() {
        // The locale dictionary and API response load independently. Do not
        // reveal a placeholder summary until real provider metadata exists.
        if (!state.sourceMetadataReady) {
            return;
        }

        const sourceInfo = document.getElementById('leaderboardSourceInfo');
        if (sourceInfo) {
            const dataLevelLabel = state.dataLevel === 'full'
                ? t('leaderboard_data_level_full', 'Full model data')
                : t('leaderboard_data_level_free', 'Free data');
            const version = Number.isFinite(state.intelligenceIndexVersion)
                ? state.intelligenceIndexVersion.toFixed(1)
                : '—';
            sourceInfo.textContent = t(
                'leaderboard_source_summary',
                '{dataLevel} · API tier: {providerTier} · Intelligence Index v{version}'
            )
                .replace('{dataLevel}', dataLevelLabel)
                .replace('{providerTier}', formatTitleCase(state.providerTier))
                .replace('{version}', version);
            sourceInfo.hidden = false;
        }

        // The three composite indices are available at both levels. Individual
        // benchmarks are only explained when the group selected Full data.
        document.querySelectorAll('[data-leaderboard-level="full"]').forEach(element => {
            element.hidden = state.dataLevel !== 'full';
        });
    }

    function sortCurrentDataBy(column, direction = 'desc') {
        const isStringColumn = column === 'model_name';
        const isAscending = direction === 'asc';

        const parseNumeric = (raw) => {
            if (typeof raw === 'number') {
                return raw;
            }
            if (typeof raw === 'string') {
                const parsed = parseFloat(raw);
                return Number.isFinite(parsed) ? parsed : null;
            }
            return null;
        };

        state.data.sort((a, b) => {
            if (isStringColumn) {
                const aVal = (a[column] ?? '').toString();
                const bVal = (b[column] ?? '').toString();
                const comparison = aVal.localeCompare(bVal, undefined, { sensitivity: 'base' });
                return isAscending ? comparison : -comparison;
            }

            const evalsA = a.evaluations || {};
            const evalsB = b.evaluations || {};
            const aRaw = evalsA[column];
            const bRaw = evalsB[column];
            const aVal = parseNumeric(aRaw);
            const bVal = parseNumeric(bRaw);
            const safeA = aVal === null ? (isAscending ? Infinity : -Infinity) : aVal;
            const safeB = bVal === null ? (isAscending ? Infinity : -Infinity) : bVal;

            if (safeA === safeB) {
                return 0;
            }
            return isAscending ? safeA - safeB : safeB - safeA;
        });
    }

    function safeJsonParse(text) {
        if (!text || !text.trim().length) {
            return null;
        }
        try {
            return JSON.parse(text);
        } catch (_) {
            return null;
        }
    }

    function extractErrorMessage(payload, rawBody) {
        if (payload && typeof payload === 'object') {
            if (typeof payload.detail === 'string' && payload.detail.trim().length) {
                return payload.detail.trim();
            }
            if (payload.detail && typeof payload.detail === 'object' && typeof payload.detail.message === 'string') {
                return payload.detail.message.trim();
            }
            if (typeof payload.message === 'string' && payload.message.trim().length) {
                return payload.message.trim();
            }
        }

        if (typeof rawBody === 'string' && rawBody.trim().length) {
            return rawBody.trim();
        }

        return null;
    }

    function renderErrorState(message) {
        const displayMessage = message || t('leaderboard_error_title', 'Failed to load leaderboard data. Please try again later.');
        const retryLabel = t('leaderboard_retry_button', 'Try again');
        contentElement.innerHTML = `
            <div class="error" role="alert">
                <span>${escapeHtml(displayMessage)}</span>
                <button type="button" class="retry-button">${escapeHtml(retryLabel)}</button>
            </div>
        `;
        reapplyTranslations();

        const retryBtn = contentElement.querySelector('.retry-button');
        if (retryBtn) {
            retryBtn.addEventListener('click', () => {
                fetchLeaderboard();
            });
        }
    }

    function renderSetupNotice(message) {
        contentElement.innerHTML = `
            <div class="setup-notice" role="alert">
                <h2 data-i18n="leaderboard_setup_missing_title">${t('leaderboard_setup_missing_title', 'Leaderboard not available')}</h2>
                <p>${escapeHtml(message)}</p>
                <button class="setup-notice-button" type="button" data-i18n="leaderboard_setup_home_button">${t('leaderboard_setup_home_button', 'Back to home')}</button>
            </div>
        `;
        reapplyTranslations();

        const setupButton = contentElement.querySelector('.setup-notice-button');
        if (setupButton) {
            setupButton.addEventListener('click', () => {
                window.location.href = '/';
            });
        }
    }

    function clearBusyState() {
        if (contentElement) {
            contentElement.setAttribute('aria-busy', 'false');
        }
    }

    async function fetchLeaderboard() {
        try {
            contentElement.setAttribute('aria-busy', 'true');
            document.body.style.display = 'block';

            contentElement.innerHTML = `
                <div class="loader-container" role="status" aria-label="${escapeHtml(t('leaderboard_loading', 'Loading leaderboard…'))}" data-i18n-attr="aria-label:leaderboard_loading">
                    <div class="loader" aria-hidden="true"></div>
                    <span class="sr-only" data-i18n="leaderboard_loading">${t('leaderboard_loading', 'Loading leaderboard\u2026')}</span>
                </div>
            `;

            const response = await window.authedFetch(`/api/v1/llm/models/leaderboard`, {
                method: 'GET',
            });

            const rawBody = await response.text();
            const payload = safeJsonParse(rawBody);
            const detailPayload = payload && typeof payload === 'object' ? payload : null;

            if (response.status === 403) {
                const detail = detailPayload && detailPayload.detail ? detailPayload.detail : detailPayload;
                if (detail && (detail.type === 'leaderboard_access_denied')) {
                    window.location.href = '/';
                    return;
                }

                renderErrorState(t('leaderboard_access_denied', 'Access to the leaderboard is restricted for your group.'));
                return;
            }

            if (response.status === 503) {
                let message = t('leaderboard_setup_unavailable', 'Please ask your administrator to set up the LLM Model Leaderboard.');
                const detail = detailPayload && typeof detailPayload === 'object' ? detailPayload.detail : null;
                if (detail && typeof detail === 'object' && detail.message) {
                    message = detail.message;
                } else if (typeof detail === 'string') {
                    message = detail;
                }

                renderSetupNotice(message);
                return;
            }

            if (!response.ok) {
                const derivedMessage = getProviderErrorMessage(detailPayload)
                    || extractErrorMessage(detailPayload, rawBody)
                    || t('leaderboard_generic_error', 'Unexpected server response.');
                notifyError(`${t('leaderboard_error_notification', 'Unable to load leaderboard.')}\n${derivedMessage}`);
                renderErrorState(derivedMessage);
                return;
            }

            if (!payload) {
                throw new Error('Unable to parse leaderboard response.');
            }

            if (payload && typeof payload === 'object' && payload.status === 'not_configured') {
                const message = typeof payload.message === 'string' && payload.message.trim().length
                    ? payload.message
                    : t('leaderboard_setup_unavailable', 'Please ask your administrator to set up the LLM Model Leaderboard.');

                renderSetupNotice(message);
                return;
            }

            const models = Array.isArray(payload)
                ? payload
                : (payload && Array.isArray(payload.models) ? payload.models : []);

            state.data = models;
            state.dataLevel = payload && payload.data_level === 'full' ? 'full' : 'free';
            state.providerTier = payload && ['free', 'pro', 'commercial'].includes(payload.provider_tier)
                ? payload.provider_tier
                : 'free';
            state.intelligenceIndexVersion = payload && typeof payload.intelligence_index_version === 'number'
                ? payload.intelligence_index_version
                : null;
            state.sourceMetadataReady = true;
            updateSourceInformation();

            if (!state.data.length) {
                contentElement.innerHTML = `
                    <div class="empty-state">
                        <strong data-i18n="leaderboard_empty_title">${t('leaderboard_empty_title', 'No models found.')}</strong>
                        <div data-i18n="leaderboard_empty_subtitle">${t('leaderboard_empty_subtitle', 'Please check back later.')}</div>
                    </div>
                `;
                reapplyTranslations();
                return;
            }

            state.currentPage = 1;
            sortCurrentDataBy(state.sortColumn, state.sortDirection);
            renderTable();
        } catch (error) {
            console.error('Error fetching leaderboard:', error);

            const fallbackMessage = t('leaderboard_error_title', 'Failed to load leaderboard data. Please try again later.');
            notifyError(fallbackMessage);
            renderErrorState(fallbackMessage);
        } finally {
            clearBusyState();
        }
    }

    function sortData(column) {
        if (state.sortColumn === column) {
            state.sortDirection = state.sortDirection === 'asc' ? 'desc' : 'asc';
        } else {
            state.sortColumn = column;
            state.sortDirection = 'desc';
        }

        sortCurrentDataBy(state.sortColumn, state.sortDirection);
        state.currentPage = 1;
        updateSortIndicators();
        renderTableBody();
    }

    function formatValue(value, isIndex = false) {
        const baseClass = 'metric-value';
        if (value === null || value === undefined) {
            return `<span class="${baseClass} null-value">\u2014</span>`;
        }

        if (typeof value !== 'number') {
            const spanClass = isIndex ? `${baseClass} index-value` : baseClass;
            return `<span class="${spanClass}">${value}</span>`;
        }

        const formatted = isIndex
            ? value.toFixed(1)
            : value < 1
                ? (value * 100).toFixed(1) + '%'
                : value.toFixed(1);

        const spanClass = isIndex ? `${baseClass} index-value` : baseClass;
        return `<span class="${spanClass}">${formatted}</span>`;
    }

    function _getColumns() {
        const metricKeys = metricProfiles[state.dataLevel] || metricProfiles.free;
        return ['model_name', ...capabilityColumns, ...listColumns, ...auxiliaryColumns, ...metricKeys];
    }

    function _isDetailColumn(col) {
        return capabilityColumns.includes(col) || listColumns.includes(col) || auxiliaryColumns.includes(col);
    }

    function renderTable() {
        _tooltipIdCounter = 0;

        const columns = _getColumns();
        const captionText = t('leaderboard_table_caption', 'AI model performance benchmarks and capabilities');

        let tableHTML = `
            <div class="table-wrapper">
                <table>
                    <caption class="sr-only">${escapeHtml(captionText)}</caption>
                    <thead>
                        <tr>
                            ${columns.map(col => {
                                const sortClass = col === state.sortColumn
                                    ? `sort-${state.sortDirection}`
                                    : '';
                                const isDetail = _isDetailColumn(col);
                                const isSortable = !isDetail;
                                const classes = [isSortable ? 'sortable' : '', sortClass].filter(Boolean).join(' ');
                                const isMetric = col !== 'model_name' && !isDetail;
                                const styleAttr = isMetric ? 'style="text-align: right; padding-right: 28px;"' : (isDetail ? 'style="text-align: center;"' : '');
                                const dataAttr = getColumnDataI18nAttr(col);
                                const ariaSort = col === state.sortColumn
                                    ? (state.sortDirection === 'asc' ? 'ascending' : 'descending')
                                    : 'none';
                                const interactiveAttrs = isSortable
                                    ? `tabindex="0" aria-sort="${ariaSort}" data-column="${col}"`
                                    : '';
                                const sortIndicatorChar = col === state.sortColumn
                                    ? (state.sortDirection === 'asc' ? '\u2191' : '\u2193')
                                    : '\u2195';
                                const sortIndicator = isSortable
                                    ? `<span class="sort-indicator" aria-hidden="true">${sortIndicatorChar}</span>`
                                    : '';
                                return `
                                    <th class="${classes}"
                                        ${interactiveAttrs}
                                        ${styleAttr}
                                        ${dataAttr}>
                                        ${getColumnLabel(col)}${sortIndicator}
                                    </th>
                                `;
                            }).join('')}
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
            <div class="pagination-container"></div>
        `;

        clearTooltips();

        contentElement.innerHTML = tableHTML;

        renderTableBody();

        contentElement.querySelectorAll('th[data-column]').forEach(th => {
            th.addEventListener('click', () => {
                const column = th.getAttribute('data-column');
                if (column) sortData(column);
            });
            th.addEventListener('keydown', (event) => {
                if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) {
                    event.preventDefault();
                    const column = th.getAttribute('data-column');
                    if (column) sortData(column);
                }
            });
        });

        reapplyTranslations();
    }

    function renderTableBody() {
        const table = contentElement.querySelector('table');
        if (!table) return;

        clearTooltips();

        const tbody = table.querySelector('tbody');
        if (!tbody) return;

        _tooltipIdCounter = 0;

        const columns = _getColumns();

        const totalPages = Math.max(1, Math.ceil(state.data.length / state.pageSize));
        if (state.currentPage > totalPages) state.currentPage = totalPages;

        const startIdx = (state.currentPage - 1) * state.pageSize;
        const endIdx = Math.min(startIdx + state.pageSize, state.data.length);
        const pageData = state.data.slice(startIdx, endIdx);

        tbody.innerHTML = pageData.map(model => `
            <tr>
                ${columns.map(col => {
                    if (col === 'model_name') {
                        return `
                            <th scope="row" class="model-name-cell">
                                <span class="model-name">${escapeHtml(model.model_name)}</span>
                            </th>
                        `;
                    }

                    if (capabilityColumns.includes(col)) {
                        return `
                            <td class="capabilities-cell">
                                ${renderCapabilities(model[col])}
                            </td>
                        `;
                    }

                    if (col === 'tools') {
                        return `
                            <td class="tools-cell">
                                ${renderToolCategories(model.tool_categories, model[col])}
                            </td>
                        `;
                    }

                    if (col === 'training_data') {
                        return `
                            <td class="training-data-cell">
                                ${renderTrainingData(model[col])}
                            </td>
                        `;
                    }

                    const value = (model.evaluations || {})[col];
                    const isIndex = col.includes('index');
                    return `
                        <td style="text-align: right;">
                            ${formatValue(value, isIndex)}
                        </td>
                    `;
                }).join('')}
            </tr>
        `).join('');

        if (typeof window.setupTooltip === 'function') {
            tbody.querySelectorAll('.tooltip-container').forEach(container => window.setupTooltip(container));
        }

        renderPagination(totalPages);
        reapplyTranslations();
    }

    function updateSortIndicators() {
        contentElement.querySelectorAll('th[data-column]').forEach(th => {
            const col = th.getAttribute('data-column');
            th.classList.remove('sort-asc', 'sort-desc');
            const indicator = th.querySelector('.sort-indicator');
            if (col === state.sortColumn) {
                th.classList.add(state.sortDirection === 'asc' ? 'sort-asc' : 'sort-desc');
                th.setAttribute('aria-sort', state.sortDirection === 'asc' ? 'ascending' : 'descending');
                if (indicator) indicator.textContent = state.sortDirection === 'asc' ? '\u2191' : '\u2193';
            } else {
                th.setAttribute('aria-sort', 'none');
                if (indicator) indicator.textContent = '\u2195';
            }
        });
    }

    function renderPagination(totalPages) {
        const container = contentElement.querySelector('.pagination-container');
        if (!container) return;

        if (totalPages <= 1) {
            container.innerHTML = '';
            return;
        }

        const prevLabel = t('leaderboard_pagination_prev', 'Previous');
        const nextLabel = t('leaderboard_pagination_next', 'Next');
        const pageInfo = t('leaderboard_pagination_info', 'Page {page} of {totalPages}')
            .replace('{page}', String(state.currentPage))
            .replace('{totalPages}', String(totalPages));
        const paginationLabel = t('leaderboard_pagination_aria', 'Table pagination');

        container.innerHTML = `
            <div class="pagination" role="navigation" aria-label="${escapeHtml(paginationLabel)}" data-i18n-attr="aria-label:leaderboard_pagination_aria">
                <button type="button" class="pagination-btn" data-page="prev" ${state.currentPage <= 1 ? 'disabled' : ''}>${escapeHtml(prevLabel)}</button>
                <span class="pagination-info" aria-live="polite">${pageInfo}</span>
                <button type="button" class="pagination-btn" data-page="next" ${state.currentPage >= totalPages ? 'disabled' : ''}>${escapeHtml(nextLabel)}</button>
            </div>
        `;

        const prevBtn = container.querySelector('[data-page="prev"]');
        const nextBtn = container.querySelector('[data-page="next"]');

        if (prevBtn) {
            prevBtn.addEventListener('click', () => {
                if (state.currentPage > 1) {
                    state.currentPage--;
                    renderTableBody();
                    const wrapper = contentElement.querySelector('.table-wrapper');
                    if (wrapper) wrapper.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            });
        }

        if (nextBtn) {
            nextBtn.addEventListener('click', () => {
                if (state.currentPage < totalPages) {
                    state.currentPage++;
                    renderTableBody();
                    const wrapper = contentElement.querySelector('.table-wrapper');
                    if (wrapper) wrapper.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            });
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        fetchLeaderboard();
        setupDetailsAnimation();
    });

    // Rebuild the dynamic source sentence after a locale load or language
    // change because it contains runtime placeholders rather than static text.
    document.addEventListener('i18n:updated', updateSourceInformation);

    function setupDetailsAnimation() {
        document.querySelectorAll('.question details').forEach(details => {
            const summary = details.querySelector('summary');
            if (summary) {
                summary.setAttribute('aria-expanded', String(!!details.open));
            }
        });

        document.querySelectorAll('.question summary').forEach(summary => {
            let isAnimating = false;
            summary.addEventListener('click', (e) => {
                const details = summary.parentElement;
                if (isAnimating) {
                    e.preventDefault();
                    return;
                }
                if (details.open) {
                    e.preventDefault();
                    isAnimating = true;
                    details.classList.add('is-closing');
                    summary.setAttribute('aria-expanded', 'false');
                    setTimeout(() => {
                        details.removeAttribute('open');
                        details.classList.remove('is-closing');
                        isAnimating = false;
                    }, 260);
                } else {
                    summary.setAttribute('aria-expanded', 'true');
                }
            });
        });

        document.querySelectorAll('.question details').forEach(details => {
            const summary = details.querySelector('summary');
            if (summary) {
                details.addEventListener('toggle', () => {
                    summary.setAttribute('aria-expanded', String(details.open));
                });
            }
        });
    }
})();
