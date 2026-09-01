import { copyFileSync, mkdirSync, rmSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const projectRoot = process.cwd();
const sourcePath = path.join(
  projectRoot,
  'electron',
  'native',
  'macos',
  'OmlorixUpdateProgress.swift',
);
const infoPlistSourcePath = path.join(
  projectRoot,
  'electron',
  'native',
  'macos',
  'OmlorixUpdateProgress-Info.plist',
);
const outputDirectory = path.join(projectRoot, '.build', 'native-macos');
const appBundlePath = path.join(outputDirectory, 'OmlorixUpdateProgress.app');
const contentsDirectory = path.join(appBundlePath, 'Contents');
const executableDirectory = path.join(contentsDirectory, 'MacOS');
const outputPath = path.join(executableDirectory, 'OmlorixUpdateProgress');
const moduleCachePath = path.join(outputDirectory, 'module-cache');
const architectures = ['arm64', 'x86_64'];

/**
 * Compile the tiny AppKit helper before electron-builder assembles the bundle.
 * A fixed deployment target prevents a helper built on a new Xcode SDK from
 * accidentally requiring that same newest macOS release at runtime.
 */
function buildNativeUpdateUI() {
  rmSync(appBundlePath, { force: true, recursive: true });
  mkdirSync(outputDirectory, { recursive: true });
  mkdirSync(executableDirectory, { recursive: true });
  copyFileSync(infoPlistSourcePath, path.join(contentsDirectory, 'Info.plist'));
  const architectureOutputs = architectures.map((architecture) => {
    const architectureOutput = path.join(outputDirectory, `OmlorixUpdateProgress-${architecture}`);
    const architectureModuleCache = path.join(moduleCachePath, architecture);
    rmSync(architectureOutput, { force: true });
    mkdirSync(architectureModuleCache, { recursive: true });
    const result = spawnSync('xcrun', [
      'swiftc',
      '-O',
      '-target',
      `${architecture}-apple-macos13.0`,
      '-framework',
      'AppKit',
      sourcePath,
      '-o',
      architectureOutput,
    ], {
      cwd: projectRoot,
      env: {
        ...process.env,
        // Keep compiler caches inside the project so sandboxed local builds and
        // ephemeral CI runners do not depend on a writable global cache folder.
        CLANG_MODULE_CACHE_PATH: architectureModuleCache,
        SWIFT_MODULE_CACHE_PATH: architectureModuleCache,
      },
      stdio: 'inherit',
    });

    if (result.error) {
      throw new Error(`Failed to start the ${architecture} Swift compiler: ${result.error.message}`);
    }
    if (result.status !== 0) {
      process.exit(result.status || 1);
    }
    return architectureOutput;
  });

  const lipoResult = spawnSync('xcrun', [
    'lipo',
    '-create',
    ...architectureOutputs,
    '-output',
    outputPath,
  ], {
    cwd: projectRoot,
    stdio: 'inherit',
  });
  if (lipoResult.error) {
    throw new Error(`Failed to start lipo: ${lipoResult.error.message}`);
  }
  if (lipoResult.status !== 0) {
    process.exit(lipoResult.status || 1);
  }

  // Seal the generated helper bundle with an ad-hoc signature so unsigned
  // development packages remain internally valid. electron-builder replaces
  // this with the release identity when production signing is enabled.
  const signingResult = spawnSync('/usr/bin/codesign', [
    '--force',
    '--deep',
    '--sign',
    '-',
    appBundlePath,
  ], {
    cwd: projectRoot,
    stdio: 'inherit',
  });
  if (signingResult.error) {
    throw new Error(`Failed to start codesign: ${signingResult.error.message}`);
  }
  if (signingResult.status !== 0) {
    process.exit(signingResult.status || 1);
  }
  console.log(`[desktop] Built native macOS update UI: ${appBundlePath}`);
}

if (process.platform !== 'darwin') {
  console.error('The native macOS update UI can only be built on macOS.');
  process.exit(1);
}

buildNativeUpdateUI();
