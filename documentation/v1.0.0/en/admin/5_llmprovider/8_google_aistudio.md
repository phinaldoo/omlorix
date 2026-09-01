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
