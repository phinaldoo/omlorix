const assert = require('node:assert/strict');
const test = require('node:test');

const { normalizeFrontendRelativePath } = require('./splitSource.cjs');

test('split source paths use portable map keys on Windows', () => {
    assert.equal(
        normalizeFrontendRelativePath('js\\chat\\chatBox.js'),
        'js/chat/chatBox.js',
    );
});
