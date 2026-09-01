const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const electronRoot = path.join(__dirname, '..');
const rendererRoot = path.join(electronRoot, 'renderer');

test('backup settings are an accessible conditional panel with separate recovery actions', async () => {
  const html = await fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8');
  const notice = html.indexOf('id="backupAvailabilityNotice"');
  const controls = html.indexOf('id="backupCreateControls"');
  const result = html.indexOf('id="backupResult"');
  const download = html.indexOf('id="backupDownloadControls"');
  const restore = html.indexOf('id="restoreButton"');
  const verify = html.indexOf('id="verifyBackupButton"');

  assert(notice >= 0, 'the stopped-server notice must exist');
  assert(controls > notice, 'backup settings must replace the stopped-server notice');
  assert(result > controls, 'the completion result must follow backup settings');
  assert(download > result, 'catalogued downloads must follow backup creation');
  assert(restore > download, 'restore must remain a separate recovery action');
  assert(verify > restore, 'standalone verification must be available beside restore');
  assert.match(
    html.slice(notice, controls),
    /role="status" aria-live="polite" aria-atomic="true"/,
  );
  assert.match(html.slice(controls, result), /id="backupCreateControls" hidden/);
  assert.match(html.slice(controls, result), /id="backupDestinationSelect"/);
  assert.match(html.slice(controls, result), /id="backupEncryptionEnabled" type="checkbox" checked/);
  assert.match(html.slice(controls, result), /id="backupButton" type="button"/);
  assert.match(html.slice(result, restore), /role="status" aria-live="polite" aria-atomic="true" hidden/);
  assert.match(html.slice(download, restore), /id="backupDownloadSelect"/);
  assert.match(html.slice(download, restore), /id="backupDownloadButton" type="button"/);
});

test('automatic update backups link to the Dashboard backup configuration', async () => {
  const [html, source] = await Promise.all([
    fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8'),
  ]);
  const navigationStart = source.indexOf('function openDashboardBackupSettings()');
  const navigationEnd = source.indexOf('function renderAutoUpdates(', navigationStart);
  const navigationSource = source.slice(navigationStart, navigationEnd);

  assert.match(html, /id="dashboardBackupSettings" tabindex="-1"/);
  assert.match(html, /id="autoUpdateBackupReferenceText"/);
  assert.match(
    html,
    /id="autoUpdateBackupSettingsButton"[^>]*type="button"[^>]*aria-controls="dashboardBackupSettings"/,
  );
  assert.match(navigationSource, /dataset\.section === 'status'/);
  assert.match(navigationSource, /dashboardLink\?\.click\(\)/);
  assert.match(navigationSource, /dashboardBackupSettings\.scrollIntoView/);
  assert.match(navigationSource, /dashboardBackupSettings\.focus/);
  assert.match(
    source,
    /autoUpdateBackupSettingsButton\.addEventListener\('click', openDashboardBackupSettings\)/,
  );
  assert.match(source, /backupDestinationId: state\.backupDestinationId/);
  assert.match(source, /backupEncryptionEnabled: state\.backupEncryptionPreferred/);
  assert.doesNotMatch(source, /BACKUP_DESTINATION_STORAGE_KEY|omlorix-launcher-backup-destination/);
  assert.match(source, /unavailableOption\.value = selectedDestinationId/);
  assert.match(
    source,
    /backupDestinationSelect\.addEventListener\('change',[\s\S]*saveBackupPolicy\(\)/,
  );
  assert.match(
    source,
    /backupEncryptionEnabled\.addEventListener\('change',[\s\S]*saveBackupPolicy\(\)/,
  );
});

test('launcher loads safe backend options only when healthy and forwards the selected controls', async () => {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const readyStart = source.indexOf('function backupServerReady(');
  const readyEnd = source.indexOf('/** Format archive sizes', readyStart);
  const panelStart = source.indexOf('function renderBackupPanel()');
  const panelEnd = source.indexOf('async function refreshBackupOptions(', panelStart);
  const loadStart = panelEnd;
  const loadEnd = source.indexOf('function dockerActionBlockedMessage(', loadStart);
  const createStart = source.indexOf('async function createServerBackup()');
  const createEnd = source.indexOf('async function runEnvironmentSetup()', createStart);

  assert.match(source.slice(readyStart, readyEnd), /stack\.healthy && omlorixServiceRunning\(stack\)/);
  assert.match(source.slice(panelStart, panelEnd), /backupCreateControls\.hidden = showNotice/);
  assert.match(source.slice(panelStart, panelEnd), /launcher_backup_unavailable_title/);
  assert.match(source.slice(panelStart, panelEnd), /archive_encryption_available/);
  assert.match(source.slice(panelStart, panelEnd), /plaintext_archives_allowed/);
  assert.match(source.slice(loadStart, loadEnd), /if \(!backupServerReady\(\)\)/);
  assert.match(source.slice(loadStart, loadEnd), /window\.omlorixServer\.getBackupOptions\(\)/);
  assert.match(source.slice(createStart, createEnd), /destinationId,/);
  assert.match(source.slice(createStart, createEnd), /encryptionEnabled,/);
  assert.match(source.slice(createStart, createEnd), /window\.omlorixServer\.backup\(\{/);
  assert.match(source, /window\.omlorixServer\.getBackupJobs\(\)/);
  assert.match(source, /window\.omlorixServer\.downloadBackup\(jobId,/);
});

test('backup option discovery and creation are connected through trusted IPC', async () => {
  const mainSource = await fs.readFile(path.join(electronRoot, 'main.js'), 'utf8');
  const preloadSource = await fs.readFile(path.join(electronRoot, 'preload.js'), 'utf8');

  assert.match(
    mainSource,
    /handleTrustedIpc\('server:get-backup-options', async \(\) => serverManager\.getBackupOptions\(\)\)/,
  );
  assert.match(
    mainSource,
    /handleTrustedIpc\('server:backup', async \(event, options\) => serverManager\.backup\(options\)\)/,
  );
  assert.match(
    preloadSource,
    /getBackupOptions: \(\) => ipcRenderer\.invoke\('server:get-backup-options'\)/,
  );
  assert.match(
    preloadSource,
    /backup: \(options\) => ipcRenderer\.invoke\('server:backup', options\)/,
  );
  assert.match(
    mainSource,
    /handleTrustedIpc\('server:get-backup-jobs', async \(\) => serverManager\.getBackupJobs\(\)\)/,
  );
  assert.match(mainSource, /handleTrustedIpc\('server:download-backup'/);
  assert.match(mainSource, /serverManager\.getBackupDownloadInfo\(options\.jobId\)/);
  assert.match(mainSource, /serverManager\.downloadBackup\(info\.jobId, result\.filePath\)/);
  assert.match(
    preloadSource,
    /getBackupJobs: \(\) => ipcRenderer\.invoke\('server:get-backup-jobs'\)/,
  );
  assert.match(preloadSource, /downloadBackup: \(jobId, options\) => invokeBackupDownload\(jobId, options\)/);
  assert.match(
    mainSource,
    /handleTrustedIpc\('server:verify-backup', async \(event, source\) => serverManager\.verifyBackup\(source\)\)/,
  );
  assert.match(
    preloadSource,
    /verifyBackup: \(source\) => ipcRenderer\.invoke\('server:verify-backup', source\)/,
  );
  assert.match(
    mainSource,
    /handleTrustedIpc\('server:update', async \(event, options\)[\s\S]*serverManager\.update\(options\)/,
  );
  assert.match(
    preloadSource,
    /update: \(options\) => ipcRenderer\.invoke\('server:update', options\)/,
  );
});

test('automatic .env backup disable is connected through confirmed trusted IPC', async () => {
  const [html, setupSource, launcherSource, preloadSource, mainSource] = await Promise.all([
    fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'setup-flow.js'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8'),
    fs.readFile(path.join(electronRoot, 'preload.js'), 'utf8'),
    fs.readFile(path.join(electronRoot, 'main.js'), 'utf8'),
  ]);

  assert.match(html, /id="disableAutomaticEnvBackupButton"[^>]*type="button"[^>]*hidden/);
  assert.match(setupSource, /refs\.disableAutomaticBackup\.hidden = !data\.setup\?\.backupConfigured/);
  assert.match(setupSource, /await showDialog\(\{[\s\S]*disable_automatic_backup_title[\s\S]*disable_automatic_backup_confirm/);
  assert.match(setupSource, /if \(!confirmed\) return;[\s\S]*window\.omlorixServer\.disableEnvBackup\(\)/);
  assert.match(launcherSource, /window\.omlorixShowLauncherDialog = showLauncherDialog/);
  assert.match(
    preloadSource,
    /disableEnvBackup: \(\) => ipcRenderer\.invoke\('server:disable-env-backup'\)/,
  );
  assert.match(
    mainSource,
    /handleTrustedIpc\('server:disable-env-backup', async \(\) => serverManager\.disableAutomaticEnvBackup\(\)\)/,
  );
});
