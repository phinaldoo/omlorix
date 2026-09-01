# Web Search

**Web Search** lets a model find current pages and read public URLs. Omlorix can use separate providers for finding results and reading pages.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then configure the search roles and safeguards below.

## Choose providers

Open **Admin Settings > Tools** and select **Open provider list** on **WebSearch**, then create the required providers. See [Web Search Providers](../8_tool_websearch/1_introduction.md).

| Need | Suitable role |
| --- | --- |
| Find pages | A Search provider |
| Read a direct page | A Scrape provider |
| Both in one provider | A provider that supports both roles; Omlorix still performs separate steps |
| Provider-run research with returned content | A Combined provider |
| Image results | SearXNG |

A search-only provider cannot read a URL. A scrape-only provider cannot discover pages. Combined search also does not automatically provide direct-URL scraping.

## Enable it for a model

1. Create and test the provider or provider pair.
2. Edit the model and turn on **Web Search**.
3. Turn off **Native web search** when you want Omlorix's configured providers rather than the model provider's own search.
4. Select the **Search provider** and **Scrape provider** shown for the chosen workflow.
5. Test a current query, a cited result, a direct public page, and a blocked or private address with a pilot user.

Deep Research has separate assignments. See [Deep Research](3_deep_research.md).

## Policy and safety

- Configure **Allowed domains** and **Blocked domains** when a provider offers them. Blocking takes precedence.
- Keep **Respect Robots.txt** enabled unless your legal and policy review explicitly permits otherwise.
- [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md) remains the instance-wide boundary. Use a firewall or proxy when you need infrastructure-level enforcement.
- Search results and scraped pages are untrusted. They can be wrong, malicious, or contain prompt injection. Train users to verify citations and never treat page instructions as trusted commands.
- Supported documents and media found during search may be stored as user files and are subject to file scanning, quotas, and retention.

Search queries, URLs, locale information, and page contents may be sent to external providers and websites. Review provider retention, cost, terms, and regional processing.

## Operations

Saving a Web Search provider validates its schema and outbound destination policy; there is no separate live connection test. After creation or any change, run the real Search and Scrape workflows.

If search returns nothing, simplify the query and check provider quota, domain rules, locale, robots rules, and logs. If search works but a direct URL fails, verify that a scrape provider is assigned. If image search is missing, select a working SearXNG search provider.

Use **Export All** and **Import Providers** in the Web Search provider list. Exports omit every API key, imports create fresh IDs, and the import dialog does not ask for replacement credentials. Follow the credential-safe workflow in [Web Search Providers](../8_tool_websearch/1_introduction.md#import-and-export), then reassign the new IDs to models and Deep Research before restoring access.
