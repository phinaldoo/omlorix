const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const { ServerManager } = require('../server-manager');

async function createManager() {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-env-import-'));
  const manager = new ServerManager({
    app: {
      isPackaged: false,
      getPath: () => tempDir,
      getVersion: () => '0.0.0-test',
    },
    appRoot: tempDir,
  });

  // This test is only about the import path and parser accepting a real ".env"
  // filename. Generated launcher defaults would add unrelated keys to the
  // temporary server home and make the assertion less direct.
  manager.ensureGeneratedSecrets = async () => {};
  return { manager, tempDir };
}

test('env import preview accepts a hidden extensionless .env file', async () => {
  const { manager, tempDir } = await createManager();
  const sourceFile = path.join(tempDir, '.env');

  await fs.writeFile(sourceFile, 'OMLORIX_VERSION=stable\nFRONTEND_HTTP_HOST_PORT=8080\n', 'utf8');

  const preview = await manager.previewEnvImport(sourceFile);

  assert.equal(preview.sourceFile, sourceFile);
  assert.equal(preview.importedCount, 2);
  assert.deepEqual(preview.newKeys, ['OMLORIX_VERSION', 'FRONTEND_HTTP_HOST_PORT']);
});

test('env import preserves launcher-owned frontend proxy safety settings', async () => {
  const { manager, tempDir } = await createManager();
  const sourceFile = path.join(tempDir, 'import.env');
  await manager.updateServerSettings((current) => ({
    ...current,
    proxy: { ...current.proxy, enabled: true },
  }));
  await fs.writeFile(
    sourceFile,
    'FRONTEND_TRUST_PROXY_HEADERS=false\nFRONTEND_HTTP_HOST_BIND=0.0.0.0\n',
    'utf8',
  );
  manager.getState = async () => ({ ok: true });
  manager.getEnvEditor = async () => ({ fields: [] });
  manager.stackStatus = async () => ({ running: 0 });
  manager.proxyServiceStatus = async () => ({ running: false });

  const preview = await manager.previewEnvImport(sourceFile);
  const result = await manager.applyEnvImport(preview.importId);
  const env = await manager.readEnv();

  assert.equal(result.ok, true);
  assert.equal(env.FRONTEND_TRUST_PROXY_HEADERS, 'true');
  assert.equal(env.FRONTEND_HTTP_HOST_BIND, '127.0.0.1');
});

test('merge import ignores launcher-owned identity, proxy credentials, and trust settings', async () => {
  const { manager, tempDir } = await createManager();
  const sourceFile = path.join(tempDir, 'protected-merge.env');
  const trustedInstallation = 'trusted-installation';
  const trustedProxySecret = 'a'.repeat(64);
  await manager.writeEnv({
    OMLORIX_INSTALLATION_ID: trustedInstallation,
    OMLORIX_LAUNCHER_PROXY_SECRET: trustedProxySecret,
    OMLORIX_LAUNCHER_PROXY_ENABLED: 'false',
    FRONTEND_TRUSTED_UPSTREAMS: '',
    FRONTEND_TRUST_PROXY_HEADERS: 'false',
    FRONTEND_HTTP_HOST_PORT: '8080',
  });
  await fs.writeFile(
    sourceFile,
    [
      'OMLORIX_INSTALLATION_ID=foreign-installation',
      `OMLORIX_LAUNCHER_PROXY_SECRET=${'b'.repeat(64)}`,
      'FRONTEND_TRUSTED_UPSTREAMS=10.0.0.0/8',
      'FRONTEND_TRUST_PROXY_HEADERS=true',
      'FRONTEND_HTTP_HOST_PORT=9090',
      '',
    ].join('\n'),
    'utf8',
  );
  manager.stackStatus = async () => ({ running: 0 });
  manager.proxyServiceStatus = async () => ({ running: false });
  manager.getState = async () => ({ ok: true });
  manager.getEnvEditor = async () => ({ fields: [] });

  const preview = await manager.previewEnvImport(sourceFile);
  const result = await manager.applyEnvImport(preview.importId);
  const imported = await manager.readEnv();

  assert.equal(preview.importedCount, 1);
  assert.deepEqual(preview.changedKeys, ['FRONTEND_HTTP_HOST_PORT']);
  assert.equal(result.ok, true);
  assert.equal(imported.OMLORIX_INSTALLATION_ID, trustedInstallation);
  assert.equal(imported.OMLORIX_LAUNCHER_PROXY_SECRET, trustedProxySecret);
  assert.equal(imported.FRONTEND_TRUSTED_UPSTREAMS, '');
  assert.equal(imported.FRONTEND_TRUST_PROXY_HEADERS, 'false');
  assert.equal(imported.FRONTEND_HTTP_HOST_PORT, '9090');
});

test('merge import leaves a standalone running proxy untouched', async () => {
  const { manager, tempDir } = await createManager();
  const sourceFile = path.join(tempDir, 'standalone-proxy.env');
  await manager.writeEnv({
    OMLORIX_LAUNCHER_PROXY_ENABLED: 'true',
    OMLORIX_LAUNCHER_PROXY_SECRET: 'a'.repeat(64),
    FRONTEND_HTTP_HOST_PORT: '8080',
  });
  await fs.writeFile(sourceFile, 'FRONTEND_HTTP_HOST_PORT=9090\n', 'utf8');

  const runtimeEvents = [];
  manager.stackStatus = async () => ({ running: 0 });
  manager.proxyServiceStatus = async () => ({ running: true });
  manager.stopProxy = async () => { runtimeEvents.push('stop-proxy'); };
  manager.startProxy = async () => { runtimeEvents.push('start-proxy'); };
  manager.getState = async () => ({ stack: { running: 0 } });
  manager.getEnvEditor = async () => ({ fields: [] });

  const preview = await manager.previewEnvImport(sourceFile);
  const result = await manager.applyEnvImport(preview.importId);

  assert.equal(result.ok, true);
  assert.equal(result.restartRequired, true);
  assert.deepEqual(runtimeEvents, []);
  assert.equal((await manager.readEnv()).FRONTEND_HTTP_HOST_PORT, '9090');
});

test('reviewed env import commits directly without backup or runtime mutation', async () => {
  const { manager, tempDir } = await createManager();
  const sourceFile = path.join(tempDir, 'running-import.env');
  await manager.writeEnv({ OMLORIX_VERSION: '1.0.0' });
  await fs.writeFile(sourceFile, 'OMLORIX_VERSION=1.1.0\n', 'utf8');

  manager.stackStatus = async () => { throw new Error('import inspected runtime'); };
  manager.proxyServiceStatus = async () => { throw new Error('import inspected proxy'); };
  manager.runDockerStep = async () => { throw new Error('import mutated Docker'); };
  manager.stopProxy = async () => { throw new Error('import stopped proxy'); };
  manager.createEnvBackup = async () => { throw new Error('import created backup'); };
  manager.validateComposeOwnership = async () => { throw new Error('import inspected ownership'); };
  manager.getState = async () => ({ stack: { running: 2 } });
  manager.getEnvEditor = async () => ({ fields: [] });

  const preview = await manager.previewEnvImport(sourceFile);
  const result = await manager.applyEnvImport(preview.importId);

  assert.equal(result.changed, true);
  assert.equal(result.restartRequired, true);
  assert.equal(Object.prototype.hasOwnProperty.call(result, 'backupFile'), false);
  assert.equal((await manager.readEnv()).OMLORIX_VERSION, '1.1.0');
});

test('complete env import removes omitted custom values and preserves launcher-owned security values', async () => {
  const { manager, tempDir } = await createManager();
  await manager.ensureServerHome();
  const sourceFile = path.join(tempDir, 'complete-import.env');
  await fs.writeFile(
    manager.envFile,
    [
      'KNOWN_SETTING=current',
      'RESET_ME=non-default',
      'CUSTOM_OLD=remove-me',
      'OMLORIX_INSTALLATION_ID=protected-installation',
      '',
    ].join('\n'),
    'utf8',
  );
  await fs.writeFile(
    path.join(manager.serverHome, '.env.example'),
    'KNOWN_SETTING=default\nRESET_ME=default-value\nMISSING_KNOWN=default-value\n',
    'utf8',
  );
  await fs.writeFile(
    sourceFile,
    [
      'KNOWN_SETTING=imported',
      'CUSTOM_NEW=keep-me',
      `JWT_SECRET_KEY=${'j'.repeat(64)}`,
      `LOG_IP_HASH_SALT=${'l'.repeat(48)}`,
      'ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
      'DATABASE_URL=postgresql://omlorix:secret@db.example.internal:5432/omlorix',
      'REDIS_URL=redis://cache.example.internal:6379/0',
      'OMLORIX_INSTALLATION_ID=untrusted-import',
      '',
    ].join('\n'),
    'utf8',
  );
  manager.stackStatus = async () => ({ running: 0 });
  manager.proxyServiceStatus = async () => ({ running: false });
  manager.getState = async () => ({ ok: true });
  manager.getEnvEditor = async () => ({ fields: [] });

  const preview = await manager.previewEnvImport(sourceFile);

  assert.equal(preview.replaceMissing, false);
  assert.equal(preview.replacement.replaceMissing, true);
  assert.ok(preview.replacement.resetKnownKeys.includes('RESET_ME'));
  assert.ok(!preview.replacement.resetKnownKeys.includes('MISSING_KNOWN'));
  assert.ok(preview.replacement.removedCustomKeys.includes('CUSTOM_OLD'));
  assert.ok(!preview.replacement.removedCustomKeys.includes('OMLORIX_INSTALLATION_ID'));

  const result = await manager.applyEnvImport(preview.importId, { replaceMissing: true });
  const imported = await manager.readEnv();

  assert.equal(result.ok, true, JSON.stringify(result.preview?.replacement?.validationErrors || {}));
  assert.equal(result.replaceMissing, true);
  assert.equal(imported.KNOWN_SETTING, 'imported');
  assert.equal(imported.CUSTOM_NEW, 'keep-me');
  assert.equal(Object.prototype.hasOwnProperty.call(imported, 'CUSTOM_OLD'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(imported, 'MISSING_KNOWN'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(imported, 'RESET_ME'), false);
  assert.equal(imported.OMLORIX_INSTALLATION_ID, 'protected-installation');
});

test('complete replacement preview blocks a partial file that would reset required values', async () => {
  const { manager, tempDir } = await createManager();
  await manager.ensureServerHome();
  const sourceFile = path.join(tempDir, 'partial-import.env');
  await fs.writeFile(
    manager.envFile,
    [
      `JWT_SECRET_KEY=${'j'.repeat(64)}`,
      'ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
      'DATABASE_URL=postgresql://omlorix:secret@db.example.internal:5432/omlorix',
      'REDIS_URL=redis://cache.example.internal:6379/0',
      '',
    ].join('\n'),
    'utf8',
  );
  await fs.writeFile(
    path.join(manager.serverHome, '.env.example'),
    [
      'JWT_SECRET_KEY=""',
      'ENCRYPTION_KEY=""',
      'DATABASE_URL=""',
      'REDIS_URL=""',
      '',
    ].join('\n'),
    'utf8',
  );
  await fs.writeFile(sourceFile, 'CUSTOM_ONLY=value\n', 'utf8');

  const preview = await manager.previewEnvImport(sourceFile);

  assert.deepEqual(preview.validationErrors, {});
  assert.ok(Object.keys(preview.replacement.validationErrors).includes('JWT_SECRET_KEY'));
  assert.ok(Object.keys(preview.replacement.validationErrors).includes('ENCRYPTION_KEY'));
  assert.ok(preview.replacement.missingRequiredKeys.includes('DATABASE_URL'));
});
