const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName} in script.js`);

    const bodyStart = source.indexOf('{', start);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') {
            depth += 1;
        } else if (source[index] === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }

    throw new Error(`Could not extract ${functionName}`);
}

function runSubtitleUpdate(active) {
    const source = fs.readFileSync(path.join(__dirname, 'script.js'), 'utf8');
    const attributes = new Map([['aria-hidden', 'true']]);
    const wrapper = {
        hidden: true,
        removeAttribute(name) {
            attributes.delete(name);
        },
        setAttribute(name, value) {
            attributes.set(name, value);
        },
    };
    const subtitle = {
        textContent: 'initial disclosure',
        closest(selector) {
            assert.equal(selector, '.temp-chat-subtitle-wrapper');
            return wrapper;
        },
    };
    let disclosureBuilds = 0;
    const context = {
        tempChatSubtitle: subtitle,
        temporaryChatActive: active,
        buildTemporaryChatSubtitle() {
            disclosureBuilds += 1;
            return 'active disclosure';
        },
    };

    vm.runInNewContext(
        `${extractFunction(source, 'updateTemporaryChatSubtitle')}\nupdateTemporaryChatSubtitle();`,
        context,
        { filename: 'script.js' },
    );

    return { attributes, disclosureBuilds, subtitle, wrapper };
}

function runButtonUpdate(active) {
    const source = fs.readFileSync(path.join(__dirname, 'script.js'), 'utf8');
    const attributes = new Map();
    let subtitleUpdates = 0;
    const context = {
        window: {
            getTranslation: (key, fallback) => `translated:${key}:${fallback}`,
        },
        headerTempChatButton: {
            classList: { toggle() {} },
            setAttribute(name, value) { attributes.set(name, value); },
        },
        temporaryChatActive: active,
        updateTemporaryChatSubtitle() { subtitleUpdates += 1; },
    };
    vm.runInNewContext(
        `${extractFunction(source, 't')}\n${extractFunction(source, 'updateTemporaryChatButtonState')}\nupdateTemporaryChatButtonState();`,
        context,
        { filename: 'script.js' },
    );
    return { attributes, subtitleUpdates };
}

test('temporary-chat disclosure is absent from the initial accessibility tree', () => {
    const html = fs.readFileSync(path.join(__dirname, '../../index.html'), 'utf8');
    assert.match(
        html,
        /<div class="temp-chat-subtitle-wrapper" hidden aria-hidden="true"><span class="temp-chat-subtitle" id="tempChatSubtitle"/,
    );
});

test('temporary-chat disclosure remains semantically hidden while mode is off', () => {
    const result = runSubtitleUpdate(false);

    assert.equal(result.wrapper.hidden, true);
    assert.equal(result.attributes.get('aria-hidden'), 'true');
    assert.equal(result.disclosureBuilds, 0);
    assert.equal(result.subtitle.textContent, 'initial disclosure');
});

test('temporary-chat disclosure is exposed and updated while mode is on', () => {
    const result = runSubtitleUpdate(true);

    assert.equal(result.wrapper.hidden, false);
    assert.equal(result.attributes.has('aria-hidden'), false);
    assert.equal(result.disclosureBuilds, 1);
    assert.equal(result.subtitle.textContent, 'active disclosure');
});

test('temporary-chat control and disclosure stay synchronized through both toggle directions', () => {
    const active = runButtonUpdate(true);
    const inactive = runButtonUpdate(false);

    assert.equal(active.attributes.get('aria-pressed'), 'true');
    assert.equal(active.attributes.get('data-i18n-attr'), 'aria-label:temp_chat_on_aria;title:temp_chat_on_aria');
    assert.equal(active.attributes.get('aria-label'), 'translated:temp_chat_on_aria:Temporary chat on');
    assert.equal(active.attributes.get('title'), 'translated:temp_chat_on_aria:Temporary chat on');
    assert.equal(inactive.attributes.get('aria-pressed'), 'false');
    assert.equal(inactive.attributes.get('data-i18n-attr'), 'aria-label:temp_chat_off_aria;title:temp_chat_off_aria');
    assert.equal(inactive.attributes.get('aria-label'), 'translated:temp_chat_off_aria:Temporary chat off');
    assert.equal(inactive.attributes.get('title'), 'translated:temp_chat_off_aria:Temporary chat off');
    assert.equal(active.subtitleUpdates, 1);
    assert.equal(inactive.subtitleUpdates, 1);
});

test('language refresh cannot overwrite temporary-chat state with a static off label', () => {
    const source = fs.readFileSync(path.join(__dirname, 'script.js'), 'utf8');
    const html = fs.readFileSync(path.join(__dirname, '../../index.html'), 'utf8');
    const i18nRoot = path.join(__dirname, '../../i18n');

    assert.match(
        html,
        /id="headerTempChatButton"[^>]*data-i18n-attr="aria-label:temp_chat_off_aria;title:temp_chat_off_aria"/,
    );
    assert.match(
        source,
        /document\.addEventListener\('i18n:updated', \(\) => \{[\s\S]*?updateTemporaryChatButtonState\(\);/,
    );
    for (const locale of fs.readdirSync(i18nRoot)) {
        const indexPath = path.join(i18nRoot, locale, 'index.json');
        if (!fs.existsSync(indexPath)) continue;
        const translations = JSON.parse(fs.readFileSync(indexPath, 'utf8'));
        assert.ok(translations.temp_chat_off_aria, `${locale} is missing the inactive temporary-chat label`);
        assert.ok(translations.temp_chat_on_aria, `${locale} is missing the active temporary-chat label`);
    }
});

test('hidden temporary-chat disclosure cannot be re-exposed by component CSS', () => {
    const css = fs.readFileSync(path.join(__dirname, '../../css/chat/chatBox/chatBox.css'), 'utf8');
    assert.match(css, /\.temp-chat-subtitle-wrapper\[hidden\]\s*\{\s*display:\s*none;/);
});
