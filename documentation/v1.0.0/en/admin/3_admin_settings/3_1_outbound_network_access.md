# Outbound Network Access

The connection controls under **Admin Settings > General** restrict requests made by Omlorix. Use them together with DNS, firewall, proxy, and cloud-network controls.

## Policies

| UI choice | Effect |
| --- | --- |
| **Offline Mode** | Allows local and private destinations and blocks public internet destinations. |
| **Allow all outbound requests** | Allows public and private destinations, subject to each feature's normal URL, redirect, and TLS checks. |
| **Allow only local and private network targets** | Blocks public destinations while allowing internal services. |
| **Allow only configured allowlist targets** | Allows destinations that match **External Request Allowlist**. |
| **Block all outbound requests** | Denies public and private outbound destinations. |

Allowlist entries can be hostnames, wildcard domains, URLs, or network ranges. An entry permits the destination; it does not disable certificate validation, provider restrictions, redirect safety, or a feature's own domain controls.

**Offline Mode** takes precedence over **External Requests Policy** and disables **Internet Connectivity Check Enabled**.

## Features to Inventory

A restrictive policy can affect:

- LLM, speech, realtime, and media-generation providers
- web search, scraping, downloads, and remote content
- OAuth, OpenID Connect discovery, and LDAP
- MCP servers and managed workspace connections
- remote file storage, backup destinations, and notification webhooks
- IP-location, version, connectivity, and other external service checks

Some authorization or picker flows also contact services directly from the user's browser. A successful server-side connection test therefore does not prove that every browser step can reach its destination.

## Safer Rollout

1. Inventory every enabled provider, identity service, connection, storage destination, backup destination, webhook, and external tool.
2. Apply the policy in a test instance or maintenance window.
3. Add the narrowest required destinations; prefer specific hosts over broad wildcards or networks.
4. Test sign-in, provider checks, managed connections, uploads, backups, and tool calls with an ordinary user.
5. Review denied-request evidence and add only understood dependencies.

Discovery documents, redirects, and separate authorization/API hosts can introduce more than one destination. Do not permit a broad private network merely to make one integration work.

## Troubleshooting

- **Provider or media feature unavailable:** allow its API and authentication destinations, then run its connection test.
- **Web Search fails:** check both the global policy and the selected search or scrape provider settings.
- **Connection authorizes but its tools fail:** the authorization and service hosts may differ.
- **Internal provider is blocked:** choose the private-only policy or add the precise endpoint to the allowlist.
