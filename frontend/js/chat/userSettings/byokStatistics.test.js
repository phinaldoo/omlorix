const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const BYOK_PATH = path.join(__dirname, '..', 'byok.js');
const USER_SETTINGS_INIT_PATH = path.join(__dirname, 'init.js');

test('opening the BYOK settings page refreshes statistics without a manual button', () => {
    const byokSource = fs.readFileSync(BYOK_PATH, 'utf8');
    const settingsSource = fs.readFileSync(USER_SETTINGS_INIT_PATH, 'utf8');

    assert.match(
        settingsSource,
        /activeSection === 'byok'[\s\S]*window\.BYOK\?\.refreshStatistics\?\.\(\)/,
    );
    assert.match(byokSource, /async function refreshStatistics\(\)[\s\S]*ensureByokStatsLoaded\(true\)/);
    assert.match(byokSource, /window\.BYOK = \{[\s\S]*refreshStatistics/);
    assert.doesNotMatch(byokSource, /byokStatisticsRefresh/);
    assert.doesNotMatch(byokSource, /byok_stats_refresh/);
});
