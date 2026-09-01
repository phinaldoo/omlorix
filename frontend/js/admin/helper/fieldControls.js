const parseNumericConstraint = (value) => {
    if (value === undefined || value === null || value === '') {
        return null;
    }
    const parsed = Number(value);
    return Number.isNaN(parsed) ? null : parsed;
};

const attachNumberConstraintHandlers = (control, minConstraint, maxConstraint) => {
    if (minConstraint === null && maxConstraint === null) {
        return;
    }
    const clampValue = () => {
        const rawValue = control.value;
        if (rawValue === '' || rawValue === null || rawValue === undefined) {
            return;
        }
        const numericValue = Number(rawValue);
        if (Number.isNaN(numericValue)) {
            return;
        }
        let clampedValue = numericValue;
        if (minConstraint !== null && clampedValue < minConstraint) {
            clampedValue = minConstraint;
        }
        if (maxConstraint !== null && clampedValue > maxConstraint) {
            clampedValue = maxConstraint;
        }
        if (clampedValue !== numericValue) {
            control.value = clampedValue;
        }
    };
    control.addEventListener('change', clampValue);
    control.addEventListener('blur', clampValue);
};

function createFieldControl(field, { value, datasetKey, attributes } = {}) {
    const coalesceAttribute = (primary, fallback) => {
        if (primary !== undefined && primary !== null) {
            return primary;
        }
        if (fallback !== undefined && fallback !== null && fallback !== '') {
            return fallback;
        }
        return undefined;
    };

    let control;
    let root;
    let fieldType = (field?.type || '').toLowerCase() || (field?.input_type || '').toLowerCase();
    const fieldAttributes = attributes || field?.attributes || {};

    // Special case: detect LLM access permissions field by key name
    if (field?.key === 'allow_llm_to_access_personal_information') {
        fieldType = 'llm_access_permissions';
    }

    switch (fieldType) {
        case 'boolean': {
            const label = document.createElement('label');
            label.className = 'toggle-switch';
            control = document.createElement('input');
            control.type = 'checkbox';
            control.className = 'toggle-input';
            label.appendChild(control);
            const slider = document.createElement('span');
            slider.className = 'toggle-slider';
            label.appendChild(slider);
            root = label;
            break;
        }
        case 'select': {
            control = document.createElement('select');
            control.className = 'select';
            if (field.multiple) {
                control.multiple = true;
            }
            const rawOptions = Array.isArray(field.options) ? field.options : [];
            const hasWebsearchProviderOptions = rawOptions.some((option) => Boolean(
                option?.metadata
                && (
                    window.WebsearchProviderLogic?.isWebsearchProviderField?.(field)
                    || option.metadata.has_search === true
                    || option.metadata.has_scrape === true
                    || option.metadata.has_combined === true
                )
            ));
            const options = hasWebsearchProviderOptions && typeof window.WebsearchProviderLogic?.sortedProviderOptions === 'function'
                ? window.WebsearchProviderLogic.sortedProviderOptions(rawOptions)
                : rawOptions;

            if (!field.multiple) {
                const placeholder = getFieldPlaceholder(field);
                const hasEmptyOption = options.some(
                    (option) => String(option?.value ?? '') === ''
                );
                if (placeholder) {
                    // Keep a real empty native option ahead of provider/model
                    // choices. Browsers otherwise auto-select the first model
                    // while the persisted value is still null, and the enhanced
                    // select then mirrors that misleading state.
                    control.dataset.placeholder = placeholder;
                    if (!hasEmptyOption) {
                        const placeholderOption = document.createElement('option');
                        placeholderOption.value = '';
                        placeholderOption.textContent = placeholder;
                        control.appendChild(placeholderOption);
                    }
                }
            }

            options.forEach((option) => {
                const opt = document.createElement('option');
                opt.value = option.value;
                const optionLabel = resolveAdminSchemaOptionLabel(option, helperT);
                const isWebsearchProviderOption = Boolean(
                    option?.metadata
                    && (
                        window.WebsearchProviderLogic?.isWebsearchProviderField?.(field)
                        || option.metadata.has_search === true
                        || option.metadata.has_scrape === true
                        || option.metadata.has_combined === true
                    )
                );
                if (option?.metadata) {
                    opt.dataset.metadata = JSON.stringify(option.metadata);
                }
                if (isWebsearchProviderOption && option.metadata.has_combined) {
                    const formatter = window.adminFormatT || window.formatTranslation;
                    if (typeof formatter === 'function') {
                        opt.textContent = formatter(
                            'websearch_provider_combined_suffix',
                            '{label} (combined)',
                            { label: optionLabel }
                        );
                    } else {
                        opt.textContent = `${optionLabel} (combined)`;
                    }
                } else {
                    opt.textContent = optionLabel;
                }
                control.appendChild(opt);
            });
            if (field.multiple) {
                const multiSelectMeta = initializeAdminMultiSelect(control, field);
                control._multiSelect = multiSelectMeta;
                root = multiSelectMeta.wrapper;
            } else {
                const singleSelectMeta = initializeAdminSingleSelect(control, field);
                control._singleSelect = singleSelectMeta;
                root = singleSelectMeta.wrapper;
            }
            break;
        }
        case 'number': {
            control = document.createElement('input');
            control.type = 'number';
            control.inputMode = 'decimal';
            control.className = 'input';
            const minValue = coalesceAttribute(fieldAttributes?.min, control.min);
            if (minValue !== undefined) {
                control.min = String(minValue);
            }
            const maxValue = coalesceAttribute(fieldAttributes?.max, control.max);
            if (maxValue !== undefined) {
                control.max = String(maxValue);
            }
            const stepValue = coalesceAttribute(fieldAttributes?.step, control.step ?? field.placeholder === 'integer' ? 1 : undefined);
            if (stepValue !== undefined) {
                control.step = String(stepValue);
            }
            const normalizedMin = parseNumericConstraint(minValue);
            const normalizedMax = parseNumericConstraint(maxValue);
            attachNumberConstraintHandlers(control, normalizedMin, normalizedMax);
            root = control;
            break;
        }
        case 'string_list': {
            // Create keyword tags UI
            control = document.createElement('div');
            control.className = 'keyword-tags-container';
            control.dataset.keywordTags = '[]';
            if (field?.metadata?.ordered === true) {
                control.dataset.orderedList = 'true';
            }

            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'keyword-tags-input';
            input.placeholder = getFieldPlaceholder(
                field,
                helperT('admin_keyword_placeholder', 'Type a keyword and press Enter...')
            );

            const list = document.createElement('div');
            list.className = 'keyword-tags-list';

            control.appendChild(input);
            control.appendChild(list);

            // Handle Enter key to add keywords
            input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    const val = input.value.trim();

                    if (val) {
                        const currentKeywords = JSON.parse(control.dataset.keywordTags || '[]');

                        if (!currentKeywords.includes(val)) {
                            const updatedKeywords = [...currentKeywords, val];
                            control.dataset.keywordTags = JSON.stringify(updatedKeywords);
                            renderKeywordTags(control, updatedKeywords);
                            input.value = '';

                            // Trigger change event
                            control.dispatchEvent(new CustomEvent('keywordschange', {
                                detail: { keywords: updatedKeywords }
                            }));
                        } else {
                            // Shake animation for duplicate
                            input.classList.add('keyword-tags-shake');
                            setTimeout(() => input.classList.remove('keyword-tags-shake'), 400);
                        }
                    }
                }
            });

            root = control;
            break;
        }
        case 'boolean_map': {
            // The persisted/API value remains an explicit boolean map, while
            // the editor presents the enabled keys as one compact multi-select.
            // This preserves the stable storage format and removes the former
            // stack of one toggle per map entry.
            control = document.createElement('select');
            control.className = 'boolean-map-control select';
            control.multiple = true;
            control.dataset.booleanMap = '{}';
            control.setAttribute('aria-label', getFieldLabel(field, field?.key || ''));
            const items = getBooleanMapItems(field, value);
            const initialValue = normalizeBooleanMapValue(field, value);

            items.forEach((item) => {
                const option = document.createElement('option');
                option.value = item.key;
                option.textContent = item.label;
                option.selected = Boolean(initialValue[item.key]);
                control.appendChild(option);
            });

            control.dataset.booleanMap = JSON.stringify(initialValue);
            const multiSelectMeta = initializeAdminMultiSelect(control, {
                ...field,
                multiple: true,
            });
            control._multiSelect = multiSelectMeta;
            control.addEventListener('change', () => {
                const nextValue = normalizeBooleanMapValue(field, parseBooleanMapDataset(control));
                Array.from(control.options).forEach((option) => {
                    nextValue[option.value] = Boolean(option.selected);
                });
                control.dataset.booleanMap = JSON.stringify(nextValue);
                dispatchBooleanMapChange(control);
            });

            root = multiSelectMeta.wrapper;
            break;
        }
        case 'access_rules': {
            // Access rules editor for time-based access windows
            control = document.createElement('div');
            control.className = 'access-rules-editor';
            control.dataset.accessRules = '[]';

            const dayLabels = [
                helperT('admin_day_mon_short', 'Mon'),
                helperT('admin_day_tue_short', 'Tue'),
                helperT('admin_day_wed_short', 'Wed'),
                helperT('admin_day_thu_short', 'Thu'),
                helperT('admin_day_fri_short', 'Fri'),
                helperT('admin_day_sat_short', 'Sat'),
                helperT('admin_day_sun_short', 'Sun')
            ];

            // Presets header
            const header = document.createElement('div');
            header.className = 'access-rules-header';

            const presetsContainer = document.createElement('div');
            presetsContainer.className = 'access-rules-presets';

            const presets = [
                {
                    key: 'school',
                    label: helperT('admin_access_rule_preset_school', 'School Hours'),
                    icon: getAdminIconMarkup('education'),
                        rules: [{
                        start: '08:00',
                        end: '16:00',
                        days: [0, 1, 2, 3, 4],
                        label: helperT('admin_access_rule_preset_school_label', 'School hours')
                    }]
                },
                {
                    key: 'business',
                    label: helperT('admin_access_rule_preset_business', 'Business Hours'),
                    icon: getAdminIconMarkup('business'),
                    rules: [{
                        start: '09:00',
                        end: '17:00',
                        days: [0, 1, 2, 3, 4],
                        label: helperT('admin_access_rule_preset_business_label', 'Business hours')
                    }]
                },
                {
                    key: 'extended',
                    label: helperT('admin_access_rule_preset_extended', 'Extended'),
                    icon: getAdminIconMarkup('clock'),
                    rules: [{
                        start: '07:00',
                        end: '18:00',
                        days: [0, 1, 2, 3, 4],
                        label: helperT('admin_access_rule_preset_extended_label', 'Extended hours')
                    }]
                },
                {
                    key: 'weekend',
                    label: helperT('admin_access_rule_preset_weekend', 'Weekend Only'),
                    icon: getAdminIconMarkup('edit'),
                    rules: [{
                        start: '00:00',
                        end: '23:59',
                        days: [5, 6],
                        label: helperT('admin_access_rule_preset_weekend_label', 'Weekend access')
                    }]
                },
                {
                    key: 'night_block',
                    label: helperT('admin_access_rule_preset_night', 'Night Block'),
                    icon: getAdminIconMarkup('night'),
                    accessMode: 'blocklist',
                    rules: [{
                        start: '22:00',
                        end: '06:00',
                        days: [0, 1, 2, 3, 4, 5, 6],
                        label: helperT('admin_access_rule_preset_night_label', 'Night hours (block)')
                    }]
                },
            ];

            // Add presets label
            const presetsLabel = document.createElement('span');
            presetsLabel.className = 'access-rules-presets-label';
            presetsLabel.textContent = helperT('admin_access_rules_quick_add', 'Quick add:');
            presetsContainer.appendChild(presetsLabel);

            presets.forEach((preset) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'access-rules-preset-btn';
                btn.dataset.preset = preset.key;
                btn.setAttribute('title', preset.label);
                btn.innerHTML = `${preset.icon}<span>${preset.label}</span>`;
                btn.addEventListener('click', () => {
                    // Add visual feedback
                    btn.classList.add('active');
                    setTimeout(() => btn.classList.remove('active'), 300);
                    
                    const currentRules = JSON.parse(control.dataset.accessRules || '[]');
                    const newRules = [...currentRules, ...preset.rules];
                    applyAccessRulesPresetMode(control, preset.accessMode);
                    control.dataset.accessRules = JSON.stringify(newRules);
                    renderAccessRules(control, newRules, dayLabels);
                    control.dispatchEvent(new CustomEvent('ruleschange', { detail: { rules: newRules } }));
                });
                presetsContainer.appendChild(btn);
            });

            header.appendChild(presetsContainer);
            control.appendChild(header);

            // Rules list container
            const listContainer = document.createElement('div');
            listContainer.className = 'access-rules-list';
            control.appendChild(listContainer);

            // Add rule button
            const addBtn = document.createElement('button');
            addBtn.type = 'button';
            addBtn.className = 'access-rules-add-btn';
            addBtn.innerHTML = `
                ${getAdminIconMarkup('plus')}
                <span>${helperT('admin_access_rules_add_rule', 'Add Rule')}</span>
            `;
            addBtn.addEventListener('click', () => {
                const currentRules = JSON.parse(control.dataset.accessRules || '[]');
                const newRule = { start: '09:00', end: '17:00', days: [0, 1, 2, 3, 4], label: '' };
                const newRules = [...currentRules, newRule];
                control.dataset.accessRules = JSON.stringify(newRules);
                renderAccessRules(control, newRules, dayLabels);
                control.dispatchEvent(new CustomEvent('ruleschange', { detail: { rules: newRules } }));
            });
            control.appendChild(addBtn);

            root = control;
            break;
        }
        case 'llm_access_permissions': {
            const LLM_ACCESS_FIELDS = [
                { key: 'first_name', label: helperT('us_label_first_name', 'First Name') },
                { key: 'language', label: helperT('us_general_language_title', 'Language') },
                { key: 'country', label: helperT('us_general_country_title', 'Country') },
                { key: 'timezone', label: helperT('us_general_timezone_title', 'Timezone') },
                { key: 'location', label: helperT('us_general_location_title', 'Location') }
            ];

            control = document.createElement('div');
            control.className = 'llm-access-admin-controls';
            control.dataset.llmAccessPermissions = '{}';
            control.dataset.llmAccessPreset = 'none';

            // Mode selector
            const modeSelector = document.createElement('div');
            modeSelector.className = 'llm-access-mode-selector';

            const modes = [
                { key: 'none', label: helperT('admin_user_settings_llm_preset_none', 'None') },
                { key: 'all', label: helperT('admin_user_settings_llm_preset_all', 'All') },
                { key: 'custom', label: helperT('admin_user_settings_llm_preset_custom', 'Custom') }
            ];

            modes.forEach((mode) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'llm-mode-btn';
                btn.dataset.mode = mode.key;
                btn.textContent = mode.label;
                btn.addEventListener('click', () => {
                    if (btn.classList.contains('active')) return;
                    modeSelector.querySelectorAll('.llm-mode-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    control.dataset.llmAccessPreset = mode.key;

                    const permissions = JSON.parse(control.dataset.llmAccessPermissions || '{}');
                    if (mode.key === 'all') {
                        LLM_ACCESS_FIELDS.forEach(f => permissions[f.key] = true);
                    } else if (mode.key === 'none') {
                        LLM_ACCESS_FIELDS.forEach(f => permissions[f.key] = false);
                    }
                    control.dataset.llmAccessPermissions = JSON.stringify(permissions);

                    fieldsContainer.style.display = mode.key === 'custom' ? 'grid' : 'none';
                    fieldsContainer.querySelectorAll('.llm-field-input').forEach(input => {
                        input.checked = Boolean(permissions[input.dataset.field]);
                    });

                    control.dispatchEvent(new CustomEvent('llmaccesschange', {
                        detail: { permissions, preset: mode.key }
                    }));
                });
                modeSelector.appendChild(btn);
            });

            control.appendChild(modeSelector);

            // Fields container
            const fieldsContainer = document.createElement('div');
            fieldsContainer.className = 'llm-access-fields boolean-map-control';
            fieldsContainer.style.display = 'none';

            LLM_ACCESS_FIELDS.forEach((fieldDef) => {
                const fieldToggle = document.createElement('label');
                fieldToggle.className = 'boolean-map-item llm-field-toggle';

                const text = document.createElement('span');
                text.className = 'boolean-map-item-text';

                const title = document.createElement('span');
                title.className = 'boolean-map-item-title';
                title.textContent = fieldDef.label;
                text.appendChild(title);

                fieldToggle.appendChild(text);

                const toggleLabel = document.createElement('span');
                toggleLabel.className = 'toggle-switch small';

                const input = document.createElement('input');
                input.type = 'checkbox';
                input.className = 'llm-field-input toggle-input';
                input.dataset.field = fieldDef.key;
                input.addEventListener('change', () => {
                    const permissions = JSON.parse(control.dataset.llmAccessPermissions || '{}');
                    permissions[fieldDef.key] = input.checked;
                    control.dataset.llmAccessPermissions = JSON.stringify(permissions);
                    control.dataset.llmAccessPreset = 'custom';

                    modeSelector.querySelectorAll('.llm-mode-btn').forEach(b => {
                        b.classList.toggle('active', b.dataset.mode === 'custom');
                    });

                    control.dispatchEvent(new CustomEvent('llmaccesschange', {
                        detail: { permissions, preset: 'custom' }
                    }));
                });
                toggleLabel.appendChild(input);

                const slider = document.createElement('span');
                slider.className = 'toggle-slider';
                toggleLabel.appendChild(slider);

                fieldToggle.appendChild(toggleLabel);
                fieldsContainer.appendChild(fieldToggle);
            });

            control.appendChild(fieldsContainer);
            root = control;
            break;
        }
        case 'context_files': {
            control = document.createElement('div');
            control.className = 'context-files-editor';
            control.dataset.contextFiles = '[]';

            // File list container (will be populated dynamically)
            const filesList = document.createElement('div');
            filesList.className = 'context-files-list';
            control.appendChild(filesList);

            // Drop zone with drag-and-drop support
            const dropzone = document.createElement('div');
            dropzone.className = 'context-files-dropzone';
            dropzone.setAttribute('role', 'button');
            dropzone.setAttribute('tabindex', '0');
            dropzone.setAttribute('aria-label', helperT('admin_drop_files_aria', 'Drop files here or click to upload'));

            const dropzoneContent = document.createElement('div');
            dropzoneContent.className = 'context-files-dropzone-content';
            dropzoneContent.innerHTML = `
                ${getAdminIconMarkup('share')}
                <div class="context-files-dropzone-text">
                    <p class="context-files-dropzone-primary">${helperT('admin_drop_files_text', 'Drop files here or')} <span>${helperT('admin_browse_files', 'browse')}</span></p>
                    <p class="context-files-dropzone-secondary">${helperT('admin_supported_files', 'PDF, images, documents, and more')}</p>
                </div>
            `;
            dropzone.appendChild(dropzoneContent);

            // Hidden file input
            const uploadInput = document.createElement('input');
            uploadInput.type = 'file';
            uploadInput.className = 'context-files-upload-input';
            uploadInput.multiple = true;
            uploadInput.id = `context-files-input-${Date.now()}`;
            uploadInput.setAttribute('aria-hidden', 'true');
            dropzone.appendChild(uploadInput);

            // Handle upload files function
            const handleUploadFiles = async (files) => {
                const groupId = control.dataset.groupId || '';
                if (!groupId) {
                    notifyError(helperT('admin_context_files_save_group_first', 'Please save the group first before uploading context files.'));
                    return;
                }

                if (!files || files.length === 0) return;

                dropzone.classList.add('disabled');

                for (const file of files) {
                    // Add uploading item to list for visual feedback
                    const listEl = control.querySelector('.context-files-list');
                    const uploadingItem = document.createElement('div');
                    uploadingItem.className = 'context-files-item uploading';
                    uploadingItem.innerHTML = `
                        <div class="context-files-item-icon">
                            ${getAdminIconMarkup('file')}
                        </div>
                        <div class="context-files-item-content">
                            <div class="context-files-item-name">${helperEscapeHtml(file.name)}</div>
                            <div class="context-files-item-progress">
                                <div class="context-files-item-progress-bar indeterminate"></div>
                            </div>
                        </div>
                    `;
                    listEl.appendChild(uploadingItem);

                    try {
                        const formData = new FormData();
                        formData.append('file', file);
                        formData.append('group_context_id', groupId);

                        const response = await window.authedFetch('/api/v1/files/upload', {
                            method: 'POST',
                            body: formData,
                        });

                        // Remove uploading item
                        uploadingItem.remove();

                        if (!response.ok) {
                            const errorData = await response.json().catch(() => ({}));
                            notifyError(errorData.detail || helperT('admin_upload_file_failed', 'Failed to upload {file}').replace('{file}', file.name));
                            continue;
                        }

                        const result = await response.json();
                        if (result.status === 'success' && result.file_id) {
                            const currentIds = JSON.parse(control.dataset.contextFiles || '[]');
                            if (!currentIds.includes(result.file_id)) {
                                const newIds = [...currentIds, result.file_id];
                                control.dataset.contextFiles = JSON.stringify(newIds);
                                await renderContextFiles(control, newIds);
                                control.dispatchEvent(new CustomEvent('contextfileschange', {
                                    detail: { fileIds: newIds }
                                }));
                            }
                        }
                    } catch (error) {
                        uploadingItem.remove();
                        console.error('Upload error:', error);
                        notifyError(helperT('admin_upload_file_failed', 'Failed to upload {file}').replace('{file}', file.name));
                    }
                }

                dropzone.classList.remove('disabled');
                uploadInput.value = '';
            };

            // Click to open file picker
            dropzone.addEventListener('click', (e) => {
                if (e.target === uploadInput) return;
                const groupId = control.dataset.groupId || '';
                if (!groupId) {
                    notifyError(helperT('admin_context_files_save_group_first', 'Please save the group first before uploading context files.'));
                    return;
                }
                uploadInput.click();
            });

            // Keyboard accessibility
            dropzone.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    dropzone.click();
                }
            });

            // File input change handler
            uploadInput.addEventListener('change', (e) => {
                handleUploadFiles(Array.from(e.target.files || []));
            });

            // Drag and drop handlers
            dropzone.addEventListener('dragenter', (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.add('dragover');
            });

            dropzone.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.add('dragover');
            });

            dropzone.addEventListener('dragleave', (e) => {
                e.preventDefault();
                e.stopPropagation();
                // Only remove class if we're leaving the dropzone entirely
                if (!dropzone.contains(e.relatedTarget)) {
                    dropzone.classList.remove('dragover');
                }
            });

            dropzone.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.remove('dragover');
                
                const files = Array.from(e.dataTransfer?.files || []);
                if (files.length > 0) {
                    handleUploadFiles(files);
                }
            });

            control.appendChild(dropzone);

            // New group note (shown when no group ID)
            const newGroupNote = document.createElement('div');
            newGroupNote.className = 'context-files-new-group-note';
            newGroupNote.innerHTML = `
                ${getAdminIconMarkup('info')}
                <span>${helperT('admin_context_files_save_group_note', 'Save the group first to enable file uploads')}</span>
            `;
            control.appendChild(newGroupNote);

            root = control;
            break;
        }
        case 'textarea': {
            control = document.createElement('textarea');
            control.className = 'input textarea';
            control.rows = field?.rows || 3;
            if (field?.max_length) {
                control.maxLength = field.max_length;
            }
            applyInputPlaceholder(control, getFieldPlaceholder(field));
            
            // Create wrapper for character counter
            const wrapper = document.createElement('div');
            wrapper.className = 'textarea-wrapper';
            wrapper.appendChild(control);
            
            if (field?.max_length) {
                const counter = document.createElement('div');
                counter.className = 'textarea-counter';
                const currentLength = (value || '').length;
                counter.textContent = `${currentLength}/${field.max_length}`;
                wrapper.appendChild(counter);
                
                // Update counter on input
                control.addEventListener('input', () => {
                    const len = control.value.length;
                    counter.textContent = `${len}/${field.max_length}`;
                    counter.classList.toggle('textarea-counter-warning', len > field.max_length * 0.9);
                    counter.classList.toggle('textarea-counter-error', len >= field.max_length);
                });
            }
            
            root = wrapper;
            break;
        }
        case 'json': {
            control = document.createElement('textarea');
            control.className = 'input textarea admin-textarea--code settings-json-editor';
            control.rows = field?.rows || 10;
            control.spellcheck = false;
            control.setAttribute('aria-label', getFieldLabel(field));
            control.setAttribute('aria-describedby', `${field.key}-json-help`);
            applyInputPlaceholder(control, getFieldPlaceholder(field));

            const wrapper = document.createElement('div');
            wrapper.className = 'textarea-wrapper settings-json-editor-wrapper';
            wrapper.appendChild(control);

            const help = document.createElement('p');
            help.id = `${field.key}-json-help`;
            help.className = 'settings-json-editor-help';
            help.textContent = helperT(
                'admin_json_editor_help',
                'Enter valid JSON. Changes are validated before they are saved.',
            );
            wrapper.appendChild(help);
            root = wrapper;
            break;
        }
        case 'string':
        default: {
            control = document.createElement('input');
            const requestedType = typeof field?.input_type === 'string'
                ? field.input_type.toLowerCase().trim()
                : '';
            const allowedInputTypes = new Set(['text', 'password', 'email', 'url', 'search', 'color']);
            control.type = allowedInputTypes.has(requestedType) ? requestedType : 'text';
            control.className = 'input';
            if (field?.max_length) {
                control.maxLength = field.max_length;
            }
            applyInputPlaceholder(control, getFieldPlaceholder(field));
            root = control;
            break;
        }
    }

    if (datasetKey) {
        control.dataset.settingKey = datasetKey;
    }

    if (fieldType === 'number') {
        applyInputPlaceholder(control, getFieldPlaceholder(field));
    }

    applyControlValue(control, field, value);

    return { root, control };
}


