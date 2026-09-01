(function () {
    const backButton = document.getElementById('audioGenerationSettingsBack');
    const fieldsContainer = document.getElementById('audioGenerationSettingsFields');

    const UI = window.MediaGenerationUI;

    let initialized = false;
    let abortController = null;
    let currentValues = {};
    let autoSaveTimer = null;

    const MODEL_SETTING_KEYS = [
        'voice',
        'response_format',
        'language',
        'sample_rate',
        'bit_rate',
        'speed',
        'optimize_streaming_latency',
        'text_normalization',
    ];

    const translate = (key, fallback) =>
        (typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback ?? key)
            : fallback ?? key);

    const getSettingLabel = (key, fallback) => {
        switch (key) {
            case 'voice':
                return translate('schema_audio_generation_voice', fallback || 'Voice');
            case 'response_format':
                return translate('schema_audio_generation_response_format', fallback || 'Audio Format');
            case 'language':
                return translate('schema_xai_tts_language', fallback || 'Language');
            case 'sample_rate':
                return translate('schema_xai_tts_sample_rate', fallback || 'Sample rate');
            case 'bit_rate':
                return translate('schema_xai_tts_bit_rate', fallback || 'MP3 bit rate');
            case 'speed':
                return translate('schema_xai_tts_speed', fallback || 'Speech speed');
            case 'optimize_streaming_latency':
                return translate('schema_xai_tts_latency', fallback || 'Latency optimization');
            case 'text_normalization':
                return translate('schema_xai_tts_text_normalization', fallback || 'Text normalization');
            default:
                return fallback || key;
        }
    };

    const API_BASE = '/api/v1/admin';

    async function apiFetch(path, opts = {}) {
        const init = { method: opts.method || 'GET', ...opts };
        if (opts.body && typeof opts.body !== 'string') {
            init.body = JSON.stringify(opts.body);
            init.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
        }
        if (abortController) {
            init.signal = abortController.signal;
        }
        const res = await window.authedFetch(API_BASE + path, init);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    }

    function showStatus(msg, type = 'error') {
        if (!msg) return;
        if (type === 'success') return window.notifySuccess?.(msg);
        if (type === 'warning' || type === 'info') return window.notifyWarning?.(msg);
        window.notifyError?.(msg);
    }

    function debounce(fn, delayMs = 250) {
        let timer = null;
        return (...args) => {
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => fn(...args), delayMs);
        };
    }

    function setSelectMessage(select, message) {
        select.innerHTML = '';
        const option = document.createElement('option');
        option.value = '';
        option.textContent = message;
        select.appendChild(option);
    }

    function buildVoiceDetailsText(voice) {
        const labels = voice && typeof voice.labels === 'object' ? voice.labels : {};
        const tokens = [];
        if (labels.gender) tokens.push(String(labels.gender));
        if (labels.age) tokens.push(String(labels.age));
        if (labels.accent) tokens.push(String(labels.accent));
        if (labels.language) tokens.push(String(labels.language));
        if (labels.voice_type) tokens.push(String(labels.voice_type));
        if (voice?.category) tokens.push(String(voice.category));
        return tokens.join(' · ');
    }

    function providerUsesSearchableVoicePicker(providerType) {
        const normalized = String(providerType || '').trim().toLowerCase();
        return normalized === 'elevenlabs';
    }

    function stripAudioPricingFromLabel(option = {}) {
        const rawLabel = String(option.label || option.value || '').trim();
        const pricingLabel = String(option.metadata?.pricing_label || '').trim();
        if (pricingLabel && rawLabel.endsWith(` (${pricingLabel})`)) {
            return rawLabel.slice(0, -(` (${pricingLabel})`.length));
        }
        return rawLabel.replace(/\s*\([^)]*(?:\$|USD|per\s+(?:million|thousand|character|token)|\/\s*(?:1m|1k|char|token))[^)]*\)\s*$/i, '').trim() || rawLabel;
    }

    async function renderSearchableVoicePicker({ field, wrapper, providerId, modelSettings, initialValue, onValueChange }) {
        const initialVoiceId = initialValue == null ? '' : String(initialValue).trim();
        modelSettings[field.key] = initialVoiceId || '';

        wrapper.classList.add('media-gen-voice-picker');

        const summary = document.createElement('div');
        summary.className = 'media-gen-voice-picker-summary';
        wrapper.appendChild(summary);

        const searchInput = document.createElement('input');
        searchInput.type = 'search';
        searchInput.className = 'input media-gen-voice-search';
        const voiceSearchLabel = translate(
            'audio_generation_voice_search_placeholder',
            'Search voices by name, accent, gender, or style'
        );
        searchInput.placeholder = voiceSearchLabel;
        searchInput.setAttribute('aria-label', voiceSearchLabel);
        searchInput.setAttribute(
            'data-i18n-attr',
            'placeholder:audio_generation_voice_search_placeholder;aria-label:audio_generation_voice_search_placeholder'
        );
        wrapper.appendChild(searchInput);

        const infoEl = document.createElement('div');
        infoEl.className = 'media-gen-voice-info';
        wrapper.appendChild(infoEl);

        const listEl = document.createElement('div');
        listEl.className = 'media-gen-voice-list';
        wrapper.appendChild(listEl);

        const loadMoreBtn = document.createElement('button');
        loadMoreBtn.type = 'button';
        loadMoreBtn.className = 'om-button border ghost media-gen-voice-load-more';
        const loadMoreSpan = document.createElement('span');
        loadMoreSpan.textContent = translate('audio_generation_voice_load_more', 'Load more voices');
        loadMoreBtn.appendChild(loadMoreSpan);
        loadMoreBtn.hidden = true;
        wrapper.appendChild(loadMoreBtn);

        let selectedVoiceId = initialVoiceId;
        let selectedVoiceName = initialVoiceId;
        let query = '';
        let nextPageToken = null;
        let hasMore = false;
        let loading = false;
        let requestCounter = 0;
        const voicesById = new Map();
        let renderedVoices = [];

        const setInfoMessage = (message, isError = false) => {
            infoEl.textContent = message || '';
            infoEl.classList.toggle('is-error', Boolean(isError));
        };

        const updateSummary = () => {
            const value = selectedVoiceId || '';
            modelSettings[field.key] = value;
            if (!value) {
                summary.textContent = translate('audio_generation_voice_selected_none', 'No voice selected');
                return;
            }
            const labelText = translate('audio_generation_voice_selected', 'Selected voice');
            const display = selectedVoiceName && selectedVoiceName !== value
                ? `${selectedVoiceName} (${value})`
                : value;
            summary.innerHTML = '';
            summary.appendChild(document.createTextNode(`${labelText}: `));
            const strong = document.createElement('strong');
            strong.textContent = display;
            summary.appendChild(strong);
        };

        const renderVoiceList = () => {
            listEl.innerHTML = '';
            if (!renderedVoices.length) {
                setInfoMessage(translate('audio_generation_voice_search_empty', 'No voices found for this search.'));
                return;
            }

            for (const voice of renderedVoices) {
                const voiceId = String(voice.id || '').trim();
                if (!voiceId) continue;
                const voiceName = String(voice.name || voiceId).trim() || voiceId;
                const details = buildVoiceDetailsText(voice);
                const isSelected = selectedVoiceId === voiceId;

                const card = document.createElement('div');
                card.className = 'media-gen-voice-card';
                if (isSelected) card.classList.add('is-selected');

                const top = document.createElement('div');
                top.className = 'media-gen-voice-card-top';
                const name = document.createElement('p');
                name.className = 'media-gen-voice-card-name';
                name.textContent = voiceName;
                top.appendChild(name);

                const selectBtn = document.createElement('button');
                selectBtn.type = 'button';
                selectBtn.className = isSelected ? 'om-button border submit' : 'om-button border ghost';
                const selectSpan = document.createElement('span');
                selectSpan.textContent = isSelected
                    ? translate('audio_generation_voice_selected_short', 'Selected')
                    : translate('audio_generation_voice_select', 'Select');
                selectBtn.appendChild(selectSpan);
                selectBtn.addEventListener('click', () => {
                    selectedVoiceId = voiceId;
                    selectedVoiceName = voiceName;
                    updateSummary();
                    renderVoiceList();
                    onValueChange?.();
                });
                top.appendChild(selectBtn);
                card.appendChild(top);

                if (details) {
                    const detail = document.createElement('p');
                    detail.className = 'media-gen-voice-card-details';
                    detail.textContent = details;
                    card.appendChild(detail);
                }

                if (voice.description) {
                    const description = document.createElement('p');
                    description.className = 'media-gen-voice-card-description';
                    description.textContent = String(voice.description);
                    card.appendChild(description);
                }

                const previewUrl = String(voice.preview_url || '').trim();
                if (previewUrl) {
                    const audio = document.createElement('audio');
                    audio.controls = true;
                    audio.preload = 'none';
                    audio.src = previewUrl;
                    card.appendChild(audio);
                }

                listEl.appendChild(card);
            }
            setInfoMessage('');
        };

        const mergeVoices = (baseVoices, incomingVoices) => {
            const merged = [];
            const seen = new Set();
            [...baseVoices, ...incomingVoices].forEach((voice) => {
                const id = String(voice?.id || '').trim();
                if (!id || seen.has(id)) return;
                seen.add(id);
                merged.push(voice);
            });
            return merged;
        };

        const fetchVoices = async ({ append = false, voiceIds = '' } = {}) => {
            if (!providerId || loading) return;
            if (append && !nextPageToken) return;
            loading = true;
            const reqId = ++requestCounter;
            setInfoMessage(translate('audio_generation_voice_search_loading', 'Loading voices...'));
            if (!append) {
                listEl.innerHTML = '';
                listEl.appendChild(UI.buildLoadingPlaceholder(
                    translate('audio_generation_voice_search_loading', 'Loading voices...')
                ));
            }

            const params = new URLSearchParams({ provider_id: providerId, page_size: '24' });
            if (query) params.set('search', query);
            if (append && nextPageToken) params.set('next_page_token', nextPageToken);
            if (voiceIds) params.set('voice_ids', voiceIds);

            try {
                const payload = await apiFetch(`/settings/audio_generation/voices?${params.toString()}`);
                if (reqId !== requestCounter) return;

                const incoming = (Array.isArray(payload.voices) ? payload.voices : [])
                    .map((voice) => ({
                        id: String(voice?.id || '').trim(),
                        name: String(voice?.name || voice?.id || '').trim(),
                        description: String(voice?.description || '').trim() || '',
                        category: String(voice?.category || '').trim() || '',
                        preview_url: String(voice?.preview_url || '').trim() || '',
                        labels: voice?.labels && typeof voice.labels === 'object' ? voice.labels : {},
                    }))
                    .filter((voice) => voice.id);

                incoming.forEach((voice) => voicesById.set(voice.id, voice));
                renderedVoices = append ? mergeVoices(renderedVoices, incoming) : incoming;
                hasMore = Boolean(payload.has_more);
                nextPageToken = payload.next_page_token ? String(payload.next_page_token) : null;
                loadMoreBtn.hidden = !hasMore;

                if (selectedVoiceId && voicesById.has(selectedVoiceId)) {
                    selectedVoiceName = voicesById.get(selectedVoiceId).name || selectedVoiceId;
                }
                renderVoiceList();
                updateSummary();
            } catch (_) {
                if (reqId !== requestCounter) return;
                setInfoMessage(translate('audio_generation_voice_search_failed', 'Failed to load provider voices.'), true);
                loadMoreBtn.hidden = true;
            } finally {
                if (reqId === requestCounter) loading = false;
            }
        };

        searchInput.addEventListener('input', debounce(() => {
            query = String(searchInput.value || '').trim();
            nextPageToken = null;
            fetchVoices({ append: false });
        }, 250));
        loadMoreBtn.addEventListener('click', () => fetchVoices({ append: true }));

        updateSummary();
        if (selectedVoiceId) {
            await fetchVoices({ voiceIds: selectedVoiceId });
        }
        await fetchVoices();
    }

    function readFieldValue(field, control) {
        if (field.type === 'boolean') {
            return Boolean(control.checked);
        }
        if (field.type === 'number') {
            return control.value === '' ? null : Number(control.value);
        }
        return control.value;
    }

    function applyFieldValue(field, control, rawValue) {
        if (field.type === 'boolean') {
            control.checked = typeof rawValue === 'string'
                ? ['1', 'true', 'yes', 'on'].includes(rawValue.trim().toLowerCase())
                : Boolean(rawValue);
            return;
        }
        control.value = rawValue == null ? '' : String(rawValue);
    }

    function renderStandardField(field, target, modelSettings, initialValue, onValueChange) {
        let control;
        let valueControl;
        if (field.type === 'boolean') {
            const toggle = UI.buildToggle();
            control = toggle.wrap;
            valueControl = toggle.input;
        } else if (field.type === 'select' && field.options) {
            control = UI.buildSelect();
            valueControl = control;
            for (const option of field.options) {
                const optionEl = document.createElement('option');
                optionEl.value = option.value;
                optionEl.textContent = option.label || option.value;
                control.appendChild(optionEl);
            }
        } else {
            control = UI.buildInput({
                type: field.type === 'number' ? 'number' : 'text',
                placeholder: field.placeholder || '',
                attributes: field.attributes,
            });
            valueControl = control;
        }

        applyFieldValue(field, valueControl, initialValue);
        modelSettings[field.key] = readFieldValue(field, valueControl);
        valueControl.addEventListener(field.type === 'select' || field.type === 'boolean' ? 'change' : 'input', () => {
            modelSettings[field.key] = readFieldValue(field, valueControl);
            onValueChange?.();
        });

        target.appendChild(UI.buildSettingsRow({
            title: getSettingLabel(field.key, field.label) || field.key,
            description: field.description || '',
            control,
        }));

        if (field.type === 'select') {
            UI.upgradeSelect(valueControl, {
                ...field,
                placeholder: field.placeholder || '',
            });
        }
    }

    async function renderModelSettingsRows(fields, target, pageState, providerContext, onValueChange) {
        UI.clearContainer(target);

        if (!fields.length) {
            target.appendChild(UI.buildEmptyStateSection({
                title: translate('audio_generation_wizard_step3', 'Step 3: Model Settings'),
                description: translate('audio_generation_model_settings_empty', 'This model has no additional settings.'),
            }));
            return;
        }

        for (const field of fields) {
            const initialValue =
                currentValues.provider_id === pageState.providerId && currentValues.model_name === pageState.modelName
                    ? currentValues[field.key] ?? field.default
                    : field.default;
            const usesSearchableVoicePicker =
                field.key === 'voice' && providerUsesSearchableVoicePicker(providerContext.providerType);

            if (usesSearchableVoicePicker) {
                const wrapper = document.createElement('div');
                await renderSearchableVoicePicker({
                    field,
                    wrapper,
                    providerId: providerContext.providerId,
                    modelSettings: pageState.modelSettings,
                    initialValue,
                    onValueChange,
                });
                target.appendChild(UI.buildSettingsRow({
                    title: getSettingLabel(field.key, field.label) || field.key,
                    description: field.description || '',
                    control: wrapper,
                    column: true,
                }));
                continue;
            }

            renderStandardField(field, target, pageState.modelSettings, initialValue, onValueChange);
        }
    }

    async function loadPage() {
        if (!fieldsContainer) return;
        UI.showLoading(fieldsContainer, translate('admin_settings_loading', 'Loading settings...'));

        try {
            const [settingsPayload, providersPayload] = await Promise.all([
                apiFetch('/settings/audio_generation?include_values=true'),
                apiFetch('/settings/audio_generation/providers'),
            ]);
            currentValues = settingsPayload?.values || {};

            UI.clearContainer(fieldsContainer);
            const { section, body } = UI.buildSettingsSection({
                title: translate('audio_generation_wizard_title', 'Configure Audio Generation'),
                description: translate('audio_generation_card_subtitle', 'The audio generation model currently used in chats.'),
            });
            fieldsContainer.appendChild(section);

            const providerSelect = UI.buildSelect({
                id: 'audioGenProviderSelect',
                placeholder: translate('audio_generation_provider_select_default', 'Select a provider'),
            });
            const modelSelect = UI.buildSelect({
                id: 'audioGenModelSelect',
                placeholder: translate('audio_generation_model_select_default', 'Select a model'),
            });
            const settingsTarget = document.createElement('div');
            settingsTarget.className = 'media-gen-step-fields';

            body.appendChild(UI.buildSettingsRow({
                title: translate('schema_audio_generation_provider_id', 'Provider'),
                description: translate('schema_audio_generation_provider_id_desc_v2', 'Select a configured text-to-speech provider.'),
                control: providerSelect,
            }));
            const modelRow = UI.buildSettingsRow({
                title: translate('schema_audio_generation_model_name', 'Model'),
                description: translate('schema_audio_generation_model_name_desc', 'Choose which model is used for audio generation.'),
                control: modelSelect,
            });
            body.appendChild(modelRow);
            body.appendChild(settingsTarget);

            const providerById = new Map();
            providerSelect.innerHTML = `<option value="">${UI.escapeHtml(translate('audio_generation_provider_select_default', 'Select a provider'))}</option>`;
            (providersPayload.providers || []).forEach((provider) => {
                providerById.set(provider.id, provider);
                const option = document.createElement('option');
                option.value = provider.id;
                const providerLabel = window.formatProviderLabel?.(provider.provider) || provider.provider;
                option.textContent = `${provider.name} (${providerLabel})`;
                providerSelect.appendChild(option);
            });
            providerSelect.value = currentValues.provider_id || '';
            modelSelect.disabled = true;

            const pageState = {
                providerId: providerSelect.value,
                modelName: currentValues.model_name || '',
                modelSettings: {},
                activeFieldKeys: new Set(),
            };
            const updateWizardVisibility = () => {
                UI.setStepVisible(modelRow, Boolean(pageState.providerId));
            };
            updateWizardVisibility();

            const persist = async () => {
                if (!initialized) return;
                const cleared = {};
                for (const key of MODEL_SETTING_KEYS) {
                    if (!pageState.activeFieldKeys.has(key)) cleared[key] = null;
                }
                if (pageState.activeFieldKeys.has('voice') && !String(pageState.modelSettings.voice || '').trim()) {
                    return;
                }
                const payload = pageState.providerId && pageState.modelName
                    ? {
                        provider_id: pageState.providerId,
                        model_name: pageState.modelName,
                        ...cleared,
                        ...pageState.modelSettings,
                    }
                    : {
                        provider_id: '',
                        model_name: '',
                        voice: null,
                        response_format: null,
                        language: null,
                        sample_rate: null,
                        bit_rate: null,
                        speed: null,
                        optimize_streaming_latency: null,
                        text_normalization: null,
                    };
                await apiFetch('/settings/audio_generation', {
                    method: 'PATCH',
                    body: payload,
                });
                currentValues = { ...currentValues, ...payload };
            };

            const scheduleAutoSave = () => {
                if (autoSaveTimer) clearTimeout(autoSaveTimer);
                autoSaveTimer = setTimeout(() => {
                    autoSaveTimer = null;
                    persist().catch((error) => {
                        console.error('Failed to autosave audio generation settings', error);
                        showStatus(translate('audio_generation_status_save_failed', 'Failed to save configuration.'));
                    });
                }, 350);
            };

            const refreshModelSettings = async ({ autosaveAfterLoad = false } = {}) => {
                UI.clearContainer(settingsTarget);
                pageState.modelSettings = {};
                pageState.activeFieldKeys = new Set();
                if (!pageState.providerId || !pageState.modelName) {
                    return;
                }
                settingsTarget.appendChild(UI.buildLoadingPlaceholder(translate('admin_settings_loading', 'Loading settings...')));
                const data = await apiFetch(
                    `/settings/audio_generation/model_settings?provider_id=${encodeURIComponent(pageState.providerId)}&model_name=${encodeURIComponent(pageState.modelName)}`
                );
                const fields = (data.sections || []).flatMap((item) => item.fields || []);
                pageState.activeFieldKeys = new Set(fields.map((field) => field.key));
                await renderModelSettingsRows(fields, settingsTarget, pageState, {
                    providerId: pageState.providerId,
                    providerType: String(providerById.get(pageState.providerId)?.provider || '').toLowerCase(),
                }, scheduleAutoSave);
                if (autosaveAfterLoad) {
                    scheduleAutoSave();
                }
            };

            const refreshModels = async ({ preserveCurrent = false } = {}) => {
                setSelectMessage(modelSelect, translate('audio_generation_model_select_loading', 'Loading models...'));
                modelSelect.disabled = true;
                UI.clearContainer(settingsTarget);
                pageState.modelName = '';
                pageState.modelSettings = {};
                pageState.activeFieldKeys = new Set();
                if (!pageState.providerId) {
                    updateWizardVisibility();
                    setSelectMessage(modelSelect, translate('audio_generation_model_select_default', 'Select a model'));
                    if (!preserveCurrent) {
                        scheduleAutoSave();
                    }
                    return;
                }
                updateWizardVisibility();
                const data = await apiFetch(`/settings/audio_generation/models?provider_id=${encodeURIComponent(pageState.providerId)}`);
                const modelField = (data.sections || []).flatMap((item) => item.fields || []).find((field) => field.key === 'model_name');
                const options = modelField?.options || [];
                modelSelect.innerHTML = `<option value="">${UI.escapeHtml(translate('audio_generation_model_select_default', 'Select a model'))}</option>`;
                options.forEach((option) => {
                    const optionEl = document.createElement('option');
                    optionEl.value = option.value;
                    optionEl.textContent = stripAudioPricingFromLabel(option);
                    modelSelect.appendChild(optionEl);
                });
                modelSelect.disabled = !options.length;
                if (preserveCurrent && currentValues.model_name && options.some((option) => option.value === currentValues.model_name)) {
                    modelSelect.value = currentValues.model_name;
                    pageState.modelName = currentValues.model_name;
                    await refreshModelSettings();
                }
                UI.upgradeSelect(modelSelect, {
                    key: 'audioGenModelSelect',
                    placeholder: translate('audio_generation_model_select_default', 'Select a model'),
                });
            };

            providerSelect.addEventListener('change', async () => {
                pageState.providerId = providerSelect.value;
                pageState.modelName = '';
                updateWizardVisibility();
                try {
                    await refreshModels();
                } catch (error) {
                    console.error('Failed to refresh audio generation models', error);
                    showStatus(translate('audio_generation_model_select_failed', 'Failed to load models'));
                }
            });

            modelSelect.addEventListener('change', async () => {
                pageState.modelName = modelSelect.value;
                try {
                    await refreshModelSettings({ autosaveAfterLoad: true });
                } catch (error) {
                    console.error('Failed to refresh audio generation model settings', error);
                    showStatus(translate('audio_generation_model_settings_error', 'Failed to load model settings.'));
                }
            });

            UI.upgradeSelect(providerSelect, {
                key: 'audioGenProviderSelect',
                placeholder: translate('audio_generation_provider_select_default', 'Select a provider'),
            });
            await refreshModels({ preserveCurrent: true });
        } catch (error) {
            UI.clearContainer(fieldsContainer);
            showStatus(translate('audio_generation_status_load_failed', 'Unable to load audio generation settings.'));
        }
    }

    const handleBackClick = () => {
        window.activateAdminPage?.('tools');
    };

    window.initAudioGenerationSettingsPage = () => {
        if (initialized) return;
        initialized = true;
        abortController = new AbortController();
        backButton?.addEventListener('click', handleBackClick);
        loadPage();
    };

    window.teardownAudioGenerationSettingsPage = () => {
        if (!initialized) return;
        initialized = false;
        if (abortController) {
            abortController.abort();
            abortController = null;
        }
        if (autoSaveTimer) {
            clearTimeout(autoSaveTimer);
            autoSaveTimer = null;
        }
        backButton?.removeEventListener('click', handleBackClick);
        UI.clearContainer(fieldsContainer);
    };
})();
