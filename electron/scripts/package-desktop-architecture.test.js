const assert = require('node:assert/strict');
const test = require('node:test');

const architectureModule = import('./package-desktop-architecture.mjs');

test('desktop architecture defaults to the host architecture', async () => {
  const { resolveDesktopArchitecture } = await architectureModule;
  assert.deepEqual(resolveDesktopArchitecture([], 'x64'), {
    electronArchitecture: 'x64',
    goArchitecture: 'amd64',
  });
});

test('desktop architecture maps an explicit cross-architecture target', async () => {
  const { resolveDesktopArchitecture } = await architectureModule;
  assert.deepEqual(resolveDesktopArchitecture(['--arm64'], 'x64'), {
    electronArchitecture: 'arm64',
    goArchitecture: 'arm64',
  });
});

test('desktop architecture rejects multi-architecture packages', async () => {
  const { resolveDesktopArchitecture } = await architectureModule;
  assert.throws(
    () => resolveDesktopArchitecture(['--x64', '--arm64'], 'x64'),
    /one architecture at a time/,
  );
  assert.throws(
    () => resolveDesktopArchitecture(['--universal'], 'arm64'),
    /Unsupported desktop architecture/,
  );
});

test('desktop architecture rejects 32-bit targets removed in Electron 44', async () => {
  const { resolveDesktopArchitecture } = await architectureModule;
  assert.throws(
    () => resolveDesktopArchitecture(['--ia32'], 'x64'),
    /Unsupported desktop architecture: ia32/,
  );
  assert.throws(
    () => resolveDesktopArchitecture(['--armv7l'], 'x64'),
    /Unsupported desktop architecture: armv7l/,
  );
});
