import { spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolveDesktopArchitecture } from './package-desktop-architecture.mjs';

const require = createRequire(import.meta.url);
const builderCli = require.resolve('electron-builder/cli.js');
const signingRequired = process.env.OMLORIX_REQUIRE_DESKTOP_SIGNING === '1';
const packageVersion = JSON.parse(readFileSync('package.json', 'utf8')).version;

const mode = process.argv[2] || 'dir';
const launcherUpdateChannel = process.env.OMLORIX_LAUNCHER_UPDATE_CHANNEL === 'beta'
  ? 'beta'
  : 'latest';
const targetMap = {
  dir: { args: ['--dir'], host: null, label: 'current host' },
  mac: { args: ['--mac'], host: 'darwin', label: 'macOS' },
  win: { args: ['--win'], host: 'win32', label: 'Windows' },
  linux: { args: ['--linux'], host: 'linux', label: 'Linux' },
};

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: process.cwd(),
    stdio: 'inherit',
    env: options.env || process.env,
  });

  // Surface launch failures explicitly so CI logs show the real issue instead of
  // only an exit code. This matters most on Windows, where launching wrapper
  // scripts directly is less predictable than invoking the CLI through Node.
  if (result.error) {
    console.error(
      `[desktop] Failed to start ${command}: ${result.error.message}`
    );
    process.exit(1);
  }

  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

const target = targetMap[mode];
if (!target) {
  console.error(`Unsupported packaging target "${mode}". Use one of: ${Object.keys(targetMap).join(', ')}`);
  process.exit(1);
}

if (target.host && process.platform !== target.host) {
  console.error(
    `Cannot build the ${target.label} desktop bundle on ${process.platform}. ` +
    'Cross-compilation is not supported.'
  );
  process.exit(1);
}

if (mode === 'mac') {
  run(process.execPath, ['electron/scripts/validate-desktop-signing.mjs']);
}

if (process.platform === 'darwin' && (mode === 'mac' || mode === 'dir')) {
  // Compile the native AppKit updater window before electron-builder copies it
  // into Contents/Resources. The nested executable is then signed together
  // with the rest of the application bundle.
  run(process.execPath, ['electron/scripts/build-macos-update-ui.mjs']);
}

// The desktop Launcher and terminal workflow must control one authoritative
// proxy executable. Build the current-platform CLI before packaging and place
// it in Resources/native so native service definitions remain valid after the
// Launcher window closes.
run(process.execPath, ['electron/scripts/prepare-cli-assets.mjs']);
mkdirSync('.build/cli', { recursive: true });
const bundledCliPath = process.platform === 'win32'
  ? '.build/cli/omlorix-server.exe'
  : '.build/cli/omlorix-server';
let desktopArchitecture;
try {
  desktopArchitecture = resolveDesktopArchitecture(process.argv.slice(3), process.arch);
} catch (error) {
  console.error(`[desktop] ${error.message}`);
  process.exit(1);
}
run('go', [
  'build',
  '-trimpath',
  `-ldflags=-s -w -X main.cliVersion=${packageVersion}`,
  '-o',
  bundledCliPath,
  './cmd/omlorix-server-cli',
], {
  env: {
    ...process.env,
    CGO_ENABLED: '0',
    GOARCH: desktopArchitecture.goArchitecture,
  },
});

if (mode === 'mac' && !signingRequired) {
  // Electron Builder treats present-but-empty signing variables as an attempt to
  // sign with invalid inputs. Remove them entirely so unsigned macOS builds can
  // proceed when release secrets are intentionally not configured.
  for (const name of [
    'CSC_LINK',
    'CSC_KEY_PASSWORD',
    'CSC_NAME',
    'APPLE_ID',
    'APPLE_APP_SPECIFIC_PASSWORD',
    'APPLE_TEAM_ID',
    'APPLE_API_KEY',
    'APPLE_API_KEY_ID',
    'APPLE_API_ISSUER',
  ]) {
    delete process.env[name];
  }
}

// Resolve the Electron Builder CLI from the installed package and invoke it via
// Node so the same code path works across macOS, Linux, and Windows runners.
//
// The release workflow uploads artifacts explicitly with `gh release upload`.
// Passing `--publish never` prevents electron-builder from auto-detecting CI,
// attempting an implicit GitHub publish, and then failing because it expects a
// separate GitHub token flow during packaging.
run(process.execPath, [
  builderCli,
  `-c.publish.channel=${launcherUpdateChannel}`,
  '--publish',
  'never',
  ...target.args,
  ...process.argv.slice(3),
]);
