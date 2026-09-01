function isRetryableMermaidRenderError(error) {
    const message = String(error?.message || error || '').toLowerCase();
    return message.includes('not in render tree');
}

async function initializeMermaidRuntime() {
    const runtime = window.OmlorixMermaidRuntime;
    if (!runtime || typeof runtime.initializeMermaidRuntime !== 'function') {
        return null;
    }
    return runtime.initializeMermaidRuntime({ theme: getMermaidTheme() });
}

async function renderMermaidDiagram(target, source) {
    if (!target) {
        return false;
    }
    const runtime = window.OmlorixMermaidRuntime;
    const normalizedSource = runtime && typeof runtime.normalizeMermaidSource === 'function'
        ? runtime.normalizeMermaidSource(source)
        : String(source || '');
    const code = String(normalizedSource || '').trim();
    if (!code) {
        target.classList.add('mermaid-diagram-error');
        target.textContent = getChatPreviewTranslation('code_block_mermaid_no_content', 'No Mermaid content.');
        return false;
    }

    let mermaidApi = null;
    try {
        mermaidApi = await initializeMermaidRuntime();
    } catch (_) {
        mermaidApi = null;
    }
    if (!mermaidApi || typeof mermaidApi.render !== 'function') {
        target.classList.add('mermaid-diagram-error');
        target.textContent = getChatPreviewTranslation('code_block_mermaid_renderer_unavailable', 'Mermaid renderer is unavailable.');
        return false;
    }

    let lastError = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
        await waitForPreviewRenderReady(target, attempt === 0 ? 8 : 16);
        const renderId = `mermaid-diagram-${Date.now()}-${mermaidRenderCounter++}`;
        try {
            const rendered = await mermaidApi.render(renderId, code);
            const svg = typeof rendered === 'string' ? rendered : rendered?.svg;
            if (!svg) {
                throw new Error(getChatPreviewTranslation('code_block_mermaid_empty_result', 'Empty Mermaid render result'));
            }
            target.classList.remove('mermaid-diagram-error');
            target.innerHTML = svg;
            if (typeof rendered?.bindFunctions === 'function') {
                rendered.bindFunctions(target);
            }
            return true;
        } catch (error) {
            lastError = error;
            if (target.isConnected && isRetryableMermaidRenderError(error) && attempt < 2) {
                continue;
            }
            break;
        }
    }

    target.classList.add('mermaid-diagram-error');
    target.textContent = formatChatPreviewTranslation('code_block_mermaid_render_error', 'Mermaid render error: {message}', {
        message: lastError?.message || getChatPreviewTranslation('common_unknown_error', 'Unknown error'),
    });
    return false;
}

async function initializeVegaRuntime() {
    const runtime = window.OmlorixVegaRuntime;
    if (!runtime || typeof runtime.loadVegaRuntime !== 'function') {
        return null;
    }
    return runtime.loadVegaRuntime();
}

/**
 * Normalize a Vega URL without contacting it. Inline data/blob and same-origin
 * HTTP(S) references stay available without consent; only cross-origin HTTP(S)
 * origins can be approved. Rejecting every other scheme prevents a remembered
 * grant from enabling file:, ftp:, javascript:, or another URL type that the
 * preview must never load.
 */
function normalizeVegaResourceReference(value) {
    const raw = typeof value === 'string' ? value.trim() : '';
    if (!raw) {
        return { kind: 'dynamic', raw: '' };
    }
    if (/^(?:data:|blob:|about:blank(?:[#?]|$)|#)/i.test(raw)) {
        return { kind: 'inline', raw };
    }

    let baseUrl;
    let parsed;
    try {
        baseUrl = document?.baseURI || window?.location?.href || 'http://localhost/';
        parsed = new URL(raw, baseUrl);
    } catch (_) {
        return { kind: 'unsupported', raw, scheme: '' };
    }

    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
        return {
            kind: 'unsupported',
            raw,
            scheme: parsed.protocol.replace(/:$/, ''),
        };
    }
    const currentOrigin = new URL(baseUrl).origin;
    if (parsed.origin === currentOrigin) {
        // Keep the authored reference intact so Vega can load local application
        // assets directly instead of asking for consent or using the proxy.
        return { kind: 'inline', raw };
    }
    return {
        kind: 'external',
        raw,
        href: parsed.href,
        origin: parsed.origin,
        label: parsed.origin,
    };
}

/**
 * Collect literal external references before Vega starts. Signal-generated
 * URLs cannot be known at this stage; the loader catches those later and the
 * same consent panel is shown before a retry is allowed.
 */
function collectVegaExternalResources(spec, path = '$', result = null) {
    const collected = result || { sources: new Map(), unsupported: [] };
    if (!spec || typeof spec !== 'object') {
        return collected;
    }
    if (Array.isArray(spec)) {
        spec.forEach((item, index) => {
            collectVegaExternalResources(item, `${path}[${index}]`, collected);
        });
        return collected;
    }

    Object.entries(spec).forEach(([key, value]) => {
        const nextPath = `${path}.${key}`;
        if (VEGA_RESOURCE_KEYS.has(String(key || '').toLowerCase()) && typeof value === 'string') {
            const normalized = normalizeVegaResourceReference(value);
            if (normalized.kind === 'external' && !collected.sources.has(normalized.origin)) {
                collected.sources.set(normalized.origin, {
                    origin: normalized.origin,
                    label: normalized.label,
                    paths: [nextPath],
                });
            } else if (normalized.kind === 'external') {
                collected.sources.get(normalized.origin).paths.push(nextPath);
            } else if (normalized.kind === 'unsupported') {
                collected.unsupported.push({ path: nextPath, reference: normalized.raw });
            }
        }
        collectVegaExternalResources(value, nextPath, collected);
    });
    return collected;
}

function getVegaExternalResourceSignature(sources) {
    return (Array.isArray(sources) ? sources : [])
        .map((item) => String(item?.origin || '').trim())
        .filter(Boolean)
        .sort()
        .join('|');
}

function getVegaExternalConsentUserKey() {
    // Remembered consent must never leak between accounts that use the same
    // browser profile. If user identity is not ready yet, allow-once still
    // works but the preference is deliberately not persisted.
    return String(window?.chatSetup?.user_id || '').trim();
}

function readSavedVegaExternalConsents() {
    try {
        const parsed = JSON.parse(window.localStorage?.getItem(VEGA_EXTERNAL_CONSENT_STORAGE_KEY) || '{}');
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch (_) {
        return {};
    }
}

function writeSavedVegaExternalConsents(value) {
    try {
        window.localStorage?.setItem(VEGA_EXTERNAL_CONSENT_STORAGE_KEY, JSON.stringify(value || {}));
    } catch (_) {
        // Consent remains valid for the current code block if storage is full
        // or unavailable; remembering it is a convenience, not a dependency.
    }
}

function hasSavedVegaExternalConsent(signature) {
    const userKey = getVegaExternalConsentUserKey();
    if (!userKey) return false;
    const saved = readSavedVegaExternalConsents();
    return Boolean(signature && saved[userKey]?.[signature]);
}

function saveVegaExternalConsent(signature, sources) {
    if (!signature) return;
    const userKey = getVegaExternalConsentUserKey();
    if (!userKey) return;
    const saved = readSavedVegaExternalConsents();
    const userConsents = saved[userKey] && typeof saved[userKey] === 'object'
        ? saved[userKey]
        : {};
    userConsents[signature] = {
        savedAt: new Date().toISOString(),
        origins: sources.map((item) => item.origin),
    };
    saved[userKey] = userConsents;

    const entries = [];
    Object.entries(saved).forEach(([currentUserKey, consents]) => {
        if (!consents || typeof consents !== 'object') return;
        Object.entries(consents).forEach(([currentSignature, meta]) => {
            entries.push({
                userKey: currentUserKey,
                signature: currentSignature,
                savedAt: String(meta?.savedAt || ''),
            });
        });
    });
    if (entries.length > VEGA_MAX_SAVED_EXTERNAL_CONSENTS) {
        entries
            .sort((a, b) => a.savedAt.localeCompare(b.savedAt))
            .slice(0, entries.length - VEGA_MAX_SAVED_EXTERNAL_CONSENTS)
            .forEach((entry) => {
                if (saved[entry.userKey] && typeof saved[entry.userKey] === 'object') {
                    delete saved[entry.userKey][entry.signature];
                }
            });
    }
    writeSavedVegaExternalConsents(saved);
}

function forgetSavedVegaExternalConsent(signature) {
    if (!signature) return;
    const userKey = getVegaExternalConsentUserKey();
    if (!userKey) return;
    const saved = readSavedVegaExternalConsents();
    if (!saved[userKey] || typeof saved[userKey] !== 'object' || !saved[userKey][signature]) {
        return;
    }
    delete saved[userKey][signature];
    writeSavedVegaExternalConsents(saved);
}

function getVegaSessionConsentSignatures(permissionHost) {
    if (!(permissionHost instanceof Element)) {
        return new Set();
    }
    if (!(permissionHost._vegaExternalConsentSignatures instanceof Set)) {
        permissionHost._vegaExternalConsentSignatures = new Set();
    }
    return permissionHost._vegaExternalConsentSignatures;
}

function hasVegaExternalConsent(permissionHost, signature) {
    return Boolean(signature) && (
        getVegaSessionConsentSignatures(permissionHost).has(signature)
        || hasSavedVegaExternalConsent(signature)
    );
}

function grantVegaExternalConsent(permissionHost, signature, sources, remember = false) {
    if (!signature) return;
    getVegaSessionConsentSignatures(permissionHost).add(signature);
    if (remember) {
        saveVegaExternalConsent(signature, sources);
    }
}

function revokeVegaExternalConsent(permissionHost, signature) {
    getVegaSessionConsentSignatures(permissionHost).delete(signature);
    forgetSavedVegaExternalConsent(signature);
}

/**
 * Preserve origins discovered at runtime (for example signal-generated URLs)
 * for the current source revision. A retry must approve the complete set, and
 * editing the specification cannot inherit discoveries from the old source.
 */
function mergeDiscoveredVegaExternalResources(permissionHost, source, sources = []) {
    const staticSources = new Map(sources.map((item) => [item.origin, item]));
    if (!(permissionHost instanceof Element)) {
        return Array.from(staticSources.values()).sort((a, b) => a.label.localeCompare(b.label));
    }
    const sourceHash = hashCodeBlockSource(source);
    if (permissionHost._vegaDiscoveredSourceHash !== sourceHash) {
        permissionHost._vegaDiscoveredSourceHash = sourceHash;
        permissionHost._vegaDiscoveredExternalSources = new Map();
    }
    const discovered = permissionHost._vegaDiscoveredExternalSources;
    if (discovered instanceof Map) {
        discovered.forEach((item, origin) => staticSources.set(origin, item));
    }
    return Array.from(staticSources.values()).sort((a, b) => a.label.localeCompare(b.label));
}

function rememberDiscoveredVegaExternalResource(permissionHost, source, resource) {
    if (!(permissionHost instanceof Element) || resource?.kind !== 'external') {
        return;
    }
    mergeDiscoveredVegaExternalResources(permissionHost, source);
    permissionHost._vegaDiscoveredExternalSources.set(resource.origin, {
        origin: resource.origin,
        label: resource.label,
        paths: [],
    });
}

class VegaExternalResourcePermissionError extends Error {
    constructor(resource) {
        super(formatChatPreviewTranslation(
            'code_block_vega_external_origin_not_approved',
            'External Vega resource is not approved: {origin}',
            { origin: resource?.label || resource?.origin || '' }
        ));
        this.name = 'VegaExternalResourcePermissionError';
        this.resource = resource;
        this.isVegaExternalResourcePermissionError = true;
    }
}

function getVegaThemeConfig() {
    const isDark = String(document?.documentElement?.dataset?.mode || '').toLowerCase() === 'dark';
    if (isDark) {
        return {
            background: 'transparent',
            view: {
                fill: 'transparent',
                stroke: 'rgba(148, 163, 184, 0.28)',
            },
            axis: {
                domainColor: 'rgba(148, 163, 184, 0.46)',
                gridColor: 'rgba(148, 163, 184, 0.18)',
                labelColor: '#e2e8f0',
                tickColor: 'rgba(148, 163, 184, 0.42)',
                titleColor: '#f8fafc',
            },
            legend: {
                labelColor: '#e2e8f0',
                titleColor: '#f8fafc',
            },
            header: {
                labelColor: '#e2e8f0',
                titleColor: '#f8fafc',
            },
            title: {
                color: '#f8fafc',
                subtitleColor: '#cbd5e1',
            },
            style: {
                'guide-label': {
                    fill: '#e2e8f0',
                },
                'guide-title': {
                    fill: '#f8fafc',
                },
            },
        };
    }

    return {
        background: 'transparent',
        view: {
            fill: 'transparent',
            stroke: 'rgba(148, 163, 184, 0.24)',
        },
        axis: {
            domainColor: 'rgba(71, 85, 105, 0.42)',
            gridColor: 'rgba(148, 163, 184, 0.2)',
            labelColor: '#0f172a',
            tickColor: 'rgba(71, 85, 105, 0.36)',
            titleColor: '#020617',
        },
        legend: {
            labelColor: '#0f172a',
            titleColor: '#020617',
        },
        header: {
            labelColor: '#0f172a',
            titleColor: '#020617',
        },
        title: {
            color: '#020617',
            subtitleColor: '#475569',
        },
        style: {
            'guide-label': {
                fill: '#0f172a',
            },
            'guide-title': {
                fill: '#020617',
            },
        },
    };
}

function mergeVegaDefaults(target, defaults) {
    const output = target && typeof target === 'object' && !Array.isArray(target)
        ? target
        : {};

    Object.entries(defaults || {}).forEach(([key, value]) => {
        if (value && typeof value === 'object' && !Array.isArray(value)) {
            output[key] = mergeVegaDefaults(output[key], value);
            return;
        }
        if (output[key] === undefined) {
            output[key] = value;
        }
    });

    return output;
}

function createThemedVegaSpec(spec) {
    const cloned = JSON.parse(JSON.stringify(spec));
    const themeConfig = getVegaThemeConfig();
    if (cloned.background === undefined) {
        cloned.background = themeConfig.background;
    }
    cloned.config = mergeVegaDefaults(cloned.config, {
        view: themeConfig.view,
        axis: themeConfig.axis,
        legend: themeConfig.legend,
        header: themeConfig.header,
        title: themeConfig.title,
        style: themeConfig.style,
    });
    return cloned;
}

function cleanupVegaPreviewTarget(target) {
    if (!(target instanceof Element)) {
        return;
    }
    if (typeof target._vegaPreviewCleanup === 'function') {
        try {
            target._vegaPreviewCleanup();
        } catch (_) {}
    }
    delete target._vegaPreviewCleanup;
}

function updateMountedVegaPreviewState(target, rendered) {
    const surface = target?.closest?.('.vega-preview-surface');
    surface?.classList?.toggle('has-error', !rendered);
    const previewPane = target?.closest?.('.code-block-preview-pane');
    if (previewPane instanceof Element) {
        previewPane.dataset.previewState = rendered ? 'ready' : 'error';
    }
}

function renderVegaExternalResourceBlocked(target) {
    target.innerHTML = `<div class="code-block-preview-status">${escapeHtml(getChatPreviewTranslation(
        'code_block_vega_external_resources_cancelled',
        'External connections remain blocked. Reload the preview to review them again.'
    ))}</div>`;
    updateMountedVegaPreviewState(target, false);
}

/**
 * Render an inline, keyboard-accessible approval surface. It mirrors the MCP
 * Apps consent interaction while keeping Vega grants in their own namespace
 * and binding them to the exact set of requested origins.
 */
function renderVegaExternalResourceConsent(target, source, sources, options = {}) {
    cleanupVegaPreviewTarget(target);
    vegaExternalConsentCounter += 1;
    const titleId = `vega-external-consent-title-${vegaExternalConsentCounter}`;
    const signature = getVegaExternalResourceSignature(sources);

    const panel = document.createElement('div');
    panel.className = 'vega-preview-consent-panel';
    panel.setAttribute('role', 'group');
    panel.setAttribute('aria-labelledby', titleId);

    const title = document.createElement('div');
    title.className = 'vega-preview-consent-title';
    title.id = titleId;
    title.textContent = getChatPreviewTranslation(
        'code_block_vega_external_resources_title',
        'Allow external connections?'
    );

    const description = document.createElement('p');
    description.className = 'vega-preview-consent-description';
    description.textContent = getChatPreviewTranslation(
        'code_block_vega_external_resources_desc',
        'This Vega preview wants to load data or assets from outside Omlorix. Allow it only if you trust these origins.'
    );

    const list = document.createElement('ul');
    list.className = 'vega-preview-consent-origins';
    sources.forEach((item) => {
        const row = document.createElement('li');
        row.textContent = item.label;
        list.appendChild(row);
    });

    const rememberLabel = document.createElement('label');
    rememberLabel.className = 'vega-preview-consent-remember';
    const rememberCheckbox = document.createElement('input');
    rememberCheckbox.type = 'checkbox';
    rememberCheckbox.className = 'vega-preview-consent-remember-checkbox';
    const rememberText = document.createElement('span');
    rememberText.textContent = getChatPreviewTranslation(
        'code_block_vega_external_resources_remember',
        'Remember for my account in this browser and these origins'
    );
    rememberLabel.append(rememberCheckbox, rememberText);
    rememberLabel.hidden = !getVegaExternalConsentUserKey();

    const actions = document.createElement('div');
    actions.className = 'vega-preview-consent-actions';
    const allowButton = document.createElement('button');
    allowButton.type = 'button';
    allowButton.className = 'vega-preview-consent-btn vega-preview-consent-btn-primary';
    allowButton.textContent = getChatPreviewTranslation(
        'code_block_vega_external_resources_allow',
        'Allow connections'
    );
    const blockButton = document.createElement('button');
    blockButton.type = 'button';
    blockButton.className = 'vega-preview-consent-btn';
    blockButton.textContent = getChatPreviewTranslation(
        'code_block_vega_external_resources_block',
        'Keep blocked'
    );

    allowButton.addEventListener('click', async () => {
        allowButton.disabled = true;
        blockButton.disabled = true;
        grantVegaExternalConsent(
            options.permissionHost,
            signature,
            sources,
            rememberCheckbox.checked
        );
        syncVegaExternalResourceControl(options.permissionHost, source, sources);
        const rendered = await renderVegaPreview(target, source, options);
        updateMountedVegaPreviewState(target, rendered);
    });
    blockButton.addEventListener('click', () => {
        renderVegaExternalResourceBlocked(target);
        syncVegaExternalResourceControl(options.permissionHost, source, sources);
    });

    actions.append(allowButton, blockButton);
    panel.append(title, description, list, rememberLabel, actions);
    target.replaceChildren(panel);
    updateMountedVegaPreviewState(target, false);
    syncVegaExternalResourceControl(options.permissionHost, source, sources);
    // The safe action receives initial focus so an immediate Enter or Space
    // keeps external connections blocked instead of granting them.
    blockButton.focus?.();
}

/**
 * Create a Vega loader whose sanitizer and fetch path both enforce the exact
 * approved-origin set. Approved data is fetched through the authenticated
 * Omlorix endpoint because the application CSP intentionally never grants the
 * main page arbitrary connect-src access. The server applies the outbound
 * network policy, DNS-rebinding protection, redirect rejection, and size cap.
 */
function createVegaPreviewLoader(vegaApi, approvedOrigins = new Set()) {
    if (!vegaApi || typeof vegaApi.loader !== 'function') {
        return null;
    }
    try {
        const loader = vegaApi.loader({
            mode: 'http',
            defaultProtocol: window?.location?.protocol === 'https:' ? 'https' : 'http',
            http: {
                credentials: 'omit',
                redirect: 'error',
                referrerPolicy: 'no-referrer',
            },
        });
        if (!loader || typeof loader.load !== 'function' || typeof loader.sanitize !== 'function') {
            return null;
        }
        const originalSanitize = loader.sanitize.bind(loader);
        const originalHttp = typeof loader.http === 'function' ? loader.http.bind(loader) : null;

        loader.sanitize = async function approvedSanitize(uri, loaderOptions = {}) {
            const sanitized = await originalSanitize(uri, loaderOptions);
            const resource = normalizeVegaResourceReference(sanitized?.href || uri);
            if (resource.kind === 'unsupported') {
                throw new Error(formatChatPreviewTranslation(
                    'code_block_vega_external_scheme_unsupported',
                    'This Vega preview cannot load the resource at {path}. Only HTTP and HTTPS external resources are supported.',
                    { path: resource.raw || String(uri || '') }
                ));
            }
            if (resource.kind === 'external' && !approvedOrigins.has(resource.origin)) {
                throw new VegaExternalResourcePermissionError(resource);
            }
            if (loaderOptions?.context === 'image' && resource.kind === 'external') {
                sanitized.crossOrigin = 'anonymous';
            }
            return sanitized;
        };

        if (typeof window.authedFetch === 'function' || typeof window.fetch === 'function') {
            loader.http = async function approvedHttp(uri, loaderOptions = {}) {
                const resource = normalizeVegaResourceReference(uri);
                if (resource.kind === 'inline' && originalHttp) {
                    return originalHttp(uri, loaderOptions);
                }
                if (resource.kind === 'external' && !approvedOrigins.has(resource.origin)) {
                    throw new VegaExternalResourcePermissionError(resource);
                }
                const {
                    response: responseType = 'text',
                    signal: upstreamSignal,
                } = loaderOptions || {};
                const controller = new AbortController();
                const abortRequest = () => controller.abort();
                if (upstreamSignal?.aborted) {
                    abortRequest();
                } else {
                    upstreamSignal?.addEventListener?.('abort', abortRequest, { once: true });
                }
                const timeoutId = setTimeout(abortRequest, VEGA_EXTERNAL_REQUEST_TIMEOUT_MS);
                let response;
                try {
                    const request = typeof window.authedFetch === 'function'
                        ? window.authedFetch.bind(window)
                        : window.fetch.bind(window);
                    response = await request(VEGA_EXTERNAL_RESOURCE_ENDPOINT, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: resource.href }),
                        signal: controller.signal,
                        credentials: 'same-origin',
                    });
                } catch (error) {
                    if (controller.signal.aborted && !upstreamSignal?.aborted) {
                        throw new Error(getChatPreviewTranslation(
                            'code_block_vega_external_fetch_timeout',
                            'The external Vega resource request timed out.'
                        ));
                    }
                    throw error;
                } finally {
                    clearTimeout(timeoutId);
                    upstreamSignal?.removeEventListener?.('abort', abortRequest);
                }
                if (!response.ok) {
                    if (response.status === 403) {
                        throw new Error(getChatPreviewTranslation(
                            'code_block_vega_external_fetch_blocked',
                            'The server network policy blocked this external Vega resource.'
                        ));
                    }
                    if (response.status === 413) {
                        throw new Error(getChatPreviewTranslation(
                            'code_block_vega_external_resource_too_large',
                            'The external Vega resource is too large to render safely.'
                        ));
                    }
                    if (response.status === 504) {
                        throw new Error(getChatPreviewTranslation(
                            'code_block_vega_external_fetch_timeout',
                            'The external Vega resource request timed out.'
                        ));
                    }
                    throw new Error(formatChatPreviewTranslation(
                        'code_block_vega_external_fetch_failed',
                        'The external Vega resource could not be loaded (HTTP {status}).',
                        { status: response.status }
                    ));
                }
                const declaredLength = Number(response.headers.get('content-length') || 0);
                if (declaredLength > VEGA_EXTERNAL_RESPONSE_MAX_LENGTH) {
                    throw new Error(getChatPreviewTranslation(
                        'code_block_vega_external_resource_too_large',
                        'The external Vega resource is too large to render safely.'
                    ));
                }
                let payload;
                if (typeof response.body?.getReader === 'function') {
                    const reader = response.body.getReader();
                    const chunks = [];
                    let receivedLength = 0;
                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        receivedLength += value.byteLength;
                        if (receivedLength > VEGA_EXTERNAL_RESPONSE_MAX_LENGTH) {
                            await reader.cancel();
                            throw new Error(getChatPreviewTranslation(
                                'code_block_vega_external_resource_too_large',
                                'The external Vega resource is too large to render safely.'
                            ));
                        }
                        chunks.push(value);
                    }
                    const combined = new Uint8Array(receivedLength);
                    let offset = 0;
                    chunks.forEach((chunk) => {
                        combined.set(chunk, offset);
                        offset += chunk.byteLength;
                    });
                    payload = combined.buffer;
                } else {
                    payload = await response.arrayBuffer();
                }
                if (payload.byteLength > VEGA_EXTERNAL_RESPONSE_MAX_LENGTH) {
                    throw new Error(getChatPreviewTranslation(
                        'code_block_vega_external_resource_too_large',
                        'The external Vega resource is too large to render safely.'
                    ));
                }
                if (responseType === 'arrayBuffer') {
                    return payload;
                }
                if (responseType === 'blob') {
                    return new Blob([payload], {
                        type: response.headers.get('content-type') || '',
                    });
                }
                const text = new TextDecoder().decode(payload);
                if (responseType === 'json') {
                    return JSON.parse(text);
                }
                return text;
            };
        };
        return loader;
    } catch (_) {
        return null;
    }
}

async function renderVegaPreview(target, source, options = {}) {
    if (!(target instanceof Element)) {
        return false;
    }

    cleanupVegaPreviewTarget(target);

    let spec;
    try {
        spec = parseVegaPreviewSpec(source);
    } catch (error) {
        target.innerHTML = `<div class="code-block-preview-status">${escapeHtml(error.message || getChatPreviewTranslation('code_block_vega_preview_unavailable', 'Vega preview is unavailable for this block.'))}</div>`;
        return false;
    }

    const previewKind = options.previewKind || inferVegaPreviewKindFromSpec(spec) || 'vega-lite';
    const collected = collectVegaExternalResources(spec);
    if (collected.unsupported.length) {
        const blocked = collected.unsupported[0];
        target.innerHTML = `<div class="code-block-preview-status">${escapeHtml(formatChatPreviewTranslation(
            'code_block_vega_external_scheme_unsupported',
            'This Vega preview cannot load the resource at {path}. Only HTTP and HTTPS external resources are supported.',
            { path: blocked.path }
        ))}</div>`;
        return false;
    }
    let externalSources = mergeDiscoveredVegaExternalResources(
        options.permissionHost,
        source,
        Array.from(collected.sources.values())
    );
    let externalSignature = getVegaExternalResourceSignature(externalSources);
    if (externalSources.length && !hasVegaExternalConsent(options.permissionHost, externalSignature)) {
        renderVegaExternalResourceConsent(target, source, externalSources, options);
        return false;
    }
    const approvedOrigins = new Set(externalSources.map((item) => item.origin));

    let runtime = null;
    try {
        runtime = await initializeVegaRuntime();
    } catch (_) {
        runtime = null;
    }

    const vegaEmbed = runtime?.vegaEmbed || window.vegaEmbed;
    const vegaApi = runtime?.vega || window.vega;
    if (typeof vegaEmbed !== 'function') {
        target.innerHTML = `<div class="code-block-preview-status">${escapeHtml(getChatPreviewTranslation('code_block_vega_renderer_unavailable', 'Vega renderer is unavailable.'))}</div>`;
        return false;
    }

    await waitForPreviewRenderReady(target, 16);

    const mount = document.createElement('div');
    mount.className = 'vega-preview-embed';
    target.innerHTML = '';
    target.appendChild(mount);

    try {
        const embedResult = await vegaEmbed(
            mount,
            createThemedVegaSpec(spec),
            {
                actions: false,
                // Parse expressions into an AST so Vega-Embed uses its bundled
                // interpreter instead of compiling strings with Function(). This
                // keeps previews compatible with the application's strict CSP,
                // which intentionally does not grant script-src 'unsafe-eval'.
                ast: true,
                renderer: 'svg',
                mode: previewKind === 'vega' ? 'vega' : 'vega-lite',
                loader: createVegaPreviewLoader(vegaApi, approvedOrigins) || undefined,
            }
        );

        let resizeObserver = null;
        let resizeTask = null;
        if (typeof ResizeObserver === 'function' && embedResult?.view && typeof embedResult.view.resize === 'function') {
            const scheduleResize = typeof requestAnimationFrame === 'function'
                ? requestAnimationFrame
                : (callback) => setTimeout(callback, 16);
            resizeObserver = new ResizeObserver(() => {
                // Resize outside the observer delivery cycle. Vega may change
                // the SVG bounds while resizing, and doing that synchronously
                // here causes Safari's undelivered-notification loop warning.
                if (resizeTask !== null) return;
                resizeTask = scheduleResize(() => {
                    resizeTask = null;
                    if (target.isConnected) {
                        embedResult.view.resize().runAsync().catch(() => {});
                    }
                });
            });
            resizeObserver.observe(target);
        }

        target._vegaPreviewCleanup = () => {
            if (resizeObserver) {
                resizeObserver.disconnect();
            }
            if (resizeTask !== null) {
                if (typeof cancelAnimationFrame === 'function') {
                    cancelAnimationFrame(resizeTask);
                } else {
                    clearTimeout(resizeTask);
                }
                resizeTask = null;
            }
            if (typeof embedResult?.finalize === 'function') {
                embedResult.finalize();
            }
        };
        return true;
    } catch (error) {
        cleanupVegaPreviewTarget(target);
        if (error?.isVegaExternalResourcePermissionError && error.resource?.kind === 'external') {
            rememberDiscoveredVegaExternalResource(options.permissionHost, source, error.resource);
            externalSources = mergeDiscoveredVegaExternalResources(
                options.permissionHost,
                source,
                externalSources
            );
            externalSignature = getVegaExternalResourceSignature(externalSources);
            if (hasVegaExternalConsent(options.permissionHost, externalSignature)) {
                // A remembered grant can already cover a URL discovered only
                // during rendering. Re-run once with the expanded signature;
                // the marker prevents an unbounded discovery/retry cycle.
                if (!options.retriedAfterDiscovery) {
                    return renderVegaPreview(target, source, {
                        ...options,
                        retriedAfterDiscovery: true,
                    });
                }
            } else {
                renderVegaExternalResourceConsent(target, source, externalSources, options);
                return false;
            }
        }
        target.innerHTML = `<div class="code-block-preview-status">${escapeHtml(formatChatPreviewTranslation('code_block_preview_render_error', '{label} render error: {message}', {
            label: getCodePreviewLabel(previewKind),
            message: error?.message || getChatPreviewTranslation('common_unknown_error', 'Unknown error'),
        }))}</div>`;
        return false;
    }
}

