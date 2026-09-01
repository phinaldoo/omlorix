(function launcher() {
  const DOCKER_READINESS_POLL_TIMEOUT_MS = 60000;
  const DOCKER_READINESS_POLL_INTERVAL_MS = 1000;
  const DOCKER_INSTALL_POLL_TIMEOUT_MS = 15 * 60 * 1000;
  const DOCKER_INSTALL_POLL_INTERVAL_MS = 5000;
  const AUTO_UPDATE_AUTOSAVE_DELAY_MS = 350;
  const PROXY_AUTOSAVE_DELAY_MS = 450;
  const ENV_EDITOR_AUTOSAVE_DELAY_MS = 450;
  const ENV_EDITOR_IDLE_RELOAD_DELAY_MS = 5000;
  const ENV_EDITOR_IDLE_RELOAD_INTERVAL_MS = 5000;
  const VERSION_LIST_REFRESH_INTERVAL_MS = 15 * 60 * 1000;
  const SERVER_UPDATE_REFRESH_INTERVAL_MS = 15 * 60 * 1000;
  const RELEASE_CHECK_FAILURE_COOLDOWN_MS = 60 * 1000;
  const SERVICE_STATUS_REFRESH_INTERVAL_MS = 10 * 1000;
  const SERVICE_STATUS_ACTION_REFRESH_INTERVAL_MS = 2 * 1000;
  const SETTINGS_CLOSE_FLUSH_TIMEOUT_MS = 5000;

  const state = {
    current: null,
    busy: false,
    settingsAutosaveTimer: null,
    settingsSaving: false,
    settingsSaveRequested: false,
    settingsSavePromise: null,
    settingsDirtyKeys: new Set(),
    settingsCloseFlushActive: false,
    settingsCloseAllowed: false,
    envEditor: null,
    envAutosaveTimer: null,
    envSaving: false,
    envSaveRequested: false,
    envReloadInFlight: false,
    envIdleReloadTimer: null,
    envLastChangeAt: 0,
    envEditVersion: 0,
    envRemovedKeys: new Set(),
    envImportPreview: null,
    envFilter: {
      search: '',
      section: 'all',
    },
    envValidationErrors: {},
    proxyValidationErrors: {},
    proxyFormDirty: false,
    proxyEditVersion: 0,
    proxyAutosaveTimer: null,
    proxySaving: false,
    proxySaveRequested: false,
    proxySavePromise: null,
    autoUpdates: null,
    autoUpdateAutosaveTimer: null,
    autoUpdateSaving: false,
    autoUpdateSaveRequested: false,
    availableVersions: [],
    availableVersionsChannel: '',
    availableVersionsRequest: 0,
    availableVersionsPromise: null,
    availableVersionsPromiseChannel: '',
    availableVersionsNextCheckAt: 0,
    availableVersionsRefreshTimer: null,
    launcherUpdateInfo: null,
    launcherUpdateMinimumVersion: '',
    launcherUpdateRequest: 0,
    launcherUpdateRetryAt: 0,
    launcherUpdateRetryChannel: '',
    serverUpdateInfo: null,
    serverUpdateRequest: 0,
    releaseUpdateRefreshPromise: null,
    releaseUpdateRefreshChannel: '',
    serverUpdateRefreshTimer: null,
    serviceStatusRefreshTimer: null,
    serviceStatusRefreshStarted: false,
    serviceStatusRefreshInFlight: false,
    serviceStatusRequest: 0,
    serviceStatusAppliedAt: 0,
    logLoading: false,
    logFollowStarting: false,
    logFollowStopping: false,
    logFollowSessionId: '',
    logFollowEndedSessionId: '',
    consoleStreamBuffer: '',
    consoleStreamTimer: null,
    refreshRequests: 0,
    dockerReadinessPoll: {
      timer: null,
      deadline: 0,
      inFlight: false,
      active: false,
      mode: 'start',
      intervalMs: DOCKER_READINESS_POLL_INTERVAL_MS,
    },
    launcherDialog: null,
    backupOptions: null,
    backupOptionsLoading: false,
    backupOptionsError: '',
    backupOptionsRequest: 0,
    backupDestinationId: '',
    backupEncryptionPreferred: true,
    backupCreating: false,
    backupLastResult: null,
    backupJobs: null,
    backupJobsLoading: false,
    backupJobsError: false,
    backupJobsRequest: 0,
    backupDownloading: false,
    backupDownloadStatus: null,
    storageFormInitialized: false,
    storageLastResult: null,
    visitorIpRepairFailure: '',
  };

  // These launcher env requirements belong to specific infrastructure toggles
  // rather than the general .env warning card at the top of the page.
  const TOGGLE_REQUIREMENT_TARGETS = new Map([
    ['DATABASE_URL', 'useBundledDB'],
    ['DATABASE_PASSWORD', 'useBundledDB'],
    ['REDIS_URL', 'useBundledRedis'],
    ['MINIO_ROOT_USER', 'useBundledStorage'],
    ['MINIO_ROOT_PASSWORD', 'useBundledStorage'],
  ]);

  const CORE_SETTINGS_ENV_KEYS = [
    'COMPOSE_PROJECT_NAME',
    'MODE',
    'OMLORIX_VERSION',
    'BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE',
    'JWT_SECRET_KEY',
    'ENCRYPTION_KEY',
    'PASSWORD_RESET_IDENTIFIER_HASH_SALT',
    'OMLORIX_USE_BUNDLED_DB',
    'OMLORIX_USE_BUNDLED_REDIS',
    'OMLORIX_USE_BUNDLED_STORAGE',
    'REDIS_ENABLED',
    'DEV_DATABASE_HOST_PORT',
    'DEV_REDIS_HOST_PORT',
  ];

  // Connection-mode fields keep the env editor focused on the variables a user
  // can actually edit for the selected bundled/external service mode.
  const CONNECTION_MODE_ENV_FIELDS = new Map([
    ['DATABASE_URL', { toggle: 'useBundledDB', showWhen: false }],
    ['DATABASE_USER', { toggle: 'useBundledDB', showWhen: true }],
    ['DATABASE_PASSWORD', { toggle: 'useBundledDB', showWhen: true }],
    ['DATABASE_HOST', { toggle: 'useBundledDB', showWhen: true }],
    ['DATABASE_PORT', { toggle: 'useBundledDB', showWhen: true }],
    ['DATABASE_NAME', { toggle: 'useBundledDB', showWhen: true }],
    ['DATABASE_SCHEMA', { toggle: 'useBundledDB', showWhen: true }],
    ['DATABASE_AUDIT_LOG_SCHEMA', { toggle: 'useBundledDB', showWhen: true }],
    ['DATABASE_LOGS_SCHEMA', { toggle: 'useBundledDB', showWhen: true }],
    ['OMLORIX_AUTO_CREATE_DATABASES', { toggle: 'useBundledDB', showWhen: true }],
    ['DATABASE_HOST_OVERRIDE', { toggle: 'useBundledDB', showWhen: true }],
    ['DATABASE_PORT_OVERRIDE', { toggle: 'useBundledDB', showWhen: true }],
    ['REDIS_URL', { toggle: 'useBundledRedis', showWhen: false }],
    ['REDIS_PASSWORD', { toggle: 'useBundledRedis', showWhen: true }],
  ]);

  const FILE_STORAGE_ENV_KEYS = [
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
  ];

  const DATABASE_TRAFFIC_MANAGER_ENV_KEYS = [
    'OMLORIX_USE_PGBOUNCER',
    'PGBOUNCER_HOST_BIND',
    'PGBOUNCER_HOST_PORT',
    'PGBOUNCER_POOL_MODE',
    'PGBOUNCER_MAX_CLIENT_CONN',
    'PGBOUNCER_DEFAULT_POOL_SIZE',
    'PGBOUNCER_RESERVE_POOL_SIZE',
  ];

  const OBSERVABILITY_ENV_KEYS = [
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
  ];

  const PROXY_PAGE_ENV_KEYS = [
    'FRONTEND_HTTP_HOST_BIND',
    'FRONTEND_HTTP_HOST_PORT',
    'API_LB_TRAEFIK_WEB_HOST_PORT',
    'API_LB_TRAEFIK_DASHBOARD_HOST_PORT',
    'TRUST_PROXY_HEADERS',
    'TRUSTED_PROXIES',
    'TRUSTED_HOSTS',
    'UVICORN_FORWARDED_ALLOW_IPS',
    'RATE_LIMIT_TRUSTED_PROXIES',
    'AUTH_TRUSTED_PROXIES',
    'RATE_LIMIT_PROXY_SETTINGS_CACHE_SECONDS',
    'OMLORIX_LAUNCHER_PROXY_ENABLED',
    'OMLORIX_LAUNCHER_PROXY_AUTOSTART',
    'OMLORIX_LAUNCHER_PROXY_BIND',
    'OMLORIX_LAUNCHER_PROXY_HTTP_PORT',
    'OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED',
    'OMLORIX_LAUNCHER_PROXY_HTTPS_PORT',
    'OMLORIX_LAUNCHER_PROXY_REDIRECT_HTTP_TO_HTTPS',
    'OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH',
    'OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH',
    'OMLORIX_LAUNCHER_PROXY_TLS_CA_PATH',
    'OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE',
  ];
  const SETTINGS_OWNED_ENV_KEYS = new Set([
    ...CORE_SETTINGS_ENV_KEYS,
    ...CONNECTION_MODE_ENV_FIELDS.keys(),
    ...FILE_STORAGE_ENV_KEYS,
    ...DATABASE_TRAFFIC_MANAGER_ENV_KEYS,
    ...OBSERVABILITY_ENV_KEYS,
    ...PROXY_PAGE_ENV_KEYS,
  ]);
  const SETTINGS_INPUT_BY_ENV_KEY = new Map([
    ['COMPOSE_PROJECT_NAME', 'composeProjectNameInput'],
    ['MODE', 'modeSelect'],
    ['JWT_SECRET_KEY', 'jwtSecretKeyInput'],
    ['ENCRYPTION_KEY', 'encryptionKeyInput'],
    ['PASSWORD_RESET_IDENTIFIER_HASH_SALT', 'passwordResetSaltInput'],
    ['DATABASE_URL', 'databaseUrlInput'],
    ['DATABASE_USER', 'databaseUserInput'],
    ['DATABASE_PASSWORD', 'databasePasswordInput'],
    ['DATABASE_HOST', 'databaseHostInput'],
    ['DATABASE_PORT', 'databasePortInput'],
    ['DATABASE_NAME', 'databaseNameInput'],
    ['DATABASE_SCHEMA', 'databaseSchemaInput'],
    ['DATABASE_AUDIT_LOG_SCHEMA', 'databaseAuditLogSchemaInput'],
    ['DATABASE_LOGS_SCHEMA', 'databaseLogsSchemaInput'],
    ['OMLORIX_AUTO_CREATE_DATABASES', 'autoCreateDatabasesInput'],
    ['OMLORIX_USE_BUNDLED_DB', 'toggleBundledDB'],
    ['OMLORIX_USE_BUNDLED_REDIS', 'toggleBundledRedis'],
    ['OMLORIX_USE_BUNDLED_STORAGE', 'toggleBundledStorage'],
    ['DATABASE_HOST_OVERRIDE', 'databaseHostOverrideInput'],
    ['DATABASE_PORT_OVERRIDE', 'databasePortOverrideInput'],
    ['DEV_DATABASE_HOST_PORT', 'devDatabaseHostPortInput'],
    ['REDIS_ENABLED', 'redisEnabledInput'],
    ['REDIS_URL', 'redisUrlInput'],
    ['REDIS_PASSWORD', 'redisPasswordInput'],
    ['DEV_REDIS_HOST_PORT', 'devRedisHostPortInput'],
    ['OMLORIX_USE_PGBOUNCER', 'togglePgbouncer'],
    ['PGBOUNCER_HOST_BIND', 'pgbouncerHostBindInput'],
    ['PGBOUNCER_HOST_PORT', 'pgbouncerHostPortInput'],
    ['PGBOUNCER_POOL_MODE', 'pgbouncerPoolModeSelect'],
    ['PGBOUNCER_MAX_CLIENT_CONN', 'pgbouncerMaxClientConnInput'],
    ['PGBOUNCER_DEFAULT_POOL_SIZE', 'pgbouncerDefaultPoolSizeInput'],
    ['PGBOUNCER_RESERVE_POOL_SIZE', 'pgbouncerReservePoolSizeInput'],
    ['MINIO_ROOT_USER', 'minioRootUserInput'],
    ['MINIO_ROOT_PASSWORD', 'minioRootPasswordInput'],
    ['MINIO_API_HOST_BIND', 'minioApiHostBindInput'],
    ['MINIO_API_HOST_PORT', 'minioApiHostPortInput'],
    ['MINIO_CONSOLE_HOST_BIND', 'minioConsoleHostBindInput'],
    ['MINIO_CONSOLE_HOST_PORT', 'minioConsoleHostPortInput'],
    ['FILE_STORAGE_PROVIDER', 'fileStorageProviderSelect'],
    ['FILE_STORAGE_LOCAL_BASE_PATH', 'fileStorageLocalBasePathInput'],
    ['FILE_STORAGE_S3_BUCKET', 'fileStorageS3BucketInput'],
    ['FILE_STORAGE_S3_PREFIX', 'fileStorageS3PrefixInput'],
    ['FILE_STORAGE_S3_REGION', 'fileStorageS3RegionInput'],
    ['FILE_STORAGE_S3_ENDPOINT_URL', 'fileStorageS3EndpointUrlInput'],
    ['FILE_STORAGE_S3_ACCESS_KEY_ID', 'fileStorageS3AccessKeyIdInput'],
    ['FILE_STORAGE_S3_SECRET_ACCESS_KEY', 'fileStorageS3SecretAccessKeyInput'],
    ['FILE_STORAGE_S3_SESSION_TOKEN', 'fileStorageS3SessionTokenInput'],
    ['FILE_STORAGE_GCS_BUCKET', 'fileStorageGcsBucketInput'],
    ['FILE_STORAGE_GCS_PREFIX', 'fileStorageGcsPrefixInput'],
    ['FILE_STORAGE_GCS_PROJECT', 'fileStorageGcsProjectInput'],
    ['FILE_STORAGE_GCS_CREDENTIALS_JSON', 'fileStorageGcsCredentialsJsonInput'],
    ['FILE_STORAGE_AZURE_CONTAINER', 'fileStorageAzureContainerInput'],
    ['FILE_STORAGE_AZURE_PREFIX', 'fileStorageAzurePrefixInput'],
    ['FILE_STORAGE_AZURE_CONNECTION_STRING', 'fileStorageAzureConnectionStringInput'],
    ['FILE_STORAGE_AZURE_ACCOUNT_URL', 'fileStorageAzureAccountUrlInput'],
    ['FILE_STORAGE_AZURE_CREDENTIAL', 'fileStorageAzureCredentialInput'],
    ['FILE_STORAGE_WEBDAV_URL', 'fileStorageWebdavUrlInput'],
    ['FILE_STORAGE_WEBDAV_USERNAME', 'fileStorageWebdavUsernameInput'],
    ['FILE_STORAGE_WEBDAV_PASSWORD', 'fileStorageWebdavPasswordInput'],
    ['FILE_STORAGE_WEBDAV_PREFIX', 'fileStorageWebdavPrefixInput'],
    ['FILE_STORAGE_WEBDAV_VERIFY_SSL', 'fileStorageWebdavVerifySslInput'],
    ['FILE_STORAGE_WEBDAV_TIMEOUT', 'fileStorageWebdavTimeoutInput'],
    ['OTEL_ENABLED', 'toggleObservability'],
    ['OTEL_SERVICE_NAME', 'otelServiceNameInput'],
    ['OTEL_EXPORTER_OTLP_ENDPOINT', 'otelExporterOtlpEndpointInput'],
    ['OTEL_EXPORTER_OTLP_INSECURE', 'otelExporterOtlpInsecureInput'],
    ['OTEL_TRACES_ENABLED', 'otelTracesEnabledInput'],
    ['OTEL_TRACES_SAMPLER', 'otelTracesSamplerSelect'],
    ['OTEL_TRACES_SAMPLER_ARG', 'otelTracesSamplerArgInput'],
    ['OTEL_METRICS_ENABLED', 'otelMetricsEnabledInput'],
    ['OTEL_PROMETHEUS_EXPORTER_ENABLED', 'otelPrometheusExporterEnabledInput'],
    ['OTEL_LOGS_ENABLED', 'otelLogsEnabledInput'],
    ['OTEL_INSTRUMENT_FASTAPI', 'otelInstrumentFastapiInput'],
    ['OTEL_INSTRUMENT_SQLALCHEMY', 'otelInstrumentSqlalchemyInput'],
    ['OTEL_INSTRUMENT_HTTP_CLIENTS', 'otelInstrumentHttpClientsInput'],
    ['OTEL_SQL_COMMENTER_ENABLED', 'otelSqlCommenterEnabledInput'],
    ['OTEL_CAPTURE_HTTP_ROUTE', 'otelCaptureHttpRouteInput'],
    ['OTEL_CAPTURE_HTTP_USER_AGENT', 'otelCaptureHttpUserAgentInput'],
    ['OTEL_HASH_HTTP_USER_AGENT', 'otelHashHttpUserAgentInput'],
    ['OTEL_GRPC_HOST_BIND', 'otelGrpcHostBindInput'],
    ['OTEL_GRPC_HOST_PORT', 'otelGrpcHostPortInput'],
    ['OTEL_HTTP_HOST_BIND', 'otelHttpHostBindInput'],
    ['OTEL_HTTP_HOST_PORT', 'otelHttpHostPortInput'],
    ['OTEL_PROMETHEUS_HOST_BIND', 'otelPrometheusHostBindInput'],
    ['OTEL_PROMETHEUS_HOST_PORT', 'otelPrometheusHostPortInput'],
    ['OTEL_HEALTHCHECK_HOST_BIND', 'otelHealthcheckHostBindInput'],
    ['OTEL_HEALTHCHECK_HOST_PORT', 'otelHealthcheckHostPortInput'],
    ['JAEGER_UI_HOST_BIND', 'jaegerUiHostBindInput'],
    ['JAEGER_UI_HOST_PORT', 'jaegerUiHostPortInput'],
    ['JAEGER_COLLECTOR_HOST_BIND', 'jaegerCollectorHostBindInput'],
    ['JAEGER_COLLECTOR_HOST_PORT', 'jaegerCollectorHostPortInput'],
    ['PROMETHEUS_HOST_BIND', 'prometheusHostBindInput'],
    ['PROMETHEUS_HOST_PORT', 'prometheusHostPortInput'],
    ['ALERTMANAGER_HOST_BIND', 'alertmanagerHostBindInput'],
    ['ALERTMANAGER_HOST_PORT', 'alertmanagerHostPortInput'],
    ['GRAFANA_HOST_BIND', 'grafanaHostBindInput'],
    ['GRAFANA_HOST_PORT', 'grafanaHostPortInput'],
    ['GRAFANA_ADMIN_USER', 'grafanaAdminUserInput'],
    ['GRAFANA_ADMIN_PASSWORD', 'grafanaAdminPasswordInput'],
    ['GRAFANA_ROOT_URL', 'grafanaRootUrlInput'],
    ['POSTGRES_EXPORTER_DATA_SOURCE_URI', 'postgresExporterDataSourceUriInput'],
    ['POSTGRES_EXPORTER_DATA_SOURCE_USER', 'postgresExporterDataSourceUserInput'],
    ['POSTGRES_EXPORTER_DATA_SOURCE_PASS', 'postgresExporterDataSourcePassInput'],
    ['REDIS_EXPORTER_ADDR', 'redisExporterAddrInput'],
    ['FRONTEND_HTTP_HOST_BIND', 'frontendHttpHostBindInput'],
    ['FRONTEND_HTTP_HOST_PORT', 'frontendHttpHostPortInput'],
    ['API_LB_TRAEFIK_WEB_HOST_PORT', 'apiLbTraefikWebHostPortInput'],
    ['API_LB_TRAEFIK_DASHBOARD_HOST_PORT', 'apiLbTraefikDashboardHostPortInput'],
    ['TRUST_PROXY_HEADERS', 'trustProxyHeadersInput'],
    ['TRUSTED_PROXIES', 'trustedProxiesInput'],
    ['TRUSTED_HOSTS', 'trustedHostsInput'],
    ['UVICORN_FORWARDED_ALLOW_IPS', 'uvicornForwardedAllowIpsInput'],
    ['RATE_LIMIT_TRUSTED_PROXIES', 'rateLimitTrustedProxiesInput'],
    ['AUTH_TRUSTED_PROXIES', 'authTrustedProxiesInput'],
    ['RATE_LIMIT_PROXY_SETTINGS_CACHE_SECONDS', 'rateLimitProxySettingsCacheSecondsInput'],
  ]);

  const els = {
    serverHomeLabel: document.getElementById('serverHomeLabel'),
    refreshButton: document.getElementById('refreshButton'),
    openFolderButton: document.getElementById('openFolderButton'),
    openButton: document.getElementById('openButton'),
    overallBadge: document.getElementById('overallBadge'),
    overallDot: document.getElementById('overallDot'),
    themeToggle: document.getElementById('themeToggle'),
    themeIcon: document.getElementById('themeIcon'),
    statusHero: document.getElementById('statusHero'),
    statusHeroIcon: document.getElementById('statusHeroIcon'),
    statusHeroMeta: document.getElementById('statusHeroMeta'),
    dockerMetric: document.getElementById('dockerMetric'),
    dockerStatus: document.getElementById('dockerStatus'),
    dockerStatusLamp: document.getElementById('dockerStatusLamp'),
    dockerStatusDetail: document.getElementById('dockerStatusDetail'),
    stackMetric: document.getElementById('stackMetric'),
    stackStatus: document.getElementById('stackStatus'),
    stackStatusLamp: document.getElementById('stackStatusLamp'),
    stackStatusDetail: document.getElementById('stackStatusDetail'),
    endpointMetric: document.getElementById('endpointMetric'),
    endpointStatus: document.getElementById('endpointStatus'),
    endpointStatusLamp: document.getElementById('endpointStatusLamp'),
    endpointStatusDetail: document.getElementById('endpointStatusDetail'),
    launcherUpdateBanner: document.getElementById('launcherUpdateBanner'),
    launcherUpdateLabel: document.getElementById('launcherUpdateLabel'),
    launcherUpdateTitle: document.getElementById('launcherUpdateTitle'),
    launcherUpdateDescription: document.getElementById('launcherUpdateDescription'),
    launcherUpdateButton: document.getElementById('launcherUpdateButton'),
    serverUpdateBanner: document.getElementById('serverUpdateBanner'),
    serverUpdateLabel: document.getElementById('serverUpdateLabel'),
    serverUpdateTitle: document.getElementById('serverUpdateTitle'),
    serverUpdateDescription: document.getElementById('serverUpdateDescription'),
    serverUpdateNote: document.getElementById('serverUpdateNote'),
    serverUpdateButton: document.getElementById('serverUpdateButton'),
    dockerSetupCard: document.getElementById('dockerSetupCard'),
    dockerSetupTitle: document.getElementById('dockerSetupTitle'),
    dockerSetupDescription: document.getElementById('dockerSetupDescription'),
    dockerSetupSteps: document.getElementById('dockerSetupSteps'),
    startDockerDesktopButton: document.getElementById('startDockerDesktopButton'),
    openDockerSetupButton: document.getElementById('openDockerSetupButton'),
    envRequirementsCard: document.getElementById('envRequirementsCard'),
    envRequirementsTitle: document.getElementById('envRequirementsTitle'),
    envRequirementsDescription: document.getElementById('envRequirementsDescription'),
    envRequirementsList: document.getElementById('envRequirementsList'),
    envRequirementsSetupButton: document.getElementById('envRequirementsSetupButton'),
    envRequirementsButton: document.getElementById('envRequirementsButton'),
    visitorIpCard: document.getElementById('visitorIpCard'),
    visitorIpDot: document.getElementById('visitorIpDot'),
    visitorIpTitle: document.getElementById('visitorIpTitle'),
    visitorIpDescription: document.getElementById('visitorIpDescription'),
    fixVisitorIpsButton: document.getElementById('fixVisitorIpsButton'),
    proxyVisitorIpCard: document.getElementById('proxyVisitorIpCard'),
    proxyVisitorIpDot: document.getElementById('proxyVisitorIpDot'),
    proxyVisitorIpTitle: document.getElementById('proxyVisitorIpTitle'),
    proxyVisitorIpDescription: document.getElementById('proxyVisitorIpDescription'),
    proxyFixVisitorIpsButton: document.getElementById('proxyFixVisitorIpsButton'),
    startButton: document.getElementById('startButton'),
    stopButton: document.getElementById('stopButton'),
    restartButton: document.getElementById('restartButton'),
    updateButton: document.getElementById('updateButton'),
    dashboardBackupSettings: document.getElementById('dashboardBackupSettings'),
    backupGroupLabel: document.getElementById('actionGroupDataLabel'),
    backupAvailabilityNotice: document.getElementById('backupAvailabilityNotice'),
    backupAvailabilityTitle: document.getElementById('backupAvailabilityTitle'),
    backupAvailabilityDescription: document.getElementById('backupAvailabilityDescription'),
    backupOptionsRetryButton: document.getElementById('backupOptionsRetryButton'),
    backupCreateControls: document.getElementById('backupCreateControls'),
    backupCreateDescription: document.getElementById('backupCreateDescription'),
    backupDestinationLabel: document.getElementById('backupDestinationLabel'),
    backupDestinationSelect: document.getElementById('backupDestinationSelect'),
    backupEncryptionOption: document.getElementById('backupEncryptionOption'),
    backupEncryptionTitle: document.getElementById('backupEncryptionTitle'),
    backupEncryptionDescription: document.getElementById('backupEncryptionDescription'),
    backupEncryptionEnabled: document.getElementById('backupEncryptionEnabled'),
    backupButton: document.getElementById('backupButton'),
    backupResult: document.getElementById('backupResult'),
    backupResultTitle: document.getElementById('backupResultTitle'),
    backupResultDescription: document.getElementById('backupResultDescription'),
    backupResultJob: document.getElementById('backupResultJob'),
    backupDownloadControls: document.getElementById('backupDownloadControls'),
    backupDownloadTitle: document.getElementById('backupDownloadTitle'),
    backupDownloadDescription: document.getElementById('backupDownloadDescription'),
    backupDownloadLabel: document.getElementById('backupDownloadLabel'),
    backupDownloadSelect: document.getElementById('backupDownloadSelect'),
    backupDownloadRefreshButton: document.getElementById('backupDownloadRefreshButton'),
    backupDownloadButton: document.getElementById('backupDownloadButton'),
    backupDownloadStatus: document.getElementById('backupDownloadStatus'),
    restoreButton: document.getElementById('restoreButton'),
    verifyBackupButton: document.getElementById('verifyBackupButton'),
    storageMigrationNotice: document.getElementById('storageMigrationNotice'),
    storageMigrationNoticeTitle: document.getElementById('storageMigrationNoticeTitle'),
    storageMigrationNoticeDescription: document.getElementById('storageMigrationNoticeDescription'),
    storageMigrationControls: document.getElementById('storageMigrationControls'),
    storageMigrationSource: document.getElementById('storageMigrationSource'),
    storageMigrationDestination: document.getElementById('storageMigrationDestination'),
    storageMigrationScope: document.getElementById('storageMigrationScope'),
    storageMigrationDryRun: document.getElementById('storageMigrationDryRun'),
    storageMigrationDeleteSource: document.getElementById('storageMigrationDeleteSource'),
    storageMigrationForce: document.getElementById('storageMigrationForce'),
    storageMigrationValidation: document.getElementById('storageMigrationValidation'),
    storageProbeButton: document.getElementById('storageProbeButton'),
    storageMigrateButton: document.getElementById('storageMigrateButton'),
    settingsForm: document.getElementById('settingsForm'),
    composeProjectNameInput: document.getElementById('composeProjectNameInput'),
    modeSelect: document.getElementById('modeSelect'),
    jwtSecretKeyInput: document.getElementById('jwtSecretKeyInput'),
    encryptionKeyInput: document.getElementById('encryptionKeyInput'),
    passwordResetSaltInput: document.getElementById('passwordResetSaltInput'),
    databaseNameInput: document.getElementById('databaseNameInput'),
    databaseUserInput: document.getElementById('databaseUserInput'),
    databasePasswordInput: document.getElementById('databasePasswordInput'),
    databaseHostInput: document.getElementById('databaseHostInput'),
    databasePortInput: document.getElementById('databasePortInput'),
    databaseSchemaInput: document.getElementById('databaseSchemaInput'),
    databaseAuditLogSchemaInput: document.getElementById('databaseAuditLogSchemaInput'),
    databaseLogsSchemaInput: document.getElementById('databaseLogsSchemaInput'),
    autoCreateDatabasesInput: document.getElementById('autoCreateDatabasesInput'),
    databaseHostOverrideInput: document.getElementById('databaseHostOverrideInput'),
    databasePortOverrideInput: document.getElementById('databasePortOverrideInput'),
    devDatabaseHostPortInput: document.getElementById('devDatabaseHostPortInput'),
    databaseUrlInput: document.getElementById('databaseUrlInput'),
    redisEnabledInput: document.getElementById('redisEnabledInput'),
    redisPasswordInput: document.getElementById('redisPasswordInput'),
    redisUrlInput: document.getElementById('redisUrlInput'),
    devRedisHostPortInput: document.getElementById('devRedisHostPortInput'),
    pgbouncerPoolModeSelect: document.getElementById('pgbouncerPoolModeSelect'),
    pgbouncerMaxClientConnInput: document.getElementById('pgbouncerMaxClientConnInput'),
    pgbouncerDefaultPoolSizeInput: document.getElementById('pgbouncerDefaultPoolSizeInput'),
    pgbouncerReservePoolSizeInput: document.getElementById('pgbouncerReservePoolSizeInput'),
    pgbouncerHostBindInput: document.getElementById('pgbouncerHostBindInput'),
    pgbouncerHostPortInput: document.getElementById('pgbouncerHostPortInput'),
    minioRootUserInput: document.getElementById('minioRootUserInput'),
    minioRootPasswordInput: document.getElementById('minioRootPasswordInput'),
    minioApiHostBindInput: document.getElementById('minioApiHostBindInput'),
    minioApiHostPortInput: document.getElementById('minioApiHostPortInput'),
    minioConsoleHostBindInput: document.getElementById('minioConsoleHostBindInput'),
    minioConsoleHostPortInput: document.getElementById('minioConsoleHostPortInput'),
    fileStorageModeInput: document.getElementById('fileStorageModeInput'),
    fileStorageProviderSelect: document.getElementById('fileStorageProviderSelect'),
    fileStorageLocalBasePathInput: document.getElementById('fileStorageLocalBasePathInput'),
    fileStorageS3BucketInput: document.getElementById('fileStorageS3BucketInput'),
    fileStorageS3ExternalBucketInput: document.getElementById('fileStorageS3ExternalBucketInput'),
    fileStorageS3PrefixInput: document.getElementById('fileStorageS3PrefixInput'),
    fileStorageS3RegionInput: document.getElementById('fileStorageS3RegionInput'),
    fileStorageS3EndpointUrlInput: document.getElementById('fileStorageS3EndpointUrlInput'),
    fileStorageS3AccessKeyIdInput: document.getElementById('fileStorageS3AccessKeyIdInput'),
    fileStorageS3SecretAccessKeyInput: document.getElementById('fileStorageS3SecretAccessKeyInput'),
    fileStorageS3SessionTokenInput: document.getElementById('fileStorageS3SessionTokenInput'),
    fileStorageGcsBucketInput: document.getElementById('fileStorageGcsBucketInput'),
    fileStorageGcsPrefixInput: document.getElementById('fileStorageGcsPrefixInput'),
    fileStorageGcsProjectInput: document.getElementById('fileStorageGcsProjectInput'),
    fileStorageGcsCredentialsJsonInput: document.getElementById('fileStorageGcsCredentialsJsonInput'),
    fileStorageAzureContainerInput: document.getElementById('fileStorageAzureContainerInput'),
    fileStorageAzurePrefixInput: document.getElementById('fileStorageAzurePrefixInput'),
    fileStorageAzureConnectionStringInput: document.getElementById('fileStorageAzureConnectionStringInput'),
    fileStorageAzureAccountUrlInput: document.getElementById('fileStorageAzureAccountUrlInput'),
    fileStorageAzureCredentialInput: document.getElementById('fileStorageAzureCredentialInput'),
    fileStorageWebdavUrlInput: document.getElementById('fileStorageWebdavUrlInput'),
    fileStorageWebdavUsernameInput: document.getElementById('fileStorageWebdavUsernameInput'),
    fileStorageWebdavPasswordInput: document.getElementById('fileStorageWebdavPasswordInput'),
    fileStorageWebdavPrefixInput: document.getElementById('fileStorageWebdavPrefixInput'),
    fileStorageWebdavVerifySslInput: document.getElementById('fileStorageWebdavVerifySslInput'),
    fileStorageWebdavTimeoutInput: document.getElementById('fileStorageWebdavTimeoutInput'),
    fileStorageProviderPanels: Array.from(document.querySelectorAll('[data-storage-provider-panel]')),
    otelServiceNameInput: document.getElementById('otelServiceNameInput'),
    otelExporterOtlpEndpointInput: document.getElementById('otelExporterOtlpEndpointInput'),
    otelExporterOtlpInsecureInput: document.getElementById('otelExporterOtlpInsecureInput'),
    otelTracesEnabledInput: document.getElementById('otelTracesEnabledInput'),
    otelTracesSamplerSelect: document.getElementById('otelTracesSamplerSelect'),
    otelTracesSamplerArgInput: document.getElementById('otelTracesSamplerArgInput'),
    otelMetricsEnabledInput: document.getElementById('otelMetricsEnabledInput'),
    otelPrometheusExporterEnabledInput: document.getElementById('otelPrometheusExporterEnabledInput'),
    otelLogsEnabledInput: document.getElementById('otelLogsEnabledInput'),
    otelInstrumentFastapiInput: document.getElementById('otelInstrumentFastapiInput'),
    otelInstrumentSqlalchemyInput: document.getElementById('otelInstrumentSqlalchemyInput'),
    otelInstrumentHttpClientsInput: document.getElementById('otelInstrumentHttpClientsInput'),
    otelSqlCommenterEnabledInput: document.getElementById('otelSqlCommenterEnabledInput'),
    otelCaptureHttpRouteInput: document.getElementById('otelCaptureHttpRouteInput'),
    otelCaptureHttpUserAgentInput: document.getElementById('otelCaptureHttpUserAgentInput'),
    otelHashHttpUserAgentInput: document.getElementById('otelHashHttpUserAgentInput'),
    otelGrpcHostBindInput: document.getElementById('otelGrpcHostBindInput'),
    otelGrpcHostPortInput: document.getElementById('otelGrpcHostPortInput'),
    otelHttpHostBindInput: document.getElementById('otelHttpHostBindInput'),
    otelHttpHostPortInput: document.getElementById('otelHttpHostPortInput'),
    otelPrometheusHostBindInput: document.getElementById('otelPrometheusHostBindInput'),
    otelPrometheusHostPortInput: document.getElementById('otelPrometheusHostPortInput'),
    otelHealthcheckHostBindInput: document.getElementById('otelHealthcheckHostBindInput'),
    otelHealthcheckHostPortInput: document.getElementById('otelHealthcheckHostPortInput'),
    jaegerUiHostBindInput: document.getElementById('jaegerUiHostBindInput'),
    jaegerUiHostPortInput: document.getElementById('jaegerUiHostPortInput'),
    jaegerCollectorHostBindInput: document.getElementById('jaegerCollectorHostBindInput'),
    jaegerCollectorHostPortInput: document.getElementById('jaegerCollectorHostPortInput'),
    prometheusHostBindInput: document.getElementById('prometheusHostBindInput'),
    prometheusHostPortInput: document.getElementById('prometheusHostPortInput'),
    alertmanagerHostBindInput: document.getElementById('alertmanagerHostBindInput'),
    alertmanagerHostPortInput: document.getElementById('alertmanagerHostPortInput'),
    grafanaHostBindInput: document.getElementById('grafanaHostBindInput'),
    grafanaHostPortInput: document.getElementById('grafanaHostPortInput'),
    grafanaAdminUserInput: document.getElementById('grafanaAdminUserInput'),
    grafanaAdminPasswordInput: document.getElementById('grafanaAdminPasswordInput'),
    grafanaRootUrlInput: document.getElementById('grafanaRootUrlInput'),
    postgresExporterDataSourceUriInput: document.getElementById('postgresExporterDataSourceUriInput'),
    postgresExporterDataSourceUserInput: document.getElementById('postgresExporterDataSourceUserInput'),
    postgresExporterDataSourcePassInput: document.getElementById('postgresExporterDataSourcePassInput'),
    redisExporterAddrInput: document.getElementById('redisExporterAddrInput'),
    versionInput: document.getElementById('versionInput'),
    updateChannelSelect: document.getElementById('updateChannelSelect'),
    proxyBadge: document.getElementById('proxyBadge'),
    proxyStatusPanel: document.getElementById('proxyStatusPanel'),
    proxyPublicUrl: document.getElementById('proxyPublicUrl'),
    proxyTargetUrl: document.getElementById('proxyTargetUrl'),
    proxyRuntimeStatus: document.getElementById('proxyRuntimeStatus'),
    proxyServiceStatus: document.getElementById('proxyServiceStatus'),
    proxyForm: document.getElementById('proxyForm'),
    trustProxyHeadersInput: document.getElementById('trustProxyHeadersInput'),
    trustedProxiesInput: document.getElementById('trustedProxiesInput'),
    trustedHostsInput: document.getElementById('trustedHostsInput'),
    uvicornForwardedAllowIpsInput: document.getElementById('uvicornForwardedAllowIpsInput'),
    rateLimitTrustedProxiesInput: document.getElementById('rateLimitTrustedProxiesInput'),
    authTrustedProxiesInput: document.getElementById('authTrustedProxiesInput'),
    rateLimitProxySettingsCacheSecondsInput: document.getElementById('rateLimitProxySettingsCacheSecondsInput'),
    proxyEnabledInput: document.getElementById('proxyEnabledInput'),
    proxySettings: document.getElementById('proxySettings'),
    frontendHttpHostBindInput: document.getElementById('frontendHttpHostBindInput'),
    frontendHttpHostPortInput: document.getElementById('frontendHttpHostPortInput'),
    apiLbTraefikWebHostPortInput: document.getElementById('apiLbTraefikWebHostPortInput'),
    apiLbTraefikDashboardHostPortInput: document.getElementById('apiLbTraefikDashboardHostPortInput'),
    proxyBindInput: document.getElementById('proxyBindInput'),
    proxyHttpPortInput: document.getElementById('proxyHttpPortInput'),
    proxyAutostartInput: document.getElementById('proxyAutostartInput'),
    proxyHttpsInput: document.getElementById('proxyHttpsInput'),
    proxyRedirectInput: document.getElementById('proxyRedirectInput'),
    proxyHttpsSettings: document.getElementById('proxyHttpsSettings'),
    proxyHttpsPortInput: document.getElementById('proxyHttpsPortInput'),
    proxyTlsCertInput: document.getElementById('proxyTlsCertInput'),
    proxyTlsCertChooseButton: document.getElementById('proxyTlsCertChooseButton'),
    proxyTlsKeyInput: document.getElementById('proxyTlsKeyInput'),
    proxyTlsKeyChooseButton: document.getElementById('proxyTlsKeyChooseButton'),
    proxyTlsCaInput: document.getElementById('proxyTlsCaInput'),
    proxyTlsCaChooseButton: document.getElementById('proxyTlsCaChooseButton'),
    proxyTlsPassphraseInput: document.getElementById('proxyTlsPassphraseInput'),
    proxyClearPassphraseInput: document.getElementById('proxyClearPassphraseInput'),
    proxyBindError: document.getElementById('proxyBindError'),
    proxyHttpPortError: document.getElementById('proxyHttpPortError'),
    proxyHttpsPortError: document.getElementById('proxyHttpsPortError'),
    proxyTlsCertError: document.getElementById('proxyTlsCertError'),
    proxyTlsKeyError: document.getElementById('proxyTlsKeyError'),
    proxyTlsCaError: document.getElementById('proxyTlsCaError'),
    proxyValidation: document.getElementById('proxyValidation'),
    proxyStopButton: document.getElementById('proxyStopButton'),
    proxyRestartButton: document.getElementById('proxyRestartButton'),
    proxyStartButton: document.getElementById('proxyStartButton'),
    proxyInstallServiceButton: document.getElementById('proxyInstallServiceButton'),
    proxyUninstallServiceButton: document.getElementById('proxyUninstallServiceButton'),
    autoUpdateForm: document.getElementById('autoUpdateForm'),
    autoUpdateBadge: document.getElementById('autoUpdateBadge'),
    autoUpdateEnabledInput: document.getElementById('autoUpdateEnabledInput'),
    autoUpdateSettings: document.getElementById('autoUpdateSettings'),
    autoUpdateScheduleSelect: document.getElementById('autoUpdateScheduleSelect'),
    autoUpdateTimeInput: document.getElementById('autoUpdateTimeInput'),
    autoUpdateWeekdaysFieldset: document.getElementById('autoUpdateWeekdaysFieldset'),
    autoUpdateWeekdayInputs: Array.from(document.querySelectorAll('[data-auto-update-weekday]')),
    autoUpdateBackupInput: document.getElementById('autoUpdateBackupInput'),
    autoUpdateBackupReferenceText: document.getElementById('autoUpdateBackupReferenceText'),
    autoUpdateBackupSettingsButton: document.getElementById('autoUpdateBackupSettingsButton'),
    autoUpdateHealthyInput: document.getElementById('autoUpdateHealthyInput'),
    autoUpdateNextRun: document.getElementById('autoUpdateNextRun'),
    autoUpdateLastMessage: document.getElementById('autoUpdateLastMessage'),
    autoUpdateVersions: document.getElementById('autoUpdateVersions'),
    autoUpdateBlockedPanel: document.getElementById('autoUpdateBlockedPanel'),
    autoUpdateBlockedMessage: document.getElementById('autoUpdateBlockedMessage'),
    autoUpdateBlockedDebug: document.getElementById('autoUpdateBlockedDebug'),
    autoUpdateLauncherButton: document.getElementById('autoUpdateLauncherButton'),
    autoUpdateValidation: document.getElementById('autoUpdateValidation'),
    autoUpdateRunNowButton: document.getElementById('autoUpdateRunNowButton'),
    autoUpdateRunNowNote: document.getElementById('autoUpdateRunNowNote'),
    envEditorPath: document.getElementById('envEditorPath'),
    envEditorSaved: document.getElementById('envEditorSaved'),
    exportEnvButton: document.getElementById('exportEnvButton'),
    importEnvButton: document.getElementById('importEnvButton'),
    envSearchInput: document.getElementById('envSearchInput'),
    envSectionFilter: document.getElementById('envSectionFilter'),
    envValidationSummary: document.getElementById('envValidationSummary'),
    envImportReview: document.getElementById('envImportReview'),
    envImportSource: document.getElementById('envImportSource'),
    envImportBadge: document.getElementById('envImportBadge'),
    envImportSummary: document.getElementById('envImportSummary'),
    envImportDetails: document.getElementById('envImportDetails'),
    replaceMissingEnvInput: document.getElementById('replaceMissingEnvInput'),
    envImportReplacementImpact: document.getElementById('envImportReplacementImpact'),
    cancelEnvImportButton: document.getElementById('cancelEnvImportButton'),
    applyEnvImportButton: document.getElementById('applyEnvImportButton'),
    envImportResult: document.getElementById('envImportResult'),
    envImportResultTitle: document.getElementById('envImportResultTitle'),
    envImportResultMessage: document.getElementById('envImportResultMessage'),
    envEditorForm: document.getElementById('envEditorForm'),
    envFields: document.getElementById('envFields'),
    customEnvKeyInput: document.getElementById('customEnvKeyInput'),
    customEnvKeyError: document.getElementById('customEnvKeyError'),
    customEnvValueInput: document.getElementById('customEnvValueInput'),
    addCustomEnvButton: document.getElementById('addCustomEnvButton'),
    servicesSubtitle: document.getElementById('servicesSubtitle'),
    serviceAutoRefreshStatus: document.getElementById('serviceAutoRefreshStatus'),
    serviceCount: document.getElementById('serviceCount'),
    servicesBody: document.getElementById('servicesBody'),
    consoleOutput: document.getElementById('consoleOutput'),
    logServiceSelect: document.getElementById('logServiceSelect'),
    logLinesInput: document.getElementById('logLinesInput'),
    logSinceInput: document.getElementById('logSinceInput'),
    logControlStatus: document.getElementById('logControlStatus'),
    loadLogsButton: document.getElementById('loadLogsButton'),
    startLogFollowButton: document.getElementById('startLogFollowButton'),
    stopLogFollowButton: document.getElementById('stopLogFollowButton'),
    clearConsoleButton: document.getElementById('clearConsoleButton'),
    toggleInputs: Array.from(document.querySelectorAll('[data-toggle]')),
    connectionSettingInputs: Array.from(document.querySelectorAll('#jwtSecretKeyInput, #encryptionKeyInput, #passwordResetSaltInput, #databaseNameInput, #databaseUserInput, #databasePasswordInput, #databaseHostInput, #databasePortInput, #databaseSchemaInput, #databaseAuditLogSchemaInput, #databaseLogsSchemaInput, #autoCreateDatabasesInput, #databaseHostOverrideInput, #databasePortOverrideInput, #databaseUrlInput, #redisPasswordInput, #redisUrlInput, #pgbouncerPoolModeSelect, #pgbouncerMaxClientConnInput, #pgbouncerDefaultPoolSizeInput, #pgbouncerReservePoolSizeInput, #pgbouncerHostBindInput, #pgbouncerHostPortInput, #minioRootUserInput, #minioRootPasswordInput, #fileStorageProviderSelect, #fileStorageLocalBasePathInput, #fileStorageS3BucketInput, #fileStorageS3ExternalBucketInput, #fileStorageS3PrefixInput, #fileStorageS3RegionInput, #fileStorageS3EndpointUrlInput, #fileStorageS3AccessKeyIdInput, #fileStorageS3SecretAccessKeyInput, #fileStorageS3SessionTokenInput, #fileStorageGcsBucketInput, #fileStorageGcsPrefixInput, #fileStorageGcsProjectInput, #fileStorageGcsCredentialsJsonInput, #fileStorageAzureContainerInput, #fileStorageAzurePrefixInput, #fileStorageAzureConnectionStringInput, #fileStorageAzureAccountUrlInput, #fileStorageAzureCredentialInput, #fileStorageWebdavUrlInput, #fileStorageWebdavUsernameInput, #fileStorageWebdavPasswordInput, #fileStorageWebdavPrefixInput, #fileStorageWebdavVerifySslInput, #fileStorageWebdavTimeoutInput, #otelServiceNameInput, #otelExporterOtlpEndpointInput, #otelExporterOtlpInsecureInput, #otelTracesEnabledInput, #otelTracesSamplerSelect, #otelTracesSamplerArgInput, #otelMetricsEnabledInput, #otelPrometheusExporterEnabledInput, #otelLogsEnabledInput, #otelInstrumentFastapiInput, #otelInstrumentSqlalchemyInput, #otelInstrumentHttpClientsInput, #otelSqlCommenterEnabledInput, #otelCaptureHttpRouteInput, #otelCaptureHttpUserAgentInput, #otelHashHttpUserAgentInput, #otelGrpcHostBindInput, #otelGrpcHostPortInput, #otelHttpHostBindInput, #otelHttpHostPortInput, #otelPrometheusHostBindInput, #otelPrometheusHostPortInput, #otelHealthcheckHostBindInput, #otelHealthcheckHostPortInput, #jaegerUiHostBindInput, #jaegerUiHostPortInput, #jaegerCollectorHostBindInput, #jaegerCollectorHostPortInput, #prometheusHostBindInput, #prometheusHostPortInput, #alertmanagerHostBindInput, #alertmanagerHostPortInput, #grafanaHostBindInput, #grafanaHostPortInput, #grafanaAdminUserInput, #grafanaAdminPasswordInput, #grafanaRootUrlInput, #postgresExporterDataSourceUriInput, #postgresExporterDataSourceUserInput, #postgresExporterDataSourcePassInput, #redisExporterAddrInput')),
    connectionModeInputs: Array.from(document.querySelectorAll('[data-connection-toggle]')),
    connectionModeNotes: Array.from(document.querySelectorAll('[data-mode-note]')),
    redisModeInputs: Array.from(document.querySelectorAll('[data-redis-mode]')),
    redisModePanels: Array.from(document.querySelectorAll('[data-redis-mode-panel]')),
    storageModeInputs: Array.from(document.querySelectorAll('[data-storage-mode]')),
    storageModePanels: Array.from(document.querySelectorAll('[data-storage-mode-panel]')),
    infrastructureModeElements: Array.from(document.querySelectorAll('[data-visible-when-toggle]')),
    navLinks: Array.from(document.querySelectorAll('.sidebar-nav .nav-link')),
    launcherRequiredOverlay: document.getElementById('launcherRequiredOverlay'),
    launcherRequiredMessage: document.getElementById('launcherRequiredMessage'),
    launcherRequiredCurrentVersion: document.getElementById('launcherRequiredCurrentVersion'),
    launcherRequiredMinimumVersion: document.getElementById('launcherRequiredMinimumVersion'),
    launcherRequiredTargetVersion: document.getElementById('launcherRequiredTargetVersion'),
    launcherRequiredNotes: document.getElementById('launcherRequiredNotes'),
    dismissLauncherRequiredButton: document.getElementById('dismissLauncherRequiredButton'),
    openLauncherRequiredUpdateButton: document.getElementById('openLauncherRequiredUpdateButton'),
    launcherDialogOverlay: document.getElementById('launcherDialogOverlay'),
    launcherDialogTitle: document.getElementById('launcherDialogTitle'),
    launcherDialogMessage: document.getElementById('launcherDialogMessage'),
    launcherDialogInputField: document.getElementById('launcherDialogInputField'),
    launcherDialogInputLabel: document.getElementById('launcherDialogInputLabel'),
    launcherDialogInput: document.getElementById('launcherDialogInput'),
    launcherDialogCancelButton: document.getElementById('launcherDialogCancelButton'),
    launcherDialogConfirmButton: document.getElementById('launcherDialogConfirmButton'),
  };

  [
    els.devDatabaseHostPortInput,
    els.devRedisHostPortInput,
    els.minioApiHostBindInput,
    els.minioApiHostPortInput,
    els.minioConsoleHostBindInput,
    els.minioConsoleHostPortInput,
  ].forEach((input) => {
    if (input) els.connectionSettingInputs.push(input);
  });

  const toggleErrorEls = new Map(
    Array.from(document.querySelectorAll('[data-toggle-error-for]')).map((element) => [
      element.dataset.toggleErrorFor,
      element,
    ])
  );

  const selectEnhancements = new Map();
  const SELECT_CARET_SVG = Icons.withSvgAttributes("chevron", { "class": "select-caret", "aria-hidden": "true" });
  const SELECT_CHECK_SVG = Icons.withSvgAttributes("check", { "class": "select-opt-check", "aria-hidden": "true" });
  const LAUNCHER_TRANSLATIONS = {
    ar: {
      launcher_env_status_unsaved_changes: 'تغييرات غير محفوظة',
      launcher_env_empty_filter: 'لا توجد متغيرات تطابق عامل التصفية الحالي.',
      launcher_env_section_general: 'عام',
      launcher_env_status_reloading: 'جارى إعادة التحميل',
      launcher_env_status_ready: 'جاهز',
      launcher_env_status_error: 'خطأ',
      launcher_env_editor_failed: 'فشل محرر .env: {error}',
      launcher_env_status_saving: 'جارى الحفظ',
      launcher_env_status_saving_changes: 'جارى حفظ التغييرات',
      launcher_env_status_fix_errors: 'أصلح الأخطاء',
      launcher_env_status_saved_with_backup: 'تم الحفظ مع نسخة احتياطية',
      launcher_env_status_no_changes: 'لا توجد تغييرات',
      launcher_env_saved_backup_restart: 'تم حفظ .env. النسخة الاحتياطية: {backupFile}\nأعد تشغيل Omlorix لتطبيق كل التغييرات.',
      launcher_env_save_failed: 'فشل حفظ .env: {error}',
      launcher_restart_recreating_containers: 'جارى إعادة إنشاء حاويات التطبيق',
      launcher_operation_ready_at: 'Omlorix جاهز على {url}',
      launcher_restart_finished: 'تمت إعادة تشغيل Omlorix.',
      launcher_start_finished: 'تم تشغيل Omlorix.',
      launcher_backup_group_label: 'النسخ الاحتياطي والاسترداد',
      launcher_auto_update_backup_reference_enabled: 'يستخدم وجهة النسخ وتشفير الأرشيف المضبوطين في لوحة المعلومات.',
      launcher_auto_update_backup_reference_disabled: 'تبقى إعدادات النسخ مضبوطة في لوحة المعلومات عندما يكون هذا الخيار متوقفًا.',
      launcher_auto_update_backup_reference_action: 'مراجعة إعدادات النسخ',
      launcher_backup_provider_local: 'محلي',
      launcher_backup_destination_local: 'تخزين محلي (قرص الخادم)',
      launcher_backup_unavailable_title: 'تتطلب نسخ الخادم الاحتياطية تشغيل Omlorix',
      launcher_backup_unavailable_desc: 'شغّل Omlorix وانتظر حتى يصبح جاهزًا لتحميل وجهات النسخ.',
      launcher_backup_loading_title: 'جارٍ تحميل وجهات النسخ…',
      launcher_backup_loading_desc: 'تتم قراءة الوجهات وإعدادات التشفير المضبوطة في إدارة Omlorix.',
      launcher_backup_load_failed_title: 'تعذر تحميل إعدادات النسخ',
      launcher_backup_load_failed_desc: 'تأكد من جاهزية Omlorix، ثم حاول تحميل الوجهات مجددًا.',
      launcher_backup_retry_action: 'المحاولة مجددًا',
      launcher_backup_create_desc: 'أنشئ نسخة كاملة للخادم باستخدام الوجهات المضبوطة في إدارة Omlorix.',
      launcher_backup_destination_label: 'الوجهة',
      launcher_backup_encryption_title: 'تشفير الأرشيف',
      launcher_backup_encryption_desc: 'شفّر الأرشيف بعبارة مرور النسخ المضبوطة لهذا الخادم.',
      launcher_backup_setup_title: 'اضبط تشفير النسخ أولًا',
      launcher_backup_setup_desc: 'عيّن عبارة مرور في إعدادات المشغّل، وأعد تشغيل Omlorix، ثم حاول مجددًا.',
      launcher_backup_plaintext_only_desc: 'التشفير غير متاح؛ يسمح هذا الخادم بأرشيفات غير مشفرة.',
      launcher_backup_create_action: 'إنشاء نسخة للخادم',
      launcher_backup_creating_action: 'جارٍ إنشاء النسخة…',
      launcher_backup_finished: 'اكتمل النسخ الاحتياطي.',
      launcher_backup_encrypted: 'مشفرة',
      launcher_backup_plaintext: 'غير مشفرة',
      launcher_backup_result_title: 'تم إنشاء النسخة بنجاح',
      launcher_backup_result_job: 'مهمة النسخ: {jobId}',
      launcher_backup_failed_generic: 'تعذر إنشاء النسخة الاحتياطية. راجع سجل المشغّل للتفاصيل.',
      launcher_restore_action: 'استعادة نسخة احتياطية',
      launcher_restore_picker_title: 'اختر نسخة Omlorix الاحتياطية',
      launcher_restore_picker_button: 'اختر النسخة الاحتياطية',
      launcher_restore_filter: 'أرشيفات نسخ Omlorix الاحتياطية',
      launcher_restore_all_files: 'كل الملفات',
      launcher_restore_confirm_title: 'هل تريد استعادة هذا الخادم؟',
      launcher_restore_confirm_message: 'سيتوقف Omlorix، ويتحقق من {file}، وينشئ نسخة أمان، ثم يستبدل قاعدة البيانات والملفات ويعيد التشغيل. ستتم الكتابة فوق البيانات الحالية.',
      launcher_restore_confirm_action: 'استعادة الخادم',
      launcher_restore_running: 'جارٍ استعادة الخادم',
      launcher_restore_requires_running: 'يجب تشغيل Omlorix قبل بدء الاستعادة الآمنة.',
      launcher_restore_stopping_services: 'جارٍ إيقاف خدمات التطبيق قبل الاستعادة',
      launcher_update_stopping_services: 'جارٍ إيقاف خدمات التطبيق قبل ترحيل قاعدة البيانات',
      launcher_update_rollback_left_offline: 'فشل التحديث بعد أن ربما بدأت عمليات ترحيل قاعدة البيانات. يظل الإصدار المستهدف {targetVersion} محددًا، ولن يشغّل Omlorix إصدارًا أقدم. تأكد من توقف الخدمات وراجع السجلات قبل إعادة المحاولة أو استعادة نسخة احتياطية متوافقة.',
      launcher_update_pre_migration_rollback_left_offline: 'فشل التحديث قبل بدء عمليات ترحيل قاعدة البيانات، وتعذر إعادة تشغيل الإصدار السابق {previousVersion} بأمان. تم إبقاء Omlorix دون اتصال. تأكد من توقف الخدمات وراجع السجلات قبل بدء التشغيل.',
      launcher_migration_resetting: 'جارٍ إعادة تعيين حاوية الترحيل',
      launcher_migration_running: 'جارٍ تشغيل ترحيلات قاعدة البيانات',
      launcher_migration_recreating_services: 'جارٍ إعادة إنشاء خدمات التطبيق',
      launcher_restore_restoring_data: 'جارٍ التحقق من النسخة الاحتياطية واستعادة بيانات الخادم',
      launcher_restore_starting_services: 'جارٍ تشغيل Omlorix بعد الاستعادة',
      launcher_restore_ready_at: 'تمت استعادة Omlorix وهو جاهز على {url}',
      launcher_restore_finished: 'اكتملت استعادة Omlorix.',
      launcher_restore_restarting_after_failure: 'توقفت عملية الاستعادة بأمان. جارٍ إعادة تشغيل Omlorix باستخدام بيانات الخادم الحالية أو المستردة.',
      launcher_restore_stopped_safely: 'توقفت الاستعادة دون ترك بيانات خادم معدّلة، وعاد Omlorix إلى حالة سليمة بالكامل. السبب: {error}',
      launcher_restore_restart_failed: 'توقفت الاستعادة بأمان، لكن Omlorix لم يعد إلى حالة سليمة بالكامل. سبب الاستعادة: {error} خطأ إعادة التشغيل: {restartError}',
      launcher_restore_reason_target_not_empty: 'هدف الاستعادة غير فارغ.',
      launcher_restore_reason_missing_required_files: 'أرشيف النسخة الاحتياطية غير مكتمل.',
      launcher_restore_reason_checksum_mismatch: 'فشل أرشيف النسخة الاحتياطية في التحقق من المجموع الاختباري.',
      launcher_restore_reason_encryption_key_mismatch: 'يتعذر فك تشفير أرشيف النسخة الاحتياطية باستخدام مفتاح تشفير هذا الخادم.',
      launcher_restore_reason_manifest_parse_failed: 'بيان النسخة الاحتياطية غير صالح.',
      launcher_restore_reason_payload_tar_parse_failed: 'إحدى حمولات النسخة الاحتياطية غير صالحة.',
      launcher_restore_reason_archive_extracted_size_exceeded: 'يتجاوز حجم النسخة الاحتياطية الحد المضبوط للاستعادة.',
      launcher_restore_reason_insufficient_disk_space: 'لا توجد مساحة خالية كافية على القرص لاستعادة هذه النسخة الاحتياطية بأمان.',
      launcher_restore_reason_source_access_failed: 'تعذر الوصول إلى مصدر النسخة الاحتياطية.',
      launcher_restore_recovery_unconfirmed: 'فشلت الاستعادة وتعذر تأكيد الاسترداد الآمن. تم إبقاء Omlorix متوقفًا لحماية بيانات الخادم. راجع سجلات الاستعادة قبل إعادة التشغيل. الخطأ الأصلي: {error}',
      launcher_restore_startup_failed_after_restore: 'تمت استعادة بيانات الخادم، لكن تعذر تشغيل Omlorix. لم يتم التراجع عن البيانات المستعادة. خطأ بدء التشغيل: {error}',
      launcher_possible_database_downgrade: 'تعذر تشغيل Omlorix {currentVersion} بعد أن كان هذا الخادم يستخدم الإصدار {highestVersion}. ربما طبّق الإصدار الأحدث ترحيلات لقاعدة البيانات لا يستطيع الإصدار الأقدم عكسها أو قراءتها. لحماية بياناتك، استخدم Omlorix {highestVersion} أو إصدارًا أحدث، أو استعد نسخة احتياطية لقاعدة البيانات متوافقة مع {currentVersion}. خطأ بدء التشغيل الأصلي: {error}',
      launcher_server_update_label: 'تحديث الخادم',
      launcher_server_update_available_title: 'يتوفر Omlorix {latestVersion}',
      launcher_server_update_description: 'الحالي: {currentVersion} · القناة: {channel}',
      launcher_server_update_action: 'التحديث إلى {latestVersion}',
      launcher_launcher_update_label: 'تحديث المشغّل',
      launcher_launcher_update_available_title: 'يتوفر مشغّل الخادم {latestVersion}',
      launcher_launcher_update_description: 'الحالي: {currentVersion} · القناة: {channel}',
      launcher_launcher_update_action: 'تحديث المشغّل',
      launcher_server_update_launcher_check_action: 'تحقق مجدداً',
      launcher_server_update_launcher_ready_description: 'يتوفر مشغّل الخادم {latestLauncherVersion} وهو يفي بالحد الأدنى المطلوب {minimumLauncherVersion}. حدّث المشغّل أولاً.',
      launcher_server_update_launcher_feed_behind_description: 'توفر قناة تحديث المشغّل حالياً الإصدار {latestLauncherVersion}، لكن إصدار Omlorix هذا يتطلب {minimumLauncherVersion} أو أحدث. تحقق مجدداً بعد نشر مشغّل متوافق.',
      launcher_server_update_launcher_required_title: 'يلزم تحديث المشغّل لاستخدام Omlorix {latestVersion}',
      launcher_server_update_launcher_required_description: 'حدّث مشغّل خادم Omlorix إلى الإصدار {minimumLauncherVersion} أو أحدث قبل تثبيت إصدار الخادم هذا.',
      launcher_server_update_launcher_action: 'تحديث المشغّل',
      launcher_server_update_requires_running: 'شغّل Omlorix وعالج تحذيرات الإعداد قبل تثبيت هذا التحديث.',
      launcher_server_update_channel_stable: 'مستقرة',
      launcher_server_update_channel_beta: 'تجريبية',
      launcher_visitor_ips_heading: 'عناوين IP للزوار',
      launcher_visitor_ip_title_proxy_stopped: 'الوكيل متوقف',
      launcher_visitor_ip_message_proxy_stopped: 'تم تمكين وكيل المشغّل لكنه متوقف. شغّله أو فعّل التشغيل التلقائي لكي تصل عناوين IP للزوار إلى Omlorix عبر الوكيل.',
      launcher_visitor_ip_title_proxy_running: 'تم التحقق من الوكيل',
      launcher_visitor_ip_message_proxy_running: 'تحقق طلب حديث من عنوان IP للزائر والمخطط العام عبر مسار الوكيل الكامل.',
      launcher_visitor_ip_repair_external_applied: 'تم تطبيق ثقة عناوين IP للزوار. تحقق من الوكيل الخارجي باستخدام عميل خارجي.',
      launcher_visitor_ip_repair_failed: 'تعذر تطبيق إعدادات IP للزوار والتحقق منها. تمت استعادة الإعدادات السابقة.',
      launcher_visitor_ip_title_repair_failed: 'فشل الإصلاح التلقائي',
      launcher_visitor_ip_message_repair_failed: '{error} تأكد من أن Omlorix قيد التشغيل وجاهز، ثم حاول مرة أخرى. راجع وحدة التحكم للحصول على التفاصيل.',
      launcher_visitor_ip_title_verification_failed: 'فشل التحقق',
      launcher_visitor_ip_message_verification_failed: 'الوكيل قيد التشغيل، لكن تعذر على Omlorix التحقق من عنوان IP للزائر والمخطط العام عبر مسار الطلب الكامل.',
      launcher_visitor_ip_direct_probe: ' يرى فحص Docker المباشر العنوان {ip}.',
      launcher_visitor_ip_action_open_proxy: 'فتح إعدادات الوكيل',
      launcher_visitor_ip_action_start_proxy: 'تشغيل الوكيل',
      launcher_visitor_ip_action_reapply: 'إعادة تطبيق الإعدادات',
      launcher_visitor_ip_action_fix: 'إصلاح تلقائي',
      launcher_visitor_ip_title_restart_required: 'يلزم إعادة تشغيل Omlorix',
      launcher_visitor_ip_message_restart_required: 'تم حفظ إعدادات الوكيل، لكن حاوية Omlorix قيد التشغيل لا تزال تستخدم إعدادات عناوين IP السابقة. أعد تشغيل Omlorix لتطبيقها.',
      launcher_visitor_ip_action_restart_omlorix: 'إعادة تشغيل Omlorix',
      launcher_proxy_action_starting: 'جارٍ تشغيل الوكيل',
      launcher_proxy_action_started: 'تم تشغيل الوكيل.',
      launcher_proxy_action_start_failed: 'فشل تشغيل الوكيل: {error}',
      launcher_proxy_background_service_installed: 'تم تثبيت خدمة الخلفية',
      launcher_proxy_background_service_not_installed: 'لم يتم تثبيت خدمة الخلفية',
      launcher_proxy_background_service_unavailable: 'خدمة الخلفية غير متاحة في هذا الإصدار',
      launcher_proxy_install_background_service: 'تثبيت خدمة الخلفية',
      launcher_proxy_remove_background_service: 'إزالة خدمة الخلفية',
      launcher_proxy_installing_background_service: 'جارٍ تثبيت خدمة الوكيل في الخلفية',
      launcher_proxy_removing_background_service: 'جارٍ إزالة خدمة الوكيل في الخلفية',
      launcher_services_subtitle: 'الخدمات المتوقعة وحالة حاوياتها الحالية.',
      launcher_services_auto_refresh: 'يتم التحديث كل 10 ثوانٍ',
      launcher_services_auto_refresh_active: 'يتم التحديث كل ثانيتين أثناء تنفيذ إجراء',
      launcher_services_running_count: '{running}/{total} قيد التشغيل',
      launcher_service_not_created: 'لم تُنشأ',
      launcher_service_not_running: 'متوقفة',
      launcher_services_empty: 'لم يتم تكوين أي خدمات.',
      launcher_stack_all_running_detail: 'جميع خدمات Omlorix المتوقعة قيد التشغيل.',
      launcher_stack_partial_running_detail: 'هناك {count} من خدمات Omlorix المتوقعة متوقفة.',
      launcher_stack_none_running_detail: 'لا تعمل أي من خدمات Omlorix المتوقعة.',
      launcher_stack_health_issues_detail: 'هناك {count} من خدمات Omlorix المتوقعة غير سليمة حتى الآن.',
    },
    de: {
      launcher_env_status_unsaved_changes: 'Ungespeicherte Änderungen',
      launcher_env_empty_filter: 'Keine Variablen entsprechen dem aktuellen Filter.',
      launcher_env_section_general: 'Allgemein',
      launcher_env_status_reloading: 'Wird neu geladen',
      launcher_env_status_ready: 'Bereit',
      launcher_env_status_error: 'Fehler',
      launcher_env_editor_failed: '.env-Editor fehlgeschlagen: {error}',
      launcher_env_status_saving: 'Wird gespeichert',
      launcher_env_status_saving_changes: 'Änderungen werden gespeichert',
      launcher_env_status_fix_errors: 'Fehler beheben',
      launcher_env_status_saved_with_backup: 'Mit Backup gespeichert',
      launcher_env_status_no_changes: 'Keine Änderungen',
      launcher_env_saved_backup_restart: '.env gespeichert. Backup: {backupFile}\nStarte Omlorix neu, damit alle Änderungen wirksam werden.',
      launcher_env_save_failed: '.env konnte nicht gespeichert werden: {error}',
      launcher_restart_recreating_containers: 'Anwendungscontainer werden neu erstellt',
      launcher_operation_ready_at: 'Omlorix ist bereit unter {url}',
      launcher_restart_finished: 'Omlorix wurde neu gestartet.',
      launcher_start_finished: 'Omlorix wurde gestartet.',
      launcher_backup_group_label: 'Backup und Wiederherstellung',
      launcher_auto_update_backup_reference_enabled: 'Verwendet das Backup-Ziel und die Archivverschlüsselung aus dem Dashboard.',
      launcher_auto_update_backup_reference_disabled: 'Die Backup-Einstellungen im Dashboard bleiben erhalten, solange diese Option deaktiviert ist.',
      launcher_auto_update_backup_reference_action: 'Backup-Einstellungen prüfen',
      launcher_backup_provider_local: 'Lokal',
      launcher_backup_destination_local: 'Lokaler Speicher (Serverfestplatte)',
      launcher_backup_unavailable_title: 'Server-Backups erfordern ein laufendes Omlorix',
      launcher_backup_unavailable_desc: 'Starte Omlorix und warte, bis es bereit ist, um Backup-Ziele zu laden.',
      launcher_backup_loading_title: 'Backup-Ziele werden geladen…',
      launcher_backup_loading_desc: 'Die in der Omlorix-Administration konfigurierten Ziele und Verschlüsselungseinstellungen werden gelesen.',
      launcher_backup_load_failed_title: 'Backup-Einstellungen konnten nicht geladen werden',
      launcher_backup_load_failed_desc: 'Prüfe, ob Omlorix bereit ist, und lade die Backup-Ziele erneut.',
      launcher_backup_retry_action: 'Erneut versuchen',
      launcher_backup_create_desc: 'Erstelle ein vollständiges Server-Backup mit den in der Omlorix-Administration konfigurierten Zielen.',
      launcher_backup_destination_label: 'Ziel',
      launcher_backup_encryption_title: 'Archivverschlüsselung',
      launcher_backup_encryption_desc: 'Verschlüssle das Archiv mit der für diesen Server konfigurierten Backup-Passphrase.',
      launcher_backup_setup_title: 'Zuerst Backup-Verschlüsselung konfigurieren',
      launcher_backup_setup_desc: 'Lege in den Launcher-Einstellungen eine Backup-Passphrase fest, starte Omlorix neu und versuche es erneut.',
      launcher_backup_plaintext_only_desc: 'Verschlüsselung ist nicht verfügbar; dieser Server erlaubt ausdrücklich unverschlüsselte Archive.',
      launcher_backup_create_action: 'Server-Backup erstellen',
      launcher_backup_creating_action: 'Backup wird erstellt…',
      launcher_backup_finished: 'Backup abgeschlossen.',
      launcher_backup_encrypted: 'Verschlüsselt',
      launcher_backup_plaintext: 'Nicht verschlüsselt',
      launcher_backup_result_title: 'Backup erfolgreich erstellt',
      launcher_backup_result_job: 'Backup-Auftrag: {jobId}',
      launcher_backup_failed_generic: 'Das Backup konnte nicht erstellt werden. Details findest du im Launcher-Protokoll.',
      launcher_restore_action: 'Backup wiederherstellen',
      launcher_restore_picker_title: 'Omlorix-Backup auswählen',
      launcher_restore_picker_button: 'Backup auswählen',
      launcher_restore_filter: 'Omlorix-Backup-Archive',
      launcher_restore_all_files: 'Alle Dateien',
      launcher_restore_confirm_title: 'Diesen Server wiederherstellen?',
      launcher_restore_confirm_message: 'Omlorix wird beendet, {file} geprüft, ein Sicherheitsbackup erstellt und anschließend werden Datenbank und Dateien ersetzt. Die aktuellen Daten werden überschrieben.',
      launcher_restore_confirm_action: 'Server wiederherstellen',
      launcher_restore_running: 'Server wird wiederhergestellt',
      launcher_restore_requires_running: 'Omlorix muss laufen, bevor die sichere Wiederherstellung gestartet werden kann.',
      launcher_restore_stopping_services: 'Anwendungsdienste werden vor der Wiederherstellung beendet',
      launcher_update_stopping_services: 'Anwendungsdienste werden vor der Datenbankmigration beendet',
      launcher_update_rollback_left_offline: 'Das Update ist fehlgeschlagen, nachdem Datenbankmigrationen möglicherweise bereits begonnen hatten. Die Zielversion {targetVersion} bleibt ausgewählt; Omlorix startet keine ältere Version. Vergewissere dich, dass die Dienste gestoppt sind, und prüfe die Protokolle, bevor du die Zielversion erneut versuchst oder ein kompatibles Backup wiederherstellst.',
      launcher_update_pre_migration_rollback_left_offline: 'Das Update ist vor Beginn der Datenbankmigrationen fehlgeschlagen, und die vorherige Version {previousVersion} konnte nicht sicher neu gestartet werden. Omlorix wurde offline gelassen. Vergewissere dich, dass die Dienste gestoppt sind, und prüfe vor dem Start die Protokolle.',
      launcher_migration_resetting: 'Migrationscontainer wird zurückgesetzt',
      launcher_migration_running: 'Datenbankmigrationen werden ausgeführt',
      launcher_migration_recreating_services: 'Anwendungsdienste werden neu erstellt',
      launcher_restore_restoring_data: 'Backup wird geprüft und Serverdaten werden wiederhergestellt',
      launcher_restore_starting_services: 'Omlorix wird nach der Wiederherstellung gestartet',
      launcher_restore_ready_at: 'Omlorix wurde wiederhergestellt und ist unter {url} bereit',
      launcher_restore_finished: 'Omlorix-Wiederherstellung abgeschlossen.',
      launcher_restore_restarting_after_failure: 'Die Wiederherstellung wurde sicher beendet. Omlorix wird mit den vorhandenen oder wiederhergestellten Serverdaten neu gestartet.',
      launcher_restore_stopped_safely: 'Die Wiederherstellung wurde beendet, ohne geänderte Serverdaten zurückzulassen, und Omlorix ist wieder vollständig fehlerfrei. Grund: {error}',
      launcher_restore_restart_failed: 'Die Wiederherstellung wurde sicher beendet, aber Omlorix ist nicht wieder vollständig fehlerfrei. Grund: {error} Neustartfehler: {restartError}',
      launcher_restore_reason_target_not_empty: 'Das Wiederherstellungsziel ist nicht leer.',
      launcher_restore_reason_missing_required_files: 'Das Sicherungsarchiv ist unvollständig.',
      launcher_restore_reason_checksum_mismatch: 'Die Prüfsummenprüfung des Sicherungsarchivs ist fehlgeschlagen.',
      launcher_restore_reason_encryption_key_mismatch: 'Das Sicherungsarchiv kann mit dem Verschlüsselungsschlüssel dieses Servers nicht entschlüsselt werden.',
      launcher_restore_reason_manifest_parse_failed: 'Das Sicherungsmanifest ist ungültig.',
      launcher_restore_reason_payload_tar_parse_failed: 'Eine Nutzlast der Sicherung ist ungültig.',
      launcher_restore_reason_archive_extracted_size_exceeded: 'Die Sicherung überschreitet das konfigurierte Größenlimit für Wiederherstellungen.',
      launcher_restore_reason_insufficient_disk_space: 'Es ist nicht genügend freier Speicherplatz vorhanden, um diese Sicherung sicher wiederherzustellen.',
      launcher_restore_reason_source_access_failed: 'Auf die Sicherungsquelle konnte nicht zugegriffen werden.',
      launcher_restore_recovery_unconfirmed: 'Die Wiederherstellung ist fehlgeschlagen und eine sichere Wiederherstellung konnte nicht bestätigt werden. Omlorix wurde zum Schutz der Serverdaten angehalten. Prüfe vor dem Neustart die Wiederherstellungsprotokolle. Ursprünglicher Fehler: {error}',
      launcher_restore_startup_failed_after_restore: 'Die Serverdaten wurden wiederhergestellt, aber Omlorix konnte nicht gestartet werden. Die wiederhergestellten Daten wurden nicht zurückgesetzt. Startfehler: {error}',
      launcher_possible_database_downgrade: 'Omlorix {currentVersion} konnte nicht starten, nachdem dieser Server bereits mit {highestVersion} betrieben wurde. Die neuere Version hat möglicherweise Datenbankmigrationen ausgeführt, die eine ältere Version weder zurücknehmen noch lesen kann. Verwende zum Schutz deiner Daten Omlorix {highestVersion} oder neuer, oder stelle ein mit {currentVersion} kompatibles Datenbank-Backup wieder her. Ursprünglicher Startfehler: {error}',
      launcher_server_update_label: 'Serverupdate',
      launcher_server_update_available_title: 'Omlorix {latestVersion} ist verfügbar',
      launcher_server_update_description: 'Aktuell: {currentVersion} · Kanal: {channel}',
      launcher_server_update_action: 'Auf {latestVersion} aktualisieren',
      launcher_launcher_update_label: 'Launcher-Update',
      launcher_launcher_update_available_title: 'Server Launcher {latestVersion} ist verfügbar',
      launcher_launcher_update_description: 'Aktuell: {currentVersion} · Kanal: {channel}',
      launcher_launcher_update_action: 'Launcher aktualisieren',
      launcher_server_update_launcher_check_action: 'Erneut prüfen',
      launcher_server_update_launcher_ready_description: 'Server Launcher {latestLauncherVersion} ist verfügbar und erfüllt die erforderliche Mindestversion {minimumLauncherVersion}. Aktualisiere zuerst den Launcher.',
      launcher_server_update_launcher_feed_behind_description: 'Der Launcher-Kanal bietet derzeit {latestLauncherVersion}, dieses Omlorix-Release benötigt jedoch {minimumLauncherVersion} oder neuer. Prüfe erneut, nachdem ein kompatibler Launcher veröffentlicht wurde.',
      launcher_server_update_launcher_required_title: 'Für Omlorix {latestVersion} ist ein Launcher-Update erforderlich',
      launcher_server_update_launcher_required_description: 'Aktualisiere den Omlorix Server Launcher auf Version {minimumLauncherVersion} oder neuer, bevor du dieses Serverrelease installierst.',
      launcher_server_update_launcher_action: 'Launcher aktualisieren',
      launcher_server_update_requires_running: 'Starte Omlorix und behebe alle Einrichtungswarnungen, bevor du dieses Update installierst.',
      launcher_server_update_channel_stable: 'Stabil',
      launcher_server_update_channel_beta: 'Beta',
      launcher_visitor_ips_heading: 'Besucher-IPs',
      launcher_visitor_ip_title_proxy_stopped: 'Proxy angehalten',
      launcher_visitor_ip_message_proxy_stopped: 'Der Launcher-Proxy ist aktiviert, aber angehalten. Starte ihn oder aktiviere den automatischen Start, damit Besucher-IPs Omlorix über den Proxy erreichen.',
      launcher_visitor_ip_title_proxy_running: 'Proxy verifiziert',
      launcher_visitor_ip_message_proxy_running: 'Eine aktuelle Ende-zu-Ende-Anfrage hat Besucher-IP und öffentliches Protokoll über den vollständigen Proxy-Pfad verifiziert.',
      launcher_visitor_ip_repair_external_applied: 'Die Besucher-IP-Vertrauensstellung wurde angewendet. Prüfe den externen Proxy mit einem externen Client.',
      launcher_visitor_ip_repair_failed: 'Die Besucher-IP-Einstellungen konnten nicht angewendet und verifiziert werden. Die vorherige Konfiguration wurde wiederhergestellt.',
      launcher_visitor_ip_title_repair_failed: 'Automatische Korrektur fehlgeschlagen',
      launcher_visitor_ip_message_repair_failed: '{error} Stelle sicher, dass Omlorix läuft und bereit ist, und versuche es erneut. Details findest du in der Konsole.',
      launcher_visitor_ip_title_verification_failed: 'Verifizierung fehlgeschlagen',
      launcher_visitor_ip_message_verification_failed: 'Der Proxy läuft, aber Omlorix konnte Besucher-IP und öffentliches Protokoll nicht über den vollständigen Anfragepfad verifizieren.',
      launcher_visitor_ip_direct_probe: ' Die direkte Docker-Prüfung sieht {ip}.',
      launcher_visitor_ip_action_open_proxy: 'Proxy-Einstellungen öffnen',
      launcher_visitor_ip_action_start_proxy: 'Proxy starten',
      launcher_visitor_ip_action_reapply: 'Einstellungen erneut anwenden',
      launcher_visitor_ip_action_fix: 'Automatisch beheben',
      launcher_visitor_ip_title_restart_required: 'Omlorix-Neustart erforderlich',
      launcher_visitor_ip_message_restart_required: 'Die Proxy-Einstellungen sind gespeichert, aber der laufende Omlorix-Container verwendet noch die vorherige Besucher-IP-Konfiguration. Starte Omlorix neu, um sie anzuwenden.',
      launcher_visitor_ip_action_restart_omlorix: 'Omlorix neu starten',
      launcher_proxy_action_starting: 'Proxy wird gestartet',
      launcher_proxy_action_started: 'Proxy wurde gestartet.',
      launcher_proxy_action_start_failed: 'Proxy konnte nicht gestartet werden: {error}',
      launcher_proxy_background_service_installed: 'Hintergrunddienst installiert',
      launcher_proxy_background_service_not_installed: 'Hintergrunddienst nicht installiert',
      launcher_proxy_background_service_unavailable: 'Der Hintergrunddienst ist in diesem Build nicht verfügbar',
      launcher_proxy_install_background_service: 'Hintergrunddienst installieren',
      launcher_proxy_remove_background_service: 'Hintergrunddienst entfernen',
      launcher_proxy_installing_background_service: 'Proxy-Hintergrunddienst wird installiert',
      launcher_proxy_removing_background_service: 'Proxy-Hintergrunddienst wird entfernt',
      launcher_services_subtitle: 'Erwartete Dienste und ihr aktueller Containerstatus.',
      launcher_services_auto_refresh: 'Aktualisierung alle 10 Sekunden',
      launcher_services_auto_refresh_active: 'Während einer Aktion Aktualisierung alle 2 Sekunden',
      launcher_services_running_count: '{running}/{total} aktiv',
      launcher_service_not_created: 'Nicht erstellt',
      launcher_service_not_running: 'Nicht aktiv',
      launcher_services_empty: 'Es sind keine Dienste konfiguriert.',
      launcher_stack_all_running_detail: 'Alle erwarteten Omlorix-Dienste sind aktiv.',
      launcher_stack_partial_running_detail: '{count} erwartete Omlorix-Dienste sind nicht aktiv.',
      launcher_stack_none_running_detail: 'Keiner der erwarteten Omlorix-Dienste ist aktiv.',
      launcher_stack_health_issues_detail: '{count} erwartete Omlorix-Dienste sind noch nicht fehlerfrei.',
    },
    es: {
      launcher_env_status_unsaved_changes: 'Cambios sin guardar',
      launcher_env_empty_filter: 'Ninguna variable coincide con el filtro actual.',
      launcher_env_section_general: 'General',
      launcher_env_status_reloading: 'Recargando',
      launcher_env_status_ready: 'Listo',
      launcher_env_status_error: 'Error',
      launcher_env_editor_failed: 'Error del editor .env: {error}',
      launcher_env_status_saving: 'Guardando',
      launcher_env_status_saving_changes: 'Guardando cambios',
      launcher_env_status_fix_errors: 'Corrige los errores',
      launcher_env_status_saved_with_backup: 'Guardado con copia de seguridad',
      launcher_env_status_no_changes: 'Sin cambios',
      launcher_env_saved_backup_restart: '.env guardado. Copia de seguridad: {backupFile}\nReinicia Omlorix para aplicar todos los cambios.',
      launcher_env_save_failed: 'No se pudo guardar .env: {error}',
      launcher_restart_recreating_containers: 'Recreando contenedores de la aplicación',
      launcher_operation_ready_at: 'Omlorix está listo en {url}',
      launcher_restart_finished: 'Omlorix se reinició.',
      launcher_start_finished: 'Omlorix se inició.',
      launcher_backup_group_label: 'Copia y recuperación',
      launcher_auto_update_backup_reference_enabled: 'Usa el destino de copia y el cifrado del archivo configurados en el panel.',
      launcher_auto_update_backup_reference_disabled: 'Los ajustes de copia permanecen configurados en el panel mientras esta opción está desactivada.',
      launcher_auto_update_backup_reference_action: 'Revisar ajustes de copia',
      launcher_backup_provider_local: 'Local',
      launcher_backup_destination_local: 'Almacenamiento local (disco del servidor)',
      launcher_backup_unavailable_title: 'Las copias del servidor requieren que Omlorix esté en ejecución',
      launcher_backup_unavailable_desc: 'Inicia Omlorix y espera a que esté listo para cargar los destinos.',
      launcher_backup_loading_title: 'Cargando destinos de copia…',
      launcher_backup_loading_desc: 'Leyendo los destinos y ajustes de cifrado configurados en la administración de Omlorix.',
      launcher_backup_load_failed_title: 'No se pudieron cargar los ajustes de copia',
      launcher_backup_load_failed_desc: 'Comprueba que Omlorix esté listo e intenta cargar de nuevo los destinos.',
      launcher_backup_retry_action: 'Intentar de nuevo',
      launcher_backup_create_desc: 'Crea una copia completa del servidor con los destinos configurados en la administración de Omlorix.',
      launcher_backup_destination_label: 'Destino',
      launcher_backup_encryption_title: 'Cifrado del archivo',
      launcher_backup_encryption_desc: 'Cifra el archivo con la contraseña de copias configurada para este servidor.',
      launcher_backup_setup_title: 'Configura primero el cifrado de copias',
      launcher_backup_setup_desc: 'Define una contraseña en los ajustes del lanzador, reinicia Omlorix e inténtalo de nuevo.',
      launcher_backup_plaintext_only_desc: 'El cifrado no está disponible; este servidor permite expresamente archivos sin cifrar.',
      launcher_backup_create_action: 'Crear copia del servidor',
      launcher_backup_creating_action: 'Creando copia…',
      launcher_backup_finished: 'Copia finalizada.',
      launcher_backup_encrypted: 'Cifrada',
      launcher_backup_plaintext: 'Sin cifrar',
      launcher_backup_result_title: 'Copia creada correctamente',
      launcher_backup_result_job: 'Tarea de copia: {jobId}',
      launcher_backup_failed_generic: 'No se pudo crear la copia. Consulta el registro del lanzador para ver los detalles.',
      launcher_restore_action: 'Restaurar copia',
      launcher_restore_picker_title: 'Seleccionar copia de Omlorix',
      launcher_restore_picker_button: 'Seleccionar copia',
      launcher_restore_filter: 'Archivos de copia de Omlorix',
      launcher_restore_all_files: 'Todos los archivos',
      launcher_restore_confirm_title: '¿Restaurar este servidor?',
      launcher_restore_confirm_message: 'Omlorix se detendrá, verificará {file}, creará una copia de seguridad y sustituirá la base de datos y los archivos antes de reiniciarse. Los datos actuales se sobrescribirán.',
      launcher_restore_confirm_action: 'Restaurar servidor',
      launcher_restore_running: 'Restaurando servidor',
      launcher_restore_requires_running: 'Omlorix debe estar en ejecución antes de iniciar una restauración segura.',
      launcher_restore_stopping_services: 'Deteniendo servicios antes de restaurar',
      launcher_update_stopping_services: 'Deteniendo los servicios de la aplicación antes de migrar la base de datos',
      launcher_update_rollback_left_offline: 'La actualización falló después de que las migraciones de la base de datos pudieran haber comenzado. La versión de destino {targetVersion} sigue seleccionada y Omlorix no iniciará una versión anterior. Confirma que los servicios estén detenidos y revisa los registros antes de reintentar o restaurar una copia compatible.',
      launcher_update_pre_migration_rollback_left_offline: 'La actualización falló antes de que comenzaran las migraciones de la base de datos y no se pudo reiniciar de forma segura la versión anterior {previousVersion}. Omlorix quedó sin conexión. Confirma que los servicios estén detenidos y revisa los registros antes de iniciarlo.',
      launcher_migration_resetting: 'Restableciendo el contenedor de migración',
      launcher_migration_running: 'Ejecutando las migraciones de la base de datos',
      launcher_migration_recreating_services: 'Recreando los servicios de la aplicación',
      launcher_restore_restoring_data: 'Verificando la copia y restaurando los datos',
      launcher_restore_starting_services: 'Iniciando Omlorix después de restaurar',
      launcher_restore_ready_at: 'Omlorix se restauró y está disponible en {url}',
      launcher_restore_finished: 'Restauración de Omlorix completada.',
      launcher_restore_restarting_after_failure: 'La restauración se detuvo de forma segura. Reiniciando Omlorix con los datos existentes o recuperados del servidor.',
      launcher_restore_stopped_safely: 'La restauración se detuvo sin dejar datos modificados y Omlorix volvió a estar completamente operativo. Motivo: {error}',
      launcher_restore_restart_failed: 'La restauración se detuvo de forma segura, pero Omlorix no volvió a estar completamente operativo. Motivo: {error}. Error de reinicio: {restartError}',
      launcher_restore_reason_target_not_empty: 'El destino de restauración no está vacío.',
      launcher_restore_reason_missing_required_files: 'El archivo de copia de seguridad está incompleto.',
      launcher_restore_reason_checksum_mismatch: 'El archivo de copia de seguridad no superó la verificación de suma de comprobación.',
      launcher_restore_reason_encryption_key_mismatch: 'El archivo de copia de seguridad no se puede descifrar con la clave de cifrado de este servidor.',
      launcher_restore_reason_manifest_parse_failed: 'El manifiesto de la copia de seguridad no es válido.',
      launcher_restore_reason_payload_tar_parse_failed: 'Una carga útil de la copia de seguridad no es válida.',
      launcher_restore_reason_archive_extracted_size_exceeded: 'La copia de seguridad supera el límite de tamaño configurado para la restauración.',
      launcher_restore_reason_insufficient_disk_space: 'No hay suficiente espacio libre en disco para restaurar esta copia de seguridad de forma segura.',
      launcher_restore_reason_source_access_failed: 'No se pudo acceder al origen de la copia de seguridad.',
      launcher_restore_recovery_unconfirmed: 'La restauración falló y no se pudo confirmar una recuperación segura. Omlorix se dejó detenido para proteger los datos del servidor. Revisa los registros de restauración antes de reiniciar. Error original: {error}',
      launcher_restore_startup_failed_after_restore: 'Los datos del servidor se restauraron, pero Omlorix no pudo iniciarse. Los datos restaurados no se revirtieron. Error de inicio: {error}',
      launcher_possible_database_downgrade: 'Omlorix {currentVersion} no pudo iniciarse después de que este servidor utilizara la versión {highestVersion}. Es posible que la versión más reciente haya aplicado migraciones de base de datos que una versión anterior no puede revertir ni leer. Para proteger tus datos, usa Omlorix {highestVersion} o posterior, o restaura una copia de la base de datos compatible con {currentVersion}. Error de inicio original: {error}',
      launcher_server_update_label: 'Actualización del servidor',
      launcher_server_update_available_title: 'Omlorix {latestVersion} está disponible',
      launcher_server_update_description: 'Actual: {currentVersion} · Canal: {channel}',
      launcher_server_update_action: 'Actualizar a {latestVersion}',
      launcher_launcher_update_label: 'Actualización del iniciador',
      launcher_launcher_update_available_title: 'Server Launcher {latestVersion} está disponible',
      launcher_launcher_update_description: 'Actual: {currentVersion} · Canal: {channel}',
      launcher_launcher_update_action: 'Actualizar iniciador',
      launcher_server_update_launcher_check_action: 'Volver a comprobar',
      launcher_server_update_launcher_ready_description: 'Server Launcher {latestLauncherVersion} está disponible y cumple la versión mínima requerida {minimumLauncherVersion}. Actualiza primero el iniciador.',
      launcher_server_update_launcher_feed_behind_description: 'El canal del iniciador ofrece actualmente {latestLauncherVersion}, pero esta versión de Omlorix requiere {minimumLauncherVersion} o posterior. Vuelve a comprobarlo cuando se publique un iniciador compatible.',
      launcher_server_update_launcher_required_title: 'Omlorix {latestVersion} requiere actualizar el iniciador',
      launcher_server_update_launcher_required_description: 'Actualiza Omlorix Server Launcher a la versión {minimumLauncherVersion} o posterior antes de instalar esta versión del servidor.',
      launcher_server_update_launcher_action: 'Actualizar iniciador',
      launcher_server_update_requires_running: 'Inicia Omlorix y resuelve los avisos de configuración antes de instalar esta actualización.',
      launcher_server_update_channel_stable: 'Estable',
      launcher_server_update_channel_beta: 'Beta',
      launcher_visitor_ips_heading: 'IP de visitantes',
      launcher_visitor_ip_title_proxy_stopped: 'Proxy detenido',
      launcher_visitor_ip_message_proxy_stopped: 'El proxy del iniciador está activado, pero detenido. Inícialo o activa el inicio automático para que las IP de los visitantes lleguen a Omlorix a través del proxy.',
      launcher_visitor_ip_title_proxy_running: 'Proxy verificado',
      launcher_visitor_ip_message_proxy_running: 'Una solicitud integral reciente verificó la IP del visitante y el esquema público a través de toda la ruta del proxy.',
      launcher_visitor_ip_repair_external_applied: 'Se aplicó la confianza de IP de visitantes. Verifica el proxy externo desde un cliente externo.',
      launcher_visitor_ip_repair_failed: 'No se pudo aplicar ni verificar la configuración de IP de visitantes. Se restauró la configuración anterior.',
      launcher_visitor_ip_title_repair_failed: 'La corrección automática ha fallado',
      launcher_visitor_ip_message_repair_failed: '{error} Asegúrate de que Omlorix esté en ejecución y listo, y vuelve a intentarlo. Consulta la consola para obtener más información.',
      launcher_visitor_ip_title_verification_failed: 'Error de verificación',
      launcher_visitor_ip_message_verification_failed: 'El proxy está en ejecución, pero Omlorix no pudo verificar la IP del visitante y el esquema público a través de toda la ruta de la solicitud.',
      launcher_visitor_ip_direct_probe: ' La comprobación directa de Docker detecta {ip}.',
      launcher_visitor_ip_action_open_proxy: 'Abrir ajustes del proxy',
      launcher_visitor_ip_action_start_proxy: 'Iniciar proxy',
      launcher_visitor_ip_action_reapply: 'Volver a aplicar ajustes',
      launcher_visitor_ip_action_fix: 'Corregir automáticamente',
      launcher_visitor_ip_title_restart_required: 'Es necesario reiniciar Omlorix',
      launcher_visitor_ip_message_restart_required: 'Los ajustes del proxy están guardados, pero el contenedor de Omlorix en ejecución aún usa la configuración anterior de IP de visitantes. Reinicia Omlorix para aplicarlos.',
      launcher_visitor_ip_action_restart_omlorix: 'Reiniciar Omlorix',
      launcher_proxy_action_starting: 'Iniciando proxy',
      launcher_proxy_action_started: 'Proxy iniciado.',
      launcher_proxy_action_start_failed: 'No se pudo iniciar el proxy: {error}',
      launcher_proxy_background_service_installed: 'Servicio en segundo plano instalado',
      launcher_proxy_background_service_not_installed: 'Servicio en segundo plano no instalado',
      launcher_proxy_background_service_unavailable: 'El servicio en segundo plano no está disponible en esta compilación',
      launcher_proxy_install_background_service: 'Instalar servicio en segundo plano',
      launcher_proxy_remove_background_service: 'Eliminar servicio en segundo plano',
      launcher_proxy_installing_background_service: 'Instalando el servicio proxy en segundo plano',
      launcher_proxy_removing_background_service: 'Eliminando el servicio proxy en segundo plano',
      launcher_services_subtitle: 'Servicios esperados y estado actual de sus contenedores.',
      launcher_services_auto_refresh: 'Se actualiza cada 10 segundos',
      launcher_services_auto_refresh_active: 'Se actualiza cada 2 segundos durante una acción',
      launcher_services_running_count: '{running}/{total} en ejecución',
      launcher_service_not_created: 'No creado',
      launcher_service_not_running: 'No está en ejecución',
      launcher_services_empty: 'No hay servicios configurados.',
      launcher_stack_all_running_detail: 'Todos los servicios esperados de Omlorix están en ejecución.',
      launcher_stack_partial_running_detail: '{count} servicios esperados de Omlorix no están en ejecución.',
      launcher_stack_none_running_detail: 'Ninguno de los servicios esperados de Omlorix está en ejecución.',
      launcher_stack_health_issues_detail: '{count} servicios esperados de Omlorix aún no están en buen estado.',
    },
    fr: {
      launcher_env_status_unsaved_changes: 'Modifications non enregistrées',
      launcher_env_empty_filter: 'Aucune variable ne correspond au filtre actuel.',
      launcher_env_section_general: 'Général',
      launcher_env_status_reloading: 'Rechargement',
      launcher_env_status_ready: 'Prêt',
      launcher_env_status_error: 'Erreur',
      launcher_env_editor_failed: 'Échec de l’éditeur .env : {error}',
      launcher_env_status_saving: 'Enregistrement',
      launcher_env_status_saving_changes: 'Enregistrement des modifications',
      launcher_env_status_fix_errors: 'Corrigez les erreurs',
      launcher_env_status_saved_with_backup: 'Enregistré avec sauvegarde',
      launcher_env_status_no_changes: 'Aucune modification',
      launcher_env_saved_backup_restart: '.env enregistré. Sauvegarde : {backupFile}\nRedémarrez Omlorix pour appliquer toutes les modifications.',
      launcher_env_save_failed: 'Échec de l’enregistrement .env : {error}',
      launcher_restart_recreating_containers: 'Recréation des conteneurs de l’application',
      launcher_operation_ready_at: 'Omlorix est prêt sur {url}',
      launcher_restart_finished: 'Omlorix a redémarré.',
      launcher_start_finished: 'Omlorix a démarré.',
      launcher_backup_group_label: 'Sauvegarde et récupération',
      launcher_auto_update_backup_reference_enabled: 'Utilise la destination et le chiffrement d’archive configurés dans le tableau de bord.',
      launcher_auto_update_backup_reference_disabled: 'Les réglages de sauvegarde du tableau de bord restent configurés lorsque cette option est désactivée.',
      launcher_auto_update_backup_reference_action: 'Vérifier les réglages de sauvegarde',
      launcher_backup_provider_local: 'Local',
      launcher_backup_destination_local: 'Stockage local (disque du serveur)',
      launcher_backup_unavailable_title: 'Les sauvegardes serveur nécessitent que Omlorix fonctionne',
      launcher_backup_unavailable_desc: 'Démarrez Omlorix et attendez qu’il soit prêt pour charger les destinations.',
      launcher_backup_loading_title: 'Chargement des destinations…',
      launcher_backup_loading_desc: 'Lecture des destinations et des réglages de chiffrement configurés dans l’administration Omlorix.',
      launcher_backup_load_failed_title: 'Impossible de charger les réglages de sauvegarde',
      launcher_backup_load_failed_desc: 'Vérifiez que Omlorix est prêt, puis rechargez les destinations.',
      launcher_backup_retry_action: 'Réessayer',
      launcher_backup_create_desc: 'Créez une sauvegarde complète du serveur avec les destinations configurées dans l’administration Omlorix.',
      launcher_backup_destination_label: 'Destination',
      launcher_backup_encryption_title: 'Chiffrement de l’archive',
      launcher_backup_encryption_desc: 'Chiffrez l’archive avec la phrase secrète configurée pour ce serveur.',
      launcher_backup_setup_title: 'Configurez d’abord le chiffrement',
      launcher_backup_setup_desc: 'Définissez une phrase secrète dans les réglages du lanceur, redémarrez Omlorix et réessayez.',
      launcher_backup_plaintext_only_desc: 'Le chiffrement est indisponible ; ce serveur autorise explicitement les archives non chiffrées.',
      launcher_backup_create_action: 'Créer une sauvegarde serveur',
      launcher_backup_creating_action: 'Création de la sauvegarde…',
      launcher_backup_finished: 'Sauvegarde terminée.',
      launcher_backup_encrypted: 'Chiffrée',
      launcher_backup_plaintext: 'Non chiffrée',
      launcher_backup_result_title: 'Sauvegarde créée avec succès',
      launcher_backup_result_job: 'Tâche de sauvegarde : {jobId}',
      launcher_backup_failed_generic: 'La sauvegarde n’a pas pu être créée. Consultez le journal du lanceur pour plus de détails.',
      launcher_restore_action: 'Restaurer une sauvegarde',
      launcher_restore_picker_title: 'Choisir une sauvegarde Omlorix',
      launcher_restore_picker_button: 'Choisir la sauvegarde',
      launcher_restore_filter: 'Archives de sauvegarde Omlorix',
      launcher_restore_all_files: 'Tous les fichiers',
      launcher_restore_confirm_title: 'Restaurer ce serveur ?',
      launcher_restore_confirm_message: 'Omlorix va s’arrêter, vérifier {file}, créer une sauvegarde de sécurité, puis remplacer la base de données et les fichiers avant de redémarrer. Les données actuelles seront écrasées.',
      launcher_restore_confirm_action: 'Restaurer le serveur',
      launcher_restore_running: 'Restauration du serveur',
      launcher_restore_requires_running: 'Omlorix doit fonctionner avant de lancer une restauration sûre.',
      launcher_restore_stopping_services: 'Arrêt des services avant la restauration',
      launcher_update_stopping_services: 'Arrêt des services applicatifs avant la migration de la base de données',
      launcher_update_rollback_left_offline: 'La mise à jour a échoué alors que les migrations de base de données avaient peut-être déjà commencé. La version cible {targetVersion} reste sélectionnée et Omlorix ne démarrera pas une version antérieure. Vérifiez que les services sont arrêtés et consultez les journaux avant de réessayer ou de restaurer une sauvegarde compatible.',
      launcher_update_pre_migration_rollback_left_offline: 'La mise à jour a échoué avant le début des migrations de base de données et la version précédente {previousVersion} n’a pas pu être redémarrée en toute sécurité. Omlorix a été laissé hors ligne. Vérifiez que les services sont arrêtés et consultez les journaux avant de le démarrer.',
      launcher_migration_resetting: 'Réinitialisation du conteneur de migration',
      launcher_migration_running: 'Exécution des migrations de la base de données',
      launcher_migration_recreating_services: 'Recréation des services applicatifs',
      launcher_restore_restoring_data: 'Vérification de la sauvegarde et restauration des données',
      launcher_restore_starting_services: 'Démarrage de Omlorix après la restauration',
      launcher_restore_ready_at: 'Omlorix est restauré et disponible à l’adresse {url}',
      launcher_restore_finished: 'Restauration de Omlorix terminée.',
      launcher_restore_restarting_after_failure: 'La restauration s’est arrêtée en toute sécurité. Redémarrage de Omlorix avec les données serveur existantes ou récupérées.',
      launcher_restore_stopped_safely: 'La restauration s’est arrêtée sans laisser de données serveur modifiées, et Omlorix est de nouveau entièrement opérationnel. Motif : {error}',
      launcher_restore_restart_failed: 'La restauration s’est arrêtée en toute sécurité, mais Omlorix n’est pas redevenu entièrement opérationnel. Motif : {error}. Erreur de redémarrage : {restartError}',
      launcher_restore_reason_target_not_empty: 'La cible de restauration n’est pas vide.',
      launcher_restore_reason_missing_required_files: 'L’archive de sauvegarde est incomplète.',
      launcher_restore_reason_checksum_mismatch: 'L’archive de sauvegarde n’a pas réussi la vérification de la somme de contrôle.',
      launcher_restore_reason_encryption_key_mismatch: 'L’archive de sauvegarde ne peut pas être déchiffrée avec la clé de chiffrement de ce serveur.',
      launcher_restore_reason_manifest_parse_failed: 'Le manifeste de sauvegarde n’est pas valide.',
      launcher_restore_reason_payload_tar_parse_failed: 'Une charge utile de sauvegarde n’est pas valide.',
      launcher_restore_reason_archive_extracted_size_exceeded: 'La sauvegarde dépasse la limite de taille configurée pour la restauration.',
      launcher_restore_reason_insufficient_disk_space: 'L’espace disque libre est insuffisant pour restaurer cette sauvegarde en toute sécurité.',
      launcher_restore_reason_source_access_failed: 'La source de sauvegarde est inaccessible.',
      launcher_restore_recovery_unconfirmed: 'La restauration a échoué et la récupération sûre n’a pas pu être confirmée. Omlorix a été laissé à l’arrêt pour protéger les données du serveur. Consultez les journaux de restauration avant de redémarrer. Erreur initiale : {error}',
      launcher_restore_startup_failed_after_restore: 'Les données du serveur ont été restaurées, mais Omlorix n’a pas pu démarrer. Les données restaurées n’ont pas été annulées. Erreur de démarrage : {error}',
      launcher_possible_database_downgrade: 'Omlorix {currentVersion} n’a pas pu démarrer après que ce serveur a utilisé la version {highestVersion}. La version plus récente a peut-être appliqué des migrations de base de données qu’une version antérieure ne peut ni annuler ni lire. Pour protéger vos données, utilisez Omlorix {highestVersion} ou une version ultérieure, ou restaurez une sauvegarde de base de données compatible avec {currentVersion}. Erreur de démarrage initiale : {error}',
      launcher_server_update_label: 'Mise à jour du serveur',
      launcher_server_update_available_title: 'Omlorix {latestVersion} est disponible',
      launcher_server_update_description: 'Actuelle : {currentVersion} · Canal : {channel}',
      launcher_server_update_action: 'Mettre à jour vers {latestVersion}',
      launcher_launcher_update_label: 'Mise à jour du lanceur',
      launcher_launcher_update_available_title: 'Server Launcher {latestVersion} est disponible',
      launcher_launcher_update_description: 'Actuelle : {currentVersion} · Canal : {channel}',
      launcher_launcher_update_action: 'Mettre à jour le lanceur',
      launcher_server_update_launcher_check_action: 'Vérifier à nouveau',
      launcher_server_update_launcher_ready_description: 'Server Launcher {latestLauncherVersion} est disponible et satisfait à la version minimale requise {minimumLauncherVersion}. Mettez d’abord le lanceur à jour.',
      launcher_server_update_launcher_feed_behind_description: 'Le canal du lanceur propose actuellement {latestLauncherVersion}, mais cette version de Omlorix nécessite {minimumLauncherVersion} ou une version ultérieure. Réessayez lorsqu’un lanceur compatible aura été publié.',
      launcher_server_update_launcher_required_title: 'Omlorix {latestVersion} nécessite une mise à jour du lanceur',
      launcher_server_update_launcher_required_description: 'Mettez à jour Omlorix Server Launcher vers la version {minimumLauncherVersion} ou une version ultérieure avant d’installer cette version du serveur.',
      launcher_server_update_launcher_action: 'Mettre à jour le lanceur',
      launcher_server_update_requires_running: 'Démarrez Omlorix et corrigez les avertissements de configuration avant d’installer cette mise à jour.',
      launcher_server_update_channel_stable: 'Stable',
      launcher_server_update_channel_beta: 'Bêta',
      launcher_visitor_ips_heading: 'IP des visiteurs',
      launcher_visitor_ip_title_proxy_stopped: 'Proxy arrêté',
      launcher_visitor_ip_message_proxy_stopped: 'Le proxy du lanceur est activé mais arrêté. Démarrez-le ou activez le démarrage automatique afin que les IP des visiteurs atteignent Omlorix via le proxy.',
      launcher_visitor_ip_title_proxy_running: 'Proxy vérifié',
      launcher_visitor_ip_message_proxy_running: 'Une requête de bout en bout récente a vérifié l’IP visiteur et le protocole public sur l’ensemble du chemin proxy.',
      launcher_visitor_ip_repair_external_applied: 'La confiance des IP visiteurs a été appliquée. Vérifiez le proxy externe depuis un client externe.',
      launcher_visitor_ip_repair_failed: 'Les paramètres d’IP visiteurs n’ont pas pu être appliqués et vérifiés. La configuration précédente a été restaurée.',
      launcher_visitor_ip_title_repair_failed: 'La correction automatique a échoué',
      launcher_visitor_ip_message_repair_failed: '{error} Vérifiez qu’Omlorix est en cours d’exécution et prêt, puis réessayez. Consultez la console pour plus de détails.',
      launcher_visitor_ip_title_verification_failed: 'Échec de la vérification',
      launcher_visitor_ip_message_verification_failed: 'Le proxy est actif, mais Omlorix n’a pas pu vérifier l’IP visiteur et le protocole public sur l’ensemble du chemin de requête.',
      launcher_visitor_ip_direct_probe: ' Le contrôle Docker direct voit {ip}.',
      launcher_visitor_ip_action_open_proxy: 'Ouvrir les paramètres du proxy',
      launcher_visitor_ip_action_start_proxy: 'Démarrer le proxy',
      launcher_visitor_ip_action_reapply: 'Réappliquer les paramètres',
      launcher_visitor_ip_action_fix: 'Corriger automatiquement',
      launcher_visitor_ip_title_restart_required: 'Redémarrage de Omlorix requis',
      launcher_visitor_ip_message_restart_required: 'Les paramètres du proxy sont enregistrés, mais le conteneur Omlorix en cours d’exécution utilise encore l’ancienne configuration des IP visiteurs. Redémarrez Omlorix pour les appliquer.',
      launcher_visitor_ip_action_restart_omlorix: 'Redémarrer Omlorix',
      launcher_proxy_action_starting: 'Démarrage du proxy',
      launcher_proxy_action_started: 'Proxy démarré.',
      launcher_proxy_action_start_failed: 'Échec du démarrage du proxy : {error}',
      launcher_proxy_background_service_installed: 'Service d’arrière-plan installé',
      launcher_proxy_background_service_not_installed: 'Service d’arrière-plan non installé',
      launcher_proxy_background_service_unavailable: 'Le service d’arrière-plan n’est pas disponible dans cette version',
      launcher_proxy_install_background_service: 'Installer le service d’arrière-plan',
      launcher_proxy_remove_background_service: 'Supprimer le service d’arrière-plan',
      launcher_proxy_installing_background_service: 'Installation du service proxy d’arrière-plan',
      launcher_proxy_removing_background_service: 'Suppression du service proxy d’arrière-plan',
      launcher_services_subtitle: 'Services attendus et état actuel de leurs conteneurs.',
      launcher_services_auto_refresh: 'Actualisation toutes les 10 secondes',
      launcher_services_auto_refresh_active: 'Actualisation toutes les 2 secondes pendant une action',
      launcher_services_running_count: '{running}/{total} en cours d’exécution',
      launcher_service_not_created: 'Non créé',
      launcher_service_not_running: 'Non actif',
      launcher_services_empty: 'Aucun service n’est configuré.',
      launcher_stack_all_running_detail: 'Tous les services Omlorix attendus sont en cours d’exécution.',
      launcher_stack_partial_running_detail: '{count} services Omlorix attendus ne sont pas en cours d’exécution.',
      launcher_stack_none_running_detail: 'Aucun des services Omlorix attendus n’est en cours d’exécution.',
      launcher_stack_health_issues_detail: '{count} services Omlorix attendus ne sont pas encore sains.',
    },
    hi: {
      launcher_env_status_unsaved_changes: 'बदलाव सहेजे नहीं गए',
      launcher_env_empty_filter: 'मौजूदा फिल्टर से कोई वेरिएबल मेल नहीं खाता.',
      launcher_env_section_general: 'सामान्य',
      launcher_env_status_reloading: 'फिर से लोड हो रहा है',
      launcher_env_status_ready: 'तैयार',
      launcher_env_status_error: 'त्रुटि',
      launcher_env_editor_failed: '.env एडिटर विफल: {error}',
      launcher_env_status_saving: 'सहेजा जा रहा है',
      launcher_env_status_saving_changes: 'बदलाव सहेजे जा रहे हैं',
      launcher_env_status_fix_errors: 'त्रुटियां ठीक करें',
      launcher_env_status_saved_with_backup: 'बैकअप के साथ सहेजा गया',
      launcher_env_status_no_changes: 'कोई बदलाव नहीं',
      launcher_env_saved_backup_restart: '.env सहेजा गया. बैकअप: {backupFile}\nसभी बदलाव लागू करने के लिए Omlorix को फिर से शुरू करें.',
      launcher_env_save_failed: '.env सहेजना विफल: {error}',
      launcher_restart_recreating_containers: 'एप्लिकेशन कंटेनर फिर से बनाए जा रहे हैं',
      launcher_operation_ready_at: 'Omlorix {url} पर तैयार है',
      launcher_restart_finished: 'Omlorix फिर से शुरू हो गया.',
      launcher_start_finished: 'Omlorix शुरू हो गया।',
      launcher_backup_group_label: 'बैकअप और पुनर्प्राप्ति',
      launcher_auto_update_backup_reference_enabled: 'डैशबोर्ड में सेट बैकअप गंतव्य और आर्काइव एन्क्रिप्शन का उपयोग करता है।',
      launcher_auto_update_backup_reference_disabled: 'यह विकल्प बंद होने पर भी बैकअप सेटिंग डैशबोर्ड में सुरक्षित रहती हैं।',
      launcher_auto_update_backup_reference_action: 'बैकअप सेटिंग देखें',
      launcher_backup_provider_local: 'स्थानीय',
      launcher_backup_destination_local: 'स्थानीय स्टोरेज (सर्वर डिस्क)',
      launcher_backup_unavailable_title: 'सर्वर बैकअप के लिए Omlorix का चलना आवश्यक है',
      launcher_backup_unavailable_desc: 'Omlorix शुरू करें और गंतव्य लोड होने तक प्रतीक्षा करें।',
      launcher_backup_loading_title: 'बैकअप गंतव्य लोड हो रहे हैं…',
      launcher_backup_loading_desc: 'Omlorix Admin में सेट गंतव्य और एन्क्रिप्शन पढ़े जा रहे हैं।',
      launcher_backup_load_failed_title: 'बैकअप सेटिंग लोड नहीं हो सकीं',
      launcher_backup_load_failed_desc: 'जाँचें कि Omlorix तैयार है, फिर गंतव्य दोबारा लोड करें।',
      launcher_backup_retry_action: 'फिर प्रयास करें',
      launcher_backup_create_desc: 'Omlorix Admin में सेट गंतव्यों से पूरा सर्वर बैकअप बनाएँ।',
      launcher_backup_destination_label: 'गंतव्य',
      launcher_backup_encryption_title: 'आर्काइव एन्क्रिप्शन',
      launcher_backup_encryption_desc: 'सर्वर के सेट बैकअप पासफ़्रेज़ से आर्काइव एन्क्रिप्ट करें।',
      launcher_backup_setup_title: 'पहले बैकअप एन्क्रिप्शन सेट करें',
      launcher_backup_setup_desc: 'लॉन्चर सेटिंग में पासफ़्रेज़ सेट करें, Omlorix पुनः शुरू करें और फिर प्रयास करें।',
      launcher_backup_plaintext_only_desc: 'एन्क्रिप्शन उपलब्ध नहीं है; यह सर्वर अनएन्क्रिप्टेड आर्काइव की अनुमति देता है।',
      launcher_backup_create_action: 'सर्वर बैकअप बनाएँ',
      launcher_backup_creating_action: 'बैकअप बन रहा है…',
      launcher_backup_finished: 'बैकअप पूरा हुआ।',
      launcher_backup_encrypted: 'एन्क्रिप्टेड',
      launcher_backup_plaintext: 'एन्क्रिप्टेड नहीं',
      launcher_backup_result_title: 'बैकअप सफलतापूर्वक बना',
      launcher_backup_result_job: 'बैकअप जॉब: {jobId}',
      launcher_backup_failed_generic: 'बैकअप नहीं बनाया जा सका। विवरण के लिए लॉन्चर लॉग देखें।',
      launcher_restore_action: 'बैकअप पुनर्स्थापित करें',
      launcher_restore_picker_title: 'Omlorix बैकअप चुनें',
      launcher_restore_picker_button: 'बैकअप चुनें',
      launcher_restore_filter: 'Omlorix बैकअप अभिलेख',
      launcher_restore_all_files: 'सभी फ़ाइलें',
      launcher_restore_confirm_title: 'इस सर्वर को पुनर्स्थापित करें?',
      launcher_restore_confirm_message: 'Omlorix रुकेगा, {file} की जाँच करेगा, सुरक्षा बैकअप बनाएगा, फिर डेटाबेस और फ़ाइलें बदलकर पुनः शुरू होगा। मौजूदा डेटा अधिलेखित होगा।',
      launcher_restore_confirm_action: 'सर्वर पुनर्स्थापित करें',
      launcher_restore_running: 'सर्वर पुनर्स्थापित हो रहा है',
      launcher_restore_requires_running: 'सुरक्षित पुनर्स्थापना शुरू करने से पहले Omlorix चलना चाहिए।',
      launcher_restore_stopping_services: 'पुनर्स्थापना से पहले सेवाएँ रोकी जा रही हैं',
      launcher_update_stopping_services: 'डेटाबेस माइग्रेशन से पहले एप्लिकेशन सेवाएँ रोकी जा रही हैं',
      launcher_update_rollback_left_offline: 'अपडेट उस समय विफल हुआ जब डेटाबेस माइग्रेशन शुरू हो चुके हो सकते थे। लक्ष्य संस्करण {targetVersion} चुना रहेगा और Omlorix कोई पुराना संस्करण शुरू नहीं करेगा। पुनः प्रयास या संगत बैकअप पुनर्स्थापित करने से पहले सेवाओं के बंद होने की पुष्टि करें और लॉग देखें।',
      launcher_update_pre_migration_rollback_left_offline: 'डेटाबेस माइग्रेशन शुरू होने से पहले अपडेट विफल हुआ और पिछला संस्करण {previousVersion} सुरक्षित रूप से फिर शुरू नहीं किया जा सका। Omlorix को ऑफ़लाइन रखा गया है। शुरू करने से पहले सेवाओं के बंद होने की पुष्टि करें और लॉग देखें।',
      launcher_migration_resetting: 'माइग्रेशन कंटेनर रीसेट किया जा रहा है',
      launcher_migration_running: 'डेटाबेस माइग्रेशन चलाए जा रहे हैं',
      launcher_migration_recreating_services: 'एप्लिकेशन सेवाएँ फिर से बनाई जा रही हैं',
      launcher_restore_restoring_data: 'बैकअप जाँचा और सर्वर डेटा पुनर्स्थापित किया जा रहा है',
      launcher_restore_starting_services: 'पुनर्स्थापना के बाद Omlorix शुरू हो रहा है',
      launcher_restore_ready_at: 'Omlorix पुनर्स्थापित है और {url} पर तैयार है',
      launcher_restore_finished: 'Omlorix पुनर्स्थापना पूरी हुई।',
      launcher_restore_restarting_after_failure: 'पुनर्स्थापना सुरक्षित रूप से रुक गई। मौजूदा या पुनर्प्राप्त सर्वर डेटा के साथ Omlorix पुनः शुरू किया जा रहा है।',
      launcher_restore_stopped_safely: 'पुनर्स्थापना बदला हुआ सर्वर डेटा छोड़े बिना रुक गई और Omlorix फिर से पूरी तरह स्वस्थ है। कारण: {error}',
      launcher_restore_restart_failed: 'पुनर्स्थापना सुरक्षित रूप से रुक गई, लेकिन Omlorix फिर से पूरी तरह स्वस्थ नहीं हुआ। पुनर्स्थापना कारण: {error} पुनः आरंभ त्रुटि: {restartError}',
      launcher_restore_reason_target_not_empty: 'पुनर्स्थापना लक्ष्य खाली नहीं है।',
      launcher_restore_reason_missing_required_files: 'बैकअप संग्रह अधूरा है।',
      launcher_restore_reason_checksum_mismatch: 'बैकअप संग्रह चेकसम सत्यापन में विफल रहा।',
      launcher_restore_reason_encryption_key_mismatch: 'इस सर्वर की एन्क्रिप्शन कुंजी से बैकअप संग्रह को डिक्रिप्ट नहीं किया जा सकता।',
      launcher_restore_reason_manifest_parse_failed: 'बैकअप मैनिफ़ेस्ट अमान्य है।',
      launcher_restore_reason_payload_tar_parse_failed: 'बैकअप पेलोड अमान्य है।',
      launcher_restore_reason_archive_extracted_size_exceeded: 'बैकअप का आकार कॉन्फ़िगर की गई पुनर्स्थापना सीमा से अधिक है।',
      launcher_restore_reason_insufficient_disk_space: 'इस बैकअप को सुरक्षित रूप से पुनर्स्थापित करने के लिए डिस्क में पर्याप्त खाली स्थान नहीं है।',
      launcher_restore_reason_source_access_failed: 'बैकअप स्रोत तक पहुँचा नहीं जा सका।',
      launcher_restore_recovery_unconfirmed: 'पुनर्स्थापना विफल हुई और सुरक्षित पुनर्प्राप्ति की पुष्टि नहीं हो सकी। सर्वर डेटा की सुरक्षा के लिए Omlorix को बंद रखा गया है। पुनः शुरू करने से पहले पुनर्स्थापना लॉग देखें। मूल त्रुटि: {error}',
      launcher_restore_startup_failed_after_restore: 'सर्वर डेटा पुनर्स्थापित हो गया, लेकिन Omlorix शुरू नहीं हो सका। पुनर्स्थापित डेटा वापस नहीं बदला गया। स्टार्टअप त्रुटि: {error}',
      launcher_possible_database_downgrade: 'इस सर्वर पर पहले {highestVersion} चलने के बाद Omlorix {currentVersion} शुरू नहीं हो सका। नए संस्करण ने ऐसे डेटाबेस माइग्रेशन लागू किए हो सकते हैं जिन्हें पुराना संस्करण वापस नहीं कर सकता या पढ़ नहीं सकता। अपने डेटा की सुरक्षा के लिए Omlorix {highestVersion} या नया संस्करण चलाएँ, या {currentVersion} के अनुकूल डेटाबेस बैकअप पुनर्स्थापित करें। मूल स्टार्टअप त्रुटि: {error}',
      launcher_server_update_label: 'सर्वर अपडेट',
      launcher_server_update_available_title: 'Omlorix {latestVersion} उपलब्ध है',
      launcher_server_update_description: 'वर्तमान: {currentVersion} · चैनल: {channel}',
      launcher_server_update_action: '{latestVersion} पर अपडेट करें',
      launcher_launcher_update_label: 'लॉन्चर अपडेट',
      launcher_launcher_update_available_title: 'Server Launcher {latestVersion} उपलब्ध है',
      launcher_launcher_update_description: 'वर्तमान: {currentVersion} · चैनल: {channel}',
      launcher_launcher_update_action: 'लॉन्चर अपडेट करें',
      launcher_server_update_launcher_check_action: 'फिर जाँचें',
      launcher_server_update_launcher_ready_description: 'Server Launcher {latestLauncherVersion} उपलब्ध है और आवश्यक न्यूनतम संस्करण {minimumLauncherVersion} को पूरा करता है। पहले लॉन्चर अपडेट करें।',
      launcher_server_update_launcher_feed_behind_description: 'लॉन्चर चैनल अभी {latestLauncherVersion} प्रदान करता है, लेकिन इस Omlorix रिलीज़ को {minimumLauncherVersion} या नया संस्करण चाहिए। संगत लॉन्चर प्रकाशित होने के बाद फिर जाँचें।',
      launcher_server_update_launcher_required_title: 'Omlorix {latestVersion} के लिए लॉन्चर अपडेट आवश्यक है',
      launcher_server_update_launcher_required_description: 'यह सर्वर रिलीज़ इंस्टॉल करने से पहले Omlorix Server Launcher को {minimumLauncherVersion} या नए संस्करण पर अपडेट करें।',
      launcher_server_update_launcher_action: 'लॉन्चर अपडेट करें',
      launcher_server_update_requires_running: 'इस अपडेट को इंस्टॉल करने से पहले Omlorix शुरू करें और सेटअप चेतावनियाँ हल करें।',
      launcher_server_update_channel_stable: 'स्थिर',
      launcher_server_update_channel_beta: 'बीटा',
      launcher_visitor_ips_heading: 'विज़िटर IP',
      launcher_visitor_ip_title_proxy_stopped: 'प्रॉक्सी रुका हुआ है',
      launcher_visitor_ip_message_proxy_stopped: 'लॉन्चर प्रॉक्सी सक्षम है, लेकिन रुका हुआ है। इसे शुरू करें या स्वचालित स्टार्ट चालू करें, ताकि विज़िटर IP प्रॉक्सी के माध्यम से Omlorix तक पहुँचें।',
      launcher_visitor_ip_title_proxy_running: 'प्रॉक्सी सत्यापित',
      launcher_visitor_ip_message_proxy_running: 'हाल के एंड-टू-एंड अनुरोध ने पूरे प्रॉक्सी पथ से विज़िटर IP और सार्वजनिक स्कीम सत्यापित की।',
      launcher_visitor_ip_repair_external_applied: 'विज़िटर IP ट्रस्ट लागू कर दिया गया है। किसी बाहरी क्लाइंट से बाहरी प्रॉक्सी को सत्यापित करें।',
      launcher_visitor_ip_repair_failed: 'विज़िटर IP सेटिंग लागू और सत्यापित नहीं की जा सकीं। पिछला कॉन्फ़िगरेशन पुनः स्थापित कर दिया गया।',
      launcher_visitor_ip_title_repair_failed: 'स्वचालित सुधार विफल रहा',
      launcher_visitor_ip_message_repair_failed: '{error} सुनिश्चित करें कि Omlorix चल रहा है और तैयार है, फिर दोबारा कोशिश करें। विवरण के लिए कंसोल देखें।',
      launcher_visitor_ip_title_verification_failed: 'सत्यापन विफल',
      launcher_visitor_ip_message_verification_failed: 'प्रॉक्सी चल रहा है, लेकिन Omlorix पूरे अनुरोध पथ से विज़िटर IP और सार्वजनिक स्कीम सत्यापित नहीं कर सका।',
      launcher_visitor_ip_direct_probe: ' सीधे Docker परीक्षण में {ip} दिखाई देता है।',
      launcher_visitor_ip_action_open_proxy: 'प्रॉक्सी सेटिंग खोलें',
      launcher_visitor_ip_action_start_proxy: 'प्रॉक्सी शुरू करें',
      launcher_visitor_ip_action_reapply: 'सेटिंग फिर लागू करें',
      launcher_visitor_ip_action_fix: 'अपने आप ठीक करें',
      launcher_visitor_ip_title_restart_required: 'Omlorix को फिर से शुरू करना आवश्यक है',
      launcher_visitor_ip_message_restart_required: 'प्रॉक्सी सेटिंग सहेजी गई हैं, लेकिन चल रहा Omlorix कंटेनर अभी भी पिछली विज़िटर IP सेटिंग का उपयोग कर रहा है। इन्हें लागू करने के लिए Omlorix को फिर से शुरू करें।',
      launcher_visitor_ip_action_restart_omlorix: 'Omlorix को फिर से शुरू करें',
      launcher_proxy_action_starting: 'प्रॉक्सी शुरू हो रहा है',
      launcher_proxy_action_started: 'प्रॉक्सी शुरू हो गया।',
      launcher_proxy_action_start_failed: 'प्रॉक्सी शुरू नहीं हो सका: {error}',
      launcher_proxy_background_service_installed: 'पृष्ठभूमि सेवा इंस्टॉल है',
      launcher_proxy_background_service_not_installed: 'पृष्ठभूमि सेवा इंस्टॉल नहीं है',
      launcher_proxy_background_service_unavailable: 'इस बिल्ड में पृष्ठभूमि सेवा उपलब्ध नहीं है',
      launcher_proxy_install_background_service: 'पृष्ठभूमि सेवा इंस्टॉल करें',
      launcher_proxy_remove_background_service: 'पृष्ठभूमि सेवा हटाएँ',
      launcher_proxy_installing_background_service: 'पृष्ठभूमि प्रॉक्सी सेवा इंस्टॉल हो रही है',
      launcher_proxy_removing_background_service: 'पृष्ठभूमि प्रॉक्सी सेवा हटाई जा रही है',
      launcher_services_subtitle: 'अपेक्षित सेवाएँ और उनके कंटेनर की मौजूदा स्थिति।',
      launcher_services_auto_refresh: 'हर 10 सेकंड में अपडेट होता है',
      launcher_services_auto_refresh_active: 'कार्रवाई के दौरान हर 2 सेकंड में अपडेट होता है',
      launcher_services_running_count: '{running}/{total} चल रही हैं',
      launcher_service_not_created: 'बनाई नहीं गई',
      launcher_service_not_running: 'नहीं चल रही',
      launcher_services_empty: 'कोई सेवा कॉन्फ़िगर नहीं है।',
      launcher_stack_all_running_detail: 'सभी अपेक्षित Omlorix सेवाएँ चल रही हैं।',
      launcher_stack_partial_running_detail: '{count} अपेक्षित Omlorix सेवाएँ नहीं चल रही हैं।',
      launcher_stack_none_running_detail: 'कोई भी अपेक्षित Omlorix सेवा नहीं चल रही है।',
      launcher_stack_health_issues_detail: '{count} अपेक्षित Omlorix सेवाएँ अभी स्वस्थ नहीं हैं।',
    },
    it: {
      launcher_env_status_unsaved_changes: 'Modifiche non salvate',
      launcher_env_empty_filter: 'Nessuna variabile corrisponde al filtro corrente.',
      launcher_env_section_general: 'Generale',
      launcher_env_status_reloading: 'Ricaricamento',
      launcher_env_status_ready: 'Pronto',
      launcher_env_status_error: 'Errore',
      launcher_env_editor_failed: 'Editor .env non riuscito: {error}',
      launcher_env_status_saving: 'Salvataggio',
      launcher_env_status_saving_changes: 'Salvataggio modifiche',
      launcher_env_status_fix_errors: 'Correggi gli errori',
      launcher_env_status_saved_with_backup: 'Salvato con backup',
      launcher_env_status_no_changes: 'Nessuna modifica',
      launcher_env_saved_backup_restart: '.env salvato. Backup: {backupFile}\nRiavvia Omlorix per applicare tutte le modifiche.',
      launcher_env_save_failed: 'Salvataggio .env non riuscito: {error}',
      launcher_restart_recreating_containers: 'Ricreazione dei container dell’applicazione',
      launcher_operation_ready_at: 'Omlorix è pronto su {url}',
      launcher_restart_finished: 'Omlorix riavviato.',
      launcher_start_finished: 'Omlorix avviato.',
      launcher_backup_group_label: 'Backup e ripristino',
      launcher_auto_update_backup_reference_enabled: 'Usa la destinazione e la crittografia dell’archivio configurate nella Dashboard.',
      launcher_auto_update_backup_reference_disabled: 'Le impostazioni di backup nella Dashboard restano configurate mentre questa opzione è disattivata.',
      launcher_auto_update_backup_reference_action: 'Controlla impostazioni backup',
      launcher_backup_provider_local: 'Locale',
      launcher_backup_destination_local: 'Archiviazione locale (disco del server)',
      launcher_backup_unavailable_title: 'I backup del server richiedono Omlorix in esecuzione',
      launcher_backup_unavailable_desc: 'Avvia Omlorix e attendi che sia pronto per caricare le destinazioni.',
      launcher_backup_loading_title: 'Caricamento delle destinazioni…',
      launcher_backup_loading_desc: 'Lettura delle destinazioni e delle impostazioni di crittografia configurate in Omlorix Admin.',
      launcher_backup_load_failed_title: 'Impossibile caricare le impostazioni di backup',
      launcher_backup_load_failed_desc: 'Verifica che Omlorix sia pronto, quindi ricarica le destinazioni.',
      launcher_backup_retry_action: 'Riprova',
      launcher_backup_create_desc: 'Crea un backup completo del server con le destinazioni configurate in Omlorix Admin.',
      launcher_backup_destination_label: 'Destinazione',
      launcher_backup_encryption_title: 'Crittografia archivio',
      launcher_backup_encryption_desc: 'Crittografa l’archivio con la passphrase di backup configurata per questo server.',
      launcher_backup_setup_title: 'Configura prima la crittografia',
      launcher_backup_setup_desc: 'Imposta una passphrase nelle impostazioni del launcher, riavvia Omlorix e riprova.',
      launcher_backup_plaintext_only_desc: 'La crittografia non è disponibile; il server consente esplicitamente archivi non crittografati.',
      launcher_backup_create_action: 'Crea backup del server',
      launcher_backup_creating_action: 'Creazione del backup…',
      launcher_backup_finished: 'Backup completato.',
      launcher_backup_encrypted: 'Crittografato',
      launcher_backup_plaintext: 'Non crittografato',
      launcher_backup_result_title: 'Backup creato correttamente',
      launcher_backup_result_job: 'Processo di backup: {jobId}',
      launcher_backup_failed_generic: 'Impossibile creare il backup. Consulta il registro del launcher per i dettagli.',
      launcher_restore_action: 'Ripristina backup',
      launcher_restore_picker_title: 'Scegli backup Omlorix',
      launcher_restore_picker_button: 'Scegli backup',
      launcher_restore_filter: 'Archivi di backup Omlorix',
      launcher_restore_all_files: 'Tutti i file',
      launcher_restore_confirm_title: 'Ripristinare questo server?',
      launcher_restore_confirm_message: 'Omlorix verrà arrestato, verificherà {file}, creerà un backup di sicurezza e sostituirà database e file prima di riavviarsi. I dati attuali saranno sovrascritti.',
      launcher_restore_confirm_action: 'Ripristina server',
      launcher_restore_running: 'Ripristino del server',
      launcher_restore_requires_running: 'Omlorix deve essere in esecuzione prima di avviare un ripristino sicuro.',
      launcher_restore_stopping_services: 'Arresto dei servizi prima del ripristino',
      launcher_update_stopping_services: 'Arresto dei servizi applicativi prima della migrazione del database',
      launcher_update_rollback_left_offline: 'L’aggiornamento non è riuscito dopo il possibile avvio delle migrazioni del database. La versione di destinazione {targetVersion} rimane selezionata e Omlorix non avvierà una versione precedente. Verifica che i servizi siano arrestati e controlla i registri prima di riprovare o ripristinare un backup compatibile.',
      launcher_update_pre_migration_rollback_left_offline: 'L’aggiornamento non è riuscito prima dell’avvio delle migrazioni del database e non è stato possibile riavviare in sicurezza la versione precedente {previousVersion}. Omlorix è rimasto offline. Verifica che i servizi siano arrestati e controlla i registri prima di avviarlo.',
      launcher_migration_resetting: 'Reimpostazione del container di migrazione',
      launcher_migration_running: 'Esecuzione delle migrazioni del database',
      launcher_migration_recreating_services: 'Ricreazione dei servizi applicativi',
      launcher_restore_restoring_data: 'Verifica del backup e ripristino dei dati',
      launcher_restore_starting_services: 'Avvio di Omlorix dopo il ripristino',
      launcher_restore_ready_at: 'Omlorix è stato ripristinato ed è pronto su {url}',
      launcher_restore_finished: 'Ripristino di Omlorix completato.',
      launcher_restore_restarting_after_failure: 'Il ripristino si è interrotto in modo sicuro. Riavvio di Omlorix con i dati del server esistenti o recuperati.',
      launcher_restore_stopped_safely: 'Il ripristino si è interrotto senza lasciare dati del server modificati e Omlorix è tornato completamente operativo. Motivo: {error}',
      launcher_restore_restart_failed: 'Il ripristino si è interrotto in modo sicuro, ma Omlorix non è tornato completamente operativo. Motivo: {error}. Errore di riavvio: {restartError}',
      launcher_restore_reason_target_not_empty: 'La destinazione di ripristino non è vuota.',
      launcher_restore_reason_missing_required_files: 'L’archivio di backup è incompleto.',
      launcher_restore_reason_checksum_mismatch: 'L’archivio di backup non ha superato la verifica del checksum.',
      launcher_restore_reason_encryption_key_mismatch: 'L’archivio di backup non può essere decrittato con la chiave di cifratura di questo server.',
      launcher_restore_reason_manifest_parse_failed: 'Il manifesto di backup non è valido.',
      launcher_restore_reason_payload_tar_parse_failed: 'Un payload di backup non è valido.',
      launcher_restore_reason_archive_extracted_size_exceeded: 'Il backup supera il limite di dimensione configurato per il ripristino.',
      launcher_restore_reason_insufficient_disk_space: 'Lo spazio libero su disco non è sufficiente per ripristinare in sicurezza questo backup.',
      launcher_restore_reason_source_access_failed: 'Non è stato possibile accedere all’origine del backup.',
      launcher_restore_recovery_unconfirmed: 'Il ripristino non è riuscito e non è stato possibile confermare un recupero sicuro. Omlorix è stato lasciato arrestato per proteggere i dati del server. Controlla i log di ripristino prima del riavvio. Errore originale: {error}',
      launcher_restore_startup_failed_after_restore: 'I dati del server sono stati ripristinati, ma Omlorix non è stato avviato. I dati ripristinati non sono stati annullati. Errore di avvio: {error}',
      launcher_possible_database_downgrade: 'Omlorix {currentVersion} non è riuscito ad avviarsi dopo che questo server aveva utilizzato la versione {highestVersion}. La versione più recente potrebbe aver applicato migrazioni del database che una versione precedente non può annullare o leggere. Per proteggere i dati, usa Omlorix {highestVersion} o una versione successiva, oppure ripristina un backup del database compatibile con {currentVersion}. Errore di avvio originale: {error}',
      launcher_server_update_label: 'Aggiornamento server',
      launcher_server_update_available_title: 'Omlorix {latestVersion} è disponibile',
      launcher_server_update_description: 'Attuale: {currentVersion} · Canale: {channel}',
      launcher_server_update_action: 'Aggiorna a {latestVersion}',
      launcher_launcher_update_label: 'Aggiornamento launcher',
      launcher_launcher_update_available_title: 'Server Launcher {latestVersion} è disponibile',
      launcher_launcher_update_description: 'Attuale: {currentVersion} · Canale: {channel}',
      launcher_launcher_update_action: 'Aggiorna launcher',
      launcher_server_update_launcher_check_action: 'Controlla di nuovo',
      launcher_server_update_launcher_ready_description: 'Server Launcher {latestLauncherVersion} è disponibile e soddisfa la versione minima richiesta {minimumLauncherVersion}. Aggiorna prima il launcher.',
      launcher_server_update_launcher_feed_behind_description: 'Il canale del launcher offre attualmente {latestLauncherVersion}, ma questa versione di Omlorix richiede {minimumLauncherVersion} o successiva. Controlla di nuovo dopo la pubblicazione di un launcher compatibile.',
      launcher_server_update_launcher_required_title: 'Omlorix {latestVersion} richiede un aggiornamento del launcher',
      launcher_server_update_launcher_required_description: 'Aggiorna Omlorix Server Launcher alla versione {minimumLauncherVersion} o successiva prima di installare questa versione del server.',
      launcher_server_update_launcher_action: 'Aggiorna launcher',
      launcher_server_update_requires_running: 'Avvia Omlorix e risolvi gli avvisi di configurazione prima di installare questo aggiornamento.',
      launcher_server_update_channel_stable: 'Stabile',
      launcher_server_update_channel_beta: 'Beta',
      launcher_visitor_ips_heading: 'IP dei visitatori',
      launcher_visitor_ip_title_proxy_stopped: 'Proxy arrestato',
      launcher_visitor_ip_message_proxy_stopped: 'Il proxy del launcher è abilitato ma arrestato. Avvialo o attiva l’avvio automatico affinché gli IP dei visitatori raggiungano Omlorix tramite il proxy.',
      launcher_visitor_ip_title_proxy_running: 'Proxy verificato',
      launcher_visitor_ip_message_proxy_running: 'Una recente richiesta end-to-end ha verificato l’IP del visitatore e lo schema pubblico lungo l’intero percorso proxy.',
      launcher_visitor_ip_repair_external_applied: 'L’attendibilità degli IP dei visitatori è stata applicata. Verifica il proxy esterno da un client esterno.',
      launcher_visitor_ip_repair_failed: 'Non è stato possibile applicare e verificare le impostazioni degli IP dei visitatori. La configurazione precedente è stata ripristinata.',
      launcher_visitor_ip_title_repair_failed: 'Correzione automatica non riuscita',
      launcher_visitor_ip_message_repair_failed: '{error} Assicurati che Omlorix sia in esecuzione e pronto, quindi riprova. Controlla la console per i dettagli.',
      launcher_visitor_ip_title_verification_failed: 'Verifica non riuscita',
      launcher_visitor_ip_message_verification_failed: 'Il proxy è in esecuzione, ma Omlorix non ha potuto verificare l’IP del visitatore e lo schema pubblico lungo l’intero percorso della richiesta.',
      launcher_visitor_ip_direct_probe: ' Il controllo Docker diretto rileva {ip}.',
      launcher_visitor_ip_action_open_proxy: 'Apri impostazioni proxy',
      launcher_visitor_ip_action_start_proxy: 'Avvia proxy',
      launcher_visitor_ip_action_reapply: 'Riapplica impostazioni',
      launcher_visitor_ip_action_fix: 'Correggi automaticamente',
      launcher_visitor_ip_title_restart_required: 'Riavvio di Omlorix necessario',
      launcher_visitor_ip_message_restart_required: 'Le impostazioni del proxy sono salvate, ma il container Omlorix in esecuzione usa ancora la precedente configurazione degli IP dei visitatori. Riavvia Omlorix per applicarle.',
      launcher_visitor_ip_action_restart_omlorix: 'Riavvia Omlorix',
      launcher_proxy_action_starting: 'Avvio del proxy',
      launcher_proxy_action_started: 'Proxy avviato.',
      launcher_proxy_action_start_failed: 'Avvio del proxy non riuscito: {error}',
      launcher_proxy_background_service_installed: 'Servizio in background installato',
      launcher_proxy_background_service_not_installed: 'Servizio in background non installato',
      launcher_proxy_background_service_unavailable: 'Il servizio in background non è disponibile in questa build',
      launcher_proxy_install_background_service: 'Installa servizio in background',
      launcher_proxy_remove_background_service: 'Rimuovi servizio in background',
      launcher_proxy_installing_background_service: 'Installazione del servizio proxy in background',
      launcher_proxy_removing_background_service: 'Rimozione del servizio proxy in background',
      launcher_services_subtitle: 'Servizi previsti e stato attuale dei relativi container.',
      launcher_services_auto_refresh: 'Aggiornamento ogni 10 secondi',
      launcher_services_auto_refresh_active: 'Aggiornamento ogni 2 secondi durante un’azione',
      launcher_services_running_count: '{running}/{total} in esecuzione',
      launcher_service_not_created: 'Non creato',
      launcher_service_not_running: 'Non in esecuzione',
      launcher_services_empty: 'Nessun servizio configurato.',
      launcher_stack_all_running_detail: 'Tutti i servizi Omlorix previsti sono in esecuzione.',
      launcher_stack_partial_running_detail: '{count} servizi Omlorix previsti non sono in esecuzione.',
      launcher_stack_none_running_detail: 'Nessuno dei servizi Omlorix previsti è in esecuzione.',
      launcher_stack_health_issues_detail: '{count} servizi Omlorix previsti non sono ancora integri.',
    },
    ja: {
      launcher_env_status_unsaved_changes: '未保存の変更',
      launcher_env_empty_filter: '現在のフィルターに一致する変数はありません。',
      launcher_env_section_general: '一般',
      launcher_env_status_reloading: '再読み込み中',
      launcher_env_status_ready: '準備完了',
      launcher_env_status_error: 'エラー',
      launcher_env_editor_failed: '.env エディターに失敗しました: {error}',
      launcher_env_status_saving: '保存中',
      launcher_env_status_saving_changes: '変更を保存中',
      launcher_env_status_fix_errors: 'エラーを修正',
      launcher_env_status_saved_with_backup: 'バックアップ付きで保存済み',
      launcher_env_status_no_changes: '変更なし',
      launcher_env_saved_backup_restart: '.env を保存しました。バックアップ: {backupFile}\nすべての変更を反映するには Omlorix を再起動してください。',
      launcher_env_save_failed: '.env の保存に失敗しました: {error}',
      launcher_restart_recreating_containers: 'アプリケーションコンテナを再作成中',
      launcher_operation_ready_at: 'Omlorix は {url} で準備完了です',
      launcher_restart_finished: 'Omlorix を再起動しました。',
      launcher_start_finished: 'Omlorix を起動しました。',
      launcher_backup_group_label: 'バックアップと復元',
      launcher_auto_update_backup_reference_enabled: 'ダッシュボードで設定したバックアップ先とアーカイブ暗号化を使用します。',
      launcher_auto_update_backup_reference_disabled: 'このオプションがオフでも、バックアップ設定はダッシュボードに保持されます。',
      launcher_auto_update_backup_reference_action: 'バックアップ設定を確認',
      launcher_backup_provider_local: 'ローカル',
      launcher_backup_destination_local: 'ローカルストレージ（サーバーディスク）',
      launcher_backup_unavailable_title: 'サーバーのバックアップには Omlorix の起動が必要です',
      launcher_backup_unavailable_desc: 'Omlorix を起動し、準備完了後にバックアップ先を読み込みます。',
      launcher_backup_loading_title: 'バックアップ先を読み込み中…',
      launcher_backup_loading_desc: 'Omlorix 管理画面で設定された保存先と暗号化設定を読み込んでいます。',
      launcher_backup_load_failed_title: 'バックアップ設定を読み込めませんでした',
      launcher_backup_load_failed_desc: 'Omlorix の準備完了を確認し、保存先を再読み込みしてください。',
      launcher_backup_retry_action: '再試行',
      launcher_backup_create_desc: 'Omlorix 管理画面で設定された保存先にサーバー全体のバックアップを作成します。',
      launcher_backup_destination_label: '保存先',
      launcher_backup_encryption_title: 'アーカイブの暗号化',
      launcher_backup_encryption_desc: 'このサーバーに設定されたパスフレーズで暗号化します。',
      launcher_backup_setup_title: '先にバックアップ暗号化を設定してください',
      launcher_backup_setup_desc: 'ランチャー設定でパスフレーズを設定し、Omlorix を再起動してから再試行してください。',
      launcher_backup_plaintext_only_desc: '暗号化は利用できません。このサーバーは非暗号化アーカイブを明示的に許可しています。',
      launcher_backup_create_action: 'サーバーバックアップを作成',
      launcher_backup_creating_action: 'バックアップを作成中…',
      launcher_backup_finished: 'バックアップが完了しました。',
      launcher_backup_encrypted: '暗号化済み',
      launcher_backup_plaintext: '非暗号化',
      launcher_backup_result_title: 'バックアップを作成しました',
      launcher_backup_result_job: 'バックアップジョブ: {jobId}',
      launcher_backup_failed_generic: 'バックアップを作成できませんでした。詳細はランチャーのログを確認してください。',
      launcher_restore_action: 'バックアップを復元',
      launcher_restore_picker_title: 'Omlorix バックアップを選択',
      launcher_restore_picker_button: 'バックアップを選択',
      launcher_restore_filter: 'Omlorix バックアップアーカイブ',
      launcher_restore_all_files: 'すべてのファイル',
      launcher_restore_confirm_title: 'このサーバーを復元しますか？',
      launcher_restore_confirm_message: 'Omlorix を停止し、{file} を検証して安全バックアップを作成した後、データベースとファイルを置き換えて再起動します。現在のデータは上書きされます。',
      launcher_restore_confirm_action: 'サーバーを復元',
      launcher_restore_running: 'サーバーを復元中',
      launcher_restore_requires_running: '安全な復元を開始する前に Omlorix を起動してください。',
      launcher_restore_stopping_services: '復元前にアプリケーションサービスを停止中',
      launcher_update_stopping_services: 'データベース移行前にアプリケーションサービスを停止中',
      launcher_update_rollback_left_offline: 'データベース移行が開始された可能性がある段階で更新に失敗しました。対象バージョン {targetVersion} は選択されたままで、Omlorix が古いバージョンを起動することはありません。再試行または互換性のあるバックアップの復元前に、サービスが停止していることとログを確認してください。',
      launcher_update_pre_migration_rollback_left_offline: 'データベース移行の開始前に更新が失敗し、以前のバージョン {previousVersion} を安全に再起動できませんでした。Omlorix はオフラインのままです。起動前にサービスが停止していることとログを確認してください。',
      launcher_migration_resetting: '移行コンテナをリセット中',
      launcher_migration_running: 'データベース移行を実行中',
      launcher_migration_recreating_services: 'アプリケーションサービスを再作成中',
      launcher_restore_restoring_data: 'バックアップを検証しサーバーデータを復元中',
      launcher_restore_starting_services: '復元後に Omlorix を起動中',
      launcher_restore_ready_at: 'Omlorix の復元が完了し、{url} で利用できます',
      launcher_restore_finished: 'Omlorix の復元が完了しました。',
      launcher_restore_restarting_after_failure: '復元は安全に停止しました。既存または復旧済みのサーバーデータで Omlorix を再起動しています。',
      launcher_restore_stopped_safely: '復元はサーバーデータを変更したままにせず停止し、Omlorix は完全に正常な状態に戻りました。理由: {error}',
      launcher_restore_restart_failed: '復元は安全に停止しましたが、Omlorix は完全に正常な状態に戻りませんでした。復元理由: {error} 再起動エラー: {restartError}',
      launcher_restore_reason_target_not_empty: '復元先が空ではありません。',
      launcher_restore_reason_missing_required_files: 'バックアップアーカイブが不完全です。',
      launcher_restore_reason_checksum_mismatch: 'バックアップアーカイブのチェックサム検証に失敗しました。',
      launcher_restore_reason_encryption_key_mismatch: 'このサーバーの暗号化キーではバックアップアーカイブを復号できません。',
      launcher_restore_reason_manifest_parse_failed: 'バックアップマニフェストが無効です。',
      launcher_restore_reason_payload_tar_parse_failed: 'バックアップのペイロードが無効です。',
      launcher_restore_reason_archive_extracted_size_exceeded: 'バックアップが設定済みの復元サイズ上限を超えています。',
      launcher_restore_reason_insufficient_disk_space: 'このバックアップを安全に復元するための空きディスク容量が不足しています。',
      launcher_restore_reason_source_access_failed: 'バックアップ元にアクセスできませんでした。',
      launcher_restore_recovery_unconfirmed: '復元に失敗し、安全な復旧を確認できませんでした。サーバーデータを保護するため Omlorix は停止したままです。再起動する前に復元ログを確認してください。元のエラー: {error}',
      launcher_restore_startup_failed_after_restore: 'サーバーデータは復元されましたが、Omlorix を起動できませんでした。復元したデータはロールバックされていません。起動エラー: {error}',
      launcher_possible_database_downgrade: 'このサーバーで以前 {highestVersion} を使用した後、Omlorix {currentVersion} を起動できませんでした。新しいバージョンによって、古いバージョンでは元に戻せない、または読み取れないデータベース移行が適用された可能性があります。データを保護するには Omlorix {highestVersion} 以降を使用するか、{currentVersion} と互換性のあるデータベースバックアップを復元してください。元の起動エラー: {error}',
      launcher_server_update_label: 'サーバーアップデート',
      launcher_server_update_available_title: 'Omlorix {latestVersion} を利用できます',
      launcher_server_update_description: '現在: {currentVersion} · チャンネル: {channel}',
      launcher_server_update_action: '{latestVersion} に更新',
      launcher_launcher_update_label: 'ランチャー更新',
      launcher_launcher_update_available_title: 'Server Launcher {latestVersion} を利用できます',
      launcher_launcher_update_description: '現在: {currentVersion} · チャンネル: {channel}',
      launcher_launcher_update_action: 'ランチャーを更新',
      launcher_server_update_launcher_check_action: '再確認',
      launcher_server_update_launcher_ready_description: 'Server Launcher {latestLauncherVersion} を利用でき、必要な最低バージョン {minimumLauncherVersion} を満たしています。先にランチャーを更新してください。',
      launcher_server_update_launcher_feed_behind_description: 'ランチャーチャンネルで現在利用できるのは {latestLauncherVersion} ですが、この Omlorix リリースには {minimumLauncherVersion} 以降が必要です。互換性のあるランチャーが公開された後に再確認してください。',
      launcher_server_update_launcher_required_title: 'Omlorix {latestVersion} にはランチャーの更新が必要です',
      launcher_server_update_launcher_required_description: 'このサーバーリリースをインストールする前に、Omlorix Server Launcher を {minimumLauncherVersion} 以降へ更新してください。',
      launcher_server_update_launcher_action: 'ランチャーを更新',
      launcher_server_update_requires_running: 'この更新をインストールする前に Omlorix を起動し、セットアップの警告を解消してください。',
      launcher_server_update_channel_stable: '安定版',
      launcher_server_update_channel_beta: 'ベータ',
      launcher_visitor_ips_heading: '訪問者 IP',
      launcher_visitor_ip_title_proxy_stopped: 'プロキシは停止中',
      launcher_visitor_ip_message_proxy_stopped: 'ランチャープロキシは有効ですが停止しています。訪問者 IP がプロキシ経由で Omlorix に届くよう、プロキシを起動するか自動起動を有効にしてください。',
      launcher_visitor_ip_title_proxy_running: 'プロキシを検証済み',
      launcher_visitor_ip_message_proxy_running: '直近のエンドツーエンド要求で、プロキシ経路全体の訪問者 IP と公開スキームを検証しました。',
      launcher_visitor_ip_repair_external_applied: '訪問者 IP の信頼設定を適用しました。外部クライアントから外部プロキシを検証してください。',
      launcher_visitor_ip_repair_failed: '訪問者 IP 設定を適用および検証できませんでした。以前の構成を復元しました。',
      launcher_visitor_ip_title_repair_failed: '自動修正に失敗しました',
      launcher_visitor_ip_message_repair_failed: '{error} Omlorix が実行中で準備完了であることを確認してから、もう一度お試しください。詳細はコンソールで確認できます。',
      launcher_visitor_ip_title_verification_failed: '検証に失敗しました',
      launcher_visitor_ip_message_verification_failed: 'プロキシは実行中ですが、Omlorix は要求経路全体の訪問者 IP と公開スキームを検証できませんでした。',
      launcher_visitor_ip_direct_probe: ' Docker への直接確認では {ip} が検出されています。',
      launcher_visitor_ip_action_open_proxy: 'プロキシ設定を開く',
      launcher_visitor_ip_action_start_proxy: 'プロキシを起動',
      launcher_visitor_ip_action_reapply: '設定を再適用',
      launcher_visitor_ip_action_fix: '自動的に修正',
      launcher_visitor_ip_title_restart_required: 'Omlorix の再起動が必要です',
      launcher_visitor_ip_message_restart_required: 'プロキシ設定は保存されていますが、実行中の Omlorix コンテナは以前の訪問者 IP 設定を使用しています。適用するには Omlorix を再起動してください。',
      launcher_visitor_ip_action_restart_omlorix: 'Omlorix を再起動',
      launcher_proxy_action_starting: 'プロキシを起動中',
      launcher_proxy_action_started: 'プロキシを起動しました。',
      launcher_proxy_action_start_failed: 'プロキシを起動できませんでした: {error}',
      launcher_proxy_background_service_installed: 'バックグラウンドサービスはインストール済みです',
      launcher_proxy_background_service_not_installed: 'バックグラウンドサービスは未インストールです',
      launcher_proxy_background_service_unavailable: 'このビルドではバックグラウンドサービスを利用できません',
      launcher_proxy_install_background_service: 'バックグラウンドサービスをインストール',
      launcher_proxy_remove_background_service: 'バックグラウンドサービスを削除',
      launcher_proxy_installing_background_service: 'バックグラウンドプロキシサービスをインストール中',
      launcher_proxy_removing_background_service: 'バックグラウンドプロキシサービスを削除中',
      launcher_services_subtitle: '想定されるサービスと現在のコンテナ状態。',
      launcher_services_auto_refresh: '10 秒ごとに更新',
      launcher_services_auto_refresh_active: '操作中は 2 秒ごとに更新',
      launcher_services_running_count: '{running}/{total} 実行中',
      launcher_service_not_created: '未作成',
      launcher_service_not_running: '停止中',
      launcher_services_empty: 'サービスが構成されていません。',
      launcher_stack_all_running_detail: '想定される Omlorix サービスはすべて実行中です。',
      launcher_stack_partial_running_detail: '想定される Omlorix サービスのうち {count} 件が実行されていません。',
      launcher_stack_none_running_detail: '想定される Omlorix サービスはどれも実行されていません。',
      launcher_stack_health_issues_detail: '想定される Omlorix サービスのうち {count} 件がまだ正常ではありません。',
    },
    pt: {
      launcher_env_status_unsaved_changes: 'Alterações não salvas',
      launcher_env_empty_filter: 'Nenhuma variável corresponde ao filtro atual.',
      launcher_env_section_general: 'Geral',
      launcher_env_status_reloading: 'Recarregando',
      launcher_env_status_ready: 'Pronto',
      launcher_env_status_error: 'Erro',
      launcher_env_editor_failed: 'Falha no editor .env: {error}',
      launcher_env_status_saving: 'Salvando',
      launcher_env_status_saving_changes: 'Salvando alterações',
      launcher_env_status_fix_errors: 'Corrija os erros',
      launcher_env_status_saved_with_backup: 'Salvo com backup',
      launcher_env_status_no_changes: 'Sem alterações',
      launcher_env_saved_backup_restart: '.env salvo. Backup: {backupFile}\nReinicie o Omlorix para aplicar todas as alterações.',
      launcher_env_save_failed: 'Falha ao salvar .env: {error}',
      launcher_restart_recreating_containers: 'Recriando contêineres da aplicação',
      launcher_operation_ready_at: 'Omlorix está pronto em {url}',
      launcher_restart_finished: 'Omlorix reiniciado.',
      launcher_start_finished: 'Omlorix iniciado.',
      launcher_backup_group_label: 'Backup e recuperação',
      launcher_auto_update_backup_reference_enabled: 'Usa o destino e a criptografia do arquivo configurados no Painel.',
      launcher_auto_update_backup_reference_disabled: 'As configurações de backup permanecem no Painel enquanto esta opção está desativada.',
      launcher_auto_update_backup_reference_action: 'Revisar configurações de backup',
      launcher_backup_provider_local: 'Local',
      launcher_backup_destination_local: 'Armazenamento local (disco do servidor)',
      launcher_backup_unavailable_title: 'Backups do servidor exigem que o Omlorix esteja em execução',
      launcher_backup_unavailable_desc: 'Inicie o Omlorix e aguarde até que esteja pronto para carregar os destinos.',
      launcher_backup_loading_title: 'Carregando destinos de backup…',
      launcher_backup_loading_desc: 'Lendo os destinos e as configurações de criptografia definidos na Administração do Omlorix.',
      launcher_backup_load_failed_title: 'Não foi possível carregar as configurações de backup',
      launcher_backup_load_failed_desc: 'Verifique se o Omlorix está pronto e carregue os destinos novamente.',
      launcher_backup_retry_action: 'Tentar novamente',
      launcher_backup_create_desc: 'Crie um backup completo do servidor com os destinos definidos na Administração do Omlorix.',
      launcher_backup_destination_label: 'Destino',
      launcher_backup_encryption_title: 'Criptografia do arquivo',
      launcher_backup_encryption_desc: 'Criptografe o arquivo com a frase secreta de backup configurada neste servidor.',
      launcher_backup_setup_title: 'Configure primeiro a criptografia',
      launcher_backup_setup_desc: 'Defina uma frase secreta nas configurações do inicializador, reinicie o Omlorix e tente novamente.',
      launcher_backup_plaintext_only_desc: 'A criptografia não está disponível; este servidor permite explicitamente arquivos não criptografados.',
      launcher_backup_create_action: 'Criar backup do servidor',
      launcher_backup_creating_action: 'Criando backup…',
      launcher_backup_finished: 'Backup concluído.',
      launcher_backup_encrypted: 'Criptografado',
      launcher_backup_plaintext: 'Não criptografado',
      launcher_backup_result_title: 'Backup criado com sucesso',
      launcher_backup_result_job: 'Tarefa de backup: {jobId}',
      launcher_backup_failed_generic: 'Não foi possível criar o backup. Consulte o registo do iniciador para obter detalhes.',
      launcher_restore_action: 'Restaurar backup',
      launcher_restore_picker_title: 'Escolher backup do Omlorix',
      launcher_restore_picker_button: 'Escolher backup',
      launcher_restore_filter: 'Arquivos de backup do Omlorix',
      launcher_restore_all_files: 'Todos os arquivos',
      launcher_restore_confirm_title: 'Restaurar este servidor?',
      launcher_restore_confirm_message: 'O Omlorix será interrompido, verificará {file}, criará um backup de segurança e substituirá o banco de dados e os arquivos antes de reiniciar. Os dados atuais serão sobrescritos.',
      launcher_restore_confirm_action: 'Restaurar servidor',
      launcher_restore_running: 'Restaurando servidor',
      launcher_restore_requires_running: 'O Omlorix deve estar em execução antes de iniciar uma restauração segura.',
      launcher_restore_stopping_services: 'Parando serviços antes da restauração',
      launcher_update_stopping_services: 'A parar os serviços da aplicação antes da migração da base de dados',
      launcher_update_rollback_left_offline: 'A atualização falhou depois de as migrações da base de dados poderem ter começado. A versão de destino {targetVersion} permanece selecionada e o Omlorix não iniciará uma versão anterior. Confirme que os serviços estão parados e consulte os registos antes de tentar novamente ou restaurar uma cópia de segurança compatível.',
      launcher_update_pre_migration_rollback_left_offline: 'A atualização falhou antes do início das migrações da base de dados e não foi possível reiniciar com segurança a versão anterior {previousVersion}. O Omlorix ficou offline. Confirme que os serviços estão parados e consulte os registos antes de o iniciar.',
      launcher_migration_resetting: 'A repor o contentor de migração',
      launcher_migration_running: 'A executar as migrações da base de dados',
      launcher_migration_recreating_services: 'A recriar os serviços da aplicação',
      launcher_restore_restoring_data: 'Verificando o backup e restaurando os dados',
      launcher_restore_starting_services: 'Iniciando o Omlorix após a restauração',
      launcher_restore_ready_at: 'O Omlorix foi restaurado e está disponível em {url}',
      launcher_restore_finished: 'Restauração do Omlorix concluída.',
      launcher_restore_restarting_after_failure: 'A restauração foi interrompida com segurança. Reiniciando o Omlorix com os dados existentes ou recuperados do servidor.',
      launcher_restore_stopped_safely: 'A restauração foi interrompida sem deixar dados do servidor alterados, e o Omlorix voltou a ficar totalmente íntegro. Motivo: {error}',
      launcher_restore_restart_failed: 'A restauração foi interrompida com segurança, mas o Omlorix não voltou a ficar totalmente íntegro. Motivo: {error}. Erro ao reiniciar: {restartError}',
      launcher_restore_reason_target_not_empty: 'O destino da restauração não está vazio.',
      launcher_restore_reason_missing_required_files: 'O arquivo de backup está incompleto.',
      launcher_restore_reason_checksum_mismatch: 'O arquivo de backup falhou na verificação da soma de controle.',
      launcher_restore_reason_encryption_key_mismatch: 'O arquivo de backup não pode ser descriptografado com a chave de criptografia deste servidor.',
      launcher_restore_reason_manifest_parse_failed: 'O manifesto de backup é inválido.',
      launcher_restore_reason_payload_tar_parse_failed: 'Uma carga útil de backup é inválida.',
      launcher_restore_reason_archive_extracted_size_exceeded: 'O backup excede o limite de tamanho configurado para restauração.',
      launcher_restore_reason_insufficient_disk_space: 'Não há espaço livre em disco suficiente para restaurar este backup com segurança.',
      launcher_restore_reason_source_access_failed: 'Não foi possível acessar a origem do backup.',
      launcher_restore_recovery_unconfirmed: 'A restauração falhou e não foi possível confirmar uma recuperação segura. O Omlorix foi mantido parado para proteger os dados do servidor. Revise os logs de restauração antes de reiniciar. Erro original: {error}',
      launcher_restore_startup_failed_after_restore: 'Os dados do servidor foram restaurados, mas o Omlorix não pôde ser iniciado. Os dados restaurados não foram revertidos. Erro de inicialização: {error}',
      launcher_possible_database_downgrade: 'O Omlorix {currentVersion} não conseguiu iniciar depois de este servidor ter utilizado a versão {highestVersion}. A versão mais recente pode ter aplicado migrações da base de dados que uma versão anterior não consegue reverter ou ler. Para proteger os dados, utilize o Omlorix {highestVersion} ou posterior, ou restaure uma cópia da base de dados compatível com {currentVersion}. Erro de arranque original: {error}',
      launcher_server_update_label: 'Atualização do servidor',
      launcher_server_update_available_title: 'Omlorix {latestVersion} está disponível',
      launcher_server_update_description: 'Atual: {currentVersion} · Canal: {channel}',
      launcher_server_update_action: 'Atualizar para {latestVersion}',
      launcher_launcher_update_label: 'Atualização do inicializador',
      launcher_launcher_update_available_title: 'O Server Launcher {latestVersion} está disponível',
      launcher_launcher_update_description: 'Atual: {currentVersion} · Canal: {channel}',
      launcher_launcher_update_action: 'Atualizar inicializador',
      launcher_server_update_launcher_check_action: 'Verificar novamente',
      launcher_server_update_launcher_ready_description: 'O Server Launcher {latestLauncherVersion} está disponível e atende à versão mínima exigida {minimumLauncherVersion}. Atualize primeiro o inicializador.',
      launcher_server_update_launcher_feed_behind_description: 'O canal do inicializador oferece atualmente {latestLauncherVersion}, mas esta versão do Omlorix requer {minimumLauncherVersion} ou posterior. Verifique novamente quando um inicializador compatível for publicado.',
      launcher_server_update_launcher_required_title: 'Omlorix {latestVersion} requer uma atualização do iniciador',
      launcher_server_update_launcher_required_description: 'Atualize o Omlorix Server Launcher para a versão {minimumLauncherVersion} ou posterior antes de instalar esta versão do servidor.',
      launcher_server_update_launcher_action: 'Atualizar iniciador',
      launcher_server_update_requires_running: 'Inicie o Omlorix e resolva os avisos de configuração antes de instalar esta atualização.',
      launcher_server_update_channel_stable: 'Estável',
      launcher_server_update_channel_beta: 'Beta',
      launcher_visitor_ips_heading: 'IPs dos visitantes',
      launcher_visitor_ip_title_proxy_stopped: 'Proxy parado',
      launcher_visitor_ip_message_proxy_stopped: 'O proxy do iniciador está ativado, mas parado. Inicie-o ou ative o arranque automático para que os IPs dos visitantes cheguem ao Omlorix através do proxy.',
      launcher_visitor_ip_title_proxy_running: 'Proxy verificado',
      launcher_visitor_ip_message_proxy_running: 'Um pedido ponto a ponto recente verificou o IP do visitante e o esquema público em todo o percurso do proxy.',
      launcher_visitor_ip_repair_external_applied: 'A confiança nos IP dos visitantes foi aplicada. Verifique o proxy externo a partir de um cliente externo.',
      launcher_visitor_ip_repair_failed: 'Não foi possível aplicar e verificar as definições de IP dos visitantes. A configuração anterior foi restaurada.',
      launcher_visitor_ip_title_repair_failed: 'A correção automática falhou',
      launcher_visitor_ip_message_repair_failed: '{error} Certifique-se de que o Omlorix está em execução e pronto e tente novamente. Consulte a consola para obter detalhes.',
      launcher_visitor_ip_title_verification_failed: 'Falha na verificação',
      launcher_visitor_ip_message_verification_failed: 'O proxy está em execução, mas o Omlorix não conseguiu verificar o IP do visitante e o esquema público em todo o percurso do pedido.',
      launcher_visitor_ip_direct_probe: ' A verificação direta do Docker deteta {ip}.',
      launcher_visitor_ip_action_open_proxy: 'Abrir definições do proxy',
      launcher_visitor_ip_action_start_proxy: 'Iniciar proxy',
      launcher_visitor_ip_action_reapply: 'Reaplicar definições',
      launcher_visitor_ip_action_fix: 'Corrigir automaticamente',
      launcher_visitor_ip_title_restart_required: 'É necessário reiniciar o Omlorix',
      launcher_visitor_ip_message_restart_required: 'As definições do proxy foram guardadas, mas o contentor Omlorix em execução ainda utiliza a configuração anterior dos IPs dos visitantes. Reinicie o Omlorix para as aplicar.',
      launcher_visitor_ip_action_restart_omlorix: 'Reiniciar Omlorix',
      launcher_proxy_action_starting: 'A iniciar o proxy',
      launcher_proxy_action_started: 'Proxy iniciado.',
      launcher_proxy_action_start_failed: 'Não foi possível iniciar o proxy: {error}',
      launcher_proxy_background_service_installed: 'Serviço em segundo plano instalado',
      launcher_proxy_background_service_not_installed: 'Serviço em segundo plano não instalado',
      launcher_proxy_background_service_unavailable: 'O serviço em segundo plano não está disponível nesta compilação',
      launcher_proxy_install_background_service: 'Instalar serviço em segundo plano',
      launcher_proxy_remove_background_service: 'Remover serviço em segundo plano',
      launcher_proxy_installing_background_service: 'A instalar o serviço proxy em segundo plano',
      launcher_proxy_removing_background_service: 'A remover o serviço proxy em segundo plano',
      launcher_services_subtitle: 'Serviços esperados e estado atual dos respetivos contentores.',
      launcher_services_auto_refresh: 'Atualiza a cada 10 segundos',
      launcher_services_auto_refresh_active: 'Atualiza a cada 2 segundos durante uma ação',
      launcher_services_running_count: '{running}/{total} em execução',
      launcher_service_not_created: 'Não criado',
      launcher_service_not_running: 'Não está em execução',
      launcher_services_empty: 'Não existem serviços configurados.',
      launcher_stack_all_running_detail: 'Todos os serviços Omlorix esperados estão em execução.',
      launcher_stack_partial_running_detail: '{count} serviços Omlorix esperados não estão em execução.',
      launcher_stack_none_running_detail: 'Nenhum dos serviços Omlorix esperados está em execução.',
      launcher_stack_health_issues_detail: '{count} serviços Omlorix esperados ainda não estão saudáveis.',
    },
    ru: {
      launcher_env_status_unsaved_changes: 'Несохраненные изменения',
      launcher_env_empty_filter: 'Нет переменных, соответствующих текущему фильтру.',
      launcher_env_section_general: 'Общие',
      launcher_env_status_reloading: 'Перезагрузка',
      launcher_env_status_ready: 'Готово',
      launcher_env_status_error: 'Ошибка',
      launcher_env_editor_failed: 'Ошибка редактора .env: {error}',
      launcher_env_status_saving: 'Сохранение',
      launcher_env_status_saving_changes: 'Сохранение изменений',
      launcher_env_status_fix_errors: 'Исправьте ошибки',
      launcher_env_status_saved_with_backup: 'Сохранено с резервной копией',
      launcher_env_status_no_changes: 'Без изменений',
      launcher_env_saved_backup_restart: '.env сохранен. Резервная копия: {backupFile}\nПерезапустите Omlorix, чтобы применить все изменения.',
      launcher_env_save_failed: 'Не удалось сохранить .env: {error}',
      launcher_restart_recreating_containers: 'Повторное создание контейнеров приложения',
      launcher_operation_ready_at: 'Omlorix готов по адресу {url}',
      launcher_restart_finished: 'Omlorix перезапущен.',
      launcher_start_finished: 'Omlorix запущен.',
      launcher_backup_group_label: 'Резервное копирование и восстановление',
      launcher_auto_update_backup_reference_enabled: 'Использует место хранения и шифрование архива, настроенные на панели.',
      launcher_auto_update_backup_reference_disabled: 'Настройки резервного копирования сохраняются на панели, пока этот параметр отключён.',
      launcher_auto_update_backup_reference_action: 'Проверить настройки копирования',
      launcher_backup_provider_local: 'Локально',
      launcher_backup_destination_local: 'Локальное хранилище (диск сервера)',
      launcher_backup_unavailable_title: 'Для копирования сервера Omlorix должен быть запущен',
      launcher_backup_unavailable_desc: 'Запустите Omlorix и дождитесь готовности для загрузки мест хранения.',
      launcher_backup_loading_title: 'Загрузка мест хранения…',
      launcher_backup_loading_desc: 'Чтение мест хранения и настроек шифрования из панели Omlorix.',
      launcher_backup_load_failed_title: 'Не удалось загрузить настройки',
      launcher_backup_load_failed_desc: 'Убедитесь, что Omlorix готов, и повторите загрузку.',
      launcher_backup_retry_action: 'Повторить',
      launcher_backup_create_desc: 'Создайте полную копию сервера в месте, заданном в панели Omlorix.',
      launcher_backup_destination_label: 'Место хранения',
      launcher_backup_encryption_title: 'Шифрование архива',
      launcher_backup_encryption_desc: 'Зашифруйте архив кодовой фразой, заданной для сервера.',
      launcher_backup_setup_title: 'Сначала настройте шифрование',
      launcher_backup_setup_desc: 'Задайте кодовую фразу в настройках, перезапустите Omlorix и повторите.',
      launcher_backup_plaintext_only_desc: 'Шифрование недоступно; сервер явно разрешает незашифрованные архивы.',
      launcher_backup_create_action: 'Создать копию сервера',
      launcher_backup_creating_action: 'Создание копии…',
      launcher_backup_finished: 'Резервное копирование завершено.',
      launcher_backup_encrypted: 'Зашифровано',
      launcher_backup_plaintext: 'Не зашифровано',
      launcher_backup_result_title: 'Резервная копия создана',
      launcher_backup_result_job: 'Задача: {jobId}',
      launcher_backup_failed_generic: 'Не удалось создать резервную копию. Подробности см. в журнале лаунчера.',
      launcher_restore_action: 'Восстановить резервную копию',
      launcher_restore_picker_title: 'Выберите резервную копию Omlorix',
      launcher_restore_picker_button: 'Выбрать копию',
      launcher_restore_filter: 'Архивы резервных копий Omlorix',
      launcher_restore_all_files: 'Все файлы',
      launcher_restore_confirm_title: 'Восстановить этот сервер?',
      launcher_restore_confirm_message: 'Omlorix остановится, проверит {file}, создаст страховочную копию, затем заменит базу данных и файлы и перезапустится. Текущие данные будут перезаписаны.',
      launcher_restore_confirm_action: 'Восстановить сервер',
      launcher_restore_running: 'Восстановление сервера',
      launcher_restore_requires_running: 'Перед безопасным восстановлением необходимо запустить Omlorix.',
      launcher_restore_stopping_services: 'Остановка служб перед восстановлением',
      launcher_update_stopping_services: 'Остановка служб приложения перед миграцией базы данных',
      launcher_update_rollback_left_offline: 'Обновление завершилось ошибкой после возможного начала миграций базы данных. Целевая версия {targetVersion} остается выбранной, и Omlorix не запустит более старую версию. Убедитесь, что службы остановлены, и проверьте журналы перед повторной попыткой или восстановлением совместимой резервной копии.',
      launcher_update_pre_migration_rollback_left_offline: 'Обновление завершилось ошибкой до начала миграций базы данных, и предыдущую версию {previousVersion} не удалось безопасно перезапустить. Omlorix оставлен в автономном режиме. Убедитесь, что службы остановлены, и проверьте журналы перед запуском.',
      launcher_migration_resetting: 'Сброс контейнера миграции',
      launcher_migration_running: 'Выполнение миграций базы данных',
      launcher_migration_recreating_services: 'Пересоздание служб приложения',
      launcher_restore_restoring_data: 'Проверка копии и восстановление данных сервера',
      launcher_restore_starting_services: 'Запуск Omlorix после восстановления',
      launcher_restore_ready_at: 'Omlorix восстановлен и доступен по адресу {url}',
      launcher_restore_finished: 'Восстановление Omlorix завершено.',
      launcher_restore_restarting_after_failure: 'Восстановление безопасно остановлено. Omlorix перезапускается с существующими или восстановленными данными сервера.',
      launcher_restore_stopped_safely: 'Восстановление остановлено без сохранения изменённых данных сервера, и Omlorix снова полностью работоспособен. Причина: {error}',
      launcher_restore_restart_failed: 'Восстановление безопасно остановлено, но Omlorix не вернулся в полностью работоспособное состояние. Причина восстановления: {error}. Ошибка перезапуска: {restartError}',
      launcher_restore_reason_target_not_empty: 'Цель восстановления не пуста.',
      launcher_restore_reason_missing_required_files: 'Архив резервной копии неполон.',
      launcher_restore_reason_checksum_mismatch: 'Архив резервной копии не прошёл проверку контрольной суммы.',
      launcher_restore_reason_encryption_key_mismatch: 'Архив резервной копии невозможно расшифровать ключом шифрования этого сервера.',
      launcher_restore_reason_manifest_parse_failed: 'Манифест резервной копии недействителен.',
      launcher_restore_reason_payload_tar_parse_failed: 'Один из файлов данных резервной копии недействителен.',
      launcher_restore_reason_archive_extracted_size_exceeded: 'Размер резервной копии превышает настроенное ограничение для восстановления.',
      launcher_restore_reason_insufficient_disk_space: 'Недостаточно свободного места на диске для безопасного восстановления этой резервной копии.',
      launcher_restore_reason_source_access_failed: 'Не удалось получить доступ к источнику резервной копии.',
      launcher_restore_recovery_unconfirmed: 'Восстановление завершилось ошибкой, и безопасное восстановление данных не подтверждено. Omlorix оставлен остановленным для защиты данных сервера. Перед перезапуском проверьте журналы восстановления. Исходная ошибка: {error}',
      launcher_restore_startup_failed_after_restore: 'Данные сервера восстановлены, но Omlorix не удалось запустить. Восстановленные данные не были отменены. Ошибка запуска: {error}',
      launcher_possible_database_downgrade: 'Не удалось запустить Omlorix {currentVersion} после того, как на этом сервере уже использовалась версия {highestVersion}. Более новая версия могла применить миграции базы данных, которые старая версия не может отменить или прочитать. Чтобы защитить данные, используйте Omlorix {highestVersion} или новее либо восстановите резервную копию базы данных, совместимую с {currentVersion}. Исходная ошибка запуска: {error}',
      launcher_server_update_label: 'Обновление сервера',
      launcher_server_update_available_title: 'Доступен Omlorix {latestVersion}',
      launcher_server_update_description: 'Текущая версия: {currentVersion} · Канал: {channel}',
      launcher_server_update_action: 'Обновить до {latestVersion}',
      launcher_launcher_update_label: 'Обновление лаунчера',
      launcher_launcher_update_available_title: 'Доступен Server Launcher {latestVersion}',
      launcher_launcher_update_description: 'Текущая: {currentVersion} · Канал: {channel}',
      launcher_launcher_update_action: 'Обновить лаунчер',
      launcher_server_update_launcher_check_action: 'Проверить снова',
      launcher_server_update_launcher_ready_description: 'Доступен Server Launcher {latestLauncherVersion}, соответствующий минимальной требуемой версии {minimumLauncherVersion}. Сначала обновите лаунчер.',
      launcher_server_update_launcher_feed_behind_description: 'Сейчас канал лаунчера предлагает {latestLauncherVersion}, но этому выпуску Omlorix требуется {minimumLauncherVersion} или новее. Проверьте снова после публикации совместимого лаунчера.',
      launcher_server_update_launcher_required_title: 'Для Omlorix {latestVersion} требуется обновление лаунчера',
      launcher_server_update_launcher_required_description: 'Перед установкой этого выпуска сервера обновите Omlorix Server Launcher до версии {minimumLauncherVersion} или новее.',
      launcher_server_update_launcher_action: 'Обновить лаунчер',
      launcher_server_update_requires_running: 'Запустите Omlorix и устраните предупреждения настройки перед установкой этого обновления.',
      launcher_server_update_channel_stable: 'Стабильный',
      launcher_server_update_channel_beta: 'Бета',
      launcher_visitor_ips_heading: 'IP-адреса посетителей',
      launcher_visitor_ip_title_proxy_stopped: 'Прокси остановлен',
      launcher_visitor_ip_message_proxy_stopped: 'Прокси программы запуска включён, но остановлен. Запустите его или включите автоматический запуск, чтобы IP-адреса посетителей поступали в Omlorix через прокси.',
      launcher_visitor_ip_title_proxy_running: 'Прокси проверен',
      launcher_visitor_ip_message_proxy_running: 'Недавний сквозной запрос подтвердил IP-адрес посетителя и публичную схему по всему пути прокси.',
      launcher_visitor_ip_repair_external_applied: 'Доверие к IP-адресам посетителей настроено. Проверьте внешний прокси с внешнего клиента.',
      launcher_visitor_ip_repair_failed: 'Не удалось применить и проверить настройки IP-адресов посетителей. Прежняя конфигурация восстановлена.',
      launcher_visitor_ip_title_repair_failed: 'Автоматическое исправление не выполнено',
      launcher_visitor_ip_message_repair_failed: '{error} Убедитесь, что Omlorix запущен и готов, затем повторите попытку. Подробности см. в консоли.',
      launcher_visitor_ip_title_verification_failed: 'Проверка не удалась',
      launcher_visitor_ip_message_verification_failed: 'Прокси работает, но Omlorix не удалось проверить IP-адрес посетителя и публичную схему по всему пути запроса.',
      launcher_visitor_ip_direct_probe: ' Прямая проверка Docker видит {ip}.',
      launcher_visitor_ip_action_open_proxy: 'Открыть настройки прокси',
      launcher_visitor_ip_action_start_proxy: 'Запустить прокси',
      launcher_visitor_ip_action_reapply: 'Применить настройки повторно',
      launcher_visitor_ip_action_fix: 'Исправить автоматически',
      launcher_visitor_ip_title_restart_required: 'Требуется перезапуск Omlorix',
      launcher_visitor_ip_message_restart_required: 'Настройки прокси сохранены, но работающий контейнер Omlorix всё ещё использует прежнюю конфигурацию IP-адресов посетителей. Перезапустите Omlorix, чтобы применить их.',
      launcher_visitor_ip_action_restart_omlorix: 'Перезапустить Omlorix',
      launcher_proxy_action_starting: 'Запуск прокси',
      launcher_proxy_action_started: 'Прокси запущен.',
      launcher_proxy_action_start_failed: 'Не удалось запустить прокси: {error}',
      launcher_proxy_background_service_installed: 'Фоновая служба установлена',
      launcher_proxy_background_service_not_installed: 'Фоновая служба не установлена',
      launcher_proxy_background_service_unavailable: 'Фоновая служба недоступна в этой сборке',
      launcher_proxy_install_background_service: 'Установить фоновую службу',
      launcher_proxy_remove_background_service: 'Удалить фоновую службу',
      launcher_proxy_installing_background_service: 'Установка фоновой службы прокси',
      launcher_proxy_removing_background_service: 'Удаление фоновой службы прокси',
      launcher_services_subtitle: 'Ожидаемые сервисы и текущее состояние их контейнеров.',
      launcher_services_auto_refresh: 'Обновление каждые 10 секунд',
      launcher_services_auto_refresh_active: 'Во время операции обновляется каждые 2 секунды',
      launcher_services_running_count: 'Работает {running}/{total}',
      launcher_service_not_created: 'Не создан',
      launcher_service_not_running: 'Не работает',
      launcher_services_empty: 'Сервисы не настроены.',
      launcher_stack_all_running_detail: 'Все ожидаемые сервисы Omlorix работают.',
      launcher_stack_partial_running_detail: 'Не работает ожидаемых сервисов Omlorix: {count}.',
      launcher_stack_none_running_detail: 'Ни один из ожидаемых сервисов Omlorix не работает.',
      launcher_stack_health_issues_detail: 'У {count} ожидаемых сервисов Omlorix ещё нет статуса «исправен».',
    },
    zh: {
      launcher_env_status_unsaved_changes: '未保存的更改',
      launcher_env_empty_filter: '没有变量匹配当前筛选条件。',
      launcher_env_section_general: '常规',
      launcher_env_status_reloading: '正在重新加载',
      launcher_env_status_ready: '就绪',
      launcher_env_status_error: '错误',
      launcher_env_editor_failed: '.env 编辑器失败：{error}',
      launcher_env_status_saving: '正在保存',
      launcher_env_status_saving_changes: '正在保存更改',
      launcher_env_status_fix_errors: '修复错误',
      launcher_env_status_saved_with_backup: '已保存并创建备份',
      launcher_env_status_no_changes: '无更改',
      launcher_env_saved_backup_restart: '.env 已保存。备份：{backupFile}\n重新启动 Omlorix 以使所有更改生效。',
      launcher_env_save_failed: '.env 保存失败：{error}',
      launcher_restart_recreating_containers: '正在重新创建应用容器',
      launcher_operation_ready_at: 'Omlorix 已在 {url} 就绪',
      launcher_restart_finished: 'Omlorix 已重新启动。',
      launcher_start_finished: 'Omlorix 已启动。',
      launcher_backup_group_label: '备份与恢复',
      launcher_auto_update_backup_reference_enabled: '使用在仪表板中配置的备份目标和归档加密设置。',
      launcher_auto_update_backup_reference_disabled: '关闭此选项时，备份设置仍保留在仪表板中。',
      launcher_auto_update_backup_reference_action: '查看备份设置',
      launcher_backup_provider_local: '本地',
      launcher_backup_destination_local: '本地存储（服务器磁盘）',
      launcher_backup_unavailable_title: '服务器备份需要 Omlorix 正在运行',
      launcher_backup_unavailable_desc: '启动 Omlorix 并等待就绪，然后加载备份目标。',
      launcher_backup_loading_title: '正在加载备份目标…',
      launcher_backup_loading_desc: '正在读取 Omlorix 管理界面中配置的目标和加密设置。',
      launcher_backup_load_failed_title: '无法加载备份设置',
      launcher_backup_load_failed_desc: '请确认 Omlorix 已就绪，然后重新加载备份目标。',
      launcher_backup_retry_action: '重试',
      launcher_backup_create_desc: '使用 Omlorix 管理界面中配置的目标创建完整服务器备份。',
      launcher_backup_destination_label: '目标',
      launcher_backup_encryption_title: '归档加密',
      launcher_backup_encryption_desc: '使用为此服务器配置的备份密码加密归档。',
      launcher_backup_setup_title: '请先配置备份加密',
      launcher_backup_setup_desc: '在启动器设置中设定备份密码，重启 Omlorix 后重试。',
      launcher_backup_plaintext_only_desc: '加密不可用；此服务器明确允许未加密的备份归档。',
      launcher_backup_create_action: '创建服务器备份',
      launcher_backup_creating_action: '正在创建备份…',
      launcher_backup_finished: '备份已完成。',
      launcher_backup_encrypted: '已加密',
      launcher_backup_plaintext: '未加密',
      launcher_backup_result_title: '备份创建成功',
      launcher_backup_result_job: '备份任务：{jobId}',
      launcher_backup_failed_generic: '无法创建备份。请查看启动器日志了解详情。',
      launcher_restore_action: '恢复备份',
      launcher_restore_picker_title: '选择 Omlorix 备份',
      launcher_restore_picker_button: '选择备份',
      launcher_restore_filter: 'Omlorix 备份归档',
      launcher_restore_all_files: '所有文件',
      launcher_restore_confirm_title: '恢复此服务器？',
      launcher_restore_confirm_message: 'Omlorix 将停止、验证 {file}、创建安全备份，然后替换数据库和文件并重新启动。当前数据将被覆盖。',
      launcher_restore_confirm_action: '恢复服务器',
      launcher_restore_running: '正在恢复服务器',
      launcher_restore_requires_running: '开始安全恢复前必须先运行 Omlorix。',
      launcher_restore_stopping_services: '正在停止应用服务以准备恢复',
      launcher_update_stopping_services: '正在停止应用服务以准备数据库迁移',
      launcher_update_rollback_left_offline: '更新在数据库迁移可能已开始后失败。目标版本 {targetVersion} 仍保持选中，Omlorix 不会启动较旧版本。重试或恢复兼容备份前，请确认服务已停止并检查操作日志。',
      launcher_update_pre_migration_rollback_left_offline: '更新在数据库迁移开始前失败，且无法安全地重新启动先前版本 {previousVersion}。Omlorix 已保持离线。启动前请确认服务已停止并检查操作日志。',
      launcher_migration_resetting: '正在重置迁移容器',
      launcher_migration_running: '正在运行数据库迁移',
      launcher_migration_recreating_services: '正在重新创建应用服务',
      launcher_restore_restoring_data: '正在验证备份并恢复服务器数据',
      launcher_restore_starting_services: '恢复后正在启动 Omlorix',
      launcher_restore_ready_at: 'Omlorix 已恢复并可在 {url} 使用',
      launcher_restore_finished: 'Omlorix 恢复完成。',
      launcher_restore_restarting_after_failure: '恢复已安全停止。正在使用现有或已恢复的服务器数据重新启动 Omlorix。',
      launcher_restore_stopped_safely: '恢复已停止，未留下已更改的服务器数据，Omlorix 已恢复为完全健康状态。原因：{error}',
      launcher_restore_restart_failed: '恢复已安全停止，但 Omlorix 未恢复为完全健康状态。恢复原因：{error}。重启错误：{restartError}',
      launcher_restore_reason_target_not_empty: '恢复目标不为空。',
      launcher_restore_reason_missing_required_files: '备份存档不完整。',
      launcher_restore_reason_checksum_mismatch: '备份存档未通过校验和验证。',
      launcher_restore_reason_encryption_key_mismatch: '无法使用此服务器的加密密钥解密备份存档。',
      launcher_restore_reason_manifest_parse_failed: '备份清单无效。',
      launcher_restore_reason_payload_tar_parse_failed: '备份负载无效。',
      launcher_restore_reason_archive_extracted_size_exceeded: '备份超过配置的恢复大小限制。',
      launcher_restore_reason_insufficient_disk_space: '可用磁盘空间不足，无法安全恢复此备份。',
      launcher_restore_reason_source_access_failed: '无法访问备份源。',
      launcher_restore_recovery_unconfirmed: '恢复失败，且无法确认是否已安全恢复。为保护服务器数据，Omlorix 已保持停止状态。请在重新启动前查看恢复日志。原始错误：{error}',
      launcher_restore_startup_failed_after_restore: '服务器数据已恢复，但 Omlorix 启动失败。已恢复的数据未回滚。启动错误：{error}',
      launcher_possible_database_downgrade: '此服务器曾运行 {highestVersion}，之后 Omlorix {currentVersion} 无法启动。较新版本可能已应用旧版本无法撤销或读取的数据库迁移。为保护数据，请使用 Omlorix {highestVersion} 或更高版本，或者恢复与 {currentVersion} 兼容的数据库备份。原始启动错误：{error}',
      launcher_server_update_label: '服务器更新',
      launcher_server_update_available_title: 'Omlorix {latestVersion} 已可用',
      launcher_server_update_description: '当前版本：{currentVersion} · 通道：{channel}',
      launcher_server_update_action: '更新到 {latestVersion}',
      launcher_launcher_update_label: '启动器更新',
      launcher_launcher_update_available_title: 'Server Launcher {latestVersion} 可用',
      launcher_launcher_update_description: '当前：{currentVersion} · 通道：{channel}',
      launcher_launcher_update_action: '更新启动器',
      launcher_server_update_launcher_check_action: '再次检查',
      launcher_server_update_launcher_ready_description: 'Server Launcher {latestLauncherVersion} 可用，并满足最低版本要求 {minimumLauncherVersion}。请先更新启动器。',
      launcher_server_update_launcher_feed_behind_description: '启动器通道目前提供 {latestLauncherVersion}，但此 Omlorix 版本需要 {minimumLauncherVersion} 或更高版本。请在兼容的启动器发布后再次检查。',
      launcher_server_update_launcher_required_title: 'Omlorix {latestVersion} 需要更新启动器',
      launcher_server_update_launcher_required_description: '安装此服务器版本前，请将 Omlorix Server Launcher 更新到 {minimumLauncherVersion} 或更高版本。',
      launcher_server_update_launcher_action: '更新启动器',
      launcher_server_update_requires_running: '安装此更新前，请启动 Omlorix 并解决所有设置警告。',
      launcher_server_update_channel_stable: '稳定',
      launcher_server_update_channel_beta: '测试版',
      launcher_visitor_ips_heading: '访客 IP',
      launcher_visitor_ip_title_proxy_stopped: '代理已停止',
      launcher_visitor_ip_message_proxy_stopped: '启动器代理已启用但处于停止状态。请启动代理或启用自动启动，以便访客 IP 通过代理到达 Omlorix。',
      launcher_visitor_ip_title_proxy_running: '代理已验证',
      launcher_visitor_ip_message_proxy_running: '最近的端到端请求已通过完整代理路径验证访客 IP 和公共协议。',
      launcher_visitor_ip_repair_external_applied: '已应用访客 IP 信任设置。请从外部客户端验证外部代理。',
      launcher_visitor_ip_repair_failed: '无法应用并验证访客 IP 设置。已恢复之前的配置。',
      launcher_visitor_ip_title_repair_failed: '自动修复失败',
      launcher_visitor_ip_message_repair_failed: '{error} 请确保 Omlorix 正在运行且已准备就绪，然后重试。有关详细信息，请查看控制台。',
      launcher_visitor_ip_title_verification_failed: '验证失败',
      launcher_visitor_ip_message_verification_failed: '代理正在运行，但 Omlorix 无法通过完整请求路径验证访客 IP 和公共协议。',
      launcher_visitor_ip_direct_probe: ' Docker 直连探测检测到 {ip}。',
      launcher_visitor_ip_action_open_proxy: '打开代理设置',
      launcher_visitor_ip_action_start_proxy: '启动代理',
      launcher_visitor_ip_action_reapply: '重新应用设置',
      launcher_visitor_ip_action_fix: '自动修复',
      launcher_visitor_ip_title_restart_required: '需要重新启动 Omlorix',
      launcher_visitor_ip_message_restart_required: '代理设置已保存，但正在运行的 Omlorix 容器仍使用之前的访客 IP 配置。请重新启动 Omlorix 以应用设置。',
      launcher_visitor_ip_action_restart_omlorix: '重新启动 Omlorix',
      launcher_proxy_action_starting: '正在启动代理',
      launcher_proxy_action_started: '代理已启动。',
      launcher_proxy_action_start_failed: '代理启动失败：{error}',
      launcher_proxy_background_service_installed: '后台服务已安装',
      launcher_proxy_background_service_not_installed: '后台服务未安装',
      launcher_proxy_background_service_unavailable: '此构建中不提供后台服务',
      launcher_proxy_install_background_service: '安装后台服务',
      launcher_proxy_remove_background_service: '移除后台服务',
      launcher_proxy_installing_background_service: '正在安装后台代理服务',
      launcher_proxy_removing_background_service: '正在移除后台代理服务',
      launcher_services_subtitle: '预期服务及其容器的当前状态。',
      launcher_services_auto_refresh: '每 10 秒更新一次',
      launcher_services_auto_refresh_active: '操作进行时每 2 秒更新一次',
      launcher_services_running_count: '已运行 {running}/{total}',
      launcher_service_not_created: '未创建',
      launcher_service_not_running: '未运行',
      launcher_services_empty: '尚未配置任何服务。',
      launcher_stack_all_running_detail: '所有预期的 Omlorix 服务都已运行。',
      launcher_stack_partial_running_detail: '有 {count} 个预期的 Omlorix 服务未运行。',
      launcher_stack_none_running_detail: '预期的 Omlorix 服务均未运行。',
      launcher_stack_health_issues_detail: '有 {count} 个预期的 Omlorix 服务尚未达到健康状态。',
    },
  };

  const VISITOR_IP_FALLBACK_TRANSLATIONS = {
    ar: {
      launcher_visitor_ip_title_needs_setup: 'يلزم الإعداد',
      launcher_visitor_ip_message_needs_setup: 'فعّل رؤوس الوكيل الموثوق بها لكي تستخدم حدود المعدل وسجلات التدقيق وفحوص المصادقة وسجلات الوصول عنوان IP الخاص بالزائر.',
      launcher_visitor_ip_title_proxy_ready: 'جاهز للوكيل',
      launcher_visitor_ip_message_proxy_ready: 'Omlorix جاهز للوثوق بوكيل المضيف أو بوكيل خارجي. قد تظل شبكة آلة Docker الافتراضية تخفي عناوين IP ما لم تصل الحركة إلى ذلك الوكيل قبل Docker.',
      launcher_visitor_ip_title_configured: 'تم الإعداد',
      launcher_visitor_ip_message_configured: 'تم إعداد تحليل الوكيل الموثوق به لمسار وكيل Docker المضمّن.',
    },
    de: {
      launcher_visitor_ip_title_needs_setup: 'Muss eingerichtet werden',
      launcher_visitor_ip_message_needs_setup: 'Aktiviere vertrauenswürdige Proxy-Header, damit Ratenbegrenzungen, Audit-Protokolle, Authentifizierungsprüfungen und Zugriffsprotokolle die Besucher-IP verwenden.',
      launcher_visitor_ip_title_proxy_ready: 'Proxy-bereit',
      launcher_visitor_ip_message_proxy_ready: 'Omlorix ist bereit, einem Host- oder externen Proxy zu vertrauen. Die Docker-VM-Vernetzung kann IPs weiterhin verbergen, wenn der Datenverkehr den Proxy nicht vor Docker erreicht.',
      launcher_visitor_ip_title_configured: 'Konfiguriert',
      launcher_visitor_ip_message_configured: 'Die Auswertung vertrauenswürdiger Proxys ist für den gebündelten Docker-Proxy-Pfad konfiguriert.',
    },
    es: {
      launcher_visitor_ip_title_needs_setup: 'Requiere configuración',
      launcher_visitor_ip_message_needs_setup: 'Activa las cabeceras de proxy de confianza para que los límites de frecuencia, los registros de auditoría, las comprobaciones de autenticación y los registros de acceso usen la IP del visitante.',
      launcher_visitor_ip_title_proxy_ready: 'Listo para el proxy',
      launcher_visitor_ip_message_proxy_ready: 'Omlorix está listo para confiar en un proxy del host o externo. La red de la máquina virtual de Docker aún puede ocultar las IP si el tráfico no llega a ese proxy antes que a Docker.',
      launcher_visitor_ip_title_configured: 'Configurado',
      launcher_visitor_ip_message_configured: 'El análisis del proxy de confianza está configurado para la ruta del proxy de Docker incluido.',
    },
    fr: {
      launcher_visitor_ip_title_needs_setup: 'Configuration requise',
      launcher_visitor_ip_message_needs_setup: 'Activez les en-têtes de proxy approuvés afin que les limites de débit, les journaux d’audit, les contrôles d’authentification et les journaux d’accès utilisent l’IP du visiteur.',
      launcher_visitor_ip_title_proxy_ready: 'Prêt pour le proxy',
      launcher_visitor_ip_message_proxy_ready: 'Omlorix est prêt à approuver un proxy hôte ou externe. Le réseau de la machine virtuelle Docker peut encore masquer les IP si le trafic n’atteint pas ce proxy avant Docker.',
      launcher_visitor_ip_title_configured: 'Configuré',
      launcher_visitor_ip_message_configured: 'L’analyse des proxys approuvés est configurée pour le chemin du proxy Docker intégré.',
    },
    hi: {
      launcher_visitor_ip_title_needs_setup: 'सेटअप आवश्यक',
      launcher_visitor_ip_message_needs_setup: 'विश्वसनीय प्रॉक्सी हेडर चालू करें, ताकि दर सीमाएँ, ऑडिट लॉग, प्रमाणीकरण जाँच और एक्सेस लॉग विज़िटर IP का उपयोग करें।',
      launcher_visitor_ip_title_proxy_ready: 'प्रॉक्सी के लिए तैयार',
      launcher_visitor_ip_message_proxy_ready: 'Omlorix किसी होस्ट या बाहरी प्रॉक्सी पर भरोसा करने के लिए तैयार है। यदि ट्रैफ़िक Docker से पहले उस प्रॉक्सी तक नहीं पहुँचता, तो Docker VM नेटवर्किंग अब भी IP छिपा सकती है।',
      launcher_visitor_ip_title_configured: 'कॉन्फ़िगर किया गया',
      launcher_visitor_ip_message_configured: 'विश्वसनीय प्रॉक्सी पार्सिंग बंडल किए गए Docker प्रॉक्सी पथ के लिए कॉन्फ़िगर है।',
    },
    it: {
      launcher_visitor_ip_title_needs_setup: 'Configurazione necessaria',
      launcher_visitor_ip_message_needs_setup: 'Abilita le intestazioni proxy attendibili affinché limiti di frequenza, log di audit, controlli di autenticazione e log di accesso usino l’IP del visitatore.',
      launcher_visitor_ip_title_proxy_ready: 'Pronto per il proxy',
      launcher_visitor_ip_message_proxy_ready: 'Omlorix è pronto a considerare attendibile un proxy host o esterno. La rete della VM Docker può comunque nascondere gli IP se il traffico non raggiunge quel proxy prima di Docker.',
      launcher_visitor_ip_title_configured: 'Configurato',
      launcher_visitor_ip_message_configured: 'L’analisi dei proxy attendibili è configurata per il percorso del proxy Docker incluso.',
    },
    ja: {
      launcher_visitor_ip_title_needs_setup: '設定が必要',
      launcher_visitor_ip_message_needs_setup: 'レート制限、監査ログ、認証チェック、アクセスログで訪問者 IP を使用できるよう、信頼できるプロキシヘッダーを有効にしてください。',
      launcher_visitor_ip_title_proxy_ready: 'プロキシの準備完了',
      launcher_visitor_ip_message_proxy_ready: 'Omlorix はホストまたは外部プロキシを信頼する準備ができています。トラフィックが Docker より先にそのプロキシへ到達しない場合、Docker VM ネットワークによって IP が隠れることがあります。',
      launcher_visitor_ip_title_configured: '設定済み',
      launcher_visitor_ip_message_configured: '信頼できるプロキシの解析は、同梱の Docker プロキシ経路向けに設定されています。',
    },
    pt: {
      launcher_visitor_ip_title_needs_setup: 'Requer configuração',
      launcher_visitor_ip_message_needs_setup: 'Ative os cabeçalhos de proxy fidedignos para que os limites de frequência, os registos de auditoria, as verificações de autenticação e os registos de acesso utilizem o IP do visitante.',
      launcher_visitor_ip_title_proxy_ready: 'Pronto para o proxy',
      launcher_visitor_ip_message_proxy_ready: 'O Omlorix está pronto para confiar num proxy do anfitrião ou externo. A rede da máquina virtual do Docker ainda pode ocultar IPs se o tráfego não chegar a esse proxy antes do Docker.',
      launcher_visitor_ip_title_configured: 'Configurado',
      launcher_visitor_ip_message_configured: 'A análise de proxies fidedignos está configurada para o caminho do proxy Docker incluído.',
    },
    ru: {
      launcher_visitor_ip_title_needs_setup: 'Требуется настройка',
      launcher_visitor_ip_message_needs_setup: 'Включите доверенные заголовки прокси, чтобы ограничения частоты, журналы аудита, проверки аутентификации и журналы доступа использовали IP-адрес посетителя.',
      launcher_visitor_ip_title_proxy_ready: 'Готово для прокси',
      launcher_visitor_ip_message_proxy_ready: 'Omlorix готов доверять прокси на хосте или внешнему прокси. Сеть виртуальной машины Docker всё ещё может скрывать IP-адреса, если трафик не поступает на этот прокси до Docker.',
      launcher_visitor_ip_title_configured: 'Настроено',
      launcher_visitor_ip_message_configured: 'Обработка доверенного прокси настроена для встроенного пути прокси Docker.',
    },
    zh: {
      launcher_visitor_ip_title_needs_setup: '需要设置',
      launcher_visitor_ip_message_needs_setup: '请启用可信代理标头，以便速率限制、审计日志、身份验证检查和访问日志使用访客 IP。',
      launcher_visitor_ip_title_proxy_ready: '代理已就绪',
      launcher_visitor_ip_message_proxy_ready: 'Omlorix 已准备好信任主机代理或外部代理。如果流量未在进入 Docker 前到达该代理，Docker 虚拟机网络仍可能隐藏 IP。',
      launcher_visitor_ip_title_configured: '已配置',
      launcher_visitor_ip_message_configured: '已为内置 Docker 代理路径配置可信代理解析。',
    },
  };

  for (const [language, translations] of Object.entries(VISITOR_IP_FALLBACK_TRANSLATIONS)) {
    Object.assign(LAUNCHER_TRANSLATIONS[language], translations);
  }

  // The complete launcher catalog is kept in a separate renderer asset so the
  // sizeable set of static labels does not obscure the launcher behavior. Merge
  // it with the focused dynamic-operation catalog above before rendering.
  const COMPLETE_LAUNCHER_TRANSLATIONS = window.OmlorixLauncherTranslations || {
    source: {},
    locales: {},
  };
  for (const [language, translations] of Object.entries(COMPLETE_LAUNCHER_TRANSLATIONS.locales || {})) {
    LAUNCHER_TRANSLATIONS[language] = {
      ...(LAUNCHER_TRANSLATIONS[language] || {}),
      ...translations,
    };
  }

  function launcherLanguage() {
    const activeLocale = String(document.documentElement.lang || 'en')
      .toLowerCase()
      .split('-')[0];
    return LAUNCHER_TRANSLATIONS[activeLocale] ? activeLocale : 'en';
  }

  /** Follow the direction selected by the active translation service. */
  function launcherDirection() {
    return document.documentElement.dir === 'rtl' ? 'rtl' : 'ltr';
  }

  function launcherT(key, fallback, vars = {}) {
    const language = launcherLanguage();
    const raw = typeof window.getTranslation === 'function'
      ? window.getTranslation(key, fallback)
      : (LAUNCHER_TRANSLATIONS[language]?.[key] || fallback);
    return String(raw || fallback || '').replace(/\{(\w+)\}/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match
    ));
  }

  // Keep this mapping explicit: translation keys are stable catalog entries,
  // while the backend-provided value is treated only as an allowlisted code.
  const RESTORE_REASON_TRANSLATIONS = Object.freeze({
    target_not_empty: {
      key: 'launcher_restore_reason_target_not_empty',
      fallback: 'The restore target is not empty.',
    },
    missing_required_files: {
      key: 'launcher_restore_reason_missing_required_files',
      fallback: 'The backup archive is incomplete.',
    },
    checksum_mismatch: {
      key: 'launcher_restore_reason_checksum_mismatch',
      fallback: 'The backup archive failed checksum verification.',
    },
    encryption_key_mismatch: {
      key: 'launcher_restore_reason_encryption_key_mismatch',
      fallback: 'The backup archive cannot be decrypted with this server\'s encryption key.',
    },
    manifest_parse_failed: {
      key: 'launcher_restore_reason_manifest_parse_failed',
      fallback: 'The backup manifest is invalid.',
    },
    payload_tar_parse_failed: {
      key: 'launcher_restore_reason_payload_tar_parse_failed',
      fallback: 'A backup payload is invalid.',
    },
    archive_extracted_size_exceeded: {
      key: 'launcher_restore_reason_archive_extracted_size_exceeded',
      fallback: 'The backup exceeds the configured restore size limit.',
    },
    insufficient_disk_space: {
      key: 'launcher_restore_reason_insufficient_disk_space',
      fallback: 'There is not enough free disk space to restore this backup safely.',
    },
    source_access_failed: {
      key: 'launcher_restore_reason_source_access_failed',
      fallback: 'The backup source could not be accessed.',
    },
  });

  /** Localize an allowlisted structured restore reason before interpolation. */
  function localizedOperationMessageValues(rawValues = {}) {
    const values = { ...rawValues };
    const reasonCode = String(values.restoreReasonCode || '');
    const reasonTranslation = RESTORE_REASON_TRANSLATIONS[reasonCode];
    if (reasonTranslation) {
      values.error = launcherT(
        reasonTranslation.key,
        String(values.error || reasonTranslation.fallback),
      );
    }
    delete values.restoreReasonCode;
    return values;
  }

  const LAUNCHER_SOURCE_KEYS = new Map(
    Object.entries(COMPLETE_LAUNCHER_TRANSLATIONS.source || {}).map(([key, source]) => [source, key]),
  );

  const LAUNCHER_SOURCE_TEMPLATES = Object.entries(COMPLETE_LAUNCHER_TRANSLATIONS.source || {})
    .filter(([_key, source]) => /\{\w+\}/.test(source))
    .map(([key, source]) => {
      const names = [];
      let cursor = 0;
      let expression = '^';
      for (const match of source.matchAll(/\{(\w+)\}/g)) {
        expression += source
          .slice(cursor, match.index)
          .replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        expression += '([\\s\\S]+?)';
        names.push(match[1]);
        cursor = match.index + match[0].length;
      }
      expression += source.slice(cursor).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      expression += '$';
      // Placeholder names are implementation details and must not make a broad
      // template appear more specific than one with more fixed copy. For
      // example, "Settings failed: {value1}" must win over the generic
      // "{value1} failed: {value2}" template.
      const literalLength = source.replace(/\{\w+\}/g, '').length;
      return {
        key,
        source,
        names,
        literalLength,
        expression: new RegExp(expression),
      };
    })
    .sort((left, right) => (
      right.literalLength - left.literalLength
      || left.names.length - right.names.length
      || right.source.length - left.source.length
    ));

  /** Translate a known English launcher phrase through its stable catalog key. */
  function translateLauncherSource(source) {
    // HTML formatting can insert newlines and indentation into a single text
    // node. Collapse that presentation-only whitespace so wrapped launcher
    // copy still resolves to the same stable catalog entry as its source text.
    const normalized = String(source || '').trim().replace(/\s+/g, ' ');
    const key = LAUNCHER_SOURCE_KEYS.get(normalized);
    if (key) return launcherT(key, normalized);

    for (const template of LAUNCHER_SOURCE_TEMPLATES) {
      const match = normalized.match(template.expression);
      if (!match) continue;
      const values = Object.fromEntries(
        template.names.map((name, index) => [name, match[index + 1]]),
      );
      return launcherT(template.key, template.source, values);
    }
    return normalized;
  }

  // Focused renderer modules use the same stable, hard-coded catalog without
  // duplicating locale selection or fallback behavior.
  if (typeof window !== 'undefined') {
    window.omlorixLauncherTranslate = translateLauncherSource;
  }

  /** Translate static text and accessibility attributes in launcher-owned UI. */
  function applyLauncherTranslations(root = document) {
    const translateTextNode = (node) => {
      const raw = String(node.nodeValue || '');
      const normalized = raw.trim();
      if (!normalized) return;
      const translated = translateLauncherSource(normalized);
      if (translated === normalized) return;
      const leading = raw.match(/^\s*/)?.[0] || '';
      const trailing = raw.match(/\s*$/)?.[0] || '';
      node.nodeValue = `${leading}${translated}${trailing}`;
    };

    if (root.nodeType === Node.TEXT_NODE) {
      translateTextNode(root);
      return;
    }

    const scope = root.nodeType === Node.ELEMENT_NODE || root.nodeType === Node.DOCUMENT_NODE
      ? root
      : null;
    if (!scope) return;

    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      translateTextNode(node);
      node = walker.nextNode();
    }

    const elements = scope.nodeType === Node.ELEMENT_NODE
      ? [scope, ...scope.querySelectorAll('*')]
      : Array.from(scope.querySelectorAll('*'));
    for (const element of elements) {
      for (const attribute of ['aria-label', 'title', 'placeholder']) {
        const source = element.getAttribute(attribute);
        if (!source || !LAUNCHER_SOURCE_KEYS.has(source.trim())) continue;
        const translated = translateLauncherSource(source);
        // MutationObserver reports attribute writes even when the value is
        // unchanged. Some technical labels and placeholders intentionally
        // stay identical across languages, so writing those values again
        // would make the translation observer continuously trigger itself.
        if (translated === source) continue;
        element.dataset[`launcherSource${attribute.replace(/(^|-)(\w)/g, (_match, _dash, letter) => letter.toUpperCase())}`] = source;
        element.setAttribute(attribute, translated);
      }
    }
  }

  // Renderer-created status rows and menu options are translated as they are
  // inserted. Exact source phrases still resolve through hard-coded keys.
  const isWithinConsoleOutput = (node) => Boolean(
    node && els.consoleOutput && els.consoleOutput.contains(node),
  );
  const launcherTranslationObserver = new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === 'attributes') {
        applyLauncherTranslations(record.target);
        continue;
      }
      if (record.type === 'characterData') {
        if (isWithinConsoleOutput(record.target)) continue;
        applyLauncherTranslations(record.target);
        continue;
      }
      for (const node of record.addedNodes) {
        if (isWithinConsoleOutput(record.target) || isWithinConsoleOutput(node)) continue;
        applyLauncherTranslations(node);
      }
    }
  });
  applyLauncherTranslations(document);
  launcherTranslationObserver.observe(document.body, {
    attributes: true,
    attributeFilter: ['aria-label', 'title', 'placeholder'],
    childList: true,
    characterData: true,
    subtree: true,
  });

  function payloadText(payload, field, keyField, valuesField) {
    const text = String(payload?.[field] || '');
    const key = payload?.[keyField];
    if (!key) return text;
    const leading = text.match(/^\s*/)?.[0] || '';
    const trailing = text.match(/\s*$/)?.[0] || '';
    return `${leading}${launcherT(key, text.trim(), payload?.[valuesField] || {})}${trailing}`;
  }

  function setBadge(kind, text) {
    els.overallBadge.className = `tag badge badge-${kind}`;
    els.overallBadge.textContent = text;
    if (els.overallDot) {
      const dotKind = kind === 'error' ? 'err' : kind;
      els.overallDot.className = `dot ${dotKind}`;
    }
  }

  function currentMode() {
    return document.documentElement.getAttribute('data-mode') === 'dark' ? 'dark' : 'light';
  }

  function updateThemeToggleIcon() {
    if (!els.themeIcon) return;
    Icons.setSvgContents(els.themeIcon, currentMode() === 'dark' ? 'sun' : 'night');
  }

  function toggleThemeMode() {
    const nextMode = currentMode() === 'dark' ? 'light' : 'dark';
    try {
      localStorage.setItem('mode', nextMode);
    } catch (error) {
      // If localStorage is unavailable, still update the active window.
    }
    document.documentElement.setAttribute('data-mode', nextMode);
    // Storage events do not fire in the window that changed localStorage, so
    // update Electron's resize backing surface directly for this local toggle.
    window.omlorixServer?.setWindowBackground?.(nextMode)?.catch(() => {});
    document.querySelectorAll('meta[name="theme-color"]').forEach((element) => {
      element.setAttribute('content', nextMode === 'dark' ? '#0a0a0a' : '#ffffff');
    });
    updateThemeToggleIcon();
  }

  function applyWindowMode(payload = {}) {
    const root = document.documentElement;
    const isFullscreen = payload.fullscreen === true || payload.mode === 'fullscreen';
    root.dataset.windowMode = isFullscreen ? 'fullscreen' : 'window';
  }

  async function initializeWindowMode() {
    applyWindowMode({ fullscreen: false });
    if (!window.omlorixServer?.getWindowMode) return;

    try {
      applyWindowMode(await window.omlorixServer.getWindowMode());
    } catch (error) {
      // Keep the launcher usable in browser-based mocks where Electron IPC is absent.
    }

    window.omlorixServer.onWindowModeChanged?.(applyWindowMode);
  }

  function closeCustomSelect(select) {
    const entry = selectEnhancements.get(select);
    if (!entry) return;
    entry.root.classList.remove('open');
    entry.button.setAttribute('aria-expanded', 'false');
    if (entry.portalPositionHandler) {
      document.removeEventListener('scroll', entry.portalPositionHandler, true);
      window.removeEventListener('resize', entry.portalPositionHandler);
      entry.portalPositionHandler = null;
    }
    if (entry.menu.classList.contains('select-menu-portal')) {
      // Restore the menu to its select wrapper after closing so normal layout,
      // keyboard behavior, and future opens continue to use the same control.
      entry.menu.classList.remove('select-menu-portal', 'is-open');
      entry.menu.style.removeProperty('top');
      entry.menu.style.removeProperty('left');
      entry.menu.style.removeProperty('right');
      entry.menu.style.removeProperty('width');
      entry.menu.style.removeProperty('max-height');
      entry.root.appendChild(entry.menu);
    }
  }

  function closeAllCustomSelects(except = null) {
    for (const select of selectEnhancements.keys()) {
      if (select !== except) closeCustomSelect(select);
    }
  }

  function selectedOption(select) {
    return select.selectedOptions?.[0] || Array.from(select.options).find((option) => option.value === select.value) || select.options[0] || null;
  }

  /** Keep a portaled menu aligned to its trigger and inside the viewport. */
  function positionPortalSelectMenu(select) {
    const entry = selectEnhancements.get(select);
    if (!entry?.menu.classList.contains('select-menu-portal')) return;

    const bounds = entry.button.getBoundingClientRect();
    const gutter = 12;
    const gap = 6;
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const width = Math.max(0, Math.min(bounds.width, viewportWidth - (gutter * 2)));
    const left = Math.max(gutter, Math.min(bounds.left, viewportWidth - width - gutter));
    const spaceBelow = Math.max(0, viewportHeight - bounds.bottom - gutter - gap);
    const spaceAbove = Math.max(0, bounds.top - gutter - gap);
    const desiredHeight = Math.min(260, entry.menu.scrollHeight);
    const openAbove = spaceBelow < desiredHeight && spaceAbove > spaceBelow;
    const availableHeight = openAbove ? spaceAbove : spaceBelow;
    const maxHeight = Math.min(260, availableHeight);
    const menuHeight = Math.min(entry.menu.scrollHeight, maxHeight);
    const preferredTop = openAbove
      ? bounds.top - gap - menuHeight
      : bounds.bottom + gap;
    const top = Math.max(gutter, Math.min(preferredTop, viewportHeight - menuHeight - gutter));

    entry.menu.style.top = `${top}px`;
    entry.menu.style.left = `${left}px`;
    entry.menu.style.right = 'auto';
    entry.menu.style.width = `${width}px`;
    entry.menu.style.maxHeight = `${maxHeight}px`;
  }

  /** Open an opt-in setup menu above clipping containers such as the wizard footer. */
  function openCustomSelect(select) {
    const entry = selectEnhancements.get(select);
    if (!entry) return;
    entry.root.classList.add('open');
    entry.button.setAttribute('aria-expanded', 'true');

    if (select.dataset.selectMenuPortal !== 'true') return;

    // The setup body scrolls and clips descendants. Moving this one menu to
    // the document layer lets it overlay the fixed wizard footer.
    entry.menu.classList.add('select-menu-portal', 'is-open');
    document.body.appendChild(entry.menu);
    entry.portalPositionHandler = () => positionPortalSelectMenu(select);
    document.addEventListener('scroll', entry.portalPositionHandler, true);
    window.addEventListener('resize', entry.portalPositionHandler);
    positionPortalSelectMenu(select);
  }

  function syncCustomSelect(select) {
    const entry = selectEnhancements.get(select);
    if (!entry) return;
    const current = selectedOption(select);
    entry.label.textContent = current ? current.textContent : '';
    entry.button.disabled = select.disabled;
    entry.button.setAttribute('aria-disabled', select.disabled ? 'true' : 'false');
    entry.menu.querySelectorAll('.select-opt').forEach((optionElement) => {
      optionElement.setAttribute('aria-selected', optionElement.dataset.value === select.value ? 'true' : 'false');
    });
  }

  function focusSelectedCustomOption(select) {
    const entry = selectEnhancements.get(select);
    if (!entry) return;
    const selectedItem = entry.menu.querySelector('.select-opt[aria-selected="true"]:not([aria-disabled="true"])');
    const firstEnabledItem = entry.menu.querySelector('.select-opt:not([aria-disabled="true"])');
    (selectedItem || firstEnabledItem)?.focus();
  }

  function chooseCustomSelectOption(select, option, entry) {
    if (option.disabled) return;
    select.value = option.value;
    syncCustomSelect(select);
    closeCustomSelect(select);
    select.dispatchEvent(new Event('change', { bubbles: true }));
    entry.button.focus();
  }

  function rebuildCustomSelect(select) {
    const entry = selectEnhancements.get(select);
    if (!entry) return;
    entry.menu.replaceChildren();
    Array.from(select.options).forEach((option) => {
      const item = document.createElement('div');
      item.className = 'select-opt';
      item.dataset.value = option.value;
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', option.value === select.value ? 'true' : 'false');
      item.tabIndex = option.disabled ? -1 : 0;
      if (option.disabled) {
        item.setAttribute('aria-disabled', 'true');
      }
      item.innerHTML = `${SELECT_CHECK_SVG}<span></span>`;
      item.querySelector('span').textContent = option.textContent;
      item.addEventListener('click', () => chooseCustomSelectOption(select, option, entry));
      item.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          chooseCustomSelectOption(select, option, entry);
        }
        if (event.key === 'Escape') {
          event.preventDefault();
          closeCustomSelect(select);
          entry.button.focus();
        }
      });
      entry.menu.appendChild(item);
    });
    syncCustomSelect(select);
  }

  function enhanceSelect(select) {
    if (!(select instanceof HTMLSelectElement) || selectEnhancements.has(select)) return;
    const root = document.createElement('div');
    root.className = 'select';

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'select-btn';
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    button.innerHTML = `<span class="select-btn-label"></span>${SELECT_CARET_SVG}`;

    const menu = document.createElement('div');
    menu.className = 'select-menu';
    menu.setAttribute('role', 'listbox');

    root.append(button, menu);
    select.after(root);
    select.classList.add('native-select-enhanced');

    const observer = new MutationObserver(() => rebuildCustomSelect(select));
    observer.observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'label', 'value'] });

    selectEnhancements.set(select, {
      button,
      label: button.querySelector('.select-btn-label'),
      menu,
      observer,
      portalPositionHandler: null,
      root,
    });

    button.addEventListener('click', (event) => {
      event.stopPropagation();
      if (select.disabled) return;
      const open = root.classList.contains('open');
      if (open) {
        closeCustomSelect(select);
        return;
      }
      closeAllCustomSelects(select);
      openCustomSelect(select);
    });

    button.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closeCustomSelect(select);
      }
      if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        closeAllCustomSelects(select);
        openCustomSelect(select);
        focusSelectedCustomOption(select);
      }
    });

    select.addEventListener('change', () => syncCustomSelect(select));
    rebuildCustomSelect(select);
  }

  function destroySelectEnhancement(select) {
    const entry = selectEnhancements.get(select);
    if (!entry) return;
    entry.observer.disconnect();
    closeCustomSelect(select);
    entry.root.remove();
    select.classList.remove('native-select-enhanced');
    selectEnhancements.delete(select);
  }

  function destroySelectEnhancementsIn(root) {
    const scope = root || document;
    scope.querySelectorAll('select').forEach(destroySelectEnhancement);
  }

  function enhanceSelectsIn(root) {
    const scope = root || document;
    scope.querySelectorAll('select').forEach(enhanceSelect);
  }

  function sidebarBadgeState(docker, stack) {
    if (!docker.installed) {
      return {
        kind: 'error',
        text: 'Docker missing',
        title: 'Docker is not installed yet.',
      };
    }
    if (!docker.running) {
      return {
        kind: 'error',
        text: 'Docker offline',
        title: 'Docker is installed, but it is not running.',
      };
    }
    if (!docker.compose) {
      return {
        kind: 'error',
        text: 'Compose missing',
        title: 'Docker is running, but the Compose plugin is missing.',
      };
    }
    if (stack.healthy && expectedServicesAreRunning(stack)) {
      return {
        kind: 'ok',
        text: 'Ready',
        title: 'Docker and the Omlorix stack are healthy.',
      };
    }
    if (stack.running > 0) {
      return {
        kind: 'warn',
        text: 'Starting',
        title: 'Docker is ready, but the stack is still starting.',
      };
    }
    return {
      kind: 'muted',
      text: 'Stopped',
      title: 'Docker is ready, but the Omlorix stack is stopped.',
    };
  }

  function dockerIsReady(docker) {
    return Boolean(docker?.installed && docker.running && docker.compose);
  }

  function dockerActionsBlocked() {
    return Boolean(state.current?.docker && !dockerIsReady(state.current.docker));
  }

  /** Return whether every configured long-running Compose service is up. */
  function expectedServicesAreRunning(stack = {}) {
    const running = Number(stack.running || 0);
    const total = Number(stack.total || 0);
    const healthIssues = Number(stack.healthIssues || 0);
    if (total > 0) return running === total && healthIssues === 0;
    // Older or temporarily degraded status responses may not know the
    // configured service set. Preserve endpoint-based readiness in that case.
    return stack.expectedKnown !== true && Boolean(stack.healthy);
  }

  function omlorixServiceRunning(stack = state.current?.stack || {}) {
    return Array.isArray(stack.services) && stack.services.some((service) => {
      const name = String(service.Service || service.Name || service.Names || '').toLowerCase();
      const stateName = String(service.State || '').toLowerCase();
      return name.includes('fastapi') && stateName === 'running';
    });
  }

  function omlorixActionsBlocked() {
    const stack = state.current?.stack;
    return Boolean(stack && (!stack.running || !omlorixServiceRunning(stack)));
  }

  /** A backup form is useful only after the complete Omlorix endpoint is ready. */
  function backupServerReady(stack = state.current?.stack || {}) {
    return Boolean(stack.healthy && omlorixServiceRunning(stack));
  }

  /** Format archive sizes without exposing implementation-specific byte counts. */
  function formatBackupBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let amount = bytes;
    let unitIndex = 0;
    while (amount >= 1024 && unitIndex < units.length - 1) {
      amount /= 1024;
      unitIndex += 1;
    }
    return `${new Intl.NumberFormat(document.documentElement.lang || 'en', {
      maximumFractionDigits: unitIndex === 0 ? 0 : 1,
    }).format(amount)} ${units[unitIndex]}`;
  }

  function backupProviderLabel(provider) {
    const normalized = String(provider || '').toLowerCase();
    return {
      s3: 'S3',
      gcs: 'GCS',
      azure: 'Azure',
      webdav: 'WebDAV',
      local: launcherT('launcher_backup_provider_local', 'Local'),
    }[normalized] || String(provider || '');
  }

  /** Populate the enhanced select from the credential-free CLI response. */
  function populateBackupDestinations() {
    if (!els.backupDestinationSelect || !state.backupOptions) return;
    const selectedDestinationId = state.backupDestinationId;
    const options = [
      {
        id: '',
        name: launcherT('launcher_backup_destination_local', 'Local storage (server disk)'),
        provider: '',
      },
      ...(Array.isArray(state.backupOptions.destinations) ? state.backupOptions.destinations : []),
    ];
    els.backupDestinationSelect.replaceChildren();
    options.forEach((destination) => {
      const option = document.createElement('option');
      option.value = destination.id || '';
      option.textContent = destination.id
        ? `${destination.name} (${backupProviderLabel(destination.provider)})`
        : destination.name;
      els.backupDestinationSelect.appendChild(option);
    });
    if (
      selectedDestinationId
      && !options.some((destination) => destination.id === selectedDestinationId)
    ) {
      // Preserve a configured destination that is temporarily unavailable.
      // Passing the ID through makes backup creation fail closed instead of
      // silently moving an unattended recovery artifact to local storage.
      const unavailableOption = document.createElement('option');
      unavailableOption.value = selectedDestinationId;
      unavailableOption.textContent = selectedDestinationId;
      els.backupDestinationSelect.appendChild(unavailableOption);
    }
    els.backupDestinationSelect.value = selectedDestinationId;
    rebuildCustomSelect(els.backupDestinationSelect);
  }

  function renderBackupResult() {
    const result = state.backupLastResult;
    if (!els.backupResult) return;
    els.backupResult.hidden = !result;
    if (!result) return;
    const size = formatBackupBytes(result.payload?.size_bytes);
    const encryption = result.payload?.encryption_enabled === false
      ? launcherT('launcher_backup_plaintext', 'Not encrypted')
      : launcherT('launcher_backup_encrypted', 'Encrypted');
    const details = [result.destinationLabel, size, encryption].filter(Boolean).join(' · ');
    els.backupResultTitle.textContent = launcherT('launcher_backup_result_title', 'Backup created successfully');
    els.backupResultDescription.textContent = details;
    els.backupResultJob.textContent = launcherT(
      'launcher_backup_result_job',
      'Backup job: {jobId}',
      { jobId: result.payload?.job_id || '—' },
    );
  }

  function completedBackupJobs() {
    return (Array.isArray(state.backupJobs) ? state.backupJobs : [])
      .filter((job) => job?.status === 'success' && job.has_artifact);
  }

  /** Render a bounded catalog selector without exposing artifact storage URIs. */
  function renderBackupDownloadPanel() {
    if (!els.backupDownloadControls) return;
    els.backupDownloadTitle.textContent = launcherT(
      'launcher_ui_backup_download_title',
      'Download a completed backup',
    );
    els.backupDownloadDescription.textContent = state.backupJobsError
      ? launcherT(
        'launcher_ui_backup_download_history_failed',
        'Backup history could not be loaded. Check that Omlorix is ready, then refresh the list.',
      )
      : launcherT(
        'launcher_ui_backup_download_description',
        'Choose a successful catalogued backup and save its validated archive to this computer.',
      );
    els.backupDownloadLabel.textContent = launcherT(
      'launcher_ui_backup_download_label',
      'Completed backup',
    );
    els.backupDownloadRefreshButton.textContent = launcherT(
      'launcher_ui_backup_download_refresh',
      'Refresh list',
    );
    els.backupDownloadButton.textContent = state.backupDownloading
      ? launcherT('launcher_ui_backup_downloading', 'Downloading backup…')
      : launcherT('launcher_ui_backup_download_action', 'Download selected backup');
    els.backupDownloadControls.setAttribute(
      'aria-busy',
      state.backupJobsLoading || state.backupDownloading ? 'true' : 'false',
    );

    const previous = els.backupDownloadSelect.value;
    const jobs = completedBackupJobs();
    els.backupDownloadSelect.replaceChildren();
    if (state.backupJobsLoading && !state.backupJobs) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = launcherT(
        'launcher_ui_backup_download_loading_history',
        'Loading backup history…',
      );
      els.backupDownloadSelect.appendChild(option);
    } else if (!jobs.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = launcherT(
        'launcher_ui_backup_download_empty',
        'No completed backups are available',
      );
      els.backupDownloadSelect.appendChild(option);
    } else {
      jobs.forEach((job) => {
        const option = document.createElement('option');
        option.value = job.id;
        const date = new Date(job.finished_at || job.created_at || '');
        const dateLabel = Number.isNaN(date.getTime())
          ? job.id
          : new Intl.DateTimeFormat(document.documentElement.lang || 'en', {
            dateStyle: 'medium',
            timeStyle: 'short',
          }).format(date);
        const encryption = job.encryption_enabled === false
          ? launcherT('launcher_backup_plaintext', 'Not encrypted')
          : launcherT('launcher_backup_encrypted', 'Encrypted');
        const shortId = String(job.id).slice(0, 12);
        option.textContent = [dateLabel, formatBackupBytes(job.size_bytes), encryption, shortId]
          .filter(Boolean)
          .join(' · ');
        els.backupDownloadSelect.appendChild(option);
      });
      els.backupDownloadSelect.value = jobs.some((job) => job.id === previous)
        ? previous
        : jobs[0].id;
    }

    const blocked = state.busy
      || state.backupJobsLoading
      || state.backupDownloading
      || envActionsBlocked()
      || dockerActionsBlocked();
    els.backupDownloadSelect.disabled = blocked || !jobs.length;
    els.backupDownloadButton.disabled = blocked || !els.backupDownloadSelect.value;
    els.backupDownloadRefreshButton.disabled = blocked;
    rebuildCustomSelect(els.backupDownloadSelect);

    const status = state.backupDownloadStatus;
    els.backupDownloadStatus.hidden = !status;
    els.backupDownloadStatus.dataset.state = status?.state || '';
    els.backupDownloadStatus.textContent = status?.message || '';
  }

  async function refreshBackupJobs({ force = false } = {}) {
    if (!backupServerReady()) {
      state.backupJobsRequest += 1;
      state.backupJobs = null;
      state.backupJobsLoading = false;
      state.backupJobsError = false;
      renderBackupDownloadPanel();
      return null;
    }
    if (state.backupJobsLoading || (state.backupJobs && !force)) {
      renderBackupDownloadPanel();
      return state.backupJobs;
    }
    const requestId = state.backupJobsRequest + 1;
    state.backupJobsRequest = requestId;
    state.backupJobsLoading = true;
    state.backupJobsError = false;
    if (force) state.backupJobs = null;
    renderBackupDownloadPanel();
    try {
      const jobs = await window.omlorixServer.getBackupJobs();
      if (requestId !== state.backupJobsRequest || !backupServerReady()) return null;
      state.backupJobs = Array.isArray(jobs) ? jobs : [];
      return state.backupJobs;
    } catch {
      if (requestId !== state.backupJobsRequest) return null;
      state.backupJobs = [];
      state.backupJobsError = true;
      appendConsole(`${launcherT(
        'launcher_ui_backup_download_history_failed',
        'Backup history could not be loaded. Check that Omlorix is ready, then refresh the list.',
      )}\n`);
      return null;
    } finally {
      if (requestId === state.backupJobsRequest) {
        state.backupJobsLoading = false;
        renderBackupDownloadPanel();
      }
    }
  }

  async function downloadSelectedBackup() {
    const jobId = String(els.backupDownloadSelect.value || '').trim();
    if (!jobId || state.backupDownloading) return;
    state.backupDownloading = true;
    state.backupDownloadStatus = null;
    setBusy(true);
    renderBackupDownloadPanel();
    try {
      const result = await window.omlorixServer.downloadBackup(jobId, {
        title: launcherT('launcher_ui_backup_download_picker_title', 'Save Omlorix backup'),
        buttonLabel: launcherT('launcher_ui_backup_download_picker_action', 'Save backup'),
        filterName: launcherT('launcher_restore_filter', 'Omlorix backup archives'),
        allFilesName: launcherT('launcher_restore_all_files', 'All files'),
      });
      if (result?.canceled) return;
      state.backupDownloadStatus = {
        state: 'success',
        message: launcherT(
          'launcher_ui_backup_download_saved',
          'Saved {fileName}.',
          { fileName: result?.fileName || '—' },
        ),
      };
    } catch (error) {
      state.backupDownloadStatus = {
        state: 'error',
        message: translatedErrorMessage(error),
      };
    } finally {
      state.backupDownloading = false;
      setBusy(false);
      renderBackupDownloadPanel();
    }
  }

  /** Render exactly one of stopped, loading, setup-required, error, or ready. */
  function renderBackupPanel() {
    if (!els.backupAvailabilityNotice || !els.backupCreateControls) return;
    els.backupGroupLabel.textContent = launcherT('launcher_backup_group_label', 'Backup & recovery');
    els.backupCreateDescription.textContent = launcherT(
      'launcher_backup_create_desc',
      'Create a full server backup using the destinations configured in Omlorix Admin.',
    );
    els.backupDestinationLabel.textContent = launcherT('launcher_backup_destination_label', 'Destination');
    els.backupEncryptionTitle.textContent = launcherT('launcher_backup_encryption_title', 'Archive encryption');
    els.backupEncryptionDescription.textContent = launcherT(
      'launcher_backup_encryption_desc',
      "Encrypt the backup archive with this server's configured backup passphrase.",
    );
    const backupButtonLabel = els.backupButton?.querySelector('span');
    if (backupButtonLabel) {
      backupButtonLabel.textContent = state.backupCreating
        ? launcherT('launcher_backup_creating_action', 'Creating backup…')
        : launcherT('launcher_backup_create_action', 'Create server backup');
    }
    els.backupCreateControls.setAttribute('aria-busy', state.backupCreating ? 'true' : 'false');

    const ready = backupServerReady();
    const capabilities = state.backupOptions?.capabilities || {};
    const setupRequired = state.backupOptions
      && !capabilities.archive_encryption_available
      && !capabilities.plaintext_archives_allowed;
    let noticeState = '';
    let noticeTitle = '';
    let noticeDescription = '';

    if (!ready) {
      noticeState = 'stopped';
      noticeTitle = launcherT(
        'launcher_backup_unavailable_title',
        'Server backups require Omlorix to be running',
      );
      noticeDescription = launcherT(
        'launcher_backup_unavailable_desc',
        'Start Omlorix and wait until it is ready to load backup destinations.',
      );
    } else if (state.backupOptionsLoading && !state.backupOptions) {
      noticeState = 'loading';
      noticeTitle = launcherT('launcher_backup_loading_title', 'Loading backup destinations…');
      noticeDescription = launcherT(
        'launcher_backup_loading_desc',
        'Reading the destinations and encryption settings configured in Omlorix Admin.',
      );
    } else if (state.backupOptionsError) {
      noticeState = 'error';
      noticeTitle = launcherT('launcher_backup_load_failed_title', 'Backup settings could not be loaded');
      noticeDescription = launcherT(
        'launcher_backup_load_failed_desc',
        'Check that Omlorix is ready, then try loading the backup destinations again.',
      );
    } else if (setupRequired) {
      noticeState = 'error';
      noticeTitle = launcherT('launcher_backup_setup_title', 'Configure backup encryption first');
      noticeDescription = launcherT(
        'launcher_backup_setup_desc',
        'Set a backup passphrase in Launcher Settings, restart Omlorix, and try again.',
      );
    }

    const showNotice = Boolean(noticeState);
    els.backupAvailabilityNotice.hidden = !showNotice;
    els.backupAvailabilityNotice.dataset.state = noticeState;
    els.backupAvailabilityTitle.textContent = noticeTitle;
    els.backupAvailabilityDescription.textContent = noticeDescription;
    els.backupOptionsRetryButton.hidden = noticeState !== 'error' || setupRequired;
    els.backupOptionsRetryButton.textContent = launcherT('launcher_backup_retry_action', 'Try again');
    els.backupCreateControls.hidden = showNotice;

    if (!ready || showNotice) {
      els.backupResult.hidden = true;
      renderBackupDownloadPanel();
      return;
    }

    if (capabilities.archive_encryption_available) {
      els.backupEncryptionEnabled.checked = state.backupEncryptionPreferred;
      els.backupEncryptionEnabled.disabled = state.busy;
      els.backupEncryptionOption.hidden = false;
    } else {
      els.backupEncryptionEnabled.checked = false;
      els.backupEncryptionEnabled.disabled = true;
      els.backupEncryptionOption.hidden = false;
      els.backupEncryptionDescription.textContent = launcherT(
        'launcher_backup_plaintext_only_desc',
        'Encryption is unavailable; this server explicitly allows plaintext backup archives.',
      );
    }
    const formBlocked = state.busy
      || state.backupOptionsLoading
      || envActionsBlocked()
      || dockerActionsBlocked();
    els.backupDestinationSelect.disabled = formBlocked;
    els.backupButton.disabled = formBlocked;
    syncCustomSelect(els.backupDestinationSelect);
    renderBackupResult();
    renderBackupDownloadPanel();
  }

  async function refreshBackupOptions({ force = false } = {}) {
    if (!backupServerReady()) {
      state.backupOptionsRequest += 1;
      state.backupOptions = null;
      state.backupOptionsLoading = false;
      state.backupOptionsError = '';
      renderBackupPanel();
      return null;
    }
    if (state.backupOptionsLoading || (state.backupOptions && !force)) {
      renderBackupPanel();
      return state.backupOptions;
    }

    const requestId = state.backupOptionsRequest + 1;
    state.backupOptionsRequest = requestId;
    state.backupOptionsLoading = true;
    state.backupOptionsError = '';
    if (force) state.backupOptions = null;
    renderBackupPanel();
    try {
      const options = await window.omlorixServer.getBackupOptions();
      if (requestId !== state.backupOptionsRequest || !backupServerReady()) return null;
      state.backupOptions = options;
      state.backupOptionsError = '';
      populateBackupDestinations();
      return options;
    } catch (error) {
      if (requestId !== state.backupOptionsRequest) return null;
      state.backupOptions = null;
      state.backupOptionsError = translatedErrorMessage(error);
      appendConsole(`${launcherT(
        'launcher_backup_load_failed_title',
        'Backup settings could not be loaded',
      )}\n`);
      return null;
    } finally {
      if (requestId === state.backupOptionsRequest) {
        state.backupOptionsLoading = false;
        renderBackupPanel();
      }
    }
  }

  function storageMigrationPayload() {
    const dryRun = Boolean(els.storageMigrationDryRun.checked);
    return {
      fromProvider: els.storageMigrationSource.value,
      toProvider: els.storageMigrationDestination.value,
      scope: els.storageMigrationScope.value,
      dryRun,
      deleteSource: !dryRun && Boolean(els.storageMigrationDeleteSource.checked),
      force: !dryRun && Boolean(els.storageMigrationForce.checked),
      userId: '',
      onlyMigratedFrom: '',
      createdAfter: '',
      createdBefore: '',
      batchSize: 200,
      maxFiles: 0,
      retries: 3,
    };
  }

  function storageMigrationValidationMessage(payload = storageMigrationPayload()) {
    if (payload.fromProvider === payload.toProvider) {
      return launcherT(
        'launcher_ui_storage_providers_must_differ',
        'Source and destination storage providers must be different.',
      );
    }
    return '';
  }

  function renderStorageMigrationPanel() {
    if (!els.storageMigrationControls) return;
    if (!state.storageFormInitialized && state.current?.env) {
      const activeProvider = String(state.current.env.FILE_STORAGE_PROVIDER || 'local').toLowerCase();
      els.storageMigrationDestination.value = activeProvider;
      els.storageMigrationSource.value = activeProvider === 'local' ? 's3' : 'local';
      state.storageFormInitialized = true;
      syncCustomSelect(els.storageMigrationSource);
      syncCustomSelect(els.storageMigrationDestination);
    }

    const ready = backupServerReady();
    // Storage providers and migration options depend on the live backend. Keep
    // the form out of view while Omlorix is stopped so the availability notice
    // is the only actionable content presented to the operator.
    els.storageMigrationControls.hidden = !ready;
    const payload = storageMigrationPayload();
    const validation = storageMigrationValidationMessage(payload);
    els.storageMigrationValidation.textContent = validation;
    els.storageMigrateButton.textContent = payload.dryRun
      ? launcherT('launcher_ui_storage_preview_action', 'Preview migration')
      : launcherT('launcher_ui_storage_migrate_action', 'Run migration');

    const blocked = state.busy || envActionsBlocked() || dockerActionsBlocked() || !ready;
    for (const control of els.storageMigrationControls.querySelectorAll('input, select, button')) {
      control.disabled = blocked;
    }
    els.storageMigrationDeleteSource.disabled = blocked || payload.dryRun;
    els.storageMigrationForce.disabled = blocked || payload.dryRun;
    els.storageMigrateButton.disabled = blocked || Boolean(validation);
    for (const select of [
      els.storageMigrationSource,
      els.storageMigrationDestination,
      els.storageMigrationScope,
    ]) syncCustomSelect(select);

    const result = state.storageLastResult;
    els.storageMigrationNotice.hidden = ready && !result;
    if (!ready) {
      els.storageMigrationNotice.hidden = false;
      els.storageMigrationNoticeTitle.textContent = launcherT(
        'launcher_ui_storage_requires_running_title',
        'Storage operations require Omlorix to be running',
      );
      els.storageMigrationNoticeDescription.textContent = launcherT(
        'launcher_ui_storage_requires_running_desc',
        'Start Omlorix and wait until it is ready before probing or migrating file storage.',
      );
      return;
    }
    if (!result) return;

    els.storageMigrationNotice.hidden = false;
    if (result.kind === 'probe') {
      els.storageMigrationNoticeTitle.textContent = launcherT(
        'launcher_ui_storage_probe_success',
        'Storage probe succeeded',
      );
      els.storageMigrationNoticeDescription.textContent = launcherT(
        'launcher_ui_storage_probe_success_desc',
        'The configured {provider} provider passed upload, download, verification, and cleanup.',
        { provider: backupProviderLabel(result.payload?.provider) },
      );
      return;
    }
    const migration = result.payload || {};
    if (migration.dry_run) {
      els.storageMigrationNoticeTitle.textContent = launcherT(
        'launcher_ui_storage_preview_complete',
        'Migration preview complete',
      );
      els.storageMigrationNoticeDescription.textContent = launcherT(
        'launcher_ui_storage_preview_summary',
        '{wouldMigrate} of {scanned} matching records would migrate.',
        { wouldMigrate: migration.would_migrate || 0, scanned: migration.scanned || 0 },
      );
    } else {
      els.storageMigrationNoticeTitle.textContent = launcherT(
        'launcher_ui_storage_migration_complete',
        'Storage migration complete',
      );
      els.storageMigrationNoticeDescription.textContent = launcherT(
        'launcher_ui_storage_migration_summary',
        '{migrated} of {scanned} records migrated; {failed} failed.',
        { migrated: migration.migrated || 0, scanned: migration.scanned || 0, failed: migration.failed || 0 },
      );
    }
  }

  async function runStorageProbe() {
    state.storageLastResult = null;
    setBusy(true);
    appendConsole(`\n> ${launcherT('launcher_ui_storage_probe_running', 'Probing configured storage')}`);
    try {
      const result = await window.omlorixServer.probeStorage();
      state.storageLastResult = { kind: 'probe', payload: result?.probe || {} };
      if (result?.state) renderState(result.state);
    } catch (error) {
      appendConsole(`${launcherT('launcher_ui_storage_probe_failed', 'Storage probe failed: {error}', { error: translatedErrorMessage(error) })}\n`);
      await refresh();
    } finally {
      setBusy(false);
      renderStorageMigrationPanel();
    }
  }

  async function runStorageMigration() {
    const payload = storageMigrationPayload();
    const validation = storageMigrationValidationMessage(payload);
    if (validation) {
      renderStorageMigrationPanel();
      return;
    }
    if (!payload.dryRun) {
      const cleanup = payload.deleteSource
        ? launcherT('launcher_ui_storage_cleanup_delete', 'Verified source objects will be deleted.')
        : launcherT('launcher_ui_storage_cleanup_retain', 'Source objects will be retained.');
      const overwrite = payload.force
        ? launcherT('launcher_ui_storage_force_warning', 'Conflicting destination objects may be overwritten.')
        : '';
      const confirmed = await showLauncherDialog({
        title: launcherT('launcher_ui_storage_confirm_title', 'Run storage migration?'),
        message: launcherT(
          'launcher_ui_storage_confirm_message',
          'Migrate {scope} from {source} to {destination}. {cleanup} {overwrite}',
          {
            scope: payload.scope,
            source: backupProviderLabel(payload.fromProvider),
            destination: backupProviderLabel(payload.toProvider),
            cleanup,
            overwrite,
          },
        ).trim(),
        confirmText: launcherT('launcher_ui_storage_migrate_action', 'Run migration'),
      });
      if (!confirmed) return;
    }

    state.storageLastResult = null;
    setBusy(true);
    const action = payload.dryRun
      ? launcherT('launcher_ui_storage_preview_running', 'Previewing storage migration')
      : launcherT('launcher_ui_storage_migration_running', 'Migrating file storage');
    appendConsole(`\n> ${action}`);
    try {
      const result = await window.omlorixServer.migrateStorage(payload);
      state.storageLastResult = { kind: 'migration', payload: result?.migration || {} };
      if (result?.state) renderState(result.state);
    } catch (error) {
      appendConsole(`${launcherT('launcher_ui_storage_migration_failed', 'Storage migration failed: {error}', { error: translatedErrorMessage(error) })}\n`);
      await refresh();
    } finally {
      setBusy(false);
      renderStorageMigrationPanel();
    }
  }

  function dockerActionBlockedMessage(docker = state.current?.docker || {}) {
    if (!docker.installed) {
      return 'Docker is not installed. Install Docker from the setup card before using Omlorix actions.';
    }
    if (!docker.running) {
      return 'Docker is not running. Start Docker before using Omlorix actions.';
    }
    if (!docker.compose) {
      return 'Docker Compose is missing. Repair the Compose plugin before using Omlorix actions.';
    }
    return 'Docker is not ready. Start Docker before using Omlorix actions.';
  }

  function autoUpdateRunNowBlockedReason({ isBusy, envBlocked, dockerBlocked, omlorixBlocked } = {}) {
    if (isBusy) {
      return 'Unavailable while another launcher action is running.';
    }
    if (envBlocked) {
      return hasDirtyEnvEditor()
        ? 'Save or discard your .env changes before running an update.'
        : 'Complete the required .env values before running an update.';
    }
    if (dockerBlocked) {
      return dockerActionBlockedMessage().replace(' before using Omlorix actions.', ' before running an update.');
    }
    if (omlorixBlocked) {
      return 'Start Omlorix before running an update.';
    }
    if (state.autoUpdates?.status?.state === 'running') {
      return 'An automatic update check is already running.';
    }
    return '';
  }

  function setMetricState(metric, lamp, kind, active = false) {
    metric.className = `hstat metric metric-status-${kind}${active ? ' metric-status-active' : ''}`;
    lamp.className = 'dot status-lamp';
  }

  /**
   * Translate a launcher-authored console message without treating the visual
   * `> ` operation marker as part of the sentence.
   *
   * Complete messages that include the marker still get first priority. The
   * second pass is needed for manager errors: their structured text is
   * cataloged, but the renderer adds the marker only after receiving the IPC
   * payload. Raw Docker output never reaches this helper.
   */
  function translateLauncherConsoleMessage(message) {
    const translated = translateLauncherSource(message);
    if (translated !== message || !message.startsWith('> ')) return translated;
    return `> ${translateLauncherSource(message.slice(2))}`;
  }

  /** Translate a known launcher-manager error while preserving OS/tool text. */
  function translatedErrorMessage(error) {
    const wrappedMessage = String(error?.message || error || '');
    const ipcEnvelope = /^Error invoking remote method ['"][^'"]+['"]:\s*/i;
    const message = ipcEnvelope.test(wrappedMessage)
      ? wrappedMessage.replace(ipcEnvelope, '').replace(/^Error:\s*/i, '')
      : wrappedMessage;
    return message.startsWith('launcher_ui_')
      ? launcherT(message, message)
      : translateLauncherSource(message);
  }

  function writeConsoleOutput(text, separate = false) {
    window.OmlorixTerminalOutput.append(els.consoleOutput, text, {
      separate,
      maxLines: 10000,
      maxCharacters: 2000000,
    });
  }

  function flushConsoleStream() {
    if (state.consoleStreamTimer !== null) {
      window.clearTimeout(state.consoleStreamTimer);
      state.consoleStreamTimer = null;
    }
    if (!state.consoleStreamBuffer) return;
    const buffered = state.consoleStreamBuffer;
    state.consoleStreamBuffer = '';
    writeConsoleOutput(buffered, false);
  }

  /** Batch high-volume process chunks while preserving their event order. */
  function queueConsoleStream(text) {
    state.consoleStreamBuffer += String(text || '');
    if (state.consoleStreamBuffer.length >= 256 * 1024) {
      flushConsoleStream();
      return;
    }
    if (state.consoleStreamTimer === null) {
      state.consoleStreamTimer = window.setTimeout(flushConsoleStream, 40);
    }
  }

  function appendConsole(text, { preserveStream = false } = {}) {
    const source = String(text || '');
    if (preserveStream) {
      if (source) queueConsoleStream(source);
      return;
    }
    // Launcher-authored messages are ordering barriers: render every earlier
    // process chunk before adding the translated summary or lifecycle marker.
    flushConsoleStream();
    const normalized = source.trim();
    let raw = source;
    if (normalized) {
      const leading = source.match(/^\s*/)?.[0] || '';
      const trailing = source.match(/\s*$/)?.[0] || '';
      raw = `${leading}${translateLauncherConsoleMessage(normalized)}${trailing}`;
    }
    if (!raw) return;
    writeConsoleOutput(raw, true);
  }

  function setLogControlStatus(message = '', level = '') {
    els.logControlStatus.textContent = message;
    if (level) {
      els.logControlStatus.dataset.level = level;
    } else {
      delete els.logControlStatus.dataset.level;
    }
  }

  function logDiagnosticsActive() {
    return state.logLoading
      || state.logFollowStarting
      || state.logFollowStopping
      || Boolean(state.logFollowSessionId);
  }

  /** Keep log reads independent from mutating Launcher operations. */
  function renderLogControls() {
    const dockerBlocked = dockerActionsBlocked();
    const followActive = state.logFollowStarting
      || state.logFollowStopping
      || Boolean(state.logFollowSessionId);
    const optionsLocked = followActive || state.logLoading;
    els.logServiceSelect.disabled = optionsLocked;
    els.logLinesInput.disabled = optionsLocked;
    els.logSinceInput.disabled = optionsLocked;
    els.loadLogsButton.disabled = dockerBlocked || optionsLocked;
    els.startLogFollowButton.disabled = dockerBlocked || optionsLocked;
    els.stopLogFollowButton.disabled = !state.logFollowSessionId || state.logFollowStopping;
    els.startLogFollowButton.setAttribute('aria-pressed', followActive ? 'true' : 'false');
    for (const button of els.servicesBody.querySelectorAll('[data-service-action="logs"]')) {
      button.disabled = dockerBlocked || logDiagnosticsActive();
    }
  }

  function selectedLogOptions() {
    els.logLinesInput.removeAttribute('aria-invalid');
    els.logSinceInput.removeAttribute('aria-invalid');
    const lines = Number(els.logLinesInput.value);
    if (!Number.isInteger(lines) || lines < 1 || lines > 5000) {
      els.logLinesInput.setAttribute('aria-invalid', 'true');
      setLogControlStatus(launcherT(
        'launcher_ui_log_lines_error',
        'Choose a line count from 1 to 5000.',
      ), 'error');
      els.logLinesInput.focus();
      return null;
    }
    return {
      service: els.logServiceSelect.value,
      lines,
      since: els.logSinceInput.value.trim(),
    };
  }

  function renderLogServiceOptions(rows = []) {
    const selected = els.logServiceSelect.value;
    const names = [...new Set(rows
      .map((service) => String(service.Service || service.Name || service.Names || '').trim())
      .filter(Boolean))];
    els.logServiceSelect.replaceChildren();
    const aggregateOption = document.createElement('option');
    aggregateOption.value = '';
    aggregateOption.textContent = launcherT('launcher_ui_log_all_services', 'All services');
    els.logServiceSelect.appendChild(aggregateOption);
    for (const name of names) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      els.logServiceSelect.appendChild(option);
    }
    if (selected && !names.includes(selected)) {
      const unavailableOption = document.createElement('option');
      unavailableOption.value = selected;
      unavailableOption.textContent = selected;
      els.logServiceSelect.appendChild(unavailableOption);
    }
    els.logServiceSelect.value = selected;
    rebuildCustomSelect(els.logServiceSelect);
    renderLogControls();
  }

  function openConsoleForService(serviceName) {
    els.logServiceSelect.value = serviceName;
    syncCustomSelect(els.logServiceSelect);
    const consoleNav = els.navLinks.find((link) => link.dataset.section === 'console');
    consoleNav?.click();
  }

  async function loadLogSnapshot(serviceName = null) {
    if (dockerActionsBlocked() || logDiagnosticsActive()) {
      if (dockerActionsBlocked()) {
        setLogControlStatus(launcherT(
          'launcher_ui_loading_logs_blocked_value1',
          'Loading logs blocked: {value1}',
          { value1: dockerActionBlockedMessage() },
        ), 'error');
      }
      return;
    }
    if (serviceName !== null) openConsoleForService(serviceName);
    const options = selectedLogOptions();
    if (!options) return;
    state.logLoading = true;
    setLogControlStatus(launcherT('launcher_ui_log_loading_snapshot', 'Loading log snapshot'));
    renderLogControls();
    appendConsole(launcherT(
      'launcher_ui_log_snapshot_heading',
      '\n> Log snapshot: {scope}\n',
      { scope: options.service || launcherT('launcher_ui_log_all_services', 'All services') },
    ));
    try {
      const logs = await window.omlorixServer.logs(options);
      appendConsole(logs, { preserveStream: true });
      setLogControlStatus(launcherT('launcher_ui_log_snapshot_loaded', 'Log snapshot loaded.'));
    } catch (error) {
      const message = translatedErrorMessage(error);
      if (/valid log time bound/i.test(String(error?.message || ''))) {
        els.logSinceInput.setAttribute('aria-invalid', 'true');
      }
      setLogControlStatus(message, 'error');
      appendConsole(launcherT('launcher_ui_logs_failed_value1', 'Logs failed: {value1}', { value1: message }));
    } finally {
      state.logLoading = false;
      renderLogControls();
    }
  }

  async function startLogFollow() {
    if (dockerActionsBlocked() || logDiagnosticsActive()) return;
    const options = selectedLogOptions();
    if (!options) return;
    state.logFollowStarting = true;
    state.logFollowEndedSessionId = '';
    setLogControlStatus(launcherT('launcher_ui_log_starting_follow', 'Starting log following'));
    renderLogControls();
    appendConsole(launcherT(
      'launcher_ui_log_follow_heading',
      '\n> Following logs: {scope}\n',
      { scope: options.service || launcherT('launcher_ui_log_all_services', 'All services') },
    ));
    try {
      const session = await window.omlorixServer.startLogFollow(options);
      if (state.logFollowEndedSessionId !== session.sessionId) {
        state.logFollowSessionId = session.sessionId;
        setLogControlStatus(launcherT('launcher_ui_log_following', 'Following logs.'));
      }
    } catch (error) {
      const message = translatedErrorMessage(error);
      if (/valid log time bound/i.test(String(error?.message || ''))) {
        els.logSinceInput.setAttribute('aria-invalid', 'true');
      }
      setLogControlStatus(message, 'error');
      appendConsole(launcherT('launcher_ui_log_follow_failed', 'Log following failed: {error}', { error: message }));
    } finally {
      state.logFollowStarting = false;
      renderLogControls();
    }
  }

  async function stopLogFollow() {
    if (state.logFollowStopping || (!state.logFollowSessionId && !state.logFollowStarting)) return;
    state.logFollowStopping = true;
    setLogControlStatus(launcherT('launcher_ui_log_stopping_follow', 'Stopping log following'));
    renderLogControls();
    try {
      await window.omlorixServer.stopLogFollow(state.logFollowSessionId);
    } catch (error) {
      const message = translatedErrorMessage(error);
      setLogControlStatus(message, 'error');
      appendConsole(launcherT('launcher_ui_log_stop_failed', 'Could not stop log following: {error}', { error: message }));
    } finally {
      state.logFollowStopping = false;
      renderLogControls();
    }
  }

  /**
   * Keep the refresh icon visually honest when multiple refresh sources overlap.
   * Menu refreshes, polling, and button clicks can all call refresh(), so this
   * counter prevents the spinner from stopping until the last request finishes.
   */
  function setRefreshButtonRefreshing(isRefreshing) {
    state.refreshRequests = Math.max(0, state.refreshRequests + (isRefreshing ? 1 : -1));
    const refreshing = state.refreshRequests > 0;
    els.refreshButton.classList.toggle('is-refreshing', refreshing);
    els.refreshButton.setAttribute('aria-busy', refreshing ? 'true' : 'false');
  }

  function setBusy(isBusy) {
    const nextBusy = Boolean(isBusy);
    const pollingCadenceChanged = state.busy !== nextBusy;
    state.busy = nextBusy;
    const dockerPolling = state.dockerReadinessPoll.active;
    const envBlocked = envActionsBlocked();
    const dockerBlocked = dockerActionsBlocked();
    const omlorixBlocked = omlorixActionsBlocked();
    const dockerGuardedButtons = [
      els.startButton,
      els.stopButton,
      els.restartButton,
      els.proxyFixVisitorIpsButton,
      els.verifyBackupButton,
    ];
    const omlorixGuardedButtons = [
      els.openButton,
      els.updateButton,
      els.backupButton,
      els.restoreButton,
      els.autoUpdateRunNowButton,
    ];
    const unguardedButtons = [
      els.openDockerSetupButton,
      els.refreshButton,
      els.exportEnvButton,
      els.envRequirementsSetupButton,
      els.fixVisitorIpsButton,
      els.importEnvButton,
      els.applyEnvImportButton,
      els.cancelEnvImportButton,
      els.proxyStartButton,
      els.proxyStopButton,
      els.proxyRestartButton,
      els.proxyInstallServiceButton,
      els.proxyUninstallServiceButton,
      els.proxyTlsCertChooseButton,
      els.proxyTlsKeyChooseButton,
      els.proxyTlsCaChooseButton,
    ];
    for (const button of dockerGuardedButtons) {
      button.disabled = nextBusy || envBlocked || dockerBlocked;
    }
    for (const button of els.servicesBody.querySelectorAll('[data-service-action]')) {
      const logAction = button.dataset.serviceAction === 'logs';
      button.disabled = dockerBlocked
        || (logAction ? logDiagnosticsActive() : nextBusy || envBlocked);
    }
    for (const button of omlorixGuardedButtons) {
      button.disabled = nextBusy || envBlocked || dockerBlocked || omlorixBlocked;
    }
    if (els.serverUpdateButton) {
      // A launcher update remains available even when Docker or Omlorix is
      // stopped. Installing a server update keeps the same health guards as the
      // existing maintenance action.
      const requiresLauncher = Boolean(state.serverUpdateInfo?.launcherRequirement);
      els.serverUpdateButton.disabled = requiresLauncher
        ? nextBusy
        : nextBusy || envBlocked || dockerBlocked || omlorixBlocked;
    }
    if (els.launcherUpdateButton) {
      // Launcher installation is independent of Docker and server health. The
      // native updater still asks for approval before downloading or relaunching.
      els.launcherUpdateButton.disabled = nextBusy;
    }
    for (const button of unguardedButtons) {
      if (!button) continue;
      button.disabled = nextBusy;
    }
    if (els.replaceMissingEnvInput) {
      // Replacement mode is only safe when the backend supplied the matching
      // impact projection. A stale checked control must never authorize a mode
      // that the operator could not review.
      els.replaceMissingEnvInput.disabled = nextBusy || !state.envImportPreview?.replacement;
    }
    if (els.proxyInstallServiceButton) {
      els.proxyInstallServiceButton.disabled = nextBusy
        || !state.current?.proxy?.serviceAvailable
        || !state.current?.proxy?.config?.enabled;
    }
    els.startDockerDesktopButton.disabled = nextBusy || dockerPolling;
    if (els.proxyStartButton) {
      els.proxyStartButton.disabled = nextBusy || !els.proxyEnabledInput.checked;
      els.proxyRestartButton.disabled = nextBusy || !els.proxyEnabledInput.checked || !state.current?.proxy?.running;
      els.proxyStopButton.disabled = nextBusy || !state.current?.proxy?.running;
    }
    const autoUpdateRunNowReason = autoUpdateRunNowBlockedReason({
      isBusy: nextBusy,
      envBlocked,
      dockerBlocked,
      omlorixBlocked,
    });
    els.autoUpdateRunNowButton.disabled = Boolean(autoUpdateRunNowReason);
    els.autoUpdateRunNowNote.textContent = autoUpdateRunNowReason;
    els.autoUpdateRunNowNote.hidden = !autoUpdateRunNowReason;
    if (!nextBusy && state.envImportPreview) {
      const preview = selectedEnvImportPreview();
      els.applyEnvImportButton.disabled = Object.keys(preview?.validationErrors || {}).length > 0;
    }
    renderBackupPanel();
    renderStorageMigrationPanel();
    renderLogControls();
    renderServiceStatusRefreshCadence();
    if (pollingCadenceChanged) {
      // Show container transitions promptly at both edges of an action, then
      // continue with the cadence appropriate for the new busy state.
      scheduleServiceStatusRefresh({ refreshNow: true });
    }
  }

  function envActionsBlocked() {
    return hasDirtyEnvEditor() || (state.current ? state.current.envRequirements?.ok === false : false);
  }

  function stopDockerReadinessPolling() {
    if (state.dockerReadinessPoll.timer) {
      window.clearTimeout(state.dockerReadinessPoll.timer);
    }
    state.dockerReadinessPoll.timer = null;
    state.dockerReadinessPoll.deadline = 0;
    state.dockerReadinessPoll.inFlight = false;
    state.dockerReadinessPoll.active = false;
    state.dockerReadinessPoll.mode = 'start';
    state.dockerReadinessPoll.intervalMs = DOCKER_READINESS_POLL_INTERVAL_MS;
    setBusy(state.busy);
  }

  async function pollDockerReadiness() {
    if (!state.dockerReadinessPoll.active || state.dockerReadinessPoll.inFlight) return;
    state.dockerReadinessPoll.inFlight = true;

    try {
      const data = await refresh();
      if (dockerIsReady(data?.docker)) {
        appendConsole('Docker is ready. The launcher refreshed automatically.');
        stopDockerReadinessPolling();
        return;
      }
    } finally {
      state.dockerReadinessPoll.inFlight = false;
    }

    const remainingMs = state.dockerReadinessPoll.deadline - Date.now();
    if (remainingMs <= 0) {
      appendConsole(state.dockerReadinessPoll.mode === 'install'
        ? 'Docker did not report ready before the installer watch timed out. The installer may still be open; use the manual Docker setup guide if needed.'
        : 'Docker did not report ready within one minute. Returning to the normal Docker start controls.');
      stopDockerReadinessPolling();
      await refresh();
      return;
    }

    // Keep exactly one scheduled check alive so slow Docker status calls cannot pile up.
    state.dockerReadinessPoll.timer = window.setTimeout(
      pollDockerReadiness,
      Math.min(state.dockerReadinessPoll.intervalMs, remainingMs)
    );
  }

  function startDockerReadinessPolling(options = {}) {
    const mode = options.mode === 'install' ? 'install' : 'start';
    const timeoutMs = Number(options.timeoutMs || (mode === 'install' ? DOCKER_INSTALL_POLL_TIMEOUT_MS : DOCKER_READINESS_POLL_TIMEOUT_MS));
    const intervalMs = Number(options.intervalMs || (mode === 'install' ? DOCKER_INSTALL_POLL_INTERVAL_MS : DOCKER_READINESS_POLL_INTERVAL_MS));
    stopDockerReadinessPolling();
    state.dockerReadinessPoll.active = true;
    state.dockerReadinessPoll.mode = mode;
    state.dockerReadinessPoll.intervalMs = intervalMs;
    state.dockerReadinessPoll.deadline = Date.now() + timeoutMs;
    setBusy(state.busy);
    appendConsole(options.message || (mode === 'install'
      ? 'Watching Docker installation status every few seconds. This can take several minutes.'
      : 'Watching Docker status every second for up to one minute.'));
    pollDockerReadiness();
  }

  function getTogglesFromInputs() {
    const toggles = {};
    for (const input of els.toggleInputs) {
      toggles[input.dataset.toggle] = input.checked;
    }
    return toggles;
  }

  /**
   * Collapse the two persisted Redis booleans into the three meaningful modes
   * presented by the settings UI. An inactive Redis configuration never needs
   * to expose a bundled/external sub-choice.
   */
  function redisModeFromInputs(toggles = getTogglesFromInputs()) {
    if (!els.redisEnabledInput.checked) return 'off';
    return toggles.useBundledRedis ? 'bundled' : 'external';
  }

  /** Keep the Redis selector and its conditional connection fields in sync. */
  function syncRedisModeControls(toggles = getTogglesFromInputs()) {
    const mode = redisModeFromInputs(toggles);
    for (const input of els.redisModeInputs) {
      input.checked = input.dataset.redisMode === mode;
    }
    for (const panel of els.redisModePanels) {
      panel.hidden = panel.dataset.redisModePanel !== mode;
    }
  }

  /** Return the explicit storage mode represented by the launcher controls. */
  function storageModeFromInputs(toggles = getTogglesFromInputs()) {
    if (toggles.useBundledStorage) return 'bundled';
    return els.fileStorageModeInput?.value === 'external' ? 'external' : 'local';
  }

  /** Keep the three storage choices and their mode-specific fields in sync. */
  function syncStorageModeControls(toggles = getTogglesFromInputs()) {
    const mode = storageModeFromInputs(toggles);
    if (els.fileStorageModeInput) els.fileStorageModeInput.value = mode;
    for (const input of els.storageModeInputs) {
      input.checked = input.dataset.storageMode === mode;
    }
    for (const panel of els.storageModePanels) {
      panel.hidden = panel.dataset.storageModePanel !== mode;
    }
  }

  function syncConnectionModeControls(toggles = getTogglesFromInputs()) {
    for (const input of els.connectionModeInputs) {
      const toggleKey = input.dataset.connectionToggle;
      const expected = input.dataset.connectionValue === 'true';
      input.checked = Boolean(toggles[toggleKey]) === expected;
    }
    for (const element of els.connectionModeNotes) {
      const toggleKey = element.dataset.modeNote;
      const expected = element.dataset.modeValue === 'true';
      element.hidden = Boolean(toggles[toggleKey]) !== expected;
    }
    for (const element of els.infrastructureModeElements) {
      const toggleKey = element.dataset.visibleWhenToggle;
      const expected = element.dataset.visibleWhenValue === 'true';
      element.hidden = Boolean(toggles[toggleKey]) !== expected;
    }
    syncRedisModeControls(toggles);
    syncStorageModeControls(toggles);
    syncFileStorageProviderPanels();
  }

  function syncFileStorageProviderPanels() {
    const provider = String(els.fileStorageProviderSelect?.value || 'local').trim() || 'local';
    for (const panel of els.fileStorageProviderPanels) {
      panel.hidden = panel.dataset.storageProviderPanel !== provider;
    }
  }

  function setChecked(element, value, defaultValue = false) {
    if (!element) return;
    const normalized = String(value ?? '').trim().toLowerCase();
    element.checked = normalized ? normalized !== 'false' : Boolean(defaultValue);
  }

  function setToggles(toggles) {
    const normalizedToggles = { ...toggles };
    if (normalizedToggles.useBundledDB === false) {
      normalizedToggles.usePgbouncer = false;
    }
    for (const input of els.toggleInputs) {
      const key = input.dataset.toggle;
      if (Object.prototype.hasOwnProperty.call(normalizedToggles, key)) {
        input.checked = Boolean(normalizedToggles[key]);
      }
    }
    syncConnectionModeControls();
    renderEnvEditor();
  }

  function hydrateForm(data) {
    const env = data.env || {};
    els.composeProjectNameInput.value = env.COMPOSE_PROJECT_NAME || 'omlorix';
    els.modeSelect.value = env.MODE || 'production';
    syncCustomSelect(els.modeSelect);
    els.updateChannelSelect.value = data.serverSettings?.updateChannel || 'stable';
    syncCustomSelect(els.updateChannelSelect);
    renderVersionOptions(env.OMLORIX_VERSION || els.updateChannelSelect.value || 'stable');
    els.jwtSecretKeyInput.value = env.JWT_SECRET_KEY || '';
    els.encryptionKeyInput.value = env.ENCRYPTION_KEY || '';
    els.passwordResetSaltInput.value = env.PASSWORD_RESET_IDENTIFIER_HASH_SALT || '';
    els.databaseNameInput.value = env.DATABASE_NAME || '';
    els.databaseUserInput.value = env.DATABASE_USER || '';
    els.databasePasswordInput.value = env.DATABASE_PASSWORD || '';
    els.databaseHostInput.value = env.DATABASE_HOST || 'localhost';
    els.databasePortInput.value = env.DATABASE_PORT || '5432';
    els.databaseSchemaInput.value = env.DATABASE_SCHEMA || 'app';
    els.databaseAuditLogSchemaInput.value = env.DATABASE_AUDIT_LOG_SCHEMA || 'audit';
    els.databaseLogsSchemaInput.value = env.DATABASE_LOGS_SCHEMA || 'logs';
    setChecked(els.autoCreateDatabasesInput, env.OMLORIX_AUTO_CREATE_DATABASES, true);
    els.databaseHostOverrideInput.value = env.DATABASE_HOST_OVERRIDE || '';
    els.databasePortOverrideInput.value = env.DATABASE_PORT_OVERRIDE || '';
    els.devDatabaseHostPortInput.value = env.DEV_DATABASE_HOST_PORT || '5432';
    els.databaseUrlInput.value = env.DATABASE_URL || '';
    setChecked(els.redisEnabledInput, env.REDIS_ENABLED, true);
    els.redisPasswordInput.value = env.REDIS_PASSWORD || '';
    els.redisUrlInput.value = env.REDIS_URL || '';
    els.devRedisHostPortInput.value = env.DEV_REDIS_HOST_PORT || '6379';
    els.pgbouncerPoolModeSelect.value = env.PGBOUNCER_POOL_MODE || 'transaction';
    syncCustomSelect(els.pgbouncerPoolModeSelect);
    els.pgbouncerMaxClientConnInput.value = env.PGBOUNCER_MAX_CLIENT_CONN || '200';
    els.pgbouncerDefaultPoolSizeInput.value = env.PGBOUNCER_DEFAULT_POOL_SIZE || '40';
    els.pgbouncerReservePoolSizeInput.value = env.PGBOUNCER_RESERVE_POOL_SIZE || '10';
    els.pgbouncerHostBindInput.value = env.PGBOUNCER_HOST_BIND || '127.0.0.1';
    els.pgbouncerHostPortInput.value = env.PGBOUNCER_HOST_PORT || '6432';
    els.minioRootUserInput.value = env.MINIO_ROOT_USER || '';
    els.minioRootPasswordInput.value = env.MINIO_ROOT_PASSWORD || '';
    els.minioApiHostBindInput.value = env.MINIO_API_HOST_BIND || '127.0.0.1';
    els.minioApiHostPortInput.value = env.MINIO_API_HOST_PORT || '9000';
    els.minioConsoleHostBindInput.value = env.MINIO_CONSOLE_HOST_BIND || '127.0.0.1';
    els.minioConsoleHostPortInput.value = env.MINIO_CONSOLE_HOST_PORT || '9001';
    const bundledStorage = env.OMLORIX_USE_BUNDLED_STORAGE === 'true';
    const savedStorageProvider = ['local', 's3', 'gcs', 'azure', 'webdav']
      .includes(String(env.FILE_STORAGE_PROVIDER || '').trim().toLowerCase())
      ? String(env.FILE_STORAGE_PROVIDER).trim().toLowerCase()
      : 'local';
    const storageMode = bundledStorage
      ? 'bundled'
      : savedStorageProvider === 'local' ? 'local' : 'external';
    els.fileStorageModeInput.value = storageMode;
    // Local and bundled modes do not discard the operator's last external
    // provider selection during an autosave refresh.
    if (storageMode === 'external') {
      els.fileStorageProviderSelect.value = savedStorageProvider;
    } else if (!els.fileStorageProviderSelect.value) {
      els.fileStorageProviderSelect.value = 's3';
    }
    syncCustomSelect(els.fileStorageProviderSelect);
    els.fileStorageLocalBasePathInput.value = env.FILE_STORAGE_LOCAL_BASE_PATH || '';
    const savedS3Bucket = env.FILE_STORAGE_S3_BUCKET || '';
    if (storageMode === 'bundled' || !els.fileStorageS3BucketInput.value) {
      els.fileStorageS3BucketInput.value = savedS3Bucket;
    }
    if ((storageMode === 'external' && savedStorageProvider === 's3')
      || !els.fileStorageS3ExternalBucketInput.value) {
      els.fileStorageS3ExternalBucketInput.value = savedS3Bucket;
    }
    els.fileStorageS3PrefixInput.value = env.FILE_STORAGE_S3_PREFIX || '';
    els.fileStorageS3RegionInput.value = env.FILE_STORAGE_S3_REGION || '';
    els.fileStorageS3EndpointUrlInput.value = env.FILE_STORAGE_S3_ENDPOINT_URL || '';
    els.fileStorageS3AccessKeyIdInput.value = env.FILE_STORAGE_S3_ACCESS_KEY_ID || '';
    els.fileStorageS3SecretAccessKeyInput.value = env.FILE_STORAGE_S3_SECRET_ACCESS_KEY || '';
    els.fileStorageS3SessionTokenInput.value = env.FILE_STORAGE_S3_SESSION_TOKEN || '';
    els.fileStorageGcsBucketInput.value = env.FILE_STORAGE_GCS_BUCKET || '';
    els.fileStorageGcsPrefixInput.value = env.FILE_STORAGE_GCS_PREFIX || '';
    els.fileStorageGcsProjectInput.value = env.FILE_STORAGE_GCS_PROJECT || '';
    els.fileStorageGcsCredentialsJsonInput.value = env.FILE_STORAGE_GCS_CREDENTIALS_JSON || '';
    els.fileStorageAzureContainerInput.value = env.FILE_STORAGE_AZURE_CONTAINER || '';
    els.fileStorageAzurePrefixInput.value = env.FILE_STORAGE_AZURE_PREFIX || '';
    els.fileStorageAzureConnectionStringInput.value = env.FILE_STORAGE_AZURE_CONNECTION_STRING || '';
    els.fileStorageAzureAccountUrlInput.value = env.FILE_STORAGE_AZURE_ACCOUNT_URL || '';
    els.fileStorageAzureCredentialInput.value = env.FILE_STORAGE_AZURE_CREDENTIAL || '';
    els.fileStorageWebdavUrlInput.value = env.FILE_STORAGE_WEBDAV_URL || '';
    els.fileStorageWebdavUsernameInput.value = env.FILE_STORAGE_WEBDAV_USERNAME || '';
    els.fileStorageWebdavPasswordInput.value = env.FILE_STORAGE_WEBDAV_PASSWORD || '';
    els.fileStorageWebdavPrefixInput.value = env.FILE_STORAGE_WEBDAV_PREFIX || '';
    els.fileStorageWebdavVerifySslInput.checked = env.FILE_STORAGE_WEBDAV_VERIFY_SSL !== 'false';
    els.fileStorageWebdavTimeoutInput.value = env.FILE_STORAGE_WEBDAV_TIMEOUT || '30';
    els.otelServiceNameInput.value = env.OTEL_SERVICE_NAME || 'omlorix-backend';
    els.otelExporterOtlpEndpointInput.value = env.OTEL_EXPORTER_OTLP_ENDPOINT || '';
    setChecked(els.otelExporterOtlpInsecureInput, env.OTEL_EXPORTER_OTLP_INSECURE, false);
    setChecked(els.otelTracesEnabledInput, env.OTEL_TRACES_ENABLED, true);
    els.otelTracesSamplerSelect.value = env.OTEL_TRACES_SAMPLER || 'parentbased_traceidratio';
    syncCustomSelect(els.otelTracesSamplerSelect);
    els.otelTracesSamplerArgInput.value = env.OTEL_TRACES_SAMPLER_ARG || '1.0';
    setChecked(els.otelMetricsEnabledInput, env.OTEL_METRICS_ENABLED, true);
    setChecked(els.otelPrometheusExporterEnabledInput, env.OTEL_PROMETHEUS_EXPORTER_ENABLED, true);
    setChecked(els.otelLogsEnabledInput, env.OTEL_LOGS_ENABLED, true);
    setChecked(els.otelInstrumentFastapiInput, env.OTEL_INSTRUMENT_FASTAPI, true);
    setChecked(els.otelInstrumentSqlalchemyInput, env.OTEL_INSTRUMENT_SQLALCHEMY, true);
    setChecked(els.otelInstrumentHttpClientsInput, env.OTEL_INSTRUMENT_HTTP_CLIENTS, true);
    setChecked(els.otelSqlCommenterEnabledInput, env.OTEL_SQL_COMMENTER_ENABLED, false);
    setChecked(els.otelCaptureHttpRouteInput, env.OTEL_CAPTURE_HTTP_ROUTE, false);
    setChecked(els.otelCaptureHttpUserAgentInput, env.OTEL_CAPTURE_HTTP_USER_AGENT, false);
    setChecked(els.otelHashHttpUserAgentInput, env.OTEL_HASH_HTTP_USER_AGENT, true);
    els.otelGrpcHostBindInput.value = env.OTEL_GRPC_HOST_BIND || '127.0.0.1';
    els.otelGrpcHostPortInput.value = env.OTEL_GRPC_HOST_PORT || '4317';
    els.otelHttpHostBindInput.value = env.OTEL_HTTP_HOST_BIND || '127.0.0.1';
    els.otelHttpHostPortInput.value = env.OTEL_HTTP_HOST_PORT || '4318';
    els.otelPrometheusHostBindInput.value = env.OTEL_PROMETHEUS_HOST_BIND || '127.0.0.1';
    els.otelPrometheusHostPortInput.value = env.OTEL_PROMETHEUS_HOST_PORT || '8889';
    els.otelHealthcheckHostBindInput.value = env.OTEL_HEALTHCHECK_HOST_BIND || '127.0.0.1';
    els.otelHealthcheckHostPortInput.value = env.OTEL_HEALTHCHECK_HOST_PORT || '13133';
    els.jaegerUiHostBindInput.value = env.JAEGER_UI_HOST_BIND || '127.0.0.1';
    els.jaegerUiHostPortInput.value = env.JAEGER_UI_HOST_PORT || '16686';
    els.jaegerCollectorHostBindInput.value = env.JAEGER_COLLECTOR_HOST_BIND || '127.0.0.1';
    els.jaegerCollectorHostPortInput.value = env.JAEGER_COLLECTOR_HOST_PORT || '14268';
    els.prometheusHostBindInput.value = env.PROMETHEUS_HOST_BIND || '127.0.0.1';
    els.prometheusHostPortInput.value = env.PROMETHEUS_HOST_PORT || '9090';
    els.alertmanagerHostBindInput.value = env.ALERTMANAGER_HOST_BIND || '127.0.0.1';
    els.alertmanagerHostPortInput.value = env.ALERTMANAGER_HOST_PORT || '9093';
    els.grafanaHostBindInput.value = env.GRAFANA_HOST_BIND || '127.0.0.1';
    els.grafanaHostPortInput.value = env.GRAFANA_HOST_PORT || '3001';
    els.grafanaAdminUserInput.value = env.GRAFANA_ADMIN_USER || '';
    els.grafanaAdminPasswordInput.value = env.GRAFANA_ADMIN_PASSWORD || '';
    els.grafanaRootUrlInput.value = env.GRAFANA_ROOT_URL || '';
    els.postgresExporterDataSourceUriInput.value = env.POSTGRES_EXPORTER_DATA_SOURCE_URI || '';
    els.postgresExporterDataSourceUserInput.value = env.POSTGRES_EXPORTER_DATA_SOURCE_USER || '';
    els.postgresExporterDataSourcePassInput.value = env.POSTGRES_EXPORTER_DATA_SOURCE_PASS || '';
    els.redisExporterAddrInput.value = env.REDIS_EXPORTER_ADDR || '';
    const toggleUpdates = {
      usePgbouncer: env.OMLORIX_USE_PGBOUNCER === 'true',
      useBundledStorage: bundledStorage,
      observabilityEnabled: env.OTEL_ENABLED === 'true',
    };
    if (String(env.OMLORIX_USE_BUNDLED_DB ?? '').trim()) {
      toggleUpdates.useBundledDB = env.OMLORIX_USE_BUNDLED_DB === 'true';
    }
    if (String(env.OMLORIX_USE_BUNDLED_REDIS ?? '').trim()) {
      // Redis Off is canonicalized to a non-bundled mode so a later unrelated
      // settings save cannot preserve the old contradictory boolean pair.
      toggleUpdates.useBundledRedis = els.redisEnabledInput.checked
        && env.OMLORIX_USE_BUNDLED_REDIS === 'true';
    }
    setToggles(toggleUpdates);
    if (state.availableVersionsChannel !== els.updateChannelSelect.value) {
      void loadAvailableVersions(els.updateChannelSelect.value, els.versionInput.value);
    }
  }

  function appendVersionOption(value, label, options = {}) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    if (options.disabled) option.disabled = true;
    els.versionInput.appendChild(option);
    return option;
  }

  function renderVersionOptions(selectedValue) {
    let selected = String(selectedValue || '').trim();
    const versions = Array.isArray(state.availableVersions) ? state.availableVersions : [];
    const seen = new Set();
    els.versionInput.replaceChildren();

    // Old launcher configs may still contain the historical moving tags. Once
    // the release list is available, show the concrete release behind the
    // selected channel instead of keeping the moving tag selectable.
    if ((selected === 'stable' || selected === 'beta') && versions.length) {
      selected = String(versions[0]?.value || '').trim();
    }

    for (const version of versions) {
      const value = String(version?.value || '').trim();
      if (!value || seen.has(value)) continue;
      seen.add(value);
      appendVersionOption(value, String(version?.label || value));
    }

    // Keep custom pinned values selectable, but do not re-add the old moving
    // channel tags.
    if (selected && !seen.has(selected)) {
      if (!(selected === 'stable' || selected === 'beta')) {
        const label = versions.length ? `${selected} (custom)` : selected;
        appendVersionOption(selected, label);
        seen.add(selected);
      }
    }

    if (!seen.size) {
      appendVersionOption('', 'No versions available', { disabled: true });
    }

    els.versionInput.value = seen.has(selected) ? selected : els.versionInput.options[0]?.value || '';
    rebuildCustomSelect(els.versionInput);
  }

  async function loadAvailableVersions(
    channelInput = els.updateChannelSelect.value,
    selectedValue = els.versionInput.value,
    options = {},
  ) {
    if (!window.omlorixServer?.getAvailableVersions) return;
    const channel = channelInput || 'stable';
    const force = options.force === true;
    if (
      !force
      && state.availableVersionsChannel === channel
      && Date.now() < state.availableVersionsNextCheckAt
    ) {
      renderVersionOptions(selectedValue || channel);
      return {
        channel,
        versions: state.availableVersions,
        cached: true,
      };
    }
    if (
      state.availableVersionsPromise
      && state.availableVersionsPromiseChannel === channel
    ) {
      return state.availableVersionsPromise;
    }

    const requestId = state.availableVersionsRequest + 1;
    state.availableVersionsRequest = requestId;
    const request = (async () => {
      try {
        const result = await window.omlorixServer.getAvailableVersions(channel, { force });
        if (requestId !== state.availableVersionsRequest) return result;
        if (!result?.unavailable) {
          state.availableVersions = result?.versions || [];
        } else if (state.availableVersionsChannel !== channel) {
          state.availableVersions = [];
        }
        state.availableVersionsChannel = result?.channel || channel;
        state.availableVersionsNextCheckAt = Date.now() + (
          result?.unavailable
            ? RELEASE_CHECK_FAILURE_COOLDOWN_MS
            : VERSION_LIST_REFRESH_INTERVAL_MS
        );
        renderVersionOptions(selectedValue || state.availableVersionsChannel);
        return result;
      } catch (error) {
        if (requestId !== state.availableVersionsRequest) return null;
        state.availableVersions = [];
        state.availableVersionsChannel = channel;
        state.availableVersionsNextCheckAt = Date.now() + RELEASE_CHECK_FAILURE_COOLDOWN_MS;
        renderVersionOptions(selectedValue || channel);
        if (!options.silent) {
          appendConsole(`Version list unavailable: ${translatedErrorMessage(error)}`);
        }
        return null;
      }
    })();
    state.availableVersionsPromise = request;
    state.availableVersionsPromiseChannel = channel;
    try {
      return await request;
    } finally {
      if (state.availableVersionsPromise === request) {
        state.availableVersionsPromise = null;
        state.availableVersionsPromiseChannel = '';
      }
    }
  }

  function refreshAvailableVersionsQuietly() {
    if (document.hidden) return;
    const channel = els.updateChannelSelect.value || state.availableVersionsChannel || 'stable';
    void loadAvailableVersions(channel, els.versionInput.value, { silent: true });
  }

  function startAvailableVersionsRefreshTimer() {
    if (state.availableVersionsRefreshTimer) return;

    // The launcher can stay open for days, so keep the release dropdown fresh
    // without requiring a full status refresh or user interaction.
    state.availableVersionsRefreshTimer = window.setInterval(
      refreshAvailableVersionsQuietly,
      VERSION_LIST_REFRESH_INTERVAL_MS,
    );

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        refreshAvailableVersionsQuietly();
      }
    });
  }

  function proxyFieldErrors() {
    return {
      bindHost: els.proxyBindError,
      httpPort: els.proxyHttpPortError,
      httpsPort: els.proxyHttpsPortError,
      tlsCertPath: els.proxyTlsCertError,
      tlsKeyPath: els.proxyTlsKeyError,
      tlsCaPath: els.proxyTlsCaError,
    };
  }

  function setProxyValidation(message) {
    const text = String(message || '');
    els.proxyValidation.textContent = text;
    els.proxyValidation.hidden = !text;
  }

  function renderProxyValidation(errors = {}) {
    state.proxyValidationErrors = errors || {};
    for (const [key, element] of Object.entries(proxyFieldErrors())) {
      const message = state.proxyValidationErrors[key] || '';
      // Proxy validation is produced by the shared server manager. Translate
      // each individual message before it is placed in a field; translating
      // the form as one combined block would not match the source catalog.
      element.textContent = translateLauncherSource(message);
      element.hidden = !message;
      const field = element.closest('.field');
      const input = field ? field.querySelector('input') : null;
      if (input) input.setAttribute('aria-invalid', message ? 'true' : 'false');
    }
    const firstError = Object.values(state.proxyValidationErrors)[0] || '';
    setProxyValidation(firstError);
  }

  function collectProxySettings() {
    return {
      trustProxyHeaders: els.trustProxyHeadersInput.checked,
      trustedProxies: els.trustedProxiesInput.value,
      trustedHosts: els.trustedHostsInput.value,
      uvicornForwardedAllowIps: els.uvicornForwardedAllowIpsInput.value,
      rateLimitTrustedProxies: els.rateLimitTrustedProxiesInput.value,
      authTrustedProxies: els.authTrustedProxiesInput.value,
      rateLimitProxySettingsCacheSeconds: els.rateLimitProxySettingsCacheSecondsInput.value,
      frontendHttpHostBind: els.frontendHttpHostBindInput.value,
      frontendHttpHostPort: els.frontendHttpHostPortInput.value,
      apiLbTraefikWebHostPort: els.apiLbTraefikWebHostPortInput.value,
      apiLbTraefikDashboardHostPort: els.apiLbTraefikDashboardHostPortInput.value,
      enabled: els.proxyEnabledInput.checked,
      autostart: els.proxyAutostartInput.checked,
      bindHost: els.proxyBindInput.value,
      httpPort: els.proxyHttpPortInput.value,
      httpsEnabled: els.proxyHttpsInput.checked,
      httpsPort: els.proxyHttpsPortInput.value,
      redirectHttpToHttps: els.proxyRedirectInput.checked,
      tlsCertPath: els.proxyTlsCertInput.value,
      tlsKeyPath: els.proxyTlsKeyInput.value,
      tlsCaPath: els.proxyTlsCaInput.value,
      tlsKeyPassphrase: els.proxyTlsPassphraseInput.value,
      clearTlsKeyPassphrase: els.proxyClearPassphraseInput.checked,
    };
  }

  async function chooseProxyTlsFile(kind, input) {
    if (!window.omlorixServer?.chooseProxyTlsFile || !input) return;
    try {
      const result = await window.omlorixServer.chooseProxyTlsFile(kind, input.value);
      if (!result || result.canceled || !result.path) return;
      input.value = result.path;
      markProxyFormChanged();
      renderProxyValidation({});
      queueProxyAutosave();
    } catch (error) {
      setProxyValidation(translatedErrorMessage(error));
    }
  }

  function renderProxyStatus(proxy) {
    const status = proxy || {};
    const config = status.config || {};
    const enabled = Boolean(config.enabled);
    const running = Boolean(status.running);
    const httpsEnabled = Boolean(config.httpsEnabled);
    els.proxyBadge.className = running
      ? 'tag badge badge-ok'
      : enabled
        ? 'tag badge badge-warn'
        : 'tag badge badge-muted';
    els.proxyBadge.textContent = running ? 'Running' : enabled ? 'Enabled' : 'Off';
    els.proxyStatusPanel.dataset.state = running ? 'running' : enabled ? 'enabled' : 'off';
    els.proxyPublicUrl.textContent = config.publicUrl || 'Not configured';
    els.proxyTargetUrl.textContent = config.target || 'Local Omlorix port';
    els.proxyRuntimeStatus.textContent = running
      ? status.startedAt
        ? launcherT(
          'launcher_ui_running_since_value1',
          'Running since {value1}',
          { value1: formatDateTime(status.startedAt) },
        )
        : translateLauncherSource('Running')
      : status.lastError
        ? `Stopped: ${status.lastError}`
        : translateLauncherSource('Stopped');
    const serviceInstalled = Boolean(status.serviceInstalled);
    const serviceAvailable = Boolean(status.serviceAvailable);
    const focusedServiceAction = document.activeElement === els.proxyInstallServiceButton
      ? 'install'
      : document.activeElement === els.proxyUninstallServiceButton
        ? 'uninstall'
        : '';
    els.proxyServiceStatus.textContent = serviceInstalled
      ? launcherT('launcher_proxy_background_service_installed', 'Background service installed')
      : serviceAvailable
        ? launcherT('launcher_proxy_background_service_not_installed', 'Background service not installed')
        : launcherT('launcher_proxy_background_service_unavailable', 'Background service is unavailable in this build');
    els.proxyInstallServiceButton.textContent = launcherT(
      'launcher_proxy_install_background_service',
      'Install background service',
    );
    els.proxyUninstallServiceButton.textContent = launcherT(
      'launcher_proxy_remove_background_service',
      'Remove background service',
    );
    els.proxyInstallServiceButton.hidden = serviceInstalled;
    els.proxyInstallServiceButton.disabled = !serviceAvailable || !enabled || state.busy;
    els.proxyUninstallServiceButton.hidden = !serviceInstalled;
    els.proxyUninstallServiceButton.disabled = !serviceAvailable || state.busy;
    // Replacing a focused control by toggling `hidden` can discard keyboard
    // and screen-reader focus. Move it to the reciprocal action after the DOM
    // has applied the visibility update.
    if (focusedServiceAction === 'install' && serviceInstalled) {
      queueMicrotask(() => els.proxyUninstallServiceButton.focus());
    } else if (focusedServiceAction === 'uninstall' && !serviceInstalled) {
      queueMicrotask(() => {
        if (!els.proxyInstallServiceButton.disabled) els.proxyInstallServiceButton.focus();
        else els.proxyServiceStatus.focus({ preventScroll: true });
      });
    }

    if (state.proxyFormDirty) {
      updateProxyVisibility();
      setBusy(state.busy);
      return;
    }

    els.proxyEnabledInput.checked = enabled;
    const env = state.current?.env || {};
    els.trustProxyHeadersInput.checked = String(env.TRUST_PROXY_HEADERS || 'false').toLowerCase() === 'true';
    els.trustedProxiesInput.value = !enabled && env.FRONTEND_TRUSTED_UPSTREAMS
      ? env.FRONTEND_TRUSTED_UPSTREAMS
      : env.TRUSTED_PROXIES || '';
    els.trustedHostsInput.value = env.TRUSTED_HOSTS || '';
    els.uvicornForwardedAllowIpsInput.value = env.UVICORN_FORWARDED_ALLOW_IPS || '127.0.0.1,::1';
    els.rateLimitTrustedProxiesInput.value = env.RATE_LIMIT_TRUSTED_PROXIES || '';
    els.authTrustedProxiesInput.value = env.AUTH_TRUSTED_PROXIES || '';
    els.rateLimitProxySettingsCacheSecondsInput.value = env.RATE_LIMIT_PROXY_SETTINGS_CACHE_SECONDS || '60';
    els.frontendHttpHostBindInput.value = env.FRONTEND_HTTP_HOST_BIND || '127.0.0.1';
    els.frontendHttpHostPortInput.value = env.FRONTEND_HTTP_HOST_PORT || '8080';
    els.apiLbTraefikWebHostPortInput.value = env.API_LB_TRAEFIK_WEB_HOST_PORT || '8080';
    els.apiLbTraefikDashboardHostPortInput.value = env.API_LB_TRAEFIK_DASHBOARD_HOST_PORT || '8081';
    els.proxyAutostartInput.checked = Boolean(config.autostart);
    els.proxyBindInput.value = config.bindHost || '0.0.0.0';
    els.proxyHttpPortInput.value = config.httpPort || '8081';
    els.proxyHttpsInput.checked = httpsEnabled;
    els.proxyRedirectInput.checked = Boolean(config.redirectHttpToHttps);
    els.proxyHttpsPortInput.value = config.httpsPort || '8443';
    els.proxyTlsCertInput.value = config.tlsCertPath || '';
    els.proxyTlsKeyInput.value = config.tlsKeyPath || '';
    els.proxyTlsCaInput.value = config.tlsCaPath || '';
    els.proxyTlsPassphraseInput.placeholder = translateLauncherSource(
      config.tlsKeyPassphraseSet ? 'Already set' : 'Optional',
    );
    updateProxyVisibility();
    if (!httpsEnabled) {
      els.proxyRedirectInput.checked = false;
    }
    setBusy(state.busy);
  }

  function queueProxyAutosave() {
    if (state.proxyAutosaveTimer) {
      window.clearTimeout(state.proxyAutosaveTimer);
    }
    state.proxyAutosaveTimer = window.setTimeout(() => {
      state.proxyAutosaveTimer = null;
      void saveProxySettings({ silent: true });
    }, PROXY_AUTOSAVE_DELAY_MS);
  }

  /**
   * Record a user-visible proxy form change.
   *
   * The revision is deliberately separate from the dirty boolean. A save may
   * legitimately clear the dirty state only when no edit happened after that
   * save captured its payload. The revision lets the completion handler prove
   * that condition instead of relying on whether the debounce timer has fired.
   */
  function markProxyFormChanged() {
    state.proxyEditVersion += 1;
    state.proxyFormDirty = true;

    // Text inputs normally wait for the debounce timer. If a previous IPC save
    // is already running, mark the edit immediately so its completion schedules
    // a follow-up save and never hydrates stale values over the current form.
    if (state.proxySaving) {
      state.proxySaveRequested = true;
    }
  }

  /**
   * Return whether a save result is older than the current form.
   *
   * Explicit save attempts are retained in this check because buttons such as
   * Start and Restart may request a second save without changing a field.
   */
  function proxyFormChangedSince(saveEditVersion) {
    return state.proxySaveRequested || state.proxyEditVersion !== saveEditVersion;
  }

  function updateProxyHttpsControls() {
    const httpsEnabled = els.proxyHttpsInput.checked;
    els.proxyHttpsSettings.hidden = !httpsEnabled;
    if (!httpsEnabled) {
      els.proxyRedirectInput.checked = false;
    }
  }

  function updateProxyVisibility() {
    const proxyEnabled = els.proxyEnabledInput.checked;
    els.proxySettings.hidden = !proxyEnabled;
    updateProxyHttpsControls();
  }

  async function saveProxySettings(options = {}) {
    if (state.proxySaving) {
      state.proxySaveRequested = true;
      if (options.waitForActiveSave && state.proxySavePromise) {
        await state.proxySavePromise.catch(() => false);
        await new Promise((resolve) => window.setTimeout(resolve, 0));
        return saveProxySettings({ ...options, waitForActiveSave: false });
      }
      return false;
    }

    state.proxySaving = true;
    state.proxySaveRequested = false;
    // Capture the revision before collecting the payload. Any later user edit
    // makes the IPC result stale, even when its debounce callback has not run.
    const saveEditVersion = state.proxyEditVersion;
    if (state.proxyAutosaveTimer) {
      window.clearTimeout(state.proxyAutosaveTimer);
      state.proxyAutosaveTimer = null;
    }
    renderProxyValidation({});
    const saveOperation = (async () => {
      const result = await window.omlorixServer.saveProxySettings(collectProxySettings());
      if (result && result.ok === false) {
        // Validation belongs to the captured payload. Do not display errors
        // for an older value after the operator has already corrected it.
        if (!proxyFormChangedSince(saveEditVersion)) {
          renderProxyValidation(result.validationErrors || {});
        }
        return false;
      }

      const hasQueuedSave = proxyFormChangedSince(saveEditVersion);
      state.proxyFormDirty = hasQueuedSave;
      if (!hasQueuedSave) {
        els.proxyTlsPassphraseInput.value = '';
        els.proxyClearPassphraseInput.checked = false;
      }
      renderState(result);
      if (!options.silent) {
        appendConsole(options.message || 'Proxy settings saved.');
      }
      return true;
    })();
    state.proxySavePromise = saveOperation;
    try {
      return await saveOperation;
    } catch (error) {
      // Like validation responses, an operational error from an older payload
      // must not replace feedback for a newer edit that is about to be saved.
      if (!proxyFormChangedSince(saveEditVersion)) {
        setProxyValidation(translatedErrorMessage(error));
      }
      if (!options.silent) {
        appendConsole(`Proxy settings failed: ${translatedErrorMessage(error)}`);
      }
      return false;
    } finally {
      const needsFollowUpSave = proxyFormChangedSince(saveEditVersion);
      state.proxySaving = false;
      state.proxySavePromise = null;
      if (needsFollowUpSave) {
        state.proxySaveRequested = false;
        queueProxyAutosave();
      }
    }
  }

  /** Build fully translated console feedback for either proxy-start entry point. */
  function proxyStartActionMessages() {
    return {
      starting: launcherT('launcher_proxy_action_starting', 'Starting proxy'),
      finished: launcherT('launcher_proxy_action_started', 'Proxy started.'),
      failed(error) {
        return launcherT(
          'launcher_proxy_action_start_failed',
          'Proxy start failed: {error}',
          { error },
        );
      },
    };
  }

  async function runProxyAction(label, fn, messages = {}) {
    setBusy(true);
    renderProxyValidation({});
    appendConsole(`\n> ${messages.starting || label}`);
    try {
      const data = await fn();
      renderState(data);
      appendConsole(messages.finished || `${label} finished.`);
    } catch (error) {
      const errorText = translatedErrorMessage(error);
      appendConsole(
        typeof messages.failed === 'function'
          ? messages.failed(errorText)
          : `${label} failed: ${errorText}`,
      );
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  function formatDateTime(value) {
    if (!value) return 'Not scheduled';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'Not scheduled';
    return new Intl.DateTimeFormat(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    }).format(date);
  }

  function setAutoUpdateValidation(message) {
    const text = String(message || '');
    els.autoUpdateValidation.textContent = text;
    els.autoUpdateValidation.hidden = !text;
  }

  function collectAutoUpdateSettings() {
    const weekdays = els.autoUpdateWeekdayInputs
      .filter((input) => input.checked)
      .map((input) => Number(input.value));
    return {
      enabled: els.autoUpdateEnabledInput.checked,
      channel: els.updateChannelSelect.value || 'stable',
      schedule: els.autoUpdateScheduleSelect.value,
      weekdays,
      time: els.autoUpdateTimeInput.value || '03:00',
      backupBeforeUpdate: els.autoUpdateBackupInput.checked,
      backupDestinationId: state.backupDestinationId,
      backupEncryptionEnabled: state.backupEncryptionPreferred,
      onlyWhenHealthy: els.autoUpdateHealthyInput.checked,
    };
  }

  /** Persist only the Dashboard policy without rewriting schedule controls. */
  async function saveBackupPolicy() {
    try {
      const snapshot = await window.omlorixServer.saveScheduledUpdates({
        backupDestinationId: state.backupDestinationId,
        backupEncryptionEnabled: state.backupEncryptionPreferred,
      });
      renderAutoUpdates(snapshot);
      return true;
    } catch (error) {
      setAutoUpdateValidation(translatedErrorMessage(error));
      return false;
    }
  }

  function validateAutoUpdateSettings(settings) {
    if (!settings.enabled) {
      return '';
    }
    if (settings.schedule === 'custom' && !settings.weekdays.length) {
      return 'Choose at least one day for custom automatic updates.';
    }
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(settings.time)) {
      return 'Choose a valid update time.';
    }
    return '';
  }

  function setAutoUpdateBadge(status, settings) {
    const stateName = status?.state || 'idle';
    if (!settings?.enabled) {
      els.autoUpdateBadge.className = 'tag badge badge-muted';
      els.autoUpdateBadge.textContent = 'Off';
      return;
    }
    if (stateName === 'blocked') {
      els.autoUpdateBadge.className = 'tag badge badge-warn';
      els.autoUpdateBadge.textContent = 'Blocked';
      return;
    }
    if (stateName === 'running') {
      els.autoUpdateBadge.className = 'tag badge badge-warn';
      els.autoUpdateBadge.textContent = 'Running';
      return;
    }
    if (stateName === 'error') {
      els.autoUpdateBadge.className = 'tag badge badge-error';
      els.autoUpdateBadge.textContent = 'Error';
      return;
    }
    els.autoUpdateBadge.className = 'tag badge badge-ok';
    els.autoUpdateBadge.textContent = 'On';
  }

  function renderAutoUpdateBlockedDebug(settings, status, requirement) {
    const currentLauncherVersion = requirement?.currentLauncherVersion || 'unknown';
    const minimumLauncherVersion = requirement?.minimumLauncherVersion || 'unknown';
    const targetVersion = requirement?.targetVersion || status.latestVersion || 'unknown';
    const reason = currentLauncherVersion !== 'unknown' && minimumLauncherVersion !== 'unknown'
      ? translateLauncherSource(
        `Current launcher ${currentLauncherVersion} is older than required launcher ${minimumLauncherVersion}.`,
      )
      : translateLauncherSource(
        'The update check reported LAUNCHER_UPDATE_REQUIRED without complete launcher version metadata.',
      );
    const translateDebugValue = (value, fallback = 'None') => {
      const normalized = String(value || '').trim();
      if (!normalized) return translateLauncherSource(fallback);
      const statusLabels = {
        blocked: 'Blocked',
        error: 'Error',
        idle: 'Idle',
        running: 'Running',
        scheduled: 'Scheduled',
        skipped: 'Skipped',
        success: 'Success',
      };
      return translateLauncherSource(statusLabels[normalized] || normalized);
    };
    const versionOrUnknown = (value) => (
      value && value !== 'unknown' ? value : translateLauncherSource('Unknown')
    );
    const rows = [
      [translateLauncherSource('Reason'), reason],
      [translateLauncherSource('Status state'), translateDebugValue(status.state, 'Unknown')],
      [translateLauncherSource('Status message'), translateDebugValue(status.lastMessage)],
      [translateLauncherSource('Automatic updates enabled'), translateLauncherSource(settings.enabled ? 'Yes' : 'No')],
      [translateLauncherSource('Update channel'), translateLauncherSource(settings.channel === 'beta' ? 'Beta' : 'Stable')],
      [translateLauncherSource('Current Omlorix version'), versionOrUnknown(status.currentVersion)],
      ['Target Omlorix version', versionOrUnknown(targetVersion)],
      ['Current launcher version', versionOrUnknown(currentLauncherVersion)],
      ['Minimum launcher version', versionOrUnknown(minimumLauncherVersion)],
      [translateLauncherSource('Release note / feed reason'), requirement?.releaseNotes || translateLauncherSource('None')],
      [translateLauncherSource('Last checked at'), status.lastCheckedAt || translateLauncherSource('Never')],
      [translateLauncherSource('Last failure at'), status.lastFailureAt || translateLauncherSource('Never')],
      [translateLauncherSource('Next run at'), status.nextRunAt || translateLauncherSource('None')],
    ];

    // Most labels above already existed in the complete catalog. Translate
    // every row here as a final safeguard before joining them into one text
    // node, because the DOM observer cannot translate individual lines later.
    for (const row of rows) {
      row[0] = translateLauncherSource(row[0]);
    }

    els.autoUpdateBlockedDebug.textContent = rows
      .map(([label, value]) => `${label}: ${value}`)
      .join('\n');
  }

  function updateAutoUpdateVisibility(settings = collectAutoUpdateSettings()) {
    const enabled = Boolean(settings.enabled);
    const customDays = enabled && settings.schedule === 'custom';
    els.autoUpdateSettings.hidden = !enabled;
    els.autoUpdateWeekdaysFieldset.hidden = !customDays;
  }

  /** Explain which Dashboard backup configuration automatic updates reuse. */
  function renderAutoUpdateBackupReference(enabled) {
    els.autoUpdateBackupReferenceText.textContent = enabled
      ? launcherT(
        'launcher_auto_update_backup_reference_enabled',
        'Uses the backup destination and archive encryption configured on the Dashboard.',
      )
      : launcherT(
        'launcher_auto_update_backup_reference_disabled',
        'Backup settings stay configured on the Dashboard while this option is off.',
      );
    els.autoUpdateBackupSettingsButton.textContent = launcherT(
      'launcher_auto_update_backup_reference_action',
      'Review backup settings',
    );
  }

  /** Open the Dashboard and move keyboard focus to its backup configuration. */
  function openDashboardBackupSettings() {
    const dashboardLink = els.navLinks.find((link) => link.dataset.section === 'status');
    dashboardLink?.click();
    window.requestAnimationFrame(() => {
      els.dashboardBackupSettings.scrollIntoView({ block: 'center', behavior: 'auto' });
      els.dashboardBackupSettings.focus({ preventScroll: true });
    });
  }

  function renderAutoUpdates(snapshot) {
    state.autoUpdates = snapshot || state.autoUpdates;
    const settings = state.autoUpdates?.settings || {};
    const status = state.autoUpdates?.status || {};
    const plaintextOnly = state.backupOptions
      && state.backupOptions.capabilities?.archive_encryption_available !== true
      && state.backupOptions.capabilities?.plaintext_archives_allowed === true;
    state.backupDestinationId = String(settings.backupDestinationId || '').trim();
    state.backupEncryptionPreferred = plaintextOnly
      ? false
      : settings.backupEncryptionEnabled !== false;
    if (state.backupOptions) {
      populateBackupDestinations();
      renderBackupPanel();
    }
    if (plaintextOnly && settings.backupEncryptionEnabled !== false) {
      void saveBackupPolicy();
    }
    els.autoUpdateEnabledInput.checked = Boolean(settings.enabled);
    els.autoUpdateScheduleSelect.value = settings.schedule || 'daily';
    syncCustomSelect(els.autoUpdateScheduleSelect);
    els.autoUpdateTimeInput.value = settings.time || '03:00';
    els.autoUpdateBackupInput.checked = settings.backupBeforeUpdate !== false;
    els.autoUpdateHealthyInput.checked = settings.onlyWhenHealthy !== false;
    renderAutoUpdateBackupReference(els.autoUpdateBackupInput.checked);

    const selected = new Set((settings.weekdays || []).map((day) => Number(day)));
    for (const input of els.autoUpdateWeekdayInputs) {
      input.checked = selected.has(Number(input.value));
    }

    updateAutoUpdateVisibility(settings);
    els.autoUpdateNextRun.textContent = settings.enabled
      ? formatDateTime(status.nextRunAt)
      : 'Not scheduled';
    els.autoUpdateLastMessage.textContent = translateLauncherSource(
      status.lastMessage || 'No automatic update has run yet.',
    );
    els.autoUpdateVersions.textContent = status.latestVersion
      ? launcherT(
        settings.channel === 'beta'
          ? 'launcher_ui_beta_value1_value2'
          : 'launcher_ui_stable_value1_value2',
        settings.channel === 'beta'
          ? 'Beta: {value1} -> {value2}'
          : 'Stable: {value1} -> {value2}',
        {
          value1: status.currentVersion || translateLauncherSource('Current'),
          value2: status.latestVersion,
        },
      )
      : translateLauncherSource('Unknown');

    const requirement = status.launcherRequirement;
    const launcherBlockVisible = Boolean(settings.enabled && status.state === 'blocked' && requirement);
    els.autoUpdateBlockedPanel.hidden = !launcherBlockVisible;
    if (requirement) {
      els.autoUpdateBlockedMessage.textContent = launcherT(
        'launcher_ui_omlorix_value1_requires_server_launcher_value2_before_automatic_updates_can',
        'Omlorix {value1} requires Server Launcher {value2} before automatic updates can continue.',
        {
          value1: requirement.targetVersion || translateLauncherSource('Latest'),
          value2: requirement.minimumLauncherVersion || translateLauncherSource('Newer'),
        },
      );
      renderAutoUpdateBlockedDebug(settings, status, requirement);
    } else {
      els.autoUpdateBlockedDebug.textContent = '';
    }

    setAutoUpdateBadge(status, settings);
    setBusy(state.busy);
  }

  async function loadAutoUpdates() {
    try {
      renderAutoUpdates(await window.omlorixServer.getScheduledUpdates());
    } catch (error) {
      setAutoUpdateValidation(`Automatic update settings failed: ${translatedErrorMessage(error)}`);
    }
  }

  function queueAutoUpdateAutosave() {
    if (state.autoUpdateAutosaveTimer) {
      window.clearTimeout(state.autoUpdateAutosaveTimer);
    }
    state.autoUpdateAutosaveTimer = window.setTimeout(() => {
      state.autoUpdateAutosaveTimer = null;
      void saveAutoUpdates({ silent: true });
    }, AUTO_UPDATE_AUTOSAVE_DELAY_MS);
  }

  function renderPendingAutoUpdateSettings() {
    renderAutoUpdates({
      settings: {
        ...(state.autoUpdates?.settings || {}),
        ...collectAutoUpdateSettings(),
      },
      status: state.autoUpdates?.status || {},
    });
  }

  async function saveAutoUpdates({ silent = false } = {}) {
    if (state.autoUpdateSaving) {
      state.autoUpdateSaveRequested = true;
      return false;
    }

    const settings = collectAutoUpdateSettings();
    const validation = validateAutoUpdateSettings(settings);
    setAutoUpdateValidation(validation);
    if (validation) return false;

    state.autoUpdateSaving = true;
    state.autoUpdateSaveRequested = false;
    if (state.autoUpdateAutosaveTimer) {
      window.clearTimeout(state.autoUpdateAutosaveTimer);
      state.autoUpdateAutosaveTimer = null;
    }

    try {
      const snapshot = await window.omlorixServer.saveScheduledUpdates(settings);
      const hasQueuedSave = state.autoUpdateSaveRequested;
      if (!hasQueuedSave) {
        renderAutoUpdates(snapshot);
      }
      if (!silent) {
        appendConsole(settings.enabled
          ? 'Automatic updates saved.'
          : 'Automatic updates disabled.');
      }
      return true;
    } catch (error) {
      setAutoUpdateValidation(translatedErrorMessage(error));
      return false;
    } finally {
      state.autoUpdateSaving = false;
      if (state.autoUpdateSaveRequested) {
        state.autoUpdateSaveRequested = false;
        queueAutoUpdateAutosave();
      }
    }
  }

  function normalizeText(value) {
    return String(value || '').toLowerCase();
  }

  function isSecretKey(key) {
    const normalized = String(key || '').toUpperCase();
    return ['DATABASE_URL', 'REDIS_URL'].includes(normalized)
      || /(SECRET|PASSWORD|PASSPHRASE|TOKEN|CREDENTIAL|CONNECTION_STRING|ENCRYPTION_KEY|PRIVATE_KEY|ACCESS_KEY|API_KEY|CLIENT_SECRET|AUTHORIZATION)/i.test(normalized);
  }

  function setSecretInputRevealed(input, button, revealed, labels) {
    if (!input || !button) return;
    input.classList.toggle('is-secret-revealed', revealed);
    if (input.type === 'password' || input.type === 'text') {
      input.type = revealed ? 'text' : 'password';
    }
    button.setAttribute('aria-pressed', revealed ? 'true' : 'false');
    button.setAttribute('aria-label', revealed ? labels.hide : labels.show);
    window.OmlorixLauncherIcons.setSecretRevealIcon(button, revealed);
  }

  function bindSecretRevealButton(button, input, sourceShowLabel) {
    if (!button || !input) return;
    if (input.id) button.setAttribute('aria-controls', input.id);
    const sourceValueLabel = sourceShowLabel.replace(/^Show\s+/i, '') || 'secret value';
    const labels = {
      show: translateLauncherSource(`Show ${sourceValueLabel}`),
      hide: translateLauncherSource(`Hide ${sourceValueLabel}`),
    };
    setSecretInputRevealed(input, button, false, labels);
    button.addEventListener('click', () => {
      const revealed = button.getAttribute('aria-pressed') !== 'true';
      setSecretInputRevealed(input, button, revealed, labels);
      input.focus();
    });
  }

  function markEnvDirty() {
    state.envLastChangeAt = Date.now();
    state.envEditVersion += 1;
    els.envEditorSaved.textContent = launcherT('launcher_env_status_unsaved_changes', 'Unsaved changes');
    setBusy(state.busy);
    queueEnvAutosave();
  }

  function hasDirtyEnvEditor() {
    return state.envRemovedKeys.size > 0 || (state.envEditor?.fields || []).some((field) => field.dirty);
  }

  function getLauncherDialogFocusTargets() {
    const targets = [
      els.launcherDialogInputField.hidden ? null : els.launcherDialogInput,
      els.launcherDialogCancelButton,
      els.launcherDialogConfirmButton,
    ];
    return targets.filter((target) => target && !target.disabled && typeof target.focus === 'function');
  }

  function focusInitialLauncherDialogTarget() {
    const initialTarget = els.launcherDialogInputField.hidden ? els.launcherDialogConfirmButton : els.launcherDialogInput;
    initialTarget.focus();
    if (initialTarget === els.launcherDialogInput) {
      els.launcherDialogInput.select();
    }
  }

  function trapLauncherDialogFocus(event) {
    if (!state.launcherDialog || event.key !== 'Tab') return;
    const focusTargets = getLauncherDialogFocusTargets();
    if (!focusTargets.length) return;

    const firstTarget = focusTargets[0];
    const lastTarget = focusTargets[focusTargets.length - 1];
    if (event.shiftKey && document.activeElement === firstTarget) {
      event.preventDefault();
      lastTarget.focus();
      return;
    }
    if (!event.shiftKey && document.activeElement === lastTarget) {
      event.preventDefault();
      firstTarget.focus();
    }
  }

  function settleLauncherDialog(value) {
    if (!state.launcherDialog) return;
    const { resolve, previousFocus } = state.launcherDialog;
    state.launcherDialog = null;
    els.launcherDialogOverlay.hidden = true;
    els.launcherDialogInput.value = '';
    els.launcherDialogInputField.hidden = true;
    resolve(value);
    if (previousFocus && typeof previousFocus.focus === 'function') {
      previousFocus.focus();
    }
  }

  function showLauncherDialog(options = {}) {
    if (state.launcherDialog) {
      settleLauncherDialog(null);
    }

    const previousFocus = document.activeElement;
    els.launcherDialogTitle.textContent = options.title || 'Confirm action';
    els.launcherDialogMessage.textContent = options.message || 'Continue?';
    els.launcherDialogConfirmButton.textContent = options.confirmText || 'Continue';
    els.launcherDialogCancelButton.textContent = options.cancelText || 'Cancel';
    els.launcherDialogInputField.hidden = !options.input;
    els.launcherDialogInput.required = Boolean(options.input);
    els.launcherDialogInput.value = options.defaultValue || '';
    els.launcherDialogInputLabel.textContent = options.inputLabel || 'Value';
    els.launcherDialogOverlay.hidden = false;

    return new Promise((resolve) => {
      state.launcherDialog = {
        resolve,
        input: Boolean(options.input),
        previousFocus,
      };
      focusInitialLauncherDialogTarget();
    });
  }

  // Setup and permanent secret management live in a focused renderer module.
  // Share this one accessible dialog implementation so those controls retain
  // the same focus trap, Escape handling, and focus restoration as the rest of
  // the Launcher instead of introducing browser-native confirmation dialogs.
  window.omlorixShowLauncherDialog = showLauncherDialog;

  async function confirmDiscardEnvChanges(action) {
    if (!hasDirtyEnvEditor()) return true;
    const confirmed = await showLauncherDialog({
      title: 'Discard unsaved .env changes?',
      message: `Discard your current .env edits and ${action}?`,
      confirmText: 'Discard changes',
    });
    if (!confirmed) {
      els.envEditorSaved.textContent = 'Unsaved changes';
      return false;
    }
    return loadEnvEditor();
  }

  function updateEnvValidationSummary() {
    const entries = Object.entries(state.envValidationErrors || {});
    if (!entries.length) {
      els.envValidationSummary.hidden = true;
      els.envValidationSummary.textContent = '';
      return;
    }
    els.envValidationSummary.hidden = false;
    els.envValidationSummary.textContent = launcherT(
      entries.length === 1
        ? 'launcher_ui_count_field_needs_attention_before_saving'
        : 'launcher_ui_count_fields_need_attention_before_saving',
      entries.length === 1
        ? '{count} field needs attention before saving.'
        : '{count} fields need attention before saving.',
      { count: entries.length },
    );
  }

  function queueEnvAutosave(delayMs = ENV_EDITOR_AUTOSAVE_DELAY_MS) {
    if (state.envAutosaveTimer) {
      window.clearTimeout(state.envAutosaveTimer);
    }
    state.envAutosaveTimer = window.setTimeout(() => {
      state.envAutosaveTimer = null;
      void saveEnvEditorNow({ silent: true });
    }, delayMs);
  }

  function envEditorHasFocusedControl() {
    const active = document.activeElement;
    return Boolean(active && (
      els.envEditorForm?.contains(active)
      || els.envSearchInput === active
      || els.envSectionFilter === active
      || els.customEnvKeyInput === active
      || els.customEnvValueInput === active
    ));
  }

  function envEditorCanAutoReload() {
    if (!state.envEditor || document.hidden || state.envReloadInFlight || state.envSaving) return false;
    if (state.envAutosaveTimer || state.envImportPreview || hasDirtyEnvEditor()) return false;
    if (envEditorHasFocusedControl()) return false;
    return Date.now() - state.envLastChangeAt >= ENV_EDITOR_IDLE_RELOAD_DELAY_MS;
  }

  async function autoReloadEnvEditorIfIdle() {
    if (!envEditorCanAutoReload()) return;
    await loadEnvEditor({ silent: true, force: true });
  }

  function startEnvIdleReloadTimer() {
    if (state.envIdleReloadTimer) return;
    state.envLastChangeAt = Date.now();
    state.envIdleReloadTimer = window.setInterval(
      autoReloadEnvEditorIfIdle,
      ENV_EDITOR_IDLE_RELOAD_INTERVAL_MS,
    );
  }

  function basename(filePath) {
    return String(filePath || '').split(/[\\/]/).filter(Boolean).pop() || String(filePath || '');
  }

  function toggleRequirementTargetForKey(key) {
    return TOGGLE_REQUIREMENT_TARGETS.get(String(key || '').trim()) || '';
  }

  function clearToggleRequirementErrors() {
    for (const [toggleKey, element] of toggleErrorEls.entries()) {
      element.textContent = '';
      element.hidden = true;
      const option = element.closest('.toggle-option, .connection-panel');
      if (option) option.classList.remove('has-error');
    }
  }

  function renderToggleRequirementErrors(groupedIssues) {
    clearToggleRequirementErrors();
    for (const [toggleKey, issues] of groupedIssues.entries()) {
      const element = toggleErrorEls.get(toggleKey);
      if (!element) continue;
      const option = element.closest('.toggle-option, .connection-panel');
      if (option) option.classList.add('has-error');
      element.hidden = false;
      element.textContent = issues
        .map((issue) => `${issue.key}: ${translateLauncherSource(
          issue.message || 'Set a non-placeholder value.',
        )}`)
        .join('\n');
    }
  }

  /**
   * Add one import-review detail card.
   *
   * Environment names are rendered as individual code tokens so long imports
   * remain scannable and can scroll inside the card instead of stretching the
   * entire Environment page. Plain-language details continue to use a normal
   * paragraph for screen readers and translated validation feedback.
   */
  function appendImportDetail(title, { body = '', keys = [] } = {}, kind = 'info') {
    const item = document.createElement('section');
    item.className = `env-import-detail env-import-detail-${kind}`;
    const heading = document.createElement('div');
    heading.className = 'env-import-detail-heading';
    const headingText = document.createElement('h4');
    headingText.textContent = translateLauncherSource(title);
    heading.appendChild(headingText);

    if (keys.length) {
      const count = document.createElement('span');
      count.className = 'env-import-detail-count';
      count.textContent = String(keys.length);
      heading.appendChild(count);
    }
    item.appendChild(heading);

    if (body) {
      const text = document.createElement('p');
      text.textContent = translateLauncherSource(body);
      item.appendChild(text);
    }
    if (keys.length) {
      const keyList = document.createElement('div');
      keyList.className = 'env-import-key-list';
      keyList.setAttribute('role', 'list');
      keyList.setAttribute('aria-label', translateLauncherSource(title));
      keyList.tabIndex = 0;
      for (const key of keys) {
        const token = document.createElement('code');
        token.setAttribute('role', 'listitem');
        token.textContent = key;
        keyList.appendChild(token);
      }
      item.appendChild(keyList);
    }
    els.envImportDetails.appendChild(item);
  }

  /** Return the merge or complete-replacement projection selected in the UI. */
  function selectedEnvImportPreview(preview = state.envImportPreview) {
    if (!preview) return null;
    if (!preview.replacement) return preview;
    return els.replaceMissingEnvInput.checked ? preview.replacement : preview;
  }

  function renderEnvImportPreview(preview) {
    const previousImportId = state.envImportPreview?.importId || '';
    state.envImportPreview = preview || null;
    els.envImportReview.hidden = !state.envImportPreview;
    if (!state.envImportPreview) {
      els.replaceMissingEnvInput.checked = false;
      els.replaceMissingEnvInput.disabled = true;
      els.envImportReplacementImpact.textContent = '';
      els.envImportSummary.innerHTML = '';
      els.envImportDetails.innerHTML = '';
      return;
    }
    if (previousImportId !== preview.importId) {
      els.replaceMissingEnvInput.checked = false;
    }
    // Older or failed preview responses may not include a replacement
    // projection. Force merge mode and make the unavailable choice inert so
    // the request always matches the impact currently rendered below.
    if (!preview.replacement) els.replaceMissingEnvInput.checked = false;
    els.replaceMissingEnvInput.disabled = state.busy || !preview.replacement;
    const displayPreview = selectedEnvImportPreview(preview);

    // A new selection supersedes the outcome of the previous import. Keep the
    // result visible at all other times so it does not disappear during the
    // ordinary editor re-render that follows a successful apply.
    renderEnvImportResult();

    const validationErrors = Object.entries(displayPreview.validationErrors || {});
    const hasValidationErrors = validationErrors.length > 0;
    els.envImportReview.dataset.state = hasValidationErrors ? 'error' : 'ready';
    els.envImportSource.textContent = displayPreview.sourceFile || '';
    els.envImportBadge.className = `tag badge ${hasValidationErrors ? 'badge-error' : 'badge-warn'}`;
    els.envImportBadge.textContent = hasValidationErrors
      ? launcherT('launcher_ui_needs_fixes', 'Needs fixes')
      : launcherT('launcher_ui_ready_to_apply', 'Ready to apply');
    els.applyEnvImportButton.disabled = hasValidationErrors || state.busy;
    els.envImportReplacementImpact.textContent = displayPreview.replaceMissing
      ? launcherT(
        'launcher_ui_replacement_impact',
        '{resetCount} known variables will return to defaults; {removeCount} custom variables will be removed.',
        {
          resetCount: displayPreview.resetKnownCount || 0,
          removeCount: displayPreview.removedCustomCount || 0,
        },
      )
      : launcherT(
        'launcher_ui_merge_impact',
        'Variables missing from the file will keep their current values.',
      );

    els.envImportSummary.innerHTML = '';
    const summaryItems = [
      [launcherT('launcher_ui_imported', 'Imported'), displayPreview.importedCount || 0],
      [launcherT('launcher_ui_changed', 'Changed'), displayPreview.changedCount || 0],
      [launcherT('launcher_ui_new', 'New'), displayPreview.newCount || 0],
      [launcherT('launcher_ui_custom', 'Custom'), displayPreview.customCount || 0],
      [launcherT('launcher_ui_unchanged', 'Unchanged'), displayPreview.unchangedCount || 0],
    ];
    for (const [label, value] of summaryItems) {
      const statistic = document.createElement('div');
      statistic.className = 'env-import-stat';
      const labelElement = document.createElement('dt');
      labelElement.className = 'env-import-stat-label';
      labelElement.textContent = label;
      const valueElement = document.createElement('dd');
      valueElement.className = 'env-import-stat-value';
      valueElement.textContent = String(value);
      statistic.append(labelElement, valueElement);
      els.envImportSummary.appendChild(statistic);
    }

    els.envImportDetails.innerHTML = '';
    if (displayPreview.changedKeys?.length) {
      appendImportDetail('Will update', { keys: displayPreview.changedKeys }, 'info');
    }
    // In replacement mode every key present in the selected file is already
    // authoritative. Repeating that its custom keys are "kept" adds an
    // unchanged-only card that competes with the actual reset/removal impact.
    if (!displayPreview.replaceMissing && displayPreview.customKeys?.length) {
      appendImportDetail('Custom keys will be kept', { keys: displayPreview.customKeys }, 'info');
    }
    if (displayPreview.replaceMissing && displayPreview.resetKnownCount) {
      appendImportDetail('Missing known keys will reset', {
        body: launcherT(
          'launcher_ui_missing_known_reset_body',
          '{resetCount} known variables will return to launcher defaults.',
          { resetCount: displayPreview.resetKnownCount },
        ),
        keys: displayPreview.resetKnownKeys,
      }, 'warn');
    } else if (displayPreview.missingKnownCount) {
      const missingKnownCount = displayPreview.missingKnownCount;
      appendImportDetail('Missing keys are safe', {
        body: missingKnownCount === 1
          ? launcherT(
            'launcher_ui_one_known_key_is_not_in_the_import_file',
            '1 known key is not in the import file and will stay unchanged.',
          )
          : launcherT(
            'launcher_ui_count_known_keys_are_not_in_the_import_file',
            '{count} known keys are not in the import file and will stay unchanged.',
            { count: missingKnownCount },
          ),
      }, 'info');
    }
    if (displayPreview.replaceMissing && displayPreview.removedCustomCount) {
      appendImportDetail('Custom keys will be removed', {
        body: launcherT(
          'launcher_ui_missing_custom_remove_body',
          '{removeCount} custom variables not in the import file will be removed.',
          { removeCount: displayPreview.removedCustomCount },
        ),
        keys: displayPreview.removedCustomKeys,
      }, 'warn');
    }
    if (displayPreview.duplicateKeys?.length) {
      appendImportDetail('Duplicate keys', {
        body: 'The last value is used for these keys.',
        keys: displayPreview.duplicateKeys,
      }, 'warn');
    }
    if (displayPreview.invalidLines?.length) {
      const invalidLineCount = displayPreview.invalidLines.length;
      appendImportDetail('Ignored lines', {
        body: invalidLineCount === 1
          ? launcherT(
            'launcher_ui_one_line_is_not_a_key_value_assignment',
            '1 line is not a KEY=value assignment and will be ignored.',
          )
          : launcherT(
            'launcher_ui_count_lines_are_not_key_value_assignments',
            '{count} lines are not KEY=value assignments and will be ignored.',
            { count: invalidLineCount },
          ),
      }, 'warn');
    }
    if (displayPreview.missingRequiredKeys?.length) {
      appendImportDetail('Required keys still missing', { keys: displayPreview.missingRequiredKeys }, 'warn');
    }
    for (const [key, error] of validationErrors.slice(0, 8)) {
      appendImportDetail(key, { body: translatedErrorMessage(error) }, 'error');
    }
  }

  /** Render a persistent, high-contrast result beneath the import review. */
  function renderEnvImportResult(title = '', message = '', stateName = 'success') {
    const visible = Boolean(title || message);
    els.envImportResult.hidden = !visible;
    els.envImportResult.dataset.state = stateName;
    els.envImportResultTitle.textContent = title;
    els.envImportResultMessage.textContent = message;
  }

  async function clearEnvImportPreview() {
    const importId = state.envImportPreview?.importId;
    renderEnvImportPreview(null);
    if (!importId) return;
    try {
      await window.omlorixServer.discardEnvImport(importId);
    } catch (error) {
      appendConsole(`.env import cleanup failed: ${translatedErrorMessage(error)}`);
    }
  }

  function setCustomEnvKeyError(message) {
    const text = String(message || '');
    els.customEnvKeyError.textContent = text;
    els.customEnvKeyError.hidden = !text;
    els.customEnvKeyInput.setAttribute('aria-invalid', text ? 'true' : 'false');
  }

  function shouldShowEnvField(field) {
    if (state.envRemovedKeys.has(String(field.key || '').trim())) return false;
    if (SETTINGS_OWNED_ENV_KEYS.has(String(field.key || '').trim())) return false;

    const mode = CONNECTION_MODE_ENV_FIELDS.get(String(field.key || '').trim());
    if (mode) {
      const toggles = getTogglesFromInputs();
      if (Boolean(toggles[mode.toggle]) !== mode.showWhen) return false;
    }

    const section = state.envFilter.section;
    const search = normalizeText(state.envFilter.search).trim();
    if (section !== 'all' && field.section !== section) return false;
    if (!search) return true;
    return [
      field.key,
      field.label,
      field.description,
      field.section,
    ].some((part) => normalizeText(part).includes(search));
  }

  function settingsInputForEnvKey(key) {
    const elementKey = SETTINGS_INPUT_BY_ENV_KEY.get(String(key || '').trim());
    return elementKey ? els[elementKey] : null;
  }

  function updateEnvField(key, updates) {
    if (!state.envEditor?.fields) return;
    const field = state.envEditor.fields.find((candidate) => candidate.key === key);
    if (!field) return;
    Object.assign(field, updates);
  }

  function removeCustomEnvField(key) {
    const normalizedKey = String(key || '').trim();
    const field = state.envEditor?.fields?.find((candidate) => candidate.key === normalizedKey);
    if (!field || field.known) return;
    state.envRemovedKeys.add(normalizedKey);
    delete state.envValidationErrors[normalizedKey];
    markEnvDirty();
    renderEnvEditor();
  }

  function applyPasteFallback(event, control, syncValue) {
    const pasteHelper = window.omlorixEnvEditorPaste;
    if (pasteHelper && pasteHelper.applyTextPaste(event, control)) {
      return;
    }

    // If the helper cannot access clipboardData, let Electron perform the
    // native paste and then mirror the finished DOM value into renderer state.
    queueMicrotask(syncValue);
  }

  function bindEnvTextControl(control, syncValue) {
    control.addEventListener('input', syncValue);
    control.addEventListener('paste', (event) => applyPasteFallback(event, control, syncValue));
  }

  function createEnvControl(field) {
    const describedBy = `env-help-${field.key}`;
    let control;

    if (field.secret) {
      control = document.createElement('input');
      control.type = 'password';
      control.className = 'env-secret-input';
      control.autocomplete = 'off';
      control.autocapitalize = 'off';
      control.autocorrect = 'off';
      control.spellcheck = false;
      control.placeholder = field.placeholder || 'Not set';
      control.value = field.value || '';
      bindEnvTextControl(control, () => {
        updateEnvField(field.key, { value: control.value, clearSecret: false, dirty: true });
        const clear = document.querySelector(`[data-env-clear="${field.key}"]`);
        if (clear) clear.checked = false;
        markEnvDirty();
      });
    } else if (field.type === 'boolean') {
      control = document.createElement('select');
      for (const value of ['', 'true', 'false']) {
        if (!value && field.required) continue;
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value || 'Unset';
        control.appendChild(option);
      }
      control.value = field.value || '';
      control.addEventListener('change', () => {
        updateEnvField(field.key, { value: control.value, dirty: true });
        markEnvDirty();
      });
    } else if (field.type === 'enum' && Array.isArray(field.options) && field.options.length) {
      control = document.createElement('select');
      if (!field.required) {
        const empty = document.createElement('option');
        empty.value = '';
        empty.textContent = 'Unset';
        control.appendChild(empty);
      }
      for (const value of field.options) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        control.appendChild(option);
      }
      control.value = field.value || '';
      control.addEventListener('change', () => {
        updateEnvField(field.key, { value: control.value, dirty: true });
        markEnvDirty();
      });
    } else {
      const useTextarea = String(field.value || '').length > 90 || /(JSON|COMMAND|URL|REPOSITORY|PATH)$/i.test(field.key);
      control = document.createElement(useTextarea ? 'textarea' : 'input');
      if (control.tagName === 'INPUT') {
        control.type = field.type === 'port' || field.type === 'integer' ? 'text' : 'text';
      }
      control.autocomplete = 'off';
      control.spellcheck = false;
      control.value = field.value || '';
      control.placeholder = field.placeholder || '';
      bindEnvTextControl(control, () => {
        updateEnvField(field.key, { value: control.value, dirty: true });
        markEnvDirty();
      });
    }

    control.dataset.envKey = field.key;
    control.id = `env-field-${field.key}`;
    control.setAttribute('aria-describedby', describedBy);
    if (state.envValidationErrors[field.key]) {
      control.setAttribute('aria-invalid', 'true');
    }
    return control;
  }

  function renderEnvField(field) {
    const row = document.createElement('div');
    row.className = 'env-field';
    row.dataset.envFieldKey = field.key;
    if (state.envValidationErrors[field.key]) {
      row.classList.add('has-error');
    }

    const meta = document.createElement('div');
    meta.className = 'env-field-meta';

    const label = document.createElement('label');
    label.htmlFor = `env-field-${field.key}`;
    label.textContent = field.key;

    const badges = document.createElement('div');
    badges.className = 'env-field-badges';
    if (field.secret) {
      const secretBadge = document.createElement('span');
      secretBadge.className = 'env-chip';
      secretBadge.textContent = 'Secret';
      badges.appendChild(secretBadge);
    }
    if (field.required) {
      const requiredBadge = document.createElement('span');
      requiredBadge.className = 'env-chip env-chip-required';
      requiredBadge.textContent = 'Required';
      badges.appendChild(requiredBadge);
    }
    if (!field.known) {
      const customBadge = document.createElement('span');
      customBadge.className = 'env-chip';
      customBadge.textContent = 'Custom';
      badges.appendChild(customBadge);
    }

    const description = document.createElement('p');
    description.id = `env-help-${field.key}`;
    description.textContent = launcherT(
      field.descriptionKey,
      field.description || field.label || '',
    );

    meta.append(label, badges, description);

    const controlWrap = document.createElement('div');
    controlWrap.className = 'env-field-control';
    const control = createEnvControl(field);
    if (field.secret) {
      const secretWrap = document.createElement('div');
      secretWrap.className = 'secret-input-wrap';
      const revealButton = document.createElement('button');
      revealButton.className = 'secret-reveal-button';
      revealButton.type = 'button';
      secretWrap.append(control, revealButton);
      controlWrap.appendChild(secretWrap);
      bindSecretRevealButton(revealButton, control, field.key);
    } else {
      controlWrap.appendChild(control);
    }

    if (field.secret) {
      const clearLabel = document.createElement('label');
      clearLabel.className = 'env-clear-secret';
      const clearInput = document.createElement('input');
      clearInput.type = 'checkbox';
      clearInput.dataset.envClear = field.key;
      clearInput.checked = Boolean(field.clearSecret);
      clearInput.addEventListener('change', () => {
        updateEnvField(field.key, { clearSecret: clearInput.checked, dirty: true });
        const secretInput = document.getElementById(`env-field-${field.key}`);
        if (secretInput) {
          secretInput.value = clearInput.checked ? '' : field.value || '';
        }
        markEnvDirty();
      });
      const clearText = document.createElement('span');
      clearText.textContent = 'Clear existing value';
      clearLabel.append(clearInput, clearText);
      controlWrap.appendChild(clearLabel);
    }

    if (!field.known) {
      const removeButton = document.createElement('button');
      removeButton.className = 'btn btn-ghost btn-sm env-remove-custom';
      removeButton.type = 'button';
      removeButton.textContent = 'Remove variable';
      removeButton.setAttribute(
        'aria-label',
        launcherT(
          'launcher_ui_remove_custom_environment_variable_value1',
          'Remove custom environment variable {value1}',
          { value1: field.key },
        ),
      );
      removeButton.addEventListener('click', () => removeCustomEnvField(field.key));
      controlWrap.appendChild(removeButton);
    }

    const errorText = translatedErrorMessage(state.envValidationErrors[field.key]);
    if (errorText) {
      const error = document.createElement('p');
      error.className = 'field-error';
      error.textContent = errorText;
      controlWrap.appendChild(error);
    }

    row.append(meta, controlWrap);
    return row;
  }

  function renderEnvSectionFilter() {
    const current = state.envFilter.section;
    els.envSectionFilter.innerHTML = '';
    const all = document.createElement('option');
    all.value = 'all';
    all.textContent = launcherT('launcher_ui_all_sections', 'All sections');
    els.envSectionFilter.appendChild(all);
    for (const group of state.envEditor?.groups || []) {
      const option = document.createElement('option');
      option.value = group;
      const groupField = state.envEditor?.fields?.find((field) => field.section === group);
      option.textContent = launcherT(groupField?.sectionKey, group);
      els.envSectionFilter.appendChild(option);
    }
    els.envSectionFilter.value = Array.from(els.envSectionFilter.options).some((option) => option.value === current)
      ? current
      : 'all';
    rebuildCustomSelect(els.envSectionFilter);
  }

  function renderEnvEditor() {
    const editor = state.envEditor;
    if (!editor) return;
    els.envEditorPath.textContent = editor.envFile || '';
    renderEnvSectionFilter();
    updateEnvValidationSummary();
    destroySelectEnhancementsIn(els.envFields);
    els.envFields.innerHTML = '';
    const fields = (editor.fields || []).filter(shouldShowEnvField);
    if (!fields.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-env-fields';
      empty.textContent = launcherT('launcher_env_empty_filter', 'No variables match the current filter.');
      els.envFields.appendChild(empty);
      return;
    }

    let lastSection = '';
    for (const field of fields) {
      if (field.section !== lastSection) {
        const heading = document.createElement('h3');
        heading.className = 'env-section-title';
        heading.textContent = launcherT(
          field.sectionKey || 'launcher_ui_env_section_general',
          field.section || 'General',
        );
        els.envFields.appendChild(heading);
        lastSection = field.section;
      }
      els.envFields.appendChild(renderEnvField(field));
    }
    enhanceSelectsIn(els.envFields);
  }

  async function loadEnvEditor(options = {}) {
    if (!options.force && hasDirtyEnvEditor()) return false;
    if (state.envReloadInFlight) return false;
    state.envReloadInFlight = true;
    if (!options.silent) {
      els.envEditorSaved.textContent = launcherT('launcher_env_status_reloading', 'Reloading');
    }
    try {
      state.envEditor = await window.omlorixServer.getEnvEditor();
      state.envValidationErrors = {};
      state.envRemovedKeys.clear();
      els.envEditorSaved.textContent = launcherT('launcher_env_status_ready', 'Ready');
      renderEnvEditor();
      return true;
    } catch (error) {
      els.envEditorSaved.textContent = launcherT('launcher_env_status_error', 'Error');
      if (!options.silent) {
        appendConsole(launcherT('launcher_env_editor_failed', '.env editor failed: {error}', {
          error: translatedErrorMessage(error),
        }));
      }
      return false;
    } finally {
      state.envReloadInFlight = false;
    }
  }

  function collectEnvEditorPayload() {
    const values = {};
    const clearSecrets = [];
    const removeKeys = Array.from(state.envRemovedKeys);
    for (const field of state.envEditor?.fields || []) {
      if (state.envRemovedKeys.has(field.key)) continue;
      if (field.secret) {
        if (field.clearSecret) {
          clearSecrets.push(field.key);
          values[field.key] = '';
        } else if (field.dirty) {
          values[field.key] = field.value || '';
        }
        continue;
      }
      if (field.dirty) {
        values[field.key] = field.value || '';
      }
    }
    return { values, clearSecrets, removeKeys };
  }

  async function saveEnvEditorNow(options = {}) {
    if (!state.envEditor || (!hasDirtyEnvEditor() && !state.envSaveRequested)) return true;
    if (state.envSaving) {
      state.envSaveRequested = true;
      return false;
    }

    state.envSaving = true;
    state.envSaveRequested = false;
    if (state.envAutosaveTimer) {
      window.clearTimeout(state.envAutosaveTimer);
      state.envAutosaveTimer = null;
    }
    const saveStartedAtVersion = state.envEditVersion;
    els.envEditorSaved.textContent = launcherT('launcher_env_status_saving', 'Saving');

    try {
      const result = await window.omlorixServer.saveEnvEditor(collectEnvEditorPayload());
      const hasNewerLocalEdits = state.envEditVersion !== saveStartedAtVersion;
      if (result && result.ok === false) {
        if (hasNewerLocalEdits) {
          state.envSaveRequested = true;
          els.envEditorSaved.textContent = launcherT('launcher_env_status_saving_changes', 'Saving changes');
          return false;
        }
        state.envValidationErrors = result.validationErrors || {};
        els.envEditorSaved.textContent = launcherT('launcher_env_status_fix_errors', 'Fix errors');
        renderEnvEditor();
        return false;
      }

      const hasQueuedSave = state.envSaveRequested || hasNewerLocalEdits;
      if (!hasQueuedSave) {
        state.envValidationErrors = {};
        state.envRemovedKeys.clear();
      }

      if (!hasQueuedSave) {
        state.envEditor = result.editor;
        els.envEditorSaved.textContent = result.changed
          ? launcherT('launcher_env_status_saved_with_backup', 'Saved with backup')
          : launcherT('launcher_env_status_no_changes', 'No changes');
        renderState(result.state);
        renderEnvEditor();
      } else {
        els.envEditorSaved.textContent = launcherT('launcher_env_status_saving_changes', 'Saving changes');
        renderState(result.state, { hydrateForm: false });
      }

      if (result.changed && !options.silent) {
        appendConsole(launcherT(
          'launcher_env_saved_backup_restart',
          '.env saved. Backup: {backupFile}\nRestart Omlorix for all changes to take effect.',
          { backupFile: result.backupFile },
        ));
      }
      return true;
    } catch (error) {
      els.envEditorSaved.textContent = launcherT('launcher_env_status_error', 'Error');
      appendConsole(launcherT('launcher_env_save_failed', '.env save failed: {error}', {
        error: translatedErrorMessage(error),
      }));
      return false;
    } finally {
      state.envSaving = false;
      if (state.envSaveRequested) {
        state.envSaveRequested = false;
        queueEnvAutosave(0);
      }
    }
  }

  function serviceIconElement() {
    return Icons.createSvgElement(Icons.server);
  }

  function serviceTag(kind, text, withDot = false) {
    const tag = document.createElement('span');
    tag.className = `tag ${kind}`;
    if (withDot) {
      const dot = document.createElement('span');
      dot.className = `dot ${kind === 'error' ? 'err' : kind}`;
      dot.setAttribute('aria-hidden', 'true');
      tag.appendChild(dot);
    }
    tag.append(document.createTextNode(text));
    return tag;
  }

  function serviceStateKind(rawState) {
    const stateName = String(rawState || '').toLowerCase();
    if (stateName === 'running') return 'ok';
    if (stateName === 'restarting' || stateName === 'created' || stateName === 'paused') return 'warn';
    if (stateName === 'exited' || stateName === 'dead' || stateName === 'removing') return 'err';
    return 'muted';
  }

  function serviceHealthKind(service, rawState) {
    if (service.Missing === true || String(rawState || '').toLowerCase() === 'not_created') {
      return {
        kind: 'muted',
        text: launcherT('launcher_service_not_running', 'not running'),
      };
    }
    const healthText = String(service.Health || service.health || service.Status || service.status || '').toLowerCase();
    if (healthText.includes('unhealthy')) return { kind: 'err', text: 'unhealthy' };
    if (healthText.includes('healthy')) return { kind: 'ok', text: 'healthy' };
    if (healthText.includes('starting')) return { kind: 'warn', text: 'starting' };
    if (String(rawState || '').toLowerCase() === 'running') return { kind: 'ok', text: 'running' };
    if (String(rawState || '').toLowerCase() === 'exited') return { kind: 'err', text: 'down' };
    return { kind: 'muted', text: 'unknown' };
  }

  function serviceUptime(service) {
    const status = String(service.Status || service.status || '').trim();
    const match = status.match(/(?:Up|Running)\s+(.+?)(?:\s+\(|$)/i);
    if (match?.[1]) return match[1].trim();
    if (/^up$/i.test(status)) return 'running';
    return service.Uptime || service.uptime || '—';
  }

  /** Keep the Services-page hint synchronized with the active polling cadence. */
  function renderServiceStatusRefreshCadence() {
    if (state.busy) {
      els.serviceAutoRefreshStatus.textContent = launcherT(
        'launcher_services_auto_refresh_active',
        'Updates every 2 seconds while an action is running',
      );
      return;
    }
    els.serviceAutoRefreshStatus.textContent = launcherT(
      'launcher_services_auto_refresh',
      'Updates every 10 seconds',
    );
  }

  /** Resolve service action copy from hardcoded, auditable translation keys. */
  function serviceActionLabel(action) {
    if (action === 'start') return launcherT('launcher_ui_start', 'Start');
    if (action === 'stop') return launcherT('launcher_ui_stop', 'Stop');
    if (action === 'restart') return launcherT('launcher_ui_restart', 'Restart');
    if (action === 'logs') return launcherT('launcher_ui_logs', 'Logs');
    return String(action || '');
  }

  /** Localize known service actions while preserving the Compose service ID. */
  function serviceOperationName(name) {
    if (name === 'Backup download') {
      return launcherT('launcher_ui_backup_download_operation', 'Backup download');
    }
    if (name === 'Visitor IP repair') {
      return launcherT('launcher_ui_visitor_ip_repair_operation', 'Visitor IP repair');
    }
    const match = /^(start|stop|restart) (.+)$/.exec(String(name || ''));
    return match ? `${serviceActionLabel(match[1])} ${match[2]}` : String(name || '');
  }

  function renderServices(stack = {}) {
    const rows = Array.isArray(stack.services) ? stack.services : [];
    const running = Number.isFinite(Number(stack.running)) ? Number(stack.running) : 0;
    const total = Number.isFinite(Number(stack.total)) ? Number(stack.total) : rows.length;
    const complete = expectedServicesAreRunning(stack);
    renderLogServiceOptions(rows);
    els.servicesSubtitle.textContent = launcherT(
      'launcher_services_subtitle',
      'Expected services and their current container state.',
    );
    renderServiceStatusRefreshCadence();
    els.serviceCount.className = `tag ${complete ? 'ok' : running > 0 ? 'warn' : 'muted'} quiet-label`;
    els.serviceCount.textContent = launcherT(
      'launcher_services_running_count',
      '{running}/{total} running',
      { running, total },
    );
    if (!rows.length) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 5;
      td.className = 'empty-cell';
      td.textContent = launcherT('launcher_services_empty', 'No services are configured.');
      tr.appendChild(td);
      els.servicesBody.replaceChildren(tr);
      return;
    }
    els.servicesBody.innerHTML = '';
    for (const service of rows) {
      const tr = document.createElement('tr');
      const name = document.createElement('td');
      const stateCell = document.createElement('td');
      const healthCell = document.createElement('td');
      const uptimeCell = document.createElement('td');
      const actionsCell = document.createElement('td');
      const rawState = String(service.State || service.state || 'unknown');
      const missing = service.Missing === true || rawState.toLowerCase() === 'not_created';
      const stateKind = serviceStateKind(rawState);
      const health = serviceHealthKind(service, rawState);
      if (missing) tr.classList.add('service-missing');
      const nameWrap = document.createElement('span');
      nameWrap.className = 'svc-name';
      nameWrap.append(serviceIconElement(), document.createTextNode(service.Service || service.Name || service.Names || 'unknown'));
      name.appendChild(nameWrap);
      stateCell.appendChild(serviceTag(
        stateKind,
        missing
          ? launcherT('launcher_service_not_created', 'not created')
          : (rawState || 'unknown'),
      ));
      healthCell.appendChild(serviceTag(health.kind, health.text, true));
      uptimeCell.className = 'svc-uptime';
      uptimeCell.textContent = serviceUptime(service);
      actionsCell.className = 'service-actions-cell';
      const actionsWrap = document.createElement('div');
      actionsWrap.className = 'service-actions';
      const serviceName = String(service.Service || service.Name || service.Names || '').trim();
      const isRunning = rawState.toLowerCase() === 'running';
      const actions = isRunning ? ['stop', 'restart', 'logs'] : ['start', 'logs'];
      for (const action of actions) {
        const button = document.createElement('button');
        const label = serviceActionLabel(action);
        button.type = 'button';
        button.className = 'btn btn-ghost service-action-button';
        button.dataset.serviceAction = action;
        button.dataset.serviceName = serviceName;
        button.textContent = label;
        button.setAttribute('aria-label', `${label} ${serviceName}`);
        button.disabled = !serviceName
          || dockerActionsBlocked()
          || (action === 'logs' ? logDiagnosticsActive() : state.busy || envActionsBlocked());
        actionsWrap.appendChild(button);
      }
      actionsCell.appendChild(actionsWrap);
      tr.append(name, stateCell, healthCell, uptimeCell, actionsCell);
      els.servicesBody.appendChild(tr);
    }
  }

  function renderVisitorIpStatus(visitorIp) {
    const status = visitorIp || {};
    if (status.ready) state.visitorIpRepairFailure = '';
    const repairFailure = state.visitorIpRepairFailure;
    const level = repairFailure ? 'error' : (status.level || 'warn');
    const heading = launcherT('launcher_visitor_ips_heading', 'Visitor IPs');
    const statusTitle = repairFailure
      ? launcherT('launcher_visitor_ip_title_repair_failed', 'Automatic fix failed')
      : launcherT(
        status.titleKey || 'launcher_visitor_ip_title_needs_setup',
        status.title || 'Needs setup',
      );
    const title = `${heading}: ${statusTitle}`;
    let message = repairFailure
      ? launcherT(
        'launcher_visitor_ip_message_repair_failed',
        '{error} Make sure Omlorix is running and ready, then try again. See Console for details.',
        { error: repairFailure },
      )
      : launcherT(
        status.messageKey || 'launcher_visitor_ip_message_needs_setup',
        status.message || 'Enable trusted proxy headers so rate limits, audit logs, auth checks, and access logs use the visitor IP.',
      );

    // The health probe deliberately targets Docker's private frontend port so
    // it cannot validate an external visitor. Label it precisely and omit it
    // while the launcher proxy is running, where it would otherwise look like
    // evidence that forwarding is still broken.
    if (!repairFailure && status.observedIp && !status.proxyRunning) {
      message += launcherT(
        'launcher_visitor_ip_direct_probe',
        ' Direct Docker probe sees {ip}.',
        { ip: status.observedIp },
      );
    }

    // Keep the main setup screen focused on work that still needs attention.
    // A stopped launcher proxy is not ready even when its trust settings have
    // already been written, so keep that actionable warning on the dashboard.
    els.visitorIpCard.hidden = Boolean(status.ready);
    els.visitorIpCard.dataset.level = level;
    els.visitorIpDot.className = `visitor-ip-dot visitor-ip-dot-${level}`;
    els.visitorIpTitle.textContent = title;
    els.visitorIpDescription.textContent = message;
    els.fixVisitorIpsButton.textContent = launcherT(
      'launcher_visitor_ip_action_open_proxy',
      'Open proxy settings',
    );

    els.proxyVisitorIpCard.dataset.level = level;
    els.proxyVisitorIpDot.className = `visitor-ip-dot visitor-ip-dot-${level}`;
    els.proxyVisitorIpTitle.textContent = title;
    els.proxyVisitorIpDescription.textContent = message;
    els.proxyFixVisitorIpsButton.textContent = status.recommendedAction === 'restart-omlorix'
      ? launcherT('launcher_visitor_ip_action_restart_omlorix', 'Restart Omlorix')
      : status.recommendedAction === 'start-proxy'
        ? launcherT('launcher_visitor_ip_action_start_proxy', 'Start proxy')
        : status.configured
          ? launcherT('launcher_visitor_ip_action_reapply', 'Reapply settings')
          : launcherT('launcher_visitor_ip_action_fix', 'Fix automatically');
  }

  function openProxySection(focusElement = null) {
    const proxyNav = document.querySelector('.sidebar-nav .nav-link[data-section="proxy"]');
    if (proxyNav) {
      proxyNav.click();
    }
    if (focusElement) {
      focusElement.focus();
    }
  }

  function renderEnvRequirements(requirements) {
    const status = requirements || {};
    const issues = Array.isArray(status.issues) ? status.issues : [];
    const generalIssues = [];
    const groupedToggleIssues = new Map();

    for (const issue of issues) {
      const toggleKey = toggleRequirementTargetForKey(issue.key);
      if (toggleKey) {
        if (!groupedToggleIssues.has(toggleKey)) {
          groupedToggleIssues.set(toggleKey, []);
        }
        groupedToggleIssues.get(toggleKey).push(issue);
        continue;
      }
      generalIssues.push(issue);
    }

    renderToggleRequirementErrors(groupedToggleIssues);

    const blocked = status.ok === false && generalIssues.length > 0;
    els.envRequirementsCard.hidden = !blocked;
    if (!blocked) {
      els.envRequirementsList.innerHTML = '';
      return;
    }

    els.envRequirementsTitle.textContent = 'Environment needs setup';
    els.envRequirementsDescription.textContent = 'Set these required .env variables before running start, stop, restart, update, backup, restore, or repair actions.';
    els.envRequirementsList.innerHTML = '';
    for (const issue of generalIssues.slice(0, 12)) {
      const chip = document.createElement('span');
      chip.className = 'env-requirements-key';
      chip.title = issue.message || '';
      chip.textContent = issue.key;
      els.envRequirementsList.appendChild(chip);
    }
  }

  function dockerSetupSteps(docker) {
    const waitingStep = state.dockerReadinessPoll.active
      ? 'Keep Docker open; this launcher is refreshing the status automatically.'
      : 'Refresh this launcher.';
    if (state.dockerReadinessPoll.active && state.dockerReadinessPoll.mode === 'install' && !docker.installed) {
      return [
        'Install Docker from the official setup page that opened in your browser.',
        'Open Docker Desktop or start Docker Engine after installation.',
        'Finish any Docker first-run setup in the Docker GUI.',
        'Keep this launcher open; it is checking for Docker automatically.',
      ];
    }
    const platform = window.omlorixServer.platform;
    if (!docker.installed) {
      if (platform === 'darwin' || platform === 'win32') {
        return ['Install Docker Desktop from the official Docker installer.', 'Open Docker Desktop and finish its first-run setup.', state.dockerReadinessPoll.active ? waitingStep : 'Refresh this launcher when Docker says it is running.'];
      }
      return ['Install Docker Engine and the Docker Compose plugin for this Linux distribution.', 'Start the Docker service and make sure your user can run docker commands.', state.dockerReadinessPoll.active ? waitingStep : 'Refresh this launcher after docker info works in a terminal.'];
    }
    if (!docker.running) {
      return ['Start Docker Desktop or the Docker Engine service.', 'Wait until Docker reports that it is running.', waitingStep];
    }
    return ['Install or repair the Docker Compose plugin.', 'Confirm docker compose version works in a terminal.', waitingStep];
  }

  function dockerSetupDescription(docker) {
    if (!state.dockerReadinessPoll.active || dockerIsReady(docker)) {
      return docker.message || 'Install Docker, start it, then refresh this status.';
    }
    if (state.dockerReadinessPoll.mode === 'install' && !docker.installed) {
      return window.omlorixServer.platform === 'win32'
        ? 'Install Docker Desktop in the Docker setup window. Omlorix is watching for Docker Desktop to appear.'
        : 'The Docker installer is running. Omlorix is watching for Docker to appear.';
    }
    if (!docker.running) {
      return 'Waiting for Docker Desktop to report that it is ready.';
    }
    return 'Docker is running. Waiting for Docker Compose to report that it is ready.';
  }

  function renderDockerStartingState(docker) {
    els.dockerSetupSteps.setAttribute('aria-live', 'polite');
    const item = document.createElement('li');
    item.className = 'docker-starting-state';
    const spinner = document.createElement('span');
    spinner.className = 'docker-starting-spinner';
    spinner.setAttribute('aria-hidden', 'true');
    const copy = document.createElement('span');
    copy.className = 'docker-starting-copy';
    const title = document.createElement('strong');
    title.textContent = state.dockerReadinessPoll.mode === 'install' && !docker.installed
      ? 'Installing Docker Desktop'
      : !docker.running
        ? 'Starting Docker'
        : 'Checking Docker Compose';
    const detail = document.createElement('span');
    detail.textContent = state.dockerReadinessPoll.mode === 'install'
      ? 'Checking every few seconds after opening the Docker setup guide.'
      : 'Refreshing every second for up to one minute.';
    copy.append(title, detail);
    item.append(spinner, copy);
    els.dockerSetupSteps.appendChild(item);

    for (const step of dockerSetupSteps(docker)) {
      const progressItem = document.createElement('li');
      progressItem.className = 'docker-progress-step';
      const marker = document.createElement('span');
      marker.className = 'docker-progress-marker';
      marker.setAttribute('aria-hidden', 'true');
      const text = document.createElement('span');
      text.textContent = step;
      progressItem.append(marker, text);
      els.dockerSetupSteps.appendChild(progressItem);
    }
  }

  function renderDockerSetup(docker) {
    const needsSetup = !docker.installed || !docker.running || !docker.compose;
    els.dockerSetupCard.hidden = !needsSetup;
    if (!needsSetup) return;

    const pollingDocker = state.dockerReadinessPoll.active && !dockerIsReady(docker);
    // A stopped, already-installed Docker instance needs a concise status
    // explanation rather than repeating recovery steps below the message.
    // Installation and Compose failures retain their more detailed guidance.
    const dockerStopped = !pollingDocker && docker.installed && !docker.running;
    els.dockerSetupCard.dataset.mode = pollingDocker ? 'active' : 'blocked';
    const setupTitle = pollingDocker
      ? state.dockerReadinessPoll.mode === 'install' && !docker.installed
        ? 'Installing Docker Desktop'
        : 'Starting Docker'
      : !docker.installed
      ? 'Docker is required'
      : !docker.running
        ? 'Docker is installed but not running'
        : 'Docker Compose is required';
    els.dockerSetupTitle.textContent = translateLauncherSource(setupTitle);

    if (dockerStopped) {
      // Keep the complete two-sentence message under one stable key. This
      // avoids interpolating the backend's English Docker message into an
      // otherwise translated sentence, which previously mixed languages.
      els.dockerSetupDescription.textContent = launcherT(
        'launcher_ui_docker_stopped_dashboard_actions_disabled',
        'Docker is installed, but Docker Desktop/Engine or Compose is not ready. Omlorix dashboard actions are disabled until Docker and Docker Compose are ready.',
      );
    } else {
      const localizedDescription = translateLauncherSource(dockerSetupDescription(docker));
      els.dockerSetupDescription.textContent = pollingDocker
        ? localizedDescription
        : launcherT(
          'launcher_ui_value1_omlorix_dashboard_actions_are_disabled_until_docker_and_docker',
          '{value1} Omlorix dashboard actions are disabled until Docker and Docker Compose are ready.',
          { value1: localizedDescription },
        );
    }
    els.openDockerSetupButton.textContent = !docker.installed ? 'Install manually' : 'Docker setup guide';
    els.startDockerDesktopButton.hidden = !docker.canStartDesktop || docker.running;
    els.startDockerDesktopButton.textContent = state.dockerReadinessPoll.active ? 'Checking Docker...' : 'Start Docker';
    els.dockerSetupSteps.innerHTML = '';
    els.dockerSetupSteps.hidden = dockerStopped;
    if (pollingDocker) {
      renderDockerStartingState(docker);
      return;
    }
    els.dockerSetupSteps.removeAttribute('aria-live');
    if (dockerStopped) return;
    for (const step of dockerSetupSteps(docker)) {
      const item = document.createElement('li');
      item.textContent = step;
      els.dockerSetupSteps.appendChild(item);
    }
  }

  function dockerMetricState(docker) {
    if (state.dockerReadinessPoll.active && state.dockerReadinessPoll.mode === 'install' && !docker.installed) {
      return {
        kind: 'info',
        text: 'Installing',
        detail: 'Docker setup is open; waiting for Docker to become available.',
        active: true,
      };
    }
    if (!docker.installed) {
      return {
        kind: 'error',
        text: 'Not installed',
        detail: docker.message || 'Install Docker Desktop or Docker Engine to run Omlorix.',
        active: false,
      };
    }
    if (state.dockerReadinessPoll.active && !dockerIsReady(docker)) {
      return {
        kind: 'info',
        text: docker.running ? 'Checking Compose' : 'Starting Docker',
        detail: docker.running
          ? 'Docker is running; waiting for Docker Compose to become ready.'
          : 'Docker Desktop or Engine is launching now.',
        active: true,
      };
    }
    if (!docker.running) {
      return {
        kind: 'warn',
        text: 'Not running',
        detail: 'Docker is installed but the daemon is stopped.',
        active: false,
      };
    }
    if (!docker.compose) {
      return {
        kind: 'warn',
        text: 'Compose missing',
        detail: 'Docker is running, but the Docker Compose plugin is unavailable.',
        active: false,
      };
    }
    return {
      kind: 'ok',
      text: 'Ready',
      detail: 'Docker and Docker Compose are ready.',
      active: false,
    };
  }

  function stackMetricState(stack, docker) {
    if (!dockerIsReady(docker)) {
      return {
        kind: 'muted',
        text: 'Unavailable',
        detail: 'Start Docker before checking Omlorix services.',
        active: false,
      };
    }
    if (!stack.total) {
      return {
        kind: 'muted',
        text: 'Stopped',
        detail: 'No Omlorix containers are currently running.',
        active: false,
      };
    }
    if (expectedServicesAreRunning(stack)) {
      return {
        kind: 'ok',
        text: launcherT(
          'launcher_services_running_count',
          '{running}/{total} running',
          { running: stack.running, total: stack.total },
        ),
        detail: launcherT(
          'launcher_stack_all_running_detail',
          'All expected Omlorix services are running.',
        ),
        active: false,
      };
    }
    if (stack.running === stack.total && Number(stack.healthIssues || 0) > 0) {
      return {
        kind: 'warn',
        text: launcherT(
          'launcher_services_running_count',
          '{running}/{total} running',
          { running: stack.running, total: stack.total },
        ),
        detail: launcherT(
          'launcher_stack_health_issues_detail',
          '{count} expected Omlorix services are not healthy yet.',
          { count: Number(stack.healthIssues || 0) },
        ),
        active: true,
      };
    }
    if (stack.running > 0) {
      return {
        kind: 'warn',
        text: launcherT(
          'launcher_services_running_count',
          '{running}/{total} running',
          { running: stack.running, total: stack.total },
        ),
        detail: launcherT(
          'launcher_stack_partial_running_detail',
          '{count} expected Omlorix services are not running.',
          { count: Math.max(0, Number(stack.total || 0) - Number(stack.running || 0)) },
        ),
        active: true,
      };
    }
    return {
      kind: 'warn',
      text: launcherT(
        'launcher_services_running_count',
        '{running}/{total} running',
        { running: 0, total: stack.total },
      ),
      detail: launcherT(
        'launcher_stack_none_running_detail',
        'None of the expected Omlorix services are running.',
      ),
      active: false,
    };
  }

  function endpointMetricState(stack, docker) {
    if (!dockerIsReady(docker)) {
      return {
        kind: 'muted',
        text: 'Unavailable',
        detail: 'Start Docker before checking the public endpoint.',
        active: false,
      };
    }
    if (stack.healthy) {
      return {
        kind: 'ok',
        text: 'Responding',
        detail: `${stack.url} is reachable.`,
        active: false,
      };
    }
    if (stack.running > 0) {
      return {
        kind: 'warn',
        text: 'Waiting',
        detail: stack.httpStatus
          ? `${stack.url} returned HTTP ${stack.httpStatus}.`
          : `${stack.url || 'The endpoint'} is not responding yet.`,
        active: true,
      };
    }
    return {
      kind: 'muted',
      text: 'Offline',
      detail: stack.url ? `${stack.url} will be checked after the stack starts.` : 'No endpoint is active.',
      active: false,
    };
  }

  function renderMetricStates(docker, stack) {
    const dockerState = dockerMetricState(docker);
    const stackState = stackMetricState(stack, docker);
    const endpointState = endpointMetricState(stack, docker);

    setMetricState(els.dockerMetric, els.dockerStatusLamp, dockerState.kind, dockerState.active);
    els.dockerStatus.textContent = dockerState.text;
    els.dockerStatusDetail.textContent = dockerState.detail;

    setMetricState(els.stackMetric, els.stackStatusLamp, stackState.kind, stackState.active);
    els.stackStatus.textContent = stackState.text;
    els.stackStatusDetail.textContent = stackState.detail;

    setMetricState(els.endpointMetric, els.endpointStatusLamp, endpointState.kind, endpointState.active);
    els.endpointStatus.textContent = endpointState.text;
    els.endpointStatusDetail.textContent = endpointState.detail;
  }

  function renderStatusHero(docker, stack) {
    const url = stack.url || 'the public endpoint';
    let level = 'muted';
    let title = 'Omlorix is stopped';
    let meta = 'Start the stack when Docker is ready.';

    if (!dockerIsReady(docker)) {
      level = !docker.installed ? 'error' : 'warn';
      title = !docker.installed
        ? 'Docker setup is required'
        : !docker.running
          ? 'Docker is not running'
          : 'Docker Compose is required';
      meta = 'Server actions are disabled until Docker and Docker Compose are ready.';
    } else if (stack.healthy && expectedServicesAreRunning(stack)) {
      level = 'ok';
      title = 'Omlorix is running';
      meta = `${url} is reachable.`;
    } else if (stack.running > 0) {
      level = 'warn';
      title = 'Omlorix is starting';
      meta = stack.httpStatus
        ? `${url} returned HTTP ${stack.httpStatus}.`
        : 'Containers are running while the endpoint comes online.';
    } else {
      level = 'muted';
      title = 'Omlorix is stopped';
      meta = 'Docker is ready. Start Omlorix when you want the server online.';
    }

    if (els.statusHero) {
      els.statusHero.dataset.state = level;
    }
    if (els.statusHeroIcon) {
      els.statusHeroIcon.className = `hero-pulse ${level === 'error' ? 'error' : level}`;
    }
    if (els.statusHeroMeta) {
      els.statusHeroMeta.textContent = meta;
    }
    const titleElement = document.getElementById('statusTitle');
    if (titleElement) {
      titleElement.textContent = title;
    }
  }

  /** Translate the release channel without exposing backend English labels. */
  function translatedServerUpdateChannel(channel) {
    return channel === 'beta'
      ? launcherT('launcher_server_update_channel_beta', 'Beta')
      : launcherT('launcher_server_update_channel_stable', 'Stable');
  }

  /** Render a launcher-only release notice unless a server dependency explains it. */
  function renderLauncherUpdateBanner() {
    const info = state.launcherUpdateInfo;
    const serverRequiresLauncher = Boolean(
      state.serverUpdateInfo?.updateAvailable
      && state.serverUpdateInfo?.launcherRequirement,
    );
    const available = Boolean(
      info?.status !== 'unsupported'
      && info?.updateAvailable
      && info?.latestVersion,
    );
    els.launcherUpdateBanner.hidden = !available || serverRequiresLauncher;
    if (!available || serverRequiresLauncher) return;

    const latestVersion = String(info.latestVersion);
    const currentVersion = String(info.currentVersion || 'unknown');
    const channel = translatedServerUpdateChannel(info.channel);
    els.launcherUpdateBanner.dir = launcherDirection();
    els.launcherUpdateLabel.textContent = launcherT(
      'launcher_launcher_update_label',
      'Launcher update',
    );
    els.launcherUpdateTitle.textContent = launcherT(
      'launcher_launcher_update_available_title',
      'Server Launcher {latestVersion} is available',
      { latestVersion },
    );
    els.launcherUpdateDescription.textContent = launcherT(
      'launcher_launcher_update_description',
      'Current: {currentVersion} · Channel: {channel}',
      { currentVersion, channel },
    );
    els.launcherUpdateButton.textContent = launcherT(
      'launcher_launcher_update_action',
      'Update launcher',
    );
    setBusy(state.busy);
  }

  /** Render the latest successful server-release check below the status cards. */
  function renderServerUpdateBanner() {
    const info = state.serverUpdateInfo;
    const available = Boolean(info?.updateAvailable && info?.latestVersion);
    els.serverUpdateBanner.hidden = !available;
    if (!available) return;
    els.serverUpdateBanner.dir = launcherDirection();

    const latestVersion = String(info.latestVersion);
    const currentVersion = String(info.currentVersion || 'unknown');
    const launcherRequirement = info.launcherRequirement || null;
    const channel = translatedServerUpdateChannel(info.channel);

    els.serverUpdateLabel.textContent = launcherT(
      'launcher_server_update_label',
      'Server update',
    );
    els.serverUpdateDescription.textContent = launcherT(
      'launcher_server_update_description',
      'Current: {currentVersion} · Channel: {channel}',
      { currentVersion, channel },
    );

    if (launcherRequirement) {
      const minimumLauncherVersion = launcherRequirement.minimumLauncherVersion || 'unknown';
      const launcherInfo = state.launcherUpdateInfo;
      const launcherFeedBehind = Boolean(
        launcherInfo
        && launcherInfo.status !== 'unsupported'
        && launcherInfo.availableVersionMeetsMinimum === false,
      );
      els.serverUpdateTitle.textContent = launcherT(
        'launcher_server_update_launcher_required_title',
        'A launcher update is required for Omlorix {latestVersion}',
        { latestVersion },
      );
      if (
        launcherInfo?.updateAvailable
        && launcherInfo.availableVersionMeetsMinimum === true
      ) {
        els.serverUpdateNote.textContent = launcherT(
          'launcher_server_update_launcher_ready_description',
          'Server Launcher {latestLauncherVersion} is available and meets the required minimum version {minimumLauncherVersion}. Update the launcher first.',
          {
            latestLauncherVersion: launcherInfo.latestVersion,
            minimumLauncherVersion,
          },
        );
      } else if (launcherFeedBehind) {
        els.serverUpdateNote.textContent = launcherT(
          'launcher_server_update_launcher_feed_behind_description',
          'The launcher feed currently offers {latestLauncherVersion}, but this Omlorix release requires {minimumLauncherVersion} or newer. Check again after a compatible launcher is published.',
          {
            latestLauncherVersion: launcherInfo.latestVersion || launcherInfo.currentVersion || 'unknown',
            minimumLauncherVersion,
          },
        );
      } else {
        els.serverUpdateNote.textContent = launcherT(
          'launcher_server_update_launcher_required_description',
          'Update Omlorix Server Launcher to {minimumLauncherVersion} or newer before installing this server release.',
          { minimumLauncherVersion },
        );
      }
      els.serverUpdateNote.hidden = false;
      els.serverUpdateButton.textContent = launcherFeedBehind
        ? launcherT(
            'launcher_server_update_launcher_check_action',
            'Check again',
          )
        : launcherT(
            'launcher_server_update_launcher_action',
            'Update launcher',
          );
      els.serverUpdateButton.removeAttribute('title');
    } else {
      els.serverUpdateTitle.textContent = launcherT(
        'launcher_server_update_available_title',
        'Omlorix {latestVersion} is available',
        { latestVersion },
      );
      els.serverUpdateButton.textContent = launcherT(
        'launcher_server_update_action',
        'Update to {latestVersion}',
        { latestVersion },
      );

      const updateBlocked = envActionsBlocked() || dockerActionsBlocked() || omlorixActionsBlocked();
      const blockedMessage = launcherT(
        'launcher_server_update_requires_running',
        'Start Omlorix and resolve any setup warnings before installing this update.',
      );
      els.serverUpdateNote.textContent = blockedMessage;
      els.serverUpdateNote.hidden = !updateBlocked;
      if (updateBlocked) {
        els.serverUpdateButton.title = blockedMessage;
      } else {
        els.serverUpdateButton.removeAttribute('title');
      }
    }

    // Reapply button guards whenever either release information or server
    // health changes, including when the banner switches to a launcher action.
    setBusy(state.busy);
    renderLauncherUpdateBanner();
  }

  /** Identify the release configuration associated with cached update data. */
  function serverUpdateFingerprint(data = state.current) {
    const env = data?.env || {};
    return `${String(env.OMLORIX_VERSION || '')}\n${String(data?.serverSettings?.updateChannel || '')}`;
  }

  function clearServerUpdateInfo() {
    state.serverUpdateInfo = null;
    renderServerUpdateBanner();
  }

  /** Check the launcher feed without downloading or installing an update. */
  async function refreshLauncherUpdateAvailability(options = {}) {
    if (!window.omlorixServer?.getLauncherUpdateInfo) return null;

    const requestId = state.launcherUpdateRequest + 1;
    state.launcherUpdateRequest = requestId;
    const requirement = state.serverUpdateInfo?.launcherRequirement;
    const channel = state.current?.serverSettings?.updateChannel || 'stable';
    if (
      options.force !== true
      && state.launcherUpdateRetryChannel === channel
      && Date.now() < state.launcherUpdateRetryAt
    ) return null;
    const minimumLauncherVersion = requirement?.minimumLauncherVersion || '';
    const requirementChanged = minimumLauncherVersion !== state.launcherUpdateMinimumVersion;
    try {
      const info = await window.omlorixServer.getLauncherUpdateInfo({
        channel,
        minimumLauncherVersion,
        // A newly discovered server dependency deserves a fresh launcher-feed
        // check even when the ordinary passive result is still cached.
        force: options.force === true || requirementChanged,
      });
      if (requestId !== state.launcherUpdateRequest) return null;
      if (info?.unavailable) {
        // Remember that this requirement was evaluated so repeated state
        // hydration cannot turn it into another forced feed request.
        state.launcherUpdateMinimumVersion = minimumLauncherVersion;
        state.launcherUpdateRetryChannel = channel;
        state.launcherUpdateRetryAt = Date.now() + RELEASE_CHECK_FAILURE_COOLDOWN_MS;
        return null;
      }
      state.launcherUpdateInfo = info || null;
      state.launcherUpdateMinimumVersion = minimumLauncherVersion;
      state.launcherUpdateRetryAt = 0;
      state.launcherUpdateRetryChannel = '';
      renderLauncherUpdateBanner();
      renderServerUpdateBanner();
      return info;
    } catch (error) {
      if (requestId !== state.launcherUpdateRequest) return null;
      // A passive launcher check must not turn a healthy dashboard into an
      // error state. Preserve the last successful result and let the explicit
      // update flow surface actionable feed or updater failures.
      if (!options.silent) {
        appendConsole(`Launcher update check failed: ${translatedErrorMessage(error)}`);
      }
      return null;
    }
  }

  /** Check the configured release channel without delaying ordinary status UI. */
  async function refreshServerUpdateAvailability(options = {}) {
    if (!window.omlorixServer?.checkServerUpdate || state.busy) return null;
    if (state.current?.setup?.required || state.current?.envRequirements?.ok === false) {
      state.serverUpdateInfo = null;
      renderServerUpdateBanner();
      return null;
    }

    const requestId = state.serverUpdateRequest + 1;
    state.serverUpdateRequest = requestId;
    const requestFingerprint = serverUpdateFingerprint();
    try {
      const info = await window.omlorixServer.checkServerUpdate();
      if (requestId !== state.serverUpdateRequest) return null;
      if (requestFingerprint !== serverUpdateFingerprint()) {
        clearServerUpdateInfo();
        return null;
      }
      state.serverUpdateInfo = info || null;
      renderServerUpdateBanner();
      return info;
    } catch (error) {
      if (requestId !== state.serverUpdateRequest) return null;
      if (requestFingerprint !== serverUpdateFingerprint()) {
        clearServerUpdateInfo();
        return null;
      }
      // Preserve a previously successful result during temporary feed/network
      // failures so an actionable update notice does not flicker away.
      if (!options.silent) {
        appendConsole(`Server update check failed: ${translatedErrorMessage(error)}`);
      }
      return null;
    }
  }

  async function refreshServerUpdateQuietly() {
    if (document.hidden) return;
    await refreshReleaseUpdateAvailability({ silent: true });
  }

  function startServerUpdateRefreshTimer() {
    if (state.serverUpdateRefreshTimer) return;
    state.serverUpdateRefreshTimer = window.setInterval(
      refreshServerUpdateQuietly,
      SERVER_UPDATE_REFRESH_INTERVAL_MS,
    );
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) refreshServerUpdateQuietly();
    });
  }

  /** Render every dashboard surface that depends on the current stack snapshot. */
  function renderStackSnapshot(docker, stack) {
    renderStatusHero(docker, stack);
    renderMetricStates(docker, stack);
    const sidebarBadge = sidebarBadgeState(docker, stack);
    setBadge(sidebarBadge.kind, sidebarBadge.text);
    els.overallBadge.title = sidebarBadge.title;
    els.overallBadge.setAttribute('aria-label', sidebarBadge.title);
    renderServices(stack);
  }

  /** Apply a focused status response without rehydrating or overwriting forms. */
  function applyServiceStatus(stack, requestedAt = Date.now()) {
    if (!state.current || requestedAt < state.serviceStatusAppliedAt) return false;
    const previousStack = state.current.stack || {};
    const mergedStack = {
      ...previousStack,
      ...stack,
      // The lightweight ten-second query skips visitor-IP diagnostics. Keep
      // the latest full-refresh values until the next complete state request.
      clientIp: stack.clientIp || previousStack.clientIp,
      backendProxyTrust: stack.backendProxyTrust || previousStack.backendProxyTrust,
    };
    state.serviceStatusAppliedAt = requestedAt;
    state.current = {
      ...state.current,
      stack: mergedStack,
    };
    renderStackSnapshot(state.current.docker || {}, mergedStack);
    setBusy(state.busy);
    void refreshBackupOptions();
    void refreshBackupJobs();
    return true;
  }

  /** Poll only service/endpoint state; full launcher state is intentionally heavier. */
  async function refreshServiceStatus() {
    if (
      document.hidden
      || state.serviceStatusRefreshInFlight
      || !state.current
      || state.current.setup?.required
      || !state.current.docker?.installed
      || !state.current.docker?.running
      || !state.current.docker?.compose
    ) {
      return null;
    }
    const requestId = state.serviceStatusRequest + 1;
    const requestedAt = Date.now();
    state.serviceStatusRequest = requestId;
    state.serviceStatusRefreshInFlight = true;
    try {
      const stack = await window.omlorixServer.getServiceStatus();
      if (requestId !== state.serviceStatusRequest) return null;
      applyServiceStatus(stack, requestedAt);
      return stack;
    } catch {
      // A transient Docker/IPC failure should keep the last trustworthy state.
      // The next interval retries automatically without flooding the console.
      return null;
    } finally {
      if (requestId === state.serviceStatusRequest) {
        state.serviceStatusRefreshInFlight = false;
      }
    }
  }

  /** Return the polling cadence for idle and active launcher states. */
  function serviceStatusRefreshIntervalMs() {
    return state.busy
      ? SERVICE_STATUS_ACTION_REFRESH_INTERVAL_MS
      : SERVICE_STATUS_REFRESH_INTERVAL_MS;
  }

  /**
   * Schedule one status tick at a time so an action can change the interval
   * immediately without leaving a second interval running in parallel.
   */
  function scheduleServiceStatusRefresh(options = {}) {
    if (!state.serviceStatusRefreshStarted) return;
    if (state.serviceStatusRefreshTimer) {
      window.clearTimeout(state.serviceStatusRefreshTimer);
    }
    if (options.refreshNow === true && !document.hidden) {
      void refreshServiceStatus();
    }
    state.serviceStatusRefreshTimer = window.setTimeout(() => {
      state.serviceStatusRefreshTimer = null;
      void refreshServiceStatus();
      scheduleServiceStatusRefresh();
    }, serviceStatusRefreshIntervalMs());
  }

  function startServiceStatusRefreshTimer() {
    if (state.serviceStatusRefreshStarted) return;
    state.serviceStatusRefreshStarted = true;
    scheduleServiceStatusRefresh();
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) scheduleServiceStatusRefresh({ refreshNow: true });
    });
  }

  function renderState(data, options = {}) {
    state.current = data;
    state.serviceStatusAppliedAt = Date.now();
    const docker = data.docker || {};
    const stack = data.stack || {};
    els.serverHomeLabel.textContent = data.serverHome || '';
    renderStackSnapshot(docker, stack);

    if (options.hydrateForm !== false) {
      hydrateForm(data);
    }
    renderDockerSetup(docker);
    renderEnvRequirements(data.envRequirements);
    renderLauncherUpdateBanner();
    renderServerUpdateBanner();
    renderVisitorIpStatus(data.visitorIp);
    renderProxyStatus(data.proxy);
    setBusy(Boolean(data.busy));
    window.dispatchEvent(new CustomEvent('omlorix:state-rendered', { detail: data }));
  }

  async function refresh() {
    setRefreshButtonRefreshing(true);
    try {
      const data = await window.omlorixServer.getState();
      renderState(data);
      void refreshBackupOptions({ force: true });
      void refreshBackupJobs({ force: true });
      return data;
    } catch (error) {
      setBadge('error', translateLauncherSource('Status error'));
      const refreshFailure = translateLauncherSource('Failed to refresh launcher status.');
      els.overallBadge.title = refreshFailure;
      els.overallBadge.setAttribute('aria-label', refreshFailure);
      appendConsole(`Status failed: ${translatedErrorMessage(error)}`);
      return null;
    } finally {
      setRefreshButtonRefreshing(false);
    }
  }

  /** Refresh local health first, then independently refresh release metadata. */
  async function refreshDashboardAndUpdates(options = {}) {
    const data = await refresh();
    if (data) {
      const channel = els.updateChannelSelect.value
        || state.availableVersionsChannel
        || data.serverSettings?.updateChannel
        || 'stable';
      await Promise.all([
        loadAvailableVersions(channel, els.versionInput.value, options),
        refreshReleaseUpdateAvailability(options),
      ]);
    }
    return data;
  }

  /**
   * Resolve the server release first because its manifest may supply the
   * minimum launcher version needed to evaluate the launcher feed result.
   */
  async function refreshReleaseUpdateAvailability(options = {}) {
    const channel = state.current?.serverSettings?.updateChannel || 'stable';
    if (state.releaseUpdateRefreshPromise) {
      if (state.releaseUpdateRefreshChannel === channel) {
        return state.releaseUpdateRefreshPromise;
      }
      try {
        await state.releaseUpdateRefreshPromise;
      } catch {
        // A different channel gets its own attempt after the active check ends.
      }
      return refreshReleaseUpdateAvailability(options);
    }

    const request = (async () => {
      await refreshServerUpdateAvailability(options);
      await refreshLauncherUpdateAvailability(options);
    })();
    state.releaseUpdateRefreshPromise = request;
    state.releaseUpdateRefreshChannel = channel;
    try {
      return await request;
    } finally {
      if (state.releaseUpdateRefreshPromise === request) {
        state.releaseUpdateRefreshPromise = null;
        state.releaseUpdateRefreshChannel = '';
      }
    }
  }

  async function runAction(label, fn, { onError } = {}) {
    if (envActionsBlocked()) {
      appendConsole(`${label} blocked: save or complete required .env values before running server actions.\n`);
      return;
    }
    if (dockerActionsBlocked()) {
      appendConsole(`${label} blocked: ${dockerActionBlockedMessage()}\n`);
      return;
    }
    setBusy(true);
    appendConsole(`\n> ${label}`);
    try {
      const data = await fn();
      renderState(data);
    } catch (error) {
      appendConsole(`${launcherT(
        'launcher_ui_value1_failed_value2',
        '{value1} failed: {value2}',
        { value1: label, value2: translatedErrorMessage(error) },
      )}\n`);
      await refresh();
      if (onError) onError(error);
    } finally {
      setBusy(false);
    }
  }

  /** Keep Visitor-IP repair failures visible on the Proxy page that launched them. */
  async function repairVisitorIps() {
    state.visitorIpRepairFailure = '';
    renderVisitorIpStatus(state.current?.visitorIp);
    await runAction(
      translateLauncherSource('Fixing visitor IP detection'),
      () => window.omlorixServer.repairVisitorIps(),
      {
        onError(error) {
          state.visitorIpRepairFailure = translatedErrorMessage(error);
          renderVisitorIpStatus(state.current?.visitorIp);
          els.proxyVisitorIpCard.focus({ preventScroll: true });
        },
      },
    );
  }

  /** Run a destination-aware full backup and retain a human-readable result. */
  async function createServerBackup() {
    if (!backupServerReady() || !state.backupOptions) {
      renderBackupPanel();
      void refreshBackupOptions({ force: true });
      return;
    }
    if (envActionsBlocked() || dockerActionsBlocked()) {
      // The form is disabled for these states; this guard only handles a click
      // that raced a settings or Docker status update.
      renderBackupPanel();
      return;
    }

    const destinationId = els.backupDestinationSelect.value || '';
    const destinationLabel = els.backupDestinationSelect.selectedOptions?.[0]?.textContent
      || launcherT('launcher_backup_destination_local', 'Local storage (server disk)');
    const encryptionEnabled = Boolean(els.backupEncryptionEnabled.checked);
    const actionLabel = launcherT('launcher_backup_creating_action', 'Creating backup…');
    state.backupLastResult = null;
    state.backupCreating = true;
    setBusy(true);
    appendConsole(`\n> ${actionLabel}`);
    try {
      const result = await window.omlorixServer.backup({
        destinationId,
        encryptionEnabled,
      });
      state.backupLastResult = {
        destinationLabel,
        payload: result?.backup || {},
      };
      if (result?.state) {
        renderState(result.state);
      }
      renderBackupPanel();
    } catch {
      appendConsole(`${launcherT(
        'launcher_backup_failed_generic',
        'The backup could not be created. Review the launcher log for details.',
      )}\n`);
      await refresh();
    } finally {
      state.backupCreating = false;
      setBusy(false);
      void refreshBackupJobs({ force: true });
    }
  }

  async function runEnvironmentSetup() {
    setBusy(true);
    els.envEditorSaved.textContent = 'Running setup';
    appendConsole('\n> Running environment setup');
    try {
      const result = await window.omlorixServer.setupEnvironment();
      state.envValidationErrors = {};
      state.envEditor = result.editor;
      renderState(result.state);
      renderEnvEditor();
      els.envEditorSaved.textContent = 'Setup complete';
      appendConsole('Environment setup finished. Review .env before starting Omlorix.');
    } catch (error) {
      els.envEditorSaved.textContent = 'Setup error';
      appendConsole(`Environment setup failed: ${translatedErrorMessage(error)}`);
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  function showLauncherRequiredDialog(payload) {
    els.launcherRequiredMessage.textContent = payload.message
      || 'This Omlorix release needs a newer Omlorix Server Launcher so the environment and deployment files stay compatible.';
    els.launcherRequiredCurrentVersion.textContent = payload.currentLauncherVersion || 'Unknown';
    els.launcherRequiredMinimumVersion.textContent = payload.minimumLauncherVersion || 'Unknown';
    els.launcherRequiredTargetVersion.textContent = payload.targetVersion || translateLauncherSource('Latest');
    const notes = String(payload.releaseNotes || '').trim();
    els.launcherRequiredNotes.textContent = notes;
    els.launcherRequiredNotes.hidden = !notes;
    els.launcherRequiredOverlay.hidden = false;
    els.openLauncherRequiredUpdateButton.focus();
  }

  function hideLauncherRequiredDialog() {
    els.launcherRequiredOverlay.hidden = true;
  }

  async function updateOmlorix() {
    if (envActionsBlocked()) {
      appendConsole('Updating Omlorix blocked: save or complete required .env values before running server actions.\n');
      return;
    }
    if (dockerActionsBlocked()) {
      appendConsole(`Updating Omlorix blocked: ${dockerActionBlockedMessage()}\n`);
      return;
    }
    if (omlorixActionsBlocked()) {
      appendConsole('Updating Omlorix blocked: Omlorix must be running before you can update it.\n');
      return;
    }
    const requestFingerprint = serverUpdateFingerprint();
    setBusy(true);
    appendConsole('\n> Updating Omlorix');
    try {
      const data = await window.omlorixServer.update({
        destinationId: state.backupDestinationId,
        encryptionEnabled: state.backupEncryptionPreferred,
      });
      if (data?.type === 'launcherUpdateRequired') {
        appendConsole(`Update paused: ${data.message}`);
        showLauncherRequiredDialog(data);
        return;
      }
      clearServerUpdateInfo();
      renderState(data);
    } catch (error) {
      appendConsole(`Updating Omlorix failed: ${translatedErrorMessage(error)}\n`);
      const data = await refresh();
      if (data && requestFingerprint !== serverUpdateFingerprint(data)) {
        clearServerUpdateInfo();
      }
    } finally {
      setBusy(false);
    }
  }

  function collectSettings(keys = null) {
    const toggles = getTogglesFromInputs();
    const fileStorageMode = storageModeFromInputs(toggles);
    // The active backend provider is derived from the user-facing mode. This
    // prevents bundled MinIO from accidentally starting while Omlorix still
    // points at a previously selected WebDAV/GCS/Azure provider.
    const fileStorageProvider = fileStorageMode === 'bundled'
      ? 's3'
      : fileStorageMode === 'local'
        ? 'local'
        : els.fileStorageProviderSelect.value || 's3';
    const fileStorageS3Bucket = fileStorageMode === 'bundled' || fileStorageProvider !== 's3'
      ? els.fileStorageS3BucketInput.value
      : els.fileStorageS3ExternalBucketInput.value;
    const settings = {
      composeProjectName: els.composeProjectNameInput.value,
      mode: els.modeSelect.value,
      version: els.versionInput.value,
      updateChannel: els.updateChannelSelect.value,
      jwtSecretKey: els.jwtSecretKeyInput.value,
      encryptionKey: els.encryptionKeyInput.value,
      passwordResetSalt: els.passwordResetSaltInput.value,
      databaseName: els.databaseNameInput.value,
      databaseUser: els.databaseUserInput.value,
      databasePassword: els.databasePasswordInput.value,
      databaseHost: els.databaseHostInput.value,
      databasePort: els.databasePortInput.value,
      databaseSchema: els.databaseSchemaInput.value,
      databaseAuditLogSchema: els.databaseAuditLogSchemaInput.value,
      databaseLogsSchema: els.databaseLogsSchemaInput.value,
      autoCreateDatabases: els.autoCreateDatabasesInput.checked,
      databaseHostOverride: els.databaseHostOverrideInput.value,
      databasePortOverride: els.databasePortOverrideInput.value,
      devDatabaseHostPort: els.devDatabaseHostPortInput.value,
      databaseUrl: els.databaseUrlInput.value,
      redisEnabled: els.redisEnabledInput.checked,
      redisPassword: els.redisPasswordInput.value,
      redisUrl: els.redisUrlInput.value,
      devRedisHostPort: els.devRedisHostPortInput.value,
      pgbouncerPoolMode: els.pgbouncerPoolModeSelect.value,
      pgbouncerMaxClientConn: els.pgbouncerMaxClientConnInput.value,
      pgbouncerDefaultPoolSize: els.pgbouncerDefaultPoolSizeInput.value,
      pgbouncerReservePoolSize: els.pgbouncerReservePoolSizeInput.value,
      pgbouncerHostBind: els.pgbouncerHostBindInput.value,
      pgbouncerHostPort: els.pgbouncerHostPortInput.value,
      minioRootUser: els.minioRootUserInput.value,
      minioRootPassword: els.minioRootPasswordInput.value,
      minioApiHostBind: els.minioApiHostBindInput.value,
      minioApiHostPort: els.minioApiHostPortInput.value,
      minioConsoleHostBind: els.minioConsoleHostBindInput.value,
      minioConsoleHostPort: els.minioConsoleHostPortInput.value,
      fileStorageProvider,
      fileStorageLocalBasePath: els.fileStorageLocalBasePathInput.value,
      // Local mode leaves the inactive object-storage bucket untouched.
      ...(fileStorageMode === 'local' ? {} : { fileStorageS3Bucket }),
      fileStorageS3Prefix: els.fileStorageS3PrefixInput.value,
      fileStorageS3Region: els.fileStorageS3RegionInput.value,
      fileStorageS3EndpointUrl: els.fileStorageS3EndpointUrlInput.value,
      fileStorageS3AccessKeyId: els.fileStorageS3AccessKeyIdInput.value,
      fileStorageS3SecretAccessKey: els.fileStorageS3SecretAccessKeyInput.value,
      fileStorageS3SessionToken: els.fileStorageS3SessionTokenInput.value,
      fileStorageGcsBucket: els.fileStorageGcsBucketInput.value,
      fileStorageGcsPrefix: els.fileStorageGcsPrefixInput.value,
      fileStorageGcsProject: els.fileStorageGcsProjectInput.value,
      fileStorageGcsCredentialsJson: els.fileStorageGcsCredentialsJsonInput.value,
      fileStorageAzureContainer: els.fileStorageAzureContainerInput.value,
      fileStorageAzurePrefix: els.fileStorageAzurePrefixInput.value,
      fileStorageAzureConnectionString: els.fileStorageAzureConnectionStringInput.value,
      fileStorageAzureAccountUrl: els.fileStorageAzureAccountUrlInput.value,
      fileStorageAzureCredential: els.fileStorageAzureCredentialInput.value,
      fileStorageWebdavUrl: els.fileStorageWebdavUrlInput.value,
      fileStorageWebdavUsername: els.fileStorageWebdavUsernameInput.value,
      fileStorageWebdavPassword: els.fileStorageWebdavPasswordInput.value,
      fileStorageWebdavPrefix: els.fileStorageWebdavPrefixInput.value,
      fileStorageWebdavVerifySsl: els.fileStorageWebdavVerifySslInput.checked,
      fileStorageWebdavTimeout: els.fileStorageWebdavTimeoutInput.value,
      otelEnabled: toggles.observabilityEnabled,
      otelServiceName: els.otelServiceNameInput.value,
      otelExporterOtlpEndpoint: els.otelExporterOtlpEndpointInput.value,
      otelExporterOtlpInsecure: els.otelExporterOtlpInsecureInput.checked,
      otelTracesEnabled: els.otelTracesEnabledInput.checked,
      otelTracesSampler: els.otelTracesSamplerSelect.value,
      otelTracesSamplerArg: els.otelTracesSamplerArgInput.value,
      otelMetricsEnabled: els.otelMetricsEnabledInput.checked,
      otelPrometheusExporterEnabled: els.otelPrometheusExporterEnabledInput.checked,
      otelLogsEnabled: els.otelLogsEnabledInput.checked,
      otelInstrumentFastapi: els.otelInstrumentFastapiInput.checked,
      otelInstrumentSqlalchemy: els.otelInstrumentSqlalchemyInput.checked,
      otelInstrumentHttpClients: els.otelInstrumentHttpClientsInput.checked,
      otelSqlCommenterEnabled: els.otelSqlCommenterEnabledInput.checked,
      otelCaptureHttpRoute: els.otelCaptureHttpRouteInput.checked,
      otelCaptureHttpUserAgent: els.otelCaptureHttpUserAgentInput.checked,
      otelHashHttpUserAgent: els.otelHashHttpUserAgentInput.checked,
      otelGrpcHostBind: els.otelGrpcHostBindInput.value,
      otelGrpcHostPort: els.otelGrpcHostPortInput.value,
      otelHttpHostBind: els.otelHttpHostBindInput.value,
      otelHttpHostPort: els.otelHttpHostPortInput.value,
      otelPrometheusHostBind: els.otelPrometheusHostBindInput.value,
      otelPrometheusHostPort: els.otelPrometheusHostPortInput.value,
      otelHealthcheckHostBind: els.otelHealthcheckHostBindInput.value,
      otelHealthcheckHostPort: els.otelHealthcheckHostPortInput.value,
      jaegerUiHostBind: els.jaegerUiHostBindInput.value,
      jaegerUiHostPort: els.jaegerUiHostPortInput.value,
      jaegerCollectorHostBind: els.jaegerCollectorHostBindInput.value,
      jaegerCollectorHostPort: els.jaegerCollectorHostPortInput.value,
      prometheusHostBind: els.prometheusHostBindInput.value,
      prometheusHostPort: els.prometheusHostPortInput.value,
      alertmanagerHostBind: els.alertmanagerHostBindInput.value,
      alertmanagerHostPort: els.alertmanagerHostPortInput.value,
      grafanaHostBind: els.grafanaHostBindInput.value,
      grafanaHostPort: els.grafanaHostPortInput.value,
      grafanaAdminUser: els.grafanaAdminUserInput.value,
      grafanaAdminPassword: els.grafanaAdminPasswordInput.value,
      grafanaRootUrl: els.grafanaRootUrlInput.value,
      postgresExporterDataSourceUri: els.postgresExporterDataSourceUriInput.value,
      postgresExporterDataSourceUser: els.postgresExporterDataSourceUserInput.value,
      postgresExporterDataSourcePass: els.postgresExporterDataSourcePassInput.value,
      redisExporterAddr: els.redisExporterAddrInput.value,
      useBundledDB: toggles.useBundledDB,
      useBundledRedis: toggles.useBundledRedis,
      usePgbouncer: toggles.usePgbouncer,
      useBundledStorage: toggles.useBundledStorage,
    };
    if (!keys) return settings;
    return Object.fromEntries(
      Array.from(keys)
        .filter((key) => Object.prototype.hasOwnProperty.call(settings, key))
        .map((key) => [key, settings[key]]),
    );
  }

  /**
   * Map a settings control to the backend payload property it owns.
   *
   * Most controls intentionally share their payload property as the HTML name.
   * The external S3 bucket is the sole alternate UI for fileStorageS3Bucket.
   */
  function settingsPayloadKeyForInput(input) {
    if (input === els.fileStorageS3ExternalBucketInput) return 'fileStorageS3Bucket';
    return String(input?.name || '').trim();
  }

  /** Mark settings dirty before scheduling IPC so edits during a save survive. */
  function markSettingsChanged(...keys) {
    for (const key of keys.flat()) {
      const normalized = String(key || '').trim();
      if (normalized) state.settingsDirtyKeys.add(normalized);
    }
    if (state.settingsSaving) {
      state.settingsSaveRequested = true;
    }
  }

  function queueSettingsAutosave() {
    if (state.settingsAutosaveTimer) {
      window.clearTimeout(state.settingsAutosaveTimer);
    }
    state.settingsAutosaveTimer = window.setTimeout(() => {
      state.settingsAutosaveTimer = null;
      void saveSettingsNow();
    }, 450);
  }

  async function saveSettingsNow() {
    if (state.settingsSaving) {
      state.settingsSaveRequested = true;
      if (state.settingsSavePromise) {
        await state.settingsSavePromise;
      }
      // The active request may not contain the edit that arrived after it
      // started. Run once more to flush the remaining dirty-key snapshot.
      return saveSettingsNow();
    }

    const dirtyKeys = new Set(state.settingsDirtyKeys);
    if (!dirtyKeys.size) return true;
    for (const key of dirtyKeys) {
      state.settingsDirtyKeys.delete(key);
    }

    state.settingsSaving = true;
    state.settingsSaveRequested = false;
    let settleSavePromise;
    state.settingsSavePromise = new Promise((resolve) => {
      settleSavePromise = resolve;
    });
    if (state.settingsAutosaveTimer) {
      window.clearTimeout(state.settingsAutosaveTimer);
      state.settingsAutosaveTimer = null;
    }

    let saveSucceeded = false;
    try {
      const data = await window.omlorixServer.saveSettings(collectSettings(dirtyKeys));
      const hasQueuedSave = state.settingsSaveRequested;
      renderState(data, { hydrateForm: !hasQueuedSave });

      if (state.autoUpdates?.settings) {
        try {
          renderAutoUpdates(await window.omlorixServer.saveScheduledUpdates({
            ...state.autoUpdates.settings,
            channel: els.updateChannelSelect.value,
          }));
        } catch (error) {
          appendConsole(`Automatic updates sync failed: ${translatedErrorMessage(error)}`);
        }
      }

      if (!hasQueuedSave) {
        await loadEnvEditor();
      }
      saveSucceeded = true;
      return true;
    } catch (error) {
      // Keep failed fields dirty so the next user edit or explicit submit can
      // retry them instead of falsely treating the form as synchronized.
      for (const key of dirtyKeys) {
        state.settingsDirtyKeys.add(key);
      }
      appendConsole(`Settings failed: ${translatedErrorMessage(error)}`);
      return false;
    } finally {
      state.settingsSaving = false;
      // A failed payload remains dirty for a future user correction, but it must
      // not enter an endless background retry loop. Only edits made while the
      // request was active, or unsaved keys after a successful request, warrant
      // an automatic follow-up.
      if (state.settingsSaveRequested || (saveSucceeded && state.settingsDirtyKeys.size)) {
        state.settingsSaveRequested = false;
        queueSettingsAutosave();
      }
      settleSavePromise(saveSucceeded);
      state.settingsSavePromise = null;
    }
  }

  function handleSettingsFieldChange(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement) || !els.settingsForm.contains(target)) return;
    markSettingsChanged(settingsPayloadKeyForInput(target));

    if (target === els.updateChannelSelect) {
      if (event.type === 'change') {
        void handleUpdateChannelChange();
      }
      return;
    }

    if (target instanceof HTMLInputElement) {
      if (target.type === 'checkbox' || target.type === 'radio') {
        // Checkbox/radio controls can emit both `input` and `change`.
        // Saving on `change` only avoids racing the browser's checked-state
        // update and prevents stale values from being written back to `.env`.
        if (event.type === 'change') {
          void saveSettingsNow();
        }
        return;
      }
      // A blur emits `change` after the final input event. Save immediately at
      // that boundary so closing/reloading the launcher does not discard a
      // compose project name (or passphrase) that is still waiting on debounce.
      if (event.type === 'change') {
        void saveSettingsNow();
        return;
      }
      queueSettingsAutosave();
      return;
    }

    if (target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement) {
      if (target === els.fileStorageProviderSelect) {
        syncFileStorageProviderPanels();
      }
      if (event.type === 'change') {
        void saveSettingsNow().then((saved) => {
          if (saved && target === els.versionInput) {
            clearServerUpdateInfo();
            return refreshReleaseUpdateAvailability({ silent: true });
          }
          return null;
        });
      } else {
        queueSettingsAutosave();
      }
    }
  }

  async function handleUpdateChannelChange() {
    const channel = els.updateChannelSelect.value || 'stable';
    const previousVersion = els.versionInput.value;
    const nextVersion = previousVersion === 'stable' || previousVersion === 'beta' ? channel : previousVersion;
    renderVersionOptions(nextVersion);
    await loadAvailableVersions(channel, nextVersion);
    const saved = await saveSettingsNow();
    if (saved) {
      clearServerUpdateInfo();
      state.launcherUpdateInfo = null;
      state.launcherUpdateMinimumVersion = '';
      renderLauncherUpdateBanner();
      await refreshReleaseUpdateAvailability({ silent: true });
    }
  }

  function handleProxyFieldChange(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement) || !els.proxyForm.contains(target)) return;

    markProxyFormChanged();
    if (
      target === els.proxyEnabledInput
      && els.proxyEnabledInput.checked
      && !state.current?.proxy?.config?.enabled
      && !state.current?.proxy?.config?.autostartExplicit
    ) {
      // Autostart is the default only when this deployment has never saved a
      // preference. Preserve an explicit manual-start choice across re-enables.
      els.proxyAutostartInput.checked = true;
    }
    if (target === els.proxyEnabledInput) {
      if (els.proxyEnabledInput.checked) {
        // Managed ingress always requires the production resolver. Keeping
        // this in sync avoids a transient untrusted backend before calibration.
        els.trustProxyHeadersInput.checked = true;
      } else if (
        state.current?.proxy?.config?.enabled
        && !state.current?.env?.FRONTEND_TRUSTED_UPSTREAMS
      ) {
        // Disabling the managed edge must not silently reinterpret its pinned
        // nginx address as an external trusted-proxy allowlist.
        els.trustProxyHeadersInput.checked = false;
      }
    }
    if (target === els.proxyEnabledInput || target === els.proxyHttpsInput) {
      updateProxyVisibility();
    }
    renderProxyValidation({});
    setBusy(state.busy);

    if (target instanceof HTMLInputElement && (target.type === 'checkbox' || target.type === 'radio')) {
      if (event.type === 'change') {
        void saveProxySettings({ silent: true });
      }
      return;
    }

    if (event.type === 'change') {
      void saveProxySettings({ silent: true });
      return;
    }

    queueProxyAutosave();
  }

  els.refreshButton.addEventListener('click', () => {
    void refreshDashboardAndUpdates({ force: true, silent: false });
  });
  els.openFolderButton.addEventListener('click', () => window.omlorixServer.revealHome());
  if (els.themeToggle) {
    els.themeToggle.addEventListener('click', toggleThemeMode);
    updateThemeToggleIcon();
  }
  els.openButton.addEventListener('click', () => {
    if (dockerActionsBlocked()) {
      appendConsole(`Opening Omlorix blocked: ${dockerActionBlockedMessage()}\n`);
      return;
    }
    if (omlorixActionsBlocked()) {
      appendConsole('Opening Omlorix blocked: Omlorix must be running before you can open it.\n');
      return;
    }
    void window.omlorixServer.openUrl();
  });
  els.envRequirementsSetupButton.addEventListener('click', runEnvironmentSetup);
  els.envRequirementsButton.addEventListener('click', () => {
    const firstMissingKey = state.current?.envRequirements?.issues?.[0]?.key;
    const settingsInput = settingsInputForEnvKey(firstMissingKey);
    if (settingsInput) {
      const settingsNav = document.querySelector('.sidebar-nav .nav-link[data-section="settings"]');
      if (settingsNav) settingsNav.click();
      settingsInput.focus();
      return;
    }

    const envNav = document.querySelector('.sidebar-nav .nav-link[data-section="environment"]');
    if (envNav) envNav.click();
    const field = firstMissingKey ? document.getElementById(`env-field-${firstMissingKey}`) : null;
    if (field) {
      field.focus();
    } else {
      els.envSearchInput.focus();
    }
  });
  els.openDockerSetupButton.addEventListener('click', async () => {
    try {
      await window.omlorixServer.openDockerSetup();
      if (!state.current?.docker?.installed) {
        startDockerReadinessPolling({
          mode: 'install',
          message: 'Opened the Docker setup guide. Install Docker in the Docker GUI, then keep this launcher open while it checks for Docker.',
        });
      }
    } catch (error) {
      appendConsole(`Docker setup guide failed: ${translatedErrorMessage(error)}`);
    }
  });
  els.startDockerDesktopButton.addEventListener('click', async () => {
    setBusy(true);
    try {
      const result = await window.omlorixServer.startDockerDesktop();
      appendConsole(result?.message || 'Docker Desktop is starting.');
      startDockerReadinessPolling({ mode: 'start' });
    } catch (error) {
      appendConsole(`Starting Docker failed: ${translatedErrorMessage(error)}`);
      await refresh();
    } finally {
      setBusy(false);
    }
  });

  els.startButton.addEventListener('click', async () => {
    if (await saveSettingsNow()) {
      await runAction('Starting Omlorix', () => window.omlorixServer.start());
    }
  });
  els.stopButton.addEventListener('click', () => runAction('Stopping Omlorix', () => window.omlorixServer.stop()));
  els.restartButton.addEventListener('click', async () => {
    if (await saveSettingsNow()) {
      await runAction('Restarting Omlorix', () => window.omlorixServer.restart());
    }
  });
  els.fixVisitorIpsButton.addEventListener('click', () => openProxySection(els.proxyFixVisitorIpsButton));
  els.updateButton.addEventListener('click', updateOmlorix);
  els.launcherUpdateButton.addEventListener('click', async () => {
    try {
      await window.omlorixServer.showLauncherUpdate();
      // The native flow updates the service cache. Re-render that result
      // without performing a second release-feed request.
      await refreshLauncherUpdateAvailability({ silent: true });
    } catch (error) {
      appendConsole(`Showing launcher update failed: ${translatedErrorMessage(error)}`);
    }
  });
  els.serverUpdateButton.addEventListener('click', async () => {
    if (!state.serverUpdateInfo?.launcherRequirement) {
      await updateOmlorix();
      return;
    }
    if (state.launcherUpdateInfo?.availableVersionMeetsMinimum === false) {
      // The known launcher feed cannot satisfy this server release yet. Force
      // a metadata refresh first instead of offering an insufficient install.
      await refreshLauncherUpdateAvailability({ force: true, silent: false });
      return;
    }
    try {
      await window.omlorixServer.showLauncherUpdate();
      await refreshLauncherUpdateAvailability({ silent: true });
    } catch (error) {
      appendConsole(`Showing launcher update failed: ${translatedErrorMessage(error)}`);
    }
  });
  els.launcherDialogCancelButton.addEventListener('click', () => {
    settleLauncherDialog(null);
  });
  els.launcherDialogConfirmButton.addEventListener('click', () => {
    if (state.launcherDialog?.input) {
      const value = els.launcherDialogInput.value.trim();
      if (!value) {
        els.launcherDialogInput.focus();
        return;
      }
      settleLauncherDialog(value);
      return;
    }
    settleLauncherDialog(true);
  });
  els.launcherDialogInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      els.launcherDialogConfirmButton.click();
    }
  });
  els.launcherDialogOverlay.addEventListener('click', (event) => {
    if (event.target === els.launcherDialogOverlay) {
      settleLauncherDialog(null);
    }
  });
  els.launcherDialogOverlay.addEventListener('keydown', trapLauncherDialogFocus);
  els.dismissLauncherRequiredButton.addEventListener('click', hideLauncherRequiredDialog);
  els.openLauncherRequiredUpdateButton.addEventListener('click', async () => {
    try {
      await window.omlorixServer.showLauncherUpdate();
      hideLauncherRequiredDialog();
    } catch (error) {
      appendConsole(`Showing launcher update failed: ${translatedErrorMessage(error)}`);
    }
  });
  els.launcherRequiredOverlay.addEventListener('click', (event) => {
    if (event.target === els.launcherRequiredOverlay) {
      hideLauncherRequiredDialog();
    }
  });
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !els.launcherDialogOverlay.hidden) {
      settleLauncherDialog(null);
      return;
    }
    if (event.key === 'Escape' && !els.launcherRequiredOverlay.hidden) {
      hideLauncherRequiredDialog();
    }
  });
  els.backupButton.addEventListener('click', () => {
    void createServerBackup();
  });
  els.backupOptionsRetryButton.addEventListener('click', () => {
    void refreshBackupOptions({ force: true });
    void refreshBackupJobs({ force: true });
  });
  els.backupDownloadRefreshButton.addEventListener('click', () => {
    void refreshBackupJobs({ force: true });
  });
  els.backupDownloadButton.addEventListener('click', () => {
    void downloadSelectedBackup();
  });
  els.backupDestinationSelect.addEventListener('change', () => {
    state.backupDestinationId = els.backupDestinationSelect.value;
    void saveBackupPolicy();
  });
  els.backupEncryptionEnabled.addEventListener('change', () => {
    if (!els.backupEncryptionEnabled.disabled) {
      state.backupEncryptionPreferred = Boolean(els.backupEncryptionEnabled.checked);
      void saveBackupPolicy();
    }
  });
  for (const control of [
    els.storageMigrationSource,
    els.storageMigrationDestination,
    els.storageMigrationScope,
    els.storageMigrationDryRun,
    els.storageMigrationDeleteSource,
    els.storageMigrationForce,
  ]) {
    const changed = () => {
      state.storageLastResult = null;
      renderStorageMigrationPanel();
    };
    control.addEventListener('input', changed);
    control.addEventListener('change', changed);
  }
  els.storageProbeButton.addEventListener('click', () => {
    void runStorageProbe();
  });
  els.storageMigrateButton.addEventListener('click', () => {
    void runStorageMigration();
  });
  const restoreButtonLabel = els.restoreButton?.querySelector('span');
  if (restoreButtonLabel) {
    restoreButtonLabel.textContent = launcherT('launcher_restore_action', 'Restore backup');
  }
  const verifyBackupButtonLabel = els.verifyBackupButton?.querySelector('span');
  if (verifyBackupButtonLabel) {
    verifyBackupButtonLabel.textContent = launcherT('launcher_ui_backup_verify_action', 'Verify backup');
  }
  els.verifyBackupButton.addEventListener('click', async () => {
    if (envActionsBlocked() || dockerActionsBlocked()) return;
    const selection = await window.omlorixServer.chooseRestoreBackup({
      title: launcherT('launcher_restore_picker_title', 'Choose Omlorix backup'),
      buttonLabel: launcherT('launcher_restore_picker_button', 'Choose backup'),
      filterName: launcherT('launcher_restore_filter', 'Omlorix backup archives'),
      allFilesName: launcherT('launcher_restore_all_files', 'All files'),
    });
    if (selection?.canceled || !selection?.filePath) return;
    runAction(
      launcherT('launcher_ui_backup_verify_action', 'Verify backup'),
      () => window.omlorixServer.verifyBackup(selection.filePath),
    );
  });
  els.restoreButton.addEventListener('click', async () => {
    if (omlorixActionsBlocked()) {
      appendConsole(`${launcherT(
        'launcher_restore_requires_running',
        'Omlorix must be running before a safe restore can begin.',
      )}\n`);
      return;
    }
    const selection = await window.omlorixServer.chooseRestoreBackup({
      title: launcherT('launcher_restore_picker_title', 'Choose Omlorix backup'),
      buttonLabel: launcherT('launcher_restore_picker_button', 'Choose backup'),
      filterName: launcherT('launcher_restore_filter', 'Omlorix backup archives'),
      allFilesName: launcherT('launcher_restore_all_files', 'All files'),
    });
    if (selection?.canceled || !selection?.filePath) return;

    const fileName = String(selection.filePath).split(/[\\/]/).pop();
    const confirmed = await showLauncherDialog({
      title: launcherT('launcher_restore_confirm_title', 'Restore this server?'),
      message: launcherT(
        'launcher_restore_confirm_message',
        'Omlorix will stop, verify {file}, create a safety backup, replace the database and files, then restart. Current data will be overwritten.',
        { file: fileName },
      ),
      confirmText: launcherT('launcher_restore_confirm_action', 'Restore server'),
    });
    if (!confirmed) return;

    runAction(
      launcherT('launcher_restore_running', 'Restoring server'),
      () => window.omlorixServer.restore(selection.filePath),
    );
  });

  els.servicesBody.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-service-action][data-service-name]');
    if (!button || dockerActionsBlocked()) return;
    const action = button.dataset.serviceAction;
    const serviceName = button.dataset.serviceName;
    const label = serviceActionLabel(action);
    if (action === 'logs') {
      await loadLogSnapshot(serviceName);
      return;
    }
    if (state.busy || envActionsBlocked()) return;
    runAction(`${label} ${serviceName}`, () => (
      window.omlorixServer.serviceAction(action, serviceName)
    ));
  });

  els.settingsForm.addEventListener('input', handleSettingsFieldChange);
  els.settingsForm.addEventListener('change', handleSettingsFieldChange);
  els.settingsForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    await saveSettingsNow();
  });

  els.proxyForm.addEventListener('input', handleProxyFieldChange);
  els.proxyForm.addEventListener('change', handleProxyFieldChange);

  els.proxyForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    await saveProxySettings();
  });

  els.proxyFixVisitorIpsButton.addEventListener('click', () => {
    if (state.current?.visitorIp?.recommendedAction === 'restart-omlorix') {
      runAction(
        launcherT('launcher_visitor_ip_action_restart_omlorix', 'Restart Omlorix'),
        () => window.omlorixServer.restart(),
      );
      return;
    }
    if (state.current?.visitorIp?.recommendedAction === 'start-proxy') {
      runProxyAction(
        'Starting proxy',
        () => window.omlorixServer.startProxy(),
        proxyStartActionMessages(),
      );
      return;
    }
    repairVisitorIps();
  });

  els.proxyTlsCertChooseButton.addEventListener('click', () => {
    chooseProxyTlsFile('cert', els.proxyTlsCertInput);
  });

  els.proxyTlsKeyChooseButton.addEventListener('click', () => {
    chooseProxyTlsFile('key', els.proxyTlsKeyInput);
  });

  els.proxyTlsCaChooseButton.addEventListener('click', () => {
    chooseProxyTlsFile('ca', els.proxyTlsCaInput);
  });

  els.proxyStartButton.addEventListener('click', async () => {
    const saved = await saveProxySettings({ message: 'Proxy settings saved before start.', waitForActiveSave: true });
    if (!saved) return;
    await runProxyAction(
      'Starting proxy',
      () => window.omlorixServer.startProxy(),
      proxyStartActionMessages(),
    );
  });

  els.proxyStopButton.addEventListener('click', () => {
    runProxyAction('Stopping proxy', () => window.omlorixServer.stopProxy());
  });

  els.proxyRestartButton.addEventListener('click', async () => {
    const saved = await saveProxySettings({ message: 'Proxy settings saved before restart.', waitForActiveSave: true });
    if (!saved) return;
    await runProxyAction('Restarting proxy', () => window.omlorixServer.restartProxy());
  });

  els.proxyInstallServiceButton.addEventListener('click', async () => {
    const saved = await saveProxySettings({
      message: launcherT(
        'launcher_ui_proxy_settings_saved_before_service_installation',
        'Proxy settings saved before service installation.',
      ),
      waitForActiveSave: true,
    });
    if (!saved) return;
    await runProxyAction(
      launcherT('launcher_proxy_installing_background_service', 'Installing background proxy service'),
      () => window.omlorixServer.installProxyService(),
    );
  });

  els.proxyUninstallServiceButton.addEventListener('click', () => {
    runProxyAction(
      launcherT('launcher_proxy_removing_background_service', 'Removing background proxy service'),
      () => window.omlorixServer.uninstallProxyService(),
    );
  });

  // The infrastructure cards live outside the settings form, so they need
  // their own change hook to persist the same .env settings immediately.
  els.toggleInputs.forEach((input) => {
    input.addEventListener('change', () => {
      syncConnectionModeControls();
      renderEnvEditor();
      markSettingsChanged(
        input.dataset.toggle === 'observabilityEnabled' ? 'otelEnabled' : input.dataset.toggle,
      );
      void saveSettingsNow();
    });
  });

  els.connectionModeInputs.forEach((input) => {
    input.addEventListener('change', () => {
      if (!input.checked) return;
      const target = els.toggleInputs.find((candidate) => candidate.dataset.toggle === input.dataset.connectionToggle);
      if (!target) return;
      target.checked = input.dataset.connectionValue === 'true';
      if (input.dataset.connectionToggle === 'useBundledDB' && !target.checked) {
        const pgbouncerToggle = els.toggleInputs.find((candidate) => candidate.dataset.toggle === 'usePgbouncer');
        if (pgbouncerToggle) pgbouncerToggle.checked = false;
      }
      syncConnectionModeControls();
      renderEnvEditor();
      markSettingsChanged(
        input.dataset.connectionToggle === 'observabilityEnabled'
          ? 'otelEnabled'
          : input.dataset.connectionToggle,
      );
      void saveSettingsNow();
    });
  });

  els.redisModeInputs.forEach((input) => {
    input.addEventListener('change', () => {
      if (!input.checked) return;

      // Persist the three UI modes through the existing environment booleans:
      // Off=(false,false), bundled=(true,true), external=(true,false).
      const mode = input.dataset.redisMode;
      els.redisEnabledInput.checked = mode !== 'off';
      const bundledToggle = els.toggleInputs.find((candidate) => candidate.dataset.toggle === 'useBundledRedis');
      if (bundledToggle) bundledToggle.checked = mode === 'bundled';

      syncConnectionModeControls();
      renderEnvEditor();
      markSettingsChanged('redisEnabled', 'useBundledRedis');
      void saveSettingsNow();
    });
  });

  els.storageModeInputs.forEach((input) => {
    input.addEventListener('change', () => {
      if (!input.checked) return;

      // Local=(false,local), bundled=(true,s3), and external=(false,provider).
      // The provider value is derived during collection so the two persisted
      // environment settings always describe the same active storage mode.
      const mode = input.dataset.storageMode;
      els.fileStorageModeInput.value = mode;
      const bundledToggle = els.toggleInputs.find(
        (candidate) => candidate.dataset.toggle === 'useBundledStorage',
      );
      if (bundledToggle) bundledToggle.checked = mode === 'bundled';

      syncConnectionModeControls();
      renderEnvEditor();
      markSettingsChanged('useBundledStorage', 'fileStorageProvider');
      void saveSettingsNow();
    });
  });

  els.connectionSettingInputs.forEach((input) => {
    input.addEventListener('input', () => {
      markSettingsChanged(settingsPayloadKeyForInput(input));
      queueSettingsAutosave();
    });
    input.addEventListener('change', () => {
      if (input === els.fileStorageProviderSelect) {
        syncFileStorageProviderPanels();
      }
      markSettingsChanged(settingsPayloadKeyForInput(input));
      void saveSettingsNow();
    });
  });

  function handleAutoUpdateAutosave(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement) || !els.autoUpdateForm.contains(target)) return;

    if (event.type !== 'input') {
      renderPendingAutoUpdateSettings();
    }
    const validation = validateAutoUpdateSettings(collectAutoUpdateSettings());
    setAutoUpdateValidation(validation);
    if (validation) return;

    // Text-like inputs can emit a stream of partial values while the user is
    // still editing. Debounce those, while discrete controls save immediately.
    if (event.type === 'input') {
      queueAutoUpdateAutosave();
      return;
    }

    void saveAutoUpdates({ silent: true });
  }

  els.autoUpdateForm.addEventListener('input', handleAutoUpdateAutosave);
  els.autoUpdateForm.addEventListener('change', handleAutoUpdateAutosave);
  els.autoUpdateForm.addEventListener('submit', (event) => {
    event.preventDefault();
    void saveAutoUpdates({ silent: true });
  });

  els.autoUpdateBackupSettingsButton.addEventListener('click', openDashboardBackupSettings);

  els.autoUpdateRunNowButton.addEventListener('click', async () => {
    if (envActionsBlocked()) {
      appendConsole('Automatic update check blocked: save or complete required .env values before running server actions.\n');
      return;
    }
    if (dockerActionsBlocked()) {
      appendConsole(`Automatic update check blocked: ${dockerActionBlockedMessage()}\n`);
      return;
    }
    if (omlorixActionsBlocked()) {
      appendConsole('Automatic update check blocked: Omlorix must be running before this action can continue.\n');
      return;
    }
    setAutoUpdateValidation('');
    setBusy(true);
    appendConsole('\n> Running automatic update check now');
    try {
      const snapshot = await window.omlorixServer.runScheduledUpdateNow();
      renderAutoUpdates(snapshot);
      appendConsole(snapshot?.status?.lastMessage || 'Automatic update check finished.');
      const requirement = snapshot?.status?.launcherRequirement;
      if (snapshot?.status?.state === 'blocked' && requirement) {
        showLauncherRequiredDialog({
          message: snapshot.status.lastMessage,
          currentLauncherVersion: requirement.currentLauncherVersion,
          minimumLauncherVersion: requirement.minimumLauncherVersion,
          targetVersion: requirement.targetVersion,
          releaseNotes: requirement.releaseNotes,
        });
      }
      await refresh();
    } catch (error) {
      appendConsole(`Automatic update check failed: ${translatedErrorMessage(error)}`);
      await loadAutoUpdates();
    } finally {
      setBusy(false);
    }
  });

  els.autoUpdateLauncherButton.addEventListener('click', async () => {
    try {
      await window.omlorixServer.showLauncherUpdate();
    } catch (error) {
      appendConsole(`Showing launcher update failed: ${translatedErrorMessage(error)}`);
    }
  });

  els.envSearchInput.addEventListener('input', () => {
    state.envFilter.search = els.envSearchInput.value;
    renderEnvEditor();
  });

  els.envSectionFilter.addEventListener('change', () => {
    state.envFilter.section = els.envSectionFilter.value;
    renderEnvEditor();
  });

  els.exportEnvButton.addEventListener('click', async () => {
    setBusy(true);
    els.envEditorSaved.textContent = 'Exporting';
    try {
      const result = await window.omlorixServer.chooseEnvExport();
      if (result?.canceled) {
        els.envEditorSaved.textContent = 'Ready';
        return;
      }
      const filePath = result?.export?.filePath || '';
      els.envEditorSaved.textContent = 'Exported';
      appendConsole(`.env exported to ${basename(filePath)}.`);
    } catch (error) {
      els.envEditorSaved.textContent = 'Export error';
      appendConsole(`.env export failed: ${translatedErrorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  });

  /** Choose an .env file and reveal the shared review panel before applying. */
  async function beginEnvImport() {
    if (!(await confirmDiscardEnvChanges('import a file'))) return;
    await clearEnvImportPreview();
    renderEnvImportResult();
    setBusy(true);
    els.envEditorSaved.textContent = 'Choosing file';
    try {
      const result = await window.omlorixServer.chooseEnvImport();
      if (result?.canceled) {
        els.envEditorSaved.textContent = 'Ready';
        return;
      }
      renderEnvImportPreview(result.preview);
      els.envEditorSaved.textContent = 'Review import';
      appendConsole(`Selected .env import: ${basename(result.preview?.sourceFile)}`);
    } catch (error) {
      els.envEditorSaved.textContent = 'Import error';
      appendConsole(`.env import failed: ${translatedErrorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  }

  els.importEnvButton.addEventListener('click', beginEnvImport);

  els.replaceMissingEnvInput.addEventListener('change', () => {
    if (state.envImportPreview) renderEnvImportPreview(state.envImportPreview);
  });

  els.cancelEnvImportButton.addEventListener('click', async () => {
    els.envEditorSaved.textContent = 'Ready';
    await clearEnvImportPreview();
  });

  els.applyEnvImportButton.addEventListener('click', async () => {
    if (!state.envImportPreview?.importId) return;
    if (!(await confirmDiscardEnvChanges('apply the import'))) return;
    setBusy(true);
    els.envEditorSaved.textContent = 'Importing';
    try {
      const result = await window.omlorixServer.applyEnvImport(
        state.envImportPreview.importId,
        // Send the mode represented by the projection on screen, rather than
        // trusting checkbox state that may predate this preview response.
        { replaceMissing: Boolean(selectedEnvImportPreview()?.replaceMissing) },
      );
      if (result && result.ok === false) {
        renderEnvImportPreview(result.preview);
        els.envEditorSaved.textContent = 'Fix import';
        appendConsole('.env import has validation errors. Review the import panel before applying.');
        return;
      }
      state.envValidationErrors = {};
      state.envEditor = result.editor;
      renderEnvImportPreview(null);
      renderState(result.state);
      renderEnvEditor();
      const messageValues = {
        importedCount: result.importedCount || 0,
      };
      let titleKey = 'launcher_ui_import_applied';
      let messageKey = 'launcher_ui_import_no_changes';
      let resultState = 'success';
      if (result.restartRequired) {
        titleKey = 'launcher_ui_import_applied_restart_needed';
        messageKey = 'launcher_ui_import_applied_restart_manually';
        resultState = 'warning';
      }
      const resultTitle = launcherT(
        titleKey,
        result.restartRequired ? 'Import applied — restart needed' : 'Import applied',
      );
      const resultMessage = launcherT(messageKey, '.env import finished with no changes.', messageValues);
      renderEnvImportResult(resultTitle, resultMessage, resultState);
      els.envEditorSaved.textContent = resultTitle;
      appendConsole(resultMessage);
    } catch (error) {
      els.envEditorSaved.textContent = 'Import error';
      appendConsole(`.env import failed: ${translatedErrorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  });

  els.addCustomEnvButton.addEventListener('click', () => {
    if (!state.envEditor) return;
    const key = els.customEnvKeyInput.value.trim();
    const value = els.customEnvValueInput.value;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
      const message = 'Use a valid environment variable key.';
      state.envValidationErrors = { ...state.envValidationErrors, [key]: message };
      setCustomEnvKeyError(message);
      updateEnvValidationSummary();
      return;
    }
    if (state.envEditor.fields.some((field) => field.key === key)) {
      state.envValidationErrors = { ...state.envValidationErrors, [key]: 'This key already exists.' };
      setCustomEnvKeyError('This key already exists.');
      renderEnvEditor();
      return;
    }
    const secret = isSecretKey(key);
    state.envEditor.fields.push({
      key,
      label: key,
      description: 'Custom variable added in the launcher.',
      section: 'Custom',
      type: 'string',
      options: [],
      secret,
      required: false,
      known: false,
      value,
      placeholder: secret ? 'Enter value' : '',
      isSet: Boolean(value),
      dirty: true,
    });
    if (!state.envEditor.groups.includes('Custom')) {
      state.envEditor.groups.push('Custom');
    }
    delete state.envValidationErrors[key];
    delete state.envValidationErrors[''];
    setCustomEnvKeyError('');
    els.customEnvKeyInput.value = '';
    els.customEnvValueInput.value = '';
    state.envFilter.section = 'Custom';
    markEnvDirty();
    renderEnvEditor();
  });

  els.envEditorForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    await saveEnvEditorNow();
  });

  els.loadLogsButton.addEventListener('click', () => void loadLogSnapshot());
  els.startLogFollowButton.addEventListener('click', () => void startLogFollow());
  els.stopLogFollowButton.addEventListener('click', () => void stopLogFollow());

  for (const input of [els.logLinesInput, els.logSinceInput]) {
    input.addEventListener('input', () => {
      input.removeAttribute('aria-invalid');
      if (els.logControlStatus.dataset.level === 'error') setLogControlStatus();
    });
  }
  els.logServiceSelect.addEventListener('change', () => {
    if (els.logControlStatus.dataset.level === 'error') setLogControlStatus();
  });

  els.clearConsoleButton.addEventListener('click', () => {
    if (state.consoleStreamTimer !== null) window.clearTimeout(state.consoleStreamTimer);
    state.consoleStreamTimer = null;
    state.consoleStreamBuffer = '';
    window.OmlorixTerminalOutput.clear(els.consoleOutput);
  });

  window.omlorixServer.onOperationStart((payload) => {
    setBusy(true);
    appendConsole(launcherT(
      'launcher_ui_operation_started',
      '\n> {operation} started',
      { operation: serviceOperationName(payload.name) },
    ));
  });

  window.omlorixServer.onOperationOutput((payload) => {
    appendConsole(payloadText(payload, 'text', 'textKey', 'textValues'), { preserveStream: true });
  });

  window.omlorixServer.onOperationEnd((payload) => {
    const messageValues = localizedOperationMessageValues(payload.messageValues);
    if (messageValues.action) messageValues.action = serviceActionLabel(messageValues.action);
    appendConsole(`\n> ${payloadText({ ...payload, messageValues }, 'message', 'messageKey', 'messageValues')}`);
    setBusy(false);
    void refreshDashboardAndUpdates({ silent: true });
  });

  window.omlorixServer.onLogFollowOutput((payload) => {
    if (
      state.logFollowSessionId
      && payload.sessionId !== state.logFollowSessionId
    ) return;
    if (!state.logFollowSessionId && !state.logFollowStarting) return;
    state.logFollowSessionId = payload.sessionId;
    renderLogControls();
    appendConsole(payload.text, { preserveStream: true });
  });

  window.omlorixServer.onLogFollowEnd((payload) => {
    if (state.logFollowSessionId && payload.sessionId !== state.logFollowSessionId) return;
    state.logFollowEndedSessionId = payload.sessionId;
    state.logFollowSessionId = '';
    state.logFollowStarting = false;
    state.logFollowStopping = false;
    if (payload.stopped) {
      setLogControlStatus(launcherT('launcher_ui_log_follow_stopped', 'Log following stopped.'));
      appendConsole(launcherT('launcher_ui_log_follow_stopped', 'Log following stopped.'));
    } else if (payload.ok) {
      setLogControlStatus(launcherT('launcher_ui_log_follow_ended', 'Log following ended.'));
      appendConsole(launcherT('launcher_ui_log_follow_ended', 'Log following ended.'));
    } else {
      setLogControlStatus(launcherT(
        'launcher_ui_log_follow_exit_error',
        'Log following ended with exit code {code}.',
        { code: payload.code },
      ), 'error');
      appendConsole(launcherT(
        'launcher_ui_log_follow_exit_error',
        'Log following ended with exit code {code}.',
        { code: payload.code },
      ));
    }
    renderLogControls();
  });

  window.omlorixServer.onScheduledUpdatesChanged((snapshot) => {
    renderAutoUpdates(snapshot);
  });

  window.addEventListener('beforeunload', (event) => {
    if (
      state.settingsCloseAllowed
      || (!state.settingsDirtyKeys.size && !state.settingsSaving)
    ) {
      return;
    }

    // Electron honors preventDefault here without showing a browser-native
    // confirmation. Keep the renderer alive just long enough to finish the
    // pending IPC write, then repeat the close with an explicit one-shot bypass.
    event.preventDefault();
    event.returnValue = false;
    if (state.settingsCloseFlushActive) return;

    state.settingsCloseFlushActive = true;
    let closeFlushTimeout = null;
    const timeout = new Promise((resolve) => {
      closeFlushTimeout = window.setTimeout(() => resolve(false), SETTINGS_CLOSE_FLUSH_TIMEOUT_MS);
    });
    void Promise.race([saveSettingsNow(), timeout]).catch(() => false).finally(() => {
      if (closeFlushTimeout !== null) {
        window.clearTimeout(closeFlushTimeout);
      }
      state.settingsCloseFlushActive = false;
    }).then(() => {
      // A failed or timed-out flush must not trap the user in a window that can
      // never close. saveSettingsNow() already reports ordinary save failures.
      state.settingsCloseAllowed = true;
      window.close();
    });
  });

  // Setup and the permanent Secrets page perform their own focused IPC writes.
  // Accept their returned state immediately so the dashboard never waits for a
  // polling refresh to reflect a completed setup or changed credential.
  window.addEventListener('omlorix:external-state', (event) => {
    if (event.detail && event.detail !== state.current) {
      const previousFingerprint = state.current ? serverUpdateFingerprint() : '';
      const setupWasRequired = state.current?.setup?.required !== false;
      renderState(event.detail);
      const releaseConfigurationChanged = previousFingerprint !== serverUpdateFingerprint();
      if (
        !event.detail.setup?.required
        && (setupWasRequired || releaseConfigurationChanged)
      ) {
        void refreshReleaseUpdateAvailability({ silent: true });
      }
    }
  });

  initializeWindowMode();
  window.omlorixServer.onRefreshRequested(() => {
    void refreshDashboardAndUpdates({ force: true, silent: false });
  });
  document.addEventListener('click', () => closeAllCustomSelects());
  document.querySelectorAll('[data-secret-toggle-for]').forEach((button) => {
    const input = document.getElementById(button.dataset.secretToggleFor);
    const sourceShowLabel = button.dataset.launcherSourceAriaLabel
      || button.getAttribute('aria-label')
      || 'Show secret value';
    bindSecretRevealButton(button, input, sourceShowLabel);
  });
  enhanceSelectsIn(document);
  renderLogControls();
  void refreshDashboardAndUpdates({ silent: true });
  startAvailableVersionsRefreshTimer();
  startServerUpdateRefreshTimer();
  startServiceStatusRefreshTimer();
  startEnvIdleReloadTimer();
  loadEnvEditor();
  loadAutoUpdates();
})();
