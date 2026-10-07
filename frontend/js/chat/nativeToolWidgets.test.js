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
        'study_legacy_archive',
        'flashcards_answer',
    ];
    for (const locale of fs.readdirSync(i18nRoot)) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(i18nRoot, locale, 'index.json'), 'utf8'));
        for (const key of requiredKeys) {
            assert.equal(typeof dictionary[key], 'string', `${locale} is missing ${key}`);
            assert.ok(dictionary[key].trim(), `${locale} has an empty ${key}`);
        }
    }
});


test('historical study content remains readable without executable markup or study controls', () => {
    class Node {
        constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; }
        append(...nodes) { this.children.push(...nodes); }
        appendChild(node) { this.append(node); }
        replaceChildren(...nodes) { this.children = nodes; }
    }
    const window = {};
    require('node:vm').runInNewContext(widgetSource, { window, document: { createElement: (tag) => new Node(tag) } });
    const root = new Node('div');
    for (const [type, data] of [
        ['quiz', { questions: [{ question: '<img onerror=alert(1)>', options: ['A', 'B'], correct_option_index: 1, explanation: 'Because B' }] }],
        ['flashcards', { cards: [{ front: '<script>bad()</script>', back: 'Meaning', hint: 'Hint' }] }],
    ]) {
        assert.equal(window.nativeToolWidgets.render(root, type, data), true);
        const nodes = [];
        const walk = (node) => { nodes.push(node); node.children.forEach(walk); };
        walk(root);
        assert.equal(nodes.filter((node) => node.tag === 'details').length, 1);
        assert.equal(nodes.some((node) => ['script', 'img', 'button', 'input'].includes(node.tag)), false);
        assert.ok(nodes.some((node) => node.textContent.startsWith('Answer: ')));
        assert.ok(nodes.some((node) => node.tag === 'summary' && node.textContent.startsWith('<')));
    }
});
