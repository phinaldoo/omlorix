# Realtime Call Settings

**Admin Settings > Models > Realtime call settings** configures low-latency voice conversations.

## Configure a Realtime Model

1. Turn on **Enable realtime conversations**.
2. Choose **Realtime provider**, **Realtime model**, and **Default voice**.
3. Choose **Realtime tools** and the displayed provider-specific options.
4. Save and test with an ordinary user.

An empty **Realtime tools** selection disables tool use for realtime sessions. For a selected tool to work, the user must also have access to the realtime feature, the model, the tool, and any group or service connection involved.

**Input transcription** and **Output transcription** control speech transcript updates. Other providers can expose **Realtime temperature**, **Realtime max output tokens**, **Speech language code**, **Session resumption**, **Context compression**, turn and activity handling, sensitivity, silence timing, **Affective dialog**, or **Proactive audio**. Only use the options displayed for the selected provider, and test how interruption, transcription, and long-session context behave together.

## Safety and Capacity

Realtime sessions can continuously send audio, transcripts, tool arguments, and tool results. Confirm provider terms, regional processing, recording expectations, and consent requirements before rollout.

Test:

- microphone permission and connection setup
- interruption and reconnect behavior
- long-session cost and provider limits
- tool confirmation and failure behavior
- access removal while group membership changes
- the applicable [Rate Limit](16_rate_limits.md) for calls and minutes

Keep the allowed tool list narrow. Do not enable tools with high-impact actions until their authentication, confirmation, and audit behavior has been reviewed.

Realtime calls are separate from [Dictation settings](15_1_dictation_settings.md) and [Read aloud settings](15_2_text_to_speech_settings.md).
