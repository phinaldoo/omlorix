function startPresentation(config) {
    'use strict';
    const channel = config.channel;
    const mode = config.mode || "present";
    const rendering = mode === "render";
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

    function enterCurrent(previousIndex) {
        const slide = slides[index];
        if (!slide) return;
        if (controller.signal.aborted || rendering) controller = new AbortController();
        slide.querySelectorAll('iframe[data-omlorix-embed-src]').forEach(frame => {
            if (!frame.dataset.omlorixEmbedSrc) return;
            if (rendering) waitUntil(new Promise(resolve => frame.addEventListener('load', resolve, { once: true })));
            frame.src = frame.dataset.omlorixEmbedSrc;
        });
        slide.getAnimations({ subtree: true }).forEach(animation => {
            if (!reducedMotion.matches && animation.playState === 'paused') animation.play();
        });
        slide.dispatchEvent(new CustomEvent('omlorix:slide-enter', {
            bubbles: true, detail: { index, previousIndex, slide, signal: controller.signal, state, reducedMotion: reducedMotion.matches, waitUntil },
        }));
    }

    function goTo(value, { initial = false } = {}) {
        if (rendering) return;
        if (mode === "editor") slides = [...document.querySelectorAll("section.slide")];
        if (!slides.length || !Number.isInteger(Number(value))) return;
        const next = Math.max(0, Math.min(Number(value), slides.length - 1));
        if (next === index && !initial) return;
        const previousIndex = index;
        if (!initial && !document.dispatchEvent(new CustomEvent('omlorix:before-slide-change', {
            cancelable: true, detail: { index: next, previousIndex },
        }))) {
            send('state', { index, count: slides.length });
            return;
        }
        if (!initial) controller.abort();
        if (slides[index]) slides[index].dispatchEvent(new CustomEvent('omlorix:slide-leave', {
            bubbles: true, detail: { index, nextIndex: next, state },
        }));
        finishTransition();
        const outgoing = slides[index];
        index = next;
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
        send('state', { index, count: slides.length });
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
        version: 1, ready, state, goTo, next: () => goTo(index + 1), previous: () => goTo(index - 1),
        fetchJSON, interval, animate, waitUntil, renderReady, mode,
        get index() { return index; }, get count() { return slides.length; },
        get signal() { return controller.signal; }, get reducedMotion() { return reducedMotion.matches; },
    });

    addEventListener('message', event => {
        if (event.source !== parent || event.data?.channel !== channel) return;
        if (event.data.type === 'omlorix-presentation:goto') goTo(event.data.index);
        if (event.data.type === 'omlorix-presentation:focus') slides[index]?.focus();
    });
    document.addEventListener('keydown', event => {
        if (mode !== 'present') return;
        const control = event.target.closest('input,textarea,select,button,a,[role="slider"],[role="button"],[contenteditable="true"]');
        if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key === 'Escape') { event.preventDefault(); send('close'); return; }
        if (event.key === 'Tab') {
            const focusable = [...(slides[index]?.querySelectorAll('button,input,select,textarea,a[href],iframe,[tabindex]') || [])]
                .filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
            if ((event.shiftKey && (event.target === focusable[0] || event.target === slides[index])) || (!event.shiftKey && event.target === focusable.at(-1))) {
                event.preventDefault(); send('focus-host', { backwards: event.shiftKey });
            }
            return;
        }
        if (control) return;
        if (['ArrowRight', 'ArrowDown', 'PageDown', ' '].includes(event.key)) { event.preventDefault(); goTo(index + 1); }
        if (['ArrowLeft', 'ArrowUp', 'PageUp'].includes(event.key)) { event.preventDefault(); goTo(index - 1); }
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
    document.addEventListener('pointerdown', () => send('activity'), { passive: true });
    document.addEventListener('visibilitychange', () => {
        if (rendering) return;
        controller.abort();
        if (document.hidden) {
            slides[index]?.getAnimations({ subtree: true }).forEach(animation => animation.pause());
            slides[index]?.querySelectorAll('audio,video').forEach(media => media.pause());
        } else enterCurrent(index);
    });
    reducedMotion.addEventListener('change', () => {
        if (reducedMotion.matches && !rendering) finishTransition();
        document.dispatchEvent(new CustomEvent('omlorix:motion-change', { detail: { reducedMotion: reducedMotion.matches } }));
    });
    document.addEventListener('DOMContentLoaded', () => {
        slides = [...document.querySelectorAll('section.slide')];
        if (!rendering) slides.forEach(slide => { slide.inert = true; slide.setAttribute('aria-hidden', 'true'); });
        resolveReady(window.OmlorixPresentation);
        // Ready callbacks can attach initial lifecycle listeners before entering.
        queueMicrotask(async () => {
            if (rendering) {
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
            send('ready', { index, count: slides.length });
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
