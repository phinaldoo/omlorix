# Bring Your Own Key (BYOK)

**Bring Your Own Key** lets permitted users configure personal providers and models under **Settings > Bring Your Own Key**. Their credentials and models are not shared administrator providers and cannot be selected as instance-wide speech or media services.

## Decide whether to allow BYOK

Enable it only when users may choose their own processor, endpoint, account, billing, and data terms. The Omlorix application service still brokers requests and applies access and outbound-network rules, so the operator remains responsible for the proxy, acceptable-use policy, and permitted destinations.

BYOK is unsuitable when every processor and model must be centrally approved, credentials must be centrally managed, or custom destinations are prohibited.

## Configure group access

Open the relevant group's **Chat > BYOK** settings and review:

- **Enable BYOK**;
- **Allowed BYOK tools**;
- optional title-generation and Web Search defaults;
- **Track BYOK usage** and its user consent behavior.

Start with no tools. Add only those the group needs; tools can read files, contact services, change data, execute code, or create cost. MCP access remains subject to the separate MCP and connection policies. For users in multiple groups, verify the effective result with a representative account.

## Security and operations

- Personal provider and model definitions are stored in that browser profile's local storage and do not sync as account data. Raw API keys are not stored with those definitions. Do not share browser profiles between users.
- When a key is entered, Omlorix exchanges it for a server-sealed credential token bound to the signed-in user, provider type, and local provider record. Only that opaque token is kept in the current tab's session storage, for at most 30 days.
- Closing the tab, signing out, changing accounts, clearing browser storage, token expiry, or rotating the server encryption key can require key entry again. Other tabs do not inherit the token.
- A user-supplied local or private endpoint must be explicitly permitted by [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md). Omlorix cannot reach a service running only on the user's computer unless it is network-accessible to the application service.
- Disabling BYOK blocks its use but does not erase browser-local provider definitions or existing usage records.
- Exported user data and administrator provider exports do not back up personal provider definitions or keys.

Publish allowed providers, destinations, data classes, cost ownership, retention expectations, and support boundaries before rollout. Pilot one group, test denied and allowed cases, key re-entry, tools, account switching, and statistics, then expand gradually. Disabling Bring Your Own Key is a policy change, not credential revocation at the user's provider.

See the [user guide](../../user/3_user_settings/12_bring_your_own_key.md) for the personal setup workflow.
