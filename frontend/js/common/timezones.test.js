const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');


const source = fs.readFileSync(path.join(__dirname, 'timezones.js'), 'utf8');


function createHarness() {
    function DateTimeFormat() {
        return {
            format: () => '',
            formatToParts: () => [{ type: 'timeZoneName', value: 'GMT+2' }],
            resolvedOptions: () => ({ timeZone: 'Europe/Berlin' }),
        };
    }

    const context = {
        Array,
        Date,
        Intl: {
            DateTimeFormat,
            supportedValuesOf: () => ['Asia/Tokyo', 'Europe/Berlin'],
        },
        Object,
        Set,
        String,
        window: {},
    };
    vm.createContext(context);
    vm.runInContext(source, context);
    return context.window.OmlorixTimeZones;
}


test('shared timezone catalog pins the browser timezone and UTC before all options', () => {
    const timeZones = createHarness();

    assert.deepEqual(
        Array.from(timeZones.getSupportedTimeZoneValues(['America/New_York'])),
        ['Europe/Berlin', 'UTC', 'America/New_York', 'Asia/Tokyo']
    );
});


test('shared timezone labels include the current UTC offset', () => {
    const timeZones = createHarness();

    assert.equal(timeZones.formatTimeZoneLabel('Europe/Berlin'), 'Europe/Berlin (UTC+2)');
});

