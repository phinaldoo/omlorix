const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const FUNCTIONS_PATH = path.join(__dirname, 'functions.js');
const INDEX_PATH = path.join(__dirname, '..', '..', 'index.html');
const SCRIPT_PATH = path.join(__dirname, 'script.js');
const WORKSPACE_PATH = path.join(__dirname, 'workspace.js');
const PERSONAL_MCP_PATH = path.join(__dirname, 'userSettings', 'mcp.js');
const USER_SETTINGS_INIT_PATH = path.join(__dirname, 'userSettings', 'init.js');

function loadWorkspacePolicyRuntime() {
    const calls = {
        visibility: [],
        managed: [],
        personal: [],
    };
    const windowObject = {
        addEventListener() {},
        MCPSettings: {
            setPolicy(policy) {
                calls.personal.push(policy);
            },
        },
        ConnectionsWorkspace: {
            setPolicy(allowed) {
                calls.managed.push(allowed);
            },
        },
    };
    const context = {
        console,
        document: {
            body: { style: {}, classList: { remove() {}, toggle() {} } },
            addEventListener() {},
            getElementById() { return null; },
            querySelector() { return null; },
            querySelectorAll() { return []; },
        },
        window: windowObject,
    };
    context.globalThis = context;
    vm.runInNewContext(fs.readFileSync(FUNCTIONS_PATH, 'utf8'), context, {
        filename: FUNCTIONS_PATH,
    });

    // Capture only the public visibility decision; each family-specific policy
    // call is observed through the mocked modules above.
    context.updateWorkspaceFeatureVisibility = (tab, allowed) => {
        calls.visibility.push({ tab, allowed });
    };
    return { calls, context, window: windowObject };
}

test('workspace connection families keep managed and personal policies independent', () => {
    const cases = [
        {
            name: 'managed only',
            policy: { allow_workspace_connections: true, allow_mcp: false },
            managed: true,
            personal: false,
            visible: true,
        },
        {
            name: 'personal only',
            policy: { allow_workspace_connections: false, allow_mcp: true },
            managed: false,
            personal: true,
            visible: true,
        },
        {
            name: 'managed and personal',
            policy: { allow_workspace_connections: true, allow_mcp: true },
            managed: true,
            personal: true,
            visible: true,
        },
        {
            name: 'neither family',
            policy: { allow_workspace_connections: false, allow_mcp: false },
            managed: false,
            personal: false,
            visible: false,
        },
    ];

    cases.forEach(({ name, policy, managed, personal, visible }) => {
        const runtime = loadWorkspacePolicyRuntime();
        runtime.context.initWorkspaceConnections(policy);

        assert.deepEqual(runtime.calls.managed, [managed], `${name}: managed policy`);
        assert.deepEqual(
            runtime.calls.personal.map((entry) => entry.allow_mcp),
            [personal],
            `${name}: personal policy`,
        );
        assert.deepEqual(
            runtime.calls.visibility,
            [{ tab: 'connections', allowed: visible }],
            `${name}: aggregate workspace visibility`,
        );
        assert.equal(runtime.window.connectionsAllowed, visible, `${name}: route policy`);
    });
});

test('connections route does not treat a disabled personal MCP policy as a managed-catalog denial', () => {
    const routeSource = fs.readFileSync(SCRIPT_PATH, 'utf8');
    const routeHelper = routeSource.match(/function isConnectionsWorkspaceAllowed\(\) \{[\s\S]*?\n\}/)?.[0] || '';
    const workspaceSource = fs.readFileSync(WORKSPACE_PATH, 'utf8');
    const tabHelper = workspaceSource.match(/isTabAllowed\(tabId\) \{[\s\S]*?\n    \},/)?.[0] || '';

    assert.ok(routeHelper, 'route policy helper must exist');
    assert.ok(tabHelper, 'workspace tab policy helper must exist');
    assert.doesNotMatch(routeHelper, /chatSetup\.allow_mcp/);
    assert.doesNotMatch(tabHelper, /chatSetup\.allow_mcp/);
});

test('personal MCP initialization never falls back to aggregate connection access', () => {
    const source = fs.readFileSync(PERSONAL_MCP_PATH, 'utf8');
    const initBlock = source.match(/function init\(\) \{[\s\S]*?\n    \}/)?.[0] || '';

    assert.ok(initBlock, 'personal MCP initialization block must exist');
    assert.doesNotMatch(initBlock, /connectionsAllowed/);
});

test('personal MCP uses Workspace Connections as its only route contract', () => {
    const indexSource = fs.readFileSync(INDEX_PATH, 'utf8');
    const mcpSource = fs.readFileSync(PERSONAL_MCP_PATH, 'utf8');
    const userSettingsSource = fs.readFileSync(USER_SETTINGS_INIT_PATH, 'utf8');

    assert.match(indexSource, /id="workspaceTabConnections"[^>]+aria-controls="workspaceSectionConnections"/);
    assert.match(indexSource, /id="workspaceSectionConnections"[^>]+aria-labelledby="workspaceTabConnections"/);
    assert.match(mcpSource, /getElementById\('workspaceSectionConnections'\)/);
    assert.match(mcpSource, /window\.MCPSettings\s*=/);

    assert.doesNotMatch(indexSource, /data-(?:section|us-page)="mcp"/);
    assert.doesNotMatch(
        userSettingsSource,
        /mcpNavItem|mcpSettingsPage|applyMcpVisibility|us_page_mcp_title/,
    );
});

test('bookmark sharing defaults to enabled while preserving an explicit denial', () => {
    const runtime = loadWorkspacePolicyRuntime();

    runtime.context.initWorkspaceBookmarks(true);
    assert.equal(runtime.window.allowBookmarkShareFeature, true);

    runtime.context.initWorkspaceBookmarks(true, false);
    assert.equal(runtime.window.allowBookmarkShareFeature, false);
});
