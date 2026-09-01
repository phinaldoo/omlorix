# OpenRouter

**OpenRouter** provides access to models routed across multiple upstream providers. Availability, capabilities, data handling, and price can depend on both OpenRouter and the selected route.

Apply [Common Provider Settings](2_provider_settings.md) for shared credential, discovery, and lifecycle rules.

## Configure

1. Create a dedicated OpenRouter API key with budgets and alerts.
2. Create an **OpenRouter** provider, enter the **API key**, and optionally set the attribution fields shown in the form.
3. Select **Test Connection**, save, and create a model from a discovered model.
4. Configure provider routing only when you have reviewed the eligible upstream providers and fallback behavior.
5. Test text, files, tools, reasoning, and any speech or media feature separately.

Model metadata is not a guarantee that every route supports the same capabilities. Restrict routing when consistent region, privacy, retention, or feature support matters. Route fallbacks can change the processor, performance, output, and cost.

Optional features such as [Image Generation](../7_tools/6_image_generation.md), [Video Generation](../7_tools/7_video_generation.md), and [Audio Generation](../7_tools/8_audio_generation.md) require separate setup and supported routed models.

Use OpenRouter's activity and billing views as the authority. If a model lists but requests fail, check account balance, route availability, data-policy restrictions, and the exact model features enabled in Omlorix. Recheck the selected upstream routes after catalog or privacy-policy changes.
