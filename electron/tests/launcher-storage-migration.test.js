const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const electronRoot = path.join(__dirname, '..');

test('launcher exposes a safe end-to-end storage migration workflow', async () => {
  const [html, renderer, preload, main] = await Promise.all([
    fs.readFile(path.join(electronRoot, 'renderer', 'launcher.html'), 'utf8'),
    fs.readFile(path.join(electronRoot, 'renderer', 'launcher.js'), 'utf8'),
    fs.readFile(path.join(electronRoot, 'preload.js'), 'utf8'),
    fs.readFile(path.join(electronRoot, 'main.js'), 'utf8'),
  ]);

  for (const id of [
    'storageMigrationSource',
    'storageMigrationDestination',
    'storageMigrationScope',
    'storageMigrationDryRun',
    'storageMigrationDeleteSource',
    'storageMigrationForce',
    'storageProbeButton',
    'storageMigrateButton',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }

  const dashboardSection = html.slice(
    html.indexOf('<section id="status"'),
    html.indexOf('<section id="settings"'),
  );
  assert.doesNotMatch(dashboardSection, /id="storageMigrationControls"/);
  assert.match(html, /id="openStorageMigrationButton"[\s\S]*?aria-controls="storage-migration"[\s\S]*?data-open-section="storage-migration"/);
  assert.match(html, /id="storage-migration"[^>]*data-parent-section="settings"/);
  assert.match(html, /id="storageMigrationBackButton"[\s\S]*?data-open-section="settings"[\s\S]*?data-section-focus="openStorageMigrationButton"/);
  assert.match(html, /id="storageMigrationDryRun" type="checkbox" checked/);
  assert.match(renderer, /showLauncherDialog\(\{/);
  assert.match(renderer, /deleteSource: !dryRun/);
  assert.match(renderer, /force: !dryRun/);
  assert.match(renderer, /els\.storageMigrationControls\.hidden = !ready/);
  assert.match(renderer, /window\.omlorixServer\.probeStorage\(\)/);
  assert.match(renderer, /window\.omlorixServer\.migrateStorage\(payload\)/);
  assert.match(preload, /probeStorage: \(\) => ipcRenderer\.invoke\('server:storage-probe'\)/);
  assert.match(preload, /migrateStorage: \(options\) => ipcRenderer\.invoke\('server:storage-migrate', options\)/);
  assert.match(main, /handleTrustedIpc\('server:storage-probe', async \(\) => serverManager\.probeStorage\(\)\)/);
  assert.match(main, /handleTrustedIpc\('server:storage-migrate', async \(event, options\) => serverManager\.migrateStorage\(options\)\)/);
});
