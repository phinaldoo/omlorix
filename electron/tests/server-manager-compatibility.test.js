const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { PassThrough } = require('node:stream');

const {
  SERVER_FILES,
  ServerManager,
  composeArgs,
  dockerCommand,
  dockerRegistryAccessErrorMessage,
  dockerSpawnEnv,
  expectedServiceNamesFromToggles,
  highestServerVersion,
  mergeExpectedComposeServices,
  normalizeLogOptions,
  observabilityCapability,
  offlineApplicationServiceNames,
  parseComposeServiceNames,
  readEnvToggles,
  serverVersionFromImage,
  stackReadinessHealthy,
  trackableServerVersion,
  writeAtomicBackupDownload,
} = require('../server-manager');

const DEDICATED_WORKER_SERVICES = [
  'operations_worker',
  'generation_worker',
  'research_worker',
  'file_processing_worker',
  'account_lifecycle_worker',
  'maintenance_worker',
  'rendering_worker',
  'media_worker',
  'connector_worker',
  'audit_event_worker',
  'realtime_gateway',
];

/** Return whether an electron-builder resource filter includes a server file. */
function resourceFilterIncludesFile(filter, relativePath) {
  if (filter === relativePath) return true;
  if (!filter.endsWith('/**')) return false;
  return relativePath.startsWith(filter.slice(0, -2));
}

async function createManager(version = '1.2.2', options = {}) {
  const userData = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-launcher-test-'));
  return new ServerManager({
    app: {
      isPackaged: false,
      getPath: () => userData,
      getName: () => 'Omlorix Server Launcher',
      getVersion: () => version,
    },
    appRoot: options.appRoot || userData,
  });
}

async function withPlatform(platform, fn) {
  const descriptor = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', {
    configurable: true,
    value: platform,
  });
  try {
    return await fn();
  } finally {
    Object.defineProperty(process, 'platform', descriptor);
  }
}

function restoreEnvValue(key, value) {
  if (value === undefined) {
    delete process.env[key];
    return;
  }
  process.env[key] = value;
}

test('assertLauncherCompatible allows releases without a minimum launcher version', async () => {
  const manager = await createManager('1.2.2');

  assert.doesNotThrow(() => manager.assertLauncherCompatible({
    version: '1.2.3',
    manifest: { minimumLauncherVersion: '0.0.0' },
  }));
  assert.doesNotThrow(() => manager.assertLauncherCompatible({
    version: '1.2.3',
    manifest: null,
  }));
});

test('available-version outages are coalesced, cooled down, and recoverable', async () => {
  const failureFixtures = [
    () => new Error('GitHub releases returned HTTP 404.'),
    () => Object.assign(new Error('GitHub releases timed out.'), { code: 'ETIMEDOUT' }),
    () => Object.assign(new Error('GitHub releases are offline.'), { code: 'ENOTFOUND' }),
    () => Object.assign(new Error('GitHub releases failed.'), { statusCode: 503 }),
  ];

  for (const makeFailure of failureFixtures) {
    const manager = await createManager();
    let now = 1_000;
    let attempts = 0;
    let recover = false;
    manager.now = () => now;
    manager.ensureServerHome = async () => {};
    manager.readEnv = async () => ({});
    manager.readServerSettings = async () => ({ updateChannel: 'stable' });
    manager.fetchJson = async () => {
      attempts += 1;
      await new Promise((resolve) => setImmediate(resolve));
      if (!recover) throw makeFailure();
      return [{ tag_name: 'v1.2.3', prerelease: false }];
    };
    const passiveOptions = { maxAgeMs: 15 * 60 * 1000, failureMaxAgeMs: 60_000 };

    const startupBurst = await Promise.allSettled([
      manager.getAvailableVersions('stable', passiveOptions),
      manager.getAvailableVersions('stable', passiveOptions),
      manager.getAvailableVersions('stable', passiveOptions),
      manager.getAvailableVersions('stable', passiveOptions),
    ]);
    assert(startupBurst.every((result) => result.status === 'rejected'));
    assert.equal(attempts, 1);

    await assert.rejects(() => manager.getAvailableVersions('stable', passiveOptions));
    assert.equal(attempts, 1, 'the passive cooldown must not contact GitHub');

    now += 60_001;
    await assert.rejects(() => manager.getAvailableVersions('stable', passiveOptions));
    assert.equal(attempts, 2, 'GitHub must be retried after the cooldown');

    recover = true;
    const recovered = await manager.getAvailableVersions('stable', {
      ...passiveOptions,
      force: true,
    });
    assert.equal(attempts, 3, 'an explicit refresh must bypass a fresh passive failure');
    assert.deepEqual(recovered.versions.map((version) => version.value), ['1.2.3']);

    const cached = await manager.getAvailableVersions('stable', passiveOptions);
    assert.strictEqual(cached, recovered);
    assert.equal(attempts, 3, 'the successful result must cover later startup consumers');
  }
});

test('service actions accept only configured long-running Compose services', async () => {
  const manager = await createManager();
  const calls = [];
  manager.readEnv = async () => ({});
  manager.execDocker = async (args) => {
    calls.push(args);
    return { ok: true, stdout: 'fastapi\nfrontend\nmigrate\n', stderr: '' };
  };
  manager.runOperation = async (name, args) => ({ name, args });

  const result = await manager.serviceAction('restart', 'fastapi');
  assert.equal(result.name, 'restart fastapi');
  assert.deepEqual(result.args.slice(-2), ['restart', 'fastapi']);
  await assert.rejects(() => manager.serviceAction('stop', 'migrate'), /long-running service/);
  await assert.rejects(() => manager.serviceAction('shell', 'fastapi'), /supported service action/);
  assert(calls.some((args) => args.slice(-2).join(' ') === 'config --services'));
});

test('service logs share validated line and time bounds with aggregate logs', async () => {
  const manager = await createManager();
  const calls = [];
  manager.readEnv = async () => ({});
  manager.execDocker = async (args) => {
    calls.push(args);
    if (args.includes('config')) return { ok: true, stdout: 'fastapi\n', stderr: '' };
    return { ok: true, stdout: 'service output\n', stderr: '' };
  };

  assert.equal(
    await manager.serviceAction('logs', 'fastapi', { lines: 99999, since: '5m' }),
    'service output\n',
  );
  assert.deepEqual(
    calls.at(-1).slice(-7),
    ['logs', '--tail', '5000', '--no-color', '--since', '5m', 'fastapi'],
  );
  assert.deepEqual(normalizeLogOptions({ lines: 7, since: '5m', follow: true }), {
    lines: 7,
    follow: true,
    since: '5m',
    service: '',
  });
  manager.startLogFollow = async (options) => options;
  assert.deepEqual(await manager.logs({ lines: 7, since: '5m', follow: true }), {
    lines: 7,
    follow: true,
    since: '5m',
    service: '',
  });
  assert.equal(normalizeLogOptions({ since: '2026-08-23T10' }).since, '2026-08-23T10');
  assert.equal(normalizeLogOptions({ since: '2026-08-23+02:00' }).since, '2026-08-23+02:00');
  await assert.rejects(
    () => manager.logs({ lines: 7, since: 'last Tuesday' }),
    /valid log time bound/i,
  );
});

test('live logs forward bounded options, redact split secrets, and stop explicitly', async () => {
  const manager = await createManager();
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  const killSignals = [];
  child.kill = (signal) => {
    killSignals.push(signal || 'SIGTERM');
    queueMicrotask(() => child.emit('close', null, signal || 'SIGTERM'));
    return true;
  };
  let spawnedArgs = null;
  manager.prepareCompose = async (options) => {
    assert.equal(options.readOnly, true);
    return {
      env: { JWT_SECRET_KEY: 'split-secret-value' },
      args: ['compose', '--env-file', manager.envFile],
    };
  };
  manager.execDocker = async (args) => {
    assert.deepEqual(args.slice(-2), ['config', '--services']);
    return { ok: true, stdout: 'fastapi\n', stderr: '' };
  };
  manager.spawnLogProcess = (executable, args, options) => {
    spawnedArgs = args;
    assert.equal(options.cwd, manager.serverHome);
    queueMicrotask(() => child.emit('spawn'));
    return child;
  };
  const output = [];
  const ended = [];
  manager.on('log-follow-output', (payload) => output.push(payload));
  manager.on('log-follow-end', (payload) => ended.push(payload));

  const session = await manager.startLogFollow({
    lines: 7,
    since: '5m',
    service: 'fastapi',
  });
  child.stdout.write('before split-');
  child.stdout.write('secret-value after\rprogress');
  const stopped = await manager.stopLogFollow(session.sessionId);

  assert.deepEqual(spawnedArgs.slice(-8), [
    'logs', '--tail', '7', '--no-color', '--follow', '--since', '5m', 'fastapi',
  ]);
  assert.deepEqual(killSignals, ['SIGTERM']);
  assert.equal(output.map((payload) => payload.text).join(''), 'before [REDACTED] after\rprogress');
  assert.equal(output.some((payload) => payload.text.includes('split-secret-value')), false);
  assert.equal(stopped.stopped, true);
  assert.equal(ended.length, 1);
  assert.equal(ended[0].sessionId, session.sessionId);
});

test('failed one-shot Compose service logs are emitted before the operation error', async () => {
  const manager = await createManager();
  const calls = [];
  const outputEvents = [];
  manager.on('operation-output', (payload) => outputEvents.push(payload));
  manager.execDocker = async (args) => {
    calls.push(args);
    if (args.includes('ps')) {
      return {
        ok: true,
        stdout: `${JSON.stringify({ Service: 'migrate', State: 'exited', ExitCode: 1 })}\n`,
        stderr: '',
      };
    }
    return {
      ok: true,
      stdout: 'migrate-1 | FATAL: password authentication failed for user "postgres"\n',
      stderr: '',
    };
  };

  await manager.emitFailedComposeServiceLogs(
    'Start',
    ['compose', '--env-file', manager.envFile],
    ['migrate'],
  );

  assert.deepEqual(
    calls.map((args) => args.slice(-5)),
    [
      ['ps', '--all', '--format', 'json', 'migrate'],
      ['logs', '--tail', '120', '--no-color', 'migrate'],
    ],
  );
  assert.equal(outputEvents.length, 1);
  assert.equal(outputEvents[0].name, 'Start');
  assert.equal(outputEvents[0].stream, 'stderr');
  assert.equal(outputEvents[0].textKey, 'launcher_ui_logs_value1');
  assert.match(outputEvents[0].textValues.value1, /password authentication failed/);
});

test('Compose ownership requires explicit adoption for unlabeled legacy containers', async () => {
  const manager = await createManager();
  const env = {
    COMPOSE_PROJECT_NAME: 'omlorix',
    OMLORIX_INSTALLATION_ID: 'new-installation-id',
  };
  let installationLabel = 'unmanaged';
  const writes = [];
  manager.execDocker = async (args) => {
    if (args[0] === 'ps') return { ok: true, stdout: 'legacy-container\n', stderr: '' };
    if (args.some((arg) => arg.includes('com.omlorix.installation.id'))) {
      return { ok: true, stdout: `${installationLabel}\n`, stderr: '' };
    }
    return { ok: false, stdout: '', stderr: 'unexpected Docker call' };
  };
  manager.readEnv = async () => env;
  manager.writeEnv = async (updates) => {
    Object.assign(env, updates);
    writes.push(updates);
  };

  await assert.rejects(
    () => manager.validateComposeOwnership(env),
    (error) => error.code === 'LEGACY_COMPOSE_ADOPTION_REQUIRED' && error.project === 'omlorix',
  );

  await manager.adoptLegacyComposeProject('omlorix');
  assert.deepEqual(writes, [{ OMLORIX_ALLOW_PROJECT_ADOPTION: 'true' }]);
  await assert.doesNotReject(() => manager.validateComposeOwnership(env));

  installationLabel = 'another-installation-id';
  await assert.rejects(
    () => manager.validateComposeOwnership(env),
    (error) => error.messageKey === 'launcher_ui_compose_ownership_mismatch',
  );
});

test('Compose adoption refuses a project containing an owned container', async () => {
  const manager = await createManager();
  manager.readEnv = async () => ({
    COMPOSE_PROJECT_NAME: 'omlorix',
    OMLORIX_INSTALLATION_ID: 'new-installation-id',
  });
  manager.execDocker = async (args) => (
    args[0] === 'ps'
      ? { ok: true, stdout: 'container-one\ncontainer-two\n', stderr: '' }
      : { ok: true, stdout: args.at(-1) === 'container-one' ? 'unmanaged\n' : 'another-installation\n', stderr: '' }
  );
  manager.writeEnv = async () => {
    throw new Error('adoption must not be armed');
  };

  await assert.rejects(
    () => manager.adoptLegacyComposeProject('omlorix'),
    /owned by another Omlorix server home/,
  );
});

test('backup verification mounts the selected archive read-only', async () => {
  const manager = await createManager();
  const archive = path.join(os.tmpdir(), `omlorix-verify-${Date.now()}.tar.zst`);
  await fs.writeFile(archive, 'test archive');
  manager.readEnv = async () => ({});
  manager.runOperation = async (name, args, options) => ({ name, args, options });
  try {
    const result = await manager.verifyBackup(archive);
    assert.equal(result.name, 'Verify backup');
    assert(result.args.includes(`${archive}:/verify/input:ro`));
    assert.deepEqual(result.args.slice(-4), ['app.backups.cli', 'verify', '--source', 'file:///verify/input']);
    assert.equal(result.options.successMessageKey, 'launcher_ui_backup_verify_finished');
  } finally {
    await fs.unlink(archive);
  }
});

test('backup download commit is atomic, private, and collision-safe', async (t) => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-download-test-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const target = path.join(directory, 'omlorix-backup-job-1.tar.zst.enc');
  const archive = Buffer.from('complete encrypted archive');

  const completed = await writeAtomicBackupDownload(target, async (handle) => {
    await handle.write(archive);
  });
  assert.equal(completed.bytes, archive.length);
  assert.deepEqual(await fs.readFile(target), archive);
  if (process.platform !== 'win32') {
    assert.equal((await fs.stat(target)).mode & 0o777, 0o600);
  }

  await assert.rejects(
    writeAtomicBackupDownload(target, async () => {
      assert.fail('an existing destination must be rejected before streaming');
    }),
    (error) => error.code === 'BACKUP_DESTINATION_EXISTS',
  );
  assert.deepEqual(await fs.readFile(target), archive);
});

test('interrupted backup download leaves no destination or partial file', async (t) => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-download-test-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const target = path.join(directory, 'omlorix-backup-job-2.tar.zst');

  await assert.rejects(
    writeAtomicBackupDownload(target, async (handle) => {
      await handle.write(Buffer.from('partial archive'));
      throw new Error('interrupted');
    }),
    (error) => error.code === 'BACKUP_DESTINATION_UNAVAILABLE',
  );
  await assert.rejects(fs.lstat(target), (error) => error.code === 'ENOENT');
  assert.deepEqual(await fs.readdir(directory), []);
});

test('backup catalog and download metadata expose only safe fields', async () => {
  const manager = await createManager();
  const calls = [];
  manager.prepareCompose = async () => ({ args: ['compose'] });
  manager.execDocker = async (args) => {
    calls.push(args);
    if (args.includes('--metadata')) {
      return {
        ok: true,
        stdout: JSON.stringify({
          job_id: 'job-1',
          filename: 'omlorix-backup-job-1.tar.zst.enc',
          bytes: 123,
          storage_uri: 's3://secret-bucket/private',
        }),
        stderr: '',
      };
    }
    return {
      ok: true,
      stdout: JSON.stringify({
        items: [{
          id: 'job-1',
          status: 'success',
          created_at: '2026-08-23T10:00:00Z',
          finished_at: '2026-08-23T10:01:00Z',
          size_bytes: 123,
          options: { encryption_enabled: true, secret: 'not-forwarded' },
          artifacts: [{ id: 'artifact-1', storage: { scheme: 's3' } }],
          storage_uri: 's3://secret-bucket/private',
        }],
      }),
      stderr: '',
    };
  };

  assert.deepEqual(await manager.getBackupJobs(), [{
    id: 'job-1',
    status: 'success',
    created_at: '2026-08-23T10:00:00Z',
    finished_at: '2026-08-23T10:01:00Z',
    size_bytes: 123,
    encryption_enabled: true,
    has_artifact: true,
  }]);
  assert.deepEqual(await manager.getBackupDownloadInfo('job-1'), {
    jobId: 'job-1',
    filename: 'omlorix-backup-job-1.tar.zst.enc',
    bytes: 123,
  });
  assert(calls.some((args) => args.slice(-2).join(' ') === 'job-1 --metadata'));
});

test('assertLauncherCompatible blocks releases requiring a newer launcher', async () => {
  const manager = await createManager('1.2.2');

  assert.throws(
    () => manager.assertLauncherCompatible({
      version: '1.2.3',
      manifest: {
        minimumLauncherVersion: '1.2.3',
        launcherUpdateReason: 'This release adds new environment settings.',
      },
    }),
    (error) => {
      assert.equal(error.code, 'LAUNCHER_UPDATE_REQUIRED');
      assert.equal(error.currentLauncherVersion, '1.2.2');
      assert.equal(error.minimumLauncherVersion, '1.2.3');
      assert.equal(error.targetVersion, '1.2.3');
      assert.equal(error.releaseNotes, 'This release adds new environment settings.');
      return true;
    },
  );
});

test('highestServerVersion tracks concrete releases monotonically', () => {
  assert.equal(trackableServerVersion('v1.2.3'), '1.2.3');
  assert.equal(trackableServerVersion('stable'), '');
  assert.equal(highestServerVersion('', '1.2.3-beta.2'), '1.2.3-beta.2');
  assert.equal(highestServerVersion('1.2.3-beta.2', '1.2.3'), '1.2.3');
  assert.equal(highestServerVersion('1.2.3', '1.1.9'), '1.2.3');
  assert.equal(highestServerVersion('1.2.3', 'stable'), '1.2.3');
});

test('serverVersionFromImage reads only concrete running image tags', () => {
  assert.equal(serverVersionFromImage('ghcr.io/phinaldoo/omlorix-backend:1.4.2'), '1.4.2');
  assert.equal(serverVersionFromImage('localhost:5000/omlorix-backend:v1.4.2@sha256:abc'), '1.4.2');
  assert.equal(serverVersionFromImage('ghcr.io/phinaldoo/omlorix-backend:stable'), '');
  assert.equal(serverVersionFromImage('ghcr.io/phinaldoo/omlorix-backend@sha256:abc'), '');
});

test('recordSuccessfulServerVersion only raises launcher version metadata', async () => {
  const manager = await createManager();
  await manager.writeLauncherMetadata({
    highestSuccessfulServerVersion: '1.2.2',
  });

  assert.equal(await manager.recordSuccessfulServerVersion('1.2.3'), '1.2.3');
  assert.equal(
    (await manager.readLauncherMetadata()).highestSuccessfulServerVersion,
    '1.2.3',
  );
  assert.equal(await manager.recordSuccessfulServerVersion('1.1.0'), '1.2.3');
  assert.equal(
    (await manager.readLauncherMetadata()).highestSuccessfulServerVersion,
    '1.2.3',
  );

  await Promise.all([
    manager.recordSuccessfulServerVersion('1.4.0'),
    manager.recordSuccessfulServerVersion('1.6.0'),
    manager.recordSuccessfulServerVersion('1.5.0'),
  ]);
  assert.equal(
    (await manager.readLauncherMetadata()).highestSuccessfulServerVersion,
    '1.6.0',
  );
});

test('launcher version history is absent from environment configuration', async () => {
  const repoRoot = path.resolve(__dirname, '..', '..');
  const envExample = await fs.readFile(path.join(repoRoot, '.env.example'), 'utf8');
  const envMetadata = JSON.parse(
    await fs.readFile(path.join(repoRoot, 'electron', 'env-metadata.json'), 'utf8'),
  );

  assert.doesNotMatch(envExample, /OMLORIX_HIGHEST_VERSION_USED/);
  assert.equal(envMetadata.fields.OMLORIX_HIGHEST_VERSION_USED, undefined);
});

test('recordRunningServerVersion records only an already-healthy server', async () => {
  const manager = await createManager();
  const recorded = [];
  manager.recordSuccessfulServerVersion = async (version) => {
    recorded.push(version);
    return version;
  };

  manager.getState = async () => ({
    stack: { healthy: false },
  });
  manager.getComposeServiceImage = async () => {
    throw new Error('unhealthy servers must not be inspected');
  };
  assert.equal(await manager.recordRunningServerVersion(), '');
  assert.deepEqual(recorded, []);

  manager.getState = async () => ({
    stack: { healthy: true },
  });
  manager.getComposeServiceImage = async () => 'ghcr.io/phinaldoo/omlorix-backend:1.4.0';
  assert.equal(await manager.recordRunningServerVersion(), '1.4.0');
  assert.deepEqual(recorded, ['1.4.0']);

  manager.getComposeServiceImage = async () => 'ghcr.io/phinaldoo/omlorix-backend:stable';
  assert.equal(await manager.recordRunningServerVersion(), '');
  assert.deepEqual(recorded, ['1.4.0']);
});

test('getBackupOptions loads only safe display data through the backend CLI', async () => {
  const manager = await createManager();
  let receivedArgs = [];
  manager.readEnv = async () => ({});
  manager.execDocker = async (args) => {
    receivedArgs = args;
    return {
      ok: true,
      stderr: '',
      stdout: JSON.stringify({
        destinations: [
          { id: 'destination-1', name: 'Primary S3', provider: 's3' },
        ],
        capabilities: {
          archive_encryption_available: true,
          archive_encryption_default_enabled: true,
          plaintext_archives_allowed: false,
          ignored_secret: 'must-not-be-forwarded',
        },
      }),
    };
  };

  const options = await manager.getBackupOptions();

  assert.deepEqual(receivedArgs.slice(-5), [
    'fastapi',
    'python',
    '-m',
    'app.backups.cli',
    'options',
  ]);
  assert.deepEqual(options.destinations, [
    { id: 'destination-1', name: 'Primary S3', provider: 's3' },
  ]);
  assert.deepEqual(options.capabilities, {
    archive_encryption_available: true,
    archive_encryption_default_enabled: true,
    plaintext_archives_allowed: false,
  });
  assert.equal(JSON.stringify(options).includes('must-not-be-forwarded'), false);
});

test('backup forwards destination and encryption choice to the shared CLI', async () => {
  const manager = await createManager();
  let receivedArgs = [];
  manager.assertUpdatePrerequisites = async () => {};
  manager.readEnv = async () => ({});
  manager.runOperation = async (name, args, options) => {
    receivedArgs = args;
    assert.equal(name, 'Backup');
    return options.resultBuilder({
      state: { stack: { healthy: true } },
      stdout: JSON.stringify({
        job_id: 'backup-1',
        status: 'success',
        destination_id: 'destination-1',
        encryption_enabled: false,
        size_bytes: 1024,
        artifacts: ['s3://private-bucket/internal/path.tar.zst'],
        unexpected_secret: 'must-not-be-forwarded',
      }),
      stderr: '',
    });
  };

  const result = await manager.backup({
    destinationId: 'destination-1',
    encryptionEnabled: false,
  });

  assert.deepEqual(receivedArgs.slice(-5), [
    'create',
    '--safe-output',
    '--destination',
    'destination-1',
    '--no-encrypted',
  ]);
  assert.equal(result.backup.job_id, 'backup-1');
  assert.deepEqual(result.backup, {
    job_id: 'backup-1',
    status: 'success',
    destination_id: 'destination-1',
    encryption_enabled: false,
    size_bytes: 1024,
  });
  assert.equal(JSON.stringify(result).includes('private-bucket'), false);
  assert.equal(JSON.stringify(result).includes('must-not-be-forwarded'), false);
  assert.equal(result.state.stack.healthy, true);
});

test('backup is rejected before Docker execution when Omlorix is stopped', async () => {
  const manager = await createManager();
  let operationStarted = false;
  manager.getState = async () => ({
    stack: {
      running: 0,
      services: [],
    },
  });
  manager.runOperation = async () => {
    operationStarted = true;
  };

  await assert.rejects(
    () => manager.backup(),
    /Omlorix must be running before you can create a backup/i,
  );
  assert.equal(operationStarted, false);
});

test('restore fencing stops active app, orphan, and one-off containers by ID', async () => {
  const manager = await createManager();
  const commands = [];
  manager.execDocker = async (args) => {
    commands.push(args);
    if (args.includes('ps')) {
      return {
        ok: true,
        stdout: [
          JSON.stringify({
            ID: 'aaaaaaaaaaaa',
            Service: 'postgres',
            State: 'running',
            Labels: 'com.docker.compose.oneoff=False',
          }),
          JSON.stringify({ ID: 'bbbbbbbbbbbb', Service: 'fastapi', State: 'running' }),
          JSON.stringify({ ID: 'cccccccccccc', Service: 'removed_worker', State: 'restarting' }),
          JSON.stringify({ ID: 'dddddddddddd', Service: 'frontend', State: 'exited' }),
          JSON.stringify({
            ID: 'eeeeeeeeeeee',
            Service: 'postgres',
            State: 'running',
            Labels: { 'com.docker.compose.oneoff': 'True' },
          }),
        ].join('\n'),
        stderr: '',
      };
    }
    return { ok: true, stdout: '', stderr: '' };
  };

  await manager.stopRemainingRestoreApplicationContainers(['compose']);

  assert.deepEqual(commands[0], [
    'compose', 'ps', '--all', '--orphans', '--format', 'json',
  ]);
  assert.deepEqual(commands[1], [
    'stop', '--time', '60', 'bbbbbbbbbbbb', 'cccccccccccc', 'eeeeeeeeeeee',
  ]);
});

test('restore fencing fails closed on a malformed infrastructure one-off label', async () => {
  const manager = await createManager();
  manager.execDocker = async () => ({
    ok: true,
    stdout: JSON.stringify({
      ID: 'aaaaaaaaaaaa',
      Service: 'postgres',
      State: 'running',
      Labels: 'com.docker.compose.oneoff=maybe',
    }),
    stderr: '',
  });

  await assert.rejects(
    () => manager.stopRemainingRestoreApplicationContainers(['compose']),
    /invalid one-off label/i,
  );
});

test('restore stops app services and runs the offline restore container before restart', async () => {
  const manager = await createManager();
  const archivePath = path.join(manager.serverHome, 'selected backup.tar.zst.enc');
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(archivePath, 'backup');
  const steps = [];
  const recordedVersions = [];

  manager.readEnv = async () => ({ OMLORIX_VERSION: '1.2.3' });
  manager.stopRemainingRestoreApplicationContainers = async () => {};
  manager.runDockerStep = async (label, args, timeoutMs, operationName, textKey) => {
    steps.push({ label, args, timeoutMs, operationName, textKey });
    return { ok: true, stdout: '', stderr: '' };
  };
  manager.waitForReady = async () => 'http://localhost:8080';
  manager.recordSuccessfulServerVersion = async (version) => recordedVersions.push(version);
  manager.getState = async () => ({ stack: { healthy: true } });

  const state = await manager.restore(archivePath);

  assert.equal(state.stack.healthy, true);
  assert.equal(steps.length, 3);
  assert.deepEqual(steps[0].args.slice(steps[0].args.lastIndexOf('stop')), [
    'stop',
    'frontend',
    'email_worker',
    ...DEDICATED_WORKER_SERVICES,
    'automation_scheduler',
    'automation_worker',
    'fastapi',
  ]);
  const restoreArgs = steps[1].args;
  assert(restoreArgs.includes('run'));
  assert(restoreArgs.includes('--rm'));
  assert(restoreArgs.includes('--no-deps'));
  assert(restoreArgs.includes('--remove-orphans'));
  assert(restoreArgs.includes(`${archivePath}:/restore/input:ro`));
  assert.deepEqual(restoreArgs.slice(-8), [
    'restore',
    '--source',
    'file:///restore/input',
    '--target',
    'in_place',
    '--confirm',
    'RESTORE-IN-PLACE',
    '--offline',
  ]);
  assert.deepEqual(steps[2].args.slice(steps[2].args.lastIndexOf('up')), [
    'up',
    '-d',
    '--no-deps',
    '--force-recreate',
    '--remove-orphans',
    'frontend',
    'email_worker',
    ...DEDICATED_WORKER_SERVICES,
    'automation_scheduler',
    'automation_worker',
    'fastapi',
  ]);
  assert.deepEqual(recordedVersions, ['1.2.3']);
  assert.equal(manager.activeOperation, null);
});

test('restore stops stale Redis workers but does not reactivate them while Redis is Off', async () => {
  const manager = await createManager();
  const archivePath = path.join(manager.serverHome, 'redis-off-backup.tar.zst');
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(archivePath, 'backup');
  const steps = [];

  manager.readEnv = async () => ({ OMLORIX_VERSION: '1.2.3', REDIS_ENABLED: 'false' });
  manager.stopRemainingRestoreApplicationContainers = async () => {};
  manager.runDockerStep = async (label, args) => {
    steps.push({ label, args });
    return { ok: true, stdout: '', stderr: '' };
  };
  manager.waitForReady = async () => 'http://localhost:8080';
  manager.recordSuccessfulServerVersion = async () => {};
  manager.getState = async () => ({ stack: { healthy: true } });

  await manager.restore(archivePath);

  // Workers may still exist from the previously applied topology, so the
  // destructive restore must stop them even though the saved mode is now Off.
  assert.deepEqual(steps[0].args.slice(steps[0].args.lastIndexOf('stop')), [
    'stop',
    'frontend',
    'email_worker',
    ...DEDICATED_WORKER_SERVICES,
    'automation_scheduler',
    'automation_worker',
    'fastapi',
  ]);
  assert.deepEqual(steps[2].args.slice(steps[2].args.lastIndexOf('up')), [
    'up',
    '-d',
    '--no-deps',
    '--force-recreate',
    '--remove-orphans',
    'frontend',
    'email_worker',
    ...DEDICATED_WORKER_SERVICES,
    'fastapi',
  ]);
});

test('restore restarts Omlorix when the offline restore command fails', async () => {
  const manager = await createManager();
  const archivePath = path.join(manager.serverHome, 'backup.tar.zst');
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(archivePath, 'backup');
  const restartCommands = [];
  const operationEnds = [];
  let readinessChecks = 0;
  let step = 0;

  manager.readEnv = async () => ({ OMLORIX_VERSION: '1.2.3' });
  manager.stopRemainingRestoreApplicationContainers = async () => {};
  manager.runDockerStep = async () => {
    step += 1;
    if (step === 2) {
      const error = new Error('restore failed');
      error.dockerResult = {
        ok: false,
        stdout: JSON.stringify({
          status: 'failed',
          error: 'Preflight failed: target_not_empty',
          preflight: { reason: 'target_not_empty' },
          recovery: { state: 'rolled_back', safe_to_restart: true },
        }),
        stderr: '',
      };
      throw error;
    }
    return { ok: true, stdout: '', stderr: '' };
  };
  manager.execDocker = async (args) => {
    restartCommands.push(args);
    return { ok: true, stdout: '', stderr: '' };
  };
  manager.waitForReady = async () => {
    readinessChecks += 1;
    return 'http://localhost:8080/ready';
  };
  manager.on('operation-end', (payload) => operationEnds.push(payload));

  await assert.rejects(() => manager.restore(archivePath), /target is not empty/i);

  assert.equal(restartCommands.length, 1);
  assert.deepEqual(
    restartCommands[0].slice(restartCommands[0].lastIndexOf('up')),
    [
    'up',
    '-d',
    '--no-deps',
    '--force-recreate',
    '--remove-orphans',
    'frontend',
    'email_worker',
    ...DEDICATED_WORKER_SERVICES,
    'automation_scheduler',
    'automation_worker',
    'fastapi',
    ],
  );
  assert.equal(readinessChecks, 1);
  assert.equal(operationEnds.at(-1).messageKey, 'launcher_restore_stopped_safely');
  assert.deepEqual(operationEnds.at(-1).messageValues, {
    error: 'The restore target is not empty.',
    restoreReasonCode: 'target_not_empty',
  });
  assert.equal(manager.activeOperation, null);
});

test('restore leaves Omlorix stopped when backend recovery is unsafe', async () => {
  const manager = await createManager();
  const archivePath = path.join(manager.serverHome, 'backup.tar.zst');
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(archivePath, 'backup');
  const restartCommands = [];
  const operationEnds = [];
  let step = 0;

  manager.readEnv = async () => ({ OMLORIX_VERSION: '1.2.3' });
  manager.stopRemainingRestoreApplicationContainers = async () => {};
  manager.runDockerStep = async () => {
    step += 1;
    if (step === 2) {
      const error = new Error('restore and rollback failed');
      error.dockerResult = {
        ok: false,
        stdout: JSON.stringify({
          status: 'failed',
          recovery: { state: 'unsafe', safe_to_restart: false },
        }),
        stderr: '',
      };
      throw error;
    }
    return { ok: true, stdout: '', stderr: '' };
  };
  manager.execDocker = async (args) => {
    restartCommands.push(args);
    return { ok: true, stdout: '', stderr: '' };
  };
  manager.on('operation-end', (payload) => operationEnds.push(payload));

  await assert.rejects(
    () => manager.restore(archivePath),
    /safe recovery could not be confirmed/,
  );

  assert.deepEqual(restartCommands, []);
  assert.equal(operationEnds.at(-1).messageKey, 'launcher_restore_recovery_unconfirmed');
  assert.equal(manager.activeOperation, null);
});

test('restore restarts services after a partially failed stop attempt', async () => {
  const manager = await createManager();
  const archivePath = path.join(manager.serverHome, 'backup.tar.zst');
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(archivePath, 'backup');
  const restartCommands = [];

  manager.readEnv = async () => ({ OMLORIX_VERSION: '1.2.3' });
  manager.runDockerStep = async () => {
    throw new Error('stop timed out after stopping fastapi');
  };
  manager.execDocker = async (args) => {
    restartCommands.push(args);
    return { ok: true, stdout: '', stderr: '' };
  };
  manager.waitForReady = async () => 'http://localhost:8080/ready';

  await assert.rejects(
    () => manager.restore(archivePath),
    /stop timed out/,
  );

  assert.equal(restartCommands.length, 1);
  assert.deepEqual(
    restartCommands[0].slice(restartCommands[0].lastIndexOf('up')),
    [
    'up',
    '-d',
    '--no-deps',
    '--force-recreate',
    '--remove-orphans',
    'frontend',
    'email_worker',
    ...DEDICATED_WORKER_SERVICES,
    'automation_scheduler',
    'automation_worker',
    'fastapi',
    ],
  );
  assert.equal(manager.activeOperation, null);
});

test('restore reserves the operation before asynchronous validation', async () => {
  const manager = await createManager();
  const archivePath = path.join(manager.serverHome, 'backup.tar.zst');
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(archivePath, 'backup');
  let releaseReadEnv;

  manager.readEnv = () => new Promise((resolve) => {
    releaseReadEnv = () => resolve({ OMLORIX_VERSION: '1.2.3' });
  });
  manager.stopRemainingRestoreApplicationContainers = async () => {};
  manager.runDockerStep = async () => ({ ok: true, stdout: '', stderr: '' });
  manager.waitForReady = async () => 'http://localhost:8080';
  manager.recordSuccessfulServerVersion = async () => {};
  manager.getState = async () => ({ stack: { healthy: true } });

  const restorePromise = manager.restore(archivePath);
  assert.equal(manager.activeOperation, 'Restore');
  await assert.rejects(() => manager.restart(), /Another operation is already running: Restore/);

  releaseReadEnv();
  await restorePromise;
  assert.equal(manager.activeOperation, null);
});

test('restore reports unavailable source archives without raw filesystem details', async () => {
  const manager = await createManager();
  const missingArchive = path.join(manager.serverHome, 'vanished.tar.zst');
  manager.readEnv = async () => ({ OMLORIX_VERSION: '1.2.3' });

  await assert.rejects(
    () => manager.restore(missingArchive),
    (error) => {
      assert.equal(
        error.message,
        'The selected restore source is unavailable. Choose an existing, accessible Omlorix backup archive.',
      );
      assert.doesNotMatch(error.message, /ENOENT|vanished\.tar\.zst/);
      return true;
    },
  );
});

test('restore reports startup failure without implying restored data was rolled back', async () => {
  const manager = await createManager();
  const archivePath = path.join(manager.serverHome, 'backup.tar.zst');
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(archivePath, 'backup');
  const restartCommands = [];
  const operationEnds = [];
  let step = 0;

  manager.readEnv = async () => ({ OMLORIX_VERSION: '1.2.3' });
  manager.stopRemainingRestoreApplicationContainers = async () => {};
  manager.runDockerStep = async () => {
    step += 1;
    if (step === 3) throw new Error('startup failed');
    return { ok: true, stdout: '', stderr: '' };
  };
  manager.execDocker = async (args) => {
    restartCommands.push(args);
    return { ok: true, stdout: '', stderr: '' };
  };
  manager.on('operation-end', (payload) => operationEnds.push(payload));

  await assert.rejects(
    () => manager.restore(archivePath),
    /Server data was restored, but Omlorix failed to start/,
  );

  assert.deepEqual(restartCommands, []);
  assert.equal(operationEnds.at(-1).messageKey, 'launcher_restore_startup_failed_after_restore');
  assert.equal(operationEnds.at(-1).messageValues.error, 'startup failed');
  assert.equal(manager.activeOperation, null);
});

test('getComposeServiceImage inspects the running service container', async () => {
  const manager = await createManager();
  const commands = [];
  manager.readEnv = async () => ({ OMLORIX_VERSION: 'configured-but-not-running' });
  manager.execDocker = async (args) => {
    commands.push(args);
    if (args.includes('ps')) return { ok: true, stdout: 'container-id\n', stderr: '' };
    return {
      ok: true,
      stdout: 'ghcr.io/phinaldoo/omlorix-backend:1.7.0\n',
      stderr: '',
    };
  };

  assert.equal(
    await manager.getComposeServiceImage('fastapi'),
    'ghcr.io/phinaldoo/omlorix-backend:1.7.0',
  );
  assert.deepEqual(commands[1], [
    'inspect',
    '--format',
    '{{.Config.Image}}',
    'container-id',
  ]);
});

test('possibleDatabaseDowngradeError explains failed starts below the recorded maximum', async () => {
  const manager = await createManager();
  await manager.writeLauncherMetadata({
    highestSuccessfulServerVersion: '1.6.0',
  });
  const original = new Error('The ready endpoint timed out.');
  const diagnosed = await manager.possibleDatabaseDowngradeError(original, {
    OMLORIX_VERSION: '1.4.0',
  });

  assert.equal(diagnosed.code, 'POSSIBLE_DATABASE_DOWNGRADE');
  assert.equal(diagnosed.currentVersion, '1.4.0');
  assert.equal(diagnosed.highestVersion, '1.6.0');
  assert.equal(diagnosed.messageKey, 'launcher_possible_database_downgrade');
  assert.match(diagnosed.message, /database migrations/i);
  assert.equal(
    await manager.possibleDatabaseDowngradeError(original, {
      OMLORIX_VERSION: '1.6.0',
    }),
    original,
  );
});

test('latestReleaseInfo rejects a missing release manifest', async () => {
  const manager = await createManager();
  const manifestUrl = 'https://github.com/phinaldoo/omlorix/releases/download/v1.2.3/omlorix-release-manifest.json';

  manager.fetchJson = async (url) => {
    if (url.endsWith('/stable.json')) {
      return {
        tag: 'v1.2.3',
        manifestUrl,
        releaseUrl: 'https://github.com/phinaldoo/omlorix/releases/tag/v1.2.3',
      };
    }
    const error = new Error(`${url} returned HTTP 404`);
    error.statusCode = 404;
    throw error;
  };

  await assert.rejects(
    () => manager.latestReleaseInfo('stable'),
    /returned HTTP 404/,
  );
});

test('getServerUpdateInfo detects newer configured server releases', async () => {
  const manager = await createManager();
  let currentVersion = '1.2.2';
  manager.ensureServerHome = async () => {};
  manager.validateProfileEnv = async () => {};
  manager.readEnv = async () => ({
    OMLORIX_VERSION: currentVersion,
  });
  manager.latestReleaseInfo = async () => ({
    channel: 'stable',
    version: '1.2.3',
    manifest: null,
    releaseUrl: 'https://github.com/phinaldoo/omlorix/releases/tag/v1.2.3',
  });

  const available = await manager.getServerUpdateInfo();
  assert.equal(available.updateAvailable, true);
  assert.equal(available.currentVersion, '1.2.2');
  assert.equal(available.latestVersion, '1.2.3');
  assert.equal(available.launcherRequirement, null);

  currentVersion = '1.2.3';
  const current = await manager.getServerUpdateInfo();
  assert.equal(current.updateAvailable, false);
});

test('dashboard update checks return launcher requirements without weakening update enforcement', async () => {
  const manager = await createManager('1.2.2');
  manager.ensureServerHome = async () => {};
  manager.validateProfileEnv = async () => {};
  manager.readEnv = async () => ({
    OMLORIX_VERSION: '1.2.2',
  });
  manager.latestReleaseInfo = async () => ({
    channel: 'stable',
    version: '2.0.0',
    manifest: {
      minimumLauncherVersion: '2.0.0',
      launcherUpdateReason: 'New deployment files are required.',
    },
    releaseUrl: 'https://github.com/phinaldoo/omlorix/releases/tag/v2.0.0',
  });

  await assert.rejects(
    () => manager.getServerUpdateInfo(),
    (error) => error?.code === 'LAUNCHER_UPDATE_REQUIRED',
  );

  const displayInfo = await manager.getServerUpdateInfo('', {
    allowLauncherUpdateRequired: true,
  });
  assert.equal(displayInfo.updateAvailable, true);
  assert.deepEqual(displayInfo.launcherRequirement, {
    currentLauncherVersion: '1.2.2',
    minimumLauncherVersion: '2.0.0',
    targetVersion: '2.0.0',
    releaseNotes: 'New deployment files are required.',
  });
});

test('ensureServerHome copies env metadata from packaged resources', async () => {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-packaged-resources-'));
  const resourcesPath = path.join(tempDir, 'Resources');
  const deploymentAssets = path.join(resourcesPath, 'deployment-assets');
  const userData = path.join(tempDir, 'UserData');
  const requiredEnvPath = path.join(deploymentAssets, 'electron', 'required-env.json');
  const envMetadataPath = path.join(deploymentAssets, 'electron', 'env-metadata.json');
  const resourcesDescriptor = Object.getOwnPropertyDescriptor(process, 'resourcesPath');

  await fs.mkdir(path.dirname(requiredEnvPath), { recursive: true });
  await fs.writeFile(path.join(deploymentAssets, '.env.example'), 'OMLORIX_VERSION=stable\n', 'utf8');
  await fs.writeFile(requiredEnvPath, '{"JWT_SECRET_KEY":{"required":true}}\n', 'utf8');
  await fs.writeFile(envMetadataPath, '{"fields":{"OMLORIX_VERSION":{"section":"Release","description":"Version metadata."}}}\n', 'utf8');

  Object.defineProperty(process, 'resourcesPath', {
    configurable: true,
    value: resourcesPath,
  });

  try {
    const manager = new ServerManager({
      app: {
        isPackaged: true,
        getPath: () => userData,
        getName: () => 'Omlorix Server Launcher',
        getVersion: () => '1.2.2',
      },
      appRoot: tempDir,
    });

    await manager.ensureServerHome();

    assert.equal(
      await fs.readFile(path.join(userData, 'server', 'electron', 'required-env.json'), 'utf8'),
      '{"JWT_SECRET_KEY":{"required":true}}\n',
    );
    assert.equal(
      await fs.readFile(path.join(userData, 'server', 'electron', 'env-metadata.json'), 'utf8'),
      '{"fields":{"OMLORIX_VERSION":{"section":"Release","description":"Version metadata."}}}\n',
    );
  } finally {
    if (resourcesDescriptor) {
      Object.defineProperty(process, 'resourcesPath', resourcesDescriptor);
    } else {
      delete process.resourcesPath;
    }
  }
});

test('ensureServerHome coalesces initialization and exposes only complete asset replacements', async (t) => {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-atomic-assets-'));
  const sourceRoot = path.join(tempDir, 'source');
  const userData = path.join(tempDir, 'user-data');
  const source = path.join(sourceRoot, '.env.example');
  const target = path.join(userData, 'server', '.env.example');
  const envFile = path.join(userData, 'server', '.env');
  await fs.mkdir(path.dirname(source), { recursive: true });
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(source, 'NEW_ASSET=complete\n', 'utf8');
  await fs.writeFile(target, 'OLD_ASSET=complete\n', 'utf8');
  await fs.writeFile(envFile, 'OMLORIX_VERSION=test\n', 'utf8');

  const manager = new ServerManager({
    app: {
      isPackaged: false,
      getPath: () => userData,
      getName: () => 'Omlorix Server Launcher',
      getVersion: () => '1.2.2',
    },
    appRoot: sourceRoot,
  });
  const originalCopyFile = fs.copyFile;
  const originalOpen = fs.open;
  let releaseCopy;
  let announceCopy;
  let copyCount = 0;
  const temporaryOpenFlags = [];
  const copyStarted = new Promise((resolve) => { announceCopy = resolve; });
  const copyGate = new Promise((resolve) => { releaseCopy = resolve; });
  fs.copyFile = async (copySource, copyTarget, ...args) => {
    if (copySource === source) {
      copyCount += 1;
      await fs.writeFile(copyTarget, '', 'utf8');
      announceCopy();
      await copyGate;
    }
    return originalCopyFile(copySource, copyTarget, ...args);
  };
  fs.open = async (filePath, flags, ...args) => {
    if (path.dirname(filePath) === path.dirname(target) && filePath.endsWith('.tmp')) {
      temporaryOpenFlags.push(flags);
    }
    return originalOpen(filePath, flags, ...args);
  };
  t.after(async () => {
    fs.copyFile = originalCopyFile;
    fs.open = originalOpen;
    await fs.rm(tempDir, { recursive: true, force: true });
  });

  const first = manager.ensureServerHome();
  await copyStarted;
  const second = manager.ensureServerHome();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(copyCount, 1);
  assert.equal(await fs.readFile(target, 'utf8'), 'OLD_ASSET=complete\n');

  releaseCopy();
  await Promise.all([first, second]);
  assert.equal(await fs.readFile(target, 'utf8'), 'NEW_ASSET=complete\n');
  assert.deepEqual(temporaryOpenFlags, ['r+']);

  await manager.ensureServerHome();
  assert.equal(copyCount, 1);
});

test('failed atomic asset initialization preserves the old file and remains retryable', async (t) => {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-asset-retry-'));
  const sourceRoot = path.join(tempDir, 'source');
  const userData = path.join(tempDir, 'user-data');
  const source = path.join(sourceRoot, '.env.example');
  const target = path.join(userData, 'server', '.env.example');
  const envFile = path.join(userData, 'server', '.env');
  await fs.mkdir(path.dirname(source), { recursive: true });
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(source, 'NEW_ASSET=complete\n', 'utf8');
  await fs.writeFile(target, 'OLD_ASSET=complete\n', 'utf8');
  await fs.writeFile(envFile, 'OMLORIX_VERSION=test\n', 'utf8');

  const manager = new ServerManager({
    app: {
      isPackaged: false,
      getPath: () => userData,
      getName: () => 'Omlorix Server Launcher',
      getVersion: () => '1.2.2',
    },
    appRoot: sourceRoot,
  });
  const originalCopyFile = fs.copyFile;
  fs.copyFile = async (copySource, copyTarget, ...args) => {
    if (copySource === source) {
      await fs.writeFile(copyTarget, 'PARTIAL', 'utf8');
      throw new Error('simulated copy failure');
    }
    return originalCopyFile(copySource, copyTarget, ...args);
  };
  t.after(async () => {
    fs.copyFile = originalCopyFile;
    await fs.rm(tempDir, { recursive: true, force: true });
  });

  await assert.rejects(manager.ensureServerHome(), /simulated copy failure/);
  assert.equal(await fs.readFile(target, 'utf8'), 'OLD_ASSET=complete\n');
  assert.equal(
    (await fs.readdir(path.dirname(target))).some((name) => name.endsWith('.tmp')),
    false,
  );

  fs.copyFile = originalCopyFile;
  await manager.ensureServerHome();
  assert.equal(await fs.readFile(target, 'utf8'), 'NEW_ASSET=complete\n');
});

test('desktop package includes every file installed into the server home', async () => {
  const packageJsonPath = path.join(__dirname, '..', '..', 'package.json');
  const packageConfig = JSON.parse(await fs.readFile(packageJsonPath, 'utf8'));
  assert(
    packageConfig.build.files.includes('cmd/omlorix-server-cli/server-files.json'),
    'the packaged Launcher must include its shared server-file contract',
  );
  assert(
    packageConfig.build.files.includes('cmd/omlorix-server-cli/server-management-contract.json'),
    'the packaged Launcher must include its shared management contract',
  );
  const deploymentAssetsResource = packageConfig.build.extraResources.find(
    (resource) => resource.to === 'deployment-assets',
  );

  assert(deploymentAssetsResource, 'package.json must define the deployment-assets resource');
  for (const relativePath of SERVER_FILES) {
    assert(
      deploymentAssetsResource.filter.some((filter) => resourceFilterIncludesFile(filter, relativePath)),
      `${relativePath} is installed by ServerManager but missing from the packaged deployment assets`,
    );
  }
});

test('update persists the selected release channel and forwards the reviewed backup policy', async () => {
  const manager = await createManager();
  const writes = [];
  const settingsWrites = [];
  const stateSnapshots = [];
  const updateSteps = [];
  let backupOptions = null;

  manager.ensureServerHome = async () => {};
  manager.ensureGeneratedSecrets = async () => {};
  manager.validateProfileEnv = async () => {};
  manager.getState = async () => {
    stateSnapshots.push(manager.activeOperation);
    return {
      stack: {
        running: 2,
        services: [{ Service: 'fastapi', State: 'running' }],
      },
    };
  };
  manager.readEnv = async () => ({
    OMLORIX_VERSION: '1.2.2',
  });
  manager.readServerSettings = async () => ({ schemaVersion: 1, updateChannel: 'stable' });
  manager.latestReleaseInfo = async (channel) => ({
    channel,
    version: '1.3.0-beta.1',
    manifest: null,
  });
  manager.assertLauncherCompatible = () => {};
  manager.backup = async (options) => {
    backupOptions = options;
  };
  manager.writeEnv = async (updates) => {
    writes.push(updates);
  };
  manager.updateServerSettings = async (update) => {
    const next = update({ schemaVersion: 1, updateChannel: 'stable' });
    settingsWrites.push(next);
    return next;
  };
  manager.composeArgs = () => ['compose'];
  manager.runUpdateStep = async (label, args, _timeout, messageKey) => {
    updateSteps.push({ label, args, messageKey });
  };
  manager.waitForReady = async () => 'http://localhost:8080/ready';

  await manager.update({
    channel: 'beta',
    destinationId: 'remote-store',
    encryptionEnabled: false,
  });

  assert.deepEqual(writes[0], { OMLORIX_VERSION: '1.3.0-beta.1' });
  assert.equal(settingsWrites[0].updateChannel, 'beta');
  assert.deepEqual(backupOptions, {
    destinationId: 'remote-store',
    encryptionEnabled: false,
    sharedLockHeld: true,
  });
  const stopStep = updateSteps.find(
    (step) => step.label === 'Stopping application services before migration',
  );
  assert(stopStep, 'the update must drain application writers before migration');
  assert.equal(stopStep.messageKey, 'launcher_update_stopping_services');
  assert.deepEqual(stopStep.args.slice(-2), ['down', '--remove-orphans']);
  assert(
    updateSteps.indexOf(stopStep)
      < updateSteps.findIndex((step) => step.label === 'Running migrations'),
    'application drain must precede migrations',
  );
  assert.deepEqual(stateSnapshots, [null, null]);
});

test('update cancels before changing the release when the reviewed backup destination fails', async () => {
  const manager = await createManager();
  let releaseWriteStarted = false;
  let updateStepStarted = false;

  manager.ensureServerHome = async () => {};
  manager.repairBundledRedisUrl = async () => {};
  manager.validateProfileEnv = async () => {};
  manager.getState = async () => ({
    stack: {
      running: 2,
      services: [{ Service: 'fastapi', State: 'running' }],
    },
  });
  manager.readEnv = async () => ({ OMLORIX_VERSION: '1.2.2' });
  manager.validateComposeOwnership = async () => {};
  manager.readServerSettings = async () => ({ schemaVersion: 1, updateChannel: 'stable' });
  manager.latestReleaseInfo = async () => ({
    channel: 'stable',
    version: '1.2.3',
    manifest: null,
  });
  manager.assertLauncherCompatible = () => {};
  manager.backup = async (options) => {
    assert.deepEqual(options, {
      destinationId: 'missing-destination',
      encryptionEnabled: false,
      sharedLockHeld: true,
    });
    throw new Error('Backup destination is unavailable.');
  };
  manager.writeEnv = async () => {
    releaseWriteStarted = true;
  };
  manager.runUpdateStep = async () => {
    updateStepStarted = true;
  };

  await assert.rejects(
    () => manager.update({
      destinationId: 'missing-destination',
      encryptionEnabled: false,
    }),
    /backup destination is unavailable/i,
  );

  assert.equal(releaseWriteStarted, false);
  assert.equal(updateStepStarted, false);
});

test('saveSettings resolves moving channel tags to concrete versions', async () => {
  const manager = await createManager();
  const writes = [];
  const settingsWrites = [];

  manager.readEnv = async () => ({
    OMLORIX_VERSION: 'stable',
  });
  manager.latestReleaseInfo = async (channel) => ({
    channel,
    version: '1.2.3',
    manifest: null,
  });
  manager.writeEnv = async (updates) => {
    writes.push(updates);
  };
  manager.updateServerSettings = async (update) => {
    const next = update({ schemaVersion: 1, updateChannel: 'stable' });
    settingsWrites.push(next);
    return next;
  };
  manager.getState = async () => ({ ok: true });

  await manager.saveSettings({
    version: 'stable',
    updateChannel: 'stable',
  });

  assert.equal(writes[0].OMLORIX_VERSION, '1.2.3');
  assert.equal(writes[0].OMLORIX_UPDATE_CHANNEL, undefined);
  assert.equal(settingsWrites[0].updateChannel, 'stable');
});

test('saveSettings persists unrelated edits when an unchanged legacy secret is invalid', async () => {
  const manager = await createManager();
  manager.getState = async () => ({ stack: { running: 0, services: [] } });

  // Reproduce an installation created before password-reset salts acquired a
  // minimum length. A legacy renderer may still include this unchanged value
  // in a complete settings payload while the operator edits only Compose.
  await manager.writeEnv({
    COMPOSE_PROJECT_NAME: 'omlorix-old',
    PASSWORD_RESET_IDENTIFIER_HASH_SALT: 'legacy',
  });

  await manager.saveSettings({
    composeProjectName: 'omlorix-new',
    passwordResetSalt: 'legacy',
  });

  const env = await manager.readEnv();
  assert.equal(env.COMPOSE_PROJECT_NAME, 'omlorix-new');
  assert.equal(env.PASSWORD_RESET_IDENTIFIER_HASH_SALT, 'legacy');
});

test('saveSettings enforces JWT secret length in UTF-8 bytes', async () => {
  const manager = await createManager();
  manager.getState = async () => ({});
  await manager.writeEnv({ JWT_SECRET_KEY: 'j'.repeat(64) });

  await assert.rejects(
    manager.saveSettings({ jwtSecretKey: 'x'.repeat(63) }),
    /at least 64 bytes/,
  );

  const multibyteSecret = 'é'.repeat(32);
  assert.equal(multibyteSecret.length, 32);
  assert.equal(Buffer.byteLength(multibyteSecret, 'utf8'), 64);
  await manager.saveSettings({ jwtSecretKey: multibyteSecret });
  assert.equal((await manager.readEnv()).JWT_SECRET_KEY, multibyteSecret);
});

test('regenerateSecrets creates a JWT secret from 64 random bytes', async () => {
  const manager = await createManager();

  await manager.regenerateSecrets(['JWT_SECRET_KEY']);

  const secret = (await manager.readEnv()).JWT_SECRET_KEY;
  assert.equal(Buffer.from(secret, 'base64').length, 64);
  assert.equal(Buffer.byteLength(secret, 'utf8') >= 64, true);
});

test('saveSettings rejects an audit IP salt that normalizes to the JWT signing key', async () => {
  const manager = await createManager();
  const jwtSecret = 'j'.repeat(64);
  const originalSalt = 'i'.repeat(32);
  manager.getState = async () => ({});
  await manager.writeEnv({
    JWT_SECRET_KEY: jwtSecret,
    LOG_IP_HASH_SALT: originalSalt,
  });

  await assert.rejects(
    manager.saveSettings({ logIpHashSalt: `  ${jwtSecret}  ` }),
    (error) => (
      error.code === 'LOG_IP_HASH_SALT_REUSES_JWT_SECRET_KEY'
      && error.validationErrors?.LOG_IP_HASH_SALT
        === 'launcher_ui_log_ip_hash_salt_must_differ_from_jwt_secret_key'
    ),
  );
  assert.equal((await manager.readEnv()).LOG_IP_HASH_SALT, originalSalt);

  const independentSalt = 's'.repeat(32);
  await manager.saveSettings({ logIpHashSalt: independentSalt });
  assert.equal((await manager.readEnv()).LOG_IP_HASH_SALT, independentSalt);
});

test('restart reserves the operation and migrates offline before recreating containers', async () => {
  const manager = await createManager();
  const commands = [];
  const events = [];

  manager.on('operation-start', (payload) => events.push(['start', payload.name, manager.activeOperation]));
  manager.repairBundledRedisUrl = async () => {
    events.push(['repair', manager.activeOperation]);
  };
  manager.ensureServerHome = async () => {
    events.push(['ensure', manager.activeOperation]);
  };
  manager.validateProfileEnv = async () => {};
  manager.readEnv = async () => ({
    OMLORIX_VERSION: '1.2.3',
  });
  manager.execDocker = async (args) => {
    commands.push(args);
    return { ok: true, stdout: '', stderr: '' };
  };
  manager.waitForReady = async () => 'http://localhost:8080/ready';
  manager.recordSuccessfulServerVersion = async (version) => {
    events.push(['record-version', version, manager.activeOperation]);
  };
  manager.getState = async () => {
    events.push(['state', manager.activeOperation]);
    return { ok: true };
  };

  const state = await manager.restart();

  assert.deepEqual(state, { ok: true });
  assert.deepEqual(events, [
    ['start', 'Restart', 'Restart'],
    ['repair', 'Restart'],
    ['ensure', 'Restart'],
    ['record-version', '1.2.3', 'Restart'],
    ['state', null],
  ]);
  assert.equal(commands.length, 4);
  assert.deepEqual(commands[0].slice(-2), ['down', '--remove-orphans']);
  assert.deepEqual(commands[1].slice(-3), ['rm', '-sf', 'migrate']);
  assert.deepEqual(commands[2].slice(-4), ['up', '-d', '--force-recreate', 'migrate']);
  assert.deepEqual(commands[3].slice(-4), ['up', '-d', '--force-recreate', '--remove-orphans']);
});

test('restart applies restored managed-proxy settings only at the explicit restart boundary', async () => {
  for (const scenario of [
    {
      enabled: true,
      expected: ['proxy-stop', 'migration-step', 'migration-step', 'migration-step', 'compose-up', 'ready', 'proxy-start'],
    },
    {
      enabled: false,
      expected: ['proxy-stop', 'migration-step', 'migration-step', 'migration-step', 'compose-up', 'ready'],
    },
  ]) {
    const manager = await createManager();
    const calls = [];
    const env = {
      OMLORIX_VERSION: '1.2.3',
      OMLORIX_LAUNCHER_PROXY_ENABLED: String(scenario.enabled),
      OMLORIX_LAUNCHER_PROXY_AUTOSTART: 'false',
    };

    manager.repairBundledRedisUrl = async () => {};
    manager.validateProfileEnv = async () => {};
    manager.ensureIngressAuthenticationCredential = async () => {};
    manager.prepareCompose = async () => ({ env });
    manager.proxyServiceStatus = async () => ({ available: false, running: false });
    manager.proxy.status = () => ({ running: true });
    manager.stopProxy = async () => { calls.push('proxy-stop'); };
    manager.startProxy = async () => { calls.push('proxy-start'); };
    manager.runDockerStep = async (label) => {
      calls.push(label === 'Recreating application containers' ? 'compose-up' : 'migration-step');
    };
    manager.finalizeProjectAdoption = async () => {};
    manager.waitForReady = async () => { calls.push('ready'); return 'http://localhost:8080/ready'; };
    manager.readEnv = async () => env;
    manager.convergeVisitorIps = async () => {};
    manager.recordSuccessfulServerVersion = async () => {};
    manager.getState = async () => ({ ok: true });

    await manager.restart();
    assert.deepEqual(calls, scenario.expected);
  }
});

test('stop returns state after the managed proxy has stopped', async () => {
  const manager = await createManager();
  let proxyRunning = true;
  const calls = [];
  manager.prepareCompose = async () => ({ args: ['docker', 'compose'] });
  manager.runOperation = async () => {
    calls.push('compose-down');
    return { proxy: { running: true } };
  };
  manager.finalizeProjectAdoption = async () => {};
  manager.stopProxy = async () => {
    calls.push('proxy-stop');
    proxyRunning = false;
  };
  manager.getState = async () => ({ proxy: { running: proxyRunning } });

  const state = await manager.stop();

  assert.deepEqual(calls, ['compose-down', 'proxy-stop']);
  assert.equal(state.proxy.running, false);
});

test('start records the configured version only after Omlorix becomes ready', async () => {
  const manager = await createManager();
  await manager.writeEnv({
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
    OMLORIX_USE_BUNDLED_STORAGE: 'false',
    OMLORIX_USE_PGBOUNCER: 'false',
    JWT_SECRET_KEY: 'x'.repeat(64),
    LOG_IP_HASH_SALT: 'i'.repeat(32),
    ENCRYPTION_KEY: 'test-encryption-key',
    DATABASE_PASSWORD: 'database-secret',
    REDIS_PASSWORD: 'redis-secret',
    REDIS_URL: 'redis://:redis-secret@redis:6379/0',
    OMLORIX_VERSION: '1.4.0',
  });
  await manager.writeLauncherMetadata({
    highestSuccessfulServerVersion: '1.3.0',
  });
  let operationOptions = null;
  const migrationSteps = [];
  manager.prepareCompose = async () => ({
    env: await manager.readEnv(),
    // ServerManager passes arguments to the Docker executable, so the
    // Compose subcommand is the first argument in the production contract.
    args: ['compose'],
  });
  manager.finalizeProjectAdoption = async () => {};
  manager.runDockerStep = async (label, args, timeoutMs, operationName, messageKey) => {
    migrationSteps.push({ label, args, timeoutMs, operationName, messageKey });
  };
  manager.runOperation = async (name, args, options) => {
    operationOptions = options;
    return options.onSuccess();
  };
  manager.waitForReady = async () => 'http://localhost:8080/ready';
  manager.getState = async () => ({ healthy: true });

  await manager.start();

  assert.equal(operationOptions.successMessageKey, 'launcher_start_finished');
  assert.deepEqual(operationOptions.failureLogServices, ['migrate']);
  assert.equal(operationOptions.failureLogComposeArgs[0], 'compose');
  assert.deepEqual(migrationSteps.map((step) => step.label), [
    'Stopping application services before migration',
    'Resetting migration container',
    'Running migrations',
  ]);
  assert.equal(migrationSteps[0].operationName, 'Start');
  assert.equal(migrationSteps[0].messageKey, 'launcher_update_stopping_services');
  assert.deepEqual(
    migrationSteps[0].args.slice(-2),
    ['down', '--remove-orphans'],
  );
  assert.deepEqual(migrationSteps[2].args.slice(-4), [
    'up', '-d', '--force-recreate', 'migrate',
  ]);
  assert.equal(
    (await manager.readLauncherMetadata()).highestSuccessfulServerVersion,
    '1.4.0',
  );
});

test('first start calibrates visitor IPs after frontend readiness without reapply', async () => {
  const manager = await createManager();
  const events = [];
  manager.repairBundledRedisUrl = async () => {};
  manager.readEnv = async () => ({
    OMLORIX_LAUNCHER_PROXY_ENABLED: 'true',
    OMLORIX_VERSION: '1.4.0',
  });
  manager.validateProfileEnv = async () => {};
  manager.writeEnv = async () => {};
  manager.prepareCompose = async () => ({ env: { OMLORIX_VERSION: '1.4.0' }, args: ['compose'] });
  manager.runDockerStep = async () => {};
  manager.runOperation = async (_name, _args, options) => options.onSuccess();
  manager.waitForReady = async () => {
    events.push('frontend-ready');
    return 'http://127.0.0.1:8080';
  };
  manager.convergeVisitorIps = async () => { events.push('calibrated'); };
  manager.recordSuccessfulServerVersion = async () => { events.push('version-recorded'); };

  await manager.start();

  assert.deepEqual(events, ['frontend-ready', 'calibrated', 'version-recorded']);
});

test('start diagnoses a possible migration downgrade when readiness fails', async () => {
  const manager = await createManager();
  await manager.writeEnv({
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
    OMLORIX_USE_BUNDLED_STORAGE: 'false',
    OMLORIX_USE_PGBOUNCER: 'false',
    JWT_SECRET_KEY: 'x'.repeat(64),
    LOG_IP_HASH_SALT: 'i'.repeat(32),
    ENCRYPTION_KEY: 'test-encryption-key',
    DATABASE_PASSWORD: 'database-secret',
    REDIS_PASSWORD: 'redis-secret',
    REDIS_URL: 'redis://:redis-secret@redis:6379/0',
    OMLORIX_VERSION: '1.4.0',
  });
  await manager.writeLauncherMetadata({
    highestSuccessfulServerVersion: '1.6.0',
  });
  manager.prepareCompose = async () => ({
    env: await manager.readEnv(),
    args: ['compose'],
  });
  manager.runDockerStep = async () => {};
  manager.runOperation = async (name, args, options) => {
    throw await options.onError(new Error('The ready endpoint timed out.'));
  };

  await assert.rejects(
    () => manager.start(),
    (error) => error?.code === 'POSSIBLE_DATABASE_DOWNGRADE'
      && error.currentVersion === '1.4.0'
      && error.highestVersion === '1.6.0',
  );
  assert.equal(
    (await manager.readLauncherMetadata()).highestSuccessfulServerVersion,
    '1.6.0',
  );
});

test('restart emits translated downgrade diagnostics after a lower version fails', async () => {
  const manager = await createManager();
  const operationEnds = [];
  manager.on('operation-end', (payload) => operationEnds.push(payload));
  manager.repairBundledRedisUrl = async () => {};
  manager.ensureServerHome = async () => {};
  manager.validateProfileEnv = async () => {};
  await manager.writeLauncherMetadata({
    highestSuccessfulServerVersion: '1.6.0',
  });
  manager.readEnv = async () => ({
    OMLORIX_VERSION: '1.4.0',
  });
  manager.execDocker = async () => ({ ok: true, stdout: '', stderr: '' });
  manager.waitForReady = async () => {
    throw new Error('http://localhost:8080/ready did not become ready.');
  };

  await assert.rejects(
    () => manager.restart(),
    (error) => error?.code === 'POSSIBLE_DATABASE_DOWNGRADE',
  );

  assert.equal(operationEnds.length, 1);
  assert.equal(operationEnds[0].ok, false);
  assert.equal(operationEnds[0].messageKey, 'launcher_possible_database_downgrade');
  assert.deepEqual(operationEnds[0].messageValues, {
    currentVersion: '1.4.0',
    highestVersion: '1.6.0',
    error: 'http://localhost:8080/ready did not become ready.',
  });
});

test('legacy .env metadata migrates monotonically into launcher metadata', async () => {
  const manager = await createManager();
  await manager.ensureServerHome();
  await manager.writeLauncherMetadata({
    highestSuccessfulServerVersion: '2.0.0',
  });
  await fs.writeFile(
    manager.envFile,
    'OMLORIX_VERSION=1.0.0\nOMLORIX_HIGHEST_VERSION_USED=1.5.0\n',
    'utf8',
  );

  await manager.ensureServerHome();

  assert.equal(
    (await manager.readLauncherMetadata()).highestSuccessfulServerVersion,
    '2.0.0',
  );
  assert.doesNotMatch(await fs.readFile(manager.envFile, 'utf8'), /OMLORIX_HIGHEST_VERSION_USED/);

  await fs.writeFile(
    manager.envFile,
    'OMLORIX_VERSION=1.0.0\nOMLORIX_HIGHEST_VERSION_USED=2.5.0\n',
    'utf8',
  );
  await manager.ensureServerHome();
  assert.equal(
    (await manager.readLauncherMetadata()).highestSuccessfulServerVersion,
    '2.5.0',
  );
});

test('legacy release and host proxy settings migrate out of dotenv with a recovery backup', async () => {
  const manager = await createManager();
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(
    manager.envFile,
    [
      'OMLORIX_VERSION=1.2.2',
      'OMLORIX_UPDATE_CHANNEL=beta',
      'OMLORIX_BACKEND_IMAGE_REPOSITORY=registry.example/backend',
      'OMLORIX_FRONTEND_IMAGE_REPOSITORY=registry.example/frontend',
      'FILE_SCANNER_COMMAND=clamscan --no-summary',
      'OMLORIX_GITHUB_TOKEN=retired-release-token',
      'OMLORIX_LAUNCHER_PROXY_ENABLED=true',
      'OMLORIX_LAUNCHER_PROXY_AUTOSTART=false',
      'OMLORIX_LAUNCHER_PROXY_BIND=127.0.0.1',
      'OMLORIX_LAUNCHER_PROXY_HTTP_PORT=9081',
      'OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE=legacy-passphrase',
      '',
    ].join('\n'),
    'utf8',
  );

  await manager.ensureServerHome();

  const settings = await manager.readServerSettings();
  const migratedEnv = await fs.readFile(manager.envFile, 'utf8');
  const backups = await fs.readdir(path.join(manager.serverHome, '.env.backups'));
  assert.equal(settings.updateChannel, 'beta');
  assert.equal(settings.proxy.enabled, true);
  assert.equal(settings.proxy.autostart, false);
  assert.equal(settings.proxy.bindHost, '127.0.0.1');
  assert.equal(settings.proxy.httpPort, '9081');
  assert.equal(settings.proxy.tlsKeyPassphrase, 'legacy-passphrase');
  assert.doesNotMatch(migratedEnv, /OMLORIX_UPDATE_CHANNEL/);
  assert.doesNotMatch(migratedEnv, /OMLORIX_(?:BACKEND|FRONTEND)_IMAGE_REPOSITORY/);
  assert.doesNotMatch(migratedEnv, /FILE_SCANNER_COMMAND/);
  assert.doesNotMatch(migratedEnv, /OMLORIX_GITHUB_TOKEN/);
  assert.doesNotMatch(migratedEnv, /OMLORIX_LAUNCHER_PROXY_(?:ENABLED|AUTOSTART|BIND|HTTP_PORT|TLS_KEY_PASSPHRASE)/);
  assert.equal(backups.length, 1);
  assert.match(
    await fs.readFile(path.join(manager.serverHome, '.env.backups', backups[0]), 'utf8'),
    /registry\.example\/backend/,
  );
  assert.doesNotMatch(
    await fs.readFile(path.join(manager.serverHome, '.env.backups', backups[0]), 'utf8'),
    /OMLORIX_GITHUB_TOKEN/,
  );
});

test('env imports ignore obsolete configuration keys', async () => {
  const manager = await createManager();
  await manager.writeEnv({ OMLORIX_VERSION: '1.0.0' });
  await manager.writeLauncherMetadata({
    highestSuccessfulServerVersion: '2.0.0',
  });
  const importFile = path.join(path.dirname(manager.serverHome), 'legacy-omlorix.env');
  await fs.writeFile(
    importFile,
    'OMLORIX_VERSION=1.5.0\nOMLORIX_HIGHEST_VERSION_USED=9.0.0\nFILE_SCANNER_COMMAND=clamscan\nOMLORIX_GITHUB_TOKEN=retired-release-token\n',
    'utf8',
  );
  const preview = await manager.previewEnvImport(importFile);
  manager.getState = async () => ({ ok: true });
  manager.getEnvEditor = async () => ({ fields: [] });
  manager.stackStatus = async () => ({ running: 0 });
  manager.proxyServiceStatus = async () => ({ running: false });

  const result = await manager.applyEnvImport(preview.importId);

  assert.equal(result.ok, true);
  assert.equal((await manager.readEnv()).OMLORIX_VERSION, '1.5.0');
  assert.equal(
    (await manager.readLauncherMetadata()).highestSuccessfulServerVersion,
    '2.0.0',
  );
  const importedEnv = await fs.readFile(manager.envFile, 'utf8');
  assert.doesNotMatch(importedEnv, /OMLORIX_HIGHEST_VERSION_USED/);
  assert.doesNotMatch(importedEnv, /FILE_SCANNER_COMMAND/);
  assert.doesNotMatch(importedEnv, /OMLORIX_GITHUB_TOKEN/);
});

test('update rollback restores the previous release channel', async () => {
  const manager = await createManager();
  const writes = [];
  const settingsWrites = [];
  const rollbackCommands = [];

  manager.ensureServerHome = async () => {};
  manager.ensureGeneratedSecrets = async () => {};
  manager.validateProfileEnv = async () => {};
  manager.getState = async () => ({
    stack: {
      running: 2,
      services: [{ Service: 'fastapi', State: 'running' }],
    },
  });
  manager.readEnv = async () => ({
    OMLORIX_VERSION: '1.2.2',
  });
  manager.readServerSettings = async () => ({ schemaVersion: 1, updateChannel: 'stable' });
  manager.latestReleaseInfo = async (channel) => ({
    channel,
    version: '1.3.0-beta.1',
    manifest: null,
  });
  manager.assertLauncherCompatible = () => {};
  manager.backup = async () => {};
  manager.writeEnv = async (updates) => {
    writes.push(updates);
  };
  manager.updateServerSettings = async (update) => {
    const current = settingsWrites.length
      ? settingsWrites[settingsWrites.length - 1]
      : { schemaVersion: 1, updateChannel: 'stable' };
    const next = update(current);
    settingsWrites.push(next);
    return next;
  };
  manager.composeArgs = () => ['compose'];
  manager.runUpdateStep = async () => {
    throw new Error('pull failed');
  };
  manager.execDocker = async (args) => {
    rollbackCommands.push(args);
    return { ok: true };
  };

  await assert.rejects(
    () => manager.update({ channel: 'beta', skipBackup: true }),
    /pull failed/,
  );

  assert.deepEqual(writes, [
    { OMLORIX_VERSION: '1.3.0-beta.1' },
    { OMLORIX_VERSION: '1.2.2' },
  ]);
  assert.deepEqual(settingsWrites.map((settings) => settings.updateChannel), ['beta', 'stable']);
  assert.equal(
    rollbackCommands.length,
    0,
    'a pull failure must restore settings without restarting an unchanged stack',
  );
});

test('update rollback never starts containers when the project-wide drain fails', async () => {
  const manager = await createManager();
  const rollbackCommands = [];

  manager.ensureServerHome = async () => {};
  manager.validateProfileEnv = async () => {};
  manager.getState = async () => ({
    stack: {
      running: 2,
      services: [{ Service: 'fastapi', State: 'running' }],
    },
  });
  manager.readEnv = async () => ({ OMLORIX_VERSION: '1.2.2' });
  manager.readServerSettings = async () => ({ schemaVersion: 1, updateChannel: 'stable' });
  manager.latestReleaseInfo = async () => ({
    channel: 'stable',
    version: '1.3.0',
    manifest: null,
  });
  manager.assertLauncherCompatible = () => {};
  manager.writeEnv = async () => {};
  manager.updateServerSettings = async (update) => update({
    schemaVersion: 1,
    updateChannel: 'stable',
  });
  manager.runUpdateStep = async (label) => {
    if (label === 'Stopping application services before migration') {
      throw new Error('initial drain failed');
    }
  };
  manager.execDocker = async (args) => {
    rollbackCommands.push(args);
    return { ok: false, stdout: '', stderr: 'rollback drain failed' };
  };

  await assert.rejects(
    () => manager.update({ skipBackup: true }),
    (error) => (
      error.messageKey === 'launcher_update_pre_migration_rollback_left_offline'
      && error.messageValues?.previousVersion === '1.2.2'
      && /left offline/.test(error.message)
    ),
  );

  assert.equal(rollbackCommands.length, 1);
  assert.deepEqual(rollbackCommands[0].slice(-2), ['down', '--remove-orphans']);
});

test('update keeps the target selected and offline after migration may have started', async () => {
  const manager = await createManager();
  const operationEnds = [];
  const writes = [];
  const settingsWrites = [];
  const dockerCommands = [];
  manager.on('operation-end', (payload) => operationEnds.push(payload));
  manager.ensureServerHome = async () => {};
  manager.validateProfileEnv = async () => {};
  manager.assertUpdatePrerequisites = async () => {};
  await manager.writeLauncherMetadata({
    highestSuccessfulServerVersion: '1.6.0',
  });
  manager.readEnv = async () => ({
    OMLORIX_VERSION: '1.5.0',
  });
  manager.readServerSettings = async () => ({ schemaVersion: 1, updateChannel: 'stable' });
  manager.latestReleaseInfo = async () => ({
    channel: 'stable',
    version: '1.4.0',
    manifest: null,
  });
  manager.assertLauncherCompatible = () => {};
  manager.writeEnv = async (updates) => writes.push(updates);
  manager.updateServerSettings = async (update) => {
    const current = settingsWrites.at(-1) || { schemaVersion: 1, updateChannel: 'stable' };
    const nextSettings = update(current);
    settingsWrites.push(nextSettings);
    return nextSettings;
  };
  manager.runUpdateStep = async (label) => {
    if (label === 'Running migrations') {
      throw new Error('migration container exited');
    }
  };
  manager.execDocker = async (args) => {
    dockerCommands.push(args);
    return { ok: true, stdout: '', stderr: '' };
  };

  await assert.rejects(
    () => manager.update({ skipBackup: true }),
    (error) => (
      error?.messageKey === 'launcher_update_rollback_left_offline'
      && /Target release 1\.4\.0 remains selected/.test(error.message)
    ),
  );

  assert.deepEqual(writes, [{ OMLORIX_VERSION: '1.4.0' }]);
  assert.deepEqual(settingsWrites.map((settings) => settings.updateChannel), ['stable']);
  assert.equal(dockerCommands.length, 1);
  assert.deepEqual(dockerCommands[0].slice(-2), ['down', '--remove-orphans']);
  assert.equal(operationEnds.length, 1);
  assert.equal(operationEnds[0].messageKey, 'launcher_update_rollback_left_offline');
  assert.deepEqual(operationEnds[0].messageValues, { targetVersion: '1.4.0' });
});

test('update rejects when Omlorix is not running', async () => {
  const manager = await createManager();
  let latestReleaseCalled = false;

  manager.ensureServerHome = async () => {};
  manager.ensureGeneratedSecrets = async () => {};
  manager.validateProfileEnv = async () => {};
  manager.getState = async () => ({
    stack: {
      running: 0,
      services: [],
    },
  });
  manager.latestReleaseInfo = async () => {
    latestReleaseCalled = true;
    return {
      channel: 'stable',
      version: '1.2.3',
      manifest: null,
    };
  };

  await assert.rejects(
    () => manager.update({ skipBackup: true }),
    /Omlorix must be running before you can update it/i,
  );
  assert.equal(latestReleaseCalled, false);
});

test('managed proxy isolation detects stale non-loopback frontend publications', async () => {
  const manager = await createManager();
  let hostIp = '192.168.1.25';
  manager.execDocker = async (args) => {
    if (args.slice(-3).join(' ') === 'ps -q frontend') {
      return { ok: true, stdout: 'frontend-container\n', stderr: '' };
    }
    if (args[0] === 'inspect') {
      return {
        ok: true,
        stdout: JSON.stringify({ '80/tcp': [{ HostIp: hostIp, HostPort: '8080' }] }),
        stderr: '',
      };
    }
    return { ok: false, stdout: '', stderr: 'unexpected Docker call' };
  };

  assert.equal(await manager.runningFrontendHasPublicBinding(['compose']), true);
  hostIp = '127.0.0.1';
  assert.equal(await manager.runningFrontendHasPublicBinding(['compose']), false);
});

test('proxyStatus prefers the live runtime proxy config while running', async () => {
  const manager = await createManager();
  manager.proxy.status = () => ({
    config: {
      enabled: true,
      bindHost: '127.0.0.1',
      httpPort: '9191',
      httpsEnabled: false,
      httpsPort: '9443',
      publicUrl: 'http://127.0.0.1:9191',
      target: 'http://127.0.0.1:8080',
    },
    running: true,
    httpRunning: true,
    httpsRunning: false,
    startedAt: '2026-06-13T00:00:00.000Z',
    lastError: '',
  });

  const status = manager.proxyStatus({
    OMLORIX_LAUNCHER_PROXY_ENABLED: 'true',
    OMLORIX_LAUNCHER_PROXY_BIND: '0.0.0.0',
    OMLORIX_LAUNCHER_PROXY_HTTP_PORT: '8081',
  });

  assert.equal(status.config.publicUrl, 'http://127.0.0.1:9191');
  assert.equal(status.config.httpPort, '9191');
});

test('saveProxySettings rejects missing TLS files before writing proxy settings', async () => {
  const manager = await createManager();
  let wroteEnv = false;
  let startedProxy = false;
  let stoppedProxy = false;

  manager.ensureServerHome = async () => {};
  manager.readEnv = async () => ({
    OMLORIX_LAUNCHER_PROXY_ENABLED: 'true',
    OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED: 'true',
  });
  manager.writeEnv = async () => {
    wroteEnv = true;
  };
  manager.proxy.status = () => ({ running: false });
  manager.proxy.start = async () => {
    startedProxy = true;
  };
  manager.proxy.stop = async () => {
    stoppedProxy = true;
  };

  await assert.rejects(
    () => manager.saveProxySettings({
      enabled: true,
      httpsEnabled: true,
      tlsCertPath: '/definitely/missing/cert.pem',
      tlsKeyPath: '/definitely/missing/key.pem',
    }),
    (error) => {
      assert.equal(error.message, 'Proxy settings need attention.');
      assert.match(error.validationErrors.tlsCertPath, /file does not exist/i);
      assert.match(error.validationErrors.tlsKeyPath, /file does not exist/i);
      return true;
    },
  );

  assert.equal(wroteEnv, false);
  assert.equal(startedProxy, false);
  assert.equal(stoppedProxy, false);
});

test('saveProxySettings stores selected TLS file paths without copying certificate files', async () => {
  const manager = await createManager();
  const sourceDir = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-launcher-certs-'));
  const certPath = path.join(sourceDir, 'fullchain.pem');
  const keyPath = path.join(sourceDir, 'privkey.pem');
  const caPath = path.join(sourceDir, 'chain.pem');
  const writes = [];
  let savedSettings = null;

  await fs.writeFile(certPath, '-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----\n', 'utf8');
  await fs.writeFile(keyPath, '-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n', 'utf8');
  await fs.writeFile(caPath, '-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----\n', 'utf8');

  manager.ensureServerHome = async () => {};
  manager.readEnv = async () => ({
    FRONTEND_HTTP_HOST_PORT: '9080',
  });
  manager.writeEnv = async (updates) => {
    writes.push(updates);
  };
  manager.updateServerSettings = async (update) => {
    savedSettings = update({ schemaVersion: 2, updateChannel: 'stable' });
    return savedSettings;
  };
  manager.proxy.status = () => ({ running: false });
  manager.getState = async () => ({ ok: true });

  const result = await manager.saveProxySettings({
    enabled: true,
    trustProxyHeaders: true,
    trustedProxies: '10.0.0.0/8,fd00::/8',
    trustedHosts: 'chat.example.com,admin.example.com',
    uvicornForwardedAllowIps: '127.0.0.1,::1,10.0.0.10',
    rateLimitTrustedProxies: '10.0.0.20',
    authTrustedProxies: '10.0.0.30',
    rateLimitProxySettingsCacheSeconds: '120',
    frontendHttpHostBind: '0.0.0.0',
    frontendHttpHostPort: '9080',
    apiLbTraefikWebHostPort: '9082',
    apiLbTraefikDashboardHostPort: '9083',
    bindHost: '127.0.0.1',
    httpPort: '9440',
    autostart: false,
    httpsEnabled: true,
    httpsPort: '9443',
    redirectHttpToHttps: true,
    tlsCertPath: certPath,
    tlsKeyPath: keyPath,
    tlsCaPath: caPath,
  });

  assert.deepEqual(result, { ok: true });
  assert.equal(writes.length, 1);
  assert.equal(writes[0].TRUST_PROXY_HEADERS, 'true');
  assert.equal(writes[0].TRUSTED_PROXIES, '');
  assert.equal(writes[0].TRUSTED_HOSTS, 'chat.example.com,admin.example.com');
  assert.equal(writes[0].UVICORN_FORWARDED_ALLOW_IPS, '');
  assert.equal(writes[0].RATE_LIMIT_TRUSTED_PROXIES, '');
  assert.equal(writes[0].AUTH_TRUSTED_PROXIES, '');
  assert.equal(writes[0].RATE_LIMIT_PROXY_SETTINGS_CACHE_SECONDS, '120');
  assert.equal(writes[0].FRONTEND_HTTP_HOST_BIND, '127.0.0.1');
  assert.equal(writes[0].FRONTEND_TRUST_PROXY_HEADERS, 'true');
  assert.equal(writes[0].FRONTEND_HTTP_HOST_PORT, '9080');
  assert.equal(writes[0].API_LB_TRAEFIK_WEB_HOST_PORT, '9082');
  assert.equal(writes[0].API_LB_TRAEFIK_DASHBOARD_HOST_PORT, '9083');
  assert.equal(writes[0].OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH, undefined);
  assert.equal(savedSettings.proxy.tlsCertPath, certPath);
  assert.equal(savedSettings.proxy.tlsKeyPath, keyPath);
  assert.equal(savedSettings.proxy.tlsCaPath, caPath);
  assert.equal(await fs.readFile(certPath, 'utf8'), '-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----\n');
});

test('repairVisitorIps does not enable or start the launcher proxy', async () => {
  const manager = await createManager();
  let startProxyCalled = false;
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(manager.envFile, [
    'OMLORIX_LAUNCHER_PROXY_ENABLED=false',
    'OMLORIX_LAUNCHER_PROXY_AUTOSTART=false',
    '',
  ].join('\n'), 'utf8');
  manager.validateProfileEnv = async () => {};
  manager.createEnvBackup = async () => {};
  manager.getComposeServiceIp = async () => '172.18.0.10';
  manager.stackStatus = async () => ({ running: 0, services: [], healthy: false });
  manager.startProxy = async () => {
    startProxyCalled = true;
  };
  manager.getState = async () => ({ ok: true });

  await manager.repairVisitorIps();

  assert.equal(startProxyCalled, false);
  const saved = await fs.readFile(manager.envFile, 'utf8');
  assert.match(saved, /^TRUST_PROXY_HEADERS=true$/m);
  assert.match(saved, /^TRUSTED_PROXIES=172\.18\.0\.10\/32$/m);
  assert.match(saved, /^UVICORN_FORWARDED_ALLOW_IPS=172\.18\.0\.10$/m);
  assert.match(saved, /^FRONTEND_TRUST_PROXY_HEADERS=false$/m);
  assert.doesNotMatch(saved, /(?:^|[,=])(?:172\.18\.0\.1|172\.66\.0\.243)(?:[,\n]|$)/m);
});

test('repairVisitorIps trusts a sanitized launcher proxy path only behind loopback ingress', async () => {
  const manager = await createManager();
  const launcherSecret = 'b'.repeat(64);
  const dockerSteps = [];
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(manager.envFile, [
    'OMLORIX_LAUNCHER_PROXY_ENABLED=true',
    `OMLORIX_LAUNCHER_PROXY_SECRET=${launcherSecret}`,
    'FRONTEND_HTTP_HOST_BIND=0.0.0.0',
    '',
  ].join('\n'), 'utf8');
  manager.validateProfileEnv = async () => {};
  manager.createEnvBackup = async () => {};
  manager.getComposeServiceIp = async () => '172.18.0.10';
  manager.stackStatus = async () => ({
    running: 2,
    healthy: true,
    services: [
      { Service: 'frontend', ID: 'frontend-1', State: 'running' },
      { Service: 'fastapi', ID: 'fastapi-1', State: 'running' },
    ],
  });
  manager.prepareCompose = async () => ({ env: {} });
  manager.runDockerStep = async (...args) => dockerSteps.push(args);
  manager.waitForReady = async () => 'http://127.0.0.1:8080';
  manager.proxyStatus = () => ({ running: true, config: { enabled: true } });
  manager.verifyVisitorIpPath = async () => ({ verified: true });
  manager.getState = async () => ({ ok: true });

  await manager.repairVisitorIps();

  const saved = await fs.readFile(manager.envFile, 'utf8');
  assert.match(saved, /^TRUSTED_PROXIES=172\.18\.0\.10\/32$/m);
  assert.match(saved, /^RATE_LIMIT_TRUSTED_PROXIES=172\.18\.0\.10\/32$/m);
  assert.match(saved, /^AUTH_TRUSTED_PROXIES=172\.18\.0\.10\/32$/m);
  assert.match(saved, /^UVICORN_FORWARDED_ALLOW_IPS=172\.18\.0\.10$/m);
  assert.match(saved, /^FRONTEND_HTTP_HOST_BIND=127\.0\.0\.1$/m);
  assert.match(saved, /^FRONTEND_TRUST_PROXY_HEADERS=true$/m);
  assert.equal(dockerSteps.length, 1);
  assert.deepEqual(dockerSteps[0][1].slice(-3), [
    '--force-recreate', 'fastapi', 'frontend',
  ]);
});

test('visitor IP detection failure leaves the environment unchanged and not ready', async () => {
  const manager = await createManager();
  await fs.mkdir(manager.serverHome, { recursive: true });
  const original = [
    `OMLORIX_LAUNCHER_PROXY_SECRET=${'d'.repeat(64)}`,
    'TRUSTED_PROXIES=old-value',
    '',
  ].join('\n');
  await fs.writeFile(manager.envFile, original, 'utf8');
  await manager.writeServerSettings({
    schemaVersion: 2,
    updateChannel: 'stable',
    proxy: { enabled: true },
  });
  manager.validateProfileEnv = async () => {};
  manager.stackStatus = async () => ({ running: 2, healthy: true });
  manager.getComposeServiceIp = async () => '';
  manager.createEnvBackup = async () => {
    throw new Error('a detection failure must happen before backup or write');
  };

  await assert.rejects(
    () => manager.repairVisitorIps(),
    (error) => error?.code === 'VISITOR_IP_CONVERGENCE_FAILED',
  );
  assert.equal(await fs.readFile(manager.envFile, 'utf8'), original);
});

test('visitor IP convergence reapplies once when the frontend topology changes', async () => {
  const manager = await createManager();
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(manager.envFile, [
    'OMLORIX_LAUNCHER_PROXY_ENABLED=true',
    `OMLORIX_LAUNCHER_PROXY_SECRET=${'e'.repeat(64)}`,
    '',
  ].join('\n'), 'utf8');
  const addresses = ['172.18.0.10', '172.18.0.11', '172.18.0.11', '172.18.0.11'];
  let recreates = 0;
  manager.validateProfileEnv = async () => {};
  manager.createEnvBackup = async () => {};
  manager.stackStatus = async () => ({ running: 2, healthy: true, services: [] });
  manager.getComposeServiceIp = async () => addresses.shift();
  manager.prepareCompose = async () => ({ env: {} });
  manager.runDockerStep = async () => { recreates += 1; };
  manager.waitForReady = async () => 'http://127.0.0.1:8080';
  manager.proxyStatus = () => ({ running: true, config: { enabled: true } });
  manager.verifyVisitorIpPath = async () => ({ verified: true });
  manager.getState = async () => ({ ok: true });

  await manager.repairVisitorIps();

  assert.equal(recreates, 2);
  const saved = await fs.readFile(manager.envFile, 'utf8');
  assert.match(saved, /^TRUSTED_PROXIES=172\.18\.0\.11\/32$/m);
});

test('dockerStatus detects Docker Desktop installed in the Windows per-user location', async () => {
  const manager = await createManager();
  const originalLocalAppData = process.env.LOCALAPPDATA;
  const originalProgramFiles = process.env.ProgramFiles;
  const originalProgramFilesX86 = process.env['ProgramFiles(x86)'];
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-docker-desktop-path-'));
  const localAppData = path.join(tempDir, 'LocalAppData');
  const dockerDesktopExe = path.join(localAppData, 'Programs', 'DockerDesktop', 'Docker Desktop.exe');
  await fs.mkdir(path.dirname(dockerDesktopExe), { recursive: true });
  await fs.writeFile(dockerDesktopExe, '', 'utf8');

  manager.execDocker = async () => ({
    ok: false,
    code: -1,
    stdout: '',
    stderr: 'docker not found',
  });
  process.env.LOCALAPPDATA = localAppData;
  process.env.ProgramFiles = path.join(tempDir, 'ProgramFiles');
  process.env['ProgramFiles(x86)'] = path.join(tempDir, 'ProgramFilesX86');

  try {
    await withPlatform('win32', async () => {
      const status = await manager.dockerStatus();
      assert.equal(status.installed, true);
      assert.equal(status.canStartDesktop, true);
      assert.equal(status.message, 'Docker Desktop is installed, but the docker command is not available on PATH yet.');
    });
  } finally {
    restoreEnvValue('LOCALAPPDATA', originalLocalAppData);
    restoreEnvValue('ProgramFiles', originalProgramFiles);
    restoreEnvValue('ProgramFiles(x86)', originalProgramFilesX86);
  }
});

test('dockerCommand uses the bundled Docker Desktop CLI on Windows when PATH is stale', async () => {
  const originalLocalAppData = process.env.LOCALAPPDATA;
  const originalProgramFiles = process.env.ProgramFiles;
  const originalProgramW6432 = process.env.ProgramW6432;
  const originalProgramFilesX86 = process.env['ProgramFiles(x86)'];
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-docker-cli-path-'));
  const localAppData = path.join(tempDir, 'LocalAppData');
  const dockerDesktopExe = path.join(localAppData, 'Programs', 'DockerDesktop', 'Docker Desktop.exe');
  const dockerCliExe = path.join(localAppData, 'Programs', 'DockerDesktop', 'resources', 'bin', 'docker.exe');
  await fs.mkdir(path.dirname(dockerDesktopExe), { recursive: true });
  await fs.mkdir(path.dirname(dockerCliExe), { recursive: true });
  await fs.writeFile(dockerDesktopExe, '', 'utf8');
  await fs.writeFile(dockerCliExe, '', 'utf8');

  process.env.LOCALAPPDATA = localAppData;
  process.env.ProgramFiles = path.join(tempDir, 'ProgramFiles');
  process.env.ProgramW6432 = path.join(tempDir, 'ProgramW6432');
  process.env['ProgramFiles(x86)'] = path.join(tempDir, 'ProgramFilesX86');

  try {
    await withPlatform('win32', async () => {
      assert.equal(dockerCommand(), dockerCliExe);
    });
  } finally {
    restoreEnvValue('LOCALAPPDATA', originalLocalAppData);
    restoreEnvValue('ProgramFiles', originalProgramFiles);
    restoreEnvValue('ProgramW6432', originalProgramW6432);
    restoreEnvValue('ProgramFiles(x86)', originalProgramFilesX86);
  }
});

test('dockerSpawnEnv exposes Docker Desktop credential helpers on Windows', async () => {
  const command = path.join('C:\\', 'Users', 'Example', 'AppData', 'Local', 'Programs', 'DockerDesktop', 'resources', 'bin', 'docker.exe');
  const dockerBinDir = path.dirname(command);
  const existingPath = path.join('C:\\', 'Windows', 'System32');

  await withPlatform('win32', async () => {
    const env = dockerSpawnEnv(command, { PATH: existingPath });
    const entries = env.PATH.split(';');
    assert.equal(entries[0], dockerBinDir);
    assert.equal(entries[1], existingPath);
  });
});

test('dockerSpawnEnv exposes Docker Desktop CLI plugins on macOS', async () => {
  // Use genuine POSIX fixture paths even when this suite runs on Windows. The
  // platform override changes application behavior, not Node's host path API.
  const dockerBinDir = '/Applications/Docker.app/Contents/Resources/bin';
  const dockerPluginDir = '/Applications/Docker.app/Contents/Resources/cli-plugins';
  const command = path.posix.join(dockerBinDir, 'docker');
  const existingPath = '/usr/bin';

  await withPlatform('darwin', async () => {
    const env = dockerSpawnEnv(
      command,
      { PATH: existingPath },
      { existsSync: (candidate) => candidate === dockerPluginDir },
    );
    const entries = env.PATH.split(path.delimiter);
    assert.equal(entries[0], dockerBinDir);
    assert.equal(entries[1], dockerPluginDir);
    assert.equal(entries[2], existingPath);
  });
});

test('dockerRegistryAccessErrorMessage explains GHCR authorization failures', () => {
  const message = dockerRegistryAccessErrorMessage(
    'Image ghcr.io/phinaldoo/omlorix-backend:0.9.8 Error error from registry: unauthorized',
    { OMLORIX_VERSION: '0.9.8' },
  );

  assert.match(message, /could not pull the official Omlorix images/i);
  assert.match(message, /0\.9\.8/);
  assert.match(message, /docker login ghcr\.io/);
});

test('dockerRegistryAccessErrorMessage explains GHCR forbidden manifest failures', () => {
  const message = dockerRegistryAccessErrorMessage(
    'failed to resolve reference "ghcr.io/phinaldoo/omlorix-backend:0.9.8": unexpected status from HEAD request to https://ghcr.io/v2/phinaldoo/omlorix-backend/manifests/0.9.8: 403 Forbidden',
    { OMLORIX_VERSION: '0.9.8' },
  );

  assert.match(message, /registry rejected access/i);
  assert.match(message, /ghcr\.io\/phinaldoo\/omlorix-backend/);
  assert.match(message, /read the packages/);
});

test('saveSettings persists dashboard infrastructure toggles to .env', async () => {
  const manager = await createManager();
  manager.getState = async () => ({ stack: { running: 0, services: [] } });

  await manager.saveSettings({
    mode: 'dev',
    composeProjectName: 'omlorix-prod',
    useBundledDB: false,
    useBundledRedis: false,
    usePgbouncer: true,
    useBundledStorage: true,
    jwtSecretKey: 'x'.repeat(64),
    encryptionKey: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
    passwordResetSalt: 'reset-salt-value',
  });

  const env = await manager.readEnv();
  assert.equal(env.MODE, 'dev');
  assert.equal(env.COMPOSE_PROJECT_NAME, 'omlorix-prod');
  assert.equal(env.OMLORIX_USE_BUNDLED_DB, 'false');
  assert.equal(env.OMLORIX_USE_BUNDLED_REDIS, 'false');
  assert.equal(env.OMLORIX_USE_PGBOUNCER, 'false');
  assert.equal(env.OMLORIX_USE_BUNDLED_STORAGE, 'true');
  assert.equal(env.FILE_STORAGE_PROVIDER, 's3');
  assert.equal(env.JWT_SECRET_KEY, 'x'.repeat(64));
  assert.equal(env.ENCRYPTION_KEY, 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=');
  assert.equal(env.PASSWORD_RESET_IDENTIFIER_HASH_SALT, 'reset-salt-value');
});

test('saveSettings persists direct database and Redis connection fields to .env', async () => {
  const manager = await createManager();
  manager.getState = async () => ({ stack: { running: 0, services: [] } });

  await manager.saveSettings({
    useBundledDB: false,
    // Redis Off canonicalizes a stale bundled flag to false while preserving
    // both stored credentials for a future mode change.
    useBundledRedis: true,
    databaseUrl: 'postgresql://omlorix:secret@db.example.com:5432/omlorix',
    databaseName: 'omlorix',
    databaseUser: 'omlorix',
    databasePassword: 'database-secret',
    databaseHost: 'db.internal',
    databasePort: '5433',
    databaseSchema: 'omlorix_app',
    databaseAuditLogSchema: 'omlorix_audit',
    databaseLogsSchema: 'omlorix_logs',
    autoCreateDatabases: false,
    databaseHostOverride: 'postgres',
    databasePortOverride: '5432',
    devDatabaseHostPort: '15432',
    redisEnabled: false,
    redisUrl: 'rediss://:secret@redis.example.com:6380/0',
    redisPassword: 'redis-secret',
    devRedisHostPort: '16379',
  });

  const env = await manager.readEnv();
  assert.equal(env.OMLORIX_USE_BUNDLED_DB, 'false');
  assert.equal(env.OMLORIX_USE_BUNDLED_REDIS, 'false');
  assert.equal(env.DATABASE_URL, 'postgresql://omlorix:secret@db.example.com:5432/omlorix');
  assert.equal(env.DATABASE_NAME, 'omlorix');
  assert.equal(env.DATABASE_USER, 'omlorix');
  assert.equal(env.DATABASE_PASSWORD, 'database-secret');
  assert.equal(env.DATABASE_HOST, 'db.internal');
  assert.equal(env.DATABASE_PORT, '5433');
  assert.equal(env.DATABASE_SCHEMA, 'omlorix_app');
  assert.equal(env.DATABASE_AUDIT_LOG_SCHEMA, 'omlorix_audit');
  assert.equal(env.DATABASE_LOGS_SCHEMA, 'omlorix_logs');
  assert.equal(env.OMLORIX_AUTO_CREATE_DATABASES, 'false');
  assert.equal(env.DATABASE_HOST_OVERRIDE, 'postgres');
  assert.equal(env.DATABASE_PORT_OVERRIDE, '5432');
  assert.equal(env.DEV_DATABASE_HOST_PORT, '15432');
  assert.equal(env.REDIS_ENABLED, 'false');
  assert.equal(env.REDIS_URL, 'rediss://:secret@redis.example.com:6380/0');
  assert.equal(env.REDIS_PASSWORD, 'redis-secret');
  assert.equal(env.DEV_REDIS_HOST_PORT, '16379');
});

test('saveSettings persists database traffic manager settings to .env', async () => {
  const manager = await createManager();
  manager.getState = async () => ({ stack: { running: 0, services: [] } });

  await manager.saveSettings({
    useBundledDB: true,
    usePgbouncer: true,
    pgbouncerPoolMode: 'session',
    pgbouncerMaxClientConn: '250',
    pgbouncerDefaultPoolSize: '50',
    pgbouncerReservePoolSize: '12',
    pgbouncerHostBind: '127.0.0.1',
    pgbouncerHostPort: '7432',
  });

  const env = await manager.readEnv();
  assert.equal(env.OMLORIX_USE_BUNDLED_DB, 'true');
  assert.equal(env.OMLORIX_USE_PGBOUNCER, 'true');
  assert.equal(env.DATABASE_URL, '');
  assert.equal(env.DATABASE_HOST_OVERRIDE, 'pgbouncer');
  assert.equal(env.DATABASE_PORT_OVERRIDE, '5432');
  assert.equal(env.DATABASE_MIGRATION_HOST_OVERRIDE, 'postgres');
  assert.equal(env.DATABASE_MIGRATION_PORT_OVERRIDE, '5432');
  assert.equal(env.PGBOUNCER_POOL_MODE, 'session');
  assert.equal(env.PGBOUNCER_MAX_CLIENT_CONN, '250');
  assert.equal(env.PGBOUNCER_DEFAULT_POOL_SIZE, '50');
  assert.equal(env.PGBOUNCER_RESERVE_POOL_SIZE, '12');
  assert.equal(env.PGBOUNCER_HOST_BIND, '127.0.0.1');
  assert.equal(env.PGBOUNCER_HOST_PORT, '7432');

  await assert.rejects(
    () => manager.saveSettings({ pgbouncerPoolMode: 'statement' }),
    /Choose one of: transaction, session/,
  );

  await manager.saveSettings({
    useBundledDB: true,
    usePgbouncer: false,
    databaseHostOverride: 'pgbouncer',
    databasePortOverride: '7432',
  });
  const directEnv = await manager.readEnv();
  assert.equal(directEnv.OMLORIX_USE_PGBOUNCER, 'false');
  assert.equal(directEnv.DATABASE_HOST_OVERRIDE, 'postgres');
  assert.equal(directEnv.DATABASE_PORT_OVERRIDE, '5432');
});

test('saveSettings persists file storage settings to .env', async () => {
  const manager = await createManager();
  manager.getState = async () => ({ stack: { running: 0, services: [] } });

  await manager.saveSettings({
    useBundledStorage: false,
    minioRootUser: 'minio-admin',
    minioRootPassword: 'minio-secret',
    minioApiHostBind: '127.0.0.2',
    minioApiHostPort: '9100',
    minioConsoleHostBind: '127.0.0.3',
    minioConsoleHostPort: '9101',
    fileStorageProvider: 'webdav',
    fileStorageLocalBasePath: '/app/data/userFiles',
    fileStorageS3Bucket: 'omlorix-user-files',
    fileStorageS3Region: 'us-east-1',
    fileStorageS3Prefix: 'uploads',
    fileStorageS3EndpointUrl: 'https://s3.example.com',
    fileStorageS3AccessKeyId: 'access-key',
    fileStorageS3SecretAccessKey: 'secret-key',
    fileStorageS3SessionToken: 'session-token',
    fileStorageGcsBucket: 'gcs-bucket',
    fileStorageGcsProject: 'gcs-project',
    fileStorageGcsPrefix: 'gcs-prefix',
    fileStorageGcsCredentialsJson: '{"type":"service_account"}',
    fileStorageAzureContainer: 'azure-container',
    fileStorageAzurePrefix: 'azure-prefix',
    fileStorageAzureConnectionString: 'DefaultEndpointsProtocol=https;',
    fileStorageAzureAccountUrl: 'https://account.blob.core.windows.net',
    fileStorageAzureCredential: 'azure-credential',
    fileStorageWebdavUrl: 'https://cloud.example.com/dav',
    fileStorageWebdavUsername: 'webdav-user',
    fileStorageWebdavPassword: 'webdav-secret',
    fileStorageWebdavPrefix: 'webdav-prefix',
    fileStorageWebdavVerifySsl: false,
    fileStorageWebdavTimeout: '45',
  });

  const env = await manager.readEnv();
  assert.equal(env.OMLORIX_USE_BUNDLED_STORAGE, 'false');
  assert.equal(env.MINIO_ROOT_USER, 'minio-admin');
  assert.equal(env.MINIO_ROOT_PASSWORD, 'minio-secret');
  assert.equal(env.MINIO_API_HOST_BIND, '127.0.0.2');
  assert.equal(env.MINIO_API_HOST_PORT, '9100');
  assert.equal(env.MINIO_CONSOLE_HOST_BIND, '127.0.0.3');
  assert.equal(env.MINIO_CONSOLE_HOST_PORT, '9101');
  assert.equal(env.FILE_STORAGE_PROVIDER, 'webdav');
  assert.equal(env.FILE_STORAGE_LOCAL_BASE_PATH, '/app/data/userFiles');
  assert.equal(env.FILE_STORAGE_S3_BUCKET, 'omlorix-user-files');
  assert.equal(env.FILE_STORAGE_S3_REGION, 'us-east-1');
  assert.equal(env.FILE_STORAGE_S3_PREFIX, 'uploads');
  assert.equal(env.FILE_STORAGE_S3_ENDPOINT_URL, 'https://s3.example.com');
  assert.equal(env.FILE_STORAGE_S3_ACCESS_KEY_ID, 'access-key');
  assert.equal(env.FILE_STORAGE_S3_SECRET_ACCESS_KEY, 'secret-key');
  assert.equal(env.FILE_STORAGE_S3_SESSION_TOKEN, 'session-token');
  assert.equal(env.FILE_STORAGE_GCS_BUCKET, 'gcs-bucket');
  assert.equal(env.FILE_STORAGE_GCS_PROJECT, 'gcs-project');
  assert.equal(env.FILE_STORAGE_GCS_PREFIX, 'gcs-prefix');
  assert.equal(env.FILE_STORAGE_GCS_CREDENTIALS_JSON, '{"type":"service_account"}');
  assert.equal(env.FILE_STORAGE_AZURE_CONTAINER, 'azure-container');
  assert.equal(env.FILE_STORAGE_AZURE_PREFIX, 'azure-prefix');
  assert.equal(env.FILE_STORAGE_AZURE_CONNECTION_STRING, 'DefaultEndpointsProtocol=https;');
  assert.equal(env.FILE_STORAGE_AZURE_ACCOUNT_URL, 'https://account.blob.core.windows.net');
  assert.equal(env.FILE_STORAGE_AZURE_CREDENTIAL, 'azure-credential');
  assert.equal(env.FILE_STORAGE_WEBDAV_URL, 'https://cloud.example.com/dav');
  assert.equal(env.FILE_STORAGE_WEBDAV_USERNAME, 'webdav-user');
  assert.equal(env.FILE_STORAGE_WEBDAV_PASSWORD, 'webdav-secret');
  assert.equal(env.FILE_STORAGE_WEBDAV_PREFIX, 'webdav-prefix');
  assert.equal(env.FILE_STORAGE_WEBDAV_VERIFY_SSL, 'false');
  assert.equal(env.FILE_STORAGE_WEBDAV_TIMEOUT, '45');
});

test('saveSettings persists observability settings to .env', async () => {
  const manager = await createManager();
  manager.getState = async () => ({ stack: { running: 0, services: [] } });

  await manager.saveSettings({
    otelEnabled: true,
    otelServiceName: 'omlorix-api',
    otelExporterOtlpEndpoint: 'https://collector.example.com:4317',
    otelExporterOtlpInsecure: true,
    otelTracesEnabled: false,
    otelTracesSampler: 'always_on',
    otelTracesSamplerArg: '0.25',
    otelMetricsEnabled: true,
    otelPrometheusExporterEnabled: false,
    otelLogsEnabled: false,
    otelInstrumentFastapi: true,
    otelInstrumentSqlalchemy: false,
    otelInstrumentHttpClients: false,
    otelSqlCommenterEnabled: true,
    otelCaptureHttpRoute: true,
    otelCaptureHttpUserAgent: true,
    otelHashHttpUserAgent: false,
    otelGrpcHostBind: '127.0.0.1',
    otelGrpcHostPort: '4319',
    otelHttpHostBind: '127.0.0.1',
    otelHttpHostPort: '4320',
    otelPrometheusHostBind: '127.0.0.1',
    otelPrometheusHostPort: '8890',
    otelHealthcheckHostBind: '127.0.0.1',
    otelHealthcheckHostPort: '13134',
    jaegerUiHostBind: '127.0.0.1',
    jaegerUiHostPort: '16687',
    jaegerCollectorHostBind: '127.0.0.1',
    jaegerCollectorHostPort: '14269',
    prometheusHostBind: '127.0.0.1',
    prometheusHostPort: '9091',
    alertmanagerHostBind: '127.0.0.1',
    alertmanagerHostPort: '9094',
    grafanaHostBind: '127.0.0.1',
    grafanaHostPort: '3002',
    grafanaAdminUser: 'grafana-admin',
    grafanaAdminPassword: 'grafana-secret',
    grafanaRootUrl: 'https://grafana.example.com',
    postgresExporterDataSourceUri: 'postgres:5432/omlorix?sslmode=disable',
    postgresExporterDataSourceUser: 'postgres-exporter',
    postgresExporterDataSourcePass: 'postgres-exporter-secret',
    redisExporterAddr: 'redis://redis:6379',
  });

  const env = await manager.readEnv();
  assert.equal(env.OTEL_ENABLED, 'true');
  assert.equal(env.OTEL_SERVICE_NAME, 'omlorix-api');
  assert.equal(env.OTEL_EXPORTER_OTLP_ENDPOINT, 'https://collector.example.com:4317');
  assert.equal(env.OTEL_EXPORTER_OTLP_INSECURE, 'true');
  assert.equal(env.OTEL_TRACES_ENABLED, 'false');
  assert.equal(env.OTEL_TRACES_SAMPLER, 'always_on');
  assert.equal(env.OTEL_TRACES_SAMPLER_ARG, '0.25');
  assert.equal(env.OTEL_METRICS_ENABLED, 'true');
  assert.equal(env.OTEL_PROMETHEUS_EXPORTER_ENABLED, 'false');
  assert.equal(env.OTEL_LOGS_ENABLED, 'false');
  assert.equal(env.OTEL_INSTRUMENT_FASTAPI, 'true');
  assert.equal(env.OTEL_INSTRUMENT_SQLALCHEMY, 'false');
  assert.equal(env.OTEL_INSTRUMENT_HTTP_CLIENTS, 'false');
  assert.equal(env.OTEL_SQL_COMMENTER_ENABLED, 'true');
  assert.equal(env.OTEL_CAPTURE_HTTP_ROUTE, 'true');
  assert.equal(env.OTEL_CAPTURE_HTTP_USER_AGENT, 'true');
  assert.equal(env.OTEL_HASH_HTTP_USER_AGENT, 'false');
  assert.equal(env.OTEL_GRPC_HOST_BIND, '127.0.0.1');
  assert.equal(env.OTEL_GRPC_HOST_PORT, '4319');
  assert.equal(env.OTEL_HTTP_HOST_BIND, '127.0.0.1');
  assert.equal(env.OTEL_HTTP_HOST_PORT, '4320');
  assert.equal(env.OTEL_PROMETHEUS_HOST_BIND, '127.0.0.1');
  assert.equal(env.OTEL_PROMETHEUS_HOST_PORT, '8890');
  assert.equal(env.OTEL_HEALTHCHECK_HOST_BIND, '127.0.0.1');
  assert.equal(env.OTEL_HEALTHCHECK_HOST_PORT, '13134');
  assert.equal(env.JAEGER_UI_HOST_BIND, '127.0.0.1');
  assert.equal(env.JAEGER_UI_HOST_PORT, '16687');
  assert.equal(env.JAEGER_COLLECTOR_HOST_BIND, '127.0.0.1');
  assert.equal(env.JAEGER_COLLECTOR_HOST_PORT, '14269');
  assert.equal(env.PROMETHEUS_HOST_BIND, '127.0.0.1');
  assert.equal(env.PROMETHEUS_HOST_PORT, '9091');
  assert.equal(env.ALERTMANAGER_HOST_BIND, '127.0.0.1');
  assert.equal(env.ALERTMANAGER_HOST_PORT, '9094');
  assert.equal(env.GRAFANA_HOST_BIND, '127.0.0.1');
  assert.equal(env.GRAFANA_HOST_PORT, '3002');
  assert.equal(env.GRAFANA_ADMIN_USER, 'grafana-admin');
  assert.equal(env.GRAFANA_ADMIN_PASSWORD, 'grafana-secret');
  assert.equal(env.GRAFANA_ROOT_URL, 'https://grafana.example.com');
  assert.equal(env.POSTGRES_EXPORTER_DATA_SOURCE_URI, 'postgres:5432/omlorix?sslmode=disable');
  assert.equal(env.POSTGRES_EXPORTER_DATA_SOURCE_USER, 'postgres-exporter');
  assert.equal(env.POSTGRES_EXPORTER_DATA_SOURCE_PASS, 'postgres-exporter-secret');
  assert.equal(env.REDIS_EXPORTER_ADDR, 'redis://redis:6379');
});

test('composeArgs enables host metrics only through the Linux observability overlay', () => {
  const env = {
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
    OTEL_ENABLED: 'true',
  };
  const macOSArgs = composeArgs('/tmp/omlorix', env, 'darwin');
  const windowsArgs = composeArgs('/tmp/omlorix', env, 'win32');
  const linuxArgs = composeArgs('/tmp/omlorix', env, 'linux');

  for (const args of [macOSArgs, windowsArgs, linuxArgs]) {
    assert(args.includes(path.join('/tmp/omlorix', 'docker-compose.observability.yml')));
  }
  assert(!macOSArgs.includes(path.join('/tmp/omlorix', 'docker-compose.observability-linux.yml')));
  assert(!windowsArgs.includes(path.join('/tmp/omlorix', 'docker-compose.observability-linux.yml')));
  assert(linuxArgs.includes(path.join('/tmp/omlorix', 'docker-compose.observability-linux.yml')));

  assert.deepEqual(observabilityCapability(readEnvToggles(env), 'darwin'), {
    enabled: true,
    hostMetrics: { available: false, enabled: false, reason: 'linux_only' },
  });
  assert.deepEqual(observabilityCapability(readEnvToggles(env), 'linux'), {
    enabled: true,
    hostMetrics: { available: true, enabled: true, reason: '' },
  });
  assert(!expectedServiceNamesFromToggles(readEnvToggles(env), 'darwin').includes('node-exporter'));
  assert(expectedServiceNamesFromToggles(readEnvToggles(env), 'linux').includes('node-exporter'));
});

test('Launcher mutations refuse a CLI-owned cross-process lock', async (t) => {
  const manager = await createManager();
  t.after(() => fs.rm(path.dirname(manager.serverHome), { recursive: true, force: true }));
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(path.join(manager.serverHome, '.omlorix-server.lock'), 'pid=123 command=update\n');

  await assert.rejects(
    manager.saveSettings({ mode: 'dev' }),
    /Another Omlorix server operation is already active/,
  );
});

test('raw Launcher environment writes enforce shared topology invariants', async (t) => {
  const manager = await createManager();
  t.after(() => fs.rm(path.dirname(manager.serverHome), { recursive: true, force: true }));
  await fs.mkdir(manager.serverHome, { recursive: true });
  await manager.writeEnvContent([
    'OMLORIX_USE_BUNDLED_DB=false',
    'OMLORIX_USE_PGBOUNCER=true',
    'REDIS_ENABLED=false',
    'OMLORIX_USE_BUNDLED_REDIS=true',
    'OMLORIX_USE_BUNDLED_STORAGE=true',
    'FILE_STORAGE_PROVIDER=local',
    '',
  ].join('\n'));

  const env = await manager.readEnv();
  assert.equal(env.OMLORIX_USE_PGBOUNCER, 'false');
  assert.equal(env.OMLORIX_USE_BUNDLED_REDIS, 'false');
  assert.equal(env.FILE_STORAGE_PROVIDER, 's3');
});

test('composeArgs exposes bundled database ports in dev mode', () => {
  const args = composeArgs('/tmp/omlorix', {
    MODE: 'dev',
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
  });

  assert(args.includes(path.join('/tmp/omlorix', 'docker-compose.dev-ports.yml')));
});

test('composeArgs skips dev port overlay for fully external managed cloud mode', () => {
  const args = composeArgs('/tmp/omlorix', {
    MODE: 'dev',
    OMLORIX_USE_BUNDLED_DB: 'false',
    OMLORIX_USE_BUNDLED_REDIS: 'false',
  });

  assert(!args.includes(path.join('/tmp/omlorix', 'docker-compose.dev-ports.yml')));
});

test('composeArgs keeps the server topology for bundled storage with Redis Off', () => {
  const args = composeArgs('/tmp/omlorix', {
    OMLORIX_USE_BUNDLED_DB: 'false',
    OMLORIX_USE_BUNDLED_REDIS: 'false',
    REDIS_ENABLED: 'false',
    OMLORIX_USE_BUNDLED_STORAGE: 'true',
  });

  assert(args.includes(path.join('/tmp/omlorix', 'docker-compose.server.yml')));
  assert(!args.includes(path.join('/tmp/omlorix', 'docker-compose.managed-cloud.yml')));
  assert(args.includes('bundled-storage'));
});

test('Redis Off omits Redis profiles, workers, and credential requirements', async () => {
  const toggles = readEnvToggles({
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'false',
    REDIS_ENABLED: 'false',
  });
  const args = composeArgs('/tmp/omlorix', {
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'false',
    REDIS_ENABLED: 'false',
  });

  assert.equal(toggles.redisEnabled, false);
  assert(!args.includes('redis-enabled'));
  assert(!args.includes('bundled-redis'));
  assert.deepEqual(
    expectedServiceNamesFromToggles(toggles),
    ['postgres', 'email_worker', ...DEDICATED_WORKER_SERVICES, 'fastapi', 'frontend'],
  );

  const manager = await createManager();
  manager.readEnv = async () => ({
    MODE: 'production',
    JWT_SECRET_KEY: 'x'.repeat(64),
    LOG_IP_HASH_SALT: 'i'.repeat(32),
    ENCRYPTION_KEY: 'test-encryption-key',
    DATABASE_PASSWORD: 'database-secret',
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'false',
    REDIS_ENABLED: 'false',
    FILE_STORAGE_PROVIDER: 'local',
  });
  await assert.doesNotReject(() => manager.validateProfileEnv());
});

test('expected service discovery excludes one-shot jobs and creates missing rows', () => {
  const expected = parseComposeServiceNames([
    'postgres',
    'migrate',
    'redis',
    'fastapi',
    'frontend',
    'metrics_token',
  ].join('\n'));
  const summary = mergeExpectedComposeServices(expected, [
    { Service: 'postgres', State: 'running', Health: 'healthy' },
    { Service: 'fastapi', State: 'running', Health: 'healthy' },
    { Service: 'frontend', State: 'exited', Status: 'Exited (1)' },
  ], { expectedKnown: true });

  assert.deepEqual(expected, ['postgres', 'redis', 'fastapi', 'frontend']);
  assert.deepEqual(summary.services.map((service) => service.Service), expected);
  assert.equal(summary.running, 2);
  assert.equal(summary.total, 4);
  assert.equal(summary.present, 3);
  assert.equal(summary.missing, 1);
  assert.equal(summary.notRunning, 2);
  assert.equal(summary.healthIssues, 0);
  assert.deepEqual(
    summary.services.find((service) => service.Service === 'redis'),
    {
      Service: 'redis',
      State: 'not_created',
      Status: '',
      Health: '',
      Expected: true,
      Missing: true,
    },
  );
});

test('runtime service discovery never indexes container names as service names', () => {
  const summary = mergeExpectedComposeServices([], [
    { Name: 'omlorix-fastapi-1', State: 'running', Health: 'healthy' },
  ], { expectedKnown: false });

  assert.equal(summary.runtimeReadFailed, true);
  assert.equal(summary.total, 0);
  assert.deepEqual(summary.services, []);
});

test('settings provide an expected service fallback when Compose config is unavailable', () => {
  assert.deepEqual(
    expectedServiceNamesFromToggles({
      useBundledDB: true,
      useBundledRedis: true,
      usePgbouncer: false,
      useBundledStorage: false,
      observabilityEnabled: false,
    }),
    [
      'postgres',
      'redis',
      'automation_scheduler',
      'automation_worker',
      'email_worker',
      ...DEDICATED_WORKER_SERVICES,
      'fastapi',
      'frontend',
    ],
  );
});

test('stackStatus combines configured services without preparing Docker networks', async () => {
  const manager = await createManager();
  const commands = [];
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(path.join(manager.serverHome, 'docker-compose.launcher-services.yml'), 'services: {}\n');
  manager.ensureServerHome = async () => {};
  manager.ensureLauncherServicesNetwork = async () => {
    throw new Error('status polling must not prepare Docker networks');
  };
  manager.readEnv = async () => ({
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
  });
  manager.resolveUrl = () => '';
  manager.getBackendProxyTrustRuntime = async () => {
    throw new Error('lightweight status must skip proxy diagnostics');
  };
  manager.execDocker = async (args) => {
    commands.push(args);
    if (args.includes('config')) {
      return {
        ok: true,
        stdout: 'postgres\nmigrate\nredis\nfastapi\nfrontend\n',
        stderr: '',
      };
    }
    return {
      ok: true,
      stdout: JSON.stringify([
        { Service: 'postgres', State: 'running', Health: 'healthy' },
        { Service: 'fastapi', State: 'running', Health: 'healthy' },
        { Service: 'frontend', State: 'exited', Status: 'Exited (1)' },
      ]),
      stderr: '',
    };
  };

  const stack = await manager.stackStatus({ includeDiagnostics: false });

  assert(commands.some((args) => args.slice(-2).join(' ') === 'config --services'));
  assert(commands.some((args) => args.slice(-4).join(' ') === 'ps --all --format json'));
  assert.equal(stack.running, 2);
  assert.equal(stack.total, 4);
  assert.equal(stack.missing, 1);
  assert.equal(stack.expectedKnown, true);
  assert.equal(stack.expectedSource, 'compose');
  assert.equal(stack.healthy, false);
  assert.equal(stack.clientIp, undefined);
  assert.equal(stack.backendProxyTrust, undefined);
});

test('launcher readiness waits for services after the endpoint responds', async () => {
  const manager = await createManager();
  manager.readEnv = async () => ({ FRONTEND_HTTP_HOST_PORT: '8080' });
  const snapshots = [
    {
      total: 2, running: 2, missing: 0, healthIssues: 1,
      endpointReady: true, healthy: false, httpStatus: 200, composeError: '',
    },
    {
      total: 2, running: 2, missing: 0, healthIssues: 0,
      endpointReady: true, healthy: true, httpStatus: 200, composeError: '',
    },
  ];
  let calls = 0;
  manager.stackStatus = async () => snapshots[Math.min(calls++, snapshots.length - 1)];

  const readyUrl = await manager.waitForReady(100, 0);

  assert.equal(readyUrl, 'http://localhost:8080/ready');
  assert.equal(calls, 2);
  assert.equal(stackReadinessHealthy(snapshots[0], true), false);
  assert.equal(stackReadinessHealthy(snapshots[1], true), true);
});

test('stackStatus marks expected services unknown when Compose config fails', async () => {
  const manager = await createManager();
  manager.ensureServerHome = async () => {};
  manager.readEnv = async () => ({
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
  });
  manager.resolveUrl = () => '';
  manager.execDocker = async (args) => {
    if (args.includes('config')) {
      return { ok: false, stdout: '', stderr: 'config failed' };
    }
    return {
      ok: true,
      stdout: JSON.stringify([
        { Service: 'fastapi', Name: 'omlorix-fastapi-1', State: 'running' },
        { Service: 'custom_service', Name: 'omlorix-custom_service-1', State: 'running' },
      ]),
      stderr: '',
    };
  };

  const stack = await manager.stackStatus({ includeDiagnostics: false });

  assert.equal(stack.expectedKnown, false);
  assert(stack.services.some((service) => service.Service === 'custom_service'));
  assert.match(stack.composeError, /config failed/);
});

test('helper connection repairs a development backend selected by the published frontend port', async () => {
  const manager = await createManager();
  const commands = [];
  manager.ensureLauncherServicesNetwork = async () => ({ created: false });
  manager.readEnv = async () => ({
    COMPOSE_PROJECT_NAME: 'saved-launcher-project',
    FRONTEND_HTTP_HOST_PORT: '8080',
  });
  manager.execDocker = async (args) => {
    commands.push(args);
    if (args[0] === 'compose') return { ok: true, stdout: '', stderr: '' };
    if (args[0] === 'ps' && args.includes('label=com.docker.compose.service=frontend')) {
      return { ok: true, stdout: 'frontend-container\nwrong-host-port-container\n', stderr: '' };
    }
    if (args[0] === 'inspect' && args.includes('{{json .NetworkSettings.Ports}}')) {
      if (args.includes('frontend-container')) {
        return { ok: true, stdout: '{"80/tcp":[{"HostIp":"0.0.0.0","HostPort":"8080"}]}\n', stderr: '' };
      }
      return { ok: true, stdout: '{"8080/tcp":[{"HostIp":"0.0.0.0","HostPort":"18080"}]}\n', stderr: '' };
    }
    if (args[0] === 'inspect') return { ok: true, stdout: 'development-project\n', stderr: '' };
    if (args[0] === 'ps' && args.includes('label=com.docker.compose.service=fastapi')) {
      return { ok: true, stdout: 'backend-container\n', stderr: '' };
    }
    if (args[0] === 'network' && args[1] === 'connect') {
      return { ok: true, stdout: '', stderr: '' };
    }
    return { ok: false, stdout: '', stderr: 'unexpected command' };
  };

  const result = await manager.attachRunningBackendToLauncherServicesNetwork();

  assert.deepEqual(result, { attached: true, running: true });
  assert.ok(commands.some((args) => (
    args[0] === 'ps'
    && args.includes('label=com.docker.compose.service=frontend')
    && !args.some((arg) => String(arg).startsWith('publish='))
  )));
  assert.ok(commands.some((args) => (
    args[0] === 'inspect'
    && args.includes('{{json .NetworkSettings.Ports}}')
    && args.includes('frontend-container')
  )));
  assert.ok(commands.some((args) => (
    args[0] === 'inspect'
    && args.includes('{{json .NetworkSettings.Ports}}')
    && args.includes('wrong-host-port-container')
  )));
  assert.ok(commands.some((args) => (
    args[0] === 'network'
    && args[1] === 'connect'
    && args[2] === 'omlorix-launcher-services'
    && args[3] === 'backend-container'
  )));
});

test('ensureGeneratedSecrets synchronizes and encodes stale bundled Redis credentials', async () => {
  const manager = await createManager();
  await manager.writeEnv({
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
    OMLORIX_USE_PGBOUNCER: 'false',
    OMLORIX_USE_BUNDLED_STORAGE: 'false',
    JWT_SECRET_KEY: 'x'.repeat(64),
    ENCRYPTION_KEY: 'secret-fernet-key',
    DATABASE_PASSWORD: 'database-secret',
    REDIS_PASSWORD: 'operator#redis:secret@word',
    REDIS_URL: 'redis://:old-password@redis:6379/0',
  });

  await manager.ensureGeneratedSecrets();

  const env = await manager.readEnv();
  assert.equal(env.REDIS_URL, 'redis://:operator%23redis%3Asecret%40word@redis:6379/0');
});

test('ensureGeneratedSecrets repairs legacy PgBouncer routing and URL bypass', async () => {
  const manager = await createManager();
  await fs.mkdir(manager.serverHome, { recursive: true });
  await fs.writeFile(manager.envFile, [
    'OMLORIX_USE_BUNDLED_DB=true',
    'OMLORIX_USE_PGBOUNCER=true',
    'DATABASE_URL=postgresql://external.example/other',
    'DATABASE_HOST_OVERRIDE=postgres',
    'DATABASE_PORT_OVERRIDE=7432',
    '',
  ].join('\n'), { mode: 0o600 });

  await manager.ensureGeneratedSecrets();

  const env = await manager.readEnv();
  assert.equal(env.DATABASE_URL, '');
  assert.equal(env.DATABASE_HOST_OVERRIDE, 'pgbouncer');
  assert.equal(env.DATABASE_PORT_OVERRIDE, '5432');
  assert.equal(env.DATABASE_MIGRATION_HOST_OVERRIDE, 'postgres');
  assert.equal(env.DATABASE_MIGRATION_PORT_OVERRIDE, '5432');
});

test('saveSettings atomically synchronizes a rotated bundled Redis password', async () => {
  const manager = await createManager();
  manager.getState = async () => ({ stack: { running: 0, services: [] } });
  await manager.writeEnv({
    REDIS_ENABLED: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
    REDIS_PASSWORD: 'old-password',
    REDIS_URL: 'redis://:old-password@redis:6379/0',
  });

  await manager.saveSettings({
    redisPassword: "rotated!redis'password",
  });

  const env = await manager.readEnv();
  assert.equal(env.REDIS_PASSWORD, "rotated!redis'password");
  assert.equal(env.REDIS_URL, 'redis://:rotated%21redis%27password@redis:6379/0');
});

test('ensureGeneratedSecrets preserves external Redis URLs when bundled Redis is disabled', async () => {
  const manager = await createManager();
  await manager.writeEnv({
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'false',
    OMLORIX_USE_PGBOUNCER: 'false',
    OMLORIX_USE_BUNDLED_STORAGE: 'false',
    JWT_SECRET_KEY: 'x'.repeat(64),
    ENCRYPTION_KEY: 'secret-fernet-key',
    DATABASE_PASSWORD: 'database-secret',
    REDIS_URL: 'rediss://:secret@redis.example.com:6380/0',
  });

  await manager.ensureGeneratedSecrets();

  const env = await manager.readEnv();
  assert.equal(env.REDIS_URL, 'rediss://:secret@redis.example.com:6380/0');
});

test('ensureGeneratedSecrets repairs a stale provider when bundled MinIO is enabled', async () => {
  const manager = await createManager();
  await manager.writeEnv({
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
    OMLORIX_USE_PGBOUNCER: 'false',
    OMLORIX_USE_BUNDLED_STORAGE: 'true',
    FILE_STORAGE_PROVIDER: 'webdav',
    JWT_SECRET_KEY: 'x'.repeat(64),
    ENCRYPTION_KEY: 'secret-fernet-key',
    DATABASE_PASSWORD: 'database-secret',
    REDIS_PASSWORD: 'redis-secret',
  });

  await manager.ensureGeneratedSecrets();

  const env = await manager.readEnv();
  assert.equal(env.FILE_STORAGE_PROVIDER, 's3');
});

test('start repairs bundled Redis localhost URLs before composing services', async () => {
  const manager = await createManager();
  let operationArgs = [];
  await manager.writeEnv({
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
    OMLORIX_USE_PGBOUNCER: 'false',
    OMLORIX_USE_BUNDLED_STORAGE: 'false',
    JWT_SECRET_KEY: 'x'.repeat(64),
    LOG_IP_HASH_SALT: 'i'.repeat(32),
    ENCRYPTION_KEY: 'secret-fernet-key',
    DATABASE_PASSWORD: 'database-secret',
    REDIS_PASSWORD: 'redis-secret',
    REDIS_URL: 'redis://:redis-secret@localhost:6379/0',
    OMLORIX_VERSION: '1.2.3',
  });
  manager.runOperation = async (name, args) => {
    operationArgs = args;
    return { ok: true };
  };
  manager.prepareCompose = async () => ({
    env: await manager.readEnv(),
    args: ['compose'],
  });
  manager.runDockerStep = async () => {};

  await manager.start();

  const env = await manager.readEnv();
  assert.equal(env.REDIS_URL, 'redis://:redis-secret@redis:6379/0');
  assert.equal(operationArgs.includes('up'), true);
});

test('launcher state exposes local secret values for reveal controls', async () => {
  const repoRoot = path.resolve(__dirname, '..', '..');
  const manager = await createManager('1.2.2', { appRoot: repoRoot });
  await manager.writeEnv({
    JWT_SECRET_KEY: 'x'.repeat(64),
    ENCRYPTION_KEY: 'secret-fernet-key',
    DATABASE_PASSWORD: 'database-secret',
    REDIS_PASSWORD: 'redis-secret',
    REDIS_URL: 'redis://:redis-secret@redis:6379/0',
    BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE: 'backup-secret',
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
    OMLORIX_USE_BUNDLED_STORAGE: 'false',
  });
  await manager.writeLauncherMetadata({
    highestSuccessfulServerVersion: '1.7.0',
  });
  manager.dockerStatus = async () => ({
    installed: false,
    running: false,
    compose: false,
  });

  const state = await manager.getState();
  const editor = await manager.getEnvEditor();
  const jwtSecret = editor.fields.find((field) => field.key === 'JWT_SECRET_KEY');

  assert.equal(state.env.BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE, 'backup-secret');
  assert.equal(state.env.JWT_SECRET_KEY, 'x'.repeat(64));
  assert.equal(state.env.ENCRYPTION_KEY, 'secret-fernet-key');
  assert.equal(state.env.OMLORIX_HIGHEST_VERSION_USED, undefined);
  assert.equal(state.launcherMetadata.highestSuccessfulServerVersion, '1.7.0');
  assert.equal(jwtSecret, undefined);
});

test('getState reports missing required environment variables', async () => {
  const manager = await createManager();
  // Write an intentionally incomplete env to test missing detection.
  await manager.writeEnv({
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
    OMLORIX_USE_BUNDLED_STORAGE: 'false',
    OMLORIX_USE_PGBOUNCER: 'false',
  });
  manager.dockerStatus = async () => ({
    installed: false,
    running: false,
    compose: false,
  });

  const state = await manager.getState();

  assert.equal(state.envRequirements.ok, false);
  assert.deepEqual(state.envRequirements.missingKeys, [
    'JWT_SECRET_KEY',
    'LOG_IP_HASH_SALT',
    'ENCRYPTION_KEY',
    'DATABASE_PASSWORD',
    'REDIS_PASSWORD',
  ]);
});

test('start rejects before mutating .env when required variables are missing', async () => {
  const manager = await createManager();
  // Write an intentionally incomplete env to test rejection.
  await manager.writeEnv({
    OMLORIX_USE_BUNDLED_DB: 'true',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
    OMLORIX_USE_BUNDLED_STORAGE: 'false',
    OMLORIX_USE_PGBOUNCER: 'false',
  });

  await assert.rejects(
    () => manager.start(),
    (error) => {
      assert.equal(error.code, 'ENV_REQUIREMENTS_MISSING');
      assert.deepEqual(error.missingRequiredKeys, [
        'JWT_SECRET_KEY',
        'LOG_IP_HASH_SALT',
        'ENCRYPTION_KEY',
        'DATABASE_PASSWORD',
        'REDIS_PASSWORD',
      ]);
      return true;
    },
  );

  const raw = await fs.readFile(manager.envFile, 'utf8');
  assert.notEqual(raw, '');
});

test('managed cloud validation requires external services instead of bundled passwords', async () => {
  const manager = await createManager();

  await manager.writeEnv({
    JWT_SECRET_KEY: 'x'.repeat(64),
    LOG_IP_HASH_SALT: 'i'.repeat(32),
    ENCRYPTION_KEY: 'test-encryption-key',
    DATABASE_URL: 'postgresql://omlorix:secret@db.example.com:5432/omlorix',
    REDIS_URL: 'rediss://:secret@redis.example.com:6380/0',
    FILE_STORAGE_PROVIDER: 's3',
  });

  await assert.doesNotReject(() => manager.validateProfileEnv());
});

test('env editor exposes stable metadata translation keys to the renderer', async () => {
  const repoRoot = path.resolve(__dirname, '..', '..');
  const manager = await createManager('1.2.2', { appRoot: repoRoot });
  await manager.writeEnv({
    ALLOW_LOCAL_OR_PRIVATE_ORIGINS: 'false',
    BACKUP_SCHEDULER_ENABLED: 'true',
  });

  const editor = await manager.getEnvEditor();
  const authOrigin = editor.fields.find((field) => field.key === 'ALLOW_LOCAL_OR_PRIVATE_ORIGINS');
  const backupScheduler = editor.fields.find((field) => field.key === 'BACKUP_SCHEDULER_ENABLED');

  assert.equal(authOrigin.sectionKey, 'launcher_ui_env_section_auth_origin_policy');
  assert.equal(
    authOrigin.descriptionKey,
    'launcher_ui_env_description_allow_local_or_private_origins',
  );
  assert.equal(backupScheduler.sectionKey, 'launcher_ui_env_section_backup_restore');
  assert.equal(
    backupScheduler.descriptionKey,
    'launcher_ui_env_description_backup_scheduler_enabled',
  );

  const shippedEnvironmentMetadata = {
    SETTINGS_CACHE_NAMESPACE: {
      sectionKey: 'launcher_ui_env_section_redis',
      descriptionKey: 'launcher_ui_env_description_settings_cache_namespace',
    },
    OMLORIX_ERASURE_LEDGER_PATH: {
      sectionKey: 'launcher_ui_env_section_backup_restore',
      descriptionKey: 'launcher_ui_env_description_omlorix_erasure_ledger_path',
    },
    RATE_LIMIT_WIDGET_FRAME_RPM: {
      sectionKey: 'launcher_ui_env_section_api_rate_limiting',
      descriptionKey: 'launcher_ui_env_description_rate_limit_widget_frame_rpm',
    },
  };
  for (const [key, expected] of Object.entries(shippedEnvironmentMetadata)) {
    const field = editor.fields.find((candidate) => candidate.key === key);
    assert(field, `${key} must be available in the Environment editor`);
    assert.equal(field.known, true, `${key} must remain a shipped environment field`);
    assert.equal(field.sectionKey, expected.sectionKey);
    assert.equal(field.descriptionKey, expected.descriptionKey);
    assert.notEqual(field.description, '');
    assert.notEqual(field.description, field.label);
  }
});

test('env editor omits connection fields owned by the settings page', async () => {
  const repoRoot = path.resolve(__dirname, '..', '..');
  const manager = await createManager('1.2.2', { appRoot: repoRoot });
  await manager.writeEnv({
    OMLORIX_USE_BUNDLED_DB: 'false',
    OMLORIX_USE_BUNDLED_REDIS: 'true',
    OMLORIX_USE_BUNDLED_STORAGE: 'false',
    MODE: 'production',
    COMPOSE_PROJECT_NAME: 'omlorix-prod',
    JWT_SECRET_KEY: 'x'.repeat(64),
    ENCRYPTION_KEY: 'test-fernet-key',
    PASSWORD_RESET_IDENTIFIER_HASH_SALT: 'reset-salt',
    OMLORIX_LAUNCHER_PROXY_ENABLED: 'true',
    FRONTEND_HTTP_HOST_BIND: '127.0.0.1',
    FRONTEND_HTTP_HOST_PORT: '8080',
    API_LB_TRAEFIK_WEB_HOST_PORT: '8080',
    API_LB_TRAEFIK_DASHBOARD_HOST_PORT: '8081',
    OMLORIX_LAUNCHER_PROXY_HTTP_PORT: '8081',
    OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE: 'secret',
    TRUST_PROXY_HEADERS: 'true',
    TRUSTED_PROXIES: '10.0.0.0/8',
    TRUSTED_HOSTS: 'chat.example.com',
    UVICORN_FORWARDED_ALLOW_IPS: '127.0.0.1,::1',
    RATE_LIMIT_TRUSTED_PROXIES: '10.0.0.20',
    AUTH_TRUSTED_PROXIES: '10.0.0.30',
    RATE_LIMIT_PROXY_SETTINGS_CACHE_SECONDS: '60',
    DATABASE_SCHEMA: 'app',
    DATABASE_AUDIT_LOG_SCHEMA: 'audit',
    DATABASE_LOGS_SCHEMA: 'logs',
    OMLORIX_AUTO_CREATE_DATABASES: 'true',
    DEV_DATABASE_HOST_PORT: '5432',
    DEV_REDIS_HOST_PORT: '6379',
    OMLORIX_USE_PGBOUNCER: 'true',
    PGBOUNCER_POOL_MODE: 'transaction',
    PGBOUNCER_MAX_CLIENT_CONN: '200',
    PGBOUNCER_HOST_PORT: '6432',
    FILE_STORAGE_PROVIDER: 's3',
    FILE_STORAGE_S3_BUCKET: 'omlorix-user-files',
    FILE_STORAGE_S3_SECRET_ACCESS_KEY: 'storage-secret',
    MINIO_ROOT_PASSWORD: 'minio-secret',
    MINIO_API_HOST_PORT: '9000',
    MINIO_CONSOLE_HOST_PORT: '9001',
    OTEL_ENABLED: 'true',
    OTEL_SERVICE_NAME: 'omlorix-api',
    OTEL_GRPC_HOST_PORT: '4317',
    GRAFANA_ADMIN_PASSWORD: 'grafana-secret',
  });

  const editor = await manager.getEnvEditor();
  const databaseUrl = editor.fields.find((field) => field.key === 'DATABASE_URL');
  const databasePassword = editor.fields.find((field) => field.key === 'DATABASE_PASSWORD');
  const mode = editor.fields.find((field) => field.key === 'MODE');
  const composeProjectName = editor.fields.find((field) => field.key === 'COMPOSE_PROJECT_NAME');
  const jwtSecret = editor.fields.find((field) => field.key === 'JWT_SECRET_KEY');
  const encryptionKey = editor.fields.find((field) => field.key === 'ENCRYPTION_KEY');
  const passwordResetSalt = editor.fields.find((field) => field.key === 'PASSWORD_RESET_IDENTIFIER_HASH_SALT');
  const bundledDb = editor.fields.find((field) => field.key === 'OMLORIX_USE_BUNDLED_DB');
  const bundledRedis = editor.fields.find((field) => field.key === 'OMLORIX_USE_BUNDLED_REDIS');
  const bundledStorage = editor.fields.find((field) => field.key === 'OMLORIX_USE_BUNDLED_STORAGE');
  const databaseSchema = editor.fields.find((field) => field.key === 'DATABASE_SCHEMA');
  const databaseAuditSchema = editor.fields.find((field) => field.key === 'DATABASE_AUDIT_LOG_SCHEMA');
  const databaseLogsSchema = editor.fields.find((field) => field.key === 'DATABASE_LOGS_SCHEMA');
  const autoCreateDatabases = editor.fields.find((field) => field.key === 'OMLORIX_AUTO_CREATE_DATABASES');
  const redisEnabled = editor.fields.find((field) => field.key === 'REDIS_ENABLED');
  const redisPassword = editor.fields.find((field) => field.key === 'REDIS_PASSWORD');
  const devDatabaseHostPort = editor.fields.find((field) => field.key === 'DEV_DATABASE_HOST_PORT');
  const devRedisHostPort = editor.fields.find((field) => field.key === 'DEV_REDIS_HOST_PORT');
  const frontendHttpHostPort = editor.fields.find((field) => field.key === 'FRONTEND_HTTP_HOST_PORT');
  const apiLbTraefikWebHostPort = editor.fields.find((field) => field.key === 'API_LB_TRAEFIK_WEB_HOST_PORT');
  const apiLbTraefikDashboardHostPort = editor.fields.find((field) => field.key === 'API_LB_TRAEFIK_DASHBOARD_HOST_PORT');
  const proxyEnabled = editor.fields.find((field) => field.key === 'OMLORIX_LAUNCHER_PROXY_ENABLED');
  const proxyHttpPort = editor.fields.find((field) => field.key === 'OMLORIX_LAUNCHER_PROXY_HTTP_PORT');
  const proxyPassphrase = editor.fields.find((field) => field.key === 'OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE');
  const trustProxyHeaders = editor.fields.find((field) => field.key === 'TRUST_PROXY_HEADERS');
  const trustedProxies = editor.fields.find((field) => field.key === 'TRUSTED_PROXIES');
  const trustedHosts = editor.fields.find((field) => field.key === 'TRUSTED_HOSTS');
  const uvicornForwardedAllowIps = editor.fields.find((field) => field.key === 'UVICORN_FORWARDED_ALLOW_IPS');
  const rateLimitTrustedProxies = editor.fields.find((field) => field.key === 'RATE_LIMIT_TRUSTED_PROXIES');
  const authTrustedProxies = editor.fields.find((field) => field.key === 'AUTH_TRUSTED_PROXIES');
  const rateLimitProxySettingsCacheSeconds = editor.fields.find((field) => field.key === 'RATE_LIMIT_PROXY_SETTINGS_CACHE_SECONDS');
  const pgbouncerEnabled = editor.fields.find((field) => field.key === 'OMLORIX_USE_PGBOUNCER');
  const pgbouncerPoolMode = editor.fields.find((field) => field.key === 'PGBOUNCER_POOL_MODE');
  const pgbouncerMaxClientConn = editor.fields.find((field) => field.key === 'PGBOUNCER_MAX_CLIENT_CONN');
  const pgbouncerHostPort = editor.fields.find((field) => field.key === 'PGBOUNCER_HOST_PORT');
  const storageProvider = editor.fields.find((field) => field.key === 'FILE_STORAGE_PROVIDER');
  const storageBucket = editor.fields.find((field) => field.key === 'FILE_STORAGE_S3_BUCKET');
  const storageSecret = editor.fields.find((field) => field.key === 'FILE_STORAGE_S3_SECRET_ACCESS_KEY');
  const minioPassword = editor.fields.find((field) => field.key === 'MINIO_ROOT_PASSWORD');
  const minioApiHostPort = editor.fields.find((field) => field.key === 'MINIO_API_HOST_PORT');
  const minioConsoleHostPort = editor.fields.find((field) => field.key === 'MINIO_CONSOLE_HOST_PORT');
  const otelEnabled = editor.fields.find((field) => field.key === 'OTEL_ENABLED');
  const otelServiceName = editor.fields.find((field) => field.key === 'OTEL_SERVICE_NAME');
  const otelGrpcHostPort = editor.fields.find((field) => field.key === 'OTEL_GRPC_HOST_PORT');
  const grafanaPassword = editor.fields.find((field) => field.key === 'GRAFANA_ADMIN_PASSWORD');

  assert.equal(databaseUrl, undefined);
  assert.equal(databasePassword, undefined);
  assert.equal(mode, undefined);
  assert.equal(composeProjectName, undefined);
  assert.equal(jwtSecret, undefined);
  assert.equal(encryptionKey, undefined);
  assert.equal(passwordResetSalt, undefined);
  assert.equal(bundledDb, undefined);
  assert.equal(bundledRedis, undefined);
  assert.equal(bundledStorage, undefined);
  assert.equal(databaseSchema, undefined);
  assert.equal(databaseAuditSchema, undefined);
  assert.equal(databaseLogsSchema, undefined);
  assert.equal(autoCreateDatabases, undefined);
  assert.equal(redisEnabled, undefined);
  assert.equal(redisPassword, undefined);
  assert.equal(devDatabaseHostPort, undefined);
  assert.equal(devRedisHostPort, undefined);
  assert.equal(frontendHttpHostPort, undefined);
  assert.equal(apiLbTraefikWebHostPort, undefined);
  assert.equal(apiLbTraefikDashboardHostPort, undefined);
  assert.equal(proxyEnabled, undefined);
  assert.equal(proxyHttpPort, undefined);
  assert.equal(proxyPassphrase, undefined);
  assert.equal(trustProxyHeaders, undefined);
  assert.equal(trustedProxies, undefined);
  assert.equal(trustedHosts, undefined);
  assert.equal(uvicornForwardedAllowIps, undefined);
  assert.equal(rateLimitTrustedProxies, undefined);
  assert.equal(authTrustedProxies, undefined);
  assert.equal(rateLimitProxySettingsCacheSeconds, undefined);
  assert.equal(pgbouncerEnabled, undefined);
  assert.equal(pgbouncerPoolMode, undefined);
  assert.equal(pgbouncerMaxClientConn, undefined);
  assert.equal(pgbouncerHostPort, undefined);
  assert.equal(storageProvider, undefined);
  assert.equal(storageBucket, undefined);
  assert.equal(storageSecret, undefined);
  assert.equal(minioPassword, undefined);
  assert.equal(minioApiHostPort, undefined);
  assert.equal(minioConsoleHostPort, undefined);
  assert.equal(otelEnabled, undefined);
  assert.equal(otelServiceName, undefined);
  assert.equal(otelGrpcHostPort, undefined);
  assert.equal(grafanaPassword, undefined);
});

test('saveEnvEditor creates unique backups for rapid consecutive changes', async () => {
  const manager = await createManager();
  // saveEnvEditor hydrates launcher state for its API response. This unit test
  // covers file backup behavior, so keep it independent from a real Docker CLI.
  manager.getState = async () => ({});
  await manager.writeEnv({ FOO: 'one' });

  await manager.saveEnvEditor({ values: { FOO: 'two' }, clearSecrets: [] });
  await manager.saveEnvEditor({ values: { FOO: 'three' }, clearSecrets: [] });

  const backupDir = path.join(manager.serverHome, '.env.backups');
  const backups = (await fs.readdir(backupDir)).filter((entry) => entry.endsWith('.bak'));
  const backupContents = await Promise.all(
    backups.map((entry) => fs.readFile(path.join(backupDir, entry), 'utf8')),
  );

  assert.equal(backups.length, 2);
  assert.equal(new Set(backups).size, 2);
  assert.equal(backupContents.some((content) => content.includes('FOO=one')), true);
  assert.equal(backupContents.some((content) => content.includes('FOO=two')), true);
});

test('saveEnvEditor removes custom environment entries', async () => {
  const manager = await createManager();
  manager.getState = async () => ({});
  await manager.writeEnv({
    FOO: 'one',
    CUSTOM_KEEP: 'two',
  });

  const result = await manager.saveEnvEditor({ values: {}, clearSecrets: [], removeKeys: ['FOO'] });
  const env = await manager.readEnv();

  assert.equal(result.changed, true);
  assert.equal(env.FOO, undefined);
  assert.equal(env.CUSTOM_KEEP, 'two');
});

test('saveEnvEditor does not remove known environment entries', async () => {
  const repoRoot = path.resolve(__dirname, '..', '..');
  const manager = await createManager('1.2.2', { appRoot: repoRoot });
  manager.getState = async () => ({});
  await manager.writeEnv({
    JWT_SECRET_KEY: 'x'.repeat(64),
    FOO: 'one',
  });

  await manager.saveEnvEditor({ values: {}, clearSecrets: [], removeKeys: ['JWT_SECRET_KEY'] });
  const env = await manager.readEnv();

  assert.equal(env.JWT_SECRET_KEY, 'x'.repeat(64));
  assert.equal(env.FOO, 'one');
});

test('saveEnvEditor rejects reused JWT material before creating a backup', async () => {
  const repoRoot = path.resolve(__dirname, '..', '..');
  const manager = await createManager('1.2.2', { appRoot: repoRoot });
  const jwtSecret = 'j'.repeat(64);
  const originalSalt = 'i'.repeat(32);
  manager.getState = async () => ({});
  await manager.writeEnv({
    JWT_SECRET_KEY: jwtSecret,
    LOG_IP_HASH_SALT: originalSalt,
  });

  await assert.rejects(
    manager.saveEnvEditor({
      values: { LOG_IP_HASH_SALT: ` ${jwtSecret} ` },
      clearSecrets: [],
    }),
    (error) => error.code === 'LOG_IP_HASH_SALT_REUSES_JWT_SECRET_KEY',
  );

  const env = await manager.readEnv();
  const backups = await fs.readdir(path.join(manager.serverHome, '.env.backups')).catch(() => []);
  assert.equal(env.LOG_IP_HASH_SALT, originalSalt);
  assert.deepEqual(backups, []);
});

test('exportEnv writes the current environment file to the selected path', async () => {
  const manager = await createManager();
  await manager.writeEnv({
    JWT_SECRET_KEY: 'x'.repeat(64),
    CUSTOM_VALUE: 'keep-me',
  });
  const targetPath = path.join(await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-env-export-')), '.env.backup');

  const result = await manager.exportEnv(targetPath);
  const exported = await fs.readFile(targetPath, 'utf8');

  assert.equal(result.ok, true);
  assert.equal(result.filePath, targetPath);
  assert.match(exported, /JWT_SECRET_KEY=/);
  assert.match(exported, /CUSTOM_VALUE=keep-me/);
});

test('setupEnvironment runs the setup-only launcher script and generates local secrets', async (t) => {
  const repoRoot = path.resolve(__dirname, '..', '..');
  const manager = await createManager('1.2.2', { appRoot: repoRoot });
  manager.dockerStatus = async () => ({
    installed: false,
    running: false,
    compose: false,
  });

  const result = await manager.setupEnvironment();
  const env = await manager.readEnv();

  assert.equal(result.state.envRequirements.ok, true);
  assert.equal(Buffer.byteLength(env.JWT_SECRET_KEY || '', 'utf8') >= 64, true);
  assert.equal(Buffer.from(env.JWT_SECRET_KEY, 'base64url').length, 64);
  assert.equal(Boolean(env.LOG_IP_HASH_SALT && env.LOG_IP_HASH_SALT.length >= 16), true);
  assert.equal(Boolean(env.ENCRYPTION_KEY), true);
  assert.equal(Boolean(env.DATABASE_PASSWORD && !env.DATABASE_PASSWORD.includes('CHANGE_ME')), true);
  assert.equal(Boolean(env.REDIS_PASSWORD && !env.REDIS_PASSWORD.includes('CHANGE_ME')), true);
  assert.match(env.REDIS_URL, /^redis:\/\/:[^@]+@redis:6379\/0$/);
});

test('PowerShell setup uses random generation APIs supported by Windows PowerShell 5.1', async () => {
  const repoRoot = path.resolve(__dirname, '..', '..');
  const source = await fs.readFile(
    path.join(repoRoot, 'script', 'server-launcher', 'start.ps1'),
    'utf8',
  );

  assert.doesNotMatch(source, /RandomNumberGenerator\]::Fill/);
  assert.match(source, /RandomNumberGenerator\]::Create\(\)/);
  assert.match(source, /\.GetBytes\(\$Buffer\)/);
  assert.match(source, /\.Dispose\(\)/);
  assert.equal(source.match(/Set-RandomBytes \$buffer/g)?.length, 2);
});
