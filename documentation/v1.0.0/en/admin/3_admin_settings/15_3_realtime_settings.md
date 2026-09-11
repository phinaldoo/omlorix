# Realtime Call Settings

**Admin Settings > Models > Realtime call settings** configures low-latency voice conversations.

## Configure a Realtime Model

1. Turn on **Enable realtime conversations**.
2. Choose **Realtime provider**, **Realtime model**, and **Default voice**.
3. Choose **Realtime tools** and the displayed provider-specific options.
4. Save and test with an ordinary user.

An empty **Realtime tools** selection disables tool use for realtime sessions. For a selected tool to work, the user must also have access to the realtime feature, the model, the tool, and any group or service connection involved.

**Input transcription** and **Output transcription** control speech transcript updates. Other providers can expose **Realtime temperature**, **Speech language code**, **Session resumption**, **Context compression**, turn and activity handling, sensitivity, silence timing, **Affective dialog**, or **Proactive audio**. Only use the options displayed for the selected provider, and test how interruption, transcription, and long-session context behave together.

## GPT-Live 1

Select an OpenAI provider and `gpt-live-1`. Choose a Live voice and a **Reasoning model** for managed Responses delegation. Voice instructions are kept separate from the Agent, Skill, memory, and tool instructions sent to that backend model. The same tool allow-list and user authorization checks apply. Existing Realtime models remain available with their own controls.

GPT-Live uses `/v1/live/sessions`, not the Realtime API. Omlorix exchanges the browser's completed ICE offer on the server and attaches an authenticated sideband connection for lifecycle enforcement and usage. API credentials remain on the server. Calls become ready on `session.started`; closing drains tool results and final events with a bounded timeout.

Captions are continuous and may overlap. Omlorix saves timed caption windows rather than treating delegated `response.completed` events as spoken-turn boundaries. The newest 32 text messages, further limited to 6000 UTF-8 bytes, provide startup history. Typed input and attached images go to the delegated backend, not directly to the voice model. Audio is not stored by Omlorix; provider-side session storage is disabled.

Voice costs $0.05/minute, billed per second with the credited 15-second WebRTC initialization minimum. Delegated model tokens and tool usage are separate. Voice duration and backend response facts come from the authenticated sideband, with finalization status retained in analytics metadata. Application call/minute limits still use elapsed session time, not token charges. Abnormal disconnects can leave the final caption window or usage incomplete; provider invoices remain authoritative.

See the [Live guide](https://developers.openai.com/api/docs/guides/live), [delegation contract](https://developers.openai.com/api/docs/guides/live-delegation), and [pricing](https://developers.openai.com/api/docs/pricing#live-session-duration).

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
