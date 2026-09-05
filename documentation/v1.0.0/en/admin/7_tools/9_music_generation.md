# Music Generation

**Music Generation** lets a chat model create a music track through a configured Google AI Studio music model. Supported requests can include a description, lyrics, and approved current-chat images.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then verify the music-specific options below.

## Configure

1. Create a [Google AI Studio](../5_llmprovider/8_google_aistudio.md) provider with billing and access to a supported music model.
2. Open **Admin Settings > Tools > Music Generation** and select the provider and model.
3. Review the visible duration, output, lyrics, reference-image, timeout, and cost settings.
4. Save and select **Music Generation** on each allowed chat model.
5. Test an instrumental request, a lyrics request, and reference images if enabled.

Model availability, duration, output format, image guidance, and language support can vary by account, region, and preview status. A model appearing in the form does not prove entitlement.

As of September 5, 2026, **Lyria 3.5** (`lyria-3.5`) is available alongside the existing Lyria 3 models. It generates full songs with lyrics and 44.1 kHz stereo MP3 audio, accepts up to ten reference images, and costs $0.08 per generated song at Google's published list price. Describe the desired duration and structure in the prompt. Omlorix uses the Interactions API with server-side interaction storage disabled, extracts audio and lyrics from model-output steps, and saves the result through the existing generated-file workflow. Lyria 3.5 always uses MP3, including when an older saved configuration requests WAV. See Google's [music guide](https://ai.google.dev/gemini-api/docs/music-generation) and [pricing](https://ai.google.dev/gemini-api/docs/pricing).

Generated music is stored as a user file and counts toward storage limits. Prompts, lyrics, and reference images are sent to Google. Establish policies for copyrighted text, artist imitation, voice likeness, personal data, and commercial use. Review provider retention and moderation, apply budgets, and use provider billing as the cost authority.

If references are ignored, verify that the selected model supports them and the images are accessible in the current chat. If output is missing or unplayable, check job status, timeout, provider quota, download access, and file storage.
