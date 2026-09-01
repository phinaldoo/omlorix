function normalizeFieldValue(field, rawValue) {
    let fieldType = field.type;
    // Special case: detect LLM access permissions field by key name
    if (field?.key === 'allow_llm_to_access_personal_information') {
        fieldType = 'llm_access_permissions';
    }
    switch (fieldType) {
        case 'boolean':
            return Boolean(rawValue);
        case 'number': {
            if (rawValue === '' || rawValue === null || rawValue === undefined) {
                return null;
            }
            const parsed = Number(rawValue);
            if (Number.isNaN(parsed)) {
                notifyError(helperT('admin_field_must_be_valid_number', '{field} must be a valid number.').replace('{field}', field.label || field.key));
                return null;
            }
            return parsed;
        }
        case 'string_list':
            if (Array.isArray(rawValue)) {
                return rawValue.map((value) => String(value).trim()).filter(Boolean);
            }
            if (typeof rawValue !== 'string') {
                return [];
            }
            return rawValue
                .split('\n')
                .map((line) => line.trim())
                .filter((line) => line.length > 0);
        case 'context_files':
            if (Array.isArray(rawValue)) {
                return rawValue.map((value) => String(value).trim()).filter(Boolean);
            }
            return [];
        case 'boolean_map':
            return normalizeBooleanMapValue(field, rawValue);
        case 'json': {
            if (rawValue && typeof rawValue === 'object') {
                return cloneSettingsValue(rawValue);
            }
            try {
                const parsed = JSON.parse(String(rawValue || ''));
                if (!parsed || typeof parsed !== 'object') {
                    throw new Error('json_root_type');
                }
                return parsed;
            } catch {
                throw new Error(helperT(
                    'admin_json_editor_invalid',
                    '{field} must contain valid JSON.',
                ).replace('{field}', field.label || field.key));
            }
        }
        case 'llm_access_permissions':
            if (rawValue && typeof rawValue === 'object' && !Array.isArray(rawValue)) {
                return cloneSettingsValue(rawValue);
            }
            return {};
        case 'select':
            if (field.multiple) {
                if (Array.isArray(rawValue)) {
                    return rawValue.map((value) => String(value).trim()).filter(Boolean);
                }
                if (rawValue === null || rawValue === undefined || rawValue === '') {
                    return [];
                }
                return [String(rawValue).trim()].filter(Boolean);
            }
        case 'string':
        default:
            return typeof rawValue === 'string' ? rawValue.trim() : rawValue ?? '';
    }
}

function createSettingsRow(field, initialValue, { onSubmit, debounceMs = 300 } = {}) {
    const { row, controlWrapper } = createFieldLayout(field);
    row.dataset.settingKey = field.key;

    const listeners = [];
    const cleanupCallbacks = [];

    const addListener = (target, event, handler) => {
        target.addEventListener(event, handler);
        listeners.push(() => target.removeEventListener(event, handler));
    };

    const { root, control } = createFieldControl(field, { value: initialValue });
    controlWrapper.appendChild(root);
    let debouncedSubmit = null;

    const controller = {
        key: field.key,
        type: field.type,
        element: row,
        control,
        setValue: (value) => applyControlValue(control, field, value),
        setDisabled: (disabled) => {
            if (control instanceof HTMLElement) {
                control.disabled = Boolean(disabled);
            }
        },
        setPending: (pending) => {
            row.classList.toggle('is-updating', Boolean(pending));
        },
        destroy: () => {
            listeners.forEach((remove) => remove());
            cleanupCallbacks.forEach((fn) => fn());
            if (debouncedSubmit?.cancel) {
                debouncedSubmit.cancel();
            }
        },
    };

    const submitValue = (value) => {
        if (typeof onSubmit === 'function') {
            try {
                const normalized = normalizeFieldValue(field, value);
                onSubmit(normalized, controller);
            } catch (error) {
                notifyError?.(
                    error.message || helperT('admin_validation_failed', 'Validation failed.')
                );
                controller.setValue(initialValue);
            }
        }
    };

    switch (field.type) {
        case 'boolean':
            addListener(control, 'change', () => submitValue(control.checked));
            break;

        case 'select':
            addListener(control, 'change', () => {
                if (field.multiple) {
                    submitValue(Array.from(control.selectedOptions || [], (option) => option.value));
                    return;
                }
                submitValue(control.value);
            });
            break;

        case 'number':
            addListener(control, 'change', () => submitValue(control.value));
            break;

        case 'string_list':
            // For keyword tags UI, listen to the custom keywordschange event
            addListener(control, 'keywordschange', (e) => {
                const keywords = e.detail?.keywords || [];
                submitValue(keywords);
            });
            break;

        case 'boolean_map':
            addListener(control, 'booleanmapchange', (e) => {
                submitValue(e.detail?.value || {});
            });
            break;

        case 'textarea':
            debouncedSubmit = createDebounced(() => submitValue(control.value), debounceMs);
            addListener(control, 'input', () => debouncedSubmit());
            addListener(control, 'blur', () => {
                debouncedSubmit?.cancel();
                submitValue(control.value);
            });
            break;

        case 'json':
            // JSON is frequently invalid while an administrator is still typing.
            // Validate and save at an explicit completion boundary so partially
            // entered documents are not rejected and reset by the debounce timer.
            addListener(control, 'blur', () => submitValue(control.value));
            addListener(control, 'keydown', (event) => {
                if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                    event.preventDefault();
                    submitValue(control.value);
                }
            });
            break;

        case 'string':
        default:
            debouncedSubmit = createDebounced(() => submitValue(control.value), debounceMs);
            addListener(control, 'input', () => debouncedSubmit());
            addListener(control, 'blur', () => {
                debouncedSubmit?.cancel();
                submitValue(control.value);
            });
            break;
    }

    return { element: row, controller };
}


function cloneSettingsValue(value) {
    if (Array.isArray(value)) {
        return value.map((entry) => cloneSettingsValue(entry));
    }
    if (value && typeof value === 'object') {
        return Object.fromEntries(
            Object.entries(value).map(([key, entry]) => [key, cloneSettingsValue(entry)])
        );
    }
    return value;
}

function createSettingsStatus(statusEl) {
    const update = (kind, message) => {
        if (!kind || !message) {
            return;
        }

        if (kind === 'success') {
            window.notifySuccess?.(message);
            return;
        }
        if (kind === 'warning') {
            window.notifyWarning?.(message);
            return;
        }
        if (kind === 'error') {
            window.notifyError?.(message);
        }
    };

    return {
        show(kind, message) {
            update(kind, message);
        },
        clear() {
            update(null, '');
        },
    };
}

function normalizeAccessRulesForComparison(value) {
    if (value == null) {
        return [];
    }
    if (!Array.isArray(value)) {
        return null;
    }
    return value.map((rule) => {
        const days = Array.isArray(rule?.days)
            ? rule.days
                .map((day) => Number(day))
                .filter((day) => Number.isInteger(day) && day >= 0 && day <= 6)
                .sort((a, b) => a - b)
            : [];
        return {
            start: typeof rule?.start === 'string' ? rule.start : '',
            end: typeof rule?.end === 'string' ? rule.end : '',
            days,
            label: typeof rule?.label === 'string' ? rule.label : '',
        };
    });
}

function valuesAreEqual(field, nextValue, previousValue) {
    let fieldType = field.type;
    // Special case: detect LLM access permissions field by key name
    if (field?.key === 'allow_llm_to_access_personal_information') {
        fieldType = 'llm_access_permissions';
    }

    const isRedactedMaskedPassword =
        (fieldType === 'string' || fieldType === 'textarea')
        && String(field?.input_type || '').toLowerCase() === 'password'
        && field?.redact_value === true
        && field?.masked_placeholder === true;

    if (
        isRedactedMaskedPassword
        && nextValue === ''
        && (previousValue === '' || previousValue === null || previousValue === undefined)
    ) {
        return true;
    }

    if (fieldType === 'boolean_map') {
        const normalizedNext = normalizeBooleanMapValue(field, nextValue);
        const normalizedPrevious = normalizeBooleanMapValue(field, previousValue);
        const keys = new Set([...Object.keys(normalizedNext), ...Object.keys(normalizedPrevious)]);
        for (const key of keys) {
            if (Boolean(normalizedNext[key]) !== Boolean(normalizedPrevious[key])) {
                return false;
            }
        }
        return true;
    }

    if (fieldType === 'access_rules') {
        const normalizedNext = normalizeAccessRulesForComparison(nextValue);
        const normalizedPrevious = normalizeAccessRulesForComparison(previousValue);
        if (!normalizedNext || !normalizedPrevious) {
            return false;
        }
        return JSON.stringify(normalizedNext) === JSON.stringify(normalizedPrevious);
    }

    if (fieldType === 'json') {
        if (!nextValue || !previousValue || typeof nextValue !== 'object' || typeof previousValue !== 'object') {
            return nextValue === previousValue;
        }
        return JSON.stringify(nextValue) === JSON.stringify(previousValue);
    }

    const isArrayField =
        fieldType === 'string_list'
        || fieldType === 'context_files'
        || (fieldType === 'select' && field.multiple);

    if (isArrayField) {
        const normalizedNext = Array.isArray(nextValue)
            ? nextValue
            : (nextValue === null || nextValue === undefined ? [] : null);
        const normalizedPrevious = Array.isArray(previousValue)
            ? previousValue
            : (previousValue === null || previousValue === undefined ? [] : null);

        if (!normalizedNext || !normalizedPrevious) {
            return false;
        }
        if (normalizedNext.length !== normalizedPrevious.length) {
            return false;
        }
        return normalizedNext.every((entry, index) => entry === normalizedPrevious[index]);
    }

    switch (fieldType) {
        case 'llm_access_permissions':
            if (typeof nextValue !== 'object' || typeof previousValue !== 'object') {
                return nextValue === previousValue;
            }
            if (!nextValue || !previousValue) {
                return nextValue === previousValue;
            }
            const keys = new Set([...Object.keys(nextValue), ...Object.keys(previousValue)]);
            for (const key of keys) {
                if (Boolean(nextValue[key]) !== Boolean(previousValue[key])) {
                    return false;
                }
            }
            return true;
        case 'number':
        case 'boolean':
        case 'select':
        case 'string':
        default:
            return Object.is(nextValue, previousValue);
    }
}

function createSettingsPageController({
    pageKey,
    containerId,
    statusId,
    transformSections,
    stringDebounceMs = 600,
    stringListDebounceMs = 600,
    disableDuringRequestTypes = ['boolean', 'number', 'select'],
    loadingMessage = helperT('admin_settings_loading', 'Loading settings...'),
    loadErrorMessage = 'Unable to load settings.',
    onError,
    onFieldSaved,
    onValueChange,
    onLoad,
    onRender,
    renderEmptyState,
    renderError,
    preserveContainerChildren = false,
    insertPosition = 'append',
}) {
    const container = typeof containerId === 'string' ? document.getElementById(containerId) : containerId;
    if (!container) {
        return {
            init() { },
            reload() { },
            teardown() { },
        };
    }

    const statusEl = typeof statusId === 'string' ? document.getElementById(statusId) : statusId;
    const status = createSettingsStatus(statusEl);
    const disableTypes = new Set(disableDuringRequestTypes ?? []);
    const preserveManagedRange = Boolean(preserveContainerChildren);
    const placeManagedBefore = insertPosition === 'prepend';

    const state = {
        active: false,
        controllers: new Map(),
        values: {},
        inFlight: new Map(),
        loadToken: 0,
        sections: [],
        groups: [],
    };

    const managedRange = preserveManagedRange
        ? {
            start: document.createComment(`settings-controller:${pageKey}:start`),
            end: document.createComment(`settings-controller:${pageKey}:end`),
        }
        : null;

    const ensureManagedRange = () => {
        if (!managedRange) {
            return;
        }

        const { start, end } = managedRange;
        const hasValidRange =
            start.parentNode === container &&
            end.parentNode === container &&
            start.compareDocumentPosition(end) & Node.DOCUMENT_POSITION_FOLLOWING;
        if (hasValidRange) {
            return;
        }

        start.remove();
        end.remove();
        if (placeManagedBefore) {
            container.insertBefore(end, container.firstChild);
            container.insertBefore(start, end);
            return;
        }
        container.appendChild(start);
        container.appendChild(end);
    };

    const clearManagedRange = () => {
        if (!managedRange) {
            return;
        }
        ensureManagedRange();
        let cursor = managedRange.start.nextSibling;
        while (cursor && cursor !== managedRange.end) {
            const next = cursor.nextSibling;
            cursor.remove();
            cursor = next;
        }
    };

    const appendManagedContent = (node) => {
        if (!preserveManagedRange) {
            container.appendChild(node);
            return;
        }
        ensureManagedRange();
        container.insertBefore(node, managedRange.end);
    };

    const cleanupControllers = () => {
        state.controllers.forEach(({ controller }) => controller.destroy?.());
        state.controllers.clear();
    };

    /**
     * Check if a dependency field exists in the controllers.
     */
    const dependencyFieldExists = (dependencyKey) => {
        if (!dependencyKey) return false;
        return state.controllers.has(dependencyKey);
    };

    /**
     * Get the current value of a field by its key.
     */
    const getFieldValue = (fieldKey) => {
        const entry = state.controllers.get(fieldKey);
        if (!entry) return undefined;
        const { field, controller } = entry;
        // Get the current DOM value
        const row = controller.element;
        if (!row) return state.values[fieldKey];
        const control = field.type === 'boolean_map'
            ? row.querySelector('.boolean-map-control')
            : row.querySelector('.toggle-input, select, input, textarea, .keyword-tags-container');
        if (!control) return state.values[fieldKey];
        switch (field.type) {
            case 'boolean':
                return Boolean(control.checked);
            case 'select':
                if (field.multiple) {
                    return Array.from(control.selectedOptions || [], (option) => option.value);
                }
                return control.value;
            case 'number':
                return control.value === '' ? null : Number(control.value);
            case 'string_list':
                if (control.dataset.keywordTags !== undefined) {
                    try {
                        return JSON.parse(control.dataset.keywordTags || '[]');
                    } catch {
                        return [];
                    }
                }
                return control.value;
            case 'boolean_map':
                return parseBooleanMapDataset(control);
            case 'json':
                try {
                    return JSON.parse(control.value || '');
                } catch {
                    return state.values[fieldKey];
                }
            default:
                return control.value;
        }
    };

    const isFieldEffectivelyVisible = (fieldKey, seen = new Set()) => {
        if (!fieldKey) {
            return true;
        }
        if (!dependencyFieldExists(fieldKey)) {
            return true;
        }
        if (seen.has(fieldKey)) {
            return false;
        }

        const entry = state.controllers.get(fieldKey);
        if (!entry) {
            return true;
        }

        const nextSeen = new Set(seen);
        nextSeen.add(fieldKey);

        const { field } = entry;
        if (field.dependency && !isSingleDependencySatisfied(field.dependency, field.dependency_value, nextSeen)) {
            return false;
        }
        if (field.dependency2 && !isSingleDependencySatisfied(field.dependency2, field.dependency2_value, nextSeen)) {
            return false;
        }
        if (field.dependency3 && !isSingleDependencySatisfied(field.dependency3, field.dependency3_value, nextSeen)) {
            return false;
        }

        return true;
    };

    const isSingleDependencySatisfied = (dependencyKey, requiredValue, seen = new Set()) => {
        if (!dependencyKey) return true;
        if (!dependencyFieldExists(dependencyKey)) return true;
        if (!isFieldEffectivelyVisible(dependencyKey, seen)) return false;
        const currentValue = getFieldValue(dependencyKey);

        if (Array.isArray(requiredValue)) {
            const normalizedRequiredValues = requiredValue.map((value) => String(value));
            if (Array.isArray(currentValue)) {
                return normalizedRequiredValues.some((value) => currentValue.includes(value));
            }
            return normalizedRequiredValues.includes(String(currentValue));
        }

        // For array values (multi-select), check if required value is included
        if (Array.isArray(currentValue)) {
            return currentValue.includes(String(requiredValue));
        }
        // For boolean comparison
        if (typeof requiredValue === 'boolean') {
            return currentValue === requiredValue;
        }
        // For string/other comparison
        return String(currentValue) === String(requiredValue);
    };

    /**
     * Check if a field's dependency condition is satisfied.
     * Returns true if the field should be visible.
     */
    const isDependencySatisfied = (field) => {
        const firstSatisfied = isSingleDependencySatisfied(field.dependency, field.dependency_value);
        if (!firstSatisfied) {
            return false;
        }
        if (!isSingleDependencySatisfied(field.dependency2, field.dependency2_value)) {
            return false;
        }
        return isSingleDependencySatisfied(field.dependency3, field.dependency3_value);
    };

    /**
     * Update visibility of all dependent fields.
     */
    const updateSectionsVisibility = () => {
        state.sections.forEach((sectionMeta) => {
            const hasVisibleFields = sectionMeta.fieldKeys.some((key) => {
                const entry = state.controllers.get(key);
                const row = entry?.controller?.element;
                return row && !row.hidden && row.style.display !== 'none';
            });
            const shouldHideSection = !hasVisibleFields;
            sectionMeta.element.hidden = shouldHideSection;
            sectionMeta.element.style.display = shouldHideSection ? 'none' : '';
        });

        // A group header has no useful content when dependencies hide every
        // section it owns. Keep the wrapper in sync with its child sections so
        // administrators never see an orphaned heading or description.
        state.groups.forEach((groupMeta) => {
            const hasVisibleSections = groupMeta.sections.some(
                (sectionEl) => !sectionEl.hidden && sectionEl.style.display !== 'none'
            );
            const shouldHideGroup = !hasVisibleSections;
            groupMeta.element.hidden = shouldHideGroup;
            groupMeta.element.style.display = shouldHideGroup ? 'none' : '';
        });
    };

    const updateDependentFieldsVisibility = () => {
        state.controllers.forEach(({ field, controller }) => {
            if (!field.dependency && !field.dependency2 && !field.dependency3) return;
            const row = controller.element;
            if (!row) return;
            const visible = isDependencySatisfied(field);
            row.hidden = !visible;
            row.style.display = visible ? '' : 'none';
        });
        updateSectionsVisibility();
        syncSectionBodyLastVisibleRow(container);
    };

    /**
     * Attach change listeners to all controls that might be dependencies.
     */
    const attachDependencyListeners = () => {
        // Collect all dependency keys
        const dependencyKeys = new Set();
        state.controllers.forEach(({ field }) => {
            if (field.dependency) {
                dependencyKeys.add(field.dependency);
            }
            if (field.dependency2) {
                dependencyKeys.add(field.dependency2);
            }
            if (field.dependency3) {
                dependencyKeys.add(field.dependency3);
            }
        });
        // Attach listeners to controls that are dependencies
        state.controllers.forEach(({ field, controller }) => {
            if (!dependencyKeys.has(field.key)) return;
            const row = controller.element;
            if (!row) return;
            const control = row.querySelector('.toggle-input, select, input, textarea, .keyword-tags-container');
            if (!control) return;
            control.addEventListener('change', updateDependentFieldsVisibility);
            // For keyword tags, also listen to custom event
            if (field.type === 'string_list') {
                control.addEventListener('keywordschange', updateDependentFieldsVisibility);
            }
        });
    };

    const abortAll = () => {
        state.inFlight.forEach((entry) => entry.abortController?.abort?.());
        state.inFlight.clear();
    };

    const resetView = () => {
        cleanupControllers();
        state.sections = [];
        state.groups = [];
        if (preserveManagedRange) {
            clearManagedRange();
            return;
        }
        container.innerHTML = '';
    };

    const renderLoadingState = () => {
        resetView();
        appendManagedContent(createAdminLoadingPlaceholder({
            message: loadingMessage,
            className: 'admin-loading-placeholder--inline admin-settings-loading-placeholder',
        }));
    };

    const revertValue = (key) => {
        const entry = state.controllers.get(key);
        if (!entry) {
            return;
        }
        entry.controller.setValue?.(cloneSettingsValue(state.values[key]));
    };

    const handleFieldUpdate = (field, newValue, controller) => {
        if (!state.active) {
            return;
        }

        // Skip hidden fields (dependency not satisfied)
        const row = controller.element;
        if (row && row.hidden) {
            return;
        }

        const key = field.key;
        if (valuesAreEqual(field, newValue, state.values[key])) {
            controller.setPending?.(false);
            return;
        }

        const existingEntry = state.inFlight.get(key);
        existingEntry?.abortController?.abort?.();

        const disableDuringRequest = disableTypes.has(field.type);
        const entry = {
            abortController: new AbortController(),
            disableDuringRequest,
        };

        state.inFlight.set(key, entry);
        if (disableDuringRequest) {
            controller.setDisabled?.(true);
        }
        controller.setPending?.(true);

        (async () => {
            const result = await updateSettingsValues(pageKey, { [key]: newValue }, { signal: entry.abortController.signal });
            if (!result) {
                throw new Error('settings_update_failed');
            }
            return result;
        })()
            .then(() => {
                if (!state.active) {
                    return;
                }
                const latestEntry = state.inFlight.get(key);
                if (latestEntry === entry) {
                    state.inFlight.delete(key);
                }
                state.values[key] = cloneSettingsValue(newValue);
                if (entry.disableDuringRequest) {
                    controller.setDisabled?.(false);
                }
                controller.setPending?.(false);
                status.clear();
                if (typeof onFieldSaved === 'function') {
                    onFieldSaved({
                        pageKey,
                        fieldKey: key,
                        value: cloneSettingsValue(newValue),
                    });
                }
                if (typeof onValueChange === 'function') {
                    onValueChange(key, cloneSettingsValue(newValue));
                }
            })
            .catch(() => {
                const latestEntry = state.inFlight.get(key);
                if (latestEntry !== entry) {
                    return;
                }
                state.inFlight.delete(key);
                revertValue(key);
                if (entry.disableDuringRequest) {
                    controller.setDisabled?.(false);
                }
                controller.setPending?.(false);
            });
    };

    const render = (sections = [], values = {}) => {
        resetView();

        const normalizedSections = sections
            .map((section = {}) => {
                const fields = Array.isArray(section?.fields) ? section.fields.filter(Boolean) : [];
                return {
                    title: section?.title,
                    description: section?.description,
                    i18n_title: section?.i18n_title,
                    i18n_description: section?.i18n_description,
                    group_title: section?.group_title,
                    group_description: section?.group_description,
                    i18n_group_title: section?.i18n_group_title,
                    i18n_group_description: section?.i18n_group_description,
                    fields,
                };
            })
            .filter((section) => section.fields.length);

        const fields = normalizedSections.flatMap((section) => section.fields);
        if (!fields.length) {
            renderEmptyState?.(container);
            return;
        }

        state.values = Object.fromEntries(
            fields.map((field) => [field.key, cloneSettingsValue(values?.[field.key])])
        );

        const fragment = document.createDocumentFragment();
        state.sections = [];
        state.groups = [];
        let activeGroup = null;

        const createSectionGroup = (section) => {
            const groupEl = document.createElement('section');
            groupEl.classList.add('settings-section-group');

            const headerEl = document.createElement('div');
            headerEl.classList.add('settings-section-group-header');

            const titleEl = document.createElement('h2');
            titleEl.classList.add('settings-section-group-title');
            titleEl.textContent = (section.i18n_group_title && typeof window.getTranslation === 'function')
                ? window.getTranslation(section.i18n_group_title, section.group_title)
                : section.group_title;
            headerEl.appendChild(titleEl);

            if (section.group_description) {
                const descriptionEl = document.createElement('p');
                descriptionEl.classList.add('settings-section-group-description');
                descriptionEl.textContent = (section.i18n_group_description && typeof window.getTranslation === 'function')
                    ? window.getTranslation(section.i18n_group_description, section.group_description)
                    : section.group_description;
                headerEl.appendChild(descriptionEl);
            }

            const bodyEl = document.createElement('div');
            bodyEl.classList.add('settings-section-group-body');
            groupEl.append(headerEl, bodyEl);
            fragment.appendChild(groupEl);
            const groupMeta = {
                key: section.group_title,
                element: groupEl,
                body: bodyEl,
                sections: [],
            };
            state.groups.push(groupMeta);
            return groupMeta;
        };

        normalizedSections.forEach((section) => {
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

            const fieldKeys = [];
            section.fields.forEach((field) => {
                const currentValue = cloneSettingsValue(state.values[field.key]);
                const { element, controller } = createSettingsRow(field, currentValue, {
                    debounceMs:
                        field.type === 'string' || field.type === 'textarea' || field.type === 'json'
                            ? stringDebounceMs
                            : field.type === 'string_list'
                                ? stringListDebounceMs
                                : 0,
                    onSubmit: (normalizedValue) => handleFieldUpdate(field, normalizedValue, controller),
                });

                element.dataset.fieldKey = field.key;
                bodyEl.appendChild(element);
                state.controllers.set(field.key, { field, controller });
                fieldKeys.push(field.key);
            });

            sectionEl.appendChild(bodyEl);
            state.sections.push({ element: sectionEl, fieldKeys });
            if (section.group_title) {
                if (!activeGroup || activeGroup.key !== section.group_title) {
                    activeGroup = createSectionGroup(section);
                }
                activeGroup.body.appendChild(sectionEl);
                activeGroup.sections.push(sectionEl);
            } else {
                activeGroup = null;
                fragment.appendChild(sectionEl);
            }
        });

        appendManagedContent(fragment);

        // Set up dependency handling
        attachDependencyListeners();
        updateDependentFieldsVisibility();

        if (typeof onRender === 'function') {
            onRender({
                pageKey,
                sections: cloneSettingsValue(normalizedSections),
                values: cloneSettingsValue(state.values),
                schemaControls: Array.from(state.controllers.values()).map(({ field, controller }) => ({
                    field,
                    control: controller.control,
                })),
                container,
            });
        }
    };

    const load = async () => {
        const token = ++state.loadToken;
        container.dataset.loading = 'true';
        renderLoadingState();
        status.show('info', loadingMessage);

        try {
            const response = await fetchSettingsSchema({ page: pageKey, includeValues: true });
            const rawSections = Array.isArray(response?.sections) ? response.sections : [];
            const sections = typeof transformSections === 'function'
                ? transformSections(rawSections)
                : rawSections;
            const values = response?.values && typeof response.values === 'object' ? response.values : {};

            if (!state.active || token !== state.loadToken) {
                return;
            }

            if (!sections.length) {
                resetView();
                if (preserveManagedRange) {
                    const temporaryContainer = document.createElement('div');
                    renderEmptyState?.(temporaryContainer);
                    appendManagedContent(temporaryContainer);
                } else {
                    renderEmptyState?.(container);
                }
                status.clear();
                window.notifyWarning?.(helperT('admin_settings_schema_empty', 'Settings schema is empty.'));
                return;
            }

            status.clear();
            render(sections, values);
            
            // Call onLoad callback with loaded values
            if (typeof onLoad === 'function') {
                onLoad(cloneSettingsValue(values));
            }
        } catch (error) {
            if (!state.active || token !== state.loadToken) {
                return;
            }

            const message = error?.message || loadErrorMessage;
            resetView();
            if (preserveManagedRange) {
                const temporaryContainer = document.createElement('div');
                renderError?.(temporaryContainer, message);
                appendManagedContent(temporaryContainer);
            } else {
                renderError?.(container, message);
            }
            onError?.(message, { phase: 'load' });
        } finally {
            if (token === state.loadToken) {
                delete container.dataset.loading;
            }
        }
    };

    return {
        init() {
            if (state.active) {
                return;
            }
            state.active = true;
            load();
        },
        /** Reload the active page while preserving the controller lifecycle. */
        reload() {
            if (!state.active) {
                return;
            }
            load();
        },
        teardown() {
            if (!state.active) {
                return;
            }
            state.active = false;
            state.loadToken += 1;
            abortAll();
            resetView();
            status.clear();
            state.values = {};
            delete container.dataset.loading;
        },
    };
}

window.createSettingsPageController = createSettingsPageController;

