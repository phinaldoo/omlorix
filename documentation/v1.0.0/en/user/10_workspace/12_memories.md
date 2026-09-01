# Memories

Memories are short saved facts or preferences that can be reused in future chats. Use Notes for longer source material and a project's **System instruction** for project rules.

## Manage memories

Open **Workspace > Memories** and choose a **Memory scope**:

- **Personal memory** belongs to your account.
- A project marked **shared** is visible and editable by every project member.

Select **New Memory** to add one durable fact. Search, edit, or delete entries as they change. Avoid sensitive, temporary, duplicated, or conflicting information.

**Import** appears only in a writable shared-project scope whose separate project memory is enabled; personal memory has no direct Import action. Select it to copy the displayed prompt into another AI provider, then paste the returned **Memory JSON**. The input must be one JSON array of at most 500 objects, each with a `date` value in `YYYY-MM-DD` format or `unknown` and a non-empty `content` value of at most 500 characters. Review the preview before **Import memories**. Omlorix reports newly created and deduplicated entries; search for stale or conflicting entries afterward.

The complete account archive under [Data Control](../6_privacy_data/2_data_controls.md) exports and restores personal Memories. It does not carry shared project Memories. Before leaving or deleting a project, separately preserve any project-memory information you are authorized to keep; importing it into another project requires that project's direct JSON workflow and write access.

## Chat behavior

The settings on this page and under **Settings > Memory** control **Enable memory**, **Include memories in chats**, and **Auto-create memories**. See [Memory Settings](../3_user_settings/11_memory_settings.md).

A model can ignore, misapply, duplicate, or create an inaccurate Memory. Repeat critical requirements in the current request and review entries regularly. Deleting a Memory does not remove the same information from an earlier chat, Note, export, or external service.

Personal and project Memory can be enabled or restricted separately. **Enable memory** must be on to write Memories, and a project must also use separate shared memory before its scope is writable. Only use a shared project scope for information appropriate for every project member.
