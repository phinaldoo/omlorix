const assert = require('node:assert/strict');
const { execFile } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const { promisify } = require('node:util');

const { createElectronSpawnEnvironment } = require('../scripts/dev-electron-runtime.cjs');

const execFileAsync = promisify(execFile);

async function buildNativeMenu(locale) {
  const electronPath = require('electron');
  const runnerPath = path.join(__dirname, 'fixtures', 'native-menu-i18n-runner.js');
  const { stdout } = await execFileAsync(electronPath, [
    '--headless',
    '--disable-gpu',
    `--lang=${locale}`,
    runnerPath,
  ], {
    cwd: path.join(__dirname, '..', '..'),
    env: createElectronSpawnEnvironment({
      ...process.env,
      ELECTRON_DISABLE_SECURITY_WARNINGS: 'true',
    }),
    timeout: 50_000,
  });

  return JSON.parse(stdout.trim());
}

function labelsByRole(result) {
  return Object.fromEntries(
    result.roleItems.map((item) => [item.role, item]),
  );
}

test('Electron builds localized German and Japanese native role labels', {
  skip: process.platform !== 'darwin',
  timeout: 120_000,
}, async () => {
  const german = await buildNativeMenu('de');
  const germanRoles = labelsByRole(german);

  assert.equal(german.locale, 'de');
  assert.equal(germanRoles.about.label, 'Über Omlorix Server Launcher');
  assert.equal(germanRoles.undo.label, 'Rückgängig');
  assert.equal(germanRoles.copy.label, 'Kopieren');
  assert.equal(germanRoles.reload.label, 'Neu laden');
  assert.equal(germanRoles.resetzoom.label, 'Tatsächliche Größe');
  assert.equal(germanRoles.togglefullscreen.label, 'Vollbildmodus ein-/ausschalten');
  german.roleItems.forEach((item) => {
    assert.equal(item.accessibilityLabel, item.label);
  });

  const japanese = await buildNativeMenu('ja');
  const japaneseRoles = labelsByRole(japanese);

  assert.equal(japanese.locale, 'ja');
  assert.equal(japaneseRoles.about.label, 'Omlorix Server Launcher について');
  assert.equal(japaneseRoles.copy.label, 'コピー');
  assert.equal(japaneseRoles.reload.label, '再読み込み');
  assert.equal(japaneseRoles.togglefullscreen.label, 'フルスクリーンを切り替える');
  japanese.roleItems.forEach((item) => {
    assert.equal(item.accessibilityLabel, item.label);
  });
});
