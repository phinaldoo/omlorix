# Firecrawl

**Firecrawl** supplies Search and browser-based Scrape roles from a hosted or self-hosted service. A single provider can fill both roles, though Omlorix calls them separately.

Use the shared controls and rollout sequence in [Web Search Providers](1_introduction.md); this page covers Firecrawl hosting and browser-worker concerns.

## Configure

Create a **Firecrawl** provider with its **API key** and optional **Base URL**. Review **Fallback country**, **Forward user locale**, **Proxy mode**, **Respect Robots.txt**, **Max search results**, enterprise privacy options, and domain controls.

For the hosted service, verify that the account plan permits the selected proxy and privacy options. For self-hosting, pin a supported version, protect the service, secure its browser workers and queues, and restrict its outbound network.

Assign the provider, then test Search, a direct URL, a slow JavaScript page, domain and robots denial, partial scrape failure, timeout, and billing. Browser jobs can continue or consume credits even when an Omlorix request times out.

Provider-side Search and browser navigation can make contacts Omlorix does not see. Use Firecrawl or infrastructure controls for those destinations. Treat all scraped content as untrusted and isolate self-hosted browser workers.

If Search works but Scrape fails, check scrape entitlement, worker health, proxy mode, queue capacity, robots rules, and the target site. Use Firecrawl's activity and billing as the cost authority.
