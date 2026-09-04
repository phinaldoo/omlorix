const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'auditLogs.js'), 'utf8');
const adminHtml = fs.readFileSync(path.join(__dirname, '../../admin.html'), 'utf8');
const auditLogsCss = fs.readFileSync(
    path.join(__dirname, '../../css/admin/auditLogs.css'),
    'utf8',
);

test('audit log page exposes filters, pagination, details, and an accessible export dialog', () => {
    assert.match(adminHtml, /id="page-audit-logs"/);
    assert.match(adminHtml, /id="auditLogsFilterForm" role="search"/);
    assert.match(adminHtml, /id="auditLogsRows"/);
    assert.match(adminHtml, /id="auditLogsLoadMoreButton"/);
    assert.match(
        adminHtml,
        /role="dialog" aria-modal="true" aria-labelledby="auditLogsExportTitle" aria-describedby="auditLogsExportDescription"/,
    );
    assert.match(adminHtml, /id="auditLogsExportError" role="alert"/);
    assert.match(
        adminHtml,
        /id="auditLogsExportReason"[^>]*minlength="3"[^>]*required[^>]*aria-describedby="auditLogsExportError"/,
    );
    assert.match(source, /window\.setTimeout\(\(\) => \{[\s\S]*dom\.exportReason\.focus\(\)/);
    assert.match(source, /event\.key === 'Escape'[\s\S]*closeExportDialog\(\)/);
});

test('audit log browser uses fixed-snapshot cursor pagination and separate detail requests', () => {
    assert.match(source, /appliedFilters: null/);
    assert.match(source, /const snapshotFilters = \(\) => Object\.freeze\(\{ \.\.\.readFilters\(\) \}\)/);
    assert.match(source, /filters = append \? state\.appliedFilters : snapshotFilters\(\)/);
    assert.match(source, /buildListUrl\(filters, \{ append \}\)/);
    assert.match(source, /params\.set\('snapshot_at', state\.snapshotAt\)/);
    assert.match(source, /params\.set\('cursor', state\.nextCursor\)/);
    assert.match(source, /\/api\/v1\/admin\/audit-logs\/\$\{encodeURIComponent\(item\.id\)\}/);
    assert.match(source, /state\.detailCache\.set\(cacheKey, detail\)/);
});

test('audit log export requires a reason and downloads the bounded JSON response', () => {
    assert.match(source, /reason\.length < 3[\s\S]*\{ invalidReason: true \}/);
    assert.match(source, /dom\.exportReason\.addEventListener\('input', clearValidExportReasonError\)/);
    assert.match(source, /method: 'POST'/);
    assert.match(source, /fetcher\('\/api\/v1\/admin\/audit-logs\/export'/);
    assert.match(source, /response\.blob\(\)/);
    assert.match(source, /URL\.createObjectURL\(blob\)/);
});

test('audit log export focuses and announces an invalid reason, then clears the error once valid', () => {
    const createElement = () => ({
        attributes: {},
        classList: {
            classes: new Set(),
            add(name) { this.classes.add(name); },
            remove(name) { this.classes.delete(name); },
            contains(name) { return this.classes.has(name); },
        },
        hidden: false,
        textContent: '',
        value: '',
        focus() { this.focused = true; },
        getAttribute(name) { return this.attributes[name]; },
        removeAttribute(name) { delete this.attributes[name]; },
        setAttribute(name, value) { this.attributes[name] = String(value); },
    });
    const reason = createElement();
    const error = createElement();
    const instrumented = source.replace(
        '    window.initAuditLogsPage = () => {',
        '    window.__auditLogsTest = { clearValidExportReasonError, showExportError };\n\n    window.initAuditLogsPage = () => {',
    );
    const context = {
        console,
        document: {
            getElementById(id) {
                return id === 'auditLogsExportReason' ? reason : error;
            },
        },
        window: {
            getTranslation(_key, fallback) { return fallback; },
        },
    };
    context.globalThis = context;
    vm.runInNewContext(instrumented, context, { filename: 'auditLogs.js' });

    const { clearValidExportReasonError, showExportError } = context.window.__auditLogsTest;
    showExportError('Reason is required.', { invalidReason: true });

    assert.equal(error.hidden, false);
    assert.equal(error.textContent, 'Reason is required.');
    assert.equal(reason.getAttribute('aria-invalid'), 'true');
    assert.equal(reason.classList.contains('field-error'), true);
    assert.equal(reason.focused, true);

    reason.value = 'ab';
    clearValidExportReasonError();
    assert.equal(error.hidden, false);
    assert.equal(reason.getAttribute('aria-invalid'), 'true');

    reason.value = '  valid reason  ';
    clearValidExportReasonError();

    assert.equal(error.hidden, true);
    assert.equal(error.textContent, '');
    assert.equal(reason.getAttribute('aria-invalid'), undefined);
    assert.equal(reason.classList.contains('field-error'), false);
});

test('audit log UI avoids unsafe HTML and native browser dialogs', () => {
    assert.doesNotMatch(source, /\.innerHTML\s*=/);
    assert.doesNotMatch(source, /\b(?:alert|confirm|prompt)\s*\(/);
    assert.match(source, /\.textContent\s*=/);
    assert.match(source, /replaceChildren\(/);
});

test('audit log text clamping preserves native table-cell layout', () => {
    assert.match(source, /text\.className = className/);
    assert.doesNotMatch(source, /cell\.className = className/);
    assert.match(source, /appendTextCell\(row, item\.reason, 'audit-log-reason'\)/);
    assert.match(auditLogsCss, /\.audit-log-reason\s*\{[^}]*display:\s*-webkit-box/);
});

test('audit log row hover behavior is limited to precise pointers', () => {
    assert.match(
        auditLogsCss,
        /@media \(hover: hover\) and \(pointer: fine\) \{[\s\S]*\.audit-logs-table tbody > tr:not\(\.audit-log-details-row\):hover td/,
    );
});

test('audit log filter actions span the filter grid and align to its end', () => {
    assert.match(
        auditLogsCss,
        /\.audit-logs-filter-actions\s*\{[^}]*grid-column:\s*1\s*\/\s*-1[^}]*justify-content:\s*flex-end/,
    );
});

test('audit log translations exist in every supported locale', () => {
    const localeRoot = path.join(__dirname, '../../i18n');
    const english = JSON.parse(fs.readFileSync(path.join(localeRoot, 'en/admin.json'), 'utf8'));
    const requiredKeys = Object.keys(english).filter(
        key => key === 'nav_audit_logs' || key.startsWith('audit_logs_'),
    );

    assert.ok(requiredKeys.length > 50, 'expected the complete audit-log translation set');
    for (const locale of fs.readdirSync(localeRoot)) {
        const file = path.join(localeRoot, locale, 'admin.json');
        if (!fs.existsSync(file)) continue;
        const translations = JSON.parse(fs.readFileSync(file, 'utf8'));
        for (const key of requiredKeys) {
            assert.equal(typeof translations[key], 'string', `${locale} missing ${key}`);
            assert.ok(translations[key].length > 0, `${locale} has empty ${key}`);
        }
    }
});
