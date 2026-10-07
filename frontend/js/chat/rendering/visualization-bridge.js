function findVisualizationFrameForMessage(event, previewId) {
    return Array.from(document.querySelectorAll('.visualizer-preview-frame'))
        .find((frame) => frame instanceof HTMLIFrameElement
            && frame.dataset.previewFrameId === previewId
            && event.source === frame.contentWindow) || null;
}

function getVisualizationFrameCapabilities(frame) {
    try {
        const parsed = JSON.parse(frame?.dataset?.visualizationCapabilities || '{}');
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_) {
        return {};
    }
}

function postVisualizationHostResponse(frame, previewId, requestId, ok, result = null, error = '') {
    frame?.contentWindow?.postMessage({
        type: VISUALIZATION_HOST_RESPONSE_MESSAGE_TYPE,
        previewId,
        requestId,
        ok: Boolean(ok),
        result,
        error: String(error || ''),
    }, window.location.origin);
}

async function handleVisualizationHostRequest(event, data) {
    const previewId = String(data?.previewId || '');
    const requestId = String(data?.requestId || '');
    const action = String(data?.action || '').trim().toLowerCase();
    const payload = data?.payload && typeof data.payload === 'object' ? data.payload : {};
    const frame = findVisualizationFrameForMessage(event, previewId);
    if (!frame || !requestId) return;
    const capabilities = getVisualizationFrameCapabilities(frame);

    try {
        const surface = visualizationSurfaces.get(frame);
        if (action === 'save-state' || action === 'save-design') {
            const result = await surface?.saveState(action === 'save-state' ? { widgetState: payload.widgetState } : { design: payload.design });
            postVisualizationHostResponse(frame, previewId, requestId, true, result);
            return;
        }
        if (action === 'design-open') {
            surface?.designOpen(payload.open === true);
            postVisualizationHostResponse(frame, previewId, requestId, true, {});
            return;
        }
        if (action === 'design-available') {
            surface?.designAvailable();
            postVisualizationHostResponse(frame, previewId, requestId, true, {});
            return;
        }
        if (action === 'send-follow-up') {
            if (capabilities.chat_followup !== true || typeof sendMessage !== 'function') {
                throw new Error(getChatPreviewTranslation(
                    'visualization_followup_unavailable',
                    'This visualization cannot continue the chat.'
                ));
            }
            const prompt = String(payload.prompt || '').trim();
            const title = String(payload.title || '').trim().slice(0, 250);
            if (!prompt || prompt.length > VISUALIZATION_MAX_FOLLOWUP_LENGTH) {
                throw new Error(getChatPreviewTranslation(
                    'visualization_followup_invalid',
                    'The proposed follow-up message is empty or too long.'
                ));
            }
            const confirmed = typeof window.showWarningConfirm === 'function'
                && await window.showWarningConfirm({
                    title: title || getChatPreviewTranslation('visualization_followup_title', 'Continue with this selection?'),
                    message: getChatPreviewTranslation(
                        'visualization_followup_desc',
                        'The visualization prepared this message. Review it before sending.'
                    ),
                    copyText: prompt,
                    confirmLabel: getChatPreviewTranslation('visualization_followup_send', 'Send message'),
                    cancelLabel: getChatPreviewTranslation('common_cancel', 'Cancel'),
                    danger: false,
                    variant: 'info',
                });
            if (!confirmed) {
                postVisualizationHostResponse(frame, previewId, requestId, true, { sent: false });
                return;
            }
            await sendMessage(prompt, false, null);
            postVisualizationHostResponse(frame, previewId, requestId, true, { sent: true });
            return;
        }

        if (action === 'external-data') {
            if (capabilities.external_data !== true) {
                throw new Error(getChatPreviewTranslation(
                    'visualization_external_data_unavailable',
                    'This visualization did not request external-data access.'
                ));
            }
            const resource = normalizeVegaResourceReference(String(payload.url || ''));
            if (resource.kind !== 'external') {
                throw new Error(getChatPreviewTranslation(
                    'visualization_external_data_invalid_url',
                    'Only public HTTP and HTTPS URLs can be requested.'
                ));
            }
            const confirmed = typeof window.showWarningConfirm === 'function'
                && await window.showWarningConfirm({
                    title: getChatPreviewTranslation('visualization_external_data_title', 'Load external visualization data?'),
                    message: formatChatPreviewTranslation(
                        'visualization_external_data_desc',
                        'Allow this visualization to request data from {origin}?',
                        { origin: resource.origin }
                    ),
                    confirmLabel: getChatPreviewTranslation('visualization_external_data_allow', 'Load data'),
                    cancelLabel: getChatPreviewTranslation('visualization_external_data_block', 'Keep blocked'),
                    danger: false,
                    variant: 'warning',
                });
            if (!confirmed) {
                throw new Error(getChatPreviewTranslation(
                    'visualization_external_data_cancelled',
                    'External data access was not approved.'
                ));
            }
            const request = typeof window.authedFetch === 'function'
                ? window.authedFetch.bind(window)
                : window.fetch.bind(window);
            const response = await request(VEGA_EXTERNAL_RESOURCE_ENDPOINT, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: resource.href }),
                credentials: 'same-origin',
            });
            if (!response.ok) {
                throw new Error(formatChatPreviewTranslation(
                    'visualization_external_data_failed',
                    'The external data request failed (HTTP {status}).',
                    { status: response.status }
                ));
            }
            const declaredLength = Number(response.headers.get('content-length'));
            if (Number.isFinite(declaredLength) && declaredLength > VEGA_EXTERNAL_RESPONSE_MAX_LENGTH) {
                throw new Error(getChatPreviewTranslation(
                    'code_block_vega_external_resource_too_large',
                    'The external resource is too large to render safely.'
                ));
            }
            const content = await response.text();
            if (new TextEncoder().encode(content).byteLength > VEGA_EXTERNAL_RESPONSE_MAX_LENGTH) {
                throw new Error(getChatPreviewTranslation(
                    'code_block_vega_external_resource_too_large',
                    'The external resource is too large to render safely.'
                ));
            }
            postVisualizationHostResponse(frame, previewId, requestId, true, {
                content,
                contentType: response.headers.get('content-type') || 'text/plain',
                url: resource.href,
            });
            return;
        }

        if (action === 'download') {
            if (capabilities.download !== true) {
                throw new Error(getChatPreviewTranslation(
                    'visualization_download_unavailable',
                    'This visualization did not request download access.'
                ));
            }
            const filename = String(payload.filename || 'visualization-data.txt')
                .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '-')
                .slice(0, 180) || 'visualization-data.txt';
            const content = String(payload.content || '');
            const blob = new Blob([content], { type: String(payload.mimeType || 'text/plain') });
            if (blob.size > VISUALIZATION_MAX_DOWNLOAD_BYTES) {
                throw new Error(getChatPreviewTranslation(
                    'visualization_download_too_large',
                    'The visualization download is too large.'
                ));
            }
            const confirmed = typeof window.showWarningConfirm === 'function'
                && await window.showWarningConfirm({
                    title: getChatPreviewTranslation('visualization_download_title', 'Download visualization file?'),
                    message: formatChatPreviewTranslation(
                        'visualization_download_desc',
                        'Allow this visualization to download {filename}?',
                        { filename }
                    ),
                    confirmLabel: getChatPreviewTranslation('visualization_download_allow', 'Download file'),
                    cancelLabel: getChatPreviewTranslation('common_cancel', 'Cancel'),
                    danger: false,
                    variant: 'info',
                });
            if (!confirmed) {
                postVisualizationHostResponse(frame, previewId, requestId, true, { downloaded: false });
                return;
            }
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 0);
            postVisualizationHostResponse(frame, previewId, requestId, true, { downloaded: true });
            return;
        }

        throw new Error(getChatPreviewTranslation('visualization_action_unsupported', 'Unsupported visualization action.'));
    } catch (error) {
        postVisualizationHostResponse(frame, previewId, requestId, false, null, error?.message || error);
    }
}

function broadcastVisualizationTheme() {
    const tokens = getVisualizerThemeTokenMap();
    const mode = getPreviewThemeMode();
    document.querySelectorAll('.visualizer-preview-frame').forEach((frame) => {
        if (!(frame instanceof HTMLIFrameElement) || !frame.dataset.previewFrameId) return;
        frame.contentWindow?.postMessage({
            type: VISUALIZATION_THEME_MESSAGE_TYPE,
            previewId: frame.dataset.previewFrameId,
            mode,
            tokens,
            expanded: Boolean(frame.closest('.is-expanded')),
        }, window.location.origin);
    });
}

function ensureVisualizationThemeObserver() {
    if (visualizationThemeObserverInitialized || typeof MutationObserver === 'undefined') return;
    const root = document.documentElement;
    if (!(root instanceof Element)) return;
    const observer = new MutationObserver((mutations) => {
        if (mutations.some((mutation) => mutation.attributeName === 'data-mode')) {
            broadcastVisualizationTheme();
        }
    });
    observer.observe(root, { attributes: true, attributeFilter: ['data-mode'] });
    visualizationThemeObserverInitialized = true;
}

function ensureCodeBlockPreviewMessageListener() {
    if (codeBlockPreviewMessageListenerInitialized || typeof window === 'undefined') {
        return;
    }
    window.addEventListener('message', (event) => {
        const data = event?.data;
        if (data?.type === VISUALIZATION_STATUS_MESSAGE_TYPE || data?.type === VISUALIZATION_KEY_MESSAGE_TYPE) {
            const frame = findVisualizationFrameForMessage(event, String(data.previewId || ''));
            const controller = frame && visualizationSurfaces.get(frame);
            if (!controller) return;
            if (data.type === VISUALIZATION_STATUS_MESSAGE_TYPE) controller.status(data.state);
            else if (data.key === 'Escape') controller.collapse();
            else if (data.key === 'Tab') controller.moveFocus?.(data.backwards === true);
            return;
        }
        if (data?.type === VISUALIZATION_HOST_REQUEST_MESSAGE_TYPE) {
            void handleVisualizationHostRequest(event, data);
            return;
        }
        if (!data || data.type !== CODE_BLOCK_HTML_PREVIEW_MESSAGE_TYPE) {
            return;
        }
        const previewId = String(data.previewId || '');
        const height = Number(data.height);
        if (!previewId || !Number.isFinite(height)) {
            return;
        }
        document.querySelectorAll('.code-block-html-preview-frame, .visualizer-preview-frame').forEach((frame) => {
            if (!(frame instanceof HTMLIFrameElement)) {
                return;
            }
            if (frame.dataset.previewFrameId !== previewId) {
                return;
            }
            // A matching identifier is not sufficient because arbitrary child
            // frames can post messages to the page. Accept size updates only
            // from the iframe whose height will be changed.
            if (event.source !== frame.contentWindow) {
                return;
            }
            const minHeight = Math.max(120, Number(frame.dataset.previewMinHeight || 220));
            const maxHeight = Math.max(minHeight, Number(frame.dataset.previewMaxHeight || 720));
            const nextHeight = Math.max(minHeight, Math.min(Math.round(height), maxHeight));
            frame.style.height = `${nextHeight}px`;
        });
    });
    codeBlockPreviewMessageListenerInitialized = true;
    document.addEventListener('keydown', (event) => activeVisualizationSurface?.keydown(event));
    ensureVisualizationThemeObserver();
    const flushState = () => { for (const store of visualizationStateStores) void store.flush().catch(() => {}); };
    window.addEventListener('pagehide', flushState);
    document.addEventListener('visibilitychange', () => { if (document.hidden) flushState(); });
}

function getPreviewThemeMode() {
    const root = document?.documentElement;
    const mode = String(root?.dataset?.mode || '').trim().toLowerCase();
    return mode === 'dark' ? 'dark' : 'light';
}

function capturePreviewThemeTokens() {
    if (typeof window === 'undefined' || typeof window.getComputedStyle !== 'function') {
        return {};
    }
    const root = document?.documentElement;
    if (!(root instanceof Element)) {
        return {};
    }
    const styles = window.getComputedStyle(root);
    const keys = [
        '--background',
        '--surface-secondary',
        '--input-bg',
        '--hover',
        '--text-color',
        '--text-color-secondary',
        '--text-color-tertiary',
        '--border-color',
        '--primary-color',
        '--primary-color-hover',
        '--accent-color',
        '--chat-info-color', '--code-token-number', '--success-color', '--error-color',
        '--code-token-operator', '--warning-color', '--surface-muted', '--app-font-family',
    ];
    const snapshot = {};
    keys.forEach((key) => {
        const value = styles.getPropertyValue(key);
        if (value && value.trim()) {
            snapshot[key] = value.trim();
        }
    });
    return snapshot;
}

/**
 * Build the stable theme contract exposed to authored visualizations. Values
 * are captured from Omlorix instead of allowing the opaque iframe to infer the
 * outer theme, and the same map is sent again whenever the application theme
 * changes.
 */
function getVisualizerThemeTokenMap() {
    const tokens = capturePreviewThemeTokens();
    const background = tokens['--background'] || '#ffffff';
    const foreground = tokens['--text-color'] || '#0f172a';
    const mutedForeground = tokens['--text-color-secondary'] || '#475569';
    const card = tokens['--surface-muted'] || tokens['--input-bg'] || background;
    const border = tokens['--border-color'] || 'rgba(148, 163, 184, 0.32)';
    const primary = tokens['--primary-color'] || tokens['--accent-color'] || '#2563eb';
    const accent = tokens['--hover'] || tokens['--surface-secondary'] || card;
    return {
        '--background': background,
        '--foreground': foreground,
        '--card': card,
        '--card-foreground': foreground,
        '--popover': card,
        '--popover-foreground': foreground,
        '--primary': foreground,
        '--primary-foreground': background,
        '--secondary': tokens['--input-bg'] || card,
        '--secondary-foreground': foreground,
        '--muted': tokens['--surface-secondary'] || card,
        '--muted-foreground': mutedForeground,
        '--accent': accent,
        '--accent-foreground': foreground,
        '--destructive': tokens['--error-color'] || primary,
        '--border': border,
        '--input': border,
        '--ring': primary,
        '--blue': tokens['--chat-info-color'] || primary,
        '--orange': tokens['--code-token-number'] || primary,
        '--green': tokens['--success-color'] || primary,
        '--red': tokens['--error-color'] || primary,
        '--purple': tokens['--code-token-operator'] || primary,
        '--yellow': tokens['--warning-color'] || primary,
        '--viz-series-1': 'var(--blue)',
        '--viz-series-2': 'var(--orange)',
        '--viz-series-3': 'var(--green)',
        '--viz-series-4': 'var(--red)',
        '--viz-series-5': 'var(--purple)',
        '--viz-series-6': 'var(--yellow)',
        '--font-size-base': '15px',
        '--font-family': tokens['--app-font-family'] || 'system-ui, sans-serif',
    };
}

function buildVisualizerThemeCssVariables() {
    return Object.entries(getVisualizerThemeTokenMap())
        .map(([key, value]) => `${key}:${value};`)
        .join('');
}

async function loadVisualizerRuntimeAssets(includeLibraries = false, source = '') {
    const fetchAsset = async (name, path) => {
            const response = await window.fetch(path, { credentials: 'same-origin', cache: 'force-cache' });
            if (!response.ok) {
                throw new Error(`Unable to load visualization runtime asset: ${name}`);
            }
            return [name, await response.text()];
    };
    if (!visualizationRuntimeCssPromise) {
        visualizationRuntimeCssPromise = fetchAsset('css', VISUALIZATION_RUNTIME_ASSET_PATHS.css)
            .then(([, css]) => css)
            .catch((error) => {
                visualizationRuntimeCssPromise = null;
                throw error;
            });
    }
    const css = await visualizationRuntimeCssPromise;
    if (!includeLibraries) {
        return { css };
    }
    const requested = Object.entries(VISUALIZATION_RUNTIME_ASSET_PATHS).filter(([name]) =>
        name !== 'css' && (name === 'controls' || String(source).includes(name) || (name === 'lucide' && String(source).includes('data-lucide')))
    );
    const libraries = await Promise.all(requested.map(([name, path]) => {
        if (!visualizationRuntimeLibraryPromises.has(name)) {
            visualizationRuntimeLibraryPromises.set(name, fetchAsset(name, path).catch((error) => {
                visualizationRuntimeLibraryPromises.delete(name);
                throw error;
            }));
        }
        return visualizationRuntimeLibraryPromises.get(name);
    }));
    return { css, ...Object.fromEntries(libraries) };
}

function normalizeVisualizationCapabilitiesForSurface(capabilities) {
    const raw = capabilities && typeof capabilities === 'object' ? capabilities : {};
    const isReadOnlyShare = document?.body?.dataset?.page === 'chat-share';
    return {
        scripts: raw.scripts !== false,
        // Shared transcripts stay fully interactive with embedded data, while
        // host actions that require an authenticated mutable chat are removed.
        external_data: !isReadOnlyShare && raw.external_data === true,
        chat_followup: !isReadOnlyShare && raw.chat_followup === true,
        download: raw.download === true,
    };
}

function getChatPreviewTranslation(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function getCodeBlockActionA11yAttrs(key, fallback) {
    const label = escapeHtml(getChatPreviewTranslation(key, fallback));
    return `title="${label}" aria-label="${label}" data-i18n-attr="aria-label:${key};title:${key}"`;
}

function setCodeBlockActionButtonLabel(button, key, fallback) {
    if (!(button instanceof Element)) {
        return;
    }
    const label = getChatPreviewTranslation(key, fallback);
    button.title = label;
    button.setAttribute('aria-label', label);
    button.setAttribute('data-i18n-attr', `aria-label:${key};title:${key}`);
}

/**
 * Reset opt-in HTML preview permissions. Editing the source calls this helper
 * so newly introduced code or remote URLs can never inherit an earlier grant.
 */
function resetHtmlPreviewPermissions(wrapper) {
    if (!(wrapper instanceof Element) || wrapper.dataset.previewKind !== 'html') {
        return;
    }
    wrapper.dataset.htmlPreviewScripts = 'false';
    wrapper.dataset.htmlPreviewExternalContent = 'false';
}

/**
 * Read the two independent HTML preview grants stored on a code block.
 */
function getHtmlPreviewPermissions(wrapper) {
    return {
        allowScripts: wrapper instanceof Element && wrapper.dataset.htmlPreviewScripts === 'true',
        allowExternalContent: wrapper instanceof Element && wrapper.dataset.htmlPreviewExternalContent === 'true',
    };
}

/** Return the enabled switches in one HTML preview settings menu. */
function getHtmlPreviewSettingsMenuItems(wrapper) {
    if (!(wrapper instanceof Element)) {
        return [];
    }
    return Array.from(wrapper.querySelectorAll('.html-preview-capability-toggle'))
        .filter((toggle) => toggle instanceof HTMLInputElement && !toggle.disabled);
}

/**
 * Open or close one code block's HTML settings menu. Because code blocks are
 * rendered dynamically, all state is derived from the wrapper instead of
 * retaining element references that could become stale after a re-render.
 */
function setHtmlPreviewSettingsMenuOpen(wrapper, isOpen, options = {}) {
    if (!(wrapper instanceof Element)) {
        return;
    }
    const settings = wrapper.querySelector('.code-block-html-settings');
    const trigger = wrapper.querySelector('.code-block-html-settings-trigger');
    const menu = wrapper.querySelector('.code-block-html-settings-menu');
    if (!(settings instanceof Element)
        || !(trigger instanceof HTMLButtonElement)
        || !(menu instanceof Element)) {
        return;
    }

    const shouldOpen = Boolean(isOpen)
        && !trigger.disabled
        && wrapper.dataset.activeView === 'preview';
    if (shouldOpen) {
        // Only one floating settings menu should be open at a time. Closing
        // peers also prevents menus in older chat messages from overlapping.
        document.querySelectorAll('.code-block-html-settings.is-open').forEach((openSettings) => {
            const openWrapper = openSettings.closest('.code-block-wrapper');
            if (openWrapper instanceof Element && openWrapper !== wrapper) {
                setHtmlPreviewSettingsMenuOpen(openWrapper, false);
            }
        });
    }

    menu.hidden = !shouldOpen;
    settings.classList.toggle('is-open', shouldOpen);
    trigger.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');

    if (!shouldOpen) {
        if (options.restoreFocus === true) {
            trigger.focus({ preventScroll: true });
        }
        return;
    }

    if (options.focus === 'first' || options.focus === 'last') {
        requestAnimationFrame(() => {
            const items = getHtmlPreviewSettingsMenuItems(wrapper);
            const target = options.focus === 'last' ? items.at(-1) : items[0];
            target?.focus({ preventScroll: true });
        });
    }
}

/** Keep a permission switch and its disabled row styling synchronized. */
function updateHtmlPreviewCapabilityToggle(toggle, options = {}) {
    if (!(toggle instanceof HTMLInputElement)) {
        return;
    }
    const available = options.available === true;
    const controlsDisabled = options.controlsDisabled === true;
    toggle.checked = available && options.enabled === true;
    toggle.disabled = controlsDisabled || !available;
    toggle.closest('.code-block-html-settings-menu-item')
        ?.classList.toggle('is-disabled', toggle.disabled);
}

/**
 * Reflect the active HTML grants and disable settings that the current source
 * cannot use. Both rows remain visible so the menu stays stable like Canvas.
 */
function syncHtmlPreviewCapabilityControls(wrapper, capabilities = null) {
    if (!(wrapper instanceof Element) || wrapper.dataset.previewKind !== 'html') {
        return;
    }
    const detected = capabilities || analyzeHtmlPreviewCapabilities(getCodeBlockSource(wrapper));
    const permissions = getHtmlPreviewPermissions(wrapper);
    const controlsDisabled = isCodeBlockPreviewToggleDisabled(wrapper);
    const scriptsToggle = wrapper.querySelector('.html-preview-scripts-toggle');
    const externalContentToggle = wrapper.querySelector('.html-preview-external-content-toggle');

    updateHtmlPreviewCapabilityToggle(scriptsToggle, {
        // A sandboxed iframe may always navigate itself. Keep interactions
        // unavailable until the viewer explicitly accepts that network risk.
        available: detected.scripts && permissions.allowExternalContent,
        enabled: permissions.allowScripts,
        controlsDisabled,
    });
    updateHtmlPreviewCapabilityToggle(externalContentToggle, {
        available: detected.externalContent,
        enabled: permissions.allowExternalContent,
        controlsDisabled,
    });
}

function getVegaExternalSourcesForWrapper(wrapper, source = '') {
    if (!(wrapper instanceof Element) || !['vega', 'vega-lite'].includes(wrapper.dataset.previewKind)) {
        return [];
    }
    let spec;
    try {
        spec = parseVegaPreviewSpec(source || getCodeBlockSource(wrapper));
    } catch (_) {
        return [];
    }
    const collected = collectVegaExternalResources(spec);
    return mergeDiscoveredVegaExternalResources(
        wrapper,
        source || getCodeBlockSource(wrapper),
        Array.from(collected.sources.values())
    );
}

/**
 * Keep the Vega permission control visible whenever the current source has a
 * known external origin. It is an action button: its label describes whether
 * activating it opens the review flow or revokes the current permission.
 */
function syncVegaExternalResourceControl(wrapper, source = '', knownSources = null) {
    if (!(wrapper instanceof Element) || !['vega', 'vega-lite'].includes(wrapper.dataset.previewKind)) {
        return;
    }
    const button = wrapper.querySelector('.vega-preview-external-resources-btn');
    if (!(button instanceof HTMLButtonElement)) {
        return;
    }
    const sources = Array.isArray(knownSources)
        ? knownSources
        : getVegaExternalSourcesForWrapper(wrapper, source);
    const signature = getVegaExternalResourceSignature(sources);
    const visible = sources.length > 0;
    const enabled = visible && hasVegaExternalConsent(wrapper, signature);
    const key = enabled
        ? 'code_block_vega_external_resources_revoke'
        : 'code_block_vega_external_resources_review';
    const fallback = enabled ? 'Block external connections' : 'Review external connections';
    const label = getChatPreviewTranslation(key, fallback);

    button.hidden = !visible;
    button.setAttribute('aria-hidden', visible ? 'false' : 'true');
    button.removeAttribute('aria-pressed');
    button.classList.toggle('is-active', enabled);
    button.dataset.externalResourceSignature = signature;
    button.title = label;
    button.setAttribute('aria-label', label);
    button.setAttribute('data-i18n-attr', `aria-label:${key};title:${key}`);
}

function getRunCodeButtonTextConfig(isRunning = false) {
    return isRunning
        ? { key: 'code_block_running', fallback: 'Running' }
        : { key: 'code_block_run', fallback: 'Run' };
}

function getRunCodeButtonA11yConfig(isRunning = false, streamLocked = false) {
    if (isRunning) {
        return { key: 'code_block_running_python', fallback: 'Running Python code' };
    }
    if (streamLocked) {
        return {
            key: 'code_block_run_python_stream_locked',
            fallback: 'Run Python code after generation finishes',
        };
    }
    return { key: 'code_block_run_python', fallback: 'Run Python code' };
}

function setCodeBlockCollapseButtonState(button, isCollapsed) {
    if (!(button instanceof Element)) {
        return;
    }
    button.innerHTML = isCollapsed ? MARKDOWN_EXPAND_SVG : MARKDOWN_COLLAPSE_SVG;
    setCodeBlockActionButtonLabel(
        button,
        isCollapsed ? 'code_block_expand' : 'code_block_collapse',
        isCollapsed ? 'Expand code block' : 'Collapse code block'
    );
}

function formatChatPreviewTranslation(key, fallback, vars) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }

    return String(getChatPreviewTranslation(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars?.[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

function reportChatCopyFeedback({ success, key, fallback }) {
    const message = getChatPreviewTranslation(key, fallback);
    if (success) {
        if (typeof notifySuccess === 'function') {
            notifySuccess(message);
        }
        if (typeof window.announceChatMessage === 'function') {
            window.announceChatMessage(message);
        }
        return;
    }

    if (typeof notifyError === 'function') {
        notifyError(message);
    }
    if (typeof window.announceChatMessage === 'function') {
        window.announceChatMessage(message, { assertive: true });
    }
}

const FEEDBACK_MODAL_COMMENT_MAX_LENGTH = 2000;
const FEEDBACK_MODAL_FOCUSABLE_SELECTOR = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [contenteditable]:not([contenteditable="false"]), [tabindex]:not([tabindex="-1"])';
let feedbackModalIdSequence = 0;

function getFeedbackModalText(reaction) {
    const isPositive = reaction === 'thumbs_up';
    return {
        title: getChatPreviewTranslation(
            isPositive ? 'chat_feedback_positive_title' : 'chat_feedback_negative_title',
            isPositive ? 'Glad it helped!' : 'Sorry about that'
        ),
        prompt: getChatPreviewTranslation(
            'chat_feedback_prompt',
            'Would you like to add a comment to help us improve?'
        ),
        close: getChatPreviewTranslation('chat_feedback_close', 'Close'),
        addComment: getChatPreviewTranslation('chat_feedback_add_comment', 'Add comment'),
        submitWithoutComment: getChatPreviewTranslation(
            'chat_feedback_submit_without_comment',
            'Submit without comment'
        ),
        commentLabel: getChatPreviewTranslation('chat_feedback_comment_label', 'Feedback comment'),
        placeholder: getChatPreviewTranslation(
            isPositive ? 'chat_feedback_positive_placeholder' : 'chat_feedback_negative_placeholder',
            isPositive ? 'What did you like about this response?' : 'What could be improved?'
        ),
        cancel: getChatPreviewTranslation('chat_feedback_cancel', 'Cancel'),
        submit: getChatPreviewTranslation('chat_feedback_submit', 'Submit'),
    };
}

function createFeedbackModalIds() {
    feedbackModalIdSequence += 1;
    return {
        titleId: `feedbackModalTitle-${feedbackModalIdSequence}`,
        descriptionId: `feedbackModalDescription-${feedbackModalIdSequence}`,
        labelId: `feedbackModalLabel-${feedbackModalIdSequence}`,
        textareaId: `feedbackModalTextarea-${feedbackModalIdSequence}`,
        charCountId: `feedbackModalCharCount-${feedbackModalIdSequence}`,
    };
}

function getFeedbackModalFocusableElements(container) {
    if (!(container instanceof Element)) {
        return [];
    }

    return Array.from(container.querySelectorAll(FEEDBACK_MODAL_FOCUSABLE_SELECTOR)).filter((element) => {
        if (element.hasAttribute('hidden')) {
            return false;
        }
        if (typeof window === 'undefined' || typeof window.getComputedStyle !== 'function') {
            return true;
        }
        const computedStyle = window.getComputedStyle(element);
        if (computedStyle.display === 'none' || computedStyle.visibility === 'hidden' || computedStyle.visibility === 'collapse') {
            return false;
        }
        const hasLayout = element.offsetParent !== null || computedStyle.position === 'fixed';
        const hasDimensions = element.offsetWidth > 0 || element.offsetHeight > 0 || element.getClientRects().length > 0;
        return hasLayout && hasDimensions;
    });
}

function focusFeedbackModalTarget(target) {
    if (!target || typeof target.focus !== 'function' || target.isConnected === false) {
        return;
    }

    const scheduleFocus = typeof requestAnimationFrame === 'function'
        ? requestAnimationFrame
        : (callback) => callback();
    scheduleFocus(() => {
        try {
            target.focus({ preventScroll: true });
        } catch (_) {
            target.focus();
        }
    });
}

function trapFeedbackModalFocus(event, overlay) {
    if (!event || event.key !== 'Tab') {
        return;
    }

    const focusableElements = getFeedbackModalFocusableElements(overlay);
    if (focusableElements.length === 0) {
        event.preventDefault();
        focusFeedbackModalTarget(overlay);
        return;
    }

    const firstElement = focusableElements[0];
    const lastElement = focusableElements[focusableElements.length - 1];

    if (event.shiftKey && document.activeElement === firstElement) {
        event.preventDefault();
        lastElement.focus();
    } else if (!event.shiftKey && document.activeElement === lastElement) {
        event.preventDefault();
        firstElement.focus();
    }
}

function focusFeedbackModalPrimaryAction(overlay) {
    if (!(overlay instanceof Element)) {
        return;
    }

    const target = overlay.querySelector('[data-feedback-action="comment"], .feedback-modal-textarea, [data-feedback-action="close"]');
    focusFeedbackModalTarget(target || overlay);
}

function updateFeedbackModalCharCount(charCountElement, valueLength, maxLength = FEEDBACK_MODAL_COMMENT_MAX_LENGTH) {
    if (!(charCountElement instanceof Element)) {
        return;
    }

    charCountElement.textContent = formatChatPreviewTranslation(
        'chat_feedback_char_count',
        '{count} / {max}',
        { count: valueLength, max: maxLength }
    );
    charCountElement.classList.toggle('is-warning', valueLength > (maxLength - 200));
}

/**
 * Mount authored HTML through the shared trusted proxy. Direct `srcdoc`
 * documents inherit Omlorix's HTTP `script-src 'self'` policy, so no meta CSP
 * inside that document can make inline scripts interactive. The proxy is
 * ordinary same-origin HTTP content and places authored code one level deeper
 * in an opaque-origin sandbox where the two explicit permission switches are
 * authoritative.
 */

function getVisualizationControlLabels() {
    const t = getChatPreviewTranslation;
    return {
        design: t('visualization_design', 'Design controls'),
        close: t('common_close', 'Close'),
        original: t('visualization_original', 'Preview original'),
        resetDesign: t('visualization_reset_design', 'Reset design'),
        submit: t('visualization_submit_design', 'Apply in chat'),
        submitPrompt: t('visualization_design_prompt', 'Update this mockup using these selected variants and design changes:'),
        variants: t('visualization_variants', 'Variants'),
        previous: t('visualization_previous_variant', 'Previous'),
        next: t('visualization_next_variant', 'Next'),
        saveError: t('visualization_state_error', 'Changes are not saved. Retry when connected.'),
        invalid: t('visualization_state_invalid', 'Visualization state must be valid JSON smaller than 16 KiB.'),
    };
}

function createVisualizationStateStore(options, notify) {
    let snapshot = JSON.parse(JSON.stringify(options.savedState || { widgetState: null, design: {}, revision: 0 }));
    let revision = snapshot.revision || 0;
    let dirty = false, timer, flight, waiters = [], loaded = false, conflict = false;
    const persistent = Boolean((options.messageId || options.getMessageId) && options.toolCallId && !options.temporary
        && document.body?.dataset?.page !== 'chat-share');
    let boundMessageId = options.messageId || '';
    const messageId = () => (boundMessageId ||= options.getMessageId?.() || '');
    const fetchState = (init) => (typeof authedFetch === 'function' ? authedFetch : window.fetch)(
        `/api/v1/chats/messages/${encodeURIComponent(messageId())}/visualizations/${encodeURIComponent(options.toolCallId)}/state`, init);
    const observer = options.messageElement && typeof MutationObserver === 'function' ? new MutationObserver(() => {
        if (messageId()) void flush().catch(() => {});
    }) : null;
    observer?.observe(options.messageElement, { attributes: true, attributeFilter: ['data-assistant-message-id'] });
    async function load() {
        if (!persistent) { loaded = true; notify('local'); return snapshot; }
        if (!messageId()) return snapshot;
        loaded = false;
        try {
            const response = await fetchState({ method: 'GET' });
            // The widget can appear before its streaming message is committed.
            if (response.status === 404) { loaded = false; return snapshot; }
            if (!response.ok) throw new Error('load');
            const saved = await response.json();
            revision = saved.revision;
            snapshot = saved;
            loaded = true;
            conflict = false;
            notify('saved');
        } catch (_) { notify('error'); }
        return snapshot;
    }
    async function flush() {
        clearTimeout(timer);
        timer = null;
        if (flight) { await flight; return dirty ? flush() : undefined; }
        if (!dirty) return;
        if (persistent && !messageId()) {
            waiters.splice(0).forEach(({ resolve }) => resolve({ persisted: false }));
            return;
        }
        const pending = waiters;
        waiters = [];
        dirty = false;
        flight = (async () => {
            if (conflict) throw new Error('conflict');
            if (!persistent) { options.onStateChange?.(snapshot); notify('local'); return { persisted: false }; }
            notify('saving');
            if (!loaded) {
                const response = await fetchState({ method: 'GET' });
                if (!response.ok) throw new Error('load');
                const current = await response.json();
                // Do not overwrite a snapshot we were unable to load initially.
                if (current.revision !== revision) { conflict = true; throw new Error('conflict'); }
                loaded = true;
            }
            const response = await fetchState({ method: 'PUT', keepalive: true, headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ widgetState: snapshot.widgetState ?? null, design: snapshot.design || {}, revision }) });
            if (response.status === 409) { conflict = true; throw new Error('conflict'); }
            if (!response.ok) throw new Error('save');
            const saved = await response.json();
            revision = saved.revision;
            snapshot.revision = revision;
            options.onStateChange?.(snapshot);
            notify('saved');
            return { persisted: true };
        })();
        try {
            const result = await flight;
            pending.forEach(({ resolve }) => resolve(result));
        } catch (error) {
            dirty = true;
            notify(conflict ? 'conflict' : 'error');
            pending.forEach(({ reject }) => reject(error));
            throw error;
        } finally { flight = null; }
        if (dirty) return flush();
    }
    function update(patch) {
        try {
            const next = { widgetState: snapshot.widgetState ?? null, design: snapshot.design || {}, ...patch };
            const serialized = JSON.stringify(next);
            if (new TextEncoder().encode(serialized).length > 16384 || (next.widgetState !== null && (typeof next.widgetState !== 'object' || Array.isArray(next.widgetState)))
                || !next.design || typeof next.design !== 'object' || Array.isArray(next.design)) throw new Error('invalid');
            snapshot = { ...JSON.parse(serialized), revision };
            // Temporary chats serialize widget payloads directly for follow-ups.
            options.onStateChange?.(snapshot);
            dirty = true;
            notify(persistent ? 'saving' : 'local');
            if (!timer) timer = setTimeout(() => { void flush().catch(() => {}); }, 450);
            return new Promise((resolve, reject) => { waiters.push({ resolve, reject }); });
        } catch (error) { notify('error'); return Promise.reject(error); }
    }
    const store = { load, flush, update, get snapshot() { return snapshot; },
        async retry() {
            if (conflict || !dirty) {
                await load();
                if (loaded) dirty = false;
                return loaded;
            }
            await flush();
            return false;
        },
        dispose() { observer?.disconnect(); clearTimeout(timer); void flush().catch(() => {}); visualizationStateStores.delete(store); },
    };
    visualizationStateStores.add(store);
    return store;
}
