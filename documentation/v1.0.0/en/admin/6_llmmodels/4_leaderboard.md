# Leaderboard

The **Leaderboard** adds Artificial Analysis benchmark and model information to Omlorix's model selection experience. It is reference data, not a guarantee of quality, safety, privacy, availability, or suitability for your workload.

## Enable it

1. Open the relevant group's **Leaderboard** settings.
2. Enable access, choose **Free** or **Full model data (Pro or Commercial)**, and enter an Artificial Analysis API key. A key is required for both levels; Omlorix's Full option requires an upstream [Pro or Commercial API tier](https://artificialanalysis.ai/data-api/docs).
3. Save, then open the leaderboard as an administrator and as a normal user in an enabled group.
4. Confirm that expected Omlorix models and saved Agents match external entries. Unmatched targets are omitted from the leaderboard even though they can still work in chat.

Omlorix starts with the user's access-filtered models and Agents, resolves an Agent's accessible base model, and matches the provider model identifier locally. Only recognized matches are returned. Custom deployment names, gateways, local tags, new releases, and aliases can therefore leave the leaderboard empty or incomplete.

## What users see

Depending on the data level and upstream fields, users can compare available model metadata, benchmark information, context, modalities, tools, and price estimates. External values may use different assumptions or update schedules from the configured Omlorix model.

## Privacy, quota, and operations

- Omlorix sends the Artificial Analysis API key and normal request metadata to fetch the selected dataset. Matching against Omlorix model identifiers happens on the server after retrieval; local model names and user identities are not query parameters to Artificial Analysis.
- Results are held only in process memory and partitioned by a one-way hash of the API key plus data level. Free data is cached for up to six hours and Full data for up to one hour; restart and relevant provider, model, or group-setting changes clear applicable entries.
- Free and Full model-data access have different fields, quotas, and cache periods. Artificial Analysis calls its expanded upstream tiers Pro and Commercial; **Full** is Omlorix's label for that expanded response.
- Leaderboard prices are informational. Provider billing and your contracts are authoritative.

If the page is empty, check that the feature is enabled for the user's effective group, the external credential and quota are valid, and the saved model names can be matched. Disable the integration before rotating its key if you need to stop external requests immediately.
