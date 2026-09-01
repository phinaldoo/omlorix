# Chat Interface

The chat interface combines navigation, the selected model, the conversation, and the message box. Controls that your organization or selected model does not allow are hidden or unavailable.

## Navigate and choose a model

Use the sidebar to create or search chats and open enabled Workspace sections. On small screens, the sidebar opens over the conversation.

The chat header shows the current title and selected model. Its menus can provide **Share chat**, **Download Chat as**, **Open Model Settings**, **Temporary chat**, **Split Screen**, and generated files. See [Selecting a Model](../5_models/2_model_select.md).

## Review the conversation

Responses can contain text, citations, files, tool activity, thinking, embedded YouTube players, or interactive content. Review a tool's displayed input, approval request, result, and error before continuing; an external action can succeed even if a later assistant step fails. Once a response is saved, its actions can include **Copy**, feedback, editing, **Regenerate options**, **Read aloud**, **Bookmark**, **Branch chat from this message**, version navigation, and deletion.

When **Show Assistant Message Metadata** is on, a completed response can show its model or provider, timing, token and cache usage, estimated cost, reasoning effort, stop reason, or tool details. Fields vary by model, and cost is an estimate rather than a bill.

Deleting a user message uses **Delete message and below** and removes every later message in that conversation path. Branch instead when you want to preserve the original and explore an alternative.

Always confirm which saved answer is visible before continuing, downloading, or sharing a chat with regenerated responses.

## Use the message box

Write your request and add only the required context. The attachment menu can include **Upload files**, **Quick screenshot**, **Add meeting**, **Choose chats**, **Choose uploaded files**, cloud sources, or media generation. Type `@` to add supported Skills, Notes, Prompts, models, Agents, or Connectors.

Review every chip and file before selecting **Send message**. Selected items can be sent to the AI or tool services used for that request. While a response is active, use **Stop response** or add later work to the [Message Queue](4_message_queue.md). If you leave the chat, an unread marker and **Response ready** notice can lead you back; reopen before resending because Omlorix may reconnect to the existing work.

During a long response, Omlorix asks supported devices to keep the screen awake. The browser can release this when the page is hidden, the device locks, or battery-saving rules apply, and it does not prevent a network interruption.

If a provider connection closes without a successful terminal event, Omlorix reports **Connection interrupted. Please try again.** Partial text is not treated as a completed answer, and queued messages remain paused. Before retrying or regenerating, inspect any visible tool activity: an external action may have completed even though the assistant response did not.

Omlorix keeps separate in-progress text drafts for chats and projects when possible. A draft is browser convenience, not a backup; it may not preserve every selected item and can be lost after storage cleanup, sign-out, or a browser change.

For files and large pastes, see [File Attachments](21_file_attachments.md). For editing and branching, see [Edit, Revise & Branch](../4_chat_conversations/1_regenerate_message.md). For storage behavior, see [Temporary Chats](2_temporary_chats.md).
