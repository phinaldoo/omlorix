const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'mcpServers.js'), 'utf8');

function createHarness() {
    const elements = new Map([
        ['mcpServerCreateTest', { dataset: {}, disabled: false, textContent: 'Test Connection' }],
        ['mcpServerCreateName', { value: 'E2E Unreachable MCP' }],
        ['mcpServerCreateDescription', { value: '' }],
        ['mcpServerCreateNamespace', { value: '' }],
        ['mcpServerCreateTransport', { value: 'streamable_http' }],
        ['mcpServerCreateAuthMode', { value: 'headers' }],
        ['mcpServerCreateEnabled', { checked: true }],
        ['mcpServerCreateUrl', { value: 'http://127.0.0.1:9/mcp' }],
        ['mcpServerCreateTimeout', { value: '30' }],
        ['mcpServerCreateHeaders', { value: '{}' }],
        ['mcpServerCreateAllowedTools', { value: '' }],
        ['mcpServerCreatePreviewSubtitle', { textContent: '' }],
        ['mcpServerCreateToolPreview', { innerHTML: '' }],
    ]);
    const notifications = [];
    const instrumented = source.replace(
        '    window.initMcpServersPage = () => {',
        `    window.__mcpServersTest = {
        getEmptyPreviewCopy,
        renderToolPreview,
        state,
        testServer,
    };

    window.initMcpServersPage = () => {`,
    );
    const context = {
        console,
        document: {
            getElementById(id) {
                return elements.get(id) || null;
            },
        },
        Icons: {
            wrapSvgBody() { return ''; },
        },
        URL,
        window: {
            async authedFetch() {
                return {
                    ok: false,
                    status: 400,
                    async json() {
                        return {
                            detail: 'Could not connect to E2E Unreachable MCP. Check the connection credentials and try again.',
                        };
                    },
                };
            },
            getTranslation(key, fallback) {
                if (key === 'mcp_connect_failed') {
                    return 'Verbindung zum MCP-Server konnte nicht hergestellt werden.';
                }
                return fallback;
            },
            notifyError(message) {
                notifications.push(message);
            },
        },
    };
    context.globalThis = context;
    vm.runInNewContext(instrumented, context, { filename: 'mcpServers.js' });

    return {
        elements,
        notifications,
        testApi: context.window.__mcpServersTest,
    };
}

test('a failed MCP test is translated and remains an error when the preview is rendered again', async () => {
    const harness = createHarness();
    const subtitle = harness.elements.get('mcpServerCreatePreviewSubtitle');
    const preview = harness.elements.get('mcpServerCreateToolPreview');

    await harness.testApi.testServer('mcpServerCreate');

    assert.equal(harness.testApi.state.preview.mode, 'error');
    assert.match(subtitle.textContent, /connection test failed/i);
    assert.match(preview.innerHTML, /Tool discovery failed/);
    assert.match(preview.innerHTML, /no successful tool discovery result is available/i);
    assert.doesNotMatch(preview.innerHTML, /server responded/i);
    assert.deepEqual(harness.notifications, ['Verbindung zum MCP-Server konnte nicht hergestellt werden.']);
    assert.doesNotMatch(harness.notifications[0], /Could not connect|credentials|E2E Unreachable MCP/);

    harness.elements.get('mcpServerCreateAuthMode').value = 'oauth';
    harness.testApi.renderToolPreview('mcpServerCreate');

    assert.equal(harness.testApi.state.preview.mode, 'error');
    assert.doesNotMatch(preview.innerHTML, /server responded/i);
});

test('MCP preview failure copy exists in every supported locale', () => {
    const localeRoot = path.join(__dirname, '../../i18n');
    const requiredKeys = ['mcp_connect_failed', 'mcp_preview_error_title', 'mcp_preview_error_desc'];

    for (const locale of fs.readdirSync(localeRoot)) {
        const file = path.join(localeRoot, locale, 'admin.json');
        if (!fs.existsSync(file)) continue;
        const translations = JSON.parse(fs.readFileSync(file, 'utf8'));
        for (const key of requiredKeys) {
            assert.equal(typeof translations[key], 'string', `${locale} missing ${key}`);
            assert.ok(translations[key].trim(), `${locale} has empty ${key}`);
        }
    }
});
