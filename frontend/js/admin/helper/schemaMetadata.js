const ADMIN_EMPTY_STATE_SVG_TAGS = new Set([
    'svg',
    'g',
    'path',
    'circle',
    'rect',
    'line',
    'polyline',
    'polygon',
    'ellipse',
]);

const ADMIN_EMPTY_STATE_SVG_ATTRS = new Set([
    'xmlns',
    'viewbox',
    'width',
    'height',
    'fill',
    'stroke',
    'stroke-width',
    'stroke-linecap',
    'stroke-linejoin',
    'stroke-miterlimit',
    'opacity',
    'transform',
    'd',
    'cx',
    'cy',
    'r',
    'rx',
    'ry',
    'x',
    'y',
    'x1',
    'x2',
    'y1',
    'y2',
    'points',
    'class',
    'role',
    'focusable',
    'aria-hidden',
    'fill-rule',
    'clip-rule',
]);

function sanitizeAdminEmptyStateSvgNode(node) {
    if (!(node instanceof Element)) {
        return null;
    }

    const tagName = node.tagName.toLowerCase();
    if (!ADMIN_EMPTY_STATE_SVG_TAGS.has(tagName)) {
        return null;
    }

    const safeNode = document.createElementNS('http://www.w3.org/2000/svg', tagName);
    Array.from(node.attributes).forEach((attr) => {
        const attrName = attr.name.toLowerCase();
        if (attrName.startsWith('on') || !ADMIN_EMPTY_STATE_SVG_ATTRS.has(attrName)) {
            return;
        }

        safeNode.setAttribute(attr.name, attr.value);
    });

    Array.from(node.children).forEach((child) => {
        const safeChild = sanitizeAdminEmptyStateSvgNode(child);
        if (safeChild) {
            safeNode.appendChild(safeChild);
        }
    });

    return safeNode;
}

function sanitizeAdminEmptyStateIcon(icon) {
    if (icon instanceof Element) {
        return icon.tagName?.toLowerCase() === 'svg' ? sanitizeAdminEmptyStateSvgNode(icon) : null;
    }

    if (typeof icon !== 'string' || !icon.trim()) {
        return null;
    }

    const parser = new DOMParser();
    const doc = parser.parseFromString(icon, 'text/html');
    if (doc.querySelector('parsererror') || doc.body.childElementCount !== 1) {
        return null;
    }

    const root = doc.body.firstElementChild;
    if (!root || root.tagName.toLowerCase() !== 'svg') {
        return null;
    }

    return sanitizeAdminEmptyStateSvgNode(root);
}

const ADMIN_SCHEMA_DISPLAY_NAME_CACHE = new Map();

function getAdminSchemaDisplayNames(type) {
    const locale = document.documentElement?.lang || 'en';
    const cacheKey = `${locale}:${type}`;
    if (ADMIN_SCHEMA_DISPLAY_NAME_CACHE.has(cacheKey)) {
        return ADMIN_SCHEMA_DISPLAY_NAME_CACHE.get(cacheKey);
    }
    try {
        const displayNames = new Intl.DisplayNames(locale ? [locale] : undefined, { type });
        ADMIN_SCHEMA_DISPLAY_NAME_CACHE.set(cacheKey, displayNames);
        return displayNames;
    } catch (error) {
        ADMIN_SCHEMA_DISPLAY_NAME_CACHE.set(cacheKey, null);
        return null;
    }
}

function resolveAdminSchemaLocalizedOptionLabel(option = {}) {
    const displayType = option?.metadata?.i18n_display_type;
    if (!displayType || !option?.value) {
        return null;
    }
    const normalizedType = String(displayType).trim().toLowerCase();
    if (normalizedType !== 'region' && normalizedType !== 'language') {
        return null;
    }
    const displayNames = getAdminSchemaDisplayNames(normalizedType);
    if (!displayNames) {
        return null;
    }
    const value = normalizedType === 'region'
        ? String(option.value).trim().toUpperCase()
        : String(option.value).trim().toLowerCase();
    return displayNames.of(value) || null;
}

const resolveAdminSchemaOptionLabel = (option = {}, translate = helperT) => {
    const fallback = option.label || option.value || option.id || '';
    if (option.i18n_label) {
        return translate(option.i18n_label, fallback);
    }
    const localizedLabel = resolveAdminSchemaLocalizedOptionLabel(option);
    return localizedLabel || fallback;
};

const PROVIDER_LABEL_MAP = {
    aiohttp: 'AIOHTTP',
    crawl4ai: 'Crawl4AI',
    custom: 'Custom',
    duckduckgo: 'DuckDuckGo',
    exa: 'Exa',
    firecrawl: 'Firecrawl',
    openai: 'OpenAI',
    openai_responses: 'OpenAI Responses API',
    openai_chat_completions: 'OpenAI Chat Completions API',
    microsoft_azure: 'Microsoft Azure',
    anthropic: 'Anthropic',
    anthropic_base: 'Anthropic Base',
    google_aistudio: 'Google AI Studio',
    openrouter: 'OpenRouter',
    ollama: 'Ollama',
    lmstudio: 'LM Studio',
    elevenlabs: 'ElevenLabs',
    groq: 'Groq',
    deepseek: 'DeepSeek',
    fireworks: 'Fireworks AI',
    cerebras: 'Cerebras',
    localai: 'LocalAI',
    automatic1111: 'Automatic1111',
    tavily: 'Tavily',
    serper: 'Serper',
    perplexity: 'Perplexity',
    searxng: 'SearXNG',
    xai: 'xAI',
    you: 'You.com',
    mistral: 'Mistral',
    meta: 'Meta',
    nvidia: 'NVIDIA',
    nebius: 'Nebius',
    minimax: 'MiniMax',
    amazon: 'Amazon',
    alibaba: 'Alibaba',
    baidu: 'Baidu'
};

const DEFAULT_PROVIDER_ICON_KEYS = [
    'openai',
    'anthropic',
    'google_aistudio',
    'ollama',
    'openrouter',
    'nvidia',
    'mistral',
    'meta',
    'xai',
    'amazon',
    'microsoft',
    'minimax',
    'lmstudio',
    'elevenlabs',
    'nebius',
];

// Keep the provider icon policy shared by the provider list and provider
// create/edit form. The backend enforces the same policy for API callers;
// this client-side copy prevents the UI from offering an invalid native
// provider selection while a schema is loading.
const CUSTOM_PROVIDER_ICON_KEYS = Object.freeze([
    'openai_responses',
    'openai_chat_completions',
    'anthropic_base',
]);

const PROVIDER_DEFAULT_ICON_MAP = Object.freeze({
    openai: 'openai',
    openai_responses: 'openai',
    openai_chat_completions: 'openai',
    microsoft_azure: 'microsoft',
    anthropic: 'anthropic',
    anthropic_base: 'anthropic',
    google_aistudio: 'google_aistudio',
    openrouter: 'openrouter',
    ollama: 'ollama',
    lmstudio: 'lmstudio',
    elevenlabs: 'elevenlabs',
    xai: 'xai',
});

function normalizeProviderIconKey(providerKey = '') {
    return String(providerKey || '').trim().toLowerCase();
}

function providerSupportsCustomIcon(providerKey = '') {
    return CUSTOM_PROVIDER_ICON_KEYS.includes(normalizeProviderIconKey(providerKey));
}

function getDefaultProviderIconKey(providerKey = '') {
    const normalized = normalizeProviderIconKey(providerKey);
    return PROVIDER_DEFAULT_ICON_MAP[normalized] || normalized || 'omlorix';
}

function formatProviderLabel(providerKey = '') {
    const rawKey = (providerKey || '').toString().trim();
    const key = rawKey.toLowerCase();
    if (!key) {
        return '';
    }
    if (PROVIDER_LABEL_MAP[key]) {
        return PROVIDER_LABEL_MAP[key];
    }
    if (/[A-Z]/.test(rawKey) && !/[_\-]/.test(rawKey)) {
        return rawKey;
    }
    return key
        .split(/[_\-]/)
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}
