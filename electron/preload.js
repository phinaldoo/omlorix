const { contextBridge, ipcRenderer } = require('electron');

/** Recreate safe structured main-process failures as ordinary renderer errors. */
async function invokeCodeExecutionEditor(channel, ...args) {
  const result = await ipcRenderer.invoke(channel, ...args);
  if (result?.ok) return result.value;
  const error = new Error(String(result?.error?.message || 'Could not save the Code Execution service'));
  error.code = String(result?.error?.code || 'UNKNOWN');
  throw error;
}

/** Preserve the manager's translatable log error instead of Electron's IPC wrapper. */
async function invokeServerLog(channel, ...args) {
  try {
    return await ipcRenderer.invoke(channel, ...args);
  } catch (error) {
    const message = String(error?.message || error || '')
      .replace(/^Error invoking remote method '[^']+':\s*/i, '')
      .replace(/^Error:\s*/i, '');
    throw new Error(message);
  }
}

/** Map safe main-process backup codes to stable renderer translation keys. */
async function invokeBackupDownload(jobId, options) {
  const result = await ipcRenderer.invoke('server:download-backup', { jobId, ...(options || {}) });
  if (result?.ok) return result.value;
  const code = String(result?.error?.code || 'BACKUP_DOWNLOAD_FAILED');
  const messageKey = {
    BACKUP_DESTINATION_EXISTS: 'launcher_ui_backup_download_destination_exists',
    BACKUP_DESTINATION_UNAVAILABLE: 'launcher_ui_backup_download_destination_unavailable',
    BACKUP_NOT_AVAILABLE: 'launcher_ui_backup_download_not_available',
    BACKUP_DOWNLOAD_FAILED: 'launcher_ui_backup_download_failed',
  }[code] || 'launcher_ui_backup_download_failed';
  const error = new Error(messageKey);
  error.code = code;
  throw error;
}

contextBridge.exposeInMainWorld('omlorixServer', {
  platform: process.platform,
  getState: () => ipcRenderer.invoke('server:get-state'),
  getServiceStatus: () => ipcRenderer.invoke('server:get-service-status'),
  getAvailableVersions: (channel, options) => (
    ipcRenderer.invoke('server:get-available-versions', channel, options)
  ),
  saveSettings: (payload) => ipcRenderer.invoke('server:save-settings', payload),
  saveSetupProgress: (payload) => ipcRenderer.invoke('server:save-setup-progress', payload),
  chooseSecretsExport: () => ipcRenderer.invoke('server:choose-secrets-export'),
  saveEnvBackupNow: () => ipcRenderer.invoke('server:save-env-backup-now'),
  disableEnvBackup: () => ipcRenderer.invoke('server:disable-env-backup'),
  chooseSecretsImport: () => ipcRenderer.invoke('server:choose-secrets-import'),
  regenerateSecrets: (keys) => ipcRenderer.invoke('server:regenerate-secrets', keys),
  getEnvEditor: () => ipcRenderer.invoke('server:get-env-editor'),
  chooseEnvImport: () => ipcRenderer.invoke('server:choose-env-import'),
  chooseEnvExport: () => ipcRenderer.invoke('server:choose-env-export'),
  applyEnvImport: (importId, options) => ipcRenderer.invoke('server:apply-env-import', importId, options),
  discardEnvImport: (importId) => ipcRenderer.invoke('server:discard-env-import', importId),
  saveEnvEditor: (payload) => ipcRenderer.invoke('server:save-env-editor', payload),
  setupEnvironment: () => ipcRenderer.invoke('server:setup-environment'),
  start: () => ipcRenderer.invoke('server:start'),
  stop: () => ipcRenderer.invoke('server:stop'),
  restart: () => ipcRenderer.invoke('server:restart'),
  saveProxySettings: (payload) => ipcRenderer.invoke('server:save-proxy-settings', payload),
  chooseProxyTlsFile: (kind, currentPath) => ipcRenderer.invoke('server:choose-proxy-tls-file', kind, currentPath),
  startProxy: () => ipcRenderer.invoke('server:start-proxy'),
  stopProxy: () => ipcRenderer.invoke('server:stop-proxy'),
  restartProxy: () => ipcRenderer.invoke('server:restart-proxy'),
  installProxyService: () => ipcRenderer.invoke('server:install-proxy-service'),
  uninstallProxyService: () => ipcRenderer.invoke('server:uninstall-proxy-service'),
  repairVisitorIps: () => ipcRenderer.invoke('server:repair-visitor-ips'),
  openDockerSetup: () => ipcRenderer.invoke('server:open-docker-setup'),
  startDockerDesktop: () => ipcRenderer.invoke('server:start-docker-desktop'),
  checkServerUpdate: () => ipcRenderer.invoke('server:check-update'),
  update: (options) => ipcRenderer.invoke('server:update', options),
  getLauncherUpdateInfo: (options) => ipcRenderer.invoke('launcher:get-update-info', options),
  showLauncherUpdate: () => ipcRenderer.invoke('launcher:show-update-window'),
  getScheduledUpdates: () => ipcRenderer.invoke('scheduled-updates:get'),
  saveScheduledUpdates: (payload) => ipcRenderer.invoke('scheduled-updates:save', payload),
  runScheduledUpdateNow: () => ipcRenderer.invoke('scheduled-updates:run-now'),
  getWindowMode: () => ipcRenderer.invoke('launcher:get-window-mode'),
  setWindowBackground: (mode) => ipcRenderer.invoke('launcher:set-background-color', mode),
  getBackupOptions: () => ipcRenderer.invoke('server:get-backup-options'),
  getBackupJobs: () => ipcRenderer.invoke('server:get-backup-jobs'),
  backup: (options) => ipcRenderer.invoke('server:backup', options),
  downloadBackup: (jobId, options) => invokeBackupDownload(jobId, options),
  probeStorage: () => ipcRenderer.invoke('server:storage-probe'),
  migrateStorage: (options) => ipcRenderer.invoke('server:storage-migrate', options),
  chooseRestoreBackup: (options) => ipcRenderer.invoke('server:choose-restore-backup', options),
  restore: (source) => ipcRenderer.invoke('server:restore', source),
  verifyBackup: (source) => ipcRenderer.invoke('server:verify-backup', source),
  serviceAction: (action, serviceName, options) => (
    ipcRenderer.invoke('server:service-action', action, serviceName, options)
  ),
  logs: (options) => invokeServerLog('server:logs', options),
  startLogFollow: (options) => invokeServerLog('server:logs-follow-start', options),
  stopLogFollow: (sessionId) => invokeServerLog('server:logs-follow-stop', sessionId),
  openUrl: () => ipcRenderer.invoke('server:open-url'),
  revealHome: () => ipcRenderer.invoke('server:reveal-home'),
  codeExecution: {
    list: () => ipcRenderer.invoke('code-execution:list'),
    getAvailableVersions: () => ipcRenderer.invoke('code-execution:get-available-versions'),
    get: (instanceId) => ipcRenderer.invoke('code-execution:get', instanceId),
    create: (payload) => invokeCodeExecutionEditor('code-execution:create', payload),
    save: (instanceId, payload) => invokeCodeExecutionEditor('code-execution:save', instanceId, payload),
    start: (instanceId) => ipcRenderer.invoke('code-execution:start', instanceId),
    stop: (instanceId) => ipcRenderer.invoke('code-execution:stop', instanceId),
    restart: (instanceId) => ipcRenderer.invoke('code-execution:restart', instanceId),
    checkUpdate: (instanceId) => ipcRenderer.invoke('code-execution:check-update', instanceId),
    update: (instanceId) => ipcRenderer.invoke('code-execution:update', instanceId),
    logs: (instanceId, lines) => ipcRenderer.invoke('code-execution:logs', instanceId, lines),
    connectionDetails: (instanceId) => ipcRenderer.invoke('code-execution:connection-details', instanceId),
    copyConnection: (instanceId) => ipcRenderer.invoke('code-execution:copy-connection', instanceId),
    openOmlorixConnections: () => ipcRenderer.invoke('code-execution:open-omlorix-connections'),
    reveal: (instanceId) => ipcRenderer.invoke('code-execution:reveal', instanceId),
    remove: (instanceId) => ipcRenderer.invoke('code-execution:remove', instanceId),
    onOperationStart: (callback) => {
      const listener = (event, payload) => callback(payload);
      ipcRenderer.on('code-execution:operation-start', listener);
      return () => ipcRenderer.removeListener('code-execution:operation-start', listener);
    },
    onOperationOutput: (callback) => {
      const listener = (event, payload) => callback(payload);
      ipcRenderer.on('code-execution:operation-output', listener);
      return () => ipcRenderer.removeListener('code-execution:operation-output', listener);
    },
    onOperationEnd: (callback) => {
      const listener = (event, payload) => callback(payload);
      ipcRenderer.on('code-execution:operation-end', listener);
      return () => ipcRenderer.removeListener('code-execution:operation-end', listener);
    },
  },
  onOperationStart: (callback) => {
    const listener = (event, payload) => callback(payload);
    ipcRenderer.on('server:operation-start', listener);
    return () => ipcRenderer.removeListener('server:operation-start', listener);
  },
  onOperationOutput: (callback) => {
    const listener = (event, payload) => callback(payload);
    ipcRenderer.on('server:operation-output', listener);
    return () => ipcRenderer.removeListener('server:operation-output', listener);
  },
  onOperationEnd: (callback) => {
    const listener = (event, payload) => callback(payload);
    ipcRenderer.on('server:operation-end', listener);
    return () => ipcRenderer.removeListener('server:operation-end', listener);
  },
  onLogFollowOutput: (callback) => {
    const listener = (event, payload) => callback(payload);
    ipcRenderer.on('server:log-follow-output', listener);
    return () => ipcRenderer.removeListener('server:log-follow-output', listener);
  },
  onLogFollowEnd: (callback) => {
    const listener = (event, payload) => callback(payload);
    ipcRenderer.on('server:log-follow-end', listener);
    return () => ipcRenderer.removeListener('server:log-follow-end', listener);
  },
  onRefreshRequested: (callback) => {
    const listener = () => callback();
    ipcRenderer.on('server:refresh-requested', listener);
    return () => ipcRenderer.removeListener('server:refresh-requested', listener);
  },
  onScheduledUpdatesChanged: (callback) => {
    const listener = (event, payload) => callback(payload);
    ipcRenderer.on('scheduled-updates:changed', listener);
    return () => ipcRenderer.removeListener('scheduled-updates:changed', listener);
  },
  onWindowModeChanged: (callback) => {
    const listener = (event, payload) => callback(payload);
    ipcRenderer.on('launcher:window-mode-changed', listener);
    return () => ipcRenderer.removeListener('launcher:window-mode-changed', listener);
  },
});

function tagDocument() {
  const root = document.documentElement;
  if (!root) return;
  root.classList.add('omlorix-server-launcher');
  root.dataset.platform = process.platform;
  root.dataset.windowMode = 'window';
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', tagDocument, { once: true });
} else {
  tagDocument();
}
