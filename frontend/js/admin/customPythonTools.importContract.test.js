const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');


const source = fs.readFileSync(path.join(__dirname, 'customPythonTools.js'), 'utf8');
const frontendRoot = path.resolve(__dirname, '../..');


function loadImportResolver() {
    const start = source.indexOf('    function resolveImportToolsFromPayload');
    const end = source.indexOf('    function resetImportState', start);
    assert.notEqual(start, -1, 'Missing custom-tool import resolver');
    assert.notEqual(end, -1, 'Missing custom-tool import resolver boundary');

    const context = {
        t: (_key, fallback) => fallback,
        formatT: (_key, fallback, values) => fallback.replace('{version}', String(values.version)),
    };
    vm.runInNewContext(
        `${source.slice(start, end)}\nthis.resolveImportToolsFromPayload = resolveImportToolsFromPayload;`,
        context,
        { filename: 'customPythonTools.importContract.js' }
    );
    return context.resolveImportToolsFromPayload;
}


test('a fresh backend export matches the server-provided import contract', () => {
    const resolveImportToolsFromPayload = loadImportResolver();
    const tools = [{ name: 'portable_tool' }];
    const contract = {
        export_type: 'custom_python_tool',
        export_version: 1.0,
    };
    const exportPayload = {
        ...contract,
        data: { tools },
    };

    const resolved = resolveImportToolsFromPayload(exportPayload, contract);

    assert.deepEqual(resolved, tools);
    assert.doesNotMatch(source, /currentCustomToolExportVersion/);
    assert.match(source, /fetchJson\(`\$\{API_BASE\}\/import-contract`\)/);
});


test('the import resolver rejects versions unsupported by the current backend', () => {
    const resolveImportToolsFromPayload = loadImportResolver();
    const contract = {
        export_type: 'custom_python_tool',
        export_version: 1.0,
    };
    const payload = {
        export_type: 'custom_python_tool',
        export_version: 2.0,
        data: { tools: [{ name: 'future_tool' }] },
    };

    assert.throws(
        () => resolveImportToolsFromPayload(payload, contract),
        /Unsupported export version\. Expected 1\./
    );
});


test('metadata failure defers version validation to the import endpoint', () => {
    const resolveImportToolsFromPayload = loadImportResolver();
    const payload = {
        export_type: 'custom_python_tool',
        export_version: 1.0,
        data: { tools: [{ name: 'portable_tool' }] },
    };

    assert.equal(resolveImportToolsFromPayload(payload, null).length, 1);
});


test('every locale can render the server-provided expected version', () => {
    const localeRoot = path.join(frontendRoot, 'i18n');
    for (const locale of fs.readdirSync(localeRoot)) {
        const adminPath = path.join(localeRoot, locale, 'admin.json');
        if (!fs.existsSync(adminPath)) continue;
        const translations = JSON.parse(fs.readFileSync(adminPath, 'utf8'));
        assert.match(
            translations.custom_tools_import_version_mismatch,
            /\{version\}/,
            `${locale} custom-tool version mismatch message must contain {version}`
        );
    }
});
