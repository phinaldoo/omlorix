# Google AI Studio

**Google AI Studio** connects Omlorix to the Gemini Developer API. Depending on the selected model and account, it can support chat, files, speech, realtime voice, images, video, music, native search, and Deep Research.

Apply [Common Provider Settings](2_provider_settings.md) for shared credential, discovery, and lifecycle rules.

## Configure

1. Create a dedicated Gemini API key with billing, region, and model access.
2. Create a **Google AI Studio** provider, enter the **API key**, and keep the default **API version** unless a required feature says otherwise.
3. Select **Test Connection**, save, and create a [model](../6_llmmodels/2_manage_llmmodels.md).
4. Enable only capabilities supported by the exact model, then test them separately.

Optional features are configured independently:

- [Dictation Settings](../3_admin_settings/15_1_dictation_settings.md)
- [Read Aloud Settings](../3_admin_settings/15_2_text_to_speech_settings.md)
- [Realtime Call Settings](../3_admin_settings/15_3_realtime_settings.md)
- [Image Generation](../7_tools/6_image_generation.md), [Video Generation](../7_tools/7_video_generation.md), and [Music Generation](../7_tools/9_music_generation.md)
- [Deep Research](../7_tools/3_deep_research.md)

A listed model is not proof that every modality is available to your account. Preview models and API versions can change; keep **Auto-delete missing models** off unless you want the discovered catalog to control saved models.

Prompts, attachments, audio, and media references may be sent to Google. Review current regional processing, retention, safety settings, and billing before enabling access.

## September 2026 model update

The catalog was checked against Google's [release notes](https://ai.google.dev/gemini-api/docs/changelog), [model deprecations](https://ai.google.dev/gemini-api/docs/deprecations), and [pricing](https://ai.google.dev/gemini-api/docs/pricing) on September 5, 2026.

- **Gemini 3.8 Flash** is recognized for chat, native search, multimodal input, and cost estimates. Model discovery supplies its 1,048,576 input and 65,536 output token limits. Its supported thinking levels are low, medium (Google's default), and high. Saved minimal effort is normalized to low for 3.7 and 3.8 Flash.
- Gemini 3 chat and auxiliary generation use `thinking_level` instead of a numeric thinking budget. Deprecated temperature, top-p, top-k, and candidate-count parameters are omitted from requests; sampling fields are hidden for these models. Existing Gemini 2.5 controls remain available.
- The standard token estimates for Gemini 3.6, 3.7, and 3.8 Flash use Google's introductory rates: $0.75 input, $0.075 cached input, and $3.75 output per million tokens through December 31, 2026. Review estimates before the announced January 2027 price change; catalog prices are static.
- **Lyria 3.5** is available under Music Generation, using the Interactions API. It is excluded from normal chat discovery. Existing image, Veo video, realtime voice, and embedding catalogs remain available. Specialized Omni video, Robotics, and Transcribe endpoints are not normal chat models and remain excluded from chat discovery.

Existing saved models keep their IDs. Refresh discovery and select the new model explicitly; no database migration is needed.
