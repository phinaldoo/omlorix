# Perplexity

**Perplexity** is a hosted Combined provider that returns ranked URLs and extracted snippets. It cannot read an arbitrary direct URL, so assign a separate Scrape provider.

Use the shared controls and rollout sequence in [Web Search Providers](1_introduction.md); this page covers Perplexity's combined-search limits.

## Configure

1. Create a Perplexity API key with billing and quota controls.
2. Create the provider and review **Max results** (1-20), per-result and total context limits, domain filters, fallback locale, **Forward user locale**, and **Respect Robots.txt**.
3. Assign Perplexity for search and another provider for scraping.
4. Test a query, a direct URL through the separate scraper, locale, domain restrictions, robots behavior, and provider billing.

Use either allowed-domain or blocked-domain rules as described by the form; do not mix modes. Restrictive rules can legitimately return no results.

Combined retrieval occurs at Perplexity before Omlorix applies local result and robots checks. Review the provider's hidden retrieval, retention, region, quota, and price. Omlorix may not have an authoritative dollar value, so use the Perplexity portal.

If direct URLs fail, check the assigned Scrape provider rather than Perplexity. For sparse results, temporarily remove domain rules, reduce context limits only after confirming quota or timeout pressure, and test a simpler query.
