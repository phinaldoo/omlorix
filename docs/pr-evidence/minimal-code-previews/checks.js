// Run await runPreviewChecks() in this proof page's browser console.
async function runPreviewChecks() {
    await window.proofReady;
    const checks = [];
    const check = (condition, name) => { if (!condition) throw new Error(name); checks.push(name); };
    const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const surface = document.querySelector('.mermaid-preview-surface');
    const stage = surface.querySelector('.mermaid-preview-stage');
    const canvas = surface.querySelector('.mermaid-preview-canvas');
    const button = name => surface.querySelector(`[data-mermaid-action="${name}"]`);
    const scale = () => Number(surface.dataset.mermaidScale);
    const matrix = () => new DOMMatrix(getComputedStyle(canvas).transform);
    button('reset').click();
    await settle();
    const initial = scale();
    button('zoom-in').click();
    await settle();
    check(Math.abs(scale() - initial * 1.25) < 1e-8, 'zoom button uses proportional steps');

    const rect = stage.getBoundingClientRect();
    const anchor = { x: rect.width * .45, y: rect.height * .4 };
    const before = matrix();
    const wheel = new WheelEvent('wheel', { deltaY: -60, ctrlKey: true, clientX: rect.left + anchor.x, clientY: rect.top + anchor.y, cancelable: true, bubbles: true });
    anchor.x = wheel.clientX - rect.left;
    anchor.y = wheel.clientY - rect.top;
    const diagramPoint = { x: (anchor.x - before.e) / before.a, y: (anchor.y - before.f) / before.a };
    stage.dispatchEvent(wheel);
    await settle();
    const after = matrix();
    check(wheel.defaultPrevented && Math.abs(after.e + diagramPoint.x * after.a - anchor.x) < .01 && Math.abs(after.f + diagramPoint.y * after.a - anchor.y) < .01, 'wheel zoom preserves the point beneath the cursor');
    const ordinaryWheel = new WheelEvent('wheel', { deltaY: 100, cancelable: true, bubbles: true });
    stage.dispatchEvent(ordinaryWheel);
    check(!ordinaryWheel.defaultPrevented, 'ordinary wheel scrolling is not trapped');

    stage.focus();
    stage.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    await settle();
    check(Math.abs(matrix().e - after.e + 40) < .01, 'keyboard pans the diagram');
    stage.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
    await settle();
    check(Math.abs(scale() - initial) < 1e-8, 'Home restores fit');

    // Synthetic PointerEvents cannot create native pointer capture. Stub only
    // capture in this check; production gesture listeners and math remain real.
    const capture = stage.setPointerCapture;
    stage.setPointerCapture = () => {};
    const pointer = (type, id, x, y) => stage.dispatchEvent(new PointerEvent(type, { pointerId: id, pointerType: 'touch', button: 0, clientX: rect.left + x, clientY: rect.top + y, bubbles: true }));
    try {
        pointer('pointerdown', 1, 100, 100);
        pointer('pointermove', 1, 120, 110);
        await settle();
        check(stage.classList.contains('is-panning'), 'touch drag enters panning');
        pointer('pointerdown', 2, 220, 110);
        const pinchStart = scale();
        pointer('pointermove', 2, 270, 110);
        await settle();
        check(Math.abs(scale() - pinchStart * 1.5) < 1e-8, 'two touch pointers pinch to zoom');
        pointer('pointercancel', 2, 270, 110);
        pointer('pointerup', 1, 120, 110);
        check(!stage.classList.contains('is-panning'), 'cancelled touch gestures release panning');
    } finally { stage.setPointerCapture = capture; }

    const wrapper = surface.closest('.code-block-wrapper');
    const remembered = scale();
    await setCodeBlockView(wrapper, 'code');
    await settle();
    await setCodeBlockView(wrapper, 'preview');
    await settle();
    check(Math.abs(scale() - remembered) < 1e-8, 'source/preview tabs preserve zoom');
    button('expand').focus();
    button('expand').click();
    await settle();
    const dialog = document.querySelector('.code-block-preview-modal');
    check(!!dialog && document.activeElement === dialog, 'fullscreen takes focus');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }));
    check(dialog.contains(document.activeElement) && document.activeElement !== dialog, 'reverse Tab from dialog stays inside');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    check(!document.querySelector('.code-block-preview-modal') && document.activeElement === button('expand'), 'Escape closes fullscreen and restores focus');

    // Real Mermaid rendering of a diagram that needs less than 25% scale.
    const target = document.createElement('div');
    target.className = 'markdown-body';
    target.style.width = '320px';
    document.body.append(target);
    try {
        const source = 'flowchart TB\n' + Array.from({ length: 40 }, (_, i) => `n${i}[Step ${i}] --> n${i + 1}`).join('\n');
        check(await mountMermaidPreview(target, source, { allowExpand: false }), 'large diagram renders');
        await settle();
        const largeSurface = target.querySelector('.mermaid-preview-surface');
        const svgRect = target.querySelector('svg').getBoundingClientRect();
        const stageRect = target.querySelector('.mermaid-preview-stage').getBoundingClientRect();
        check(Number(largeSurface.dataset.mermaidScale) < .25 && svgRect.top >= stageRect.top && svgRect.bottom <= stageRect.bottom && svgRect.left >= stageRect.left && svgRect.right <= stageRect.right, 'large diagram is fully fitted below 25%');
        target.style.width = '260px';
        await settle();
        check(target.querySelector('svg').getBoundingClientRect().width <= 212, 'fitted diagram follows resize');
        const stale = target.querySelector('[data-mermaid-action="zoom-in"]');
        const previous = largeSurface.dataset.mermaidScale;
        target._previewCleanup();
        stale.click();
        await settle();
        check(largeSurface.dataset.mermaidScale === previous, 'cleanup removes zoom handlers');
        const obsolete = mountMermaidPreview(target, source, { allowExpand: false });
        const replacement = mountMermaidPreview(target, 'flowchart LR\n A --> B', { allowExpand: false });
        const results = await Promise.all([obsolete, replacement]);
        check(!results[0] && results[1] && target.querySelectorAll('.mermaid-preview-surface').length === 1, 'reload discards an obsolete asynchronous render');
    } finally { target._previewCleanup?.(); target.remove(); }
    button('reset').click();
    await settle();
    check(document.documentElement.scrollWidth <= innerWidth, 'page has no horizontal overflow');
    return checks;
}
