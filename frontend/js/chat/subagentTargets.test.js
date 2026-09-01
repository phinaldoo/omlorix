const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readSendMessageSource } = require('./sending/source.cjs');

const modulePath = path.join(__dirname, 'subagentTargets.js');
const moduleSource = fs.readFileSync(modulePath, 'utf8');

function loadSelectionWindow() {
    const listeners = new Map();
    const document = {
        readyState: 'loading',
        addEventListener(name, handler) {
            listeners.set(name, handler);
        },
    };
    const window = {};
    vm.runInNewContext(moduleSource, {
        console: { ...console, error() {} },
        document,
        window,
    });
    return window;
}

function loadSelectionApi() {
    return loadSelectionWindow().SubagentTargets;
}

test('delegation target selection serializes exact typed IDs and supports automatic mode', () => {
    const api = loadSelectionApi();

    assert.equal(api.getSelection(), null);
    api.setSelection([
        { type: 'agent', id: 'agent-1', name: 'Research Agent' },
        { type: 'model', id: 'model-1', name: 'Fast Model' },
        { type: 'invalid', id: 'ignored' },
    ]);
    assert.deepEqual(Array.from(api.getSelection(), (target) => ({ ...target })), [
        { type: 'agent', id: 'agent-1' },
        { type: 'model', id: 'model-1' },
    ]);

    api.setSelection(null);
    assert.equal(api.getSelection(), null);
});

test('user model summaries normalize into typed delegation targets', () => {
    const api = loadSelectionApi();

    assert.deepEqual(
        { ...api._normalizeTargetForTest({
            model_kind: 'base',
            model_id: 'model-1',
            name: 'Fast Model',
            provider: 'openai',
        }) },
        {
            type: 'model',
            id: 'model-1',
            name: 'Fast Model',
            description: '',
            provider: 'openai',
            base_model_name: 'Fast Model',
            is_shared: false,
        },
    );
    assert.equal(
        api._normalizeTargetForTest({ model_kind: 'agent', model_id: 'agent-1' }).type,
        'agent',
    );
});

test('composer, queue, and regeneration payloads carry the strict selection', () => {
    const sendSource = readSendMessageSource();
    const queueSource = fs.readFileSync(path.join(__dirname, 'messageQueue.js'), 'utf8');

    assert.doesNotMatch(moduleSource, /\/api\/v1\/subagents\/targets/);
    assert.match(moduleSource, /\/api\/v1\/llm\/models\/user/);
    assert.match(moduleSource, /menu\.classList\.add\('open'\)/);
    assert.match(moduleSource, /MAX_SELECTED_TARGETS\s*=\s*20/);
    assert.match(sendSource, /subagent_targets:\s*Array\.isArray\(payloadSubagentTargets\)/);
    assert.match(sendSource, /payload\.subagent_targets\s*=\s*Array\.isArray\(subagentTargets\)/);
    assert.match(queueSource, /subagentTargets/);
});

test('failed target discovery remains a retryable error and does not restrict delegation', async () => {
    const window = loadSelectionWindow();
    window.getCachedUserModels = async () => {
        throw new Error('Model catalog unavailable');
    };

    await window.SubagentTargets.refresh('e2e-all-tools-model');

    assert.equal(window.SubagentTargets.getAvailability(), 'error');
    assert.equal(window.SubagentTargets.getSelection(), null);

    window.getCachedUserModels = async () => [
        {
            model_kind: 'base',
            model_id: 'e2e-all-tools-model',
            name: 'Parent',
            model_select_tools: ['subagent'],
        },
        {
            model_kind: 'base',
            model_id: 'worker-model',
            name: 'Worker',
            model_select_tools: [],
        },
    ];

    await window.SubagentTargets.refresh('e2e-all-tools-model');

    assert.equal(window.SubagentTargets.getAvailability(), 'enabled');
    assert.equal(window.SubagentTargets.getSelection(), null);
});
