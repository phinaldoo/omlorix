const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { ServerManager } = require('./server-manager');

const TEST_USER_DATA = fs.mkdtempSync(path.join(os.tmpdir(), 'omlorix-proxy-state-'));
test.after(() => fs.rmSync(TEST_USER_DATA, { recursive: true, force: true }));

/** Build a manager without touching Electron or the real launcher data folder. */
function createManager() {
  return new ServerManager({
    app: {
      getPath() {
        return TEST_USER_DATA;
      },
      getName() {
        return 'Omlorix Server Launcher';
      },
      getVersion() {
        return 'test';
      },
      isPackaged: false,
    },
    appRoot: process.cwd(),
  });
}

/** Return a complete valid proxy form payload with focused overrides. */
function proxyPayload(overrides = {}) {
  return {
    trustProxyHeaders: true,
    trustedProxies: '127.0.0.0/8,::1/128,172.31.250.10/32,172.31.250.1/32',
    trustedHosts: '',
    uvicornForwardedAllowIps: '127.0.0.1,::1,172.31.250.10,172.31.250.1',
    rateLimitTrustedProxies: '127.0.0.0/8,::1/128,172.31.250.10/32,172.31.250.1/32',
    authTrustedProxies: '127.0.0.0/8,::1/128,172.31.250.10/32,172.31.250.1/32',
    rateLimitProxySettingsCacheSeconds: '60',
    frontendHttpHostBind: '127.0.0.1',
    frontendHttpHostPort: '8080',
    apiLbTraefikWebHostPort: '8080',
    apiLbTraefikDashboardHostPort: '8081',
    enabled: true,
    autostart: true,
    bindHost: '0.0.0.0',
    httpPort: '8081',
    httpsEnabled: false,
    httpsPort: '8443',
    redirectHttpToHttps: false,
    tlsCertPath: '',
    tlsKeyPath: '',
    tlsCaPath: '',
    ...overrides,
  };
}

/** Isolate proxy-setting persistence and CLI discovery for save tests. */
function isolateProxySettingsSave(manager, serviceStatus) {
  const writes = {};
  manager.ensureServerHome = async () => {};
  manager.readEnv = async () => ({});
  manager.writeEnv = async (updates) => { writes.env = updates; };
  manager.updateServerSettings = async (update) => {
    writes.settings = update({ schemaVersion: 2, updateChannel: 'stable' });
    return writes.settings;
  };
  manager.getState = async () => ({ ok: true });
  manager.proxyServiceStatus = async () => serviceStatus;
  return writes;
}

test('Visitor IP status reports an enabled inactive launcher proxy as stopped', () => {
  const manager = createManager();
  const status = manager.visitorIpStatus(
    {
      TRUST_PROXY_HEADERS: 'true',
      TRUSTED_PROXIES: '127.0.0.0/8,172.31.250.10/32,172.31.250.1/32',
    },
    { vmNetworkingLikely: true },
    { healthy: true, clientIp: { ip: '172.31.250.1' } },
    { running: false, config: { enabled: true, autostart: false } },
  );

  assert.equal(status.level, 'warn');
  assert.equal(status.title, 'Proxy stopped');
  assert.equal(status.titleKey, 'launcher_visitor_ip_title_proxy_stopped');
  assert.equal(status.messageKey, 'launcher_visitor_ip_message_proxy_stopped');
  assert.equal(status.configured, true);
  assert.equal(status.ready, false);
  assert.equal(status.proxyEnabled, true);
  assert.equal(status.proxyRunning, false);
  assert.equal(status.observedIp, '172.31.250.1');
  assert.equal(status.recommendedAction, 'start-proxy');
});

test('every Visitor IP status branch supplies stable display translation keys', () => {
  const manager = createManager();
  const unconfigured = manager.visitorIpStatus(
    {},
    { vmNetworkingLikely: true },
    { services: [] },
    { running: false, config: { enabled: false } },
  );
  const proxyReady = manager.visitorIpStatus(
    { TRUST_PROXY_HEADERS: 'true', TRUSTED_PROXIES: '127.0.0.0/8' },
    { vmNetworkingLikely: true },
    { services: [] },
    { running: false, config: { enabled: false } },
  );
  const configured = manager.visitorIpStatus(
    { TRUST_PROXY_HEADERS: 'true', TRUSTED_PROXIES: '127.0.0.0/8' },
    { vmNetworkingLikely: false },
    { services: [] },
    { running: false, config: { enabled: false } },
  );

  assert.deepEqual(
    [unconfigured.titleKey, unconfigured.messageKey],
    ['launcher_visitor_ip_title_needs_setup', 'launcher_visitor_ip_message_needs_setup'],
  );
  assert.deepEqual(
    [proxyReady.titleKey, proxyReady.messageKey],
    ['launcher_visitor_ip_title_proxy_ready', 'launcher_visitor_ip_message_proxy_ready'],
  );
  assert.deepEqual(
    [configured.titleKey, configured.messageKey],
    ['launcher_visitor_ip_title_configured', 'launcher_visitor_ip_message_configured'],
  );
});

test('Visitor IP status becomes healthy when proxy trust and runtime are active', () => {
  const manager = createManager();
  const status = manager.visitorIpStatus(
    {
      TRUST_PROXY_HEADERS: 'true',
      TRUSTED_PROXIES: '127.0.0.0/8,172.31.250.10/32,172.31.250.1/32',
    },
    { vmNetworkingLikely: true },
    { healthy: true, clientIp: { ip: '172.31.250.1' } },
    { running: true, config: { enabled: true, autostart: true } },
    {
      verified: true,
      verifiedAt: '2026-08-09T12:00:00.000Z',
      clientIp: '127.0.0.1',
      scheme: 'http',
    },
  );

  assert.equal(status.level, 'ok');
  assert.equal(status.title, 'Proxy verified');
  assert.equal(status.titleKey, 'launcher_visitor_ip_title_proxy_running');
  assert.equal(status.messageKey, 'launcher_visitor_ip_message_proxy_running');
  assert.equal(status.ready, true);
  assert.equal(status.proxyRunning, true);
  assert.equal(status.observedIp, '127.0.0.1');
});

test('Visitor IP status requires a restart when the backend loaded older trust settings', () => {
  const manager = createManager();
  const status = manager.visitorIpStatus(
    {
      TRUST_PROXY_HEADERS: 'true',
      TRUSTED_PROXIES: '127.0.0.0/8,172.31.250.10/32,172.31.250.1/32',
    },
    { vmNetworkingLikely: true },
    {
      healthy: true,
      services: [{ Service: 'fastapi', State: 'running' }],
      backendProxyTrust: {
        known: true,
        configured: false,
        matchesDesired: false,
      },
      clientIp: { ip: '172.31.250.1' },
    },
    { running: true, config: { enabled: true, autostart: true } },
  );

  assert.equal(status.level, 'warn');
  assert.equal(status.title, 'Restart required');
  assert.equal(status.titleKey, 'launcher_visitor_ip_title_restart_required');
  assert.equal(status.messageKey, 'launcher_visitor_ip_message_restart_required');
  assert.equal(status.ready, false);
  assert.equal(status.restartRequired, true);
  assert.equal(status.recommendedAction, 'restart-omlorix');
});

test('live backend environment confirms when saved proxy trust settings are active', async () => {
  const manager = createManager();
  manager.execDocker = async (args) => {
    assert.deepEqual(args, [
      'inspect',
      '--format',
      '{{json .Config.Env}}',
      'fastapi-container',
    ]);
    return {
      ok: true,
      stdout: JSON.stringify([
        'DATABASE_PASSWORD=do-not-return-or-log',
        'TRUST_PROXY_HEADERS=true',
        'TRUSTED_PROXIES=127.0.0.0/8,172.31.250.10/32',
        'RATE_LIMIT_TRUSTED_PROXIES=127.0.0.0/8,172.31.250.10/32',
        'AUTH_TRUSTED_PROXIES=127.0.0.0/8,172.31.250.10/32',
        'UVICORN_FORWARDED_ALLOW_IPS=127.0.0.1,172.31.250.10',
      ]),
      stderr: '',
    };
  };
  const desiredEnv = {
    TRUST_PROXY_HEADERS: 'true',
    TRUSTED_PROXIES: '127.0.0.0/8,172.31.250.10/32',
    RATE_LIMIT_TRUSTED_PROXIES: '127.0.0.0/8,172.31.250.10/32',
    AUTH_TRUSTED_PROXIES: '127.0.0.0/8,172.31.250.10/32',
    UVICORN_FORWARDED_ALLOW_IPS: '127.0.0.1,172.31.250.10',
  };

  const runtime = await manager.getBackendProxyTrustRuntime(
    'fastapi-container',
    desiredEnv,
  );

  assert.deepEqual(runtime, {
    known: true,
    configured: true,
    matchesDesired: true,
  });
});

test('empty dynamic proxy trust does not make an unchanged backend look stale', async () => {
  const manager = createManager();
  manager.execDocker = async () => ({
    ok: true,
    // Compose now fails closed until visitor-IP convergence discovers the
    // installation-specific frontend address.
    stdout: JSON.stringify([
      'TRUST_PROXY_HEADERS=false',
      'TRUSTED_PROXIES=',
      'RATE_LIMIT_TRUSTED_PROXIES=',
      'AUTH_TRUSTED_PROXIES=',
      'UVICORN_FORWARDED_ALLOW_IPS=127.0.0.1,::1',
    ]),
    stderr: '',
  });
  const savedEnv = {
    TRUST_PROXY_HEADERS: 'false',
    TRUSTED_PROXIES: '',
    RATE_LIMIT_TRUSTED_PROXIES: '',
    AUTH_TRUSTED_PROXIES: '',
    UVICORN_FORWARDED_ALLOW_IPS: '127.0.0.1,::1',
  };

  const backendProxyTrust = await manager.getBackendProxyTrustRuntime(
    'fastapi-container',
    savedEnv,
  );
  const status = manager.visitorIpStatus(
    savedEnv,
    { vmNetworkingLikely: true },
    {
      services: [{ Service: 'fastapi', State: 'running' }],
      backendProxyTrust,
    },
    { running: false, config: { enabled: false } },
  );

  assert.deepEqual(backendProxyTrust, {
    known: true,
    configured: false,
    matchesDesired: true,
  });
  assert.equal(status.title, 'Needs setup');
  assert.equal(status.restartRequired, false);
  assert.equal(status.recommendedAction, 'fix');
});

test('launcher state evaluates Visitor IP readiness with the live proxy runtime', async () => {
  const manager = createManager();
  manager.ensureServerHome = async () => {};
  manager.readEnv = async () => ({
    TRUST_PROXY_HEADERS: 'true',
    TRUSTED_PROXIES: '127.0.0.0/8,172.31.250.10/32,172.31.250.1/32',
  });
  manager.dockerStatus = async () => ({
    running: true,
    compose: true,
    vmNetworkingLikely: true,
  });
  manager.stackStatus = async () => ({
    healthy: true,
    clientIp: { ip: '172.31.250.1' },
    services: [],
  });
  manager.readSetupState = async () => ({});
  manager.readLauncherMetadata = async () => ({});
  manager.proxy = {
    status() {
      return {
        running: false,
        config: { enabled: true, autostart: false },
      };
    },
  };

  const state = await manager.getState();

  assert.equal(state.proxy.running, false);
  assert.equal(state.visitorIp.title, 'Proxy stopped');
  assert.equal(state.visitorIp.ready, false);
  assert.equal(state.visitorIp.recommendedAction, 'start-proxy');
});

test('enabling a proxy without an autostart preference defaults autostart on', () => {
  const manager = createManager();
  const updates = manager.buildProxyEnvUpdates(proxyPayload({ autostart: undefined }));

  assert.equal(updates.OMLORIX_LAUNCHER_PROXY_ENABLED, 'true');
  assert.equal(updates.OMLORIX_LAUNCHER_PROXY_AUTOSTART, 'true');
  assert.equal(updates.TRUST_PROXY_HEADERS, 'true');
  assert.equal(updates.TRUSTED_PROXIES, '');
  assert.equal(updates.UVICORN_FORWARDED_ALLOW_IPS, '');
});

test('external proxy mode normalizes only an allowlisted single-address edge', () => {
  const manager = createManager();
  const updates = manager.buildProxyEnvUpdates(proxyPayload({
    enabled: false,
    trustProxyHeaders: true,
    trustedProxies: '192.0.2.10/32,2001:db8::10/128',
  }));

  assert.equal(updates.FRONTEND_TRUST_PROXY_HEADERS, 'true');
  assert.equal(
    updates.FRONTEND_TRUSTED_UPSTREAMS,
    '192.0.2.10/32,2001:db8::10/128',
  );
  assert.equal(updates.TRUSTED_PROXIES, '');
  assert.equal(updates.UVICORN_FORWARDED_ALLOW_IPS, '');
});

test('Launcher consumes the CLI external verification readiness contract', async () => {
  const manager = createManager();
  manager.execServerCli = async () => ({
    ok: true,
    stdout: JSON.stringify({
      ready: true,
      verification: {
        verified: true,
        verified_at: '2026-08-09T12:00:00Z',
        topology_fingerprint: 'topology-1',
        client_ip: '198.51.100.8',
        scheme: 'https',
        host: 'chat.example.com',
      },
    }),
  });

  const verification = await manager.cliVisitorIpVerification();
  const status = manager.visitorIpStatus(
    {
      TRUST_PROXY_HEADERS: 'true',
      TRUSTED_PROXIES: '172.31.250.10/32',
      FRONTEND_TRUSTED_UPSTREAMS: '192.0.2.10/32',
    },
    { vmNetworkingLikely: true },
    { services: [] },
    { running: false, config: { enabled: false } },
    verification,
  );

  assert.equal(verification.verified, true);
  assert.equal(status.externalProxyConfigured, true);
  assert.equal(status.ready, true);
  assert.equal(status.title, 'Proxy verified');
});

test('saving an enabled autostart proxy starts the authoritative CLI immediately', async () => {
  const manager = createManager();
  const actions = [];
  const writes = isolateProxySettingsSave(manager, {
    available: true,
    installed: false,
    running: false,
  });
  manager.runProxyServiceCommand = async (action) => { actions.push(action); };
  manager.proxy = {
    status() {
      return { running: false };
    },
    async start() {
      throw new Error('the Electron listener must not compete with the CLI');
    },
    async stop() {
      throw new Error('stop should not be called');
    },
  };

  const state = await manager.saveProxySettings(proxyPayload());

  assert.deepEqual(state, { ok: true });
  assert.equal(writes.env.OMLORIX_LAUNCHER_PROXY_AUTOSTART, undefined);
  assert.equal(writes.settings.proxy.autostart, true);
  assert.deepEqual(actions, ['start']);
});

test('saving an enabled autostart proxy starts the in-process fallback immediately', async () => {
  const manager = createManager();
  const starts = [];

  isolateProxySettingsSave(manager, {
    available: false,
    installed: false,
    running: false,
  });
  manager.proxy = {
    status() {
      return { running: false };
    },
    async start(config) {
      starts.push(config);
    },
    async stop() {
      throw new Error('stop should not be called');
    },
  };

  const state = await manager.saveProxySettings(proxyPayload());

  assert.deepEqual(state, { ok: true });
  assert.equal(starts.length, 1);
  assert.equal(starts[0].enabled, true);
  assert.equal(starts[0].autostart, true);
  assert.equal(starts[0].httpPort, '8081');
});

test('an explicit manual-start preference keeps a stopped proxy stopped on save', async () => {
  const manager = createManager();
  let startCalls = 0;

  isolateProxySettingsSave(manager, {
    available: false,
    installed: false,
    running: false,
  });
  manager.proxy = {
    status() {
      return { running: false };
    },
    async start() {
      startCalls += 1;
    },
    async stop() {
      throw new Error('stop should not be called');
    },
  };

  await manager.saveProxySettings(proxyPayload({ autostart: false }));

  assert.equal(startCalls, 0);
});

test('external proxy mode rejects trust-all networks before writing .env', async () => {
  const manager = createManager();
  manager.ensureServerHome = async () => {};
  manager.readEnv = async () => ({});
  manager.writeEnv = async () => {
    throw new Error('invalid external trust must not be persisted');
  };

  await assert.rejects(
    () => manager.saveProxySettings(proxyPayload({
      enabled: false,
      trustProxyHeaders: true,
      trustedProxies: '0.0.0.0/0',
    })),
    (error) => error.validationErrors?.trustedProxies
      === 'Trusted proxy networks must not include the entire Internet.',
  );
});

test('Launcher controls the installed background proxy through the shared CLI', async () => {
  const manager = createManager();
  const actions = [];
  manager.ensureServerHome = async () => {};
  manager.readEnv = async () => ({
    OMLORIX_LAUNCHER_PROXY_ENABLED: 'true',
    OMLORIX_LAUNCHER_PROXY_SECRET: 'c'.repeat(64),
  });
  manager.proxyServiceStatus = async () => ({
    available: true,
    installed: true,
    running: false,
  });
  // Keep the production platform selector intact while replacing both process
  // boundaries; development tests must not require a packaged CLI or elevation.
  const recordAction = async (action) => { actions.push(action); };
  manager.runProxyServiceCommand = recordAction;
  manager.runElevatedWindowsProxyServiceCommand = recordAction;
  manager.proxy.start = async () => {
    throw new Error('the Electron listener must not compete with the service');
  };
  manager.getState = async () => ({ ok: true });

  await manager.startProxy();

  assert.deepEqual(actions, ['start']);
});

test('Launcher uses the detached shared CLI before native service installation', async () => {
  const manager = createManager();
  const actions = [];
  manager.ensureServerHome = async () => {};
  manager.readEnv = async () => ({
    OMLORIX_LAUNCHER_PROXY_ENABLED: 'true',
    OMLORIX_LAUNCHER_PROXY_SECRET: 'd'.repeat(64),
  });
  manager.proxyServiceStatus = async () => ({
    available: true,
    installed: false,
    running: false,
  });
  manager.runProxyServiceCommand = async (action) => { actions.push(action); };
  manager.proxy.start = async () => {
    throw new Error('the in-process proxy must remain a development fallback');
  };
  manager.getState = async () => ({ ok: true });

  await manager.startProxy();

  assert.deepEqual(actions, ['start']);
});

test('packaged Launcher ignores OMLORIX_SERVER_CLI_PATH', (t) => {
  const appRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'omlorix-packaged-cli-'));
  t.after(() => fs.rmSync(appRoot, { recursive: true, force: true }));
  const executableName = process.platform === 'win32' ? 'omlorix-server.exe' : 'omlorix-server';
  const bundled = path.join(appRoot, 'native', executableName);
  fs.mkdirSync(path.dirname(bundled), { recursive: true });
  fs.writeFileSync(bundled, 'bundled');
  const previous = process.env.OMLORIX_SERVER_CLI_PATH;
  process.env.OMLORIX_SERVER_CLI_PATH = path.join(appRoot, 'attacker-cli');
  t.after(() => {
    if (previous === undefined) delete process.env.OMLORIX_SERVER_CLI_PATH;
    else process.env.OMLORIX_SERVER_CLI_PATH = previous;
  });
  const manager = new ServerManager({
    app: {
      getPath: () => TEST_USER_DATA,
      getName: () => 'Omlorix Server Launcher',
      getVersion: () => 'test',
      isPackaged: true,
    },
    appRoot,
  });

  assert.equal(manager.serverCliExecutable(), bundled);
  assert.equal(
    manager.serverCliExecutable({ allowEnvironmentOverride: false }),
    bundled,
  );
});

test('development Launcher keeps the explicit CLI override for unprivileged tests', (t) => {
  const previous = process.env.OMLORIX_SERVER_CLI_PATH;
  process.env.OMLORIX_SERVER_CLI_PATH = '/tmp/omlorix-development-cli';
  t.after(() => {
    if (previous === undefined) delete process.env.OMLORIX_SERVER_CLI_PATH;
    else process.env.OMLORIX_SERVER_CLI_PATH = previous;
  });

  assert.equal(createManager().serverCliExecutable(), '/tmp/omlorix-development-cli');
});

test('Launcher refreshes an outdated installed service before startup', async () => {
  const manager = createManager();
  const actions = [];
  let statusReads = 0;
  manager.ensureServerHome = async () => {};
  manager.readEnv = async () => ({ OMLORIX_LAUNCHER_PROXY_ENABLED: 'false' });
  manager.proxyServiceStatus = async () => {
    statusReads += 1;
    return statusReads === 1
      ? { available: true, installed: true, running: true, updateRequired: true }
      : { available: true, installed: true, running: true, updateRequired: false };
  };
  manager.runElevatedWindowsProxyServiceCommand = async (action) => { actions.push(action); };
  manager.controlAuthoritativeProxy = async (action) => { actions.push(action); };
  manager.proxy.status = () => ({ running: false });

  await manager.initializeProxy();

  assert.deepEqual(actions, ['refresh-service', 'stop']);
});

test('starting an already-running refreshed service remains idempotent', async () => {
  const manager = createManager();
  const actions = [];
  let statusReads = 0;
  manager.ensureServerHome = async () => {};
  manager.readEnv = async () => ({ OMLORIX_LAUNCHER_PROXY_ENABLED: 'true' });
  manager.proxyServiceStatus = async () => {
    statusReads += 1;
    return statusReads === 1
      ? { available: true, installed: true, running: true, updateRequired: true }
      : { available: true, installed: true, running: true, updateRequired: false };
  };
  manager.runElevatedWindowsProxyServiceCommand = async (action) => { actions.push(action); };
  manager.controlAuthoritativeProxy = async (action) => { actions.push(action); };
  manager.proxy.status = () => ({ running: false });
  manager.getState = async () => ({ ok: true });

  await manager.startProxy();

  assert.deepEqual(actions, ['refresh-service']);
});
