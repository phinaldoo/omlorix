# OpenAI

The native **OpenAI** provider supports OpenAI chat models and, where configured, transcription, speech, realtime voice, and image services. Use a compatible provider type for a custom endpoint.

Apply [Common Provider Settings](2_provider_settings.md) for shared credential, discovery, and lifecycle rules.

## Configure

1. Create a dedicated OpenAI project and API key. Add provider-side budgets, rate limits, and alerts; a ChatGPT subscription does not provide API quota.
2. Create an **OpenAI** provider and enter the **API key**. Add **Organization** or **Project ID** only when required for account scoping.
3. Select **Test Connection**, save, and create a [model](../6_llmmodels/2_manage_llmmodels.md) from the discovered list.
4. Test text chat before enabling files, reasoning, tools, native web search, prompt caching, or special service tiers.

Optional features require separate configuration: [Dictation](../3_admin_settings/15_1_dictation_settings.md), [Read Aloud](../3_admin_settings/15_2_text_to_speech_settings.md), [Realtime Call](../3_admin_settings/15_3_realtime_settings.md), [Image Generation](../7_tools/6_image_generation.md), and [Audio Generation](../7_tools/8_audio_generation.md). Omlorix's Video Generation provider list does not use the native OpenAI provider type.

## Data and cost controls

- **Store responses** controls the upstream storage request where supported; it does not control Omlorix chat history or establish a zero-data-retention agreement.
- **Share User Identifier** sends a stable user identifier to OpenAI for supported safety features. Leave it off unless your privacy policy permits that transfer.
- Prompt caching, service tiers, reasoning, media, and native tools can change latency and cost. Test them deliberately.
- **Service tier** offers **Flex**, **Standard**, and **Fast mode** where the model supports them. OpenAI renamed Priority processing to [Fast mode](https://developers.openai.com/api/docs/guides/fast-mode). The native OpenAI provider sends `service_tier: "fast"`, including for existing saved `priority` settings and request overrides. Saved settings and exports remain compatible and need no migration. Compatible endpoints and other providers retain their existing API values. Fast mode carries a per-token premium.

Model access and capabilities vary by project, region, and rollout. Use OpenAI billing as the cost authority and document all enabled data transfers under the relevant compliance pages. Test provider-native tools separately from Omlorix tools because they have different configuration and data flows.

## GPT-6 Astra

Select `gpt-6-astra` from the discovered models after your OpenAI project receives access. Omlorix supplies its image/document input capabilities, April 30, 2026 knowledge cutoff, 922,000-token input limit and 128,000-token output limit (within the 1,050,000-token context window). Existing models and defaults are unchanged.

- Reasoning is always enabled. Supported efforts are **Low**, **Medium**, **High**, **Extra high**, and **Maximum**. Medium is the default. Old saved or per-request `none`/`minimal` efforts are sent as `low`; other unsupported efforts use the default. Temperature, Top P, and log probabilities are excluded from requests.
- Tools require the **OpenAI** or **OpenAI Responses API** provider. Astra can use **OpenAI Chat Completions API** for conversations without tools; Omlorix rejects tool requests and tool history on that endpoint. Change the provider before using tools.
- Pro reasoning, persisted reasoning, tool search, and 30-minute prompt caching reuse the existing Responses controls. Catalog cost estimates include cache writes, Flex/Fast rates, and the full-request long-context surcharge above 272,000 input tokens. Standard rates per million tokens are $10 input, $1 cache read, $12.50 cache write, and $50 output; above the threshold they are $20, $2, $25, and $75 respectively. Regional processing surcharges are not included in Omlorix's catalog estimates.

OpenAI can stop an Astra conversation with `misalignment_policy_violation`. Omlorix stops processing, shows a translated review message, hides the failed response's retry action, and records the available request/response IDs. Review already executed actions with the responsible operator; the stop does not undo them. Stored chats retain the stop in chat and message metadata, including through backup/import/export, and reject later sends and regeneration. Temporary conversations retain the stop in their in-browser transcript without adding persistent history. There is no automatic retry or resume of a stopped workflow.

Async tool calling, mid-turn WebSocket steering, and cache-preserving reasoning configuration updates are optional upstream features; this compatibility update does not enable them.

Sources: [Astra model details](https://developers.openai.com/api/docs/models/gpt-6-astra), [migration guidance](https://developers.openai.com/api/docs/guides/latest-model), [pricing](https://developers.openai.com/api/docs/pricing), [safety stops](https://developers.openai.com/api/docs/guides/safety-checks/misalignment-monitoring).
