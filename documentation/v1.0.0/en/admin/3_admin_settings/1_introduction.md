# Admin Settings

Open **Admin Settings** from the main navigation. The **Owner** and **Admin** roles can use these pages. Actions reserved for the Owner are identified where they appear.

The sidebar groups the pages into **Settings**, **Access**, **Authentication**, **Conversations**, **AI Models**, **Capabilities**, **Analytics**, and **System**. Related detail pages open from their parent page—for example, **Admin Notifications** from **Dashboard**, user announcements from **Users**, and speech settings from **Models**. **Audit Logs** is a direct investigation page for Owners and Admins and is distinct from Admin Notifications.

## What Belongs Here

Most Admin Settings are instance-wide. **Users** controls individual accounts and preferences; **Groups** is the main place to assign features, permissions, limits, context, and delegated management to ordinary users. Model and tool access may depend on both the relevant global configuration and the user's group.

Server lifecycle, network exposure, TLS, database services, and deployment credentials are managed with the [Server Launcher](../2_setup/1_2_server_launcher.md) or [Server CLI](../2_setup/1_3_server_cli.md), not in Admin Settings.

## Make Changes Safely

1. Identify whether the change is global, group-specific, or user-specific.
2. Review dependent sign-in methods, groups, models, tools, storage, and scheduled work.
3. Save and wait for confirmation. Some specialized controls save immediately or require a confirmation dialog.
4. Restart Omlorix only when the page or guide tells you the change is read at startup.
5. Test the affected workflow with an ordinary **User** account as well as an administrator.

Keep credentials only in fields the UI identifies as protected. Exports may mask or omit secrets; always inspect an export before treating it as a complete migration package.

See [Roles and Instance Ownership](../4_user_group_management/2_roles_and_ownership.md) for the authority model.
