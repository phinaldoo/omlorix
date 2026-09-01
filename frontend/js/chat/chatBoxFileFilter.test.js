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

function loadFileCapabilityHelpers(modelSupportedFileFormats) {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const context = { window: { modelSupportedFileFormats } };
    vm.runInNewContext(
        [
            extractFunction(source, 'getSupportedMimeTypesForCurrentModel'),
            extractFunction(source, 'isFileSupportedForCurrentModel'),
            `this.helpers = {
                getSupportedMimeTypesForCurrentModel,
                isFileSupportedForCurrentModel,
            };`,
        ].join('\n\n'),
        context,
        { filename: 'chatBox.js' },
    );
    return context.helpers;
}

test('model file checks ignore MIME parameters on uploads and capability entries', () => {
    const { isFileSupportedForCurrentModel } = loadFileCapabilityHelpers({
        supported_file_formats: [
            { category: 'document', file_formats: ['image/svg+xml; profile=vector'] },
        ],
    });

    assert.equal(
        isFileSupportedForCurrentModel({ type: 'image/svg+xml; charset=utf-8' }),
        true,
    );
    assert.equal(isFileSupportedForCurrentModel({ type: 'image/png' }), false);
});

test('embedded file picker renders every loaded result without a recent-item cap', () => {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const renderQuickpickList = extractFunction(source, 'renderQuickpickList');

    assert.doesNotMatch(renderQuickpickList, /slice\(/);
    assert.doesNotMatch(renderQuickpickList, /appendQuickpickOpenFullButton/);
});

test('embedded file picker paginates from its own scrolling region', () => {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const getScrollContainer = extractFunction(source, 'getUploadedFilesScrollContainer');

    assert.match(getScrollContainer, /return chatBoxFilesQuickpickScroll/);
    assert.match(source, /chatBoxFilesQuickpickScroll\.addEventListener\('scroll'[\s\S]*maybeLoadMoreUploadedFiles\('quickpick'\)/);
});
