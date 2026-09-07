const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(__dirname, 'workspaceRemoteConnections.js'), 'utf8');
const dynamicFormsSource = fs.readFileSync(path.join(__dirname, 'workspaceCreateEditForms.js'), 'utf8');
const styles = fs.readFileSync(path.join(root, 'css/chat/connections.css'), 'utf8');
const mcpStyles = fs.readFileSync(path.join(root, 'css/userSettings/style.css'), 'utf8');
const markup = `${fs.readFileSync(path.join(root, 'index.html'), 'utf8')}\n${dynamicFormsSource}`;
const mcpSource = fs.readFileSync(path.join(__dirname, 'userSettings/mcp.js'), 'utf8');

async function flushPromises() {
    for (let index = 0; index < 6; index += 1) await Promise.resolve();
}

function loadDirectRouteRuntime() {
    const fetchCalls = [];
    const document = {
        activeElement: null,
        addEventListener() {},
        getElementById() { return null; },
        querySelectorAll() { return []; },
    };
    const window = {
        WorkspaceManager: { getActiveTab: () => 'connections' },
        addEventListener() {},
        authedFetch: async (url) => {
            fetchCalls.push(url);
            return {
                ok: true,
                status: 200,
                json: async () => [],
            };
        },
        location: { pathname: '/workspace/connections' },
    };
    const context = {
        console,
        document,
        requestAnimationFrame(callback) { callback(); },
        window,
    };
    context.globalThis = context;
    vm.runInNewContext(source, context, { filename: 'workspaceRemoteConnections.js' });
    return { fetchCalls, window };
}

test('remote connection UI uses owned APIs, full-page editors, and no native dialogs', () => {
    assert.match(source, /\/api\/v1\/remote-connections\/ssh/);
    assert.match(source, /\/api\/v1\/remote-connections\/ssh\/test/);
    assert.match(source, /\/api\/v1\/remote-connections\/acp/);
    assert.match(source, /returnFocus/);
    assert.match(source, /setPageVisibility\(el\('connectionsWorkspace'\), false\)/);
    assert.match(source, /setPageVisibility\(el\('sshConnectionEditorPage'\), kind === 'ssh'\)/);
    assert.match(source, /setPageVisibility\(el\('acpConnectionEditorPage'\), kind === 'acp'\)/);
    assert.doesNotMatch(source, /connectionsModalOverlay|openModal/);
    assert.doesNotMatch(source, /\b(?:alert|confirm|prompt)\s*\(/);
    assert.match(source, /el\('sshPrivateKey'\)\.value = ''/);
    assert.match(source, /if \(state\.savingSsh\) return/);
    assert.match(source, /if \(state\.savingAcp\) return/);
    assert.match(source, /form\?\.setAttribute\('aria-busy', 'true'\)/);
    assert.match(source, /await window\.ModelSelectLoadModels\?\.\(\{ forceRefresh: true \}\)/);
    assert.doesNotMatch(source, /ModelSelectLoadModels\?\.\(true\)/);
});

test('remote connection UI applies dependent group policy before loading APIs', () => {
    assert.match(source, /allowAcp = allowSsh && policy\.allow_custom_acp_connections === true/);
    assert.match(source, /if \(!state\.allowSsh\)[\s\S]*state\.ssh = \[\][\s\S]*state\.acp = \[\]/);
    assert.match(source, /state\.allowAcp[\s\S]*api\('\/api\/v1\/remote-connections\/acp'\)[\s\S]*Promise\.resolve\(\[\]\)/);
    assert.match(source, /remoteSection\.hidden = !state\.allowSsh/);
    assert.match(source, /acpSection\.hidden = !state\.allowAcp/);
});

test('direct connections route loads when async chat setup enables SSH and ACP', () => {
    assert.match(source, /const becameAvailable = \([\s\S]*!state\.allowSsh && allowSsh[\s\S]*!state\.allowAcp && allowAcp/);
    assert.match(source, /window\.WorkspaceManager\?\.getActiveTab\?\.\(\) === 'connections'/);
    assert.match(source, /routePath === '\/workspace\/connections'/);
    assert.match(source, /state\.initialized && becameAvailable && connectionsPageActive[\s\S]*void load\(\)/);
});

test('cold direct route fetches remote connections as soon as setup policy arrives', async () => {
    const { fetchCalls, window } = loadDirectRouteRuntime();

    // The initial route render happens before /settings/chat/setup resolves.
    window.RemoteConnectionsWorkspace.show();
    assert.deepEqual(fetchCalls, []);

    window.RemoteConnectionsWorkspace.setPolicy({
        allow_ssh_connections: true,
        allow_custom_acp_connections: true,
    });
    await flushPromises();

    assert.deepEqual(fetchCalls, [
        '/api/v1/remote-connections/ssh',
        '/api/v1/remote-connections/acp',
    ]);
});

test('connection sections use shared headers and the requested display order', () => {
    const serverIndex = markup.indexOf('id="connectionsCatalogBlock"');
    const customMcpIndex = markup.indexOf('id="connectionsPersonalSection"');
    const sshIndex = markup.indexOf('id="connectionsSshSection"');
    const acpIndex = markup.indexOf('id="connectionsAcpSection"');

    assert.ok(serverIndex >= 0);
    assert.ok(serverIndex < customMcpIndex);
    assert.ok(customMcpIndex < sshIndex);
    assert.ok(sshIndex < acpIndex);

    assert.match(
        markup,
        /id="connectionsSshSection"[\s\S]*?projects-header connections-shell-header connections-action-header[\s\S]*?id="addSshConnectionBtn"/,
    );
    assert.match(
        markup,
        /id="connectionsAcpSection"[\s\S]*?projects-header connections-shell-header connections-action-header[\s\S]*?id="addAcpProfileBtn"/,
    );
    assert.match(
        mcpSource,
        /projects-header connections-shell-header connections-action-header/,
    );
    assert.doesNotMatch(mcpSource, /mcp-list-header|workspace-skills-header-actions/);
});

test('personal MCP cards use compact icon actions and an immediately persisted switch', () => {
    assert.doesNotMatch(mcpSource, /mcp-server-status/);
    assert.doesNotMatch(mcpStyles, /mcp-server-status/);
    assert.match(mcpSource, /mcp-card-icon-action" data-action="edit"[\s\S]*aria-label=/);
    assert.match(mcpSource, /mcp-card-icon-action" data-action="delete"[\s\S]*aria-label=/);
    assert.match(mcpSource, /role="switch"[\s\S]*data-action="toggle"/);
    assert.match(mcpSource, /async function toggleServer/);
    assert.match(mcpSource, /method:\s*'PATCH'[\s\S]*enabled,/);
    assert.match(mcpSource, /input\.checked = previousEnabled/);
    assert.match(mcpSource, /card\.querySelectorAll\('button, input'\)[\s\S]*control\.disabled = locked/);
    assert.match(mcpSource, /finally \{[\s\S]*setServerCardMutationLocked\(card, false\)/);
    assert.match(
        mcpStyles,
        /#connectionsPersonalMcpRoot \.mcp-server-card-skin \.mcp-card-icon-action[\s\S]*width:\s*40px/,
    );
    assert.match(mcpStyles, /\.mcp-server-toggle[\s\S]*height:\s*40px/);
    assert.match(mcpSource, /data-action="oauth"/);
    assert.match(mcpSource, /mcpField_auth_mode/);
    assert.match(mcpSource, /upgradeEditorCustomSelect\('mcpField_transport'\)/);
    assert.match(mcpSource, /upgradeEditorCustomSelect\('mcpField_auth_mode'\)/);
    assert.match(mcpSource, /window\.upgradeAdminSingleSelect\(select/);
    assert.match(mcpStyles, /\.mcp-editor-custom-select \.admin-select-trigger/);
    assert.match(mcpSource, /\/oauth\/start/);
    assert.match(mcpSource, /mcp_oauth_status/);
});

test('responsive connection headers keep action buttons content-sized', () => {
    assert.match(styles, /\.connections-action-header \.om-button\.border[\s\S]*width:\s*fit-content/);
    assert.match(styles, /@media \(max-width: 480px\)[\s\S]*\.connections-shell-header\.connections-action-header[\s\S]*flex-direction:\s*column/);
    assert.match(styles, /\.remote-connection-form-grid[\s\S]*grid-template-columns:/);
    assert.match(styles, /@media \(max-width: 480px\)[\s\S]*\.remote-connection-form-grid[\s\S]*grid-template-columns:\s*minmax\(0, 1fr\)/);
});

test('SSH editor tests its current draft and persists its selected icon', () => {
    assert.match(dynamicFormsSource, /id: 'sshConnectionEditorPage'/);
    assert.match(dynamicFormsSource, /pickerId: 'sshIconPicker'/);
    assert.match(dynamicFormsSource, /id: 'testSshBtn'/);
    assert.match(source, /const testUrl = state\.editing[\s\S]*\/ssh\/\$\{encodeURIComponent\(state\.editing\.id\)\}\/test/);
    assert.match(source, /await api\(testUrl,[\s\S]*JSON\.stringify\(sshPayload\(\)\)/);
    assert.match(source, /icon:\s*state\.sshIconPicker\?\.getValue\(\) \|\| 'server'/);
});

test('SSH private keys are normalized, validated, and importable with accessible feedback', () => {
    assert.match(source, /function normalizeSshPrivateKey/);
    assert.match(source, /replace\(\/\\r\\n\?\/g, '\\n'\)\.trim\(\)/);
    assert.match(source, /function inspectSshPrivateKey/);
    assert.match(source, /SSH_PRIVATE_KEY_MAX_BYTES = 65536/);
    assert.match(source, /file\.size > SSH_PRIVATE_KEY_MAX_BYTES/);
    assert.match(source, /await file\.text\(\)/);
    assert.match(source, /setTimeout\(\(\) => preparePrivateKey\(\), 0\)/);
    assert.match(dynamicFormsSource, /id: 'chooseSshPrivateKeyBtn'/);
    assert.match(dynamicFormsSource, /id="sshPrivateKeyFile" type="file" hidden/);
    assert.match(dynamicFormsSource, /id="sshPrivateKeyStatus" role="status" aria-live="polite"/);
    assert.match(dynamicFormsSource, /id="sshConnectionFeedback" role="status" aria-live="polite"/);
});

test('SSH test failures retain actionable structured diagnostics', () => {
    assert.match(source, /function localizedSshTestFailure/);
    assert.match(source, /ssh_authentication_failed/);
    assert.match(source, /ssh_host_key_verification_failed/);
    assert.match(source, /ssh_connection_timeout/);
    assert.match(source, /ssh_connection_unreachable/);
    assert.match(source, /localizedSshTestFailure\(result\.error_code\)/);
    assert.doesNotMatch(source, /notifyError\?\.\(t\('workspace_ssh_error'[\s\S]*Connection failed[\s\S]*\)\);\n\s*}/);
});

test('ACP editor tests its current unsaved profile values', () => {
    assert.match(
        source,
        /\/acp\/\$\{encodeURIComponent\(state\.editing\.id\)\}\/test[\s\S]*JSON\.stringify\(acpPayload\(\)\)/,
    );
});

test('SSH status presentation requires an enabled connected device', () => {
    assert.match(source, /if \(!connection\.enabled\) return t\('workspace_connections_status_disabled'/);
    assert.match(source, /connection\.status\?\.state === 'connected'[\s\S]*'status-connected'[\s\S]*'status-idle'/);
});

test('SSH connections cannot replace the configured code execution service', () => {
    assert.doesNotMatch(source, /sshCodeDefault/);
    assert.doesNotMatch(source, /use_for_code_execution/);
    assert.doesNotMatch(source, /workspace_ssh_code_default/);
});

test('discovered SSH fingerprints wrap inside the editor page', () => {
    assert.match(source, /ssh-host-key-option/);
    assert.match(styles, /\.ssh-host-key-option[\s\S]*white-space:\s*normal/);
    assert.match(styles, /overflow-wrap:\s*anywhere/);
});

test('SSH and ACP pages reuse the shared icon picker with scoped capabilities', () => {
    assert.match(source, /SSH_ICON_KEYS = \['laptop', 'desktop', 'pc', 'mobile', 'server', 'terminal', 'home'\]/);
    assert.match(source, /ACP_ICON_KEYS = \['codex', 'opencode', 'terminal'/);
    assert.match(source, /state\.sshIconPicker = window\.IconPicker\.createIconPicker\(\{[\s\S]*allowCustomSvg:\s*false/);
    assert.match(source, /state\.acpIconPicker = window\.IconPicker\.createIconPicker\(\{[\s\S]*allowCustomSvg:\s*true/);
    assert.match(source, /icon:\s*state\.acpIconPicker\?\.getValue\(\) \|\| 'terminal'/);
    assert.match(dynamicFormsSource, /id: 'acpConnectionEditorPage'/);
    assert.match(dynamicFormsSource, /pickerId: 'acpIconPicker'/);
});

test('remote connection translations exist in every supported locale', () => {
    const localeRoot = path.join(root, 'i18n');
    const required = [
        'workspace_connections_title',
        'workspace_connections_mcp_list_title',
        'workspace_connections_mcp_field_auth_mode',
        'workspace_connections_mcp_auth_headers',
        'workspace_connections_mcp_auth_oauth',
        'workspace_connections_mcp_oauth_connect',
        'workspace_connections_mcp_oauth_reconnect',
        'workspace_connections_mcp_oauth_save_first',
        'workspace_connections_mcp_oauth_start_failed',
        'workspace_connections_mcp_oauth_success',
        'workspace_connections_mcp_oauth_failed',
        'workspace_ssh_title',
        'workspace_ssh_private_key',
        'workspace_ssh_create_title',
        'workspace_ssh_edit_title',
        'workspace_ssh_icon_help',
        'workspace_remote_icon_label',
        'workspace_acp_title',
        'workspace_acp_create_title',
        'workspace_acp_edit_title',
        'workspace_acp_icon_help',
        'workspace_acp_test_success',
        'workspace_acp_runtime_failed',
        'workspace_remote_validation_error',
        'workspace_remote_validation_field',
        'workspace_remote_request_failed',
        'workspace_ssh_private_key_required',
        'workspace_ssh_private_key_invalid',
        'workspace_ssh_private_key_encrypted',
        'workspace_ssh_private_key_ready',
        'workspace_ssh_private_key_ready_normalized',
        'workspace_ssh_choose_key_file',
        'workspace_ssh_private_key_file_too_large',
        'workspace_ssh_private_key_file_read_failed',
        'workspace_ssh_authentication_failed',
        'workspace_ssh_host_key_verification_failed',
        'workspace_ssh_connection_timeout',
        'workspace_ssh_connection_unreachable',
        'workspace_acp_enable_ssh_first',
        'workspace_ssh_delete_dependencies',
        'icon_picker_icon_laptop',
        'icon_picker_icon_server',
    ];
    for (const locale of fs.readdirSync(localeRoot)) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(localeRoot, locale, 'index.json'), 'utf8'));
        for (const key of required) assert.equal(typeof dictionary[key], 'string', `${locale} is missing ${key}`);
    }
});

test('structured API validation is localized and attached to accessible fields', () => {
    assert.match(source, /Array\.isArray\(payload\.detail\)/);
    assert.match(source, /fieldErrors/);
    assert.match(source, /aria-errormessage/);
    assert.match(source, /workspace_remote_validation_field/);
    assert.match(source, /focusFirstError/);
    assert.match(source, /ssh_private_key_required/);
    assert.doesNotMatch(source, /new Error\(payload\.detail/);
});

test('SSH and custom ACP group policy translations exist in every locale', () => {
    const localeRoot = path.join(root, 'i18n');
    const required = [
        'schema_group_field_settings_tools_mcp_allow_ssh_connections_label',
        'schema_group_field_settings_tools_mcp_allow_ssh_connections_description',
        'schema_group_field_settings_tools_mcp_allow_custom_acp_connections_label',
        'schema_group_field_settings_tools_mcp_allow_custom_acp_connections_description',
    ];
    for (const locale of fs.readdirSync(localeRoot)) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(localeRoot, locale, 'admin.json'), 'utf8'));
        for (const key of required) assert.equal(typeof dictionary[key], 'string', `${locale} is missing ${key}`);
    }
});
