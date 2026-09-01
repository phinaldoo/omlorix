const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const FRONTEND_PATH = path.join(__dirname, '..', '..');
const INDEX_PATH = path.join(FRONTEND_PATH, 'index.html');
const DYNAMIC_HEADER_PATHS = [
    path.join(__dirname, 'agents.js'),
    path.join(__dirname, 'workspaceConnections.js'),
    path.join(__dirname, 'userSettings', 'mcp.js'),
];

test('workspace page headers keep their titles without visible descriptions', () => {
    const indexSource = fs.readFileSync(INDEX_PATH, 'utf8');
    const workspaceStart = indexSource.indexOf('<!-- Workspace Container -->');
    const workspaceEnd = indexSource.indexOf('<!-- Chat Container -->', workspaceStart);

    // Limit this assertion to the workspace markup so descriptions belonging
    // to other application surfaces remain independent of this layout rule.
    assert.notEqual(workspaceStart, -1, 'workspace container marker should exist');
    assert.notEqual(workspaceEnd, -1, 'chat container marker should follow the workspace');
    const workspaceSource = indexSource.slice(workspaceStart, workspaceEnd);

    assert.doesNotMatch(workspaceSource, /class="projects-header-subtitle"/);

    // Agents and connection subpages are rendered into index.html at runtime,
    // so their templates must follow the same title-only header rule.
    for (const sourcePath of DYNAMIC_HEADER_PATHS) {
        const source = fs.readFileSync(sourcePath, 'utf8');
        assert.doesNotMatch(source, /class="projects-header-subtitle"/, sourcePath);
    }

    for (const titleKey of [
        'workspace_skills_title',
        'workspace_notifications_title',
        'workspace_connections_title',
        'workspace_memories_title',
        'workspace_prompts_title',
        'workspace_bookmarks_title',
    ]) {
        assert.match(workspaceSource, new RegExp(`data-i18n="${titleKey}"`));
    }
});
