const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');


const SOURCE = fs.readFileSync(path.join(__dirname, 'modelsApi.js'), 'utf8');
const MODELS_SOURCE = fs.readFileSync(path.join(__dirname, 'models.js'), 'utf8');


test('admin model management uses the dedicated protected inventory endpoint', () => {
    assert.match(SOURCE, /fetchJson\('\/api\/v1\/llm\/models\/admin'/);
    assert.doesNotMatch(SOURCE, /fetchAdminModels[\s\S]*?\/api\/v1\/llm\/models\/user/);
});


test('voice settings use their independently owned pages and discovery endpoints', () => {
    assert.match(SOURCE, /\/settings\/dictation\/transcription\/models/);
    assert.match(SOURCE, /\/settings\/dictation\/live-transcription\/models/);
    assert.match(SOURCE, /\/settings\/realtime\/models/);
    assert.match(MODELS_SOURCE, /pageKey: 'dictation'/);
    assert.match(MODELS_SOURCE, /pageKey: 'read_aloud'/);
    assert.match(MODELS_SOURCE, /pageKey: 'realtime'/);
    assert.doesNotMatch(MODELS_SOURCE, /MODELS_DICTATION_FIELD_KEYS/);
});
