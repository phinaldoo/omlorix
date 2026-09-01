const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

test('service connections modal toggles use the shared switch wrapper', () => {
    const source = fs.readFileSync(path.join(__dirname, 'serviceConnections.js'), 'utf8');

    // The toggle slider must live inside the shared toggle-switch wrapper or the
    // absolute-positioned track will anchor against the full label instead.
    assert.match(
        source,
        /<span class="toggle-switch">\s*<input type="checkbox" id="serviceConnectionCodeToggle" class="toggle-input">\s*<span class="toggle-slider" aria-hidden="true"><\/span>\s*<\/span>\s*<span class="service-connection-toggle-label" data-i18n="service_connections_code_execution">/
    );
    assert.match(
        source,
        /<span class="toggle-switch">\s*<input type="checkbox" id="serviceConnectionLatexToggle" class="toggle-input">\s*<span class="toggle-slider" aria-hidden="true"><\/span>\s*<\/span>\s*<span class="service-connection-toggle-label" data-i18n="service_connections_latex_pdf">/
    );
    assert.match(
        source,
        /<span class="toggle-switch">\s*<input type="checkbox" id="serviceConnectionSlideToggle" class="toggle-input">\s*<span class="toggle-slider" aria-hidden="true"><\/span>\s*<\/span>\s*<span class="service-connection-toggle-label" data-i18n="service_connections_slide_renderer">/
    );
});

test('service connections modal styles include dedicated toggle alignment and focus states', () => {
    const source = fs.readFileSync(path.join(__dirname, '..', '..', 'css', 'admin', 'serviceConnections.css'), 'utf8');

    assert.match(source, /\.service-connection-toggle \.toggle-switch\s*\{/);
    assert.match(source, /\.service-connection-toggle \.toggle-input:focus-visible \+ \.toggle-slider\s*\{/);
    assert.match(source, /\.service-connection-toggle:focus-within\s*\{/);
});

test('service connections modal uses its own translated save label', () => {
    const source = fs.readFileSync(path.join(__dirname, 'serviceConnections.js'), 'utf8');
    const i18nRoot = path.join(__dirname, '..', '..', 'i18n');
    const locales = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    assert.match(source, /data-i18n="service_connection_save_btn"/);
    assert.doesNotMatch(source, /data-i18n="groups_save_btn"/);
    for (const locale of locales) {
        const translations = JSON.parse(fs.readFileSync(path.join(i18nRoot, locale, 'admin.json'), 'utf8'));
        assert.equal(typeof translations.service_connection_save_btn, 'string', `${locale} is missing service_connection_save_btn`);
        assert.ok(translations.service_connection_save_btn.trim(), `${locale} has an empty service_connection_save_btn`);
    }
    const german = JSON.parse(fs.readFileSync(path.join(i18nRoot, 'de', 'admin.json'), 'utf8'));
    assert.equal(german.service_connection_save_btn, 'Verbindung speichern');
});

test('saving a service connection updates local state and refreshes every status automatically', () => {
    const source = fs.readFileSync(path.join(__dirname, 'serviceConnections.js'), 'utf8');

    assert.match(source, /state\.connections = state\.connections\.map\(\(item\) => \(/);
    assert.match(source, /state\.connections = \[\.\.\.state\.connections, savedConnection\];/);
    assert.match(source, /await refreshAllStatuses\(\);/);
});

test('service connection table refreshes restore focus to live controls', () => {
    const source = fs.readFileSync(path.join(__dirname, 'serviceConnections.js'), 'utf8');

    assert.match(
        source,
        /closeForm\(\{ restoreFocus: false \}\);[\s\S]*renderTable\(\);[\s\S]*restoreLastFocusedElement\('formLastFocusedElement'\);[\s\S]*await refreshAllStatuses\(\);/,
    );
    assert.match(
        source,
        /closeDelete\(\{ restoreFocus: false \}\);[\s\S]*await loadConnections\(\);[\s\S]*restoreLastFocusedElement\('deleteLastFocusedElement'\);/,
    );
    assert.match(source, /root\.querySelector\('#serviceConnectionsCreateButton'\)/);
    assert.match(source, /findConnectionAction\(id, button\.dataset\.action\)/);
    assert.match(source, /shouldRestoreFocus && document\.activeElement !== button/);
});

test('service connections page refreshes translated shell labels after i18n loads', () => {
    const source = fs.readFileSync(path.join(__dirname, 'serviceConnections.js'), 'utf8');

    assert.match(source, /document\.addEventListener\('i18n:updated', handleI18nUpdated\)/);
    assert.match(source, /applyStaticTranslations\(\);\s*renderTable\(\);/);
    assert.match(source, /data-i18n="service_connections_refresh_all"/);
    assert.match(source, /data-i18n-attr="placeholder:service_connections_search_placeholder;aria-label:service_connections_search_placeholder"/);
});

test('service connections status rows show api key validity alongside service connectivity', () => {
    const source = fs.readFileSync(path.join(__dirname, 'serviceConnections.js'), 'utf8');

    assert.match(source, /const apiKeyStatusLabel = \(status\) => \{/);
    assert.match(source, /service_connection_api_key_valid/);
    assert.match(source, /service_connection_api_key_invalid/);
    assert.match(source, /const distinctAuthStatuses = new Set/);
    assert.match(source, /distinctAuthStatuses\.size === 1/);
    assert.match(source, /distinctAuthStatuses\.size > 1/);
    assert.match(source, /purpose\.label.*service_connection_api_key_label.*apiKeyStatusLabel\(purpose\.auth\)/s);
    assert.match(source, /service_connection_api_key_label.*apiKeyStatusLabel\(authStatus\)/s);
});

test('service connection status details remain fully visible instead of being ellipsized', () => {
    const scriptSource = fs.readFileSync(path.join(__dirname, 'serviceConnections.js'), 'utf8');
    const styleSource = fs.readFileSync(path.join(__dirname, '..', '..', 'css', 'admin', 'serviceConnections.css'), 'utf8');

    assert.match(scriptSource, /service-connection-subtle service-connection-status-details/);
    assert.match(styleSource, /\.service-connections-table td:nth-child\(5\)\s*\{[^}]*white-space:\s*normal;/s);
    assert.match(styleSource, /\.service-connection-status-details\s*\{[^}]*text-overflow:\s*clip;/s);
    assert.match(styleSource, /\.service-connection-status-details\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
});

test('service connections use translated, contained mobile cards', () => {
    const scriptSource = fs.readFileSync(path.join(__dirname, 'serviceConnections.js'), 'utf8');
    const styleSource = fs.readFileSync(path.join(__dirname, '..', '..', 'css', 'admin', 'serviceConnections.css'), 'utf8');

    // Every cell needs a stable translated label because the table header is
    // hidden when rows switch to the compact card presentation.
    for (const field of ['name', 'baseUrl', 'enabled', 'weight', 'status', 'actions']) {
        assert.match(scriptSource, new RegExp(`data-label="\\$\\{labels\\.${field}\\}"`));
    }

    assert.match(styleSource, /\.service-connections-table,\s*\.service-connections-table tbody\s*\{[^}]*min-width:\s*0;/s);
    assert.match(styleSource, /grid-template-areas:\s*"name url"\s*"purposes purposes"\s*"weight status"\s*"actions actions"/s);
    assert.match(styleSource, /\.service-connections-table td\.service-connection-cell\s*\{[^}]*width:\s*auto;/s);
    assert.match(styleSource, /\.service-connections-table \.service-connection-url,[^{]*\{[^}]*overflow-wrap:\s*anywhere;/s);
    assert.match(scriptSource, /class="om-button border submit service-connections-primary-action" id="serviceConnectionsCreateButton"/);
    assert.match(styleSource, /\.service-connections-toolbar \.service-connections-primary-action\s*\{[^}]*grid-column:\s*1 \/ -1;/s);
    assert.match(styleSource, /@media \(max-width:\s*520px\)[\s\S]*grid-template-areas:\s*"name"\s*"url"\s*"purposes"\s*"weight"\s*"status"\s*"actions"/s);
    assert.match(scriptSource, /<caption class="sr-only" data-i18n="page_service_connections">/);
});
