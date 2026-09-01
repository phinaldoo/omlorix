# Set Up HTTPS

Use HTTPS for every non-local deployment. Keep Omlorix's direct browser endpoint private and terminate TLS at the Server Launcher proxy or another trusted edge.

## Before You Start

- point the public hostname to the proxy
- obtain a certificate valid for that hostname and its private key
- allow the chosen HTTP/HTTPS ports through the host firewall only where needed
- keep Omlorix's direct browser endpoint on loopback or an internal network

The Launcher and CLI can use an existing certificate but do not issue or renew one.

## Launcher Proxy

In **Proxy**:

1. Set the public hostname and listener ports.
2. Enable HTTPS and select the certificate and private key; add a CA chain or key passphrase when required.
3. Enable HTTP-to-HTTPS redirect if the HTTP listener is exposed.
4. Save, enable, and start the proxy.
5. Run **Visitor IPs** detection, repair if offered, and verify.

Install the background proxy service if the endpoint must remain available while the Launcher is closed. Refresh that service after moving the Launcher or updating packaged proxy assets.

Without the background service, the managed proxy runs inside the Launcher. Quitting the Launcher therefore stops public access, and the Launcher shows a native confirmation before it exits. This does not stop an independently managed external proxy.

## CLI Proxy

Inspect the current state:

```bash
omlorix-server proxy status
omlorix-server proxy settings
```

Use `proxy configure name=value` for the hostname, ports, certificate, key, optional CA chain, redirect, and autostart settings. Then:

```bash
omlorix-server proxy enable
omlorix-server proxy start
omlorix-server visitor-ip detect
omlorix-server visitor-ip repair
omlorix-server visitor-ip verify
```

Use `proxy install-service` for background operation and `proxy refresh-service` after the CLI executable moves or changes. Run `omlorix-server --help` for the current setting names.

## External Proxy or Tunnel

Configure the edge to:

- send HTTP and WebSocket traffic to the private Omlorix browser endpoint
- preserve the original scheme and host
- overwrite, not append to, client-supplied forwarding headers
- connect from a fixed address or narrow CIDR you can trust
- enforce request-size and timeout values compatible with uploads and streaming responses

Enable proxy-header trust only for the actual proxy sources. Never trust all IPv4 or IPv6 addresses. If the direct browser endpoint is publicly reachable, forwarded-header trust can be bypassed.

## Finish Omlorix Configuration

In **Admin Settings > General**, place the exact HTTPS origin first in **Public URLs**. Save and restart every Omlorix application replica. Then update callback URLs at enabled OAuth and SSO providers.

Verify from an external browser:

- the certificate chain and hostname are valid
- HTTP redirects to HTTPS, if offered
- password sign-in and refresh work
- streaming responses and realtime features stay connected
- share and reset links use the HTTPS origin
- OAuth, SSO, and passkeys complete if enabled
- **Visitor IPs** reports the real client address

## Encryption Boundary

The browser padlock proves only the browser-to-edge connection. If policy requires encryption on every hop, also protect proxy-to-Omlorix traffic and external PostgreSQL, Redis, storage, telemetry, and provider connections.

## Troubleshooting

- **Page loads but sign-in fails:** the browser origin probably does not match **Public URLs**, or forwarded scheme/host values are wrong. Save, restart, and retest.
- **WebSockets or streaming fail:** enable upgrade forwarding and increase proxy timeouts.
- **Wrong visitor IP:** narrow and correct the trusted proxy chain; do not broaden it to hide the warning.
- **Certificate warning:** fix the hostname, validity dates, key pairing, and complete chain before inviting users.
- **Plain HTTP remains reachable:** bind the direct browser endpoint privately and expose only the TLS edge.

Repeat visitor-IP verification after every proxy, tunnel, load-balancer, or certificate-path change. Add the result to the maintenance acceptance checks in [Operate and Update Omlorix](4_operations.md).
