const assert = require('node:assert/strict');
const { execFile } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const https = require('node:https');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { promisify } = require('node:util');

const { LauncherReverseProxy, normalizeProxyConfig } = require('../server-proxy');

const execFileAsync = promisify(execFile);
const REPOSITORY_ROOT = path.resolve(__dirname, '..', '..');
const NGINX_IMAGE = 'nginx:1.27-alpine';
const LAUNCHER_SECRET = 'a'.repeat(64);

async function dockerAvailable() {
  try {
    await execFileAsync('docker', ['info'], { timeout: 5000 });
    // Avoid turning the ordinary desktop suite into an implicit network pull.
    await execFileAsync('docker', ['image', 'inspect', NGINX_IMAGE], { timeout: 5000 });
    return true;
  } catch {
    return false;
  }
}

async function waitForHttp(url) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // Container startup is asynchronous; retry within the fixed deadline.
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error('nginx did not become ready');
}

async function unusedPort() {
  const server = http.createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return String(port);
}

function httpsRequest(url) {
  return new Promise((resolve, reject) => {
    https.get(url, {
      rejectUnauthorized: false,
      headers: { Host: 'chat.example.test' },
    }, (response) => {
      response.resume();
      response.on('end', () => resolve(response));
    }).on('error', reject);
  });
}

test('nginx isolates authenticated visitor rate buckets and logs one normalized identity', async (context) => {
  if (!(await dockerAvailable())) {
    context.skip('Docker and the pinned nginx image are required for this integration check');
    return;
  }

  const temporaryDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-nginx-ip-'));
  const renderedConfig = path.join(temporaryDirectory, 'default.conf');
  const containerName = `omlorix-nginx-ip-${crypto.randomBytes(6).toString('hex')}`;
  try {
    await execFileAsync(
      path.join(REPOSITORY_ROOT, 'nginx', 'bin', 'render-forwarded-for.sh'),
      [
        path.join(REPOSITORY_ROOT, 'nginx', 'default.http.conf.template', 'default.conf'),
        renderedConfig,
      ],
      {
        env: {
          ...process.env,
          FRONTEND_TRUST_PROXY_HEADERS: 'true',
          OMLORIX_LAUNCHER_PROXY_SECRET: LAUNCHER_SECRET,
          FRONTEND_TRUSTED_UPSTREAMS: '',
        },
      },
    );
    await execFileAsync('docker', [
      'run', '--rm', '-d', '--name', containerName,
      '--add-host', 'fastapi:127.0.0.1',
      '-p', '127.0.0.1::80',
      '-v', `${renderedConfig}:/etc/nginx/conf.d/default.conf:ro`,
      NGINX_IMAGE,
    ]);
    const { stdout: portOutput } = await execFileAsync('docker', ['port', containerName, '80/tcp']);
    const port = portOutput.trim().match(/:(\d+)$/)?.[1];
    assert.ok(port, `Could not parse Docker port output: ${portOutput}`);
    const origin = `http://127.0.0.1:${port}`;
    await waitForHttp(origin);

    const requestFor = (visitorIp, requestPath = '/api/not-found') => fetch(`${origin}${requestPath}`, {
      headers: {
        'X-Omlorix-Launcher-Secret': LAUNCHER_SECRET,
        'X-Forwarded-For': visitorIp,
      },
    });

    const requestStatusesFor = (visitorIps) => Promise.all(visitorIps.map(async (visitorIp) => {
      const response = await requestFor(visitorIp);
      await response.arrayBuffer();
      return response.status;
    }));
    const rejectionCount = (statuses) => statuses.filter((status) => status === 503).length;
    const rateLimitWaveSize = 150;
    const sharedVisitorWave = Array(rateLimitWaveSize).fill('198.51.100.10');

    // Each wave stays below the configured 160-connection cap, while repeated
    // waves can deterministically exceed the 100r/s rate and 200-request burst.
    const initialSharedStatuses = await requestStatusesFor(sharedVisitorWave);
    assert.equal(rejectionCount(initialSharedStatuses), 0, 'a sub-burst wave should remain allowed');

    let sharedBucketRejections = 0;
    for (let waveIndex = 1; waveIndex < 8 && sharedBucketRejections === 0; waveIndex += 1) {
      sharedBucketRejections += rejectionCount(await requestStatusesFor(sharedVisitorWave));
    }
    const separateStatuses = await requestStatusesFor(Array.from(
      { length: rateLimitWaveSize },
      (_, requestIndex) => `198.51.100.${requestIndex + 20}`,
    ));
    const separateBucketRejections = rejectionCount(separateStatuses);
    assert.ok(sharedBucketRejections > 0, 'one visitor should exhaust its own burst allowance');
    assert.equal(separateBucketRejections, 0, 'independent visitors must not share a rate bucket');

    await fetch(`${origin}/probe-direct`, { headers: { 'X-Forwarded-For': '198.51.100.200' } });
    await fetch(`${origin}/probe-wrong-secret`, {
      headers: {
        'X-Omlorix-Launcher-Secret': 'b'.repeat(64),
        'X-Forwarded-For': '198.51.100.202',
      },
    });
    await fetch(`${origin}/probe-malformed`, {
      headers: {
        'X-Omlorix-Launcher-Secret': LAUNCHER_SECRET,
        'X-Forwarded-For': '198.51.100.203, 198.51.100.204',
      },
    });
    await requestFor('198.51.100.201', '/probe-authenticated');
    const { stdout: logs } = await execFileAsync('docker', ['logs', '--tail', '4', containerName]);
    const accessLines = logs.trim().split('\n');
    assert.equal(accessLines.length, 4);
    assert.match(accessLines[0], /^172\./, 'direct ingress must ignore a spoofed forwarding header');
    assert.match(accessLines[1], /^172\./, 'the wrong launcher credential must fail closed');
    assert.match(accessLines[2], /^172\./, 'a forwarded chain must fail the single-address contract');
    assert.match(accessLines[3], /^198\.51\.100\.201 /, 'authenticated ingress must log the visitor');
  } finally {
    await execFileAsync('docker', ['stop', containerName], { timeout: 10000 }).catch(() => {});
    await fs.rm(temporaryDirectory, { recursive: true, force: true });
  }
});

test('HTTP and HTTPS retain visitor metadata through Launcher and nginx', async (context) => {
  if (!(await dockerAvailable())) {
    context.skip('Docker and the pinned nginx image are required for this integration check');
    return;
  }

  const temporaryDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-nginx-https-'));
  const renderedConfig = path.join(temporaryDirectory, 'frontend.conf');
  const backendConfig = path.join(temporaryDirectory, 'backend.conf');
  const certificatePath = path.join(temporaryDirectory, 'certificate.pem');
  const keyPath = path.join(temporaryDirectory, 'private-key.pem');
  const suffix = crypto.randomBytes(6).toString('hex');
  const networkName = `omlorix-nginx-https-${suffix}`;
  const frontendContainer = `omlorix-nginx-frontend-${suffix}`;
  const backendContainer = `omlorix-nginx-backend-${suffix}`;
  const launcherProxy = new LauncherReverseProxy({ logger: { error() {} } });

  try {
    try {
      await execFileAsync('openssl', [
        'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-subj', '/CN=localhost', '-keyout', keyPath, '-out', certificatePath,
      ]);
    } catch {
      context.skip('OpenSSL is required for the focused HTTPS path check');
      return;
    }
    await execFileAsync(
      path.join(REPOSITORY_ROOT, 'nginx', 'bin', 'render-forwarded-for.sh'),
      [
        path.join(REPOSITORY_ROOT, 'nginx', 'default.http.conf.template', 'default.conf'),
        renderedConfig,
      ],
      {
        env: {
          ...process.env,
          FRONTEND_TRUST_PROXY_HEADERS: 'true',
          OMLORIX_LAUNCHER_PROXY_SECRET: LAUNCHER_SECRET,
          FRONTEND_TRUSTED_UPSTREAMS: '',
        },
      },
    );
    await fs.writeFile(backendConfig, `server {
  listen 8000;
  access_log off;
  location / {
    add_header X-Observed-Scheme $http_x_forwarded_proto always;
    add_header X-Observed-Client $http_x_forwarded_for always;
    return 200 "ok";
  }
}\n`);
    await execFileAsync('docker', ['network', 'create', networkName]);
    await execFileAsync('docker', [
      'run', '--rm', '-d', '--name', backendContainer,
      '--network', networkName, '--network-alias', 'fastapi',
      '-v', `${backendConfig}:/etc/nginx/conf.d/default.conf:ro`,
      NGINX_IMAGE,
    ]);
    await execFileAsync('docker', [
      'run', '--rm', '-d', '--name', frontendContainer,
      '--network', networkName,
      '-p', '127.0.0.1::80',
      '-v', `${renderedConfig}:/etc/nginx/conf.d/default.conf:ro`,
      NGINX_IMAGE,
    ]);
    const { stdout: portOutput } = await execFileAsync('docker', ['port', frontendContainer, '80/tcp']);
    const frontendPort = portOutput.trim().match(/:(\d+)$/)?.[1];
    assert.ok(frontendPort, `Could not parse Docker port output: ${portOutput}`);
    await waitForHttp(`http://127.0.0.1:${frontendPort}/ready`);

    const httpPort = await unusedPort();
    const httpsPort = await unusedPort();
    await launcherProxy.start({
      ...normalizeProxyConfig({}),
      enabled: true,
      bindHost: '127.0.0.1',
      httpPort,
      httpsEnabled: true,
      httpsPort,
      tlsCertPath: certificatePath,
      tlsKeyPath: keyPath,
      launcherSecret: LAUNCHER_SECRET,
      publicHostname: 'chat.example.test',
      target: `http://127.0.0.1:${frontendPort}`,
    });

    const httpResponse = await fetch(`http://127.0.0.1:${httpPort}/api/not-found`, {
      headers: { Host: 'chat.example.test' },
    });
    assert.equal(httpResponse.status, 200);
    assert.equal(httpResponse.headers.get('x-observed-scheme'), 'http');
    assert.equal(httpResponse.headers.get('x-observed-client'), '127.0.0.1');

    const httpsResponse = await httpsRequest(`https://127.0.0.1:${httpsPort}/api/not-found`);
    assert.equal(httpsResponse.statusCode, 200);
    assert.equal(httpsResponse.headers['x-observed-scheme'], 'https');
    assert.equal(httpsResponse.headers['x-observed-client'], '127.0.0.1');
  } finally {
    await launcherProxy.stop();
    await execFileAsync('docker', ['rm', '-f', frontendContainer, backendContainer], { timeout: 10000 }).catch(() => {});
    await execFileAsync('docker', ['network', 'rm', networkName], { timeout: 10000 }).catch(() => {});
    await fs.rm(temporaryDirectory, { recursive: true, force: true });
  }
});
