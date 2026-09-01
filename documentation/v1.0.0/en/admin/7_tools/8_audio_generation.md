# Audio Generation

**Audio Generation** lets a chat model create speech through a separate text-to-speech model and save it as a user file. It is distinct from **Read Aloud**, which speaks an existing assistant response.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then verify voices, formats, and consent requirements below.

## Configure

1. Create a supported speech provider and verify model and voice access.
2. Open **Admin Settings > Tools > Audio Generation** and select **Configure Audio Generation**.
3. Choose the provider, model, voice, format, and any supported instruction or multi-speaker settings.
4. Save and select **Audio Generation** on each allowed chat model.
5. Test short speech, the expected language, the longest normal input, and every allowed format or speaker mode.

Providers differ in voices, languages, custom instructions, multi-speaker support, formats, and retention controls. A listed voice can still require a particular account plan or region.

Generated audio is stored as a user file and counts toward storage limits. Text, voice choices, and any pronunciation or speaker instructions are sent to the selected provider.

Obtain consent where voice or personal data is involved, disclose synthetic speech where required, and review impersonation, copyright, accessibility, retention, and provider training policies. Apply budgets and use provider billing as the cost authority.

If settings appear valid but generation fails, confirm that the voice belongs to the selected model, the requested format is supported, the account has quota, and the chat model and group can use **Audio Generation**.
