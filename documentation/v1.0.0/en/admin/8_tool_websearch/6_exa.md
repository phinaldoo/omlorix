# Exa

**Exa** is a hosted Combined provider that returns search results with extracted content and can also read direct URLs. It does not provide Omlorix image search.

Use the shared controls and rollout sequence in [Web Search Providers](1_introduction.md); this page covers Exa's combined retrieval.

## Configure

1. Create a dedicated Exa API key with provider-side spend controls.
2. Create an **Exa** Web Search Provider and enter the **API key**.
3. Review **Max search results**, **Type**, **Fallback country**, **Forward user locale**, **Respect Robots.txt**, and domain controls.
4. Assign Exa for search and, when wanted, as the Scrape provider for direct URLs.
5. Test a query, a direct URL, domain restrictions, robots behavior, and provider billing.

Combined search happens at Exa before Omlorix can apply every local check. Domain controls can be sent upstream where supported and results are checked again, but provider-side hidden retrieval still follows Exa's systems and terms.

Queries, country, URLs, and page contents are sent to Exa. Review retention, regional processing, result limits, and cost. Omlorix may record a provider-reported estimate, but the Exa dashboard and invoice are authoritative.

If search works but a URL does not, test Exa's direct-content entitlement and check robots, domain, and outbound rules. If results disappear after applying a domain rule, confirm the hostnames and remember that an allowlist excludes all other hosts.
