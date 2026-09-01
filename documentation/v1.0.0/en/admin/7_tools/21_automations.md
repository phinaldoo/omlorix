# Automations

**Automations** lets a model help create and manage the signed-in user's automation definitions and schedule settings. Automation runs can create chats, call providers and connections, and consume quota without the user being present.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then apply the unattended-execution checks below.

## Enable and test

1. Select **Automations** on the models permitted to manage them.
2. Confirm the group's automation, model, file, connection, and notification access.
3. Test the information and list operations, then create, edit, activate, pause, and delete a harmless scheduled automation.
4. Let a harmless schedule run and verify its time zone, created chat, notifications, attached context, and provider usage.

An automation stores authority for future execution. Limit its model, files, notes, skills, and MCP connections to what the task needs. Do not permit destructive external tools unless the unattended behavior and recovery process are explicitly acceptable.

The tool can show a summary of an existing webhook trigger, but it cannot create, change, rotate, or remove one. It also cannot delete an automation while a webhook trigger is attached; the user must remove that trigger in the **Automations** interface first. The tool has no run-now operation.

Pausing or deleting an automation stops future runs but does not undo completed actions or remove chats and files already created. Users must rotate webhook secrets in the **Automations** interface, and connection grants should be reviewed as part of offboarding.
