const { EventEmitter } = require('events');
const crypto = require('crypto');
const fs = require('fs/promises');
const fssync = require('fs');
const http = require('http');
const https = require('https');
const net = require('net');
const path = require('path');
const { spawn } = require('child_process');
const { dockerCommand, dockerSpawnEnv } = require('./server-manager');

const REGISTRY_VERSION = 1;
const DEFAULT_VERSION = '0.9.0';
const VERSION_CACHE_MS = 5 * 60 * 1000;
const SHARED_NETWORK = 'omlorix-launcher-services';
const GATEWAY_HEALTH_PATH = '/health';
const GATEWAY_HEALTH_DETAILS_PATH = '/health/details';
const INSTANCE_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,62}$/;
const VERSION_PATTERN = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$/;
const MEMORY_OPTIONS = new Set(['256m', '512m', '1g', '2g', '4g', '8g']);

// Keep the Launcher-generated environment explicit and deterministic. These
// values mirror the current Code Execution service contract; instance settings
// override the small subset exposed in the UI below. Legacy switches remain in
// envFor() so users can still start an intentionally pinned older release.
const CODE_EXECUTION_RUNTIME_DEFAULTS = Object.freeze({
  REDIS_URL: 'redis://redis:6379/0',
  REDIS_SOCKET_CONNECT_TIMEOUT: '5',
  REDIS_SOCKET_TIMEOUT: '5',
  REDIS_HEALTH_CHECK_INTERVAL: '30',
  GATEWAY_DOCKER_HOST: 'tcp://docker-proxy:2375',
  DOCKER_CLIENT_TIMEOUT: '30',
  STRONG_SANDBOX_RUNTIMES: 'runsc,kata,kata-runtime,io.containerd.runsc.v1,io.containerd.kata.v2',
  SANDBOX_RUNTIME: '',
  SECCOMP_PROFILE_DAEMON_PATH: '',
  MAX_REQUEST_BODY_SIZE: '33554432',
  MAX_INPUT_FILES: '10',
  MAX_INPUT_FILE_SIZE: '5242880',
  MAX_INPUT_TOTAL_SIZE: '20971520',
  MAX_FILE_NAME_LENGTH: '128',
  CONTAINER_CREATE_GUARD_TIMEOUT: '30',
  MAX_EXECUTIONS_PER_SESSION: '100',
  FILE_PROVISION_TIMEOUT: '30',
  RATE_LIMIT_REQUESTS_PER_WINDOW: '30',
  RATE_LIMIT_WINDOW_SECONDS: '60',
  CONTAINER_RATE_LIMIT_REQUESTS_PER_WINDOW: '10',
  CONTAINER_RATE_LIMIT_WINDOW_SECONDS: '60',
  SANDBOX_USER: 'sandbox',
  SANDBOX_TMP_ROOT_SIZE: '512m',
  SANDBOX_SHM_SIZE: '128m',
  SANDBOX_HOME_TMPFS_SIZE: '256m',
  MAX_PIP_PACKAGES: '5',
  MAX_PIP_PACKAGE_NAME_LENGTH: '64',
  SANDBOX_ENV_TARGET_PATH: '/home/sandbox/.env',
  MAX_CONCURRENT_RENDERS: '2',
  RENDER_RATE_LIMIT_REQUESTS_PER_WINDOW: '10',
  RENDER_RATE_LIMIT_WINDOW_SECONDS: '60',
  RENDER_MAX_REQUEST_BODY_BYTES: '180000000',
  RENDER_MAX_HTML_CHARS: '2000000',
  RENDER_MAX_INPUT_FILES: '32',
  RENDER_MAX_SLIDES: '200',
  RENDER_MAX_ASSET_BYTES: '25000000',
  RENDER_MAX_TOTAL_ASSET_BYTES: '120000000',
  RENDER_MAX_OUTPUT_BYTES: '220000000',
  RENDER_SANDBOX_MEM_LIMIT: '2g',
  RENDER_SANDBOX_CPU_PERIOD: '100000',
  RENDER_SANDBOX_CPU_QUOTA: '200000',
  RENDER_SANDBOX_PIDS_LIMIT: '512',
  RENDER_SANDBOX_TMP_ROOT_SIZE: '1g',
  RENDER_SANDBOX_SHM_SIZE: '512m',
  RENDER_SANDBOX_HOME_TMPFS_SIZE: '256m',
});
// Runtime management assets are immutable and shared with the CLI. Refreshing
// them at lifecycle boundaries upgrades existing instances without touching
// their secrets, registry settings, sandbox environment, or volumes.
const INSTANCE_BUNDLE_FILES = Object.freeze([
  'docker-compose.yml',
  'haproxy.cfg',
  'TECNATIVA_DOCKER_SOCKET_PROXY_LICENSE.txt',
]);

/**
 * Extract published stable semantic versions from GitHub's releases response.
 * Drafts and prereleases stay available through manual pinning, but are not
 * offered as safe defaults in the normal launcher workflow.
 */
function parseReleaseVersions(payload) {
  if (!Array.isArray(payload)) return [];
  const seen = new Set();
  const versions = [];
  for (const release of payload) {
    if (release?.draft || release?.prerelease) continue;
    const version = String(release?.tag_name || '').trim().replace(/^v/i, '');
    if (!VERSION_PATTERN.test(version) || seen.has(version)) continue;
    seen.add(version);
    versions.push(version);
  }
  return versions;
}

/** Convert Docker Compose JSON output from either array or JSON-lines format. */
function parseComposeRows(raw) {
  const normalized = String(raw || '').trim();
  if (!normalized) return [];
  try {
    const parsed = JSON.parse(normalized);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch (_error) {
    return normalized.split(/\r?\n/).map((line) => {
      try {
        return JSON.parse(line);
      } catch (_lineError) {
        return null;
      }
    }).filter(Boolean);
  }
}

/** Parse the simple launcher-owned dotenv file without expanding variables. */
function parseEnv(raw) {
  const values = {};
  for (const line of String(raw || '').split(/\r?\n/)) {
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (match) values[match[1]] = match[2];
  }
  return values;
}

/** Serialize only validated launcher values, quoting values when required. */
function envValue(value) {
  const normalized = String(value ?? '');
  if (/^[A-Za-z0-9_./:@+-]*$/.test(normalized)) return normalized;
  return `"${normalized.replaceAll('\\', '\\\\').replaceAll('"', '\\"')}"`;
}

function serializeEnv(values) {
  return `${Object.entries(values).map(([key, value]) => `${key}=${envValue(value)}`).join('\n')}\n`;
}

function boolValue(value) {
  return value === true || ['1', 'true', 'yes', 'on'].includes(String(value || '').toLowerCase());
}

function boundedInteger(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ''), 10);
  if (!Number.isInteger(parsed)) return fallback;
  return Math.max(minimum, Math.min(maximum, parsed));
}

/** Produce a readable stable slug without allowing Docker/name injection. */
function slugify(value) {
  const slug = String(value || '')
    .toLowerCase()
    .normalize('NFKD')
    .replace(/\p{M}+/gu, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 36);
  return slug || 'execution';
}

/** Make a renderer-safe error with a stable code and no sensitive process data. */
function managerError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

/** Check a host port before reserving it in a new instance configuration. */
function portAvailable(host, port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once('error', () => resolve(false));
    server.listen({ host, port, exclusive: true }, () => {
      server.close(() => resolve(true));
    });
  });
}

/** Execute Docker without a shell so instance values never become commands. */
function executeDocker(args, { cwd, timeoutMs = 120000 } = {}) {
  return new Promise((resolve) => {
    const executable = dockerCommand();
    const child = spawn(executable, args, {
      cwd,
      windowsHide: true,
      env: dockerSpawnEnv(executable),
    });
    let stdout = '';
    let stderr = '';
    let timedOut = false;
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    const timeout = timeoutMs > 0 ? setTimeout(() => {
      timedOut = true;
      child.kill();
    }, timeoutMs) : null;
    child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    child.on('error', (error) => {
      if (timeout) clearTimeout(timeout);
      finish({ ok: false, code: -1, stdout, stderr: error.message, timedOut });
    });
    child.on('close', (code) => {
      if (timeout) clearTimeout(timeout);
      finish({ ok: code === 0 && !timedOut, code, stdout, stderr, timedOut });
    });
  });
}

/** Request authenticated gateway JSON over the loopback-only published port. */
function requestGateway(port, apiKey, pathname = GATEWAY_HEALTH_DETAILS_PATH, timeoutMs = 3500) {
  return new Promise((resolve) => {
    const request = http.request({
      host: '127.0.0.1',
      port,
      path: pathname,
      method: 'GET',
      timeout: timeoutMs,
      headers: { Authorization: `Bearer ${apiKey}` },
    }, (response) => {
      let raw = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => {
        if (raw.length < 1024 * 1024) raw += chunk;
      });
      response.on('end', () => {
        let data = null;
        try { data = JSON.parse(raw); } catch (_error) {}
        resolve({
          ok: response.statusCode >= 200 && response.statusCode < 300,
          statusCode: response.statusCode || null,
          data,
        });
      });
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve({ ok: false, statusCode: null, data: null }));
    request.end();
  });
}

/** Fetch the newest published semantic version without accepting redirects. */
function fetchLatestVersion(timeoutMs = 8000) {
  const headers = {
    Accept: 'application/vnd.github+json',
    'User-Agent': 'omlorix-server-launcher',
  };
  return new Promise((resolve, reject) => {
    const request = https.get({
      hostname: 'api.github.com',
      path: '/repos/phinaldoo/omlorix/releases/latest',
      timeout: timeoutMs,
      headers,
    }, (response) => {
      let raw = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => {
        if (raw.length < 1024 * 1024) raw += chunk;
      });
      response.on('end', () => {
        if (response.statusCode !== 200) {
          reject(managerError('UPDATE_CHECK_FAILED', 'Could not check the latest Code Execution release.'));
          return;
        }
        try {
          const payload = JSON.parse(raw);
          const version = String(payload.tag_name || '').replace(/^v/i, '');
          if (!VERSION_PATTERN.test(version)) throw new Error('invalid version');
          resolve({ version, releaseUrl: String(payload.html_url || '') });
        } catch (_error) {
          reject(managerError('UPDATE_CHECK_FAILED', 'The Code Execution release response was invalid.'));
        }
      });
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => reject(managerError('UPDATE_CHECK_FAILED', 'Could not check the latest Code Execution release.')));
  });
}

/** Fetch the concrete stable releases used by the launcher's version picker. */
function fetchAvailableVersions(timeoutMs = 8000) {
  const headers = {
    Accept: 'application/vnd.github+json',
    'User-Agent': 'omlorix-server-launcher',
  };
  return new Promise((resolve, reject) => {
    const request = https.get({
      hostname: 'api.github.com',
      path: '/repos/phinaldoo/omlorix-code-execution/releases?per_page=50',
      timeout: timeoutMs,
      headers,
    }, (response) => {
      let raw = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => {
        if (raw.length < 1024 * 1024) raw += chunk;
      });
      response.on('end', () => {
        if (response.statusCode !== 200) {
          reject(managerError('VERSION_LIST_FAILED', 'Could not load Code Execution releases.'));
          return;
        }
        try {
          const versions = parseReleaseVersions(JSON.parse(raw));
          if (!versions.length) throw new Error('no stable releases');
          resolve({ versions });
        } catch (_error) {
          reject(managerError('VERSION_LIST_FAILED', 'The Code Execution releases response was invalid.'));
        }
      });
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => reject(managerError('VERSION_LIST_FAILED', 'Could not load Code Execution releases.')));
  });
}

class CodeExecutionManager extends EventEmitter {
  constructor({ app, appRoot, serverManager }) {
    super();
    this.app = app;
    this.appRoot = appRoot;
    this.serverManager = serverManager;
    // Stateful launcher features live below the same server home accepted by
    // `omlorix-server --home`, so either surface sees the identical registry.
    this.home = path.join(serverManager?.serverHome || path.join(app.getPath('userData'), 'server'), 'code-execution');
    this.legacyHome = path.join(app.getPath('userData'), 'code-execution');
    this.instancesHome = path.join(this.home, 'instances');
    this.registryFile = path.join(this.home, 'instances.json');
    this.registryWrite = Promise.resolve();
    this.activeOperations = new Set();
    this.availableVersionsCache = null;
    this.availableVersionsRequest = null;
    this.homeReady = false;
  }

  bundleRoot() {
    return this.app.isPackaged
      ? path.join(process.resourcesPath, 'code-execution-bundle')
      : path.join(this.appRoot, 'electron', 'code-execution');
  }

  /** Refresh immutable Compose/proxy files for a new or existing instance. */
  async syncInstanceBundle(instanceId) {
    const home = this.instanceHome(instanceId);
    await fs.mkdir(home, { recursive: true, mode: 0o700 });
    for (const name of INSTANCE_BUNDLE_FILES) {
      await fs.copyFile(path.join(this.bundleRoot(), name), path.join(home, name));
    }
  }

  /** Locate the adjacent source checkout used by the development launcher. */
  localSourceRoot() {
    const candidate = path.resolve(this.appRoot, '..', 'code_execution');
    const required = [
      path.join(candidate, 'gateway', 'Dockerfile'),
      path.join(candidate, 'sandbox', 'Dockerfile'),
    ];
    return !this.app.isPackaged && required.every((file) => fssync.existsSync(file))
      ? candidate
      : '';
  }

  async ensureHome({ sharedLockHeld = false } = {}) {
    if (this.homeReady) return;
    const prepare = async () => {
      // Older launchers stored this directory beside `server`. Move it only
      // when the canonical CLI-compatible location is unused; an existing
      // canonical registry always wins and the legacy copy remains recoverable.
      if (
        path.resolve(this.legacyHome) !== path.resolve(this.home)
        && !fssync.existsSync(this.home)
        && fssync.existsSync(this.legacyHome)
      ) {
        await fs.mkdir(path.dirname(this.home), { recursive: true, mode: 0o700 });
        await fs.rename(this.legacyHome, this.home);
      }
      await fs.mkdir(this.instancesHome, { recursive: true, mode: 0o700 });
      if (!fssync.existsSync(this.registryFile)) {
        await this.persistRegistry({ version: REGISTRY_VERSION, instances: [] });
      }
      this.homeReady = true;
    };
    if (!sharedLockHeld && typeof this.serverManager?.withSharedOperationLock === 'function') {
      await this.serverManager.withSharedOperationLock('code-execution migrate', prepare);
      return;
    }
    await prepare();
  }

  async readRegistry() {
    await this.ensureHome();
    try {
      const parsed = JSON.parse(await fs.readFile(this.registryFile, 'utf8'));
      const instances = Array.isArray(parsed.instances) ? parsed.instances : [];
      return {
        version: REGISTRY_VERSION,
        instances: instances.filter((item) => INSTANCE_ID_PATTERN.test(String(item?.id || ''))),
      };
    } catch (_error) {
      throw managerError('REGISTRY_INVALID', 'The Code Execution instance registry could not be read.');
    }
  }

  async persistRegistry(registry) {
    await fs.mkdir(this.home, { recursive: true, mode: 0o700 });
    const next = JSON.stringify({
      version: REGISTRY_VERSION,
      instances: registry.instances || [],
    }, null, 2);
    const temporary = `${this.registryFile}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
    await fs.writeFile(temporary, `${next}\n`, { encoding: 'utf8', mode: 0o600 });
    await fs.rename(temporary, this.registryFile);
  }

  /** Serialize one complete registry read-modify-write transaction. */
  async updateRegistry(mutation) {
    const update = async () => {
      const registry = await this.readRegistry();
      return mutation(registry);
    };
    const pending = this.registryWrite.then(update, update);
    this.registryWrite = pending.then(() => undefined, () => undefined);
    return pending;
  }

  instanceHome(instanceId) {
    if (!INSTANCE_ID_PATTERN.test(String(instanceId || ''))) {
      throw managerError('INSTANCE_INVALID', 'The Code Execution instance identifier is invalid.');
    }
    const resolved = path.resolve(this.instancesHome, instanceId);
    const relative = path.relative(path.resolve(this.instancesHome), resolved);
    if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
      throw managerError('INSTANCE_INVALID', 'The Code Execution instance path is invalid.');
    }
    return resolved;
  }

  async registeredInstance(instanceId) {
    const registry = await this.readRegistry();
    const instance = registry.instances.find((item) => item.id === instanceId);
    if (!instance) throw managerError('INSTANCE_NOT_FOUND', 'The Code Execution instance was not found.');
    return { registry, instance };
  }

  async readInstanceEnv(instanceId) {
    return parseEnv(await fs.readFile(path.join(this.instanceHome(instanceId), '.env'), 'utf8'));
  }

  composeArgs(instanceId) {
    const home = this.instanceHome(instanceId);
    return [
      'compose',
      '--env-file', path.join(home, '.env'),
      '-f', path.join(home, 'docker-compose.yml'),
    ];
  }

  normalizeSettings(payload = {}, existing = {}) {
    const name = String(payload.name ?? existing.name ?? 'Code Execution').trim().slice(0, 80);
    if (!name) throw managerError('NAME_REQUIRED', 'Enter a name for the Code Execution service.');
    const version = String(payload.version ?? existing.version ?? DEFAULT_VERSION).trim().replace(/^v/i, '');
    if (!VERSION_PATTERN.test(version)) {
      throw managerError('VERSION_INVALID', 'Enter a semantic Code Execution version such as 0.9.0.');
    }
    const port = boundedInteger(payload.port ?? existing.port, 8000, 1, 65535);
    const memory = String(payload.memory ?? existing.memory ?? '512m').toLowerCase();
    if (!MEMORY_OPTIONS.has(memory)) {
      throw managerError('MEMORY_INVALID', 'Choose a supported sandbox memory limit.');
    }
    const imageSource = String(payload.imageSource ?? existing.imageSource ?? 'release').trim();
    if (!['local', 'release'].includes(imageSource)) {
      throw managerError('IMAGE_SOURCE_INVALID', 'Choose a supported Code Execution image source.');
    }
    if (imageSource === 'local' && !this.localSourceRoot()) {
      throw managerError('SOURCE_MISSING', 'The local Code Execution source checkout could not be found.');
    }
    return {
      name,
      version,
      imageSource,
      port,
      memory,
      maxConcurrent: boundedInteger(payload.maxConcurrent ?? existing.maxConcurrent, 10, 1, 100),
      sessionTimeout: boundedInteger(payload.sessionTimeout ?? existing.sessionTimeout, 1200, 60, 86400),
      networkAccess: boolValue(payload.networkAccess ?? existing.networkAccess),
      allowPip: boolValue(payload.allowPip ?? existing.allowPip),
    };
  }

  /**
   * Resolve an omitted version at creation time.
   *
   * Every launcher surface resolves the same published release regardless of
   * whether Electron itself is packaged or running from source. Explicit
   * versions remain supported for pinning and reproducible rollbacks.
   */
  async resolveCreationVersion(payload = {}) {
    if (String(payload.version || '').trim()) return payload;

    const latest = await fetchLatestVersion();
    return { ...payload, version: latest.version };
  }

  /**
   * Return cached published releases for the launcher version picker.
   */
  async availableReleaseVersions() {
    const cached = this.availableVersionsCache;
    if (cached && Date.now() - cached.loadedAt < VERSION_CACHE_MS) return cached.value;
    if (this.availableVersionsRequest) return this.availableVersionsRequest;

    // Coalesce concurrent modal opens and retain the result briefly. This
    // keeps the picker responsive without needlessly consuming GitHub's API
    // quota.
    this.availableVersionsRequest = (async () => {
      // GitHub's dedicated latest endpoint is authoritative. The release-list
      // order can differ when an older release is published after a newer tag.
      const [latest, releases] = await Promise.all([
        fetchLatestVersion(),
        fetchAvailableVersions(),
      ]);
      const value = {
        latestVersion: latest.version,
        versions: [
          latest.version,
          ...releases.versions.filter((version) => version !== latest.version),
        ],
      };
      this.availableVersionsCache = { loadedAt: Date.now(), value };
      return value;
    })();

    try {
      return await this.availableVersionsRequest;
    } finally {
      this.availableVersionsRequest = null;
    }
  }

  /** Return the same published release choices in development and production. */
  async availableVersions() {
    const releases = await this.availableReleaseVersions();
    return {
      source: 'release',
      latestVersion: releases.latestVersion,
      versions: releases.versions,
      releaseError: false,
    };
  }

  envFor(instance, settings, secret) {
    const imageRoot = 'ghcr.io/phinaldoo/omlorix-code-execution';
    const localImages = instance.imageSource === 'local';
    return {
      COMPOSE_PROJECT_NAME: `omlorix-codeexec-${instance.id}`,
      CODE_EXECUTION_INSTANCE_ID: instance.id,
      CODE_EXECUTION_NETWORK_ALIAS: `codeexec-${instance.id}`,
      CODE_EXECUTION_VERSION: settings.version,
      CODE_EXECUTION_GATEWAY_IMAGE: localImages
        ? `omlorix-code-execution-gateway:${settings.version}`
        : `${imageRoot}-gateway:${settings.version}`,
      SANDBOX_IMAGE: localImages
        ? `omlorix-code-execution-sandbox:${settings.version}`
        : `${imageRoot}-sandbox:${settings.version}`,
      GATEWAY_HOST_BIND: '127.0.0.1',
      GATEWAY_PORT: settings.port,
      ...CODE_EXECUTION_RUNTIME_DEFAULTS,
      APP_ENV: 'production',
      ALLOW_RESTRICTED_LOCAL_DOCKER_PROXY: 'true',
      REQUIRE_AUTH: 'true',
      METRICS_AUTH_REQUIRED: 'true',
      API_KEYS: `launcher:${secret}`,
      ENABLE_DOCS: 'false',
      ENABLE_CORS: 'false',
      ALLOWED_HOSTS: `127.0.0.1,localhost,gateway,codeexec-${instance.id}`,
      RENDER_ALLOWED_HOSTS: '127.0.0.1,localhost,gateway',
      REQUIRE_SHARED_STATE: 'true',
      SANDBOX_NETWORK_MODE: settings.networkAccess ? 'bridge' : 'none',
      ALLOW_PIP_INSTALLS: settings.allowPip ? 'true' : 'false',
      ALLOW_SANDBOX_ENV_INJECTION: 'false',
      USE_DOCKER_DEFAULT_SECCOMP: 'true',
      REQUIRE_STRONG_SANDBOX_ISOLATION: 'false',
      MAX_CONCURRENT_EXECUTIONS: settings.maxConcurrent,
      MAX_ACTIVE_SESSIONS: Math.max(settings.maxConcurrent * 10, 20),
      MAX_CONTAINERS_PER_PRINCIPAL: 3,
      DEFAULT_TIMEOUT: 30,
      SESSION_TIMEOUT_SECONDS: settings.sessionTimeout,
      MAX_SESSION_LIFETIME_SECONDS: Math.max(settings.sessionTimeout, 3600),
      SANDBOX_MEM_LIMIT: settings.memory,
      SANDBOX_CPU_PERIOD: 100000,
      SANDBOX_CPU_QUOTA: 100000,
      SANDBOX_PIDS_LIMIT: 256,
      SANDBOX_READ_ONLY_ROOTFS: 'true',
    };
  }

  async create(payload = {}, options = {}) {
    return this.runOperation('__registry__', 'Create Code Execution', async () => {
    // Source and packaged launchers intentionally create identical immutable
    // release-image deployments. Local-source instances are legacy-only.
    const releasePayload = { ...payload, imageSource: 'release' };
    const settings = this.normalizeSettings(await this.resolveCreationVersion(releasePayload));
    const instanceId = await this.updateRegistry(async (registry) => {
      if (registry.instances.some((item) => Number(item.port) === settings.port)) {
        throw managerError('PORT_IN_USE', 'That gateway port is already assigned to another managed instance.');
      }
      if (!(await portAvailable('127.0.0.1', settings.port))) {
        throw managerError('PORT_IN_USE', 'That gateway port is already in use on this computer.');
      }

      const id = `${slugify(settings.name)}-${crypto.randomBytes(4).toString('hex')}`.slice(0, 63);
      const instance = {
        id,
        ...settings,
        // The renderer selects this explicitly: release choices pull immutable
        // images, while the development-only local choice builds the adjacent
        // checkout under its declared semantic version.
        imageSource: settings.imageSource,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      };
      const home = this.instanceHome(id);
      await fs.mkdir(home, { recursive: true, mode: 0o700 });
      try {
        await this.syncInstanceBundle(id);
        await fs.writeFile(path.join(home, '.env_sandbox'), '', { encoding: 'utf8', mode: 0o600 });
        const secret = crypto.randomBytes(32).toString('hex');
        await fs.writeFile(path.join(home, '.env'), serializeEnv(this.envFor(instance, settings, secret)), {
          encoding: 'utf8',
          mode: 0o600,
        });
        registry.instances.push(instance);
        await this.persistRegistry(registry);
      } catch (error) {
        await fs.rm(home, { recursive: true, force: true });
        throw error;
      }
      return id;
    });
      return this.get(instanceId);
    }, options);
  }

  async save(instanceId, payload = {}, options = {}) {
    return this.runOperation(instanceId, 'Save Code Execution', async () => {
    await this.updateRegistry(async (registry) => {
      const instance = registry.instances.find((item) => item.id === instanceId);
      if (!instance) throw managerError('INSTANCE_NOT_FOUND', 'The Code Execution instance was not found.');
      const settings = this.normalizeSettings(payload, instance);
      if (registry.instances.some((item) => item.id !== instanceId && Number(item.port) === settings.port)) {
        throw managerError('PORT_IN_USE', 'That gateway port is already assigned to another managed instance.');
      }
      if (Number(instance.port) !== settings.port && !(await portAvailable('127.0.0.1', settings.port))) {
        throw managerError('PORT_IN_USE', 'That gateway port is already in use on this computer.');
      }
      const envPath = path.join(this.instanceHome(instanceId), '.env');
      // Snapshot both data and restorable metadata before changing the live
      // environment so a registry failure cannot leave split configuration.
      const originalEnvMetadata = await fs.stat(envPath);
      const originalEnvContents = await fs.readFile(envPath);
      const env = parseEnv(originalEnvContents.toString('utf8'));
      const secret = String(env.API_KEYS || '').split(':').slice(1).join(':');
      if (!secret) throw managerError('SECRET_MISSING', 'The Code Execution API key is missing.');
      const updated = { ...instance, ...settings, updatedAt: new Date().toISOString() };
      await fs.writeFile(
        envPath,
        serializeEnv(this.envFor(updated, settings, secret)),
        { encoding: 'utf8', mode: 0o600 },
      );
      registry.instances = registry.instances.map((item) => item.id === instanceId ? updated : item);
      try {
        await this.persistRegistry(registry);
      } catch (registryError) {
        try {
          await fs.writeFile(envPath, originalEnvContents);
          await fs.chmod(envPath, originalEnvMetadata.mode);
          await fs.utimes(envPath, originalEnvMetadata.atime, originalEnvMetadata.mtime);
        } catch (rollbackError) {
          // Preserve the registry error as the public failure while retaining
          // rollback diagnostics for callers that explicitly inspect it.
          registryError.rollbackError = rollbackError;
        }
        throw registryError;
      }
    });
      return this.get(instanceId);
    }, options);
  }

  async status(instance) {
    const home = this.instanceHome(instance.id);
    const env = await this.readInstanceEnv(instance.id).catch(() => ({}));
    const result = await executeDocker(
      [...this.composeArgs(instance.id), 'ps', '--all', '--format', 'json'],
      { cwd: home, timeoutMs: 10000 },
    );
    const services = result.ok ? parseComposeRows(result.stdout) : [];
    const gateway = services.find((row) => String(row.Service || '').toLowerCase() === 'gateway');
    const running = String(gateway?.State || '').toLowerCase() === 'running';
    const apiKey = String(env.API_KEYS || '').split(':').slice(1).join(':');
    const health = running && apiKey
      ? await requestGateway(instance.port, apiKey)
      : { ok: false, statusCode: null, data: null };
    return {
      running,
      healthy: health.ok,
      healthStatus: String(health.data?.status || (running ? 'starting' : 'stopped')),
      services,
      activeExecutions: Number(health.data?.metrics?.active_executions || 0),
      activeRenders: Number(health.data?.metrics?.active_renders || 0),
      sandboxImageAvailable: Boolean(health.data?.sandbox_image_available),
      runtime: String(health.data?.sandbox_runtime || ''),
      composeError: result.ok ? '' : 'Docker Compose status is unavailable.',
    };
  }

  publicInstance(instance, status = null) {
    return {
      ...instance,
      homeName: instance.id,
      localUrl: `http://127.0.0.1:${instance.port}`,
      connectionUrl: `http://codeexec-${instance.id}:8000`,
      busy: this.activeOperations.has(instance.id),
      status,
    };
  }

  async list() {
    const registry = await this.readRegistry();
    const values = await Promise.all(registry.instances.map(async (instance) => (
      this.publicInstance(instance, await this.status(instance))
    )));
    return { instances: values, sharedNetwork: SHARED_NETWORK };
  }

  async get(instanceId) {
    const { instance } = await this.registeredInstance(instanceId);
    return this.publicInstance(instance, await this.status(instance));
  }

  async ensureSharedNetwork() {
    if (this.serverManager?.ensureLauncherServicesNetwork) {
      await this.serverManager.ensureLauncherServicesNetwork();
    } else {
      const inspect = await executeDocker(['network', 'inspect', SHARED_NETWORK], { cwd: this.home, timeoutMs: 10000 });
      if (!inspect.ok) {
        const create = await executeDocker([
          'network', 'create',
          '--label', 'com.omlorix.launcher.managed=true',
          SHARED_NETWORK,
        ], { cwd: this.home, timeoutMs: 10000 });
        if (!create.ok) throw managerError('NETWORK_FAILED', 'Could not create the private launcher services network.');
      }
    }
    await this.serverManager?.attachRunningBackendToLauncherServicesNetwork?.();
  }

  async runOperation(instanceId, name, operation, options = {}) {
    const execute = async () => {
      await this.ensureHome({ sharedLockHeld: true });
      if (options.nested) return operation();
    if (this.activeOperations.has(instanceId)) {
      throw managerError('INSTANCE_BUSY', 'Another action is already running for this instance.');
    }
    this.activeOperations.add(instanceId);
    this.emit('operation-start', { instanceId, name });
    try {
      const result = await operation();
      this.emit('operation-end', { instanceId, name, ok: true });
      return result;
    } catch (error) {
      this.emit('operation-end', {
        instanceId,
        name,
        ok: false,
        message: error?.message || 'Code Execution action failed.',
      });
      throw error;
    } finally {
      this.activeOperations.delete(instanceId);
    }
    };
    if (typeof this.serverManager?.withSharedOperationLock === 'function') {
      return this.serverManager.withSharedOperationLock(
        name.toLowerCase(),
        execute,
        { lockHeld: options.sharedLockHeld === true },
      );
    }
    return execute();
  }

  emitOutput(instanceId, name, text) {
    this.emit('operation-output', { instanceId, name, text: String(text || '') });
  }

  /** Wait until the authenticated gateway reports ready after a Compose up. */
  async waitForHealthy(instance, apiKey, timeoutMs = 90000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const health = await requestGateway(instance.port, apiKey, GATEWAY_HEALTH_PATH, 2500);
      if (health.ok) return;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    throw managerError('READY_TIMEOUT', 'Code Execution started but did not become healthy in time.');
  }

  async start(instanceId, options = {}) {
    return this.runOperation(instanceId, 'Start Code Execution', async () => {
      const { instance } = await this.registeredInstance(instanceId);
      const home = this.instanceHome(instanceId);
      const env = await this.readInstanceEnv(instanceId);
      await this.syncInstanceBundle(instanceId);
      await this.ensureSharedNetwork();
      if (instance.imageSource === 'local') {
        const sourceRoot = this.localSourceRoot();
        if (!sourceRoot) {
          throw managerError('SOURCE_MISSING', 'The local Code Execution source checkout could not be found.');
        }
        const builds = [
          [env.CODE_EXECUTION_GATEWAY_IMAGE, path.join(sourceRoot, 'gateway')],
          [env.SANDBOX_IMAGE, path.join(sourceRoot, 'sandbox')],
        ];
        for (const [image, context] of builds) {
          this.emitOutput(instanceId, 'Start Code Execution', `Building ${image}\n`);
          const build = await executeDocker(
            ['build', '--tag', image, context],
            { cwd: sourceRoot, timeoutMs: 30 * 60 * 1000 },
          );
          if (!build.ok) throw managerError('IMAGE_BUILD_FAILED', 'Could not build the local Code Execution images.');
        }
      } else {
        for (const image of [env.CODE_EXECUTION_GATEWAY_IMAGE, env.SANDBOX_IMAGE]) {
          this.emitOutput(instanceId, 'Start Code Execution', `Pulling ${image}\n`);
          const pull = await executeDocker(['pull', image], { cwd: home, timeoutMs: 30 * 60 * 1000 });
          if (!pull.ok) throw managerError('IMAGE_PULL_FAILED', 'Could not pull the Code Execution images.');
        }
      }
      const up = await executeDocker(
        [...this.composeArgs(instanceId), 'up', '-d', '--force-recreate', '--remove-orphans'],
        { cwd: home, timeoutMs: 180000 },
      );
      if (!up.ok) throw managerError('START_FAILED', 'Could not start the Code Execution service.');
      const apiKey = String(env.API_KEYS || '').split(':').slice(1).join(':');
      await this.waitForHealthy(instance, apiKey);
      return this.get(instanceId);
    }, options);
  }

  async stop(instanceId, options = {}) {
    return this.runOperation(instanceId, 'Stop Code Execution', async () => {
      await this.registeredInstance(instanceId);
      const result = await executeDocker(
        [...this.composeArgs(instanceId), 'down', '--remove-orphans'],
        { cwd: this.instanceHome(instanceId), timeoutMs: 120000 },
      );
      if (!result.ok) throw managerError('STOP_FAILED', 'Could not stop the Code Execution service.');
      return this.get(instanceId);
    }, options);
  }

  async restart(instanceId, options = {}) {
    return this.runOperation(instanceId, 'Restart Code Execution', async () => {
      const { instance } = await this.registeredInstance(instanceId);
      const env = await this.readInstanceEnv(instanceId);
      await this.syncInstanceBundle(instanceId);
      await this.ensureSharedNetwork();
      const result = await executeDocker(
        [...this.composeArgs(instanceId), 'up', '-d', '--force-recreate', '--remove-orphans'],
        { cwd: this.instanceHome(instanceId), timeoutMs: 180000 },
      );
      if (!result.ok) throw managerError('RESTART_FAILED', 'Could not restart the Code Execution service.');
      const apiKey = String(env.API_KEYS || '').split(':').slice(1).join(':');
      await this.waitForHealthy(instance, apiKey);
      return this.get(instanceId);
    }, options);
  }

  async checkUpdate(instanceId) {
    const { instance } = await this.registeredInstance(instanceId);
    const latest = await fetchLatestVersion();
    return {
      currentVersion: instance.version,
      latestVersion: latest.version,
      updateAvailable: instance.version !== latest.version,
      releaseUrl: latest.releaseUrl,
    };
  }

  async update(instanceId) {
    return this.runOperation(instanceId, 'Update Code Execution', async () => {
      const nested = { sharedLockHeld: true, nested: true };
      const { instance: previousInstance } = await this.registeredInstance(instanceId);
      const latest = await this.checkUpdate(instanceId);
      if (!latest.updateAvailable) return this.get(instanceId);
      await this.save(instanceId, { version: latest.latestVersion }, nested);
      try {
        return await this.start(instanceId, nested);
      } catch (updateError) {
      // A pull or health failure must not leave the registry claiming that an
      // unavailable image is current. Restore through the normal two-file save
      // transaction, then recreate the last known configuration.
      try {
        await this.save(instanceId, previousInstance, nested);
        await this.start(instanceId, nested);
      } catch (rollbackError) {
        const error = managerError(
          'UPDATE_ROLLBACK_FAILED',
          `The Code Execution update failed, and version ${previousInstance.version} could not be restored.`,
        );
        error.cause = updateError;
        error.rollbackError = rollbackError;
        throw error;
      }
      const error = managerError(
        'UPDATE_FAILED_ROLLED_BACK',
        `The Code Execution update failed. Version ${previousInstance.version} was restored.`,
      );
      error.cause = updateError;
        throw error;
      }
    });
  }

  async logs(instanceId, lines = 250) {
    await this.registeredInstance(instanceId);
    const count = boundedInteger(lines, 250, 20, 2000);
    const result = await executeDocker(
      [...this.composeArgs(instanceId), 'logs', '--tail', String(count), '--no-color'],
      { cwd: this.instanceHome(instanceId), timeoutMs: 15000 },
    );
    if (!result.ok) throw managerError('LOGS_FAILED', 'Could not read Code Execution logs.');
    return result.stdout;
  }

  async connectionDetails(instanceId, options = {}) {
    return this.runOperation(instanceId, 'Connect Code Execution', async () => {
    const { instance } = await this.registeredInstance(instanceId);
    const env = await this.readInstanceEnv(instanceId);
    const apiKey = String(env.API_KEYS || '').split(':').slice(1).join(':');
    if (!apiKey) throw managerError('SECRET_MISSING', 'The Code Execution API key is missing.');

    // The copied hostname is a private Docker alias, not a host-loopback URL.
    // Repair the shared-network attachment immediately before handing it to
    // Omlorix so older/running stacks and development Compose stacks can resolve
    // the alias without requiring an otherwise unrelated service restart.
    await this.ensureSharedNetwork();
      return {
      name: instance.name,
      base_url: `http://codeexec-${instance.id}:8000`,
      api_key: apiKey,
      enabled_for_code_execution: true,
      enabled_for_latex_pdf: true,
      enabled_for_slide_renderer: true,
      weight: 1,
      adminUrl: '/admin/service-connections',
      };
    }, options);
  }

  async remove(instanceId, options = {}) {
    return this.runOperation(instanceId, 'Delete Code Execution', async () => {
      await this.updateRegistry(async (registry) => {
        const instance = registry.instances.find((item) => item.id === instanceId);
        if (!instance) throw managerError('INSTANCE_NOT_FOUND', 'The Code Execution instance was not found.');
        const home = this.instanceHome(instanceId);
        const down = await executeDocker(
          [...this.composeArgs(instanceId), 'down', '--remove-orphans', '--volumes'],
          { cwd: home, timeoutMs: 120000 },
        );
        if (!down.ok) throw managerError('DELETE_FAILED', 'Could not stop and remove the Code Execution containers.');
        registry.instances = registry.instances.filter((item) => item.id !== instanceId);
        await this.persistRegistry(registry);
        await fs.rm(home, { recursive: true, force: true });
      });
      return { deleted: true, instanceId };
    }, options);
  }

  async reveal(instanceId, shell) {
    await this.registeredInstance(instanceId);
    const error = await shell.openPath(this.instanceHome(instanceId));
    if (error) throw managerError('REVEAL_FAILED', 'Could not open the Code Execution instance folder.');
    return { ok: true };
  }
}

module.exports = {
  CODE_EXECUTION_RUNTIME_DEFAULTS,
  CodeExecutionManager,
  DEFAULT_VERSION,
  GATEWAY_HEALTH_PATH,
  GATEWAY_HEALTH_DETAILS_PATH,
  SHARED_NETWORK,
  parseReleaseVersions,
  parseComposeRows,
  parseEnv,
  serializeEnv,
  slugify,
};
