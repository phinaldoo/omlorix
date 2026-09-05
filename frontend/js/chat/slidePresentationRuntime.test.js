const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

async function runtime(mode = "present", initialize = () => {}) {
    const slides = [0, 1, 2].map(() => {
        const classes = new Set();
        return Object.assign(new EventTarget(), {
            dataset: {}, classList: {
                add(...names) { names.forEach(n => classes.add(n)); },
                remove(...names) { names.forEach(n => classes.delete(n)); },
                toggle(n, on) { if (on) classes.add(n); else classes.delete(n); },
                contains(n) { return classes.has(n); },
            }, contains() { return false; }, setAttribute() {}, removeAttribute() {},
            querySelectorAll() { return []; }, getAnimations() { return []; },
            animate() { throw new Error('The runtime must not invent visual animations'); },
        });
    });
    const document = Object.assign(new EventTarget(), {
        images: [], documentElement: { dataset: {} }, querySelectorAll: () => slides,
    });
    const motion = Object.assign(new EventTarget(), { matches: false });
    const frames = new Map(); let frameId = 0;
    const context = vm.createContext({
        window: { fetch }, parent: { postMessage() {} }, document, matchMedia: () => motion,
        addEventListener() {}, Event, CustomEvent, AbortController, URL, Promise,
        setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask,
        requestAnimationFrame(fn) { frames.set(++frameId, fn); return frameId; },
        cancelAnimationFrame(id) { frames.delete(id); },
    });
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../../../backend/app/tools/slide_presentation/playback.js'), 'utf8'), context);
    context.startPresentation({ channel: 'test', initialIndex: 0, connect: [], mode });
    initialize(context.window.OmlorixPresentation, document, slides);
    document.dispatchEvent(new Event('DOMContentLoaded'));
    await context.window.OmlorixPresentation.ready;
    await Promise.resolve();
    return { api: context.window.OmlorixPresentation, slides, document, frames, motion };
}

test('navigation is immediate without authored motion and awaits cancellable authored transitions', async () => {
    const { api, slides, document, motion } = await runtime();
    api.goTo(1);
    assert.equal(slides[0].classList.contains('omlorix-leaving'), false);
    assert.equal(slides[1].classList.contains('omlorix-entering'), false);
    const transitions = [];
    document.addEventListener('omlorix:transition', event => {
        let finish;
        event.detail.waitUntil(new Promise(resolve => { finish = resolve; }));
        transitions.push({ ...event.detail, finish });
    });
    api.goTo(2);
    assert.equal(slides[1].classList.contains('omlorix-leaving'), true);
    assert.equal(slides[1].inert, true);
    api.goTo(0);
    assert.equal(transitions[0].signal.aborted, true);
    assert.equal(transitions[1].direction, -1);
    transitions[0].finish();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(slides[0].classList.contains('omlorix-entering'), true, 'stale completion cannot finish the next transition');
    transitions[1].finish();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(slides[0].classList.contains('omlorix-entering'), false);
    api.goTo(1);
    motion.matches = true;
    motion.dispatchEvent(new Event('change'));
    assert.equal(transitions[2].signal.aborted, true);
    assert.equal(slides[0].classList.contains('omlorix-leaving'), false);
    transitions[2].finish();
});

test('managed animation can cancel itself without scheduling another frame', async () => {
    const { api, frames } = await runtime();
    let stop;
    stop = api.animate(() => stop());
    const [id, tick] = frames.entries().next().value;
    frames.delete(id);
    tick(100);
    assert.equal(frames.size, 0);
});


test('render mode initializes every live slide and waits for asynchronous content', async () => {
    const entered = [];
    const embed = Object.assign(new EventTarget(), { dataset: { omlorixEmbedSrc: 'https://embed.example.org/page' } });
    let finish;
    const pending = new Promise(resolve => { finish = resolve; });
    const { api, slides, document } = await runtime('render', (api, document, slides) => {
        api.waitUntil(pending);
        slides[0].querySelectorAll = () => [embed];
        slides.forEach(slide => slide.addEventListener('omlorix:slide-enter', event => entered.push(event.detail.index)));
    });
    assert.deepEqual(entered, [0, 1, 2]);
    assert.equal(api.count, 3);
    assert.ok(slides.every(slide => !slide.inert && slide.classList.contains('omlorix-active')));
    assert.notEqual(document.documentElement.dataset.omlorixRenderReady, 'true');
    api.goTo(2);
    assert.equal(api.index, 0);
    finish();
    await Promise.resolve();
    assert.notEqual(document.documentElement.dataset.omlorixRenderReady, 'true');
    assert.equal(embed.src, 'https://embed.example.org/page');
    embed.dispatchEvent(new Event('load'));
    await api.renderReady;
    assert.equal(document.documentElement.dataset.omlorixRenderReady, 'true');
});

test('readiness reports failed initialization and preserves requests started in ready', async () => {
    let signal;
    const { api, document } = await runtime('editor', api => {
        signal = api.signal;
        api.waitUntil(Promise.reject(new Error('API unavailable')));
    });
    assert.equal(signal.aborted, false);
    await assert.rejects(api.renderReady, /API unavailable/);
    assert.equal(document.documentElement.dataset.omlorixRenderReady, 'error');
});
