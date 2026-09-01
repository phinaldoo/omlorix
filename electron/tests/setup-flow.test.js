const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

const { ServerManager } = require('../server-manager');

const ENV_TEMPLATE = `
JWT_SECRET_KEY=""
ENCRYPTION_KEY=""
PASSWORD_RESET_IDENTIFIER_HASH_SALT=""
LOG_IP_HASH_SALT=""
BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE=""
DATABASE_PASSWORD="CHANGE_ME"
REDIS_PASSWORD="CHANGE_ME"
REDIS_URL="redis://:CHANGE_ME@redis:6379/0"
OMLORIX_USE_BUNDLED_DB=true
OMLORIX_USE_BUNDLED_REDIS=true
OMLORIX_USE_BUNDLED_STORAGE=false
DATABASE_NAME="omlorix"
DATABASE_USER="postgres"
FILE_STORAGE_PROVIDER="local"
FRONTEND_HTTP_HOST_PORT=8080
`;

/** Replace or append dotenv assignments while preserving all other test data. */
function updateEnvContentForTest(raw, updates) {
  let next = String(raw || '');
  for (const [key, value] of Object.entries(updates)) {
    const assignment = `${key}="${String(value).replaceAll('\\', '\\\\').replaceAll('"', '\\"')}"`;
    const pattern = new RegExp(`^${key}=.*$`, 'm');
    next = pattern.test(next)
      ? next.replace(pattern, assignment)
      : `${next.trimEnd()}\n${assignment}\n`;
  }
  return next;
}

/** Create a manager whose status checks never require Docker or local sockets. */
async function createManager({ existingEnv = '' } = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-setup-flow-'));
  const userData = path.join(root, 'user-data');
  await fs.writeFile(path.join(root, '.env.example'), ENV_TEMPLATE, 'utf8');
  const manager = new ServerManager({
    app: {
      isPackaged: false,
      getPath: () => userData,
      getVersion: () => '0.0.0-test',
      getName: () => 'Omlorix Test',
    },
    appRoot: root,
  });
  manager.dockerStatus = async () => ({ installed: false, running: false, compose: false });
  if (existingEnv) {
    await fs.mkdir(manager.serverHome, { recursive: true });
    await fs.writeFile(manager.envFile, existingEnv, 'utf8');
  }
  return { manager, root };
}

test('new server setup remains required until an automatic .env backup is configured', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));

  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();
  let env = await manager.readEnv();
  let setup = await manager.readSetupState(env);
  assert.equal(setup.required, true);
  assert.equal(setup.backupCurrent, false);
  await assert.rejects(() => manager.saveSetupProgress({ complete: true }), /automatic \.env backup/i);

  const backupFile = path.join(root, 'omlorix.env');
  await manager.exportSecretsBackup(backupFile);
  env = await manager.readEnv();
  setup = await manager.readSetupState(env);
  assert.equal(setup.backupCurrent, true);
  let backupRaw = await fs.readFile(backupFile, 'utf8');
  assert.match(backupRaw, /ENCRYPTION_KEY=/);
  assert.match(backupRaw, /^OMLORIX_UPDATE_CHANNEL=stable$/m);
  assert.equal(backupRaw.includes('FRONTEND_HTTP_HOST_PORT'), true);
  assert.doesNotMatch(await fs.readFile(manager.envFile, 'utf8'), /^OMLORIX_UPDATE_CHANNEL=/m);

  await manager.updateServerSettings((current) => ({ ...current, updateChannel: 'beta' }));
  backupRaw = await fs.readFile(backupFile, 'utf8');
  assert.match(backupRaw, /^OMLORIX_UPDATE_CHANNEL=beta$/m);
  assert.doesNotMatch(await fs.readFile(manager.envFile, 'utf8'), /^OMLORIX_UPDATE_CHANNEL=/m);
  assert.equal(setup.backupConfigured, true);
  assert.equal(setup.backupFilePath, backupFile);
  const sharedBackupConfig = JSON.parse(await fs.readFile(
    path.join(manager.serverHome, '.omlorix-server-env-backup.json'),
    'utf8',
  ));
  assert.equal(sharedBackupConfig.target, backupFile);
  assert.match(sharedBackupConfig.fingerprint, /^[0-9a-f]{64}$/);

  const completed = await manager.saveSetupProgress({ currentStep: 99, complete: true });
  assert.equal(completed.setup.required, false);
  assert.equal(completed.setup.currentStep, 6);
});

test('generated-secret initialization upgrades a legacy short password-reset salt', async (t) => {
  const legacyEnv = ENV_TEMPLATE.replace(
    'PASSWORD_RESET_IDENTIFIER_HASH_SALT=""',
    'PASSWORD_RESET_IDENTIFIER_HASH_SALT="legacy"',
  );
  const { manager, root } = await createManager({ existingEnv: legacyEnv });
  t.after(() => fs.rm(root, { recursive: true, force: true }));

  await manager.ensureGeneratedSecrets();

  const env = await manager.readEnv();
  assert.notEqual(env.PASSWORD_RESET_IDENTIFIER_HASH_SALT, 'legacy');
  assert.match(env.PASSWORD_RESET_IDENTIFIER_HASH_SALT, /^[a-f0-9]{64}$/);
});

test('reaching review can complete setup without starting services', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));

  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();
  await manager.exportSecretsBackup(path.join(root, 'omlorix-secrets.env'));

  const review = await manager.saveSetupProgress({ currentStep: 5, complete: true });
  assert.equal(review.setup.complete, true);
  assert.equal(review.setup.required, false);
  assert.equal(review.setup.currentStep, 5);
});

test('setup checkpoints acknowledge the durable write without running diagnostics', { timeout: 2000 }, async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();

  let diagnosticReads = 0;
  manager.getState = async () => {
    diagnosticReads += 1;
    return new Promise(() => {});
  };

  const checkpoint = await manager.saveSetupProgress({ currentStep: 3 });

  assert.equal(diagnosticReads, 0);
  assert.deepEqual(checkpoint, {
    setup: {
      complete: false,
      currentStep: 3,
      completedAt: '',
      required: true,
    },
  });

  const restarted = new ServerManager({ app: manager.app, appRoot: root });
  const resumed = await restarted.readSetupState(await restarted.readEnv());
  assert.equal(resumed.currentStep, 3);
  assert.equal(resumed.required, true);
});

test('changing any setting automatically refreshes the selected .env backup', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));

  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();
  const backupFile = path.join(root, 'omlorix.env');
  await manager.exportSecretsBackup(backupFile);
  assert.equal((await manager.readSetupState(await manager.readEnv())).backupCurrent, true);

  await manager.writeEnv({ FRONTEND_HTTP_HOST_PORT: '9090' });
  assert.equal((await manager.readSetupState(await manager.readEnv())).backupCurrent, true);
  assert.equal((await manager.readEnv()).FRONTEND_HTTP_HOST_PORT, '9090');
  assert.equal((await fs.readFile(backupFile, 'utf8')).includes('FRONTEND_HTTP_HOST_PORT=9090'), true);

  await fs.writeFile(backupFile, 'stale copy\n', 'utf8');
  await manager.saveAutomaticEnvBackup();
  assert.equal((await fs.readFile(backupFile, 'utf8')).includes('FRONTEND_HTTP_HOST_PORT=9090'), true);
});

test('automatic .env backup can be disabled without touching its former destination', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();

  const currentTarget = path.join(root, 'current-recovery.env');
  await manager.exportSecretsBackup(currentTarget);
  const currentRaw = await fs.readFile(currentTarget, 'utf8');
  assert.equal((await manager.getState()).setup.backupCurrent, true);
  manager.automaticEnvBackupError = 'write_failed';

  // Disabling changes only the canonical record, so it must not require write
  // access to the configured external device.
  await fs.chmod(currentTarget, 0o400);
  let disabled;
  try {
    disabled = await manager.disableAutomaticEnvBackup();
  } finally {
    await fs.chmod(currentTarget, 0o600);
  }
  assert.equal(disabled.ok, true);
  assert.equal(disabled.state.setup.backupConfigured, false);
  assert.equal(disabled.state.setup.backupCurrent, false);
  assert.equal(disabled.state.setup.backupFilePath, '');
  assert.equal(disabled.state.automaticEnvBackupError, '');
  assert.equal(await fs.readFile(currentTarget, 'utf8'), currentRaw);

  // Once disabled, subsequent environment writes must not refresh the retained
  // copy. Replacing the writer with a failure also proves the destination is not
  // accessed, which covers unavailable and read-only recovery devices.
  const originalWriter = manager.writeAutomaticEnvBackupFile.bind(manager);
  manager.writeAutomaticEnvBackupFile = async () => {
    throw new Error('the disabled recovery destination must not be accessed');
  };
  await manager.writeEnv({ FRONTEND_HTTP_HOST_PORT: '9292' });
  assert.equal(await fs.readFile(currentTarget, 'utf8'), currentRaw);
  manager.writeAutomaticEnvBackupFile = originalWriter;

  const outdatedTarget = path.join(root, 'outdated-recovery.env');
  await manager.exportSecretsBackup(outdatedTarget);
  await fs.writeFile(outdatedTarget, 'retained outdated recovery copy\n', 'utf8');
  assert.equal((await manager.getState()).setup.backupCurrent, false);
  await manager.disableAutomaticEnvBackup();
  assert.equal(await fs.readFile(outdatedTarget, 'utf8'), 'retained outdated recovery copy\n');

  const missingTarget = path.join(root, 'missing-recovery.env');
  await manager.exportSecretsBackup(missingTarget);
  await fs.rm(missingTarget);
  const missingDisabled = await manager.disableAutomaticEnvBackup();
  assert.equal(missingDisabled.state.setup.backupConfigured, false);
  await assert.rejects(fs.stat(missingTarget), { code: 'ENOENT' });

  const disabledAgain = await manager.disableAutomaticEnvBackup();
  assert.equal(disabledAgain.ok, true);
  assert.equal(disabledAgain.state.setup.backupConfigured, false);
  assert.deepEqual(
    JSON.parse(await fs.readFile(manager.automaticEnvBackupConfigFile, 'utf8')),
    { target: '', lastSavedAt: '', fingerprint: '' },
  );
});

test('automatic .env refresh failures warn without failing the live write', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));

  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();
  const unavailableBackup = path.join(root, 'unavailable.env');
  await manager.exportSecretsBackup(unavailableBackup);
  await fs.rm(unavailableBackup);
  await fs.mkdir(unavailableBackup);

  await manager.writeEnv({ FRONTEND_HTTP_HOST_PORT: '9191' });

  assert.equal((await manager.readEnv()).FRONTEND_HTTP_HOST_PORT, '9191');
  const warningState = await manager.getState();
  assert.ok(warningState.automaticEnvBackupError);
  await assert.rejects(() => manager.saveAutomaticEnvBackup());
  await assert.rejects(() => manager.exportSecretsBackup(unavailableBackup));

  const recoveredBackup = path.join(root, 'recovered.env');
  const recovered = await manager.exportSecretsBackup(recoveredBackup);
  assert.equal(recovered.state.automaticEnvBackupError, '');
});

test('automatic backup setup-state updates are serialized', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));

  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();
  const raw = await fs.readFile(manager.envFile, 'utf8');
  const firstBackup = path.join(root, 'first.env');
  const secondBackup = path.join(root, 'second.env');
  const writeTargets = [];
  const originalWriter = manager.writeAutomaticEnvBackupFile.bind(manager);
  let releaseFirst;
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  manager.writeAutomaticEnvBackupFile = async (targetPath, contents) => {
    writeTargets.push(targetPath);
    if (targetPath === firstBackup) await firstGate;
    return originalWriter(targetPath, contents);
  };

  const firstWrite = manager.recordAutomaticEnvBackup(firstBackup, raw);
  const secondWrite = manager.recordAutomaticEnvBackup(secondBackup, raw);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(writeTargets, [firstBackup]);
  releaseFirst();
  await Promise.all([firstWrite, secondWrite]);

  assert.deepEqual(writeTargets, [firstBackup, secondBackup]);
  assert.equal((await manager.readSetupState(await manager.readEnv())).backupFilePath, secondBackup);
});

test('configured installations created before the wizard migrate as complete', async (t) => {
  const configured = ENV_TEMPLATE
    .replace('JWT_SECRET_KEY=""', `JWT_SECRET_KEY="${'j'.repeat(64)}"`)
    .replace('LOG_IP_HASH_SALT=""', `LOG_IP_HASH_SALT="${'i'.repeat(48)}"`)
    .replace('ENCRYPTION_KEY=""', 'ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="')
    .replace('DATABASE_PASSWORD="CHANGE_ME"', 'DATABASE_PASSWORD="existing-database-password"')
    .replace('REDIS_PASSWORD="CHANGE_ME"', 'REDIS_PASSWORD="existing-redis-password"')
    .replace('redis://:CHANGE_ME@redis', 'redis://:existing-redis-password@redis');
  const { manager, root } = await createManager({ existingEnv: configured });
  t.after(() => fs.rm(root, { recursive: true, force: true }));

  const state = await manager.getState();
  assert.equal(state.setup.required, false);
  assert.equal(state.setup.complete, true);
  assert.equal(state.setup.currentStep, 6);
});

test('.env backup preserves JSON, quotes, backslashes, and newlines exactly', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();
  const credentials = '{"type":"service_account","private_key":"line 1\\\\quoted\\\"\\nline 2"}';
  const passphrase = 'first line\nsecond "quoted" line with \\\\slashes';
  await manager.writeEnv({
    FILE_STORAGE_GCS_CREDENTIALS_JSON: credentials,
    BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE: passphrase,
  });

  const backupFile = path.join(root, 'omlorix.env');
  await manager.exportSecretsBackup(backupFile);
  const restoreFile = path.join(root, 'omlorix-restore.env');
  await fs.copyFile(backupFile, restoreFile);
  await manager.writeEnv({
    FILE_STORAGE_GCS_CREDENTIALS_JSON: 'changed',
    BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE: 'changed',
  });
  const previousDestinationContents = await fs.readFile(backupFile, 'utf8');
  await manager.importSecretsBackup(restoreFile);

  const restored = await manager.readEnv();
  assert.equal(restored.FILE_STORAGE_GCS_CREDENTIALS_JSON, credentials);
  assert.equal(restored.BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE, passphrase);
  assert.equal(await fs.readFile(backupFile, 'utf8'), previousDestinationContents);
  assert.equal((await manager.readSetupState(restored)).backupFilePath, restoreFile);
  assert.equal(
    await fs.readFile(restoreFile, 'utf8'),
    await manager.recoveryEnvContent(await fs.readFile(manager.envFile, 'utf8')),
  );
});

test('.env backup restore replaces the snapshot instead of retaining newer variables', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();

  const automaticBackup = path.join(root, 'automatic.env');
  await manager.exportSecretsBackup(automaticBackup);
  const restoreFile = path.join(root, 'restore.env');
  await fs.copyFile(automaticBackup, restoreFile);
  await manager.writeEnv({ CUSTOM_BROKEN_SETTING: 'must-be-removed' });

  await manager.importSecretsBackup(restoreFile);

  const restored = await manager.readEnv();
  assert.equal(Object.prototype.hasOwnProperty.call(restored, 'CUSTOM_BROKEN_SETTING'), false);
});

test('.env recovery does not rewrite a token-free source solely for CRLF formatting', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();

  const exportedBackup = path.join(root, 'automatic.env');
  const restoreFile = path.join(root, 'restore-crlf.env');
  await manager.exportSecretsBackup(exportedBackup);
  const crlfRecoveryRaw = (await fs.readFile(exportedBackup, 'utf8')).replace(/\n/g, '\r\n');
  await fs.writeFile(restoreFile, crlfRecoveryRaw, 'utf8');

  await manager.importSecretsBackup(restoreFile);

  assert.equal(await fs.readFile(restoreFile, 'utf8'), crlfRecoveryRaw);
});

test('complete .env recovery replaces launcher security values without runtime mutation', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();

  const exportedBackup = path.join(root, 'automatic.env');
  const restoreFile = path.join(root, 'restore.env');
  await manager.exportSecretsBackup(exportedBackup);
  await fs.copyFile(exportedBackup, restoreFile);
  const sourceRaw = await fs.readFile(restoreFile, 'utf8');
  const recoveredInstallationId = 'recovered-installation-id';
  const recoveredProxySecret = 'b'.repeat(64);
  const exactRecoveryRaw = updateEnvContentForTest(sourceRaw, {
    OMLORIX_UPDATE_CHANNEL: 'beta',
    OMLORIX_INSTALLATION_ID: recoveredInstallationId,
    OMLORIX_LAUNCHER_PROXY_SECRET: recoveredProxySecret,
    OMLORIX_GITHUB_TOKEN: 'retired-release-token',
    FRONTEND_TRUSTED_UPSTREAMS: '10.25.0.10/32',
    FRONTEND_TRUST_PROXY_HEADERS: 'true',
  });
  await fs.writeFile(restoreFile, exactRecoveryRaw, 'utf8');

  manager.stackStatus = async () => { throw new Error('recovery inspected runtime'); };
  manager.proxyServiceStatus = async () => { throw new Error('recovery inspected proxy'); };
  manager.runDockerStep = async () => { throw new Error('recovery mutated Docker'); };
  manager.stopProxy = async () => { throw new Error('recovery stopped proxy'); };
  manager.createEnvBackup = async () => { throw new Error('recovery created backup'); };
  manager.validateComposeOwnership = async () => { throw new Error('recovery inspected ownership'); };
  manager.getState = async () => ({ stack: { running: 2 } });

  const result = await manager.importSecretsBackup(restoreFile);
  const restoredRaw = await fs.readFile(manager.envFile, 'utf8');
  const restored = await manager.readEnv();

  assert.equal(result.restartRequired, true);
  for (const key of [
    'OMLORIX_UPDATE_CHANNEL',
    'OMLORIX_LAUNCHER_PROXY_ENABLED',
    'OMLORIX_LAUNCHER_PROXY_AUTOSTART',
    'OMLORIX_LAUNCHER_PROXY_BIND',
    'OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME',
    'OMLORIX_LAUNCHER_PROXY_HTTP_PORT',
    'OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED',
    'OMLORIX_LAUNCHER_PROXY_HTTPS_PORT',
    'OMLORIX_LAUNCHER_PROXY_REDIRECT_HTTP_TO_HTTPS',
    'OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH',
    'OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH',
    'OMLORIX_LAUNCHER_PROXY_TLS_CA_PATH',
    'OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE',
  ]) {
    assert.doesNotMatch(restoredRaw, new RegExp(`^${key}=`, 'm'));
  }
  assert.equal(restored.OMLORIX_INSTALLATION_ID, recoveredInstallationId);
  assert.equal(restored.OMLORIX_LAUNCHER_PROXY_SECRET, recoveredProxySecret);
  assert.equal(restored.FRONTEND_TRUSTED_UPSTREAMS, '10.25.0.10/32');
  assert.equal(restored.FRONTEND_TRUST_PROXY_HEADERS, 'true');
  assert.equal(Object.prototype.hasOwnProperty.call(restored, 'OMLORIX_GITHUB_TOKEN'), false);
  const rewrittenRecoveryRaw = await fs.readFile(restoreFile, 'utf8');
  assert.doesNotMatch(
    rewrittenRecoveryRaw,
    /OMLORIX_GITHUB_TOKEN/,
  );
  assert.equal(rewrittenRecoveryRaw, await manager.recoveryEnvContent(restoredRaw));
  const setup = await manager.readSetupState(restored);
  assert.equal(setup.backupFilePath, restoreFile);
  assert.equal(setup.backupCurrent, true);
  assert.equal((await manager.readServerSettings()).updateChannel, 'beta');
});

test('legacy recovery keeps restored live state when its external copy cannot be refreshed', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();

  const exportedBackup = path.join(root, 'automatic.env');
  const restoreFile = path.join(root, 'legacy-read-only.env');
  await manager.exportSecretsBackup(exportedBackup);
  const legacyRecoveryRaw = updateEnvContentForTest(
    await fs.readFile(exportedBackup, 'utf8'),
    {
      OMLORIX_GITHUB_TOKEN: 'retired-release-token',
      CUSTOM_RECOVERY_VALUE: 'restored',
    },
  );
  await fs.writeFile(restoreFile, legacyRecoveryRaw, 'utf8');
  await manager.writeEnv({ CUSTOM_RECOVERY_VALUE: 'current' });
  manager.recordAutomaticEnvBackup = async () => {
    throw new Error('simulated read-only recovery destination');
  };
  manager.getState = async () => ({ ok: true });

  await manager.importSecretsBackup(restoreFile);

  const restored = await manager.readEnv();
  assert.equal(restored.CUSTOM_RECOVERY_VALUE, 'restored');
  assert.equal(Object.prototype.hasOwnProperty.call(restored, 'OMLORIX_GITHUB_TOKEN'), false);
  assert.match(await fs.readFile(restoreFile, 'utf8'), /OMLORIX_GITHUB_TOKEN/);
  assert.equal(manager.automaticEnvBackupError, 'write_failed');
  const setup = await manager.readSetupState(restored);
  assert.equal(setup.backupFilePath, restoreFile);
  assert.equal(setup.backupCurrent, false);
});

test('.env recovery without an update channel preserves the current setting', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();

  const recoveryFile = path.join(root, 'channel-less.env');
  await manager.exportSecretsBackup(recoveryFile);
  const withoutChannel = (await fs.readFile(recoveryFile, 'utf8'))
    .replace(/^OMLORIX_UPDATE_CHANNEL=.*(?:\r?\n|$)/m, '');
  await fs.writeFile(recoveryFile, withoutChannel, 'utf8');
  await manager.updateServerSettings((current) => ({ ...current, updateChannel: 'beta' }));

  await manager.importSecretsBackup(recoveryFile);

  assert.equal((await manager.readServerSettings()).updateChannel, 'beta');
  assert.doesNotMatch(await fs.readFile(manager.envFile, 'utf8'), /^OMLORIX_UPDATE_CHANNEL=/m);
});

test('.env recovery rejects an invalid update channel before changing live state', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();

  const recoveryFile = path.join(root, 'invalid-channel.env');
  await manager.exportSecretsBackup(recoveryFile);
  const invalidRecovery = updateEnvContentForTest(
    await fs.readFile(recoveryFile, 'utf8'),
    { OMLORIX_UPDATE_CHANNEL: 'nightly' },
  );
  await fs.writeFile(recoveryFile, invalidRecovery, 'utf8');
  const liveBefore = await fs.readFile(manager.envFile, 'utf8');
  const settingsBefore = await manager.readServerSettings();

  await assert.rejects(
    () => manager.importSecretsBackup(recoveryFile),
    /update channel must be stable or beta/i,
  );

  assert.equal(await fs.readFile(manager.envFile, 'utf8'), liveBefore);
  assert.deepEqual(await manager.readServerSettings(), settingsBefore);
});

test('.env backup restore never overwrites its source before the live commit', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();

  const automaticBackup = path.join(root, 'automatic.env');
  await manager.exportSecretsBackup(automaticBackup);
  const restoreFile = path.join(root, 'restore.env');
  await fs.copyFile(automaticBackup, restoreFile);
  const sourceBefore = await fs.readFile(restoreFile, 'utf8');
  await manager.writeEnv({ FRONTEND_HTTP_HOST_PORT: '9393' });
  const liveBefore = await fs.readFile(manager.envFile, 'utf8');
  manager.writeExactEnvRecoveryContent = async () => {
    throw new Error('simulated live commit failure');
  };

  await assert.rejects(
    () => manager.importSecretsBackup(restoreFile),
    /simulated live commit failure/,
  );
  assert.equal(await fs.readFile(restoreFile, 'utf8'), sourceBefore);
  assert.equal(await fs.readFile(manager.envFile, 'utf8'), liveBefore);
});

test('stale automatic backup state remains visible after a launcher restart', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();
  const backupFile = path.join(root, 'omlorix.env');
  await manager.exportSecretsBackup(backupFile);
  await fs.rm(backupFile);
  await fs.mkdir(backupFile);
  await manager.writeEnv({ FRONTEND_HTTP_HOST_PORT: '9494' });

  const restarted = new ServerManager({ app: manager.app, appRoot: root });
  restarted.dockerStatus = async () => ({ installed: false, running: false, compose: false });
  const state = await restarted.getState();

  assert.equal(state.setup.backupConfigured, true);
  assert.equal(state.setup.backupCurrent, false);
  assert.equal(state.automaticEnvBackupError, 'outdated');
});

test('.env backup import reports an import-specific error for server-folder paths', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();
  const externalBackup = path.join(root, 'omlorix-secrets.env');
  await manager.exportSecretsBackup(externalBackup);
  const inServerBackup = path.join(manager.serverHome, 'omlorix-secrets.env');
  await fs.copyFile(externalBackup, inServerBackup);

  await assert.rejects(
    () => manager.importSecretsBackup(inServerBackup),
    /selected Omlorix \.env backup must be outside the server folder/i,
  );
});

test('.env backup import requires a sufficiently long password reset salt', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();
  const backupFile = path.join(root, 'omlorix-secrets.env');
  await manager.exportSecretsBackup(backupFile);
  const completeBackup = await fs.readFile(backupFile, 'utf8');

  await fs.writeFile(
    backupFile,
    completeBackup.replace(/^PASSWORD_RESET_IDENTIFIER_HASH_SALT=.*$/m, ''),
    'utf8',
  );
  await assert.rejects(() => manager.importSecretsBackup(backupFile), /password reset salt is missing/i);

  await fs.writeFile(
    backupFile,
    completeBackup.replace(/^PASSWORD_RESET_IDENTIFIER_HASH_SALT=.*$/m, 'PASSWORD_RESET_IDENTIFIER_HASH_SALT="short"'),
    'utf8',
  );
  await assert.rejects(() => manager.importSecretsBackup(backupFile), /at least 16 characters/i);
});

test('.env backup import rejects missing active recovery credentials', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();
  const backupFile = path.join(root, 'omlorix-secrets.env');
  await manager.exportSecretsBackup(backupFile);
  const completeBackup = await fs.readFile(backupFile, 'utf8');

  for (const key of ['BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE', 'DATABASE_PASSWORD', 'REDIS_PASSWORD']) {
    await fs.writeFile(backupFile, completeBackup.replace(new RegExp(`^${key}=.*$`, 'm'), ''), 'utf8');
    await assert.rejects(
      () => manager.importSecretsBackup(backupFile),
      new RegExp(`incomplete.*${key}`, 'i'),
    );
  }
});

test('secret regeneration ignores credentials without a safe local generator', async (t) => {
  const { manager, root } = await createManager();
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();
  await manager.writeEnv({ FILE_STORAGE_GCS_CREDENTIALS_JSON: '{"project_id":"keep-me"}' });

  await manager.regenerateSecrets(['FILE_STORAGE_GCS_CREDENTIALS_JSON']);

  assert.equal(
    (await manager.readEnv()).FILE_STORAGE_GCS_CREDENTIALS_JSON,
    '{"project_id":"keep-me"}',
  );
});

test('every setup transition renders after its checkpoint without waiting for diagnostics', async () => {
  const setupSource = await fs.readFile(
    path.join(__dirname, '..', 'renderer', 'setup-flow.js'),
    'utf8',
  );
  const busyStart = setupSource.indexOf('  function setBusy');
  const busyEnd = setupSource.indexOf('\n  /** Derive Docker-dependent wizard chrome', busyStart);
  const navigationStart = setupSource.indexOf('  async function persistStep');
  const navigationEnd = setupSource.indexOf('\n  function appendLaunchLog', navigationStart);
  assert.ok(busyStart >= 0 && busyEnd > busyStart);
  assert.ok(navigationStart >= 0 && navigationEnd > navigationStart);

  function createNavigationHarness(startingStep) {
    const control = () => ({ disabled: false });
    const state = {
      busy: false,
      step: startingStep,
      showUntilDismissed: false,
      current: {
        setup: { complete: false, required: true, backupCurrent: true },
        docker: { installed: false, running: false, compose: false },
      },
    };
    const refs = {
      back: control(),
      next: control(),
      reviewDashboard: control(),
      downloadSetup: control(),
      regenerateSetup: control(),
      importSetup: control(),
      downloadPermanent: control(),
      changeBackupLocation: control(),
      disableAutomaticBackup: control(),
      importPermanent: control(),
      announcement: { textContent: '' },
      backupConfirm: { textContent: '' },
    };
    const checkpointCalls = [];
    const renderedSteps = [];
    const savedSteps = [];
    let checkpointError = null;
    let diagnosticReads = 0;
    const context = {
      state,
      refs,
      dockerReady: () => false,
      saveRuntimeStep: async () => { savedSteps.push('runtime'); },
      saveDataStep: async () => { savedSteps.push('data'); },
      saveAccessStep: async () => { savedSteps.push('access'); },
      launchOrFinish: async () => {},
      renderStep: () => { renderedSteps.push(state.step); },
      acceptState: (data) => { state.current = data; },
      translateLauncherMessage: (message) => message,
      t: (key) => key,
      window: {
        omlorixServer: {
          saveSetupProgress: async (payload) => {
            checkpointCalls.push(payload);
            if (checkpointError) throw checkpointError;
            return {
              setup: {
                currentStep: payload.currentStep,
                complete: payload.complete === true,
                required: payload.complete !== true,
              },
            };
          },
          getState: async () => {
            diagnosticReads += 1;
            return new Promise(() => {});
          },
        },
      },
    };
    vm.runInNewContext(
      `${setupSource.slice(busyStart, busyEnd)}\n`
        + `${setupSource.slice(navigationStart, navigationEnd)}\n`
        + 'this.goNextForTest = goNext; this.goBackForTest = goBack;',
      context,
    );
    return {
      context,
      checkpointCalls,
      renderedSteps,
      savedSteps,
      diagnosticReads: () => diagnosticReads,
      failCheckpoint: (error) => { checkpointError = error; },
    };
  }

  for (const startingStep of [0, 1, 2, 3, 4]) {
    const harness = createNavigationHarness(startingStep);
    await harness.context.goNextForTest();

    const expectedStep = startingStep + 1;
    assert.equal(harness.context.state.step, expectedStep);
    assert.deepEqual(harness.renderedSteps, [expectedStep]);
    assert.equal(harness.context.state.busy, false);
    assert.equal(harness.context.refs.back.disabled, false);
    assert.equal(harness.context.refs.next.disabled, false);
    assert.equal(harness.checkpointCalls.length, 1);
    assert.equal(harness.checkpointCalls[0].currentStep, expectedStep);
    assert.equal(harness.checkpointCalls[0].complete, expectedStep === 5);
    assert.equal(harness.diagnosticReads(), 0);
    assert.deepEqual(
      harness.savedSteps,
      startingStep === 1 ? ['runtime']
        : startingStep === 2 ? ['data']
          : startingStep === 3 ? ['access']
            : [],
    );
  }

  for (const startingStep of [1, 2, 3, 4, 5]) {
    const harness = createNavigationHarness(startingStep);
    await harness.context.goBackForTest();

    const expectedStep = startingStep - 1;
    assert.equal(harness.context.state.step, expectedStep);
    assert.deepEqual(harness.renderedSteps, [expectedStep]);
    assert.equal(harness.context.state.busy, false);
    assert.equal(harness.context.refs.back.disabled, expectedStep === 0);
    assert.equal(harness.context.refs.next.disabled, false);
    assert.equal(harness.checkpointCalls.length, 1);
    assert.equal(harness.checkpointCalls[0].currentStep, expectedStep);
    assert.equal(harness.checkpointCalls[0].complete, false);
    assert.equal(harness.diagnosticReads(), 0);
  }

  const retryHarness = createNavigationHarness(2);
  retryHarness.failCheckpoint(new Error('checkpoint failed'));
  await retryHarness.context.goNextForTest();
  assert.equal(retryHarness.context.state.step, 2);
  assert.deepEqual(retryHarness.renderedSteps, []);
  assert.equal(retryHarness.context.state.busy, false);
  assert.equal(retryHarness.context.refs.back.disabled, false);
  assert.equal(retryHarness.context.refs.next.disabled, false);
  assert.equal(retryHarness.context.refs.announcement.textContent, 'checkpoint failed');

  retryHarness.failCheckpoint(null);
  await retryHarness.context.goNextForTest();
  assert.equal(retryHarness.context.state.step, 3);
  assert.deepEqual(retryHarness.renderedSteps, [3]);
  assert.equal(retryHarness.checkpointCalls.length, 2);
});

test('backup passphrase is edited only from the Secrets page without a regeneration action', async () => {
  const rendererRoot = path.join(__dirname, '..', 'renderer');
  const [html, launcherSource, setupSource] = await Promise.all([
    fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'setup-flow.js'), 'utf8'),
  ]);

  const settingsStart = html.indexOf('<section id="settings"');
  const settingsEnd = html.indexOf('<!-- Secrets Section -->', settingsStart);
  const settingsMarkup = html.slice(settingsStart, settingsEnd);
  const secretsStart = html.indexOf('<section id="secrets"');
  const secretsEnd = html.indexOf('<!-- Proxy Section -->', secretsStart);
  const secretsMarkup = html.slice(secretsStart, secretsEnd);
  const secretsHeaderEnd = secretsMarkup.indexOf('</div>\n\n        <div id="serverSecretsMount">');
  const secretsHeaderMarkup = secretsMarkup.slice(0, secretsHeaderEnd);
  const setupSecretsStart = html.indexOf('<article class="setup-panel" data-setup-panel="4"');
  const setupSecretsEnd = html.indexOf('<article class="setup-panel" data-setup-panel="5"', setupSecretsStart);
  const setupSecretsMarkup = html.slice(setupSecretsStart, setupSecretsEnd);
  const setupSecretListIndex = setupSecretsMarkup.indexOf('id="setupSecretList"');
  const setupSecretActionsMatch = setupSecretsMarkup.match(/<div class="setup-secret-actions">([\s\S]*?)<\/div>/);

  assert.doesNotMatch(settingsMarkup, /Backup passphrase|backupPassphraseInput/);
  assert.doesNotMatch(secretsMarkup, /regenerateSecretsButton|regenerate_safe/);
  assert.doesNotMatch(secretsMarkup, /secretBackupCard|backup_outdated_title|backupFingerprintShort/);
  assert.match(secretsHeaderMarkup, /id="downloadSecretsBackupButton"[^>]*data-setup-i18n="save_now"/);
  assert.match(secretsHeaderMarkup, /id="changeEnvBackupLocationButton"[^>]*data-setup-i18n="change_backup_location"/);
  assert.match(secretsHeaderMarkup, /id="disableAutomaticEnvBackupButton"[^>]*data-setup-i18n="disable_automatic_backup"[^>]*hidden/);
  assert.match(secretsHeaderMarkup, /id="importSecretsBackupButton"[^>]*data-setup-i18n="restore_complete_env"/);
  assert.match(secretsMarkup, /id="automaticEnvBackupWarning"[^>]*role="status"/);
  assert.doesNotMatch(launcherSource, /backupPassphraseInput/);
  assert.doesNotMatch(setupSource, /regeneratePermanent|regenerate_safe|regenerateArmedUntil|renderBackupStatus/);
  assert.match(
    setupSource,
    /key: 'BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE', setting: 'backupPassphrase'/,
  );
  assert.match(setupSource, /regenerateSecrets\(\['JWT_SECRET_KEY'\]\)/);
  assert.match(
    setupSource,
    /new TextEncoder\(\)\.encode\(value\.trim\(\)\)\.length < 64/,
  );
  assert.match(html, /id="setupRegenerateSecretsButton"/);
  assert.match(html, /data-setup-i18n="choose_backup_location"/);
  assert.ok(setupSecretsMarkup.indexOf('id="setupBackupConfirm"') < setupSecretListIndex);
  assert.ok(setupSecretsMarkup.indexOf('id="setupDownloadSecretsButton"') < setupSecretListIndex);
  assert.ok(setupSecretActionsMatch);
  assert.match(setupSecretActionsMatch[1], /id="setupRegenerateSecretsButton"/);
  assert.match(setupSecretActionsMatch[1], /id="setupImportBackupButton"/);
  assert.doesNotMatch(setupSecretActionsMatch[1], /id="setupDownloadSecretsButton"/);
  assert.match(setupSource, /refs\.regenerateSetup\.addEventListener\('click', regenerateSetupSecrets\)/);
  assert.match(
    setupSource,
    /async function exportSecrets\(\)[\s\S]*?finally \{[\s\S]*?setBusy\(false\);[\s\S]*?renderBackupConfirm\(\);[\s\S]*?\n  \}/,
  );
  assert.match(setupSource, /refs\.downloadPermanent\.addEventListener\('click', saveEnvBackupNow\)/);
  assert.match(setupSource, /refs\.changeBackupLocation\.addEventListener\('click', changeEnvBackupLocation\)/);
  assert.match(setupSource, /refs\.disableAutomaticBackup\.addEventListener\('click', disableAutomaticEnvBackup\)/);
  assert.match(setupSource, /const confirmed = await showDialog\(\{/);
  assert.match(setupSource, /if \(!confirmed\) return;/);
  assert.match(launcherSource, /window\.omlorixShowLauncherDialog = showLauncherDialog/);
  assert.match(setupSource, /refs\.importPermanent\.addEventListener\('click', importSecrets\)/);
  assert.doesNotMatch(setupSource, /omlorix:request-env-import/);
  assert.doesNotMatch(setupSource, /state\.importArmedUntil|refs\.importPermanent\.textContent\s*=\s*t\('import_now'\)/);
  assert.doesNotMatch(setupSource, /managementMessage\.textContent = t\('backup_outdated_desc'\)/);
  assert.match(setupSource, /renderAutomaticBackupWarning\(data\.automaticEnvBackupError\)/);
});

test('secret-management failures use an explicit error presentation', async () => {
  const rendererRoot = path.join(__dirname, '..', 'renderer');
  const [setupSource, setupCss] = await Promise.all([
    fs.readFile(path.join(rendererRoot, 'setup-flow.js'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'setup-flow.css'), 'utf8'),
  ]);

  assert.match(setupSource, /function setManagementMessage\(message = '', isError = false\)/);
  assert.match(setupSource, /classList\.toggle\('is-error', Boolean\(message\) && isError\)/);
  assert.match(setupSource, /t\('import_failed',[\s\S]*?\), true\)/);
  assert.doesNotMatch(setupSource, /result\.restartError/);
  assert.match(setupCss, /#secretManagementMessage\.is-error:not\(:empty\)[\s\S]*?var\(--danger-bg\)/);
  assert.match(
    setupCss,
    /\.setup-backup-location:has\(\.setup-backup-confirm\[data-state="current"\]\)\s*\{[\s\S]*?background:\s*var\(--success-bg\);[\s\S]*?border-color:\s*var\(--success-border\);/,
  );
});

test('welcome setup starts Docker with a loading button and two-second readiness polling', async () => {
  const rendererRoot = path.join(__dirname, '..', 'renderer');
  const [setupSource, setupCss] = await Promise.all([
    fs.readFile(path.join(rendererRoot, 'setup-flow.js'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'setup-flow.css'), 'utf8'),
  ]);

  // The control is available only for an installed, stopped Docker Desktop.
  // Once activated it must expose its busy state, use one serial poll loop,
  // and always release both controls after the bounded startup window.
  assert.match(setupSource, /const SETUP_DOCKER_POLL_INTERVAL_MS = 2000;/);
  assert.match(setupSource, /const SETUP_DOCKER_START_TIMEOUT_MS = 120000;/);
  assert.match(setupSource, /docker\.installed && !docker\.running && docker\.canStartDesktop/);
  assert.match(setupSource, /startDocker\.disabled = state\.busy \|\| state\.dockerStartPolling\.active;/);
  assert.match(setupSource, /startDocker\.setAttribute\('aria-busy', 'true'\)/);
  assert.match(setupSource, /await window\.omlorixServer\.startDockerDesktop\(\)/);
  assert.match(setupSource, /window\.setTimeout\([\s\S]*?SETUP_DOCKER_POLL_INTERVAL_MS\)/);
  assert.match(setupSource, /deadline = Date\.now\(\) \+ SETUP_DOCKER_START_TIMEOUT_MS;/);
  assert.match(setupSource, /timeoutTimer = window\.setTimeout\([\s\S]*?stopSetupDockerPolling\(\)[\s\S]*?SETUP_DOCKER_START_TIMEOUT_MS/);
  assert.match(setupSource, /Date\.now\(\) >= state\.dockerStartPolling\.deadline[\s\S]*?stopSetupDockerPolling\(\)/);
  assert.match(setupSource, /state\.dockerStartPolling\.timeoutTimer = null;/);
  assert.match(setupSource, /generation !== state\.dockerStartPolling\.generation[\s\S]*?return;/);
  assert.match(setupCss, /\.setup-readiness-actions\s*\{[\s\S]*?--setup-readiness-action-height:\s*34px;/);
  assert.match(setupCss, /\.setup-readiness-actions > \.btn\s*\{[\s\S]*?height:\s*var\(--setup-readiness-action-height\);[\s\S]*?min-height:\s*var\(--setup-readiness-action-height\);/);
  assert.match(setupCss, /\.setup-readiness-refresh\s*\{[\s\S]*?width:\s*var\(--setup-readiness-action-height\);/);
  assert.match(setupCss, /\.setup-start-docker-spinner\s*\{[\s\S]*?animation:\s*spin 0\.8s linear infinite;/);
});

test('review launch action and completion step require Docker readiness', async () => {
  const setupSource = await fs.readFile(
    path.join(__dirname, '..', 'renderer', 'setup-flow.js'),
    'utf8',
  );

  // Exercise the same pure presentation calculation used by renderStep so the
  // ready and Docker-later branches cannot drift apart behind static markup.
  const presentationStart = setupSource.indexOf('  function setupStepPresentation');
  const presentationEnd = setupSource.indexOf('\n  /** Render the setup rail', presentationStart);
  assert.ok(presentationStart >= 0 && presentationEnd > presentationStart);
  const context = {
    STEP_LABEL_KEYS: ['welcome', 'type', 'data', 'access', 'secrets', 'review', 'done'],
  };
  vm.runInNewContext(
    `${setupSource.slice(presentationStart, presentationEnd)}\nthis.presentSetupStep = setupStepPresentation;`,
    context,
  );

  const dockerUnavailable = context.presentSetupStep(5, false);
  assert.deepEqual(Array.from(dockerUnavailable.visibleStepLabelKeys), ['welcome', 'type', 'data', 'access', 'secrets', 'review']);
  assert.equal(dockerUnavailable.currentStepNumber, 6);
  assert.equal(dockerUnavailable.totalSteps, 6);
  assert.equal(dockerUnavailable.progress, 100);
  assert.equal(dockerUnavailable.nextHidden, true);

  const dockerReady = context.presentSetupStep(5, true);
  assert.deepEqual(Array.from(dockerReady.visibleStepLabelKeys), ['welcome', 'type', 'data', 'access', 'secrets', 'review', 'done']);
  assert.equal(dockerReady.currentStepNumber, 6);
  assert.equal(dockerReady.totalSteps, 7);
  assert.equal(dockerReady.progress, 83);
  assert.equal(dockerReady.nextHidden, false);

  const completed = context.presentSetupStep(6, true);
  assert.equal(completed.currentStepNumber, 7);
  assert.equal(completed.totalSteps, 7);
  assert.equal(completed.progress, 100);
  assert.equal(completed.nextHidden, true);

  // Review remains a completed, dismissible configuration when Docker is not
  // ready, while stale callbacks are prevented from entering the done panel.
  assert.match(setupSource, /refs\.reviewDashboard\.hidden = state\.step !== 5;/);
  assert.doesNotMatch(setupSource, /state\.step === 5 \? \(dockerReady\(\) \? t\('start_server'\) : t\('save_setup'\)\)/);
  assert.match(setupSource, /if \(state\.step === 5 && !dockerReady\(\)\) \{[\s\S]*?renderStep\(\);[\s\S]*?return;/);
  assert.match(
    setupSource,
    /if \(hadCurrentState && wasDockerReady !== dockerReady\(\) && !refs\.overlay\.hidden\)[\s\S]*?renderStep\(\{ preserveViewport: true \}\);/,
  );
  assert.doesNotMatch(
    setupSource,
    /wasDockerReady !== dockerReady\(\) && state\.step >= 5/,
  );
  assert.match(setupSource, /if \(!dockerReady\(\) && state\.step === 6\) state\.step = 5;/);
  assert.doesNotMatch(setupSource, /refs\.doneTitle\.textContent = t\('saved_no_docker_title'\)/);
});

test('review continuously tracks Docker readiness in both directions', async () => {
  const setupSource = await fs.readFile(
    path.join(__dirname, '..', 'renderer', 'setup-flow.js'),
    'utf8',
  );

  // The normal launcher status timer intentionally polls only stack services;
  // Review therefore owns a focused full-state check while it is visible.
  assert.match(setupSource, /dockerReadinessMonitor:\s*\{[\s\S]*?inFlight:\s*false,[\s\S]*?timer:\s*null,/);
  assert.match(setupSource, /function shouldMonitorSetupDockerReadiness\(\)\s*\{[\s\S]*?state\.step >= 5 && !refs\.overlay\.hidden/);
  assert.match(setupSource, /function scheduleSetupDockerReadinessMonitor\(\)[\s\S]*?window\.setTimeout\([\s\S]*?SETUP_DOCKER_POLL_INTERVAL_MS/);
  assert.match(setupSource, /async function pollSetupDockerReadinessMonitor\(\)[\s\S]*?acceptState\(await window\.omlorixServer\.getState\(\)\)/);
  assert.match(setupSource, /if \(dockerReady\(\) && state\.current\)[\s\S]*?running:\s*false,[\s\S]*?compose:\s*false/);
  assert.match(setupSource, /if \(!document\.hidden\) refreshSetupDockerReadinessMonitor\(\)/);
  assert.match(setupSource, /function dismissSetup\(\)[\s\S]*?stopSetupDockerReadinessMonitor\(\)/);

  // Execute the production monitor functions with deterministic IPC and timer
  // doubles to prove both readiness edges, including fail-closed disconnects.
  const monitorStart = setupSource.indexOf('  function shouldMonitorSetupDockerReadiness');
  const monitorEnd = setupSource.indexOf('\n  /** Render the setup rail', monitorStart);
  assert.ok(monitorStart >= 0 && monitorEnd > monitorStart);

  const monitorState = {
    step: 5,
    current: { docker: { installed: true, running: false, compose: false } },
    dockerReadinessMonitor: { inFlight: false, timer: null },
  };
  const acceptedStates = [];
  const clearedTimers = [];
  let timeoutDelay = null;
  let nextState = { docker: { installed: true, running: true, compose: true } };
  let getStateError = null;
  const monitorContext = {
    SETUP_DOCKER_POLL_INTERVAL_MS: 2000,
    state: monitorState,
    refs: { overlay: { hidden: false } },
    dockerReady: () => Boolean(
      monitorState.current?.docker?.installed
      && monitorState.current?.docker?.running
      && monitorState.current?.docker?.compose
    ),
    acceptState: (data) => {
      monitorState.current = data;
      acceptedStates.push(data);
    },
    window: {
      clearTimeout: (timer) => clearedTimers.push(timer),
      setTimeout: (_callback, delay) => {
        timeoutDelay = delay;
        return 17;
      },
      omlorixServer: {
        getState: async () => {
          if (getStateError) throw getStateError;
          return nextState;
        },
      },
    },
  };
  vm.runInNewContext(
    `${setupSource.slice(monitorStart, monitorEnd)}\n`
      + 'this.pollMonitor = pollSetupDockerReadinessMonitor;\n'
      + 'this.stopMonitor = stopSetupDockerReadinessMonitor;',
    monitorContext,
  );

  await monitorContext.pollMonitor();
  assert.equal(monitorContext.dockerReady(), true);
  assert.equal(acceptedStates.length, 1);
  assert.equal(timeoutDelay, 2000);

  monitorContext.stopMonitor();
  assert.deepEqual(clearedTimers, [17]);
  getStateError = new Error('Docker connection lost');
  nextState = null;
  await monitorContext.pollMonitor();
  assert.equal(monitorContext.dockerReady(), false);
  assert.equal(acceptedStates.length, 2);
  assert.equal(acceptedStates[1].docker.running, false);
  assert.equal(acceptedStates[1].docker.compose, false);
});

test('external data services hide bundled URLs and expose accessible reveal controls', async () => {
  const rendererRoot = path.join(__dirname, '..', 'renderer');
  const [html, setupSource, setupCss] = await Promise.all([
    fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'setup-flow.js'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'setup-flow.css'), 'utf8'),
  ]);

  // Exercise the production URL classifier without booting the full Electron
  // document, which requires native IPC and every launcher panel.
  const helperStart = setupSource.indexOf('  function externalConnectionUrl');
  const helperEnd = setupSource.indexOf('\n  function dispatchLauncherState', helperStart);
  assert.ok(helperStart >= 0 && helperEnd > helperStart);
  const context = { URL };
  vm.runInNewContext(
    `${setupSource.slice(helperStart, helperEnd)}\nthis.externalConnectionUrlForTest = externalConnectionUrl;`,
    context,
  );
  const classify = context.externalConnectionUrlForTest;

  assert.equal(classify('redis://:generated@redis:6379/0', ['redis:', 'rediss:'], ['redis']), '');
  assert.equal(classify('redis://:secret@127.0.0.1:6379/0', ['redis:', 'rediss:'], ['redis']), '');
  assert.equal(
    classify('rediss://:secret@cache.example.com:6380/0', ['redis:', 'rediss:'], ['redis']),
    'rediss://:secret@cache.example.com:6380/0',
  );
  assert.equal(classify('postgresql://user:secret@postgres/omlorix', ['postgres:', 'postgresql:'], ['postgres']), '');
  assert.equal(
    classify('postgresql://user:secret@db.example.com/omlorix', ['postgres:', 'postgresql:'], ['postgres']),
    'postgresql://user:secret@db.example.com/omlorix',
  );
  assert.match(setupSource, /byId\('setupDatabaseUrl'\)\.value = externalConnectionUrl\([\s\S]*?env\.DATABASE_URL/);
  assert.match(setupSource, /byId\('setupRedisUrl'\)\.value = externalConnectionUrl\([\s\S]*?env\.REDIS_URL/);

  for (const [inputId, buttonId, showKey, hideKey] of [
    ['setupDatabaseUrl', 'setupDatabaseUrlRevealButton', 'show_postgres_url', 'hide_postgres_url'],
    ['setupRedisUrl', 'setupRedisUrlRevealButton', 'show_redis_url', 'hide_redis_url'],
  ]) {
    assert.match(html, new RegExp(`id="${inputId}"[^>]*type="password"[^>]*aria-describedby=`));
    assert.match(html, new RegExp(`id="${buttonId}"[^>]*data-setup-reveal-for="${inputId}"[^>]*data-show-aria-key="${showKey}"[^>]*data-hide-aria-key="${hideKey}"[^>]*aria-pressed="false"`));
    const revealMarkup = html.match(new RegExp(`<button[^>]*id="${buttonId}"[^>]*>`))?.[0] || '';
    assert.ok(revealMarkup, `${buttonId} should exist`);
    assert.match(revealMarkup, /class="secret-reveal-button"/);
    assert.doesNotMatch(revealMarkup, /data-setup-i18n(?:-aria-label)?=/);
  }
  assert.match(setupSource, /initializeSetupConnectionRevealButtons\(\);/);
  assert.match(setupCss, /\.setup-connection-input\s*\{[^}]*width:\s*100%;/);
  assert.doesNotMatch(setupCss, /\.setup-connection-input\s*\{[^}]*grid-template-columns:/);

  // Exercise the same reveal binding used by both static connection fields.
  const revealStart = setupSource.indexOf('  function setSetupInputRevealed');
  const revealEnd = setupSource.indexOf('\n  /** Bind static connection URL reveal controls', revealStart);
  const attributes = new Map();
  let clickHandler = null;
  let focused = false;
  const button = {
    dataset: { showAriaKey: 'show_redis_url', hideAriaKey: 'hide_redis_url' },
    textContent: '',
    setAttribute: (name, value) => attributes.set(name, value),
    getAttribute: (name) => attributes.get(name) ?? null,
    addEventListener: (name, handler) => { if (name === 'click') clickHandler = handler; },
  };
  const input = { id: 'setupRedisUrl', type: 'password', focus: () => { focused = true; } };
  const renderedIconStates = [];
  const revealContext = {
    t: (key) => ({ show: 'Show', hide: 'Hide', show_redis_url: 'Show Redis URL', hide_redis_url: 'Hide Redis URL' })[key],
    window: {
      OmlorixLauncherIcons: {
        setSecretRevealIcon: (_button, revealed) => renderedIconStates.push(revealed),
      },
    },
  };
  vm.runInNewContext(
    `${setupSource.slice(revealStart, revealEnd)}\nthis.bindSetupRevealButtonForTest = bindSetupRevealButton;`,
    revealContext,
  );
  revealContext.bindSetupRevealButtonForTest(button, input);
  assert.equal(attributes.get('aria-controls'), 'setupRedisUrl');
  assert.equal(attributes.get('aria-label'), 'Show Redis URL');
  assert.equal(attributes.get('aria-pressed'), 'false');
  assert.deepEqual(renderedIconStates, [false]);
  clickHandler();
  assert.equal(input.type, 'text');
  assert.equal(attributes.get('aria-label'), 'Hide Redis URL');
  assert.equal(attributes.get('aria-pressed'), 'true');
  assert.deepEqual(renderedIconStates, [false, true]);
  assert.equal(focused, true);
});

test('every launcher password field uses the shared inline eye control', async () => {
  const rendererRoot = path.join(__dirname, '..', 'renderer');
  const [html, launcherSource, setupSource] = await Promise.all([
    fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'setup-flow.js'), 'utf8'),
  ]);

  const passwordInputIds = [...html.matchAll(/<input\b[^>]*\btype="password"[^>]*>/g)]
    .map((match) => match[0].match(/\bid="([^"]+)"/)?.[1])
    .filter(Boolean);
  assert(passwordInputIds.length > 0, 'the launcher should retain password fields');

  for (const inputId of passwordInputIds) {
    const revealMarkup = html.match(new RegExp(
      `<button\\b[^>]*\\bclass="[^"]*secret-reveal-button[^"]*"[^>]*\\bdata-(?:secret-toggle|setup-reveal)-for="${inputId}"[^>]*>`,
    ))?.[0];
    assert.ok(revealMarkup, `${inputId} must have an inline eye reveal button`);
    assert.match(revealMarkup, /aria-pressed="false"/);
  }

  // Runtime-created Environment, storage-provider, setup, and permanent
  // secret fields must use the same component as static password inputs.
  assert.match(launcherSource, /secretWrap\.className = 'secret-input-wrap'/);
  assert.match(launcherSource, /revealButton\.className = 'secret-reveal-button'/);
  assert.match(setupSource, /secretWrap\.className = 'secret-input-wrap'/);
  assert.match(setupSource, /secretWrap\.className = 'secret-input-wrap setup-secret-input-wrap'/);
  assert.match(setupSource, /reveal\.className = 'secret-reveal-button'/);
  assert.match(html, /<script src="launcher-icons\.js"><\/script>/);
});

test('narrow macOS setup windows reserve the hidden titlebar above progress', async () => {
  const setupCss = await fs.readFile(
    path.join(__dirname, '..', 'renderer', 'setup-flow.css'),
    'utf8',
  );

  // The rail is hidden at this breakpoint, so its mobile replacement must
  // inherit the traffic-light inset without adding empty space in fullscreen.
  // It also replaces, rather than duplicates, the footer progress indicator.
  const responsiveStart = setupCss.indexOf('@media (max-width: 900px)');
  const responsiveEnd = setupCss.indexOf('@media (max-width: 620px)', responsiveStart);
  const responsiveCss = setupCss.slice(responsiveStart, responsiveEnd);
  assert.match(
    setupCss,
    /body:has\(\.setup-overlay:not\(\[hidden\]\)\)\s*\{[\s\S]*?overflow:\s*hidden;/,
  );
  assert.match(
    responsiveCss,
    /\.setup-footer\s*\{\s*justify-content:\s*flex-end;\s*\}/,
  );
  assert.match(
    responsiveCss,
    /\.setup-footer-progress\s*\{\s*display:\s*none;\s*\}/,
  );
  assert.match(
    responsiveCss,
    /html\[data-platform="darwin"\]\[data-window-mode="window"\] \.setup-mobile-progress\s*\{[\s\S]*?padding-top:\s*calc\(12px \+ var\(--desktop-mac-titlebar-height, 34px\)\)/,
  );
  assert.doesNotMatch(
    responsiveCss,
    /html\[data-platform="darwin"\]\[data-window-mode="fullscreen"\] \.setup-mobile-progress/,
  );
});

test('desktop setup rail shares the stage top and footer boundaries', async () => {
  const setupCss = await fs.readFile(
    path.join(__dirname, '..', 'renderer', 'setup-flow.css'),
    'utf8',
  );

  // Both columns must consume the same parent-owned measurements so changing
  // either the rail or stage cannot reintroduce the visible edge drift.
  assert.match(setupCss, /\.setup-shell\s*\{[\s\S]*?--setup-content-top:\s*72px;[\s\S]*?--setup-footer-block-size:\s*67px;/);
  assert.match(setupCss, /\.setup-rail\s*\{[\s\S]*?padding:\s*var\(--setup-content-top\) 20px 0;/);
  assert.match(setupCss, /\.setup-stage-body\s*\{[\s\S]*?padding:\s*var\(--setup-content-top\) 56px 56px;/);
  assert.match(setupCss, /\.setup-rail-note\s*\{[\s\S]*?min-height:\s*var\(--setup-footer-block-size\);[\s\S]*?margin:\s*20px -20px 0;[\s\S]*?padding:\s*18px 26px 0;/);
  assert.match(setupCss, /\.setup-footer\s*\{[\s\S]*?min-height:\s*var\(--setup-footer-block-size\);/);
});

test('review footer orders back, launcher dashboard, and primary action buttons', async () => {
  const launcherHtml = await fs.readFile(
    path.join(__dirname, '..', 'renderer', 'launcher.html'),
    'utf8',
  );
  const footerStart = launcherHtml.indexOf('<footer class="setup-footer">');
  const footerEnd = launcherHtml.indexOf('</footer>', footerStart);

  assert.ok(footerStart >= 0, 'setup footer should exist');
  assert.ok(footerEnd > footerStart, 'setup footer should have a closing tag');

  const footerMarkup = launcherHtml.slice(footerStart, footerEnd);
  assert.ok(
    footerMarkup.indexOf('id="setupBackButton"') < footerMarkup.indexOf('id="setupReviewDashboardButton"'),
    'Back should appear before Open launcher dashboard',
  );
  assert.ok(
    footerMarkup.indexOf('id="setupReviewDashboardButton"') < footerMarkup.indexOf('id="setupNextButton"'),
    'Open launcher dashboard should appear before the primary server action',
  );
});

test('input-adjacent actions and inline secret controls share launcher sizing', async () => {
  const rendererRoot = path.join(__dirname, '..', 'renderer');
  const [launcherCss, setupCss] = await Promise.all([
    fs.readFile(path.join(rendererRoot, 'launcher.css'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'setup-flow.css'), 'utf8'),
  ]);

  // File pickers align beside their inputs, while secret reveal controls sit
  // inside the field without consuming a third layout column.
  assert.match(launcherCss, /--control-height:\s*38px;/);
  assert.match(launcherCss, /\.path-picker > input,[\s\S]*?\.path-picker > \.btn\s*\{[\s\S]*?height:\s*var\(--control-height\);[\s\S]*?min-height:\s*var\(--control-height\);/);
  assert.match(launcherCss, /\.secret-input-wrap\s*\{[\s\S]*?position:\s*relative;/);
  assert.match(launcherCss, /\.secret-reveal-button\s*\{[\s\S]*?position:\s*absolute;/);
  assert.match(setupCss, /--setup-access-control-height:\s*42px;/);
  assert.match(setupCss, /\.setup-file-field > div > input,[\s\S]*?\.setup-file-field > div > \.btn\s*\{[\s\S]*?height:\s*var\(--setup-access-control-height\);[\s\S]*?min-height:\s*var\(--setup-access-control-height\);/);
  assert.match(setupCss, /\.setup-secret-row\s*\{[\s\S]*?grid-template-columns:\s*minmax\(170px, 1fr\) minmax\(180px, 1\.2fr\);/);
});

test('corrupt setup metadata fails closed instead of bypassing the backup gate', async (t) => {
  const configured = ENV_TEMPLATE
    .replace('JWT_SECRET_KEY=""', `JWT_SECRET_KEY="${'j'.repeat(64)}"`)
    .replace('ENCRYPTION_KEY=""', 'ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="')
    .replace('DATABASE_PASSWORD="CHANGE_ME"', 'DATABASE_PASSWORD="existing-database-password"')
    .replace('REDIS_PASSWORD="CHANGE_ME"', 'REDIS_PASSWORD="existing-redis-password"')
    .replace('redis://:CHANGE_ME@redis', 'redis://:existing-redis-password@redis');
  const { manager, root } = await createManager({ existingEnv: configured });
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.writeFile(manager.setupStateFile, '{not valid json', 'utf8');

  const setup = await manager.readSetupState(await manager.readEnv());
  assert.equal(setup.required, true);
  assert.equal(setup.backupCurrent, false);
});
