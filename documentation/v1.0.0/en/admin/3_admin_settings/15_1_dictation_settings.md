# Dictation Settings

**Admin Settings > Models > Dictation settings** configures transcription for uploaded recordings, meeting recordings, and live chat dictation.

## Choose Providers by Workload

- **File & meeting transcription:** handles uploaded or recorded meeting media and provides the non-live microphone fallback.
- **Live chat dictation:** streams supported message-composer and message-edit microphone recordings first, then falls back to **File & meeting transcription** when live transcription is unavailable.

Turn on **Enable file & meeting transcription** or **Enable live chat dictation**, then select the displayed provider and model. The available choices depend on configured [Providers](13_llm_providers.md) and outbound-access policy.

Provider-specific controls appear after selection. **Transcript delay** trades faster partial text for greater transcript stability on supported live providers. xAI can additionally expose **xAI formatting language**, **xAI endpointing silence (ms)**, **xAI key terms**, **Keep xAI filler words**, Smart Turn controls, and **xAI voice activity threshold**. Change one control at a time and test normal speech, silence, interruptions, accents, and background noise.

## Rollout Checklist

1. Confirm the provider permits the audio content your organization will send.
2. Test a short supported recording for each enabled workload.
3. Test a long recording near the configured upload and request limits.
4. Verify language detection or the chosen language behavior.
5. Trigger a controlled primary failure and confirm fallback behavior.
6. Apply a [Rate Limit](16_rate_limits.md) in transcription minutes where needed.

Fallback can send the same audio to another service. Document that transfer in your privacy and processor records before enabling it.

Dictation is separate from browser or provider speech playback and from low-latency voice conversations. See [Read aloud settings](15_2_text_to_speech_settings.md) and [Realtime call settings](15_3_realtime_settings.md).
