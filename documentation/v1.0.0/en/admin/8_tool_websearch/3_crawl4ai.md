# Crawl4AI

**Crawl4AI** connects Omlorix to a self-hosted browser-based scraping service. It reads known URLs but does not discover them, so pair it with a Search provider.

Use the shared controls and rollout sequence in [Web Search Providers](1_introduction.md); this page covers the browser-rendering service only.

## Deploy and configure

1. Deploy a supported Crawl4AI release on a network reachable by the Omlorix application service.
2. Restrict access, enable authentication, use trusted TLS where appropriate, and constrain the service's outbound network.
3. Create a **Crawl4AI** provider with the service **Base URL**. Do not rely on the backend's `http://localhost:11235` default unless Crawl4AI actually shares the Omlorix application container's network namespace; normally use its private DNS name and port.
4. Enter the **API Token** when the service requires it. Omlorix sends it as a bearer token. [Crawl4AI 0.9 and later](https://github.com/unclecode/crawl4ai/blob/main/deploy/docker/MIGRATION.md) require `CRAWL4AI_API_TOKEN` for a network-exposed Docker API and otherwise bind to loopback, where a separate Omlorix service cannot reach it. Configure the same token in Crawl4AI and Omlorix. The field remains optional only for an older compatible release or an explicitly reviewed proxy contract.
5. Review **Retry Count**, **Respect Robots.txt**, and allowed/blocked domains.
6. Assign it as the Scrape provider and test both a search result and a direct public URL.

Browser scraping is resource-intensive and exposes the renderer to untrusted sites. Isolate the service, patch its browser frequently, limit CPU and memory, and prevent access to private networks and instance metadata.

There is no separate live connection test when saving the provider. If pages fail, inspect both Omlorix and Crawl4AI logs, authentication, service capacity, browser startup, target certificates, redirects, robots rules, and domain or outbound policy. Pin and retest releases because the service interface and browser behavior can change.
