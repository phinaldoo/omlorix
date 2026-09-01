const test = require('node:test');
const assert = require('node:assert/strict');

const { compareVersions, normalizeVersion } = require('../version-utils');

test('normalizeVersion accepts launcher tag prefixes', () => {
  assert.equal(normalizeVersion('server-launcher-v1.2.3'), '1.2.3');
  assert.equal(normalizeVersion('launcher-v1.2.3-beta.1'), '1.2.3-beta.1');
});

test('compareVersions orders stable semver and prereleases', () => {
  assert.equal(compareVersions('1.2.4', '1.2.3'), 1);
  assert.equal(compareVersions('v1.2.3', '1.2.3'), 0);
  assert.equal(compareVersions('1.2.3-beta.1', '1.2.3'), -1);
  assert.equal(compareVersions('1.3.0', '1.10.0'), -1);
  assert.equal(compareVersions('1.2.3-beta.10', '1.2.3-beta.2'), 1);
  assert.equal(compareVersions('1.2.3-beta', '1.2.3-beta.1'), -1);
  assert.equal(compareVersions('1.2.3-alpha.z', '1.2.3-alpha.9'), 1);
});
