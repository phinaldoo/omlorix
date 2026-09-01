function normalizeVersion(value) {
  return String(value || '').trim().replace(/^(?:server-launcher-|launcher-)?v/i, '');
}

function parseVersion(value) {
  const normalized = normalizeVersion(value);
  const [core, prerelease = ''] = normalized.split('-', 2);
  const parts = core.split('.').map((part) => {
    const parsed = Number.parseInt(part, 10);
    return Number.isFinite(parsed) ? parsed : 0;
  });
  while (parts.length < 3) parts.push(0);
  return {
    normalized,
    parts: parts.slice(0, 3),
    prerelease,
  };
}

/**
 * Compare prerelease identifiers using semver ordering rules so numeric
 * segments sort numerically and shorter equal identifier lists win.
 */
function comparePrereleaseIdentifiers(left, right) {
  const leftIdentifiers = String(left || '').split('.').filter(Boolean);
  const rightIdentifiers = String(right || '').split('.').filter(Boolean);
  const maxLength = Math.max(leftIdentifiers.length, rightIdentifiers.length);
  for (let index = 0; index < maxLength; index += 1) {
    const leftIdentifier = leftIdentifiers[index];
    const rightIdentifier = rightIdentifiers[index];
    if (leftIdentifier === undefined) return -1;
    if (rightIdentifier === undefined) return 1;
    if (leftIdentifier === rightIdentifier) continue;

    const leftIsNumeric = /^\d+$/.test(leftIdentifier);
    const rightIsNumeric = /^\d+$/.test(rightIdentifier);
    if (leftIsNumeric && rightIsNumeric) {
      const leftValue = Number.parseInt(leftIdentifier, 10);
      const rightValue = Number.parseInt(rightIdentifier, 10);
      if (leftValue > rightValue) return 1;
      if (leftValue < rightValue) return -1;
      continue;
    }
    if (leftIsNumeric && !rightIsNumeric) return -1;
    if (!leftIsNumeric && rightIsNumeric) return 1;
    if (leftIdentifier > rightIdentifier) return 1;
    if (leftIdentifier < rightIdentifier) return -1;
  }
  return 0;
}

function compareVersions(left, right) {
  const a = parseVersion(left);
  const b = parseVersion(right);
  for (let index = 0; index < 3; index += 1) {
    if (a.parts[index] > b.parts[index]) return 1;
    if (a.parts[index] < b.parts[index]) return -1;
  }
  if (a.prerelease && !b.prerelease) return -1;
  if (!a.prerelease && b.prerelease) return 1;
  const prereleaseComparison = comparePrereleaseIdentifiers(a.prerelease, b.prerelease);
  if (prereleaseComparison !== 0) return prereleaseComparison;
  return 0;
}

module.exports = {
  compareVersions,
  normalizeVersion,
};
