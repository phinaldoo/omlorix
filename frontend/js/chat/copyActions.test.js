const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readStreamMessagesSource } = require('./messages/source.cjs');

const I18N_ROOT = path.join(__dirname, '..', '..', 'i18n');
const SEND_MESSAGE_PATH = path.join(__dirname, 'rendering', 'markdown-ui.js');
const STREAM_MESSAGES_PATH = path.join(__dirname, 'streamMessages.js');
const STREAM_MESSAGES_SOURCE = readStreamMessagesSource();
const CHAT_BOX_PATH = path.join(__dirname, 'chatBox.js');
const CANVAS_WIDGET_PATH = path.join(__dirname, 'canvas-widget.js');
const COPY_FEEDBACK_KEYS = [
    'chat_copy_code_success',
    'chat_copy_code_error',
    'chat_clipboard_copy_fallback_failed',
    'chat_copy_table_success',
    'chat_copy_table_error',
    'chat_copy_message_success',
    'chat_copy_message_error',
];

function extractFunction(source, functionName) {
    const functionToken = `function ${functionName}`;
    const start = source.indexOf(functionToken);
    assert.notEqual(start, -1, `${functionName} not found`);
    const asyncStart = source.lastIndexOf(`async ${functionToken}`, start);
    const declarationStart = asyncStart !== -1 ? asyncStart : start;

    const bodyStart = source.indexOf('{', declarationStart);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') {
            depth += 1;
        } else if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(declarationStart, index + 1);
            }
        }
    }

    throw new Error(`${functionName} body was not closed`);
}

function createDocument(execCommandResult) {
    const appended = [];
    const removed = [];

    const body = {
        appendChild(node) {
            appended.push(node);
            return node;
        },
        removeChild(node) {
            removed.push(node);
            return node;
        },
    };

    return {
        appended,
        removed,
        document: {
            body,
            createElement() {
                return {
                    value: '',
                    style: {},
                    focus() {},
                    select() {},
                };
            },
            execCommand(command) {
                assert.equal(command, 'copy');
                return execCommandResult;
            },
        },
    };
}

function loadFunction(filePath, functionName, context) {
    const source = filePath === STREAM_MESSAGES_PATH
        ? STREAM_MESSAGES_SOURCE
        : readFrontendSource(filePath, 'utf8');
    return vm.runInNewContext(
        `${extractFunction(source, functionName)}\n${functionName};`,
        context,
        { filename: path.basename(filePath) },
    );
}

test('sendMessage copyToClipboard resolves true when the clipboard API succeeds', async () => {
    let copiedValue = null;
    const copyToClipboard = loadFunction(SEND_MESSAGE_PATH, 'copyToClipboard', {
        navigator: {
            clipboard: {
                async writeText(value) {
                    copiedValue = value;
                },
            },
        },
        document: {},
    });

    await assert.doesNotReject(async () => {
        const result = await copyToClipboard('hello world');
        assert.equal(result, true);
    });
    assert.equal(copiedValue, 'hello world');
});

test('sendMessage copyToClipboard rejects when the fallback copy command fails', async () => {
    const { document, appended, removed } = createDocument(false);
    const copyToClipboard = loadFunction(SEND_MESSAGE_PATH, 'copyToClipboard', {
        navigator: {},
        document,
    });

    await assert.rejects(() => copyToClipboard('hello world'), /Clipboard copy fallback failed/);
    assert.equal(appended.length, 1);
    assert.equal(removed.length, 1);
    assert.equal(appended[0], removed[0]);
});

test('streamMessages writeTextToClipboardWithFallback resolves true when fallback copy succeeds', async () => {
    const { document, appended, removed } = createDocument(true);
    const writeTextToClipboardWithFallback = loadFunction(
        STREAM_MESSAGES_PATH,
        'writeTextToClipboardWithFallback',
        {
            navigator: {},
            document,
        },
    );

    const result = await writeTextToClipboardWithFallback('assistant reply');
    assert.equal(result, true);
    assert.equal(appended.length, 1);
    assert.equal(removed.length, 1);
});

test('streamMessages writeTextToClipboardWithFallback rejects when fallback copy fails', async () => {
    const { document } = createDocument(false);
    const writeTextToClipboardWithFallback = loadFunction(
        STREAM_MESSAGES_PATH,
        'writeTextToClipboardWithFallback',
        {
            navigator: {},
            document,
        },
    );

    await assert.rejects(
        () => writeTextToClipboardWithFallback('assistant reply'),
        /Clipboard copy fallback failed/,
    );
});

test('compliance watermark helper follows the enabled setting and configured text', () => {
    const values = new Map([
        ['compliance_enable_watermark', 'true'],
        ['compliance_watermark', '  Generated marker  '],
    ]);
    const context = {
        localStorage: {
            getItem(key) {
                return values.get(key) || null;
            },
        },
        window: {
            chatSetup: {
                compliance: {
                    enable_watermark: false,
                    watermark: 'Fallback marker',
                },
            },
        },
    };
    const helperSource = [
        extractFunction(STREAM_MESSAGES_SOURCE, 'safeLocalStorageGet'),
        extractFunction(STREAM_MESSAGES_SOURCE, 'getComplianceWatermarkPayload'),
        extractFunction(STREAM_MESSAGES_SOURCE, 'appendComplianceWatermarkIfNeeded'),
        'appendComplianceWatermarkIfNeeded;',
    ].join('\n');
    const appendComplianceWatermarkIfNeeded = vm.runInNewContext(helperSource, context, {
        filename: path.basename(STREAM_MESSAGES_PATH),
    });

    assert.equal(
        appendComplianceWatermarkIfNeeded('answer\n'),
        'answer\n\nGenerated marker',
    );
    values.set('compliance_enable_watermark', 'false');
    assert.equal(appendComplianceWatermarkIfNeeded('answer'), 'answer');
});

test('all named generated-content copy paths invoke the compliance helper', () => {
    const sourceFiles = [SEND_MESSAGE_PATH, CHAT_BOX_PATH, CANVAS_WIDGET_PATH];
    sourceFiles.forEach((filePath) => {
        const source = readFrontendSource(filePath, 'utf8');
        assert.match(source, /appendComplianceWatermarkIfNeeded/);
    });
});

test('chat copy feedback translations exist in every supported locale', () => {
    const locales = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    locales.forEach((locale) => {
        const file = path.join(I18N_ROOT, locale, 'index.json');
        const dictionary = JSON.parse(readFrontendSource(file, 'utf8'));

        COPY_FEEDBACK_KEYS.forEach((key) => {
            assert.ok(
                Object.prototype.hasOwnProperty.call(dictionary, key),
                `${locale} is missing ${key}`,
            );
        });
    });
});
