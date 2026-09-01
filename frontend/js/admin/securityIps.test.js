const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'securityIps.js'), 'utf8');
const adminHtml = fs.readFileSync(path.join(__dirname, '../../admin.html'), 'utf8');
const analyticsCss = fs.readFileSync(
    path.join(__dirname, '../../css/admin/securityIps.css'),
    'utf8',
);

test('security IP ban modal collects duration and reason', () => {
    assert.match(source, /id="securityIpsDurationInput"/);
    assert.match(source, /id="securityIpsReasonInput"/);
    assert.match(source, /security_ips_duration_days_label/);
    assert.match(source, /security_ips_reason_label/);
    assert.match(source, /security_ips_validation_duration_invalid/);
    assert.match(source, /security_ips_validation_reason_required/);
});

test('security IP ban requests include duration and reason for new bans', () => {
    assert.match(source, /payload\.duration_days = options\.durationDays;/);
    assert.match(source, /payload\.reason = options\.reason;/);
    assert.match(source, /durationDays,/);
    assert.match(source, /reason,/);
});

test('security IP ban list exposes edit action and edit modal labels', () => {
    assert.match(source, /securityIpAction: 'edit'/);
    assert.match(source, /Icons\?\.edit/);
    assert.match(source, /security_ips_edit_modal_title/);
    assert.match(source, /security_ips_save_edit_btn/);
    assert.match(source, /getRemainingDurationDays\(editingEntry\)/);
});

test('security IP ban edits use the saved entry update endpoint', () => {
    assert.match(source, /const updateIpBlock = async \(originalIpAddress, ipAddress, fallback, options = \{\}\) => \{/);
    assert.match(source, /method: 'PUT'/);
    assert.match(source, /\/api\/v1\/admin\/ip-address\/blocked\/\$\{encodeURIComponent\(originalIpAddress\)\}/);
    assert.match(source, /await updateIpBlock\(/);
});

test('successful IP ban mutations restore focus only after the list refresh', () => {
    assert.match(
        source,
        /hide\(\{ restoreFocus: false \}\);[\s\S]*await loadBlockedIps\(\);[\s\S]*finally \{[\s\S]*addButton\?\.focus\?\.\(\);/,
    );
});

test('IP analytics exposes filters pagination privacy controls and lifecycle datasets', () => {
    assert.match(adminHtml, /id="securityIpsStatsIpFilter"/);
    assert.match(adminHtml, /id="securityIpsStatsEventsPagination"/);
    assert.match(adminHtml, /id="securityIpsStatsExportBtn"/);
    assert.match(adminHtml, /id="securityIpsStatsImportBtn"/);
    assert.match(adminHtml, /id="securityIpsStatsDeleteBtn"/);
    assert.match(source, /\/ip-address\/statistics\/events/);
    assert.match(source, /\/ip-address\/statistics\/filters/);
    assert.match(source, /manual_bans_created/);
    assert.match(source, /automatic_bans_created/);
    assert.match(source, /rate_limited_requests/);
    assert.doesNotMatch(source, /blocked_attempts \|\| 0/);
});

test('IP analytics toolbar contains its filters and uses synchronized admin custom selects', () => {
    assert.match(
        adminHtml,
        /class="security-ips-toolbar"[\s\S]*id="securityIpsStatsIpFilter"[\s\S]*id="securityIpsStatsRange"[\s\S]*id="securityIpsStatsRetentionWarning"[\s\S]*<\/div>\s*<\/div>\s*<!--[\s\S]*KPI cards/,
    );
    assert.match(source, /window\.upgradeAdminSingleSelect\(select, field\)/);
    assert.match(source, /emptyValueIsOption: true/);
    assert.match(source, /syncAnalyticsCustomSelect\(select, \{ refreshOptions: true \}\)/);
    assert.match(source, /\[el\.countryFilter, el\.eventFilter, el\.sourceFilter\]\.forEach\(\(select\)/);
    assert.match(analyticsCss, /\.security-ips-toolbar-header\s*\{[^}]*display:\s*flex;/s);
    assert.match(analyticsCss, /\.security-ips-filter-grid\s*\{[^}]*border-top:/s);
});

test('IP analytics event badges style every canonical backend event type', () => {
    const eventTypes = ['ban_created', 'request_denied', 'ban_removed', 'rate_limited'];
    for (const eventType of eventTypes) {
        assert.match(
            analyticsCss,
            new RegExp(`\\.security-ips-event-type\\.${eventType}\\s*\\{[^}]*background:[^}]*color:[^}]*border:`, 's'),
            `missing complete badge styling for ${eventType}`,
        );
    }
    assert.doesNotMatch(analyticsCss, /\.security-ips-event-type\.(?:blocked|blocked_attempt)\b/);
});

test('IP analytics legend maps all four labels to the chart design tokens', () => {
    const expectedLegendTokens = {
        denied: '--admin-warning',
        'rate-limited': '--admin-error',
        'manual-bans': '--admin-accent',
        'automatic-bans': '--admin-success',
    };
    for (const [legendName, token] of Object.entries(expectedLegendTokens)) {
        assert.match(
            adminHtml,
            new RegExp(`stats-legend-item security-ips-legend-${legendName}`),
            `missing legend item for ${legendName}`,
        );
        assert.match(
            analyticsCss,
            new RegExp(`\\.security-ips-legend-${legendName}::before\\s*\\{[^}]*var\\(${token}\\)`, 's'),
            `legend ${legendName} does not use ${token}`,
        );
        assert.match(
            source,
            new RegExp(`backgroundColor:[^\\n]*getPropertyValue\\('${token}'\\)`),
            `chart dataset does not use ${token}`,
        );
    }
});

test('country activity table switches to labelled cards at its wider breakpoint', () => {
    assert.match(
        analyticsCss,
        /@media \(max-width: 1400px\)[\s\S]*\.security-ips-stats-table thead\s*\{[^}]*display:\s*none;/,
    );
    assert.match(
        analyticsCss,
        /\.security-ips-stats-table tbody tr\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/s,
    );
    assert.match(
        analyticsCss,
        /\.security-ips-stats-table td::before\s*\{[^}]*content:\s*attr\(data-label\)/s,
    );
});

test('IP analytics provider statuses use hardcoded translation keys', () => {
    assert.match(source, /case 'configured':\s*return t\('security_ips_stats_provider_configured'/);
    assert.match(source, /case 'disabled':\s*return t\('security_ips_stats_provider_disabled'/);
    assert.match(source, /case 'missing':[\s\S]*return t\('security_ips_stats_provider_missing'/);
    assert.doesNotMatch(source, /security_ips_stats_provider_\$\{/);
});

test('IP analytics translations exist in every supported locale', () => {
    const localeRoot = path.join(__dirname, '../../i18n');
    const requiredKeys = [
        'security_ips_stats_provider_title',
        'security_ips_stats_filter_event',
        'security_ips_stats_dataset_rate_limited',
        'security_ips_stats_event_ban_removed',
        'security_ips_stats_exact_range',
        'security_ips_stats_delete_desc',
    ];
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
