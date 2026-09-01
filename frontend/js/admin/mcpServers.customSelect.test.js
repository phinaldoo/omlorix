const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(__dirname, 'mcpServers.js'), 'utf8');
const styles = fs.readFileSync(path.join(root, 'css/admin/mcpServers.css'), 'utf8');

test('MCP admin create and edit forms enhance transport and authentication selects', () => {
    assert.match(source, /upgradeFormSelect\(prefix, 'Transport'\)/);
    assert.match(source, /upgradeFormSelect\(prefix, 'AuthMode'\)/);
    assert.match(source, /window\.upgradeAdminSingleSelect\(select/);
    assert.match(source, /aria-labelledby="\$\{prefix\}TransportLabel"/);
    assert.match(source, /aria-labelledby="\$\{prefix\}AuthModeLabel"/);
    assert.match(styles, /\.mcp-server-custom-select \.admin-select-trigger/);
});
