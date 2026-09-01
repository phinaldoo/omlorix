function createFieldLayout(field) {
    const row = document.createElement('div');
    row.classList.add('settings-row');
    if (field?.type) {
        row.dataset.fieldType = field.type;
    }
    if (field?.type === 'access_rules') {
        row.classList.add('column', 'settings-row--access-rules');
    } else if (field?.type === 'json') {
        row.classList.add('column', 'settings-row--json');
    }

    const left = document.createElement('div');
    left.classList.add('settings-row-left');

    const title = document.createElement('p');
    title.classList.add('settings-row-title');
    const _labelFallback = field.label ?? field.key ?? '';
    title.textContent = (field.i18n_label && typeof window.getTranslation === 'function')
        ? window.getTranslation(field.i18n_label, _labelFallback)
        : _labelFallback;
    left.appendChild(title);

    if (field.description) {
        const desc = document.createElement('p');
        desc.classList.add('settings-row-desc');
        desc.textContent = (field.i18n_description && typeof window.getTranslation === 'function')
            ? window.getTranslation(field.i18n_description, field.description)
            : field.description;
        left.appendChild(desc);
    }

    row.appendChild(left);

    const controlWrapper = document.createElement('div');
    controlWrapper.classList.add('settings-row-control');
    if (field?.type === 'access_rules') {
        controlWrapper.classList.add('settings-row-control--access-rules');
    } else if (field?.type === 'json') {
        controlWrapper.classList.add('settings-row-control--json');
    }
    row.appendChild(controlWrapper);

    return { row, controlWrapper };
}

function syncSectionBodyLastVisibleRow(target = document) {
    if (!(target instanceof Document || target instanceof DocumentFragment || target instanceof Element)) {
        return;
    }

    const sectionBodies = [];
    if (target instanceof Element && target.classList.contains('settings-section-body')) {
        sectionBodies.push(target);
    }
    sectionBodies.push(...Array.from(target.querySelectorAll('.settings-section-body')));

    // If no .settings-section-body found and the target itself contains .settings-row children, treat it as one
    if (!sectionBodies.length && target instanceof Element) {
        const hasRows = Array.from(target.children).some((child) => child.classList?.contains('settings-row'));
        if (hasRows) {
            sectionBodies.push(target);
        }
    }

    sectionBodies.forEach((body) => {
        const rows = Array.from(body.children).filter((child) =>
            child instanceof HTMLElement && child.classList.contains('settings-row')
        );

        let lastVisibleRow = null;
        rows.forEach((row) => {
            row.classList.remove('is-last-visible-row');
            const isVisible = !row.hidden && row.style.display !== 'none';
            if (isVisible) {
                lastVisibleRow = row;
            }
        });

        if (lastVisibleRow) {
            lastVisibleRow.classList.add('is-last-visible-row');
        }
    });
}

function syncSchemaSectionVisibility(target = document) {
    if (!(target instanceof Document || target instanceof DocumentFragment || target instanceof Element)) {
        return;
    }

    const sectionEls = [];
    if (target instanceof Element && target.classList.contains('settings-section')) {
        sectionEls.push(target);
    }
    sectionEls.push(...Array.from(target.querySelectorAll('.settings-section')));

    sectionEls.forEach((sectionEl) => {
        const rowEls = sectionEl.querySelectorAll('.settings-section-body .settings-row');
        const hasVisibleFields = Array.from(rowEls).some((row) => !row.hidden && row.style.display !== 'none');
        const shouldHideSection = !hasVisibleFields;
        sectionEl.hidden = shouldHideSection;
        sectionEl.style.display = shouldHideSection ? 'none' : '';
    });
}

function applyControlValue(control, field, value) {
    let fieldType = (field?.type || '').toLowerCase();
    // Special case: detect LLM access permissions field by key name
    if (field?.key === 'allow_llm_to_access_personal_information') {
        fieldType = 'llm_access_permissions';
    }
    switch (fieldType) {
        case 'boolean':
            control.checked = Boolean(value);
            break;
        case 'number':
            control.value = value === null || value === undefined ? '' : String(value);
            break;
        case 'string_list':
            // For keyword tags UI, we store keywords in a data attribute
            if (control.dataset.keywordTags !== undefined) {
                const keywords = Array.isArray(value) ? value : [];
                control.dataset.keywordTags = JSON.stringify(keywords);
                renderKeywordTags(control, keywords);
            } else {
                // Fallback for textarea (shouldn't happen with new implementation)
                control.value = Array.isArray(value) ? value.join('\n') : value ?? '';
            }
            break;
        case 'access_rules':
            if (control.dataset.accessRules !== undefined) {
                const rules = Array.isArray(value) ? value : [];
                control.dataset.accessRules = JSON.stringify(rules);
                renderAccessRules(control, rules);
            }
            break;
        case 'context_files':
            if (control.dataset.contextFiles !== undefined) {
                const fileIds = Array.isArray(value) ? value : [];
                control.dataset.contextFiles = JSON.stringify(fileIds);
                renderContextFiles(control, fileIds);
            }
            break;
        case 'boolean_map':
            if (control.dataset.booleanMap !== undefined) {
                const normalized = normalizeBooleanMapValue(field, value);
                control.dataset.booleanMap = JSON.stringify(normalized);
                // Boolean maps are presented as a single multi-select. Keep a
                // small compatibility branch for older callers that still
                // provide the former group of checkbox controls.
                if (control.tagName === 'SELECT' && control.multiple) {
                    Array.from(control.options).forEach((option) => {
                        option.selected = Boolean(normalized[option.value]);
                    });
                    control._multiSelect?.syncFromSelect?.();
                } else {
                    control.querySelectorAll('.boolean-map-input').forEach((input) => {
                        input.checked = Boolean(normalized[input.dataset.mapKey]);
                    });
                }
            }
            break;
        case 'json': {
            const jsonValue = value === null || value === undefined
                ? field?.default
                : value;
            control.value = JSON.stringify(
                jsonValue && typeof jsonValue === 'object' ? jsonValue : {},
                null,
                2,
            );
            break;
        }
        case 'llm_access_permissions':
            if (control.dataset.llmAccessPermissions !== undefined) {
                const LLM_FIELDS = ['first_name', 'language', 'country', 'timezone', 'location'];
                let permissions = {};
                let preset = typeof field?.preset_value === 'string' ? field.preset_value : 'none';

                if (value && typeof value === 'object' && !Array.isArray(value)) {
                    LLM_FIELDS.forEach(f => {
                        permissions[f] = Boolean(value[f]);
                    });
                    if (!['none', 'all', 'custom'].includes(preset)) {
                        const allTrue = LLM_FIELDS.every(f => permissions[f]);
                        const allFalse = LLM_FIELDS.every(f => !permissions[f]);
                        if (allTrue) preset = 'all';
                        else if (allFalse) preset = 'none';
                        else preset = 'custom';
                    }
                } else if (value === true) {
                    LLM_FIELDS.forEach(f => permissions[f] = true);
                    preset = 'all';
                } else {
                    LLM_FIELDS.forEach(f => permissions[f] = false);
                    preset = 'none';
                }

                control.dataset.llmAccessPermissions = JSON.stringify(permissions);
                control.dataset.llmAccessPreset = preset;

                control.querySelectorAll('.llm-mode-btn').forEach(btn => {
                    btn.classList.toggle('active', btn.dataset.mode === preset);
                });

                const fieldsContainer = control.querySelector('.llm-access-fields');
                if (fieldsContainer) {
                    fieldsContainer.style.display = preset === 'custom' ? 'grid' : 'none';
                    fieldsContainer.querySelectorAll('.llm-field-input').forEach(input => {
                        input.checked = Boolean(permissions[input.dataset.field]);
                    });
                }
            }
            break;
        case 'select':
            if (control.multiple) {
                const desiredValues = new Set(
                    Array.isArray(value)
                        ? value.map((entry) => String(entry))
                        : value !== undefined && value !== null
                            ? [String(value)]
                            : []
                );
                Array.from(control.options).forEach((option) => {
                    option.selected = desiredValues.has(option.value);
                });
                if (control._multiSelect?.syncFromSelect) {
                    control._multiSelect.syncFromSelect();
                }
            } else {
                // An absent schema value is an actual empty selection. Leaving
                // the native select untouched lets the browser silently keep
                // (or promote) its first real option, which makes provider-backed
                // model fields look configured even though no model was saved.
                control.value = value === undefined || value === null
                    ? ''
                    : String(value);
                if (control._singleSelect?.syncFromSelect) {
                    control._singleSelect.syncFromSelect();
                }
            }
            break;
        case 'textarea':
            control.value = value ?? '';
            // Update character counter if present
            const counter = control.parentElement?.querySelector('.textarea-counter');
            if (counter && field?.max_length) {
                counter.textContent = `${(value || '').length}/${field.max_length}`;
            }
            break;
        case 'string':
        default:
            control.value = value ?? '';
            break;
    }
}

if (typeof window !== 'undefined') {
    // Page-specific settings affordances (for example an explicit policy-clear
    // action) can update a schema control through the same normalization/render
    // path used by the shared settings controller.
    window.setAdminSchemaControlValue = applyControlValue;
}

function getBooleanMapItems(field, value) {
    const metadataItems = Array.isArray(field?.metadata?.items) ? field.metadata.items : [];
    const defaultKeys = field?.default && typeof field.default === 'object' && !Array.isArray(field.default)
        ? Object.keys(field.default)
        : [];
    const valueKeys = value && typeof value === 'object' && !Array.isArray(value)
        ? Object.keys(value)
        : [];
    const keys = [...new Set([
        ...metadataItems.map((item) => String(item?.key || '').trim()).filter(Boolean),
        ...defaultKeys,
        ...valueKeys,
    ])];

    return keys.map((key) => {
        const item = metadataItems.find((candidate) => String(candidate?.key || '') === key) || {};
        const fallbackLabel = item.label || key.replace(/[_-]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
        const fallbackDescription = item.description || '';
        return {
            key,
            label: item.i18n_label ? helperT(item.i18n_label, fallbackLabel) : fallbackLabel,
            description: item.i18n_description ? helperT(item.i18n_description, fallbackDescription) : fallbackDescription,
        };
    });
}

function normalizeBooleanMapValue(field, value) {
    const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const defaults = field?.default && typeof field.default === 'object' && !Array.isArray(field.default)
        ? field.default
        : {};
    const normalized = {};

    getBooleanMapItems(field, source).forEach((item) => {
        if (Object.prototype.hasOwnProperty.call(source, item.key)) {
            normalized[item.key] = Boolean(source[item.key]);
        } else {
            normalized[item.key] = Boolean(defaults[item.key]);
        }
    });

    return normalized;
}

function parseBooleanMapDataset(control) {
    try {
        return JSON.parse(control.dataset.booleanMap || '{}');
    } catch {
        return {};
    }
}

function dispatchBooleanMapChange(control) {
    const value = normalizeBooleanMapValue({ default: {} }, parseBooleanMapDataset(control));
    control.dispatchEvent(new CustomEvent('booleanmapchange', {
        detail: { value },
    }));
}

/**
 * Render a string-list control.
 *
 * Most string lists remain compact tags. Fields marked as ordered use full
 * rows with keyboard-accessible move controls because their position carries
 * meaning (for example, the first public URL is the canonical URL).
 */
function renderKeywordTags(container, keywords, focusRequest = null) {
    const listEl = container.querySelector('.keyword-tags-list');
    if (!listEl) return;

    listEl.innerHTML = '';
    const isOrdered = container.dataset.orderedList === 'true';
    let elementToFocus = null;
    if (isOrdered) {
        listEl.setAttribute('role', 'list');
    } else {
        listEl.removeAttribute?.('role');
    }

    if (!keywords || keywords.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'keyword-tags-empty';
        empty.textContent = helperT('admin_keywords_empty', 'No keywords added yet.');
        listEl.appendChild(empty);
        if (focusRequest) {
            container.querySelector('.keyword-tags-input')?.focus?.();
        }
        return;
    }

    const persistKeywords = (updatedKeywords) => {
        container.dataset.keywordTags = JSON.stringify(updatedKeywords);
        container.dispatchEvent(new CustomEvent('keywordschange', {
            detail: { keywords: updatedKeywords }
        }));
    };

    keywords.forEach((keyword, index) => {
        const tag = document.createElement('div');
        tag.className = 'keyword-tag';
        if (isOrdered) {
            tag.classList.add('keyword-tag-ordered');
            tag.setAttribute('role', 'listitem');
        }

        const value = document.createElement('span');
        value.className = 'keyword-tag-value';
        value.textContent = String(keyword ?? '');
        tag.appendChild(value);

        if (isOrdered && index === 0) {
            const primaryBadge = document.createElement('span');
            primaryBadge.className = 'keyword-tag-primary';
            primaryBadge.textContent = helperT('admin_public_url_primary', 'Primary');
            tag.appendChild(primaryBadge);
        }

        const actions = document.createElement('span');
        actions.className = 'keyword-tag-actions';

        if (isOrdered) {
            const createMoveButton = ({ direction, icon, disabled, targetIndex }) => {
                const labelKey = direction === 'up'
                    ? 'admin_public_url_move_up_aria'
                    : 'admin_public_url_move_down_aria';
                const fallback = direction === 'up'
                    ? 'Move {url} up'
                    : 'Move {url} down';
                const button = document.createElement('button');
                button.type = 'button';
                button.className = `keyword-tag-action keyword-tag-move-${direction}`;
                button.disabled = disabled;
                const label = helperFormatT(labelKey, fallback, { url: keyword });
                button.setAttribute('aria-label', label);
                button.setAttribute('title', label);
                button.innerHTML = getAdminIconMarkup(icon);
                button.addEventListener('click', () => {
                    const currentKeywords = JSON.parse(container.dataset.keywordTags || '[]');
                    if (
                        index < 0
                        || index >= currentKeywords.length
                        || targetIndex < 0
                        || targetIndex >= currentKeywords.length
                    ) {
                        return;
                    }
                    [currentKeywords[index], currentKeywords[targetIndex]] = [
                        currentKeywords[targetIndex],
                        currentKeywords[index],
                    ];
                    container.dataset.keywordTags = JSON.stringify(currentKeywords);
                    renderKeywordTags(container, currentKeywords, {
                        index: targetIndex,
                        action: `move-${direction}`,
                    });
                    persistKeywords(currentKeywords);
                });
                if (
                    focusRequest?.index === index
                    && focusRequest?.action === `move-${direction}`
                ) {
                    elementToFocus = button;
                }
                return button;
            };

            actions.append(
                createMoveButton({
                    direction: 'up',
                    icon: 'chevronTop',
                    disabled: index === 0,
                    targetIndex: index - 1,
                }),
                createMoveButton({
                    direction: 'down',
                    icon: 'chevron',
                    disabled: index === keywords.length - 1,
                    targetIndex: index + 1,
                })
            );
        }

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'keyword-tag-action keyword-tag-remove';
        removeBtn.setAttribute(
            'aria-label',
            helperFormatT('admin_keyword_remove_aria', 'Remove {keyword}', { keyword })
        );
        removeBtn.innerHTML = getAdminIconMarkup('close');
        actions.appendChild(removeBtn);

        removeBtn.addEventListener('click', () => {
            const currentKeywords = JSON.parse(container.dataset.keywordTags || '[]');
            // Values are unique, but removing by index preserves deterministic
            // focus and avoids surprising behavior if imported data is not.
            const updatedKeywords = currentKeywords.filter((_entry, itemIndex) => itemIndex !== index);
            container.dataset.keywordTags = JSON.stringify(updatedKeywords);
            renderKeywordTags(container, updatedKeywords, isOrdered ? {
                index: Math.min(index, updatedKeywords.length - 1),
                action: 'remove',
            } : null);
            persistKeywords(updatedKeywords);
        });

        if (
            focusRequest?.index === index
            && focusRequest?.action === 'remove'
        ) {
            elementToFocus = removeBtn;
        }
        tag.appendChild(actions);
        listEl.appendChild(tag);
    });

    elementToFocus?.focus?.();
}

const CONTEXT_FILE_TYPE_ICONS = {
    'application/pdf': 'pdf.svg',
    'image/png': 'png.svg',
    'image/jpeg': 'jpg.svg',
    'image/jpg': 'jpg.svg',
    'image/gif': 'gif.svg',
    'text/plain': 'txt.svg',
    'text/html': 'html.svg',
    'text/css': 'css.svg',
    'application/javascript': 'js.svg',
    'text/javascript': 'js.svg',
    'application/json': 'js.svg',
    'application/vnd.ms-excel': 'xls.svg',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xls.svg',
    'application/vnd.ms-powerpoint': 'ppt.svg',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'ppt.svg',
    'application/msword': 'txt.svg',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'txt.svg',
};

function getContextFileIcon(fileType) {
    return CONTEXT_FILE_TYPE_ICONS[fileType] || 'txt.svg';
}

function formatContextFileSize(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return '';
    if (bytes >= 1024 * 1024) {
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }
    if (bytes >= 1024) {
        return `${Math.round(bytes / 1024)} KB`;
    }
    return `${Math.round(bytes)} B`;
}

async function renderContextFiles(container, fileIds) {
    const listEl = container.querySelector('.context-files-list');
    if (!listEl) return;

    listEl.innerHTML = '';

    // No files - show nothing (empty state is handled by dropzone)
    if (!fileIds || fileIds.length === 0) {
        return;
    }

    // Show loading state
    const loadingEl = createAdminLoadingPlaceholder({
        message: helperT('admin_context_files_loading', 'Loading files...'),
        className: '',
    });
    listEl.appendChild(loadingEl);

    try {
        const response = await window.authedFetch('/api/v1/files/by-ids', {
            method: 'POST',
            body: JSON.stringify({ file_ids: fileIds }),
        });

        if (!response.ok) {
            listEl.innerHTML = `
                <div class="context-files-error">
                    ${getAdminIconMarkup('error')}
                    ${helperT('admin_context_files_load_failed', 'Failed to load files')}
                </div>
            `;
            return;
        }

        const files = await response.json();
        listEl.innerHTML = '';

        if (!files || files.length === 0) {
            return;
        }

        // File count header
        const countEl = document.createElement('div');
        countEl.className = 'context-files-count';
        countEl.innerHTML = `
            <span class="context-files-count-text">${helperT('admin_context_files_uploaded', 'Uploaded files')}</span>
            <span class="context-files-count-badge">${files.length}</span>
        `;
        listEl.appendChild(countEl);

        files.forEach((file) => {
            const fileId = file.file_id || file.id;
            const fileName = file.meta?.original_filename || file.file_name || 'Unknown file';
            const fileType = file.file_type || '';
            const fileSize = file.file_size || 0;
            const iconName = getContextFileIcon(fileType);

            const item = document.createElement('div');
            item.className = 'context-files-item';
            item.dataset.fileId = fileId;

            const iconWrapper = document.createElement('div');
            iconWrapper.className = 'context-files-item-icon';
            const iconImg = document.createElement('img');
            iconImg.src = `/assets/file_svgs/${iconName}`;
            iconImg.alt = fileType.split('/').pop()?.toUpperCase() || 'FILE';
            iconImg.width = 28;
            iconImg.height = 28;
            iconImg.loading = 'lazy';
            iconWrapper.appendChild(iconImg);

            const content = document.createElement('div');
            content.className = 'context-files-item-content';

            const nameEl = document.createElement('div');
            nameEl.className = 'context-files-item-name';
            nameEl.textContent = fileName;
            nameEl.title = fileName;

            const metaEl = document.createElement('div');
            metaEl.className = 'context-files-item-meta';
            
            // File extension
            if (fileType) {
                const ext = fileType.split('/').pop();
                if (ext) {
                    const extSpan = document.createElement('span');
                    extSpan.textContent = ext.toUpperCase();
                    metaEl.appendChild(extSpan);
                }
            }
            
            // Separator and size
            if (fileSize) {
                if (metaEl.children.length > 0) {
                    const sep = document.createElement('span');
                    sep.className = 'context-files-item-meta-separator';
                    metaEl.appendChild(sep);
                }
                const sizeSpan = document.createElement('span');
                sizeSpan.textContent = formatContextFileSize(fileSize);
                metaEl.appendChild(sizeSpan);
            }

            content.appendChild(nameEl);
            content.appendChild(metaEl);

            const removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.className = 'context-files-item-remove';
            removeBtn.title = helperT('admin_context_file_remove_title', 'Remove file');
            removeBtn.setAttribute('aria-label', helperT('admin_context_file_remove_title', 'Remove file'));
            removeBtn.innerHTML = getAdminIconMarkup('close');
            removeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                // Animate removal
                item.style.opacity = '0';
                item.style.transform = 'translateX(10px)';
                item.style.transition = 'opacity 0.15s ease, transform 0.15s ease';
                
                setTimeout(() => {
                    const currentIds = JSON.parse(container.dataset.contextFiles || '[]');
                    const updatedIds = currentIds.filter((id) => id !== fileId);
                    container.dataset.contextFiles = JSON.stringify(updatedIds);
                    renderContextFiles(container, updatedIds);
                    container.dispatchEvent(new CustomEvent('contextfileschange', {
                        detail: { fileIds: updatedIds }
                    }));
                }, 150);
            });

            item.appendChild(iconWrapper);
            item.appendChild(content);
            item.appendChild(removeBtn);
            listEl.appendChild(item);
        });
    } catch (error) {
        console.error('Failed to load context files:', error);
        listEl.innerHTML = `
            <div class="context-files-error">
                ${getAdminIconMarkup('error')}
                ${helperT('admin_context_files_load_failed', 'Failed to load files')}
            </div>
        `;
    }
}

function applyAccessRulesPresetMode(control, accessMode) {
    if (!control || !accessMode) {
        return;
    }

    const rulesFieldName = control.name || control.dataset?.settingKey || '';
    const modeFieldName = rulesFieldName.endsWith('.rules')
        ? `${rulesFieldName.slice(0, -'.rules'.length)}.mode`
        : 'settings.access_windows.mode';
    const scope = control.closest?.('form') || document;
    const fields = Array.from(scope.querySelectorAll?.('select, input, textarea') || []);
    const modeControl = fields.find((field) => field.name === modeFieldName || field.dataset?.settingKey === modeFieldName);

    if (!modeControl || modeControl.value === accessMode) {
        return;
    }

    modeControl.value = accessMode;
    modeControl.dispatchEvent(new Event('change', { bubbles: true }));
}

function renderAccessRules(container, rules, dayLabels = [
    helperT('admin_day_mon_short', 'Mon'),
    helperT('admin_day_tue_short', 'Tue'),
    helperT('admin_day_wed_short', 'Wed'),
    helperT('admin_day_thu_short', 'Thu'),
    helperT('admin_day_fri_short', 'Fri'),
    helperT('admin_day_sat_short', 'Sat'),
    helperT('admin_day_sun_short', 'Sun')
]) {
    const listEl = container.querySelector('.access-rules-list');
    if (!listEl) return;

    listEl.innerHTML = '';

    if (!rules || rules.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'access-rules-empty';
        empty.innerHTML = `
            ${getAdminIconMarkup('clock')}
            <div class="access-rules-empty-text">${helperT('admin_access_rules_empty', 'No access rules defined yet')}</div>
            <div class="access-rules-empty-hint">${helperT('admin_access_rules_empty_hint', 'Add a rule or use a preset above to get started')}</div>
        `;
        listEl.appendChild(empty);
        return;
    }

    rules.forEach((rule, index) => {
        const item = document.createElement('div');
        item.className = 'access-rule-item';
        item.dataset.ruleIndex = index;

        // Times wrapper for responsive layout
        const timesWrapper = document.createElement('div');
        timesWrapper.className = 'access-rule-times-wrapper';

        // Start time
        const startGroup = document.createElement('div');
        startGroup.className = 'access-rule-time-group';
        const startLabel = document.createElement('div');
        startLabel.className = 'access-rule-time-label';
        startLabel.textContent = helperT('admin_access_rule_start', 'Start');
        const startInput = document.createElement('input');
        startInput.type = 'time';
        startInput.className = 'access-rule-time-input';
        startInput.value = rule.start || '09:00';
        startInput.setAttribute('aria-label', helperT('admin_access_rule_start_time_aria', 'Start time'));
        startInput.addEventListener('change', () => {
            updateRuleField(container, index, 'start', startInput.value);
            updateTimelineVisualization(item, startInput.value, endInput.value);
        });
        startGroup.append(startLabel, startInput);
        timesWrapper.appendChild(startGroup);

        // Arrow connector
        const connector = document.createElement('div');
        connector.className = 'access-rule-time-connector';
        connector.innerHTML = getAdminIconMarkup('arrow_right');
        timesWrapper.appendChild(connector);

        // End time
        const endGroup = document.createElement('div');
        endGroup.className = 'access-rule-time-group';
        const endLabel = document.createElement('div');
        endLabel.className = 'access-rule-time-label';
        endLabel.textContent = helperT('admin_access_rule_end', 'End');
        const endInput = document.createElement('input');
        endInput.type = 'time';
        endInput.className = 'access-rule-time-input';
        endInput.value = rule.end || '17:00';
        endInput.setAttribute('aria-label', helperT('admin_access_rule_end_time_aria', 'End time'));
        endInput.addEventListener('change', () => {
            updateRuleField(container, index, 'end', endInput.value);
            updateTimelineVisualization(item, startInput.value, endInput.value);
        });
        endGroup.append(endLabel, endInput);
        timesWrapper.appendChild(endGroup);

        item.appendChild(timesWrapper);

        // Days
        const daysContainer = document.createElement('div');
        daysContainer.className = 'access-rule-days';
        daysContainer.setAttribute('role', 'group');
        daysContainer.setAttribute('aria-label', helperT('admin_access_rule_days', 'Days of the week'));
        
        dayLabels.forEach((dayLabel, dayIndex) => {
            const dayBtn = document.createElement('button');
            dayBtn.type = 'button';
            dayBtn.className = 'access-rule-day-btn';
            // Mark weekend days
            if (dayIndex === 5 || dayIndex === 6) {
                dayBtn.dataset.weekend = 'true';
            }
            if (Array.isArray(rule.days) && rule.days.includes(dayIndex)) {
                dayBtn.classList.add('active');
                dayBtn.setAttribute('aria-pressed', 'true');
            } else {
                dayBtn.setAttribute('aria-pressed', 'false');
            }
            dayBtn.textContent = dayLabel;
            dayBtn.setAttribute('title', getFullDayName(dayIndex));
            dayBtn.addEventListener('click', () => {
                dayBtn.classList.toggle('active');
                const isActive = dayBtn.classList.contains('active');
                dayBtn.setAttribute('aria-pressed', isActive.toString());
                
                const currentRules = JSON.parse(container.dataset.accessRules || '[]');
                const currentDays = Array.isArray(currentRules[index]?.days) ? [...currentRules[index].days] : [];
                if (isActive) {
                    if (!currentDays.includes(dayIndex)) currentDays.push(dayIndex);
                } else {
                    const pos = currentDays.indexOf(dayIndex);
                    if (pos > -1) currentDays.splice(pos, 1);
                }
                currentDays.sort((a, b) => a - b);
                currentRules[index].days = currentDays;
                container.dataset.accessRules = JSON.stringify(currentRules);
                container.dispatchEvent(new CustomEvent('ruleschange', { detail: { rules: currentRules } }));
            });
            daysContainer.appendChild(dayBtn);
        });
        item.appendChild(daysContainer);

        // Footer row with label and remove button
        const footerRow = document.createElement('div');
        footerRow.className = 'access-rule-footer-row';

        // Label input
        const labelInput = document.createElement('input');
        labelInput.type = 'text';
        labelInput.className = 'access-rule-label-input';
        labelInput.placeholder = helperT('admin_rule_label_placeholder', 'Rule label (optional)');
        labelInput.value = rule.label || '';
        labelInput.setAttribute('aria-label', helperT('admin_rule_label_aria', 'Rule description or label'));
        labelInput.addEventListener('input', () => updateRuleField(container, index, 'label', labelInput.value));
        footerRow.appendChild(labelInput);

        // Remove button
        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'access-rule-remove-btn';
        removeBtn.setAttribute('title', helperT('admin_access_rule_remove', 'Remove this rule'));
        removeBtn.setAttribute('aria-label', helperT('admin_access_rule_remove', 'Remove this rule'));
        removeBtn.innerHTML = getAdminIconMarkup('close');
        removeBtn.addEventListener('click', () => {
            // Update data immediately to avoid stale index on rapid clicks
            const currentRules = JSON.parse(container.dataset.accessRules || '[]');
            currentRules.splice(index, 1);
            container.dataset.accessRules = JSON.stringify(currentRules);
            
            // Add fade-out animation, then re-render
            item.style.opacity = '0';
            item.style.transform = 'translateX(20px)';
            item.style.transition = 'all 0.2s ease';
            listEl.style.pointerEvents = 'none';
            
            setTimeout(() => {
                listEl.style.pointerEvents = '';
                renderAccessRules(container, currentRules, dayLabels);
                container.dispatchEvent(new CustomEvent('ruleschange', { detail: { rules: currentRules } }));
            }, 200);
        });
        footerRow.appendChild(removeBtn);

        item.appendChild(footerRow);

        // Visual timeline bar
        const timeline = document.createElement('div');
        timeline.className = 'access-rule-timeline';
        const timelineFill = document.createElement('div');
        timelineFill.className = 'access-rule-timeline-fill';
        timeline.appendChild(timelineFill);
        
        const timelineLabels = document.createElement('div');
        timelineLabels.className = 'access-rule-timeline-labels';
        timelineLabels.innerHTML = '<span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span>';
        
        item.appendChild(timeline);
        item.appendChild(timelineLabels);

        // Initialize timeline visualization
        updateTimelineVisualization(item, rule.start || '09:00', rule.end || '17:00');

        listEl.appendChild(item);
    });
}

function parseTimeToMinutes(timeStr) {
    if (!timeStr) return 0;
    const [hours, minutes] = timeStr.split(':').map(Number);
    return hours * 60 + (minutes || 0);
}

function updateTimelineVisualization(ruleItem, startTime, endTime) {
    const timelineFill = ruleItem.querySelector('.access-rule-timeline-fill');
    if (!timelineFill) return;
    
    const startMinutes = parseTimeToMinutes(startTime);
    const endMinutes = parseTimeToMinutes(endTime);
    const totalMinutes = 24 * 60;
    
    const startPercent = (startMinutes / totalMinutes) * 100;
    
    // Handle overnight rules (end < start)
    if (endMinutes <= startMinutes) {
        // Rule spans overnight - show two segments
        const endPercent = (endMinutes / totalMinutes) * 100;
        const widthToMidnight = 100 - startPercent;
        
        timelineFill.style.left = `${startPercent}%`;
        timelineFill.style.width = `${widthToMidnight}%`;
        timelineFill.style.background = 'linear-gradient(90deg, var(--admin-info) 0%, var(--admin-accent) 100%)';
        
        // Add second fill for overnight portion if needed
        let secondFill = ruleItem.querySelector('.access-rule-timeline-fill-overnight');
        if (!secondFill && endPercent > 0) {
            secondFill = document.createElement('div');
            secondFill.className = 'access-rule-timeline-fill access-rule-timeline-fill-overnight';
            secondFill.style.left = '0%';
            secondFill.style.width = `${endPercent}%`;
            secondFill.style.background = 'linear-gradient(90deg, var(--admin-accent) 0%, var(--admin-info) 100%)';
            const timeline = ruleItem.querySelector('.access-rule-timeline');
            if (timeline) timeline.appendChild(secondFill);
        } else if (secondFill) {
            secondFill.style.width = `${endPercent}%`;
        }
    } else {
        // Normal same-day rule
        const widthPercent = ((endMinutes - startMinutes) / totalMinutes) * 100;
        timelineFill.style.left = `${startPercent}%`;
        timelineFill.style.width = `${widthPercent}%`;
        timelineFill.style.background = 'var(--admin-info)';
        
        // Remove overnight fill if it exists
        const secondFill = ruleItem.querySelector('.access-rule-timeline-fill-overnight');
        if (secondFill) secondFill.remove();
    }
}

function getFullDayName(dayIndex) {
    const days = [
        helperT('admin_day_monday', 'Monday'),
        helperT('admin_day_tuesday', 'Tuesday'),
        helperT('admin_day_wednesday', 'Wednesday'),
        helperT('admin_day_thursday', 'Thursday'),
        helperT('admin_day_friday', 'Friday'),
        helperT('admin_day_saturday', 'Saturday'),
        helperT('admin_day_sunday', 'Sunday')
    ];
    return days[dayIndex] || '';
}

function updateRuleField(container, index, field, value) {
    const currentRules = JSON.parse(container.dataset.accessRules || '[]');
    if (currentRules[index]) {
        currentRules[index][field] = value;
        container.dataset.accessRules = JSON.stringify(currentRules);
        container.dispatchEvent(new CustomEvent('ruleschange', { detail: { rules: currentRules } }));
    }
}

function applyInputPlaceholder(control, placeholder) {
    if (!control || typeof placeholder !== 'string' || !placeholder) {
        return;
    }
    if (control.tagName === 'INPUT' || control.tagName === 'TEXTAREA') {
        control.placeholder = placeholder;
    }
}

function getFieldPlaceholder(field, fallback = '') {
    if (!field) {
        return fallback;
    }
    const maskedMarker = getMaskedFieldSubmissionMarker(field);
    if (maskedMarker !== null) {
        // Backend-generated secret markers are display data, not translatable
        // copy. They must win over a generic translated "enter a key" prompt.
        return maskedMarker;
    }
    if (field.i18n_placeholder) {
        return helperT(field.i18n_placeholder, field.placeholder || fallback);
    }
    return field.placeholder || fallback;
}

function getFieldLabel(field, fallback = '') {
    const labelFallback = field?.label ?? field?.key ?? fallback;
    return field?.i18n_label
        ? helperT(field.i18n_label, labelFallback)
        : labelFallback;
}

