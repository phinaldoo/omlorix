# Custom

**Custom** connects Omlorix to operator-controlled HTTP services for Search, Scrape, or both. Use it for an internal gateway or a provider not supported directly.

Use the shared controls and rollout sequence in [Web Search Providers](1_introduction.md); this page covers the gateway-specific checks.

## Before you enable it

Use only a gateway verified as compatible with Omlorix's **Custom** provider. Validate it in a non-production environment and keep the gateway versioned; saving the provider does not prove end-to-end compatibility.

Create the provider with:

- the Search **Base URL**;
- an optional **Scrape Base URL**;
- **Fallback country**, **Forward user locale**, and **Number of results** where needed;
- **Respect Robots.txt** and domain controls.

One provider can fill both Search and Scrape, but Omlorix still calls the roles separately. Test query results, direct URLs, raw content if enabled, partial results, invalid responses, timeouts, redirects, robots rules, and domain denials.

The Custom form has no API-key or custom-header field. Protect the service with private-network controls, source allowlisting, or a reverse proxy that authenticates Omlorix without requiring an application-supplied header. If the endpoint requires a per-request bearer token or custom header, use a supported provider type or change the gateway contract. Do not expose an unauthenticated internal scraper publicly. The gateway must also control hidden redirects and downstream destinations; Omlorix can validate only the targets it sees.

Omlorix does not treat custom provider cost metadata as authoritative. Use gateway logs and the upstream provider's billing records.
