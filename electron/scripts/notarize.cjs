const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: process.cwd(),
    encoding: 'utf8',
    stdio: 'inherit',
  });

  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed with exit code ${result.status || 1}`);
  }
}

module.exports = async function notarize(context) {
  if (context.electronPlatformName !== 'darwin') {
    return;
  }

  if (process.env.OMLORIX_REQUIRE_MACOS_NOTARIZATION !== '1') {
    console.log(
      `[desktop] Skipping notarization for ${context.packager.appInfo.productFilename}: ` +
      'Apple notarization is disabled for this build.'
    );
    return;
  }

  const requiredEnv = ['APPLE_ID', 'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID'];
  const missingEnv = requiredEnv.filter((name) => !process.env[name]);
  if (missingEnv.length > 0) {
    throw new Error(
      `Missing Apple notarization environment variables: ${missingEnv.join(', ')}`
    );
  }

  const appPath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`
  );
  const notarizationZipPath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}-notarization.zip`
  );

  console.log(`[desktop] Preparing notarization archive for ${appPath}`);
  fs.rmSync(notarizationZipPath, { force: true });
  run('ditto', ['-c', '-k', '--keepParent', appPath, notarizationZipPath]);

  try {
    console.log(`[desktop] Notarizing ${notarizationZipPath}`);
    run('xcrun', [
      'notarytool',
      'submit',
      notarizationZipPath,
      '--apple-id',
      process.env.APPLE_ID,
      '--password',
      process.env.APPLE_APP_SPECIFIC_PASSWORD,
      '--team-id',
      process.env.APPLE_TEAM_ID,
      '--wait',
    ]);
  } finally {
    fs.rmSync(notarizationZipPath, { force: true });
  }

  console.log(`[desktop] Stapling notarization ticket to ${appPath}`);
  run('xcrun', ['stapler', 'staple', appPath]);
  run('xcrun', ['stapler', 'validate', appPath]);
};
