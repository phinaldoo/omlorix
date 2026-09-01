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

Model access and capabilities vary by project, region, and rollout. Use OpenAI billing as the cost authority and document all enabled data transfers under the relevant compliance pages. Test provider-native tools separately from Omlorix tools because they have different configuration and data flows.
