const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'models.js'), 'utf8');

test('model import picker leaves type validation to the in-app preflight', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', '..', 'admin.html'), 'utf8');
    const [importInput = ''] = html.match(/<input\b[^>]*id="importModelsFileInput"[^>]*>/) || [];

    assert.ok(importInput, 'model import file input must exist');
    assert.doesNotMatch(importInput, /\saccept=/);
});

function extractArrowDeclaration(name) {
    const start = source.indexOf(`const ${name} =`);
    assert.notEqual(start, -1, `expected ${name}`);
    const bodyStart = source.indexOf('{', start);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) return source.slice(start, source.indexOf(';', index) + 1);
    }
    throw new Error(`could not extract ${name}`);
}

test('invalid model exports are distinct from valid empty exports', () => {
    const notifications = [];
    const context = {
        MODELS_EXPORT_VERSION: 1.0,
        notifyError(message) { notifications.push(message); },
        t(_key, fallback) { return fallback; },
    };
    vm.runInNewContext(
        `${extractArrowDeclaration('resolveModelsFromPayload')}\nthis.resolve = resolveModelsFromPayload;`,
        context,
        { filename: 'models.js' },
    );

    assert.equal(context.resolve({ export_type: 'llm_model', export_version: 2 }), null);
    assert.equal(notifications.length, 1);
    assert.deepEqual(
        Array.from(context.resolve({
            export_type: 'llm_model',
            export_version: 1.0,
            data: { models: [] },
        })),
        [],
    );
    assert.match(source, /if \(modelsToImport === null\) \{\s*return;\s*\}/);
});
