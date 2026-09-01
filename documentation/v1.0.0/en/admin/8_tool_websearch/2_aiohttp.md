# AIOHTTP

**AIOHTTP** is Omlorix's built-in page scraper. It reads known public URLs but does not search for them. Pair it with a Search provider.

Use the shared controls and rollout sequence in [Web Search Providers](1_introduction.md); this page covers the built-in scraper only.

## Configure

Create an **AIOHTTP** provider and review:

- **Respect Robots.txt**;
- **Verify SSL Certificate**, which should remain enabled in production;
- **Allowed domains** and **Blocked domains**;
- any displayed content or request limits.

No external scraping account or API key is required. The Omlorix application service contacts each target site directly, so its public IP address and request details are visible to that site.

Test a small public HTML page, a redirect, a blocked host, a robots denial, and a direct URL. The scraper is intended for ordinary public pages, not browser automation, sign-in flows, or sites requiring JavaScript rendering.

Use [Crawl4AI](3_crawl4ai.md) for pages that need a browser-rendering service. Disable certificate verification only for a controlled diagnostic against a known endpoint, never as a production workaround. If AIOHTTP fails, check [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md), name resolution, certificates, redirects, robots rules, domain rules, page size, and content type.
