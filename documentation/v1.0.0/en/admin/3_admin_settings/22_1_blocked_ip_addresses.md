# IP Bans

Open **Admin Settings > Security > Manage IP bans** to block an exact visitor address temporarily. Use the main [Security](22_0_security.md) page for longer-lived exact-IP and country policy.

## Block an Address

1. Select **Add IP address**.
2. Enter an exact IPv4 or IPv6 **IP address**.
3. Set **Duration in days** from 1 to 365.
4. Enter a concise **Reason**.
5. Select **Block IP**.

The list shows **IP address**, **Blocked at**, **Expires at**, **Reason**, and **Actions**. **Edit IP ban** can change the address, duration, or reason. Expired bans stop applying automatically.

Keep the reason factual and minimal. It is retained for operational and audit review and should not contain secrets or unnecessary personal information.

## Unblock an Address

Select **Unblock IP** and confirm. This removes only the managed ban. Exact-IP rules, country rules, account restrictions, or another control can still deny access.

## Safety Notes

- A ban applies to an exact address, not a network range.
- Verify the effective visitor address when Omlorix is behind a proxy. Blocking the proxy address can affect everyone behind it.
- Keep a recovery path before blocking an administrator address.
- A server-level emergency override that suspends IP enforcement also suspends managed bans. Remove it through the server-management workflow and restart after recovery.
- Use [IP Analytics](22_2_ip_analytics.md) to investigate recorded events. Counts show observed activity, not identity or intent.
