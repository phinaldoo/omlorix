# Memories

Memories are a bounded long-term profile that Omlorix builds from user messages. There are no embeddings and no Memories tool for the chat model to call. Instead, a separate memory-model request runs after each non-empty user message whenever the user's group has Memories enabled.

Omlorix stores the profile in two forms:

- up to 100 atomic facts, which are the editable source of truth; and
- one complete materialized profile assembled from those facts and attached to every later chat request.

Use Notes for longer source material and a project's **System instruction** for project rules.

## Manage memories

Open **Workspace > Memories** and choose a **Memory scope**:

- **Personal memory** belongs to your account.
- A project marked **shared** is visible and editable by every project member.

The profile card shows the complete text supplied to chat models, its version, the active fact count, facts that need review, and the last automatic update status. Select **New Memory** to add one durable fact. Search, confirm, edit, or delete facts as they change. Confirming or editing a fact restarts its age-based lifecycle.

**Import** appears only in a writable shared-project scope whose separate project memory is enabled; personal memory has no direct Import action. Select it to copy the displayed prompt into another AI provider, then paste the returned **Memory JSON**. The input must be one JSON array of at most 100 objects, each with a `date` value in `YYYY-MM-DD` format or `unknown` and a non-empty `content` value of at most 500 characters. Review the preview before **Import memories**. Omlorix reports newly created and deduplicated entries; search for stale or conflicting entries afterward.

The complete account archive under [Data Control](../6_privacy_data/2_data_controls.md) exports and restores personal Memories. It does not carry shared project Memories. Before leaving or deleting a project, separately preserve any project-memory information you are authorized to keep; importing it into another project requires that project's direct JSON workflow and write access.

## Chat behavior

Memory behavior is group-managed. An Owner, Admin, or authorized group manager can enable or disable Memories for a group and choose a dedicated **Memory model**. Leaving the model blank uses the model selected for the chat. Disabling Memories hides **Workspace > Memories**, stops automatic extraction, and stops adding the profile to new model requests; it does not delete stored facts.

The memory request receives the complete current atomic fact set and the new user message, returns schema-validated create/update/confirm/forget candidates, and never receives tools. Passwords, API keys, payment credentials, and similar secrets are rejected rather than stored. A provider failure does not fail the chat; Omlorix retries background work and exposes the last status on the profile card.

Facts receive a stability class. Stable facts are reviewed after 365 days and expire after 1,095; slow-changing facts after 180 and 540; changing facts after 45 and 180; and short-lived facts after 7 and 30. A new supporting message, manual edit, or **Confirm** action restarts those dates. Reads alone do not keep a fact alive. Expired facts are excluded immediately and removed by lifecycle maintenance. An explicit retraction can remove a fact sooner, and a high-value new fact can replace the weakest fact only when the 100-fact collection is full.

A model can ignore, misapply, duplicate, or create an inaccurate Memory. Repeat critical requirements in the current request and review entries regularly. Deleting a Memory does not remove the same information from an earlier chat, Note, export, or external service.

Personal and project Memory can be restricted separately. Group Memories must be enabled to write facts, and a project must also use separate shared memory before its project scope is writable. A project chat receives the user's complete personal profile plus the enabled shared project facts. Only use a shared project scope for information appropriate for every project member.

Deleting a memory removes its text immediately. Background updates from older messages cannot bring that fact back. If you state the information again in a newer message, it may be saved as a new memory.
