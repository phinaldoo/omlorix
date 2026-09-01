const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readSendMessageSource } = require('./sending/source.cjs');

const CHAT_BOX_PATH = path.join(__dirname, 'chatBox.js');
const MODEL_SETTINGS_PATH = path.join(__dirname, 'modelSettings.js');
const SEND_MESSAGE_SOURCE = readSendMessageSource();
const SPLIT_SCREEN_PATH = path.join(__dirname, 'splitScreen.js');
const ICONS_PATH = path.join(__dirname, '..', 'common', 'icons.js');

/** Extract one ordinary function declaration from a production source file. */
function extractFunction(source, functionName) {
    const asyncStart = source.indexOf(`async function ${functionName}(`);
    const start = asyncStart >= 0 ? asyncStart : source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName}`);
    const parametersStart = source.indexOf('(', start);
    let parameterDepth = 0;
    let parametersEnd = -1;
    for (let index = parametersStart; index < source.length; index += 1) {
        if (source[index] === '(') parameterDepth += 1;
        if (source[index] === ')') {
            parameterDepth -= 1;
            if (parameterDepth === 0) {
                parametersEnd = index;
                break;
            }
        }
    }
    const bodyStart = source.indexOf('{', parametersEnd);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') {
            depth -= 1;
            if (depth === 0) return source.slice(start, index + 1);
        }
    }
    throw new Error(`Could not extract ${functionName}`);
}

test('model settings always submit an explicit MCP request allowlist without a sidebar', () => {
    const source = readFrontendSource(MODEL_SETTINGS_PATH, 'utf8');
    const context = {
        Array,
        Boolean,
        Map,
        Object,
        Set,
        String,
        modelSettingsState: { controls: new Map() },
        window: { getSelectedMcpServerIds: () => ['notion-server', 'notion-server'] },
    };
    vm.runInNewContext([
        extractFunction(source, 'assignNestedValue'),
        extractFunction(source, 'getNestedValue'),
        extractFunction(source, 'getCurrentModelSettingValues'),
        'this.result = getCurrentModelSettingValues();',
    ].join('\n\n'), context);

    assert.deepEqual(
        Array.from(context.result.settings.enabled_mcp_servers),
        ['notion-server'],
    );

    context.window.getSelectedMcpServerIds = () => [];
    vm.runInNewContext('this.emptyResult = getCurrentModelSettingValues();', context);
    assert.deepEqual(Array.from(context.emptyResult.settings.enabled_mcp_servers), []);
});

test('model settings preserve multiline system instructions as one string', () => {
    const source = readFrontendSource(MODEL_SETTINGS_PATH, 'utf8');
    assert.match(
        source,
        /const isTextarea =[^;]*inputType === 'textarea'[\s\S]*?document\.createElement\('textarea'\)/,
    );
    const control = {
        tagName: 'TEXTAREA',
        value: 'First system line.\n\nSecond system line.',
        dataset: { fieldType: 'text' },
        setAttribute: () => {},
    };
    const context = { JSON, Number, Object, String, control };
    vm.runInNewContext([
        extractFunction(source, 'parseListValue'),
        extractFunction(source, 'isStructuredMappingInputType'),
        extractFunction(source, 'parseStructuredMappingValue'),
        extractFunction(source, 'setStructuredControlValidity'),
        extractFunction(source, 'extractFieldValue'),
        "this.result = extractFieldValue({ input_type: 'textarea' }, control);",
    ].join('\n\n'), context);

    assert.equal(context.result, 'First system line.\n\nSecond system line.');
});

test('model settings multiselect keyboard options exclude hidden and disabled choices', () => {
    const source = readFrontendSource(MODEL_SETTINGS_PATH, 'utf8');
    const available = { hidden: false, disabled: false, getAttribute: () => 'false' };
    const hidden = { hidden: true, disabled: false, getAttribute: () => 'false' };
    const disabled = { hidden: false, disabled: true, getAttribute: () => 'false' };
    const ariaDisabled = { hidden: false, disabled: false, getAttribute: () => 'true' };
    const context = {
        Array,
        Boolean,
        menu: {
            querySelectorAll() {
                return [hidden, disabled, ariaDisabled, available];
            },
        },
    };

    vm.runInNewContext([
        extractFunction(source, 'isModelSettingsMultiSelectOptionAvailable'),
        extractFunction(source, 'getInteractiveModelSettingsMultiSelectOptions'),
        'this.result = getInteractiveModelSettingsMultiSelectOptions(menu);',
    ].join('\n\n'), context);

    assert.deepEqual(Array.from(context.result), [available]);
});

test('model settings multiselect mirrors runtime native availability changes', () => {
    const source = readFrontendSource(MODEL_SETTINGS_PATH, 'utf8');
    const nativeOption = { hidden: false, disabled: false, selected: true };
    const attributes = new Map();
    const customOption = {
        disabled: false,
        hidden: false,
        setAttribute(name, value) { attributes.set(name, String(value)); },
    };
    const selectionUpdates = [];
    const context = {
        Boolean,
        optionButtons: new Map([['projects', customOption]]),
        selectOptions: new Map([['projects', nativeOption]]),
        selectionUpdates,
    };
    const declarations = [
        extractFunction(source, 'isModelSettingsMultiSelectOptionAvailable'),
        extractFunction(source, 'mirrorModelSettingsMultiSelectOptionAvailability'),
        extractFunction(source, 'syncModelSettingsMultiSelectOptions'),
    ].join('\n\n');

    vm.runInNewContext([
        declarations,
        'syncModelSettingsMultiSelectOptions(selectOptions, optionButtons, '
            + '(value, selected) => selectionUpdates.push([value, selected]));',
    ].join('\n\n'), context);

    assert.equal(customOption.hidden, false);
    assert.equal(customOption.disabled, false);
    assert.equal(attributes.get('aria-disabled'), 'false');
    assert.equal(JSON.stringify(selectionUpdates), JSON.stringify([['projects', true]]));

    nativeOption.hidden = true;
    nativeOption.disabled = true;
    nativeOption.selected = false;
    vm.runInNewContext(
        'syncModelSettingsMultiSelectOptions(selectOptions, optionButtons, '
            + '(value, selected) => selectionUpdates.push([value, selected]));',
        context,
    );

    assert.equal(customOption.hidden, true);
    assert.equal(customOption.disabled, true);
    assert.equal(attributes.get('aria-disabled'), 'true');
    assert.equal(JSON.stringify(selectionUpdates.at(-1)), JSON.stringify(['projects', false]));
});

test('model settings multiselect bulk actions preserve unavailable choices', () => {
    const source = readFrontendSource(MODEL_SETTINGS_PATH, 'utf8');
    const available = { hidden: false, disabled: false, selected: false };
    const hidden = { hidden: true, disabled: false, selected: false };
    const disabled = { hidden: false, disabled: true, selected: false };
    const updates = [];
    const context = {
        Boolean,
        options: new Map([
            ['available', available],
            ['hidden', hidden],
            ['disabled', disabled],
        ]),
        updates,
    };

    vm.runInNewContext([
        extractFunction(source, 'isModelSettingsMultiSelectOptionAvailable'),
        extractFunction(source, 'setAvailableModelSettingsMultiSelectOptions'),
        'setAvailableModelSettingsMultiSelectOptions(options, true, (value) => updates.push(value));',
    ].join('\n\n'), context);

    assert.equal(available.selected, true);
    assert.equal(hidden.selected, false);
    assert.equal(disabled.selected, false);
    assert.deepEqual(updates, ['available']);
});

test('connector mentions are model-aware, mirrored to settings, and cleared after send', () => {
    const chatBoxSource = readFrontendSource(CHAT_BOX_PATH, 'utf8');
    const settingsSource = readFrontendSource(MODEL_SETTINGS_PATH, 'utf8');
    const sendSource = SEND_MESSAGE_SOURCE;
    const splitSource = readFrontendSource(SPLIT_SCREEN_PATH, 'utf8');

    assert.match(chatBoxSource, /mcp\/connectors\/mentions\?\$\{params\.toString\(\)\}/);
    assert.match(chatBoxSource, /\{ key: 'connectors', items: filteredConnectors \}/);
    assert.match(chatBoxSource, /window\.setMcpServerEnabledForCurrentRequest\?\.\(serverId, true\)/);
    assert.match(settingsSource, /setModelSettingFieldValue\('settings\.enabled_mcp_servers'/);
    assert.match(sendSource, /window\.clearAllMcpConnectorAttachments\(\)/);
    assert.match(
        chatBoxSource,
        /window\.addEventListener\('modelSelect:changed',[\s\S]*clearAllMcpConnectorAttachments\(\)/,
    );
    assert.match(
        sendSource,
        /chat_realtime_connectors_unsupported[\s\S]*return;/,
    );
    assert.match(splitSource, /settings\.settings\.enabled_mcp_servers = Array\.from\(new Set/);
});

test('managed connector mentions reuse the provider icons from connection cards', () => {
    const chatBoxSource = readFrontendSource(CHAT_BOX_PATH, 'utf8');
    const iconContext = {};
    vm.runInNewContext(readFrontendSource(ICONS_PATH, 'utf8'), iconContext, {
        filename: ICONS_PATH,
    });

    const renderedValues = [];
    const context = {
        Icons: iconContext.Icons,
        window: {
            IconPicker: {
                renderIconMarkup(value) {
                    renderedValues.push(value);
                    return value;
                },
            },
        },
        connector: { provider: 'notion', icon: '' },
    };
    vm.runInNewContext([
        extractFunction(chatBoxSource, 'getMentionMcpConnectorIcon'),
        'this.result = getMentionMcpConnectorIcon(connector);',
    ].join('\n\n'), context);

    assert.equal(iconContext.Icons.getConnectionProviderIconKey('notion'), 'notion');
    assert.equal(iconContext.Icons.getConnectionProviderIconKey('google_drive'), 'google_drive');
    assert.deepEqual(renderedValues, ['notion']);
    assert.equal(context.result, 'notion');
});

test('connector composer state participates in queue and split-screen snapshots', () => {
    const chatBoxSource = readFrontendSource(CHAT_BOX_PATH, 'utf8');
    assert.match(
        chatBoxSource,
        /mcpConnectors: collectChatComposerEntitySnapshots\(selectedMcpServerIds, mcpConnectorMetadataMap/,
    );
    assert.match(chatBoxSource, /snapshot\.mcpConnectors[\s\S]*addMcpConnectorAttachment\(normalized\)/);
});

test('failed connector fetches clear stale data only after the model context changes', async () => {
    const source = readFrontendSource(CHAT_BOX_PATH, 'utf8');

    async function runFetch({ sameContext, throws }) {
        const state = {
            connectors: [{ id: 'old-server' }],
            modelId: sameContext ? 'model-2' : 'model-1',
            projectId: '',
            lastFetched: 0,
        };
        const context = {
            console: { error() {} },
            Date,
            URLSearchParams,
            document: {
                getElementById(id) {
                    if (id === 'modelSelect') return { getAttribute: () => 'model-2' };
                    if (id === 'chatContainer') return { getAttribute: () => '' };
                    return null;
                },
            },
            mcpConnectorMentionState: state,
            window: {
                authedFetch: async () => {
                    if (throws) throw new Error('network error');
                    return { ok: false, status: 503 };
                },
            },
        };
        const fetchConnectors = vm.runInNewContext(
            `${extractFunction(source, 'fetchMcpConnectorsForMention')}\nfetchMcpConnectorsForMention;`,
            context,
        );
        const result = await fetchConnectors({ forceRefresh: true });
        return { result: Array.from(result), connectors: Array.from(state.connectors) };
    }

    for (const throws of [false, true]) {
        assert.deepEqual((await runFetch({ sameContext: true, throws })).connectors, [{ id: 'old-server' }]);
        assert.deepEqual((await runFetch({ sameContext: false, throws })).connectors, []);
    }
});

test('every chat locale contains the connector mention vocabulary', () => {
    const i18nRoot = path.join(__dirname, '..', '..', 'i18n');
    const requiredKeys = [
        'mention_connectors',
        'mention_unknown_connector',
        'mention_connector_description',
        'chat_attachment_type_connector',
        'chat_attachment_remove_connector',
        'chat_realtime_connectors_unsupported',
    ];
    fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .forEach((entry) => {
            const locale = entry.name;
            const payload = JSON.parse(readFrontendSource(path.join(i18nRoot, locale, 'index.json'), 'utf8'));
            requiredKeys.forEach((key) => {
                assert.equal(typeof payload[key], 'string', `${locale} is missing ${key}`);
                assert.ok(payload[key].trim(), `${locale} has an empty ${key}`);
            });
        });
});
