# Database

**Admin Settings > Database** manages full-instance backup jobs, destinations, schedules, and history. Restore is intentionally not available in the web admin.

Use this page to:

- create a backup under **Backup Now**
- configure and test **Backup Destinations**
- create **Backup Schedules** and retention policies
- verify, download, or delete completed jobs in **Backup History**

The Server Launcher and `omlorix-server` CLI can also download a successful job from the same catalog without exposing its storage URI. They validate the catalogued artifact and refuse to overwrite the chosen host path; see [Backups, Destinations & Schedules](23_1_backups.md).

Read [Backups, Destinations & Schedules](23_1_backups.md) before the first production backup. Use [Full-Instance Restore](23_2_restore.md) for the server-side recovery workflow.

## Operator Responsibilities

- Monitor the database, storage destinations, and host capacity outside the web page.
- Keep a verified backup before updates, imports, direct maintenance, and high-impact security or access changes.
- Protect deployment configuration, credentials, recovery keys, and remote file storage separately.
- Do not treat a database-only copy as complete recovery material.
- Rehearse restoration in an isolated compatible environment on a regular schedule.

Use the [Server Launcher](../2_setup/1_2_server_launcher.md) or [Server CLI](../2_setup/1_3_server_cli.md) for ordinary server lifecycle and recovery operations.
