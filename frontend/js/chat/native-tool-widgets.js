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

    /** Render an accessible, keyboard-native multiple-choice quiz. */
    function renderQuiz(root, data) {
        const questions = Array.isArray(data.questions) ? data.questions : [];
        const widget = element('section', 'native-tool-widget quiz-native-widget');
        const header = element('header', 'native-widget-header');
        const titleWrap = element('div', 'native-widget-title-wrap');
        titleWrap.append(
            element('p', 'native-widget-eyebrow', t('assistant_tool_quiz_name', 'Quiz')),
            element('h3', 'native-widget-title', data.title || t('assistant_tool_quiz_name', 'Quiz')),
            element('p', 'native-widget-subtitle', data.description || ''),
        );
        const progress = element('span', 'native-widget-progress-copy');
        header.append(titleWrap, progress);
        const body = element('div', 'quiz-native-body');
        const feedback = element('p', 'native-widget-feedback');
        feedback.setAttribute('role', 'status');
        feedback.setAttribute('aria-live', 'polite');
        let index = 0;
        let score = 0;
        let answered = false;

        function showQuestion() {
            answered = false;
            feedback.textContent = '';
            body.replaceChildren();
            if (index >= questions.length) {
                progress.textContent = '';
                body.append(
                    element('h4', 'native-widget-result-title', t('quiz_complete', 'Quiz complete')),
                    element('p', 'native-widget-result-copy', tf('quiz_score', 'You scored {score} out of {total}.', {
                        score,
                        total: questions.length,
                    })),
                );
                const restart = button('native-widget-primary-button', t('quiz_restart', 'Try again'));
                restart.addEventListener('click', () => {
                    index = 0;
                    score = 0;
                    showQuestion();
                });
                body.appendChild(restart);
                return;
            }

            const question = questions[index] || {};
            progress.textContent = tf('quiz_progress', '{current} of {total}', {
                current: index + 1,
                total: questions.length,
            });
            const fieldset = element('fieldset', 'quiz-native-fieldset');
            const legend = element('legend', 'quiz-native-question', question.question || '');
            fieldset.appendChild(legend);
            (Array.isArray(question.options) ? question.options : []).forEach((option, optionIndex) => {
                const optionButton = button('quiz-native-option', option);
                optionButton.dataset.optionIndex = String(optionIndex);
                optionButton.addEventListener('click', () => {
                    if (answered) return;
                    answered = true;
                    const correctIndex = numberValue(question.correct_option_index, -1);
                    const correct = optionIndex === correctIndex;
                    if (correct) score += 1;
                    fieldset.querySelectorAll('button').forEach((candidate) => {
                        candidate.disabled = true;
                        const candidateIndex = numberValue(candidate.dataset.optionIndex, -1);
                        if (candidateIndex === correctIndex) candidate.dataset.answer = 'correct';
                        if (candidate === optionButton && !correct) candidate.dataset.answer = 'incorrect';
                    });
                    feedback.textContent = correct
                        ? t('quiz_correct', 'Correct.')
                        : t('quiz_incorrect', 'Not quite.');
                    if (question.explanation) {
                        feedback.textContent += ` ${question.explanation}`;
                    }
                    next.hidden = false;
                    next.focus();
                });
                fieldset.appendChild(optionButton);
            });
            const next = button('native-widget-primary-button', index + 1 === questions.length
                ? t('quiz_view_results', 'View results')
                : t('quiz_next', 'Next question'));
            next.hidden = true;
            next.addEventListener('click', () => {
                index += 1;
                showQuestion();
            });
            body.append(fieldset, feedback, next);
        }

        widget.append(header, body);
        root.replaceChildren(widget);
        showQuestion();
    }

    /** Render a self-contained study session without executing payload scripts. */
    function renderFlashcards(root, data) {
        const originalCards = Array.isArray(data.cards) ? data.cards.map((card) => ({ ...card })) : [];
        let cards = originalCards.map((card) => ({ ...card }));
        let index = 0;
        let revealed = false;
        let reversed = false;
        let shuffled = false;
        let mastered = 0;
        let reviews = 0;
        const widget = element('section', 'native-tool-widget flashcards-native-widget');
        const header = element('header', 'native-widget-header');
        const titleWrap = element('div', 'native-widget-title-wrap');
        titleWrap.append(
            element('p', 'native-widget-eyebrow', t('assistant_tool_flashcards_name', 'Flashcards')),
            element('h3', 'native-widget-title', data.title || t('assistant_tool_flashcards_name', 'Flashcards')),
            element('p', 'native-widget-subtitle', data.description || ''),
        );
        const controls = element('div', 'native-widget-header-actions');
        const shuffle = button('native-widget-pill', t('flashcards_shuffle', 'Shuffle'));
        const reverse = button('native-widget-pill', t('flashcards_reverse', 'Reverse'));
        shuffle.setAttribute('aria-pressed', 'false');
        reverse.setAttribute('aria-pressed', 'false');
        controls.append(shuffle, reverse);
        header.append(titleWrap, controls);

        const metrics = element('div', 'native-widget-metrics');
        const queueValue = element('dd');
        const masteryValue = element('dd');
        const reviewValue = element('dd');
        [
            [t('flashcards_queue', 'Queue'), queueValue],
            [t('flashcards_mastery', 'Mastered'), masteryValue],
            [t('flashcards_reviews', 'Reviews'), reviewValue],
        ].forEach(([label, value]) => {
            const item = element('dl', 'native-widget-metric');
            item.append(element('dt', '', label), value);
            metrics.appendChild(item);
        });

        const stage = element('div', 'flashcards-native-stage');
        const cardButton = button('flashcards-native-card');
        const sideLabel = element('span', 'flashcards-native-side-label');
        const cardText = element('strong', 'flashcards-native-text');
        const support = element('div', 'flashcards-native-support');
        cardButton.append(sideLabel, cardText, support);
        const actions = element('div', 'native-widget-actions');
        const flip = button('native-widget-primary-button', t('flashcards_show_answer', 'Show answer'));
        const again = button('native-widget-secondary-button', t('flashcards_again', 'Again'));
        const hard = button('native-widget-secondary-button', t('flashcards_hard', 'Hard'));
        const good = button('native-widget-primary-button', t('flashcards_got_it', 'Got it'));
        actions.append(flip, again, hard, good);

        function currentCard() {
            return cards[index] || null;
        }

        function renderCard() {
            const card = currentCard();
            queueValue.textContent = String(Math.max(cards.length - index, 0));
            masteryValue.textContent = `${mastered}/${originalCards.length}`;
            reviewValue.textContent = String(reviews);
            if (!card) {
                stage.replaceChildren(
                    element('h4', 'native-widget-result-title', t('flashcards_complete', 'Deck finished')),
                    element('p', 'native-widget-result-copy', tf('flashcards_summary', '{mastered} of {total} cards mastered.', {
                        mastered,
                        total: originalCards.length,
                    })),
                );
                const restart = button('native-widget-primary-button', t('flashcards_study_again', 'Study again'));
                restart.addEventListener('click', reset);
                stage.appendChild(restart);
                actions.hidden = true;
                return;
            }

            if (!stage.contains(cardButton)) stage.replaceChildren(cardButton);
            actions.hidden = false;
            const front = reversed ? card.back : card.front;
            const back = reversed ? card.front : card.back;
            sideLabel.textContent = revealed
                ? t('flashcards_answer', 'Answer')
                : t('flashcards_prompt', 'Prompt');
            cardText.textContent = revealed ? back : front;
            cardButton.classList.toggle('is-revealed', revealed);
            cardButton.setAttribute('aria-label', revealed
                ? t('flashcards_hide_answer', 'Show prompt')
                : t('flashcards_show_answer', 'Show answer'));
            support.replaceChildren();
            if (revealed) {
                [
                    [t('flashcards_example', 'Example'), card.example],
                    [t('flashcards_hint', 'Hint'), card.hint],
                    [t('flashcards_note', 'Note'), card.note],
                ].filter((item) => item[1]).forEach(([label, value]) => {
                    const item = element('p', 'flashcards-native-support-item');
                    item.append(element('span', '', `${label}: `), document.createTextNode(String(value)));
                    support.appendChild(item);
                });
            }
            flip.hidden = revealed;
            again.hidden = hard.hidden = good.hidden = !revealed;
        }

        function toggleReveal() {
            if (!currentCard()) return;
            revealed = !revealed;
            renderCard();
        }

        function rate(kind) {
            if (!revealed || !currentCard()) return;
            reviews += 1;
            if (kind === 'good') mastered += 1;
            if (kind === 'again') {
                cards.splice(Math.min(index + 3, cards.length), 0, { ...currentCard() });
            }
            if (kind === 'hard') {
                cards.splice(Math.min(index + 5, cards.length), 0, { ...currentCard() });
            }
            index += 1;
            revealed = false;
            renderCard();
        }

        function reset() {
            cards = originalCards.map((card) => ({ ...card }));
            index = 0;
            revealed = false;
            mastered = 0;
            reviews = 0;
            renderCard();
        }

        cardButton.addEventListener('click', toggleReveal);
        flip.addEventListener('click', toggleReveal);
        again.addEventListener('click', () => rate('again'));
        hard.addEventListener('click', () => rate('hard'));
        good.addEventListener('click', () => rate('good'));
        reverse.addEventListener('click', () => {
            reversed = !reversed;
            reverse.setAttribute('aria-pressed', String(reversed));
            reverse.classList.toggle('is-active', reversed);
            reset();
        });
        shuffle.addEventListener('click', () => {
            shuffled = !shuffled;
            shuffle.setAttribute('aria-pressed', String(shuffled));
            shuffle.classList.toggle('is-active', shuffled);
            reset();
            if (shuffled) {
                for (let cursor = cards.length - 1; cursor > 0; cursor -= 1) {
                    const swapIndex = Math.floor(Math.random() * (cursor + 1));
                    [cards[cursor], cards[swapIndex]] = [cards[swapIndex], cards[cursor]];
                }
            }
            renderCard();
        });

        widget.append(header, metrics, stage, actions);
        root.replaceChildren(widget);
        renderCard();
    }

    /** Build the card shell consumed by the existing deep-research controller. */
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
        quiz: renderQuiz,
        flashcards: renderFlashcards,
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
