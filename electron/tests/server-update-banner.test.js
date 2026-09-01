const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const vm = require('node:vm');

const rendererRoot = path.join(__dirname, '..', 'renderer');

test('server update banner is accessible and placed directly below status metrics', async () => {
  const html = await fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8');
  const statusMetrics = html.indexOf('class="hero-stats metric-grid"');
  const banner = html.indexOf('id="serverUpdateBanner"');
  const nextDashboardCard = html.indexOf('id="dockerSetupCard"');

  assert(statusMetrics >= 0, 'status metrics must exist');
  assert(banner > statusMetrics, 'server update banner must follow the status metrics');
  assert(nextDashboardCard > banner, 'server update banner must precede other dashboard cards');
  assert.match(
    html.slice(banner, nextDashboardCard),
    /role="status" aria-live="polite" aria-atomic="true" hidden/,
  );
  assert.match(html.slice(banner, nextDashboardCard), /id="serverUpdateButton"[^>]*type="button"/);
});

test('launcher updates have an independent accessible dashboard notice', async () => {
  const html = await fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8');
  const statusMetrics = html.indexOf('class="hero-stats metric-grid"');
  const launcherBanner = html.indexOf('id="launcherUpdateBanner"');
  const serverBanner = html.indexOf('id="serverUpdateBanner"');

  assert(launcherBanner > statusMetrics, 'launcher update banner must follow the status metrics');
  assert(serverBanner > launcherBanner, 'server and launcher notices must have stable ordering');
  assert.match(
    html.slice(launcherBanner, serverBanner),
    /role="status" aria-live="polite" aria-atomic="true" hidden/,
  );
  assert.match(
    html.slice(launcherBanner, serverBanner),
    /id="launcherUpdateButton"[^>]*type="button"/,
  );
});

test('dynamic launcher copy is translated in every supported launcher language', async () => {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const languages = ['ar', 'de', 'es', 'fr', 'hi', 'it', 'ja', 'pt', 'ru', 'zh'];
  const requiredKeys = [
    'launcher_start_finished',
    'launcher_restore_restarting_after_failure',
    'launcher_restore_stopped_safely',
    'launcher_restore_restart_failed',
    'launcher_restore_reason_target_not_empty',
    'launcher_restore_reason_missing_required_files',
    'launcher_restore_reason_checksum_mismatch',
    'launcher_restore_reason_encryption_key_mismatch',
    'launcher_restore_reason_manifest_parse_failed',
    'launcher_restore_reason_payload_tar_parse_failed',
    'launcher_restore_reason_archive_extracted_size_exceeded',
    'launcher_restore_reason_insufficient_disk_space',
    'launcher_restore_reason_source_access_failed',
    'launcher_restore_recovery_unconfirmed',
    'launcher_restore_startup_failed_after_restore',
    'launcher_possible_database_downgrade',
    'launcher_server_update_label',
    'launcher_server_update_available_title',
    'launcher_server_update_description',
    'launcher_server_update_action',
    'launcher_launcher_update_label',
    'launcher_launcher_update_available_title',
    'launcher_launcher_update_description',
    'launcher_launcher_update_action',
    'launcher_server_update_launcher_check_action',
    'launcher_server_update_launcher_required_title',
    'launcher_server_update_launcher_required_description',
    'launcher_server_update_launcher_ready_description',
    'launcher_server_update_launcher_feed_behind_description',
    'launcher_server_update_launcher_action',
    'launcher_server_update_requires_running',
    'launcher_server_update_channel_stable',
    'launcher_server_update_channel_beta',
    'launcher_visitor_ips_heading',
    'launcher_visitor_ip_title_proxy_stopped',
    'launcher_visitor_ip_message_proxy_stopped',
    'launcher_visitor_ip_title_proxy_running',
    'launcher_visitor_ip_title_verification_failed',
    'launcher_visitor_ip_message_verification_failed',
    'launcher_proxy_background_service_installed',
    'launcher_proxy_background_service_not_installed',
    'launcher_proxy_background_service_unavailable',
    'launcher_proxy_install_background_service',
    'launcher_proxy_remove_background_service',
    'launcher_proxy_installing_background_service',
    'launcher_proxy_removing_background_service',
    'launcher_visitor_ip_message_proxy_running',
    'launcher_visitor_ip_direct_probe',
    'launcher_visitor_ip_action_open_proxy',
    'launcher_visitor_ip_action_start_proxy',
    'launcher_visitor_ip_action_reapply',
    'launcher_visitor_ip_action_fix',
    'launcher_visitor_ip_title_restart_required',
    'launcher_visitor_ip_message_restart_required',
    'launcher_visitor_ip_action_restart_omlorix',
    'launcher_proxy_action_starting',
    'launcher_proxy_action_started',
    'launcher_proxy_action_start_failed',
    'launcher_backup_group_label',
    'launcher_backup_provider_local',
    'launcher_backup_destination_local',
    'launcher_backup_unavailable_title',
    'launcher_backup_unavailable_desc',
    'launcher_backup_loading_title',
    'launcher_backup_loading_desc',
    'launcher_backup_load_failed_title',
    'launcher_backup_load_failed_desc',
    'launcher_backup_retry_action',
    'launcher_backup_create_desc',
    'launcher_backup_destination_label',
    'launcher_backup_encryption_title',
    'launcher_backup_encryption_desc',
    'launcher_backup_setup_title',
    'launcher_backup_setup_desc',
    'launcher_backup_plaintext_only_desc',
    'launcher_backup_create_action',
    'launcher_backup_creating_action',
    'launcher_backup_finished',
    'launcher_backup_encrypted',
    'launcher_backup_plaintext',
    'launcher_backup_result_title',
    'launcher_backup_result_job',
    'launcher_backup_failed_generic',
    'launcher_auto_update_backup_reference_enabled',
    'launcher_auto_update_backup_reference_disabled',
    'launcher_auto_update_backup_reference_action',
    'launcher_services_subtitle',
    'launcher_services_auto_refresh',
    'launcher_services_auto_refresh_active',
    'launcher_services_running_count',
    'launcher_service_not_created',
    'launcher_service_not_running',
    'launcher_services_empty',
    'launcher_stack_all_running_detail',
    'launcher_stack_partial_running_detail',
    'launcher_stack_none_running_detail',
    'launcher_stack_health_issues_detail',
  ];

  const catalogStart = source.indexOf('const LAUNCHER_TRANSLATIONS = {');
  const catalogEnd = source.indexOf('\n  };', catalogStart);
  assert(catalogStart >= 0 && catalogEnd > catalogStart, 'launcher translation catalog must exist');
  const catalogSource = source.slice(catalogStart, catalogEnd);
  const catalogs = new Map(languages.map((language, index) => {
    const marker = `    ${language}: {`;
    const start = catalogSource.indexOf(marker);
    const nextMarker = languages[index + 1] ? `    ${languages[index + 1]}: {` : '';
    const end = nextMarker ? catalogSource.indexOf(nextMarker, start + marker.length) : catalogSource.length;
    assert(start >= 0 && end > start, `${language} launcher translation catalog must exist`);
    return [language, catalogSource.slice(start, end)];
  }));

  for (const [language, catalog] of catalogs) {
    for (const key of requiredKeys) {
      const catalogEntries = catalog.match(new RegExp(`\\b${key}:`, 'g')) || [];
      assert.equal(catalogEntries.length, 1, `${language}: ${key} must appear exactly once`);
    }
  }
});

test('structured restore reason codes are localized before wrapper interpolation', async () => {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const start = source.indexOf('  const RESTORE_REASON_TRANSLATIONS');
  const end = source.indexOf('\n  const LAUNCHER_SOURCE_KEYS', start);
  assert(start >= 0 && end > start, 'restore reason localization helpers must exist');

  const context = {
    launcherT: (key) => `translated:${key}`,
  };
  vm.runInNewContext(
    `${source.slice(start, end)}\nthis.localizeValues = localizedOperationMessageValues;`,
    context,
  );

  const localized = context.localizeValues({
    error: 'The restore target is not empty.',
    restoreReasonCode: 'target_not_empty',
  });
  assert.equal(
    localized.error,
    'translated:launcher_restore_reason_target_not_empty',
  );
  assert.equal(Object.hasOwn(localized, 'restoreReasonCode'), false);

  const unknown = context.localizeValues({
    error: 'Sanitized backend error',
    restoreReasonCode: 'future_reason',
  });
  assert.equal(unknown.error, 'Sanitized backend error');
});

test('launcher update rendering accounts for server minimum-version requirements', async () => {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const launcherRenderStart = source.indexOf('function renderLauncherUpdateBanner()');
  const launcherRenderEnd = source.indexOf('/** Render the latest successful server-release check', launcherRenderStart);
  const launcherRenderSource = source.slice(launcherRenderStart, launcherRenderEnd);
  const serverRenderStart = source.indexOf('function renderServerUpdateBanner()');
  const serverRenderEnd = source.indexOf('/** Identify the release configuration', serverRenderStart);
  const serverRenderSource = source.slice(serverRenderStart, serverRenderEnd);
  const refreshStart = source.indexOf('async function refreshLauncherUpdateAvailability(');
  const refreshEnd = source.indexOf('/** Check the configured release channel', refreshStart);
  const refreshSource = source.slice(refreshStart, refreshEnd);

  assert.match(launcherRenderSource, /serverUpdateInfo\?\.launcherRequirement/);
  assert.match(launcherRenderSource, /launcherUpdateBanner\.hidden = !available \|\| serverRequiresLauncher/);
  assert.match(serverRenderSource, /availableVersionMeetsMinimum === true/);
  assert.match(serverRenderSource, /availableVersionMeetsMinimum === false/);
  assert.match(serverRenderSource, /launcher_server_update_launcher_feed_behind_description/);
  assert.match(serverRenderSource, /launcher_server_update_launcher_check_action/);
  assert.match(refreshSource, /const minimumLauncherVersion = requirement\?\.minimumLauncherVersion \|\| ''/);
  assert.match(refreshSource, /getLauncherUpdateInfo\(\{[\s\S]*minimumLauncherVersion,/);
  assert.match(refreshSource, /getLauncherUpdateInfo/);
  assert.match(refreshSource, /force: options\.force === true \|\| requirementChanged/);
});

test('passive launcher IPC returns a safe compatibility-aware update summary', async () => {
  const electronRoot = path.join(rendererRoot, '..');
  const mainSource = await fs.readFile(path.join(electronRoot, 'main.js'), 'utf8');
  const preloadSource = await fs.readFile(path.join(electronRoot, 'preload.js'), 'utf8');
  const helperStart = mainSource.indexOf('async function getPassiveLauncherUpdateInfo(');
  const helperEnd = mainSource.indexOf('function createLauncherUpdateMenuItem()', helperStart);
  const helperSource = mainSource.slice(helperStart, helperEnd);

  assert.match(mainSource, /handleTrustedIpc\('launcher:get-update-info'/);
  assert.match(preloadSource, /getLauncherUpdateInfo: \(options\) => ipcRenderer\.invoke\('launcher:get-update-info', options\)/);
  assert.match(helperSource, /maxAgeMs: force \? 0 : LAUNCHER_UPDATE_CACHE_MAX_AGE_MS/);
  assert.match(helperSource, /failureMaxAgeMs: force \? 0 : RELEASE_CHECK_FAILURE_COOLDOWN_MS/);
  assert.match(helperSource, /status: 'unavailable'/);
  assert.match(helperSource, /logPassiveReleaseFailure/);
  assert.match(helperSource, /compareVersions\(latestVersion, minimumLauncherVersion\) >= 0/);
  assert.match(helperSource, /availableVersionMeetsMinimum/);
  assert.doesNotMatch(helperSource, /feedUrl|requestHeaders|token/i);
});

test('startup release refreshes coalesce IPC and do not echo launcher state', async () => {
  const electronRoot = path.join(rendererRoot, '..');
  const launcherSource = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const setupSource = await fs.readFile(path.join(rendererRoot, 'setup-flow.js'), 'utf8');
  const mainSource = await fs.readFile(path.join(electronRoot, 'main.js'), 'utf8');
  const preloadSource = await fs.readFile(path.join(electronRoot, 'preload.js'), 'utf8');

  const versionsStart = launcherSource.indexOf('async function loadAvailableVersions(');
  const versionsEnd = launcherSource.indexOf('function refreshAvailableVersionsQuietly()', versionsStart);
  const versionsSource = launcherSource.slice(versionsStart, versionsEnd);
  assert.match(versionsSource, /availableVersionsPromiseChannel === channel/);
  assert.match(versionsSource, /getAvailableVersions\(channel, \{ force \}\)/);
  assert.match(versionsSource, /result\?\.unavailable/);
  assert.match(preloadSource, /getAvailableVersions: \(channel, options\)/);
  assert.match(mainSource, /getPassiveAvailableVersions\(channel, options\)/);

  const coordinatorStart = launcherSource.indexOf('async function refreshReleaseUpdateAvailability(');
  const coordinatorEnd = launcherSource.indexOf('async function runAction(', coordinatorStart);
  const coordinatorSource = launcherSource.slice(coordinatorStart, coordinatorEnd);
  assert.match(coordinatorSource, /state\.releaseUpdateRefreshPromise/);
  assert.match(coordinatorSource, /refreshServerUpdateAvailability\(options\)/);
  assert.match(coordinatorSource, /refreshLauncherUpdateAvailability\(options\)/);
  assert.match(launcherSource, /refreshDashboardAndUpdates\(\{ force: true, silent: false \}\)/);
  assert.match(setupSource, /acceptState\(event\.detail, \{ broadcast: false \}\)/);
  assert.match(setupSource, /if \(options\.broadcast !== false\) dispatchLauncherState\(data\)/);
});

test('passive release diagnostics are bounded and sanitized', async () => {
  const mainSource = await fs.readFile(path.join(rendererRoot, '..', 'main.js'), 'utf8');
  const helperStart = mainSource.indexOf('function logPassiveReleaseFailure(');
  const helperEnd = mainSource.indexOf('\nfunction sendToRenderer(', helperStart);
  const helperSource = mainSource.slice(helperStart, helperEnd);
  const messages = [];
  let now = 10_000;
  const context = {
    Date: { now: () => now },
    console: { warn: (message) => messages.push(message) },
    launcherT: (key, values = {}) => {
      const templates = {
        stable: 'stable',
        launcher_update_metadata: 'Launcher update metadata',
        server_release_metadata: 'Server release metadata',
        passive_release_check_unavailable: '{resource} is unavailable for the {channel} channel ({reason}); passive retries are paused for {seconds} seconds.',
      };
      return String(templates[key] || key).replace(/\{(\w+)\}/g, (_match, name) => values[name]);
    },
  };
  vm.runInNewContext(
    `const RELEASE_CHECK_FAILURE_COOLDOWN_MS = 60000;\n`
      + `const passiveReleaseFailureLogs = new Map();\n`
      + `${helperSource}\nthis.logFailure = logPassiveReleaseFailure;`,
    context,
  );

  const failure = new Error('HTTP 404 at https://feed.example/?token=secret');
  context.logFailure('launcher_update_metadata', 'stable', failure);
  context.logFailure('launcher_update_metadata', 'stable', failure);
  context.logFailure('launcher_update_metadata', 'stable', failure);
  assert.equal(messages.length, 1);
  assert.match(messages[0], /HTTP 404/);
  assert.doesNotMatch(messages[0], /feed\.example|secret/);

  context.logFailure('server_release_metadata', 'stable', failure);
  assert.equal(messages.length, 2, 'the two independent release resources each log once');
  now += 60_001;
  context.logFailure('launcher_update_metadata', 'stable', failure);
  assert.equal(messages.length, 3, 'a new diagnostic is allowed after the cooldown');
});

test('server update banner follows the active translation locale and direction', async () => {
  const launcherSource = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const setupSource = await fs.readFile(path.join(rendererRoot, 'setup-flow.js'), 'utf8');
  const languageStart = launcherSource.indexOf('function launcherLanguage()');
  const languageEnd = launcherSource.indexOf('function payloadText(', languageStart);
  const localization = launcherSource.slice(languageStart, languageEnd);

  assert.match(localization, /document\.documentElement\.lang/);
  assert.match(localization, /document\.documentElement\.dir/);
  assert.doesNotMatch(localization, /navigator\.languages/);
  assert.match(setupSource, /document\.documentElement\.setAttribute\('lang', locale\)/);
  assert.match(setupSource, /document\.documentElement\.setAttribute\('dir', direction\)/);
});

test('server update cache is guarded by the saved release fingerprint', async () => {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const refreshStart = source.indexOf('async function refreshServerUpdateAvailability(');
  const refreshEnd = source.indexOf('function refreshServerUpdateQuietly()', refreshStart);
  const refreshSource = source.slice(refreshStart, refreshEnd);
  const settingsStart = source.indexOf('function handleSettingsFieldChange(');
  const settingsEnd = source.indexOf('function handleProxyFieldChange(', settingsStart);
  const settingsSource = source.slice(settingsStart, settingsEnd);
  const updateStart = source.indexOf('async function updateOmlorix()');
  const updateEnd = source.indexOf('function collectSettings()', updateStart);
  const updateSource = source.slice(updateStart, updateEnd);

  assert.match(refreshSource, /const requestFingerprint = serverUpdateFingerprint\(\)/);
  assert.equal(
    (refreshSource.match(/requestFingerprint !== serverUpdateFingerprint\(\)/g) || []).length,
    2,
    'successful and failed checks must both reject stale release fingerprints',
  );
  assert.match(
    settingsSource,
    /clearServerUpdateInfo\(\);\s*return refreshReleaseUpdateAvailability/,
  );
  assert.match(
    settingsSource,
    /clearServerUpdateInfo\(\);[\s\S]*await refreshReleaseUpdateAvailability/,
  );
  assert.match(updateSource, /const requestFingerprint = serverUpdateFingerprint\(\)/);
  assert.match(
    updateSource,
    /requestFingerprint !== serverUpdateFingerprint\(data\)[\s\S]*clearServerUpdateInfo\(\)/,
  );
});

test('core text settings save immediately when editing finishes', async () => {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const saveStart = source.indexOf('async function saveSettingsNow(');
  const saveEnd = source.indexOf('function handleSettingsFieldChange(', saveStart);
  const saveSource = source.slice(saveStart, saveEnd);
  const handlerStart = source.indexOf('function handleSettingsFieldChange(');
  const handlerEnd = source.indexOf('async function handleUpdateChannelChange(', handlerStart);
  const handlerSource = source.slice(handlerStart, handlerEnd);

  // Unrelated release settings and legacy secrets must not be bundled into a
  // Compose-name edit. The backend receives only the renderer's dirty keys.
  assert.match(saveSource, /saveSettings\(collectSettings\(dirtyKeys\)\)/);

  // Input events remain debounced while typing, but the final blur/change must
  // flush immediately so closing the launcher cannot strand the last edit.
  assert.match(
    handlerSource,
    /if \(event\.type === 'change'\) \{\s*void saveSettingsNow\(\);\s*return;\s*\}\s*queueSettingsAutosave\(\)/,
  );
  assert.match(
    source,
    /startButton\.addEventListener\('click', async \(\) => \{\s*if \(await saveSettingsNow\(\)\)/,
  );
  assert.match(
    source,
    /restartButton\.addEventListener\('click', async \(\) => \{\s*if \(await saveSettingsNow\(\)\)/,
  );
  assert.match(
    source,
    /addEventListener\('beforeunload',[\s\S]*Promise\.race\(\[saveSettingsNow\(\), timeout\]\)/,
  );
  const closeStart = source.indexOf("window.addEventListener('beforeunload'");
  const closeEnd = source.indexOf("window.addEventListener('omlorix:external-state'", closeStart);
  const closeSource = source.slice(closeStart, closeEnd);
  assert.match(closeSource, /Promise\.race\(\[saveSettingsNow\(\), timeout\]\)\.catch\(\(\) => false\)/);
  assert.match(closeSource, /SETTINGS_CLOSE_FLUSH_TIMEOUT_MS/);
  assert.match(closeSource, /settingsCloseAllowed = true;\s*window\.close\(\)/);
  assert.match(closeSource, /\.finally\(\(\) => \{[\s\S]*settingsCloseFlushActive = false/);
});
