const {
  app,
  BrowserWindow,
  Menu,
  Tray,
  clipboard,
  dialog,
  ipcMain,
  nativeTheme,
  shell,
} = require('electron');
const path = require('path');
const { ServerManager } = require('./server-manager');
const { CodeExecutionManager } = require('./code-execution-manager');
const { ScheduledUpdateManager } = require('./scheduled-updates');
const { getTrustedLauncherUrl, isTrustedLauncherUrl, isTrustedRendererUrl } = require('./launcher-security');
const { createLauncherAutoUpdateService } = require('./launcher-auto-updater');
const { runLauncherUpdateFlow } = require('./launcher-update-flow');
const { compareVersions } = require('./version-utils');
const { createNativeUpdateProgressController } = require('./native-update-progress');
const {
  createEnvExportDialogOptions,
  createEnvImportDialogOptions,
  createSecretsImportDialogOptions,
} = require('./env-import-dialog');
const { createEditMenuTemplate } = require('./edit-menu');
const { createLauncherTranslator } = require('./launcher-native-i18n');
const { createLocalizedRoleMenuItem } = require('./native-menu');
const { createViewMenuTemplate } = require('./view-menu');
const {
  APP_NAME,
  createAboutPanelOptions,
  createMacApplicationMenuTemplate,
  desktopBuildVersion,
} = require('./app-menu');

let mainWindow = null;
let serverManager = null;
let codeExecutionManager = null;
let scheduledUpdateManager = null;
let launcherAutoUpdater = null;
let launcherUpdateFlowPromise = null;
let launcherInstallWatchdog = null;
let launcherUpdateProgressController = null;
let launcherTray = null;
let confirmedProxyQuit = false;
let proxyQuitPromptOpen = false;
let hasSingleInstanceLock = true;
const LAUNCHER_UPDATE_CACHE_MAX_AGE_MS = 4 * 60 * 60 * 1000;
const RELEASE_CHECK_FAILURE_COOLDOWN_MS = 60 * 1000;
const passiveReleaseFailureLogs = new Map();
const trustedLauncherUrl = getTrustedLauncherUrl(__dirname);
const trustedRendererUrls = [
  trustedLauncherUrl,
];
const htmlFullscreenWindows = new WeakMap();
const LAUNCHER_BACKGROUND_COLORS = Object.freeze({
  light: '#f4f4f2',
  dark: '#09090b',
});

/**
 * Return the native compositor color that matches the renderer canvas.
 *
 * Electron exposes this surface briefly while a window is resized faster than
 * Chromium can repaint it. Keeping the values aligned with launcher.css avoids
 * a contrasting flash around the newly exposed edge.
 */
function launcherBackgroundColor(mode) {
  return mode === 'dark'
    ? LAUNCHER_BACKGROUND_COLORS.dark
    : LAUNCHER_BACKGROUND_COLORS.light;
}

/** Translate native launcher UI with the same OS locale used by Chromium. */
function launcherT(key, variables = {}) {
  const locale = app.isReady() && typeof app.getLocale === 'function' ? app.getLocale() : 'en';
  return createLauncherTranslator(locale)(key, variables);
}

function appRoot() {
  return app.getAppPath();
}

function revealWindow(window) {
  if (!window || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  if (!window.isVisible()) window.show();
  app.focus({ steal: true });
  window.focus();
}

function getLauncherWindowMode(window) {
  const fullscreen = Boolean(
    window
      && !window.isDestroyed()
      && (
        window.isFullScreen()
        || window.isSimpleFullScreen?.()
        || htmlFullscreenWindows.get(window)
      ),
  );

  return {
    fullscreen,
    mode: fullscreen ? 'fullscreen' : 'window',
  };
}

function sendLauncherWindowMode(window) {
  if (!window || window.isDestroyed()) return;
  window.webContents.send('launcher:window-mode-changed', getLauncherWindowMode(window));
}

function createMainWindow() {
  const window = new BrowserWindow({
    width: 1180,
    height: 820,
    // The renderer has dedicated compact layouts below 900px and 560px. Let
    // operators reach those layouts while keeping enough width for setup
    // forms and launcher actions to remain comfortably usable.
    minWidth: 520,
    minHeight: 700,
    // Start with the OS appearance before the renderer can report a persisted
    // launcher override. The preload bridge synchronizes the exact mode once
    // launcher-init.js has read localStorage.
    backgroundColor: launcherBackgroundColor(nativeTheme.shouldUseDarkColors ? 'dark' : 'light'),
    title: 'Omlorix Server Launcher',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  window.once('ready-to-show', () => revealWindow(window));
  window.on('closed', () => {
    htmlFullscreenWindows.delete(window);
    void serverManager?.stopLogFollow().catch(() => {});
    if (mainWindow === window) mainWindow = null;
  });
  window.on('enter-full-screen', () => sendLauncherWindowMode(window));
  window.on('leave-full-screen', () => sendLauncherWindowMode(window));
  window.on('enter-html-full-screen', () => {
    htmlFullscreenWindows.set(window, true);
    sendLauncherWindowMode(window);
  });
  window.on('leave-html-full-screen', () => {
    htmlFullscreenWindows.set(window, false);
    sendLauncherWindowMode(window);
  });
  const blockUntrustedNavigation = (event, url) => {
    if (isTrustedLauncherUrl(url, trustedLauncherUrl)) {
      return;
    }

    event.preventDefault();
    if (url && /^https?:/i.test(url)) {
      shell.openExternal(url).catch(() => {});
    }
  };
  window.webContents.on('will-navigate', blockUntrustedNavigation);
  window.webContents.on('will-redirect', blockUntrustedNavigation);
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url && /^https?:/i.test(url)) {
      shell.openExternal(url).catch(() => {});
    }
    return { action: 'deny' };
  });

  return window;
}

async function loadLauncher() {
  if (!mainWindow) {
    mainWindow = createMainWindow();
  }
  await mainWindow.loadFile(path.join(__dirname, 'renderer', 'launcher.html'));
}

function ensureLauncherTray() {
  if (launcherTray || !serverManager?.proxy?.status().running) return;
  launcherTray = new Tray(path.join(__dirname, 'assets', 'launcher-icon.png'));
  launcherTray.setToolTip(app.getName());
  launcherTray.setContextMenu(Menu.buildFromTemplate([
    {
      label: launcherT('open_omlorix'),
      click: () => {
        if (!mainWindow) void loadLauncher();
        else revealWindow(mainWindow);
      },
    },
    { type: 'separator' },
    {
      label: launcherT('quit_launcher'),
      click: () => app.quit(),
    },
  ]));
  launcherTray.on('double-click', () => {
    if (!mainWindow) void loadLauncher();
    else revealWindow(mainWindow);
  });
}

function syncLauncherTray() {
  if (serverManager?.proxy?.status().running) {
    ensureLauncherTray();
  } else if (launcherTray) {
    launcherTray.destroy();
    launcherTray = null;
  }
}

async function confirmProxyQuit() {
  const result = await showNativeMessageBox({
    type: 'warning',
    title: launcherT('quit_stops_proxy_title'),
    message: launcherT('quit_stops_proxy_title'),
    detail: launcherT('quit_stops_proxy_detail'),
    buttons: [launcherT('cancel'), launcherT('quit_launcher')],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
  });
  return result.response === 1;
}

function assertTrustedIpcSender(event) {
  const senderUrl = event?.senderFrame?.url;
  if (isTrustedRendererUrl(senderUrl, trustedRendererUrls)) {
    return;
  }

  throw new Error('Unauthorized renderer origin.');
}

function handleTrustedIpc(channel, handler) {
  ipcMain.handle(channel, async (event, ...args) => {
    assertTrustedIpcSender(event);
    return handler(event, ...args);
  });
}

/** Keep passive release outages concise and free of URLs, tokens, and stacks. */
function logPassiveReleaseFailure(resourceKey, channel, error) {
  const key = `${resourceKey}:${channel}`;
  const now = Date.now();
  const lastLoggedAt = passiveReleaseFailureLogs.get(key);
  if (
    lastLoggedAt !== undefined
    && now - lastLoggedAt < RELEASE_CHECK_FAILURE_COOLDOWN_MS
  ) return;
  passiveReleaseFailureLogs.set(key, now);

  const statusMatch = String(error?.message || '').match(/\bHTTP\s+(\d{3})\b/i);
  const status = Number(error?.statusCode || error?.status || statusMatch?.[1] || 0);
  const code = String(error?.code || '').trim();
  const reason = status >= 400 && status <= 599
    ? `HTTP ${status}`
    : /^(?:E[A-Z0-9_]+|ERR_[A-Z0-9_]+)$/.test(code)
      ? code
      : 'network error';
  console.warn(`[Launcher] ${launcherT('passive_release_check_unavailable', {
    resource: launcherT(resourceKey),
    channel: launcherT(channel),
    reason,
    seconds: 60,
  })}`);
}

function sendToRenderer(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

function nativeDialogParent() {
  return mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined;
}

function showNativeMessageBox(options) {
  const parent = nativeDialogParent();
  if (parent) {
    return dialog.showMessageBox(parent, options);
  }
  return dialog.showMessageBox(options);
}

/** Confirm and retry a lifecycle action that encounters legacy resources. */
async function runWithLegacyComposeAdoption(action) {
  try {
    return await action();
  } catch (error) {
    if (error?.code !== 'LEGACY_COMPOSE_ADOPTION_REQUIRED') throw error;
    const project = String(error.project || '').trim();
    const result = await showNativeMessageBox({
      type: 'warning',
      title: launcherT('legacy_compose_adoption_title'),
      message: launcherT('legacy_compose_adoption_title'),
      detail: launcherT('legacy_compose_adoption_detail', { project }),
      buttons: [launcherT('cancel'), launcherT('legacy_compose_adoption_action')],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    });
    if (result.response !== 1) throw error;

    // Re-check every container after confirmation so a project that changed
    // while the dialog was open can never be adopted accidentally.
    await serverManager.adoptLegacyComposeProject(project);
    return action();
  }
}

async function ensureLauncherInstallReady() {
  if (
    process.platform !== 'darwin'
    || !app.isPackaged
    || typeof app.isInApplicationsFolder !== 'function'
    || app.isInApplicationsFolder()
  ) {
    return { ok: true };
  }

  const prompt = await showNativeMessageBox({
    type: 'info',
    title: launcherT('software_update'),
    message: launcherT('move_before_update'),
    detail: launcherT('move_before_update_detail'),
    buttons: [launcherT('move_to_applications'), launcherT('cancel')],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  });
  if (prompt.response !== 0) {
    return { ok: false, status: 'install-location-required' };
  }

  if (typeof app.moveToApplicationsFolder !== 'function') {
    return { ok: false, status: 'install-location-required' };
  }

  const moved = app.moveToApplicationsFolder();
  return {
    ok: false,
    status: moved ? 'moving-to-applications' : 'install-location-required',
  };
}

function setLauncherUpdateProgress(progress) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.setProgressBar(progress);
}

/** Escape translated copy before embedding it in the updater's data URL. */
function escapeLauncherHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function launcherUpdateProgressHtml() {
  const localizedJson = JSON.stringify({
    updating: launcherT('updating'),
    progressOf: launcherT('progress_of'),
    progressOfSpeed: launcherT('progress_of_speed'),
  }).replaceAll('<', '\\u003c');
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline';">
  <title>${escapeLauncherHtml(launcherT('updating_launcher'))}</title>
  <style>
    :root {
      color-scheme: light dark;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: transparent;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: var(--window-bg, #f5f5f5);
      color: var(--text, #1f1f1f);
    }
    .panel {
      width: 100%;
      height: 100vh;
      display: grid;
      grid-template-columns: 96px 1fr;
      gap: 20px;
      align-items: center;
      padding: 28px 32px;
    }
    .icon {
      width: 72px;
      height: 72px;
      border-radius: 18px;
      display: grid;
      place-items: center;
      color: white;
      font-weight: 800;
      letter-spacing: .02em;
      background: linear-gradient(180deg, #2f7df6, #123ab8);
      box-shadow: inset 0 0 0 3px rgba(255,255,255,.75), 0 8px 24px rgba(0,0,0,.22);
    }
    .content { min-width: 0; }
    h1 {
      margin: 0 0 18px;
      font-size: 22px;
      line-height: 1.2;
      font-weight: 700;
    }
    progress {
      width: 100%;
      height: 12px;
      margin-bottom: 18px;
      accent-color: #147efb;
    }
    .row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
    }
    .detail {
      min-height: 28px;
      color: var(--muted, #5f6368);
      font-size: 18px;
      font-weight: 600;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .buttons {
      display: flex;
      gap: 10px;
      flex: 0 0 auto;
    }
    button {
      min-width: 126px;
      border: 0;
      border-radius: 9px;
      padding: 9px 16px;
      font: inherit;
      font-size: 16px;
      font-weight: 650;
      color: var(--button-text, #1f1f1f);
      background: var(--button-bg, #dedede);
    }
    button.primary-ready {
      color: white;
      background: #147efb;
    }
    button:disabled {
      opacity: .55;
    }
    button[hidden] { display: none; }
    @media (prefers-color-scheme: dark) {
      body {
        --window-bg: #2f2f2f;
        --text: #f2f2f2;
        --muted: #d3d3d3;
        --button-bg: #4a4a4a;
        --button-text: #f2f2f2;
      }
    }
  </style>
</head>
<body>
  <main class="panel">
    <div class="icon" aria-hidden="true">&gt;_</div>
    <section class="content" aria-live="polite">
      <h1 id="message">${escapeLauncherHtml(launcherT('preparing_update'))}</h1>
      <progress id="progress" max="100" value="0"></progress>
      <div class="row">
        <div class="detail" id="detail">${escapeLauncherHtml(launcherT('starting_download'))}</div>
        <div class="buttons">
          <button id="secondary" type="button" hidden>${escapeLauncherHtml(launcherT('later'))}</button>
          <button id="primary" type="button">${escapeLauncherHtml(launcherT('cancel'))}</button>
        </div>
      </div>
    </section>
  </main>
  <script>
    const localized = ${localizedJson};
    const message = document.getElementById('message');
    const detail = document.getElementById('detail');
    const progress = document.getElementById('progress');
    const primary = document.getElementById('primary');
    const secondary = document.getElementById('secondary');
    let currentPhase = 'downloading';

    function formatBytes(value) {
      const bytes = Number(value || 0);
      if (!Number.isFinite(bytes) || bytes <= 0) return '';
      const units = ['B', 'KB', 'MB', 'GB'];
      let next = bytes;
      let unit = 0;
      while (next >= 1024 && unit < units.length - 1) {
        next /= 1024;
        unit += 1;
      }
      return unit === 0 ? Math.round(next) + ' ' + units[unit] : next.toFixed(1) + ' ' + units[unit];
    }

    function progressDetail(state) {
      if (state.transferred && state.total) {
        const values = {
          transferred: formatBytes(state.transferred),
          total: formatBytes(state.total),
          speed: formatBytes(state.bytesPerSecond),
        };
        const template = state.bytesPerSecond ? localized.progressOfSpeed : localized.progressOf;
        return template.replace(/\\{(\\w+)\\}/g, (match, name) => values[name] || match);
      }
      return state.detail || '';
    }

    function applyState(state) {
      currentPhase = state.phase || currentPhase;
      message.textContent = state.message || localized.updating;
      detail.textContent = currentPhase === 'downloading' ? progressDetail(state) : (state.detail || '');
      progress.value = Math.max(0, Math.min(100, Number(state.percent || 0)));
      primary.hidden = !state.primaryLabel;
      primary.textContent = state.primaryLabel || '';
      primary.classList.toggle('primary-ready', currentPhase === 'ready');
      secondary.hidden = !state.secondaryLabel;
      secondary.textContent = state.secondaryLabel || '';
    }

    primary.addEventListener('click', () => {
      window.launcherUpdateProgress.sendAction(currentPhase === 'downloading' ? 'cancel' : 'primary');
    });
    secondary.addEventListener('click', () => {
      window.launcherUpdateProgress.sendAction('secondary');
    });
    window.launcherUpdateProgress.onState(applyState);
  </script>
</body>
</html>`;
}

/**
 * Create the HTML progress window used on Windows and Linux. macOS uses the
 * AppKit helper below so none of the visible updater controls are web-rendered.
 */
function createWebLauncherUpdateProgressWindow(updateInfo = {}) {
  if (launcherUpdateProgressController) {
    launcherUpdateProgressController.close();
  }

  const channel = `launcher-update-progress-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const actionWaiters = [];
  let latestState = {
    phase: 'downloading',
    message: launcherT('preparing_update'),
    detail: `Omlorix Server Launcher ${updateInfo.latestVersion || ''}`.trim(),
    percent: 0,
    primaryLabel: launcherT('cancel'),
  };

  const window = new BrowserWindow({
    width: 620,
    height: 230,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    title: launcherT('updating_launcher'),
    parent: mainWindow || undefined,
    modal: Boolean(mainWindow),
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'update-progress-preload.js'),
      // Chromium normalizes command-line switch names to lowercase on Windows.
      // Keep this argument lowercase so the sandboxed preload can find the IPC
      // channel in process.argv and connect the progress window to main.
      additionalArguments: [`--launcher-update-progress-channel=${channel}`],
    },
  });

  const sendState = () => {
    if (!window.isDestroyed()) {
      window.webContents.send(`${channel}:state`, latestState);
    }
  };
  const resolveAction = (action) => {
    const waiter = actionWaiters.shift();
    if (waiter) waiter(action);
  };
  const actionListener = (event, action) => {
    resolveAction(String(action || ''));
  };

  ipcMain.on(`${channel}:action`, actionListener);
  window.webContents.once('did-finish-load', sendState);
  window.once('ready-to-show', () => window.show());
  window.on('closed', () => {
    ipcMain.removeListener(`${channel}:action`, actionListener);
    resolveAction('cancel');
    if (launcherUpdateProgressController?.window === window) {
      launcherUpdateProgressController = null;
    }
  });
  window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(launcherUpdateProgressHtml())}`).catch(() => {});

  launcherUpdateProgressController = {
    window,
    setState(state = {}) {
      latestState = { ...latestState, ...state };
      sendState();
    },
    updateProgress(payload = {}) {
      const percent = Math.max(0, Math.min(100, Number(payload.percent || 0)));
      this.setState({
        phase: 'downloading',
        percent,
        transferred: Number(payload.transferred || 0),
        total: Number(payload.total || 0),
        bytesPerSecond: Number(payload.bytesPerSecond || 0),
      });
    },
    waitForAction() {
      while (actionWaiters.length) {
        const staleWaiter = actionWaiters.shift();
        if (staleWaiter) staleWaiter('stale');
      }
      return new Promise((resolve) => actionWaiters.push(resolve));
    },
    close() {
      ipcMain.removeListener(`${channel}:action`, actionListener);
      if (!window.isDestroyed()) {
        window.close();
      }
      if (launcherUpdateProgressController?.window === window) {
        launcherUpdateProgressController = null;
      }
    },
  };

  return launcherUpdateProgressController;
}

/**
 * Create the platform update presentation. The macOS path is a separately
 * compiled AppKit process using native controls; other platforms retain the
 * established Electron window until their own native updater UI is available.
 */
function createLauncherUpdateProgressWindow(updateInfo = {}) {
  if (process.platform !== 'darwin') {
    return createWebLauncherUpdateProgressWindow(updateInfo);
  }

  if (launcherUpdateProgressController) {
    launcherUpdateProgressController.close();
  }

  const nativeController = createNativeUpdateProgressController({
    app,
    progressText: {
      progressOf: launcherT('progress_of'),
      progressOfSpeed: launcherT('progress_of_speed'),
    },
  });
  const controller = {
    ...nativeController,
    close() {
      nativeController.close();
      if (launcherUpdateProgressController === controller) {
        launcherUpdateProgressController = null;
      }
    },
  };
  launcherUpdateProgressController = controller;
  const isChecking = updateInfo.phase === 'checking';
  controller.setState({
    phase: isChecking ? 'checking' : 'downloading',
    windowTitle: isChecking ? launcherT('software_update') : launcherT('updating_launcher'),
    message: isChecking ? launcherT('checking_for_updates') : launcherT('preparing_update'),
    detail: isChecking
      ? 'Omlorix Server Launcher'
      : `Omlorix Server Launcher ${updateInfo.latestVersion || ''}`.trim(),
    percent: isChecking ? null : 0,
    primaryLabel: launcherT('cancel'),
  });
  return controller;
}

function clearLauncherInstallWatchdog() {
  if (!launcherInstallWatchdog) return;
  clearTimeout(launcherInstallWatchdog);
  launcherInstallWatchdog = null;
}

function scheduleLauncherInstallWatchdog() {
  clearLauncherInstallWatchdog();
  launcherInstallWatchdog = setTimeout(() => {
    launcherInstallWatchdog = null;
    showNativeMessageBox({
      type: 'warning',
      title: launcherT('software_update'),
      message: launcherT('update_install_incomplete'),
      detail: launcherT('update_handoff_failed'),
      buttons: [launcherT('ok')],
      defaultId: 0,
      noLink: true,
    }).catch(() => {});
  }, 60000);
}

async function runNativeLauncherUpdateCheck() {
  if (launcherUpdateFlowPromise) {
    return launcherUpdateFlowPromise;
  }

  launcherUpdateFlowPromise = (async () => {
    try {
      const result = await runLauncherUpdateFlow({
        launcherAutoUpdater,
        showMessageBox: showNativeMessageBox,
        setProgressBar: setLauncherUpdateProgress,
        createProgressWindow: createLauncherUpdateProgressWindow,
        ensureInstallReady: ensureLauncherInstallReady,
        showCheckingProgress: process.platform === 'darwin',
        translate: launcherT,
      });
      if (result?.status === 'installing') {
        scheduleLauncherInstallWatchdog();
      } else {
        clearLauncherInstallWatchdog();
      }
      return result;
    } finally {
      launcherUpdateFlowPromise = null;
    }
  })();

  return launcherUpdateFlowPromise;
}

function requestLauncherUpdateCheck() {
  runNativeLauncherUpdateCheck().catch(() => {});
}

/**
 * Return the safe, display-oriented subset of launcher release information.
 *
 * The minimum version comes from the already-resolved Omlorix release manifest.
 * Comparing it in the main process keeps version ordering consistent with the
 * compatibility enforcement used by ServerManager.
 */
async function getPassiveLauncherUpdateInfo(options = {}) {
  const channel = options?.channel === 'beta' ? 'beta' : 'stable';
  const minimumLauncherVersion = String(options?.minimumLauncherVersion || '')
    .trim()
    .slice(0, 64);
  const force = options?.force === true;
  let result;
  try {
    result = await launcherAutoUpdater.check(channel, {
      maxAgeMs: force ? 0 : LAUNCHER_UPDATE_CACHE_MAX_AGE_MS,
      failureMaxAgeMs: force ? 0 : RELEASE_CHECK_FAILURE_COOLDOWN_MS,
    });
  } catch (error) {
    logPassiveReleaseFailure('launcher_update_metadata', channel, error);
    return {
      status: 'unavailable',
      unavailable: true,
      channel,
      currentVersion: String(app?.getVersion?.() || '').trim(),
      latestVersion: '',
      updateAvailable: false,
      releaseName: '',
      releaseDate: '',
      minimumLauncherVersion,
      availableVersionMeetsMinimum: null,
    };
  }
  const latestVersion = String(result?.latestVersion || '').trim();

  return {
    status: String(result?.status || ''),
    channel: result?.channel === 'beta' ? 'beta' : 'stable',
    currentVersion: String(result?.currentVersion || '').trim(),
    latestVersion,
    updateAvailable: Boolean(result?.updateAvailable),
    releaseName: String(result?.releaseName || '').trim(),
    releaseDate: String(result?.releaseDate || '').trim(),
    minimumLauncherVersion,
    availableVersionMeetsMinimum: minimumLauncherVersion && latestVersion
      ? compareVersions(latestVersion, minimumLauncherVersion) >= 0
      : null,
  };
}

/** Return a structured passive failure so Electron does not print handler stacks. */
async function getPassiveAvailableVersions(channelInput, options = {}) {
  const channel = channelInput === 'beta' ? 'beta' : 'stable';
  try {
    return await serverManager.getAvailableVersions(channel, {
      force: options?.force === true,
    });
  } catch (error) {
    logPassiveReleaseFailure('server_release_metadata', channel, error);
    return {
      channel,
      versions: [],
      unavailable: true,
    };
  }
}

function createLauncherUpdateMenuItem() {
  return {
    label: launcherT('check_for_updates'),
    click: requestLauncherUpdateCheck,
  };
}

function configureNativeAboutPanel() {
  app.setAboutPanelOptions(createAboutPanelOptions({
    appName: app.getName() || APP_NAME,
    appVersion: app.getVersion(),
    buildVersion: desktopBuildVersion(process.env, app.getVersion()),
  }));
}

function buildLauncherMenuTemplate() {
  const submenu = [
    {
      label: launcherT('refresh_status'),
      accelerator: 'CmdOrCtrl+R',
      click: () => sendToRenderer('server:refresh-requested', {}),
    },
    {
      label: launcherT('open_omlorix'),
      accelerator: 'CmdOrCtrl+O',
      click: async () => {
        try {
          await serverManager.openUrl(shell);
        } catch (_error) {
          dialog.showErrorBox(APP_NAME, launcherT('open_omlorix_failed'));
        }
      },
    },
    {
      label: launcherT('show_server_files'),
      click: async () => {
        try {
          await serverManager.revealServerHome(shell);
        } catch (_error) {
          dialog.showErrorBox(APP_NAME, launcherT('show_server_files_failed'));
        }
      },
    },
  ];

  if (process.platform !== 'darwin') {
    submenu.splice(1, 0, createLauncherUpdateMenuItem());
    submenu.push(
      { type: 'separator' },
      createLocalizedRoleMenuItem('quit', 'menu_quit_app', launcherT, {
        appName: app.getName() || APP_NAME,
      }),
    );
  }

  return {
    label: process.platform === 'darwin' ? launcherT('server') : APP_NAME,
    submenu,
  };
}

function buildMenu() {
  const template = [
    ...(process.platform === 'darwin'
      ? [createMacApplicationMenuTemplate(
        app.getName() || APP_NAME,
        [createLauncherUpdateMenuItem()],
        launcherT,
      )]
      : []),
    buildLauncherMenuTemplate(),
    createEditMenuTemplate(launcherT),
    createViewMenuTemplate(launcherT),
    {
      label: launcherT('help'),
      submenu: [
        {
          // Leave unavailable guides visible so operators know they are planned,
          // while preventing navigation to the not-yet-launched project website.
          label: `${launcherT('deployment_guide')} — ${launcherT('coming_soon')}`,
          enabled: false,
        },
        {
          label: `${launcherT('update_guide')} — ${launcherT('coming_soon')}`,
          enabled: false,
        },
        {
          label: launcherT('docker_desktop'),
          click: () => serverManager.openDockerSetup(shell).catch(() => {}),
        },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function registerIpc() {
  handleTrustedIpc('launcher:get-window-mode', async () => getLauncherWindowMode(mainWindow));
  handleTrustedIpc('launcher:set-background-color', async (event, mode) => {
    const color = launcherBackgroundColor(mode);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.setBackgroundColor(color);
    }
    return color;
  });
  handleTrustedIpc('server:get-state', async () => serverManager.getState());
  handleTrustedIpc('server:get-service-status', async () => serverManager.stackStatus({
    includeDiagnostics: false,
  }));
  handleTrustedIpc('server:get-available-versions', async (event, channel, options) => (
    getPassiveAvailableVersions(channel, options)
  ));
  handleTrustedIpc('server:save-settings', async (event, payload) => serverManager.saveSettings(payload));
  handleTrustedIpc('server:save-setup-progress', async (event, payload) => serverManager.saveSetupProgress(payload));
  handleTrustedIpc('server:regenerate-secrets', async (event, keys) => serverManager.regenerateSecrets(keys));
  handleTrustedIpc('server:save-env-backup-now', async () => serverManager.saveAutomaticEnvBackup());
  handleTrustedIpc('server:disable-env-backup', async () => serverManager.disableAutomaticEnvBackup());
  handleTrustedIpc('server:choose-secrets-export', async () => {
    const result = await dialog.showSaveDialog(mainWindow, {
      title: launcherT('export_env'),
      defaultPath: 'omlorix.env',
      buttonLabel: launcherT('export'),
      filters: [
        { name: launcherT('environment_files'), extensions: ['env'] },
        { name: launcherT('all_files'), extensions: ['*'] },
      ],
      properties: ['showOverwriteConfirmation', 'createDirectory'],
    });
    if (result.canceled || !result.filePath) return { canceled: true };
    return {
      canceled: false,
      export: await serverManager.exportSecretsBackup(result.filePath),
    };
  });
  handleTrustedIpc('server:choose-secrets-import', async () => {
    // Use the shared permissive picker so macOS does not disable a hidden,
    // extensionless ".env" file while still showing the restore-specific label.
    const result = await dialog.showOpenDialog(
      mainWindow,
      createSecretsImportDialogOptions(launcherT),
    );
    if (result.canceled || !result.filePaths.length) return { canceled: true };
    const imported = await serverManager.importSecretsBackup(result.filePaths[0]);
    syncLauncherTray();
    return {
      canceled: false,
      ...imported,
    };
  });
  handleTrustedIpc('server:get-env-editor', async () => serverManager.getEnvEditor());
  handleTrustedIpc('server:choose-env-import', async () => {
    const result = await dialog.showOpenDialog(mainWindow, createEnvImportDialogOptions(launcherT));
    if (result.canceled || !result.filePaths.length) {
      return { canceled: true };
    }
    return {
      canceled: false,
      preview: await serverManager.previewEnvImport(result.filePaths[0]),
    };
  });
  handleTrustedIpc('server:choose-env-export', async () => {
    const result = await dialog.showSaveDialog(
      mainWindow,
      createEnvExportDialogOptions(launcherT),
    );
    if (result.canceled || !result.filePath) {
      return { canceled: true };
    }
    return {
      canceled: false,
      export: await serverManager.exportEnv(result.filePath),
    };
  });
  handleTrustedIpc('server:apply-env-import', async (event, importId, options) => {
    const imported = await serverManager.applyEnvImport(importId, options);
    syncLauncherTray();
    return imported;
  });
  handleTrustedIpc('server:discard-env-import', async (event, importId) => serverManager.discardEnvImport(importId));
  handleTrustedIpc('server:save-env-editor', async (event, payload) => {
    try {
      return await serverManager.saveEnvEditor(payload);
    } catch (error) {
      if (error.validationErrors) {
        return { ok: false, validationErrors: error.validationErrors };
      }
      throw error;
    }
  });
  handleTrustedIpc('server:setup-environment', async () => serverManager.setupEnvironment());
  handleTrustedIpc('server:start', async () => runWithLegacyComposeAdoption(() => serverManager.start()));
  handleTrustedIpc('server:stop', async () => runWithLegacyComposeAdoption(() => serverManager.stop()));
  handleTrustedIpc('server:restart', async () => runWithLegacyComposeAdoption(() => serverManager.restart()));
  handleTrustedIpc('server:save-proxy-settings', async (event, payload) => {
    try {
      const state = await serverManager.saveProxySettings(payload);
      syncLauncherTray();
      return state;
    } catch (error) {
      if (error.validationErrors) {
        return { ok: false, validationErrors: error.validationErrors };
      }
      throw error;
    }
  });
  handleTrustedIpc('server:choose-proxy-tls-file', async (event, kind, currentPath = '') => {
    const filtersByKind = {
      cert: [
        { name: launcherT('certificate_files'), extensions: ['pem', 'crt', 'cer'] },
        { name: launcherT('all_files'), extensions: ['*'] },
      ],
      key: [
        { name: launcherT('private_key_files'), extensions: ['pem', 'key'] },
        { name: launcherT('all_files'), extensions: ['*'] },
      ],
      ca: [
        { name: launcherT('certificate_chain_files'), extensions: ['pem', 'crt', 'cer'] },
        { name: launcherT('all_files'), extensions: ['*'] },
      ],
    };
    const selectedKind = Object.prototype.hasOwnProperty.call(filtersByKind, kind) ? kind : 'cert';
    const selectedPath = String(currentPath || '').trim();
    const dialogTitle = selectedKind === 'key'
      ? launcherT('choose_tls_private_key')
      : selectedKind === 'ca'
        ? launcherT('choose_tls_ca_chain')
        : launcherT('choose_tls_certificate');
    const result = await dialog.showOpenDialog(mainWindow, {
      title: dialogTitle,
      properties: ['openFile'],
      defaultPath: selectedPath || undefined,
      filters: filtersByKind[selectedKind],
    });
    if (result.canceled || !result.filePaths.length) {
      return { canceled: true, path: '' };
    }
    return { canceled: false, path: result.filePaths[0] };
  });
  handleTrustedIpc('server:start-proxy', async () => {
    const state = await serverManager.startProxy();
    syncLauncherTray();
    return state;
  });
  handleTrustedIpc('server:stop-proxy', async () => {
    const state = await serverManager.stopProxy();
    syncLauncherTray();
    return state;
  });
  handleTrustedIpc('server:restart-proxy', async () => {
    const state = await serverManager.restartProxy();
    syncLauncherTray();
    return state;
  });
  handleTrustedIpc('server:install-proxy-service', async () => {
    const state = await serverManager.installProxyService();
    syncLauncherTray();
    return state;
  });
  handleTrustedIpc('server:uninstall-proxy-service', async () => {
    const state = await serverManager.uninstallProxyService();
    syncLauncherTray();
    return state;
  });
  handleTrustedIpc('server:repair-visitor-ips', async () => serverManager.repairVisitorIps());
  // A dashboard check is informational: return launcher compatibility details
  // with the available server release so the renderer can offer the correct
  // next action. Actual update execution continues to enforce compatibility.
  handleTrustedIpc('server:check-update', async () => serverManager.getServerUpdateInfo('', {
    allowLauncherUpdateRequired: true,
  }));
  handleTrustedIpc('server:open-docker-setup', async () => serverManager.openDockerSetup(shell));
  handleTrustedIpc('server:start-docker-desktop', async () => serverManager.startDockerDesktop(shell));
  handleTrustedIpc('server:update', async (event, options) => {
    try {
      return await runWithLegacyComposeAdoption(() => serverManager.update(options));
    } catch (error) {
      if (error?.code === 'LAUNCHER_UPDATE_REQUIRED') {
        return {
          ok: false,
          type: 'launcherUpdateRequired',
          message: error.message,
          currentLauncherVersion: error.currentLauncherVersion,
          minimumLauncherVersion: error.minimumLauncherVersion,
          targetVersion: error.targetVersion,
          releaseNotes: error.releaseNotes,
        };
      }
      throw error;
    }
  });
  handleTrustedIpc('launcher:show-update-window', async () => {
    return runNativeLauncherUpdateCheck();
  });
  handleTrustedIpc('launcher:get-update-info', async (event, options) => {
    return getPassiveLauncherUpdateInfo(options);
  });
  handleTrustedIpc('scheduled-updates:get', async () => scheduledUpdateManager.snapshot());
  handleTrustedIpc('scheduled-updates:save', async (event, payload) => scheduledUpdateManager.saveSettings(payload));
  handleTrustedIpc('scheduled-updates:run-now', async () => scheduledUpdateManager.runNow());
  handleTrustedIpc('server:get-backup-options', async () => serverManager.getBackupOptions());
  handleTrustedIpc('server:get-backup-jobs', async () => serverManager.getBackupJobs());
  handleTrustedIpc('server:backup', async (event, options) => serverManager.backup(options));
  handleTrustedIpc('server:download-backup', async (event, options = {}) => {
    try {
      const info = await serverManager.getBackupDownloadInfo(options.jobId);
      const result = await dialog.showSaveDialog(mainWindow, {
        title: String(options.title || launcherT('save_backup')),
        defaultPath: path.join(app.getPath('downloads'), info.filename),
        buttonLabel: String(options.buttonLabel || launcherT('save_backup')),
        filters: [
          {
            name: String(options.filterName || launcherT('backup_archives')),
            extensions: info.filename.endsWith('.enc') ? ['enc'] : ['zst'],
          },
          { name: String(options.allFilesName || launcherT('all_files')), extensions: ['*'] },
        ],
        properties: ['createDirectory'],
      });
      if (result.canceled || !result.filePath) {
        return { ok: true, value: { canceled: true } };
      }
      const download = await serverManager.downloadBackup(info.jobId, result.filePath);
      return { ok: true, value: { canceled: false, ...download } };
    } catch (error) {
      const allowedCodes = new Set([
        'BACKUP_DESTINATION_EXISTS',
        'BACKUP_DESTINATION_UNAVAILABLE',
        'BACKUP_NOT_AVAILABLE',
        'BACKUP_DOWNLOAD_FAILED',
      ]);
      const code = allowedCodes.has(error?.code) ? error.code : 'BACKUP_DOWNLOAD_FAILED';
      return { ok: false, error: { code } };
    }
  });
  handleTrustedIpc('server:storage-probe', async () => serverManager.probeStorage());
  handleTrustedIpc('server:storage-migrate', async (event, options) => serverManager.migrateStorage(options));
  handleTrustedIpc('server:choose-restore-backup', async (event, options = {}) => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: String(options.title || launcherT('choose_omlorix_backup')),
      buttonLabel: String(options.buttonLabel || launcherT('choose_backup')),
      filters: [
        {
          name: String(options.filterName || launcherT('backup_archives')),
          extensions: ['enc', 'zst'],
        },
        { name: String(options.allFilesName || launcherT('all_files')), extensions: ['*'] },
      ],
      properties: ['openFile'],
    });
    if (result.canceled || !result.filePaths.length) return { canceled: true };
    return { canceled: false, filePath: result.filePaths[0] };
  });
  handleTrustedIpc('server:restore', async (event, source) => serverManager.restore(source));
  handleTrustedIpc('server:verify-backup', async (event, source) => serverManager.verifyBackup(source));
  handleTrustedIpc('server:service-action', async (event, action, serviceName, options) => (
    serverManager.serviceAction(action, serviceName, options)
  ));
  handleTrustedIpc('server:logs', async (event, options) => serverManager.logs(options));
  handleTrustedIpc('server:logs-follow-start', async (event, options) => (
    serverManager.startLogFollow(options)
  ));
  handleTrustedIpc('server:logs-follow-stop', async (event, sessionId) => (
    serverManager.stopLogFollow(sessionId)
  ));
  handleTrustedIpc('server:open-url', async () => {
    await serverManager.openUrl(shell);
    return { ok: true };
  });
  handleTrustedIpc('server:reveal-home', async () => {
    await serverManager.revealServerHome(shell);
    return { ok: true };
  });

  // Code Execution deployments have independent lifecycles and secrets, so
  // their IPC surface is deliberately separate from the Omlorix stack actions.
  const editorErrorCodes = new Set([
    'NAME_REQUIRED',
    'VERSION_INVALID',
    'MEMORY_INVALID',
    'IMAGE_SOURCE_INVALID',
    'SOURCE_MISSING',
    'PORT_IN_USE',
    'INSTANCE_NOT_FOUND',
    'SECRET_MISSING',
  ]);
  const editorResult = async (operation) => {
    try {
      return { ok: true, value: await operation() };
    } catch (error) {
      const code = String(error?.code || 'UNKNOWN');
      return {
        ok: false,
        error: {
          code,
          // Only manager-authored validation errors may cross into the
          // renderer. Filesystem/process errors can contain private paths.
          message: editorErrorCodes.has(code)
            ? String(error?.message || 'Could not save the Code Execution service')
            : 'Could not save the Code Execution service',
        },
      };
    }
  };
  handleTrustedIpc('code-execution:list', async () => codeExecutionManager.list());
  handleTrustedIpc('code-execution:get-available-versions', async () => codeExecutionManager.availableVersions());
  handleTrustedIpc('code-execution:get', async (event, instanceId) => codeExecutionManager.get(instanceId));
  handleTrustedIpc('code-execution:create', async (event, payload) => (
    editorResult(() => codeExecutionManager.create(payload))
  ));
  handleTrustedIpc('code-execution:save', async (event, instanceId, payload) => (
    editorResult(() => codeExecutionManager.save(instanceId, payload))
  ));
  handleTrustedIpc('code-execution:start', async (event, instanceId) => codeExecutionManager.start(instanceId));
  handleTrustedIpc('code-execution:stop', async (event, instanceId) => codeExecutionManager.stop(instanceId));
  handleTrustedIpc('code-execution:restart', async (event, instanceId) => codeExecutionManager.restart(instanceId));
  handleTrustedIpc('code-execution:check-update', async (event, instanceId) => codeExecutionManager.checkUpdate(instanceId));
  handleTrustedIpc('code-execution:update', async (event, instanceId) => codeExecutionManager.update(instanceId));
  handleTrustedIpc('code-execution:logs', async (event, instanceId, lines) => codeExecutionManager.logs(instanceId, lines));
  handleTrustedIpc('code-execution:connection-details', async (event, instanceId) => (
    codeExecutionManager.connectionDetails(instanceId)
  ));
  handleTrustedIpc('code-execution:copy-connection', async (event, instanceId) => {
    const details = await codeExecutionManager.connectionDetails(instanceId);
    const { adminUrl: _adminUrl, ...payload } = details;
    clipboard.writeText(JSON.stringify(payload, null, 2));
    return { ok: true };
  });
  handleTrustedIpc('code-execution:open-omlorix-connections', async () => {
    await serverManager.openServiceConnections(shell);
    return { ok: true };
  });
  handleTrustedIpc('code-execution:reveal', async (event, instanceId) => (
    codeExecutionManager.reveal(instanceId, shell)
  ));
  handleTrustedIpc('code-execution:remove', async (event, instanceId) => codeExecutionManager.remove(instanceId));
}

app.whenReady().then(async () => {
  hasSingleInstanceLock = app.requestSingleInstanceLock();
  if (!hasSingleInstanceLock) {
    app.quit();
    return;
  }

  app.setName(APP_NAME);
  configureNativeAboutPanel();
  serverManager = new ServerManager({ app, appRoot: appRoot() });
  codeExecutionManager = new CodeExecutionManager({
    app,
    appRoot: appRoot(),
    serverManager,
  });
  scheduledUpdateManager = new ScheduledUpdateManager({ app, serverManager });
  launcherAutoUpdater = createLauncherAutoUpdateService({
    app,
    readSettings: () => serverManager.readServerSettings(),
    fetcher: serverManager.fetchJson.bind(serverManager),
  });
  serverManager.on('operation-start', (payload) => sendToRenderer('server:operation-start', payload));
  serverManager.on('operation-output', (payload) => sendToRenderer('server:operation-output', payload));
  serverManager.on('operation-end', (payload) => sendToRenderer('server:operation-end', payload));
  serverManager.on('log-follow-output', (payload) => sendToRenderer('server:log-follow-output', payload));
  serverManager.on('log-follow-end', (payload) => sendToRenderer('server:log-follow-end', payload));
  codeExecutionManager.on('operation-start', (payload) => sendToRenderer('code-execution:operation-start', payload));
  codeExecutionManager.on('operation-output', (payload) => sendToRenderer('code-execution:operation-output', payload));
  codeExecutionManager.on('operation-end', (payload) => sendToRenderer('code-execution:operation-end', payload));
  scheduledUpdateManager.on('changed', (payload) => sendToRenderer('scheduled-updates:changed', payload));
  launcherAutoUpdater.on('progress', (payload) => {
    const percent = Math.max(0, Math.min(100, Number(payload?.percent || 0)));
    setLauncherUpdateProgress(percent > 0 ? percent / 100 : 0.01);
    launcherUpdateProgressController?.updateProgress?.(payload);
  });
  launcherAutoUpdater.on('downloaded', () => setLauncherUpdateProgress(-1));
  launcherAutoUpdater.on('updater-error', () => {
    setLauncherUpdateProgress(-1);
    clearLauncherInstallWatchdog();
    if (launcherUpdateFlowPromise) return;
    showNativeMessageBox({
      type: 'error',
      title: launcherT('software_update'),
      message: launcherT('finish_install_failed'),
      detail: launcherT('updater_reported_error'),
      buttons: [launcherT('ok')],
      defaultId: 0,
      noLink: true,
    }).catch(() => {});
  });

  registerIpc();
  buildMenu();
  // Upgrade legacy generated credentials before the first renderer state is
  // loaded. In particular, old short password-reset salts would otherwise make
  // the settings page reject every unrelated autosave until the operator found
  // and manually repaired the hidden legacy value.
  await serverManager.ensureGeneratedSecrets();
  await scheduledUpdateManager.initialize();
  await serverManager.initializeProxy();
  ensureLauncherTray();
  await loadLauncher();
  // Do not delay the first paint with Docker/HTTP checks. Once visible, record
  // the configured version if the launcher attached to an already-healthy
  // server that was started before this launcher session.
  void serverManager.recordRunningServerVersion().catch(() => {});
});

app.on('second-instance', () => {
  if (mainWindow) revealWindow(mainWindow);
});

app.on('activate', async () => {
  if (!mainWindow) {
    await loadLauncher();
    return;
  }
  revealWindow(mainWindow);
});

app.on('window-all-closed', () => {
  if (serverManager?.proxy?.status().running) {
    ensureLauncherTray();
    return;
  }
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', (event) => {
  void serverManager?.stopLogFollow().catch(() => {});
  if (confirmedProxyQuit || !serverManager?.proxy?.status().running) return;
  event.preventDefault();
  if (proxyQuitPromptOpen) return;
  proxyQuitPromptOpen = true;
  void confirmProxyQuit().then(async (confirmed) => {
    proxyQuitPromptOpen = false;
    if (!confirmed) return;
    confirmedProxyQuit = true;
    await serverManager.proxy.stop().catch(() => {});
    app.quit();
  });
});
