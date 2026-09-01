const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { app, BrowserWindow } = require('electron');

const ROOT = path.resolve(__dirname, '..', '..', '..');
const LOCALES = ['en', 'de', 'es', 'zh', 'fr', 'hi', 'ar', 'ja', 'it', 'pt', 'ru'];
const BYOK_SOURCE = fs.readFileSync(path.join(ROOT, 'frontend/js/chat/byok.js'), 'utf8');
const DELETE_MODAL_SOURCE = fs.readFileSync(
    path.join(ROOT, 'frontend/js/common/deleteWarningModal.js'),
    'utf8',
);
const USED_KEYS = new Set(
    [...BYOK_SOURCE.matchAll(/(?:byokT|translationRef|formatTranslation)\(\s*['"]([^'"]+)['"]/g)]
        .map((match) => match[1]),
);
USED_KEYS.add('schema_backend_api_credentials_and_endpoints');
USED_KEYS.add('schema_backend_organization_id');

function loadCatalog(locale) {
    const index = JSON.parse(fs.readFileSync(
        path.join(ROOT, 'frontend/i18n', locale, 'index.json'),
        'utf8',
    ));
    const schema = JSON.parse(fs.readFileSync(
        path.join(ROOT, 'frontend/i18n', locale, 'schema.json'),
        'utf8',
    ));
    const merged = { ...index, ...schema };
    return Object.fromEntries([...USED_KEYS].map((key) => [key, merged[key]]));
}

const CATALOGS = Object.fromEntries(LOCALES.map((locale) => [locale, loadCatalog(locale)]));

function scriptTag(source) {
    return `<script>${source.replace(/<\/script/gi, '<\\/script')}</script>`;
}

function fixtureHtml() {
    const supportSource = `
        let Icons = {
            assistant: '', close: '', edit: '', info: '', plus: '', trash: '', warning: '',
        };
        const catalogs = ${JSON.stringify(CATALOGS)};
        let translations = catalogs.de;
        window.getTranslation = (key, fallback) => translations[key] ?? fallback ?? key;
        window.formatTranslation = (key, fallback, vars = {}) => String(window.getTranslation(key, fallback))
            .replace(/\\{(\\w+)\\}/g, (_, token) => String(vars[token] ?? ''));
        function notifyError() {}
        function notifySuccess() {}
        window.authedFetch = async (url) => {
            if (String(url).includes('provider-schema')) {
                return {
                    ok: true,
                    json: async () => ({
                        sections: [{
                            title: 'API credentials & endpoints',
                            i18n_title: 'schema_backend_api_credentials_and_endpoints',
                            fields: [{
                                key: 'settings.organization',
                                label: 'Organization ID',
                                i18n_label: 'schema_backend_organization_id',
                            }],
                        }],
                    }),
                };
            }
            return { ok: true, json: async () => ({}) };
        };

        function applyText(element, text) {
            const hasChildSpan = element.children?.length === 1
                && element.children[0].tagName === 'SPAN';
            if (hasChildSpan) element.children[0].innerText = text;
            else element.textContent = text;
        }

        window.__setByokLocale = (locale) => {
            translations = catalogs[locale];
            document.documentElement.lang = locale;
            document.documentElement.dir = locale === 'ar' ? 'rtl' : 'ltr';
            document.querySelectorAll('[data-i18n]').forEach((element) => {
                const value = translations[element.getAttribute('data-i18n')];
                if (value != null) applyText(element, value);
            });
            document.querySelectorAll('[data-i18n-attr]').forEach((element) => {
                element.getAttribute('data-i18n-attr').split(';').forEach((pair) => {
                    const [attribute, key] = pair.split(':').map((part) => part.trim());
                    if (translations[key] != null) element.setAttribute(attribute, translations[key]);
                });
            });
            document.dispatchEvent(new CustomEvent('i18n:updated', { detail: { lang: locale } }));
            return true;
        };
    `;
    const readySource = `
        document.addEventListener('DOMContentLoaded', () => {
            window.BYOK.setPolicy({ allow_byok: true, byok_statistics_enabled: false });
            window.__byokLanguageFixtureReady = true;
        });
    `;
    return [
        '<!doctype html><html lang="de"><head><meta charset="utf-8"><title>BYOK language refresh test</title></head><body>',
        '<div id="byokNavItem"></div>',
        '<main id="byokSettingsPage" class="active"><div id="byokSettingsRoot"></div></main>',
        scriptTag(supportSource),
        scriptTag(DELETE_MODAL_SOURCE),
        scriptTag(BYOK_SOURCE),
        scriptTag(readySource),
        '</body></html>',
    ].join('');
}

async function waitFor(browserWindow, expression, message) {
    for (let attempt = 0; attempt < 200; attempt += 1) {
        if (await browserWindow.webContents.executeJavaScript(expression)) return;
        await new Promise((resolve) => setTimeout(resolve, 10));
    }
    throw new Error(message);
}

async function setLocale(browserWindow, locale) {
    await browserWindow.webContents.executeJavaScript(
        `window.__setByokLocale(${JSON.stringify(locale)})`,
    );
}

async function readState(browserWindow) {
    return browserWindow.webContents.executeJavaScript(`(() => ({
        rootProvider: document.querySelector('.byok-section-title')?.textContent.trim(),
        emptyProvider: document.querySelector('#byokProviderList .byok-empty-title')?.textContent.trim(),
        statsTitle: document.querySelector('#byokStatisticsToggleLabel')?.textContent.trim(),
        statsEmpty: document.querySelector('#byokStatisticsContent .byok-placeholder')?.textContent.trim(),
        providerTitle: document.querySelector('#byokProviderEditorTitle')?.textContent.trim(),
        providerType: document.querySelector('#byokProviderTypeLabel')?.textContent.trim(),
        providerSchemaTitle: document.querySelector('#byokProviderSettingsFields h2')?.textContent.trim(),
        providerSchemaField: document.querySelector('#byokProviderSettingsFields h3')?.textContent.trim(),
        providerName: document.querySelector('#byokProviderName')?.value,
        providerApiKey: document.querySelector('#byokProviderApiKey')?.value,
        providerOrganization: document.querySelector('[data-field-key="settings.organization"]')?.value,
        modelTitle: document.querySelector('#byokModelEditorTitle')?.textContent.trim(),
        modelProvider: document.querySelector('#byokModelProviderLabel')?.textContent.trim(),
        modelProviderOption: document.querySelector('#byokModelProviderInstance option')?.textContent.trim(),
        remoteModel: document.querySelector('#byokRemoteModelLabel')?.textContent.trim(),
        remoteModelOption: document.querySelector('#byokRemoteModelSelect option')?.textContent.trim(),
        modelSchemaEmpty: document.querySelector('#byokModelSettingsFields')?.textContent.trim(),
        modelCancel: document.querySelector('#byokModelCancelButton')?.textContent.trim(),
        modelSave: document.querySelector('#byokModelSaveButtonLabel')?.textContent.trim(),
        dialogTitle: document.querySelector('#byokDialogTitle')?.textContent.trim(),
        dialogDescription: document.querySelector('#byokDialogDescription')?.textContent.trim(),
        dialogCancel: document.querySelector('#byokDialogCancelButton')?.textContent.trim(),
        dialogConfirm: document.querySelector('#byokDialogConfirmButton')?.textContent.trim(),
        dialogOpen: !document.querySelector('#byokDialogOverlay')?.hidden,
        annotatedCopy: Array.from(document.querySelectorAll('[data-i18n]')).map((element) => ({
            key: element.getAttribute('data-i18n'),
            text: element.textContent.trim(),
        })),
    }))()`);
}

function assertRootCopy(state, catalog, locale) {
    assert.equal(state.rootProvider, catalog.byok_provider_instances_title, `${locale}: provider title`);
    assert.equal(state.emptyProvider, catalog.byok_provider_empty_title, `${locale}: provider empty state`);
    assert.equal(state.statsTitle, catalog.byok_stats_track_usage, `${locale}: statistics title`);
    assert.equal(state.statsEmpty, catalog.byok_stats_tracking_off, `${locale}: statistics empty state`);
    state.annotatedCopy.forEach(({ key, text }) => {
        assert.equal(text, catalog[key], `${locale}: annotated ${key}`);
    });
}

async function exerciseProviderEditor(browserWindow) {
    await browserWindow.webContents.executeJavaScript(
        `document.getElementById('byokCreateProviderButton').click()`,
    );
    await waitFor(
        browserWindow,
        `document.querySelector('[data-field-key="settings.organization"]') !== null`,
        'Provider schema did not render',
    );
    await browserWindow.webContents.executeJavaScript(`(() => {
        document.getElementById('byokProviderName').value = 'Draft provider';
        document.getElementById('byokProviderApiKey').value = 'dummy-not-a-secret';
        document.querySelector('[data-field-key="settings.organization"]').value = 'draft-org';
    })()`);

    for (const locale of LOCALES) {
        await setLocale(browserWindow, locale);
        const state = await readState(browserWindow);
        const catalog = CATALOGS[locale];
        assertRootCopy(state, catalog, locale);
        assert.equal(state.providerTitle, catalog.byok_provider_editor_title_add, `${locale}: provider dialog`);
        assert.equal(state.providerType, catalog.byok_provider_type_label, `${locale}: provider type`);
        assert.equal(
            state.providerSchemaTitle,
            catalog.schema_backend_api_credentials_and_endpoints,
            `${locale}: provider schema title`,
        );
        assert.equal(
            state.providerSchemaField,
            catalog.schema_backend_organization_id,
            `${locale}: provider schema field`,
        );
        assert.equal(state.providerName, 'Draft provider', `${locale}: provider name draft`);
        assert.equal(state.providerApiKey, 'dummy-not-a-secret', `${locale}: API key draft`);
        assert.equal(state.providerOrganization, 'draft-org', `${locale}: schema draft`);
    }
    await browserWindow.webContents.executeJavaScript(
        `document.getElementById('byokProviderCancelButton').click()`,
    );
}

async function exerciseModelEditor(browserWindow) {
    await browserWindow.webContents.executeJavaScript(
        `document.getElementById('byokCreateModelButton').click()`,
    );
    for (const locale of LOCALES) {
        await setLocale(browserWindow, locale);
        const state = await readState(browserWindow);
        const catalog = CATALOGS[locale];
        assertRootCopy(state, catalog, locale);
        assert.equal(state.modelTitle, catalog.byok_model_editor_title_add, `${locale}: model dialog`);
        assert.equal(state.modelProvider, catalog.byok_model_provider_instance_label, `${locale}: model provider`);
        assert.equal(state.modelProviderOption, catalog.byok_choose_provider_instance, `${locale}: provider option`);
        assert.equal(state.remoteModel, catalog.byok_remote_model_label, `${locale}: remote model`);
        assert.equal(state.remoteModelOption, catalog.byok_remote_select_provider_first, `${locale}: remote option`);
        assert.equal(
            state.modelSchemaEmpty,
            catalog.byok_remote_select_provider_for_schema,
            `${locale}: model schema empty state`,
        );
        assert.equal(state.modelCancel, catalog.byok_action_cancel, `${locale}: model cancel`);
        assert.equal(state.modelSave, catalog.byok_model_save, `${locale}: model save`);
    }
    await browserWindow.webContents.executeJavaScript(
        `document.getElementById('byokModelCancelButton').click()`,
    );
}

async function exerciseOpenDialog(browserWindow) {
    await browserWindow.webContents.executeJavaScript(`(() => {
        const toggle = document.getElementById('byokStatisticsToggle');
        toggle.checked = true;
        toggle.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);
    await waitFor(browserWindow, `!document.getElementById('byokDialogOverlay').hidden`, 'Dialog did not open');
    for (const locale of LOCALES) {
        await setLocale(browserWindow, locale);
        const state = await readState(browserWindow);
        const catalog = CATALOGS[locale];
        assert.equal(state.dialogOpen, true, `${locale}: dialog remains open`);
        assert.equal(state.dialogTitle, catalog.byok_stats_consent_title, `${locale}: dialog title`);
        assert.equal(
            state.dialogDescription,
            catalog.byok_stats_consent_desc.replace('{days}', '90'),
            `${locale}: dialog description`,
        );
        assert.equal(state.dialogCancel, catalog.common_cancel, `${locale}: dialog cancel`);
        assert.equal(state.dialogConfirm, catalog.byok_stats_consent_enable, `${locale}: dialog confirm`);
    }
    await browserWindow.webContents.executeJavaScript(
        `document.getElementById('byokDialogCancelButton').click()`,
    );
}

async function run() {
    const browserWindow = new BrowserWindow({ show: false, webPreferences: { sandbox: true } });
    await browserWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(fixtureHtml())}`);
    await waitFor(
        browserWindow,
        'window.__byokLanguageFixtureReady === true',
        'BYOK language fixture did not initialize',
    );

    await exerciseProviderEditor(browserWindow);
    await exerciseModelEditor(browserWindow);
    await exerciseOpenDialog(browserWindow);

    browserWindow.destroy();
    process.stdout.write(`${JSON.stringify({ locales: LOCALES.length, status: 'passed' })}\n`);
}

app.whenReady()
    .then(run)
    .then(() => app.quit())
    .catch((error) => {
        console.error(error);
        app.exit(1);
    });
