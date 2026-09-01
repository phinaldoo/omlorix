const { spawn } = require('node:child_process');

const {
  createElectronSpawnEnvironment,
  defaultProjectRoot,
  ensureElectronPathFile,
  resolveElectronExecutable,
} = require('./dev-electron-runtime.cjs');

/**
 * Starts the Electron desktop launcher in development mode.
 *
 * We intentionally resolve the executable ourselves instead of shelling out
 * to a bare `electron` command. That avoids two Windows-specific failure
 * modes we hit during local setup:
 * 1. a stray `electron.exe` in the repo root can shadow npm's `.cmd` shim
 * 2. Electron can finish unpacking while still missing `path.txt`
 */
function run() {
  const projectRoot = defaultProjectRoot();

  // Repairing the metadata up front keeps future `require('electron')`
  // calls healthy even after this launcher exits.
  ensureElectronPathFile({ projectRoot });

  const electronExecutable = resolveElectronExecutable({ projectRoot });
  const electronArguments = process.argv.slice(2);
  const developmentArguments = electronArguments.length ? electronArguments : ['.'];

  const childProcess = spawn(electronExecutable, developmentArguments, {
    cwd: projectRoot,
    env: createElectronSpawnEnvironment(),
    stdio: 'inherit',
    windowsHide: false,
  });

  childProcess.on('exit', (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal);
      return;
    }

    process.exit(code ?? 0);
  });
}

run();
