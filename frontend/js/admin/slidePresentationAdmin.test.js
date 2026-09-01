const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');

test('slide presentation admin uses a single tool card and no separate rendering page', () => {
    const helperSource = readFrontendSource(path.join(__dirname, 'helper.js'), 'utf8');
    const pagesSource = readFrontendSource(path.join(__dirname, 'pages.js'), 'utf8');
    const adminHtmlSource = readFrontendSource(path.join(__dirname, '..', '..', 'admin.html'), 'utf8');

    assert.doesNotMatch(helperSource, /targetPage:\s*'slide-presentation-render-settings'/);
    assert.doesNotMatch(helperSource, /key:\s*'slide-presentation-render-settings'/);
    assert.doesNotMatch(helperSource, /tool_title_slide_presentation_rendering/);
    assert.doesNotMatch(pagesSource, /initSlidePresentationRenderSettingsPage/);
    assert.doesNotMatch(adminHtmlSource, /slide-presentation-render\.js/);
});

test('tool error statistics prefer nested slide-generation diagnostics', () => {
    const statisticsSource = readFrontendSource(path.join(__dirname, 'modelStatistics.js'), 'utf8');

    assert.match(statisticsSource, /err\?\.meta\?\.nested_generation/);
    assert.match(statisticsSource, /err\?\.meta\?\.slide_presentation/);
    assert.match(statisticsSource, /nestedContext\?\.model_name \|\| \(hasInternalComponent/);
    assert.match(statisticsSource, /nestedContext\?\.provider \|\| \(hasInternalComponent/);
    assert.match(statisticsSource, /nestedContext\?\.phase/);
});
