const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractSnippet(source, marker, endMarker) {
    const start = source.indexOf(marker);
    assert.notEqual(start, -1, `expected snippet starting with ${marker}`);
    const end = source.indexOf(endMarker, start);
    assert.notEqual(end, -1, `expected snippet ending before ${endMarker}`);
    return source.slice(start, end);
}

function loadHelpers() {
    const source = fs.readFileSync(path.join(__dirname, 'adminNotification.js'), 'utf8');
    const context = {};

    vm.runInNewContext(
        [
            extractSnippet(source, 'const normalizeCategory =', 'const formatCategoryFallback ='),
            extractSnippet(source, 'const getKnownCategoryMeta =', 'const formatCategory ='),
            extractSnippet(source, 'const shouldRenderNotificationDetails =', 'const formatNotificationDetails ='),
            `this.helpers = {
                shouldRenderNotificationDetails,
            };`,
        ].join('\n\n'),
        context,
        { filename: 'adminNotification.js' },
    );

    return context.helpers;
}

test('admin notifications hide details for known admin notification categories', () => {
    const { shouldRenderNotificationDetails } = loadHelpers();

    assert.equal(
        shouldRenderNotificationDetails('llm_model_added', { provider_name: 'Nebius Paid' }),
        false,
    );
    assert.equal(
        shouldRenderNotificationDetails('llm_model_removed', { provider_name: 'Nebius Paid' }),
        false,
    );
    assert.equal(
        shouldRenderNotificationDetails('llm_model_auto_deleted', { provider_name: 'Nebius Paid' }),
        false,
    );
    assert.equal(
        shouldRenderNotificationDetails('llm_provider_availability', { provider_name: 'Nebius Paid' }),
        false,
    );
    assert.equal(
        shouldRenderNotificationDetails('security', { trace_id: 'abc123' }),
        false,
    );
});

test('admin notifications still show details for unknown categories that can need diagnostics', () => {
    const { shouldRenderNotificationDetails } = loadHelpers();

    assert.equal(
        shouldRenderNotificationDetails('custom_diagnostic_category', { trace_id: 'abc123' }),
        true,
    );
});

test('admin notifications do not render empty details payloads', () => {
    const { shouldRenderNotificationDetails } = loadHelpers();

    assert.equal(shouldRenderNotificationDetails('system', null), false);
    assert.equal(shouldRenderNotificationDetails('system', ''), false);
    assert.equal(shouldRenderNotificationDetails('system', undefined), false);
});
