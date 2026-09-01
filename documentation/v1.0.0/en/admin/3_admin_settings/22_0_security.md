# Security

**Admin Settings > Security** controls token lifetimes, sign-in protection, network restrictions, and retention for security records.

Before tightening access, keep a second tested Owner session and a server recovery path available. Test through every proxy, VPN, and network path that must remain usable.

## Authentication Token Lifetimes

- **Access token expiry (minutes):** how long a newly issued access token remains valid.
- **Refresh token expiry (minutes):** how long a session can continue obtaining new access tokens.

Shorter values reduce the useful life of stolen tokens but increase reauthentication. Tokens already issued keep the lifetime assigned when they were created. To end a specific user's current sessions, use the session-revoking actions under [Users](4_1_users.md).

## Sign-in Protection & Sessions

Turn on **Block after failed sign-in attempts**, then set **Failed sign-in attempt limit** and **Block duration (hours)**. A failed-sign-in block and a manual account lock are separate conditions; clearing one may leave the other active.

For user recovery, password replacement, session revocation, and two-factor reset, see [Users](4_1_users.md).

## IP Restrictions

**Enable IP restrictions** controls exact-IP and country-based rules. Managed [IP Bans](22_1_blocked_ip_addresses.md) are maintained separately.

### Exact IP Rules

1. Turn on **Enable exact IP rules**.
2. Set **Exact IP mode** to **Allowlist** or **Blocklist**.
3. Enter one exact IPv4 or IPv6 address per line in **Allowed IP addresses** or **Blocked IP addresses**.

These fields accept exact addresses, not network ranges. Before enabling an allowlist, include every administrator, proxy, VPN, monitoring service, and recovery address that needs access. Omlorix performs lockout-safety checks when saving, but operators must still verify all real access paths.

### Country Rules

Turn on **Enable country rules**, choose **Country mode**, and enter two-letter codes in **Allowed country codes** or **Blocked country codes**. Decide whether **Allow IPs without country match** should permit an address whose country cannot be determined.

Select an **IP location provider** and enter its protected credential when required. Lookup failure becomes an unknown-country result, so choose that behavior deliberately. Location lookups can disclose visitor IP addresses to another service; update legal notices and the [Processor & Transfer Register](22_5_processor_transfer_register.md) first.

### Trusted Proxies

Enable **Trust proxy headers** only when Omlorix is behind proxies you control. Add exact addresses or network ranges under **Trusted proxies**.

The proxy must replace untrusted forwarding headers. Incorrect trust can treat the proxy as every visitor, accept a spoofed address, or lock out legitimate users. Verify the effective visitor address before enabling restrictions; see [Set Up HTTPS](../2_setup/3_setup_https.md).

A server-level emergency override can suspend IP enforcement for recovery. While it is active, the web page cannot re-enable enforcement. Remove the override through the server-management workflow, restart Omlorix, and verify the effective policy afterward.

## Authentication Log Retention

**Enable auth log auto-cleanup** applies routine age or count cleanup. Choose **Auth log cleanup mode**, complete the displayed limit, and set **Cleanup interval (seconds)**.

**Per-user deletion retention** separately controls authentication records after their user is deleted:

- **Delete instantly**
- **Delete after N days**
- **Keep forever**

Set **Retention window after deletion (days)** when delayed deletion is selected.

## Audit Log and Admin Notification Retention

**Post-deletion retention** applies one policy to a deleted user's audit records and user-scoped Admin Notifications. Choose **Delete instantly**, **Delete after N days**, or **Keep forever**.

Account data, authentication logs, and audit/notification records have separate schedules. Review [Post-Deletion Retention](22_6_post_deletion_retention.md) before changing them.

## Audit Event Investigation

Owners and Admins can use **Admin Settings > Audit Logs** to browse a stable, sanitized event snapshot and create a bounded JSON export with a recorded investigation reason. The audit browser has separate range, row, detail, and disclosure limits; see [Audit Logs](22_4_audit_logs.md) before exporting operational evidence.

## Related Pages

- [IP Bans](22_1_blocked_ip_addresses.md)
- [IP Analytics](22_2_ip_analytics.md)
- [Audit Logs](22_4_audit_logs.md)
- [Privacy Policy and Terms of Service](22_3_legal_pages.md)
