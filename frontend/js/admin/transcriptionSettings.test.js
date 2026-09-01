const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');


const LOCALES = [
    'ar', 'de', 'en', 'es', 'fr', 'hi', 'it', 'ja', 'pt', 'ru', 'zh',
];

const ROUTING_KEYS = [
    'schema_models_sec1_title',
    'schema_models_sec1_desc',
    'schema_models_transcription_enabled',
    'schema_models_transcription_provider_id',
    'schema_models_transcription_model',
    'schema_models_live_transcription_sec_title',
    'schema_models_live_transcription_sec_desc',
    'schema_models_live_transcription_enabled',
    'schema_models_live_transcription_provider_id',
    'schema_models_live_transcription_model',
    'schema_models_live_transcription_xai_language',
    'schema_models_live_transcription_xai_language_desc',
    'schema_models_live_transcription_xai_language_placeholder',
    'schema_models_live_transcription_xai_endpointing_ms',
    'schema_models_live_transcription_xai_endpointing_ms_desc',
    'schema_models_live_transcription_xai_keyterms',
    'schema_models_live_transcription_xai_keyterms_desc',
    'schema_models_live_transcription_xai_keyterms_placeholder',
    'schema_models_live_transcription_xai_filler_words',
    'schema_models_live_transcription_xai_filler_words_desc',
    'schema_models_live_transcription_xai_smart_turn',
    'schema_models_live_transcription_xai_smart_turn_desc',
    'schema_models_live_transcription_xai_smart_turn_timeout_ms',
    'schema_models_live_transcription_xai_smart_turn_timeout_ms_desc',
    'schema_models_live_transcription_xai_vad_threshold',
    'schema_models_live_transcription_xai_vad_threshold_desc',
];


const readAdminLocale = (locale) => JSON.parse(readFileSync(
    path.join(__dirname, `../../i18n/${locale}/admin.json`),
    'utf8',
));


test('admin transcription settings distinguish file and live routing in every locale', () => {
    for (const locale of LOCALES) {
        const translations = readAdminLocale(locale);
        for (const key of ROUTING_KEYS) {
            assert.ok(
                String(translations[key] || '').trim(),
                `${locale} is missing ${key}`,
            );
        }
        assert.notEqual(
            translations.schema_models_sec1_title,
            translations.schema_models_live_transcription_sec_title,
        );
    }
});


test('English admin guidance states priority, meeting routing, and fallback', () => {
    const translations = readAdminLocale('en');

    assert.match(translations.schema_models_sec1_title, /File & meeting/);
    assert.match(translations.schema_models_sec1_desc, /fallback/);
    assert.match(translations.schema_models_live_transcription_sec_title, /Live chat/);
    assert.match(translations.schema_models_live_transcription_sec_desc, /Used first/);
    assert.match(
        translations.schema_models_live_transcription_sec_desc,
        /meetings still use File & meeting transcription/,
    );
});
