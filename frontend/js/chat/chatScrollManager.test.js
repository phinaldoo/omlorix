const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readStreamMessagesSource } = require('./messages/source.cjs');
const { readSendMessageSource } = require('./sending/source.cjs');

const managerSource = readFrontendSource(path.join(__dirname, 'chatScrollManager.js'), 'utf8');

/** Minimal DOM element with vertically positioned transcript children. */
class FakeElement {
    constructor(className = '') {
        this.className = className;
        this.children = [];
        this.parentElement = null;
        this.listeners = new Map();
        this.dataset = {};
        this.isConnected = true;
        this.scrollTop = 0;
        this.scrollHeight = 0;
        this.clientHeight = 0;
        this.clientWidth = 600;
        this.offsetWidth = 600;
        this.style = {};
        this.contentTop = 0;
        this.height = 0;
        this.scrollCalls = [];
    }

    matches(selector) {
        const classes = String(selector)
            .split(',')
            .map((part) => part.trim())
            .filter((part) => part.startsWith('.'))
            .map((part) => part.slice(1));
        const ownClasses = new Set(this.className.split(/\s+/).filter(Boolean));
        return classes.some((className) => ownClasses.has(className));
    }

    closest(selector) {
        let current = this;
        while (current) {
            if (current.matches(selector)) return current;
            current = current.parentElement;
        }
        return null;
    }

    appendChild(child) {
        child.parentElement = this;
        this.children.push(child);
        return child;
    }

    querySelector(selector) {
        const visit = (element) => {
            for (const child of element.children) {
                if (child.matches(selector)) return child;
                const nested = visit(child);
                if (nested) return nested;
            }
            return null;
        };
        return visit(this);
    }

    addEventListener(type, listener) {
        const listeners = this.listeners.get(type) || [];
        listeners.push(listener);
        this.listeners.set(type, listeners);
    }

    dispatch(type, event = {}) {
        const payload = {
            target: this,
            defaultPrevented: false,
            ...event,
        };
        (this.listeners.get(type) || []).forEach((listener) => listener(payload));
    }

    getBoundingClientRect() {
        const viewport = this.matches('.chat-area') ? this : this.closest('.chat-area');
        if (this === viewport) {
            return { top: 0, bottom: this.clientHeight, left: 0, right: this.clientWidth };
        }
        const top = this.contentTop - (viewport?.scrollTop || 0);
        return { top, bottom: top + this.height, left: 0, right: this.clientWidth };
    }

    scrollTo(options) {
        this.scrollCalls.push(options);
        this.scrollTop = Number(options?.top) || 0;
    }
}

/** Create the controller with deterministic animation frames. */
function createHarness() {
    let nextFrameId = 1;
    const frames = new Map();
    const documentListeners = new Map();
    const document = {
        documentElement: {
            getAttribute() {
                return 'false';
            },
        },
        addEventListener(type, listener) {
            documentListeners.set(type, listener);
        },
        getElementById() {
            return null;
        },
    };
    const context = {
        Element: FakeElement,
        HTMLElement: FakeElement,
        document,
        window: {
            matchMedia() {
                return { matches: false };
            },
        },
        requestAnimationFrame(callback) {
            const id = nextFrameId++;
            frames.set(id, callback);
            return id;
        },
        cancelAnimationFrame(id) {
            frames.delete(id);
        },
        setTimeout,
        clearTimeout,
    };
    vm.runInNewContext(managerSource, context, { filename: 'chatScrollManager.js' });

    const flushFrame = () => {
        const pending = Array.from(frames.entries());
        frames.clear();
        pending.forEach(([, callback]) => callback(16));
    };
    const flushAllFrames = () => {
        let guard = 10;
        while (frames.size && guard > 0) {
            flushFrame();
            guard -= 1;
        }
    };

    return {
        manager: context.window.ChatScrollManager,
        flushFrame,
        flushAllFrames,
        frames,
    };
}

/** Create a scroll viewport with four ordered top-level message anchors. */
function createTranscript() {
    const viewport = new FakeElement('chat-area');
    viewport.clientHeight = 500;
    viewport.scrollHeight = 2000;
    viewport.scrollTop = 1000;

    const container = new FakeElement('chat-area-container');
    viewport.appendChild(container);
    const messages = [0, 400, 800, 1200].map((contentTop) => {
        const message = new FakeElement('assistant-message-container');
        message.contentTop = contentTop;
        message.height = 400;
        container.appendChild(message);
        return message;
    });
    return { viewport, container, messages };
}

test('direct wheel input cancels stale post-layout restoration frames', () => {
    const { manager, flushAllFrames, frames } = createHarness();
    const { viewport, messages } = createTranscript();

    manager.preserveDuringMutation(messages[0], () => {
        messages.slice(2).forEach((message) => { message.contentTop += 100; });
        viewport.scrollHeight += 100;
    });
    assert.equal(viewport.scrollTop, 1100, 'the immediate correction keeps the visible message anchored');
    assert.ok(frames.size > 0, 'late layout corrections were queued');

    viewport.dispatch('wheel');
    viewport.scrollTop = 700;
    messages.slice(2).forEach((message) => { message.contentTop += 50; });
    flushAllFrames();

    assert.equal(viewport.scrollTop, 700, 'a pre-gesture snapshot never overwrites the user scroll');
    assert.equal(frames.size, 0);
});

test('guarded frame corrections apply only the remaining anchor delta', () => {
    const { manager, flushFrame, flushAllFrames } = createHarness();
    const { viewport, messages } = createTranscript();

    manager.preserveDuringMutation(messages[0], () => {
        messages.slice(2).forEach((message) => { message.contentTop += 100; });
        viewport.scrollHeight += 100;
    });
    assert.equal(viewport.scrollTop, 1100);

    messages.slice(2).forEach((message) => { message.contentTop += 40; });
    viewport.scrollHeight += 40;
    flushFrame();
    assert.equal(viewport.scrollTop, 1140, 'async layout growth is corrected once');

    flushAllFrames();
    assert.equal(viewport.scrollTop, 1140, 'the next frame does not restore the original stale scrollTop');
});

test('hidden assistant versions are excluded before binary anchor selection', () => {
    const { manager } = createHarness();
    const { viewport, container, messages } = createTranscript();
    viewport.scrollTop = 600;

    // A display:none version reports the viewport-origin zero rectangle in real
    // browsers. Placing it at the binary midpoint reproduces the ordering break
    // that previously caused the next response to become the anchor.
    const hiddenVersion = new FakeElement('assistant-message-container');
    hiddenVersion.dataset.hidden = 'true';
    hiddenVersion.style.display = 'none';
    hiddenVersion.getBoundingClientRect = () => ({
        top: 0,
        bottom: 0,
        left: 0,
        right: 0,
    });
    hiddenVersion.getClientRects = () => [];
    hiddenVersion.parentElement = container;
    container.children.splice(2, 0, hiddenVersion);

    const snapshot = manager.capture(messages[1]);
    assert.equal(snapshot.anchor, messages[1], 'the first rendered viewport message remains the anchor');
});

test('unordered rendered rectangles use the visual-order linear fallback', () => {
    const { manager } = createHarness();
    const { viewport, container, messages } = createTranscript();
    viewport.scrollTop = 600;

    // Positioned or transformed children can be rendered outside normal DOM
    // order. This midpoint lies above the viewport and makes a raw binary search
    // skip the partially visible second message.
    const reorderedChild = new FakeElement('assistant-message-container');
    reorderedChild.contentTop = 0;
    reorderedChild.height = 300;
    reorderedChild.parentElement = container;
    container.children.splice(2, 0, reorderedChild);

    const snapshot = manager.capture(messages[1]);
    assert.equal(snapshot.anchor, messages[1], 'fallback chooses the visually first intersecting message');
});

test('continued scrollbar-thumb movement invalidates snapshots captured mid-drag', () => {
    const { manager, flushAllFrames } = createHarness();
    const { viewport, messages } = createTranscript();
    viewport.offsetWidth = 620;
    manager.bind(viewport);

    viewport.dispatch('mousedown', { clientX: 599 });
    manager.preserveDuringMutation(messages[0], () => {
        messages.slice(2).forEach((message) => { message.contentTop += 100; });
        viewport.scrollHeight += 100;
    });
    assert.equal(viewport.scrollTop, 1100);

    viewport.scrollTop = 650;
    viewport.dispatch('scroll');
    messages.slice(2).forEach((message) => { message.contentTop += 50; });
    flushAllFrames();
    assert.equal(viewport.scrollTop, 650);
});

test('auto-follow detaches on input and resumes only after reaching the bottom', () => {
    const { manager, flushAllFrames } = createHarness();
    const { viewport } = createTranscript();
    viewport.style.scrollBehavior = 'smooth';
    viewport.scrollTop = 1500;
    manager.beginStream(viewport, { autoFollow: true });

    viewport.scrollHeight = 2100;
    assert.equal(manager.scheduleFollow(viewport), true);
    viewport.dispatch('wheel');
    viewport.scrollTop = 1200;
    flushAllFrames();
    assert.equal(viewport.scrollTop, 1200);
    assert.equal(manager.isFollowing(viewport), false);

    viewport.scrollTop = 1600;
    viewport.dispatch('scroll');
    assert.equal(manager.isFollowing(viewport), true, 'manually reaching the bottom resumes following');
    viewport.scrollHeight = 2200;
    assert.equal(manager.scheduleFollow(viewport), true);
    flushAllFrames();
    assert.equal(viewport.scrollTop, 1700);
    assert.equal(viewport.scrollCalls.at(-1).behavior, 'auto', 'stream following never starts a CSS smooth animation');
    assert.equal(viewport.style.scrollBehavior, 'smooth', 'the pane style is restored after the correction');
});

test('wheel input stops an in-progress native smooth positioning scroll', () => {
    const { manager, flushAllFrames } = createHarness();
    const { viewport } = createTranscript();

    manager.scrollToPosition(viewport, 1250, { behavior: 'smooth' });
    assert.equal(viewport.scrollCalls[0].top, 1250);
    assert.equal(viewport.scrollCalls[0].behavior, 'smooth');

    viewport.scrollTop = 900;
    viewport.dispatch('wheel');
    assert.equal(viewport.scrollCalls[1].top, 900);
    assert.equal(viewport.scrollCalls[1].behavior, 'auto');
    assert.equal(manager.isFollowing(viewport), false);

    flushAllFrames();
    viewport.scrollTop = 1500;
    viewport.dispatch('scroll');
    assert.equal(manager.isFollowing(viewport), true, 'user scrolling can resume follow after interruption cleanup');
});

test('immediate programmatic positioning at the bottom stays detached from follow', () => {
    const { manager, flushAllFrames } = createHarness();
    const { viewport } = createTranscript();

    manager.scrollToPosition(viewport, 1500);
    viewport.dispatch('scroll');
    assert.equal(
        manager.isFollowing(viewport),
        false,
        'the scroll event caused by positioning must not re-enable auto-follow',
    );

    flushAllFrames();
    assert.equal(manager.isFollowing(viewport), false, 'clearing the marker preserves the positioning contract');
});

test('stream integrations use the shared user-aware controller', () => {
    const indexSource = readFrontendSource(path.join(__dirname, '..', '..', 'index.html'), 'utf8');
    const sendSource = readSendMessageSource();
    const streamSource = readStreamMessagesSource();
    const splitSource = readFrontendSource(path.join(__dirname, 'splitScreen.js'), 'utf8');

    assert.ok(indexSource.indexOf('/js/chat/chatScrollManager.js') < indexSource.indexOf('/js/chat/messages/shared.js'));
    assert.match(sendSource, /ChatScrollManager\.preserveDuringMutation/);
    assert.match(sendSource, /ChatScrollManager\.scheduleFollow\(regenerationViewport\)/);
    assert.match(streamSource, /ChatScrollManager\?\.scheduleFollow\?\.\(scroll\)/);
    assert.match(splitSource, /ChatScrollManager\.scheduleFollow\(area\)/);
    const subagentHandlerStart = streamSource.indexOf('function handleSubagentStreamEvent');
    const persistedBlockStart = streamSource.indexOf('function renderPersistedSubagentBlock');
    assert.notEqual(subagentHandlerStart, -1, 'handleSubagentStreamEvent marker must exist');
    assert.notEqual(persistedBlockStart, -1, 'renderPersistedSubagentBlock marker must exist');
    assert.ok(
        subagentHandlerStart < persistedBlockStart,
        'subagent handler must remain before persisted block rendering',
    );
    assert.doesNotMatch(
        streamSource.slice(subagentHandlerStart, persistedBlockStart),
        /scroll\.scrollTop\s*=\s*scroll\.scrollHeight/
    );
});
