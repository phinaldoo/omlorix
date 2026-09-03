(function () {
    const dom = {
        page: document.getElementById('page-models'),
        list: document.getElementById('modelsList'),
        providerFilter: document.getElementById('modelProviderFilterSelect'),
        search: document.getElementById('modelSearchInput'),
        searchClear: document.getElementById('modelSearchClear'),
        bulkToggle: document.getElementById('toggleModelBulkSelectButton'),
        bulkEditButton: document.getElementById('bulkEditModelsButton'),
        bulkDeleteButton: document.getElementById('bulkDeleteModelsButton'),
        bulkClearButton: document.getElementById('clearModelBulkSelectionButton'),
        create: document.getElementById('createModelButton'),
        exportButton: document.getElementById('exportModelsButton'),
        importButton: document.getElementById('importModelsButton'),
        importInput: document.getElementById('importModelsFileInput'),
        importOverlay: document.getElementById('importModelsOverlay'),
        importClose: document.getElementById('importModelsClose'),
        importCancel: document.getElementById('importModelsCancel'),
        importConfirm: document.getElementById('importModelsConfirm'),
        importList: document.getElementById('importModelsList'),
        importSelectAll: document.getElementById('importModelsSelectAll'),
        importFileName: document.getElementById('importModelsFileName'),
        importStatus: document.getElementById('importModelsStatus'),
        deleteOverlay: document.getElementById('deleteModelOverlay'),
        deleteMessage: document.getElementById('deleteModelMessage'),
        deleteCancel: document.getElementById('deleteModelCancelButton'),
        deleteConfirm: document.getElementById('deleteModelPrimaryButton'),
        deleteConfirmText: document.getElementById('deleteModelPrimaryText'),
        editPage: document.getElementById('page-models-edit'),
        editForm: document.getElementById('modelEditForm'),
        editFormTitle: document.getElementById('modelEditFormTitle'),
        editFormSubtitle: document.getElementById('modelEditFormSubtitle'),
        editModelId: document.getElementById('modelEditModelIdInput'),
        editName: document.getElementById('modelEditNameInput'),
        editDescription: document.getElementById('modelEditDescriptionInput'),
        editIcon: document.getElementById('modelEditIconInput'),
        editStatus: document.getElementById('modelEditStatusSelect'),
        editAccessEveryone: document.getElementById('modelEditAccessEveryone'),
        editAccessUsers: document.getElementById('modelEditAccessUsers'),
        editAccessGroups: document.getElementById('modelEditAccessGroups'),
        editSchemaFields: document.getElementById('modelEditSchemaFields'),
        editSchemaLoading: document.getElementById('modelEditSchemaLoading'),
        editBanner: document.getElementById('modelEditBanner'),
        editBackButton: document.getElementById('modelEditFormBack'),
        editSubmitButton: document.getElementById('modelEditSubmit'),
    };

    if (!dom.page) {
        return;
    }

    const t = window.adminT || ((key, fallback) =>
        (typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback ?? key)
            : fallback ?? key));

    const formatT = window.adminFormatT || ((key, fallback, vars) => {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        const template = t(key, fallback);
        return String(template).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars?.[token];
            return value === undefined || value === null ? '' : String(value);
        });
    });

    const escapeHtml = (value) => {
        const div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    };

    const normalizeBackendErrorDetail = (detail) => {
        if (typeof detail !== 'string') {
            return '';
        }

        const normalized = detail.trim();
        if (!normalized) {
            return '';
        }

        return normalized.replace(/\.$/, '');
    };

    const translateBackendError = (detail, translateKey = '') => {
        if (typeof detail !== 'string') {
            return '';
        }

        const trimmedDetail = detail.trim();
        if (!trimmedDetail) {
            return '';
        }

        if (normalizeBackendErrorDetail(trimmedDetail) !== 'Update the default model before deleting this entry' || !translateKey) {
            return trimmedDetail;
        }

        return t(translateKey, trimmedDetail);
    };

    const getLocalizedModelDeleteErrorMessage = (error, fallback = '') => {
        const translatedDetail = translateBackendError(
            error?.payload?.detail || error?.message,
            'models_delete_default_model_error',
        );

        return translatedDetail || error?.message || fallback;
    };

    const summarizeModelDeleteFailures = (failedDeletions, fallback = '') => {
        if (!Array.isArray(failedDeletions) || !failedDeletions.length) {
            return fallback;
        }

        const messages = failedDeletions
            .map(({ error }) => getLocalizedModelDeleteErrorMessage(error))
            .filter(Boolean);

        if (!messages.length) {
            return fallback;
        }

        const uniqueMessages = [...new Set(messages)];
        return uniqueMessages.length === 1 ? uniqueMessages[0] : fallback;
    };

    const getFieldPlaceholder = window.getFieldPlaceholder || ((field, fallback = '') => {
        if (!field) {
            return fallback;
        }
        if (field.i18n_placeholder && typeof window.getTranslation === 'function') {
            return window.getTranslation(field.i18n_placeholder, field.placeholder || fallback);
        }
        return field.placeholder || fallback;
    });

    let modelsLanguageObserver = null;

    const updateSearchClearVisibility = () => {
        if (!dom.search || !dom.searchClear) {
            return;
        }
        const hasValue = Boolean(dom.search.value && dom.search.value.trim().length);
        dom.searchClear.hidden = !hasValue;
    };

    const handleSearchInput = () => {
        updateSearchClearVisibility();
        renderModels();
    };

    const handleSearchClear = (event) => {
        event.preventDefault();
        if (!dom.search) {
            return;
        }
        dom.search.value = '';
        dom.search.focus();
        dom.search.dispatchEvent(new Event('input', { bubbles: true }));
    };

    const formatProviderType = window.formatProviderLabel || ((key) => key || '');
    const modelsApi = window.modelsApi || {};

    const createReloadableSettingsController = (options = {}) => {
        if (typeof window.createSettingsPageController !== 'function') {
            return { init() {}, teardown() {}, reload() {} };
        }
        const baseController = window.createSettingsPageController(options);
        return {
            init() {
                baseController.init?.();
            },
            teardown() {
                baseController.teardown?.();
            },
            reload() {
                baseController.reload?.();
            },
        };
    };

    const getSettingsFieldRow = (container, fieldKey) =>
        container?.querySelector(`.settings-row[data-field-key="${fieldKey}"]`) || null;

    const getSettingsSelectControl = (container, fieldKey) =>
        getSettingsFieldRow(container, fieldKey)?.querySelector('select.admin-select-native, select.select, select') || null;

    const refreshSettingsSingleSelectUi = (select, fieldKey) => {
        if (!select || typeof window.initializeAdminSingleSelect !== 'function') {
            return;
        }
        const wrapper = select.closest('.admin-select');
        const parent = wrapper?.parentElement;
        if (!wrapper || !parent) {
            return;
        }
        const meta = window.initializeAdminSingleSelect(select, {
            key: fieldKey,
            placeholder: select.dataset.placeholder || t('admin_select_placeholder_single', 'Select an option…'),
        });
        select._singleSelect = meta;
        parent.replaceChild(meta.wrapper, wrapper);
    };

    const createProviderModelOptionsRefresher = ({
        container,
        providerFieldKey,
        modelFieldKey,
        fetchOptions,
        placeholderKey,
        placeholderFallback,
        loadingClassName = 'is-updating',
    }) => {
        let requestToken = 0;

        const setLoading = (loading) => {
            const row = getSettingsFieldRow(container, modelFieldKey);
            if (!row) {
                return;
            }
            row.classList.toggle(loadingClassName, Boolean(loading));
            const modelSelect = getSettingsSelectControl(container, modelFieldKey);
            if (modelSelect) {
                modelSelect.disabled = Boolean(loading);
            }
        };

        const applyOptions = (options) => {
            const modelSelect = getSettingsSelectControl(container, modelFieldKey);
            if (!modelSelect) {
                return;
            }
            const previousValue = modelSelect.value;
            const normalizedOptions = Array.isArray(options)
                ? options
                    .map((option) => {
                        const value = String(option?.value ?? '').trim();
                        if (!value) {
                            return null;
                        }
                        const label = String(option?.label ?? value).trim() || value;
                        return { value, label };
                    })
                    .filter(Boolean)
                : [];
            const nextValue = normalizedOptions.some((option) => option.value === previousValue)
                ? previousValue
                : '';

            modelSelect.innerHTML = '';
            const placeholderOption = document.createElement('option');
            placeholderOption.value = '';
            placeholderOption.textContent = modelSelect.dataset.placeholder || t(placeholderKey, placeholderFallback);
            modelSelect.appendChild(placeholderOption);
            normalizedOptions.forEach((option) => {
                const opt = document.createElement('option');
                opt.value = option.value;
                opt.textContent = option.label;
                modelSelect.appendChild(opt);
            });
            modelSelect.value = nextValue;
            if (modelSelect._singleSelect?.syncFromSelect) {
                modelSelect._singleSelect.syncFromSelect();
            }
            refreshSettingsSingleSelectUi(modelSelect, modelFieldKey);
        };

        const refresh = async (providerId) => {
            if (typeof fetchOptions !== 'function') {
                return;
            }
            const token = ++requestToken;
            setLoading(true);
            try {
                const response = await fetchOptions(providerId || '');
                if (token !== requestToken) {
                    return;
                }
                applyOptions(response?.options || []);
            } catch (error) {
                if (token !== requestToken) {
                    return;
                }
                console.error(`Failed to refresh ${modelFieldKey} options`, error);
                notifyError(error?.message || t('admin_request_failed', 'Request failed.'));
            } finally {
                if (token === requestToken) {
                    setLoading(false);
                }
            }
        };

        const refreshForCurrentProvider = () => {
            const providerSelect = getSettingsSelectControl(container, providerFieldKey);
            if (!providerSelect) {
                return;
            }
            refresh(providerSelect.value);
        };

        return {
            refresh,
            refreshForCurrentProvider,
        };
    };

    const createProviderBackedModelsSettingsController = ({
        pageKey,
        fieldsContainerId,
        statusId,
        providerFieldKey,
        modelFieldKey,
        fetchOptions,
        placeholderKey,
        placeholderFallback,
        reloadSchemaOnProviderChange = false,
        reloadSchemaOnModelChange = false,
    }) => {
        const fieldsContainer = document.getElementById(fieldsContainerId);
        if (!fieldsContainer || typeof window.createSettingsPageController !== 'function') {
            return { init() {}, teardown() {}, reload() {} };
        }

        const modelOptions = createProviderModelOptionsRefresher({
            container: fieldsContainer,
            providerFieldKey,
            modelFieldKey,
            fetchOptions,
            placeholderKey,
            placeholderFallback,
        });

        /**
         * Show immediate, accessible feedback while a provider or model is
         * saved and its dependent schema is fetched. The full-page settings
         * loader replaces this compact indicator once reloading begins.
         */
        const attachSchemaLoadingIndicator = () => {
            const attach = (fieldKey) => {
                const select = getSettingsSelectControl(fieldsContainer, fieldKey);
                const row = getSettingsFieldRow(fieldsContainer, fieldKey);
                if (!select || !row || select.dataset.schemaLoadingBound === 'true') {
                    return;
                }
                select.dataset.schemaLoadingBound = 'true';
                select.addEventListener('change', () => {
                    row.querySelector('.provider-schema-loading')?.remove();
                    const indicator = document.createElement('div');
                    indicator.className = 'provider-schema-loading';
                    indicator.setAttribute('role', 'status');
                    const spinner = document.createElement('span');
                    spinner.className = 'admin-loading-spinner admin-loading-spinner--small';
                    spinner.setAttribute('aria-hidden', 'true');
                    const label = document.createElement('span');
                    label.textContent = t('admin_settings_loading', 'Loading settings...');
                    indicator.append(spinner, label);
                    row.appendChild(indicator);
                });
            };
            if (reloadSchemaOnProviderChange) {
                attach(providerFieldKey);
            }
            if (reloadSchemaOnModelChange) {
                attach(modelFieldKey);
            }
        };

        const controller = createReloadableSettingsController({
            pageKey,
            containerId: fieldsContainer,
            statusId,
            onError: (message) => notifyError?.(message),
            onFieldSaved: ({ fieldKey, value }) => {
                if (fieldKey === providerFieldKey || (reloadSchemaOnModelChange && fieldKey === modelFieldKey)) {
                    setTimeout(() => {
                        if (reloadSchemaOnProviderChange || fieldKey === modelFieldKey) {
                            controller.reload();
                            return;
                        }
                        modelOptions.refresh(value);
                    }, 0);
                }
            },
            onRender: attachSchemaLoadingIndicator,
        });

        const refreshModelOptionsForCurrentProvider = () => {
            setTimeout(() => {
                modelOptions.refreshForCurrentProvider();
            }, 0);
        };

        return {
            init() {
                controller.init();
                refreshModelOptionsForCurrentProvider();
            },
            teardown() {
                controller.teardown();
            },
            reload() {
                controller.reload();
                refreshModelOptionsForCurrentProvider();
            },
        };
    };

    const modelsReadAloudSettingsController = (() => {
        const fieldsContainer = document.getElementById('modelsReadAloudSettingsFields');
        const statusNode = document.getElementById('modelsReadAloudSettingsStatus');
        if (!fieldsContainer || typeof window.createSettingsPageController !== 'function') {
            return { init() {}, teardown() {}, reload() {} };
        }

        let readAloudEnhanceTimer = null;
        let readAloudObserver = null;
        let readAloudProvidersPromise = null;
        const READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID = 'browser_native';
        const translate = (key, fallback) =>
            (typeof window.getTranslation === 'function'
                ? window.getTranslation(key, fallback ?? key)
                : fallback ?? key);
        const invalidateReadAloudProviderCache = () => {
            readAloudProvidersPromise = null;
        };
        const findFieldRow = (fieldKey) =>
            fieldsContainer.querySelector(`.settings-row[data-field-key="${fieldKey}"]`);
        const getSelectControl = (fieldKey) =>
            findFieldRow(fieldKey)?.querySelector('select.admin-select-native, select.select, select');
        const loadReadAloudProviders = async () => {
            if (typeof modelsApi.fetchReadAloudProviders !== 'function') {
                return new Map();
            }
            if (!readAloudProvidersPromise) {
                readAloudProvidersPromise = modelsApi.fetchReadAloudProviders()
                    .then((response) => {
                        const map = new Map();
                        const providers = Array.isArray(response?.providers) ? response.providers : [];
                        providers.forEach((provider) => {
                            const id = String(provider?.id || '').trim();
                            if (!id) {
                                return;
                            }
                            map.set(id, provider);
                        });
                        return map;
                    })
                    .catch((error) => {
                        readAloudProvidersPromise = null;
                        throw error;
                    });
            }
            return readAloudProvidersPromise;
        };
        const scheduleReadAloudEnhancement = () => {
            if (readAloudEnhanceTimer) {
                clearTimeout(readAloudEnhanceTimer);
            }
            readAloudEnhanceTimer = setTimeout(() => {
                enhanceReadAloudControls().catch((error) => {
                    console.error('Failed to enhance read aloud controls', error);
                });
            }, 0);
        };
        const buildVoiceDetailsText = (voice) => {
            const labels = voice && typeof voice.labels === 'object' ? voice.labels : {};
            const parts = [];
            if (labels.gender) parts.push(String(labels.gender));
            if (labels.age) parts.push(String(labels.age));
            if (labels.accent) parts.push(String(labels.accent));
            if (labels.language) parts.push(String(labels.language));
            if (labels.voice_type) parts.push(String(labels.voice_type));
            if (voice?.category) parts.push(String(voice.category));
            return parts.join(' · ');
        };
        const providerUsesSearchableVoicePicker = (providerType) => {
            const normalized = String(providerType || '').trim().toLowerCase();
            return normalized === 'elevenlabs';
        };
        const renderReadAloudElevenLabsVoicePicker = async ({ row, select, providerId, contextKey }) => {
            const controlWrapper = row?.querySelector('.settings-row-control');
            if (!controlWrapper || !select) {
                return;
            }

            const existingPicker = controlWrapper.querySelector('.read-aloud-elevenlabs-picker');
            if (existingPicker?.dataset.contextKey === contextKey) {
                return;
            }
            existingPicker?.remove();

            const selectUi = select.closest('.admin-select');
            if (selectUi) {
                selectUi.style.display = 'none';
            } else {
                select.style.display = 'none';
            }

            const picker = document.createElement('div');
            picker.className = 'read-aloud-elevenlabs-picker';
            picker.dataset.contextKey = contextKey;
            picker.style.cssText = 'display:grid;gap:10px;width:100%;';

            const selectedLabel = document.createElement('div');
            selectedLabel.style.cssText = 'font-size:12px;color:var(--text-color-secondary);';
            picker.appendChild(selectedLabel);

            const searchInput = document.createElement('input');
            searchInput.type = 'search';
            searchInput.className = 'settings-input';
            searchInput.style.cssText = 'width:100%;';
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
            picker.appendChild(searchInput);

            const infoEl = document.createElement('div');
            infoEl.style.cssText = 'font-size:12px;color:var(--text-color-secondary);min-height:18px;';
            picker.appendChild(infoEl);

            const listEl = document.createElement('div');
            listEl.style.cssText = [
                'display:grid',
                'gap:8px',
                'max-height:280px',
                'overflow:auto',
                'padding-right:4px',
            ].join(';');
            picker.appendChild(listEl);

            const loadMoreBtn = document.createElement('button');
            loadMoreBtn.type = 'button';
            loadMoreBtn.className = 'om-button border ghost';
            loadMoreBtn.innerHTML = `<span>${escapeHtml(translate('audio_generation_voice_load_more', 'Load more voices'))}</span>`;
            loadMoreBtn.hidden = true;
            picker.appendChild(loadMoreBtn);

            controlWrapper.appendChild(picker);

            let selectedVoiceId = String(select.value || '').trim();
            let selectedVoiceName = selectedVoiceId;
            let query = '';
            let nextPageToken = null;
            let hasMore = false;
            let loading = false;
            let requestCounter = 0;
            let renderedVoices = [];
            const voicesById = new Map();

            const ensureSelectOption = (value, label) => {
                const normalizedValue = String(value || '').trim();
                if (!normalizedValue) {
                    return;
                }
                const existingOption = Array.from(select.options || []).find(
                    (option) => String(option.value || '').trim() === normalizedValue
                );
                if (existingOption) {
                    existingOption.textContent = label || normalizedValue;
                    return;
                }
                const option = document.createElement('option');
                option.value = normalizedValue;
                option.textContent = label || normalizedValue;
                select.appendChild(option);
            };
            const syncSelectValue = (emitChange = true) => {
                ensureSelectOption(
                    selectedVoiceId,
                    selectedVoiceName && selectedVoiceName !== selectedVoiceId
                        ? `${selectedVoiceName} (${selectedVoiceId})`
                        : selectedVoiceId
                );
                select.value = selectedVoiceId || '';
                if (select._singleSelect?.syncFromSelect) {
                    select._singleSelect.syncFromSelect();
                }
                if (emitChange) {
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                }
            };
            const updateSelectedSummary = () => {
                if (!selectedVoiceId) {
                    selectedLabel.textContent = translate(
                        'audio_generation_voice_selected_none',
                        'No voice selected'
                    );
                    return;
                }
                const label = selectedVoiceName && selectedVoiceName !== selectedVoiceId
                    ? `${selectedVoiceName} (${selectedVoiceId})`
                    : selectedVoiceId;
                selectedLabel.textContent = `${translate('audio_generation_voice_selected', 'Selected voice')}: ${label}`;
            };
            const setInfoMessage = (message, isError = false) => {
                infoEl.textContent = message || '';
                infoEl.style.color = isError ? 'var(--error-text)' : 'var(--text-color-secondary)';
            };
            const renderVoiceList = () => {
                listEl.innerHTML = '';
                if (!renderedVoices.length) {
                    setInfoMessage(translate('audio_generation_voice_search_empty', 'No voices found for this search.'));
                    return;
                }
                setInfoMessage('');
                renderedVoices.forEach((voice) => {
                    const voiceId = String(voice?.id || '').trim();
                    if (!voiceId) {
                        return;
                    }
                    const voiceName = String(voice?.name || voiceId).trim() || voiceId;
                    const details = buildVoiceDetailsText(voice);
                    const isSelected = selectedVoiceId === voiceId;

                    const card = document.createElement('div');
                    card.style.cssText = [
                        'border:1px solid var(--border-color)',
                        'border-radius:10px',
                        'padding:10px',
                        'background:var(--bg-normal)',
                    ].join(';');

                    const topRow = document.createElement('div');
                    topRow.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:8px;';

                    const title = document.createElement('div');
                    title.style.cssText = 'font-weight:600;font-size:13px;';
                    title.textContent = voiceName;
                    topRow.appendChild(title);

                    const selectBtn = document.createElement('button');
                    selectBtn.type = 'button';
                    selectBtn.className = isSelected ? 'om-button border submit' : 'om-button border ghost';
                    selectBtn.style.cssText = 'height:28px;padding:0 10px;';
                    selectBtn.innerHTML = `<span>${escapeHtml(
                        isSelected
                            ? translate('audio_generation_voice_selected_short', 'Selected')
                            : translate('audio_generation_voice_select', 'Select')
                    )}</span>`;
                    selectBtn.addEventListener('click', () => {
                        selectedVoiceId = voiceId;
                        selectedVoiceName = voiceName;
                        updateSelectedSummary();
                        syncSelectValue(true);
                        renderVoiceList();
                    });
                    topRow.appendChild(selectBtn);
                    card.appendChild(topRow);

                    if (details) {
                        const detailsEl = document.createElement('div');
                        detailsEl.style.cssText = 'margin-top:4px;font-size:12px;color:var(--text-color-secondary);';
                        detailsEl.textContent = details;
                        card.appendChild(detailsEl);
                    }

                    if (voice?.description) {
                        const descriptionEl = document.createElement('div');
                        descriptionEl.style.cssText = 'margin-top:4px;font-size:12px;color:var(--text-color-secondary);';
                        descriptionEl.textContent = String(voice.description);
                        card.appendChild(descriptionEl);
                    }

                    const previewUrl = String(voice?.preview_url || '').trim();
                    if (previewUrl) {
                        const audio = document.createElement('audio');
                        audio.controls = true;
                        audio.preload = 'none';
                        audio.src = previewUrl;
                        audio.style.cssText = 'margin-top:8px;width:100%;max-width:360px;height:32px;';
                        card.appendChild(audio);
                    }

                    listEl.appendChild(card);
                });
            };
            const fetchVoices = async ({ append = false, voiceIds = [] } = {}) => {
                if (loading || typeof modelsApi.searchReadAloudVoices !== 'function') {
                    return;
                }
                loading = true;
                const requestToken = ++requestCounter;
                setInfoMessage(translate('audio_generation_voice_search_loading', 'Loading voices...'));
                try {
                    const payload = await modelsApi.searchReadAloudVoices({
                        providerId,
                        search: query,
                        pageSize: 24,
                        nextPageToken: append ? nextPageToken : '',
                        voiceIds,
                    });
                    if (requestToken !== requestCounter) {
                        return;
                    }
                    const incomingVoices = Array.isArray(payload?.voices) ? payload.voices : [];
                    const baseVoices = append ? renderedVoices : [];
                    const merged = [];
                    const seen = new Set();
                    [...baseVoices, ...incomingVoices].forEach((voice) => {
                        const voiceId = String(voice?.id || '').trim();
                        if (!voiceId || seen.has(voiceId)) {
                            return;
                        }
                        seen.add(voiceId);
                        merged.push(voice);
                        voicesById.set(voiceId, voice);
                    });
                    renderedVoices = merged;
                    hasMore = Boolean(payload?.has_more);
                    nextPageToken = String(payload?.next_page_token || '').trim() || null;
                    loadMoreBtn.hidden = !hasMore;
                    if (selectedVoiceId && voicesById.has(selectedVoiceId)) {
                        selectedVoiceName = String(voicesById.get(selectedVoiceId)?.name || selectedVoiceId);
                    }
                    renderVoiceList();
                    updateSelectedSummary();
                } catch (error) {
                    if (requestToken !== requestCounter) {
                        return;
                    }
                    setInfoMessage(
                        translate('audio_generation_voice_search_failed', 'Failed to load provider voices.'),
                        true
                    );
                    console.error('Failed to load read aloud voices', error);
                } finally {
                    if (requestToken === requestCounter) {
                        loading = false;
                    }
                }
            };

            const debounce = (fn, delayMs = 250) => {
                let timer = null;
                return (...args) => {
                    if (timer) {
                        clearTimeout(timer);
                    }
                    timer = setTimeout(() => fn(...args), delayMs);
                };
            };

            searchInput.addEventListener('input', debounce(() => {
                query = searchInput.value.trim();
                nextPageToken = null;
                renderedVoices = [];
                fetchVoices({ append: false });
            }));
            loadMoreBtn.addEventListener('click', () => {
                fetchVoices({ append: true });
            });
            select.addEventListener('change', () => {
                const nextValue = String(select.value || '').trim();
                if (nextValue === selectedVoiceId) {
                    return;
                }
                selectedVoiceId = nextValue;
                selectedVoiceName = voicesById.get(nextValue)?.name || nextValue;
                updateSelectedSummary();
                renderVoiceList();
            });

            if (selectedVoiceId && !voicesById.has(selectedVoiceId)) {
                await fetchVoices({ voiceIds: [selectedVoiceId] });
            }
            await fetchVoices();
            updateSelectedSummary();
        };
        const enhanceReadAloudControls = async () => {
            const providerSelect = getSelectControl('read_aloud_provider_id');
            const modelSelect = getSelectControl('read_aloud_model');
            const voiceRow = findFieldRow('read_aloud_voice');
            const voiceSelect = getSelectControl('read_aloud_voice');
            if (!providerSelect || !modelSelect || !voiceRow || !voiceSelect) {
                return;
            }
            const providerId = String(providerSelect.value || '').trim();
            if (!providerId || providerId === READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID) {
                return;
            }
            const providersById = await loadReadAloudProviders();
            const providerType = String(providersById.get(providerId)?.provider || '').trim().toLowerCase();
            if (!providerUsesSearchableVoicePicker(providerType)) {
                return;
            }
            const contextKey = `${providerId}:${String(modelSelect.value || '').trim()}`;
            await renderReadAloudElevenLabsVoicePicker({
                row: voiceRow,
                select: voiceSelect,
                providerId,
                contextKey,
            });
        };

        const controller = window.createSettingsPageController({
            pageKey: 'read_aloud',
            containerId: fieldsContainer,
            statusId: statusNode,
            onError: (message) => notifyError?.(message),
            onFieldSaved: ({ fieldKey }) => {
                if (fieldKey === 'read_aloud_provider_id' || fieldKey === 'read_aloud_model') {
                    setTimeout(() => {
                        api.reload();
                    }, 0);
                    return;
                }
            },
        });
        const api = {
            init() {
                invalidateReadAloudProviderCache();
                if (!readAloudObserver) {
                    readAloudObserver = new MutationObserver(() => {
                        scheduleReadAloudEnhancement();
                    });
                    readAloudObserver.observe(fieldsContainer, { childList: true, subtree: true });
                }
                controller.init();
                setTimeout(() => {
                    scheduleReadAloudEnhancement();
                }, 0);
            },
            teardown() {
                if (readAloudEnhanceTimer) {
                    clearTimeout(readAloudEnhanceTimer);
                    readAloudEnhanceTimer = null;
                }
                readAloudObserver?.disconnect();
                readAloudObserver = null;
                controller.teardown?.();
            },
            reload() {
                invalidateReadAloudProviderCache();
                controller.teardown?.();
                controller.init();
            },
        };
        return api;
    })();

    const modelsSettingsController = createReloadableSettingsController({
        pageKey: 'models',
        containerId: 'modelsSettingsFields',
        statusId: 'modelsSettingsStatus',
        onError: (message) => notifyError?.(message),
    });

    const modelsDictationSettingsController = (() => {
        const fieldsContainer = document.getElementById('modelsDictationSettingsFields');
        if (!fieldsContainer || typeof window.createSettingsPageController !== 'function') {
            return { init() {}, teardown() {}, reload() {} };
        }

        // Dictation owns two independent provider/model pairs: completed-file
        // transcription (also used by meetings) and the preferred live stream.
        // One settings renderer owns the container while these focused
        // refreshers update only their respective model select.
        const completedFileOptions = createProviderModelOptionsRefresher({
            container: fieldsContainer,
            providerFieldKey: 'transcription_provider_id',
            modelFieldKey: 'transcription_model',
            fetchOptions: modelsApi.fetchTranscriptionModels,
            placeholderKey: 'models_select_transcription_placeholder',
            placeholderFallback: 'Select a transcription model',
        });
        const liveOptions = createProviderModelOptionsRefresher({
            container: fieldsContainer,
            providerFieldKey: 'live_transcription_provider_id',
            modelFieldKey: 'live_transcription_model',
            fetchOptions: modelsApi.fetchLiveTranscriptionModels,
            placeholderKey: 'models_select_live_transcription_placeholder',
            placeholderFallback: 'Select a live transcription model',
        });
        const controller = createReloadableSettingsController({
            pageKey: 'dictation',
            containerId: fieldsContainer,
            statusId: 'modelsDictationSettingsStatus',
            onError: (message) => notifyError?.(message),
            onFieldSaved: ({ fieldKey }) => {
                if ([
                    'transcription_provider_id',
                    'transcription_model',
                    'live_transcription_provider_id',
                    'live_transcription_model',
                ].includes(fieldKey)) {
                    // Model selectors and provider-specific controls are
                    // server-composed wizard steps. Reload after each parent
                    // selection so obsolete fields disappear atomically.
                    setTimeout(() => controller.reload(), 0);
                }
            },
        });
        const refreshModels = () => {
            setTimeout(() => {
                completedFileOptions.refreshForCurrentProvider();
                liveOptions.refreshForCurrentProvider();
            }, 0);
        };
        return {
            init() {
                controller.init();
                refreshModels();
            },
            teardown() {
                controller.teardown();
            },
            reload() {
                controller.reload();
                refreshModels();
            },
        };
    })();

    const modelsRealtimeSettingsController = createProviderBackedModelsSettingsController({
        pageKey: 'realtime',
        fieldsContainerId: 'modelsRealtimeSettingsFields',
        statusId: 'modelsRealtimeSettingsStatus',
        providerFieldKey: 'realtime_provider_id',
        modelFieldKey: 'realtime_model',
        fetchOptions: modelsApi.fetchRealtimeModels,
        placeholderKey: 'models_select_realtime_placeholder',
        placeholderFallback: 'Select a realtime model',
        reloadSchemaOnProviderChange: true,
        reloadSchemaOnModelChange: true,
    });

    const state = {
        models: [],
        initialized: false,
        loading: false,
        deleteTarget: null,
        deleteTargets: [],
        view: 'list',
        actionMenuListenersAttached: false,
        bulkSelectionMode: false,
        selectedModelIds: new Set(),
    };

    const setEditSchemaLoadingMessage = (message = t('provider_form_loading', 'Loading model configuration…')) => {
        if (!dom.editSchemaLoading) {
            return;
        }
        dom.editSchemaLoading.textContent = message;
    };

    const getModelDisplayLabel = (detail = {}) => {
        const preferred = [detail.name, detail.model_name, detail.id]
            .map((value) => (typeof value === 'string' ? value.trim() : ''))
            .find(Boolean);
        return preferred || t('common_model', 'model');
    };

    const isManagedBaseModel = (model = {}) =>
        String(model?.model_kind || 'base') === 'base' && !model?.is_custom_agent;

    const getBulkSchemaLoadingStatus = (current, total, modelName) =>
        formatT('models_bulk_schema_loading_progress', 'Loading {current}/{total} model schemas for {name}.', {
            current,
            total,
            name: modelName || t('common_model', 'model'),
        });

    const resolveModelIconMarkup = (model = {}) => {
        const fallback = Icons?.omlorixModel || Icons?.omlorix || '';
        const pickIconMarkup = (value, imageAlt) => {
            if (window.IconPicker?.renderModelIconMarkup) {
                return window.IconPicker.renderModelIconMarkup(value, {
                    fallback: '',
                    imageAlt,
                });
            }
            if (typeof value !== 'string') {
                return '';
            }
            const trimmed = value.trim();
            if (!trimmed) {
                return '';
            }
            if (trimmed.startsWith('<')) {
                return trimmed;
            }
            const mapped = Icons?.[trimmed];
            if (typeof mapped === 'string' && mapped.trim()) {
                return fallback;
            }
            return '';
        };

        const fromModel = pickIconMarkup(model.model_icon, t('models_icon_alt', 'Model icon'));
        if (fromModel) {
            return fromModel;
        }
        const fromProvider = pickIconMarkup(model.provider_icon, t('providers_icon_alt', 'Provider icon'));
        if (fromProvider) {
            return fromProvider;
        }
        return fallback || '<span class="provider-icon-fallback">?</span>';
    };

    const actionMenuState = {
        openMenu: null,
    };

    const getSelectedModels = () =>
        state.models.filter((model) => state.selectedModelIds.has(model.id));

    const clearBulkSelection = ({ preserveMode = false } = {}) => {
        state.selectedModelIds.clear();
        if (!preserveMode) {
            state.bulkSelectionMode = false;
        }
    };

    const updateBulkUiState = () => {
        const isMultiEditActive = state.bulkSelectionMode && state.view === 'list';
        if (dom.bulkToggle) {
            dom.bulkToggle.classList.toggle('active', state.bulkSelectionMode);
            dom.bulkToggle.textContent = state.bulkSelectionMode
                ? t('models_bulk_cancel_selection', 'Cancel Selection')
                : t('models_bulk_select_multiple', 'Select Multiple');
        }
        if (dom.bulkClearButton) {
            dom.bulkClearButton.hidden = !isMultiEditActive;
        }
        if (dom.bulkEditButton) {
            dom.bulkEditButton.hidden = !isMultiEditActive;
        }
        if (dom.bulkDeleteButton) {
            dom.bulkDeleteButton.hidden = !isMultiEditActive;
        }
        if (dom.bulkEditButton) {
            dom.bulkEditButton.disabled = state.selectedModelIds.size < 2;
        }
        if (dom.bulkDeleteButton) {
            dom.bulkDeleteButton.disabled = state.selectedModelIds.size < 1;
        }
    };

    const toggleBulkSelectionMode = (forceValue = null) => {
        const nextValue = typeof forceValue === 'boolean' ? forceValue : !state.bulkSelectionMode;
        state.bulkSelectionMode = nextValue;
        if (!nextValue) {
            clearBulkSelection({ preserveMode: true });
            state.bulkSelectionMode = false;
        }
        updateBulkUiState();
        renderModels();
    };

    const closeActionMenu = () => {
        if (!actionMenuState.openMenu) {
            return;
        }
        const { container, toggle } = actionMenuState.openMenu;
        container.classList.remove('open');
        container.classList.remove('open-up');
        toggle?.setAttribute('aria-expanded', 'false');
        actionMenuState.openMenu = null;
    };

    const updateActionMenuDirection = (container) => {
        if (!container) {
            return;
        }
        const dropdown = container.querySelector('.model-menu-dropdown');
        if (!dropdown) {
            return;
        }

        const boundary = container.closest('.model-list') || document.documentElement;
        const previousDisplay = dropdown.style.display;
        const previousVisibility = dropdown.style.visibility;

        dropdown.style.display = 'block';
        dropdown.style.visibility = 'hidden';

        const dropdownHeight = dropdown.getBoundingClientRect().height;
        const boundaryRect = boundary.getBoundingClientRect();
        const containerRect = container.getBoundingClientRect();
        const spaceBelow = Math.max(0, boundaryRect.bottom - containerRect.bottom - 4);
        const spaceAbove = Math.max(0, containerRect.top - boundaryRect.top - 4);
        const shouldOpenUp = dropdownHeight > spaceBelow && spaceAbove > spaceBelow;

        container.classList.toggle('open-up', shouldOpenUp);

        dropdown.style.display = previousDisplay;
        dropdown.style.visibility = previousVisibility;
    };

    const openActionMenu = (container, toggle) => {
        if (!container || !toggle) {
            return;
        }
        if (actionMenuState.openMenu?.container === container) {
            closeActionMenu();
            return;
        }
        closeActionMenu();
        updateActionMenuDirection(container);
        container.classList.add('open');
        toggle.setAttribute('aria-expanded', 'true');
        actionMenuState.openMenu = { container, toggle };
    };

    const ensureActionMenuListeners = () => {
        if (state.actionMenuListenersAttached) {
            return;
        }
        document.addEventListener('click', (event) => {
            if (!actionMenuState.openMenu) {
                return;
            }
            if (!actionMenuState.openMenu.container.contains(event.target)) {
                closeActionMenu();
            }
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && actionMenuState.openMenu) {
                closeActionMenu();
            }
        });
        state.actionMenuListenersAttached = true;
    };

    const importState = {
        payload: null,
        models: [],
        selected: new Set(),
        fileName: '',
    };

    const viewPages = {
        list: dom.page,
        ...(dom.editPage ? { edit: dom.editPage } : {}),
    };

    const showView = (key = 'list') => {
        Object.entries(viewPages).forEach(([viewKey, node]) => {
            if (!node) {
                return;
            }
            node.hidden = viewKey !== key;
        });
        state.view = key;
        updateBulkUiState();
    };

    const resetEditForm = () => {
        // Clear any existing field validation errors
        const errorRows = dom.editSchemaFields?.querySelectorAll('.settings-row.has-error') || [];
        errorRows.forEach((row) => {
            row.classList.remove('has-error', 'shake-error');
            const errorEl = row.querySelector('.field-error-message');
            if (errorEl) errorEl.remove();
        });
        editState.schemaControls = [];
        editState.detail = null;
        editState.details = [];
        editState.modelId = null;
        editState.modelIds = [];
        editState.mode = 'single';
        editState.providerKey = null;
        editState.mixedFieldKeys = new Set();
        editState.touchedFieldKeys = new Set();
        editState.initialSnapshot = null;
        if (dom.editForm) {
            dom.editForm.reset();
        }
        if (dom.editBanner) {
            dom.editBanner.hidden = true;
            dom.editBanner.innerHTML = '';
        }
        if (dom.editModelId) {
            dom.editModelId.value = '';
        }
        if (dom.editAccessUsers) {
            dom.editAccessUsers.value = '';
        }
        if (dom.editAccessGroups) {
            dom.editAccessGroups.value = '';
        }
        if (dom.editSchemaFields) {
            dom.editSchemaFields.innerHTML = '';
            if (dom.editSchemaLoading) {
                dom.editSchemaLoading.hidden = false;
                setEditSchemaLoadingMessage();
                dom.editSchemaFields.appendChild(dom.editSchemaLoading);
            }
        }
    };

    const coerceString = (value) => (typeof value === 'string' ? value : value != null ? String(value) : '');

    const stripUndefinedValues = (value) => {
        if (value === undefined) {
            return undefined;
        }
        if (value === null) {
            return null;
        }
        if (Array.isArray(value)) {
            const next = value
                .map((entry) => stripUndefinedValues(entry))
                .filter((entry) => entry !== undefined);
            return next;
        }
        if (typeof value === 'object') {
            const next = {};
            Object.entries(value).forEach(([key, entry]) => {
                const cleaned = stripUndefinedValues(entry);
                if (cleaned !== undefined) {
                    next[key] = cleaned;
                }
            });
            return Object.keys(next).length ? next : undefined;
        }
        return value;
    };

    const buildEditPayload = () => {
        const schemaValues = collectEditSchemaValues() || {};

        if (editState.mode === 'bulk') {
            const payload = {
                model_ids: [...editState.modelIds],
            };
            const applyIfTouched = (key, value) => {
                if (editState.touchedFieldKeys.has(key)) {
                    payload[key] = value;
                }
            };

            applyIfTouched('model_name', coerceString(schemaValues.model_name).trim());
            applyIfTouched('name', coerceString(schemaValues.name).trim());
            applyIfTouched('description', coerceString(schemaValues.description).trim());

            if (editState.touchedFieldKeys.has('model_icon')) {
                const rawModelIcon = schemaValues.model_icon || '';
                payload.model_icon = window.IconPicker?.sanitizeIconValue
                    ? window.IconPicker.sanitizeIconValue(rawModelIcon)
                    : rawModelIcon;
            }

            applyIfTouched('status', schemaValues.status || 'normal');
            if (editState.touchedFieldKeys.has('access')) {
                payload.access = buildAccessPayload(schemaValues.access);
            }

            delete schemaValues.model_name;
            delete schemaValues.name;
            delete schemaValues.description;
            delete schemaValues.model_icon;
            delete schemaValues.status;
            delete schemaValues.access;

            const schemaPayload = splitSchemaValuesByTools(schemaValues);
            let cleanedSettings = stripUndefinedValues(schemaPayload.settings) || {};
            if (window.WebsearchProviderLogic) {
                cleanedSettings = window.WebsearchProviderLogic.processWebsearchValuesForSubmit(
                    cleanedSettings,
                    editState.schemaControls
                );
            }

            const touchedSettings = {};
            editState.touchedFieldKeys.forEach((key) => {
                if (['model_name', 'name', 'description', 'model_icon', 'status', 'access', 'tools'].includes(key)) {
                    return;
                }
                const normalizedKey = key.startsWith('settings.') ? key.slice('settings.'.length) : key;
                const value = getNestedValue(cleanedSettings, normalizedKey);
                if (value !== undefined) {
                    setNestedValue(touchedSettings, normalizedKey.split('.').filter(Boolean), value);
                }
            });

            if (editState.touchedFieldKeys.has('tools')) {
                payload.tools = Array.isArray(schemaPayload.tools) ? schemaPayload.tools : [];
            }
            if (Object.keys(touchedSettings).length) {
                payload.settings = touchedSettings;
            }
            if (Object.keys(payload).length === 1) {
                notifyError(t('models_bulk_choose_field', 'Choose at least one field to update.'));
                return null;
            }
            return payload;
        }

        const modelName = coerceString(
            schemaValues.model_name
            ?? editState.detail?.model_name
        ).trim();
        if (!modelName) {
            notifyError(t('models_create_model_id_required', 'Model ID is required.'));
            return null;
        }

        const name = coerceString(schemaValues.name).trim();
        if (!name) {
            notifyError(t('models_create_model_name_required', 'Model name is required.'));
            return null;
        }

        const description = coerceString(schemaValues.description).trim();

        // Extract model_icon from schema values (icon picker provides this)
        const rawModelIcon = schemaValues.model_icon || '';
        const modelIcon = window.IconPicker?.sanitizeIconValue
            ? window.IconPicker.sanitizeIconValue(rawModelIcon)
            : rawModelIcon;

        const status = schemaValues.status || 'normal';
        const access = buildAccessPayload(schemaValues.access);

        delete schemaValues.model_name;
        delete schemaValues.name;
        delete schemaValues.description;
        delete schemaValues.model_icon;
        delete schemaValues.status;
        delete schemaValues.access;

        const schemaPayload = splitSchemaValuesByTools(schemaValues);
        let cleanedSettings = stripUndefinedValues(schemaPayload.settings) || {};
        
        // Process websearch provider values for combined providers
        if (window.WebsearchProviderLogic) {
            cleanedSettings = window.WebsearchProviderLogic.processWebsearchValuesForSubmit(
                cleanedSettings,
                editState.schemaControls
            );
        }
        
        const normalizedTools = Array.isArray(schemaPayload.tools) ? schemaPayload.tools : [];

        const payload = {
            model_name: modelName,
            name,
            description,
            model_icon: modelIcon,
            status,
            access,
            tools: normalizedTools,
            settings: cleanedSettings,
        };
        if (editState.detail && Object.prototype.hasOwnProperty.call(editState.detail, 'is_active')) {
            payload.is_active = Boolean(editState.detail.is_active);
        }
        return payload;
    };

    const submitEditForm = async (event) => {
        event?.preventDefault?.();
        if (editState.mode === 'single' && !editState.modelId) {
            notifyError(t('models_update_unavailable', 'Unable to update this model.'));
            return;
        }
        if (editState.mode === 'bulk' && editState.modelIds.length < 2) {
            notifyError(t('models_bulk_min_selection', 'Select at least two models to bulk edit.'));
            return;
        }

        // Validate required schema fields first
        const invalidRows = validateEditRequiredSchemaFields();
        if (invalidRows.length > 0) {
            const fieldCount = invalidRows.length;
            notifyError(formatT('admin_required_fields_count', 'Please fill in {count} required field(s).', { count: fieldCount }));
            scrollToFirstEditInvalidField(invalidRows);
            return;
        }

        const payload = buildEditPayload();
        if (!payload) {
            return;
        }
        
        // Validate websearch providers if web_search tool is enabled
        if (window.WebsearchProviderLogic?.validateWebsearchProviders) {
            const validation = window.WebsearchProviderLogic.validateWebsearchProviders({
                tools: payload.tools,
                settings: payload.settings,
                schemaControls: editState.schemaControls,
            });
            if (!validation.valid) {
                notifyError(validation.error);
                return;
            }
        }
        
        try {
            setButtonLoadingState(dom.editSubmitButton, true, t('admin_saving', 'Saving...'));
            const response = editState.mode === 'bulk'
                ? await modelsApi.bulkUpdateModels(payload)
                : await modelsApi.updateModel(editState.modelId, payload);
            if (!response.ok) {
                throw await modelsApi.buildResponseError(
                    response,
                    editState.mode === 'bulk'
                        ? t('models_update_bulk_failed', 'Failed to update models.')
                        : t('models_update_failed', 'Failed to update model.'),
                );
            }
            notifySuccess(
                editState.mode === 'bulk'
                    ? formatT('models_update_bulk_success', '{count} models updated successfully.', { count: editState.modelIds.length })
                    : t('models_update_success', 'Model updated successfully.')
            );
            await loadModels();
            modelsSettingsController.reload();
            clearBulkSelection();
            updateBulkUiState();
            goToListView();
        } catch (error) {
            console.error('Failed to update model(s)', error);
            notifyError(error?.message || (editState.mode === 'bulk'
                ? t('models_update_bulk_failed', 'Failed to update models.')
                : t('models_update_failed', 'Failed to update model.')));
        } finally {
            setButtonLoadingState(dom.editSubmitButton, false);
        }
    };

    const handleModelListClick = (event) => {
        if (event.target.closest('.model-select-cell')) {
            return;
        }
        if (event.target.closest('.model-delete-btn')) {
            return;
        }
        if (event.target.closest('.model-actions')) {
            return;
        }
        const row = event.target.closest('.model-row');
        if (!row?.dataset.modelId) {
            return;
        }
        if (state.bulkSelectionMode) {
            const modelId = row.dataset.modelId;
            if (state.selectedModelIds.has(modelId)) {
                state.selectedModelIds.delete(modelId);
            } else {
                state.selectedModelIds.add(modelId);
            }
            updateBulkUiState();
            renderModels();
            return;
        }
        openModelEdit(row.dataset.modelId);
    };

    const goToListView = () => {
        showView('list');
        resetEditForm();
        editState.modelId = null;
        editState.providerKey = null;
    };

    const editState = {
        modelId: null,
        modelIds: [],
        mode: 'single',
        providerKey: null,
        schemaFields: [],
        schemaControls: [],
        loading: false,
        detail: null,
        details: [],
        mixedFieldKeys: new Set(),
        touchedFieldKeys: new Set(),
        initialSnapshot: null,
    };
    const UNSAVED_GUARD_ID = 'admin-models-edit-unsaved';
    let unsavedGuardRegistered = false;

    const MODELS_EXPORT_VERSION = 1.0;

    const ensureArray = (value) => (Array.isArray(value) ? value : []);

    const UNLIMITED_COUNT_FIELDS = new Set([
        'max_image_count',
        'max_video_count',
        'max_audio_count',
        'max_document_count',
        'max_youtube_video_count',
    ]);

    /**
     * Proxy to shared initializeAdminSingleSelect helper to avoid duplication.
     */
    const initializeAdminSingleSelect = (select, field) => {
        if (typeof window.initializeAdminSingleSelect === 'function') {
            return window.initializeAdminSingleSelect(select, field);
        }
        throw new Error('initializeAdminSingleSelect helper is not available');
    };

    /**
     * Proxy to the shared searchable admin multi-select helper.
     */
    const initializeAdminMultiSelect = (select, field) => {
        if (typeof window.initializeAdminMultiSelect === 'function') {
            return window.initializeAdminMultiSelect(select, field);
        }
        throw new Error('initializeAdminMultiSelect helper is not available');
    };

    const sanitizeUnlimitedFieldValue = (fieldKey, value) => {
        if (!fieldKey) {
            return value;
        }
        const key = fieldKey.split('.').pop();
        if (!UNLIMITED_COUNT_FIELDS.has(key)) {
            return value;
        }
        if (value === undefined || value === null || value === '') {
            return value;
        }
        const numericValue = typeof value === 'string' ? Number(value.trim()) : Number(value);
        if (Number.isNaN(numericValue)) {
            return value;
        }
        return numericValue === -1 ? '' : value;
    };

    const coerceAttributeLength = (value) => {
        if (value === null || value === undefined || value === '') {
            return null;
        }
        const numeric = Number(value);
        return Number.isFinite(numeric) && numeric >= 0 ? numeric : null;
    };

    const applyFieldAttributesToControl = (control, field) => {
        if (!control || !field?.attributes) {
            return;
        }
        const { attributes } = field;
        const hasMin = attributes.min !== undefined && attributes.min !== null && attributes.min !== '';
        const hasMax = attributes.max !== undefined && attributes.max !== null && attributes.max !== '';

        const tagName = control.tagName?.toLowerCase();
        const inputType = control.type;

        const isNumberInput = tagName === 'input' && inputType === 'number';
        if (isNumberInput) {
            if (hasMin) {
                control.min = attributes.min;
            }
            if (hasMax) {
                control.max = attributes.max;
            }
            if (attributes.step !== undefined && attributes.step !== null && attributes.step !== '') {
                control.step = attributes.step;
            }
            return;
        }

        const isTextInput = tagName === 'textarea' || (tagName === 'input' && inputType !== 'number');
        if (!isTextInput) {
            return;
        }
        if (hasMin) {
            const minLength = coerceAttributeLength(attributes.min);
            if (minLength !== null) {
                control.minLength = minLength;
            }
        }
        if (hasMax) {
            const maxLength = coerceAttributeLength(attributes.max);
            if (maxLength !== null) {
                control.maxLength = maxLength;
            }
        }
    };

    const createSchemaControl = (field, rawValue, options = {}) => {
        const { mixed = false, bulkMode = false } = options;
        const value = sanitizeUnlimitedFieldValue(field?.key, rawValue);
        const { row, controlWrapper } = typeof createFieldLayout === 'function'
            ? createFieldLayout(field)
            : { row: document.createElement('div'), controlWrapper: document.createElement('div') };
        row.classList.add('settings-row');
        controlWrapper.classList.add('settings-row-control');

        const fieldType = field.type === 'input' ? (field.input_type || 'text') : field.type;
        let control;

        // Check if this is a model_icon field and use the icon picker
        if (window.IconPicker && window.IconPicker.shouldUseIconPicker(field)) {
            const { row: iconRow, control: iconControl } = window.IconPicker.createIconPickerControl(field, value);
            return { row: iconRow, control: iconControl };
        }

        switch (field.type) {
            case 'boolean':
            case 'toggle': {
                const label = document.createElement('label');
                label.className = 'toggle-switch';
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.className = 'toggle-input';
                checkbox.checked = mixed ? false : Boolean(value ?? field.default);
                if (mixed) {
                    checkbox.indeterminate = true;
                }
                const slider = document.createElement('span');
                slider.className = 'toggle-slider';
                label.append(checkbox, slider);
                control = checkbox;
                controlWrapper.appendChild(label);
                break;
            }
            case 'select': {
                const select = document.createElement('select');
                select.className = 'select';
                if (field.multiple) {
                    select.multiple = true;
                }

                if (bulkMode && mixed && !field.multiple) {
                    const mixedOption = document.createElement('option');
                    mixedOption.value = '';
                    mixedOption.textContent = t('admin_mixed_values', 'Mixed values');
                    select.appendChild(mixedOption);
                }

                const rawSelected = value ?? field.default;
                const selectedValues = field.multiple
                    ? new Set(
                        Array.isArray(rawSelected)
                            ? rawSelected.map((val) => String(val))
                            : rawSelected != null
                                ? [String(rawSelected)]
                                : []
                    )
                    : null;

                // Check if this is a websearch provider field
                const isWebsearchField = window.WebsearchProviderLogic?.isWebsearchProviderField(field);
                const rawOptions = ensureArray(field.options);
                const options = isWebsearchField && typeof window.WebsearchProviderLogic?.sortedProviderOptions === 'function'
                    ? window.WebsearchProviderLogic.sortedProviderOptions(rawOptions)
                    : rawOptions;

                options.forEach((option) => {
                    const opt = document.createElement('option');
                    opt.value = option.value;
                    const optionLabel = typeof window.resolveAdminSchemaOptionLabel === 'function'
                        ? window.resolveAdminSchemaOptionLabel(option, t)
                        : (option.i18n_label ? t(option.i18n_label, option.label || option.value || option.id || '') : (option.label || option.value || option.id || ''));
                    
                    // Store metadata for websearch provider options
                    if (isWebsearchField && option.metadata) {
                        opt.dataset.metadata = JSON.stringify(option.metadata);
                        // Add visual indicator for combined providers
                        if (option.metadata.has_combined) {
                            opt.textContent = formatT('websearch_provider_combined_suffix', '{label} (combined)', { label: optionLabel });
                        } else {
                            opt.textContent = optionLabel;
                        }
                    } else {
                        opt.textContent = optionLabel;
                    }
                    
                    if (field.multiple && selectedValues) {
                        opt.selected = selectedValues.has(String(option.value));
                    }
                    select.appendChild(opt);
                });

                if (!field.multiple) {
                    const selectedValue = rawSelected;
                    if (!mixed && selectedValue !== undefined && selectedValue !== null) {
                        select.value = selectedValue;
                    }
                }

                control = select;
                
                // Use custom multi-select for multiple selections
                if (field.multiple) {
                    const multiSelectMeta = initializeAdminMultiSelect(select, field);
                    select._multiSelect = multiSelectMeta;
                    controlWrapper.appendChild(multiSelectMeta.wrapper);
                } else {
                    const singleSelectMeta = initializeAdminSingleSelect(select, field);
                    select._singleSelect = singleSelectMeta;
                    controlWrapper.appendChild(singleSelectMeta.wrapper);
                }
                break;
            }
            case 'number': {
                const input = document.createElement('input');
                input.type = 'number';
                input.className = 'input';
                if (field.attributes?.min !== undefined) input.min = field.attributes.min;
                if (field.attributes?.max !== undefined) input.max = field.attributes.max;
                input.value = mixed ? '' : value ?? field.default ?? '';
                control = input;
                controlWrapper.appendChild(input);
                break;
            }
            case 'string_list': {
                const textarea = document.createElement('textarea');
                textarea.className = 'input';
                textarea.rows = 3;
                textarea.value = mixed ? '' : Array.isArray(value) ? value.join('\n') : value ?? field.default ?? '';
                control = textarea;
                controlWrapper.appendChild(textarea);
                break;
            }
            case 'input':
            case 'string':
            default: {
                const input = document.createElement(fieldType === 'textarea' ? 'textarea' : 'input');
                if (fieldType && fieldType !== 'textarea') {
                    input.type = fieldType;
                } else if (!fieldType || fieldType === 'textarea') {
                    input.type = 'text';
                }
                input.className = 'input';
                input.value = mixed ? '' : value ?? field.default ?? '';
                control = input;
                controlWrapper.appendChild(input);
                break;
            }
        }

        if (control) {
            const placeholder = getFieldPlaceholder(field);
            if (placeholder) {
                control.placeholder = placeholder;
            }
            if (mixed && (control.tagName === 'INPUT' || control.tagName === 'TEXTAREA')) {
                control.placeholder = t('admin_mixed_values', 'Mixed values');
            }
            if (bulkMode) {
                control.dataset.bulkFieldKey = field.key || '';
                if (mixed) {
                    control.dataset.mixedValue = 'true';
                }
                const markTouched = () => {
                    if (control.indeterminate) {
                        control.indeterminate = false;
                    }
                    if (field?.key) {
                        editState.touchedFieldKeys.add(field.key);
                    }
                    delete control.dataset.mixedValue;
                };
                control.addEventListener('input', markTouched);
                control.addEventListener('change', markTouched);
            }
        }

        applyFieldAttributesToControl(control, field);

        return { row, control };
    };

    /**
     * Check if a dependency field exists in the edit schema controls.
     */
    const editDependencyFieldExists = (dependencyKey) => {
        if (!dependencyKey) return false;
        return editState.schemaControls.some(({ field }) => field.key === dependencyKey);
    };

    /**
     * Get the current value of a field by its key from edit schema controls.
     */
    const getEditFieldValue = (fieldKey) => {
        const entry = editState.schemaControls.find(({ field }) => field.key === fieldKey);
        if (!entry) return undefined;
        const { field, control } = entry;
        if (!control) return undefined;
        switch (field.type) {
            case 'boolean':
            case 'toggle':
                return Boolean(control.checked);
            case 'select':
                if (field.multiple) {
                    return Array.from(control.selectedOptions || []).map((opt) => opt.value);
                }
                return control.value;
            case 'number':
                return control.value === '' ? null : Number(control.value);
            default:
                return control.value;
        }
    };

    const isSingleEditDependencySatisfied = (dependencyKey, requiredValue) => {
        if (!dependencyKey) return true;
        if (!editDependencyFieldExists(dependencyKey)) return true;
        const currentValue = getEditFieldValue(dependencyKey);
        if (window.SchemaDependencyUtils?.matchesDependencyValue) {
            return window.SchemaDependencyUtils.matchesDependencyValue(currentValue, requiredValue);
        }

        if (Array.isArray(requiredValue)) {
            const normalizedRequiredValues = requiredValue.map((value) => String(value));
            if (Array.isArray(currentValue)) {
                return normalizedRequiredValues.some((value) => currentValue.includes(value));
            }
            return normalizedRequiredValues.includes(String(currentValue));
        }

        if (Array.isArray(currentValue)) {
            return currentValue.includes(String(requiredValue));
        }
        if (typeof requiredValue === 'boolean') {
            return currentValue === requiredValue;
        }
        return String(currentValue) === String(requiredValue);
    };

    /**
     * Check if a field's dependency condition is satisfied.
     * Returns true if the field should be visible.
     */
    const isEditDependencySatisfied = (field) => {
        const firstSatisfied = isSingleEditDependencySatisfied(field.dependency, field.dependency_value);
        if (!firstSatisfied) {
            return false;
        }
        return isSingleEditDependencySatisfied(field.dependency2, field.dependency2_value);
    };

    /**
     * Update visibility of all dependent fields in edit form.
     */
    const refreshEditWebsearchCombinedState = () => {
        if (!window.WebsearchProviderLogic?.refreshScrapeFieldState) {
            return;
        }
        if (!Array.isArray(editState.schemaControls) || !editState.schemaControls.length) {
            return;
        }
        window.WebsearchProviderLogic.refreshScrapeFieldState(editState.schemaControls);
    };

    const updateEditDependentFieldsVisibility = () => {
        editState.schemaControls.forEach(({ field, control }) => {
            if (!field.dependency && !field.dependency2) return;
            const row = control?.closest?.('.settings-row');
            if (!row) return;
            const visible = isEditDependencySatisfied(field);
            row.hidden = !visible;
            row.style.display = visible ? '' : 'none';
        });
        refreshEditWebsearchCombinedState();
        window.syncSchemaSectionVisibility?.(dom.editSchemaFields);
        window.syncSectionBodyLastVisibleRow?.(dom.editSchemaFields);
    };

    /**
     * Attach change listeners to all controls that might be dependencies in edit form.
     */
    const attachEditDependencyListeners = () => {
        // Collect all dependency keys
        const dependencyKeys = new Set();
        editState.schemaControls.forEach(({ field }) => {
            if (field.dependency) {
                dependencyKeys.add(field.dependency);
            }
            if (field.dependency2) {
                dependencyKeys.add(field.dependency2);
            }
        });
        // Attach listeners to controls that are dependencies
        editState.schemaControls.forEach(({ field, control }) => {
            if (!dependencyKeys.has(field.key)) return;
            if (!control) return;
            control.addEventListener('change', updateEditDependentFieldsVisibility);
        });
    };

    /**
     * Check if a field value is empty (for required field validation).
     */
    const isEditFieldValueEmpty = (field, control) => {
        if (!control) return true;
        if (
            editState.mode === 'bulk'
            && control.dataset.mixedValue === 'true'
            && !editState.touchedFieldKeys.has(field?.key)
        ) {
            return false;
        }
        switch (field.type) {
            case 'boolean':
            case 'toggle':
                return false; // Booleans are never empty
            case 'select':
                return !control.value;
            case 'number':
                return control.value === '';
            case 'string_list':
                return control.value?.trim() === '';
            default:
                return control.value?.trim() === '';
        }
    };

    /**
     * Add error state to a field row.
     */
    const setEditFieldError = (row, message = t('admin_field_required', 'This field is required')) => {
        if (!row) return;
        row.classList.add('has-error');
        // Add error message if not already present
        let errorEl = row.querySelector('.field-error-message');
        if (!errorEl) {
            errorEl = document.createElement('p');
            errorEl.className = 'field-error-message';
            const controlWrapper = row.querySelector('.settings-row-control');
            if (controlWrapper) {
                controlWrapper.appendChild(errorEl);
            } else {
                row.appendChild(errorEl);
            }
        }
        errorEl.textContent = message;
        // Trigger shake animation
        row.classList.remove('shake-error');
        void row.offsetWidth; // Force reflow
        row.classList.add('shake-error');
    };

    /**
     * Clear error state from a field row.
     */
    const clearEditFieldError = (row) => {
        if (!row) return;
        row.classList.remove('has-error', 'shake-error');
        const errorEl = row.querySelector('.field-error-message');
        if (errorEl) {
            errorEl.remove();
        }
    };

    /**
     * Validate all required schema fields in edit form.
     * Returns array of invalid field rows.
     */
    const validateEditRequiredSchemaFields = () => {
        const invalidRows = [];
        editState.schemaControls.forEach(({ field, control }) => {
            if (!field || !control) return;
            const row = control.closest?.('.settings-row');
            if (!row) return;
            // Skip hidden fields (dependency not satisfied)
            if (row.hidden) return;
            // Clear any previous error
            clearEditFieldError(row);
            // Check if field is required and empty
            if (field.required && isEditFieldValueEmpty(field, control)) {
                const label = field.label || field.key || t('admin_this_field', 'This field');
                setEditFieldError(row, formatT('admin_field_required_named', '{field} is required', { field: label }));
                invalidRows.push(row);
            }
        });
        return invalidRows;
    };

    /**
     * Scroll to the first invalid field with smooth animation.
     */
    const scrollToFirstEditInvalidField = (invalidRows) => {
        if (!invalidRows.length) return;
        const firstRow = invalidRows[0];
        firstRow.scrollIntoView({
            behavior: 'smooth',
            block: 'center',
        });
        // Focus the input if possible
        const control = firstRow.querySelector('input, select, textarea');
        if (control) {
            setTimeout(() => control.focus(), 300);
        }
    };

    /**
     * Attach input listeners to clear errors on user input in edit form.
     */
    const attachEditErrorClearListeners = () => {
        editState.schemaControls.forEach(({ field, control }) => {
            if (!control) return;
            const row = control.closest?.('.settings-row');
            if (!row) return;
            const clearOnInput = () => {
                if (row.classList.contains('has-error')) {
                    clearEditFieldError(row);
                }
            };
            control.addEventListener('input', clearOnInput);
            control.addEventListener('change', clearOnInput);
        });
    };

    const parseMultiLineInput = (value) =>
        (value || '')
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter((line) => line.length > 0);

    const isStopSequencesField = (field) => {
        const segments = (field?.key || '').split('.');
        return segments[segments.length - 1] === 'stop_sequences';
    };

    const coerceValueByInputType = (field, value) => {
        if (value == null) {
            return value;
        }
        if (typeof value !== 'string') {
            return value;
        }
        const trimmed = value.trim();
        if (!trimmed) {
            return null;
        }
        const inputType = (field?.input_type || '').toLowerCase();
        if (inputType === 'int' || inputType === 'integer') {
            const parsed = Number.parseInt(trimmed, 10);
            return Number.isNaN(parsed) ? null : parsed;
        }
        if (inputType === 'float' || inputType === 'number') {
            const parsed = Number.parseFloat(trimmed);
            return Number.isNaN(parsed) ? null : parsed;
        }
        return value;
    };

    const normalizeSchemaFieldValue = (field, value) => {
        if (isStopSequencesField(field)) {
            if (Array.isArray(value)) {
                return value;
            }
            return parseMultiLineInput(value);
        }
        const coerced = coerceValueByInputType(field, value);
        if (typeof coerced === 'string' && coerced.trim() === '') {
            return null;
        }
        return coerced;
    };

    const getNestedValue = (source, path) => {
        if (!source || !path) {
            return undefined;
        }
        const segments = path.split('.').filter(Boolean);
        return segments.reduce((acc, key) => (acc == null ? acc : acc[key]), source);
    };

    const setNestedValue = (target, segments, value) => {
        if (!segments.length) {
            return;
        }
        const [first, ...rest] = segments;
        if (!rest.length) {
            target[first] = value;
            return;
        }
        if (typeof target[first] !== 'object' || target[first] === null) {
            target[first] = {};
        }
        setNestedValue(target[first], rest, value);
    };

    const normalizeSchemaSections = (schema) => {
        if (!schema) {
            return [];
        }
        if (Array.isArray(schema.sections)) {
            return schema.sections
                .map((section = {}) => {
                    const fields = Array.isArray(section.fields) ? section.fields.filter(Boolean) : [];
                    return fields.length
                        ? {
                            title: section.title ?? null,
                            description: section.description ?? null,
                            i18n_title: section.i18n_title ?? null,
                            i18n_description: section.i18n_description ?? null,
                            fields,
                        }
                        : null;
                })
                .filter(Boolean);
        }
        if (Array.isArray(schema.fields)) {
            return [
                {
                    title: schema.title ?? null,
                    description: schema.description ?? null,
                    fields: schema.fields.filter(Boolean),
                },
            ];
        }
        if (Array.isArray(schema)) {
            return [
                {
                    title: null,
                    description: null,
                    fields: schema.filter(Boolean),
                },
            ];
        }
        return [];
    };

    const cloneSchemaField = (field) => JSON.parse(JSON.stringify(field || {}));

    const stableSerialize = (value) => {
        if (Array.isArray(value)) {
            return `[${value.map((entry) => stableSerialize(entry)).join(',')}]`;
        }
        if (value && typeof value === 'object') {
            return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableSerialize(value[key])}`).join(',')}}`;
        }
        return JSON.stringify(value);
    };

    const areValuesEqual = (left, right) => stableSerialize(left) === stableSerialize(right);

    const normalizeOptionList = (field) =>
        ensureArray(field?.options)
            .map((option) => {
                const value = String(option?.value ?? '').trim();
                if (!value) {
                    return null;
                }
                return {
                    value,
                    label: option?.label ?? value,
                    i18n_label: option?.i18n_label ?? null,
                    metadata: option?.metadata ?? null,
                };
            })
            .filter(Boolean);

    const intersectFieldOptions = (baseField, fields) => {
        if (baseField?.type !== 'select') {
            return normalizeOptionList(baseField);
        }
        const optionMaps = fields.map((field) => {
            const map = new Map();
            normalizeOptionList(field).forEach((option) => map.set(option.value, option));
            return map;
        });
        const firstOptions = normalizeOptionList(baseField);
        return firstOptions.filter((option) => optionMaps.every((map) => map.has(option.value)));
    };

    const areFieldsCompatible = (baseField, candidateField) => {
        if (!baseField || !candidateField || baseField.key !== candidateField.key) {
            return false;
        }
        if ((baseField.type || '') !== (candidateField.type || '')) {
            return false;
        }
        if ((baseField.input_type || '') !== (candidateField.input_type || '')) {
            return false;
        }
        if (Boolean(baseField.multiple) !== Boolean(candidateField.multiple)) {
            return false;
        }
        if ((baseField.dependency || '') !== (candidateField.dependency || '')) {
            return false;
        }
        if ((baseField.dependency2 || '') !== (candidateField.dependency2 || '')) {
            return false;
        }
        return true;
    };

    const buildSharedSchemaSections = (schemas) => {
        if (!Array.isArray(schemas) || !schemas.length) {
            return [];
        }
        const normalizedSchemas = schemas.map((schema) => normalizeSchemaSections(schema));
        const baseSections = normalizedSchemas[0] || [];
        const mergedSections = [];

        baseSections.forEach((section) => {
            const sharedFields = [];
            section.fields.forEach((field) => {
                const peerFields = [];
                for (let index = 1; index < normalizedSchemas.length; index += 1) {
                    const peerField = (normalizedSchemas[index] || [])
                        .flatMap((item) => ensureArray(item.fields))
                        .find((candidate) => candidate?.key === field.key);
                    if (!areFieldsCompatible(field, peerField)) {
                        return;
                    }
                    peerFields.push(peerField);
                }
                const allFields = [field, ...peerFields];
                const mergedField = cloneSchemaField(field);
                if (mergedField.type === 'select') {
                    mergedField.options = intersectFieldOptions(field, allFields);
                    if (!mergedField.options.length) {
                        return;
                    }
                }
                sharedFields.push(mergedField);
            });

            const validKeys = new Set(sharedFields.map((field) => field.key));
            const filteredFields = sharedFields.filter((field) => {
                const deps = [field.dependency, field.dependency2].filter(Boolean);
                return deps.every((dependencyKey) => validKeys.has(dependencyKey));
            });

            if (filteredFields.length) {
                mergedSections.push({
                    ...section,
                    fields: filteredFields,
                });
            }
        });

        return mergedSections;
    };

    const buildMixedFieldKeySet = (sections, valuesList) => {
        const mixedKeys = new Set();
        sections.forEach((section) => {
            ensureArray(section.fields).forEach((field) => {
                const firstValue = getNestedValue(valuesList[0], field.key);
                const allSame = valuesList.every((values) => areValuesEqual(getNestedValue(values, field.key), firstValue));
                if (!allSame) {
                    mixedKeys.add(field.key);
                }
            });
        });
        return mixedKeys;
    };

    const createBulkSharedValues = (sections, valuesList, mixedFieldKeys) => {
        const sharedValues = {};
        sections.forEach((section) => {
            ensureArray(section.fields).forEach((field) => {
                if (mixedFieldKeys.has(field.key)) {
                    return;
                }
                const value = getNestedValue(valuesList[0], field.key);
                if (value === undefined) {
                    return;
                }
                setNestedValue(sharedValues, field.key.split('.').filter(Boolean), value);
            });
        });
        return sharedValues;
    };

    const renderEditSchema = (sections, values = {}, options = {}) => {
        const mixedFieldKeys = options.mixedFieldKeys || new Set();
        const bulkMode = Boolean(options.bulkMode);
        if (!dom.editSchemaFields) {
            return;
        }
        dom.editSchemaFields.innerHTML = '';
        editState.schemaControls = [];
        if (dom.editSchemaLoading) {
            dom.editSchemaLoading.hidden = true;
        }
        if (!sections?.length) {
            dom.editSchemaFields.innerHTML = `<p class="provider-form-empty">${escapeHtml(t('provider_edit_schema_empty', 'This provider does not expose additional settings.'))}</p>`;
            return;
        }
        const fragment = document.createDocumentFragment();
        sections.forEach((section) => {
            const sectionEl = document.createElement('section');
            sectionEl.classList.add('settings-section');

            if (section.title || section.description) {
                const headerEl = document.createElement('div');
                headerEl.classList.add('settings-section-header');

                if (section.title) {
                    const titleEl = document.createElement('h3');
                    titleEl.classList.add('settings-section-title');
                    titleEl.textContent = (section.i18n_title && typeof window.getTranslation === 'function')
                        ? window.getTranslation(section.i18n_title, section.title)
                        : section.title;
                    headerEl.appendChild(titleEl);
                }

                if (section.description) {
                    const descEl = document.createElement('p');
                    descEl.classList.add('settings-section-description');
                    descEl.textContent = (section.i18n_description && typeof window.getTranslation === 'function')
                        ? window.getTranslation(section.i18n_description, section.description)
                        : section.description;
                    headerEl.appendChild(descEl);
                }

                sectionEl.appendChild(headerEl);
            }

            const bodyEl = document.createElement('div');
            bodyEl.classList.add('settings-section-body');

            section.fields.forEach((field) => {
                if (!field?.key) {
                    return;
                }
                const value = getNestedValue(values, field.key);
                const { row, control } = createSchemaControl(field, value, {
                    mixed: mixedFieldKeys.has(field.key),
                    bulkMode,
                });
                row.dataset.fieldKey = field.key;
                editState.schemaControls.push({ field, control });
                bodyEl.appendChild(row);
            });

            sectionEl.appendChild(bodyEl);
            fragment.appendChild(sectionEl);
        });
        dom.editSchemaFields.appendChild(fragment);
        // Set up dependency handling
        attachEditDependencyListeners();
        updateEditDependentFieldsVisibility();
        // Attach listeners to clear errors on input
        attachEditErrorClearListeners();
        // Set up websearch provider combined logic
        if (window.WebsearchProviderLogic) {
            window.WebsearchProviderLogic.attachWebsearchProviderLogic(editState.schemaControls);
        }
    };

    const loadEditSchema = async (providerKey, providerId, modelId, values = {}, options = {}) => {
        if (!dom.editSchemaFields) {
            return;
        }
        if (!providerKey || !providerId) {
            renderEditSchema([], {}, options);
            return;
        }
        if (dom.editSchemaLoading) {
            dom.editSchemaLoading.hidden = false;
            setEditSchemaLoadingMessage();
            dom.editSchemaFields.innerHTML = '';
            dom.editSchemaFields.appendChild(dom.editSchemaLoading);
        }
        try {
            const schema = await modelsApi.fetchProviderModelSchema(providerKey, providerId, modelId);
            const sections = normalizeSchemaSections(schema);
            editState.schemaFields = sections;
            renderEditSchema(sections, values, options);
        } catch (error) {
            console.error('Failed to load model schema', error);
            notifyError(error?.message || t('models_configuration_load_failed', 'Failed to load model configuration.'));
            renderEditSchema([], {}, options);
        }
    };

    const collectEditSchemaValues = () => {
        const data = {};
        editState.schemaControls.forEach(({ field, control }) => {
            if (!field?.key || !control) {
                return;
            }
            // Skip hidden fields (dependency not satisfied)
            const row = control.closest?.('.settings-row');
            if (row && row.hidden) return;
            let value;
            switch (field.type) {
                case 'boolean':
                case 'toggle':
                    value = Boolean(control.checked);
                    break;
                case 'select':
                    if (field.multiple) {
                        value = Array.from(control.selectedOptions || []).map((option) => option.value);
                        break;
                    }
                    value = control.value;
                    break;
                case 'number':
                    value = control.value === '' ? null : Number(control.value);
                    break;
                case 'string_list':
                    value = parseMultiLineInput(control.value);
                    break;
                default:
                    value = control.value;
            }
            const segments = field.key.split('.').filter(Boolean);
            if (!segments.length) {
                return;
            }
            value = normalizeSchemaFieldValue(field, value);
            const normalizedSegments = segments[0] === 'settings' ? segments.slice(1) : segments;
            if (!normalizedSegments.length) {
                if (value && typeof value === 'object' && !Array.isArray(value)) {
                    Object.assign(data, value);
                }
                return;
            }
            setNestedValue(data, normalizedSegments, value);
        });
        return data;
    };

    const getEditSnapshot = () => JSON.stringify({
        mode: editState.mode,
        values: collectEditSchemaValues(),
        mixed: Array.from(editState.schemaControls || [])
            .filter(({ control }) => control?.dataset?.mixedValue === 'true')
            .map(({ field }) => field?.key)
            .filter(Boolean),
    });

    const rememberEditSnapshot = () => {
        editState.initialSnapshot = getEditSnapshot();
    };

    const hasUnsavedEditChanges = () => {
        if (state.view !== 'edit' || !editState.initialSnapshot) {
            return false;
        }
        return getEditSnapshot() !== editState.initialSnapshot;
    };

    const requestUnsavedEditConfirmation = (onConfirm) => {
        if (typeof window.unsavedChangesManager?.confirmIfNeeded === 'function') {
            const prompted = window.unsavedChangesManager.confirmIfNeeded({
                id: UNSAVED_GUARD_ID,
                onConfirm,
            });
            if (prompted) {
                return;
            }
        }
        onConfirm?.();
    };

    const splitSchemaValuesByTools = (values) => {
        if (!values || typeof values !== 'object') {
            return { settings: {}, tools: [] };
        }
        const { tools, ...settings } = values;
        const normalizedTools = Array.isArray(tools)
            ? tools.filter((tool) => typeof tool === 'string').map((tool) => tool.trim()).filter(Boolean)
            : typeof tools === 'string'
                ? parseMultiLineInput(tools)
                : [];
        return { settings, tools: normalizedTools };
    };

    const buildSchemaValuePayload = (detail) => {
        if (!detail) {
            return { settings: {}, tools: [] };
        }
        return {
            ...detail,
            settings: detail.settings || {},
            tools: Array.isArray(detail.tools) ? detail.tools : detail.tools || [],
        };
    };

    const populateAccessFields = (access = {}) => {
        if (dom.editAccessEveryone) {
            dom.editAccessEveryone.checked = Boolean(access.everyone);
        }
        if (dom.editAccessUsers) {
            dom.editAccessUsers.value = Array.isArray(access.users) ? access.users.join('\n') : '';
        }
        if (dom.editAccessGroups) {
            dom.editAccessGroups.value = Array.isArray(access.groups) ? access.groups.join('\n') : '';
        }
    };

    const normalizeMultiValue = (value) => {
        if (Array.isArray(value)) {
            return value.map((entry) => String(entry).trim()).filter(Boolean);
        }
        if (typeof value === 'string') {
            return parseMultiLineInput(value);
        }
        return [];
    };

    const buildAccessPayload = (accessValues) => {
        const access = (typeof accessValues === 'object' && accessValues !== null) ? accessValues : {};
        return {
            everyone: Boolean(access.everyone),
            users: normalizeMultiValue(access.users),
            groups: normalizeMultiValue(access.groups),
        };
    };

    const setLoading = (value) => {
        state.loading = Boolean(value);
        dom.page.classList.toggle('is-loading', state.loading);
    };

    const renderModelsLoadingState = (message = t('models_loading', 'Loading models…')) => {
        dom.list.innerHTML = '';
        const loadingState = window.createAdminLoadingPlaceholder({
            message,
            className: '',
        });
        dom.list.appendChild(loadingState);
    };

    const renderEmptyState = (
        message = t('models_empty_title', 'No models yet'),
        description = t('models_empty_desc', 'Create a model to make it available across the platform.')
    ) => {
        dom.list.innerHTML = '';
        const empty = window.createAdminEmptyPlaceholder({
            title: message,
            description,
            icon: Icons?.omlorix || '',
            className: 'provider-empty-state',
        });
        dom.list.appendChild(empty);
    };

    const filterModels = () => {
        const providerFilter = dom.providerFilter?.value || 'all';
        const term = (dom.search?.value || '').trim().toLowerCase();

        return state.models.filter((model) => {
            if (providerFilter !== 'all' && model.provider !== providerFilter) {
                return false;
            }
            if (!term) {
                return true;
            }
            const haystacks = [model.name, model.description, model.model_name, model.provider_name, model.provider]
                .map((value) => (value || '').toLowerCase());
            return haystacks.some((value) => value.includes(term));
        });
    };

    const renderModels = () => {
        const filtered = filterModels();
        dom.list.innerHTML = '';

        if (!filtered.length) {
            const hasFilters = (dom.providerFilter?.value || 'all') !== 'all' || Boolean(dom.search?.value?.trim());
            renderEmptyState(
                hasFilters ? t('models_empty_filtered', 'No models match your filters') : t('models_empty_title', 'No models yet'),
                hasFilters ? '' : t('models_empty_desc', 'Create a model to make it available across the platform.')
            );
            return;
        }

        const header = document.createElement('div');
        header.className = 'provider-table-header model-table-header';
        if (state.bulkSelectionMode) {
            header.classList.add('bulk-select-enabled');
        }
        const headerCells = [
            ...(state.bulkSelectionMode ? [{ className: 'header-select', text: '' }] : []),
            { className: 'header-icon', text: t('table_header_icon', 'Icon') },
            { className: 'header-provider', text: t('table_header_provider', 'Provider') },
            { className: 'header-custom', text: t('table_header_model', 'Model') },
            { className: 'header-actions', text: t('table_header_actions', 'Actions') },
        ];
        headerCells.forEach(({ className, text }) => {
            const cell = document.createElement('div');
            cell.className = className;
            if (className === 'header-select') {
                const selectAll = document.createElement('input');
                selectAll.type = 'checkbox';
                selectAll.className = 'model-select-checkbox';
                selectAll.setAttribute('aria-label', t('models_bulk_select_all', 'Select all models'));
                selectAll.checked = filtered.length > 0 && filtered.every((model) => state.selectedModelIds.has(model.id));
                selectAll.indeterminate = filtered.some((model) => state.selectedModelIds.has(model.id)) && !selectAll.checked;
                selectAll.addEventListener('click', (event) => event.stopPropagation());
                selectAll.addEventListener('change', () => {
                    if (selectAll.checked) {
                        filtered.forEach((model) => state.selectedModelIds.add(model.id));
                    } else {
                        filtered.forEach((model) => state.selectedModelIds.delete(model.id));
                    }
                    updateBulkUiState();
                    renderModels();
                });
                cell.appendChild(selectAll);
            } else {
                cell.textContent = text;
            }
            header.appendChild(cell);
        });
        dom.list.appendChild(header);

        const fragment = document.createDocumentFragment();
        filtered.forEach((model) => {
            const row = document.createElement('div');
            row.className = 'provider-row model-row';
            row.dataset.modelId = model.id;
            const modelIdentifyingName = model.name || model.model_name || t('models_unnamed', 'Unnamed model');
            if (state.bulkSelectionMode) {
                row.classList.add('bulk-select-enabled');
            }
            if (state.selectedModelIds.has(model.id)) {
                row.classList.add('is-selected');
            }

            if (state.bulkSelectionMode) {
                const selectCell = document.createElement('div');
                selectCell.className = 'model-select-cell';
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.className = 'model-select-checkbox';
                checkbox.setAttribute('aria-label', formatT(
                    'models_bulk_select_row',
                    'Select model {name}',
                    { name: modelIdentifyingName }
                ));
                checkbox.checked = state.selectedModelIds.has(model.id);
                checkbox.addEventListener('click', (event) => event.stopPropagation());
                checkbox.addEventListener('change', () => {
                    if (checkbox.checked) {
                        state.selectedModelIds.add(model.id);
                    } else {
                        state.selectedModelIds.delete(model.id);
                    }
                    updateBulkUiState();
                    renderModels();
                });
                selectCell.appendChild(checkbox);
                row.appendChild(selectCell);
            }

            const iconCell = document.createElement('div');
            iconCell.className = 'provider-icon';
            iconCell.setAttribute('aria-hidden', 'true');
            iconCell.innerHTML = resolveModelIconMarkup(model);

            const providerCell = document.createElement('div');
            providerCell.className = 'provider-name';
            providerCell.dataset.label = t('table_header_provider', 'Provider');
            providerCell.textContent = model.provider_name || formatProviderType(model.provider);

            const nameCell = document.createElement('div');
            nameCell.className = 'provider-custom';
            nameCell.dataset.label = t('table_header_model', 'Model');

            const nameRow = document.createElement('div');
            nameRow.className = 'model-name-row';

            const name = document.createElement('div');
            name.className = 'model-name';
            name.textContent = modelIdentifyingName;
            nameRow.appendChild(name);

            if (model.increased_errors) {
                const warningBadge = document.createElement('span');
                warningBadge.className = 'model-error-indicator';
                warningBadge.setAttribute('role', 'status');
                warningBadge.setAttribute('aria-label', t('models_error_rate_elevated', 'Elevated error rate'));
                warningBadge.innerHTML = `
                    <div class="model-error-indicator-content">
                        ${Icons.warning}
                        <span class="model-error-indicator-text">${t('models_error_rate_elevated', 'Elevated error rate')}</span>
                    </div>
                `;
                nameRow.appendChild(warningBadge);
            }

            // A provider's display name and API model name are often identical. Avoid
            // repeating the same value on mobile cards (and desktop rows), while still
            // retaining genuinely useful descriptions or differing API model names.
            const metaText = (model.description || model.model_name || '').trim();
            const primaryName = name.textContent.trim();
            nameCell.appendChild(nameRow);
            if (metaText && metaText.localeCompare(primaryName, undefined, { sensitivity: 'accent' }) !== 0) {
                const meta = document.createElement('div');
                meta.className = 'model-meta';
                meta.textContent = metaText;
                meta.title = metaText;
                nameCell.appendChild(meta);
            }

            const actionsCell = document.createElement('div');
            actionsCell.className = 'provider-actions model-actions';
            actionsCell.dataset.label = t('table_header_actions', 'Actions');

            const primaryActions = document.createElement('div');
            primaryActions.className = 'model-actions-primary';

            const editButton = document.createElement('button');
            editButton.type = 'button';
            editButton.className = 'action-btn edit-btn model-edit-btn';
            editButton.title = t('models_edit_title', 'Edit model');
            editButton.setAttribute('aria-label', editButton.title);
            editButton.innerHTML = Icons?.edit;
            editButton.addEventListener('click', (event) => {
                event.stopPropagation();
                openModelEdit(model.id);
            });
            primaryActions.appendChild(editButton);

            const deleteButton = document.createElement('button');
            deleteButton.type = 'button';
            deleteButton.className = 'action-btn delete-btn model-delete-btn';
            deleteButton.title = t('models_delete_title', 'Delete model');
            deleteButton.setAttribute('aria-label', deleteButton.title);
            deleteButton.innerHTML = Icons?.trash || '';
            deleteButton.addEventListener('click', (event) => {
                event.stopPropagation();
                openDeleteModelModal(model);
            });
            primaryActions.appendChild(deleteButton);

            const menuContainer = document.createElement('div');
            menuContainer.className = 'model-actions-menu';

            const menuToggle = document.createElement('button');
            menuToggle.type = 'button';
            menuToggle.className = 'action-btn icon-only model-menu-toggle';
            menuToggle.setAttribute('aria-haspopup', 'true');
            menuToggle.setAttribute('aria-expanded', 'false');
            menuToggle.setAttribute('aria-label', t('table_header_actions', 'Actions'));
            menuToggle.innerHTML = Icons.ellipsisVertical;

            const dropdown = document.createElement('div');
            dropdown.className = 'model-menu-dropdown';
            dropdown.innerHTML = `
                <button type="button" class="dropdown-item" data-action="duplicate" data-model-id="${model.id}">
                    ${Icons?.copy}
                    <span>${t('models_duplicate_btn', 'Duplicate')}</span>
                </button>
            `;

            menuToggle.addEventListener('click', (event) => {
                event.stopPropagation();
                ensureActionMenuListeners();
                openActionMenu(menuContainer, menuToggle);
            });

            dropdown.addEventListener('click', (event) => {
                const actionButton = event.target.closest('.dropdown-item');
                if (!actionButton) {
                    return;
                }
                const action = actionButton.dataset.action;
                const targetModelId = actionButton.dataset.modelId;
                closeActionMenu();
                if (action === 'duplicate' && targetModelId) {
                    duplicateModel(targetModelId, actionButton);
                }
            });

            menuContainer.append(menuToggle, dropdown);

            actionsCell.append(primaryActions, menuContainer);

            row.append(iconCell, providerCell, nameCell, actionsCell);
            fragment.appendChild(row);
        });

        dom.list.appendChild(fragment);
    };

    const populateProviderFilter = (models) => {
        if (!dom.providerFilter) {
            return;
        }
        const providerTypes = [...new Set(models.map((model) => model.provider).filter(Boolean))].sort();
        const previous = dom.providerFilter.value || 'all';
        dom.providerFilter.innerHTML = ['all', ...providerTypes]
            .map((provider) => `<option value="${provider}">${provider === 'all' ? t('providers_filter_all', 'All providers') : formatProviderType(provider)}</option>`)
            .join('');
        dom.providerFilter.value = providerTypes.includes(previous) ? previous : 'all';
        upgradeModelProviderFilterSelect();
    };

    const upgradeModelProviderFilterSelect = () => {
        window.upgradeAdminSingleSelect?.(dom.providerFilter, {
            key: 'models-filter',
            placeholder: t('providers_filter_all', 'All providers')
        });
    };

    const loadModels = async () => {
        setLoading(true);
        renderModelsLoadingState(t('models_loading', 'Loading models…'));
        try {
            const models = await modelsApi.fetchAdminModels();
            state.models = Array.isArray(models) ? models.filter(isManagedBaseModel) : [];
        } catch (error) {
            console.error('Failed to fetch models', error);
            state.models = [];
            renderEmptyState(t('models_load_failed', 'Failed to load models'), error?.message || t('provider_groups_load_failed_text', 'Please try refreshing the page.'));
            return;
        } finally {
            setLoading(false);
        }
        const validIds = new Set(state.models.map((model) => model.id));
        [...state.selectedModelIds].forEach((modelId) => {
            if (!validIds.has(modelId)) {
                state.selectedModelIds.delete(modelId);
            }
        });
        populateProviderFilter(state.models);
        updateBulkUiState();
        renderModels();
    };

    const handleCreateClick = async () => {
        if (typeof window.startModelsCreateFlow === 'function') {
            window.startModelsCreateFlow();
        }
    };

    const showEditView = () => {
        showView('edit');
    };

    const populateEditForm = (detail) => {
        if (!detail) {
            return;
        }
        if (dom.editFormTitle) {
            dom.editFormTitle.textContent = formatT('models_edit_form_title', 'Edit {name}', { name: detail.name || t('common_model', 'Model') });
        }
        if (dom.editFormSubtitle) {
            const providerLabel = detail.provider_name || formatProviderType(detail.provider);
            dom.editFormSubtitle.textContent = formatT('models_edit_form_subtitle', 'Provider: {provider}', { provider: providerLabel });
        }
        if (dom.editName) {
            dom.editName.value = detail.name || '';
        }
        if (dom.editModelId) {
            dom.editModelId.value = detail.model_name || '';
        }
        if (dom.editDescription) {
            dom.editDescription.value = detail.description || '';
        }
        if (dom.editIcon) {
            dom.editIcon.value = detail.model_icon || '';
        }
        if (dom.editStatus) {
            dom.editStatus.value = detail.status || 'normal';
        }
        populateAccessFields(detail.access || {});
    };

    const setEditBanner = (title, body) => {
        if (!dom.editBanner) {
            return;
        }
        if (!title && !body) {
            dom.editBanner.hidden = true;
            dom.editBanner.replaceChildren();
            return;
        }
        const fragment = document.createDocumentFragment();
        if (title) {
            const strong = document.createElement('strong');
            strong.textContent = title;
            fragment.appendChild(strong);
        }
        if (body) {
            fragment.appendChild(document.createTextNode(body));
        }
        dom.editBanner.hidden = false;
        dom.editBanner.replaceChildren(fragment);
    };

    const openModelEdit = async (modelId) => {
        if (!modelId || !dom.editPage) {
            return;
        }
        const detail = state.models.find((m) => m.id === modelId || m.model_id === modelId);
        if (!detail) {
            notifyError(t('models_not_found_refresh', 'Model not found. Please refresh the list.'));
            return;
        }
        try {
            showEditView();
            editState.loading = true;
            if (dom.editSubmitButton) {
                setButtonLoadingState(dom.editSubmitButton, false);
                if (typeof setButtonLabel === 'function') {
                    setButtonLabel(dom.editSubmitButton, t('btn_save_changes', 'Save Changes'));
                }
            }
            resetEditForm();
            editState.modelId = modelId;
            editState.modelIds = [modelId];
            editState.mode = 'single';
            editState.detail = detail;
            editState.details = [detail];
            editState.providerKey = detail.provider;
            populateEditForm(detail);
            setEditBanner('', '');
            await loadEditSchema(detail.provider, detail.provider_id, modelId, buildSchemaValuePayload(detail));
            rememberEditSnapshot();
            if (dom.editSubmitButton) {
                dom.editSubmitButton.disabled = false;
            }
        } catch (error) {
            console.error('Failed to open model for editing', error);
            notifyError(error?.message || t('models_single_load_failed', 'Failed to load model.'));
            goToListView();
        } finally {
            editState.loading = false;
        }
    };

    const openBulkEdit = async () => {
        if (!dom.editPage) {
            return;
        }
        const details = getSelectedModels();
        if (details.length < 2) {
            notifyError(t('models_bulk_min_selection', 'Select at least two models to bulk edit.'));
            return;
        }

        showEditView();
        editState.loading = true;
        resetEditForm();
        editState.mode = 'bulk';
        editState.modelIds = details.map((detail) => detail.id);
        editState.details = details;
        editState.detail = null;

        if (dom.editFormTitle) {
            dom.editFormTitle.textContent = formatT('models_bulk_edit_title', 'Bulk Edit {count} Models', {
                count: details.length,
            });
        }
        if (dom.editFormSubtitle) {
            dom.editFormSubtitle.textContent = t('models_bulk_edit_subtitle', 'Only fields shared by all selected models can be edited.');
        }
        setEditBanner(
            formatT('models_bulk_edit_selected', '{count} models selected', {
                count: details.length,
            }),
            t('models_bulk_edit_mixed_desc', "Fields with different current values are shown as mixed. Leaving them untouched keeps each model's existing value.")
        );

        try {
            if (dom.editSubmitButton) {
                setButtonLoadingState(dom.editSubmitButton, false);
                if (typeof setButtonLabel === 'function') {
                    setButtonLabel(dom.editSubmitButton, formatT('models_bulk_edit_submit', 'Update {count} Models', {
                        count: details.length,
                    }));
                }
            }
            if (dom.editSchemaLoading) {
                dom.editSchemaLoading.hidden = false;
                dom.editSchemaFields.innerHTML = '';
                setEditSchemaLoadingMessage(getBulkSchemaLoadingStatus(1, details.length, getModelDisplayLabel(details[0])));
                dom.editSchemaFields.appendChild(dom.editSchemaLoading);
            }

            const schemaPayloads = [];
            for (let index = 0; index < details.length; index += 1) {
                const detail = details[index];
                if (dom.editSchemaLoading) {
                    setEditSchemaLoadingMessage(
                        getBulkSchemaLoadingStatus(index + 1, details.length, getModelDisplayLabel(detail))
                    );
                }
                const schemaPayload = await modelsApi.fetchProviderModelSchema(detail.provider, detail.provider_id, detail.id);
                schemaPayloads.push(schemaPayload);
            }
            const sharedSections = buildSharedSchemaSections(schemaPayloads);
            const valuesList = details.map((detail) => buildSchemaValuePayload(detail));
            const mixedFieldKeys = buildMixedFieldKeySet(sharedSections, valuesList);
            const sharedValues = createBulkSharedValues(sharedSections, valuesList, mixedFieldKeys);

            editState.schemaFields = sharedSections;
            editState.mixedFieldKeys = mixedFieldKeys;
            renderEditSchema(sharedSections, sharedValues, {
                mixedFieldKeys,
                bulkMode: true,
            });
            rememberEditSnapshot();
            if (dom.editSubmitButton) {
                dom.editSubmitButton.disabled = !sharedSections.length;
            }

            if (!sharedSections.length) {
                setEditBanner(
                    formatT('models_bulk_edit_selected', '{count} models selected', {
                        count: details.length,
                    }),
                    t('models_bulk_edit_no_shared_fields', 'These models do not share editable schema fields. Choose a more similar set of models to bulk edit.')
                );
            }
        } catch (error) {
            console.error('Failed to open bulk edit', error);
            notifyError(error?.message || t('models_bulk_edit_load_failed', 'Failed to load bulk edit form.'));
            goToListView();
        } finally {
            setEditSchemaLoadingMessage();
            editState.loading = false;
        }
    };

    const handleDeleteModalKeydown = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeDeleteModelModal();
        }
    };

    const openDeleteModelModal = (model) => {
        if (!dom.deleteOverlay) {
            return;
        }
        const bulkTargets = Array.isArray(model)
            ? model
                .map((entry) => (entry && typeof entry === 'object' ? entry : null))
                .filter(Boolean)
            : [];
        if (bulkTargets.length) {
            state.deleteTargets = bulkTargets;
            state.deleteTarget = null;
        } else {
            state.deleteTarget = model;
            state.deleteTargets = [];
        }
        if (dom.deleteMessage) {
            const rateLimitImpact = t(
                'models_delete_rate_limits_impact',
                'Each deleted model will also be removed from all usage limits; limits left without any models will be deleted.'
            );
            let confirmationMessage = '';
            if (bulkTargets.length) {
                const count = bulkTargets.length;
                confirmationMessage = count === 1
                    ? t('models_delete_confirm_bulk_one', 'Are you sure you want to delete 1 selected model? This will also remove all associated usage statistics.')
                    : formatT('models_delete_confirm_bulk_other', 'Are you sure you want to delete {count} selected models? This will also remove all associated usage statistics.', { count });
            } else {
                const label = model?.name || model?.model_name || t('models_delete_subject_fallback', 'this model');
                confirmationMessage = formatT(
                    'models_delete_confirm_named',
                    'Are you sure you want to delete "{name}"? This will also remove all associated usage statistics.',
                    { name: label }
                );
            }
            dom.deleteMessage.textContent = `${confirmationMessage} ${rateLimitImpact}`;
        }
        if (dom.deleteConfirmText) {
            dom.deleteConfirmText.textContent = bulkTargets.length
                ? t('models_delete_bulk_btn', 'Delete Selected')
                : t('models_delete_btn', 'Delete Model');
        }
        dom.deleteConfirm?.removeAttribute('disabled');
        dom.deleteOverlay.hidden = false;
        dom.deleteOverlay.classList.add('active');
        dom.deleteConfirm?.focus();
        document.addEventListener('keydown', handleDeleteModalKeydown, true);
    };

    const closeDeleteModelModal = () => {
        document.removeEventListener('keydown', handleDeleteModalKeydown, true);
        if (dom.deleteOverlay) {
            dom.deleteOverlay.classList.remove('active');
            dom.deleteOverlay.hidden = true;
        }
        if (dom.deleteMessage) {
            const confirmationMessage = t('models_delete_confirm_default', 'Are you sure you want to delete this model? This will also remove all associated usage statistics.');
            const rateLimitImpact = t(
                'models_delete_rate_limits_impact',
                'Each deleted model will also be removed from all usage limits; limits left without any models will be deleted.'
            );
            dom.deleteMessage.textContent = `${confirmationMessage} ${rateLimitImpact}`;
        }
        if (dom.deleteConfirmText) {
            dom.deleteConfirmText.textContent = t('models_delete_btn', 'Delete Model');
        }
        dom.deleteConfirm?.removeAttribute('disabled');
        state.deleteTarget = null;
        state.deleteTargets = [];
    };

    const deleteCurrentModel = async () => {
        const targetIds = state.deleteTargets.length
            ? state.deleteTargets.map((model) => model?.id).filter(Boolean)
            : (state.deleteTarget?.id ? [state.deleteTarget.id] : []);
        if (!targetIds.length) {
            closeDeleteModelModal();
            return;
        }
        const isBulkDelete = targetIds.length > 1;

        const originalConfirmIconHtml = dom.deleteConfirm?.querySelector('svg')?.outerHTML || '';
        const restoreConfirmIcon = () => {
            if (!dom.deleteConfirm || !originalConfirmIconHtml) {
                return;
            }
            const currentIcon = dom.deleteConfirm.querySelector('svg');
            if (currentIcon) {
                currentIcon.outerHTML = originalConfirmIconHtml;
                return;
            }
            dom.deleteConfirm.insertAdjacentHTML('afterbegin', originalConfirmIconHtml);
        };

        try {
            dom.deleteConfirm?.setAttribute('disabled', 'true');
            if (dom.deleteConfirmText) {
                dom.deleteConfirmText.textContent = isBulkDelete
                    ? t('models_deleting_bulk_ellipsis', 'Deleting selected models...')
                    : t('admin_deleting_ellipsis', 'Deleting...');
            }

            const confirmIcon = dom.deleteConfirm?.querySelector('svg');
            if (confirmIcon) {
                confirmIcon.outerHTML = Icons.refresh;
            }

            const deletedIds = [];
            const failedDeletions = [];
            for (const modelId of targetIds) {
                const response = await modelsApi.deleteModel(modelId);
                if (response.ok) {
                    deletedIds.push(modelId);
                } else {
                    const error = await modelsApi.buildResponseError(response, t('models_delete_failed', 'Failed to delete model.'));
                    failedDeletions.push({ id: modelId, error });
                }
            }

            if (failedDeletions.length === 0) {
                notifySuccess(
                    isBulkDelete
                        ? (deletedIds.length === 1
                            ? t('models_delete_bulk_success_one', '1 model deleted successfully.')
                            : formatT('models_delete_bulk_success_other', '{count} models deleted successfully.', { count: deletedIds.length }))
                        : t('models_delete_success', 'Model deleted successfully.')
                );
            } else if (deletedIds.length === 0) {
                const genericFailureMessage = isBulkDelete
                    ? t('models_delete_bulk_failed', 'Failed to delete selected models.')
                    : t('models_delete_failed', 'Failed to delete model.');
                notifyError(summarizeModelDeleteFailures(failedDeletions, genericFailureMessage));
            } else {
                const successMsg = isBulkDelete
                    ? (deletedIds.length === 1
                        ? t('models_delete_bulk_success_one', '1 model deleted successfully.')
                        : formatT('models_delete_bulk_success_other', '{count} models deleted successfully.', { count: deletedIds.length }))
                    : t('models_delete_success', 'Model deleted successfully.');
                const genericFailureMessage = isBulkDelete
                    ? formatT('models_delete_bulk_partial_failed', '{count} model(s) failed to delete.', { count: failedDeletions.length })
                    : t('models_delete_failed', 'Failed to delete model.');
                const failureMsg = summarizeModelDeleteFailures(failedDeletions, genericFailureMessage);
                notifyError(`${successMsg} ${failureMsg}`);
                console.error('Failed deletions:', failedDeletions);
            }

            await loadModels();
            modelsSettingsController.reload();
            if (isBulkDelete) {
                clearBulkSelection({ preserveMode: true });
                updateBulkUiState();
            }
            restoreConfirmIcon();
            closeDeleteModelModal();
        } catch (error) {
            console.error('Failed to delete model(s)', error);
            notifyError(getLocalizedModelDeleteErrorMessage(
                error,
                isBulkDelete
                    ? t('models_delete_bulk_failed', 'Failed to delete selected models.')
                    : t('models_delete_failed', 'Failed to delete model.'),
            ));
            dom.deleteConfirm?.removeAttribute('disabled');
            restoreConfirmIcon();
            if (dom.deleteConfirmText) {
                dom.deleteConfirmText.textContent = isBulkDelete
                    ? t('models_delete_bulk_btn', 'Delete Selected')
                    : t('models_delete_btn', 'Delete Model');
            }
        }
    };

    const openBulkDelete = () => {
        const selectedModels = getSelectedModels();
        if (!selectedModels.length) {
            notifyError(t('models_bulk_delete_min_selection', 'Select at least one model to delete.'));
            return;
        }
        openDeleteModelModal(selectedModels);
    };

    const setActionButtonBusy = (button, isBusy, busyLabel = 'Working…') => {
        if (!button) {
            return;
        }
        if (isBusy) {
            button.dataset.originalTitle = button.dataset.originalTitle || button.title || '';
            button.title = busyLabel;
            button.classList.add('is-busy');
            button.disabled = true;
            button.setAttribute('aria-busy', 'true');
            return;
        }
        button.disabled = false;
        button.classList.remove('is-busy');
        button.removeAttribute('aria-busy');
        if (button.dataset.originalTitle !== undefined) {
            button.title = button.dataset.originalTitle;
            delete button.dataset.originalTitle;
        }
    };

    const duplicateModel = async (modelId, triggerButton) => {
        if (!modelId) {
            notifyError(t('models_duplicate_unavailable', 'Unable to duplicate this model.'));
            return;
        }
        try {
            setActionButtonBusy(triggerButton, true, t('admin_duplicating_ellipsis', 'Duplicating...'));
            const response = await modelsApi.duplicateModel(modelId);
            if (!response.ok) {
                throw await modelsApi.buildResponseError(response, t('models_duplicate_failed', 'Failed to duplicate model.'));
            }
            notifySuccess(t('models_duplicate_success', 'Model duplicated successfully.'));
            await loadModels();
        } catch (error) {
            console.error('Failed to duplicate model', error);
            notifyError(error?.message || t('models_duplicate_failed', 'Failed to duplicate model.'));
        } finally {
            setActionButtonBusy(triggerButton, false);
        }
    };

    const setButtonLoadingState = (button, isLoading, loadingLabel = t('admin_loading_ellipsis', 'Loading...')) => {
        if (!button) {
            return;
        }
        const labelTarget = button.querySelector('span');
        const getLabel = () => (labelTarget ? labelTarget.textContent : button.textContent);
        const setLabel = (value) => {
            if (labelTarget) {
                labelTarget.textContent = value;
            } else {
                button.textContent = value;
            }
        };

        if (isLoading) {
            if (!button.dataset.originalLabel) {
                button.dataset.originalLabel = getLabel()?.trim() || '';
            }
            button.disabled = true;
            button.classList.add('loading');
            button.setAttribute('aria-busy', 'true');
            setLabel(loadingLabel);
        } else {
            button.disabled = false;
            button.classList.remove('loading');
            button.removeAttribute('aria-busy');
            if (button.dataset.originalLabel !== undefined) {
                setLabel(button.dataset.originalLabel || '');
                delete button.dataset.originalLabel;
            }
        }
    };

    const resolveModelsFromPayload = (payload) => {
        if (!payload || typeof payload !== 'object') {
            notifyError(t('models_import_invalid_export', 'Invalid export file.'));
            return null;
        }
        if (payload.export_type !== 'llm_model') {
            notifyError(t('models_import_unsupported_type', 'Unsupported export file type.'));
            return null;
        }
        if (payload.export_version !== MODELS_EXPORT_VERSION) {
            notifyError(t('models_import_version_mismatch', 'Unsupported export version. Expected 1.0.'));
            return null;
        }
        const models = payload?.data?.models;
        return Array.isArray(models) ? models : [];
    };

    const setImportStatus = (message = '', type = '') => {
        if (!dom.importStatus) {
            return;
        }
        if (!message) {
            dom.importStatus.hidden = true;
            dom.importStatus.textContent = '';
            dom.importStatus.className = 'provider-import-status';
            return;
        }
        dom.importStatus.hidden = false;
        dom.importStatus.textContent = message;
        dom.importStatus.className = `provider-import-status ${type}`.trim();
    };

    const handleImportModalKeydown = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeImportModal();
        }
    };

    const openImportModal = () => {
        if (!dom.importOverlay) {
            return;
        }
        dom.importOverlay.hidden = false;
        dom.importOverlay.classList.add('active');
        if (dom.importFileName) {
            dom.importFileName.textContent = importState.fileName || '';
        }
        if (dom.importSelectAll) {
            dom.importSelectAll.checked = importState.models.length === importState.selected.size;
        }
        setImportStatus();
        dom.importConfirm?.focus();
        document.addEventListener('keydown', handleImportModalKeydown, true);
    };

    const closeImportModal = () => {
        document.removeEventListener('keydown', handleImportModalKeydown, true);
        dom.importOverlay?.classList.remove('active');
        if (dom.importOverlay) {
            dom.importOverlay.hidden = true;
        }
        importState.payload = null;
        importState.models = [];
        importState.selected = new Set();
        importState.fileName = '';
        if (dom.importList) {
            dom.importList.innerHTML = '';
        }
        if (dom.importFileName) {
            dom.importFileName.textContent = '';
        }
        if (dom.importSelectAll) {
            dom.importSelectAll.checked = false;
        }
        setImportStatus();
    };

    const renderImportList = () => {
        if (!dom.importList) {
            return;
        }
        dom.importList.innerHTML = '';

        const { models: importModels, selected } = importState;
        if (!importModels.length) {
            const empty = document.createElement('div');
            empty.className = 'provider-import-empty';
            empty.textContent = t('models_import_empty', 'No models found in this file.');
            dom.importList.appendChild(empty);
            return;
        }

        const fragment = document.createDocumentFragment();

        importModels.forEach((model, index) => {
            const entry = document.createElement('label');
            entry.className = 'provider-import-entry';
            entry.setAttribute('role', 'option');
            entry.setAttribute('aria-selected', selected.has(index) ? 'true' : 'false');

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = selected.has(index);
            checkbox.dataset.modelIndex = String(index);
            checkbox.addEventListener('change', (event) => {
                const target = event.currentTarget;
                const idx = Number.parseInt(target.dataset.modelIndex, 10);
                if (Number.isNaN(idx)) {
                    return;
                }
                if (target.checked) {
                    importState.selected.add(idx);
                } else {
                    importState.selected.delete(idx);
                }
                target.closest('.provider-import-entry')?.setAttribute('aria-selected', target.checked ? 'true' : 'false');
                if (dom.importSelectAll) {
                    dom.importSelectAll.checked = importState.selected.size === importState.models.length;
                }
                setImportStatus();
            });
            entry.appendChild(checkbox);

            const content = document.createElement('div');
            content.className = 'provider-import-entry-content';

            const title = document.createElement('p');
            title.className = 'provider-import-entry-title';
            title.textContent = model?.name || t('models_import_unnamed', '(Unnamed model)');
            content.appendChild(title);

            const meta = document.createElement('div');
            meta.className = 'provider-import-entry-meta';
            const providerLabel = model?.provider_name || formatProviderType(model?.provider) || t('common_unknown', 'Unknown');
            const providerMeta = document.createElement('span');
            providerMeta.textContent = formatT('models_import_provider_meta', 'Provider: {provider}', { provider: providerLabel });
            meta.appendChild(providerMeta);
            if (model?.status) {
                const statusMeta = document.createElement('span');
                statusMeta.textContent = formatT('models_import_status_meta', 'Status: {status}', { status: model.status });
                meta.appendChild(statusMeta);
            }
            content.appendChild(meta);

            if (Array.isArray(model?.capabilities) && model.capabilities.length) {
                const caps = document.createElement('div');
                caps.className = 'provider-import-entry-meta';
                caps.textContent = formatT('models_import_capabilities_meta', 'Capabilities: {capabilities}', {
                    capabilities: model.capabilities.slice(0, 3).join(', ')
                });
                content.appendChild(caps);
            }

            if (model?.description) {
                const description = document.createElement('p');
                description.className = 'provider-import-entry-meta';
                description.textContent = model.description;
                content.appendChild(description);
            }

            entry.appendChild(content);
            fragment.appendChild(entry);
        });

        dom.importList.appendChild(fragment);
    };

    const toggleSelectAllImports = (event) => {
        const { checked } = event.currentTarget;
        importState.selected.clear();
        if (checked) {
            importState.models.forEach((_, idx) => importState.selected.add(idx));
        }
        renderImportList();
        setImportStatus();
    };

    const submitSelectedImports = async () => {
        const { payload, selected, models: importModels } = importState;
        if (!payload) {
            setImportStatus(t('models_import_choose_file_first', 'Please choose a model file first.'), '');
            return;
        }
        if (!selected.size) {
            setImportStatus(t('models_import_select_one', 'Select at least one model to import.'), '');
            return;
        }

        try {
            setButtonLoadingState(dom.importConfirm, true, t('admin_importing_ellipsis', 'Importing...'));
            const indices = Array.from(selected).sort((a, b) => a - b);
            const filteredModels = indices.map((index) => importModels[index]).filter(Boolean);

            const filteredPayload = {
                ...payload,
                data: {
                    ...(payload.data || {}),
                    models: filteredModels,
                },
            };

            const response = await modelsApi.importModels(filteredPayload);
            if (!response.ok) {
                throw await modelsApi.buildResponseError(response, t('models_import_failed', 'Failed to import models.'));
            }

            const result = await response.json();
            const createdCount = result?.created?.length || 0;
            const errorCount = result?.errors?.length || 0;

            if (createdCount) {
                const successMessage = formatT(
                    createdCount === 1 ? 'models_import_success_single' : 'models_import_success_plural',
                    createdCount === 1 ? 'Imported {count} model successfully.' : 'Imported {count} models successfully.',
                    { count: createdCount }
                );
                notifySuccess(successMessage);
                setImportStatus(successMessage, 'success');
            }

            if (errorCount) {
                const formattedErrors = Array.isArray(result?.errors)
                    ? result.errors
                        .map((entry) => {
                            if (!entry || typeof entry !== 'object') {
                                return '';
                            }
                            const displayIndex = entry.index === undefined ? '?' : Number(entry.index) + 1;
                            const name = entry.name ? ` (${entry.name})` : '';
                            const detail = entry.error ? JSON.stringify(entry.error) : t('common_unknown_error', 'Unknown error');
                            return `• Item ${displayIndex}${name}: ${detail}`;
                        })
                        .filter(Boolean)
                    : [];
                const summary = formatT(
                    errorCount === 1 ? 'models_import_issues_single' : 'models_import_issues_plural',
                    errorCount === 1 ? '{count} model has issues.' : '{count} models have issues.',
                    { count: errorCount }
                );
                const details = formattedErrors.length ? `\n${formattedErrors.join('\n')}` : '';
                const warning = `${summary} ${t('models_import_check_file', 'Check the import file.')}${details}`;
                setImportStatus(warning, '');
                notifyWarning(warning);
            }

            await loadModels();

            if (!errorCount) {
                closeImportModal();
            }
        } catch (error) {
            console.error('Failed to import models', error);
            setImportStatus(error?.message || t('models_import_failed', 'Failed to import models.'), '');
            notifyError(error?.message || t('models_import_failed', 'Failed to import models.'));
        } finally {
            setButtonLoadingState(dom.importConfirm, false);
        }
    };

    const handleExportModels = async () => {
        try {
            setButtonLoadingState(dom.exportButton, true, t('admin_exporting_ellipsis', 'Exporting...'));
            const response = await modelsApi.exportModels();
            if (!response.ok) {
                throw await modelsApi.buildResponseError(response, t('models_export_failed', 'Failed to export models.'));
            }

            const exportData = await response.json();
            const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
            const timestamp = new Date().toISOString().replace(/[:\.]/g, '-');
            const filename = `llm-models-${timestamp}.json`;

            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(link.href);

            notifySuccess(t('models_export_success', 'Model export downloaded successfully.'));
        } catch (error) {
            console.error('Failed to export models', error);
            notifyError(error?.message || t('models_export_failed', 'Failed to export models.'));
        } finally {
            setButtonLoadingState(dom.exportButton, false);
        }
    };

    const handleImportModels = async (event) => {
        const input = event?.target;
        if (!input?.files?.length) {
            return;
        }

        const [file] = input.files;
        input.value = '';

        const isJson = file && (file.type === 'application/json' || file.name?.toLowerCase().endsWith('.json'));
        if (!isJson) {
            notifyError(t('models_import_select_json', 'Please select a valid JSON file.'));
            return;
        }

        try {
            const fileContent = await file.text();
            let payload;
            try {
                payload = JSON.parse(fileContent);
            } catch (parseError) {
                notifyError(t('models_import_invalid_json', 'Invalid JSON file.'));
                return;
            }

            const modelsToImport = resolveModelsFromPayload(payload);
            if (modelsToImport === null) {
                return;
            }
            if (!modelsToImport.length) {
                notifyWarning(t('models_import_empty', 'No models found in this file.'));
                return;
            }

            importState.payload = payload;
            importState.models = modelsToImport;
            importState.selected = new Set(modelsToImport.map((_, idx) => idx));
            importState.fileName = file.name || 'models.json';

            renderImportList();
            openImportModal();
        } catch (error) {
            console.error('Failed to import models', error);
            notifyError(error?.message || t('models_import_failed', 'Failed to import models.'));
        }
    };

    /**
     * Check if any overlay/modal/dropdown is currently open that should consume ESC.
     */
    const isOverlayOpen = () => {
        // Check for open action menu
        if (actionMenuState.openMenu) return true;
        // Check for open multi-selects
        if (document.querySelector('.admin-multiselect.open')) return true;
        // Check for open icon picker
        if (document.querySelector('.icon-picker-dropdown:not([hidden])')) return true;
        // Check for delete modal
        if (dom.deleteOverlay && !dom.deleteOverlay.hidden) return true;
        // Check for import modal
        if (dom.importOverlay && !dom.importOverlay.hidden) return true;
        // Check for other open modals/overlays
        if (document.querySelector('.overlay-container.visible, .modal-overlay.visible')) return true;
        return false;
    };

    /**
     * Handle global keyboard navigation for the models page.
     */
    const handleEditKeydown = (event) => {
        // Only handle Escape key
        if (event.key !== 'Escape') return;
        
        // Only act when in edit view
        if (state.view !== 'edit') return;
        
        // Don't navigate if an overlay/dropdown is open (let it handle ESC)
        if (isOverlayOpen()) return;
        
        // Don't navigate if focus is in a text input that has content (allow clearing)
        const activeEl = document.activeElement;
        if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA')) {
            if (activeEl.value && activeEl.value.trim()) {
                // If input has content, first ESC blurs the field
                activeEl.blur();
                return;
            }
        }
        
        event.preventDefault();
        event.stopPropagation();
        requestUnsavedEditConfirmation(goToListView);
    };

    const bindEvents = () => {
        registerUnsavedGuard();
        if (dom.providerFilter && dom.providerFilter.dataset.bound !== 'true') {
            dom.providerFilter.addEventListener('change', renderModels);
            dom.providerFilter.dataset.bound = 'true';
        }

        if (dom.search && dom.search.dataset.bound !== 'true') {
            dom.search.addEventListener('input', handleSearchInput);
            dom.search.dataset.bound = 'true';
            updateSearchClearVisibility();
        }

        if (dom.searchClear && dom.searchClear.dataset.bound !== 'true') {
            dom.searchClear.addEventListener('click', handleSearchClear);
            dom.searchClear.dataset.bound = 'true';
            updateSearchClearVisibility();
        }

        if (dom.create && dom.create.dataset.bound !== 'true') {
            dom.create.addEventListener('click', handleCreateClick);
            dom.create.dataset.bound = 'true';
        }

        if (dom.bulkToggle && dom.bulkToggle.dataset.bound !== 'true') {
            dom.bulkToggle.addEventListener('click', () => toggleBulkSelectionMode());
            dom.bulkToggle.dataset.bound = 'true';
        }

        if (dom.bulkClearButton && dom.bulkClearButton.dataset.bound !== 'true') {
            dom.bulkClearButton.addEventListener('click', () => {
                clearBulkSelection({ preserveMode: true });
                updateBulkUiState();
                renderModels();
            });
            dom.bulkClearButton.dataset.bound = 'true';
        }

        if (dom.bulkEditButton && dom.bulkEditButton.dataset.bound !== 'true') {
            dom.bulkEditButton.addEventListener('click', openBulkEdit);
            dom.bulkEditButton.dataset.bound = 'true';
        }

        if (dom.bulkDeleteButton && dom.bulkDeleteButton.dataset.bound !== 'true') {
            dom.bulkDeleteButton.addEventListener('click', openBulkDelete);
            dom.bulkDeleteButton.dataset.bound = 'true';
        }

        if (dom.exportButton && dom.exportButton.dataset.bound !== 'true') {
            dom.exportButton.addEventListener('click', handleExportModels);
            dom.exportButton.dataset.bound = 'true';
        }

        if (dom.importButton && dom.importButton.dataset.bound !== 'true') {
            dom.importButton.addEventListener('click', () => dom.importInput?.click());
            dom.importButton.dataset.bound = 'true';
        }

        if (dom.importInput && dom.importInput.dataset.bound !== 'true') {
            dom.importInput.addEventListener('change', handleImportModels);
            dom.importInput.dataset.bound = 'true';
        }

        if (dom.importOverlay && dom.importOverlay.dataset.bound !== 'true') {
            dom.importOverlay.addEventListener('click', (event) => {
                if (event.target === dom.importOverlay) {
                    closeImportModal();
                }
            });
            dom.importOverlay.dataset.bound = 'true';
        }

        if (dom.importClose && dom.importClose.dataset.bound !== 'true') {
            dom.importClose.addEventListener('click', closeImportModal);
            dom.importClose.dataset.bound = 'true';
        }

        if (dom.importCancel && dom.importCancel.dataset.bound !== 'true') {
            dom.importCancel.addEventListener('click', closeImportModal);
            dom.importCancel.dataset.bound = 'true';
        }

        if (dom.importConfirm && dom.importConfirm.dataset.bound !== 'true') {
            dom.importConfirm.addEventListener('click', submitSelectedImports);
            dom.importConfirm.dataset.bound = 'true';
        }

        if (dom.importSelectAll && dom.importSelectAll.dataset.bound !== 'true') {
            dom.importSelectAll.addEventListener('change', toggleSelectAllImports);
            dom.importSelectAll.dataset.bound = 'true';
        }

        if (dom.deleteOverlay && dom.deleteOverlay.dataset.bound !== 'true') {
            dom.deleteOverlay.addEventListener('click', (event) => {
                if (event.target === dom.deleteOverlay) {
                    closeDeleteModelModal();
                }
            });
            dom.deleteOverlay.dataset.bound = 'true';
        }

        if (dom.deleteCancel && dom.deleteCancel.dataset.bound !== 'true') {
            dom.deleteCancel.addEventListener('click', (event) => {
                event.preventDefault();
                closeDeleteModelModal();
            });
            dom.deleteCancel.dataset.bound = 'true';
        }

        if (dom.deleteConfirm && dom.deleteConfirm.dataset.bound !== 'true') {
            dom.deleteConfirm.addEventListener('click', (event) => {
                event.preventDefault();
                deleteCurrentModel();
            });
            dom.deleteConfirm.dataset.bound = 'true';
        }

        if (dom.list && dom.list.dataset.bound !== 'true') {
            dom.list.addEventListener('click', handleModelListClick);
            dom.list.dataset.bound = 'true';
        }

        if (dom.editForm && dom.editForm.dataset.bound !== 'true') {
            dom.editForm.addEventListener('submit', submitEditForm);
            dom.editForm.dataset.bound = 'true';
        }

        if (dom.editBackButton && dom.editBackButton.dataset.bound !== 'true') {
            dom.editBackButton.addEventListener('click', () => requestUnsavedEditConfirmation(goToListView));
            dom.editBackButton.dataset.bound = 'true';
        }

        // Global keyboard navigation for edit view
        if (!state.keyboardBound) {
            document.addEventListener('keydown', handleEditKeydown);
            state.keyboardBound = true;
        }
    };

    const settingsControllerByRoute = {
        models: modelsSettingsController,
        'models-dictation-settings': modelsDictationSettingsController,
        'models-read-aloud-settings': modelsReadAloudSettingsController,
        'models-realtime-settings': modelsRealtimeSettingsController,
    };

    // The Models list and its voice-setting subpages share one page lifecycle
    // group, but each schema renderer owns independent active/inactive state.
    // Track the renderer that currently owns the visible route so first entry
    // initializes it, later refreshes reload it, and hidden renderers are
    // cleanly deactivated before another route takes over.
    let activeSettingsRouteKey = null;

    const activateSettingsController = (pageKey, { reloadSchema = false } = {}) => {
        const routeKey = Object.prototype.hasOwnProperty.call(settingsControllerByRoute, pageKey)
            ? pageKey
            : 'models';
        const nextController = settingsControllerByRoute[routeKey];

        if (activeSettingsRouteKey !== routeKey) {
            if (activeSettingsRouteKey) {
                settingsControllerByRoute[activeSettingsRouteKey]?.teardown();
            }
            activeSettingsRouteKey = routeKey;
            nextController.init();
            return;
        }

        if (reloadSchema) {
            nextController.reload();
            return;
        }
        nextController.init();
    };

    const init = ({ pageKey = 'models', reloadSchema = false } = {}) => {
        if (!modelsLanguageObserver && document.documentElement) {
            modelsLanguageObserver = new MutationObserver((mutations) => {
                const langChanged = mutations.some((mutation) => mutation.type === 'attributes' && mutation.attributeName === 'lang');
                if (langChanged && state.initialized && dom.page && !dom.page.hidden) {
                    renderModels();
                }
            });
            modelsLanguageObserver.observe(document.documentElement, {
                attributes: true,
                attributeFilter: ['lang'],
            });
        }
        if (state.initialized) {
            if (pageKey === 'models') {
                loadModels();
            }
            activateSettingsController(pageKey, { reloadSchema });
            return;
        }
        state.initialized = true;
        bindEvents();
        goToListView();
        updateBulkUiState();
        if (pageKey === 'models') {
            loadModels();
        }
        activateSettingsController(pageKey, { reloadSchema });
    };

    const teardown = () => {
        if (!activeSettingsRouteKey) {
            return;
        }
        settingsControllerByRoute[activeSettingsRouteKey]?.teardown();
        activeSettingsRouteKey = null;
    };

    const registerUnsavedGuard = () => {
        if (unsavedGuardRegistered || typeof window.unsavedChangesManager?.register !== 'function') {
            return;
        }
        window.unsavedChangesManager.register({
            id: UNSAVED_GUARD_ID,
            priority: 205,
            isActive: () => Boolean(dom.editPage && !dom.editPage.hidden),
            isDirty: () => hasUnsavedEditChanges(),
            discard: () => {
                goToListView();
            },
        });
        unsavedGuardRegistered = true;
    };

    window.initModelsPage = init;
    window.teardownModelsPage = teardown;
    window.adminModelsShowList = goToListView;
})();
