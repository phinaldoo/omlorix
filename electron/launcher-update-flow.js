const { createLauncherTranslator } = require('./launcher-native-i18n');

const MINIMUM_CHECKING_PROGRESS_MS = 350;
const defaultTranslate = createLauncherTranslator('en');

/** Keep a fast update check visible long enough to avoid a one-frame flash. */
function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function runLauncherUpdateFlow({
  launcherAutoUpdater,
  showMessageBox,
  setProgressBar = () => {},
  createProgressWindow,
  ensureInstallReady,
  showCheckingProgress = false,
  translate = defaultTranslate,
} = {}) {
  if (!launcherAutoUpdater) {
    throw new Error('Launcher updater is not configured.');
  }
  if (typeof showMessageBox !== 'function') {
    throw new Error('Launcher updater requires a message box function.');
  }

  let progressWindow = null;
  const t = translate;
  try {
    let result;
    if (showCheckingProgress && typeof createProgressWindow === 'function') {
      const checkingStartedAt = Date.now();
      progressWindow = createProgressWindow({ phase: 'checking' });
      progressWindow.setState?.({
        phase: 'checking',
        windowTitle: t('software_update'),
        message: t('checking_for_updates'),
        detail: 'Omlorix Server Launcher',
        primaryLabel: t('cancel'),
      });

      // The updater library does not expose a cancellable check operation. A
      // Cancel action dismisses the native UI and ignores the eventual result.
      const checkPromise = launcherAutoUpdater.check();
      const firstResult = await Promise.race([
        checkPromise.then((value) => ({ type: 'checked', value })),
        progressWindow.waitForAction().then((value) => ({ type: 'action', value })),
      ]);
      if (firstResult.type === 'action' && firstResult.value === 'cancel') {
        progressWindow.close?.();
        progressWindow = null;
        checkPromise.catch(() => {});
        return { ok: true, status: 'cancelled' };
      }
      result = firstResult.value;
      const remainingVisibleTime = MINIMUM_CHECKING_PROGRESS_MS - (Date.now() - checkingStartedAt);
      if (remainingVisibleTime > 0) {
        await delay(remainingVisibleTime);
      }
      progressWindow.close?.();
      progressWindow = null;
    } else {
      result = await launcherAutoUpdater.check();
    }

    if (result.status === 'unsupported') {
      await showMessageBox({
        type: 'info',
        title: t('software_update'),
        message: t('updates_unavailable'),
        detail: t('updates_packaged_only'),
        buttons: [t('ok')],
        defaultId: 0,
        noLink: true,
      });
      return { ok: true, status: 'unsupported' };
    }

    if (!result.updateAvailable) {
      await showMessageBox({
        type: 'info',
        title: t('software_update'),
        message: t('launcher_up_to_date'),
        detail: t('newest_channel_version', {
          currentVersion: result.currentVersion,
          channel: t(result.channel === 'beta' ? 'beta' : 'stable'),
        }),
        buttons: [t('ok')],
        defaultId: 0,
        noLink: true,
      });
      return { ok: true, status: 'current' };
    }

    if (typeof ensureInstallReady === 'function') {
      const installReady = await ensureInstallReady(result);
      if (installReady && installReady.ok === false) {
        return {
          ok: true,
          status: installReady.status || 'install-location-required',
        };
      }
    }

    const prompt = await showMessageBox({
      type: 'info',
      title: t('software_update'),
      message: t('launcher_version_available', { latestVersion: result.latestVersion }),
      detail: t('install_prompt', {
        currentVersion: result.currentVersion,
        channel: t(result.channel === 'beta' ? 'beta' : 'stable'),
      }),
      buttons: [t('install_update'), t('later')],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    });
    if (prompt.response !== 0) {
      return { ok: true, status: 'deferred' };
    }

    progressWindow = typeof createProgressWindow === 'function'
      ? createProgressWindow({
          channel: result.channel,
          currentVersion: result.currentVersion,
          latestVersion: result.latestVersion,
        })
      : null;

    let downloaded = null;
    try {
      setProgressBar(0.01);
      progressWindow?.setState?.({
        phase: 'downloading',
        message: t('downloading_update'),
        detail: `Omlorix Server Launcher ${result.latestVersion}`,
        percent: 0,
        primaryLabel: t('cancel'),
      });

      const downloadPromise = launcherAutoUpdater.download();
      const actionPromise = progressWindow?.waitForAction
        ? progressWindow.waitForAction()
        : new Promise(() => {});
      const firstResult = await Promise.race([
        downloadPromise.then((value) => ({ type: 'downloaded', value })),
        actionPromise.then((value) => ({ type: 'action', value })),
      ]);

      if (firstResult.type === 'action' && firstResult.value === 'cancel') {
        progressWindow?.setState?.({
          phase: 'finishing',
          message: t('finishing_download'),
          detail: t('download_continues'),
          primaryLabel: '',
        });
        downloaded = await downloadPromise;
        progressWindow?.close?.();
        progressWindow = null;
        setProgressBar(-1);
        return { ok: true, status: 'downloaded' };
      }

      if (firstResult.type === 'action') {
        downloaded = await downloadPromise;
      } else {
        downloaded = firstResult.value;
      }
      setProgressBar(-1);
    } catch (error) {
      progressWindow?.close?.();
      progressWindow = null;
      throw error;
    }

    if (!progressWindow) {
      await launcherAutoUpdater.install();
      return { ok: true, status: 'installing' };
    }

    if (progressWindow) {
      progressWindow.setState({
        phase: 'ready',
        message: t('ready_to_install'),
        detail: t('downloaded_relaunch', { latestVersion: downloaded.latestVersion }),
        percent: 100,
        primaryLabel: t('install_and_relaunch'),
        secondaryLabel: t('later'),
      });
      const action = await progressWindow.waitForAction();
      progressWindow.close();
      progressWindow = null;
      if (action !== 'primary') {
        return { ok: true, status: 'downloaded' };
      }
    }

    await launcherAutoUpdater.install();
    return { ok: true, status: 'installing' };
  } catch (error) {
    progressWindow?.close?.();
    setProgressBar(-1);
    await showMessageBox({
      type: 'error',
      title: t('software_update'),
      message: t('update_failed'),
      // Updater-library errors are technical English and may contain internal
      // URLs or platform details. Keep the native dialog fully localized and
      // leave the original exception available to the caller for diagnostics.
      detail: t('update_failed_retry'),
      buttons: [t('ok')],
      defaultId: 0,
      noLink: true,
    });
    throw error;
  }
}

module.exports = {
  runLauncherUpdateFlow,
};
