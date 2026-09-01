const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const WORKSPACE_PATH = path.join(__dirname, 'workspace.js');
const WORKSPACE_CSS_PATH = path.join(__dirname, '..', '..', 'css', 'chat', 'workspace-core.css');
const INDEX_PATH = path.join(__dirname, '..', '..', 'index.html');

test('Workspace navigation implements desktop tabs and a compact title menu', () => {
    const source = fs.readFileSync(WORKSPACE_PATH, 'utf8');
    const styles = fs.readFileSync(WORKSPACE_CSS_PATH, 'utf8');
    const markup = fs.readFileSync(INDEX_PATH, 'utf8');

    assert.match(markup, /id="mainHeaderWorkspace" role="tablist"/);
    const desktopTabsMarkup = markup.match(/<nav\b[^>]*id="mainHeaderWorkspace"[\s\S]*?<\/nav>/)?.[0];
    assert.ok(desktopTabsMarkup, 'Missing desktop Workspace tablist markup');
    assert.equal((desktopTabsMarkup.match(/role="tab"/g) || []).length, 10);
    assert.equal((desktopTabsMarkup.match(/aria-selected="true"/g) || []).length, 1);
    assert.equal((desktopTabsMarkup.match(/aria-selected="false"/g) || []).length, 9);
    assert.equal((desktopTabsMarkup.match(/aria-controls="workspaceSection/g) || []).length, 10);
    assert.match(markup, /id="workspaceSectionNotifications" role="tabpanel" aria-labelledby="workspaceTabNotifications"/);
    assert.match(markup, /id="workspaceSectionBookmarks" role="tabpanel" aria-labelledby="workspaceTabBookmarks"/);
    assert.match(markup, /class="om-button" id="workspaceMobileTrigger"[^>]+aria-haspopup="menu"/);
    assert.match(markup, /id="workspaceMobileDropdown" role="menu"/);
    assert.doesNotMatch(markup, /\/css\/chat\/workspace\.css/);
    let previousStylesheetIndex = -1;
    for (const stylesheet of [
        'workspace-core.css',
        'workspace-notifications.css',
        'workspace-library.css',
        'workspace-skills.css',
        'workspace-skill-dialogs.css',
        'workspace-skill-import.css',
        'workspace-memories.css',
    ]) {
        const stylesheetIndex = markup.indexOf(`/css/chat/${stylesheet}`);
        assert.ok(stylesheetIndex > previousStylesheetIndex, `${stylesheet} must load in cascade order`);
        previousStylesheetIndex = stylesheetIndex;
    }
    assert.doesNotMatch(markup, /workspace-fab-nav|workspace-mobile-dock|workspace-header-context/);
    assert.match(source, /setupHeaderTabAccessibility\(\)/);
    assert.match(source, /tab\.setAttribute\('role', 'tab'\)/);
    assert.match(source, /tab\.setAttribute\('aria-controls', section\.id\)/);
    assert.match(source, /section\.setAttribute\('role', 'tabpanel'\)/);
    assert.match(source, /event\.key === 'ArrowLeft'/);
    assert.match(source, /event\.key === 'ArrowRight'/);
    assert.match(source, /\['ArrowUp'\]/);
    assert.match(source, /\['ArrowDown'\]/);
    assert.match(source, /event\.key === 'Home'/);
    assert.match(source, /event\.key === 'End'/);
    assert.match(source, /option\.setAttribute\('role', 'menuitemradio'\)/);
    assert.match(source, /option\.setAttribute\('aria-checked'/);
    assert.match(styles, /\.workspace-header-tab\s*\{[^}]*font-size:\s*14px;/s);
    assert.doesNotMatch(styles, /\.workspace-mobile-title-trigger\s*\{/);
    assert.match(styles, /\.workspace-header-mobile-option\s*\{[^}]*font-size:\s*14px;/s);
    assert.match(styles, /@container chat-layout \(max-width: 1000px\)/);
});
