function closeCodeBlockPreviewModal() {
    if (!activeCodeBlockPreviewModal) {
        return;
    }
    const modal = activeCodeBlockPreviewModal;
    activeCodeBlockPreviewModal = null;
    if (modal._escapeHandler) {
        document.removeEventListener('keydown', modal._escapeHandler);
    }
    if (typeof modal._cleanup === 'function') {
        try {
            modal._cleanup();
        } catch (_) {}
    }
    document.body.classList.remove('code-block-preview-modal-open');
    modal.remove();
    if (modal._previousFocus instanceof HTMLElement && modal._previousFocus.isConnected) {
        modal._previousFocus.focus({ preventScroll: true });
    }
}

const MERMAID_PREVIEW_MAX_SCALE = 8;
const MERMAID_PREVIEW_BUTTON_FACTOR = 1.25;

// One coordinate system for every input: viewport pixels = diagram * scale + offset.
// The minimum follows fit so even very large diagrams can be seen in full.
function getMermaidViewportFit(width, height, diagramWidth, diagramHeight) {
    return Math.min(MERMAID_PREVIEW_MAX_SCALE, Math.max(1, width - 48) / diagramWidth, Math.max(1, height - 80) / diagramHeight);
}

function zoomMermaidViewport(state, scale, anchorX, anchorY) {
    const nextScale = Math.max(Math.min(0.1, state.fitScale), Math.min(scale, MERMAID_PREVIEW_MAX_SCALE));
    const ratio = nextScale / state.scale;
    state.x = anchorX - (anchorX - state.x) * ratio;
    state.y = anchorY - (anchorY - state.y) * ratio;
    state.scale = nextScale;
    state.fitted = false;
}

function fitMermaidViewport(state) {
    state.scale = state.fitScale;
    state.x = (state.width - state.diagramWidth * state.scale) / 2;
    state.y = (state.height - 32 - state.diagramHeight * state.scale) / 2;
    state.fitted = true;
}

function bindMermaidPreviewSurface(surface, metrics) {
    const stage = surface.querySelector('.mermaid-preview-stage');
    const canvas = surface.querySelector('.mermaid-preview-canvas');
    const value = surface.querySelector('.mermaid-preview-zoom-value');
    const zoomIn = surface.querySelector('[data-mermaid-action="zoom-in"]');
    const zoomOut = surface.querySelector('[data-mermaid-action="zoom-out"]');
    const ac = new AbortController();
    const pointers = new Map();
    let frame = 0;
    const state = {
        diagramWidth: metrics.width, diagramHeight: metrics.height,
        width: 0, height: 0, scale: 1, fitScale: 1, x: 0, y: 0, fitted: true,
    };
    canvas.style.width = `${metrics.width}px`;
    canvas.style.height = `${metrics.height}px`;

    const paint = () => {
        frame = 0;
        canvas.style.transform = `translate(${state.x}px, ${state.y}px) scale(${state.scale})`;
        surface.dataset.mermaidScale = String(state.scale);
        value.textContent = `${Math.round(state.scale * 100)}%`;
        zoomIn.disabled = state.scale >= MERMAID_PREVIEW_MAX_SCALE;
        zoomOut.disabled = state.scale <= Math.min(0.1, state.fitScale);
    };
    const update = () => {
        // Keep a small part of the chart reachable, without snapping small charts
        // back to the center during pointer-anchored zoom.
        state.x = Math.max(32 - metrics.width * state.scale, Math.min(state.x, state.width - 32));
        state.y = Math.max(32 - metrics.height * state.scale, Math.min(state.y, state.height - 32));
        if (!frame) frame = requestAnimationFrame(paint);
    };
    const fit = () => { fitMermaidViewport(state); update(); };
    const zoom = (scale, x = state.width / 2, y = state.height / 2) => {
        zoomMermaidViewport(state, scale, x, y);
        update();
    };
    const point = (event) => {
        const rect = stage.getBoundingClientRect();
        return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };
    const resize = () => {
        const width = stage.clientWidth;
        const height = stage.clientHeight;
        if (!width || !height) return; // Hidden code tabs retain their viewport.
        const dx = (width - state.width) / 2;
        const dy = (height - state.height) / 2;
        state.width = width;
        state.height = height;
        state.fitScale = getMermaidViewportFit(width, height, metrics.width, metrics.height);
        if (state.fitted) fit();
        else { state.x += dx; state.y += dy; update(); }
    };
    const observer = new ResizeObserver(resize);
    observer.observe(stage);
    resize();

    surface.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-mermaid-action]');
        if (!button || button.disabled) return;
        event.preventDefault();
        event.stopPropagation();
        switch (button.dataset.mermaidAction) {
            case 'zoom-in': zoom(state.scale * MERMAID_PREVIEW_BUTTON_FACTOR); break;
            case 'zoom-out': zoom(state.scale / MERMAID_PREVIEW_BUTTON_FACTOR); break;
            case 'reset': fit(); break;
        }
    }, { signal: ac.signal });

    stage.addEventListener('wheel', (event) => {
        // Ordinary scrolling continues through the conversation. Trackpad pinch
        // and Ctrl/Cmd+wheel zoom at the pointer, including line-mode mouse wheels.
        if (!event.ctrlKey && !event.metaKey) return;
        event.preventDefault();
        const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? state.height : 1;
        const delta = Math.max(-200, Math.min(200, event.deltaY * unit));
        const anchor = point(event);
        zoom(state.scale * Math.exp(-delta * 0.005), anchor.x, anchor.y);
    }, { passive: false, signal: ac.signal });

    stage.addEventListener('keydown', (event) => {
        if (event.target !== stage || event.ctrlKey || event.metaKey || event.altKey) return;
        const distance = event.shiftKey ? 100 : 40;
        switch (event.key) {
            case '+': case '=': zoom(state.scale * MERMAID_PREVIEW_BUTTON_FACTOR); break;
            case '-': case '_': zoom(state.scale / MERMAID_PREVIEW_BUTTON_FACTOR); break;
            case '0': case 'Home': fit(); break;
            case 'ArrowLeft': state.x += distance; break;
            case 'ArrowRight': state.x -= distance; break;
            case 'ArrowUp': state.y += distance; break;
            case 'ArrowDown': state.y -= distance; break;
            default: return;
        }
        event.preventDefault();
        if (event.key.startsWith('Arrow')) state.fitted = false;
        update();
    }, { signal: ac.signal });

    stage.addEventListener('pointerdown', (event) => {
        if (event.button !== 0 || event.target.closest('a')) return;
        stage.focus({ preventScroll: true });
        stage.setPointerCapture(event.pointerId);
        pointers.set(event.pointerId, point(event));
        stage.classList.add('is-panning');
    }, { signal: ac.signal });
    stage.addEventListener('pointermove', (event) => {
        if (!pointers.has(event.pointerId)) return;
        const before = Array.from(pointers.values());
        pointers.set(event.pointerId, point(event));
        const after = Array.from(pointers.values());
        if (before.length === 1) {
            state.x += after[0].x - before[0].x;
            state.y += after[0].y - before[0].y;
        } else {
            const center = points => ({ x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2 });
            const distance = points => Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
            const oldCenter = center(before);
            const newCenter = center(after);
            const oldDistance = distance(before);
            if (oldDistance > 0) zoomMermaidViewport(state, state.scale * distance(after) / oldDistance, oldCenter.x, oldCenter.y);
            state.x += newCenter.x - oldCenter.x;
            state.y += newCenter.y - oldCenter.y;
        }
        state.fitted = false;
        update();
    }, { signal: ac.signal });
    const endPointer = (event) => {
        pointers.delete(event.pointerId);
        stage.classList.toggle('is-panning', pointers.size > 0);
    };
    for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) {
        stage.addEventListener(type, endPointer, { signal: ac.signal });
    }

    // Safari emits gesture events for trackpad pinch instead of Ctrl+wheel.
    let gestureScale = 1;
    stage.addEventListener('gesturestart', (event) => {
        event.preventDefault();
        gestureScale = state.scale;
    }, { passive: false, signal: ac.signal });
    stage.addEventListener('gesturechange', (event) => {
        event.preventDefault();
        if (pointers.size > 1) return;
        const anchor = Number.isFinite(event.clientX) && Number.isFinite(event.clientY)
            ? point(event) : { x: state.width / 2, y: state.height / 2 };
        zoom(gestureScale * event.scale, anchor.x, anchor.y);
    }, { passive: false, signal: ac.signal });
    stage.addEventListener('gestureend', event => event.preventDefault(), { passive: false, signal: ac.signal });

    return () => {
        ac.abort();
        observer.disconnect();
        cancelAnimationFrame(frame);
        for (const id of pointers.keys()) {
            if (stage.hasPointerCapture(id)) stage.releasePointerCapture(id);
        }
        pointers.clear();
    };
}

async function mountMermaidPreview(target, source, options = {}) {
    if (!(target instanceof Element)) return false;
    target._previewCleanup?.();
    const surface = document.createElement('div');
    surface.className = `mermaid-preview-surface${options.isModal ? ' is-modal' : ''}`;
    const action = (name, key, fallback, icon) => {
        const label = escapeHtml(getChatPreviewTranslation(key, fallback));
        return `<button type="button" class="mermaid-preview-action" data-mermaid-action="${name}" aria-label="${label}" title="${label}" data-i18n-attr="aria-label:${key};title:${key}">${icon}</button>`;
    };
    const instructions = escapeHtml(getChatPreviewTranslation('code_block_mermaid_navigation', 'Drag to pan. Pinch or Ctrl/⌘ + scroll to zoom. Keyboard: +/− to zoom, arrow keys to pan, Home to fit.'));
    surface.innerHTML = `
        <div class="mermaid-preview-toolbar mermaid-preview-toolbar-top">
            ${options.isModal
                ? action('close-modal', 'files_preview_close_aria', 'Close preview', MARKDOWN_CLOSE_SVG)
                : options.allowExpand !== false ? action('expand', 'code_block_open_large_preview', 'Open large preview', MARKDOWN_EXPAND_PREVIEW_SVG) : ''}
        </div>
        <div class="mermaid-preview-stage" tabindex="0" role="group" aria-label="${instructions}" title="${instructions}" data-i18n-attr="aria-label:code_block_mermaid_navigation;title:code_block_mermaid_navigation">
            <div class="mermaid-preview-canvas"></div>
        </div>
        <div class="mermaid-preview-toolbar mermaid-preview-toolbar-bottom" hidden>
            ${action('zoom-out', 'code_block_zoom_out_aria', 'Zoom out', MARKDOWN_ZOOM_OUT_SVG)}
            <span class="mermaid-preview-zoom-value" aria-live="off">100%</span>
            ${action('zoom-in', 'code_block_zoom_in_aria', 'Zoom in', MARKDOWN_ZOOM_IN_SVG)}
            ${action('reset', 'code_block_reset_zoom_aria', 'Reset zoom', MARKDOWN_RESET_ZOOM_SVG)}
        </div>
    `;
    target.replaceChildren(surface);
    const topToolbar = surface.querySelector('.mermaid-preview-toolbar-top');
    const handleToolbarAction = (event) => {
        const button = event.target.closest('button[data-mermaid-action]');
        if (!button) return;
        event.stopPropagation();
        if (button.dataset.mermaidAction === 'close-modal') closeCodeBlockPreviewModal();
        else openMermaidPreviewModal(surface.closest('.code-block-wrapper'));
    };
    topToolbar.addEventListener('click', handleToolbarAction);
    let disposed = false;
    let disposeViewport = null;
    target._previewCleanup = () => {
        disposed = true;
        disposeViewport?.();
        topToolbar.removeEventListener('click', handleToolbarAction);
    };
    const canvas = surface.querySelector('.mermaid-preview-canvas');
    const rendered = await renderMermaidDiagram(canvas, source);
    if (disposed) return false;
    const svg = canvas.querySelector('svg');
    const box = svg?.viewBox?.baseVal;
    const width = box?.width || parseFloat(svg?.getAttribute('width'));
    const height = box?.height || parseFloat(svg?.getAttribute('height'));
    if (rendered && width > 0 && height > 0) {
        // Leave the SVG at its intrinsic size; only the containing canvas moves.
        svg.style.maxWidth = 'none';
        svg.style.width = `${width}px`;
        svg.style.height = `${height}px`;
        surface.querySelector('.mermaid-preview-toolbar-bottom').hidden = false;
        disposeViewport = bindMermaidPreviewSurface(surface, { width, height });
        return true;
    }
    surface.classList.add('has-error');
    return false;
}

