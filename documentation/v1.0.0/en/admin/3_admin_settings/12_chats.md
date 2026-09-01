# Chats

**Admin Settings > Chats** provides audited access to stored conversations and migration from Open WebUI.

This administrator page does not import ChatGPT archives. ChatGPT migration is user-scoped under **Settings > Data Control** and is visible only when the user's group allows data controls; it imports into the signed-in account. See the [user data-control guide](../../user/6_privacy_data/2_data_controls.md). Use **Import/Export Users** for complete Omlorix account archives.

## Review Stored Chats

Select **Go to chats** to open the administrative conversation view. Before access is granted, provide the requested reason. Use this view only for an approved operational, support, legal, or security purpose.

Administrative access can expose prompts, responses, uploaded content, tool output, and other personal or confidential information. Limit access to the smallest necessary scope, avoid copying content into tickets, and follow your organization's review and retention policy. Administrative review is recorded for accountability.

## Import from Open WebUI

Choose the import action that matches the source export:

- **Import chats for a single user:** upload a chat export and select the destination Omlorix account.
- **Import archived chats for a single user:** upload an archived-chat export; imported conversations are marked as archived.
- **Import chats of all users:** upload **Users CSV** and **All Chats JSON** together. Omlorix matches source users to existing Omlorix accounts by email address.

Review the preview before starting. An all-users import does not create missing users; chats for unmatched email addresses are skipped.

During conversion:

- alternative conversation branches become separate Omlorix chats
- the source archive state is retained
- only the primary or currently selected branch retains the source pin
- attachment files are not transferred

The import transfers conversations, not passwords, identity-provider links, permissions, groups, provider configuration, or user preferences.

## Safe Migration Workflow

1. Back up Omlorix and retain the original Open WebUI exports.
2. Create or import the required users first.
3. Test a representative single-user import and inspect titles, messages, branches, archive state, and pins.
4. Run the all-users preview and resolve unexpected email matches or skipped users.
5. Start the import once and review the result summary.

Retrying the same Open WebUI source can create duplicate chats. Confirm whether a previous job completed before starting it again. For complete Omlorix account portability, use the archive workflows documented under [Users](4_1_users.md).
