const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const frontendRoot = path.join(__dirname, '..', '..');
const elementsCss = fs.readFileSync(path.join(frontendRoot, 'css', 'common', 'elements.css'), 'utf8');
const projectsCss = fs.readFileSync(path.join(frontendRoot, 'css', 'chat', 'projects.css'), 'utf8');
const automationsCss = fs.readFileSync(path.join(frontendRoot, 'css', 'chat', 'automations.css'), 'utf8');
const workspaceCoreCss = fs.readFileSync(path.join(frontendRoot, 'css', 'chat', 'workspace-core.css'), 'utf8');
const workspaceSkillsCss = fs.readFileSync(path.join(frontendRoot, 'css', 'chat', 'workspace-skills.css'), 'utf8');
const indexHtml = fs.readFileSync(path.join(frontendRoot, 'index.html'), 'utf8');
const createEditFormsSource = fs.readFileSync(path.join(__dirname, 'createEditForms.js'), 'utf8');
const workspaceCreateEditFormsSource = fs.readFileSync(path.join(__dirname, 'workspaceCreateEditForms.js'), 'utf8');
const agentsSource = fs.readFileSync(path.join(__dirname, 'agents.js'), 'utf8');
const mcpSource = fs.readFileSync(path.join(__dirname, 'userSettings', 'mcp.js'), 'utf8');

/**
 * Extract a CSS rule body for a literal selector so the assertions document
 * the spacing ownership without depending on a browser-specific CSS parser.
 */
function ruleBody(source, selector) {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const match = source.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`));
    assert.ok(match, `Expected CSS rule for ${selector}`);
    return match[1];
}

test('project-style pages share one bottom-gap contract', () => {
    const containerRule = ruleBody(elementsCss, '.projects-container');
    const contentRule = ruleBody(elementsCss, '.projects-content');
    const formContentRule = ruleBody(elementsCss, '.projects-content:has(> .projects-create-form)');
    const formButtonsRule = ruleBody(elementsCss, '.projects-create-form > .projects-create-buttons');

    assert.match(containerRule, /--projects-page-end-gap:\s*20px/);
    assert.match(containerRule, /padding-bottom:\s*env\(safe-area-inset-bottom,\s*0px\)/);
    assert.doesNotMatch(containerRule, /padding-bottom:\s*20px/);
    assert.match(contentRule, /padding-bottom:\s*var\(--projects-page-end-gap,\s*20px\)/);
    assert.match(contentRule, /min-height:\s*100%/);
    assert.match(contentRule, /box-sizing:\s*border-box/);
    assert.match(formContentRule, /padding-bottom:\s*0/);
    assert.match(formButtonsRule, /margin-top:\s*auto/);
    assert.match(formButtonsRule, /padding-bottom:\s*var\(--projects-page-end-gap,\s*20px\)/);
});

test('shared Project and Automation CSS lives only in the common component layer', () => {
    assert.match(elementsCss, /\.projects-content-main-element\s*\{/);
    assert.match(elementsCss, /\.project-icon\s*\{/);
    assert.match(elementsCss, /\.projects-name-and-icon-row\s*\{/);

    for (const featureCss of [projectsCss, automationsCss]) {
        assert.doesNotMatch(featureCss, /(?:^|\n)\.projects-content-main-element\s*\{/);
        assert.doesNotMatch(featureCss, /(?:^|\n)\.project-icon\s*\{/);
        assert.doesNotMatch(featureCss, /(?:^|\n)\.projects-name-and-icon-row\s*\{/);
    }

    assert.doesNotMatch(automationsCss, /\.automation-icon\b/);
    assert.doesNotMatch(
        automationsCss,
        /\.automations-(?:container|content|header|create-form|create-input|create-description|create-buttons)(?![-\w])/,
    );
});

test('Workspace mobile spacing has no obsolete dock clearance', () => {
    const projectSectionSelector = '#workspaceContainer > .workspace-section:has(> .projects-content)';
    const projectSectionRules = [...workspaceCoreCss.matchAll(
        /#workspaceContainer > \.workspace-section:has\(> \.projects-content\)\s*\{([^}]+)\}/g,
    )].map((match) => match[1]);
    const containerRule = ruleBody(workspaceCoreCss, '#workspaceContainer');
    const workspaceSectionRules = [...workspaceCoreCss.matchAll(
        /#workspaceContainer > \.workspace-section\s*\{([^}]+)\}/g,
    )].map((match) => match[1]);
    const mobileSectionRule = workspaceSectionRules[1] || '';
    const pageRootRule = ruleBody(
        workspaceCoreCss,
        '#workspaceContainer:not(.full-bleed) > .workspace-section > *',
    );

    assert.equal(projectSectionRules.length, 1);
    assert.equal(workspaceSectionRules.length, 3);
    assert.match(projectSectionRules[0], /padding-bottom:\s*0/);
    assert.match(containerRule, /grid-template-columns:\s*minmax\(0, 1fr\)/);
    assert.match(containerRule, /flex:\s*1 1 auto/);
    assert.doesNotMatch(containerRule, /workspace-mobile-dock/);
    assert.match(mobileSectionRule, /padding:\s*24px 14px 0/);
    assert.match(mobileSectionRule, /scroll-padding-bottom:\s*var\(--projects-page-end-gap,\s*20px\)/);
    assert.match(pageRootRule, /flex-shrink:\s*0/);
    assert.doesNotMatch(workspaceCoreCss, /workspace-mobile-dock|workspace-more-sheet/);
    assert.doesNotMatch(workspaceCoreCss, /#workspaceContainer > \.workspace-section::after/);
    assert.ok(workspaceCoreCss.includes(projectSectionSelector));
});

test('short Skill states keep their navigation row at the shared page end', () => {
    const skillsLayoutRule = ruleBody(
        workspaceSkillsCss,
        '#workspaceContainer.workspace-skills-active #skillsContent,\n' +
            '#workspaceContainer.workspace-skills-active #skillsContentCreate,\n' +
            '#workspaceContainer.workspace-skills-active #skillsContentView,\n' +
            '#workspaceContainer.workspace-skills-active #skillsContentEdit',
    );
    const viewActionsRule = ruleBody(workspaceSkillsCss, '.skill-view-actions');
    const viewContentRule = ruleBody(workspaceSkillsCss, '#skillsContentView');

    assert.match(skillsLayoutRule, /min-height:\s*100%/);
    assert.doesNotMatch(skillsLayoutRule, /min-height:\s*0/);
    assert.match(viewActionsRule, /margin:\s*auto auto 0/);
    assert.match(viewActionsRule, /padding-top:\s*16px/);
    assert.match(viewActionsRule, /padding-bottom:\s*var\(--projects-page-end-gap,\s*20px\)/);
    assert.match(viewContentRule, /padding-bottom:\s*0/);
});

test('all project-style create and edit surfaces use the shared content shell', () => {
    const dynamicWorkspaceSurfaceIds = [
        'skillsContentCreate',
        'skillsContentEdit',
        'promptLibraryEditorContent',
    ];

    for (const id of dynamicWorkspaceSurfaceIds) {
        assert.match(workspaceCreateEditFormsSource, new RegExp(`id: '${id}'`), `${id} must be rendered dynamically`);
    }
    assert.match(
        fs.readFileSync(path.join(__dirname, '..', 'common', 'createEditFormRenderer.js'), 'utf8'),
        /contentClass = 'projects-content'/,
        'the renderer must default every other dynamic surface to the shared content shell',
    );

    assert.equal((createEditFormsSource.match(/contentClass: 'projects-content'/g) || []).length, 2);
    assert.doesNotMatch(createEditFormsSource, /automations-(?:content|header|create-form|create-buttons)/);
    assert.match(createEditFormsSource, /pageId: isEdit \? 'automationsContentEditAutomation' : 'automationsContentCreateAutomation'/);
    assert.match(createEditFormsSource, /pageId: isEdit \? 'projectsContentEditProject' : 'projectsContentCreateProject'/);
    assert.match(agentsSource, /formRenderer\.renderPage\(\{[\s\S]{0,100}?id: 'agentsEditorView'/);
    assert.match(mcpSource, /contentClass: 'projects-content mcp-editor-page'/);
});
