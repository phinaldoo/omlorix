const fs = require('fs/promises');
const fssync = require('fs');
const http = require('http');
const https = require('https');
const httpProxy = require('http-proxy');
const net = require('node:net');

const DEFAULT_PROXY_HTTP_PORT = '8081';
const DEFAULT_PROXY_HTTPS_PORT = '8443';
const DEFAULT_PROXY_BIND = '0.0.0.0';
const TLS_FILE_MAX_BYTES = 1024 * 1024;

function envTruthy(value) {
  return ['1', 'true', 'yes', 'on'].includes(String(value || '').trim().toLowerCase());
}

function normalizePort(value, fallback) {
  const text = String(value || '').trim() || String(fallback);
  if (!/^\d+$/.test(text)) return text;
  return String(Number(text));
}

function validatePort(name, value) {
  if (!/^\d+$/.test(String(value || ''))) {
    return `${name} must be a number.`;
  }
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    return `${name} must be between 1 and 65535.`;
  }
  return '';
}

function validateBindHost(value) {
  const host = String(value || '').trim();
  if (!host) return 'Bind address is required.';
  if (/[\s/\\]/.test(host)) return 'Bind address must be a host or IP address, not a URL.';
  return '';
}

function validatePublicHostname(value) {
  const host = String(value || '').trim();
  if (!host) return 'Public hostname is required.';
  const unwrapped = host.startsWith('[') && host.endsWith(']') ? host.slice(1, -1) : host;
  if (net.isIP(unwrapped)) return '';
  if (host.length > 253 || !/^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$/.test(host)) {
    return 'Public hostname must be a hostname or IP address, without a scheme, path, or port.';
  }
  if (host.split('.').some((label) => !label || label.length > 63 || label.startsWith('-') || label.endsWith('-'))) {
    return 'Public hostname contains an invalid DNS label.';
  }
  return '';
}

function firstConfiguredPublicHost(value) {
  return String(value || '').split(',').map((item) => item.trim()).find(Boolean) || '';
}

function publicHostForBind(bindHost) {
  const host = String(bindHost || '').trim();
  if (!host || host === '0.0.0.0' || host === '::') return 'localhost';
  return host.includes(':') && !host.startsWith('[') ? `[${host}]` : host;
}

function buildProxyUrl({ protocol, bindHost, port }) {
  return `${protocol}://${publicHostForBind(bindHost)}:${port}`;
}

/**
 * Replace every client-controlled forwarding identity at the launcher edge.
 *
 * http-proxy's `xfwd` option appends to an existing X-Forwarded-For value,
 * which is unsafe at a public listener. The launcher is the authoritative edge,
 * so downstream services must receive only the address and scheme observed on
 * this socket. The standardized Forwarded header is removed for the same reason.
 */
function canonicalSocketAddress(value) {
  let remoteAddress = String(value || '').trim().split('%', 1)[0];
  if (remoteAddress.toLowerCase().startsWith('::ffff:')) {
    const mappedAddress = remoteAddress.slice(7);
    if (net.isIP(mappedAddress) === 4) remoteAddress = mappedAddress;
  }
  return net.isIP(remoteAddress) ? remoteAddress : '';
}

function setAuthoritativeForwardedHeaders(proxyReq, req, launcherSecret = '', publicHostname = '') {
  const remoteAddress = canonicalSocketAddress(req?.socket?.remoteAddress);
  const forwardedProto = req?.socket?.encrypted ? 'https' : 'http';
  let verificationNonce = '';
  if (remoteAddress && net.isIP(remoteAddress)) {
    const address = net.isIP(remoteAddress) === 4
      ? remoteAddress.split('.').map(Number)
      : [];
    const loopback = remoteAddress === '::1' || address[0] === 127;
    if (loopback) {
      try {
        const requestUrl = new URL(req?.url || '/', 'http://localhost');
        const candidate = requestUrl.pathname === '/api/v1/proxy-verification'
          ? requestUrl.searchParams.get('nonce') || ''
          : '';
        if (/^[A-Za-z0-9_-]{16,128}$/.test(candidate)) verificationNonce = candidate;
      } catch {
        verificationNonce = '';
      }
    }
  }

  proxyReq.setHeader('X-Forwarded-For', remoteAddress);
  proxyReq.setHeader('X-Real-IP', remoteAddress);
  proxyReq.setHeader('X-Forwarded-Proto', forwardedProto);
  proxyReq.setHeader('X-Forwarded-Host', publicHostname);
  proxyReq.setHeader('X-Omlorix-Launcher-Secret', launcherSecret);
  // Only a loopback controller request receives this one-use marker. Incoming
  // values are always overwritten, so an external visitor cannot manufacture
  // a successful readiness response through the public listener.
  proxyReq.setHeader('X-Omlorix-Verification-Nonce', verificationNonce);
  proxyReq.removeHeader('X-Omlorix-Proxy-Verification');
  proxyReq.removeHeader('X-Omlorix-Proxy-Verification-Nonce');
  proxyReq.removeHeader('Forwarded');
}

function normalizeProxyConfig(env = {}) {
  const frontendPort = normalizePort(env.FRONTEND_HTTP_HOST_PORT, '8080');
  const bindHost = String(env.OMLORIX_LAUNCHER_PROXY_BIND || DEFAULT_PROXY_BIND).trim() || DEFAULT_PROXY_BIND;
  const httpPort = normalizePort(env.OMLORIX_LAUNCHER_PROXY_HTTP_PORT, DEFAULT_PROXY_HTTP_PORT);
  const httpsPort = normalizePort(env.OMLORIX_LAUNCHER_PROXY_HTTPS_PORT, DEFAULT_PROXY_HTTPS_PORT);
  const enabled = envTruthy(env.OMLORIX_LAUNCHER_PROXY_ENABLED);
  const autostartValue = String(env.OMLORIX_LAUNCHER_PROXY_AUTOSTART ?? '').trim();
  const autostartExplicit = Boolean(autostartValue);
  const httpsEnabled = envTruthy(env.OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED);
  const publicHostname = String(
    env.OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME
      || firstConfiguredPublicHost(env.TRUSTED_HOSTS)
      || publicHostForBind(bindHost),
  ).trim();

  return {
    enabled,
    // An enabled launcher proxy should be available after the launcher opens.
    // Preserve an explicit false value for operators who intentionally prefer
    // manual starts, while making missing/blank legacy settings follow enabled.
    autostart: autostartValue ? envTruthy(autostartValue) : enabled,
    autostartExplicit,
    bindHost,
    httpPort,
    httpsEnabled,
    httpsPort,
    redirectHttpToHttps: envTruthy(env.OMLORIX_LAUNCHER_PROXY_REDIRECT_HTTP_TO_HTTPS),
    tlsCertPath: String(env.OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH || '').trim(),
    tlsKeyPath: String(env.OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH || '').trim(),
    tlsCaPath: String(env.OMLORIX_LAUNCHER_PROXY_TLS_CA_PATH || '').trim(),
    tlsKeyPassphrase: String(env.OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE || ''),
    tlsKeyPassphraseSet: Boolean(env.OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE),
    launcherSecret: String(env.OMLORIX_LAUNCHER_PROXY_SECRET || '').trim(),
    publicHostname,
    target: `http://127.0.0.1:${frontendPort}`,
    publicUrl: httpsEnabled
      ? buildProxyUrl({ protocol: 'https', bindHost: publicHostname, port: httpsPort })
      : buildProxyUrl({ protocol: 'http', bindHost: publicHostname, port: httpPort }),
    httpUrl: buildProxyUrl({ protocol: 'http', bindHost: publicHostname, port: httpPort }),
    httpsUrl: buildProxyUrl({ protocol: 'https', bindHost: publicHostname, port: httpsPort }),
  };
}

function validateProxyConfig(config, { requireTlsFiles = false } = {}) {
  const errors = {};
  const bindError = validateBindHost(config.bindHost);
  if (bindError) errors.bindHost = bindError;
  const publicHostError = validatePublicHostname(config.publicHostname);
  if (publicHostError) errors.publicHostname = publicHostError;
  if (config.enabled && !/^[0-9a-f]{64}$/i.test(String(config.launcherSecret || ''))) {
    errors.launcherSecret = 'The launcher proxy authentication secret is missing or invalid.';
  }

  const httpPortError = validatePort('HTTP port', config.httpPort);
  if (httpPortError) errors.httpPort = httpPortError;

  try {
    const target = new URL(config.target);
    const targetPort = target.port || (target.protocol === 'https:' ? '443' : '80');
    const bindCouldReachTarget = ['0.0.0.0', '::', '127.0.0.1', 'localhost', '::1'].includes(String(config.bindHost || '').trim());
    if (bindCouldReachTarget && String(config.httpPort) === String(targetPort)) {
      errors.httpPort = 'HTTP proxy port must be different from the Omlorix Docker port.';
    }
  } catch (error) {
    errors.target = 'Proxy target must be a valid URL.';
  }

  if (config.httpsEnabled) {
    const httpsPortError = validatePort('HTTPS port', config.httpsPort);
    if (httpsPortError) errors.httpsPort = httpsPortError;
    if (String(config.httpPort) === String(config.httpsPort)) {
      errors.httpsPort = 'HTTPS port must be different from the HTTP port.';
    }
    try {
      const target = new URL(config.target);
      const targetPort = target.port || (target.protocol === 'https:' ? '443' : '80');
      const bindCouldReachTarget = ['0.0.0.0', '::', '127.0.0.1', 'localhost', '::1'].includes(String(config.bindHost || '').trim());
      if (bindCouldReachTarget && String(config.httpsPort) === String(targetPort)) {
        errors.httpsPort = 'HTTPS proxy port must be different from the Omlorix Docker port.';
      }
    } catch {
      errors.target = 'Proxy target must be a valid URL.';
    }
    if (!config.tlsCertPath) errors.tlsCertPath = 'Certificate file is required when HTTPS is enabled.';
    if (!config.tlsKeyPath) errors.tlsKeyPath = 'Private key file is required when HTTPS is enabled.';
  }

  if (requireTlsFiles && config.httpsEnabled) {
    for (const [key, filePath] of [
      ['tlsCertPath', config.tlsCertPath],
      ['tlsKeyPath', config.tlsKeyPath],
      ['tlsCaPath', config.tlsCaPath],
    ]) {
      if (!filePath) continue;
      try {
        const stat = fssync.statSync(filePath);
        if (!stat.isFile()) {
          errors[key] = 'Choose a regular file.';
        } else if (stat.size > TLS_FILE_MAX_BYTES) {
          errors[key] = 'TLS files must be 1 MB or smaller.';
        }
      } catch (error) {
        errors[key] = 'File does not exist or cannot be read.';
      }
    }
  }

  return errors;
}

function proxyErrorName(error) {
  return error?.code || error?.name || 'ProxyError';
}

class LauncherReverseProxy {
  constructor({ logger = console } = {}) {
    this.logger = logger;
    this.proxy = null;
    this.httpServer = null;
    this.httpsServer = null;
    this._upgradedSockets = new Set();
    this.config = normalizeProxyConfig({});
    this.lastError = '';
    this.startedAt = '';
  }

  status(config = this.config) {
    return {
      config: {
        ...config,
        tlsKeyPassphrase: '',
        launcherSecret: '',
        launcherSecretSet: Boolean(config.launcherSecret),
        tlsKeyPassphraseSet: Boolean(config.tlsKeyPassphraseSet || config.tlsKeyPassphrase),
      },
      running: Boolean(this.httpServer || this.httpsServer),
      httpRunning: Boolean(this.httpServer),
      httpsRunning: Boolean(this.httpsServer),
      startedAt: this.startedAt,
      lastError: this.lastError,
    };
  }

  async readTlsFile(filePath, label) {
    const stat = await fs.stat(filePath);
    if (!stat.isFile()) throw new Error(`${label} must be a regular file.`);
    if (stat.size > TLS_FILE_MAX_BYTES) throw new Error(`${label} must be 1 MB or smaller.`);
    return fs.readFile(filePath);
  }

  async tlsOptions(config) {
    const certificate = await this.readTlsFile(config.tlsCertPath, 'TLS certificate');
    const options = {
      cert: certificate,
      key: await this.readTlsFile(config.tlsKeyPath, 'TLS private key'),
      minVersion: 'TLSv1.2',
    };
    if (config.tlsCaPath) {
      const chain = await this.readTlsFile(config.tlsCaPath, 'TLS CA chain');
      // Node serves intermediates from the certificate chain, while `ca`
      // configures trust anchors for client-certificate verification. Append
      // the optional chain to `cert` so Electron and the Go CLI present the
      // same full server chain.
      options.cert = Buffer.concat([
        Buffer.from(certificate.toString('utf8').trimEnd()),
        Buffer.from('\n'),
        Buffer.from(chain.toString('utf8').trim()),
        Buffer.from('\n'),
      ]);
    }
    if (config.tlsKeyPassphrase) {
      options.passphrase = config.tlsKeyPassphrase;
    }
    return options;
  }

  createProxy(config) {
    const proxy = httpProxy.createProxyServer({
      target: config.target,
      changeOrigin: false,
      ws: true,
      xfwd: false,
      proxyTimeout: 0,
    });

    proxy.on('error', (error, req, res) => {
      const errorType = proxyErrorName(error);
      this.lastError = errorType;
      this.logger.error(`[launcher-proxy] ${req?.method || 'UNKNOWN'} ${req?.url || ''} failed: ${errorType}`.trim());
      const responseBody = JSON.stringify({
        detail: 'Omlorix proxy upstream is unavailable',
        error: errorType,
      });
      if (res && (typeof res.writeHead === 'function' || res instanceof http.ServerResponse)) {
        if (!res.headersSent) {
          res.writeHead(502, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
        }
        if (typeof res.end === 'function') {
          res.end(responseBody);
        }
        return;
      }
      if (res && typeof res.end === 'function') {
        const rawResponse = [
          'HTTP/1.1 502 Bad Gateway',
          'Content-Type: application/json',
          'Cache-Control: no-store',
          'Connection: close',
          `Content-Length: ${Buffer.byteLength(responseBody)}`,
          '',
          responseBody,
        ].join('\r\n');
        res.end(rawResponse);
        return;
      }
      if (res && typeof res.destroy === 'function' && !res.destroyed) {
        res.destroy();
      }
    });

    proxy.on('proxyReq', (proxyReq, req) => {
      setAuthoritativeForwardedHeaders(proxyReq, req, config.launcherSecret, config.publicHostname);
    });
    proxy.on('proxyReqWs', (proxyReq, req) => {
      setAuthoritativeForwardedHeaders(proxyReq, req, config.launcherSecret, config.publicHostname);
    });

    return proxy;
  }

  createHttpHandler(config) {
    return (req, res) => {
      if (config.redirectHttpToHttps && config.httpsEnabled) {
        // Never build redirects from an untrusted request Host header. The
        // configured public hostname is validated when the proxy starts.
        const locationHost = config.publicHostname || publicHostForBind(config.bindHost);
        const portSuffix = String(config.httpsPort) === '443' ? '' : `:${config.httpsPort}`;
        res.writeHead(308, {
          Location: `https://${locationHost}${portSuffix}${req.url || '/'}`,
          'Cache-Control': 'no-store',
        });
        res.end();
        return;
      }
      this.proxy.web(req, res);
    };
  }

  attachUpgradeHandler(server) {
    server.on('upgrade', (req, socket, head) => {
      this._upgradedSockets.add(socket);
      const cleanup = () => {
        this._upgradedSockets.delete(socket);
      };
      socket.once('close', cleanup);
      socket.once('end', cleanup);
      socket.once('error', cleanup);
      this.proxy.ws(req, socket, head);
    });
  }

  listen(server, port, bindHost) {
    return new Promise((resolve, reject) => {
      const onError = (error) => {
        server.off('listening', onListening);
        reject(error);
      };
      const onListening = () => {
        server.off('error', onError);
        resolve();
      };
      server.once('error', onError);
      server.once('listening', onListening);
      server.listen(Number(port), bindHost);
    });
  }

  async start(config) {
    const nextConfig = { ...normalizeProxyConfig({}), ...config };
    const errors = validateProxyConfig(nextConfig, { requireTlsFiles: true });
    if (Object.keys(errors).length) {
      const message = Object.values(errors)[0] || 'Proxy settings are invalid.';
      const error = new Error(message);
      error.validationErrors = errors;
      throw error;
    }
    if (!nextConfig.enabled) {
      throw new Error('Enable the launcher proxy before starting it.');
    }

    await this.stop();
    this.config = nextConfig;
    this.proxy = this.createProxy(nextConfig);

    try {
      this.httpServer = http.createServer(this.createHttpHandler(nextConfig));
      this.attachUpgradeHandler(this.httpServer);
      await this.listen(this.httpServer, nextConfig.httpPort, nextConfig.bindHost);

      if (nextConfig.httpsEnabled) {
        this.httpsServer = https.createServer(await this.tlsOptions(nextConfig), (req, res) => {
          this.proxy.web(req, res);
        });
        this.attachUpgradeHandler(this.httpsServer);
        await this.listen(this.httpsServer, nextConfig.httpsPort, nextConfig.bindHost);
      }

      this.lastError = '';
      this.startedAt = new Date().toISOString();
      return this.status(nextConfig);
    } catch (error) {
      this.lastError = proxyErrorName(error);
      await this.stop();
      throw error;
    }
  }

  async stop() {
    const servers = [this.httpServer, this.httpsServer].filter(Boolean);
    this.httpServer = null;
    this.httpsServer = null;

    for (const socket of this._upgradedSockets) {
      if (!socket.destroyed) {
        socket.destroy();
      }
    }
    this._upgradedSockets.clear();

    await Promise.all(servers.map((server) => new Promise((resolve) => {
      server.close(() => resolve());
    })));

    if (this.proxy) {
      this.proxy.close();
      this.proxy = null;
    }
    this.startedAt = '';
  }
}

module.exports = {
  DEFAULT_PROXY_BIND,
  DEFAULT_PROXY_HTTP_PORT,
  DEFAULT_PROXY_HTTPS_PORT,
  LauncherReverseProxy,
  normalizeProxyConfig,
  validateProxyConfig,
};
