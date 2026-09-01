# Omlorix Features

This catalog summarizes the implemented Omlorix product: the web application, administration tools, integrations, data controls, deployment options, Server Launcher, and `omlorix-server` CLI. It describes available behavior rather than planned work or internal implementation details.

Sections and feature names are sorted alphabetically.

## Administration and Governance

| Feature | Description |
| --- | --- |
| Activity dashboard | Summarize users, chats, pending accounts, system activity, and operational state for administrators. |
| Branding and login customization | Configure application names, light/dark logos, icons, favicons, login backgrounds, branding copy, login layouts, and color themes. |
| Built-in tool administration | Configure code-execution output limits, presentation models, custom/native deep-research behavior, weather backends, and provider-specific search or scrape choices. |
| Deleted-user lifecycle | Review pending deletions, restore users, cancel scheduled deletion, or permanently hard-delete accounts and associated data. |
| Group administration | Create, edit, duplicate, delete, import, and export hierarchical groups; configure owners, managers, coordinators, members, context files, independent group settings, and policies. |
| IP policy administration | Configure IP or country allow/block rules, manage individual blocked addresses, and validate proxy-aware restriction settings. |
| Legal content management | Edit versioned privacy policies, terms of service, notices, and acceptance requirements. |
| Media-generation administration | Select and configure image, video, audio, and music providers, models, voices, formats, reference-file behavior, and generation defaults. |
| Secret rotation | Rotate server-side secret material with audit logging and without exposing stored credentials in API responses. |
| Server bootstrap and settings management | Complete one-time server setup, edit schema-driven settings, and reset defaults. |
| Service connection registry | Configure weighted code-execution, LaTeX, and slide-rendering service endpoints and refresh their availability or health status. |
| User account administration | Create and activate users; change roles, profiles, passwords, 2FA state, account settings, and deletion state. |
| User bulk provisioning | Download CSV/XLSX templates, validate bulk user files, create users in batches, and import or export user directories. |
| User content inspection | Let authorized administrators inspect a user's profile, chats, and messages for support and moderation workflows. |
| User data transfer administration | Export or restore canonical one-user and all-users archives containing the account profile, every retained chat with bookmarks and embedded Subagent/Deep Research history, and supported user-owned content. Coverage distinguishes restorable, export-only, instance-owned, and excluded security state. Imported Skill and ordinary-folder share identifiers—and Note share identifiers restored by an administrator—require explicit review and cross-instance regeneration. |
| User notification administration | Create, target, update, list, and delete platform notifications for everyone, selected users, or selected groups. |
| User policy settings | Apply schema-driven per-user access, model, upload, sharing, feature, and preference policies. |
| Web-search administration | Create, validate, update, delete, import, and export search/scrape provider configurations with masked secrets. |

## Agents, Skills, MCP, and Extensibility

| Feature | Description |
| --- | --- |
| Agent asset library | Upload new agent assets, attach existing user files, reuse inherited shared files, list assets, and remove them. |
| Agent authoring | Create, edit, list, and delete agents with private instructions, base models, skills, icons, access controls, attachments, and model-picker integration. |
| Agent sharing and cloning | Create share links, invite collaborators, preview or accept shared agents, clone them, revoke links, and unsubscribe. |
| Custom Python tools | Create, edit, test, enable, import, export, and delete administrator-defined Python tools executed by a timeout-bounded subprocess with the application service's privileges; the runner is not a security sandbox. |
| Managed skill catalog | Maintain centrally managed, read-only-for-users skills with administrator pagination, import/export, and file controls. |
| MCP app frames and widgets | Create sandboxed MCP application/widget frames, proxy frame resources, refresh frame tokens, and render interactive tool output. |
| MCP OAuth | Discover OAuth metadata, start user or admin MCP authorization, process callbacks, and store refreshed credentials securely. |
| MCP resources, prompts, and templates | List and read bounded text or binary MCP resources, keep invalid content distinct from server health, restrict app frames to supported HTML/XHTML MIME types, list resource templates and prompts, and invoke them during model sessions. |
| MCP server administration | Create, test, update, delete, import, export, and inspect globally managed remote MCP servers over streamable HTTP or SSE, with namespaces, tool allowlists, timeouts, header secrets, and optional OAuth. |
| MCP tool discovery and calls | Discover namespaced MCP tools, preview availability, enforce per-model constraints, and execute structured calls. |
| Personal MCP servers | Let users create, test, update, delete, and inspect their own MCP servers when policy permits. |
| Skill authoring and drafts | Create and edit reusable instruction skills with compatibility, license, and metadata fields; save in-progress drafts and preview them before publishing. |
| Skill file bundles | Manage the `scripts`, `references`, and `assets` folders for personal and managed skills with scoped upload/list/delete operations. |
| Skill import and export | Import packaged skills, Markdown definitions with supporting files, or explicitly reviewed unverified URL payloads, and export portable skill catalogs. |
| Skill sharing and cloning | Share skills by link or invitation, preview and accept them, clone them, revoke links, and unsubscribe. |
| Subagent execution | Launch accessible models or saved Agents as tools, stream their activity in the parent response, and persist completed state, events, results, failures, and artifacts in the containing chat. |

## Authentication, Identity, and Accounts

| Feature | Description |
| --- | --- |
| Access-status checks | Report lockout, scheduled-access, password-change, legal-acceptance, and step-up requirements before protected actions. |
| Account security emails | Send localized notices for new-device sign-in, password, email, passkey, two-factor, social-identity, deactivation, and deletion events. |
| Account signup and sign-in | Support configurable registration, email/password sign-in, sign-out, verified-email handling, and disabled-login modes. |
| Account slots | Keep multiple signed-in accounts, list available slots, switch the active account, and remove individual slots. |
| Active session management | List current logins with minimized metadata and revoke individual sessions or all sessions affected by credential changes. |
| Email address changes | Keep the current address authoritative until the new address is verified; notify both addresses, offer a cancellation link to the old address, and revoke sessions and reset links after confirmation. |
| Enterprise SSO | Support one SAML configuration, one OIDC configuration, and optional sign-in domain restrictions. |
| External identity linking | Link or unlink social identities from an existing account and safely synchronize optional provider profile pictures. |
| Forced password changes | Require password replacement before application access when an administrator or policy marks a credential as temporary. |
| Just-in-time identity provisioning | Create or link users during LDAP, SAML, or OIDC sign-in and synchronize allowed profile, group, and role fields. |
| LDAP authentication | Configure directory searches, SSL/StartTLS, CA certificates, timeouts, user/group mappings, required groups, and profile synchronization. |
| Login and password policy | Configure sign-in/signup availability, allowed signup domains, default roles/groups, password complexity, and failed-attempt lockouts. |
| OAuth social login | Sign in with Apple, GitHub.com or one self-hosted GitHub Enterprise Server, Google, Microsoft, or Slack, including allowed domains/workspaces and provider-specific signup policy. |
| Passkeys | Register, authenticate with, list, and remove WebAuthn passkeys. |
| Password lifecycle | Set a password for federated accounts, change an existing password, and enforce current password policy. |
| Password reset | Request, validate, and complete time-limited email reset links through the durable email outbox and configured SMTP delivery. |
| Recent-authentication step-up | Require a fresh password, OTP, TOTP, or passkey challenge before sensitive account and security changes. |
| Refresh-token rotation | Maintain long-lived sessions with rotating refresh-token families, reuse protection, revocation, and secure cookies. |
| Role-based access control | Separate protected instance-owner, administrator, user, pending-user, and delegated group-management authority while preventing external identity sources from granting administrative roles. |
| SCIM 2.0 provisioning | Expose discovery schemas plus paginated Users and Groups create/read/replace/patch/delete APIs for directory synchronization. |
| Terms and privacy gates | Require configured privacy/terms acknowledgement during signup or access and track accepted document revisions. |
| TOTP and one-time-code 2FA | Enroll or deactivate 2FA, issue setup material, throttle challenges, enforce expiry/attempt limits, and support administrator reset. |

## Automations, Notifications, and Productivity

| Feature | Description |
| --- | --- |
| Automation context | Save a prompt and attach a model, skill, files, notes, and external connections to each automation. |
| Automation import and export | Backend archive support and the complete account archive can move supported automation definitions, remap recreated personal MCP servers, and flag unavailable MCP selections for review. The Automations page has no item-level import or export actions. |
| Automation lifecycle | Create, inspect, edit, activate, pause, and delete saved automation jobs. |
| Automation scheduling | Run once at a chosen time or recur on selected days and local times through timezone-aware scheduler and worker processes. |
| Notification inbox | List in-app notifications, expose unread state, mark items seen, and remove handled share invitations. |
| Notification webhooks (administrator) | Deliver configured administrator notifications to external webhook URLs without exposing secrets. |
| Todo bulk operations and search | Backend and model-tool operations can search, update, or reorder multiple todo items. The Todo page has no bulk-selection mode or bulk action menu. |
| Todo item details | Track notes, priorities, due dates and all-day deadlines, todo/doing/done status, subtasks, links, attachments, tags, and custom ordering. |
| Todo lists | Create, rename, delete, and populate multiple todo lists in the web UI. Supported Todo data is portable through the complete account archive, but the Todo page has no list-level import or export actions. |
| Todo state and marked queue | Complete/reopen tasks, mark important items, and retrieve a dedicated marked-task queue. |
| Webhook delivery history | Record delivery status, response codes, errors, request metadata, and the chat created by each trigger. |
| Webhook request hardening | Authenticate secret headers, enforce payload limits and rate limits, support idempotency keys, and filter sensitive headers or fields. |
| Webhook triggers | Generate and rotate trigger URLs/secrets, map or append payload data to prompts, include selected headers, and enable or disable triggers. |

## Chat and Conversation Experience

| Feature | Description |
| --- | --- |
| Archived chats | Archive and unarchive conversations and browse a dedicated archived-chat view. |
| Assistant response metadata | Inspect provider/model identity, timing, token/cache usage, cost-related fields, reasoning effort, stop reason, and tool metadata. |
| Bookmarks | Bookmark user or assistant messages, filter and sort bookmarks, jump back to the source chat, and remove bookmarks. |
| Branching and duplication | Fork a conversation from a message or duplicate a whole chat to explore an alternative direction. |
| Chat attention and read state | Track unread/attention state, mark chats read, and query conversations that need attention. |
| Chat downloads | Download a conversation as JSON, plain text, Markdown, PDF, or Microsoft Word (DOCX) from the main view or either split-screen panel. |
| Chat list management | Page, search, filter, rename, delete, and drag/reorder chat entries. |
| Chat project assignment | Move a conversation into or out of a project and initialize project-aware chat context. |
| Chat references | Search chats and uploaded files, attach selected prior conversations as context, and show reference counts. |
| Code-block actions | Copy or download code, run supported Python snippets in the sandbox, and preview supported structured output such as Vega specs. |
| Composer attachments | Add local uploads, workspace files, cloud files, chat references, screenshots, or meeting media with model-format checks. |
| Composer drafts and large pastes | Preserve in-progress composer state and convert oversized pasted content into manageable attachments. |
| Composer mentions | Search and attach skills, notes, prompt templates, models or agents, and eligible MCP connectors with `@`; preserve those choices in queued and split-screen sends. |
| Context quick picker | Search recent chats and files from the composer or open the full picker for a larger result set. |
| Generation cancellation and recovery | Poll generation state, stop in-flight work, reattach to an existing stream, recover persisted tool-call output, and reject provider streams that close without a terminal success event. |
| Meeting transcription | Upload audio/video or record microphone/screen audio, require participant consent plus legal-basis and retention metadata, transcribe through the configured provider, and save a Markdown transcript into a chat or project. |
| Message editing and deletion | Copy user or assistant messages, edit user messages, delete an assistant response, or delete a user message and everything below it before regenerating. |
| Message feedback | Rate model responses and submit structured feedback for later administrator analysis. |
| Message queue | Queue attachment-aware prompts, pause/resume, reorder, edit, remove, retry, or send the next item while another response runs. |
| Model switching safeguards | Change models per chat while warning about unsupported historic attachments or capabilities. |
| Per-chat generation settings | Use schema-driven provider controls for sampling, token limits, structured output, reasoning/quick-thinking, tools, and other request options, with reusable presets and independent split-screen values. |
| Pinned chats | Pin/unpin conversations and explicitly reorder the pinned list. |
| Response regeneration and revision | Regenerate an assistant answer or edit a user message and generate again from that point when group policy permits those actions. |
| Rich Markdown rendering | Render tables, task lists, alerts, code highlighting, KaTeX math, Mermaid diagrams, Vega/Vega-Lite charts, and safe links. |
| Screen capture | Capture a quick screenshot from the composer and validate it against the selected model's image support. |
| Split-screen conversations | Run two independent chats side by side, select models/settings per panel, send to one or both, resize panels, and drag chats between sides. |
| Streaming responses | Stream text, thinking, nested generation, tools, citations, media placeholders, and terminal events with interruption handling. |
| Temporary chats | Start non-persistent or retention-limited conversations, save them explicitly, and optionally make temporary mode the default. |
| Title generation | Generate conversation titles from the first message using the current or a configured title model and instruction. |
| Tool-call lifecycle | Display live inputs, approvals, results, errors, created artifacts, and follow-up calls for built-in, MCP, and custom tools. |
| YouTube embeds | Recognize safe YouTube links and render embedded video players inside messages. |

## Collaboration, Sharing, and Workspaces

| Feature | Description |
| --- | --- |
| Canvas sharing | Publish generated artifacts with expiring, optionally password-protected links; change credentials/expiry or revoke access later. |
| Chat sharing | Share chats by link or invitation with public/private access modes, expiry, password rotation, file access, and revocation. |
| Collaboration invitations | Discover shareable public users and send targeted invitations for chats, projects, folders, notes, prompts, skills, agents, and todo lists. |
| File-folder sharing | Share folders, preview their visible files, accept or clone them, invite members, revoke links, and unsubscribe. |
| Group access windows | Allow or block access during timezone-aware schedules, apply presets, and preview the next accessible or blocked period. |
| Group chat-retention policies | Auto-delete chats after a configured age and separately govern shadow-deleted and saved-temporary-chat retention windows. |
| Group compliance notices | Show a configurable chat-box warning and apply a group-defined watermark when compliance policy requires it. |
| Group context and defaults | Supply group-level instructions/context files and default access or feature policies to members. |
| Group feature entitlements | Enable or restrict chats, files, projects, automations, notes, todos, prompts, memories, skills, agents, BYOK, MCP/connections, sharing, and message actions. |
| Group portability | Duplicate group configuration or import/export validated group definitions for reuse and migration. |
| Group upload quotas | Limit whether members may upload files, how many files they may upload, and their maximum aggregate storage. |
| Managed groups | Let delegated group managers edit permitted settings, find/promote manager candidates, and view their managed groups. |
| Notes history and restore | Browse individual note revisions, preview historic content, and restore a selected version. |
| Notes sharing | Share notes from the Notes menu by link or invitation, choose clone, live-share, or collaboration access, accept or clone received notes, revoke links, and unsubscribe. |
| Notes workspace | Create, edit, search, download, and delete Markdown notes. Add file references or audio from the editor's More actions menu; Notes-only import/export and workspace-assignment actions are not available in the web UI. |
| Personal and project memories | Create, edit, and delete memories through one scope-aware workflow, import or export shared project memories, preserve personal memories in account archives, and control context inclusion or automatic creation. |
| Project membership | Invite users, join through a link, list or remove members, leave a project, and revoke project sharing. |
| Project workspaces | Organize chats and persistent context files inside named/iconized projects with project system instructions, optional isolated memories, detail views, and cleanup on deletion. |
| Prompt library | Create, edit, delete, improve, share, invite, accept, clone, and unsubscribe from reusable prompt templates. |
| Shared-item dashboard | Aggregate existing owned and received chats, canvas artifacts, projects, folders, notes, prompts, skills, agents, and todos in one settings view. The dashboard manages existing share records; it does not create shares. |
| Shared-resource subscriptions | Preserve membership state for accepted resources and provide consistent accept, clone, decline/revoke, and unsubscribe flows. |
| Temporary group accounts | Batch-create expiring accounts for a group, revoke them, and apply separate retention/deletion policy after expiry. |
| Todo-list sharing | Share lists by link or invitation, preview/accept or clone them, revoke links, and unsubscribe with permission-aware task access. |
| Workspace home | Show a personalized workspace overview and recent or relevant resources across Omlorix. |
| Workspace navigation | Browse dedicated views for files, folders, projects, notes, prompts, skills, agents, todos, automations, connections, and shared items. |

## Content Creation, Research, and Built-in Tools

| Feature | Description |
| --- | --- |
| Audio generation | Generate spoken or other audio with configured provider/model, voice, language, format, bitrate, sample-rate, speed, and latency options. |
| Automation tool | Let models inspect eligible models, skills, and connectors and then list, create, edit, activate, pause, or delete automations and manage their schedule settings. Existing webhook summaries are readable, but users manage webhook triggers themselves. |
| Built-in tool governance | Enable tools per model/group, expose translated tool descriptions, and apply tool-specific quotas and access checks. |
| Canvas artifacts | Create model-generated Markdown, HTML, Mermaid, CSV, and LaTeX artifacts; view, edit, save, copy, download, and reopen them; and open compatible existing tabular or workbook files in Canvas. |
| Canvas HTML preview | Render generated websites in a sandbox with opt-in interactions, controlled external connections, and persistent preview storage. |
| Canvas Markdown editor | Switch between rich editing and Markdown source with formatting, tables, links, images, slash commands, history, and PDF export. |
| Canvas spreadsheet editor | Edit CSV, TSV, XLS, and XLSX workbooks with sheets, formulas, rows/columns, find, undo/redo, selection statistics, and safe compatibility mode. |
| Code execution | Run code in a sandboxed external service with bounded output and render returned files or execution results in chat. |
| Deep research | Run custom multi-stage or provider-native research with planning, source collection, evidence audit, revisions, quality gates, live activity, and cancellation. |
| Deep-research artifacts | Browse sources and generated files, download individual artifacts, and export the final report as Markdown or PDF. |
| Flashcards | Generate interactive study-card widgets from chat context. |
| Image generation | Generate images with provider/model-specific sizes, options, and reference inputs where supported. |
| LaTeX documents and PDFs | Edit LaTeX canvas source, compile through a configured rendering service, preview results, and download PDFs. |
| Memory context and tool | Supply recent memories from the effective personal or project scope as model context, and let models save a new memory when automatic memory creation is enabled. The tool does not list existing memories. |
| Music generation | Generate music with configured provider/model, output format, and optional reference images. |
| Notes tool | Let models list, view, create, and edit notes while enforcing shared-item permissions and stale-edit checks. Users delete Notes themselves in Workspace. |
| Quiz | Generate interactive quiz widgets from chat context. |
| Skill tool | Let models list or read owned skills and prepare reviewable skill drafts with optional starter files without saving them until the user confirms. |
| Slide presentation exports | Download generated PowerPoint files or retrieve a deck as per-slide images, a ZIP archive, or a PDF. |
| Slide presentations | Generate slide decks, reopen them by file, edit revisioned HTML source, render drafts, and retrieve individual slides. |
| Todo tool | Let models list, view, search, create, update, reorder, complete, reopen, move, or tag permitted todo lists and tasks, including non-destructive bulk changes. Users delete tasks and lists themselves on the Todo page. |
| Video generation | Generate videos with provider/model-specific duration, size, polling, retry, and optional reference-file controls. |
| Visualization | Generate interactive chart, map, mind-map, table, metric, hierarchy, and other structured visualization widgets. |
| Weather data | Resolve locations and fetch current conditions or forecasts through Open-Meteo or OpenWeatherMap. |
| Web search and scraping | Combine search, image search, page scraping, robots-aware retrieval, domain filters, and citations through configurable providers. |
| Web-search provider ecosystem | Support AIOHTTP, Crawl4AI, custom adapters, DuckDuckGo, Exa, Firecrawl, Ollama, Perplexity, SearXNG, Serper, Tavily, and You.com across their configured search, image-search, scrape, or combined roles. |

## Data Controls, Backups, and Portability

| Feature | Description |
| --- | --- |
| Administrative export jobs | Create, page, inspect, download, and delete asynchronous user or chat exports without holding long API requests open. |
| Backup capability discovery | Report supported destinations and backup/restore behavior before an operator starts a job. |
| Backup destinations | Configure, test, update, and remove local, S3-compatible, Google Cloud Storage, Azure Blob Storage, or WebDAV destinations with masked credentials and destination-specific validation. |
| Backup encryption | Encrypt archives, protect destination credentials, and avoid returning sensitive backup URIs or keys in normal API responses. |
| Backup job lifecycle | Create, list, inspect, verify, download, retain, and delete backup artifacts with paginated history. |
| Backup schedules | Create, edit, delete, and run recurring backup schedules immediately. |
| Chat interoperability | Let users import validated ChatGPT ZIP exports with branch, attachment, and duplicate handling; let administrators import Open WebUI archives; and export Omlorix conversations for migration or personal use. |
| Data erasure cleanup | Remove owned data, files, sessions, shares, references, statistics, and policy-governed audit/auth records during hard deletion. Existing full-instance backup archives remain governed by their configured retention lifecycle. |
| Entity-level portability | Canonical user archives round-trip retained chats and bookmarks, including embedded Subagent and Deep Research run histories, plus owned files/folders, projects, Note history, Todo lists, Skills/assets, Agents, Prompts, Automations, slide presentations, personal Memories, connection metadata, MCP definitions, model presets, and portable settings. Project memberships and shared project Memories are absent. Group/authentication metadata, activity logs, feedback, usage statistics, and shared Agent subscriptions are export-only; Notifications remain instance-owned. |
| File-backend migration | Move stored files between local, S3-compatible, GCS, Azure Blob, and WebDAV backends with dry-run and provenance options. |
| Full user bundles | Transfer a user's profile and supported content through streamed self-service or administrator-managed bundles with a coverage manifest. Exclude passwords and reset/session credentials, social bindings, OAuth handshake and authoritative SCIM provisioning state, instance-managed notifications, queued email, pending email/authentication proofs, trusted-device notification markers, reusable connection/MCP credentials, and browser-local BYOK setup. Require review of imported Skill and ordinary-folder share identifiers, administrator-restored Note share identifiers, and retained SSO/LDAP linkage metadata. |
| Open WebUI bulk migration | Import one or many Open WebUI chat archives through audited administrator workflows. |
| Public legal documents | Serve legal-document availability, privacy policy, terms, disclosure metadata, revision state, and language metadata to public clients. |
| Restore workflows | Restore an empty or explicitly confirmed in-place server from a job/URI or uploaded archive with preflight limits, tracked state, pre-restore backup, and rollback safeguards. |
| Self-service data controls | Let users export/import their data, delete chats or files, reset settings, request account deletion, and understand retention consequences. |
| Settings and catalog portability | Move server settings and managed catalogs through validated import/export formats while masking or excluding secrets. |
| Streamed large exports | Stream full chat or data archives to keep memory use bounded for large accounts. |
| User-deletion confirmation | Use the standard accessible confirmation dialog for administrator-initiated user deletion and permanent deletion, without a separate policy-preview request. |
| User-deletion retention | Support immediate or delayed deletion, block access during the retention window, and automatically purge expired accounts. |

## Deployment, Infrastructure, and Platform Security

| Feature | Description |
| --- | --- |
| API diagnostics | Expose readiness/health, version, client-IP, and proxy-verification endpoints for operators and load balancers. |
| Automated database migrations | Run main and audit schema migrations before application startup and through the dedicated Compose migration service. |
| Bundled or external data services | Use bundled PostgreSQL, Redis, PgBouncer, and MinIO or connect operator-managed equivalents. |
| Containerized services | Deploy the web frontend, application API, migrations, email worker, durable workload workers, realtime gateway, automation scheduler, data services, and optional infrastructure through Compose. |
| Content Security Policy | Apply hardened production CSP, share-page CSP, safe frame policies, and restricted YouTube/canvas exceptions. |
| CORS, origin, and host validation | Dynamically restrict CORS, same-origin auth flows, Host headers, trusted private origins, and public URL candidates. |
| Database migration validation and repair | Run main and audit migrations in order, verify Alembic heads, and repair validated revision state when needed. |
| Deployment profiles | Select single-server, single-server-plus, externally managed service, managed-cloud, or development profiles. |
| Distributed workers and realtime gateway | Isolate email delivery, operations, LLM generation, Deep Research and long agents, file processing, rendering, generated media, dictation, read-aloud TTS, meeting transcription, connector ingestion, audit events, account lifecycle, and maintenance behind durable queues and dedicated workers with workload-specific retry or at-most-once policies, cancellation, crash reconciliation, retention, health checks, and telemetry. Route realtime HTTP and WebSocket traffic through its own strictly scoped gateway, and use Redis, when configured, for live cross-process streams. |
| Egress and SSRF controls | Configure offline/allowlist/private/deny modes and apply redirect, DNS, private-address, TLS, and robots protections to outbound requests. |
| Health-aware startup | Validate required environment values and external services, wait for readiness, and surface database downgrade or compatibility failures. |
| Maintenance write freeze | Temporarily reject mutating API requests during restore or other coordinated maintenance while preserving safe reads. |
| Managed-cloud Compose | Run application services against externally managed database, Redis, and file storage without bundled stateful containers. |
| Network isolation | Keep internal services on scoped Docker networks, bind sensitive operator endpoints to loopback by default, and validate trusted proxies. |
| PgBouncer pooling | Enable transaction or session pooling only with bundled PostgreSQL, route long-running application services through the pool, and keep migrations on the direct database endpoint. |
| Proxy-terminated HTTPS | Keep the Docker frontend on private HTTP and terminate public TLS in the Launcher/CLI proxy or another trusted edge. |
| Request rate limiting | Enforce Redis-backed request controls plus model, tool, dictation-minute, realtime-minute, and webhook limits. |
| Secret, credential, and metadata protection | Encrypt sensitive database JSON, mask settings and logs, minimize session/model/agent payloads, keep private instructions and provider configuration out of user-facing APIs, and validate secret/key material. |
| Storage backends | Store user files locally or in S3-compatible/MinIO, Google Cloud Storage, Azure Blob Storage, or WebDAV. |
| Untrusted content isolation | Sanitize model/user Markdown, links, SVG, slide HTML, and widget output; serve active attachments as downloads or sandboxed previews; and restrict embedded frames. |

## Files, Storage, and External Connections

| Feature | Description |
| --- | --- |
| Cloud-file import | Browse and import Google Drive files directly into the user's Omlorix library. |
| Connection catalog | Show available external providers, authentication readiness, connection state, granted capabilities, and discovered tool counts. |
| Connection lifecycle | Connect through OAuth or supported personal-access tokens, enable/disable, refresh, edit, revoke provider credentials, and disconnect. |
| Connection providers | Integrate GitHub, Gmail, Google Calendar, Google Drive, Notion, and Slack as MCP tools or file-source adapters. |
| Document text extraction | Convert supported office documents, PDFs, and text formats into model-ready text when needed and treat active SVG/HTML markup as inert text rather than executable inline content. |
| File attachments | Attach uploaded or generated files to chats, notes, agents, automations, projects, and group context. |
| File CRUD and bulk actions | Upload, download, rename, delete, delete-all, retrieve by IDs, and perform partial-failure-aware bulk operations. |
| File export | Export a user's file library with safe names, storage metadata, and authorized content. |
| File folders | Create hierarchical organization, add/remove or move files, rename folders, and delete folders with membership synchronization. |
| File previews | Preview audio/video/images, HTML, Markdown, PDFs with selectable text or rendered pages, and unsupported formats with download fallbacks. |
| File rendering | Render Markdown to PDF, LaTeX to PDF, spreadsheet content, canvas HTML, and generated presentation assets through controlled services. |
| File storage accounting | Report per-user usage and administrator storage statistics across local and object-backed providers. |
| File workspace browsing | Page, search, filter, and sort accessible owned, shared, project, uploaded, and generated files while reporting workspace counts and folder membership. |
| Generated-file library | Collect canvas files, presentations, LaTeX PDFs, notes, and other tool artifacts in the chat and workspace file views. |
| Google Picker | Use browser-assisted Google Picker sessions and OAuth callbacks for recent-file browsing and multi-file import. |
| Google Workspace actions | Search or read Gmail messages, create drafts, send mail, and list, create, update, or delete Google Calendar events through capability-gated tools. |
| Model-aware file validation | Enforce provider/model input formats, upload size/count limits, media constraints, and attachment metadata minimization. |
| Note attachment security | Resolve embedded note media only after checking note and underlying file authorization. |
| Project-scoped files | List and manage files within a project while preserving project membership and ownership rules. |
| Secure file references | Serve shared-chat, shared-folder, note, agent, and canvas files through scoped references rather than exposing storage paths. |
| Storage cleanup and reference tracking | Remove cloud/local objects and derived previews when content is deleted while cleaning stale cross-feature references. |
| Upload deduplication and limits | Detect duplicate uploads within scope, enforce user/group limits, and bound cloud-import or archive sizes. |

## Models, Providers, Speech, and Realtime

| Feature | Description |
| --- | --- |
| Automatic context compaction | Let supported Anthropic and OpenAI Responses models compact older conversation context near configured limits, preserve compacted state for later turns, account for its usage, and retry safely when an endpoint lacks support. |
| BYOK credential tokens | Seal user-supplied API keys into short-lived request tokens so cleartext credentials do not need to be persisted. |
| BYOK model discovery | Query supported provider catalogs with a user's key, configure personal provider/model instances, and validate schemas. |
| BYOK usage controls | Opt into personal usage statistics, choose retention, view provider/model/tool/error summaries, and export or delete the data. |
| Dictation transcription | Upload bounded audio for speech-to-text, validate codec/duration, and account for dictation-minute quotas. |
| Live transcription | Stream microphone transcription with configurable provider/model, delay, language, key terms, VAD, endpointing, filler-word, and smart-turn options. |
| Local model runtimes | Discover Ollama/LM Studio versions and models, list loaded models, and stream download, update, load, or unload operations. |
| Model access controls | Limit models to everyone, selected users, or selected groups and combine access with group/user feature policies. |
| Model capability settings | Configure accepted input/output formats, context window, token limits, image detail, storage, service tier, tools, and provider-specific features. |
| Model catalog | Create, edit, bulk-update, duplicate, delete, import, export, search, and filter provider-backed models. |
| Model leaderboards | When group policy permits, match accessible models to Artificial Analysis free/full benchmark data, combine it with capabilities and local performance signals, and expose sortable details in a dedicated page and model selector. |
| Model presets | Save, list, retrieve, reuse, and delete per-model generation-setting presets. |
| Model selection preferences | Remember the last model, manage pinned models, search the catalog, and display descriptions, capabilities, throughput, elevated-error indicators, or leaderboard data. |
| Model/tool policy | Select built-in tools, managed/user MCP servers, external connection tools, realtime tools, and custom-tool access per model. |
| OpenRouter routing | Discover a model's upstream providers and route by a specific provider, automatic strategy, price, throughput, or latency. |
| Provider background synchronization | Refresh remote catalogs, add/update models, optionally remove missing models, and notify administrators about availability changes. |
| Provider groups | Combine provider instances for common-model discovery, weighted/distributed selection, fallback, and policy-managed routing. |
| Provider integrations | Support Anthropic and Anthropic-compatible endpoints, Azure OpenAI, Google AI Studio, LM Studio, Ollama, OpenAI Responses/Chat Completions-compatible APIs, OpenRouter, and xAI. |
| Provider lifecycle | Create, edit, test, validate, delete, import, export, and inspect providers with custom base URLs, headers, icons, and masked credentials. |
| Provider schemas | Generate explicit provider/model forms, validate typed settings, suggest common URLs, and expose schema-safe BYOK variants. |
| Rate-limit policies | Create, inspect, update, and delete model/tool/dictation/realtime quotas and show affected users their current usage. |
| Read-aloud speech | Read assistant messages through browser-native speech or configured ElevenLabs, Google, OpenAI, or xAI voices. |
| Realtime calls | Start OpenAI, Google AI Studio, or xAI WebRTC/live sessions with microphone audio, text/orb views, interruption, proactive/affective options, tools, and transcripts. |
| Realtime persistence and recovery | Heartbeat sessions, refresh connections, prepare input, persist turns/tool calls, resume sessions, compress context, and stop safely. |
| Reasoning and thinking controls | Use a unified quick-thinking selector or detailed settings to configure effort/budget, summaries, visibility, verbosity, and provider-specific reasoning behavior when supported. |
| Speech and media providers | Configure ElevenLabs, Google AI Studio, OpenAI/OpenRouter-compatible, and xAI capabilities for transcription, speech, image, audio, music, or video where supported. |
| Usage metadata accounting | Normalize provider token/cache/reasoning/audio usage, request counts, durations, throughput, and pricing metadata. |

## Observability, Analytics, and Audit

| Feature | Description |
| --- | --- |
| API telemetry status | Report whether telemetry is enabled and expose Prometheus-formatted application metrics when configured. |
| Audit logging | Record authentication, administration, sharing, provider, tool, data-transfer, backup, and other sensitive events without logging secrets; let Owners and Admins browse sanitized events with bounded filters and audited JSON export. |
| Authentication and audit-log retention | Partition authentication logs, clean them by age/count, and apply separate retain/delete/redact policies to authentication and audit records after user deletion. |
| Business and concurrency metrics | Track operational activity and concurrency signals for dashboards and capacity planning. |
| Feedback analytics | View ratings by model and over time, filter individual records, and export or selectively delete feedback. |
| Grafana dashboards | Provision Omlorix overview and database dashboards against the bundled observability stack. |
| IP security analytics | Configure collection with regulatory justification, view overviews/events/per-IP detail, filter, import/export, and purge retained data. |
| LLM error analytics | Inspect provider/model error rates, recent errors, filters, and bounded diagnostic timelines. |
| LLM performance analytics | Report requests, tokens, cost, latency, throughput, cache use, and category/provider/model breakdowns. |
| Observability stack | Optionally run OpenTelemetry Collector, Prometheus, Alertmanager, Jaeger, Grafana, and PostgreSQL/Redis exporters; add a hardened host node-exporter on Linux and omit it safely on macOS and Windows. |
| OpenTelemetry instrumentation | Export privacy-aware application and outbound-request traces, metrics, and logs with controls for path labels and browser metadata. |
| Realtime analytics | Analyze call totals, timelines, models, errors, and interruptions and export or delete realtime datasets. |
| Statistics portability and retention | Export or delete analytics datasets and automatically retain or redact BYOK data according to policy; those operations do not alter existing full-instance backup archives. |
| Tool-call analytics | Measure calls, success/failure, errors, and usage by built-in, MCP, or custom tool. |
| User and group analytics | Opt in to tracked-user reporting and compare per-user or per-group usage, models, providers, categories, errors, and tool calls. |
| Version and update awareness | Expose the running version and create deduplicated notifications when a newer release is detected. |

## Server Launcher and CLI Operations

| Feature | Description |
| --- | --- |
| Automatic environment backup | Keep a protected secondary `.env` copy synchronized after settings or secrets change and report backup health. |
| Automatic server updates | Schedule daily/weekend/custom maintenance windows, require healthy services, optionally back up first with the reviewed destination and archive-encryption policy shared by Launcher and CLI, run now, and inspect last/next state. |
| Automation-friendly CLI output | Provide JSON status and discovery output, bounded or followed log streams, explicit file arguments, optional readiness waits, and cross-process operation locks for terminal automation. |
| Backup and restore operations | Discover destinations, create and verify backups, download successful catalog artifacts with size/checksum validation and collision-safe local writes, and perform coordinated empty or confirmed in-place restores from the launcher or CLI. |
| CLI configuration management | List/get/set/unset/edit/import/export configuration with optional JSON output and masked secrets. |
| Code Execution service manager | Create multiple versioned sandbox services; set ports, memory, concurrency, session timeout, network/pip policy; and start/stop/restart/update/log/delete them. |
| Compose profile editor | Select production/development, bundled/external services, PgBouncer, storage, observability, and release settings. |
| Cross-platform packaged operation | Run the launcher and standalone CLI on Windows, macOS, or Linux with embedded Compose templates and managed server directories, without requiring a source checkout. |
| Data-service configuration | Configure bundled or external PostgreSQL/Redis and local/MinIO/S3/GCS/Azure/WebDAV storage with validation. |
| Deployment doctor | Validate Docker, Compose files, environment requirements, service connectivity, configuration consistency, and health before operations. |
| Desktop launcher updates | Check signed launcher releases and published checksums, select stable/beta channels, show native update progress, and require compatible launcher versions. |
| First-run server wizard | Choose setup depth, release/profile, data services, access/proxy mode, TLS, and recovery backup, then save a valid setup with or without immediately checking Docker or starting the server. |
| Installation ownership | Label managed Compose resources, require explicit adoption of matching unlabeled legacy projects, and refuse resources already owned by another installation identity. |
| Launcher and CLI parity | Expose ordinary server-management workflows in both the graphical Electron launcher and terminal-first `omlorix-server` CLI. |
| Launcher localization | Translate the full setup and management interface into every language supported by the web application. |
| Logs and console | View or follow aggregate/per-service logs, choose time/line bounds, and display operation output in the launcher console. |
| Managed reverse proxy | Configure HTTP/HTTPS ports, hostnames, certificates, upstream isolation, security headers, and proxy start/stop/restart state; warn that quitting the Launcher stops an in-process public proxy unless a background proxy service is installed. |
| Proxy service installation | Install, refresh, or uninstall the managed proxy as a macOS or Windows background service. |
| Release channels and compatibility | Check stable/beta feeds, select versions, prevent incompatible launcher/server updates, and detect downgrade/readiness issues. |
| Secret management | Generate/regenerate required secrets, reveal only on request, export/import them, save an immediate recovery copy, or disable automatic backup. |
| Server configuration editor | Inspect, paste, import, edit, validate, and save server settings with metadata, secret masking, and change warnings. |
| Server lifecycle | Initialize, start, stop, restart, update, open Omlorix or its managed files, and report the current installation and version. |
| Service controls | List expected/actual services, display health, and start, stop, restart, or tail logs for one service. |
| Update safeguards | Lock concurrent operations, back up to the reviewed destination with the reviewed archive-encryption mode before updates, cancel on backup failure, wait for readiness, retain compatibility metadata, and surface recovery guidance. |
| Visitor-IP remediation | Detect the Docker/proxy topology, repair visitor-IP proxy settings, verify the externally observed client IP, and report readiness. |

## User Experience, Accessibility, and Personalization

| Feature | Description |
| --- | --- |
| Accessible interaction patterns | Use keyboard-navigable controls, focus trapping/restoration, ARIA labels/live regions, accessible confirmation dialogs, and screen-reader announcements. |
| Chat appearance | Choose full-width layout, user/assistant Markdown rendering, message navigation/metadata visibility, and composer control visibility. |
| Color and theme controls | Choose system/light/dark mode, multiple color themes, font settings, and consistent login/admin/chat theming. |
| Command palette | Search context-aware application commands and conversations, navigate results by keyboard, and jump directly to chat or workspace actions. |
| First-run experience | Detect missing locale defaults automatically and show a non-blocking welcome card with privacy controls. |
| Internationalization | Provide Arabic, Chinese, English, French, German, Hindi, Italian, Japanese, Portuguese, Russian, and Spanish translations. |
| Keyboard shortcuts | Offer a centralized shortcut registry and help view for navigation, chat, workspace, composer, and settings actions. |
| Locale and regional preferences | Configure language, country, timezone, and location with browser-locale inference. |
| Mobile and touch support | Provide responsive layouts, mobile sidebars/sheets, touch-safe hover behavior, pull-to-refresh, and compact split-screen tabs. |
| Motion preferences | Respect OS reduced-motion preferences without disabling functionality. |
| Notification and status UI | Present translated toasts, inline errors, badges, progress, empty states, and persistent operation status without native browser dialogs. |
| Personal information controls | Choose which profile/location fields the LLM may access, using none/all/custom presets. |
| Personality settings | Select a personality preset or supply a custom instruction that is merged into permitted chat context. |
| Profile personalization | Edit personal details and public visibility, upload/remove a profile picture, and show account-specific pictures in multi-account slots. |
| Progressive Web App | Serve a configurable web manifest, cache-busted static assets, and version guards for installable browser use. |
| Right-to-left layout | Automatically switch document direction for Arabic while preserving localized navigation and form behavior. |
| Sidebar customization | Show/hide primary sidebar buttons, preserve open state, and expose profile, workspace, project, automation, and archive navigation. |
| Speech controls | Control dictation, live transcription, assistant read-aloud, playback speed, microphone state, and realtime-call transcript visibility. |
| Unsaved-change protection | Detect dirty forms/editors, warn before destructive navigation, and preserve or intentionally discard pending work. |
| Wake-lock support | Keep compatible devices awake during active long-running chat or realtime work. |
