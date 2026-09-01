const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName} in script.js`);

    const bodyStart = source.indexOf('{', start);
    assert.notEqual(bodyStart, -1, `expected ${functionName} body`);

    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') {
            depth += 1;
        } else if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }

    throw new Error(`Could not extract ${functionName}`);
}

function loadTemporaryChatHelpers({ search = '', savedPreference = false, setup = {} } = {}) {
    const source = fs.readFileSync(path.join(__dirname, 'script.js'), 'utf8');
    const windowObject = {
        location: { search },
        chatSetup: setup,
        getChatBooleanSetting(key, fallback) {
            assert.equal(key, 'always_use_temporary_chat');
            assert.equal(fallback, false);
            return savedPreference;
        },
    };

    const context = {
        window: windowObject,
        localStorage: {
            getItem() {
                return savedPreference ? 'true' : 'false';
            },
        },
        URLSearchParams,
    };

    vm.runInNewContext(
        [
            extractFunction(source, 'readStoredBoolean'),
            extractFunction(source, 'shouldAlwaysUseTemporaryChat'),
            extractFunction(source, 'isTemporaryChatAllowed'),
            extractFunction(source, 'parseTemporaryChatQueryValue'),
            extractFunction(source, 'readTemporaryChatUrlOverride'),
            'let temporaryChatSessionOverride = readTemporaryChatUrlOverride();',
            extractFunction(source, 'getResolvedTemporaryChatMode'),
            `this.helpers = {
                parseTemporaryChatQueryValue,
                readTemporaryChatUrlOverride,
                getResolvedTemporaryChatMode,
                getTemporaryChatSessionOverride() {
                    return temporaryChatSessionOverride;
                },
            };`,
        ].join('\n\n'),
        context,
        { filename: 'script.js' },
    );

    return context.helpers;
}

test('temporary-chat=false URL parameter does not disable saved temporary chat preference', () => {
    const helpers = loadTemporaryChatHelpers({
        search: '?temporary-chat=false',
        savedPreference: true,
    });

    assert.equal(helpers.readTemporaryChatUrlOverride(), null);
    assert.equal(helpers.getResolvedTemporaryChatMode(), true);
    assert.equal(helpers.getTemporaryChatSessionOverride(), null);
});

test('temporary-chat=true URL parameter can still enable temporary chat for the session', () => {
    const helpers = loadTemporaryChatHelpers({
        search: '?temporary-chat=true',
        savedPreference: false,
    });

    assert.equal(helpers.readTemporaryChatUrlOverride(), true);
    assert.equal(helpers.getResolvedTemporaryChatMode(), true);
    assert.equal(helpers.getTemporaryChatSessionOverride(), true);
});
