const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

const { createChatScrollCoordinator } = require('./chatScroll.js');

class FakeEventTarget {
    constructor() {
        this.listeners = new Map();
    }

    addEventListener(type, listener) {
        if (!this.listeners.has(type)) {
            this.listeners.set(type, []);
        }
        this.listeners.get(type).push(listener);
    }

    removeEventListener(type, listener) {
        const listeners = this.listeners.get(type) || [];
        this.listeners.set(type, listeners.filter((candidate) => candidate !== listener));
    }

    dispatch(type, event = {}) {
        const normalizedEvent = { type, ...event };
        (this.listeners.get(type) || []).forEach((listener) => listener(normalizedEvent));
    }
}

function createClassList() {
    const values = new Set();
    return {
        add(...names) {
            names.forEach((name) => values.add(name));
        },
        remove(...names) {
            names.forEach((name) => values.delete(name));
        },
        contains(name) {
            return values.has(name);
        },
        set(value) {
            values.clear();
            String(value || '').split(/\s+/).filter(Boolean).forEach((name) => values.add(name));
        },
    };
}

class FakeElement extends FakeEventTarget {
    constructor() {
        super();
        this.classList = createClassList();
        this.children = [];
        this.id = '';
        this.parentElement = null;
        this.style = {};
    }

    set className(value) {
        this._className = String(value || '');
        this.classList.set(value);
    }

    get className() {
        return this._className || '';
    }

    setAttribute() {}

    appendChild(child) {
        if (child.parentElement) {
            child.parentElement.children = child.parentElement.children
                .filter((candidate) => candidate !== child);
        }
        this.children.push(child);
        child.parentElement = this;
        return child;
    }

    get lastElementChild() {
        return this.children.at(-1) || null;
    }

    contains(candidate) {
        if (candidate === this) {
            return true;
        }
        return this.children.some((child) => child === candidate || child.contains?.(candidate));
    }

    remove() {
        if (!this.parentElement) {
            return;
        }
        this.parentElement.children = this.parentElement.children
            .filter((candidate) => candidate !== this);
        this.parentElement = null;
    }

    get offsetHeight() {
        return Number.parseFloat(this.style.height || '0') || 0;
    }

    getBoundingClientRect() {
        return { height: 0, left: 0, top: 0, width: 0 };
    }
}

class FakeMessageArea extends FakeElement {
    constructor(viewport, offsetTop, height = 60) {
        super();
        this.height = height;
        this.offsetTop = offsetTop;
        this.viewport = viewport;
    }

    getBoundingClientRect() {
        return {
            height: this.height,
            left: 0,
            top: this.offsetTop - this.viewport.scrollTop,
            width: 500,
        };
    }
}

class FakeMessageContent extends FakeElement {
    constructor(messageId, area) {
        super();
        this.id = `u-${messageId}`;
        this.area = area;
        this.parentElement = area;
    }

    closest(selector) {
        return selector === '.user-message-area' ? this.area : null;
    }
}

class FakeContainer extends FakeElement {
    constructor(naturalScrollHeight) {
        super();
        this.messages = new Map();
        this.naturalScrollHeight = naturalScrollHeight;
    }

    addMessage(messageId, area) {
        const content = new FakeMessageContent(messageId, area);
        area.content = content;
        this.messages.set(messageId, content);
        this.appendChild(area);
        return content;
    }

    querySelector(selector) {
        if (selector === '.dynamic-scroll-spacer') {
            return this.children.find((child) => child.classList.contains('dynamic-scroll-spacer')) || null;
        }
        if (selector.startsWith('#u-')) {
            return this.messages.get(selector.slice(3)) || null;
        }
        return null;
    }

    querySelectorAll(selector) {
        if (selector === '[id]') {
            return Array.from(this.messages.values());
        }
        if (selector === 'img') {
            return [];
        }
        return [];
    }
}

class FakeViewport extends FakeElement {
    constructor(container, clientHeight = 500) {
        super();
        this.clientHeight = clientHeight;
        this.container = container;
        this.scrollCalls = [];
        this._scrollTop = 0;
    }

    get scrollHeight() {
        const spacer = this.container.querySelector('.dynamic-scroll-spacer');
        return this.container.naturalScrollHeight + (spacer?.offsetHeight || 0);
    }

    get scrollTop() {
        return this._scrollTop;
    }

    set scrollTop(value) {
        const maximum = Math.max(0, this.scrollHeight - this.clientHeight);
        this._scrollTop = Math.min(maximum, Math.max(0, Number(value) || 0));
        this.dispatch('scroll');
    }

    scrollTo({ top, behavior }) {
        this.scrollCalls.push({ behavior, top });
        this.scrollTop = top;
    }

    getBoundingClientRect() {
        return {
            height: this.clientHeight,
            left: 0,
            top: 0,
            width: 500,
        };
    }
}

class FakeClock {
    constructor() {
        this.nextId = 1;
        this.now = 0;
        this.tasks = new Map();
    }

    schedule(callback, delay, frame = false) {
        const id = this.nextId;
        this.nextId += 1;
        this.tasks.set(id, {
            callback,
            frame,
            time: this.now + Math.max(0, Number(delay) || 0),
        });
        return id;
    }

    cancel(id) {
        this.tasks.delete(id);
    }

    advance(milliseconds) {
        const end = this.now + milliseconds;
        while (true) {
            const next = Array.from(this.tasks.entries())
                .filter(([, task]) => task.time <= end)
                .sort((left, right) => left[1].time - right[1].time || left[0] - right[0])[0];
            if (!next) {
                break;
            }
            const [id, task] = next;
            this.tasks.delete(id);
            this.now = task.time;
            task.callback(task.frame ? this.now : undefined);
        }
        this.now = end;
    }
}

function createScrollFixture({
    initialScrollTop = 700,
    messageOffsetTop = 1500,
    naturalScrollHeight = 1800,
    reduceMotion = false,
} = {}) {
    const clock = new FakeClock();
    const globalEvents = new FakeEventTarget();
    const observers = [];
    const container = new FakeContainer(naturalScrollHeight);
    const viewport = new FakeViewport(container);
    const messageArea = new FakeMessageArea(viewport, messageOffsetTop);
    container.addMessage('message-1', messageArea);
    viewport.scrollTop = initialScrollTop;

    class FakeResizeObserver {
        constructor(callback) {
            this.callback = callback;
            this.disconnected = false;
            observers.push(this);
        }

        observe() {}

        disconnect() {
            this.disconnected = true;
        }
    }

    const documentElement = {};
    const runtime = {
        CSS: { escape: (value) => value },
        HTMLElement: FakeElement,
        ResizeObserver: FakeResizeObserver,
        addEventListener: globalEvents.addEventListener.bind(globalEvents),
        cancelAnimationFrame: (id) => clock.cancel(id),
        clearTimeout: (id) => clock.cancel(id),
        document: {
            createElement: () => new FakeElement(),
            documentElement,
            getElementById(id) {
                if (id === 'chatArea') return viewport;
                if (id === 'chatAreaContainer') return container;
                return null;
            },
        },
        matchMedia: () => ({ matches: reduceMotion }),
        performance: { now: () => clock.now },
        requestAnimationFrame: (callback) => clock.schedule(callback, 16, true),
        setTimeout: (callback, delay) => clock.schedule(callback, delay),
    };
    const coordinator = createChatScrollCoordinator(runtime);

    return {
        clock,
        container,
        coordinator,
        globalEvents,
        messageArea,
        observers,
        viewport,
    };
}

function assertMessageAligned(fixture, area = fixture.messageArea) {
    const messageTop = area.getBoundingClientRect().top;
    assert.ok(Math.abs(messageTop) <= 1, `expected prompt at viewport top, got ${messageTop}px`);
}

test('aligns a new prompt from far above the bottom and keeps the spacer during programmatic scroll events', () => {
    const fixture = createScrollFixture();

    assert.equal(fixture.coordinator.alignUserMessage('message-1'), true);
    fixture.clock.advance(120);

    const spacer = fixture.container.querySelector('.dynamic-scroll-spacer');
    assert.ok(spacer.offsetHeight > 0, 'the target should be made reachable with temporary space');
    assert.equal(fixture.coordinator.isAligning(fixture.viewport), true);

    fixture.clock.advance(400);
    assertMessageAligned(fixture);
    assert.equal(spacer.offsetHeight, 200);
});

test('remeasures and corrects a delayed layout shift above the prompt', () => {
    const fixture = createScrollFixture();
    fixture.coordinator.alignUserMessage('message-1');
    fixture.clock.advance(500);
    assertMessageAligned(fixture);

    fixture.messageArea.offsetTop += 180;
    fixture.container.naturalScrollHeight += 180;
    fixture.observers.forEach((observer) => observer.callback());
    fixture.clock.advance(20);

    assertMessageAligned(fixture);
    assert.equal(fixture.viewport.scrollTop, 1680);
});

test('guard heartbeat catches transition-driven reflow even without an observer callback', () => {
    const fixture = createScrollFixture();
    fixture.coordinator.alignUserMessage('message-1');
    fixture.clock.advance(500);

    fixture.messageArea.offsetTop += 75;
    fixture.container.naturalScrollHeight += 75;
    fixture.clock.advance(120);

    assertMessageAligned(fixture);
    assert.equal(fixture.viewport.scrollTop, 1575);
});

test('remeasures when the chat viewport changes height after composer or keyboard resizing', () => {
    const fixture = createScrollFixture();
    fixture.coordinator.alignUserMessage('message-1');
    fixture.clock.advance(500);

    fixture.viewport.clientHeight = 400;
    fixture.observers.forEach((observer) => observer.callback());
    fixture.clock.advance(20);

    assertMessageAligned(fixture);
    assert.equal(fixture.container.querySelector('.dynamic-scroll-spacer').offsetHeight, 100);
});

test('real wheel input cancels alignment and no later frame overrides the user', () => {
    const fixture = createScrollFixture();
    fixture.coordinator.alignUserMessage('message-1');
    fixture.clock.advance(100);

    fixture.viewport.dispatch('wheel');
    fixture.viewport.scrollTop = 400;
    fixture.clock.advance(1000);

    assert.equal(fixture.coordinator.isAligning(fixture.viewport), false);
    assert.equal(fixture.viewport.scrollTop, 400);
});

test('user navigation removes spacer only after removal cannot clamp the viewport', () => {
    const fixture = createScrollFixture();
    fixture.coordinator.alignUserMessage('message-1');
    fixture.clock.advance(500);
    const spacer = fixture.container.querySelector('.dynamic-scroll-spacer');
    assert.equal(spacer.offsetHeight, 200);

    fixture.viewport.dispatch('wheel');
    fixture.viewport.scrollTop = 1400;
    fixture.clock.advance(20);
    assert.equal(spacer.offsetHeight, 200, 'space remains while it still supports the current position');

    fixture.viewport.scrollTop = 1250;
    fixture.clock.advance(20);
    assert.equal(spacer.offsetHeight, 0, 'space disappears once natural content supports the position');
    assert.equal(fixture.viewport.scrollTop, 1250);
});

test('a newer send supersedes an older alignment transaction', () => {
    const fixture = createScrollFixture({ naturalScrollHeight: 2200 });
    const secondArea = new FakeMessageArea(fixture.viewport, 2050);
    fixture.container.addMessage('message-2', secondArea);

    fixture.coordinator.alignUserMessage('message-1');
    fixture.clock.advance(100);
    fixture.coordinator.alignUserMessage('message-2');
    fixture.clock.advance(500);

    assertMessageAligned(fixture, secondArea);
    assert.ok(fixture.messageArea.getBoundingClientRect().top < -400);
});

test('an older failed send cannot cancel the newer prompt alignment', () => {
    const fixture = createScrollFixture({ naturalScrollHeight: 2200 });
    const secondArea = new FakeMessageArea(fixture.viewport, 2050);
    fixture.container.addMessage('message-2', secondArea);

    fixture.coordinator.alignUserMessage('message-2');
    fixture.clock.advance(100);
    const cancelled = fixture.coordinator.cancel(fixture.viewport, {
        container: fixture.container,
        messageId: 'message-1',
        removeSpacer: true,
    });
    fixture.clock.advance(500);

    assert.equal(cancelled, false);
    assert.equal(fixture.coordinator.isAligning(fixture.viewport), true);
    assertMessageAligned(fixture, secondArea);
    assert.ok(fixture.container.querySelector('.dynamic-scroll-spacer'));
});

test('reduced-motion preference aligns immediately without a smooth animation', () => {
    const fixture = createScrollFixture({ reduceMotion: true });
    fixture.coordinator.alignUserMessage('message-1');
    fixture.clock.advance(16);

    assertMessageAligned(fixture);
    assert.equal(fixture.viewport.scrollTop, 1500);
});

test('scroll-to-bottom cancels alignment and removes temporary spacer geometry', () => {
    const fixture = createScrollFixture();
    fixture.coordinator.alignUserMessage('message-1');
    fixture.clock.advance(100);
    assert.ok(fixture.container.querySelector('.dynamic-scroll-spacer'));

    fixture.coordinator.scrollToBottom(fixture.viewport, fixture.container, { behavior: 'smooth' });

    assert.equal(fixture.coordinator.isAligning(fixture.viewport), false);
    assert.equal(fixture.container.querySelector('.dynamic-scroll-spacer'), null);
    assert.equal(fixture.viewport.scrollTop, 1300);
    assert.equal(fixture.viewport.scrollCalls.at(-1).behavior, 'smooth');
});

test('missing or unmounted message targets abort without adding a spacer', () => {
    const fixture = createScrollFixture();

    assert.equal(fixture.coordinator.alignUserMessage('missing-message'), false);
    assert.equal(fixture.container.querySelector('.dynamic-scroll-spacer'), null);
});

test('main and split chat integrations delegate spacer ownership to the coordinator', () => {
    const indexHtml = readFrontendSource(path.join(__dirname, '../../index.html'), 'utf8');
    const chatsSource = readFrontendSource(path.join(__dirname, 'chats.js'), 'utf8');
    const splitSource = readFrontendSource(path.join(__dirname, 'splitScreen.js'), 'utf8');
    const chatCss = readFrontendSource(path.join(__dirname, '../../css/chat/chat.css'), 'utf8');
    const splitCss = readFrontendSource(path.join(__dirname, '../../css/chat/splitScreen.css'), 'utf8');

    assert.ok(
        indexHtml.indexOf('/js/chat/chatScroll.js') < indexHtml.indexOf('/js/chat/composer/state-and-transport.js'),
        'the coordinator must load before send-message flows can use it',
    );
    assert.match(chatsSource, /ChatScrollCoordinator\.alignUserMessage\(messageId, options\)/);
    assert.match(chatsSource, /ChatScrollCoordinator\.bindViewport\(chatArea, chatAreaContainer\)/);
    assert.match(splitSource, /ChatScrollCoordinator\.bindViewport\(area, container\)/);
    const splitScrollHelper = splitSource.slice(
        splitSource.indexOf('function scrollSplitAreaToBottom'),
        splitSource.indexOf('function scrollSplitUserMessageToTop'),
    );
    assert.ok(
        splitScrollHelper.indexOf('ChatScrollCoordinator.scrollToBottom')
            < splitScrollHelper.indexOf('ChatScrollManager.scrollToBottom'),
        'split scroll-to-bottom must remove coordinator spacer geometry before re-arming follow',
    );
    assert.doesNotMatch(splitSource, /spacer\.style\.height\s*=\s*['"]0/);
    assert.match(chatCss, /--chat-trailing-spacer-height:\s*140px/);
    assert.match(splitCss, /var\(--chat-trailing-spacer-height\)/);
});
