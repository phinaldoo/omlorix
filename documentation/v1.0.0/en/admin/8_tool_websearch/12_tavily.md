# Tavily

**Tavily** supplies hosted Search and Scrape roles. One provider can fill both, though Omlorix calls Search and Extract separately. It does not provide Omlorix image search.

Use the shared controls and rollout sequence in [Web Search Providers](1_introduction.md); this page covers Tavily's Search and Extract behavior.

Create a **Tavily** provider with its **API key** and review **Respect Robots.txt**, **Fallback country**, **Forward user locale**, and domain controls. Assign it to one or both roles and test a query, a direct URL, domain and robots denial, partial extraction failure, timeout, and provider billing.

Country influences search ranking. Domain controls filter targets Omlorix sees but do not fully control Tavily's provider-side search or extraction traffic. Use provider and infrastructure controls for strict destination policy.

Queries, country, URLs, and extracted content are sent to Tavily. Review retention, region, quota, and price. Search and extraction can consume separate credits; the Tavily dashboard is authoritative.

If Search works but direct URLs fail, verify Extract entitlement, robots and domain rules, target accessibility, and plan limits. If locale causes an error, turn off forwarding and test a broadly supported fallback country.
