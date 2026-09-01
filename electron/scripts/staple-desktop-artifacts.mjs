import { readdirSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

if (process.env.OMLORIX_REQUIRE_MACOS_NOTARIZATION !== '1') {
  // Keep the script safe when it is run manually or reused by another workflow:
  // the repository-controlled toggle must explicitly authorize Apple uploads.
  console.log('[desktop] Apple notarization is disabled; skipping DMG notarization and stapling.');
  process.exit(0);
}

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: process.cwd(),
    stdio: 'inherit',
  });
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

function requireNotarizationCredentials() {
  const requiredEnv = ['APPLE_ID', 'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID'];
  const missingEnv = requiredEnv.filter((name) => !process.env[name]);

  if (missingEnv.length > 0) {
    console.error(
      `Missing Apple notarization environment variables: ${missingEnv.join(', ')}`
    );
    process.exit(1);
  }
}

const distDir = path.resolve(process.cwd(), 'dist');
const dmgArtifacts = readdirSync(distDir)
  .filter((entry) => entry.endsWith('.dmg'))
  .map((entry) => path.join(distDir, entry));

if (!dmgArtifacts.length) {
  console.error('No macOS DMG artifacts found in dist/.');
  process.exit(1);
}

requireNotarizationCredentials();

for (const artifact of dmgArtifacts) {
  console.log(`[desktop] Notarizing ${artifact}`);
  run('xcrun', [
    'notarytool',
    'submit',
    artifact,
    '--apple-id',
    process.env.APPLE_ID,
    '--password',
    process.env.APPLE_APP_SPECIFIC_PASSWORD,
    '--team-id',
    process.env.APPLE_TEAM_ID,
    '--wait',
  ]);

  console.log(`[desktop] Stapling ${artifact}`);
  run('xcrun', ['stapler', 'staple', artifact]);
  run('xcrun', ['stapler', 'validate', artifact]);
}
