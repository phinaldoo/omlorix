const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'adminSkills.js'), 'utf8');
const authSource = fs.readFileSync(path.join(__dirname, '..', 'common', 'auth.js'), 'utf8');

test('admin skill importer hides its empty warning until files were inspected', () => {
    const renderStart = source.indexOf('function renderImportSkillsList()');
    assert.notEqual(renderStart, -1, 'expected the admin skill import list renderer');
    const renderEnd = source.indexOf('\nfunction handleImportSkillToggle', renderStart);
    assert.notEqual(renderEnd, -1, 'expected the end of the import list renderer');
    const renderer = source.slice(renderStart, renderEnd);

    assert.match(
        renderer,
        /const shouldShowEmptyState = AdminSkillsState\.importMode === 'files'\s*&& Boolean\(AdminSkillsState\.importFileLabel\)/,
    );
    assert.match(renderer, /host\.hidden = !shouldShowEmptyState/);
    assert.match(renderer, /if \(!shouldShowEmptyState\) return/);
    assert.match(renderer, /host\.hidden = false/);
});

test('admin skill file imports let authenticated fetch set the multipart boundary', () => {
    const importFilesStart = source.indexOf('async importFiles(files, archiveSelections)');
    assert.notEqual(importFilesStart, -1, 'expected AdminSkillsAPI.importFiles');
    const importFilesEnd = source.indexOf('\n    async ', importFilesStart + 10);
    const importFilesSource = source.slice(importFilesStart, importFilesEnd);

    assert.match(importFilesSource, /body: formData/);
    assert.doesNotMatch(importFilesSource, /Content-Type/);
    assert.match(authSource, /const isFormData = [\s\S]*?body instanceof FormData/);
    assert.match(authSource, /if \(isFormData\) \{\s*mergedHeaders\.delete\('Content-Type'\)/);
});

test('admin skill ZIP limit errors have dedicated translations in every locale', () => {
    const staleJsonKey = ['admin', 'skills', 'import', 'select', 'json'].join('_');
    assert.match(source, /admin_skills_import_zip_too_many_files/);
    assert.match(source, /admin_skills_import_zip_compression_ratio/);
    assert.doesNotMatch(source, new RegExp(staleJsonKey));

    const localeRoot = path.join(__dirname, '..', '..', 'i18n');
    for (const locale of ['ar', 'de', 'en', 'es', 'fr', 'hi', 'it', 'ja', 'pt', 'ru', 'zh']) {
        const translations = JSON.parse(
            fs.readFileSync(path.join(localeRoot, locale, 'admin.json'), 'utf8'),
        );
        assert.ok(translations.admin_skills_import_zip_too_many_files, `${locale} file-count error`);
        assert.ok(translations.admin_skills_import_zip_compression_ratio, `${locale} ratio error`);
        assert.equal(
            Object.hasOwn(translations, staleJsonKey),
            false,
            `${locale} stale JSON guidance`,
        );
    }
});

test('managed skill terminology is translated consistently in every locale', () => {
    const localeRoot = path.join(__dirname, '..', '..', 'i18n');
    const expectedLabels = {
        ar: 'Skill مُدارة',
        de: 'Verwalteter Skill',
        en: 'Managed Skill',
        es: 'Skill gestionada',
        fr: 'Skill gérée',
        hi: 'प्रबंधित Skill',
        it: 'Skill gestita',
        ja: '管理対象Skill',
        pt: 'Skill gerida',
        ru: 'Управляемый Skill',
        zh: '受管Skill',
    };

    for (const [locale, expectedLabel] of Object.entries(expectedLabels)) {
        const translations = JSON.parse(
            fs.readFileSync(path.join(localeRoot, locale, 'index.json'), 'utf8'),
        );
        assert.equal(translations.chat_attachment_admin_skill, expectedLabel);
        assert.equal(translations.workspace_skills_admin_badge, expectedLabel);
    }
});
