const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const yaml = require('js-yaml');

const projectRoot = path.join(__dirname, '..', '..');
const desktopSecurityEnv = [
  'OMLORIX_REQUIRE_DESKTOP_SIGNING',
  'OMLORIX_REQUIRE_MACOS_NOTARIZATION',
  'CSC_LINK',
  'CSC_KEY_PASSWORD',
  'APPLE_ID',
  'APPLE_APP_SPECIFIC_PASSWORD',
  'APPLE_TEAM_ID',
];

/**
 * Run the production signing validator with an isolated security environment.
 *
 * Removing inherited credentials keeps these tests deterministic on developer
 * machines and CI runners that may already define Electron Builder variables.
 */
function runDesktopSigningValidation(overrides) {
  const env = { ...process.env };
  for (const name of desktopSecurityEnv) delete env[name];
  Object.assign(env, overrides);

  return spawnSync(
    process.execPath,
    ['electron/scripts/validate-desktop-signing.mjs'],
    {
      cwd: projectRoot,
      env,
      encoding: 'utf8',
    },
  );
}

/**
 * Expand the Electron Builder macros used by the Windows artifact templates.
 *
 * Keeping this small expansion in the test makes the collision check explicit
 * without depending on Electron Builder internals or running a Windows build.
 */
function expandArtifactName(template, values) {
  return template.replace(/\$\{([^}]+)\}/g, (_match, key) => values[key]);
}

test('source-inspection tests receive LF text on every checkout platform', async () => {
  const attributes = await fs.readFile(path.join(projectRoot, '.gitattributes'), 'utf8');
  assert.match(attributes, /^\* text=auto eol=lf$/m);
});

test('Windows installer and portable targets have distinct artifact names', async () => {
  const packageConfig = JSON.parse(
    await fs.readFile(path.join(projectRoot, 'package.json'), 'utf8'),
  );
  const values = {
    productName: packageConfig.build.productName,
    version: packageConfig.version,
    os: 'win',
    arch: 'x64',
    ext: 'exe',
  };
  const setupName = expandArtifactName(packageConfig.build.nsis.artifactName, values);
  const portableName = expandArtifactName(packageConfig.build.portable.artifactName, values);

  assert.notEqual(setupName, portableName);
  assert.match(setupName, /-setup\.exe$/);
  assert.match(portableName, /-portable\.exe$/);
});

test('desktop publication avoids personal maintainer metadata', async () => {
  const [packageJson, supportGuide, releaseWorkflow] = await Promise.all([
    fs.readFile(path.join(projectRoot, 'package.json'), 'utf8'),
    fs.readFile(
      path.join(projectRoot, 'documentation', 'v1.0.0', 'en', 'common', 'support.md'),
      'utf8',
    ),
    fs.readFile(
      path.join(projectRoot, '.github', 'workflows', 'server-launcher-release.yml'),
      'utf8',
    ),
  ]);
  const packageConfig = JSON.parse(packageJson);

  assert.equal(Object.hasOwn(packageConfig, 'author'), false);
  assert.equal(Object.hasOwn(packageConfig.build.linux, 'maintainer'), false);
  assert.deepEqual(packageConfig.build.linux.target, ['AppImage', 'tar.gz']);
  assert.match(supportGuide, /github\.com\/phinaldoo\/omlorix\/security\/advisories\/new/);
  assert.doesNotMatch(supportGuide, /mailto:/i);
  assert.doesNotMatch(releaseWorkflow, /\.deb(?:'|\s|$)/m);
});

test('Windows latest-download alias explicitly selects the NSIS setup artifact', async () => {
  const releaseWorkflow = await fs.readFile(
    path.join(projectRoot, '.github', 'workflows', 'server-launcher-release.yml'),
    'utf8',
  );

  assert.match(
    releaseWorkflow,
    /-name 'Omlorix Server Launcher-\*-setup\.exe'/,
  );
});

test('macOS app and native helper require the Electron 44 platform minimum', async () => {
  const [packageJson, helperInfoPlist, helperBuildScript] = await Promise.all([
    fs.readFile(path.join(projectRoot, 'package.json'), 'utf8'),
    fs.readFile(
      path.join(projectRoot, 'electron', 'native', 'macos', 'OmlorixUpdateProgress-Info.plist'),
      'utf8',
    ),
    fs.readFile(
      path.join(projectRoot, 'electron', 'scripts', 'build-macos-update-ui.mjs'),
      'utf8',
    ),
  ]);
  const packageConfig = JSON.parse(packageJson);

  assert.equal(packageConfig.build.mac.minimumSystemVersion, '13.0');
  assert.match(
    helperInfoPlist,
    /<key>LSMinimumSystemVersion<\/key>\s*<string>13\.0<\/string>/,
  );
  assert.match(helperBuildScript, /apple-macos13\.0/);
});

test('Windows rereleases override legacy artifact templates before packaging', async () => {
  const releaseWorkflow = await fs.readFile(
    path.join(projectRoot, '.github', 'workflows', 'server-launcher-release.yml'),
    'utf8',
  );
  const windowsBuildStep = releaseWorkflow.indexOf(
    '- name: Build Windows desktop artifacts',
  );
  const aliasStep = releaseWorkflow.indexOf(
    '- name: Create latest desktop download aliases',
  );

  // The current workflow runs even when rerelease mode checks out an older tag.
  // Keep the overrides in that workflow, ahead of alias creation, so historical
  // package metadata cannot restore the installer/portable name collision.
  assert.notEqual(windowsBuildStep, -1);
  assert.ok(windowsBuildStep < aliasStep);
  assert.match(
    releaseWorkflow,
    /-c\.nsis\.artifactName=\$\{productName\}-\$\{version\}-\$\{os\}-\$\{arch\}-setup\.\$\{ext\}/,
  );
  assert.match(
    releaseWorkflow,
    /-c\.portable\.artifactName=\$\{productName\}-\$\{version\}-\$\{os\}-\$\{arch\}-portable\.\$\{ext\}/,
  );
});

test('macOS releases make signing optional and require it for notarization', async () => {
  const releaseWorkflow = await fs.readFile(
    path.join(projectRoot, '.github', 'workflows', 'server-launcher-release.yml'),
    'utf8',
  );
  const appNotarizationHook = await fs.readFile(
    path.join(projectRoot, 'electron', 'scripts', 'notarize.cjs'),
    'utf8',
  );
  const dmgNotarizationScript = await fs.readFile(
    path.join(projectRoot, 'electron', 'scripts', 'staple-desktop-artifacts.mjs'),
    'utf8',
  );
  const verificationScript = await fs.readFile(
    path.join(projectRoot, 'electron', 'scripts', 'verify-macos-artifacts.mjs'),
    'utf8',
  );

  // Signing is enabled only for a complete certificate pair. A missing GitHub
  // secret resolves to an empty string, so an entirely absent pair produces an
  // unsigned build while a partial pair fails before packaging.
  assert.match(releaseWorkflow, /CSC_LINK: \$\{\{ secrets\.CSC_LINK \}\}/);
  assert.match(releaseWorkflow, /CSC_KEY_PASSWORD: \$\{\{ secrets\.CSC_KEY_PASSWORD \}\}/);
  assert.match(
    releaseWorkflow,
    /\[ -n "\$CSC_LINK" \] && \[ -n "\$CSC_KEY_PASSWORD" \][\s\S]*should_sign=1/,
  );
  assert.match(
    releaseWorkflow,
    /CSC_LINK and CSC_KEY_PASSWORD must either both be configured or both be unset/,
  );
  assert.match(
    releaseWorkflow,
    /MACOS_NOTARIZATION_ENABLED: \$\{\{ secrets\.MACOS_NOTARIZATION_ENABLED \}\}/,
  );
  assert.match(releaseWorkflow, /""\|false\)\s*should_notarize=0/);
  assert.match(releaseWorkflow, /true\)\s*should_notarize=1/);
  assert.match(
    releaseWorkflow,
    /OMLORIX_REQUIRE_DESKTOP_SIGNING: \$\{\{ steps\.macos_security\.outputs\.should_sign \|\| '0' \}\}[\s\S]*OMLORIX_REQUIRE_MACOS_NOTARIZATION: \$\{\{ steps\.macos_security\.outputs\.should_notarize \|\| '0' \}\}/,
  );
  assert.match(releaseWorkflow, /macOS notarization requires CSC_LINK and CSC_KEY_PASSWORD/);
  assert.match(
    releaseWorkflow,
    /Check out current macOS release security policy[\s\S]*ref: \$\{\{ github\.sha \}\}[\s\S]*validate-desktop-signing\.mjs/,
  );
  assert.match(
    releaseWorkflow,
    /Apply current macOS release security policy[\s\S]*cp "\.release-security-policy\/electron\/scripts\/\$script" "electron\/scripts\/\$script"/,
  );
  assert.match(
    releaseWorkflow,
    /npm run \$\{\{ matrix\.script \}\} -- -c\.afterSign=electron\/scripts\/notarize\.cjs/,
  );
  assert.match(
    releaseWorkflow,
    /Notarize and staple packaged macOS artifacts[\s\S]*steps\.macos_security\.outputs\.should_notarize == '1'/,
  );
  assert.match(
    releaseWorkflow,
    /Verify macOS desktop artifacts[\s\S]*OMLORIX_REQUIRE_DESKTOP_SIGNING:[\s\S]*OMLORIX_REQUIRE_MACOS_NOTARIZATION:/,
  );
  assert.match(appNotarizationHook, /OMLORIX_REQUIRE_MACOS_NOTARIZATION !== '1'/);
  assert.doesNotMatch(appNotarizationHook, /OMLORIX_REQUIRE_DESKTOP_SIGNING !== '1'/);
  assert.match(dmgNotarizationScript, /OMLORIX_REQUIRE_MACOS_NOTARIZATION !== '1'/);
  assert.match(verificationScript, /if \(signingRequired\)[\s\S]*codesign/);
  assert.match(verificationScript, /Code-signature verification is disabled for unsigned build/);
});

test('unsigned macOS validation does not require certificate credentials', () => {
  const result = runDesktopSigningValidation({
    OMLORIX_REQUIRE_DESKTOP_SIGNING: '0',
    OMLORIX_REQUIRE_MACOS_NOTARIZATION: '0',
  });

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Production signing is not required for this build/);
});

test('signed-only macOS validation needs certificate credentials but not Apple credentials', () => {
  const result = runDesktopSigningValidation({
    OMLORIX_REQUIRE_DESKTOP_SIGNING: '1',
    OMLORIX_REQUIRE_MACOS_NOTARIZATION: '0',
    CSC_LINK: 'test-certificate',
    CSC_KEY_PASSWORD: 'test-password',
  });

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Production signing credentials detected/);
  assert.match(result.stdout, /Apple notarization is disabled for this signed build/);
});

test('enabled macOS notarization requires signing and all Apple credentials', () => {
  const missingAppleCredentials = runDesktopSigningValidation({
    OMLORIX_REQUIRE_DESKTOP_SIGNING: '1',
    OMLORIX_REQUIRE_MACOS_NOTARIZATION: '1',
    CSC_LINK: 'test-certificate',
    CSC_KEY_PASSWORD: 'test-password',
  });
  assert.equal(missingAppleCredentials.status, 1);
  assert.match(missingAppleCredentials.stderr, /APPLE_ID/);
  assert.match(missingAppleCredentials.stderr, /APPLE_APP_SPECIFIC_PASSWORD/);
  assert.match(missingAppleCredentials.stderr, /APPLE_TEAM_ID/);

  const signingDisabled = runDesktopSigningValidation({
    OMLORIX_REQUIRE_DESKTOP_SIGNING: '0',
    OMLORIX_REQUIRE_MACOS_NOTARIZATION: '1',
  });
  assert.equal(signingDisabled.status, 1);
  assert.match(signingDisabled.stderr, /cannot be enabled when production signing is disabled/);

  const fullyConfigured = runDesktopSigningValidation({
    OMLORIX_REQUIRE_DESKTOP_SIGNING: '1',
    OMLORIX_REQUIRE_MACOS_NOTARIZATION: '1',
    CSC_LINK: 'test-certificate',
    CSC_KEY_PASSWORD: 'test-password',
    APPLE_ID: 'developer@example.com',
    APPLE_APP_SPECIFIC_PASSWORD: 'test-app-password',
    APPLE_TEAM_ID: 'TESTTEAMID',
  });
  assert.equal(fullyConfigured.status, 0, fullyConfigured.stderr);
  assert.match(fullyConfigured.stdout, /Apple notarization credentials detected/);
});

test('server releases remain isolated from concurrent main pushes', async () => {
  const releaseWorkflow = await fs.readFile(
    path.join(projectRoot, '.github', 'workflows', 'release.yml'),
    'utf8',
  );
  const releaseWorkflowDocument = yaml.load(releaseWorkflow);

  // The tested release commit is reachable through its immutable tag. It must
  // never be pushed directly over a branch that may have advanced meanwhile.
  assert.match(releaseWorkflow, /--base-tag-prefix v/);
  assert.match(
    releaseWorkflow,
    /git push --atomic "\$repo_url" "refs\/tags\/\$\{RELEASE_TAG\}:refs\/tags\/\$\{RELEASE_TAG\}"/,
  );
  assert.doesNotMatch(
    releaseWorkflow,
    /git push origin HEAD:\$\{\{ github\.ref_name \}\} --follow-tags/,
  );

  // Version synchronization and release-feed publication both retry from the
  // latest remote tip, preserving commits that land during those short writes.
  assert.match(releaseWorkflow, /for attempt in 1 2 3 4 5 6 7 8; do/);
  assert.match(releaseWorkflow, /release-feed changed during attempt/);
  assert.match(releaseWorkflow, /beta-baseline\.json/);
  assert.match(releaseWorkflow, /existing_beta\.get\("fallbackChannel"\) != "stable"/);
  assert.match(releaseWorkflow, /Refreshed the \{refresh_reason\} beta feed from the current stable release/);
  // The feed must describe concrete assets that the workflow creates. Merely
  // mentioning these keys in a validator is not enough to make a release work.
  assert.match(
    releaseWorkflow,
    /"serverBundleUrl": f"\{asset_base\}\/omlorix-server-\{version\}\.tar\.gz"/,
  );
  assert.match(
    releaseWorkflow,
    /"serverBundleSha256Url": f"\{asset_base\}\/omlorix-server-\{version\}\.tar\.gz\.sha256"/,
  );
  assert.match(releaseWorkflow, /Upload server release bundle/);
  assert.match(releaseWorkflow, /sha256sum "\$bundle_name" > "\$bundle_name\.sha256"/);
  assert.match(releaseWorkflow, /released_version = os\.environ\["RELEASE_VERSION"\]/);
  assert.doesNotMatch(
    releaseWorkflow,
    /git show "\$\{RELEASE_TAG\}:\$\{path\}" > "\$path"/,
  );
  const jobs = releaseWorkflowDocument?.jobs;
  assert.ok(jobs && typeof jobs === 'object', 'release workflow must define jobs');
  assert.ok(Object.hasOwn(jobs, 'finalize'), 'release workflow must define the finalize job');
  assert.ok(
    Object.hasOwn(jobs, 'sync-main-version'),
    'release workflow must define the sync-main-version job',
  );
  const synchronizationNeeds = jobs['sync-main-version'].needs;
  assert.ok(Array.isArray(synchronizationNeeds), 'sync-main-version needs must be a job list');
  assert.ok(
    synchronizationNeeds.includes('finalize'),
    'sync-main-version must wait for finalize',
  );
});

test('server release permissions and publication order preserve the staging boundary', async () => {
  const releaseWorkflow = await fs.readFile(
    path.join(projectRoot, '.github', 'workflows', 'release.yml'),
    'utf8',
  );
  const document = yaml.load(releaseWorkflow);
  const jobs = document.jobs;

  assert.deepEqual(document.permissions, { contents: 'read' });
  assert.equal(jobs.publish.permissions.contents, 'write');
  assert.equal(jobs['image-build'].permissions.contents, 'read');
  assert.equal(jobs['image-build'].permissions.packages, 'write');
  assert.equal(jobs.finalize.permissions.contents, 'write');
  assert.equal(jobs.finalize.permissions.packages, 'write');
  assert.equal(jobs['sync-main-version'].permissions.contents, 'write');

  for (const jobName of ['prepare', 'publish', 'image-build', 'sync-main-version']) {
    const checkoutSteps = jobs[jobName].steps.filter((step) =>
      String(step.uses || '').startsWith('actions/checkout@'));
    assert.ok(checkoutSteps.length > 0, `${jobName} must check out release source`);
    for (const step of checkoutSteps) {
      assert.equal(
        step.with?.['persist-credentials'],
        false,
        `${jobName} must not persist a write-capable checkout credential`,
      );
    }
  }

  const verifyDraft = releaseWorkflow.indexOf('- name: Verify the complete draft release payload');
  const prepareFeed = releaseWorkflow.indexOf('- name: Prepare and validate channel feed payload');
  const promoteChannels = releaseWorkflow.indexOf('- name: Promote verified image manifests to the release channel');
  const publishRelease = releaseWorkflow.indexOf('- name: Publish GitHub release');
  assert.ok(verifyDraft !== -1 && verifyDraft < prepareFeed);
  assert.ok(prepareFeed < promoteChannels);
  assert.ok(promoteChannels < publishRelease);

  const manifestJob = JSON.stringify(jobs['image-manifest']);
  const finalizeJob = JSON.stringify(jobs.finalize);
  const verifyDraftStep = jobs.finalize.steps.find(
    (step) => step.name === 'Verify the complete draft release payload',
  );
  assert.doesNotMatch(manifestJob, /IMAGE_CHANNEL/);
  assert.match(finalizeJob, /IMAGE_CHANNEL/);
  assert.match(verifyDraftStep.run, /download-github-draft-assets\.sh/);
  assert.doesNotMatch(verifyDraftStep.run, /^\s*gh release download/m);
  assert.match(releaseWorkflow, /already public and immutable/);
  assert.match(releaseWorkflow, /would move the \{channel\} channel backward/);
});

test('stable launcher releases refresh only placeholder beta feeds', async () => {
  const releaseWorkflow = await fs.readFile(
    path.join(projectRoot, '.github', 'workflows', 'server-launcher-release.yml'),
    'utf8',
  );

  assert.match(releaseWorkflow, /launcher-beta\.json/);
  assert.match(releaseWorkflow, /"fallbackChannel": "stable"/);
  assert.match(releaseWorkflow, /existing_beta\.get\("fallbackChannel"\) == "stable"/);
  assert.match(releaseWorkflow, /electronUpdaterChannel/);
  assert.match(releaseWorkflow, /missing required launcher feed fields/);
});

test('draft release assets use the authenticated paginated API before publication', async () => {
  const draftDownloadScript = await fs.readFile(
    path.join(projectRoot, 'dev_scripts', 'download-github-draft-assets.sh'),
    'utf8',
  );

  assert.doesNotMatch(draftDownloadScript, /^\s*gh release download/m);
  assert.match(draftDownloadScript, /--json apiUrl,isDraft/);
  assert.match(draftDownloadScript, /gh api --paginate/);
  assert.match(draftDownloadScript, /Accept: application\/octet-stream/);
  assert.match(draftDownloadScript, /Refusing unsafe draft release asset name/);
  assert.match(draftDownloadScript, /missing required asset/);
  assert.match(draftDownloadScript, /requested_asset_count="\$#"/);
});

test('draft release downloader supports an empty asset filter with nounset enabled', async () => {
  const scriptPath = path.join(
    projectRoot,
    'dev_scripts',
    'download-github-draft-assets.sh',
  );
  const fixtureRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-draft-assets-test-'));
  const fakeBin = path.join(fixtureRoot, 'bin');
  const downloadDirectory = path.join(fixtureRoot, 'downloads');
  const fakeGh = path.join(fakeBin, 'gh');

  try {
    await fs.mkdir(fakeBin);
    await fs.writeFile(
      fakeGh,
      `#!/usr/bin/env bash
case "$1:$2" in
  release:view)
    printf 'true\\thttps://api.github.test/releases/1\\n'
    ;;
  api:--paginate)
    printf 'fixture.txt\\thttps://api.github.test/assets/1\\n'
    ;;
  api:--header)
    printf 'verified draft payload\\n'
    ;;
  *)
    exit 64
    ;;
esac
`,
      'utf8',
    );
    await fs.chmod(fakeGh, 0o755);

    const result = spawnSync(
      'bash',
      [scriptPath, 'owner/repository', 'v1.0.0', downloadDirectory],
      {
        encoding: 'utf8',
        env: {
          ...process.env,
          PATH: `${fakeBin}${path.delimiter}${process.env.PATH}`,
        },
      },
    );

    assert.equal(result.status, 0, result.stderr);
    assert.equal(
      await fs.readFile(path.join(downloadDirectory, 'fixture.txt'), 'utf8'),
      'verified draft payload\n',
    );
  } finally {
    await fs.rm(fixtureRoot, { recursive: true, force: true });
  }
});

test('launcher releases stage a complete asset set before becoming public', async () => {
  const releaseWorkflow = await fs.readFile(
    path.join(projectRoot, '.github', 'workflows', 'server-launcher-release.yml'),
    'utf8',
  );
  const document = yaml.load(releaseWorkflow);
  const jobs = document.jobs;

  assert.deepEqual(document.permissions, { contents: 'read' });
  assert.equal(jobs.publish.permissions.contents, 'write');
  assert.deepEqual(jobs.desktop.permissions, { contents: 'read' });
  assert.deepEqual(jobs.cli.permissions, { contents: 'read' });
  assert.equal(jobs.upload.permissions.contents, 'write');
  assert.deepEqual(jobs.verify_release_assets.permissions, { contents: 'write' });
  assert.equal(jobs.finalize.permissions.contents, 'write');

  for (const jobName of [
    'prepare',
    'publish',
    'desktop',
    'cli',
    'upload',
    'verify_release_assets',
  ]) {
    const checkoutSteps = jobs[jobName].steps.filter((step) =>
      String(step.uses || '').startsWith('actions/checkout@'));
    assert.ok(checkoutSteps.length > 0, `${jobName} must check out launcher source`);
    for (const step of checkoutSteps) {
      assert.equal(
        step.with?.['persist-credentials'],
        false,
        `${jobName} must not persist checkout credentials while running source code`,
      );
    }
  }

  assert.doesNotMatch(JSON.stringify(jobs.desktop), /gh release upload/);
  assert.doesNotMatch(JSON.stringify(jobs.cli), /gh release upload/);
  assert.match(JSON.stringify(jobs.desktop), /launcher-release-desktop-/);
  assert.match(JSON.stringify(jobs.cli), /launcher-release-cli-/);

  const uploadAssets = releaseWorkflow.indexOf('- name: Verify and upload the complete launcher asset set to the draft');
  const verifyDraftDownload = releaseWorkflow.indexOf('- name: Download and verify draft release assets');
  const publishRelease = releaseWorkflow.indexOf('- name: Publish launcher release');
  const verifyAssetsStep = jobs.upload.steps.find(
    (step) => step.name === 'Verify and upload the complete launcher asset set to the draft',
  );
  assert.ok(uploadAssets !== -1 && uploadAssets < verifyDraftDownload);
  assert.ok(verifyDraftDownload < publishRelease);
  assert.match(
    verifyAssetsStep.run,
    /gh release view "\$\{\{ needs\.publish\.outputs\.tag \}\}" \\\n\s+--repo "\$\{\{ github\.repository \}\}" \\\n\s+--json isDraft/,
  );
  assert.deepEqual(jobs.verify_release_assets.strategy.matrix.include, [
    { runner: 'ubuntu-latest', verifier: 'sha256sum' },
    { runner: 'macos-latest', verifier: 'shasum' },
  ]);
  const verifyDraftStep = jobs.verify_release_assets.steps.at(-1);
  assert.doesNotMatch(verifyDraftStep.run, /^\s*gh release download/m);
  assert.match(verifyDraftStep.run, /download-github-draft-assets\.sh/);
  assert.match(verifyDraftStep.run, /verify-release-checksums\.sh/);
  assert.match(jobs.finalize.if, /needs\.verify_release_assets\.result == 'success'/);
  assert.match(releaseWorkflow, /already public and immutable/);
  assert.match(releaseWorkflow, /release-feed changed during launcher attempt/);
});

test('launcher release checksums remain usable after GitHub asset download', async () => {
  const releaseWorkflow = await fs.readFile(
    path.join(projectRoot, '.github', 'workflows', 'server-launcher-release.yml'),
    'utf8',
  );
  const checksumVerifier = await fs.readFile(
    path.join(projectRoot, 'dev_scripts', 'server-launcher', 'verify-release-checksums.sh'),
    'utf8',
  );
  const aliasStep = releaseWorkflow.indexOf('- name: Create latest desktop download aliases');
  const normalizeStep = releaseWorkflow.indexOf('- name: Normalize desktop release asset filenames');
  const checksumStep = releaseWorkflow.indexOf('- name: Checksum desktop artifacts');

  assert.ok(aliasStep !== -1 && aliasStep < normalizeStep);
  assert.ok(normalizeStep < checksumStep);
  assert.match(releaseWorkflow, /normalized_name="\$\{artifact_name\/\/ \/-\}"/);
  assert.match(releaseWorkflow, /output_name="\$\(basename "\$output"\)"/);
  assert.match(releaseWorkflow, /\(cd dist && sha256sum "\$output_name" > "\$output_name\.sha256"\)/);
  assert.match(checksumVerifier, /recorded_name" != "\$expected_name/);
  assert.match(checksumVerifier, /Checksum entry must not contain a path/);
  assert.match(checksumVerifier, /sha256sum --check/);
  assert.match(checksumVerifier, /shasum -a 256 -c/);
});

test('release checksum verifier accepts a portable entry and rejects an embedded path', async () => {
  const verifierPath = path.join(
    projectRoot,
    'dev_scripts',
    'server-launcher',
    'verify-release-checksums.sh',
  );
  const artifactDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-checksum-test-'));
  const artifactName = 'omlorix-server-cli-darwin-arm64';
  const artifact = Buffer.from('portable release checksum fixture\n');
  const digest = crypto.createHash('sha256').update(artifact).digest('hex');
  const checksumPath = path.join(artifactDirectory, `${artifactName}.sha256`);
  const verifier = process.platform === 'darwin' ? 'shasum' : 'sha256sum';

  try {
    await fs.writeFile(path.join(artifactDirectory, artifactName), artifact);
    await fs.writeFile(checksumPath, `${digest}  ${artifactName}\n`, 'utf8');

    const portableResult = spawnSync(
      'bash',
      [verifierPath, artifactDirectory, verifier],
      { encoding: 'utf8' },
    );
    assert.equal(portableResult.status, 0, portableResult.stderr);

    await fs.writeFile(checksumPath, `${digest}  dist/${artifactName}\n`, 'utf8');
    const pathBearingResult = spawnSync(
      'bash',
      [verifierPath, artifactDirectory, verifier],
      { encoding: 'utf8' },
    );
    assert.notEqual(pathBearingResult.status, 0);
    assert.match(pathBearingResult.stderr, /must name its release asset basename/);
  } finally {
    await fs.rm(artifactDirectory, { recursive: true, force: true });
  }
});

test('launcher rereleases enforce channel semantics and monotonicity', async () => {
  const releaseWorkflow = await fs.readFile(
    path.join(projectRoot, '.github', 'workflows', 'server-launcher-release.yml'),
    'utf8',
  );

  assert.match(releaseWorkflow, /Beta rereleases require a beta target tag/);
  assert.match(releaseWorkflow, /Stable rereleases cannot target a beta launcher release tag/);
  assert.match(releaseWorkflow, /Reject stale launcher rerelease targets/);
  assert.match(releaseWorkflow, /would move the \{channel\} launcher channel backward/);
  assert.match(releaseWorkflow, /--base-tag-prefix "\$\{\{ env\.LAUNCHER_RELEASE_PREFIX \}\}"/);
});

test('Dependabot only declares package directories with matching manifests', async () => {
  const dependabot = yaml.load(
    await fs.readFile(path.join(projectRoot, '.github', 'dependabot.yml'), 'utf8'),
  );
  const npmDirectories = dependabot.updates
    .filter((entry) => entry['package-ecosystem'] === 'npm')
    .map((entry) => entry.directory);

  assert.deepEqual(npmDirectories, ['/']);
});

test('Windows CLI signing stages one final artifact set', async () => {
  const releaseWorkflow = await fs.readFile(
    path.join(projectRoot, '.github', 'workflows', 'server-launcher-release.yml'),
    'utf8',
  );
  const releaseWorkflowDocument = yaml.load(releaseWorkflow);
  const signingJob = releaseWorkflowDocument?.jobs?.windows_cli_signing;

  assert.match(releaseWorkflow, /windows_cli_signing:[\s\S]*runs-on: windows-latest/);
  assert.match(releaseWorkflow, /WINDOWS_CODESIGN_PFX_BASE64: \$\{\{ secrets\.WINDOWS_CODESIGN_PFX_BASE64 \}\}/);
  assert.match(releaseWorkflow, /signtool\.exe[\s\S]*sign \/fd SHA256[\s\S]*verify \/pa \/all \/v/);
  assert.match(releaseWorkflow, /Get-AuthenticodeSignature[\s\S]*Status -ne 'Valid'/);
  assert.match(releaseWorkflow, /needs\.windows_cli_signing\.result == 'success'/);
  assert.match(releaseWorkflow, /launcher-candidate-cli-windows-amd64/);
  assert.match(releaseWorkflow, /launcher-release-cli-windows-amd64/);
  assert.ok(signingJob, 'release workflow must define the Windows CLI signing job');
  assert.ok(Array.isArray(signingJob.steps), 'Windows CLI signing job must define steps');
  assert.ok(signingJob.steps.length > 0, 'Windows CLI signing job must not be empty');
  const signingStep = signingJob.steps.find((step) => step.name === 'Sign and verify the Windows CLI');
  const finalArtifactStep = signingJob.steps.find((step) => step.name === 'Stage final Windows CLI artifacts');
  assert.equal(signingStep.if, "${{ env.WINDOWS_CODESIGN_PFX_BASE64 != '' }}");
  assert.equal(finalArtifactStep.if, undefined);
  assert.match(finalArtifactStep.uses, /^actions\/upload-artifact@/);
});
