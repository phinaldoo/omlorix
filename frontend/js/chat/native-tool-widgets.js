(function () {
    'use strict';

    const FRONTEND_WIDGET_TYPES = new Set([
        'weather',
        'quiz',
        'flashcards',
        'deep_research',
        'skill_draft',
        'notes_result',
    ]);

    /** Resolve translated UI copy while retaining an English bootstrap fallback. */
    function t(key, fallback) {
        return typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback)
            : fallback;
    }

    /** Interpolate translated strings when the shared formatter is not loaded yet. */
    function tf(key, fallback, values = {}) {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, values);
        }
        return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_match, token) => {
            const value = values[token];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    /** Create a DOM node without interpolating tool-controlled values into HTML. */
    function element(tag, className = '', text = '') {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== '') node.textContent = String(text);
        return node;
    }

    function button(className, label) {
        const node = element('button', className, label);
        node.type = 'button';
        return node;
    }

    function parseWidgetData(content) {
        if (content && typeof content === 'object') return content;
        const source = String(content || '').trim();
        if (!source) throw new Error('Widget data is empty.');
        const parsed = JSON.parse(source);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            throw new Error('Widget data must be an object.');
        }
        return parsed;
    }

    function numberValue(value, fallback = 0) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    function formatDate(value, options) {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value || '');
        return new Intl.DateTimeFormat(document.documentElement.lang || undefined, options).format(date);
    }

    function weatherSymbol(code) {
        const value = Number(code);
        if (value === 0) return '☀️';
        if ([1, 2].includes(value)) return '🌤️';
        if (value === 3) return '☁️';
        if ([45, 48].includes(value)) return '🌫️';
        if ([51, 53, 55, 56, 57].includes(value)) return '🌦️';
        if ([61, 63, 65, 66, 67, 80, 81, 82].includes(value)) return '🌧️';
        if ([71, 73, 75, 77, 85, 86].includes(value)) return '❄️';
        if ([95, 96, 99].includes(value)) return '⛈️';
        return '🌡️';
    }

    function temperature(value) {
        const match = String(value ?? '').match(/-?\d+(?:\.\d+)?/);
        return match ? String(Math.round(Number(match[0]))) : '--';
    }

    /** Render the non-interactive weather card from normalized provider data. */
    function renderWeather(root, data) {
        const current = data.current_weather || {};
        const forecast = data.forecast || {};
        const hourly = forecast.hourly || {};
        const daily = forecast.daily || {};
        const widget = element('section', 'native-tool-widget weather-native-widget');
        widget.setAttribute('aria-label', t('assistant_tool_weather_name', 'Weather'));

        const header = element('header', 'native-widget-header');
        const titleWrap = element('div', 'native-widget-title-wrap');
        titleWrap.append(
            element('h3', 'native-widget-title', data.city || t('weather_unknown_location', 'Unknown location')),
            element('p', 'native-widget-subtitle', [data.date, data.time].filter(Boolean).join(' · ')),
        );
        header.append(titleWrap);

        const currentRow = element('div', 'weather-native-current');
        const currentCopy = element('div', 'weather-native-current-copy');
        currentCopy.append(
            element('strong', 'weather-native-temperature', `${temperature(current.temperature)}°`),
            element('span', 'weather-native-condition', current.description || ''),
        );
        currentRow.append(
            currentCopy,
            element('span', 'weather-native-symbol', weatherSymbol(current.weathercode)),
        );

        const details = element('dl', 'native-widget-metrics');
        const humidityValues = Array.isArray(hourly.relative_humidity) ? hourly.relative_humidity : [];
        const metricValues = [
            [t('weather_high', 'High'), temperature(daily.temperature_daily_high?.[0]) + '°'],
            [t('weather_low', 'Low'), temperature(daily.temperature_daily_low?.[0]) + '°'],
            [t('weather_humidity', 'Humidity'), humidityValues.length ? `${numberValue(humidityValues[0])}%` : '--'],
            [t('weather_wind', 'Wind'), String(current.windspeed ?? '--')],
        ];
        metricValues.forEach(([label, value]) => {
            const item = element('div', 'native-widget-metric');
            item.append(element('dt', '', label), element('dd', '', value));
            details.appendChild(item);
        });

        const dailyList = element('div', 'weather-native-forecast');
        dailyList.setAttribute('aria-label', t('weather_daily_forecast', 'Daily forecast'));
        const dates = Array.isArray(daily.date) ? daily.date : [];
        dates.slice(0, 7).forEach((date, index) => {
            const day = element('div', 'weather-native-day');
            day.append(
                element('span', 'weather-native-day-name', formatDate(date, { weekday: 'short' })),
                element('span', 'weather-native-day-symbol', weatherSymbol(daily.weather_code?.[index])),
                element(
                    'span',
                    'weather-native-day-temperature',
                    `${temperature(daily.temperature_daily_high?.[index])}° / ${temperature(daily.temperature_daily_low?.[index])}°`,
                ),
            );
            dailyList.appendChild(day);
        });

        widget.append(header, currentRow, details, dailyList);
        root.replaceChildren(widget);
    }

    /** Read-only compatibility for saved/imported study widgets from retired tools. */
    function renderLegacyStudy(root, data) {
        const widget = element('section', 'native-tool-widget legacy-study-widget');
        widget.append(
            element('h3', 'native-widget-title', data.title || ''),
            element('p', 'native-widget-subtitle', data.description || ''),
            element('p', 'native-widget-subtitle', t('study_legacy_archive', 'Saved study material. Ask for an interactive visualization to practice it again.')),
        );
        const questions = Array.isArray(data.questions) ? data.questions.slice(0, 20) : [];
        const cards = Array.isArray(data.cards) ? data.cards.slice(0, 40) : [];
        [...questions, ...cards].forEach((item) => {
            if (!item || typeof item !== 'object') return;
            const details = element('details');
            details.appendChild(element('summary', '', item.question || item.front || ''));
            const options = Array.isArray(item.options) ? item.options : [];
            if (options.length) {
                const list = element('ol');
                options.forEach((option) => list.appendChild(element('li', '', option)));
                details.appendChild(list);
            }
            const answer = Number.isInteger(item.correct_option_index)
                ? options[item.correct_option_index] : item.back;
            if (answer) details.appendChild(element('p', '', `${t('flashcards_answer', 'Answer')}: ${answer}`));
            for (const text of [item.explanation, item.hint, item.example, item.pronunciation, item.category, item.note]) {
                if (text) details.appendChild(element('p', '', text));
            }
            widget.appendChild(details);
        });
        root.replaceChildren(widget);
    }

    function renderDeepResearch(root, data) {
        const terminal = Boolean(data.terminal) || ['completed', 'failed', 'error', 'cancelled'].includes(String(data.status));
        const progressValue = terminal ? 100 : 4;
        const widget = element('section', 'deep-research-widget');
        const runId = String(data.run_id || '');
        Object.assign(widget.dataset, {
            widgetId: runId,
            runId,
            sessionId: runId,
            generationId: String(data.generation_id || ''),
            status: String(data.status || 'running'),
            phase: String(data.phase || 'starting'),
            model: String(data.model || ''),
            executionMode: String(data.execution_mode || 'custom'),
            errorCode: String(data.error_code || ''),
            warningCode: String(data.warning_code || ''),
            knownPhases: JSON.stringify(data.known_phases || []),
            activitySteps: JSON.stringify(data.activity_steps || []),
            finalReportPath: String(data.final_report_path || ''),
            archivePath: String(data.archive_path || ''),
            files: JSON.stringify(data.files || []),
        });
        widget.setAttribute('aria-live', 'polite');
        widget.setAttribute('aria-busy', String(!terminal));
        const statusKey = data.status === 'completed'
            ? (data.has_completion_warning ? 'deep_research_completed_with_warnings' : 'deep_research_completed')
            : (['failed', 'error'].includes(data.status) ? 'deep_research_failed'
                : (data.status === 'cancelled' ? 'deep_research_cancelled' : 'deep_research_starting'));
        const statusFallback = data.status === 'completed'
            ? (data.has_completion_warning ? 'Research complete with warnings.' : 'Research completed.')
            : (['failed', 'error'].includes(data.status) ? 'Deep research failed.'
                : (data.status === 'cancelled' ? 'Deep research cancelled.' : 'Starting research'));

        const icon = element('span', 'deep-research-card-icon');
        icon.dataset.role = 'icon';
        icon.setAttribute('aria-hidden', 'true');
        const body = element('div', 'deep-research-card-body');
        const heading = element('div', 'deep-research-card-heading');
        const title = element('h3', 'deep-research-title', t('deep_research_title', 'Deep Research'));
        const status = element('span', 'deep-research-status', t(statusKey, statusFallback));
        status.dataset.role = 'status';
        heading.append(title, status);
        const query = element('p', 'deep-research-query', data.query || '');
        const progress = element('div', 'deep-research-progress');
        progress.setAttribute('role', 'progressbar');
        progress.setAttribute('aria-label', t('deep_research_progress_aria', 'Research progress'));
        progress.setAttribute('aria-valuemin', '0');
        progress.setAttribute('aria-valuemax', '100');
        progress.setAttribute('aria-valuenow', String(progressValue));
        const progressBar = element('span', 'deep-research-progress-bar');
        progressBar.dataset.role = 'progress';
        progressBar.style.width = `${progressValue}%`;
        progress.appendChild(progressBar);
        const error = element('p', 'deep-research-error', ['failed', 'error'].includes(data.status) ? t(statusKey, statusFallback) : '');
        error.dataset.role = 'error';
        error.setAttribute('role', 'alert');
        error.hidden = !['failed', 'error'].includes(data.status);
        const open = button('deep-research-open', data.status === 'completed'
            ? t('deep_research_view_report', 'View report')
            : t('deep_research_open_details', 'View research'));
        open.dataset.action = 'open';
        body.append(heading, query, progress, error, open);
        const chevron = button('deep-research-card-chevron');
        chevron.dataset.role = 'chevron';
        chevron.dataset.action = 'toggle';
        chevron.setAttribute('aria-controls', 'deepResearchSidebar');
        chevron.setAttribute('aria-expanded', 'false');
        chevron.setAttribute('aria-label', t('deep_research_open_details', 'View research'));
        widget.append(icon, body, chevron);
        root.replaceChildren(widget);
    }

    /** Create the trusted result-card shell used by the skill draft sidebar. */
    function renderSkillDraft(root, data) {
        const card = element('div', 'skill-draft-result-card canvas-markdown-result-widget');
        card.dataset.draftId = String(data.draft_id || '');
        const header = element('div', 'canvas-markdown-result-header');
        const icon = element('div', 'skill-draft-result-icon canvas-markdown-result-icon');
        icon.dataset.role = 'card-icon';
        icon.setAttribute('aria-hidden', 'true');
        const meta = element('div', 'skill-draft-result-meta canvas-markdown-result-meta');
        const title = element('div', 'skill-draft-result-title canvas-markdown-result-title', data.name || 'untitled-skill');
        title.dataset.role = 'card-title';
        const summary = element('div', 'skill-draft-result-sub canvas-markdown-result-sub');
        summary.dataset.role = 'card-summary';
        meta.append(title, summary);
        header.append(icon, meta);
        const open = button('skill-draft-result-open-btn canvas-markdown-result-open-btn');
        open.dataset.action = 'open-editor';
        open.setAttribute('aria-expanded', 'false');
        open.setAttribute('aria-controls', 'skillDraftPreviewPanel');
        const openIcon = element('span');
        openIcon.dataset.role = 'open-icon';
        openIcon.setAttribute('aria-hidden', 'true');
        const openLabel = element('span');
        openLabel.dataset.role = 'open-label';
        open.append(openIcon, openLabel);
        const store = element('div', 'skill-draft-widget-data');
        store.dataset.jsonStore = 'true';
        store.hidden = true;
        store.textContent = JSON.stringify(data);
        card.append(header, open, store);
        root.replaceChildren(card);
    }

    /** Build the compact Notes result card; notes.js attaches its open action. */
    function renderNotesResult(root, data) {
        const card = element('div', 'canvas-markdown-result-widget notes-tool-result-widget');
        card.dataset.noteId = String(data.note_id || '');
        card.dataset.noteTitle = String(data.title || '');
        card.dataset.noteOperation = String(data.operation || 'create');
        const header = element('div', 'canvas-markdown-result-header');
        const icon = element('div', 'canvas-markdown-result-icon canvas-type-markdown');
        icon.setAttribute('aria-hidden', 'true');
        if (window.Icons?.file) icon.innerHTML = window.Icons.file;
        const meta = element('div', 'canvas-markdown-result-meta');
        meta.append(
            element('div', 'canvas-markdown-result-title', data.title || ''),
            element('div', 'canvas-markdown-result-sub', t('notes_tool_widget_status_created', 'Created note')),
        );
        header.append(icon, meta);
        const open = button('canvas-markdown-result-open-btn notes-tool-result-open-btn');
        open.dataset.noteOpen = 'true';
        const openIcon = element('span');
        openIcon.setAttribute('aria-hidden', 'true');
        if (window.Icons?.eye) openIcon.innerHTML = window.Icons.eye;
        open.append(openIcon, element('span', 'canvas-markdown-result-open-label', t('notes_tool_open_note', 'Open Note')));
        card.append(header, open);
        root.replaceChildren(card);
    }

    const RENDERERS = {
        weather: renderWeather,
        quiz: renderLegacyStudy,
        flashcards: renderLegacyStudy,
        deep_research: renderDeepResearch,
        skill_draft: renderSkillDraft,
        notes_result: renderNotesResult,
    };

    /** Render one supported first-party widget entirely in the parent document. */
    function render(root, widgetType, content) {
        const type = String(widgetType || '').trim().toLowerCase();
        const renderer = RENDERERS[type];
        if (!root || !renderer) return false;
        renderer(root, parseWidgetData(content));
        return true;
    }

    window.nativeToolWidgets = {
        isSupported(widgetType) {
            return FRONTEND_WIDGET_TYPES.has(String(widgetType || '').trim().toLowerCase());
        },
        parseWidgetData,
        render,
    };
}());
