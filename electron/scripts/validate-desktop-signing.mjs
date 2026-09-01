const signingRequired = process.env.OMLORIX_REQUIRE_DESKTOP_SIGNING === '1';
const notarizationRequired = process.env.OMLORIX_REQUIRE_MACOS_NOTARIZATION === '1';

if (!signingRequired) {
  if (notarizationRequired) {
    console.error(
      '[desktop] macOS notarization cannot be enabled when production signing is disabled.'
    );
    process.exit(1);
  }
  console.log('[desktop] Production signing is not required for this build.');
  process.exit(0);
}

const certificateEnv = ['CSC_LINK', 'CSC_KEY_PASSWORD'];
const notarizationEnv = ['APPLE_ID', 'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID'];
const requiredEnv = notarizationRequired
  ? [...certificateEnv, ...notarizationEnv]
  : certificateEnv;

const missing = requiredEnv.filter((name) => !process.env[name]);

if (missing.length) {
  console.error(
    '[desktop] Production desktop signing is required, but these environment variables are missing: ' +
    missing.join(', ')
  );
  process.exit(1);
}

console.log('[desktop] Production signing credentials detected.');
if (notarizationRequired) {
  console.log('[desktop] Apple notarization credentials detected.');
} else {
  console.log('[desktop] Apple notarization is disabled for this signed build.');
}
