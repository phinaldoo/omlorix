# You.com

**You.com** supplies hosted Web Search and Contents roles. One provider can fill both, though Omlorix calls the roles separately. It does not provide Omlorix image search.

Use the shared controls and rollout sequence in [Web Search Providers](1_introduction.md); this page covers You.com's Search and Contents behavior.

Create a **You.com** provider with its **API key** and review **Fallback country**, **Forward user locale**, **Result count**, and domain controls. Robots checking is always applied for content retrieval.

Keep the result count compatible with both Search and Contents when one provider fills both roles. Test a query, a direct URL, locale, domain and robots denial, timeout, and billing.

Queries, country, URLs, and retrieved content are sent to You.com. Domain rules control visible targets but not every provider-side request. Review provider retention, region, safe-search behavior, cache freshness, quota, and terms.

Search and Contents can be billed separately, and Omlorix may not show an authoritative cost. Use the You.com platform for usage and charges. If Search works but Contents fails, reduce the result count, check entitlement and target policy, and test a single direct URL.
