const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const INDEX_PATH = path.join(__dirname, '..', '..', '..', 'index.html');
const INIT_PATH = path.join(__dirname, 'init.js');
const CSS_PATH = path.join(__dirname, '..', '..', '..', 'css', 'userSettings', 'style.css');
const I18N_PATH = path.join(__dirname, '..', '..', '..', 'i18n');

test('user settings keeps page titles but removes header descriptions', () => {
    const indexSource = fs.readFileSync(INDEX_PATH, 'utf8');
    const initSource = fs.readFileSync(INIT_PATH, 'utf8');
    const cssSource = fs.readFileSync(CSS_PATH, 'utf8');

    // The header container and translated title remain; only the descriptive
    // paragraph and the code that updates it are intentionally gone.
    assert.match(indexSource, /class="us-settings-header-text"[\s\S]*data-i18n="us_page_profile_title"/);
    assert.doesNotMatch(indexSource, /<p[^>]*data-i18n="us_page_[^"]+_desc"/);
    assert.doesNotMatch(initSource, /userSettingsHeaderDesc|desc:\s*\(\)\s*=>\s*usT\(\s*['"]us_page_[^'"]+_desc/);
    assert.doesNotMatch(cssSource, /\.us-settings-header p\s*\{/);

    for (const localeEntry of fs.readdirSync(I18N_PATH, { withFileTypes: true })) {
        if (!localeEntry.isDirectory()) continue;
        const dictionary = JSON.parse(
            fs.readFileSync(path.join(I18N_PATH, localeEntry.name, 'index.json'), 'utf8'),
        );
        const pageTitles = Object.keys(dictionary).filter((key) => /^us_page_.+_title$/.test(key));
        const pageDescriptions = Object.keys(dictionary).filter((key) => /^us_page_.+_desc$/.test(key));
        assert.equal(pageTitles.length, 12, `${localeEntry.name} should keep all user settings page titles`);
        assert.deepEqual(pageDescriptions, [], `${localeEntry.name} should not keep header descriptions`);
    }
});
