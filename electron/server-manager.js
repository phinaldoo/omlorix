const { EventEmitter } = require('events');
const { spawn } = require('child_process');
const crypto = require('crypto');
const fs = require('fs/promises');
const fssync = require('fs');
const http = require('http');
const https = require('https');
const net = require('node:net');
const path = require('path');
const { compareVersions, normalizeVersion } = require('./version-utils');
const {
  DEFAULT_CHANNEL,
  UPDATE_CHANNELS,
  channelLabel,
  fetchJson,
  normalizeUpdateChannel,
  resolveAvailableVersions,
  resolveReleaseInfo,
} = require('./release-channels');
const {
  LauncherReverseProxy,
  normalizeProxyConfig,
  validateProxyConfig,
} = require('./server-proxy');
const { common: SERVER_FILES } = require('../cmd/omlorix-server-cli/server-files.json');
const {
  defaultLines: DEFAULT_LOG_LINES,
  minimumLines: MIN_LOG_LINES,
  maximumLines: MAX_LOG_LINES,
  maximumTimeBoundLength: MAX_LOG_TIME_BOUND_LENGTH,
} = require('../cmd/omlorix-server-cli/server-management-contract.json').logs;

// Launcher-managed helper services use one private external network. Keeping
// this outside either Compose project's lifecycle lets Omlorix and any number of
// independently updated helpers communicate without publishing helper APIs to
// the LAN.
const LAUNCHER_SERVICES_NETWORK = 'omlorix-launcher-services';

const ENV_ENUM_OPTIONS = {
  DB_MIGRATIONS_MODE: ['off', 'auto', 'required'],
  FILE_STORAGE_PROVIDER: ['local', 's3', 'gcs', 'azure', 'webdav'],
  MODE: ['production', 'dev'],
  PGBOUNCER_POOL_MODE: ['session', 'transaction'],
  OTEL_TRACES_SAMPLER: ['always_on', 'always_off', 'traceidratio', 'parentbased_traceidratio'],
};

const STORAGE_PROVIDERS = new Set(['local', 's3', 'gcs', 'azure', 'webdav']);
const STORAGE_MIGRATION_SCOPES = new Set(['all', 'files', 'deep-research', 'presentations']);
const LOG_FOLLOW_FORCE_STOP_MS = 2000;
const AVAILABLE_VERSIONS_CACHE_MAX_AGE_MS = 15 * 60 * 1000;
const RELEASE_CHECK_FAILURE_COOLDOWN_MS = 60 * 1000;
const BACKUP_JOB_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const BACKUP_DOWNLOAD_FILENAME_PATTERN = /^omlorix-backup-[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.tar\.zst(?:\.enc)?$/;

const ENV_REQUIRED_KEYS = new Set([
  'JWT_SECRET_KEY',
  'LOG_IP_HASH_SALT',
  'ENCRYPTION_KEY',
  'DATABASE_PASSWORD',
]);

// Retired variables are accepted only long enough to migrate existing
// installations and legacy imports. They must never reach the environment
// editor, Compose, or a newly written recovery snapshot.
const RETIRED_ENV_KEYS = new Set([
  'OMLORIX_GITHUB_TOKEN',
]);

const LAUNCHER_HIDDEN_ENV_KEYS = new Set([
  ...RETIRED_ENV_KEYS,
  'OMLORIX_INSTALLATION_ID',
  'OMLORIX_ALLOW_PROJECT_ADOPTION',
  'OMLORIX_UPDATE_CHANNEL',
  'OMLORIX_BACKEND_IMAGE_REPOSITORY',
  'OMLORIX_FRONTEND_IMAGE_REPOSITORY',
  'FILE_SCANNER_COMMAND',
  // The bundled migration endpoint is derived topology, not an operator-facing
  // application connection setting.
  'DATABASE_MIGRATION_HOST_OVERRIDE',
  'DATABASE_MIGRATION_PORT_OVERRIDE',
  // This security switch is derived from the launcher proxy state. Keeping it
  // out of the generic editor prevents an unsafe proxy/header combination.
  'FRONTEND_TRUST_PROXY_HEADERS',
  'FRONTEND_TRUSTED_UPSTREAMS',
  'OMLORIX_LAUNCHER_PROXY_SECRET',
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
  'CADDY_HTTP_HOST_PORT',
  'CADDY_HTTPS_HOST_PORT',
]);

const ENV_SECRET_KEYS = new Set([
  'DATABASE_URL',
  'REDIS_URL',
]);

// The recovery bundle deliberately contains every launcher-managed credential
// that can be required to restore a deployment. Keeping this list explicit
// prevents ordinary configuration values from accidentally leaking into a
// secrets-only export while still covering bundled and external services.
const SECRET_BACKUP_KEYS = [
  'JWT_SECRET_KEY',
  'ENCRYPTION_KEY',
  'PASSWORD_RESET_IDENTIFIER_HASH_SALT',
  'LOG_IP_HASH_SALT',
  'BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE',
  'DATABASE_PASSWORD',
  'DATABASE_URL',
  'REDIS_PASSWORD',
  'REDIS_URL',
  'MINIO_ROOT_USER',
  'MINIO_ROOT_PASSWORD',
  'FILE_STORAGE_S3_ACCESS_KEY_ID',
  'FILE_STORAGE_S3_SECRET_ACCESS_KEY',
  'FILE_STORAGE_S3_SESSION_TOKEN',
  'FILE_STORAGE_GCS_CREDENTIALS_JSON',
  'FILE_STORAGE_AZURE_CONNECTION_STRING',
  'FILE_STORAGE_AZURE_CREDENTIAL',
  'FILE_STORAGE_WEBDAV_USERNAME',
  'FILE_STORAGE_WEBDAV_PASSWORD',
  'OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE',
  'OMLORIX_LAUNCHER_PROXY_SECRET',
  'GRAFANA_ADMIN_USER',
  'GRAFANA_ADMIN_PASSWORD',
];

const SECRET_BACKUP_CONTEXT_KEYS = [
  'COMPOSE_PROJECT_NAME',
  'OMLORIX_INSTALLATION_ID',
  'OMLORIX_USE_BUNDLED_DB',
  'OMLORIX_USE_BUNDLED_REDIS',
  'REDIS_ENABLED',
  'OMLORIX_USE_BUNDLED_STORAGE',
  'DATABASE_NAME',
  'DATABASE_USER',
  'FILE_STORAGE_PROVIDER',
];

// Only credentials with a safe local generation strategy may be rotated by
// the setup UI. External-service credentials must always come from their
// provider instead of being replaced with plausible but unusable random text.
const REGENERATABLE_SECRET_KEYS = new Set([
  'JWT_SECRET_KEY',
  'ENCRYPTION_KEY',
  'PASSWORD_RESET_IDENTIFIER_HASH_SALT',
  'LOG_IP_HASH_SALT',
  'BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE',
  'DATABASE_PASSWORD',
  'REDIS_PASSWORD',
  'MINIO_ROOT_USER',
  'MINIO_ROOT_PASSWORD',
  'GRAFANA_ADMIN_USER',
  'GRAFANA_ADMIN_PASSWORD',
]);
const SETUP_STATE_VERSION = 1;
const LAUNCHER_METADATA_VERSION = 2;
const SERVER_SETTINGS_VERSION = 2;

// These values configure the host process owned by the Launcher/CLI. They are
// projected into the legacy env-shaped runtime API for compatibility, but are
// persisted only in server-settings.json and never passed to Compose.
const MANAGED_PROXY_SETTINGS_ENV_KEYS = new Set([
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
]);

// Recovery snapshots remain dotenv-compatible so older Launcher and CLI
// builds can inspect them. Management-only values are projected into that
// interchange format, then stripped again before the live Compose env is
// committed.
const RECOVERY_MANAGEMENT_SETTINGS_ENV_KEYS = new Set([
  'OMLORIX_UPDATE_CHANNEL',
  ...MANAGED_PROXY_SETTINGS_ENV_KEYS,
]);

const SETTINGS_OWNED_ENV_KEYS = new Set([
  ...MANAGED_PROXY_SETTINGS_ENV_KEYS,
  'COMPOSE_PROJECT_NAME',
  'MODE',
  'OMLORIX_VERSION',
  'BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE',
  'JWT_SECRET_KEY',
  'ENCRYPTION_KEY',
  'PASSWORD_RESET_IDENTIFIER_HASH_SALT',
  'LOG_IP_HASH_SALT',
  'OMLORIX_USE_BUNDLED_DB',
  'OMLORIX_USE_BUNDLED_REDIS',
  'OMLORIX_USE_BUNDLED_STORAGE',
  'FRONTEND_HTTP_HOST_BIND',
  'FRONTEND_HTTP_HOST_PORT',
  'FRONTEND_TRUST_PROXY_HEADERS',
  'OMLORIX_LAUNCHER_PROXY_SECRET',
  'DEV_DATABASE_HOST_PORT',
  'DEV_REDIS_HOST_PORT',
  'API_LB_TRAEFIK_WEB_HOST_PORT',
  'API_LB_TRAEFIK_DASHBOARD_HOST_PORT',
  'TRUST_PROXY_HEADERS',
  'TRUSTED_PROXIES',
  'TRUSTED_HOSTS',
  'UVICORN_FORWARDED_ALLOW_IPS',
  'RATE_LIMIT_TRUSTED_PROXIES',
  'AUTH_TRUSTED_PROXIES',
  'RATE_LIMIT_PROXY_SETTINGS_CACHE_SECONDS',
  'DATABASE_URL',
  'DATABASE_USER',
  'DATABASE_PASSWORD',
  'DATABASE_HOST',
  'DATABASE_PORT',
  'DATABASE_NAME',
  'DATABASE_SCHEMA',
  'DATABASE_AUDIT_LOG_SCHEMA',
  'DATABASE_LOGS_SCHEMA',
  'OMLORIX_AUTO_CREATE_DATABASES',
  'DATABASE_HOST_OVERRIDE',
  'DATABASE_PORT_OVERRIDE',
  'REDIS_ENABLED',
  'REDIS_URL',
  'REDIS_PASSWORD',
  'OMLORIX_USE_PGBOUNCER',
  'PGBOUNCER_HOST_BIND',
  'PGBOUNCER_HOST_PORT',
  'PGBOUNCER_POOL_MODE',
  'PGBOUNCER_MAX_CLIENT_CONN',
  'PGBOUNCER_DEFAULT_POOL_SIZE',
  'PGBOUNCER_RESERVE_POOL_SIZE',
  'MINIO_ROOT_USER',
  'MINIO_ROOT_PASSWORD',
  'MINIO_API_HOST_BIND',
  'MINIO_API_HOST_PORT',
  'MINIO_CONSOLE_HOST_BIND',
  'MINIO_CONSOLE_HOST_PORT',
  'FILE_STORAGE_PROVIDER',
  'FILE_STORAGE_LOCAL_BASE_PATH',
  'FILE_STORAGE_S3_BUCKET',
  'FILE_STORAGE_S3_PREFIX',
  'FILE_STORAGE_S3_REGION',
  'FILE_STORAGE_S3_ENDPOINT_URL',
  'FILE_STORAGE_S3_ACCESS_KEY_ID',
  'FILE_STORAGE_S3_SECRET_ACCESS_KEY',
  'FILE_STORAGE_S3_SESSION_TOKEN',
  'FILE_STORAGE_GCS_BUCKET',
  'FILE_STORAGE_GCS_PREFIX',
  'FILE_STORAGE_GCS_PROJECT',
  'FILE_STORAGE_GCS_CREDENTIALS_JSON',
  'FILE_STORAGE_AZURE_CONTAINER',
  'FILE_STORAGE_AZURE_PREFIX',
  'FILE_STORAGE_AZURE_CONNECTION_STRING',
  'FILE_STORAGE_AZURE_ACCOUNT_URL',
  'FILE_STORAGE_AZURE_CREDENTIAL',
  'FILE_STORAGE_WEBDAV_URL',
  'FILE_STORAGE_WEBDAV_USERNAME',
  'FILE_STORAGE_WEBDAV_PASSWORD',
  'FILE_STORAGE_WEBDAV_PREFIX',
  'FILE_STORAGE_WEBDAV_VERIFY_SSL',
  'FILE_STORAGE_WEBDAV_TIMEOUT',
  'OTEL_ENABLED',
  'OTEL_SERVICE_NAME',
  'OTEL_EXPORTER_OTLP_ENDPOINT',
  'OTEL_EXPORTER_OTLP_INSECURE',
  'OTEL_TRACES_ENABLED',
  'OTEL_TRACES_SAMPLER',
  'OTEL_TRACES_SAMPLER_ARG',
  'OTEL_METRICS_ENABLED',
  'OTEL_PROMETHEUS_EXPORTER_ENABLED',
  'OTEL_LOGS_ENABLED',
  'OTEL_INSTRUMENT_FASTAPI',
  'OTEL_INSTRUMENT_SQLALCHEMY',
  'OTEL_INSTRUMENT_HTTP_CLIENTS',
  'OTEL_SQL_COMMENTER_ENABLED',
  'OTEL_CAPTURE_HTTP_ROUTE',
  'OTEL_CAPTURE_HTTP_USER_AGENT',
  'OTEL_HASH_HTTP_USER_AGENT',
  'OTEL_GRPC_HOST_BIND',
  'OTEL_GRPC_HOST_PORT',
  'OTEL_HTTP_HOST_BIND',
  'OTEL_HTTP_HOST_PORT',
  'OTEL_PROMETHEUS_HOST_BIND',
  'OTEL_PROMETHEUS_HOST_PORT',
  'OTEL_HEALTHCHECK_HOST_BIND',
  'OTEL_HEALTHCHECK_HOST_PORT',
  'JAEGER_UI_HOST_BIND',
  'JAEGER_UI_HOST_PORT',
  'JAEGER_COLLECTOR_HOST_BIND',
  'JAEGER_COLLECTOR_HOST_PORT',
  'PROMETHEUS_HOST_BIND',
  'PROMETHEUS_HOST_PORT',
  'ALERTMANAGER_HOST_BIND',
  'ALERTMANAGER_HOST_PORT',
  'GRAFANA_HOST_BIND',
  'GRAFANA_HOST_PORT',
  'GRAFANA_ADMIN_USER',
  'GRAFANA_ADMIN_PASSWORD',
  'GRAFANA_ROOT_URL',
  'POSTGRES_EXPORTER_DATA_SOURCE_URI',
  'POSTGRES_EXPORTER_DATA_SOURCE_USER',
  'POSTGRES_EXPORTER_DATA_SOURCE_PASS',
  'REDIS_EXPORTER_ADDR',
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
]);

const ENV_SECRET_KEY_PATTERN = /(SECRET|PASSWORD|PASSPHRASE|TOKEN|CREDENTIAL|CONNECTION_STRING|ENCRYPTION_KEY|PRIVATE_KEY|ACCESS_KEY|API_KEY|CLIENT_SECRET|AUTHORIZATION)/i;
const TRUSTED_PROXY_LOOPBACK_CIDRS = [
  '127.0.0.0/8',
  '::1/128',
];
const UVICORN_TRUSTED_PROXY_ENV_VALUE = '127.0.0.1,::1';
// Every launcher-managed Compose file supplies this value when the saved
// TRUSTED_PROXIES entry is missing or empty. Runtime comparisons must model
// that interpolation or an unchanged default installation looks stale forever.
const COMPOSE_DEFAULT_TRUSTED_PROXIES = '';
// These values are loaded into the backend container at creation time. Reading
// them from the live container lets the launcher distinguish saved proxy
// settings from settings that are actually active.
const PROXY_BACKEND_ENV_KEYS = Object.freeze([
  'TRUST_PROXY_HEADERS',
  'TRUSTED_PROXIES',
  'RATE_LIMIT_TRUSTED_PROXIES',
  'AUTH_TRUSTED_PROXIES',
  'UVICORN_FORWARDED_ALLOW_IPS',
]);
const PROXY_BACKEND_ENV_INSPECT_KEYS = new Set([
  ...PROXY_BACKEND_ENV_KEYS,
  'OMLORIX_TRUST_PROXY_HEADERS',
  'OMLORIX_TRUSTED_PROXIES',
]);

class LauncherUpdateRequiredError extends Error {
  constructor({ currentLauncherVersion, minimumLauncherVersion, targetVersion, releaseNotes = '' }) {
    super(`Omlorix ${targetVersion} requires Omlorix Server Launcher ${minimumLauncherVersion} or newer.`);
    this.name = 'LauncherUpdateRequiredError';
    this.code = 'LAUNCHER_UPDATE_REQUIRED';
    this.currentLauncherVersion = currentLauncherVersion;
    this.minimumLauncherVersion = minimumLauncherVersion;
    this.targetVersion = targetVersion;
    this.releaseNotes = releaseNotes;
  }
}

class LegacyComposeAdoptionRequiredError extends Error {
  constructor(project) {
    super(`Compose project ${project} predates installation ownership labels and must be explicitly adopted before lifecycle commands can continue.`);
    this.name = 'LegacyComposeAdoptionRequiredError';
    this.code = 'LEGACY_COMPOSE_ADOPTION_REQUIRED';
    this.project = project;
  }
}

class PossibleDatabaseDowngradeError extends Error {
  constructor({ currentVersion, highestVersion, originalError }) {
    const originalMessage = String(originalError?.message || originalError || 'Omlorix did not become ready.');
    super(
      `Omlorix ${currentVersion} could not start after this server previously ran ${highestVersion}. `
      + 'The newer release may have applied database migrations that an older release cannot reverse or read. '
      + `Use Omlorix ${highestVersion} or newer, or restore a database backup compatible with ${currentVersion}. `
      + `Original startup error: ${originalMessage}`,
    );
    this.name = 'PossibleDatabaseDowngradeError';
    this.code = 'POSSIBLE_DATABASE_DOWNGRADE';
    this.currentVersion = currentVersion;
    this.highestVersion = highestVersion;
    this.originalError = originalError;
    this.messageKey = 'launcher_possible_database_downgrade';
    this.messageValues = {
      currentVersion,
      highestVersion,
      error: originalMessage,
    };
  }
}

class EnvRequirementsError extends Error {
  constructor(status, envFile) {
    const keys = [...status.missingKeys, ...status.invalidKeys];
    const suffix = keys.length ? `: ${keys.join(', ')}` : '.';
    super(`Set all required .env variables before running server actions${suffix}`);
    this.name = 'EnvRequirementsError';
    this.code = 'ENV_REQUIREMENTS_MISSING';
    this.envFile = envFile;
    this.missingRequiredKeys = status.missingKeys;
    this.invalidRequiredKeys = status.invalidKeys;
    this.issues = status.issues;
  }
}

function dockerCommand() {
  if (process.platform === 'darwin') {
    const candidates = macDockerCliPaths();
    for (const candidate of candidates) {
      if (fssync.existsSync(candidate)) return candidate;
    }
    return 'docker';
  }
  if (process.platform === 'win32') {
    const candidates = windowsDockerCliPaths();
    for (const candidate of candidates) {
      if (fssync.existsSync(candidate)) return candidate;
    }
    return 'docker.exe';
  }
  return 'docker';
}

function envPathKey(env = process.env) {
  return Object.keys(env).find((key) => key.toLowerCase() === 'path') || 'PATH';
}

function dockerSpawnEnv(
  command = dockerCommand(),
  env = process.env,
  { existsSync = fssync.existsSync } = {},
) {
  const nextEnv = { ...env };
  if (process.platform !== 'win32' && process.platform !== 'darwin') {
    return nextEnv;
  }

  const pathModule = process.platform === 'win32' ? path.win32 : path.posix;
  if (!pathModule.isAbsolute(command)) {
    return nextEnv;
  }

  // Docker Desktop keeps helper binaries and CLI plugins beside the bundled
  // docker executable. GUI-launched apps often miss those directories in PATH,
  // so add them explicitly when the launcher calls Docker by absolute path.
  const dockerBinDir = pathModule.dirname(command);
  const dockerPluginDir = process.platform === 'darwin'
    ? pathModule.join(pathModule.dirname(dockerBinDir), 'cli-plugins')
    : '';
  const delimiter = process.platform === 'win32' ? ';' : path.delimiter;
  const pathKey = envPathKey(nextEnv);
  const entries = String(nextEnv[pathKey] || '')
    .split(delimiter)
    .filter(Boolean);
  const prependEntries = [
    dockerBinDir,
    dockerPluginDir && existsSync(dockerPluginDir) ? dockerPluginDir : '',
  ]
    .filter(Boolean)
    .filter((entry) => !entries.some((existing) => existing.toLowerCase() === entry.toLowerCase()));
  if (prependEntries.length) {
    nextEnv[pathKey] = [...prependEntries, ...entries].join(delimiter);
  }
  return nextEnv;
}

function dockerRegistryAccessErrorMessage(output, env = {}) {
  const text = String(output || '');
  const unauthorized = /(?:error from registry:\s*)?unauthorized|denied|authentication required|\b403 Forbidden\b/i.test(text);
  if (!unauthorized) return '';
  const repositories = [
    'ghcr.io/phinaldoo/omlorix-backend',
    'ghcr.io/phinaldoo/omlorix-frontend',
  ];
  if (!repositories.some((repository) => text.includes(repository) || repository.startsWith('ghcr.io/'))) {
    return '';
  }
  const version = env.OMLORIX_VERSION || 'stable';
  return [
    `Docker could not pull the official Omlorix images for version ${version} because the registry rejected access.`,
    `Repositories: ${repositories.join(', ')}.`,
    'Make the GHCR packages public or sign in with `docker login ghcr.io` using an account/token that can read the packages.',
  ].join(' ');
}

function dockerSetupUrl(platform = process.platform) {
  if (platform === 'darwin' || platform === 'win32') {
    return 'https://www.docker.com/products/docker-desktop/';
  }
  return 'https://docs.docker.com/engine/install/';
}

function dockerDesktopAppPath(platform = process.platform) {
  if (platform === 'darwin') {
    return '/Applications/Docker.app';
  }
  if (platform === 'win32') {
    const candidates = windowsDockerDesktopAppPaths();
    for (const candidate of candidates) {
      if (fssync.existsSync(candidate)) return candidate;
    }
    return candidates[0] || '';
  }
  return '';
}

function windowsDockerDesktopAppPaths(env = process.env) {
  const paths = [];
  const add = (...segments) => {
    if (segments.some((segment) => !segment)) return;
    const candidate = path.join(...segments);
    if (!paths.includes(candidate)) paths.push(candidate);
  };
  add(env.ProgramFiles || 'C:\\Program Files', 'Docker', 'Docker', 'Docker Desktop.exe');
  add(env['ProgramFiles(x86)'], 'Docker', 'Docker', 'Docker Desktop.exe');
  add(env.LOCALAPPDATA, 'Programs', 'DockerDesktop', 'Docker Desktop.exe');
  add(env.LOCALAPPDATA, 'Programs', 'Docker', 'Docker', 'Docker Desktop.exe');
  return paths;
}

function macDockerCliPaths() {
  return [
    '/Applications/Docker.app/Contents/Resources/bin/docker',
    '/usr/local/bin/docker',
    '/opt/homebrew/bin/docker',
  ];
}

function windowsDockerCliPaths(env = process.env) {
  const paths = [];
  const add = (...segments) => {
    if (segments.some((segment) => !segment)) return;
    const candidate = path.join(...segments);
    if (!paths.includes(candidate)) paths.push(candidate);
  };

  for (const appPath of windowsDockerDesktopAppPaths(env)) {
    add(path.dirname(appPath), 'resources', 'bin', 'docker.exe');
  }
  add(env.ProgramFiles || 'C:\\Program Files', 'Docker', 'Docker', 'resources', 'bin', 'docker.exe');
  add(env.ProgramW6432, 'Docker', 'Docker', 'resources', 'bin', 'docker.exe');
  add(env['ProgramFiles(x86)'], 'Docker', 'Docker', 'resources', 'bin', 'docker.exe');
  add(env.LOCALAPPDATA, 'Docker', 'resources', 'bin', 'docker.exe');
  add(env.LOCALAPPDATA, 'Programs', 'DockerDesktop', 'resources', 'bin', 'docker.exe');
  add(env.LOCALAPPDATA, 'Programs', 'Docker', 'Docker', 'resources', 'bin', 'docker.exe');
  return paths;
}

function envTruthy(value) {
  return ['1', 'true', 'yes', 'on'].includes(String(value || '').trim().toLowerCase());
}

function readEnvToggles(env) {
  // Match the backend's default: an absent or empty REDIS_ENABLED value means
  // enabled. This keeps existing deployments on Redis until they explicitly
  // choose Off in the launcher.
  const redisEnabledValue = String(env.REDIS_ENABLED ?? '').trim();
  const redisEnabled = redisEnabledValue ? envTruthy(redisEnabledValue) : true;
  return {
    useBundledDB: envTruthy(env.OMLORIX_USE_BUNDLED_DB),
    // Redis Off is one canonical mode, regardless of a stale bundled flag
    // written by an older launcher version.
    useBundledRedis: redisEnabled && envTruthy(env.OMLORIX_USE_BUNDLED_REDIS),
    redisEnabled,
    usePgbouncer: envTruthy(env.OMLORIX_USE_PGBOUNCER),
    useBundledStorage: envTruthy(env.OMLORIX_USE_BUNDLED_STORAGE),
    observabilityEnabled: envTruthy(env.OTEL_ENABLED),
    devMode: String(env.MODE || '').trim().toLowerCase() === 'dev',
  };
}

/** Return derived values that must change atomically with topology settings. */
function topologyInvariantUpdates(env = {}) {
  const updates = {};
  const useBundledDB = envTruthy(env.OMLORIX_USE_BUNDLED_DB);
  const usePgbouncer = useBundledDB && envTruthy(env.OMLORIX_USE_PGBOUNCER);
  if (!useBundledDB) {
    updates.OMLORIX_USE_PGBOUNCER = 'false';
  } else {
    // A URL takes precedence over split DATABASE_* fields in the backend. It
    // must be cleared in bundled mode or a stale external URL can bypass both
    // PostgreSQL and PgBouncer routing.
    updates.DATABASE_URL = '';
    updates.DATABASE_HOST_OVERRIDE = usePgbouncer ? 'pgbouncer' : 'postgres';
    updates.DATABASE_PORT_OVERRIDE = '5432';
    updates.DATABASE_MIGRATION_HOST_OVERRIDE = 'postgres';
    updates.DATABASE_MIGRATION_PORT_OVERRIDE = '5432';
  }
  const redisValue = String(env.REDIS_ENABLED ?? '').trim();
  const redisEnabled = redisValue ? envTruthy(redisValue) : true;
  const useBundledRedis = redisEnabled && envTruthy(env.OMLORIX_USE_BUNDLED_REDIS);
  if (!redisEnabled) updates.OMLORIX_USE_BUNDLED_REDIS = 'false';
  if (envTruthy(env.OMLORIX_USE_BUNDLED_STORAGE)) {
    updates.FILE_STORAGE_PROVIDER = 's3';
  }
  const redisPassword = String(env.REDIS_PASSWORD || '');
  if (useBundledRedis && redisPassword) {
    updates.REDIS_URL = defaultLocalRedisUrl(env, redisPassword);
  }
  return updates;
}

function buildComposeProfiles(toggles) {
  const profiles = [];
  const redisEnabled = toggles.redisEnabled !== false;
  if (toggles.useBundledDB) profiles.push('bundled-db');
  if (redisEnabled) profiles.push('redis-enabled');
  if (redisEnabled && toggles.useBundledRedis) profiles.push('bundled-redis');
  if (toggles.usePgbouncer) profiles.push('pgbouncer');
  if (toggles.useBundledStorage) profiles.push('bundled-storage');
  return profiles;
}

/** Derive the packaged long-running service set when Compose cannot render config. */
function linuxHostMetricsSupported(platform = process.platform) {
  return platform === 'linux';
}

function observabilityCapability(toggles, platform = process.platform) {
  const enabled = Boolean(toggles?.observabilityEnabled);
  const available = linuxHostMetricsSupported(platform);
  return {
    enabled,
    hostMetrics: {
      available,
      enabled: enabled && available,
      reason: available ? '' : 'linux_only',
    },
  };
}

const DEDICATED_WORKER_SERVICE_NAMES = Object.freeze([
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
]);

const RESTORE_INFRASTRUCTURE_SERVICE_NAMES = new Set([
  'postgres',
  'redis',
  'pgbouncer',
  'minio',
  'otel-collector',
  'jaeger',
  'prometheus',
  'alertmanager',
  'postgres-exporter',
  'redis-exporter',
  'node-exporter',
  'grafana',
]);
const COMPOSE_ONE_OFF_LABEL = 'com.docker.compose.oneoff';

function composeContainerIsOneOff(row) {
  const labels = row?.Labels;
  let rawValue;
  let found = false;
  if (typeof labels === 'string') {
    const matchingValues = labels.split(',').flatMap((label) => {
      const separator = label.indexOf('=');
      if (separator < 0 || label.slice(0, separator).trim() !== COMPOSE_ONE_OFF_LABEL) {
        return [];
      }
      return [label.slice(separator + 1).trim()];
    });
    if (matchingValues.length === 1) {
      [rawValue] = matchingValues;
      found = true;
    }
  } else if (labels && typeof labels === 'object' && !Array.isArray(labels)) {
    found = Object.prototype.hasOwnProperty.call(labels, COMPOSE_ONE_OFF_LABEL);
    rawValue = labels[COMPOSE_ONE_OFF_LABEL];
  } else {
    throw new Error('Docker Compose returned invalid labels for an active infrastructure container.');
  }
  if (!found) {
    throw new Error('Docker Compose omitted or duplicated the one-off label for an active infrastructure container.');
  }
  if (typeof rawValue === 'boolean') return rawValue;
  if (typeof rawValue === 'string') {
    const normalized = rawValue.trim().toLowerCase();
    if (normalized === 'true') return true;
    if (normalized === 'false') return false;
  }
  throw new Error('Docker Compose returned an invalid one-off label for an active infrastructure container.');
}

function restoreApplicationContainerIds(stdout) {
  const raw = String(stdout || '').trim();
  if (!raw) return [];
  const rows = parseComposeJson(raw);
  if (!rows.length) {
    throw new Error('Docker Compose returned invalid container inventory JSON.');
  }
  const seen = new Set();
  const ids = [];
  for (const row of rows) {
    if (typeof row?.State !== 'string' || !row.State.trim()) {
      throw new Error('Docker Compose returned an invalid container state.');
    }
    const state = row.State.trim().toLowerCase();
    if (!['running', 'restarting', 'paused'].includes(state)) continue;
    if (typeof row?.Service !== 'string') {
      throw new Error('Docker Compose returned an invalid active container service.');
    }
    const service = row.Service.trim().toLowerCase();
    if (
      RESTORE_INFRASTRUCTURE_SERVICE_NAMES.has(service)
      && !composeContainerIsOneOff(row)
    ) continue;
    const id = typeof row?.ID === 'string' ? row.ID.trim() : '';
    if (!/^[a-fA-F0-9]{12,64}$/.test(id)) {
      throw new Error('Docker Compose returned an invalid active container ID.');
    }
    if (!seen.has(id)) {
      seen.add(id);
      ids.push(id);
    }
  }
  return ids.sort();
}

function offlineApplicationServiceNames() {
  return [
    'frontend',
    'email_worker',
    ...DEDICATED_WORKER_SERVICE_NAMES,
    'automation_scheduler',
    'automation_worker',
    'fastapi',
  ];
}

function offlineMigrationDrainArgs(args) {
  // Known-name stop lists cannot catch a writer left behind by a renamed or
  // removed Compose service. A project-wide down keeps volumes but removes all
  // current and orphaned containers before migrations begin.
  return [...args, 'down', '--remove-orphans'];
}

function expectedServiceNamesFromToggles(toggles, platform = process.platform) {
  const names = [];
  const redisEnabled = toggles.redisEnabled !== false;
  if (toggles.useBundledDB) names.push('postgres');
  if (redisEnabled && toggles.useBundledRedis) names.push('redis');
  if (toggles.usePgbouncer) names.push('pgbouncer');
  if (toggles.useBundledStorage) names.push('minio');
  if (redisEnabled) names.push('automation_scheduler', 'automation_worker');
  names.push('email_worker', ...DEDICATED_WORKER_SERVICE_NAMES, 'fastapi', 'frontend');
  if (toggles.observabilityEnabled) {
    names.push(
      'otel-collector',
      'jaeger',
      'prometheus',
      'alertmanager',
      'postgres-exporter',
      ...(redisEnabled ? ['redis-exporter'] : []),
      ...(linuxHostMetricsSupported(platform) ? ['node-exporter'] : []),
      'grafana',
    );
  }
  return names;
}

function composeArgs(serverHome, env, platform = process.platform) {
  const toggles = readEnvToggles(env);
  const profiles = buildComposeProfiles(toggles);
  // The managed-cloud file intentionally contains no bundled infrastructure.
  // Keep the server topology whenever any bundled service is selected, even
  // when both the database and Redis connection themselves are external/off.
  const useManagedCloud = !toggles.useBundledDB
    && (!toggles.redisEnabled || !toggles.useBundledRedis)
    && !toggles.usePgbouncer
    && !toggles.useBundledStorage;
  const baseFile = useManagedCloud ? 'docker-compose.managed-cloud.yml' : 'docker-compose.server.yml';
  const args = ['compose', '--env-file', path.join(serverHome, '.env')];
  args.push('-f', path.join(serverHome, baseFile));
  args.push('-f', path.join(serverHome, 'docker-compose.frontend-port.yml'));
  const launcherServicesFile = path.join(serverHome, 'docker-compose.launcher-services.yml');
  if (fssync.existsSync(launcherServicesFile)) {
    args.push('-f', launcherServicesFile);
  }
  if (toggles.devMode && !useManagedCloud) {
    args.push('-f', path.join(serverHome, 'docker-compose.dev-ports.yml'));
  }
  if (toggles.observabilityEnabled) {
    args.push('-f', path.join(serverHome, 'docker-compose.observability.yml'));
    if (linuxHostMetricsSupported(platform)) {
      args.push('-f', path.join(serverHome, 'docker-compose.observability-linux.yml'));
    }
  }
  for (const profile of profiles) {
    args.push('--profile', profile);
  }
  return args;
}

/**
 * Ask Compose to produce the compact progress display it would use in a real
 * terminal. Electron captures the child process through pipes, so Compose's
 * automatic detection otherwise selects plain mode and prints every transient
 * download state as another permanent line.
 *
 * Only user-visible streamed operations use this wrapper. Status and JSON
 * queries retain their ordinary machine-readable output.
 */
function terminalComposeArgs(args) {
  const values = Array.isArray(args) ? args : [];
  if (values[0] !== 'compose') return values;
  return [
    'compose',
    '--ansi',
    'always',
    '--progress',
    'tty',
    ...values.slice(1),
  ];
}

/**
 * Normalize the Launcher log contract shared by bounded reads and live
 * follows. Docker accepts RFC3339/date timestamps, Unix timestamps, and Go
 * duration strings for `--since`; rejecting everything else here gives the
 * renderer a deterministic validation error before a long-running process is
 * created.
 */
function validLogCalendarTimestamp(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2})(?::(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?)?(Z|[+-]\d{2}:\d{2})?|(Z|[+-]\d{2}:\d{2}))?$/.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year
    || date.getUTCMonth() !== month - 1
    || date.getUTCDate() !== day
  ) return false;
  if (match[4] !== undefined && Number(match[4]) > 23) return false;
  if (match[5] !== undefined && Number(match[5]) > 59) return false;
  if (match[6] !== undefined && Number(match[6]) > 59) return false;
  const timezone = match[7] || match[8] || '';
  const timezoneMatch = /^([+-])(\d{2}):(\d{2})$/.exec(timezone);
  return !timezoneMatch
    || (Number(timezoneMatch[2]) <= 23 && Number(timezoneMatch[3]) <= 59);
}

function validLogRelativeDuration(value) {
  let body = value;
  const negative = body.startsWith('-');
  if (negative || body.startsWith('+')) body = body.slice(1);
  if (body === '0') return true;

  const componentPattern = /(\d+(?:\.\d*)?|\.\d+)(ns|us|µs|μs|ms|s|m|h)/g;
  const units = {
    ns: 1n,
    us: 1000n,
    µs: 1000n,
    μs: 1000n,
    ms: 1000000n,
    s: 1000000000n,
    m: 60000000000n,
    h: 3600000000000n,
  };
  const limit = negative ? 9223372036854775808n : 9223372036854775807n;
  let total = 0n;
  let consumed = 0;
  let match;
  while ((match = componentPattern.exec(body)) !== null) {
    if (match.index !== consumed) return false;
    consumed = componentPattern.lastIndex;
    const [whole = '0', fraction = ''] = match[1].split('.');
    const unit = units[match[2]];
    let nanoseconds = BigInt(whole || '0') * unit;
    if (fraction) {
      nanoseconds += (BigInt(fraction) * unit) / (10n ** BigInt(fraction.length));
    }
    total += nanoseconds;
    if (total > limit) return false;
  }
  return consumed > 0 && consumed === body.length;
}

function validLogUnixTimestamp(value) {
  const match = /^(\d{1,19})(?:\.\d{1,9})?$/.exec(value);
  return Boolean(match) && BigInt(match[1]) <= 9223372036854775807n;
}

function normalizeLogOptions(options = {}) {
  const values = typeof options === 'number' ? { lines: options } : (options || {});
  const requestedLines = values.lines === undefined || values.lines === ''
    ? DEFAULT_LOG_LINES
    : Number(values.lines);
  if (!Number.isInteger(requestedLines) || requestedLines < MIN_LOG_LINES) {
    throw new Error('Log lines must be a positive integer.');
  }

  const since = String(values.since || '').trim();
  if (since) {
    const relativeDuration = validLogRelativeDuration(since);
    const unixTimestamp = validLogUnixTimestamp(since);
    const calendarTimestamp = validLogCalendarTimestamp(since);
    if (since.length > MAX_LOG_TIME_BOUND_LENGTH || (!relativeDuration && !unixTimestamp && !calendarTimestamp)) {
      throw new Error('Use a valid log time bound such as 5m or 2026-08-23T10:30:00Z.');
    }
  }

  return {
    lines: Math.min(MAX_LOG_LINES, requestedLines),
    follow: values.follow === true,
    since,
    service: String(values.service || '').trim(),
  };
}

/** Build the Compose suffix without ever interpolating renderer input. */
function composeLogArgs(options = {}) {
  const normalized = normalizeLogOptions(options);
  const args = ['logs', '--tail', String(normalized.lines), '--no-color'];
  if (normalized.follow) args.push('--follow');
  if (normalized.since) args.push('--since', normalized.since);
  if (normalized.service) args.push(normalized.service);
  return args;
}

/** Return non-trivial configured secrets that must not cross the log IPC. */
function logRedactionValues(env = {}) {
  return [...new Set(Object.entries(env)
    .filter(([key, value]) => isSecretEnvKey(key) && String(value || '').length >= 4)
    .map(([, value]) => String(value)))]
    .sort((left, right) => right.length - left.length);
}

function escapeRegularExpression(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Redact secrets across arbitrary child-process chunk boundaries. Only a
 * suffix that is an actual prefix of a configured secret is retained, so
 * ordinary low-volume log lines still appear immediately even when one secret
 * (for example a service-account JSON document) is unusually long.
 */
function createLogRedactor(secretValues = []) {
  const secrets = [...new Set(secretValues.map((value) => String(value || '')).filter(Boolean))]
    .sort((left, right) => right.length - left.length);
  if (!secrets.length) {
    return {
      push: (text) => String(text || ''),
      flush: () => '',
    };
  }

  const matcher = new RegExp(secrets.map(escapeRegularExpression).join('|'), 'g');
  const maximumSecretLength = Math.max(...secrets.map((value) => value.length));
  let pending = '';

  const consume = (flush = false) => {
    let output = '';
    let cursor = 0;
    matcher.lastIndex = 0;
    let match = matcher.exec(pending);
    while (match) {
      output += `${pending.slice(cursor, match.index)}[REDACTED]`;
      cursor = match.index + match[0].length;
      match = matcher.exec(pending);
    }
    pending = pending.slice(cursor);
    if (flush) {
      output += pending;
      pending = '';
      return output;
    }

    let retainedLength = 0;
    const possibleLength = Math.min(pending.length, maximumSecretLength - 1);
    for (let length = possibleLength; length > 0; length -= 1) {
      const suffix = pending.slice(-length);
      if (secrets.some((secret) => secret.startsWith(suffix))) {
        retainedLength = length;
        break;
      }
    }
    const emitLength = pending.length - retainedLength;
    output += pending.slice(0, emitLength);
    pending = pending.slice(emitLength);
    return output;
  };

  return {
    push(text) {
      pending += String(text || '');
      return consume(false);
    },
    flush() {
      return consume(true);
    },
  };
}

function redactLogText(text, env = {}) {
  const redactor = createLogRedactor(logRedactionValues(env));
  return `${redactor.push(text)}${redactor.flush()}`;
}

/** Derive the same privacy-preserving per-home Compose name as the Go CLI. */
function composeProjectNameForHome(serverHome) {
  let normalized = path.resolve(serverHome).split(path.sep).join('/');
  if (process.platform === 'win32') normalized = normalized.toLowerCase();
  return `omlorix-${crypto.createHash('sha256').update(normalized).digest('hex').slice(0, 12)}`;
}

/** Return a normalized concrete server version, excluding moving/custom tags. */
function trackableServerVersion(value) {
  const normalized = normalizeVersion(value);
  return /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(normalized) ? normalized : '';
}

/** Extract a concrete Omlorix release tag from a running container image name. */
function serverVersionFromImage(image) {
  const imageName = String(image || '').trim().split('@', 1)[0];
  const tagSeparator = imageName.lastIndexOf(':');
  if (tagSeparator <= imageName.lastIndexOf('/')) return '';
  return trackableServerVersion(imageName.slice(tagSeparator + 1));
}

/** Keep launcher version history monotonic across starts, upgrades, and imports. */
function highestServerVersion(currentHighest, successfulVersion) {
  const current = trackableServerVersion(currentHighest);
  const candidate = trackableServerVersion(successfulVersion);
  if (!candidate) return current;
  if (!current || compareVersions(candidate, current) > 0) return candidate;
  return current;
}

/** Normalize launcher-owned metadata stored beside this server installation. */
function defaultLauncherMetadata(overrides = {}) {
  const verification = overrides.visitorIpVerification
    && typeof overrides.visitorIpVerification === 'object'
    ? overrides.visitorIpVerification
    : {};
  return {
    version: LAUNCHER_METADATA_VERSION,
    highestSuccessfulServerVersion: trackableServerVersion(
      overrides.highestSuccessfulServerVersion,
    ),
    visitorIpVerification: {
      verified: verification.verified === true,
      verifiedAt: String(verification.verifiedAt || ''),
      topologyFingerprint: String(verification.topologyFingerprint || ''),
      clientIp: String(verification.clientIp || ''),
      scheme: String(verification.scheme || ''),
      host: String(verification.host || ''),
      errorCode: String(verification.errorCode || ''),
    },
  };
}

/** Normalize permission-restricted management state shared by Launcher and CLI. */
function defaultServerSettings(overrides = {}) {
  const proxy = overrides.proxy && typeof overrides.proxy === 'object'
    ? overrides.proxy
    : {};
  return {
    schemaVersion: SERVER_SETTINGS_VERSION,
    updateChannel: normalizeUpdateChannel(overrides.updateChannel),
    proxy: {
      enabled: proxy.enabled === true,
      autostart: proxy.autostart !== false,
      bindHost: String(proxy.bindHost || '0.0.0.0').trim() || '0.0.0.0',
      publicHostname: String(proxy.publicHostname || '').trim(),
      httpPort: String(proxy.httpPort || '8081').trim() || '8081',
      httpsEnabled: proxy.httpsEnabled === true,
      httpsPort: String(proxy.httpsPort || '8443').trim() || '8443',
      redirectHttpToHttps: proxy.redirectHttpToHttps === true,
      tlsCertPath: String(proxy.tlsCertPath || '').trim(),
      tlsKeyPath: String(proxy.tlsKeyPath || '').trim(),
      tlsCaPath: String(proxy.tlsCaPath || '').trim(),
      tlsKeyPassphrase: String(proxy.tlsKeyPassphrase || ''),
    },
  };
}

/** Convert the host proxy settings to the existing internal env-shaped API. */
function proxySettingsEnv(settings = defaultServerSettings()) {
  const proxy = defaultServerSettings(settings).proxy;
  return {
    OMLORIX_LAUNCHER_PROXY_ENABLED: String(proxy.enabled),
    OMLORIX_LAUNCHER_PROXY_AUTOSTART: String(proxy.autostart),
    OMLORIX_LAUNCHER_PROXY_BIND: proxy.bindHost,
    OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME: proxy.publicHostname,
    OMLORIX_LAUNCHER_PROXY_HTTP_PORT: proxy.httpPort,
    OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED: String(proxy.httpsEnabled),
    OMLORIX_LAUNCHER_PROXY_HTTPS_PORT: proxy.httpsPort,
    OMLORIX_LAUNCHER_PROXY_REDIRECT_HTTP_TO_HTTPS: String(proxy.redirectHttpToHttps),
    OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH: proxy.tlsCertPath,
    OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH: proxy.tlsKeyPath,
    OMLORIX_LAUNCHER_PROXY_TLS_CA_PATH: proxy.tlsCaPath,
    OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE: proxy.tlsKeyPassphrase,
  };
}

/** Normalize legacy dotenv proxy values into typed management settings. */
function proxySettingsFromEnv(env = {}) {
  const autostart = String(env.OMLORIX_LAUNCHER_PROXY_AUTOSTART ?? '').trim();
  return defaultServerSettings({
    proxy: {
      enabled: envTruthy(env.OMLORIX_LAUNCHER_PROXY_ENABLED),
      autostart: autostart ? envTruthy(autostart) : true,
      bindHost: env.OMLORIX_LAUNCHER_PROXY_BIND,
      publicHostname: env.OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME,
      httpPort: env.OMLORIX_LAUNCHER_PROXY_HTTP_PORT,
      httpsEnabled: envTruthy(env.OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED),
      httpsPort: env.OMLORIX_LAUNCHER_PROXY_HTTPS_PORT,
      redirectHttpToHttps: envTruthy(env.OMLORIX_LAUNCHER_PROXY_REDIRECT_HTTP_TO_HTTPS),
      tlsCertPath: env.OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH,
      tlsKeyPath: env.OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH,
      tlsCaPath: env.OMLORIX_LAUNCHER_PROXY_TLS_CA_PATH,
      tlsKeyPassphrase: env.OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE,
    },
  }).proxy;
}

/** Read and strictly validate the optional recovery-only update channel. */
function recoveryUpdateChannelFromEnv(env = {}) {
  const present = Object.prototype.hasOwnProperty.call(env, 'OMLORIX_UPDATE_CHANNEL');
  if (!present) return { present: false, updateChannel: '' };
  const updateChannel = String(env.OMLORIX_UPDATE_CHANNEL || '').trim().toLowerCase();
  if (!UPDATE_CHANNELS.includes(updateChannel)) {
    throw new Error('The recovery file update channel must be stable or beta.');
  }
  return { present: true, updateChannel };
}

/** Build a renderer event that preserves translated diagnostic metadata. */
function operationFailurePayload(name, error, code = -1) {
  return {
    name,
    ok: false,
    code,
    message: error?.message || String(error || `${name} failed.`),
    ...(error?.messageKey ? {
      messageKey: error.messageKey,
      messageValues: error.messageValues || {},
    } : {}),
  };
}

function backupDownloadError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

/** Commit a producer's bytes without exposing a partial or overwriting a path. */
async function writeAtomicBackupDownload(target, producer) {
  const absoluteTarget = path.resolve(String(target || ''));
  const parent = path.dirname(absoluteTarget);
  let parentStat;
  try {
    parentStat = await fs.stat(parent);
  } catch {
    throw backupDownloadError(
      'BACKUP_DESTINATION_UNAVAILABLE',
      'The selected backup destination directory is unavailable.',
    );
  }
  if (!parentStat.isDirectory()) {
    throw backupDownloadError(
      'BACKUP_DESTINATION_UNAVAILABLE',
      'The selected backup destination directory is unavailable.',
    );
  }
  try {
    await fs.lstat(absoluteTarget);
    throw backupDownloadError(
      'BACKUP_DESTINATION_EXISTS',
      'A file already exists at the selected backup destination.',
    );
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }

  const temporaryPath = path.join(
    parent,
    `.${path.basename(absoluteTarget)}.omlorix-download-${crypto.randomBytes(12).toString('hex')}.partial`,
  );
  let temporary = null;
  try {
    temporary = await fs.open(temporaryPath, 'wx', 0o600);
    await producer(temporary);
    await temporary.sync();
    await temporary.close();
    temporary = null;
    try {
      // A same-directory hard link is an atomic no-replace commit on every
      // supported host filesystem. The private temporary name is unlinked in
      // finally, leaving the completed archive at the chosen path.
      await fs.link(temporaryPath, absoluteTarget);
    } catch (error) {
      if (error?.code === 'EEXIST') {
        throw backupDownloadError(
          'BACKUP_DESTINATION_EXISTS',
          'A file already exists at the selected backup destination.',
        );
      }
      throw backupDownloadError(
        'BACKUP_DESTINATION_UNAVAILABLE',
        'The downloaded backup could not be committed safely.',
      );
    }
    let completed;
    try {
      completed = await fs.stat(absoluteTarget);
    } catch {
      await fs.unlink(absoluteTarget).catch(() => {});
      throw backupDownloadError(
        'BACKUP_DESTINATION_UNAVAILABLE',
        'The downloaded backup could not be verified on disk.',
      );
    }
    return { path: absoluteTarget, bytes: completed.size };
  } catch (error) {
    if (String(error?.code || '').startsWith('BACKUP_')) throw error;
    throw backupDownloadError(
      'BACKUP_DESTINATION_UNAVAILABLE',
      'The selected backup destination could not be written.',
    );
  } finally {
    if (temporary) await temporary.close().catch(() => {});
    await fs.unlink(temporaryPath).catch(() => {});
  }
}

/** Stream one Docker command's stdout to an already private file handle. */
async function streamDockerOutputToFile(args, cwd, fileHandle) {
  const dockerExecutable = dockerCommand();
  const child = spawn(dockerExecutable, args, {
    cwd,
    windowsHide: true,
    env: dockerSpawnEnv(dockerExecutable),
  });
  let stderr = '';
  child.stderr.on('data', (chunk) => {
    if (stderr.length < 64 * 1024) stderr += chunk.toString().slice(0, 64 * 1024 - stderr.length);
  });
  const completion = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code) => resolve(Number(code ?? -1)));
  });
  try {
    for await (const chunk of child.stdout) {
      let offset = 0;
      while (offset < chunk.length) {
        const { bytesWritten } = await fileHandle.write(
          chunk,
          offset,
          chunk.length - offset,
          null,
        );
        if (bytesWritten <= 0) {
          throw new Error('The backup download stopped before the archive was fully written.');
        }
        offset += bytesWritten;
      }
    }
    const code = await completion;
    if (code !== 0) {
      const expectedArtifactFailure = /backup job (?:not found|is not complete)|has no artifacts|catalog checksum|checksum and size/i.test(stderr);
      throw backupDownloadError(
        expectedArtifactFailure ? 'BACKUP_NOT_AVAILABLE' : 'BACKUP_DOWNLOAD_FAILED',
        expectedArtifactFailure
          ? 'The selected backup is not available to download.'
          : 'The backup archive could not be downloaded.',
      );
    }
  } catch (error) {
    child.kill();
    await completion.catch(() => {});
    if (String(error?.code || '').startsWith('BACKUP_')) throw error;
    throw backupDownloadError('BACKUP_DOWNLOAD_FAILED', 'The backup archive could not be downloaded.');
  }
}

/** Parse the last complete JSON object emitted by a one-shot CLI command. */
function parseTrailingJsonObject(output) {
  const text = String(output || '').trim();
  let objectStart = text.lastIndexOf('{');
  while (objectStart >= 0) {
    try {
      const parsed = JSON.parse(text.slice(objectStart));
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
    } catch {
      // Nested objects also begin with `{`; keep walking backward until the
      // top-level CLI payload is found.
    }
    objectStart = text.lastIndexOf('{', objectStart - 1);
  }
  return null;
}

// Stable backend reason codes are preserved for renderer-side localization.
// These English messages remain useful for logs, thrown errors, and older
// renderers that do not yet understand the structured code.
const RESTORE_FAILURE_REASON_MESSAGES = Object.freeze({
  target_not_empty: 'The restore target is not empty.',
  missing_required_files: 'The backup archive is incomplete.',
  checksum_mismatch: 'The backup archive failed checksum verification.',
  encryption_key_mismatch: 'The backup archive cannot be decrypted with this server\'s encryption key.',
  manifest_parse_failed: 'The backup manifest is invalid.',
  payload_tar_parse_failed: 'A backup payload is invalid.',
  archive_extracted_size_exceeded: 'The backup exceeds the configured restore size limit.',
  insufficient_disk_space: 'There is not enough free disk space to restore this backup safely.',
  source_access_failed: 'The backup source could not be accessed.',
});

/** Return a stable code and concise fallback for a backend restore rejection. */
function restoreFailureReason(payload, fallback = '') {
  const preflight = payload?.preflight && typeof payload.preflight === 'object'
    ? payload.preflight
    : {};
  const reason = String(preflight.reason || '');
  const knownReason = Object.prototype.hasOwnProperty.call(
    RESTORE_FAILURE_REASON_MESSAGES,
    reason,
  );
  return {
    code: knownReason ? reason : '',
    message: RESTORE_FAILURE_REASON_MESSAGES[reason]
      || String(payload?.error || '').trim()
      || (reason ? reason.replaceAll('_', ' ') : '')
      || String(fallback || '').trim()
      || 'The backend restore command failed. Review the restore logs for details.',
  };
}

function normalizeStorageProvider(value, fieldName = 'storage provider') {
  let normalized = String(value || '').trim().toLowerCase();
  if (normalized === 's3-compatible') normalized = 's3';
  if (!STORAGE_PROVIDERS.has(normalized)) {
    throw new Error(`${fieldName} must be local, s3, gcs, azure, or webdav.`);
  }
  return normalized;
}

function normalizeStorageMigrationOptions(payload = {}) {
  const fromProvider = normalizeStorageProvider(payload.fromProvider, 'Source provider');
  const toProvider = normalizeStorageProvider(payload.toProvider, 'Destination provider');
  if (fromProvider === toProvider) {
    throw new Error('Source and destination storage providers must be different.');
  }
  const scope = String(payload.scope || 'all').trim().toLowerCase();
  if (!STORAGE_MIGRATION_SCOPES.has(scope)) {
    throw new Error('Migration scope must be all, files, deep-research, or presentations.');
  }
  const integer = (value, fallback, minimum, label) => {
    const normalized = value === '' || value === null || value === undefined
      ? fallback
      : Number(value);
    if (!Number.isSafeInteger(normalized) || normalized < minimum) {
      throw new Error(`${label} must be an integer of at least ${minimum}.`);
    }
    return normalized;
  };
  const text = (value, label) => {
    const normalized = String(value || '').trim();
    if (normalized.length > 255 || /[\r\n\0]/.test(normalized)) {
      throw new Error(`${label} must be 255 characters or fewer and contain no control characters.`);
    }
    return normalized;
  };
  const date = (value, label) => {
    const normalized = text(value, label);
    if (!normalized) return '';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(normalized)) {
      throw new Error(`${label} must use YYYY-MM-DD.`);
    }
    const parsed = new Date(`${normalized}T00:00:00Z`);
    if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== normalized) {
      throw new Error(`${label} must be a valid date using YYYY-MM-DD.`);
    }
    return normalized;
  };
  const createdAfter = date(payload.createdAfter, 'Created after');
  const createdBefore = date(payload.createdBefore, 'Created before');
  if (createdAfter && createdBefore && createdAfter > createdBefore) {
    throw new Error('Created after must not be later than created before.');
  }
  return {
    fromProvider,
    toProvider,
    scope,
    userId: text(payload.userId, 'User ID'),
    onlyMigratedFrom: payload.onlyMigratedFrom
      ? normalizeStorageProvider(payload.onlyMigratedFrom, 'Migration origin')
      : '',
    createdAfter,
    createdBefore,
    batchSize: integer(payload.batchSize, 200, 1, 'Batch size'),
    maxFiles: integer(payload.maxFiles, 0, 0, 'Maximum records'),
    retries: integer(payload.retries, 3, 1, 'Retries'),
    dryRun: payload.dryRun !== false,
    deleteSource: payload.deleteSource === true,
    force: payload.force === true,
  };
}

/** Read the backend's sanitized failure and explicit recovery decision. */
function restoreFailureFromError(error) {
  const result = error?.dockerResult;
  const payload = parseTrailingJsonObject(result?.stdout)
    || parseTrailingJsonObject(result?.stderr);
  const recovery = payload?.recovery || payload?.preflight?.recovery;
  const failureReason = restoreFailureReason(payload, error?.message || String(error || ''));
  return {
    reason: failureReason.message,
    reasonCode: failureReason.code,
    recovery: recovery && typeof recovery === 'object'
      ? {
          state: String(recovery.state || 'unknown'),
          safeToRestart: recovery.safe_to_restart === true,
        }
      : null,
  };
}

function randomSecret(bytes = 48) {
  return crypto.randomBytes(bytes).toString('base64');
}

function randomJwtSecret() {
  return randomSecret(64);
}

function jwtSecretByteLength(value) {
  return Buffer.byteLength(String(value ?? '').trim(), 'utf8');
}

function randomUrlSecret(bytes = 48) {
  return crypto.randomBytes(bytes).toString('base64url');
}

function randomFernetKey() {
  return crypto.randomBytes(32).toString('base64').replace(/\+/g, '-').replace(/\//g, '_');
}

function isValidFernetKey(value) {
  const normalized = String(value || '').trim();
  if (!/^[A-Za-z0-9_-]{43}=$/.test(normalized)) return false;
  try {
    return Buffer.from(normalized.replace(/-/g, '+').replace(/_/g, '/'), 'base64').length === 32;
  } catch (error) {
    return false;
  }
}

/**
 * Produce a stable, non-reversible identifier for the effective .env values.
 * Sorting the keys makes the freshness check independent of harmless line
 * ordering while still detecting every launcher-managed setting change.
 */
function envBackupFingerprint(env = {}) {
  const material = Object.keys(env)
    .sort()
    .map((key) => `${key}\0${String(env[key] || '')}`)
    .join('\0');
  return crypto.createHash('sha256').update(material).digest('hex');
}

/**
 * Return the recovery values that must be present for the deployment described
 * by a secrets bundle. Optional integration credentials remain optional, while
 * the active database, cache, and storage credentials must be self-contained.
 */
function requiredSecretBackupKeys(env = {}) {
  const toggles = readEnvToggles(env);
  const required = new Set([
    'JWT_SECRET_KEY',
    'ENCRYPTION_KEY',
    'PASSWORD_RESET_IDENTIFIER_HASH_SALT',
    'LOG_IP_HASH_SALT',
    'BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE',
    toggles.useBundledDB ? 'DATABASE_PASSWORD' : 'DATABASE_URL',
  ]);

  // Disabled Redis deployments retain saved connection values, but those
  // credentials are not active recovery requirements until Redis is enabled.
  if (toggles.redisEnabled) {
    required.add(toggles.useBundledRedis ? 'REDIS_PASSWORD' : 'REDIS_URL');
  }

  if (toggles.useBundledStorage) {
    required.add('MINIO_ROOT_USER');
    required.add('MINIO_ROOT_PASSWORD');
  } else {
    const provider = String(env.FILE_STORAGE_PROVIDER || 'local').trim().toLowerCase();
    const providerKeys = {
      s3: ['FILE_STORAGE_S3_ACCESS_KEY_ID', 'FILE_STORAGE_S3_SECRET_ACCESS_KEY'],
      gcs: ['FILE_STORAGE_GCS_CREDENTIALS_JSON'],
      webdav: ['FILE_STORAGE_WEBDAV_USERNAME', 'FILE_STORAGE_WEBDAV_PASSWORD'],
    };
    for (const key of providerKeys[provider] || []) required.add(key);

    // Azure accepts either a connection string or an account URL plus a
    // credential. Treat either complete authentication mode as recoverable.
    if (provider === 'azure' && !envValueIsFilled(env.FILE_STORAGE_AZURE_CONNECTION_STRING)) {
      required.add('FILE_STORAGE_AZURE_CREDENTIAL');
    }
  }

  return Array.from(required);
}

function defaultSetupState(overrides = {}) {
  return {
    version: SETUP_STATE_VERSION,
    complete: Boolean(overrides.complete),
    currentStep: Number.isInteger(overrides.currentStep) ? overrides.currentStep : 0,
    backupFingerprint: String(overrides.backupFingerprint || ''),
    backupSavedAt: String(overrides.backupSavedAt || ''),
    backupFileName: String(overrides.backupFileName || ''),
    // The path is chosen explicitly during onboarding and remains launcher
    // metadata rather than an environment variable consumed by containers.
    backupFilePath: String(overrides.backupFilePath || ''),
    completedAt: String(overrides.completedAt || ''),
  };
}

function defaultGrafanaAdminUser() {
  return 'omlorix-admin';
}

function encodeUrlComponent(value) {
  // encodeURIComponent deliberately leaves a few RFC 3986 reserved bytes
  // untouched. Escape those remaining delimiters so arbitrary operator-entered
  // passwords cannot be reinterpreted as Redis URI syntax.
  return encodeURIComponent(String(value || '')).replace(/[!'()*]/g, (character) => (
    `%${character.charCodeAt(0).toString(16).toUpperCase()}`
  ));
}

function defaultLocalRedisUrl(env = {}, password = '') {
  // The Electron launcher starts Omlorix through the server Compose stack. From
  // inside those backend containers, bundled Redis is reachable by the Compose
  // service name, not by localhost. Host-facing localhost URLs are only useful
  // for source-checkout development commands.
  return `redis://:${encodeUrlComponent(password)}@redis:6379/0`;
}

function shouldResetGrafanaAdminUser(value) {
  const normalized = String(value || '').trim();
  return !normalized || normalized === 'CHANGE_ME' || normalized === 'admin';
}

function findInlineCommentIndex(valuePart) {
  let quote = null;
  let escaped = false;
  for (let i = 0; i < valuePart.length; i += 1) {
    const char = valuePart[i];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (char === '\\') {
      escaped = true;
      continue;
    }
    if (quote) {
      if (char === quote) quote = null;
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      continue;
    }
    if (char === '#' && (i === 0 || /\s/.test(valuePart[i - 1]))) {
      return i;
    }
  }
  return -1;
}

function parseAssignmentLine(line) {
  const match = String(line || '').match(/^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$/);
  if (!match) return null;
  const valuePart = match[4] || '';
  const commentIndex = findInlineCommentIndex(valuePart);
  return {
    prefix: match[1],
    key: match[2],
    separator: match[3],
    valuePart,
    inlineComment: commentIndex >= 0 ? valuePart.slice(commentIndex) : '',
  };
}

function hasOddBackslashRunBefore(value, index) {
  let count = 0;
  for (let cursor = index - 1; cursor >= 0 && value[cursor] === '\\'; cursor -= 1) {
    count += 1;
  }
  return count % 2 === 1;
}

function unquoteEnvValue(valuePart) {
  let value = String(valuePart || '').trim();
  const commentIndex = findInlineCommentIndex(value);
  if (commentIndex >= 0) {
    value = value.slice(0, commentIndex).trim();
  }
  // Values written by serializeValue are complete JSON strings.
  // Decode those before applying the permissive legacy parser so quotes,
  // backslashes, and escaped newlines round-trip byte-for-byte.
  if (value.startsWith('"') && value.endsWith('"')) {
    try {
      const decoded = JSON.parse(value);
      if (typeof decoded === 'string') return decoded;
    } catch (error) {
      // Fall through for hand-written .env values that use shell-like quoting
      // but are not valid JSON strings.
    }
  }
  if (value.startsWith('"') || value.startsWith("'")) {
    const quote = value[0];
    let closingIndex = -1;
    for (let i = 1; i < value.length; i += 1) {
      if (value[i] === quote && !hasOddBackslashRunBefore(value, i)) {
        closingIndex = i;
        break;
      }
    }
    value = closingIndex >= 0 ? value.slice(1, closingIndex) : value.slice(1);
    if (quote === '"') {
      try {
        value = JSON.parse(`"${value.replace(/"/g, '\\"')}"`);
      } catch (error) {
        value = value.replace(/\\"/g, '"').replace(/\\\\/g, '\\');
      }
    }
  }
  return value;
}

function parseEnv(content) {
  const values = {};
  for (const line of String(content || '').split(/\r?\n/)) {
    const parsed = parseAssignmentLine(line);
    if (!parsed) continue;
    values[parsed.key] = unquoteEnvValue(parsed.valuePart);
  }
  return values;
}

function parseEnvDetailed(content) {
  const values = {};
  const keys = [];
  const duplicateKeys = [];
  const invalidLines = [];
  const seen = new Set();
  const lines = String(content || '').split(/\r?\n/);

  lines.forEach((line, index) => {
    const parsed = parseAssignmentLine(line);
    const trimmed = String(line || '').trim();
    if (!parsed) {
      if (trimmed && !trimmed.startsWith('#')) {
        invalidLines.push({ line: index + 1, text: trimmed.slice(0, 160) });
      }
      return;
    }

    if (seen.has(parsed.key) && !duplicateKeys.includes(parsed.key)) {
      duplicateKeys.push(parsed.key);
    }
    seen.add(parsed.key);
    if (!keys.includes(parsed.key)) {
      keys.push(parsed.key);
    }
    values[parsed.key] = unquoteEnvValue(parsed.valuePart);
  });

  return { values, keys, duplicateKeys, invalidLines };
}

function serializeValue(value) {
  const str = String(value ?? '');
  if (!str) return '""';
  if (/[\s#"']/.test(str)) {
    return JSON.stringify(str);
  }
  return str;
}

function updateEnvContent(content, updates) {
  const seen = new Set();
  const lines = String(content || '').split(/\r?\n/).map((line) => {
    const parsed = parseAssignmentLine(line);
    if (!parsed) return line;
    const key = parsed.key;
    if (!Object.prototype.hasOwnProperty.call(updates, key)) return line;
    seen.add(key);
    const inlineComment = parsed.inlineComment ? ` ${parsed.inlineComment.trim()}` : '';
    return `${parsed.prefix}${key}${parsed.separator}${serializeValue(updates[key])}${inlineComment}`;
  });

  const append = [];
  for (const [key, value] of Object.entries(updates)) {
    if (!seen.has(key)) {
      append.push(`${key}=${serializeValue(value)}`);
    }
  }
  if (append.length) {
    if (lines.length && lines[lines.length - 1] !== '') {
      lines.push('');
    }
    lines.push(...append);
  }
  return lines.join('\n').replace(/\n{3,}/g, '\n\n');
}

function removeEnvKeysFromContent(content, removeKeys) {
  const keys = new Set(Array.isArray(removeKeys) ? removeKeys.map((key) => String(key || '').trim()).filter(Boolean) : []);
  if (!keys.size) return String(content || '');
  return String(content || '').split(/\r?\n/).filter((line) => {
    const parsed = parseAssignmentLine(line);
    return !parsed || !keys.has(parsed.key);
  }).join('\n').replace(/\n{3,}/g, '\n\n');
}

function humanizeEnvKey(key) {
  return String(key || '')
    .toLowerCase()
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function inferEnvType(key, value) {
  if (ENV_ENUM_OPTIONS[key]) return 'enum';
  const normalized = String(value ?? '').trim().toLowerCase();
  if (['true', 'false'].includes(normalized)) return 'boolean';
  if (/_PORT$|_HOST_PORT$|PORT$/.test(key)) return 'port';
  if (/(_SECONDS|_RPM|_SIZE|_AGE|_CONN|_TIMEOUT)$/.test(key)) return 'integer';
  if (/_URL$|_ENDPOINT_URL$|ROOT_URL$/.test(key)) return 'url';
  return 'string';
}

function isSecretEnvKey(key) {
  const normalized = String(key || '').toUpperCase();
  return ENV_SECRET_KEYS.has(normalized) || ENV_SECRET_KEY_PATTERN.test(normalized);
}

function proxyTrustConfigured(env) {
  return envTruthy(env.TRUST_PROXY_HEADERS || env.OMLORIX_TRUST_PROXY_HEADERS)
    && Boolean(String(env.TRUSTED_PROXIES || env.OMLORIX_TRUSTED_PROXIES || '').trim());
}

/**
 * Return the value Compose will load into the backend container for a saved
 * proxy environment key.
 *
 * Most env-file values pass through unchanged. TRUSTED_PROXIES is different:
 * managed Compose files fail closed when it is empty; visitor-IP convergence
 * persists the exact dynamic frontend address after Docker assigns it.
 */
function desiredBackendProxyEnvValue(env, key) {
  if (key === 'TRUSTED_PROXIES') {
    return String(env.TRUSTED_PROXIES || COMPOSE_DEFAULT_TRUSTED_PROXIES);
  }
  return String(env[key] ?? '');
}

/** Parse Docker's JSON array of KEY=VALUE entries without exposing other env values. */
function selectBackendProxyEnvironment(raw) {
  let entries;
  try {
    entries = JSON.parse(String(raw || '').trim());
  } catch (error) {
    return null;
  }
  if (!Array.isArray(entries)) return null;

  const selected = {};
  for (const entry of entries) {
    const text = String(entry || '');
    const separator = text.indexOf('=');
    if (separator < 1) continue;
    const key = text.slice(0, separator);
    if (PROXY_BACKEND_ENV_INSPECT_KEYS.has(key)) {
      selected[key] = text.slice(separator + 1);
    }
  }
  return selected;
}

function dockerVmNetworkingLikely(infoText) {
  const text = String(infoText || '');
  if (process.platform === 'win32' || process.platform === 'darwin') return true;
  return /Docker Desktop|desktop-linux|linuxkit|WSL2?|moby/i.test(text);
}

function isLikelyIpAddress(value) {
  return net.isIP(String(value || '').trim()) !== 0;
}

function buildTrustedProxyEnvValue(...proxyIps) {
  const entries = [...TRUSTED_PROXY_LOOPBACK_CIDRS];
  const seen = new Set(entries);
  for (const proxyIp of proxyIps) {
    const normalizedIp = String(proxyIp || '').trim();
    if (isLikelyIpAddress(normalizedIp) && !seen.has(normalizedIp)) {
      entries.push(normalizedIp);
      seen.add(normalizedIp);
    }
  }
  return entries.join(',');
}

function buildUvicornForwardedAllowIps(...proxyIps) {
  const entries = [UVICORN_TRUSTED_PROXY_ENV_VALUE];
  const seen = new Set(entries);
  for (const proxyIp of proxyIps) {
    const normalizedIp = String(proxyIp || '').trim();
    if (isLikelyIpAddress(normalizedIp) && !seen.has(normalizedIp)) {
      entries.push(normalizedIp);
      seen.add(normalizedIp);
    }
  }
  return entries.join(',');
}

function normalizeEnvMetadata(raw) {
  const metadata = raw && typeof raw === 'object' ? raw : {};
  const translations = metadata.translations && typeof metadata.translations === 'object'
    ? metadata.translations
    : {};
  return {
    fields: metadata.fields && typeof metadata.fields === 'object' ? metadata.fields : {},
    sectionKeys: translations.sections && typeof translations.sections === 'object'
      ? translations.sections
      : {},
    descriptionKeys: translations.descriptions && typeof translations.descriptions === 'object'
      ? translations.descriptions
      : {},
  };
}

function parseEnvExampleMetadata(content, envMetadata = {}) {
  const lines = String(content || '').split(/\r?\n/);
  const fields = [];
  const byKey = new Map();
  const configuredFields = envMetadata.fields && typeof envMetadata.fields === 'object'
    ? envMetadata.fields
    : {};
  const sectionKeys = envMetadata.sectionKeys && typeof envMetadata.sectionKeys === 'object'
    ? envMetadata.sectionKeys
    : {};
  const descriptionKeys = envMetadata.descriptionKeys && typeof envMetadata.descriptionKeys === 'object'
    ? envMetadata.descriptionKeys
    : {};

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const parsed = parseAssignmentLine(line);
    if (parsed) {
      const key = parsed.key;
      const defaultValue = unquoteEnvValue(parsed.valuePart);
      const configured = configuredFields[key] && typeof configuredFields[key] === 'object'
        ? configuredFields[key]
        : {};
      const section = configured.section || 'General';
      const metadata = {
        key,
        section,
        sectionKey: sectionKeys[section] || 'launcher_ui_env_section_general',
        defaultValue,
        description: configured.description || '',
        descriptionKey: descriptionKeys[key] || '',
        label: configured.label || humanizeEnvKey(key),
        type: configured.type || inferEnvType(key, defaultValue),
        secret: typeof configured.secret === 'boolean' ? configured.secret : isSecretEnvKey(key),
        required: ENV_REQUIRED_KEYS.has(key),
        options: Array.isArray(configured.options) ? configured.options : (ENV_ENUM_OPTIONS[key] || []),
      };
      fields.push(metadata);
      byKey.set(key, metadata);
    }
  }

  return { fields, byKey };
}

function allowedUrlProtocolsForEnvKey(key) {
  const normalized = String(key || '').toUpperCase();
  if (normalized === 'DATABASE_URL' || normalized === 'AUDIT_DATABASE_URL') {
    return ['postgres:', 'postgresql:', 'postgresql+psycopg:', 'postgresql+psycopg2:', 'sqlite:'];
  }
  if (normalized === 'REDIS_URL') {
    return ['redis:', 'rediss:'];
  }
  return ['http:', 'https:'];
}

function validateEnvValue(key, value, metadata) {
  const str = String(value ?? '');
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
    return 'Use only letters, numbers, and underscores. The first character must be a letter or underscore.';
  }
  // The backup passphrase has historically supported quoted multi-line values
  // and the serializer escapes them safely. Keep that format importable while
  // rejecting line breaks for ordinary variables and NUL for every value.
  if (/\u0000/.test(str) || (key !== 'BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE' && /[\r\n]/.test(str))) {
    return 'Values must be a single line.';
  }
  if (metadata?.required && !str.trim()) {
    return 'This value is required.';
  }
  const type = metadata?.type || inferEnvType(key, str);
  if (type === 'boolean' && str && !['true', 'false'].includes(str.toLowerCase())) {
    return 'Use true or false.';
  }
  if (type === 'port' && str) {
    if (!/^\d+$/.test(str)) return 'Use a numeric port.';
    const port = Number(str);
    if (!Number.isInteger(port) || port < 1 || port > 65535) return 'Use a port from 1 to 65535.';
  }
  if (type === 'integer' && str && !/^-?\d+$/.test(str)) {
    return 'Use a whole number.';
  }
  if (type === 'enum' && str && metadata?.options?.length && !metadata.options.includes(str)) {
    return `Choose one of: ${metadata.options.join(', ')}.`;
  }
  if (type === 'url' && str) {
    try {
      const parsed = new URL(str);
      if (!allowedUrlProtocolsForEnvKey(key).includes(parsed.protocol)) {
        return 'Use a supported URL scheme.';
      }
    } catch (error) {
      return 'Use a valid URL.';
    }
  }
  return '';
}

function envValueIsFilled(value) {
  const normalized = String(value ?? '').trim();
  return Boolean(normalized) && normalized !== 'CHANGE_ME' && !normalized.includes('CHANGE_ME');
}

const LOG_IP_SALT_REUSES_JWT_VALIDATION_KEY = 'launcher_ui_log_ip_hash_salt_must_differ_from_jwt_secret_key';

/**
 * Return the cross-field secret validation errors for a final environment.
 *
 * The backend trims both values before use, so the launcher must compare the
 * same normalized representation. Empty values remain the responsibility of
 * the existing required-field checks and must not be mistaken for key reuse.
 */
function independentSecuritySecretErrors(env = {}) {
  const jwtSecret = String(env.JWT_SECRET_KEY ?? '').trim();
  const logIpHashSalt = String(env.LOG_IP_HASH_SALT ?? '').trim();
  if (jwtSecret && logIpHashSalt && jwtSecret === logIpHashSalt) {
    return { LOG_IP_HASH_SALT: LOG_IP_SALT_REUSES_JWT_VALIDATION_KEY };
  }
  return {};
}

/** Reject a final environment that reuses JWT signing material as a salt. */
function assertIndependentSecuritySecrets(env = {}) {
  const validationErrors = independentSecuritySecretErrors(env);
  if (!Object.keys(validationErrors).length) return;
  // IPC preserves Error.message but not arbitrary properties consistently.
  // Use the stable translation key as the transport-safe fallback as well as
  // the field-level validation value consumed by the Environment editor.
  const error = new Error(LOG_IP_SALT_REUSES_JWT_VALIDATION_KEY);
  error.code = 'LOG_IP_HASH_SALT_REUSES_JWT_SECRET_KEY';
  error.messageKey = LOG_IP_SALT_REUSES_JWT_VALIDATION_KEY;
  error.validationErrors = validationErrors;
  throw error;
}

function requiredEnvKeysForToggles(toggles) {
  const keys = new Set();
  keys.add('JWT_SECRET_KEY');
  keys.add('LOG_IP_HASH_SALT');
  keys.add('ENCRYPTION_KEY');
  if (toggles.useBundledDB) {
    keys.add('DATABASE_PASSWORD');
  } else {
    keys.add('DATABASE_URL');
  }
  if (toggles.redisEnabled) {
    if (toggles.useBundledRedis) {
      keys.add('REDIS_PASSWORD');
    } else {
      keys.add('REDIS_URL');
    }
  }
  if (toggles.useBundledStorage) {
    keys.add('MINIO_ROOT_USER');
    keys.add('MINIO_ROOT_PASSWORD');
  }
  return Array.from(keys);
}

function envRequirementIssue(key, message, kind = 'missing') {
  return {
    key,
    label: humanizeEnvKey(key),
    message,
    kind,
  };
}

function launcherEnvKeyIsHidden(key) {
  return LAUNCHER_HIDDEN_ENV_KEYS.has(String(key || '').trim());
}

function buildEnvRequirementStatus(env = {}) {
  const toggles = readEnvToggles(env);
  const requiredKeys = new Set(requiredEnvKeysForToggles(toggles));
  const issues = [];

  for (const key of requiredKeys) {
    if (!envValueIsFilled(env[key])) {
      issues.push(envRequirementIssue(key, 'Set a non-placeholder value.'));
    }
  }

  if (envValueIsFilled(env.JWT_SECRET_KEY) && jwtSecretByteLength(env.JWT_SECRET_KEY) < 64) {
    issues.push(envRequirementIssue('JWT_SECRET_KEY', 'Use at least 64 bytes.', 'invalid'));
  }
  if (envValueIsFilled(env.LOG_IP_HASH_SALT) && String(env.LOG_IP_HASH_SALT).trim().length < 16) {
    issues.push(envRequirementIssue('LOG_IP_HASH_SALT', 'Use at least 16 characters.', 'invalid'));
  }
  for (const [key, message] of Object.entries(independentSecuritySecretErrors(env))) {
    issues.push(envRequirementIssue(key, message, 'invalid'));
  }

  if (toggles.redisEnabled && !toggles.useBundledRedis) {
    const redisUrl = String(env.REDIS_URL || '').trim();
    if (envValueIsFilled(redisUrl)) {
      if (/(localhost|127\.0\.0\.1):/.test(redisUrl) || redisUrl === 'redis://redis:6379/0') {
        issues.push(envRequirementIssue('REDIS_URL', 'Use your external Redis service, not localhost or the bundled redis hostname.', 'invalid'));
      }
    }
  }

  const dedupedIssues = [];
  const seen = new Set();
  for (const issue of issues) {
    const id = `${issue.key}:${issue.kind}`;
    if (seen.has(id)) continue;
    seen.add(id);
    dedupedIssues.push(issue);
  }

  const missingKeys = dedupedIssues
    .filter((issue) => issue.kind === 'missing')
    .map((issue) => issue.key);
  const invalidKeys = dedupedIssues
    .filter((issue) => issue.kind !== 'missing')
    .map((issue) => issue.key);

  return {
    ok: dedupedIssues.length === 0,
    requiredKeys: Array.from(requiredKeys),
    missingKeys,
    invalidKeys,
    issues: dedupedIssues,
    message: dedupedIssues.length
      ? `Set required .env variables before running server actions: ${dedupedIssues.map((issue) => issue.key).join(', ')}.`
      : 'All required .env variables are set.',
  };
}

async function exists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch (error) {
    return false;
  }
}

async function copyFileEnsuringDirectory(source, target) {
  await fs.mkdir(path.dirname(target), { recursive: true });
  const temporaryFile = path.join(
    path.dirname(target),
    `.${path.basename(target)}.${crypto.randomUUID()}.tmp`,
  );
  try {
    // copyFile() opens its destination with truncation. Copying beside the
    // target first ensures readers see either the previous complete asset or
    // the complete replacement, even if another process refreshes it too.
    await fs.copyFile(source, temporaryFile);
    // Windows requires a writable handle for FlushFileBuffers, which backs
    // FileHandle.sync(). The temporary copy is ours and already complete.
    const temporaryHandle = await fs.open(temporaryFile, 'r+');
    try {
      await temporaryHandle.sync();
    } finally {
      await temporaryHandle.close();
    }
    await fs.rename(temporaryFile, target);
  } finally {
    await fs.rm(temporaryFile, { force: true }).catch(() => {});
  }
}

function parseComposeJson(stdout) {
  const trimmed = String(stdout || '').trim();
  if (!trimmed) return [];
  try {
    const parsed = JSON.parse(trimmed);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch (error) {
    const rows = [];
    for (const line of trimmed.split(/\r?\n/)) {
      if (!line.trim()) continue;
      try {
        rows.push(JSON.parse(line));
      } catch (lineError) {
        return [];
      }
    }
    return rows;
  }
}

// These Compose services are expected to complete and exit successfully. They
// are prerequisites for the long-running stack, not dashboard services, so
// counting them as stopped would make a healthy deployment look incomplete.
const COMPOSE_INIT_SERVICES = new Set(['migrate', 'minio_init', 'metrics_token']);
const COMPOSE_FAILURE_LOG_TAIL = 120;

/** Parse the active service names emitted by `docker compose config --services`. */
function parseComposeServiceNames(stdout) {
  const seen = new Set();
  return String(stdout || '')
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter((value) => {
      if (!value || COMPOSE_INIT_SERVICES.has(value) || seen.has(value)) return false;
      seen.add(value);
      return true;
    });
}

/** Return the stable expected-service view used by the launcher dashboard. */
function mergeExpectedComposeServices(expectedNames, runtimeRows, options = {}) {
  const rows = Array.isArray(runtimeRows) ? runtimeRows : [];
  const configuredNames = Array.isArray(expectedNames)
    ? expectedNames.filter((name) => name && !COMPOSE_INIT_SERVICES.has(name))
    : [];
  const runtimeByService = new Map();
  let runtimeReadFailed = false;
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    // Compose 2.20+ includes Service in JSON output. Name/Names identify the
    // concrete container and must never be treated as a Compose service key.
    const serviceName = String(row.Service || '').trim();
    if (!serviceName) {
      runtimeReadFailed = true;
      continue;
    }
    if (!serviceName || COMPOSE_INIT_SERVICES.has(serviceName) || runtimeByService.has(serviceName)) {
      continue;
    }
    runtimeByService.set(serviceName, row);
  }

  // A failed config query should degrade to the runtime rows instead of
  // claiming that no services are expected. A successful query is
  // authoritative even if it unexpectedly returns an empty set.
  const serviceNames = options.expectedKnown === true
    ? configuredNames
    : [...new Set([...configuredNames, ...runtimeByService.keys()])];
  const services = serviceNames.map((serviceName) => {
    const runtime = runtimeByService.get(serviceName);
    if (runtime) {
      return {
        ...runtime,
        Service: serviceName,
        Expected: true,
        Missing: false,
      };
    }
    return {
      Service: serviceName,
      State: 'not_created',
      Status: '',
      Health: '',
      Expected: true,
      Missing: true,
    };
  });
  const running = services.filter(
    (service) => String(service.State || '').toLowerCase() === 'running',
  ).length;
  const present = services.filter((service) => service.Missing !== true).length;
  const healthIssues = services.filter((service) => {
    if (String(service.State || '').toLowerCase() !== 'running') return false;
    const health = String(service.Health || service.Status || '').toLowerCase();
    return health.includes('unhealthy') || health.includes('starting');
  }).length;

  return {
    services,
    running,
    total: services.length,
    present,
    missing: services.length - present,
    notRunning: services.length - running,
    healthIssues,
    expectedKnown: options.expectedKnown === true,
    runtimeReadFailed,
  };
}

/**
 * Apply the launcher's full-stack readiness contract.
 *
 * The endpoint can become responsive while another configured service still
 * reports `starting`. Lifecycle operations must not return success during that
 * gap because the dashboard and unattended-update guard would immediately
 * describe the same stack as incomplete.
 */
function stackReadinessHealthy(summary, endpointReady) {
  return Boolean(
    endpointReady
    && Number(summary?.total || 0) > 0
    && Number(summary?.running || 0) === Number(summary?.total || 0)
    && Number(summary?.missing || 0) === 0
    && Number(summary?.healthIssues || 0) === 0
  );
}

function requestUrl(url, timeoutMs = 3500) {
  return new Promise((resolve) => {
    let parsedUrl;
    try {
      parsedUrl = url instanceof URL ? url : new URL(url);
    } catch (error) {
      resolve({ ok: false, statusCode: null });
      return;
    }

    const client = parsedUrl.protocol === 'https:' ? https : http;
    if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
      resolve({ ok: false, statusCode: null });
      return;
    }

    const req = client.get(parsedUrl, (res) => {
      res.resume();
      resolve({ ok: res.statusCode >= 200 && res.statusCode < 400, statusCode: res.statusCode });
    });
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      resolve({ ok: false, statusCode: null });
    });
    req.on('error', () => resolve({ ok: false, statusCode: null }));
  });
}

function requestJson(url, timeoutMs = 3500, requestOptions = {}) {
  return new Promise((resolve) => {
    let parsedUrl;
    try {
      parsedUrl = url instanceof URL ? url : new URL(url);
    } catch (error) {
      resolve({ ok: false, statusCode: null, data: null });
      return;
    }

    const client = parsedUrl.protocol === 'https:' ? https : http;
    if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
      resolve({ ok: false, statusCode: null, data: null });
      return;
    }

    const req = client.get(parsedUrl, requestOptions, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => {
        body += chunk;
      });
      res.on('end', () => {
        let data = null;
        try {
          data = body ? JSON.parse(body) : null;
        } catch (error) {
          data = null;
        }
        resolve({ ok: res.statusCode >= 200 && res.statusCode < 400, statusCode: res.statusCode, data });
      });
    });
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      resolve({ ok: false, statusCode: null, data: null });
    });
    req.on('error', () => resolve({ ok: false, statusCode: null, data: null }));
  });
}

/**
 * Reserve the same cross-process mutation lock used by omlorix-server.
 *
 * The synchronous open is intentional: every mutation must own the lock before
 * its first asynchronous Docker or filesystem action can yield.
 */
function acquireSharedOperationLock(serverHome, command) {
  fssync.mkdirSync(serverHome, { recursive: true });
  const lockPath = path.join(serverHome, '.omlorix-server.lock');
  const openLock = () => fssync.openSync(lockPath, 'wx', 0o600);
  let descriptor;
  try {
    descriptor = openLock();
  } catch (error) {
    if (error?.code === 'EEXIST') {
      try {
        const lockAge = Date.now() - fssync.statSync(lockPath).mtimeMs;
        if (lockAge > 6 * 60 * 60 * 1000) {
          fssync.unlinkSync(lockPath);
          descriptor = openLock();
        }
      } catch (retryError) {
        if (retryError?.code !== 'ENOENT') throw retryError;
        descriptor = openLock();
      }
    }
    if (descriptor === undefined) {
      throw new Error('Another Omlorix server operation is already active.');
    }
  }
  const token = crypto.randomBytes(32).toString('hex');
  fssync.writeFileSync(
    descriptor,
    `pid=${process.pid} command=${command} started=${new Date().toISOString()} token=${token}\n`,
  );
  fssync.closeSync(descriptor);
  const release = () => {
    try {
      fssync.unlinkSync(lockPath);
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
  };
  release.token = token;
  return release;
}

class ServerManager extends EventEmitter {
  constructor({ app, appRoot, now = Date.now }) {
    super();
    this.app = app;
    this.appRoot = appRoot;
    this.now = now;
    this.serverHome = path.join(app.getPath('userData'), 'server');
    this.envFile = path.join(this.serverHome, '.env');
    this.setupStateFile = path.join(this.serverHome, '.launcher-setup.json');
    this.automaticEnvBackupConfigFile = path.join(this.serverHome, '.omlorix-server-env-backup.json');
    this.launcherMetadataFile = path.join(this.serverHome, '.launcher-metadata.json');
    this.serverSettingsFile = path.join(this.serverHome, 'server-settings.json');
    this.setupStateWrite = Promise.resolve();
    this.launcherMetadataWrite = Promise.resolve();
    this.serverSettingsWrite = Promise.resolve();
    // Concurrent callers share one complete initialization, while deployment
    // assets are prepared only once per Launcher process. Migrations remain
    // repeatable because trusted imports and CLI activity can add legacy state.
    this.serverHomeInitialization = null;
    this.serverHomeAssetPreparation = null;
    // Keep only a stable, renderer-safe status code here. Raw filesystem
    // exceptions can contain untranslated platform text and absolute paths, so
    // they must never cross the IPC boundary as launcher UI copy.
    this.automaticEnvBackupError = '';
    this.activeOperation = null;
    this.sharedOperationLockToken = '';
    this.pendingEnvImports = new Map();
    this.logFollowSession = null;
    this.availableVersionsCache = new Map();
    this.availableVersionsFailures = new Map();
    this.availableVersionsRequests = new Map();
    this.spawnLogProcess = (executable, args, options) => spawn(executable, args, options);
    this.proxy = new LauncherReverseProxy();
  }

  /** Run a mutation under the CLI-compatible per-server lock. */
  async withSharedOperationLock(command, operation, { lockHeld = false } = {}) {
    if (!lockHeld && this.activeOperation) {
      throw new Error(`Another operation is already running: ${this.activeOperation}`);
    }
    const releaseSharedLock = lockHeld
      ? () => {}
      : acquireSharedOperationLock(this.serverHome, command);
    const previousLockToken = this.sharedOperationLockToken;
    if (!lockHeld) this.sharedOperationLockToken = releaseSharedLock.token;
    try {
      return await operation();
    } finally {
      this.sharedOperationLockToken = previousLockToken;
      releaseSharedLock();
    }
  }

  resourceRoot() {
    if (this.app.isPackaged) {
      return path.join(process.resourcesPath, 'deployment-assets');
    }
    return this.appRoot;
  }

  async ensureServerHome() {
    if (!this.serverHomeInitialization) {
      const initialization = this.initializeServerHome();
      const guardedInitialization = initialization.finally(() => {
        if (this.serverHomeInitialization === guardedInitialization) {
          this.serverHomeInitialization = null;
        }
      });
      this.serverHomeInitialization = guardedInitialization;
    }
    return this.serverHomeInitialization;
  }

  /** Prepare deployment assets and one-time installation metadata. */
  async initializeServerHome() {
    await this.ensureServerHomeAssets();

    // Management preferences do not belong in the container environment. Move
    // the legacy channel and remove retired image repository overrides before
    // any Docker command can consume the installation configuration.
    await this.migrateLegacyServerSettings();

    // Early builds stored launcher history in .env. Move that value into the
    // per-server launcher metadata before any Docker command can consume it.
    await this.migrateLegacyLauncherMetadata();
  }

  /** Return the first successful, process-local asset preparation promise. */
  async ensureServerHomeAssets() {
    if (!this.serverHomeAssetPreparation) {
      const preparation = this.prepareServerHomeAssets();
      const guardedPreparation = preparation.catch((error) => {
        if (this.serverHomeAssetPreparation === guardedPreparation) {
          this.serverHomeAssetPreparation = null;
        }
        throw error;
      });
      this.serverHomeAssetPreparation = guardedPreparation;
    }
    return this.serverHomeAssetPreparation;
  }

  async prepareServerHomeAssets() {
    await fs.mkdir(this.serverHome, { recursive: true });
    const sourceRoot = this.resourceRoot();
    for (const relativePath of SERVER_FILES) {
      const source = path.join(sourceRoot, relativePath);
      const target = path.join(this.serverHome, relativePath);
      if (await exists(source)) {
        await copyFileEnsuringDirectory(source, target);
      }
    }

    let freshEnvironment = false;
    if (!(await exists(this.envFile))) {
      freshEnvironment = true;
      const example = path.join(this.serverHome, '.env.example');
      if (await exists(example)) {
        await copyFileEnsuringDirectory(example, this.envFile);
      } else {
        await fs.writeFile(this.envFile, '', 'utf8');
      }

      // A marker is created at the same moment as a genuinely new environment.
      // Existing installations predate this file and are migrated separately,
      // so an upgrade never forces a configured administrator through onboarding.
      if (!(await exists(this.setupStateFile))) {
        await this.writeSetupState(defaultSetupState());
      }
    }

    if (freshEnvironment) {
      const raw = await fs.readFile(this.envFile, 'utf8').catch(() => '');
      await fs.writeFile(this.envFile, `${updateEnvContent(raw, {
        COMPOSE_PROJECT_NAME: composeProjectNameForHome(this.serverHome),
        OMLORIX_INSTALLATION_ID: crypto.randomBytes(32).toString('hex'),
        FRONTEND_HTTP_HOST_BIND: '127.0.0.1',
      })}\n`, 'utf8');
    }
  }

  /** Read the shared Launcher/CLI settings for this server installation. */
  async readServerSettings() {
    try {
      const stored = JSON.parse(await fs.readFile(this.serverSettingsFile, 'utf8'));
      const channel = String(stored?.updateChannel || '').trim().toLowerCase();
      if (!UPDATE_CHANNELS.includes(channel)) {
        throw new Error('Server settings contain an invalid update channel.');
      }
      return defaultServerSettings(stored);
    } catch (error) {
      if (error?.code === 'ENOENT') return defaultServerSettings();
      if (error instanceof SyntaxError) throw new Error('Server settings are invalid.');
      throw error;
    }
  }

  /** Persist shared management settings atomically beside the server home. */
  async writeServerSettings(value) {
    await fs.mkdir(this.serverHome, { recursive: true });
    const settings = defaultServerSettings(value);
    const temporaryFile = `${this.serverSettingsFile}.${crypto.randomUUID()}.tmp`;
    try {
      await fs.writeFile(temporaryFile, `${JSON.stringify(settings, null, 2)}\n`, {
        encoding: 'utf8',
        mode: 0o600,
      });
      await fs.rename(temporaryFile, this.serverSettingsFile);
    } finally {
      await fs.rm(temporaryFile, { force: true }).catch(() => {});
    }
    // Recovery snapshots include host-management state even though the live
    // Compose env does not. Refresh an already configured external copy after
    // every settings change, matching writeEnvContent().
    try {
      const rawEnv = await fs.readFile(this.envFile, 'utf8');
      if (!this.suppressAutomaticEnvBackupRefresh) {
        await this.refreshAutomaticEnvBackup(rawEnv);
      }
      this.automaticEnvBackupError = '';
    } catch (error) {
      if (error?.code !== 'ENOENT') this.automaticEnvBackupError = 'write_failed';
    }
    return settings;
  }

  /** Serialize settings updates shared with asynchronous launcher checks. */
  async updateServerSettings(update) {
    const applyUpdate = async () => {
      const current = await this.readServerSettings();
      const next = typeof update === 'function' ? update({ ...current }) : update;
      return this.writeServerSettings(next || current);
    };
    const operation = this.serverSettingsWrite.then(applyUpdate, applyUpdate);
    this.serverSettingsWrite = operation.then(() => undefined, () => undefined);
    return operation;
  }

  /**
   * Move the former dotenv update preference into shared management settings
   * and permanently retire obsolete environment settings. Existing JSON
   * settings win if both stores are present.
   */
  async migrateLegacyServerSettings() {
    const raw = await fs.readFile(this.envFile, 'utf8').catch((error) => {
      if (error?.code === 'ENOENT') return '';
      throw error;
    });
    const env = parseEnv(raw);
    const legacyKeys = [
      'OMLORIX_UPDATE_CHANNEL',
      'OMLORIX_BACKEND_IMAGE_REPOSITORY',
      'OMLORIX_FRONTEND_IMAGE_REPOSITORY',
      'FILE_SCANNER_COMMAND',
      ...RETIRED_ENV_KEYS,
      ...MANAGED_PROXY_SETTINGS_ENV_KEYS,
    ];
    const presentKeys = legacyKeys.filter((key) => Object.prototype.hasOwnProperty.call(env, key));
    const settingsExist = await exists(this.serverSettingsFile);
    let storedSettings = null;
    if (settingsExist) {
      storedSettings = JSON.parse(await fs.readFile(this.serverSettingsFile, 'utf8'));
    }
    let settings = await this.readServerSettings();
    if (!settingsExist && Object.prototype.hasOwnProperty.call(env, 'OMLORIX_UPDATE_CHANNEL')) {
      const legacyChannel = String(env.OMLORIX_UPDATE_CHANNEL || '').trim().toLowerCase();
      if (legacyChannel && !UPDATE_CHANNELS.includes(legacyChannel)) {
        throw new Error(`Invalid legacy update channel ${legacyChannel}.`);
      }
      settings = defaultServerSettings({ updateChannel: legacyChannel || DEFAULT_CHANNEL });
    }
    const legacyProxyKeysPresent = [...MANAGED_PROXY_SETTINGS_ENV_KEYS]
      .some((key) => Object.prototype.hasOwnProperty.call(env, key));
    if (legacyProxyKeysPresent && !(storedSettings?.proxy && typeof storedSettings.proxy === 'object')) {
      settings = defaultServerSettings({
        ...settings,
        proxy: proxySettingsFromEnv(env),
      });
    }
    if (presentKeys.length) {
      await this.createEnvBackup(removeEnvKeysFromContent(raw, [...RETIRED_ENV_KEYS]));
    }
    if (!settingsExist || Number(storedSettings?.schemaVersion || 0) < SERVER_SETTINGS_VERSION || legacyProxyKeysPresent) {
      await this.writeServerSettings(settings);
    }
    if (!presentKeys.length) return;
    const migratedRaw = removeEnvKeysFromContent(raw, legacyKeys);
    await this.writeEnvContent(`${migratedRaw.trimEnd()}\n`);
  }

  /** Read launcher-owned metadata for this specific server installation. */
  async readLauncherMetadata() {
    try {
      const stored = JSON.parse(await fs.readFile(this.launcherMetadataFile, 'utf8'));
      return defaultLauncherMetadata(stored);
    } catch (error) {
      if (error?.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error;
      return defaultLauncherMetadata();
    }
  }

  /** Persist launcher metadata atomically without exposing it to Compose. */
  async writeLauncherMetadata(value) {
    await fs.mkdir(this.serverHome, { recursive: true });
    const metadata = defaultLauncherMetadata(value);
    const temporaryFile = `${this.launcherMetadataFile}.${crypto.randomUUID()}.tmp`;
    await fs.writeFile(temporaryFile, `${JSON.stringify(metadata, null, 2)}\n`, 'utf8');
    await fs.rename(temporaryFile, this.launcherMetadataFile);
    return metadata;
  }

  /** Serialize read-modify-write updates so concurrent health checks stay monotonic. */
  async updateLauncherMetadata(update) {
    const applyUpdate = async () => {
      const current = await this.readLauncherMetadata();
      const next = typeof update === 'function' ? update({ ...current }) : update;
      return this.writeLauncherMetadata(next || current);
    };
    const operation = this.launcherMetadataWrite.then(applyUpdate, applyUpdate);
    this.launcherMetadataWrite = operation.then(() => undefined, () => undefined);
    return operation;
  }

  /** Migrate and remove the short-lived legacy .env metadata variable. */
  async migrateLegacyLauncherMetadata() {
    const raw = await fs.readFile(this.envFile, 'utf8').catch((error) => {
      if (error?.code === 'ENOENT') return '';
      throw error;
    });
    const env = parseEnv(raw);
    if (!Object.prototype.hasOwnProperty.call(env, 'OMLORIX_HIGHEST_VERSION_USED')) return;

    const legacyHighest = trackableServerVersion(env.OMLORIX_HIGHEST_VERSION_USED);
    if (legacyHighest) {
      await this.updateLauncherMetadata((current) => ({
        ...current,
        highestSuccessfulServerVersion: highestServerVersion(
          current.highestSuccessfulServerVersion,
          legacyHighest,
        ),
      }));
    }

    const migratedRaw = removeEnvKeysFromContent(raw, ['OMLORIX_HIGHEST_VERSION_USED']);
    await fs.writeFile(this.envFile, `${migratedRaw.trimEnd()}\n`, 'utf8');
  }

  /** Persist setup metadata atomically enough for a small local JSON file. */
  async writeSetupState(value) {
    await fs.mkdir(this.serverHome, { recursive: true });
    const state = defaultSetupState(value);
    // Status refresh, environment hydration, and onboarding can all request
    // state concurrently during startup. A unique temporary name prevents one
    // writer from renaming another writer's file out from underneath it.
    const temporaryFile = `${this.setupStateFile}.${crypto.randomUUID()}.tmp`;
    await fs.writeFile(temporaryFile, `${JSON.stringify(state, null, 2)}\n`, 'utf8');
    await fs.rename(temporaryFile, this.setupStateFile);
    return state;
  }

  /**
   * Read the resumable first-run state and migrate installations created before
   * the wizard existed. A valid pre-existing environment is treated as complete.
   */
  async readSetupState(env = {}) {
    let stored = null;
    let stateFileMissing = false;
    try {
      stored = JSON.parse(await fs.readFile(this.setupStateFile, 'utf8'));
    } catch (error) {
      stateFileMissing = error?.code === 'ENOENT';
      if (!stateFileMissing && !(error instanceof SyntaxError)) throw error;
    }

    if (!stored) {
      const requirements = buildEnvRequirementStatus(env);
      // Only a genuinely missing marker denotes a pre-wizard installation.
      // Corrupt setup metadata fails closed and resumes onboarding so a damaged
      // file can never bypass the required secrets export.
      const legacyComplete = stateFileMissing && requirements.ok;
      stored = defaultSetupState({
        complete: legacyComplete,
        currentStep: legacyComplete ? 6 : 0,
        completedAt: legacyComplete ? new Date().toISOString() : '',
      });
      await this.writeSetupState(stored);
    }

    const state = defaultSetupState(stored);
    const backupConfig = await this.readAutomaticEnvBackupConfig(state);
    const physicalRaw = await fs.readFile(this.envFile, 'utf8').catch(() => '');
    const liveRaw = Buffer.from(await this.recoveryEnvContent(physicalRaw));
    const currentFingerprint = crypto.createHash('sha256').update(liveRaw).digest('hex');
    let backupFileCurrent = false;
    if (backupConfig.target) {
      try {
        // Verify the actual recovery file rather than trusting metadata alone.
        // This detects deleted, moved, externally edited, and unavailable
        // destinations after a launcher restart.
        const backupStat = await fs.stat(backupConfig.target);
        if (backupStat.isFile() && backupStat.size <= 1024 * 1024) {
          const backupRaw = await fs.readFile(backupConfig.target);
          backupFileCurrent = crypto.createHash('sha256').update(backupRaw).digest('hex') === currentFingerprint;
        }
      } catch {
        backupFileCurrent = false;
      }
    }
    return {
      ...state,
      backupFingerprint: backupConfig.fingerprint,
      backupSavedAt: backupConfig.lastSavedAt,
      backupFileName: backupConfig.target ? path.basename(backupConfig.target) : '',
      backupFilePath: backupConfig.target,
      required: !state.complete,
      backupCurrent: Boolean(
        backupConfig.fingerprint
        && backupConfig.fingerprint === currentFingerprint
        && backupFileCurrent
      ),
      backupConfigured: Boolean(backupConfig.target),
      backupFingerprintShort: backupConfig.fingerprint ? backupConfig.fingerprint.slice(0, 12) : '',
      currentFingerprintShort: currentFingerprint.slice(0, 12),
    };
  }

  /** Read the CLI-compatible recovery-copy record, with one-way legacy fallback. */
  async readAutomaticEnvBackupConfig(legacySetup = null) {
    try {
      const parsed = JSON.parse(await fs.readFile(this.automaticEnvBackupConfigFile, 'utf8'));
      return {
        target: String(parsed?.target || ''),
        lastSavedAt: String(parsed?.lastSavedAt || ''),
        fingerprint: String(parsed?.fingerprint || ''),
        lastError: String(parsed?.lastError || ''),
      };
    } catch (error) {
      if (error?.code !== 'ENOENT') {
        if (error instanceof SyntaxError) throw new Error('Automatic .env backup settings are invalid.');
        throw error;
      }
    }
    let legacy = legacySetup;
    if (!legacy) {
      try {
        legacy = JSON.parse(await fs.readFile(this.setupStateFile, 'utf8'));
      } catch (error) {
        if (error?.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error;
      }
    }
    const setup = defaultSetupState(legacy || {});
    let legacyFingerprint = '';
    if (setup.backupFilePath) {
      try {
        legacyFingerprint = crypto.createHash('sha256')
          .update(await fs.readFile(setup.backupFilePath))
          .digest('hex');
      } catch {
        legacyFingerprint = '';
      }
    }
    return {
      target: setup.backupFilePath,
      lastSavedAt: setup.backupSavedAt,
      fingerprint: legacyFingerprint,
      lastError: '',
    };
  }

  /** Atomically persist the shared Launcher/CLI recovery-copy record. */
  async writeAutomaticEnvBackupConfig(config) {
    const temporaryFile = `${this.automaticEnvBackupConfigFile}.${crypto.randomUUID()}.tmp`;
    const payload = {
      target: String(config?.target || ''),
      lastSavedAt: String(config?.lastSavedAt || ''),
      fingerprint: String(config?.fingerprint || ''),
      ...(config?.lastError ? { lastError: String(config.lastError) } : {}),
    };
    await fs.writeFile(temporaryFile, `${JSON.stringify(payload, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
    await fs.rename(temporaryFile, this.automaticEnvBackupConfigFile);
    return payload;
  }

  /** Save only navigation/completion state; deployment state remains in its two stores. */
  async saveSetupProgress(payload = {}) {
    return this.withSharedOperationLock(
      'setup progress',
      () => this.saveSetupProgressUnlocked(payload),
    );
  }

  async saveSetupProgressUnlocked(payload = {}) {
    await this.ensureServerHome();
    await this.ensureGeneratedSecrets();
    const env = await this.readEnv();
    const current = await this.readSetupState(env);
    const nextStep = Number.isInteger(payload.currentStep)
      ? Math.max(0, Math.min(6, payload.currentStep))
      : current.currentStep;
    const wantsComplete = payload.complete === true;
    if (wantsComplete && !current.backupCurrent) {
      throw new Error('Choose a current automatic .env backup before completing server setup.');
    }
    const next = {
      ...current,
      currentStep: nextStep,
      complete: wantsComplete ? true : current.complete,
      completedAt: wantsComplete ? (current.completedAt || new Date().toISOString()) : current.completedAt,
    };
    delete next.required;
    delete next.backupCurrent;
    delete next.backupConfigured;
    delete next.backupFingerprintShort;
    delete next.currentFingerprintShort;
    const persisted = await this.writeSetupState(next);
    // Checkpoint navigation must not wait for the broad Launcher snapshot.
    // getState() probes Docker, Compose, the proxy, and network topology, even
    // though the setup file has already been committed. Return only the fields
    // the renderer needs to acknowledge that durable commit.
    return {
      setup: {
        complete: persisted.complete,
        currentStep: persisted.currentStep,
        completedAt: persisted.completedAt,
        required: !persisted.complete,
      },
    };
  }

  /**
   * Validate that an automatic backup target is separate from the live server
   * directory. A copy inside serverHome would be lost with the deployment it
   * is meant to recover and could also be consumed accidentally by tooling.
   */
  automaticEnvBackupPath(targetPath, options = {}) {
    const rawTarget = String(targetPath || '').trim();
    if (!rawTarget) throw new Error('Choose a file path for the .env backup.');
    const normalizedTarget = path.resolve(rawTarget);
    const relativeToServerHome = path.relative(path.resolve(this.serverHome), normalizedTarget);
    if (!relativeToServerHome.startsWith('..') && !path.isAbsolute(relativeToServerHome)) {
      throw new Error(
        options.outsideServerMessage
        || 'Store the .env backup outside the Omlorix server folder.',
      );
    }
    return normalizedTarget;
  }

  /**
   * Atomically replace the selected external .env copy and restrict its file
   * permissions where the host platform supports POSIX modes.
   */
  async writeAutomaticEnvBackupFile(targetPath, raw) {
    const normalizedTarget = this.automaticEnvBackupPath(targetPath);
    const temporaryFile = `${normalizedTarget}.${crypto.randomUUID()}.tmp`;
    await fs.writeFile(temporaryFile, String(raw || ''), { encoding: 'utf8', mode: 0o600 });
    try {
      await fs.rename(temporaryFile, normalizedTarget);
    } catch (error) {
      // Windows does not consistently replace an existing destination with
      // rename(). copyFile() still writes from a complete temporary file and
      // therefore avoids exposing partially generated backup contents.
      if (process.platform === 'win32' && ['EEXIST', 'EPERM'].includes(error?.code)) {
        await fs.copyFile(temporaryFile, normalizedTarget);
        await fs.rm(temporaryFile, { force: true });
        await fs.chmod(normalizedTarget, 0o600).catch(() => {});
        return normalizedTarget;
      }
      await fs.rm(temporaryFile, { force: true }).catch(() => {});
      throw error;
    }
    await fs.chmod(normalizedTarget, 0o600).catch(() => {});
    return normalizedTarget;
  }

  /** Record a successful external copy as the current automatic destination. */
  async recordAutomaticEnvBackup(targetPath, raw) {
    const applyUpdate = async () => {
      const normalizedTarget = await this.writeAutomaticEnvBackupFile(targetPath, raw);
      const parsed = parseEnv(raw);
      const fingerprint = crypto.createHash('sha256').update(String(raw || '')).digest('hex');
      const savedAt = new Date().toISOString();
      await this.writeAutomaticEnvBackupConfig({
        target: normalizedTarget,
        fingerprint,
        lastSavedAt: savedAt,
      });
      const current = await this.readSetupState(parsed);
      await this.writeSetupState({
        ...current,
        backupFingerprint: envBackupFingerprint(parsed),
        backupSavedAt: savedAt,
        backupFileName: path.basename(normalizedTarget),
        backupFilePath: normalizedTarget,
      });
      this.automaticEnvBackupError = '';
      return normalizedTarget;
    };
    const operation = this.setupStateWrite.then(applyUpdate, applyUpdate);
    this.setupStateWrite = operation.then(() => undefined, () => undefined);
    return operation;
  }

  /**
   * Remember an explicitly selected recovery destination even when its first
   * automatic refresh fails. Persisting the path lets the derived freshness
   * check keep the warning visible after the launcher restarts and gives Save
   * now a deterministic target to retry.
   */
  async rememberAutomaticEnvBackupTarget(targetPath, env) {
    const normalizedTarget = this.automaticEnvBackupPath(targetPath);
    const applyUpdate = async () => {
      const current = await this.readSetupState(env);
      const existing = await this.readAutomaticEnvBackupConfig(current);
      await this.writeAutomaticEnvBackupConfig({ ...existing, target: normalizedTarget });
      await this.writeSetupState({
        ...current,
        backupFileName: path.basename(normalizedTarget),
        backupFilePath: normalizedTarget,
      });
    };
    const operation = this.setupStateWrite.then(applyUpdate, applyUpdate);
    this.setupStateWrite = operation.then(() => undefined, () => undefined);
    return operation;
  }

  /** Refresh the configured external copy after a launcher-managed .env edit. */
  async refreshAutomaticEnvBackup(raw) {
    const targetPath = (await this.readAutomaticEnvBackupConfig()).target;
    if (!targetPath) return { configured: false, filePath: '' };
    const filePath = await this.recordAutomaticEnvBackup(
      targetPath,
      await this.recoveryEnvContent(raw),
    );
    return { configured: true, filePath };
  }

  /** Build a complete recovery snapshot without polluting the live Compose env. */
  async recoveryEnvContent(raw) {
    const settings = await this.readServerSettings();
    const withoutManagementSettings = removeEnvKeysFromContent(
      String(raw || ''),
      [...RECOVERY_MANAGEMENT_SETTINGS_ENV_KEYS, ...RETIRED_ENV_KEYS],
    );
    return `${updateEnvContent(withoutManagementSettings, {
      OMLORIX_UPDATE_CHANNEL: settings.updateChannel,
      ...proxySettingsEnv(settings),
    }).replace(/[\r\n]+$/, '')}\n`;
  }

  /**
   * Persist the live .env and immediately refresh its external automatic copy.
   * The live change is intentionally retained if the external device becomes
   * unavailable. The warning remains available through getState(), while the
   * successful live write remains operational for its caller.
   */
  async writeEnvContent(nextRaw) {
    const environmentOnlyRaw = removeEnvKeysFromContent(
      String(nextRaw || ''),
      [...MANAGED_PROXY_SETTINGS_ENV_KEYS, ...RETIRED_ENV_KEYS],
    );
    const canonicalRaw = `${updateEnvContent(
      environmentOnlyRaw,
      topologyInvariantUpdates(parseEnv(environmentOnlyRaw)),
    ).trimEnd()}\n`;
    // Every launcher-managed writer funnels through this final boundary. Check
    // the fully merged and canonicalized environment immediately before disk I/O
    // so partial settings edits and imports cannot bypass cross-field policy.
    assertIndependentSecuritySecrets(parseEnv(canonicalRaw));
    // Write beside the target and rename it into place so a crash cannot leave
    // a partially written trust configuration. The 0600 mode also protects the
    // launcher-to-nginx authentication credential stored in this file.
    const temporaryFile = `${this.envFile}.${crypto.randomUUID()}.tmp`;
    try {
      await fs.writeFile(temporaryFile, canonicalRaw, { encoding: 'utf8', mode: 0o600 });
      await fs.rename(temporaryFile, this.envFile);
    } finally {
      await fs.rm(temporaryFile, { force: true }).catch(() => {});
    }
    try {
      await this.refreshAutomaticEnvBackup(canonicalRaw);
      this.automaticEnvBackupError = '';
    } catch (error) {
      this.automaticEnvBackupError = 'write_failed';
    }
  }

  /**
   * Save the complete current .env and remember this path for automatic updates.
   * The legacy method name is retained for the existing preload/IPC boundary.
   */
  async exportSecretsBackup(targetPath) {
    return this.withSharedOperationLock(
      'secrets export',
      () => this.exportSecretsBackupUnlocked(targetPath),
    );
  }

  async exportSecretsBackupUnlocked(targetPath) {
    await this.ensureServerHome();
    await this.ensureGeneratedSecrets();
    const raw = await fs.readFile(this.envFile, 'utf8');
    const normalizedTarget = await this.recordAutomaticEnvBackup(
      targetPath,
      await this.recoveryEnvContent(raw),
    );
    return {
      ok: true,
      filePath: normalizedTarget,
      fileName: path.basename(normalizedTarget),
      state: await this.getState(),
    };
  }

  /** Update the remembered automatic destination without showing a file picker. */
  async saveAutomaticEnvBackup() {
    return this.withSharedOperationLock(
      'secrets save-now',
      () => this.saveAutomaticEnvBackupUnlocked(),
    );
  }

  async saveAutomaticEnvBackupUnlocked() {
    await this.ensureServerHome();
    const raw = await fs.readFile(this.envFile, 'utf8');
    const setup = await this.readSetupState(parseEnv(raw));
    if (!setup.backupFilePath) {
      throw new Error('Choose an automatic .env backup location first.');
    }
    const filePath = await this.recordAutomaticEnvBackup(
      setup.backupFilePath,
      await this.recoveryEnvContent(raw),
    );
    return {
      ok: true,
      filePath,
      fileName: path.basename(filePath),
      state: await this.getState(),
    };
  }

  /**
   * Stop future automatic recovery-copy refreshes without touching the copy.
   *
   * Persisting an explicit empty canonical record is important: both the
   * Launcher and CLI otherwise fall back to legacy setup metadata when the
   * shared record is missing. The operation deliberately never reads or writes
   * the former target, so an unavailable or read-only destination can still be
   * disabled safely.
   */
  async disableAutomaticEnvBackup() {
    return this.withSharedOperationLock(
      'secrets disable-backup',
      async () => {
        await this.ensureServerHome();
        await this.writeAutomaticEnvBackupConfig({});
        this.automaticEnvBackupError = '';
        return {
          ok: true,
          state: await this.getState(),
        };
      },
    );
  }

  /**
   * Restore a trusted, self-contained Launcher recovery snapshot.
   *
   * Unlike Environment imports, recovery deliberately includes installation
   * identity and every launcher-managed security value. Host proxy settings
   * are split back into server-settings.json. Running services keep
   * their current process environment until the operator explicitly restarts.
   */
  async importSecretsBackup(sourcePath) {
    return this.withSharedOperationLock(
      'secrets import',
      () => this.importSecretsBackupUnlocked(sourcePath),
    );
  }

  async importSecretsBackupUnlocked(sourcePath) {
    await this.ensureServerHome();
    // Validate the future automatic destination before changing the live .env,
    // so an invalid in-server import path cannot cause a partial restore.
    const normalizedSource = this.automaticEnvBackupPath(sourcePath, {
      outsideServerMessage: 'The selected Omlorix .env backup must be outside the server folder.',
    });
    const sourceStat = await fs.stat(normalizedSource);
    if (!sourceStat.isFile() || sourceStat.size > 1024 * 1024) {
      throw new Error('Choose an Omlorix .env backup smaller than 1 MB.');
    }
    const rawImport = await fs.readFile(normalizedSource, 'utf8');
    const rawImportedEnv = parseEnv(rawImport);
    const containsRetiredSettings = [...RETIRED_ENV_KEYS]
      .some((key) => Object.prototype.hasOwnProperty.call(rawImportedEnv, key));
    const importableRaw = removeEnvKeysFromContent(rawImport, [...RETIRED_ENV_KEYS]);
    const imported = parseEnv(importableRaw);
    const updates = {};
    for (const key of [...SECRET_BACKUP_KEYS, ...SECRET_BACKUP_CONTEXT_KEYS]) {
      if (Object.prototype.hasOwnProperty.call(imported, key)) updates[key] = imported[key];
    }
    if (!updates.ENCRYPTION_KEY || !updates.JWT_SECRET_KEY) {
      throw new Error('This file is not a complete Omlorix .env backup.');
    }
    if (jwtSecretByteLength(updates.JWT_SECRET_KEY) < 64) {
      throw new Error('The imported JWT secret must contain at least 64 bytes.');
    }
    if (!isValidFernetKey(updates.ENCRYPTION_KEY)) {
      throw new Error('The imported encryption key is not a valid Fernet key.');
    }
    if (!updates.PASSWORD_RESET_IDENTIFIER_HASH_SALT) {
      throw new Error('The imported password reset salt is missing.');
    }
    if (String(updates.PASSWORD_RESET_IDENTIFIER_HASH_SALT).length < 16) {
      throw new Error('The imported password reset salt must contain at least 16 characters.');
    }
    const missingRecoveryKeys = requiredSecretBackupKeys(imported)
      .filter((key) => !envValueIsFilled(imported[key]));
    if (missingRecoveryKeys.length) {
      throw new Error(
        `This Omlorix .env backup is incomplete. Missing: ${missingRecoveryKeys.join(', ')}.`,
      );
    }

    // Recovery validates the complete source independently from the protected
    // Environment-import preview because every key, including launcher-owned
    // identity and trust values, will be restored exactly as supplied.
    const parsedImport = parseEnvDetailed(importableRaw);
    if (parsedImport.invalidLines.length) {
      throw new Error('The selected .env backup contains invalid environment lines.');
    }
    if (parsedImport.duplicateKeys.length) {
      throw new Error(
        `The selected .env backup contains duplicate keys: ${parsedImport.duplicateKeys.join(', ')}.`,
      );
    }
    const metadata = await this.envExampleMetadata();
    const requiredKeys = new Set(requiredEnvKeysForToggles(readEnvToggles(imported)));
    const validationErrors = {};
    for (const [key, value] of Object.entries(imported)) {
      const field = metadata.byKey.get(key) || {
        key,
        type: inferEnvType(key, value),
        secret: isSecretEnvKey(key),
        options: ENV_ENUM_OPTIONS[key] || [],
      };
      const error = validateEnvValue(key, value, {
        ...field,
        required: requiredKeys.has(key),
      });
      if (error) validationErrors[key] = error;
    }
    if (Object.keys(validationErrors).length) {
      const details = Object.entries(validationErrors)
        .map(([key, message]) => `${key}: ${message}`)
        .join('; ');
      throw new Error(`The selected .env backup contains invalid settings. ${details}`);
    }

    const standaloneRequirements = buildEnvRequirementStatus(imported);
    if (!standaloneRequirements.ok) {
      const details = standaloneRequirements.issues
        .map((issue) => `${issue.key}: ${issue.message}`)
        .join('; ');
      throw new Error(`The selected .env backup is incomplete. ${details}`);
    }

    // The recovery file is authoritative for both stores. Split the update
    // channel and host proxy settings back out before committing Compose env.
    const currentRaw = await fs.readFile(this.envFile, 'utf8');
    const currentSettings = await this.readServerSettings();
    const recoveredChannel = recoveryUpdateChannelFromEnv(imported);
    const importedHasProxySettings = [...MANAGED_PROXY_SETTINGS_ENV_KEYS]
      .some((key) => Object.prototype.hasOwnProperty.call(imported, key));
    const importedHasManagementSettings = recoveredChannel.present || importedHasProxySettings;
    const nextSettings = importedHasManagementSettings
      ? defaultServerSettings({
          ...currentSettings,
          ...(recoveredChannel.present ? { updateChannel: recoveredChannel.updateChannel } : {}),
          ...(importedHasProxySettings ? { proxy: proxySettingsFromEnv(imported) } : {}),
        })
      : currentSettings;
    const nextRaw = `${removeEnvKeysFromContent(
      importableRaw,
      [...RECOVERY_MANAGEMENT_SETTINGS_ENV_KEYS, ...RETIRED_ENV_KEYS],
    ).replace(/[\r\n]+$/, '')}\n`;
    const changed = currentRaw !== nextRaw
      || JSON.stringify(currentSettings) !== JSON.stringify(nextSettings);
    if (changed) {
      await this.writeExactEnvRecoveryContent(nextRaw);
      if (importedHasManagementSettings) {
        this.suppressAutomaticEnvBackupRefresh = true;
        try {
          await this.writeServerSettings(nextSettings);
        } finally {
          this.suppressAutomaticEnvBackupRefresh = false;
        }
      }
    }

    // The selected file already is the recovery copy. Legacy snapshots that
    // still contain retired settings must be rewritten once so the configured
    // automatic backup cannot preserve or later reintroduce those settings.
    // Use the canonical recovery projection so its persisted fingerprint is
    // immediately current for the restored live environment.
    if (containsRetiredSettings) {
      try {
        await this.recordAutomaticEnvBackup(
          normalizedSource,
          await this.recoveryEnvContent(nextRaw),
        );
      } catch (_error) {
        // The live recovery has already committed. Treat an unavailable or
        // read-only external destination like any other automatic-backup
        // refresh failure: retain the restored live state and expose the stale
        // backup warning so the operator can choose a writable destination.
        await this.rememberAutomaticEnvBackupTarget(normalizedSource, parseEnv(nextRaw));
        this.automaticEnvBackupError = 'write_failed';
      }
    } else {
      await this.adoptExactEnvRecoveryTarget(normalizedSource, rawImport);
    }
    return {
      state: await this.getState(),
      changed,
      restartRequired: changed,
    };
  }

  /** Atomically commit an exact snapshot without launcher-derived rewrites. */
  async writeExactEnvRecoveryContent(raw) {
    const temporaryFile = `${this.envFile}.${crypto.randomUUID()}.recovery.tmp`;
    const sanitizedRaw = removeEnvKeysFromContent(String(raw || ''), [...RETIRED_ENV_KEYS]);
    try {
      await fs.writeFile(temporaryFile, sanitizedRaw, { encoding: 'utf8', mode: 0o600 });
      await fs.rename(temporaryFile, this.envFile);
      await fs.chmod(this.envFile, 0o600).catch(() => {});
    } finally {
      await fs.rm(temporaryFile, { force: true }).catch(() => {});
    }
  }

  /** Record the selected existing snapshot without copying or changing it. */
  async adoptExactEnvRecoveryTarget(targetPath, raw) {
    const normalizedTarget = this.automaticEnvBackupPath(targetPath);
    const parsed = parseEnv(raw);
    const fingerprint = crypto.createHash('sha256').update(String(raw || '')).digest('hex');
    const savedAt = new Date().toISOString();
    await this.writeAutomaticEnvBackupConfig({
      target: normalizedTarget,
      fingerprint,
      lastSavedAt: savedAt,
    });
    const current = await this.readSetupState(parsed);
    await this.writeSetupState({
      ...current,
      backupFingerprint: envBackupFingerprint(parsed),
      backupSavedAt: savedAt,
      backupFileName: path.basename(normalizedTarget),
      backupFilePath: normalizedTarget,
    });
    this.automaticEnvBackupError = '';
  }

  /** Regenerate a reviewed set of secrets with the format each consumer expects. */
  async regenerateSecrets(keys = []) {
    return this.withSharedOperationLock(
      'secrets regenerate',
      () => this.regenerateSecretsUnlocked(keys),
    );
  }

  async regenerateSecretsUnlocked(keys = []) {
    await this.ensureServerHome();
    const requested = Array.isArray(keys) && keys.length
      ? keys.filter((key) => REGENERATABLE_SECRET_KEYS.has(key))
      : [
          'JWT_SECRET_KEY',
          'ENCRYPTION_KEY',
          'PASSWORD_RESET_IDENTIFIER_HASH_SALT',
          'LOG_IP_HASH_SALT',
          'BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE',
          'DATABASE_PASSWORD',
          'REDIS_PASSWORD',
        ];
    const updates = {};
    for (const key of requested) {
      if (key === 'JWT_SECRET_KEY') updates[key] = randomJwtSecret();
      else if (key === 'ENCRYPTION_KEY') updates[key] = randomFernetKey();
      else if (key === 'REDIS_PASSWORD') updates[key] = randomUrlSecret(36);
      else if (key === 'PASSWORD_RESET_IDENTIFIER_HASH_SALT' || key === 'LOG_IP_HASH_SALT') updates[key] = crypto.randomBytes(32).toString('hex');
      else if (key === 'MINIO_ROOT_USER' || key === 'GRAFANA_ADMIN_USER') updates[key] = `omlorix-${crypto.randomBytes(12).toString('base64url')}`;
      else updates[key] = randomSecret(36);
    }
    if (updates.REDIS_PASSWORD) {
      const env = await this.readEnv();
      const toggles = readEnvToggles(env);
      if (toggles.redisEnabled && toggles.useBundledRedis) {
        updates.REDIS_URL = defaultLocalRedisUrl(env, updates.REDIS_PASSWORD);
      }
    }
    await this.writeEnv(updates);
    return this.getState();
  }

  async readEnv() {
    await this.ensureServerHome();
    const raw = await fs.readFile(this.envFile, 'utf8');
    const settings = await this.readServerSettings();
    return { ...parseEnv(raw), ...proxySettingsEnv(settings) };
  }

  proxyConfigFromEnv(env) {
    return normalizeProxyConfig(env);
  }

  serverCliExecutable({ allowEnvironmentOverride = !this.app.isPackaged } = {}) {
    const explicit = String(process.env.OMLORIX_SERVER_CLI_PATH || '').trim();
    // Development and test harnesses may select a locally built CLI, but a
    // packaged or elevated workflow must resolve only the bundled artifact.
    // PowerShell quoting protects syntax; this gate protects provenance.
    if (allowEnvironmentOverride && explicit) return explicit;
    const executableName = process.platform === 'win32' ? 'omlorix-server.exe' : 'omlorix-server';
    const candidates = this.app.isPackaged
      ? [path.join(process.resourcesPath || this.appRoot, 'native', executableName)]
      : [path.join(this.appRoot, '.build', 'cli', executableName)];
    return candidates.find((candidate) => fssync.existsSync(candidate)) || '';
  }

  /** Run the bundled authoritative CLI without leaking environment secrets. */
  execServerCli(argumentsList, { timeoutMs = 15000 } = {}) {
    const executable = this.serverCliExecutable();
    if (!executable) {
      return Promise.resolve({ ok: false, unavailable: true, stdout: '', stderr: '' });
    }
    return new Promise((resolve) => {
      const child = spawn(executable, [
        '--home', this.serverHome,
        '--env-file', this.envFile,
        '--source-root', this.resourceRoot(),
        ...argumentsList,
      ], {
        cwd: this.serverHome,
        windowsHide: true,
        env: {
          ...process.env,
          ...(this.sharedOperationLockToken
            ? { OMLORIX_SERVER_LOCK_TOKEN: this.sharedOperationLockToken }
            : {}),
        },
      });
      let stdout = '';
      let stderr = '';
      let settled = false;
      const finish = (result) => {
        if (settled) return;
        settled = true;
        resolve(result);
      };
      const timer = setTimeout(() => {
        child.kill();
        finish({ ok: false, unavailable: false, stdout, stderr: 'CLI operation timed out.' });
      }, timeoutMs);
      child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
      child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
      child.on('error', (error) => {
        clearTimeout(timer);
        finish({ ok: false, unavailable: error?.code === 'ENOENT', stdout, stderr: '' });
      });
      child.on('close', (code) => {
        clearTimeout(timer);
        finish({ ok: code === 0, unavailable: false, stdout, stderr });
      });
    });
  }

  async proxyServiceStatus() {
    const result = await this.execServerCli(['--json', 'proxy', 'status']);
    if (!result.ok) {
      return { available: !result.unavailable, installed: false, running: false };
    }
    try {
      const parsed = JSON.parse(result.stdout);
      return {
        available: true,
        installed: Boolean(parsed.service_installed),
        running: Boolean(parsed.running),
        updateRequired: Boolean(parsed.service_update_required),
        pid: Number(parsed.pid || 0),
      };
    } catch {
      return { available: true, installed: false, running: false };
    }
  }

  /** Read the CLI's topology-bound external verification without exposing it. */
  async cliVisitorIpVerification() {
    const result = await this.execServerCli(['--json', 'visitor-ip', 'status']);
    if (!result.ok) return { verified: false, errorCode: 'verification_unavailable' };
    try {
      const status = JSON.parse(result.stdout);
      const verification = status.verification || {};
      return {
        // `ready` includes freshness and topology checks; the stored verified
        // bit alone may describe a now-stale external request.
        verified: Boolean(status.ready),
        verifiedAt: String(verification.verified_at || ''),
        topologyFingerprint: String(verification.topology_fingerprint || ''),
        clientIp: String(verification.client_ip || ''),
        scheme: String(verification.scheme || ''),
        host: String(verification.host || ''),
        errorCode: String(verification.error_code || ''),
      };
    } catch {
      return { verified: false, errorCode: 'verification_unavailable' };
    }
  }

  proxyStatus(env, serviceStatus = {}) {
    if (serviceStatus.running) {
      return {
        running: true,
        config: this.proxyConfigFromEnv(env),
        serviceAvailable: Boolean(serviceStatus.available),
        serviceInstalled: Boolean(serviceStatus.installed),
        managedByCli: true,
        managedByService: Boolean(serviceStatus.installed),
        servicePid: Number(serviceStatus.pid || 0),
      };
    }
    const liveStatus = this.proxy.status();
    const status = liveStatus.running
      ? liveStatus
      : this.proxy.status(this.proxyConfigFromEnv(env));
    return {
      ...status,
      serviceAvailable: Boolean(serviceStatus.available),
      serviceInstalled: Boolean(serviceStatus.installed),
      managedByCli: false,
      managedByService: false,
    };
  }

  async initializeProxy() {
    await this.ensureServerHome();
    const env = await this.readEnv();
    const config = this.proxyConfigFromEnv(env);
    let service = await this.proxyServiceStatus();
    if (service.available) {
      if (service.installed && service.updateRequired) {
        try {
          await this.runElevatedWindowsProxyServiceCommand('refresh-service');
          service = await this.proxyServiceStatus();
        } catch (error) {
          const errorMessage = error.message || String(error);
          this.emit('operation-output', {
            name: 'Proxy',
            stream: 'stderr',
            text: `Background proxy service update failed: ${errorMessage}\n`,
            textKey: 'launcher_ui_background_proxy_service_update_failed_error',
            textValues: { error: errorMessage },
          });
        }
      }
      if (this.proxy.status().running) await this.proxy.stop();
      if (!config.enabled && service.running) {
        await this.controlAuthoritativeProxy('stop', service);
      } else if (config.enabled && config.autostart && !service.running) {
        await this.controlAuthoritativeProxy('start', service);
      }
      return this.proxyStatus(env, await this.proxyServiceStatus());
    }
    if (!config.enabled || !config.autostart) {
      return this.proxyStatus(env, service);
    }

    try {
      return await this.proxy.start(config);
    } catch (error) {
      this.emit('operation-output', {
        name: 'Proxy',
        stream: 'stderr',
        text: `Launcher proxy autostart failed: ${error.message || error}\n`,
      });
      return this.proxyStatus(env, service);
    }
  }

  async runProxyServiceCommand(action) {
    const result = await this.execServerCli(['proxy', action], { timeoutMs: 30000 });
    if (!result.ok) {
      const error = new Error('The background proxy service operation failed.');
      error.code = result.unavailable ? 'PROXY_SERVICE_UNAVAILABLE' : 'PROXY_SERVICE_FAILED';
      throw error;
    }
    return result;
  }

  /** Control the CLI proxy, elevating on Windows only for an installed Service. */
  async controlAuthoritativeProxy(action, service = {}) {
    if (process.platform === 'win32' && service.installed) {
      return this.runElevatedWindowsProxyServiceCommand(action);
    }
    return this.runProxyServiceCommand(action);
  }

  async runElevatedWindowsProxyServiceCommand(action) {
    if (process.platform !== 'win32') return this.runProxyServiceCommand(action);
    const executable = this.serverCliExecutable({ allowEnvironmentOverride: false });
    if (!executable) {
      const error = new Error('The background proxy service is unavailable in this build.');
      error.code = 'PROXY_SERVICE_UNAVAILABLE';
      throw error;
    }
    const quotePowerShell = (value) => `'${String(value).replaceAll("'", "''")}'`;
    const argumentList = [
      '--home', this.serverHome,
      '--env-file', this.envFile,
      'proxy', action,
    ].map(quotePowerShell).join(',');
    return new Promise((resolve, reject) => {
      const command = [
        `$process = Start-Process -FilePath ${quotePowerShell(executable)}`,
        `-ArgumentList @(${argumentList}) -Verb RunAs -Wait -PassThru`,
        'exit $process.ExitCode',
      ].join(' ');
      const child = spawn('powershell.exe', [
        '-NoProfile', '-NonInteractive', '-Command', command,
      ], {
        windowsHide: true,
        stdio: 'ignore',
        env: {
          ...process.env,
          ...(this.sharedOperationLockToken
            ? { OMLORIX_SERVER_LOCK_TOKEN: this.sharedOperationLockToken }
            : {}),
        },
      });
      child.on('error', () => reject(new Error('Could not request permission to manage the Windows proxy service.')));
      child.on('close', (code) => {
        if (code === 0) resolve({ ok: true });
        else reject(new Error('The Windows proxy service operation was cancelled or failed.'));
      });
    });
  }

  async writeEnv(updates) {
    await this.ensureServerHome();
    const raw = await fs.readFile(this.envFile, 'utf8').catch(() => '');
    const containerUpdates = Object.fromEntries(
      Object.entries(updates || {}).filter(([key]) => !MANAGED_PROXY_SETTINGS_ENV_KEYS.has(key)),
    );
    const nextRaw = `${updateEnvContent(raw, containerUpdates)}\n`;
    if (nextRaw.trimEnd() === raw.trimEnd()) return;
    await this.writeEnvContent(nextRaw);
  }

  /**
   * Persist the highest concrete Omlorix version that reached a healthy state.
   *
   * The value is monotonic: selecting, importing, or unsuccessfully attempting
   * a lower release can never erase evidence that the database may already
   * contain migrations from a newer release.
   */
  async recordSuccessfulServerVersion(version = '') {
    await this.ensureServerHome();
    const candidate = trackableServerVersion(version);
    if (!candidate) {
      return (await this.readLauncherMetadata()).highestSuccessfulServerVersion;
    }
    const metadata = await this.updateLauncherMetadata((current) => ({
      ...current,
      highestSuccessfulServerVersion: highestServerVersion(
        current.highestSuccessfulServerVersion,
        candidate,
      ),
    }));
    return metadata.highestSuccessfulServerVersion;
  }

  /** Record an already-running server when the launcher attaches after startup. */
  async recordRunningServerVersion() {
    const state = await this.getState();
    if (!state?.stack?.healthy) return '';
    const image = await this.getComposeServiceImage('fastapi');
    const runningVersion = serverVersionFromImage(image);
    if (!runningVersion) return '';
    return this.recordSuccessfulServerVersion(runningVersion);
  }

  /** Add a migration-focused diagnosis only for a concrete version downgrade. */
  async possibleDatabaseDowngradeError(error, env = {}) {
    if (error?.code === 'POSSIBLE_DATABASE_DOWNGRADE') return error;
    const currentVersion = trackableServerVersion(env.OMLORIX_VERSION);
    const metadata = await this.readLauncherMetadata();
    const highestVersion = metadata.highestSuccessfulServerVersion;
    if (!currentVersion || !highestVersion || compareVersions(currentVersion, highestVersion) >= 0) {
      return error;
    }
    return new PossibleDatabaseDowngradeError({
      currentVersion,
      highestVersion,
      originalError: error,
    });
  }

  async envExampleMetadata() {
    const example = path.join(this.serverHome, '.env.example');
    const metadataFile = path.join(this.serverHome, 'electron', 'env-metadata.json');
    const raw = await fs.readFile(example, 'utf8').catch(() => '');
    const metadataRaw = await fs.readFile(metadataFile, 'utf8').catch(() => '{}');
    let metadata = {};
    try {
      metadata = normalizeEnvMetadata(JSON.parse(metadataRaw));
    } catch (error) {
      metadata = {};
    }
    return parseEnvExampleMetadata(raw, metadata);
  }

  async createEnvBackup(raw) {
    const backupDir = path.join(this.serverHome, '.env.backups');
    const stamp = new Date().toISOString()
      .replace(/[-:]/g, '')
      .replace('T', '-')
      .replace(/\.(\d{3})Z$/, '-$1');
    const uniqueSuffix = crypto.randomUUID().slice(0, 8);
    const backupFile = path.join(backupDir, `.env.${stamp}-${uniqueSuffix}.bak`);
    await fs.mkdir(backupDir, { recursive: true });
    await fs.writeFile(backupFile, raw, { encoding: 'utf8', mode: 0o600 });
    return backupFile;
  }

  async getEnvEditor() {
    await this.ensureServerHome();

    // Ensure toggle defaults are applied before computing required keys.
    await this.ensureGeneratedSecrets();

    const raw = await fs.readFile(this.envFile, 'utf8').catch(() => '');
    const env = parseEnv(raw);
    const toggles = readEnvToggles(env);
    const requiredKeys = new Set(requiredEnvKeysForToggles(toggles));
    const metadata = await this.envExampleMetadata();
    const fieldKeys = [];
    const seen = new Set();

    for (const field of metadata.fields) {
      if (launcherEnvKeyIsHidden(field.key)) continue;
      if (SETTINGS_OWNED_ENV_KEYS.has(field.key)) continue;
      fieldKeys.push(field.key);
      seen.add(field.key);
    }
    for (const key of requiredKeys) {
      if (launcherEnvKeyIsHidden(key)) continue;
      if (SETTINGS_OWNED_ENV_KEYS.has(key)) continue;
      if (!seen.has(key)) {
        fieldKeys.push(key);
        seen.add(key);
      }
    }
    for (const key of Object.keys(env).sort()) {
      if (launcherEnvKeyIsHidden(key)) continue;
      if (SETTINGS_OWNED_ENV_KEYS.has(key)) continue;
      if (!seen.has(key)) fieldKeys.push(key);
    }

    const fields = fieldKeys.map((key) => {
      const field = metadata.byKey.get(key) || {
        key,
        section: requiredKeys.has(key) ? 'Required' : 'Custom',
        sectionKey: requiredKeys.has(key) ? 'launcher_ui_required' : 'launcher_ui_custom',
        defaultValue: '',
        description: requiredKeys.has(key)
          ? 'Required for the active deployment configuration.'
          : 'Custom variable from the current .env file.',
        descriptionKey: requiredKeys.has(key)
          ? 'launcher_ui_env_description_required_active'
          : 'launcher_ui_env_description_custom_variable',
        label: humanizeEnvKey(key),
        type: inferEnvType(key, env[key]),
        secret: isSecretEnvKey(key),
        required: requiredKeys.has(key),
        options: ENV_ENUM_OPTIONS[key] || [],
      };
      const fieldRequired = requiredKeys.has(key);
      const present = Object.prototype.hasOwnProperty.call(env, key);
      const currentValue = present ? String(env[key] ?? '') : '';
      return {
        ...field,
        required: fieldRequired,
        value: currentValue,
        present,
        isSet: envValueIsFilled(currentValue),
        placeholder: String(field.defaultValue ?? ''),
        known: metadata.byKey.has(key),
      };
    });

    const groups = Array.from(new Set(fields.map((field) => field.section)));
    return {
      envFile: this.envFile,
      groups,
      fields,
    };
  }

  buildEnvImportPreview({
    importId,
    sourceFile,
    parsed,
    currentEnv,
    metadata,
    replaceMissing = false,
  }) {
    // Replacement mode models omitted known variables with their documented
    // defaults and drops omitted custom variables. Launcher-owned hidden keys
    // remain protected and are restored during commit.
    const defaultEnv = Object.fromEntries(
      metadata.fields
        .filter((field) => !launcherEnvKeyIsHidden(field.key))
        .map((field) => [field.key, String(field.defaultValue ?? '')]),
    );
    const protectedEnv = Object.fromEntries(
      Object.entries(currentEnv).filter(([key]) => launcherEnvKeyIsHidden(key)),
    );
    const replacementValues = Object.fromEntries(
      Object.entries(parsed.values).filter(([key]) => !launcherEnvKeyIsHidden(key)),
    );
    // Launcher-owned values are never part of an operator import. Apply the
    // same filtered projection in merge and replacement mode so the preview,
    // validation, and eventual commit all enforce one security boundary.
    const nextEnv = replaceMissing
      ? { ...defaultEnv, ...protectedEnv, ...replacementValues }
      : { ...currentEnv, ...replacementValues };
    const toggles = readEnvToggles(nextEnv);
    const requiredKeys = new Set(requiredEnvKeysForToggles(toggles));
    const validationErrors = {};
    const importedKeys = parsed.keys.filter((key) => !launcherEnvKeyIsHidden(key));
    const knownKeys = [];
    const customKeys = [];
    const changedKeys = [];
    const unchangedKeys = [];
    const newKeys = [];

    for (const key of importedKeys) {
      const value = String(parsed.values[key] ?? '');
      const field = metadata.byKey.get(key) || {
        key,
        type: inferEnvType(key, value),
        secret: isSecretEnvKey(key),
        required: requiredKeys.has(key),
        options: ENV_ENUM_OPTIONS[key] || [],
      };
      field.required = requiredKeys.has(key);
      const error = validateEnvValue(key, value, field);
      if (error) validationErrors[key] = error;

      if (metadata.byKey.has(key)) {
        knownKeys.push(key);
      } else {
        customKeys.push(key);
      }

      if (!Object.prototype.hasOwnProperty.call(currentEnv, key)) {
        newKeys.push(key);
        changedKeys.push(key);
      } else if (String(currentEnv[key] ?? '') !== value) {
        changedKeys.push(key);
      } else {
        unchangedKeys.push(key);
      }
    }

    const requirementStatus = buildEnvRequirementStatus(nextEnv);
    for (const issue of requirementStatus.issues) {
      if (replaceMissing || Object.prototype.hasOwnProperty.call(replacementValues, issue.key)) {
        validationErrors[issue.key] = issue.message;
      }
    }
    const missingRequiredKeys = requirementStatus.issues
      .map((issue) => issue.key)
      .filter((key) => !Object.prototype.hasOwnProperty.call(replacementValues, key));

    const missingKnownCount = metadata.fields
      .filter((field) => !launcherEnvKeyIsHidden(field.key))
      .filter((field) => !Object.prototype.hasOwnProperty.call(replacementValues, field.key))
      .length;
    const resetKnownKeys = replaceMissing
      ? metadata.fields
        .filter((field) => !launcherEnvKeyIsHidden(field.key))
        .filter((field) => !Object.prototype.hasOwnProperty.call(replacementValues, field.key))
        .filter((field) => (
          String(currentEnv[field.key] ?? field.defaultValue ?? '')
          !== String(field.defaultValue ?? '')
        ))
        .map((field) => field.key)
      : [];
    const removedCustomKeys = replaceMissing
      ? Object.keys(currentEnv)
        .filter((key) => !launcherEnvKeyIsHidden(key))
        .filter((key) => !metadata.byKey.has(key))
        .filter((key) => !Object.prototype.hasOwnProperty.call(replacementValues, key))
      : [];
    const replacementChanges = replaceMissing
      ? [...resetKnownKeys, ...removedCustomKeys]
      : [];
    const allChangedKeys = [...new Set([...changedKeys, ...replacementChanges])];

    return {
      importId,
      sourceFile,
      replaceMissing,
      importedCount: importedKeys.length,
      changedCount: allChangedKeys.length,
      unchangedCount: unchangedKeys.length,
      newCount: newKeys.length,
      knownCount: knownKeys.length,
      customCount: customKeys.length,
      missingKnownCount,
      duplicateKeys: parsed.duplicateKeys,
      invalidLines: parsed.invalidLines,
      validationErrors,
      missingRequiredKeys,
      changedKeys: allChangedKeys.slice(0, 24),
      customKeys: customKeys.slice(0, 24),
      newKeys: newKeys.slice(0, 24),
      resetKnownCount: resetKnownKeys.length,
      resetKnownKeys: resetKnownKeys.slice(0, 24),
      removedCustomCount: removedCustomKeys.length,
      removedCustomKeys: removedCustomKeys.slice(0, 24),
    };
  }

  async previewEnvImport(sourceFile) {
    await this.ensureServerHome();
    const filePath = String(sourceFile || '');
    if (!filePath) {
      throw new Error('Choose a .env file to import.');
    }
    const stat = await fs.stat(filePath);
    if (!stat.isFile()) {
      throw new Error('Choose a regular .env file.');
    }
    if (stat.size > 1024 * 1024) {
      throw new Error('The selected .env file is larger than 1 MB.');
    }

    const raw = await fs.readFile(filePath, 'utf8');
    const importableRaw = removeEnvKeysFromContent(raw, [...RETIRED_ENV_KEYS]);
    const parsedInput = parseEnvDetailed(importableRaw);
    const currentRaw = await fs.readFile(this.envFile, 'utf8').catch(() => '');
    const currentEnv = parseEnv(currentRaw);
    const parsed = {
      ...parsedInput,
      values: { ...parsedInput.values },
      keys: parsedInput.keys.filter((key) => key !== 'OMLORIX_HIGHEST_VERSION_USED'),
      duplicateKeys: parsedInput.duplicateKeys.filter(
        (key) => key !== 'OMLORIX_HIGHEST_VERSION_USED',
      ),
    };
    // Launcher history is never imported as deployment configuration. Legacy
    // files remain importable, but this obsolete key is ignored entirely.
    delete parsed.values.OMLORIX_HIGHEST_VERSION_USED;
    if (!parsed.keys.length) {
      throw new Error('The selected file does not contain any environment variables.');
    }
    const metadata = await this.envExampleMetadata();
    const importId = crypto.randomUUID();
    const preview = this.buildEnvImportPreview({
      importId,
      sourceFile: filePath,
      parsed,
      currentEnv,
      metadata,
    });
    preview.replacement = this.buildEnvImportPreview({
      importId,
      sourceFile: filePath,
      parsed,
      currentEnv,
      metadata,
      replaceMissing: true,
    });

    this.pendingEnvImports.set(importId, {
      values: parsed.values,
      raw: importableRaw,
      sourceFile: filePath,
    });
    for (const pendingImportId of this.pendingEnvImports.keys()) {
      if (pendingImportId !== importId) {
        this.pendingEnvImports.delete(pendingImportId);
      }
    }
    return preview;
  }

  discardEnvImport(importId) {
    this.pendingEnvImports.delete(String(importId || ''));
    return { ok: true };
  }

  async applyEnvImport(importId, options = {}) {
    return this.withSharedOperationLock(
      'config import',
      () => this.applyEnvImportUnlocked(importId, options),
    );
  }

  async applyEnvImportUnlocked(importId, options = {}) {
    await this.ensureServerHome();
    const pending = this.pendingEnvImports.get(String(importId || ''));
    if (!pending) {
      throw new Error('The selected .env import is no longer available. Choose the file again.');
    }

    const raw = await fs.readFile(this.envFile, 'utf8').catch(() => '');
    const metadata = await this.envExampleMetadata();
    const currentEnv = parseEnv(raw);
    const parsed = {
      values: { ...pending.values },
      keys: Object.keys(pending.values),
      duplicateKeys: [],
      invalidLines: [],
    };
    const basePreview = this.buildEnvImportPreview({
      importId,
      sourceFile: pending.sourceFile,
      parsed,
      currentEnv,
      metadata,
    });
    const replacementPreview = this.buildEnvImportPreview({
      importId,
      sourceFile: pending.sourceFile,
      parsed,
      currentEnv,
      metadata,
      replaceMissing: true,
    });
    basePreview.replacement = replacementPreview;
    const replaceMissing = options?.replaceMissing === true;
    const preview = replaceMissing ? replacementPreview : basePreview;
    if (Object.keys(preview.validationErrors).length) {
      return { ok: false, preview: basePreview };
    }

    const defaultEnv = Object.fromEntries(
      metadata.fields
        .filter((field) => !launcherEnvKeyIsHidden(field.key))
        .map((field) => [field.key, String(field.defaultValue ?? '')]),
    );
    const protectedEnv = Object.fromEntries(
      Object.entries(currentEnv).filter(([key]) => launcherEnvKeyIsHidden(key)),
    );
    const replacementValues = Object.fromEntries(
      Object.entries(parsed.values).filter(([key]) => !launcherEnvKeyIsHidden(key)),
    );
    const mergedEnv = replaceMissing
      ? { ...defaultEnv, ...protectedEnv, ...replacementValues }
      : { ...currentEnv, ...replacementValues };
    const launcherProxyEnabled = (await this.readServerSettings()).proxy.enabled;
    const externalProxyEnabled = Boolean(
      String(mergedEnv.FRONTEND_TRUSTED_UPSTREAMS || '').trim(),
    );
    const launcherOwnedUpdates = {
      // Imported values must not decide whether the Docker frontend trusts the
      // launcher proxy's forwarded headers.
      FRONTEND_TRUST_PROXY_HEADERS: String(launcherProxyEnabled || externalProxyEnabled),
    };
    if (launcherProxyEnabled) {
      // Keep direct clients from bypassing the launcher's header sanitization.
      launcherOwnedUpdates.FRONTEND_HTTP_HOST_BIND = '127.0.0.1';
    }
    // Complete replacement starts from the selected file itself. Remove
    // launcher-owned hidden values from that source and restore their current
    // trusted values so an ordinary config import cannot replace installation
    // identity, proxy authentication, or derived trust settings.
    const importedRaw = replaceMissing
      ? updateEnvContent(
        removeEnvKeysFromContent(
          String(pending.raw || ''),
          ['OMLORIX_HIGHEST_VERSION_USED', ...LAUNCHER_HIDDEN_ENV_KEYS],
        ),
        protectedEnv,
      )
      : updateEnvContent(raw, replacementValues);
    const nextRaw = `${updateEnvContent(importedRaw, launcherOwnedUpdates)}\n`;
    const changed = nextRaw.trimEnd() !== raw.trimEnd();

    if (changed) {
      // A reviewed Environment import is a file operation, not a lifecycle
      // operation. Commit the validated projection directly and leave every
      // running container and proxy untouched until the operator explicitly
      // chooses Restart. writeEnvContent remains atomic and refreshes the
      // configured recovery copy, but it does not create an extra timestamped
      // pre-import backup.
      await this.writeEnvContent(nextRaw);
    }
    this.pendingEnvImports.delete(importId);

    return {
      ok: true,
      state: await this.getState(),
      editor: await this.getEnvEditor(),
      changed,
      restartRequired: changed,
      replaceMissing,
      changedKeys: preview.changedKeys,
      importedCount: preview.importedCount,
      customCount: preview.customCount,
      newCount: preview.newCount,
    };
  }

  async saveEnvEditor(payload) {
    return this.withSharedOperationLock(
      'config edit',
      () => this.saveEnvEditorUnlocked(payload),
    );
  }

  async saveEnvEditorUnlocked(payload) {
    await this.ensureServerHome();
    const raw = await fs.readFile(this.envFile, 'utf8').catch(() => '');
    const metadata = await this.envExampleMetadata();
    const env = parseEnv(raw);
    const toggles = readEnvToggles(env);
    const requiredKeys = new Set(requiredEnvKeysForToggles(toggles));
    const values = payload?.values && typeof payload.values === 'object' && !Array.isArray(payload.values)
      ? payload.values
      : {};
    const clearSecrets = new Set(Array.isArray(payload?.clearSecrets) ? payload.clearSecrets : []);
    const removeKeys = Array.isArray(payload?.removeKeys)
      ? payload.removeKeys.map((key) => String(key || '').trim()).filter(Boolean)
      : [];
    const updates = {};
    const errors = {};
    const customRemoveKeys = [];

    for (const key of removeKeys) {
      if (launcherEnvKeyIsHidden(key) || SETTINGS_OWNED_ENV_KEYS.has(key) || metadata.byKey.has(key)) continue;
      const error = validateEnvValue(key, '', { key, required: false, type: inferEnvType(key, '') });
      if (error) {
        errors[key] = error;
        continue;
      }
      customRemoveKeys.push(key);
    }

    for (const [key, rawValue] of Object.entries(values)) {
      if (customRemoveKeys.includes(key)) continue;
      if (launcherEnvKeyIsHidden(key)) continue;
      const field = metadata.byKey.get(key) || {
        key,
        type: inferEnvType(key, rawValue),
        secret: isSecretEnvKey(key),
        required: requiredKeys.has(key),
        options: ENV_ENUM_OPTIONS[key] || [],
      };
      field.required = requiredKeys.has(key);
      const value = String(rawValue ?? '');
      if (field.secret && !value && !clearSecrets.has(key)) {
        continue;
      }
      const error = validateEnvValue(key, value, field);
      if (error) {
        errors[key] = error;
        continue;
      }
      updates[key] = value;
    }

    for (const key of clearSecrets) {
      if (launcherEnvKeyIsHidden(key)) continue;
      const field = metadata.byKey.get(key) || {
        key,
        type: inferEnvType(key, ''),
        secret: isSecretEnvKey(key),
        required: requiredKeys.has(key),
        options: ENV_ENUM_OPTIONS[key] || [],
      };
      field.required = requiredKeys.has(key);
      const error = validateEnvValue(key, '', field);
      if (error) {
        errors[key] = error;
        continue;
      }
      updates[key] = '';
    }

    if (Object.keys(errors).length) {
      const error = new Error('Env validation failed.');
      error.validationErrors = errors;
      throw error;
    }

    if (!Object.keys(updates).length && !customRemoveKeys.length) {
      return {
        state: await this.getState(),
        editor: await this.getEnvEditor(),
        backupFile: '',
        changed: false,
        changedKeys: [],
      };
    }

    const updatedRaw = updateEnvContent(raw, updates);
    const nextRaw = `${removeEnvKeysFromContent(updatedRaw, customRemoveKeys)}\n`;
    // Validate before creating the rollback copy so a rejected edit has no file
    // side effects. writeEnvContent repeats this check as the shared last guard.
    assertIndependentSecuritySecrets(parseEnv(nextRaw));
    let backupFile = '';
    if (nextRaw.trimEnd() !== raw.trimEnd()) {
      backupFile = await this.createEnvBackup(raw);
      await this.writeEnvContent(nextRaw);
    }

    return {
      state: await this.getState(),
      editor: await this.getEnvEditor(),
      backupFile,
      changed: Boolean(backupFile),
      changedKeys: [...Object.keys(updates), ...customRemoveKeys],
    };
  }

  async ensureGeneratedSecrets() {
    await this.ensureServerHome();
    const raw = await fs.readFile(this.envFile, 'utf8').catch(() => '');
    const env = parseEnv(raw);
    const updates = {};

    if (!env.COMPOSE_PROJECT_NAME) {
      updates.COMPOSE_PROJECT_NAME = composeProjectNameForHome(this.serverHome);
    }
    if (!env.OMLORIX_INSTALLATION_ID || env.OMLORIX_INSTALLATION_ID === 'CHANGE_ME') {
      updates.OMLORIX_INSTALLATION_ID = crypto.randomBytes(32).toString('hex');
    }
    if (!env.FRONTEND_HTTP_HOST_BIND) updates.FRONTEND_HTTP_HOST_BIND = '127.0.0.1';

    // Set default toggles if not present so subsequent logic sees the correct state
    if (!env.OMLORIX_USE_BUNDLED_DB) updates.OMLORIX_USE_BUNDLED_DB = 'true';
    if (!env.OMLORIX_USE_BUNDLED_REDIS) updates.OMLORIX_USE_BUNDLED_REDIS = 'true';
    if (!env.OMLORIX_USE_PGBOUNCER) updates.OMLORIX_USE_PGBOUNCER = 'false';
    if (!env.OMLORIX_USE_BUNDLED_STORAGE) updates.OMLORIX_USE_BUNDLED_STORAGE = 'false';

    // Recompute toggles after applying defaults so conditional logic is correct.
    const effectiveEnv = { ...env, ...updates };
    const toggles = readEnvToggles(effectiveEnv);

    // Repair homes where enabling PgBouncer only started the container and
    // left application traffic pointed directly at PostgreSQL.
    for (const [key, value] of Object.entries(topologyInvariantUpdates(effectiveEnv))) {
      if (String(effectiveEnv[key] ?? '') !== value) updates[key] = value;
    }

    // Repair configurations saved by older launchers that treated the bundled
    // service switch and active file provider as unrelated settings.
    if (toggles.useBundledStorage
      && String(effectiveEnv.FILE_STORAGE_PROVIDER || '').trim().toLowerCase() !== 's3') {
      updates.FILE_STORAGE_PROVIDER = 's3';
    }

    if (!env.JWT_SECRET_KEY) updates.JWT_SECRET_KEY = randomJwtSecret();
    if (!env.ENCRYPTION_KEY) updates.ENCRYPTION_KEY = randomFernetKey();
    // Early launcher versions accepted short password-reset salts. Current
    // settings validation requires at least 16 characters, so leaving one of
    // those legacy values in place makes every full settings autosave fail even
    // when the operator edits an unrelated field such as COMPOSE_PROJECT_NAME.
    // Replace only missing/invalid legacy values; valid installations retain
    // their existing salt and therefore keep stable reset-token identifiers.
    if (String(env.PASSWORD_RESET_IDENTIFIER_HASH_SALT || '').length < 16) {
      updates.PASSWORD_RESET_IDENTIFIER_HASH_SALT = crypto.randomBytes(32).toString('hex');
    }
    if (String(env.LOG_IP_HASH_SALT || '').length < 16) {
      updates.LOG_IP_HASH_SALT = crypto.randomBytes(32).toString('hex');
    }
    if (!env.BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE) {
      updates.BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE = randomSecret(36);
    }
    // A fixed-length hex credential is safe to embed in the generated nginx
    // map and authenticates the host proxy without trusting Docker NAT peers.
    if (!/^[0-9a-f]{64}$/i.test(String(env.OMLORIX_LAUNCHER_PROXY_SECRET || ''))) {
      updates.OMLORIX_LAUNCHER_PROXY_SECRET = crypto.randomBytes(32).toString('hex');
    }
    if (!env.DATABASE_PASSWORD || env.DATABASE_PASSWORD === 'CHANGE_ME') updates.DATABASE_PASSWORD = randomSecret();
    if (!env.REDIS_PASSWORD || env.REDIS_PASSWORD === 'CHANGE_ME') updates.REDIS_PASSWORD = randomUrlSecret();
    const redisPassword = updates.REDIS_PASSWORD || env.REDIS_PASSWORD || '';
    if (toggles.redisEnabled && toggles.useBundledRedis && redisPassword) {
      const expectedRedisUrl = defaultLocalRedisUrl(env, redisPassword);
      if (String(env.REDIS_URL || '').trim() !== expectedRedisUrl) {
        updates.REDIS_URL = expectedRedisUrl;
      }
    }
    if (shouldResetGrafanaAdminUser(env.GRAFANA_ADMIN_USER)) updates.GRAFANA_ADMIN_USER = defaultGrafanaAdminUser();
    if (!env.GRAFANA_ADMIN_PASSWORD || env.GRAFANA_ADMIN_PASSWORD === 'CHANGE_ME') updates.GRAFANA_ADMIN_PASSWORD = randomSecret();

    if (!env.FRONTEND_HTTP_HOST_PORT || env.FRONTEND_HTTP_HOST_PORT === '80') updates.FRONTEND_HTTP_HOST_PORT = '8080';
    if (!env.OMLORIX_VERSION) updates.OMLORIX_VERSION = 'stable';
    if (toggles.useBundledStorage) {
      if (!env.MINIO_ROOT_USER || env.MINIO_ROOT_USER === 'CHANGE_ME') updates.MINIO_ROOT_USER = `omlorix-${crypto.randomBytes(18).toString('base64url')}`;
      if (!env.MINIO_ROOT_PASSWORD || env.MINIO_ROOT_PASSWORD === 'CHANGE_ME') updates.MINIO_ROOT_PASSWORD = randomSecret();
    }
    if (Object.keys(updates).length) {
      await this.writeEnvContent(`${updateEnvContent(raw, updates)}\n`);
    }
  }

  /**
   * Backfill only the authenticated-ingress credential on existing installs.
   * Restart should not regenerate unrelated application secrets, while older
   * proxy-enabled installations still need the credential before nginx starts.
   */
  async ensureIngressAuthenticationCredential() {
    const env = await this.readEnv();
    const ingressEnabled = envTruthy(env.OMLORIX_LAUNCHER_PROXY_ENABLED)
      || Boolean(String(env.FRONTEND_TRUSTED_UPSTREAMS || '').trim());
    if (!ingressEnabled || /^[0-9a-f]{64}$/i.test(String(env.OMLORIX_LAUNCHER_PROXY_SECRET || ''))) {
      return false;
    }
    await this.writeEnv({
      OMLORIX_LAUNCHER_PROXY_SECRET: crypto.randomBytes(32).toString('hex'),
    });
    return true;
  }

  async repairBundledRedisUrl() {
    await this.ensureServerHome();
    const raw = await fs.readFile(this.envFile, 'utf8').catch(() => '');
    const env = parseEnv(raw);
    const toggles = readEnvToggles(env);
    const redisPassword = env.REDIS_PASSWORD || '';

    const expectedRedisUrl = defaultLocalRedisUrl(env, redisPassword);
    if (
      !toggles.redisEnabled
      || !toggles.useBundledRedis
      || !redisPassword
      || String(env.REDIS_URL || '').trim() === expectedRedisUrl
    ) {
      return false;
    }

    await this.writeEnvContent(`${updateEnvContent(raw, {
      REDIS_URL: expectedRedisUrl,
    })}\n`);
    return true;
  }

  async validateProfileEnv() {
    await this.readServerSettings();
    const env = await this.readEnv();
    const toggles = readEnvToggles(env);
    if (toggles.usePgbouncer) {
      const poolMode = String(env.PGBOUNCER_POOL_MODE || 'transaction').trim().toLowerCase();
      if (!ENV_ENUM_OPTIONS.PGBOUNCER_POOL_MODE.includes(poolMode)) {
        throw new Error('Choose one of: transaction, session.');
      }
    }
    const requirements = buildEnvRequirementStatus(env);
    if (!requirements.ok) {
      throw new EnvRequirementsError(requirements, this.envFile);
    }

    if (!toggles.useBundledDB && (!toggles.redisEnabled || !toggles.useBundledRedis)) {
      const missing = [];
      if (!env.DATABASE_URL) missing.push('DATABASE_URL');
      if (toggles.redisEnabled && !env.REDIS_URL) missing.push('REDIS_URL');
      const provider = String(env.FILE_STORAGE_PROVIDER || '').trim();
      if (!provider || provider === 'local') {
        missing.push('FILE_STORAGE_PROVIDER=s3|gcs|azure|webdav');
      }
      if (missing.length) {
        throw new Error(`Managed Cloud requires ${missing.join(', ')} in ${this.envFile}.`);
      }
    }

    const mode = String(env.MODE || '').trim().toLowerCase();
    if (mode === 'dev') {
      return;
    }

    if (!env.JWT_SECRET_KEY || jwtSecretByteLength(env.JWT_SECRET_KEY) < 64) {
      throw new Error(`JWT_SECRET_KEY must be set and at least 64 bytes long in ${this.envFile}.`);
    }
    if (!env.LOG_IP_HASH_SALT || String(env.LOG_IP_HASH_SALT).trim().length < 16) {
      throw new Error(`LOG_IP_HASH_SALT must be set and at least 16 characters long in ${this.envFile}.`);
    }
    if (!env.ENCRYPTION_KEY) {
      throw new Error(`ENCRYPTION_KEY must be set in ${this.envFile}.`);
    }
    if (!toggles.useBundledDB) {
      if (!env.DATABASE_URL || String(env.DATABASE_URL).includes('CHANGE_ME')) {
        throw new Error(`DATABASE_URL must be set and must not contain CHANGE_ME in ${this.envFile}.`);
      }
    } else {
      if (!env.DATABASE_PASSWORD || env.DATABASE_PASSWORD === 'CHANGE_ME') {
        throw new Error(`DATABASE_PASSWORD must be set to a non-placeholder value in ${this.envFile}.`);
      }
    }
    if (toggles.redisEnabled) {
      if (!toggles.useBundledRedis) {
        if (!env.REDIS_URL || String(env.REDIS_URL).includes('CHANGE_ME')) {
          throw new Error(`REDIS_URL must be set and must not contain CHANGE_ME in ${this.envFile}.`);
        }
        if (/(localhost|127\.0\.0\.1):/.test(String(env.REDIS_URL)) || String(env.REDIS_URL).trim() === 'redis://redis:6379/0') {
          throw new Error(`REDIS_URL must point to your external Redis service, not localhost or the bundled redis hostname, in ${this.envFile}.`);
        }
      } else if (!env.REDIS_PASSWORD || env.REDIS_PASSWORD === 'CHANGE_ME') {
        throw new Error(`REDIS_PASSWORD must be set to a non-placeholder value in ${this.envFile}.`);
      }
    }
  }

  async execDocker(args, options = {}) {
    return new Promise((resolve) => {
      const dockerExecutable = dockerCommand();
      const child = spawn(dockerExecutable, args, {
        cwd: this.serverHome,
        windowsHide: true,
        env: dockerSpawnEnv(dockerExecutable),
      });
      let stdout = '';
      let stderr = '';
      let timeout = null;
      if (options.timeoutMs) {
        timeout = setTimeout(() => {
          child.kill();
        }, options.timeoutMs);
      }
      child.stdout.on('data', (chunk) => {
        stdout += chunk.toString();
      });
      child.stderr.on('data', (chunk) => {
        stderr += chunk.toString();
      });
      child.on('close', (code) => {
        if (timeout) clearTimeout(timeout);
        resolve({ ok: code === 0, code, stdout, stderr });
      });
      child.on('error', (error) => {
        if (timeout) clearTimeout(timeout);
        resolve({ ok: false, code: -1, stdout, stderr: error.message });
      });
    });
  }

  /** Ensure the stable private network shared with launcher-managed helpers. */
  async ensureLauncherServicesNetwork() {
    await fs.mkdir(this.serverHome, { recursive: true });
    const inspect = await this.execDocker(
      ['network', 'inspect', LAUNCHER_SERVICES_NETWORK],
      { timeoutMs: 10000 },
    );
    if (inspect.ok) return { created: false, name: LAUNCHER_SERVICES_NETWORK };

    const create = await this.execDocker([
      'network',
      'create',
      '--label',
      'com.omlorix.launcher.managed=true',
      LAUNCHER_SERVICES_NETWORK,
    ], { timeoutMs: 10000 });
    if (!create.ok) {
      // A simultaneous helper start may have created the network between the
      // inspect and create calls. Confirm the final state before reporting an
      // error instead of surfacing Docker's race-dependent wording.
      const retry = await this.execDocker(
        ['network', 'inspect', LAUNCHER_SERVICES_NETWORK],
        { timeoutMs: 10000 },
      );
      if (!retry.ok) {
        throw new Error('Could not create the private launcher services network.');
      }
    }
    return { created: true, name: LAUNCHER_SERVICES_NETWORK };
  }

  /** Prepare Compose arguments, optionally without mutating Docker resources. */
  async prepareCompose(options = {}) {
    await this.ensureServerHome();
    if (!options.readOnly
      && fssync.existsSync(path.join(this.serverHome, 'docker-compose.launcher-services.yml'))) {
      await this.ensureLauncherServicesNetwork();
    }
    const env = await this.readEnv();
    // Dashboard polling is observational and remains available while an old
    // project awaits operator confirmation. Every mutating path validates
    // ownership before it can issue a lifecycle command.
    if (!options.readOnly) await this.validateComposeOwnership(env);
    return { env, args: composeArgs(this.serverHome, env) };
  }

  /** Refuse lifecycle work when a same-named project has another home identity. */
  async validateComposeOwnership(env = {}) {
    const project = String(env.COMPOSE_PROJECT_NAME || '').trim();
    const identity = String(env.OMLORIX_INSTALLATION_ID || '').trim();
    // Legacy test/development homes can predate identity metadata. Fresh
    // Launcher/CLI homes always receive it before any Compose mutation, and
    // identity-bearing installations are checked strictly below.
    if (!project || !identity) return;
    const containers = await this.execDocker([
      'ps', '-a', '--filter', `label=com.docker.compose.project=${project}`, '--format', '{{.ID}}',
    ], { timeoutMs: 15000 });
    if (!containers.ok) throw new Error('Could not verify Compose project ownership.');
    for (const containerId of String(containers.stdout || '').split(/\s+/).filter(Boolean)) {
      const inspected = await this.execDocker([
        'inspect', '--format', '{{index .Config.Labels "com.omlorix.installation.id"}}', containerId,
      ], { timeoutMs: 15000 });
      const actualIdentity = inspected.ok ? String(inspected.stdout || '').trim() : '';
      if (actualIdentity === identity) continue;

      const unlabeled = inspected.ok && (!actualIdentity || actualIdentity === 'unmanaged');
      if (unlabeled && envTruthy(env.OMLORIX_ALLOW_PROJECT_ADOPTION)) continue;
      if (unlabeled) throw new LegacyComposeAdoptionRequiredError(project);

      const error = new Error('This Compose project belongs to another Omlorix server home. Choose a unique project name.');
      error.messageKey = 'launcher_ui_compose_ownership_mismatch';
      throw error;
    }
  }

  /** Arm a one-time adoption only after every existing container is unlabeled. */
  async adoptLegacyComposeProject(project) {
    const env = await this.readEnv();
    const configuredProject = String(env.COMPOSE_PROJECT_NAME || '').trim();
    if (!configuredProject || configuredProject !== String(project || '').trim()) {
      throw new Error('The Compose project changed before adoption could be confirmed.');
    }
    const containers = await this.execDocker([
      'ps', '-a', '--filter', `label=com.docker.compose.project=${configuredProject}`, '--format', '{{.ID}}',
    ], { timeoutMs: 15000 });
    if (!containers.ok) throw new Error('Could not verify the legacy Compose project.');
    const containerIds = String(containers.stdout || '').split(/\s+/).filter(Boolean);
    if (!containerIds.length) throw new Error('The legacy Compose project no longer has containers to adopt.');

    for (const containerId of containerIds) {
      const inspected = await this.execDocker([
        'inspect', '--format', '{{index .Config.Labels "com.omlorix.installation.id"}}', containerId,
      ], { timeoutMs: 15000 });
      const actualIdentity = inspected.ok ? String(inspected.stdout || '').trim() : '';
      if (!inspected.ok || (actualIdentity && actualIdentity !== 'unmanaged')) {
        throw new Error('The Compose project contains resources owned by another Omlorix server home.');
      }
    }
    await this.writeEnv({ OMLORIX_ALLOW_PROJECT_ADOPTION: 'true' });
  }

  /** Close the adoption exception and verify recreated resources strictly. */
  async finalizeProjectAdoption() {
    const env = await this.readEnv();
    if (!envTruthy(env.OMLORIX_ALLOW_PROJECT_ADOPTION)) return;
    await this.validateComposeOwnership({ ...env, OMLORIX_ALLOW_PROJECT_ADOPTION: 'false' });
    await this.writeEnv({ OMLORIX_ALLOW_PROJECT_ADOPTION: 'false' });
  }

  /** Attach an already-running backend after helper management is first used. */
  async attachRunningBackendToLauncherServicesNetwork() {
    await this.ensureLauncherServicesNetwork();
    const env = await this.readEnv();
    const args = composeArgs(this.serverHome, env);
    const backend = await this.execDocker([...args, 'ps', '-q', 'fastapi'], { timeoutMs: 10000 });
    let containerId = backend.ok
      ? String(backend.stdout || '').trim().split(/\s+/).filter(Boolean)[0] || ''
      : '';

    // In source development, the launcher UI can point at an Omlorix stack
    // started directly from the checkout. Its Compose project can therefore
    // differ from the launcher's saved server project even though it owns the
    // configured frontend port. Resolve that one unambiguous stack by its
    // published frontend port, then attach the matching FastAPI service.
    if (!containerId) {
      containerId = await this.backendContainerForPublishedFrontend(env);
    }
    if (!containerId) return { attached: false, running: false };

    const connect = await this.execDocker(
      ['network', 'connect', LAUNCHER_SERVICES_NETWORK, containerId],
      { timeoutMs: 10000 },
    );
    if (!connect.ok && !/already exists|already connected|endpoint with name/i.test(connect.stderr || connect.stdout)) {
      throw new Error('Could not attach the running Omlorix backend to the launcher services network.');
    }
    return { attached: connect.ok, running: true };
  }

  /**
   * Find the FastAPI container behind the one Compose frontend publishing the
   * launcher's configured HTTP port. Ambiguous matches fail closed so helper
   * credentials are never made reachable from an unrelated Omlorix stack.
   */
  async backendContainerForPublishedFrontend(env = {}) {
    const parsedPort = Number.parseInt(String(env.FRONTEND_HTTP_HOST_PORT || '8080'), 10);
    if (!Number.isInteger(parsedPort) || parsedPort < 1 || parsedPort > 65535) return '';

    const frontend = await this.execDocker([
      'ps',
      '--filter', 'label=com.docker.compose.service=frontend',
      '--format', '{{.ID}}',
    ], { timeoutMs: 10000 });
    const frontendIds = frontend.ok
      ? [...new Set(String(frontend.stdout || '').trim().split(/\s+/).filter(Boolean))]
      : [];
    const matchingFrontendIds = [];
    for (const frontendId of frontendIds) {
      const ports = await this.execDocker([
        'inspect',
        '--format', '{{json .NetworkSettings.Ports}}',
        frontendId,
      ], { timeoutMs: 10000 });
      if (!ports.ok) continue;
      try {
        const publishedPorts = JSON.parse(String(ports.stdout || '').trim());
        const hasHostBinding = Object.values(publishedPorts || {}).some((bindings) => (
          Array.isArray(bindings)
          && bindings.some((binding) => String(binding?.HostPort || '') === String(parsedPort))
        ));
        if (hasHostBinding) matchingFrontendIds.push(frontendId);
      } catch (_error) {
        // Ignore containers whose port inspection is unavailable or malformed;
        // the unambiguous-match rule below keeps the fallback fail-closed.
      }
    }
    if (matchingFrontendIds.length !== 1) return '';

    const project = await this.execDocker([
      'inspect',
      '--format', '{{ index .Config.Labels "com.docker.compose.project" }}',
      matchingFrontendIds[0],
    ], { timeoutMs: 10000 });
    const projectName = project.ok ? String(project.stdout || '').trim() : '';
    if (!projectName) return '';

    const backend = await this.execDocker([
      'ps',
      '--filter', `label=com.docker.compose.project=${projectName}`,
      '--filter', 'label=com.docker.compose.service=fastapi',
      '--format', '{{.ID}}',
    ], { timeoutMs: 10000 });
    const backendIds = backend.ok
      ? [...new Set(String(backend.stdout || '').trim().split(/\s+/).filter(Boolean))]
      : [];
    return backendIds.length === 1 ? backendIds[0] : '';
  }

  async getComposeServiceIp(serviceName) {
    const { args } = await this.prepareCompose();
    const ps = await this.execDocker([...args, 'ps', '-q', serviceName], { timeoutMs: 10000 });
    const containerId = String(ps.stdout || '').trim().split(/\s+/).filter(Boolean)[0];
    if (!ps.ok || !containerId) {
      return '';
    }

    const inspect = await this.execDocker(
      ['inspect', '--format', '{{json .NetworkSettings.Networks}}', containerId],
      { timeoutMs: 10000 },
    );
    if (!inspect.ok) {
      return '';
    }

    // A service can join helper or observability networks. Select the named
    // Omlorix Compose network instead of trusting Docker's object iteration
    // order, which is not a topology contract.
    try {
      const networks = JSON.parse(String(inspect.stdout || '').trim());
      const env = await this.readEnv();
      const expectedName = `${String(env.COMPOSE_PROJECT_NAME || 'omlorix').trim()}_omlorix-network`;
      const endpoint = networks?.[expectedName];
      const address = String(endpoint?.IPAddress || '').trim();
      return net.isIP(address) ? address : '';
    } catch (_error) {
      return '';
    }
  }

  /** Return the image reference used by a running Compose service container. */
  async getComposeServiceImage(serviceName) {
    const { args } = await this.prepareCompose();
    const ps = await this.execDocker([...args, 'ps', '-q', serviceName], { timeoutMs: 10000 });
    const containerId = String(ps.stdout || '').trim().split(/\s+/).filter(Boolean)[0];
    if (!ps.ok || !containerId) return '';

    const inspect = await this.execDocker(
      ['inspect', '--format', '{{.Config.Image}}', containerId],
      { timeoutMs: 10000 },
    );
    return inspect.ok ? String(inspect.stdout || '').trim() : '';
  }

  /**
   * Compare saved proxy trust settings with the environment loaded by a live
   * backend container. Docker does not update Config.Env when .env is edited,
   * so a mismatch means Omlorix must be recreated before visitor IPs are ready.
   */
  async getBackendProxyTrustRuntime(containerId, desiredEnv) {
    const unknown = {
      known: false,
      configured: false,
      matchesDesired: false,
    };
    if (!containerId) return unknown;

    const inspect = await this.execDocker(
      ['inspect', '--format', '{{json .Config.Env}}', containerId],
      { timeoutMs: 10000 },
    );
    if (!inspect.ok) return unknown;

    const runtimeEnv = selectBackendProxyEnvironment(inspect.stdout);
    if (!runtimeEnv) return unknown;

    return {
      known: true,
      configured: proxyTrustConfigured(runtimeEnv),
      // Compare aliases independently as well as canonical keys. This catches
      // stale legacy values without letting a canonical Compose default hide
      // an alias change that still affects backend trust resolution.
      matchesDesired: [...PROXY_BACKEND_ENV_INSPECT_KEYS].every(
        (key) => String(runtimeEnv[key] ?? '') === desiredBackendProxyEnvValue(desiredEnv, key),
      ),
    };
  }

  async dockerStatus() {
    const hasDocker = await this.execDocker(['--version'], { timeoutMs: 5000 });
    const desktopAppPath = dockerDesktopAppPath();
    const desktopInstalled = desktopAppPath ? fssync.existsSync(desktopAppPath) : false;
    if (!hasDocker.ok) {
      return {
        installed: desktopInstalled,
        running: false,
        compose: false,
        version: '',
        composeVersion: '',
        setupUrl: dockerSetupUrl(),
        canStartDesktop: desktopInstalled,
        message: desktopInstalled
          ? 'Docker Desktop is installed, but the docker command is not available on PATH yet.'
          : 'Docker is not installed or is not available on PATH.',
      };
    }

    const info = await this.execDocker(['info'], { timeoutMs: 8000 });
    const compose = await this.execDocker(['compose', 'version'], { timeoutMs: 8000 });
    const infoText = info.stdout || info.stderr || '';
    return {
      installed: true,
      running: info.ok,
      compose: compose.ok,
      version: hasDocker.stdout.trim() || hasDocker.stderr.trim(),
      composeVersion: compose.stdout.trim() || compose.stderr.trim(),
      vmNetworkingLikely: info.ok ? dockerVmNetworkingLikely(infoText) : false,
      setupUrl: dockerSetupUrl(),
      canStartDesktop: desktopInstalled,
      message: info.ok && compose.ok ? 'Docker is ready.' : 'Docker is installed, but Docker Desktop/Engine or Compose is not ready.',
    };
  }

  async openDockerSetup(shell) {
    await shell.openExternal(dockerSetupUrl());
    return { ok: true };
  }

  async startDockerDesktop(shell) {
    if (process.platform === 'darwin') {
      const result = await shell.openPath('/Applications/Docker.app');
      if (result) throw new Error(result);
      return { ok: true, message: 'Docker Desktop is starting.' };
    }

    if (process.platform === 'win32') {
      const appPath = dockerDesktopAppPath();
      if (!appPath || !fssync.existsSync(appPath)) {
        throw new Error('Docker Desktop is not installed in the default location.');
      }
      const result = await shell.openPath(appPath);
      if (result) throw new Error(result);
      return { ok: true, message: 'Docker Desktop is starting.' };
    }

    throw new Error('Starting Docker Desktop from the launcher is only available on macOS and Windows.');
  }

  publicEnv(env) {
    return {
      COMPOSE_PROJECT_NAME: env.COMPOSE_PROJECT_NAME || 'omlorix',
      MODE: env.MODE || 'production',
      OMLORIX_VERSION: env.OMLORIX_VERSION || 'stable',
      FRONTEND_HTTP_HOST_PORT: env.FRONTEND_HTTP_HOST_PORT || '8080',
      FRONTEND_HTTP_HOST_BIND: env.FRONTEND_HTTP_HOST_BIND || '127.0.0.1',
      FRONTEND_TRUST_PROXY_HEADERS: env.FRONTEND_TRUST_PROXY_HEADERS || 'false',
      FRONTEND_TRUSTED_UPSTREAMS: env.FRONTEND_TRUSTED_UPSTREAMS || '',
      DEV_DATABASE_HOST_PORT: env.DEV_DATABASE_HOST_PORT || '5432',
      DEV_REDIS_HOST_PORT: env.DEV_REDIS_HOST_PORT || '6379',
      API_LB_TRAEFIK_WEB_HOST_PORT: env.API_LB_TRAEFIK_WEB_HOST_PORT || '8080',
      API_LB_TRAEFIK_DASHBOARD_HOST_PORT: env.API_LB_TRAEFIK_DASHBOARD_HOST_PORT || '8081',
      TRUST_PROXY_HEADERS: env.TRUST_PROXY_HEADERS || 'false',
      OMLORIX_DISABLE_IP_RESTRICTIONS: env.OMLORIX_DISABLE_IP_RESTRICTIONS || 'false',
      TRUSTED_PROXIES: env.TRUSTED_PROXIES || '',
      TRUSTED_HOSTS: env.TRUSTED_HOSTS || '',
      RATE_LIMIT_TRUSTED_PROXIES: env.RATE_LIMIT_TRUSTED_PROXIES || '',
      AUTH_TRUSTED_PROXIES: env.AUTH_TRUSTED_PROXIES || '',
      RATE_LIMIT_PROXY_SETTINGS_CACHE_SECONDS: env.RATE_LIMIT_PROXY_SETTINGS_CACHE_SECONDS || '60',
      UVICORN_FORWARDED_ALLOW_IPS: env.UVICORN_FORWARDED_ALLOW_IPS || UVICORN_TRUSTED_PROXY_ENV_VALUE,
      BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE_SET: Boolean(env.BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE),
      BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE: env.BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE || '',
      JWT_SECRET_KEY: env.JWT_SECRET_KEY || '',
      ENCRYPTION_KEY: env.ENCRYPTION_KEY || '',
      PASSWORD_RESET_IDENTIFIER_HASH_SALT: env.PASSWORD_RESET_IDENTIFIER_HASH_SALT || '',
      LOG_IP_HASH_SALT: env.LOG_IP_HASH_SALT || '',
      DATABASE_URL: env.DATABASE_URL || '',
      DATABASE_USER: env.DATABASE_USER || 'postgres',
      DATABASE_PASSWORD: env.DATABASE_PASSWORD || '',
      DATABASE_HOST: env.DATABASE_HOST || 'localhost',
      DATABASE_PORT: env.DATABASE_PORT || '5432',
      DATABASE_NAME: env.DATABASE_NAME || 'omlorix',
      DATABASE_SCHEMA: env.DATABASE_SCHEMA || 'app',
      DATABASE_AUDIT_LOG_SCHEMA: env.DATABASE_AUDIT_LOG_SCHEMA || 'audit',
      DATABASE_LOGS_SCHEMA: env.DATABASE_LOGS_SCHEMA || 'logs',
      OMLORIX_AUTO_CREATE_DATABASES: env.OMLORIX_AUTO_CREATE_DATABASES || 'true',
      DATABASE_HOST_OVERRIDE: env.DATABASE_HOST_OVERRIDE || 'postgres',
      DATABASE_PORT_OVERRIDE: env.DATABASE_PORT_OVERRIDE || '5432',
      REDIS_ENABLED: env.REDIS_ENABLED || 'true',
      REDIS_PASSWORD: env.REDIS_PASSWORD || '',
      REDIS_URL: env.REDIS_URL || '',
      PGBOUNCER_HOST_BIND: env.PGBOUNCER_HOST_BIND || '127.0.0.1',
      PGBOUNCER_HOST_PORT: env.PGBOUNCER_HOST_PORT || '6432',
      PGBOUNCER_POOL_MODE: env.PGBOUNCER_POOL_MODE || 'transaction',
      PGBOUNCER_MAX_CLIENT_CONN: env.PGBOUNCER_MAX_CLIENT_CONN || '200',
      PGBOUNCER_DEFAULT_POOL_SIZE: env.PGBOUNCER_DEFAULT_POOL_SIZE || '40',
      PGBOUNCER_RESERVE_POOL_SIZE: env.PGBOUNCER_RESERVE_POOL_SIZE || '10',
      MINIO_ROOT_USER: env.MINIO_ROOT_USER || '',
      MINIO_ROOT_PASSWORD: env.MINIO_ROOT_PASSWORD || '',
      MINIO_API_HOST_BIND: env.MINIO_API_HOST_BIND || '127.0.0.1',
      MINIO_API_HOST_PORT: env.MINIO_API_HOST_PORT || '9000',
      MINIO_CONSOLE_HOST_BIND: env.MINIO_CONSOLE_HOST_BIND || '127.0.0.1',
      MINIO_CONSOLE_HOST_PORT: env.MINIO_CONSOLE_HOST_PORT || '9001',
      FILE_STORAGE_PROVIDER: env.FILE_STORAGE_PROVIDER || 'local',
      FILE_STORAGE_LOCAL_BASE_PATH: env.FILE_STORAGE_LOCAL_BASE_PATH || '/app/data/userFiles',
      FILE_STORAGE_S3_BUCKET: env.FILE_STORAGE_S3_BUCKET || '',
      FILE_STORAGE_S3_PREFIX: env.FILE_STORAGE_S3_PREFIX || '',
      FILE_STORAGE_S3_REGION: env.FILE_STORAGE_S3_REGION || '',
      FILE_STORAGE_S3_ENDPOINT_URL: env.FILE_STORAGE_S3_ENDPOINT_URL || '',
      FILE_STORAGE_S3_ACCESS_KEY_ID: env.FILE_STORAGE_S3_ACCESS_KEY_ID || '',
      FILE_STORAGE_S3_SECRET_ACCESS_KEY: env.FILE_STORAGE_S3_SECRET_ACCESS_KEY || '',
      FILE_STORAGE_S3_SESSION_TOKEN: env.FILE_STORAGE_S3_SESSION_TOKEN || '',
      FILE_STORAGE_GCS_BUCKET: env.FILE_STORAGE_GCS_BUCKET || '',
      FILE_STORAGE_GCS_PREFIX: env.FILE_STORAGE_GCS_PREFIX || '',
      FILE_STORAGE_GCS_PROJECT: env.FILE_STORAGE_GCS_PROJECT || '',
      FILE_STORAGE_GCS_CREDENTIALS_JSON: env.FILE_STORAGE_GCS_CREDENTIALS_JSON || '',
      FILE_STORAGE_AZURE_CONTAINER: env.FILE_STORAGE_AZURE_CONTAINER || '',
      FILE_STORAGE_AZURE_PREFIX: env.FILE_STORAGE_AZURE_PREFIX || '',
      FILE_STORAGE_AZURE_CONNECTION_STRING: env.FILE_STORAGE_AZURE_CONNECTION_STRING || '',
      FILE_STORAGE_AZURE_ACCOUNT_URL: env.FILE_STORAGE_AZURE_ACCOUNT_URL || '',
      FILE_STORAGE_AZURE_CREDENTIAL: env.FILE_STORAGE_AZURE_CREDENTIAL || '',
      FILE_STORAGE_WEBDAV_URL: env.FILE_STORAGE_WEBDAV_URL || '',
      FILE_STORAGE_WEBDAV_USERNAME: env.FILE_STORAGE_WEBDAV_USERNAME || '',
      FILE_STORAGE_WEBDAV_PASSWORD: env.FILE_STORAGE_WEBDAV_PASSWORD || '',
      FILE_STORAGE_WEBDAV_PREFIX: env.FILE_STORAGE_WEBDAV_PREFIX || '',
      FILE_STORAGE_WEBDAV_VERIFY_SSL: env.FILE_STORAGE_WEBDAV_VERIFY_SSL || 'true',
      FILE_STORAGE_WEBDAV_TIMEOUT: env.FILE_STORAGE_WEBDAV_TIMEOUT || '30',
      OTEL_ENABLED: env.OTEL_ENABLED || 'false',
      OTEL_SERVICE_NAME: env.OTEL_SERVICE_NAME || 'omlorix-backend',
      OTEL_EXPORTER_OTLP_ENDPOINT: env.OTEL_EXPORTER_OTLP_ENDPOINT || '',
      OTEL_EXPORTER_OTLP_INSECURE: env.OTEL_EXPORTER_OTLP_INSECURE || 'false',
      OTEL_TRACES_ENABLED: env.OTEL_TRACES_ENABLED || 'true',
      OTEL_TRACES_SAMPLER: env.OTEL_TRACES_SAMPLER || 'parentbased_traceidratio',
      OTEL_TRACES_SAMPLER_ARG: env.OTEL_TRACES_SAMPLER_ARG || '1.0',
      OTEL_METRICS_ENABLED: env.OTEL_METRICS_ENABLED || 'true',
      OTEL_PROMETHEUS_EXPORTER_ENABLED: env.OTEL_PROMETHEUS_EXPORTER_ENABLED || 'true',
      OTEL_LOGS_ENABLED: env.OTEL_LOGS_ENABLED || 'true',
      OTEL_INSTRUMENT_FASTAPI: env.OTEL_INSTRUMENT_FASTAPI || 'true',
      OTEL_INSTRUMENT_SQLALCHEMY: env.OTEL_INSTRUMENT_SQLALCHEMY || 'true',
      OTEL_INSTRUMENT_HTTP_CLIENTS: env.OTEL_INSTRUMENT_HTTP_CLIENTS || 'true',
      OTEL_SQL_COMMENTER_ENABLED: env.OTEL_SQL_COMMENTER_ENABLED || 'false',
      OTEL_CAPTURE_HTTP_ROUTE: env.OTEL_CAPTURE_HTTP_ROUTE || 'false',
      OTEL_CAPTURE_HTTP_USER_AGENT: env.OTEL_CAPTURE_HTTP_USER_AGENT || 'false',
      OTEL_HASH_HTTP_USER_AGENT: env.OTEL_HASH_HTTP_USER_AGENT || 'true',
      OTEL_GRPC_HOST_BIND: env.OTEL_GRPC_HOST_BIND || '127.0.0.1',
      OTEL_GRPC_HOST_PORT: env.OTEL_GRPC_HOST_PORT || '4317',
      OTEL_HTTP_HOST_BIND: env.OTEL_HTTP_HOST_BIND || '127.0.0.1',
      OTEL_HTTP_HOST_PORT: env.OTEL_HTTP_HOST_PORT || '4318',
      OTEL_PROMETHEUS_HOST_BIND: env.OTEL_PROMETHEUS_HOST_BIND || '127.0.0.1',
      OTEL_PROMETHEUS_HOST_PORT: env.OTEL_PROMETHEUS_HOST_PORT || '8889',
      OTEL_HEALTHCHECK_HOST_BIND: env.OTEL_HEALTHCHECK_HOST_BIND || '127.0.0.1',
      OTEL_HEALTHCHECK_HOST_PORT: env.OTEL_HEALTHCHECK_HOST_PORT || '13133',
      JAEGER_UI_HOST_BIND: env.JAEGER_UI_HOST_BIND || '127.0.0.1',
      JAEGER_UI_HOST_PORT: env.JAEGER_UI_HOST_PORT || '16686',
      JAEGER_COLLECTOR_HOST_BIND: env.JAEGER_COLLECTOR_HOST_BIND || '127.0.0.1',
      JAEGER_COLLECTOR_HOST_PORT: env.JAEGER_COLLECTOR_HOST_PORT || '14268',
      PROMETHEUS_HOST_BIND: env.PROMETHEUS_HOST_BIND || '127.0.0.1',
      PROMETHEUS_HOST_PORT: env.PROMETHEUS_HOST_PORT || '9090',
      ALERTMANAGER_HOST_BIND: env.ALERTMANAGER_HOST_BIND || '127.0.0.1',
      ALERTMANAGER_HOST_PORT: env.ALERTMANAGER_HOST_PORT || '9093',
      GRAFANA_HOST_BIND: env.GRAFANA_HOST_BIND || '127.0.0.1',
      GRAFANA_HOST_PORT: env.GRAFANA_HOST_PORT || '3001',
      GRAFANA_ADMIN_USER: env.GRAFANA_ADMIN_USER || '',
      GRAFANA_ADMIN_PASSWORD: env.GRAFANA_ADMIN_PASSWORD || '',
      GRAFANA_ROOT_URL: env.GRAFANA_ROOT_URL || '',
      POSTGRES_EXPORTER_DATA_SOURCE_URI: env.POSTGRES_EXPORTER_DATA_SOURCE_URI || '',
      POSTGRES_EXPORTER_DATA_SOURCE_USER: env.POSTGRES_EXPORTER_DATA_SOURCE_USER || '',
      POSTGRES_EXPORTER_DATA_SOURCE_PASS: env.POSTGRES_EXPORTER_DATA_SOURCE_PASS || '',
      REDIS_EXPORTER_ADDR: env.REDIS_EXPORTER_ADDR || '',
      OMLORIX_USE_BUNDLED_DB: env.OMLORIX_USE_BUNDLED_DB || 'true',
      OMLORIX_USE_BUNDLED_REDIS: env.OMLORIX_USE_BUNDLED_REDIS || 'true',
      OMLORIX_USE_PGBOUNCER: env.OMLORIX_USE_PGBOUNCER || 'false',
      OMLORIX_USE_BUNDLED_STORAGE: env.OMLORIX_USE_BUNDLED_STORAGE || 'false',
    };
  }

  async stackStatus(options = {}) {
    // Polling must observe Docker state without creating or repairing the
    // launcher-services network as a side effect.
    const { env, args } = await this.prepareCompose({ readOnly: true });
    // Resolve the configured service set independently from container state.
    // `ps --all` then supplies runtime details for running and stopped
    // containers, while the merge creates explicit rows for missing ones.
    const [config, ps] = await Promise.all([
      this.execDocker([...args, 'config', '--services'], { timeoutMs: 10000 }),
      this.execDocker([...args, 'ps', '--all', '--format', 'json'], { timeoutMs: 10000 }),
    ]);
    const configuredServices = config.ok
      ? parseComposeServiceNames(config.stdout)
      : expectedServiceNamesFromToggles(readEnvToggles(env));
    const summary = mergeExpectedComposeServices(
      configuredServices,
      ps.ok ? parseComposeJson(ps.stdout) : [],
      { expectedKnown: config.ok },
    );
    const { services } = summary;
    const backendService = services.find(
      (service) => service.Service === 'fastapi'
        && String(service.State || '').toLowerCase() === 'running',
    );
    const url = this.resolveUrl(env);
    const readyUrl = url ? `${url.replace(/\/$/, '')}/ready` : '';
    const health = readyUrl ? await requestUrl(readyUrl) : { ok: false, statusCode: null };
    const endpointReady = health.ok
      && Number(health.statusCode) >= 200
      && Number(health.statusCode) < 300;
    const includeDiagnostics = options.includeDiagnostics !== false;
    const backendProxyTrust = includeDiagnostics
      ? await this.getBackendProxyTrustRuntime(backendService?.ID || '', env)
      : undefined;
    const clientIpProbe = includeDiagnostics && endpointReady
      ? await requestJson(`${url.replace(/\/$/, '')}/api/v1/client-ip`, 2500)
      : null;
    const composeErrors = [
      ...(config.ok ? [] : [config.stderr || 'Could not resolve expected Compose services.']),
      ...(ps.ok ? [] : [ps.stderr || ps.stdout || 'Could not read Compose service state.']),
      ...(ps.ok && summary.runtimeReadFailed
        ? ['Compose service state omitted the required Service field.']
        : []),
    ].filter(Boolean);

    return {
      ...summary,
      expectedSource: config.ok ? 'compose' : 'settings',
      services,
      url,
      readyUrl,
      endpointReady,
      healthy: stackReadinessHealthy(summary, endpointReady),
      httpStatus: health.statusCode,
      ...(clientIpProbe ? {
        clientIp: {
          ok: clientIpProbe.ok,
          ip: clientIpProbe.data?.ip || '',
          statusCode: clientIpProbe.statusCode,
        },
      } : {}),
      ...(backendProxyTrust ? { backendProxyTrust } : {}),
      composeError: composeErrors.join('\n'),
    };
  }

  visitorIpTopologyFingerprint(env, stack, proxyStatus) {
    const serviceTopology = (stack?.services || [])
      .filter((service) => ['frontend', 'fastapi'].includes(service.Service))
      .map((service) => `${service.Service}:${service.ID || ''}:${service.State || ''}`)
      .sort();
    const config = proxyStatus?.config || {};
    const material = JSON.stringify({
      serviceTopology,
      composeProject: env.COMPOSE_PROJECT_NAME || 'omlorix',
      frontendBind: env.FRONTEND_HTTP_HOST_BIND || '127.0.0.1',
      frontendPort: env.FRONTEND_HTTP_HOST_PORT || '8080',
      trustedProxies: env.TRUSTED_PROXIES || '',
      uvicornAllowIps: env.UVICORN_FORWARDED_ALLOW_IPS || '',
      frontendTrust: env.FRONTEND_TRUST_PROXY_HEADERS || 'false',
      externalUpstreams: env.FRONTEND_TRUSTED_UPSTREAMS || '',
      launcherSecretHash: crypto.createHash('sha256')
        .update(String(env.OMLORIX_LAUNCHER_PROXY_SECRET || ''))
        .digest('hex'),
      proxy: {
        bindHost: config.bindHost || '',
        httpPort: config.httpPort || '',
        httpsEnabled: Boolean(config.httpsEnabled),
        httpsPort: config.httpsPort || '',
        redirectHttpToHttps: Boolean(config.redirectHttpToHttps),
        publicHostname: config.publicHostname || '',
        tlsCertPath: config.tlsCertPath || '',
        tlsKeyPath: config.tlsKeyPath || '',
        tlsCaPath: config.tlsCaPath || '',
        runtimeIdentity: proxyStatus?.managedByCli
          ? `cli:${proxyStatus.servicePid || 0}:${proxyStatus?.managedByService ? 'service' : 'detached'}`
          : `electron:${proxyStatus?.startedAt || ''}`,
      },
    });
    return crypto.createHash('sha256').update(material).digest('hex');
  }

  /**
   * Exercise the complete public path and persist a topology-bound result.
   * A cached success is accepted briefly, but container IDs, relevant env
   * values, or listener settings immediately produce a different fingerprint.
   */
  async verifyVisitorIpPath(env, stack, proxyStatus, { force = false } = {}) {
    const topologyFingerprint = this.visitorIpTopologyFingerprint(env, stack, proxyStatus);
    const metadata = await this.readLauncherMetadata();
    const previous = metadata.visitorIpVerification || {};
    const previousAge = Date.now() - Date.parse(previous.verifiedAt || '');
    if (
      !force
      && previous.verified
      && previous.topologyFingerprint === topologyFingerprint
      && Number.isFinite(previousAge)
      && previousAge >= 0
      && previousAge < 60_000
    ) {
      return previous;
    }

    const config = proxyStatus?.config || {};
    const frontendBoundToLoopback = ['127.0.0.1', '::1', 'localhost']
      .includes(String(env.FRONTEND_HTTP_HOST_BIND || '').trim().toLowerCase());
    let result = {
      verified: false,
      verifiedAt: '',
      topologyFingerprint,
      clientIp: '',
      scheme: '',
      host: '',
      errorCode: 'not_ready',
    };

    if (config.enabled && proxyStatus?.running && stack?.healthy && frontendBoundToLoopback) {
      const protocol = config.httpsEnabled ? 'https' : 'http';
      const port = config.httpsEnabled ? config.httpsPort : config.httpPort;
      const localHost = String(config.bindHost || '') === '::' ? '[::1]' : '127.0.0.1';
      const nonce = crypto.randomBytes(24).toString('base64url');
      const verificationUrl = `${protocol}://${localHost}:${port}/api/v1/proxy-verification?nonce=${nonce}`;
      const probe = await requestJson(verificationUrl, 5000, {
        // The request never leaves this host. The configured certificate is
        // still used for real visitors, while local verification must also work
        // with private CAs and certificates whose DNS name is not 127.0.0.1.
        rejectUnauthorized: false,
        headers: {
          Host: config.publicHostname || 'localhost',
        },
      });
      const clientIp = String(probe.data?.client_ip || '');
      const scheme = String(probe.data?.scheme || '');
      const host = String(probe.data?.host || '');
      const clientIsLoopback = clientIp === '::1' || clientIp.startsWith('127.');
      const verified = Boolean(
        probe.ok
        && probe.data?.nonce === nonce
        && probe.data?.trust_chain_accepted === true
        && clientIsLoopback
        && scheme === protocol
        && host.toLowerCase() === String(config.publicHostname || '').toLowerCase()
      );
      result = {
        verified,
        verifiedAt: verified ? new Date().toISOString() : '',
        topologyFingerprint,
        clientIp,
        scheme,
        host,
        errorCode: verified ? '' : 'end_to_end_failed',
      };
    } else if (config.enabled && env.FRONTEND_HTTP_HOST_BIND !== '127.0.0.1') {
      result.errorCode = 'frontend_not_loopback';
    } else if (config.enabled && !proxyStatus?.running) {
      result.errorCode = 'proxy_stopped';
    }

    await this.updateLauncherMetadata((current) => ({
      ...current,
      visitorIpVerification: result,
    }));
    return result;
  }

  visitorIpStatus(env, docker, stack, proxyStatus = {}, verification = {}) {
    const configured = proxyTrustConfigured(env);
    const vmNetworkingLikely = Boolean(docker?.vmNetworkingLikely);
    const observedIp = verification.clientIp || stack?.clientIp?.ip || '';
    const proxyEnabled = Boolean(proxyStatus?.config?.enabled);
    const externalProxyConfigured = !proxyEnabled
      && Boolean(String(env.FRONTEND_TRUSTED_UPSTREAMS || '').trim());
    const proxyRunning = Boolean(proxyStatus?.running);
    const proxyStopped = proxyEnabled && !proxyRunning;
    const backendRunning = Boolean(
      stack?.services?.some(
        (service) => service.Service === 'fastapi'
          && String(service.State || '').toLowerCase() === 'running',
      ),
    );
    const runtimeTrustKnown = Boolean(stack?.backendProxyTrust?.known);
    const runtimeTrustMatches = Boolean(stack?.backendProxyTrust?.matchesDesired);
    const restartRequired = backendRunning
      && runtimeTrustKnown
      && !runtimeTrustMatches;
    const runtimeTrustReady = !backendRunning || (runtimeTrustKnown && runtimeTrustMatches);
    let level = 'warn';
    let title = 'Needs setup';
    let message = 'Enable trusted proxy headers so rate limits, audit logs, auth checks, and access logs use the visitor IP.';
    let titleKey = 'launcher_visitor_ip_title_needs_setup';
    let messageKey = 'launcher_visitor_ip_message_needs_setup';

    if (restartRequired) {
      title = 'Restart required';
      message = 'Proxy settings are saved, but the running Omlorix container still has the previous visitor-IP configuration. Restart Omlorix to apply them.';
      titleKey = 'launcher_visitor_ip_title_restart_required';
      messageKey = 'launcher_visitor_ip_message_restart_required';
    } else if (proxyStopped) {
      // Enabling the proxy only persists its configuration. Until the in-process
      // listener is running, visitors cannot enter through the trusted host-side
      // hop and Docker Desktop will continue to hide their source addresses.
      title = 'Proxy stopped';
      message = 'The launcher proxy is enabled but stopped. Start it or turn on automatic startup so visitor IPs reach Omlorix through the proxy.';
      titleKey = 'launcher_visitor_ip_title_proxy_stopped';
      messageKey = 'launcher_visitor_ip_message_proxy_stopped';
    } else if (
      configured
      && runtimeTrustReady
      && verification.verified
      && ((proxyEnabled && proxyRunning) || externalProxyConfigured)
    ) {
      level = 'ok';
      title = 'Proxy verified';
      message = 'A recent end-to-end request verified the visitor IP and public scheme through the running proxy.';
      titleKey = 'launcher_visitor_ip_title_proxy_running';
      messageKey = 'launcher_visitor_ip_message_proxy_running';
    } else if (configured && proxyEnabled && proxyRunning) {
      level = 'error';
      title = 'Verification failed';
      message = 'The proxy is running, but Omlorix could not verify the visitor IP and public scheme through the complete request path.';
      titleKey = 'launcher_visitor_ip_title_verification_failed';
      messageKey = 'launcher_visitor_ip_message_verification_failed';
    } else if (configured) {
      level = vmNetworkingLikely ? 'warn' : 'ok';
      title = vmNetworkingLikely ? 'Proxy-ready' : 'Configured';
      message = vmNetworkingLikely
        ? 'Omlorix is ready to trust a host or external proxy. Docker VM networking can still hide IPs unless traffic reaches that proxy before Docker.'
        : 'Trusted proxy parsing is configured for the bundled Docker proxy path.';
      titleKey = vmNetworkingLikely
        ? 'launcher_visitor_ip_title_proxy_ready'
        : 'launcher_visitor_ip_title_configured';
      messageKey = vmNetworkingLikely
        ? 'launcher_visitor_ip_message_proxy_ready'
        : 'launcher_visitor_ip_message_configured';
    }

    return {
      level,
      title,
      titleKey,
      message,
      messageKey,
      configured,
      // Ready means the running backend loaded the saved trust values and, when
      // launcher-managed proxying is enabled, its listener is also active.
      ready: configured && runtimeTrustReady && verification.verified === true,
      vmNetworkingLikely,
      observedIp,
      proxyEnabled,
      externalProxyConfigured,
      proxyRunning,
      verification,
      restartRequired,
      recommendedAction: restartRequired
        ? 'restart-omlorix'
        : proxyStopped
          ? 'start-proxy'
          : configured && proxyEnabled
            ? 'fix'
            : configured
              ? 'restart'
            : 'fix',
    };
  }

  resolveUrl(env) {
    // The launcher always talks to the local HTTP port exposed by Compose.
    return `http://localhost:${env.FRONTEND_HTTP_HOST_PORT || '8080'}`;
  }

  async getState() {
    await this.ensureServerHome();
    const env = await this.readEnv();
    const envRequirements = buildEnvRequirementStatus(env);
    const docker = await this.dockerStatus();
    const stack = docker.installed && docker.running && docker.compose
      ? await this.stackStatus()
      : {
          ...mergeExpectedComposeServices(
            expectedServiceNamesFromToggles(readEnvToggles(env)),
            [],
            { expectedKnown: true },
          ),
          expectedSource: 'settings',
          url: this.resolveUrl(env),
          healthy: false,
          httpStatus: null,
          composeError: '',
        };
    const proxyService = await this.proxyServiceStatus();
    const proxy = this.proxyStatus(env, proxyService);
    const externalProxyConfigured = !proxy.config?.enabled
      && Boolean(String(env.FRONTEND_TRUSTED_UPSTREAMS || '').trim());
    const visitorIpVerification = await (
      externalProxyConfigured
        ? this.cliVisitorIpVerification()
        : this.verifyVisitorIpPath(env, stack, proxy)
    ).catch(() => ({ verified: false, errorCode: 'verification_unavailable' }));
    const setup = await this.readSetupState(env);
    const serverSettings = await this.readServerSettings();
    const automaticEnvBackupError = this.automaticEnvBackupError
      || (setup.backupConfigured && !setup.backupCurrent ? 'outdated' : '');

    return {
      appName: this.app.getName(),
      appVersion: this.app.getVersion(),
      serverHome: this.serverHome,
      envFile: this.envFile,
      docker,
      stack,
      observability: observabilityCapability(readEnvToggles(env)),
      visitorIp: this.visitorIpStatus(env, docker, stack, proxy, visitorIpVerification),
      proxy,
      setup,
      automaticEnvBackupError,
      launcherMetadata: await this.readLauncherMetadata(),
      // The renderer needs the release preference only. Host proxy state is
      // already represented by the redacted proxy status/config contract, so
      // never serialize the stored TLS-key passphrase across IPC.
      serverSettings: {
        schemaVersion: serverSettings.schemaVersion,
        updateChannel: serverSettings.updateChannel,
      },
      envRequirements,
      env: this.publicEnv(env),
      busy: Boolean(this.activeOperation),
      operation: this.activeOperation,
      isPackaged: this.app.isPackaged,
    };
  }

  async saveSettings(payload) {
    return this.withSharedOperationLock(
      'config set',
      () => this.saveSettingsUnlocked(payload),
    );
  }

  async saveSettingsUnlocked(payload) {
    await this.ensureServerHome();
    const currentEnv = await this.readEnv();
    const updates = {};
    const jwtSecretKey = String(payload?.jwtSecretKey || '');
    const encryptionKey = String(payload?.encryptionKey || '');
    const passwordResetSalt = String(payload?.passwordResetSalt || '');
    const logIpHashSalt = String(payload?.logIpHashSalt || '');

    // Older renderers and compatibility callers may send the complete settings
    // snapshot for every edit. Validate an existing secret only when this request
    // actually changes it, otherwise a legacy value would prevent operators from
    // repairing unrelated settings. Explicit attempts to clear or replace a
    // secret still receive the current strict validation.
    if (
      payload?.jwtSecretKey !== undefined
      && jwtSecretKey !== String(currentEnv.JWT_SECRET_KEY || '')
      && jwtSecretByteLength(jwtSecretKey) < 64
    ) {
      throw new Error('JWT secret key must contain at least 64 bytes.');
    }
    if (
      payload?.encryptionKey !== undefined
      && encryptionKey !== String(currentEnv.ENCRYPTION_KEY || '')
      && !isValidFernetKey(encryptionKey)
    ) {
      throw new Error('Encryption key must be a valid Fernet key.');
    }
    if (
      payload?.passwordResetSalt !== undefined
      && passwordResetSalt !== String(currentEnv.PASSWORD_RESET_IDENTIFIER_HASH_SALT || '')
      && passwordResetSalt.length < 16
    ) {
      throw new Error('Password reset salt must contain at least 16 characters.');
    }
    if (
      payload?.logIpHashSalt !== undefined
      && logIpHashSalt !== String(currentEnv.LOG_IP_HASH_SALT || '')
      && logIpHashSalt.length < 16
    ) {
      throw new Error('Audit IP hash salt must contain at least 16 characters.');
    }
    if (payload?.composeProjectName !== undefined) {
      updates.COMPOSE_PROJECT_NAME = String(payload.composeProjectName || 'omlorix').trim();
    }
    if (payload?.mode !== undefined) {
      const mode = String(payload.mode || 'production').trim().toLowerCase();
      updates.MODE = mode === 'dev' ? 'dev' : 'production';
    }
    if (payload?.version !== undefined) {
      const rawVersion = String(payload.version || 'stable').trim() || 'stable';
      updates.OMLORIX_VERSION = rawVersion === 'stable' || rawVersion === 'beta'
        ? (await this.latestReleaseInfo(rawVersion)).version
        : rawVersion;
    }

    if (payload?.backupPassphrase) {
      updates.BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE = String(payload.backupPassphrase);
    }
    if (payload?.jwtSecretKey !== undefined) {
      updates.JWT_SECRET_KEY = jwtSecretKey;
    }
    if (payload?.encryptionKey !== undefined) {
      updates.ENCRYPTION_KEY = encryptionKey;
    }
    if (payload?.passwordResetSalt !== undefined) {
      updates.PASSWORD_RESET_IDENTIFIER_HASH_SALT = passwordResetSalt;
    }
    if (payload?.logIpHashSalt !== undefined) {
      updates.LOG_IP_HASH_SALT = logIpHashSalt;
    }

    if (payload?.databaseName !== undefined) {
      updates.DATABASE_NAME = String(payload.databaseName || '').trim();
    }
    if (payload?.databaseUser !== undefined) {
      updates.DATABASE_USER = String(payload.databaseUser || '').trim();
    }
    if (payload?.databasePassword !== undefined) {
      updates.DATABASE_PASSWORD = String(payload.databasePassword || '');
    }
    if (payload?.databaseHost !== undefined) {
      updates.DATABASE_HOST = String(payload.databaseHost || 'localhost').trim();
    }
    if (payload?.databasePort !== undefined) {
      updates.DATABASE_PORT = String(payload.databasePort || '5432').trim();
    }
    if (payload?.databaseSchema !== undefined) {
      updates.DATABASE_SCHEMA = String(payload.databaseSchema || 'app').trim();
    }
    if (payload?.databaseAuditLogSchema !== undefined) {
      updates.DATABASE_AUDIT_LOG_SCHEMA = String(payload.databaseAuditLogSchema || 'audit').trim();
    }
    if (payload?.databaseLogsSchema !== undefined) {
      updates.DATABASE_LOGS_SCHEMA = String(payload.databaseLogsSchema || 'logs').trim();
    }
    if (payload?.autoCreateDatabases !== undefined) {
      updates.OMLORIX_AUTO_CREATE_DATABASES = String(Boolean(payload.autoCreateDatabases));
    }
    if (payload?.databaseHostOverride !== undefined) {
      updates.DATABASE_HOST_OVERRIDE = String(payload.databaseHostOverride || '').trim();
    }
    if (payload?.databasePortOverride !== undefined) {
      updates.DATABASE_PORT_OVERRIDE = String(payload.databasePortOverride || '').trim();
    }
    if (payload?.devDatabaseHostPort !== undefined) {
      updates.DEV_DATABASE_HOST_PORT = String(payload.devDatabaseHostPort || '5432').trim();
    }
    if (payload?.databaseUrl !== undefined) {
      updates.DATABASE_URL = String(payload.databaseUrl || '').trim();
    }
    if (payload?.redisPassword !== undefined) {
      updates.REDIS_PASSWORD = String(payload.redisPassword || '');
    }
    if (payload?.redisEnabled !== undefined) {
      updates.REDIS_ENABLED = String(Boolean(payload.redisEnabled));
    }
    if (payload?.redisUrl !== undefined) {
      updates.REDIS_URL = String(payload.redisUrl || '').trim();
    }
    if (payload?.devRedisHostPort !== undefined) {
      updates.DEV_REDIS_HOST_PORT = String(payload.devRedisHostPort || '6379').trim();
    }
    if (payload?.pgbouncerHostBind !== undefined) {
      updates.PGBOUNCER_HOST_BIND = String(payload.pgbouncerHostBind || '127.0.0.1').trim();
    }
    if (payload?.pgbouncerHostPort !== undefined) {
      updates.PGBOUNCER_HOST_PORT = String(payload.pgbouncerHostPort || '6432').trim();
    }
    if (payload?.pgbouncerPoolMode !== undefined) {
      const poolMode = String(payload.pgbouncerPoolMode || 'transaction').trim().toLowerCase();
      if (!ENV_ENUM_OPTIONS.PGBOUNCER_POOL_MODE.includes(poolMode)) {
        throw new Error('Choose one of: transaction, session.');
      }
      updates.PGBOUNCER_POOL_MODE = poolMode;
    }
    if (payload?.pgbouncerMaxClientConn !== undefined) {
      updates.PGBOUNCER_MAX_CLIENT_CONN = String(payload.pgbouncerMaxClientConn || '200').trim();
    }
    if (payload?.pgbouncerDefaultPoolSize !== undefined) {
      updates.PGBOUNCER_DEFAULT_POOL_SIZE = String(payload.pgbouncerDefaultPoolSize || '40').trim();
    }
    if (payload?.pgbouncerReservePoolSize !== undefined) {
      updates.PGBOUNCER_RESERVE_POOL_SIZE = String(payload.pgbouncerReservePoolSize || '10').trim();
    }
    if (payload?.minioRootUser !== undefined) {
      updates.MINIO_ROOT_USER = String(payload.minioRootUser || '').trim();
    }
    if (payload?.minioRootPassword !== undefined) {
      updates.MINIO_ROOT_PASSWORD = String(payload.minioRootPassword || '');
    }
    if (payload?.minioApiHostBind !== undefined) {
      updates.MINIO_API_HOST_BIND = String(payload.minioApiHostBind || '127.0.0.1').trim();
    }
    if (payload?.minioApiHostPort !== undefined) {
      updates.MINIO_API_HOST_PORT = String(payload.minioApiHostPort || '9000').trim();
    }
    if (payload?.minioConsoleHostBind !== undefined) {
      updates.MINIO_CONSOLE_HOST_BIND = String(payload.minioConsoleHostBind || '127.0.0.1').trim();
    }
    if (payload?.minioConsoleHostPort !== undefined) {
      updates.MINIO_CONSOLE_HOST_PORT = String(payload.minioConsoleHostPort || '9001').trim();
    }
    if (payload?.fileStorageProvider !== undefined) {
      updates.FILE_STORAGE_PROVIDER = String(payload.fileStorageProvider || 'local').trim().toLowerCase();
    }
    if (payload?.fileStorageLocalBasePath !== undefined) {
      updates.FILE_STORAGE_LOCAL_BASE_PATH = String(payload.fileStorageLocalBasePath || '').trim();
    }
    if (payload?.fileStorageS3Bucket !== undefined) {
      updates.FILE_STORAGE_S3_BUCKET = String(payload.fileStorageS3Bucket || '').trim();
    }
    if (payload?.fileStorageS3Prefix !== undefined) {
      updates.FILE_STORAGE_S3_PREFIX = String(payload.fileStorageS3Prefix || '').trim();
    }
    if (payload?.fileStorageS3Region !== undefined) {
      updates.FILE_STORAGE_S3_REGION = String(payload.fileStorageS3Region || '').trim();
    }
    if (payload?.fileStorageS3EndpointUrl !== undefined) {
      updates.FILE_STORAGE_S3_ENDPOINT_URL = String(payload.fileStorageS3EndpointUrl || '').trim();
    }
    if (payload?.fileStorageS3AccessKeyId !== undefined) {
      updates.FILE_STORAGE_S3_ACCESS_KEY_ID = String(payload.fileStorageS3AccessKeyId || '');
    }
    if (payload?.fileStorageS3SecretAccessKey !== undefined) {
      updates.FILE_STORAGE_S3_SECRET_ACCESS_KEY = String(payload.fileStorageS3SecretAccessKey || '');
    }
    if (payload?.fileStorageS3SessionToken !== undefined) {
      updates.FILE_STORAGE_S3_SESSION_TOKEN = String(payload.fileStorageS3SessionToken || '');
    }
    if (payload?.fileStorageGcsBucket !== undefined) {
      updates.FILE_STORAGE_GCS_BUCKET = String(payload.fileStorageGcsBucket || '').trim();
    }
    if (payload?.fileStorageGcsPrefix !== undefined) {
      updates.FILE_STORAGE_GCS_PREFIX = String(payload.fileStorageGcsPrefix || '').trim();
    }
    if (payload?.fileStorageGcsProject !== undefined) {
      updates.FILE_STORAGE_GCS_PROJECT = String(payload.fileStorageGcsProject || '').trim();
    }
    if (payload?.fileStorageGcsCredentialsJson !== undefined) {
      updates.FILE_STORAGE_GCS_CREDENTIALS_JSON = String(payload.fileStorageGcsCredentialsJson || '');
    }
    if (payload?.fileStorageAzureContainer !== undefined) {
      updates.FILE_STORAGE_AZURE_CONTAINER = String(payload.fileStorageAzureContainer || '').trim();
    }
    if (payload?.fileStorageAzurePrefix !== undefined) {
      updates.FILE_STORAGE_AZURE_PREFIX = String(payload.fileStorageAzurePrefix || '').trim();
    }
    if (payload?.fileStorageAzureConnectionString !== undefined) {
      updates.FILE_STORAGE_AZURE_CONNECTION_STRING = String(payload.fileStorageAzureConnectionString || '');
    }
    if (payload?.fileStorageAzureAccountUrl !== undefined) {
      updates.FILE_STORAGE_AZURE_ACCOUNT_URL = String(payload.fileStorageAzureAccountUrl || '').trim();
    }
    if (payload?.fileStorageAzureCredential !== undefined) {
      updates.FILE_STORAGE_AZURE_CREDENTIAL = String(payload.fileStorageAzureCredential || '');
    }
    if (payload?.fileStorageWebdavUrl !== undefined) {
      updates.FILE_STORAGE_WEBDAV_URL = String(payload.fileStorageWebdavUrl || '').trim();
    }
    if (payload?.fileStorageWebdavUsername !== undefined) {
      updates.FILE_STORAGE_WEBDAV_USERNAME = String(payload.fileStorageWebdavUsername || '').trim();
    }
    if (payload?.fileStorageWebdavPassword !== undefined) {
      updates.FILE_STORAGE_WEBDAV_PASSWORD = String(payload.fileStorageWebdavPassword || '');
    }
    if (payload?.fileStorageWebdavPrefix !== undefined) {
      updates.FILE_STORAGE_WEBDAV_PREFIX = String(payload.fileStorageWebdavPrefix || '').trim();
    }
    if (payload?.fileStorageWebdavVerifySsl !== undefined) {
      updates.FILE_STORAGE_WEBDAV_VERIFY_SSL = String(Boolean(payload.fileStorageWebdavVerifySsl));
    }
    if (payload?.fileStorageWebdavTimeout !== undefined) {
      updates.FILE_STORAGE_WEBDAV_TIMEOUT = String(payload.fileStorageWebdavTimeout || '30').trim();
    }
    if (payload?.otelEnabled !== undefined) {
      updates.OTEL_ENABLED = String(Boolean(payload.otelEnabled));
    }
    if (payload?.otelServiceName !== undefined) {
      updates.OTEL_SERVICE_NAME = String(payload.otelServiceName || 'omlorix-backend').trim();
    }
    if (payload?.otelExporterOtlpEndpoint !== undefined) {
      updates.OTEL_EXPORTER_OTLP_ENDPOINT = String(payload.otelExporterOtlpEndpoint || '').trim();
    }
    if (payload?.otelExporterOtlpInsecure !== undefined) {
      updates.OTEL_EXPORTER_OTLP_INSECURE = String(Boolean(payload.otelExporterOtlpInsecure));
    }
    if (payload?.otelTracesEnabled !== undefined) {
      updates.OTEL_TRACES_ENABLED = String(Boolean(payload.otelTracesEnabled));
    }
    if (payload?.otelTracesSampler !== undefined) {
      updates.OTEL_TRACES_SAMPLER = String(payload.otelTracesSampler || 'parentbased_traceidratio').trim();
    }
    if (payload?.otelTracesSamplerArg !== undefined) {
      updates.OTEL_TRACES_SAMPLER_ARG = String(payload.otelTracesSamplerArg || '1.0').trim();
    }
    if (payload?.otelMetricsEnabled !== undefined) {
      updates.OTEL_METRICS_ENABLED = String(Boolean(payload.otelMetricsEnabled));
    }
    if (payload?.otelPrometheusExporterEnabled !== undefined) {
      updates.OTEL_PROMETHEUS_EXPORTER_ENABLED = String(Boolean(payload.otelPrometheusExporterEnabled));
    }
    if (payload?.otelLogsEnabled !== undefined) {
      updates.OTEL_LOGS_ENABLED = String(Boolean(payload.otelLogsEnabled));
    }
    if (payload?.otelInstrumentFastapi !== undefined) {
      updates.OTEL_INSTRUMENT_FASTAPI = String(Boolean(payload.otelInstrumentFastapi));
    }
    if (payload?.otelInstrumentSqlalchemy !== undefined) {
      updates.OTEL_INSTRUMENT_SQLALCHEMY = String(Boolean(payload.otelInstrumentSqlalchemy));
    }
    if (payload?.otelInstrumentHttpClients !== undefined) {
      updates.OTEL_INSTRUMENT_HTTP_CLIENTS = String(Boolean(payload.otelInstrumentHttpClients));
    }
    if (payload?.otelSqlCommenterEnabled !== undefined) {
      updates.OTEL_SQL_COMMENTER_ENABLED = String(Boolean(payload.otelSqlCommenterEnabled));
    }
    if (payload?.otelCaptureHttpRoute !== undefined) {
      updates.OTEL_CAPTURE_HTTP_ROUTE = String(Boolean(payload.otelCaptureHttpRoute));
    }
    if (payload?.otelCaptureHttpUserAgent !== undefined) {
      updates.OTEL_CAPTURE_HTTP_USER_AGENT = String(Boolean(payload.otelCaptureHttpUserAgent));
    }
    if (payload?.otelHashHttpUserAgent !== undefined) {
      updates.OTEL_HASH_HTTP_USER_AGENT = String(Boolean(payload.otelHashHttpUserAgent));
    }
    if (payload?.otelGrpcHostBind !== undefined) {
      updates.OTEL_GRPC_HOST_BIND = String(payload.otelGrpcHostBind || '127.0.0.1').trim();
    }
    if (payload?.otelGrpcHostPort !== undefined) {
      updates.OTEL_GRPC_HOST_PORT = String(payload.otelGrpcHostPort || '4317').trim();
    }
    if (payload?.otelHttpHostBind !== undefined) {
      updates.OTEL_HTTP_HOST_BIND = String(payload.otelHttpHostBind || '127.0.0.1').trim();
    }
    if (payload?.otelHttpHostPort !== undefined) {
      updates.OTEL_HTTP_HOST_PORT = String(payload.otelHttpHostPort || '4318').trim();
    }
    if (payload?.otelPrometheusHostBind !== undefined) {
      updates.OTEL_PROMETHEUS_HOST_BIND = String(payload.otelPrometheusHostBind || '127.0.0.1').trim();
    }
    if (payload?.otelPrometheusHostPort !== undefined) {
      updates.OTEL_PROMETHEUS_HOST_PORT = String(payload.otelPrometheusHostPort || '8889').trim();
    }
    if (payload?.otelHealthcheckHostBind !== undefined) {
      updates.OTEL_HEALTHCHECK_HOST_BIND = String(payload.otelHealthcheckHostBind || '127.0.0.1').trim();
    }
    if (payload?.otelHealthcheckHostPort !== undefined) {
      updates.OTEL_HEALTHCHECK_HOST_PORT = String(payload.otelHealthcheckHostPort || '13133').trim();
    }
    if (payload?.jaegerUiHostBind !== undefined) {
      updates.JAEGER_UI_HOST_BIND = String(payload.jaegerUiHostBind || '127.0.0.1').trim();
    }
    if (payload?.jaegerUiHostPort !== undefined) {
      updates.JAEGER_UI_HOST_PORT = String(payload.jaegerUiHostPort || '16686').trim();
    }
    if (payload?.jaegerCollectorHostBind !== undefined) {
      updates.JAEGER_COLLECTOR_HOST_BIND = String(payload.jaegerCollectorHostBind || '127.0.0.1').trim();
    }
    if (payload?.jaegerCollectorHostPort !== undefined) {
      updates.JAEGER_COLLECTOR_HOST_PORT = String(payload.jaegerCollectorHostPort || '14268').trim();
    }
    if (payload?.prometheusHostBind !== undefined) {
      updates.PROMETHEUS_HOST_BIND = String(payload.prometheusHostBind || '127.0.0.1').trim();
    }
    if (payload?.prometheusHostPort !== undefined) {
      updates.PROMETHEUS_HOST_PORT = String(payload.prometheusHostPort || '9090').trim();
    }
    if (payload?.alertmanagerHostBind !== undefined) {
      updates.ALERTMANAGER_HOST_BIND = String(payload.alertmanagerHostBind || '127.0.0.1').trim();
    }
    if (payload?.alertmanagerHostPort !== undefined) {
      updates.ALERTMANAGER_HOST_PORT = String(payload.alertmanagerHostPort || '9093').trim();
    }
    if (payload?.grafanaHostBind !== undefined) {
      updates.GRAFANA_HOST_BIND = String(payload.grafanaHostBind || '127.0.0.1').trim();
    }
    if (payload?.grafanaHostPort !== undefined) {
      updates.GRAFANA_HOST_PORT = String(payload.grafanaHostPort || '3001').trim();
    }
    if (payload?.grafanaAdminUser !== undefined) {
      updates.GRAFANA_ADMIN_USER = String(payload.grafanaAdminUser || '').trim();
    }
    if (payload?.grafanaAdminPassword !== undefined) {
      updates.GRAFANA_ADMIN_PASSWORD = String(payload.grafanaAdminPassword || '');
    }
    if (payload?.grafanaRootUrl !== undefined) {
      updates.GRAFANA_ROOT_URL = String(payload.grafanaRootUrl || '').trim();
    }
    if (payload?.postgresExporterDataSourceUri !== undefined) {
      updates.POSTGRES_EXPORTER_DATA_SOURCE_URI = String(payload.postgresExporterDataSourceUri || '').trim();
    }
    if (payload?.postgresExporterDataSourceUser !== undefined) {
      updates.POSTGRES_EXPORTER_DATA_SOURCE_USER = String(payload.postgresExporterDataSourceUser || '').trim();
    }
    if (payload?.postgresExporterDataSourcePass !== undefined) {
      updates.POSTGRES_EXPORTER_DATA_SOURCE_PASS = String(payload.postgresExporterDataSourcePass || '');
    }
    if (payload?.redisExporterAddr !== undefined) {
      updates.REDIS_EXPORTER_ADDR = String(payload.redisExporterAddr || '').trim();
    }

    // Handle toggles from payload
    if (payload?.useBundledDB !== undefined) {
      updates.OMLORIX_USE_BUNDLED_DB = String(payload.useBundledDB);
      if (!payload.useBundledDB) {
        updates.OMLORIX_USE_PGBOUNCER = 'false';
      }
    }
    if (payload?.useBundledRedis !== undefined) {
      updates.OMLORIX_USE_BUNDLED_REDIS = String(payload.useBundledRedis);
    }
    if (payload?.usePgbouncer !== undefined) {
      updates.OMLORIX_USE_PGBOUNCER = String(payload.usePgbouncer);
    }
    if (payload?.useBundledStorage !== undefined) {
      updates.OMLORIX_USE_BUNDLED_STORAGE = String(payload.useBundledStorage);
    }

    if (updates.OMLORIX_USE_BUNDLED_DB === 'false') {
      updates.OMLORIX_USE_PGBOUNCER = 'false';
    }
    if (updates.REDIS_ENABLED === 'false') {
      updates.OMLORIX_USE_BUNDLED_REDIS = 'false';
    }
    if (updates.OMLORIX_USE_BUNDLED_STORAGE === 'true') {
      // Bundled MinIO is an S3-compatible backend. Canonicalize the provider
      // atomically so API/setup callers cannot start MinIO while Omlorix still
      // points at an older local, GCS, Azure, or WebDAV selection.
      updates.FILE_STORAGE_PROVIDER = 's3';
    }

    // REDIS_URL is derived configuration in bundled mode. Canonicalize it in
    // the same atomic settings write so password rotations and topology changes
    // cannot leave a stale credential for the backend containers.
    const effectiveRedisEnv = { ...currentEnv, ...updates };
    const effectiveRedisToggles = readEnvToggles(effectiveRedisEnv);
    if (effectiveRedisToggles.redisEnabled && effectiveRedisToggles.useBundledRedis) {
      const redisPassword = String(effectiveRedisEnv.REDIS_PASSWORD || '');
      if (redisPassword) {
        updates.REDIS_URL = defaultLocalRedisUrl(effectiveRedisEnv, redisPassword);
      }
    }

    await this.writeEnv(updates);
    if (payload?.updateChannel !== undefined) {
      await this.updateServerSettings((current) => ({
        ...current,
        updateChannel: normalizeUpdateChannel(payload.updateChannel),
      }));
    }
    return this.getState();
  }

  buildProxyEnvUpdates(payload = {}, currentEnv = {}) {
    // Callers that enable the proxy without specifying an autostart preference
    // receive the safe operational default. The launcher form always supplies
    // the checkbox value, so an explicit operator opt-out remains respected.
    const autostart = payload.autostart === undefined
      ? Boolean(payload.enabled)
      : Boolean(payload.autostart);
    const launcherProxyEnabled = Boolean(payload.enabled);
    const requestedTrustedProxies = String(payload.trustedProxies || '').trim();
    const externalProxyEnabled = Boolean(payload.trustProxyHeaders)
      && !launcherProxyEnabled
      && Boolean(requestedTrustedProxies);
    const authenticatedIngressEnabled = launcherProxyEnabled || externalProxyEnabled;
    const currentManagedTrustedProxy = String(currentEnv.TRUSTED_PROXIES || '').trim();
    const backendTrustedProxy = authenticatedIngressEnabled
      ? currentManagedTrustedProxy
      : requestedTrustedProxies;
    const backendTrustedProxyIp = authenticatedIngressEnabled
      ? currentManagedTrustedProxy.replace(/\/(?:32|128)$/, '')
      : String(payload.uvicornForwardedAllowIps || UVICORN_TRUSTED_PROXY_ENV_VALUE).trim();
    const currentLauncherSecret = String(currentEnv.OMLORIX_LAUNCHER_PROXY_SECRET || '').trim();
    const launcherSecret = /^[0-9a-f]{64}$/i.test(currentLauncherSecret)
      ? currentLauncherSecret
      : crypto.randomBytes(32).toString('hex');
    const requestedFrontendBind = String(payload.frontendHttpHostBind || '127.0.0.1').trim();
    const updates = {
      TRUST_PROXY_HEADERS: String(authenticatedIngressEnabled || Boolean(payload.trustProxyHeaders)),
      TRUSTED_PROXIES: backendTrustedProxy,
      TRUSTED_HOSTS: String(payload.trustedHosts || '').trim(),
      UVICORN_FORWARDED_ALLOW_IPS: backendTrustedProxyIp,
      RATE_LIMIT_TRUSTED_PROXIES: authenticatedIngressEnabled
        ? backendTrustedProxy
        : String(payload.rateLimitTrustedProxies || '').trim(),
      AUTH_TRUSTED_PROXIES: authenticatedIngressEnabled
        ? backendTrustedProxy
        : String(payload.authTrustedProxies || '').trim(),
      RATE_LIMIT_PROXY_SETTINGS_CACHE_SECONDS: String(payload.rateLimitProxySettingsCacheSeconds || '60').trim(),
      // The launcher may preserve a forwarding chain only when it is the sole
      // path to the Docker frontend. Loopback binding prevents remote clients
      // from bypassing the launcher's authoritative header rewrite.
      FRONTEND_HTTP_HOST_BIND: launcherProxyEnabled ? '127.0.0.1' : requestedFrontendBind,
      FRONTEND_HTTP_HOST_PORT: String(payload.frontendHttpHostPort || '8080').trim(),
      FRONTEND_TRUST_PROXY_HEADERS: String(launcherProxyEnabled || externalProxyEnabled),
      FRONTEND_TRUSTED_UPSTREAMS: externalProxyEnabled ? requestedTrustedProxies : '',
      API_LB_TRAEFIK_WEB_HOST_PORT: String(payload.apiLbTraefikWebHostPort || '8080').trim(),
      API_LB_TRAEFIK_DASHBOARD_HOST_PORT: String(payload.apiLbTraefikDashboardHostPort || '8081').trim(),
      OMLORIX_LAUNCHER_PROXY_ENABLED: String(Boolean(payload.enabled)),
      OMLORIX_LAUNCHER_PROXY_SECRET: launcherSecret,
      OMLORIX_LAUNCHER_PROXY_AUTOSTART: String(autostart),
      OMLORIX_LAUNCHER_PROXY_BIND: String(payload.bindHost || '0.0.0.0').trim(),
      // Redirects must never reflect the request Host header. The setup flow's
      // required public host is also the canonical redirect/verification host.
      OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME: String(
        payload.publicHostname
          || String(payload.trustedHosts || '').split(',').map((item) => item.trim()).find(Boolean)
          || currentEnv.OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME
          || '',
      ).trim(),
      OMLORIX_LAUNCHER_PROXY_HTTP_PORT: String(payload.httpPort || '8081').trim(),
      OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED: String(Boolean(payload.httpsEnabled)),
      OMLORIX_LAUNCHER_PROXY_HTTPS_PORT: String(payload.httpsPort || '8443').trim(),
      OMLORIX_LAUNCHER_PROXY_REDIRECT_HTTP_TO_HTTPS: String(Boolean(payload.redirectHttpToHttps)),
      OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH: String(payload.tlsCertPath || '').trim(),
      OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH: String(payload.tlsKeyPath || '').trim(),
      OMLORIX_LAUNCHER_PROXY_TLS_CA_PATH: String(payload.tlsCaPath || '').trim(),
    };

    if (payload.clearTlsKeyPassphrase) {
      updates.OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE = '';
    } else if (payload.tlsKeyPassphrase) {
      updates.OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE = String(payload.tlsKeyPassphrase);
    } else if (currentEnv.OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE) {
      updates.OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE = currentEnv.OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE;
    }

    return updates;
  }

  async saveProxySettings(payload, options = {}) {
    return this.withSharedOperationLock('proxy settings', async () => {
    await this.ensureServerHome();
    const env = await this.readEnv();
    const updates = this.buildProxyEnvUpdates(payload, env);
    const nextEnv = { ...env, ...updates };
    const config = this.proxyConfigFromEnv(nextEnv);
    const errors = validateProxyConfig(config, { requireTlsFiles: true });
    const externalTrustedNetworks = String(updates.FRONTEND_TRUSTED_UPSTREAMS || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
    if (externalTrustedNetworks.some((item) => item === '0.0.0.0/0' || item === '::/0')) {
      errors.trustedProxies = 'Trusted proxy networks must not include the entire Internet.';
    }
    if (Object.keys(errors).length) {
      const error = new Error('Proxy settings need attention.');
      error.validationErrors = errors;
      throw error;
    }

    const proxySettings = proxySettingsFromEnv(nextEnv);
    const containerUpdates = Object.fromEntries(
      Object.entries(updates).filter(([key]) => !MANAGED_PROXY_SETTINGS_ENV_KEYS.has(key)),
    );

    // Keep the security boundary safe if the process is interrupted between
    // the two atomic files. Enabling first closes the direct Docker listener;
    // disabling first removes the host listener before restoring Docker bind.
    if (config.enabled) {
      await this.writeEnv(containerUpdates);
      await this.updateServerSettings((current) => ({ ...current, proxy: proxySettings }));
    } else {
      await this.updateServerSettings((current) => ({ ...current, proxy: proxySettings }));
      await this.writeEnv(containerUpdates);
    }

    if (config.enabled) {
      await this.enforceRunningFrontendProxyIsolation(
        String(env.FRONTEND_HTTP_HOST_BIND || '127.0.0.1').trim() !== '127.0.0.1',
      );
    }

    const service = await this.proxyServiceStatus();
    const proxyRunning = this.proxy.status().running;
    if (service.available) {
      // The bundled CLI is authoritative even before native-service install:
      // its detached process survives closing Electron and uses the same code
      // and status contract as systemd, launchd, and Windows Service modes.
      if (proxyRunning) await this.proxy.stop();
      if (service.running) {
        await this.controlAuthoritativeProxy('stop', service);
      }
      if (config.enabled && (service.running || config.autostart)) {
        await this.controlAuthoritativeProxy('start', service);
      }
    } else if (config.enabled && (proxyRunning || config.autostart)) {
      // Saving an enabled autostart configuration should make it true now, not
      // only after the next launcher restart. start() safely replaces a running
      // listener, which also applies changed ports or TLS files immediately.
      await this.proxy.start(config);
    } else if (proxyRunning && !config.enabled) {
      await this.proxy.stop();
    }

      return this.getState();
    }, { lockHeld: options.sharedLockHeld === true });
  }

  async startProxy(options = {}) {
    return this.withSharedOperationLock('proxy start', async () => {
    await this.ensureServerHome();
    await this.enforceRunningFrontendProxyIsolation();
    const env = await this.readEnv();
    const config = this.proxyConfigFromEnv(env);
    let service = await this.proxyServiceStatus();
    if (service.available) {
      if (service.installed && service.updateRequired) {
        await this.runElevatedWindowsProxyServiceCommand('refresh-service');
        service = await this.proxyServiceStatus();
      }
      if (this.proxy.status().running) await this.proxy.stop();
      if (!service.running) await this.controlAuthoritativeProxy('start', service);
    } else {
      await this.proxy.start(config);
    }
      return this.getState();
    }, { lockHeld: options.sharedLockHeld === true });
  }

  async stopProxy(options = {}) {
    return this.withSharedOperationLock('proxy stop', async () => {
    const service = await this.proxyServiceStatus();
    if (service.available && service.running) {
      await this.controlAuthoritativeProxy('stop', service);
    }
    await this.proxy.stop();
      return this.getState();
    }, { lockHeld: options.sharedLockHeld === true });
  }

  async restartProxy(options = {}) {
    return this.withSharedOperationLock('proxy restart', async () => {
    await this.ensureServerHome();
    await this.enforceRunningFrontendProxyIsolation();
    const env = await this.readEnv();
    const config = this.proxyConfigFromEnv(env);
    let service = await this.proxyServiceStatus();
    if (service.available) {
      if (service.installed && service.updateRequired) {
        await this.runElevatedWindowsProxyServiceCommand('refresh-service');
        service = await this.proxyServiceStatus();
      }
      if (service.running) await this.controlAuthoritativeProxy('stop', service);
      await this.controlAuthoritativeProxy('start', service);
    } else {
      await this.proxy.start(config);
    }
      return this.getState();
    }, { lockHeld: options.sharedLockHeld === true });
  }

  async installProxyService(options = {}) {
    return this.withSharedOperationLock('proxy install-service', async () => {
    await this.ensureServerHome();
    await this.enforceRunningFrontendProxyIsolation();
    const inProcessWasRunning = this.proxy.status().running;
    if (inProcessWasRunning) await this.proxy.stop();
    try {
      await this.runElevatedWindowsProxyServiceCommand('install-service');
    } catch (error) {
      // A cancelled elevation prompt or install failure must not turn a
      // functioning proxy into an outage.
      if (inProcessWasRunning) {
        const env = await this.readEnv();
        await this.proxy.start(this.proxyConfigFromEnv(env)).catch(() => {});
      }
      throw error;
    }
      return this.getState();
    }, { lockHeld: options.sharedLockHeld === true });
  }

  /** Close a live public Docker binding before any managed proxy starts. */
  async enforceRunningFrontendProxyIsolation(forceRecreate = false) {
    const env = await this.readEnv();
    if (!envTruthy(env.OMLORIX_LAUNCHER_PROXY_ENABLED)) return false;
    const currentBind = String(env.FRONTEND_HTTP_HOST_BIND || '127.0.0.1').trim();
    if (currentBind !== '127.0.0.1') {
      await this.writeEnv({ FRONTEND_HTTP_HOST_BIND: '127.0.0.1' });
    }
    const prepared = await this.prepareCompose();
    // Fresh production homes always persist the bind key. Keep missing-key
    // development/test homes side-effect free, while checking the actual live
    // Docker publication for every real installation because .env can already
    // say loopback even when a stale container still exposes another address.
    const liveBindingIsPublic = Object.hasOwn(env, 'FRONTEND_HTTP_HOST_BIND')
      ? await this.runningFrontendHasPublicBinding(prepared.args)
      : false;
    if (!forceRecreate && currentBind === '127.0.0.1' && !liveBindingIsPublic) return false;
    const container = await this.execDocker([...prepared.args, 'ps', '-q', 'frontend'], { timeoutMs: 15000 });
    if (!container.ok || !String(container.stdout || '').trim()) return false;
    const recreated = await this.execDocker(
      [...prepared.args, 'up', '-d', '--no-deps', '--force-recreate', 'frontend'],
      { timeoutMs: 120000 },
    );
    if (!recreated.ok) {
      const error = new Error('Could not close the direct frontend listener before starting the managed proxy.');
      error.messageKey = 'launcher_ui_proxy_frontend_isolation_failed';
      throw error;
    }
    return true;
  }

  /** Return true unless a running frontend is proven to bind only loopback. */
  async runningFrontendHasPublicBinding(composeArguments) {
    const container = await this.execDocker(
      [...composeArguments, 'ps', '-q', 'frontend'],
      { timeoutMs: 15000 },
    );
    const containerId = container.ok
      ? String(container.stdout || '').trim().split(/\s+/).filter(Boolean)[0] || ''
      : '';
    if (!containerId) return false;
    const inspected = await this.execDocker([
      'inspect', '--format', '{{json .HostConfig.PortBindings}}', containerId,
    ], { timeoutMs: 15000 });
    if (!inspected.ok) return true;
    try {
      const bindings = JSON.parse(String(inspected.stdout || '').trim());
      return Object.values(bindings || {}).flat().some((binding) => {
        const host = String(binding?.HostIp || '').trim().toLowerCase();
        return !(host === '::1' || host === '::ffff:127.0.0.1' || /^127\./.test(host));
      });
    } catch {
      return true;
    }
  }

  async uninstallProxyService(options = {}) {
    return this.withSharedOperationLock('proxy uninstall-service', async () => {
    await this.ensureServerHome();
    const service = await this.proxyServiceStatus();
    if (service.installed) await this.runElevatedWindowsProxyServiceCommand('uninstall-service');
    const env = await this.readEnv();
    const config = this.proxyConfigFromEnv(env);
    if (config.enabled && config.autostart) {
      const nextService = await this.proxyServiceStatus();
      if (nextService.available) await this.controlAuthoritativeProxy('start', nextService);
      else await this.proxy.start(config);
    }
      return this.getState();
    }, { lockHeld: options.sharedLockHeld === true });
  }

  setupScriptCommand() {
    if (process.platform === 'win32') {
      const scriptPath = path.join(this.serverHome, 'script', 'server-launcher', 'start.ps1');
      return {
        command: 'powershell.exe',
        args: ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', scriptPath, '-SetupOnly', '-ServerHome', this.serverHome],
      };
    }

    return {
      command: 'sh',
      args: [path.join(this.serverHome, 'script', 'server-launcher', 'start.sh')],
    };
  }

  async exportEnv(targetPath) {
    await this.ensureServerHome();
    const normalizedTarget = String(targetPath || '').trim();
    if (!normalizedTarget) {
      throw new Error('Choose a file path for the .env export.');
    }

    const raw = await fs.readFile(this.envFile, 'utf8').catch((error) => {
      if (error?.code === 'ENOENT') return '';
      throw error;
    });
    await fs.writeFile(normalizedTarget, raw, 'utf8');
    return {
      ok: true,
      filePath: normalizedTarget,
      byteLength: Buffer.byteLength(raw, 'utf8'),
    };
  }

  async setupEnvironment() {
    return this.withSharedOperationLock('setup', async () => {
      if (this.activeOperation) {
        throw new Error(`Another operation is already running: ${this.activeOperation}`);
      }
      await this.ensureServerHome();

      this.activeOperation = 'Setup';
      this.emit('operation-start', { name: 'Setup' });
      return new Promise((resolve, reject) => {
      const setup = this.setupScriptCommand();
      let output = '';
      const child = spawn(setup.command, setup.args, {
        cwd: this.serverHome,
        windowsHide: true,
        env: {
          ...process.env,
          OMLORIX_SETUP_ONLY: '1',
          OMLORIX_SERVER_HOME: this.serverHome,
          OMLORIX_ENV_FILE: this.envFile,
        },
      });

      const write = (stream, chunk) => {
        const text = chunk.toString();
        output += text;
        this.emit('operation-output', { name: 'Setup', stream, text });
      };

      child.stdout.on('data', (chunk) => write('stdout', chunk));
      child.stderr.on('data', (chunk) => write('stderr', chunk));
      child.on('error', (error) => {
        this.activeOperation = null;
        this.emit('operation-end', { name: 'Setup', ok: false, code: -1, message: error.message });
        reject(error);
      });
      child.on('close', async (code) => {
        this.activeOperation = null;
        const ok = code === 0;
        const message = ok
          ? 'Environment setup finished.'
          : output.trim() || `Environment setup failed with exit code ${code}.`;
        this.emit('operation-end', { name: 'Setup', ok, code, message });
        if (!ok) {
          reject(new Error(message));
          return;
        }
        // Normalize every required secret before rendering readiness. This also
        // closes the small handoff window between the setup subprocess exiting
        // and the Launcher reading the newly written environment file.
        await this.ensureGeneratedSecrets();
        resolve({
          state: await this.getState(),
          editor: await this.getEnvEditor(),
        });
      });
      });
    });
  }

  async runOperation(name, args, options = {}) {
    const {
      successMessage = '',
      successMessageKey = '',
      successMessageValues = {},
      onSuccess = null,
      onError = null,
      resultBuilder = null,
      sharedLockHeld = false,
      failureLogServices = [],
      failureLogComposeArgs = [],
    } = options;
    return this.withSharedOperationLock(name.toLowerCase(), async () => {
      if (this.activeOperation) {
        throw new Error(`Another operation is already running: ${this.activeOperation}`);
      }
      await this.ensureServerHome();
      await this.validateProfileEnv();
      this.activeOperation = name;
      this.emit('operation-start', { name });
      return new Promise((resolve, reject) => {
      const dockerExecutable = dockerCommand();
      let output = '';
      let stdout = '';
      let stderr = '';
      const captureStructuredOutput = typeof resultBuilder === 'function';
      // Structured results are parsed from stdout. TTY progress redraw frames
      // must never be interleaved with that machine-readable payload.
      const spawnArgs = captureStructuredOutput ? args : terminalComposeArgs(args);
      const child = spawn(dockerExecutable, spawnArgs, {
        cwd: this.serverHome,
        windowsHide: true,
        env: dockerSpawnEnv(dockerExecutable),
      });
      let settled = false;

      const finishFailure = async (sourceError, code = -1) => {
        if (settled) return;
        settled = true;
        let error = sourceError instanceof Error ? sourceError : new Error(String(sourceError || `${name} failed.`));
        if (typeof onError === 'function') {
          try {
            error = await onError(error) || error;
          } catch (transformError) {
            error = transformError;
          }
        }
        this.activeOperation = null;
        this.emit('operation-end', operationFailurePayload(name, error, code));
        reject(error);
      };

      const finishSuccess = async () => {
        if (settled) return;
        try {
          if (typeof onSuccess === 'function') {
            await onSuccess();
          }
          if (settled) return;
          this.activeOperation = null;
          const operationState = await this.getState();
          if (settled) return;
          const result = typeof resultBuilder === 'function'
            ? await resultBuilder({ state: operationState, stdout, stderr })
            : operationState;
          this.emit('operation-end', {
            name,
            ok: true,
            code: 0,
            message: successMessage || `${name} finished.`,
            ...(successMessageKey ? {
              messageKey: successMessageKey,
              messageValues: successMessageValues,
            } : {}),
          });
          settled = true;
          resolve(result);
        } catch (error) {
          await finishFailure(error, -1);
        }
      };

      const write = (stream, chunk) => {
        const text = chunk.toString();
        output += text;
        // Most long-running Docker operations only need the existing combined
        // failure log. Capture split streams only for commands whose caller
        // requested a structured success result, such as backup creation.
        if (captureStructuredOutput) {
          if (stream === 'stdout') {
            stdout += text;
          } else {
            stderr += text;
          }
        }
        this.emit('operation-output', { name, stream, text });
      };

      child.stdout.on('data', (chunk) => write('stdout', chunk));
      child.stderr.on('data', (chunk) => write('stderr', chunk));
      child.on('error', (error) => {
        void finishFailure(error, -1);
      });
      child.on('close', async (code) => {
        if (settled) return;
        if (code !== 0) {
          // Compose reports dependency failures in its own progress output but
          // does not include the stopped container's stdout/stderr. Fetch the
          // bounded log tail before ending the operation so the renderer shows
          // the useful application error ahead of the generic exit-code error.
          await this.emitFailedComposeServiceLogs(
            name,
            failureLogComposeArgs,
            failureLogServices,
            output,
          );
          const env = await this.readEnv().catch(() => ({}));
          const accessMessage = dockerRegistryAccessErrorMessage(output, env);
          const error = new Error(accessMessage || `${name} failed with exit code ${code}.`);
          error.operationOutput = output;
          await finishFailure(error, code);
          return;
        }
        await finishSuccess();
      });
      });
    }, { lockHeld: sharedLockHeld });
  }

  /**
   * Append recent logs for one-shot Compose services that caused an operation
   * to fail.
   *
   * Compose's streamed `up` output names a failed dependency but omits its
   * actual logs. A status query confirms the non-zero exit when possible, and
   * the original Compose output remains a fallback if that query itself fails.
   * Diagnostic collection is deliberately best-effort: losing Docker while
   * collecting logs must never replace the original startup error.
   */
  async emitFailedComposeServiceLogs(operationName, composeBaseArgs, candidateServices, composeOutput = '') {
    const baseArgs = Array.isArray(composeBaseArgs) ? composeBaseArgs : [];
    const candidates = [...new Set(
      (Array.isArray(candidateServices) ? candidateServices : [])
        .map((service) => String(service || '').trim())
        .filter((service) => /^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(service)),
    )];
    if (!baseArgs.length || !candidates.length) return;

    try {
      // Newer Compose versions explicitly report `service "name" didn't
      // complete successfully`. Preserve that evidence in case `ps` becomes
      // unavailable immediately after the failed `up` command.
      const namedFailures = new Set();
      const failurePattern = /service ["']([^"']+)["'] didn't complete successfully/gi;
      for (const match of String(composeOutput || '').matchAll(failurePattern)) {
        if (candidates.includes(match[1])) namedFailures.add(match[1]);
      }

      const status = await this.execDocker(
        [...baseArgs, 'ps', '--all', '--format', 'json', ...candidates],
        { timeoutMs: 10000 },
      );
      if (status.ok) {
        for (const row of parseComposeJson(status.stdout)) {
          const service = String(row?.Service || '').trim();
          const exitCode = Number(row?.ExitCode);
          if (candidates.includes(service) && Number.isFinite(exitCode) && exitCode !== 0) {
            namedFailures.add(service);
          }
        }
      }

      for (const service of namedFailures) {
        const logs = await this.execDocker(
          [...baseArgs, 'logs', '--tail', String(COMPOSE_FAILURE_LOG_TAIL), '--no-color', service],
          { timeoutMs: 15000 },
        );
        const logText = [logs.stdout, logs.stderr]
          .filter(Boolean)
          .join(logs.stdout && logs.stderr ? '\n' : '')
          .trimEnd();
        if (!logText) continue;

        // Reuse the launcher's translated Logs heading while keeping container
        // output byte-for-byte readable underneath the failed service name.
        const detailedLogs = `${service}:\n${logText}`;
        this.emitOperationOutput(
          operationName,
          'stderr',
          `\n> Logs\n${detailedLogs}\n`,
          'launcher_ui_logs_value1',
          { value1: detailedLogs },
        );
      }
    } catch {
      // Best-effort diagnostics must not obscure the original Compose failure.
    }
  }

  emitOperationOutput(name, stream, text, textKey = '', textValues = {}) {
    this.emit('operation-output', {
      name,
      stream,
      text,
      ...(textKey ? { textKey, textValues } : {}),
    });
  }

  async runDockerStep(label, args, timeoutMs = 120000, operationName = 'Update', textKey = '', textValues = {}) {
    this.emitOperationOutput(operationName, 'stdout', `\n${label}\n`, textKey, textValues);
    const result = await this.execDocker(terminalComposeArgs(args), { timeoutMs });
    if (result.stdout) this.emitOperationOutput(operationName, 'stdout', result.stdout);
    if (result.stderr) this.emitOperationOutput(operationName, 'stderr', result.stderr);
    if (!result.ok) {
      const env = await this.readEnv().catch(() => ({}));
      const error = new Error(dockerRegistryAccessErrorMessage(`${result.stderr}\n${result.stdout}`, env) || result.stderr || result.stdout || `${label} failed.`);
      // Restore coordination must distinguish a confirmed rollback from an
      // interrupted or unsafe failure before deciding whether to restart the
      // application services. Preserve the raw structured command result for
      // that decision without putting it into the user-facing error message.
      error.dockerResult = result;
      throw error;
    }
    return result;
  }

  async start() {
    return this.withSharedOperationLock('start', async () => {
      // Fail closed before generating or rewriting anything when the operator's
      // environment is incomplete.
      await this.validateProfileEnv();
      await this.ensureGeneratedSecrets();
      await this.repairBundledRedisUrl();
      const currentEnv = await this.readEnv();
      await this.writeEnv({
        FRONTEND_HTTP_HOST_PORT: currentEnv.FRONTEND_HTTP_HOST_PORT || '8080',
      });
      const { env, args } = await this.prepareCompose();
      await this.runDockerStep(
        'Stopping application services before migration',
        offlineMigrationDrainArgs(args),
        120000,
        'Start',
        'launcher_update_stopping_services',
      );
      await this.runDockerStep(
        'Resetting migration container',
        [...args, 'rm', '-sf', 'migrate'],
        30000,
        'Start',
        'launcher_migration_resetting',
      );
      await this.runDockerStep(
        'Running migrations',
        [...args, 'up', '-d', '--force-recreate', 'migrate'],
        180000,
        'Start',
        'launcher_migration_running',
      );
      const startArgs = [...args, 'up', '-d'];
      if (envTruthy(env.OMLORIX_ALLOW_PROJECT_ADOPTION)) startArgs.push('--force-recreate');
      startArgs.push('--remove-orphans');
      return this.runOperation(
      'Start',
      startArgs,
      {
        sharedLockHeld: true,
        successMessage: 'Omlorix started.',
        successMessageKey: 'launcher_start_finished',
        failureLogServices: ['migrate'],
        failureLogComposeArgs: args,
        onSuccess: async () => {
          await this.finalizeProjectAdoption();
          const readyUrl = await this.waitForReady();
          const currentEnv = await this.readEnv();
          if (
            envTruthy(currentEnv.OMLORIX_LAUNCHER_PROXY_ENABLED)
            || Boolean(String(currentEnv.FRONTEND_TRUSTED_UPSTREAMS || '').trim())
          ) {
            await this.convergeVisitorIps('Start', { lockHeld: true });
          }
          this.emitOperationOutput(
            'Start',
            'stdout',
            `\nOmlorix is ready at ${readyUrl}\n`,
            'launcher_operation_ready_at',
            { url: readyUrl },
          );
          await this.recordSuccessfulServerVersion(env.OMLORIX_VERSION);
        },
        onError: (error) => this.possibleDatabaseDowngradeError(error, env),
      }
      );
    });
  }

  async stop() {
    return this.withSharedOperationLock('stop', async () => {
      const { args } = await this.prepareCompose();
      await this.runOperation(
        'Stop',
        [...args, 'down', '--remove-orphans'],
        { successMessage: 'Omlorix stopped.', sharedLockHeld: true }
      );
      await this.finalizeProjectAdoption();
      // Full shutdown includes every launcher-managed public listener. Keeping
      // the proxy alive after Compose exits produces a misleading 502 endpoint.
      await this.stopProxy({ sharedLockHeld: true });
      return this.getState();
    });
  }

  async restart() {
    return this.withSharedOperationLock('restart', async () => {
      if (this.activeOperation) {
      throw new Error(`Another operation is already running: ${this.activeOperation}`);
      }
    this.activeOperation = 'Restart';
    this.emit('operation-start', { name: 'Restart' });
    let env = {};
    try {
      await this.repairBundledRedisUrl();
      await this.validateProfileEnv();
      await this.ensureIngressAuthenticationCredential();
      ({ env } = await this.prepareCompose());
      // Restart is the explicit point at which imported proxy settings become
      // live. Stop either proxy implementation before replacing containers so
      // it cannot keep serving with an old authentication secret or listener
      // configuration while the frontend changes underneath it.
      const proxyConfig = this.proxyConfigFromEnv(env);
      const proxyServiceBeforeRestart = await this.proxyServiceStatus();
      const proxyWasRunning = Boolean(
        proxyServiceBeforeRestart.running || this.proxy.status().running,
      );
      const resumeManagedProxy = Boolean(
        proxyConfig.enabled && (proxyWasRunning || proxyConfig.autostart),
      );
      if (proxyWasRunning) {
        await this.stopProxy({ sharedLockHeld: true });
      }
      const args = composeArgs(this.serverHome, env);
      await this.runDockerStep(
        'Stopping application services before migration',
        offlineMigrationDrainArgs(args),
        120000,
        'Restart',
        'launcher_update_stopping_services',
      );
      await this.runDockerStep(
        'Resetting migration container',
        [...args, 'rm', '-sf', 'migrate'],
        30000,
        'Restart',
        'launcher_migration_resetting',
      );
      await this.runDockerStep(
        'Running migrations',
        [...args, 'up', '-d', '--force-recreate', 'migrate'],
        180000,
        'Restart',
        'launcher_migration_running',
      );
      const restartArgs = [...args, 'up', '-d', '--force-recreate', '--remove-orphans'];
      await this.runDockerStep(
        'Recreating application containers',
        restartArgs,
        180000,
        'Restart',
        'launcher_restart_recreating_containers',
      );
      await this.finalizeProjectAdoption();
      const readyUrl = await this.waitForReady();
      if (resumeManagedProxy) {
        await this.startProxy({ sharedLockHeld: true });
      }
      const currentEnv = await this.readEnv();
      if (
        envTruthy(currentEnv.OMLORIX_LAUNCHER_PROXY_ENABLED)
        || Boolean(String(currentEnv.FRONTEND_TRUSTED_UPSTREAMS || '').trim())
      ) {
        await this.convergeVisitorIps('Restart', { lockHeld: true });
      }
      await this.recordSuccessfulServerVersion(env.OMLORIX_VERSION);
      this.emitOperationOutput(
        'Restart',
        'stdout',
        `\nOmlorix is ready at ${readyUrl}\n`,
        'launcher_operation_ready_at',
        { url: readyUrl },
      );
      this.activeOperation = null;
      const state = await this.getState();
      this.emit('operation-end', {
        name: 'Restart',
        ok: true,
        code: 0,
        message: 'Omlorix restarted.',
        messageKey: 'launcher_restart_finished',
      });
      return state;
    } catch (error) {
      const failure = await this.possibleDatabaseDowngradeError(error, env);
      this.emit('operation-end', operationFailurePayload('Restart', failure));
      throw failure;
      } finally {
        this.activeOperation = null;
      }
    });
  }

  async repairVisitorIps() {
    if (this.activeOperation) {
      throw new Error(`Another operation is already running: ${this.activeOperation}`);
    }
    // Reserve the operation before the first asynchronous action. This closes
    // the race where a start/restart could begin while detection was underway.
    this.activeOperation = 'Visitor IP repair';
    let releaseSharedLock = () => {};
    try {
      releaseSharedLock = acquireSharedOperationLock(
        this.serverHome,
        'visitor-ip repair',
      );
    } catch (error) {
      this.activeOperation = null;
      throw error;
    }
    this.emit('operation-start', { name: this.activeOperation });
    try {
      await this.ensureServerHome();
      await this.validateProfileEnv();
      await this.convergeVisitorIps(this.activeOperation, { lockHeld: true });
      const repairedEnv = await this.readEnv();
      const managedProxyEnabled = envTruthy(repairedEnv.OMLORIX_LAUNCHER_PROXY_ENABLED);
      this.activeOperation = null;
      const state = await this.getState();
      this.emit('operation-end', {
        name: 'Visitor IP repair',
        ok: true,
        code: 0,
        message: managedProxyEnabled
          ? 'Visitor IP forwarding was verified.'
          : 'Visitor IP trust was applied. Verify the external proxy from an external client.',
        messageKey: managedProxyEnabled
          ? 'launcher_visitor_ip_message_proxy_running'
          : 'launcher_visitor_ip_repair_external_applied',
      });
      return state;
    } catch (error) {
      this.emit('operation-end', operationFailurePayload('Visitor IP repair', error));
      throw error;
    } finally {
      releaseSharedLock();
      this.activeOperation = null;
    }
  }

  /**
   * Apply the authenticated ingress contract, recreate the affected services,
   * and require a successful end-to-end verification. At most two topology
   * passes are allowed; failure restores the previous environment snapshot.
   */
  async convergeVisitorIps(operationName = 'Visitor IP repair', { lockHeld = false } = {}) {
    const releaseSharedLock = lockHeld
      ? () => {}
      : acquireSharedOperationLock(this.serverHome, 'visitor-ip repair');
    let originalRaw = '';
    let originalEnv = {};
    let launcherProxyEnabled = false;
    let wasRunning = false;
    let envChanged = false;

    try {
      originalRaw = await fs.readFile(this.envFile, 'utf8');
      originalEnv = {
        ...parseEnv(originalRaw),
        ...proxySettingsEnv(await this.readServerSettings()),
      };
      launcherProxyEnabled = envTruthy(originalEnv.OMLORIX_LAUNCHER_PROXY_ENABLED);
      const externalProxyEnabled = Boolean(
        String(originalEnv.FRONTEND_TRUSTED_UPSTREAMS || '').trim(),
      );
      const stackBefore = await this.stackStatus({ includeDiagnostics: false });
      wasRunning = (stackBefore.running || 0) > 0;
      let expectedFrontendIp = '';
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const frontendIp = await this.getComposeServiceIp('frontend');
        if (!frontendIp) {
          throw new Error('Could not identify the frontend on the named Omlorix Docker network.');
        }
        if (expectedFrontendIp && frontendIp === expectedFrontendIp) break;
        expectedFrontendIp = frontendIp;

        const env = await this.readEnv();
        const launcherSecret = String(env.OMLORIX_LAUNCHER_PROXY_SECRET || '');
        if ((launcherProxyEnabled || externalProxyEnabled) && !/^[0-9a-f]{64}$/i.test(launcherSecret)) {
          throw new Error('The launcher proxy authentication credential is unavailable.');
        }
        const proxyCidr = `${frontendIp}/${net.isIP(frontendIp) === 6 ? '128' : '32'}`;
        const updates = {
          TRUST_PROXY_HEADERS: 'true',
          TRUSTED_PROXIES: proxyCidr,
          RATE_LIMIT_TRUSTED_PROXIES: proxyCidr,
          AUTH_TRUSTED_PROXIES: proxyCidr,
          UVICORN_FORWARDED_ALLOW_IPS: frontendIp,
          FRONTEND_TRUST_PROXY_HEADERS: String(launcherProxyEnabled || externalProxyEnabled),
          ...(launcherProxyEnabled ? { FRONTEND_HTTP_HOST_BIND: '127.0.0.1' } : {}),
        };
        if (!envChanged) await this.createEnvBackup(originalRaw);
        await this.writeEnv(updates);
        envChanged = true;

        if (wasRunning) {
          const { env: composeEnv } = await this.prepareCompose();
          const args = composeArgs(this.serverHome, composeEnv);
          await this.runDockerStep(
            'Applying authenticated visitor IP settings',
            [...args, 'up', '-d', '--force-recreate', 'fastapi', 'frontend'],
            180000,
            operationName,
          );
          await this.waitForReady();
        }

        const finalFrontendIp = await this.getComposeServiceIp('frontend');
        if (finalFrontendIp === expectedFrontendIp) break;
        if (attempt === 1) {
          throw new Error('The Docker network topology did not stabilize after two attempts.');
        }
      }

      if (launcherProxyEnabled && wasRunning) {
        const env = await this.readEnv();
        const stack = await this.stackStatus();
        const proxy = this.proxyStatus(env, await this.proxyServiceStatus());
        const verification = await this.verifyVisitorIpPath(env, stack, proxy, { force: true });
        if (!verification.verified) {
          throw new Error('End-to-end visitor IP and scheme verification failed.');
        }
      }
    } catch (error) {
      if (envChanged) {
        await this.writeEnvContent(originalRaw).catch(() => {});
        if (wasRunning) {
          const args = composeArgs(this.serverHome, originalEnv);
          await this.execDocker(
            [...args, 'up', '-d', '--force-recreate', 'fastapi', 'frontend'],
            { timeoutMs: 180000 },
          ).catch(() => {});
        }
      }
      const failure = new Error('Visitor IP settings could not be applied and verified. The previous configuration was restored.');
      failure.code = 'VISITOR_IP_CONVERGENCE_FAILED';
      failure.messageKey = 'launcher_visitor_ip_repair_failed';
      failure.cause = error;
      throw failure;
    } finally {
      releaseSharedLock();
    }
  }

  async fetchJson(url, timeoutMs = 10000, requestOptions = {}) {
    return fetchJson(url, timeoutMs, requestOptions);
  }

  async latestReleaseInfo(channelInput = DEFAULT_CHANNEL) {
    const channel = normalizeUpdateChannel(channelInput);
    const releaseInfo = await resolveReleaseInfo({
      channel,
      fetcher: this.fetchJson.bind(this),
    });
    let manifest = releaseInfo.manifest;
    const manifestUrl = releaseInfo.manifestUrl;
    if (manifestUrl) {
      manifest = await this.fetchJson(manifestUrl, 10000);
    }
    if (!manifest && (releaseInfo.minimumLauncherVersion || releaseInfo.launcherUpdateReason)) {
      manifest = {
        version: releaseInfo.version,
        channel,
        minimumLauncherVersion: releaseInfo.minimumLauncherVersion || '0.0.0',
        launcherUpdateReason: releaseInfo.launcherUpdateReason || '',
      };
    }
    return {
      channel,
      version: releaseInfo.version,
      manifest,
      releaseUrl: releaseInfo.releaseUrl || '',
      launcherVersion: releaseInfo.launcherVersion || manifest?.launcherVersion || '',
      launcherReleaseTag: releaseInfo.launcherReleaseTag || manifest?.launcherReleaseTag || '',
      launcherReleaseUrl: releaseInfo.launcherReleaseUrl || manifest?.launcherReleaseUrl || '',
    };
  }

  async getAvailableVersions(channelInput = '', options = {}) {
    await this.ensureServerHome();
    const settings = await this.readServerSettings();
    const channel = normalizeUpdateChannel(channelInput || settings.updateChannel);
    const pendingRequest = this.availableVersionsRequests.get(channel);
    if (pendingRequest) return pendingRequest;

    const force = options?.force === true;
    const maxAgeMs = Math.max(
      0,
      Number(options?.maxAgeMs ?? AVAILABLE_VERSIONS_CACHE_MAX_AGE_MS) || 0,
    );
    const failureMaxAgeMs = Math.max(
      0,
      Number(options?.failureMaxAgeMs ?? RELEASE_CHECK_FAILURE_COOLDOWN_MS) || 0,
    );
    const checkedAt = this.now();
    const cached = this.availableVersionsCache.get(channel);
    if (!force && cached && checkedAt - cached.checkedAt < maxAgeMs) {
      return cached.result;
    }

    const failed = this.availableVersionsFailures.get(channel);
    if (!force && failed && checkedAt - failed.checkedAt < failureMaxAgeMs) {
      throw failed.error;
    }

    const request = (async () => {
      try {
        const result = {
          channel,
          versions: await resolveAvailableVersions({
            channel,
            fetcher: this.fetchJson.bind(this),
          }),
        };
        this.availableVersionsCache.set(channel, {
          checkedAt: this.now(),
          result,
        });
        this.availableVersionsFailures.delete(channel);
        return result;
      } catch (error) {
        this.availableVersionsFailures.set(channel, {
          checkedAt: this.now(),
          error,
        });
        throw error;
      } finally {
        if (this.availableVersionsRequests.get(channel) === request) {
          this.availableVersionsRequests.delete(channel);
        }
      }
    })();
    this.availableVersionsRequests.set(channel, request);
    return request;
  }

  assertLauncherCompatible(releaseInfo) {
    const minimumLauncherVersion = String(releaseInfo?.manifest?.minimumLauncherVersion || '').trim();
    if (!minimumLauncherVersion || minimumLauncherVersion === '0.0.0') return;
    const currentLauncherVersion = this.app.getVersion();
    if (compareVersions(currentLauncherVersion, minimumLauncherVersion) >= 0) return;
    throw new LauncherUpdateRequiredError({
      currentLauncherVersion,
      minimumLauncherVersion,
      targetVersion: releaseInfo.version,
      releaseNotes: releaseInfo.manifest?.launcherUpdateReason || releaseInfo.manifest?.notes || '',
    });
  }

  async getServerUpdateInfo(channelInput = '', options = {}) {
    if (this.activeOperation) {
      throw new Error(`Another operation is already running: ${this.activeOperation}`);
    }
    await this.ensureServerHome();
    await this.validateProfileEnv();
    const env = await this.readEnv();
    const settings = await this.readServerSettings();
    const currentVersion = env.OMLORIX_VERSION || 'stable';
    const releaseInfo = await this.latestReleaseInfo(channelInput || settings.updateChannel);
    let launcherRequirement = null;
    try {
      this.assertLauncherCompatible(releaseInfo);
    } catch (error) {
      // Scheduled and manual update execution must still fail closed when the
      // selected server release needs a newer launcher. The dashboard check is
      // read-only, however, and needs the compatibility details so it can show
      // an actionable banner instead of silently losing the available update.
      if (error?.code !== 'LAUNCHER_UPDATE_REQUIRED' || !options.allowLauncherUpdateRequired) {
        throw error;
      }
      launcherRequirement = {
        currentLauncherVersion: error.currentLauncherVersion || '',
        minimumLauncherVersion: error.minimumLauncherVersion || '',
        targetVersion: error.targetVersion || releaseInfo.version,
        releaseNotes: error.releaseNotes || '',
      };
    }
    const latestVersion = releaseInfo.version;

    // "stable" is a moving image tag. Treat it as updateable because the
    // launcher cannot prove whether the local stable image matches GitHub.
    const updateAvailable = currentVersion === releaseInfo.channel || currentVersion === 'stable'
      || compareVersions(latestVersion, currentVersion) > 0;

    return {
      channel: releaseInfo.channel,
      channelLabel: channelLabel(releaseInfo.channel),
      currentVersion,
      latestVersion,
      updateAvailable,
      releaseUrl: releaseInfo.releaseUrl,
      releaseNotes: releaseInfo.manifest?.notes || '',
      minimumLauncherVersion: releaseInfo.manifest?.minimumLauncherVersion || '',
      launcherUpdateReason: releaseInfo.manifest?.launcherUpdateReason || '',
      launcherRequirement,
    };
  }

  async runUpdateStep(
    label,
    args,
    timeoutMs = 120000,
    messageKey = null,
    operationName = 'Update',
  ) {
    return this.runDockerStep(label, args, timeoutMs, operationName, messageKey);
  }

  async waitForReady(timeoutMs = 120000, pollIntervalMs = 5000) {
    const env = await this.readEnv();
    const readyUrl = `${this.resolveUrl(env).replace(/\/$/, '')}/ready`;
    const started = Date.now();
    let lastStatus = 'not reachable';
    while (true) {
      // stackStatus uses the same service denominator and /ready probe shown by
      // the dashboard, so a successful operation cannot be followed by an
      // immediately contradictory `stack.healthy=false` state.
      const stack = await this.stackStatus({ includeDiagnostics: false });
      if (stack.healthy) {
        return readyUrl;
      }
      if (stack.composeError) {
        lastStatus = stack.composeError;
      } else if (Number(stack.missing || 0) > 0) {
        lastStatus = `${stack.missing} of ${stack.total} configured services are missing`;
      } else if (Number(stack.running || 0) !== Number(stack.total || 0)) {
        lastStatus = `${stack.running || 0} of ${stack.total || 0} configured services are running`;
      } else if (Number(stack.healthIssues || 0) > 0) {
        lastStatus = `${stack.healthIssues} configured service(s) are still starting or unhealthy`;
      } else {
        lastStatus = stack.httpStatus ? `HTTP ${stack.httpStatus}` : 'not reachable';
      }
      const elapsed = Date.now() - started;
      if (elapsed >= timeoutMs) break;
      await new Promise((resolve) => setTimeout(
        resolve,
        Math.max(0, Math.min(pollIntervalMs, timeoutMs - elapsed)),
      ));
    }
    throw new Error(`The complete Omlorix stack did not become ready at ${readyUrl} (${lastStatus}).`);
  }

  /**
   * Require the Omlorix stack to already be up before a live-server action.
   *
   * The updater uses `fastapi` for backup and migration orchestration, so a
   * stopped stack would fail partway through after the user has already been
   * prompted to wait. Failing fast keeps the UI honest and avoids a partial
   * update attempt with a confusing Docker error.
   */
  async assertUpdatePrerequisites(actionDescription = 'update it') {
    const state = await this.getState();
    const stack = state?.stack || {};
    const fastapiRunning = Array.isArray(stack.services)
      ? stack.services.some((service) => {
        const name = String(service.Service || service.Name || service.Names || '').toLowerCase();
        const stateName = String(service.State || '').toLowerCase();
        return (name === 'fastapi' || name.includes('fastapi')) && stateName === 'running';
      })
      : false;
    if (stack.running <= 0 || !fastapiRunning) {
      throw new Error(`Omlorix must be running before you can ${actionDescription}. Start the stack first, then try again.`);
    }
  }

  async update(options = {}) {
    return this.withSharedOperationLock('update', async () => {
      if (this.activeOperation) {
      throw new Error(`Another operation is already running: ${this.activeOperation}`);
      }
    await this.ensureServerHome();
    await this.repairBundledRedisUrl();
    await this.validateProfileEnv();
    await this.assertUpdatePrerequisites();
    if (fssync.existsSync(path.join(this.serverHome, 'docker-compose.launcher-services.yml'))) {
      await this.ensureLauncherServicesNetwork();
    }
    const env = await this.readEnv();
    await this.validateComposeOwnership(env);
    const previous = env.OMLORIX_VERSION || 'stable';
    const settings = await this.readServerSettings();
    const previousChannel = settings.updateChannel;
    const targetChannel = normalizeUpdateChannel(options.channel || settings.updateChannel);
    const releaseInfo = await this.latestReleaseInfo(targetChannel);
    this.assertLauncherCompatible(releaseInfo);
    const next = releaseInfo.version;
    const nextChannel = normalizeUpdateChannel(releaseInfo.channel || targetChannel);

    if (!options.skipBackup) {
      await this.backup({
        destinationId: options.destinationId,
        encryptionEnabled: options.encryptionEnabled,
        sharedLockHeld: true,
      });
    }

    this.activeOperation = 'Update';
    this.emit('operation-start', { name: 'Update' });
    // A pull failure is unrelated to database compatibility. Only add the
    // downgrade diagnosis after the selected release starts touching the
    // database or application containers.
    let downgradeSensitivePhase = false;
    let rollbackRequiresDrain = false;
    let migrationMayHaveStarted = false;
    try {
      await this.writeEnv({ OMLORIX_VERSION: next });
      await this.updateServerSettings((current) => ({ ...current, updateChannel: nextChannel }));
      const args = composeArgs(this.serverHome, env);
      await this.runUpdateStep(`Pulling ${next}`, [...args, 'pull'], 180000);
      // Main and audit schema gates deliberately reject writes from the old
      // application version. Drain every writer/consumer before migrations so
      // the transition is fail-closed without surfacing transient audit errors.
      rollbackRequiresDrain = true;
      await this.runUpdateStep(
        'Stopping application services before migration',
        offlineMigrationDrainArgs(args),
        120000,
        'launcher_update_stopping_services',
      );
      await this.runUpdateStep(
        'Resetting migration container',
        [...args, 'rm', '-sf', 'migrate'],
        30000,
        'launcher_migration_resetting',
      );
      downgradeSensitivePhase = true;
      // From this point forward the database may contain committed changes
      // even when Compose reports a failure. Never select or start the old
      // image after crossing this boundary.
      migrationMayHaveStarted = true;
      await this.runUpdateStep(
        'Running migrations',
        [...args, 'up', '-d', '--force-recreate', '--remove-orphans', 'migrate'],
        180000,
        'launcher_migration_running',
      );
      await this.runUpdateStep(
        'Recreating application containers',
        [...args, 'up', '-d', '--force-recreate', '--remove-orphans'],
        180000,
        'launcher_migration_recreating_services',
      );
      await this.finalizeProjectAdoption();
      const readyUrl = await this.waitForReady();
      await this.recordSuccessfulServerVersion(next);
      this.emit('operation-output', { name: 'Update', stream: 'stdout', text: `\nOmlorix is ready at ${readyUrl}\n` });
      this.activeOperation = null;
      const state = await this.getState();
      this.emit('operation-end', { name: 'Update', ok: true, code: 0, message: 'Omlorix updated and started.' });
      return state;
    } catch (error) {
      const failure = downgradeSensitivePhase
        ? await this.possibleDatabaseDowngradeError(error, {
          ...env,
          OMLORIX_VERSION: next,
        })
        : error;
      let reportedFailure = failure;
      if (migrationMayHaveStarted) {
        this.emit('operation-output', {
          name: 'Update',
          stream: 'stderr',
          text: `\nUpdate failed after database migrations may have started. Keeping target release ${next} selected and leaving Omlorix offline.\n`,
        });
        const drain = await this.execDocker(
          offlineMigrationDrainArgs(composeArgs(this.serverHome, env)),
          { timeoutMs: 120000 },
        );
        const drainDetail = drain.ok
          ? 'Omlorix was left offline.'
          : 'Not every application container could be confirmed stopped; inspect Docker before retrying.';
        reportedFailure = new Error(
          `Update failed after database migrations may have started. Target release ${next} remains selected. ${drainDetail}`,
          { cause: failure },
        );
        reportedFailure.messageKey = 'launcher_update_rollback_left_offline';
        reportedFailure.messageValues = { targetVersion: next };
        reportedFailure.operationOutput = [
          failure.operationOutput,
          drain.stderr,
          drain.stdout,
        ].filter(Boolean).join('\n');
      } else if (previous && (previous !== next || previousChannel !== nextChannel)) {
        this.emit('operation-output', { name: 'Update', stream: 'stderr', text: `\nUpdate failed. Rolling image tag back to ${previous} on the ${previousChannel} channel; database migrations are not reverted.\n` });
        await this.writeEnv({ OMLORIX_VERSION: previous });
        await this.updateServerSettings((current) => ({
          ...current,
          updateChannel: previousChannel,
        }));
        const args = composeArgs(this.serverHome, env);
        if (rollbackRequiresDrain) {
          const drain = await this.execDocker(
            offlineMigrationDrainArgs(args),
            { timeoutMs: 120000 },
          );
          let rollbackStep = drain;
          if (drain.ok) {
            rollbackStep = await this.execDocker([...args, 'pull'], { timeoutMs: 180000 });
          }
          if (rollbackStep.ok) {
            rollbackStep = await this.execDocker(
              [...args, 'up', '-d', '--force-recreate', '--remove-orphans'],
              { timeoutMs: 180000 },
            );
          }
          if (!rollbackStep.ok) {
            reportedFailure = new Error(
              'Update failed and rollback could not be completed safely. Omlorix was left offline; inspect the operation logs before starting it.',
              { cause: failure },
            );
            reportedFailure.messageKey = 'launcher_update_pre_migration_rollback_left_offline';
            reportedFailure.messageValues = { previousVersion: previous };
            reportedFailure.operationOutput = [
              failure.operationOutput,
              rollbackStep.stderr,
              rollbackStep.stdout,
            ].filter(Boolean).join('\n');
          }
        }
      }
      this.emit('operation-end', operationFailurePayload('Update', reportedFailure));
      throw reportedFailure;
      } finally {
        this.activeOperation = null;
      }
    }, { lockHeld: options.sharedLockHeld === true });
  }

  /** Probe the active FILE_STORAGE_PROVIDER through the backend's safe test object. */
  async probeStorage() {
    return this.withSharedOperationLock('storage probe', async () => {
      await this.assertUpdatePrerequisites('probe file storage');
      const { args } = await this.prepareCompose();
      return this.runOperation(
        'Storage probe',
        [...args, 'exec', '-T', 'fastapi', 'python', '-m', 'app.files.cli', 'storage-probe'],
        {
          sharedLockHeld: true,
          successMessage: 'Storage probe finished.',
          successMessageKey: 'launcher_ui_storage_probe_finished',
          resultBuilder: async ({ state, stdout }) => {
            const payload = parseTrailingJsonObject(stdout);
            const status = String(payload?.probe?.status || '').toLowerCase();
            if (!payload || status !== 'ok') {
              const error = new Error('Omlorix did not return a successful storage probe result.');
              error.messageKey = 'launcher_ui_storage_probe_invalid';
              throw error;
            }
            return {
              state,
              probe: {
                provider: normalizeStorageProvider(payload.provider),
                status: 'ok',
              },
            };
          },
        },
      );
    });
  }

  /** Migrate storage-backed records through the same backend command as Make. */
  async migrateStorage(payload = {}) {
    const options = normalizeStorageMigrationOptions(payload);
    return this.withSharedOperationLock('storage migrate', async () => {
      await this.assertUpdatePrerequisites('migrate file storage');
      const { args } = await this.prepareCompose();
      const command = [
        ...args,
        'exec', '-T', 'fastapi', 'python', '-m', 'app.files.cli', 'migrate-files',
        '--from-provider', options.fromProvider,
        '--to-provider', options.toProvider,
        '--scope', options.scope,
        '--batch-size', String(options.batchSize),
        '--max-files', String(options.maxFiles),
        '--retries', String(options.retries),
      ];
      for (const [flag, value] of [
        ['--user-id', options.userId],
        ['--only-migrated-from', options.onlyMigratedFrom],
        ['--created-after', options.createdAfter],
        ['--created-before', options.createdBefore],
      ]) {
        if (value) command.push(flag, value);
      }
      if (options.dryRun) command.push('--dry-run');
      if (options.deleteSource) command.push('--delete-source');
      if (options.force) command.push('--force');

      return this.runOperation(
        options.dryRun ? 'Storage migration preview' : 'Storage migration',
        command,
        {
          sharedLockHeld: true,
          successMessage: options.dryRun ? 'Storage migration preview finished.' : 'Storage migration finished.',
          successMessageKey: options.dryRun
            ? 'launcher_ui_storage_migration_preview_finished'
            : 'launcher_ui_storage_migration_finished',
          resultBuilder: async ({ state, stdout }) => {
            const result = parseTrailingJsonObject(stdout);
            if (!result || typeof result.scanned !== 'number' || typeof result.failed !== 'number') {
              const error = new Error('Omlorix did not return a valid storage migration result.');
              error.messageKey = 'launcher_ui_storage_migration_invalid';
              throw error;
            }
            const count = (key) => {
              const value = Number(result[key]);
              return Number.isSafeInteger(value) && value >= 0 ? value : 0;
            };
            return {
              state,
              migration: {
                source_provider: options.fromProvider,
                destination_provider: options.toProvider,
                scope: options.scope,
                dry_run: options.dryRun,
                scanned: count('scanned'),
                would_migrate: count('would_migrate'),
                migrated: count('migrated'),
                resumed: count('resumed'),
                failed: count('failed'),
                deleted_source: count('deleted_source'),
                source_cleanup_failed: count('source_cleanup_failed'),
                objects: count('objects'),
              },
            };
          },
        },
      );
    });
  }

  /**
   * Return the enabled backup destinations and capabilities used by Admin.
   *
   * The one-shot CLI query deliberately returns display-only fields. Electron
   * never decrypts or receives cloud destination credentials.
   */
  async getBackupOptions() {
    const { args } = await this.prepareCompose();
    const result = await this.execDocker(
      [...args, 'exec', '-T', 'fastapi', 'python', '-m', 'app.backups.cli', 'options'],
      { timeoutMs: 30000 },
    );
    if (!result.ok) {
      throw new Error('Could not load backup destinations. Make sure Omlorix is running and ready.');
    }
    const payload = parseTrailingJsonObject(result.stdout);
    if (!payload || !Array.isArray(payload.destinations) || !payload.capabilities) {
      throw new Error('Omlorix returned an invalid backup destination response.');
    }
    return {
      destinations: payload.destinations
        .filter((destination) => destination && destination.id && destination.name)
        .map((destination) => ({
          id: String(destination.id),
          name: String(destination.name),
          provider: String(destination.provider || 'local'),
        })),
      capabilities: {
        archive_encryption_available: payload.capabilities.archive_encryption_available === true,
        archive_encryption_default_enabled: payload.capabilities.archive_encryption_default_enabled !== false,
        plaintext_archives_allowed: payload.capabilities.plaintext_archives_allowed === true,
      },
    };
  }

  /** Create a full backup through the same CLI and service used by Admin. */
  async backup(options = {}) {
    await this.assertUpdatePrerequisites('create a backup');
    const { args } = await this.prepareCompose();
    const destinationId = String(options?.destinationId || '').trim();
    if (destinationId.length > 255) {
      throw new Error('The selected backup destination is invalid.');
    }
    const encryptionEnabled = options?.encryptionEnabled !== false;
    const command = [
      ...args,
      'exec',
      '-T',
      'fastapi',
      'python',
      '-m',
      'app.backups.cli',
      'create',
      '--safe-output',
    ];
    if (destinationId) {
      command.push('--destination', destinationId);
    }
    if (!encryptionEnabled) {
      command.push('--no-encrypted');
    }
    return this.runOperation(
      'Backup',
      command,
      {
        sharedLockHeld: options.sharedLockHeld === true,
        successMessage: 'Backup finished.',
        successMessageKey: 'launcher_backup_finished',
        resultBuilder: async ({ state, stdout }) => {
          const backupResult = parseTrailingJsonObject(stdout);
          if (!backupResult || backupResult.status !== 'success' || !backupResult.job_id) {
            throw new Error('Omlorix did not return a valid completed backup result.');
          }
          const sizeBytes = Number(backupResult.size_bytes);
          return {
            state,
            // Do not forward legacy artifact URIs, internal errors, or any
            // unexpected future CLI fields into the renderer process.
            backup: {
              job_id: String(backupResult.job_id || ''),
              status: 'success',
              destination_id: backupResult.destination_id
                ? String(backupResult.destination_id)
                : null,
              encryption_enabled: backupResult.encryption_enabled !== false,
              size_bytes: Number.isFinite(sizeBytes) && sizeBytes >= 0 ? sizeBytes : null,
            },
          };
        },
        onError: async (error) => {
          // Operation events are rendered in the user's launcher language;
          // keep low-level Docker/CLI wording in the diagnostic stream only.
          error.messageKey = 'launcher_backup_failed_generic';
          error.messageValues = {};
          return error;
        },
      },
    );
  }

  /** Return a bounded, credential-free catalog for the native download UI. */
  async getBackupJobs() {
    const { args } = await this.prepareCompose();
    const result = await this.execDocker(
      [
        ...args,
        'exec', '-T', 'fastapi', 'python', '-m', 'app.backups.cli',
        'list', '--page', '1', '--page-size', '50',
      ],
      { timeoutMs: 30000 },
    );
    if (!result.ok) {
      throw new Error('Could not load backup history. Make sure Omlorix is running and ready.');
    }
    const payload = parseTrailingJsonObject(result.stdout);
    if (!payload || !Array.isArray(payload.items)) {
      throw new Error('Omlorix returned an invalid backup history response.');
    }
    return payload.items
      .filter((job) => job && BACKUP_JOB_ID_PATTERN.test(String(job.id || '')))
      .map((job) => {
        const sizeBytes = Number(job.size_bytes);
        return {
          id: String(job.id),
          status: String(job.status || ''),
          created_at: String(job.created_at || ''),
          finished_at: String(job.finished_at || ''),
          size_bytes: Number.isFinite(sizeBytes) && sizeBytes >= 0 ? sizeBytes : null,
          encryption_enabled: job.options?.encryption_enabled !== false,
          has_artifact: Array.isArray(job.artifacts) && job.artifacts.length > 0,
        };
      });
  }

  /** Materialize once to obtain the backend-authoritative archive filename. */
  async getBackupDownloadInfo(jobId) {
    const normalizedJobId = String(jobId || '').trim();
    if (!BACKUP_JOB_ID_PATTERN.test(normalizedJobId)) {
      throw backupDownloadError('BACKUP_NOT_AVAILABLE', 'Choose a valid completed backup.');
    }
    const { args } = await this.prepareCompose();
    const result = await this.execDocker(
      [
        ...args,
        'exec', '-T', 'fastapi', 'python', '-m', 'app.backups.cli',
        'download', normalizedJobId, '--metadata',
      ],
      { timeoutMs: 30 * 60 * 1000 },
    );
    if (!result.ok) {
      throw backupDownloadError('BACKUP_NOT_AVAILABLE', 'The selected backup is not available to download.');
    }
    const payload = parseTrailingJsonObject(result.stdout);
    const filename = String(payload?.filename || '');
    const bytes = Number(payload?.bytes);
    if (
      payload?.job_id !== normalizedJobId
      || !BACKUP_DOWNLOAD_FILENAME_PATTERN.test(filename)
      || path.basename(filename) !== filename
      || !Number.isFinite(bytes)
      || bytes < 0
    ) {
      throw backupDownloadError('BACKUP_NOT_AVAILABLE', 'Omlorix returned invalid backup download metadata.');
    }
    return { jobId: normalizedJobId, filename, bytes };
  }

  /** Stream a completed artifact to an explicit host path and commit atomically. */
  async downloadBackup(jobId, target) {
    const normalizedJobId = String(jobId || '').trim();
    if (!BACKUP_JOB_ID_PATTERN.test(normalizedJobId)) {
      throw backupDownloadError('BACKUP_NOT_AVAILABLE', 'Choose a valid completed backup.');
    }
    if (!String(target || '').trim()) {
      throw backupDownloadError('BACKUP_DESTINATION_UNAVAILABLE', 'Choose a backup destination path.');
    }
    return this.withSharedOperationLock('backup download', async () => {
      if (this.activeOperation) {
        throw new Error(`Another operation is already running: ${this.activeOperation}`);
      }
      await this.validateProfileEnv();
      const { args } = await this.prepareCompose();
      const name = 'Backup download';
      this.activeOperation = name;
      this.emit('operation-start', { name });
      try {
        const completed = await writeAtomicBackupDownload(target, (fileHandle) => (
          streamDockerOutputToFile(
            [
              ...args,
              'exec', '-T', 'fastapi', 'python', '-m', 'app.backups.cli',
              'download', normalizedJobId,
            ],
            this.serverHome,
            fileHandle,
          )
        ));
        this.emit('operation-end', {
          name,
          ok: true,
          code: 0,
          message: 'Backup download finished.',
          messageKey: 'launcher_ui_backup_download_finished',
          messageValues: {},
        });
        return {
          jobId: normalizedJobId,
          fileName: path.basename(completed.path),
          bytes: completed.bytes,
        };
      } catch (error) {
        const safeError = String(error?.code || '').startsWith('BACKUP_')
          ? error
          : backupDownloadError('BACKUP_DOWNLOAD_FAILED', 'The backup archive could not be downloaded.');
        safeError.messageKey = {
          BACKUP_DESTINATION_EXISTS: 'launcher_ui_backup_download_destination_exists',
          BACKUP_DESTINATION_UNAVAILABLE: 'launcher_ui_backup_download_destination_unavailable',
          BACKUP_NOT_AVAILABLE: 'launcher_ui_backup_download_not_available',
        }[safeError.code] || 'launcher_ui_backup_download_failed';
        safeError.messageValues = {};
        this.emit('operation-end', operationFailurePayload(name, safeError));
        throw safeError;
      } finally {
        this.activeOperation = null;
      }
    });
  }

  async stopRemainingRestoreApplicationContainers(args) {
    const inventory = await this.execDocker(
      [...args, 'ps', '--all', '--orphans', '--format', 'json'],
      { timeoutMs: 30000 },
    );
    if (!inventory.ok) {
      throw new Error('Could not inventory Compose containers before restore.');
    }
    const containerIds = restoreApplicationContainerIds(inventory.stdout);
    if (!containerIds.length) return;
    const stopped = await this.execDocker(
      ['stop', '--time', '60', ...containerIds],
      { timeoutMs: 120000 },
    );
    if (!stopped.ok) {
      throw new Error('Could not stop every active application container before restore.');
    }
  }

  async restore(source) {
    return this.withSharedOperationLock('restore', async () => {
      if (this.activeOperation) {
      throw new Error(`Another operation is already running: ${this.activeOperation}`);
      }

    const trimmedSource = String(source || '').trim();
    if (!trimmedSource) {
      throw new Error('Choose an Omlorix backup archive to restore.');
    }
    const sourcePath = path.resolve(trimmedSource);
    const lowerSourcePath = sourcePath.toLowerCase();
    if (!lowerSourcePath.endsWith('.tar.zst') && !lowerSourcePath.endsWith('.tar.zst.enc')) {
      throw new Error('Choose an Omlorix backup ending in .tar.zst or .tar.zst.enc.');
    }

    // Claim the destructive operation before the first await. Otherwise a
    // restart or scheduled update can enter while environment/file validation
    // is pending and both workflows can mutate the same Compose stack.
    this.activeOperation = 'Restore';
    this.emit('operation-start', { name: 'Restore' });
    // Every process that can touch restored state must be stopped regardless
    // of the newly saved topology. For example, Redis may have been switched
    // Off without recreating the existing worker containers yet.
    const servicesToStop = offlineApplicationServiceNames();
    let servicesToStart = [];
    let env = {};
    let args = [];
    let stopAttempted = false;
    let applicationStopped = false;
    let restoreCommandStarted = false;
    let dataReplacementCompleted = false;
    try {
      // Read the environment first so Restore retains its immediate operation
      // reservation contract; the network is still created before Compose runs.
      env = await this.readEnv();
      if (fssync.existsSync(path.join(this.serverHome, 'docker-compose.launcher-services.yml'))) {
        await this.ensureLauncherServicesNetwork();
      }
      args = composeArgs(this.serverHome, env);
      // Explicitly targeting a profiled Compose service during `up` activates
      // its profile. Restart workers only when the selected topology enables
      // Redis, while the stop phase above remains deliberately unconditional.
      servicesToStart = readEnvToggles(env).redisEnabled
        ? [
          'frontend',
          'email_worker',
          ...DEDICATED_WORKER_SERVICE_NAMES,
          'automation_scheduler',
          'automation_worker',
          'fastapi',
        ]
        : ['frontend', 'email_worker', ...DEDICATED_WORKER_SERVICE_NAMES, 'fastapi'];
      let sourceStat;
      try {
        sourceStat = await fs.stat(sourcePath);
      } catch {
        // Do not expose platform-specific paths or raw filesystem diagnostics in
        // the launcher when a selected archive vanishes or cannot be accessed.
        throw new Error('The selected restore source is unavailable. Choose an existing, accessible Omlorix backup archive.');
      }
      if (!sourceStat.isFile()) {
        throw new Error('The selected restore source is not a file.');
      }

      // A stop command can time out after stopping only part of the stack. Mark
      // the phase before awaiting so the failure path always attempts recovery.
      stopAttempted = true;
      await this.runDockerStep(
        'Stopping application services before restore',
        [...args, 'stop', ...servicesToStop],
        120000,
        'Restore',
        'launcher_restore_stopping_services',
      );
      // The named stop above is graceful, but cannot see removed services or
      // one-off `compose run` containers. Fence every remaining project
      // application container by validated container ID before restoring.
      await this.stopRemainingRestoreApplicationContainers(args);
      applicationStopped = true;

      // The one-shot container inherits the normal backend volumes and
      // environment but is the only application process running. Mount the
      // administrator-selected archive read-only at a stable container path;
      // no browser upload or live FastAPI worker participates in restoration.
      restoreCommandStarted = true;
      await this.runDockerStep(
        'Verifying backup and restoring server data',
        [
          ...args,
          'run',
          '--rm',
          '--no-deps',
          '--remove-orphans',
          '--volume',
          `${sourcePath}:/restore/input:ro`,
          'fastapi',
          'python',
          '-m',
          'app.backups.cli',
          'restore',
          '--source',
          'file:///restore/input',
          '--target',
          'in_place',
          '--confirm',
          'RESTORE-IN-PLACE',
          '--offline',
        ],
        2 * 60 * 60 * 1000,
        'Restore',
        'launcher_restore_restoring_data',
      );
      // From this point onward the archive has replaced server data. Startup
      // failures must not be described as failed or rolled-back restores.
      dataReplacementCompleted = true;

      await this.runDockerStep(
        'Starting Omlorix after restore',
        [...args, 'up', '-d', '--no-deps', '--force-recreate', '--remove-orphans', ...servicesToStart],
        180000,
        'Restore',
        'launcher_restore_starting_services',
      );
      applicationStopped = false;
      const readyUrl = await this.waitForReady();
      this.emitOperationOutput(
        'Restore',
        'stdout',
        `\nOmlorix restored and ready at ${readyUrl}\n`,
        'launcher_restore_ready_at',
        { url: readyUrl },
      );
      await this.recordSuccessfulServerVersion(env.OMLORIX_VERSION);
      const state = await this.getState();
      this.emit('operation-end', {
        name: 'Restore',
        ok: true,
        code: 0,
        message: 'Omlorix restore completed.',
        messageKey: 'launcher_restore_finished',
      });
      return state;
    } catch (error) {
      const restoreFailure = restoreCommandStarted
        ? restoreFailureFromError(error)
        : {
            reason: error.message || String(error),
            reasonCode: '',
            recovery: { state: 'not_started', safeToRestart: true },
          };
      const recovery = restoreFailure.recovery;
      const restoreMessageValues = {
        error: restoreFailure.reason,
        ...(restoreFailure.reasonCode ? { restoreReasonCode: restoreFailure.reasonCode } : {}),
      };
      const servicesMayBeStopped = stopAttempted && !dataReplacementCompleted;
      const canRestartSafely = !applicationStopped || recovery?.safeToRestart === true;

      if (servicesMayBeStopped && canRestartSafely) {
        this.emitOperationOutput(
          'Restore',
          'stderr',
          '\nRestore stopped safely. Restarting Omlorix with the existing or recovered server data.\n',
          'launcher_restore_restarting_after_failure',
        );
        const restartResult = await this.execDocker(
          [...args, 'up', '-d', '--no-deps', '--force-recreate', '--remove-orphans', ...servicesToStart],
          { timeoutMs: 180000 },
        );
        let restartError = '';
        if (!restartResult.ok) {
          restartError = restartResult.stderr || restartResult.stdout || 'Docker could not restart the application services.';
        } else {
          try {
            await this.waitForReady();
            applicationStopped = false;
          } catch (readinessError) {
            restartError = readinessError.message || String(readinessError);
          }
        }
        if (restartError) {
          error.message = `Restore stopped safely, but Omlorix did not return to full health. Restore reason: ${restoreFailure.reason} Restart error: ${restartError}`;
          error.messageKey = 'launcher_restore_restart_failed';
          error.messageValues = { ...restoreMessageValues, restartError };
        } else {
          error.message = `Restore stopped without leaving changed server data, and Omlorix returned to full health. Reason: ${restoreFailure.reason}`;
          error.messageKey = 'launcher_restore_stopped_safely';
          error.messageValues = restoreMessageValues;
        }
      } else if (servicesMayBeStopped && !canRestartSafely) {
        const restoreError = restoreFailure.reason;
        error.message = `Restore failed and safe recovery could not be confirmed. Omlorix was left stopped to protect the server data. Review restore logs before restarting. Original error: ${restoreError}`;
        error.messageKey = 'launcher_restore_recovery_unconfirmed';
        error.messageValues = restoreMessageValues;
      } else if (dataReplacementCompleted) {
        const startupError = error.message || String(error);
        error.message = `Server data was restored, but Omlorix failed to start. The restored data was not rolled back. Startup error: ${startupError}`;
        error.messageKey = 'launcher_restore_startup_failed_after_restore';
        error.messageValues = { error: startupError };
      }
      this.emit('operation-end', operationFailurePayload('Restore', error));
      throw error;
      } finally {
        this.activeOperation = null;
      }
    });
  }

  /**
   * Verify a host backup archive without stopping or mutating the live stack.
   *
   * The selected file is mounted read-only into the same one-shot backend
   * image used by restore. This keeps validation behavior identical to the
   * standalone CLI while avoiding a browser upload or a dependency on a
   * currently running FastAPI container.
   */
  async verifyBackup(source) {
    const trimmedSource = String(source || '').trim();
    if (!trimmedSource) {
      throw new Error('Choose an Omlorix backup archive to verify.');
    }
    const sourcePath = path.resolve(trimmedSource);
    const lowerSourcePath = sourcePath.toLowerCase();
    if (!lowerSourcePath.endsWith('.tar.zst') && !lowerSourcePath.endsWith('.tar.zst.enc')) {
      throw new Error('Choose an Omlorix backup ending in .tar.zst or .tar.zst.enc.');
    }

    let sourceStat;
    try {
      sourceStat = await fs.stat(sourcePath);
    } catch {
      throw new Error('The selected backup is unavailable. Choose an existing, accessible Omlorix backup archive.');
    }
    if (!sourceStat.isFile()) {
      throw new Error('The selected backup is not a file.');
    }

    const { args } = await this.prepareCompose();
    return this.runOperation(
      'Verify backup',
      [
        ...args,
        'run',
        '--rm',
        '--no-deps',
        '--volume',
        `${sourcePath}:/verify/input:ro`,
        'fastapi',
        'python',
        '-m',
        'app.backups.cli',
        'verify',
        '--source',
        'file:///verify/input',
      ],
      {
        successMessage: 'Backup verification completed.',
        successMessageKey: 'launcher_ui_backup_verify_finished',
        onError: async (error) => {
          error.messageKey = 'launcher_ui_backup_verify_failed';
          error.messageValues = {};
          return error;
        },
      },
    );
  }

  /**
   * Apply one lifecycle action to a configured long-running Compose service.
   * Service names are resolved from Compose itself and never interpolated into
   * a shell command, preventing arbitrary service or argument injection.
   */
  async serviceAction(action, serviceName, options = {}) {
    const normalizedAction = String(action || '').trim().toLowerCase();
    const normalizedService = String(serviceName || '').trim();
    const allowedActions = new Set(['start', 'stop', 'restart', 'logs']);
    if (!allowedActions.has(normalizedAction)) {
      throw new Error('Choose a supported service action.');
    }
    if (!normalizedService) {
      throw new Error('Choose a configured service.');
    }

    if (normalizedAction === 'logs') {
      return this.logs({ ...options, service: normalizedService });
    }

    const { args } = await this.prepareCompose();
    const config = await this.execDocker([...args, 'config', '--services'], { timeoutMs: 10000 });
    if (!config.ok) {
      throw new Error(config.stderr || config.stdout || 'Could not resolve configured Compose services.');
    }
    const configuredServices = parseComposeServiceNames(config.stdout);
    if (!configuredServices.includes(normalizedService)) {
      throw new Error('Choose a configured long-running service.');
    }

    const actionArgs = normalizedAction === 'start'
      ? [...args, 'up', '-d', '--no-deps', normalizedService]
      : normalizedAction === 'stop'
        ? [...args, 'stop', normalizedService]
        : [...args, 'restart', normalizedService];
    return this.runOperation(
      `${normalizedAction} ${normalizedService}`,
      actionArgs,
      {
        successMessage: `${normalizedService}: ${normalizedAction} completed.`,
        successMessageKey: 'launcher_ui_service_action_completed',
        successMessageValues: { service: normalizedService, action: normalizedAction },
        onError: async (error) => {
          error.messageKey = 'launcher_ui_service_action_operation_failed';
          error.messageValues = { service: normalizedService, action: normalizedAction };
          return error;
        },
      },
    );
  }

  /** Resolve read-only Compose context and validate an optional service scope. */
  async prepareLogs(options = {}) {
    const normalized = normalizeLogOptions(options);
    const { env, args } = await this.prepareCompose({ readOnly: true });
    if (normalized.service) {
      const config = await this.execDocker([...args, 'config', '--services'], { timeoutMs: 10000 });
      if (!config.ok) {
        throw new Error('Could not resolve configured Compose services.');
      }
      if (!parseComposeServiceNames(config.stdout).includes(normalized.service)) {
        throw new Error('Choose a configured long-running service.');
      }
    }
    return { env, args, options: normalized };
  }

  /** Read a bounded snapshot, or route an explicit follow to its cancellable lifecycle. */
  async logs(options = {}) {
    const normalized = normalizeLogOptions(options);
    if (normalized.follow) return this.startLogFollow(normalized);
    const context = await this.prepareLogs(normalized);
    const result = await this.execDocker(
      [...context.args, ...composeLogArgs(context.options)],
      { timeoutMs: 15000 },
    );
    if (!result.ok) {
      throw new Error(redactLogText(
        result.stderr || result.stdout || 'Could not read Docker Compose logs.',
        context.env,
      ));
    }
    return redactLogText(result.stdout, context.env);
  }

  /** Start one cancellable aggregate or per-service Compose log stream. */
  async startLogFollow(options = {}) {
    if (this.logFollowSession) {
      throw new Error('Log following is already active. Stop it before starting another stream.');
    }
    const context = await this.prepareLogs({
      ...(typeof options === 'number' ? { lines: options } : options),
      follow: true,
    });
    const sessionId = crypto.randomUUID();
    const dockerExecutable = dockerCommand();
    const commandArgs = [...context.args, ...composeLogArgs(context.options)];
    const redactionValues = logRedactionValues(context.env);
    const stdoutRedactor = createLogRedactor(redactionValues);
    const stderrRedactor = createLogRedactor(redactionValues);
    let child;
    try {
      child = this.spawnLogProcess(dockerExecutable, commandArgs, {
        cwd: this.serverHome,
        windowsHide: true,
        env: dockerSpawnEnv(dockerExecutable),
      });
    } catch (error) {
      throw new Error('Could not start log following.', { cause: error });
    }

    let resolveClosed;
    const closed = new Promise((resolve) => {
      resolveClosed = resolve;
    });
    const session = {
      id: sessionId,
      child,
      closed,
      finish: null,
      finished: false,
      stopRequested: false,
      forceStopTimer: null,
    };
    this.logFollowSession = session;

    const emitText = (stream, text) => {
      if (!text) return;
      this.emit('log-follow-output', { sessionId, stream, text });
    };
    child.stdout?.on('data', (chunk) => emitText('stdout', stdoutRedactor.push(chunk.toString())));
    child.stderr?.on('data', (chunk) => emitText('stderr', stderrRedactor.push(chunk.toString())));

    return new Promise((resolve, reject) => {
      let started = false;
      session.finish = (code = -1, signal = null, startError = null) => {
        if (session.finished) return;
        session.finished = true;
        if (session.forceStopTimer) clearTimeout(session.forceStopTimer);
        emitText('stdout', stdoutRedactor.flush());
        emitText('stderr', stderrRedactor.flush());
        if (this.logFollowSession === session) this.logFollowSession = null;
        const stopped = session.stopRequested;
        const payload = {
          sessionId,
          ok: stopped || code === 0,
          stopped,
          code: Number.isInteger(code) ? code : -1,
          signal: signal || '',
        };
        if (started) this.emit('log-follow-end', payload);
        resolveClosed(payload);
        if (!started) {
          const error = new Error('Could not start log following.');
          if (startError) error.cause = startError;
          reject(error);
        }
      };

      child.once('spawn', () => {
        started = true;
        resolve({ sessionId, options: context.options });
      });
      child.once('error', (error) => session.finish(-1, null, error));
      child.once('close', (code, signal) => session.finish(code, signal));
    });
  }

  /** Stop the active stream and wait until no further output can be emitted. */
  async stopLogFollow(sessionId = '') {
    const session = this.logFollowSession;
    if (!session) return { stopped: false };
    if (sessionId && String(sessionId) !== session.id) {
      throw new Error('The active log stream changed. Try stopping it again.');
    }
    if (!session.stopRequested) {
      session.stopRequested = true;
      try {
        session.child.kill();
      } catch {
        session.finish?.(-1, 'SIGTERM');
        return session.closed;
      }
      session.forceStopTimer = setTimeout(() => {
        if (session.finished) return;
        try {
          session.child.kill('SIGKILL');
        } catch {
          session.finish?.(-1, 'SIGKILL');
          return;
        }
        // A failed platform kill must not leave the Launcher controls wedged.
        setTimeout(() => session.finish?.(-1, 'SIGKILL'), LOG_FOLLOW_FORCE_STOP_MS).unref?.();
      }, LOG_FOLLOW_FORCE_STOP_MS);
      session.forceStopTimer.unref?.();
    }
    return session.closed;
  }

  async revealServerHome(shell) {
    await this.ensureServerHome();
    await shell.openPath(this.serverHome);
  }

  async openUrl(shell) {
    const env = await this.readEnv();
    const proxyStatus = this.proxyStatus(env, await this.proxyServiceStatus());
    const url = proxyStatus.running && proxyStatus.config?.enabled
      ? proxyStatus.config.publicUrl
      : this.resolveUrl(env);
    await shell.openExternal(url);
  }

  /** Open Omlorix's authenticated service-connections page for manual pairing. */
  async openServiceConnections(shell) {
    const env = await this.readEnv();
    const proxyStatus = this.proxyStatus(env, await this.proxyServiceStatus());
    const baseUrl = proxyStatus.running && proxyStatus.config?.enabled
      ? proxyStatus.config.publicUrl
      : this.resolveUrl(env);
    const target = new URL('/admin/service-connections', baseUrl).toString();
    await shell.openExternal(target);
  }

  hasServerBundleSource() {
    return fssync.existsSync(this.resourceRoot());
  }
}

module.exports = {
  SERVER_FILES,
  ServerManager,
  dockerCommand,
  dockerSpawnEnv,
  dockerRegistryAccessErrorMessage,
  linuxHostMetricsSupported,
  observabilityCapability,
  expectedServiceNamesFromToggles,
  offlineApplicationServiceNames,
  mergeExpectedComposeServices,
  stackReadinessHealthy,
  parseComposeServiceNames,
  readEnvToggles,
  buildComposeProfiles,
  composeArgs,
  terminalComposeArgs,
  normalizeLogOptions,
  composeLogArgs,
  createLogRedactor,
  redactLogText,
  normalizeStorageMigrationOptions,
  highestServerVersion,
  serverVersionFromImage,
  trackableServerVersion,
  envBackupFingerprint,
  writeAtomicBackupDownload,
  // Preserve the exported helper name for compatibility with launcher tooling;
  // it now fingerprints the complete effective .env rather than a key subset.
  secretBackupFingerprint: envBackupFingerprint,
};
