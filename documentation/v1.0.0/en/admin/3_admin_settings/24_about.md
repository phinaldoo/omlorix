# About

**Admin Settings > About** shows the running Omlorix version and links to **Documentation**, **Feedback & Bugs**, **Release Notes**, and **Support**. It does not install updates.

When a newer release is available, the page can show an update notice and create an [Admin Notification](2_1_admin_notifications.md). Omlorix avoids creating the same release notification repeatedly. The check needs permitted outbound access; if it is unavailable, Omlorix continues to run.

## Update Safely

1. Record the current version and read the relevant **Release Notes**.
2. Check deployment, compatibility, and server-management requirements.
3. Create and verify a full backup, remote file-storage copy, and protected recovery bundle.
4. Update with the [Server Launcher](../2_setup/1_2_server_launcher.md), [Server CLI](../2_setup/1_3_server_cli.md), or [source-checkout](../2_setup/1_5_source_checkout.md) workflow used by the installation.
5. Reopen **About** and confirm the expected version.
6. Test login, chat, providers, files, Agents, Automations, backups, and restore readiness.

An update notice compares available and running versions only. It does not assess local modifications, capacity, compatibility, or backup quality.

Before opening an external support or issue link, remove credentials, private addresses, personal data, prompts, files, and sensitive logs from the report.
