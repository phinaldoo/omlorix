const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '../../..');
const source = fs.readFileSync(path.join(__dirname, 'init.js'), 'utf8');
const indexSource = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const adminSource = fs.readFileSync(path.join(root, 'js/admin/userSettings.js'), 'utf8');

test('managed accounts hide local profile and authentication controls', () => {
    assert.match(source, /data\?\.externally_managed === true/);
    assert.match(source, /personalInformation\.hidden = externallyManaged/);
    assert.match(source, /signInMethods\.hidden = externallyManaged/);
    assert.match(source, /!externallyManaged && typeof window\.loadSignInMethods/);
    assert.match(indexSource, /id="externallyManagedAccountNotice"[^>]*hidden/);
    assert.match(indexSource, /id="personalInformationSettingsSection"/);
});

test('admin editor removes managed identity and local authentication controls', () => {
    assert.match(adminSource, /if \(!profile\.externally_managed\) \{[\s\S]*const personalSection/);
    assert.match(adminSource, /Local password, failed-attempt, and 2FA controls/);
    assert.match(adminSource, /state\.profile\?\.externally_managed[\s\S]*wrong_sign_in_attempts/);
});
