const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'script.js'), 'utf8');

function extractFunction(functionName) {
    const asyncStart = source.indexOf(`async function ${functionName}(`);
    const start = asyncStart >= 0 ? asyncStart : source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName} in script.js`);
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
        if (source[index] === '}') depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
    }

    throw new Error(`Could not extract ${functionName}`);
}

function loadRouteRuntime(pathname) {
    const routeCalls = [];
    const window = {
        chatSetup: undefined,
        location: { pathname },
        realtimeCall: null,
        PromptLibraryManager: {
            handleSharedPromptRoute: (intent) => routeCalls.push(['prompt-share', intent]),
        },
    };
    const context = {
        window,
        routeTransitionToken: 0,
        hasSplitScreenUrlState: () => false,
        showAutomationsContainer: (options) => routeCalls.push(['automations', options]),
        showProjectsContainer: (options) => routeCalls.push(['projects', options]),
        showUnavailableFeatureRouteFallback: (route) => routeCalls.push(['fallback', route]),
    };

    // Only the feature-route cases execute in this regression harness. Other
    // route dependencies remain intentionally absent so unexpected routing is
    // surfaced as a test failure instead of silently passing through a stub.
    vm.runInNewContext(
        [
            extractFunction('normalizeRoutePath'),
            extractFunction('parsePromptShareRoute'),
            extractFunction('isChatSetupReadyForFeatureRoutes'),
            extractFunction('resumeSetupDependentRouteAfterChatSetup'),
            extractFunction('handleAppRoute'),
            'this.routes = { handleAppRoute, resumeSetupDependentRouteAfterChatSetup };',
        ].join('\n\n'),
        context,
        { filename: 'script.js' },
    );

    return { routeCalls, routes: context.routes, window };
}

test('prompt share deep links route all supported modes to the prompt acceptance flow', () => {
    for (const mode of ['clone', 'live', 'collaborate']) {
        const pathname = `/prompts/${mode}/share-token`;
        const runtime = loadRouteRuntime(pathname);

        assert.equal(runtime.routes.handleAppRoute(pathname), true);
        assert.deepEqual(
            JSON.parse(JSON.stringify(runtime.routeCalls)),
            [['prompt-share', { shareId: 'share-token', shareType: mode }]],
        );
    }
});

test('prompt share route parsing is exact and safely decodes the bearer token', () => {
    const context = {};
    vm.runInNewContext(
        `${extractFunction('normalizeRoutePath')}\n${extractFunction('parsePromptShareRoute')}\nthis.parsePromptShareRoute = parsePromptShareRoute;`,
        context,
        { filename: 'script.js' },
    );

    assert.deepEqual(
        JSON.parse(JSON.stringify(context.parsePromptShareRoute('/prompts/live/token%2D123'))),
        { shareId: 'token-123', shareType: 'live' },
    );
    assert.equal(context.parsePromptShareRoute('/prompts/live/token/extra'), null);
    assert.equal(context.parsePromptShareRoute('/prompts/unknown/token'), null);
    assert.equal(context.parsePromptShareRoute('/prompts/live/%E0%A4%A'), null);
});

test('cold feature routes retain their URL until server policy is ready', () => {
    for (const [pathname, featureFlag, expectedRoute] of [
        ['/automations', 'enableAutomationsFeature', 'automations'],
        ['/projects', 'enableProjectsFeature', 'projects'],
    ]) {
        const runtime = loadRouteRuntime(pathname);

        assert.equal(runtime.routes.handleAppRoute(pathname), true);
        assert.equal(runtime.window.location.pathname, pathname);
        assert.deepEqual(runtime.routeCalls, []);

        runtime.window.chatSetup = {};
        runtime.window[featureFlag] = true;
        runtime.routes.resumeSetupDependentRouteAfterChatSetup();

        assert.equal(runtime.routeCalls.length, 1);
        assert.equal(runtime.routeCalls[0][0], expectedRoute);
        assert.equal(runtime.routeCalls[0][1].skipHistory, true);
        assert.equal(runtime.window.location.pathname, pathname);
    }
});

test('cold Automations and Projects routes wait for chat setup and resume afterward', () => {
    assert.match(
        source,
        /document\.addEventListener\('chatSetupReady',[\s\S]*?resumeSetupDependentRouteAfterChatSetup\(\)/,
    );
    assert.match(
        source,
        /function resumeSetupDependentRouteAfterChatSetup\(\)[\s\S]*?currentPath === '\/automations'[\s\S]*?currentPath === '\/projects'[\s\S]*?handleAppRoute\(currentPath\)/,
    );
    assert.match(
        source,
        /case '\/automations':[\s\S]*?if \(!isChatSetupReadyForFeatureRoutes\(\)\) return true;[\s\S]*?showAutomationsContainer\(\{ skipHistory: true \}\)/,
    );
    assert.match(
        source,
        /case '\/projects':[\s\S]*?if \(!isChatSetupReadyForFeatureRoutes\(\)\) return true;[\s\S]*?showProjectsContainer\(\{ skipHistory: true \}\)/,
    );
});

test('restored feature routes do not create duplicate browser history entries', () => {
    assert.match(
        source,
        /async function showAutomationsContainer\(options = \{\}\)[\s\S]*?if \(!options\.skipHistory && normalizeRoutePath\(window\.location\.pathname\) !== '\/automations'\)[\s\S]*?pushState/,
    );
    assert.match(
        source,
        /async function showProjectsContainer\(options = \{\}\)[\s\S]*?if \(!options\.skipHistory && normalizeRoutePath\(window\.location\.pathname\) !== '\/projects'\)[\s\S]*?pushState/,
    );
});

test('known-disabled feature deep links fall back to a valid chat URL', () => {
    assert.match(
        source,
        /case '\/automations':[\s\S]*?enableAutomationsFeature !== true[\s\S]*?showUnavailableFeatureRouteFallback\('\/automations'\)/,
    );
    assert.match(
        source,
        /case '\/projects':[\s\S]*?enableProjectsFeature !== true[\s\S]*?showUnavailableFeatureRouteFallback\('\/projects'\)/,
    );
    assert.match(
        source,
        /async function showUnavailableFeatureRouteFallback\(routePath\)[\s\S]*?navigationGuard: isCurrentTransition[\s\S]*?history\.replaceState\(null, '', '\/'\)/,
    );
});

test('an unavailable-route fallback does not render after a newer navigation', async () => {
    let resolveSplitExit;
    const calls = [];
    const window = {
        location: { pathname: '/automations' },
        history: { replaceState: (...args) => calls.push(['replaceState', ...args]) },
        SplitScreenManager: {
            active: true,
            requestDisable: () => new Promise((resolve) => { resolveSplitExit = resolve; }),
        },
    };
    const context = {
        window,
        history: window.history,
        hideWorkspaceContainer: () => calls.push(['hideWorkspace']),
    };

    vm.runInNewContext(
        [
            'let routeTransitionToken = 1;',
            extractFunction('requestSplitScreenExitForNavigation'),
            extractFunction('showChatStartContainer'),
            extractFunction('normalizeRoutePath'),
            extractFunction('showUnavailableFeatureRouteFallback'),
            'this.routes = {',
            '  showUnavailableFeatureRouteFallback,',
            '  supersede(pathname) { routeTransitionToken += 1; window.location.pathname = pathname; },',
            '};',
        ].join('\n\n'),
        context,
        { filename: 'script.js' },
    );

    const pendingFallback = context.routes.showUnavailableFeatureRouteFallback('/automations');
    context.routes.supersede('/projects');
    resolveSplitExit(true);

    assert.equal(await pendingFallback, false);
    assert.deepEqual(calls, []);
});
