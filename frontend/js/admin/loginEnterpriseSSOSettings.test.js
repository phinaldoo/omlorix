const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

const source = readFrontendSource(path.join(__dirname, 'login.js'), 'utf8');
const helperSource = readFrontendSource(path.join(__dirname, 'helper.js'), 'utf8');

const enterpriseSsoTranslationKeys = [
    'schema_login_social_github_base_url',
    'schema_login_social_github_base_url_desc',
    'schema_login_enterprise_sso_group_identity_providers',
    'schema_login_enterprise_sso_group_user_provisioning',
    'schema_login_enterprise_sso_scim_title',
    'schema_login_enterprise_sso_scim_desc',
    'schema_login_enterprise_sso_saml_attribute_mapping',
    'schema_login_enterprise_sso_saml_attribute_mapping_desc',
    'schema_login_enterprise_sso_oidc_scopes',
    'schema_login_enterprise_sso_oidc_scopes_desc',
    'schema_login_enterprise_sso_oidc_attribute_mapping',
    'schema_login_enterprise_sso_oidc_attribute_mapping_desc',
    'admin_json_editor_help',
    'admin_json_editor_invalid',
    'auth_diagnostics_title',
    'auth_diagnostics_description',
    'auth_diagnostics_refresh',
    'auth_diagnostics_test_oidc',
    'auth_diagnostics_time',
    'auth_diagnostics_reference',
    'auth_diagnostics_provider',
    'auth_diagnostics_stage',
    'auth_diagnostics_cause',
    'auth_diagnostics_empty',
    'auth_diagnostics_check_required',
    'auth_diagnostics_check_scopes',
    'auth_diagnostics_check_discovery',
    'auth_diagnostics_check_issuer',
    'auth_diagnostics_check_jwks',
    'auth_diagnostics_check_email_claims',
    'auth_diagnostics_issuer_values',
    'auth_diagnostics_missing_values',
    'auth_diagnostics_key_count',
    'auth_diagnostics_passed',
    'auth_diagnostics_warning',
    'auth_diagnostics_failed',
    'auth_diagnostics_load_error',
    'auth_diagnostics_test_error',
];

test('settings renderer groups consecutive Enterprise SSO subsections accessibly', () => {
    assert.match(helperSource, /group_title: section\?\.group_title/);
    assert.match(helperSource, /createElement\('h2'\)/);
    assert.match(helperSource, /settings-section-group-body/);
    assert.match(helperSource, /activeGroup\.body\.appendChild\(sectionEl\)/);
    assert.match(helperSource, /state\.groups\.forEach\(\(groupMeta\) =>/);
    assert.match(helperSource, /groupMeta\.element\.hidden = shouldHideGroup/);
});

test('shared settings controllers expose a guarded reload operation', () => {
    assert.match(helperSource, /reload\(\) \{\s*if \(!state\.active\) \{\s*return;\s*\}\s*load\(\);/);
});

test('Enterprise SSO diagnostics are server-tested and rendered without HTML injection', () => {
    assert.match(source, /\/api\/v1\/admin\/auth-diagnostics\/oidc\/test/);
    assert.match(source, /\/api\/v1\/admin\/auth-diagnostics\?page_size=20/);
    assert.match(source, /item\.textContent/);
    assert.match(source, /cell\.textContent/);
    assert.doesNotMatch(source, /auth-diagnostics[\s\S]*innerHTML/);
});

test('Enterprise SSO controls and diagnostics are translated in every locale', () => {
    const i18nRoot = path.resolve(__dirname, '../../i18n');
    for (const locale of fs.readdirSync(i18nRoot)) {
        const adminPath = path.join(i18nRoot, locale, 'admin.json');
        if (!fs.existsSync(adminPath)) {
            continue;
        }
        const translations = JSON.parse(readFrontendSource(adminPath, 'utf8'));
        for (const key of enterpriseSsoTranslationKeys) {
            assert.equal(
                typeof translations[key] === 'string' && translations[key].trim().length > 0,
                true,
                `${locale}/admin.json is missing ${key}`,
            );
        }
    }
});
