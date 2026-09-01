# Admin Notifications

**Admin Notifications** records operational and security events for administrators. It is separate from announcements shown to users.

## Review the History

- use **Types** and **Categories** to narrow the list
- use **Refresh** to load new events
- page through the results and expand an entry for available details
- follow an action link only after checking that it is relevant to the event
- use **Download JSON** to export the stored history
- use **Clear All** only when the entire notification history may be removed

The page does not provide a general message-text search. Clearing notifications does not clear [Audit Logs](22_4_audit_logs.md) and does not fix the event that created them.

Exports can contain user, provider, network, or security context. Store them as sensitive operational records and remove unneeded copies.

## Administrative Notifications

The **Administrative notifications** section controls delivery to an external webhook:

- **Enable outgoing notifications** enables delivery for newly created admin notifications.
- **Webhook destination URL** must be a permitted public HTTPS endpoint.

Previously stored notifications are not replayed after you enable the webhook. Confirm that outbound network policy permits the destination, that the receiving service authenticates and retains events appropriately, and that no secret is placed in notification text.

Post-deletion handling for user-scoped admin notifications is coupled to audit-log retention under [Security](22_0_security.md). The effective audit boundary is also shown in the [Audit Logs](22_4_audit_logs.md) browser and export. Create user-facing messages under [User Notifications](4_2_user_notifications.md).
