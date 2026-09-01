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

const MERMAID_PREVIEW_MIN_SCALE = 0.25;
const MERMAID_PREVIEW_MAX_SCALE = 4;
const MERMAID_PREVIEW_BUTTON_STEP = 0.15;
const MERMAID_PREVIEW_WHEEL_SENSITIVITY = 0.0025;

function getMermaidPreviewStage(surface) {
    return surface instanceof Element ? surface.querySelector('.mermaid-preview-stage') : null;
}

function getMermaidPreviewCanvas(surface) {
    return surface instanceof Element ? surface.querySelector('.mermaid-preview-canvas') : null;
}

function getMermaidPreviewSvg(surface) {
    const canvas = getMermaidPreviewCanvas(surface);
    return canvas instanceof Element ? canvas.querySelector('svg') : null;
}

function getMermaidPreviewScrollbar(surface, axis) {
    if (!(surface instanceof Element)) {
        return null;
    }
    return surface.querySelector(`.mermaid-preview-scrollbar[data-axis="${axis}"]`);
}

function clampMermaidPreviewScale(value) {
    return Math.max(MERMAID_PREVIEW_MIN_SCALE, Math.min(Number(value) || 1, MERMAID_PREVIEW_MAX_SCALE));
}

function measureMermaidPreviewSvg(svg) {
    if (!(svg instanceof SVGElement)) {
        return null;
    }

    let width = 0;
    let height = 0;

    if (svg.viewBox && Number(svg.viewBox.baseVal?.width) > 0 && Number(svg.viewBox.baseVal?.height) > 0) {
        width = Number(svg.viewBox.baseVal.width);
        height = Number(svg.viewBox.baseVal.height);
    }

    if (!(width > 0 && height > 0)) {
        const widthAttr = parseFloat(svg.getAttribute('width') || '');
        const heightAttr = parseFloat(svg.getAttribute('height') || '');
        if (widthAttr > 0 && heightAttr > 0) {
            width = widthAttr;
            height = heightAttr;
        }
    }

    if (!(width > 0 && height > 0) && typeof svg.getBBox === 'function') {
        try {
            const box = svg.getBBox();
            if (box && box.width > 0 && box.height > 0) {
                width = box.width;
                height = box.height;
            }
        } catch (_) {}
    }

    if (!(width > 0 && height > 0)) {
        const rect = svg.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
            width = rect.width;
            height = rect.height;
        }
    }

    if (!(width > 0 && height > 0)) {
        return null;
    }

    return { width, height };
}

function normalizeMermaidPreviewSvg(surface) {
    const svg = getMermaidPreviewSvg(surface);
    if (!(svg instanceof SVGElement)) {
        return null;
    }

    const metrics = measureMermaidPreviewSvg(svg);
    if (!metrics) {
        return null;
    }

    // Mermaid injects an inline max-width that prevents interactive zoom from
    // enlarging the diagram once the preview reaches its intrinsic width.
    svg.style.maxWidth = 'none';
    svg.setAttribute('width', String(metrics.width));
    svg.setAttribute('height', String(metrics.height));
    return metrics;
}

function ensureMermaidSurfaceMetrics(surface) {
    if (!(surface instanceof Element)) {
        return null;
    }

    const storedWidth = Number(surface.dataset.mermaidBaseWidth || '');
    const storedHeight = Number(surface.dataset.mermaidBaseHeight || '');
    if (storedWidth > 0 && storedHeight > 0) {
        return { width: storedWidth, height: storedHeight };
    }

    const svg = getMermaidPreviewSvg(surface);
    const measured = measureMermaidPreviewSvg(svg);
    if (!measured) {
        return null;
    }

    surface.dataset.mermaidBaseWidth = String(measured.width);
    surface.dataset.mermaidBaseHeight = String(measured.height);
    return measured;
}

function getMermaidStageInnerSize(stage) {
    if (!(stage instanceof Element) || typeof window === 'undefined' || typeof window.getComputedStyle !== 'function') {
        return null;
    }
    const styles = window.getComputedStyle(stage);
    const paddingLeft = parseFloat(styles.paddingLeft || '0') || 0;
    const paddingRight = parseFloat(styles.paddingRight || '0') || 0;
    const paddingTop = parseFloat(styles.paddingTop || '0') || 0;
    const paddingBottom = parseFloat(styles.paddingBottom || '0') || 0;
    return {
        width: Math.max(stage.clientWidth - paddingLeft - paddingRight, 0),
        height: Math.max(stage.clientHeight - paddingTop - paddingBottom, 0),
    };
}

function getMermaidSurfaceFitScale(surface) {
    if (!(surface instanceof Element)) {
        return 1;
    }
    const stage = getMermaidPreviewStage(surface);
    const metrics = ensureMermaidSurfaceMetrics(surface);
    const innerSize = getMermaidStageInnerSize(stage);
    if (!metrics || !innerSize || !(innerSize.width > 0) || !(innerSize.height > 0)) {
        return 1;
    }

    const fitScale = Math.min(innerSize.width / metrics.width, innerSize.height / metrics.height);
    if (!Number.isFinite(fitScale) || fitScale <= 0) {
        return 1;
    }
    return clampMermaidPreviewScale(fitScale);
}

function updateMermaidPreviewScrollbars(surface) {
    if (!(surface instanceof Element)) {
        return;
    }

    const stage = getMermaidPreviewStage(surface);
    const horizontal = getMermaidPreviewScrollbar(surface, 'x');
    const vertical = getMermaidPreviewScrollbar(surface, 'y');
    if (!(stage instanceof Element) || !(horizontal instanceof Element) || !(vertical instanceof Element)) {
        return;
    }

    const horizontalThumb = horizontal.querySelector('.mermaid-preview-scrollbar-thumb');
    const verticalThumb = vertical.querySelector('.mermaid-preview-scrollbar-thumb');
    if (!(horizontalThumb instanceof HTMLElement) || !(verticalThumb instanceof HTMLElement)) {
        return;
    }

    const maxScrollLeft = Math.max(stage.scrollWidth - stage.clientWidth, 0);
    const maxScrollTop = Math.max(stage.scrollHeight - stage.clientHeight, 0);
    const hasHorizontalOverflow = maxScrollLeft > 1;
    const hasVerticalOverflow = maxScrollTop > 1;

    surface.classList.toggle('has-mermaid-scroll-x', hasHorizontalOverflow);
    surface.classList.toggle('has-mermaid-scroll-y', hasVerticalOverflow);

    horizontal.hidden = !hasHorizontalOverflow;
    vertical.hidden = !hasVerticalOverflow;

    if (hasHorizontalOverflow) {
        const trackWidth = horizontal.clientWidth;
        const rawThumbWidth = (stage.clientWidth / stage.scrollWidth) * trackWidth;
        const thumbWidth = Math.min(Math.max(rawThumbWidth, 40), trackWidth);
        const maxThumbLeft = Math.max(trackWidth - thumbWidth, 0);
        const left = Math.min(
            Math.max(maxScrollLeft > 0 ? (stage.scrollLeft / maxScrollLeft) * maxThumbLeft : 0, 0),
            maxThumbLeft
        );
        horizontalThumb.style.width = `${thumbWidth}px`;
        horizontalThumb.style.transform = `translateX(${left}px)`;
    } else {
        horizontalThumb.style.width = '';
        horizontalThumb.style.transform = '';
    }

    if (hasVerticalOverflow) {
        const trackHeight = vertical.clientHeight;
        const rawThumbHeight = (stage.clientHeight / stage.scrollHeight) * trackHeight;
        const thumbHeight = Math.min(Math.max(rawThumbHeight, 40), trackHeight);
        const maxThumbTop = Math.max(trackHeight - thumbHeight, 0);
        const top = Math.min(
            Math.max(maxScrollTop > 0 ? (stage.scrollTop / maxScrollTop) * maxThumbTop : 0, 0),
            maxThumbTop
        );
        verticalThumb.style.height = `${thumbHeight}px`;
        verticalThumb.style.transform = `translateY(${top}px)`;
    } else {
        verticalThumb.style.height = '';
        verticalThumb.style.transform = '';
    }
}

function updateMermaidPreviewViewport(surface, nextScale, options = {}) {
    if (!(surface instanceof Element)) {
        return;
    }

    const stage = getMermaidPreviewStage(surface);
    const canvas = getMermaidPreviewCanvas(surface);
    const svg = getMermaidPreviewSvg(surface);
    const metrics = ensureMermaidSurfaceMetrics(surface);
    const innerSize = getMermaidStageInnerSize(stage);
    const clampedScale = clampMermaidPreviewScale(nextScale);
    const previousScale = clampMermaidPreviewScale(surface.dataset.mermaidScale || 1);
    const previousOffsetX = Number(surface.dataset.mermaidOffsetX || '0') || 0;
    const previousOffsetY = Number(surface.dataset.mermaidOffsetY || '0') || 0;

    surface.dataset.mermaidScale = clampedScale.toFixed(3);
    surface.style.setProperty('--mermaid-preview-scale', String(clampedScale));

    const value = surface.querySelector('.mermaid-preview-zoom-value');
    if (value) {
        value.textContent = `${Math.round(clampedScale * 100)}%`;
    }

    if (!(stage instanceof Element) || !(canvas instanceof Element) || !(svg instanceof SVGElement) || !metrics || !innerSize) {
        return;
    }

    const stageRect = stage.getBoundingClientRect();
    const anchorViewportX = Number.isFinite(options.anchorClientX)
        ? options.anchorClientX - stageRect.left
        : stage.clientWidth / 2;
    const anchorViewportY = Number.isFinite(options.anchorClientY)
        ? options.anchorClientY - stageRect.top
        : stage.clientHeight / 2;
    const normalizedAnchorX = Math.max(0, Math.min(anchorViewportX, stage.clientWidth));
    const normalizedAnchorY = Math.max(0, Math.min(anchorViewportY, stage.clientHeight));

    const contentX = stage.scrollLeft + normalizedAnchorX;
    const contentY = stage.scrollTop + normalizedAnchorY;
    const diagramX = previousScale > 0 ? Math.max(contentX - previousOffsetX, 0) / previousScale : 0;
    const diagramY = previousScale > 0 ? Math.max(contentY - previousOffsetY, 0) / previousScale : 0;

    const scaledWidth = metrics.width * clampedScale;
    const scaledHeight = metrics.height * clampedScale;
    const nextOffsetX = Math.max((innerSize.width - scaledWidth) / 2, 0);
    const nextOffsetY = Math.max((innerSize.height - scaledHeight) / 2, 0);

    canvas.style.width = `${scaledWidth}px`;
    canvas.style.height = `${scaledHeight}px`;
    canvas.style.margin = `${nextOffsetY}px ${nextOffsetX}px`;
    svg.style.width = `${scaledWidth}px`;
    svg.style.height = `${scaledHeight}px`;
    surface.dataset.mermaidOffsetX = String(nextOffsetX);
    surface.dataset.mermaidOffsetY = String(nextOffsetY);

    const applyScroll = () => {
        const maxScrollLeft = Math.max(stage.scrollWidth - stage.clientWidth, 0);
        const maxScrollTop = Math.max(stage.scrollHeight - stage.clientHeight, 0);

        if (options.resetViewport) {
            stage.scrollLeft = 0;
            stage.scrollTop = 0;
            updateMermaidPreviewScrollbars(surface);
            return;
        }

        const nextScrollLeft = nextOffsetX + (diagramX * clampedScale) - normalizedAnchorX;
        const nextScrollTop = nextOffsetY + (diagramY * clampedScale) - normalizedAnchorY;
        stage.scrollLeft = Math.min(Math.max(nextScrollLeft, 0), maxScrollLeft);
        stage.scrollTop = Math.min(Math.max(nextScrollTop, 0), maxScrollTop);
        updateMermaidPreviewScrollbars(surface);
    };

    applyScroll();
    if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(applyScroll);
    }
}

function setMermaidSurfaceScale(surface, nextScale, options = {}) {
    updateMermaidPreviewViewport(surface, nextScale, options);
}

function resetMermaidSurfaceViewport(surface) {
    setMermaidSurfaceScale(surface, getMermaidSurfaceFitScale(surface), { resetViewport: true });
}

function bindMermaidPreviewScrollbar(surface, axis, ac) {
    const stage = getMermaidPreviewStage(surface);
    const scrollbar = getMermaidPreviewScrollbar(surface, axis);
    const thumb = scrollbar instanceof Element
        ? scrollbar.querySelector('.mermaid-preview-scrollbar-thumb')
        : null;
    if (!(stage instanceof Element) || !(scrollbar instanceof HTMLElement) || !(thumb instanceof HTMLElement)) {
        return;
    }

    const startDrag = (event) => {
        if (event.button !== 0) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();

        const trackRect = scrollbar.getBoundingClientRect();
        const thumbRect = thumb.getBoundingClientRect();
        const maxScroll = axis === 'x'
            ? Math.max(stage.scrollWidth - stage.clientWidth, 0)
            : Math.max(stage.scrollHeight - stage.clientHeight, 0);
        const trackSize = axis === 'x' ? trackRect.width : trackRect.height;
        const thumbSize = axis === 'x' ? thumbRect.width : thumbRect.height;
        const maxThumbOffset = Math.max(trackSize - thumbSize, 0);
        const pointerOffsetInThumb = event.target === thumb
            ? ((axis === 'x' ? event.clientX - thumbRect.left : event.clientY - thumbRect.top))
            : thumbSize / 2;

        const move = (moveEvent) => {
            const pointerOffsetInTrack = axis === 'x'
                ? moveEvent.clientX - trackRect.left
                : moveEvent.clientY - trackRect.top;
            const nextThumbOffset = Math.min(
                Math.max(pointerOffsetInTrack - pointerOffsetInThumb, 0),
                maxThumbOffset
            );
            const nextProgress = maxThumbOffset > 0 ? nextThumbOffset / maxThumbOffset : 0;
            const nextScroll = nextProgress * maxScroll;
            if (axis === 'x') {
                stage.scrollLeft = nextScroll;
            } else {
                stage.scrollTop = nextScroll;
            }
            updateMermaidPreviewScrollbars(surface);
        };

        const stop = () => {
            window.removeEventListener('pointermove', move);
            window.removeEventListener('pointerup', stop);
            window.removeEventListener('pointercancel', stop);
        };

        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', stop, { once: true });
        window.addEventListener('pointercancel', stop, { once: true });
        move(event);
    };

    scrollbar.addEventListener('pointerdown', startDrag, { signal: ac.signal });
}

function bindMermaidPreviewSurface(surface, { allowExpand = true } = {}) {
    if (!(surface instanceof Element) || surface.dataset.boundMermaidPreviewSurface === 'true') {
        return;
    }
    surface.dataset.boundMermaidPreviewSurface = 'true';
    const stage = getMermaidPreviewStage(surface);
    const ac = new AbortController();
    surface._mermaidPreviewAbortController = ac;
    const resizeObserver = typeof ResizeObserver === 'function'
        ? new ResizeObserver(() => {
            updateMermaidPreviewViewport(surface, Number(surface.dataset.mermaidScale || 1));
            updateMermaidPreviewScrollbars(surface);
        })
        : null;
    if (resizeObserver && stage instanceof Element) {
        resizeObserver.observe(stage);
        surface._mermaidPreviewResizeObserver = resizeObserver;
    }

    surface.addEventListener('click', (event) => {
        const actionButton = event.target instanceof Element
            ? event.target.closest('.mermaid-preview-action')
            : null;
        if (!(actionButton instanceof HTMLButtonElement)) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        const action = actionButton.dataset.mermaidAction || '';
        const currentScale = Number(surface.dataset.mermaidScale || 1);
        if (action === 'zoom-in') {
            setMermaidSurfaceScale(surface, currentScale + MERMAID_PREVIEW_BUTTON_STEP);
            return;
        }
        if (action === 'zoom-out') {
            setMermaidSurfaceScale(surface, currentScale - MERMAID_PREVIEW_BUTTON_STEP);
            return;
        }
        if (action === 'reset') {
            resetMermaidSurfaceViewport(surface);
            return;
        }
        if (action === 'close-modal') {
            closeCodeBlockPreviewModal();
            return;
        }
        if (action === 'expand' && allowExpand) {
            const wrapper = surface.closest('.code-block-wrapper');
            if (wrapper) {
                openMermaidPreviewModal(wrapper);
            }
        }
    }, { signal: ac.signal });

    if (stage instanceof Element) {
        stage.addEventListener('scroll', () => {
            updateMermaidPreviewScrollbars(surface);
        }, { passive: true, signal: ac.signal });

        stage.addEventListener('wheel', (event) => {
            if (!event.ctrlKey) {
                return;
            }
            event.preventDefault();
            const currentScale = Number(surface.dataset.mermaidScale || 1);
            const nextScale = currentScale * Math.exp(-event.deltaY * MERMAID_PREVIEW_WHEEL_SENSITIVITY);
            setMermaidSurfaceScale(surface, nextScale, {
                anchorClientX: event.clientX,
                anchorClientY: event.clientY,
            });
        }, { passive: false, signal: ac.signal });

        let gestureStartScale = null;
        stage.addEventListener('gesturestart', (event) => {
            gestureStartScale = Number(surface.dataset.mermaidScale || 1);
            event.preventDefault();
        }, { passive: false, signal: ac.signal });
        stage.addEventListener('gesturechange', (event) => {
            if (!Number.isFinite(gestureStartScale)) {
                gestureStartScale = Number(surface.dataset.mermaidScale || 1);
            }
            event.preventDefault();
            setMermaidSurfaceScale(surface, gestureStartScale * Number(event.scale || 1), {
                anchorClientX: Number.isFinite(event.clientX) ? event.clientX : undefined,
                anchorClientY: Number.isFinite(event.clientY) ? event.clientY : undefined,
            });
        }, { passive: false, signal: ac.signal });
        stage.addEventListener('gestureend', () => {
            gestureStartScale = null;
        }, { signal: ac.signal });
    }

    bindMermaidPreviewScrollbar(surface, 'x', ac);
    bindMermaidPreviewScrollbar(surface, 'y', ac);
}

async function mountMermaidPreview(target, source, options = {}) {
    if (!(target instanceof Element)) {
        return false;
    }
    if (typeof target._previewCleanup === 'function') {
        target._previewCleanup();
        delete target._previewCleanup;
    }
    const surface = document.createElement('div');
    surface.className = `mermaid-preview-surface${options.isModal ? ' is-modal' : ''}`;
    // Resolve accessibility copy before building the toolbar so dynamically-created
    // preview controls are translated immediately, including before an i18n reapply.
    const closePreviewLabel = getChatPreviewTranslation('files_preview_close_aria', 'Close preview');
    const expandPreviewLabel = getChatPreviewTranslation('code_block_open_large_preview', 'Open large preview');
    const zoomOutLabel = getChatPreviewTranslation('code_block_zoom_out_aria', 'Zoom out');
    const zoomInLabel = getChatPreviewTranslation('code_block_zoom_in_aria', 'Zoom in');
    const resetZoomLabel = getChatPreviewTranslation('code_block_reset_zoom_aria', 'Reset zoom');
    surface.innerHTML = `
        <div class="mermaid-preview-toolbar mermaid-preview-toolbar-top">
            ${options.isModal
                ? `<button type="button" class="mermaid-preview-action" data-mermaid-action="close-modal" aria-label="${escapeHtml(closePreviewLabel)}" title="${escapeHtml(closePreviewLabel)}" data-i18n-attr="aria-label:files_preview_close_aria;title:files_preview_close_aria">${MARKDOWN_CLOSE_SVG}</button>`
                : (options.allowExpand !== false
                    ? `<button type="button" class="mermaid-preview-action" data-mermaid-action="expand" aria-label="${escapeHtml(expandPreviewLabel)}" title="${escapeHtml(expandPreviewLabel)}" data-i18n-attr="aria-label:code_block_open_large_preview;title:code_block_open_large_preview">${MARKDOWN_EXPAND_PREVIEW_SVG}</button>`
                    : '')}
        </div>
        <div class="mermaid-preview-stage">
            <div class="mermaid-preview-canvas"></div>
        </div>
        <div class="mermaid-preview-scrollbar mermaid-preview-scrollbar-x" data-axis="x" hidden>
            <div class="mermaid-preview-scrollbar-thumb"></div>
        </div>
        <div class="mermaid-preview-scrollbar mermaid-preview-scrollbar-y" data-axis="y" hidden>
            <div class="mermaid-preview-scrollbar-thumb"></div>
        </div>
        <div class="mermaid-preview-toolbar mermaid-preview-toolbar-bottom">
            <button type="button" class="mermaid-preview-action" data-mermaid-action="zoom-out" aria-label="${escapeHtml(zoomOutLabel)}" title="${escapeHtml(zoomOutLabel)}" data-i18n-attr="aria-label:code_block_zoom_out_aria;title:code_block_zoom_out_aria">${MARKDOWN_ZOOM_OUT_SVG}</button>
            <span class="mermaid-preview-zoom-value">100%</span>
            <button type="button" class="mermaid-preview-action" data-mermaid-action="zoom-in" aria-label="${escapeHtml(zoomInLabel)}" title="${escapeHtml(zoomInLabel)}" data-i18n-attr="aria-label:code_block_zoom_in_aria;title:code_block_zoom_in_aria">${MARKDOWN_ZOOM_IN_SVG}</button>
            <button type="button" class="mermaid-preview-action" data-mermaid-action="reset" aria-label="${escapeHtml(resetZoomLabel)}" title="${escapeHtml(resetZoomLabel)}" data-i18n-attr="aria-label:code_block_reset_zoom_aria;title:code_block_reset_zoom_aria">${MARKDOWN_RESET_ZOOM_SVG}</button>
        </div>
    `;
    target.innerHTML = '';
    target.appendChild(surface);
    bindMermaidPreviewSurface(surface, { allowExpand: options.allowExpand !== false });
    target._previewCleanup = () => {
        if (surface._mermaidPreviewAbortController) {
            surface._mermaidPreviewAbortController.abort();
            delete surface._mermaidPreviewAbortController;
        }
        if (surface._mermaidPreviewResizeObserver) {
            surface._mermaidPreviewResizeObserver.disconnect();
            delete surface._mermaidPreviewResizeObserver;
        }
    };

    const canvas = surface.querySelector('.mermaid-preview-canvas');
    const rendered = await renderMermaidDiagram(canvas, source);
    if (rendered) {
        const normalizedMetrics = normalizeMermaidPreviewSvg(surface);
        if (normalizedMetrics) {
            surface.dataset.mermaidBaseWidth = String(normalizedMetrics.width);
            surface.dataset.mermaidBaseHeight = String(normalizedMetrics.height);
        } else {
            ensureMermaidSurfaceMetrics(surface);
        }
        const initialScale = Number.isFinite(options.initialScale)
            ? options.initialScale
            : getMermaidSurfaceFitScale(surface);
        setMermaidSurfaceScale(surface, initialScale, { resetViewport: true });
        updateMermaidPreviewScrollbars(surface);
    } else {
        surface.classList.add('has-error');
    }
    return rendered;
}

