const MODEL_SETTINGS_SIDEBAR_SELECTOR = '#modelSettingsSidebar .right-sidebar-area';
const MODEL_SETTINGS_LOADING_CLASS = 'model-settings-loading';
let modelFileFormatCatalogPromise = null;

const modelSettingsState = {
    initialized: false,
    controls: new Map(),
    schema: null,
    modelLookup: null,
    activeModelId: null,
    activeProjectId: '',
    lastRequestToken: null,
    persistedValuesByModel: new Map(),
    // Preset state
    presets: [],
    selectedPresetId: null,
    pendingDeletePresetId: null,
    pendingDeletePresetName: null,
    presetsEnabled: false,
    quickThinkingByModel: new Map(),
    quickThinkingLoadingFallback: null,
};

function storeSupportedFileFormatsFromSchemaPayload(schemaPayload, modelId) {
    if (typeof window === 'undefined') {
        return;
    }
    const normalizedModelId = modelId ? String(modelId) : null;
    const supported = Array.isArray(schemaPayload?.supported_file_formats)
        ? schemaPayload.supported_file_formats
        : [];
    window.modelSupportedFileFormats = {
        model_id: normalizedModelId,
        supported_file_formats: supported,
    };
    window.dispatchEvent(
        new CustomEvent('modelSupportedFileFormats:updated', {
            detail: window.modelSupportedFileFormats,
        })
    );
}

/**
 * Fetch the application-wide MIME catalog once per page.
 *
 * Model settings now carry only small group names. Keeping the static catalog
 * in a separately cached response avoids downloading the same long document
 * MIME list whenever the user switches models.
 */
async function fetchModelFileFormatCatalog() {
    if (!modelFileFormatCatalogPromise) {
        modelFileFormatCatalogPromise = window.authedFetch('/api/v1/llm/model/file-format-catalog')
            .then((response) => {
                if (!response.ok) {
                    throw new Error(`Failed to load file format catalog (${response.status})`);
                }
                return response.json();
            })
            .then((payload) => (
                payload?.groups && typeof payload.groups === 'object'
                    ? payload.groups
                    : {}
            ))
            .catch((error) => {
                // Permit a later retry after transient authentication/network
                // failures. An empty catalog preserves the historical frontend
                // fallback of letting backend validation make the final call.
                modelFileFormatCatalogPromise = null;
                console.error('Model settings: failed to load file format catalog', error);
                return {};
            });
    }
    return modelFileFormatCatalogPromise;
}

async function expandSupportedFileFormatsFromSchemaPayload(schemaPayload) {
    if (Array.isArray(schemaPayload?.supported_file_formats)) {
        // Retain compatibility with BYOK and older server responses during a
        // rolling frontend/backend update.
        return schemaPayload.supported_file_formats;
    }
    const groupNames = Array.isArray(schemaPayload?.supported_file_format_groups)
        ? schemaPayload.supported_file_format_groups
        : [];
    if (!groupNames.length) {
        return [];
    }

    const catalog = await fetchModelFileFormatCatalog();
    return groupNames
        .filter((groupName) => Array.isArray(catalog[groupName]))
        .map((groupName) => ({
            category: groupName,
            file_formats: [...catalog[groupName]],
        }));
}

function findModelSettingsField(schema, fieldKey) {
    for (const section of schema?.sections || []) {
        const field = (section?.fields || []).find((candidate) => candidate?.key === fieldKey);
        if (field) {
            return field;
        }
    }
    return null;
}

/** Hydrate request-scoped MCP choices outside the generic model schema. */
async function hydrateMcpServerOptions(schemaPayload, modelId, projectId) {
    const field = findModelSettingsField(
        schemaPayload?.schema,
        'settings.enabled_mcp_servers'
    );
    if (!field) {
        return;
    }

    const params = new URLSearchParams({ model_id: String(modelId) });
    if (projectId) {
        params.set('project_id', String(projectId));
    }
    try {
        const response = await window.authedFetch(`/api/v1/llm/mcp/connectors/mentions?${params.toString()}`);
        if (!response.ok) {
            throw new Error(`Failed to load MCP connector choices (${response.status})`);
        }
        const connectors = await response.json();
        field.options = (Array.isArray(connectors) ? connectors : [])
            .filter((connector) => connector?.id && connector?.name)
            .map((connector) => ({
                value: String(connector.id),
                label: String(connector.name),
            }));
    } catch (error) {
        // The rest of the settings form remains useful when connector loading
        // fails. The composer mention UI can retry through the same endpoint.
        field.options = [];
        console.error('Model settings: failed to load MCP connector choices', error);
    }
}

/** Wait briefly for bundled schema translations before rendering i18n-only text. */
async function waitForModelSettingsTranslations() {
    if (typeof window === 'undefined' || window.__omlorixI18nReady !== false) {
        return;
    }
    await new Promise((resolve) => {
        let settled = false;
        const finish = () => {
            if (settled) return;
            settled = true;
            clearTimeout(timeoutId);
            document.removeEventListener('i18n:updated', finish);
            resolve();
        };
        // Translation loading should normally finish before the schema and its
        // auxiliary requests. Keep a bounded fallback for offline/error cases.
        const timeoutId = setTimeout(finish, 2000);
        document.addEventListener('i18n:updated', finish, { once: true });
    });
}

function getModelSettingsSidebarContainer() {
    return document.querySelector(MODEL_SETTINGS_SIDEBAR_SELECTOR);
}

function deepCloneValue(value) {
    if (Array.isArray(value)) {
        return value.map((item) => deepCloneValue(item));
    }
    if (value && typeof value === 'object') {
        return Object.keys(value).reduce((clone, key) => {
            clone[key] = deepCloneValue(value[key]);
            return clone;
        }, {});
    }
    return value;
}

function cloneSchemaForRendering(schema) {
    if (!schema) {
        return null;
    }
    return {
        ...schema,
        sections: (schema.sections || []).map((section) => ({
            ...section,
            fields: (section.fields || []).map((field) => ({ ...field })),
        })),
    };
}

function persistCurrentModelValues() {
    if (!modelSettingsState.activeModelId || !modelSettingsState.controls.size) {
        return;
    }
    const currentValues = withoutRequestScopedMcpSelection(getCurrentModelSettingValues());
    modelSettingsState.persistedValuesByModel.set(String(modelSettingsState.activeModelId), currentValues);
}

/** MCP selection belongs to one composer request, never to model persistence. */
function withoutRequestScopedMcpSelection(values) {
    const cloned = deepCloneValue(values || {});
    if (cloned.settings && typeof cloned.settings === 'object') {
        delete cloned.settings.enabled_mcp_servers;
        if (!Object.keys(cloned.settings).length) {
            delete cloned.settings;
        }
    }
    return cloned;
}

function applyPersistedValuesToSchema(schema, modelId) {
    const clonedSchema = cloneSchemaForRendering(schema);
    if (!clonedSchema || !modelId) {
        return clonedSchema || schema;
    }
    const persistedValues = modelSettingsState.persistedValuesByModel.get(String(modelId));
    if (!persistedValues) {
        return clonedSchema;
    }
    clonedSchema.sections.forEach((section) => {
        (section.fields || []).forEach((field) => {
            if (!field?.key) {
                return;
            }
            const preservedValue = getNestedValue(persistedValues, field.key);
            if (preservedValue === undefined) {
                return;
            }
            field.value = deepCloneValue(preservedValue);
            if (field.type === 'boolean') {
                field.default = Boolean(preservedValue);
            }
        });
    });
    return clonedSchema;
}

function setSidebarInner(html) {
    const container = getModelSettingsSidebarContainer();
    if (!container) {
        return;
    }
    container.innerHTML = html;
}

function clearRenderedModelSettingsState() {
    modelSettingsState.controls.clear();
    modelSettingsState.schema = null;
}

function translateModelSettingsText(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    if (typeof window !== 'undefined' && typeof window.t === 'function') {
        const translated = window.t(key);
        if (translated && translated !== key) {
            return translated;
        }
    }
    return fallback;
}

function translateModelSettingsBackendMessage(message) {
    const normalizedMessage = String(message || '').trim();
    if (normalizedMessage === 'This model does not support custom model settings.') {
        return translateModelSettingsText(
            'model_settings_custom_settings_unsupported',
            normalizedMessage
        );
    }
    return normalizedMessage;
}

function isReasoningEffortField(fieldKey) {
    return fieldKey === 'settings.reasoning_effort' || fieldKey === 'settings.thinking_level';
}

function translateModelSettingsOption(option, fieldKey = null) {
    const fallback = option?.label || String(option?.value ?? '');
    if (isReasoningEffortField(fieldKey)) {
        const normalizedValue = String(option?.value ?? '').trim().toLowerCase();
        const valueI18nKey = REASONING_EFFORT_I18N_BY_VALUE[normalizedValue];
        if (valueI18nKey) {
            return translateModelSettingsText(valueI18nKey, fallback);
        }
    }
    if (option?.i18n_label) {
        return translateModelSettingsText(option.i18n_label, fallback);
    }
    return fallback;
}

function translateModelSettingsSchema(schema) {
    const clonedSchema = cloneSchemaForRendering(schema);
    if (!clonedSchema || !Array.isArray(clonedSchema.sections)) {
        return clonedSchema || schema;
    }

    // Provider schemas are shared between admin and chat surfaces, so the
    // backend sends stable i18n keys plus English fallbacks. Translate the
    // cloned payload right before rendering to keep the original schema stable.
    clonedSchema.sections.forEach((section) => {
        if (section?.i18n_title) {
            section.title = translateModelSettingsText(section.i18n_title, section.title || '');
        }
        if (section?.i18n_description) {
            section.description = translateModelSettingsText(section.i18n_description, section.description || '');
        }

        (section?.fields || []).forEach((field) => {
            if (field?.i18n_label) {
                field.label = translateModelSettingsText(field.i18n_label, field.label || field.key || '');
            }
            if (field?.i18n_description) {
                field.description = translateModelSettingsText(field.i18n_description, field.description || '');
            }
            if (field?.i18n_placeholder) {
                field.placeholder = translateModelSettingsText(field.i18n_placeholder, field.placeholder || '');
            }
            if (Array.isArray(field?.options)) {
                field.options = field.options.map((option) => {
                    if (!option || typeof option !== 'object') {
                        return option;
                    }
                    return {
                        ...option,
                        label: translateModelSettingsOption(option, field.key),
                    };
                });
            }
        });
    });

    return clonedSchema;
}

function getSchemaFields(schema = modelSettingsState.schema) {
    if (!schema || !Array.isArray(schema.sections)) {
        return [];
    }
    const fields = [];
    schema.sections.forEach((section) => {
        (section?.fields || []).forEach((field) => {
            if (field?.key) {
                fields.push(field);
            }
        });
    });
    return fields;
}

function getSchemaFieldByKey(fieldKey) {
    if (!fieldKey) {
        return null;
    }
    return getSchemaFields().find((field) => field.key === fieldKey) || null;
}

function readCurrentFieldValue(fieldKey) {
    if (!fieldKey) {
        return undefined;
    }
    const entry = modelSettingsState.controls.get(fieldKey);
    if (entry?.control && entry?.field) {
        return extractFieldValue(entry.field, entry.control);
    }
    return getNestedValue(getCurrentModelSettingValues(), fieldKey);
}

function persistLiveModelValues() {
    if (!modelSettingsState.activeModelId) {
        return;
    }
    modelSettingsState.persistedValuesByModel.set(
        String(modelSettingsState.activeModelId),
        withoutRequestScopedMcpSelection(getCurrentModelSettingValues())
    );
}

const QUICK_THINKING_VALUES = Object.freeze({
    off: '__quick-thinking-off',
    auto: '__quick-thinking-auto',
    budget: '__quick-thinking-budget',
    on: '__quick-thinking-on',
});

const REASONING_EFFORT_I18N_BY_VALUE = Object.freeze({
    none: 'chatbox_thinking_option_off',
    on: 'llm.shared.settings.reasoning_effort.option.on',
    minimal: 'llm.shared.settings.reasoning_effort.option.minimal',
    low: 'llm.shared.settings.reasoning_effort.option.low',
    medium: 'llm.shared.settings.reasoning_effort.option.medium',
    high: 'llm.shared.settings.reasoning_effort.option.high',
    xhigh: 'llm.shared.settings.reasoning_effort.option.xhigh',
    max: 'llm.shared.settings.reasoning_effort.option.max',
});

function normalizeQuickThinkingOptionValue(value) {
    return String(value ?? '').trim().toLowerCase();
}

function cloneQuickThinkingControlState(state, modelId = null) {
    if (!state || typeof state !== 'object') {
        return null;
    }
    const options = Array.isArray(state.options)
        ? state.options
            .map((option) => {
                if (!option || option.value === undefined || option.value === null) {
                    return null;
                }
                return {
                    value: String(option.value),
                    label: option.label || String(option.value),
                };
            })
            .filter(Boolean)
        : [];
    if (!options.length) {
        return null;
    }
    return {
        modelId: modelId ? String(modelId) : (state.modelId ? String(state.modelId) : null),
        label: state.label || translateModelSettingsText('chatbox_thinking_button_label', 'Thinking'),
        currentValue: state.currentValue === undefined ? null : state.currentValue,
        currentLabel: state.currentLabel || state.label || translateModelSettingsText('chatbox_thinking_button_label', 'Thinking'),
        options,
        meta: state.meta && typeof state.meta === 'object' ? { ...state.meta } : {},
    };
}

function setQuickThinkingLoadingFallback(state, modelId = null) {
    modelSettingsState.quickThinkingLoadingFallback = cloneQuickThinkingControlState(state, modelId);
}

function clearQuickThinkingLoadingFallback() {
    modelSettingsState.quickThinkingLoadingFallback = null;
}

function getCachedQuickThinkingControlState(modelId) {
    const normalizedModelId = modelId ? String(modelId) : null;
    if (!normalizedModelId) {
        return null;
    }
    const cachedState = modelSettingsState.quickThinkingByModel.get(normalizedModelId);
    return cloneQuickThinkingControlState(cachedState, normalizedModelId);
}

function getQuickThinkingControlState() {
    if (!modelSettingsState.activeModelId || !modelSettingsState.schema) {
        if (modelSettingsState.quickThinkingLoadingFallback) {
            return cloneQuickThinkingControlState(
                modelSettingsState.quickThinkingLoadingFallback,
                modelSettingsState.activeModelId
            );
        }
        return null;
    }

    const fieldKeys = {
        enable: ['settings.reasoning_enabled', 'settings.thinking', 'settings.reasoning'],
        adaptive: ['settings.thinking_adaptive', 'settings.thinking_dynamic'],
        effort: ['settings.reasoning_effort', 'settings.thinking_level'],
        budget: ['settings.reasoning_max_tokens', 'settings.thinking_budget'],
        mode: ['settings.reasoning_mode'],
    };
    const pickField = (keys) => keys.map((key) => getSchemaFieldByKey(key)).find(Boolean) || null;

    const enableField = pickField(fieldKeys.enable);
    const adaptiveField = pickField(fieldKeys.adaptive);
    const effortField = pickField(fieldKeys.effort);
    const budgetField = pickField(fieldKeys.budget);
    const modeField = pickField(fieldKeys.mode);

    if (!enableField && !adaptiveField && !effortField && !budgetField) {
        return null;
    }

    const effortOptions = effortField?.type === 'select' && Array.isArray(effortField.options)
        ? effortField.options
        : [];
    const hasNoneEffortOption = effortOptions.some((option) => normalizeQuickThinkingOptionValue(option?.value) === 'none');
    const options = [];
    if (enableField?.type === 'boolean' && (adaptiveField || budgetField || (effortField && !hasNoneEffortOption))) {
        options.push({
            value: QUICK_THINKING_VALUES.off,
            label: translateModelSettingsText('chatbox_thinking_option_off', 'Off'),
        });
    }
    if (adaptiveField?.type === 'boolean') {
        options.push({
            value: QUICK_THINKING_VALUES.auto,
            label: translateModelSettingsText('chatbox_thinking_option_auto', 'Auto'),
        });
    }
    if (budgetField) {
        options.push({
            value: QUICK_THINKING_VALUES.budget,
            label: translateModelSettingsText('chatbox_thinking_option_budget', 'Budget'),
        });
    }
    if (effortOptions.length) {
        effortOptions.forEach((option) => {
            if (option?.value === undefined || option?.value === null || option.value === '') {
                return;
            }
            const normalizedValue = normalizeQuickThinkingOptionValue(option.value);
            options.push({
                value: String(option.value),
                label: normalizedValue === 'none'
                    ? translateModelSettingsText('chatbox_thinking_option_off', 'Off')
                    : translateModelSettingsOption(option, effortField?.key),
            });
        });
    }
    if (!options.length && enableField?.type === 'boolean') {
        options.push({
            value: QUICK_THINKING_VALUES.off,
            label: translateModelSettingsText('chatbox_thinking_option_off', 'Off'),
        });
        options.push({
            value: QUICK_THINKING_VALUES.on,
            label: translateModelSettingsText('chatbox_thinking_option_on', 'On'),
        });
    }

    const uniqueOptions = [];
    const seenValues = new Set();
    options.forEach((option) => {
        if (!option || seenValues.has(option.value)) {
            return;
        }
        seenValues.add(option.value);
        uniqueOptions.push(option);
    });
    if (!uniqueOptions.length) {
        return null;
    }

    const enableValue = enableField ? readCurrentFieldValue(enableField.key) : undefined;
    const adaptiveValue = adaptiveField ? readCurrentFieldValue(adaptiveField.key) : undefined;
    const effortValue = effortField ? readCurrentFieldValue(effortField.key) : undefined;
    const budgetValue = budgetField ? readCurrentFieldValue(budgetField.key) : undefined;
    const modeValue = modeField ? readCurrentFieldValue(modeField.key) : undefined;

    let currentValue = null;
    if (hasNoneEffortOption && normalizeQuickThinkingOptionValue(effortValue) === 'none') {
        currentValue = String(effortValue);
    } else if (hasNoneEffortOption && enableField?.type === 'boolean' && enableValue === false) {
        const noneOption = effortOptions.find((option) => normalizeQuickThinkingOptionValue(option?.value) === 'none');
        currentValue = noneOption ? String(noneOption.value) : QUICK_THINKING_VALUES.off;
    } else if (enableField?.type === 'boolean' && enableValue === false) {
        currentValue = QUICK_THINKING_VALUES.off;
    } else if (adaptiveField?.type === 'boolean' && adaptiveValue === true) {
        currentValue = QUICK_THINKING_VALUES.auto;
    } else if (
        budgetField
        && (
            modeValue === 'budget'
            || (
                !modeField
                && budgetValue !== undefined
                && budgetValue !== null
                && String(budgetValue).trim() !== ''
                && !effortValue
            )
        )
    ) {
        currentValue = QUICK_THINKING_VALUES.budget;
    } else if (effortValue !== undefined && effortValue !== null && String(effortValue).trim() !== '') {
        currentValue = String(effortValue);
    } else if (enableField?.type === 'boolean' && enableValue === true) {
        currentValue = QUICK_THINKING_VALUES.on;
    }

    const currentOption = uniqueOptions.find(
        (option) => normalizeQuickThinkingOptionValue(option?.value) === normalizeQuickThinkingOptionValue(currentValue)
    ) || null;
    const controlLabel = effortField?.label || enableField?.label || adaptiveField?.label || budgetField?.label || 'Thinking';

    const quickThinkingState = {
        modelId: modelSettingsState.activeModelId,
        label: controlLabel,
        currentValue,
        currentLabel: currentOption?.label || translateModelSettingsText('chatbox_thinking_button_label', 'Thinking'),
        options: uniqueOptions,
        meta: {
            enableFieldKey: enableField?.key || null,
            adaptiveFieldKey: adaptiveField?.key || null,
            effortFieldKey: effortField?.key || null,
            budgetFieldKey: budgetField?.key || null,
            modeFieldKey: modeField?.key || null,
        },
    };

    const normalizedModelId = String(modelSettingsState.activeModelId || '');
    if (normalizedModelId) {
        const cachedState = cloneQuickThinkingControlState(quickThinkingState, normalizedModelId);
        if (cachedState) {
            modelSettingsState.quickThinkingByModel.set(normalizedModelId, cachedState);
        }
    }

    return quickThinkingState;
}

function readSchemaFieldValue(field, settings = {}) {
    if (!field?.key) {
        return undefined;
    }
    const settingValue = getNestedValue(settings, field.key);
    if (settingValue !== undefined) {
        return settingValue;
    }
    if (field.value !== undefined) {
        return field.value;
    }
    return field.default;
}

function getQuickThinkingControlStateFromSchema(schema, modelId = null, settings = {}) {
    const fields = getSchemaFields(schema);
    const pickField = (keys) => keys.map((key) => fields.find((field) => field?.key === key)).find(Boolean) || null;

    const enableField = pickField(['settings.reasoning_enabled', 'settings.thinking', 'settings.reasoning']);
    const adaptiveField = pickField(['settings.thinking_adaptive', 'settings.thinking_dynamic']);
    const effortField = pickField(['settings.reasoning_effort', 'settings.thinking_level']);
    const budgetField = pickField(['settings.reasoning_max_tokens', 'settings.thinking_budget']);
    const modeField = pickField(['settings.reasoning_mode']);

    if (!enableField && !adaptiveField && !effortField && !budgetField) {
        return null;
    }

    const effortOptions = effortField?.type === 'select' && Array.isArray(effortField.options)
        ? effortField.options
        : [];
    const hasNoneEffortOption = effortOptions.some((option) => normalizeQuickThinkingOptionValue(option?.value) === 'none');
    const options = [];

    if (enableField?.type === 'boolean' && (adaptiveField || budgetField || (effortField && !hasNoneEffortOption))) {
        options.push({
            value: QUICK_THINKING_VALUES.off,
            label: translateModelSettingsText('chatbox_thinking_option_off', 'Off'),
        });
    }
    if (adaptiveField?.type === 'boolean') {
        options.push({
            value: QUICK_THINKING_VALUES.auto,
            label: translateModelSettingsText('chatbox_thinking_option_auto', 'Auto'),
        });
    }
    if (budgetField) {
        options.push({
            value: QUICK_THINKING_VALUES.budget,
            label: translateModelSettingsText('chatbox_thinking_option_budget', 'Budget'),
        });
    }
    effortOptions.forEach((option) => {
        if (option?.value === undefined || option?.value === null || option.value === '') {
            return;
        }
        const normalizedValue = normalizeQuickThinkingOptionValue(option.value);
        options.push({
            value: String(option.value),
            label: normalizedValue === 'none'
                ? translateModelSettingsText('chatbox_thinking_option_off', 'Off')
                : translateModelSettingsOption(option, effortField?.key),
        });
    });
    if (!options.length && enableField?.type === 'boolean') {
        options.push({
            value: QUICK_THINKING_VALUES.off,
            label: translateModelSettingsText('chatbox_thinking_option_off', 'Off'),
        });
        options.push({
            value: QUICK_THINKING_VALUES.on,
            label: translateModelSettingsText('chatbox_thinking_option_on', 'On'),
        });
    }

    const uniqueOptions = [];
    const seenValues = new Set();
    options.forEach((option) => {
        if (!option || seenValues.has(option.value)) {
            return;
        }
        seenValues.add(option.value);
        uniqueOptions.push(option);
    });
    if (!uniqueOptions.length) {
        return null;
    }

    const enableValue = enableField ? readSchemaFieldValue(enableField, settings) : undefined;
    const adaptiveValue = adaptiveField ? readSchemaFieldValue(adaptiveField, settings) : undefined;
    const effortValue = effortField ? readSchemaFieldValue(effortField, settings) : undefined;
    const budgetValue = budgetField ? readSchemaFieldValue(budgetField, settings) : undefined;
    const modeValue = modeField ? readSchemaFieldValue(modeField, settings) : undefined;

    let currentValue = null;
    if (hasNoneEffortOption && normalizeQuickThinkingOptionValue(effortValue) === 'none') {
        currentValue = String(effortValue);
    } else if (hasNoneEffortOption && enableField?.type === 'boolean' && enableValue === false) {
        const noneOption = effortOptions.find((option) => normalizeQuickThinkingOptionValue(option?.value) === 'none');
        currentValue = noneOption ? String(noneOption.value) : QUICK_THINKING_VALUES.off;
    } else if (enableField?.type === 'boolean' && enableValue === false) {
        currentValue = QUICK_THINKING_VALUES.off;
    } else if (adaptiveField?.type === 'boolean' && adaptiveValue === true) {
        currentValue = QUICK_THINKING_VALUES.auto;
    } else if (
        budgetField
        && (
            modeValue === 'budget'
            || (
                budgetValue !== undefined
                && budgetValue !== null
                && String(budgetValue).trim() !== ''
                && (modeField || !effortValue)
            )
        )
    ) {
        currentValue = QUICK_THINKING_VALUES.budget;
    } else if (effortValue !== undefined && effortValue !== null && String(effortValue).trim() !== '') {
        currentValue = String(effortValue);
    } else if (enableField?.type === 'boolean' && enableValue === true) {
        currentValue = QUICK_THINKING_VALUES.on;
    }

    const currentOption = uniqueOptions.find(
        (option) => normalizeQuickThinkingOptionValue(option?.value) === normalizeQuickThinkingOptionValue(currentValue)
    ) || null;

    return {
        modelId: modelId ? String(modelId) : null,
        label: effortField?.label || enableField?.label || adaptiveField?.label || budgetField?.label || translateModelSettingsText('chatbox_thinking_button_label', 'Thinking'),
        currentValue,
        currentLabel: currentOption?.label || translateModelSettingsText('chatbox_thinking_button_label', 'Thinking'),
        options: uniqueOptions,
        meta: {
            enableFieldKey: enableField?.key || null,
            adaptiveFieldKey: adaptiveField?.key || null,
            effortFieldKey: effortField?.key || null,
            budgetFieldKey: budgetField?.key || null,
            modeFieldKey: modeField?.key || null,
        },
    };
}

function applyQuickThinkingValueToSettings(settings, quickThinkingState, nextValue) {
    if (!quickThinkingState) {
        return settings && typeof settings === 'object' ? deepCloneValue(settings) : {};
    }
    const nextSettings = settings && typeof settings === 'object' ? deepCloneValue(settings) : {};
    const {
        enableFieldKey,
        adaptiveFieldKey,
        effortFieldKey,
        budgetFieldKey,
        modeFieldKey,
    } = quickThinkingState.meta || {};

    const updates = {};
    if (nextValue === QUICK_THINKING_VALUES.off) {
        if (!enableFieldKey) {
            return nextSettings;
        }
        updates[enableFieldKey] = false;
        if (adaptiveFieldKey) updates[adaptiveFieldKey] = false;
        if (modeFieldKey) updates[modeFieldKey] = '';
        if (effortFieldKey) updates[effortFieldKey] = '';
        if (budgetFieldKey) updates[budgetFieldKey] = '';
    } else if (nextValue === QUICK_THINKING_VALUES.auto) {
        if (enableFieldKey) updates[enableFieldKey] = true;
        if (adaptiveFieldKey) updates[adaptiveFieldKey] = true;
        if (modeFieldKey) updates[modeFieldKey] = '';
        if (effortFieldKey) updates[effortFieldKey] = '';
        if (budgetFieldKey) updates[budgetFieldKey] = '';
    } else if (nextValue === QUICK_THINKING_VALUES.budget) {
        if (enableFieldKey) updates[enableFieldKey] = true;
        if (adaptiveFieldKey) updates[adaptiveFieldKey] = false;
        if (modeFieldKey) updates[modeFieldKey] = 'budget';
        if (effortFieldKey) updates[effortFieldKey] = '';
    } else if (nextValue === QUICK_THINKING_VALUES.on) {
        if (!enableFieldKey) {
            return nextSettings;
        }
        updates[enableFieldKey] = true;
    } else {
        const normalizedNextValue = normalizeQuickThinkingOptionValue(nextValue);
        if (enableFieldKey) updates[enableFieldKey] = true;
        if (adaptiveFieldKey) updates[adaptiveFieldKey] = false;
        if (modeFieldKey) updates[modeFieldKey] = 'effort';
        if (budgetFieldKey) updates[budgetFieldKey] = '';
        if (effortFieldKey) updates[effortFieldKey] = normalizedNextValue === 'none' ? 'none' : nextValue;
    }

    Object.entries(updates).forEach(([fieldKey, value]) => {
        assignNestedValue(nextSettings, fieldKey, value);
    });
    return nextSettings;
}

function emitModelSettingsStateChanged() {
    if (typeof window === 'undefined') {
        return;
    }
    window.dispatchEvent(new CustomEvent('modelSettings:stateChanged', {
        detail: {
            modelId: modelSettingsState.activeModelId,
            settings: getCurrentModelSettingValues(),
            quickThinking: getQuickThinkingControlState(),
        },
    }));
}

function setModelSettingFieldValue(fieldKey, value) {
    if (!fieldKey) {
        return false;
    }
    const entry = modelSettingsState.controls.get(fieldKey);
    if (!entry?.control) {
        return false;
    }

    const { control, field } = entry;
    entry.field.value = value;

    if (control.dataset.fieldType === 'boolean') {
        control.checked = Boolean(value);
        return true;
    }

    if (control.tagName === 'SELECT') {
        if (control.multiple) {
            const values = Array.isArray(value) ? value.map(String) : [];
            Array.from(control.options).forEach((option) => {
                option.selected = values.includes(option.value);
            });
            if (control._multiSelect) {
                control.dispatchEvent(new Event('change', { bubbles: true }));
            }
            return true;
        }
        control.value = value === undefined || value === null ? '' : String(value);
        return true;
    }

    if (control.tagName === 'TEXTAREA') {
        if (isStructuredMappingInputType(field.input_type) && value && typeof value === 'object' && !Array.isArray(value)) {
            control.value = JSON.stringify(value, null, 2);
            extractFieldValue(field, control);
        } else {
            control.value = Array.isArray(value)
                ? value.join('\n')
                : (value === undefined || value === null ? '' : String(value));
        }
        return true;
    }

    if (control.tagName === 'INPUT') {
        control.value = value === undefined || value === null ? '' : String(value);
        return true;
    }

    return false;
}

function applyQuickThinkingControlValue(nextValue) {
    const state = getQuickThinkingControlState();
    if (!state) {
        return false;
    }

    const {
        enableFieldKey,
        adaptiveFieldKey,
        effortFieldKey,
        budgetFieldKey,
        modeFieldKey,
    } = state.meta || {};

    const updates = {};
    if (nextValue === QUICK_THINKING_VALUES.off) {
        if (!enableFieldKey) {
            return false;
        }
        updates[enableFieldKey] = false;
        if (adaptiveFieldKey) updates[adaptiveFieldKey] = false;
        if (modeFieldKey) updates[modeFieldKey] = '';
        if (effortFieldKey) updates[effortFieldKey] = '';
        if (budgetFieldKey) updates[budgetFieldKey] = '';
    } else if (nextValue === QUICK_THINKING_VALUES.auto) {
        if (enableFieldKey) updates[enableFieldKey] = true;
        if (adaptiveFieldKey) updates[adaptiveFieldKey] = true;
        if (modeFieldKey) updates[modeFieldKey] = '';
        if (effortFieldKey) updates[effortFieldKey] = '';
        if (budgetFieldKey) updates[budgetFieldKey] = '';
    } else if (nextValue === QUICK_THINKING_VALUES.budget) {
        if (enableFieldKey) updates[enableFieldKey] = true;
        if (adaptiveFieldKey) updates[adaptiveFieldKey] = false;
        if (modeFieldKey) updates[modeFieldKey] = 'budget';
        if (effortFieldKey) updates[effortFieldKey] = '';
    } else if (nextValue === QUICK_THINKING_VALUES.on) {
        if (!enableFieldKey) {
            return false;
        }
        updates[enableFieldKey] = true;
    } else {
        const normalizedNextValue = normalizeQuickThinkingOptionValue(nextValue);
        if (enableFieldKey) updates[enableFieldKey] = true;
        if (adaptiveFieldKey) updates[adaptiveFieldKey] = false;
        if (modeFieldKey) updates[modeFieldKey] = 'effort';
        if (budgetFieldKey) updates[budgetFieldKey] = '';
        if (effortFieldKey) updates[effortFieldKey] = normalizedNextValue === 'none' ? 'none' : nextValue;
    }

    Object.entries(updates).forEach(([fieldKey, value]) => {
        setModelSettingFieldValue(fieldKey, value);
    });

    updateDependentFieldsVisibility();
    persistLiveModelValues();
    emitModelSettingsStateChanged();
    return true;
}

function applyPreferredQuickThinkingValue(preferredValue) {
    if (preferredValue === undefined || preferredValue === null || preferredValue === '') {
        return false;
    }
    const state = getQuickThinkingControlState();
    if (!state || !Array.isArray(state.options) || !state.options.length) {
        return false;
    }

    const normalizedPreferredValue = normalizeQuickThinkingOptionValue(preferredValue);
    const matchingOption = state.options.find(
        (option) => normalizeQuickThinkingOptionValue(option?.value) === normalizedPreferredValue
    );
    if (!matchingOption) {
        return false;
    }

    if (normalizeQuickThinkingOptionValue(state.currentValue) === normalizedPreferredValue) {
        return true;
    }
    return applyQuickThinkingControlValue(matchingOption.value);
}

function showModelSettingsLoading() {
    clearRenderedModelSettingsState();
    setSidebarInner(`<div class="${MODEL_SETTINGS_LOADING_CLASS}">${translateModelSettingsText('model_settings_loading', 'Loading...')}</div>`);
    emitModelSettingsStateChanged();
}

function showModelSettingsEmpty(message) {
    clearRenderedModelSettingsState();
    setSidebarInner(`<div class="model-settings-empty">${message || translateModelSettingsText('model_settings_empty', 'No settings available for this model.')}</div>`);
    emitModelSettingsStateChanged();
}

async function ensureModelLookup() {
    if (modelSettingsState.modelLookup) {
        return modelSettingsState.modelLookup;
    }

    try {
        const payload = typeof window.getCachedUserModels === 'function'
            ? await window.getCachedUserModels()
            : await (async () => {
                const response = await window.authedFetch('/api/v1/llm/models/user');
                if (!response.ok) {
                    throw new Error(translateModelSettingsText(
                        'model_settings_load_models_failed_status',
                        'Failed to load models ({status})'
                    ).replace('{status}', String(response.status)));
                }
                return response.json();
            })();
        if (!Array.isArray(payload)) {
            throw new Error(translateModelSettingsText('model_settings_unexpected_models_response', 'Unexpected models response.'));
        }
        const groupedModels = typeof window.BYOK?.getAllSelectableModels === 'function'
            ? window.BYOK.getAllSelectableModels(payload)
            : { allModels: payload };
        const models = Array.isArray(groupedModels.allModels) ? groupedModels.allModels : payload;
        const lookup = new Map();
        models.forEach((model) => {
            if (model?.model_id) {
                lookup.set(String(model.model_id), model);
            }
        });
        modelSettingsState.modelLookup = lookup;
    } catch (error) {
        console.error('Model settings: unable to build model lookup', error);
        modelSettingsState.modelLookup = new Map();
    }
    return modelSettingsState.modelLookup;
}

function assignNestedValue(target, keyPath, value) {
    if (!keyPath) {
        return;
    }
    const segments = keyPath.split('.');
    let cursor = target;
    for (let i = 0; i < segments.length - 1; i += 1) {
        const segment = segments[i];
        if (typeof cursor[segment] !== 'object' || cursor[segment] === null || Array.isArray(cursor[segment])) {
            cursor[segment] = {};
        }
        cursor = cursor[segment];
    }
    cursor[segments[segments.length - 1]] = value;
}

function parseListValue(raw) {
    if (!raw) {
        return [];
    }
    return raw
        .split(/\r?\n|,/g)
        .map((item) => item.trim())
        .filter(Boolean);
}

function isStructuredMappingInputType(inputType) {
    const normalized = String(inputType || '').trim().toLowerCase();
    return normalized === 'object'
        || normalized === 'json_object'
        || /^dict\s*\[/.test(normalized);
}

function parseStructuredMappingValue(raw, inputType = '') {
    const text = String(raw || '').trim();
    if (!text) {
        return { value: undefined, error: '' };
    }

    let parsed;
    try {
        parsed = JSON.parse(text);
    } catch (_) {
        return {
            value: undefined,
            error: translateModelSettingsText(
                'model_settings_invalid_json_object',
                'Enter a valid JSON object.'
            ),
        };
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return {
            value: undefined,
            error: translateModelSettingsText(
                'model_settings_json_object_required',
                'Enter a JSON object, not a list or scalar value.'
            ),
        };
    }

    const normalizedType = String(inputType || '').trim().toLowerCase();
    if (normalizedType === 'dict[str,float]') {
        const normalized = {};
        for (const [rawTokenId, rawBias] of Object.entries(parsed)) {
            const tokenId = String(rawTokenId || '').trim();
            const bias = typeof rawBias === 'number' ? rawBias : Number.NaN;
            if (!/^\d+$/.test(tokenId)) {
                return {
                    value: undefined,
                    error: translateModelSettingsText(
                        'model_settings_non_negative_token_ids_required',
                        'Every key must be a non-negative token ID.'
                    ),
                };
            }
            if (!Number.isFinite(bias) || bias < -100 || bias > 100) {
                return {
                    value: undefined,
                    error: translateModelSettingsText(
                        'model_settings_bias_range_required',
                        'Every bias must be a number from -100 to 100.'
                    ),
                };
            }
            normalized[tokenId] = bias;
        }
        return { value: normalized, error: '' };
    }
    return { value: parsed, error: '' };
}

function setStructuredControlValidity(control, error) {
    if (!control) {
        return;
    }
    if (typeof control.setCustomValidity === 'function') {
        control.setCustomValidity(error || '');
    }
    control.setAttribute?.('aria-invalid', error ? 'true' : 'false');
}

function extractFieldValue(field, control) {
    if (!control) {
        return undefined;
    }
    const metaType = (field.input_type || '').toLowerCase();
    if (control.dataset.fieldType === 'boolean') {
        return Boolean(control.checked);
    }
    if (control.tagName === 'SELECT') {
        if (control.multiple) {
            const values = Array.from(control.selectedOptions).map((option) => option.value).filter(Boolean);
            return values.length ? values : undefined;
        }
        return control.value || undefined;
    }
    if (isStructuredMappingInputType(metaType)) {
        const parsed = parseStructuredMappingValue(control.value || '', metaType);
        setStructuredControlValidity(control, parsed.error);
        return parsed.value;
    }
    if (metaType === 'list[str]') {
        const values = parseListValue(control.value || '');
        return values.length ? values : undefined;
    }
    if (!control.value || !control.value.trim()) {
        return undefined;
    }
    if (metaType === 'int') {
        const parsedInt = Number.parseInt(control.value, 10);
        return Number.isNaN(parsedInt) ? undefined : parsedInt;
    }
    if (metaType === 'float') {
        const parsedFloat = Number.parseFloat(control.value);
        return Number.isNaN(parsedFloat) ? undefined : parsedFloat;
    }
    return control.value;
}

function dependencyFieldExists(fieldKey) {
    if (!fieldKey) {
        return false;
    }
    return modelSettingsState.controls.has(fieldKey);
}

function getDependencyFieldValue(fieldKey) {
    if (!fieldKey) {
        return undefined;
    }
    const entry = modelSettingsState.controls.get(fieldKey);
    if (!entry || !entry.control) {
        return undefined;
    }
    return extractFieldValue(entry.field, entry.control);
}

function isSingleDependencySatisfied(fieldKey, requiredValue) {
    if (!fieldKey) {
        return true;
    }
    if (!dependencyFieldExists(fieldKey)) {
        return true;
    }
    const currentValue = getDependencyFieldValue(fieldKey);

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
}

function isDependencySatisfied(field) {
    return isSingleDependencySatisfied(field?.dependency, field?.dependency_value)
        && isSingleDependencySatisfied(field?.dependency2, field?.dependency2_value);
}

function updateDependentFieldsVisibility() {
    modelSettingsState.controls.forEach(({ field, wrapper }) => {
        if ((!field?.dependency && !field?.dependency2) || !wrapper) {
            return;
        }
        const visible = isDependencySatisfied(field);
        wrapper.hidden = !visible;
        wrapper.style.display = visible ? '' : 'none';
    });
}

function attachDependencyListeners() {
    const dependencyKeys = new Set();
    modelSettingsState.controls.forEach(({ field }) => {
        if (field?.dependency) {
            dependencyKeys.add(field.dependency);
        }
        if (field?.dependency2) {
            dependencyKeys.add(field.dependency2);
        }
    });
    dependencyKeys.forEach((key) => {
        const entry = modelSettingsState.controls.get(key);
        if (!entry?.control) {
            return;
        }
        entry.control.addEventListener('change', updateDependentFieldsVisibility);
    });
}

function getCurrentModelSettingValues() {
    const result = {};
    modelSettingsState.controls.forEach(({ field, control }) => {
        if (!field || !control) {
            return;
        }
        let value = extractFieldValue(field, control);
        // Multi-select extraction normalizes no selected options to undefined.
        // For MCP servers, that exact empty selection disables every server and
        // must therefore remain explicit in the request payload.
        if (field.key === 'settings.enabled_mcp_servers' && value === undefined) {
            value = [];
        }
        if (field.type === 'boolean') {
            assignNestedValue(result, field.key, Boolean(value));
            return;
        }
        // An empty MCP-server selection has a meaningful, security-relevant
        // value: it deliberately disables every MCP server for this request.
        // Keep it in the payload instead of treating it like an unset optional
        // multi-select field.
        const preserveEmptyMcpServerSelection =
            field.key === 'settings.enabled_mcp_servers' && Array.isArray(value);
        if (
            value !== undefined &&
            value !== null &&
            (!(Array.isArray(value) && !value.length) || preserveEmptyMcpServerSelection)
        ) {
            assignNestedValue(result, field.key, value);
        }
    });

    // MCP servers are always an explicit per-request allowlist, including for
    // models that intentionally do not expose the settings sidebar. Omitting
    // this field would invoke the backend's compatibility behavior and expose
    // every eligible server, which is not appropriate for new chat requests.
    const fieldSelection = getNestedValue(result, 'settings.enabled_mcp_servers');
    const mentionedSelection = typeof window.getSelectedMcpServerIds === 'function'
        ? window.getSelectedMcpServerIds()
        : [];
    const combinedSelection = Array.from(new Set([
        ...(Array.isArray(fieldSelection) ? fieldSelection : []),
        ...(Array.isArray(mentionedSelection) ? mentionedSelection : []),
    ].map((value) => String(value || '').trim()).filter(Boolean)));
    assignNestedValue(result, 'settings.enabled_mcp_servers', combinedSelection);
    return result;
}

/**
 * Mirror a composer connector mention into the visible settings controls.
 *
 * The composer remains the fallback source of truth when the sidebar is not
 * supported or has not finished loading. When controls do exist, updating the
 * native selects also refreshes their accessible custom-select presentation.
 */
function setMcpServerEnabledForCurrentRequest(serverId, enabled = true) {
    const normalizedId = String(serverId || '').trim();
    if (!normalizedId) {
        return false;
    }

    const serverEntry = modelSettingsState.controls.get('settings.enabled_mcp_servers');
    if (serverEntry?.control?.tagName === 'SELECT') {
        const selectedValues = new Set(
            Array.from(serverEntry.control.selectedOptions || []).map((option) => option.value)
        );
        if (enabled) {
            selectedValues.add(normalizedId);
        } else {
            selectedValues.delete(normalizedId);
        }
        setModelSettingFieldValue('settings.enabled_mcp_servers', Array.from(selectedValues));
    }

    // A connector mention is an explicit request to use MCP. If the user had
    // disabled the MCP tool in the sidebar, restore that tool without changing
    // any of their other tool choices.
    if (enabled) {
        const toolsEntry = modelSettingsState.controls.get('settings.enabled_tools');
        if (toolsEntry?.control?.tagName === 'SELECT') {
            const enabledTools = new Set(
                Array.from(toolsEntry.control.selectedOptions || []).map((option) => option.value)
            );
            enabledTools.add('mcp');
            setModelSettingFieldValue('settings.enabled_tools', Array.from(enabledTools));
        }
    }

    updateDependentFieldsVisibility();
    emitModelSettingsStateChanged();
    return Boolean(serverEntry?.control);
}

function syncMentionedMcpServersIntoControls() {
    const selectedIds = typeof window.getSelectedMcpServerIds === 'function'
        ? window.getSelectedMcpServerIds()
        : [];
    selectedIds.forEach((serverId) => {
        setMcpServerEnabledForCurrentRequest(serverId, true);
    });
}

function clearMcpServersForNextRequest() {
    setModelSettingFieldValue('settings.enabled_mcp_servers', []);
    modelSettingsState.persistedValuesByModel.forEach((values, modelId) => {
        modelSettingsState.persistedValuesByModel.set(
            modelId,
            withoutRequestScopedMcpSelection(values)
        );
    });
    updateDependentFieldsVisibility();
    emitModelSettingsStateChanged();
}

function areSummaryValuesEqual(a, b) {
    if (Array.isArray(a) || Array.isArray(b)) {
        if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) {
            return false;
        }
        return a.every((value, index) => String(value) === String(b[index]));
    }
    if (typeof a === 'boolean' || typeof b === 'boolean') {
        return Boolean(a) === Boolean(b);
    }
    return String(a ?? '') === String(b ?? '');
}

function formatModelSettingSummaryValue(field, control, value) {
    if (control?.dataset?.fieldType === 'boolean') {
        return value
            ? translateModelSettingsText('chat_regenerate_summary_enabled', 'Enabled')
            : translateModelSettingsText('chat_regenerate_summary_disabled', 'Disabled');
    }

    if (control?.tagName === 'SELECT') {
        if (control.multiple) {
            const labels = Array.from(control.selectedOptions || [])
                .map((option) => option.textContent?.trim() || option.value)
                .filter(Boolean);
            return labels.join(', ');
        }
        const selectedOption = control.selectedOptions?.[0];
        return selectedOption?.textContent?.trim() || selectedOption?.value || String(value ?? '');
    }

    if (Array.isArray(value)) {
        return value.map((item) => String(item)).join(', ');
    }

    return String(value ?? '').trim();
}

function getCurrentModelSettingsSummary(options = {}) {
    const maxItems = Math.max(1, Number.parseInt(options.maxItems, 10) || 3);
    const items = [];

    modelSettingsState.controls.forEach(({ field, control }) => {
        if (!field || !control || items.length >= maxItems) {
            return;
        }

        const value = extractFieldValue(field, control);
        const defaultValue = field?.type === 'boolean'
            ? Boolean(field.default)
            : field?.default;
        const hasValue = value !== undefined && value !== null && !(Array.isArray(value) && !value.length);
        const summaryValue = hasValue ? value : Boolean(control.checked);
        const differsFromDefault = hasValue
            ? !areSummaryValuesEqual(value, defaultValue)
            : (field?.type === 'boolean' && !areSummaryValuesEqual(Boolean(control.checked), defaultValue));

        if (!hasValue && field?.type !== 'boolean') {
            return;
        }
        if (!differsFromDefault) {
            return;
        }

        const displayValue = formatModelSettingSummaryValue(field, control, summaryValue);
        if (!displayValue) {
            return;
        }

        items.push({
            key: field.key,
            label: String(field.label || field.title || field.key || '').trim(),
            value: displayValue,
        });
    });

    return {
        items,
        text: items.map((item) => `${item.label}: ${item.value}`).join(', '),
    };
}

function buildInputId(fieldKey) {
    const safeKey = fieldKey.replace(/[^a-z0-9]+/gi, '-');
    const uid = Math.random().toString(36).slice(2, 7);
    return `model-setting-${safeKey}-${uid}`;
}

function createBooleanField(field, inputId) {
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.id = inputId;
    input.classList.add('toggle-input');
    input.dataset.fieldKey = field.key;
    input.dataset.fieldType = 'boolean';
    const value = field.value;
    input.checked = value === undefined ? Boolean(field.default) : Boolean(value);
    return input;
}

function createSelectField(field, inputId) {
    const select = document.createElement('select');
    select.id = inputId;
    select.dataset.fieldKey = field.key;
    select.dataset.fieldType = 'select';
    if (field.multiple) {
        select.multiple = true;
    }
    const currentValue = field.value;
    const values = Array.isArray(currentValue) ? currentValue.map(String) : [currentValue].filter(Boolean).map(String);
    const options = Array.isArray(field.options) ? field.options : [];
    const includeBlank = !field.required && !select.multiple;
    if (includeBlank) {
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = field.placeholder || translateModelSettingsText('model_settings_select_placeholder', 'Select...');
        select.appendChild(blank);
    }
    options.forEach((option) => {
        const opt = document.createElement('option');
        opt.value = String(option.value ?? '');
        opt.textContent = translateModelSettingsOption(option, field.key);
        if (values.includes(opt.value)) {
            opt.selected = true;
        }
        select.appendChild(opt);
    });

    if (field.multiple) {
        select._multiSelect = initializeModelSettingsMultiSelect(select, field);
    }
    return select;
}

/** Return whether a custom or native multi-select option is currently usable. */
function isModelSettingsMultiSelectOptionAvailable(option) {
    return Boolean(
        option
        && !option.hidden
        && !option.disabled
        && option.getAttribute?.('aria-disabled') !== 'true'
    );
}

/** Collect the options that may receive keyboard focus in a custom listbox. */
function getInteractiveModelSettingsMultiSelectOptions(menu) {
    return Array.from(
        menu?.querySelectorAll?.('.model-settings-multiselect-option') || []
    ).filter(isModelSettingsMultiSelectOptionAvailable);
}

/** Mirror native option availability onto its accessible custom button. */
function mirrorModelSettingsMultiSelectOptionAvailability(option, button) {
    if (!option || !button) return;
    const available = isModelSettingsMultiSelectOptionAvailable(option);
    button.hidden = !available;
    button.disabled = !available;
    button.setAttribute('aria-disabled', available ? 'false' : 'true');
}

/** Synchronize every custom option with the current native option state. */
function syncModelSettingsMultiSelectOptions(selectOptions, optionButtons, updateOptionButton) {
    selectOptions.forEach((option, value) => {
        mirrorModelSettingsMultiSelectOptionAvailability(option, optionButtons.get(value));
        updateOptionButton(value, Boolean(option.selected));
    });
}

/** Apply a bulk selection only to options that remain available to the user. */
function setAvailableModelSettingsMultiSelectOptions(selectOptions, selected, updateOptionButton) {
    selectOptions.forEach((option, value) => {
        if (!isModelSettingsMultiSelectOptionAvailable(option)) return;
        option.selected = selected;
        updateOptionButton(value, selected);
    });
}

function initializeModelSettingsMultiSelect(select, field) {
    const wrapper = document.createElement('div');
    wrapper.className = 'model-settings-multiselect';

    let typeBuffer = '';
    let lastTypeTs = 0;
    const searchConfig = field?.search || {};
    const isSearchable = Boolean(field?.searchable || searchConfig.enabled);

    const isLikelyMobileViewport = () => {
        if (typeof window.matchMedia !== 'function') {
            return false;
        }
        return (
            window.matchMedia('(max-width: 768px)').matches
            || window.matchMedia('(hover: none) and (pointer: coarse)').matches
        );
    };

    const triggerId = `${select.id || `${field.key}-multi`}-trigger`;
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.id = triggerId;
    trigger.className = 'model-settings-multiselect-trigger placeholder';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');

    const triggerLabel = document.createElement('span');
    triggerLabel.className = 'model-settings-multiselect-value';
    trigger.appendChild(triggerLabel);

    const triggerCaret = document.createElement('span');
    triggerCaret.className = 'model-settings-multiselect-caret';
    trigger.appendChild(triggerCaret);

    const menu = document.createElement('div');
    menu.className = 'model-settings-multiselect-menu';
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-multiselectable', 'true');
    menu.hidden = true;

    wrapper.append(trigger, menu);

    let searchInput = null;
    if (isSearchable) {
        const searchContainer = document.createElement('label');
        searchContainer.className = 'model-settings-multiselect-search';

        const searchIcon = document.createElement('span');
        searchIcon.className = 'model-settings-multiselect-search-icon';
        searchIcon.setAttribute('aria-hidden', 'true');
        searchIcon.innerHTML = typeof Icons !== 'undefined' ? (Icons.magnifyingGlass || '') : '';
        searchContainer.appendChild(searchIcon);

        const searchPlaceholder = searchConfig.placeholder || translateModelSettingsText(
            'model_settings_search_tools_placeholder',
            'Search tools...'
        );
        searchInput = document.createElement('input');
        searchInput.type = 'search';
        searchInput.className = 'model-settings-multiselect-search-input';
        searchInput.placeholder = searchPlaceholder;
        searchInput.setAttribute('aria-label', searchPlaceholder);
        if (!searchConfig.placeholder) {
            searchInput.setAttribute(
                'data-i18n-attr',
                'placeholder:model_settings_search_tools_placeholder;aria-label:model_settings_search_tools_placeholder'
            );
        }
        searchInput.autocomplete = 'off';
        searchInput.spellcheck = false;
        searchContainer.appendChild(searchInput);

        menu.appendChild(searchContainer);
    }

    const selectAllActions = document.createElement('div');
    selectAllActions.className = 'model-settings-multiselect-actions';

    const selectAllBtn = document.createElement('button');
    selectAllBtn.type = 'button';
    selectAllBtn.className = 'model-settings-multiselect-action-btn';
    selectAllBtn.textContent = translateModelSettingsText('model_settings_select_all', 'Select All');

    const unselectAllBtn = document.createElement('button');
    unselectAllBtn.type = 'button';
    unselectAllBtn.className = 'model-settings-multiselect-action-btn';
    unselectAllBtn.textContent = translateModelSettingsText('model_settings_unselect_all', 'Unselect All');

    selectAllActions.append(selectAllBtn, unselectAllBtn);
    menu.appendChild(selectAllActions);

    /** Return only options that keyboard users can currently interact with. */
    const getFocusableOptions = () => getInteractiveModelSettingsMultiSelectOptions(menu);

    const clearKeyboardFocus = () => {
        menu.querySelectorAll('.model-settings-multiselect-option.keyboard-focus').forEach((option) => {
            option.classList.remove('keyboard-focus');
        });
    };

    const focusOption = (option) => {
        if (!option) {
            return;
        }
        clearKeyboardFocus();
        option.classList.add('keyboard-focus');
        option.focus();
        option.scrollIntoView({ block: 'nearest' });
    };

    const clearTypeAhead = () => {
        typeBuffer = '';
        lastTypeTs = 0;
        clearKeyboardFocus();
    };

    const handleTypeAhead = (event) => {
        if (!wrapper.classList.contains('open')) {
            return false;
        }

        if (event.key === 'Enter') {
            const focused =
                menu.querySelector('.model-settings-multiselect-option.keyboard-focus') ||
                document.activeElement;
            if (focused && getFocusableOptions().includes(focused)) {
                event.preventDefault();
                focused.click();
                return true;
            }
            return false;
        }

        if (event.key === 'Escape') {
            event.preventDefault();
            closeMenu();
            trigger.focus();
            return true;
        }

        if (event.key.length !== 1 || event.metaKey || event.ctrlKey || event.altKey) {
            return false;
        }

        const now = Date.now();
        if (now - lastTypeTs > 700) {
            typeBuffer = '';
        }
        lastTypeTs = now;

        typeBuffer += event.key.toLowerCase();

        const options = getFocusableOptions();
        if (!options.length) {
            return false;
        }

        const keyboardTarget =
            menu.querySelector('.model-settings-multiselect-option.keyboard-focus') || document.activeElement;
        const startIndex = options.indexOf(keyboardTarget);
        const ordered =
            startIndex >= 0
                ? [...options.slice(startIndex + 1), ...options.slice(0, startIndex + 1)]
                : options;

        let match = ordered.find((opt) =>
            (opt.textContent || '').trim().toLowerCase().startsWith(typeBuffer)
        );

        if (!match && typeBuffer.length > 1) {
            typeBuffer = event.key.toLowerCase();
            match = ordered.find((opt) =>
                (opt.textContent || '').trim().toLowerCase().startsWith(typeBuffer)
            );
        }

        if (match) {
            event.preventDefault();
            focusOption(match);
            return true;
        }

        return false;
    };

    const optionButtons = new Map();
    const selectOptions = new Map();

    Array.from(select.options).forEach((opt) => {
        const value = String(opt.value ?? '');
        selectOptions.set(value, opt);

        const optionButton = document.createElement('button');
        optionButton.type = 'button';
        optionButton.tabIndex = -1;
        optionButton.className = 'model-settings-multiselect-option';
        optionButton.dataset.value = value;
        optionButton.dataset.searchText = String(opt.textContent || value || '—').trim().toLowerCase();
        optionButton.setAttribute('role', 'option');
        optionButton.setAttribute('aria-selected', opt.selected ? 'true' : 'false');

        const check = document.createElement('span');
        check.className = 'model-settings-multiselect-check';
        optionButton.appendChild(check);

        const text = document.createElement('span');
        text.className = 'model-settings-multiselect-text';
        text.textContent = opt.textContent || value || '—';
        optionButton.appendChild(text);

        if (opt.selected) {
            optionButton.classList.add('selected');
        }

        optionButton.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            toggleValue(value);
        });

        optionButtons.set(value, optionButton);
        menu.appendChild(optionButton);
    });

    const empty = document.createElement('div');
    empty.className = 'model-settings-multiselect-empty';
    empty.setAttribute('role', 'status');
    empty.setAttribute('aria-live', 'polite');
    empty.hidden = true;
    menu.appendChild(empty);

    const updateEmptyState = () => {
        const hasVisibleOption = Array.from(optionButtons.values()).some((button) => !button.hidden);
        if (hasVisibleOption) {
            empty.hidden = true;
            return;
        }
        empty.textContent = searchInput?.value.trim()
            ? translateModelSettingsText('model_settings_no_matching_tools', 'No matching tools')
            : translateModelSettingsText('model_settings_no_options_available', 'No options available');
        empty.hidden = false;
    };

    const filterOptions = () => {
        const searchTerm = (searchInput?.value || '').trim().toLowerCase();
        optionButtons.forEach((button, value) => {
            mirrorModelSettingsMultiSelectOptionAvailability(selectOptions.get(value), button);
            if (!button.hidden && searchTerm) {
                button.hidden = !(button.dataset.searchText || '').includes(searchTerm);
            }
        });
        updateEmptyState();
    };

    select.classList.add('model-settings-multiselect-native');
    select.setAttribute('aria-hidden', 'true');
    select.tabIndex = -1;
    wrapper.appendChild(select);

    const placeholder = field.placeholder || translateModelSettingsText('model_settings_select_placeholder', 'Select...');

    const updateSummary = () => {
        const selectedOptions = Array.from(select.selectedOptions);
        if (!selectedOptions.length) {
            triggerLabel.textContent = placeholder;
            trigger.classList.add('placeholder');
            return;
        }

        trigger.classList.remove('placeholder');
        if (selectedOptions.length <= 2) {
            triggerLabel.textContent = selectedOptions.map((opt) => opt.textContent).join(', ');
            return;
        }
        triggerLabel.textContent = window.formatTranslation
            ? window.formatTranslation(
                'model_settings_multiselect_selected_count',
                '{count} selected',
                { count: selectedOptions.length }
            )
            : translateModelSettingsText(
                'model_settings_multiselect_selected_count',
                '{count} selected'
            ).replace('{count}', selectedOptions.length);
    };

    const updateOptionButton = (value, selected) => {
        const button = optionButtons.get(value);
        if (!button) {
            return;
        }
        button.classList.toggle('selected', selected);
        button.setAttribute('aria-selected', selected ? 'true' : 'false');
    };

    const toggleValue = (value) => {
        const option = selectOptions.get(value);
        if (!isModelSettingsMultiSelectOptionAvailable(option)) {
            return;
        }
        const nextSelected = !option.selected;
        option.selected = nextSelected;
        updateOptionButton(value, nextSelected);
        updateSummary();
        select.dispatchEvent(new Event('change', { bubbles: true }));
    };

    const syncFromSelect = () => {
        syncModelSettingsMultiSelectOptions(selectOptions, optionButtons, updateOptionButton);
        updateSummary();
        filterOptions();
    };

    select.addEventListener('change', syncFromSelect);

    selectAllBtn.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        setAvailableModelSettingsMultiSelectOptions(selectOptions, true, updateOptionButton);
        updateSummary();
        select.dispatchEvent(new Event('change', { bubbles: true }));
    });

    unselectAllBtn.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        setAvailableModelSettingsMultiSelectOptions(selectOptions, false, updateOptionButton);
        updateSummary();
        select.dispatchEvent(new Event('change', { bubbles: true }));
    });

    const closeMenu = () => {
        if (!wrapper.classList.contains('open')) {
            return;
        }
        wrapper.classList.remove('open');
        menu.classList.remove('open');
        menu.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
        document.removeEventListener('click', handleDocumentClick, true);
        document.removeEventListener('keydown', handleKeydown, true);
        if (searchInput) {
            searchInput.value = '';
            filterOptions();
        }
        clearTypeAhead();
    };

    const handleDocumentClick = (event) => {
        if (!wrapper.contains(event.target)) {
            closeMenu();
        }
    };

    const handleKeydown = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeMenu();
            trigger.focus();
        }
    };

    wrapper._closeMenu = closeMenu;

    const openMenu = () => {
        document.querySelectorAll('.model-settings-multiselect.open').forEach((openWrapper) => {
            if (openWrapper !== wrapper && typeof openWrapper._closeMenu === 'function') {
                openWrapper._closeMenu();
            }
        });
        menu.hidden = false;
        wrapper.classList.add('open');
        menu.classList.add('open');
        trigger.setAttribute('aria-expanded', 'true');
        document.addEventListener('click', handleDocumentClick, true);
        document.addEventListener('keydown', handleKeydown, true);
        clearTypeAhead();
        if (searchInput && !isLikelyMobileViewport()) {
            searchInput.focus();
            return;
        }
        focusInitialOption();
    };

    trigger.addEventListener('click', (event) => {
        event.preventDefault();
        if (wrapper.classList.contains('open')) {
            closeMenu();
        } else {
            openMenu();
        }
    });

    trigger.addEventListener('keydown', (event) => {
        if (handleTypeAhead(event)) {
            return;
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!wrapper.classList.contains('open')) {
                openMenu();
            }
        } else if (event.key === 'Tab' && wrapper.classList.contains('open')) {
            closeMenu();
        }
    });

    const focusInitialOption = () => {
        const focusableOptions = getFocusableOptions();
        const firstSelected = focusableOptions.find((option) => option.classList.contains('selected'));
        focusOption(firstSelected || focusableOptions[0]);
    };

    menu.addEventListener('keydown', (event) => {
        if (searchInput && event.target === searchInput) {
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                focusInitialOption();
            } else if (event.key === 'Tab') {
                closeMenu();
            }
            return;
        }

        if (handleTypeAhead(event)) {
            return;
        }
        const focusableOptions = getFocusableOptions();
        if (!focusableOptions.length) {
            return;
        }

        const currentIndex = focusableOptions.indexOf(document.activeElement);
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            const nextIndex = currentIndex === -1 ? 0 : Math.min(focusableOptions.length - 1, currentIndex + 1);
            focusOption(focusableOptions[nextIndex]);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            const prevIndex = currentIndex <= 0 ? 0 : currentIndex - 1;
            focusOption(focusableOptions[prevIndex]);
        } else if (event.key === 'Home') {
            event.preventDefault();
            focusOption(focusableOptions[0]);
        } else if (event.key === 'End') {
            event.preventDefault();
            focusOption(focusableOptions[focusableOptions.length - 1]);
        } else if (event.key === 'Tab') {
            closeMenu();
        }
    });

    searchInput?.addEventListener('input', filterOptions);

    syncFromSelect();

    // Expose synchronization so settings surfaces that reuse this accessible
    // multi-select can update selections or translated labels in place.
    return { wrapper, triggerId, syncFromSelect, openMenu, closeMenu };
}

function createTextualField(field, inputId) {
    const inputType = (field.input_type || '').toLowerCase();
    const isStructuredMapping = isStructuredMappingInputType(inputType);
    const isTextarea = inputType === 'list[str]' || inputType === 'textarea' || isStructuredMapping;
    const control = isTextarea ? document.createElement('textarea') : document.createElement('input');
    if (!isTextarea) {
        let nativeType = 'text';
        if (inputType === 'int') {
            nativeType = 'number';
            control.step = '1';
        } else if (inputType === 'float') {
            nativeType = 'number';
            control.step = 'any';
        } else if (inputType === 'date') {
            nativeType = 'date';
        }
        control.type = nativeType;
    }
    control.id = inputId;
    control.dataset.fieldKey = field.key;
    control.dataset.fieldType = 'text';
    control.dataset.inputType = inputType;
    if (field.placeholder) {
        control.placeholder = field.placeholder;
    }
    if (field.attributes) {
        const { min, max, step } = field.attributes;
        if (min !== undefined) control.min = min;
        if (max !== undefined) control.max = max;
        if (step !== undefined && control.type === 'number') control.step = step;
    }
    if (isStructuredMapping && field.value && typeof field.value === 'object' && !Array.isArray(field.value)) {
        control.value = JSON.stringify(field.value, null, 2);
    } else if (Array.isArray(field.value) && isTextarea) {
        control.value = field.value.join('\n');
    } else if (field.value !== undefined && field.value !== null) {
        control.value = field.value;
    }
    return control;
}

function createFieldControl(field) {
    const inputId = buildInputId(field.key);
    if (field.type === 'boolean') {
        return createBooleanField(field, inputId);
    }
    if (field.type === 'select') {
        return createSelectField(field, inputId);
    }
    return createTextualField(field, inputId);
}

function renderField(field) {
    const wrapper = document.createElement('div');
    wrapper.className = 'model-settings-field';
    const control = createFieldControl(field);
    const multiSelectMeta = control?._multiSelect;
    const label = document.createElement('label');
    label.className = 'model-settings-label';
    label.htmlFor = multiSelectMeta?.triggerId || control.id;
    label.textContent = field.label || field.key;
    const description = document.createElement('p');
    description.className = 'model-settings-description';
    if (field.description) {
        description.textContent = field.description;
    } else {
        description.hidden = true;
    }

    if (control.type === 'checkbox') {
        wrapper.classList.add('model-settings-field-toggle');
        const header = document.createElement('div');
        header.className = 'model-settings-toggle-header';

        const textColumn = document.createElement('div');
        textColumn.className = 'model-settings-toggle-text';
        textColumn.appendChild(label);
        if (field.description) {
            textColumn.appendChild(description);
        }

        const toggleSwitch = document.createElement('label');
        toggleSwitch.className = 'toggle-switch';
        toggleSwitch.setAttribute('aria-label', field.label || field.key);
        toggleSwitch.appendChild(control);
        const slider = document.createElement('span');
        slider.className = 'toggle-slider';
        toggleSwitch.appendChild(slider);

        header.appendChild(textColumn);
        header.appendChild(toggleSwitch);

        wrapper.appendChild(header);
    } else {
        const fieldBody = document.createElement('div');
        fieldBody.className = 'model-settings-input';
        if (multiSelectMeta) {
            fieldBody.appendChild(multiSelectMeta.wrapper);
        } else {
            fieldBody.appendChild(control);
        }
        wrapper.appendChild(label);
        if (field.description) {
            wrapper.appendChild(description);
        }
        wrapper.appendChild(fieldBody);
    }
    modelSettingsState.controls.set(field.key, { field, control, wrapper });
    const handleControlUpdate = () => {
        if (isStructuredMappingInputType(field.input_type)) {
            extractFieldValue(field, control);
        }
        updateDependentFieldsVisibility();
        persistLiveModelValues();
        emitModelSettingsStateChanged();
    };
    control.addEventListener('change', handleControlUpdate);
    if (isStructuredMappingInputType(field.input_type)) {
        control.addEventListener('input', handleControlUpdate);
        extractFieldValue(field, control);
    }
    return wrapper;
}

function validateCurrentModelSettings({ report = true } = {}) {
    for (const { field, control, wrapper } of modelSettingsState.controls.values()) {
        if (!field || !control || wrapper?.hidden) {
            continue;
        }
        extractFieldValue(field, control);
        if (typeof control.checkValidity === 'function' && !control.checkValidity()) {
            if (report && typeof control.reportValidity === 'function') {
                control.reportValidity();
            }
            if (report && typeof control.focus === 'function') {
                control.focus();
            }
            return false;
        }
    }
    return true;
}

function renderSection(section) {
    const sectionEl = document.createElement('section');
    sectionEl.className = 'model-settings-section';
    if (section.title) {
        const header = document.createElement('header');
        header.className = 'model-settings-section-header';
        const title = document.createElement('h4');
        title.textContent = section.title;
        header.appendChild(title);
        if (section.description) {
            const desc = document.createElement('p');
            desc.textContent = section.description;
            header.appendChild(desc);
        }
        sectionEl.appendChild(header);
    }
    const body = document.createElement('div');
    body.className = 'model-settings-section-body';
    (section.fields || []).forEach((field) => {
        if (!field?.key) {
            return;
        }
        const fieldNode = renderField(field);
        body.appendChild(fieldNode);
    });
    sectionEl.appendChild(body);
    return sectionEl;
}

function renderModelSettingsSchema(schema) {
    const container = getModelSettingsSidebarContainer();
    modelSettingsState.controls.clear();
    if (!container) {
        return;
    }
    const translatedSchema = translateModelSettingsSchema(schema);
    if (!translatedSchema || !Array.isArray(translatedSchema.sections) || !translatedSchema.sections.length) {
        showModelSettingsEmpty(translateModelSettingsText(
            'model_settings_no_adjustable_settings',
            'This model has no adjustable settings.'
        ));
        return;
    }
    const fragment = document.createDocumentFragment();
    translatedSchema.sections.forEach((section) => {
        const renderedSection = renderSection(section);
        fragment.appendChild(renderedSection);
    });
    container.innerHTML = '';
    container.appendChild(fragment);
    modelSettingsState.schema = translatedSchema;
    attachDependencyListeners();
    updateDependentFieldsVisibility();
    syncMentionedMcpServersIntoControls();
    emitModelSettingsStateChanged();
}

async function fetchModelSettingsSchema(modelId, provider, projectId) {
    if (!modelId || !provider) {
        return null;
    }
    const params = new URLSearchParams({
        model_id: modelId,
        provider,
    });
    if (projectId) {
        params.set('project_id', projectId);
    }
    const response = await window.authedFetch(`/api/v1/llm/model/settings?${params.toString()}`);
    if (!response.ok) {
        throw new Error(`Failed to load settings schema (${response.status})`);
    }
    const payload = await response.json();
    if (!payload || typeof payload !== 'object') {
        return null;
    }
    const [supportedFileFormats] = await Promise.all([
        expandSupportedFileFormatsFromSchemaPayload(payload),
        hydrateMcpServerOptions(payload, modelId, projectId),
        waitForModelSettingsTranslations(),
    ]);
    // Keep downstream upload filtering independent from the compact wire
    // representation. Existing consumers continue to receive the same shape.
    payload.supported_file_formats = supportedFileFormats;
    return payload;
}

async function fetchModelSettingsSchemaForModel(modelId, projectId = getActiveProjectId()) {
    const normalizedModelId = modelId ? String(modelId) : null;
    if (!normalizedModelId) {
        return null;
    }

    const lookup = await ensureModelLookup();
    const modelMeta = lookup.get(normalizedModelId);
    if (!modelMeta || !modelMeta.provider) {
        return null;
    }

    if (modelMeta.is_byok && typeof window.BYOK?.getModelSettingsSchemaForModel === 'function') {
        const schemaPayload = await window.BYOK.getModelSettingsSchemaForModel(normalizedModelId);
        if (!schemaPayload?.supported || !schemaPayload?.schema) {
            return {
                supported: false,
                schema: null,
                supported_file_formats: [],
            };
        }
        return {
            ...schemaPayload,
            supported_file_formats: [],
        };
    }

    return fetchModelSettingsSchema(normalizedModelId, modelMeta.provider, projectId || '');
}

function getActiveProjectId() {
    const chatContainer = document.getElementById('chatContainer');
    return chatContainer?.getAttribute('data-project-id') || '';
}

function getLoadedModelSettingsModelId() {
    return modelSettingsState.activeModelId;
}

function getLoadedModelSettingsProjectId() {
    return modelSettingsState.activeProjectId || '';
}

async function reloadModelSettingsIfNeeded({ awaitReload = false } = {}) {
    if (typeof window === 'undefined' || typeof window.loadModelSettingsFor !== 'function') {
        return false;
    }
    const activeModelId = typeof window.getActiveModelId === 'function' ? window.getActiveModelId() : null;
    const loadedModelId = typeof window.getLoadedModelSettingsModelId === 'function'
        ? window.getLoadedModelSettingsModelId()
        : null;
    const loadedProjectId = typeof window.getLoadedModelSettingsProjectId === 'function'
        ? window.getLoadedModelSettingsProjectId()
        : '';
    const activeProjectId = String(document.getElementById('chatContainer')?.getAttribute('data-project-id') || '');
    if (
        activeModelId
        && (
            String(activeModelId) !== String(loadedModelId || '')
            || activeProjectId !== String(loadedProjectId || '')
        )
    ) {
        const promise = window.loadModelSettingsFor(activeModelId);
        if (awaitReload) {
            await promise;
        }
        return true;
    }
    return false;
}

function getActiveModelId() {
    const select = document.getElementById('modelSelect');
    return select?.getAttribute('data-model-id') || null;
}

async function loadModelSettingsFor(modelId, options = {}) {
    persistCurrentModelValues();

    const normalizedModelId = modelId ? String(modelId) : null;
    const normalizedProjectId = getActiveProjectId() || '';
    const forceReload = options?.force === true;
    const preferredQuickThinkingValue = options?.preferredQuickThinkingValue;
    const fallbackQuickThinkingState = options?.fallbackQuickThinkingState || getQuickThinkingControlState();

    if (
        normalizedModelId
        && normalizedModelId === modelSettingsState.activeModelId
        && normalizedProjectId === (modelSettingsState.activeProjectId || '')
        && modelSettingsState.schema
        && !forceReload
    ) {
        return;
    }

    if (!normalizedModelId) {
        clearQuickThinkingLoadingFallback();
        modelSettingsState.activeModelId = null;
        modelSettingsState.activeProjectId = '';
        setPresetUiAvailability(false);
        showModelSettingsEmpty('Select a model to configure settings.');
        // Reset presets
        loadPresetsForModel(null);
        return;
    }

    const lookup = await ensureModelLookup();
    const modelMeta = lookup.get(normalizedModelId);
    if (!modelMeta || !modelMeta.provider) {
        clearQuickThinkingLoadingFallback();
        setPresetUiAvailability(false);
        showModelSettingsEmpty(translateModelSettingsText(
            'model_settings_provider_metadata_missing',
            'Provider metadata missing for this model.'
        ));
        loadPresetsForModel(null);
        return;
    }

    const requestToken = `${normalizedModelId}-${Date.now()}`;
    modelSettingsState.lastRequestToken = requestToken;
    modelSettingsState.activeModelId = normalizedModelId;
    modelSettingsState.activeProjectId = normalizedProjectId;
    const cachedQuickThinkingState = getCachedQuickThinkingControlState(normalizedModelId);
    setQuickThinkingLoadingFallback(
        cachedQuickThinkingState || fallbackQuickThinkingState,
        normalizedModelId
    );
    showModelSettingsLoading();
    // Reset presets state while determining availability
    loadPresetsForModel(null);

    try {
        if (modelMeta.is_byok && typeof window.BYOK?.getModelSettingsSchemaForModel === 'function') {
            const schemaPayload = await window.BYOK.getModelSettingsSchemaForModel(normalizedModelId);
            if (modelSettingsState.lastRequestToken !== requestToken) {
                return;
            }
            if (!schemaPayload?.supported || !schemaPayload?.schema) {
                setPresetUiAvailability(false);
                clearQuickThinkingLoadingFallback();
                showModelSettingsEmpty(translateModelSettingsText(
                    'model_settings_byok_no_editable_settings',
                    'This BYOK model has no editable settings.'
                ));
                return;
            }
            storeSupportedFileFormatsFromSchemaPayload({ supported_file_formats: [] }, normalizedModelId);
            setPresetUiAvailability(false);
            renderModelSettingsSchema(schemaPayload.schema);
            applyPreferredQuickThinkingValue(preferredQuickThinkingValue);
            clearQuickThinkingLoadingFallback();
            return;
        }

        const schemaPayload = await fetchModelSettingsSchema(
            normalizedModelId,
            modelMeta.provider,
            normalizedProjectId
        );
        if (modelSettingsState.lastRequestToken !== requestToken) {
            return;
        }
        if (!schemaPayload) {
            setPresetUiAvailability(false);
            clearQuickThinkingLoadingFallback();
            showModelSettingsEmpty(translateModelSettingsText(
                'model_settings_no_settings_returned',
                'No settings returned for this model.'
            ));
            return;
        }
        storeSupportedFileFormatsFromSchemaPayload(schemaPayload, normalizedModelId);
        if (!schemaPayload.supported || !schemaPayload.schema) {
            setPresetUiAvailability(false);
            clearQuickThinkingLoadingFallback();
            showModelSettingsEmpty(
                translateModelSettingsBackendMessage(schemaPayload.message)
                || translateModelSettingsText(
                    'model_settings_custom_settings_unsupported',
                    'This model does not support custom model settings.'
                )
            );
            return;
        }

        setPresetUiAvailability(true);
        const schemaWithPersistedValues = applyPersistedValuesToSchema(schemaPayload.schema, normalizedModelId) || schemaPayload.schema;
        renderModelSettingsSchema(schemaWithPersistedValues);
        applyPreferredQuickThinkingValue(preferredQuickThinkingValue);
        clearQuickThinkingLoadingFallback();
        // Load presets for supported models (don't await to avoid blocking)
        loadPresetsForModel(normalizedModelId);
    } catch (error) {
        console.error('Model settings: failed to fetch schema', error);
        if (modelSettingsState.lastRequestToken === requestToken) {
            setPresetUiAvailability(false);
            clearQuickThinkingLoadingFallback();
            showModelSettingsEmpty(translateModelSettingsText(
                'model_settings_load_failed_retry',
                'Unable to load settings. Please try again later.'
            ));
        }
    }
}

// =====================
// Preset Functions
// =====================

async function fetchPresetsList(modelId) {
    if (!modelId) return [];
    try {
        const response = await window.authedFetch(`/api/v1/llm/models/${modelId}/presets`);
        if (!response.ok) {
            console.error('Failed to fetch presets', response.status);
            return [];
        }
        return await response.json();
    } catch (error) {
        console.error('Error fetching presets:', error);
        return [];
    }
}

async function fetchPresetDetail(modelId, presetId) {
    if (!modelId || !presetId) return null;
    try {
        const response = await window.authedFetch(`/api/v1/llm/models/${modelId}/presets/${presetId}`);
        if (!response.ok) {
            console.error('Failed to fetch preset detail', response.status);
            return null;
        }
        return await response.json();
    } catch (error) {
        console.error('Error fetching preset detail:', error);
        return null;
    }
}

async function savePreset(modelId, name, settings) {
    if (!modelId || !name) return null;
    try {
        const response = await window.authedFetch(`/api/v1/llm/models/${modelId}/presets`, {
            method: 'POST',
            body: JSON.stringify({ name, settings }),
        });
        if (!response.ok) {
            console.error('Failed to save preset', response.status);
            return null;
        }
        return await response.json();
    } catch (error) {
        console.error('Error saving preset:', error);
        return null;
    }
}

async function deletePreset(modelId, presetId) {
    if (!modelId || !presetId) return false;
    try {
        const response = await window.authedFetch(`/api/v1/llm/models/${modelId}/presets/${presetId}`, {
            method: 'DELETE',
        });
        return response.ok;
    } catch (error) {
        console.error('Error deleting preset:', error);
        return false;
    }
}

function renderPresetsDropdown() {
    const listEl = document.getElementById('presetsDropdownList');
    const labelEl = document.getElementById('presetsDropdownLabel');
    if (!listEl) return;

    listEl.innerHTML = '';

    // Add "None" option first
    const noneItem = document.createElement('div');
    noneItem.className = 'presets-dropdown-item' + (modelSettingsState.selectedPresetId === null ? ' active' : '');
    const noneName = document.createElement('span');
    noneName.className = 'presets-dropdown-item-name';
    noneName.textContent = translateModelSettingsText('model_settings_preset_none', 'None');
    noneItem.appendChild(noneName);
    noneItem.addEventListener('click', (e) => {
        e.stopPropagation();
        selectPreset(null);
        closePresetsDropdown();
    });
    listEl.appendChild(noneItem);

    // Add preset items
    if (modelSettingsState.presets.length === 0) {
        // No presets yet, just show the None option
    } else {
        modelSettingsState.presets.forEach((preset) => {
            const item = document.createElement('div');
            item.className = 'presets-dropdown-item' + (modelSettingsState.selectedPresetId === preset.id ? ' active' : '');
            
            const nameSpan = document.createElement('span');
            nameSpan.className = 'presets-dropdown-item-name';
            nameSpan.textContent = preset.name;
            item.appendChild(nameSpan);

            const deleteBtn = document.createElement('button');
            deleteBtn.type = 'button';
            deleteBtn.className = 'presets-dropdown-item-delete';
            deleteBtn.innerHTML = Icons.trash;
            deleteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                openDeletePresetOverlay(preset.id, preset.name);
            });
            item.appendChild(deleteBtn);

            item.addEventListener('click', (e) => {
                if (e.target.closest('.presets-dropdown-item-delete')) return;
                e.stopPropagation();
                selectPreset(preset.id);
                closePresetsDropdown();
            });

            listEl.appendChild(item);
        });
    }

    // Update label
    if (labelEl) {
        if (modelSettingsState.selectedPresetId === null) {
            labelEl.textContent = translateModelSettingsText('model_settings_preset_none', 'None');
        } else {
            const selected = modelSettingsState.presets.find(p => p.id === modelSettingsState.selectedPresetId);
            labelEl.textContent = selected ? selected.name : translateModelSettingsText('model_settings_preset_none', 'None');
        }
    }
}


function closePresetsDropdown() {
    const dropdown = document.getElementById('presetsDropdown');
    if (dropdown) {
        dropdown.classList.remove('open');
    }
}

function setPresetUiAvailability(enabled) {
    modelSettingsState.presetsEnabled = Boolean(enabled);
    const container = document.getElementById('presetsContainer');
    if (container) {
        container.hidden = !enabled;
    }
    if (!enabled) {
        closePresetsDropdown();
        closeSavePresetOverlay();
        closeDeletePresetOverlay();
    }
}

function togglePresetsDropdown() {
    const dropdown = document.getElementById('presetsDropdown');
    if (dropdown) {
        dropdown.classList.toggle('open');
    }
}

async function selectPreset(presetId) {
    modelSettingsState.selectedPresetId = presetId;
    renderPresetsDropdown();

    if (presetId === null) {
        // Clear all settings to empty/default
        clearAllSettingValues();
        return;
    }

    // Load preset values and apply them
    const modelId = modelSettingsState.activeModelId;
    if (!modelId) return;

    const presetDetail = await fetchPresetDetail(modelId, presetId);
    if (!presetDetail || !presetDetail.settings) {
        console.error('Failed to load preset detail');
        return;
    }

    applyPresetValues(presetDetail.settings);
}

function clearAllSettingValues() {
    modelSettingsState.controls.forEach(({ field, control }) => {
        if (!control) return;

        if (control.dataset.fieldType === 'boolean') {
            control.checked = false;
        } else if (control.tagName === 'SELECT') {
            if (control.multiple) {
                Array.from(control.options).forEach(opt => opt.selected = false);
                // Update custom multi-select if present
                if (control._multiSelect) {
                    control.dispatchEvent(new Event('change', { bubbles: true }));
                }
            } else {
                control.value = '';
            }
        } else if (control.tagName === 'TEXTAREA' || control.tagName === 'INPUT') {
            control.value = '';
        }
    });
    updateDependentFieldsVisibility();
    persistLiveModelValues();
    emitModelSettingsStateChanged();
}

function getNestedValue(obj, keyPath) {
    if (!obj || !keyPath) return undefined;
    const segments = keyPath.split('.');
    let cursor = obj;
    for (const segment of segments) {
        if (cursor === null || cursor === undefined) return undefined;
        cursor = cursor[segment];
    }
    return cursor;
}

function flattenModelSettingValues(settings, prefix = '') {
    if (!settings || typeof settings !== 'object' || Array.isArray(settings)) {
        return [];
    }
    return Object.entries(settings).flatMap(([key, value]) => {
        const path = prefix ? `${prefix}.${key}` : key;
        if (value && typeof value === 'object' && !Array.isArray(value)) {
            const schemaField = getSchemaFieldByKey(path);
            if (schemaField && isStructuredMappingInputType(schemaField.input_type)) {
                return [[path, value]];
            }
            return flattenModelSettingValues(value, path);
        }
        return [[path, value]];
    });
}

function applyModelSettingValues(settings = {}) {
    flattenModelSettingValues(settings).forEach(([fieldKey, value]) => {
        setModelSettingFieldValue(fieldKey, value);
    });
    updateDependentFieldsVisibility();
    persistLiveModelValues();
    emitModelSettingsStateChanged();
}

function applyPresetValues(settings) {
    // First clear all values
    clearAllSettingValues();

    // Then apply preset values
    modelSettingsState.controls.forEach(({ field, control }) => {
        if (!control || !field?.key) return;

        const value = getNestedValue(settings, field.key);
        if (value === undefined || value === null) return;

        if (control.dataset.fieldType === 'boolean') {
            control.checked = Boolean(value);
        } else if (control.tagName === 'SELECT') {
            if (control.multiple && Array.isArray(value)) {
                Array.from(control.options).forEach(opt => {
                    opt.selected = value.includes(opt.value);
                });
                // Update custom multi-select if present
                if (control._multiSelect) {
                    control.dispatchEvent(new Event('change', { bubbles: true }));
                }
            } else {
                control.value = String(value);
            }
        } else if (control.tagName === 'TEXTAREA') {
            if (isStructuredMappingInputType(field.input_type) && value && typeof value === 'object' && !Array.isArray(value)) {
                control.value = JSON.stringify(value, null, 2);
                extractFieldValue(field, control);
            } else if (Array.isArray(value)) {
                control.value = value.join('\n');
            } else {
                control.value = String(value);
            }
        } else if (control.tagName === 'INPUT') {
            control.value = String(value);
        }
    });
    updateDependentFieldsVisibility();
    persistLiveModelValues();
    emitModelSettingsStateChanged();
}

async function loadPresetsForModel(modelId) {
    modelSettingsState.presets = [];
    modelSettingsState.selectedPresetId = null;

    if (!modelId) {
        renderPresetsDropdown();
        return;
    }

    const presets = await fetchPresetsList(modelId);
    modelSettingsState.presets = Array.isArray(presets) ? presets : [];
    renderPresetsDropdown();
}

// Overlay functions
let presetSavePreviousFocus = null;

function openSavePresetOverlay() {
    const overlay = document.getElementById('presetSaveOverlay');
    const input = document.getElementById('presetNameInput');
    if (overlay) {
        presetSavePreviousFocus = document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;
        overlay.removeAttribute('hidden');
        overlay.setAttribute('aria-hidden', 'false');
        overlay.classList.add('open');
        document.body.classList.add('modal-open');
        if (input) {
            input.value = '';
            requestAnimationFrame(() => input.focus({ preventScroll: true }));
        }
    }
}

function closeSavePresetOverlay() {
    const overlay = document.getElementById('presetSaveOverlay');
    if (overlay) {
        overlay.classList.remove('open');
        overlay.setAttribute('aria-hidden', 'true');
        overlay.setAttribute('hidden', '');
        document.body.classList.remove('modal-open');
        const previousFocus = presetSavePreviousFocus;
        presetSavePreviousFocus = null;
        if (previousFocus?.isConnected) {
            previousFocus.focus({ preventScroll: true });
        }
    }
}

function openDeletePresetOverlay(presetId, presetName) {
    modelSettingsState.pendingDeletePresetId = presetId;
    modelSettingsState.pendingDeletePresetName = presetName;
    
    const overlay = document.getElementById('presetDeleteOverlay');
    const message = document.getElementById('presetDeleteMessage');
    if (overlay) {
        if (message) {
            message.textContent = translateModelSettingsText(
                'model_settings_delete_preset_confirm',
                'Are you sure you want to delete the preset "{presetName}"?'
            ).replace('{presetName}', presetName);
        }
        overlay.removeAttribute('hidden');
    }
}

function closeDeletePresetOverlay() {
    const overlay = document.getElementById('presetDeleteOverlay');
    if (overlay) {
        overlay.setAttribute('hidden', '');
    }
    modelSettingsState.pendingDeletePresetId = null;
    modelSettingsState.pendingDeletePresetName = null;
}

async function confirmSavePreset() {
    const input = document.getElementById('presetNameInput');
    const name = input?.value?.trim();
    
    if (!name) {
        input?.focus();
        return;
    }

    const modelId = modelSettingsState.activeModelId;
    if (!modelId) {
        closeSavePresetOverlay();
        return;
    }

    const settings = getCurrentModelSettingValues();
    const result = await savePreset(modelId, name, settings);
    
    if (result) {
        // Reload presets and re-render
        await loadPresetsForModel(modelId);
    }

    closeSavePresetOverlay();
}

async function confirmDeletePreset() {
    const presetId = modelSettingsState.pendingDeletePresetId;
    const modelId = modelSettingsState.activeModelId;

    if (!presetId || !modelId) {
        closeDeletePresetOverlay();
        return;
    }

    const success = await deletePreset(modelId, presetId);
    
    if (success) {
        // If we deleted the currently selected preset, reset to None
        if (modelSettingsState.selectedPresetId === presetId) {
            modelSettingsState.selectedPresetId = null;
        }
        // Reload presets
        await loadPresetsForModel(modelId);
    }

    closeDeletePresetOverlay();
}

function initPresetEventListeners() {
    // Dropdown toggle
    const dropdownTrigger = document.getElementById('presetsDropdownTrigger');
    if (dropdownTrigger) {
        // Set the dropdown chevron using Icons.chevron if available
        const oldCaret = dropdownTrigger.querySelector('.presets-dropdown-caret');
        if (oldCaret && typeof Icons !== 'undefined' && Icons.chevron) {
            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = Icons.chevron.trim();
            const svgEl = tempDiv.querySelector('svg');
            if (svgEl) {
                svgEl.setAttribute('class', 'presets-dropdown-caret');
                svgEl.setAttribute('width', '12');
                svgEl.setAttribute('height', '12');
                oldCaret.replaceWith(svgEl);
            }
        }
        dropdownTrigger.addEventListener('click', (e) => {
            e.stopPropagation();
            togglePresetsDropdown();
        });
    }

    // Close dropdown on outside click
    document.addEventListener('click', (e) => {
        const dropdown = document.getElementById('presetsDropdown');
        if (dropdown && !dropdown.contains(e.target)) {
            closePresetsDropdown();
        }
    });

    // Save button
    const saveBtn = document.getElementById('presetsSaveBtn');
    if (saveBtn) {
        saveBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            openSavePresetOverlay();
        });
    }

    // Save overlay buttons
    const saveCancelBtn = document.getElementById('presetSaveCancelBtn');
    if (saveCancelBtn) {
        saveCancelBtn.addEventListener('click', closeSavePresetOverlay);
    }
    
    const saveConfirmBtn = document.getElementById('presetSaveConfirmBtn');
    if (saveConfirmBtn) {
        saveConfirmBtn.addEventListener('click', confirmSavePreset);
    }

    // Enter key in preset name input
    const presetNameInput = document.getElementById('presetNameInput');
    if (presetNameInput) {
        presetNameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                confirmSavePreset();
            } else if (e.key === 'Escape') {
                closeSavePresetOverlay();
            }
        });
    }

    // Delete overlay buttons
    const deleteCancelBtn = document.getElementById('presetDeleteCancelBtn');
    if (deleteCancelBtn) {
        deleteCancelBtn.addEventListener('click', closeDeletePresetOverlay);
    }

    const deleteConfirmBtn = document.getElementById('presetDeleteConfirmBtn');
    if (deleteConfirmBtn) {
        deleteConfirmBtn.addEventListener('click', confirmDeletePreset);
    }

    // Close overlays on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeSavePresetOverlay();
            closeDeletePresetOverlay();
        }
    });

    // Close overlays on backdrop click
    const saveOverlay = document.getElementById('presetSaveOverlay');
    if (saveOverlay) {
        saveOverlay.addEventListener('click', (e) => {
            if (e.target === saveOverlay) {
                closeSavePresetOverlay();
            }
        });
        saveOverlay.addEventListener('keydown', (e) => {
            if (e.key !== 'Tab' || !saveOverlay.classList.contains('open')) return;
            const focusable = Array.from(saveOverlay.querySelectorAll(
                'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
            )).filter((element) => !element.hidden && element.getClientRects().length > 0);
            if (!focusable.length) {
                e.preventDefault();
                saveOverlay.querySelector('[role="dialog"]')?.focus({ preventScroll: true });
                return;
            }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        });
    }

    const deleteOverlay = document.getElementById('presetDeleteOverlay');
    if (deleteOverlay) {
        deleteOverlay.addEventListener('click', (e) => {
            if (e.target === deleteOverlay) {
                closeDeletePresetOverlay();
            }
        });
    }
}

function handleModelChange(event) {
    const fallbackQuickThinkingState = getQuickThinkingControlState();
    const nextModelId = event?.detail?.modelId || getActiveModelId();
    loadModelSettingsFor(nextModelId, {
        preferredQuickThinkingValue: fallbackQuickThinkingState?.currentValue,
        fallbackQuickThinkingState,
    });
}

/**
 * Reload the schema for the model currently selected in chat.
 *
 * The backend calculates connection and MCP availability while building this
 * schema. `loadModelSettingsFor` snapshots existing control values first, so
 * this refresh retains unrelated, unsaved model-setting changes.
 */
async function refreshActiveModelSettings() {
    const activeModelId = getActiveModelId();
    if (!activeModelId) {
        return false;
    }
    await loadModelSettingsFor(activeModelId, { force: true });
    return true;
}

function initializeModelSettings(modelId) {
    if (!modelSettingsState.initialized) {
        modelSettingsState.initialized = true;
        window.addEventListener('modelSelect:changed', handleModelChange);
        // Initialize preset event listeners
        initPresetEventListeners();
    }
    const targetModelId = modelId || getActiveModelId();
    if (!targetModelId) {
        return;
    }
    loadModelSettingsFor(targetModelId);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initializeModelSettings());
} else {
    initializeModelSettings();
}

window.addEventListener('byok:modelsChanged', () => {
    modelSettingsState.modelLookup = null;
});

// Connection settings dispatch this event after a successful availability
// change. This also refreshes a closed sidebar, ready for its next opening.
window.addEventListener('modelSettings:refreshRequested', () => {
    void refreshActiveModelSettings().catch((error) => {
        console.error('Model settings: failed to refresh active model schema', error);
    });
});

window.getCurrentModelSettingValues = getCurrentModelSettingValues;
window.validateCurrentModelSettings = validateCurrentModelSettings;
window.getCurrentModelSettingsSummary = getCurrentModelSettingsSummary;
window.renderModelSettingsSchema = renderModelSettingsSchema;
window.loadModelSettingsFor = loadModelSettingsFor;
window.refreshActiveModelSettings = refreshActiveModelSettings;
window.getActiveModelId = getActiveModelId;
window.initializeModelSettings = initializeModelSettings;
window.getQuickThinkingControlState = getQuickThinkingControlState;
window.getQuickThinkingControlStateFromSchema = getQuickThinkingControlStateFromSchema;
window.translateModelSettingsSchema = translateModelSettingsSchema;
window.getLoadedModelSettingsModelId = getLoadedModelSettingsModelId;
window.getLoadedModelSettingsProjectId = getLoadedModelSettingsProjectId;
window.reloadModelSettingsIfNeeded = reloadModelSettingsIfNeeded;
window.applyQuickThinkingControlValue = applyQuickThinkingControlValue;
window.applyQuickThinkingValueToSettings = applyQuickThinkingValueToSettings;
window.applyModelSettingValues = applyModelSettingValues;
window.fetchModelSettingsSchemaForModel = fetchModelSettingsSchemaForModel;
window.expandSupportedFileFormatsFromSchemaPayload = expandSupportedFileFormatsFromSchemaPayload;
window.parseStructuredMappingValue = parseStructuredMappingValue;
window.setMcpServerEnabledForCurrentRequest = setMcpServerEnabledForCurrentRequest;
window.clearMcpServersForNextRequest = clearMcpServersForNextRequest;
