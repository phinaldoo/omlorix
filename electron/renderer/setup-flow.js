(function setupFlow() {
  'use strict';

  const STEP_LABEL_KEYS = [
    'step_label_welcome',
    'step_label_type',
    'step_label_data',
    'step_label_access',
    'step_label_secrets',
    'step_label_review',
    'step_label_done',
  ];

  // Docker Desktop can take several seconds to expose its engine after the
  // application opens. Keep setup checks frequent without overlapping a slow
  // status request, which could otherwise pile up IPC and Docker processes.
  const SETUP_DOCKER_POLL_INTERVAL_MS = 2000;
  // Docker startup is not allowed to lock the setup controls indefinitely.
  // The independent watchdog also recovers the UI if a status request stalls.
  const SETUP_DOCKER_START_TIMEOUT_MS = 120000;

  const SECRET_DEFINITIONS = [
    { key: 'JWT_SECRET_KEY', setting: 'jwtSecretKey', purposeKey: 'secret_jwt' },
    { key: 'ENCRYPTION_KEY', setting: 'encryptionKey', purposeKey: 'secret_encryption' },
    { key: 'PASSWORD_RESET_IDENTIFIER_HASH_SALT', setting: 'passwordResetSalt', purposeKey: 'secret_reset' },
    { key: 'LOG_IP_HASH_SALT', setting: 'logIpHashSalt', purposeKey: 'secret_audit_ip' },
    { key: 'BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE', setting: 'backupPassphrase', purposeKey: 'secret_backup' },
    { key: 'DATABASE_PASSWORD', setting: 'databasePassword', purposeKey: 'secret_database', bundled: 'OMLORIX_USE_BUNDLED_DB' },
    { key: 'REDIS_PASSWORD', setting: 'redisPassword', purposeKey: 'secret_redis', bundled: 'OMLORIX_USE_BUNDLED_REDIS' },
    { key: 'MINIO_ROOT_PASSWORD', setting: 'minioRootPassword', purposeKey: 'secret_storage', bundled: 'OMLORIX_USE_BUNDLED_STORAGE' },
  ];

  // English is the source copy. The launcher follows the operating-system
  // locale and falls back to English when an older installation uses a locale
  // not currently supported by Omlorix.
  const TEXT = {
    en: {
      nav_secrets: 'Secrets', secrets_title: 'Secrets', secrets_subtitle: 'Manage server credentials and the automatic .env recovery copy.',
      backup_missing_title: 'No current secrets backup', backup_missing_desc: 'Download a recovery file and keep it somewhere separate from this server.',
      backup_current_title: 'Secrets backup is current', backup_current_desc: 'The saved recovery file matches the secrets currently used by this server.',
      backup_outdated_title: 'Secrets backup is out of date', backup_outdated_desc: 'A secret changed after the last download. Save a new recovery file.',
      download_backup: 'Save secrets backup', import_backup: 'Import backup', save_now: 'Save now', change_backup_location: 'Change location', disable_automatic_backup: 'Disable automatic backup', import_env: 'Import .env', restore_complete_env: 'Restore complete .env', choose_backup_location: 'Choose backup location', secret_actions_title: 'Secret management',
      secret_actions_desc: 'Regenerating secrets can sign users out or disconnect services. Before first launch it is safe to regenerate the complete set.', regenerate_all: 'Regenerate secrets',
      post_launch_secret_desc: 'Critical encryption and service credentials are locked after first launch. You can update the backup passphrase or import a trusted recovery file.',
      post_launch_secret_desc_runtime: 'The JWT signing key and audit IP salt are operator-managed here. Restart Omlorix after changing the signing key; every user will be signed out.',
      first_run_setup: 'First-run setup', change_later: 'You can change these settings later in the launcher.', setup_steps_label: 'Server setup steps',
      welcome_eyebrow: 'Private AI, under your control', welcome_title: 'Set up your Omlorix server',
      welcome_desc: 'We’ll prepare secure defaults, your data services, network access, and a required recovery backup. No command line is needed.',
      restore_existing: 'Restore from an existing .env backup',
      step_1: 'Step 1 · Setup type', type_title: 'Choose how much to configure', type_desc: 'Recommended setup is ready for most private servers. Custom setup exposes external services and developer options.',
      recommended: 'Recommended', recommended_desc: 'Production mode, stable updates, and services managed by Omlorix.', custom: 'Custom', custom_desc: 'Use external services or review advanced deployment choices.',
      advanced_runtime: 'Advanced release settings', mode: 'Mode', production: 'Production', development: 'Development', update_channel: 'Update channel', stable: 'Stable', beta: 'Beta',
      step_2: 'Step 2 · Data and storage', data_title: 'Where should your data live?', data_desc: 'Keep the recommended built-in services or connect infrastructure you already manage.',
      database: 'Database', database_desc: 'Stores accounts, conversations, settings, and audit history.', built_in: 'Built in', external: 'External', postgres_url: 'PostgreSQL connection URL',
      cache: 'Cache and background work', cache_desc: 'Keeps sessions and scheduled tasks responsive.', redis_url: 'Redis connection URL', file_storage: 'File storage', file_storage_desc: 'Stores uploads, attachments, and generated files.',
      local_folder: 'Omlorix data folder', built_in_storage: 'Built-in object storage',
      step_3: 'Step 3 · Access and proxy', access_title: 'Who should be able to connect?', access_desc: 'Choose the closest match. Advanced proxy controls appear only when you need them.',
      this_computer: 'Only this computer', this_computer_desc: 'Safest for personal testing.', local_network: 'Local network', local_network_desc: 'Other devices on your trusted network can connect.',
      public_domain: 'Domain or public address', public_domain_desc: 'Use HTTPS and a trusted proxy for internet access.', http_port: 'Omlorix port', public_host: 'Public host name', proxy_choice: 'How is HTTPS handled?',
      launcher_proxy: 'Launcher proxy', existing_proxy: 'Existing proxy or tunnel', trusted_proxies: 'Trusted proxy addresses', trusted_proxies_hint: 'Only these systems may report a visitor’s real address.',
      proxy_http_port: 'Public HTTP port', proxy_https_port: 'Public HTTPS port', tls_later: 'The launcher proxy starts with HTTP. Add a certificate on the Proxy page before exposing it to the internet.',
      enable_https: 'Enable HTTPS now', enable_https_desc: 'Choose an existing certificate and private key for your public host.', certificate_file: 'Certificate file', private_key_file: 'Private key file', choose_file: 'Choose', tls_files_required: 'Choose both a certificate and a private key to enable HTTPS.',
      step_4: 'Step 4 · Secrets', protect_title: 'Protect and back up your server', protect_desc: 'Choose a separate .env backup file once. The launcher will automatically update that copy whenever a setting changes.',
      recovery_warning_title: 'Choose a secure backup location', recovery_warning_desc: 'The .env copy contains credentials and encryption keys. Store it outside the Omlorix server folder on a protected device or vault.',
      step_5: 'Step 5 · Review', review_title: 'Review and start your server', review_desc: 'Everything required is ready. You can return to any previous step before starting.',
      done_title: 'Your server is ready', done_desc: 'Open Omlorix to configure the website and create the first administrator.', open_omlorix: 'Open Omlorix', open_dashboard: 'Open launcher dashboard', retry_start: 'Try starting again',
      back: 'Back', get_started: 'Get started', continue: 'Continue', cancel: 'Cancel', start_server: 'Start server', save_setup: 'Save setup',
      step_label_welcome: 'Welcome', step_label_type: 'Setup type', step_label_data: 'Data', step_label_access: 'Access', step_label_secrets: 'Secrets', step_label_review: 'Review', step_label_done: 'Complete',
      docker_ready: 'Docker and Compose are ready.', docker_missing: 'Docker is not installed. You can configure Omlorix now and install Docker later.', docker_stopped: 'Docker is installed but not running. Start it before launching Omlorix.', compose_missing: 'Docker Compose is not available.',
      refresh_status: 'Refresh status', start_docker: 'Start Docker', starting_docker: 'Starting Docker…',
      export_last_saved_remaining: 'The entered secrets do not meet the minimum requirements. Click Choose backup location {remaining} more times to use the last saved values instead.',
      backup_required: 'Choose the automatic .env backup location before continuing.', backup_saved: 'Automatic .env backup enabled. Future setting changes will update this copy.',
      secret_jwt: 'Signs login tokens; changing it signs out every user after restart', secret_encryption: 'Encrypts saved credentials', secret_reset: 'Protects password-reset identifiers', secret_audit_ip: 'Protects audit IP fingerprints', secret_backup: 'Encrypts server backup archives', secret_database: 'Bundled database password', secret_redis: 'Bundled cache password', secret_storage: 'Bundled storage password',
      show: 'Show', hide: 'Hide', show_secret_value: 'Show secret value', hide_secret_value: 'Hide secret value', show_postgres_url: 'Show PostgreSQL URL', hide_postgres_url: 'Hide PostgreSQL URL', show_redis_url: 'Show Redis URL', hide_redis_url: 'Hide Redis URL', setup_type: 'Setup type', storage: 'File storage', access: 'Access', proxy: 'Proxy', backup: 'Secrets backup', current: 'Current', required: 'Required',
      built_in_database: 'Built-in PostgreSQL', external_database: 'External PostgreSQL', built_in_cache: 'Built-in Redis', external_cache: 'External Redis', local_only: 'Only this computer', lan_access: 'Local network', public_access: 'Public address', disabled: 'Off',
      saved_no_docker_title: 'Setup is saved', saved_no_docker_desc: 'Install and start Docker, then use the launcher dashboard to start Omlorix.', starting_title: 'Starting your server', starting_desc: 'Omlorix is preparing its services. This can take a few minutes on the first run.',
      start_failed_title: 'Omlorix could not start', start_failed_desc: 'Your configuration and backup are safe. Review the message below, adjust settings if needed, and try again.',
      regenerated: 'New secrets generated. The automatic .env backup was updated.',
      import_now: 'Import now', import_confirm: 'Importing replaces launcher settings and credentials. Stop Omlorix first and use only a trusted .env backup. Select “Import now” to continue.',
      stop_before_import: 'Stop Omlorix before importing an .env backup.',
      imported: 'Complete .env restored. Review the settings, then start or restart Omlorix manually.', export_failed: 'The .env backup could not be saved: {error}', import_failed: 'The .env backup could not be imported: {error}', backup_saved_now: '.env backup saved now.', disable_automatic_backup_title: 'Disable automatic .env backup?', disable_automatic_backup_desc: 'Future changes will no longer update the configured recovery copy. The existing recovery file will be retained and must be deleted separately if desired.', disable_automatic_backup_confirm: 'Disable automatic backup', automatic_backup_disabled: 'Automatic .env backup disabled. The existing recovery file was retained.', disable_automatic_backup_failed: 'The automatic .env backup could not be disabled: {error}', secret_saved: 'Secret saved.', secret_saved_restart: 'Signing key saved. Restart Omlorix to apply it and sign out every user.', generate_signing_key: 'Generate new signing key',
      automatic_backup_failed_title: 'Automatic .env backup needs attention', automatic_backup_failed_desc: 'The latest .env change was saved, but the recovery copy could not be updated. Check the destination or choose a new location.', automatic_backup_outdated_desc: 'The recovery copy does not match the current .env. Save it now or choose a new location.',
      invalid_postgres: 'Enter a PostgreSQL URL beginning with postgresql:// or postgres://.', invalid_redis: 'Enter a Redis URL beginning with redis:// or rediss://.', invalid_port: 'Enter a port between 1 and 65535.', required_field: 'This field is required.',
      proxy_ports_different: 'The public HTTP and HTTPS ports must be different.',
      proxy_frontend_port_different: 'The public HTTP port must be different from the Omlorix port.',
      secret_min_length: 'Use at least 16 characters.', secret_jwt_min_length: 'Use at least 64 bytes.', secret_fernet_invalid: 'Use a valid Fernet encryption key.', secret_log_ip_must_differ_from_jwt: 'Use an audit IP hash salt different from the JWT signing key.',
      managed_storage_required: 'When both the database and cache are external, choose external file storage too.',
      s3_endpoint: 'Endpoint URL', bucket: 'Bucket', region: 'Region', access_key: 'Access key ID', secret_key: 'Secret access key', project: 'Project', credentials_json: 'Credentials JSON', container: 'Container', connection_string: 'Connection string', account_url: 'Account URL', credential: 'Credential', webdav_url: 'WebDAV URL', username: 'Username', password: 'Password',
      backup_meta: 'Last saved {date} · fingerprint {fingerprint}', step_count: 'Step {current} of {total}', setup_complete_log: 'Configuration saved.\nSecrets backup verified.\nOmlorix is ready at {url}.',
    },
  };
  Object.assign(TEXT, window.OmlorixSetupTranslations || {});

  const locale = (navigator.language || 'en').toLowerCase().split('-')[0];

  /** Return translated copy, substituting simple named placeholders. */
  function t(key, values = {}) {
    let value = TEXT[locale]?.[key] || TEXT.en[key] || key;
    for (const [name, replacement] of Object.entries(values)) {
      value = value.replaceAll(`{${name}}`, String(replacement));
    }
    return value;
  }

  /** Translate stable server-manager messages through the full launcher catalog. */
  function translateLauncherMessage(message) {
    const source = String(message || '');
    return typeof window.omlorixLauncherTranslate === 'function'
      ? window.omlorixLauncherTranslate(source)
      : source;
  }

  const state = {
    current: null,
    step: 0,
    busy: false,
    hydrated: false,
    showUntilDismissed: false,
    exportInvalidClicks: 0,
    exportInvalidArmedUntil: 0,
    dockerStartPolling: {
      active: false,
      inFlight: false,
      timer: null,
      timeoutTimer: null,
      deadline: 0,
      generation: 0,
    },
    dockerReadinessMonitor: {
      inFlight: false,
      timer: null,
    },
  };

  const byId = (id) => document.getElementById(id);
  const refs = {
    overlay: byId('setupOverlay'), stageBody: byId('setupStageBody'), stepList: byId('setupStepList'), announcement: byId('setupAnnouncement'),
    back: byId('setupBackButton'), next: byId('setupNextButton'), reviewDashboard: byId('setupReviewDashboardButton'), footer: document.querySelector('.setup-footer'), count: byId('setupStepCount'), progress: byId('setupProgressFill'), mobileStep: byId('setupMobileStep'), mobileProgress: byId('setupMobileProgress'),
    readiness: byId('setupReadiness'), launchReadiness: byId('setupLaunchReadiness'), review: byId('setupReview'), secretList: byId('setupSecretList'), backupConfirm: byId('setupBackupConfirm'),
    downloadSetup: byId('setupDownloadSecretsButton'), regenerateSetup: byId('setupRegenerateSecretsButton'), importSetup: byId('setupImportBackupButton'),
    doneMark: byId('setupDoneMark'), doneTitle: byId('setupDoneTitle'), doneDescription: byId('setupDoneDescription'), launchLog: byId('setupLaunchLog'), openOmlorix: byId('setupOpenOmlorixButton'), retry: byId('setupRetryButton'), dashboard: byId('setupDashboardButton'),
    downloadPermanent: byId('downloadSecretsBackupButton'), changeBackupLocation: byId('changeEnvBackupLocationButton'), disableAutomaticBackup: byId('disableAutomaticEnvBackupButton'), importPermanent: byId('importSecretsBackupButton'), managementMessage: byId('secretManagementMessage'), permanentList: byId('permanentSecretList'),
    managementDescription: byId('secretManagementDescription'),
    automaticBackupWarning: byId('automaticEnvBackupWarning'), automaticBackupWarningTitle: byId('automaticEnvBackupWarningTitle'), automaticBackupWarningDescription: byId('automaticEnvBackupWarningDescription'),
  };

  /** Apply translations to static setup and Secrets-page markup. */
  function applyTranslations() {
    document.querySelectorAll('[data-setup-i18n]').forEach((element) => {
      element.textContent = t(element.dataset.setupI18n);
    });
    document.querySelectorAll('[data-setup-i18n-aria-label]').forEach((element) => {
      element.setAttribute('aria-label', t(element.dataset.setupI18nAriaLabel));
    });
    const direction = locale === 'ar' ? 'rtl' : 'ltr';
    document.documentElement.setAttribute('lang', locale);
    document.documentElement.setAttribute('dir', direction);
    refs.overlay?.setAttribute('dir', direction);
    byId('secrets')?.setAttribute('dir', direction);
  }

  /** Move the existing core-secret editor from Settings into its permanent page. */
  function mountExistingSecretsEditor() {
    const subsection = byId('serverSecretsSubsection');
    const mount = byId('serverSecretsMount');
    if (subsection && mount && subsection.parentElement !== mount) mount.appendChild(subsection);
  }

  function selected(name) {
    return document.querySelector(`input[name="${name}"]:checked`)?.value || '';
  }

  function setSelected(name, value) {
    const input = document.querySelector(`input[name="${name}"][value="${value}"]`);
    if (input) input.checked = true;
  }

  function boolEnv(key) {
    return String(state.current?.env?.[key] || '').toLowerCase() === 'true';
  }

  /**
   * Return a saved URL only when it represents a plausible external service.
   * Bundled and loopback endpoints are derived implementation details; showing
   * them after an operator selects External makes a generated credential look
   * like a valid value they should keep.
   */
  function externalConnectionUrl(value, allowedProtocols, bundledHosts) {
    const source = String(value || '').trim();
    if (!source) return '';
    try {
      const parsed = new URL(source);
      const protocol = parsed.protocol.toLowerCase();
      const hostname = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, '');
      const internalHosts = new Set(['localhost', '127.0.0.1', '::1', ...bundledHosts]);
      return allowedProtocols.includes(protocol) && !internalHosts.has(hostname)
        ? source
        : '';
    } catch {
      return '';
    }
  }

  function dispatchLauncherState(data) {
    window.dispatchEvent(new CustomEvent('omlorix:external-state', { detail: data }));
  }

  /** Keep both the wizard and normal launcher synchronized after every IPC write. */
  function acceptState(data, options = {}) {
    if (!data) return;
    const hadCurrentState = Boolean(state.current);
    const wasDockerReady = dockerReady();
    state.current = data;
    if (options.hydrate || !state.hydrated) hydrateWizard(data);
    refs.disableAutomaticBackup.hidden = !data.setup?.backupConfigured;
    renderAutomaticBackupWarning(data.automaticEnvBackupError);
    renderPermanentSecretList();
    applyPostLaunchSecretSafety();
    renderReadiness(refs.readiness);
    renderReadiness(refs.launchReadiness);
    // `complete` is the persisted source of truth. `required` is retained as
    // a derived compatibility field in launcher state, so honor both while
    // deciding which initial surface is safe to reveal.
    const setupIsRequired = !data.setup || data.setup.complete !== true || data.setup.required === true;
    refs.overlay.hidden = !(setupIsRequired || state.showUntilDismissed);

    // Do this only after the overlay has been placed in its final state. The
    // page starts with the dashboard hidden, which makes this state hand-off
    // atomic from the user's perspective: they see either setup or dashboard,
    // never the dashboard first and setup a moment later.
    document.documentElement.classList.remove('setup-state-pending');
    if (options.broadcast !== false) dispatchLauncherState(data);

    // Docker readiness owns the optional completion step throughout setup.
    // A status refresh can happen on the Welcome page as well as Review, so
    // rebuild the visible wizard chrome on either readiness edge regardless
    // of the current step. Preserve position and focus because this refresh
    // is a status update, not navigation initiated by the operator.
    if (hadCurrentState && wasDockerReady !== dockerReady() && !refs.overlay.hidden) {
      if (!dockerReady() && state.step === 6) state.step = 5;
      renderStep({ preserveViewport: true });
    }
  }

  function setBusy(value) {
    state.busy = Boolean(value);
    refs.back.disabled = state.busy || state.step === 0;
    refs.next.disabled = state.busy || (state.step === 4 && !state.current?.setup?.backupCurrent);
    refs.reviewDashboard.disabled = state.busy;
    refs.downloadSetup.disabled = state.busy;
    refs.regenerateSetup.disabled = state.busy;
    refs.importSetup.disabled = state.busy;
    refs.downloadPermanent.disabled = state.busy;
    refs.changeBackupLocation.disabled = state.busy;
    refs.disableAutomaticBackup.disabled = state.busy;
    refs.importPermanent.disabled = state.busy;
  }

  /** Derive Docker-dependent wizard chrome without mutating the document. */
  function setupStepPresentation(step, ready) {
    const visibleStepLabelKeys = ready ? STEP_LABEL_KEYS : STEP_LABEL_KEYS.slice(0, -1);
    return {
      visibleStepLabelKeys,
      currentStepNumber: Math.min(step + 1, visibleStepLabelKeys.length),
      totalSteps: visibleStepLabelKeys.length,
      progress: Math.round((step / (visibleStepLabelKeys.length - 1)) * 100),
      nextHidden: step === 6 || (step === 5 && !ready),
    };
  }

  /** Monitor Docker only while the Review or Done setup surface is visible. */
  function shouldMonitorSetupDockerReadiness() {
    return state.step >= 5 && !refs.overlay.hidden;
  }

  /** Stop the continuous Review/Done Docker monitor and release its timer. */
  function stopSetupDockerReadinessMonitor() {
    if (state.dockerReadinessMonitor.timer) {
      window.clearTimeout(state.dockerReadinessMonitor.timer);
    }
    state.dockerReadinessMonitor.timer = null;
  }

  /** Keep exactly one two-second Docker readiness check scheduled. */
  function scheduleSetupDockerReadinessMonitor() {
    if (!shouldMonitorSetupDockerReadiness()) {
      stopSetupDockerReadinessMonitor();
      return;
    }
    if (state.dockerReadinessMonitor.timer || state.dockerReadinessMonitor.inFlight) return;
    state.dockerReadinessMonitor.timer = window.setTimeout(() => {
      state.dockerReadinessMonitor.timer = null;
      void pollSetupDockerReadinessMonitor();
    }, SETUP_DOCKER_POLL_INTERVAL_MS);
  }

  /** Re-read Docker and fail closed when its status cannot be confirmed. */
  async function pollSetupDockerReadinessMonitor() {
    if (!shouldMonitorSetupDockerReadiness() || state.dockerReadinessMonitor.inFlight) return;
    state.dockerReadinessMonitor.inFlight = true;
    try {
      acceptState(await window.omlorixServer.getState());
    } catch {
      // A lost Docker/IPC connection must immediately revoke the launch step.
      // Preserve every other last-known setting while marking only runtime
      // readiness unavailable; a later successful poll restores it.
      if (dockerReady() && state.current) {
        acceptState({
          ...state.current,
          docker: {
            ...(state.current.docker || {}),
            running: false,
            compose: false,
          },
        });
      }
    } finally {
      state.dockerReadinessMonitor.inFlight = false;
      scheduleSetupDockerReadinessMonitor();
    }
  }

  /** Check immediately after returning to the launcher, without duplicate polls. */
  function refreshSetupDockerReadinessMonitor() {
    if (!shouldMonitorSetupDockerReadiness()) {
      stopSetupDockerReadinessMonitor();
      return;
    }
    stopSetupDockerReadinessMonitor();
    void pollSetupDockerReadinessMonitor();
  }

  /** Render the setup rail, active panel, progress, and context-aware action. */
  function renderStep(options = {}) {
    const ready = dockerReady();
    const presentation = setupStepPresentation(state.step, ready);
    const { visibleStepLabelKeys } = presentation;
    const panels = Array.from(document.querySelectorAll('[data-setup-panel]'));
    panels.forEach((panel) => { panel.hidden = Number(panel.dataset.setupPanel) !== state.step; });
    refs.stepList.replaceChildren();
    visibleStepLabelKeys.forEach((key, index) => {
      const item = document.createElement('li');
      item.className = `setup-step-item${index === state.step ? ' is-active' : ''}${index < state.step ? ' is-complete' : ''}`;
      const number = document.createElement('span');
      number.className = 'setup-step-number';
      number.textContent = index < state.step ? '✓' : String(index + 1);
      const label = document.createElement('span');
      label.textContent = t(key);
      item.append(number, label);
      refs.stepList.appendChild(item);
    });
    refs.progress.style.width = `${presentation.progress}%`;
    refs.mobileProgress.style.width = `${presentation.progress}%`;
    refs.count.textContent = t('step_count', {
      current: presentation.currentStepNumber,
      total: presentation.totalSteps,
    });
    refs.mobileStep.textContent = refs.count.textContent;
    refs.back.hidden = state.step === 0 || state.step === 6;
    refs.next.hidden = presentation.nextHidden;
    refs.footer.hidden = state.step === 6;
    refs.reviewDashboard.hidden = state.step !== 5;
    refs.next.textContent = state.step === 0 ? t('get_started')
      : state.step === 5 ? t('start_server')
        : t('continue');
    refs.next.disabled = state.busy || (state.step === 4 && !state.current?.setup?.backupCurrent);
    const announcedAction = refs.next.hidden && state.step === 5
      ? t('open_dashboard')
      : refs.next.textContent;
    refs.announcement.textContent = `${t(STEP_LABEL_KEYS[state.step])}. ${announcedAction}`;
    if (state.step === 4) renderSetupSecretList();
    if (state.step === 5) renderReview();
    scheduleSetupDockerReadinessMonitor();
    if (!options.preserveViewport) {
      refs.stageBody.scrollTop = 0;
      window.setTimeout(() => panels[state.step]?.querySelector('h1, h2')?.focus?.(), 0);
    }
  }

  function dockerStatusReady(docker = {}) {
    return Boolean(docker.installed && docker.running && docker.compose);
  }

  function dockerReady() {
    return dockerStatusReady(state.current?.docker || {});
  }

  function renderReadiness(target) {
    if (!target || !state.current) return;
    const docker = state.current.docker || {};
    let kind = 'is-ready';
    let message = t('docker_ready');
    if (!docker.installed) { kind = 'is-warning'; message = t('docker_missing'); }
    else if (!docker.running) { kind = 'is-warning'; message = t('docker_stopped'); }
    else if (!docker.compose) { kind = 'is-error'; message = t('compose_missing'); }
    target.replaceChildren();
    const row = document.createElement('div');
    row.className = `setup-ready-row ${kind}`;
    const dot = document.createElement('span'); dot.className = 'setup-ready-dot'; dot.setAttribute('aria-hidden', 'true');
    const copy = document.createElement('span');
    copy.className = 'setup-ready-copy';
    copy.textContent = message;
    row.append(dot, copy);

    // The welcome panel is the point at which users commonly start Docker.
    // Keep a lightweight, in-place status check here so they do not need to
    // restart the launcher just to continue setup.
    if (target === refs.readiness) {
      const actions = document.createElement('div');
      actions.className = 'setup-readiness-actions';
      const canStartDocker = Boolean(docker.installed && !docker.running && docker.canStartDesktop);
      if (state.dockerStartPolling.active || canStartDocker) {
        const startDocker = document.createElement('button');
        startDocker.className = 'btn btn-primary setup-start-docker-button';
        startDocker.type = 'button';
        startDocker.disabled = state.busy || state.dockerStartPolling.active;
        if (state.dockerStartPolling.active) {
          startDocker.setAttribute('aria-busy', 'true');
          const spinner = document.createElement('span');
          spinner.className = 'setup-start-docker-spinner';
          spinner.setAttribute('aria-hidden', 'true');
          const label = document.createElement('span');
          label.textContent = t('starting_docker');
          startDocker.append(spinner, label);
        } else {
          startDocker.textContent = t('start_docker');
          startDocker.addEventListener('click', startDockerFromSetup);
        }
        actions.appendChild(startDocker);
      }

      const refresh = document.createElement('button');
      refresh.className = 'btn btn-ghost btn-icon refresh-button setup-readiness-refresh';
      refresh.type = 'button';
      refresh.disabled = state.busy || state.dockerStartPolling.active;
      refresh.setAttribute('aria-label', t('refresh_status'));
      refresh.title = t('refresh_status');
      refresh.innerHTML = Icons.withSvgAttributes("refreshSpinning", { "class": "refresh-icon", "aria-hidden": "true" });
      refresh.addEventListener('click', () => refreshReadiness(refresh));
      actions.appendChild(refresh);
      row.appendChild(actions);
    }

    target.appendChild(row);
  }

  /** Clear the setup-only Docker readiness watch and restore normal controls. */
  function stopSetupDockerPolling() {
    if (state.dockerStartPolling.timer) {
      window.clearTimeout(state.dockerStartPolling.timer);
    }
    if (state.dockerStartPolling.timeoutTimer) {
      window.clearTimeout(state.dockerStartPolling.timeoutTimer);
    }
    // Invalidate callbacks and status responses belonging to this attempt.
    state.dockerStartPolling.generation += 1;
    state.dockerStartPolling.active = false;
    state.dockerStartPolling.inFlight = false;
    state.dockerStartPolling.timer = null;
    state.dockerStartPolling.timeoutTimer = null;
    state.dockerStartPolling.deadline = 0;
    renderReadiness(refs.readiness);
  }

  /** Schedule exactly one Docker status check two seconds after the last one. */
  function scheduleSetupDockerPoll() {
    if (!state.dockerStartPolling.active || state.dockerStartPolling.timer) return;
    if (Date.now() >= state.dockerStartPolling.deadline) {
      stopSetupDockerPolling();
      return;
    }
    const generation = state.dockerStartPolling.generation;
    state.dockerStartPolling.timer = window.setTimeout(() => {
      if (generation !== state.dockerStartPolling.generation) return;
      state.dockerStartPolling.timer = null;
      void pollSetupDockerReadiness(generation);
    }, SETUP_DOCKER_POLL_INTERVAL_MS);
  }

  /** Refresh setup state until Docker Engine and Compose both become ready. */
  async function pollSetupDockerReadiness(generation = state.dockerStartPolling.generation) {
    if (generation !== state.dockerStartPolling.generation
      || !state.dockerStartPolling.active || state.dockerStartPolling.inFlight) return;
    state.dockerStartPolling.inFlight = true;
    try {
      const data = await window.omlorixServer.getState();
      // Ignore a response from an attempt that timed out or was superseded.
      if (generation !== state.dockerStartPolling.generation
        || !state.dockerStartPolling.active) return;
      acceptState(data);
      if (dockerStatusReady(data?.docker)) {
        refs.announcement.textContent = t('docker_ready');
        stopSetupDockerPolling();
        return;
      }
      // If Docker disappears while its desktop app is opening, return to the
      // ordinary missing-Docker state instead of polling a stale installation.
      if (!data?.docker?.installed) {
        stopSetupDockerPolling();
        return;
      }
    } catch (error) {
      if (generation !== state.dockerStartPolling.generation
        || !state.dockerStartPolling.active) return;
      // A transient status failure should be announced but must not end the
      // watch; Docker may still be progressing through its startup sequence.
      refs.announcement.textContent = translateLauncherMessage(error.message || String(error));
    } finally {
      if (generation === state.dockerStartPolling.generation) {
        state.dockerStartPolling.inFlight = false;
      }
    }
    if (generation === state.dockerStartPolling.generation) scheduleSetupDockerPoll();
  }

  /** Launch Docker Desktop and keep the triggering button in a loading state. */
  async function startDockerFromSetup() {
    const docker = state.current?.docker || {};
    if (state.busy || state.dockerStartPolling.active
      || !docker.installed || docker.running || !docker.canStartDesktop) return;

    state.dockerStartPolling.generation += 1;
    const generation = state.dockerStartPolling.generation;
    state.dockerStartPolling.active = true;
    state.dockerStartPolling.deadline = Date.now() + SETUP_DOCKER_START_TIMEOUT_MS;
    state.dockerStartPolling.timeoutTimer = window.setTimeout(() => {
      if (generation === state.dockerStartPolling.generation) stopSetupDockerPolling();
    }, SETUP_DOCKER_START_TIMEOUT_MS);
    renderReadiness(refs.readiness);
    try {
      await window.omlorixServer.startDockerDesktop();
      if (generation === state.dockerStartPolling.generation) scheduleSetupDockerPoll();
    } catch (error) {
      if (generation !== state.dockerStartPolling.generation) return;
      refs.announcement.textContent = translateLauncherMessage(error.message || String(error));
      stopSetupDockerPolling();
    }
  }

  /** Re-read Docker availability while preserving the user's setup choices. */
  async function refreshReadiness(button) {
    if (state.busy) return;
    button.disabled = true;
    button.classList.add('is-refreshing');
    button.setAttribute('aria-busy', 'true');
    try {
      acceptState(await window.omlorixServer.getState());
    } catch (error) {
      // Keep the visible Docker result intact and announce a failed refresh
      // through the setup flow's existing assistive-technology live region.
      refs.announcement.textContent = translateLauncherMessage(error.message || String(error));
    } finally {
      button.disabled = false;
      button.classList.remove('is-refreshing');
      button.setAttribute('aria-busy', 'false');
    }
  }

  /** Populate wizard choices once, preserving unsaved typing during refreshes. */
  function hydrateWizard(data) {
    const env = data.env || {};
    byId('setupMode').value = env.MODE || 'production';
    byId('setupUpdateChannel').value = data.serverSettings?.updateChannel || 'stable';
    setSelected('setupDatabase', String(env.OMLORIX_USE_BUNDLED_DB) === 'false' ? 'external' : 'bundled');
    setSelected('setupRedis', String(env.OMLORIX_USE_BUNDLED_REDIS) === 'false' ? 'external' : 'bundled');
    // The bundled Redis URL contains the generated Redis password and internal
    // `redis` hostname. Never carry that derived endpoint into External mode.
    // Genuine external endpoints survive launcher restarts and mode toggles.
    byId('setupDatabaseUrl').value = externalConnectionUrl(
      env.DATABASE_URL,
      ['postgres:', 'postgresql:'],
      ['postgres'],
    );
    byId('setupRedisUrl').value = externalConnectionUrl(
      env.REDIS_URL,
      ['redis:', 'rediss:'],
      ['redis'],
    );
    const provider = env.FILE_STORAGE_PROVIDER || 'local';
    byId('setupStorageMode').value = String(env.OMLORIX_USE_BUNDLED_STORAGE) === 'true' ? 'bundled' : provider;
    byId('setupFrontendPort').value = env.FRONTEND_HTTP_HOST_PORT || '8080';
    const bind = env.FRONTEND_HTTP_HOST_BIND || '127.0.0.1';
    const proxy = data.proxy?.config || {};
    const freshRecommendedSetup = Boolean(data.setup?.required && Number(data.setup?.currentStep || 0) === 0);
    const access = freshRecommendedSetup ? 'local'
      : bind === '127.0.0.1' && !proxy.enabled ? 'local'
        : (proxy.enabled || env.TRUST_PROXY_HEADERS === 'true' ? 'public' : 'lan');
    setSelected('setupAccess', access);
    setSelected('setupProxy', proxy.enabled ? 'launcher' : 'external');
    byId('setupTrustedProxies').value = env.TRUSTED_PROXIES || '';
    byId('setupProxyPort').value = proxy.httpPort || '8081';
    byId('setupProxyHttpsPort').value = proxy.httpsPort || '8443';
    byId('setupProxyHttps').checked = Boolean(proxy.httpsEnabled);
    byId('setupTlsCertificate').value = proxy.tlsCertPath || '';
    byId('setupTlsKey').value = proxy.tlsKeyPath || '';
    byId('setupPublicHost').value = env.TRUSTED_HOSTS || '';
    state.hydrated = true;
    renderStorageFields();
    renderConditionals();
  }

  /** Show provider-specific fields without exposing unrelated storage settings. */
  function renderStorageFields() {
    const provider = byId('setupStorageMode').value;
    const env = state.current?.env || {};
    const specs = {
      s3: [
        ['setupS3Endpoint', 's3_endpoint', 'url', env.FILE_STORAGE_S3_ENDPOINT_URL], ['setupS3Bucket', 'bucket', 'text', env.FILE_STORAGE_S3_BUCKET],
        ['setupS3Region', 'region', 'text', env.FILE_STORAGE_S3_REGION], ['setupS3AccessKey', 'access_key', 'text', env.FILE_STORAGE_S3_ACCESS_KEY_ID],
        ['setupS3SecretKey', 'secret_key', 'password', env.FILE_STORAGE_S3_SECRET_ACCESS_KEY],
      ],
      gcs: [['setupGcsBucket', 'bucket', 'text', env.FILE_STORAGE_GCS_BUCKET], ['setupGcsProject', 'project', 'text', env.FILE_STORAGE_GCS_PROJECT], ['setupGcsCredentials', 'credentials_json', 'password', env.FILE_STORAGE_GCS_CREDENTIALS_JSON, true]],
      azure: [['setupAzureContainer', 'container', 'text', env.FILE_STORAGE_AZURE_CONTAINER], ['setupAzureConnection', 'connection_string', 'password', env.FILE_STORAGE_AZURE_CONNECTION_STRING, true], ['setupAzureAccountUrl', 'account_url', 'url', env.FILE_STORAGE_AZURE_ACCOUNT_URL], ['setupAzureCredential', 'credential', 'password', env.FILE_STORAGE_AZURE_CREDENTIAL]],
      webdav: [['setupWebdavUrl', 'webdav_url', 'url', env.FILE_STORAGE_WEBDAV_URL], ['setupWebdavUsername', 'username', 'text', env.FILE_STORAGE_WEBDAV_USERNAME], ['setupWebdavPassword', 'password', 'password', env.FILE_STORAGE_WEBDAV_PASSWORD]],
    };
    const container = byId('setupStorageFields');
    container.replaceChildren();
    for (const [id, labelKey, type, value, wide] of specs[provider] || []) {
      const field = document.createElement('div'); field.className = `field${wide ? ' field-wide' : ''}`;
      const caption = document.createElement('label'); caption.className = 'field-label'; caption.htmlFor = id; caption.textContent = t(labelKey);
      const input = document.createElement(type === 'textarea' ? 'textarea' : 'input'); input.id = id; input.type = type; input.value = value || ''; input.autocomplete = 'off';
      field.appendChild(caption);
      if (type === 'password') {
        // Provider credentials use the same in-field eye control as every
        // other secret in the launcher instead of exposing an adjacent action.
        input.spellcheck = false;
        const secretWrap = document.createElement('div'); secretWrap.className = 'secret-input-wrap';
        const reveal = document.createElement('button'); reveal.className = 'secret-reveal-button'; reveal.type = 'button';
        reveal.dataset.showAriaKey = 'show_secret_value'; reveal.dataset.hideAriaKey = 'hide_secret_value';
        secretWrap.append(input, reveal);
        field.appendChild(secretWrap);
        bindSetupRevealButton(reveal, input);
      } else {
        field.appendChild(input);
      }
      container.appendChild(field);
    }
  }

  function renderConditionals() {
    document.querySelectorAll('[data-show-when]').forEach((element) => {
      const [name, value] = element.dataset.showWhen.split(':');
      element.hidden = selected(name) !== value;
    });
    const isPublic = selected('setupAccess') === 'public';
    document.querySelectorAll('.setup-public-field').forEach((element) => { element.hidden = !isPublic; });
    document.querySelectorAll('[data-show-when^="setupProxy:"]').forEach((element) => {
      element.hidden = !isPublic || selected('setupProxy') !== element.dataset.showWhen.split(':')[1];
    });
    // Recommended setup has no extra release choices. For custom setup, show
    // the complete section directly instead of making users open a disclosure.
    byId('setupRuntimeAdvanced').hidden = selected('setupType') !== 'custom';
    const tlsEnabled = Boolean(byId('setupProxyHttps')?.checked);
    byId('setupTlsFields').hidden = !tlsEnabled;
    byId('setupTlsLaterNotice').hidden = tlsEnabled;
  }

  function setError(id, message) {
    const error = byId(id);
    if (!error) return;
    error.textContent = message || '';
    const input = error.parentElement?.querySelector('input');
    if (input) input.setAttribute('aria-invalid', message ? 'true' : 'false');
  }

  function validPort(value) {
    const number = Number(value);
    return Number.isInteger(number) && number >= 1 && number <= 65535;
  }

  function validateDataStep() {
    let valid = true;
    setError('setupDatabaseUrlError', ''); setError('setupRedisUrlError', '');
    byId('setupStorageError').textContent = '';
    if (selected('setupDatabase') === 'external' && !/^postgres(?:ql)?:\/\//i.test(byId('setupDatabaseUrl').value.trim())) {
      setError('setupDatabaseUrlError', t('invalid_postgres')); valid = false;
    }
    if (selected('setupRedis') === 'external' && !/^rediss?:\/\//i.test(byId('setupRedisUrl').value.trim())) {
      setError('setupRedisUrlError', t('invalid_redis')); valid = false;
    }
    const provider = byId('setupStorageMode').value;
    if (selected('setupDatabase') === 'external' && selected('setupRedis') === 'external' && ['local', 'bundled'].includes(provider)) {
      byId('setupStorageError').textContent = t('managed_storage_required');
      byId('setupStorageMode').setAttribute('aria-invalid', 'true');
      valid = false;
    } else {
      byId('setupStorageMode').setAttribute('aria-invalid', 'false');
    }
    const requiredByProvider = { s3: ['setupS3Bucket', 'setupS3AccessKey', 'setupS3SecretKey'], gcs: ['setupGcsBucket', 'setupGcsCredentials'], azure: ['setupAzureContainer'], webdav: ['setupWebdavUrl'] };
    for (const id of requiredByProvider[provider] || []) {
      const input = byId(id);
      input?.setAttribute('aria-invalid', input.value.trim() ? 'false' : 'true');
      if (!input?.value.trim()) valid = false;
    }
    return valid;
  }

  function validateAccessStep() {
    let valid = true;
    let firstInvalidInput = null;
    const invalidate = (inputId, errorId, message) => {
      setError(errorId, message);
      const input = byId(inputId);
      input?.setAttribute('aria-invalid', 'true');
      if (!firstInvalidInput) firstInvalidInput = input;
      valid = false;
    };

    const portInput = byId('setupFrontendPort');
    byId('setupAccessError').textContent = '';
    setError('setupFrontendPortError', '');
    if (!validPort(portInput.value)) {
      invalidate('setupFrontendPort', 'setupFrontendPortError', t('invalid_port'));
    }

    const isPublic = selected('setupAccess') === 'public';
    const proxyMode = selected('setupProxy');
    setError('setupPublicHostError', '');
    if (isPublic && !byId('setupPublicHost').value.trim()) {
      invalidate('setupPublicHost', 'setupPublicHostError', t('required_field'));
    }

    setError('setupTrustedProxiesError', '');
    if (isPublic && proxyMode === 'external' && !byId('setupTrustedProxies').value.trim()) {
      invalidate('setupTrustedProxies', 'setupTrustedProxiesError', t('required_field'));
    }

    setError('setupProxyPortError', '');
    setError('setupProxyHttpsPortError', '');
    byId('setupTlsError').textContent = '';
    if (isPublic && proxyMode === 'launcher') {
      const proxyPortValid = validPort(byId('setupProxyPort').value);
      const httpsPortValid = validPort(byId('setupProxyHttpsPort').value);
      if (!proxyPortValid) invalidate('setupProxyPort', 'setupProxyPortError', t('invalid_port'));
      if (!httpsPortValid) invalidate('setupProxyHttpsPort', 'setupProxyHttpsPortError', t('invalid_port'));
      if (proxyPortValid && validPort(portInput.value)
        && Number(byId('setupProxyPort').value) === Number(portInput.value)) {
        invalidate('setupProxyPort', 'setupProxyPortError', t('proxy_frontend_port_different'));
      }
      if (proxyPortValid && httpsPortValid && byId('setupProxyPort').value === byId('setupProxyHttpsPort').value) {
        invalidate('setupProxyHttpsPort', 'setupProxyHttpsPortError', t('proxy_ports_different'));
      }
      if (byId('setupProxyHttps').checked && (!byId('setupTlsCertificate').value || !byId('setupTlsKey').value)) {
        byId('setupTlsError').textContent = t('tls_files_required');
        if (!firstInvalidInput) firstInvalidInput = byId('setupChooseTlsCertificate');
        valid = false;
      }
    }

    if (!valid) {
      firstInvalidInput?.focus();
      firstInvalidInput?.scrollIntoView({ block: 'center', behavior: 'auto' });
    }
    return valid;
  }

  /** Render IPC proxy validation on a field that is visible in the active mode. */
  function renderAccessSaveError(error) {
    const validationErrors = error?.validationErrors || {};
    const fieldTargets = {
      httpPort: ['setupProxyPort', 'setupProxyPortError'],
      httpsPort: ['setupProxyHttpsPort', 'setupProxyHttpsPortError'],
      target: ['setupProxyPort', 'setupProxyPortError'],
    };
    const tlsKeys = new Set(['tlsCertPath', 'tlsKeyPath', 'tlsCaPath']);
    const generalMessages = [];
    let firstMessage = '';
    let firstVisibleTarget = null;
    const tlsMessages = [];

    for (const [key, rawMessage] of Object.entries(validationErrors)) {
      const message = translateLauncherMessage(rawMessage);
      if (!message) continue;
      firstMessage ||= message;
      if (tlsKeys.has(key) && !byId('setupTlsFields').hidden) {
        tlsMessages.push(message);
        firstVisibleTarget ||= byId('setupTlsError');
        continue;
      }
      const [inputId, errorId] = fieldTargets[key] || [];
      const input = inputId ? byId(inputId) : null;
      if (input && !input.closest('[hidden]')) {
        setError(errorId, message);
        firstVisibleTarget ||= input;
      } else {
        generalMessages.push(message);
      }
    }

    if (tlsMessages.length) byId('setupTlsError').textContent = tlsMessages.join(' ');
    const fallbackMessage = error?.message
      ? translateLauncherMessage(error.message)
      : t('required_field');
    if (!firstMessage) firstMessage = fallbackMessage;
    if (!Object.keys(validationErrors).length) generalMessages.push(fallbackMessage);
    byId('setupAccessError').textContent = generalMessages.join(' ');
    firstVisibleTarget ||= byId('setupAccessError');
    refs.announcement.textContent = firstMessage;
    firstVisibleTarget?.scrollIntoView({ block: 'center', behavior: 'auto' });
    return firstMessage;
  }

  async function saveRuntimeStep() {
    const recommended = selected('setupType') === 'recommended';
    const data = await window.omlorixServer.saveSettings({
      mode: recommended ? 'production' : byId('setupMode').value,
      updateChannel: recommended ? 'stable' : byId('setupUpdateChannel').value,
    });
    acceptState(data);
  }

  /** Use the native file picker already shared with the permanent Proxy page. */
  async function chooseTlsFile(kind, input) {
    try {
      const result = await window.omlorixServer.chooseProxyTlsFile(kind, input.value);
      if (!result?.canceled && result.path) {
        input.value = result.path;
        byId('setupTlsError').textContent = '';
      }
    } catch (error) {
      byId('setupTlsError').textContent = translateLauncherMessage(error.message || String(error));
    }
  }

  function storagePayload() {
    const provider = byId('setupStorageMode').value;
    return {
      useBundledStorage: provider === 'bundled', fileStorageProvider: provider === 'bundled' ? 's3' : provider,
      fileStorageS3EndpointUrl: byId('setupS3Endpoint')?.value || '', fileStorageS3Bucket: byId('setupS3Bucket')?.value || '', fileStorageS3Region: byId('setupS3Region')?.value || '', fileStorageS3AccessKeyId: byId('setupS3AccessKey')?.value || '', fileStorageS3SecretAccessKey: byId('setupS3SecretKey')?.value || '',
      fileStorageGcsBucket: byId('setupGcsBucket')?.value || '', fileStorageGcsProject: byId('setupGcsProject')?.value || '', fileStorageGcsCredentialsJson: byId('setupGcsCredentials')?.value || '',
      fileStorageAzureContainer: byId('setupAzureContainer')?.value || '', fileStorageAzureConnectionString: byId('setupAzureConnection')?.value || '', fileStorageAzureAccountUrl: byId('setupAzureAccountUrl')?.value || '', fileStorageAzureCredential: byId('setupAzureCredential')?.value || '',
      fileStorageWebdavUrl: byId('setupWebdavUrl')?.value || '', fileStorageWebdavUsername: byId('setupWebdavUsername')?.value || '', fileStorageWebdavPassword: byId('setupWebdavPassword')?.value || '',
    };
  }

  async function saveDataStep() {
    if (!validateDataStep()) throw new Error(t('required_field'));
    const bundledDb = selected('setupDatabase') === 'bundled';
    const bundledRedis = selected('setupRedis') === 'bundled';
    let data = await window.omlorixServer.saveSettings({
      useBundledDB: bundledDb, databaseUrl: bundledDb ? '' : byId('setupDatabaseUrl').value.trim(),
      // First-run setup offers bundled/external Redis rather than Off, so it
      // must explicitly re-enable Redis if imported settings disabled it.
      redisEnabled: true, useBundledRedis: bundledRedis, redisUrl: bundledRedis ? state.current.env.REDIS_URL : byId('setupRedisUrl').value.trim(),
      ...storagePayload(),
    });
    if (byId('setupStorageMode').value === 'bundled'
      && (!data.env?.MINIO_ROOT_USER || !data.env?.MINIO_ROOT_PASSWORD)) {
      data = await window.omlorixServer.regenerateSecrets(['MINIO_ROOT_USER', 'MINIO_ROOT_PASSWORD']);
    }
    acceptState(data);
  }

  /** Save the complete proxy form because proxy validation is intentionally centralized. */
  async function saveAccessStep() {
    if (!validateAccessStep()) throw new Error(t('required_field'));
    const access = selected('setupAccess');
    const proxyMode = selected('setupProxy');
    const isPublic = access === 'public';
    const launcherProxy = isPublic && proxyMode === 'launcher';
    const externalProxy = isPublic && proxyMode === 'external';
    const trusted = externalProxy ? byId('setupTrustedProxies').value.trim() : (launcherProxy ? '127.0.0.1,::1' : '');
    const currentProxy = state.current?.proxy?.config || {};
    const frontendPort = byId('setupFrontendPort').value;
    const requestedProxyPort = byId('setupProxyPort').value || '8081';
    const inactiveProxyPort = requestedProxyPort === frontendPort ? (frontendPort === '8081' ? '8082' : '8081') : requestedProxyPort;
    let data;
    try {
      data = await window.omlorixServer.saveProxySettings({
        trustProxyHeaders: isPublic, trustedProxies: trusted, trustedHosts: isPublic ? byId('setupPublicHost').value.trim() : '',
        uvicornForwardedAllowIps: trusted || '127.0.0.1,::1', rateLimitTrustedProxies: trusted, authTrustedProxies: trusted, rateLimitProxySettingsCacheSeconds: '60',
        frontendHttpHostBind: access === 'local' || launcherProxy ? '127.0.0.1' : '0.0.0.0', frontendHttpHostPort: frontendPort,
        apiLbTraefikWebHostPort: '8080', apiLbTraefikDashboardHostPort: '8081',
        enabled: launcherProxy, autostart: launcherProxy, bindHost: '0.0.0.0', httpPort: launcherProxy ? requestedProxyPort : inactiveProxyPort, httpsEnabled: launcherProxy && byId('setupProxyHttps').checked,
        httpsPort: byId('setupProxyHttpsPort').value || '8443', redirectHttpToHttps: launcherProxy && byId('setupProxyHttps').checked, tlsCertPath: byId('setupTlsCertificate').value || currentProxy.tlsCertPath || '', tlsKeyPath: byId('setupTlsKey').value || currentProxy.tlsKeyPath || '', tlsCaPath: currentProxy.tlsCaPath || '',
      });
      if (data?.ok === false) {
        const validationError = new Error(Object.values(data.validationErrors || {})[0] || t('required_field'));
        validationError.validationErrors = data.validationErrors || {};
        throw validationError;
      }
    } catch (error) {
      error.setupAnnouncement = renderAccessSaveError(error);
      throw error;
    }
    acceptState(data);
  }

  function activeSecretDefinitions() {
    return SECRET_DEFINITIONS.filter((definition) => !definition.bundled || boolEnv(definition.bundled));
  }

  function createSecretRow(definition, value, editable, errorScope) {
    const row = document.createElement('div'); row.className = 'setup-secret-row'; row.dataset.secretKey = definition.key;
    const inputId = `${errorScope}-secret-${definition.setting}`;
    const copy = document.createElement('label'); copy.htmlFor = inputId;
    const name = document.createElement('span'); name.className = 'setup-secret-name'; name.textContent = definition.key;
    const purpose = document.createElement('span'); purpose.className = 'setup-secret-purpose'; purpose.textContent = t(definition.purposeKey);
    copy.append(name, purpose);
    const input = document.createElement('input'); input.id = inputId; input.className = 'setup-secret-value'; input.type = 'password'; input.value = value || ''; input.autocomplete = 'new-password'; input.spellcheck = false; input.readOnly = !editable;
    const error = document.createElement('span');
    error.className = 'setup-secret-error field-error';
    error.setAttribute('role', 'alert');
    error.id = `${errorScope}Secret${definition.setting[0].toUpperCase()}${definition.setting.slice(1)}Error`;
    input.setAttribute('aria-describedby', error.id);
    const secretWrap = document.createElement('div'); secretWrap.className = 'secret-input-wrap setup-secret-input-wrap';
    const reveal = document.createElement('button'); reveal.className = 'secret-reveal-button'; reveal.type = 'button';
    reveal.dataset.showAriaKey = 'show_secret_value'; reveal.dataset.hideAriaKey = 'hide_secret_value';
    secretWrap.append(input, reveal);
    bindSetupRevealButton(reveal, input);
    row.append(copy, secretWrap, error);
    return row;
  }

  /** Synchronize a sensitive input and its translated Show/Hide control. */
  function setSetupInputRevealed(button, input, revealed) {
    const showAriaKey = button.dataset.showAriaKey || 'show';
    const hideAriaKey = button.dataset.hideAriaKey || 'hide';
    input.type = revealed ? 'text' : 'password';
    button.setAttribute('aria-label', t(revealed ? hideAriaKey : showAriaKey));
    button.setAttribute('aria-pressed', revealed ? 'true' : 'false');
    window.OmlorixLauncherIcons.setSecretRevealIcon(button, revealed);
  }

  /** Attach the common reveal behavior used by URLs and generated secrets. */
  function bindSetupRevealButton(button, input) {
    if (!button || !input) return;
    if (input.id) button.setAttribute('aria-controls', input.id);
    setSetupInputRevealed(button, input, false);
    button.addEventListener('click', () => {
      const revealed = button.getAttribute('aria-pressed') !== 'true';
      setSetupInputRevealed(button, input, revealed);
      input.focus();
    });
  }

  /** Bind static connection URL reveal controls after translations are ready. */
  function initializeSetupConnectionRevealButtons() {
    document.querySelectorAll('[data-setup-reveal-for]').forEach((button) => {
      bindSetupRevealButton(button, byId(button.dataset.setupRevealFor));
    });
  }

  function renderSetupSecretList() {
    refs.secretList.replaceChildren();
    for (const definition of activeSecretDefinitions()) {
      refs.secretList.appendChild(createSecretRow(definition, state.current?.env?.[definition.key], true, 'setup'));
    }
    renderBackupConfirm();
  }

  function renderPermanentSecretList() {
    if (!refs.permanentList || !state.current) return;
    refs.permanentList.replaceChildren();
    for (const definition of activeSecretDefinitions().filter((item) => !['JWT_SECRET_KEY', 'ENCRYPTION_KEY', 'PASSWORD_RESET_IDENTIFIER_HASH_SALT'].includes(item.key))) {
      const editable = !state.current.setup?.complete || ['LOG_IP_HASH_SALT', 'BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE'].includes(definition.key);
      const row = createSecretRow(definition, state.current.env?.[definition.key], editable, 'permanent');
      const input = row.querySelector('input');
      input.addEventListener('change', async () => {
        if (input.readOnly) return;
        try {
          setBusy(true);
          const data = await saveSecretRows(refs.permanentList);
          setManagementMessage(definition.key === 'JWT_SECRET_KEY'
            ? t('secret_saved_restart')
            : ((data.automaticEnvBackupError || !data.setup?.backupConfigured)
              ? t('secret_saved')
              : t('backup_saved_now')));
        }
        catch (error) {
          setManagementMessage(translateLauncherMessage(error.message || String(error)), true);
        }
        finally { setBusy(false); }
      });
      refs.permanentList.appendChild(row);
    }

    // The existing Settings editor owns the JWT input. Add the operator action
    // beside that single canonical field instead of rendering a duplicate.
    const jwtInput = byId('jwtSecretKeyInput');
    const jwtField = jwtInput?.closest('.field');
    if (jwtInput && jwtField) {
      let warning = byId('jwtSigningKeyOperatorWarning');
      if (!warning) {
        warning = document.createElement('p');
        warning.id = 'jwtSigningKeyOperatorWarning';
        warning.className = 'field-hint';
        jwtField.appendChild(warning);
      }
      warning.textContent = t('secret_jwt');

      let generateButton = byId('generateJwtSigningKeyButton');
      if (!generateButton) {
        generateButton = document.createElement('button');
        generateButton.id = 'generateJwtSigningKeyButton';
        generateButton.className = 'btn btn-ghost btn-sm';
        generateButton.type = 'button';
        jwtField.appendChild(generateButton);
      }
      generateButton.textContent = t('generate_signing_key');
      generateButton.onclick = async () => {
        try {
          setBusy(true);
          const data = await window.omlorixServer.regenerateSecrets(['JWT_SECRET_KEY']);
          acceptState(data, { hydrate: true });
          setManagementMessage(t('secret_saved_restart'));
        } catch (error) {
          setManagementMessage(error.message || String(error), true);
        } finally {
          setBusy(false);
        }
      };
    }
  }

  /** Present secret-management outcomes without retaining a stale severity. */
  function setManagementMessage(message = '', isError = false) {
    if (!refs.managementMessage) return;
    refs.managementMessage.textContent = String(message || '');
    refs.managementMessage.classList.toggle('is-error', Boolean(message) && isError);
  }

  /** Keep automatic-copy failures visible without turning a successful save into an error. */
  function renderAutomaticBackupWarning(errorCode) {
    if (!refs.automaticBackupWarning) return;
    const code = String(errorCode || '').trim();
    refs.automaticBackupWarning.hidden = !code;
    refs.automaticBackupWarningTitle.textContent = code
      ? t('automatic_backup_failed_title')
      : '';
    refs.automaticBackupWarningDescription.textContent = code
      ? t(code === 'outdated' ? 'automatic_backup_outdated_desc' : 'automatic_backup_failed_desc')
      : '';
  }

  /**
   * Encryption and service credentials cannot be rotated by changing one text
   * field after data exists. Lock those direct editors after first launch;
   * importing a known recovery bundle remains available for restoration.
   */
  function applyPostLaunchSecretSafety() {
    const complete = Boolean(state.current?.setup?.complete);
    for (const id of ['encryptionKeyInput', 'databasePasswordInput', 'redisPasswordInput', 'minioRootPasswordInput']) {
      const input = byId(id);
      if (input) input.readOnly = complete;
    }
    if (refs.managementDescription) {
      refs.managementDescription.textContent = t(complete ? 'post_launch_secret_desc_runtime' : 'secret_actions_desc');
    }
  }

  function validateSecretValue(key, value) {
    if (!value) return t('required_field');
    if (key === 'JWT_SECRET_KEY' && new TextEncoder().encode(value.trim()).length < 64) return t('secret_jwt_min_length');
    if (key === 'ENCRYPTION_KEY' && !/^[A-Za-z0-9_-]{43}=$/.test(value)) return t('secret_fernet_invalid');
    if (value.length < 16 && key !== 'MINIO_ROOT_USER') return t('secret_min_length');
    return '';
  }

  /** Validate all secret inputs and render each field-level error. */
  function validateSecretRows(container) {
    let firstInvalidInput = null;
    for (const row of container.querySelectorAll('[data-secret-key]')) {
      const key = row.dataset.secretKey; const input = row.querySelector('input'); const value = input.value;
      const error = validateSecretValue(key, value);
      const errorElement = row.querySelector('.setup-secret-error');
      input.setAttribute('aria-invalid', error ? 'true' : 'false');
      if (errorElement) errorElement.textContent = error;
      if (error) {
        firstInvalidInput ||= input;
      }
    }

    // Individual length checks cannot enforce separation between two fields.
    // Match the backend's trimmed comparison and attach the error to the salt,
    // which is the value the operator must replace with independent material.
    const jwtRow = container.querySelector('[data-secret-key="JWT_SECRET_KEY"]');
    const logIpSaltRow = container.querySelector('[data-secret-key="LOG_IP_HASH_SALT"]');
    const jwtSecret = jwtRow?.querySelector('input')?.value.trim() || '';
    const logIpHashSalt = logIpSaltRow?.querySelector('input')?.value.trim() || '';
    if (jwtSecret && logIpHashSalt && jwtSecret === logIpHashSalt) {
      const input = logIpSaltRow.querySelector('input');
      const errorElement = logIpSaltRow.querySelector('.setup-secret-error');
      input.setAttribute('aria-invalid', 'true');
      if (errorElement) errorElement.textContent = t('secret_log_ip_must_differ_from_jwt');
      firstInvalidInput ||= input;
    }
    return firstInvalidInput;
  }

  async function saveSecretRows(container) {
    const firstInvalidInput = validateSecretRows(container);
    if (firstInvalidInput) {
      // Validate every row before returning so users can correct all visible
      // errors at once rather than discovering them one export attempt at a time.
      firstInvalidInput.focus();
      firstInvalidInput.scrollIntoView({ block: 'center', behavior: 'auto' });
      throw new Error(t('required_field'));
    }
    const payload = {};
    for (const row of container.querySelectorAll('[data-secret-key]')) {
      const definition = SECRET_DEFINITIONS.find((candidate) => candidate.key === row.dataset.secretKey);
      if (definition) payload[definition.setting] = row.querySelector('input').value;
    }
    const data = await window.omlorixServer.saveSettings(payload);
    state.exportInvalidClicks = 0;
    state.exportInvalidArmedUntil = 0;
    acceptState(data);
    return data;
  }

  function renderBackupConfirm() {
    const current = Boolean(state.current?.setup?.backupCurrent);
    refs.backupConfirm.dataset.state = current ? 'current' : 'missing';
    refs.backupConfirm.textContent = current ? t('backup_saved') : t('backup_required');
    refs.next.disabled = state.busy || (state.step === 4 && !current);
  }

  async function exportSecrets() {
    let failureMessage = '';
    let keepStatusMessage = false;
    try {
      setBusy(true);
      if (state.step === 4) {
        const invalidInput = validateSecretRows(refs.secretList);
        if (invalidInput) {
          const now = Date.now();
          if (now > state.exportInvalidArmedUntil) state.exportInvalidClicks = 0;
          state.exportInvalidClicks += 1;
          state.exportInvalidArmedUntil = now + 10000;

          if (state.exportInvalidClicks < 3) {
            const remaining = 3 - state.exportInvalidClicks;
            refs.backupConfirm.textContent = t('export_last_saved_remaining', { remaining });
            refs.backupConfirm.dataset.state = 'missing';
            invalidInput.focus();
            invalidInput.scrollIntoView({ block: 'center', behavior: 'auto' });
            keepStatusMessage = true;
            return;
          }

          // An explicit third click exports the persisted valid secrets. The
          // invalid edits remain unsaved, so the recovery bundle stays safe to
          // import even when a user is still editing the fields above.
          state.exportInvalidClicks = 0;
          state.exportInvalidArmedUntil = 0;
        } else {
          await saveSecretRows(refs.secretList);
        }
      }
      const result = await window.omlorixServer.chooseSecretsExport();
      if (!result?.canceled && result.export?.state) acceptState(result.export.state, { hydrate: true });
    } catch (error) {
      failureMessage = t('export_failed', {
        error: translateLauncherMessage(error.message || String(error)),
      });
      // Report the error on the surface where export was requested. Previously
      // renderBackupConfirm() immediately replaced this message in finally,
      // making validation and filesystem failures look like a dead button.
      if (state.step === 4 && !refs.overlay.hidden) {
        refs.backupConfirm.textContent = failureMessage;
        refs.backupConfirm.dataset.state = 'missing';
      } else {
        setManagementMessage(failureMessage, true);
      }
    } finally {
      setBusy(false);
      if (!failureMessage && !keepStatusMessage) renderBackupConfirm();
    }
  }

  /** Refresh the remembered .env copy without reopening the location picker. */
  async function saveEnvBackupNow() {
    try {
      setBusy(true);
      const result = state.current?.setup?.backupConfigured
        ? await window.omlorixServer.saveEnvBackupNow()
        : await window.omlorixServer.chooseSecretsExport();
      const nextState = result?.state || result?.export?.state;
      if (nextState) {
        acceptState(nextState);
        setManagementMessage(t('backup_saved_now'));
      }
    } catch (error) {
      setManagementMessage(t('export_failed', {
        error: translateLauncherMessage(error.message || String(error)),
      }), true);
    } finally {
      setBusy(false);
    }
  }

  /** Let an operator replace an unavailable or obsolete automatic destination. */
  async function changeEnvBackupLocation() {
    try {
      setBusy(true);
      const result = await window.omlorixServer.chooseSecretsExport();
      if (!result?.canceled && result.export?.state) {
        acceptState(result.export.state);
        setManagementMessage(t('backup_saved_now'));
      }
    } catch (error) {
      setManagementMessage(t('export_failed', {
        error: translateLauncherMessage(error.message || String(error)),
      }), true);
    } finally {
      setBusy(false);
    }
  }

  /** Disable future recovery-copy refreshes after an explicit confirmation. */
  async function disableAutomaticEnvBackup() {
    if (!state.current?.setup?.backupConfigured || state.busy) return;
    const showDialog = window.omlorixShowLauncherDialog;
    if (typeof showDialog !== 'function') return;
    const confirmed = await showDialog({
      title: t('disable_automatic_backup_title'),
      message: t('disable_automatic_backup_desc'),
      confirmText: t('disable_automatic_backup_confirm'),
      cancelText: t('cancel'),
    });
    if (!confirmed) return;

    try {
      setBusy(true);
      const result = await window.omlorixServer.disableEnvBackup();
      if (result?.state) acceptState(result.state);
      setManagementMessage(t('automatic_backup_disabled'));
    } catch (error) {
      setManagementMessage(t('disable_automatic_backup_failed', {
        error: translateLauncherMessage(error.message || String(error)),
      }), true);
    } finally {
      setBusy(false);
    }
  }

  /** Restore the complete trusted snapshot without mutating running services. */
  async function importSecrets() {
    try {
      setBusy(true);
      const result = await window.omlorixServer.chooseSecretsImport();
      if (!result?.canceled && result.state) {
        state.hydrated = false; acceptState(result.state, { hydrate: true });
        setManagementMessage(t('imported'));
      }
    } catch (error) {
      setManagementMessage(t('import_failed', {
        error: translateLauncherMessage(error.message || String(error)),
      }), true);
    } finally { setBusy(false); renderBackupConfirm(); }
  }

  /** Generate a fresh complete secret set during first-run setup only. */
  async function regenerateSetupSecrets() {
    try {
      setBusy(true);
      const keys = activeSecretDefinitions().map((definition) => definition.key);
      const data = await window.omlorixServer.regenerateSecrets(keys);
      state.hydrated = false; acceptState(data, { hydrate: true });
      setManagementMessage(t('regenerated'));
      if (state.step === 4) renderSetupSecretList();
    } catch (error) {
      setManagementMessage(translateLauncherMessage(error.message || String(error)), true);
    }
    finally { setBusy(false); }
  }

  function addReviewRow(label, value) {
    const row = document.createElement('div'); row.className = 'setup-review-row';
    const key = document.createElement('span'); key.textContent = label;
    const result = document.createElement('strong'); result.textContent = value;
    row.append(key, result); refs.review.appendChild(row);
  }

  function renderReview() {
    refs.review.replaceChildren();
    const storage = byId('setupStorageMode').selectedOptions[0]?.textContent || byId('setupStorageMode').value;
    addReviewRow(t('setup_type'), t(selected('setupType')));
    addReviewRow(t('database'), selected('setupDatabase') === 'bundled' ? t('built_in_database') : t('external_database'));
    addReviewRow(t('cache'), selected('setupRedis') === 'bundled' ? t('built_in_cache') : t('external_cache'));
    addReviewRow(t('storage'), storage);
    const accessLabels = { local: t('local_only'), lan: t('lan_access'), public: t('public_access') };
    addReviewRow(t('access'), accessLabels[selected('setupAccess')] || selected('setupAccess'));
    addReviewRow(t('proxy'), selected('setupAccess') === 'public' ? t(selected('setupProxy') === 'launcher' ? 'launcher_proxy' : 'existing_proxy') : t('disabled'));
    addReviewRow(t('backup'), state.current?.setup?.backupCurrent ? t('current') : t('required'));
  }

  async function persistStep(nextStep, { complete = false } = {}) {
    const checkpoint = await window.omlorixServer.saveSetupProgress({ currentStep: nextStep, complete });
    if (checkpoint?.setup && state.current) {
      acceptState({
        ...state.current,
        setup: { ...state.current.setup, ...checkpoint.setup },
      });
    }
    return checkpoint;
  }

  async function goNext() {
    if (state.busy) return;
    // A stale click or programmatic invocation must not enter the completion
    // panel after Docker readiness has removed the visible launch action.
    if (state.step === 5 && !dockerReady()) {
      renderStep();
      return;
    }
    try {
      setBusy(true);
      if (state.step === 1) await saveRuntimeStep();
      if (state.step === 2) await saveDataStep();
      if (state.step === 3) await saveAccessStep();
      if (state.step === 4) {
        // Secret values are validated and saved by the export action. Keep
        // navigation independent so a user can continue after exporting the
        // last valid backup, even while reviewing incomplete edits here.
        if (!state.current?.setup?.backupCurrent) throw new Error(t('backup_required'));
      }
      if (state.step === 5) {
        await launchOrFinish();
        return;
      }
      const nextStep = Math.min(5, state.step + 1);
      if (nextStep === 5) {
        // Reaching Review means configuration and backup requirements are met.
        // Starting services remains a separate, explicit choice on this page.
        state.showUntilDismissed = true;
        try {
          await persistStep(nextStep, { complete: true });
        } catch (error) {
          state.showUntilDismissed = false;
          throw error;
        }
      } else {
        await persistStep(nextStep);
      }
      state.step = nextStep;
      renderStep();
    } catch (error) {
      refs.announcement.textContent = error.setupAnnouncement
        || translateLauncherMessage(error.message || String(error));
      if (state.step === 4) {
        refs.backupConfirm.textContent = translateLauncherMessage(error.message || String(error));
      }
    } finally { setBusy(false); }
  }

  async function goBack() {
    if (state.busy || state.step <= 0) return;
    const previousStep = state.step;
    try {
      setBusy(true);
      await persistStep(previousStep - 1);
      state.step = previousStep - 1;
      renderStep();
    } catch (error) {
      refs.announcement.textContent = error.setupAnnouncement
        || translateLauncherMessage(error.message || String(error));
    } finally {
      setBusy(false);
    }
  }

  function appendLaunchLog(text, { preserveStream = false } = {}) {
    // stdout/stderr events are arbitrary chunks. The shared renderer preserves
    // carriage-return and Docker ANSI redraws instead of treating each event as
    // a completed line; launcher-authored summaries remain separate messages.
    window.OmlorixTerminalOutput.append(refs.launchLog, text, {
      separate: !preserveStream,
    });
  }

  /** Start the actual Docker stack after Docker and Compose are both ready. */
  async function launchOrFinish() {
    // Retry buttons and delayed callbacks can outlive a Docker status change.
    // Return to Review instead of exposing the Docker-only completion step.
    if (!dockerReady()) {
      state.step = 5;
      renderStep();
      return;
    }
    state.showUntilDismissed = true;
    state.step = 6;
    window.OmlorixTerminalOutput.clear(refs.launchLog);
    refs.retry.hidden = true;
    refs.openOmlorix.hidden = !dockerReady();
    renderStep();

    refs.doneMark.textContent = '…'; refs.doneTitle.textContent = t('starting_title'); refs.doneDescription.textContent = t('starting_desc');
    try {
      const started = await window.omlorixServer.start();
      acceptState(started);
      await persistStep(6, { complete: true });
      refs.doneMark.textContent = '✓'; refs.doneTitle.textContent = t('done_title'); refs.doneDescription.textContent = t('done_desc'); refs.openOmlorix.hidden = false;
      appendLaunchLog(t('setup_complete_log', { url: state.current?.stack?.url || 'Omlorix' }));
    } catch (error) {
      refs.doneMark.textContent = '!'; refs.doneTitle.textContent = t('start_failed_title'); refs.doneDescription.textContent = t('start_failed_desc'); refs.retry.hidden = false; refs.openOmlorix.hidden = true;
      appendLaunchLog(translateLauncherMessage(error.message || String(error)));
    }
  }

  function dismissSetup() {
    state.showUntilDismissed = false;
    refs.overlay.hidden = true;
    stopSetupDockerReadinessMonitor();
    dispatchLauncherState(state.current);
  }

  // All conditional choices update immediately and preserve keyboard behavior.
  document.querySelectorAll('input[name="setupType"], input[name="setupDatabase"], input[name="setupRedis"], input[name="setupAccess"], input[name="setupProxy"]').forEach((input) => {
    input.addEventListener('change', () => {
      if (input.name === 'setupType' && input.value === 'recommended' && input.checked) {
        setSelected('setupDatabase', 'bundled'); setSelected('setupRedis', 'bundled'); byId('setupStorageMode').value = 'local';
      }
      renderConditionals(); renderStorageFields();
    });
  });
  byId('setupStorageMode').addEventListener('change', renderStorageFields);
  byId('setupProxyHttps').addEventListener('change', renderConditionals);
  byId('setupChooseTlsCertificate').addEventListener('click', () => chooseTlsFile('cert', byId('setupTlsCertificate')));
  byId('setupChooseTlsKey').addEventListener('click', () => chooseTlsFile('key', byId('setupTlsKey')));
  refs.next.addEventListener('click', goNext);
  refs.back.addEventListener('click', goBack);
  refs.downloadSetup.addEventListener('click', exportSecrets);
  refs.downloadPermanent.addEventListener('click', saveEnvBackupNow);
  refs.changeBackupLocation.addEventListener('click', changeEnvBackupLocation);
  refs.disableAutomaticBackup.addEventListener('click', disableAutomaticEnvBackup);
  refs.importSetup.addEventListener('click', importSecrets);
  refs.importPermanent.addEventListener('click', importSecrets);
  refs.regenerateSetup.addEventListener('click', regenerateSetupSecrets);
  refs.retry.addEventListener('click', launchOrFinish);
  refs.reviewDashboard.addEventListener('click', dismissSetup);
  refs.dashboard.addEventListener('click', dismissSetup);
  refs.openOmlorix.addEventListener('click', () => { window.omlorixServer.openUrl(); dismissSetup(); });

  window.omlorixServer.onOperationOutput((payload) => {
    if (state.step === 6 && state.showUntilDismissed) {
      appendLaunchLog(payload.text || '', { preserveStream: true });
    }
  });

  window.addEventListener('omlorix:state-rendered', (event) => {
    if (event.detail && event.detail !== state.current) {
      // This state already came from the Launcher. Rendering it in the setup
      // surface must not echo it back and trigger another release refresh.
      acceptState(event.detail, { broadcast: false });
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshSetupDockerReadinessMonitor();
  });

  applyTranslations();
  initializeSetupConnectionRevealButtons();
  mountExistingSecretsEditor();
  renderStep();
  window.omlorixServer.getState().then(async (data) => {
    // Show the appropriate surface as soon as the persisted state is known.
    // Secret generation can require a second IPC write, but it must not keep a
    // first-run user on the dashboard while that write is in progress.
    const applyInitialState = (nextData) => {
      state.step = Math.max(0, Math.min(5, Number(nextData.setup?.currentStep || 0)));
      acceptState(nextData, { hydrate: true });
      renderStep();
    };

    applyInitialState(data);

    const env = data.env || {};
    const missingSecretKeys = data.setup?.required ? SECRET_DEFINITIONS
      .filter((definition) => {
        if (definition.bundled && String(env[definition.bundled]) !== 'true') return false;
        const value = String(env[definition.key] || '');
        return !value || value === 'CHANGE_ME';
      })
      .map((definition) => definition.key) : [];

    if (missingSecretKeys.length) {
      const generatedState = await window.omlorixServer.regenerateSecrets(missingSecretKeys);
      applyInitialState(generatedState);
    }
  }).catch((error) => {
    // Fail closed: if setup state cannot be read, continue presenting setup
    // rather than revealing a dashboard that could bypass first-run gating.
    refs.overlay.hidden = false;
    refs.readiness.textContent = translateLauncherMessage(error.message || String(error));
  });
})();
