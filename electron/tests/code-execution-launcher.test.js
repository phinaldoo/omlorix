const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const repositoryRoot = path.join(__dirname, '..', '..');

test('Code Execution has an independent accessible launcher page and dialogs', async () => {
  const html = await fs.readFile(path.join(repositoryRoot, 'electron/renderer/launcher.html'), 'utf8');
  assert.match(html, /data-section="code-execution"/);
  assert.match(html, /id="code-execution"[^>]*aria-labelledby="codeExecutionTitle"/);
  assert.match(html, /id="codeExecutionEditorOverlay"[\s\S]*role="dialog"[\s\S]*aria-modal="true"/);
  assert.match(html, /id="codeExecutionLogsOverlay"[\s\S]*role="dialog"[\s\S]*aria-modal="true"/);
  assert.match(html, /id="codeExecutionEditorError"[^>]*role="alert"[^>]*tabindex="-1"/);
  assert.match(html, /id="codeExecutionVersionSelect"[\s\S]*aria-describedby="codeExecutionVersionStatus"/);
  assert.match(html, /id="codeExecutionCustomVersionField"[^>]*hidden/);
  assert.doesNotMatch(html, /id="codeExecutionVersionInput"/);
  assert.doesNotMatch(html, /field field-full code-execution-version-help/);
  assert(
    html.indexOf('id="codeExecutionVersionSelect"')
      < html.indexOf('id="codeExecutionVersionStatus"'),
    'version help must remain inside and after the Version control',
  );
  assert(
    html.indexOf('id="codeExecutionVersionStatus"')
      < html.indexOf('id="codeExecutionPortInput"'),
    'version help must not become a separate grid row beside the health port',
  );
  assert.doesNotMatch(html, /onclick=/);
});

test('launcher manager IPC is isolated behind the trusted preload bridge', async () => {
  const [main, preload] = await Promise.all([
    fs.readFile(path.join(repositoryRoot, 'electron/main.js'), 'utf8'),
    fs.readFile(path.join(repositoryRoot, 'electron/preload.js'), 'utf8'),
  ]);
  for (const action of ['list', 'get-available-versions', 'create', 'save', 'start', 'stop', 'restart', 'logs', 'remove']) {
    assert.match(main, new RegExp(`code-execution:${action}`));
    assert.match(preload, new RegExp(`code-execution:${action}`));
  }
  assert.match(main, /clipboard\.writeText\(JSON\.stringify\(payload, null, 2\)\)/);
  assert.match(main, /const editorResult = async \(operation\)/);
  assert.match(main, /editorErrorCodes\.has\(code\)/);
  assert.match(preload, /invokeCodeExecutionEditor\('code-execution:create', payload\)/);
  assert.match(preload, /error\.code = String\(result\?\.error\?\.code/);
});

test('Code Execution version picker loads releases and preserves explicit pinning', async () => {
  const source = await fs.readFile(
    path.join(repositoryRoot, 'electron/renderer/code-execution-page.js'),
    'utf8',
  );
  assert.match(source, /api\.getAvailableVersions\(\)/);
  assert.match(source, /imageSource/);
  assert.match(source, /versionOptionValue\('release', latestVersion\)/);
  assert.doesNotMatch(source, /Local checkout/);
  assert.match(source, /CUSTOM_VERSION_VALUE/);
  assert.match(source, /VERSION_PATTERN\.test\(value\)/);
  assert.match(source, /elements\.name\.value = instance\?\.name \|\| tr\('Local Code Execution'\)/);
  assert.match(source, /Could not load published releases\. Enter a version manually or retry\./);
  assert.match(source, /function showEditorError\(error\)/);
  assert.match(source, /error\?\.code === 'PORT_IN_USE'/);
  assert.match(source, /elements\.port\.focus\(\)/);
  assert.match(source, /That gateway port is already assigned to another managed instance\./);
  for (const [code, message] of [
    ['NAME_REQUIRED', 'Enter a name for the Code Execution service.'],
    ['VERSION_INVALID', 'Enter a semantic Code Execution version such as 0.9.0.'],
    ['MEMORY_INVALID', 'Choose a supported sandbox memory limit.'],
    ['IMAGE_SOURCE_INVALID', 'Choose a supported Code Execution image source.'],
    ['SOURCE_MISSING', 'The local Code Execution source checkout could not be found.'],
    ['INSTANCE_NOT_FOUND', 'The Code Execution instance was not found.'],
    ['SECRET_MISSING', 'The Code Execution API key is missing.'],
  ]) {
    assert.match(source, new RegExp(`\\['${code}', '${message.replace(/[.*+?^${}()|[\\]\\]/g, '\\$&')}'\\]`));
  }
  assert.match(source, /if \(state\.versionLoading\)[\s\S]*Loading published Code Execution releases/);
  assert.match(source, /if \(!validateCustomVersion\(\)\)[\s\S]*customVersion\.reportValidity\(\)/);
});

test('launcher bundles the helper Compose definition, proxy license, and private Omlorix network overlay', async () => {
  const [manifest, helperCompose, proxyConfig, proxyLicense, notices, overlay, css] = await Promise.all([
    fs.readFile(path.join(repositoryRoot, 'package.json'), 'utf8'),
    fs.readFile(path.join(repositoryRoot, 'electron/code-execution/docker-compose.yml'), 'utf8'),
    fs.readFile(path.join(repositoryRoot, 'electron/code-execution/haproxy.cfg'), 'utf8'),
    fs.readFile(
      path.join(repositoryRoot, 'electron/code-execution/TECNATIVA_DOCKER_SOCKET_PROXY_LICENSE.txt'),
      'utf8',
    ),
    fs.readFile(path.join(repositoryRoot, 'THIRD_PARTY_NOTICES.md'), 'utf8'),
    fs.readFile(path.join(repositoryRoot, 'docker-compose.launcher-services.yml'), 'utf8'),
    fs.readFile(path.join(repositoryRoot, 'electron/renderer/launcher.css'), 'utf8'),
  ]);
  assert.match(manifest, /code-execution-bundle/);
  assert.match(manifest, /TECNATIVA_DOCKER_SOCKET_PROXY_LICENSE\.txt/);
  assert.match(manifest, /THIRD_PARTY_NOTICES\.md/);
  assert.match(helperCompose, /name: omlorix-launcher-services/);
  assert.match(helperCompose, /GATEWAY_HOST_BIND:-127\.0\.0\.1/);
  assert.match(helperCompose, /docker-socket-proxy/);
  assert.match(helperCompose, /docker-socket-proxy:v0\.4\.2@sha256:/);
  assert.match(helperCompose, /\.\/haproxy\.cfg:\/usr\/local\/etc\/haproxy\/haproxy\.cfg\.template:ro/);
  assert.match(helperCompose, /EVENTS: "0"/);
  assert.match(helperCompose, /redis:8\.8\.1-alpine@sha256:8096655e/);
  assert.match(helperCompose, /REDIS_SOCKET_CONNECT_TIMEOUT/);
  assert.match(helperCompose, /MAX_EXECUTIONS_PER_SESSION/);
  assert.match(helperCompose, /RENDER_MAX_TOTAL_ASSET_BYTES/);
  assert.match(helperCompose, /SANDBOX_ENV_SOURCE_PATH: \/etc\/code-execution\/\.env_sandbox/);
  assert.match(helperCompose, /DOCKER_HOST: \$\{GATEWAY_DOCKER_HOST:-tcp:\/\/docker-proxy:2375\}/);
  assert.doesNotMatch(helperCompose, /profiles:[\s\S]{0,80}local-docker/);
  assert.doesNotMatch(helperCompose, /build:\s*\n\s+context: \.\/gateway/);
  assert.match(proxyConfig, /timeout connect 10s/);
  assert.match(proxyConfig, /timeout client 10m/);
  assert.match(proxyConfig, /backend docker-events[\s\S]*timeout server 30s/);
  assert.doesNotMatch(proxyConfig, /timeout server 0/);
  assert.match(proxyConfig, /Modified for Omlorix:/);
  assert.match(proxyConfig, /TECNATIVA_DOCKER_SOCKET_PROXY_LICENSE\.txt/);
  assert.match(proxyLicense, /Apache License\s+Version 2\.0, January 2004/);
  assert.match(proxyLicense, /4\. Redistribution\./);
  assert.match(notices, /Tecnativa docker-socket-proxy v0\.4\.2/);
  assert.match(notices, /do not apply to original Omlorix code or assets/);
  assert.match(overlay, /launcher-services/);
  assert.match(overlay, /fastapi:/);
  assert.match(css, /\.code-execution-notice\s*\{[\s\S]*?border-radius:\s*var\(--radius\)/);
});

test('Omlorix Service Connections can securely paste a launcher handoff', async () => {
  const source = await fs.readFile(
    path.join(repositoryRoot, 'frontend/js/admin/serviceConnections.js'),
    'utf8',
  );
  assert.match(source, /serviceConnectionsImportLauncherButton/);
  assert.match(source, /navigator\.clipboard\.readText\(\)/);
  assert.match(source, /typeof payload\.api_key !== 'string'/);
  assert.match(source, /serviceConnectionApiKeyInput'\)\.value = payload\.api_key/);
});
