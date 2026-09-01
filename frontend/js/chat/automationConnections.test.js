const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const AUTOMATIONS_PATH = path.join(__dirname, 'automations.js');
const CREATE_EDIT_FORMS_PATH = path.join(__dirname, 'createEditForms.js');
const INDEX_PATH = path.join(__dirname, '..', '..', 'index.html');
const CSS_PATH = path.join(__dirname, '..', '..', 'css', 'chat', 'automations.css');
const ELEMENTS_CSS_PATH = path.join(__dirname, '..', '..', 'css', 'common', 'elements.css');

test('automation create and edit forms expose a branded connection multi-select', () => {
    const source = fs.readFileSync(AUTOMATIONS_PATH, 'utf8');
    const formsSource = fs.readFileSync(CREATE_EDIT_FORMS_PATH, 'utf8');
    const index = fs.readFileSync(INDEX_PATH, 'utf8');
    const css = fs.readFileSync(CSS_PATH, 'utf8');

    assert.match(index, /src="\/js\/chat\/createEditForms\.js" defer/);
    assert.match(formsSource, /connectionsSelectId: isEdit \? 'automationEditConnectionsSelect' : 'automationConnectionsSelect'/);
    assert.match(formsSource, /class="automations-connections-select"[\s\S]*role="group" aria-labelledby=/);
    assert.match(source, /mcp\/connectors\/mentions\?\$\{params\.toString\(\)\}/);
    assert.match(source, /mcp_server_ids: AutomationState\.create\.selectedMcpServerIds/);
    assert.match(source, /mcp_server_ids: AutomationState\.edit\.selectedMcpServerIds/);
    assert.match(source, /getConnectionProviderIconKey\(connection\?\.provider\)/);
    assert.match(source, /loadAutomationConnections\(mode, \{ pruneUnavailable: true \}\)/);
    assert.match(source, /if \(modelChanged\) \{[\s\S]*state\.selectedMcpServerIds = \[\]/);
    assert.match(
        source,
        /function handleAutomationConnectionsSelectDocumentClick[\s\S]*?trigger\?\.classList\.remove\('open'\);[\s\S]*?trigger\?\.setAttribute\('aria-expanded', 'false'\)/,
    );
    assert.match(
        source,
        /function closeAutomationTransientDropdowns[\s\S]*?connectionsTrigger\?\.classList\.remove\('open'\);[\s\S]*?connectionsTrigger\?\.setAttribute\('aria-expanded', 'false'\)/,
    );
    assert.match(css, /\.automations-connections-dropdown-item/);
});

test('automation model picker excludes custom agents unsupported by background execution', () => {
    const source = fs.readFileSync(AUTOMATIONS_PATH, 'utf8');

    assert.match(
        source,
        /function isAutomationEligibleModel\(model\)[\s\S]*model\.model_kind !== 'agent'/,
    );
    assert.match(source, /automationsModelsCache = models\.filter\(isAutomationEligibleModel\)/);
});

test('automation model picker keeps its trigger and icons within the form control', () => {
    const css = fs.readFileSync(ELEMENTS_CSS_PATH, 'utf8');

    assert.match(css, /\.shared-model-select-trigger,[\s\S]*?min-height:\s*52px/);
    assert.match(css, /\.shared-model-select-icon,[\s\S]*?width:\s*36px;[\s\S]*?height:\s*36px/);
    assert.match(css, /\.shared-model-select-icon svg,[\s\S]*?width:\s*22px;[\s\S]*?height:\s*22px/);
    assert.match(css, /\.shared-model-select-label,[\s\S]*?text-overflow:\s*ellipsis/);
    assert.match(css, /\.shared-model-select-list\s*\{[\s\S]*?max-height:\s*280px/);
});

test('automation persistence uses stable icon IDs and the current schedule shape', () => {
    const source = fs.readFileSync(AUTOMATIONS_PATH, 'utf8');

    assert.match(source, /return iconData\?\.iconId \|\| AUTOMATION_DEFAULT_ICON_ID/);
    assert.doesNotMatch(source, /iconIndex/);
    assert.doesNotMatch(source, /\['start', 'end'\]/);
});

test('automation connection copy is translated in every supported locale', () => {
    const i18nRoot = path.join(__dirname, '..', '..', 'i18n');
    const requiredKeys = [
        'automations_create_connections_label',
        'automations_create_connections_hint',
        'automations_connections_select_model_first',
        'automations_connections_loading',
        'automations_connections_none_selected',
        'automations_connections_remove_aria',
        'automations_connections_add',
        'automations_connections_empty',
        'automations_connections_load_error',
        'automations_connections_item_description',
    ];

    fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .forEach((entry) => {
            const payload = JSON.parse(fs.readFileSync(path.join(i18nRoot, entry.name, 'index.json'), 'utf8'));
            requiredKeys.forEach((key) => {
                assert.equal(typeof payload[key], 'string', `${entry.name} is missing ${key}`);
                assert.ok(payload[key].trim(), `${entry.name} has an empty ${key}`);
            });
        });
});

test('automation persistence and execution keep an explicit MCP allowlist', () => {
    const modelSource = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'backend', 'app', 'automations', 'models.py'), 'utf8');
    const jobSource = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'backend', 'app', 'automations', 'jobs.py'), 'utf8');

    assert.match(modelSource, /mcp_server_ids = Column\(JSON/);
    assert.match(modelSource, /def remove_mcp_server_from_automations\(/);
    assert.match(jobSource, /"enabled_mcp_servers": list\(getattr\(automation, "mcp_server_ids"/);
});
