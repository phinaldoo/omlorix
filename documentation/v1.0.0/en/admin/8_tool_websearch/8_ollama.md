# Ollama

The **Ollama** Web Search provider uses Ollama's hosted Search and Fetch service. It is separate from a local Ollama model server and requires a hosted Ollama API key.

Use the shared controls and rollout sequence in [Web Search Providers](1_introduction.md); this page covers the hosted Ollama service only.

Create the provider with its **API key**, then review **Max search results**, **Respect Robots.txt**, and domain controls. Assign it for search; it can also read direct webpages where supported. Keep the result count within the provider's documented range.

Queries and direct URLs are sent to Ollama's hosted service. Local domain and robots checks can filter returned content, but they cannot prevent every provider-side retrieval or redirect. If strict destination control is required, use a self-hosted Search and Scrape pair with network egress restrictions.

The service requires public outbound access to Ollama. It will not work in **Offline Mode** or a private-only policy unless explicitly permitted. A local Ollama model server at a private address cannot replace this hosted Web Search provider.

If authentication fails, verify that the key has hosted Web Search access. If results are empty, check the result limit, domain and robots filters, provider quota, and outbound policy.
