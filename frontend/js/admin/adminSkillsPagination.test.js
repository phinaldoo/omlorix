const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'adminSkills.js'), 'utf8');
const adminHtml = fs.readFileSync(path.join(__dirname, '../../admin.html'), 'utf8');
const pagesSource = fs.readFileSync(path.join(__dirname, 'pages.js'), 'utf8');
const paginationCss = fs.readFileSync(
    path.join(__dirname, '../../css/admin/adminUserNotifications.css'),
    'utf8',
);

function extractListHelpers() {
    const start = source.indexOf('function buildAdminSkillsListUrl');
    const end = source.indexOf('const AdminSkillsAPI =', start);
    assert.notEqual(start, -1, 'expected the managed-skill list URL helper');
    assert.notEqual(end, -1, 'expected the managed-skill API marker');

    const context = { URLSearchParams };
    vm.runInNewContext(
        `${source.slice(start, end)}
        this.helpers = { buildAdminSkillsListUrl, generateAdminSkillPageNumbers };`,
        context,
        { filename: 'adminSkills.js' },
    );
    return context.helpers;
}

test('managed-skill list URLs carry bounded paging and encoded backend search', () => {
    const { buildAdminSkillsListUrl } = extractListHelpers();

    assert.equal(
        buildAdminSkillsListUrl({ page: 2, pageSize: 10, search: ' 100% podcast ' }),
        '/api/v1/skills/admin?page=2&page_size=10&search=100%25+podcast',
    );
    assert.equal(
        buildAdminSkillsListUrl({ page: 1, pageSize: 10, search: '   ' }),
        '/api/v1/skills/admin?page=1&page_size=10',
    );
});

test('managed-skill page numbers stay compact around the current page', () => {
    const { generateAdminSkillPageNumbers } = extractListHelpers();

    assert.deepEqual(Array.from(generateAdminSkillPageNumbers(1, 3)), [1, 2, 3]);
    assert.deepEqual(
        Array.from(generateAdminSkillPageNumbers(6, 12)),
        [1, '…', 5, 6, 7, '…', 12],
    );
    assert.deepEqual(
        Array.from(generateAdminSkillPageNumbers(11, 12)),
        [1, '…', 8, 9, 10, 11, 12],
    );
});

test('paginated lists keep their height and use reduced-motion-aware scrolling', () => {
    assert.match(source, /if \(!AdminSkillsState\.skills\.length\) \{/);
    assert.match(source, /list\.classList\.add\('is-page-loading'\)/);
    assert.match(source, /list\.classList\.remove\('is-page-loading'\)/);
    assert.match(
        paginationCss,
        /\.admin-skills-list\.is-page-loading,[\s\S]*?\.db-entity-list\.is-page-loading/,
    );

    const helperStart = pagesSource.indexOf('window.scrollAdminPaginatedListToStart');
    const helperEnd = pagesSource.indexOf('const pages =', helperStart);
    assert.notEqual(helperStart, -1, 'expected the shared paginated-list scroll helper');
    assert.notEqual(helperEnd, -1, 'expected the page registry marker');

    const scrollOptions = [];
    let prefersReducedMotion = false;
    const context = {
        window: {
            matchMedia: () => ({ matches: prefersReducedMotion }),
        },
    };
    vm.runInNewContext(pagesSource.slice(helperStart, helperEnd), context);
    context.window.scrollAdminPaginatedListToStart({
        scrollIntoView(options) {
            scrollOptions.push(options);
        },
    });
    assert.deepEqual(JSON.parse(JSON.stringify(scrollOptions)), [{
        behavior: 'smooth',
        block: 'start',
        inline: 'nearest',
    }]);

    prefersReducedMotion = true;
    context.window.scrollAdminPaginatedListToStart({
        scrollIntoView(options) {
            scrollOptions.push(options);
        },
    });
    assert.equal(scrollOptions[1].behavior, 'auto');
});

test('managed-skill pagination is accessible and every locale includes its copy', () => {
    assert.match(adminHtml, /id="adminSkillSearchInput"[^>]+maxlength="200"/);
    assert.match(adminHtml, /id="adminSkillsList"[^>]+aria-live="polite"/);
    assert.match(adminHtml, /id="adminSkillsPagination"[^>]+pagination_navigation_aria/);
    assert.match(source, /new AbortController\(\)/);
    assert.match(source, /AdminSkillsAPI\.fetchSkill\(skillId/);

    const i18nRoot = path.join(__dirname, '../../i18n');
    for (const locale of fs.readdirSync(i18nRoot)) {
        const adminPath = path.join(i18nRoot, locale, 'admin.json');
        if (!fs.existsSync(adminPath)) continue;
        const translations = JSON.parse(fs.readFileSync(adminPath, 'utf8'));
        assert.ok(translations.admin_skills_detail_load_failed, `${locale} is missing detail-load copy`);
        assert.ok(translations.admin_skills_pagination_showing, `${locale} is missing pagination copy`);
        assert.ok(translations.admin_skills_page_aria, `${locale} is missing page aria copy`);
        for (const token of ['{start}', '{end}', '{total}']) {
            assert.ok(
                translations.admin_skills_pagination_showing.includes(token),
                `${locale} pagination copy is missing ${token}`,
            );
        }
        assert.ok(
            translations.admin_skills_page_aria.includes('{page}'),
            `${locale} page label is missing {page}`,
        );
    }
});

test('leaving the managed-skill flow cancels a pending detail navigation', () => {
    const helperStart = source.indexOf('function cancelAdminSkillDetailRequest');
    const helperEnd = source.indexOf('const t =', helperStart);
    assert.notEqual(helperStart, -1, 'expected the detail-request cancellation helper');
    assert.notEqual(helperEnd, -1, 'expected the translation helper marker');

    let abortCount = 0;
    const context = {
        AdminSkillsState: {
            detailRequestController: {
                abort() {
                    abortCount += 1;
                },
            },
        },
    };
    vm.runInNewContext(
        `${source.slice(helperStart, helperEnd)}
        cancelAdminSkillDetailRequest();
        this.controllerAfterCancel = AdminSkillsState.detailRequestController;`,
        context,
        { filename: 'adminSkills.js' },
    );

    assert.equal(abortCount, 1);
    assert.equal(context.controllerAfterCancel, null);
    assert.match(
        source,
        /if \(activatedPage !== 'skills-edit'\) \{\s*cancelAdminSkillDetailRequest\(\);/,
    );
});
