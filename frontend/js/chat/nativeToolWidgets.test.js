const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { readStreamMessagesSource } = require('./messages/source.cjs');

const widgetSource = fs.readFileSync(path.join(__dirname, 'native-tool-widgets.js'), 'utf8');
const streamSource = readStreamMessagesSource();
const indexSource = fs.readFileSync(path.join(__dirname, '../../index.html'), 'utf8');
const shareSource = fs.readFileSync(path.join(__dirname, '../../chat_share.html'), 'utf8');

test('all first-party tool widgets use the structured frontend renderer', () => {
    for (const type of [
        'weather',
        'quiz',
        'flashcards',
        'deep_research',
        'skill_draft',
        'notes_result',
    ]) {
        assert.match(widgetSource, new RegExp(`['"]${type}['"]`));
    }
    assert.match(streamSource, /renderMode === 'frontend'/);
    assert.doesNotMatch(streamSource, /BACKEND_SCRIPT_WIDGET_TYPES/);
});

test('native widget rendering is loaded before transcript widget rendering', () => {
    for (const source of [indexSource, shareSource]) {
        assert.ok(
            source.indexOf('/js/chat/native-tool-widgets.js')
                < source.indexOf('/js/chat/messages/shared.js'),
        );
        assert.match(source, /\/css\/chat\/native-tool-widgets\.css/);
    }
});

test('tool-controlled display values are assigned through text nodes', () => {
    assert.match(widgetSource, /node\.textContent = String\(text\)/);
    assert.match(widgetSource, /store\.textContent = JSON\.stringify\(data\)/);
    assert.doesNotMatch(widgetSource, /root\.innerHTML\s*=/);
    assert.doesNotMatch(widgetSource, /widget\.innerHTML\s*=/);
});

test('every locale translates native widget controls', () => {
    const i18nRoot = path.join(__dirname, '../../i18n');
    const requiredKeys = [
        'weather_unknown_location',
        'weather_daily_forecast',
        'quiz_complete',
        'quiz_score',
        'quiz_next',
        'flashcards_shuffle',
        'flashcards_show_answer',
        'flashcards_summary',
        'flashcards_study_again',
    ];
    for (const locale of fs.readdirSync(i18nRoot)) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(i18nRoot, locale, 'index.json'), 'utf8'));
        for (const key of requiredKeys) {
            assert.equal(typeof dictionary[key], 'string', `${locale} is missing ${key}`);
            assert.ok(dictionary[key].trim(), `${locale} has an empty ${key}`);
        }
    }
});
