const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function createThroughputChart(models) {
    const canvas = {
        style: {},
        parentElement: {
            querySelector() {
                return null;
            },
        },
        getContext() {
            return {};
        },
    };
    let chartConfig;

    function Chart(_context, config) {
        chartConfig = config;
        this.destroy = () => {};
    }

    const context = {
        Chart,
        console,
        document: {
            hidden: false,
            documentElement: {
                getAttribute() {
                    return 'light';
                },
            },
            getElementById(id) {
                return id === 'throughputChart' ? canvas : null;
            },
            addEventListener() {},
        },
        window: {
            addEventListener() {},
            getTranslation(_key, fallback) {
                return fallback;
            },
        },
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'modelStatistics.js'), 'utf8');
    const instrumented = source.replace(
        /\n\}\)\(\);\s*$/,
        `
    window.__throughputChartTest = {
        render(models) {
            state.throughput = { models };
            updateThroughputChart();
        },
    };
})();`,
    );
    vm.runInNewContext(instrumented, context, { filename: 'modelStatistics.js' });
    context.window.__throughputChartTest.render(models);

    return chartConfig;
}

test('throughput chart renders configured model names instead of category indexes', () => {
    const config = createThroughputChart([
        {
            display_name: 'GPT-5.6 Terra',
            model_name: 'openai/gpt-5.6',
            model_id: 'model-1',
            avg_throughput: 42,
        },
        {
            model_name: 'ox-alpha',
            model_id: 'model-2',
            avg_throughput: 31,
        },
        {
            model_id: 'tool-enabled-model',
            avg_throughput: 20,
        },
    ]);

    assert.deepEqual(
        Array.from(config.data.labels),
        ['GPT-5.6 Terra', 'ox-alpha', 'tool-enabled-model'],
    );

    const categoryTick = config.options.scales.y.ticks.callback;
    assert.equal(categoryTick(0), 'GPT-5.6 Terra');
    assert.equal(categoryTick(1), 'ox-alpha');
    assert.equal(categoryTick(2), 'tool-enabled-model');
});
