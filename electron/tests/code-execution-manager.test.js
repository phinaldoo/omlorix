const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const {
  CODE_EXECUTION_RUNTIME_DEFAULTS,
  CodeExecutionManager,
  GATEWAY_HEALTH_PATH,
  GATEWAY_HEALTH_DETAILS_PATH,
  parseReleaseVersions,
  parseComposeRows,
  parseEnv,
  serializeEnv,
  slugify,
} = require('../code-execution-manager');

const repositoryRoot = path.join(__dirname, '..', '..');

test('Launcher uses the canonical authenticated gateway health routes', () => {
  assert.equal(GATEWAY_HEALTH_PATH, '/health');
  assert.equal(GATEWAY_HEALTH_DETAILS_PATH, '/health/details');
});

/** Build a manager whose persistent directory is isolated per test. */
async function managerFixture() {
  const userData = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-codeexec-'));
  const manager = new CodeExecutionManager({
    app: { isPackaged: false, getPath: () => userData },
    appRoot: repositoryRoot,
    serverManager: {
      ensureLauncherServicesNetwork: async () => ({ created: false }),
      attachRunningBackendToLauncherServicesNetwork: async () => ({ attached: false, running: false }),
    },
  });
  return { manager, userData };
}

test('launcher dotenv serialization preserves validated values without expansion', () => {
  const source = {
    SIMPLE: 'value',
    URL: 'http://codeexec-local:8000',
    QUOTED: 'value with spaces',
    BOOL: 'true',
  };
  assert.deepEqual(parseEnv(serializeEnv(source)), {
    ...source,
    QUOTED: '"value with spaces"',
  });
});

test('Compose status parser accepts arrays and JSON-lines output', () => {
  const rows = [{ Service: 'gateway', State: 'running' }, { Service: 'redis', State: 'running' }];
  assert.deepEqual(parseComposeRows(JSON.stringify(rows)), rows);
  assert.deepEqual(parseComposeRows(rows.map((row) => JSON.stringify(row)).join('\n')), rows);
});

test('instance slugs are bounded Docker-safe identifiers', () => {
  assert.equal(slugify('  Zürich / Primary 🚀 '), 'zurich-primary');
  assert.equal(slugify('---'), 'execution');
  assert(slugify('x'.repeat(100)).length <= 36);
});

test('release picker keeps unique published stable semantic versions', () => {
  assert.deepEqual(parseReleaseVersions([
    { tag_name: 'v2.0.0', draft: false, prerelease: false },
    { tag_name: '2.0.0', draft: false, prerelease: false },
    { tag_name: 'v2.1.0-beta.1', draft: false, prerelease: true },
    { tag_name: 'v1.9.0', draft: false, prerelease: false },
    { tag_name: 'not-a-version', draft: false, prerelease: false },
    { tag_name: 'v1.8.0', draft: true, prerelease: false },
  ]), ['2.0.0', '1.9.0']);
});

test('Code Execution uses the CLI server home and migrates the legacy Launcher root', async (t) => {
  const userData = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-codeexec-migrate-'));
  t.after(() => fs.rm(userData, { recursive: true, force: true }));
  const legacyHome = path.join(userData, 'code-execution');
  await fs.mkdir(path.join(legacyHome, 'instances'), { recursive: true });
  await fs.writeFile(path.join(legacyHome, 'instances.json'), JSON.stringify({ version: 1, instances: [] }));
  const serverHome = path.join(userData, 'server');
  const manager = new CodeExecutionManager({
    app: { isPackaged: false, getPath: () => userData },
    appRoot: repositoryRoot,
    serverManager: { serverHome },
  });

  await manager.ensureHome();

  assert.equal(manager.home, path.join(serverHome, 'code-execution'));
  assert.equal(await fs.readFile(path.join(manager.home, 'instances.json'), 'utf8'), JSON.stringify({ version: 1, instances: [] }));
  await assert.rejects(fs.access(legacyHome));
});

test('development launchers expose the same published versions and image source as production', async (t) => {
  const { manager, userData } = await managerFixture();
  t.after(() => fs.rm(userData, { recursive: true, force: true }));

  manager.availableVersionsCache = {
    loadedAt: Date.now(),
    value: { latestVersion: '2.0.0', versions: ['2.0.0', '1.9.0'] },
  };
  assert.deepEqual(await manager.availableVersions(), {
    source: 'release',
    latestVersion: '2.0.0',
    versions: ['2.0.0', '1.9.0'],
    releaseError: false,
  });
  assert.equal(manager.normalizeSettings({
    name: 'Primary',
    version: '2.0.0',
  }).imageSource, 'release');
});

test('an explicitly pinned instance version is preserved', async (t) => {
  const { manager, userData } = await managerFixture();
  t.after(() => fs.rm(userData, { recursive: true, force: true }));

  assert.deepEqual(
    await manager.resolveCreationVersion({ name: 'Primary', version: '1.2.3', imageSource: 'release' }),
    { name: 'Primary', version: '1.2.3', imageSource: 'release' },
  );
  assert.equal(manager.normalizeSettings({
    name: 'Primary',
    version: '1.2.3',
    imageSource: 'release',
  }).imageSource, 'release');
});

test('duplicate managed ports retain a safe code and user-facing message', async (t) => {
  const { manager, userData } = await managerFixture();
  t.after(() => fs.rm(userData, { recursive: true, force: true }));
  await manager.ensureHome();
  await manager.persistRegistry({
    version: 1,
    instances: [{ id: 'existing-ab12cd34', name: 'Existing', version: '0.9.2', port: 8123 }],
  });

  await assert.rejects(
    manager.create({ name: 'Duplicate', version: '0.9.2', port: 8123 }),
    (error) => {
      assert.equal(error.code, 'PORT_IN_USE');
      assert.equal(error.message, 'That gateway port is already assigned to another managed instance.');
      return true;
    },
  );
});

test('instance environment isolates names, images, hosts, and private defaults', async (t) => {
  const { manager, userData } = await managerFixture();
  t.after(() => fs.rm(userData, { recursive: true, force: true }));
  const settings = manager.normalizeSettings({ name: 'Primary', port: 8123 });
  const instance = { id: 'primary-ab12cd34', imageSource: 'release' };
  const env = manager.envFor(instance, settings, 'secret-value');

  assert.equal(env.COMPOSE_PROJECT_NAME, 'omlorix-codeexec-primary-ab12cd34');
  assert.equal(env.CODE_EXECUTION_NETWORK_ALIAS, 'codeexec-primary-ab12cd34');
  assert.match(env.ALLOWED_HOSTS, /codeexec-primary-ab12cd34/);
  assert.equal(env.GATEWAY_HOST_BIND, '127.0.0.1');
  assert.equal(env.ALLOW_RESTRICTED_LOCAL_DOCKER_PROXY, 'true');
  assert.equal(env.GATEWAY_DOCKER_HOST, 'tcp://docker-proxy:2375');
  assert.equal(env.REDIS_URL, 'redis://redis:6379/0');
  assert.equal(env.REDIS_HEALTH_CHECK_INTERVAL, '30');
  assert.equal(env.SANDBOX_NETWORK_MODE, 'none');
  assert.equal(env.ALLOW_PIP_INSTALLS, 'false');
  assert.equal(env.API_KEYS, 'launcher:secret-value');
  assert.equal(env.MAX_EXECUTIONS_PER_SESSION, '100');
  assert.equal(env.SANDBOX_TMP_ROOT_SIZE, '512m');
  assert.equal(env.RENDER_MAX_TOTAL_ASSET_BYTES, '120000000');
  assert.equal(env.CODE_EXECUTION_GATEWAY_IMAGE, 'ghcr.io/phinaldoo/omlorix-code-execution-gateway:0.9.0');

  const releaseSettings = manager.normalizeSettings({
    name: 'Published',
    version: '2.0.0',
    imageSource: 'release',
  });
  const releaseEnv = manager.envFor({ id: 'published-ab12cd34', imageSource: 'release' }, releaseSettings, 'secret');
  assert.equal(releaseEnv.CODE_EXECUTION_GATEWAY_IMAGE, 'ghcr.io/phinaldoo/omlorix-code-execution-gateway:2.0.0');
});

test('managed Compose and template cover every current gateway runtime default', async () => {
  const [compose, template] = await Promise.all([
    fs.readFile(path.join(repositoryRoot, 'electron/code-execution/docker-compose.yml'), 'utf8'),
    fs.readFile(path.join(repositoryRoot, 'electron/code-execution/.env.example'), 'utf8'),
  ]);

  for (const key of Object.keys(CODE_EXECUTION_RUNTIME_DEFAULTS)) {
    assert.match(template, new RegExp(`^${key}=`, 'm'), `${key} is missing from the template`);
    if (key === 'GATEWAY_DOCKER_HOST') {
      assert.match(compose, /DOCKER_HOST: \$\{GATEWAY_DOCKER_HOST:-/);
    } else {
      assert.match(compose, new RegExp(`^\\s+${key}:`, 'm'), `${key} is missing from Compose`);
    }
  }
});

test('runtime bundle refresh upgrades existing instances without changing secrets', async (t) => {
  const { manager, userData } = await managerFixture();
  t.after(() => fs.rm(userData, { recursive: true, force: true }));
  const id = 'existing-ab12cd34';
  const home = manager.instanceHome(id);
  await fs.mkdir(home, { recursive: true });
  await fs.writeFile(path.join(home, 'docker-compose.yml'), 'stale compose\n');
  await fs.writeFile(path.join(home, '.env'), 'API_KEYS=launcher:keep-me\n');

  await manager.syncInstanceBundle(id);

  const [compose, proxyConfig, env] = await Promise.all([
    fs.readFile(path.join(home, 'docker-compose.yml'), 'utf8'),
    fs.readFile(path.join(home, 'haproxy.cfg'), 'utf8'),
    fs.readFile(path.join(home, '.env'), 'utf8'),
  ]);
  assert.match(compose, /docker-socket-proxy:v0\.4\.2@sha256:/);
  assert.match(proxyConfig, /timeout server 30s/);
  assert.equal(env, 'API_KEYS=launcher:keep-me\n');
});

test('connection handoff exposes the private Docker alias but never stores the key in the registry', async (t) => {
  const { manager, userData } = await managerFixture();
  t.after(() => fs.rm(userData, { recursive: true, force: true }));
  const id = 'primary-ab12cd34';
  const instance = {
    id,
    name: 'Primary execution',
    version: '0.9.0',
    port: 8123,
    memory: '512m',
    maxConcurrent: 10,
    sessionTimeout: 1200,
    networkAccess: false,
    allowPip: false,
    imageSource: 'local',
  };
  await manager.ensureHome();
  await fs.mkdir(manager.instanceHome(id), { recursive: true });
  await fs.writeFile(path.join(manager.instanceHome(id), '.env'), 'API_KEYS=launcher:private-key\n');
  await manager.persistRegistry({ version: 1, instances: [instance] });

  let networkEnsures = 0;
  let backendAttachments = 0;
  manager.serverManager = {
    ensureLauncherServicesNetwork: async () => {
      networkEnsures += 1;
    },
    attachRunningBackendToLauncherServicesNetwork: async () => {
      backendAttachments += 1;
      return { attached: true, running: true };
    },
  };

  const registry = JSON.parse(await fs.readFile(manager.registryFile, 'utf8'));
  assert.doesNotMatch(JSON.stringify(registry), /private-key/);
  assert.deepEqual(await manager.connectionDetails(id), {
    name: 'Primary execution',
    base_url: 'http://codeexec-primary-ab12cd34:8000',
    api_key: 'private-key',
    enabled_for_code_execution: true,
    enabled_for_latex_pdf: true,
    enabled_for_slide_renderer: true,
    weight: 1,
    adminUrl: '/admin/service-connections',
  });
  assert.equal(networkEnsures, 1);
  assert.equal(backendAttachments, 1);
});

test('save restores the instance environment and metadata when registry persistence fails', async (t) => {
  const { manager, userData } = await managerFixture();
  t.after(() => fs.rm(userData, { recursive: true, force: true }));
  const id = 'primary-ab12cd34';
  const instance = {
    id,
    name: 'Primary execution',
    version: '0.9.0',
    port: 8123,
    memory: '512m',
    maxConcurrent: 10,
    sessionTimeout: 1200,
    networkAccess: false,
    allowPip: false,
    // This test exercises registry rollback, so use the self-contained release
    // path instead of depending on an adjacent development source checkout.
    imageSource: 'release',
  };
  const envPath = path.join(manager.instanceHome(id), '.env');
  const originalContents = Buffer.from('API_KEYS=launcher:private-key\nCUSTOM=value\n');
  const originalTimestamp = new Date('2024-01-02T03:04:05.000Z');

  await manager.ensureHome();
  await fs.mkdir(manager.instanceHome(id), { recursive: true });
  await fs.writeFile(envPath, originalContents);
  await fs.chmod(envPath, 0o640);
  await fs.utimes(envPath, originalTimestamp, originalTimestamp);
  await manager.persistRegistry({ version: 1, instances: [instance] });
  const originalMetadata = await fs.stat(envPath);
  const registryError = new Error('registry write failed');
  manager.persistRegistry = async () => {
    throw registryError;
  };

  await assert.rejects(
    () => manager.save(id, { name: 'Updated execution' }),
    (error) => error === registryError,
  );

  const restoredMetadata = await fs.stat(envPath);
  assert.deepEqual(await fs.readFile(envPath), originalContents);
  assert.equal(restoredMetadata.mode, originalMetadata.mode);
  assert.equal(restoredMetadata.atimeMs, originalMetadata.atimeMs);
  assert.equal(restoredMetadata.mtimeMs, originalMetadata.mtimeMs);
});

test('failed updates restore and restart the previous Code Execution version', async (t) => {
  const { manager, userData } = await managerFixture();
  t.after(() => fs.rm(userData, { recursive: true, force: true }));
  const previous = {
    id: 'primary-ab12cd34',
    name: 'Primary execution',
    version: '1.0.0',
    port: 8123,
    memory: '512m',
    maxConcurrent: 10,
    sessionTimeout: 1200,
    networkAccess: false,
    allowPip: false,
    imageSource: 'release',
  };
  const savedVersions = [];
  let startCalls = 0;

  manager.registeredInstance = async () => ({ instance: previous });
  manager.checkUpdate = async () => ({
    currentVersion: '1.0.0',
    latestVersion: '2.0.0',
    updateAvailable: true,
  });
  manager.save = async (_instanceId, payload) => {
    savedVersions.push(payload.version);
  };
  manager.start = async () => {
    startCalls += 1;
    if (startCalls === 1) throw new Error('image pull failed');
    return { id: previous.id, version: previous.version };
  };

  await assert.rejects(
    () => manager.update(previous.id),
    (error) => error.code === 'UPDATE_FAILED_ROLLED_BACK'
      && error.cause?.message === 'image pull failed',
  );
  assert.deepEqual(savedVersions, ['2.0.0', '1.0.0']);
  assert.equal(startCalls, 2);
});
