const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const skillsSource = fs.readFileSync(path.join(__dirname, 'skills.js'), 'utf8');
const scriptSource = fs.readFileSync(path.join(__dirname, 'script.js'), 'utf8');

test('skill sharing accepts only the current typed browser routes', () => {
    assert.match(skillsSource, /\/skills\/(clone|live|collaborate)\//);
    assert.doesNotMatch(skillsSource, /path\.includes\('\/skills\/shared\/'\)/);
    assert.doesNotMatch(scriptSource, /pathname\.startsWith\('\/skills\/shared\/'\)/);
});

test('only collaborate subscriptions receive skill edit controls', () => {
    assert.match(
        skillsSource,
        /function canEditSkill\(skill\)[\s\S]*?skill\.is_subscribed !== true[\s\S]*?skill\.share_type === 'collaborate'/,
    );

    const subscribedBranchStart = skillsSource.indexOf('} else if (isSubscribed)');
    const ownedBranchStart = skillsSource.indexOf('} else {', subscribedBranchStart);
    assert.notEqual(subscribedBranchStart, -1);
    assert.ok(ownedBranchStart > subscribedBranchStart);
    const subscribedBranch = skillsSource.slice(subscribedBranchStart, ownedBranchStart);
    assert.match(subscribedBranch, /canEditSkill\(skill\)/);
    assert.match(subscribedBranch, /data-action="edit"/);
    assert.match(subscribedBranch, /data-action="unsubscribe"/);
    assert.doesNotMatch(subscribedBranch, /data-action="delete"/);
    assert.doesNotMatch(subscribedBranch, /data-action="share"/);
});

test('direct skill mutation entry points enforce effective edit access', () => {
    for (const methodSignature of [
        'showEditScreen(skillId)',
        'async handleUpdate()',
        'async handleFileDelete(skillId, folderType, filename)',
    ]) {
        const start = skillsSource.indexOf(`\n    ${methodSignature} {`);
        assert.notEqual(start, -1, `expected ${methodSignature}`);
        const methodPrefix = skillsSource.slice(start, start + 850);
        assert.match(methodPrefix, /canEditSkill\(skill\)/, `expected edit-access guard in ${methodSignature}`);
    }

    assert.match(
        skillsSource,
        /const skill = SkillsState\.activeSkillContext;\s*if \(!skill \|\| skill\.is_admin_skill === true \|\| !canEditSkill\(skill\)\) return;/,
    );
});

test('Collaborate sharing describes real synchronized editing', () => {
    const descriptionStart = skillsSource.indexOf('function getSkillShareTypeDescription(shareType)');
    const descriptionEnd = skillsSource.indexOf('function getSkillFolderLabel', descriptionStart);
    const descriptionBody = skillsSource.slice(descriptionStart, descriptionEnd);
    assert.match(
        descriptionBody,
        /case 'collaborate':[\s\S]*?workspace_skills_share_type_desc_collaborate_edit/,
    );
    assert.doesNotMatch(descriptionBody, /case 'collaborate-edit'/);
});
