# SearXNG

**SearXNG** is a self-hosted metasearch provider for web and image discovery. It does not read result pages, so pair it with a Scrape provider.

Use the shared controls and rollout sequence in [Web Search Providers](1_introduction.md); this page covers the self-hosted metasearch service.

## Deploy and configure

1. Deploy a supported SearXNG release, pin it, and persist its configuration.
2. Enable structured search responses and the search engines you intend to use. Image search requires working image engines.
3. Restrict access to Omlorix and operators; do not expose an unprotected instance publicly.
4. Create a **SearXNG** provider with a **Base URL** reachable from the Omlorix application service, then review locale and result settings.
5. Assign a Scrape provider and test web search, a direct URL, and image search. Omlorix returns at most 10 image results per image-search request, even when the provider's general result count is higher.

Queries reach the search engines enabled in SearXNG. Omlorix's outbound policy controls the connection to SearXNG and subsequent local scraping, not SearXNG's own egress. Apply destination, logging, retention, rate, and network controls inside the SearXNG deployment as well.

If provider creation fails, test the **Base URL** from the Omlorix service network. If SearXNG returns an authorization error or a webpage instead of results, confirm that structured search responses are enabled and that the proxy permits them. If web results have no content, check the separate scraper. If images are empty, test the configured image engines and inspect SearXNG rate-limit or block logs.
