const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const http = require('http');
const https = require('https');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  LauncherReverseProxy,
  normalizeProxyConfig,
  validateProxyConfig,
} = require('./server-proxy');

const LAUNCHER_SECRET = 'a'.repeat(64);

function listen(server, host = '127.0.0.1') {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, host, () => resolve(server.address().port));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(() => resolve()));
}

function requestText(url, options = {}) {
  return new Promise((resolve, reject) => {
    http.get(url, options, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => {
        body += chunk;
      });
      res.on('end', () => resolve({ statusCode: res.statusCode, headers: res.headers, body }));
    }).on('error', reject);
  });
}

function requestHttpsText(url) {
  return new Promise((resolve, reject) => {
    https.get(url, { rejectUnauthorized: false }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve({ statusCode: res.statusCode, body }));
    }).on('error', reject);
  });
}

function requestUpgradeText(port, path = '/ws', extraHeaders = []) {
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, '127.0.0.1');
    let body = '';
    let settled = false;

    const finish = () => {
      if (settled) return;
      settled = true;
      resolve(body);
    };

    socket.setEncoding('utf8');
    socket.on('connect', () => {
      socket.write([
        `GET ${path} HTTP/1.1`,
        'Host: 127.0.0.1',
        'Connection: Upgrade',
        'Upgrade: websocket',
        'Sec-WebSocket-Version: 13',
        'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==',
        ...extraHeaders,
        '',
        '',
      ].join('\r\n'));
    });
    socket.on('data', (chunk) => {
      body += chunk;
    });
    socket.on('end', finish);
    socket.on('close', finish);
    socket.on('error', reject);
  });
}

function openUpgradeSocket(port, path = '/ws') {
  const socket = net.connect(port, '127.0.0.1');
  let body = '';
  let connectedResolve;
  let readyResolve;

  const connected = new Promise((resolve, reject) => {
    connectedResolve = resolve;
    socket.on('error', reject);
  });

  const ready = new Promise((resolve, reject) => {
    readyResolve = resolve;
    socket.on('error', reject);
  });

  const closed = new Promise((resolve, reject) => {
    socket.on('close', resolve);
    socket.on('error', reject);
  });

  socket.setEncoding('utf8');
  socket.on('connect', () => {
    connectedResolve();
    socket.write([
      `GET ${path} HTTP/1.1`,
      'Host: 127.0.0.1',
      'Connection: Upgrade',
      'Upgrade: websocket',
      'Sec-WebSocket-Version: 13',
      'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==',
      '',
      '',
    ].join('\r\n'));
  });
  socket.on('data', (chunk) => {
    body += chunk;
    if (body.includes('101 Switching Protocols')) {
      readyResolve();
    }
  });

  return {
    socket,
    connected,
    ready,
    closed,
    response: () => body,
  };
}

test('normalizeProxyConfig builds a safe default proxy target and public URL', () => {
  const config = normalizeProxyConfig({
    FRONTEND_HTTP_HOST_PORT: '9080',
    OMLORIX_LAUNCHER_PROXY_ENABLED: 'true',
    OMLORIX_LAUNCHER_PROXY_BIND: '0.0.0.0',
    OMLORIX_LAUNCHER_PROXY_HTTP_PORT: '9090',
  });

  assert.equal(config.enabled, true);
  assert.equal(config.autostart, true);
  assert.equal(config.autostartExplicit, false);
  assert.equal(config.target, 'http://127.0.0.1:9080');
  assert.equal(config.publicUrl, 'http://localhost:9090');
});

test('normalizeProxyConfig preserves an explicit manual-start preference', () => {
  const config = normalizeProxyConfig({
    OMLORIX_LAUNCHER_PROXY_ENABLED: 'false',
    OMLORIX_LAUNCHER_PROXY_AUTOSTART: 'false',
  });

  assert.equal(config.enabled, false);
  assert.equal(config.autostart, false);
  assert.equal(config.autostartExplicit, true);
});

test('validateProxyConfig requires certificate and key paths for HTTPS', () => {
  const errors = validateProxyConfig(normalizeProxyConfig({
    OMLORIX_LAUNCHER_PROXY_ENABLED: 'true',
    OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED: 'true',
  }));

  assert.match(errors.tlsCertPath, /certificate file is required/i);
  assert.match(errors.tlsKeyPath, /private key file is required/i);
});

test('validateProxyConfig rejects HTTPS collisions with the Docker target', () => {
  const errors = validateProxyConfig(normalizeProxyConfig({
    FRONTEND_HTTP_HOST_PORT: '8443',
    OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED: 'true',
    OMLORIX_LAUNCHER_PROXY_HTTPS_PORT: '8443',
    OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH: '/tmp/cert.pem',
    OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH: '/tmp/key.pem',
  }));

  assert.match(errors.httpsPort, /different from the Omlorix Docker port/i);
});

test('tlsOptions appends the optional CA chain to the served certificate chain', async (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'omlorix-proxy-chain-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const certPath = path.join(directory, 'cert.pem');
  const keyPath = path.join(directory, 'key.pem');
  const caPath = path.join(directory, 'ca.pem');
  fs.writeFileSync(certPath, 'LEAF CERTIFICATE\n');
  fs.writeFileSync(keyPath, 'PRIVATE KEY\n');
  fs.writeFileSync(caPath, 'INTERMEDIATE CERTIFICATE\n');
  const proxy = new LauncherReverseProxy({ logger: { error() {} } });

  const options = await proxy.tlsOptions({
    tlsCertPath: certPath,
    tlsKeyPath: keyPath,
    tlsCaPath: caPath,
    tlsKeyPassphrase: '',
  });

  assert.equal(options.cert.toString('utf8'), 'LEAF CERTIFICATE\nINTERMEDIATE CERTIFICATE\n');
  assert.equal(options.ca, undefined);
});

test('LauncherReverseProxy forwards HTTP requests without leaking upstream errors', async () => {
  let observedHeaders = null;
  const upstream = http.createServer((req, res) => {
    observedHeaders = req.headers;
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end(`upstream:${req.url}`);
  });
  const upstreamPort = await listen(upstream);

  const portProbe = http.createServer();
  const proxyPort = await listen(portProbe);
  await close(portProbe);

  const proxy = new LauncherReverseProxy({
    logger: {
      error() {},
    },
  });

  try {
    await proxy.start({
      ...normalizeProxyConfig({}),
      enabled: true,
      bindHost: '127.0.0.1',
      httpPort: String(proxyPort),
      target: `http://127.0.0.1:${upstreamPort}`,
      launcherSecret: LAUNCHER_SECRET,
      publicHostname: 'chat.example.test',
    });

    const response = await requestText(
      `http://127.0.0.1:${proxyPort}/ready?probe=1`,
      {
        headers: {
          Host: 'chat.example.test',
          'X-Forwarded-For': '203.0.113.66',
          'X-Real-IP': '203.0.113.67',
          'X-Forwarded-Host': 'attacker.example',
          Forwarded: 'for=203.0.113.68;proto=https',
        },
      },
    );
    assert.equal(response.statusCode, 200);
    assert.equal(response.body, 'upstream:/ready?probe=1');
    assert.equal(observedHeaders.host, 'chat.example.test');
    assert.equal(observedHeaders['x-forwarded-host'], 'chat.example.test');
    assert.equal(observedHeaders['x-forwarded-for'], '127.0.0.1');
    assert.equal(observedHeaders['x-real-ip'], '127.0.0.1');
    assert.equal(observedHeaders['x-forwarded-proto'], 'http');
    assert.equal(observedHeaders['x-omlorix-launcher-secret'], LAUNCHER_SECRET);
    assert.equal(observedHeaders['x-omlorix-verification-nonce'], '');
    assert.equal(observedHeaders['x-omlorix-proxy-verification'], undefined);
    assert.equal(observedHeaders.forwarded, undefined);
  } finally {
    await proxy.stop();
    await close(upstream);
  }
});

test('LauncherReverseProxy preserves the public HTTPS scheme for nginx', async (t) => {
  const tlsDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'omlorix-proxy-tls-'));
  t.after(() => fs.rmSync(tlsDirectory, { recursive: true, force: true }));
  const certPath = path.join(tlsDirectory, 'certificate.pem');
  const keyPath = path.join(tlsDirectory, 'private-key.pem');
  try {
    execFileSync('openssl', [
      'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
      '-subj', '/CN=localhost', '-keyout', keyPath, '-out', certPath,
    ], { stdio: 'ignore' });
  } catch {
    t.skip('OpenSSL is unavailable for the focused HTTPS listener test.');
    return;
  }

  let observedScheme = '';
  const upstream = http.createServer((req, res) => {
    observedScheme = req.headers['x-forwarded-proto'];
    res.end('secure');
  });
  const upstreamPort = await listen(upstream);
  const httpsProbe = http.createServer();
  const httpsPort = await listen(httpsProbe);
  await close(httpsProbe);
  const httpProbe = http.createServer();
  const httpPort = await listen(httpProbe);
  await close(httpProbe);
  const proxy = new LauncherReverseProxy({ logger: { error() {} } });

  try {
    await proxy.start({
      ...normalizeProxyConfig({}),
      enabled: true,
      bindHost: '127.0.0.1',
      httpPort: String(httpPort),
      httpsEnabled: true,
      httpsPort: String(httpsPort),
      tlsCertPath: certPath,
      tlsKeyPath: keyPath,
      launcherSecret: LAUNCHER_SECRET,
      target: `http://127.0.0.1:${upstreamPort}`,
    });
    const response = await requestHttpsText(`https://127.0.0.1:${httpsPort}/ready`);
    assert.equal(response.statusCode, 200);
    assert.equal(response.body, 'secure');
    assert.equal(observedScheme, 'https');
  } finally {
    await proxy.stop();
    await close(upstream);
  }
});

test('LauncherReverseProxy returns a 502 upgrade response when the upstream is unavailable', async () => {
  const upstreamProbe = http.createServer();
  const upstreamPort = await listen(upstreamProbe);
  await close(upstreamProbe);

  const portProbe = http.createServer();
  const proxyPort = await listen(portProbe);
  await close(portProbe);

  const proxy = new LauncherReverseProxy({
    logger: {
      error() {},
    },
  });

  try {
    await proxy.start({
      ...normalizeProxyConfig({}),
      enabled: true,
      bindHost: '127.0.0.1',
      httpPort: String(proxyPort),
      target: `http://127.0.0.1:${upstreamPort}`,
      launcherSecret: LAUNCHER_SECRET,
    });

    const response = await requestUpgradeText(proxyPort);
    assert.match(response, /HTTP\/1\.1 502 Bad Gateway/);
    assert.match(response, /Omlorix proxy upstream is unavailable/);
    assert.match(response, /"error":/);
  } finally {
    await proxy.stop();
  }
});

test('LauncherReverseProxy overwrites spoofed forwarding headers for WebSocket upgrades', async () => {
  let observedHeaders = null;
  const upstream = http.createServer();
  upstream.on('upgrade', (req, socket) => {
    observedHeaders = req.headers;
    socket.end([
      'HTTP/1.1 101 Switching Protocols',
      'Connection: Upgrade',
      'Upgrade: websocket',
      '',
      '',
    ].join('\r\n'));
  });
  const upstreamPort = await listen(upstream);

  const portProbe = http.createServer();
  const proxyPort = await listen(portProbe);
  await close(portProbe);

  const proxy = new LauncherReverseProxy({
    logger: {
      error() {},
    },
  });

  try {
    await proxy.start({
      ...normalizeProxyConfig({}),
      enabled: true,
      bindHost: '127.0.0.1',
      httpPort: String(proxyPort),
      target: `http://127.0.0.1:${upstreamPort}`,
      launcherSecret: LAUNCHER_SECRET,
    });

    const response = await requestUpgradeText(
      proxyPort,
      '/ws',
      [
        'X-Forwarded-For: 203.0.113.66',
        'X-Real-IP: 203.0.113.67',
        'Forwarded: for=203.0.113.68;proto=https',
      ],
    );

    assert.match(response, /101 Switching Protocols/);
    assert.equal(observedHeaders['x-forwarded-for'], '127.0.0.1');
    assert.equal(observedHeaders['x-real-ip'], '127.0.0.1');
    assert.equal(observedHeaders['x-forwarded-proto'], 'http');
    assert.equal(observedHeaders['x-omlorix-launcher-secret'], LAUNCHER_SECRET);
    assert.equal(observedHeaders['x-omlorix-verification-nonce'], '');
    assert.equal(observedHeaders.forwarded, undefined);
  } finally {
    await proxy.stop();
    await close(upstream);
  }
});

test('LauncherReverseProxy closes upgraded sockets during shutdown', async () => {
  const proxy = new LauncherReverseProxy({
    logger: {
      error() {},
    },
  });
  const server = http.createServer();
  proxy.httpServer = server;
  proxy.proxy = {
    close() {},
    ws() {},
  };
  proxy.attachUpgradeHandler(server);
  const proxyPort = await listen(server);

  try {
    const connection = openUpgradeSocket(proxyPort);
    await connection.connected;
    await new Promise((resolve) => setTimeout(resolve, 100));

    await proxy.stop();
    await connection.closed;
    assert.equal(connection.socket.destroyed, true);
  } finally {
    await proxy.stop();
  }
});
