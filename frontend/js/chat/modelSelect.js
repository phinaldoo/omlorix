// Local state
let msModels = [];
let msAdminModels = [];
let msByokModels = [];
let msFilteredModels = [];
let msSelectedModelId = null;
let msSearchActive = false;
let msFilterInput = null;
let msFilterOutput = null;
let msInitDone = false;
let msHighlightedModelId = null;
let msHighlightedModelIndex = -1;
let msRemotePinnedModelIds = [];
let msLocalPinnedModelIds = [];
let msPinnedModelOrder = [];
const MODEL_PIN_LIMIT = 8;
const MODEL_PINNED_ORDER_STORAGE_KEY = 'omlorix_pinned_model_order_v1';
const MODEL_LOCAL_PINNED_STORAGE_KEY = 'omlorix_local_pinned_models_v1';
const msContext = {
    mode: 'main',
    side: null,
    anchorEl: null,
    selectedModelId: null,
    onSelect: null,
    onClose: null,
};

// Mobile bottom sheet state
const msMobile = {
    mobileBreakpoint: 768,
    isDragging: false,
    dragStartY: 0,
    dragCurrentY: 0,
    dragThreshold: 100,
    dragLastY: 0,
    dragLastTime: 0,
    dragVelocity: 0,
    velocityCloseThreshold: 0.65,
    isOpen: false,
    pointerId: null,
    didDragMove: false,
    skipHandleClick: false,
    boundWithPointerEvents: false,
    
    isMobile() {
        return window.innerWidth <= msMobile.mobileBreakpoint;
    },
    
    supportsPointerEvents() {
        return typeof window !== 'undefined' && typeof window.PointerEvent === 'function';
    },
    
    getBackdrop() {
        return document.getElementById('modelSelectBackdrop');
    },
    
    getDragHandle() {
        return document.getElementById('modelSelectDragHandle');
    },
    
    getDropdown() {
        return document.getElementById('modelSelectDropdown');
    },
    
    getCloseButton() {
        return document.getElementById('modelSelectMobileClose');
    },
    
    getMainPanel() {
        return document.getElementById('modelSelectMainPanel');
    }
};

function resolveModelIcon(iconValue) {
    const fallback = (typeof Icons === 'object' && Icons?.omlorix) ? Icons.omlorix : '';
    if (window.IconPicker?.renderIconMarkup) {
        return window.IconPicker.renderIconMarkup(iconValue, {
            fallback,
            imageAlt: translate('model_select_icon_alt', 'Model icon'),
        });
    }
    if (typeof iconValue !== 'string') {
        return fallback;
    }
    const trimmed = iconValue.trim();
    if (!trimmed) {
        return fallback;
    }
    if (trimmed.startsWith('<')) {
        if (window.ChatSanitizer?.sanitizeSvg) {
            return window.ChatSanitizer.sanitizeSvg(trimmed) || fallback;
        }
        if (window.DOMPurify?.sanitize) {
            return window.DOMPurify.sanitize(trimmed, {
                USE_PROFILES: { svg: true },
                FORBID_ATTR: ['style', 'srcdoc'],
                ALLOW_DATA_ATTR: false,
            }) || fallback;
        }
        return fallback;
    }
    const mapped = Icons?.[trimmed];
    if (typeof mapped === 'string' && mapped.trim()) {
        return mapped;
    }
    return fallback;
}

function applyModelIcon(container, iconValue) {
    if (!container) {
        return;
    }
    container.innerHTML = resolveModelIcon(iconValue);
}

/**
 * Render the icon and name shared by every model-select trigger.
 *
 * The main selector and split-panel selectors deliberately use the same DOM
 * contract so icon sanitization, sizing, truncation, and future visual changes
 * cannot drift between chat modes.
 *
 * @param {HTMLElement} toggle - The om-button model-select trigger to update.
 * @param {object} model - Model data containing name and model_icon values.
 * @returns {{icon: HTMLElement, name: HTMLElement}|null} Rendered elements.
 */
function renderModelSelectTriggerContent(toggle, model = {}) {
    if (!toggle) return null;

    toggle.querySelectorAll(':scope > .label-icon, :scope > .label-name').forEach((child) => child.remove());
    const icon = document.createElement('span');
    icon.className = 'label-icon';
    applyModelIcon(icon, model.model_icon);
    icon.style.display = 'inline-flex';
    const svg = icon.querySelector('svg');
    if (svg) {
        svg.setAttribute('width', '16');
        svg.setAttribute('height', '16');
        svg.style.width = '16px';
        svg.style.height = '16px';
        svg.style.display = 'block';
    }

    const name = document.createElement('span');
    name.className = 'label-name';
    name.textContent = model.name || '';
    const insertionPoint = toggle.querySelector(':scope > .model-select-trigger-skeleton')
        || toggle.querySelector(':scope > .model-select-trigger-status')
        || toggle.querySelector(':scope > .chevron');
    if (insertionPoint) {
        toggle.insertBefore(icon, insertionPoint);
        toggle.insertBefore(name, insertionPoint);
    } else {
        toggle.append(icon, name);
    }
    return { icon, name };
}

function isMobileDevice() {
    if (typeof window === 'undefined') return false;
    if (typeof navigator !== 'undefined') {
        const ua = navigator.userAgent || '';
        if (/Mobi|Android|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(ua)) {
            return true;
        }
    }
    return typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches;
}

let msRafReposition = null;

const MODEL_SELECT_WARNING_TOOLTIP_ORIGIN = 'model-select-warning';
let msWarningTooltipId = 0;

function nextModelSelectWarningTooltipId() {
    msWarningTooltipId += 1;
    return `modelSelectWarningTooltip${msWarningTooltipId}`;
}

function cleanupModelSelectWarningTooltips() {
    if (typeof document === 'undefined') return;
    document
        .querySelectorAll(`.tooltip[data-tooltip-origin="${MODEL_SELECT_WARNING_TOOLTIP_ORIGIN}"]`)
        .forEach((el) => el.remove());
}


const MODEL_SELECT_OUTPUT_TOOL_FORMATS = {
    image_generation: 'image',
    video_generation: 'video',
    audio_generation: 'audio',
};

const MODEL_SELECT_FORMAT_META = {
    text: {
        key: 'model_select_format_text',
        fallback: 'Text',
        icon: Icons.textLines,
    },
    image: {
        key: 'model_select_format_image',
        fallback: 'Image',
        icon: Icons.image_gen,
    },
    audio: {
        key: 'model_select_format_audio',
        fallback: 'Audio',
        icon: Icons.audio_gen,
    },
    video: {
        key: 'model_select_format_video',
        fallback: 'Video',
        icon: Icons.video_gen,
    },
    pdf: {
        key: 'model_select_format_pdf',
        fallback: 'PDF',
        icon: Icons.file,
    },
    text_document: {
        key: 'model_select_format_document',
        fallback: 'Document',
        icon: Icons.file,
    },
};

const MODEL_SELECT_TOOL_META = {
    subagent: {
        key: 'model_select_tool_subagent',
        fallback: 'Subagent',
        icon: Icons.model_tool_subagent,
    },
    web_search: {
        key: 'model_select_tool_web_search',
        fallback: 'Web search',
        icon: Icons.globe,
    },
    weather: {
        key: 'model_select_tool_weather',
        fallback: 'Weather',
        icon: Icons.model_tool_weather,
    },
    flashcards: {
        key: 'model_select_tool_flashcards',
        fallback: 'Flashcards',
        icon: Icons.model_tool_flashcards,
    },
    quiz: {
        key: 'model_select_tool_quiz',
        fallback: 'Quiz',
        icon: Icons.model_tool_quiz,
    },
    todos: {
        key: 'model_select_tool_todos',
        fallback: 'Todos',
        icon: Icons.checklist,
    },
    notes: {
        key: 'model_select_tool_notes',
        fallback: 'Notes',
        icon: Icons.model_tool_notes,
    },
    automations: {
        key: 'model_select_tool_automations',
        fallback: 'Automations',
        icon: Icons.clock,
    },
    skills: {
        key: 'model_select_tool_skills',
        fallback: 'Skills',
        icon: Icons.star,
    },
    memories: {
        key: 'model_select_tool_memories',
        fallback: 'Memories',
        icon: Icons.model_tool_memories,
    },
    music_generation: {
        key: 'model_select_tool_music_generation',
        fallback: 'Music generation',
        icon: Icons.music,
    },
    canvas: {
        key: 'model_select_tool_canvas',
        fallback: 'Canvas',
        icon: Icons.file,
    },
    slide_presentation: {
        key: 'model_select_tool_slide_presentation',
        fallback: 'Slide presentation',
        icon: Icons.desktop,
    },
    code_execution: {
        key: 'model_select_tool_code_execution',
        fallback: 'Code execution',
        icon: Icons.code,
    },
    deep_research: {
        key: 'model_select_tool_deep_research',
        fallback: 'Deep research',
        icon: Icons.globe,
    },
};

// Managed connection provider keys are public catalog identifiers returned by
// the model-list endpoint.  Resolve their artwork locally so the backend never
// needs to send arbitrary markup as part of a model summary.
const MODEL_SELECT_CONNECTION_ICON_KEYS = {
    github: 'github',
    notion: 'notion',
    slack: 'slack',
    gmail: 'gmail',
    google_calendar: 'google_calendar',
};

function translate(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function interpolateTranslation(template, values = {}) {
    return String(template || '').replace(/\{([a-zA-Z0-9_]+)\}/g, (match, key) => (
        Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match
    ));
}

function getProviderRecipientDisclosure(model) {
    if (!model || !model.is_provider_group) return '';
    const names = Array.isArray(model.provider_recipients)
        ? Array.from(new Set(model.provider_recipients
            .map((recipient) => formatFallbackLabel(recipient?.provider || ''))
            .filter(Boolean)))
        : [];
    if (!names.length) return '';
    const recipients = names.join(', ');
    return interpolateTranslation(
        translate('model_select_provider_group_recipients', 'May send to: {recipients}'),
        { recipients }
    );
}

function msEscapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function normalizeStringList(value) {
    if (value == null) return [];
    const source = Array.isArray(value)
        ? value
        : (typeof value === 'object' && !(value instanceof String) ? Object.keys(value) : [value]);
    const seen = new Set();
    const result = [];
    source.forEach((item) => {
        const text = String(item ?? '').trim();
        if (!text || seen.has(text)) return;
        seen.add(text);
        result.push(text);
    });
    return result;
}

function interpolateTranslation(template, values = {}) {
    return String(template || '').replace(/\{([a-zA-Z0-9_]+)\}/g, (match, key) => (
        Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match
    ));
}

function getProviderRecipientDisclosure(model) {
    if (!model || !model.is_provider_group) return '';
    const names = Array.isArray(model.provider_recipients)
        ? Array.from(new Set(model.provider_recipients
            .map((recipient) => formatFallbackLabel(recipient?.provider || ''))
            .filter(Boolean)))
        : [];
    if (!names.length) return '';
    const recipients = names.join(', ');
    return interpolateTranslation(
        translate('model_select_provider_group_recipients', 'May send to: {recipients}'),
        { recipients }
    );
}

function getModelInputFormats(model) {
    const settings = model?.settings && typeof model.settings === 'object' ? model.settings : {};
    return normalizeStringList(model?.input_formats).length
        ? normalizeStringList(model.input_formats)
        : (normalizeStringList(settings.input_formats).length ? normalizeStringList(settings.input_formats) : ['text']);
}

function getModelOutputFormats(model) {
    const settings = model?.settings && typeof model.settings === 'object' ? model.settings : {};
    const outputFormats = normalizeStringList(model?.output_formats).length
        ? normalizeStringList(model.output_formats)
        : (normalizeStringList(settings.output_formats).length ? normalizeStringList(settings.output_formats) : ['text']);
    const seen = new Set(outputFormats);
    getModelRawTools(model).forEach((toolName) => {
        const outputFormat = MODEL_SELECT_OUTPUT_TOOL_FORMATS[toolName];
        if (outputFormat && !seen.has(outputFormat)) {
            seen.add(outputFormat);
            outputFormats.push(outputFormat);
        }
    });
    return outputFormats;
}

function getModelRawTools(model) {
    return normalizeStringList(model?.tools);
}

/**
 * Return whether a tool is supplied by an MCP server.
 *
 * MCP tools are runtime integrations rather than useful model capabilities, so
 * the model picker intentionally keeps both the aggregate marker and generated
 * public tool names out of its compact icon preview.
 */
function isModelSelectMcpTool(toolName) {
    const key = String(toolName || '').trim().toLowerCase();
    return key === 'mcp' || key.startsWith('mcp_');
}

function getModelSelectTools(model) {
    const explicit = normalizeStringList(model?.model_select_tools);
    const tools = explicit.length ? explicit : getModelRawTools(model);
    return tools.filter((toolName) => (
        !(toolName in MODEL_SELECT_OUTPUT_TOOL_FORMATS)
        && !isModelSelectMcpTool(toolName)
    ));
}

function formatFallbackLabel(value) {
    return String(value || '')
        .split(/[^a-zA-Z0-9]+/)
        .filter(Boolean)
        .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
        .join(' ');
}

function getFormatMeta(format) {
    const key = String(format || '').trim();
    const meta = MODEL_SELECT_FORMAT_META[key];
    if (meta) {
        return {
            icon: meta.icon,
            label: translate(meta.key, meta.fallback),
        };
    }
    return {
        icon: MODEL_SELECT_FORMAT_META.text.icon,
        label: formatFallbackLabel(key) || key,
    };
}

function getToolMeta(toolName) {
    const key = String(toolName || '').trim();
    const meta = MODEL_SELECT_TOOL_META[key];
    if (meta) {
        return {
            icon: meta.icon,
            label: translate(meta.key, meta.fallback),
        };
    }
    return {
        icon: Icons.omlorix,
        label: formatFallbackLabel(key) || key,
    };
}

function getStatusText(status) {
    if (status === 'alpha') return translate('model_select_status_alpha', 'Alpha');
    if (status === 'experimental') return translate('model_select_status_experimental', 'Experimental');
    if (status === 'beta') return translate('model_select_status_beta', 'Beta');
    return '';
}

function createStatusBadge(status) {
    const text = getStatusText(status);
    if (!text) return null;
    const badge = document.createElement('span');
    badge.className = `model-select-item-status ${status}`;
    badge.textContent = text;
    return badge;
}

function formatPillHtml(format) {
    const meta = getFormatMeta(format);
    return `<span class="model-select-format-pill">${meta.icon}<span>${msEscapeHtml(meta.label)}</span></span>`;
}

function toolChipHtml(toolName) {
    const meta = getToolMeta(toolName);
    return `<span class="model-select-icon-chip" title="${msEscapeHtml(meta.label)}" aria-label="${msEscapeHtml(meta.label)}">${meta.icon}</span>`;
}

function getModelSelectConnections(model) {
    const rawConnections = Array.isArray(model?.model_select_connections)
        ? model.model_select_connections
        : [];
    const seenProviders = new Set();
    const connections = [];

    rawConnections.forEach((item) => {
        if (!item || typeof item !== 'object') return;
        const provider = String(item.provider || '').trim().toLowerCase();
        const title = String(item.title || '').trim();
        if (!provider || !title || seenProviders.has(provider)) return;
        seenProviders.add(provider);
        connections.push({ provider, title });
    });
    return connections;
}

function connectionPillHtml(connection) {
    const iconKey = MODEL_SELECT_CONNECTION_ICON_KEYS[connection.provider];
    const icon = (iconKey && Icons?.[iconKey]) || Icons?.connections || '';
    return `
        <span class="model-select-format-pill model-select-connection-pill">
            <span aria-hidden="true">${icon}</span>
            <span>${msEscapeHtml(connection.title)}</span>
        </span>
    `;
}

/**
 * Render only the connection providers already authorized by both the model
 * and the current user's group. The backend computes that intersection so the
 * browser never receives hidden model MCP settings.
 */
function modelSelectConnectionsDetailSectionHtml(model) {
    const connections = getModelSelectConnections(model);
    if (!connections.length) {
        return '';
    }

    return `
        <div class="model-select-detail-section model-select-connections-section">
            <span class="model-select-detail-section-label">${msEscapeHtml(translate('model_select_detail_connections', 'Connections'))}</span>
            <div class="model-select-format-row">${connections.map(connectionPillHtml).join('')}</div>
        </div>
    `;
}

/**
 * Build the optional tools portion of the desktop model detail card.
 *
 * The normalized picker tool list deliberately excludes generation formats
 * and MCP runtime integrations. If nothing remains, omit the whole section so
 * the card only describes capabilities the model actually advertises.
 */
function modelSelectToolsDetailSectionHtml(model) {
    const tools = getModelSelectTools(model);
    if (!tools.length) {
        return '';
    }

    return `
        <div class="model-select-detail-section">
            <span class="model-select-detail-section-label">${msEscapeHtml(translate('model_select_detail_tools', 'Tools'))}</span>
            <div class="model-select-icon-row">${tools.map(toolChipHtml).join('')}</div>
        </div>
    `;
}

function modelMatchesActiveFormatFilters(model) {
    if (msFilterInput && !getModelInputFormats(model).includes(msFilterInput)) {
        return false;
    }
    if (msFilterOutput && !getModelOutputFormats(model).includes(msFilterOutput)) {
        return false;
    }
    return true;
}

function getSelectedModelId() {
    return msSelectedModelId;
}

function getSelectedModel() {
    if (!msSelectedModelId) return null;
    return msModels.find(m => m.model_id === msSelectedModelId) || null;
}

function normalizeModelId(value) {
    const normalized = String(value ?? '').trim();
    return normalized || null;
}

function normalizeModelIdList(values, { limit = MODEL_PIN_LIMIT } = {}) {
    if (!Array.isArray(values)) return [];
    const normalized = [];
    const seen = new Set();
    for (const value of values) {
        const modelId = normalizeModelId(value);
        if (!modelId || seen.has(modelId)) {
            continue;
        }
        seen.add(modelId);
        normalized.push(modelId);
        if (normalized.length >= limit) {
            break;
        }
    }
    return normalized;
}

function stringArraysEqual(a, b) {
    if (a === b) return true;
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) {
        return false;
    }
    for (let i = 0; i < a.length; i += 1) {
        if (String(a[i]) !== String(b[i])) {
            return false;
        }
    }
    return true;
}

function getLocalStorage() {
    try {
        return typeof window === 'undefined' ? null : window.localStorage;
    } catch (_) {
        return null;
    }
}

function safeReadLocalStorageJson(key, fallback = []) {
    const storage = getLocalStorage();
    if (!storage) {
        return fallback;
    }
    try {
        const raw = storage.getItem(key);
        if (!raw) {
            return fallback;
        }
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : fallback;
    } catch (_) {
        return fallback;
    }
}

function safeWriteLocalStorageJson(key, value) {
    const storage = getLocalStorage();
    if (!storage) {
        return;
    }
    try {
        if (!Array.isArray(value) || value.length === 0) {
            storage.removeItem(key);
            return;
        }
        storage.setItem(key, JSON.stringify(value));
    } catch (_) {
        // Ignore storage failures; pinning still works for the session.
    }
}

function getStoredPinnedModelOrder() {
    return normalizeModelIdList(
        safeReadLocalStorageJson(MODEL_PINNED_ORDER_STORAGE_KEY, []),
        { limit: MODEL_PIN_LIMIT }
    );
}

function setStoredPinnedModelOrder(modelIds) {
    safeWriteLocalStorageJson(
        MODEL_PINNED_ORDER_STORAGE_KEY,
        normalizeModelIdList(modelIds, { limit: MODEL_PIN_LIMIT })
    );
}

function getStoredLocalPinnedModelIds() {
    return normalizeModelIdList(
        safeReadLocalStorageJson(MODEL_LOCAL_PINNED_STORAGE_KEY, []),
        { limit: MODEL_PIN_LIMIT }
    );
}

function setStoredLocalPinnedModelIds(modelIds) {
    safeWriteLocalStorageJson(
        MODEL_LOCAL_PINNED_STORAGE_KEY,
        normalizeModelIdList(modelIds, { limit: MODEL_PIN_LIMIT })
    );
}

function isByokPinnedModelId(modelId) {
    return !!(
        modelId
        && typeof window.BYOK?.isByokModelId === 'function'
        && window.BYOK.isByokModelId(modelId)
    );
}

function getAvailableModelIds() {
    return new Set(
        msModels
            .map((model) => normalizeModelId(model?.model_id))
            .filter(Boolean)
    );
}

function applyPinnedModelState({ remoteIds = [], localIds = [], orderIds = [] } = {}) {
    const availableIds = getAvailableModelIds();
    const nextRemoteIds = normalizeModelIdList(remoteIds, { limit: MODEL_PIN_LIMIT })
        .filter((modelId) => availableIds.has(modelId) && !isByokPinnedModelId(modelId));
    const nextLocalIds = normalizeModelIdList(localIds, { limit: MODEL_PIN_LIMIT })
        .filter((modelId) => availableIds.has(modelId) && isByokPinnedModelId(modelId));

    const allowedPinnedIds = new Set([...nextRemoteIds, ...nextLocalIds]);
    const mergedOrder = normalizeModelIdList(orderIds, { limit: MODEL_PIN_LIMIT })
        .filter((modelId) => allowedPinnedIds.has(modelId));

    nextRemoteIds.forEach((modelId) => {
        if (!mergedOrder.includes(modelId)) {
            mergedOrder.push(modelId);
        }
    });
    nextLocalIds.forEach((modelId) => {
        if (!mergedOrder.includes(modelId)) {
            mergedOrder.push(modelId);
        }
    });

    msPinnedModelOrder = mergedOrder.slice(0, MODEL_PIN_LIMIT);
    const activePinnedIds = new Set(msPinnedModelOrder);
    msRemotePinnedModelIds = nextRemoteIds.filter((modelId) => activePinnedIds.has(modelId));
    msLocalPinnedModelIds = nextLocalIds.filter((modelId) => activePinnedIds.has(modelId));

    return {
        remoteIds: [...msRemotePinnedModelIds],
        localIds: [...msLocalPinnedModelIds],
        orderIds: [...msPinnedModelOrder],
    };
}

function syncPinnedModelsFromSources() {
    const remoteIds = normalizeModelIdList(window?.chatSetup?.pinned_models || [], { limit: MODEL_PIN_LIMIT });
    const localIds = getStoredLocalPinnedModelIds();
    const orderIds = getStoredPinnedModelOrder();
    const nextState = applyPinnedModelState({ remoteIds, localIds, orderIds });
    setStoredLocalPinnedModelIds(nextState.localIds);
    setStoredPinnedModelOrder(nextState.orderIds);
    return nextState;
}

function getPinnedModelIds() {
    return normalizeModelIdList(msPinnedModelOrder, { limit: MODEL_PIN_LIMIT });
}

function isModelPinned(modelId) {
    const normalizedModelId = normalizeModelId(modelId);
    return normalizedModelId ? getPinnedModelIds().includes(normalizedModelId) : false;
}

function getPinnedModels() {
    const modelsById = new Map(
        msModels
            .map((model) => [normalizeModelId(model?.model_id), model])
            .filter(([modelId, model]) => modelId && model)
    );
    return getPinnedModelIds()
        .map((modelId) => modelsById.get(modelId))
        .filter(Boolean);
}

function sortModelsByPinPriority(models) {
    const pinnedOrder = new Map(getPinnedModelIds().map((modelId, index) => [modelId, index]));
    const sourceOrder = new Map(
        msModels
            .map((model, index) => [normalizeModelId(model?.model_id), index])
            .filter(([modelId]) => modelId)
    );
    return [...models].sort((a, b) => {
        const aId = normalizeModelId(a?.model_id);
        const bId = normalizeModelId(b?.model_id);
        const aPinned = aId ? pinnedOrder.has(aId) : false;
        const bPinned = bId ? pinnedOrder.has(bId) : false;
        if (aPinned && bPinned) {
            return pinnedOrder.get(aId) - pinnedOrder.get(bId);
        }
        if (aPinned !== bPinned) {
            return aPinned ? -1 : 1;
        }
        return (sourceOrder.get(aId) ?? 0) - (sourceOrder.get(bId) ?? 0);
    });
}

function renderPinnedModelsSidebar() {
    const pinnedModels = getPinnedModels();
    if (typeof window.ChatSidebarMid?.renderPinnedModels === 'function') {
        const unpinLabel = translate('model_select_unpin_model', 'Unpin model');
        window.ChatSidebarMid.renderPinnedModels(pinnedModels, {
            applyModelIcon,
            onSelect: selectModel,
            onUnpin: async (model) => togglePinnedModel(model.model_id),
            unpinIcon: Icons.unpin,
            unpinLabel
        });
        return;
    }

    const section = document.getElementById('sidebarPinnedModels');
    const list = document.getElementById('sidebarPinnedModelsList');
    if (!section || !list) {
        return;
    }

    section.hidden = pinnedModels.length === 0;
    list.innerHTML = '';

    pinnedModels.forEach((model) => {
        const row = document.createElement('div');
        row.className = 'sidebar-quick-model';

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'sidebar-quick-model-button';
        button.dataset.modelId = model.model_id;

        const iconWrap = document.createElement('span');
        iconWrap.className = 'sidebar-quick-model-icon';
        applyModelIcon(iconWrap, model.model_icon);

        const label = document.createElement('span');
        label.className = 'sidebar-quick-model-label';
        label.textContent = model.name || translate('model_select_unnamed_model', 'Unnamed model');

        button.appendChild(iconWrap);
        button.appendChild(label);
        button.addEventListener('click', async () => {
            await selectModel(model);
        });

        const unpinButton = document.createElement('button');
        unpinButton.type = 'button';
        unpinButton.className = 'sidebar-quick-model-unpin';
        unpinButton.innerHTML = Icons.unpin;
        unpinButton.setAttribute('aria-label', translate('model_select_unpin_model', 'Unpin model'));
        unpinButton.setAttribute('title', translate('model_select_unpin_model', 'Unpin model'));
        unpinButton.addEventListener('click', async (event) => {
            event.preventDefault();
            event.stopPropagation();
            await togglePinnedModel(model.model_id);
        });

        row.appendChild(button);
        row.appendChild(unpinButton);
        list.appendChild(row);
    });
}

async function refreshPinnedModelsUi({ rerenderDropdown = true } = {}) {
    renderPinnedModelsSidebar();
    if (rerenderDropdown) {
        await ModelSelectRenderMainList();
    }
}

async function persistRemotePinnedModels(remoteIds) {
    const res = await window.authedFetch('/api/v1/users/settings/pinned-models/set', {
        method: 'POST',
        body: JSON.stringify({
            pinned_models: normalizeModelIdList(remoteIds, { limit: MODEL_PIN_LIMIT }),
        }),
    });

    if (res.status === 401) {
        if (typeof redirectToLogin === 'function') {
            redirectToLogin();
        }
        throw new Error(translate('model_select_unauthorized', 'Unauthorized'));
    }

    const payload = await res.json().catch(() => null);
    if (!res.ok) {
        throw new Error(
            payload?.detail
            || payload?.message
            || `Failed to update pinned models (${res.status})`
        );
    }

    return normalizeModelIdList(payload?.pinned_models || remoteIds, { limit: MODEL_PIN_LIMIT });
}

async function setPinnedModelIds(nextPinnedIds) {
    const availableIds = getAvailableModelIds();
    const normalizedPinnedIds = normalizeModelIdList(nextPinnedIds, { limit: MODEL_PIN_LIMIT })
        .filter((modelId) => availableIds.has(modelId));
    const nextRemoteIds = normalizedPinnedIds.filter((modelId) => !isByokPinnedModelId(modelId));
    const nextLocalIds = normalizedPinnedIds.filter((modelId) => isByokPinnedModelId(modelId));

    const previousState = {
        remoteIds: [...msRemotePinnedModelIds],
        localIds: [...msLocalPinnedModelIds],
        orderIds: [...msPinnedModelOrder],
    };

    applyPinnedModelState({
        remoteIds: nextRemoteIds,
        localIds: nextLocalIds,
        orderIds: normalizedPinnedIds,
    });
    setStoredLocalPinnedModelIds(msLocalPinnedModelIds);
    setStoredPinnedModelOrder(msPinnedModelOrder);
    await refreshPinnedModelsUi();

    try {
        const remoteChanged = !stringArraysEqual(previousState.remoteIds, nextRemoteIds);
        const resolvedRemoteIds = remoteChanged
            ? await persistRemotePinnedModels(nextRemoteIds)
            : nextRemoteIds;

        applyPinnedModelState({
            remoteIds: resolvedRemoteIds,
            localIds: nextLocalIds,
            orderIds: normalizedPinnedIds,
        });
        setStoredLocalPinnedModelIds(msLocalPinnedModelIds);
        setStoredPinnedModelOrder(msPinnedModelOrder);
        if (window.chatSetup && typeof window.chatSetup === 'object') {
            window.chatSetup.pinned_models = [...msRemotePinnedModelIds];
        }
        await refreshPinnedModelsUi();
        return true;
    } catch (error) {
        console.error('Failed to update pinned models', error);
        applyPinnedModelState(previousState);
        setStoredLocalPinnedModelIds(msLocalPinnedModelIds);
        setStoredPinnedModelOrder(msPinnedModelOrder);
        await refreshPinnedModelsUi();
        if (typeof window.notifyError === 'function') {
            window.notifyError(
                error?.message
                || translate('model_select_pin_update_failed', 'Failed to update pinned models.')
            );
        }
        return false;
    }
}

async function togglePinnedModel(modelId) {
    const normalizedModelId = normalizeModelId(modelId);
    if (!normalizedModelId) {
        return false;
    }

    const currentPinnedIds = getPinnedModelIds();
    if (currentPinnedIds.includes(normalizedModelId)) {
        return setPinnedModelIds(currentPinnedIds.filter((pinnedModelId) => pinnedModelId !== normalizedModelId));
    }

    if (currentPinnedIds.length >= MODEL_PIN_LIMIT) {
        if (typeof window.notifyWarning === 'function') {
            window.notifyWarning(translate('model_select_pin_limit_reached', 'You can pin up to 8 models.'));
        } else if (typeof window.notifyError === 'function') {
            window.notifyError(translate('model_select_pin_limit_reached', 'You can pin up to 8 models.'));
        }
        return false;
    }

    return setPinnedModelIds([...currentPinnedIds, normalizedModelId]);
}

function resetModelSelectContext() {
    msContext.mode = 'main';
    msContext.side = null;
    msContext.anchorEl = null;
    msContext.selectedModelId = null;
    msContext.onSelect = null;
    msContext.onClose = null;
}

function setModelSelectHeaderOpenState(isOpen) {
    const mainHeader = document.querySelector('.main-container-header');
    if (!mainHeader) return;
    mainHeader.classList.toggle('model-select-open', Boolean(isOpen));
}

function setModelSelectContext(options = {}) {
    msContext.mode = options.mode || 'main';
    msContext.side = options.side || null;
    msContext.anchorEl = options.anchorEl || null;
    msContext.selectedModelId = options.selectedModelId || null;
    msContext.onSelect = typeof options.onSelect === 'function' ? options.onSelect : null;
    msContext.onClose = typeof options.onClose === 'function' ? options.onClose : null;
}

function getModelSelectContext() {
    return {
        mode: msContext.mode,
        side: msContext.side,
        anchorEl: msContext.anchorEl,
        selectedModelId: msContext.selectedModelId,
    };
}

function getRenderedSelectedModelId() {
    if (msContext.mode !== 'main' && msContext.selectedModelId) {
        return msContext.selectedModelId;
    }
    return msSelectedModelId;
}

function isAnchoredModelSelectContext() {
    if (!msContext.anchorEl) return false;
    const wrap = document.getElementById('modelSelect');
    return !wrap || !wrap.contains(msContext.anchorEl);
}

function positionAnchoredModelSelectDropdown() {
    const dropdown = document.getElementById('modelSelectDropdown');
    if (!dropdown) return;

    if (!isAnchoredModelSelectContext() || msMobile.isMobile()) {
        dropdown.classList.remove('model-select-dropdown--anchored');
        dropdown.style.top = '';
        dropdown.style.left = '';
        dropdown.style.width = '';
        dropdown.style.right = '';
        return;
    }

    const anchor = msContext.anchorEl;
    const rect = anchor.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const gap = 8;
    const preferredWidth = Math.max(360, rect.width);

    dropdown.classList.add('model-select-dropdown--anchored');
    dropdown.style.width = `${preferredWidth}px`;
    dropdown.style.left = '0px';
    dropdown.style.top = '0px';
    dropdown.style.right = 'auto';

    const measured = dropdown.getBoundingClientRect();
    let left = rect.left;
    if (left + measured.width > viewportWidth - 8) {
        left = Math.max(8, viewportWidth - measured.width - 8);
    }

    let top = rect.bottom + gap;
    if (top + measured.height > viewportHeight - 8) {
        const aboveTop = rect.top - measured.height - gap;
        top = aboveTop >= 8 ? aboveTop : Math.max(8, viewportHeight - measured.height - 8);
    }

    dropdown.style.left = `${left}px`;
    dropdown.style.top = `${top}px`;
}

async function fetchSupportedFileFormatsForModel(modelId) {
    if (!modelId) {
        return [];
    }
    const model = msModels.find((m) => String(m.model_id) === String(modelId));
    if (!model?.provider) {
        return [];
    }
    if (model.is_byok) {
        return [];
    }
    const params = new URLSearchParams({
        model_id: String(modelId),
        provider: String(model.provider),
    });
    const chatContainer = document.getElementById('chatContainer');
    const projectId = chatContainer?.getAttribute('data-project-id') || '';
    if (projectId) {
        params.set('project_id', projectId);
    }
    try {
        const res = await window.authedFetch(`/api/v1/llm/model/settings?${params.toString()}`);
        if (!res.ok) {
            return [];
        }
        const payload = await res.json();
        if (typeof window.expandSupportedFileFormatsFromSchemaPayload === 'function') {
            return await window.expandSupportedFileFormatsFromSchemaPayload(payload);
        }
        return Array.isArray(payload?.supported_file_formats) ? payload.supported_file_formats : [];
    } catch (_) {
        return [];
    }
}

function buildSupportedMimeSetFromPayload(supportedFileFormats) {
    const supported = new Set();
    (Array.isArray(supportedFileFormats) ? supportedFileFormats : []).forEach((entry) => {
        const formats = Array.isArray(entry?.file_formats) ? entry.file_formats : [];
        formats.forEach((mime) => {
            if (typeof mime === 'string' && mime.trim()) {
                supported.add(mime.trim().toLowerCase());
            }
        });
    });
    return supported;
}

function collectUnsupportedChatHistoryFiles(supportedMimeSet) {
    if (!supportedMimeSet || supportedMimeSet.size === 0) {
        return [];
    }
    const unsupported = new Map();
    const container = document.getElementById('chatAreaContainer') || document.getElementById('chatArea');
    if (!container) {
        return [];
    }
    const blocks = container.querySelectorAll('[data-file-id]');
    blocks.forEach((el) => {
        const fileId = el.dataset.fileId;
        if (!fileId || unsupported.has(fileId)) {
            return;
        }

        let type = String(el.dataset.fileType || '').toLowerCase();
        if (!type) {
            const nested = el.querySelector?.('[data-file-type]');
            if (nested?.dataset?.fileType) {
                type = String(nested.dataset.fileType || '').toLowerCase();
            }
        }

        if (type && !supportedMimeSet.has(type)) {
            const nameCandidate =
                el.getAttribute?.('aria-label')
                || el.querySelector?.('p')?.textContent
                || el.textContent;
            const name = String(nameCandidate || fileId).trim() || fileId;
            unsupported.set(fileId, { id: fileId, name });
        }
    });
    return Array.from(unsupported.values());
}

async function showModelChangeUnsupportedFilesConfirm({ description, confirmText }) {
    const overlay = document.getElementById('modelChangeUnsupportedFilesOverlay');
    const descEl = document.getElementById('modelChangeUnsupportedFilesDesc');
    const cancelBtn = document.getElementById('modelChangeUnsupportedFilesCancel');
    const confirmBtn = document.getElementById('modelChangeUnsupportedFilesConfirm');
    const confirmTextEl = document.getElementById('modelChangeUnsupportedFilesConfirmText');

    if (!overlay || !cancelBtn || !confirmBtn) {
        if (typeof window.showWarningConfirm === 'function') {
            return await window.showWarningConfirm({
                title: translate('model_change_unsupported_title', 'Change model?'),
                message: description || translate('model_change_unsupported_title', 'Change model?'),
                confirmLabel: confirmText || translate('model_change_confirm', 'Change model'),
            });
        }
        notifyError?.(translate('model_change_confirm_unavailable', 'Model change confirmation is unavailable. Please reload the page and try again.'));
        return false;
    }

    if (descEl) {
        descEl.textContent = description || '';
    }
    if (confirmTextEl && confirmText) {
        confirmTextEl.textContent = confirmText;
    }

    return await new Promise((resolve) => {
        const cleanup = () => {
            overlay.setAttribute('hidden', '');
            overlay.setAttribute('aria-hidden', 'true');
            overlay.removeEventListener('click', onBackdrop);
            document.removeEventListener('keydown', onKeydown);
            cancelBtn.removeEventListener('click', onCancel);
            confirmBtn.removeEventListener('click', onConfirm);
        };
        const finish = (value) => {
            cleanup();
            resolve(value);
        };
        const onCancel = (e) => {
            e?.preventDefault?.();
            finish(false);
        };
        const onConfirm = (e) => {
            e?.preventDefault?.();
            finish(true);
        };
        const onBackdrop = (e) => {
            if (e.target === overlay) {
                finish(false);
            }
        };
        const onKeydown = (e) => {
            if (e.key === 'Escape') {
                e.preventDefault();
                finish(false);
            }
        };

        overlay.removeAttribute('hidden');
        overlay.setAttribute('aria-hidden', 'false');
        overlay.addEventListener('click', onBackdrop);
        document.addEventListener('keydown', onKeydown);
        cancelBtn.addEventListener('click', onCancel);
        confirmBtn.addEventListener('click', onConfirm);
    });
}

function setModelSelectDataAttribute(modelId) {
    const container = document.getElementById('modelSelect');
    if (!container) return;
    container.setAttribute('data-model-id', modelId || '');
}

async function updateModelSelectSearchVisibility(visible) {
    const searchRow = document.querySelector('.model-select-search-row');
    const searchInput = document.getElementById('modelSelectSearch');
    if (!searchRow || !searchInput) return;

    const shouldShow = !!visible;
    searchRow.style.display = shouldShow ? '' : 'none';
    searchInput.disabled = !shouldShow;

    if (!shouldShow) {
        const hadValue = searchInput.value !== '';
        if (hadValue) {
            searchInput.value = '';
        }
        if (msSearchActive || hadValue) {
            await applyFilter('');
        }
    }
}

function scheduleModelSelectReposition() {
    if (msRafReposition) return;
    msRafReposition = requestAnimationFrame(() => {
        msRafReposition = null;
        hideModelSelectDetail();
        const dd = document.getElementById('modelSelectDropdown');
        if (dd?.classList.contains('open')) {
            positionAnchoredModelSelectDropdown();
        }
    });
}


async function openModelSelect(options = {}) {
    const modelSelectDropdown = document.getElementById('modelSelectDropdown');
    const backdrop = msMobile.getBackdrop();
    if (!modelSelectDropdown) return;

    setModelSelectContext(options);
    modelSelectDropdown.classList.add('open');
    document.getElementById('modelSelect')?.setAttribute('aria-expanded', 'true');
    document.getElementById('modelSelectToggle')?.setAttribute('aria-expanded', 'true');
    msMobile.isOpen = true;
    setModelSelectHeaderOpenState(true);
    positionAnchoredModelSelectDropdown();
    
    // Show backdrop on mobile
    if (msMobile.isMobile() && backdrop) {
        backdrop.classList.add('active');
        backdrop.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
    }
    
    const modelSelectList = document.getElementById('modelSelectList');
    if (modelSelectList) {
        modelSelectList.scrollTop = 0;
    }
    const modelSelectSearch = document.getElementById('modelSelectSearch');
    if (
        modelSelectSearch &&
        !modelSelectSearch.disabled &&
        !isMobileDevice()
    ) {
        modelSelectSearch.focus();
    }
    
    // Bind mobile events
    if (msMobile.isMobile()) {
        bindMobileEvents();
    }
}

async function closeModelSelect() {
    const modelSelectDropdown = document.getElementById('modelSelectDropdown');
    const backdrop = msMobile.getBackdrop();
    const onClose = msContext.onClose;
    const closedContext = getModelSelectContext();
    if (!modelSelectDropdown) {
        resetModelSelectContext();
        if (onClose) {
            onClose(closedContext);
        }
        return;
    }
    
    modelSelectDropdown.classList.remove('open', 'dragging');
    document.getElementById('modelSelect')?.setAttribute('aria-expanded', 'false');
    document.getElementById('modelSelectToggle')?.setAttribute('aria-expanded', 'false');
    modelSelectDropdown.classList.remove('model-select-dropdown--anchored');
    modelSelectDropdown.style.transform = '';
    modelSelectDropdown.style.top = '';
    modelSelectDropdown.style.left = '';
    modelSelectDropdown.style.right = '';
    modelSelectDropdown.style.width = '';
    resetMobileDragState();
    msMobile.isOpen = false;
    setModelSelectHeaderOpenState(false);
    
    // Hide backdrop
    if (backdrop) {
        backdrop.classList.remove('active');
        backdrop.setAttribute('aria-hidden', 'true');
        backdrop.style.opacity = '';
    }
    
    // Restore body scroll
    document.body.style.overflow = '';
    
    hideModelSelectDetail();
    
    // Unbind mobile events
    unbindMobileEvents();
    
    // Clear search on close
    const modelSelectSearch = document.getElementById('modelSelectSearch');
    if (modelSelectSearch && modelSelectSearch.value) {
        modelSelectSearch.value = '';
        document.getElementById('modelSelectSearchClear')?.classList.remove('is-visible');
        applyFilter('');
    }

    resetModelSelectContext();
    if (onClose) {
        onClose(closedContext);
    }
}
async function toggleModelSelect(options = {}) {
    const modelSelectDropdown = document.getElementById('modelSelectDropdown');
    if (!modelSelectDropdown) return;
    const requestedMode = options.mode || 'main';
    const requestedSide = options.side || null;
    const requestedAnchor = options.anchorEl || null;
    const sameContext = modelSelectDropdown.classList.contains('open')
        && msContext.mode === requestedMode
        && msContext.side === requestedSide
        && msContext.anchorEl === requestedAnchor;
    if (sameContext) {
        closeModelSelect();
        return;
    }
    if (modelSelectDropdown.classList.contains('open')) {
        await closeModelSelect();
    }
    openModelSelect(options);
}

// Attach to the explicit toggle button to avoid bubbling from inside the dropdown
const modelSelectToggleBtn = document.getElementById('modelSelectToggle');
if (modelSelectToggleBtn) {
    modelSelectToggleBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleModelSelect();
    });
}

/** Show the leaderboard destination only when chat setup grants access. */
function setModelSelectLeaderboardAccess(hasAccess) {
    const footer = document.getElementById('modelSelectLeaderboardFooter');
    if (!footer) return;
    footer.hidden = !Boolean(hasAccess);
}

window.setModelSelectLeaderboardAccess = setModelSelectLeaderboardAccess;

const modelSelectHelpButton = document.getElementById('modelSelectHelpButton');
const modelSelectLeaderboardIcon = document.getElementById('modelSelectLeaderboardIcon');
if (modelSelectLeaderboardIcon && typeof Icons !== 'undefined' && Icons.question) {
    // Reuse the shared icon source instead of maintaining inline SVG markup.
    modelSelectLeaderboardIcon.innerHTML = Icons.question;
}
if (modelSelectHelpButton) {
    // The native anchor handles new-tab navigation even if other JavaScript is
    // unavailable. Closing the dropdown keeps the original tab tidy on return.
    modelSelectHelpButton.addEventListener('click', () => closeModelSelect());
}
// Close on outside click
document.addEventListener('click', (e) => {
    const wrap = document.getElementById('modelSelect');
    const dd = document.getElementById('modelSelectDropdown');
    if (!wrap || !dd) return;
    if (!wrap.contains(e.target)) {
        closeModelSelect();
    }
});
// Close on Escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeModelSelect();
    }
});

async function initModelSelect(hasLeaderboardAccessParam) {
    const hasLeaderboardAccess = typeof hasLeaderboardAccessParam === 'undefined'
        ? Boolean(window.hasLeaderboardAccess)
        : Boolean(hasLeaderboardAccessParam);
    setModelSelectLeaderboardAccess(hasLeaderboardAccess);

    if (msInitDone) {
        await updateModelSelectSearchVisibility(true);
        await refreshPinnedModelsUi();
        return;
    }

    const modelSelectDropdown = document.getElementById('modelSelectDropdown');
    const modelSelectSearch = document.getElementById('modelSelectSearch');
    const modelSelectSearchClear = document.getElementById('modelSelectSearchClear');
    const modelSelectList = document.getElementById('modelSelectList');

    await updateModelSelectSearchVisibility(true);

    if (modelSelectSearch) {
        modelSelectSearch.addEventListener('input', async (e) => {
            const query = e.target.value;
            modelSelectSearchClear?.classList.toggle('is-visible', !!query);
            await applyFilter(query);
        });
        modelSelectSearch.addEventListener('keydown', handleModelSelectSearchKeydown);
    }

    if (modelSelectSearchClear && modelSelectSearch) {
        modelSelectSearchClear.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            modelSelectSearch.value = '';
            modelSelectSearchClear.classList.remove('is-visible');
            modelSelectSearch.focus();
            await applyFilter('');
        });
    }

    // Prevent dropdown click from closing itself
    modelSelectDropdown.addEventListener('click', (e) => e.stopPropagation());

    // Keep an anchored selector aligned while its list or viewport moves.
    if (modelSelectList) {
        modelSelectList.addEventListener('scroll', () => {
            hideModelSelectDetail();
            scheduleModelSelectReposition();
        });
    }

    window.addEventListener('resize', scheduleModelSelectReposition);
    window.addEventListener('scroll', scheduleModelSelectReposition, true);

    msInitDone = true;
}

async function ModelSelectLoadModelsInternal(options = {}) {
    const { forceRefresh = false } = options || {};
    const models = typeof window.getCachedUserModels === 'function'
        ? await window.getCachedUserModels({ forceRefresh })
        : await (async () => {
            const res = await window.authedFetch(`/api/v1/llm/models/user`, {
                method: 'GET',
            });
            if (res.status === 401) {
                // Token invalid, redirect handled by auth helper
                if (typeof redirectToLogin === 'function') redirectToLogin();
                return null;
            }
            if (!res.ok) {
                console.error('Model list fetch failed', res.status, await res.text());
                return null;
            }
            return res.json();
        })();
    if (!models) {
        return;
    }
    if (!Array.isArray(models)) {
        console.error('Unexpected models payload');
        return;
    }
    if (typeof window.BYOK?.setAdminModels === 'function') {
        window.BYOK.setAdminModels(models);
    }
    const groupedModels = typeof window.BYOK?.getAllSelectableModels === 'function'
        ? window.BYOK.getAllSelectableModels(models)
        : {
            adminModels: Array.isArray(models) ? models : [],
            byokModels: [],
            allModels: Array.isArray(models) ? models : [],
        };
    msAdminModels = Array.isArray(groupedModels.adminModels) ? groupedModels.adminModels : [];
    msByokModels = Array.isArray(groupedModels.byokModels) ? groupedModels.byokModels : [];
    msModels = Array.isArray(groupedModels.allModels) ? groupedModels.allModels : [];
    msFilteredModels = [...msModels];
    syncPinnedModelsFromSources();
    renderModelSelectFilters();
    await applyFilter(document.getElementById('modelSelectSearch')?.value || '');

    const storedModelId = typeof window.BYOK?.getStoredSelectedModelId === 'function'
        ? window.BYOK.getStoredSelectedModelId()
        : null;
    const selectedByStored = storedModelId
        ? msModels.find((m) => String(m.model_id) === String(storedModelId))
        : null;
    const last = msAdminModels.find((m) => m.is_last);
    const selected = selectedByStored || last || msModels[0] || null;
    msSelectedModelId = selected ? selected.model_id : null;
    setModelSelectDataAttribute(msSelectedModelId);
    await refreshPinnedModelsUi();
    if (selected) updateModelSelectLabel(selected);
    if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('modelSelect:changed', { detail: { modelId: msSelectedModelId } }));
    }

    // Return the exact payload used to rebuild the selector. Other model-list
    // consumers can reuse it instead of issuing another request and risking a
    // briefly inconsistent view of the available custom agents.
    return models;
}

/**
 * Replace the main-header skeleton with resolved model content.
 *
 * When loading fails or there are no selectable models, the translated generic
 * label is shown so the header never remains in an indefinite loading state.
 */
function finishModelSelectTriggerLoading() {
    const skeleton = document.getElementById('modelSelectTriggerSkeleton');
    if (skeleton) {
        skeleton.hidden = true;
        skeleton.style.display = 'none';
    }

    const toggle = document.getElementById('modelSelectToggle');
    if (!toggle) return;
    let name = toggle.querySelector(':scope > .label-name');
    if (!name) {
        name = document.createElement('span');
        name.className = 'label-name';
        const insertionPoint = toggle.querySelector(':scope > .model-select-trigger-skeleton')
            || toggle.querySelector(':scope > .model-select-trigger-status')
        if (insertionPoint) {
            toggle.insertBefore(name, insertionPoint);
        } else {
            toggle.appendChild(name);
        }
    }
    if (!name.textContent?.trim()) {
        name.textContent = translate('model_select_title', 'Select Model');
    }
}

/**
 * Load selectable models and always settle the main-header loading skeleton.
 *
 * @param {object} options - Model loading options.
 * @returns {Promise<void>}
 */
async function ModelSelectLoadModels(options = {}) {
    try {
        return await ModelSelectLoadModelsInternal(options);
    } finally {
        finishModelSelectTriggerLoading();
    }
}

/**
 * Refresh every chat-shell consumer of the user model inventory.
 *
 * Custom agents are exposed by the backend as selectable models. Their CRUD
 * flow therefore needs to refresh both the main selector and any secondary
 * UI that keeps a model cache, such as the chat-box mention menu. Publishing
 * the already-fetched payload keeps those consumers on one snapshot.
 *
 * @returns {Promise<Array|undefined>} The refreshed user model list, when available.
 */
async function refreshUserModelConsumers() {
    const models = await ModelSelectLoadModels({ forceRefresh: true });
    if (!Array.isArray(models)) {
        return;
    }

    window.dispatchEvent(new CustomEvent('userModels:refreshed', {
        detail: { models },
    }));

    return models;
}

function createModelSectionLabel(label) {
    const item = document.createElement('div');
    item.className = 'model-select-section-label';
    item.textContent = label;
    return item;
}

function collectAvailableModelFormats(kind) {
    const set = new Set();
    msModels.forEach((model) => {
        const formats = kind === 'input' ? getModelInputFormats(model) : getModelOutputFormats(model);
        formats.forEach((format) => set.add(format));
    });
    const preferred = ['text', 'image', 'audio', 'video', 'pdf', 'text_document'];
    return [
        ...preferred.filter((format) => set.has(format)),
        ...Array.from(set).filter((format) => !preferred.includes(format)).sort(),
    ];
}

function getModelSelectFilterScrollPositions(host) {
    const positions = {};
    host.querySelectorAll('.model-select-filter-group[data-kind]').forEach((group) => {
        positions[group.dataset.kind] = group.scrollLeft;
    });
    return positions;
}

function restoreModelSelectFilterScrollPositions(host, positions) {
    host.querySelectorAll('.model-select-filter-group[data-kind]').forEach((group) => {
        const scrollLeft = positions[group.dataset.kind];
        if (typeof scrollLeft === 'number') {
            group.scrollLeft = scrollLeft;
        }
    });
}

function renderModelSelectFilters() {
    const host = document.getElementById('modelSelectFilters');
    if (!host) return;
    const scrollPositions = getModelSelectFilterScrollPositions(host);

    const buildGroup = (label, formats, kind, currentValue) => {
        if (!formats.length) return '';
        const chips = formats.map((format) => {
            const meta = getFormatMeta(format);
            const active = currentValue === format ? ' is-active' : '';
            return `
                <button type="button" class="model-select-filter-chip${active}" data-kind="${kind}" data-value="${msEscapeHtml(format)}" aria-pressed="${currentValue === format ? 'true' : 'false'}">
                    ${meta.icon}<span>${msEscapeHtml(meta.label)}</span>
                </button>
            `;
        }).join('');
        return `
            <div class="model-select-filter-group" data-kind="${kind}">
                <span class="model-select-filter-label">${msEscapeHtml(label)}</span>
                ${chips}
            </div>
        `;
    };

    host.innerHTML = [
        buildGroup(translate('model_select_filter_input', 'In'), collectAvailableModelFormats('input'), 'input', msFilterInput),
        buildGroup(translate('model_select_filter_output', 'Out'), collectAvailableModelFormats('output'), 'output', msFilterOutput),
    ].join('');

    restoreModelSelectFilterScrollPositions(host, scrollPositions);

    host.querySelectorAll('.model-select-filter-chip').forEach((chip) => {
        chip.addEventListener('click', async (event) => {
            event.preventDefault();
            event.stopPropagation();
            const kind = chip.dataset.kind;
            const value = chip.dataset.value || null;
            if (kind === 'input') {
                msFilterInput = msFilterInput === value ? null : value;
            } else if (kind === 'output') {
                msFilterOutput = msFilterOutput === value ? null : value;
            }
            renderModelSelectFilters();
            await applyFilter(document.getElementById('modelSelectSearch')?.value || '');
        });
    });
}

async function ModelSelectRenderMainList() {
    const listEl = document.getElementById('modelSelectList');
    if (!listEl) return;

    cleanupModelSelectWarningTooltips();
    
    listEl.innerHTML = '';

    const hasActiveFilters = Boolean(msFilterInput || msFilterOutput);

    if (msSearchActive || hasActiveFilters) {
        for (const m of sortModelsByPinPriority(msFilteredModels)) {
            const item = createModelItem(m);
            listEl.appendChild(item);
        }
    } else {
        const pinnedModelIds = new Set(getPinnedModelIds());
        const pinnedModels = getPinnedModels();
        const unpinnedAdminModels = msAdminModels.filter((model) => !pinnedModelIds.has(String(model.model_id)));
        const unpinnedByokModels = msByokModels.filter((model) => !pinnedModelIds.has(String(model.model_id)));
        const showAdminSectionLabel = unpinnedAdminModels.length > 0 && unpinnedByokModels.length > 0;

        if (pinnedModels.length) {
            listEl.appendChild(createModelSectionLabel(translate('model_select_pinned_models', 'Pinned Models')));
            for (const model of pinnedModels) {
                listEl.appendChild(createModelItem(model));
            }
        }

        if (unpinnedAdminModels.length) {
            if (showAdminSectionLabel) {
                listEl.appendChild(createModelSectionLabel(translate('model_select_server_models', 'Server Models')));
            }
            for (const m of unpinnedAdminModels) {
                listEl.appendChild(createModelItem(m));
            }
        }
        if (unpinnedByokModels.length) {
            listEl.appendChild(createModelSectionLabel(translate('model_select_my_models', 'My Models')));
            for (const m of unpinnedByokModels) {
                listEl.appendChild(createModelItem(m));
            }
        }
    }

    if (!listEl.children.length) {
        const empty = document.createElement('div');
        empty.className = 'model-select-empty';
        empty.textContent = translate('model_select_no_models_available', 'No models available');
        listEl.appendChild(empty);
    }
}

function createModelItem(m) {
    const item = document.createElement('div');
    const itemClasses = ['model-select-item'];
    if (String(m.model_id) === String(getRenderedSelectedModelId())) {
        itemClasses.push('active');
    }
    if (msSearchActive && m.model_id === msHighlightedModelId) {
        itemClasses.push('search-highlight');
    }
    if (isModelPinned(m.model_id)) {
        itemClasses.push('is-pinned');
    }
    item.className = itemClasses.join(' ');
    item.dataset.modelId = m.model_id;
    item.setAttribute('role', 'option');
    item.setAttribute('tabindex', '0');
    item.setAttribute('aria-selected', String(m.model_id) === String(getRenderedSelectedModelId()) ? 'true' : 'false');

    const left = document.createElement('div');
    left.className = 'model-select-item-left';

    const svgWrap = document.createElement('div');
    svgWrap.className = 'model-select-item-left-svg';
    applyModelIcon(svgWrap, m.model_icon);

    const nameEl = document.createElement('div');
    nameEl.className = 'model-select-name';
    nameEl.textContent = m.name || translate('model_select_unnamed_model', 'Unnamed model');

    const nameRow = document.createElement('div');
    nameRow.className = 'model-select-name-row';
    nameRow.appendChild(nameEl);
    const statusBadge = createStatusBadge(m.status);
    if (statusBadge) {
        nameRow.appendChild(statusBadge);
    }

    const descEl = document.createElement('div');
    descEl.className = 'model-select-desc';
    let detailPrefix = '';
    if (m.model_kind === 'agent') {
        if (m.is_shared) {
            const sharedAgentLabel = translate('model_select_shared_agent', 'Shared agent');
            if (m.owner_name) {
                const byLabel = translate('model_select_by', 'by');
                detailPrefix = `${sharedAgentLabel} ${byLabel} ${m.owner_name}`;
            } else {
                detailPrefix = sharedAgentLabel;
            }
        } else {
            detailPrefix = translate('model_select_custom_agent', 'Custom agent');
        }
    }
    const descriptionText = [
        detailPrefix,
        m.description || '',
        getProviderRecipientDisclosure(m),
    ].filter(Boolean).join(' · ');
    descEl.textContent = descriptionText;
    if (!descEl.textContent) {
        descEl.style.display = 'none';
    }

    const textWrap = document.createElement('div');
    textWrap.className = 'model-select-item-left-text';
    textWrap.appendChild(nameRow);
    textWrap.appendChild(descEl);

    left.appendChild(svgWrap);
    left.appendChild(textWrap);

    const right = document.createElement('div');
    right.className = 'model-select-item-right';

    if (m.increased_errors) {
        const warningText = translate(
            'model_select_elevated_errors',
            'This model is experiencing elevated error rates'
        );
        const warningTooltipId = nextModelSelectWarningTooltipId();
        const warningContainer = document.createElement('span');
        warningContainer.className = 'tooltip-container model-select-warning-container';

        const warningTrigger = document.createElement('button');
        warningTrigger.type = 'button';
        warningTrigger.className = 'tooltip-content model-select-capability model-select-warning';
        warningTrigger.setAttribute('aria-label', translate('model_select_warning_label', 'Warning'));
        warningTrigger.setAttribute('aria-describedby', warningTooltipId);
        warningTrigger.innerHTML = Icons.warning;
        warningTrigger.addEventListener('click', (event) => event.stopPropagation());
        warningContainer.appendChild(warningTrigger);

        const warningTooltip = document.createElement('div');
        warningTooltip.className = 'tooltip';
        warningTooltip.id = warningTooltipId;
        warningTooltip.dataset.tooltipOrigin = MODEL_SELECT_WARNING_TOOLTIP_ORIGIN;
        warningTooltip.setAttribute('role', 'tooltip');
        warningTooltip.textContent = warningText;
        warningContainer.appendChild(warningTooltip);

        if (typeof window !== 'undefined' && typeof window.setupTooltip === 'function') {
            window.setupTooltip(warningContainer);
        }
        right.appendChild(warningContainer);
    }

    const iconRegistry = typeof Icons !== 'undefined' ? Icons : null;
    const featureBodies = typeof featureIconBodies !== 'undefined' ? featureIconBodies : iconRegistry?.featureIconBodies;
    if (iconRegistry?.createSvgElement && iconRegistry?.wrapSvgBody && featureBodies?.modelCheck16) {
        const check = iconRegistry.createSvgElement(iconRegistry.wrapSvgBody(featureBodies.modelCheck16, {
            viewBox: '0 0 16 16',
            strokeWidth: '2',
            className: 'model-select-check',
        }));
        right.appendChild(check);
    }

    const pinButton = document.createElement('button');
    const modelPinned = isModelPinned(m.model_id);
    pinButton.type = 'button';
    pinButton.className = 'model-select-pin-btn';
    if (modelPinned) {
        pinButton.classList.add('is-pinned');
    }
    pinButton.innerHTML = modelPinned ? Icons.unpin : (Icons.pin);
    pinButton.setAttribute('aria-pressed', modelPinned ? 'true' : 'false');
    pinButton.setAttribute(
        'aria-label',
        modelPinned
            ? translate('model_select_unpin_model', 'Unpin model')
            : translate('model_select_pin_model', 'Pin model')
    );
    pinButton.setAttribute(
        'title',
        modelPinned
            ? translate('model_select_unpin_model', 'Unpin model')
            : translate('model_select_pin_model', 'Pin model')
    );
    pinButton.addEventListener('click', async (event) => {
        event.preventDefault();
        event.stopPropagation();
        await togglePinnedModel(m.model_id);
    });
    right.appendChild(pinButton);

    item.appendChild(left);
    item.appendChild(right);

    const meta = document.createElement('div');
    meta.className = 'model-select-item-meta';
    meta.innerHTML = [
        ...getModelInputFormats(m).slice(0, 3).map(formatPillHtml),
        ...getModelSelectTools(m).slice(0, 3).map(toolChipHtml),
    ].join('');
    item.appendChild(meta);

    item.addEventListener('click', async (e) => {
        e.stopPropagation();
        await selectModel(m);
    });
    item.addEventListener('keydown', async (e) => {
        if (e.target !== item) return;
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            e.stopPropagation();
            await selectModel(m);
        }
    });
    item.addEventListener('mouseenter', () => showModelSelectDetail(item, m));
    item.addEventListener('mouseleave', hideModelSelectDetail);
    item.addEventListener('focus', () => showModelSelectDetail(item, m));
    item.addEventListener('blur', hideModelSelectDetail);

    return item;
}

function getModelSelectDetailElement() {
    return document.getElementById('modelSelectDetail');
}

function hideModelSelectDetail() {
    const detail = getModelSelectDetailElement();
    if (!detail) return;
    detail.classList.remove('is-visible');
    detail.setAttribute('aria-hidden', 'true');
}

function showModelSelectDetail(row, model) {
    if (!row || !model || msMobile.isMobile()) return;
    const detail = getModelSelectDetailElement();
    const panel = document.getElementById('modelSelectDropdown');
    if (!detail || !panel || !panel.classList.contains('open')) return;

    const iconHtml = resolveModelIcon(model.model_icon);
    const statusText = getStatusText(model.status);
    const statusHtml = statusText
        ? `<span class="model-select-item-status ${msEscapeHtml(model.status)}">${msEscapeHtml(statusText)}</span>`
        : '';
    const descriptionText = String(model.description || '').trim();
    const inputs = getModelInputFormats(model).map(formatPillHtml).join('');
    const outputs = getModelOutputFormats(model).map(formatPillHtml).join('');
    const toolsSection = modelSelectToolsDetailSectionHtml(model);
    const connectionsSection = modelSelectConnectionsDetailSectionHtml(model);
    const speedValue = model.tokens_per_second;
    const speed = speedValue != null
        ? `${msEscapeHtml(speedValue)}<span class="model-select-stat-unit">${msEscapeHtml(translate('model_select_tokens_per_second_unit', 'tok/s'))}</span>`
        : `<span class="model-select-stat-unit">${msEscapeHtml(translate('model_select_tokens_per_second_placeholder', 'Speed data soon'))}</span>`;

    detail.innerHTML = `
        <div class="model-select-detail-head">
            <div class="model-select-detail-icon" aria-hidden="true">${iconHtml}</div>
            <div class="model-select-detail-title">
                <div class="model-select-detail-name">${msEscapeHtml(model.name || translate('model_select_unnamed_model', 'Unnamed model'))}${statusHtml}</div>
            </div>
        </div>
        ${descriptionText ? `<p class="model-select-detail-desc">${msEscapeHtml(descriptionText)}</p>` : ''}
        <div class="model-select-detail-section">
            <span class="model-select-detail-section-label">${msEscapeHtml(translate('model_select_detail_formats', 'Formats'))}</span>
            <div class="model-select-format-row">
                ${inputs}
                <span class="model-select-arrow" aria-hidden="true">
                    ${Icons.arrow_right}
                </span>
                ${outputs}
            </div>
        </div>
        ${toolsSection}
        ${connectionsSection}
        <div class="model-select-detail-section">
            <span class="model-select-detail-section-label">${msEscapeHtml(translate('model_select_detail_performance', 'Performance'))}</span>
            <div class="model-select-stat-row">
                <div class="model-select-stat">
                    ${Icons.stats_arrow}
                    ${speed}
                </div>
            </div>
        </div>
    `;

    positionModelSelectDetail(row, detail, panel);
    detail.classList.add('is-visible');
    detail.setAttribute('aria-hidden', 'false');
}

function positionModelSelectDetail(row, detail, panel) {
    const rowRect = row.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const gap = 7;
    const width = 320;
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    let left = panelRect.right + gap;
    if (left + width > viewportWidth - 12) {
        left = panelRect.left - width - gap;
    }
    if (left < 12) {
        left = 12;
    }

    detail.style.left = '0px';
    detail.style.top = '0px';
    const cardHeight = detail.getBoundingClientRect().height;
    let top = rowRect.top;
    if (top + cardHeight > viewportHeight - 12) {
        top = viewportHeight - cardHeight - 12;
    }
    if (top < 12) {
        top = 12;
    }

    detail.style.left = `${left}px`;
    detail.style.top = `${top}px`;
}

async function ModelSelectSaveLastModel(modelId) {
    if (typeof window.BYOK?.setStoredSelectedModelId === 'function') {
        window.BYOK.setStoredSelectedModelId(modelId);
    }
    if (typeof window.BYOK?.isByokModelId === 'function' && window.BYOK.isByokModelId(modelId)) {
        return;
    }
    const body = JSON.stringify({ model_id: modelId });
    await window.authedFetch(`/api/v1/users/settings/last-model/set`, {
        method: 'POST',
        body
    });
}

function updateModelSelectLabel(model) {
    const toggle = document.getElementById('modelSelectToggle');
    if (!toggle) return;
    const recipientDisclosure = getProviderRecipientDisclosure(model);
    renderModelSelectTriggerContent(toggle, model);
    const titleParts = [model.name || '', recipientDisclosure].filter(Boolean);
    const labelText = titleParts.join(' - ');
    if (labelText) {
        toggle.title = labelText;
        toggle.setAttribute('aria-label', labelText);
    } else {
        toggle.removeAttribute('title');
        toggle.removeAttribute('aria-label');
    }
    const status = document.getElementById('modelSelectTriggerStatus');
    if (status) {
        const statusText = getStatusText(model.status);
        if (statusText) {
            status.hidden = false;
            status.textContent = statusText;
            status.className = `model-select-trigger-status ${model.status}`;
        } else {
            status.hidden = true;
            status.textContent = '';
            status.className = 'model-select-trigger-status';
        }
    }
    finishModelSelectTriggerLoading();
}

async function selectModel(model) {
    if (!model) return;

    if (msContext.mode !== 'main' && typeof msContext.onSelect === 'function') {
        await msContext.onSelect(model);
        closeModelSelect();
        return;
    }

    const previousModelId = msSelectedModelId;
    const nextModelId = model.model_id;
    if (previousModelId && String(previousModelId) !== String(nextModelId)) {
        const supportedFormatsPayload = await fetchSupportedFileFormatsForModel(nextModelId);
        const supportedMimeSet = buildSupportedMimeSetFromPayload(supportedFormatsPayload);

        const currentAttachments = typeof window.getCurrentChatAttachmentFiles === 'function'
            ? (window.getCurrentChatAttachmentFiles() || [])
            : [];
        const unsupportedAttachments = supportedMimeSet.size
            ? currentAttachments.filter((f) => {
                const mime = String(f?.mime_type || '').toLowerCase();
                return mime && !supportedMimeSet.has(mime);
            })
            : [];

        if (unsupportedAttachments.length) {
            const names = unsupportedAttachments.map((f) => f.name || f.id).slice(0, 8).join(', ');
            const suffix = unsupportedAttachments.length > 8 ? ` (+${unsupportedAttachments.length - 8} more)` : '';
            const ok = await showModelChangeUnsupportedFilesConfirm({
                description: interpolateTranslation(
                    translate('model_change_remove_attachments_desc', 'Changing the model will remove some attached files from the chat box: {files}'),
                    { files: `${names}${suffix}` }
                ),
                confirmText: translate('model_change_remove_attachments_confirm', 'Remove & change model'),
            });
            if (!ok) {
                return;
            }
            if (typeof window.removeChatAttachmentsByIds === 'function') {
                window.removeChatAttachmentsByIds(unsupportedAttachments.map((f) => f.id));
            }
        }

        const unsupportedHistory = collectUnsupportedChatHistoryFiles(supportedMimeSet);
        if (unsupportedHistory.length) {
            const names = unsupportedHistory.map((f) => f.name || f.id).slice(0, 8).join(', ');
            const suffix = unsupportedHistory.length > 8 ? ` (+${unsupportedHistory.length - 8} more)` : '';
            const ok = await showModelChangeUnsupportedFilesConfirm({
                description: interpolateTranslation(
                    translate('model_change_unsupported_history_desc', 'This chat history contains files that the selected model may not support: {files}'),
                    { files: `${names}${suffix}` }
                ),
                confirmText: translate('model_change_anyway_confirm', 'Change model anyway'),
            });
            if (!ok) {
                return;
            }
        }
    }

    msSelectedModelId = model.model_id;
    setModelSelectDataAttribute(msSelectedModelId);
    // Update UI immediately
    updateModelSelectLabel(model);
    await refreshPinnedModelsUi();
    if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('modelSelect:changed', { detail: { modelId: msSelectedModelId } }));
    }
    // Persist selection
    try {
        await ModelSelectSaveLastModel(model.model_id);
    } catch (e) {
        console.error('Failed to save last model', e);
    }
    // Close dropdown after selection
    closeModelSelect();
}

function getHighlightedModel() {
    if (!msSearchActive || !msFilteredModels.length) {
        return null;
    }
    if (msHighlightedModelIndex >= 0 && msHighlightedModelIndex < msFilteredModels.length) {
        return msFilteredModels[msHighlightedModelIndex];
    }
    return msFilteredModels[0];
}

function updateSearchHighlightStyles() {
    const listEl = document.getElementById('modelSelectList');
    if (!listEl) return;
    const items = listEl.querySelectorAll('.model-select-item');
    items.forEach((item) => {
        if (
            msSearchActive &&
            msHighlightedModelId &&
            String(item.dataset.modelId) === String(msHighlightedModelId)
        ) {
            item.classList.add('search-highlight');
        } else {
            item.classList.remove('search-highlight');
        }
    });
}

function scrollHighlightedModelIntoView() {
    if (!msSearchActive || !msHighlightedModelId) return;
    const listEl = document.getElementById('modelSelectList');
    if (!listEl) return;
    const items = listEl.querySelectorAll('.model-select-item');
    for (const item of items) {
        if (String(item.dataset.modelId) === String(msHighlightedModelId)) {
            if (typeof item.scrollIntoView === 'function') {
                item.scrollIntoView({ block: 'nearest' });
            }
            break;
        }
    }
}

function clearSearchHighlight() {
    msHighlightedModelId = null;
    msHighlightedModelIndex = -1;
    updateSearchHighlightStyles();
}

function setSearchHighlightByIndex(index) {
    if (!msSearchActive || !msFilteredModels.length) {
        clearSearchHighlight();
        return;
    }
    const length = msFilteredModels.length;
    const normalizedIndex = ((index % length) + length) % length;
    msHighlightedModelIndex = normalizedIndex;
    msHighlightedModelId = msFilteredModels[normalizedIndex]?.model_id ?? null;
    updateSearchHighlightStyles();
    scrollHighlightedModelIntoView();
}

async function handleModelSelectSearchKeydown(event) {
    if (!msSearchActive) {
        return;
    }

    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && msFilteredModels.length) {
        event.preventDefault();
        const delta = event.key === 'ArrowDown' ? 1 : -1;
        const nextIndex = msHighlightedModelIndex === -1
            ? (delta > 0 ? 0 : msFilteredModels.length - 1)
            : msHighlightedModelIndex + delta;
        setSearchHighlightByIndex(nextIndex);
        return;
    }

    if (event.key === 'Enter' && msFilteredModels.length) {
        event.preventDefault();
        const model = getHighlightedModel();
        if (model) {
            await selectModel(model);
        }
    }
}

async function applyFilter(query) {
    const q = (query || '').toLowerCase().trim();
    msSearchActive = !!q;

    msFilteredModels = sortModelsByPinPriority(msModels.filter((m) => {
        if (!modelMatchesActiveFormatFilters(m)) {
            return false;
        }
        if (!q) {
            return true;
        }
        const name = (m.name || '').toLowerCase();
        const description = (m.description || '').toLowerCase();
        const formats = [...getModelInputFormats(m), ...getModelOutputFormats(m)].join(' ').toLowerCase();
        const tools = getModelSelectTools(m).join(' ').toLowerCase();
        return (
            name.includes(q)
            || description.includes(q)
            || formats.includes(q)
            || tools.includes(q)
        );
    }));
    
    await ModelSelectRenderMainList();

    if (msSearchActive && msFilteredModels.length) {
        setSearchHighlightByIndex(msHighlightedModelIndex === -1 ? 0 : msHighlightedModelIndex);
    } else {
        clearSearchHighlight();
    }
}

// ============================================================================
// Mobile Bottom Sheet Touch Handlers
// ============================================================================

function resetMobileDragState(options = {}) {
    msMobile.isDragging = false;
    msMobile.dragStartY = 0;
    msMobile.dragCurrentY = 0;
    msMobile.dragLastY = 0;
    msMobile.dragLastTime = 0;
    msMobile.dragVelocity = 0;
    if (!options.preservePointerId) {
        msMobile.pointerId = null;
    }
    msMobile.didDragMove = false;
    if (!options.preserveClickSkip) {
        msMobile.skipHandleClick = false;
    }
}

function beginMobileDrag(clientY) {
    if (!msMobile.isMobile() || !msMobile.isOpen) return false;

    msMobile.isDragging = true;
    msMobile.dragStartY = clientY;
    msMobile.dragCurrentY = clientY;
    msMobile.dragLastY = clientY;
    msMobile.dragLastTime = typeof performance !== 'undefined' ? performance.now() : Date.now();
    msMobile.dragVelocity = 0;
    msMobile.didDragMove = false;

    const dropdown = msMobile.getDropdown();
    if (dropdown) dropdown.classList.add('dragging');
    return true;
}

function applyMobileDragStyles(deltaY) {
    const dropdown = msMobile.getDropdown();
    if (dropdown) {
        if (deltaY > 0) {
            dropdown.style.transform = `translateY(${deltaY}px)`;
        } else {
            dropdown.style.transform = '';
        }
    }

    const backdrop = msMobile.getBackdrop();
    if (backdrop) {
        if (deltaY > 0) {
            const progress = Math.min(deltaY / 300, 1);
            backdrop.style.opacity = String(1 - progress * 0.5);
        } else {
            backdrop.style.opacity = '';
        }
    }
}

/**
 * Return a close distance that scales with the visible bottom sheet.
 *
 * A fixed 100px threshold is disproportionately large for a short model list:
 * the final rows can move entirely below the viewport before the gesture is
 * accepted. Keeping a sensible floor still distinguishes a drag from a tap.
 */
function getMobileDragCloseThreshold(dropdown) {
    const sheetHeight = dropdown?.getBoundingClientRect?.().height;
    if (!Number.isFinite(sheetHeight) || sheetHeight <= 0) {
        return msMobile.dragThreshold;
    }
    return Math.min(msMobile.dragThreshold, Math.max(48, sheetHeight * 0.18));
}

function updateMobileDrag(clientY) {
    if (!msMobile.isDragging) return;

    msMobile.dragCurrentY = clientY;
    const deltaY = Math.max(0, msMobile.dragCurrentY - msMobile.dragStartY);
    if (!msMobile.didDragMove && deltaY > 6) {
        msMobile.didDragMove = true;
    }

    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    const elapsed = now - (msMobile.dragLastTime || now);
    if (elapsed > 0) {
        const velocity = (clientY - msMobile.dragLastY) / elapsed;
        msMobile.dragVelocity = velocity > 0 ? velocity : 0;
    }
    msMobile.dragLastY = clientY;
    msMobile.dragLastTime = now;

    applyMobileDragStyles(deltaY);
}

function finishMobileDrag(options = {}) {
    if (!msMobile.isDragging) return;

    const dropdown = msMobile.getDropdown();
    const backdrop = msMobile.getBackdrop();
    if (dropdown) dropdown.classList.remove('dragging');
    if (backdrop) backdrop.style.opacity = '';

    const deltaY = Math.max(0, msMobile.dragCurrentY - msMobile.dragStartY);
    const closeThreshold = getMobileDragCloseThreshold(dropdown);
    const shouldCloseByDistance = deltaY >= closeThreshold;
    const shouldCloseByVelocity = msMobile.dragVelocity > msMobile.velocityCloseThreshold && deltaY > 10;
    const shouldCloseByTap = !msMobile.didDragMove && !options.ignoreTap;
    const shouldClose = options.forceClose || shouldCloseByDistance || shouldCloseByVelocity || shouldCloseByTap;

    if (shouldClose) {
        closeModelSelect();
        return;
    }

    if (dropdown) {
        dropdown.style.transform = '';
    }
    const shouldSkipNextClick = msMobile.didDragMove;
    if (shouldSkipNextClick) {
        msMobile.skipHandleClick = true;
    }
    resetMobileDragState({ preserveClickSkip: shouldSkipNextClick || !!options.preserveClickSkip });
}

function findTouchById(touchList, id) {
    if (!touchList || typeof touchList.length === 'undefined') return null;
    if (typeof id !== 'number') {
        return touchList.length > 0 ? touchList[0] : null;
    }
    for (let i = 0; i < touchList.length; i += 1) {
        const touch = touchList[i];
        if (touch && touch.identifier === id) {
            return touch;
        }
    }
    return touchList.length > 0 ? touchList[0] : null;
}

function handleMobileTouchStart(event) {
    if (!msMobile.isMobile() || !msMobile.isOpen) return;
    
    const touch = event.touches && event.touches[0];
    if (!touch) return;
    msMobile.pointerId = touch.identifier;

    beginMobileDrag(touch.clientY);
}

function handleMobileTouchMove(event) {
    if (!msMobile.isDragging || !msMobile.isMobile()) return;
    
    const touch = findTouchById(event.touches, msMobile.pointerId);
    if (!touch) return;

    updateMobileDrag(touch.clientY);
    if (typeof event.cancelable !== 'undefined' && event.cancelable) {
        event.preventDefault();
    }
}

function handleMobileTouchEnd(event) {
    if (!msMobile.isDragging) return;

    const touch = findTouchById(event.changedTouches, msMobile.pointerId);
    if (touch) {
        msMobile.dragCurrentY = touch.clientY;
    }

    finishMobileDrag();
}

function handleMobileTouchCancel() {
    if (!msMobile.isDragging) return;
    finishMobileDrag({ ignoreTap: true, preserveClickSkip: true });
}

function handleMobilePointerDown(event) {
    if (!msMobile.isMobile() || !msMobile.isOpen) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;

    msMobile.pointerId = event.pointerId;
    if (typeof event.target?.setPointerCapture === 'function') {
        event.target.setPointerCapture(event.pointerId);
    }
    beginMobileDrag(event.clientY);
}

function handleMobilePointerMove(event) {
    if (!msMobile.isDragging || event.pointerId !== msMobile.pointerId) return;
    updateMobileDrag(event.clientY);
    if (typeof event.cancelable !== 'undefined' && event.cancelable) {
        event.preventDefault();
    }
}

function handleMobilePointerUp(event) {
    if (event.pointerId !== msMobile.pointerId) return;
    msMobile.dragCurrentY = event.clientY;
    if (typeof event.target?.releasePointerCapture === 'function') {
        event.target.releasePointerCapture(event.pointerId);
    }
    finishMobileDrag();
}

function handleMobilePointerCancel(event) {
    if (event.pointerId !== msMobile.pointerId) return;
    if (typeof event.target?.releasePointerCapture === 'function') {
        event.target.releasePointerCapture(event.pointerId);
    }
    finishMobileDrag({ ignoreTap: true, preserveClickSkip: true });
}

function handleDragHandleClick(event) {
    if (!msMobile.isMobile() || !msMobile.isOpen) return;
    if (msMobile.skipHandleClick) {
        msMobile.skipHandleClick = false;
        return;
    }
    event.preventDefault();
    closeModelSelect();
}

function handleBackdropClick(event) {
    if (event.target === msMobile.getBackdrop()) {
        closeModelSelect();
    }
}

function handleWindowResize() {
    if (!msMobile.isOpen) return;

    // Mobile Safari can emit resize events while its browser chrome changes
    // during a touch. Resetting the inline transform mid-gesture makes the
    // sheet jump back while the gesture state keeps advancing.
    if (msMobile.isDragging) return;
    
    const dropdown = msMobile.getDropdown();
    const backdrop = msMobile.getBackdrop();
    
    if (!dropdown) return;
    
    // Reset any inline styles that may conflict
    dropdown.style.transform = '';
    
    // Update body scroll lock based on current viewport
    if (msMobile.isMobile()) {
        document.body.style.overflow = 'hidden';
        if (backdrop) {
            backdrop.classList.add('active');
        }
    } else {
        document.body.style.overflow = '';
        if (backdrop) {
            backdrop.classList.remove('active');
        }
    }
}

function bindMobileEvents() {
    const dragHandle = msMobile.getDragHandle();
    const backdrop = msMobile.getBackdrop();
    const closeButton = msMobile.getCloseButton();
    
    if (dragHandle) {
        dragHandle.addEventListener('click', handleDragHandleClick);

        // Pointer events already represent touch input on current Safari,
        // Chrome, and Firefox. Binding both APIs lets one finger create two
        // competing drag lifecycles with different identifiers on iOS.
        if (msMobile.supportsPointerEvents()) {
            dragHandle.addEventListener('pointerdown', handleMobilePointerDown);
            dragHandle.addEventListener('pointermove', handleMobilePointerMove, { passive: false });
            dragHandle.addEventListener('pointerup', handleMobilePointerUp);
            dragHandle.addEventListener('pointercancel', handleMobilePointerCancel);
            msMobile.boundWithPointerEvents = true;
        } else {
            dragHandle.addEventListener('touchstart', handleMobileTouchStart, { passive: true });
            dragHandle.addEventListener('touchmove', handleMobileTouchMove, { passive: false });
            dragHandle.addEventListener('touchend', handleMobileTouchEnd, { passive: true });
            dragHandle.addEventListener('touchcancel', handleMobileTouchCancel, { passive: true });
        }
    }
    
    if (backdrop) {
        backdrop.addEventListener('click', handleBackdropClick);
    }
    
    if (closeButton) {
        closeButton.addEventListener('click', closeModelSelect);
    }
    
    window.addEventListener('resize', handleWindowResize);
}

function unbindMobileEvents() {
    const dragHandle = msMobile.getDragHandle();
    const backdrop = msMobile.getBackdrop();
    const closeButton = msMobile.getCloseButton();
    
    if (dragHandle) {
        dragHandle.removeEventListener('click', handleDragHandleClick);
        if (msMobile.boundWithPointerEvents) {
            dragHandle.removeEventListener('pointerdown', handleMobilePointerDown);
            dragHandle.removeEventListener('pointermove', handleMobilePointerMove);
            dragHandle.removeEventListener('pointerup', handleMobilePointerUp);
            dragHandle.removeEventListener('pointercancel', handleMobilePointerCancel);
            msMobile.boundWithPointerEvents = false;
        } else {
            dragHandle.removeEventListener('touchstart', handleMobileTouchStart);
            dragHandle.removeEventListener('touchmove', handleMobileTouchMove);
            dragHandle.removeEventListener('touchend', handleMobileTouchEnd);
            dragHandle.removeEventListener('touchcancel', handleMobileTouchCancel);
        }
    }
    
    if (backdrop) {
        backdrop.removeEventListener('click', handleBackdropClick);
    }
    
    if (closeButton) {
        closeButton.removeEventListener('click', closeModelSelect);
    }
    
    window.removeEventListener('resize', handleWindowResize);
}

// Expose selector helpers for the other chat modules.
window.initModelSelect = initModelSelect;
window.ModelSelectLoadModels = ModelSelectLoadModels;
window.refreshUserModelConsumers = refreshUserModelConsumers;
window.getSelectedModel = getSelectedModel;
window.selectModel = selectModel;
window.updateModelSelectLabel = updateModelSelectLabel;
window.renderModelSelectTriggerContent = renderModelSelectTriggerContent;
window.getSelectedModelId = getSelectedModelId;
window.getPinnedModelIds = getPinnedModelIds;
window.togglePinnedModel = togglePinnedModel;
window.setModelSelectDataAttribute = setModelSelectDataAttribute;
window.openModelSelect = openModelSelect;
window.closeModelSelect = closeModelSelect;
window.toggleModelSelect = toggleModelSelect;
window.getModelSelectContext = getModelSelectContext;

/**
 * Initialize the selector from the authoritative chat setup payload.
 *
 * init.js is loaded earlier in the document and starts its request immediately,
 * so a very fast cached response can arrive before this script is registered.
 * Supporting both the saved payload and the readiness event removes that
 * script-order race while keeping initialization single-shot.
 */
function initializeModelSelectFromChatSetup(chatSetup) {
    if (!chatSetup || typeof chatSetup !== 'object') return;

    initModelSelect(Boolean(chatSetup.has_leaderboard_access)).catch((error) => {
        console.error('Failed to initialize model selector', error);
    });
    ModelSelectLoadModels().catch((error) => {
        console.error('Failed to load models', error);
    });
}

if (window.chatSetup) {
    initializeModelSelectFromChatSetup(window.chatSetup);
} else {
    document.addEventListener('chatSetupReady', (event) => {
        initializeModelSelectFromChatSetup(event?.detail);
    }, { once: true });
}

window.addEventListener('byok:modelsChanged', () => {
    ModelSelectLoadModels({ forceRefresh: true }).catch((error) => {
        console.error('Failed to reload models after BYOK update', error);
    });
});
