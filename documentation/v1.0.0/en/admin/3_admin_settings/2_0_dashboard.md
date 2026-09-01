# Dashboard

**Admin Settings > Dashboard** is a quick operational summary. It is useful for triage, but it is not a billing report or a replacement for infrastructure monitoring and logs.

## Summary Cards

- **Active users:** users active within the last seven days.
- **Peak 5-min concurrent:** the highest number of unique active users in a five-minute window during the last seven days. A new instance shows that it is still collecting the full window.
- **Pending user invites:** users awaiting approval.
- **LLM Provider availability:** current reachability of configured providers. The card is hidden when no provider exists.
- **Model health:** recent error-rate status for configured models. The card is hidden when no model exists.
- **Internet connectivity:** external reachability. The card is hidden when **Internet Connectivity Check Enabled** is off.

The user, provider, and model cards open the corresponding Admin Settings page.

## Admin Notifications Preview

The notification panel shows recent operational events. Open [Admin Notifications](2_1_admin_notifications.md) for the complete list, filters, JSON export, and **Clear All** action.

Treat **Status unknown**, missing cards, and empty panels as unavailable evidence—not proof that a dependency is healthy. When a card warns, open its linked page, run the available connection test, and corroborate the result with the relevant server or provider logs.
