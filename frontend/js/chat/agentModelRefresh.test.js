const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const AGENTS_PATH = path.join(__dirname, 'agents.js');
const CHAT_BOX_PATH = path.join(__dirname, 'chatBox.js');
const MODEL_SELECT_PATH = path.join(__dirname, 'modelSelect.js');
const DELETE_CONFIRM_PATH = path.join(__dirname, '..', 'common', 'deleteConfirm.js');

/**
 * Return source located between two stable production markers.
 *
 * @param {string} source - Complete JavaScript source.
 * @param {string} startMarker - Inclusive start marker.
 * @param {string} endMarker - Exclusive end marker.
 * @returns {string} The selected source fragment.
 */
function sourceBetween(source, startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start);
    assert.notEqual(start, -1, `missing start marker: ${startMarker}`);
    assert.notEqual(end, -1, `missing end marker: ${endMarker}`);
    return source.slice(start, end);
}

/** Load the private agent API boundary without initializing workspace DOM. */
function loadAgentsApi(getCachedUserModels) {
    const source = readFrontendSource(AGENTS_PATH, 'utf8');
    const apiSource = sourceBetween(
        source,
        'const AgentsAPI = {',
        'function getSection()',
    );
    const context = {
        window: {
            getCachedUserModels,
        },
    };
    vm.runInNewContext(`${apiSource}\nthis.__agentsApiTest = AgentsAPI;`, context, { filename: AGENTS_PATH });
    return context.__agentsApiTest;
}

/** Load the private agent-asset display-name normalizer. */
function loadAgentAssetDisplayName() {
    const source = readFrontendSource(AGENTS_PATH, 'utf8');
    const helperSource = sourceBetween(
        source,
        'function agentAssetDisplayName(asset)',
        'function upsertFileMeta(file)',
    );
    const context = {};
    vm.runInNewContext(`${helperSource}\nthis.__agentAssetDisplayName = agentAssetDisplayName;`, context, { filename: AGENTS_PATH });
    return context.__agentAssetDisplayName;
}

/** Load the incoming agent-share handler with observable UI and API boundaries. */
function loadIncomingAgentShareHandler({ pathname, preview, confirmed = true }) {
    const source = readFrontendSource(AGENTS_PATH, 'utf8');
    const handlerSource = sourceBetween(
        source,
        'async function maybeHandleIncomingSharePath()',
        'function bindStaticEvents()',
    );
    const calls = {
        accepted: [],
        cloned: [],
        confirmations: [],
        history: [],
    };
    const context = {
        AgentsState: { acceptHandled: false },
        AgentsAPI: {
            previewShare: async () => preview,
            acceptShare: async (shareId) => calls.accepted.push(shareId),
            cloneShare: async (shareId) => calls.cloned.push(shareId),
        },
        agentsEnabled: () => true,
        console,
        loadAgents: async () => {},
        notifyError: () => {},
        notifySuccess: () => {},
        refreshAgentModelConsumers: async () => {},
        t: (_key, fallback) => fallback,
        window: {
            location: { pathname },
            history: {
                replaceState: (_state, _title, url) => calls.history.push(url),
            },
            showWarningConfirm: async (options) => {
                calls.confirmations.push(options);
                return confirmed;
            },
            showWorkspaceContainer: () => {},
        },
    };
    vm.runInNewContext(
        `${handlerSource}\nthis.__incomingAgentShareHandler = maybeHandleIncomingSharePath;`,
        context,
        { filename: AGENTS_PATH },
    );
    return { handler: context.__incomingAgentShareHandler, calls };
}

test('agent base-model list accepts only canonical base-model summaries', async () => {
    const api = loadAgentsApi(async () => [
        { model_id: 'base-explicit', model_kind: 'base', is_custom_agent: false },
        { model_id: 'missing-kind' },
        { model_id: 'agent-explicit', model_kind: 'base', is_custom_agent: true },
        { model_id: 'agent-modern', model_kind: 'agent', is_custom_agent: true },
    ]);

    const models = await api.listBaseModels();

    assert.deepEqual(
        Array.from(models, (model) => model.model_id),
        ['base-explicit', 'agent-explicit'],
    );
});

test('saved agent assets display their original filename', () => {
    const displayName = loadAgentAssetDisplayName();
    const source = readFrontendSource(AGENTS_PATH, 'utf8');

    assert.equal(displayName({
        id: 'asset-1',
        file_name: 'agent-d97df670-b3ee2244.md',
        original_filename: 'workspace-e2e-renamed.md',
    }), 'workspace-e2e-renamed.md');
    assert.equal(displayName({
        id: 'asset-2',
        file_name: 'agent-legacy-storage.md',
        meta: { original_filename: 'legacy-reference.md' },
    }), 'legacy-reference.md');
    assert.match(source, /name:\s*agentAssetDisplayName\(asset\)/);
});

test('model refresh broadcasts the same authoritative payload used by the selector', async () => {
    const source = readFrontendSource(MODEL_SELECT_PATH, 'utf8');
    const helperSource = sourceBetween(
        source,
        'async function refreshUserModelConsumers()',
        'function createModelSectionLabel',
    );
    const models = [{ model_id: 'agent-new', name: 'New agent' }];
    const dispatched = [];
    let receivedOptions = null;

    class CustomEvent {
        constructor(type, init = {}) {
            this.type = type;
            this.detail = init.detail;
        }
    }

    const context = {
        Array,
        CustomEvent,
        ModelSelectLoadModels: async (options) => {
            receivedOptions = options;
            return models;
        },
        window: {
            dispatchEvent(event) {
                dispatched.push(event);
            },
        },
    };
    vm.runInNewContext(helperSource, context);

    const result = await context.refreshUserModelConsumers();

    assert.equal(receivedOptions.forceRefresh, true);
    assert.equal(result, models);
    assert.equal(dispatched.length, 1);
    assert.equal(dispatched[0].type, 'userModels:refreshed');
    assert.equal(dispatched[0].detail.models, models);
});

test('model refresh publishes an empty inventory but ignores unavailable results', async () => {
    const source = readFrontendSource(MODEL_SELECT_PATH, 'utf8');
    const helperSource = sourceBetween(
        source,
        'async function refreshUserModelConsumers()',
        'function createModelSectionLabel',
    );
    const dispatched = [];
    let loaderResult;

    class CustomEvent {
        constructor(type, init = {}) {
            this.type = type;
            this.detail = init.detail;
        }
    }

    const context = {
        Array,
        CustomEvent,
        ModelSelectLoadModels: async () => loaderResult,
        window: {
            dispatchEvent(event) {
                dispatched.push(event);
            },
        },
    };
    vm.runInNewContext(helperSource, context);

    assert.equal(await context.refreshUserModelConsumers(), undefined);
    assert.equal(dispatched.length, 0);

    loaderResult = { models: [] };
    assert.equal(await context.refreshUserModelConsumers(), undefined);
    assert.equal(dispatched.length, 0);

    loaderResult = [];
    const result = await context.refreshUserModelConsumers();
    assert.equal(result, loaderResult);
    assert.equal(dispatched.length, 1);
    assert.equal(dispatched[0].detail.models, loaderResult);
});

test('agent refresh fallback only publishes array results from an available source', async () => {
    const source = readFrontendSource(AGENTS_PATH, 'utf8');
    const helperSource = sourceBetween(
        source,
        'async function refreshAgentModelConsumers()',
        'const escapeHtml',
    );
    const dispatched = [];

    class CustomEvent {
        constructor(type, init = {}) {
            this.type = type;
            this.detail = init.detail;
        }
    }

    const context = {
        Array,
        CustomEvent,
        console,
        window: {
            dispatchEvent(event) {
                dispatched.push(event);
            },
        },
    };
    vm.runInNewContext(helperSource, context);

    await context.refreshAgentModelConsumers();
    assert.equal(dispatched.length, 0);

    context.window.getCachedUserModels = async () => undefined;
    await context.refreshAgentModelConsumers();
    assert.equal(dispatched.length, 0);

    const models = [];
    context.window.ModelSelectLoadModels = async () => models;
    await context.refreshAgentModelConsumers();
    assert.equal(dispatched.length, 1);
    assert.equal(dispatched[0].detail.models, models);
});

test('mention menu replaces its private model cache and rerenders when open', () => {
    const source = readFrontendSource(CHAT_BOX_PATH, 'utf8');
    const helperSource = sourceBetween(
        source,
        'function updateMentionModelsFromRefresh(models)',
        "window.addEventListener('userModels:refreshed'",
    );
    const modelMentionState = { models: [{ model_id: 'old-agent' }], lastFetched: 0 };
    const skillMentionState = { isOpen: true, query: 'new' };
    const renders = [];
    const models = [{ model_id: 'new-agent', name: 'New agent' }];
    const context = {
        Array,
        Date: { now: () => 12345 },
        modelMentionState,
        skillMentionState,
        filterSkills: () => ['skills'],
        filterNotes: () => ['notes'],
        filterPrompts: () => ['prompts'],
        filterModels: () => modelMentionState.models,
        renderMentionDropdown: (...args) => renders.push(args),
    };
    vm.runInNewContext(helperSource, context);

    context.updateMentionModelsFromRefresh(models);

    assert.equal(modelMentionState.models, models);
    assert.equal(modelMentionState.lastFetched, 12345);
    assert.equal(renders.length, 1);
    assert.equal(renders[0][3], models);
});

test('agent create, edit, and delete flows refresh model consumers', () => {
    const source = readFrontendSource(AGENTS_PATH, 'utf8');
    const saveFlow = sourceBetween(source, 'async function saveAgent()', 'async function deleteAgent(agent)');
    const deleteFlow = sourceBetween(source, 'async function deleteAgent(agent)', 'async function removeSharedAgent(agent)');

    // Creation and editing share saveAgent, so one post-save refresh covers
    // both branches after the API mutation and asset updates have completed.
    assert.match(saveFlow, /AgentsAPI\.updateAgent[\s\S]*AgentsAPI\.createAgent/);
    assert.match(saveFlow, /Promise\.all\(\[[\s\S]*refreshAgentModelConsumers\(\)/);
    assert.match(deleteFlow, /await AgentsAPI\.deleteAgent\(agent\.id\);[\s\S]*await refreshAgentModelConsumers\(\);/);
});

test('agent share preview blocks actions when the base model is unavailable', async () => {
    const { handler, calls } = loadIncomingAgentShareHandler({
        pathname: '/agents/live/share-1',
        preview: {
            share_type: 'live',
            name: 'Restricted agent',
            base_model_accessible: false,
            can_complete_share_action: false,
            clone_skill_will_be_omitted: false,
        },
        // Returning true verifies that the handler also guards the mutation;
        // a real disabled primary button cannot resolve the modal this way.
        confirmed: true,
    });

    await handler();

    assert.equal(calls.confirmations.length, 1);
    assert.equal(calls.confirmations[0].confirmDisabled, true);
    assert.match(calls.confirmations[0].message, /do not have access to its base model/);
    assert.deepEqual(calls.accepted, []);
    assert.deepEqual(calls.cloned, []);
    assert.deepEqual(calls.history, ['/workspace/agents']);
});

test('clone preview warns about an omitted inaccessible skill and still clones', async () => {
    const { handler, calls } = loadIncomingAgentShareHandler({
        pathname: '/agents/clone/share-1',
        preview: {
            share_type: 'clone',
            name: 'Agent with private skill',
            base_model_accessible: true,
            can_complete_share_action: true,
            clone_skill_will_be_omitted: true,
        },
    });

    await handler();

    assert.equal(calls.confirmations.length, 1);
    assert.equal(calls.confirmations[0].confirmDisabled, false);
    assert.match(calls.confirmations[0].message, /created without that skill/);
    assert.deepEqual(calls.accepted, []);
    assert.deepEqual(calls.cloned, ['share-1']);
});

test('shared confirmation modal supports a disabled primary action', () => {
    const source = readFrontendSource(DELETE_CONFIRM_PATH, 'utf8');
    assert.match(source, /primaryBtn\.disabled = Boolean\(options\.confirmDisabled\)/);
});
