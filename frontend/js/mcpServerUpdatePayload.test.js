const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const PERSONAL_MCP_PATH = path.join(__dirname, 'chat', 'userSettings', 'mcp.js');
const ADMIN_MCP_PATH = path.join(__dirname, 'admin', 'mcpServers.js');

function saveServerBlock(filePath, nextFunctionName) {
    const source = fs.readFileSync(filePath, 'utf8');
    const start = source.indexOf('async function saveServer(');
    const end = source.indexOf(nextFunctionName, start);

    assert.notEqual(start, -1, `${filePath}: saveServer must exist`);
    assert.notEqual(end, -1, `${filePath}: saveServer boundary must exist`);
    return source.slice(start, end);
}

test('personal MCP edits omit immutable ownership from PATCH requests', () => {
    const source = fs.readFileSync(PERSONAL_MCP_PATH, 'utf8');
    const saveServer = saveServerBlock(PERSONAL_MCP_PATH, 'function updateToggleAccessibleLabel');

    assert.match(source, /owner_type: 'user'/, 'create payloads must retain user ownership');
    assert.match(saveServer, /if \(method === 'PATCH'\) delete payload\.owner_type;/);
    assert.ok(
        saveServer.indexOf("delete payload.owner_type") < saveServer.indexOf('body: JSON.stringify(payload)'),
        'owner_type must be removed before serializing the PATCH body',
    );
});

test('admin MCP edits follow the same immutable ownership contract', () => {
    const source = fs.readFileSync(ADMIN_MCP_PATH, 'utf8');
    const saveServer = saveServerBlock(ADMIN_MCP_PATH, 'async function connectOAuth');

    assert.match(source, /owner_type: 'admin'/, 'create payloads must retain admin ownership');
    assert.match(saveServer, /if \(method === 'PATCH'\) delete payload\.owner_type;/);
    assert.ok(
        saveServer.indexOf("delete payload.owner_type") < saveServer.indexOf('body: JSON.stringify(payload)'),
        'owner_type must be removed before serializing the PATCH body',
    );
});
