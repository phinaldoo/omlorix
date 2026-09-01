const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const test = require('node:test');

const rendererRoot = path.join(__dirname, '..', 'renderer');

test('background-service actions preserve focus when reciprocal controls swap', async () => {
  const [html, renderer] = await Promise.all([
    fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8'),
  ]);

  assert.match(
    html,
    /id="proxyServiceStatus"[^>]*role="status"[^>]*aria-live="polite"[^>]*tabindex="-1"/,
  );
  assert.match(renderer, /focusedServiceAction === 'install'[\s\S]*proxyUninstallServiceButton\.focus\(\)/);
  assert.match(renderer, /focusedServiceAction === 'uninstall'[\s\S]*proxyInstallServiceButton\.focus\(\)/);
});
