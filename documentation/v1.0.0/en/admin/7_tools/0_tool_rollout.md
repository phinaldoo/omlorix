# Tool Rollout Checklist

Use this checklist for every chat tool, workspace action, media generator, renderer, and external connection. Feature pages document only the decisions unique to that capability.

## Understand the access layers

A tool is usable only when every required layer is ready:

1. Its provider, Service Connection, or external dependency is configured and healthy.
2. The tool's administrator settings are enabled and saved.
3. The intended model includes the tool and supports reliable tool calling.
4. The user's effective group permits the underlying feature, files, sharing, or connection.
5. Rate limits, storage limits, and provider quotas allow the request.
6. The signed-in user can access every selected file, workspace item, or external account.

An Admin account can bypass or differ from normal group policy. Always verify with an ordinary pilot user.

## Pilot before broad access

1. Start with one reliable model and one restricted group.
2. Use the smallest useful provider credential, scope, model allowlist, and tool allowlist.
3. Test a normal request, invalid input, permission denial, provider failure, timeout, and quota limit.
4. Verify output ownership, file storage, statistics, audit records, and deletion behavior.
5. Confirm the user-visible failure explains what the user can do next without exposing private infrastructure details.
6. Expand access only after monitoring cost, latency, and error rates.

## Review data and authority

Document for each tool:

- what prompts, files, profile data, locations, URLs, or workspace records leave Omlorix;
- which provider or service receives them and in which region;
- whether the tool can read, create, change, delete, publish, or spend;
- which credentials and user grants authorize it;
- retention, training, moderation, and billing terms;
- who responds to abuse, compromise, failed jobs, or unexpected cost.

Search results, documents, model output, generated code, and external tool results are untrusted. They must not override authorization or operational policy.

## Change or retire a tool

Before changing a provider, model, renderer, credential, or service:

1. Disable new access or restrict it to the pilot group.
2. Preserve any user outputs that must remain available.
3. Rotate or revoke external credentials at their source.
4. Change one dependency and run the real end-to-end workflow.
5. Remove obsolete model assignments and group access.
6. Delete the old Omlorix configuration only after dependent automations, Agents, chats, and files have a replacement.

Disabling a tool prevents new calls; it does not undo external actions or delete existing user data.

## Move tool configurations between instances

A model export carries model settings, tool names, access data, and embedded relationship IDs. It does not carry the dependencies behind them. Before importing models on another instance:

1. Restore each dependency through its own supported path: LLM providers, Web Search providers, administrator MCP servers, custom Python tools, Skills, users/groups, and Service Connections or their external services.
2. Record every fresh destination ID. Feature imports generally create new records rather than preserving source IDs.
3. Update a protected copy of the model export so provider, Web Search, MCP, Skill, user, group, and model references point to destination records.
4. Configure and verify a destination default before model import. If none exists, set imported models' `access.everyone` to `false`; the first Everyone-visible model can become default even while inactive. Import models inactive and restricted, reselect any reference that cannot be safely mapped, and run the full pilot before activation.

Provider/API credentials, MCP headers and OAuth grants, Web Search keys, Service Connection secrets, and external service state are not supplied by a model export. Use a verified [full-instance backup](../3_admin_settings/23_1_backups.md) for same-instance recovery, while still protecting external services and separately managed runtimes according to their own pages.

Continue with the page for the specific tool, or review [Model Settings](../6_llmmodels/3_llm_model_settings.md) when a configured tool is missing from chat.
