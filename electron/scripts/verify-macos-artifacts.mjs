import { existsSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: process.cwd(),
    stdio: 'inherit',
  });
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

const distDir = path.resolve(process.cwd(), 'dist');
const productName = 'Omlorix Server Launcher.app';
const notarizationRequired = process.env.OMLORIX_REQUIRE_MACOS_NOTARIZATION === '1';
const appCandidates = [
  path.join(distDir, 'mac', productName),
  path.join(distDir, 'mac-arm64', productName),
  path.join(distDir, 'mac-universal', productName),
];
const appPath = appCandidates.find((candidate) => existsSync(candidate));

if (!appPath) {
  console.error('No macOS app bundle found in dist/.');
  process.exit(1);
}

console.log(`[desktop] Verifying code signature for ${appPath}`);
run('codesign', ['--verify', '--deep', '--strict', '--verbose=2', appPath]);
if (notarizationRequired) {
  // Gatekeeper assessment and stapler validation require a completed Apple
  // notarization. A signed-only release still verifies the local code signature
  // above, but deliberately has no ticket for these checks to validate.
  run('spctl', ['--assess', '--type', 'execute', '--verbose=4', appPath]);
  run('xcrun', ['stapler', 'validate', appPath]);
} else {
  console.log('[desktop] Skipping app notarization ticket verification.');
}

const dmgArtifacts = readdirSync(distDir)
  .filter((entry) => entry.endsWith('.dmg'))
  .map((entry) => path.join(distDir, entry));

if (!dmgArtifacts.length) {
  console.error('No macOS DMG artifacts found in dist/.');
  process.exit(1);
}

for (const artifact of dmgArtifacts) {
  if (notarizationRequired) {
    console.log(`[desktop] Verifying notarized DMG ${artifact}`);
    run('xcrun', ['stapler', 'validate', artifact]);
  } else {
    console.log(`[desktop] Notarization ticket verification is disabled for ${artifact}.`);
  }
}
