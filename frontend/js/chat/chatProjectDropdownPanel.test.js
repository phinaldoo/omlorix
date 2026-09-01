const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const frontendRoot = path.join(__dirname, '..', '..');

test('add-to-project uses the shared in-place dropdown panel', () => {
    const chatsSource = fs.readFileSync(path.join(__dirname, 'chats.js'), 'utf8');
    const chatsHelperSource = fs.readFileSync(path.join(__dirname, 'chatsHelper.js'), 'utf8');
    const projectsChatSource = fs.readFileSync(path.join(__dirname, 'projectsChat.js'), 'utf8');
    const chatStyles = fs.readFileSync(path.join(frontendRoot, 'css', 'chat', 'chat.css'), 'utf8');

    assert.match(chatsSource, /data-dropdown-open-panel="projects"/);
    assert.match(chatsSource, /aria-controls="\$\{escapeSidebarDropdownHtml\(projectPanelId\)\}"/);
    assert.match(chatsSource, /projectPanel\.id = projectPanelId/);
    assert.match(chatsSource, /dataset\.dropdownPanel = 'projects'/);
    assert.match(chatsSource, /data-dropdown-panel-back/);
    assert.match(chatsSource, /window\.createDropdownPanelNavigator\?\.\(/);
    assert.doesNotMatch(chatsSource, /chat-select-submenu|updateSubmenuPosition|resetSubmenuPosition/);

    assert.match(chatsHelperSource, /getDropdownPanelNavigator\?\.\(dropdown\)\?\.reset\(\{ focus: false \}\)/);
    assert.match(projectsChatSource, /getDropdownPanelNavigator\?\.\(dropdown\)\?\.reset\(\{ focus: false \}\)/);
    assert.doesNotMatch(chatStyles, /\.chat-select-submenu|\.add-project-item/);
});

test('the no-project option treats an absent project id as null', () => {
    const chatsSource = fs.readFileSync(path.join(__dirname, 'chats.js'), 'utf8');

    assert.match(chatsSource, /const currentProjectId = chat\.project_id \?\? null/);
    assert.match(chatsSource, /currentProjectId === option\.id \? Icons\.check : ''/);
});
