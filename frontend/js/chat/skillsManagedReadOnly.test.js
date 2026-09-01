const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const chatDirectory = __dirname;
const frontendDirectory = path.join(chatDirectory, '..', '..');
const skillsSource = fs.readFileSync(path.join(chatDirectory, 'skills.js'), 'utf8');
const workspaceSkillsCss = fs.readFileSync(path.join(frontendDirectory, 'css', 'chat', 'workspace-skills.css'), 'utf8');
const indexHtml = fs.readFileSync(path.join(frontendDirectory, 'index.html'), 'utf8');

const managedViewTranslationKeys = [
    'workspace_skills_view_author_label',
    'workspace_skills_view_back',
    'workspace_skills_view_compatibility_label',
    'workspace_skills_view_details_title',
    'workspace_skills_view_instructions_title',
    'workspace_skills_view_license_label',
    'workspace_skills_view_managed_notice_text',
    'workspace_skills_view_managed_notice_title',
    'workspace_skills_view_metadata_label',
    'workspace_skills_view_resources_title',
    'workspace_skills_view_title',
];

test('managed skill cards expose an explicit read-only Open action', () => {
    const adminBranchStart = skillsSource.indexOf('if (isAdminSkill)');
    const subscribedBranchStart = skillsSource.indexOf('} else if (isSubscribed)', adminBranchStart);
    assert.notEqual(adminBranchStart, -1);
    assert.notEqual(subscribedBranchStart, -1);

    const adminBranch = skillsSource.slice(adminBranchStart, subscribedBranchStart);
    assert.match(adminBranch, /data-action="view"/);
    assert.match(adminBranch, /workspace_skills_action_open/);
    assert.doesNotMatch(adminBranch, /data-action="edit"/);
    assert.doesNotMatch(adminBranch, /data-action="delete"/);
    assert.doesNotMatch(adminBranch, /data-action="share"/);
});

test('managed skill details use a dedicated semantic view without edit controls', () => {
    const viewStart = indexHtml.indexOf('id="skillsContentView"');
    const editStart = indexHtml.indexOf('id="workspaceSectionAgents"');
    assert.notEqual(viewStart, -1);
    assert.ok(editStart > viewStart);

    const managedView = indexHtml.slice(viewStart, editStart);
    assert.match(managedView, /<article class="skill-view-card" aria-labelledby="skillViewName">/);
    assert.match(managedView, /class="skill-view-managed-notice" role="note"/);
    assert.match(managedView, /id="skillViewContent"/);
    assert.match(managedView, /id="skillViewResourcesSection"/);
    assert.match(managedView, /id="skillViewBackBtn"/);
    assert.doesNotMatch(managedView, /<input\b/);
    assert.doesNotMatch(managedView, /<textarea\b/);
    assert.doesNotMatch(managedView, /saveSkillChangesBtn/);
});

test('managed skill rendering exposes full authorized data but no file mutations', () => {
    const renderStart = skillsSource.indexOf('\n    renderManagedSkillView(skill) {');
    const deleteStart = skillsSource.indexOf('\n    showDeleteScreen(skillId) {', renderStart);
    assert.notEqual(renderStart, -1);
    assert.notEqual(deleteStart, -1);

    const renderBody = skillsSource.slice(renderStart, deleteStart);
    assert.match(renderBody, /renderMarkdownToHtml\(content\)/);
    assert.match(renderBody, /skill\.author/);
    assert.match(renderBody, /skill\.compatibility/);
    assert.match(renderBody, /skill\.license/);
    assert.match(renderBody, /JSON\.stringify\(skill\.metadata, null, 2\)/);
    assert.match(renderBody, /\{ readOnly: true \}/);

    const fileRendererStart = skillsSource.indexOf('fileItem(file, folderType, skillId, { readOnly = false } = {})');
    const cardRendererStart = skillsSource.indexOf('skillCard(skill)', fileRendererStart);
    const fileRenderer = skillsSource.slice(fileRendererStart, cardRendererStart);
    assert.match(fileRenderer, /readOnly \? '' : `<button/);
});

test('managed skills are guarded from every personal mutation entry point', () => {
    for (const methodSignature of [
        'showEditScreen(skillId)',
        'showDeleteScreen(skillId)',
        'async handleUpdate()',
        'async handleDelete()',
        'async handleFileDelete(skillId, folderType, filename)',
        'async handleUnsubscribe(skillId)',
        'async showShareModal(skillId)',
    ]) {
        const start = skillsSource.indexOf(`\n    ${methodSignature} {`);
        assert.notEqual(start, -1, `expected ${methodSignature}`);
        const methodPrefix = skillsSource.slice(start, start + 700);
        assert.match(methodPrefix, /is_admin_skill/, `expected managed-skill guard in ${methodSignature}`);
    }

    assert.match(
        skillsSource,
        /const skill = SkillsState\.activeSkillContext;\s*if \(!skill \|\| skill\.is_admin_skill === true \|\| !canEditSkill\(skill\)\) return;/,
    );
});

test('managed skill detail view is responsive and restores focus to its Open action', () => {
    assert.match(workspaceSkillsCss, /#skillsContentView/);
    assert.match(workspaceSkillsCss, /\.skill-view-details-grid\s*\{[\s\S]*?grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/);
    assert.match(workspaceSkillsCss, /@media \(max-width: 640px\)[\s\S]*?\.skill-view-details-grid\s*\{\s*grid-template-columns: 1fr;/);
    assert.match(skillsSource, /detailReturnSkillId/);
    assert.match(skillsSource, /querySelectorAll\('\.skill-action-btn\[data-action="view"\]'/);
    assert.match(skillsSource, /SkillsDOM\.skillViewName\?\.focus\(\)/);
});

test('managed skill detail copy is translated in every supported locale', () => {
    const i18nDirectory = path.join(frontendDirectory, 'i18n');
    const locales = fs.readdirSync(i18nDirectory)
        .filter(locale => fs.existsSync(path.join(i18nDirectory, locale, 'index.json')));

    assert.ok(locales.length > 1);
    for (const locale of locales) {
        const translations = JSON.parse(
            fs.readFileSync(path.join(i18nDirectory, locale, 'index.json'), 'utf8'),
        );
        for (const key of managedViewTranslationKeys) {
            assert.equal(typeof translations[key], 'string', `${locale} is missing ${key}`);
            assert.ok(translations[key].trim(), `${locale} has an empty ${key}`);
        }
    }
});
