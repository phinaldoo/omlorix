const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const SANITIZER_PATH = path.join(__dirname, 'chatSanitizer.js');

function loadSanitizer() {
    const source = fs.readFileSync(SANITIZER_PATH, 'utf8');
    const sanitizeCalls = [];
    const context = {
        window: {
            DOMPurify: {
                sanitize(value, options) {
                    sanitizeCalls.push({ value, options });
                    return value;
                },
            },
        },
        console,
        setTimeout,
        clearTimeout,
    };
    vm.runInNewContext(source, context, { filename: SANITIZER_PATH });
    return { sanitizer: context.window.ChatSanitizer, sanitizeCalls };
}

test('chat sanitizer preserves the Markdown HTML preview permission metadata', () => {
    const { sanitizer, sanitizeCalls } = loadSanitizer();
    const source = '<input class="html-preview-capability-toggle" data-html-preview-permission="scripts">';

    const sanitized = sanitizer.sanitizeHtml(source);
    const options = sanitizeCalls.at(-1)?.options;

    assert.equal(sanitized, source);
    assert.ok(options, 'DOMPurify should receive a sanitization policy');
    assert.equal(options.ALLOW_DATA_ATTR, false);
    assert.ok(
        options.ADD_ATTR.includes('data-html-preview-permission'),
        'the permission metadata must be explicitly allowlisted',
    );
});
