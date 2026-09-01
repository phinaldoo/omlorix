const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const modelsSource = fs.readFileSync(path.join(__dirname, 'models.js'), 'utf8');
const pagesSource = fs.readFileSync(path.join(__dirname, 'pages.js'), 'utf8');

/**
 * Extract one arrow-function declaration without evaluating the large Models
 * page module or coupling this lifecycle regression to unrelated DOM details.
 */
function extractArrowDeclaration(name) {
    const start = modelsSource.indexOf(`const ${name} =`);
    assert.notEqual(start, -1, `expected ${name}`);
    const arrow = modelsSource.indexOf('=>', start);
    assert.notEqual(arrow, -1, `expected arrow function ${name}`);
    const bodyStart = modelsSource.indexOf('{', arrow);
    let depth = 0;
    for (let index = bodyStart; index < modelsSource.length; index += 1) {
        if (modelsSource[index] === '{') depth += 1;
        if (modelsSource[index] === '}') depth -= 1;
        if (depth === 0) {
            return modelsSource.slice(start, modelsSource.indexOf(';', index) + 1);
        }
    }
    throw new Error(`could not extract ${name}`);
}

function createController() {
    const calls = { init: 0, reload: 0, teardown: 0 };
    return {
        calls,
        init() { calls.init += 1; },
        reload() { calls.reload += 1; },
        teardown() { calls.teardown += 1; },
    };
}

test('first entry initializes each Models settings subpage before later reloads', () => {
    const controllers = {
        models: createController(),
        'models-dictation-settings': createController(),
        'models-read-aloud-settings': createController(),
        'models-realtime-settings': createController(),
    };
    const context = { controllers };

    vm.runInNewContext(
        `
        const settingsControllerByRoute = controllers;
        let activeSettingsRouteKey = null;
        ${extractArrowDeclaration('activateSettingsController')}
        ${extractArrowDeclaration('teardown')}
        this.activate = activateSettingsController;
        this.teardownActive = teardown;
        `,
        context,
        { filename: 'models.js' },
    );

    context.activate('models');
    context.activate('models-dictation-settings', { reloadSchema: true });

    assert.deepEqual(controllers.models.calls, { init: 1, reload: 0, teardown: 1 });
    assert.deepEqual(
        controllers['models-dictation-settings'].calls,
        { init: 1, reload: 0, teardown: 0 },
        'dictation must initialize on first navigation instead of issuing an inactive reload',
    );

    context.activate('models-read-aloud-settings', { reloadSchema: true });
    assert.deepEqual(
        controllers['models-dictation-settings'].calls,
        { init: 1, reload: 0, teardown: 1 },
    );
    assert.deepEqual(
        controllers['models-read-aloud-settings'].calls,
        { init: 1, reload: 0, teardown: 0 },
    );

    context.activate('models-realtime-settings', { reloadSchema: true });
    assert.deepEqual(
        controllers['models-read-aloud-settings'].calls,
        { init: 1, reload: 0, teardown: 1 },
    );
    assert.deepEqual(
        controllers['models-realtime-settings'].calls,
        { init: 1, reload: 0, teardown: 0 },
        'realtime must initialize on first navigation instead of issuing an inactive reload',
    );

    context.activate('models-realtime-settings', { reloadSchema: true });
    assert.deepEqual(
        controllers['models-realtime-settings'].calls,
        { init: 1, reload: 1, teardown: 0 },
        'an already active realtime page should reload its schema',
    );

    context.teardownActive();
    assert.deepEqual(
        controllers['models-realtime-settings'].calls,
        { init: 1, reload: 1, teardown: 1 },
    );
});

test('the admin router tears down the active Models schema controller on exit', () => {
    assert.match(
        extractArrowDeclaration('init'),
        /activateSettingsController\(pageKey, \{ reloadSchema \}\)/,
    );
    assert.match(
        pagesSource,
        /pageGroup\(\['models',[\s\S]*?teardown:\s*\(\)\s*=>\s*window\.teardownModelsPage\?\.\(\)/,
    );
    assert.match(modelsSource, /window\.teardownModelsPage\s*=\s*teardown/);
});
