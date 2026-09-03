const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

function readWorkspaceFile(relativePath) {
    return fs.readFileSync(path.join(__dirname, '../../..', relativePath), 'utf8');
}

test('server setup completes after default role without a legal configuration page', () => {
    const markup = readWorkspaceFile('frontend/server_setup.html');
    const stateSource = readWorkspaceFile('frontend/js/server_setup/state.js');
    const mainSource = readWorkspaceFile('frontend/js/server_setup/main.js');
    const navigationSource = readWorkspaceFile('frontend/js/server_setup/navigation.js');
    const apiSource = readWorkspaceFile('frontend/js/server_setup/api.js');

    assert.match(stateSource, /totalSteps:\s*6/);
    assert.match(stateSource, /const SHOW_DONATION_STEP = (?:true|false);/);
    assert.match(navigationSource, /return SHOW_DONATION_STEP \? \[1, \.\.\.steps\] : steps;/);
    assert.deepEqual(
        Array.from(markup.matchAll(/class="step(?: active)?" data-step="(\d+)"/g), (match) => match[1]),
        ['0', '1', '2', '3', '4', '5', '6']
    );
    assert.match(markup, /<!-- Step 6: Complete Screen -->/);

    // Every configuration screen uses the same copy-left/form-right structure,
    // with no decorative image requests or lazy-loading code left behind.
    assert.equal((markup.match(/class="step-copy"/g) || []).length, 5);
    assert.equal((markup.match(/class="step-form"/g) || []).length, 5);
    assert.doesNotMatch(markup, /step-icon|assets\/server_setup\/.*\.webp/);
    assert.doesNotMatch(mainSource, /lazyLoadStepImages|stepImagesLoaded/);

    const removedSetupTerms = [
        'privacyLinkToggle',
        'termsLinkToggle',
        'legalReviewConfirmation',
        'controllerLegalNameInput',
        'controllerAddressInput',
        'dpoContactEmailInput',
        '/assets/server_setup/privacy.webp',
        'show_privacy_notice_link',
        'show_terms_of_service_link',
        'legal_review_confirmed',
    ];
    const setupSources = [markup, stateSource, mainSource, navigationSource, apiSource].join('\n');
    for (const removedTerm of removedSetupTerms) {
        assert.doesNotMatch(setupSources, new RegExp(removedTerm));
    }
});

test('server setup keeps its next action at the navigation end when back is hidden', () => {
    const setupStyles = readWorkspaceFile('frontend/css/serverSetup/style.css');

    assert.match(
        setupStyles,
        /\.navigation \.submit\s*\{[\s\S]*?margin-inline-start:\s*auto;/
    );
});
