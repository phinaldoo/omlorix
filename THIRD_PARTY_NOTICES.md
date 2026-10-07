# Third-party notices

This file identifies third-party material distributed with Omlorix. The licenses listed here apply only to the identified third-party material and do not apply to original Omlorix code or assets. Original Omlorix code and assets are made available under the PolyForm Free Trial License 1.0.0 in [`LICENSE`](LICENSE).

## Tecnativa docker-socket-proxy v0.4.2

Omlorix distributes a modified HAProxy configuration derived from Tecnativa's `docker-socket-proxy` project:

- Omlorix file: `electron/code-execution/haproxy.cfg`
- Upstream file: <https://github.com/Tecnativa/docker-socket-proxy/blob/v0.4.2/haproxy.cfg>
- Upstream project: <https://github.com/Tecnativa/docker-socket-proxy/tree/v0.4.2>
- Upstream license: Apache License 2.0
- License copy: `electron/code-execution/TECNATIVA_DOCKER_SOCKET_PROXY_LICENSE.txt`

The Omlorix version removes the upstream server-state file and seamless-reload directives, replaces the Docker events backend's zero timeout with a finite 30-second timeout, and adds comments describing the managed deny-by-default configuration. The modified file contains the same prominent modification notice and license reference so that the attribution remains present when the Launcher or CLI installs it into a Code Execution instance.

Tecnativa has not endorsed, sponsored, certified, or approved Omlorix. The use of Tecnativa's name above is solely to identify the origin of the modified configuration and its applicable license.

## Third-party provider names

Omlorix distributes no third-party provider logo or brand artwork. Compatible services are identified by text; provider entries use the neutral Connections icon. The provider icon policy is recorded in:

- `third_party_assets_manifest/provider-brand-assets.md`
- `third_party_assets_manifest/provider-brand-assets.manifest.json`

Any third-party provider names and trademarks remain the property of their respective owners. Their textual inclusion does not state or imply affiliation, sponsorship, certification, approval, partnership, or endorsement.

## Additional third-party material

Additional dependency and asset license information is maintained in:

- `third_party_assets_manifest/`
- `third_party_licenses/electron/`
- `third_party_licenses/go/`
- `frontend/legal/third_party_licenses/`
- `backend/app/assets/licenses/`

Third-party names, logos, libraries, fonts, and other materials remain subject to their respective licenses, terms, and policies.
