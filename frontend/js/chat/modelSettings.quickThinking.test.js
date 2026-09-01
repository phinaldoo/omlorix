const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readSendMessageSource } = require('./sending/source.cjs');

const sendMessageSource = readSendMessageSource();

function loadModelSettingsHelpers(translations = {}, options = {}) {
    const document = {
        readyState: 'loading',
        addEventListener: () => {},
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => [],
        createElement: () => ({
            appendChild: () => {},
            classList: { add: () => {}, remove: () => {}, toggle: () => {} },
            dataset: {},
            setAttribute: () => {},
        }),
    };
    const window = {
        addEventListener: () => {},
        dispatchEvent: () => {},
        getTranslation: (key, fallback) => translations[key] || fallback,
        authedFetch: options.authedFetch,
    };
    const context = {
        console,
        CustomEvent: class CustomEvent {
            constructor(type, options = {}) {
                this.type = type;
                this.detail = options.detail;
            }
        },
        document,
        fetch: async () => {
            throw new Error('fetch should not be called in this helper test');
        },
        window,
    };
    context.globalThis = context;

    const source = fs.readFileSync(
        path.join(__dirname, 'modelSettings.js'),
        'utf8'
    );
    vm.runInNewContext(source, context, { filename: 'frontend/js/chat/modelSettings.js' });
    return window;
}

test('compact file format groups expand through one cached catalog request', async () => {
    let requestCount = 0;
    const helpers = loadModelSettingsHelpers({}, {
        authedFetch: async (url) => {
            requestCount += 1;
            assert.equal(url, '/api/v1/llm/model/file-format-catalog');
            return {
                ok: true,
                json: async () => ({
                    groups: {
                        image: ['image/png'],
                        document: ['application/pdf'],
                    },
                }),
            };
        },
    });
    const payload = {
        supported_file_format_groups: ['image', 'document'],
    };

    const first = await helpers.expandSupportedFileFormatsFromSchemaPayload(payload);
    const second = await helpers.expandSupportedFileFormatsFromSchemaPayload(payload);

    assert.deepEqual(JSON.parse(JSON.stringify(first)), [
        { category: 'image', file_formats: ['image/png'] },
        { category: 'document', file_formats: ['application/pdf'] },
    ]);
    assert.deepEqual(
        JSON.parse(JSON.stringify(second)),
        JSON.parse(JSON.stringify(first))
    );
    assert.equal(requestCount, 1);
});

test('quick thinking helper translates reasoning effort options from schema metadata', () => {
    const helpers = loadModelSettingsHelpers({
        'chatbox_thinking_option_off': 'Aus',
        'llm.shared.settings.reasoning_effort.option.low': 'Niedrig',
        'llm.shared.settings.reasoning_effort.option.medium': 'Mittel',
        'llm.shared.settings.reasoning_effort.option.high': 'Hoch',
    });
    const schema = {
        sections: [{
            fields: [
                { key: 'settings.reasoning_enabled', type: 'boolean', default: true },
                {
                    key: 'settings.reasoning_effort',
                    type: 'select',
                    default: 'medium',
                    options: [
                        { value: 'low', label: 'Low' },
                        { value: 'medium', label: 'Medium' },
                        { value: 'high', label: 'High' },
                    ],
                },
            ],
        }],
    };

    const state = helpers.getQuickThinkingControlStateFromSchema(schema, 'left-model', {});

    assert.equal(state.currentValue, 'medium');
    assert.equal(state.currentLabel, 'Mittel');
    assert.deepEqual(
        JSON.parse(JSON.stringify(state.options.map((option) => option.label))),
        ['Aus', 'Niedrig', 'Mittel', 'Hoch']
    );
});

test('quick thinking apply helper writes nested settings without mutating the source object', () => {
    const helpers = loadModelSettingsHelpers();
    const quickThinkingState = {
        meta: {
            enableFieldKey: 'settings.reasoning_enabled',
            adaptiveFieldKey: 'settings.thinking_adaptive',
            effortFieldKey: 'settings.reasoning_effort',
            budgetFieldKey: 'settings.reasoning_max_tokens',
            modeFieldKey: 'settings.reasoning_mode',
        },
    };
    const original = {
        settings: {
            reasoning_enabled: false,
            reasoning_effort: 'low',
            reasoning_max_tokens: 512,
        },
    };

    const next = helpers.applyQuickThinkingValueToSettings(original, quickThinkingState, 'high');

    assert.equal(original.settings.reasoning_enabled, false);
    assert.equal(original.settings.reasoning_effort, 'low');
    assert.deepEqual(JSON.parse(JSON.stringify(next)), {
        settings: {
            reasoning_enabled: true,
            reasoning_effort: 'high',
            reasoning_max_tokens: '',
            thinking_adaptive: false,
            reasoning_mode: 'effort',
        },
    });
});

test('model settings schema helper translates sections, fields, placeholders, and options', () => {
    const helpers = loadModelSettingsHelpers({
        'llm.shared.section_model_settings.title': 'Modelleinstellungen',
        'llm.shared.section_configure_options.description': 'Zusätzliche Optionen konfigurieren.',
        'schema_backend_temperature': 'Temperatur',
        'schema_backend_controls_randomness_for_text_generation': 'Steuert die Zufälligkeit.',
        'model_settings_select_placeholder': 'Auswählen...',
        'llm.shared.settings.reasoning_effort.option.high': 'Hoch',
    });
    const schema = {
        sections: [{
            title: 'Model settings',
            i18n_title: 'llm.shared.section_model_settings.title',
            description: 'Configure options.',
            i18n_description: 'llm.shared.section_configure_options.description',
            fields: [{
                key: 'settings.temperature',
                label: 'Temperature',
                i18n_label: 'schema_backend_temperature',
                description: 'Controls randomness for text generation.',
                i18n_description: 'schema_backend_controls_randomness_for_text_generation',
                placeholder: 'Select...',
                i18n_placeholder: 'model_settings_select_placeholder',
                type: 'select',
                options: [{
                    value: 'high',
                    label: 'High',
                    i18n_label: 'llm.shared.settings.reasoning_effort.option.high',
                }],
            }],
        }],
    };

    const translated = helpers.translateModelSettingsSchema(schema);

    assert.equal(translated.sections[0].title, 'Modelleinstellungen');
    assert.equal(translated.sections[0].description, 'Zusätzliche Optionen konfigurieren.');
    assert.equal(translated.sections[0].fields[0].label, 'Temperatur');
    assert.equal(translated.sections[0].fields[0].description, 'Steuert die Zufälligkeit.');
    assert.equal(translated.sections[0].fields[0].placeholder, 'Auswählen...');
    assert.equal(translated.sections[0].fields[0].options[0].label, 'Hoch');
    assert.equal(schema.sections[0].title, 'Model settings');
    assert.equal(schema.sections[0].fields[0].label, 'Temperature');
});

test('model settings renderer supports compact i18n-only schema text', () => {
    const helpers = loadModelSettingsHelpers({
        'schema_backend_model_context': 'Modellkontext',
        'schema_backend_temperature': 'Temperatur',
        'llm.shared.settings.reasoning_effort.option.high': 'Hoch',
    });
    const translated = helpers.translateModelSettingsSchema({
        sections: [{
            i18n_title: 'schema_backend_model_context',
            fields: [{
                key: 'settings.temperature',
                i18n_label: 'schema_backend_temperature',
                type: 'select',
                options: [{
                    value: 'high',
                    i18n_label: 'llm.shared.settings.reasoning_effort.option.high',
                }],
            }],
        }],
    });

    assert.equal(translated.sections[0].title, 'Modellkontext');
    assert.equal(translated.sections[0].fields[0].label, 'Temperatur');
    assert.equal(translated.sections[0].fields[0].options[0].label, 'Hoch');
});

test('structured mapping editor parses and validates logit bias JSON', () => {
    const helpers = loadModelSettingsHelpers({
        model_settings_invalid_json_object: 'translated invalid JSON',
        model_settings_json_object_required: 'translated object required',
        model_settings_non_negative_token_ids_required: 'translated token ID',
        model_settings_bias_range_required: 'translated bias range',
    });

    const valid = helpers.parseStructuredMappingValue(
        '{"123": -1.5, "456": 2}',
        'dict[str,float]'
    );
    assert.deepEqual(JSON.parse(JSON.stringify(valid.value)), {
        123: -1.5,
        456: 2,
    });
    assert.equal(valid.error, '');

    const invalidJson = helpers.parseStructuredMappingValue('{bad', 'dict[str,float]');
    assert.equal(invalidJson.error, 'translated invalid JSON');

    const invalidShape = helpers.parseStructuredMappingValue('[1, 2]', 'dict[str,float]');
    assert.equal(invalidShape.error, 'translated object required');

    const invalidToken = helpers.parseStructuredMappingValue(
        '{"not-a-token": 1}',
        'dict[str,float]'
    );
    assert.equal(invalidToken.error, 'translated token ID');

    const invalidBias = helpers.parseStructuredMappingValue(
        '{"123": 101}',
        'dict[str,float]'
    );
    assert.equal(invalidBias.error, 'translated bias range');
});

test('model-setting validation and regeneration messages exist in every locale', () => {
    const i18nRoot = path.join(__dirname, '../../i18n');
    const requiredKeys = [
        'model_settings_invalid_json_object',
        'model_settings_json_object_required',
        'model_settings_non_negative_token_ids_required',
        'model_settings_bias_range_required',
        'model_settings_invalid_structured_value',
        'model_settings_invalid_structured_value_regenerate',
    ];
    const locales = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);
    for (const locale of locales) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(i18nRoot, locale, 'index.json'), 'utf8'));
        for (const key of requiredKeys) {
            assert.ok(dictionary[key]?.trim(), `${locale} must translate ${key}`);
        }
    }
});

test('model-setting tool search copy exists in every locale', () => {
    const i18nRoot = path.join(__dirname, '../../i18n');
    const requiredKeys = [
        'model_settings_search_tools_placeholder',
        'model_settings_no_matching_tools',
    ];
    const locales = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);
    for (const locale of locales) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(i18nRoot, locale, 'index.json'), 'utf8'));
        for (const key of requiredKeys) {
            assert.ok(dictionary[key]?.trim(), `${locale} must translate ${key}`);
        }
    }
});

test('send and regenerate validation use distinct translation keys', () => {
    assert.match(
        sendMessageSource,
        /model_settings_invalid_structured_value'[\s\S]*?before sending/,
    );
    assert.match(
        sendMessageSource,
        /model_settings_invalid_structured_value_regenerate'[\s\S]*?before regenerating/,
    );
});

test('system-instruction schema text exists in every locale', () => {
    const i18nRoot = path.join(__dirname, '../../i18n');
    const requiredKeys = [
        'model_settings_system_instruction_label',
        'model_settings_system_instruction_description',
        'model_settings_system_instruction_placeholder',
    ];
    const locales = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);
    for (const locale of locales) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(i18nRoot, locale, 'schema.json'), 'utf8'));
        for (const key of requiredKeys) {
            assert.ok(dictionary[key]?.trim(), `${locale} must translate ${key}`);
        }
    }
});
