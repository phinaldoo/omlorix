# Serper

**Serper** provides hosted Google-style web result discovery. It does not read pages or provide Omlorix image search, so pair it with a Scrape provider.

Use the shared controls and rollout sequence in [Web Search Providers](1_introduction.md); this page covers Serper's locale and credit behavior.

Create a **Serper** provider with its **API key** and review **Fallback language**, **Fallback country**, **Forward user locale**, and **Number of results** (1-20). Then assign a scraper and test representative queries, locale behavior, provider quota, and page retrieval.

Queries and locale are sent to Serper; result pages are sent to the selected scraper and then to the chat model. Review both providers' retention and terms. Each query consumes provider credit, and Omlorix may not show an authoritative cost; use the Serper dashboard.

If Serper returns URLs but the model has no page content, troubleshoot the Scrape provider. For authentication or quota errors, verify the key, plan, remaining credits, and rate limits before retrying.
