# Generation, memory, and workspace architecture

## Shared generation lifecycle

All six chat protocols enter `GenerationEngine`: OpenAI Responses, Chat Completions, Anthropic, Google AI Studio, Ollama, and OpenRouter Responses. Adapters encode native requests and decode native events. They yield `ProviderCall` and `ToolCall` effects; the engine admits requests, enforces the generation-wide tool ceiling, releases clean database transactions before provider I/O and stream waits, drives tool generators, attaches context diagnostics to persistence, and closes resources on completion, error, or disconnect. Protocol-specific reasoning, citations, media, usage, and partial-response encoding remain in adapters. Provider retry/fallback handlers receive effect failures through the generator rather than losing their native recovery semantics.

The public provider entry points and frontend stream protocol remain compatible. Synchronous SDKs still use the existing interruptible stream bridge and its bounded backpressure. This is a modular backend, with independently deployed workers for slow work; it does not require a separate service per protocol.

Stored OpenAI response-ID continuation is no longer used for chat admission: its inherited server-side input is opaque to the local budget. Native transcript replay includes encrypted reasoning when available and permits complete old turns to be removed safely. This can increase replay costs compared with response-ID continuation; provider prompt caching remains available. Legacy continuation metadata is readable but does not bypass the new admission boundary.

## Tool result contract

`ToolResult` separates current-round `model_content`, bounded `history_receipt`, artifact IDs/revisions, UI payload, file channels, and metadata. Feature-owned receipt modules for Notes, Skills, Todos, Automations, and Canvas define the durable representation. The provider helper no longer owns feature-specific compaction rules. Rich bodies remain available to the current model call; future turns receive IDs, revisions, lengths/digests, operation outcomes, and pagination cursors instead of repeated document bodies. Explicit detail/read tools retrieve current content when required.

Tool workers encode the compatibility mapping at their wire boundary and reconstruct the typed result on receipt. Realtime sends current model content to the provider and persists the receipt. Existing widget and file events keep their native channels. Unknown tools have a bounded generic receipt rather than unbounded history persistence.

## Whole-request context admission

`ContextBuilder` considers system instructions, native tool schemas, workspace context, attached notes, memories, history, the current turn, and the requested output reserve together. It uses the configured model input limit, with a conservative 8,192-token fallback when a limit is unknown. Configure the actual model limits for production and BYOK deployments. Ollama's smaller `num_ctx` is respected and the chosen window is sent to Ollama. Explicit output limits are honored up to the model output cap; absent a requested limit, the reserve is at most 4,096 tokens and one quarter of the window.

Text is estimated using UTF-8 bytes as a conservative upper bound. Native media uses explicit estimates, not exact provider tokenization, so media-heavy requests can still receive provider-side limit errors. A safety allowance is reserved. Budgeting runs before each provider round, including tool follow-ups. It drops optional segments in priority/age order while preserving required instructions, the current turn, and complete tool call/result groups. Required context that cannot fit fails before provider I/O with translated actionable feedback. No required text is silently sliced.

Persisted diagnostics contain segment source, priority, content revision hash, token estimates, and removal counts; they do not retain another copy of the prompt. Prefix manifests keep optional notes and memories separate from required workspace instructions in all protocols.

## Memory consistency and delivery

`memories` is the authoritative fact store. `memory_states` contains fact revision and processing status. `memory_profiles` is a derived projection, valid only when its source revision matches and its next lifecycle transition has not elapsed. Failed runs update status without materializing profiles. Reads fall back to facts when a profile is missing or invalid. Fact writes and profile rebuilds serialize against the owner lock.

The durable memory job is an outbox record in the existing worker table, inserted in the same transaction as the persisted chat/realtime user message. Its idempotency key identifies the source message. Both inline and external modes pull from the dedicated `memory` queue with their own concurrency budget, renewable leases, retries, and 24-hour source expiry. No provider I/O occurs in the source transaction. Temporary requests without a persisted source do not create extraction jobs.

Manual and model-driven forgetting delete content and retain minimal semantic-key/ID/version/time guards. Older extraction cannot recreate the fact, including when forgetting arrives before creation. Guards outlive every accepted source; the age check runs again after model I/O. Full database backups preserve operational state; portable archives export facts and regenerate projections. Migration invalidates old projections and preserves existing fact content.

## Workspace read models and API compatibility

Feature `queries.py` modules project only catalog columns in SQL. Access candidates come from indexed owner/subscriber IDs, and live share tokens are checked on every page and detail read. Duplicate grants do not duplicate list rows. SQL pagination uses a unique ordering key, a page plus one lookahead row, and opaque scope-bound cursors. Access never comes from the cursor. Literal search escapes wildcard characters. New composite indexes support ownership, subscription, and page order paths.

Notes and Todo-list APIs retain their `items`, `limit`, `offset`, and `has_more` fields and add `next_cursor`. Their workspace clients follow cursors. Tools for Notes, Skills, Todos, and Automations return bounded lightweight summaries with `next_cursor`; pass it back with the same filters and without `offset`. Page size is at most 200. Legacy offsets are accepted up to 10,000; use cursors for deeper traversal. Concurrent edits can move an item in a mutable sort order; this is live pagination, not a snapshot archive.

Skills exposes `GET /api/v1/skills/catalog` with `q`, `limit`, `offset`, and `cursor`. Catalog bodies/descriptions are previews, marked `summary_only`. `GET /api/v1/skills/{id}/detail` returns full authorized content and file metadata. The workspace and mention picker use the catalog; opening/editing and file refreshes load detail. Late responses cannot replace a newer selection. The legacy `GET /api/v1/skills` remains a full-detail array but is now bounded to a page (default 50, maximum 200); clients needing traversal should migrate to the catalog.

These changes bound individual requests and isolate memory capacity. They are not evidence of multi-million-user throughput: production sizing still requires representative load tests, provider quotas, database plan measurements, queue-age monitoring, and deployment-specific capacity budgets.
