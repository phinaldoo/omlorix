const path = require('path');
const { fileURLToPath, pathToFileURL } = require('url');

function normalizeFilePath(value) {
  const normalized = path.normalize(value);
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized;
}

function getTrustedLauncherUrl(baseDir) {
  return pathToFileURL(path.join(baseDir, 'renderer', 'launcher.html')).toString();
}

function getTrustedRendererUrl(baseDir, fileName) {
  return pathToFileURL(path.join(baseDir, 'renderer', fileName)).toString();
}

function isTrustedLauncherUrl(value, trustedUrl) {
  if (typeof value !== 'string' || typeof trustedUrl !== 'string') {
    return false;
  }

  try {
    const candidate = new URL(value);
    const trusted = new URL(trustedUrl);

    if (candidate.protocol !== 'file:' || trusted.protocol !== 'file:') {
      return false;
    }

    return normalizeFilePath(fileURLToPath(candidate)) === normalizeFilePath(fileURLToPath(trusted));
  } catch (error) {
    return false;
  }
}

function isTrustedRendererUrl(value, trustedUrls) {
  const allowedUrls = Array.isArray(trustedUrls) ? trustedUrls : [trustedUrls];
  return allowedUrls.some((trustedUrl) => isTrustedLauncherUrl(value, trustedUrl));
}

module.exports = {
  getTrustedLauncherUrl,
  getTrustedRendererUrl,
  isTrustedLauncherUrl,
  isTrustedRendererUrl,
};
