const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');


const source = fs.readFileSync(path.join(__dirname, 'rateLimits.js'), 'utf8');


test('new rate limits preserve the selected active state', () => {
    assert.doesNotMatch(source, /delete payload\.is_active/);
    assert.match(source, /createRateLimit\(payload\)/);
});


test('rate-limit timezone options come from the shared timezone catalog', () => {
    assert.match(source, /OmlorixTimeZones\?\.getSupportedTimeZoneValues/);
    assert.doesNotMatch(source, /Intl\.supportedValuesOf\(['"]timeZone['"]\)/);
});
