# User Notifications

Open **Manage Notifications** from **Admin Settings > Users** to create in-app announcements and alerts. These messages are separate from operational [Admin Notifications](2_1_admin_notifications.md).

## Create a Notification

Select **Create Notification**, then set:

- **Message:** required, up to the displayed 255-character limit
- **Category:** an organizational label of up to 64 characters
- **Type:** **Info**, **Warning**, or **Error**
- **Recipients:** **Send to everyone**, **Specific Users**, **Specific Groups**, or a combination of users and groups

At least one recipient scope is required. Review **Send to everyone** carefully because it makes the notification available to every user who can load notifications.

## Manage Existing Notifications

The table shows **Message**, **Category**, **Type**, **Recipients**, **Created**, and **Actions**. Page through the list and use the row actions to edit or delete an entry.

Creating an entry marks a new notification for its recipients. Editing changes the stored entry but does not create a second delivery event. Deleting removes the stored notification. These actions are audited.

User notification history is instance-owned: it is not included in canonical user archives, and this page has no import or export action. Recreate required announcements after a migration and verify that targeted users and groups exist first.

Do not use notifications for passwords, credentials, private prompts, or unnecessary personal data. For policy changes that require acceptance, use [Privacy Policy and Terms of Service](22_3_legal_pages.md) and the enforcement controls under **Login**.
