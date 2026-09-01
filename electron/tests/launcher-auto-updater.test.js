const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { EventEmitter } = require('node:events');
const fs = require('node:fs/promises');
const { createRequire } = require('node:module');
const os = require('node:os');
const path = require('node:path');
const { parseUpdateInfo } = require('electron-updater/out/providers/Provider');

const {
  createLauncherAutoUpdateService,
  electronUpdaterChannel,
  normalizeReleaseDownloadBaseUrl,
  releaseDownloadBaseUrl,
} = require('../launcher-auto-updater');

class FakeUpdater extends EventEmitter {
  constructor(checkResult = null) {
    super();
    this.checkResult = checkResult;
    this.feedOptions = null;
    this.downloaded = false;
    this.installed = false;
  }

  setFeedURL(options) {
    this.feedOptions = options;
  }

  async checkForUpdates() {
    return this.checkResult;
  }

  async downloadUpdate() {
    this.emit('download-progress', {
      percent: 42,
      bytesPerSecond: 1024,
      transferred: 420,
      total: 1000,
    });
    this.emit('update-downloaded', this.checkResult.updateInfo);
    this.downloaded = true;
    return ['/tmp/launcher-update'];
  }

  quitAndInstall() {
    this.installed = true;
  }
}

function createService({
  settings = { updateChannel: 'stable' },
  feed = {},
  updater,
  app,
  ...serviceOptions
} = {}) {
  const fakeUpdater = updater || new FakeUpdater({
    isUpdateAvailable: true,
    updateInfo: {
      version: feed.version || '1.2.3',
      releaseName: 'Omlorix Server Launcher',
      releaseNotes: 'Updater-powered release.',
    },
  });
  const calls = [];
  const service = createLauncherAutoUpdateService({
    app: app || {
      getVersion: () => '1.2.2',
    },
    readSettings: async () => settings,
    fetcher: async (url, timeoutMs) => {
      calls.push({ url, timeoutMs });
      return feed;
    },
    getUpdater: () => fakeUpdater,
    ...serviceOptions,
  });
  return { calls, service, updater: fakeUpdater };
}

test('electron-updater bounds crafted ordered-map metadata parsing', () => {
  const updaterRequire = createRequire(require.resolve('electron-updater/package.json'));
  assert.equal(updaterRequire('js-yaml/package.json').version, '4.3.1');

  const result = spawnSync(
    process.execPath,
    [
      '-e',
      `
        const { parseUpdateInfo } = require('electron-updater/out/providers/Provider');
        const entries = 150000;
        const metadata = '!!omap\\n' + Array.from(
          { length: entries },
          (_, index) => \`- k\${index}: \${index}\`,
        ).join('\\n') + '\\n';
        const parsed = parseUpdateInfo(
          metadata,
          'latest-mac.yml',
          new URL('https://updates.example/latest-mac.yml'),
        );
        if (!Array.isArray(parsed) || parsed.length !== entries) process.exit(1);
      `,
    ],
    {
      cwd: path.join(__dirname, '..', '..'),
      encoding: 'utf8',
      timeout: 5000,
    },
  );

  assert.notEqual(
    result.error?.code,
    'ETIMEDOUT',
    'crafted !!omap update metadata exceeded the five-second CPU bound',
  );
  assert.equal(result.status, 0, result.stderr || result.error?.message);
});

test('electron-updater rejects malformed launcher metadata', () => {
  assert.throws(
    () => parseUpdateInfo(
      'version: [unterminated',
      'latest-mac.yml',
      new URL('https://updates.example/latest-mac.yml'),
    ),
    (error) => error.code === 'ERR_UPDATER_INVALID_UPDATE_INFO',
  );
});

test('electronUpdaterChannel maps app channels to electron-updater channels', () => {
  assert.equal(electronUpdaterChannel('stable'), 'latest');
  assert.equal(electronUpdaterChannel('beta'), 'beta');
  assert.equal(electronUpdaterChannel('unknown'), 'latest');
});

test('releaseDownloadBaseUrl creates a tag-scoped generic provider URL', () => {
  assert.equal(
    releaseDownloadBaseUrl('server-launcher-v1.2.3'),
    'https://github.com/phinaldoo/omlorix/releases/download/server-launcher-v1.2.3/',
  );
});

test('normalizeReleaseDownloadBaseUrl rejects non-HTTPS updater feeds', () => {
  assert.throws(
    () => normalizeReleaseDownloadBaseUrl('http://updates.example/launcher'),
    /HTTPS/i,
  );
});

test('check configures electron-updater for stable launcher releases', async () => {
  const { calls, service, updater } = createService({
    settings: { updateChannel: 'stable' },
    feed: {
      channel: 'stable',
      version: '1.2.3',
      tag: 'server-launcher-v1.2.3',
    },
  });

  const result = await service.check();

  assert.equal(result.updateAvailable, true);
  assert.equal(result.currentVersion, '1.2.2');
  assert.equal(result.latestVersion, '1.2.3');
  assert.equal(updater.autoDownload, false);
  assert.equal(updater.autoInstallOnAppQuit, false);
  assert.equal(updater.allowPrerelease, false);
  assert.equal(updater.channel, 'latest');
  assert.deepEqual(updater.feedOptions, {
    provider: 'generic',
    url: 'https://github.com/phinaldoo/omlorix/releases/download/server-launcher-v1.2.3/',
    channel: 'latest',
  });
  assert.equal(calls[0].url, 'https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/launcher-stable.json');
});

test('passive checks reuse a fresh launcher result without another feed request', async () => {
  const { calls, service, updater } = createService({
    settings: { updateChannel: 'stable' },
    feed: {
      channel: 'stable',
      version: '1.2.3',
      tag: 'server-launcher-v1.2.3',
    },
  });
  let updaterChecks = 0;
  const originalCheck = updater.checkForUpdates.bind(updater);
  updater.checkForUpdates = async () => {
    updaterChecks += 1;
    return originalCheck();
  };

  const first = await service.check('', { maxAgeMs: 60_000 });
  const second = await service.check('', { maxAgeMs: 60_000 });

  assert.strictEqual(second, first);
  assert.equal(calls.length, 1);
  assert.equal(updaterChecks, 1);
});

test('concurrent launcher checks share one electron-updater request', async () => {
  let finishCheck;
  const updater = new FakeUpdater();
  updater.checkForUpdates = () => new Promise((resolve) => {
    finishCheck = resolve;
  });
  let updaterChecks = 0;
  const delayedCheck = updater.checkForUpdates.bind(updater);
  updater.checkForUpdates = () => {
    updaterChecks += 1;
    return delayedCheck();
  };
  const { service } = createService({
    settings: { updateChannel: 'stable' },
    feed: {
      channel: 'stable',
      version: '1.2.3',
      tag: 'server-launcher-v1.2.3',
    },
    updater,
  });

  const firstCheck = service.check();
  const secondCheck = service.check();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(updaterChecks, 1);

  finishCheck({
    isUpdateAvailable: true,
    updateInfo: { version: '1.2.3' },
  });
  const [first, second] = await Promise.all([firstCheck, secondCheck]);
  assert.deepEqual(second, first);
  assert.equal(first.latestVersion, '1.2.3');
});

test('passive launcher failures are coalesced, cooled down, and recoverable', async () => {
  const failureFixtures = [
    () => new Error('Launcher feed returned HTTP 404.'),
    () => Object.assign(new Error('Launcher feed timed out.'), { code: 'ETIMEDOUT' }),
    () => Object.assign(new Error('Launcher feed is offline.'), { code: 'ENOTFOUND' }),
    () => Object.assign(new Error('Launcher feed failed.'), { statusCode: 503 }),
  ];

  for (const makeFailure of failureFixtures) {
    let now = 1_000;
    let attempts = 0;
    let recover = false;
    const { service } = createService({
      now: () => now,
      fetcher: async () => {
        attempts += 1;
        await new Promise((resolve) => setImmediate(resolve));
        if (!recover) throw makeFailure();
        return {
          channel: 'stable',
          version: '1.2.3',
          tag: 'server-launcher-v1.2.3',
        };
      },
    });
    const passiveOptions = { maxAgeMs: 4 * 60 * 60 * 1000, failureMaxAgeMs: 60_000 };

    const startupBurst = await Promise.allSettled([
      service.check('stable', passiveOptions),
      service.check('stable', passiveOptions),
      service.check('stable', passiveOptions),
      service.check('stable', passiveOptions),
    ]);
    assert(startupBurst.every((result) => result.status === 'rejected'));
    assert.equal(attempts, 1);

    await assert.rejects(() => service.check('stable', passiveOptions));
    assert.equal(attempts, 1, 'the passive cooldown must not contact the feed');

    now += 60_001;
    await assert.rejects(() => service.check('stable', passiveOptions));
    assert.equal(attempts, 2, 'the feed must be retried after the cooldown');

    recover = true;
    const recovered = await service.check('stable', {
      maxAgeMs: 0,
      failureMaxAgeMs: 0,
    });
    assert.equal(attempts, 3, 'an explicit check must bypass a fresh passive failure');
    assert.equal(recovered.latestVersion, '1.2.3');
  }

  const channelAttempts = [];
  const { service } = createService({
    fetcher: async (url) => {
      channelAttempts.push(url);
      throw new Error('HTTP 404');
    },
  });
  const passiveOptions = { maxAgeMs: 4 * 60 * 60 * 1000, failureMaxAgeMs: 60_000 };
  await assert.rejects(() => service.check('stable', passiveOptions));
  await assert.rejects(() => service.check('beta', passiveOptions));
  await assert.rejects(() => service.check('stable', passiveOptions));
  assert.equal(channelAttempts.length, 2, 'each release channel must retain its own cooldown');
  assert.match(channelAttempts[0], /launcher-stable\.json$/);
  assert.match(channelAttempts[1], /launcher-beta\.json$/);
});

test('check rejects a launcher feed updater URL on an unexpected host', async () => {
  const { service, updater } = createService({
    settings: { updateChannel: 'stable' },
    feed: {
      channel: 'stable',
      version: '1.2.3',
      tag: 'server-launcher-v1.2.3',
      electronUpdaterUrl: 'https://evil-updates.example/server-launcher-v1.2.3/',
    },
  });

  await assert.rejects(
    () => service.check(),
    /must match the expected release download URL/,
  );
  assert.equal(updater.feedOptions, null);
});

test('check rejects a launcher feed updater URL for a different release tag', async () => {
  const { service, updater } = createService({
    settings: { updateChannel: 'stable' },
    feed: {
      channel: 'stable',
      version: '1.2.3',
      tag: 'server-launcher-v1.2.3',
      electronUpdaterUrl: 'https://github.com/phinaldoo/omlorix/releases/download/server-launcher-v9.9.9/',
    },
  });

  await assert.rejects(
    () => service.check(),
    /must match the expected release download URL/,
  );
  assert.equal(updater.feedOptions, null);
});

test('check configures beta channel with the public generic feed', async () => {
  const { service, updater } = createService({
    settings: { updateChannel: 'beta' },
    feed: {
      channel: 'beta',
      version: '1.2.3-beta.1',
      tag: 'server-launcher-v1.2.3-beta.1',
    },
  });

  const result = await service.check();

  assert.equal(result.channel, 'beta');
  assert.equal(result.latestVersion, '1.2.3-beta.1');
  assert.equal(updater.allowPrerelease, true);
  assert.equal(updater.channel, 'beta');
  assert.equal(updater.requestHeaders, null);
  assert.deepEqual(updater.feedOptions, {
    provider: 'generic',
    url: 'https://github.com/phinaldoo/omlorix/releases/download/server-launcher-v1.2.3-beta.1/',
    channel: 'beta',
  });
});

test('beta baseline feed uses stable launcher metadata before the first prerelease', async () => {
  const { service, updater } = createService({
    settings: { updateChannel: 'beta' },
    feed: {
      channel: 'beta',
      version: '1.2.3',
      tag: 'server-launcher-v1.2.3',
      electronUpdaterChannel: 'latest',
      fallbackChannel: 'stable',
    },
  });

  const result = await service.check();

  assert.equal(result.channel, 'beta');
  assert.equal(result.latestVersion, '1.2.3');
  assert.equal(updater.allowPrerelease, true);
  assert.equal(updater.channel, 'latest');
  assert.equal(updater.feedOptions.channel, 'latest');
});

test('download relays progress and enables install', async () => {
  const { service, updater } = createService({
    settings: { updateChannel: 'stable' },
    feed: {
      version: '1.2.3',
      tag: 'server-launcher-v1.2.3',
    },
  });
  const progressEvents = [];
  service.on('progress', (payload) => progressEvents.push(payload));

  await service.check();
  const result = await service.download();
  const installResult = await service.install();

  assert.equal(result.downloaded, true);
  assert.deepEqual(result.files, ['/tmp/launcher-update']);
  assert.equal(progressEvents[0].percent, 42);
  assert.deepEqual(installResult, { ok: true, installer: 'electron-updater' });
  assert.equal(updater.installed, true);
});

test('packaged macOS install delegates the verified update to electron-updater', async () => {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-launcher-install-test-'));
  const zipPath = path.join(tempDir, 'launcher.zip');
  const helperDir = path.join(tempDir, 'user-data');
  await fs.writeFile(zipPath, 'zip');

  let quitCalled = false;
  const spawnCalls = [];
  const spawn = (command, args, options) => {
    spawnCalls.push({ command, args, options });
    return { unref() {} };
  };
  const { service, updater } = createService({
    app: {
      isPackaged: true,
      getVersion: () => '1.2.2',
      getPath: () => helperDir,
      quit: () => {
        quitCalled = true;
      },
    },
    processInfo: {
      execPath: '/Applications/Omlorix Server Launcher.app/Contents/MacOS/Omlorix Server Launcher',
      pid: 4242,
    },
    platform: 'darwin',
    spawn,
  });
  service.downloadedResult = {
    downloaded: true,
    files: [zipPath],
  };

  const installResult = await service.install();
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(installResult, { ok: true, installer: 'electron-updater' });
  assert.equal(updater.installed, true);
  assert.equal(spawnCalls.length, 0);
  assert.equal(quitCalled, false);
  await assert.rejects(
    fs.access(path.join(helperDir, 'launcher-update-helper', 'install-mac-update.sh')),
    { code: 'ENOENT' },
  );
});

test('check returns unsupported when electron-updater is inactive in development', async () => {
  const { service } = createService({
    settings: { updateChannel: 'stable' },
    feed: {
      version: '1.2.3',
      tag: 'server-launcher-v1.2.3',
    },
    updater: new FakeUpdater(null),
  });

  const result = await service.check();

  assert.equal(result.updateAvailable, false);
  assert.equal(result.status, 'unsupported');
});
