const test = require('node:test');
const assert = require('node:assert/strict');
const { execFile } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { promisify } = require('node:util');
const yaml = require('js-yaml');

const { CodeExecutionManager } = require('../code-execution-manager');
const {
  SERVER_FILES,
  ServerManager,
  composeArgs,
  envBackupFingerprint,
  normalizeLogOptions,
  normalizeStorageMigrationOptions,
} = require('../server-manager');
const { DEFAULT_SETTINGS: SCHEDULE_DEFAULTS, ScheduledUpdateManager } = require('../scheduled-updates');

const execFileAsync = promisify(execFile);
const repoRoot = path.resolve(__dirname, '..', '..');
let goAvailability;

/** Keep the ordinary desktop suite usable without the Go development toolchain. */
async function goAvailable() {
  if (!goAvailability) {
    goAvailability = execFileAsync('go', ['version'], { timeout: 5000 })
      .then(() => true, () => false);
  }
  return goAvailability;
}

async function requireGo(t) {
  if (await goAvailable()) return true;
  t.skip('Go is required for the Launcher/CLI parity integration checks.');
  return false;
}

/** Execute the real CLI entry point with an isolated server home. */
async function runCLI(home, args) {
  return execFileAsync('go', ['run', './cmd/omlorix-server-cli', ...args], {
    cwd: repoRoot,
    env: { ...process.env, OMLORIX_SERVER_HOME: home },
    timeout: 120000,
  });
}

test('Launcher and CLI expose reachable ordinary management commands', async (t) => {
  if (!(await requireGo(t))) return;
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-parity-'));
  t.after(() => fs.rm(home, { recursive: true, force: true }));

  const { stdout } = await runCLI(home, ['--help']);
  for (const command of [
    'start', 'stop', 'restart', 'update', 'services', 'service <start|stop|restart|logs>',
    'backup download <job-id> --output <path>', 'backup-options', 'backup-verify', 'restore', 'code-execution', 'auto-update',
    'update-channel [stable|beta]',
    'visitor-ip <status|detect|repair|verify>',
    'proxy <status|settings|configure|enable|disable|start|stop|restart|install-service|refresh-service|uninstall-service>',
    'storage probe', 'storage migrate --from-provider', 'storage migrate-local',
  ]) {
    assert.ok(stdout.includes(command), `CLI help does not expose ${command}`);
  }
  assert.match(stdout, /--attach-project <name>\s{2}Explicitly adopt/);
  assert.match(stdout, /logs \[--lines 220\] \[--follow\] \[--service <name>\] \[--since <time>\]/);

  for (const method of ['start', 'stop', 'restart', 'update', 'backup', 'getBackupJobs', 'getBackupDownloadInfo', 'downloadBackup', 'restore', 'verifyBackup', 'probeStorage', 'migrateStorage', 'serviceAction', 'logs', 'startLogFollow', 'stopLogFollow']) {
    assert.equal(typeof ServerManager.prototype[method], 'function', `Launcher method ${method} is unreachable`);
  }
  for (const method of ['exportSecretsBackup', 'saveAutomaticEnvBackup', 'disableAutomaticEnvBackup', 'importSecretsBackup']) {
    assert.equal(typeof ServerManager.prototype[method], 'function', `Launcher secrets method ${method} is unreachable`);
  }
  for (const method of ['repairVisitorIps', 'startProxy', 'stopProxy', 'restartProxy', 'installProxyService', 'uninstallProxyService']) {
    assert.equal(typeof ServerManager.prototype[method], 'function', `Launcher proxy method ${method} is unreachable`);
  }
  for (const method of ['availableVersions', 'create', 'save', 'checkUpdate', 'start', 'stop', 'restart', 'update', 'logs', 'connectionDetails', 'remove']) {
    assert.equal(typeof CodeExecutionManager.prototype[method], 'function', `Launcher Code Execution method ${method} is unreachable`);
  }
  assert.equal(typeof ScheduledUpdateManager.prototype.runNow, 'function');
  assert.equal(typeof ServerManager.prototype.readServerSettings, 'function');
  assert.equal(typeof ServerManager.prototype.writeServerSettings, 'function');
});

test('Launcher and CLI reject invalid log time bounds before Docker', async (t) => {
  if (!(await requireGo(t))) return;
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-log-bound-parity-'));
  t.after(() => fs.rm(home, { recursive: true, force: true }));

  const validBounds = [
    '5m', '+5m', '-5m', '.5h', '1.h', '1µs', '1μs',
    '1234567890.123456789', '9223372036854775807',
    '2026-08-23', '2026-08-23Z', '2026-08-23+02:00',
    '2026-08-23T10', '2026-08-23T10:30', '2026-08-23T10:30:00Z',
  ];
  for (const since of validBounds) {
    assert.doesNotThrow(() => normalizeLogOptions({ since }));
  }
  await runCLI(home, [
    ...validBounds.flatMap((since) => ['--since', since]),
    '--help',
  ]);
  const invalidBounds = [
    'last Tuesday', '1Μs', '5M', '9223372036854775808ns',
    '9223372036854775808', '9999999999999999999',
    '2026-08-23 10:30', '2026-08-23+02', '2026-08-23+0200',
    '2026-08-23T23:59:60Z',
  ];
  for (const since of invalidBounds) {
    assert.throws(
      () => normalizeLogOptions({ since }),
      /valid log time bound/i,
    );
  }
  await Promise.all(invalidBounds.map((since) => assert.rejects(
    runCLI(home, ['--since', since, 'logs']),
    /--since must be a valid log time bound such as 5m or 2026-08-23T10:30:00Z/i,
  )));
  assert.deepEqual(await fs.readdir(home), []);
});

test('fresh Launcher and CLI homes install the same complete shared asset tree', async (t) => {
  if (!(await requireGo(t))) return;
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-fresh-home-parity-'));
  const cliHome = path.join(root, 'cli-home');
  const launcherData = path.join(root, 'launcher-data');
  t.after(() => fs.rm(root, { recursive: true, force: true }));

  const manager = new ServerManager({
    app: {
      isPackaged: false,
      getPath: () => launcherData,
      getName: () => 'Omlorix Server Launcher',
      getVersion: () => 'test',
    },
    appRoot: repoRoot,
  });

  await Promise.all([
    manager.ensureServerHome(),
    runCLI(cliHome, ['--source-root', repoRoot, 'init']),
  ]);

  for (const relativePath of SERVER_FILES) {
    const [launcherAsset, cliAsset] = await Promise.all([
      fs.readFile(path.join(manager.serverHome, relativePath)),
      fs.readFile(path.join(cliHome, relativePath)),
    ]);
    assert.deepEqual(cliAsset, launcherAsset, `${relativePath} differs between fresh homes`);
  }

  for (const relativePath of [
    'otel/grafana/provisioning/dashboards/dashboards.yml',
    'otel/grafana/provisioning/datasources/datasources.yml',
  ]) {
    assert(SERVER_FILES.includes(relativePath), `${relativePath} is missing from the shared contract`);
  }
});

test('launcher-managed Compose services carry installation ownership labels', async () => {
  const composeFiles = [
    'docker-compose.server.yml',
    'docker-compose.managed-cloud.yml',
    'docker-compose.observability.yml',
    'docker-compose.observability-linux.yml',
  ];
  for (const relativePath of composeFiles) {
    const document = yaml.load(await fs.readFile(path.join(repoRoot, relativePath), 'utf8'));
    for (const [service, config] of Object.entries(document.services || {})) {
      assert.equal(
        config.labels?.['com.omlorix.installation.id'],
        '${OMLORIX_INSTALLATION_ID:-unmanaged}',
        `${relativePath}:${service} is missing its installation ownership label`,
      );
    }
  }
});

test('launcher-managed Compose volumes remain compatible with existing installations', async () => {
  const composeFiles = [
    'docker-compose.server.yml',
    'docker-compose.managed-cloud.yml',
    'docker-compose.observability.yml',
    'docker-compose.observability-linux.yml',
  ];
  for (const relativePath of composeFiles) {
    const document = yaml.load(await fs.readFile(path.join(repoRoot, relativePath), 'utf8'));
    for (const [volume, config] of Object.entries(document.volumes || {})) {
      assert.equal(
        config?.labels?.['com.omlorix.installation.id'],
        undefined,
        `${relativePath}:${volume} must not require destructive recreation to add an ownership label`,
      );
    }
  }
});

test('observability host metrics never mount the host root and remain Linux-only', async () => {
  const [commonRaw, linuxRaw, emptyTargetsRaw, linuxTargetsRaw] = await Promise.all([
    fs.readFile(path.join(repoRoot, 'docker-compose.observability.yml'), 'utf8'),
    fs.readFile(path.join(repoRoot, 'docker-compose.observability-linux.yml'), 'utf8'),
    fs.readFile(path.join(repoRoot, 'otel/prometheus-host-metrics-empty.yml'), 'utf8'),
    fs.readFile(path.join(repoRoot, 'otel/prometheus-host-metrics-linux.yml'), 'utf8'),
  ]);
  const common = yaml.load(commonRaw);
  const linux = yaml.load(linuxRaw);

  assert.equal(common.services['node-exporter'], undefined);
  assert.deepEqual(yaml.load(emptyTargetsRaw), []);
  assert.deepEqual(yaml.load(linuxTargetsRaw), [{ targets: ['node-exporter:9100'] }]);

  const nodeExporter = linux.services['node-exporter'];
  assert.deepEqual(nodeExporter.volumes, ['/proc:/host/proc:ro', '/sys:/host/sys:ro']);
  assert(nodeExporter.command.includes('--no-collector.filesystem'));
  assert.equal(nodeExporter.read_only, true);
  assert.deepEqual(nodeExporter.cap_drop, ['ALL']);
  assert(!linuxRaw.includes('/:'));
  assert(!linuxRaw.includes('rslave'));
});

test('Launcher and CLI contracts enforce shared defaults and security boundaries', async (t) => {
  if (!(await requireGo(t))) return;
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-parity-'));
  t.after(() => fs.rm(home, { recursive: true, force: true }));

  const compose = composeArgs(home, {
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
    REDIS_ENABLED: 'true',
    OMLORIX_USE_PGBOUNCER: 'false',
    OMLORIX_USE_BUNDLED_STORAGE: 'false',
  });
  assert.ok(compose.includes(path.join(home, 'docker-compose.server.yml')));
  assert.ok(compose.includes('bundled-db'));
  assert.ok(compose.includes('bundled-redis'));

  const codeExecution = new CodeExecutionManager({
    app: { isPackaged: true, getPath: () => home },
    appRoot: repoRoot,
    serverManager: {},
  });
  const defaults = codeExecution.normalizeSettings({ name: 'Primary', port: 8123, version: '1.2.3' });
  assert.equal(defaults.maxConcurrent, 10);
  assert.equal(defaults.sessionTimeout, 1200);
  assert.equal(defaults.memory, '512m');
  const instanceEnv = codeExecution.envFor({ id: 'primary', imageSource: 'release' }, defaults, 'secret');
  assert.equal(instanceEnv.ALLOW_SANDBOX_ENV_INJECTION, 'false');
  assert.equal(instanceEnv.REQUIRE_AUTH, 'true');
  assert.equal(instanceEnv.GATEWAY_HOST_BIND, '127.0.0.1');
  assert.equal(instanceEnv.GATEWAY_DOCKER_HOST, 'tcp://docker-proxy:2375');
  assert.equal(instanceEnv.REDIS_HEALTH_CHECK_INTERVAL, '30');
  assert.equal(instanceEnv.MAX_EXECUTIONS_PER_SESSION, '100');
  assert.equal(instanceEnv.RENDER_MAX_OUTPUT_BYTES, '220000000');

  const cliCodeExecutionSource = await fs.readFile(
    path.join(repoRoot, 'cmd/omlorix-server-cli/code_execution.go'),
    'utf8',
  );
  const connectionStart = cliCodeExecutionSource.indexOf('func connectionCodeExecution(');
  const connectionEnd = cliCodeExecutionSource.indexOf('\nfunc ', connectionStart + 1);
  assert.notEqual(connectionStart, -1, 'expected connectionCodeExecution');
  assert.notEqual(connectionEnd, -1, 'expected the end of connectionCodeExecution');
  const connectionSource = cliCodeExecutionSource.slice(connectionStart, connectionEnd);
  assert.match(connectionSource, /ensureLauncherServicesNetwork\(opts\)/);
  assert.match(connectionSource, /attachOmlorixBackendToHelperNetwork\(opts\)/);
  const connectionRepairSource = connectionSource.slice(
    connectionSource.indexOf('if err := ensureDockerReady(opts)'),
    connectionSource.indexOf('home, _ := codeExecutionInstanceHome'),
  );
  assert.doesNotMatch(connectionRepairSource, /return err/);
  assert.match(connectionRepairSource, /Warning: could not verify Docker readiness/);
  assert.match(connectionRepairSource, /Warning: could not ensure the helper services network/);
  assert.match(connectionRepairSource, /Warning: could not attach the Omlorix backend/);

  assert.equal(SCHEDULE_DEFAULTS.backupBeforeUpdate, true);
  assert.equal(SCHEDULE_DEFAULTS.backupDestinationId, '');
  assert.equal(SCHEDULE_DEFAULTS.backupEncryptionEnabled, true);
  assert.equal(SCHEDULE_DEFAULTS.onlyWhenHealthy, true);
  assert.notEqual(
    envBackupFingerprint({ A: '1', SECRET: 'first' }),
    envBackupFingerprint({ A: '1', SECRET: 'second' }),
  );

  await assert.rejects(
    runCLI(home, ['--max-concurrent', '0', 'code-execution', 'create', '--name', 'Invalid']),
    /--max-concurrent must be between 1 and 100/,
  );
  await assert.rejects(
    runCLI(home, ['--source-root', repoRoot, 'secrets', 'regenerate', 'ENCRYPTION_KEY']),
    /rotating ENCRYPTION_KEY requires --confirm ROTATE-ENCRYPTION-KEY/,
  );
});

test('Launcher and CLI synchronize automatic .env backup disable state', async (t) => {
  if (!(await requireGo(t))) return;
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-env-backup-parity-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const manager = new ServerManager({
    app: {
      isPackaged: false,
      getPath: () => root,
      getName: () => 'Omlorix',
      getVersion: () => 'test',
    },
    appRoot: repoRoot,
  });
  manager.dockerStatus = async () => ({ installed: false, running: false, compose: false });
  await manager.ensureServerHome();
  await manager.ensureGeneratedSecrets();
  const target = path.join(root, 'retained-recovery.env');

  await manager.exportSecretsBackup(target);
  let cliStatus = JSON.parse((await runCLI(
    manager.serverHome,
    ['--json', 'secrets', 'backup-status'],
  )).stdout);
  assert.equal(cliStatus.configured, true);
  assert.equal(cliStatus.current, true);

  await manager.disableAutomaticEnvBackup();
  cliStatus = JSON.parse((await runCLI(
    manager.serverHome,
    ['--json', 'secrets', 'backup-status'],
  )).stdout);
  assert.equal(cliStatus.configured, false);

  await runCLI(manager.serverHome, ['secrets', 'export', target]);
  assert.equal((await manager.getState()).setup.backupConfigured, true);
  const retainedRaw = await fs.readFile(target, 'utf8');

  await runCLI(manager.serverHome, ['secrets', 'disable-backup']);
  const launcherState = await manager.getState();
  assert.equal(launcherState.setup.backupConfigured, false);
  assert.equal(launcherState.setup.backupFilePath, '');
  assert.equal(await fs.readFile(target, 'utf8'), retainedRaw);
});

test('Launcher backup invokes the same guarded backend command contract', async (t) => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-parity-'));
  t.after(() => fs.rm(home, { recursive: true, force: true }));
  const manager = new ServerManager({
    app: {
      isPackaged: false,
      getPath: () => home,
      getName: () => 'Omlorix',
      getVersion: () => 'test',
    },
    appRoot: repoRoot,
  });
  manager.assertUpdatePrerequisites = async () => {};
  manager.prepareCompose = async () => ({ env: {}, args: ['docker', 'compose'] });
  manager.runOperation = async (name, command, operationOptions) => ({ name, command, operationOptions });

  const result = await manager.backup({ destinationId: 'local-backup' });
  assert.equal(result.name, 'Backup');
  assert.deepEqual(result.command.slice(0, 2), ['docker', 'compose']);
  assert.ok(result.command.join(' ').includes('python -m app.backups.cli create --safe-output'));
  assert.ok(result.command.includes('--destination'));
  assert.ok(!result.command.includes('--no-encrypted'));

  await assert.rejects(manager.serviceAction('delete', 'fastapi'), /supported service action/);
  await assert.rejects(manager.verifyBackup(path.join(home, 'not-a-backup.txt')), /ending in \.tar\.zst/);
});

test('Launcher storage management invokes the shared backend command contract', async (t) => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-storage-parity-'));
  t.after(() => fs.rm(home, { recursive: true, force: true }));
  const manager = new ServerManager({
    app: {
      isPackaged: false,
      getPath: () => home,
      getName: () => 'Omlorix',
      getVersion: () => 'test',
    },
    appRoot: repoRoot,
  });
  manager.assertUpdatePrerequisites = async () => {};
  manager.prepareCompose = async () => ({ env: {}, args: ['docker', 'compose'] });
  manager.runOperation = async (name, command, operationOptions) => ({ name, command, operationOptions });

  const probe = await manager.probeStorage();
  assert.equal(probe.name, 'Storage probe');
  assert.match(probe.command.join(' '), /python -m app\.files\.cli storage-probe/);
  const probeResult = await probe.operationOptions.resultBuilder({
    state: { ready: true },
    stdout: '{"provider":"webdav","probe":{"status":"ok","internal":"not-forwarded"}}',
  });
  assert.deepEqual(probeResult, {
    state: { ready: true },
    probe: { provider: 'webdav', status: 'ok' },
  });

  const migration = await manager.migrateStorage({
    fromProvider: 'local',
    toProvider: 'webdav',
    scope: 'presentations',
    dryRun: false,
    deleteSource: true,
    force: true,
    userId: 'user-1',
    onlyMigratedFrom: 's3',
    createdAfter: '2026-01-01',
    createdBefore: '2026-02-01',
    batchSize: 50,
    maxFiles: 10,
    retries: 4,
  });
  const command = migration.command.join(' ');
  assert.equal(migration.name, 'Storage migration');
  assert.match(command, /python -m app\.files\.cli migrate-files/);
  assert.match(command, /--from-provider local --to-provider webdav --scope presentations/);
  assert.match(command, /--user-id user-1 --only-migrated-from s3/);
  assert.match(command, /--delete-source --force$/);
  const migrationResult = await migration.operationOptions.resultBuilder({
    state: { ready: true },
    stdout: JSON.stringify({
      scanned: 3,
      migrated: 2,
      would_migrate: 0,
      resumed: 1,
      failed: 1,
      deleted_source: 2,
      source_cleanup_failed: 0,
      objects: 4,
      categories: { secret_internal_shape: true },
    }),
  });
  assert.deepEqual(migrationResult.migration, {
    source_provider: 'local',
    destination_provider: 'webdav',
    scope: 'presentations',
    dry_run: false,
    scanned: 3,
    would_migrate: 0,
    migrated: 2,
    resumed: 1,
    failed: 1,
    deleted_source: 2,
    source_cleanup_failed: 0,
    objects: 4,
  });

  assert.throws(
    () => normalizeStorageMigrationOptions({ fromProvider: 'local', toProvider: 'local' }),
    /must be different/,
  );
  assert.throws(
    () => normalizeStorageMigrationOptions({ fromProvider: 'local', toProvider: 's3', createdAfter: '2026-02-01', createdBefore: '2026-01-01' }),
    /must not be later/,
  );
});
