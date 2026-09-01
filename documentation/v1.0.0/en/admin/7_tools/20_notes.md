# Notes

**Notes** lets a model list, read, create, and edit the signed-in user's persistent Markdown notes in **Workspace > Notes**.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then verify the note-specific privacy and edit behavior below.

## Enable and test

1. Select **Notes** on a reliable tool-calling model.
2. Enable it only for groups that may expose note content to the selected provider.
3. Test listing, reading, creating, full-content editing, and focused snippet editing as a normal user. Also verify that a stale edit is rejected after the Note changes and that a Live read-only subscriber cannot edit it.

Full note text can be sent to the chat provider and may contain sensitive information. Apply group policy, provider review, retention controls, and rate limits accordingly. Users should name the note clearly and verify edits, especially when several notes have similar titles.

The tool cannot delete Notes. A user must delete an owned Note from **Workspace > Notes**, where the normal confirmation applies.

Disabling the tool stops model access but does not delete notes. Notes remain subject to normal account export, retention, and deletion behavior.
