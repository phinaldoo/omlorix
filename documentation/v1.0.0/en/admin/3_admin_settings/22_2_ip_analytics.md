# IP Analytics

Open **Admin Settings > Security > View analytics** to review stored IP-related security events. This optional feature records full IP addresses, timestamps, event details, and derived country codes.

## Enable Collection

1. Review the **Privacy & Compliance Notice**.
2. Set **Retention days** from 1 to 3650.
3. Enter a **Legal basis or policy reference**.
4. Confirm the compliance statement.
5. Select **Enable Analytics**.

Before enabling collection, document the purpose, access controls, retention and deletion process, privacy notice, and any transfer to the selected **IP location provider**. The confirmation records the operator's decision and is not legal approval.

Disabling analytics stops new collection. It does not delete existing rows; they remain until retention cleanup or manual deletion.

## Read the Dashboard

The dashboard summarizes **Active IP bans**, **Known origin countries**, **Denied requests**, and **Most denied requests**. It also shows country activity and the **IP security event timeline**.

Recorded event types include **Request denied**, **Rate limited**, **Ban created**, and **Ban removed**. Filter by **IP address**, **Country**, **Event type**, **Source**, and time period.

Retention and display limits can truncate the result. Country is approximate, and event volume does not establish a person's identity, physical location, or intent.

## Export, Import, and Delete

- **Export** downloads a versioned JSON file containing stored analytics events and the non-secret analytics settings snapshot.
- **Import** accepts a compatible JSON export up to 20 MiB. It adds valid events without duplicating existing event IDs or aggregation keys and reports invalid or duplicate rows as skipped.
- **Delete data** removes analytics rows in the selected period.

Import also restores retention and legal-basis settings. Collection is enabled from the imported snapshot only when its recorded confirmation and documented justification or policy reference form a valid combination. Review those settings immediately after migration rather than treating the source operator's confirmation as approval for the destination.

Active IP bans are not exported or imported, and deleting analytics does not remove them. IP-location provider credentials are not included either; configure and test the destination provider separately.

Exports contain personal and security-sensitive information. Restrict access, encrypt retained copies, set a deletion date, and do not attach them to a public support request.
