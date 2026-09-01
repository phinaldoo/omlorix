const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

const chatDirectory = __dirname;
const skillsSource = readFrontendSource(path.join(chatDirectory, 'skills.js'), 'utf8');
const filesSource = readFrontendSource(path.join(chatDirectory, 'files.js'), 'utf8');
const chatBoxSource = readFrontendSource(path.join(chatDirectory, 'chatBox.js'), 'utf8');
const modalSource = readFrontendSource(path.join(chatDirectory, 'deleteWarningModals.js'), 'utf8');

test('user skill import accepts and submits every selected Markdown file', () => {
    assert.match(modalSource, /id="skillImportFileInput"[^>]*\bmultiple\b/);
    assert.match(skillsSource, /Array\.from\(e\.dataTransfer\?\.files \|\| \[\]\)/);
    assert.match(skillsSource, /Array\.from\(e\.target\.files \|\| \[\]\)/);
    assert.match(skillsSource, /formData\.append\('files', entry\.file, entry\.file\.name\)/);
    assert.match(skillsSource, /\/api\/v1\/skills\/import-markdown-files/);
});

test('skill import dropzone owns its drag sequence', () => {
    assert.match(
        filesSource,
        /isSkillImportModalOpen\(\)[\s\S]*?document\.getElementById\('skillImportOverlay'\)/,
    );
    for (const handler of ['handleDragEnter', 'handleDragOver', 'handleDragLeave', 'handleDrop']) {
        const start = filesSource.indexOf(`${handler}(event)`);
        assert.notEqual(start, -1, `expected ${handler}`);
        const body = filesSource.slice(start, start + 450);
        assert.match(body, /this\.isSkillImportModalOpen\(\)/);
        assert.match(body, /this\.reset\(\)/);
    }
    assert.match(
        skillsSource,
        /dz\.addEventListener\('drop', \(e\) => \{[\s\S]*?e\.stopPropagation\(\);[\s\S]*?this\._importHandleFiles\(files\)/,
    );
    assert.match(
        chatBoxSource,
        /function isSkillImportDropActive\(\)[\s\S]*?document\.getElementById\('skillImportOverlay'\)/,
    );
    for (const handler of ['handleChatDragEnter', 'handleChatDragOver', 'handleChatDragLeave', 'handleChatDrop']) {
        const start = chatBoxSource.indexOf(`function ${handler}(event)`);
        assert.notEqual(start, -1, `expected ${handler}`);
        const body = chatBoxSource.slice(start, start + 400);
        assert.match(body, /isSkillImportDropActive\(\)/);
        assert.match(body, /resetChatDropState\(\)/);
    }
});

test('workspace skill import supports locale-specific plural categories', () => {
    assert.match(skillsSource, /new Intl\.PluralRules\(locale\)\.select/);
    assert.match(skillsSource, /pluralKey\.replace\(\/_other\$\/, `_\$\{pluralCategory\}`\)/);

    const russian = JSON.parse(
        readFrontendSource(path.join(__dirname, '..', '..', 'i18n', 'ru', 'index.json'), 'utf8'),
    );
    for (const key of [
        'workspace_skills_import_files_failed_few',
        'workspace_skills_import_files_failed_many',
        'workspace_skills_import_files_success_few',
        'workspace_skills_import_files_success_many',
    ]) {
        assert.ok(russian[key], `expected Russian plural translation ${key}`);
        assert.match(russian[key], /\{count\}/);
    }
});
