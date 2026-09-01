const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');

const {
  getTrustedLauncherUrl,
  isTrustedLauncherUrl,
  isTrustedRendererUrl,
} = require('../launcher-security');

test('isTrustedLauncherUrl accepts the bundled launcher page with hashes and queries', () => {
  const trustedUrl = getTrustedLauncherUrl(path.resolve(__dirname, '..'));

  assert.equal(isTrustedLauncherUrl(trustedUrl, trustedUrl), true);
  assert.equal(isTrustedLauncherUrl(`${trustedUrl}#logs`, trustedUrl), true);
  assert.equal(isTrustedLauncherUrl(`${trustedUrl}?tab=setup`, trustedUrl), true);
});

test('isTrustedLauncherUrl rejects different origins and different local files', () => {
  const trustedUrl = getTrustedLauncherUrl(path.resolve(__dirname, '..'));

  assert.equal(isTrustedLauncherUrl('https://evil.example/pwned.html', trustedUrl), false);
  assert.equal(isTrustedLauncherUrl('file:///tmp/pwned.html', trustedUrl), false);
  assert.equal(isTrustedLauncherUrl('not a url', trustedUrl), false);
});

test('isTrustedRendererUrl accepts only the launcher renderer page', () => {
  const baseDir = path.resolve(__dirname, '..');
  const trustedUrls = [
    getTrustedLauncherUrl(baseDir),
  ];

  assert.equal(isTrustedRendererUrl(trustedUrls[0], trustedUrls), true);
  assert.equal(isTrustedRendererUrl('file:///tmp/update.html', trustedUrls), false);
});
