# Automations

Automations send a saved **Automation prompt** to a selected **AI model** on a schedule or through a webhook. Each run creates a chat you can review later; it does not continue an existing conversation.

Open **Automations** from the sidebar or **Commands and chat search**. The page appears only when your account is allowed to use it.

## Create an automation

Select **Add**, then enter an **Automation name**, a self-contained **Automation prompt**, and an **AI model**. You can optionally add model-compatible **Connections**, one **Skill**, **Notes**, and **Files**. Select the model before choosing its Connections.

Choose a trigger:

- **Schedule > Recurring:** add one or more times and choose the weekdays for each rule.
- **Schedule > Run once:** choose one future local date and time; the automation pauses after it runs.
- **Webhook:** generate credentials that let an external service start the automation. See [Automation Webhooks](2_automation_webhooks.md).

Check **Run at (local time)** against the Time Zone in **Settings > General**, then turn on **Automation active**. Because no one is present to answer a follow-up, state the task, source, time period, output format, and what to do if information is missing.

## Manage runs

Use an automation's menu to **Edit**, **Pause**, **Activate**, or **Delete** it. After changing the schedule, model, or Time Zone, confirm the displayed next run.

Find run results in the generated chats and [Workspace Notifications](../10_workspace/3_notifications.md). Check the existing chat before retrying a failed run, because it may contain partial work.

Deleting an automation stops future runs but keeps earlier chats and notifications. It does not reverse actions already performed through a Connection.

Give an automation only the Files, Notes, Skill, and Connections it needs. Prefer read-only access, avoid unattended irreversible actions, and review its output regularly. If a referenced item or model becomes unavailable, edit the automation and choose a replacement.

A compatible Automations tool can help a model list, create, edit, activate, pause, or delete Automations and manage their schedule settings. It can show a summary of an existing webhook trigger but cannot create, change, rotate, or remove one. An Automation with a webhook trigger cannot be deleted through the tool until you remove the trigger yourself on the Automations page. Give exact names, inspect the proposed schedule and context, and verify the result—especially before activating it.

## Portability

The Automations page does not provide **Import** or **Export** actions for individual Automations or collections.

To move supported Automation data between Omlorix accounts, use the complete account archive under **Settings > Data Control**. Personal MCP servers are recreated before Automations so compatible selections can be remapped to their new IDs. An unavailable destination model, server, or access policy can remove an MCP selection and add a **needs review** warning without discarding the Automation. Imported automations can also require you to reselect unavailable Files, Notes, Skills, or other Connections. Re-enter MCP headers or OAuth authorization after import. Webhook URLs, secrets, and delivery state are not portable credentials and must be configured again. See [Data Control](../6_privacy_data/2_data_controls.md).
