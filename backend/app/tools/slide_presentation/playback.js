function startPresentation(config) {
    'use strict';
    const channel = config.channel;
    const mode = config.mode || "present";
    const rendering = mode === "render";
    const preview = mode === "preview";
    const previewControllers = new Map();
    let previewSlots = [];
    let previewScrollFrame;
    let previewNavigationIndex = null;
    const pendingRender = [];
    let resolveRender, rejectRender;
    const renderReady = new Promise((resolve, reject) => { resolveRender = resolve; rejectRender = reject; });
    renderReady.catch(() => {});
    window.omlorixPresentationRenderReady = renderReady;
    const waitUntil = promise => {
        const task = Promise.resolve(promise);
        task.catch(() => {});
        pendingRender.push(task);
    };
    const send = (type, detail = {}) => parent.postMessage({ type: `omlorix-presentation:${type}`, channel, ...detail }, '*');
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
    const nativeFetch = window.fetch.bind(window);
    let slides = [];
    let index = -1;
    let transition = 0;
    let transitionTimer;
    let controller = new AbortController();
    let resolveReady;
    const ready = new Promise(resolve => { resolveReady = resolve; });
    const state = Object.create(null);
    let transitionController = new AbortController();
    let hostVisible = true;

    // Each renderer sets its complete DOM state synchronously. Managed animations
    // only decorate that state, so cancellation always reveals the latest result.
    const steps = new Map();
    function navigationState() {
        const entry = steps.get(index);
        return { index, count: slides.length, step: entry?.step || 0, stepCount: entry?.count || 0 };
    }

    function cancelSteps() {
        steps.forEach(entry => entry.controller?.abort());
    }

    function applyStep(slideIndex, value, motion = false) {
        const entry = steps.get(slideIndex);
        if (!entry) return;
        entry.controller?.abort();
        entry.controller = new AbortController();
        const signal = entry.controller.signal;
        const previousStep = entry.step;
        entry.step = Math.max(0, Math.min(value, entry.count));
        const slide = slides[slideIndex];
        const canAnimate = motion && mode === 'present' && !reducedMotion.matches && hostVisible && !document.hidden;
        const detail = {
            slide, index: slideIndex, step: entry.step, previousStep,
            signal, reducedMotion: reducedMotion.matches,
            animate(element, keyframes, options = {}) {
                if (!canAnimate || signal.aborted || !slide.contains(element)) return null;
                const animation = element.animate(keyframes, {
                    ...options, duration: Math.max(0, Math.min(Number(options.duration) || 0, 10000)),
                    delay: 0, endDelay: 0, iterations: 1, fill: 'none',
                });
                const cancel = () => animation.cancel();
                signal.addEventListener('abort', cancel, { once: true });
                animation.finished.then(cancel, () => {}).finally(() => signal.removeEventListener('abort', cancel));
                return animation;
            },
        };
        try { entry.render(detail); }
        catch (error) {
            console.error('Presentation step failed', error);
            if (rendering) waitUntil(Promise.reject(error));
        }
        // If a reveal hid the focused control, return keyboard focus to the slide.
        const focused = document.activeElement;
        if (focused && slide.contains(focused) && (focused.closest('[hidden],[inert],[aria-hidden="true"]')
            || !focused.getClientRects().length || getComputedStyle(focused).visibility === 'hidden')) slide.focus();
        slide.dispatchEvent(new CustomEvent('omlorix:step-change', { bubbles: true, detail }));
    }

    function registerSteps(slideIndex, { count, render, exportStep = count }) {
        if (!Number.isInteger(slideIndex) || !slides[slideIndex] || !Number.isInteger(count)
            || count < 0 || count > 100 || !Number.isInteger(exportStep) || exportStep < 0
            || exportStep > count || typeof render !== 'function') throw new TypeError('invalid_presentation_steps');
        steps.get(slideIndex)?.controller?.abort();
        steps.set(slideIndex, { count, render, exportStep, step: 0 });
        applyStep(slideIndex, mode === 'present' ? 0 : exportStep);
        if (slideIndex === index) send('state', navigationState());
    }

    function advance(direction) {
        if (rendering) return;
        const entry = steps.get(index);
        if (mode === 'present' && entry && (direction > 0 ? entry.step < entry.count : entry.step > 0)) {
            finishTransition();
            applyStep(index, entry.step + direction, true);
            send('state', navigationState());
        } else if (index + direction >= 0 && index + direction < slides.length) {
            goTo(index + direction, { step: direction < 0 ? 'end' : 0 });
        }
    }

    function finishTransition() {
        clearTimeout(transitionTimer);
        transitionController.abort();
        slides.forEach((slide, i) => {
            slide.classList.remove('omlorix-entering', 'omlorix-leaving');
            slide.classList.toggle('omlorix-active', i === index);
            slide.inert = i !== index;
            slide.setAttribute('aria-hidden', String(i !== index));
            if (i !== index) {
                slide.getAnimations({ subtree: true }).forEach(animation => animation.pause());
                slide.querySelectorAll('audio,video').forEach(media => media.pause());
                // Embeds cannot access the runtime lifecycle: unload them on leave.
                slide.querySelectorAll('iframe').forEach(frame => frame.removeAttribute('src'));
            }
        });
    }

    function enterCurrent(previousIndex, currentIndex = index, slideController = controller) {
        if (!rendering && (!hostVisible || document.hidden)) return;
        const slide = slides[currentIndex];
        if (!slide) return;
        if (!preview && (controller.signal.aborted || rendering)) slideController = controller = new AbortController();
        slide.querySelectorAll('iframe[data-omlorix-embed-src]').forEach(frame => {
            if (!frame.dataset.omlorixEmbedSrc) return;
            if (rendering) waitUntil(new Promise(resolve => frame.addEventListener('load', resolve, { once: true })));
            frame.src = frame.dataset.omlorixEmbedSrc;
        });
        slide.getAnimations({ subtree: true }).forEach(animation => {
            if (!reducedMotion.matches && animation.playState === 'paused') animation.play();
        });
        slide.dispatchEvent(new CustomEvent('omlorix:slide-enter', {
            bubbles: true, detail: { index: currentIndex, previousIndex, slide, signal: slideController.signal, state, reducedMotion: reducedMotion.matches, waitUntil },
        }));
        const entry = steps.get(currentIndex);
        if (entry) applyStep(currentIndex, mode === 'present' ? entry.step : entry.exportStep);
    }

    // One document preserves authored state and avoids a runtime per slide.
    // Only visible slides run lifecycle-managed work; all slides stay readable.
    function syncPreview() {
        const viewport = document.body.getBoundingClientRect();
        const bounds = previewSlots.map(slot => slot.getBoundingClientRect());
        const previousIndex = index;
        let nearest = index < 0 ? 0 : index;
        let distance = Infinity;
        bounds.forEach((rect, i) => {
            const nextDistance = Math.abs((rect.top + rect.bottom - viewport.top - viewport.bottom) / 2);
            if (nextDistance < distance) { nearest = i; distance = nextDistance; }
        });
        if (document.body.scrollTop <= 1) nearest = 0;
        else if (document.body.scrollTop + document.body.clientHeight >= document.body.scrollHeight - 1) nearest = slides.length - 1;
        index = previewNavigationIndex ?? nearest;
        bounds.forEach((rect, i) => {
            const visible = hostVisible && !document.hidden && rect.bottom > viewport.top && rect.top < viewport.bottom;
            if (visible && !previewControllers.has(i)) {
                const active = new AbortController();
                previewControllers.set(i, active);
                controller = active;
                enterCurrent(previousIndex, i, active);
            } else if (!visible && previewControllers.has(i)) {
                previewControllers.get(i).abort();
                previewControllers.delete(i);
                slides[i].dispatchEvent(new CustomEvent('omlorix:slide-leave', {
                    bubbles: true, detail: { index: i, nextIndex: index, state },
                }));
                slides[i].getAnimations({ subtree: true }).forEach(animation => animation.pause());
                slides[i].querySelectorAll('audio,video').forEach(media => media.pause());
                slides[i].querySelectorAll('iframe').forEach(frame => frame.removeAttribute('src'));
            }
        });
        controller = previewControllers.get(index) || controller;
        if (index !== previousIndex) send('state', navigationState());
    }

    function scrollPreview(next, behavior = 'auto') {
        const slot = previewSlots[next];
        if (!slot) return;
        previewNavigationIndex = next;
        const viewport = document.body.getBoundingClientRect();
        const rect = slot.getBoundingClientRect();
        document.body.scrollTo({
            top: document.body.scrollTop + rect.top - viewport.top - (viewport.height - rect.height) / 2,
            behavior: reducedMotion.matches ? 'auto' : behavior,
        });
        syncPreview();
    }

    function initializePreview() {
        previewSlots = slides.map(slide => {
            const slot = document.createElement('div');
            slot.className = 'omlorix-preview-slot';
            slide.before(slot);
            slot.appendChild(slide);
            slide.classList.add('omlorix-active');
            slide.inert = false;
            slide.removeAttribute('aria-hidden');
            slide.setAttribute('tabindex', '-1');
            slide.getAnimations({ subtree: true }).forEach(animation => animation.pause());
            return slot;
        });
        const resize = new ResizeObserver(() => {
            previewSlots.forEach(slot => slot.style.setProperty('--omlorix-preview-scale', slot.clientWidth / 1920));
            scrollPreview(Math.max(0, index));
        });
        previewSlots.forEach(slot => {
            slot.style.setProperty('--omlorix-preview-scale', slot.clientWidth / 1920);
            resize.observe(slot);
        });
        resize.observe(document.body);
        document.body.addEventListener('scroll', () => {
            if (previewScrollFrame) return;
            previewScrollFrame = requestAnimationFrame(() => {
                previewScrollFrame = null;
                syncPreview();
            });
        }, { passive: true });
        scrollPreview(config.initialIndex);
    }

    function goTo(value, { initial = false, behavior = 'smooth', step = 0 } = {}) {
        if (rendering) return;
        if (mode === "editor") slides = [...document.querySelectorAll("section.slide")];
        if (!slides.length || !Number.isInteger(Number(value))) return;
        const next = Math.max(0, Math.min(Number(value), slides.length - 1));
        if (preview) { scrollPreview(next, initial ? 'auto' : behavior); return; }
        if (next === index && !initial) {
            const entry = steps.get(index);
            if (mode === 'present' && entry) {
                applyStep(index, step === 'end' ? entry.count : 0);
                send('state', navigationState());
            }
            return;
        }
        const previousIndex = index;
        if (!initial && !document.dispatchEvent(new CustomEvent('omlorix:before-slide-change', {
            cancelable: true, detail: { index: next, previousIndex },
        }))) {
            send('state', navigationState());
            return;
        }
        cancelSteps();
        if (!initial) controller.abort();
        if (slides[index]) slides[index].dispatchEvent(new CustomEvent('omlorix:slide-leave', {
            bubbles: true, detail: { index, nextIndex: next, state },
        }));
        finishTransition();
        const outgoing = slides[index];
        index = next;
        const entry = steps.get(index);
        if (entry) entry.step = mode === 'present' ? (step === 'end' ? entry.count : 0) : entry.exportStep;
        const incoming = slides[index];
        const activeElement = document.activeElement;
        if (outgoing?.contains(activeElement)) activeElement.blur();
        outgoing?.classList.remove('omlorix-active');
        outgoing?.classList.add('omlorix-leaving');
        if (outgoing) { outgoing.inert = true; outgoing.setAttribute('aria-hidden', 'true'); }
        incoming.inert = false;
        incoming.setAttribute('aria-hidden', 'false');
        incoming.classList.add('omlorix-active', 'omlorix-entering');
        incoming.setAttribute('tabindex', '-1');
        const declaredDuration = Number(incoming.dataset.transitionDuration || document.documentElement.dataset.transitionDuration);
        const duration = reducedMotion.matches || initial || !Number.isFinite(declaredDuration)
            ? 0 : Math.max(0, Math.min(declaredDuration, 10000));
        const token = ++transition;
        transitionController = new AbortController();
        const pending = [];
        let accepting = true;
        // The deck supplies every visual effect. The host only coordinates the
        // two visible slides, cancellation and author-declared completion.
        document.dispatchEvent(new CustomEvent('omlorix:transition', { detail: {
            incoming, outgoing, index, previousIndex, direction: next > previousIndex ? 1 : -1,
            initial, reducedMotion: reducedMotion.matches, signal: transitionController.signal,
            waitUntil(promise) { if (accepting) pending.push(Promise.resolve(promise)); },
        } }));
        accepting = false;
        // Observe author promises even when motion is skipped on initial entry.
        const completion = pending.length ? Promise.allSettled(pending) : null;
        enterCurrent(previousIndex);
        send('state', navigationState());
        if (initial || reducedMotion.matches || (!duration && !pending.length)) finishTransition();
        else {
            // Ten seconds is a cleanup ceiling, never a default animation.
            transitionTimer = setTimeout(() => { if (token === transition) finishTransition(); }, pending.length ? 10000 : duration);
            if (completion) {
                const cssFinished = duration ? new Promise(resolve => setTimeout(resolve, duration)) : Promise.resolve();
                Promise.all([completion, cssFinished]).then(() => { if (token === transition) finishTransition(); });
            }
        }
    }

    async function fetchJSON(url, { signal = controller.signal, timeout = 8000 } = {}) {
        const target = new URL(url);
        if (target.protocol !== 'https:' || target.username || target.password || !config.connect.includes(target.origin)) {
            throw new Error('presentation_api_origin_not_allowed');
        }
        const abort = new AbortController();
        const cancel = () => abort.abort();
        if (signal?.aborted) cancel();
        signal?.addEventListener('abort', cancel, { once: true });
        const timer = setTimeout(cancel, Math.max(100, Math.min(timeout, 30000)));
        try {
            const response = await nativeFetch(target.href, { method: 'GET', mode: 'cors', credentials: 'omit', redirect: 'error', signal: abort.signal });
            if (!response.ok) throw new Error('presentation_api_request_failed');
            const reader = response.body.getReader();
            const chunks = [];
            let size = 0;
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                size += value.byteLength;
                if (size > 2 * 1024 * 1024) { await reader.cancel(); throw new Error('presentation_api_response_too_large'); }
                chunks.push(value);
            }
            const bytes = new Uint8Array(size);
            let offset = 0;
            chunks.forEach(chunk => { bytes.set(chunk, offset); offset += chunk.length; });
            return JSON.parse(new TextDecoder().decode(bytes));
        } finally {
            clearTimeout(timer);
            signal?.removeEventListener('abort', cancel);
        }
    }

    function interval(callback, milliseconds, signal = controller.signal) {
        if (signal.aborted) return () => {};
        const timer = setInterval(callback, Math.max(16, milliseconds));
        const cancel = () => { clearInterval(timer); signal.removeEventListener('abort', cancel); };
        signal.addEventListener('abort', cancel, { once: true });
        return cancel;
    }

    function animate(callback, signal = controller.signal) {
        let request;
        let cancelled = false;
        const cancel = () => { cancelled = true; cancelAnimationFrame(request); signal.removeEventListener('abort', cancel); };
        function tick(time) {
            if (signal.aborted || cancelled) return;
            callback(time);
            if (!signal.aborted && !cancelled && !reducedMotion.matches) request = requestAnimationFrame(tick);
        }
        signal.addEventListener('abort', cancel, { once: true });
        if (!signal.aborted) request = requestAnimationFrame(tick);
        return cancel;
    }

    window.OmlorixPresentation = Object.freeze({
        version: 2, ready, state, goTo, registerSteps, next: () => advance(1), previous: () => advance(-1),
        fetchJSON, interval, animate, waitUntil, renderReady, mode,
        get index() { return index; }, get count() { return slides.length; },
        get step() { return steps.get(index)?.step || 0; }, get stepCount() { return steps.get(index)?.count || 0; },
        get signal() { return controller.signal; }, get reducedMotion() { return reducedMotion.matches; },
    });

    addEventListener('message', event => {
        if (event.source !== parent || event.data?.channel !== channel) return;
        if (event.data.type === 'omlorix-presentation:goto') goTo(event.data.index, {
            behavior: event.data.behavior === 'auto' ? 'auto' : 'smooth',
        });
        if (event.data.type === 'omlorix-presentation:advance' && [-1, 1].includes(event.data.direction)) advance(event.data.direction);
        if (event.data.type === 'omlorix-presentation:focus') slides[index]?.focus();
        if (event.data.type === 'omlorix-presentation:visibility' && typeof event.data.visible === 'boolean') {
            const visible = event.data.visible;
            if (visible === hostVisible) return;
            hostVisible = visible;
            cancelSteps();
            if (preview) { syncPreview(); return; }
            controller.abort();
            if (visible && !document.hidden) enterCurrent(index);
            else {
                slides[index]?.getAnimations({ subtree: true }).forEach(animation => animation.pause());
                slides[index]?.querySelectorAll('audio,video').forEach(media => media.pause());
                slides[index]?.querySelectorAll('iframe').forEach(frame => frame.removeAttribute('src'));
            }
        }
    });
    document.addEventListener('keydown', event => {
        if (mode !== 'present' && !preview) return;
        previewNavigationIndex = null;
        const control = event.target.closest('input,textarea,select,button,a,[role="slider"],[role="button"],[role="tab"],[role="listbox"],[role="spinbutton"],[contenteditable]:not([contenteditable="false"])');
        if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key === 'Escape') { event.preventDefault(); send('close'); return; }
        if (event.key === 'Tab') {
            const focusable = [...((preview ? document : slides[index])?.querySelectorAll('button,input,select,textarea,a[href],iframe,[tabindex]') || [])]
                .filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
            if ((event.shiftKey && (event.target === focusable[0] || event.target === slides[index])) || (!event.shiftKey && event.target === focusable.at(-1))) {
                event.preventDefault(); send('focus-host', { backwards: event.shiftKey });
            }
            return;
        }
        if (control) return;
        if (['ArrowRight', 'ArrowDown', 'PageDown', ' '].includes(event.key)) { event.preventDefault(); advance(event.key === ' ' && event.shiftKey ? -1 : 1); }
        if (['ArrowLeft', 'ArrowUp', 'PageUp'].includes(event.key)) { event.preventDefault(); advance(-1); }
        if (event.key === 'Home') { event.preventDefault(); goTo(0); }
        if (event.key === 'End') { event.preventDefault(); goTo(slides.length - 1); }
        if (event.key.toLowerCase() === 'f') send('fullscreen');
    });
    document.addEventListener('click', event => {
        const target = event.target.closest('[data-slide-go]');
        if (target) { event.preventDefault(); goTo(Number(target.dataset.slideGo)); }
    });
    let lastActivity = 0;
    document.addEventListener('pointermove', () => {
        if (performance.now() - lastActivity > 250) { lastActivity = performance.now(); send('activity'); }
    }, { passive: true });
    document.addEventListener('pointerdown', () => {
        previewNavigationIndex = null;
        send('activity', { userInitiated: true });
    }, { passive: true });
    if (preview) {
        ['wheel', 'touchstart', 'keydown'].forEach(type => document.addEventListener(type, () => {
            if (type !== 'keydown') previewNavigationIndex = null;
            send('activity', { userInitiated: true });
        }, { passive: true }));
    }
    document.addEventListener('visibilitychange', () => {
        if (rendering) return;
        if (preview) { syncPreview(); return; }
        cancelSteps();
        controller.abort();
        if (document.hidden) {
            slides[index]?.getAnimations({ subtree: true }).forEach(animation => animation.pause());
            slides[index]?.querySelectorAll('audio,video').forEach(media => media.pause());
        } else if (hostVisible) enterCurrent(index);
    });
    reducedMotion.addEventListener('change', () => {
        cancelSteps();
        if (reducedMotion.matches && !rendering && !preview) finishTransition();
        document.dispatchEvent(new CustomEvent('omlorix:motion-change', { detail: { reducedMotion: reducedMotion.matches } }));
    });
    document.addEventListener('DOMContentLoaded', () => {
        slides = [...document.querySelectorAll('section.slide')];
        if (!rendering) slides.forEach(slide => { slide.inert = true; slide.setAttribute('aria-hidden', 'true'); });
        resolveReady(window.OmlorixPresentation);
        // Ready callbacks can attach initial lifecycle listeners before entering.
        queueMicrotask(async () => {
            if (preview) initializePreview();
            else if (rendering) {
                // Every slide must initialize, including charts created on entry.
                slides.forEach((slide, i) => {
                    index = i;
                    slide.classList.add('omlorix-active');
                    slide.inert = false;
                    slide.removeAttribute('aria-hidden');
                    enterCurrent(-1);
                });
                index = 0;
            } else goTo(config.initialIndex, { initial: true });
            send('ready', navigationState());
            let timer;
            try {
                await Promise.race([
                    (async () => {
                        // Tasks may register further initialization while resolving.
                        for (let offset = 0; offset < pendingRender.length;) {
                            const batch = pendingRender.slice(offset);
                            offset += batch.length;
                            await Promise.all(batch);
                        }
                        await document.fonts?.ready;
                        await Promise.all([...document.images].map(img => img.decode().catch(() => {})));
                    })(),
                    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('presentation_render_timeout')), 30000); }),
                ]);
                document.documentElement.dataset.omlorixRenderReady = 'true';
                resolveRender();
            } catch (error) {
                document.documentElement.dataset.omlorixRenderReady = 'error';
                rejectRender(error);
            } finally { clearTimeout(timer); }
        });
    }, { once: true });
}
