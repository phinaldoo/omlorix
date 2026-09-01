const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const workspaceSource = fs.readFileSync(path.join(__dirname, 'workspace.js'), 'utf8');
const modalSource = fs.readFileSync(path.join(__dirname, 'deleteWarningModals.js'), 'utf8');
const authSource = fs.readFileSync(path.join(__dirname, '..', 'common', 'auth.js'), 'utf8');
const scriptSource = fs.readFileSync(path.join(__dirname, 'script.js'), 'utf8');

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName}`);
    const bodyStart = source.indexOf('{', source.indexOf(')', start));
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
    }
    throw new Error(`could not extract ${functionName}`);
}

test('prompt acceptance endpoints preserve clone and subscription semantics', () => {
    const context = {
        PROMPT_SHARE_TYPES: new Set(['clone', 'live', 'collaborate']),
        encodeURIComponent,
    };
    vm.runInNewContext(
        `${extractFunction(workspaceSource, 'getPromptShareAcceptanceEndpoint')}\nthis.endpoint = getPromptShareAcceptanceEndpoint;`,
        context,
        { filename: 'workspace.js' },
    );

    assert.equal(context.endpoint('clone', 'token / value'), '/api/v1/prompts/clone/token%20%2F%20value');
    assert.equal(context.endpoint('live', 'live-token'), '/api/v1/prompts/shared/live-token/accept');
    assert.equal(context.endpoint('collaborate', 'edit-token'), '/api/v1/prompts/shared/edit-token/accept');
    assert.equal(context.endpoint('unknown', 'token'), null);
});

test('preview response is authoritative for the accepted prompt share mode', () => {
    assert.match(
        workspaceSource,
        /const actualShareType = String\(payload\?\.share_type[\s\S]*?this\.pendingPromptShareType = actualShareType[\s\S]*?getPromptShareAcceptanceEndpoint\(shareType, shareId\)/,
    );
});

test('prompt share URLs are captured before being replaced with a non-secret route', () => {
    assert.match(
        workspaceSource,
        /handleSharedPromptRoute\(intent\)[\s\S]*?storePendingPromptShare\(intent\)[\s\S]*?history\.replaceState\([\s\S]*?'\/workspace\/prompts'/,
    );
    assert.match(workspaceSource, /sessionStorage\?\.removeItem\(PROMPT_SHARE_PENDING_STORAGE_KEY\)/);
});

test('prompt share intent remains recoverable until acceptance or cancellation', () => {
    const previewSuccessStart = workspaceSource.indexOf('this.pendingPromptShareType = actualShareType;');
    const previewFailureStart = workspaceSource.indexOf('} catch (error) {', previewSuccessStart);
    assert.ok(previewSuccessStart >= 0 && previewFailureStart > previewSuccessStart);
    assert.doesNotMatch(
        workspaceSource.slice(previewSuccessStart, previewFailureStart),
        /clearStoredPendingPromptShare/,
    );
    const previewFailureEnd = workspaceSource.indexOf('} finally {', previewFailureStart);
    assert.ok(previewFailureEnd > previewFailureStart);
    assert.doesNotMatch(
        workspaceSource.slice(previewFailureStart, previewFailureEnd),
        /clearStoredPendingPromptShare/,
    );
    assert.match(
        workspaceSource,
        /const payload = await response\.json[\s\S]*?this\.hidePromptAcceptModal\(\)/,
    );
    assert.match(
        workspaceSource,
        /hidePromptAcceptModal\([\s\S]*?if \(!preserveStoredIntent\) this\.clearStoredPendingPromptShare\(\)/,
    );
});

test('auth bootstrap removes prompt bearer links before creating a login redirect', () => {
    const stored = new Map();
    const historyCalls = [];
    const context = {
        PROMPT_SHARE_PENDING_STORAGE_KEY: 'omlorix_pending_prompt_share',
        decodeURIComponent,
        JSON,
        console,
        window: {
            location: { pathname: '/prompts/live/token%2D123' },
            history: {
                state: { existing: true },
                replaceState: (...args) => historyCalls.push(args),
            },
            sessionStorage: {
                getItem: (key) => stored.get(key) ?? null,
                setItem: (key, value) => stored.set(key, value),
            },
        },
    };
    vm.runInNewContext(
        `${extractFunction(authSource, 'capturePromptShareRedirectIntent')}\nthis.capture = capturePromptShareRedirectIntent;`,
        context,
        { filename: 'auth.js' },
    );

    assert.equal(context.capture(), true);
    assert.deepEqual(JSON.parse(stored.get('omlorix_pending_prompt_share')), {
        shareId: 'token-123',
        shareType: 'live',
    });
    assert.equal(historyCalls.length, 1);
    assert.equal(historyCalls[0][2], '/workspace/prompts');
    assert.deepEqual(JSON.parse(JSON.stringify(historyCalls[0][0])), {
        existing: true,
        workspaceTab: 'prompts',
        pendingPromptShare: true,
    });
});

test('auth bootstrap preserves the bearer URL when session storage is unavailable', () => {
    const historyCalls = [];
    const context = {
        PROMPT_SHARE_PENDING_STORAGE_KEY: 'omlorix_pending_prompt_share',
        decodeURIComponent,
        JSON,
        console: { warn: () => {} },
        window: {
            location: { pathname: '/prompts/collaborate/share-token' },
            history: { state: null, replaceState: (...args) => historyCalls.push(args) },
            sessionStorage: {
                getItem: () => null,
                setItem: () => { throw new Error('storage unavailable'); },
            },
        },
    };
    vm.runInNewContext(
        `${extractFunction(authSource, 'capturePromptShareRedirectIntent')}\nthis.capture = capturePromptShareRedirectIntent;`,
        context,
        { filename: 'auth.js' },
    );

    assert.equal(context.capture(), false);
    assert.equal(historyCalls.length, 0);
    assert.equal(context.window.location.pathname, '/prompts/collaborate/share-token');
});

test('prompt share routes reject encoded path separators after decoding', () => {
    const context = { decodeURIComponent };
    vm.runInNewContext(
        [
            extractFunction(scriptSource, 'normalizeRoutePath'),
            extractFunction(scriptSource, 'parsePromptShareRoute'),
            'this.parse = parsePromptShareRoute;',
        ].join('\n'),
        context,
        { filename: 'script.js' },
    );

    assert.deepEqual(
        JSON.parse(JSON.stringify(context.parse('/prompts/live/share-token'))),
        { shareId: 'share-token', shareType: 'live' },
    );
    assert.equal(context.parse('/prompts/live/..%2Fadmin'), null);
    assert.equal(context.parse('/prompts/live/..%5Cadmin'), null);
});

test('auth bootstrap does not persist prompt IDs containing decoded separators', () => {
    const stored = [];
    const historyCalls = [];
    const context = {
        PROMPT_SHARE_PENDING_STORAGE_KEY: 'omlorix_pending_prompt_share',
        decodeURIComponent,
        JSON,
        console,
        window: {
            location: { pathname: '/prompts/live/..%2Fadmin' },
            history: { state: null, replaceState: (...args) => historyCalls.push(args) },
            sessionStorage: {
                getItem: () => null,
                setItem: (...args) => stored.push(args),
            },
        },
    };
    vm.runInNewContext(
        `${extractFunction(authSource, 'capturePromptShareRedirectIntent')}\nthis.capture = capturePromptShareRedirectIntent;`,
        context,
        { filename: 'auth.js' },
    );

    assert.equal(context.capture(), false);
    assert.deepEqual(stored, []);
    assert.deepEqual(historyCalls, []);
});

test('auth prompt-share capture is scoped inside its classic-script IIFE', () => {
    assert.match(authSource, /^\(function \(\) \{/);
    assert.match(authSource, /\}\)\(\);\s*$/);
});

test('the shared prompt confirmation modal exposes the required accessible controls', () => {
    for (const id of [
        'promptAcceptOverlay',
        'promptAcceptTitle',
        'promptAcceptDescription',
        'promptAcceptPreviewContent',
        'promptAcceptConfirmBtn',
    ]) {
        assert.match(modalSource, new RegExp(id));
    }
    assert.match(modalSource, /describedby: 'promptAcceptDescription'/);
    assert.match(modalSource, /overlayAttrs: \{ 'aria-hidden': 'true' \}/);
    assert.match(workspaceSource, /promptAcceptOverlay[\s\S]*?overlay\.classList\.add\('active'\)/);
});
