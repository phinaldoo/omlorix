function openCodeBlockPreviewModal({
    title,
    ariaLabel,
    mountPreview,
    fullscreen = false,
    hideHeader = false,
    modalClass = '',
}) {
    const modal = document.createElement('div');
    modal.className = `code-block-preview-modal-overlay shared-modal-overlay${fullscreen ? ' is-fullscreen' : ''}`;
    const previewLabel = getChatPreviewTranslation('code_block_preview_label', 'Preview');
    const closePreviewLabel = getChatPreviewTranslation('files_preview_close_aria', 'Close preview');
    modal.innerHTML = `
        <div class="code-block-preview-modal shared-modal shared-modal--large shared-modal--fixed${fullscreen ? ' is-fullscreen' : ''}${modalClass ? ` ${escapeHtml(modalClass)}` : ''}" role="dialog" aria-modal="true" aria-label="${escapeHtml(ariaLabel || title || previewLabel)}" tabindex="-1">
            ${hideHeader ? '' : `<div class="code-block-preview-modal-header shared-modal-header shared-modal-header--main">
                <span class="code-block-preview-modal-title shared-modal-title">${escapeHtml(title || previewLabel)}</span>
                <button type="button" class="code-block-preview-modal-close shared-modal-close" aria-label="${escapeHtml(closePreviewLabel)}" data-i18n-attr="aria-label:files_preview_close_aria">${MARKDOWN_CLOSE_SVG}</button>
            </div>`}
            <div class="code-block-preview-modal-body shared-modal-body"></div>
        </div>
    `;

    const dialog = modal.querySelector('.code-block-preview-modal');
    const closeButton = modal.querySelector('.code-block-preview-modal-close');
    const body = modal.querySelector('.code-block-preview-modal-body');
    if (body) {
        body.classList.add('markdown-body');
    }
    if (closeButton) {
        closeButton.addEventListener('click', () => closeCodeBlockPreviewModal());
    }
    modal.addEventListener('click', (event) => {
        if (event.target === modal) {
            closeCodeBlockPreviewModal();
        }
    });
    const escapeHandler = (event) => {
        if (event.key === 'Escape') {
            closeCodeBlockPreviewModal();
            return;
        }
        if (event.key === 'Tab' && dialog) {
            const focusable = Array.from(dialog.querySelectorAll(
                'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), iframe, [tabindex]:not([tabindex="-1"])'
            )).filter((element) => !element.hidden && element.getClientRects().length > 0);
            if (!focusable.length) {
                event.preventDefault();
                dialog.focus();
                return;
            }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    };
    modal._escapeHandler = escapeHandler;
    document.addEventListener('keydown', escapeHandler);

    modal._previousFocus = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    document.body.appendChild(modal);
    document.body.classList.add('code-block-preview-modal-open');
    activeCodeBlockPreviewModal = modal;
    dialog?.focus({ preventScroll: true });
    modal._cleanup = () => {
        if (body && typeof body._previewCleanup === 'function') {
            body._previewCleanup();
        }
    };

    if (body && typeof mountPreview === 'function') {
        const mounted = mountPreview(body);
        if (mounted && typeof mounted.then === 'function') {
            mounted.catch(() => {});
        }
    }
}

function openMermaidPreviewModal(wrapper) {
    if (!(wrapper instanceof Element)) {
        return;
    }
    const source = getCodeBlockSource(wrapper);
    if (!source) {
        return;
    }
    closeCodeBlockPreviewModal();

    const previewTitle = getChatPreviewTranslation('code_block_mermaid_preview_title', 'Mermaid preview');
    openCodeBlockPreviewModal({
        title: previewTitle,
        ariaLabel: previewTitle,
        fullscreen: true,
        hideHeader: true,
        mountPreview(body) {
            return mountMermaidPreview(body, source, {
                allowExpand: false,
                isModal: true,
            });
        },
    });
}

const ALLOWED_PREVIEW_ACTIONS = Object.freeze(['expand', 'run-interactive']);

function validatePreviewAction(action) {
    const normalized = String(action || '').trim().toLowerCase();
    return ALLOWED_PREVIEW_ACTIONS.includes(normalized) ? normalized : null;
}

function openStandaloneVisualizerPreviewModal(source, options = {}) {
    closeCodeBlockPreviewModal();
    openCodeBlockPreviewModal({
        title: options.title || getChatPreviewTranslation('visualization_preview_title', 'Visualization preview'),
        ariaLabel: getChatPreviewTranslation('visualization_preview_aria', 'Visualization preview'),
        hideHeader: true,
        modalClass: 'is-visualizer',
        mountPreview(body) {
            return mountVisualizerPreview(body, source, {
                ...options,
                allowExpand: false,
                allowScripts: false,
                isModal: true,
                showClose: true,
            });
        },
    });
}

function bindVisualizerPreviewSurface(surface, {
    allowExpand = true,
    allowScripts = false,
    source = '',
    target = null,
    isModal = false,
    title = '',
    mode = 'normal',
    capabilities = {},
    showClose = false,
} = {}) {
    if (!(surface instanceof Element) || surface.dataset.boundVisualizerPreviewSurface === 'true') {
        return;
    }
    surface.dataset.boundVisualizerPreviewSurface = 'true';
    surface.addEventListener('click', async (event) => {
        const closeButton = event.target instanceof Element
            ? event.target.closest('[data-visualizer-close]')
            : null;
        if (closeButton instanceof HTMLButtonElement && isModal) {
            event.preventDefault();
            event.stopPropagation();
            closeCodeBlockPreviewModal();
            return;
        }
        const actionButton = event.target instanceof Element
            ? event.target.closest('[data-preview-action]')
            : null;
        if (!(actionButton instanceof HTMLButtonElement)) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        const action = validatePreviewAction(actionButton.getAttribute('data-preview-action'));
        if (action === 'run-interactive' && !allowScripts && target instanceof Element) {
            await mountVisualizerPreview(target, source, {
                allowExpand,
                allowScripts: true,
                isModal,
                title,
                mode,
                capabilities,
                showClose,
            });
            return;
        }
        if (action === 'expand' && allowExpand) {
            openStandaloneVisualizerPreviewModal(source, { title, mode, capabilities });
            return;
        }
    });
}

async function mountVisualizerPreview(target, source, options = {}) {
    if (!(target instanceof Element)) {
        return false;
    }

    const capabilities = normalizeVisualizationCapabilitiesForSurface(
        options.capabilities && typeof options.capabilities === 'object'
            ? options.capabilities
            : { scripts: true, external_data: false, chat_followup: false, download: false }
    );
    const canEnableScripts = capabilities.scripts !== false;
    const allowScripts = canEnableScripts && options.allowScripts === true;
    ensureCodeBlockPreviewMessageListener();

    let runtimeAssets;
    try {
        // Static-first rendering fetches only the small CSS contract. The much
        // larger optional libraries are loaded after the viewer explicitly
        // enables interaction for an artifact that can run scripts.
        runtimeAssets = await loadVisualizerRuntimeAssets(allowScripts);
    } catch (error) {
        target.innerHTML = `<div class="code-block-preview-status">${escapeHtml(
            getChatPreviewTranslation('visualization_runtime_unavailable', 'The visualization runtime could not be loaded.')
        )}</div>`;
        return false;
    }

    const surface = document.createElement('div');
    const mode = options.mode === 'wide' ? 'wide' : 'normal';
    surface.className = `visualizer-preview-surface${options.isModal ? ' is-modal' : ''}${mode === 'wide' ? ' is-wide' : ''}`;
    const previewBadge = getChatPreviewTranslation('visualization_preview_label', 'Visualization');
    const expandLabel = getChatPreviewTranslation('code_block_open_large_preview', 'Open large preview');
    const runLabel = getChatPreviewTranslation(
        'code_block_html_preview_run_interactive',
        'Run interactive preview and allow external content'
    );
    const closeLabel = getChatPreviewTranslation('files_preview_close_aria', 'Close preview');
    const displayTitle = String(options.title || previewBadge).trim() || previewBadge;
    surface.innerHTML = `
        <div class="visualizer-preview-toolbar">
            <div class="visualizer-preview-heading">
                <span class="visualizer-preview-heading-icon" aria-hidden="true">${Icons?.chartLine || Icons?.image || ''}</span>
                <div class="visualizer-preview-badge">${escapeHtml(displayTitle)}</div>
            </div>
            <div class="visualizer-preview-toolbar-actions">
                ${canEnableScripts && !allowScripts ? `<button type="button" class="code-block-preview-run-btn visualizer-preview-run-btn" data-preview-action="run-interactive" aria-label="${escapeHtml(runLabel)}" data-i18n="code_block_html_preview_run_interactive" data-i18n-attr="aria-label:code_block_html_preview_run_interactive"><span aria-hidden="true">${MARKDOWN_RUN_SVG}</span><span>${escapeHtml(runLabel)}</span></button>` : ''}
                ${options.allowExpand !== false ? `<button type="button" class="visualizer-preview-action" data-preview-action="expand" aria-label="${escapeHtml(expandLabel)}" title="${escapeHtml(expandLabel)}" data-i18n-attr="aria-label:code_block_open_large_preview;title:code_block_open_large_preview">${MARKDOWN_EXPAND_PREVIEW_SVG}</button>` : ''}
                ${options.showClose === true ? `<button type="button" class="visualizer-preview-action visualizer-preview-close" data-visualizer-close aria-label="${escapeHtml(closeLabel)}" title="${escapeHtml(closeLabel)}" data-i18n-attr="aria-label:files_preview_close_aria;title:files_preview_close_aria">${MARKDOWN_CLOSE_SVG}</button>` : ''}
            </div>
        </div>
        <div class="visualizer-preview-stage">
            <div class="visualizer-preview-frame-shell"></div>
        </div>
    `;

    target.innerHTML = '';
    target.appendChild(surface);
    bindVisualizerPreviewSurface(surface, {
        allowExpand: options.allowExpand !== false,
        allowScripts,
        source,
        target,
        isModal: options.isModal === true,
        title: displayTitle,
        mode,
        capabilities,
        showClose: options.showClose === true,
    });

    const frameShell = surface.querySelector('.visualizer-preview-frame-shell');
    if (!(frameShell instanceof Element)) {
        return false;
    }

    const iframe = document.createElement('iframe');
    iframe.className = 'visualizer-preview-frame';
    // Direct srcdoc content inherits Omlorix's response CSP in Safari and other
    // browsers, which blocks even an explicitly narrowed inline-script policy.
    // The trusted HTML-preview proxy is same-origin; it mounts this document
    // one level deeper in an opaque sandbox where its CSP is authoritative.
    iframe.setAttribute('referrerpolicy', 'no-referrer');
    iframe.setAttribute('loading', 'lazy');
    iframe.setAttribute('title', allowScripts
        ? getChatPreviewTranslation('visualization_preview_interactive_frame_title', 'Interactive visualization preview')
        : getChatPreviewTranslation('visualization_preview_static_frame_title', 'Static visualization preview'));
    iframe.setAttribute('data-i18n-attr', allowScripts
        ? 'title:visualization_preview_interactive_frame_title'
        : 'title:visualization_preview_static_frame_title');
    iframe.dataset.previewFrameId = options.previewId || `visualizer-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    iframe.dataset.previewMinHeight = options.isModal ? '320' : '160';
    iframe.dataset.previewMaxHeight = options.isModal ? '2400' : '1800';
    iframe.dataset.visualizationCapabilities = JSON.stringify(capabilities);
    iframe.dataset.visualizationMode = mode;
    iframe.style.height = options.isModal ? '560px' : '240px';
    const previewDocument = buildVisualizerPreviewDocument(source, iframe.dataset.previewFrameId, {
        allowScripts,
        capabilities,
        runtimeCss: runtimeAssets.css,
        d3: runtimeAssets.d3,
        topojson: runtimeAssets.topojson,
        lucide: runtimeAssets.lucide,
        emptyLabel: getChatPreviewTranslation('visualization_preview_empty', 'No visualization content.'),
    });
    frameShell.appendChild(iframe);

    const proxyRuntime = typeof window !== 'undefined' ? window.OmlorixCanvasHtmlPreview : null;
    if (!proxyRuntime || typeof proxyRuntime.render !== 'function') {
        target.innerHTML = `<div class="code-block-preview-status">${escapeHtml(
            getChatPreviewTranslation('visualization_runtime_unavailable', 'The visualization runtime could not be loaded.')
        )}</div>`;
        return false;
    }
    iframe.addEventListener('canvashtmlpreviewload', () => broadcastVisualizationTheme(), { once: true });
    const rendered = proxyRuntime.render(iframe, previewDocument, {
        title: iframe.title,
        // The host bridge always needs JavaScript for sizing and theme sync.
        // Authored scripts were already removed above unless the viewer opted
        // into the interactive mode.
        allowScripts: true,
        allowEval: false,
        // Static mode contains only Omlorix's generated bridge script. Once the
        // viewer explicitly requests authored interactivity, that same action
        // also grants external content because scripts can self-navigate.
        allowExternalContent: allowScripts,
        trustedLocalScripts: !allowScripts,
        hydrateAuthenticatedFiles: false,
        relayVisualizationMessages: true,
    });
    if (!rendered) {
        target.innerHTML = `<div class="code-block-preview-status">${escapeHtml(
            getChatPreviewTranslation('visualization_runtime_unavailable', 'The visualization runtime could not be loaded.')
        )}</div>`;
        return false;
    }

    return true;
}

// Tool-generated visualization widgets use this narrow public renderer surface
// so transcript rendering stays decoupled from the large Markdown module.
window.OmlorixVisualizer = Object.freeze({
    mount: mountVisualizerPreview,
});

async function mountVegaPreview(target, source, options = {}) {
    if (!(target instanceof Element)) {
        return false;
    }

    const previewKind = options.previewKind || getVegaPreviewKind('', source) || 'vega-lite';
    syncVegaExternalResourceControl(options.permissionHost, source);
    const surface = document.createElement('div');
    surface.className = `vega-preview-surface${options.isModal ? ' is-modal' : ''}`;
    surface.dataset.previewKind = previewKind;
    surface.innerHTML = `
        <div class="vega-preview-stage">
            <div class="vega-preview-canvas"></div>
        </div>
    `;
    target.innerHTML = '';
    target.appendChild(surface);

    const canvas = surface.querySelector('.vega-preview-canvas');
    target._previewCleanup = () => cleanupVegaPreviewTarget(canvas);
    const rendered = await renderVegaPreview(canvas, source, {
        previewKind,
        permissionHost: options.permissionHost,
    });
    if (!rendered) {
        surface.classList.add('has-error');
    }
    return rendered;
}

function openVegaPreviewModal(wrapper) {
    if (!(wrapper instanceof Element)) {
        return;
    }
    const source = getCodeBlockSource(wrapper);
    if (!source) {
        return;
    }
    const previewKind = wrapper.dataset.previewKind || getVegaPreviewKind('', source) || 'vega-lite';
    const previewLabel = getCodePreviewLabel(previewKind);
    closeCodeBlockPreviewModal();

    openCodeBlockPreviewModal({
        title: `${previewLabel} Preview`,
        ariaLabel: `${previewLabel} preview`,
        mountPreview(body) {
            return mountVegaPreview(body, source, {
                isModal: true,
                previewKind,
                permissionHost: wrapper,
            });
        },
    });
}

function ensureCodeBlockPreview(wrapper, options = {}) {
    if (!(wrapper instanceof Element)) {
        return false;
    }
    const forceRender = options.force === true;
    const previewKind = wrapper.dataset.previewKind || '';
    if (!previewKind) {
        return false;
    }
    const previewPane = wrapper.querySelector('.code-block-preview-pane');
    if (!(previewPane instanceof Element)) {
        return false;
    }
    const source = getCodeBlockSource(wrapper);
    const sourceHash = hashCodeBlockSource(source);
    if (!forceRender && previewPane.dataset.previewHash === sourceHash && previewPane.dataset.previewState === 'ready') {
        return true;
    }

    if (typeof previewPane._previewCleanup === 'function') {
        previewPane._previewCleanup();
        delete previewPane._previewCleanup;
    }

    previewPane.dataset.previewHash = sourceHash;
    previewPane.dataset.previewState = 'loading';
    previewPane.innerHTML = `<div class="code-block-preview-status">${escapeHtml(getChatPreviewTranslation('code_block_preview_rendering', 'Rendering preview...'))}</div>`;

    if (previewKind === 'html') {
        return mountHtmlCodePreview(previewPane, source, wrapper, getHtmlPreviewPermissions(wrapper));
    }

    if (previewKind === 'mermaid') {
        return mountMermaidPreview(previewPane, source, {
            allowExpand: true,
        }).then((rendered) => {
            previewPane.dataset.previewState = rendered ? 'ready' : 'error';
            return rendered;
        }).catch(() => {
            previewPane.dataset.previewState = 'error';
            return false;
        });
    }

    if (previewKind === 'vega' || previewKind === 'vega-lite') {
        return mountVegaPreview(previewPane, source, {
            previewKind,
            permissionHost: wrapper,
        }).then((rendered) => {
            previewPane.dataset.previewState = rendered ? 'ready' : 'error';
            return rendered;
        }).catch(() => {
            previewPane.dataset.previewState = 'error';
            return false;
        });
    }

    if (previewKind === 'svg') {
        previewPane.dataset.previewState = renderSvgCodePreview(previewPane, source) ? 'ready' : 'error';
        return previewPane.dataset.previewState === 'ready';
    }

    if (previewKind === 'markdown') {
        previewPane.dataset.previewState = renderMarkdownCodePreview(previewPane, source) ? 'ready' : 'error';
        return previewPane.dataset.previewState === 'ready';
    }

    if (previewKind === 'csv' || previewKind === 'tsv') {
        previewPane.dataset.previewState = renderDelimitedPreview(previewPane, source, previewKind === 'tsv' ? '\t' : ',') ? 'ready' : 'error';
        return previewPane.dataset.previewState === 'ready';
    }

    if (previewKind === 'json') {
        try {
            const parsed = JSON.parse(source);
            previewPane.dataset.previewState = renderStructuredDataPreview(previewPane, parsed) ? 'ready' : 'error';
        } catch (error) {
            console.error('JSON preview parsing failed:', error);
            previewPane.innerHTML = `<div class="code-block-preview-status">${escapeHtml(getChatPreviewTranslation('code_block_json_preview_unavailable', 'JSON preview is unavailable for this block.'))}</div>`;
            previewPane.dataset.previewState = 'error';
        }
        return previewPane.dataset.previewState === 'ready';
    }

    if (previewKind === 'yaml') {
        try {
            const parsed = parseSimpleYaml(source);
            previewPane.dataset.previewState = renderStructuredDataPreview(previewPane, parsed) ? 'ready' : 'error';
        } catch (_) {
            previewPane.dataset.previewState = renderYamlOutlinePreview(previewPane, source) ? 'ready' : 'error';
        }
        return previewPane.dataset.previewState === 'ready';
    }

    return false;
}

/**
 * Dispose preview resources before another surface replaces rendered Markdown.
 * Canvas and Notes reuse the chat code-block renderer outside message nodes,
 * so their editors need an explicit lifecycle hook for iframes, observers,
 * Vega views, and delayed live-preview work.
 */
