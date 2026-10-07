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
            if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
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

// Expansion keeps the original iframe in place, preserving live control state.
// Siblings along its ancestor path become inert, as for the shared modals.
function expandVisualizationSurface(surface, controller, trigger) {
    activeVisualizationSurface?.collapse();
    const inertNodes = [];
    let current = surface;
    while (current.parentElement && current !== document.body) {
        Array.from(current.parentElement.children).forEach((sibling) => {
            if (sibling !== current && !sibling.inert) {
                sibling.inert = true;
                inertNodes.push(sibling);
            }
        });
        current = current.parentElement;
    }
    surface.classList.add('is-expanded');
    surface.setAttribute('role', 'dialog');
    surface.setAttribute('aria-modal', 'true');
    document.body.classList.add('visualization-expanded');
    controller.collapse = () => {
        surface.classList.remove('is-expanded');
        surface.setAttribute('role', 'region');
        surface.removeAttribute('aria-modal');
        inertNodes.forEach((node) => { node.inert = false; });
        document.body.classList.remove('visualization-expanded');
        activeVisualizationSurface = null;
        controller.collapse = () => {};
        broadcastVisualizationTheme();
        if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
    controller.keydown = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            controller.collapse();
        } else if (event.key === 'Tab') {
            const elements = Array.from(surface.querySelectorAll('button:not([disabled]), iframe, [tabindex="0"], summary'))
                .filter((element) => element.getClientRects().length && !element.closest('[hidden]'));
            const first = elements[0];
            const last = elements.at(-1);
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
            }
        }
    };
    activeVisualizationSurface = controller;
    broadcastVisualizationTheme();
    surface.querySelector('[data-visualizer-close]')?.focus();
}

async function mountVisualizerPreview(target, source, options = {}) {
    if (!(target instanceof Element)) return false;
    target._previewCleanup?.();
    const capabilities = normalizeVisualizationCapabilitiesForSurface(options.capabilities || { scripts: true });
    const allowScripts = capabilities.scripts !== false;
    const title = String(options.title || getChatPreviewTranslation('visualization_preview_label', 'Visualization'));
    const mode = options.mode === 'wide' ? 'wide' : 'normal';
    const id = `visualizer-${crypto.randomUUID()}`;
    const label = (key, fallback) => getCodeBlockActionA11yAttrs(key, fallback);
    const surface = document.createElement('section');
    surface.className = `visualizer-preview-surface${mode === 'wide' ? ' is-wide' : ''}`;
    surface.setAttribute('role', 'region');
    surface.setAttribute('aria-label', title);
    surface.innerHTML = `
        <div class="visualizer-preview-toolbar">
            <div class="visualizer-preview-heading">
                <span class="visualizer-preview-heading-icon" aria-hidden="true">${Icons.visualization}</span>
                <span class="visualizer-preview-badge">${escapeHtml(title)}</span>
            </div>
            <div class="visualizer-preview-toolbar-actions">
                <button type="button" class="visualizer-preview-action" data-preview-action="design" hidden aria-expanded="false" ${label('visualization_design', 'Design controls')}>${MARKDOWN_SETTINGS_SVG}</button>
                <button type="button" class="visualizer-preview-action" data-preview-action="source" aria-pressed="false" ${label('visualization_view_source', 'View source')}>${MARKDOWN_CODE_SVG}</button>
                <button type="button" class="visualizer-preview-action" data-preview-action="reset" ${label('visualization_reset', 'Reset visualization')}>${MARKDOWN_RESET_ZOOM_SVG}</button>
                <button type="button" class="visualizer-preview-action" data-preview-action="download" disabled ${label('visualization_save_html', 'Download HTML')}>${MARKDOWN_DOWNLOAD_SVG}</button>
                <button type="button" class="visualizer-preview-action visualizer-preview-expand" data-preview-action="expand" ${label('code_block_open_large_preview', 'Open large preview')}>${MARKDOWN_EXPAND_PREVIEW_SVG}</button>
                <button type="button" class="visualizer-preview-action visualizer-preview-close" data-visualizer-close ${label('files_preview_close_aria', 'Close preview')}>${MARKDOWN_CLOSE_SVG}</button>
            </div>
        </div>
        <div class="visualizer-state-status" hidden><span role="status" aria-live="polite"></span><button type="button" class="visualizer-preview-action" data-preview-action="retry-state" hidden ${label('visualization_retry_state', 'Retry saving')}>${MARKDOWN_RELOAD_SVG}</button></div>
        <div class="visualizer-preview-status" role="status" aria-live="polite"></div>
        <div class="visualizer-preview-stage" id="${id}-view"><div class="visualizer-preview-frame-shell"></div></div>
        <div class="visualizer-preview-source" id="${id}-source" hidden><pre tabindex="0"><code></code></pre></div>
        ${options.summary ? `<details class="visualizer-preview-summary"><summary data-i18n="visualization_text_alternative">${escapeHtml(getChatPreviewTranslation('visualization_text_alternative', 'Text alternative'))}</summary><p>${escapeHtml(options.summary)}</p></details>` : ''}
    `;
    target.replaceChildren(surface);
    const stage = surface.querySelector('.visualizer-preview-stage');
    const sourcePane = surface.querySelector('.visualizer-preview-source');
    sourcePane.querySelector('code').textContent = String(source || '');
    const sourceButton = surface.querySelector('[data-preview-action="source"]');
    sourceButton.setAttribute('aria-controls', `${id}-source`);
    const statusNode = surface.querySelector('.visualizer-preview-status');
    const stateStatus = surface.querySelector('.visualizer-state-status');
    let stateLoaded = false;
    const stateStore = createVisualizationStateStore(options, (state) => {
        const labels = {
            saving: ['visualization_state_saving', 'Saving selections…'],
            saved: ['visualization_state_saved', 'Selections saved'],
            local: ['visualization_state_local', 'Selections stay in this session'],
            error: ['visualization_state_error', 'Changes are not saved. Retry when connected.'],
            conflict: ['visualization_state_conflict', 'Changed in another tab. Reload saved selections.'],
        };
        stateStatus.hidden = false;
        stateStatus.querySelector('span').textContent = getChatPreviewTranslation(...labels[state]);
        const retry = stateStatus.querySelector('button');
        retry.hidden = !['error', 'conflict'].includes(state);
        retry.setAttribute('aria-label', getChatPreviewTranslation(state === 'conflict' ? 'visualization_reload_state' : 'visualization_retry_state', state === 'conflict' ? 'Reload saved selections' : 'Retry saving'));
    });

    const controller = {
        collapse() {},
        status(state) {
            if (!['loading', 'ready', 'error'].includes(state)) return;
            surface.dataset.state = state;
            statusNode.hidden = state === 'ready';
            statusNode.textContent = state === 'loading'
                ? getChatPreviewTranslation('visualization_loading', 'Preparing visualization…')
                : state === 'error' ? getChatPreviewTranslation('visualization_run_error', 'This visualization could not run. Reset it or ask for a corrected version.') : '';
        },
    };
    controller.status('loading');
    let iframe = null;
    let assets = null;
    let timeout = null;
    let disposed = false;
    let renderGeneration = 0;
    let visibilityObserver = null;
    target._previewCleanup = () => {
        disposed = true;
        renderGeneration += 1;
        clearTimeout(timeout);
        visibilityObserver?.disconnect();
        controller.collapse();
        stateStore.dispose();
        iframe?.remove();
    };
    ensureCodeBlockPreviewMessageListener();

    async function render() {
        const generation = ++renderGeneration;
        clearTimeout(timeout);
        controller.status('loading');
        try {
            if (!stateLoaded) { await stateStore.load(); stateLoaded = true; }
            assets = await loadVisualizerRuntimeAssets(allowScripts, source);
            if (disposed || generation !== renderGeneration) return false;
            const proxyRuntime = window.OmlorixCanvasHtmlPreview;
            if (!proxyRuntime?.render) throw new Error('Missing visualization proxy');
            iframe?.remove();
            const designButton = surface.querySelector('[data-preview-action="design"]');
            designButton.hidden = true;
            designButton.setAttribute('aria-expanded', 'false');
            iframe = document.createElement('iframe');
            iframe.className = 'visualizer-preview-frame';
            iframe.title = title;
            iframe.setAttribute('referrerpolicy', 'no-referrer');
            iframe.dataset.previewFrameId = `${id}-${generation}`;
            iframe.dataset.previewMinHeight = '120';
            iframe.dataset.previewMaxHeight = '1800';
            iframe.dataset.visualizationCapabilities = JSON.stringify(capabilities);
            iframe.style.height = '240px';
            visualizationSurfaces.set(iframe, {
                saveState(patch) { return stateStore.update(patch); },
                designOpen(open) {
                    const button = surface.querySelector('[data-preview-action="design"]');
                    button.setAttribute('aria-expanded', String(open));
                    if (!open) button.focus({ preventScroll: true });
                },
                designAvailable() { surface.querySelector('[data-preview-action="design"]').hidden = false; },
                status(state) { clearTimeout(timeout); controller.status(state); },
                collapse() { controller.collapse(); },
                moveFocus(backwards) {
                    if (!surface.classList.contains('is-expanded')) return;
                    const controls = Array.from(surface.querySelectorAll('button, iframe, summary'))
                        .filter((node) => node.getClientRects().length && !node.disabled && !node.closest('[hidden]'));
                    const index = controls.indexOf(iframe);
                    controls[(index + (backwards ? -1 : 1) + controls.length) % controls.length]?.focus();
                },
            });
            surface.querySelector('.visualizer-preview-frame-shell').replaceChildren(iframe);
            const previewDocument = buildVisualizerPreviewDocument(source, iframe.dataset.previewFrameId, {
                title, allowScripts, capabilities, savedState: stateStore.snapshot, runtimeCss: assets.css, ...assets,
            });
            timeout = setTimeout(() => controller.status('error'), 15000);
            iframe.addEventListener('canvashtmlpreviewload', (event) => {
                if (event.detail?.navigated) controller.status('error');
                else broadcastVisualizationTheme();
            });
            if (!proxyRuntime.render(iframe, previewDocument, {
                title, visualization: true, allowScripts: true, allowEval: false,
                allowExternalContent: false, hydrateAuthenticatedFiles: false, relayVisualizationMessages: true,
            })) throw new Error('Visualization proxy unavailable');
            surface.querySelector('[data-preview-action="download"]').disabled = false;
            return true;
        } catch (_) {
            if (generation === renderGeneration && !disposed) controller.status('error');
            return false;
        }
    }

    surface.addEventListener('click', (event) => {
        const button = event.target instanceof Element ? event.target.closest('button') : null;
        if (!button) return;
        if (button.hasAttribute('data-visualizer-close')) return controller.collapse();
        switch (button.dataset.previewAction) {
            case 'design': {
                const open = button.getAttribute('aria-expanded') !== 'true';
                button.setAttribute('aria-expanded', String(open));
                iframe?.contentWindow?.postMessage({ type: VISUALIZATION_CONTROL_MESSAGE_TYPE, previewId: iframe.dataset.previewFrameId, open }, window.location.origin);
                break;
            }
            case 'retry-state':
                void stateStore.retry().then((reloaded) => { if (reloaded) void render(); }).catch(() => {});
                break;
            case 'source': {
                const showSource = sourcePane.hidden;
                sourcePane.hidden = !showSource;
                stage.hidden = showSource;
                button.setAttribute('aria-pressed', String(showSource));
                break;
            }
            case 'expand':
                expandVisualizationSurface(surface, controller, button);
                break;
            case 'reset':
                void stateStore.update({ widgetState: null, design: {} }).catch(() => {});
                void render();
                break;
            case 'download': {
                if (!assets) break;
                const html = buildVisualizerPreviewDocument(source, 'standalone', {
                    title, summary: options.summary, allowScripts, savedState: stateStore.snapshot, capabilities: { scripts: allowScripts }, runtimeCss: assets.css, ...assets,
                });
                const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
                const link = document.createElement('a');
                link.href = url;
                link.download = `${title.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-').slice(0, 100) || 'visualization'}.html`;
                link.click();
                setTimeout(() => URL.revokeObjectURL(url), 1000);
                break;
            }
        }
    });
    // Long transcripts allocate a runtime only when approaching the viewport.
    if (options.isWidget && typeof IntersectionObserver === 'function') {
        visibilityObserver = new IntersectionObserver((entries) => {
            if (!entries.some((entry) => entry.isIntersecting)) return;
            visibilityObserver.disconnect();
            void render();
        }, { rootMargin: '600px' });
        visibilityObserver.observe(surface);
        return true;
    }
    return render();
}

window.OmlorixVisualizer = Object.freeze({
    mount: mountVisualizerPreview,
    flushState: () => Promise.all(Array.from(visualizationStateStores, (store) => store.flush().catch(() => {}))),
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
        const rendering = mountMermaidPreview(previewPane, source, { allowExpand: true });
        const surface = previewPane.firstElementChild;
        return rendering.then((rendered) => {
            if (previewPane.firstElementChild === surface) {
                previewPane.dataset.previewState = rendered ? 'ready' : 'error';
            }
            return rendered;
        }).catch(() => {
            if (previewPane.firstElementChild === surface) previewPane.dataset.previewState = 'error';
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
