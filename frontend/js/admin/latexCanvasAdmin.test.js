const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

test('LaTeX rendering is infrastructure rather than a standalone admin tool', () => {
    const helperSource = readFrontendSource(path.join(__dirname, 'helper.js'), 'utf8');
    const pagesSource = readFrontendSource(path.join(__dirname, 'pages.js'), 'utf8');
    const serviceConnectionsSource = readFrontendSource(path.join(__dirname, 'serviceConnections.js'), 'utf8');
    const adminHtmlSource = readFrontendSource(path.join(__dirname, '..', '..', 'admin.html'), 'utf8');

    // Canvas owns the model-facing LaTeX workflow. The admin Tools page should
    // therefore expose only the shared service-connections infrastructure.
    assert.doesNotMatch(helperSource, /targetPage:\s*'latex-pdf-settings'/);
    assert.doesNotMatch(helperSource, /key:\s*'latex-pdf-settings'/);
    assert.doesNotMatch(helperSource, /tool_title_latex_pdf/);
    assert.doesNotMatch(pagesSource, /initLatexPdfSettingsPage/);
    assert.doesNotMatch(adminHtmlSource, /\/js\/admin\/latexPdf\.js/);

    // Keep the renderer purpose configurable through one infrastructure card
    // and the shared connections table.
    assert.match(helperSource, /targetPage:\s*'service-connections'/);
    assert.match(helperSource, /titleKey:\s*'tool_title_service_connections'/);
    assert.match(serviceConnectionsSource, /enabled_for_latex_pdf/);
    assert.match(serviceConnectionsSource, /service_connections_latex_pdf/);
});
