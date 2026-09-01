const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readStreamMessagesSource } = require('./messages/source.cjs');

const source = readStreamMessagesSource();

/**
 * Extract one top-level function without evaluating the full browser bundle.
 * This keeps the tests focused on the edit/regenerate orchestration contract.
 */
function extractFunction(functionName) {
    const functionStart = source.indexOf(`function ${functionName}`);
    assert.notEqual(functionStart, -1, `${functionName} not found`);
    // Preserve the async modifier when extracting orchestration functions that
    // await fetches or downstream generation work.
    const start = source.slice(functionStart - 6, functionStart) === 'async '
        ? functionStart - 6
        : functionStart;

    const paramsStart = source.indexOf('(', start);
    let paramsDepth = 0;
    let bodyStart = -1;
    for (let index = paramsStart; index < source.length; index += 1) {
        if (source[index] === '(') paramsDepth += 1;
        if (source[index] === ')') {
            paramsDepth -= 1;
            if (paramsDepth === 0) {
                bodyStart = source.indexOf('{', index);
                break;
            }
        }
    }
    assert.notEqual(bodyStart, -1, `${functionName} body start not found`);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
    }
    throw new Error(`${functionName} body was not closed`);
}

function loadEligibilityHelpers({ allowed = true, latestUser = true, linkedUser = true } = {}) {
    const editedUser = { id: 'edited-user' };
    const otherUser = { id: 'other-user' };
    const assistant = {
        id: 'a-assistant-1',
        dataset: { isLatestVersion: 'true', retryCount: '0' },
    };
    const chatAreaContainer = {
        querySelectorAll(selector) {
            if (selector === '.user-message-container') {
                return latestUser ? [otherUser, editedUser] : [editedUser, otherUser];
            }
            if (selector === '.assistant-message-container') return [assistant];
            return [];
        },
    };
    const context = {
        window: { chatSetup: { allow_regenerate_response: allowed } },
        document: {
            getElementById(id) {
                return id === 'chatAreaContainer' ? chatAreaContainer : null;
            },
        },
        canRegenerateAssistantMessage: () => true,
        getAssistantRegenerateUserMessageTarget: () => ({
            userMessageContainer: linkedUser ? editedUser : otherUser,
        }),
    };

    const helpers = vm.runInNewContext(
        [
            extractFunction('isUserMessageEditRegenerationAllowed'),
            extractFunction('getUserMessageEditRegenerationTarget'),
            '({ isUserMessageEditRegenerationAllowed, getUserMessageEditRegenerationTarget });',
        ].join('\n'),
        context,
        { filename: 'streamMessages.userEditRegenerationEligibility.js' },
    );
    helpers.editedUser = editedUser;
    return helpers;
}

test('edit regeneration eligibility requires the live group permission', () => {
    assert.equal(loadEligibilityHelpers({ allowed: true }).isUserMessageEditRegenerationAllowed(), true);
    assert.equal(loadEligibilityHelpers({ allowed: false }).isUserMessageEditRegenerationAllowed(), false);

    const missingSetupContext = { window: {} };
    const { isUserMessageEditRegenerationAllowed } = vm.runInNewContext(
        `${extractFunction('isUserMessageEditRegenerationAllowed')}\n({ isUserMessageEditRegenerationAllowed });`,
        missingSetupContext,
    );
    assert.equal(isUserMessageEditRegenerationAllowed(), false);
});

test('edit regeneration eligibility resolves only the latest linked user response', () => {
    const eligible = loadEligibilityHelpers();
    assert.equal(eligible.getUserMessageEditRegenerationTarget({
        userMessageContainer: eligible.editedUser,
    }).assistantMessageId, 'assistant-1');

    const notLatest = loadEligibilityHelpers({ latestUser: false });
    assert.equal(notLatest.getUserMessageEditRegenerationTarget({
        userMessageContainer: notLatest.editedUser,
    }), null);

    const notLinked = loadEligibilityHelpers({ linkedUser: false });
    assert.equal(notLinked.getUserMessageEditRegenerationTarget({
        userMessageContainer: notLinked.editedUser,
    }), null);
});

function loadSaveFunction({ responseOk = true, regenerationTarget = true } = {}) {
    const calls = [];
    const session = {
        messageId: 'user-1',
        textarea: { value: 'Updated prompt' },
        userMessageContainer: {},
        userMessageContent: {},
        columnWrapper: {},
        currentFiles: [],
        currentChatReferences: [],
        pendingUploads: new Map(),
        isSaving: false,
    };
    const context = {
        window: {
            authedFetch: async (url) => {
                calls.push(`fetch:${url}`);
                return {
                    ok: responseOk,
                    json: async () => ({ detail: 'Save failed' }),
                };
            },
            triggerRegeneration: async (messageId) => {
                calls.push(`regenerate:${messageId}`);
                return true;
            },
        },
        userMessageEditHasChanges: () => true,
        updateUserMessageEditActionState: () => calls.push('update-state'),
        getUserMessageEditRegenerationTarget: () => (
            regenerationTarget ? { assistantMessageId: 'assistant-1' } : null
        ),
        buildUserMessageEditPayload: () => ({ message_id: 'user-1', content: 'Updated prompt' }),
        renderUserMessageTextContent: () => calls.push('render-message'),
        rerenderUserMessageFiles: () => calls.push('render-files'),
        normalizeUserMessageEditFiles: (files) => files,
        normalizeUserMessageEditChatReferences: (references) => references,
        exitUserMessageEditMode: () => calls.push('exit-edit'),
        notifySuccess: () => calls.push('success'),
        notifyError: () => calls.push('error'),
        notifyWarning: () => calls.push('warning'),
        getStreamText: (_key, fallback) => fallback,
        console: { error() {} },
    };
    const { saveUserMessageEdit } = vm.runInNewContext(
        `${extractFunction('saveUserMessageEdit')}\n({ saveUserMessageEdit });`,
        context,
        { filename: 'streamMessages.saveUserMessageEdit.js' },
    );
    return { calls, saveUserMessageEdit, session };
}

test('Save & regenerate commits the edit before triggering the existing regeneration flow', async () => {
    const runtime = loadSaveFunction();
    const saved = await runtime.saveUserMessageEdit(runtime.session, { regenerateAfterSave: true });

    assert.equal(saved, true);
    assert.deepEqual(runtime.calls, [
        'update-state',
        'fetch:/api/v1/chats/messages/edit',
        'render-message',
        'render-files',
        'exit-edit',
        'success',
        'regenerate:assistant-1',
    ]);
});

test('ordinary Save keeps its existing save-only behavior', async () => {
    const runtime = loadSaveFunction();
    const saved = await runtime.saveUserMessageEdit(runtime.session);

    assert.equal(saved, true);
    assert.equal(runtime.calls.includes('regenerate:assistant-1'), false);
    assert.equal(runtime.calls.includes('success'), true);
});

test('a failed edit never starts regeneration', async () => {
    const runtime = loadSaveFunction({ responseOk: false });
    const saved = await runtime.saveUserMessageEdit(runtime.session, { regenerateAfterSave: true });

    assert.equal(saved, false);
    assert.equal(runtime.calls.includes('regenerate:assistant-1'), false);
    assert.equal(runtime.calls.includes('error'), true);
});

test('a stale combined action cannot save or regenerate a non-latest message', async () => {
    const runtime = loadSaveFunction({ regenerationTarget: false });
    const saved = await runtime.saveUserMessageEdit(runtime.session, { regenerateAfterSave: true });

    assert.equal(saved, false);
    assert.deepEqual(runtime.calls, ['warning']);
});

test('the split action and translations are present in every supported locale', () => {
    const createComposer = extractFunction('createUserMessageEditComposer');
    const css = fs.readFileSync(path.join(__dirname, '..', '..', 'css', 'chat', 'chat.css'), 'utf8');

    assert.match(createComposer, /if \(regenerationTarget\) \{/);
    assert.match(createComposer, /setAttribute\('aria-haspopup', 'menu'\)/);
    assert.match(createComposer, /saveUserMessageEdit\(session, \{ regenerateAfterSave: true \}\)/);
    assert.match(createComposer, /className = 'select-dropdown user-message-edit-save-dropdown'/);
    assert.match(css, /\.user-message-edit-save-actions\s*\{/);
    assert.match(css, /\.user-message-edit-save-dropdown\s*\{[\s\S]*?top: calc\(100% \+ 8px\);[\s\S]*?bottom: auto;/);
    assert.match(css, /@media \(hover: hover\) and \(pointer: fine\)[\s\S]*\.user-message-edit-save-menu-btn:hover/);
    assert.match(css, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.user-message-edit-save-menu-btn svg/);

    const i18nRoot = path.join(__dirname, '..', '..', 'i18n');
    const locales = fs.readdirSync(i18nRoot).filter((locale) => (
        fs.existsSync(path.join(i18nRoot, locale, 'index.json'))
    ));
    for (const locale of locales) {
        const messages = JSON.parse(fs.readFileSync(path.join(i18nRoot, locale, 'index.json'), 'utf8'));
        assert.ok(messages.chat_edit_save_options, `${locale} is missing chat_edit_save_options`);
        assert.ok(messages.chat_edit_save_and_regenerate, `${locale} is missing chat_edit_save_and_regenerate`);
    }
});
