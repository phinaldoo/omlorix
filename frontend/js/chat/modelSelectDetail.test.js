const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SCRIPT_PATH = path.join(__dirname, 'modelSelect.js');

/**
 * Render the real model detail implementation with the smallest DOM surface
 * needed by the desktop tooltip. This keeps the regression test focused on
 * generated markup without introducing a browser-only test dependency.
 */
function renderModelDetail(model) {
    const detail = {
        innerHTML: '',
        style: {},
        classList: {
            add() {},
            remove() {},
        },
        setAttribute() {},
        getBoundingClientRect() {
            return { height: 160 };
        },
    };
    const panel = {
        classList: {
            contains(className) {
                return className === 'open';
            },
        },
        getBoundingClientRect() {
            return { left: 100, right: 420 };
        },
    };
    const document = {
        getElementById(id) {
            if (id === 'modelSelectDetail') return detail;
            if (id === 'modelSelectDropdown') return panel;
            return null;
        },
        addEventListener() {},
        querySelectorAll() {
            return [];
        },
    };
    const window = {
        innerWidth: 1280,
        innerHeight: 800,
        addEventListener() {},
        removeEventListener() {},
    };
    const Icons = new Proxy({}, {
        get() {
            return '<svg aria-hidden="true"></svg>';
        },
    });
    const context = {
        console,
        document,
        Icons,
        window,
        URLSearchParams,
        setTimeout,
        clearTimeout,
    };

    vm.runInNewContext(fs.readFileSync(SCRIPT_PATH, 'utf8'), context);
    context.showModelSelectDetail(
        {
            getBoundingClientRect() {
                return { top: 100 };
            },
        },
        model
    );

    return detail.innerHTML;
}

test('model detail omits the complete tools section when no picker-visible tools exist', () => {
    const withoutTools = renderModelDetail({
        name: 'Text model',
        tools: [],
    });
    const withOnlyFilteredTools = renderModelDetail({
        name: 'Generation model',
        tools: ['image_generation', 'mcp', 'mcp_weather'],
    });

    for (const markup of [withoutTools, withOnlyFilteredTools]) {
        assert.doesNotMatch(markup, /model_select_no_tools|No tools/);
        assert.doesNotMatch(markup, />Tools<\/span>/);
        assert.doesNotMatch(markup, /class="model-select-icon-row"/);
        assert.match(markup, />Formats<\/span>/);
        assert.match(markup, />Performance<\/span>/);
    }
});

test('model detail still shows tools when the model advertises a picker-visible tool', () => {
    const markup = renderModelDetail({
        name: 'Search model',
        tools: ['web_search'],
    });

    assert.match(markup, />Tools<\/span>/);
    assert.match(markup, /class="model-select-icon-row"/);
    assert.match(markup, /aria-label="Web search"/);
});

test('model detail shows only backend-authorized MCP connection summaries', () => {
    const markup = renderModelDetail({
        name: 'Connected model',
        tools: ['mcp'],
        model_select_connections: [
            { provider: 'github', title: 'GitHub' },
            { provider: 'notion', title: 'Notion' },
        ],
    });

    assert.match(markup, />Connections<\/span>/);
    assert.match(markup, /model-select-connections-section/);
    assert.match(markup, />GitHub<\/span>/);
    assert.match(markup, />Notion<\/span>/);
    assert.doesNotMatch(markup, />Tools<\/span>/);
});

test('model detail omits connections when no authorized summaries are present', () => {
    const markup = renderModelDetail({
        name: 'MCP model without group connections',
        tools: ['mcp'],
        model_select_connections: [],
    });

    assert.doesNotMatch(markup, />Connections<\/span>/);
    assert.doesNotMatch(markup, /model-select-connections-section/);
    assert.doesNotMatch(markup, /No connections/);
});
