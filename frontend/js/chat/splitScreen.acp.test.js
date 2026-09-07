const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const splitSource = [
    'splitScreen/core.js',
    'splitScreen/lifecycle.js',
    'splitScreen/streaming.js',
    'splitScreen/controls.js',
].map((sourcePath) => fs.readFileSync(path.join(__dirname, sourcePath), 'utf8')).join('\n');
const sendSource = fs.readFileSync(path.join(__dirname, 'sending/regeneration.js'), 'utf8');

test('split-screen requests preserve independent ACP session controls', () => {
    assert.match(splitSource, /leftAcpState/);
    assert.match(splitSource, /rightAcpState/);
    assert.match(splitSource, /acp_model_id:\s*acpState\.modelId \|\| null/);
    assert.match(splitSource, /acp_session_id:\s*acpState\.sessionId \|\| null/);
    assert.match(splitSource, /acp_security_level:\s*acpState\.securityLevel \|\| null/);
    assert.match(splitSource, /acp_reasoning_effort:\s*acpState\.reasoningEffort \|\| null/);
});

test('split-screen consumes ACP configuration and permission events', () => {
    assert.match(splitSource, /obj\.t === 'acp_config'/);
    assert.match(splitSource, /UpdatePanelAcpState\(side, obj\.d \|\| \{\}\)/);
    assert.match(splitSource, /obj\.t === 'acp_permission'/);
    assert.match(splitSource, /await window\.handleAcpPermissionRequest\?\.\(obj\.d \|\| \{\}\)/);
});

test('permission prompts are serialized when both split panels request approval', () => {
    assert.match(sendSource, /acpPermissionDecisionQueue/);
    assert.match(sendSource, /acpPermissionDecisionQueue\.then\(resolveRequest, resolveRequest\)/);
});
