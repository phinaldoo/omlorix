# Audit Logs

**Admin Settings > Audit Logs** lets Owners and Admins investigate the general audit store without direct database access. It is separate from [authentication-log retention](22_0_security.md) and [Admin Notifications](2_1_admin_notifications.md).

## Browse Events

The initial view covers the previous seven days. Set **From** and **To**, then optionally filter by exact **Category**, exact **Action**, exact **Actor ID**, or an event or resource **Reference**. Browsing accepts a maximum range of 366 days.

Results are frozen to one snapshot. **Load more** continues that same snapshot, so newly created events do not appear halfway through an investigation. Select **Refresh** or apply the filters again to start a current snapshot.

The table shows the event time, category, action, actor, recorded operational reason, and whether sanitized details are available. Opening **View details** can add the event ID, privacy-preserving IP or device fingerprints, and allowlisted structured context. Older rows that contain raw network or device values do not expose those values through this page.

## Export a Filtered Snapshot

1. Apply the exact filters and confirm the time range.
2. Select **Export JSON**.
3. Enter an investigation reason of at least three characters.
4. Select **Download JSON** and protect the resulting file as a sensitive operational record.

An export is limited to 31 days and 50,000 events. Narrow the range or add filters when it exceeds the row limit. The versioned JSON envelope records the export time, effective range, event count, retention summary, and sanitized events. Export does not include raw database rows or hidden detail fields.

## Security, Auditability, and Retention

List, detail, and successful export operations create audit events. A rejected export that exceeds the row limit is also audited with the supplied reason. Expect your own investigation activity to appear in a refreshed snapshot.

The same store records security-sensitive session lifecycle changes, credential and connection changes, durable model-tool mutations, and explicit private or bulk exports and downloads. The newly covered state-changing flows stage their event in the same database transaction as the affected state and deliver it through the durable audit-event outbox, so a committed mutation does not lose its audit intent when the audit database is temporarily unavailable. High-frequency reads such as inline previews, heartbeat traffic, and ordinary list views are intentionally excluded so investigations retain a useful signal-to-noise ratio.

Responses omit credentials, tokens, prompts, session material, raw network identifiers, and unapproved detail keys. The page also bounds nested detail depth, item count, and text length. Sanitization reduces exposure but does not make an export public or anonymous; event IDs, actor IDs, reasons, categories, and allowed resource references can still be sensitive.

The retention summary reports the current policy for events associated with deleted users. General audit events do not currently have an age- or count-based cleanup limit in this browser. An absent event may have been removed by the post-deletion policy or by a later data lifecycle change. See [Post-Deletion Retention](22_6_post_deletion_retention.md).
