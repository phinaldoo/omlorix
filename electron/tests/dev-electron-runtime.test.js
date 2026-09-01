const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const {
  createElectronSpawnEnvironment,
  ensureElectronPathFile,
  resolveElectronExecutable,
} = require('../scripts/dev-electron-runtime.cjs');

/**
 * Creates a temporary fake Electron package tree that mirrors the parts of
 * `node_modules/electron` our recovery helpers care about.
 *
 * @returns {string}
 */
function createFakeProjectRoot() {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'omlorix-electron-runtime-'));
  fs.mkdirSync(path.join(projectRoot, 'node_modules', 'electron', 'dist'), { recursive: true });
  return projectRoot;
}

test('Electron GUI environment removes an inherited Node-only runtime flag', () => {
  const sourceEnvironment = {
    ELECTRON_RUN_AS_NODE: '1',
    PATH: '/example/bin',
  };

  const childEnvironment = createElectronSpawnEnvironment(sourceEnvironment);

  assert.equal(childEnvironment.ELECTRON_RUN_AS_NODE, undefined);
  assert.equal(childEnvironment.PATH, sourceEnvironment.PATH);
  assert.equal(sourceEnvironment.ELECTRON_RUN_AS_NODE, '1');
});

test('ensureElectronPathFile recreates path.txt when the Windows binary exists', () => {
  const projectRoot = createFakeProjectRoot();
  const executablePath = path.join(projectRoot, 'node_modules', 'electron', 'dist', 'electron.exe');

  fs.writeFileSync(executablePath, 'binary');

  const result = ensureElectronPathFile({ projectRoot, platform: 'win32' });

  assert.equal(result.repaired, true);
  assert.equal(
    fs.readFileSync(path.join(projectRoot, 'node_modules', 'electron', 'path.txt'), 'utf8'),
    'electron.exe',
  );
});

test('resolveElectronExecutable falls back to the unpacked binary when require fails', () => {
  const projectRoot = createFakeProjectRoot();
  const executablePath = path.join(projectRoot, 'node_modules', 'electron', 'dist', 'electron.exe');

  fs.writeFileSync(executablePath, 'binary');

  const resolvedExecutable = resolveElectronExecutable({
    projectRoot,
    platform: 'win32',
    electronLoader: () => {
      throw new Error('ENOENT: no such file or directory, open path.txt');
    },
  });

  assert.equal(resolvedExecutable, executablePath);
  assert.equal(
    fs.readFileSync(path.join(projectRoot, 'node_modules', 'electron', 'path.txt'), 'utf8'),
    'electron.exe',
  );
});
