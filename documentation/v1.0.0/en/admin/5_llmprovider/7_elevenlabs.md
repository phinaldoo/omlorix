# ElevenLabs

ElevenLabs supplies speech transcription and speech generation. It does not create chat models.

Apply [Common Provider Settings](2_provider_settings.md) for shared credential and lifecycle rules.

Because ElevenLabs is speech-only, it is excluded from **Provider Groups**. Configure it directly in dictation, text-to-speech, or audio-generation settings instead.

## Configure

1. Create a least-privilege ElevenLabs API key and verify account access to the required models and voices.
2. Open **Admin Settings > Providers**, select **Add Provider**, and choose **ElevenLabs**.
3. Enter the **Name** and **API key**, then test and save.
4. Select the **Provider** and **Model** under [Dictation Settings](../3_admin_settings/15_1_dictation_settings.md), [Read Aloud Settings](../3_admin_settings/15_2_text_to_speech_settings.md), or [Audio Generation](../7_tools/8_audio_generation.md).
5. Test with a short recording and a short speech sample before enabling group access.

**Enable logging** controls the provider's logging request where the account supports it. Turning it off requests [ElevenLabs Zero Retention Mode](https://elevenlabs.io/docs/eleven-api/resources/zero-retention-mode), which ElevenLabs currently limits to eligible enterprise customers; verify in provider request history that it was applied. It does not delete Omlorix files, transcripts, generated audio, statistics, or audit records. Confirm the provider plan and retention terms rather than assuming zero retention.

If models or voices are missing, check account entitlement, region, quota, and provider status. If transcription works but speech does not, verify that the selected model supports the chosen voice and output format.
