const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');

const {
  OFFICIAL_CHANNEL_FEEDS,
  OFFICIAL_LAUNCHER_CHANNEL_FEEDS,
  OFFICIAL_RELEASES_API_URL,
  buildFetchHttpError,
  fetchJson,
  headersForRedirect,
  normalizeAvailableVersionsFromGitHubReleases,
  normalizeLauncherReleaseInfoFromFeed,
  normalizeReleaseInfoFromFeed,
  normalizeUpdateChannel,
  resolveAvailableVersions,
  resolveLauncherReleaseInfo,
  resolveReleaseInfo,
} = require('../release-channels');

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject);
      resolve(server.address());
    });
  });
}

function close(server) {
  return new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}

test('normalizeUpdateChannel accepts stable and beta only', () => {
  assert.equal(normalizeUpdateChannel('stable'), 'stable');
  assert.equal(normalizeUpdateChannel('BETA'), 'beta');
  assert.equal(normalizeUpdateChannel('nightly'), 'stable');
  assert.equal(normalizeUpdateChannel(''), 'stable');
});

test('update metadata endpoints are fixed to the official Omlorix infrastructure', () => {
  assert.equal(OFFICIAL_CHANNEL_FEEDS.stable, 'https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/stable.json');
  assert.equal(OFFICIAL_CHANNEL_FEEDS.beta, 'https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/beta.json');
  assert.equal(OFFICIAL_LAUNCHER_CHANNEL_FEEDS.stable, 'https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/launcher-stable.json');
  assert.equal(OFFICIAL_LAUNCHER_CHANNEL_FEEDS.beta, 'https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/launcher-beta.json');
  assert.equal(OFFICIAL_RELEASES_API_URL, 'https://api.github.com/repos/phinaldoo/omlorix/releases?per_page=50');
});

test('normalizeReleaseInfoFromFeed accepts tag-based beta feed payloads', () => {
  const info = normalizeReleaseInfoFromFeed({
    channel: 'beta',
    tag: 'v1.2.0-beta.1',
    manifestUrl: 'https://github.com/phinaldoo/omlorix/releases/download/v1.2.0-beta.1/omlorix-release-manifest.json',
    minimumLauncherVersion: '1.0.0',
    launcherVersion: '1.1.0',
    launcherReleaseTag: 'server-launcher-v1.1.0',
  });

  assert.equal(info.channel, 'beta');
  assert.equal(info.version, '1.2.0-beta.1');
  assert.equal(info.minimumLauncherVersion, '1.0.0');
  assert.equal(info.launcherVersion, '1.1.0');
  assert.equal(info.launcherReleaseTag, 'server-launcher-v1.1.0');
});

test('normalizeLauncherReleaseInfoFromFeed accepts launcher-prefixed tags', () => {
  const info = normalizeLauncherReleaseInfoFromFeed({
    channel: 'stable',
    tag: 'server-launcher-v1.1.0',
    releaseUrl: 'https://github.com/phinaldoo/omlorix/releases/tag/server-launcher-v1.1.0',
    electronUpdaterUrl: 'https://updates.example/launcher/v1.1.0/',
    electronUpdaterChannel: 'latest',
  });

  assert.equal(info.channel, 'stable');
  assert.equal(info.version, '1.1.0');
  assert.equal(info.tag, 'server-launcher-v1.1.0');
  assert.equal(info.electronUpdaterUrl, 'https://updates.example/launcher/v1.1.0/');
  assert.equal(info.electronUpdaterChannel, 'latest');
});

test('normalizeAvailableVersionsFromGitHubReleases filters versions by channel', () => {
  const payload = [
    { tag_name: 'v1.3.0-beta.2', prerelease: true },
    { tag_name: 'v1.2.0', prerelease: false },
    { tag_name: 'v1.2.1', prerelease: false, draft: true },
    { tag_name: 'server-launcher-v1.1.0', prerelease: false },
    { tag_name: 'v1.1.0-beta.1', prerelease: true },
  ];

  assert.deepEqual(
    normalizeAvailableVersionsFromGitHubReleases(payload, 'stable').map((version) => version.value),
    ['1.2.0'],
  );
  assert.deepEqual(
    normalizeAvailableVersionsFromGitHubReleases(payload, 'beta').map((version) => version.value),
    ['1.3.0-beta.2', '1.1.0-beta.1'],
  );
});

test('resolveAvailableVersions fetches public release metadata anonymously', async () => {
  const calls = [];
  const versions = await resolveAvailableVersions({
    channel: 'stable',
    fetcher: async (url, timeoutMs) => {
      calls.push({ url, timeoutMs });
      return [{ tag_name: 'v1.2.3', prerelease: false }];
    },
  });

  assert.deepEqual(versions.map((version) => version.value), ['1.2.3']);
  assert.deepEqual(calls, [
    {
      url: 'https://api.github.com/repos/phinaldoo/omlorix/releases?per_page=50',
      timeoutMs: 10000,
    },
  ]);
});

test('buildFetchHttpError includes GitHub response details when available', () => {
  const error = buildFetchHttpError(
    'https://api.github.com/repos/acme/private-omlorix/releases/latest',
    404,
    JSON.stringify({
      message: 'Not Found',
      documentation_url: 'https://docs.github.com/rest/releases/releases#get-the-latest-release',
    }),
  );

  assert.match(error.message, /HTTP 404/);
  assert.match(error.message, /Not Found/);
  assert.match(error.message, /Docs:/);
});

test('fetchJson follows release asset redirects without leaking auth across origins', async () => {
  let redirectedAuthorization = '';
  const assetServer = http.createServer((req, res) => {
    redirectedAuthorization = String(req.headers.authorization || '');
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('Connection', 'close');
    res.end(JSON.stringify({ version: '1.2.3' }));
  });
  const assetAddress = await listen(assetServer);

  const feedServer = http.createServer((req, res) => {
    res.writeHead(302, {
      Location: `http://127.0.0.1:${assetAddress.port}/omlorix-release-manifest.json`,
      Connection: 'close',
    });
    res.end();
  });
  const feedAddress = await listen(feedServer);

  try {
    const payload = await fetchJson(`http://127.0.0.1:${feedAddress.port}/download`, 10000, {
      headers: { Authorization: 'Bearer secret-token' },
    });

    assert.deepEqual(payload, { version: '1.2.3' });
    assert.equal(redirectedAuthorization, '');
  } finally {
    await close(feedServer);
    await close(assetServer);
  }
});

test('headersForRedirect preserves headers only across the same origin', () => {
  assert.deepEqual(
    headersForRedirect(
      'https://api.github.com/repos/acme/private-omlorix/releases/latest',
      'https://api.github.com/repos/acme/private-omlorix/releases/tag/v1.2.3',
      { Authorization: 'Bearer secret-token' },
    ),
    { Authorization: 'Bearer secret-token' },
  );
  assert.deepEqual(
    headersForRedirect(
      'https://api.github.com/repos/acme/private-omlorix/releases/latest',
      'https://github.com/acme/private-omlorix/releases/tag/v1.2.3',
      { Authorization: 'Bearer secret-token', Accept: 'application/json' },
    ),
    { Accept: 'application/json' },
  );
});

test('headersForRedirect strips authorization case-insensitively', () => {
  assert.deepEqual(
    headersForRedirect(
      'https://updates.example/stable.json',
      'https://cdn.example/stable.json',
      {
        Authorization: 'Bearer upper',
        authorization: 'Bearer lower',
        AUTHORIZATION: 'Bearer loud',
        Accept: 'application/json',
      },
    ),
    { Accept: 'application/json' },
  );
});

test('fetchJson applies one timeout budget across redirects', async () => {
  let assetResponseCompleted = false;
  const assetServer = http.createServer((req, res) => {
    setTimeout(() => {
      assetResponseCompleted = true;
      res.setHeader('Content-Type', 'application/json');
      res.setHeader('Connection', 'close');
      res.end(JSON.stringify({ version: '1.2.3' }));
    }, 80);
  });
  const assetAddress = await listen(assetServer);

  const feedServer = http.createServer((req, res) => {
    setTimeout(() => {
      res.writeHead(302, {
        Location: `http://127.0.0.1:${assetAddress.port}/omlorix-release-manifest.json`,
        Connection: 'close',
      });
      res.end();
    }, 60);
  });
  const feedAddress = await listen(feedServer);

  try {
    await assert.rejects(
      () => fetchJson(`http://127.0.0.1:${feedAddress.port}/download`, 90),
      /timed out/,
    );
    // Comparing elapsed wall time is flaky when the parallel test runner
    // briefly starves the event loop. The contract is that the shared timeout
    // wins before the redirected response's later timer can complete.
    assert.equal(assetResponseCompleted, false);
  } finally {
    await close(feedServer);
    await close(assetServer);
  }
});

test('resolveReleaseInfo fails when the official channel feed fails', async () => {
  const calls = [];
  await assert.rejects(
    () => resolveReleaseInfo({
      channel: 'stable',
      fetcher: async (url, timeoutMs) => {
        calls.push({ url, timeoutMs });
        throw new Error('missing feed');
      },
    }),
    /missing feed/,
  );

  assert.deepEqual(calls, [
    {
      url: 'https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/stable.json',
      timeoutMs: 10000,
    },
  ]);
});

test('resolveLauncherReleaseInfo reads the launcher channel feed', async () => {
  const calls = [];
  const info = await resolveLauncherReleaseInfo({
    channel: 'stable',
    fetcher: async (url, timeoutMs) => {
      calls.push({ url, timeoutMs });
      return {
        tag: 'server-launcher-v1.1.0',
      };
    },
  });

  assert.equal(info.version, '1.1.0');
  assert.deepEqual(calls, [
    {
      url: 'https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/launcher-stable.json',
      timeoutMs: 10000,
    },
  ]);
});
