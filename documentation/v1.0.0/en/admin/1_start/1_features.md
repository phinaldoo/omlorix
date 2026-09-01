# Omlorix for Operators

Omlorix is a self-hosted, multi-user AI workspace. Users can chat with configured models, work with files and reusable content, collaborate in shared workspaces, and use capabilities such as web search, media generation, code execution, and connected services when an administrator enables them.

This guide is for the people who install and operate the server. The [user guide](../../user/1_quick_start/1_start.md) explains the everyday product experience.

## What You Operate

- **Accounts and access:** users, groups, roles, sign-in methods, rate limits, and time-based access.
- **AI services:** providers, provider groups, models, model visibility, tools, and managed skills.
- **Data and governance:** chats, files, notifications, statistics, legal pages, audit-event review and export, retention, canonical Omlorix account archive import/export, user-scoped ChatGPT archive import, administrator Open WebUI chat import, backups, and restore.
- **Infrastructure:** PostgreSQL, Redis, file storage, HTTPS, updates, logs, monitoring, and optional Code Execution services.

Features remain unavailable until their dependencies are configured. For example, users need an accessible model before they can chat, and password reset needs working email delivery and an accurate public URL.

## Know Where Configuration Belongs

| Surface | Use it for |
|---|---|
| **Server Launcher** or **`omlorix-server` CLI** | Server lifecycle, deployment topology, secrets recovery, updates, backups, proxying, storage operations, and diagnostics |
| **Admin Settings** | Users, groups, authentication, providers, models, capabilities, analytics, security, and governance |
| **Group settings** | The permissions, limits, and defaults applied to members |
| **User settings** | A person's own preferences and permitted connections |

The Launcher and CLI expose the same ordinary server-management capabilities. They see the same installation only when they use the exact same server home, and they must not perform write operations concurrently. Use [Operate and Update Omlorix](../2_setup/4_operations.md) as the shared runbook and the [Server Configuration Reference](../2_setup/7_environment_variables.md) for deployment decisions.

Admin Settings changes product behavior inside the running instance; they do not replace deployment configuration. Likewise, a healthy server does not prove that a provider, model, external connection, or user permission is ready.

## Understand Feature Dependencies

Most optional capabilities require several independent layers. A typical tool needs a healthy provider or Service Connection, an enabled administrator setting, a capable model, group permission, and sufficient quota. Test all layers with an ordinary pilot user before broad access; see the [Tool Rollout Checklist](../7_tools/0_tool_rollout.md).

Keep these recovery assets distinct:

- a full-instance backup protects Omlorix database state and the archive's included data;
- the protected server recovery copy preserves deployment settings and critical secrets;
- external PostgreSQL, user-file storage, and monitoring data may need provider-native backups as well.

Losing an encryption key or backup passphrase can make otherwise intact data unusable. Store recovery material outside the server home on protected storage that survives loss of the host, then rehearse a restore.

## Recommended Path

1. [Choose an installation method](../2_setup/1_1_setup.md).
2. Complete [First Steps](../2_setup/2_first_steps.md).
3. Configure at least one [provider](../3_admin_settings/13_llm_providers.md) and [model](../3_admin_settings/15_0_llm_models.md).
4. Review [Security](../3_admin_settings/22_0_security.md), [Audit Logs](../3_admin_settings/22_4_audit_logs.md), [Backups](../3_admin_settings/23_1_backups.md), and HTTPS before inviting users.
5. Create a verified backup, protect the recovery copy, and adopt the [operations runbook](../2_setup/4_operations.md).


Do not treat the application, its templates, or these pages as legal, compliance, or security certification.
