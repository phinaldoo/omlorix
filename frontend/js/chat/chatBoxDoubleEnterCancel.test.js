const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName} in chatBox.js`);

    const bodyStart = source.indexOf('{', start);
    assert.notEqual(bodyStart, -1, `expected ${functionName} body`);

    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') {
            depth += 1;
        } else if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }

    throw new Error(`Could not extract ${functionName}`);
}

function loadDoubleEnterCancelHelpers() {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const windowDeclaration = source.match(/const DOUBLE_ENTER_CANCEL_WINDOW_MS = .*?;/)?.[0];
    assert.ok(windowDeclaration, 'expected double enter cancel window declaration');

    const context = {
        requestCancelEnabled: false,
        cancelCalls: [],
        timers: [],
        clearedTimerIds: [],
        generating: true,
        cancelPending: false,
        now: 1000,
    };

    vm.runInNewContext(
        [
            windowDeclaration,
            'let lastEnterKeyTime = 0;',
            'let doubleEnterCancelTimeoutId = 0;',
            'function setSendButtonRequestingCancel(enabled) { requestCancelEnabled = Boolean(enabled); }',
            'function isCurrentSendContextGenerating() { return generating; }',
            'function isChatSendCancellationPending() { return cancelPending; }',
            'function cancelActiveGeneration(options) { cancelCalls.push(options); }',
            'const Date = { now() { return now; } };',
            'function setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; }',
            'function clearTimeout(id) { clearedTimerIds.push(id); }',
            extractFunction(source, 'resetDoubleEnterTimer'),
            extractFunction(source, 'handleDoubleEnterCancel'),
            `this.helpers = {
                handleDoubleEnterCancel,
                getState() {
                    return {
                        lastEnterKeyTime,
                        requestCancelEnabled,
                        cancelCalls: cancelCalls.slice(),
                        clearedTimerIds: clearedTimerIds.slice(),
                        timers: timers.slice(),
                    };
                },
                setNow(value) {
                    now = value;
                },
                setGenerating(value) {
                    generating = Boolean(value);
                },
                runTimer(index = 0) {
                    const timer = timers[index];
                    if (!timer) {
                        throw new Error('expected timer to exist');
                    }
                    timer.fn();
                },
            };`,
        ].join('\n\n'),
        context,
        { filename: 'chatBox.js' },
    );

    return context.helpers;
}

test('first Enter while streaming arms the cancellation window until the timeout expires', () => {
    const helpers = loadDoubleEnterCancelHelpers();

    assert.equal(helpers.handleDoubleEnterCancel(), false);

    const armedState = helpers.getState();
    assert.equal(armedState.lastEnterKeyTime, 1000);
    assert.equal(armedState.requestCancelEnabled, true);
    assert.equal(armedState.timers.length, 1);
    assert.equal(armedState.timers[0].ms, 1200);

    helpers.runTimer();

    const clearedState = helpers.getState();
    assert.equal(clearedState.lastEnterKeyTime, 0);
    assert.equal(clearedState.requestCancelEnabled, false);
});

test('second Enter inside the window cancels immediately', () => {
    const helpers = loadDoubleEnterCancelHelpers();

    assert.equal(helpers.handleDoubleEnterCancel(), false);

    helpers.setNow(1600);
    assert.equal(helpers.handleDoubleEnterCancel(), true);

    const state = helpers.getState();
    assert.equal(state.lastEnterKeyTime, 0);
    assert.equal(state.requestCancelEnabled, false);
    assert.equal(JSON.stringify(state.cancelCalls), JSON.stringify([{ showVisualFeedback: true, scope: 'target' }]));
});

test('non-streaming state does not arm cancellation and clears stale state', () => {
    const helpers = loadDoubleEnterCancelHelpers();

    assert.equal(helpers.handleDoubleEnterCancel(), false);
    helpers.setGenerating(false);

    assert.equal(helpers.handleDoubleEnterCancel(), false);

    const state = helpers.getState();
    assert.equal(state.lastEnterKeyTime, 0);
    assert.equal(state.requestCancelEnabled, false);
});

test('resetting the armed state clears the previously scheduled timeout', () => {
    const helpers = loadDoubleEnterCancelHelpers();

    assert.equal(helpers.handleDoubleEnterCancel(), false);
    helpers.setGenerating(false);
    assert.equal(helpers.handleDoubleEnterCancel(), false);

    const state = helpers.getState();
    assert.equal(JSON.stringify(state.clearedTimerIds), JSON.stringify([1]));
});
