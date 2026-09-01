const { EventEmitter } = require('events');
const {
  normalizeUpdateChannel,
  resolveLauncherReleaseInfo,
} = require('./release-channels');
const { normalizeVersion } = require('./version-utils');

const OFFICIAL_RELEASE_DOWNLOAD_BASE_URL = 'https://github.com/phinaldoo/omlorix/releases/download';
const ELECTRON_UPDATER_CHANNELS = {
  stable: 'latest',
  beta: 'beta',
};

function getDefaultAutoUpdater() {
  // Loading electron-updater touches Electron internals, so keep it lazy. Unit
  // tests can inject a fake updater and plain Node never has to boot Electron.
  return require('electron-updater').autoUpdater;
}

function electronUpdaterChannel(channelInput) {
  const channel = normalizeUpdateChannel(channelInput);
  return ELECTRON_UPDATER_CHANNELS[channel] || ELECTRON_UPDATER_CHANNELS.stable;
}

/**
 * Select the metadata filename published with the concrete launcher release.
 *
 * Before the first prerelease exists, the beta channel feed deliberately
 * points at the current stable launcher and therefore needs `latest.yml`.
 * Once a real beta is published it selects `beta.yml` as usual. Stable feeds
 * may never redirect operators onto prerelease metadata.
 */
function electronUpdaterChannelForRelease(channelInput, releaseInfo = {}) {
  const channel = normalizeUpdateChannel(channelInput);
  const advertised = String(releaseInfo.electronUpdaterChannel || '').trim().toLowerCase();
  if (!advertised) return electronUpdaterChannel(channel);
  if (!['latest', 'beta'].includes(advertised)) {
    throw new Error('Launcher update feed included an unsupported updater channel.');
  }
  if (channel === 'stable' && advertised !== 'latest') {
    throw new Error('Stable launcher update feed cannot select prerelease metadata.');
  }
  return advertised;
}

function trimTrailingSlash(value) {
  return String(value || '').replace(/\/+$/, '');
}

function normalizeReleaseDownloadBaseUrl(value) {
  const parsed = new URL(`${trimTrailingSlash(value)}/`);
  if (parsed.protocol !== 'https:') {
    throw new Error('Launcher updater feed URL must use HTTPS.');
  }
  return trimTrailingSlash(parsed.toString());
}

function releaseDownloadBaseUrl(tag) {
  const normalizedTag = String(tag || '').trim();
  if (!normalizedTag) {
    throw new Error('Launcher update feed did not include a release tag.');
  }
  const base = normalizeReleaseDownloadBaseUrl(OFFICIAL_RELEASE_DOWNLOAD_BASE_URL);
  return `${base}/${encodeURIComponent(normalizedTag)}/`;
}

function releaseInfoUpdaterUrl(releaseInfo = {}, fallbackTag) {
  const expectedFeedUrl = releaseDownloadBaseUrl(fallbackTag);
  const feedUpdaterUrl = String(releaseInfo.electronUpdaterUrl || '').trim();
  if (!feedUpdaterUrl) {
    return expectedFeedUrl;
  }

  // The remotely published channel document may repeat the updater URL for
  // other clients, but it must not expand this launcher's hardcoded
  // trust boundary to a different host, repository, path, or release tag.
  const normalizedFeedUrl = `${normalizeReleaseDownloadBaseUrl(feedUpdaterUrl)}/`;
  if (normalizedFeedUrl !== expectedFeedUrl) {
    throw new Error(
      'Launcher updater feed URL must match the expected release download URL.',
    );
  }
  return expectedFeedUrl;
}

function normalizeReleaseNotes(value) {
  if (Array.isArray(value)) {
    return value
      .map((entry) => {
        if (typeof entry === 'string') return entry;
        return String(entry?.note || '').trim();
      })
      .filter(Boolean)
      .join('\n\n');
  }
  return String(value || '').trim();
}

function buildUpdateResult({
  channel,
  currentVersion,
  updateInfo = {},
  updateAvailable = false,
  downloaded = false,
  files = [],
  status = updateAvailable ? 'available' : 'current',
} = {}) {
  return {
    channel: normalizeUpdateChannel(channel),
    currentVersion: normalizeVersion(currentVersion),
    latestVersion: normalizeVersion(updateInfo.version || currentVersion),
    updateAvailable: Boolean(updateAvailable),
    downloaded: Boolean(downloaded),
    status,
    releaseName: String(updateInfo.releaseName || '').trim(),
    releaseNotes: normalizeReleaseNotes(updateInfo.releaseNotes),
    releaseDate: String(updateInfo.releaseDate || '').trim(),
    files,
  };
}

class LauncherAutoUpdateService extends EventEmitter {
  constructor({
    app,
    readSettings,
    fetcher,
    getUpdater = getDefaultAutoUpdater,
    now = Date.now,
  } = {}) {
    super();
    this.app = app;
    this.readSettings = readSettings;
    this.fetcher = fetcher;
    this.getUpdater = getUpdater;
    this.now = now;
    this.updater = null;
    this.configuredKey = '';
    this.lastResult = null;
    this.lastCheckedAt = 0;
    this.checkFailures = new Map();
    this.checkPromise = null;
    this.checkChannel = '';
    this.downloadedResult = null;
    this.listenersAttached = false;
  }

  updaterInstance() {
    if (!this.updater) {
      this.updater = this.getUpdater();
      this.attachUpdaterListeners(this.updater);
    }
    return this.updater;
  }

  attachUpdaterListeners(updater) {
    if (this.listenersAttached || !updater?.on) return;
    this.listenersAttached = true;

    updater.on('download-progress', (progress) => {
      this.emit('progress', {
        percent: Number(progress?.percent || 0),
        bytesPerSecond: Number(progress?.bytesPerSecond || 0),
        transferred: Number(progress?.transferred || 0),
        total: Number(progress?.total || 0),
      });
    });

    updater.on('update-downloaded', (updateInfo) => {
      const channel = this.lastResult?.channel || 'stable';
      this.downloadedResult = buildUpdateResult({
        channel,
        currentVersion: this.app?.getVersion?.() || this.lastResult?.currentVersion || '',
        updateInfo,
        updateAvailable: true,
        downloaded: true,
        status: 'downloaded',
      });
      this.emit('downloaded', this.downloadedResult);
    });

    updater.on('error', (error) => {
      this.emit('updater-error', {
        message: error?.message || String(error || 'Launcher update failed.'),
      });
    });
  }

  /** Resolve the shared management preference without consulting container env. */
  async resolveChannel(channelInput) {
    if (String(channelInput || '').trim()) return normalizeUpdateChannel(channelInput);
    const settings = typeof this.readSettings === 'function' ? await this.readSettings() : {};
    return normalizeUpdateChannel(settings?.updateChannel);
  }

  async configure(channelInput) {
    const channel = await this.resolveChannel(channelInput);
    const releaseInfo = await resolveLauncherReleaseInfo({
      channel,
      fetcher: this.fetcher,
    });
    const updaterChannel = electronUpdaterChannelForRelease(channel, releaseInfo);
    const feedUrl = releaseInfoUpdaterUrl(
      releaseInfo,
      releaseInfo.tag || releaseInfo.launcherReleaseTag,
    );
    const updater = this.updaterInstance();
    const configKey = `${channel}:${updaterChannel}:${feedUrl}`;

    // Keep downloads user-approved: check first, download on the button press,
    // and only restart/install after the user presses the install button.
    updater.autoDownload = false;
    updater.autoInstallOnAppQuit = false;
    updater.allowPrerelease = channel === 'beta';
    updater.allowDowngrade = false;
    updater.channel = updaterChannel;
    updater.requestHeaders = null;

    if (this.configuredKey !== configKey) {
      updater.setFeedURL({
        provider: 'generic',
        url: feedUrl,
        channel: updaterChannel,
      });
      this.configuredKey = configKey;
      this.downloadedResult = null;
    }

    return {
      channel,
      feedUrl,
      releaseInfo,
      updater,
    };
  }

  /**
   * Check for a launcher release without downloading it.
   *
   * Dashboard refreshes may ask for this information frequently, so callers
   * can provide a cache lifetime. Interactive checks omit maxAgeMs and always
   * contact the release feed. A single in-flight updater request is shared to
   * prevent electron-updater from running overlapping checks.
   */
  async check(channelInput, { maxAgeMs = 0, failureMaxAgeMs = 0 } = {}) {
    const channel = await this.resolveChannel(channelInput);
    const cacheLifetime = Math.max(0, Number(maxAgeMs) || 0);
    const failureCacheLifetime = Math.max(0, Number(failureMaxAgeMs) || 0);
    const checkedAt = this.now();
    const cacheIsFresh = cacheLifetime > 0
      && this.lastResult?.channel === channel
      && checkedAt - this.lastCheckedAt < cacheLifetime;
    if (cacheIsFresh) {
      return this.lastResult;
    }

    if (this.checkPromise) {
      // A check for the same channel can be shared directly. If settings
      // changed channels while a request was active, finish that request and
      // then perform the newly requested channel check.
      if (this.checkChannel === channel) {
        return this.checkPromise;
      }
      try {
        await this.checkPromise;
      } catch {
        // The pending request's failure does not determine whether the newly
        // requested channel can be checked successfully.
      }
      return this.check(channelInput, { maxAgeMs, failureMaxAgeMs });
    }

    const lastFailure = this.checkFailures.get(channel);
    const failureCacheIsFresh = failureCacheLifetime > 0
      && lastFailure
      && checkedAt - lastFailure.checkedAt < failureCacheLifetime;
    if (failureCacheIsFresh) {
      throw lastFailure.error;
    }

    this.checkChannel = channel;
    this.checkPromise = (async () => {
      try {
        const { updater } = await this.configure(channel);
        const currentVersion = this.app?.getVersion?.() || '';
        const result = await updater.checkForUpdates();
        if (!result) {
          this.lastResult = buildUpdateResult({
            channel,
            currentVersion,
            updateAvailable: false,
            status: 'unsupported',
          });
        } else {
          this.lastResult = buildUpdateResult({
            channel,
            currentVersion,
            updateInfo: result.updateInfo,
            updateAvailable: Boolean(result.isUpdateAvailable),
            status: result.isUpdateAvailable ? 'available' : 'current',
          });
        }
        this.lastCheckedAt = this.now();
        this.checkFailures.delete(channel);
        return this.lastResult;
      } catch (error) {
        this.checkFailures.set(channel, {
          checkedAt: this.now(),
          error,
        });
        throw error;
      }
    })();

    try {
      return await this.checkPromise;
    } finally {
      this.checkPromise = null;
      this.checkChannel = '';
    }
  }

  async download() {
    if (!this.lastResult?.updateAvailable) {
      throw new Error('No launcher update is available to download.');
    }

    const updater = this.updaterInstance();
    const files = await updater.downloadUpdate();
    if (!this.downloadedResult) {
      this.downloadedResult = buildUpdateResult({
        channel: this.lastResult.channel,
        currentVersion: this.lastResult.currentVersion,
        updateInfo: { version: this.lastResult.latestVersion },
        updateAvailable: true,
        downloaded: true,
        files,
        status: 'downloaded',
      });
    } else {
      this.downloadedResult.files = files;
    }
    return this.downloadedResult;
  }

  /**
   * Hand the downloaded artifact back to the platform updater for installation.
   *
   * On macOS, electron-updater delegates this step to Squirrel.Mac, which
   * verifies that the replacement bundle satisfies the installed app's code
   * signing requirement before it is allowed to replace and relaunch the app.
   * Keeping installation behind this API preserves that trust boundary.
   */
  async install() {
    if (!this.downloadedResult?.downloaded) {
      throw new Error('Download the launcher update before installing it.');
    }
    this.updaterInstance().quitAndInstall(false, true);
    return { ok: true, installer: 'electron-updater' };
  }
}

function createLauncherAutoUpdateService(options) {
  return new LauncherAutoUpdateService(options);
}

module.exports = {
  OFFICIAL_RELEASE_DOWNLOAD_BASE_URL,
  LauncherAutoUpdateService,
  buildUpdateResult,
  createLauncherAutoUpdateService,
  electronUpdaterChannel,
  electronUpdaterChannelForRelease,
  normalizeReleaseDownloadBaseUrl,
  releaseDownloadBaseUrl,
};
