# Read Aloud Settings

**Admin Settings > Models > Read aloud settings** configures speech playback for assistant messages.

## Playback Options

Choose **Read aloud provider**. Browser-native speech uses voices installed or exposed by the user's browser and operating system. It normally requires no provider credential, but voice quality and availability vary by device.

Provider-backed speech exposes **Read aloud model** and provider-specific settings such as **Voice**, **Audio Format** or **Response Format**, language, sample rate, speed, and latency controls when supported. Configure credentials on [Providers](13_llm_providers.md), then test the exact combination before enabling it for users.

## Operational Guidance

- test a short and long assistant response in each supported browser
- verify the intended language, voice, speed, and audio format
- confirm how generated audio is handled under your organization's data policy
- account for provider cost, quota, outbound access, and regional availability
- keep browser-native playback available as a fallback only if its variability is acceptable

Read aloud turns completed text into audio. It is not the same as [Dictation settings](15_1_dictation_settings.md), which turns audio into text, or [Realtime call settings](15_3_realtime_settings.md), which supports live voice conversations.
