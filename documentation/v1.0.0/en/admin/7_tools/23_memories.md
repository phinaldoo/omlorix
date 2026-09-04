# Automatic Memory

Memory is not a model tool. The chat model cannot read or write memory through a tool call. When a group's Memory feature is active, Omlorix queues a separate, tool-free extraction request after every non-empty user message. The selected memory model compares that message with the user's complete current fact set and returns schema-constrained create, update, confirm, or forget candidates.

Atomic facts are capped at 100 per user. They remain the source of truth, while a materialized full profile makes chat attachment a single indexed lookup. Every later chat receives the whole active personal profile; an enabled project-memory scope is added alongside it. No embeddings or similarity lookup are used.

## Enable and test

1. Under **Admin Settings > Groups > Memories**, enable Memories for the intended group.
2. Choose a dedicated inexpensive completion model, or leave **Memory model** empty to use each chat's current model.
3. Send a harmless message containing a reusable personal fact, then verify the automatic status, atomic fact, and full profile under **Workspace > Memories**.
4. Send a correction and verify that the existing semantic fact is updated rather than duplicated.
5. Disable Memories for a test group and verify that the Workspace page is hidden, extraction stops, and new chat requests omit memory context.

Production deployments process these requests through the dedicated durable Memory Worker with encrypted job payloads, idempotency, and retries. Redis-off and local installations use the same database queue; local mode starts a bounded consumer inside the API process. The source message and accepted memory job commit in one transaction. Provider errors do not fail the primary chat and the Memories page reports the last run status without exposing credentials or raw provider errors.

The lifecycle marks facts for review and later deletes them according to their stability class. Supporting evidence, a manual edit, or explicit confirmation refreshes the lifecycle; merely reading a fact does not. Secret candidates are never stored. Users can inspect, confirm, edit, and delete facts in the Workspace, and personal memories remain covered by account export/import and account deletion.

Memory extraction uses a dedicated worker queue in managed deployments, with independent concurrency and no Redis requirement. Local development keeps pending work in the durable database queue and only claims the configured concurrent batch. Sources older than 24 hours are rejected, including jobs that finish model processing after that deadline. Deletion removes fact content immediately and retains only a short-lived semantic-key/ID/version/timestamp guard so older jobs cannot restore it. A newer statement can create a new fact. These operational guards are excluded from portable memory exports; full-instance backups preserve them alongside queue state. See [worker configuration](../2_setup/7_environment_variables.md#dedicated-workers).

Facts, profiles, and processing status have separate lifecycles. `memories` holds facts; `memory_states` tracks the fact revision and operational status; `memory_profiles` is a disposable projection with its source revision and next validity boundary. Reads use a profile only when both the revision and time boundary are valid, otherwise they derive context from live facts without writing during the read. Recording a failed extraction never creates an empty profile that hides existing facts. Maintenance rebuilds invalid projections in bounded batches. Legacy facts are preserved during migration, including scopes already above the current write limit; their context projection remains bounded.

Portable user/project archives contain facts, not processing status or derived profiles. Full database backups preserve state, deletion guards, and queued work. Restored or imported facts rebuild projections through the same service boundary. See [generation and workspace architecture](../../architecture/1_generation_memory_workspace.md) for the shared execution and read-model contracts.
