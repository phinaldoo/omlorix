const http = require('http');
const https = require('https');

const DEFAULT_CHANNEL = 'stable';
const UPDATE_CHANNELS = ['stable', 'beta'];
const OFFICIAL_CHANNEL_FEEDS = {
  stable: 'https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/stable.json',
  beta: 'https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/beta.json',
};
const OFFICIAL_LAUNCHER_CHANNEL_FEEDS = {
  stable: 'https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/launcher-stable.json',
  beta: 'https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/launcher-beta.json',
};
const OFFICIAL_RELEASES_API_URL = 'https://api.github.com/repos/phinaldoo/omlorix/releases?per_page=50';

function normalizeUpdateChannel(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return UPDATE_CHANNELS.includes(normalized) ? normalized : DEFAULT_CHANNEL;
}

function channelLabel(channel) {
  return normalizeUpdateChannel(channel) === 'beta' ? 'Beta' : 'Stable';
}

function summarizeResponseBody(body) {
  const text = String(body || '').trim();
  if (!text) return '';

  try {
    const parsed = JSON.parse(text);
    const parts = [];
    if (parsed && typeof parsed === 'object') {
      if (String(parsed.message || '').trim()) {
        parts.push(String(parsed.message).trim());
      }
      if (String(parsed.documentation_url || '').trim()) {
        parts.push(`Docs: ${String(parsed.documentation_url).trim()}`);
      }
    }
    return parts.join(' | ');
  } catch (error) {
    return text.slice(0, 400);
  }
}

function buildFetchHttpError(url, statusCode, body) {
  const summary = summarizeResponseBody(body);
  if (summary) {
    return new Error(`${url} returned HTTP ${statusCode}: ${summary}`);
  }
  return new Error(`${url} returned HTTP ${statusCode}`);
}

function removeAuthorizationHeaders(headers = {}) {
  const sanitized = { ...headers };
  for (const key of Object.keys(sanitized)) {
    if (key.toLowerCase() === 'authorization') {
      delete sanitized[key];
    }
  }
  return sanitized;
}

function headersForRedirect(previousUrl, nextUrl, headers = {}) {
  const previous = new URL(previousUrl);
  const next = new URL(nextUrl);
  if (previous.origin === next.origin) {
    return headers;
  }

  return removeAuthorizationHeaders(headers);
}

function remainingTimeoutMs(deadlineMs) {
  return Math.max(1, deadlineMs - Date.now());
}

function fetchJson(url, timeoutMs = 10000, requestOptions = {}, redirectCount = 0, deadlineMs = Date.now() + timeoutMs) {
  const parsed = new URL(url);
  const client = parsed.protocol === 'https:' ? https : http;
  const headers = {
    Accept: 'application/json, application/vnd.github+json',
    'User-Agent': 'omlorix-server-launcher',
    ...(requestOptions.headers || {}),
  };
  return new Promise((resolve, reject) => {
    const req = client.get(parsed, {
      headers,
    }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => {
        body += chunk;
      });
      res.on('end', () => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          if (redirectCount >= 5) {
            reject(new Error(`${url} redirected too many times.`));
            return;
          }
          const nextUrl = new URL(res.headers.location, parsed).toString();
          fetchJson(nextUrl, timeoutMs, {
            ...requestOptions,
            headers: headersForRedirect(url, nextUrl, headers),
          }, redirectCount + 1, deadlineMs).then(resolve, reject);
          return;
        }
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(buildFetchHttpError(url, res.statusCode, body));
          return;
        }
        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(error);
        }
      });
    });
    req.setTimeout(remainingTimeoutMs(deadlineMs), () => {
      req.destroy(new Error(`${url} timed out.`));
    });
    req.on('error', reject);
  });
}

function normalizeTagVersion(value) {
  return String(value || '').trim().replace(/^(?:server-launcher-|launcher-)?v/i, '');
}

function normalizeReleaseInfoFromFeed(feed, channelInput = DEFAULT_CHANNEL) {
  const channel = normalizeUpdateChannel(feed?.channel || channelInput);
  const version = normalizeTagVersion(feed?.version || feed?.tag);
  if (!version) {
    throw new Error(`${channelLabel(channel)} update feed did not include a version.`);
  }
  return {
    channel,
    version,
    manifestUrl: String(feed?.manifestUrl || '').trim(),
    releaseUrl: String(feed?.releaseUrl || '').trim(),
    manifest: feed?.manifest && typeof feed.manifest === 'object' ? feed.manifest : null,
    minimumLauncherVersion: String(feed?.minimumLauncherVersion || '').trim(),
    launcherUpdateReason: String(feed?.launcherUpdateReason || '').trim(),
    launcherVersion: normalizeTagVersion(feed?.launcherVersion || feed?.launcherReleaseTag),
    launcherReleaseTag: String(feed?.launcherReleaseTag || '').trim(),
    launcherReleaseUrl: String(feed?.launcherReleaseUrl || '').trim(),
  };
}

function normalizeAvailableVersionsFromGitHubReleases(payload, channelInput = DEFAULT_CHANNEL) {
  const channel = normalizeUpdateChannel(channelInput);
  if (!Array.isArray(payload)) {
    throw new Error('Available releases response was not a list.');
  }

  const seen = new Set();
  const versions = [];

  for (const release of payload) {
    if (release?.draft) continue;
    const rawTag = String(release?.tag_name || release?.tag || '').trim();
    if (/^(?:server-launcher-|launcher-)/i.test(rawTag)) continue;
    const version = normalizeTagVersion(rawTag);
    if (!version || seen.has(version)) continue;

    const prerelease = Boolean(release?.prerelease) || /-/.test(version);
    if (channel === 'stable' && prerelease) continue;
    if (channel === 'beta' && !prerelease) continue;

    seen.add(version);
    versions.push({
      value: version,
      label: version,
      channel,
      prerelease,
      releaseUrl: String(release?.html_url || '').trim(),
    });
  }

  return versions;
}

function normalizeLauncherReleaseInfoFromFeed(feed, channelInput = DEFAULT_CHANNEL) {
  const channel = normalizeUpdateChannel(feed?.channel || channelInput);
  const version = normalizeTagVersion(feed?.launcherVersion || feed?.version || feed?.launcherReleaseTag || feed?.tag);
  if (!version) {
    throw new Error(`${channelLabel(channel)} launcher update feed did not include a version.`);
  }
  return {
    channel,
    version,
    tag: String(feed?.launcherReleaseTag || feed?.tag || `server-launcher-v${version}`).trim(),
    releaseUrl: String(feed?.launcherReleaseUrl || feed?.releaseUrl || '').trim(),
    electronUpdaterUrl: String(feed?.electronUpdaterUrl || '').trim(),
    electronUpdaterChannel: String(feed?.electronUpdaterChannel || '').trim(),
    electronUpdaterProvider: String(feed?.electronUpdaterProvider || '').trim(),
  };
}

async function resolveLauncherReleaseInfo({
  channel: channelInput = DEFAULT_CHANNEL,
  fetcher = fetchJson,
} = {}) {
  const channel = normalizeUpdateChannel(channelInput);
  const feedUrl = OFFICIAL_LAUNCHER_CHANNEL_FEEDS[channel];
  const feed = await fetcher(feedUrl, 10000);
  return normalizeLauncherReleaseInfoFromFeed(feed, channel);
}

async function resolveReleaseInfo({
  channel: channelInput = DEFAULT_CHANNEL,
  fetcher = fetchJson,
} = {}) {
  const channel = normalizeUpdateChannel(channelInput);
  const feedUrl = OFFICIAL_CHANNEL_FEEDS[channel];
  const feed = await fetcher(feedUrl, 10000);
  return normalizeReleaseInfoFromFeed(feed, channel);
}

async function resolveAvailableVersions({
  channel: channelInput = DEFAULT_CHANNEL,
  fetcher = fetchJson,
} = {}) {
  const channel = normalizeUpdateChannel(channelInput);
  const url = OFFICIAL_RELEASES_API_URL;
  const payload = await fetcher(url, 10000);
  return normalizeAvailableVersionsFromGitHubReleases(payload, channel);
}

module.exports = {
  DEFAULT_CHANNEL,
  OFFICIAL_CHANNEL_FEEDS,
  OFFICIAL_LAUNCHER_CHANNEL_FEEDS,
  OFFICIAL_RELEASES_API_URL,
  UPDATE_CHANNELS,
  buildFetchHttpError,
  channelLabel,
  fetchJson,
  headersForRedirect,
  normalizeAvailableVersionsFromGitHubReleases,
  normalizeLauncherReleaseInfoFromFeed,
  normalizeReleaseInfoFromFeed,
  normalizeUpdateChannel,
  resolveAvailableVersions,
  resolveLauncherReleaseInfo,
  resolveReleaseInfo,
};
