const fs = require('node:fs');
const path = require('node:path');

/**
 * Returns the repo root for desktop launcher development scripts.
 *
 * Keeping this in one helper makes the dev launcher scripts easier to test
 * because tests can point the helpers at a temporary project tree.
 *
 * @returns {string}
 */
function defaultProjectRoot() {
  return path.resolve(__dirname, '..', '..');
}

/**
 * Returns the relative executable path that Electron stores in `path.txt`
 * for the current platform.
 *
 * The Electron npm package normally writes this file during installation.
 * When that step fails on some Windows setups, we can still recover if the
 * unpacked binary already exists inside `node_modules/electron/dist`.
 *
 * @param {string} platform
 * @returns {string}
 */
function getElectronExecutableRelativePath(platform = process.platform) {
  switch (platform) {
    case 'darwin':
    case 'mas':
      return path.join('Electron.app', 'Contents', 'MacOS', 'Electron');
    case 'linux':
    case 'freebsd':
    case 'openbsd':
      return 'electron';
    case 'win32':
      return 'electron.exe';
    default:
      throw new Error(`Electron builds are not available on platform: ${platform}`);
  }
}

/**
 * Builds the absolute path to the local Electron npm package directory.
 *
 * @param {string} projectRoot
 * @returns {string}
 */
function getElectronPackageDirectory(projectRoot = defaultProjectRoot()) {
  return path.join(projectRoot, 'node_modules', 'electron');
}

/**
 * Builds the environment for the GUI Electron child process.
 *
 * Development shells embedded in Electron-based tools can export
 * `ELECTRON_RUN_AS_NODE=1` for their own helper commands. The Omlorix launcher
 * is a desktop application, so forwarding that flag would make its Electron
 * executable run `main.js` as plain Node and leave the Electron `app` API
 * unavailable.
 *
 * @param {NodeJS.ProcessEnv} [environment]
 * @returns {NodeJS.ProcessEnv}
 */
function createElectronSpawnEnvironment(environment = process.env) {
  const childEnvironment = { ...environment };
  delete childEnvironment.ELECTRON_RUN_AS_NODE;
  return childEnvironment;
}

/**
 * Builds the absolute path to the Electron binary inside the unpacked
 * `dist` folder.
 *
 * @param {object} options
 * @param {string} [options.projectRoot]
 * @param {string} [options.platform]
 * @returns {string}
 */
function getElectronDistExecutable({
  projectRoot = defaultProjectRoot(),
  platform = process.platform,
} = {}) {
  return path.join(
    getElectronPackageDirectory(projectRoot),
    'dist',
    getElectronExecutableRelativePath(platform),
  );
}

/**
 * Repairs the Electron package metadata when the executable is already on
 * disk but `path.txt` was never written.
 *
 * This keeps the normal `require('electron')` resolution path healthy for
 * follow-up commands after we recover once.
 *
 * @param {object} options
 * @param {string} [options.projectRoot]
 * @param {string} [options.platform]
 * @param {typeof fs} [options.fsModule]
 * @returns {{ repaired: boolean, pathFile: string, executablePath: string }}
 */
function ensureElectronPathFile({
  projectRoot = defaultProjectRoot(),
  platform = process.platform,
  fsModule = fs,
} = {}) {
  const packageDirectory = getElectronPackageDirectory(projectRoot);
  const executableRelativePath = getElectronExecutableRelativePath(platform);
  const executablePath = path.join(packageDirectory, 'dist', executableRelativePath);
  const pathFile = path.join(packageDirectory, 'path.txt');

  if (!fsModule.existsSync(executablePath)) {
    return { repaired: false, pathFile, executablePath };
  }

  const existingPathValue = fsModule.existsSync(pathFile)
    ? fsModule.readFileSync(pathFile, 'utf8').trim()
    : null;

  if (existingPathValue === executableRelativePath) {
    return { repaired: false, pathFile, executablePath };
  }

  fsModule.writeFileSync(pathFile, executableRelativePath, 'utf8');
  return { repaired: true, pathFile, executablePath };
}

/**
 * Resolves the Electron executable for development mode.
 *
 * The normal path is `require('electron')`, but that can fail on Windows
 * when Electron's postinstall download finished and the package binary exists
 * while `path.txt` is missing. In that case we repair `path.txt` and fall
 * back to the unpacked binary directly.
 *
 * @param {object} options
 * @param {string} [options.projectRoot]
 * @param {string} [options.platform]
 * @param {typeof fs} [options.fsModule]
 * @param {() => string} [options.electronLoader]
 * @returns {string}
 */
function resolveElectronExecutable({
  projectRoot = defaultProjectRoot(),
  platform = process.platform,
  fsModule = fs,
  electronLoader = () => require('electron'),
} = {}) {
  try {
    const resolvedExecutable = electronLoader();
    if (typeof resolvedExecutable === 'string' && fsModule.existsSync(resolvedExecutable)) {
      return resolvedExecutable;
    }
  } catch (error) {
    const fallbackExecutable = getElectronDistExecutable({ projectRoot, platform });
    if (fsModule.existsSync(fallbackExecutable)) {
      ensureElectronPathFile({ projectRoot, platform, fsModule });
      return fallbackExecutable;
    }

    const packageDirectory = getElectronPackageDirectory(projectRoot);
    throw new Error(
      [
        'Unable to locate the Electron development executable.',
        `Expected a working Electron installation in "${packageDirectory}".`,
        `Original loader error: ${error.message}`,
      ].join(' '),
    );
  }

  const fallbackExecutable = getElectronDistExecutable({ projectRoot, platform });
  if (fsModule.existsSync(fallbackExecutable)) {
    ensureElectronPathFile({ projectRoot, platform, fsModule });
    return fallbackExecutable;
  }

  throw new Error(
    [
      'Unable to locate the Electron development executable.',
      'The Electron npm package resolved without an executable path,',
      'and no unpacked binary was found in `node_modules/electron/dist`.',
    ].join(' '),
  );
}

module.exports = {
  createElectronSpawnEnvironment,
  defaultProjectRoot,
  ensureElectronPathFile,
  getElectronDistExecutable,
  getElectronExecutableRelativePath,
  getElectronPackageDirectory,
  resolveElectronExecutable,
};
