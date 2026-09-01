# Web Search Providers

Web Search providers find pages, read known URLs, or combine both tasks. The [Web Search](../7_tools/2_websearch.md) guide explains model assignment and safety; this page is the canonical reference for provider roles and shared controls.

## Provider roles

| Provider | Search | Scrape direct URLs | Combined search with content | Images |
| --- | :---: | :---: | :---: | :---: |
| [AIOHTTP](2_aiohttp.md) | — | Yes | — | — |
| [Crawl4AI](3_crawl4ai.md) | — | Yes | — | — |
| [Custom](4_custom.md) | Yes | Yes | — | — |
| [DuckDuckGo](5_duckduckgo.md) | Yes | — | — | — |
| [Exa](6_exa.md) | — | Yes | Yes | — |
| [Firecrawl](7_firecrawl.md) | Yes | Yes | — | — |
| [Ollama](8_ollama.md) | — | Yes | Yes | — |
| [Perplexity](9_perplexity.md) | — | — | Yes | — |
| [SearXNG](10_searxng.md) | Yes | — | — | Yes |
| [Serper](11_serper.md) | Yes | — | — | — |
| [Tavily](12_tavily.md) | Yes | Yes | — | — |
| [You.com](13_youcom.md) | Yes | Yes | — | — |

Search-only providers require a scraper. Combined providers require a separate scraper for arbitrary URLs unless their page says otherwise.

## Create and test

1. Open **Admin Settings > Tools**, select **Open provider list** on **WebSearch**, and then select **New Web Search Provider**.
2. Choose a type, enter a unique **Name**, and complete the displayed settings.
3. Save the provider, assign it to a restricted model, and run a real query as a pilot user.
4. Test a direct public URL whenever the provider will fill the Scrape role.
5. Test locale, domain, robots, outbound-network, quota, and error behavior.

Saving generally validates the form and destination policy, not live credentials or complete functionality. A provider that supports two roles must be tested once for each role.

## Shared controls

- **Forward user locale** can send saved profile country or language values supported by that provider. Leave it off unless localized results justify the additional transfer.
- **Number of results** or similar limits apply per query; larger values increase provider usage, scraping, latency, and model context.
- **Respect Robots.txt** should remain on unless policy and legal review approve otherwise.
- **Allowed domains** and **Blocked domains** use hostnames; blocking takes precedence.
- [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md) controls the provider endpoint and locally fetched targets. Provider-side hidden traffic needs provider or infrastructure controls.

Queries, locale, URLs, and content can cross several processors. Review credentials, retention, region, provider terms, result quality, prompt injection, cost, and rate limits. A zero or missing Omlorix cost does not mean the provider is free; reconcile its billing dashboard.

## Import and export

Use **Export All** and **Import Providers** in the Web Search provider list. The version 1.0 bundle contains source IDs, provider types, unique names, non-secret settings, and credential-presence metadata. Every `api_key` value is removed.

Import creates fresh IDs and never updates an existing provider. Duplicate names, missing required keys, invalid settings, and blocked outbound destinations are reported per item. The import dialog lets you select entries but does not prompt for replacement credentials.

Credential handling depends on the type:

- Exa, Firecrawl, Tavily, Serper, You.com, Perplexity, and Ollama require an API key. Recreate these entries manually through the UI. If an approved automated migration must inject keys, use only a permission-restricted, ephemeral copy on protected local storage; never modify the retained export or place the copy in synced folders, backups, logs, tickets, or source control. Dispose of it under the deployment's secret-handling procedure.
- Crawl4AI's API token is optional but is still removed from the export. It can be imported without the token and then edited, or restored through the same protected-copy procedure.
- AIOHTTP, Custom, DuckDuckGo, and SearXNG need no API key and can be imported from the secret-free bundle as-is.

Source IDs are portability metadata only. After import, replace the old Web Search IDs in models and in both Search and Scrape fields under Deep Research. Deleting a provider clears matching model assignments and removes the Web Search tool from affected models, but it does not remap Deep Research. Repeat a real query and direct-URL test before restoring access.
