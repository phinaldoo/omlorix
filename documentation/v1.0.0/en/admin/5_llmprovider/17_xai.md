# xAI

The native **xAI** provider can supply Grok chat models and, where available to the account, transcription, speech, realtime voice, images, and video.

Apply [Common Provider Settings](2_provider_settings.md) for shared credential, discovery, and lifecycle rules.

## Configure

1. Create a dedicated xAI API key and set provider-side budgets and alerts.
2. Create an **xAI** provider, enter the **API key**, select **Test Connection**, and save.
3. Create a [model](../6_llmmodels/2_manage_llmmodels.md) from a discovered model.
4. Start with text chat, then enable and test files, reasoning, tools, and native search separately.

Configure optional features independently under [Dictation](../3_admin_settings/15_1_dictation_settings.md), [Read Aloud](../3_admin_settings/15_2_text_to_speech_settings.md), [Realtime Call](../3_admin_settings/15_3_realtime_settings.md), [Image Generation](../7_tools/6_image_generation.md), [Audio Generation](../7_tools/8_audio_generation.md), and [Video Generation](../7_tools/7_video_generation.md).

Availability varies by model, account, region, and rollout. Discovery is not proof of entitlement. Media jobs can be slow and expensive; test size, duration, polling, failure handling, and file storage before enabling broad access.

Review xAI's current retention and data-use terms for prompts, files, audio, and generated media. Use provider billing as the cost authority. Native search and Omlorix Web Search are separate data paths and must be reviewed independently.

## September 22, 2026 catalog review

[Grok 4.7](https://docs.x.ai/developers/models/grok-4.7) (`grok-4.7`) is recognized with a 500,000-token context window, May 2026 knowledge cutoff, and low, medium, high, and xhigh reasoning efforts (default: high). The catalog includes [standard, priority, and long-context rates](https://docs.x.ai/developers/pricing): standard input/cache/output rates are $2/$0.50/$6 per million tokens, doubling at 200,000 input tokens. The live API still determines account availability and supplies additional aliases.

Existing Grok models and callable redirects remain available. Grok 4.7 Fast is not a public API model and is not added to the catalog.
